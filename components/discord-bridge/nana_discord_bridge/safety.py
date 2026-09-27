"""Small safety helpers for Discord event filtering."""

from __future__ import annotations

import re
from typing import Optional, Union

import discord

from .config import BridgeConfig


MENTION_RE = re.compile(r"<@!?\d+>")
VoiceTarget = Union[discord.VoiceChannel, discord.StageChannel]


def is_allowed_message(message: discord.Message, config: BridgeConfig) -> bool:
    if message.author.bot:
        return False
    if config.allowed_guild_id is not None:
        if message.guild is None or message.guild.id != config.allowed_guild_id:
            return False
    if config.allowed_text_channel_id is not None:
        if message.channel.id != config.allowed_text_channel_id:
            return False
    return True


def extract_prompt(message: discord.Message, bot_user: Optional[discord.ClientUser], prefix: str) -> Optional[str]:
    content = (message.content or "").strip()
    if not content:
        return None

    lowered = content.lower()
    prefix_lower = prefix.lower()
    if lowered.startswith(prefix_lower):
        return content[len(prefix):].strip()

    if bot_user and bot_user.mentioned_in(message):
        cleaned = MENTION_RE.sub("", content).strip()
        return cleaned

    return None


def _is_voice_target(channel: object) -> bool:
    return isinstance(channel, (discord.VoiceChannel, discord.StageChannel))


def resolve_voice_channel(message: discord.Message, config: BridgeConfig) -> Optional[VoiceTarget]:
    guild = message.guild
    if guild is None:
        return None

    if config.allowed_voice_channel_id is not None:
        channel = guild.get_channel(config.allowed_voice_channel_id)
        if _is_voice_target(channel):
            return channel

    if config.auto_join_user_voice:
        voice_state = getattr(message.author, "voice", None)
        channel = getattr(voice_state, "channel", None)
        if _is_voice_target(channel):
            return channel

    return None
