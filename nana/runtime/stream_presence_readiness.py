"""
Stream presence readiness preview for osu!.

Aggregates VTS, voice/TTS config, dialogue/public filtering, subtitle/OBS-file
status, and osu! executor preflight into one read-only status payload.

No live input. No cursor move/click. No SetCursorPos. No SendInput.
No score submit. No VTS connect/auth. No ElevenLabs/OpenAI/TTS call.
No OBS API call.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from nana.integrations.vts import get_vts_runtime, vts_snapshot
from nana.runtime.capabilities import (
    build_osu_executor_preflight_payload as build_executor_preflight_payload,
)
from nana.runtime.stream_event_core import ingest_stream_event_preview
from nana.runtime.stream_public_output import (
    build_public_subtitle_payload,
    read_public_subtitle_status,
)
from nana.runtime.stream_response_preview import build_stream_response_preview


STREAM_PRESENCE_SCHEMA = "nana.osu.stream_presence_readiness.v1"
STREAM_PRESENCE_DECISION = "stream_presence_readiness_preview_only_no_input"

PUBLIC_FILTER_ALLOWLIST = [
    "game",
    "activity_state",
    "song",
    "difficulty",
    "accuracy",
    "misses",
    "rank",
    "score",
    "combo",
    "max_combo",
    "passed",
    "osu_status",
    "reaction",
    "safe_hint",
    "stream_mode",
    "speak_allowed",
]

PUBLIC_FILTER_BLOCKLIST = [
    "local_path",
    "api_key",
    "token",
    "secret",
    "private_chat_history",
    "private_memory",
    "system_commands",
    "raw_file_contents",
    "username",
    "account",
    "timing",
    "screen_coords",
    "screen",
    "coordinate",
    "cursor",
    "mouse",
    "hit_t",
    "aim_t",
]

_ALLOWED_FIELD_SET = set(PUBLIC_FILTER_ALLOWLIST)
_TEXT_FIELDS = {"song", "difficulty", "osu_status", "reaction", "safe_hint", "activity_state", "stream_mode"}
_INT_FIELDS = {"misses", "score", "combo", "max_combo"}
_FLOAT_FIELDS = {"accuracy"}
_BOOL_FIELDS = {"passed", "speak_allowed"}
_BLOCKED_KEY_EXACT = {
    "x",
    "y",
    "screen_x",
    "screen_y",
    "cursor_x",
    "cursor_y",
    "mouse_x",
    "mouse_y",
    "hit_t",
    "aim_t",
    "hit_time",
    "aim_time",
    "time_until_action_ms",
    "relative_ms_from_now",
}
_BLOCKED_KEY_MARKERS = {
    "local_path",
    "api_key",
    "apikey",
    "token",
    "secret",
    "bearer",
    "authorization",
    "credential",
    "password",
    "private_chat_history",
    "chat_history",
    "private_memory",
    "system_command",
    "raw_file_contents",
    "file_contents",
    "username",
    "account",
    "timing",
    "screen",
    "coord",
    "cursor",
    "mouse",
}

_API_ASSIGNMENT_PATTERN = re.compile(r"(?i)\b(?:api[_-]?key|token|secret|bearer|authorization)\b\s*[:=]\s*[^\s]+")
_WINDOWS_PATH_PATTERN = re.compile(r"(?i)\b[A-Z]:\\[^\r\n]+")
_UNIX_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s/]+/)+[^\s/]+")
_SECRET_PREFIX_PATTERN = re.compile(r"(?i)\b(?:sk|rk|pk)-[A-Za-z0-9_-]{8,}\b")
_LONG_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")

_MISSING_CONFIG_VALUES = {
    "",
    "OPENAI_KEY_CUA_BAN",
    "ELEVENLABS_KEY_CUA_BAN",
    "YOUR_API_KEY",
    "YOUR_ELEVEN_API_KEY",
}


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _looks_private(value: Any) -> bool:
    text = str(value or "")
    return any(
        pattern.search(text)
        for pattern in (
            _API_ASSIGNMENT_PATTERN,
            _WINDOWS_PATH_PATTERN,
            _UNIX_PATH_PATTERN,
            _SECRET_PREFIX_PATTERN,
            _LONG_TOKEN_PATTERN,
        )
    )


def _sanitize_public_text(value: Any, *, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    text = re.sub(r"\s+", " ", text).strip()
    if not text or _looks_private(text):
        return fallback
    return text[:160]


def _blocked_field_name(key: str) -> bool:
    lowered = key.lower()
    return lowered in _BLOCKED_KEY_EXACT or any(marker in lowered for marker in _BLOCKED_KEY_MARKERS)


def _coerce_public_value(key: str, value: Any) -> Any:
    if key == "game":
        return "osu"
    if key in _TEXT_FIELDS:
        return _sanitize_public_text(value)
    if key == "rank":
        return _sanitize_public_text(value).upper()
    if key in _INT_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if key in _FLOAT_FIELDS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if key in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        return _is_truthy(value)
    return value


def filter_public_stream_event(raw_event: dict[str, Any] | None) -> dict[str, Any]:
    """
    Build a public-safe osu! event.

    Unknown keys and blocked/private-looking values are dropped. Text fields are
    sanitized. The returned event is safe to feed into stream_event_core.
    """
    raw = raw_event if isinstance(raw_event, dict) else {}
    blocked_fields: list[str] = []
    safe_event: dict[str, Any] = {"game": "osu"}

    if "song_title" in raw and "song" not in raw:
        raw = dict(raw)
        raw["song"] = raw.get("song_title")

    for key, value in raw.items():
        if str(key).startswith("_"):
            continue
        if key not in _ALLOWED_FIELD_SET:
            if _blocked_field_name(str(key)) or _looks_private(value):
                blocked_fields.append(str(key))
            continue
        if _looks_private(value):
            blocked_fields.append(str(key))
            if key in _TEXT_FIELDS or key == "rank":
                safe_event[key] = "unknown"
            continue
        coerced = _coerce_public_value(key, value)
        if coerced is not None:
            safe_event[key] = coerced

    safe_event.setdefault("activity_state", "result")
    safe_event.setdefault("stream_mode", "talk_ready")
    safe_event.setdefault("speak_allowed", True)
    safe_event.setdefault("song", "unknown")
    safe_event.setdefault("difficulty", "unknown")
    safe_event["_blocked_fields"] = blocked_fields
    return safe_event


def _core_event_from_public_event(public_event: dict[str, Any]) -> dict[str, Any]:
    event = {k: v for k, v in public_event.items() if not k.startswith("_")}
    if "song" in event:
        event["song_title"] = event["song"]
    return event


def _build_vts_readiness() -> dict[str, Any]:
    try:
        snapshot = vts_snapshot(get_vts_runtime())
        ready = bool(snapshot.get("ready"))
        connected = bool(snapshot.get("connected"))
        auth_status = snapshot.get("auth_status_label") or snapshot.get("auth_status") or "unknown"
        last_error = _sanitize_public_text(snapshot.get("last_error") or "none", fallback="filtered")
    except Exception as exc:
        ready = False
        connected = False
        auth_status = "snapshot_unavailable"
        last_error = f"{type(exc).__name__}:{exc}"

    return {
        "vts_ready": ready,
        "vts_connected": connected,
        "vts_auth_status": auth_status,
        "vts_last_error": last_error,
        "vts_degrade_mode": "normal" if ready else "no_vts_continue",
        "vts_call": False,
    }


def _configured_secret(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text not in _MISSING_CONFIG_VALUES)


def _build_voice_readiness() -> dict[str, Any]:
    try:
        from nana import config as nana_config

        eleven_key = getattr(nana_config, "ELEVEN_API_KEY", None)
        voice_id = getattr(nana_config, "VOICE_ID", None)
        debug_no_tts = bool(getattr(nana_config, "DEBUG_NO_TTS", False))
        voice_test_mode = bool(getattr(nana_config, "VOICE_TEST_MODE", False))
        provider = "elevenlabs" if _configured_secret(eleven_key) else "none"
        configured = provider == "elevenlabs" and bool(str(voice_id or "").strip())
        disabled = debug_no_tts or voice_test_mode
    except Exception:
        provider = "unknown"
        configured = False
        disabled = False

    voice_ready = bool(configured and not disabled)
    if not configured:
        voice_mode = "unavailable"
        degrade = "subtitle_only_mode"
    elif disabled:
        voice_mode = "configured_disabled_no_call"
        degrade = "subtitle_only_mode"
    else:
        voice_mode = "configured_no_call"
        degrade = "normal"

    return {
        "voice_provider": provider,
        "voice_configured": bool(configured),
        "voice_ready": voice_ready,
        "voice_mode": voice_mode,
        "voice_degrade_mode": degrade,
        "voice_call": False,
    }


def _fallback_response_preview(public_event: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "decision": "companion_response_preview_only_no_output",
        "source_game": "osu",
        "activity_state": str(public_event.get("activity_state") or "unknown"),
        "stream_mode": str(public_event.get("stream_mode") or "quiet_waiting"),
        "speak_allowed": bool(public_event.get("speak_allowed")),
        "response_intent": "waiting",
        "response_style": "waiting",
        "line_candidate": "",
        "performance_tags": [],
        "private_data": "filtered",
        "fallback_reason": reason,
        "voice_call": False,
        "vts_call": False,
        "subtitle_call": False,
        "obs_call": False,
        "real_input": False,
        "submit": False,
    }


def _build_dialogue_readiness(public_event: dict[str, Any]) -> dict[str, Any]:
    try:
        core_preview = ingest_stream_event_preview(_core_event_from_public_event(public_event))
        response_preview = build_stream_response_preview(core_preview)
        mode = "template"
        ready = True
        degrade = "normal"
        error = None
    except Exception as exc:
        response_preview = _fallback_response_preview(public_event, reason=type(exc).__name__)
        mode = "fallback"
        ready = True
        degrade = "template_fallback"
        error = f"{type(exc).__name__}:{exc}"

    blocked_fields = list(public_event.get("_blocked_fields") or [])
    if blocked_fields:
        mode = "fallback"
        degrade = "template_fallback"
    private_leak_detected = any(
        _looks_private(value)
        for key, value in public_event.items()
        if not key.startswith("_")
    ) or _looks_private(response_preview.get("line_candidate") or "")
    if private_leak_detected:
        ready = False
        degrade = "template_fallback"
        mode = "fallback"

    return {
        "dialogue_ready": ready,
        "dialogue_mode": mode,
        "public_filter_active": True,
        "public_filter_allowlist": list(PUBLIC_FILTER_ALLOWLIST),
        "public_filter_blocklist": list(PUBLIC_FILTER_BLOCKLIST),
        "public_filter_blocked_fields": blocked_fields,
        "public_event": {k: v for k, v in public_event.items() if not k.startswith("_")},
        "response_preview": response_preview,
        "dialogue_degrade_mode": degrade,
        "dialogue_error": error,
        "private_data": "filtered",
        "voice_call": False,
        "vts_call": False,
        "obs_call": False,
        "real_input": False,
        "submit": False,
    }


def _path_parent_writable(path: Path) -> tuple[bool, bool]:
    parent = path.parent
    parent_exists = parent.exists()
    if not parent_exists:
        return False, False
    try:
        writable = os.access(parent, os.W_OK)
    except Exception:
        writable = False
    return parent_exists, bool(writable)


def _build_subtitle_readiness(response_preview: dict[str, Any], raw_event: dict[str, Any] | None) -> dict[str, Any]:
    output_path = None
    if isinstance(raw_event, dict):
        output_path = raw_event.get("subtitle_path") or raw_event.get("_subtitle_path")
    payload = build_public_subtitle_payload(response_preview, output_path=output_path)
    path = Path(str(payload.get("output_path")))
    parent_exists, parent_writable = _path_parent_writable(path)

    try:
        status = read_public_subtitle_status(path=path)
        file_exists = bool(status.get("exists"))
        fresh = bool(status.get("fresh"))
    except Exception:
        file_exists = False
        fresh = False

    subtitle_ready = bool(parent_exists and parent_writable)
    return {
        "subtitle_ready": subtitle_ready,
        "subtitle_path": str(path),
        "subtitle_parent_exists": parent_exists,
        "subtitle_parent_writable": parent_writable,
        "subtitle_file_exists": file_exists,
        "subtitle_fresh": fresh,
        "subtitle_degrade_mode": "normal" if subtitle_ready else "voice_or_vts_only",
        "obs_api_call": False,
        "obs_call": False,
        "file_write": False,
    }


def _executor_error_payload(exc: Exception) -> dict[str, Any]:
    return {
        "decision": "executor_preflight_error_no_input",
        "ready_for_manual_review": False,
        "focus_readiness": "unavailable_no_helper",
        "emergency_abort_available": "unavailable_no_helper",
        "emergency_abort_strategy": "preview_only",
        "abort_command_available": False,
        "abort_flag_path": None,
        "emergency_release_available": "unavailable_no_helper",
        "emergency_release_strategy": "preview_only",
        "release_command_available": False,
        "blockers": [f"executor_preflight_error:{type(exc).__name__}"],
        "executable": False,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
    }


def _build_executor_readiness(
    include_executor: bool,
    raw_event: dict[str, Any] | None,
    *,
    executor_text: str | None = None,
) -> dict[str, Any]:
    if not include_executor:
        return {
            "executor_preflight_decision": "executor_preflight_skipped_by_request",
            "executor_ready_for_manual_review": True,
            "focus_readiness": "skipped",
            "emergency_abort_available": "skipped",
            "emergency_abort_strategy": "skipped",
            "abort_command_available": False,
            "abort_flag_path": None,
            "emergency_release_available": "skipped",
            "emergency_release_strategy": "skipped",
            "release_command_available": False,
            "executor_payload": None,
            "token_present": False,
            "token_valid": False,
            "executable": False,
            "real_input": False,
            "submit": False,
        }

    text = str(executor_text or "")
    if isinstance(raw_event, dict):
        text = text or str(raw_event.get("executor_text") or raw_event.get("_executor_text") or "")
    try:
        payload = build_executor_preflight_payload(text)
    except Exception as exc:
        payload = _executor_error_payload(exc)

    return {
        "executor_preflight_decision": payload.get("decision"),
        "executor_ready_for_manual_review": bool(payload.get("ready_for_manual_review")),
        "focus_readiness": payload.get("focus_readiness") or payload.get("window_focus_readiness") or "unavailable_no_helper",
        "emergency_abort_available": payload.get("emergency_abort_available") or "unavailable_no_helper",
        "emergency_abort_strategy": payload.get("emergency_abort_strategy") or "preview_only",
        "abort_command_available": bool(payload.get("abort_command_available")),
        "abort_flag_path": payload.get("abort_flag_path"),
        "emergency_release_available": payload.get("emergency_release_available") or "unavailable_no_helper",
        "emergency_release_strategy": payload.get("emergency_release_strategy") or "preview_only",
        "release_command_available": bool(payload.get("release_command_available")),
        "executor_payload": payload,
        "token_present": bool(payload.get("token_present")),
        "token_valid": bool(payload.get("token_valid")),
        "executable": False,
        "real_input": False,
        "submit": False,
    }


def build_stream_presence_readiness_preview(
    event_or_flags: dict[str, Any] | None = None,
    *,
    include_executor: bool = True,
    executor_text: str | None = None,
) -> dict[str, Any]:
    """
    Build an aggregate stream presence readiness payload.

    This helper is read-only and preview-only. It never performs input, voice,
    VTS connect/auth, OBS API, or score submission.
    """
    raw_event = event_or_flags if isinstance(event_or_flags, dict) else {}
    public_event = filter_public_stream_event(raw_event)
    vts = _build_vts_readiness()
    voice = _build_voice_readiness()
    dialogue = _build_dialogue_readiness(public_event)
    subtitle = _build_subtitle_readiness(dialogue["response_preview"], raw_event)
    executor = _build_executor_readiness(include_executor, raw_event, executor_text=executor_text)

    output_path_available = bool(
        subtitle["subtitle_ready"] or voice["voice_ready"] or vts["vts_ready"]
    )
    executor_ready = bool(executor["executor_ready_for_manual_review"] or not include_executor)
    dialogue_path_available = bool(dialogue["dialogue_ready"] and dialogue["dialogue_mode"] in {"template", "fallback"})
    overall_ready = bool(
        dialogue["public_filter_active"]
        and dialogue_path_available
        and output_path_available
        and executor_ready
    )

    degraded = (
        not overall_ready
        or vts["vts_degrade_mode"] != "normal"
        or voice["voice_degrade_mode"] != "normal"
        or dialogue["dialogue_degrade_mode"] != "normal"
        or subtitle["subtitle_degrade_mode"] != "normal"
    )

    payload: dict[str, Any] = {
        "schema": STREAM_PRESENCE_SCHEMA,
        "version": 1,
        "decision": STREAM_PRESENCE_DECISION,
        "overall_ready": overall_ready,
        "stream_safe_mode": "degraded" if degraded else "normal",
        "created_at": time.time(),
        **vts,
        **voice,
        **dialogue,
        **subtitle,
        **executor,
        "include_executor": bool(include_executor),
        "executable": False,
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "voice_call": False,
        "vts_call": False,
        "obs_call": False,
        "obs_api_call": False,
        "vision_call": False,
    }
    return payload


def print_stream_presence_readiness_preview(payload: dict[str, Any]) -> None:
    print("osu! Stream Presence Readiness")
    print(f"  Decision: {payload['decision']}")
    print(f"  Overall ready: {payload['overall_ready']}")
    print(f"  Stream safe mode: {payload['stream_safe_mode']}")
    print(f"  VTS ready: {payload['vts_ready']}")
    print(f"  VTS degrade: {payload['vts_degrade_mode']}")
    print(f"  Voice ready: {payload['voice_ready']}")
    print(f"  Voice degrade: {payload['voice_degrade_mode']}")
    print(f"  Dialogue ready: {payload['dialogue_ready']}")
    print(f"  Dialogue mode: {payload['dialogue_mode']}")
    print(f"  Public filter active: {payload['public_filter_active']}")
    print(f"  Subtitle ready: {payload['subtitle_ready']}")
    print(f"  Subtitle path: {payload['subtitle_path']}")
    print(f"  OBS API call: {payload['obs_api_call']}")
    print(f"  Executor manual-review ready: {payload['executor_ready_for_manual_review']}")
    print(f"  Focus readiness: {payload['focus_readiness']}")
    print(f"  Emergency abort available: {payload['emergency_abort_available']}")
    print(f"  Emergency abort strategy: {payload.get('emergency_abort_strategy', 'preview_only')}")
    print(f"  Emergency release available: {payload['emergency_release_available']}")
    print(f"  Emergency release strategy: {payload.get('emergency_release_strategy', 'preview_only')}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Voice call: {payload['voice_call']}")
    print(f"  VTS call: {payload['vts_call']}")
    print(f"  OBS call: {payload['obs_call']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
