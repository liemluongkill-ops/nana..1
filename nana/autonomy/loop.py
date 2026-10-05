"""AutonomyLoop: background thread that ticks the gate and fires expressions.

Phase A2 behavior:

    - one daemon thread
    - ticks every CadenceScheduler.next_period_s() seconds
    - per tick:
        1. observer_stub.get_context() -> GateContext
        2. cadence.can_express(mode) -> (allowed, reason)
        3. if allowed: gate.evaluate(ctx) -> GateDecision
        4. cadence.record_expression(mode, accepted, reason)
        5. express_stub.emit(mode, level, payload)  (only if accepted)
    - pause()/resume() via the cadence scheduler
    - /autonomy-pause and /autonomy-resume slash commands are wired
      in nana/main.py; the loop just exposes the methods here

The thread runs in lock-step with a mocked clock during smoke tests.
The loop also exposes a synchronous tick() method for smoke, so we
can drive the loop without a real thread.
"""

from __future__ import annotations

import enum
import os
import threading
import time
from typing import Callable, Optional

from nana.autonomy.cadence import CadenceScheduler
from nana.autonomy.expression_gate import ExpressionGate, GateContext
from nana.autonomy.inner_thought import InnerThought
from nana.autonomy.observer import RealObserver, init_observer, get_context as observer_get_context
from nana.autonomy.express import AutonomyExpress
from nana.runtime.context_autonomy import (
    PrivateOwnerAutonomyAuthority,
    require_autonomy_snapshot,
)
from nana.runtime.context_contracts import Lane
from nana.runtime.livestream_identity import is_livestream_source


AUTONOMY_DEBUG_STDOUT = os.getenv("NANA_AUTONOMY_DEBUG_STDOUT", "0") == "1"


def _select_mode(context) -> str:
    forced = context.get("forced_mode")
    if forced is not None:
        return forced
    attention_window = str(context.get("attention_window") or "").lower()
    preferred = {
        "browse_active": "stream_host",
        "recent_chat": "stream_host",
        "browse_light": "observer_aware",
        "work_paused": "observer_aware",
        "chill": "idle_banter",
        "idle": "idle_banter",
        "away": "idle_banter",
    }.get(attention_window)
    order = [preferred] if preferred else []
    order += [
        item
        for item in ("stream_host", "observer_aware", "idle_banter")
        if item not in order
    ]
    return next((item for item in order if item is not None), "idle_banter")


