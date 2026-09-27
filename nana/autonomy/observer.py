"""Phase A5: Real Observer — replaces observer_stub.py.

Replaces the stub state with real reads from the runtime context
snapshot (nana/runtime/context.py). Every field in GateContext maps
directly to a runtime signal:

    user_is_typing     -> keyboard.hook (live, no polling)
    game_active        -> zone == "game" OR process watch
    command_in_flight  -> main loop's internal busy flag
    audio_busy         -> voice.snapshot()["speaking"]
    mood_affection     -> emotion vector from memory
    scene_relevance    -> attention window evaluation
    silence_duration_s -> time since last chat (mark_chat_time)
    silence_window_s   -> normalization window (60s, fixed)
    jitter_value       -> rng.random() per tick
    web_context        -> browser snapshot (Phase A7)

No I/O, no LLM calls, no VTS. Pure signal extraction.
"""

from __future__ import annotations

import random
import time
from typing import Callable, Optional

from nana.runtime.attention import evaluate_attention_window
from nana.runtime.context import context_snapshot
from nana.autonomy.expression_gate import GateContext


# How long to consider a chat "recent" for silence purposes.
_SILENCE_WINDOW_S = 60.0


class RealObserver:
    """Reads real signals from the runtime and exposes get_context().

    Thread-safe: reads from the shared context snapshot under the
    context_lock (already held by context_snapshot). The observer
    itself holds no locks.
    """

    def __init__(self, clock=None, rng=None):
        self._clock = clock or time.monotonic
        self._rng = rng or random.Random()
        # Passed in by main.py so the observer can ask "is something
        # in the middle of a command right now?"
        self._command_in_flight_fn: Optional[Callable[[], bool]] = None
        # Passed in by main.py: mark when user last typed.
        self._last_keystroke_fn: Optional[Callable[[], float]] = None

    def wire(self, *, command_in_flight_fn=None, last_keystroke_fn=None) -> None:
        """Called once by main.py to inject the two runtime callbacks."""
        self._command_in_flight_fn = command_in_flight_fn
        self._last_keystroke_fn = last_keystroke_fn

    # ---- public API --------------------------------------------------

    def get_context(self) -> dict:
        """Return a GateContext-compatible dict with real signal values."""
        now = self._clock()

        # Read runtime snapshot (thread-safe copy).
        ctx = context_snapshot()

        # --- user_is_typing: active keystrokes in last 1.5s --------
        user_is_typing = self._is_typing(now)

        # --- audio_busy: voice speaking right now -------------------
        # Deferred import to avoid circular at module level.
        # main.py wires this via set_voice_snapshot_fn.
        audio_busy = self._is_audio_busy()

        # --- game_active --------------------------------------------
        zone = ctx.get("active_zone", "unknown")
        game_active = zone == "game"

        # --- command_in_flight --------------------------------------
        command_in_flight = False
        if self._command_in_flight_fn is not None:
            try:
                command_in_flight = bool(self._command_in_flight_fn())
            except Exception:
                pass

        # --- mood_affection: from emotion memory -------------------
        mood_affection = self._get_mood_affection()

        # --- scene_relevance: attention window score -------------
        scene_relevance = self._get_scene_relevance(ctx)

        # --- silence_duration_s: time since last chat -------------
        silence_duration_s = self._get_silence_duration(now, ctx)

        # --- jitter_value ------------------------------------------
        jitter_value = self._rng.random() if self._rng else random.random()

        # --- web_context: Phase A7 Web Awareness -------------------
        # Read via the scraper so we cache and never block the loop.
        # Minimum cache age 1.5s so a burst of ticks does not re-read
        # the snapshot every time.
        web_context = self._get_web_context(min_age_s=1.5)

        # --- relevance boost when web context is effective --------
        # If the user is in the browser with a fresh page, the page
        # is itself "relevance" and should push scene_relevance up.
        # This is what makes Nana "know what Ba is reading/watching".
        if web_context.get("effective"):
            scene_relevance = max(scene_relevance, 0.85)

        # --- attention window: Phase A7 mode-picker hint ------------
        attention_window = ""
        try:
            from nana.runtime.attention import evaluate_attention_window
            aw = evaluate_attention_window({
                "zone": zone,
                "active_app": ctx.get("active_app"),
                "browser_kind": (ctx.get("browser") or {}).get("kind"),
                "idle_state": ctx.get("idle_state"),
                "idle_seconds": ctx.get("idle_seconds"),
                "in_flow": ctx.get("in_flow"),
            })
            attention_window = aw.get("window", "")
        except Exception:
            attention_window = ""

        return {
            "user_is_typing": user_is_typing,
            "game_active": game_active,
            "command_in_flight": command_in_flight,
            "audio_busy": audio_busy,
            "mood_affection": mood_affection,
            "scene_relevance": scene_relevance,
            "silence_duration_s": silence_duration_s,
            "silence_window_s": _SILENCE_WINDOW_S,
            "forced_mode": None,
            "jitter_value": jitter_value,
            "web_context": web_context,
            "attention_window": attention_window,
        }

    def make_gate_context(self) -> GateContext:
        """Convenience: return a typed GateContext directly."""
        d = self.get_context()
        return GateContext(
            user_is_typing=d["user_is_typing"],
            game_active=d["game_active"],
            command_in_flight=d["command_in_flight"],
            audio_busy=d["audio_busy"],
            mood_affection=d["mood_affection"],
            scene_relevance=d["scene_relevance"],
            silence_duration_s=d["silence_duration_s"],
            silence_window_s=d["silence_window_s"],
            forced_mode=d.get("forced_mode"),
            jitter_value=d["jitter_value"],
        )

    # ---- internal helpers ------------------------------------------

    def _is_typing(self, now: float) -> bool:
        """Return True if the user has pressed a key in the last 3.0s."""
        if self._last_keystroke_fn is None:
            return False
        try:
            last_key = self._last_keystroke_fn()
            return (now - last_key) < 3.0
        except Exception:
            return False

    def _is_audio_busy(self) -> bool:
        """Return True if voice is currently speaking (TTS or stream)."""
        # _voice_snapshot_fn is set by main.py via set_voice_snapshot_fn().
        fn = getattr(self, "_voice_snapshot_fn", None)
        if fn is None:
            return False
        try:
            snap = fn()
            return bool(snap.get("speaking", False))
        except Exception:
            return False

    def _get_mood_affection(self) -> float:
        """Return current affection from emotion memory.

        Falls back to 0.7 if memory is unavailable (stub behaviour).
        """
        try:
            from nana.memory import memory, memory_lock
            with memory_lock:
                return float(memory.get("emotion", {}).get("affection", 0.7))
        except Exception:
            return 0.7

    def _get_scene_relevance(self, ctx: dict) -> float:
        """Map attention window to a relevance score [0, 1].

        Scores are biased upward so the gate still fires (intensity >= 0.4)
        in most realistic scenarios.
        """
        attention = evaluate_attention_window({
            "zone": ctx.get("active_zone"),
            "active_app": ctx.get("active_app"),
            "browser_kind": (ctx.get("browser") or {}).get("kind"),
            "idle_state": ctx.get("idle_state"),
            "idle_seconds": ctx.get("idle_seconds"),
            "in_flow": ctx.get("in_flow"),
            "last_chat_age": None,  # silence handled separately
        })
        window = attention.get("window", "unknown")
        score_map = {
            "browse_active": 0.85,
            "chill":        0.80,
            "browse_light": 0.70,
            "idle":         0.65,
            "work_paused":  0.50,
            "work_flow":    0.30,
            "work_active":  0.20,
            "game":         0.15,
            "away":         0.40,
            "recent_chat":  0.75,
            "unknown":      0.50,
        }
        return score_map.get(window, 0.50)

    def _get_silence_duration(self, now: float, ctx: dict) -> float:
        """Return seconds since last chat (0 if recently chatted)."""
        last_chat = ctx.get("last_chat_time") or 0
        if last_chat <= 0:
            return 0.0
        return max(0.0, now - last_chat)

    def _get_web_context(self, min_age_s: float = 1.5) -> dict:
        """Phase A7: scrape the browser context snapshot.

        Returns a stable dict; never raises.
        """
        try:
            from nana.autonomy.web_context import get_web_context
            return get_web_context(min_age_s=min_age_s)
        except Exception:
            return {
                "available": False,
                "in_browser_zone": False,
                "fresh": False,
                "stale": False,
                "effective": False,
                "age_seconds": None,
                "browser": None,
                "url": None,
                "title": None,
                "page_heading": None,
                "meta_description": None,
                "selected_text": None,
                "kind": "unknown",
                "social_post_text": None,
                "social_vibe": None,
                "site_signals": {},
                "summary_hint": "Web context unavailable (observer error).",
            }

    # ---- wiring hooks for main.py ---------------------------------

    def set_voice_snapshot_fn(self, fn: Callable[[], dict]) -> None:
        """main.py calls this so the observer can ask voice for its state."""
        self._voice_snapshot_fn = fn


# Module-level singleton — mirrors the stub's get_context() API.
_observer: Optional[RealObserver] = None


def init_observer(clock=None, rng=None) -> RealObserver:
    global _observer
    _observer = RealObserver(clock=clock, rng=rng)
    return _observer


def get_context() -> dict:
    """Public API matching the old observer_stub signature."""
    if _observer is None:
        init_observer()
    return _observer.get_context()
