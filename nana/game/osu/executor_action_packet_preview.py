"""
osu! executor action packet preview - Phase 34.

Builds the final no-input dispatch packet shape from the Phase 33 simulated
tick loop. This module never dispatches input, never calls SendInput, never
invokes emergency release, never writes persistent action packets, and never
marks anything as armed or executable.
"""

from __future__ import annotations

from typing import Any, Callable

from nana.game.osu.executor_dry_run import build_executor_dry_run_schedule
from nana.game.osu.executor_sim_tick_loop import MAX_SAMPLES, build_executor_sim_tick_loop_payload


ACTION_PACKET_PREVIEW_SCHEMA = "nana.osu.executor_action_packet_preview.v1"
ACTION_PACKET_SCHEMA = "nana.osu.executor_action_packet.v1"
DEFAULT_SAMPLES = 20
DEFAULT_INTERVAL_MS = 25
DEFAULT_EARLY_WINDOW_MS = 120
DEFAULT_LATE_WINDOW_MS = 120
DEFAULT_LEASE_TTL_MS = 3000
DEFAULT_TIMING_CONSISTENCY_TOLERANCE_MS = 2

PayloadBuilder = Callable[..., dict[str, Any]]

ACTION_MAP = {
    "tap_candidate": "would_send_tap",
    "slider_follow_candidate": "would_send_slider_follow",
    "spinner_candidate": "would_send_spinner_follow_or_hold",
    "move_to_candidate": "would_send_move_to",
}


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
        "samples": DEFAULT_SAMPLES,
        "interval_ms": DEFAULT_INTERVAL_MS,
        "early_window_ms": DEFAULT_EARLY_WINDOW_MS,
        "late_window_ms": DEFAULT_LATE_WINDOW_MS,
        "lease_ttl_ms": DEFAULT_LEASE_TTL_MS,
    }
    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        if name == "samples":
            values["samples"] = int(raw_value)
        elif name == "interval-ms":
            values["interval_ms"] = int(raw_value)
        elif name == "early-window-ms":
            values["early_window_ms"] = int(raw_value)
        elif name == "late-window-ms":
            values["late_window_ms"] = int(raw_value)
        elif name in {"lease-ttl-ms", "ttl-ms"}:
            values["lease_ttl_ms"] = int(raw_value)
    return values


def _ensure_packet_defaults(text: str) -> str:
    flags = _parse_flags(text)
    tokens = str(text or "").split()
    if not tokens:
        tokens = ["/osu-executor-action-packet-preview"]
    names = {
        token[2:].split("=", 1)[0]
        for token in tokens[1:]
        if token.startswith("--") and "=" in token
    }
    additions: list[str] = []
    if "samples" not in names:
        additions.append(f"--samples={flags['samples']}")
    if "interval-ms" not in names:
        additions.append(f"--interval-ms={flags['interval_ms']}")
    if "early-window-ms" not in names:
        additions.append(f"--early-window-ms={flags['early_window_ms']}")
    if "late-window-ms" not in names:
        additions.append(f"--late-window-ms={flags['late_window_ms']}")
    if "lease-ttl-ms" not in names and "ttl-ms" not in names:
        additions.append(f"--lease-ttl-ms={flags['lease_ttl_ms']}")
    return " ".join([*tokens, *additions])


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


def _metadata_complete(action: dict[str, Any]) -> bool:
    return _xy_present(action.get("playfield")) and _xy_present(action.get("screen"))


def _first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


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


def _empty_packet_fields() -> dict[str, Any]:
    return {
        "packet_ready": False,
        "packet_kind": "preview_only",
        "packet_schema": ACTION_PACKET_SCHEMA,
        "packet_id": None,
        "packet_action": None,
        "packet_action_type": None,
        "source_sample_index": None,
        "source_tick_decision": None,
        "loop_result": "loop_not_ready",
        "execute_preview_count": 0,
        "packet_source": "unavailable",
        "stale_lease_ignored": False,
        "samples_max": MAX_SAMPLES,
        "action_id": None,
        "source_object_index": None,
        "scheduled_t": None,
        "current_time_ms": None,
        "time_to_action_ms": None,
        "timing_delta_ms": None,
        "timing_consistency_error_ms": None,
        "timing_consistent": None,
        "timing_consistency_tolerance_ms": DEFAULT_TIMING_CONSISTENCY_TOLERANCE_MS,
        "selected_metadata_source": "unavailable",
        "selected_metadata_complete": False,
        "selected_playfield": None,
        "selected_screen": None,
        "action_match_reason": None,
        "playfield": None,
        "screen": None,
        "timing_state": None,
    }


