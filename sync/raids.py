"""#raid-signups (SYNC-SPEC.md §5 raid.*, §8 text-channel version): one message and a thread per
raid, buttons that call the site, a decline modal, reminders in the thread, lock, cancel and
close. Every visible change comes from a job; a click only calls the site and answers
ephemerally."""

from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord.ext import commands

from . import raid_render as render
from .api import ApiError, SiteApi
from .config import SyncConfig
from .runner import JobRunner

log = logging.getLogger("sync.raids")

Job = dict[str, Any]

ACTIONS = ("accept", "tentative", "decline", "bench")


class RaidButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sync:raid:(?P<action>accept|tentative|decline|bench):(?P<raid_id>[A-Za-z0-9]+)"):
    STYLE = {"accept": discord.ButtonStyle.success, "tentative": discord.ButtonStyle.secondary, "decline": discord.ButtonStyle.danger, "bench": discord.ButtonStyle.primary}
    LABEL = {"accept": "Accept", "tentative": "Tentative", "decline": "Decline", "bench": "Join bench"}

    def __init__(self, action: str, raid_id: str):
        self.action = action
        self.raid_id = raid_id
        super().__init__(discord.ui.Button(label=self.LABEL[action], style=self.STYLE[action], custom_id=f"sync:raid:{action}:{raid_id}"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
        return cls(match["action"], match["raid_id"])

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: RaidsCog | None = interaction.client.get_cog("RaidsCog")  # type: ignore[assignment]
        if cog is None:
            return await interaction.response.send_message("The sync is not running.", ephemeral=True)
        await cog.on_click(interaction, self.raid_id, self.action)


class DeclineModal(discord.ui.Modal, title="Decline this raid"):
    reason = discord.ui.TextInput(label="Reason (optional)", required=False, max_length=200, style=discord.TextStyle.short)

    def __init__(self, cog: RaidsCog, raid_id: str):
        super().__init__()
        self.cog = cog
        self.raid_id = raid_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.respond(interaction, self.raid_id, "ABSENT", reason=str(self.reason.value or "").strip() or None)


def raid_view(raid_id: str, url: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in ACTIONS:
        view.add_item(RaidButton(action, raid_id))
    view.add_item(discord.ui.Button(label="View roster", style=discord.ButtonStyle.link, url=url))
    return view


class RaidsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, api: SiteApi, config: SyncConfig, runner: JobRunner):
        self.bot = bot
        self.api = api
        self.config = config
        for job_type, handler in (
            ("raid.post", self.job_post),
            ("raid.update", self.job_update),
            ("raid.remind", self.job_remind),
            ("raid.reserves.remind", self.job_reserves_remind),
            ("raid.lock", self.job_lock),
            ("raid.cancel", self.job_cancel),
            ("raid.close", self.job_close),
            ("raid.delete", self.job_delete),
        ):
            runner.handle(job_type, handler)

    # --- Discord objects ---------------------------------------------------------------

    def channel(self) -> discord.TextChannel:
        channel = self.bot.get_channel(self.config.raid_signups_channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"RAID_SIGNUPS_CHANNEL_ID {self.config.raid_signups_channel_id} is not a text channel the bot can see")
        return channel

    async def message_for(self, raid: Job) -> discord.Message:
        message_id = raid.get("discord", {}).get("messageId")
        if not message_id:
            raise RuntimeError(f"raid {raid.get('id')} has no message yet")
        return await self.channel().fetch_message(int(message_id))

    async def member_for(self, guild: discord.Guild, discord_id: Any) -> discord.Member | None:
        """Cache first, then the API: a raider who joined since the last gateway sync is still here."""
        member = guild.get_member(int(discord_id))
        if member is None:
            try:
                member = await guild.fetch_member(int(discord_id))
            except discord.HTTPException:
                return None  # anyone who left the server is skipped (§5)
        return member

    async def thread_for(self, raid: Job) -> discord.Thread | None:
        thread_id = raid.get("discord", {}).get("threadId")
        if not thread_id:
            return None
        thread = self.bot.get_channel(int(thread_id))
        if thread is None:
            try:
                thread = await self.bot.fetch_channel(int(thread_id))
            except discord.HTTPException:
                return None
        return thread if isinstance(thread, discord.Thread) else None

    # --- jobs ----------------------------------------------------------------------

    async def job_post(self, job: Job) -> dict[str, Any]:
        raid = await self.api.raid(str(job["payload"]["raidId"]))
        discord_ids = raid.get("discord") or {}
        if discord_ids.get("messageId"):
            # Already posted (a retry after a lost ack). The message and its thread are two
            # objects in a text channel, so a missing thread id is looked up, never guessed.
            message = await self.message_for(raid)
            thread_id = discord_ids.get("threadId") or (message.thread.id if message.thread else None)
            if not thread_id:
                thread_id = (await message.create_thread(name=render.base_title(raid), auto_archive_duration=10080)).id
            return {"threadId": str(thread_id), "messageId": str(message.id)}
        raid["late"] = bool(job["payload"].get("late")) or bool(raid.get("late"))
        message = await self.channel().send(embed=render.raid_embed(raid), view=raid_view(str(raid["id"]), str(raid.get("url"))))
        try:
            thread = await message.create_thread(name=render.base_title(raid), auto_archive_duration=10080)
        except discord.HTTPException:
            # The site only learns both ids together; leave nothing behind for the retry to duplicate.
            await message.delete()
            raise
        return {"threadId": str(thread.id), "messageId": str(message.id)}

    async def job_update(self, job: Job) -> None:
        raid = await self.api.raid(str(job["payload"]["raidId"]))
        message = await self.message_for(raid)
        status = str(raid.get("status"))
        if status in ("DONE", "CANCELLED"):
            # Reconcile (§6 step 7): a finished raid reads as its compact line, no buttons, thread archived.
            await message.edit(content=render.compact_line(raid), embed=None, view=None)
            thread = await self.thread_for(raid)
            if thread and not thread.archived:
                await thread.edit(archived=True)
            return
        await message.edit(content=None, embed=render.raid_embed(raid), view=raid_view(str(raid["id"]), str(raid.get("url"))))
        thread = await self.thread_for(raid)
        if thread and thread.archived:
            await thread.edit(archived=False)

    async def job_remind(self, job: Job) -> None:
        p = job["payload"]
        raid = await self.api.raid(str(p["raidId"]))
        thread = await self.thread_for(raid)
        if thread is None:
            raise RuntimeError("no thread to remind in")
        members = []
        for discord_id in p.get("discordIds", []):
            member = await self.member_for(thread.guild, discord_id)
            if member is not None:
                members.append(member)
        if not members:
            return
        hours = p.get("hours", 72)
        mentions = " ".join(m.mention for m in members)
        await thread.send(f"{mentions}\nThe raid is in about {hours} hours and you haven't answered yet. Accept, Tentative or Decline above, or on the web.", allowed_mentions=discord.AllowedMentions(users=members))

    async def job_reserves_remind(self, job: Job) -> None:
        p = job["payload"]
        discord_ids = p.get("discordIds", [])
        if not discord_ids:
            return
        raid = await self.api.raid(str(p["raidId"]))
        thread = await self.thread_for(raid)
        if thread is None:
            raise RuntimeError("no thread to remind in")
        members = []
        for discord_id in discord_ids:
            member = await self.member_for(thread.guild, discord_id)
            if member is not None:
                members.append(member)
        if not members:
            return
        mentions = " ".join(member.mention for member in members)
        reserves_url = f"{self.config.site_public_url}/members/calendar/{p['raidId']}?reserves=1"
        await thread.send(
            render.reserves_reminder(mentions, str(p["reservesLockAt"]), reserves_url),
            allowed_mentions=discord.AllowedMentions(users=members),
        )

    async def job_lock(self, job: Job) -> None:
        raid = await self.api.raid(str(job["payload"]["raidId"]))
        message = await self.message_for(raid)
        # A job handed out again after a lost ack must not announce twice: the message
        # already reading as locked means the first run got this far.
        announced = bool(message.embeds) and str(message.embeds[0].title or "").startswith(render.PREFIX["LOCKED"])
        await self.job_update(job)
        thread = await self.thread_for(raid)
        if thread and not announced:
            await thread.send("Sign-ups are locked. Officers can still change answers on the web.")

    async def job_cancel(self, job: Job) -> None:
        p = job["payload"]
        raid = await self.api.raid(str(p["raidId"]))
        reason = str(p.get("reason") or raid.get("cancelledReason") or "").strip() or None
        message = await self.message_for(raid)
        # Same re-hand guard as job_lock: once the message is the compact cancelled line,
        # the thread notice and the DMs have already gone out.
        announced = (message.content or "").startswith("❌")
        thread = await self.thread_for(raid)
        if thread and not announced:
            if thread.archived:
                await thread.edit(archived=False)
            await thread.send(f"This raid is cancelled{': ' + reason if reason else '.'}")
        await message.edit(content=render.compact_line(raid, cancelled_reason=reason), embed=None, view=None)
        # DM everyone who had accepted (§5); the site lists them in the payload.
        guild = self.channel().guild
        for discord_id in [] if announced else p.get("acceptedDiscordIds", []):
            member = await self.member_for(guild, discord_id)
            if member is None:
                continue
            try:
                await member.send(f"{render.base_title(raid)} is cancelled{': ' + reason if reason else '.'} Details on the web: {raid.get('url')}")
            except discord.HTTPException:
                log.info("could not DM %s about the cancellation", discord_id)
        if thread:
            await thread.edit(archived=True)

    async def job_close(self, job: Job) -> None:
        raid = await self.api.raid(str(job["payload"]["raidId"]))
        message = await self.message_for(raid)
        await message.edit(content=render.compact_line(raid), embed=None, view=None)
        thread = await self.thread_for(raid)
        if thread and not thread.archived:
            await thread.edit(archived=True)

    async def job_delete(self, job: Job) -> None:
        """The raid is gone from the site, so the payload carries the Discord ids and nothing
        is fetched. Quiet by design: no thread notice, no DMs (cancel is the loud path)."""
        p = job["payload"]
        thread = await self.thread_for({"discord": {"threadId": p.get("threadId")}})
        if thread is not None:
            try:
                await thread.delete()
            except discord.NotFound:
                pass  # already gone: a retry after a lost ack
            except discord.Forbidden:
                # Without Manage Threads the thread cannot go (nor be locked); the bot started
                # it, so it may still archive it.
                log.warning("raid %s: cannot delete thread %s, archiving it", p.get("raidId"), thread.id)
                try:
                    await thread.edit(archived=True)
                except discord.HTTPException:
                    log.warning("raid %s: cannot archive thread %s either", p.get("raidId"), thread.id)
        message_id = p.get("messageId")
        if message_id:
            try:
                await self.channel().get_partial_message(int(message_id)).delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                log.warning("raid %s: cannot delete message %s", p.get("raidId"), message_id)

    # --- clicks --------------------------------------------------------------------

    async def on_click(self, interaction: discord.Interaction, raid_id: str, action: str) -> None:
        if action == "decline":
            return await interaction.response.send_modal(DeclineModal(self, raid_id))
        await interaction.response.defer(ephemeral=True, thinking=True)
        if action == "bench":
            try:
                result = await self.api.bench(raid_id, interaction.user.id, key=str(interaction.id))
            except ApiError as e:
                await interaction.followup.send(e.reason, ephemeral=True)
                return
            await interaction.followup.send(render.viewer_line(result), ephemeral=True)
            return
        await self._respond_deferred(interaction, raid_id, action.upper())

    async def respond(self, interaction: discord.Interaction, raid_id: str, response: str, *, reason: str | None) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._respond_deferred(interaction, raid_id, response, reason=reason)

    async def _respond_deferred(self, interaction: discord.Interaction, raid_id: str, response: str, *, reason: str | None = None) -> None:
        try:
            result = await self.api.respond(raid_id, interaction.user.id, response, reason=reason, key=str(interaction.id))
        except ApiError as e:
            await interaction.followup.send(e.reason, ephemeral=True)
            return
        message = render.viewer_line(result)
        if response in ("ACCEPT", "TENTATIVE"):
            message += render.reserves_prompt(result)
        await interaction.followup.send(message, ephemeral=True)
