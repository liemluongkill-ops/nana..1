"""
osu! no-input runtime packet preview - Phase 40.

Builds a compact packet from the cached Phase 39 play plan and current tosu
time. The packet is in-memory and preview-only.
"""

from __future__ import annotations

import copy
import re
import time
from bisect import bisect_left
from typing import Any, Callable

from nana.game.osu.bridge import read_tosu_state
from nana.game.osu.play_plan_cache import (
    beatmap_identity_from_state,
    compare_beatmap_identity,
    get_cached_play_plan,
)
from nana.game.osu.result_source import read_result_source_snapshot


RUNTIME_PACKET_PREVIEW_SCHEMA = "nana.osu.runtime_packet_preview.v1"
RUNTIME_PACKET_SCHEMA = "nana.osu.runtime_packet.v1"
DEFAULT_EARLY_WINDOW_MS = 80
DEFAULT_LATE_WINDOW_MS = 60

StateReader = Callable[[], dict[str, Any]]
ActivityReader = Callable[[dict[str, Any] | None], dict[str, Any]]

_LAST_RUNTIME_PACKET: dict[str, Any] | None = None


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


def clear_runtime_packet_state() -> None:
    global _LAST_RUNTIME_PACKET
    _LAST_RUNTIME_PACKET = None


def _copy_packet(payload: dict[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(payload)
    copied.update(_base_flags())
    return copied


def _store_packet(payload: dict[str, Any]) -> dict[str, Any]:
    global _LAST_RUNTIME_PACKET
    safe_payload = _copy_packet(payload)
    _LAST_RUNTIME_PACKET = _copy_packet(safe_payload)
    return safe_payload


def _safe_state(reader: StateReader | None = None) -> dict[str, Any]:
    try:
        return (reader or read_tosu_state)()
    except Exception as exc:
        return {
            "ok": False,
            "source": "tosu",
            "base_url": None,
            "current_time_ms": None,
            "beatmap": {},
            "raw": None,
            "errors": [f"bridge_read_error:{type(exc).__name__}"],
        }


def _safe_activity(
    state: dict[str, Any],
    *,
    activity_reader: ActivityReader | None = None,
) -> str:
    try:
        payload = (activity_reader or read_result_source_snapshot)(bridge_state=state)
        return str(payload.get("activity_state") or "unknown")
    except Exception:
        return "unknown"


def _parse_int_flag(text: str, name: str, default: int) -> int:
    match = re.search(rf"(?:^|\s){re.escape(name)}=(-?\d+)(?=\s|$)", text or "")
    if not match:
        return int(default)
    return int(match.group(1))


def _parse_windows(text: str) -> tuple[int, int, list[str]]:
    blockers: list[str] = []
    early_window_ms = _parse_int_flag(text, "--early-window-ms", DEFAULT_EARLY_WINDOW_MS)
    late_window_ms = _parse_int_flag(text, "--late-window-ms", DEFAULT_LATE_WINDOW_MS)
    if early_window_ms < 0:
        blockers.append("early_window_ms_negative")
    if late_window_ms < 0:
        blockers.append("late_window_ms_negative")
    return early_window_ms, late_window_ms, blockers


def _empty_payload(
    *,
    decision: str,
    reason: str,
    early_window_ms: int = DEFAULT_EARLY_WINDOW_MS,
    late_window_ms: int = DEFAULT_LATE_WINDOW_MS,
    blockers: list[str] | None = None,
    cache: dict[str, Any] | None = None,
    activity_state: str | None = None,
    current_time_ms: int | None = None,
    lookup_cost_ms: float | None = None,
    current_beatmap_identity: dict[str, Any] | None = None,
    identity_result: dict[str, Any] | None = None,
    map_changed: bool = False,
) -> dict[str, Any]:
    cached_identity = cache.get("cached_beatmap_identity") if cache else None
    identity_result = identity_result or {
        "checked": False,
        "match": None,
        "status": "cache_identity_unverified",
        "checked_fields": [],
        "mismatched_fields": [],
    }
    payload = {
        "schema": RUNTIME_PACKET_PREVIEW_SCHEMA,
        "version": 1,
        "decision": decision,
        "reason": reason,
        "packet_schema": RUNTIME_PACKET_SCHEMA,
        "packet_ready": False,
        "packet_id": None,
        "packet_source": "play_plan_cache" if cache else "unavailable",
        "cache_hit": cache is not None,
        "cache_key": cache.get("cache_key") if cache else None,
        "beatmap_path": cache.get("beatmap_path") if cache else None,
        "map_source": cache.get("map_source") if cache else None,
        "map_changed": bool(map_changed),
        "cache_identity_checked": bool(identity_result.get("checked")),
        "cache_identity_match": identity_result.get("match"),
        "cache_identity_status": identity_result.get("status"),
        "cache_identity_checked_fields": list(identity_result.get("checked_fields") or []),
        "cache_identity_mismatched_fields": list(identity_result.get("mismatched_fields") or []),
        "cached_beatmap_identity": cached_identity,
        "current_beatmap_identity": current_beatmap_identity,
        "plan_ready": bool(cache.get("plan_ready")) if cache else False,
        "object_count": int(cache.get("object_count") or 0) if cache else 0,
        "action_count": int(cache.get("action_count") or 0) if cache else 0,
        "build_cost_ms": float(cache.get("build_cost_ms") or 0.0) if cache else 0.0,
        "lookup_cost_ms": lookup_cost_ms,
        "activity_state": activity_state,
        "action_id": None,
        "action_type": None,
        "source_object_index": None,
        "scheduled_t": None,
        "current_time_ms": current_time_ms,
        "time_to_action_ms": None,
        "early_window_ms": early_window_ms,
        "late_window_ms": late_window_ms,
        "playfield": None,
        "screen": None,
        "calibrated": bool(cache.get("calibrated")) if cache else False,
        "timing_state": "no_action",
        "suggested_intent": None,
        "blockers": list(blockers or []),
        "created_at": time.time(),
        **_base_flags(),
    }
    payload.update(_base_flags())
    return payload


def _lookup_action(actions: list[dict[str, Any]], current_time_ms: int) -> tuple[dict[str, Any] | None, float]:
    started = time.perf_counter()
    times = [int(action.get("scheduled_t") or 0) for action in actions]
    index = bisect_left(times, int(current_time_ms))
    if index >= len(actions):
        index = len(actions) - 1 if actions else 0
    action = actions[index] if actions else None
    return action, round((time.perf_counter() - started) * 1000.0, 3)


def _timing_state(time_to_action_ms: int, *, early_window_ms: int, late_window_ms: int) -> str:
    if time_to_action_ms > early_window_ms:
        return "early"
    if -late_window_ms <= time_to_action_ms <= early_window_ms:
        return "ready_window"
    return "late"


def _suggested_intent(action_type: str | None) -> str:
    if action_type == "tap_candidate":
        return "would_tap"
    if action_type == "slider_follow_candidate":
        return "would_slider_follow"
    if action_type == "spinner_candidate":
        return "would_spinner_follow_or_hold"
    return "would_move_only"


def build_runtime_packet_payload(
    text: str = "",
    *,
    bridge_state: dict[str, Any] | None = None,
    state_reader: StateReader | None = None,
    activity_reader: ActivityReader | None = None,
) -> dict[str, Any]:
    early_window_ms, late_window_ms, param_blockers = _parse_windows(text)
    cache = get_cached_play_plan()
    if param_blockers:
        return _store_packet(
            _empty_payload(
                decision="runtime_packet_hold_no_input",
                reason="invalid_runtime_packet_params",
                early_window_ms=early_window_ms,
                late_window_ms=late_window_ms,
                blockers=param_blockers,
                cache=cache,
            )
        )
    if cache is None or cache.get("plan_ready") is not True:
        return _store_packet(
            _empty_payload(
                decision="runtime_packet_hold_no_input",
                reason="no_cached_play_plan",
                early_window_ms=early_window_ms,
                late_window_ms=late_window_ms,
                blockers=["no_cached_play_plan"],
                cache=cache,
            )
        )

    state = bridge_state if bridge_state is not None else _safe_state(state_reader)
    activity_state = _safe_activity(state, activity_reader=activity_reader)
    current_identity = beatmap_identity_from_state(state)
    identity_result = compare_beatmap_identity(cache.get("cached_beatmap_identity"), current_identity)
    map_changed = identity_result.get("match") is False
    current_time_ms = state.get("current_time_ms")
    if current_time_ms is None:
        return _store_packet(
            _empty_payload(
                decision="runtime_packet_hold_no_input",
                reason="current_time_missing",
                early_window_ms=early_window_ms,
                late_window_ms=late_window_ms,
                blockers=["current_time_missing"],
                cache=cache,
                activity_state=activity_state,
                current_beatmap_identity=current_identity,
                identity_result=identity_result,
                map_changed=map_changed,
            )
        )
    if activity_state != "gameplay":
        return _store_packet(
            _empty_payload(
                decision="runtime_packet_hold_no_input",
                reason=f"activity_not_gameplay:{activity_state}",
                early_window_ms=early_window_ms,
                late_window_ms=late_window_ms,
                blockers=[f"activity_not_gameplay:{activity_state}"],
                cache=cache,
                activity_state=activity_state,
                current_time_ms=int(current_time_ms),
                current_beatmap_identity=current_identity,
                identity_result=identity_result,
                map_changed=map_changed,
            )
        )
    if map_changed:
        return _store_packet(
            _empty_payload(
                decision="runtime_packet_hold_no_input",
                reason="cached_play_plan_map_mismatch",
                early_window_ms=early_window_ms,
                late_window_ms=late_window_ms,
                blockers=["cached_play_plan_map_mismatch"],
                cache=cache,
                activity_state=activity_state,
                current_time_ms=int(current_time_ms),
                current_beatmap_identity=current_identity,
                identity_result=identity_result,
                map_changed=True,
            )
        )

    actions = [dict(action) for action in cache.get("actions") or []]
    action, lookup_cost_ms = _lookup_action(actions, int(current_time_ms))
    if action is None:
        return _store_packet(
            _empty_payload(
                decision="runtime_packet_hold_no_input",
                reason="action_plan_empty",
                early_window_ms=early_window_ms,
                late_window_ms=late_window_ms,
                blockers=["action_plan_empty"],
                cache=cache,
                activity_state=activity_state,
                current_time_ms=int(current_time_ms),
                lookup_cost_ms=lookup_cost_ms,
                current_beatmap_identity=current_identity,
                identity_result=identity_result,
                map_changed=map_changed,
            )
        )

    scheduled_t = int(action.get("scheduled_t") or 0)
    time_to_action_ms = scheduled_t - int(current_time_ms)
    action_type = action.get("action_type")
    timing_state = _timing_state(
        time_to_action_ms,
        early_window_ms=early_window_ms,
        late_window_ms=late_window_ms,
    )
    payload = {
        "schema": RUNTIME_PACKET_PREVIEW_SCHEMA,
        "version": 1,
        "decision": "runtime_packet_preview_only_no_input",
        "reason": "packet_from_cached_play_plan",
        "packet_schema": RUNTIME_PACKET_SCHEMA,
        "packet_ready": True,
        "packet_id": f"runtime-packet-{action.get('action_id')}-{scheduled_t}",
        "packet_source": "play_plan_cache",
        "cache_hit": True,
        "cache_key": cache.get("cache_key"),
        "beatmap_path": cache.get("beatmap_path"),
        "map_source": cache.get("map_source"),
        "map_changed": bool(map_changed),
        "cache_identity_checked": bool(identity_result.get("checked")),
        "cache_identity_match": identity_result.get("match"),
        "cache_identity_status": identity_result.get("status"),
        "cache_identity_checked_fields": list(identity_result.get("checked_fields") or []),
        "cache_identity_mismatched_fields": list(identity_result.get("mismatched_fields") or []),
        "cached_beatmap_identity": cache.get("cached_beatmap_identity"),
        "current_beatmap_identity": current_identity,
        "plan_ready": True,
        "object_count": int(cache.get("object_count") or 0),
        "action_count": int(cache.get("action_count") or 0),
        "build_cost_ms": float(cache.get("build_cost_ms") or 0.0),
        "lookup_cost_ms": lookup_cost_ms,
        "activity_state": activity_state,
        "action_id": action.get("action_id"),
        "action_type": action_type,
        "source_object_index": action.get("source_object_index"),
        "scheduled_t": scheduled_t,
        "current_time_ms": int(current_time_ms),
        "time_to_action_ms": time_to_action_ms,
        "early_window_ms": early_window_ms,
        "late_window_ms": late_window_ms,
        "playfield": action.get("playfield"),
        "screen": action.get("screen"),
        "calibrated": bool(cache.get("calibrated")),
        "timing_state": timing_state,
        "suggested_intent": _suggested_intent(str(action_type) if action_type else None),
        "blockers": [],
        "created_at": time.time(),
        **_base_flags(),
    }
    payload.update(_base_flags())
    return _store_packet(payload)


def build_runtime_packet_status_payload(text: str = "") -> dict[str, Any]:
    if _LAST_RUNTIME_PACKET is not None:
        payload = _copy_packet(_LAST_RUNTIME_PACKET)
        payload["decision"] = "runtime_packet_status_only_no_input"
        payload["reason"] = "last_runtime_packet"
        payload.update(_base_flags())
        return payload

    cache = get_cached_play_plan()
    payload = _empty_payload(
        decision="runtime_packet_status_only_no_input",
        reason="no_runtime_packet",
        blockers=["no_runtime_packet"] if cache is not None else ["no_cached_play_plan"],
        cache=cache,
    )
    payload.update(_base_flags())
    return payload


def build_runtime_packet_error_payload(exc: Exception) -> dict[str, Any]:
    return _empty_payload(
        decision="runtime_packet_hold_no_input",
        reason=f"runtime_packet_error:{type(exc).__name__}",
        blockers=[f"runtime_packet_error:{type(exc).__name__}"],
    )


def print_runtime_packet_payload(payload: dict[str, Any], *, title: str = "osu! Runtime Packet Preview") -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Packet ready: {payload['packet_ready']}")
    print(f"  Packet source: {payload.get('packet_source')}")
    print(f"  Packet id: {payload.get('packet_id')}")
    print(f"  Cache identity status: {payload.get('cache_identity_status')}")
    print(f"  Cache identity match: {payload.get('cache_identity_match')}")
    print(f"  Map changed: {payload.get('map_changed')}")
    print(f"  Action id: {payload.get('action_id')}")
    print(f"  Action type: {payload.get('action_type')}")
    print(f"  Scheduled t: {payload.get('scheduled_t')}")
    print(f"  Current time ms: {payload.get('current_time_ms')}")
    print(f"  Time to action ms: {payload.get('time_to_action_ms')}")
    print(f"  Timing state: {payload.get('timing_state')}")
    print(f"  Suggested intent: {payload.get('suggested_intent')}")
    print(f"  Playfield: {payload.get('playfield')}")
    print(f"  Screen: {payload.get('screen')}")
    print(f"  Lookup cost ms: {payload.get('lookup_cost_ms')}")
    print(f"  Blockers: {payload['blockers']}")
    print(f"  Dispatch: {payload['dispatch']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
