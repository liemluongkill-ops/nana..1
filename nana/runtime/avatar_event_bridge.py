"""STAGE-8K: Public viewer-event metadata bridge for avatar reactions.

This module translates public chat/starter metadata into the canonical avatar
event names used by STAGE-8J avatar_director. It is session-only and
decision-only: no VTS, OBS, TTS, Discord, subtitle, or game calls.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


PHASE = "STAGE-8K"

EVENT_MESSAGE = "message"
EVENT_EMOJI_ONLY = "emoji_only"
EVENT_STICKER = "sticker"
EVENT_STARTER = "auto_starter"
EVENT_HIGHLIGHT = "highlight"
EVENT_SUBSCRIPTION = "subscription"


def _clean(value: Any, fallback: str = "") -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text or fallback


def _short(value: Any, limit: int = 120) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _has_metadata_list(metadata: dict[str, Any], key: str) -> bool:
    value = metadata.get(key)
    return bool(value) if isinstance(value, (list, tuple, set, dict)) else bool(value)


def _is_emoji_only_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return False
    if re.fullmatch(r"<a?:[A-Za-z0-9_]{1,64}:\d{10,32}>", compact):
        return True
    return not any(ch.isalnum() for ch in compact) and any(ord(ch) > 127 for ch in compact)


def classify_avatar_event(
    *,
    text: Any = "",
    event_type: str = "message",
    metadata: dict[str, Any] | None = None,
    source: str = "discord",
) -> str:
    """Return a canonical avatar event name for public-stage input."""
    raw = _clean(text)
    event = _clean(event_type, "message").lower()
    meta = metadata if isinstance(metadata, dict) else {}
    route = meta.get("route") if isinstance(meta.get("route"), dict) else {}

    if event in {"starter", "auto_starter", "social_starter", "starter_proposal"}:
        return EVENT_STARTER
    if _clean(source).lower() in {"starter", "starter_proposal", "auto_starter"}:
        return EVENT_STARTER
    if meta.get("source") in {"starter_proposal", "auto_starter"}:
        return EVENT_STARTER

    if event in {"subscription", "sub", "follow", "follower", "gift", "member"}:
        return EVENT_SUBSCRIPTION
    if event in {"highlight", "superchat", "milestone"}:
        return EVENT_HIGHLIGHT

    if event in {"sticker", "sticker_create", "sticker_update"}:
        return EVENT_STICKER
    if _has_metadata_list(meta, "stickers") or route.get("input_has_sticker"):
        return EVENT_STICKER

    if event in {"emoji", "emoji_only", "emote", "reaction"}:
        return EVENT_EMOJI_ONLY
    if _is_emoji_only_text(raw):
        return EVENT_EMOJI_ONLY

    if route.get("input_has_media") and not raw:
        return EVENT_STICKER
    return EVENT_MESSAGE


@dataclass(frozen=True)
class AvatarEventRecord:
    event: str
    source: str
    viewer_name: str = ""
    channel: str = ""
    text_preview: str = ""
    request_id: str = ""
    reason: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self, *, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        data = asdict(self)
        data["age_seconds"] = max(0.0, current - self.created_at)
        return data


class AvatarEventBridge:
    """Session-only bridge from public viewer events to avatar event names."""

    _instance: Optional["AvatarEventBridge"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_event = AvatarEventRecord(EVENT_MESSAGE, "init", reason="none")
        self._stats = {
            "observed": 0,
            "message": 0,
            "emoji_only": 0,
            "sticker": 0,
            "starter": 0,
            "highlight": 0,
            "subscription": 0,
        }

    @classmethod
    def get_instance(cls) -> "AvatarEventBridge":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_test(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def observe_public_event(
        self,
        *,
        text: Any = "",
        event_type: str = "message",
        metadata: dict[str, Any] | None = None,
        source: str = "discord",
        viewer_name: str = "",
        channel: str = "",
        request_id: str = "",
        reason: str = "",
    ) -> AvatarEventRecord:
        event = classify_avatar_event(
            text=text,
            event_type=event_type,
            metadata=metadata,
            source=source,
        )
        record = AvatarEventRecord(
            event=event,
            source=_clean(source, "discord"),
            viewer_name=_clean(viewer_name),
            channel=_clean(channel),
            text_preview=_short(text),
            request_id=_clean(request_id),
            reason=_clean(reason, "public_event"),
        )
        with self._lock:
            self._last_event = record
            self._stats["observed"] += 1
            if event in self._stats:
                self._stats[event] += 1
        return record

    def observe_starter_event(
        self,
        *,
        text: Any = "",
        proposal_id: str = "",
        event_id: str = "",
        channel: str = "",
    ) -> AvatarEventRecord:
        return self.observe_public_event(
            text=text,
            event_type=EVENT_STARTER,
            metadata={"source": "starter_proposal", "proposal_id": proposal_id, "event_id": event_id},
            source="starter_proposal",
            channel=channel,
            request_id=event_id or proposal_id,
            reason="starter_send",
        )

    def last_event(self) -> AvatarEventRecord:
        with self._lock:
            return self._last_event

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "phase": PHASE,
                "read_only": True,
                "can_act": False,
                "last_event": self._last_event.to_dict(),
                "stats": dict(self._stats),
                "safety": {
                    "voice_call": False,
                    "vts_call": False,
                    "obs_call": False,
                    "game_input": False,
                    "discord_send": False,
                },
            }

    def status_lines(self) -> list[str]:
        snap = self.snapshot()
        last = dict(snap.get("last_event") or {})
        stats = dict(snap.get("stats") or {})
        return [
            "🎭 Avatar Event Bridge (STAGE-8K)",
            "  Mode: metadata-only | read_only=True | can_act=False",
            (
                "  Last: "
                f"event={last.get('event')} | source={last.get('source')} | "
                f"viewer={last.get('viewer_name') or 'none'} | age={last.get('age_seconds', 0.0):.1f}s"
            ),
            f"  Preview: {last.get('text_preview') or 'none'}",
            (
                "  Stats: "
                f"observed={stats.get('observed', 0)} | message={stats.get('message', 0)} | "
                f"emoji={stats.get('emoji_only', 0)} | sticker={stats.get('sticker', 0)} | "
                f"starter={stats.get('starter', 0)} | highlight={stats.get('highlight', 0)}"
            ),
            "  Commands: /avatar-event-status | /avatar-event-preview | /avatar-event-test <text>",
            "  Safety: metadata only; no VTS/OBS/TTS/Discord/game input.",
        ]


def get_avatar_event_bridge() -> AvatarEventBridge:
    return AvatarEventBridge.get_instance()


def avatar_event_status_lines() -> list[str]:
    return get_avatar_event_bridge().status_lines()


def avatar_event_preview_lines() -> list[str]:
    record = get_avatar_event_bridge().last_event()
    return [
        "🎭 Avatar Event Preview (STAGE-8K)",
        f"  Last event: {record.event} | source={record.source} | viewer={record.viewer_name or 'none'}",
        f"  Text preview: {record.text_preview or 'none'}",
        "  Next dry-run commands:",
        f"    /avatar-director-preview {record.event}",
        f"    /avatar-reaction-preview {record.event}",
        f"    /avatar-vts-preview {record.event}",
        "  Safety: preview pointers only; no VTS/OBS/TTS/Discord/game input.",
    ]


def avatar_event_test_lines(text: str = "") -> list[str]:
    record = get_avatar_event_bridge().observe_public_event(
        text=text,
        event_type="message",
        source="manual-test",
        viewer_name="tester",
        reason="manual_test",
    )
    return [
        "🧪 Avatar Event Test (STAGE-8K)",
        f"  Input: {text or '(empty)'}",
        f"  Classified: {record.event}",
        f"  Reason: {record.reason}",
        "  Safety: metadata-only; no VTS/OBS/TTS/Discord/game input.",
    ]
