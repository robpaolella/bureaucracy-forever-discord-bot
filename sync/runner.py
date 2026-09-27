"""The outbox job runner (SYNC-SPEC.md §1.7, §5).

Polls `GET /outbox`, runs each job through its handler, and acks. Jobs are processed in
order per entity: the runner keeps one queue per entity key and never runs two jobs for
the same entity at once, with up to `concurrency` entities in flight. A handler that raises
fails the job (`ok: false`); the site owns the backoff and the FAILED threshold.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .api import ApiError, SiteApi

log = logging.getLogger("sync.runner")

Job = dict[str, Any]
Handler = Callable[[Job], Awaitable[dict[str, Any] | None]]


class JobRunner:
    def __init__(self, api: SiteApi, *, poll_seconds: float = 5.0, concurrency: int = 4, batch: int = 20):
        self.api = api
        self.poll_seconds = poll_seconds
        self.batch = batch
        self.handlers: dict[str, Handler] = {}
        # Job types no handler exists for yet. They are left RUNNING (never acked) so the
        # site hands them out again later rather than failing them for good.
        self.deferred: set[str] = set()
        self._queues: dict[str, asyncio.Queue[Job]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._seen: set[str] = set()
        self._entity_slots = asyncio.Semaphore(concurrency)
        self._stop = asyncio.Event()
        self._deferred_logged: set[str] = set()

    def handle(self, job_type: str, handler: Handler) -> None:
        self.handlers[job_type] = handler

    def defer(self, *job_types: str) -> None:
        self.deferred.update(job_types)

    # --- polling -------------------------------------------------------------------

    async def run(self) -> None:
        log.info("outbox runner polling every %ss", self.poll_seconds)
        while not self._stop.is_set():
            try:
                jobs = await self.api.outbox(self.batch)
                for job in jobs:
                    self.dispatch(job)
            except ApiError as e:
                log.warning("outbox poll: %s", e)
            except Exception:  # noqa: BLE001 — the poll loop must survive anything
                log.exception("outbox poll failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    def dispatch(self, job: Job) -> None:
        job_id = str(job.get("id", ""))
        if not job_id or job_id in self._seen:
            return
        job_type = str(job.get("type", ""))
        if job_type in self.deferred and job_type not in self.handlers:
            if job_id not in self._deferred_logged:
                log.info("deferring %s %s until its handler ships", job_type, job_id)
                self._deferred_logged.add(job_id)
            return
        self._seen.add(job_id)
        entity = str(job.get("entity") or "misc")
        queue = self._queues.get(entity)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[entity] = queue
            self._workers[entity] = asyncio.create_task(self._drain(entity, queue), name=f"outbox:{entity}")
        queue.put_nowait(job)

    async def _drain(self, entity: str, queue: asyncio.Queue[Job]) -> None:
        async with self._entity_slots:
            while True:
                try:
                    job = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                await self.run_one(job)
                queue.task_done()
        self._queues.pop(entity, None)
        self._workers.pop(entity, None)

    # --- one job -------------------------------------------------------------------

    async def run_one(self, job: Job) -> None:
        job_id = str(job["id"])
        job_type = str(job.get("type", ""))
        handler = self.handlers.get(job_type)
        try:
            if handler is None:
                await self.api.ack(job_id, ok=False, error="unknown type")
                log.warning("job %s: unknown type %s", job_id, job_type)
                return
            result = await handler(job)
            await self.api.ack(job_id, ok=True, result=result or {})
            log.info("job %s %s done", job_id, job_type)
        except ApiError as e:
            log.warning("job %s %s: %s", job_id, job_type, e)
            await self._fail(job_id, str(e))
        except Exception as e:  # noqa: BLE001 — every failure is reported to the site, never swallowed
            log.exception("job %s %s failed", job_id, job_type)
            await self._fail(job_id, f"{type(e).__name__}: {e}")
        finally:
            self._seen.discard(job_id)

    async def _fail(self, job_id: str, error: str) -> None:
        try:
            await self.api.ack(job_id, ok=False, error=error)
        except Exception:  # noqa: BLE001
            log.exception("job %s: could not report failure", job_id)
