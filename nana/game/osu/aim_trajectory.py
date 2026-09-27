"""
osu! aim trajectory / replay path preview - Phase 24.

Builds local trajectory previews from map objects, timing, and calibration.

No live input. No cursor move/click. No SetCursorPos. No SendInput.
No mouse_event. No score submit. No VTS. No ElevenLabs. No OBS API.
No Vision. No Stardew changes. No external bot import/run.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any, Callable

from nana.game.osu.beatmap_parser import parse_osu_text
from nana.game.osu.bridge import read_current_map_text, read_tosu_state
from nana.game.osu.calibration import PlayfieldRect, parse_playfield_rect
from nana.game.osu.input_benchmark import build_input_benchmark_preview
from nana.game.osu.input_readiness import (
    DEFAULT_LEASE_LIMIT,
    build_input_readiness_status,
)
from nana.game.osu.router import DEFAULT_AIM_LEAD_MS, aim_timeline_telemetry


DEFAULT_AIM_TRAJECTORY_PATH = Path(__file__).resolve().parent / "data" / "aim_trajectory.json"
DEFAULT_SAMPLE_STEP_MS = 16
SCHEMA_VERSION = "nana.osu.aim_trajectory.v1"

ReadinessBuilder = Callable[..., dict[str, Any]]
BenchmarkBuilder = Callable[..., dict[str, Any]]


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_trajectory_flags(text: str) -> dict[str, Any]:
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


def _read_map_text(state: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    try:
        return read_current_map_text(state)
    except Exception as exc:
        return None, None, f"current_map_read_error:{type(exc).__name__}"


def _read_playfield_rect() -> tuple[PlayfieldRect | None, str | None]:
    try:
        return parse_playfield_rect(), None
    except ValueError as exc:
        return None, str(exc)


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


def _action_hint(kind: str | None) -> str:
    if kind == "circle":
        return "tap_candidate"
    if kind == "slider":
        return "slider_follow_candidate"
    if kind == "spinner":
        return "spinner_candidate"
    return "move_only"


def _trajectory_points(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for obj in timeline.get("objects") or []:
        screen = obj.get("screen") or None
        segment_distance = obj.get("from_prev_px")
        segment_speed = obj.get("aim_speed_px_s")
        segment_duration = None
        if segment_distance is not None and segment_speed not in (None, 0):
            try:
                segment_duration = round((float(segment_distance) / float(segment_speed)) * 1000.0, 3)
            except (TypeError, ValueError, ZeroDivisionError):
                segment_duration = None
        point = {
            "object_index": obj.get("index"),
            "kind": obj.get("kind") or "unknown",
            "hit_t": obj.get("hit_time"),
            "aim_t": obj.get("aim_time"),
            "dt": obj.get("dt"),
            "playfield": {
                "x": obj.get("x"),
                "y": obj.get("y"),
            },
            "screen": _screen_payload(screen),
            "segment_distance_px": segment_distance,
            "segment_duration_ms": segment_duration,
            "segment_speed_px_s": segment_speed,
            "action_hint": _action_hint(obj.get("kind")),
        }
        points.append(point)
    return points


def _preview_ready(status: dict[str, Any]) -> bool:
    return (
        bool(status.get("adapter_zone_enabled"))
        and bool(status.get("bridge_ok"))
        and bool(status.get("map_loaded"))
        and bool(status.get("object_stream_available"))
    )


def _input_ready(status: dict[str, Any], benchmark: dict[str, Any]) -> bool:
    return (
        status.get("readiness") == "ready"
        and benchmark.get("recommended_benchmark") == "ready_for_future_input_benchmark"
    )


def _minimal_benchmark_status(
    text: str,
    *,
    builder: BenchmarkBuilder,
    readiness_kwargs: dict[str, Any],
) -> dict[str, Any]:
    try:
        return builder(text, sleep_fn=lambda seconds: None, **readiness_kwargs)
    except Exception as exc:
        return {
            "decision": "input_benchmark_preview_not_ready_hold_no_input",
            "readiness": "not_ready",
            "recommended_benchmark": "not_ready",
            "blockers": [f"trajectory_benchmark_error:{type(exc).__name__}"],
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "vision_call": False,
        }


def build_aim_trajectory_payload(
    text: str = "",
    *,
    bridge_state: dict[str, Any] | None = None,
    map_text: str | None = None,
    map_source: str | None = None,
    playfield_rect: PlayfieldRect | None | str = None,
    readiness_builder: ReadinessBuilder | None = None,
    benchmark_builder: BenchmarkBuilder | None = None,
) -> dict[str, Any]:
    """
    Build a local no-input trajectory payload.

    Optional args are for smoke/mock tests. Command path reads only tosu/map and
    calibration data, then emits preview/JSON/write output.
    """
    flags = _parse_trajectory_flags(text)
    lead_ms = int(flags["lead_ms"])
    limit = int(flags["limit"])
    sample_step_ms = int(flags["sample_step_ms"])
    readiness_builder = readiness_builder or build_input_readiness_status
    benchmark_builder = benchmark_builder or build_input_benchmark_preview

    state = bridge_state if bridge_state is not None else _read_bridge_state()
    if map_text is None:
        map_text, map_source, map_error = _read_map_text(state)
    else:
        map_error = None
    parsed = parse_osu_text(map_text or "", source=map_source) if map_text else None

    if isinstance(playfield_rect, PlayfieldRect):
        rect = playfield_rect
        calibration_error = None
    elif playfield_rect == "missing":
        rect = None
        calibration_error = "playfield_rect_missing"
    else:
        rect, calibration_error = _read_playfield_rect()

    readiness_kwargs = {
        "bridge_state": state,
        "map_text": map_text or "",
        "map_source": map_source,
        "playfield_rect": rect if rect is not None else "missing",
    }
    try:
        readiness = readiness_builder(text, **readiness_kwargs)
    except Exception as exc:
        readiness = {
            "decision": "input_readiness_status_only_no_input",
            "readiness": "not_ready",
            "activity_state": "unknown",
            "adapter_zone_enabled": False,
            "bridge_ok": bool(state.get("ok")),
            "map_loaded": bool(parsed),
            "object_stream_available": False,
            "playfield_calibrated": rect is not None,
            "blockers": [f"trajectory_readiness_error:{type(exc).__name__}"],
            "current_time_ms": state.get("current_time_ms"),
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "vision_call": False,
        }
    benchmark = _minimal_benchmark_status(
        text,
        builder=benchmark_builder,
        readiness_kwargs=readiness_kwargs,
    )

    timeline = {
        "decision": "telemetry_only_no_input",
        "current_time_ms": state.get("current_time_ms"),
        "lead_ms": lead_ms,
        "limit": limit,
        "count": 0,
        "calibrated": rect is not None,
        "immediate_window_count": 0,
        "upcoming_window_count": 0,
        "objects": [],
        "real_input": False,
        "submit": False,
    }
    timeline_error = None
    if parsed:
        try:
            timeline = aim_timeline_telemetry(
                parsed,
                current_time_ms=state.get("current_time_ms"),
                lead_ms=lead_ms,
                limit=limit,
                playfield_rect=rect,
            )
        except Exception as exc:
            timeline_error = f"trajectory_timeline_error:{type(exc).__name__}"
    elif not map_error:
        map_error = "current_map_unavailable"

    points = _trajectory_points(timeline)
    input_ready = _input_ready(readiness, benchmark)
    preview_allowed = parsed is not None and _preview_ready(readiness)
    blockers = list(readiness.get("blockers") or [])
    if map_error and "map_not_loaded" not in blockers:
        blockers.append("map_not_loaded")
    if timeline_error and timeline_error not in blockers:
        blockers.append(timeline_error)

    if input_ready and points:
        decision = "trajectory_ready_preview_only"
    elif preview_allowed and points:
        decision = "trajectory_preview_only_not_input_ready"
    elif parsed and points:
        decision = "trajectory_preview_only_limited"
    else:
        decision = "trajectory_hold_no_points"

    return {
        "schema": SCHEMA_VERSION,
        "version": 1,
        "decision": decision,
        "activity_state": readiness.get("activity_state") or "unknown",
        "calibrated": bool(rect is not None),
        "calibration_error": calibration_error,
        "playfield_rect": _rect_payload(rect),
        "readiness": readiness.get("readiness") or "not_ready",
        "readiness_decision": readiness.get("decision"),
        "readiness_blockers": list(readiness.get("blockers") or []),
        "benchmark_recommendation": benchmark.get("recommended_benchmark") or "not_ready",
        "benchmark_decision": benchmark.get("decision"),
        "benchmark_readiness": benchmark.get("readiness") or "not_ready",
        "input_ready": bool(input_ready),
        "lead_ms": lead_ms,
        "limit": limit,
        "sample_step_ms": sample_step_ms,
        "current_time_ms": state.get("current_time_ms"),
        "map_loaded": bool(parsed),
        "map_source": map_source,
        "map_error": map_error,
        "objects_count": int(timeline.get("count") or 0),
        "points_count": len(points),
        "timeline_windows": {
            "immediate_0_500_ms": int(timeline.get("immediate_window_count") or 0),
            "upcoming_500_1500_ms": int(timeline.get("upcoming_window_count") or 0),
        },
        "trajectory_points": points,
        "points": points,
        "blockers": blockers,
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


def resolve_aim_trajectory_path(raw_path: str | None) -> Path:
    if raw_path:
        return Path(raw_path)
    return DEFAULT_AIM_TRAJECTORY_PATH


def write_aim_trajectory_payload(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    output = dict(payload)
    output["written_at"] = time.time()
    output["output_path"] = str(path)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
    return {
        "decision": "trajectory_file_write_no_input",
        "path": str(path),
        "bytes_written": path.stat().st_size,
        "payload": output,
        "file_write": True,
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
    }


def build_aim_trajectory_write(
    text: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    flags = _parse_trajectory_flags(text)
    payload = build_aim_trajectory_payload(text, **kwargs)
    output_path = resolve_aim_trajectory_path(flags.get("path"))
    return write_aim_trajectory_payload(payload, output_path)


def print_aim_trajectory_preview(payload: dict[str, Any], *, max_points: int = 5) -> None:
    print("osu! Aim Trajectory Preview")
    print(f"  Decision: {payload['decision']}")
    print(f"  Activity state: {payload['activity_state']}")
    print(f"  Readiness: {payload['readiness']}")
    print(f"  Benchmark recommendation: {payload['benchmark_recommendation']}")
    print(f"  Input ready: {payload['input_ready']}")
    print(f"  Calibrated: {payload['calibrated']}")
    print(f"  Objects count: {payload['objects_count']}")
    print(f"  Points count: {payload['points_count']}")
    print(f"  Lead ms: {payload['lead_ms']}")
    print(f"  Limit: {payload['limit']}")
    print(f"  Sample step ms: {payload['sample_step_ms']}")
    print(f"  Blockers: {payload['blockers']}")
    for point in payload["trajectory_points"][:max_points]:
        screen = point.get("screen")
        screen_text = "null" if screen is None else f"{screen.get('x')},{screen.get('y')}"
        print(
            "  Point: "
            f"#{point.get('object_index')} {point.get('kind')} "
            f"hit_t={point.get('hit_t')} aim_t={point.get('aim_t')} dt={point.get('dt')} "
            f"pf=({point['playfield'].get('x')},{point['playfield'].get('y')}) "
            f"screen={screen_text} action={point.get('action_hint')}"
        )
    print(f"  Real input allowed: {payload['real_input_allowed']}")
    print(f"  Submit allowed: {payload['submit_allowed']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
    print(f"  VTS call: {payload['vts_call']}")
    print(f"  Voice call: {payload['voice_call']}")
    print(f"  OBS call: {payload['obs_call']}")
    print(f"  Vision call: {payload['vision_call']}")


def print_aim_trajectory_write(result: dict[str, Any]) -> None:
    payload = result["payload"]
    print("osu! Aim Trajectory Write")
    print(f"  Decision: {result['decision']}")
    print(f"  Payload decision: {payload['decision']}")
    print(f"  Path: {result['path']}")
    print(f"  Bytes written: {result['bytes_written']}")
    print(f"  Calibrated: {payload['calibrated']}")
    print(f"  Points count: {payload['points_count']}")
    print(f"  File write: {result['file_write']}")
    print(f"  Real input allowed: {result['real_input_allowed']}")
    print(f"  Submit allowed: {result['submit_allowed']}")
    print(f"  Real input: {result['real_input']}")
    print(f"  Submit: {result['submit']}")
    print(f"  VTS call: {result['vts_call']}")
    print(f"  Voice call: {result['voice_call']}")
    print(f"  OBS call: {result['obs_call']}")
    print(f"  Vision call: {result['vision_call']}")
