from types import SimpleNamespace

import bot


def member_with(*role_names):
    return SimpleNamespace(roles=[SimpleNamespace(name=n) for n in role_names])


def test_a_member_with_a_main_can_keep_it_or_pick_it_again():
    view = bot.MainSelectView(member_with("Warlock Main", "Warlock", "Mage"))
    select, keep = view.children
    # Nothing pre-selected, or picking Warlock again would send Discord nothing.
    assert not any(o.default for o in select.options)
    assert "Warlock" in select.placeholder
    assert isinstance(keep, bot.KeepMainButton)
    assert (keep.label, keep.main) == ("Keep Warlock", "Warlock")


def test_a_member_without_a_main_gets_only_the_class_menu():
    view = bot.MainSelectView(member_with("Mage"))
    assert len(view.children) == 1
    assert view.children[0].placeholder == "Choose your main class..."
