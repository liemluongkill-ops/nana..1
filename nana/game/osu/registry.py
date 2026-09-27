from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from nana.game.osu.aim_status import DEFAULT_STALE_MS, read_aim_status
from nana.game.osu.beatmap_parser import parse_osu_text
from nana.game.osu.bridge import OSU_STATE_PATH_DEFAULT, read_current_map_text, read_tosu_state, write_state
from nana.game.osu.calibration import (
    OSU_PLAYFIELD_RECT_ENV,
    calibration_rect_from_corners,
    parse_playfield_rect,
    read_mouse_position,
)
from nana.game.osu.reaction import read_vts_reaction_preview, send_vts_reaction
from nana.game.osu.router import (
    DEFAULT_AIM_LEAD_MS,
    aim_plan_preview,
    aim_timeline_json_payload,
    aim_timeline_telemetry,
    preview_next_objects,
    preview_summary,
)
from nana.game.osu.stream_persona import build_stream_persona_preview
from nana.game.osu.result_source import (
    build_result_source_status_output,
    merge_result_sources,
    parse_manual_result_flags,
    read_result_source_snapshot,
)
from nana.game.osu.result_stage import (
    DEFAULT_STALE_MS as DEFAULT_RESULT_STAGE_STALE_MS,
    build_quick_stage_fields,
    clear_staged_result,
    get_staged_result_snapshot,
    parse_stage_flags,
    print_quick_stage_result,
    print_stage_result_clear,
    print_stage_result_status,
    print_stage_result_write,
    read_staged_result,
    write_staged_result,
)
from nana.game.osu.input_readiness import (
    build_input_lease_preview,
    build_input_readiness_status,
    print_input_lease_preview,
    print_input_readiness_status,
)
from nana.game.osu.input_benchmark import (
    build_input_benchmark_preview,
    build_input_benchmark_status,
    print_input_benchmark_preview,
    print_input_benchmark_status,
)
from nana.game.osu.input_safety import (
    build_emergency_release_dry_run,
    build_emergency_release_status,
    build_input_safety_status,
    print_emergency_abort_preview,
    print_emergency_release_dry_run,
    print_emergency_release_preview,
    print_emergency_release_result,
    print_emergency_release_status,
    print_focus_status,
    print_input_safety_status,
    read_emergency_abort_readiness,
    read_emergency_release_readiness,
    read_osu_focus_readiness,
    run_emergency_release,
)
from nana.game.osu.aim_trajectory import (
    build_aim_trajectory_payload,
    build_aim_trajectory_write,
    print_aim_trajectory_preview,
    print_aim_trajectory_write,
)
from nana.game.osu.executor_dry_run import (
    build_executor_contract_preview,
    build_executor_dry_run_schedule,
    build_executor_dry_run_write,
    print_executor_contract_preview,
    print_executor_dry_run_schedule,
    print_executor_dry_run_write,
)
from nana.game.osu.executor_preflight import (
    REAL_INPUT_EXECUTOR_ENV,
    build_executor_preflight_payload,
    print_executor_preflight_status,
)
from nana.game.osu.executor_arm_preview import (
    build_executor_arm_preview_payload,
    build_executor_arm_status_payload,
    print_executor_arm_preview,
)
from nana.game.osu.executor_armed_lease import (
    build_executor_armed_lease_clear_payload,
    build_executor_armed_lease_preview_payload,
    build_executor_armed_lease_status_payload,
    print_executor_armed_lease,
)
from nana.game.osu.executor_simulated_tick import (
    build_executor_simulated_tick_payload,
    build_executor_simulated_tick_status_payload,
    print_executor_simulated_tick,
)
from nana.game.osu.executor_sim_tick_loop import (
    build_executor_sim_tick_loop_payload,
    build_executor_sim_tick_loop_status_payload,
    print_executor_sim_tick_loop,
)
from nana.game.osu.executor_action_packet_preview import (
    build_executor_action_packet_preview_payload,
    build_executor_action_packet_status_payload,
    print_executor_action_packet_preview,
)
from nana.game.osu.executor_dispatch_preflight import (
    build_executor_dispatch_preflight_payload,
    build_executor_dispatch_preflight_status_payload,
    print_executor_dispatch_preflight,
)
from nana.game.osu.executor_shadow_dispatch_session import (
    build_executor_shadow_dispatch_session_payload,
    build_executor_shadow_dispatch_session_status_payload,
    print_executor_shadow_dispatch_session,
)
from nana.game.osu.pre_input_commit_barrier import (
    build_pre_input_commit_barrier_payload,
    build_pre_input_commit_error_payload,
    build_pre_input_commit_json_payload,
    build_pre_input_commit_status_payload,
    print_pre_input_commit_barrier,
)
from nana.game.osu.play_plan_cache import (
    build_play_plan_payload,
    build_play_plan_status_payload,
    build_runtime_tick_payload,
    print_play_plan_payload,
    print_runtime_tick_payload,
)
from nana.game.osu.runtime_packet import (
    build_runtime_packet_error_payload,
    build_runtime_packet_payload,
    build_runtime_packet_status_payload,
    print_runtime_packet_payload,
)
from nana.game.osu.runtime_packet_loop import (
    build_runtime_packet_loop_error_payload,
    build_runtime_packet_loop_payload,
    build_runtime_packet_loop_status_payload,
    print_runtime_packet_loop_payload,
)
from nana.game.osu.runtime_controller_preview import (
    build_runtime_controller_error_payload,
    build_runtime_controller_preview_payload,
    build_runtime_controller_status_payload,
    print_runtime_controller_payload,
)
from nana.game.osu.runtime_executor_intent import (
    build_runtime_executor_intent_error_payload,
    build_runtime_executor_intent_payload,
    build_runtime_executor_intent_status_payload,
    print_runtime_executor_intent_payload,
)
from nana.game.osu.runtime_executor_backend_dry import (
    build_runtime_executor_backend_dry_error_payload,
    build_runtime_executor_backend_dry_payload,
    build_runtime_executor_backend_dry_status_payload,
    print_runtime_executor_backend_dry_payload,
)
from nana.game.osu.runtime_executor_backend_gate import (
    build_runtime_executor_backend_gate_error_payload,
    build_runtime_executor_backend_gate_payload,
    build_runtime_executor_backend_gate_status_payload,
    print_runtime_executor_backend_gate_payload,
)
from nana.game.osu.vendor_bot_runtime import (
    build_kill_switch_status_payload,
    build_vendor_boss_auto_start_payload,
    build_vendor_boss_auto_status_payload,
    build_vendor_boss_auto_stop_payload,
    build_vendor_boss_kill_all_payload,
    build_vendor_boss_kill_payload,
    build_vendor_boss_lifecycle_payload,
    build_vendor_model_heuristic_payload,
    build_vendor_model_install_payload,
    build_vendor_bot_start_payload,
    build_vendor_bot_status_payload,
    build_vendor_bot_stop_payload,
    print_vendor_bot_payload,
)
from nana.game.osu.cursor_dance_runtime import (
    build_cursor_dance_kill_payload,
    build_cursor_dance_start_payload,
    build_cursor_dance_status_payload,
    build_cursor_dance_stop_payload,
    print_cursor_dance_payload,
)
from nana.runtime.stream_event_core import ingest_stream_event_preview
from nana.runtime.stream_public_output import (
    build_public_subtitle_payload,
    clear_public_subtitle_file,
    read_public_subtitle_status,
    write_public_subtitle_file,
)
from nana.runtime.stream_response_preview import build_stream_response_preview
from nana.runtime.stream_result_subtitle_pipeline import (
    build_result_subtitle_pipeline_preview,
    build_result_subtitle_pipeline_write,
)
from nana.runtime.stream_presence_readiness import (
    build_stream_presence_readiness_preview,
    print_stream_presence_readiness_preview,
)

OSU_COMMANDS = {
    "/osu-status",
    "/osu-ready",
    "/osu-adapter-status",
    "/osu-bridge-status",
    "/osu-map-status",
    "/osu-load-current-map",
    "/osu-next-objects",
    "/osu-router-preview",
    "/osu-play-dry-run",
    "/osu-calibration-status",
    "/osu-coordinate-preview",
    "/osu-mouse-position",
    "/osu-calibration-guide",
    "/osu-calibration-rect",
    "/osu-aim-plan-preview",
    "/osu-aim-timeline",
    "/osu-aim-timeline-json",
    "/osu-aim-timeline-write",
    "/osu-aim-trajectory-preview",
    "/osu-aim-trajectory-json",
    "/osu-aim-trajectory-write",
    "/osu-executor-contract-preview",
    "/osu-executor-dry-run-schedule",
    "/osu-executor-dry-run-json",
    "/osu-executor-dry-run-write",
    "/osu-executor-preflight-status",
    "/osu-executor-preflight-preview",
    "/osu-executor-preflight-json",
    "/osu-executor-arm-status",
    "/osu-executor-arm-preview",
    "/osu-executor-arm-json",
    "/osu-executor-armed-lease-preview",
    "/osu-executor-armed-lease-json",
    "/osu-executor-armed-lease-status",
    "/osu-executor-armed-lease-clear",
    "/osu-executor-sim-tick-preview",
    "/osu-executor-sim-tick-json",
    "/osu-executor-sim-tick-status",
    "/osu-executor-sim-tick-loop-preview",
    "/osu-executor-sim-tick-loop-json",
    "/osu-executor-sim-tick-loop-status",
    "/osu-executor-action-packet-preview",
    "/osu-executor-action-packet-json",
    "/osu-executor-action-packet-status",
    "/osu-executor-dispatch-preflight-preview",
    "/osu-executor-dispatch-preflight-json",
    "/osu-executor-dispatch-preflight-status",
    "/osu-executor-shadow-dispatch-session-preview",
    "/osu-executor-shadow-dispatch-session-json",
    "/osu-executor-shadow-dispatch-session-status",
    "/osu-pre-input-commit-preview",
    "/osu-pre-input-commit-json",
    "/osu-pre-input-commit-status",
    "/osu-play-plan-preview",
    "/osu-play-plan-json",
    "/osu-play-plan-status",
    "/osu-runtime-tick-preview",
    "/osu-runtime-tick-json",
    "/osu-runtime-packet-preview",
    "/osu-runtime-packet-json",
    "/osu-runtime-packet-status",
    "/osu-runtime-packet-loop-preview",
    "/osu-runtime-packet-loop-json",
    "/osu-runtime-packet-loop-status",
    "/osu-runtime-controller-preview",
    "/osu-runtime-controller-json",
    "/osu-runtime-controller-status",
    "/osu-runtime-intent-preview",
    "/osu-runtime-intent-json",
    "/osu-runtime-intent-status",
    "/osu-runtime-backend-dry-preview",
    "/osu-runtime-backend-dry-json",
    "/osu-runtime-backend-dry-status",
    "/osu-runtime-backend-gate-preview",
    "/osu-runtime-backend-gate-json",
    "/osu-runtime-backend-gate-status",
    "/osu-vendor-bot-status",
    "/osu-vendor-bot-start",
    "/osu-vendor-bot-stop",
    "/osu-vendor-bot-json",
    "/osu-vendor-boss-lifecycle",
    "/osu-vendor-boss-auto-start",
    "/osu-vendor-boss-auto-stop",
    "/osu-vendor-boss-auto-status",
    "/osu-vendor-boss-kill",
    "/osu-vendor-boss-kill-all",
    "/osu-kill-switch",
    "/osu-kill-switch-status",
    "/osu-vendor-model-heuristic",
    "/osu-vendor-model-install",
    "/osu-cursor-dance-status",
    "/osu-cursor-dance-start",
    "/osu-cursor-dance-stop",
    "/osu-cursor-dance-kill",
    "/osu-stream-presence-readiness",
    "/osu-stream-presence-readiness-json",
    "/osu-aim-status",
    "/osu-vts-reaction-preview",
    "/osu-vts-reaction-send",
    "/osu-stream-persona-preview",
    "/osu-core-event-preview",
    "/osu-core-result-preview",
    "/osu-companion-response-preview",
    "/osu-public-subtitle-preview",
    "/osu-public-subtitle-write",
    "/osu-public-subtitle-status",
    "/osu-public-subtitle-obs-guide",
    "/osu-public-subtitle-clear",
    "/osu-result-subtitle-pipeline-preview",
    "/osu-result-subtitle-pipeline-write",
    "/osu-result-source-status",
    "/osu-result-stage",
    "/osu-result-stage-status",
    "/osu-result-stage-clear",
    "/osu-result-subtitle-auto-preview",
    "/osu-result-subtitle-auto-write",
    # Phase 20: Quick stage shortcuts
    "/osu-result-stage-quick",
    "/osu-result-stage-strong",
    "/osu-result-stage-weak",
    "/osu-result-stage-fail",
    # Phase 21: Result/subtitle lifecycle helpers
    "/osu-result-lifecycle-preview",
    "/osu-result-lifecycle-write",
    "/osu-result-lifecycle-clear",
    "/osu-result-lifecycle-status",
    # Phase 22: Input readiness / lease preview only
    "/osu-input-readiness-status",
    "/osu-input-lease-preview",
    # Phase 23: Input benchmark / latency preview only
    "/osu-input-benchmark-preview",
    "/osu-input-benchmark-status",
    # Phase 28: Input safety readiness preview only
    "/osu-input-safety-status",
    "/osu-input-focus-status",
    "/osu-emergency-release-preview",
    "/osu-emergency-abort-preview",
    # Phase 29: gated emergency key-up release only
    "/osu-emergency-release-status",
    "/osu-emergency-release-dry-run",
    "/osu-emergency-release",
}

DEFAULT_AIM_TIMELINE_PATH = Path(__file__).resolve().parent / "data" / "aim_timeline.json"
DEFAULT_OSU_STATE_METADATA_PATH = OSU_STATE_PATH_DEFAULT


def register_commands(command_set, *, ctx=None) -> set[str]:
    command_set.update(OSU_COMMANDS)
    return set(OSU_COMMANDS)


def _ctx_state(ctx=None) -> dict[str, Any]:
    if isinstance(ctx, dict):
        state = ctx.get("context_state")
        if isinstance(state, dict):
            return state
    return {}


def _parse_calibration_rect_flags(text: str) -> dict[str, float]:
    values = {}
    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        if name in {"left", "top", "right", "bottom"}:
            values[name] = float(raw_value)
    missing = [name for name in ("left", "top", "right", "bottom") if name not in values]
    if missing:
        raise ValueError(f"missing_flags:{','.join(missing)}")
    return values


def _parse_aim_plan_flags(text: str) -> dict[str, int | str]:
    values: dict[str, int | str] = {
        "lead_ms": DEFAULT_AIM_LEAD_MS,
        "limit": 15,
    }
    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        if name == "lead-ms":
            values["lead_ms"] = int(raw_value)
        elif name == "limit":
            values["limit"] = int(raw_value)
        elif name == "path":
            values["path"] = raw_value
    if int(values["limit"]) <= 0:
        raise ValueError("limit_must_be_positive")
    return values


def _resolve_aim_timeline_path(raw_path: str | None) -> Path:
    if raw_path:
        return Path(raw_path)
    return DEFAULT_AIM_TIMELINE_PATH


def _resolve_osu_state_metadata_path(raw_path: str | None = None) -> Path:
    if raw_path:
        return Path(raw_path)
    return DEFAULT_OSU_STATE_METADATA_PATH


def _parse_aim_status_flags(text: str) -> dict[str, int | str]:
    values: dict[str, int | str] = {
        "stale_ms": DEFAULT_STALE_MS,
    }
    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        if name == "path":
            values["path"] = raw_value
        elif name == "metadata-path":
            values["metadata_path"] = raw_value
        elif name == "stale-ms":
            values["stale_ms"] = int(raw_value)
        elif name == "operator-approval-token":
            values["operator_approval_token"] = raw_value
    if int(values["stale_ms"]) <= 0:
        raise ValueError("stale_ms_must_be_positive")
    return values


# ---------------------------------------------------------------------------
# Result event parser
# ---------------------------------------------------------------------------

_RESULT_BOOL_TRUE = {"true", "1", "yes", "on"}


def _parse_result_flags(text: str) -> dict[str, Any]:
    """Parse result event flags: score, accuracy, misses, combo, max_combo, rank, passed."""
    values: dict[str, Any] = {}
    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        if name == "score":
            values["score"] = int(raw_value)
        elif name == "accuracy":
            values["accuracy"] = float(raw_value)
        elif name == "misses":
            values["misses"] = int(raw_value)
        elif name == "combo":
            values["combo"] = int(raw_value)
        elif name == "max-combo":
            values["max_combo"] = int(raw_value)
        elif name == "rank":
            values["rank"] = raw_value.strip().upper()
        elif name == "passed":
            values["passed"] = raw_value.strip().lower() in _RESULT_BOOL_TRUE
    return values


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
    return path


def _bridge_snapshot(ctx=None) -> dict[str, Any]:
    state = read_tosu_state()
    current_text, source, error = read_current_map_text(state)
    parsed = parse_osu_text(current_text, source=source) if current_text else None
    current_time_ms = state.get("current_time_ms")
    try:
        playfield_rect = parse_playfield_rect()
        calibration_error = None
    except ValueError as exc:
        playfield_rect = None
        calibration_error = str(exc)
    preview = (
        preview_next_objects(
            parsed or {"hit_objects": []},
            current_time_ms=current_time_ms,
            limit=10,
            playfield_rect=playfield_rect,
        )
        if parsed
        else None
    )
    return {
        "adapter": "osu",
        "ready": True,
        "scope": "bridge_preview",
        "input_enabled": False,
        "observer_enabled": False,
        "bridge": state,
        "current_map_text": current_text,
        "current_map_source": source,
        "current_map_error": error,
        "parsed": parsed,
        "current_time_ms": current_time_ms,
        "playfield_rect": playfield_rect,
        "calibration_error": calibration_error,
        "preview": preview,
        "context_state": _ctx_state(ctx),
        "written_state": None,
        "timestamp": time.time(),
    }


def adapter_snapshot(ctx=None) -> dict[str, Any]:
    snapshot = _bridge_snapshot(ctx)
    beatmap = snapshot.get("parsed") or {}
    stats = beatmap.get("stats") or {}
    snapshot.update(
        {
            "map_title": (beatmap.get("metadata") or {}).get("Title"),
            "map_artist": (beatmap.get("metadata") or {}).get("Artist"),
            "map_version": (beatmap.get("metadata") or {}).get("Version"),
            "object_count": stats.get("total", 0),
            "circles": stats.get("circles", 0),
            "sliders": stats.get("sliders", 0),
            "spinners": stats.get("spinners", 0),
            "holds": stats.get("holds", 0),
        }
    )
    return snapshot


def print_osu_status(ctx=None) -> None:
    snapshot = adapter_snapshot(ctx)
    bridge = snapshot["bridge"]
    print("osu! Adapter")
    print(f"  Ready: {snapshot['ready']} scope={snapshot['scope']}")
    print("  Mode: bridge+parser preview only; no real input.")
    print(f"  Bridge: ok={bridge['ok']} source={bridge['source']} base_url={bridge['base_url']}")
    print(f"  Current time: {snapshot['current_time_ms']}")
    print(f"  Beatmap: {snapshot['map_artist']} - {snapshot['map_title']} [{snapshot['map_version']}]")
    print(
        f"  Objects: total={snapshot['object_count']} circles={snapshot['circles']} sliders={snapshot['sliders']} spinners={snapshot['spinners']}"
    )
    if bridge.get("errors"):
        print(f"  Bridge errors: {','.join(bridge['errors'])}")


