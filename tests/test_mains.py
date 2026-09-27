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


def test_summary_reads_like_the_roster():
    assert summary(CLASSES, {"name": "Red Tape", "class": "druid", "spec": "Feral", "raidRole": "tank"}) == "**Red Tape** — Feral Druid (Tank)"
