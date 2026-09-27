"""The rest of "Set my main" (SYNC-SPEC.md §9.7): after the class roles are granted, ask the spec,
the raid role when the spec fills two, then the two-part character name, and write the main to
the web roster. Menus come from the site's /classes so the class data has one copy."""

from __future__ import annotations

import asyncio
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


async def load_classes(api: SiteApi, *, timeout_s: float = 3.0) -> dict[str, Any]:
    """The site's class table, cached for an hour; it changes only with a deploy. An empty or
    odd answer is not cached, so one bad response cannot switch the flow off for an hour."""
    global _classes_cache
    now = time.monotonic()
    if _classes_cache and now - _classes_cache[0] < CLASSES_TTL_S:
        return _classes_cache[1]
    data = await asyncio.wait_for(api.classes(), timeout=timeout_s)
    if isinstance(data.get("classes"), list) and data["classes"]:
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


def split_name(name: str) -> tuple[str, str]:
    """`Red Tape` -> ("Red", "Tape"); a one-part name leaves the second blank."""
    first, _, second = name.strip().partition(" ")
    return first, second.strip()


def current_main_for(member: dict[str, Any] | None, cls: dict[str, Any]) -> dict[str, Any] | None:
    """The member's web main when it is this class, so the flow can mark and prefill it. A main of
    another class is a different character, so nothing carries over."""
    main = (member or {}).get("main")
    if not isinstance(main, dict) or str(main.get("class", "")).lower() != str(cls.get("key", "")).lower():
        return None
    return main


async def load_member(api: SiteApi, discord_id: int, *, timeout_s: float = 3.0) -> dict[str, Any] | None:
    """What the site has for this member, or None when it has nothing or cannot say in time;
    the flow still works without it, just without the current main marked."""
    try:
        return await asyncio.wait_for(api.member(discord_id), timeout=timeout_s)
    except Exception as e:  # noqa: BLE001 — only the prefill is lost
        log.warning("could not load member %s from the site: %s", discord_id, e)
        return None


def summary(classes: dict[str, Any], main: dict[str, Any]) -> str:
    """`Red Tape — Feral Druid (Tank)` from the site's answer."""
    cls = class_entry(classes, str(main.get("class", ""))) or {}
    return f"**{main.get('name', '?')}** — {main.get('spec', '?')} {cls.get('label', str(main.get('class', '')).title())} ({role_label(classes, str(main.get('raidRole', '')))})"


class PickerView(discord.ui.View):
    """An ephemeral step of the flow; after the timeout the menu is disabled instead of failing."""

    def __init__(self, item: discord.ui.Item[Any]):
        super().__init__(timeout=180)
        self.add_item(item)
        self.message: discord.InteractionMessage | None = None

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Select):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


def current_note(text: str, is_current: bool) -> str:
    """An option's description, marked when it is what the member has now. Marked rather than
    pre-selected: Discord sends nothing when a pre-selected option is picked again."""
    if not is_current:
        return text
    return f"{text} · your current pick" if text else "Your current pick"


