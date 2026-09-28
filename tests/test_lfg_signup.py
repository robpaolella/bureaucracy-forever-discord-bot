import asyncio
import time

from lfg import InGroupView, Store, SwitchGroupView, other_groups_phrase, signup_plan


def _rows(*pairs):
    return [{"user_id": uid, "role": role} for uid, role in pairs]


def test_signup_plan_for_someone_not_in_the_group():
    rows = _rows((1, "tank"), (2, "dps"))
    assert signup_plan(rows, 9, "healer") == ("join", None)
    assert signup_plan(rows, 9, "tank") == ("full", None)


def test_signup_plan_for_someone_already_in_the_group():
    rows = _rows((1, "tank"), (9, "healer"), (2, "dps"))
    assert signup_plan(rows, 9, "healer") == ("same", "healer")
    assert signup_plan(rows, 9, "dps") == ("change", "healer")
    assert signup_plan(rows, 9, "tank") == ("full", "healer")


def test_signup_plan_counts_the_movers_own_slot_as_free():
    # The only DPS moving within DPS is "same"; the healer can move to DPS while two DPS slots are open.
    rows = _rows((9, "healer"), (2, "dps"), (3, "dps"))
    assert signup_plan(rows, 9, "dps") == ("change", "healer")
    rows = _rows((9, "healer"), (2, "dps"), (3, "dps"), (4, "dps"))
    assert signup_plan(rows, 9, "dps") == ("full", "healer")


def _store(tmp_path):
    store = Store(str(tmp_path / "lfg.db"))
    now = int(time.time())
    for mid, dungeon, status, owner in ((1, "Deadmines", "open", 50), (2, "Stockades", "filled", 9), (3, "Wailing Caverns", "cancelled", 50), (4, "Razorfen Kraul", "open", 50)):
        store.create_post(message_id=mid, guild_id=1, channel_id=1, thread_id=None, owner_id=owner, dungeon=dungeon, notes=None, created_at=now + mid)
        if status != "open":
            store.set_status(mid, status)
    return store


def test_active_signups_are_other_open_or_filled_groups_only(tmp_path):
    store = _store(tmp_path)
    store.add_signup(1, 9, "healer")
    store.add_signup(2, 9, "tank")
    store.add_signup(3, 9, "dps")
    store.add_signup(4, 9, "dps")
    got = [(r["dungeon"], r["role"]) for r in store.active_signups(9, exclude_mid=4)]
    assert got == [("Deadmines", "healer"), ("Stockades", "tank")]
    assert store.active_signups(77, exclude_mid=4) == []


def test_set_role_keeps_roster_order(tmp_path):
    store = _store(tmp_path)
    for uid, role in ((5, "tank"), (9, "healer"), (6, "dps")):
        store.add_signup(1, uid, role)
    store.set_role(1, 9, "dps")
    assert [(r["user_id"], r["role"]) for r in store.signups(1)] == [(5, "tank"), (9, "dps"), (6, "dps")]


def test_other_groups_phrase_names_each_group_and_leadership():
    one = [{"dungeon": "Deadmines", "role": "healer", "leads": False}]
    two = one + [{"dungeon": "Stockades", "role": "tank", "leads": True}]
    assert other_groups_phrase(one) == "**Deadmines** (Healer)"
    assert other_groups_phrase(two) == "**Deadmines** (Healer) and **Stockades** (Tank, you lead it)"


def test_private_views_offer_change_and_leave_or_switch_and_stay():
    async def labels():
        change = [c.label for c in InGroupView(None, 1, "tank").children]  # type: ignore[arg-type]
        stay = [c.label for c in InGroupView(None, 1, None).children]  # type: ignore[arg-type]
        switch = [c.label for c in SwitchGroupView(None, 1, "dps").children]  # type: ignore[arg-type]
        return change, stay, switch

    change, stay, switch = asyncio.run(labels())
    assert change == ["Change role to Tank", "Leave group"]
    assert stay == ["Leave group"]
    assert switch == ["Switch to this group", "Stay where I am"]
