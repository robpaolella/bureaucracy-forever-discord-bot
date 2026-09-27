from types import SimpleNamespace

from sync.members import member_snapshot, roles_changed


def _member(mid, roles, name="Konvett", avatar="https://cdn.discordapp.com/a.png", guild_id=1):
    return SimpleNamespace(
        id=mid,
        name=name.lower(),
        display_name=name,
        display_avatar=SimpleNamespace(url=avatar) if avatar else None,
        roles=[SimpleNamespace(id=r) for r in roles],
        guild=SimpleNamespace(id=guild_id),
        bot=False,
    )


def test_snapshot_carries_id_shown_name_avatar_and_role_ids_without_everyone():
    m = _member(202297378256977920, [1, 10, 20])  # 1 is @everyone (the guild id)
    assert member_snapshot(m) == {"discordId": "202297378256977920", "name": "Konvett", "avatarUrl": "https://cdn.discordapp.com/a.png", "roles": ["10", "20"]}
    assert member_snapshot(_member(5, [1], avatar=None))["avatarUrl"] is None


def test_roles_changed_watches_roles_name_and_avatar_only():
    a = _member(5, [1, 10])
    assert roles_changed(a, _member(5, [1, 10])) is False
    assert roles_changed(a, _member(5, [1, 10, 20])) is True
    assert roles_changed(a, _member(5, [1, 10], name="Other")) is True
    assert roles_changed(a, _member(5, [1, 10], avatar="https://cdn.discordapp.com/b.png")) is True
