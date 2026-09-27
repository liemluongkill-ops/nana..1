from __future__ import annotations

import atexit
import csv
import io
import os
import json
import shutil
import subprocess
import sys
import threading
import time
import importlib.util
from pathlib import Path
from typing import Any

from nana.game.osu.bridge import OSU_STATE_PATH_DEFAULT, OSU_STATE_PATH_ENV, read_tosu_state, write_state
from nana.game.osu.input_safety import run_emergency_release
from nana.game.osu.result_source import read_result_source_snapshot

VENDOR_ROOT = Path(__file__).resolve().parent / "vendor" / "osu_ai_bot_main"
VENDOR_AUTO_BOT = VENDOR_ROOT / "auto_bot.py"
VENDOR_LIVE_PLAY = VENDOR_ROOT / "live_play.py"
VENDOR_ENV_EXAMPLE = VENDOR_ROOT / ".env.example"
VENDOR_MODELS_DIR = VENDOR_ROOT / "models"
DEFAULT_VENDOR_MODEL = VENDOR_MODELS_DIR / "model.keras"
NANA_MODEL_ENV = "NANA_OSU_VENDOR_MODEL_PATH"
MODEL_ENV = "MODEL_PATH"
MAP_ENV = "OSU_MAP_PATH"
VENDOR_RUNTIME_STATE_ENV = "NANA_OSU_VENDOR_RUNTIME_STATE_PATH"
KILL_SWITCH_HOTKEY_ENV = "NANA_OSU_KILL_SWITCH_HOTKEY_ENABLED"
KILL_SWITCH_KEY_ENV = "NANA_OSU_KILL_SWITCH_KEY"
KILL_SWITCH_DEFAULT_KEY = "delete"
KILL_SWITCH_ENABLED_VALUES = {"", "1", "true", "yes", "on"}
REQUIRED_MODULES = (
    "tensorflow",
    "pandas",
    "numpy",
    "matplotlib",
    "win32api",
    "win32gui",
    "websocket",
    "pygame",
    "dotenv",
    "keyboard",
)
RESTART_BACKOFF_MS = 10000

_PROCESS: subprocess.Popen | None = None
_STARTED_AT: float | None = None
_SCRIPT_NAME: str | None = None
_LAST_EXIT_CODE: int | None = None
_LAST_EXIT_AT: float | None = None
_LAST_RUNTIME_MS: int | None = None
_RESTART_COUNT = 0
_LAST_CRASH_REASON: str | None = None
_RESTART_BACKOFF_MS = RESTART_BACKOFF_MS
_WATCHER_THREAD: threading.Thread | None = None
_WATCHER_STOP: threading.Event | None = None
_WATCHER_INTERVAL_MS = 500
_WATCHER_STARTED_AT: float | None = None
_WATCHER_LAST_PAYLOAD: dict[str, Any] | None = None
_WATCHER_LOCK = threading.Lock()
_KILL_LOCK = threading.RLock()
_KILL_HOTKEY_REGISTERED = False
_KILL_HOTKEY_HANDLE: Any = None
_KILL_HOTKEY_ERROR: str | None = None
_LAST_KILL_CALLED_AT: float | None = None
_LAST_KILL_TRIGGER: str | None = None
_LAST_KILL_MODE: str | None = None
_LAST_KILLED_PIDS: list[int] = []
_LAST_KILL_ERROR: str | None = None


def _safety_flags() -> dict[str, bool]:
    return {
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
    }


def _supervise_process() -> int | None:
    global _PROCESS, _STARTED_AT, _SCRIPT_NAME
    global _LAST_EXIT_CODE, _LAST_EXIT_AT, _LAST_RUNTIME_MS, _RESTART_COUNT, _LAST_CRASH_REASON

    if _PROCESS is None:
        return None
    exit_code = _PROCESS.poll()
    if exit_code is None:
        return None
    now = time.time()
    _LAST_EXIT_CODE = int(exit_code)
    _LAST_EXIT_AT = now
    _LAST_RUNTIME_MS = int((now - _STARTED_AT) * 1000) if _STARTED_AT else None
    _RESTART_COUNT += 1
    _LAST_CRASH_REASON = "process_exited" if exit_code == 0 else f"process_exit_code:{exit_code}"
    _PROCESS = None
    _STARTED_AT = None
    _SCRIPT_NAME = None
    return exit_code


def _restart_backoff_remaining_ms(now: float | None = None) -> int:
    if _LAST_EXIT_AT is None:
        return 0
    now = time.time() if now is None else now
    elapsed_ms = int((now - _LAST_EXIT_AT) * 1000)
    return max(0, int(_RESTART_BACKOFF_MS) - elapsed_ms)


def _crash_diagnostics() -> dict[str, Any]:
    return {
        "last_exit_code": _LAST_EXIT_CODE,
        "last_exit_at": _LAST_EXIT_AT,
        "last_runtime_ms": _LAST_RUNTIME_MS,
        "restart_count": _RESTART_COUNT,
        "last_crash_reason": _LAST_CRASH_REASON,
        "restart_backoff_ms": _RESTART_BACKOFF_MS,
        "restart_backoff_remaining_ms": _restart_backoff_remaining_ms(),
    }


def _apply_crash_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
    payload.update(_crash_diagnostics())
    return payload


def _base_payload(decision: str, reason: str) -> dict[str, Any]:
    _supervise_process()
    running = _PROCESS is not None and _PROCESS.poll() is None
    model_path = _current_model_path({})
    map_path = _current_map_path({})
    dependency_status = _dependency_status()
    payload = {
        "schema": "nana.osu.vendor_bot_runtime.v1",
        "version": 1,
        "decision": decision,
        "reason": reason,
        "vendor_root": str(VENDOR_ROOT),
        "vendor_present": VENDOR_ROOT.exists(),
        "auto_bot_present": VENDOR_AUTO_BOT.exists(),
        "live_play_present": VENDOR_LIVE_PLAY.exists(),
        "env_example_present": VENDOR_ENV_EXAMPLE.exists(),
        "running": running,
        "pid": _PROCESS.pid if running and _PROCESS is not None else None,
        "script": _SCRIPT_NAME,
        "started_at": _STARTED_AT,
        "uptime_ms": int((time.time() - _STARTED_AT) * 1000) if running and _STARTED_AT else 0,
        "model_path": str(model_path) if model_path else None,
        "model_present": bool(model_path and model_path.exists()),
        "map_path": str(map_path) if map_path else None,
        "map_present": bool(map_path and map_path.exists()),
        "dependency_status": dependency_status,
        "dependencies_ready": all(dependency_status.values()),
        "model_env_name": NANA_MODEL_ENV,
        "fallback_model_env_name": MODEL_ENV,
        "default_model_path": str(DEFAULT_VENDOR_MODEL),
        "start_command_hint": "/osu-vendor-bot-start --mode=auto_bot",
        "real_input": running,
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
    }
    _apply_crash_diagnostics(payload)
    payload.update(_boss_state_fields())
    payload.update(_kill_switch_fields())
    return payload


