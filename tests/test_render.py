from sync import render

APP = {
    "id": "cabc123",
    "character": "Footnote",
    "path": "raider",
    "status": "pending",
    "wowClass": "priest",
    "spec": "Holy",
    "raidRole": "healer",
    "discordName": "footnote",
    "logsUrl": "https://logs.example.com/r/1",
    "url": "https://www.bureauguild.com/officers/applications/cabc123",
    "answers": [{"key": "availability", "label": "Can you make the nights?", "answer": "Both nights"}, {"key": "wipe", "label": "A wipe", "answer": "x" * 80}],
}


def test_titles_and_tags():
    assert render.title_for(APP) == "Footnote — Priest (Holy) — Raider"
    assert render.title_for({**APP, "path": "social", "wowClass": None, "spec": None}) == "Footnote — Social"
    assert render.embed_title(APP) == "Footnote · Priest · Holy · Healer"
    assert render.tags_for(APP) == ["Raider", "Pending"]
    assert render.tags_for({**APP, "status": "accepted"}) == ["Raider", "Accepted"]


def test_embed_fields_and_footer():
    e = render.application_embed(APP, "https://www.bureauguild.com")
    names = [f.name for f in e.fields]
    assert names[:4] == ["Discord", "Path", "Status", "Logs"]
    assert "Can you make the nights?" in names and "A wipe" in names
    long_field = next(f for f in e.fields if f.name == "A wipe")
    assert long_field.inline is False
    assert e.footer.text == "Submitted on the web"
    assert e.colour == render.COLOUR_PENDING
    assert render.application_embed({**APP, "status": "declined", "decidedBy": "Ledgerline"}, "x").colour == render.COLOUR_DECLINED


def test_decision_embed_and_note_line():
    e = render.decision_embed({"status": "accepted", "character": "Footnote", "decidedBy": "Ledgerline", "source": "discord"})
    assert e.title == "Accepted · Footnote"
    assert "via Discord" in (e.description or "")
    assert render.note_line("Robert", "Logs look fine.") == "**Robert** (web) · Logs look fine."


def test_application_id_from_url():
    assert render.application_id_from_url("https://www.bureauguild.com/officers/applications/cabc123") == "cabc123"
    assert render.application_id_from_url("https://www.bureauguild.com/members/roster") is None
    assert render.application_id_from_url(None) is None
