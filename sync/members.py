"""member.roles.sync and officers.notify (SYNC-SPEC.md §5), and the member snapshot the roster
follows (§3): the whole guild each tick, one member on every role change, join or leave."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord.ext import commands

from .api import ApiError, SiteApi
from .config import SyncConfig
from .runner import JobRunner

log = logging.getLogger("sync.members")

Job = dict[str, Any]


def member_snapshot(member: discord.Member) -> dict[str, Any]:
    """What the site stores about a member: id, the name people see, avatar, role ids. Pure."""
    avatar = member.display_avatar.url if member.display_avatar else None
    return {"discordId": str(member.id), "name": (member.display_name or member.name)[:80], "avatarUrl": avatar, "roles": [str(r.id) for r in member.roles if r.id != member.guild.id]}


def roles_changed(before: discord.Member, after: discord.Member) -> bool:
    """A snapshot is worth sending when the roles, the shown name or the avatar changed."""
    return [r.id for r in before.roles] != [r.id for r in after.roles] or before.display_name != after.display_name or before.display_avatar != after.display_avatar


class MembersCog(commands.Cog):
    def __init__(self, bot: commands.Bot, api: SiteApi, config: SyncConfig, runner: JobRunner):
        self.bot = bot
        self.api = api
        self.config = config
        self._stop = asyncio.Event()
        runner.handle("member.roles.sync", self.job_roles_sync)
        runner.handle("officers.notify", self.job_officers_notify)

    def allowed_roles(self) -> set[int]:
        """Only ever Guild Member, Guest, Raider, Trial and Social. Never Officer: that role grants site access."""
        return {r for r in (self.config.role_guild_member_id, self.config.role_guest_id, self.config.role_raider_id, self.config.role_trial_id, self.config.role_social_id) if r}

    # --- snapshot (§3) --------------------------------------------------------------------

    async def snapshot_loop(self, seconds: float) -> None:
        """The whole guild every `seconds`, so the roster heals from anything the listeners missed."""
        while not self._stop.is_set():
            try:
                await self.send_full_snapshot()
            except ApiError as e:
                log.warning("member snapshot: %s", e)
            except Exception:  # noqa: BLE001
                log.exception("member snapshot failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=seconds)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    async def send_full_snapshot(self) -> None:
        guild = self._guild()
        if not guild.chunked:
            await guild.chunk()
        members = [member_snapshot(m) for m in guild.members if not m.bot]
        if not members:
            return
        result = await self.api.members_sync(members, full=True)
        changed = {k: v for k, v in result.items() if isinstance(v, int) and v}
        if changed:
            log.info("member snapshot: %s", changed)

    async def send_one(self, member: discord.Member, *, left: bool = False) -> None:
        snap = member_snapshot(member)
        if left:
            snap["roles"] = []
        try:
            await self.api.members_sync([snap], full=False)
        except ApiError as e:
            log.warning("member snapshot for %s: %s", member.id, e)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if not after.bot and roles_changed(before, after):
            await self.send_one(after)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if not member.bot:
            await self.send_one(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if not member.bot:
            await self.send_one(member, left=True)

    # --- jobs (§5) --------------------------------------------------------------------------

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
