"""Camera-agnostic human-presence gate for a Nana Presence Node."""

from __future__ import annotations

import threading
import time
from typing import Callable


class PresenceHumanGate:
    """Turn face detections into a stable interactive/display-only policy."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        confirmations_required: int = 2,
        minimum_confidence: float = 0.55,
        absence_grace_seconds: float = 10.0,
        conversation_latch_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if confirmations_required < 1:
            raise ValueError("confirmations_required must be at least 1")
        if not 0.0 <= minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be between 0 and 1")
        if absence_grace_seconds < 0 or conversation_latch_seconds < 0:
            raise ValueError("presence timing values cannot be negative")

        self._enabled = bool(enabled)
        self.confirmations_required = int(confirmations_required)
        self.minimum_confidence = float(minimum_confidence)
        self.absence_grace_seconds = float(absence_grace_seconds)
        self.conversation_latch_seconds = float(conversation_latch_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._positive_streak = 0
        self._confirmed = False
        self._last_seen_at: float | None = None
        self._last_observation_positive = False
        self._conversation_until = 0.0

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    def reset(self, *, enabled: bool | None = None) -> None:
        """Clear stale evidence while preserving an explicit gate policy."""
        with self._lock:
            if enabled is not None:
                self._enabled = bool(enabled)
            self._positive_streak = 0
            self._confirmed = False
            self._last_seen_at = None
            self._last_observation_positive = False
            self._conversation_until = 0.0

    def observe(
        self,
        person_detected: bool,
        *,
        confidence: float = 1.0,
        at: float | None = None,
    ) -> dict[str, object]:
        now = self._clock() if at is None else float(at)
        positive = bool(person_detected) and confidence >= self.minimum_confidence
        with self._lock:
            if (
                self._last_seen_at is not None
                and now - self._last_seen_at > self.absence_grace_seconds
            ):
                self._confirmed = False
                self._positive_streak = 0

            self._last_observation_positive = positive
            if positive:
                self._positive_streak += 1
                self._last_seen_at = now
                if self._positive_streak >= self.confirmations_required:
                    self._confirmed = True
            else:
                self._positive_streak = 0
            return self._snapshot_locked(now)

    def note_interaction(self, *, at: float | None = None) -> None:
        now = self._clock() if at is None else float(at)
        with self._lock:
            if not self._enabled or self._allows_audio_locked(now):
                self._conversation_until = max(
                    self._conversation_until,
                    now + self.conversation_latch_seconds,
                )

    def allows_audio(self, *, at: float | None = None) -> bool:
        now = self._clock() if at is None else float(at)
        with self._lock:
            return self._allows_audio_locked(now)

    def snapshot(self, *, at: float | None = None) -> dict[str, object]:
        now = self._clock() if at is None else float(at)
        with self._lock:
            return self._snapshot_locked(now)

    def _allows_audio_locked(self, now: float) -> bool:
        if not self._enabled:
            return True
        fresh_presence = (
            self._confirmed
            and self._last_seen_at is not None
            and now - self._last_seen_at <= self.absence_grace_seconds
        )
        return fresh_presence or now < self._conversation_until

    def _snapshot_locked(self, now: float) -> dict[str, object]:
        allowed = self._allows_audio_locked(now)
        last_seen_age = (
            max(0.0, now - self._last_seen_at)
            if self._last_seen_at is not None
            else None
        )
        if not self._enabled:
            state = "disabled"
        elif (
            self._confirmed
            and last_seen_age is not None
            and last_seen_age <= self.absence_grace_seconds
        ):
            state = (
                "present"
                if self._last_observation_positive
                else "absence_grace"
            )
        elif now < self._conversation_until:
            state = "conversation_latch"
        elif self._positive_streak:
            state = "candidate"
        else:
            state = "absent"

        return {
            "enabled": self._enabled,
            "state": state,
            "audio_allowed": allowed,
            "interaction_mode": "interactive" if allowed else "display_only",
            "positive_streak": self._positive_streak,
            "confirmations_required": self.confirmations_required,
            "minimum_confidence": self.minimum_confidence,
            "last_seen_age_s": last_seen_age,
            "absence_grace_seconds": self.absence_grace_seconds,
            "conversation_latch_remaining_s": max(
                0.0, self._conversation_until - now
            ),
        }


PRESENCE_HUMAN_GATE = PresenceHumanGate()
