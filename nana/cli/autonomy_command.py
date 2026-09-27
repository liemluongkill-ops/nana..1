"""CLI autonomy command helpers bound to the current CLI AutonomyLoop."""

from __future__ import annotations

from nana.autonomy.expression_gate import GateContext
from nana.cli.globals import AUTONOMY_EXPRESS, AUTONOMY_LOOP


def autonomy_status_snapshot() -> dict:
    """Return /autonomy-status data without importing legacy main."""

    try:
        cadence = AUTONOMY_LOOP.cadence
        events = cadence.events
        accepted = sum(1 for event in events if event.accepted)
        rejected = sum(1 for event in events if not event.accepted)
        top_rejects = sorted(
            AUTONOMY_LOOP.reject_tallies.items(),
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        gate_debug = None
        try:
            ctx_dict = AUTONOMY_LOOP.observer.get_context()
            ctx = GateContext(
                mode="idle_banter",
                user_is_typing=ctx_dict.get("user_is_typing", False),
                game_active=ctx_dict.get("game_active", False),
                command_in_flight=ctx_dict.get("command_in_flight", False),
                audio_busy=ctx_dict.get("audio_busy", False),
                mood_affection=ctx_dict.get("mood_affection", 0.5),
                scene_relevance=ctx_dict.get("scene_relevance", 0.5),
                silence_duration_s=ctx_dict.get("silence_duration_s", 0.0),
                silence_window_s=ctx_dict.get("silence_window_s", 60.0),
                forced_mode=ctx_dict.get("forced_mode"),
                jitter_value=ctx_dict.get("jitter_value", 0.5),
            )
            gate_debug = AUTONOMY_LOOP.gate.evaluate_debug(ctx)
        except Exception:
            gate_debug = None
        return {
            "state": AUTONOMY_LOOP.state.value,
            "paused": cadence.paused,
            "output_enabled": AUTONOMY_LOOP.output_enabled,
            "tick_count": AUTONOMY_LOOP._tick_count,
            "accepted": accepted,
            "rejected": rejected,
            "last_event": events[-1] if events else None,
            "top_reject_reasons": top_rejects,
            "gate_diagnostics": gate_debug,
        }
    except Exception as exc:
        return {"state": "unknown", "error": str(exc)}


def autonomy_set_mode(mode: str) -> str:
    """Return the new autonomy mode label, or 'unknown' for bad input."""

    mode = (mode or "").strip().lower()
    if mode in ("ultra-short", "ultra_short", "ultrashort", "short"):
        AUTONOMY_EXPRESS.set_prefer_ultra_short(True)
        return "ultra_short"
    if mode in ("full", "normal", "all"):
        AUTONOMY_EXPRESS.set_prefer_ultra_short(False)
        return "full"
    return "unknown"
