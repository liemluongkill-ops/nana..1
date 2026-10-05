"""Session-owned public visual state derived from confirmed audio sink writes.

The store retains one bounded current value. It never retains PCM, reply text,
or a signal backlog, and it is independent from the generic avatar mouth feed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time
from typing import Any, Callable


PROTOCOL_NAME = "nana.public-visual.v1"
STALE_AFTER_MS = 180
EXPRESSION_STALE_AFTER_MS = 5000
MAX_ID_CHARS = 256
PUBLIC_VISEMES = frozenset({"aa", "ih", "ee", "oh", "ou", "nn", "sil"})
PUBLIC_EXPRESSION_ACTIONS = frozenset({
    "blink",
    "cat_teary_smile",
    "curious",
    "happy",
    "idle",
    "listen",
    "nod",
    "playful",
    "playful_wink",
    "serious_think",
    "settle",
    "shy",
    "shy_crying",
    "surprised",
    "surprised_pout",
    "think",
    "wink_soft_smile",
})
TERMINAL_REASONS = frozenset({"completed", "cancelled", "failed", "shutdown"})


def _canonical_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if len(value) > MAX_ID_CHARS or "\x1f" in value:
        return None
    return value


def _normalized(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        return None
    return round(parsed, 6)


@dataclass(frozen=True)
class PublicVisualPlaybackIdentity:
    stream_session_id: str
    playback_id: str
    attempt_id: str
    event_id: str

    def __post_init__(self) -> None:
        if any(
            _canonical_id(value) is None
            for value in (
                self.stream_session_id,
                self.playback_id,
                self.attempt_id,
                self.event_id,
            )
        ):
            raise ValueError("invalid_public_visual_identity")


def identity_from_playback_request(request: Any) -> PublicVisualPlaybackIdentity:
    """Project the four public identifiers and nothing else from a CUM5 request."""

    return PublicVisualPlaybackIdentity(
        request.artifact.scope.stream_session_id,
        request.playback_id,
        request.generated_record.attempt_id,
        request.artifact.scope.event_id,
    )


class PublicVisualSignalStore:
    """Keep only the latest signal for one active public stream session."""

    def __init__(
        self,
        active_session_id: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        canonical_session = _canonical_id(active_session_id)
        if canonical_session is None:
            raise ValueError("invalid_public_visual_session")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._active_session_id = canonical_session
        self._clock = clock
        self._lock = threading.RLock()
        self._cursor = 0
        self._active_identity: PublicVisualPlaybackIdentity | None = None
        self._sequence = 0
        self._current: dict[str, Any] | None = None
        self._expression_action: str | None = None
        self._expression_started_at: float | None = None
        self._terminal = False
        self._shutdown = False

    @property
    def active_session_id(self) -> str:
        with self._lock:
            return self._active_session_id

    def _now(self) -> float | None:
        try:
            value = float(self._clock())
        except Exception:
            return None
        return value if math.isfinite(value) else None

    def begin_playback(
        self,
        identity: PublicVisualPlaybackIdentity,
        *,
        expression_action: str | None = None,
    ) -> bool:
        if not isinstance(identity, PublicVisualPlaybackIdentity):
            return False
        action = str(expression_action or "").strip().lower() or None
        if action is not None and action not in PUBLIC_EXPRESSION_ACTIONS:
            return False
        with self._lock:
            if (
                self._shutdown
                or identity.stream_session_id != self._active_session_id
                or identity == self._active_identity
            ):
                return False
            self._active_identity = identity
            self._sequence = 0
            self._current = None
            self._expression_action = action
            self._expression_started_at = None
            self._terminal = False
            self._cursor += 1
            return True

    def publish_frame(
        self,
        identity: PublicVisualPlaybackIdentity,
        *,
        sequence: int,
        open_value: float,
        energy: float,
        viseme: str,
        speaking: bool,
    ) -> bool:
        normalized_open = _normalized(open_value)
        normalized_energy = _normalized(energy)
        normalized_viseme = str(viseme or "").strip().lower()
        if (
            not isinstance(identity, PublicVisualPlaybackIdentity)
            or type(sequence) is not int
            or sequence < 1
            or normalized_open is None
            or normalized_energy is None
            or normalized_viseme not in PUBLIC_VISEMES
            or type(speaking) is not bool
        ):
            return False
        if speaking:
            if normalized_viseme == "sil":
                return False
        else:
            normalized_open = 0.0
            normalized_energy = 0.0
            normalized_viseme = "sil"
        now = self._now()
        if now is None:
            return False
        with self._lock:
            if (
                self._shutdown
                or self._terminal
                or identity != self._active_identity
                or identity.stream_session_id != self._active_session_id
                or sequence != self._sequence + 1
            ):
                return False
            if self._expression_action is not None and self._expression_started_at is None:
                self._expression_started_at = now
            self._sequence = sequence
            self._cursor += 1
            self._current = {
                "identity": identity,
                "sequence": sequence,
                "open": normalized_open,
                "energy": normalized_energy,
                "viseme": normalized_viseme,
                "speaking": speaking,
                "produced_at": now,
                "terminal_reason": None,
            }
            return True

    def terminal(self, identity: PublicVisualPlaybackIdentity, reason: str) -> bool:
        normalized_reason = str(reason or "").strip().lower()
        if (
            not isinstance(identity, PublicVisualPlaybackIdentity)
            or normalized_reason not in TERMINAL_REASONS
        ):
            return False
        now = self._now()
        if now is None:
            return False
        with self._lock:
            if self._terminal or identity != self._active_identity:
                return False
            self._sequence += 1
            self._cursor += 1
            self._terminal = True
            self._expression_action = None
            self._expression_started_at = None
            self._current = {
                "identity": identity,
                "sequence": self._sequence,
                "open": 0.0,
                "energy": 0.0,
                "viseme": "sil",
                "speaking": False,
                "produced_at": now,
                "terminal_reason": normalized_reason,
            }
            return True

    def change_session(self, active_session_id: str) -> bool:
        canonical_session = _canonical_id(active_session_id)
        if canonical_session is None:
            return False
        with self._lock:
            if self._shutdown or canonical_session == self._active_session_id:
                return False
            self._active_session_id = canonical_session
            self._active_identity = None
            self._sequence = 0
            self._current = None
            self._expression_action = None
            self._expression_started_at = None
            self._terminal = False
            self._cursor += 1
            return True

    def shutdown(self) -> bool:
        now = self._now()
        with self._lock:
            if self._shutdown:
                return False
            identity = self._active_identity
            self._cursor += 1
            if identity is not None and now is not None:
                self._sequence += 1
                self._terminal = True
                self._current = {
                    "identity": identity,
                    "sequence": self._sequence,
                    "open": 0.0,
                    "energy": 0.0,
                    "viseme": "sil",
                    "speaking": False,
                    "produced_at": now,
                    "terminal_reason": "shutdown",
                }
            else:
                self._current = None
            self._expression_action = None
            self._expression_started_at = None
            self._shutdown = True
            return True

    def snapshot(self, after: int = 0) -> dict[str, Any]:
        if type(after) is not int or after < 0:
            raise ValueError("after_must_be_nonnegative_integer")
        now = self._now()
        with self._lock:
            cursor = self._cursor
            current = dict(self._current) if self._current is not None else None
            action = self._expression_action
            expression_started_at = self._expression_started_at
        payload: dict[str, Any] = {
            "ok": True,
            "protocol": PROTOCOL_NAME,
            "cursor": cursor,
            "stale_after_ms": STALE_AFTER_MS,
            "current": None,
        }
        if current is None or cursor == after or now is None:
            return payload
        produced_at = float(current["produced_at"])
        if now < produced_at:
            return payload
        age_ms = max(0, int(round((now - produced_at) * 1000.0)))
        if age_ms >= STALE_AFTER_MS:
            return payload
        identity = current["identity"]
        expression = None
        if action is not None and expression_started_at is not None and now >= expression_started_at:
            expression_age_ms = max(
                0,
                int(round((now - expression_started_at) * 1000.0)),
            )
            expression_expires = max(0, EXPRESSION_STALE_AFTER_MS - expression_age_ms)
            if expression_expires > 0:
                expression = {
                    "event_id": identity.event_id,
                    "action": action,
                    "expires_in_ms": expression_expires,
                }
        payload["current"] = {
            "stream_session_id": identity.stream_session_id,
            "playback_id": identity.playback_id,
            "attempt_id": identity.attempt_id,
            "event_id": identity.event_id,
            "sequence": current["sequence"],
            "open": current["open"],
            "energy": current["energy"],
            "viseme": current["viseme"],
            "speaking": current["speaking"],
            "age_ms": age_ms,
            "expires_in_ms": max(0, STALE_AFTER_MS - age_ms),
            "expression": expression,
            "terminal_reason": current["terminal_reason"],
        }
        return payload


__all__ = [
    "EXPRESSION_STALE_AFTER_MS",
    "MAX_ID_CHARS",
    "PROTOCOL_NAME",
    "PUBLIC_EXPRESSION_ACTIONS",
    "PUBLIC_VISEMES",
    "PublicVisualPlaybackIdentity",
    "PublicVisualSignalStore",
    "STALE_AFTER_MS",
    "TERMINAL_REASONS",
    "identity_from_playback_request",
]
