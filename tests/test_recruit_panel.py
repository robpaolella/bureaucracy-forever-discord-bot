import os

os.environ.setdefault("SITE_PUBLIC_URL", "https://www.bureauguild.com")

import recruit  # noqa: E402


def test_apply_view_is_two_link_buttons_to_the_site():
    view = recruit.ApplyView()
    buttons = [item for item in view.children if hasattr(item, "url")]
    assert [b.url for b in buttons] == ["https://www.bureauguild.com/apply?path=raider", "https://www.bureauguild.com/apply?path=social"]
    assert all(b.custom_id is None for b in buttons)
    assert "join the server first" in recruit.APPLY_MESSAGE
