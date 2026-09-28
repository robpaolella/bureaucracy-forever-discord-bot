import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from sync.raids import RaidsCog


def _http_error(cls):
    return cls(SimpleNamespace(status=404 if cls is discord.NotFound else 403, reason="x"), "x")


def _cog(thread=None, message=None):
    cog = RaidsCog.__new__(RaidsCog)
    bot = MagicMock()
    bot.get_channel.return_value = thread
    bot.fetch_channel = AsyncMock(return_value=thread)
    cog.bot = bot
    channel = MagicMock()
    channel.get_partial_message.return_value = message
    cog.channel = lambda: channel  # type: ignore[method-assign]
    return cog, channel


def test_delete_removes_thread_then_message_from_payload_ids():
    thread = MagicMock(spec=discord.Thread)
    thread.delete = AsyncMock()
    message = SimpleNamespace(delete=AsyncMock())
    cog, channel = _cog(thread, message)
    asyncio.run(cog.job_delete({"payload": {"raidId": "r1", "threadId": "11", "messageId": "22"}}))
    thread.delete.assert_awaited_once()
    channel.get_partial_message.assert_called_once_with(22)
    message.delete.assert_awaited_once()


def test_delete_treats_missing_objects_as_done():
    thread = MagicMock(spec=discord.Thread)
    thread.delete = AsyncMock(side_effect=_http_error(discord.NotFound))
    message = SimpleNamespace(delete=AsyncMock(side_effect=_http_error(discord.NotFound)))
    cog, _ = _cog(thread, message)
    asyncio.run(cog.job_delete({"payload": {"raidId": "r1", "threadId": "11", "messageId": "22"}}))


def test_delete_archives_and_locks_a_thread_it_may_not_delete():
    thread = MagicMock(spec=discord.Thread)
    thread.delete = AsyncMock(side_effect=_http_error(discord.Forbidden))
    thread.edit = AsyncMock()
    message = SimpleNamespace(delete=AsyncMock())
    cog, _ = _cog(thread, message)
    asyncio.run(cog.job_delete({"payload": {"raidId": "r1", "threadId": "11", "messageId": "22"}}))
    thread.edit.assert_awaited_once_with(archived=True, locked=True)
    message.delete.assert_awaited_once()


def test_delete_without_discord_ids_does_nothing():
    cog, channel = _cog()
    asyncio.run(cog.job_delete({"payload": {"raidId": "r1"}}))
    channel.get_partial_message.assert_not_called()
