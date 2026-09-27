"""The rest of "Set my main" (SYNC-SPEC.md §9.7): after the class roles are granted, ask the spec,
the raid role when the spec fills two, then the two-part character name, and write the main to
the web roster. Menus come from the site's /classes so the class data has one copy."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import discord

from .api import ApiError, SiteApi

log = logging.getLogger("sync.mains")

NAME_PART = re.compile(r"^[A-Za-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u00FF]{2,12}$")
CLASSES_TTL_S = 3600.0

_classes_cache: tuple[float, dict[str, Any]] | None = None


async def load_classes(api: SiteApi) -> dict[str, Any]:
    """The site's class table, cached for an hour; it changes only with a deploy."""
    global _classes_cache
    now = time.monotonic()
    if _classes_cache and now - _classes_cache[0] < CLASSES_TTL_S:
        return _classes_cache[1]
    data = await api.classes()
    _classes_cache = (now, data)
    return data


def class_entry(classes: dict[str, Any], label: str) -> dict[str, Any] | None:
    """The class whose label matches the panel's ("Druid"), or None."""
    for c in classes.get("classes", []):
        if str(c.get("label", "")).lower() == label.lower() or str(c.get("key", "")).lower() == label.lower():
            return c
    return None


def role_label(classes: dict[str, Any], role: str) -> str:
    return str((classes.get("roles") or {}).get(role, role.title()))


def name_ok(first: str, second: str) -> bool:
    """Two names, 2 to 12 letters each; the second may be left blank."""
    return bool(NAME_PART.match(first.strip())) and (not second.strip() or bool(NAME_PART.match(second.strip())))


def summary(classes: dict[str, Any], main: dict[str, Any]) -> str:
    """`Red Tape — Feral Druid (Tank)` from the site's answer."""
    cls = class_entry(classes, str(main.get("class", ""))) or {}
    return f"**{main.get('name', '?')}** — {main.get('spec', '?')} {cls.get('label', str(main.get('class', '')).title())} ({role_label(classes, str(main.get('raidRole', '')))})"


class SpecSelect(discord.ui.Select):
    def __init__(self, api: SiteApi, classes: dict[str, Any], cls: dict[str, Any]):
        self.api = api
        self.classes = classes
        self.cls = cls
        options = [discord.SelectOption(label=str(s["name"]), value=str(s["name"]), description=" or ".join(role_label(classes, r) for r in s.get("roles", []))) for s in cls.get("specs", [])]
        super().__init__(placeholder=f"Which {cls.get('label', 'class')} spec is your main?", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        spec = next((s for s in self.cls.get("specs", []) if s["name"] == self.values[0]), None)
        if spec is None:
            return await interaction.response.edit_message(content="That spec is not on the list any more; start again.", view=None)
        roles = [str(r) for r in spec.get("roles", [])]
        if len(roles) > 1:
            # Feral and friends: the spec alone does not say which seat they fill.
            view = discord.ui.View(timeout=180)
            view.add_item(RoleSelect(self.api, self.classes, self.cls, spec, roles))
            return await interaction.response.edit_message(content=f"**{spec['name']}** can fill more than one raid role. Which is yours?", view=view)
        await interaction.response.send_modal(NameModal(self.api, self.classes, self.cls, spec, roles[0] if roles else "melee"))


class RoleSelect(discord.ui.Select):
    def __init__(self, api: SiteApi, classes: dict[str, Any], cls: dict[str, Any], spec: dict[str, Any], roles: list[str]):
        self.api = api
        self.classes = classes
        self.cls = cls
        self.spec = spec
        super().__init__(placeholder="Your raid role...", min_values=1, max_values=1, options=[discord.SelectOption(label=role_label(classes, r), value=r) for r in roles])

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NameModal(self.api, self.classes, self.cls, self.spec, self.values[0]))


class NameModal(discord.ui.Modal, title="Your main's name"):
    first = discord.ui.TextInput(label="First name", placeholder="Red", min_length=2, max_length=12)
    second = discord.ui.TextInput(label="Second name", placeholder="Tape", min_length=2, max_length=12, required=False)

    def __init__(self, api: SiteApi, classes: dict[str, Any], cls: dict[str, Any], spec: dict[str, Any], role: str):
        super().__init__()
        self.api = api
        self.classes = classes
        self.cls = cls
        self.spec = spec
        self.role = role

    async def on_submit(self, interaction: discord.Interaction) -> None:
        first = str(self.first.value or "").strip()
        second = str(self.second.value or "").strip()
        if not name_ok(first, second):
            return await interaction.response.send_message("Names are 2 to 12 letters each, letters only.", ephemeral=True)
        await interaction.response.defer(ephemeral=True, thinking=True)
        body = {"firstName": first, "secondName": second, "wowClass": self.cls.get("key"), "spec": self.spec.get("name"), "raidRole": self.role}
        try:
            result = await self.api.set_main(interaction.user.id, body, key=str(interaction.id))
        except ApiError as e:
            await interaction.followup.send(e.reason, ephemeral=True)
            return
        await interaction.followup.send(f"Main set: {summary(self.classes, result.get('main') or {})}. The roster on the web is updated.", ephemeral=True)


async def continue_main_flow(interaction: discord.Interaction, class_label: str, api: SiteApi, done_text: str) -> bool:
    """After the class roles: show the spec picker on the same ephemeral message. False when the
    site cannot be reached, so the caller finishes the old way."""
    try:
        classes = await load_classes(api)
    except Exception as e:  # noqa: BLE001 — the class roles are already granted; the roster part can wait
        log.warning("could not load classes from the site: %s", e)
        return False
    cls = class_entry(classes, class_label)
    if cls is None:
        return False
    view = discord.ui.View(timeout=180)
    view.add_item(SpecSelect(api, classes, cls))
    await interaction.response.edit_message(content=f"{done_text}\n\nNow the roster: which **{cls.get('label', class_label)}** spec is your main?", view=view)
    return True
