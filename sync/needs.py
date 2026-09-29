"""/recruitment (SYNC-SPEC.md §9.8): officers set a spec's recruitment need from Discord. Class,
then spec, then High / Medium / Closed; the bot writes it through `PUT /needs`, the same write
as the web needs editor, so the public recruitment page and the officer editor both follow."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .api import ApiError, SiteApi
from .config import SyncConfig

log = logging.getLogger("sync.needs")

Needs = dict[str, Any]
STATUS_LABEL = {"high": "High", "medium": "Medium", "closed": "Closed"}


def status_label(status: str) -> str:
    return STATUS_LABEL.get(status, status.title())


def class_summary(cls: Needs) -> str:
    """`High: Fury · Medium: Arms` for a class menu option; every spec closed says so. Pure."""
    parts = []
    for status in ("high", "medium"):
        specs = [s["name"] for s in cls.get("specs", []) if s.get("status") == status]
        if specs:
            parts.append(f"{status_label(status)}: {', '.join(specs)}")
    return (" · ".join(parts) or "All closed")[:100]


def find_class(needs: Needs, key: str) -> Needs | None:
    return next((c for c in needs.get("classes", []) if c.get("key") == key), None)


def set_local(needs: Needs, key: str, spec: str, status: str) -> None:
    """Mirror a saved change into the menus' copy, so the next pick shows it without a refetch."""
    for s in (find_class(needs, key) or {}).get("specs", []):
        if s.get("name") == spec:
            s["status"] = status


class NeedsView(discord.ui.View):
    """One ephemeral step of /recruitment; the menus are disabled when it times out."""

    def __init__(self, item: discord.ui.Item[Any]):
        super().__init__(timeout=300)
        self.add_item(item)
        self.message: discord.Message | None = None

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Select):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class ClassSelect(discord.ui.Select):
    def __init__(self, api: SiteApi, needs: Needs):
        self.api = api
        self.needs = needs
        options = [discord.SelectOption(label=str(c["label"]), value=str(c["key"]), description=class_summary(c)) for c in needs.get("classes", [])][:25]
        super().__init__(placeholder="Which class?", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        cls = find_class(self.needs, self.values[0])
        if cls is None:
            return await interaction.response.edit_message(content="That class is not on the list any more; run /recruitment again.", view=None)
        if not cls.get("specs"):
            return await interaction.response.edit_message(content=f"The website lists no {cls['label']} specs; tell whoever runs the site.", view=None)
        self.view.stop()  # or its timeout would later overwrite the next step's menu
        view = NeedsView(SpecSelect(self.api, self.needs, cls))
        await interaction.response.edit_message(content=f"**{cls['label']}**: which spec?", view=view)
        view.message = await interaction.original_response()


class SpecSelect(discord.ui.Select):
    def __init__(self, api: SiteApi, needs: Needs, cls: Needs):
        self.api = api
        self.needs = needs
        self.cls = cls
        options = [discord.SelectOption(label=str(s["name"]), value=str(s["name"]), description=f"Now: {status_label(str(s.get('status', 'closed')))}") for s in cls.get("specs", [])][:25]
        super().__init__(placeholder=f"Which {cls['label']} spec?", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        spec = next((s for s in self.cls.get("specs", []) if s.get("name") == self.values[0]), None)
        if spec is None:
            return await interaction.response.edit_message(content="That spec is not on the list any more; run /recruitment again.", view=None)
        self.view.stop()
        view = NeedsView(StatusSelect(self.api, self.needs, self.cls, spec))
        now = status_label(str(spec.get("status", "closed")))
        await interaction.response.edit_message(content=f"**{self.cls['label']} {spec['name']}** is **{now}** now. Set it to?", view=view)
        view.message = await interaction.original_response()


class StatusSelect(discord.ui.Select):
    def __init__(self, api: SiteApi, needs: Needs, cls: Needs, spec: Needs):
        self.api = api
        self.needs = needs
        self.cls = cls
        self.spec = spec
        statuses = [str(s) for s in needs.get("statuses") or STATUS_LABEL]
        current = spec.get("status")
        options = [discord.SelectOption(label=status_label(s), value=s, description="Current" if s == current else None) for s in statuses]
        super().__init__(placeholder="Priority...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        status = self.values[0]
        self.view.stop()
        label = f"{self.cls['label']} {self.spec['name']}"
        # The site may take longer than Discord's three seconds on a cold start.
        await interaction.response.defer()
        try:
            await self.api.set_need(str(self.cls["key"]), str(self.spec["name"]), status, interaction.user.id, key=str(interaction.id))
        except ApiError as e:
            await interaction.edit_original_response(content=e.reason, view=None)
            return
        except Exception as e:  # noqa: BLE001 — a timeout or refused connection
            log.warning("set need %s to %s failed: %s", label, status, e)
            await interaction.edit_original_response(content="Couldn't reach the website. Nothing changed; try again in a minute.", view=None)
            return
        set_local(self.needs, str(self.cls["key"]), str(self.spec["name"]), status)
        # Carry on with the class menu, so several specs can be set in one go.
        view = NeedsView(ClassSelect(self.api, self.needs))
        await interaction.edit_original_response(
            content=f"**{label}** is now **{status_label(status)}**. The website shows it within a minute.\nSet another, or dismiss this message.",
            view=view,
        )
        view.message = await interaction.original_response()


class NeedsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, api: SiteApi, config: SyncConfig):
        self.bot = bot
        self.api = api
        self.config = config

    @app_commands.command(name="recruitment", description="Officers: set a spec's recruitment priority on the website.")
    @app_commands.guild_only()
    async def recruitment(self, interaction: discord.Interaction) -> None:
        channel_id = self.config.recruitment_needs_channel_id
        if channel_id and interaction.channel_id != channel_id:
            return await interaction.response.send_message(f"Run this in <#{channel_id}>.", ephemeral=True)
        officer = self.config.role_officer_id
        if not officer:
            return await interaction.response.send_message("`ROLE_OFFICER_ID` is not set in the bot's config, so I can't tell who is an officer.", ephemeral=True)
        if not any(r.id == officer for r in getattr(interaction.user, "roles", [])):
            return await interaction.response.send_message("Only officers set recruitment needs.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            needs = await asyncio.wait_for(self.api.needs(), timeout=10)
        except ApiError as e:
            return await interaction.followup.send(e.reason, ephemeral=True)
        except Exception as e:  # noqa: BLE001
            log.warning("could not load recruitment needs: %s", e)
            return await interaction.followup.send("Couldn't reach the website. Try again in a minute.", ephemeral=True)
        if not needs.get("classes"):
            return await interaction.followup.send("The website sent no classes; tell whoever runs the site.", ephemeral=True)
        view = NeedsView(ClassSelect(self.api, needs))
        view.message = await interaction.followup.send("Which class's recruitment need do you want to change?", view=view, ephemeral=True, wait=True)
