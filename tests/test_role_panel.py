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


class FakeMember:
    def __init__(self, roles):
        self.roles = roles
        self.changes = []

    async def add_roles(self, *roles, reason=None):
        self.changes.append(("add", [r.name for r in roles]))

    async def remove_roles(self, *roles, reason=None):
        self.changes.append(("remove", [r.name for r in roles]))


class FakeInteraction:
    def __init__(self, member, guild_roles):
        self.user = member
        self.guild = SimpleNamespace(roles=guild_roles)
        self.client = SimpleNamespace(get_cog=lambda name: None)  # no site sync: roles only
        self.edits = []

        async def defer():
            pass

        self.response = SimpleNamespace(defer=defer, is_done=lambda: True)

    async def edit_original_response(self, **kwargs):
        self.edits.append(kwargs)


def test_keeping_the_main_touches_no_roles_and_says_so():
    import asyncio

    guild_roles = [SimpleNamespace(name=n) for n in bot.CLASSES + bot.main_role_names()]
    held = [r for r in guild_roles if r.name in ("Warlock", "Warlock Main")]
    member = FakeMember(held)
    interaction = FakeInteraction(member, guild_roles)
    asyncio.run(bot.KeepMainButton("Warlock").callback(interaction))
    assert member.changes == []
    assert "Your main stays **Warlock**" in interaction.edits[-1]["content"]