def _parse_flags(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        values[name.strip()] = raw_value.strip()
    return values


def _script_from_mode(mode: str) -> Path:
    if mode == "live_play":
        return VENDOR_LIVE_PLAY
    return VENDOR_AUTO_BOT


def _dependency_status() -> dict[str, bool]:
    return {name: importlib.util.find_spec(name) is not None for name in REQUIRED_MODULES}


def _kill_hotkey_enabled_by_env() -> bool:
    return os.getenv(KILL_SWITCH_HOTKEY_ENV, "").strip().lower() in KILL_SWITCH_ENABLED_VALUES


def _kill_hotkey_key() -> str:
    return os.getenv(KILL_SWITCH_KEY_ENV, KILL_SWITCH_DEFAULT_KEY).strip().lower() or KILL_SWITCH_DEFAULT_KEY


def _keyboard_dependency_available() -> bool:
    try:
        return importlib.util.find_spec("keyboard") is not None
    except Exception:
        return False


def _kill_switch_fields() -> dict[str, Any]:
    return {
        "kill_switch_available": _keyboard_dependency_available(),
        "kill_hotkey_enabled": _kill_hotkey_enabled_by_env(),
        "kill_hotkey_registered": _KILL_HOTKEY_REGISTERED,
        "kill_hotkey_key": _kill_hotkey_key(),
        "kill_hotkey_error": _KILL_HOTKEY_ERROR,
        "last_kill_called_at": _LAST_KILL_CALLED_AT,
        "last_kill_trigger": _LAST_KILL_TRIGGER,
        "last_kill_mode": _LAST_KILL_MODE,
        "last_killed_pids": list(_LAST_KILLED_PIDS),
        "last_kill_error": _LAST_KILL_ERROR,
    }


def _runtime_state_path() -> Path:
    return Path(os.getenv(OSU_STATE_PATH_ENV) or OSU_STATE_PATH_DEFAULT)


def _vendor_boss_state_path() -> Path:
    return _runtime_state_path().with_name("vendor_boss_state.json")


def _read_vendor_boss_state() -> dict[str, Any] | None:
    path = _vendor_boss_state_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _boss_state_fields() -> dict[str, Any]:
    state = _read_vendor_boss_state()
    path = _vendor_boss_state_path()
    fields = {
        "boss_state_path": str(path),
        "boss_state_present": state is not None,
        "boss_status_reason": None,
        "boss_ws_message_count": None,
        "boss_last_ws_error": None,
        "boss_last_ws_message_at": None,
        "boss_raw_menu_state": None,
        "boss_activity_state": None,
        "boss_beatmap_path": None,
        "boss_beatmap_original_path": None,
        "boss_beatmap_parser_path": None,
        "boss_beatmap_parser_path_exists": None,
        "boss_beatmap_path_source": None,
        "boss_beatmap_path_exists": None,
        "boss_beatmap_processed": None,
        "boss_beatmap_process_error": None,
        "boss_beatmap_process_status": None,
        "boss_aim_mode": None,
        "boss_aim_profile": None,
        "boss_hard_snap_enabled": None,
        "boss_aim_profile_warning": None,
        "boss_hard_snap_lead_ms": None,
        "boss_hard_snap_max_gap_ms": None,
        "boss_hard_snap_stale_grace_ms": None,
        "boss_hard_snap_target_index": None,
        "boss_hard_snap_target_type": None,
        "boss_hard_snap_target_time_ms": None,
        "boss_hard_snap_time_to_target_ms": None,
        "boss_hard_snap_selected_time_ms": None,
        "boss_hard_snap_snap_count": None,
        "boss_hard_snap_hold_count": None,
        "boss_hard_snap_reason": None,
        "boss_hard_snap_target_playfield_xy": None,
        "boss_hard_snap_target_screen_xy": None,
        "boss_hard_snap_slider_lead_ms": None,
        "boss_hard_snap_slider_speed_multiplier": None,
        "boss_hard_snap_slider_exit_ms": None,
        "boss_hard_snap_slider_active": None,
        "boss_hard_snap_slider_index": None,
        "boss_hard_snap_slider_raw_progress": None,
        "boss_hard_snap_slider_effective_progress": None,
        "boss_hard_snap_slider_slide_index": None,
        "boss_hard_snap_slider_local_progress": None,
        "boss_hard_snap_slider_target_playfield_xy": None,
        "boss_hard_snap_slider_target_screen_xy": None,
        "boss_hard_snap_slider_exit_count": None,
        "boss_hard_snap_slider_fallback_count": None,
        "boss_hard_snap_slider_reason": None,
        "boss_hard_snap_arbiter_mode": None,
        "boss_hard_snap_arbiter_active": None,
        "boss_hard_snap_arbiter_bypassed": None,
        "boss_hard_snap_arbiter_warning": None,
        "boss_hard_snap_arbiter_enabled": None,
        "boss_hard_snap_arbiter_winner_index": None,
        "boss_hard_snap_arbiter_winner_type": None,
        "boss_hard_snap_arbiter_reason": None,
        "boss_hard_snap_arbiter_previous_index": None,
        "boss_hard_snap_arbiter_switch_count": None,
        "boss_hard_snap_arbiter_switch_delta_ms": None,
        "boss_hard_snap_arbiter_stale_skipped_count": None,
        "boss_hard_snap_slider_aim_owner_cleared_count": None,
        "boss_hard_snap_slider_release_to_next_ms": None,
        "boss_hard_snap_slider_endpoint_hold_ms": None,
        "boss_input_mode": None,
        "boss_key_dispatch_allowed": None,
        "boss_slider_mode": None,
        "boss_spinner_mode": None,
        "boss_slider_mode_effective": None,
        "boss_spinner_mode_effective": None,
        "boss_simple_slider_path_mode": None,
        "boss_simple_slider_path_len": None,
        "boss_simple_slider_path_num_points": None,
        "boss_simple_slider_progress_total": None,
        "boss_simple_slider_slide_index": None,
        "boss_simple_slider_local_progress": None,
        "boss_simple_slider_dense_follow_count": None,
        "boss_simple_slider_sparse_follow_count": None,
        "boss_slider_output_smoothing_enabled": None,
        "boss_slider_output_smoothed_count": None,
        "boss_slider_output_raw_xy": None,
        "boss_slider_output_smoothed_xy": None,
        "boss_input_move_called": None,
        "boss_sendinput_move_count": None,
        "boss_click_enabled": None,
        "boss_click_count": None,
        "boss_tap_count": None,
        "boss_hit_keys": None,
        "boss_hit_offset_ms": None,
        "boss_hit_window_ms": None,
        "boss_slider_follow_lead_ms": None,
        "boss_slider_speed_multiplier": None,
        "boss_slider_time_authority_enabled": None,
        "boss_slider_time_authority_abandon_count": None,
        "boss_last_slider_abandon_index": None,
        "boss_last_slider_abandon_overrun_ms": None,
        "boss_slider_exit_preaim_ms": None,
        "boss_slider_exit_snap_ms": None,
        "boss_slider_exit_preaim_mode": None,
        "boss_slider_exit_early_aim_ms": None,
        "boss_slider_exit_next_max_delta_ms": None,
        "boss_slider_exit_handoff_count": None,
        "boss_slider_exit_snap_count": None,
        "boss_slider_exit_stale_snap_ignored_count": None,
        "boss_slider_exit_early_aim_count": None,
        "boss_slider_exit_blend_count": None,
        "boss_slider_exit_snap_preaim_count": None,
        "boss_slider_exit_blend_progress": None,
        "boss_slider_exit_preaim_target_xy": None,
        "boss_slider_exit_preaim_next_object_index": None,
        "boss_slider_exit_preaim_next_object_type": None,
        "boss_slider_exit_preaim_next_delta_ms": None,
        "boss_slider_exit_preaim_last_reason": None,
        "boss_slider_exit_next_object_index": None,
        "boss_slider_exit_next_object_type": None,
        "boss_slider_exit_next_object_time_delta_ms": None,
        "boss_slider_exit_overrun_ms": None,
        "boss_slider_exit_blocked_ms": None,
        "boss_slider_exit_last_reason": None,
        "boss_slider_exit_last_slider_index": None,
        "boss_slider_follow_count": None,
        "boss_spinner_spin_count": None,
        "boss_simple_slider_follow_count": None,
        "boss_pilot_slider_follow_count": None,
        "boss_simple_spinner_spin_count": None,
        "boss_pilot_spinner_spin_count": None,
        "boss_hold_count": None,
        "boss_key_down_count": None,
        "boss_key_up_count": None,
        "boss_last_hit_key": None,
        "boss_last_action_type": None,
        "boss_last_action_time_ms": None,
        "boss_last_action_scheduled_time_ms": None,
        "boss_last_action_effective_time_ms": None,
        "boss_last_action_delta_ms": None,
        "boss_last_action_error": None,
        "boss_tap_attempt_count": None,
        "boss_tap_on_time_count": None,
        "boss_tap_early_count": None,
        "boss_tap_late_count": None,
        "boss_slider_start_count": None,
        "boss_spinner_start_count": None,
        "boss_slider_duration_ms": None,
        "boss_slider_path_len": None,
        "boss_slider_slide_count": None,
        "boss_slider_follow_sample_count": None,
        "boss_slider_last_progress": None,
        "boss_slider_last_xy": None,
        "boss_spinner_rpm": None,
        "boss_spinner_radius": None,
        "boss_spinner_move_count": None,
        "boss_spinner_last_angle": None,
        "boss_long_hold_active": None,
        "boss_long_hold_key": None,
        "boss_long_hold_started_at": None,
        "boss_long_hold_expected_end": None,
        "boss_selected_action_index": None,
        "boss_previous_selected_action_index": None,
        "boss_selected_index_delta": None,
        "boss_skipped_object_count": None,
        "boss_selected_action_type": None,
        "boss_previous_action_type": None,
        "boss_selected_action_time_ms": None,
        "boss_selected_action_end_time_ms": None,
        "boss_time_to_selected_start_ms": None,
        "boss_time_to_selected_end_ms": None,
        "boss_next_object_index": None,
        "boss_next_object_type": None,
        "boss_next_object_time_ms": None,
        "boss_time_to_next_object_ms": None,
        "boss_active_hold_index": None,
        "boss_active_hold_type": None,
        "boss_active_hold_key": None,
        "boss_loop_interval_ms": None,
        "boss_move_interval_ms": None,
        "boss_slider_move_interval_ms": None,
        "boss_slider_target_delta": None,
        "boss_slider_screen_delta": None,
        "boss_slider_progress": None,
        "boss_slider_jitter_warning_count": None,
        "boss_note_skip_warning_count": None,
        "boss_hold_blocks_next_warning_count": None,
        "boss_scheduler_enabled": None,
        "boss_scheduler_queue_len": None,
        "boss_scheduler_next_event_type": None,
        "boss_scheduler_next_event_time_ms": None,
        "boss_scheduler_last_event_type": None,
        "boss_scheduler_last_event_delta_ms": None,
        "boss_scheduler_key_down_count": None,
        "boss_scheduler_key_up_count": None,
        "boss_active_follow_type": None,
        "boss_active_follow_index": None,
        "boss_active_follow_start_time_ms": None,
        "boss_active_follow_end_time_ms": None,
        "boss_active_follow_progress": None,
        "boss_active_slider_slide_index": None,
        "boss_active_slider_local_progress": None,
        "boss_active_slider_target_time_ms": None,
        "boss_active_slider_start_time_ms": None,
        "boss_active_slider_end_time_ms": None,
        "boss_active_slider_elapsed_ms": None,
        "boss_active_slider_duration_ms": None,
        "boss_active_slider_raw_progress": None,
        "boss_active_slider_speed_progress": None,
        "boss_active_slider_effective_progress": None,
        "boss_active_slider_remaining_ms": None,
        "boss_active_slider_time_expired": None,
        "boss_active_slider_blocks_next_object": None,
        "boss_next_object_after_slider_index": None,
        "boss_time_to_next_object_after_slider_ms": None,
        "boss_active_slider_follow_hz": None,
        "boss_active_slider_follow_interval_ms": None,
        "boss_active_slider_path_len": None,
        "boss_active_slider_target_xy": None,
        "boss_active_slider_screen_xy": None,
        "boss_follow_lag_ms": None,
        "boss_rect_mode": None,
        "boss_playfield_transform": None,
        "boss_rect_auto_mode": None,
        "boss_rect_source": None,
        "boss_env_playfield_rect": None,
        "boss_client_full_rect": None,
        "boss_aspect_fit_playfield_rect": None,
        "boss_osu_pilot_rect": None,
        "boss_official_shift_rect": None,
        "boss_resolved_playfield_rect": None,
        "boss_playfield_scale": None,
        "boss_playfield_margin_x": None,
        "boss_playfield_margin_y": None,
        "boss_vertical_shift_px": None,
        "boss_osu_window_rect": None,
        "boss_osu_client_rect": None,
        "boss_monitor_size": None,
        "boss_virtual_screen": None,
        "boss_dpi_scale": None,
        "boss_last_input_kind": None,
        "boss_last_target_source": None,
        "boss_last_target_playfield_xy": None,
        "boss_last_target_screen_xy": None,
        "boss_last_tosu_time_ms": None,
        "boss_time_candidates": None,
        "boss_selected_time_source": None,
        "boss_selected_time_ms": None,
        "boss_time_sane": None,
        "boss_time_sanity_reason": None,
        "boss_first_object_time_ms": None,
        "boss_last_object_end_time_ms": None,
        "boss_time_vs_first_ms": None,
        "boss_time_vs_last_ms": None,
        "boss_start_max_late_ms": None,
        "boss_late_start_hold": None,
        "boss_late_start_reason": None,
        "boss_movement_armed": None,
        "boss_last_effective_time_ms": None,
        "boss_last_path_index": None,
        "boss_start_offset": None,
        "boss_is_ready": None,
        "boss_is_playing": None,
        "boss_timer_mode": None,
        "boss_normalized_path_len": None,
        "boss_move_count": None,
        "boss_last_move_xy": None,
        "boss_last_move_error": None,
        "boss_heartbeat_interval_ms": None,
        "boss_debug_log_enabled": None,
    }
    if not isinstance(state, dict):
        return fields
    fields.update(
        {
            "boss_status_reason": state.get("status_reason"),
            "boss_ws_message_count": state.get("ws_message_count"),
            "boss_last_ws_error": state.get("last_ws_error"),
            "boss_last_ws_message_at": state.get("last_ws_message_at"),
            "boss_raw_menu_state": state.get("raw_menu_state"),
            "boss_activity_state": state.get("activity_state"),
            "boss_beatmap_path": state.get("beatmap_path"),
            "boss_beatmap_original_path": state.get("beatmap_original_path"),
            "boss_beatmap_parser_path": state.get("beatmap_parser_path"),
            "boss_beatmap_parser_path_exists": state.get("beatmap_parser_path_exists"),
            "boss_beatmap_path_source": state.get("beatmap_path_source"),
            "boss_beatmap_path_exists": state.get("beatmap_path_exists"),
            "boss_beatmap_processed": state.get("beatmap_processed"),
            "boss_beatmap_process_error": state.get("beatmap_process_error"),
            "boss_beatmap_process_status": state.get("beatmap_process_status"),
            "boss_aim_mode": state.get("aim_mode"),
            "boss_aim_profile": state.get("aim_profile"),
            "boss_hard_snap_enabled": state.get("hard_snap_enabled"),
            "boss_aim_profile_warning": state.get("aim_profile_warning"),
            "boss_hard_snap_lead_ms": state.get("hard_snap_lead_ms"),
            "boss_hard_snap_max_gap_ms": state.get("hard_snap_max_gap_ms"),
            "boss_hard_snap_stale_grace_ms": state.get("hard_snap_stale_grace_ms"),
            "boss_hard_snap_target_index": state.get("hard_snap_target_index"),
            "boss_hard_snap_target_type": state.get("hard_snap_target_type"),
            "boss_hard_snap_target_time_ms": state.get("hard_snap_target_time_ms"),
            "boss_hard_snap_time_to_target_ms": state.get("hard_snap_time_to_target_ms"),
            "boss_hard_snap_selected_time_ms": state.get("hard_snap_selected_time_ms"),
            "boss_hard_snap_snap_count": state.get("hard_snap_snap_count"),
            "boss_hard_snap_hold_count": state.get("hard_snap_hold_count"),
            "boss_hard_snap_reason": state.get("hard_snap_reason"),
            "boss_hard_snap_target_playfield_xy": state.get("hard_snap_target_playfield_xy"),
            "boss_hard_snap_target_screen_xy": state.get("hard_snap_target_screen_xy"),
            "boss_hard_snap_slider_lead_ms": state.get("hard_snap_slider_lead_ms"),
            "boss_hard_snap_slider_speed_multiplier": state.get("hard_snap_slider_speed_multiplier"),
            "boss_hard_snap_slider_exit_ms": state.get("hard_snap_slider_exit_ms"),
            "boss_hard_snap_slider_active": state.get("hard_snap_slider_active"),
            "boss_hard_snap_slider_index": state.get("hard_snap_slider_index"),
            "boss_hard_snap_slider_raw_progress": state.get("hard_snap_slider_raw_progress"),
            "boss_hard_snap_slider_effective_progress": state.get("hard_snap_slider_effective_progress"),
            "boss_hard_snap_slider_slide_index": state.get("hard_snap_slider_slide_index"),
            "boss_hard_snap_slider_local_progress": state.get("hard_snap_slider_local_progress"),
            "boss_hard_snap_slider_target_playfield_xy": state.get("hard_snap_slider_target_playfield_xy"),
            "boss_hard_snap_slider_target_screen_xy": state.get("hard_snap_slider_target_screen_xy"),
            "boss_hard_snap_slider_exit_count": state.get("hard_snap_slider_exit_count"),
            "boss_hard_snap_slider_fallback_count": state.get("hard_snap_slider_fallback_count"),
            "boss_hard_snap_slider_reason": state.get("hard_snap_slider_reason"),
            "boss_hard_snap_arbiter_mode": state.get("hard_snap_arbiter_mode"),
            "boss_hard_snap_arbiter_active": state.get("hard_snap_arbiter_active"),
            "boss_hard_snap_arbiter_bypassed": state.get("hard_snap_arbiter_bypassed"),
            "boss_hard_snap_arbiter_warning": state.get("hard_snap_arbiter_warning"),
            "boss_hard_snap_arbiter_enabled": state.get("hard_snap_arbiter_enabled"),
            "boss_hard_snap_arbiter_winner_index": state.get("hard_snap_arbiter_winner_index"),
            "boss_hard_snap_arbiter_winner_type": state.get("hard_snap_arbiter_winner_type"),
            "boss_hard_snap_arbiter_reason": state.get("hard_snap_arbiter_reason"),
            "boss_hard_snap_arbiter_previous_index": state.get("hard_snap_arbiter_previous_index"),
            "boss_hard_snap_arbiter_switch_count": state.get("hard_snap_arbiter_switch_count"),
            "boss_hard_snap_arbiter_switch_delta_ms": state.get("hard_snap_arbiter_switch_delta_ms"),
            "boss_hard_snap_arbiter_stale_skipped_count": state.get("hard_snap_arbiter_stale_skipped_count"),
            "boss_hard_snap_slider_aim_owner_cleared_count": state.get("hard_snap_slider_aim_owner_cleared_count"),
            "boss_hard_snap_slider_release_to_next_ms": state.get("hard_snap_slider_release_to_next_ms"),
            "boss_hard_snap_slider_endpoint_hold_ms": state.get("hard_snap_slider_endpoint_hold_ms"),
            "boss_input_mode": state.get("input_mode"),
            "boss_key_dispatch_allowed": state.get("key_dispatch_allowed"),
            "boss_slider_mode": state.get("slider_mode"),
            "boss_spinner_mode": state.get("spinner_mode"),
            "boss_slider_mode_effective": state.get("slider_mode_effective"),
            "boss_spinner_mode_effective": state.get("spinner_mode_effective"),
            "boss_simple_slider_path_mode": state.get("simple_slider_path_mode"),
            "boss_simple_slider_path_len": state.get("simple_slider_path_len"),
            "boss_simple_slider_path_num_points": state.get("simple_slider_path_num_points"),
            "boss_simple_slider_progress_total": state.get("simple_slider_progress_total"),
            "boss_simple_slider_slide_index": state.get("simple_slider_slide_index"),
            "boss_simple_slider_local_progress": state.get("simple_slider_local_progress"),
            "boss_simple_slider_dense_follow_count": state.get("simple_slider_dense_follow_count"),
            "boss_simple_slider_sparse_follow_count": state.get("simple_slider_sparse_follow_count"),
            "boss_slider_output_smoothing_enabled": state.get("slider_output_smoothing_enabled"),
            "boss_slider_output_smoothed_count": state.get("slider_output_smoothed_count"),
            "boss_slider_output_raw_xy": state.get("slider_output_raw_xy"),
            "boss_slider_output_smoothed_xy": state.get("slider_output_smoothed_xy"),
            "boss_input_move_called": state.get("input_move_called"),
            "boss_sendinput_move_count": state.get("sendinput_move_count"),
            "boss_click_enabled": state.get("click_enabled"),
            "boss_click_count": state.get("click_count"),
            "boss_tap_count": state.get("tap_count"),
            "boss_hit_keys": state.get("hit_keys"),
            "boss_hit_offset_ms": state.get("hit_offset_ms"),
            "boss_hit_window_ms": state.get("hit_window_ms"),
            "boss_slider_follow_lead_ms": state.get("slider_follow_lead_ms"),
            "boss_slider_speed_multiplier": state.get("slider_speed_multiplier"),
            "boss_slider_time_authority_enabled": state.get("slider_time_authority_enabled"),
            "boss_slider_time_authority_abandon_count": state.get("slider_time_authority_abandon_count"),
            "boss_last_slider_abandon_index": state.get("last_slider_abandon_index"),
            "boss_last_slider_abandon_overrun_ms": state.get("last_slider_abandon_overrun_ms"),
            "boss_slider_exit_preaim_ms": state.get("slider_exit_preaim_ms"),
            "boss_slider_exit_snap_ms": state.get("slider_exit_snap_ms"),
            "boss_slider_exit_preaim_mode": state.get("slider_exit_preaim_mode"),
            "boss_slider_exit_early_aim_ms": state.get("slider_exit_early_aim_ms"),
            "boss_slider_exit_next_max_delta_ms": state.get("slider_exit_next_max_delta_ms"),
            "boss_slider_exit_handoff_count": state.get("slider_exit_handoff_count"),
            "boss_slider_exit_snap_count": state.get("slider_exit_snap_count"),
            "boss_slider_exit_stale_snap_ignored_count": state.get("slider_exit_stale_snap_ignored_count"),
            "boss_slider_exit_early_aim_count": state.get("slider_exit_early_aim_count"),
            "boss_slider_exit_blend_count": state.get("slider_exit_blend_count"),
            "boss_slider_exit_snap_preaim_count": state.get("slider_exit_snap_preaim_count"),
            "boss_slider_exit_blend_progress": state.get("slider_exit_blend_progress"),
            "boss_slider_exit_preaim_target_xy": state.get("slider_exit_preaim_target_xy"),
            "boss_slider_exit_preaim_next_object_index": state.get("slider_exit_preaim_next_object_index"),
            "boss_slider_exit_preaim_next_object_type": state.get("slider_exit_preaim_next_object_type"),
            "boss_slider_exit_preaim_next_delta_ms": state.get("slider_exit_preaim_next_delta_ms"),
            "boss_slider_exit_preaim_last_reason": state.get("slider_exit_preaim_last_reason"),
            "boss_slider_exit_next_object_index": state.get("slider_exit_next_object_index"),
            "boss_slider_exit_next_object_type": state.get("slider_exit_next_object_type"),
            "boss_slider_exit_next_object_time_delta_ms": state.get("slider_exit_next_object_time_delta_ms"),
            "boss_slider_exit_overrun_ms": state.get("slider_exit_overrun_ms"),
            "boss_slider_exit_blocked_ms": state.get("slider_exit_blocked_ms"),
            "boss_slider_exit_last_reason": state.get("slider_exit_last_reason"),
            "boss_slider_exit_last_slider_index": state.get("slider_exit_last_slider_index"),
            "boss_slider_follow_count": state.get("slider_follow_count"),
            "boss_spinner_spin_count": state.get("spinner_spin_count"),
            "boss_simple_slider_follow_count": state.get("simple_slider_follow_count"),
            "boss_pilot_slider_follow_count": state.get("pilot_slider_follow_count"),
            "boss_simple_spinner_spin_count": state.get("simple_spinner_spin_count"),
            "boss_pilot_spinner_spin_count": state.get("pilot_spinner_spin_count"),
            "boss_hold_count": state.get("hold_count"),
            "boss_key_down_count": state.get("key_down_count"),
            "boss_key_up_count": state.get("key_up_count"),
            "boss_last_hit_key": state.get("last_hit_key"),
            "boss_last_action_type": state.get("last_action_type"),
            "boss_last_action_time_ms": state.get("last_action_time_ms"),
            "boss_last_action_scheduled_time_ms": state.get("last_action_scheduled_time_ms"),
            "boss_last_action_effective_time_ms": state.get("last_action_effective_time_ms"),
            "boss_last_action_delta_ms": state.get("last_action_delta_ms"),
            "boss_last_action_error": state.get("last_action_error"),
            "boss_tap_attempt_count": state.get("tap_attempt_count"),
            "boss_tap_on_time_count": state.get("tap_on_time_count"),
            "boss_tap_early_count": state.get("tap_early_count"),
            "boss_tap_late_count": state.get("tap_late_count"),
            "boss_slider_start_count": state.get("slider_start_count"),
            "boss_spinner_start_count": state.get("spinner_start_count"),
            "boss_slider_duration_ms": state.get("slider_duration_ms"),
            "boss_slider_path_len": state.get("slider_path_len"),
            "boss_slider_slide_count": state.get("slider_slide_count"),
            "boss_slider_follow_sample_count": state.get("slider_follow_sample_count"),
            "boss_slider_last_progress": state.get("slider_last_progress"),
            "boss_slider_last_xy": state.get("slider_last_xy"),
            "boss_spinner_rpm": state.get("spinner_rpm"),
            "boss_spinner_radius": state.get("spinner_radius"),
            "boss_spinner_move_count": state.get("spinner_move_count"),
            "boss_spinner_last_angle": state.get("spinner_last_angle"),
            "boss_long_hold_active": state.get("long_hold_active"),
            "boss_long_hold_key": state.get("long_hold_key"),
            "boss_long_hold_started_at": state.get("long_hold_started_at"),
            "boss_long_hold_expected_end": state.get("long_hold_expected_end"),
            "boss_selected_action_index": state.get("selected_action_index"),
            "boss_previous_selected_action_index": state.get("previous_selected_action_index"),
            "boss_selected_index_delta": state.get("selected_index_delta"),
            "boss_skipped_object_count": state.get("skipped_object_count"),
            "boss_selected_action_type": state.get("selected_action_type"),
            "boss_previous_action_type": state.get("previous_action_type"),
            "boss_selected_action_time_ms": state.get("selected_action_time_ms"),
            "boss_selected_action_end_time_ms": state.get("selected_action_end_time_ms"),
            "boss_time_to_selected_start_ms": state.get("time_to_selected_start_ms"),
            "boss_time_to_selected_end_ms": state.get("time_to_selected_end_ms"),
            "boss_next_object_index": state.get("next_object_index"),
            "boss_next_object_type": state.get("next_object_type"),
            "boss_next_object_time_ms": state.get("next_object_time_ms"),
            "boss_time_to_next_object_ms": state.get("time_to_next_object_ms"),
            "boss_active_hold_index": state.get("active_hold_index"),
            "boss_active_hold_type": state.get("active_hold_type"),
            "boss_active_hold_key": state.get("active_hold_key"),
            "boss_loop_interval_ms": state.get("loop_interval_ms"),
            "boss_move_interval_ms": state.get("move_interval_ms"),
            "boss_slider_move_interval_ms": state.get("slider_move_interval_ms"),
            "boss_slider_target_delta": state.get("slider_target_delta"),
            "boss_slider_screen_delta": state.get("slider_screen_delta"),
            "boss_slider_progress": state.get("slider_progress"),
            "boss_slider_jitter_warning_count": state.get("slider_jitter_warning_count"),
            "boss_note_skip_warning_count": state.get("note_skip_warning_count"),
            "boss_hold_blocks_next_warning_count": state.get("hold_blocks_next_warning_count"),
            "boss_scheduler_enabled": state.get("scheduler_enabled"),
            "boss_scheduler_queue_len": state.get("scheduler_queue_len"),
            "boss_scheduler_next_event_type": state.get("scheduler_next_event_type"),
            "boss_scheduler_next_event_time_ms": state.get("scheduler_next_event_time_ms"),
            "boss_scheduler_last_event_type": state.get("scheduler_last_event_type"),
            "boss_scheduler_last_event_delta_ms": state.get("scheduler_last_event_delta_ms"),
            "boss_scheduler_key_down_count": state.get("scheduler_key_down_count"),
            "boss_scheduler_key_up_count": state.get("scheduler_key_up_count"),
            "boss_active_follow_type": state.get("active_follow_type"),
            "boss_active_follow_index": state.get("active_follow_index"),
            "boss_active_follow_start_time_ms": state.get("active_follow_start_time_ms"),
            "boss_active_follow_end_time_ms": state.get("active_follow_end_time_ms"),
            "boss_active_follow_progress": state.get("active_follow_progress"),
            "boss_active_slider_slide_index": state.get("active_slider_slide_index"),
            "boss_active_slider_local_progress": state.get("active_slider_local_progress"),
            "boss_active_slider_target_time_ms": state.get("active_slider_target_time_ms"),
            "boss_active_slider_start_time_ms": state.get("active_slider_start_time_ms"),
            "boss_active_slider_end_time_ms": state.get("active_slider_end_time_ms"),
            "boss_active_slider_elapsed_ms": state.get("active_slider_elapsed_ms"),
            "boss_active_slider_duration_ms": state.get("active_slider_duration_ms"),
            "boss_active_slider_raw_progress": state.get("active_slider_raw_progress"),
            "boss_active_slider_speed_progress": state.get("active_slider_speed_progress"),
            "boss_active_slider_effective_progress": state.get("active_slider_effective_progress"),
            "boss_active_slider_remaining_ms": state.get("active_slider_remaining_ms"),
            "boss_active_slider_time_expired": state.get("active_slider_time_expired"),
            "boss_active_slider_blocks_next_object": state.get("active_slider_blocks_next_object"),
            "boss_next_object_after_slider_index": state.get("next_object_after_slider_index"),
            "boss_time_to_next_object_after_slider_ms": state.get("time_to_next_object_after_slider_ms"),
            "boss_active_slider_follow_hz": state.get("active_slider_follow_hz"),
            "boss_active_slider_follow_interval_ms": state.get("active_slider_follow_interval_ms"),
            "boss_active_slider_path_len": state.get("active_slider_path_len"),
            "boss_active_slider_target_xy": state.get("active_slider_target_xy"),
            "boss_active_slider_screen_xy": state.get("active_slider_screen_xy"),
            "boss_follow_lag_ms": state.get("follow_lag_ms"),
            "boss_rect_mode": state.get("rect_mode"),
            "boss_playfield_transform": state.get("playfield_transform"),
            "boss_rect_auto_mode": state.get("rect_auto_mode"),
            "boss_rect_source": state.get("rect_source"),
            "boss_env_playfield_rect": state.get("env_playfield_rect"),
            "boss_client_full_rect": state.get("client_full_rect"),
            "boss_aspect_fit_playfield_rect": state.get("aspect_fit_playfield_rect"),
            "boss_osu_pilot_rect": state.get("osu_pilot_rect"),
            "boss_official_shift_rect": state.get("official_shift_rect"),
            "boss_resolved_playfield_rect": state.get("resolved_playfield_rect"),
            "boss_playfield_scale": state.get("playfield_scale"),
            "boss_playfield_margin_x": state.get("playfield_margin_x"),
            "boss_playfield_margin_y": state.get("playfield_margin_y"),
            "boss_vertical_shift_px": state.get("vertical_shift_px"),
            "boss_osu_window_rect": state.get("osu_window_rect"),
            "boss_osu_client_rect": state.get("osu_client_rect"),
            "boss_monitor_size": state.get("monitor_size"),
            "boss_virtual_screen": state.get("virtual_screen"),
            "boss_dpi_scale": state.get("dpi_scale"),
            "boss_last_input_kind": state.get("last_input_kind"),
            "boss_last_target_source": state.get("last_target_source"),
            "boss_last_target_playfield_xy": state.get("last_target_playfield_xy"),
            "boss_last_target_screen_xy": state.get("last_target_screen_xy"),
            "boss_last_tosu_time_ms": state.get("last_tosu_time_ms"),
            "boss_time_candidates": state.get("time_candidates"),
            "boss_selected_time_source": state.get("selected_time_source"),
            "boss_selected_time_ms": state.get("selected_time_ms"),
            "boss_time_sane": state.get("time_sane"),
            "boss_time_sanity_reason": state.get("time_sanity_reason"),
            "boss_first_object_time_ms": state.get("first_object_time_ms"),
            "boss_last_object_end_time_ms": state.get("last_object_end_time_ms"),
            "boss_time_vs_first_ms": state.get("time_vs_first_ms"),
            "boss_time_vs_last_ms": state.get("time_vs_last_ms"),
            "boss_start_max_late_ms": state.get("start_max_late_ms"),
            "boss_late_start_hold": state.get("late_start_hold"),
            "boss_late_start_reason": state.get("late_start_reason"),
            "boss_movement_armed": state.get("movement_armed"),
            "boss_last_effective_time_ms": state.get("last_effective_time_ms"),
            "boss_last_path_index": state.get("last_path_index"),
            "boss_start_offset": state.get("start_offset"),
            "boss_is_ready": state.get("is_ready"),
            "boss_is_playing": state.get("is_playing"),
            "boss_timer_mode": state.get("timer_mode"),
            "boss_normalized_path_len": state.get("normalized_path_len"),
            "boss_move_count": state.get("move_count"),
            "boss_last_move_xy": state.get("last_move_xy"),
            "boss_last_move_error": state.get("last_move_error"),
            "boss_heartbeat_interval_ms": state.get("heartbeat_interval_ms"),
            "boss_debug_log_enabled": state.get("debug_log_enabled"),
        }
    )
    return fields


def _raw_menu_state(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return None
    menu = raw.get("menu")
    if not isinstance(menu, dict):
        return None
    return menu.get("state")


def _menu_state_is_gameplay(value: Any) -> bool:
    try:
        return int(str(value).strip()) == 2
    except (TypeError, ValueError):
        return False


def _menu_state_is_known(value: Any) -> bool:
    try:
        int(str(value).strip())
        return True
    except (TypeError, ValueError):
        return False


def _nested_value(data: Any, path: str) -> Any:
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _has_explicit_gameplay_signal(state: dict[str, Any]) -> bool:
    raw = state.get("raw")
    if not isinstance(raw, dict):
        return False
    for path in (
        "play.time.current",
        "play.time.live",
        "gameplay.time.current",
        "gameplay.time.live",
        "beatmap.time.live",
    ):
        value = _nested_value(raw, path)
        if value is not None:
            try:
                return int(float(value)) >= 0
            except (TypeError, ValueError):
                return True
    return False


def _activity_detection(state: dict[str, Any]) -> dict[str, Any]:
    raw = state.get("raw")
    raw_state = _raw_menu_state(raw)
    diagnostics = {
        "activity_state": "unknown",
        "lifecycle_activity_source": "unavailable",
        "raw_menu_state": raw_state,
        "raw_menu_state_type": type(raw_state).__name__ if raw_state is not None else None,
        "activity_detection_error": None,
    }

    if raw_state is not None:
        if _menu_state_is_gameplay(raw_state):
            diagnostics["activity_state"] = "gameplay"
            diagnostics["lifecycle_activity_source"] = "raw_menu_state"
            return diagnostics
        if _menu_state_is_known(raw_state):
            diagnostics["activity_state"] = "non_gameplay"
            diagnostics["lifecycle_activity_source"] = "raw_menu_state"
            return diagnostics

    if isinstance(raw, dict) and isinstance(raw.get("menu"), dict) and raw_state is None:
        diagnostics["activity_state"] = "unknown"
        diagnostics["lifecycle_activity_source"] = "raw_menu_state_missing"
        return diagnostics

    if _has_explicit_gameplay_signal(state):
        diagnostics["activity_state"] = "gameplay"
        diagnostics["lifecycle_activity_source"] = "explicit_gameplay_time"
        return diagnostics

    try:
        readiness_activity = read_result_source_snapshot(bridge_state=state).get("activity_state") or "unknown"
        if readiness_activity in {"menu", "result", "break", "non_gameplay"}:
            diagnostics["activity_state"] = "non_gameplay"
            diagnostics["lifecycle_activity_source"] = "result_source_snapshot"
            return diagnostics
        diagnostics["lifecycle_activity_source"] = "result_source_snapshot"
    except Exception as exc:
        diagnostics["activity_detection_error"] = f"{type(exc).__name__}:{exc}"

    return diagnostics


def _activity_state(raw: Any) -> str | None:
    return _activity_detection({"raw": raw, "beatmap": {}, "current_time_ms": None}).get("activity_state", "unknown")


def _apply_activity_diagnostics(payload: dict[str, Any], diagnostics: dict[str, Any]) -> dict[str, Any]:
    payload["activity_state"] = diagnostics.get("activity_state")
    payload["lifecycle_activity_source"] = diagnostics.get("lifecycle_activity_source")
    payload["raw_menu_state"] = diagnostics.get("raw_menu_state")
    payload["raw_menu_state_type"] = diagnostics.get("raw_menu_state_type")
    payload["activity_detection_error"] = diagnostics.get("activity_detection_error")
    return payload


def _beatmap_state(state: dict[str, Any]) -> dict[str, Any]:
    beatmap = dict(state.get("beatmap") or {})
    raw = beatmap.get("raw") if isinstance(beatmap.get("raw"), dict) else {}
    return {
        "raw": raw,
        "id": beatmap.get("id") or raw.get("id"),
        "set_id": beatmap.get("set_id") or raw.get("set"),
        "artist": beatmap.get("artist") or raw.get("artist") or raw.get("artistUnicode"),
        "title": beatmap.get("title") or raw.get("title") or raw.get("titleUnicode"),
        "version": beatmap.get("version") or raw.get("version"),
        "creator": beatmap.get("creator") or raw.get("mapper") or raw.get("creator"),
        "path": beatmap.get("path"),
        "folder": beatmap.get("folder"),
        "file": beatmap.get("file"),
        "source": state.get("source"),
    }


def _write_core_runtime_state(event: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    _supervise_process()
    running = _PROCESS is not None and _PROCESS.poll() is None
    model_path = _current_model_path({})
    state = read_tosu_state()
    activity = _activity_detection(state)
    payload = {
        "schema": "nana.osu.vendor_runtime_state.v1",
        "adapter": "osu",
        "source": "nana_vendor_bot_runtime",
        "created_at": time.time(),
        "current_time_ms": state.get("current_time_ms"),
        "activity_state": activity.get("activity_state"),
        "lifecycle_activity_source": activity.get("lifecycle_activity_source"),
        "raw_menu_state": activity.get("raw_menu_state"),
        "raw_menu_state_type": activity.get("raw_menu_state_type"),
        "activity_detection_error": activity.get("activity_detection_error"),
        "beatmap": _beatmap_state(state),
        "skill": {
            "name": "osu_boss",
            "mode": "vendor_auto_bot",
            "active": running,
        },
        "runtime": {
            "event": event,
            "runner": "vendor_auto_bot",
            "running": running,
            "pid": _PROCESS.pid if running and _PROCESS is not None else None,
            "script": _SCRIPT_NAME,
            "uptime_ms": int((time.time() - _STARTED_AT) * 1000) if running and _STARTED_AT else 0,
            "vendor_root": str(VENDOR_ROOT),
            "model_path": str(model_path) if model_path else None,
            "model_present": bool(model_path and model_path.exists()),
            "real_input": running,
            "submit": False,
            "blockers": [],
            **_crash_diagnostics(),
            **_boss_state_fields(),
            **_kill_switch_fields(),
        },
        "safety": _safety_flags(),
    }
    if extra:
        payload["runtime"].update(extra)
    try:
        path = write_state(payload)
        return {"state_write": True, "state_path": str(path), "state_error": None}
    except Exception as exc:
        return {"state_write": False, "state_path": str(_runtime_state_path()), "state_error": f"{type(exc).__name__}:{exc}"}


def _write_vendor_boss_killed_state(*, event: str, trigger: str, mode: str, killed_pids: list[int], error: str | None) -> dict[str, Any]:
    payload = {
        "schema": "nana.osu.vendor_boss_state.v1",
        "created_at": time.time(),
        "process_pid": None,
        "ws_connected": False,
        "ws_message_count": None,
        "last_ws_error": None,
        "last_ws_message_at": None,
        "raw_menu_state": None,
        "activity_state": "stopped",
        "beatmap_path": None,
        "beatmap_original_path": None,
        "beatmap_parser_path": None,
        "beatmap_parser_path_exists": False,
        "beatmap_path_exists": False,
        "beatmap_path_source": None,
        "beatmap_processed": False,
        "beatmap_process_error": None,
        "beatmap_process_status": "killed",
        "aim_mode": None,
        "slider_mode": None,
        "spinner_mode": None,
        "slider_mode_effective": None,
        "spinner_mode_effective": None,
        "simple_slider_path_mode": None,
        "simple_slider_path_len": 0,
        "simple_slider_path_num_points": None,
        "simple_slider_progress_total": None,
        "simple_slider_slide_index": None,
        "simple_slider_local_progress": None,
        "simple_slider_dense_follow_count": 0,
        "simple_slider_sparse_follow_count": 0,
        "slider_output_smoothing_enabled": None,
        "slider_output_smoothed_count": 0,
        "slider_output_raw_xy": None,
        "slider_output_smoothed_xy": None,
        "input_move_called": False,
        "sendinput_move_count": 0,
        "click_enabled": False,
        "click_count": 0,
        "tap_count": 0,
        "hit_keys": None,
        "hit_offset_ms": None,
        "hit_window_ms": None,
        "slider_follow_count": 0,
        "spinner_spin_count": 0,
        "simple_slider_follow_count": 0,
        "pilot_slider_follow_count": 0,
        "simple_spinner_spin_count": 0,
        "pilot_spinner_spin_count": 0,
        "hold_count": 0,
        "key_down_count": 0,
        "key_up_count": 0,
        "last_hit_key": None,
        "last_action_type": None,
        "last_action_time_ms": None,
        "last_action_scheduled_time_ms": None,
        "last_action_effective_time_ms": None,
        "last_action_delta_ms": None,
        "last_action_error": None,
        "tap_attempt_count": 0,
        "tap_on_time_count": 0,
        "tap_early_count": 0,
        "tap_late_count": 0,
        "slider_start_count": 0,
        "spinner_start_count": 0,
        "slider_duration_ms": None,
        "slider_path_len": 0,
        "slider_slide_count": None,
        "slider_follow_sample_count": 0,
        "slider_last_progress": None,
        "slider_last_xy": None,
        "spinner_rpm": None,
        "spinner_radius": None,
        "spinner_move_count": 0,
        "spinner_last_angle": None,
        "long_hold_active": False,
        "long_hold_key": None,
        "long_hold_started_at": None,
        "long_hold_expected_end": None,
        "selected_action_index": None,
        "previous_selected_action_index": None,
        "selected_index_delta": None,
        "skipped_object_count": 0,
        "selected_action_type": None,
        "previous_action_type": None,
        "selected_action_time_ms": None,
        "selected_action_end_time_ms": None,
        "time_to_selected_start_ms": None,
        "time_to_selected_end_ms": None,
        "next_object_index": None,
        "next_object_type": None,
        "next_object_time_ms": None,
        "time_to_next_object_ms": None,
        "active_hold_index": None,
        "active_hold_type": None,
        "active_hold_key": None,
        "loop_interval_ms": None,
        "move_interval_ms": None,
        "slider_move_interval_ms": None,
        "slider_target_delta": None,
        "slider_screen_delta": None,
        "slider_progress": None,
        "slider_jitter_warning_count": 0,
        "note_skip_warning_count": 0,
        "hold_blocks_next_warning_count": 0,
        "scheduler_enabled": False,
        "scheduler_queue_len": 0,
        "scheduler_next_event_type": None,
        "scheduler_next_event_time_ms": None,
        "scheduler_last_event_type": None,
        "scheduler_last_event_delta_ms": None,
        "scheduler_key_down_count": 0,
        "scheduler_key_up_count": 0,
        "active_follow_type": None,
        "active_follow_index": None,
        "active_follow_start_time_ms": None,
        "active_follow_end_time_ms": None,
        "active_follow_progress": None,
        "active_slider_follow_hz": None,
        "active_slider_follow_interval_ms": None,
        "active_slider_path_len": 0,
        "active_slider_target_xy": None,
        "active_slider_screen_xy": None,
        "follow_lag_ms": None,
        "rect_mode": None,
        "rect_auto_mode": None,
        "playfield_transform": None,
        "rect_source": None,
        "env_playfield_rect": None,
        "client_full_rect": None,
        "aspect_fit_playfield_rect": None,
        "osu_pilot_rect": None,
        "official_shift_rect": None,
        "resolved_playfield_rect": None,
        "playfield_scale": None,
        "playfield_margin_x": None,
        "playfield_margin_y": None,
        "vertical_shift_px": None,
        "osu_window_rect": None,
        "osu_client_rect": None,
        "monitor_size": None,
        "virtual_screen": None,
        "dpi_scale": None,
        "last_input_kind": None,
        "last_target_source": None,
        "last_target_playfield_xy": None,
        "last_target_screen_xy": None,
        "last_tosu_time_ms": None,
        "last_effective_time_ms": None,
        "last_path_index": None,
        "start_offset": 0,
        "is_ready": False,
        "normalized_path_len": 0,
        "is_playing": False,
        "timer_mode": "STOPPED",
        "last_tosu_time": None,
        "tosu_last_update": None,
        "cached_window_rect_present": False,
        "last_move_at": None,
        "move_count": 0,
        "last_move_xy": None,
        "last_move_error": None,
        "status_reason": "killed",
        "runtime_event": event,
        "killed": True,
        "kill_trigger": trigger,
        "kill_mode": mode,
        "killed_pids": killed_pids,
        "kill_error": error,
    }
    path = _vendor_boss_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
        return {"boss_state_write": True, "boss_state_path": str(path), "boss_state_error": None}
    except Exception as exc:
        return {"boss_state_write": False, "boss_state_path": str(path), "boss_state_error": f"{type(exc).__name__}:{exc}"}


def _current_model_path(flags: dict[str, str]) -> Path | None:
    raw = flags.get("model-path")
    if raw:
        return Path(raw)
    nana_raw = os.getenv(NANA_MODEL_ENV)
    if nana_raw:
        return Path(nana_raw)
    env_raw = os.getenv(MODEL_ENV)
    if env_raw:
        env_path = Path(env_raw)
        if env_path.exists():
            return env_path
    return DEFAULT_VENDOR_MODEL


def _current_map_path(flags: dict[str, str]) -> Path | None:
    raw = flags.get("map-path") or os.getenv(MAP_ENV)
    if raw:
        return Path(raw)
    try:
        state = read_tosu_state()
    except Exception:
        return None
    beatmap = state.get("beatmap") or {}
    path = beatmap.get("path")
    return Path(str(path)) if path else None


def _resolve_runtime_config(flags: dict[str, str], *, mode: str = "auto_bot") -> tuple[Path | None, Path | None, list[str]]:
    blockers: list[str] = []
    deps = _dependency_status()
    for name, ready in deps.items():
        if not ready:
            blockers.append(f"dependency_missing:{name}")
    model_path = _current_model_path(flags)
    map_path = _current_map_path(flags)
    if model_path is None:
        blockers.append("model_path_missing")
    elif not model_path.exists():
        blockers.append("model_path_not_found")
    if mode == "auto_bot":
        return model_path, map_path, blockers
    if map_path is None:
        blockers.append("map_path_missing")
    elif not map_path.exists():
        blockers.append("map_path_not_found")
    return model_path, map_path, blockers


def _stop_watcher_thread() -> bool:
    global _WATCHER_THREAD, _WATCHER_STOP, _WATCHER_STARTED_AT, _WATCHER_LAST_PAYLOAD

    was_running = _WATCHER_THREAD is not None and _WATCHER_THREAD.is_alive()
    if _WATCHER_STOP is not None:
        _WATCHER_STOP.set()
    if _WATCHER_THREAD is not None:
        _WATCHER_THREAD.join(timeout=2.0)
    _WATCHER_THREAD = None
    _WATCHER_STOP = None
    _WATCHER_STARTED_AT = None
    with _WATCHER_LOCK:
        _WATCHER_LAST_PAYLOAD = None
    return was_running


def _call_emergency_release() -> dict[str, Any]:
    try:
        return run_emergency_release(
            "/osu-emergency-release --operator-approval-token=I_APPROVE_OSU_EMERGENCY_RELEASE"
        )
    except Exception as exc:
        return {"decision": "release_error", "error": f"{type(exc).__name__}:{exc}"}


def _terminate_process_tree(pid: int) -> tuple[bool, str | None]:
    if not pid:
        return False, "pid_missing"
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if completed.returncode == 0:
                return True, None
            stderr = (completed.stderr or completed.stdout or "").strip()
            return False, stderr or f"taskkill_returncode:{completed.returncode}"
        except Exception as exc:
            return False, f"{type(exc).__name__}:{exc}"
    try:
        os.kill(int(pid), 9)
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}:{exc}"


def _tracked_process_pid() -> int | None:
    if _PROCESS is None:
        return None
    try:
        if _PROCESS.poll() is None:
            return int(_PROCESS.pid)
    except Exception:
        return None
    return None


def _kill_tracked_process_tree() -> tuple[list[int], list[str]]:
    global _PROCESS, _STARTED_AT, _SCRIPT_NAME

    killed_pids: list[int] = []
    errors: list[str] = []
    pid = _tracked_process_pid()
    if pid is None:
        _PROCESS = None if _PROCESS is not None and _PROCESS.poll() is not None else _PROCESS
        return killed_pids, errors
    ok, error = _terminate_process_tree(pid)
    if ok:
        killed_pids.append(pid)
    elif error:
        errors.append(f"tracked_process:{pid}:{error}")
        try:
            if _PROCESS is not None:
                _PROCESS.kill()
                killed_pids.append(pid)
        except Exception as exc:
            errors.append(f"tracked_process_kill:{pid}:{type(exc).__name__}:{exc}")
    _PROCESS = None
    _STARTED_AT = None
    _SCRIPT_NAME = None
    return killed_pids, errors


def _normalize_command_line(value: Any) -> str:
    return str(value or "").replace("/", "\\").lower()


def _is_vendor_auto_bot_command_line(command_line: Any) -> bool:
    normalized = _normalize_command_line(command_line)
    vendor_path = str(VENDOR_AUTO_BOT).replace("/", "\\").lower()
    return vendor_path in normalized or "osu_ai_bot_main\\auto_bot.py" in normalized


def _scan_process_command_lines() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    try:
        completed = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                "name='python.exe' or name='pythonw.exe'",
                "get",
                "ProcessId,CommandLine",
                "/format:csv",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    rows: list[dict[str, Any]] = []
    try:
        reader = csv.DictReader(io.StringIO(completed.stdout))
        for row in reader:
            pid_raw = row.get("ProcessId")
            command_line = row.get("CommandLine")
            try:
                pid = int(str(pid_raw).strip())
            except Exception:
                continue
            rows.append({"pid": pid, "command_line": command_line})
    except Exception:
        return []
    return rows


def _kill_stale_vendor_processes() -> tuple[list[int], list[str]]:
    current_pid = os.getpid()
    killed_pids: list[int] = []
    errors: list[str] = []
    for proc in _scan_process_command_lines():
        pid = proc.get("pid")
        if not isinstance(pid, int) or pid == current_pid:
            continue
        if not _is_vendor_auto_bot_command_line(proc.get("command_line")):
            continue
        ok, error = _terminate_process_tree(pid)
        if ok:
            killed_pids.append(pid)
        elif error:
            errors.append(f"stale_process:{pid}:{error}")
    return killed_pids, errors


def build_vendor_boss_kill_payload(text: str = "", *, kill_all: bool = False, trigger: str = "command") -> dict[str, Any]:
    global _LAST_KILL_CALLED_AT, _LAST_KILL_TRIGGER, _LAST_KILL_MODE, _LAST_KILLED_PIDS, _LAST_KILL_ERROR

    with _KILL_LOCK:
        _LAST_KILL_CALLED_AT = time.time()
        _LAST_KILL_TRIGGER = trigger
        _LAST_KILL_MODE = "kill_all" if kill_all else "tracked"
        killed_pids: list[int] = []
        errors: list[str] = []

        watcher_was_running = _stop_watcher_thread()
        release_before = _call_emergency_release()
        tracked_killed, tracked_errors = _kill_tracked_process_tree()
        killed_pids.extend(tracked_killed)
        errors.extend(tracked_errors)
        stale_killed: list[int] = []
        if kill_all:
            stale_killed, stale_errors = _kill_stale_vendor_processes()
            killed_pids.extend(stale_killed)
            errors.extend(stale_errors)
        release_after = _call_emergency_release()

        _LAST_KILLED_PIDS = sorted(set(int(pid) for pid in killed_pids))
        _LAST_KILL_ERROR = "; ".join(errors) if errors else None

        payload = _base_payload("vendor_boss_kill_switch", "killed")
        payload.update(_watcher_fields())
        payload.update(
            {
                "decision": "vendor_boss_kill_switch",
                "reason": "killed",
                "killed": True,
                "kill_trigger": trigger,
                "kill_mode": _LAST_KILL_MODE,
                "watcher_was_running": watcher_was_running,
                "watcher_running": False,
                "running": False,
                "pid": None,
                "script": None,
                "uptime_ms": 0,
                "real_input": False,
                "tracked_killed_pids": tracked_killed,
                "stale_killed_pids": stale_killed,
                "killed_pids": list(_LAST_KILLED_PIDS),
                "last_killed_pids": list(_LAST_KILLED_PIDS),
                "release_called": True,
                "release_before": release_before,
                "release_after": release_after,
                "last_kill_error": _LAST_KILL_ERROR,
                "blockers": [],
            }
        )
        payload.update(_kill_switch_fields())
        boss_state = _write_vendor_boss_killed_state(
            event="kill_switch",
            trigger=trigger,
            mode=_LAST_KILL_MODE,
            killed_pids=list(_LAST_KILLED_PIDS),
            error=_LAST_KILL_ERROR,
        )
        payload.update(boss_state)
        payload.update(
            _write_core_runtime_state(
                "kill_switch",
                extra={
                    "running": False,
                    "real_input": False,
                    "killed": True,
                    "kill_trigger": trigger,
                    "kill_mode": _LAST_KILL_MODE,
                    "killed_pids": list(_LAST_KILLED_PIDS),
                    "release_called": True,
                    "last_kill_error": _LAST_KILL_ERROR,
                },
            )
        )
        return payload


def build_vendor_boss_kill_all_payload(text: str = "", *, trigger: str = "command") -> dict[str, Any]:
    return build_vendor_boss_kill_payload(text, kill_all=True, trigger=trigger)


def build_kill_switch_status_payload(text: str = "") -> dict[str, Any]:
    payload = _base_payload("osu_kill_switch_status", "status_only")
    payload.update(_watcher_fields())
    payload.update(_kill_switch_fields())
    payload["blockers"] = []
    payload.update(_write_core_runtime_state("kill_switch_status"))
    return payload


def _kill_switch_hotkey_callback() -> None:
    print("OSU KILL SWITCH DEL TRIGGERED", flush=True)
    try:
        build_vendor_boss_kill_all_payload("/osu-kill-switch", trigger="hotkey")
    except Exception as exc:
        global _LAST_KILL_CALLED_AT, _LAST_KILL_TRIGGER, _LAST_KILL_MODE, _LAST_KILL_ERROR
        _LAST_KILL_CALLED_AT = time.time()
        _LAST_KILL_TRIGGER = "hotkey"
        _LAST_KILL_MODE = "kill_all"
        _LAST_KILL_ERROR = f"{type(exc).__name__}:{exc}"
        print(f"OSU KILL SWITCH ERROR: {_LAST_KILL_ERROR}", flush=True)


def register_kill_switch_hotkey() -> dict[str, Any]:
    global _KILL_HOTKEY_REGISTERED, _KILL_HOTKEY_HANDLE, _KILL_HOTKEY_ERROR

    if _KILL_HOTKEY_REGISTERED:
        return _kill_switch_fields()
    if not _kill_hotkey_enabled_by_env():
        _KILL_HOTKEY_ERROR = None
        return _kill_switch_fields()
    try:
        import keyboard

        key = _kill_hotkey_key()
        _KILL_HOTKEY_HANDLE = keyboard.add_hotkey(key, _kill_switch_hotkey_callback)
        _KILL_HOTKEY_REGISTERED = True
        _KILL_HOTKEY_ERROR = None
    except Exception as exc:
        _KILL_HOTKEY_REGISTERED = False
        _KILL_HOTKEY_HANDLE = None
        _KILL_HOTKEY_ERROR = f"{type(exc).__name__}:{exc}"
    return _kill_switch_fields()


def _atexit_cleanup() -> None:
    try:
        if (_PROCESS is not None and _PROCESS.poll() is None) or (_WATCHER_THREAD is not None and _WATCHER_THREAD.is_alive()):
            build_vendor_boss_kill_payload("/osu-vendor-boss-kill", kill_all=False, trigger="atexit")
    except Exception:
        pass


def build_vendor_bot_status_payload(text: str = "") -> dict[str, Any]:
    flags = _parse_flags(text)
    mode = flags.get("mode", "auto_bot")
    model_path, map_path, blockers = _resolve_runtime_config(flags, mode=mode)
    payload = _base_payload("vendor_bot_runtime_status", "status_only")
    payload["model_path"] = str(model_path) if model_path else None
    payload["model_present"] = bool(model_path and model_path.exists())
    payload["map_path"] = str(map_path) if map_path else None
    payload["map_present"] = bool(map_path and map_path.exists())
    payload["blockers"] = blockers
    payload.update(_write_core_runtime_state("status"))
    return payload


def build_vendor_bot_start_payload(text: str = "") -> dict[str, Any]:
    global _PROCESS, _STARTED_AT, _SCRIPT_NAME
    global _LAST_EXIT_CODE, _LAST_EXIT_AT, _LAST_RUNTIME_MS, _LAST_CRASH_REASON

    flags = _parse_flags(text)
    mode = flags.get("mode", "auto_bot")
    script = _script_from_mode(mode)
    model_path, map_path, blockers = _resolve_runtime_config(flags, mode=mode)
    backoff_remaining = _restart_backoff_remaining_ms()

    if backoff_remaining > 0:
        payload = _base_payload("vendor_bot_runtime_hold", "recent_crash_backoff")
        payload["requested_mode"] = mode
        payload["requested_script"] = str(script)
        payload["blockers"] = ["recent_crash_backoff"]
        payload.update(_write_core_runtime_state("start_blocked_recent_crash", extra={"blockers": ["recent_crash_backoff"]}))
        return payload

    if _PROCESS is not None and _PROCESS.poll() is None:
        payload = _base_payload("vendor_bot_runtime_already_running", "already_running")
        payload["requested_mode"] = mode
        payload.update(_write_core_runtime_state("already_running"))
        return payload

    if not VENDOR_ROOT.exists():
        return _base_payload("vendor_bot_runtime_hold", "vendor_root_missing")
    if not script.exists():
        payload = _base_payload("vendor_bot_runtime_hold", "script_missing")
        payload["requested_mode"] = mode
        payload["requested_script"] = str(script)
        return payload
    if blockers:
        payload = _base_payload("vendor_bot_runtime_hold", "runtime_config_not_ready")
        payload["requested_mode"] = mode
        payload["requested_script"] = str(script)
        payload["model_path"] = str(model_path) if model_path else None
        payload["model_present"] = bool(model_path and model_path.exists())
        payload["map_path"] = str(map_path) if map_path else None
        payload["map_present"] = bool(map_path and map_path.exists())
        payload["blockers"] = blockers
        payload.update(_write_core_runtime_state("start_blocked", extra={"blockers": blockers}))
        return payload

    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(VENDOR_ROOT))
    env[MODEL_ENV] = str(model_path)
    env[VENDOR_RUNTIME_STATE_ENV] = str(_runtime_state_path())
    if map_path:
        env[MAP_ENV] = str(map_path)
    env.setdefault("NANA_OSU_VENDOR_WIGGLE_ENABLED", "0")

    _PROCESS = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(VENDOR_ROOT),
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    _STARTED_AT = time.time()
    _SCRIPT_NAME = script.name
    _LAST_EXIT_CODE = None
    _LAST_EXIT_AT = None
    _LAST_RUNTIME_MS = None
    _LAST_CRASH_REASON = None

    payload = _base_payload("vendor_bot_runtime_started", "process_started")
    payload["requested_mode"] = mode
    payload["requested_script"] = str(script)
    payload["model_path"] = str(model_path)
    payload["model_present"] = True
    payload["map_path"] = str(map_path) if map_path else None
    payload["map_present"] = bool(map_path and map_path.exists())
    payload["blockers"] = []
    payload.update(_write_core_runtime_state("started"))
    return payload


def build_vendor_boss_lifecycle_payload(text: str = "") -> dict[str, Any]:
    flags = _parse_flags(text)
    mode = "auto_bot"
    _supervise_process()
    state = read_tosu_state()
    activity = _activity_detection(state)
    activity_state = activity.get("activity_state") or "unknown"
    running = _PROCESS is not None and _PROCESS.poll() is None

    if activity_state == "gameplay":
        if running:
            payload = _base_payload("vendor_boss_lifecycle_status", "already_running")
            payload["lifecycle_action"] = "already_running"
            payload["boss_skill_active"] = True
            _apply_activity_diagnostics(payload, activity)
            payload["blockers"] = []
            payload.update(_write_core_runtime_state("lifecycle_already_running"))
            return payload

        backoff_remaining = _restart_backoff_remaining_ms()
        if backoff_remaining > 0:
            payload = _base_payload("vendor_boss_lifecycle_blocked", "recent_crash_backoff")
            payload["lifecycle_action"] = "start_blocked_recent_crash"
            payload["boss_skill_active"] = False
            _apply_activity_diagnostics(payload, activity)
            payload["blockers"] = ["recent_crash_backoff"]
            payload.update(_write_core_runtime_state("lifecycle_start_blocked_recent_crash", extra={"blockers": ["recent_crash_backoff"]}))
            return payload

        script = _script_from_mode(mode)
        model_path, map_path, blockers = _resolve_runtime_config(flags, mode=mode)
        if not VENDOR_ROOT.exists():
            blockers.append("vendor_root_missing")
        if not script.exists():
            blockers.append("script_missing")
        if blockers:
            payload = _base_payload("vendor_boss_lifecycle_blocked", "runtime_config_not_ready")
            payload["lifecycle_action"] = "blocked"
            payload["boss_skill_active"] = False
            _apply_activity_diagnostics(payload, activity)
            payload["requested_mode"] = mode
            payload["requested_script"] = str(script)
            payload["model_path"] = str(model_path) if model_path else None
            payload["model_present"] = bool(model_path and model_path.exists())
            payload["map_path"] = str(map_path) if map_path else None
            payload["map_present"] = bool(map_path and map_path.exists())
            payload["blockers"] = blockers
            payload.update(_write_core_runtime_state("lifecycle_blocked", extra={"blockers": blockers}))
            return payload

        payload = build_vendor_bot_start_payload("/osu-vendor-bot-start --mode=auto_bot")
        payload["decision"] = "vendor_boss_lifecycle_started"
        payload["reason"] = "gameplay_started_boss"
        payload["lifecycle_action"] = "started"
        payload["boss_skill_active"] = bool(_PROCESS is not None and _PROCESS.poll() is None)
        _apply_activity_diagnostics(payload, activity)
        payload.update(_write_core_runtime_state("lifecycle_started"))
        return payload

    if running:
        payload = _base_payload("vendor_boss_lifecycle_status", "prewarmed_idle")
        payload["lifecycle_action"] = "prewarmed_idle"
        payload["boss_skill_active"] = False
        _apply_activity_diagnostics(payload, activity)
        payload["blockers"] = []
        payload.update(_write_core_runtime_state("lifecycle_prewarmed_idle"))
        return payload

    backoff_remaining = _restart_backoff_remaining_ms()
    if backoff_remaining > 0:
        payload = _base_payload("vendor_boss_lifecycle_blocked", "recent_crash_backoff")
        payload["lifecycle_action"] = "start_blocked_recent_crash"
        payload["boss_skill_active"] = False
        _apply_activity_diagnostics(payload, activity)
        payload["blockers"] = ["recent_crash_backoff"]
        payload.update(_write_core_runtime_state("lifecycle_prewarm_blocked_recent_crash", extra={"blockers": ["recent_crash_backoff"]}))
        return payload

    payload = build_vendor_bot_start_payload("/osu-vendor-bot-start --mode=auto_bot")
    _apply_activity_diagnostics(payload, activity)
    if _PROCESS is not None and _PROCESS.poll() is None:
        payload["decision"] = "vendor_boss_lifecycle_prewarmed"
        payload["reason"] = "prewarmed_idle"
        payload["lifecycle_action"] = "prewarmed_idle"
        payload["boss_skill_active"] = False
        payload.update(_write_core_runtime_state("lifecycle_prewarmed_idle"))
        return payload

    payload["decision"] = "vendor_boss_lifecycle_blocked"
    payload["lifecycle_action"] = "blocked"
    payload["boss_skill_active"] = False
    payload.setdefault("blockers", [payload.get("reason") or "prewarm_start_failed"])
    payload.update(_write_core_runtime_state("lifecycle_prewarm_blocked", extra={"blockers": payload.get("blockers")}))
    return payload


def _parse_interval_ms(flags: dict[str, str]) -> int:
    raw = flags.get("interval-ms")
    if raw is None:
        return 500
    try:
        value = int(raw)
    except ValueError:
        return 500
    return max(100, min(5000, value))


def _watcher_loop() -> None:
    global _WATCHER_LAST_PAYLOAD
    while _WATCHER_STOP is not None and not _WATCHER_STOP.is_set():
        try:
            payload = build_vendor_boss_lifecycle_payload("/osu-vendor-boss-lifecycle --mode=auto_bot")
        except Exception as exc:
            payload = _base_payload("vendor_boss_auto_error", "lifecycle_error")
            payload["lifecycle_action"] = "blocked"
            payload["boss_skill_active"] = _PROCESS is not None and _PROCESS.poll() is None
            payload["activity_state"] = "unknown"
            payload["blockers"] = [f"lifecycle_error:{type(exc).__name__}"]
            payload["error"] = str(exc)
            payload.update(_write_core_runtime_state("watcher_lifecycle_error", extra={"error": str(exc)}))
        with _WATCHER_LOCK:
            _WATCHER_LAST_PAYLOAD = payload
        if _WATCHER_STOP is None:
            break
        _WATCHER_STOP.wait(_WATCHER_INTERVAL_MS / 1000.0)


def build_vendor_boss_auto_start_payload(text: str = "") -> dict[str, Any]:
    global _WATCHER_THREAD, _WATCHER_STOP, _WATCHER_INTERVAL_MS, _WATCHER_STARTED_AT

    flags = _parse_flags(text)
    if _WATCHER_THREAD is not None and _WATCHER_THREAD.is_alive():
        payload = _base_payload("vendor_boss_auto_status", "watcher_already_running")
        payload.update(_watcher_fields())
        payload.update(_write_core_runtime_state("watcher_already_running"))
        return payload

    _WATCHER_INTERVAL_MS = _parse_interval_ms(flags)
    _WATCHER_STOP = threading.Event()
    _WATCHER_THREAD = threading.Thread(target=_watcher_loop, name="nana-osu-vendor-boss-watcher", daemon=True)
    _WATCHER_STARTED_AT = time.time()
    _WATCHER_THREAD.start()

    payload = _base_payload("vendor_boss_auto_started", "watcher_started")
    payload.update(_watcher_fields())
    payload.update(_write_core_runtime_state("watcher_started"))
    return payload


def _watcher_fields() -> dict[str, Any]:
    _supervise_process()
    running = _WATCHER_THREAD is not None and _WATCHER_THREAD.is_alive()
    with _WATCHER_LOCK:
        last_payload = dict(_WATCHER_LAST_PAYLOAD) if isinstance(_WATCHER_LAST_PAYLOAD, dict) else None
    fields = {
        "watcher_running": running,
        "watcher_interval_ms": _WATCHER_INTERVAL_MS,
        "watcher_started_at": _WATCHER_STARTED_AT,
        "watcher_uptime_ms": int((time.time() - _WATCHER_STARTED_AT) * 1000) if running and _WATCHER_STARTED_AT else 0,
        "last_lifecycle_action": last_payload.get("lifecycle_action") if last_payload else None,
        "last_lifecycle_decision": last_payload.get("decision") if last_payload else None,
        "last_activity_state": last_payload.get("activity_state") if last_payload else None,
    }
    fields.update(_crash_diagnostics())
    return fields


def build_vendor_boss_auto_status_payload(text: str = "") -> dict[str, Any]:
    payload = _base_payload("vendor_boss_auto_status", "status_only")
    payload.update(_watcher_fields())
    payload.update(_write_core_runtime_state("watcher_status"))
    return payload


def build_vendor_boss_auto_stop_payload(text: str = "") -> dict[str, Any]:
    flags = _parse_flags(text)
    stop_boss = flags.get("stop-boss", "true").strip().lower() not in {"0", "false", "no", "off"}
    was_running = _stop_watcher_thread()

    stop_payload = None
    if stop_boss and _PROCESS is not None and _PROCESS.poll() is None:
        stop_payload = build_vendor_bot_stop_payload("/osu-vendor-bot-stop")

    payload = _base_payload("vendor_boss_auto_stopped", "watcher_stopped")
    payload["watcher_was_running"] = was_running
    payload["watcher_running"] = False
    payload["watcher_interval_ms"] = _WATCHER_INTERVAL_MS
    payload["stop_boss"] = stop_boss
    payload["boss_stop_called"] = stop_payload is not None
    payload["boss_stop_reason"] = stop_payload.get("reason") if stop_payload else None
    payload["blockers"] = []
    payload.update(_write_core_runtime_state("watcher_stopped", extra={"stop_boss": stop_boss}))
    return payload


def build_vendor_model_install_payload(text: str = "") -> dict[str, Any]:
    flags = _parse_flags(text)
    raw_source = flags.get("source") or flags.get("model-path")
    payload = _base_payload("vendor_model_install_hold", "source_required")
    payload["target_model_path"] = str(DEFAULT_VENDOR_MODEL)
    payload["installed"] = False
    payload["source_model_path"] = raw_source

    if not raw_source:
        payload["blockers"] = ["source_required"]
        payload["install_command_hint"] = "/osu-vendor-model-install --source=D:\\path\\to\\model.keras"
        return payload

    source = Path(raw_source)
    payload["source_model_path"] = str(source)
    payload["source_present"] = source.exists()
    if not source.exists() or not source.is_file():
        payload["reason"] = "source_not_found"
        payload["blockers"] = ["source_not_found"]
        return payload
    if source.suffix.lower() not in {".keras", ".h5", ".hdf5"}:
        payload["reason"] = "unsupported_model_suffix"
        payload["blockers"] = ["unsupported_model_suffix"]
        return payload

    VENDOR_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, DEFAULT_VENDOR_MODEL)
    payload = _base_payload("vendor_model_installed", "model_copied_to_nana_vendor")
    payload["target_model_path"] = str(DEFAULT_VENDOR_MODEL)
    payload["source_model_path"] = str(source)
    payload["source_present"] = True
    payload["installed"] = True
    payload["blockers"] = []
    payload.update(_write_core_runtime_state("model_installed", extra={"source_model_path": str(source)}))
    return payload


