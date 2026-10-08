"""Rendering for #raid-signups (SYNC-SPEC.md §8, text-channel version). Pure functions over the
site's `GET /raids/:id` JSON so they can be unit-tested."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import discord

COLOURS = {
    "SCHEDULED": discord.Colour(0x2E8B8B),  # teal
    "LOCKED": discord.Colour(0xC9A24E),  # sand
    "DONE": discord.Colour(0x2E8B57),  # green
    "CANCELLED": discord.Colour(0xB3453B),  # red
}
PREFIX = {"SCHEDULED": "🟢 Open", "LOCKED": "🔒 Locked", "DONE": "✅ Done", "CANCELLED": "❌ Cancelled"}
ROLE_LABEL = {"tank": "Tanks", "healer": "Healers", "melee": "Melee", "ranged": "Ranged"}
ROLE_ORDER = ("tank", "healer", "melee", "ranged")


def _unix(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def base_title(raid: dict[str, Any]) -> str:
    """`Molten Core — Thu Nov 19`: the raid's name as the site generates it, also the thread name."""
    return str(raid.get("name", "Raid"))[:100]


def embed_title(raid: dict[str, Any]) -> str:
    """State prefix + name, with " · added late" for a raid created inside the post window."""
    status = str(raid.get("status", "SCHEDULED"))
    title = f"{PREFIX.get(status, status)} · {base_title(raid)}"
    if raid.get("late") and status == "SCHEDULED":
        title += " · added late"
    return title[:256]


def bar(count: int, need: int, width: int = 10) -> str:
    if need <= 0:
        return "·" * width
    filled = min(width, round(width * min(count, need) / need))
    return "█" * filled + "░" * (width - filled)


def composition_block(raid: dict[str, Any]) -> str:
    """Monospace: `Accepted 34 / 40` then a bar per role from requirements and roster acceptances."""
    req = raid.get("requirements") or {}
    bars = raid.get("bars") or {}
    size = (raid.get("template") or {}).get("size") or sum(int(req.get(r, 0)) for r in ROLE_ORDER)
    accepted = int((raid.get("counts") or {}).get("accepted", 0))
    lines = [f"Accepted {accepted:>2} / {size}"]
    for role in ROLE_ORDER:
        need = int(req.get(role, 0))
        have = int(bars.get(role, 0))
        lines.append(f"{ROLE_LABEL[role]:<8}{bar(have, need)} {have}/{need}")
    return "```\n" + "\n".join(lines) + "\n```"


def raid_embed(raid: dict[str, Any], now: datetime | None = None) -> discord.Embed:
    status = str(raid.get("status", "SCHEDULED"))
    start = _unix(str(raid["startsAt"]))
    end = _unix(str(raid["endsAt"]))
    locks = _unix(str(raid["locksAt"]))
    template = raid.get("template") or {}
    size = template.get("size")
    # template.short ("MC") is for the site's calendar chips; the embed has room for the full name.
    subtitle = f"{template.get('name', base_title(raid))}" + (f" · {size}-player" if size else "") + " · full clear"
    embed = discord.Embed(title=embed_title(raid), colour=COLOURS.get(status, COLOURS["SCHEDULED"]), url=raid.get("url"))
    embed.description = "\n".join(
        [
            subtitle,
            f"<t:{start}:F> – <t:{end}:t> · <t:{start}:R>",
            "-# Shown above in your local time; the guild runs on Pacific time.",
            composition_block(raid),
        ]
    )
    counts = raid.get("counts") or {}
    embed.add_field(name="Accepted", value=str(counts.get("accepted", 0)), inline=True)
    embed.add_field(name="Tentative", value=str(counts.get("tentative", 0)), inline=True)
    embed.add_field(name="Declined", value=str(counts.get("declined", 0)), inline=True)
    embed.add_field(name="Not answered", value=str(counts.get("unanswered", 0)), inline=True)
    embed.add_field(name="Bench", value=str(counts.get("bench", 0)), inline=True)
    embed.add_field(name="Locks at", value=f"<t:{locks}:f>", inline=True)
    if raid.get("notes"):
        embed.add_field(name="Notes", value=str(raid["notes"])[:1024], inline=False)
    # Footers cannot carry <t:> markup; the embed timestamp renders "Updated" in each viewer's zone.
    embed.timestamp = now or datetime.now().astimezone()
    embed.set_footer(text="Updated · Full roster, bench and who hasn't answered are on the web")
    return embed


def compact_line(raid: dict[str, Any], *, cancelled_reason: str | None = None) -> str:
    """After close or cancel the message becomes one line (§8). The reason comes from the cancel
    job's payload the first time and from the site's `cancelledReason` on every re-render after,
    so reconcile never wipes it."""
    status = str(raid.get("status", "DONE"))
    if status == "CANCELLED" or cancelled_reason is not None:
        reason = cancelled_reason or raid.get("cancelledReason") or "no reason given"
        return f"❌ {base_title(raid)} · cancelled: {reason}"[:2000]
    attended = int((raid.get("counts") or {}).get("attended", 0))
    return f"✅ {base_title(raid)} · {attended} attended"[:2000]


def viewer_line(result: dict[str, Any]) -> str:
    """The ephemeral answer after a click, from the site's response."""
    viewer = result.get("viewer") or {}
    standing = str(viewer.get("standing", "ROSTER"))
    response = viewer.get("response")
    words = {"accept": "Accept", "tentative": "Tentative", "absent": "Decline", None: "no answer"}
    where = "on the bench" if standing == "BENCH" else "on the roster"
    return f"You're set to **{words.get(response, str(response))}**, {where}."


def reserves_prompt(result: dict[str, Any]) -> str:
    """The optional reserve-picker line after an Accept or Tentative response."""
    viewer = result.get("viewer") or {}
    if (
        viewer.get("response") in ("accept", "tentative")
        and viewer.get("lootTable") is True
        and viewer.get("reservesLocked") is False
        and viewer.get("reservesComplete") is False
    ):
        return f"\nSelect your loot reserves for this raid: {viewer.get('reservesUrl')}"
    return ""