def _base_payload(
    *,
    decision: str,
    reason: str,
    blockers: list[str] | None = None,
    packet_ready: bool = False,
    packet_action: str | None = None,
    packet_action_type: str | None = None,
    packet_id: str | None = None,
    source_sample_index: int | None = None,
    source_tick_decision: str | None = None,
    loop: dict[str, Any] | None = None,
    action: dict[str, Any] | None = None,
    current_time_ms: Any = None,
    time_to_action_ms: Any = None,
) -> dict[str, Any]:
    loop = loop or {}
    action = action or {}
    playfield = action.get("playfield")
    screen = action.get("screen")
    selected_playfield = _first_not_none(action.get("selected_playfield"), playfield)
    selected_screen = _first_not_none(action.get("selected_screen"), screen)
    payload = {
        "schema": ACTION_PACKET_PREVIEW_SCHEMA,
        "version": 1,
        "decision": decision,
        **_empty_packet_fields(),
        "reason": reason,
        "blockers": blockers or [],
        **_base_flags(),
    }
    payload.update(
        {
            "packet_ready": bool(packet_ready),
            "packet_id": packet_id,
            "packet_action": packet_action,
            "packet_action_type": packet_action_type,
            "source_sample_index": source_sample_index,
            "source_tick_decision": source_tick_decision,
            "loop_result": loop.get("loop_result") or payload["loop_result"],
            "execute_preview_count": int(loop.get("execute_preview_count") or 0),
            "packet_source": action.get("packet_source")
            or (
                "fresh_action_packet_preview"
                if action.get("selected_metadata_source") in {"loop_selected_action", "lease_selected_action"}
                else "existing_preview_lease"
                if packet_ready
                else "unavailable"
            ),
            "stale_lease_ignored": bool(loop.get("stale_lease_ignored")),
            "samples_max": MAX_SAMPLES,
            "action_id": _first_not_none(action.get("action_id"), loop.get("selected_action_id")),
            "source_object_index": _first_not_none(
                action.get("source_object_index"),
                action.get("selected_source_object_index"),
                loop.get("selected_source_object_index"),
            ),
            "scheduled_t": _first_not_none(action.get("scheduled_t"), loop.get("selected_scheduled_t")),
            "current_time_ms": current_time_ms,
            "time_to_action_ms": time_to_action_ms,
            "selected_metadata_source": action.get("selected_metadata_source") or "unavailable",
            "selected_metadata_complete": _metadata_complete({"playfield": selected_playfield, "screen": selected_screen}),
            "selected_playfield": selected_playfield,
            "selected_screen": selected_screen,
            "action_match_reason": action.get("action_match_reason"),
            "playfield": playfield,
            "screen": screen,
            "timing_state": action.get("timing_state") or loop.get("selected_timing_state"),
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


def _selected_action_from_snapshot(loop: dict[str, Any], execute_sample: dict[str, Any]) -> dict[str, Any]:
    sample_has_selected_metadata = any(
        execute_sample.get(key) is not None
        for key in (
            "selected_action_id",
            "selected_action_type",
            "selected_source_object_index",
            "selected_scheduled_t",
            "selected_timing_state",
            "selected_playfield",
            "selected_screen",
        )
    )
    loop_has_selected_metadata = any(
        loop.get(key) is not None
        for key in (
            "selected_action_id",
            "selected_action_type",
            "selected_source_object_index",
            "selected_scheduled_t",
            "selected_timing_state",
            "selected_playfield",
            "selected_screen",
        )
    )
    source = "unavailable"
    match_reason = "selected_action_metadata_unavailable"
    if sample_has_selected_metadata:
        source = "loop_selected_action"
        match_reason = "loop_selected_action_metadata"
    elif loop_has_selected_metadata:
        source = "lease_selected_action"
        match_reason = "lease_selected_action_metadata"
    return {
        "action_id": _first_not_none(execute_sample.get("selected_action_id"), loop.get("selected_action_id")),
        "source_object_index": _first_not_none(
            execute_sample.get("selected_source_object_index"),
            loop.get("selected_source_object_index"),
        ),
        "action_type": _first_not_none(execute_sample.get("selected_action_type"), loop.get("selected_action_type")),
        "scheduled_t": _first_not_none(execute_sample.get("selected_scheduled_t"), loop.get("selected_scheduled_t")),
        "relative_ms_from_now": _first_not_none(
            execute_sample.get("time_to_action_ms"),
            loop.get("selected_relative_ms"),
            loop.get("time_to_action_ms"),
        ),
        "time_until_action_ms": _first_not_none(execute_sample.get("time_to_action_ms"), loop.get("time_to_action_ms")),
        "playfield": _first_not_none(execute_sample.get("selected_playfield"), loop.get("selected_playfield")),
        "screen": _first_not_none(execute_sample.get("selected_screen"), loop.get("selected_screen")),
        "reason": _first_not_none(execute_sample.get("reason"), loop.get("reason")),
        "action_hint": _first_not_none(execute_sample.get("reason"), loop.get("reason")),
        "timing_state": _first_not_none(execute_sample.get("selected_timing_state"), loop.get("selected_timing_state")),
        "selected_metadata_source": source,
        "action_match_reason": match_reason,
        "executable": False,
    }


def _safe_loop(
    text: str,
    *,
    loop_builder: PayloadBuilder | None,
    loop_kwargs: dict[str, Any],
) -> dict[str, Any]:
    builder = loop_builder or build_executor_sim_tick_loop_payload
    return builder(text, **loop_kwargs)


def _safe_schedule(text: str, *, schedule_builder: PayloadBuilder | None, schedule_kwargs: dict[str, Any]) -> dict[str, Any]:
    builder = schedule_builder or build_executor_dry_run_schedule
    try:
        return builder(text, **schedule_kwargs)
    except Exception as exc:
        return {
            "schema": "nana.osu.executor_dry_run_schedule.v1",
            "decision": "executor_dry_run_hold_no_points",
            "current_time_ms": None,
            "actions": [],
            "blockers": [f"action_packet_schedule_error:{type(exc).__name__}"],
            "executable": False,
            "real_input": False,
            "submit": False,
        }


def _matching_action(schedule: dict[str, Any], loop: dict[str, Any]) -> dict[str, Any]:
    selected_id = loop.get("selected_action_id")
    selected_t = _coerce_int(loop.get("selected_scheduled_t"))
    selected_type = loop.get("selected_action_type")
    actions = list(schedule.get("actions") or [])
    timing_type_matches: list[dict[str, Any]] = []
    if selected_t is not None:
        for action in actions:
            if _coerce_int(action.get("scheduled_t")) != selected_t:
                continue
            if selected_type is not None and action.get("action_type") != selected_type:
                continue
            timing_type_matches.append(dict(action))
    if timing_type_matches:
        if selected_id is not None:
            for action in timing_type_matches:
                if action.get("action_id") == selected_id:
                    action["_packet_match_found"] = True
                    action["_packet_match_reason"] = "scheduled_time_type_id_match"
                    action["_selected_metadata_source"] = "schedule_time_type_match"
                    return action
        action = dict(timing_type_matches[0])
        action["_packet_match_found"] = True
        action["_packet_match_reason"] = "scheduled_time_type_match"
        action["_selected_metadata_source"] = "schedule_time_type_match"
        return action
    return {
        "_packet_match_found": False,
        "_packet_match_reason": "selected_action_not_found_in_schedule",
        "_selected_metadata_source": "unavailable",
        "action_id": selected_id,
        "source_object_index": None,
        "action_type": selected_type,
        "scheduled_t": loop.get("selected_scheduled_t"),
        "relative_ms_from_now": loop.get("time_to_action_ms"),
        "time_until_action_ms": loop.get("time_to_action_ms"),
        "playfield": None,
        "screen": None,
        "reason": loop.get("reason"),
        "action_hint": loop.get("reason"),
        "timing_state": loop.get("selected_timing_state"),
        "executable": False,
    }


def _first_execute_sample(loop: dict[str, Any]) -> dict[str, Any] | None:
    index = loop.get("first_execute_sample_index")
    for sample in loop.get("sample_decisions") or []:
        if sample.get("index") == index and sample.get("tick_decision") == "would_execute_preview_only":
            return dict(sample)
    for sample in loop.get("sample_decisions") or []:
        if sample.get("tick_decision") == "would_execute_preview_only":
            return dict(sample)
    return None


def _packet_action(action_type: Any) -> str:
    return ACTION_MAP.get(str(action_type or ""), "would_hold_unknown_action")


def _packet_id(action: dict[str, Any], source_sample_index: int | None) -> str:
    action_id = action.get("action_id") or "unknown-action"
    index = "unknown" if source_sample_index is None else str(source_sample_index)
    return f"packet-preview-{action_id}-{index}"


def build_executor_action_packet_preview_payload(
    text: str = "",
    *,
    decision_label: str = "executor_action_packet_preview_only_no_input",
    loop_builder: PayloadBuilder | None = None,
    schedule_builder: PayloadBuilder | None = None,
    **loop_kwargs: Any,
) -> dict[str, Any]:
    loop_text = _ensure_packet_defaults(text)
    try:
        loop = _safe_loop(loop_text, loop_builder=loop_builder, loop_kwargs=dict(loop_kwargs))
    except Exception as exc:
        return _base_payload(
            decision="executor_action_packet_hold_no_input",
            reason=f"loop_error:{type(exc).__name__}",
            blockers=[f"loop_error:{type(exc).__name__}"],
        )

    if not bool(loop.get("observed_execute_preview_only")):
        reason = str(loop.get("loop_result") or "loop_not_ready")
        blockers = [reason, *[str(blocker) for blocker in (loop.get("blockers") or [])]]
        return _base_payload(
            decision="executor_action_packet_hold_no_input",
            reason=reason,
            blockers=list(dict.fromkeys(blockers)),
            loop=loop,
            current_time_ms=loop.get("current_time_ms"),
            time_to_action_ms=loop.get("time_to_action_ms"),
        )

    execute_sample = _first_execute_sample(loop)
    if execute_sample is None:
        return _base_payload(
            decision="executor_action_packet_hold_no_input",
            reason="execute_sample_unavailable",
            blockers=["execute_sample_unavailable"],
            loop=loop,
            current_time_ms=loop.get("current_time_ms"),
            time_to_action_ms=loop.get("time_to_action_ms"),
        )

    schedule = _safe_schedule(loop_text, schedule_builder=schedule_builder, schedule_kwargs=dict(loop_kwargs))
    snapshot_action = _selected_action_from_snapshot(loop, execute_sample)
    action = snapshot_action
    action_match_found = _metadata_complete(snapshot_action)
    action_match_reason = snapshot_action.get("action_match_reason")
    schedule_match_missing = False
    if not action_match_found:
        action = _matching_action(schedule, loop)
        action_match_found = bool(action.pop("_packet_match_found", False))
        action_match_reason = action.pop("_packet_match_reason", None)
        action["selected_metadata_source"] = action.pop("_selected_metadata_source", "unavailable")
        action["action_match_reason"] = action_match_reason
        if not action_match_found:
            schedule_match_missing = True
            action = snapshot_action
            action["selected_metadata_source"] = snapshot_action.get("selected_metadata_source") or "unavailable"
            action["action_match_reason"] = action_match_reason or "selected_action_metadata_unavailable"
        elif not _metadata_complete(action):
            action_match_found = False
    action_type = action.get("action_type") or loop.get("selected_action_type") or "unknown"
    packet_action = _packet_action(action_type)
    source_sample_index = _coerce_int(execute_sample.get("index"))
    current_time_ms = execute_sample.get("current_time_ms")
    time_to_action_ms = execute_sample.get("time_to_action_ms")
    if current_time_ms is None:
        current_time_ms = loop.get("current_time_ms")
    if time_to_action_ms is None:
        time_to_action_ms = loop.get("time_to_action_ms")

    blockers = [str(blocker) for blocker in (schedule.get("blockers") or [])]
    if not action_match_found:
        blockers.append("selected_action_metadata_unavailable")
        if schedule_match_missing:
            blockers.append("selected_action_not_found_in_schedule")
        return _base_payload(
            decision="executor_action_packet_hold_no_input",
            reason="selected_action_metadata_unavailable",
            blockers=list(dict.fromkeys(blockers)),
            packet_ready=False,
            packet_action=packet_action,
            packet_action_type=str(action_type or "unknown"),
            packet_id=None,
            source_sample_index=source_sample_index,
            source_tick_decision="would_execute_preview_only",
            loop=loop,
            action=action,
            current_time_ms=current_time_ms,
            time_to_action_ms=time_to_action_ms,
        )

    timing = _timing_diagnostics(action.get("scheduled_t"), current_time_ms, time_to_action_ms)
    if timing.get("timing_consistent") is False:
        blockers.append("action_packet_timing_mismatch")
        return _base_payload(
            decision="executor_action_packet_hold_no_input",
            reason="action_packet_timing_mismatch",
            blockers=list(dict.fromkeys(blockers)),
            packet_ready=False,
            packet_action=packet_action,
            packet_action_type=str(action_type or "unknown"),
            packet_id=None,
            source_sample_index=source_sample_index,
            source_tick_decision="would_execute_preview_only",
            loop=loop,
            action=action,
            current_time_ms=current_time_ms,
            time_to_action_ms=time_to_action_ms,
        )

    packet_ready = packet_action != "would_hold_unknown_action"
    decision = decision_label if packet_ready else "executor_action_packet_hold_no_input"
    if not packet_ready:
        blockers.append("unknown_action_type")
    return _base_payload(
        decision=decision,
        reason=packet_action if packet_ready else "unknown_action_type",
        blockers=list(dict.fromkeys(blockers)),
        packet_ready=packet_ready,
        packet_action=packet_action,
        packet_action_type=str(action_type or "unknown"),
        packet_id=_packet_id(action, source_sample_index) if packet_ready else None,
        source_sample_index=source_sample_index,
        source_tick_decision="would_execute_preview_only",
        loop=loop,
        action=action,
        current_time_ms=current_time_ms,
        time_to_action_ms=time_to_action_ms,
    )


def build_executor_action_packet_status_payload(text: str = "", **kwargs: Any) -> dict[str, Any]:
    return build_executor_action_packet_preview_payload(
        text,
        decision_label="executor_action_packet_status_only_no_input",
        **kwargs,
    )


def print_executor_action_packet_preview(
    payload: dict[str, Any],
    *,
    title: str = "osu! Executor Action Packet Preview",
) -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Packet ready: {payload['packet_ready']}")
    print(f"  Packet kind: {payload['packet_kind']}")
    print(f"  Packet schema: {payload['packet_schema']}")
    print(f"  Packet id: {payload['packet_id']}")
    print(f"  Packet action: {payload['packet_action']}")
    print(f"  Packet action type: {payload['packet_action_type']}")
    print(f"  Source sample index: {payload['source_sample_index']}")
    print(f"  Source tick decision: {payload['source_tick_decision']}")
    print(f"  Loop result: {payload['loop_result']}")
    print(f"  Execute preview count: {payload['execute_preview_count']}")
    print(f"  Packet source: {payload['packet_source']}")
    print(f"  Stale lease ignored: {payload['stale_lease_ignored']}")
    print(f"  Samples max: {payload['samples_max']}")
    print(f"  Action id: {payload['action_id']}")
    print(f"  Source object index: {payload['source_object_index']}")
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
    print(f"  Playfield: {payload['playfield']}")
    print(f"  Screen: {payload['screen']}")
    print(f"  Timing state: {payload['timing_state']}")
    print(f"  Reason: {payload['reason']}")
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
