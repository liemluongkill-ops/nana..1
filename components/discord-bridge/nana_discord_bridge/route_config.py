"""Optional multi-server/channel routing configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


def _int_or_none(value) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(str(value).strip())


def _int_set(values) -> set[int]:
    return {int(str(value).strip()) for value in (values or []) if str(value).strip()}


def _int_map(mapping) -> Dict[int, int]:
    return {
        int(str(key).strip()): int(str(value).strip())
        for key, value in (mapping or {}).items()
        if str(key).strip() and str(value).strip()
    }


@dataclass(frozen=True)
class GuildRoute:
    guild_id: int
    name: str = ""
    chat_channel_ids: set[int] = field(default_factory=set)
    media_channel_ids: set[int] = field(default_factory=set)
    default_voice_channel_id: Optional[int] = None
    voice_by_text_channel: Dict[int, int] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, guild_id: int, payload: dict) -> "GuildRoute":
        return cls(
            guild_id=guild_id,
            name=str(payload.get("name") or ""),
            chat_channel_ids=_int_set(payload.get("chat_channel_ids")),
            media_channel_ids=_int_set(payload.get("media_channel_ids")),
            default_voice_channel_id=_int_or_none(payload.get("default_voice_channel_id")),
            voice_by_text_channel=_int_map(payload.get("voice_by_text_channel")),
        )

    def input_role_for(self, channel_id: int) -> str:
        if channel_id in self.media_channel_ids:
            return "media"
        if channel_id in self.chat_channel_ids:
            return "chat"
        return "unconfigured_text"

    def voice_channel_for(self, text_channel_id: int) -> Optional[int]:
        return self.voice_by_text_channel.get(text_channel_id) or self.default_voice_channel_id


@dataclass(frozen=True)
class RouteConfig:
    path: Path
    guilds: Dict[int, GuildRoute] = field(default_factory=dict)
    error: str = ""

    @classmethod
    def load(cls, path: Path) -> "RouteConfig":
        if not path.exists():
            return cls(path=path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return cls(path=path, error=str(exc))
        guilds = {}
        try:
            for raw_guild_id, guild_payload in (payload.get("guilds") or {}).items():
                guild_id = int(str(raw_guild_id).strip())
                guilds[guild_id] = GuildRoute.from_payload(guild_id, dict(guild_payload or {}))
        except (TypeError, ValueError) as exc:
            return cls(path=path, error=str(exc))
        return cls(path=path, guilds=guilds)

    def guild_for(self, guild_id: Optional[int]) -> Optional[GuildRoute]:
        if guild_id is None:
            return None
        return self.guilds.get(guild_id)
