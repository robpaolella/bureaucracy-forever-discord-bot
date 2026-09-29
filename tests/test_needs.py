import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sync import needs
from sync.api import ApiError


def _needs():
    return {
        "statuses": ["high", "medium", "closed"],
        "classes": [
            {"key": "warrior", "label": "Warrior", "specs": [{"name": "Arms", "status": "medium"}, {"name": "Fury", "status": "high"}, {"name": "Protection", "status": "high"}]},
            {"key": "mage", "label": "Mage", "specs": [{"name": "Arcane", "status": "closed"}, {"name": "Fire", "status": "closed"}]},
        ],
    }


def test_class_options_summarise_what_is_open():
    data = _needs()
    assert needs.class_summary(data["classes"][0]) == "High: Fury, Protection · Medium: Arms"
    assert needs.class_summary(data["classes"][1]) == "All closed"


def test_the_three_menus_class_spec_then_priority():
    data = _needs()
    classes = needs.ClassSelect(None, data)
    assert [o.label for o in classes.options] == ["Warrior", "Mage"]
    specs = needs.SpecSelect(None, data, data["classes"][0])
    assert [(o.label, o.description) for o in specs.options] == [("Arms", "Now: Medium"), ("Fury", "Now: High"), ("Protection", "Now: High")]
    status = needs.StatusSelect(None, data, data["classes"][0], data["classes"][0]["specs"][2])
    assert [(o.label, o.value, o.description) for o in status.options] == [("High", "high", "Current"), ("Medium", "medium", None), ("Closed", "closed", None)]


def _pick(select, value, api):
    select._values = [value]  # what Discord would have sent
    select.api = api
    i = SimpleNamespace(
        id=900,
        user=SimpleNamespace(id=77),
        response=SimpleNamespace(defer=AsyncMock()),
        edit_original_response=AsyncMock(),
        original_response=AsyncMock(return_value=None),
    )
    asyncio.run(select.callback(i))
    return i


def test_saving_writes_through_the_site_and_offers_another_class():
    data = _needs()
    api = SimpleNamespace(set_need=AsyncMock(return_value={}))
    select = needs.StatusSelect(api, data, data["classes"][0], data["classes"][0]["specs"][2])
    i = _pick(select, "closed", api)
    api.set_need.assert_awaited_once_with("warrior", "Protection", "closed", 77, key="900")
    kwargs = i.edit_original_response.await_args.kwargs
    assert kwargs["content"].startswith("**Warrior Protection** is now **Closed**.")
    assert isinstance(kwargs["view"].children[0], needs.ClassSelect)
    assert data["classes"][0]["specs"][2]["status"] == "closed"  # the next menu shows it


def test_a_refusal_is_shown_and_nothing_is_marked_changed():
    data = _needs()
    api = SimpleNamespace(set_need=AsyncMock(side_effect=ApiError(403, {"error": "Only officers set recruitment needs."})))
    select = needs.StatusSelect(api, data, data["classes"][0], data["classes"][0]["specs"][2])
    i = _pick(select, "closed", api)
    assert i.edit_original_response.await_args.kwargs == {"content": "Only officers set recruitment needs.", "view": None}
    assert data["classes"][0]["specs"][2]["status"] == "high"


def _command(config, user_roles, channel_id):
    cog = needs.NeedsCog.__new__(needs.NeedsCog)
    cog.config = config
    cog.api = SimpleNamespace(needs=AsyncMock(return_value=_needs()))
    sent = []

    async def send_message(text, ephemeral):
        sent.append(text)

    i = SimpleNamespace(channel_id=channel_id, user=SimpleNamespace(roles=[SimpleNamespace(id=r) for r in user_roles]), response=SimpleNamespace(send_message=send_message))
    asyncio.run(needs.NeedsCog.recruitment.callback(cog, i))
    return sent


def test_the_command_only_runs_in_the_recruitment_channel_and_for_officers():
    config = SimpleNamespace(recruitment_needs_channel_id=10, role_officer_id=5)
    assert _command(config, [5], 11) == ["Run this in <#10>."]
    assert _command(config, [4], 10) == ["Only officers set recruitment needs."]
