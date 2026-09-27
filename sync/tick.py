"""The bot is the clock (SYNC-SPEC.md §1.4): POST /tick every TICK_SECONDS."""

from __future__ import annotations

import asyncio
import logging

from .api import ApiError, SiteApi

log = logging.getLogger("sync.tick")


class TickLoop:
    def __init__(self, api: SiteApi, *, seconds: float = 60.0):
        self.api = api
        self.seconds = seconds
        self._stop = asyncio.Event()

    async def run(self) -> None:
        log.info("tick every %ss", self.seconds)
        while not self._stop.is_set():
            try:
                counts = await self.api.tick()
                changed = {k: v for k, v in counts.items() if isinstance(v, int) and v}
                if changed:
                    log.info("tick: %s", changed)
            except ApiError as e:
                log.warning("tick: %s", e)
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.seconds)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()