def build_vendor_model_heuristic_payload(text: str = "") -> dict[str, Any]:
    VENDOR_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        import numpy as np
        import tensorflow as tf

        model = tf.keras.Sequential(
            [
                tf.keras.layers.Input(shape=(2048, 5), name="osu_features"),
                tf.keras.layers.Dense(2, use_bias=False, trainable=False, name="copy_beatmap_xy_features"),
            ],
            name="nana_osu_heuristic_feature_passthrough_model",
        )
        weights = np.zeros((5, 2), dtype=np.float32)
        weights[0, 0] = 1.0
        weights[1, 1] = 1.0
        model.layers[0].set_weights([weights])
        model.compile(optimizer="adam", loss="mse")
        model.save(DEFAULT_VENDOR_MODEL)
        loaded = tf.keras.models.load_model(DEFAULT_VENDOR_MODEL)
        probe = loaded.predict(np.zeros((1, 2048, 5), dtype=np.float32), verbose=0)
        payload = _base_payload("vendor_model_heuristic_ready", "heuristic_model_built")
        payload.update(
            {
                "model_path": str(DEFAULT_VENDOR_MODEL),
                "model_present": DEFAULT_VENDOR_MODEL.exists(),
                "model_kind": "heuristic_feature_passthrough",
                "input_shape": str(loaded.input_shape),
                "output_shape": str(loaded.output_shape),
                "probe_shape": list(probe.shape),
                "installed": True,
                "blockers": [],
            }
        )
        payload.update(_write_core_runtime_state("heuristic_model_built", extra={"model_kind": "heuristic_feature_passthrough"}))
        return payload
    except Exception as exc:
        payload = _base_payload("vendor_model_heuristic_hold", "heuristic_model_build_failed")
        payload["model_path"] = str(DEFAULT_VENDOR_MODEL)
        payload["installed"] = False
        payload["blockers"] = [f"heuristic_model_build_failed:{type(exc).__name__}"]
        payload["error"] = str(exc)
        payload.update(_write_core_runtime_state("heuristic_model_build_failed", extra={"error": str(exc)}))
    return payload


