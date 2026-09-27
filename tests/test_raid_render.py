from datetime import UTC, datetime

from sync import raid_render as render

RAID = {
    "id": "r1",
    "name": "Molten Core — Thu Nov 19",
    "status": "SCHEDULED",
    "startsAt": "2026-11-20T04:00:00.000Z",
    "endsAt": "2026-11-20T07:00:00.000Z",
    "locksAt": "2026-11-20T02:00:00.000Z",
    "late": False,
    "template": {"name": "Molten Core", "short": "MC", "size": 40},
    "requirements": {"tank": 4, "healer": 12, "melee": 10, "ranged": 14},
    "bars": {"tank": 3, "healer": 12, "melee": 5, "ranged": 14},
    "counts": {"accepted": 34, "tentative": 2, "declined": 1, "unanswered": 3, "bench": 2, "roster": 40, "attended": 0},
    "notes": None,
    "url": "https://www.bureauguild.com/members/calendar/r1",
    "discord": {"threadId": None, "messageId": None},
}


def test_titles_follow_state_and_lateness():
    assert render.embed_title(RAID) == "🟢 Open · Molten Core — Thu Nov 19"
    assert render.embed_title({**RAID, "late": True}) == "🟢 Open · Molten Core — Thu Nov 19 · added late"
    assert render.embed_title({**RAID, "status": "LOCKED", "late": True}) == "🔒 Locked · Molten Core — Thu Nov 19"
    assert render.base_title(RAID) == "Molten Core — Thu Nov 19"


def test_composition_block_and_bars():
    block = render.composition_block(RAID)
    assert "Accepted 34 / 40" in block
    assert "Tanks   ████████░░ 3/4" in block
    assert "Healers ██████████ 12/12" in block
    assert render.bar(0, 4) == "░" * 10 and render.bar(4, 4) == "█" * 10 and render.bar(5, 0) == "·" * 10


def test_embed_has_six_inline_fields_timestamps_and_footer():
    e = render.raid_embed(RAID)
    assert [f.name for f in e.fields] == ["Accepted", "Tentative", "Declined", "Not answered", "Bench", "Locks at"]
    assert all(f.inline for f in e.fields)
    start = int(datetime(2026, 11, 20, 4, tzinfo=UTC).timestamp())
    locks = int(datetime(2026, 11, 20, 2, tzinfo=UTC).timestamp())
    assert f"<t:{start}:F>" in (e.description or "") and f"<t:{locks}:f>" in e.fields[5].value
    assert e.timestamp is not None
    assert e.colour == render.COLOURS["SCHEDULED"]
    assert "on the web" in (e.footer.text or "")


def test_compact_lines_and_viewer_line():
    assert render.compact_line({**RAID, "status": "DONE", "counts": {**RAID["counts"], "attended": 38}}) == "✅ Molten Core — Thu Nov 19 · 38 attended"
    assert render.compact_line(RAID, cancelled_reason="server down") == "❌ Molten Core — Thu Nov 19 · cancelled: server down"
    # Reconcile re-renders a cancelled raid with no payload reason; the site's field keeps it.
    assert render.compact_line({**RAID, "status": "CANCELLED", "cancelledReason": "Not enough healers"}) == "❌ Molten Core — Thu Nov 19 · cancelled: Not enough healers"
    assert render.compact_line({**RAID, "status": "CANCELLED"}) == "❌ Molten Core — Thu Nov 19 · cancelled: no reason given"
    assert render.viewer_line({"viewer": {"standing": "BENCH", "response": "accept"}}) == "You're set to **Accept**, on the bench."
    assert render.viewer_line({"viewer": {"standing": "ROSTER", "response": "absent"}}) == "You're set to **Decline**, on the roster."
