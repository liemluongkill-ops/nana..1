"""
osu! executor dispatch preflight - Phase 35.

Validates a Phase 34 action packet for future dispatch review eligibility.
This module is preview-only: it never dispatches input, never calls SendInput,
never invokes emergency release, never moves/clicks/taps, and never marks
anything as armed or executable.
"""

from __future__ import annotations

from typing import Any, Callable

from nana.game.osu.executor_action_packet_preview import build_executor_action_packet_preview_payload
from nana.game.osu.executor_sim_tick_loop import MAX_SAMPLES
from nana.game.osu.input_safety import build_input_safety_status


DISPATCH_PREFLIGHT_SCHEMA = "nana.osu.executor_dispatch_preflight.v1"
ACTION_PACKET_SCHEMA = "nana.osu.executor_action_packet.v1"
DEFAULT_DISPATCH_LATE_WINDOW_MS = 120
DEFAULT_DISPATCH_EARLY_WINDOW_MS = 120
DEFAULT_TIMING_CONSISTENCY_TOLERANCE_MS = 2

VALID_PACKET_ACTIONS = {
    "would_send_tap",
    "would_send_slider_follow",
    "would_send_spinner_follow_or_hold",
    "would_send_move_to",
}
UPSTREAM_FALSE_FIELDS = (
    "dispatch",
    "armed",
    "executable",
    "real_input",
    "submit",
    "gameplay_input",
    "key_down_sent",
    "key_up_sent",
    "cursor_move",
    "click",
    "tap",
)

PayloadBuilder = Callable[..., dict[str, Any]]


def _base_flags() -> dict[str, bool]:
    return {
        "dispatch": False,
        "armed": False,
        "executable": False,
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "gameplay_input": False,
        "key_down_sent": False,
        "key_up_sent": False,
        "cursor_move": False,
        "click": False,
        "tap": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
    }