def print_osu_bridge_status(ctx=None) -> None:
    snapshot = adapter_snapshot(ctx)
    bridge = snapshot["bridge"]
    print("osu! Bridge")
    print(f"  Ok: {bridge['ok']}")
    print(f"  Source: {bridge['source']}")
    print(f"  Base URL: {bridge['base_url']}")
    print(f"  Current time ms: {snapshot['current_time_ms']}")
    print(f"  Current map source: {snapshot['current_map_source']}")
    print(f"  Current map error: {snapshot['current_map_error']}")
    print(f"  Errors: {bridge.get('errors') or []}")


def print_osu_map_status(ctx=None) -> None:
    snapshot = adapter_snapshot(ctx)
    beatmap = snapshot.get("parsed") or {}
    metadata = beatmap.get("metadata") or {}
    difficulty = beatmap.get("difficulty") or {}
    stats = beatmap.get("stats") or {}
    print("osu! Map")
    print(f"  Title: {metadata.get('Title')} | Artist: {metadata.get('Artist')} | Version: {metadata.get('Version')}")
    print(f"  Source: {snapshot['current_map_source']}")
    print(f"  Time: {snapshot['current_time_ms']}")
    print(f"  Objects: {stats.get('total', 0)}")
    print(f"  Difficulty: CS={difficulty.get('CircleSize')} AR={difficulty.get('ApproachRate')} OD={difficulty.get('OverallDifficulty')} HP={difficulty.get('HPDrainRate')}")


def print_osu_load_current_map(ctx=None) -> None:
    snapshot = adapter_snapshot(ctx)
    current_text = snapshot.get("current_map_text")
    source = snapshot.get("current_map_source")
    if not current_text:
        print("osu! Load Current Map")
        print(f"  Status: unavailable | error={snapshot.get('current_map_error')}")
        return
    parsed = snapshot.get("parsed") or parse_osu_text(current_text, source=source)
    state_path = write_state(
        {
            "adapter": "osu",
            "source": source,
            "current_time_ms": snapshot.get("current_time_ms"),
            "beatmap": parsed.get("metadata") if isinstance(parsed, dict) else {},
            "stats": parsed.get("stats") if isinstance(parsed, dict) else {},
        }
    )
    print("osu! Load Current Map")
    print("  Status: loaded")
    print(f"  Source: {source}")
    print(f"  State path: {state_path}")
    print(f"  Title: {(parsed.get('metadata') or {}).get('Title')}")
    print(f"  Objects: {(parsed.get('stats') or {}).get('total', 0)}")


def print_osu_next_objects(ctx=None) -> None:
    snapshot = adapter_snapshot(ctx)
    parsed = snapshot.get("parsed")
    if not parsed:
        print("osu! Next Objects")
        print("  Status: unavailable")
        print(f"  Error: {snapshot.get('current_map_error')}")
        print("  Real input: False")
        print("  Submit: False")
        return
    print("osu! Next Objects")
    print(preview_summary(parsed, current_time_ms=snapshot.get("current_time_ms"), limit=10, playfield_rect=snapshot.get("playfield_rect")))
    print("  Real input: False")
    print("  Submit: False")


def print_osu_router_preview(ctx=None) -> None:
    snapshot = adapter_snapshot(ctx)
    parsed = snapshot.get("parsed")
    if not parsed:
        print("osu! Router Preview")
        print("  Status: unavailable")
        print(f"  Error: {snapshot.get('current_map_error')}")
        print("  Real input: False")
        print("  Submit: False")
        return
    preview = preview_next_objects(
        parsed,
        current_time_ms=snapshot.get("current_time_ms"),
        limit=10,
        playfield_rect=snapshot.get("playfield_rect"),
    )
    print("osu! Router Preview")
    print(f"  Decision: preview_only_no_input")
    print(f"  Count: {preview['count']}")
    print(f"  Calibrated: {preview['calibrated']}")
    print("  Real input: False")
    print("  Submit: False")


def print_osu_play_dry_run(ctx=None) -> None:
    print("osu! Play Dry Run")
    print("  Decision: dry_run_preview_only")
    print("  Real input: False")
    print("  Submit: False")


