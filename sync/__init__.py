"""Discord ↔ web sync (SYNC-SPEC.md). Loaded as an extension by bot.py.

The site is the only source of truth; this package polls its outbox, runs the jobs it finds,
calls its API on clicks and thread messages, and ticks its clock. Raid jobs (§5 raid.*) ship
with Phase 5 and are deferred until then.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from .api import SiteApi
from .applications import ApplicationsCog, DecisionButton
from .config import SyncConfig
from .members import MembersCog
from .runner import JobRunner
from .tick import TickLoop

log = logging.getLogger("sync")

RAID_JOB_TYPES = ("raid.post", "raid.update", "raid.remind", "raid.lock", "raid.cancel", "raid.close")


class SyncCog(commands.Cog):
    """Owns the API session and the two loops; starts them once the gateway is ready."""

    def __init__(self, bot: commands.Bot, config: SyncConfig):
        self.bot = bot
        self.config = config
        self.api = SiteApi(config.site_api_url, config.shared_secret)
        self.runner = JobRunner(self.api, poll_seconds=config.poll_seconds)
        self.runner.defer(*RAID_JOB_TYPES)
        self.ticker = TickLoop(self.api, seconds=config.tick_seconds)
        self._tasks: list[asyncio.Task[None]] = []

    async def cog_load(self) -> None:
        await self.api.start()
        self.bot.add_dynamic_items(DecisionButton)

    async def cog_unload(self) -> None:
        self.ticker.stop()
        await self.runner.shutdown()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.api.close()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._tasks:
            return
        try:
            health = await self.api.health()
            log.info("site reachable, version %s", health.get("version"))
        except Exception as e:  # noqa: BLE001 — start anyway; the loops report each failure
            log.warning("site not reachable yet: %s", e)
        apps: ApplicationsCog | None = self.bot.get_cog("ApplicationsCog")  # type: ignore[assignment]
        if apps is not None:
            try:
                await apps.ensure_tags()
            except Exception as e:  # noqa: BLE001
                log.warning("could not prepare #applications tags: %s", e)
        self._tasks = [asyncio.create_task(self.runner.run(), name="sync:outbox"), asyncio.create_task(self.ticker.run(), name="sync:tick")]
        log.info("sync running")


async def setup(bot: commands.Bot) -> None:
    config = SyncConfig.from_env()
    if not config.enabled:
        log.warning("sync disabled: set SITE_API_URL and BOT_SHARED_SECRET (missing: %s)", ", ".join(config.missing()))
        return
    missing = config.missing()
    if missing:
        log.warning("sync starting with missing settings: %s", ", ".join(missing))
    core = SyncCog(bot, config)
    await bot.add_cog(core)
    await bot.add_cog(ApplicationsCog(bot, core.api, config, core.runner))
    await bot.add_cog(MembersCog(bot, config, core.runner))
    intents: discord.Intents = bot.intents
    if not intents.message_content:
        log.warning("Message Content intent is off: thread messages will not become notes")
