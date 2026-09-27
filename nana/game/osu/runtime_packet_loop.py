"""
osu! runtime packet loop preview - Phase 42.

Repeatedly samples the cached-plan runtime packet builder and summarizes timing
and lookup stability. State is in-memory only.
"""

from __future__ import annotations

import copy
import re
import statistics
import time
from typing import Any, Callable

from nana.game.osu.play_plan_cache import get_cached_play_plan
from nana.game.osu.runtime_packet import build_runtime_packet_payload


RUNTIME_PACKET_LOOP_SCHEMA = "nana.osu.runtime_packet_loop_preview.v1"
DEFAULT_SAMPLES = 10
DEFAULT_INTERVAL_MS = 16
SAMPLES_MIN = 1
SAMPLES_MAX = 120
INTERVAL_MS_MIN = 0
INTERVAL_MS_MAX = 100

StateReader = Callable[[], dict[str, Any]]
ActivityReader = Callable[[dict[str, Any] | None], dict[str, Any]]
Sleeper = Callable[[float], None]

_LAST_RUNTIME_PACKET_LOOP: dict[str, Any] | None = None


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


def clear_runtime_packet_loop_state() -> None:
    global _LAST_RUNTIME_PACKET_LOOP
    _LAST_RUNTIME_PACKET_LOOP = None


def _copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(payload)
    copied.update(_base_flags())
    return copied


def _store_payload(payload: dict[str, Any]) -> dict[str, Any]:
    global _LAST_RUNTIME_PACKET_LOOP
    safe_payload = _copy_payload(payload)
    _LAST_RUNTIME_PACKET_LOOP = _copy_payload(safe_payload)
    return safe_payload


def _parse_int_flag(text: str, name: str, default: int) -> tuple[int, str | None]:
    match = re.search(rf"(?:^|\s){re.escape(name)}=(\S+)(?=\s|$)", text or "")
    if not match:
        return int(default), None
    raw = match.group(1)
    try:
        return int(raw), None
    except ValueError:
        return int(default), f"{name.lstrip('-').replace('-', '_')}_invalid"


def _parse_loop_params(text: str) -> tuple[int, int, list[str]]:
    samples, samples_error = _parse_int_flag(text, "--samples", DEFAULT_SAMPLES)
    interval_ms, interval_error = _parse_int_flag(text, "--interval-ms", DEFAULT_INTERVAL_MS)
    blockers = [error for error in (samples_error, interval_error) if error]
    if samples < SAMPLES_MIN or samples > SAMPLES_MAX:
        blockers.append(f"samples_out_of_bounds:{SAMPLES_MIN}..{SAMPLES_MAX}")
    if interval_ms < INTERVAL_MS_MIN or interval_ms > INTERVAL_MS_MAX:
        blockers.append(f"interval_ms_out_of_bounds:{INTERVAL_MS_MIN}..{INTERVAL_MS_MAX}")
    return samples, interval_ms, blockers


def _dedupe(values: list[Any]) -> list[Any]:
    seen: set[str] = set()
    result: list[Any] = []
    for value in values:
        marker = repr(value)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _empty_payload(
    *,
    decision: str,
    reason: str,
    samples_requested: int = DEFAULT_SAMPLES,
    interval_ms: int = DEFAULT_INTERVAL_MS,
    blockers: list[str] | None = None,
    samples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": RUNTIME_PACKET_LOOP_SCHEMA,
        "version": 1,
        "decision": decision,
        "reason": reason,
        "loop_result": "loop_not_ready",
        "samples_requested": samples_requested,
        "samples_attempted": 0,
        "samples_ok": 0,
        "interval_ms": interval_ms,
        "samples_min": SAMPLES_MIN,
        "samples_max": SAMPLES_MAX,
        "interval_ms_min": INTERVAL_MS_MIN,
        "interval_ms_max": INTERVAL_MS_MAX,
        "packet_ready_count": 0,
        "identity_match_count": 0,
        "identity_unverified_count": 0,
        "map_mismatch_count": 0,
        "early_count": 0,
        "ready_window_count": 0,
        "late_count": 0,
        "unique_action_count": 0,
        "first_action_id": None,
        "last_action_id": None,
        "first_current_time_ms": None,
        "last_current_time_ms": None,
        "current_time_delta_ms": None,
        "min_lookup_cost_ms": None,
        "max_lookup_cost_ms": None,
        "median_lookup_cost_ms": None,
        "max_time_to_action_ms": None,
        "min_time_to_action_ms": None,
        "closest_time_to_action_ms": None,
        "blockers": list(blockers or []),
        "samples": list(samples or []),
        "created_at": time.time(),
        **_base_flags(),
    }
    payload.update(_base_flags())
    return payload