def build_vendor_bot_stop_payload(text: str = "") -> dict[str, Any]:
    global _PROCESS, _STARTED_AT, _SCRIPT_NAME

    _supervise_process()
    if _PROCESS is None or _PROCESS.poll() is not None:
        _PROCESS = None
        _STARTED_AT = None
        _SCRIPT_NAME = None
        payload = _base_payload("vendor_bot_runtime_stopped", "not_running")
        payload["release_called"] = False
        payload.update(_write_core_runtime_state("stop_not_running"))
        return payload

    pid = _PROCESS.pid
    release_result = None
    try:
        release_result = run_emergency_release(
            "/osu-emergency-release --operator-approval-token=I_APPROVE_OSU_EMERGENCY_RELEASE"
        )
    except Exception as exc:
        release_result = {"decision": "release_error", "error": type(exc).__name__}
    _PROCESS.terminate()
    try:
        _PROCESS.wait(timeout=3)
        reason = "terminated"
    except subprocess.TimeoutExpired:
        _PROCESS.kill()
        _PROCESS.wait(timeout=3)
        reason = "killed_after_timeout"

    _PROCESS = None
    _STARTED_AT = None
    _SCRIPT_NAME = None

    payload = _base_payload("vendor_bot_runtime_stopped", reason)
    payload["stopped_pid"] = pid
    payload["release_called"] = True
    payload["release_result"] = release_result
    payload.update(_write_core_runtime_state("stopped", extra={"stopped_pid": pid, "release_called": True}))
    return payload


