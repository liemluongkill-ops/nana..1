"""
osu! input readiness + lease preview — Phase 22.

Read-only readiness layer for future osu! input work.

No live input. No mouse/click. No SendInput. No score submit.
No VTS. No ElevenLabs. No OBS API. No Vision. No Stardew changes.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from nana.game.osu.beatmap_parser import parse_osu_text
from nana.game.osu.bridge import read_current_map_text, read_tosu_state
from nana.game.osu.calibration import PlayfieldRect, parse_playfield_rect
from nana.game.osu.input_safety import (
    read_emergency_abort_readiness,
    read_emergency_release_readiness,
    read_osu_focus_readiness,
)
from nana.game.osu.result_source import read_result_source_snapshot
from nana.game.osu.router import DEFAULT_AIM_LEAD_MS, aim_timeline_telemetry, preview_next_objects


DEFAULT_READINESS_STALE_MS = 3000
DEFAULT_LEASE_LIMIT = 15


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_flags(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {
        "stale_ms": DEFAULT_READINESS_STALE_MS,
        "lead_ms": DEFAULT_AIM_LEAD_MS,
        "limit": DEFAULT_LEASE_LIMIT,
    }
    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        if name == "stale-ms":
            values["stale_ms"] = int(raw_value)
        elif name == "lead-ms":
            values["lead_ms"] = int(raw_value)
        elif name == "limit":
            values["limit"] = int(raw_value)
        elif name in {"adapter-enabled", "zone-enabled", "osu-zone-enabled"}:
            values["adapter_enabled"] = _is_truthy(raw_value)
    if int(values["stale_ms"]) <= 0:
        raise ValueError("stale_ms_must_be_positive")
    if int(values["lead_ms"]) < 0:
        raise ValueError("lead_ms_must_be_non_negative")
    if int(values["limit"]) <= 0:
        raise ValueError("limit_must_be_positive")
    return values


def _read_bridge_state() -> dict[str, Any]:
    try:
        return read_tosu_state()
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


def _detect_activity_state(state: dict[str, Any]) -> str:
    try:
        return read_result_source_snapshot(bridge_state=state).get("activity_state") or "unknown"
    except Exception:
        return "unknown"


def _read_map_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    current_text, source, error = read_current_map_text(state)
    if not current_text:
        return {
            "loaded": False,
            "source": source,
            "error": error or "current_map_unavailable",
            "parsed": None,
            "object_count": 0,
            "stream_available": False,
            "first_time": None,
            "last_time": None,
        }
    try:
        parsed = parse_osu_text(current_text, source=source)
    except Exception as exc:
        return {
            "loaded": False,
            "source": source,
            "error": f"map_parse_error:{type(exc).__name__}",
            "parsed": None,
            "object_count": 0,
            "stream_available": False,
            "first_time": None,
            "last_time": None,
        }
    stats = parsed.get("stats") or {}
    object_count = int(stats.get("total") or 0)
    return {
        "loaded": True,
        "source": source,
        "error": None,
        "parsed": parsed,
        "object_count": object_count,
        "stream_available": object_count > 0,
        "first_time": stats.get("first_time"),
        "last_time": stats.get("last_time"),
    }


def _read_calibration_snapshot() -> dict[str, Any]:
    try:
        rect = parse_playfield_rect()
    except ValueError as exc:
        return {
            "calibrated": False,
            "rect": None,
            "error": str(exc),
        }
    if rect is None:
        return {
            "calibrated": False,
            "rect": None,
            "error": "playfield_rect_missing",
        }
    return {
        "calibrated": True,
        "rect": rect,
        "error": None,
        }


def _read_input_safety_snapshot() -> dict[str, Any]:
    try:
        focus = read_osu_focus_readiness()
    except Exception as exc:
        focus = {
            "focus_readiness": "error",
            "reason": f"focus_readiness_error:{type(exc).__name__}",
        }
    try:
        release = read_emergency_release_readiness()
    except Exception as exc:
        release = {
            "emergency_release_available": "error",
            "release_strategy": "preview_only",
            "reason": f"release_readiness_error:{type(exc).__name__}",
        }
    try:
        abort = read_emergency_abort_readiness()
    except Exception as exc:
        abort = {
            "emergency_abort_available": "error",
            "abort_strategy": "preview_only",
            "reason": f"abort_readiness_error:{type(exc).__name__}",
        }
    return {
        "focus": focus,
        "release": release,
        "abort": abort,
    }


def _bridge_freshness(state: dict[str, Any], *, stale_ms: int) -> dict[str, Any]:
    current_time = state.get("current_time_ms")
    if current_time is None:
        return {
            "fresh": False,
            "stale": False,
            "age_ms": None,
            "reason": "current_time_missing",
        }
    try:
        int(current_time)
    except (TypeError, ValueError):
        return {
            "fresh": False,
            "stale": True,
            "age_ms": None,
            "reason": "current_time_invalid",
        }
    return {
        "fresh": True,
        "stale": False,
        "age_ms": 0,
        "reason": "current_time_available",
        "stale_threshold_ms": stale_ms,
    }


def _maybe_timing_preview(
    parsed: dict[str, Any] | None,
    *,
    current_time_ms: int | None,
    playfield_rect: PlayfieldRect | None,
    lead_ms: int,
    limit: int,
) -> dict[str, Any]:
    started = time.perf_counter()

    def with_cost(payload: dict[str, Any]) -> dict[str, Any]:
        payload["preview_cost_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        return payload

    if not parsed:
        return with_cost({
            "lead_ms": int(lead_ms),
            "limit": int(limit),
            "count": 0,
            "calibrated": playfield_rect is not None,
            "immediate_window_count": 0,
            "upcoming_window_count": 0,
            "first_time": None,
            "last_time": None,
            "real_input": False,
            "submit": False,
        })
    try:
        telemetry = aim_timeline_telemetry(
            parsed,
            current_time_ms=current_time_ms,
            lead_ms=int(lead_ms),
            limit=int(limit),
            playfield_rect=playfield_rect,
        )
    except Exception:
        preview = preview_next_objects(
            parsed,
            current_time_ms=current_time_ms,
            limit=int(limit),
            playfield_rect=playfield_rect,
        )
        return with_cost({
            "lead_ms": int(lead_ms),
            "limit": int(limit),
            "count": int(preview.get("count") or 0),
            "calibrated": bool(preview.get("calibrated")),
            "immediate_window_count": 0,
            "upcoming_window_count": 0,
            "first_time": preview.get("first_time"),
            "last_time": preview.get("last_time"),
            "real_input": False,
            "submit": False,
        })
    return with_cost({
        "lead_ms": int(telemetry.get("lead_ms") or lead_ms),
        "limit": int(telemetry.get("limit") or limit),
        "count": int(telemetry.get("count") or 0),
        "calibrated": bool(telemetry.get("calibrated")),
        "immediate_window_count": int(telemetry.get("immediate_window_count") or 0),
        "upcoming_window_count": int(telemetry.get("upcoming_window_count") or 0),
        "first_time": telemetry["objects"][0].get("hit_time") if telemetry.get("objects") else None,
        "last_time": telemetry["objects"][-1].get("hit_time") if telemetry.get("objects") else None,
        "real_input": False,
        "submit": False,
    })


def build_input_readiness_status(
    text: str = "",
    *,
    bridge_state: dict[str, Any] | None = None,
    map_text: str | None = None,
    map_source: str | None = None,
    playfield_rect: PlayfieldRect | None | str = None,
) -> dict[str, Any]:
    """
    Build read-only osu! input readiness status.

    Optional args are for smoke/mock tests; command path reads existing bridge and
    calibration only.
    """
    flags = _parse_flags(text)
    adapter_enabled = bool(flags.get("adapter_enabled", True))
    stale_ms = int(flags["stale_ms"])
    lead_ms = int(flags["lead_ms"])
    limit = int(flags["limit"])

    state = bridge_state if bridge_state is not None else _read_bridge_state()
    bridge_ok = bool(state.get("ok"))
    current_time_ms = state.get("current_time_ms")
    activity_state = _detect_activity_state(state)

    if map_text is not None:
        parsed = parse_osu_text(map_text, source=map_source)
        stats = parsed.get("stats") or {}
        map_snapshot = {
            "loaded": True,
            "source": map_source,
            "error": None,
            "parsed": parsed,
            "object_count": int(stats.get("total") or 0),
            "stream_available": int(stats.get("total") or 0) > 0,
            "first_time": stats.get("first_time"),
            "last_time": stats.get("last_time"),
        }
    else:
        map_snapshot = _read_map_snapshot(state)

    if isinstance(playfield_rect, PlayfieldRect):
        calibration = {"calibrated": True, "rect": playfield_rect, "error": None}
    elif playfield_rect == "missing":
        calibration = {"calibrated": False, "rect": None, "error": "playfield_rect_missing"}
    else:
        calibration = _read_calibration_snapshot()

    freshness = _bridge_freshness(state, stale_ms=stale_ms)
    safety = _read_input_safety_snapshot()
    focus = safety["focus"]
    release = safety["release"]
    abort = safety["abort"]
    rect = calibration.get("rect") if isinstance(calibration.get("rect"), PlayfieldRect) else None
    timing = _maybe_timing_preview(
        map_snapshot.get("parsed"),
        current_time_ms=current_time_ms,
        playfield_rect=rect,
        lead_ms=lead_ms,
        limit=limit,
    )

    blockers: list[str] = []
    if not adapter_enabled:
        blockers.append("adapter_zone_disabled")
    if not bridge_ok:
        blockers.append("tosu_bridge_not_ok")
    if current_time_ms is None:
        blockers.append("current_time_missing")
    if not map_snapshot["loaded"]:
        blockers.append("map_not_loaded")
    if not map_snapshot["stream_available"]:
        blockers.append("object_stream_unavailable")
    if not calibration["calibrated"]:
        blockers.append("playfield_uncalibrated")
    if activity_state != "gameplay":
        blockers.append(f"activity_not_gameplay:{activity_state}")
    if freshness.get("stale"):
        blockers.append("bridge_state_stale")

    readiness = "ready" if not blockers else "not_ready"

    return {
        "decision": "input_readiness_status_only_no_input",
        "adapter": "osu",
        "adapter_zone_enabled": adapter_enabled,
        "bridge_ok": bridge_ok,
        "bridge_source": state.get("source") or "tosu",
        "bridge_base_url": state.get("base_url"),
        "bridge_errors": list(state.get("errors") or []),
        "current_time_ms": current_time_ms,
        "activity_state": activity_state,
        "fresh": bool(freshness.get("fresh")),
        "stale": bool(freshness.get("stale")),
        "freshness_reason": freshness.get("reason"),
        "stale_threshold_ms": stale_ms,
        "map_loaded": bool(map_snapshot["loaded"]),
        "map_source": map_snapshot.get("source"),
        "map_error": map_snapshot.get("error"),
        "object_stream_available": bool(map_snapshot["stream_available"]),
        "object_count": int(map_snapshot["object_count"]),
        "first_object_time": map_snapshot.get("first_time"),
        "last_object_time": map_snapshot.get("last_time"),
        "playfield_calibrated": bool(calibration["calibrated"]),
        "playfield_rect": calibration.get("rect"),
        "calibration_error": calibration.get("error"),
        "window_focus_readiness": focus.get("focus_readiness") or "unavailable_no_helper",
        "focus_readiness": focus.get("focus_readiness") or "unavailable_no_helper",
        "window_title": focus.get("window_title"),
        "window_class": focus.get("window_class"),
        "process_name": focus.get("process_name"),
        "is_osu_window": bool(focus.get("is_osu_window")),
        "focus_reason": focus.get("reason"),
        "abort_release_readiness": {
            "emergency_abort_available": abort.get("emergency_abort_available") or "unavailable_no_helper",
            "emergency_release_available": release.get("emergency_release_available") or "unavailable_no_helper",
        },
        "emergency_abort_available": abort.get("emergency_abort_available") or "unavailable_no_helper",
        "abort_strategy": abort.get("abort_strategy") or "preview_only",
        "abort_command_available": bool(abort.get("abort_command_available")),
        "emergency_release_available": release.get("emergency_release_available") or "unavailable_no_helper",
        "release_strategy": release.get("release_strategy") or "preview_only",
        "release_command_available": bool(release.get("release_command_available")),
        "readiness": readiness,
        "blockers": blockers,
        "timing_budget": {
            "lead_ms": timing["lead_ms"],
            "limit": timing["limit"],
            "preview_count": timing["count"],
            "immediate_window_count": timing["immediate_window_count"],
            "upcoming_window_count": timing["upcoming_window_count"],
            "first_time": timing["first_time"],
            "last_time": timing["last_time"],
            "preview_cost_ms": timing["preview_cost_ms"],
            "frame_ms_reference": 24,
            "reference_source": "external_osu_ai_bot_readme",
        },
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
    }


def build_input_lease_preview(
    text: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    status = build_input_readiness_status(text, **kwargs)
    blockers = list(status["blockers"])

    preview_ready = (
        status["adapter_zone_enabled"]
        and status["bridge_ok"]
        and status["map_loaded"]
        and status["object_stream_available"]
        and status["playfield_calibrated"]
    )
    benchmark_ready = preview_ready and status["activity_state"] == "gameplay" and status["current_time_ms"] is not None

    if benchmark_ready:
        recommended_lease = "ready_for_benchmark_no_input"
        decision = "input_lease_preview_ready_for_benchmark_no_input"
        readiness = "ready"
    elif preview_ready:
        recommended_lease = "ready_aim_preview_only"
        decision = "input_lease_preview_ready_aim_preview_only"
        readiness = "partial_ready"
    else:
        recommended_lease = "not_ready"
        decision = "input_lease_preview_not_ready"
        readiness = "not_ready"

    return {
        "decision": decision,
        "readiness": readiness,
        "recommended_lease": recommended_lease,
        "blockers": blockers,
        "status": status,
        "timing_budget": status["timing_budget"],
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


def _format_rect(rect: Any) -> str:
    if not isinstance(rect, PlayfieldRect):
        return "None"
    return f"{int(rect.left)},{int(rect.top)},{int(rect.width)},{int(rect.height)}"


def print_input_readiness_status(status: dict[str, Any]) -> None:
    print("osu! Input Readiness Status")
    print(f"  Decision: {status['decision']}")
    print(f"  Adapter: {status['adapter']}")
    print(f"  Adapter zone enabled: {status['adapter_zone_enabled']}")
    print(f"  Tosu bridge ok: {status['bridge_ok']}")
    print(f"  Bridge source: {status['bridge_source']}")
    print(f"  Bridge base URL: {status['bridge_base_url']}")
    print(f"  Bridge errors: {status['bridge_errors']}")
    print(f"  Current time ms: {status['current_time_ms']}")
    print(f"  Activity state: {status['activity_state']}")
    print(f"  Fresh: {status['fresh']}")
    print(f"  Stale: {status['stale']}")
    print(f"  Freshness reason: {status['freshness_reason']}")
    print(f"  Stale threshold ms: {status['stale_threshold_ms']}")
    print(f"  Map loaded: {status['map_loaded']}")
    print(f"  Map source: {status['map_source']}")
    print(f"  Map error: {status['map_error']}")
    print(f"  Object stream available: {status['object_stream_available']}")
    print(f"  Object count: {status['object_count']}")
    print(f"  First object time: {status['first_object_time']}")
    print(f"  Last object time: {status['last_object_time']}")
    print(f"  Playfield calibrated: {status['playfield_calibrated']}")
    print(f"  Playfield rect: {_format_rect(status['playfield_rect'])}")
    print(f"  Calibration error: {status['calibration_error']}")
    print(f"  Window/focus readiness: {status['window_focus_readiness']}")
    print(f"  Window title: {status.get('window_title')}")
    print(f"  Window class: {status.get('window_class')}")
    print(f"  Process name: {status.get('process_name')}")
    print(f"  Is osu window: {status.get('is_osu_window')}")
    print(f"  Focus reason: {status.get('focus_reason')}")
    print(f"  Emergency abort available: {status.get('emergency_abort_available')}")
    print(f"  Abort strategy: {status.get('abort_strategy')}")
    print(f"  Emergency release available: {status.get('emergency_release_available')}")
    print(f"  Release strategy: {status.get('release_strategy')}")
    print(f"  Abort/release readiness: {status['abort_release_readiness']}")
    print(f"  Readiness: {status['readiness']}")
    print(f"  Blockers: {status['blockers']}")
    timing = status["timing_budget"]
    print(f"  Timing lead ms: {timing['lead_ms']}")
    print(f"  Timing limit: {timing['limit']}")
    print(f"  Timing preview count: {timing['preview_count']}")
    print(f"  Timing immediate window count: {timing['immediate_window_count']}")
    print(f"  Timing upcoming window count: {timing['upcoming_window_count']}")
    print(f"  Timing frame ms reference: {timing['frame_ms_reference']}")
    print(f"  Real input allowed: {status['real_input_allowed']}")
    print(f"  Submit allowed: {status['submit_allowed']}")
    print(f"  Real input: {status['real_input']}")
    print(f"  Submit: {status['submit']}")
    print(f"  VTS call: {status['vts_call']}")
    print(f"  Voice call: {status['voice_call']}")
    print(f"  OBS call: {status['obs_call']}")
    print(f"  Vision call: {status['vision_call']}")


def print_input_lease_preview(preview: dict[str, Any]) -> None:
    status = preview["status"]
    timing = preview["timing_budget"]
    print("osu! Input Lease Preview")
    print(f"  Decision: {preview['decision']}")
    print(f"  Readiness: {preview['readiness']}")
    print(f"  Recommended lease: {preview['recommended_lease']}")
    print(f"  Blockers: {preview['blockers']}")
    print(f"  Adapter zone enabled: {status['adapter_zone_enabled']}")
    print(f"  Tosu bridge ok: {status['bridge_ok']}")
    print(f"  Activity state: {status['activity_state']}")
    print(f"  Map loaded: {status['map_loaded']}")
    print(f"  Object stream available: {status['object_stream_available']}")
    print(f"  Playfield calibrated: {status['playfield_calibrated']}")
    print(f"  Current time ms: {status['current_time_ms']}")
    print(f"  Timing lead ms: {timing['lead_ms']}")
    print(f"  Timing limit: {timing['limit']}")
    print(f"  Timing preview count: {timing['preview_count']}")
    print(f"  Timing immediate window count: {timing['immediate_window_count']}")
    print(f"  Timing upcoming window count: {timing['upcoming_window_count']}")
    print(f"  Timing frame ms reference: {timing['frame_ms_reference']}")
    print(f"  Real input allowed: {preview['real_input_allowed']}")
    print(f"  Submit allowed: {preview['submit_allowed']}")
    print(f"  Real input: {preview['real_input']}")
    print(f"  Submit: {preview['submit']}")
    print(f"  VTS call: {preview['vts_call']}")
    print(f"  Voice call: {preview['voice_call']}")
    print(f"  OBS call: {preview['obs_call']}")
    print(f"  Vision call: {preview['vision_call']}")
