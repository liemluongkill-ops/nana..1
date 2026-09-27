"""Route Discord text input to the correct Nana voice output boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import discord

from .config import BridgeConfig
from .route_config import RouteConfig
from .safety import VoiceTarget, resolve_voice_channel


@dataclass(frozen=True)
class DiscordRoute:
    chat_channel_id: int
    chat_channel_name: str
    voice_channel_id: Optional[int]
    voice_channel_name: str
    voice_source: str
    input_role: str
    can_play_voice: bool

    @property
    def voice_label(self) -> str:
        return self.voice_channel_name if self.can_play_voice else "none"

    def metadata(self) -> dict:
        input_surface = "discord_media" if self.input_role == "media" else "discord_text"
        return {
            "input_surface": input_surface,
            "output_surface": "discord_voice" if self.can_play_voice else "discord_text_only",
            "input_role": self.input_role,
            "chat_channel_id": self.chat_channel_id,
            "chat_channel_name": self.chat_channel_name,
            "voice_channel_id": self.voice_channel_id,
            "voice_channel_name": self.voice_channel_name,
            "voice_source": self.voice_source,
            "can_play_voice": self.can_play_voice,
            "local_playback": False,
        }


def build_route(message: discord.Message, config: BridgeConfig) -> tuple[DiscordRoute, Optional[VoiceTarget]]:
    guild = message.guild
    route_config = RouteConfig.load(config.route_config_path)
    guild_route = route_config.guild_for(guild.id if guild else None)

    input_role = "dm" if guild is None else "unconfigured_text"
    configured_voice_channel_id = None
    if guild_route is not None:
        input_role = guild_route.input_role_for(message.channel.id)
        configured_voice_channel_id = guild_route.voice_channel_for(message.channel.id)

    voice_channel = None
    if guild is not None and configured_voice_channel_id is not None:
        channel = guild.get_channel(configured_voice_channel_id)
        if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            voice_channel = channel

    if voice_channel is None:
        voice_channel = resolve_voice_channel(message, config)
    voice_source = "none"
    if voice_channel is not None:
        if configured_voice_channel_id is not None:
            voice_source = "routes_json"
        elif config.allowed_voice_channel_id:
            voice_source = "configured_voice_channel"
        else:
            voice_source = "caller_current_voice"

    route = DiscordRoute(
        chat_channel_id=message.channel.id,
        chat_channel_name=getattr(message.channel, "name", "unknown"),
        voice_channel_id=voice_channel.id if voice_channel else None,
        voice_channel_name=voice_channel.name if voice_channel else "",
        voice_source=voice_source,
        input_role=input_role,
        can_play_voice=voice_channel is not None,
    )
    return route, voice_channel
