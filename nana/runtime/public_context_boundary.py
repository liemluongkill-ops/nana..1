"""Pure lane-first public context boundary.

The projection in this module accepts only caller-provided public metadata and
request text.  It never calls context, browser, awareness, memory, provider,
or action helpers, which keeps the public lane safe even when a caller's
request context contains private hooks as tripwires.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nana.runtime.public_identity import CanonicalPublicIdentity, normalize_adapter_identity


@dataclass(frozen=True)
class PublicEventScope:
    platform: str
    room_id: str
    stream_session_id: str
    event_id: str
    display_name: str
    identity: CanonicalPublicIdentity

    @property
    def durable_viewer_key(self) -> str:
        return self.identity.actor_key

    @property
    def session_key(self) -> str:
        return f"{self.platform}:{self.room_id}:{self.stream_session_id}"


_PUBLIC_METADATA_KEYS = (
    "platform",
    "room_id",
    "channel_id",
    "stream_session_id",
    "event_id",
    "author_id",
    "display_name",
    "public_text",
    "public_metadata",
    "topic",
    # Consent is a public boolean control, not viewer identity or memory text.
    # Keep it in the lane-safe projection so Phase 2 can require an explicit
    # opt-in without reopening private context.
    "memory_consent",
    "public_memory_consent",
)


def _value(raw: Any, *names: str) -> Any:
    if isinstance(raw, dict):
        for name in names:
            if name in raw:
                return raw[name]
        return None
    for name in names:
        try:
            value = getattr(raw, name)
        except AttributeError:
            continue
        if value is not None:
            return value
    return None


def _text(value: Any, fallback: str = "") -> str:
    text = "" if value is None else str(value).strip()
    return text or fallback


def _public_metadata(request_context: Any) -> dict[str, Any]:
    """Copy only explicitly public metadata; never inspect private callables."""

    if not isinstance(request_context, dict):
        return {}
    source = request_context
    result: dict[str, Any] = {}
    for key in _PUBLIC_METADATA_KEYS:
        if key not in source:
            continue
        value = source[key]
        if key == "public_metadata":
            if isinstance(value, dict):
                result[key] = {
                    str(meta_key): meta_value
                    for meta_key, meta_value in value.items()
                    if str(meta_key) in {"topic", "room", "channel", "language"}
                    and (isinstance(meta_value, (str, int, float, bool)) or meta_value is None)
                }
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
    return result


def _scope_from_metadata(viewer_name: str | None, platform: str | None, metadata: Any) -> PublicEventScope:
    source = metadata if isinstance(metadata, dict) else {}
    merged = dict(source)
    merged.setdefault("platform", platform)
    merged.setdefault("display_name", viewer_name)
    identity = normalize_adapter_identity(merged)
    room_id = _text(_value(merged, "room_id", "channel_id"), "public")
    stream_session_id = _text(_value(merged, "stream_session_id", "session_id"), "legacy")
    event_id = _text(_value(merged, "event_id", "eventId", "message_id", "request_id"), "event")
    display_name = _text(_value(merged, "display_name", "viewer_name", "author_name"), "viewer")
    return PublicEventScope(
        platform=identity.platform,
        room_id=room_id,
        stream_session_id=stream_session_id,
        event_id=event_id,
        display_name=display_name,
        identity=identity,
    )


def build_public_safe_snapshot(request_context: Any, request_text: Any, scope: PublicEventScope) -> dict[str, Any]:
    """Project caller-provided public inputs into a lane-safe context object."""

    metadata = _public_metadata(request_context)
    public_context = metadata.get("public_metadata") if isinstance(metadata.get("public_metadata"), dict) else {}
    # Keep this shape useful to existing prompt assembly while ensuring every
    # value originated in the public request or immutable event scope.
    return {
        "lane": "public_viewer",
        "platform": scope.platform,
        "room_id": scope.room_id,
        "stream_session_id": scope.stream_session_id,
        "event_id": scope.event_id,
        "display_name": scope.display_name,
        "author_id": scope.identity.author_id,
        "actor_key": scope.identity.actor_key,
        "memory_consent": bool(
            metadata.get("memory_consent") is True
            or metadata.get("public_memory_consent") is True
        ),
        "public_metadata": public_context,
        "public_text": _text(metadata.get("public_text"), _text(request_text)),
        "active_zone": "public_stage",
        "active_app": "public_chat",
        "idle_state": "active",
        "browser": {},
        "time": {},
    }


__all__ = ["PublicEventScope", "build_public_safe_snapshot"]
