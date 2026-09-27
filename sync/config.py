"""Environment for the Discord ↔ web sync (SYNC-SPEC.md §10)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.isdigit() else 0


def _seconds(name: str, default: float) -> float:
    """A positive number of seconds, or the default; a typo must not stop the bot starting."""
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass(frozen=True)
class SyncConfig:
    site_api_url: str
    site_public_url: str
    shared_secret: str
    applications_forum_id: int
    raid_signups_channel_id: int
    officers_channel_id: int
    role_guild_member_id: int
    role_guest_id: int
    role_raider_id: int
    poll_seconds: float
    tick_seconds: float

    @classmethod
    def from_env(cls) -> SyncConfig:
        return cls(
            site_api_url=os.environ.get("SITE_API_URL", "").rstrip("/"),
            site_public_url=os.environ.get("SITE_PUBLIC_URL", "https://www.bureauguild.com").rstrip("/"),
            shared_secret=os.environ.get("BOT_SHARED_SECRET", ""),
            applications_forum_id=_int("APPLICATIONS_FORUM_ID"),
            raid_signups_channel_id=_int("RAID_SIGNUPS_CHANNEL_ID"),
            officers_channel_id=_int("OFFICERS_CHANNEL_ID"),
            role_guild_member_id=_int("ROLE_GUILD_MEMBER_ID"),
            role_guest_id=_int("ROLE_GUEST_ID"),
            role_raider_id=_int("ROLE_RAIDER_ID"),
            poll_seconds=_seconds("POLL_SECONDS", 5.0),
            tick_seconds=_seconds("TICK_SECONDS", 60.0),
        )

    @property
    def enabled(self) -> bool:
        """The sync only runs with both halves of the link configured."""
        return bool(self.site_api_url and self.shared_secret)

    def missing(self) -> list[str]:
        """Names of required settings that are empty, for the startup log."""
        out = []
        for name, value in (
            ("SITE_API_URL", self.site_api_url),
            ("BOT_SHARED_SECRET", self.shared_secret),
            ("APPLICATIONS_FORUM_ID", self.applications_forum_id),
            ("OFFICERS_CHANNEL_ID", self.officers_channel_id),
            ("ROLE_GUILD_MEMBER_ID", self.role_guild_member_id),
            ("ROLE_GUEST_ID", self.role_guest_id),
            ("ROLE_RAIDER_ID", self.role_raider_id),
        ):
            if not value:
                out.append(name)
        return out
