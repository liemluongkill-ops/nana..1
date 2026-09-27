"""Controlled outbound Discord outbox for Nana stage messages."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class DiscordOutboxEvent:
    event_id: str
    proposal_id: str
    text: str
    channel_id: int
    guild_id: Optional[int]
    channel_name: str
    created_at: float
    auto_send: bool
    phase: str
    raw: dict

    @classmethod
    def from_path(cls, path: Path) -> "DiscordOutboxEvent":
        payload = json.loads(path.read_text(encoding="utf-8"))
        event_id = str(payload.get("event_id") or path.stem)
        proposal_id = str(payload.get("proposal_id") or "")
        text = str(payload.get("text") or "")
        raw_channel_id = str(payload.get("channel_id") or "").strip()
        if not raw_channel_id:
            raise ValueError("missing channel_id")
        try:
            channel_id = int(raw_channel_id)
        except ValueError as exc:
            raise ValueError("channel_id must be an integer") from exc
        raw_guild_id = str(payload.get("guild_id") or "").strip()
        guild_id = int(raw_guild_id) if raw_guild_id else None
        channel_name = str(payload.get("channel_name") or "").strip().lstrip("#")
        if not text.strip():
            raise ValueError("missing text")
        return cls(
            event_id=event_id,
            proposal_id=proposal_id,
            text=text[:1900],
            channel_id=channel_id,
            guild_id=guild_id,
            channel_name=channel_name,
            created_at=float(payload.get("created_at") or time.time()),
            auto_send=bool(payload.get("auto_send", False)),
            phase=str(payload.get("phase") or ""),
            raw=dict(payload),
        )


def iter_outbox_files(outbox_dir: Path, limit: int = 5) -> list[Path]:
    if not outbox_dir.exists():
        return []
    files = [
        item for item in outbox_dir.glob("*.json")
        if item.is_file() and not item.name.startswith(".")
    ]
    return sorted(files, key=lambda item: item.stat().st_mtime)[:limit]


def move_outbox_file(path: Path, state: str, *, reason: Optional[str] = None) -> Path:
    target_dir = path.parent / state
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        target = target_dir / f"{path.stem}-{int(time.time())}{path.suffix}"
    if reason:
        reason_path = target.with_suffix(target.suffix + ".reason.txt")
        reason_path.write_text(reason, encoding="utf-8")
    shutil.move(str(path), str(target))
    return target
