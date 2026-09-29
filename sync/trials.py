"""The trial check-in (SYNC-SPEC.md §3, §5 `trial.checkin`): when a trial is up, a message in
#officers mentioning the Officer role, with Promote to Raider and an Extend trial menu. The site
decides who may answer and what each answer does; the click calls it and shows the outcome."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

import discord
from discord.ext import commands

from .api import ApiError, SiteApi
from .config import SyncConfig
from .runner import JobRunner

log = logging.getLogger("sync.trials")

Job = dict[str, Any]
EXTEND_DAYS = range(1, 8)


def _timestamp(iso: object) -> int | None:
    """Unix seconds from the site's ISO time, or None when it is missing or odd."""
    if not isinstance(iso, str) or not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def checkin_text(payload: Job, officer_role_id: int) -> str:
    """The check-in message. Pure."""
    ping = f"<@&{officer_role_id}> " if officer_role_id else ""
    who = f"**{payload.get('name') or 'A trial'}** (<@{payload['discordId']}>)"
    started = _timestamp(payload.get("startedAt"))
    since = f", on trial since <t:{started}:D>" if started else ""
    if payload.get("extended"):
        return f"{ping}{who}'s trial extension is up{since}. Promote them to full Raider, or extend the trial again?"
    return f"{ping}{who}'s trial has hit two weeks{since}. Promote them to full Raider, or extend the trial?"


def promoted_text(discord_id: str, officer: discord.abc.User) -> str:
    return f"✅ <@{discord_id}> was promoted to full Raider by {officer.mention}. The Trial role comes off in a moment."


def extended_text(discord_id: str, days: int, check_in_at: object, officer: discord.abc.User) -> str:
    when = _timestamp(check_in_at)
    again = f" I'll ask again <t:{when}:R>." if when else ""
    return f"⏳ <@{discord_id}>'s trial was extended by {days} day{'s' if days != 1 else ''} by {officer.mention}.{again}"


class TrialPromoteButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sync:trial:promote:(?P<uid>\d+)"):
    """Promote to Raider. Survives restarts: the id carries the member."""

    def __init__(self, uid: str):
        self.uid = uid
        super().__init__(discord.ui.Button(label="Promote to Raider", style=discord.ButtonStyle.success, custom_id=f"sync:trial:promote:{uid}"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
        return cls(match["uid"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await answer(interaction, self.uid, "promote", None)


class TrialExtendSelect(discord.ui.DynamicItem[discord.ui.Select], template=r"sync:trial:extend:(?P<uid>\d+)"):
    """Extend the trial by 1 to 7 days; the check-in comes back when they are up."""

    def __init__(self, uid: str):
        self.uid = uid
        options = [discord.SelectOption(label=f"{d} day{'s' if d != 1 else ''}", value=str(d)) for d in EXTEND_DAYS]
        super().__init__(discord.ui.Select(placeholder="Extend trial by...", min_values=1, max_values=1, options=options, custom_id=f"sync:trial:extend:{uid}"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Select, match: re.Match[str], /):
        return cls(match["uid"])

    async def callback(self, interaction: discord.Interaction) -> None:
        await answer(interaction, self.uid, "extend", int(self.item.values[0]))


def checkin_view(discord_id: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(TrialPromoteButton(discord_id))
    view.add_item(TrialExtendSelect(discord_id))
    return view


async def answer(interaction: discord.Interaction, discord_id: str, action: str, days: int | None) -> None:
    """Send the officer's answer to the site; on success the message becomes the outcome."""
    cog: TrialsCog | None = interaction.client.get_cog("TrialsCog")  # type: ignore[assignment]
    if cog is None:
        return await interaction.response.send_message("The sync is not running.", ephemeral=True)
    # The site may take longer than Discord's three seconds on a cold start.
    await interaction.response.defer()
    try:
        result = await cog.api.trial(int(discord_id), action, interaction.user.id, days=days, key=str(interaction.id))
    except ApiError as e:
        await interaction.followup.send(e.reason, ephemeral=True)
        return
    except Exception as e:  # noqa: BLE001 — a timeout or refused connection; the buttons stay for another try
        log.warning("trial %s for %s failed: %s", action, discord_id, e)
        await interaction.followup.send("Couldn't reach the website. Try again in a minute.", ephemeral=True)
        return
    if action == "promote":
        text = promoted_text(discord_id, interaction.user)
    else:
        text = extended_text(discord_id, days or 0, result.get("checkInAt"), interaction.user)
    await interaction.edit_original_response(content=text, view=None, allowed_mentions=discord.AllowedMentions.none())


class TrialsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, api: SiteApi, config: SyncConfig, runner: JobRunner):
        self.bot = bot
        self.api = api
        self.config = config
        runner.handle("trial.checkin", self.job_checkin)

    async def job_checkin(self, job: Job) -> None:
        p = job["payload"]
        discord_id = str(p.get("discordId", ""))
        if not discord_id.isdigit():
            raise ValueError("trial.checkin needs a discordId")
        channel = self.bot.get_channel(self.config.officers_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError("OFFICERS_CHANNEL_ID is not a channel the bot can post in")
        role = self.config.role_officer_id
        await channel.send(
            checkin_text(p, role),
            view=checkin_view(discord_id),
            allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[discord.Object(id=role)] if role else False),
        )