def print_osu_input_readiness_status(text: str = "") -> None:
    """
    Read-only readiness check for future osu! input work.

    Phase 22 does not dispatch input, move/click, submit, call VTS, call OBS,
    call ElevenLabs, use Vision, or touch Stardew.
    """
    try:
        status = build_input_readiness_status(text)
    except Exception as exc:
        status = {
            "decision": "input_readiness_status_only_no_input",
            "adapter": "osu",
            "adapter_zone_enabled": True,
            "bridge_ok": False,
            "bridge_source": "tosu",
            "bridge_base_url": None,
            "bridge_errors": [f"readiness_error:{type(exc).__name__}"],
            "current_time_ms": None,
            "activity_state": "unknown",
            "fresh": False,
            "stale": False,
            "freshness_reason": "readiness_error",
            "stale_threshold_ms": 3000,
            "map_loaded": False,
            "map_source": None,
            "map_error": str(exc),
            "object_stream_available": False,
            "object_count": 0,
            "first_object_time": None,
            "last_object_time": None,
            "playfield_calibrated": False,
            "playfield_rect": None,
            "calibration_error": "readiness_error",
            "window_focus_readiness": "unavailable_no_helper",
            "abort_release_readiness": "unavailable_no_helper",
            "readiness": "not_ready",
            "blockers": ["readiness_error"],
            "timing_budget": {
                "lead_ms": DEFAULT_AIM_LEAD_MS,
                "limit": 15,
                "preview_count": 0,
                "immediate_window_count": 0,
                "upcoming_window_count": 0,
                "first_time": None,
                "last_time": None,
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
    print_input_readiness_status(status)


def print_osu_input_lease_preview(text: str = "") -> None:
    """
    Read-only lease recommendation for future osu! input work.
    """
    try:
        preview = build_input_lease_preview(text)
    except Exception as exc:
        status = {
            "adapter_zone_enabled": True,
            "bridge_ok": False,
            "activity_state": "unknown",
            "map_loaded": False,
            "object_stream_available": False,
            "playfield_calibrated": False,
            "current_time_ms": None,
        }
        preview = {
            "decision": "input_lease_preview_not_ready",
            "readiness": "not_ready",
            "recommended_lease": "not_ready",
            "blockers": [f"lease_preview_error:{type(exc).__name__}"],
            "status": status,
            "timing_budget": {
                "lead_ms": DEFAULT_AIM_LEAD_MS,
                "limit": 15,
                "preview_count": 0,
                "immediate_window_count": 0,
                "upcoming_window_count": 0,
                "frame_ms_reference": 24,
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
    print_input_lease_preview(preview)


def _input_safety_error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "schema": "nana.osu.input_safety.v1",
        "version": 1,
        "decision": "input_safety_status_only_no_input",
        "focus": {
            "focus_readiness": "error",
            "window_title": None,
            "window_class": None,
            "process_name": None,
            "is_osu_window": False,
            "reason": f"input_safety_error:{type(exc).__name__}",
            "real_input": False,
            "submit": False,
        },
        "release": {
            "emergency_release_available": "error",
            "release_strategy": "preview_only",
            "release_command_available": False,
            "safety_release_only": False,
            "gameplay_input": False,
            "key_down_sent": False,
            "key_up_sent": False,
            "cursor_move": False,
            "click": False,
            "reason": f"input_safety_error:{type(exc).__name__}",
            "real_input": False,
            "submit": False,
        },
        "abort": {
            "emergency_abort_available": "error",
            "abort_strategy": "preview_only",
            "abort_command_available": False,
            "abort_flag_path": None,
            "abort_flag_parent_exists": False,
            "reason": f"input_safety_error:{type(exc).__name__}",
            "real_input": False,
            "submit": False,
        },
        "focus_readiness": "error",
        "window_focus_readiness": "error",
        "window_title": None,
        "window_class": None,
        "process_name": None,
        "is_osu_window": False,
        "focus_reason": f"input_safety_error:{type(exc).__name__}",
        "emergency_release_available": "error",
        "release_strategy": "preview_only",
        "release_command_available": False,
        "release_reason": f"input_safety_error:{type(exc).__name__}",
        "release_keys": (),
        "filtered_extra_keys": (),
        "env_gate_name": "NANA_OSU_EMERGENCY_RELEASE_ENABLED",
        "env_gate_present": False,
        "env_gate_enabled": False,
        "token_present": False,
        "token_valid": False,
        "release_helper_available": False,
        "safety_release_only": True,
        "gameplay_input": False,
        "key_down_sent": False,
        "key_up_sent": False,
        "cursor_move": False,
        "click": False,
        "emergency_abort_available": "error",
        "abort_strategy": "preview_only",
        "abort_command_available": False,
        "abort_flag_path": None,
        "abort_flag_parent_exists": False,
        "abort_reason": f"input_safety_error:{type(exc).__name__}",
        "executable": False,
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


def print_osu_input_safety_status(text: str = "") -> None:
    """
    Read-only focus/window plus emergency release/abort readiness.
    """
    try:
        payload = build_input_safety_status()
    except Exception as exc:
        payload = _input_safety_error_payload(exc)
    print_input_safety_status(payload)


def print_osu_input_focus_status(text: str = "") -> None:
    """
    Read-only foreground window focus readiness. Does not focus/activate/click.
    """
    try:
        payload = read_osu_focus_readiness()
    except Exception as exc:
        payload = _input_safety_error_payload(exc)["focus"]
    print_focus_status(payload)


def print_osu_emergency_release_preview(text: str = "") -> None:
    """
    Preview release readiness only. Does not send key-up in Phase 28.
    """
    try:
        payload = read_emergency_release_readiness()
    except Exception as exc:
        payload = _input_safety_error_payload(exc)["release"]
    print_emergency_release_preview(payload)


def print_osu_emergency_release_status(text: str = "") -> None:
    """
    Status only. Reports gate/env/key list without sending release events.
    """
    try:
        payload = build_emergency_release_status(text)
    except Exception as exc:
        payload = {
            **_input_safety_error_payload(exc),
            "decision": "emergency_release_status_only_no_input",
            "release_helper_available": False,
            "release_keys": (),
            "filtered_extra_keys": (),
        }
    print_emergency_release_status(payload)


def print_osu_emergency_release_dry_run(text: str = "") -> None:
    """
    Dry-run only. Parses env/token and lists keys; does not send key-up events.
    """
    try:
        payload = build_emergency_release_dry_run(text)
    except Exception as exc:
        payload = {
            **_input_safety_error_payload(exc),
            "decision": "emergency_release_dry_run_no_input",
            "would_release_keys": (),
            "blockers": [f"emergency_release_dry_run_error:{type(exc).__name__}"],
        }
    print_emergency_release_dry_run(payload)


def print_osu_emergency_release(text: str = "") -> None:
    """
    Gated emergency release-only command.

    Sends neutral key-up events only when env and operator token are valid.
    """
    try:
        payload = run_emergency_release(text)
    except Exception as exc:
        payload = {
            **_input_safety_error_payload(exc),
            "decision": "emergency_release_hold_no_input",
            "hold_reason": f"emergency_release_error:{type(exc).__name__}",
            "backend": None,
            "sent_count": 0,
        }
    print_emergency_release_result(payload)


def print_osu_emergency_abort_preview(text: str = "") -> None:
    """
    Preview operator abort readiness only. Does not kill processes.
    """
    try:
        payload = read_emergency_abort_readiness()
    except Exception as exc:
        payload = _input_safety_error_payload(exc)["abort"]
    print_emergency_abort_preview(payload)


def print_osu_input_benchmark_preview(text: str = "") -> None:
    """
    Read-only benchmark timing probe for future osu! input work.

    Phase 23 does not dispatch input, move/click, submit, call VTS, call OBS,
    call ElevenLabs, use Vision, touch Stardew, or import/run external bot code.
    """
    try:
        preview = build_input_benchmark_preview(text)
    except Exception as exc:
        preview = {
            "decision": "input_benchmark_preview_not_ready_hold_no_input",
            "readiness": "not_ready",
            "recommended_benchmark": "not_ready",
            "samples_requested": 0,
            "samples_attempted": 0,
            "samples_ok": 0,
            "samples_ready": 0,
            "loop_latency": {"min_ms": None, "median_ms": None, "max_ms": None, "p95_ms": None},
            "preview_cost": {"min_ms": None, "median_ms": None, "max_ms": None, "p95_ms": None},
            "initial_loop_latency_ms": None,
            "initial_preview_cost_ms": None,
            "current_time_first": None,
            "current_time_last": None,
            "current_time_delta": None,
            "timing_lead_ms": DEFAULT_AIM_LEAD_MS,
            "timing_limit": 15,
            "blockers": [f"benchmark_preview_error:{type(exc).__name__}"],
            "sample_errors": [str(exc)],
            "real_input_allowed": False,
            "submit_allowed": False,
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "voice_call": False,
            "obs_call": False,
            "vision_call": False,
        }
    print_input_benchmark_preview(preview)


def print_osu_input_benchmark_status(text: str = "") -> None:
    """
    Read-only status for the last in-process benchmark preview.
    """
    try:
        status = build_input_benchmark_status()
    except Exception as exc:
        status = {
            "decision": "input_benchmark_status_error_no_input",
            "cached": False,
            "message": f"benchmark_status_error:{type(exc).__name__}",
            "last_preview": None,
            "real_input_allowed": False,
            "submit_allowed": False,
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "voice_call": False,
            "obs_call": False,
            "vision_call": False,
        }
    print_input_benchmark_status(status)


def print_osu_calibration_status(ctx=None) -> None:
    try:
        rect = parse_playfield_rect()
        print("osu! Calibration Status")
        print("  Status: calibrated")
        print(f"  Rect: {rect}")
    except ValueError as exc:
        print("osu! Calibration Status")
        print("  Status: uncalibrated")
        print(f"  Reason: {exc}")
    print("  Real input: False")
    print("  Submit: False")


def print_osu_coordinate_preview(ctx=None) -> None:
    snapshot = adapter_snapshot(ctx)
    parsed = snapshot.get("parsed")
    if not parsed:
        print("osu! Coordinate Preview")
        print("  Status: unavailable")
        print(f"  Error: {snapshot.get('current_map_error')}")
        print("  Real input: False")
        print("  Submit: False")
        return
    preview = preview_next_objects(
        parsed,
        current_time_ms=snapshot.get("current_time_ms"),
        limit=5,
        playfield_rect=snapshot.get("playfield_rect"),
    )
    print("osu! Coordinate Preview")
    print(f"  Calibrated: {preview['calibrated']}")
    print(f"  Objects: {preview['count']}")
    print("  Real input: False")
    print("  Submit: False")


def print_osu_mouse_position() -> None:
    x, y = read_mouse_position()
    print("osu! Mouse Position")
    print(f"  Screen: ({x}, {y})")
    print("  Real input: False")
    print("  Submit: False")


def print_osu_calibration_guide() -> None:
    print("osu! Calibration Guide")
    print("  Hover top-left and bottom-right corners, then use /osu-calibration-rect.")
    print("  Real input: False")
    print("  Submit: False")


def print_osu_calibration_rect(text: str = "") -> None:
    try:
        values = _parse_calibration_rect_flags(text)
        rect = calibration_rect_from_corners(values["left"], values["top"], values["right"], values["bottom"])
    except ValueError as exc:
        print("osu! Calibration Rect")
        print(f"  Error: {exc}")
        print("  Real input: False")
        print("  Submit: False")
        return
    rect_text = f"{int(rect.left)},{int(rect.top)},{int(rect.width)},{int(rect.height)}"
    print("osu! Calibration Rect")
    print(f"  Env: {OSU_PLAYFIELD_RECT_ENV}={rect_text}")
    print("  Real input: False")
    print("  Submit: False")


def print_osu_aim_plan_preview(ctx=None, text: str = "") -> None:
    snapshot = adapter_snapshot(ctx)
    parsed = snapshot.get("parsed")
    if not parsed:
        print("osu! Aim Plan Preview")
        print("  Decision: preview_only_no_input")
        print(f"  Error: {snapshot.get('current_map_error')}")
        print("  Real input: False")
        print("  Submit: False")
        return
    try:
        options = _parse_aim_plan_flags(text)
    except ValueError as exc:
        print("osu! Aim Plan Preview")
        print("  Decision: preview_only_no_input")
        print(f"  Error: {exc}")
        print("  Real input: False")
        print("  Submit: False")
        return
    payload = aim_plan_preview(
        parsed,
        current_time_ms=snapshot.get("current_time_ms"),
        lead_ms=int(options["lead_ms"]),
        limit=int(options["limit"]),
        playfield_rect=snapshot.get("playfield_rect"),
    )
    print("osu! Aim Plan Preview")
    print(f"  Decision: {payload['decision']}")
    print(f"  Lead ms: {payload['lead_ms']}")
    print(f"  Objects: {payload['count']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")


def print_osu_aim_timeline(ctx=None, text: str = "") -> None:
    snapshot = adapter_snapshot(ctx)
    parsed = snapshot.get("parsed")
    if not parsed:
        print("osu! Aim Timeline")
        print("  Decision: telemetry_only_no_input")
        print(f"  Error: {snapshot.get('current_map_error')}")
        print("  Real input: False")
        print("  Submit: False")
        return
    try:
        options = _parse_aim_plan_flags(text)
    except ValueError as exc:
        print("osu! Aim Timeline")
        print("  Decision: telemetry_only_no_input")
        print(f"  Error: {exc}")
        print("  Real input: False")
        print("  Submit: False")
        return
    payload = aim_timeline_telemetry(
        parsed,
        current_time_ms=snapshot.get("current_time_ms"),
        lead_ms=int(options["lead_ms"]),
        limit=int(options["limit"]),
        playfield_rect=snapshot.get("playfield_rect"),
    )
    print("osu! Aim Timeline")
    print(f"  Decision: {payload['decision']}")
    print(f"  Lead ms: {payload['lead_ms']}")
    print(f"  Objects: {payload['count']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")


def print_osu_aim_timeline_json(ctx=None, text: str = "") -> None:
    snapshot = adapter_snapshot(ctx)
    parsed = snapshot.get("parsed")
    if not parsed:
        print(json.dumps({"decision": "telemetry_json_only_no_input", "error": snapshot.get("current_map_error"), "real_input": False, "submit": False}, ensure_ascii=False, indent=2))
        return
    try:
        options = _parse_aim_plan_flags(text)
    except ValueError as exc:
        print(json.dumps({"decision": "telemetry_json_only_no_input", "error": str(exc), "real_input": False, "submit": False}, ensure_ascii=False, indent=2))
        return
    payload = aim_timeline_json_payload(
        parsed,
        current_time_ms=snapshot.get("current_time_ms"),
        lead_ms=int(options["lead_ms"]),
        limit=int(options["limit"]),
        playfield_rect=snapshot.get("playfield_rect"),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_aim_timeline_write(ctx=None, text: str = "") -> None:
    snapshot = adapter_snapshot(ctx)
    parsed = snapshot.get("parsed")
    if not parsed:
        print("osu! Aim Timeline Write")
        print("  Decision: telemetry_file_write_no_input")
        print(f"  Error: {snapshot.get('current_map_error')}")
        print("  Real input: False")
        print("  Submit: False")
        return
    try:
        options = _parse_aim_plan_flags(text)
        output_path = _resolve_aim_timeline_path(options.get("path") if isinstance(options.get("path"), str) else None)
    except ValueError as exc:
        print("osu! Aim Timeline Write")
        print("  Decision: telemetry_file_write_no_input")
        print(f"  Error: {exc}")
        print("  Real input: False")
        print("  Submit: False")
        return
    payload = aim_timeline_json_payload(
        parsed,
        current_time_ms=snapshot.get("current_time_ms"),
        lead_ms=int(options["lead_ms"]),
        limit=int(options["limit"]),
        playfield_rect=snapshot.get("playfield_rect"),
    )
    payload["schema"] = "nana.osu.aim_timeline.v1"
    payload["written_at"] = time.time()
    _write_json_atomic(output_path, payload)
    print("osu! Aim Timeline Write")
    print("  Decision: telemetry_file_write_no_input")
    print(f"  Path: {output_path}")
    print(f"  Calibrated: {payload['calibrated']}")
    print(f"  Objects: {payload['count']}")
    print("  Real input: False")
    print("  Submit: False")


def _trajectory_error_payload(decision: str, exc: Exception) -> dict[str, Any]:
    return {
        "schema": "nana.osu.aim_trajectory.v1",
        "version": 1,
        "decision": decision,
        "activity_state": "unknown",
        "calibrated": False,
        "calibration_error": None,
        "playfield_rect": None,
        "readiness": "not_ready",
        "readiness_decision": None,
        "readiness_blockers": [f"trajectory_error:{type(exc).__name__}"],
        "benchmark_recommendation": "not_ready",
        "benchmark_decision": None,
        "benchmark_readiness": "not_ready",
        "input_ready": False,
        "lead_ms": DEFAULT_AIM_LEAD_MS,
        "limit": 15,
        "sample_step_ms": 16,
        "current_time_ms": None,
        "map_loaded": False,
        "map_source": None,
        "map_error": str(exc),
        "objects_count": 0,
        "points_count": 0,
        "timeline_windows": {"immediate_0_500_ms": 0, "upcoming_500_1500_ms": 0},
        "trajectory_points": [],
        "points": [],
        "blockers": [f"trajectory_error:{type(exc).__name__}"],
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


def print_osu_aim_trajectory_preview(text: str = "") -> None:
    """
    Preview local osu! aim trajectory only.

    No input, no submit, no voice/VTS/OBS/Vision, no external bot import/run.
    """
    try:
        payload = build_aim_trajectory_payload(text)
    except Exception as exc:
        payload = _trajectory_error_payload("trajectory_hold_no_points", exc)
    print_aim_trajectory_preview(payload)


def print_osu_aim_trajectory_json(text: str = "") -> None:
    """
    Emit local osu! aim trajectory payload as JSON.
    """
    try:
        payload = build_aim_trajectory_payload(text)
    except Exception as exc:
        payload = _trajectory_error_payload("trajectory_hold_no_points", exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_aim_trajectory_write(text: str = "") -> None:
    """
    Write local osu! aim trajectory JSON only.

    No OBS/API and no input path.
    """
    try:
        result = build_aim_trajectory_write(text)
    except Exception as exc:
        payload = _trajectory_error_payload("trajectory_hold_no_points", exc)
        result = {
            "decision": "trajectory_file_write_error_no_input",
            "path": None,
            "bytes_written": 0,
            "payload": payload,
            "file_write": False,
            "real_input_allowed": False,
            "submit_allowed": False,
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "voice_call": False,
            "obs_call": False,
            "vision_call": False,
        }
    print_aim_trajectory_write(result)


def _executor_error_schedule(decision: str, exc: Exception) -> dict[str, Any]:
    return {
        "schema": "nana.osu.executor_dry_run_schedule.v1",
        "version": 1,
        "decision": decision,
        "source_trajectory_schema": None,
        "source_trajectory_decision": "trajectory_hold_no_points",
        "input_ready": False,
        "advisory_ready": False,
        "readiness": "not_ready",
        "benchmark_recommendation": "not_ready",
        "executable": False,
        "lead_ms": DEFAULT_AIM_LEAD_MS,
        "limit": 15,
        "sample_step_ms": 16,
        "current_time_ms": None,
        "schedule_count": 0,
        "late_count": 0,
        "immediate_count": 0,
        "future_count": 0,
        "unknown_count": 0,
        "first_action_t": None,
        "last_action_t": None,
        "actions": [],
        "blockers": [f"executor_error:{type(exc).__name__}"],
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


def print_osu_executor_contract_preview(text: str = "") -> None:
    """
    Preview future executor contract only. No executable input path.
    """
    try:
        contract = build_executor_contract_preview(text)
    except Exception as exc:
        contract = {
            "schema": "nana.osu.executor_contract.v1",
            "version": 1,
            "decision": "executor_contract_preview_only_no_input",
            "scope": "osu_executor_future_contract",
            "action_types": [],
            "required_action_fields": [],
            "timing_states": [],
            "trajectory_source": "nana.osu.aim_trajectory.v1",
            "lead_ms": DEFAULT_AIM_LEAD_MS,
            "limit": 15,
            "sample_step_ms": 16,
            "executable": False,
            "real_input_allowed": False,
            "submit_allowed": False,
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "voice_call": False,
            "obs_call": False,
            "vision_call": False,
            "error": f"executor_contract_error:{type(exc).__name__}",
        }
    print_executor_contract_preview(contract)


def print_osu_executor_dry_run_schedule(text: str = "") -> None:
    """
    Print non-executable action schedule from trajectory points.
    """
    try:
        schedule = build_executor_dry_run_schedule(text)
    except Exception as exc:
        schedule = _executor_error_schedule("executor_dry_run_hold_no_points", exc)
    print_executor_dry_run_schedule(schedule)


def print_osu_executor_dry_run_json(text: str = "") -> None:
    """
    Emit non-executable action schedule as JSON.
    """
    try:
        schedule = build_executor_dry_run_schedule(text)
    except Exception as exc:
        schedule = _executor_error_schedule("executor_dry_run_hold_no_points", exc)
    print(json.dumps(schedule, ensure_ascii=False, indent=2))


def print_osu_executor_dry_run_write(text: str = "") -> None:
    """
    Write non-executable local JSON schedule only.
    """
    try:
        result = build_executor_dry_run_write(text)
    except Exception as exc:
        schedule = _executor_error_schedule("executor_dry_run_hold_no_points", exc)
        result = {
            "decision": "executor_dry_run_file_write_error_no_input",
            "path": None,
            "bytes_written": 0,
            "payload": schedule,
            "file_write": False,
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
    print_executor_dry_run_write(result)


def _preflight_error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "schema": "nana.osu.executor_preflight.v1",
        "version": 1,
        "decision": "executor_preflight_blocked_no_input",
        "env_gate_name": REAL_INPUT_EXECUTOR_ENV,
        "env_gate_present": False,
        "env_gate_enabled": False,
        "token_present": False,
        "token_valid": False,
        "readiness_ready": False,
        "benchmark_ready": False,
        "trajectory_ready": False,
        "schedule_ready": False,
        "all_actions_non_executable": True,
        "emergency_abort_available": "unavailable_no_helper",
        "emergency_abort_strategy": "preview_only",
        "abort_command_available": False,
        "abort_flag_path": None,
        "emergency_release_available": "unavailable_no_helper",
        "emergency_release_strategy": "preview_only",
        "release_command_available": False,
        "focus_readiness": "unavailable_no_helper",
        "window_focus_readiness": "unavailable_no_helper",
        "schedule_count": 0,
        "schedule_current_time_ms": None,
        "first_action_t": None,
        "last_action_t": None,
        "first_action_relative_ms": None,
        "source_schedule_decision": "executor_dry_run_hold_no_points",
        "source_trajectory_decision": "trajectory_hold_no_points",
        "readiness": "not_ready",
        "benchmark_recommendation": "not_ready",
        "blockers": [f"preflight_error:{type(exc).__name__}"],
        "ready_for_manual_review": False,
        "executable": False,
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


def print_osu_executor_preflight_status(text: str = "") -> None:
    """
    Print final no-input executor preflight status.
    """
    try:
        payload = build_executor_preflight_payload(text)
    except Exception as exc:
        payload = _preflight_error_payload(exc)
    print_executor_preflight_status(payload, title="osu! Executor Preflight Status")


def print_osu_executor_preflight_preview(text: str = "") -> None:
    """
    Preview final no-input executor preflight gates.
    """
    try:
        payload = build_executor_preflight_payload(text)
    except Exception as exc:
        payload = _preflight_error_payload(exc)
    print_executor_preflight_status(payload, title="osu! Executor Preflight Preview")


def print_osu_executor_preflight_json(text: str = "") -> None:
    """
    Emit final no-input executor preflight payload as JSON.
    """
    try:
        payload = build_executor_preflight_payload(text)
    except Exception as exc:
        payload = _preflight_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _executor_arm_error_payload(decision: str, exc: Exception) -> dict[str, Any]:
    return {
        "schema": "nana.osu.executor_arm_preview.v1",
        "version": 1,
        "decision": decision,
        "reason": f"executor_arm_error:{type(exc).__name__}",
        "armable": False,
        "armed": False,
        "executable": False,
        "selected_action_id": None,
        "selected_action_type": None,
        "selected_source_object_index": None,
        "selected_scheduled_t": None,
        "selected_relative_ms": None,
        "selected_timing_state": None,
        "selected_playfield": None,
        "selected_screen": None,
        "preflight_decision": "executor_preflight_error_no_input",
        "ready_for_manual_review": False,
        "focus_readiness": "unavailable_no_helper",
        "emergency_abort_available": "unavailable_no_helper",
        "emergency_release_available": "unavailable_no_helper",
        "release_strategy": "preview_only",
        "schedule_count": 0,
        "late_count": 0,
        "immediate_count": 0,
        "future_count": 0,
        "blockers": [f"executor_arm_error:{type(exc).__name__}"],
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
        "created_at": time.time(),
    }


def print_osu_executor_arm_status(text: str = "") -> None:
    """
    Status-only executor arm review. No persistent armed state and no input.
    """
    try:
        payload = build_executor_arm_status_payload(text)
    except Exception as exc:
        payload = _executor_arm_error_payload("executor_arm_status_only_no_input", exc)
    print_executor_arm_preview(payload, title="osu! Executor Arm Status")


def print_osu_executor_arm_preview(text: str = "") -> None:
    """
    Preview one future executor action candidate for manual review only.
    """
    try:
        payload = build_executor_arm_preview_payload(text)
    except Exception as exc:
        payload = _executor_arm_error_payload("executor_arm_preview_hold_no_input", exc)
    print_executor_arm_preview(payload, title="osu! Executor Arm Preview")


def print_osu_executor_arm_json(text: str = "") -> None:
    """
    Emit one-action arm preview as JSON. No persistent state and no input.
    """
    try:
        payload = build_executor_arm_preview_payload(text)
    except Exception as exc:
        payload = _executor_arm_error_payload("executor_arm_preview_hold_no_input", exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _executor_armed_lease_error_payload(decision: str, lease_state: str, exc: Exception) -> dict[str, Any]:
    return {
        "schema": "nana.osu.executor_armed_lease.v1",
        "version": 1,
        "decision": decision,
        "reason": f"executor_armed_lease_error:{type(exc).__name__}",
        "lease_state": lease_state,
        "lease_id": None,
        "lease_ttl_ms": 500,
        "lease_created_at": None,
        "lease_expires_at": None,
        "lease_remaining_ms": 0,
        "armed_preview": False,
        "armed": False,
        "armable": False,
        "executable": False,
        "selected_action_id": None,
        "selected_action_type": None,
        "selected_scheduled_t": None,
        "selected_relative_ms": None,
        "selected_timing_state": None,
        "focus_readiness": "unavailable_no_helper",
        "emergency_abort_available": "unavailable_no_helper",
        "emergency_release_available": "unavailable_no_helper",
        "preflight_decision": "executor_preflight_error_no_input",
        "ready_for_manual_review": False,
        "blockers": [f"executor_armed_lease_error:{type(exc).__name__}"],
        "file_write": False,
        "cleared": False,
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
        "created_at": time.time(),
    }


def print_osu_executor_armed_lease_preview(text: str = "") -> None:
    """
    Create a no-input armed lease preview for one selected action.
    """
    try:
        payload = build_executor_armed_lease_preview_payload(text)
    except Exception as exc:
        payload = _executor_armed_lease_error_payload("executor_armed_lease_preview_hold_no_input", "no_lease", exc)
    print_executor_armed_lease(payload, title="osu! Executor Armed Lease Preview")


def print_osu_executor_armed_lease_json(text: str = "") -> None:
    """
    Emit a no-input armed lease preview as JSON.
    """
    try:
        payload = build_executor_armed_lease_preview_payload(text)
    except Exception as exc:
        payload = _executor_armed_lease_error_payload("executor_armed_lease_preview_hold_no_input", "no_lease", exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_executor_armed_lease_status(text: str = "") -> None:
    """
    Read current no-input armed lease preview status.
    """
    try:
        payload = build_executor_armed_lease_status_payload(text)
    except Exception as exc:
        payload = _executor_armed_lease_error_payload("executor_armed_lease_status_only_no_input", "no_lease", exc)
    print_executor_armed_lease(payload, title="osu! Executor Armed Lease Status")


def print_osu_executor_armed_lease_clear(text: str = "") -> None:
    """
    Clear only the preview lease state/file. Does not call emergency release.
    """
    try:
        payload = build_executor_armed_lease_clear_payload(text)
    except Exception as exc:
        payload = _executor_armed_lease_error_payload("executor_armed_lease_clear_hold_no_input", "no_lease", exc)
    print_executor_armed_lease(payload, title="osu! Executor Armed Lease Clear")


def _executor_sim_tick_error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "schema": "nana.osu.executor_simulated_tick.v1",
        "version": 1,
        "decision": "executor_sim_tick_hold_no_input",
        "tick_decision": "would_hold",
        "reason": f"executor_sim_tick_error:{type(exc).__name__}",
        "lease_state": "no_lease",
        "lease_id": None,
        "lease_remaining_ms": 0,
        "selected_action_id": None,
        "selected_action_type": None,
        "selected_scheduled_t": None,
        "selected_timing_state": None,
        "current_time_ms": None,
        "time_to_action_ms": None,
        "early_window_ms": 80,
        "late_window_ms": 60,
        "would_execute": False,
        "would_wait": False,
        "would_hold": True,
        "would_skip_late": False,
        "armed_preview": False,
        "armed": False,
        "armable": False,
        "executable": False,
        "focus_readiness": "unavailable_no_helper",
        "emergency_abort_available": "unavailable_no_helper",
        "emergency_release_available": "unavailable_no_helper",
        "preflight_decision": "executor_preflight_error_no_input",
        "status_recheck_mode": "snapshot",
        "blockers": [f"executor_sim_tick_error:{type(exc).__name__}"],
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
        "created_at": time.time(),
    }


def print_osu_executor_sim_tick_preview(text: str = "") -> None:
    """
    Simulate one executor tick. Preview only; no input.
    """
    try:
        payload = build_executor_simulated_tick_payload(text)
    except Exception as exc:
        payload = _executor_sim_tick_error_payload(exc)
    print_executor_simulated_tick(payload, title="osu! Executor Simulated Tick Preview")


def print_osu_executor_sim_tick_json(text: str = "") -> None:
    """
    Emit simulated tick preview as JSON. No input.
    """
    try:
        payload = build_executor_simulated_tick_payload(text)
    except Exception as exc:
        payload = _executor_sim_tick_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_executor_sim_tick_status(text: str = "") -> None:
    """
    Status-only simulated tick. No input.
    """
    try:
        payload = build_executor_simulated_tick_status_payload(text)
    except Exception as exc:
        payload = _executor_sim_tick_error_payload(exc)
        payload["decision"] = "executor_sim_tick_status_only_no_input"
    print_executor_simulated_tick(payload, title="osu! Executor Simulated Tick Status")


def _executor_sim_tick_loop_error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "schema": "nana.osu.executor_sim_tick_loop.v1",
        "version": 1,
        "decision": "executor_sim_tick_loop_hold_no_input",
        "loop_result": "loop_not_ready",
        "reason": f"executor_sim_tick_loop_error:{type(exc).__name__}",
        "samples_requested": 8,
        "samples_attempted": 0,
        "samples_ok": 0,
        "interval_ms": 25,
        "early_window_ms": 80,
        "late_window_ms": 60,
        "lease_ttl_ms": 2000,
        "first_tick_decision": None,
        "last_tick_decision": None,
        "observed_wait": False,
        "observed_execute_preview_only": False,
        "observed_skip_late": False,
        "observed_hold": False,
        "execute_preview_count": 0,
        "wait_count": 0,
        "skip_late_count": 0,
        "hold_count": 0,
        "min_time_to_action_ms": None,
        "max_time_to_action_ms": None,
        "closest_time_to_action_ms": None,
        "first_execute_sample_index": None,
        "sample_decisions": [],
        "sample_decisions_truncated": False,
        "lease_state": "no_lease",
        "lease_id": None,
        "lease_remaining_ms": 0,
        "selected_action_id": None,
        "selected_action_type": None,
        "selected_scheduled_t": None,
        "selected_timing_state": None,
        "current_time_ms": None,
        "time_to_action_ms": None,
        "focus_readiness": "unavailable_no_helper",
        "emergency_abort_available": "unavailable_no_helper",
        "emergency_release_available": "unavailable_no_helper",
        "preflight_decision": "executor_preflight_error_no_input",
        "status_recheck_mode": "snapshot",
        "armed_preview": False,
        "armable": False,
        "armed": False,
        "executable": False,
        "blockers": [f"executor_sim_tick_loop_error:{type(exc).__name__}"],
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
        "created_at": time.time(),
    }


def print_osu_executor_sim_tick_loop_preview(text: str = "") -> None:
    """
    Sample simulated ticks over a short interval. Preview only; no input.
    """
    try:
        payload = build_executor_sim_tick_loop_payload(text)
    except Exception as exc:
        payload = _executor_sim_tick_loop_error_payload(exc)
    print_executor_sim_tick_loop(payload, title="osu! Executor Sim Tick Loop Preview")


def print_osu_executor_sim_tick_loop_json(text: str = "") -> None:
    """
    Emit simulated tick loop preview as JSON. No input.
    """
    try:
        payload = build_executor_sim_tick_loop_payload(text)
    except Exception as exc:
        payload = _executor_sim_tick_loop_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_executor_sim_tick_loop_status(text: str = "") -> None:
    """
    Status-only simulated tick loop. No input.
    """
    try:
        payload = build_executor_sim_tick_loop_status_payload(text)
    except Exception as exc:
        payload = _executor_sim_tick_loop_error_payload(exc)
        payload["decision"] = "executor_sim_tick_loop_status_only_no_input"
    print_executor_sim_tick_loop(payload, title="osu! Executor Sim Tick Loop Status")


def _executor_action_packet_error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "schema": "nana.osu.executor_action_packet_preview.v1",
        "version": 1,
        "decision": "executor_action_packet_hold_no_input",
        "packet_ready": False,
        "packet_kind": "preview_only",
        "packet_schema": "nana.osu.executor_action_packet.v1",
        "packet_id": None,
        "packet_action": None,
        "packet_action_type": None,
        "source_sample_index": None,
        "source_tick_decision": None,
        "loop_result": "loop_not_ready",
        "execute_preview_count": 0,
        "action_id": None,
        "source_object_index": None,
        "scheduled_t": None,
        "current_time_ms": None,
        "time_to_action_ms": None,
        "playfield": None,
        "screen": None,
        "timing_state": None,
        "reason": f"executor_action_packet_error:{type(exc).__name__}",
        "blockers": [f"executor_action_packet_error:{type(exc).__name__}"],
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
        "created_at": time.time(),
    }


def print_osu_executor_action_packet_preview(text: str = "") -> None:
    """
    Build final preview-only action packet shape. No input.
    """
    try:
        payload = build_executor_action_packet_preview_payload(text)
    except Exception as exc:
        payload = _executor_action_packet_error_payload(exc)
    print_executor_action_packet_preview(payload, title="osu! Executor Action Packet Preview")


def print_osu_executor_action_packet_json(text: str = "") -> None:
    """
    Emit final preview-only action packet shape as JSON. No input.
    """
    try:
        payload = build_executor_action_packet_preview_payload(text)
    except Exception as exc:
        payload = _executor_action_packet_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_executor_action_packet_status(text: str = "") -> None:
    """
    Status-only action packet preview. No input.
    """
    try:
        payload = build_executor_action_packet_status_payload(text)
    except Exception as exc:
        payload = _executor_action_packet_error_payload(exc)
        payload["decision"] = "executor_action_packet_status_only_no_input"
    print_executor_action_packet_preview(payload, title="osu! Executor Action Packet Status")


def _executor_dispatch_preflight_error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "schema": "nana.osu.executor_dispatch_preflight.v1",
        "version": 1,
        "decision": "executor_dispatch_preflight_hold_no_input",
        "dispatch_ready": False,
        "dispatch_mode": "preview_only",
        "packet_valid": False,
        "packet_ready": False,
        "packet_schema": None,
        "packet_id": None,
        "packet_action": None,
        "packet_action_type": None,
        "action_id": None,
        "source_object_index": None,
        "source_sample_index": None,
        "source_tick_decision": None,
        "loop_result": None,
        "execute_preview_count": 0,
        "scheduled_t": None,
        "current_time_ms": None,
        "time_to_action_ms": None,
        "dispatch_late_window_ms": 120,
        "dispatch_early_window_ms": 120,
        "timing_state": None,
        "playfield": None,
        "screen": None,
        "focus_readiness": "unavailable_no_helper",
        "window_title": None,
        "window_class": None,
        "process_name": None,
        "emergency_abort_available": "unavailable_no_helper",
        "emergency_abort_strategy": "preview_only",
        "emergency_release_available": "unavailable_no_helper",
        "emergency_release_strategy": "preview_only",
        "blockers": [f"executor_dispatch_preflight_error:{type(exc).__name__}"],
        "reason": f"executor_dispatch_preflight_error:{type(exc).__name__}",
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
        "created_at": time.time(),
    }


def print_osu_executor_dispatch_preflight_preview(text: str = "") -> None:
    """
    Validate final preview-only action packet for dispatch review. No input.
    """
    try:
        payload = build_executor_dispatch_preflight_payload(text)
    except Exception as exc:
        payload = _executor_dispatch_preflight_error_payload(exc)
    print_executor_dispatch_preflight(payload, title="osu! Executor Dispatch Preflight Preview")


def print_osu_executor_dispatch_preflight_json(text: str = "") -> None:
    """
    Emit dispatch preflight validation as JSON. No input.
    """
    try:
        payload = build_executor_dispatch_preflight_payload(text)
    except Exception as exc:
        payload = _executor_dispatch_preflight_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_executor_dispatch_preflight_status(text: str = "") -> None:
    """
    Status-only dispatch preflight validation. No input.
    """
    try:
        payload = build_executor_dispatch_preflight_status_payload(text)
    except Exception as exc:
        payload = _executor_dispatch_preflight_error_payload(exc)
        payload["decision"] = "executor_dispatch_preflight_status_only_no_input"
    print_executor_dispatch_preflight(payload, title="osu! Executor Dispatch Preflight Status")


def _executor_shadow_dispatch_session_error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "schema": "nana.osu.executor_shadow_dispatch_session.v1",
        "version": 1,
        "decision": "executor_shadow_dispatch_session_hold_no_input",
        "session_result": "session_not_ready",
        "reason": f"executor_shadow_dispatch_session_error:{type(exc).__name__}",
        "attempts_requested": 0,
        "attempts_attempted": 0,
        "attempts_ok": 0,
        "attempts_max": 10,
        "interval_ms": 0,
        "interval_max_ms": 500,
        "samples_max": 50,
        "dispatch_ready_count": 0,
        "packet_valid_count": 0,
        "packet_ready_count": 0,
        "timing_consistent_count": 0,
        "metadata_complete_count": 0,
        "stale_lease_ignored_count": 0,
        "unique_action_count": 0,
        "first_dispatch_ready_attempt_index": None,
        "first_packet_id": None,
        "first_packet_action": None,
        "first_time_to_action_ms": None,
        "min_time_to_action_ms": None,
        "max_time_to_action_ms": None,
        "closest_time_to_action_ms": None,
        "attempts": [],
        "blockers": [f"executor_shadow_dispatch_session_error:{type(exc).__name__}"],
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


def print_osu_executor_shadow_dispatch_session_preview(text: str = "") -> None:
    """
    Run a bounded multi-attempt shadow dispatch session. No input.
    """
    try:
        payload = build_executor_shadow_dispatch_session_payload(text)
    except Exception as exc:
        payload = _executor_shadow_dispatch_session_error_payload(exc)
    print_executor_shadow_dispatch_session(payload, title="osu! Executor Shadow Dispatch Session Preview")


def print_osu_executor_shadow_dispatch_session_json(text: str = "") -> None:
    """
    Emit shadow dispatch session preview as JSON. No input.
    """
    try:
        payload = build_executor_shadow_dispatch_session_payload(text)
    except Exception as exc:
        payload = _executor_shadow_dispatch_session_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_executor_shadow_dispatch_session_status(text: str = "") -> None:
    """
    Status-only shadow dispatch session view. No persistent execution state.
    """
    try:
        payload = build_executor_shadow_dispatch_session_status_payload(text)
    except Exception as exc:
        payload = _executor_shadow_dispatch_session_error_payload(exc)
        payload["decision"] = "executor_shadow_dispatch_session_status_only_no_input"
    print_executor_shadow_dispatch_session(payload, title="osu! Executor Shadow Dispatch Session Status")


def print_osu_pre_input_commit_preview(text: str = "") -> None:
    """
    Build a no-input pre-input commit barrier dossier for operator review.
    """
    try:
        payload = build_pre_input_commit_barrier_payload(text)
    except Exception as exc:
        payload = build_pre_input_commit_error_payload(exc)
    print_pre_input_commit_barrier(payload, title="osu! Pre-Input Commit Barrier Preview")


def print_osu_pre_input_commit_json(text: str = "") -> None:
    """
    Emit the pre-input commit barrier dossier as JSON. No input.
    """
    try:
        payload = build_pre_input_commit_json_payload(text)
    except Exception as exc:
        payload = build_pre_input_commit_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_pre_input_commit_status(text: str = "") -> None:
    """
    Status-only pre-input commit barrier. No persistent commit state.
    """
    try:
        payload = build_pre_input_commit_status_payload(text)
    except Exception as exc:
        payload = build_pre_input_commit_error_payload(exc)
        payload["decision"] = "pre_input_commit_status_only_no_input"
    print_pre_input_commit_barrier(payload, title="osu! Pre-Input Commit Barrier Status")


def _play_plan_error_payload(exc: Exception, *, schema: str = "nana.osu.play_plan_cache.v1") -> dict[str, Any]:
    return {
        "schema": schema,
        "version": 1,
        "decision": "play_plan_cache_hold_no_input" if schema.endswith("play_plan_cache.v1") else "runtime_tick_hold_no_input",
        "reason": f"play_plan_error:{type(exc).__name__}",
        "plan_ready": False,
        "cache_hit": False,
        "cache_key": None,
        "beatmap_path": None,
        "map_source": None,
        "map_changed": False,
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
        "blockers": [f"play_plan_error:{type(exc).__name__}"],
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
        "created_at": time.time(),
    }


def print_osu_play_plan_preview(text: str = "") -> None:
    """
    Build/cache a no-input current beatmap play plan.
    """
    try:
        payload = build_play_plan_payload(text)
    except Exception as exc:
        payload = _play_plan_error_payload(exc)
    print_play_plan_payload(payload, title="osu! Play Plan Cache Preview")


def print_osu_play_plan_json(text: str = "") -> None:
    """
    Emit the no-input current beatmap play plan as JSON.
    """
    try:
        payload = build_play_plan_payload(text)
    except Exception as exc:
        payload = _play_plan_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_play_plan_status(text: str = "") -> None:
    """
    Read the in-memory no-input play plan cache status.
    """
    try:
        payload = build_play_plan_status_payload(text)
    except Exception as exc:
        payload = _play_plan_error_payload(exc)
        payload["decision"] = "play_plan_cache_status_only_no_input"
    print_play_plan_payload(payload, title="osu! Play Plan Cache Status")


def print_osu_runtime_tick_preview(text: str = "") -> None:
    """
    Preview a runtime tick lookup from the cached play plan. No input.
    """
    try:
        payload = build_runtime_tick_payload(text)
    except Exception as exc:
        payload = _play_plan_error_payload(exc, schema="nana.osu.runtime_tick_preview.v1")
    print_runtime_tick_payload(payload, title="osu! Runtime Tick Preview")


def print_osu_runtime_tick_json(text: str = "") -> None:
    """
    Emit runtime tick lookup payload as JSON. No input.
    """
    try:
        payload = build_runtime_tick_payload(text)
    except Exception as exc:
        payload = _play_plan_error_payload(exc, schema="nana.osu.runtime_tick_preview.v1")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_runtime_packet_preview(text: str = "") -> None:
    """
    Preview a compact runtime packet from the cached play plan. No input.
    """
    try:
        payload = build_runtime_packet_payload(text)
    except Exception as exc:
        payload = build_runtime_packet_error_payload(exc)
    print_runtime_packet_payload(payload, title="osu! Runtime Packet Preview")


def print_osu_runtime_packet_json(text: str = "") -> None:
    """
    Emit runtime packet payload as JSON. No input.
    """
    try:
        payload = build_runtime_packet_payload(text)
    except Exception as exc:
        payload = build_runtime_packet_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_runtime_packet_status(text: str = "") -> None:
    """
    Read the last in-memory runtime packet/cache state. No input.
    """
    try:
        payload = build_runtime_packet_status_payload(text)
    except Exception as exc:
        payload = build_runtime_packet_error_payload(exc)
        payload["decision"] = "runtime_packet_status_only_no_input"
    print_runtime_packet_payload(payload, title="osu! Runtime Packet Status")


def print_osu_runtime_packet_loop_preview(text: str = "") -> None:
    """
    Preview a thin runtime packet sampling loop. No input.
    """
    try:
        payload = build_runtime_packet_loop_payload(text)
    except Exception as exc:
        payload = build_runtime_packet_loop_error_payload(exc)
    print_runtime_packet_loop_payload(payload, title="osu! Runtime Packet Loop Preview")


def print_osu_runtime_packet_loop_json(text: str = "") -> None:
    """
    Emit runtime packet loop payload as JSON. No input.
    """
    try:
        payload = build_runtime_packet_loop_payload(text)
    except Exception as exc:
        payload = build_runtime_packet_loop_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_runtime_packet_loop_status(text: str = "") -> None:
    """
    Read the last in-memory runtime packet loop payload. No input.
    """
    try:
        payload = build_runtime_packet_loop_status_payload(text)
    except Exception as exc:
        payload = build_runtime_packet_loop_error_payload(exc)
        payload["decision"] = "runtime_packet_loop_status_only_no_input"
    print_runtime_packet_loop_payload(payload, title="osu! Runtime Packet Loop Status")


def print_osu_runtime_controller_preview(text: str = "") -> None:
    """
    Preview a thin runtime controller classification. No input.
    """
    try:
        payload = build_runtime_controller_preview_payload(text)
    except Exception as exc:
        payload = build_runtime_controller_error_payload(exc)
    print_runtime_controller_payload(payload, title="osu! Runtime Controller Preview")


def print_osu_runtime_controller_json(text: str = "") -> None:
    """
    Emit runtime controller preview payload as JSON. No input.
    """
    try:
        payload = build_runtime_controller_preview_payload(text)
    except Exception as exc:
        payload = build_runtime_controller_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_runtime_controller_status(text: str = "") -> None:
    """
    Read the last in-memory runtime controller payload. No input.
    """
    try:
        payload = build_runtime_controller_status_payload(text)
    except Exception as exc:
        payload = build_runtime_controller_error_payload(exc)
        payload["decision"] = "runtime_controller_status_only_no_input"
    print_runtime_controller_payload(payload, title="osu! Runtime Controller Status")


def print_osu_runtime_intent_preview(text: str = "") -> None:
    """
    Preview a concrete no-input runtime executor intent.
    """
    try:
        payload = build_runtime_executor_intent_payload(text)
    except Exception as exc:
        payload = build_runtime_executor_intent_error_payload(exc)
    print_runtime_executor_intent_payload(payload, title="osu! Runtime Executor Intent Preview")


def print_osu_runtime_intent_json(text: str = "") -> None:
    """
    Emit runtime executor intent payload as JSON. No input.
    """
    try:
        payload = build_runtime_executor_intent_payload(text)
    except Exception as exc:
        payload = build_runtime_executor_intent_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_runtime_intent_status(text: str = "") -> None:
    """
    Read the last in-memory runtime executor intent payload. No input.
    """
    try:
        payload = build_runtime_executor_intent_status_payload(text)
    except Exception as exc:
        payload = build_runtime_executor_intent_error_payload(exc)
        payload["decision"] = "runtime_executor_intent_status_only_no_input"
    print_runtime_executor_intent_payload(payload, title="osu! Runtime Executor Intent Status")


def print_osu_runtime_backend_dry_preview(text: str = "") -> None:
    """
    Preview a dry runtime executor backend operation. No input.
    """
    try:
        payload = build_runtime_executor_backend_dry_payload(text)
    except Exception as exc:
        payload = build_runtime_executor_backend_dry_error_payload(exc)
    print_runtime_executor_backend_dry_payload(payload, title="osu! Runtime Executor Backend Dry Preview")


def print_osu_runtime_backend_dry_json(text: str = "") -> None:
    """
    Emit dry runtime executor backend payload as JSON. No input.
    """
    try:
        payload = build_runtime_executor_backend_dry_payload(text)
    except Exception as exc:
        payload = build_runtime_executor_backend_dry_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_runtime_backend_dry_status(text: str = "") -> None:
    """
    Read the last in-memory dry runtime executor backend payload. No input.
    """
    try:
        payload = build_runtime_executor_backend_dry_status_payload(text)
    except Exception as exc:
        payload = build_runtime_executor_backend_dry_error_payload(exc)
        payload["decision"] = "runtime_executor_backend_dry_status_only_no_input"
    print_runtime_executor_backend_dry_payload(payload, title="osu! Runtime Executor Backend Dry Status")


def print_osu_runtime_backend_gate_preview(text: str = "") -> None:
    """
    Preview the gated runtime executor backend boundary. No input.
    """
    try:
        payload = build_runtime_executor_backend_gate_payload(text)
    except Exception as exc:
        payload = build_runtime_executor_backend_gate_error_payload(exc)
    print_runtime_executor_backend_gate_payload(payload, title="osu! Runtime Executor Backend Gate Preview")


def print_osu_runtime_backend_gate_json(text: str = "") -> None:
    """
    Emit gated runtime executor backend boundary payload as JSON. No input.
    """
    try:
        payload = build_runtime_executor_backend_gate_payload(text)
    except Exception as exc:
        payload = build_runtime_executor_backend_gate_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_runtime_backend_gate_status(text: str = "") -> None:
    """
    Read the last in-memory gated runtime executor backend payload. No input.
    """
    try:
        payload = build_runtime_executor_backend_gate_status_payload(text)
    except Exception as exc:
        payload = build_runtime_executor_backend_gate_error_payload(exc)
        payload["decision"] = "runtime_executor_backend_gate_status_only_no_input"
    print_runtime_executor_backend_gate_payload(payload, title="osu! Runtime Executor Backend Gate Status")


def print_osu_vendor_bot_status(text: str = "") -> None:
    payload = build_vendor_bot_status_payload(text)
    print_vendor_bot_payload(payload, title="osu! Vendor Bot Runtime Status")


def print_osu_vendor_bot_start(text: str = "") -> None:
    payload = build_vendor_bot_start_payload(text)
    print_vendor_bot_payload(payload, title="osu! Vendor Bot Runtime Start")


def print_osu_vendor_bot_stop(text: str = "") -> None:
    payload = build_vendor_bot_stop_payload(text)
    print_vendor_bot_payload(payload, title="osu! Vendor Bot Runtime Stop")


def print_osu_vendor_bot_json(text: str = "") -> None:
    payload = build_vendor_bot_status_payload(text)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_vendor_boss_lifecycle(text: str = "") -> None:
    payload = build_vendor_boss_lifecycle_payload(text)
    print_vendor_bot_payload(payload, title="osu! Vendor Boss Lifecycle")
    print(f"  Lifecycle action: {payload.get('lifecycle_action')}")
    print(f"  Boss skill active: {payload.get('boss_skill_active')}")
    print(f"  Activity state: {payload.get('activity_state')}")


def print_osu_vendor_boss_auto_start(text: str = "") -> None:
    payload = build_vendor_boss_auto_start_payload(text)
    print_vendor_bot_payload(payload, title="osu! Vendor Boss Auto Start")
    print(f"  Watcher running: {payload.get('watcher_running')}")
    print(f"  Watcher interval ms: {payload.get('watcher_interval_ms')}")
    print(f"  Last lifecycle action: {payload.get('last_lifecycle_action')}")


def print_osu_vendor_boss_auto_stop(text: str = "") -> None:
    payload = build_vendor_boss_auto_stop_payload(text)
    print_vendor_bot_payload(payload, title="osu! Vendor Boss Auto Stop")
    print(f"  Watcher was running: {payload.get('watcher_was_running')}")
    print(f"  Watcher running: {payload.get('watcher_running')}")
    print(f"  Stop boss: {payload.get('stop_boss')}")
    print(f"  Boss stop called: {payload.get('boss_stop_called')}")


def print_osu_vendor_boss_auto_status(text: str = "") -> None:
    payload = build_vendor_boss_auto_status_payload(text)
    print_vendor_bot_payload(payload, title="osu! Vendor Boss Auto Status")
    print(f"  Watcher running: {payload.get('watcher_running')}")
    print(f"  Watcher interval ms: {payload.get('watcher_interval_ms')}")
    print(f"  Watcher uptime ms: {payload.get('watcher_uptime_ms')}")
    print(f"  Last lifecycle action: {payload.get('last_lifecycle_action')}")
    print(f"  Last activity state: {payload.get('last_activity_state')}")


def print_osu_vendor_boss_kill(text: str = "") -> None:
    payload = build_vendor_boss_kill_payload(text)
    print_vendor_bot_payload(payload, title="osu! Vendor Boss Kill")
    print(f"  Killed: {payload.get('killed')}")
    print(f"  Kill mode: {payload.get('kill_mode')}")
    print(f"  Killed pids: {payload.get('killed_pids')}")
    print(f"  Watcher running: {payload.get('watcher_running')}")


def print_osu_vendor_boss_kill_all(text: str = "") -> None:
    payload = build_vendor_boss_kill_all_payload(text)
    print_vendor_bot_payload(payload, title="osu! Vendor Boss Kill All")
    print(f"  Killed: {payload.get('killed')}")
    print(f"  Kill mode: {payload.get('kill_mode')}")
    print(f"  Killed pids: {payload.get('killed_pids')}")
    print(f"  Watcher running: {payload.get('watcher_running')}")


def print_osu_kill_switch(text: str = "") -> None:
    payload = build_vendor_boss_kill_all_payload(text, trigger="command_alias")
    print_vendor_bot_payload(payload, title="osu! Kill Switch")
    print(f"  Killed: {payload.get('killed')}")
    print(f"  Kill mode: {payload.get('kill_mode')}")
    print(f"  Killed pids: {payload.get('killed_pids')}")
    print(f"  Watcher running: {payload.get('watcher_running')}")


def print_osu_kill_switch_status(text: str = "") -> None:
    payload = build_kill_switch_status_payload(text)
    print_vendor_bot_payload(payload, title="osu! Kill Switch Status")
    print(f"  Kill switch available: {payload.get('kill_switch_available')}")
    print(f"  Kill hotkey enabled: {payload.get('kill_hotkey_enabled')}")
    print(f"  Kill hotkey registered: {payload.get('kill_hotkey_registered')}")
    print(f"  Kill hotkey key: {payload.get('kill_hotkey_key')}")
    print(f"  Last killed pids: {payload.get('last_killed_pids')}")


def print_osu_vendor_model_install(text: str = "") -> None:
    payload = build_vendor_model_install_payload(text)
    print_vendor_bot_payload(payload, title="osu! Vendor Model Install")
    print(f"  Target model path: {payload.get('target_model_path')}")
    print(f"  Source model path: {payload.get('source_model_path')}")
    print(f"  Source present: {payload.get('source_present')}")
    print(f"  Installed: {payload.get('installed')}")


def print_osu_vendor_model_heuristic(text: str = "") -> None:
    payload = build_vendor_model_heuristic_payload(text)
    print_vendor_bot_payload(payload, title="osu! Vendor Heuristic Model")
    print(f"  Model kind: {payload.get('model_kind')}")
    print(f"  Input shape: {payload.get('input_shape')}")
    print(f"  Output shape: {payload.get('output_shape')}")
    print(f"  Probe shape: {payload.get('probe_shape')}")
    print(f"  Installed: {payload.get('installed')}")


def print_osu_cursor_dance_status(text: str = "") -> None:
    payload = build_cursor_dance_status_payload(text)
    print_cursor_dance_payload(payload, title="osu! Cursor Dance Boss Status")


def print_osu_cursor_dance_start(text: str = "") -> None:
    payload = build_cursor_dance_start_payload(text)
    print_cursor_dance_payload(payload, title="osu! Cursor Dance Boss Start")


def print_osu_cursor_dance_stop(text: str = "") -> None:
    payload = build_cursor_dance_stop_payload(text)
    print_cursor_dance_payload(payload, title="osu! Cursor Dance Boss Stop")
    print(f"  Stopped PID: {payload.get('stopped_pid')}")
    print(f"  Release called: {payload.get('release_called')}")


def print_osu_cursor_dance_kill(text: str = "") -> None:
    payload = build_cursor_dance_kill_payload(text)
    print_cursor_dance_payload(payload, title="osu! Cursor Dance Boss Kill")
    print(f"  Killed: {payload.get('killed')}")
    print(f"  Killed pids: {payload.get('killed_pids')}")
    print(f"  Release called: {payload.get('release_called')}")


def _stream_presence_error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "schema": "nana.osu.stream_presence_readiness.v1",
        "version": 1,
        "decision": "stream_presence_readiness_preview_only_no_input",
        "overall_ready": False,
        "stream_safe_mode": "degraded",
        "vts_ready": False,
        "vts_connected": False,
        "vts_auth_status": "unavailable",
        "vts_last_error": "readiness_error",
        "vts_degrade_mode": "no_vts_continue",
        "voice_provider": "unknown",
        "voice_configured": False,
        "voice_ready": False,
        "voice_mode": "unavailable",
        "voice_degrade_mode": "subtitle_only_mode",
        "dialogue_ready": False,
        "dialogue_mode": "fallback",
        "public_filter_active": True,
        "public_filter_allowlist": [],
        "public_filter_blocklist": [],
        "public_filter_blocked_fields": [],
        "dialogue_degrade_mode": "template_fallback",
        "dialogue_error": f"{type(exc).__name__}:{exc}",
        "subtitle_ready": False,
        "subtitle_path": "unavailable",
        "subtitle_parent_exists": False,
        "subtitle_parent_writable": False,
        "subtitle_file_exists": False,
        "subtitle_fresh": False,
        "subtitle_degrade_mode": "voice_or_vts_only",
        "obs_api_call": False,
        "executor_preflight_decision": "executor_preflight_unavailable_no_input",
        "executor_ready_for_manual_review": False,
        "token_present": False,
        "token_valid": False,
        "focus_readiness": "unavailable_no_helper",
        "emergency_abort_available": "unavailable_no_helper",
        "emergency_abort_strategy": "preview_only",
        "abort_command_available": False,
        "abort_flag_path": None,
        "emergency_release_available": "unavailable_no_helper",
        "emergency_release_strategy": "preview_only",
        "release_command_available": False,
        "executable": False,
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "voice_call": False,
        "vts_call": False,
        "obs_call": False,
        "vision_call": False,
        "created_at": time.time(),
    }


def _stream_presence_include_executor(text: str) -> bool:
    for token in str(text or "").split()[1:]:
        if token.startswith("--include-executor="):
            return token.split("=", 1)[1].strip().lower() not in {"0", "false", "no", "off"}
    return True


def _stream_presence_executor_text(text: str) -> str:
    parts = ["/osu-executor-preflight-preview"]
    for token in str(text or "").split()[1:]:
        if token.startswith("--operator-approval-token="):
            parts.append(token)
    return " ".join(parts)


def print_osu_stream_presence_readiness(text: str = "") -> None:
    """
    Print aggregate stream presence readiness.

    Read-only preview/status only. Does not call VTS connect/auth, voice/TTS,
    OBS API, or any live input path.
    """
    try:
        payload = build_stream_presence_readiness_preview(
            None,
            include_executor=_stream_presence_include_executor(text),
            executor_text=_stream_presence_executor_text(text),
        )
    except Exception as exc:
        payload = _stream_presence_error_payload(exc)
    print_stream_presence_readiness_preview(payload)


def print_osu_stream_presence_readiness_json(text: str = "") -> None:
    """
    Emit aggregate stream presence readiness payload as JSON.
    """
    try:
        payload = build_stream_presence_readiness_preview(
            None,
            include_executor=_stream_presence_include_executor(text),
            executor_text=_stream_presence_executor_text(text),
        )
    except Exception as exc:
        payload = _stream_presence_error_payload(exc)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_osu_aim_status(text: str = "") -> None:
    try:
        options = _parse_aim_status_flags(text)
        source_path = _resolve_aim_timeline_path(options.get("path") if isinstance(options.get("path"), str) else None)
        status = read_aim_status(source_path, stale_ms=int(options["stale_ms"]))
    except ValueError as exc:
        print("osu! Aim Status")
        print("  Decision: status_only_no_input")
        print("  Source: invalid")
        print("  Status: unavailable")
        print(f"  Reason: {exc}")
        print("  Real input: False")
        print("  Submit: False")
        return
    print("osu! Aim Status")
    print(f"  Decision: {status['decision']}")
    print(f"  Source: {status['source']}")
    print(f"  Status: {status['status']}")
    print(f"  Reason: {status['reason']}")
    print(f"  Real input: {status['real_input']}")
    print(f"  Submit: {status['submit']}")


def print_osu_vts_reaction_preview(text: str = "") -> None:
    try:
        options = _parse_aim_status_flags(text)
        source_path = _resolve_aim_timeline_path(options.get("path") if isinstance(options.get("path"), str) else None)
        preview = read_vts_reaction_preview(source_path=source_path, stale_ms=int(options["stale_ms"]))
    except ValueError as exc:
        print("osu! VTS Reaction Preview")
        print("  Decision: reaction_preview_only_no_vts")
        print("  Source: invalid")
        print("  Aim status: unavailable")
        print("  Reaction: offline")
        print(f"  Reason: {exc}")
        print("  VTS call: False")
        print("  Real input: False")
        print("  Submit: False")
        return
    print("osu! VTS Reaction Preview")
    print(f"  Decision: {preview['decision']}")
    print(f"  Source: {preview['source']}")
    print(f"  Aim status: {preview['aim_status']}")
    print(f"  Reaction: {preview['reaction']}")
    print(f"  Reason: {preview['reason']}")
    print(f"  VTS call: {preview['vts_call']}")
    print("  Real input: False")
    print("  Submit: False")


def print_osu_stream_persona_preview(text: str = "") -> None:
    try:
        options = _parse_aim_status_flags(text)
        timeline_path = _resolve_aim_timeline_path(options.get("path") if isinstance(options.get("path"), str) else None)
        metadata_path = _resolve_osu_state_metadata_path(
            options.get("metadata_path") if isinstance(options.get("metadata_path"), str) else None
        )
        preview = build_stream_persona_preview(
            source_path=timeline_path,
            stale_ms=int(options["stale_ms"]),
            metadata_path=metadata_path,
        )
    except ValueError as exc:
        print("osu! Stream Persona Preview")
        print("  Decision: stream_persona_preview_only_no_output")
        print("  osu_status: unavailable")
        print("  reaction: offline")
        print("  song: unknown")
        print("  difficulty: unknown")
        print("  Safe hint: waiting for data")
        print(f"  Line: preview-only unavailable ({exc})")
        print("  Stream mode: quiet_waiting")
        print("  Speak allowed: False")
        print("  Subtitle allowed: False")
        print("  Voice allowed: False")
        print("  Silence reason: unavailable_state_quiet_waiting")
        print("  Private data: filtered")
        print("  Voice call: False")
        print("  VTS call: False")
        print("  Real input: False")
        print("  Submit: False")
        return
    event = preview["public_event"]
    print("osu! Stream Persona Preview")
    print(f"  Decision: {preview['decision']}")
    print(f"  osu_status: {event['osu_status']}")
    print(f"  reaction: {event['reaction']}")
    print(f"  song: {event['song_title']}")
    print(f"  difficulty: {event['difficulty']}")
    print(f"  Safe hint: {event['safe_hint']}")
    print(f"  Line: {preview['line']}")
    print(f"  Stream mode: {preview['stream_mode']}")
    print(f"  Speak allowed: {preview['speak_allowed']}")
    print(f"  Subtitle allowed: {preview['subtitle_allowed']}")
    print(f"  Voice allowed: {preview['voice_allowed']}")
    print(f"  Silence reason: {preview['gate_reason']}")
    print(f"  Private data: {preview['private_data']}")
    print(f"  Voice call: {preview['voice_call']}")
    print(f"  VTS call: {preview['vts_call']}")
    print(f"  Real input: {preview['real_input']}")
    print(f"  Submit: {preview['submit']}")


def print_osu_core_event_preview(text: str = "") -> None:
    try:
        options = _parse_aim_status_flags(text)
        timeline_path = _resolve_aim_timeline_path(options.get("path") if isinstance(options.get("path"), str) else None)
        metadata_path = _resolve_osu_state_metadata_path(
            options.get("metadata_path") if isinstance(options.get("metadata_path"), str) else None
        )

        # Gather stream persona data
        persona_preview = build_stream_persona_preview(
            source_path=timeline_path,
            stale_ms=int(options["stale_ms"]),
            metadata_path=metadata_path,
        )

        # Build safe event from persona public_event + stream_mode/speak gates
        event = persona_preview["public_event"]
        safe_event = {
            "game": "osu",
            "activity_state": "gameplay",
            "stream_mode": persona_preview["stream_mode"],
            "speak_allowed": persona_preview["speak_allowed"],
            "subtitle_allowed": persona_preview["subtitle_allowed"],
            "voice_allowed": persona_preview["voice_allowed"],
            "song_title": event.get("song_title"),
            "difficulty": event.get("difficulty"),
            "artist": event.get("artist"),
            "reaction": event.get("reaction"),
            "safe_hint": event.get("safe_hint"),
            "osu_status": event.get("osu_status"),
        }

        core_preview = ingest_stream_event_preview(safe_event)

    except ValueError:
        print("osu! Core Event Preview")
        print("  Decision: core_event_preview_only_no_output")
        print("  Source game: unknown")
        print("  Activity state: unknown")
        print("  Stream mode: quiet_waiting")
        print("  Speak allowed: False")
        print("  Received: False")
        print("  Performance tags: []")
        print("  Line candidate: ")
        print("  Private data: filtered")
        print("  Voice call: False")
        print("  VTS call: False")
        print("  Real input: False")
        print("  Submit: False")
        return

    print("osu! Core Event Preview")
    print(f"  Decision: {core_preview['decision']}")
    print(f"  Source game: {core_preview['source_game']}")
    print(f"  Activity state: {core_preview['activity_state']}")
    print(f"  Stream mode: {core_preview['stream_mode']}")
    print(f"  Speak allowed: {core_preview['speak_allowed']}")
    print(f"  Received: {core_preview['received']}")
    print(f"  song: {safe_event['song_title']}")
    print(f"  difficulty: {safe_event['difficulty']}")
    print(f"  reaction: {safe_event['reaction']}")
    tags = core_preview["performance_tags"]
    print(f"  Performance tags: {tags if tags else []}")
    line_candidate = core_preview.get("line_candidate") or ""
    print(f"  Line candidate: {line_candidate}")
    print(f"  Private data: {core_preview['private_data']}")
    print(f"  Voice call: {core_preview['voice_call']}")
    print(f"  VTS call: {core_preview['vts_call']}")
    print(f"  Real input: {core_preview['real_input']}")
    print(f"  Submit: {core_preview['submit']}")


def print_osu_core_result_preview(text: str = "") -> None:
    """
    Preview osu! core decision for result/menu/break events.

    Supports manual result fields:
      --score=<int>
      --accuracy=<float>
      --misses=<int>
      --combo=<int>
      --max-combo=<int>
      --rank=<S/A/B/C/D/F>
      --passed=true|false

    Behavior:
    - result/menu/break with result data: stream_mode=talk_ready, speak_allowed=True
    - result/menu/break without data: stream_mode=quiet_waiting, speak_allowed=False
    - Always preview only; no voice/VTS/input/submit.
    """
    # Parse result-specific flags
    result_fields = _parse_result_flags(text)

    # Determine activity_state from flags
    activity_state = "result"
    for token in str(text or "").split()[1:]:
        if token.startswith("--activity-state="):
            activity_state = token.split("=", 1)[1].strip()
            break

    try:
        # Gather optional metadata from timeline/state if available
        timeline_path = DEFAULT_AIM_TIMELINE_PATH
        metadata_path = DEFAULT_OSU_STATE_METADATA_PATH

        # Try to get song metadata
        song_title = "unknown"
        difficulty = "unknown"
        artist = "unknown"
        try:
            from nana.game.osu.stream_persona import _resolve_song_metadata
            title, diff, art = _resolve_song_metadata(metadata_path)
            if title != "unknown":
                song_title = title
            if diff != "unknown":
                difficulty = diff
            if art != "unknown":
                artist = art
        except Exception:
            pass

        # Determine stream_mode based on whether we have result data
        has_result_data = bool(result_fields)
        result_like_state = activity_state in {"result", "menu", "break"}

        if result_like_state and has_result_data:
            stream_mode = "talk_ready"
            speak_allowed = True
            subtitle_allowed = True
        elif result_like_state and not has_result_data:
            stream_mode = "quiet_waiting"
            speak_allowed = False
            subtitle_allowed = False
        else:
            # Fallback: gameplay or other states use persona path
            stream_mode = "silent_play"
            speak_allowed = False
            subtitle_allowed = False

        # Build safe event
        safe_event: dict[str, Any] = {
            "game": "osu",
            "activity_state": activity_state,
            "stream_mode": stream_mode,
            "speak_allowed": speak_allowed,
            "subtitle_allowed": subtitle_allowed,
            "voice_allowed": False,
            "song_title": song_title,
            "difficulty": difficulty,
            "artist": artist,
        }

        # Inject result fields if provided
        for key in ("score", "accuracy", "misses", "combo", "max_combo", "rank", "passed"):
            if key in result_fields:
                safe_event[key] = result_fields[key]

        # Inject into core ingest
        core_preview = ingest_stream_event_preview(safe_event)

    except ValueError:
        print("osu! Core Result Preview")
        print("  Decision: core_result_event_preview_only_no_output")
        print("  Source game: unknown")
        print("  Activity state: unknown")
        print("  Stream mode: quiet_waiting")
        print("  Speak allowed: False")
        print("  Received: False")
        print("  Performance tags: []")
        print("  Line candidate: ")
        print("  Private data: filtered")
        print("  Voice call: False")
        print("  VTS call: False")
        print("  Real input: False")
        print("  Submit: False")
        return

    print("osu! Core Result Preview")
    print(f"  Decision: core_result_event_preview_only_no_output")
    print(f"  Source game: {core_preview['source_game']}")
    print(f"  Activity state: {core_preview['activity_state']}")
    print(f"  Stream mode: {core_preview['stream_mode']}")
    print(f"  Speak allowed: {core_preview['speak_allowed']}")
    print(f"  Received: {core_preview['received']}")

    # Result-specific fields
    score = result_fields.get("score")
    accuracy = result_fields.get("accuracy")
    misses = result_fields.get("misses")
    combo = result_fields.get("combo")
    max_combo = result_fields.get("max_combo")
    rank = result_fields.get("rank")
    passed = result_fields.get("passed")

    print(f"  song: {song_title}")
    print(f"  difficulty: {difficulty}")
    if score is not None:
        print(f"  score: {score}")
    if accuracy is not None:
        print(f"  accuracy: {accuracy}")
    if misses is not None:
        print(f"  misses: {misses}")
    if combo is not None:
        print(f"  combo: {combo}")
    if max_combo is not None:
        print(f"  max_combo: {max_combo}")
    if rank is not None:
        print(f"  rank: {rank}")
    if passed is not None:
        print(f"  passed: {passed}")

    tags = core_preview["performance_tags"]
    print(f"  Performance tags: {tags if tags else []}")

    line_candidate = core_preview.get("line_candidate") or ""
    print(f"  Line candidate: {line_candidate}")
    print(f"  Response intent: review_result")
    print(f"  Private data: {core_preview['private_data']}")
    print(f"  Voice call: {core_preview['voice_call']}")
    print(f"  VTS call: {core_preview['vts_call']}")
    print(f"  Real input: {core_preview['real_input']}")
    print(f"  Submit: {core_preview['submit']}")


def print_osu_companion_response_preview(text: str = "") -> None:
    """
    Preview companion-level response for osu! result/menu/break states.

    Supports the same flags as /osu-core-result-preview:
      --score=<int>
      --accuracy=<float>
      --misses=<int>
      --combo=<int>
      --max-combo=<int>
      --rank=<S/A/B/C/D/F>
      --passed=true|false
      --activity-state=<result/menu/break>

    Behavior:
    - Reuses /osu-core-result-preview parsing to avoid duplication.
    - Passes core preview to build_stream_response_preview.
    - Returns companion-level response preview with deterministic template line.
    - No voice, no VTS, no ElevenLabs, no OBS, no real input, no submit.
    """
    # Parse result-specific flags (same as /osu-core-result-preview)
    result_fields = _parse_result_flags(text)

    # Determine activity_state from flags
    activity_state = "result"
    for token in str(text or "").split()[1:]:
        if token.startswith("--activity-state="):
            activity_state = token.split("=", 1)[1].strip()
            break

    # Try to get optional metadata from state
    song_title = "unknown"
    difficulty = "unknown"
    try:
        from nana.game.osu.stream_persona import _resolve_song_metadata
        title, diff, _ = _resolve_song_metadata(DEFAULT_OSU_STATE_METADATA_PATH)
        if title != "unknown":
            song_title = title
        if diff != "unknown":
            difficulty = diff
    except Exception:
        pass

    # Determine stream mode
    has_result_data = bool(result_fields)
    result_like_state = activity_state in {"result", "menu", "break"}

    if result_like_state and has_result_data:
        stream_mode = "talk_ready"
        speak_allowed = True
    elif result_like_state and not has_result_data:
        stream_mode = "quiet_waiting"
        speak_allowed = False
    else:
        stream_mode = "silent_play"
        speak_allowed = False

    # Build core event preview (matches stream_event_core ingest)
    safe_event: dict[str, Any] = {
        "game": "osu",
        "activity_state": activity_state,
        "stream_mode": stream_mode,
        "speak_allowed": speak_allowed,
        "song_title": song_title,
        "difficulty": difficulty,
    }
    for key in ("score", "accuracy", "misses", "combo", "max_combo", "rank", "passed"):
        if key in result_fields:
            safe_event[key] = result_fields[key]

    # Ingest into core
    core_preview = ingest_stream_event_preview(safe_event)

    # Build companion response preview
    companion_preview = build_stream_response_preview(core_preview)

    # Print output
    print("osu! Companion Response Preview")
    print(f"  Decision: companion_response_preview_only_no_output")
    print(f"  Source game: {companion_preview['source_game']}")
    print(f"  Activity state: {companion_preview['activity_state']}")
    print(f"  Stream mode: {companion_preview['stream_mode']}")
    print(f"  Speak allowed: {companion_preview['speak_allowed']}")
    print(f"  Response intent: {companion_preview['response_intent']}")
    print(f"  Response style: {companion_preview['response_style']}")

    # Result-specific fields (same as core result preview)
    score = result_fields.get("score")
    accuracy = result_fields.get("accuracy")
    misses = result_fields.get("misses")
    combo = result_fields.get("combo")
    max_combo = result_fields.get("max_combo")
    rank = result_fields.get("rank")
    passed = result_fields.get("passed")
    if score is not None:
        print(f"  score: {score}")
    if accuracy is not None:
        print(f"  accuracy: {accuracy}")
    if misses is not None:
        print(f"  misses: {misses}")
    if combo is not None:
        print(f"  combo: {combo}")
    if max_combo is not None:
        print(f"  max_combo: {max_combo}")
    if rank is not None:
        print(f"  rank: {rank}")
    if passed is not None:
        print(f"  passed: {passed}")

    tags = companion_preview["performance_tags"]
    print(f"  Performance tags: {tags if tags else []}")
    print(f"  Line candidate: {companion_preview['line_candidate']}")
    print(f"  Private data: {companion_preview['private_data']}")
    print(f"  Voice call: {companion_preview['voice_call']}")
    print(f"  VTS call: {companion_preview['vts_call']}")
    print(f"  Subtitle call: {companion_preview['subtitle_call']}")
    print(f"  OBS call: {companion_preview['obs_call']}")
    print(f"  Real input: {companion_preview['real_input']}")
    print(f"  Submit: {companion_preview['submit']}")


def print_osu_public_subtitle_preview(text: str = "") -> None:
    """
    Preview public subtitle output for osu! result/menu/break states.

    Supports the same flags as /osu-companion-response-preview:
      --score=<int>
      --accuracy=<float>
      --misses=<int>
      --combo=<int>
      --max-combo=<int>
      --rank=<S/A/B/C/D/F>
      --passed=true|false
      --activity-state=<result/menu/break>

    Behavior:
    - Reuses /osu-companion-response-preview parsing to avoid duplication.
    - Builds companion response preview, then builds public subtitle payload.
    - No file write. No voice, no VTS, no ElevenLabs, no OBS API, no real input, no submit.
    - Decision: public_subtitle_preview_only_no_write
    """
    # Parse result-specific flags (same as /osu-companion-response-preview)
    result_fields = _parse_result_flags(text)

    # Determine activity_state from flags
    activity_state = "result"
    for token in str(text or "").split()[1:]:
        if token.startswith("--activity-state="):
            activity_state = token.split("=", 1)[1].strip()
            break

    # Try to get optional metadata from state
    song_title = "unknown"
    difficulty = "unknown"
    try:
        from nana.game.osu.stream_persona import _resolve_song_metadata
        title, diff, _ = _resolve_song_metadata(DEFAULT_OSU_STATE_METADATA_PATH)
        if title != "unknown":
            song_title = title
        if diff != "unknown":
            difficulty = diff
    except Exception:
        pass

    # Determine stream mode
    has_result_data = bool(result_fields)
    result_like_state = activity_state in {"result", "menu", "break"}

    if result_like_state and has_result_data:
        stream_mode = "talk_ready"
        speak_allowed = True
    elif result_like_state and not has_result_data:
        stream_mode = "quiet_waiting"
        speak_allowed = False
    else:
        stream_mode = "silent_play"
        speak_allowed = False

    # Build core event preview
    safe_event: dict[str, Any] = {
        "game": "osu",
        "activity_state": activity_state,
        "stream_mode": stream_mode,
        "speak_allowed": speak_allowed,
        "song_title": song_title,
        "difficulty": difficulty,
    }
    for key in ("score", "accuracy", "misses", "combo", "max_combo", "rank", "passed"):
        if key in result_fields:
            safe_event[key] = result_fields[key]

    # Ingest into core
    core_preview = ingest_stream_event_preview(safe_event)

    # Build companion response preview
    companion_preview = build_stream_response_preview(core_preview)

    # Build public subtitle payload
    payload = build_public_subtitle_payload(companion_preview)

    # Print output
    print("osu! Public Subtitle Preview")
    print(f"  Decision: {payload['decision']}")
    print(f"  Source game: {payload['source_game']}")
    print(f"  Activity state: {payload['activity_state']}")
    print(f"  Stream mode: {payload['stream_mode']}")
    print(f"  Speak allowed: {payload['speak_allowed']}")

    # Result-specific fields (same as companion response preview)
    score = result_fields.get("score")
    accuracy = result_fields.get("accuracy")
    misses = result_fields.get("misses")
    combo = result_fields.get("combo")
    max_combo = result_fields.get("max_combo")
    rank = result_fields.get("rank")
    passed = result_fields.get("passed")
    if score is not None:
        print(f"  score: {score}")
    if accuracy is not None:
        print(f"  accuracy: {accuracy}")
    if misses is not None:
        print(f"  misses: {misses}")
    if combo is not None:
        print(f"  combo: {combo}")
    if max_combo is not None:
        print(f"  max_combo: {max_combo}")
    if rank is not None:
        print(f"  rank: {rank}")
    if passed is not None:
        print(f"  passed: {passed}")

    print(f"  Line: {payload['line']}")
    print(f"  Output path: {payload['output_path']}")
    print(f"  File write: {payload['file_write']}")
    print(f"  Subtitle call: {payload['subtitle_call']}")
    print(f"  Voice call: {payload['voice_call']}")
    print(f"  VTS call: {payload['vts_call']}")
    print(f"  OBS call: {payload['obs_call']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")


def _parse_approval_token(text: str) -> str | None:
    """Parse --operator-approval-token from command text."""
    for token in str(text or "").split()[1:]:
        if token.startswith("--operator-approval-token="):
            return token.split("=", 1)[1].strip()
    return None


def print_osu_public_subtitle_write(text: str = "") -> None:
    """
    Gated write of public subtitle to local text file.

    Supports the same flags as /osu-companion-response-preview:
      --score=<int>
      --accuracy=<float>
      --misses=<int>
      --combo=<int>
      --max-combo=<int>
      --rank=<S/A/B/C/D/F>
      --passed=true|false
      --activity-state=<result/menu/break>
      --operator-approval-token=I_APPROVE_OSU_PUBLIC_SUBTITLE_WRITE

    Gates:
    - env NANA_OSU_PUBLIC_SUBTITLE_WRITE_ENABLED=1
    - operator approval token matches exactly
    - stream_mode == talk_ready
    - speak_allowed == True
    - line_candidate non-empty

    If any gate fails: Decision=public_subtitle_hold, File write=False.
    If all gates pass: Decision=public_subtitle_file_written, File write=True.

    No voice, no VTS, no ElevenLabs, no OBS API, no real input, no submit.
    """
    APPROVED_TOKEN = "I_APPROVE_OSU_PUBLIC_SUBTITLE_WRITE"

    # Parse result-specific flags (same as /osu-companion-response-preview)
    result_fields = _parse_result_flags(text)

    # Determine activity_state from flags
    activity_state = "result"
    for token in str(text or "").split()[1:]:
        if token.startswith("--activity-state="):
            activity_state = token.split("=", 1)[1].strip()
            break

    # Parse optional path override
    output_path_override: str | None = None
    for token in str(text or "").split()[1:]:
        if token.startswith("--path="):
            output_path_override = token.split("=", 1)[1].strip()
            break

    # Parse operator approval token
    operator_token = _parse_approval_token(text)
    token_ok = operator_token == APPROVED_TOKEN

    # Try to get optional metadata from state
    song_title = "unknown"
    difficulty = "unknown"
    try:
        from nana.game.osu.stream_persona import _resolve_song_metadata
        title, diff, _ = _resolve_song_metadata(DEFAULT_OSU_STATE_METADATA_PATH)
        if title != "unknown":
            song_title = title
        if diff != "unknown":
            difficulty = diff
    except Exception:
        pass

    # Determine stream mode
    has_result_data = bool(result_fields)
    result_like_state = activity_state in {"result", "menu", "break"}

    if result_like_state and has_result_data:
        stream_mode = "talk_ready"
        speak_allowed = True
    elif result_like_state and not has_result_data:
        stream_mode = "quiet_waiting"
        speak_allowed = False
    else:
        stream_mode = "silent_play"
        speak_allowed = False

    # Build core event preview
    safe_event: dict[str, Any] = {
        "game": "osu",
        "activity_state": activity_state,
        "stream_mode": stream_mode,
        "speak_allowed": speak_allowed,
        "song_title": song_title,
        "difficulty": difficulty,
    }
    for key in ("score", "accuracy", "misses", "combo", "max_combo", "rank", "passed"):
        if key in result_fields:
            safe_event[key] = result_fields[key]

    # Ingest into core
    core_preview = ingest_stream_event_preview(safe_event)

    # Build companion response preview
    companion_preview = build_stream_response_preview(core_preview)

    # Build public subtitle payload
    payload = build_public_subtitle_payload(
        companion_preview,
        output_path=output_path_override,
    )

    # Add token gate flag to payload
    payload["_token_gated"] = token_ok

    # Write file (gate checked inside)
    result = write_public_subtitle_file(payload)

    # Print output
    print("osu! Public Subtitle Write")
    print(f"  Decision: {result['decision']}")
    if result.get("hold_reason"):
        print(f"  Hold reason: {result['hold_reason']}")
    if result.get("output_path"):
        print(f"  Output path: {result['output_path']}")
    if result.get("bytes_written") is not None:
        print(f"  Bytes written: {result['bytes_written']}")
    print(f"  File write: {result['file_write']}")
    print(f"  Subtitle call: {result['subtitle_call']}")
    print(f"  Voice call: {result['voice_call']}")
    print(f"  VTS call: {result['vts_call']}")
    print(f"  OBS call: {result['obs_call']}")
    print(f"  Real input: {result['real_input']}")
    print(f"  Submit: {result['submit']}")


def _parse_path_flag(text: str) -> str | None:
    """Parse --path=<path> from command text."""
    for token in str(text or "").split()[1:]:
        if token.startswith("--path="):
            return token.split("=", 1)[1].strip()
    return None


def _parse_stale_ms_flag(text: str, default: int = 10000) -> int:
    """Parse --stale-ms=<ms> from command text."""
    for token in str(text or "").split()[1:]:
        if token.startswith("--stale-ms="):
            return int(token.split("=", 1)[1].strip())
    return default


def print_osu_public_subtitle_status(text: str = "") -> None:
    """
    Read-only status of public subtitle file for OBS Text Source.

    Supports:
      --path=<path>           optional override path
      --stale-ms=<ms>        stale threshold, default 10000

    No file write. No voice, no VTS, no ElevenLabs, no OBS API, no real input, no submit.
    """
    override_path = _parse_path_flag(text)
    stale_ms = _parse_stale_ms_flag(text, default=10000)
    status = read_public_subtitle_status(path=override_path, stale_ms=stale_ms)

    print("osu! Public Subtitle Status")
    print(f"  Decision: {status['decision']}")
    print(f"  Path: {status['path']}")
    print(f"  Exists: {status['exists']}")
    print(f"  Bytes: {status['bytes']}")
    print(f"  Age ms: {status['age_ms']}")
    print(f"  Fresh: {status['fresh']}")
    print(f"  Stale threshold ms: {status['stale_threshold_ms']}")
    print(f"  Line: {status['line']}")
    print(f"  File write: {status['file_write']}")
    print(f"  Subtitle call: {status['subtitle_call']}")
    print(f"  Voice call: {status['voice_call']}")
    print(f"  VTS call: {status['vts_call']}")
    print(f"  OBS call: {status['obs_call']}")
    print(f"  Real input: {status['real_input']}")
    print(f"  Submit: {status['submit']}")


def print_osu_public_subtitle_obs_guide() -> None:
    """
    Print OBS setup guide for public subtitle text file source.

    Read only. No OBS API. No file write. No voice, no VTS, no ElevenLabs, no real input, no submit.
    """
    print("osu! Public Subtitle OBS Guide")
    print("  Decision: public_subtitle_obs_guide_only_no_api")
    print("  1. OBS -> Add Text source (or use existing)")
    print("  2. Enable 'Read from file' checkbox")
    print("  3. Select nana\\runtime\\data\\public_subtitle.txt from the repository root")
    print("  4. Set font, size, color in OBS source properties")
    print("  5. Nana writes only when /osu-public-subtitle-write succeeds with valid gates")
    print(f"  OBS call: False")
    print(f"  File write: False")
    print(f"  Real input: False")
    print(f"  Submit: False")


def print_osu_public_subtitle_clear(text: str = "") -> None:
    """
    Clear the public subtitle file by writing an empty string.

    Gates:
    - env NANA_OSU_PUBLIC_SUBTITLE_CLEAR_ENABLED=1
    - operator approval token matches exactly: I_APPROVE_OSU_PUBLIC_SUBTITLE_CLEAR

    Supports:
      --path=<path>
      --operator-approval-token=<TOKEN>

    If gates fail: Decision=public_subtitle_hold, File write=False.
    If gates pass: Decision=public_subtitle_cleared, File write=True, file is empty.

    No voice, no VTS, no ElevenLabs, no OBS API, no real input, no submit.
    """
    APPROVED_TOKEN = "I_APPROVE_OSU_PUBLIC_SUBTITLE_CLEAR"

    override_path = _parse_path_flag(text)
    operator_token = _parse_approval_token(text)
    token_ok = operator_token == APPROVED_TOKEN

    result = clear_public_subtitle_file(
        path=override_path,
        operator_approval_token=operator_token,
    )

    print("osu! Public Subtitle Clear")
    print(f"  Decision: {result['decision']}")
    if result.get("hold_reason"):
        print(f"  Hold reason: {result['hold_reason']}")
    print(f"  Path: {result['path']}")
    print(f"  File write: {result['file_write']}")
    if result.get("bytes_written") is not None:
        print(f"  Bytes written: {result['bytes_written']}")
    print(f"  Subtitle call: {result['subtitle_call']}")
    print(f"  Voice call: {result['voice_call']}")
    print(f"  VTS call: {result['vts_call']}")
    print(f"  OBS call: {result['obs_call']}")
    print(f"  Real input: {result['real_input']}")
    print(f"  Submit: {result['submit']}")


def print_osu_result_subtitle_pipeline_preview(text: str = "") -> None:
    """
    Preview result -> subtitle pipeline (no write).

    Supports same flags as /osu-companion-response-preview:
      --score=<int>
      --accuracy=<float>
      --misses=<int>
      --combo=<int>
      --max-combo=<int>
      --rank=<S/A/B/C/D/F>
      --passed=true|false
      --activity-state=<result/menu/break>
      --path=<path>

    Decision: result_subtitle_pipeline_preview_only_no_write
    No file write. No voice, no VTS, no ElevenLabs, no OBS API, no real input, no submit.
    """
    result = build_result_subtitle_pipeline_preview(text)

    print("osu! Result Subtitle Pipeline Preview")
    print(f"  Decision: {result['decision']}")
    print(f"  Source game: {result['source_game']}")
    print(f"  Activity state: {result['activity_state']}")
    print(f"  Stream mode: {result['stream_mode']}")
    print(f"  Speak allowed: {result['speak_allowed']}")
    print(f"  Response style: {result.get('response_style', 'unknown')}")
    print(f"  Line: {result['line']}")
    print(f"  Output path: {result['output_path']}")
    print(f"  File write: {result['file_write']}")
    print(f"  Pipeline write: {result['pipeline_write']}")
    print(f"  Subtitle call: {result['subtitle_call']}")
    print(f"  Voice call: {result['voice_call']}")
    print(f"  VTS call: {result['vts_call']}")
    print(f"  OBS call: {result['obs_call']}")
    print(f"  Real input: {result['real_input']}")
    print(f"  Submit: {result['submit']}")


def print_osu_result_subtitle_pipeline_write(text: str = "") -> None:
    """
    Gated write of result -> subtitle pipeline.

    Supports same flags as /osu-companion-response-preview:
      --score=<int>
      --accuracy=<float>
      --misses=<int>
      --combo=<int>
      --max-combo=<int>
      --rank=<S/A/B/C/D/F>
      --passed=true|false
      --activity-state=<result/menu/break>
      --path=<path>
      --operator-approval-token=I_APPROVE_OSU_RESULT_SUBTITLE_PIPELINE_WRITE

    Gates:
    - env NANA_OSU_RESULT_SUBTITLE_PIPELINE_WRITE_ENABLED=1
    - operator approval token matches exactly
    - stream_mode == talk_ready
    - speak_allowed == True
    - line non-empty

    If any gate fails: Decision=result_subtitle_pipeline_hold, File write=False.
    If all gates pass: Decision=result_subtitle_pipeline_file_written, File write=True.

    No voice, no VTS, no ElevenLabs, no OBS API, no real input, no submit.
    """
    result = build_result_subtitle_pipeline_write(text)

    print("osu! Result Subtitle Pipeline Write")
    print(f"  Decision: {result['decision']}")
    if result.get("hold_reason"):
        print(f"  Hold reason: {result['hold_reason']}")
    print(f"  Source game: {result['source_game']}")
    print(f"  Activity state: {result['activity_state']}")
    print(f"  Stream mode: {result['stream_mode']}")
    print(f"  Speak allowed: {result['speak_allowed']}")
    print(f"  Response style: {result.get('response_style', 'unknown')}")
    print(f"  Line: {result['line']}")
    print(f"  Output path: {result['output_path']}")
    if result.get("bytes_written") is not None:
        print(f"  Bytes written: {result['bytes_written']}")
    print(f"  File write: {result['file_write']}")
    print(f"  Pipeline write: {result['pipeline_write']}")
    print(f"  Subtitle call: {result['subtitle_call']}")
    print(f"  Voice call: {result['voice_call']}")
    print(f"  VTS call: {result['vts_call']}")
    print(f"  OBS call: {result['obs_call']}")
    print(f"  Real input: {result['real_input']}")
    print(f"  Submit: {result['submit']}")


def print_osu_result_source_status(text: str = "") -> None:
    """
    Read-only status of osu! result source from bridge.

    Tries to extract result fields from the existing tosu bridge state.
    Does NOT write file. No voice, no VTS, no ElevenLabs, no OBS API, no real input, no submit.
    """
    try:
        snapshot = read_result_source_snapshot()
        output = build_result_source_status_output(snapshot)
    except Exception:
        # Fail gracefully — show unavailability
        print("osu! Result Source Status")
        print("  Decision: result_source_status_only_no_write")
        print("  Source: tosu")
        print("  Available: False")
        print("  Unavailable reason: bridge_read_error")
        print("  File write: False")
        print("  Pipeline write: False")
        print("  Voice call: False")
        print("  VTS call: False")
        print("  OBS call: False")
        print("  Real input: False")
        print("  Submit: False")
        return

    print(output)


def _stage_output_path_from_flags(flags: dict[str, Any]) -> Path | None:
    raw_path = flags.get("path")
    if not raw_path:
        return None
    return Path(str(raw_path))


def _quick_stage_output_path_from_flags(text: str) -> Path | None:
    """Parse --path from quick stage command text."""
    for token in str(text or "").split()[1:]:
        if token.startswith("--path="):
            return Path(token.split("=", 1)[1].strip())
    return None


def print_osu_result_stage_quick(text: str = "") -> None:
    """
    Quick stage: minimal flags required.

    Minimal flags:
      --accuracy=<float>
      --misses=<int>
      --rank=<S/A/B/C/D/F>

    Optional:
      --score=<int>
      --combo=<int>
      --max-combo=<int>
      --passed=true|false

    Defaults:
    - passed=true unless rank=F or --passed=false
    - score=0 if omitted
    - combo/max_combo omitted if not provided
    - activity_state=result

    Writes staged_result.json via write_staged_result().
    No voice, no VTS, no ElevenLabs, no OBS API, no real input, no submit.
    """
    try:
        result_fields = build_quick_stage_fields(text, preset=None)
        result = write_staged_result(
            result_fields,
            activity_state="result",
            output_path=_quick_stage_output_path_from_flags(text),
        )
    except Exception as exc:
        result = {
            "decision": "result_stage_write_error",
            "path": "",
            "written": False,
            "error": repr(exc),
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "voice_call": False,
        }
    print_quick_stage_result(result, preset=None)
    if result.get("error"):
        print(f"  Error: {result['error']}")


def print_osu_result_stage_preset(text: str = "", preset: str = "strong") -> None:
    """
    Preset stage: strong / weak / fail.

    Each preset sets fixed defaults, with optional overrides:
      --score=<int>
      --combo=<int>
      --max-combo=<int>

    Presets:
    - strong: accuracy=99.2, misses=0, rank=S, passed=true
    - weak: accuracy=85.0, misses=50, rank=C, passed=true
    - fail: accuracy=50.0, misses=200, rank=F, passed=false

    Writes staged_result.json via write_staged_result().
    No voice, no VTS, no ElevenLabs, no OBS API, no real input, no submit.
    """
    try:
        result_fields = build_quick_stage_fields(text, preset=preset)
        result = write_staged_result(
            result_fields,
            activity_state="result",
            output_path=_quick_stage_output_path_from_flags(text),
        )
    except Exception as exc:
        result = {
            "decision": "result_stage_write_error",
            "path": "",
            "written": False,
            "error": repr(exc),
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "voice_call": False,
        }
    print_quick_stage_result(result, preset=preset)
    if result.get("error"):
        print(f"  Error: {result['error']}")


def print_osu_result_stage(text: str = "") -> None:
    """
    Stage a manual osu! result into a local JSON file.

    This only writes the staged-result state file. It does not write subtitles,
    call VTS/OBS/voice, move/click, submit, or send osu input.
    """
    try:
        flags = parse_stage_flags(text)
        result_keys = {"score", "accuracy", "misses", "combo", "max_combo", "rank", "passed"}
        result_fields = {key: flags[key] for key in result_keys if key in flags}
        result = write_staged_result(
            result_fields,
            activity_state=str(flags.get("activity_state") or "result"),
            output_path=_stage_output_path_from_flags(flags),
        )
    except Exception as exc:
        result = {
            "decision": "result_stage_write_error",
            "path": "",
            "written": False,
            "error": repr(exc),
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "voice_call": False,
        }
    print_stage_result_write(result)
    if result.get("error"):
        print(f"  Error: {result['error']}")


def print_osu_result_stage_status(text: str = "") -> None:
    """
    Read-only status of the staged manual result file.
    """
    try:
        flags = parse_stage_flags(text)
        stale_ms = int(flags.get("stale_ms") or DEFAULT_RESULT_STAGE_STALE_MS)
        status = read_staged_result(
            output_path=_stage_output_path_from_flags(flags),
            stale_ms=stale_ms,
        )
    except Exception as exc:
        status = {
            "exists": False,
            "fresh": False,
            "stale": False,
            "stale_reason": f"stage_status_error:{type(exc).__name__}",
            "path": "",
            "age_ms": None,
            "written_at": None,
            "activity_state": None,
            "source": None,
            "result_fields": None,
            "song": None,
            "difficulty": None,
            "schema_valid": False,
            "real_input": False,
            "submit": False,
        }
    print_stage_result_status(status)


def print_osu_result_stage_clear(text: str = "") -> None:
    """
    Clear staged manual result file behind env+token gates.
    """
    flags = parse_stage_flags(text)
    token = _parse_auto_token(text)
    result = clear_staged_result(
        operator_approval_token=token,
        output_path=_stage_output_path_from_flags(flags),
    )
    print_stage_result_clear(result)


_MANUAL_RESULT_KEYS = {"score", "accuracy", "misses", "combo", "max_combo", "rank", "passed"}
_LIFECYCLE_WRITE_ENV = "NANA_OSU_RESULT_LIFECYCLE_WRITE_ENABLED"
_LIFECYCLE_WRITE_TOKEN = "I_APPROVE_OSU_RESULT_LIFECYCLE_WRITE"
_LIFECYCLE_CLEAR_ENV = "NANA_OSU_RESULT_LIFECYCLE_CLEAR_ENABLED"
_LIFECYCLE_CLEAR_TOKEN = "I_APPROVE_OSU_RESULT_LIFECYCLE_CLEAR"


def _parse_stage_path_flag(text: str) -> Path | None:
    for token in str(text or "").split()[1:]:
        if token.startswith(("--stage-path=", "--staged-path=", "--stage-result-path=")):
            return Path(token.split("=", 1)[1].strip())
    return None


def _parse_subtitle_path_flag(text: str, manual_flags: dict[str, Any] | None = None) -> str | None:
    for token in str(text or "").split()[1:]:
        if token.startswith(("--subtitle-path=", "--public-subtitle-path=")):
            return token.split("=", 1)[1].strip()
    if manual_flags and manual_flags.get("path"):
        return str(manual_flags["path"])
    return _parse_path_flag(text)


def _parse_stage_stale_ms_flag(text: str, default: int = DEFAULT_RESULT_STAGE_STALE_MS) -> int:
    for token in str(text or "").split()[1:]:
        if token.startswith("--stage-stale-ms="):
            return int(token.split("=", 1)[1].strip())
    return _parse_stale_ms_flag(text, default=default)


def _parse_subtitle_stale_ms_flag(text: str, default: int = 10000) -> int:
    for token in str(text or "").split()[1:]:
        if token.startswith("--subtitle-stale-ms="):
            return int(token.split("=", 1)[1].strip())
    return _parse_stale_ms_flag(text, default=default)


def _env_gate_enabled(env_name: str) -> bool:
    return os.environ.get(env_name, "").strip() in {"1", "true", "yes", "on"}


def _build_osu_result_subtitle_context(
    text: str = "",
    *,
    include_bridge: bool,
    preview_decision: str,
) -> dict[str, Any]:
    manual_flags = parse_manual_result_flags(text)
    manual_has_result = any(key in manual_flags for key in _MANUAL_RESULT_KEYS)
    stage_path = _parse_stage_path_flag(text)
    stage_stale_ms = _parse_stage_stale_ms_flag(text)

    try:
        staged_status = read_staged_result(output_path=stage_path, stale_ms=stage_stale_ms)
    except Exception:
        staged_status = {
            "exists": False,
            "fresh": False,
            "stale": False,
            "stale_reason": "stage_status_error",
            "path": str(stage_path or ""),
            "age_ms": None,
            "written_at": None,
            "activity_state": None,
            "source": None,
            "result_fields": None,
            "song": None,
            "difficulty": None,
            "schema_valid": False,
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "voice_call": False,
        }

    bridge_snapshot = None
    if include_bridge:
        try:
            bridge_snapshot = read_result_source_snapshot()
        except Exception:
            bridge_snapshot = None

    staged_snapshot = None
    if not manual_has_result:
        try:
            staged_snapshot = get_staged_result_snapshot(
                stale_ms=stage_stale_ms,
                output_path=stage_path,
            )
        except Exception:
            staged_snapshot = None

    merged = merge_result_sources(bridge_snapshot, manual_flags, staged_snapshot)
    activity_state = merged["activity_state"] or "result"
    merged["activity_state"] = activity_state
    result_fields = merged["result_fields"]
    has_result_data = merged["has_result_data"]
    result_like_state = activity_state in {"result", "menu", "break"}

    if result_like_state and has_result_data:
        stream_mode = "talk_ready"
        speak_allowed = True
    elif result_like_state and not has_result_data:
        stream_mode = "quiet_waiting"
        speak_allowed = False
    else:
        stream_mode = "silent_play"
        speak_allowed = False

    safe_event: dict[str, Any] = {
        "game": "osu",
        "activity_state": activity_state,
        "stream_mode": stream_mode,
        "speak_allowed": speak_allowed,
        "song_title": merged["song_title"],
        "difficulty": merged["difficulty"],
    }
    for key in ("score", "accuracy", "misses", "combo", "max_combo", "rank", "passed"):
        if key in result_fields:
            safe_event[key] = result_fields[key]

    core_preview = ingest_stream_event_preview(safe_event)
    companion_preview = build_stream_response_preview(core_preview)
    payload = build_public_subtitle_payload(
        companion_preview,
        output_path=_parse_subtitle_path_flag(text, manual_flags),
    )
    payload["decision"] = preview_decision
    payload["pipeline_write"] = False
    payload["response_intent"] = companion_preview.get("response_intent", "unknown")
    payload["response_style"] = companion_preview.get("response_style", "unknown")
    payload["performance_tags"] = list(companion_preview.get("performance_tags", ()))

    return {
        "manual_flags": manual_flags,
        "manual_has_result": manual_has_result,
        "stage_path": stage_path,
        "stage_stale_ms": stage_stale_ms,
        "staged_status": staged_status,
        "staged_snapshot": staged_snapshot,
        "bridge_snapshot": bridge_snapshot,
        "merged": merged,
        "core_preview": core_preview,
        "companion_preview": companion_preview,
        "payload": payload,
    }


def _lifecycle_preview_hold_reason(context: dict[str, Any]) -> str | None:
    merged = context["merged"]
    payload = context["payload"]
    if not merged.get("has_result_data"):
        return "missing_result_data"
    if payload.get("stream_mode") != "talk_ready":
        return "stream_mode_not_talk_ready"
    if not payload.get("speak_allowed"):
        return "speak_not_allowed"
    if not payload.get("line"):
        return "empty_line"
    return None


def _print_lifecycle_source_state(context: dict[str, Any]) -> None:
    merged = context["merged"]
    staged = context["staged_status"]
    print(f"  Result source: {merged['source']}")
    print(f"  Source available: {str(merged['source_available']).lower()}")
    print(f"  Stage path: {staged['path']}")
    print(f"  Staged exists: {str(staged['exists']).lower()}")
    print(f"  Staged fresh: {str(staged['fresh']).lower()}")
    print(f"  Staged stale: {str(staged['stale']).lower()}")
    if staged.get("stale_reason"):
        print(f"  Staged stale reason: {staged['stale_reason']}")
    if staged.get("source"):
        print(f"  Staged source: {staged['source']}")


def print_osu_result_lifecycle_preview(text: str = "") -> None:
    """
    Preview direct/staged osu! result through the companion subtitle pipeline.

    No bridge read, no file write, no voice/VTS/OBS/real input/submit.
    """
    context = _build_osu_result_subtitle_context(
        text,
        include_bridge=False,
        preview_decision="result_lifecycle_preview_only_no_write",
    )
    payload = context["payload"]
    hold_reason = _lifecycle_preview_hold_reason(context)
    decision = "result_lifecycle_preview_hold" if hold_reason else "result_lifecycle_preview_only_no_write"

    print("osu! Result Lifecycle Preview")
    print(f"  Decision: {decision}")
    if hold_reason:
        print(f"  Hold reason: {hold_reason}")
    _print_lifecycle_source_state(context)
    print(f"  Activity state: {payload['activity_state']}")
    print(f"  Stream mode: {payload['stream_mode']}")
    print(f"  Speak allowed: {str(payload['speak_allowed']).lower()}")
    print(f"  Response style: {payload['response_style']}")
    print(f"  Line: {payload['line']}")
    print(f"  Output path: {payload['output_path']}")
    print("  File write: False")
    print("  Pipeline write: False")
    print("  Voice call: False")
    print("  VTS call: False")
    print("  OBS call: False")
    print("  Real input: False")
    print("  Submit: False")


def print_osu_result_lifecycle_write(text: str = "") -> None:
    """
    Gated direct/staged result lifecycle write.

    Uses write_public_subtitle_file with lifecycle env/token gates.
    No bridge read, no voice/VTS/OBS/real input/submit.
    """
    context = _build_osu_result_subtitle_context(
        text,
        include_bridge=False,
        preview_decision="result_lifecycle_preview_only_no_write",
    )
    payload = dict(context["payload"])
    operator_token = _parse_auto_token(text)
    payload["_token_gated"] = operator_token == _LIFECYCLE_WRITE_TOKEN
    result = write_public_subtitle_file(
        payload,
        write_env_name=_LIFECYCLE_WRITE_ENV,
    )

    file_written = result.get("decision") == "public_subtitle_file_written"
    decision = "result_lifecycle_file_written" if file_written else "result_lifecycle_hold"
    pipeline_write = bool(result.get("file_write"))

    print("osu! Result Lifecycle Write")
    print(f"  Decision: {decision}")
    if result.get("hold_reason"):
        print(f"  Hold reason: {result['hold_reason']}")
    _print_lifecycle_source_state(context)
    print(f"  Activity state: {payload['activity_state']}")
    print(f"  Stream mode: {payload['stream_mode']}")
    print(f"  Speak allowed: {str(payload['speak_allowed']).lower()}")
    print(f"  Response style: {payload['response_style']}")
    print(f"  Line: {payload['line']}")
    print(f"  Output path: {result['output_path']}")
    if result.get("bytes_written") is not None:
        print(f"  Bytes written: {result['bytes_written']}")
    print(f"  File write: {str(bool(result['file_write'])).lower()}")
    print(f"  Pipeline write: {str(pipeline_write).lower()}")
    print("  Voice call: False")
    print("  VTS call: False")
    print("  OBS call: False")
    print("  Real input: False")
    print("  Submit: False")


def print_osu_result_lifecycle_clear(text: str = "") -> None:
    """
    Clear staged result and public subtitle under lifecycle env/token gates.
    """
    stage_path = _parse_stage_path_flag(text)
    subtitle_path = _parse_subtitle_path_flag(text, parse_manual_result_flags(text))
    operator_token = _parse_auto_token(text)

    stage_result = clear_staged_result(
        operator_approval_token=operator_token,
        output_path=stage_path,
        clear_env_name=_LIFECYCLE_CLEAR_ENV,
        approved_token=_LIFECYCLE_CLEAR_TOKEN,
    )
    subtitle_result = clear_public_subtitle_file(
        path=subtitle_path,
        operator_approval_token=operator_token,
        clear_env_name=_LIFECYCLE_CLEAR_ENV,
        approved_token=_LIFECYCLE_CLEAR_TOKEN,
    )

    cleared = bool(stage_result.get("cleared")) and bool(subtitle_result.get("file_write"))
    decision = "result_lifecycle_cleared" if cleared else "result_lifecycle_hold"
    hold_reason = stage_result.get("hold_reason") or subtitle_result.get("hold_reason")

    print("osu! Result Lifecycle Clear")
    print(f"  Decision: {decision}")
    if hold_reason:
        print(f"  Hold reason: {hold_reason}")
    print(f"  Stage path: {stage_result['path']}")
    print(f"  Stage cleared: {str(bool(stage_result.get('cleared'))).lower()}")
    print(f"  Subtitle path: {subtitle_result['path']}")
    print(f"  Subtitle cleared: {str(bool(subtitle_result.get('file_write'))).lower()}")
    if subtitle_result.get("bytes_written") is not None:
        print(f"  Subtitle bytes written: {subtitle_result['bytes_written']}")
    print(f"  File write: {str(cleared).lower()}")
    print("  Pipeline write: False")
    print("  Voice call: False")
    print("  VTS call: False")
    print("  OBS call: False")
    print("  Real input: False")
    print("  Submit: False")


def print_osu_result_lifecycle_status(text: str = "") -> None:
    """
    Combined read-only status for staged result and public subtitle.
    """
    stage_path = _parse_stage_path_flag(text)
    subtitle_path = _parse_subtitle_path_flag(text, parse_manual_result_flags(text))
    stage_stale_ms = _parse_stage_stale_ms_flag(text)
    subtitle_stale_ms = _parse_subtitle_stale_ms_flag(text)
    stage_status = read_staged_result(output_path=stage_path, stale_ms=stage_stale_ms)
    subtitle_status = read_public_subtitle_status(path=subtitle_path, stale_ms=subtitle_stale_ms)

    print("osu! Result Lifecycle Status")
    print("  Decision: result_lifecycle_status_only_no_write")
    print(f"  Stage path: {stage_status['path']}")
    print(f"  Staged exists: {str(stage_status['exists']).lower()}")
    print(f"  Staged fresh: {str(stage_status['fresh']).lower()}")
    print(f"  Staged stale: {str(stage_status['stale']).lower()}")
    if stage_status.get("stale_reason"):
        print(f"  Staged stale reason: {stage_status['stale_reason']}")
    if stage_status.get("source"):
        print(f"  Staged source: {stage_status['source']}")
    fields = stage_status.get("result_fields") or {}
    for key in ("score", "accuracy", "misses", "combo", "max_combo", "rank", "passed"):
        if fields.get(key) is not None:
            print(f"  staged_{key}: {fields[key]}")
    print(f"  Subtitle path: {subtitle_status['path']}")
    print(f"  Public subtitle exists: {str(subtitle_status['exists']).lower()}")
    print(f"  Public subtitle fresh: {str(subtitle_status['fresh']).lower()}")
    print(f"  Public subtitle bytes: {subtitle_status['bytes']}")
    print(f"  Public subtitle line: {subtitle_status['line']}")
    print(f"  Lifecycle write env: {str(_env_gate_enabled(_LIFECYCLE_WRITE_ENV)).lower()}")
    print(f"  Lifecycle clear env: {str(_env_gate_enabled(_LIFECYCLE_CLEAR_ENV)).lower()}")
    print("  File write: False")
    print("  Pipeline write: False")
    print("  Voice call: False")
    print("  VTS call: False")
    print("  OBS call: False")
    print("  Real input: False")
    print("  Submit: False")


def print_osu_result_subtitle_auto_preview(text: str = "") -> None:
    """
    Auto-preview result subtitle using bridge or manual flags.

    Tries bridge source first. Falls back to manual flags if provided.
    Falls back to no-line/no-write if neither available.

    Supports same result flags as Phase 17:
      --score=<int>
      --accuracy=<float>
      --misses=<int>
      --combo=<int>
      --max-combo=<int>
      --rank=<S/A/B/C/D/F>
      --passed=true|false
      --activity-state=<result/menu/break/gameplay>
      --path=<path>

    Manual flags override bridge fields if both exist.

    Decision: result_subtitle_auto_preview_only_no_write
    No file write. No voice, no VTS, no ElevenLabs, no OBS API, no real input, no submit.
    """
    context = _build_osu_result_subtitle_context(
        text,
        include_bridge=True,
        preview_decision="result_subtitle_auto_preview_only_no_write",
    )
    payload = context["payload"]
    merged = context["merged"]

    # Print output
    print("osu! Result Subtitle Auto Preview")
    print(f"  Decision: {payload['decision']}")
    print(f"  Result source: {merged['source']}")
    print(f"  Source available: {str(merged['source_available']).lower()}")
    print(f"  Activity state: {payload['activity_state']}")
    print(f"  Stream mode: {payload['stream_mode']}")
    print(f"  Speak allowed: {str(payload['speak_allowed']).lower()}")
    print(f"  Response style: {payload['response_style']}")
    print(f"  Line: {payload['line']}")
    print(f"  Output path: {payload['output_path']}")
    print(f"  File write: False")
    print(f"  Pipeline write: False")
    print(f"  Voice call: False")
    print(f"  VTS call: False")
    print(f"  OBS call: False")
    print(f"  Real input: False")
    print(f"  Submit: False")


_AUTO_WRITE_ENV = "NANA_OSU_RESULT_SUBTITLE_AUTO_WRITE_ENABLED"
_AUTO_WRITE_TOKEN = "I_APPROVE_OSU_RESULT_SUBTITLE_AUTO_WRITE"


def _is_auto_write_env_enabled() -> bool:
    return os.environ.get(_AUTO_WRITE_ENV, "").strip() in {"1", "true", "yes", "on"}


def _parse_auto_token(text: str) -> str | None:
    for token in str(text or "").split()[1:]:
        if token.startswith("--operator-approval-token="):
            return token.split("=", 1)[1].strip()
    return None


def print_osu_result_subtitle_auto_write(text: str = "") -> None:
    """
    Gated auto-write of result subtitle using bridge or manual flags.

    Same behavior as /osu-result-subtitle-auto-preview, then gated file write.

    Gates:
    - env NANA_OSU_RESULT_SUBTITLE_AUTO_WRITE_ENABLED=1
    - operator approval token matches exactly
    - stream_mode == talk_ready
    - speak_allowed == True
    - line non-empty

    If any gate fails: Decision=result_subtitle_auto_hold, File write=False.
    If all gates pass: Decision=result_subtitle_auto_file_written, File write=True.

    No voice, no VTS, no ElevenLabs, no OBS API, no real input, no submit.
    """
    context = _build_osu_result_subtitle_context(
        text,
        include_bridge=True,
        preview_decision="result_subtitle_auto_preview_only_no_write",
    )
    payload = dict(context["payload"])
    merged = context["merged"]
    payload["_token_gated"] = _parse_auto_token(text) == _AUTO_WRITE_TOKEN
    write_result = write_public_subtitle_file(
        payload,
        write_env_name=_AUTO_WRITE_ENV,
    )

    file_write = bool(write_result["file_write"])
    pipeline_write = file_write
    decision = "result_subtitle_auto_file_written" if file_write else "result_subtitle_auto_hold"
    hold_reason = write_result.get("hold_reason")
    bytes_written = write_result.get("bytes_written")

    # Print output
    print("osu! Result Subtitle Auto Write")
    print(f"  Decision: {decision}")
    print(f"  Result source: {merged['source']}")
    print(f"  Source available: {str(merged['source_available']).lower()}")
    print(f"  Activity state: {payload['activity_state']}")
    print(f"  Stream mode: {payload['stream_mode']}")
    print(f"  Speak allowed: {str(payload['speak_allowed']).lower()}")
    print(f"  Response style: {payload['response_style']}")
    print(f"  Line: {payload['line']}")
    if hold_reason:
        print(f"  Hold reason: {hold_reason}")
    print(f"  Output path: {write_result['output_path']}")
    if bytes_written is not None:
        print(f"  Bytes written: {bytes_written}")
    print(f"  File write: {str(file_write).lower()}")
    print(f"  Pipeline write: {str(pipeline_write).lower()}")
    print(f"  Voice call: False")
    print(f"  VTS call: False")
    print(f"  OBS call: False")
    print(f"  Real input: False")
    print(f"  Submit: False")


def print_osu_vts_reaction_send_result(result: dict[str, Any]) -> None:
    print("osu! VTS Reaction Send")
    print(f"  Decision: {result['decision']}")
    print(f"  Source: {result['source']}")
    print(f"  Aim status: {result['aim_status']}")
    print(f"  Reaction: {result['reaction']}")
    print(f"  Reason: {result['reason']}")
    print(f"  Gates: env={result['gates']['env']} token={result['gates']['token']}")
    print(f"  VTS call: {result['vts_call']}")
    if result.get("vts_error_type"):
        print(f"  VTS error type: {result['vts_error_type']}")
    if result.get("vts_error"):
        print(f"  VTS error: {result['vts_error']}")
    print("  Real input: False")
    print("  Submit: False")


async def run_osu_vts_reaction_send(text: str = "") -> None:
    try:
        options = _parse_aim_status_flags(text)
        source_path = _resolve_aim_timeline_path(options.get("path") if isinstance(options.get("path"), str) else None)
        operator_token = str(options.get("operator_approval_token") or "")
        result = await send_vts_reaction(
            source_path=source_path,
            stale_ms=int(options["stale_ms"]),
            operator_approval_token=operator_token,
        )
    except ValueError as exc:
        print("osu! VTS Reaction Send")
        print("  Decision: hold")
        print("  Source: invalid")
        print("  Aim status: unavailable")
        print("  Reaction: offline")
        print(f"  Reason: {exc}")
        print("  Gates: env=False token=False")
        print("  VTS call: False")
        print("  Real input: False")
        print("  Submit: False")
        return
    print_osu_vts_reaction_send_result(result)


def handle_command(ctx, text, vts=None, voice=None):
    command = str(text or "").strip().split(" ", 1)[0].lower()
    if command not in OSU_COMMANDS:
        return None
    if command == "/osu-vts-reaction-send":
        raise RuntimeError("osu_vts_reaction_send_requires_async_handler")
    if command in {"/osu-status", "/osu-ready", "/osu-adapter-status"}:
        print_osu_status(ctx)
        return False
    if command == "/osu-bridge-status":
        print_osu_bridge_status(ctx)
        return False
    if command == "/osu-map-status":
        print_osu_map_status(ctx)
        return False
    if command == "/osu-load-current-map":
        print_osu_load_current_map(ctx)
        return False
    if command == "/osu-next-objects":
        print_osu_next_objects(ctx)
        return False
    if command == "/osu-router-preview":
        print_osu_router_preview(ctx)
        return False
    if command == "/osu-play-dry-run":
        print_osu_play_dry_run(ctx)
        return False
    if command == "/osu-input-readiness-status":
        print_osu_input_readiness_status(text)
        return False
    if command == "/osu-input-lease-preview":
        print_osu_input_lease_preview(text)
        return False
    if command == "/osu-input-safety-status":
        print_osu_input_safety_status(text)
        return False
    if command == "/osu-input-focus-status":
        print_osu_input_focus_status(text)
        return False
    if command == "/osu-emergency-release-preview":
        print_osu_emergency_release_preview(text)
        return False
    if command == "/osu-emergency-release-status":
        print_osu_emergency_release_status(text)
        return False
    if command == "/osu-emergency-release-dry-run":
        print_osu_emergency_release_dry_run(text)
        return False
    if command == "/osu-emergency-release":
        print_osu_emergency_release(text)
        return False
    if command == "/osu-emergency-abort-preview":
        print_osu_emergency_abort_preview(text)
        return False
    if command == "/osu-input-benchmark-preview":
        print_osu_input_benchmark_preview(text)
        return False
    if command == "/osu-input-benchmark-status":
        print_osu_input_benchmark_status(text)
        return False
    if command == "/osu-calibration-status":
        print_osu_calibration_status(ctx)
        return False
    if command == "/osu-coordinate-preview":
        print_osu_coordinate_preview(ctx)
        return False
    if command == "/osu-mouse-position":
        print_osu_mouse_position()
        return False
    if command == "/osu-calibration-guide":
        print_osu_calibration_guide()
        return False
    if command == "/osu-calibration-rect":
        print_osu_calibration_rect(text)
        return False
    if command == "/osu-aim-plan-preview":
        print_osu_aim_plan_preview(ctx, text)
        return False
    if command == "/osu-aim-timeline":
        print_osu_aim_timeline(ctx, text)
        return False
    if command == "/osu-aim-timeline-json":
        print_osu_aim_timeline_json(ctx, text)
        return False
    if command == "/osu-aim-timeline-write":
        print_osu_aim_timeline_write(ctx, text)
        return False
    if command == "/osu-aim-trajectory-preview":
        print_osu_aim_trajectory_preview(text)
        return False
    if command == "/osu-aim-trajectory-json":
        print_osu_aim_trajectory_json(text)
        return False
    if command == "/osu-aim-trajectory-write":
        print_osu_aim_trajectory_write(text)
        return False
    if command == "/osu-executor-contract-preview":
        print_osu_executor_contract_preview(text)
        return False
    if command == "/osu-executor-dry-run-schedule":
        print_osu_executor_dry_run_schedule(text)
        return False
    if command == "/osu-executor-dry-run-json":
        print_osu_executor_dry_run_json(text)
        return False
    if command == "/osu-executor-dry-run-write":
        print_osu_executor_dry_run_write(text)
        return False
    if command == "/osu-executor-preflight-status":
        print_osu_executor_preflight_status(text)
        return False
    if command == "/osu-executor-preflight-preview":
        print_osu_executor_preflight_preview(text)
        return False
    if command == "/osu-executor-preflight-json":
        print_osu_executor_preflight_json(text)
        return False
    if command == "/osu-executor-arm-status":
        print_osu_executor_arm_status(text)
        return False
    if command == "/osu-executor-arm-preview":
        print_osu_executor_arm_preview(text)
        return False
    if command == "/osu-executor-arm-json":
        print_osu_executor_arm_json(text)
        return False
    if command == "/osu-executor-armed-lease-preview":
        print_osu_executor_armed_lease_preview(text)
        return False
    if command == "/osu-executor-armed-lease-json":
        print_osu_executor_armed_lease_json(text)
        return False
    if command == "/osu-executor-armed-lease-status":
        print_osu_executor_armed_lease_status(text)
        return False
    if command == "/osu-executor-armed-lease-clear":
        print_osu_executor_armed_lease_clear(text)
        return False
    if command == "/osu-executor-sim-tick-preview":
        print_osu_executor_sim_tick_preview(text)
        return False
    if command == "/osu-executor-sim-tick-json":
        print_osu_executor_sim_tick_json(text)
        return False
    if command == "/osu-executor-sim-tick-status":
        print_osu_executor_sim_tick_status(text)
        return False
    if command == "/osu-executor-sim-tick-loop-preview":
        print_osu_executor_sim_tick_loop_preview(text)
        return False
    if command == "/osu-executor-sim-tick-loop-json":
        print_osu_executor_sim_tick_loop_json(text)
        return False
    if command == "/osu-executor-sim-tick-loop-status":
        print_osu_executor_sim_tick_loop_status(text)
        return False
    if command == "/osu-executor-action-packet-preview":
        print_osu_executor_action_packet_preview(text)
        return False
    if command == "/osu-executor-action-packet-json":
        print_osu_executor_action_packet_json(text)
        return False
    if command == "/osu-executor-action-packet-status":
        print_osu_executor_action_packet_status(text)
        return False
    if command == "/osu-executor-dispatch-preflight-preview":
        print_osu_executor_dispatch_preflight_preview(text)
        return False
    if command == "/osu-executor-dispatch-preflight-json":
        print_osu_executor_dispatch_preflight_json(text)
        return False
    if command == "/osu-executor-dispatch-preflight-status":
        print_osu_executor_dispatch_preflight_status(text)
        return False
    if command == "/osu-executor-shadow-dispatch-session-preview":
        print_osu_executor_shadow_dispatch_session_preview(text)
        return False
    if command == "/osu-executor-shadow-dispatch-session-json":
        print_osu_executor_shadow_dispatch_session_json(text)
        return False
    if command == "/osu-executor-shadow-dispatch-session-status":
        print_osu_executor_shadow_dispatch_session_status(text)
        return False
    if command == "/osu-pre-input-commit-preview":
        print_osu_pre_input_commit_preview(text)
        return False
    if command == "/osu-pre-input-commit-json":
        print_osu_pre_input_commit_json(text)
        return False
    if command == "/osu-pre-input-commit-status":
        print_osu_pre_input_commit_status(text)
        return False
    if command == "/osu-play-plan-preview":
        print_osu_play_plan_preview(text)
        return False
    if command == "/osu-play-plan-json":
        print_osu_play_plan_json(text)
        return False
    if command == "/osu-play-plan-status":
        print_osu_play_plan_status(text)
        return False
    if command == "/osu-runtime-tick-preview":
        print_osu_runtime_tick_preview(text)
        return False
    if command == "/osu-runtime-tick-json":
        print_osu_runtime_tick_json(text)
        return False
    if command == "/osu-runtime-packet-preview":
        print_osu_runtime_packet_preview(text)
        return False
    if command == "/osu-runtime-packet-json":
        print_osu_runtime_packet_json(text)
        return False
    if command == "/osu-runtime-packet-status":
        print_osu_runtime_packet_status(text)
        return False
    if command == "/osu-runtime-packet-loop-preview":
        print_osu_runtime_packet_loop_preview(text)
        return False
    if command == "/osu-runtime-packet-loop-json":
        print_osu_runtime_packet_loop_json(text)
        return False
    if command == "/osu-runtime-packet-loop-status":
        print_osu_runtime_packet_loop_status(text)
        return False
    if command == "/osu-runtime-controller-preview":
        print_osu_runtime_controller_preview(text)
        return False
    if command == "/osu-runtime-controller-json":
        print_osu_runtime_controller_json(text)
        return False
    if command == "/osu-runtime-controller-status":
        print_osu_runtime_controller_status(text)
        return False
    if command == "/osu-runtime-intent-preview":
        print_osu_runtime_intent_preview(text)
        return False
    if command == "/osu-runtime-intent-json":
        print_osu_runtime_intent_json(text)
        return False
    if command == "/osu-runtime-intent-status":
        print_osu_runtime_intent_status(text)
        return False
    if command == "/osu-runtime-backend-dry-preview":
        print_osu_runtime_backend_dry_preview(text)
        return False
    if command == "/osu-runtime-backend-dry-json":
        print_osu_runtime_backend_dry_json(text)
        return False
    if command == "/osu-runtime-backend-dry-status":
        print_osu_runtime_backend_dry_status(text)
        return False
    if command == "/osu-runtime-backend-gate-preview":
        print_osu_runtime_backend_gate_preview(text)
        return False
    if command == "/osu-runtime-backend-gate-json":
        print_osu_runtime_backend_gate_json(text)
        return False
    if command == "/osu-runtime-backend-gate-status":
        print_osu_runtime_backend_gate_status(text)
        return False
    if command == "/osu-vendor-bot-status":
        print_osu_vendor_bot_status(text)
        return False
    if command == "/osu-vendor-bot-start":
        print_osu_vendor_bot_start(text)
        return False
    if command == "/osu-vendor-bot-stop":
        print_osu_vendor_bot_stop(text)
        return False
    if command == "/osu-vendor-bot-json":
        print_osu_vendor_bot_json(text)
        return False
    if command == "/osu-vendor-boss-lifecycle":
        print_osu_vendor_boss_lifecycle(text)
        return False
    if command == "/osu-vendor-boss-auto-start":
        print_osu_vendor_boss_auto_start(text)
        return False
    if command == "/osu-vendor-boss-auto-stop":
        print_osu_vendor_boss_auto_stop(text)
        return False
    if command == "/osu-vendor-boss-auto-status":
        print_osu_vendor_boss_auto_status(text)
        return False
    if command == "/osu-vendor-boss-kill":
        print_osu_vendor_boss_kill(text)
        return False
    if command == "/osu-vendor-boss-kill-all":
        print_osu_vendor_boss_kill_all(text)
        return False
    if command == "/osu-kill-switch":
        print_osu_kill_switch(text)
        return False
    if command == "/osu-kill-switch-status":
        print_osu_kill_switch_status(text)
        return False
    if command == "/osu-vendor-model-heuristic":
        print_osu_vendor_model_heuristic(text)
        return False
    if command == "/osu-vendor-model-install":
        print_osu_vendor_model_install(text)
        return False
    if command == "/osu-cursor-dance-status":
        print_osu_cursor_dance_status(text)
        return False
    if command == "/osu-cursor-dance-start":
        print_osu_cursor_dance_start(text)
        return False
    if command == "/osu-cursor-dance-stop":
        print_osu_cursor_dance_stop(text)
        return False
    if command == "/osu-cursor-dance-kill":
        print_osu_cursor_dance_kill(text)
        return False
    if command == "/osu-stream-presence-readiness":
        print_osu_stream_presence_readiness(text)
        return False
    if command == "/osu-stream-presence-readiness-json":
        print_osu_stream_presence_readiness_json(text)
        return False
    if command == "/osu-aim-status":
        print_osu_aim_status(text)
        return False
    if command == "/osu-vts-reaction-preview":
        print_osu_vts_reaction_preview(text)
        return False
    if command == "/osu-stream-persona-preview":
        print_osu_stream_persona_preview(text)
        return False
    if command == "/osu-core-event-preview":
        print_osu_core_event_preview(text)
        return False
    if command == "/osu-core-result-preview":
        print_osu_core_result_preview(text)
        return False
    if command == "/osu-companion-response-preview":
        print_osu_companion_response_preview(text)
        return False
    if command == "/osu-public-subtitle-preview":
        print_osu_public_subtitle_preview(text)
        return False
    if command == "/osu-public-subtitle-write":
        print_osu_public_subtitle_write(text)
        return False
    if command == "/osu-public-subtitle-status":
        print_osu_public_subtitle_status(text)
        return False
    if command == "/osu-public-subtitle-obs-guide":
        print_osu_public_subtitle_obs_guide()
        return False
    if command == "/osu-public-subtitle-clear":
        print_osu_public_subtitle_clear(text)
        return False
    if command == "/osu-result-subtitle-pipeline-preview":
        print_osu_result_subtitle_pipeline_preview(text)
        return False
    if command == "/osu-result-subtitle-pipeline-write":
        print_osu_result_subtitle_pipeline_write(text)
        return False
    if command == "/osu-result-source-status":
        print_osu_result_source_status(text)
        return False
    if command == "/osu-result-stage":
        print_osu_result_stage(text)
        return False
    if command == "/osu-result-stage-quick":
        print_osu_result_stage_quick(text)
        return False
    if command == "/osu-result-stage-strong":
        print_osu_result_stage_preset(text, preset="strong")
        return False
    if command == "/osu-result-stage-weak":
        print_osu_result_stage_preset(text, preset="weak")
        return False
    if command == "/osu-result-stage-fail":
        print_osu_result_stage_preset(text, preset="fail")
        return False
    if command == "/osu-result-stage-status":
        print_osu_result_stage_status(text)
        return False
    if command == "/osu-result-stage-clear":
        print_osu_result_stage_clear(text)
        return False
    if command == "/osu-result-subtitle-auto-preview":
        print_osu_result_subtitle_auto_preview(text)
        return False
    if command == "/osu-result-subtitle-auto-write":
        print_osu_result_subtitle_auto_write(text)
        return False
    if command == "/osu-result-lifecycle-preview":
        print_osu_result_lifecycle_preview(text)
        return False
    if command == "/osu-result-lifecycle-write":
        print_osu_result_lifecycle_write(text)
        return False
    if command == "/osu-result-lifecycle-clear":
        print_osu_result_lifecycle_clear(text)
        return False
    if command == "/osu-result-lifecycle-status":
        print_osu_result_lifecycle_status(text)
        return False
    return False
