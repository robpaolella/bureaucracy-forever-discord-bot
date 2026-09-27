import os

from sync.api import ApiError, SiteApi
from sync.config import SyncConfig


def test_bearer_and_idempotency_headers():
    api = SiteApi("https://site.example/", "s3cret")
    assert api.base == "https://site.example/api/bot"
    assert api._headers["Authorization"] == "Bearer s3cret"
    assert api.headers_for("123") == {"Content-Type": "application/json", "Idempotency-Key": "123"}
    assert "Idempotency-Key" not in api.headers_for(None)


def test_api_error_reason_prefers_reason_then_error():
    assert ApiError(409, {"reason": "Sign-ups are locked."}).reason == "Sign-ups are locked."
    assert ApiError(400, {"error": "bad"}).reason == "bad"
    assert ApiError(500, "html").reason == "Something went wrong on the site."


def test_config_enabled_and_missing(monkeypatch):
    for k in list(os.environ):
        if k.startswith(("SITE_", "BOT_", "APPLICATIONS_", "RAID_", "OFFICERS_", "ROLE_", "POLL_", "TICK_")):
            monkeypatch.delenv(k, raising=False)
    off = SyncConfig.from_env()
    assert not off.enabled
    monkeypatch.setenv("SITE_API_URL", "https://site.example/")
    monkeypatch.setenv("BOT_SHARED_SECRET", "x")
    monkeypatch.setenv("APPLICATIONS_FORUM_ID", "1553511194441023518")
    on = SyncConfig.from_env()
    assert on.enabled and on.site_api_url == "https://site.example" and on.applications_forum_id == 1553511194441023518
    assert "OFFICERS_CHANNEL_ID" in on.missing() and "APPLICATIONS_FORUM_ID" not in on.missing()
    assert on.poll_seconds == 5 and on.tick_seconds == 60
