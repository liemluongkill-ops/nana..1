"""Expression gate: hybrid (hard constraints + intensity + mode mix).

Four steps from NANA_AUTONOMY_DESIGN.md:

    1. Hard constraints (must pass)
    2. Soft intensity score [0, 1]
    3. Mode-specific output mix (TTS / VTS / subtitle)
    4. Budget guard (per-minute cap)

Output of evaluate() is a GateDecision carrying:
    - allowed: bool
    - level:   full | vts_only | skip
    - reason:  string for logging
    - payload: {tts: bool, vts: bool, subtitle: bool}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# Intensity thresholds (locked in design doc).
INTENSITY_FULL = 0.70
INTENSITY_VTS_ONLY = 0.40

# Weights for the intensity score.
W_MOOD = 0.30
W_RELEVANCE = 0.40
W_SILENCE = 0.20
W_JITTER = 0.10

# Soft penalty when user is actively typing (Phase A6).
# Downgrades full TTS to VTS-only, not a hard block.
W_TYPING_PENALTY = 0.20

# Per-mode TTS probability.
MODE_TTS_PROB = {
    "idle_banter":    (0.40, 0.60),
    "observer_aware": (0.05, 0.05),
    "stream_host":    (0.90, 0.90),
}

# Subtitle is tied to TTS in v1.
SUBTITLE_FOLLOWS_TTS = True

# Budget guard.
EXPRESSIONS_PER_MINUTE_CAP = 4


@dataclass
class GateContext:
    """Everything the gate needs to know about the current frame."""

    mode: str
    user_is_typing: bool = False
    game_active: bool = False
    command_in_flight: bool = False
    audio_busy: bool = False
    # Soft signals (all 0..1):
    mood_affection: float = 0.5
    scene_relevance: float = 0.5
    silence_duration_s: float = 0.0
    silence_window_s: float = 60.0  # normalization window
    # Mode override hint: if non-None, the observer forced this mode.
    forced_mode: Optional[str] = None
    # Pre-jittered value from a seeded RNG; gate accepts a scalar so
    # smoke can assert exact behavior.
    jitter_value: float = 0.5
    # Phase A6: manual override. When True, the gate short-circuits
    # and allows full output regardless of typing/audio/state.
    forced_by_user: bool = False


@dataclass
class GateDecision:
    allowed: bool
    level: str  # "full" | "vts_only" | "skip"
    reason: str
    intensity: float
    payload: dict


class ExpressionGate:
    """Pure-function style gate: no I/O, no time, no threads.

    The loop calls evaluate(ctx) once per tick. If the decision is
    'skip', the loop just sleeps. If 'full' or 'vts_only', the loop
    then hands the payload to the express stub.
    """

    def __init__(self, rng=None):
        # We import here to keep the gate free of runtime imports at
        # module load. random.Random is fine for the gate too.
        import random
        self._rng = rng or random.Random()

    # ---- public API --------------------------------------------------

    def evaluate(self, ctx: GateContext) -> GateDecision:
        # Step 0: manual override (Phase A6) — user wants Nana to talk
        # right now. Short-circuit to full output.
        if ctx.forced_by_user:
            payload = self._payload(ctx.mode, "full")
            payload["forced_by_user"] = True
            return GateDecision(True, "full", "ok_forced_by_user", 1.0, payload)

        # Step 1: hard constraints.
        # Phase A6: user_is_typing is NO LONGER a hard block. It is a
        # soft penalty applied to the intensity score below. Only
        # command_in_flight, audio_busy, and game_silence_no_tts remain
        # as true hard blocks.
        hard = self._check_hard(ctx)
        if hard is not None:
            return GateDecision(False, "skip", hard, 0.0, {})

        # Step 2: intensity score (with soft typing penalty).
        intensity = self._intensity(ctx)
        if ctx.user_is_typing:
            intensity = max(0.0, intensity - W_TYPING_PENALTY)

        # Step 3: intensity -> level.
        if intensity >= INTENSITY_FULL:
            level = "full"
        elif intensity >= INTENSITY_VTS_ONLY:
            level = "vts_only"
        else:
            reason = "intensity_below_floor"
            if ctx.user_is_typing:
                reason = "intensity_below_floor_typing"
            return GateDecision(False, "skip", reason, intensity, {})

        # Step 4: if user is typing, force VTS-only (no TTS).
        if ctx.user_is_typing and level == "full":
            level = "vts_only"

        # Step 5: mode-specific mix.
        payload = self._payload(ctx.mode, level)

        return GateDecision(True, level, "ok", intensity, payload)

    # ---- internals ---------------------------------------------------

    def _check_hard(self, ctx: GateContext) -> Optional[str]:
        # Phase A6: user_is_typing removed from hard gates. It is now
        # a soft penalty applied in evaluate().
        if ctx.command_in_flight:
            return "command_in_flight"
        if ctx.audio_busy:
            return "audio_busy"
        # Game silence override: if a game is active, only observer_aware
        # mode is allowed, and that mode never produces TTS anyway, so
        # any TTS-requiring expression (idle_banter / stream_host)
        # collapses to a VTS-only payload below.
        if ctx.game_active and ctx.mode == "stream_host":
            return "game_silence_no_tts"
        return None

    def _intensity(self, ctx: GateContext) -> float:
        silence_norm = min(1.0, ctx.silence_duration_s / max(1.0, ctx.silence_window_s))
        raw = (
            W_MOOD * ctx.mood_affection
            + W_RELEVANCE * ctx.scene_relevance
            + W_SILENCE * silence_norm
            + W_JITTER * ctx.jitter_value
        )
        # Clamp to [0, 1] so a degenerate context cannot overflow.
        return max(0.0, min(1.0, raw))

    def _payload(self, mode: str, level: str) -> dict:
        vts = level in ("full", "vts_only")
        if level == "vts_only":
            return {"tts": False, "vts": vts, "subtitle": False}
        # level == "full" -> consult mode mix.
        prob_range = MODE_TTS_PROB.get(mode, (0.5, 0.5))
        # Pick a deterministic scalar from the rng; if the caller's
        # jitter_value is 0.5 we just use a fair coin.
        roll = self._rng.random()
        tts = prob_range[0] <= roll <= prob_range[1] if prob_range[0] == prob_range[1] else (
            roll < (prob_range[0] + prob_range[1]) / 2
        )
        subtitle = tts and SUBTITLE_FOLLOWS_TTS
        return {"tts": tts, "vts": vts, "subtitle": subtitle}

    def evaluate_debug(self, ctx: GateContext) -> dict:
        """Return a full diagnostic breakdown of every gate check.

        Does NOT modify any state. Used by the loop for DEBUG log lines
        and by /autonomy-status.

        Returns:
            {
                "hard_check": {
                    "passed": bool,
                    "reason_if_failed": str or None,
                },
                "intensity": {
                    "score": float,
                    "components": {
                        "mood":     float,
                        "relevance": float,
                        "silence":  float,
                        "jitter":   float,
                    },
                    "threshold_full":    float,
                    "threshold_vts_only": float,
                    "level_result": "full" | "vts_only" | "skip",
                },
                "cadence": {
                    "allowed": bool,
                    "reason": str,
                },
                "mode": ctx.mode,
                "suggestion": str,
            }
        """
        hard_failed = self._check_hard(ctx)
        hard_passed = hard_failed is None

        silence_norm = min(1.0, ctx.silence_duration_s / max(1.0, ctx.silence_window_s))
        comp_mood = ctx.mood_affection
        comp_rel  = ctx.scene_relevance
        comp_sil  = silence_norm
        comp_jit  = ctx.jitter_value
        base_intensity = max(0.0, min(1.0,
            W_MOOD       * comp_mood
            + W_RELEVANCE * comp_rel
            + W_SILENCE   * comp_sil
            + W_JITTER     * comp_jit
        ))
        # Apply soft penalty (Phase A6) when user is typing.
        typing_penalty = W_TYPING_PENALTY if ctx.user_is_typing else 0.0
        intensity = max(0.0, base_intensity - typing_penalty)

        # Level after penalty.
        if intensity >= INTENSITY_FULL:
            level_result = "full"
        elif intensity >= INTENSITY_VTS_ONLY:
            level_result = "vts_only"
        else:
            level_result = "skip"
        # If user is typing, force VTS-only even at full intensity.
        if ctx.user_is_typing and level_result == "full":
            level_result = "vts_only_typing"

        # Suggestion
        if not hard_passed:
            suggestion = _hard_suggestion(hard_failed, ctx)
        elif ctx.user_is_typing and intensity < INTENSITY_VTS_ONLY:
            suggestion = (
                f"Typing penalty active (-{W_TYPING_PENALTY:.2f}). "
                f"Base score {round(base_intensity, 2)} - "
                f"{W_TYPING_PENALTY:.2f} = {round(intensity, 2)}. "
                f"Need >= {INTENSITY_VTS_ONLY} even with penalty. "
                f"Increase silence/mood/relevance, or stop typing."
            )
        elif level_result == "skip":
            deficit = INTENSITY_VTS_ONLY - intensity
            components = {
                "mood":      round(comp_mood * W_MOOD, 3),
                "relevance": round(comp_rel  * W_RELEVANCE, 3),
                "silence":   round(comp_sil  * W_SILENCE, 3),
                "jitter":    round(comp_jit  * W_JITTER, 3),
            }
            biggest = max(components, key=components.get)
            deficit_pct = round(deficit * 100, 1)
            suggestion = (
                f"Intensity too low (+{round(intensity,2)}). "
                f"Need +{deficit_pct:.0f}% more. "
                f"Biggest drag: {biggest}={components[biggest]:.2f}. "
                f"Boost {biggest} or increase silence."
            )
        elif level_result in ("vts_only", "vts_only_typing"):
            deficit = INTENSITY_FULL - intensity
            typing_note = " (typing-penalty active)" if ctx.user_is_typing else ""
            suggestion = (
                f"Intensity {round(intensity,2)}{typing_note} — vts_only OK, "
                f"need +{round(deficit,2)} to reach full TTS ({INTENSITY_FULL})."
            )
        else:
            suggestion = "All checks passed. Full output eligible."

        return {
            "hard_check": {
                "passed": hard_passed,
                "reason_if_failed": hard_failed,
            },
            "intensity": {
                "score": round(intensity, 3),
                "base_score": round(base_intensity, 3),
                "typing_penalty": round(typing_penalty, 3),
                "user_is_typing": ctx.user_is_typing,
                "components": {
                    "mood":      round(comp_mood, 3),
                    "relevance": round(comp_rel,  3),
                    "silence":   round(comp_sil,  3),
                    "jitter":    round(comp_jit,  3),
                },
                "weights": {
                    "mood":      W_MOOD,
                    "relevance": W_RELEVANCE,
                    "silence":   W_SILENCE,
                    "jitter":    W_JITTER,
                },
                "threshold_full":     INTENSITY_FULL,
                "threshold_vts_only": INTENSITY_VTS_ONLY,
                "level_result": level_result,
            },
            "mode": ctx.mode,
            "suggestion": suggestion,
        }


def _hard_suggestion(reason: str, ctx: GateContext) -> str:
    table = {
        "command_in_flight": (
            "A slash command is still executing. "
            "Wait for it to finish."
        ),
        "audio_busy": (
            "Nana is currently speaking (TTS/stream in progress). "
            "Wait for playback to finish."
        ),
        "game_silence_no_tts": (
            f"Game is active (zone=game) but mode={ctx.mode}. "
            "In game mode only observer_aware (VTS-only) is allowed."
        ),
    }
    return table.get(reason, f"Hard gate blocked: {reason}.")
