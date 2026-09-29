import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from sync import trials
from sync.api import ApiError

P = {"discordId": "123", "name": "Konvett", "startedAt": "2026-09-14T19:00:00.000Z", "extended": False}


def test_first_check_in_pings_officers_and_says_two_weeks():
    text = trials.checkin_text(P, 555)
    assert text.startswith("<@&555> **Konvett** (<@123>)'s trial has hit two weeks, on trial since <t:")
    assert text.endswith(":D>. Promote them to full Raider, or extend the trial?")


def test_after_an_extension_it_says_the_extension_is_up():
    text = trials.checkin_text({**P, "extended": True, "startedAt": None}, 0)
    assert text == "**Konvett** (<@123>)'s trial extension is up. Promote them to full Raider, or extend the trial again?"


def test_the_view_has_promote_and_a_one_to_seven_day_menu():
    promote, extend = trials.checkin_view("123").children
    assert promote.custom_id == "sync:trial:promote:123"
    assert extend.custom_id == "sync:trial:extend:123"
    assert [o.value for o in extend.item.options] == [str(d) for d in range(1, 8)]


def _interaction(api):
    cog = SimpleNamespace(api=api)
    i = SimpleNamespace(
        id=900,
        user=SimpleNamespace(id=77, mention="<@77>"),
        client=SimpleNamespace(get_cog=lambda name: cog),
        response=SimpleNamespace(defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )
    return i


def test_extend_calls_the_site_and_turns_the_message_into_the_outcome():
    api = SimpleNamespace(trial=AsyncMock(return_value={"action": "extend", "checkInAt": "2026-10-01T00:00:00Z"}))
    i = _interaction(api)
    asyncio.run(trials.answer(i, "123", "extend", 3))
    api.trial.assert_awaited_once_with(123, "extend", 77, days=3, key="900")
    kwargs = i.edit_original_response.await_args.kwargs
    assert kwargs["view"] is None
    assert kwargs["content"].startswith("⏳ <@123>'s trial was extended by 3 days by <@77>. I'll ask again <t:")


def test_promote_outcome():
    api = SimpleNamespace(trial=AsyncMock(return_value={"action": "promote", "rank": "raider"}))
    i = _interaction(api)
    asyncio.run(trials.answer(i, "123", "promote", None))
    api.trial.assert_awaited_once_with(123, "promote", 77, days=None, key="900")
    assert "promoted to full Raider by <@77>" in i.edit_original_response.await_args.kwargs["content"]


def test_a_refusal_from_the_site_is_shown_only_to_the_clicker_and_the_buttons_stay():
    api = SimpleNamespace(trial=AsyncMock(side_effect=ApiError(403, {"error": "Only officers answer trial check-ins."})))
    i = _interaction(api)
    asyncio.run(trials.answer(i, "123", "promote", None))
    i.followup.send.assert_awaited_once_with("Only officers answer trial check-ins.", ephemeral=True)
    i.edit_original_response.assert_not_awaited()


def test_the_job_posts_in_officers_with_only_the_officer_role_pingable():
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    cog = trials.TrialsCog.__new__(trials.TrialsCog)
    cog.bot = SimpleNamespace(get_channel=lambda cid: channel)
    cog.config = SimpleNamespace(officers_channel_id=1, role_officer_id=555)
    asyncio.run(cog.job_checkin({"payload": P}))
    args, kwargs = channel.send.await_args
    assert args[0].startswith("<@&555> ")
    mentions = kwargs["allowed_mentions"]
    assert [r.id for r in mentions.roles] == [555] and mentions.users is False and mentions.everyone is False


def test_a_conflict_closes_the_check_in_but_a_refusal_leaves_it_open():
    api = SimpleNamespace(trial=AsyncMock(side_effect=ApiError(409, {"reason": "Konvett is not a trial any more (rank: raider)."})))
    i = _interaction(api)
    asyncio.run(trials.answer(i, "123", "extend", 2))
    kwargs = i.edit_original_response.await_args.kwargs
    assert kwargs["view"] is None and kwargs["content"].startswith("Konvett is not a trial any more")


def test_custom_ids_route_to_the_right_item_and_member():
    assert trials.TrialPromoteButton.__discord_ui_compiled_template__.fullmatch("sync:trial:promote:123")
    assert not trials.TrialPromoteButton.__discord_ui_compiled_template__.fullmatch("sync:trial:extend:123")
    assert not trials.TrialExtendSelect.__discord_ui_compiled_template__.fullmatch("sync:trial:extend:abc")
    match = trials.TrialExtendSelect.__discord_ui_compiled_template__.fullmatch("sync:trial:extend:123")
    item = asyncio.run(trials.TrialExtendSelect.from_custom_id(None, None, match))
    assert item.uid == "123"


def test_the_menu_sends_the_picked_days_as_a_number(monkeypatch):
    seen = []

    async def fake_answer(interaction, uid, action, days):
        seen.append((uid, action, days))

    monkeypatch.setattr(trials, "answer", fake_answer)
    select = trials.TrialExtendSelect("123")
    select.item._values = ["1"]
    asyncio.run(select.callback(None))
    assert seen == [("123", "extend", 1)]
    assert trials.extended_text("123", 1, None, SimpleNamespace(mention="<@77>")) == "\u23f3 <@123>'s trial was extended by 1 day by <@77>."