class SpecSelect(discord.ui.Select):
    def __init__(self, api: SiteApi, classes: dict[str, Any], cls: dict[str, Any], current: dict[str, Any] | None = None):
        self.api = api
        self.classes = classes
        self.cls = cls
        self.current = current
        now = (current or {}).get("spec")
        options = [
            discord.SelectOption(label=str(s["name"]), value=str(s["name"]), description=current_note(" or ".join(role_label(classes, r) for r in s.get("roles", [])), s["name"] == now))
            for s in cls.get("specs", [])
        ]
        super().__init__(placeholder=f"Which {cls.get('label', 'class')} spec is your main?", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        spec = next((s for s in self.cls.get("specs", []) if s["name"] == self.values[0]), None)
        if spec is None:
            return await interaction.response.edit_message(content="That spec is not on the list any more; start again.", view=None)
        roles = [str(r) for r in spec.get("roles", [])]
        if not roles:
            return await interaction.response.edit_message(content="The site lists no raid role for that spec; tell an officer.", view=None)
        if len(roles) > 1:
            # Feral and friends: the spec alone does not say which seat they fill.
            view = PickerView(RoleSelect(self.api, self.classes, self.cls, spec, roles, self.current))
            await interaction.response.edit_message(content=f"**{spec['name']}** can fill more than one raid role. Which is yours?", view=view)
            view.message = await interaction.original_response()
            return
        await interaction.response.send_modal(NameModal(self.api, self.classes, self.cls, spec, roles[0], self.current))


class RoleSelect(discord.ui.Select):
    def __init__(self, api: SiteApi, classes: dict[str, Any], cls: dict[str, Any], spec: dict[str, Any], roles: list[str], current: dict[str, Any] | None = None):
        self.api = api
        self.classes = classes
        self.cls = cls
        self.spec = spec
        self.current = current
        # The raid role is only "current" when the spec is too; a Feral tank who was Balance before has none.
        now = (current or {}).get("raidRole") if (current or {}).get("spec") == spec.get("name") else None
        options = [discord.SelectOption(label=role_label(classes, r), value=r, description=current_note("", r == now) or None) for r in roles]
        super().__init__(placeholder="Your raid role...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NameModal(self.api, self.classes, self.cls, self.spec, self.values[0], self.current))


class NameModal(discord.ui.Modal, title="Your main's name"):
    first = discord.ui.TextInput(label="First name", placeholder="Red", min_length=2, max_length=12)
    second = discord.ui.TextInput(label="Second name", placeholder="Tape", min_length=2, max_length=12, required=False)

    def __init__(self, api: SiteApi, classes: dict[str, Any], cls: dict[str, Any], spec: dict[str, Any], role: str, current: dict[str, Any] | None = None):
        super().__init__()
        self.api = api
        self.classes = classes
        self.cls = cls
        self.spec = spec
        self.role = role
        if current and current.get("name"):
            # Keeping the same character: the name is already there, so changing the spec is two clicks.
            self.first.default, self.second.default = split_name(str(current["name"]))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        first = str(self.first.value or "").strip()
        second = str(self.second.value or "").strip()
        if not name_ok(first, second):
            # The picker stays, so they can open the modal again.
            return await interaction.response.send_message("Names are 2 to 12 letters each, letters only.", ephemeral=True)
        # The modal came from the picker message; answering there replaces the picker with the result.
        await interaction.response.defer()
        body = {"firstName": first, "secondName": second, "wowClass": self.cls.get("key"), "spec": self.spec.get("name"), "raidRole": self.role}
        try:
            result = await self.api.set_main(interaction.user.id, body, key=str(interaction.id))
        except ApiError as e:
            text = "The website hasn't picked you up yet; try again in a few minutes. Your Discord class roles are set." if e.status == 404 else e.reason
            await interaction.edit_original_response(content=text, view=None)
            return
        except Exception as e:  # noqa: BLE001 — a timeout or a refused connection, not the member's doing
            log.warning("set main for %s failed: %s", interaction.user.id, e)
            await interaction.edit_original_response(content="Couldn't reach the website roster. Your Discord class roles are set; try Set my main again later.", view=None)
            return
        await interaction.edit_original_response(content=f"Main set: {summary(self.classes, result.get('main') or {})}. The roster on the web is updated.", view=None)


async def continue_main_flow(interaction: discord.Interaction, class_label: str, api: SiteApi, done_text: str) -> bool:
    """After the class roles: show the spec picker on the same ephemeral message. The caller has
    deferred already, so this edits the original response. False when the site cannot be
    reached or does not know the class, so the caller finishes the old way."""
    classes_or_error, member = await asyncio.gather(load_classes(api), load_member(api, interaction.user.id), return_exceptions=True)
    if isinstance(classes_or_error, BaseException):
        # The class roles are already granted; the roster part can wait.
        log.warning("could not load classes from the site: %s", classes_or_error)
        return False
    classes = classes_or_error
    cls = class_entry(classes, class_label)
    if cls is None:
        log.warning("the site's class table has no %s", class_label)
        return False
    current = current_main_for(member if isinstance(member, dict) else None, cls)
    view = PickerView(SpecSelect(api, classes, cls, current))
    label = cls.get("label", class_label)
    if current:
        ask = f"Now the roster. It has {summary(classes, current)}. Pick the same spec to keep it, or another; the name comes next."
    else:
        ask = f"Now the roster: which **{label}** spec is your main?"
    await interaction.edit_original_response(content=f"{done_text}\n\n{ask}", view=view)
    view.message = await interaction.original_response()
    return True
