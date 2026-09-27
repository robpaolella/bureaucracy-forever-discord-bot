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


def snapshot_changed(before: discord.Member, after: discord.Member, watched: set[int]) -> bool:
    """A snapshot is worth sending when a role the roster reads, the shown name or the avatar
    changed. Class and group-finder roles from the panel are not the roster's business."""
    roles_before = {r.id for r in before.roles} & watched
    roles_after = {r.id for r in after.roles} & watched
    return roles_before != roles_after or before.display_name != after.display_name or before.display_avatar != after.display_avatar


def roles_after_change(member: discord.Member, add: list[discord.Role], remove: list[discord.Role]) -> list[discord.Role]:
    """The member's roles with `add` in and `remove` out, in one list for a single edit call, so
    Discord sends one update instead of two half-states."""
    keep = [r for r in member.roles if r not in remove and not r.is_default()]
    return keep + [r for r in add if r not in keep]


class MembersCog(commands.Cog):
    def __init__(self, bot: commands.Bot, api: SiteApi, config: SyncConfig, runner: JobRunner):
        self.bot = bot
        self.api = api
        self.config = config
        self._stop = asyncio.Event()
        # Listener posts go out one at a time, in order, so an older state never overwrites a newer one.
        self._send_lock = asyncio.Lock()
        runner.handle("member.roles.sync", self.job_roles_sync)
        runner.handle("officers.notify", self.job_officers_notify)

    # --- snapshot (§3) --------------------------------------------------------------------

    async def snapshot_loop(self, seconds: float) -> None:
        """The whole guild every `seconds` (SNAPSHOT_SECONDS), so the roster heals from anything the listeners missed."""
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
        if guild.unavailable:
            log.info("member snapshot skipped: guild unavailable")
            return
        if not guild.chunked:
            await asyncio.wait_for(guild.chunk(), timeout=30)
        # A full snapshot marks everyone it leaves out as gone, so an incomplete cache must not be sent as one.
        if not guild.chunked:
            log.warning("member snapshot skipped: cache holds %s of %s members", len(guild.members), guild.member_count)
            return
        members = [member_snapshot(m) for m in guild.members if not m.bot]
        if not members:
            return
        async with self._send_lock:
            result = await self.api.members_sync(members, full=True)
        changed = {k: v for k, v in result.items() if isinstance(v, int) and v}
        if changed:
            log.info("member snapshot: %s", changed)

    def _ours(self, member: discord.Member) -> bool:
        """Only the configured guild feeds the roster; a second server the bot sits in must never touch it."""
        return not member.bot and member.guild.id == self.config.guild_id

    async def send_one(self, member: discord.Member, *, left: bool = False) -> None:
        snap = member_snapshot(member)
        if left:
            snap["roles"] = []
        async with self._send_lock:
            try:
                await self.api.members_sync([snap], full=False)
            except ApiError as e:
                log.warning("member snapshot for %s: %s", member.id, e)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if self._ours(after) and snapshot_changed(before, after, self.config.watched_roles):
            await self.send_one(after)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if self._ours(member):
            await self.send_one(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        if self._ours(member):
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
        allowed = self.config.grantable_roles
        add = [guild.get_role(int(r)) for r in p.get("add", []) if str(r).isdigit() and int(r) in allowed]
        remove = [guild.get_role(int(r)) for r in p.get("remove", []) if str(r).isdigit() and int(r) in allowed]
        add = [r for r in add if r and r not in member.roles]
        remove = [r for r in remove if r and r in member.roles]
        if add or remove:
            # One edit, one gateway event: the site never sees a half-applied state.
            await member.edit(roles=roles_after_change(member, add, remove), reason="Web roster rank changed")

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
        guild = self.bot.get_guild(self.config.guild_id) if self.config.guild_id else None
        if guild is None:
            raise RuntimeError("GUILD_ID is not a guild the bot is in")
        return guild
