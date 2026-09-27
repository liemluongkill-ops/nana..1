"""
osu! executor simulated tick - Phase 32.

Consumes the no-input armed lease preview and decides what would happen at this
tick. This module never executes input, never calls SendInput, never invokes
emergency release, and never marks anything as armed or executable.
"""

from __future__ import annotations

from typing import Any, Callable

from nana.game.osu.executor_armed_lease import (
    build_executor_armed_lease_preview_payload,
    build_executor_armed_lease_status_payload,
)
from nana.game.osu.executor_dry_run import build_executor_dry_run_schedule
from nana.game.osu.executor_preflight import REAL_INPUT_EXECUTOR_TOKEN


SIM_TICK_SCHEMA = "nana.osu.executor_simulated_tick.v1"
DEFAULT_EARLY_WINDOW_MS = 80
DEFAULT_LATE_WINDOW_MS = 60

PayloadBuilder = Callable[..., dict[str, Any]]
Clock = Callable[[], float]


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


def _parse_flags(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {
        "operator_approval_token": None,
        "early_window_ms": DEFAULT_EARLY_WINDOW_MS,
        "late_window_ms": DEFAULT_LATE_WINDOW_MS,
    }
    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        if name == "operator-approval-token":
            values["operator_approval_token"] = raw_value.strip()
        elif name == "early-window-ms":
            values["early_window_ms"] = int(raw_value)
        elif name == "late-window-ms":
            values["late_window_ms"] = int(raw_value)
    if int(values["early_window_ms"]) < 0:
        raise ValueError("early_window_ms_must_be_non_negative")
    if int(values["late_window_ms"]) < 0:
        raise ValueError("late_window_ms_must_be_non_negative")
    return values


def _empty_tick_fields() -> dict[str, Any]:
    return {
        "lease_state": "no_lease",
        "lease_id": None,
        "lease_remaining_ms": 0,
        "selected_action_id": None,
        "selected_action_type": None,
        "selected_source_object_index": None,
        "selected_scheduled_t": None,
        "selected_timing_state": None,
        "selected_playfield": None,
        "selected_screen": None,
        "current_time_ms": None,
        "time_to_action_ms": None,
        "armed_preview": False,
        "armable": False,
        "focus_readiness": "unavailable_no_lease",
        "emergency_abort_available": "unavailable_no_lease",
        "emergency_release_available": "unavailable_no_lease",
        "preflight_decision": "not_checked_no_lease",
        "status_recheck_mode": "snapshot",
        "lease_refresh_performed": False,
        "stale_lease_ignored": False,
        "stale_lease_state": None,
    }


def _required_payload(
    *,
    decision: str,
    tick_decision: str,
    reason: str,
    early_window_ms: int,
    late_window_ms: int,
    blockers: list[str] | None = None,
    lease_status: dict[str, Any] | None = None,
    current_time_ms: int | None = None,
    time_to_action_ms: int | None = None,
    lease_refresh_performed: bool = False,
    stale_lease_ignored: bool = False,
    stale_lease_state: str | None = None,
) -> dict[str, Any]:
    status = lease_status or {}
    payload = {
        "schema": SIM_TICK_SCHEMA,
        "version": 1,
        "decision": decision,
        "tick_decision": tick_decision,
        "reason": reason,
        **_empty_tick_fields(),
        "early_window_ms": int(early_window_ms),
        "late_window_ms": int(late_window_ms),
        "would_execute": tick_decision == "would_execute_preview_only",
        "would_wait": tick_decision == "would_wait",
        "would_hold": tick_decision == "would_hold",
        "would_skip_late": tick_decision == "would_skip_late",
        "blockers": blockers or [],
        **_base_flags(),
    }
    for key in (
        "lease_state",
        "lease_id",
        "lease_remaining_ms",
        "selected_action_id",
        "selected_action_type",
        "selected_source_object_index",
        "selected_scheduled_t",
        "selected_timing_state",
        "selected_playfield",
        "selected_screen",
        "armed_preview",
        "armable",
        "focus_readiness",
        "emergency_abort_available",
        "emergency_release_available",
        "preflight_decision",
        "status_recheck_mode",
        "lease_refresh_performed",
        "stale_lease_ignored",
        "stale_lease_state",
    ):
        if key in status:
            payload[key] = status.get(key)
    payload["current_time_ms"] = current_time_ms
    payload["time_to_action_ms"] = time_to_action_ms
    payload["lease_refresh_performed"] = bool(lease_refresh_performed or payload.get("lease_refresh_performed"))
    payload["stale_lease_ignored"] = bool(stale_lease_ignored or payload.get("stale_lease_ignored"))
    payload["stale_lease_state"] = stale_lease_state or payload.get("stale_lease_state")
    payload.update(_base_flags())
    return payload


def _safe_status(
    text: str,
    *,
    status_builder: PayloadBuilder | None,
    preflight_builder: PayloadBuilder | None,
    schedule_builder: PayloadBuilder | None,
    clock: Clock | None,
    schedule_kwargs: dict[str, Any],
) -> dict[str, Any]:
    builder = status_builder or build_executor_armed_lease_status_payload
    kwargs = dict(schedule_kwargs)
    if preflight_builder is not None:
        kwargs["preflight_builder"] = preflight_builder
    if schedule_builder is not None:
        kwargs["schedule_builder"] = schedule_builder
    if clock is not None:
        kwargs["clock"] = clock
    return builder(text, **kwargs)


def _safe_preview(
    text: str,
    *,
    preview_builder: PayloadBuilder | None,
    preflight_builder: PayloadBuilder | None,
    schedule_builder: PayloadBuilder | None,
    clock: Clock | None,
    schedule_kwargs: dict[str, Any],
) -> dict[str, Any]:
    builder = preview_builder or build_executor_armed_lease_preview_payload
    kwargs = dict(schedule_kwargs)
    if preflight_builder is not None:
        kwargs["preflight_builder"] = preflight_builder
    if schedule_builder is not None:
        kwargs["schedule_builder"] = schedule_builder
    if clock is not None:
        kwargs["clock"] = clock
    return builder(text, **kwargs)


def _safe_schedule(text: str, *, schedule_builder: PayloadBuilder | None, schedule_kwargs: dict[str, Any]) -> dict[str, Any]:
    builder = schedule_builder or build_executor_dry_run_schedule
    try:
        return builder(text, **schedule_kwargs)
    except Exception as exc:
        return {
            "schema": "nana.osu.executor_dry_run_schedule.v1",
            "decision": "executor_dry_run_hold_no_points",
            "current_time_ms": None,
            "blockers": [f"sim_tick_schedule_error:{type(exc).__name__}"],
            "executable": False,
            "real_input": False,
            "submit": False,
        }


def _token_valid(text: str) -> bool:
    return _parse_flags(text).get("operator_approval_token") == REAL_INPUT_EXECUTOR_TOKEN


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _current_time_from_schedule(schedule: dict[str, Any]) -> int | None:
    if schedule.get("current_time_ms") is not None:
        return _coerce_int(schedule.get("current_time_ms"))
    return _coerce_int(schedule.get("schedule_current_time_ms"))


def build_executor_simulated_tick_payload(
    text: str = "",
    *,
    decision_label: str = "executor_sim_tick_preview_only_no_input",
    status_builder: PayloadBuilder | None = None,
    preview_builder: PayloadBuilder | None = None,
    preflight_builder: PayloadBuilder | None = None,
    schedule_builder: PayloadBuilder | None = None,
    clock: Clock | None = None,
    **schedule_kwargs: Any,
) -> dict[str, Any]:
    flags = _parse_flags(text)
    early_window_ms = int(flags["early_window_ms"])
    late_window_ms = int(flags["late_window_ms"])

    try:
        lease_status = _safe_status(
            text,
            status_builder=status_builder,
            preflight_builder=preflight_builder,
            schedule_builder=schedule_builder,
            clock=clock,
            schedule_kwargs=schedule_kwargs,
        )
    except Exception as exc:
        return _required_payload(
            decision="executor_sim_tick_hold_no_input",
            tick_decision="would_hold",
            reason=f"lease_status_error:{type(exc).__name__}",
            early_window_ms=early_window_ms,
            late_window_ms=late_window_ms,
            blockers=[f"lease_status_error:{type(exc).__name__}"],
        )

    original_lease_state = lease_status.get("lease_state")
    lease_refresh_performed = False
    stale_lease_ignored = False
    if original_lease_state != "preview_active" and _token_valid(text):
        stale_lease_ignored = original_lease_state not in {None, "no_lease"}
        try:
            _safe_preview(
                text,
                preview_builder=preview_builder,
                preflight_builder=preflight_builder,
                schedule_builder=schedule_builder,
                clock=clock,
                schedule_kwargs=schedule_kwargs,
            )
            lease_refresh_performed = True
            lease_status = _safe_status(
                text,
                status_builder=status_builder,
                preflight_builder=preflight_builder,
                schedule_builder=schedule_builder,
                clock=clock,
                schedule_kwargs=schedule_kwargs,
            )
        except Exception as exc:
            return _required_payload(
                decision="executor_sim_tick_hold_no_input",
                tick_decision="would_hold",
                reason=f"lease_preview_error:{type(exc).__name__}",
                early_window_ms=early_window_ms,
                late_window_ms=late_window_ms,
                blockers=[f"lease_preview_error:{type(exc).__name__}"],
                lease_status=lease_status,
                stale_lease_ignored=stale_lease_ignored,
                stale_lease_state=str(original_lease_state or "unknown"),
            )

    if lease_status.get("lease_state") != "preview_active":
        reason = f"lease_not_active:{lease_status.get('lease_state') or 'unknown'}"
        blockers = [reason, *[str(blocker) for blocker in (lease_status.get("blockers") or [])]]
        return _required_payload(
            decision="executor_sim_tick_hold_no_input",
            tick_decision="would_hold",
            reason=reason,
            early_window_ms=early_window_ms,
            late_window_ms=late_window_ms,
            blockers=blockers,
            lease_status=lease_status,
            lease_refresh_performed=lease_refresh_performed,
            stale_lease_ignored=stale_lease_ignored,
            stale_lease_state=str(original_lease_state or "unknown") if stale_lease_ignored else None,
        )

    if lease_status.get("armable") is not True or lease_status.get("armed_preview") is not True:
        reason = "lease_not_armable"
        blockers = [reason, *[str(blocker) for blocker in (lease_status.get("blockers") or [])]]
        return _required_payload(
            decision="executor_sim_tick_hold_no_input",
            tick_decision="would_hold",
            reason=reason,
            early_window_ms=early_window_ms,
            late_window_ms=late_window_ms,
            blockers=blockers,
            lease_status=lease_status,
            lease_refresh_performed=lease_refresh_performed,
            stale_lease_ignored=stale_lease_ignored,
            stale_lease_state=str(original_lease_state or "unknown") if stale_lease_ignored else None,
        )

    schedule = _safe_schedule(text, schedule_builder=schedule_builder, schedule_kwargs=schedule_kwargs)
    current_time_ms = _current_time_from_schedule(schedule)
    selected_scheduled_t = _coerce_int(lease_status.get("selected_scheduled_t"))
    if current_time_ms is None or selected_scheduled_t is None:
        blockers = ["no_current_time"] if current_time_ms is None else ["no_selected_scheduled_t"]
        return _required_payload(
            decision="executor_sim_tick_hold_no_input",
            tick_decision="would_hold",
            reason=blockers[0],
            early_window_ms=early_window_ms,
            late_window_ms=late_window_ms,
            blockers=blockers,
            lease_status=lease_status,
            current_time_ms=current_time_ms,
            time_to_action_ms=None,
            lease_refresh_performed=lease_refresh_performed,
            stale_lease_ignored=stale_lease_ignored,
            stale_lease_state=str(original_lease_state or "unknown") if stale_lease_ignored else None,
        )

    time_to_action_ms = selected_scheduled_t - current_time_ms
    if time_to_action_ms > early_window_ms:
        tick_decision = "would_wait"
        reason = "action_before_early_window"
    elif time_to_action_ms < -late_window_ms:
        tick_decision = "would_skip_late"
        reason = "action_after_late_window"
    else:
        tick_decision = "would_execute_preview_only"
        reason = "action_inside_timing_window"

    return _required_payload(
        decision=decision_label,
        tick_decision=tick_decision,
        reason=reason,
        early_window_ms=early_window_ms,
        late_window_ms=late_window_ms,
        blockers=[],
        lease_status=lease_status,
        current_time_ms=current_time_ms,
        time_to_action_ms=time_to_action_ms,
        lease_refresh_performed=lease_refresh_performed,
        stale_lease_ignored=stale_lease_ignored,
        stale_lease_state=str(original_lease_state or "unknown") if stale_lease_ignored else None,
    )


def build_executor_simulated_tick_status_payload(text: str = "", **kwargs: Any) -> dict[str, Any]:
    return build_executor_simulated_tick_payload(
        text,
        decision_label="executor_sim_tick_status_only_no_input",
        **kwargs,
    )


def print_executor_simulated_tick(payload: dict[str, Any], *, title: str = "osu! Executor Simulated Tick") -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Tick decision: {payload['tick_decision']}")
    print(f"  Reason: {payload['reason']}")
    print(f"  Lease state: {payload['lease_state']}")
    print(f"  Lease id: {payload['lease_id']}")
    print(f"  Lease remaining ms: {payload['lease_remaining_ms']}")
    print(f"  Selected action id: {payload['selected_action_id']}")
    print(f"  Selected action type: {payload['selected_action_type']}")
    print(f"  Selected source object index: {payload['selected_source_object_index']}")
    print(f"  Selected scheduled t: {payload['selected_scheduled_t']}")
    print(f"  Selected timing state: {payload['selected_timing_state']}")
    print(f"  Selected playfield: {payload['selected_playfield']}")
    print(f"  Selected screen: {payload['selected_screen']}")
    print(f"  Current time ms: {payload['current_time_ms']}")
    print(f"  Time to action ms: {payload['time_to_action_ms']}")
    print(f"  Lease refresh performed: {payload['lease_refresh_performed']}")
    print(f"  Stale lease ignored: {payload['stale_lease_ignored']}")
    print(f"  Stale lease state: {payload['stale_lease_state']}")
    print(f"  Early window ms: {payload['early_window_ms']}")
    print(f"  Late window ms: {payload['late_window_ms']}")
    print(f"  Would execute: {payload['would_execute']}")
    print(f"  Would wait: {payload['would_wait']}")
    print(f"  Would hold: {payload['would_hold']}")
    print(f"  Would skip late: {payload['would_skip_late']}")
    print(f"  Armed preview: {payload['armed_preview']}")
    print(f"  Armed: {payload['armed']}")
    print(f"  Armable: {payload['armable']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Focus readiness: {payload['focus_readiness']}")
    print(f"  Emergency abort available: {payload['emergency_abort_available']}")
    print(f"  Emergency release available: {payload['emergency_release_available']}")
    print(f"  Preflight decision: {payload['preflight_decision']}")
    print(f"  Status recheck mode: {payload['status_recheck_mode']}")
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
    print(f"  Tap: {payload['tap']}")
    print(f"  VTS call: {payload['vts_call']}")
    print(f"  Voice call: {payload['voice_call']}")
    print(f"  OBS call: {payload['obs_call']}")
    print(f"  Vision call: {payload['vision_call']}")
