from types import SimpleNamespace

from sync.members import member_snapshot, roles_after_change, snapshot_changed


def _role(rid, default=False):
    return SimpleNamespace(id=rid, is_default=lambda: default)


def _member(mid, roles, name="Konvett", avatar="https://cdn.discordapp.com/a.png", guild_id=1):
    return SimpleNamespace(
        id=mid,
        name=name.lower(),
        display_name=name,
        display_avatar=SimpleNamespace(url=avatar),
        roles=[_role(r, default=r == guild_id) for r in roles],
        guild=SimpleNamespace(id=guild_id),
        bot=False,
    )


def test_snapshot_carries_id_shown_name_avatar_and_role_ids_without_everyone():
    m = _member(202297378256977920, [1, 10, 20])  # 1 is @everyone (the guild id)
    assert member_snapshot(m) == {"discordId": "202297378256977920", "name": "Konvett", "avatarUrl": "https://cdn.discordapp.com/a.png", "roles": ["10", "20"]}


def test_snapshot_changed_watches_roster_roles_name_and_avatar_only():
    watched = {10, 20}
    a = _member(5, [1, 10, 99])
    assert snapshot_changed(a, _member(5, [1, 10, 99]), watched) is False
    assert snapshot_changed(a, _member(5, [1, 10, 99, 77]), watched) is False  # a class role from the panel
    assert snapshot_changed(a, _member(5, [1, 10, 20]), watched) is True
    assert snapshot_changed(a, _member(5, [1, 99]), watched) is True
    assert snapshot_changed(a, _member(5, [1, 10, 99], name="Other"), watched) is True
    assert snapshot_changed(a, _member(5, [1, 10, 99], avatar="https://cdn.discordapp.com/b.png"), watched) is True


def test_roles_after_change_is_one_list_without_everyone():
    everyone, officer, raider, trial, social = _role(1, default=True), _role(2), _role(3), _role(4), _role(5)
    m = SimpleNamespace(roles=[everyone, officer, raider, social])
    out = roles_after_change(m, add=[trial, raider], remove=[social])
    assert [r.id for r in out] == [2, 3, 4]  # Officer kept, Social gone, Trial added, Raider not doubled
