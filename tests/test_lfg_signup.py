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


# --- the cog's flows, with a real store and a fake interaction -------------------------

class _Response:
    def __init__(self):
        self.sent = []

    async def send_message(self, content, *, view=None, ephemeral=False):
        self.sent.append(("send", content, view))

    async def edit_message(self, *, content, view=None):
        self.sent.append(("edit", content, view))


def _interaction(uid):
    from types import SimpleNamespace

    return SimpleNamespace(user=SimpleNamespace(id=uid), guild=None, response=_Response())


def _cog(tmp_path):
    from lfg import Lfg

    cog = Lfg.__new__(Lfg)  # skip __init__: no bot, no expiry loop
    cog.store = _store(tmp_path)
    cog.thread_notes = []

    async def refresh(guild, mid):
        return None

    async def say(mid, text):
        cog.thread_notes.append((mid, text))

    cog.refresh = refresh
    cog.say_in_thread = say
    return cog


def test_role_button_while_in_the_group_offers_change_then_changes_in_place(tmp_path):
    cog = _cog(tmp_path)
    cog.store.add_signup(1, 9, "healer")
    i = _interaction(9)
    asyncio.run(cog.handle_button(i, "dps", 1))
    kind, text, view = i.response.sent[0]
    assert "in this group as **Healer**" in text and isinstance(view, InGroupView)
    asyncio.run(cog.change_role(_interaction(9), 1, "dps"))
    assert [(r["user_id"], r["role"]) for r in cog.store.signups(1)] == [(9, "dps")]
    assert cog.thread_notes == [(1, "<@9> switched from **Healer** to **DPS**.")]


def test_late_change_role_click_after_the_slot_filled_or_the_group_closed(tmp_path):
    cog = _cog(tmp_path)
    cog.store.add_signup(1, 9, "healer")
    cog.store.add_signup(1, 5, "tank")
    i = _interaction(9)
    asyncio.run(cog.change_role(i, 1, "tank"))
    assert "filled up in the meantime" in i.response.sent[0][1]
    cog.store.set_status(1, "cancelled")
    i = _interaction(9)
    asyncio.run(cog.change_role(i, 1, "dps"))
    assert i.response.sent[0][1] == "This group is closed."
    assert [(r["user_id"], r["role"]) for r in cog.store.signups(1)] == [(9, "healer"), (5, "tank")]


def test_signing_up_while_in_another_running_group_asks_first(tmp_path):
    cog = _cog(tmp_path)
    cog.store.add_signup(2, 9, "tank")  # Stockades, filled, and 9 leads it
    i = _interaction(9)
    asyncio.run(cog.handle_button(i, "healer", 1))
    kind, text, view = i.response.sent[0]
    assert isinstance(view, SwitchGroupView)
    assert "**Stockades** (Tank, you lead it)" in text and "takes you out of that group" in text
    assert cog.store.signups(1) == []  # nothing moved yet


def test_confirming_the_switch_moves_the_member_and_tells_both_threads(tmp_path):
    cog = _cog(tmp_path)
    cog.store.add_signup(2, 9, "tank")
    i = _interaction(9)
    asyncio.run(cog.join(i, 1, "healer", edit=True, leave_others=True))
    assert i.response.sent[0][1] == "You're in as **Healer**. You've left **Stockades** (Tank, you lead it)."
    assert [r["user_id"] for r in cog.store.signups(1)] == [9]
    assert cog.store.signups(2) == []
    assert cog.thread_notes == [(1, "<@9> has signed up as **Healer** for this group."), (2, "<@9> left this group to join a **Deadmines** group.")]


def test_late_switch_confirm_after_the_group_closed_or_the_slot_filled(tmp_path):
    cog = _cog(tmp_path)
    cog.store.add_signup(2, 9, "tank")
    cog.store.add_signup(1, 5, "healer")
    i = _interaction(9)
    asyncio.run(cog.join(i, 1, "healer", edit=True, leave_others=True))
    assert i.response.sent[0][1] == "The Healer slots just filled up."
    cog.store.set_status(1, "expired")
    i = _interaction(9)
    asyncio.run(cog.join(i, 1, "dps", edit=True, leave_others=True))
    assert i.response.sent[0][1] == "This group is closed."
    assert [r["user_id"] for r in cog.store.signups(2)] == [9]  # still in the other group


def test_a_closed_other_group_does_not_trigger_the_question(tmp_path):
    cog = _cog(tmp_path)
    cog.store.add_signup(3, 9, "dps")  # Wailing Caverns, cancelled
    i = _interaction(9)
    asyncio.run(cog.handle_button(i, "dps", 1))
    assert i.response.sent[0][1] == "You're in as **DPS**."
    assert [r["user_id"] for r in cog.store.signups(3)] == [9]
