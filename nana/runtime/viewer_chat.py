"""Public viewer chat inbox for the new Nana runtime.

STAGE-7B keeps external chat transport outside Nana. Discord/YouTube/Twitch
bridges can submit public viewer messages here, while Nana core owns persona
boundary, queue state, duplicate/rate guards, and read-only status.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import itertools
import re
import threading
import time
from typing import Any

from nana.runtime.persona_boundary import resolve_persona_boundary


DEFAULT_MAX_MESSAGES = 50
DEFAULT_DUPLICATE_WINDOW_SECONDS = 5.0
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 30.0
DEFAULT_RATE_LIMIT_MAX = 5
MAX_TEXT_CHARS = 500
PREVIEW_CHARS = 120
DEFAULT_PRIORITY_VIEWERS = ("linhcute2746",)
PRIORITY_PUBLIC_LABEL = "priority_public_viewer"


def _clean_part(value: Any, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text or fallback


def _clean_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:MAX_TEXT_CHARS]


def _preview(value: Any, limit: int = PREVIEW_CHARS) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _normalize_viewer_name(value: Any) -> str:
    text = _clean_part(value, "").lower()
    # Discord may include legacy discriminators or mention wrappers depending on
    # transport/version. Keep this normalization local to routing, not persona.
    text = re.sub(r"^<@!?", "", text)
    text = re.sub(r">$", "", text)
    text = re.sub(r"#\d{4}$", "", text)
    text = text.lstrip("@")
    return re.sub(r"\s+", "", text)


@dataclass(frozen=True)
class ViewerMessage:
    id: int
    platform: str
    channel: str
    viewer_name: str
    text: str
    source_id: str
    received_at: float
    received_monotonic: float
    interaction_scope: str
    persona_lane: str
    memory_policy: str
    status: str = "queued"
    priority: str = "normal"
    can_act: bool = False
    voice_call: bool = False
    vts_call: bool = False
    obs_call: bool = False
    game_input: bool = False

    def to_dict(self, *, now: float | None = None) -> dict[str, Any]:
        data = asdict(self)
        current = time.time() if now is None else now
        data["age_seconds"] = max(0.0, current - self.received_at)
        data["text_preview"] = _preview(self.text)
        return data


class ViewerChatQueue:
    """Session-only public viewer chat queue.

    This class intentionally has no Discord/OBS/TTS/VTS dependencies. It is a
    core runtime inbox that external bridge modules may call later.
    """

    def __init__(
        self,
        *,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        duplicate_window_seconds: float = DEFAULT_DUPLICATE_WINDOW_SECONDS,
        rate_limit_window_seconds: float = DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
        rate_limit_max: int = DEFAULT_RATE_LIMIT_MAX,
        priority_viewers: tuple[str, ...] | list[str] | set[str] | None = None,
    ) -> None:
        self.max_messages = int(max_messages)
        self.duplicate_window_seconds = float(duplicate_window_seconds)
        self.rate_limit_window_seconds = float(rate_limit_window_seconds)
        self.rate_limit_max = int(rate_limit_max)
        raw_priority_viewers = DEFAULT_PRIORITY_VIEWERS if priority_viewers is None else priority_viewers
        self.priority_viewers = {
            normalized
            for normalized in (_normalize_viewer_name(name) for name in raw_priority_viewers)
            if normalized
        }
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._messages: deque[ViewerMessage] = deque(maxlen=self.max_messages)
        self._recent_signatures: dict[tuple[str, str, str, str], float] = {}
        self._viewer_events: dict[tuple[str, str, str], deque[float]] = {}
        self._stats = {
            "accepted": 0,
            "duplicate": 0,
            "rate_limited": 0,
            "rejected": 0,
            "cleared": 0,
            "dropped_by_capacity": 0,
            "priority_accepted": 0,
            "priority_rate_bypass": 0,
        }

    def clear(self) -> dict[str, Any]:
        with self._lock:
            count = len(self._messages)
            self._messages.clear()
            self._recent_signatures.clear()
            self._viewer_events.clear()
            self._stats["cleared"] += count
            return {"cleared": count, "queued": 0, "can_act": False}

    def submit(
        self,
        *,
        platform: str = "manual",
        channel: str = "test",
        viewer_name: str = "viewer",
        text: str = "",
        source_id: str | None = None,
        allow_duplicate_text: bool = False,
        now: float | None = None,
        monotonic_now: float | None = None,
    ) -> dict[str, Any]:
        platform_clean = _clean_part(platform, "manual").lower()
        channel_clean = _clean_part(channel, "test")
        name_clean = _clean_part(viewer_name, "viewer")
        text_clean = _clean_text(text)
        current = time.time() if now is None else float(now)
        current_mono = time.monotonic() if monotonic_now is None else float(monotonic_now)

        if not text_clean:
            with self._lock:
                self._stats["rejected"] += 1
                queued = len(self._messages)
            return {
                "accepted": False,
                "reason": "empty_text",
                "message": None,
                "can_act": False,
                "queued": queued,
            }

        signature = (
            platform_clean,
            channel_clean.lower(),
            name_clean.lower(),
            text_clean.lower(),
        )
        viewer_key = (platform_clean, channel_clean.lower(), name_clean.lower())
        is_priority = self.is_priority_viewer(name_clean)
        priority_label = PRIORITY_PUBLIC_LABEL if is_priority else "normal"

        boundary = resolve_persona_boundary(
            viewer_name=name_clean,
            stream_mode=True,
            platform=platform_clean,
        )

        with self._lock:
            self._expire_recent_locked(current_mono)
            last_seen = self._recent_signatures.get(signature)
            if (
                not allow_duplicate_text
                and last_seen is not None
                and current_mono - last_seen <= self.duplicate_window_seconds
            ):
                self._stats["duplicate"] += 1
                return {
                    "accepted": False,
                    "reason": "duplicate_recent",
                    "message": None,
                    "can_act": False,
                    "queued": len(self._messages),
                    "priority": priority_label,
                }

            events = self._viewer_events.setdefault(viewer_key, deque())
            if is_priority:
                self._stats["priority_rate_bypass"] += 1
            else:
                while events and current_mono - events[0] > self.rate_limit_window_seconds:
                    events.popleft()
                if len(events) >= self.rate_limit_max:
                    self._stats["rate_limited"] += 1
                    return {
                        "accepted": False,
                        "reason": "rate_limited",
                        "message": None,
                        "can_act": False,
                        "queued": len(self._messages),
                        "priority": priority_label,
                    }

            if len(self._messages) >= self.max_messages:
                self._stats["dropped_by_capacity"] += 1

            message = ViewerMessage(
                id=next(self._ids),
                platform=platform_clean,
                channel=channel_clean,
                viewer_name=name_clean,
                text=text_clean,
                source_id=_clean_part(source_id, f"{platform_clean}:{channel_clean}:{int(current * 1000)}"),
                received_at=current,
                received_monotonic=current_mono,
                interaction_scope=boundary.interaction_scope,
                persona_lane=boundary.persona_lane,
                memory_policy=boundary.memory_policy,
                priority=priority_label,
            )
            self._messages.append(message)
            self._recent_signatures[signature] = current_mono
            if not is_priority:
                events.append(current_mono)
            self._stats["accepted"] += 1
            if is_priority:
                self._stats["priority_accepted"] += 1

            return {
                "accepted": True,
                "reason": "queued",
                "message": message,
                "can_act": False,
                "queued": len(self._messages),
                "priority": priority_label,
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            messages = [message.to_dict(now=now) for message in self._messages]
            newest = messages[-1] if messages else None
            oldest = messages[0] if messages else None
            return {
                "mode": "STAGE-7B",
                "queued": len(messages),
                "max_messages": self.max_messages,
                "can_act": False,
                "bridge_connected": False,
                "public_persona": "public_vtuber_persona",
                "stats": dict(self._stats),
                "newest": newest,
                "oldest": oldest,
                "messages": messages,
                "duplicate_window_seconds": self.duplicate_window_seconds,
                "rate_limit_window_seconds": self.rate_limit_window_seconds,
                "rate_limit_max": self.rate_limit_max,
                "priority_viewers": sorted(self.priority_viewers),
                "priority_rate_limit_bypass": True,
            }

    def size(self) -> int:
        with self._lock:
            return len(self._messages)

    def is_priority_viewer(self, viewer_name: Any) -> bool:
        return _normalize_viewer_name(viewer_name) in self.priority_viewers

    def _expire_recent_locked(self, monotonic_now: float) -> None:
        expired = [
            key
            for key, seen_at in self._recent_signatures.items()
            if monotonic_now - seen_at > self.duplicate_window_seconds
        ]
        for key in expired:
            self._recent_signatures.pop(key, None)


_VIEWER_CHAT_QUEUE = ViewerChatQueue()


def get_viewer_chat_queue() -> ViewerChatQueue:
    return _VIEWER_CHAT_QUEUE


def submit_viewer_chat_message(
    *,
    platform: str = "manual",
    channel: str = "test",
    viewer_name: str = "viewer",
    text: str = "",
    source_id: str | None = None,
    allow_duplicate_text: bool = False,
) -> dict[str, Any]:
    return get_viewer_chat_queue().submit(
        platform=platform,
        channel=channel,
        viewer_name=viewer_name,
        text=text,
        source_id=source_id,
        allow_duplicate_text=allow_duplicate_text,
    )


def clear_viewer_chat_queue() -> dict[str, Any]:
    return get_viewer_chat_queue().clear()


def viewer_chat_status_lines() -> list[str]:
    snapshot = get_viewer_chat_queue().snapshot()
    stats = dict(snapshot.get("stats") or {})
    newest = snapshot.get("newest") or {}
    lines = [
        "💬 Viewer Chat Queue",
        "  Mode: STAGE-7B | read_only=True | can_act=False",
        (
            "  Queue: "
            f"{snapshot.get('queued')}/{snapshot.get('max_messages')} | "
            f"bridge_connected={snapshot.get('bridge_connected')} | "
            f"persona={snapshot.get('public_persona')}"
        ),
        (
            "  Guards: "
            f"duplicate_window={snapshot.get('duplicate_window_seconds'):.1f}s | "
            f"rate_limit={snapshot.get('rate_limit_max')}/"
            f"{snapshot.get('rate_limit_window_seconds'):.0f}s per viewer"
        ),
        (
            "  Priority gate: "
            f"users={', '.join(snapshot.get('priority_viewers') or []) or 'none'} | "
            f"bypass_rate_limit={snapshot.get('priority_rate_limit_bypass')} | "
            "persona=public_vtuber_persona"
        ),
        (
            "  Stats: "
            f"accepted={stats.get('accepted', 0)} | duplicate={stats.get('duplicate', 0)} | "
            f"rate_limited={stats.get('rate_limited', 0)} | rejected={stats.get('rejected', 0)} | "
            f"dropped={stats.get('dropped_by_capacity', 0)} | cleared={stats.get('cleared', 0)} | "
            f"priority={stats.get('priority_accepted', 0)}"
        ),
    ]
    if newest:
        lines.append(
            "  Newest: "
            f"#{newest.get('id')} | {newest.get('platform')}#{newest.get('channel')} | "
            f"{newest.get('viewer_name')} | priority={newest.get('priority')}: {newest.get('text_preview')}"
        )
        lines.append(
            "  Boundary: "
            f"scope={newest.get('interaction_scope')} | lane={newest.get('persona_lane')} | "
            f"memory={newest.get('memory_policy')}"
        )
    else:
        lines.append("  Newest: none")
    lines.extend(
        [
            "  Safety: voice_call=False | vts_call=False | subtitle_call=False | obs_call=False | game_input=False",
            "  Live verify: /viewer-chat-status | /viewer-chat-test linh|hello Nana | /viewer-chat-clear",
        ]
    )
    return lines


def viewer_chat_test_report(
    name: str = "viewer",
    message: str = "hello Nana",
    *,
    platform: str = "discord",
    channel: str = "chung",
) -> list[str]:
    result = submit_viewer_chat_message(
        platform=platform,
        channel=channel,
        viewer_name=name,
        text=message,
        source_id=f"manual-test:{name}:{int(time.time() * 1000)}",
    )
    queued_message = result.get("message")
    if isinstance(queued_message, ViewerMessage):
        boundary = (
            f"scope={queued_message.interaction_scope} | lane={queued_message.persona_lane} | "
            f"memory={queued_message.memory_policy}"
        )
        preview = _preview(queued_message.text)
        message_id = queued_message.id
    else:
        boundary = "scope=none | lane=none | memory=none"
        preview = _preview(message)
        message_id = "none"
    return [
        "🧪 Viewer Chat Queue Test",
        f"  Input: platform={platform} | channel={channel} | name={name} | message={preview}",
        f"  Accepted: {result.get('accepted')} | reason={result.get('reason')} | queued={result.get('queued')}",
        f"  Message ID: {message_id}",
        f"  Boundary: {boundary}",
        f"  Priority: {getattr(queued_message, 'priority', result.get('priority', 'normal'))}",
        "  Safety: can_act=False | voice_call=False | vts_call=False | obs_call=False | game_input=False",
    ]


def viewer_chat_clear_report() -> list[str]:
    result = clear_viewer_chat_queue()
    return [
        "🧹 Viewer Chat Queue Clear",
        f"  Cleared: {result.get('cleared')} | queued={result.get('queued')}",
        "  Safety: can_act=False | no bridge/voice/VTS/OBS call.",
    ]
