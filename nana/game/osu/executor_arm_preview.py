"""
osu! executor arm preview - Phase 30.

Selects one future/manual-review action candidate from the existing no-input
preflight and dry-run schedule. This module does not arm persistent state, does
not execute input, does not call SendInput, and does not invoke emergency
release.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from nana.game.osu.executor_dry_run import build_executor_dry_run_schedule
from nana.game.osu.executor_preflight import build_executor_preflight_payload


ARM_PREVIEW_SCHEMA = "nana.osu.executor_arm_preview.v1"

PayloadBuilder = Callable[..., dict[str, Any]]


def _base_flags() -> dict[str, bool]:
    return {
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


def _empty_selection() -> dict[str, Any]:
    return {
        "selected_action_id": None,
        "selected_action_type": None,
        "selected_source_object_index": None,
        "selected_scheduled_t": None,
        "selected_relative_ms": None,
        "selected_timing_state": None,
        "selected_playfield": None,
        "selected_screen": None,
    }


def _safe_preflight(
    text: str,
    *,
    builder: PayloadBuilder,
    schedule_builder: PayloadBuilder,
    schedule_kwargs: dict[str, Any],
) -> dict[str, Any]:
    try:
        return builder(text, schedule_builder=schedule_builder, **schedule_kwargs)
    except TypeError:
        return builder(text, **schedule_kwargs)
    except Exception as exc:
        return {
            "schema": "nana.osu.executor_preflight.v1",
            "version": 1,
            "decision": "executor_preflight_error_no_input",
            "ready_for_manual_review": False,
            "focus_readiness": "unavailable_no_helper",
            "emergency_abort_available": "unavailable_no_helper",
            "emergency_release_available": "unavailable_no_helper",
            "emergency_release_strategy": "preview_only",
            "schedule_count": 0,
            "blockers": [f"preflight_error:{type(exc).__name__}"],
            "executable": False,
            "real_input": False,
            "submit": False,
        }


def _safe_schedule(text: str, *, builder: PayloadBuilder, schedule_kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        return builder(text, **schedule_kwargs)
    except Exception as exc:
        return {
            "schema": "nana.osu.executor_dry_run_schedule.v1",
            "version": 1,
            "decision": "executor_dry_run_hold_no_points",
            "schedule_count": 0,
            "late_count": 0,
            "immediate_count": 0,
            "future_count": 0,
            "unknown_count": 0,
            "actions": [],
            "blockers": [f"schedule_error:{type(exc).__name__}"],
            "executable": False,
            "real_input": False,
            "submit": False,
        }


def _count_actions(schedule: dict[str, Any], state: str) -> int:
    value = schedule.get(f"{state}_count")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0
    return sum(1 for action in schedule.get("actions") or [] if action.get("timing_state") == state)


def _relative_ms(action: dict[str, Any]) -> int | None:
    value = action.get("relative_ms_from_now")
    if value is None:
        value = action.get("time_until_action_ms")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_action_candidate(schedule: dict[str, Any]) -> dict[str, Any] | None:
    for action in schedule.get("actions") or []:
        if action.get("timing_state") not in {"immediate", "future"}:
            continue
        if action.get("executable") is not False:
            continue
        return dict(action)
    return None


def _selection_from_action(action: dict[str, Any] | None) -> dict[str, Any]:
    if action is None:
        return _empty_selection()
    return {
        "selected_action_id": action.get("action_id"),
        "selected_action_type": action.get("action_type"),
        "selected_source_object_index": action.get("source_object_index"),
        "selected_scheduled_t": action.get("scheduled_t"),
        "selected_relative_ms": _relative_ms(action),
        "selected_timing_state": action.get("timing_state"),
        "selected_playfield": action.get("playfield"),
        "selected_screen": action.get("screen"),
    }


def _build_payload(
    text: str,
    *,
    decision_when_ready: str,
    decision_when_hold: str,
    preflight_builder: PayloadBuilder | None = None,
    schedule_builder: PayloadBuilder | None = None,
    **schedule_kwargs: Any,
) -> dict[str, Any]:
    schedule_builder = schedule_builder or build_executor_dry_run_schedule
    preflight = _safe_preflight(
        text,
        builder=preflight_builder or build_executor_preflight_payload,
        schedule_builder=schedule_builder,
        schedule_kwargs=schedule_kwargs,
    )
    schedule = _safe_schedule(text, builder=schedule_builder, schedule_kwargs=schedule_kwargs)

    ready_for_manual_review = bool(preflight.get("ready_for_manual_review"))
    preflight_blockers = [str(blocker) for blocker in (preflight.get("blockers") or [])]
    candidate = _safe_action_candidate(schedule) if ready_for_manual_review else None

    if not ready_for_manual_review:
        decision = decision_when_hold
        armable = False
        blockers = preflight_blockers or ["preflight_not_ready"]
        reason = blockers[0]
    elif candidate is None:
        decision = decision_when_hold
        armable = False
        blockers = ["no_safe_action_to_arm"]
        reason = "no_safe_action_to_arm"
    else:
        decision = decision_when_ready
        armable = True
        blockers = []
        reason = "selected_action_ready_for_manual_review"

    return {
        "schema": ARM_PREVIEW_SCHEMA,
        "version": 1,
        "decision": decision,
        "reason": reason,
        "armable": armable,
        **_base_flags(),
        **_selection_from_action(candidate),
        "preflight_decision": preflight.get("decision"),
        "ready_for_manual_review": ready_for_manual_review,
        "focus_readiness": preflight.get("focus_readiness") or "unavailable_no_helper",
        "emergency_abort_available": preflight.get("emergency_abort_available") or "unavailable_no_helper",
        "emergency_release_available": preflight.get("emergency_release_available") or "unavailable_no_helper",
        "release_strategy": preflight.get("emergency_release_strategy") or preflight.get("release_strategy") or "preview_only",
        "schedule_decision": schedule.get("decision"),
        "schedule_count": int(schedule.get("schedule_count") or len(schedule.get("actions") or [])),
        "late_count": _count_actions(schedule, "late"),
        "immediate_count": _count_actions(schedule, "immediate"),
        "future_count": _count_actions(schedule, "future"),
        "blockers": blockers,
        "preflight_blockers": preflight_blockers,
        "schedule_blockers": [str(blocker) for blocker in (schedule.get("blockers") or [])],
        "created_at": time.time(),
    }


def build_executor_arm_preview_payload(
    text: str = "",
    *,
    preflight_builder: PayloadBuilder | None = None,
    schedule_builder: PayloadBuilder | None = None,
    **schedule_kwargs: Any,
) -> dict[str, Any]:
    return _build_payload(
        text,
        decision_when_ready="executor_arm_preview_ready_no_input",
        decision_when_hold="executor_arm_preview_hold_no_input",
        preflight_builder=preflight_builder,
        schedule_builder=schedule_builder,
        **schedule_kwargs,
    )


def build_executor_arm_status_payload(
    text: str = "",
    *,
    preflight_builder: PayloadBuilder | None = None,
    schedule_builder: PayloadBuilder | None = None,
    **schedule_kwargs: Any,
) -> dict[str, Any]:
    payload = _build_payload(
        text,
        decision_when_ready="executor_arm_status_only_no_input",
        decision_when_hold="executor_arm_status_only_no_input",
        preflight_builder=preflight_builder,
        schedule_builder=schedule_builder,
        **schedule_kwargs,
    )
    payload["decision"] = "executor_arm_status_only_no_input"
    return payload


def print_executor_arm_preview(payload: dict[str, Any], *, title: str = "osu! Executor Arm Preview") -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Reason: {payload.get('reason')}")
    print(f"  Armable: {payload['armable']}")
    print(f"  Armed: {payload['armed']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Selected action id: {payload['selected_action_id']}")
    print(f"  Selected action type: {payload['selected_action_type']}")
    print(f"  Selected source object index: {payload['selected_source_object_index']}")
    print(f"  Selected scheduled t: {payload['selected_scheduled_t']}")
    print(f"  Selected relative ms: {payload['selected_relative_ms']}")
    print(f"  Selected timing state: {payload['selected_timing_state']}")
    print(f"  Selected playfield: {payload['selected_playfield']}")
    print(f"  Selected screen: {payload['selected_screen']}")
    print(f"  Preflight decision: {payload['preflight_decision']}")
    print(f"  Ready for manual review: {payload['ready_for_manual_review']}")
    print(f"  Focus readiness: {payload['focus_readiness']}")
    print(f"  Emergency abort available: {payload['emergency_abort_available']}")
    print(f"  Emergency release available: {payload['emergency_release_available']}")
    print(f"  Release strategy: {payload['release_strategy']}")
    print(f"  Schedule count: {payload['schedule_count']}")
    print(f"  Late count: {payload['late_count']}")
    print(f"  Immediate count: {payload['immediate_count']}")
    print(f"  Future count: {payload['future_count']}")
    print(f"  Blockers: {payload['blockers']}")
    print(f"  Real input allowed: {payload['real_input_allowed']}")
    print(f"  Submit allowed: {payload['submit_allowed']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
    print(f"  Gameplay input: {payload['gameplay_input']}")
    print(f"  Key down sent: {payload['key_down_sent']}")
    print(f"  Key up sent: {payload['key_up_sent']}")
    print(f"  Cursor move: {payload['cursor_move']}")
    print(f"  Click: {payload['click']}")
    print(f"  VTS call: {payload['vts_call']}")
    print(f"  Voice call: {payload['voice_call']}")
    print(f"  OBS call: {payload['obs_call']}")
    print(f"  Vision call: {payload['vision_call']}")
