"""
osu! executor safety preflight - Phase 26.

Final no-input review gate before any future discussion of real input.

No live input. No cursor move/click. No SetCursorPos. No SendInput.
No mouse_event. No score submit. No VTS. No ElevenLabs. No OBS API.
No Vision. No Stardew changes. No external bot import/run.
No real execute function is defined in this module.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from nana.game.osu.executor_dry_run import build_executor_dry_run_schedule
from nana.game.osu.input_safety import (
    build_input_safety_status,
    read_emergency_abort_readiness,
    read_emergency_release_readiness,
    read_osu_focus_readiness,
)


PREFLIGHT_SCHEMA = "nana.osu.executor_preflight.v1"
REAL_INPUT_EXECUTOR_ENV = "NANA_OSU_REAL_INPUT_EXECUTOR_ENABLED"
REAL_INPUT_EXECUTOR_TOKEN = "I_APPROVE_OSU_REAL_INPUT_EXECUTOR"

ScheduleBuilder = Callable[..., dict[str, Any]]
ReadinessReader = Callable[[], dict[str, Any]]


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_operator_token(text: str) -> str | None:
    for token in str(text or "").split()[1:]:
        if token.startswith("--operator-approval-token="):
            return token.split("=", 1)[1].strip()
    return None


def _safe_schedule(text: str, *, builder: ScheduleBuilder, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        return builder(text, **kwargs)
    except Exception as exc:
        return {
            "schema": "nana.osu.executor_dry_run_schedule.v1",
            "version": 1,
            "decision": "executor_dry_run_hold_no_points",
            "source_trajectory_schema": None,
            "source_trajectory_decision": "trajectory_hold_no_points",
            "input_ready": False,
            "advisory_ready": False,
            "readiness": "not_ready",
            "benchmark_recommendation": "not_ready",
            "executable": False,
            "current_time_ms": None,
            "schedule_count": 0,
            "actions": [],
            "blockers": [f"preflight_schedule_error:{type(exc).__name__}"],
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "vision_call": False,
        }


def _all_actions_non_executable(schedule: dict[str, Any]) -> bool:
    actions = list(schedule.get("actions") or [])
    return bool(schedule.get("executable") is False) and all(action.get("executable") is False for action in actions)


def _first_relative_ms(schedule: dict[str, Any]) -> int | None:
    actions = list(schedule.get("actions") or [])
    if not actions:
        return None
    value = actions[0].get("relative_ms_from_now")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def build_executor_preflight_payload(
    text: str = "",
    *,
    schedule_builder: ScheduleBuilder | None = None,
    focus_reader: ReadinessReader | None = None,
    release_reader: ReadinessReader | None = None,
    abort_reader: ReadinessReader | None = None,
    **schedule_kwargs: Any,
) -> dict[str, Any]:
    """
    Build a no-input preflight payload.

    The env and token are only review gates. They never enable execution in
    Phase 26, and executable/real_input remain false.
    """
    schedule_builder = schedule_builder or build_executor_dry_run_schedule
    schedule = _safe_schedule(text, builder=schedule_builder, kwargs=schedule_kwargs)
    safety = build_input_safety_status(
        focus_reader=focus_reader or read_osu_focus_readiness,
        release_reader=release_reader or read_emergency_release_readiness,
        abort_reader=abort_reader or read_emergency_abort_readiness,
    )

    raw_env = os.environ.get(REAL_INPUT_EXECUTOR_ENV)
    env_gate_present = raw_env is not None and str(raw_env).strip() != ""
    env_gate_enabled = _is_truthy(raw_env)
    operator_token = _parse_operator_token(text)
    token_present = bool(operator_token)
    token_valid = operator_token == REAL_INPUT_EXECUTOR_TOKEN

    readiness_ready = schedule.get("readiness") == "ready"
    benchmark_ready = schedule.get("benchmark_recommendation") == "ready_for_future_input_benchmark"
    trajectory_ready = schedule.get("source_trajectory_decision") == "trajectory_ready_preview_only"
    schedule_ready = schedule.get("decision") == "executor_dry_run_schedule_ready_no_input"
    all_non_executable = _all_actions_non_executable(schedule)
    schedule_count = int(schedule.get("schedule_count") or 0)

    blockers: list[str] = []
    if not env_gate_enabled:
        blockers.append("missing_or_disabled_env_gate")
    if not token_valid:
        blockers.append("missing_or_invalid_operator_token")
    if not readiness_ready:
        blockers.append("readiness_not_ready")
    if not benchmark_ready:
        blockers.append("benchmark_not_ready")
    if not trajectory_ready:
        blockers.append("trajectory_not_ready")
    if not schedule_ready:
        blockers.append("schedule_not_ready")
    if schedule_count <= 0:
        blockers.append("schedule_empty")
    if not all_non_executable:
        blockers.append("executable_action_present")
    for blocker in schedule.get("blockers") or []:
        if blocker not in blockers:
            blockers.append(str(blocker))

    chain_ready = (
        readiness_ready
        and benchmark_ready
        and trajectory_ready
        and schedule_ready
        and schedule_count > 0
        and all_non_executable
    )
    gate_ready = env_gate_enabled and token_valid

    if gate_ready and chain_ready:
        decision = "executor_preflight_ready_for_manual_review_no_input"
        ready_for_manual_review = True
    elif not gate_ready:
        decision = "executor_preflight_hold_no_input"
        ready_for_manual_review = False
    else:
        decision = "executor_preflight_blocked_no_input"
        ready_for_manual_review = False

    return {
        "schema": PREFLIGHT_SCHEMA,
        "version": 1,
        "decision": decision,
        "env_gate_name": REAL_INPUT_EXECUTOR_ENV,
        "env_gate_present": env_gate_present,
        "env_gate_enabled": env_gate_enabled,
        "token_present": token_present,
        "token_valid": token_valid,
        "readiness_ready": readiness_ready,
        "benchmark_ready": benchmark_ready,
        "trajectory_ready": trajectory_ready,
        "schedule_ready": schedule_ready,
        "all_actions_non_executable": all_non_executable,
        "emergency_abort_available": safety.get("emergency_abort_available") or "unavailable_no_helper",
        "emergency_abort_strategy": safety.get("abort_strategy") or "preview_only",
        "abort_command_available": bool(safety.get("abort_command_available")),
        "abort_flag_path": safety.get("abort_flag_path"),
        "emergency_release_available": safety.get("emergency_release_available") or "unavailable_no_helper",
        "emergency_release_strategy": safety.get("release_strategy") or "preview_only",
        "release_command_available": bool(safety.get("release_command_available")),
        "focus_readiness": safety.get("focus_readiness") or "unavailable_no_helper",
        "window_focus_readiness": safety.get("window_focus_readiness") or "unavailable_no_helper",
        "window_title": safety.get("window_title"),
        "window_class": safety.get("window_class"),
        "process_name": safety.get("process_name"),
        "is_osu_window": bool(safety.get("is_osu_window")),
        "input_safety": safety,
        "schedule_count": schedule_count,
        "schedule_current_time_ms": schedule.get("current_time_ms"),
        "first_action_t": schedule.get("first_action_t"),
        "last_action_t": schedule.get("last_action_t"),
        "first_action_relative_ms": _first_relative_ms(schedule),
        "source_schedule_decision": schedule.get("decision"),
        "source_trajectory_decision": schedule.get("source_trajectory_decision"),
        "readiness": schedule.get("readiness") or "not_ready",
        "benchmark_recommendation": schedule.get("benchmark_recommendation") or "not_ready",
        "blockers": blockers,
        "ready_for_manual_review": ready_for_manual_review,
        "executable": False,
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
        "created_at": time.time(),
    }


def print_executor_preflight_status(payload: dict[str, Any], *, title: str = "osu! Executor Preflight Status") -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Env gate: {payload['env_gate_name']}")
    print(f"  Env gate present: {payload['env_gate_present']}")
    print(f"  Env gate enabled: {payload['env_gate_enabled']}")
    print(f"  Token present: {payload['token_present']}")
    print(f"  Token valid: {payload['token_valid']}")
    print(f"  Readiness ready: {payload['readiness_ready']}")
    print(f"  Benchmark ready: {payload['benchmark_ready']}")
    print(f"  Trajectory ready: {payload['trajectory_ready']}")
    print(f"  Schedule ready: {payload['schedule_ready']}")
    print(f"  All actions non-executable: {payload['all_actions_non_executable']}")
    print(f"  Emergency abort available: {payload['emergency_abort_available']}")
    print(f"  Emergency abort strategy: {payload.get('emergency_abort_strategy')}")
    print(f"  Emergency release available: {payload['emergency_release_available']}")
    print(f"  Emergency release strategy: {payload.get('emergency_release_strategy')}")
    print(f"  Focus readiness: {payload['focus_readiness']}")
    print(f"  Window title: {payload.get('window_title')}")
    print(f"  Window class: {payload.get('window_class')}")
    print(f"  Process name: {payload.get('process_name')}")
    print(f"  Schedule count: {payload['schedule_count']}")
    print(f"  Schedule current time ms: {payload['schedule_current_time_ms']}")
    print(f"  First action t: {payload['first_action_t']}")
    print(f"  Last action t: {payload['last_action_t']}")
    print(f"  First action relative ms: {payload['first_action_relative_ms']}")
    print(f"  Source schedule decision: {payload['source_schedule_decision']}")
    print(f"  Source trajectory decision: {payload['source_trajectory_decision']}")
    print(f"  Blockers: {payload['blockers']}")
    print(f"  Ready for manual review: {payload['ready_for_manual_review']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input allowed: {payload['real_input_allowed']}")
    print(f"  Submit allowed: {payload['submit_allowed']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
    print(f"  VTS call: {payload['vts_call']}")
    print(f"  Voice call: {payload['voice_call']}")
    print(f"  OBS call: {payload['obs_call']}")
    print(f"  Vision call: {payload['vision_call']}")
