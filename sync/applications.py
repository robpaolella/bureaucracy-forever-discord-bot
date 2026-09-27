"""#applications: forum posts for web applications, decisions from either side, notes both
ways (SYNC-SPEC.md §5, §8). Every visible change is a job the site queued; a click here only
calls the site and answers ephemerally."""

from __future__ import annotations

import logging
import re
from datetime import UTC
from typing import Any

import discord
from discord.ext import commands

from . import render
from .api import ApiError, SiteApi
from .config import SyncConfig
from .runner import JobRunner

log = logging.getLogger("sync.applications")

Job = dict[str, Any]


class DecisionButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sync:app:(?P<action>accept|decline):(?P<app_id>[A-Za-z0-9]+)"):
    """Accept / Decline on the starter message. Survives restarts: the id carries the action and the application."""

    def __init__(self, action: str, app_id: str):
        self.action = action
        self.app_id = app_id
        super().__init__(
            discord.ui.Button(
                label="Accept" if action == "accept" else "Decline",
                style=discord.ButtonStyle.success if action == "accept" else discord.ButtonStyle.danger,
                custom_id=f"sync:app:{action}:{app_id}",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
        return cls(match["action"], match["app_id"])

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: ApplicationsCog | None = interaction.client.get_cog("ApplicationsCog")  # type: ignore[assignment]
        if cog is None:
            return await interaction.response.send_message("The sync is not running.", ephemeral=True)
        await cog.on_decision_click(interaction, self.app_id, self.action)


def decision_view(app_id: str, url: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(DecisionButton("accept", app_id))
    view.add_item(DecisionButton("decline", app_id))
    view.add_item(discord.ui.Button(label="Open on web", style=discord.ButtonStyle.link, url=url))
    return view


def link_only_view(url: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Open on web", style=discord.ButtonStyle.link, url=url))
    return view


class ApplicationsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, api: SiteApi, config: SyncConfig, runner: JobRunner):
        self.bot = bot
        self.api = api
        self.config = config
        self._tags: dict[str, discord.ForumTag] = {}
        self._thread_app: dict[int, str] = {}
        for job_type, handler in (
            ("application.post", self.job_post),
            ("application.update", self.job_update),
            ("application.note.post", self.job_note_post),
            ("application.note.edit", self.job_note_edit),
            ("application.note.delete", self.job_note_delete),
            ("application.decide", self.job_decide),
            ("application.reopen", self.job_reopen),
            ("application.nudge", self.job_nudge),
        ):
            runner.handle(job_type, handler)

    # --- Discord objects ---------------------------------------------------------------

    def forum(self) -> discord.ForumChannel:
        channel = self.bot.get_channel(self.config.applications_forum_id)
        if not isinstance(channel, discord.ForumChannel):
            raise RuntimeError(f"APPLICATIONS_FORUM_ID {self.config.applications_forum_id} is not a forum channel the bot can see")
        return channel

    async def ensure_tags(self) -> dict[str, discord.ForumTag]:
        """§8: the bot creates missing tags on startup and caches their ids."""
        forum = self.forum()
        have = {t.name: t for t in forum.available_tags}
        missing = [n for n in render.TAG_NAMES if n not in have]
        if missing:
            tags = list(forum.available_tags) + [discord.ForumTag(name=n) for n in missing]
            forum = await forum.edit(available_tags=tags)
            have = {t.name: t for t in forum.available_tags}
        self._tags = have
        return have

    def tags(self, names: list[str]) -> list[discord.ForumTag]:
        return [self._tags[n] for n in names if n in self._tags]

    async def thread_for(self, app: Job) -> discord.Thread:
        thread_id = app.get("discordThreadId")
        if not thread_id:
            raise RuntimeError(f"application {app.get('id')} has no thread yet")
        thread = self.bot.get_channel(int(thread_id)) or await self.bot.fetch_channel(int(thread_id))
        if not isinstance(thread, discord.Thread):
            raise RuntimeError(f"{thread_id} is not a thread")
        return thread

    async def starter_message(self, app: Job, thread: discord.Thread) -> discord.Message:
        message_id = int(app.get("discordMessageId") or thread.id)
        return await thread.fetch_message(message_id)

    async def resolve_application_id(self, thread: discord.Thread) -> str | None:
        """Which application a thread belongs to: cached from jobs, else read off the starter's link button."""
        cached = self._thread_app.get(thread.id)
        if cached:
            return cached
        try:
            starter = await thread.fetch_message(thread.id)
        except discord.HTTPException:
            return None
        for row in starter.components:
            for item in getattr(row, "children", []):
                app_id = render.application_id_from_url(getattr(item, "url", None))
                if app_id:
                    self._thread_app[thread.id] = app_id
                    return app_id
        return None

    # --- jobs ----------------------------------------------------------------------

    async def job_post(self, job: Job) -> dict[str, Any]:
        app = await self.api.application(str(job["payload"]["applicationId"]))
        if app.get("discordThreadId"):
            # Already posted (a retried job): report the ids we have.
            return {"threadId": str(app["discordThreadId"]), "messageId": str(app.get("discordMessageId") or app["discordThreadId"])}
        if not self._tags:
            await self.ensure_tags()
        forum = self.forum()
        url = str(app.get("url"))
        created = await forum.create_thread(
            name=render.title_for(app),
            embed=render.application_embed(app, self.config.site_public_url),
            view=decision_view(str(app["id"]), url),
            applied_tags=self.tags(render.tags_for(app)),
        )
        self._thread_app[created.thread.id] = str(app["id"])
        return {"threadId": str(created.thread.id), "messageId": str(created.message.id)}

    async def job_update(self, job: Job) -> None:
        app = await self.api.application(str(job["payload"]["applicationId"]))
        thread = await self.thread_for(app)
        if not self._tags:
            await self.ensure_tags()
        pending = app.get("status") == "pending"
        # The update handler also fixes archive/lock/tag drift (§6 step 7).
        if thread.archived or thread.locked:
            await thread.edit(archived=False, locked=False)
        starter = await self.starter_message(app, thread)
        url = str(app.get("url"))
        await starter.edit(embed=render.application_embed(app, self.config.site_public_url), view=decision_view(str(app["id"]), url) if pending else link_only_view(url))
        await thread.edit(applied_tags=self.tags(render.tags_for(app)), archived=not pending, locked=not pending)

    async def job_note_post(self, job: Job) -> dict[str, Any]:
        p = job["payload"]
        app = await self.api.application(str(p["applicationId"]))
        thread = await self.thread_for(app)
        if thread.archived:
            await thread.edit(archived=False)
        message = await thread.send(render.note_line(str(p.get("author", "Officer")), str(p.get("body", ""))), allowed_mentions=discord.AllowedMentions.none())
        return {"messageId": str(message.id)}

    async def _find_note(self, job: Job) -> tuple[discord.Thread, discord.Message] | None:
        p = job["payload"]
        app = await self.api.application(str(p["applicationId"]))
        message_id = p.get("discordMessageId") or next((n.get("discordMessageId") for n in app.get("notes", []) if n.get("id") == p.get("noteId")), None)
        if not message_id:
            return None
        thread = await self.thread_for(app)
        try:
            return thread, await thread.fetch_message(int(message_id))
        except discord.NotFound:
            return None

    async def job_note_edit(self, job: Job) -> None:
        found = await self._find_note(job)
        if found:
            await found[1].edit(content=render.note_line(str(job["payload"].get("author", "Officer")), str(job["payload"].get("body", ""))))

    async def job_note_delete(self, job: Job) -> None:
        found = await self._find_note(job)
        if found:
            await found[1].delete()

    async def job_decide(self, job: Job) -> dict[str, Any]:
        p = job["payload"]
        app = await self.api.application(str(p["applicationId"]))
        thread = await self.thread_for(app)
        if thread.archived or thread.locked:
            await thread.edit(archived=False, locked=False)
        await thread.send(embed=render.decision_embed(p))
        dm_delivered = False
        applicant_id = p.get("applicantDiscordId")
        member = thread.guild.get_member(int(applicant_id)) if applicant_id else None
        if member is None and applicant_id:
            try:
                member = await thread.guild.fetch_member(int(applicant_id))
            except discord.HTTPException:
                member = None
        if member is not None:
            if p.get("status") == "accepted":
                await self.grant_member_roles(member)
            try:
                await member.send(str(p.get("dm") or ""))
                dm_delivered = True
            except discord.HTTPException:
                log.info("could not DM %s (%s)", member, applicant_id)
        await self.job_update(job)
        return {"dmDelivered": dm_delivered}

    async def grant_member_roles(self, member: discord.Member) -> None:
        """On accept: add Guild Member, remove Guest (§5). Never touches Officer."""
        guild = member.guild
        add = guild.get_role(self.config.role_guild_member_id)
        remove = guild.get_role(self.config.role_guest_id)
        if add and add not in member.roles:
            await member.add_roles(add, reason="Application accepted")
        if remove and remove in member.roles:
            await member.remove_roles(remove, reason="Application accepted")

    async def job_reopen(self, job: Job) -> None:
        p = job["payload"]
        app = await self.api.application(str(p["applicationId"]))
        thread = await self.thread_for(app)
        await thread.edit(archived=False, locked=False)
        await self.job_update(job)
        await thread.send(f"Reopened by **{p.get('reopenedBy', 'an officer')}**.", allowed_mentions=discord.AllowedMentions.none())

    async def job_nudge(self, job: Job) -> None:
        p = job["payload"]
        channel = self.bot.get_channel(self.config.officers_channel_id)
        if not isinstance(channel, discord.abc.Messageable):
            raise RuntimeError("OFFICERS_CHANNEL_ID is not a channel the bot can post in")
        url = f"{self.config.site_public_url}/officers/applications/{p['applicationId']}"
        await channel.send(f"Application from **{p.get('character', 'someone')}** has been pending 24h: {url}", allowed_mentions=discord.AllowedMentions.none())

    # --- clicks --------------------------------------------------------------------

    async def on_decision_click(self, interaction: discord.Interaction, app_id: str, action: str) -> None:
        status = "ACCEPTED" if action == "accept" else "DECLINED"
        try:
            await self.api.decide(app_id, status, interaction.user.id, reason=None, key=str(interaction.id))
        except ApiError as e:
            return await interaction.response.send_message(e.reason, ephemeral=True)
        await interaction.response.send_message(f"{'Accepted' if action == 'accept' else 'Declined'}. The thread updates in a moment.", ephemeral=True)

    # --- thread messages become notes ---------------------------------------------

    def _in_applications_forum(self, channel: discord.abc.Messageable | None) -> discord.Thread | None:
        return channel if isinstance(channel, discord.Thread) and channel.parent_id == self.config.applications_forum_id else None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        thread = self._in_applications_forum(message.channel)
        if thread is None or message.author.bot or not message.content.strip():
            return
        app_id = await self.resolve_application_id(thread)
        if not app_id:
            return
        try:
            await self.api.post_note(app_id, message_id=message.id, author_discord_id=message.author.id, body=message.content, created_at=message.created_at.astimezone(UTC).isoformat())
        except ApiError as e:
            if e.status == 403:
                return  # not an officer on the site: ignored, as §8 says
            log.warning("note from %s in %s: %s", message.author, thread.id, e)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        channel = self.bot.get_channel(payload.channel_id)
        if self._in_applications_forum(channel) is None:
            return
        content = payload.data.get("content")
        author = payload.data.get("author") or {}
        if not isinstance(content, str) or author.get("bot"):
            return
        try:
            await self.api.edit_note(payload.message_id, content)
        except ApiError as e:
            if e.status != 404:
                log.warning("note edit %s: %s", payload.message_id, e)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        channel = self.bot.get_channel(payload.channel_id)
        if self._in_applications_forum(channel) is None:
            return
        try:
            await self.api.delete_note(payload.message_id)
        except ApiError as e:
            if e.status != 404:
                log.warning("note delete %s: %s", payload.message_id, e)
