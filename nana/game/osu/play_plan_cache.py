"""
osu! no-input play plan cache - Phase 39.

Builds a lightweight, time-indexed action plan from the current beatmap and
keeps it in memory for fast runtime tick lookup. This module never dispatches
input and does not depend on the pre-input commit, shadow dispatch, or dispatch
preflight dossier stack.
"""

from __future__ import annotations

import hashlib
import time
from bisect import bisect_left
from typing import Any, Callable

from nana.game.osu.beatmap_parser import parse_osu_text
from nana.game.osu.bridge import read_current_map_text, read_tosu_state
from nana.game.osu.calibration import PlayfieldRect, parse_playfield_rect, playfield_to_screen
from nana.game.osu.result_source import read_result_source_snapshot


PLAY_PLAN_SCHEMA = "nana.osu.play_plan_cache.v1"
RUNTIME_TICK_SCHEMA = "nana.osu.runtime_tick_preview.v1"

StateReader = Callable[[], dict[str, Any]]
MapReader = Callable[[dict[str, Any] | None], tuple[str | None, str | None, str | None]]
CalibrationReader = Callable[[], PlayfieldRect | None]
ActivityReader = Callable[[dict[str, Any] | None], dict[str, Any]]

_PLAN_CACHE: dict[str, Any] | None = None


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


def clear_play_plan_cache() -> None:
    global _PLAN_CACHE
    _PLAN_CACHE = None


def get_cached_play_plan() -> dict[str, Any] | None:
    if _PLAN_CACHE is None:
        return None
    payload = dict(_PLAN_CACHE)
    payload["actions"] = [dict(action) for action in _PLAN_CACHE.get("actions") or []]
    payload.update(_base_flags())
    return payload


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


def _safe_map_text(
    state: dict[str, Any],
    *,
    map_text: str | None = None,
    map_source: str | None = None,
    map_reader: MapReader | None = None,
) -> tuple[str | None, str | None, str | None]:
    if map_text is not None:
        return map_text, map_source or "provided_map_text", None
    try:
        return (map_reader or read_current_map_text)(state)
    except Exception as exc:
        return None, None, f"current_map_read_error:{type(exc).__name__}"


def _safe_rect(
    *,
    playfield_rect: PlayfieldRect | None | str = None,
    calibration_reader: CalibrationReader | None = None,
) -> tuple[PlayfieldRect | None, str | None]:
    if isinstance(playfield_rect, PlayfieldRect):
        return playfield_rect, None
    if playfield_rect == "missing":
        return None, "playfield_rect_missing"
    try:
        return (calibration_reader or parse_playfield_rect)(), None
    except Exception as exc:
        return None, f"playfield_rect_error:{type(exc).__name__}"


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


def _map_key(map_text: str | None, map_source: str | None) -> str | None:
    if not map_text:
        return None
    digest = hashlib.sha1(str(map_text).encode("utf-8", errors="replace")).hexdigest()
    return f"{map_source or 'unknown'}:{digest}"


