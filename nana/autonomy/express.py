"""Autonomy Express: real VTS / TTS / subtitle / lipsync wiring.

Phase A4 (replaces express_stub.py):

    Backends (all callable, all optional):
        tts(text, on_done=None)        -> None
            Renders audio and plays it. on_done fires after playback
            ends (or after a failure). on_done must always be called
            exactly once if a callback was passed.

        vts(hotkey_id)                 -> None
            Triggers a VTube Studio hotkey. Never blocks. Returns
            immediately. Exceptions are caught and logged.

        subtitle(text)                 -> None
            Writes a single line to the public subtitle file.
            Best-effort. Exceptions are caught and logged.

        lipsync_stop()                 -> None
            CRITICAL: forces the lipsync mouth value to 0.0 so
            Nana stops moving her mouth. Called after every TTS
            (via on_done) and immediately for VTS-only payloads
            (no TTS = no on_done). Exceptions are caught.

    Micro reaction policy (locked in design):
        Pool:     ["爱心眼", "星星眼", "脸红"]    (eye only, no head/body)
        Prob:     25% per accepted expression with payload.vts=True
        Cooldown: 4s between two micro reactions
        Timing:   fires BEFORE TTS so the eye expression holds
                  while Nana speaks

    Ultra-short priority:
        When prefer_ultra_short is True, the inner_thought picker
        boosts ultra_short lines. This is a knob the user can flip
        via /autonomy-mode.

This module is pure logic: it does not import voice.engine,
integrations.vts, or any audio backend. The real backends are
registered by main.py. The smoke test registers mocks.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from nana.autonomy.inner_thought import Thought


# Locked numbers from A4 design.
MICRO_REACTION_POOL = ("爱心眼", "星星眼", "脸红")
MICRO_REACTION_PROBABILITY = 0.25
MICRO_REACTION_COOLDOWN_S = 4.0

# The default mode is ultra_short priority ON. Slash command flips it.
DEFAULT_PREFER_ULTRA_SHORT = True


@dataclass
class ExpressTrace:
    """A record of what one emit() actually did. Used by the loop's
    log and by the smoke for assertions.
    """

    t_monotonic: float
    mode: str
    level: str
    payload: dict
    text: str
    # What fired, in order:
    micro_reaction: Optional[str] = None
    micro_reaction_t: Optional[float] = None
    subtitle_written: bool = False
    tts_started_t: Optional[float] = None
    tts_finished_t: Optional[float] = None
    lipsync_stop_t: Optional[float] = None
    # Whether each backend raised (we still proceed).
    errors: list = field(default_factory=list)


class AutonomyExpress:
    """Owns the output wiring. Backends are pluggable callables.

    The instance is safe to use from multiple threads, although the
    autonomy loop calls it from a single background thread.
    """

    def __init__(self, clock=None, rng=None):
        self._clock = clock or time.monotonic
        self._rng = rng or random.Random()
        self._lock = threading.Lock()

        # Backends default to no-op. main.py replaces them.
        self._tts: Optional[Callable] = None
        self._vts: Optional[Callable] = None
        self._subtitle: Optional[Callable] = None
        self._lipsync_stop: Optional[Callable] = None

        # Last micro reaction timestamp (for cooldown).
        self._last_micro_t: Optional[float] = None
        # For the smoke to read.
        self.last_trace: Optional[ExpressTrace] = None
        self.prefer_ultra_short: bool = DEFAULT_PREFER_ULTRA_SHORT
        # Smoke-only knob: when True, _maybe_micro_reaction always
        # returns a hotkey (no probability check). Real wire (main.py)
        # leaves this False.
        self._force_micro_reaction: bool = False

    def set_force_micro_reaction(self, value: bool) -> None:
        self._force_micro_reaction = bool(value)

    # ---- backend registration ---------------------------------------

    def set_tts(self, fn: Optional[Callable]) -> None:
        with self._lock:
            self._tts = fn

    def set_vts(self, fn: Optional[Callable]) -> None:
        with self._lock:
            self._vts = fn

    def set_subtitle(self, fn: Optional[Callable]) -> None:
        with self._lock:
            self._subtitle = fn

    def set_lipsync_stop(self, fn: Optional[Callable]) -> None:
        with self._lock:
            self._lipsync_stop = fn

    # ---- ultra-short priority ---------------------------------------

    def set_prefer_ultra_short(self, value: bool) -> None:
        self.prefer_ultra_short = bool(value)

    # ---- the main emit() -------------------------------------------

    def emit(self, mode: str, level: str, payload: dict, thought: Thought) -> ExpressTrace:
        """Fire the thought. Returns a trace describing what happened.

        Order of operations:
            1. VTS micro reaction (subject to probability and cooldown)
               Fires BEFORE TTS so the eye expression "leads" speech.
            2. Subtitle (if payload.subtitle)
            3. TTS (if payload.tts) with on_done = lipsync_stop
            4. If VTS-only (no TTS), call lipsync_stop immediately
               to ensure mouth=0.0 with no TTS on_done to call it.

        All backend exceptions are caught; the loop must never crash
        because a backend misbehaved.
        """
        t0 = self._clock()
        trace = ExpressTrace(
            t_monotonic=t0,
            mode=mode,
            level=level,
            payload=dict(payload),
            text=thought.text,
        )

        # Step 1: micro reaction (if vts is on, with probability and cooldown).
        if payload.get("vts"):
            hotkey = self._maybe_micro_reaction(t0)
            if hotkey is not None:
                trace.micro_reaction = hotkey
                trace.micro_reaction_t = self._clock()
                self._safe_call(self._vts, hotkey, trace=trace, label=f"vts:{hotkey}")
                with self._lock:
                    self._last_micro_t = t0

        # Step 2: subtitle.
        if payload.get("subtitle"):
            self._safe_call(self._subtitle, thought.text, trace=trace, label="subtitle")
            trace.subtitle_written = True

        # Step 3: TTS.
        if payload.get("tts"):
            tts_started = self._clock()
            trace.tts_started_t = tts_started
            on_done = self._make_tts_on_done(trace)
            self._safe_call(
                self._tts, thought.text, trace=trace, label="tts",
                kwargs={"on_done": on_done},
            )
            # If TTS backend did NOT call on_done synchronously, we
            # cannot know when lipsync actually stops. That is the
            # real backend's job; the mock backend in smoke fires
            # on_done. We do not block here.
        else:
            # Step 4: VTS-only. Ensure lipsync stops immediately.
            # This is the safety net against mấp máy môi vô nghĩa
            # even when there is no TTS to drive on_done.
            self._safe_call(self._lipsync_stop, trace=trace, label="lipsync_stop_vts_only")
            trace.lipsync_stop_t = self._clock()

        with self._lock:
            self.last_trace = trace
        return trace

    # ---- micro reaction policy --------------------------------------

    def _maybe_micro_reaction(self, now: float) -> Optional[str]:
        """Apply cooldown + probability, then pick a hotkey.

        Cooldown is enforced here so that tests can override
        _pick_micro_reaction to force a pick without bypassing the
        cooldown.
        """
        with self._lock:
            if self._last_micro_t is not None:
                if now - self._last_micro_t < MICRO_REACTION_COOLDOWN_S:
                    return None
        if not self._force_micro_reaction:
            if self._rng.random() >= MICRO_REACTION_PROBABILITY:
                return None
        return self._pick_micro_reaction()

    def _pick_micro_reaction(self) -> str:
        """Pick a hotkey from the pool. Cooldown has already been
        verified by _maybe_micro_reaction().
        """
        return self._rng.choice(MICRO_REACTION_POOL)

    # ---- helpers ----------------------------------------------------

    def _make_tts_on_done(self, trace: ExpressTrace) -> Callable:
        def _on_done():
            trace.tts_finished_t = self._clock()
            self._safe_call(self._lipsync_stop, trace=trace, label="lipsync_stop_tts_done")
            trace.lipsync_stop_t = self._clock()
        return _on_done

    def _safe_call(self, fn, *args, trace: ExpressTrace, label: str, kwargs: Optional[dict] = None):
        if fn is None:
            return
        try:
            if kwargs:
                fn(*args, **kwargs)
            else:
                fn(*args)
        except Exception as exc:
            trace.errors.append({"where": label, "error": str(exc)})


__all__ = [
    "AutonomyExpress",
    "ExpressTrace",
    "MICRO_REACTION_POOL",
    "MICRO_REACTION_PROBABILITY",
    "MICRO_REACTION_COOLDOWN_S",
    "DEFAULT_PREFER_ULTRA_SHORT",
]
