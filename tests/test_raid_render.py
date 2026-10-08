import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sync import raid_render as render
from sync.raids import RaidsCog

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


def test_reserves_reminder_mentions_members_with_the_lock_time_and_picker_link():
    assert render.reserves_reminder(
        "<@1> <@2>",
        "2026-11-20T02:00:00.000Z",
        "https://www.bureauguild.com/members/calendar/r1?reserves=1",
    ) == (
        "<@1> <@2>\nYou haven't picked both loot reserves for this raid. "
        "Choose them before <t:1795140000:F>: "
        "https://www.bureauguild.com/members/calendar/r1?reserves=1"
    )


def test_reserves_prompt_only_for_an_incomplete_open_loot_reservation():
    result = {
        "viewer": {
            "response": "accept",
            "lootTable": True,
            "reservesLocked": False,
            "reservesComplete": False,
            "reservesUrl": "https://www.bureauguild.com/members/calendar/r1?reserves=1",
        }
    }
    assert render.reserves_prompt(result) == "\nSelect your loot reserves for this raid: https://www.bureauguild.com/members/calendar/r1?reserves=1"
    assert render.reserves_prompt({**result, "viewer": {**result["viewer"], "response": "absent"}}) == ""
    for field, value in (("lootTable", False), ("reservesLocked", True), ("reservesComplete", True)):
        assert render.reserves_prompt({**result, "viewer": {**result["viewer"], field: value}}) == ""
    assert render.reserves_prompt({"viewer": None}) == ""
    assert render.reserves_prompt({"viewer": {"response": "accept"}}) == ""


def _interaction():
    return SimpleNamespace(
        id=1,
        user=SimpleNamespace(id=2),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def test_reserves_prompt_is_sent_only_after_accept_or_tentative_not_bench_or_decline():
    result = {
        "viewer": {
            "standing": "ROSTER",
            "response": "accept",
            "lootTable": True,
            "reservesLocked": False,
            "reservesComplete": False,
            "reservesUrl": "https://www.bureauguild.com/members/calendar/r1?reserves=1",
        }
    }
    cog = RaidsCog.__new__(RaidsCog)
    cog.api = SimpleNamespace(respond=AsyncMock(return_value=result), bench=AsyncMock(return_value=result))

    accept = _interaction()
    asyncio.run(cog._respond_deferred(accept, "r1", "ACCEPT"))
    accept.followup.send.assert_awaited_once_with(
        "You're set to **Accept**, on the roster.\nSelect your loot reserves for this raid: https://www.bureauguild.com/members/calendar/r1?reserves=1",
        ephemeral=True,
    )

    tentative = _interaction()
    tentative_result = {**result, "viewer": {**result["viewer"], "response": "tentative"}}
    cog.api.respond.return_value = tentative_result
    asyncio.run(cog._respond_deferred(tentative, "r1", "TENTATIVE"))
    tentative.followup.send.assert_awaited_once_with(
        "You're set to **Tentative**, on the roster.\nSelect your loot reserves for this raid: https://www.bureauguild.com/members/calendar/r1?reserves=1",
        ephemeral=True,
    )

    decline = _interaction()
    cog.api.respond.return_value = {**result, "viewer": {**result["viewer"], "response": "absent"}}
    asyncio.run(cog._respond_deferred(decline, "r1", "ABSENT"))
    decline.followup.send.assert_awaited_once_with("You're set to **Decline**, on the roster.", ephemeral=True)

    bench = _interaction()
    asyncio.run(cog.on_click(bench, "r1", "bench"))
    bench.followup.send.assert_awaited_once_with("You're set to **Accept**, on the roster.", ephemeral=True)


def _reserve_reminder_cog():
    cog = RaidsCog.__new__(RaidsCog)
    cog.api = SimpleNamespace(raid=AsyncMock(return_value=RAID))
    cog.config = SimpleNamespace(site_public_url="https://www.bureauguild.com")
    return cog


def test_reserve_reminder_sends_to_reachable_members_and_is_safe_to_repeat():
    cog = _reserve_reminder_cog()
    first = SimpleNamespace(mention="<@1>")
    second = SimpleNamespace(mention="<@2>")
    thread = SimpleNamespace(guild=object(), send=AsyncMock())
    cog.thread_for = AsyncMock(return_value=thread)
    cog.member_for = AsyncMock(side_effect=[first, second, first, second])
    job = {
        "payload": {
            "raidId": "r1",
            "discordIds": ["1", "2"],
            "reservesLockAt": "2026-11-20T02:00:00.000Z",
        }
    }

    asyncio.run(cog.job_reserves_remind(job))
    asyncio.run(cog.job_reserves_remind(job))

    assert thread.send.await_count == 2
    content = (
        "<@1> <@2>\nYou haven't picked both loot reserves for this raid. "
        "Choose them before <t:1795140000:F>: "
        "https://www.bureauguild.com/members/calendar/r1?reserves=1"
    )
    assert thread.send.await_args_list[0].args == (content,)
    assert thread.send.await_args_list[0].kwargs["allowed_mentions"].users == [first, second]


def test_reserve_reminder_skips_empty_and_unreachable_recipient_lists():
    cog = _reserve_reminder_cog()

    asyncio.run(cog.job_reserves_remind({"payload": {"raidId": "r1", "discordIds": [], "reservesLockAt": "2026-11-20T02:00:00.000Z"}}))

    cog.api.raid.assert_not_awaited()
    thread = SimpleNamespace(guild=object(), send=AsyncMock())
    cog.thread_for = AsyncMock(return_value=thread)
    cog.member_for = AsyncMock(return_value=None)
    asyncio.run(
        cog.job_reserves_remind(
            {"payload": {"raidId": "r1", "discordIds": ["missing"], "reservesLockAt": "2026-11-20T02:00:00.000Z"}}
        )
    )

    thread.send.assert_not_awaited()