def _sample_record(index: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": index,
        "action_id": payload.get("action_id"),
        "scheduled_t": payload.get("scheduled_t"),
        "current_time_ms": payload.get("current_time_ms"),
        "time_to_action_ms": payload.get("time_to_action_ms"),
        "timing_state": payload.get("timing_state"),
        "lookup_cost_ms": payload.get("lookup_cost_ms"),
        "packet_ready": bool(payload.get("packet_ready")),
        "reason": payload.get("reason"),
        "cache_identity_status": payload.get("cache_identity_status"),
        "cache_identity_match": payload.get("cache_identity_match"),
        "blockers": list(payload.get("blockers") or []),
    }


def _is_hard_hold(record: dict[str, Any]) -> bool:
    reason = str(record.get("reason") or "")
    blockers = [str(blocker) for blocker in record.get("blockers") or []]
    if reason in {"no_cached_play_plan", "cached_play_plan_map_mismatch", "current_time_missing"}:
        return True
    if reason.startswith("activity_not_gameplay"):
        return True
    return any(
        blocker in {"no_cached_play_plan", "cached_play_plan_map_mismatch", "current_time_missing"}
        or blocker.startswith("activity_not_gameplay")
        for blocker in blockers
    )


def _summarize_samples(
    *,
    samples_requested: int,
    interval_ms: int,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    blockers = _dedupe([blocker for record in records for blocker in (record.get("blockers") or [])])
    packet_ready_count = sum(1 for record in records if record.get("packet_ready") is True)
    action_ids = [record.get("action_id") for record in records if record.get("action_id") is not None]
    current_times = [_as_int(record.get("current_time_ms")) for record in records]
    current_times = [value for value in current_times if value is not None]
    lookup_costs = [_as_float(record.get("lookup_cost_ms")) for record in records]
    lookup_costs = [value for value in lookup_costs if value is not None]
    time_to_action_values = [_as_int(record.get("time_to_action_ms")) for record in records]
    time_to_action_values = [value for value in time_to_action_values if value is not None]

    decision = (
        "runtime_packet_loop_preview_only_no_input"
        if packet_ready_count > 0
        else "runtime_packet_loop_hold_no_input"
    )
    reason = "runtime_packet_samples_observed" if packet_ready_count > 0 else "no_packet_ready_observed"
    if blockers:
        reason = str(blockers[0])
    payload = {
        "schema": RUNTIME_PACKET_LOOP_SCHEMA,
        "version": 1,
        "decision": decision,
        "reason": reason,
        "loop_result": (
            "observed_runtime_packet_ready_preview_only"
            if packet_ready_count > 0
            else "no_packet_ready_observed"
        ),
        "samples_requested": samples_requested,
        "samples_attempted": len(records),
        "samples_ok": len(records),
        "interval_ms": interval_ms,
        "samples_min": SAMPLES_MIN,
        "samples_max": SAMPLES_MAX,
        "interval_ms_min": INTERVAL_MS_MIN,
        "interval_ms_max": INTERVAL_MS_MAX,
        "packet_ready_count": packet_ready_count,
        "identity_match_count": sum(1 for record in records if record.get("cache_identity_match") is True),
        "identity_unverified_count": sum(
            1 for record in records if record.get("cache_identity_status") == "cache_identity_unverified"
        ),
        "map_mismatch_count": sum(
            1
            for record in records
            if record.get("cache_identity_match") is False
            or record.get("reason") == "cached_play_plan_map_mismatch"
        ),
        "early_count": sum(1 for record in records if record.get("timing_state") == "early"),
        "ready_window_count": sum(1 for record in records if record.get("timing_state") == "ready_window"),
        "late_count": sum(1 for record in records if record.get("timing_state") == "late"),
        "unique_action_count": len(set(action_ids)),
        "first_action_id": action_ids[0] if action_ids else None,
        "last_action_id": action_ids[-1] if action_ids else None,
        "first_current_time_ms": current_times[0] if current_times else None,
        "last_current_time_ms": current_times[-1] if current_times else None,
        "current_time_delta_ms": (
            current_times[-1] - current_times[0] if len(current_times) >= 2 else None
        ),
        "min_lookup_cost_ms": round(min(lookup_costs), 3) if lookup_costs else None,
        "max_lookup_cost_ms": round(max(lookup_costs), 3) if lookup_costs else None,
        "median_lookup_cost_ms": round(float(statistics.median(lookup_costs)), 3) if lookup_costs else None,
        "max_time_to_action_ms": max(time_to_action_values) if time_to_action_values else None,
        "min_time_to_action_ms": min(time_to_action_values) if time_to_action_values else None,
        "closest_time_to_action_ms": (
            min(time_to_action_values, key=lambda value: abs(value)) if time_to_action_values else None
        ),
        "blockers": blockers,
        "samples": records,
        "created_at": time.time(),
        **_base_flags(),
    }
    payload.update(_base_flags())
    return payload


def build_runtime_packet_loop_payload(
    text: str = "",
    *,
    bridge_state: dict[str, Any] | None = None,
    state_reader: StateReader | None = None,
    activity_reader: ActivityReader | None = None,
    sleeper: Sleeper | None = None,
) -> dict[str, Any]:
    samples_requested, interval_ms, param_blockers = _parse_loop_params(text)
    if param_blockers:
        return _store_payload(
            _empty_payload(
                decision="runtime_packet_loop_hold_no_input",
                reason="invalid_loop_params",
                samples_requested=samples_requested,
                interval_ms=interval_ms,
                blockers=param_blockers,
            )
        )

    cache = get_cached_play_plan()
    if cache is None or cache.get("plan_ready") is not True:
        return _store_payload(
            _empty_payload(
                decision="runtime_packet_loop_hold_no_input",
                reason="no_cached_play_plan",
                samples_requested=samples_requested,
                interval_ms=interval_ms,
                blockers=["no_cached_play_plan"],
            )
        )

    records: list[dict[str, Any]] = []
    sleep_fn = sleeper or time.sleep
    for index in range(samples_requested):
        packet = build_runtime_packet_payload(
            text,
            bridge_state=bridge_state,
            state_reader=state_reader,
            activity_reader=activity_reader,
        )
        record = _sample_record(index, packet)
        records.append(record)
        if _is_hard_hold(record):
            break
        if interval_ms > 0 and index < samples_requested - 1:
            sleep_fn(interval_ms / 1000.0)

    return _store_payload(
        _summarize_samples(
            samples_requested=samples_requested,
            interval_ms=interval_ms,
            records=records,
        )
    )


def build_runtime_packet_loop_status_payload(text: str = "") -> dict[str, Any]:
    if _LAST_RUNTIME_PACKET_LOOP is not None:
        payload = _copy_payload(_LAST_RUNTIME_PACKET_LOOP)
        payload["decision"] = "runtime_packet_loop_status_only_no_input"
        payload["reason"] = "last_runtime_packet_loop"
        payload.update(_base_flags())
        return payload
    return _empty_payload(
        decision="runtime_packet_loop_status_only_no_input",
        reason="no_runtime_packet_loop",
        blockers=["no_runtime_packet_loop"],
    )


def build_runtime_packet_loop_error_payload(exc: Exception) -> dict[str, Any]:
    return _empty_payload(
        decision="runtime_packet_loop_hold_no_input",
        reason=f"runtime_packet_loop_error:{type(exc).__name__}",
        blockers=[f"runtime_packet_loop_error:{type(exc).__name__}"],
    )


def print_runtime_packet_loop_payload(
    payload: dict[str, Any],
    *,
    title: str = "osu! Runtime Packet Loop Preview",
) -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Loop result: {payload['loop_result']}")
    print(f"  Samples attempted: {payload['samples_attempted']}")
    print(f"  Packet ready count: {payload['packet_ready_count']}")
    print(
        "  Identity: "
        f"match={payload['identity_match_count']} "
        f"unverified={payload['identity_unverified_count']} "
        f"mismatch={payload['map_mismatch_count']}"
    )
    print(
        "  Timing: "
        f"early={payload['early_count']} "
        f"ready={payload['ready_window_count']} "
        f"late={payload['late_count']}"
    )
    print(f"  Unique action count: {payload['unique_action_count']}")
    print(
        "  Lookup cost ms: "
        f"min={payload['min_lookup_cost_ms']} "
        f"median={payload['median_lookup_cost_ms']} "
        f"max={payload['max_lookup_cost_ms']}"
    )
    print(f"  First action id: {payload['first_action_id']}")
    print(f"  Last action id: {payload['last_action_id']}")
    print(f"  Blockers: {payload['blockers']}")
    print(f"  Dispatch: {payload['dispatch']}")
    print(f"  Armed: {payload['armed']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
