"""member.roles.sync and officers.notify (SYNC-SPEC.md §5)."""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord.ext import commands

from .config import SyncConfig
from .runner import JobRunner

log = logging.getLogger("sync.members")

Job = dict[str, Any]


class MembersCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: SyncConfig, runner: JobRunner):
        self.bot = bot
        self.config = config
        runner.handle("member.roles.sync", self.job_roles_sync)
        runner.handle("officers.notify", self.job_officers_notify)

    def allowed_roles(self) -> set[int]:
        """Only ever Guild Member, Guest and Raider. Never Officer: that role grants site access."""
        return {r for r in (self.config.role_guild_member_id, self.config.role_guest_id, self.config.role_raider_id) if r}

    async def job_roles_sync(self, job: Job) -> None:
        p = job["payload"]
        guild = self._guild()
        member = guild.get_member(int(p["discordId"]))
        if member is None:
            try:
                member = await guild.fetch_member(int(p["discordId"]))
            except discord.NotFound:
                log.info("roles sync: %s is not in the server", p["discordId"])
                return
        allowed = self.allowed_roles()
        add = [guild.get_role(int(r)) for r in p.get("add", []) if int(r) in allowed]
        remove = [guild.get_role(int(r)) for r in p.get("remove", []) if int(r) in allowed]
        add = [r for r in add if r and r not in member.roles]
        remove = [r for r in remove if r and r in member.roles]
        if add:
            await member.add_roles(*add, reason="Web roster rank changed")
        if remove:
            await member.remove_roles(*remove, reason="Web roster rank changed")

    async def job_officers_notify(self, job: Job) -> None:
        p = job["payload"]
        if p.get("silent"):
            return
        text = str(p.get("text", "")).strip()
        if not text:
            return
        channel = self.bot.get_channel(self.config.officers_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError("OFFICERS_CHANNEL_ID is not a channel the bot can post in")
        await channel.send(text[:2000], allowed_mentions=discord.AllowedMentions.none())

    def _guild(self) -> discord.Guild:
        guild = self.bot.guilds[0] if self.bot.guilds else None
        if guild is None:
            raise RuntimeError("the bot is not in a guild")
        return guild
