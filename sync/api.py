"""HTTP client for the site's /api/bot/* routes (SYNC-SPEC.md §2, §4).

Every request carries `Authorization: Bearer <secret>`; every write carries an
`Idempotency-Key` taken from the Discord interaction or message that caused it, so a retry
replays the site's first answer instead of applying twice.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import aiohttp

log = logging.getLogger("sync.api")

JSON = dict[str, Any]


class ApiError(Exception):
    """A non-2xx answer. `reason` is what the bot may show verbatim to the user who clicked."""

    def __init__(self, status: int, body: Any):
        self.status = status
        self.body = body if isinstance(body, dict) else {}
        super().__init__(f"site answered {status}: {self.reason}")

    @property
    def reason(self) -> str:
        return str(self.body.get("reason") or self.body.get("error") or "Something went wrong on the site.")


class SiteApi:
    def __init__(self, base_url: str, secret: str, *, timeout_s: float = 15.0):
        self.base = base_url.rstrip("/") + "/api/bot"
        self._headers = {"Authorization": f"Bearer {secret}", "Accept": "application/json"}
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout, headers=self._headers)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def headers_for(self, idempotency_key: str | None) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if idempotency_key:
            h["Idempotency-Key"] = str(idempotency_key)[:128]
        return h

    async def request(self, method: str, path: str, *, json: JSON | None = None, params: dict[str, str] | None = None, idempotency_key: str | None = None) -> JSON:
        if self._session is None:
            await self.start()
        assert self._session is not None
        async with self._session.request(method, self.base + path, json=json, params=params, headers=self.headers_for(idempotency_key)) as resp:
            body: Any = None
            try:
                body = await resp.json(content_type=None)
            except Exception:  # noqa: BLE001 — a non-JSON answer is still an error worth its status
                body = {"error": (await resp.text())[:300]}
            if resp.status >= 400:
                raise ApiError(resp.status, body)
            return body if isinstance(body, dict) else {}

    # --- outbox and tick ---------------------------------------------------------------

    async def outbox(self, limit: int = 20) -> list[JSON]:
        data = await self.request("GET", "/outbox", params={"limit": str(limit)})
        jobs = data.get("jobs", [])
        return jobs if isinstance(jobs, list) else []

    async def ack(self, job_id: str, *, ok: bool, result: JSON | None = None, error: str | None = None) -> JSON:
        body: JSON = {"ok": True, "result": result or {}} if ok else {"ok": False, "error": (error or "unknown error")[:1000]}
        return await self.request("POST", f"/outbox/{job_id}/ack", json=body)

    async def tick(self) -> JSON:
        return await self.request("POST", "/tick")

    async def health(self) -> JSON:
        return await self.request("GET", "/health")

    # --- applications ------------------------------------------------------------------

    async def application(self, app_id: str) -> JSON:
        return await self.request("GET", f"/applications/{app_id}")

    async def decide(self, app_id: str, status: str, by_discord_id: int, *, reason: str | None, key: str) -> JSON:
        body: JSON = {"status": status, "byDiscordId": str(by_discord_id)}
        if reason:
            body["reason"] = reason
        return await self.request("POST", f"/applications/{app_id}/decision", json=body, idempotency_key=key)

    async def reopen(self, app_id: str, by_discord_id: int, *, key: str) -> JSON:
        return await self.request("POST", f"/applications/{app_id}/reopen", json={"byDiscordId": str(by_discord_id)}, idempotency_key=key)

    async def post_note(self, app_id: str, *, message_id: int, author_discord_id: int, body: str, created_at: str) -> JSON:
        payload = {"discordMessageId": str(message_id), "authorDiscordId": str(author_discord_id), "body": body, "createdAt": created_at}
        return await self.request("POST", f"/applications/{app_id}/notes", json=payload, idempotency_key=f"note:{message_id}")

    async def edit_note(self, message_id: int, body: str) -> JSON:
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        return await self.request("PATCH", f"/notes/by-message/{message_id}", json={"body": body}, idempotency_key=f"note-edit:{message_id}:{digest}")

    async def delete_note(self, message_id: int) -> JSON:
        return await self.request("DELETE", f"/notes/by-message/{message_id}", idempotency_key=f"note-delete:{message_id}")

    # --- raids and members ------------------------------------------------------------

    async def raid(self, raid_id: str, discord_id: int | None = None) -> JSON:
        params = {"discordId": str(discord_id)} if discord_id else None
        return await self.request("GET", f"/raids/{raid_id}", params=params)

    async def respond(self, raid_id: str, discord_id: int, response: str, *, reason: str | None, key: str) -> JSON:
        body: JSON = {"discordId": str(discord_id), "response": response}
        if reason:
            body["reason"] = reason
        return await self.request("POST", f"/raids/{raid_id}/respond", json=body, idempotency_key=key)

    async def bench(self, raid_id: str, discord_id: int, *, key: str) -> JSON:
        return await self.request("POST", f"/raids/{raid_id}/bench", json={"discordId": str(discord_id)}, idempotency_key=key)

    async def member(self, discord_id: int) -> JSON | None:
        try:
            return await self.request("GET", f"/members/{discord_id}")
        except ApiError as e:
            if e.status == 404:
                return None
            raise
