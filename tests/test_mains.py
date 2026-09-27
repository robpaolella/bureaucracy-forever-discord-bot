from sync.mains import class_entry, name_ok, role_label, summary

CLASSES = {
    "roles": {"tank": "Tank", "healer": "Healer", "melee": "Melee DPS", "ranged": "Ranged DPS"},
    "classes": [
        {"key": "druid", "label": "Druid", "specs": [{"name": "Restoration", "roles": ["healer"]}, {"name": "Feral", "roles": ["tank", "melee"]}, {"name": "Balance", "roles": ["ranged"]}]},
        {"key": "mage", "label": "Mage", "specs": [{"name": "Frost", "roles": ["ranged"]}]},
    ],
}


def test_names_are_two_parts_of_two_to_twelve_letters_second_optional():
    assert name_ok("Red", "Tape")
    assert name_ok(" Red ", "")
    assert name_ok("Red", "")
    assert not name_ok("R", "Tape")
    assert not name_ok("Red", "Tapeisfartoolong")
    assert not name_ok("Red1", "Tape")
    assert name_ok("Ångström", "Ödegaard")


def test_class_lookup_by_label_or_key_and_role_labels():
    assert class_entry(CLASSES, "Druid")["key"] == "druid"
    assert class_entry(CLASSES, "mage")["label"] == "Mage"
    assert class_entry(CLASSES, "Monk") is None
    assert role_label(CLASSES, "melee") == "Melee DPS"
    assert role_label({}, "tank") == "Tank"


def test_a_class_without_specs_still_resolves():
    table = {"classes": [{"key": "monk", "label": "Monk", "specs": []}]}
    assert class_entry(table, "Monk")["specs"] == []


def test_summary_reads_like_the_roster():
    assert summary(CLASSES, {"name": "Red Tape", "class": "druid", "spec": "Feral", "raidRole": "tank"}) == "**Red Tape** — Feral Druid (Tank)"


# --- editing a main that is already set ------------------------------------------------------

import asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from sync.mains import NameModal, RoleSelect, SpecSelect, continue_main_flow, current_main_for, split_name  # noqa: E402

DRUID = CLASSES["classes"][0]
FERAL_TANK = {"name": "Red Tape", "class": "druid", "spec": "Feral", "raidRole": "tank"}


def test_names_split_back_into_their_two_parts():
    assert split_name("Red Tape") == ("Red", "Tape")
    assert split_name("Redtape") == ("Redtape", "")
    assert split_name(" Red  Tape ") == ("Red", "Tape")


def test_only_a_main_of_the_same_class_carries_over():
    assert current_main_for({"main": FERAL_TANK}, DRUID) == FERAL_TANK
    assert current_main_for({"main": FERAL_TANK}, CLASSES["classes"][1]) is None
    assert current_main_for({"main": None}, DRUID) is None
    assert current_main_for(None, DRUID) is None


def test_the_current_spec_is_marked_not_preselected():
    options = {o.value: o for o in SpecSelect(None, CLASSES, DRUID, FERAL_TANK).options}
    assert options["Feral"].description == "Tank or Melee DPS · your current pick"
    assert options["Balance"].description == "Ranged DPS"
    assert not any(o.default for o in options.values())


def test_the_current_raid_role_is_marked_only_on_the_current_spec():
    feral = DRUID["specs"][1]
    marked = {o.value: o.description for o in RoleSelect(None, CLASSES, DRUID, feral, ["tank", "melee"], FERAL_TANK).options}
    assert marked == {"tank": "Your current pick", "melee": None}
    balance_before = {**FERAL_TANK, "spec": "Balance", "raidRole": "ranged"}
    unmarked = {o.value: o.description for o in RoleSelect(None, CLASSES, DRUID, feral, ["tank", "melee"], balance_before).options}
    assert unmarked == {"tank": None, "melee": None}


def test_the_name_modal_opens_with_the_current_name():
    feral = DRUID["specs"][1]
    modal = NameModal(None, CLASSES, DRUID, feral, "tank", FERAL_TANK)
    assert (modal.first.default, modal.second.default) == ("Red", "Tape")
    blank = NameModal(None, CLASSES, DRUID, feral, "tank")
    assert (blank.first.default, blank.second.default) == (None, None)


class FakeApi:
    def __init__(self, member=None, member_error=None):
        self._member = member
        self._member_error = member_error

    async def classes(self):
        return CLASSES

    async def member(self, discord_id):
        if self._member_error:
            raise self._member_error
        return self._member


class FakeInteraction:
    def __init__(self):
        self.user = SimpleNamespace(id=42)
        self.edits = []

    async def edit_original_response(self, **kwargs):
        self.edits.append(kwargs)

    async def original_response(self):
        return None


def run_flow(api):
    interaction = FakeInteraction()
    ok = asyncio.run(continue_main_flow(interaction, "Druid", api, "Main set."))
    return ok, interaction.edits[-1]


def test_the_flow_names_the_current_main_and_passes_it_on():
    ok, edit = run_flow(FakeApi(member={"main": FERAL_TANK}))
    assert ok
    assert "It has **Red Tape** — Feral Druid (Tank)" in edit["content"]
    assert edit["view"].children[0].current == FERAL_TANK


def test_the_flow_still_runs_when_the_member_lookup_fails():
    ok, edit = run_flow(FakeApi(member_error=RuntimeError("down")))
    assert ok
    assert "which **Druid** spec is your main?" in edit["content"]
    assert edit["view"].children[0].current is None
