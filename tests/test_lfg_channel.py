from types import SimpleNamespace
from unittest.mock import MagicMock

import discord

import lfg


def _interaction(channels):
    here = MagicMock(spec=discord.TextChannel)
    guild = SimpleNamespace(get_channel=lambda cid: channels.get(cid))
    return SimpleNamespace(channel=here, guild=guild), here


def test_posts_go_to_the_lfg_channel_wherever_the_command_ran(monkeypatch):
    lfg_channel = MagicMock(spec=discord.TextChannel)
    monkeypatch.setattr(lfg, "LFG_CHANNEL_ID", 42)
    interaction, _ = _interaction({42: lfg_channel})
    assert lfg.post_channel(interaction) is lfg_channel


def test_without_an_lfg_channel_posts_stay_where_the_command_ran(monkeypatch):
    monkeypatch.setattr(lfg, "LFG_CHANNEL_ID", 0)
    interaction, here = _interaction({})
    assert lfg.post_channel(interaction) is here


def test_a_configured_channel_that_is_missing_or_not_text_gives_none(monkeypatch):
    monkeypatch.setattr(lfg, "LFG_CHANNEL_ID", 42)
    assert lfg.post_channel(_interaction({})[0]) is None
    assert lfg.post_channel(_interaction({42: MagicMock(spec=discord.VoiceChannel)})[0]) is None
