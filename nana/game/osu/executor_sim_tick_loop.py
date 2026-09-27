"""
osu! executor simulated tick loop - Phase 33.

Samples the Phase 32 no-input simulated tick multiple times and summarizes
whether the preview-only timing window was observed. This module never executes
input, never calls SendInput, never invokes emergency release, and never marks
anything as armed or executable.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from nana.game.osu.executor_simulated_tick import build_executor_simulated_tick_payload


SIM_TICK_LOOP_SCHEMA = "nana.osu.executor_sim_tick_loop.v1"
DEFAULT_SAMPLES = 8
MAX_SAMPLES = 50
DEFAULT_INTERVAL_MS = 25
MAX_INTERVAL_MS = 250
DEFAULT_EARLY_WINDOW_MS = 80
DEFAULT_LATE_WINDOW_MS = 60
DEFAULT_LEASE_TTL_MS = 2000
PRINT_SAMPLE_DECISION_LIMIT = 20

PayloadBuilder = Callable[..., dict[str, Any]]
Clock = Callable[[], float]
Sleeper = Callable[[float], None]


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


def _validated_flags(text: str) -> tuple[dict[str, int], list[str]]:
    flags = _parse_flags(text)
    blockers: list[str] = []
    if flags["samples"] <= 0 or flags["samples"] > MAX_SAMPLES:
        blockers.append(f"samples_out_of_bounds:1..{MAX_SAMPLES}")
    if flags["interval_ms"] < 0 or flags["interval_ms"] > MAX_INTERVAL_MS:
        blockers.append(f"interval_ms_out_of_bounds:0..{MAX_INTERVAL_MS}")
    if flags["early_window_ms"] < 0:
        blockers.append("early_window_ms_must_be_non_negative")
    if flags["late_window_ms"] < 0:
        blockers.append("late_window_ms_must_be_non_negative")
    if flags["lease_ttl_ms"] <= 0:
        blockers.append("lease_ttl_ms_must_be_positive")
    return flags, blockers


def _ensure_loop_defaults(text: str, flags: dict[str, int]) -> str:
    tokens = str(text or "").split()
    if not tokens:
        tokens = ["/osu-executor-sim-tick-loop-preview"]
    names = {
        token[2:].split("=", 1)[0]
        for token in tokens[1:]
        if token.startswith("--") and "=" in token
    }
    additions: list[str] = []
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
        "focus_readiness": "unavailable_no_lease",
        "emergency_abort_available": "unavailable_no_lease",
        "emergency_release_available": "unavailable_no_lease",
        "preflight_decision": "not_checked_no_lease",
        "status_recheck_mode": "snapshot",
        "lease_refresh_performed": False,
        "stale_lease_ignored": False,
        "stale_lease_state": None,
        "armed_preview": False,
        "armable": False,
    }


def _sample_summary(sample: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "index": index,
        "decision": sample.get("decision"),
        "tick_decision": sample.get("tick_decision"),
        "reason": sample.get("reason"),
        "lease_state": sample.get("lease_state"),
        "lease_remaining_ms": sample.get("lease_remaining_ms"),
        "selected_action_id": sample.get("selected_action_id"),
        "selected_action_type": sample.get("selected_action_type"),
        "selected_source_object_index": sample.get("selected_source_object_index"),
        "selected_scheduled_t": sample.get("selected_scheduled_t"),
        "selected_timing_state": sample.get("selected_timing_state"),
        "selected_playfield": sample.get("selected_playfield"),
        "selected_screen": sample.get("selected_screen"),
        "current_time_ms": sample.get("current_time_ms"),
        "time_to_action_ms": sample.get("time_to_action_ms"),
        "lease_refresh_performed": bool(sample.get("lease_refresh_performed")),
        "stale_lease_ignored": bool(sample.get("stale_lease_ignored")),
        "stale_lease_state": sample.get("stale_lease_state"),
        "would_execute": bool(sample.get("would_execute")),
        "would_wait": bool(sample.get("would_wait")),
        "would_hold": bool(sample.get("would_hold")),
        "would_skip_late": bool(sample.get("would_skip_late")),
        "blockers": [str(blocker) for blocker in (sample.get("blockers") or [])],
    }


def _loop_result_label(*, execute_count: int, wait_count: int, skip_late_count: int, hold_count: int, samples_ok: int) -> str:
    if execute_count > 0:
        return "observed_execute_preview_only"
    if samples_ok <= 0 or hold_count > 0:
        return "no_execute_hold"
    if wait_count > 0 and skip_late_count == 0:
        return "no_execute_observed_waiting"
    if skip_late_count > 0:
        return "no_execute_observed_late"
    return "loop_not_ready"


def _base_payload(
    *,
    decision: str,
    loop_result: str,
    reason: str,
    flags: dict[str, int],
    blockers: list[str] | None = None,
    samples_attempted: int = 0,
    samples_ok: int = 0,
    sample_decisions: list[dict[str, Any]] | None = None,
    last_tick: dict[str, Any] | None = None,
    execute_count: int = 0,
    wait_count: int = 0,
    skip_late_count: int = 0,
    hold_count: int = 0,
    time_to_action_values: list[int] | None = None,
    first_execute_sample_index: int | None = None,
    first_tick_decision: str | None = None,
    last_tick_decision: str | None = None,
    sample_decisions_truncated: bool = False,
) -> dict[str, Any]:
    sample_decisions = sample_decisions or []
    last_tick = last_tick or {}
    time_to_action_values = time_to_action_values or []
    if first_tick_decision is None and sample_decisions:
        first_tick_decision = sample_decisions[0]["tick_decision"]
    if last_tick_decision is None and sample_decisions:
        last_tick_decision = sample_decisions[-1]["tick_decision"]
    closest_time_to_action_ms = (
        min(time_to_action_values, key=lambda value: abs(value)) if time_to_action_values else None
    )
    payload = {
        "schema": SIM_TICK_LOOP_SCHEMA,
        "version": 1,
        "decision": decision,
        "loop_result": loop_result,
        "reason": reason,
        "samples_requested": int(flags["samples"]),
        "samples_attempted": int(samples_attempted),
        "samples_ok": int(samples_ok),
        "interval_ms": int(flags["interval_ms"]),
        "early_window_ms": int(flags["early_window_ms"]),
        "late_window_ms": int(flags["late_window_ms"]),
        "lease_ttl_ms": int(flags["lease_ttl_ms"]),
        "first_tick_decision": first_tick_decision,
        "last_tick_decision": last_tick_decision,
        "observed_wait": wait_count > 0,
        "observed_execute_preview_only": execute_count > 0,
        "observed_skip_late": skip_late_count > 0,
        "observed_hold": hold_count > 0,
        "execute_preview_count": int(execute_count),
        "wait_count": int(wait_count),
        "skip_late_count": int(skip_late_count),
        "hold_count": int(hold_count),
        "min_time_to_action_ms": min(time_to_action_values) if time_to_action_values else None,
        "max_time_to_action_ms": max(time_to_action_values) if time_to_action_values else None,
        "closest_time_to_action_ms": closest_time_to_action_ms,
        "first_execute_sample_index": first_execute_sample_index,
        "sample_decisions": sample_decisions,
        "sample_decisions_truncated": bool(sample_decisions_truncated),
        "blockers": blockers or [],
        **_empty_tick_fields(),
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
        "current_time_ms",
        "time_to_action_ms",
        "focus_readiness",
        "emergency_abort_available",
        "emergency_release_available",
        "preflight_decision",
        "status_recheck_mode",
        "lease_refresh_performed",
        "stale_lease_ignored",
        "stale_lease_state",
        "armed_preview",
        "armable",
    ):
        if key in last_tick:
            payload[key] = last_tick.get(key)
    stale_samples = [sample for sample in sample_decisions if sample.get("stale_lease_ignored")]
    if stale_samples:
        payload["stale_lease_ignored"] = True
        payload["stale_lease_state"] = stale_samples[0].get("stale_lease_state")
        payload["lease_refresh_performed"] = True
    payload.update(_base_flags())
    return payload


def build_executor_sim_tick_loop_payload(
    text: str = "",
    *,
    decision_label: str = "executor_sim_tick_loop_preview_only_no_input",
    tick_builder: PayloadBuilder | None = None,
    sleep_fn: Sleeper | None = None,
    clock: Clock | None = None,
    **tick_kwargs: Any,
) -> dict[str, Any]:
    try:
        flags, param_blockers = _validated_flags(text)
    except Exception as exc:
        flags = {
            "samples": DEFAULT_SAMPLES,
            "interval_ms": DEFAULT_INTERVAL_MS,
            "early_window_ms": DEFAULT_EARLY_WINDOW_MS,
            "late_window_ms": DEFAULT_LATE_WINDOW_MS,
            "lease_ttl_ms": DEFAULT_LEASE_TTL_MS,
        }
        return _base_payload(
            decision="executor_sim_tick_loop_hold_no_input",
            loop_result="loop_not_ready",
            reason="invalid_loop_params",
            flags=flags,
            blockers=[f"invalid_loop_params:{type(exc).__name__}"],
        )

    if param_blockers:
        return _base_payload(
            decision="executor_sim_tick_loop_hold_no_input",
            loop_result="loop_not_ready",
            reason="invalid_loop_params",
            flags=flags,
            blockers=param_blockers,
        )

    tick_text = _ensure_loop_defaults(text, flags)
    builder = tick_builder or build_executor_simulated_tick_payload
    sleeper = sleep_fn if sleep_fn is not None else time.sleep
    sample_decisions: list[dict[str, Any]] = []
    sample_decisions_truncated = False
    all_blockers: list[str] = []
    time_to_action_values: list[int] = []
    last_tick: dict[str, Any] | None = None
    execute_count = 0
    wait_count = 0
    skip_late_count = 0
    hold_count = 0
    samples_ok = 0
    first_execute_sample_index: int | None = None
    first_tick_decision: str | None = None
    last_tick_decision: str | None = None
    samples_attempted = 0

    for index in range(int(flags["samples"])):
        samples_attempted += 1
        try:
            call_kwargs = dict(tick_kwargs)
            if clock is not None and "clock" not in call_kwargs:
                call_kwargs["clock"] = clock
            tick = builder(tick_text, **call_kwargs)
        except Exception as exc:
            tick = {
                "schema": "nana.osu.executor_simulated_tick.v1",
                "decision": "executor_sim_tick_hold_no_input",
                "tick_decision": "would_hold",
                "reason": f"sim_tick_loop_sample_error:{type(exc).__name__}",
                "lease_state": "no_lease",
                "blockers": [f"sim_tick_loop_sample_error:{type(exc).__name__}"],
                **_base_flags(),
            }

        last_tick = tick
        tick_decision = tick.get("tick_decision")
        if first_tick_decision is None:
            first_tick_decision = str(tick_decision) if tick_decision is not None else None
        last_tick_decision = str(tick_decision) if tick_decision is not None else None
        if len(sample_decisions) < PRINT_SAMPLE_DECISION_LIMIT:
            sample_decisions.append(_sample_summary(tick, index))
        else:
            sample_decisions_truncated = True
        all_blockers.extend(str(blocker) for blocker in (tick.get("blockers") or []))
        value = _coerce_int(tick.get("time_to_action_ms"))
        if value is not None:
            time_to_action_values.append(value)
        if tick.get("schema") == "nana.osu.executor_simulated_tick.v1":
            samples_ok += 1
        if tick_decision == "would_execute_preview_only":
            execute_count += 1
            if first_execute_sample_index is None:
                first_execute_sample_index = index
        elif tick_decision == "would_wait":
            wait_count += 1
        elif tick_decision == "would_skip_late":
            skip_late_count += 1
        elif tick_decision == "would_hold":
            hold_count += 1

        if tick_decision == "would_hold":
            break
        if tick.get("lease_state") not in {None, "preview_active"} and tick_decision != "would_execute_preview_only":
            break
        if index < int(flags["samples"]) - 1 and flags["interval_ms"] > 0:
            sleeper(flags["interval_ms"] / 1000.0)

    loop_result = _loop_result_label(
        execute_count=execute_count,
        wait_count=wait_count,
        skip_late_count=skip_late_count,
        hold_count=hold_count,
        samples_ok=samples_ok,
    )
    decision = (
        "executor_sim_tick_loop_hold_no_input"
        if loop_result in {"loop_not_ready", "no_execute_hold"}
        else decision_label
    )
    reason = loop_result
    if hold_count > 0 and execute_count == 0:
        reason = (last_tick or {}).get("reason") or "tick_hold_observed"
    return _base_payload(
        decision=decision,
        loop_result=loop_result,
        reason=reason,
        flags=flags,
        blockers=list(dict.fromkeys(all_blockers)),
        samples_attempted=samples_attempted,
        samples_ok=samples_ok,
        sample_decisions=sample_decisions,
        last_tick=last_tick,
        execute_count=execute_count,
        wait_count=wait_count,
        skip_late_count=skip_late_count,
        hold_count=hold_count,
        time_to_action_values=time_to_action_values,
        first_execute_sample_index=first_execute_sample_index,
        first_tick_decision=first_tick_decision,
        last_tick_decision=last_tick_decision,
        sample_decisions_truncated=sample_decisions_truncated,
    )


def build_executor_sim_tick_loop_status_payload(text: str = "", **kwargs: Any) -> dict[str, Any]:
    return build_executor_sim_tick_loop_payload(
        text,
        decision_label="executor_sim_tick_loop_status_only_no_input",
        **kwargs,
    )


def print_executor_sim_tick_loop(payload: dict[str, Any], *, title: str = "osu! Executor Sim Tick Loop") -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Loop result: {payload['loop_result']}")
    print(f"  Reason: {payload['reason']}")
    print(f"  Samples requested: {payload['samples_requested']}")
    print(f"  Samples attempted: {payload['samples_attempted']}")
    print(f"  Samples ok: {payload['samples_ok']}")
    print(f"  Interval ms: {payload['interval_ms']}")
    print(f"  Early window ms: {payload['early_window_ms']}")
    print(f"  Late window ms: {payload['late_window_ms']}")
    print(f"  Lease ttl ms: {payload['lease_ttl_ms']}")
    print(f"  First tick decision: {payload['first_tick_decision']}")
    print(f"  Last tick decision: {payload['last_tick_decision']}")
    print(f"  Observed wait: {payload['observed_wait']}")
    print(f"  Observed execute preview only: {payload['observed_execute_preview_only']}")
    print(f"  Observed skip late: {payload['observed_skip_late']}")
    print(f"  Observed hold: {payload['observed_hold']}")
    print(f"  Execute preview count: {payload['execute_preview_count']}")
    print(f"  Wait count: {payload['wait_count']}")
    print(f"  Skip late count: {payload['skip_late_count']}")
    print(f"  Hold count: {payload['hold_count']}")
    print(f"  Min time to action ms: {payload['min_time_to_action_ms']}")
    print(f"  Max time to action ms: {payload['max_time_to_action_ms']}")
    print(f"  Closest time to action ms: {payload['closest_time_to_action_ms']}")
    print(f"  First execute sample index: {payload['first_execute_sample_index']}")
    for sample in payload["sample_decisions"][:PRINT_SAMPLE_DECISION_LIMIT]:
        print(
            "  Sample: "
            f"{sample['index']} {sample['tick_decision']} "
            f"togo={sample['time_to_action_ms']} lease={sample['lease_state']} "
            f"reason={sample['reason']}"
        )
    print(f"  Blockers: {payload['blockers']}")
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
    print(f"  Armed preview: {payload['armed_preview']}")
    print(f"  Armed: {payload['armed']}")
    print(f"  Armable: {payload['armable']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Focus readiness: {payload['focus_readiness']}")
    print(f"  Emergency abort available: {payload['emergency_abort_available']}")
    print(f"  Emergency release available: {payload['emergency_release_available']}")
    print(f"  Preflight decision: {payload['preflight_decision']}")
    print(f"  Status recheck mode: {payload['status_recheck_mode']}")
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
