"""
osu! executor shadow dispatch session - Phase 37.

Runs a bounded multi-attempt review over Phase 36 dispatch preflight payloads.
This module is preview-only: it never dispatches input, never invokes OS input
or emergency release paths, never moves/clicks/taps, and never marks
anything as armed or executable.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from nana.game.osu.executor_dispatch_preflight import build_executor_dispatch_preflight_payload
from nana.game.osu.executor_sim_tick_loop import MAX_SAMPLES


SHADOW_DISPATCH_SESSION_SCHEMA = "nana.osu.executor_shadow_dispatch_session.v1"
DEFAULT_ATTEMPTS = 3
MAX_ATTEMPTS = 10
DEFAULT_INTERVAL_MS = 80
MAX_INTERVAL_MS = 500

PayloadBuilder = Callable[..., dict[str, Any]]
Sleeper = Callable[[float], None]


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
        "attempts": DEFAULT_ATTEMPTS,
        "interval_ms": DEFAULT_INTERVAL_MS,
    }
    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        if name == "attempts":
            values["attempts"] = int(raw_value)
        elif name == "interval-ms":
            values["interval_ms"] = int(raw_value)
    return values


def _validated_flags(text: str) -> tuple[dict[str, int], list[str]]:
    flags = _parse_flags(text)
    blockers: list[str] = []
    if flags["attempts"] <= 0 or flags["attempts"] > MAX_ATTEMPTS:
        blockers.append(f"attempts_out_of_bounds:1..{MAX_ATTEMPTS}")
    if flags["interval_ms"] < 0 or flags["interval_ms"] > MAX_INTERVAL_MS:
        blockers.append(f"interval_ms_out_of_bounds:0..{MAX_INTERVAL_MS}")
    return flags, blockers


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _attempt_record(payload: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "index": index,
        "decision": payload.get("decision"),
        "dispatch_ready": bool(payload.get("dispatch_ready")),
        "packet_valid": bool(payload.get("packet_valid")),
        "packet_ready": bool(payload.get("packet_ready")),
        "packet_id": payload.get("packet_id"),
        "packet_action": payload.get("packet_action"),
        "packet_action_type": payload.get("packet_action_type"),
        "source_object_index": payload.get("source_object_index"),
        "source_sample_index": payload.get("source_sample_index"),
        "scheduled_t": payload.get("scheduled_t"),
        "current_time_ms": payload.get("current_time_ms"),
        "time_to_action_ms": payload.get("time_to_action_ms"),
        "timing_consistent": payload.get("timing_consistent") is True,
        "timing_consistency_error_ms": payload.get("timing_consistency_error_ms"),
        "selected_metadata_source": payload.get("selected_metadata_source"),
        "selected_metadata_complete": bool(payload.get("selected_metadata_complete")),
        "packet_source": payload.get("packet_source") or "unavailable",
        "stale_lease_ignored": bool(payload.get("stale_lease_ignored")),
        "focus_readiness": payload.get("focus_readiness"),
        "emergency_abort_available": payload.get("emergency_abort_available"),
        "emergency_release_available": payload.get("emergency_release_available"),
        "blockers": [str(blocker) for blocker in (payload.get("blockers") or [])],
    }


def _error_attempt(index: int, exc: Exception) -> dict[str, Any]:
    return {
        "index": index,
        "decision": "executor_dispatch_preflight_hold_no_input",
        "dispatch_ready": False,
        "packet_valid": False,
        "packet_ready": False,
        "packet_id": None,
        "packet_action": None,
        "packet_action_type": None,
        "source_object_index": None,
        "source_sample_index": None,
        "scheduled_t": None,
        "current_time_ms": None,
        "time_to_action_ms": None,
        "timing_consistent": False,
        "timing_consistency_error_ms": None,
        "selected_metadata_source": None,
        "selected_metadata_complete": False,
        "packet_source": "unavailable",
        "stale_lease_ignored": False,
        "focus_readiness": "error",
        "emergency_abort_available": "error",
        "emergency_release_available": "error",
        "blockers": [f"dispatch_preflight_error:{type(exc).__name__}"],
    }


def _action_key(attempt: dict[str, Any]) -> tuple[Any, ...] | None:
    if attempt.get("source_object_index") is not None or attempt.get("scheduled_t") is not None:
        return (
            attempt.get("packet_action"),
            attempt.get("source_object_index"),
            attempt.get("scheduled_t"),
        )
    if attempt.get("packet_id"):
        return (attempt.get("packet_id"),)
    return None


def _base_payload(
    *,
    decision: str,
    session_result: str,
    reason: str,
    attempts_requested: int,
    interval_ms: int,
    attempt_records: list[dict[str, Any]] | None = None,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    attempts = attempt_records or []
    blockers = blockers or []
    time_values = [_coerce_int(attempt.get("time_to_action_ms")) for attempt in attempts]
    time_values = [value for value in time_values if value is not None]
    ready_attempts = [attempt for attempt in attempts if attempt.get("dispatch_ready") is True]
    unique_actions = {
        key
        for key in (_action_key(attempt) for attempt in attempts if attempt.get("packet_ready") is True)
        if key is not None
    }
    first_ready = ready_attempts[0] if ready_attempts else {}
    merged_blockers = list(dict.fromkeys([*blockers, *[b for attempt in attempts for b in attempt["blockers"]]]))
    payload = {
        "schema": SHADOW_DISPATCH_SESSION_SCHEMA,
        "version": 1,
        "decision": decision,
        "session_result": session_result,
        "reason": reason,
        "attempts_requested": int(attempts_requested),
        "attempts_attempted": len(attempts),
        "attempts_ok": sum(1 for attempt in attempts if not str(attempt.get("decision") or "").endswith("_error")),
        "attempts_max": MAX_ATTEMPTS,
        "interval_ms": int(interval_ms),
        "interval_max_ms": MAX_INTERVAL_MS,
        "samples_max": MAX_SAMPLES,
        "dispatch_ready_count": sum(1 for attempt in attempts if attempt.get("dispatch_ready") is True),
        "packet_valid_count": sum(1 for attempt in attempts if attempt.get("packet_valid") is True),
        "packet_ready_count": sum(1 for attempt in attempts if attempt.get("packet_ready") is True),
        "timing_consistent_count": sum(1 for attempt in attempts if attempt.get("timing_consistent") is True),
        "metadata_complete_count": sum(1 for attempt in attempts if attempt.get("selected_metadata_complete") is True),
        "stale_lease_ignored_count": sum(1 for attempt in attempts if attempt.get("stale_lease_ignored") is True),
        "unique_action_count": len(unique_actions),
        "first_dispatch_ready_attempt_index": first_ready.get("index"),
        "first_packet_id": first_ready.get("packet_id"),
        "first_packet_action": first_ready.get("packet_action"),
        "first_time_to_action_ms": first_ready.get("time_to_action_ms"),
        "min_time_to_action_ms": min(time_values) if time_values else None,
        "max_time_to_action_ms": max(time_values) if time_values else None,
        "closest_time_to_action_ms": min(time_values, key=lambda value: abs(value)) if time_values else None,
        "attempts": attempts,
        "blockers": merged_blockers,
        **_base_flags(),
    }
    payload.update(_base_flags())
    return payload


def build_executor_shadow_dispatch_session_payload(
    text: str = "",
    *,
    decision_label: str = "executor_shadow_dispatch_session_preview_only_no_input",
    dispatch_preflight_builder: PayloadBuilder | None = None,
    sleep_fn: Sleeper | None = None,
    **dispatch_kwargs: Any,
) -> dict[str, Any]:
    try:
        flags, param_blockers = _validated_flags(text)
    except Exception as exc:
        return _base_payload(
            decision="executor_shadow_dispatch_session_hold_no_input",
            session_result="session_not_ready",
            reason="invalid_session_params",
            attempts_requested=DEFAULT_ATTEMPTS,
            interval_ms=DEFAULT_INTERVAL_MS,
            blockers=[f"invalid_session_params:{type(exc).__name__}"],
        )

    if param_blockers:
        return _base_payload(
            decision="executor_shadow_dispatch_session_hold_no_input",
            session_result="session_not_ready",
            reason="invalid_session_params",
            attempts_requested=int(flags["attempts"]),
            interval_ms=int(flags["interval_ms"]),
            blockers=param_blockers,
        )

    builder = dispatch_preflight_builder or build_executor_dispatch_preflight_payload
    sleeper = sleep_fn if sleep_fn is not None else time.sleep
    attempts: list[dict[str, Any]] = []
    for index in range(int(flags["attempts"])):
        try:
            payload = builder(text, **dispatch_kwargs)
            attempts.append(_attempt_record(payload, index))
        except Exception as exc:
            attempts.append(_error_attempt(index, exc))
        if index < int(flags["attempts"]) - 1 and int(flags["interval_ms"]) > 0:
            sleeper(int(flags["interval_ms"]) / 1000.0)

    ready_count = sum(1 for attempt in attempts if attempt.get("dispatch_ready") is True)
    if ready_count > 0:
        return _base_payload(
            decision=decision_label,
            session_result="observed_dispatch_ready_preview_only",
            reason="observed_dispatch_ready_preview_only",
            attempts_requested=int(flags["attempts"]),
            interval_ms=int(flags["interval_ms"]),
            attempt_records=attempts,
        )

    blockers = ["no_dispatch_ready_observed"]
    return _base_payload(
        decision="executor_shadow_dispatch_session_hold_no_input",
        session_result="no_dispatch_ready_observed",
        reason="no_dispatch_ready_observed",
        attempts_requested=int(flags["attempts"]),
        interval_ms=int(flags["interval_ms"]),
        attempt_records=attempts,
        blockers=blockers,
    )


def build_executor_shadow_dispatch_session_status_payload(text: str = "", **kwargs: Any) -> dict[str, Any]:
    return _base_payload(
        decision="executor_shadow_dispatch_session_status_only_no_input",
        session_result="session_not_ready",
        reason="no_persistent_shadow_session_state",
        attempts_requested=0,
        interval_ms=0,
        blockers=["no_persistent_shadow_session_state"],
    )


def print_executor_shadow_dispatch_session(
    payload: dict[str, Any],
    *,
    title: str = "osu! Executor Shadow Dispatch Session",
) -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Session result: {payload['session_result']}")
    print(f"  Reason: {payload['reason']}")
    print(f"  Attempts requested: {payload['attempts_requested']}")
    print(f"  Attempts attempted: {payload['attempts_attempted']}")
    print(f"  Attempts ok: {payload['attempts_ok']}")
    print(f"  Attempts max: {payload['attempts_max']}")
    print(f"  Interval ms: {payload['interval_ms']}")
    print(f"  Interval max ms: {payload['interval_max_ms']}")
    print(f"  Samples max: {payload['samples_max']}")
    print(f"  Dispatch ready count: {payload['dispatch_ready_count']}")
    print(f"  Packet valid count: {payload['packet_valid_count']}")
    print(f"  Packet ready count: {payload['packet_ready_count']}")
    print(f"  Timing consistent count: {payload['timing_consistent_count']}")
    print(f"  Metadata complete count: {payload['metadata_complete_count']}")
    print(f"  Stale lease ignored count: {payload['stale_lease_ignored_count']}")
    print(f"  Unique action count: {payload['unique_action_count']}")
    print(f"  First dispatch ready attempt index: {payload['first_dispatch_ready_attempt_index']}")
    print(f"  First packet id: {payload['first_packet_id']}")
    print(f"  First packet action: {payload['first_packet_action']}")
    print(f"  First time to action ms: {payload['first_time_to_action_ms']}")
    print(f"  Min time to action ms: {payload['min_time_to_action_ms']}")
    print(f"  Max time to action ms: {payload['max_time_to_action_ms']}")
    print(f"  Closest time to action ms: {payload['closest_time_to_action_ms']}")
    for attempt in payload["attempts"]:
        print(
            "  Attempt: "
            f"{attempt['index']} decision={attempt['decision']} "
            f"ready={attempt['dispatch_ready']} packet={attempt['packet_id']} "
            f"action={attempt['packet_action']} togo={attempt['time_to_action_ms']} "
            f"metadata={attempt['selected_metadata_complete']} blockers={attempt['blockers']}"
        )
    print(f"  Blockers: {payload['blockers']}")
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
