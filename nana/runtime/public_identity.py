"""Transport-neutral identity normalization for the public lane.

Adapters may pass dictionaries or small request objects.  This module is the
single place where those transport values become a stable, platform-scoped
viewer identity; session identifiers are deliberately excluded from the actor
key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CanonicalPublicIdentity:
    platform: str
    author_id: str
    actor_key: str


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


def _clean(value: Any, fallback: str) -> str:
    text = "" if value is None else str(value).strip()
    return text or fallback


def normalize_adapter_identity(raw: Any) -> CanonicalPublicIdentity:
    """Normalize a platform adapter payload into a canonical public identity.

    Missing stable author IDs become event-local anonymous IDs.  They are
    intentionally explicit so later persistence code cannot mistake them for
    durable viewer identities.
    """

    platform = _clean(_value(raw, "platform", "source", "provider"), "public_chat").lower()
    event_id = _clean(_value(raw, "event_id", "eventId", "message_id", "request_id"), "unknown-event")
    author = _value(raw, "author_id", "authorId", "author_channel_id", "user_id", "userId")
    author_id = _clean(author, f"anonymous:{event_id}")
    actor_key = f"{platform}:{author_id}"
    return CanonicalPublicIdentity(platform=platform, author_id=author_id, actor_key=actor_key)


__all__ = ["CanonicalPublicIdentity", "normalize_adapter_identity"]
