"""
osu! executor contract / dry-run action schedule - Phase 25.

Builds non-executable action schedules from trajectory points.

No live input. No cursor move/click. No SetCursorPos. No SendInput.
No mouse_event. No score submit. No VTS. No ElevenLabs. No OBS API.
No Vision. No Stardew changes. No external bot import/run.
No real execute function is defined in this module.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from nana.game.osu.aim_trajectory import (
    DEFAULT_SAMPLE_STEP_MS,
    build_aim_trajectory_payload,
)
from nana.game.osu.input_readiness import DEFAULT_LEASE_LIMIT
from nana.game.osu.router import DEFAULT_AIM_LEAD_MS


DEFAULT_EXECUTOR_DRY_RUN_PATH = Path(__file__).resolve().parent / "data" / "executor_dry_run_schedule.json"
CONTRACT_SCHEMA = "nana.osu.executor_contract.v1"
SCHEDULE_SCHEMA = "nana.osu.executor_dry_run_schedule.v1"

TrajectoryBuilder = Callable[..., dict[str, Any]]


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_executor_flags(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {
        "lead_ms": DEFAULT_AIM_LEAD_MS,
        "limit": DEFAULT_LEASE_LIMIT,
        "sample_step_ms": DEFAULT_SAMPLE_STEP_MS,
        "path": None,
    }
    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        if name == "lead-ms":
            values["lead_ms"] = int(raw_value)
        elif name == "limit":
            values["limit"] = int(raw_value)
        elif name == "sample-step-ms":
            values["sample_step_ms"] = int(raw_value)
        elif name == "path":
            values["path"] = raw_value
        elif name in {"adapter-enabled", "zone-enabled", "osu-zone-enabled"}:
            values["adapter_enabled"] = _is_truthy(raw_value)
    if int(values["lead_ms"]) < 0:
        raise ValueError("lead_ms_must_be_non_negative")
    if int(values["limit"]) <= 0:
        raise ValueError("limit_must_be_positive")
    if int(values["sample_step_ms"]) <= 0:
        raise ValueError("sample_step_ms_must_be_positive")
    return values


def _action_type(point: dict[str, Any]) -> str:
    hint = point.get("action_hint")
    if hint in {"tap_candidate", "slider_follow_candidate", "spinner_candidate"}:
        return str(hint)
    return "move_to_candidate"


def _scheduled_time(point: dict[str, Any]) -> int | None:
    action_type = _action_type(point)
    value = point.get("aim_t") if action_type == "move_to_candidate" else point.get("hit_t")
    if value is None:
        value = point.get("aim_t") or point.get("hit_t")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _relative_time(scheduled_t: int | None, current_time_ms: Any) -> int | None:
    if scheduled_t is None or current_time_ms is None:
        return None
    try:
        return int(scheduled_t) - int(current_time_ms)
    except (TypeError, ValueError):
        return None


def _timing_state(relative_ms: int | None) -> str:
    if relative_ms is None:
        return "unknown"
    if relative_ms < 0:
        return "late"
    if relative_ms <= 500:
        return "immediate"
    return "future"


def _build_action(point: dict[str, Any], *, action_index: int, current_time_ms: Any) -> dict[str, Any]:
    scheduled_t = _scheduled_time(point)
    relative_ms = _relative_time(scheduled_t, current_time_ms)
    return {
        "action_id": f"dryrun-{action_index:04d}",
        "source_object_index": point.get("object_index"),
        "action_type": _action_type(point),
        "scheduled_t": scheduled_t,
        "relative_ms_from_now": relative_ms,
        "time_until_action_ms": relative_ms,
        "playfield": dict(point.get("playfield") or {"x": None, "y": None}),
        "screen": point.get("screen"),
        "reason": point.get("action_hint") or "move_only",
        "action_hint": point.get("action_hint") or "move_only",
        "timing_state": _timing_state(relative_ms),
        "executable": False,
    }


def build_executor_contract_preview(text: str = "") -> dict[str, Any]:
    flags = _parse_executor_flags(text)
    return {
        "schema": CONTRACT_SCHEMA,
        "version": 1,
        "decision": "executor_contract_preview_only_no_input",
        "scope": "osu_executor_future_contract",
        "action_types": [
            "move_to_candidate",
            "tap_candidate",
            "slider_follow_candidate",
            "spinner_candidate",
        ],
        "required_action_fields": [
            "action_id",
            "source_object_index",
            "action_type",
            "scheduled_t",
            "relative_ms_from_now",
            "playfield",
            "screen",
            "reason",
            "timing_state",
            "executable",
        ],
        "timing_states": ["late", "immediate", "future", "unknown"],
        "trajectory_source": "nana.osu.aim_trajectory.v1",
        "lead_ms": int(flags["lead_ms"]),
        "limit": int(flags["limit"]),
        "sample_step_ms": int(flags["sample_step_ms"]),
        "executable": False,
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
    }


def build_executor_dry_run_schedule(
    text: str = "",
    *,
    trajectory_builder: TrajectoryBuilder | None = None,
    **trajectory_kwargs: Any,
) -> dict[str, Any]:
    flags = _parse_executor_flags(text)
    trajectory_builder = trajectory_builder or build_aim_trajectory_payload
    try:
        trajectory = trajectory_builder(text, **trajectory_kwargs)
    except Exception as exc:
        trajectory = {
            "schema": "nana.osu.aim_trajectory.v1",
            "version": 1,
            "decision": "trajectory_hold_no_points",
            "input_ready": False,
            "readiness": "not_ready",
            "benchmark_recommendation": "not_ready",
            "current_time_ms": None,
            "trajectory_points": [],
            "points": [],
            "blockers": [f"executor_trajectory_error:{type(exc).__name__}"],
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "vision_call": False,
        }

    points = list(trajectory.get("trajectory_points") or trajectory.get("points") or [])
    current_time_ms = trajectory.get("current_time_ms")
    actions = [
        _build_action(point, action_index=index, current_time_ms=current_time_ms)
        for index, point in enumerate(points)
    ]
    late_count = sum(1 for action in actions if action["timing_state"] == "late")
    immediate_count = sum(1 for action in actions if action["timing_state"] == "immediate")
    future_count = sum(1 for action in actions if action["timing_state"] == "future")
    unknown_count = sum(1 for action in actions if action["timing_state"] == "unknown")
    first_action_t = actions[0]["scheduled_t"] if actions else None
    last_action_t = actions[-1]["scheduled_t"] if actions else None
    advisory_ready = bool(trajectory.get("input_ready"))

    if not actions:
        decision = "executor_dry_run_hold_no_points"
    elif advisory_ready:
        decision = "executor_dry_run_schedule_ready_no_input"
    else:
        decision = "executor_dry_run_schedule_not_input_ready"

    return {
        "schema": SCHEDULE_SCHEMA,
        "version": 1,
        "decision": decision,
        "source_trajectory_schema": trajectory.get("schema"),
        "source_trajectory_decision": trajectory.get("decision"),
        "input_ready": advisory_ready,
        "advisory_ready": advisory_ready,
        "readiness": trajectory.get("readiness") or "not_ready",
        "benchmark_recommendation": trajectory.get("benchmark_recommendation") or "not_ready",
        "executable": False,
        "lead_ms": int(flags["lead_ms"]),
        "limit": int(flags["limit"]),
        "sample_step_ms": int(flags["sample_step_ms"]),
        "current_time_ms": current_time_ms,
        "schedule_count": len(actions),
        "late_count": late_count,
        "immediate_count": immediate_count,
        "future_count": future_count,
        "unknown_count": unknown_count,
        "first_action_t": first_action_t,
        "last_action_t": last_action_t,
        "actions": actions,
        "blockers": list(trajectory.get("blockers") or trajectory.get("readiness_blockers") or []),
        "created_at": time.time(),
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
    }


def resolve_executor_dry_run_path(raw_path: str | None) -> Path:
    if raw_path:
        return Path(raw_path)
    return DEFAULT_EXECUTOR_DRY_RUN_PATH


def write_executor_dry_run_schedule(schedule: dict[str, Any], path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    output = dict(schedule)
    output["written_at"] = time.time()
    output["output_path"] = str(path)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
    return {
        "decision": "executor_dry_run_file_write_no_input",
        "path": str(path),
        "bytes_written": path.stat().st_size,
        "payload": output,
        "file_write": True,
        "executable": False,
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
    }


def build_executor_dry_run_write(text: str = "", **kwargs: Any) -> dict[str, Any]:
    flags = _parse_executor_flags(text)
    schedule = build_executor_dry_run_schedule(text, **kwargs)
    output_path = resolve_executor_dry_run_path(flags.get("path"))
    return write_executor_dry_run_schedule(schedule, output_path)


def print_executor_contract_preview(contract: dict[str, Any]) -> None:
    print("osu! Executor Contract Preview")
    print(f"  Decision: {contract['decision']}")
    print(f"  Schema: {contract['schema']}")
    print(f"  Action types: {contract['action_types']}")
    print(f"  Timing states: {contract['timing_states']}")
    print(f"  Trajectory source: {contract['trajectory_source']}")
    print(f"  Lead ms: {contract['lead_ms']}")
    print(f"  Limit: {contract['limit']}")
    print(f"  Sample step ms: {contract['sample_step_ms']}")
    print(f"  Executable: {contract['executable']}")
    print(f"  Real input allowed: {contract['real_input_allowed']}")
    print(f"  Submit allowed: {contract['submit_allowed']}")
    print(f"  Real input: {contract['real_input']}")
    print(f"  Submit: {contract['submit']}")
    print(f"  VTS call: {contract['vts_call']}")
    print(f"  Voice call: {contract['voice_call']}")
    print(f"  OBS call: {contract['obs_call']}")
    print(f"  Vision call: {contract['vision_call']}")


def print_executor_dry_run_schedule(schedule: dict[str, Any], *, max_actions: int = 5) -> None:
    print("osu! Executor Dry-Run Schedule")
    print(f"  Decision: {schedule['decision']}")
    print(f"  Source trajectory decision: {schedule['source_trajectory_decision']}")
    print(f"  Input ready: {schedule['input_ready']}")
    print(f"  Advisory ready: {schedule['advisory_ready']}")
    print(f"  Executable: {schedule['executable']}")
    print(f"  Schedule count: {schedule['schedule_count']}")
    print(f"  Late count: {schedule['late_count']}")
    print(f"  Immediate count: {schedule['immediate_count']}")
    print(f"  Future count: {schedule['future_count']}")
    print(f"  Unknown count: {schedule['unknown_count']}")
    print(f"  First action t: {schedule['first_action_t']}")
    print(f"  Last action t: {schedule['last_action_t']}")
    print(f"  Blockers: {schedule['blockers']}")
    for action in schedule["actions"][:max_actions]:
        screen = action.get("screen")
        screen_text = "null" if screen is None else f"{screen.get('x')},{screen.get('y')}"
        playfield = action.get("playfield") or {}
        print(
            "  Action: "
            f"{action.get('action_id')} {action.get('action_type')} "
            f"obj={action.get('source_object_index')} t={action.get('scheduled_t')} "
            f"rel={action.get('relative_ms_from_now')} state={action.get('timing_state')} "
            f"pf=({playfield.get('x')},{playfield.get('y')}) screen={screen_text} "
            f"executable={action.get('executable')}"
        )
    print(f"  Real input allowed: {schedule['real_input_allowed']}")
    print(f"  Submit allowed: {schedule['submit_allowed']}")
    print(f"  Real input: {schedule['real_input']}")
    print(f"  Submit: {schedule['submit']}")
    print(f"  VTS call: {schedule['vts_call']}")
    print(f"  Voice call: {schedule['voice_call']}")
    print(f"  OBS call: {schedule['obs_call']}")
    print(f"  Vision call: {schedule['vision_call']}")


def print_executor_dry_run_write(result: dict[str, Any]) -> None:
    payload = result["payload"]
    print("osu! Executor Dry-Run Write")
    print(f"  Decision: {result['decision']}")
    print(f"  Payload decision: {payload['decision']}")
    print(f"  Path: {result['path']}")
    print(f"  Bytes written: {result['bytes_written']}")
    print(f"  Schedule count: {payload['schedule_count']}")
    print(f"  Executable: {result['executable']}")
    print(f"  File write: {result['file_write']}")
    print(f"  Real input allowed: {result['real_input_allowed']}")
    print(f"  Submit allowed: {result['submit_allowed']}")
    print(f"  Real input: {result['real_input']}")
    print(f"  Submit: {result['submit']}")
    print(f"  VTS call: {result['vts_call']}")
    print(f"  Voice call: {result['voice_call']}")
    print(f"  OBS call: {result['obs_call']}")
    print(f"  Vision call: {result['vision_call']}")
