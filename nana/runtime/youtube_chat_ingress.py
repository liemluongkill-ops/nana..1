"""Offline-safe YouTube Live Chat ingress contract.

This module owns only normalization and bounded buffering. Explicit YouTube
REST/streamList transports or the separately gated local fixture simulator feed
responses into it, and default-off CUM1 may project accepted events into the
Stream contract. This module does not make network calls, invoke models, play
audio, write OBS output, or change stream state.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import re
import threading
import time
from typing import Any, Mapping


PHASE = "STREAM-YT-INGRESS-1"
DEFAULT_RECENT_LIMIT = 30
DEFAULT_REALTIME_LIMIT = 10
DEFAULT_ACTIONABLE_LIMIT = 1
DEFAULT_ACTIONABLE_TTL_SECONDS = 30.0
MAX_MESSAGE_ID_CHARS = 160
MAX_TEXT_CHARS = 500
MAX_VIEWER_CHARS = 120

_EVENT_TYPE_NAMES = {
    0: "invalid_type",
    1: "text_message_event",
    2: "tombstone",
    3: "fan_funding_event",
    4: "chat_ended_event",
    5: "sponsor_only_mode_started_event",
    6: "sponsor_only_mode_ended_event",
    7: "new_sponsor_event",
    10: "user_banned_event",
    15: "super_chat_event",
    16: "super_sticker_event",
    17: "member_milestone_chat_event",
    18: "membership_gifting_event",
    19: "gift_membership_received_event",
    20: "poll_event",
    21: "gift_event",
}


def _field(value: Any, *names: str, default: Any = None) -> Any:
    """Read a field from either a dict-like fake or a protobuf-style object."""

    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _clean(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _normalize_event_type(value: Any) -> str:
    if isinstance(value, int) and value in _EVENT_TYPE_NAMES:
        return _EVENT_TYPE_NAMES[value]
    text = _clean(value, 80)
    text = re.sub(r"(?<!^)(?=[A-Z])", "_", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text or "unknown"


def _parse_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    if hasattr(value, "ToDatetime"):
        try:
            value = value.ToDatetime()
        except Exception:
            return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _fallback_message_id(
    *,
    event_type: str,
    viewer_name: str,
    text: str,
    published_at: float | None,
) -> str:
    material = "|".join(
        (
            event_type,
            viewer_name.lower(),
            text,
            "" if published_at is None else f"{published_at:.6f}",
        )
    )
    return "derived-" + sha256(material.encode("utf-8")).hexdigest()[:32]


def _message_text(snippet: Any) -> str:
    direct = _field(snippet, "displayMessage", "display_message", default="")
    if direct:
        return _clean(direct, MAX_TEXT_CHARS)
    details = _field(snippet, "textMessageDetails", "text_message_details", default=None)
    nested = _field(details, "messageText", "message_text", default="")
    if nested:
        return _clean(nested, MAX_TEXT_CHARS)
    for key in ("userComment", "user_comment", "altText", "alt_text"):
        value = _field(snippet, key, default="")
        if value:
            return _clean(value, MAX_TEXT_CHARS)
    return ""


@dataclass(frozen=True)
class ViewerEvent:
    """Sanitized, transport-neutral representation of one public chat event."""

    message_id: str
    event_type: str
    viewer_name: str
    text: str
    published_at: float | None
    received_at: float
    delivery_ms: float | None
    live_chat_id: str
    author_channel_id: str
    actionable_candidate: bool
    source: str = "youtube_live_chat"
    message_id_origin: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["delivery_ms"] = None if self.delivery_ms is None else round(self.delivery_ms, 3)
        return data


def normalize_youtube_chat_message(
    raw_message: Any,
    *,
    received_at: float | None = None,
    live_chat_id: str | None = None,
) -> ViewerEvent:
    """Normalize a REST or streamList-shaped message without retaining raw data."""

    received = time.time() if received_at is None else float(received_at)
    snippet = _field(raw_message, "snippet", default=raw_message)
    author = _field(raw_message, "authorDetails", "author_details", default=None)
    event_type = _normalize_event_type(
        _field(snippet, "type", default=_field(raw_message, "type", default="unknown")),
    )
    viewer_name = _clean(
        _field(author, "displayName", "display_name", default=_field(raw_message, "viewer_name", default="viewer")),
        MAX_VIEWER_CHARS,
    ) or "viewer"
    text = _message_text(snippet)
    published_at = _parse_timestamp(_field(snippet, "publishedAt", "published_at", default=None))
    message_id = _clean(_field(raw_message, "id", "message_id", default=""), MAX_MESSAGE_ID_CHARS)
    message_id_origin = "provider" if message_id else "derived"
    if not message_id:
        message_id = _fallback_message_id(
            event_type=event_type,
            viewer_name=viewer_name,
            text=text,
            published_at=published_at,
        )
    chat_id = _clean(
        live_chat_id
        or _field(snippet, "liveChatId", "live_chat_id", default=_field(raw_message, "live_chat_id", default="")),
        MAX_MESSAGE_ID_CHARS,
    )
    author_channel_id = _clean(
        _field(snippet, "authorChannelId", "author_channel_id", default=_field(author, "channelId", "channel_id", default="")),
        MAX_MESSAGE_ID_CHARS,
    )
    delivery_ms = None
    if published_at is not None and received >= published_at:
        delivery_ms = (received - published_at) * 1000.0
    actionable = bool(text) and event_type not in {
        "message_deleted_event",
        "user_banned_event",
        "tombstone",
    }
    return ViewerEvent(
        message_id=message_id,
        event_type=event_type,
        viewer_name=viewer_name,
        text=text,
        published_at=published_at,
        received_at=received,
        delivery_ms=delivery_ms,
        live_chat_id=chat_id,
        author_channel_id=author_channel_id,
        actionable_candidate=actionable,
        message_id_origin=message_id_origin,
    )


@dataclass(frozen=True)
class IngestResult:
    """Bounded result from one response; safe to expose in a diagnostic probe."""

    history_count: int
    new_count: int
    duplicate_count: int
    invalid_count: int
    queued_actionable: int
    dropped_actionable: int
    next_page_token_present: bool
    polling_interval_ms: int | None
    new_events: tuple[ViewerEvent, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class YouTubeChatIngress:
    """Keep recent context and a tiny candidate queue behind a transport boundary."""

    def __init__(
        self,
        *,
        recent_limit: int = DEFAULT_RECENT_LIMIT,
        realtime_limit: int = DEFAULT_REALTIME_LIMIT,
        actionable_limit: int = DEFAULT_ACTIONABLE_LIMIT,
        actionable_ttl_seconds: float = DEFAULT_ACTIONABLE_TTL_SECONDS,
    ) -> None:
        self.recent_limit = max(1, int(recent_limit))
        self.realtime_limit = max(1, int(realtime_limit))
        self.actionable_limit = max(1, int(actionable_limit))
        self.actionable_ttl_seconds = max(0.0, float(actionable_ttl_seconds))
        self._recent: deque[ViewerEvent] = deque(maxlen=self.recent_limit)
        self._realtime: deque[ViewerEvent] = deque(maxlen=self.realtime_limit)
        self._actionable: deque[ViewerEvent] = deque(maxlen=self.actionable_limit)
        self._seen_order: deque[str] = deque(maxlen=max(300, self.recent_limit * 20))
        self._seen_ids: set[str] = set()
        self._lock = threading.RLock()
        self._connected = False
        self._next_page_token: str | None = None
        self._polling_interval_ms: int | None = None
        self._offline_at: str | None = None
        self._stats = {
            "responses": 0,
            "history": 0,
            "accepted": 0,
            "duplicate": 0,
            "invalid": 0,
            "actionable_queued": 0,
            "actionable_dropped": 0,
            "actionable_expired": 0,
        }

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    @property
    def next_page_token(self) -> str | None:
        """Cursor for a future transport reconnect; never included in snapshots."""

        with self._lock:
            return self._next_page_token

    def ingest_response(
        self,
        response: Any,
        *,
        received_at: float | None = None,
        live_chat_id: str | None = None,
        bootstrap: bool = False,
    ) -> IngestResult:
        """Ingest one response; bootstrap items become context, never actions."""

        with self._lock:
            return self._ingest_response(
                response,
                received_at=received_at,
                live_chat_id=live_chat_id,
                bootstrap=bootstrap,
            )

    def _ingest_response(
        self,
        response: Any,
        *,
        received_at: float | None = None,
        live_chat_id: str | None = None,
        bootstrap: bool = False,
    ) -> IngestResult:
        """Perform response ingestion while the caller holds ``_lock``."""

        now = time.time() if received_at is None else float(received_at)
        self._stats["responses"] += 1
        items = _field(response, "items", default=()) or ()
        if isinstance(items, (str, bytes)):
            items = ()
        next_token = _field(response, "nextPageToken", "next_page_token", default=None)
        if next_token:
            self._next_page_token = str(next_token)
        polling = _field(response, "pollingIntervalMillis", "polling_interval_millis", default=None)
        try:
            self._polling_interval_ms = int(polling) if polling is not None else self._polling_interval_ms
        except (TypeError, ValueError):
            pass
        offline_at = _field(response, "offlineAt", "offline_at", default=None)
        if offline_at:
            self._offline_at = str(offline_at)

        history_count = 0
        new_events: list[ViewerEvent] = []
        duplicate_count = 0
        invalid_count = 0
        queued_actionable = 0
        dropped_actionable = 0
        self._connected = True
        for raw_message in items:
            try:
                event = normalize_youtube_chat_message(
                    raw_message,
                    received_at=now,
                    live_chat_id=live_chat_id,
                )
            except Exception:
                invalid_count += 1
                self._stats["invalid"] += 1
                continue
            if event.message_id in self._seen_ids:
                duplicate_count += 1
                self._stats["duplicate"] += 1
                continue
            self._remember_id(event.message_id)
            self._recent.append(event)
            self._stats["accepted"] += 1
            if bootstrap:
                history_count += 1
                self._stats["history"] += 1
                continue
            new_events.append(event)
            self._realtime.append(event)
            if event.actionable_candidate:
                if self._enqueue_actionable(event, now=now):
                    queued_actionable += 1
                    self._stats["actionable_queued"] += 1
                else:
                    dropped_actionable += 1
                    self._stats["actionable_dropped"] += 1

        return IngestResult(
            history_count=history_count,
            new_count=len(new_events),
            duplicate_count=duplicate_count,
            invalid_count=invalid_count,
            queued_actionable=queued_actionable,
            dropped_actionable=dropped_actionable,
            next_page_token_present=bool(self._next_page_token),
            polling_interval_ms=self._polling_interval_ms,
            new_events=tuple(new_events),
        )

    def peek_actionable(self, *, now: float | None = None) -> ViewerEvent | None:
        with self._lock:
            self._expire_actionable(time.time() if now is None else float(now))
            return self._actionable[0] if self._actionable else None

    def pop_actionable(self, *, now: float | None = None) -> ViewerEvent | None:
        with self._lock:
            self._expire_actionable(time.time() if now is None else float(now))
            return self._actionable.popleft() if self._actionable else None

    def build_context(
        self,
        *,
        recent_limit: int = DEFAULT_RECENT_LIMIT,
        realtime_limit: int = 5,
    ) -> dict[str, Any]:
        """Return bounded context plus pending candidates, with no raw transport data."""

        with self._lock:
            self._expire_actionable(time.time())
            recent = list(self._recent)[-max(1, int(recent_limit)):]
            realtime = list(self._realtime)[-max(1, int(realtime_limit)):]
            return {
                "phase": PHASE,
                "source": "youtube_live_chat",
                "recent_messages": [event.to_dict() for event in recent],
                "realtime_messages": [event.to_dict() for event in realtime],
                "pending_actionable": [event.to_dict() for event in self._actionable],
                "recent_count": len(recent),
                "realtime_count": len(realtime),
                "pending_count": len(self._actionable),
                "next_page_token_present": bool(self._next_page_token),
                "polling_interval_ms": self._polling_interval_ms,
                "offline_at_present": bool(self._offline_at),
                "stats": dict(self._stats),
                "safety": {
                    "network_call": False,
                    "llm_call": False,
                    "tts_call": False,
                    "obs_call": False,
                    "memory_write": False,
                },
            }

    def snapshot(self) -> dict[str, Any]:
        context = self.build_context()
        return {
            **context,
            "connected": self._connected,
            "recent_limit": self.recent_limit,
            "realtime_limit": self.realtime_limit,
            "actionable_limit": self.actionable_limit,
            "actionable_ttl_seconds": self.actionable_ttl_seconds,
        }

    def _remember_id(self, message_id: str) -> None:
        if len(self._seen_order) >= self._seen_order.maxlen:
            expired = self._seen_order.popleft()
            self._seen_ids.discard(expired)
        self._seen_order.append(message_id)
        self._seen_ids.add(message_id)

    def _enqueue_actionable(self, event: ViewerEvent, *, now: float) -> bool:
        self._expire_actionable(now)
        if len(self._actionable) >= self.actionable_limit:
            return False
        self._actionable.append(event)
        return True

    def _expire_actionable(self, now: float) -> None:
        if self.actionable_ttl_seconds <= 0:
            return
        while self._actionable and now - self._actionable[0].received_at > self.actionable_ttl_seconds:
            self._actionable.popleft()
            self._stats["actionable_expired"] += 1


__all__ = [
    "DEFAULT_ACTIONABLE_LIMIT",
    "DEFAULT_ACTIONABLE_TTL_SECONDS",
    "DEFAULT_REALTIME_LIMIT",
    "DEFAULT_RECENT_LIMIT",
    "IngestResult",
    "PHASE",
    "ViewerEvent",
    "YouTubeChatIngress",
    "normalize_youtube_chat_message",
]
