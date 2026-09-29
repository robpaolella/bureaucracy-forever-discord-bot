import asyncio
from types import SimpleNamespace

import bot


class Role:
    def __init__(self, rid, name, default=False):
        self.id, self.name, self._default = rid, name, default
        self.mention = f"<@&{rid}>"

    def is_default(self):
        return self._default


class Member:
    def __init__(self, roles):
        self.id = 7
        self.mention = "<@7>"
        self.roles = roles
        self.edits = []

    async def edit(self, *, roles, reason=None):
        self.edits.append([r.name for r in roles])
        self.roles = roles


class Response:
    def __init__(self):
        self.edited = []
        self.sent = []

    async def edit_message(self, **kwargs):
        self.edited.append(kwargs)

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))


EVERYONE = Role(1, "@everyone", default=True)
MEMBER, GUEST = Role(2, "Guild Member"), Role(3, "Guest")
RAIDER, TRIAL, SOCIAL = Role(4, "Raider"), Role(5, "Trial"), Role(6, "Social")
ROLES = [EVERYONE, MEMBER, GUEST, RAIDER, TRIAL, SOCIAL]


def _interaction(member, monkeypatch):
    monkeypatch.setattr(bot, "RANK_ROLE_IDS", {"raider": 4, "trial": 5, "social": 6})
    by_id = {r.id: r for r in ROLES}
    guild = SimpleNamespace(roles=ROLES, get_member=lambda uid: member, get_role=by_id.get)
    return SimpleNamespace(guild=guild, user=SimpleNamespace(mention="<@99>", __str__=lambda: "Officer"), response=Response())


def test_each_choice_grants_its_rank_roles_and_takes_the_others_off():
    assert bot.triage_rank_roles("social") == (["social"], ["raider", "trial"])
    assert bot.triage_rank_roles("raider") == (["raider"], ["trial", "social"])
    assert bot.triage_rank_roles("trial") == (["raider", "trial"], ["social"])


def test_guild_member_asks_social_raider_or_trial_before_touching_roles(monkeypatch):
    member = Member([EVERYONE, GUEST])
    i = _interaction(member, monkeypatch)
    asyncio.run(bot.TriageButton("member", 7).callback(i))
    assert member.edits == []
    reply = i.response.edited[0]
    assert "Social, a Raider or a Raider Trial" in reply["content"]
    assert [c.item.label for c in reply["view"].children] == ["Social", "Raider", "Raider Trial", "Back"]


def test_raider_trial_gets_member_raider_and_trial_and_loses_guest_in_one_edit(monkeypatch):
    member = Member([EVERYONE, GUEST])
    i = _interaction(member, monkeypatch)
    asyncio.run(bot.TriageButton("trial", 7).callback(i))
    assert member.edits == [["Guild Member", "Raider", "Trial"]]
    assert i.response.edited[0]["content"] == "New user <@7> was set to <@&2>, <@&4> and <@&5> by <@99>."


def test_social_and_raider_choices(monkeypatch):
    member = Member([EVERYONE, GUEST, TRIAL])
    asyncio.run(bot.TriageButton("social", 7).callback(_interaction(member, monkeypatch)))
    assert member.edits[-1] == ["Guild Member", "Social"]
    asyncio.run(bot.TriageButton("raider", 7).callback(_interaction(member, monkeypatch)))
    assert member.edits[-1] == ["Guild Member", "Raider"]


def test_an_unconfigured_rank_role_is_reported_and_nothing_changes(monkeypatch):
    member = Member([EVERYONE, GUEST])
    i = _interaction(member, monkeypatch)
    monkeypatch.setattr(bot, "RANK_ROLE_IDS", {"raider": 4, "trial": 0, "social": 6})
    asyncio.run(bot.TriageButton("raider", 7).callback(i))
    assert member.edits == []
    assert "ROLE_TRIAL_ID" in i.response.sent[0][0][0]
