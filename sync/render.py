"""Discord rendering for applications (SYNC-SPEC.md §8 #applications). Pure functions over the
site's JSON so they can be unit-tested; the cog turns their output into messages."""

from __future__ import annotations

import re
from typing import Any

import discord

COLOUR_PENDING = discord.Colour(0x2E8B8B)  # teal
COLOUR_ACCEPTED = discord.Colour(0x2E8B57)  # green
COLOUR_DECLINED = discord.Colour(0xB3453B)  # red

PATH_LABEL = {"raider": "Raider", "social": "Social"}
STATUS_LABEL = {"pending": "Pending", "accepted": "Accepted", "declined": "Declined"}
TAG_NAMES = ("Raider", "Social", "Pending", "Accepted", "Declined")

_APP_URL = re.compile(r"/officers/applications/([A-Za-z0-9]+)")


def class_label(app: dict[str, Any]) -> str | None:
    c = app.get("wowClass")
    return str(c).capitalize() if c else None


def title_for(app: dict[str, Any]) -> str:
    """Forum post title: `Name — Class (Spec) — Path`, or `Name — Social`."""
    name = str(app.get("character", "Applicant"))
    path = str(app.get("path", "raider"))
    if path == "social":
        return f"{name} — Social"
    cls = class_label(app)
    spec = app.get("spec")
    middle = f"{cls} ({spec})" if cls and spec else (cls or "Raider")
    return f"{name} — {middle} — {PATH_LABEL.get(path, path.title())}"[:100]


def embed_title(app: dict[str, Any]) -> str:
    """Embed title: `Name · Class · Spec`, trimmed to what is known. (§8 lists a role too; the site's application payload carries none.)"""
    parts = [str(app.get("character", "Applicant"))]
    cls = class_label(app)
    if cls:
        parts.append(cls)
    if app.get("spec"):
        parts.append(str(app["spec"]))
    return " · ".join(parts)[:256]


def tags_for(app: dict[str, Any]) -> list[str]:
    return [PATH_LABEL.get(str(app.get("path")), "Raider"), STATUS_LABEL.get(str(app.get("status")), "Pending")]


def application_embed(app: dict[str, Any], public_url: str) -> discord.Embed:
    status = str(app.get("status", "pending"))
    colour = {"accepted": COLOUR_ACCEPTED, "declined": COLOUR_DECLINED}.get(status, COLOUR_PENDING)
    embed = discord.Embed(title=embed_title(app), colour=colour, url=app.get("url") or f"{public_url}/officers/applications/{app.get('id')}")
    embed.add_field(name="Discord", value=str(app.get("discordName") or "—"), inline=True)
    embed.add_field(name="Path", value=PATH_LABEL.get(str(app.get("path")), "Raider"), inline=True)
    embed.add_field(name="Status", value=STATUS_LABEL.get(status, status.title()), inline=True)
    if app.get("logsUrl"):
        embed.add_field(name="Logs", value=str(app["logsUrl"])[:1024], inline=False)
    for answer in app.get("answers") or []:
        text = str(answer.get("answer", "")).strip()
        if not text:
            continue
        long = len(text) > 60
        embed.add_field(name=str(answer.get("label", answer.get("key", "Answer")))[:256], value=text[:1024], inline=not long)
    if status != "pending" and app.get("decidedBy"):
        embed.add_field(name="Decision", value=f"{STATUS_LABEL[status]} by {app['decidedBy']}", inline=False)
    embed.set_footer(text="Submitted on the web")
    return embed


def decision_embed(payload: dict[str, Any]) -> discord.Embed:
    """The decision notice posted in the thread (§5 application.decide)."""
    status = str(payload.get("status", "declined"))
    accepted = status == "accepted"
    via = "Discord" if payload.get("source") == "discord" else "the web"
    embed = discord.Embed(
        title=f"{'Accepted' if accepted else 'Declined'} · {payload.get('character', '')}".strip(" ·"),
        description=f"By **{payload.get('decidedBy', 'an officer')}** via {via}." + (f"\n{payload['reason']}" if payload.get("reason") else ""),
        colour=COLOUR_ACCEPTED if accepted else COLOUR_DECLINED,
    )
    return embed


def note_line(author: str, body: str) -> str:
    """A web note mirrored into the thread: `**Name** (web) · body`."""
    return f"**{author}** (web) · {body}"[:2000]


def application_id_from_url(url: str | None) -> str | None:
    """The application id inside an `Open on web` link, used to map a thread back to its application."""
    if not url:
        return None
    m = _APP_URL.search(url)
    return m.group(1) if m else None