class AutonomyState(str, enum.Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"


class AutonomyLoop:
    """Owns the background thread and the cadence/gate pipeline."""

    def __init__(self, clock=None, rng_factory=None):
        self._clock = clock or time.monotonic
        self._rng_factory = rng_factory
        self._rng = rng_factory() if rng_factory else None
        self._cadence = CadenceScheduler(clock=clock, rng=self._rng)
        self._gate = ExpressionGate(rng=self._rng)
        self._thought = InnerThought(
            rng=self._rng,
            clock=clock,
        )
        self._express = AutonomyExpress(
            clock=clock,
            rng=self._rng,
        )
        self._observer = RealObserver(
            clock=clock,
            rng=self._rng,
            audience_authority=PrivateOwnerAutonomyAuthority(),
        )
        self._state = AutonomyState.STOPPED
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._tick_count = 0
        self._log: list = []
        # Reject reason tallies for /autonomy-status Diagnostics.
        self._reject_tallies: dict = {}
        # Phase A6: one-shot user override flag. Set by /say or chat
        # path to force the next autonomy tick to bypass all gates.
        # Consumed (reset to False) by _do_tick().
        self._user_override: bool = False

    def request_user_override(self) -> None:
        """Phase A6: /say and chat handlers call this to force the
        next autonomy tick to speak regardless of gates.
        """
        self._user_override = True

    # ---- public API --------------------------------------------------

    def start(self, *, paused: bool = False) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        if paused:
            self._cadence.pause()
            self._state = AutonomyState.PAUSED
        else:
            self._cadence.resume()
            self._state = AutonomyState.RUNNING
        self._thread = threading.Thread(target=self._run, daemon=True, name="autonomy-loop")
        self._thread.start()

    def stop(self, join_timeout_s: float | None = None) -> bool:
        """Request stop and report whether the owned thread actually exited.

        ``None`` waits without a deadline. A bounded timeout returns ``False``
        and leaves the loop in ``stopping`` while its thread remains alive.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is None or not thread.is_alive():
            self._state = AutonomyState.STOPPED
            return True

        self._state = AutonomyState.STOPPING
        if thread is threading.current_thread():
            return False

        timeout = None if join_timeout_s is None else max(0.0, float(join_timeout_s))
        thread.join(timeout=timeout)
        if thread.is_alive():
            return False

        self._state = AutonomyState.STOPPED
        return True

    def pause(self) -> None:
        self._cadence.pause()
        if self._state == AutonomyState.RUNNING:
            self._state = AutonomyState.PAUSED

    def resume(self) -> None:
        self._cadence.resume()
        if self._state == AutonomyState.PAUSED:
            self._state = AutonomyState.RUNNING

    def tick(self) -> dict:
        """Run one synchronous tick. Returns a dict describing what
        happened; useful for smoke tests and for the slash command
        to print a single-step trace.
        """
        return self._do_tick()

    def note_user_command(self) -> None:
        self._cadence.note_user_command()

    @property
    def state(self) -> AutonomyState:
        if (
            self._state == AutonomyState.STOPPING
            and self._thread is not None
            and not self._thread.is_alive()
        ):
            self._state = AutonomyState.STOPPED
        return self._state

    @property
    def output_enabled(self) -> bool:
        """Whether any autonomous subsystem may emit user-visible output."""
        return self.state == AutonomyState.RUNNING

    @property
    def log(self) -> list:
        return list(self._log)

    @property
    def cadence(self) -> CadenceScheduler:
        return self._cadence

    @property
    def gate(self) -> ExpressionGate:
        return self._gate

    @property
    def express(self) -> AutonomyExpress:
        return self._express

    @property
    def observer(self) -> RealObserver:
        return self._observer

    @property
    def reject_tallies(self) -> dict:
        with threading.Lock():
            return dict(self._reject_tallies)

    # ---- internals ---------------------------------------------------

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                self._do_tick()
                period = self._cadence.next_period_s()
                # Event.wait sleeps at most the period; if stop_event fires
                # we wake up immediately.
                self._stop_event.wait(timeout=period)
        finally:
            self._state = AutonomyState.STOPPED

    def _do_tick(self) -> dict:
        self._tick_count += 1
        forced_by_user = self._user_override
        self._user_override = False

        # Paused autonomy must not collect owner state. Preserve the established
        # cadence reason while exiting before the observer boundary.
        if self._cadence.paused:
            mode = "idle_banter"
            _allowed, reason = self._cadence.can_express(mode)
            self._cadence.record_expression(mode, accepted=False, reason=reason)
            decision = {
                "accepted": False,
                "reason": reason,
                "level": "skip",
                "mode": mode,
            }
            self._log_tick(decision, mode=mode)
            self._tally_reject(f"cadence/{reason}")
            self._print_rejected(
                f"cadence/{reason}",
                {"cadence_reason": reason},
                mode=mode,
            )
            return decision

        ctx_dict = self._observer.get_context()

        try:
            autonomy_snapshot = require_autonomy_snapshot(
                ctx_dict.get("_autonomy_snapshot")
            )
        except Exception:
            # Unresolved observations get only a fixed neutral cadence identity.
            # Raw observer fields cannot select a per-mode cooldown bucket.
            mode = "idle_banter"
            allowed, reason = self._cadence.can_express(mode)
            if not allowed:
                self._cadence.record_expression(mode, accepted=False, reason=reason)
                decision = {
                    "accepted": False,
                    "reason": reason,
                    "level": "skip",
                    "mode": mode,
                }
                self._log_tick(decision, mode=mode)
                self._tally_reject(f"cadence/{reason}")
                self._print_rejected(
                    f"cadence/{reason}",
                    {"cadence_reason": reason},
                    mode=mode,
                )
                return decision
            self._cadence.record_expression(
                mode,
                accepted=False,
                reason="audience_unresolved",
            )
            decision = {
                "accepted": False,
                "reason": "audience_unresolved",
                "level": "skip",
                "mode": mode,
            }
            self._log_tick(decision, mode=mode)
            self._tally_reject("audience_unresolved")
            self._print_rejected("audience_unresolved", {}, mode=mode)
            return decision

        frozen_context = autonomy_snapshot.payload
        mode = _select_mode(frozen_context)

        if mode is None:
            decision = {
                "accepted": False,
                "reason": "inner_thought_no_mode",
                "level": "skip",
            }
            self._log_tick(decision, mode=None)
            self._tally_reject("inner_thought_no_mode")
            self._print_rejected("inner_thought_no_mode", {}, mode=mode)
            return decision

        # Step 1: cadence.
        allowed, reason = self._cadence.can_express(mode)
        if not allowed:
            self._cadence.record_expression(mode, accepted=False, reason=reason)
            decision = {
                "accepted": False,
                "reason": reason,
                "level": "skip",
                "mode": mode,
            }
            self._log_tick(decision, mode=mode)
            self._tally_reject(f"cadence/{reason}")
            self._print_rejected(f"cadence/{reason}", {"cadence_reason": reason}, mode=mode)
            return decision

        ctx_dict = dict(frozen_context)
        ctx_dict["_autonomy_snapshot"] = autonomy_snapshot
        ctx_dict["forced_by_user"] = forced_by_user
        web_ctx = frozen_context.get("web_context") or {}

        # --- [DEBUG ATTENTION] accepted cadence, validated snapshot ----------
        self._print_attention_debug(ctx_dict)

        ctx = GateContext(
            mode=mode,
            user_is_typing=frozen_context.get("user_is_typing", False),
            game_active=frozen_context.get("game_active", False),
            command_in_flight=frozen_context.get("command_in_flight", False),
            audio_busy=frozen_context.get("audio_busy", False),
            mood_affection=frozen_context.get("mood_affection", 0.5),
            scene_relevance=frozen_context.get("scene_relevance", 0.5),
            silence_duration_s=frozen_context.get("silence_duration_s", 0.0),
            silence_window_s=frozen_context.get("silence_window_s", 60.0),
            forced_mode=frozen_context.get("forced_mode"),
            jitter_value=frozen_context.get("jitter_value", 0.5),
            forced_by_user=forced_by_user,
        )

        autonomy_audience = autonomy_snapshot.audience
        public_audience = autonomy_audience.lane is Lane.PUBLIC_STAGE
        livestream_stage = (
            public_audience
            and is_livestream_source(autonomy_audience.public_scope.platform)
        )

        # Step 2: gate — use debug version for detailed diagnostics.
        gate_debug = self._gate.evaluate_debug(ctx)
        gate_decision = self._gate.evaluate(ctx)

        if not gate_decision.allowed:
            self._cadence.record_expression(mode, accepted=False, reason=gate_decision.reason)
            decision = {
                "accepted": False,
                "reason": gate_decision.reason,
                "level": gate_decision.level,
                "mode": mode,
                "intensity": gate_decision.intensity,
            }
            self._log_tick(decision, mode=mode)
            self._tally_reject(f"gate/{gate_decision.reason}")
            self._print_rejected(
                f"gate/{gate_decision.reason}",
                gate_debug,
                mode=mode,
            )
            return decision

        # Step 2b: STAGE-8A/8B stream policy — explicit public stage only.
        # The older "stream_host" autonomy mode is also used for browser/recent
        # chat banter, so it is not enough to prove Nana is currently live.
        # Callers that truly run a public stream lane must set this flag.
        if livestream_stage:
            try:
                from nana.runtime.stream_state import get_stream_state
                from nana.runtime.proactive_engine import get_proactive_engine

                stream_policy = get_stream_state().get_policy()
                proactive_decision = get_proactive_engine().should_proactive(
                    policy=stream_policy,
                    chat_velocity=0.0,  # proactive tick has no real velocity
                    last_proactive_age_s=999.0,
                    last_nana_chat_age_s=999.0,
                )

                if not stream_policy.can_proactive or not proactive_decision.should_proactive:
                    self._cadence.record_expression(mode, accepted=False, reason="stream_policy_blocked")
                    reject_reason = (
                        f"stream_policy/{stream_policy.state.value}/"
                        f"{proactive_decision.reason}"
                    )
                    decision = {
                        "accepted": False,
                        "reason": reject_reason,
                        "level": "skip",
                        "mode": mode,
                    }
                    self._log_tick(decision, mode=mode)
                    self._tally_reject(reject_reason)
                    self._print_rejected(reject_reason, {}, mode=mode)
                    return decision
            except Exception as exc:
                reject_reason = f"stream_policy_error/{type(exc).__name__}"
                self._cadence.record_expression(mode, accepted=False, reason="stream_policy_error")
                decision = {
                    "accepted": False,
                    "reason": reject_reason,
                    "level": "skip",
                    "mode": mode,
                }
                self._log_tick(decision, mode=mode)
                self._tally_reject(reject_reason)
                self._print_rejected(reject_reason, {}, mode=mode)
                return decision

        # Step 3: pick the actual text.
        # Phase A7: pick() now tries LLM first, falls back to templates.
        # Pass ctx_dict and web_ctx so the LLM prompt has full context.
        picker = (
            self._thought.pick_prefer_ultra_short
            if self._express.prefer_ultra_short
            else self._thought.pick
        )
        thought = picker(mode, ctx_dict=ctx_dict, web_ctx=web_ctx)
        if thought is None:
            self._cadence.record_expression(mode, accepted=False, reason="all_lines_on_cooldown")
            decision = {
                "accepted": False,
                "reason": "all_lines_on_cooldown",
                "level": "skip",
                "mode": mode,
            }
            self._log_tick(decision, mode=mode)
            self._tally_reject("all_lines_on_cooldown")
            self._print_rejected("all_lines_on_cooldown", {}, mode=mode)
            return decision

        # Step 4: express.
        if public_audience:
            from dataclasses import replace
            from nana.runtime.persona_boundary import sanitize_public_reply

            scope = autonomy_audience.public_scope
            public_text = sanitize_public_reply(
                thought.text,
                viewer_name=scope.display_name,
            )
            if livestream_stage:
                from nana.runtime.livestream_identity import (
                    finalize_livestream_identity,
                )

                public_text = finalize_livestream_identity(
                    public_text,
                    source=scope.platform,
                    viewer_name=scope.display_name,
                )
            thought = replace(thought, text=public_text)
        trace = self._express.emit(mode, gate_decision.level, gate_decision.payload, thought)
        self._cadence.record_expression(mode, accepted=True, reason="ok")
        decision = {
            "accepted": True,
            "reason": "ok",
            "level": gate_decision.level,
            "mode": mode,
            "intensity": gate_decision.intensity,
            "payload": gate_decision.payload,
            "thought": thought,
            "trace": trace,
        }
        self._log_tick(decision, mode=mode)
        return decision

    def _log_tick(self, decision: dict, mode) -> None:
        self._log.append({
            "tick": self._tick_count,
            "t": self._clock(),
            "mode": mode,
            **decision,
        })

    # ---- debug helpers -----------------------------------------------

    def _print_attention_debug(self, ctx_dict: dict) -> None:
        """Print [DEBUG ATTENTION] every tick so the user can see
        why the policy is observe vs engage.
        """
        if not AUTONOMY_DEBUG_STDOUT:
            return
        # Derive attention window from scene_relevance.
        score = ctx_dict.get("scene_relevance", 0.5)
        if score >= 0.80:
            policy = "engage"
        elif score >= 0.60:
            policy = "available"
        elif score >= 0.40:
            policy = "observe"
        elif score >= 0.20:
            policy = "low"
        else:
            policy = "mute"

        reason_parts = []
        if ctx_dict.get("user_is_typing"):
            reason_parts.append("typing")
        if ctx_dict.get("game_active"):
            reason_parts.append("game")
        if ctx_dict.get("command_in_flight"):
            reason_parts.append("cmd_flight")
        if ctx_dict.get("audio_busy"):
            reason_parts.append("speaking")
        if not reason_parts:
            reason_parts.append("idle/silent")

        print(
            f"[DEBUG ATTENTION] "
            f"Policy: {policy} | "
            f"Score: {score:.2f} | "
            f"Reason: {', '.join(reason_parts)} | "
            f"Audio: {'busy' if ctx_dict.get('audio_busy') else 'idle'} | "
            f"Silence: {ctx_dict.get('silence_duration_s', 0):.1f}s | "
            f"Mood: {ctx_dict.get('mood_affection', 0):.2f}"
        )

    def _print_rejected(self, reason: str, debug_info: dict, mode: str) -> None:
        """Print [DEBUG REJECTED] with specific gate details."""
        if not AUTONOMY_DEBUG_STDOUT:
            return
        prefix = "[DEBUG REJECTED]"

        if reason.startswith("cadence/"):
            c_reason = reason.split("/", 1)[1]
            detail = debug_info.get("cadence_reason", c_reason)
            print(f"{prefix} Reason: cadence/{c_reason} | Detail: {detail} | Mode: {mode}")

        elif reason.startswith("gate/"):
            g_reason = reason.split("/", 1)[1]
            if g_reason in ("command_in_flight", "audio_busy", "game_silence_no_tts"):
                val_map = {
                    "command_in_flight":  ("Active",  "Idle"),
                    "audio_busy":         ("Speaking", "Idle"),
                    "game_silence_no_tts": ("Game+stream_host", "OK"),
                }
                val, need = val_map.get(g_reason, ("Unknown", "Unknown"))
                print(f"{prefix} Reason: {g_reason} | Value: {val} | Required: {need} | Mode: {mode}")

            elif g_reason in ("intensity_below_floor", "intensity_below_floor_typing"):
                intensity_info = debug_info.get("intensity", {})
                score = intensity_info.get("score", 0)
                base = intensity_info.get("base_score", score)
                penalty = intensity_info.get("typing_penalty", 0)
                comps = intensity_info.get("components", {})
                thr_full = intensity_info.get("threshold_full", 0.70)
                thr_vts  = intensity_info.get("threshold_vts_only", 0.40)
                biggest = max(comps, key=comps.get) if comps else "n/a"
                pen_str = f" (pen={penalty:.2f})" if penalty > 0 else ""
                print(
                    f"{prefix} Reason: {g_reason}{pen_str} | "
                    f"Score: {score:.2f} (base {base:.2f}) | "
                    f"Threshold_VTS: >={thr_vts:.2f} | "
                    f"Threshold_Full: >={thr_full:.2f} | "
                    f"Biggest: {biggest}={comps.get(biggest, 0):.3f} | "
                    f"Mode: {mode}"
                )

            else:
                print(f"{prefix} Reason: gate/{g_reason} | Mode: {mode}")

        elif reason == "inner_thought_no_mode":
            print(f"{prefix} Reason: inner_thought_no_mode | All lines on cooldown or no eligible mode | Mode: {mode}")

        elif reason == "all_lines_on_cooldown":
            print(f"{prefix} Reason: all_lines_on_cooldown | Every line in {mode} is on cooldown | Mode: {mode}")

        else:
            print(f"{prefix} Reason: {reason} | Mode: {mode}")

    def _tally_reject(self, reason: str) -> None:
        """Track reject reasons for /autonomy-status Diagnostics."""
        with threading.Lock():
            self._reject_tallies[reason] = self._reject_tallies.get(reason, 0) + 1


# ── Module-level singleton (mirrors main.py line 208) ──────────────────────────
AUTONOMY_LOOP = AutonomyLoop()
AUTONOMY_EXPRESS = AUTONOMY_LOOP.express