def _parse_flags(text: str) -> dict[str, int]:
    values = {
        "dispatch_late_window_ms": DEFAULT_DISPATCH_LATE_WINDOW_MS,
        "dispatch_early_window_ms": DEFAULT_DISPATCH_EARLY_WINDOW_MS,
    }
    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        if name == "dispatch-late-window-ms":
            values["dispatch_late_window_ms"] = int(raw_value)
        elif name == "dispatch-early-window-ms":
            values["dispatch_early_window_ms"] = int(raw_value)
    if values["dispatch_late_window_ms"] < 0:
        raise ValueError("dispatch_late_window_ms_must_be_non_negative")
    if values["dispatch_early_window_ms"] < 0:
        raise ValueError("dispatch_early_window_ms_must_be_non_negative")
    return values


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _xy_present(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return value.get("x") is not None and value.get("y") is not None


def _timing_diagnostics(
    scheduled_t: Any,
    current_time_ms: Any,
    time_to_action_ms: Any,
    *,
    tolerance_ms: int = DEFAULT_TIMING_CONSISTENCY_TOLERANCE_MS,
) -> dict[str, Any]:
    scheduled = _coerce_int(scheduled_t)
    current = _coerce_int(current_time_ms)
    to_action = _coerce_int(time_to_action_ms)
    if scheduled is None or current is None or to_action is None:
        return {
            "timing_delta_ms": None,
            "timing_consistency_error_ms": None,
            "timing_consistent": None,
            "timing_consistency_tolerance_ms": int(tolerance_ms),
        }
    delta = scheduled - current
    error = delta - to_action
    return {
        "timing_delta_ms": delta,
        "timing_consistency_error_ms": error,
        "timing_consistent": abs(error) <= int(tolerance_ms),
        "timing_consistency_tolerance_ms": int(tolerance_ms),
    }


def _safe_packet(
    text: str,
    *,
    packet_builder: PayloadBuilder | None,
    packet_kwargs: dict[str, Any],
) -> dict[str, Any]:
    builder = packet_builder or build_executor_action_packet_preview_payload
    return builder(text, **packet_kwargs)


def _safe_safety(
    *,
    safety_builder: PayloadBuilder | None,
    safety_kwargs: dict[str, Any],
) -> dict[str, Any]:
    builder = safety_builder or build_input_safety_status
    return builder(**safety_kwargs)


def _empty_fields() -> dict[str, Any]:
    return {
        "dispatch_ready": False,
        "dispatch_mode": "preview_only",
        "packet_valid": False,
        "packet_ready": False,
        "packet_schema": None,
        "packet_id": None,
        "packet_action": None,
        "packet_action_type": None,
        "action_id": None,
        "source_object_index": None,
        "source_sample_index": None,
        "source_tick_decision": None,
        "loop_result": None,
        "execute_preview_count": 0,
        "packet_source": "unavailable",
        "stale_lease_ignored": False,
        "samples_max": MAX_SAMPLES,
        "scheduled_t": None,
        "current_time_ms": None,
        "time_to_action_ms": None,
        "timing_delta_ms": None,
        "timing_consistency_error_ms": None,
        "timing_consistent": None,
        "timing_consistency_tolerance_ms": DEFAULT_TIMING_CONSISTENCY_TOLERANCE_MS,
        "selected_metadata_source": None,
        "selected_metadata_complete": False,
        "selected_playfield": None,
        "selected_screen": None,
        "action_match_reason": None,
        "dispatch_late_window_ms": DEFAULT_DISPATCH_LATE_WINDOW_MS,
        "dispatch_early_window_ms": DEFAULT_DISPATCH_EARLY_WINDOW_MS,
        "timing_state": None,
        "playfield": None,
        "screen": None,
        "focus_readiness": "unavailable_no_helper",
        "window_title": None,
        "window_class": None,
        "process_name": None,
        "emergency_abort_available": "unavailable_no_helper",
        "emergency_abort_strategy": "preview_only",
        "emergency_release_available": "unavailable_no_helper",
        "emergency_release_strategy": "preview_only",
    }


def _base_payload(
    *,
    decision: str,
    packet: dict[str, Any] | None,
    safety: dict[str, Any] | None,
    flags: dict[str, int],
    blockers: list[str],
    reason: str,
    dispatch_ready: bool = False,
    packet_valid: bool = False,
) -> dict[str, Any]:
    packet = packet or {}
    safety = safety or {}
    payload = {
        "schema": DISPATCH_PREFLIGHT_SCHEMA,
        "version": 1,
        "decision": decision,
        **_empty_fields(),
        "blockers": list(blockers),
        "reason": reason,
        **_base_flags(),
    }
    payload.update(
        {
            "dispatch_ready": bool(dispatch_ready),
            "dispatch_mode": "preview_only",
            "packet_valid": bool(packet_valid),
            "packet_ready": bool(packet.get("packet_ready")),
            "packet_schema": packet.get("packet_schema"),
            "packet_id": packet.get("packet_id"),
            "packet_action": packet.get("packet_action"),
            "packet_action_type": packet.get("packet_action_type"),
            "action_id": packet.get("action_id"),
            "source_object_index": packet.get("source_object_index"),
            "source_sample_index": packet.get("source_sample_index"),
            "source_tick_decision": packet.get("source_tick_decision"),
            "loop_result": packet.get("loop_result"),
            "execute_preview_count": int(packet.get("execute_preview_count") or 0),
            "packet_source": packet.get("packet_source") or ("fresh_action_packet_preview" if packet.get("packet_ready") else "unavailable"),
            "stale_lease_ignored": bool(packet.get("stale_lease_ignored")),
            "samples_max": int(packet.get("samples_max") or MAX_SAMPLES),
            "scheduled_t": packet.get("scheduled_t"),
            "current_time_ms": packet.get("current_time_ms"),
            "time_to_action_ms": packet.get("time_to_action_ms"),
            "selected_metadata_source": packet.get("selected_metadata_source"),
            "selected_metadata_complete": bool(packet.get("selected_metadata_complete")),
            "selected_playfield": packet.get("selected_playfield"),
            "selected_screen": packet.get("selected_screen"),
            "action_match_reason": packet.get("action_match_reason"),
            "dispatch_late_window_ms": int(flags["dispatch_late_window_ms"]),
            "dispatch_early_window_ms": int(flags["dispatch_early_window_ms"]),
            "timing_state": packet.get("timing_state"),
            "playfield": packet.get("playfield"),
            "screen": packet.get("screen"),
            "focus_readiness": safety.get("focus_readiness") or "unavailable_no_helper",
            "window_title": safety.get("window_title"),
            "window_class": safety.get("window_class"),
            "process_name": safety.get("process_name"),
            "emergency_abort_available": safety.get("emergency_abort_available") or "unavailable_no_helper",
            "emergency_abort_strategy": safety.get("abort_strategy") or "preview_only",
            "emergency_release_available": safety.get("emergency_release_available") or "unavailable_no_helper",
            "emergency_release_strategy": safety.get("release_strategy") or "preview_only",
        }
    )
    payload.update(
        _timing_diagnostics(
            payload.get("scheduled_t"),
            payload.get("current_time_ms"),
            payload.get("time_to_action_ms"),
        )
    )
    payload.update(_base_flags())
    return payload


def _validate_packet(packet: dict[str, Any], safety: dict[str, Any], flags: dict[str, int]) -> list[str]:
    blockers: list[str] = []
    if packet.get("packet_ready") is not True:
        blockers.append("packet_not_ready")
    if packet.get("packet_schema") != ACTION_PACKET_SCHEMA:
        blockers.append("bad_packet_schema")
    if packet.get("packet_action") not in VALID_PACKET_ACTIONS:
        blockers.append("unknown_packet_action")
    if packet.get("source_tick_decision") != "would_execute_preview_only":
        blockers.append("source_tick_not_execute_preview")
    if packet.get("source_sample_index") is None:
        blockers.append("missing_source_sample_index")
    if not packet.get("action_id"):
        blockers.append("missing_action_id")
    if packet.get("scheduled_t") is None:
        blockers.append("missing_scheduled_t")
    if packet.get("current_time_ms") is None:
        blockers.append("missing_current_time_ms")
    time_to_action_ms = _coerce_int(packet.get("time_to_action_ms"))
    if time_to_action_ms is None:
        blockers.append("missing_time_to_action_ms")
    else:
        if time_to_action_ms < -int(flags["dispatch_late_window_ms"]):
            blockers.append("time_to_action_older_than_late_window")
        if time_to_action_ms > int(flags["dispatch_early_window_ms"]):
            blockers.append("time_to_action_later_than_early_window")
    timing = _timing_diagnostics(
        packet.get("scheduled_t"),
        packet.get("current_time_ms"),
        packet.get("time_to_action_ms"),
    )
    if timing.get("timing_consistent") is False or packet.get("timing_consistent") is False:
        blockers.append("dispatch_timing_mismatch")
    if not _xy_present(packet.get("playfield")):
        blockers.append("missing_playfield_xy")
    if not _xy_present(packet.get("screen")):
        blockers.append("missing_screen_xy")
    if packet.get("timing_state") not in {"immediate", "future"}:
        blockers.append("timing_state_not_reviewable")
    if (safety.get("focus_readiness") or "unavailable_no_helper") != "ready":
        blockers.append(f"focus_not_ready:{safety.get('focus_readiness') or 'unavailable_no_helper'}")
    if (safety.get("emergency_abort_available") or "unavailable_no_helper") != "ready":
        blockers.append(f"emergency_abort_not_ready:{safety.get('emergency_abort_available') or 'unavailable_no_helper'}")
    if (safety.get("emergency_release_available") or "unavailable_no_helper") != "ready":
        blockers.append(
            f"emergency_release_not_ready:{safety.get('emergency_release_available') or 'unavailable_no_helper'}"
        )
    for field in UPSTREAM_FALSE_FIELDS:
        if packet.get(field) is True:
            blockers.append(f"upstream_flag_true:{field}")
    return list(dict.fromkeys(blockers))


def build_executor_dispatch_preflight_payload(
    text: str = "",
    *,
    decision_label: str = "executor_dispatch_preflight_ready_no_input",
    packet_builder: PayloadBuilder | None = None,
    safety_builder: PayloadBuilder | None = None,
    **packet_kwargs: Any,
) -> dict[str, Any]:
    flags = _parse_flags(text)
    try:
        packet = _safe_packet(text, packet_builder=packet_builder, packet_kwargs=dict(packet_kwargs))
    except Exception as exc:
        return _base_payload(
            decision="executor_dispatch_preflight_hold_no_input",
            packet=None,
            safety=None,
            flags=flags,
            blockers=[f"packet_builder_error:{type(exc).__name__}"],
            reason=f"packet_builder_error:{type(exc).__name__}",
        )

    try:
        safety = _safe_safety(safety_builder=safety_builder, safety_kwargs={})
    except Exception as exc:
        safety = {
            "focus_readiness": "error",
            "emergency_abort_available": "error",
            "emergency_release_available": "error",
            "blockers": [f"safety_builder_error:{type(exc).__name__}"],
        }

    if packet.get("packet_ready") is not True:
        reason = str(packet.get("reason") or packet.get("loop_result") or "packet_not_ready")
        blockers = ["packet_not_ready", *[str(blocker) for blocker in (packet.get("blockers") or [])]]
        return _base_payload(
            decision="executor_dispatch_preflight_hold_no_input",
            packet=packet,
            safety=safety,
            flags=flags,
            blockers=list(dict.fromkeys(blockers)),
            reason=reason,
            dispatch_ready=False,
            packet_valid=False,
        )

    blockers = _validate_packet(packet, safety, flags)
    packet_valid = not blockers
    if packet_valid:
        decision = decision_label
        reason = "dispatch_review_ready_preview_only"
    else:
        decision = "executor_dispatch_preflight_blocked_no_input"
        reason = blockers[0]
    return _base_payload(
        decision=decision,
        packet=packet,
        safety=safety,
        flags=flags,
        blockers=blockers,
        reason=reason,
        dispatch_ready=packet_valid,
        packet_valid=packet_valid,
    )


def build_executor_dispatch_preflight_status_payload(text: str = "", **kwargs: Any) -> dict[str, Any]:
    return build_executor_dispatch_preflight_payload(
        text,
        decision_label="executor_dispatch_preflight_status_only_no_input",
        **kwargs,
    )


def print_executor_dispatch_preflight(
    payload: dict[str, Any],
    *,
    title: str = "osu! Executor Dispatch Preflight Preview",
) -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Dispatch ready: {payload['dispatch_ready']}")
    print(f"  Dispatch mode: {payload['dispatch_mode']}")
    print(f"  Packet valid: {payload['packet_valid']}")
    print(f"  Packet ready: {payload['packet_ready']}")
    print(f"  Packet schema: {payload['packet_schema']}")
    print(f"  Packet id: {payload['packet_id']}")
    print(f"  Packet action: {payload['packet_action']}")
    print(f"  Packet action type: {payload['packet_action_type']}")
    print(f"  Action id: {payload['action_id']}")
    print(f"  Source object index: {payload['source_object_index']}")
    print(f"  Source sample index: {payload['source_sample_index']}")
    print(f"  Source tick decision: {payload['source_tick_decision']}")
    print(f"  Loop result: {payload['loop_result']}")
    print(f"  Execute preview count: {payload['execute_preview_count']}")
    print(f"  Packet source: {payload['packet_source']}")
    print(f"  Stale lease ignored: {payload['stale_lease_ignored']}")
    print(f"  Samples max: {payload['samples_max']}")
    print(f"  Scheduled t: {payload['scheduled_t']}")
    print(f"  Current time ms: {payload['current_time_ms']}")
    print(f"  Time to action ms: {payload['time_to_action_ms']}")
    print(f"  Timing delta ms: {payload['timing_delta_ms']}")
    print(f"  Timing consistency error ms: {payload['timing_consistency_error_ms']}")
    print(f"  Timing consistent: {payload['timing_consistent']}")
    print(f"  Timing consistency tolerance ms: {payload['timing_consistency_tolerance_ms']}")
    print(f"  Selected metadata source: {payload['selected_metadata_source']}")
    print(f"  Selected metadata complete: {payload['selected_metadata_complete']}")
    print(f"  Selected playfield: {payload['selected_playfield']}")
    print(f"  Selected screen: {payload['selected_screen']}")
    print(f"  Action match reason: {payload['action_match_reason']}")
    print(f"  Dispatch late window ms: {payload['dispatch_late_window_ms']}")
    print(f"  Dispatch early window ms: {payload['dispatch_early_window_ms']}")
    print(f"  Timing state: {payload['timing_state']}")
    print(f"  Playfield: {payload['playfield']}")
    print(f"  Screen: {payload['screen']}")
    print(f"  Focus readiness: {payload['focus_readiness']}")
    print(f"  Window title: {payload['window_title']}")
    print(f"  Window class: {payload['window_class']}")
    print(f"  Process name: {payload['process_name']}")
    print(f"  Emergency abort available: {payload['emergency_abort_available']}")
    print(f"  Emergency abort strategy: {payload['emergency_abort_strategy']}")
    print(f"  Emergency release available: {payload['emergency_release_available']}")
    print(f"  Emergency release strategy: {payload['emergency_release_strategy']}")
    print(f"  Blockers: {payload['blockers']}")
    print(f"  Reason: {payload['reason']}")
    print(f"  Dispatch: {payload['dispatch']}")
    print(f"  Armed: {payload['armed']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input allowed: {payload['real_input_allowed']}")
    print(f"  Submit allowed: {payload['submit_allowed']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
    print(f"  Gameplay input: {payload['gameplay_input']}")
    print(f"  Key down sent: {payload['key_down_sent']}")
    print(f"  Key up sent: {payload['key_up_sent']}")
    print(f"  Cursor move: {payload['cursor_move']}")
    print(f"  Click: {payload['click']}")
    print(f"  Tap: {payload['tap']}")
    print(f"  VTS call: {payload['vts_call']}")
    print(f"  Voice call: {payload['voice_call']}")
    print(f"  OBS call: {payload['obs_call']}")
    print(f"  Vision call: {payload['vision_call']}")
