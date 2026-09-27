"""JSON contract models shared by the Discord bridge and Nana core."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def _now() -> float:
    return time.time()


@dataclass
class NanaDiscordRequest:
    request_id: str
    source: str
    event_type: str
    text: str
    guild_id: Optional[int]
    channel_id: Optional[int]
    voice_channel_id: Optional[int]
    author_id: Optional[int]
    author_name: str
    audio_target: str = "discord_voice"
    local_playback: bool = False
    created_at: float = field(default_factory=_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        text: str,
        guild_id: Optional[int],
        channel_id: Optional[int],
        voice_channel_id: Optional[int],
        author_id: Optional[int],
        author_name: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "NanaDiscordRequest":
        return cls(
            request_id=f"discord-{uuid.uuid4().hex}",
            source="discord",
            event_type="message",
            text=text,
            guild_id=guild_id,
            channel_id=channel_id,
            voice_channel_id=voice_channel_id,
            author_id=author_id,
            author_name=author_name,
            metadata=metadata or {},
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    def write(self, path: Path) -> None:
        path.write_text(self.to_json(), encoding="utf-8")


@dataclass
class NanaDiscordReply:
    request_id: str
    ok: bool
    reply_text: str = ""
    speak: bool = False
    audio_paths: List[str] = field(default_factory=list)
    audio_target: str = "discord_voice"
    local_playback: bool = False
    status: str = "ok"
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json_file(cls, path: Path) -> "NanaDiscordReply":
        payload = json.loads(path.read_text(encoding="utf-8"))
        audio_paths = payload.get("audio_paths") or []
        if isinstance(audio_paths, str):
            audio_paths = [audio_paths]
        return cls(
            request_id=str(payload.get("request_id") or path.stem),
            ok=bool(payload.get("ok", False)),
            reply_text=str(payload.get("reply_text") or ""),
            speak=bool(payload.get("speak", False)),
            audio_paths=[str(item) for item in audio_paths],
            audio_target=str(payload.get("audio_target") or "discord_voice"),
            local_playback=bool(payload.get("local_playback", False)),
            status=str(payload.get("status") or "ok"),
            error=payload.get("error"),
            metadata=dict(payload.get("metadata") or {}),
        )
