"""Cadence: timing, jitter, burst control, backoff.

Locked rules from NANA_AUTONOMY_DESIGN.md:

    Default cycle period: 5-15s, picked uniformly, jittered +/-30%
    Global burst floor: 8s between any two expressions
    Per-mode burst floor: 20s between two expressions of the same mode
    Cooldown after user command: 10s before any autonomous output
    Backoff on gate rejection: double the wait, cap 60s, reset on accept

This module is deterministic when given a seeded RNG so smoke tests
can assert the exact timing pattern.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


# Locked numbers from the design doc. Do not mutate in Phase A2.
DEFAULT_PERIOD_MIN_S = 5.0
DEFAULT_PERIOD_MAX_S = 15.0
PERIOD_JITTER = 0.30  # +/- 30% of the picked period

GLOBAL_BURST_FLOOR_S = 8.0
PER_MODE_BURST_FLOOR_S = 20.0
COMMAND_COOLDOWN_S = 10.0

BACKOFF_INITIAL_S = 5.0
BACKOFF_MULTIPLIER = 2.0
BACKOFF_CAP_S = 60.0


@dataclass
class CadenceEvent:
    """A single accepted or rejected expression event for telemetry."""

    t_monotonic: float
    mode: Optional[str]
    accepted: bool
    reason: str
    wait_before_s: float
    next_period_s: float


@dataclass
class CadenceState:
    """Mutable state owned by the scheduler."""

    last_expression_t: Optional[float] = None
    last_mode: Optional[str] = None
    last_mode_t: Optional[float] = None
    last_command_t: Optional[float] = None
    current_period_s: float = DEFAULT_PERIOD_MIN_S
    events: list = field(default_factory=list)
    # Backoff chain: doubles after each reject, resets on accept.
    consecutive_rejects: int = 0
    paused: bool = False


class CadenceScheduler:
    """Decides when the next expression attempt is allowed and how long
    the loop should sleep before the next tick.

    Two public methods:

        can_express(mode, now) -> (allowed: bool, reason: str)
            Pure check; no side effects on state. Used by the loop
            before calling the gate.

        next_period_s() -> float
            Returns how long the loop should sleep now. Called after
            can_express returns True (i.e. before actually trying to
            express). Pulls from the jittered period range and folds
            in any backoff.
    """

    def __init__(self, clock=None, rng=None):
        # clock(now) -> float: monotonic seconds. Mocked in smoke.
        self._clock = clock or time.monotonic
        # rng: random.Random instance. Seeded in smoke for determinism.
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._state = CadenceState()

    # ---- public API --------------------------------------------------

    def can_express(self, mode: str, now: Optional[float] = None) -> tuple:
        """Return (allowed, reason).

        reason is one of:
            ok
            paused
            cooldown_after_command
            burst_global_floor
            burst_per_mode_floor
        """
        with self._lock:
            if self._state.paused:
                return False, "paused"
            t = self._clock() if now is None else now

            # Cooldown after user command: must wait >= COMMAND_COOLDOWN_S
            if self._state.last_command_t is not None:
                if t - self._state.last_command_t < COMMAND_COOLDOWN_S:
                    return False, "cooldown_after_command"

            # No expression yet, fresh start is always allowed.
            if self._state.last_expression_t is None:
                return True, "ok"

            # Global burst floor: any two expressions must be 8s+ apart.
            if t - self._state.last_expression_t < GLOBAL_BURST_FLOOR_S:
                return False, "burst_global_floor"

            # Per-mode burst floor: same mode twice must be 20s+ apart.
            if (
                self._state.last_mode == mode
                and self._state.last_mode_t is not None
                and t - self._state.last_mode_t < PER_MODE_BURST_FLOOR_S
            ):
                return False, "burst_per_mode_floor"

            return True, "ok"

    def next_period_s(self) -> float:
        """Return how long the loop should sleep. Uses current backoff
        state to scale the picked period.
        """
        with self._lock:
            if self._state.consecutive_rejects > 0:
                backoff = min(
                    BACKOFF_INITIAL_S * (BACKOFF_MULTIPLIER ** (self._state.consecutive_rejects - 1)),
                    BACKOFF_CAP_S,
                )
                # Use backoff as a minimum and jitter from there.
                base = max(backoff, DEFAULT_PERIOD_MIN_S)
            else:
                base = self._state.current_period_s
            period = self._rng.uniform(
                DEFAULT_PERIOD_MIN_S * (1 - PERIOD_JITTER),
                DEFAULT_PERIOD_MAX_S * (1 + PERIOD_JITTER),
            )
            # Convex combination: 70% picked, 30% backoff/previous.
            blended = 0.7 * period + 0.3 * base
            self._state.current_period_s = blended
            return blended

    def record_expression(self, mode: str, accepted: bool, reason: str = "") -> None:
        """Record the outcome of an attempted expression. Updates state
        for future can_express checks and backoff chain.
        """
        with self._lock:
            now = self._clock()
            self._state.events.append(
                CadenceEvent(
                    t_monotonic=now,
                    mode=mode,
                    accepted=accepted,
                    reason=reason,
                    wait_before_s=self._state.current_period_s,
                    next_period_s=self._state.current_period_s,
                )
            )
            if accepted:
                self._state.last_expression_t = now
                self._state.last_mode = mode
                self._state.last_mode_t = now
                self._state.consecutive_rejects = 0
            else:
                self._state.consecutive_rejects += 1

    def note_user_command(self) -> None:
        """Mark that the user just sent a command. Starts the 10s
        cooldown.
        """
        with self._lock:
            self._state.last_command_t = self._clock()

    def pause(self) -> None:
        with self._lock:
            self._state.paused = True

    def resume(self) -> None:
        with self._lock:
            self._state.paused = False

    # ---- introspection for smoke ------------------------------------

    @property
    def events(self):
        with self._lock:
            return list(self._state.events)

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._state.paused

    @property
    def state(self) -> CadenceState:
        with self._lock:
            return self._state
