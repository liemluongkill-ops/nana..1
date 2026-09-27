"""Receipt-driven public output state; no I/O, playback, or durable facts."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Literal, Mapping

from nana.runtime.public_context_boundary import PublicEventScope


TRANSITIONS = {
    "generated": frozenset({"published", "interrupted"}),
    "published": frozenset({"playback_started", "interrupted"}),
    "playback_started": frozenset({"delivered", "interrupted"}),
    "delivered": frozenset(),
    "interrupted": frozenset(),
}
DELIVERY_STATES = tuple(TRANSITIONS)
VOICE_TRANSITIONS = {
    "generated": frozenset({"playback_started", "interrupted"}),
    "playback_started": frozenset({"delivered", "interrupted"}),
    "delivered": frozenset(),
    "interrupted": frozenset(),
}
_MODE_TRANSITIONS = {
    "published_then_voice": TRANSITIONS,
    "voice_only": VOICE_TRANSITIONS,
}


@dataclass(frozen=True)
class PublicDeliveryRecord:
    event_id: str
    output_id: str
    state: str
    attempt_id: str
    revision: int
    text_preview: str
    scope: PublicEventScope
    updated_at: float
    delivery_mode: Literal["published_then_voice", "voice_only"] = "published_then_voice"

    def __post_init__(self) -> None:
        if not isinstance(self.delivery_mode, str) or self.delivery_mode not in _MODE_TRANSITIONS:
            raise ValueError("unknown delivery mode")
        if self.state not in _MODE_TRANSITIONS[self.delivery_mode]:
            raise ValueError("unknown delivery state")
        if not all(isinstance(v, str) and v for v in (self.event_id, self.output_id, self.attempt_id)):
            raise ValueError("event, output, and attempt IDs are required")
        if self.event_id != self.scope.event_id:
            raise ValueError("delivery event does not match scope")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("delivery revision must be a nonnegative integer")
        if not isinstance(self.updated_at, (int, float)) or not math.isfinite(self.updated_at):
            raise ValueError("finite delivery timestamp required")

    @property
    def key(self) -> tuple[str, str, str, str, str, str]:
        return (self.scope.platform, self.scope.room_id, self.scope.stream_session_id,
                self.event_id, self.output_id, self.attempt_id)


def transition_delivery(
    record: PublicDeliveryRecord, target_state: str, revision: int,
    attempt_id: str, receipt: Mapping[str, Any] | None,
) -> PublicDeliveryRecord:
    """Keep the current immutable record on missing, stale, or invalid evidence.

    The adapter supplies a correlated receipt; time elapsed, generation EOF,
    queue acceptance, and an avatar motion ACK are not speech completion.
    A retry cannot be expressed as a transition of the old attempt.
    """
    if (type(revision) is not int or revision <= record.revision
            or attempt_id != record.attempt_id
            or target_state not in _MODE_TRANSITIONS[record.delivery_mode][record.state]):
        return record
    if not isinstance(receipt, Mapping):
        return record
    # Old callers omit the mode only for the original publication-led path.
    if receipt.get("delivery_mode", "published_then_voice") != record.delivery_mode:
        return record
    expected = {
        "event_id": record.event_id, "output_id": record.output_id,
        "attempt_id": record.attempt_id, "state": target_state,
        "revision": revision, "platform": record.scope.platform,
        "room_id": record.scope.room_id,
        "stream_session_id": record.scope.stream_session_id,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        return record
    timestamp = receipt.get("timestamp", record.updated_at)
    if (not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool)
            or not math.isfinite(timestamp) or timestamp < record.updated_at):
        return record
    return replace(record, state=target_state, revision=revision, updated_at=float(timestamp))


def start_delivery_attempt(
    prior: PublicDeliveryRecord, *, attempt_id: str, output_id: str,
    revision: int, timestamp: float,
) -> PublicDeliveryRecord:
    """Explicitly start a separate retry; callers retain the original record."""
    if prior.state not in {"delivered", "interrupted"}:
        raise ValueError("prior attempt must be terminal before retry")
    if attempt_id == prior.attempt_id or output_id == prior.output_id:
        raise ValueError("retry requires new attempt and output IDs")
    if type(revision) is not int or revision <= prior.revision:
        raise ValueError("retry revision must advance")
    if timestamp < prior.updated_at:
        raise ValueError("retry timestamp must not roll back")
    return PublicDeliveryRecord(prior.event_id, output_id, "generated", attempt_id,
                                revision, prior.text_preview, prior.scope, timestamp,
                                delivery_mode=prior.delivery_mode)