def _identity_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def beatmap_identity_from_state(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    beatmap = state.get("beatmap") or {}
    identity = {
        "id": _identity_value(beatmap.get("id")),
        "set_id": _identity_value(beatmap.get("set_id")),
        "path": _identity_value(beatmap.get("path")),
        "folder": _identity_value(beatmap.get("folder")),
        "file": _identity_value(beatmap.get("file")),
        "artist": _identity_value(beatmap.get("artist")),
        "title": _identity_value(beatmap.get("title")),
        "version": _identity_value(beatmap.get("version")),
        "creator": _identity_value(beatmap.get("creator")),
        "state_source": _identity_value(state.get("source")),
        "state_base_url": _identity_value(state.get("base_url")),
    }
    identity["available"] = any(
        identity.get(field) is not None
        for field in ("id", "set_id", "path", "folder", "file", "artist", "title", "version")
    )
    identity["stable_fields_present"] = [
        field
        for field in ("id", "set_id", "path", "folder", "file")
        if identity.get(field) is not None
    ]
    return identity


def compare_beatmap_identity(
    cached: dict[str, Any] | None,
    current: dict[str, Any] | None,
) -> dict[str, Any]:
    checked_fields: list[str] = []
    mismatched_fields: list[str] = []
    for field in ("id", "set_id", "path", "folder", "file"):
        cached_value = _identity_value((cached or {}).get(field))
        current_value = _identity_value((current or {}).get(field))
        if cached_value is None or current_value is None:
            continue
        checked_fields.append(field)
        if cached_value != current_value:
            mismatched_fields.append(field)

    if mismatched_fields:
        return {
            "checked": True,
            "match": False,
            "status": "cache_identity_mismatch",
            "checked_fields": checked_fields,
            "mismatched_fields": mismatched_fields,
        }
    if checked_fields:
        return {
            "checked": True,
            "match": True,
            "status": "cache_identity_match",
            "checked_fields": checked_fields,
            "mismatched_fields": [],
        }
    return {
        "checked": False,
        "match": None,
        "status": "cache_identity_unverified",
        "checked_fields": [],
        "mismatched_fields": [],
    }


def _rect_payload(rect: PlayfieldRect | None) -> dict[str, Any] | None:
    if rect is None:
        return None
    return {
        "left": rect.left,
        "top": rect.top,
        "width": rect.width,
        "height": rect.height,
        "right": rect.right,
        "bottom": rect.bottom,
        "source": rect.source,
    }


def _screen_payload(screen: dict[str, Any] | None) -> dict[str, Any] | None:
    if not screen:
        return None
    return {
        "x": screen.get("screen_x"),
        "y": screen.get("screen_y"),
    }


def _action_type(kind: str | None) -> str:
    if kind == "circle":
        return "tap_candidate"
    if kind == "slider":
        return "slider_follow_candidate"
    if kind == "spinner":
        return "spinner_candidate"
    return "move_to_candidate"


def _playfield(obj: dict[str, Any]) -> dict[str, Any]:
    return {"x": obj.get("x"), "y": obj.get("y")}


def _build_action(obj: dict[str, Any], *, action_index: int, rect: PlayfieldRect | None) -> dict[str, Any]:
    playfield = _playfield(obj)
    screen = None
    if rect is not None and playfield.get("x") is not None and playfield.get("y") is not None:
        screen = _screen_payload(playfield_to_screen(float(playfield["x"]), float(playfield["y"]), rect))
    return {
        "action_id": f"plan-{action_index:04d}",
        "action_type": _action_type(obj.get("kind")),
        "source_object_index": obj.get("index"),
        "scheduled_t": obj.get("time"),
        "playfield": playfield,
        "screen": screen,
        "executable": False,
    }


def _empty_plan_payload(
    *,
    decision: str,
    reason: str,
    map_source: str | None = None,
    map_key: str | None = None,
    cache_hit: bool = False,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": PLAY_PLAN_SCHEMA,
        "version": 1,
        "decision": decision,
        "reason": reason,
        "plan_ready": False,
        "cache_hit": bool(cache_hit),
        "cache_key": map_key,
        "beatmap_path": map_source,
        "map_source": map_source,
        "map_changed": False,
        "cached_beatmap_identity": None,
        "object_count": 0,
        "action_count": 0,
        "build_cost_ms": 0.0,
        "lookup_cost_ms": None,
        "current_time_ms": None,
        "next_action_id": None,
        "next_action_t": None,
        "time_to_action_ms": None,
        "playfield": None,
        "screen": None,
        "calibrated": False,
        "playfield_rect": None,
        "actions": [],
        "created_at": time.time(),
        "blockers": list(blockers or []),
        **_base_flags(),
    }
    payload.update(_base_flags())
    return payload


def _plan_from_cache(map_key: str | None) -> dict[str, Any] | None:
    if _PLAN_CACHE is None or not map_key:
        return None
    if _PLAN_CACHE.get("cache_key") != map_key:
        return None
    return _PLAN_CACHE


def _store_plan(plan: dict[str, Any]) -> dict[str, Any]:
    global _PLAN_CACHE
    _PLAN_CACHE = dict(plan)
    _PLAN_CACHE["actions"] = [dict(action) for action in plan.get("actions") or []]
    return _PLAN_CACHE


def _build_plan(
    *,
    state: dict[str, Any],
    map_text: str | None,
    map_source: str | None,
    map_error: str | None,
    rect: PlayfieldRect | None,
    calibration_error: str | None,
    force_rebuild: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    key = _map_key(map_text, map_source)
    beatmap_identity = beatmap_identity_from_state(state)
    existing = _plan_from_cache(key)
    if existing is not None and not force_rebuild:
        payload = dict(existing)
        payload["actions"] = [dict(action) for action in existing.get("actions") or []]
        payload["decision"] = "play_plan_cache_ready_no_input"
        payload["reason"] = "cache_hit"
        payload["cache_hit"] = True
        payload["map_changed"] = False
        payload["build_cost_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        payload.update(_base_flags())
        return payload

    blockers: list[str] = []
    if map_error:
        blockers.append(map_error)
    if not map_text:
        blockers.append("current_map_unavailable")
    if not state.get("ok"):
        blockers.append("tosu_bridge_not_ok")
    if blockers:
        return _empty_plan_payload(
            decision="play_plan_cache_hold_no_input",
            reason=blockers[0],
            map_source=map_source,
            map_key=key,
            blockers=blockers,
        )

    try:
        parsed = parse_osu_text(map_text or "", source=map_source)
    except Exception as exc:
        return _empty_plan_payload(
            decision="play_plan_cache_hold_no_input",
            reason=f"map_parse_error:{type(exc).__name__}",
            map_source=map_source,
            map_key=key,
            blockers=[f"map_parse_error:{type(exc).__name__}"],
        )

    objects = list(parsed.get("hit_objects") or [])
    actions = [
        _build_action(obj, action_index=index, rect=rect)
        for index, obj in enumerate(objects)
        if obj.get("time") is not None
    ]
    actions.sort(key=lambda action: int(action.get("scheduled_t") or 0))
    if not actions:
        return _empty_plan_payload(
            decision="play_plan_cache_hold_no_input",
            reason="action_plan_empty",
            map_source=map_source,
            map_key=key,
            blockers=["action_plan_empty"],
        )

    old_key = _PLAN_CACHE.get("cache_key") if _PLAN_CACHE else None
    payload = {
        "schema": PLAY_PLAN_SCHEMA,
        "version": 1,
        "decision": "play_plan_cache_ready_no_input",
        "reason": "plan_built",
        "plan_ready": True,
        "cache_hit": False,
        "cache_key": key,
        "beatmap_path": map_source,
        "map_source": map_source,
        "map_changed": bool(old_key and old_key != key),
        "cached_beatmap_identity": beatmap_identity,
        "object_count": int((parsed.get("stats") or {}).get("total") or len(objects)),
        "action_count": len(actions),
        "build_cost_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "lookup_cost_ms": None,
        "current_time_ms": state.get("current_time_ms"),
        "next_action_id": None,
        "next_action_t": None,
        "time_to_action_ms": None,
        "playfield": None,
        "screen": None,
        "calibrated": rect is not None,
        "calibration_error": calibration_error,
        "playfield_rect": _rect_payload(rect),
        "actions": actions,
        "created_at": time.time(),
        "blockers": [],
        **_base_flags(),
    }
    payload.update(_base_flags())
    return dict(_store_plan(payload))


def build_play_plan_payload(
    text: str = "",
    *,
    bridge_state: dict[str, Any] | None = None,
    map_text: str | None = None,
    map_source: str | None = None,
    playfield_rect: PlayfieldRect | None | str = None,
    state_reader: StateReader | None = None,
    map_reader: MapReader | None = None,
    calibration_reader: CalibrationReader | None = None,
    force_rebuild: bool = False,
) -> dict[str, Any]:
    state = bridge_state if bridge_state is not None else _safe_state(state_reader)
    current_map_text, current_map_source, map_error = _safe_map_text(
        state,
        map_text=map_text,
        map_source=map_source,
        map_reader=map_reader,
    )
    rect, calibration_error = _safe_rect(
        playfield_rect=playfield_rect,
        calibration_reader=calibration_reader,
    )
    return _build_plan(
        state=state,
        map_text=current_map_text,
        map_source=current_map_source,
        map_error=map_error,
        rect=rect,
        calibration_error=calibration_error,
        force_rebuild=force_rebuild,
    )


def build_play_plan_status_payload(text: str = "") -> dict[str, Any]:
    if _PLAN_CACHE is None:
        return _empty_plan_payload(
            decision="play_plan_cache_status_only_no_input",
            reason="no_cached_play_plan",
            blockers=["no_cached_play_plan"],
        )
    payload = dict(_PLAN_CACHE)
    payload["actions"] = [dict(action) for action in _PLAN_CACHE.get("actions") or []]
    payload["decision"] = "play_plan_cache_status_only_no_input"
    payload["reason"] = "cache_hit"
    payload["cache_hit"] = True
    payload["build_cost_ms"] = 0.0
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


def build_runtime_tick_payload(
    text: str = "",
    *,
    bridge_state: dict[str, Any] | None = None,
    map_text: str | None = None,
    map_source: str | None = None,
    playfield_rect: PlayfieldRect | None | str = None,
    state_reader: StateReader | None = None,
    map_reader: MapReader | None = None,
    calibration_reader: CalibrationReader | None = None,
    activity_reader: ActivityReader | None = None,
) -> dict[str, Any]:
    state = bridge_state if bridge_state is not None else _safe_state(state_reader)
    activity_state = _safe_activity(state, activity_reader=activity_reader)
    current_time_ms = state.get("current_time_ms")
    if current_time_ms is None:
        payload = _empty_plan_payload(
            decision="runtime_tick_hold_no_input",
            reason="current_time_missing",
            blockers=["current_time_missing"],
        )
        payload.update({"schema": RUNTIME_TICK_SCHEMA, "activity_state": activity_state})
        payload.update(_base_flags())
        return payload
    if activity_state != "gameplay":
        payload = _empty_plan_payload(
            decision="runtime_tick_hold_no_input",
            reason=f"activity_not_gameplay:{activity_state}",
            blockers=[f"activity_not_gameplay:{activity_state}"],
        )
        payload.update({"schema": RUNTIME_TICK_SCHEMA, "activity_state": activity_state, "current_time_ms": current_time_ms})
        payload.update(_base_flags())
        return payload

    plan = build_play_plan_payload(
        text,
        bridge_state=state,
        map_text=map_text,
        map_source=map_source,
        playfield_rect=playfield_rect,
        map_reader=map_reader,
        calibration_reader=calibration_reader,
    )
    if plan.get("plan_ready") is not True:
        payload = dict(plan)
        payload["schema"] = RUNTIME_TICK_SCHEMA
        payload["decision"] = "runtime_tick_hold_no_input"
        payload["activity_state"] = activity_state
        payload["current_time_ms"] = current_time_ms
        payload.update(_base_flags())
        return payload

    action, lookup_cost_ms = _lookup_action(list(plan.get("actions") or []), int(current_time_ms))
    if action is None:
        payload = dict(plan)
        payload["schema"] = RUNTIME_TICK_SCHEMA
        payload["decision"] = "runtime_tick_hold_no_input"
        payload["reason"] = "action_plan_empty"
        payload["blockers"] = ["action_plan_empty"]
        payload["activity_state"] = activity_state
        payload["lookup_cost_ms"] = lookup_cost_ms
        payload.update(_base_flags())
        return payload

    scheduled_t = int(action.get("scheduled_t") or 0)
    payload = {
        "schema": RUNTIME_TICK_SCHEMA,
        "version": 1,
        "decision": "runtime_tick_preview_only_no_input",
        "reason": "next_action_lookup",
        "plan_ready": True,
        "cache_hit": bool(plan.get("cache_hit")),
        "cache_key": plan.get("cache_key"),
        "beatmap_path": plan.get("beatmap_path"),
        "map_source": plan.get("map_source"),
        "map_changed": bool(plan.get("map_changed")),
        "activity_state": activity_state,
        "object_count": int(plan.get("object_count") or 0),
        "action_count": int(plan.get("action_count") or 0),
        "build_cost_ms": float(plan.get("build_cost_ms") or 0.0),
        "lookup_cost_ms": lookup_cost_ms,
        "current_time_ms": int(current_time_ms),
        "next_action_id": action.get("action_id"),
        "next_action_type": action.get("action_type"),
        "next_action_t": scheduled_t,
        "time_to_action_ms": scheduled_t - int(current_time_ms),
        "source_object_index": action.get("source_object_index"),
        "playfield": action.get("playfield"),
        "screen": action.get("screen"),
        "calibrated": bool(plan.get("calibrated")),
        "playfield_rect": plan.get("playfield_rect"),
        "blockers": [],
        "created_at": time.time(),
        **_base_flags(),
    }
    payload.update(_base_flags())
    return payload


def print_play_plan_payload(payload: dict[str, Any], *, title: str = "osu! Play Plan Cache") -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Plan ready: {payload['plan_ready']}")
    print(f"  Cache hit: {payload['cache_hit']}")
    print(f"  Beatmap path: {payload.get('beatmap_path')}")
    print(f"  Object count: {payload['object_count']}")
    print(f"  Action count: {payload['action_count']}")
    print(f"  Build cost ms: {payload['build_cost_ms']}")
    print(f"  Calibrated: {payload['calibrated']}")
    print(f"  Blockers: {payload['blockers']}")
    print(f"  Dispatch: {payload['dispatch']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")


def print_runtime_tick_payload(payload: dict[str, Any], *, title: str = "osu! Runtime Tick Preview") -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Plan ready: {payload['plan_ready']}")
    print(f"  Cache hit: {payload['cache_hit']}")
    print(f"  Beatmap path: {payload.get('beatmap_path')}")
    print(f"  Object count: {payload['object_count']}")
    print(f"  Action count: {payload['action_count']}")
    print(f"  Build cost ms: {payload['build_cost_ms']}")
    print(f"  Lookup cost ms: {payload.get('lookup_cost_ms')}")
    print(f"  Current time ms: {payload.get('current_time_ms')}")
    print(f"  Next action id: {payload.get('next_action_id')}")
    print(f"  Next action t: {payload.get('next_action_t')}")
    print(f"  Time to action ms: {payload.get('time_to_action_ms')}")
    print(f"  Playfield: {payload.get('playfield')}")
    print(f"  Screen: {payload.get('screen')}")
    print(f"  Blockers: {payload['blockers']}")
    print(f"  Dispatch: {payload['dispatch']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