def print_vendor_bot_payload(payload: dict[str, Any], *, title: str) -> None:
    print(title)
    print(f"  Decision: {payload.get('decision')}")
    print(f"  Reason: {payload.get('reason')}")
    print(f"  Vendor root: {payload.get('vendor_root')}")
    print(f"  Vendor present: {payload.get('vendor_present')}")
    print(f"  Auto bot present: {payload.get('auto_bot_present')}")
    print(f"  Live play present: {payload.get('live_play_present')}")
    print(f"  Env example present: {payload.get('env_example_present')}")
    print(f"  Running: {payload.get('running')}")
    print(f"  PID: {payload.get('pid')}")
    print(f"  Script: {payload.get('script')}")
    print(f"  Uptime ms: {payload.get('uptime_ms')}")
    print(f"  Model path: {payload.get('model_path')}")
    print(f"  Model present: {payload.get('model_present')}")
    print(f"  Map path: {payload.get('map_path')}")
    print(f"  Map present: {payload.get('map_present')}")
    print(f"  Dependencies ready: {payload.get('dependencies_ready')}")
    print(f"  Dependency status: {payload.get('dependency_status')}")
    if payload.get("activity_state") is not None:
        print(f"  Activity state: {payload.get('activity_state')}")
    if payload.get("lifecycle_activity_source") is not None:
        print(f"  Lifecycle activity source: {payload.get('lifecycle_activity_source')}")
        print(f"  Raw menu state: {payload.get('raw_menu_state')}")
        print(f"  Raw menu state type: {payload.get('raw_menu_state_type')}")
        print(f"  Activity detection error: {payload.get('activity_detection_error')}")
    print(f"  Last exit code: {payload.get('last_exit_code')}")
    print(f"  Last exit at: {payload.get('last_exit_at')}")
    print(f"  Last runtime ms: {payload.get('last_runtime_ms')}")
    print(f"  Restart count: {payload.get('restart_count')}")
    print(f"  Last crash reason: {payload.get('last_crash_reason')}")
    print(f"  Restart backoff ms: {payload.get('restart_backoff_ms')}")
    print(f"  Restart backoff remaining ms: {payload.get('restart_backoff_remaining_ms')}")
    print(f"  Kill switch available: {payload.get('kill_switch_available')}")
    print(f"  Kill hotkey enabled: {payload.get('kill_hotkey_enabled')}")
    print(f"  Kill hotkey registered: {payload.get('kill_hotkey_registered')}")
    print(f"  Kill hotkey key: {payload.get('kill_hotkey_key')}")
    print(f"  Kill hotkey error: {payload.get('kill_hotkey_error')}")
    print(f"  Last kill called at: {payload.get('last_kill_called_at')}")
    print(f"  Last kill trigger: {payload.get('last_kill_trigger')}")
    print(f"  Last kill mode: {payload.get('last_kill_mode')}")
    print(f"  Last killed pids: {payload.get('last_killed_pids')}")
    print(f"  Last kill error: {payload.get('last_kill_error')}")
    print("  Emergency kill: press DEL or run /osu-kill-switch")
    print(f"  Boss status reason: {payload.get('boss_status_reason')}")
    print(f"  Boss ws message count: {payload.get('boss_ws_message_count')}")
    print(f"  Boss last ws error: {payload.get('boss_last_ws_error')}")
    print(f"  Boss last ws message at: {payload.get('boss_last_ws_message_at')}")
    print(f"  Boss raw menu state: {payload.get('boss_raw_menu_state')}")
    print(f"  Boss activity state: {payload.get('boss_activity_state')}")
    print(f"  Boss beatmap path: {payload.get('boss_beatmap_path')}")
    print(f"  Boss beatmap original path: {payload.get('boss_beatmap_original_path')}")
    print(f"  Boss beatmap parser path: {payload.get('boss_beatmap_parser_path')}")
    print(f"  Boss beatmap parser path exists: {payload.get('boss_beatmap_parser_path_exists')}")
    print(f"  Boss beatmap path source: {payload.get('boss_beatmap_path_source')}")
    print(f"  Boss beatmap path exists: {payload.get('boss_beatmap_path_exists')}")
    print(f"  Boss beatmap processed: {payload.get('boss_beatmap_processed')}")
    print(f"  Boss beatmap process error: {payload.get('boss_beatmap_process_error')}")
    print(f"  Boss beatmap process status: {payload.get('boss_beatmap_process_status')}")
    print(f"  Boss aim mode: {payload.get('boss_aim_mode')}")
    print(f"  Boss aim profile: {payload.get('boss_aim_profile')}")
    print(f"  Boss hard snap enabled: {payload.get('boss_hard_snap_enabled')}")
    print(f"  Boss aim profile warning: {payload.get('boss_aim_profile_warning')}")
    print(f"  Boss hard snap lead ms: {payload.get('boss_hard_snap_lead_ms')}")
    print(f"  Boss hard snap max gap ms: {payload.get('boss_hard_snap_max_gap_ms')}")
    print(f"  Boss hard snap target index: {payload.get('boss_hard_snap_target_index')}")
    print(f"  Boss hard snap target type: {payload.get('boss_hard_snap_target_type')}")
    print(f"  Boss hard snap target time ms: {payload.get('boss_hard_snap_target_time_ms')}")
    print(f"  Boss hard snap time to target ms: {payload.get('boss_hard_snap_time_to_target_ms')}")
    print(f"  Boss hard snap selected time ms: {payload.get('boss_hard_snap_selected_time_ms')}")
    print(f"  Boss hard snap snap count: {payload.get('boss_hard_snap_snap_count')}")
    print(f"  Boss hard snap hold count: {payload.get('boss_hard_snap_hold_count')}")
    print(f"  Boss hard snap reason: {payload.get('boss_hard_snap_reason')}")
    print(f"  Boss hard snap target playfield xy: {payload.get('boss_hard_snap_target_playfield_xy')}")
    print(f"  Boss hard snap target screen xy: {payload.get('boss_hard_snap_target_screen_xy')}")
    print(f"  Boss hard snap slider lead ms: {payload.get('boss_hard_snap_slider_lead_ms')}")
    print(f"  Boss hard snap slider speed multiplier: {payload.get('boss_hard_snap_slider_speed_multiplier')}")
    print(f"  Boss hard snap slider exit ms: {payload.get('boss_hard_snap_slider_exit_ms')}")
    print(f"  Boss hard snap slider active: {payload.get('boss_hard_snap_slider_active')}")
    print(f"  Boss hard snap slider index: {payload.get('boss_hard_snap_slider_index')}")
    print(f"  Boss hard snap slider raw progress: {payload.get('boss_hard_snap_slider_raw_progress')}")
    print(f"  Boss hard snap slider effective progress: {payload.get('boss_hard_snap_slider_effective_progress')}")
    print(f"  Boss hard snap slider slide index: {payload.get('boss_hard_snap_slider_slide_index')}")
    print(f"  Boss hard snap slider local progress: {payload.get('boss_hard_snap_slider_local_progress')}")
    print(f"  Boss hard snap slider target playfield xy: {payload.get('boss_hard_snap_slider_target_playfield_xy')}")
    print(f"  Boss hard snap slider target screen xy: {payload.get('boss_hard_snap_slider_target_screen_xy')}")
    print(f"  Boss hard snap slider exit count: {payload.get('boss_hard_snap_slider_exit_count')}")
    print(f"  Boss hard snap slider fallback count: {payload.get('boss_hard_snap_slider_fallback_count')}")
    print(f"  Boss hard snap slider reason: {payload.get('boss_hard_snap_slider_reason')}")
    print(f"  Boss hard snap arbiter mode: {payload.get('boss_hard_snap_arbiter_mode')}")
    print(f"  Boss hard snap arbiter active: {payload.get('boss_hard_snap_arbiter_active')}")
    print(f"  Boss hard snap arbiter bypassed: {payload.get('boss_hard_snap_arbiter_bypassed')}")
    print(f"  Boss hard snap arbiter warning: {payload.get('boss_hard_snap_arbiter_warning')}")
    print(f"  Boss hard snap arbiter enabled: {payload.get('boss_hard_snap_arbiter_enabled')}")
    print(f"  Boss hard snap arbiter winner index: {payload.get('boss_hard_snap_arbiter_winner_index')}")
    print(f"  Boss hard snap arbiter winner type: {payload.get('boss_hard_snap_arbiter_winner_type')}")
    print(f"  Boss hard snap arbiter reason: {payload.get('boss_hard_snap_arbiter_reason')}")
    print(f"  Boss hard snap arbiter previous index: {payload.get('boss_hard_snap_arbiter_previous_index')}")
    print(f"  Boss hard snap arbiter switch count: {payload.get('boss_hard_snap_arbiter_switch_count')}")
    print(f"  Boss hard snap arbiter switch delta ms: {payload.get('boss_hard_snap_arbiter_switch_delta_ms')}")
    print(f"  Boss hard snap arbiter stale skipped count: {payload.get('boss_hard_snap_arbiter_stale_skipped_count')}")
    print(f"  Boss hard snap slider aim owner cleared count: {payload.get('boss_hard_snap_slider_aim_owner_cleared_count')}")
    print(f"  Boss hard snap slider release to next ms: {payload.get('boss_hard_snap_slider_release_to_next_ms')}")
    print(f"  Boss hard snap slider endpoint hold ms: {payload.get('boss_hard_snap_slider_endpoint_hold_ms')}")
    print(f"  Boss input mode: {payload.get('boss_input_mode')}")
    print(f"  Boss key dispatch allowed: {payload.get('boss_key_dispatch_allowed')}")
    print(f"  Boss slider mode: {payload.get('boss_slider_mode')}")
    print(f"  Boss spinner mode: {payload.get('boss_spinner_mode')}")
    print(f"  Boss slider mode effective: {payload.get('boss_slider_mode_effective')}")
    print(f"  Boss spinner mode effective: {payload.get('boss_spinner_mode_effective')}")
    print(f"  Boss simple slider path mode: {payload.get('boss_simple_slider_path_mode')}")
    print(f"  Boss simple slider path len: {payload.get('boss_simple_slider_path_len')}")
    print(f"  Boss simple slider path num points: {payload.get('boss_simple_slider_path_num_points')}")
    print(f"  Boss simple slider progress total: {payload.get('boss_simple_slider_progress_total')}")
    print(f"  Boss simple slider slide index: {payload.get('boss_simple_slider_slide_index')}")
    print(f"  Boss simple slider local progress: {payload.get('boss_simple_slider_local_progress')}")
    print(f"  Boss simple slider dense follow count: {payload.get('boss_simple_slider_dense_follow_count')}")
    print(f"  Boss simple slider sparse follow count: {payload.get('boss_simple_slider_sparse_follow_count')}")
    print(f"  Boss slider output smoothing enabled: {payload.get('boss_slider_output_smoothing_enabled')}")
    print(f"  Boss slider output smoothed count: {payload.get('boss_slider_output_smoothed_count')}")
    print(f"  Boss slider output raw xy: {payload.get('boss_slider_output_raw_xy')}")
    print(f"  Boss slider output smoothed xy: {payload.get('boss_slider_output_smoothed_xy')}")
    print(f"  Boss input move called: {payload.get('boss_input_move_called')}")
    print(f"  Boss SendInput move count: {payload.get('boss_sendinput_move_count')}")
    print(f"  Boss click enabled: {payload.get('boss_click_enabled')}")
    print(f"  Boss click count: {payload.get('boss_click_count')}")
    print(f"  Boss tap count: {payload.get('boss_tap_count')}")
    print(f"  Boss hit keys: {payload.get('boss_hit_keys')}")
    print(f"  Boss hit offset ms: {payload.get('boss_hit_offset_ms')}")
    print(f"  Boss hit window ms: {payload.get('boss_hit_window_ms')}")
    print(f"  Boss slider follow lead ms: {payload.get('boss_slider_follow_lead_ms')}")
    print(f"  Boss slider speed multiplier: {payload.get('boss_slider_speed_multiplier')}")
    print(f"  Boss slider time authority enabled: {payload.get('boss_slider_time_authority_enabled')}")
    print(f"  Boss slider time authority abandon count: {payload.get('boss_slider_time_authority_abandon_count')}")
    print(f"  Boss last slider abandon index: {payload.get('boss_last_slider_abandon_index')}")
    print(f"  Boss last slider abandon overrun ms: {payload.get('boss_last_slider_abandon_overrun_ms')}")
    print(f"  Boss slider exit preaim ms: {payload.get('boss_slider_exit_preaim_ms')}")
    print(f"  Boss slider exit snap ms: {payload.get('boss_slider_exit_snap_ms')}")
    print(f"  Boss slider exit preaim mode: {payload.get('boss_slider_exit_preaim_mode')}")
    print(f"  Boss slider exit early aim ms: {payload.get('boss_slider_exit_early_aim_ms')}")
    print(f"  Boss slider exit next max delta ms: {payload.get('boss_slider_exit_next_max_delta_ms')}")
    print(f"  Boss slider exit handoff count: {payload.get('boss_slider_exit_handoff_count')}")
    print(f"  Boss slider exit snap count: {payload.get('boss_slider_exit_snap_count')}")
    print(f"  Boss slider exit stale snap ignored count: {payload.get('boss_slider_exit_stale_snap_ignored_count')}")
    print(f"  Boss slider exit early aim count: {payload.get('boss_slider_exit_early_aim_count')}")
    print(f"  Boss slider exit blend count: {payload.get('boss_slider_exit_blend_count')}")
    print(f"  Boss slider exit snap preaim count: {payload.get('boss_slider_exit_snap_preaim_count')}")
    print(f"  Boss slider exit blend progress: {payload.get('boss_slider_exit_blend_progress')}")
    print(f"  Boss slider exit preaim target xy: {payload.get('boss_slider_exit_preaim_target_xy')}")
    print(f"  Boss slider exit preaim next object index: {payload.get('boss_slider_exit_preaim_next_object_index')}")
    print(f"  Boss slider exit preaim next object type: {payload.get('boss_slider_exit_preaim_next_object_type')}")
    print(f"  Boss slider exit preaim next delta ms: {payload.get('boss_slider_exit_preaim_next_delta_ms')}")
    print(f"  Boss slider exit preaim last reason: {payload.get('boss_slider_exit_preaim_last_reason')}")
    print(f"  Boss slider exit next object index: {payload.get('boss_slider_exit_next_object_index')}")
    print(f"  Boss slider exit next object type: {payload.get('boss_slider_exit_next_object_type')}")
    print(f"  Boss slider exit next object time delta ms: {payload.get('boss_slider_exit_next_object_time_delta_ms')}")
    print(f"  Boss slider exit overrun ms: {payload.get('boss_slider_exit_overrun_ms')}")
    print(f"  Boss slider exit blocked ms: {payload.get('boss_slider_exit_blocked_ms')}")
    print(f"  Boss slider exit last reason: {payload.get('boss_slider_exit_last_reason')}")
    print(f"  Boss slider exit last slider index: {payload.get('boss_slider_exit_last_slider_index')}")
    print(f"  Boss slider follow count: {payload.get('boss_slider_follow_count')}")
    print(f"  Boss spinner spin count: {payload.get('boss_spinner_spin_count')}")
    print(f"  Boss simple slider follow count: {payload.get('boss_simple_slider_follow_count')}")
    print(f"  Boss pilot slider follow count: {payload.get('boss_pilot_slider_follow_count')}")
    print(f"  Boss simple spinner spin count: {payload.get('boss_simple_spinner_spin_count')}")
    print(f"  Boss pilot spinner spin count: {payload.get('boss_pilot_spinner_spin_count')}")
    print(f"  Boss hold count: {payload.get('boss_hold_count')}")
    print(f"  Boss key down count: {payload.get('boss_key_down_count')}")
    print(f"  Boss key up count: {payload.get('boss_key_up_count')}")
    print(f"  Boss last hit key: {payload.get('boss_last_hit_key')}")
    print(f"  Boss last action type: {payload.get('boss_last_action_type')}")
    print(f"  Boss last action time ms: {payload.get('boss_last_action_time_ms')}")
    print(f"  Boss last action scheduled time ms: {payload.get('boss_last_action_scheduled_time_ms')}")
    print(f"  Boss last action effective time ms: {payload.get('boss_last_action_effective_time_ms')}")
    print(f"  Boss last action delta ms: {payload.get('boss_last_action_delta_ms')}")
    print(f"  Boss last action error: {payload.get('boss_last_action_error')}")
    print(f"  Boss tap attempt count: {payload.get('boss_tap_attempt_count')}")
    print(f"  Boss tap on time count: {payload.get('boss_tap_on_time_count')}")
    print(f"  Boss tap early count: {payload.get('boss_tap_early_count')}")
    print(f"  Boss tap late count: {payload.get('boss_tap_late_count')}")
    print(f"  Boss slider start count: {payload.get('boss_slider_start_count')}")
    print(f"  Boss spinner start count: {payload.get('boss_spinner_start_count')}")
    print(f"  Boss slider duration ms: {payload.get('boss_slider_duration_ms')}")
    print(f"  Boss slider path len: {payload.get('boss_slider_path_len')}")
    print(f"  Boss slider slide count: {payload.get('boss_slider_slide_count')}")
    print(f"  Boss slider follow sample count: {payload.get('boss_slider_follow_sample_count')}")
    print(f"  Boss slider last progress: {payload.get('boss_slider_last_progress')}")
    print(f"  Boss slider last xy: {payload.get('boss_slider_last_xy')}")
    print(f"  Boss spinner rpm: {payload.get('boss_spinner_rpm')}")
    print(f"  Boss spinner radius: {payload.get('boss_spinner_radius')}")
    print(f"  Boss spinner move count: {payload.get('boss_spinner_move_count')}")
    print(f"  Boss spinner last angle: {payload.get('boss_spinner_last_angle')}")
    print(f"  Boss long hold active: {payload.get('boss_long_hold_active')}")
    print(f"  Boss long hold key: {payload.get('boss_long_hold_key')}")
    print(f"  Boss long hold started at: {payload.get('boss_long_hold_started_at')}")
    print(f"  Boss long hold expected end: {payload.get('boss_long_hold_expected_end')}")
    print(f"  Boss selected action index: {payload.get('boss_selected_action_index')}")
    print(f"  Boss previous selected action index: {payload.get('boss_previous_selected_action_index')}")
    print(f"  Boss selected index delta: {payload.get('boss_selected_index_delta')}")
    print(f"  Boss skipped object count: {payload.get('boss_skipped_object_count')}")
    print(f"  Boss selected action type: {payload.get('boss_selected_action_type')}")
    print(f"  Boss previous action type: {payload.get('boss_previous_action_type')}")
    print(f"  Boss selected action time ms: {payload.get('boss_selected_action_time_ms')}")
    print(f"  Boss selected action end time ms: {payload.get('boss_selected_action_end_time_ms')}")
    print(f"  Boss time to selected start ms: {payload.get('boss_time_to_selected_start_ms')}")
    print(f"  Boss time to selected end ms: {payload.get('boss_time_to_selected_end_ms')}")
    print(f"  Boss next object index: {payload.get('boss_next_object_index')}")
    print(f"  Boss next object type: {payload.get('boss_next_object_type')}")
    print(f"  Boss next object time ms: {payload.get('boss_next_object_time_ms')}")
    print(f"  Boss time to next object ms: {payload.get('boss_time_to_next_object_ms')}")
    print(f"  Boss active hold index: {payload.get('boss_active_hold_index')}")
    print(f"  Boss active hold type: {payload.get('boss_active_hold_type')}")
    print(f"  Boss active hold key: {payload.get('boss_active_hold_key')}")
    print(f"  Boss loop interval ms: {payload.get('boss_loop_interval_ms')}")
    print(f"  Boss move interval ms: {payload.get('boss_move_interval_ms')}")
    print(f"  Boss slider move interval ms: {payload.get('boss_slider_move_interval_ms')}")
    print(f"  Boss slider target delta: {payload.get('boss_slider_target_delta')}")
    print(f"  Boss slider screen delta: {payload.get('boss_slider_screen_delta')}")
    print(f"  Boss slider progress: {payload.get('boss_slider_progress')}")
    print(f"  Boss note skip warning count: {payload.get('boss_note_skip_warning_count')}")
    print(f"  Boss hold blocks next warning count: {payload.get('boss_hold_blocks_next_warning_count')}")
    print(f"  Boss slider jitter warning count: {payload.get('boss_slider_jitter_warning_count')}")
    print(f"  Boss scheduler enabled: {payload.get('boss_scheduler_enabled')}")
    print(f"  Boss scheduler queue len: {payload.get('boss_scheduler_queue_len')}")
    print(f"  Boss scheduler next event type: {payload.get('boss_scheduler_next_event_type')}")
    print(f"  Boss scheduler next event time ms: {payload.get('boss_scheduler_next_event_time_ms')}")
    print(f"  Boss scheduler last event type: {payload.get('boss_scheduler_last_event_type')}")
    print(f"  Boss scheduler last event delta ms: {payload.get('boss_scheduler_last_event_delta_ms')}")
    print(f"  Boss scheduler key down count: {payload.get('boss_scheduler_key_down_count')}")
    print(f"  Boss scheduler key up count: {payload.get('boss_scheduler_key_up_count')}")
    print(f"  Boss active follow type: {payload.get('boss_active_follow_type')}")
    print(f"  Boss active follow index: {payload.get('boss_active_follow_index')}")
    print(f"  Boss active follow start time ms: {payload.get('boss_active_follow_start_time_ms')}")
    print(f"  Boss active follow end time ms: {payload.get('boss_active_follow_end_time_ms')}")
    print(f"  Boss active follow progress: {payload.get('boss_active_follow_progress')}")
    print(f"  Boss active slider slide index: {payload.get('boss_active_slider_slide_index')}")
    print(f"  Boss active slider local progress: {payload.get('boss_active_slider_local_progress')}")
    print(f"  Boss active slider target time ms: {payload.get('boss_active_slider_target_time_ms')}")
    print(f"  Boss active slider start time ms: {payload.get('boss_active_slider_start_time_ms')}")
    print(f"  Boss active slider end time ms: {payload.get('boss_active_slider_end_time_ms')}")
    print(f"  Boss active slider elapsed ms: {payload.get('boss_active_slider_elapsed_ms')}")
    print(f"  Boss active slider duration ms: {payload.get('boss_active_slider_duration_ms')}")
    print(f"  Boss active slider raw progress: {payload.get('boss_active_slider_raw_progress')}")
    print(f"  Boss active slider speed progress: {payload.get('boss_active_slider_speed_progress')}")
    print(f"  Boss active slider effective progress: {payload.get('boss_active_slider_effective_progress')}")
    print(f"  Boss active slider remaining ms: {payload.get('boss_active_slider_remaining_ms')}")
    print(f"  Boss active slider time expired: {payload.get('boss_active_slider_time_expired')}")
    print(f"  Boss active slider blocks next object: {payload.get('boss_active_slider_blocks_next_object')}")
    print(f"  Boss next object after slider index: {payload.get('boss_next_object_after_slider_index')}")
    print(f"  Boss time to next object after slider ms: {payload.get('boss_time_to_next_object_after_slider_ms')}")
    print(f"  Boss active slider follow hz: {payload.get('boss_active_slider_follow_hz')}")
    print(f"  Boss active slider follow interval ms: {payload.get('boss_active_slider_follow_interval_ms')}")
    print(f"  Boss active slider path len: {payload.get('boss_active_slider_path_len')}")
    print(f"  Boss active slider target xy: {payload.get('boss_active_slider_target_xy')}")
    print(f"  Boss active slider screen xy: {payload.get('boss_active_slider_screen_xy')}")
    print(f"  Boss follow lag ms: {payload.get('boss_follow_lag_ms')}")
    print(f"  Boss rect mode: {payload.get('boss_rect_mode')}")
    print(f"  Boss playfield transform: {payload.get('boss_playfield_transform')}")
    print(f"  Boss rect auto mode: {payload.get('boss_rect_auto_mode')}")
    print(f"  Boss rect source: {payload.get('boss_rect_source')}")
    print(f"  Boss env playfield rect: {payload.get('boss_env_playfield_rect')}")
    print(f"  Boss client full rect: {payload.get('boss_client_full_rect')}")
    print(f"  Boss aspect fit playfield rect: {payload.get('boss_aspect_fit_playfield_rect')}")
    print(f"  Boss osu pilot rect: {payload.get('boss_osu_pilot_rect')}")
    print(f"  Boss official shift rect: {payload.get('boss_official_shift_rect')}")
    print(f"  Boss resolved playfield rect: {payload.get('boss_resolved_playfield_rect')}")
    print(f"  Boss playfield scale: {payload.get('boss_playfield_scale')}")
    print(f"  Boss playfield margin x: {payload.get('boss_playfield_margin_x')}")
    print(f"  Boss playfield margin y: {payload.get('boss_playfield_margin_y')}")
    print(f"  Boss vertical shift px: {payload.get('boss_vertical_shift_px')}")
    print(f"  Boss osu window rect: {payload.get('boss_osu_window_rect')}")
    print(f"  Boss osu client rect: {payload.get('boss_osu_client_rect')}")
    print(f"  Boss monitor size: {payload.get('boss_monitor_size')}")
    print(f"  Boss virtual screen: {payload.get('boss_virtual_screen')}")
    print(f"  Boss dpi scale: {payload.get('boss_dpi_scale')}")
    print(f"  Boss last input kind: {payload.get('boss_last_input_kind')}")
    print(f"  Boss last target source: {payload.get('boss_last_target_source')}")
    print(f"  Boss last target playfield xy: {payload.get('boss_last_target_playfield_xy')}")
    print(f"  Boss last target screen xy: {payload.get('boss_last_target_screen_xy')}")
    print(f"  Boss last tosu time ms: {payload.get('boss_last_tosu_time_ms')}")
    print(f"  Boss selected time source: {payload.get('boss_selected_time_source')}")
    print(f"  Boss selected time ms: {payload.get('boss_selected_time_ms')}")
    print(f"  Boss time sane: {payload.get('boss_time_sane')}")
    print(f"  Boss time sanity reason: {payload.get('boss_time_sanity_reason')}")
    print(f"  Boss first object time ms: {payload.get('boss_first_object_time_ms')}")
    print(f"  Boss last object end time ms: {payload.get('boss_last_object_end_time_ms')}")
    print(f"  Boss time vs first ms: {payload.get('boss_time_vs_first_ms')}")
    print(f"  Boss time vs last ms: {payload.get('boss_time_vs_last_ms')}")
    print(f"  Boss start max late ms: {payload.get('boss_start_max_late_ms')}")
    print(f"  Boss late start hold: {payload.get('boss_late_start_hold')}")
    print(f"  Boss late start reason: {payload.get('boss_late_start_reason')}")
    print(f"  Boss movement armed: {payload.get('boss_movement_armed')}")
    print(f"  Boss last effective time ms: {payload.get('boss_last_effective_time_ms')}")
    print(f"  Boss last path index: {payload.get('boss_last_path_index')}")
    print(f"  Boss start offset: {payload.get('boss_start_offset')}")
    print(f"  Boss is ready: {payload.get('boss_is_ready')}")
    print(f"  Boss is playing: {payload.get('boss_is_playing')}")
    print(f"  Boss timer mode: {payload.get('boss_timer_mode')}")
    print(f"  Boss normalized path len: {payload.get('boss_normalized_path_len')}")
    print(f"  Boss move count: {payload.get('boss_move_count')}")
    print(f"  Boss last move xy: {payload.get('boss_last_move_xy')}")
    print(f"  Boss last move error: {payload.get('boss_last_move_error')}")
    print(f"  Boss heartbeat interval ms: {payload.get('boss_heartbeat_interval_ms')}")
    print(f"  Boss debug log enabled: {payload.get('boss_debug_log_enabled')}")
    print(f"  Blockers: {payload.get('blockers', [])}")
    print(f"  Start command hint: {payload.get('start_command_hint')}")
    if payload.get("release_called") is not None:
        print(f"  Release called: {payload.get('release_called')}")
    if payload.get("state_write") is not None:
        print(f"  State write: {payload.get('state_write')}")
        print(f"  State path: {payload.get('state_path')}")
        print(f"  State error: {payload.get('state_error')}")
    print(f"  Real input: {payload.get('real_input')}")
    print(f"  Submit: {payload.get('submit')}")


register_kill_switch_hotkey()
atexit.register(_atexit_cleanup)
