"""
Stream Result Subtitle Pipeline — Phase 17.

Chains result flags -> core event preview -> companion response preview -> public subtitle payload.
Adds a write gate that requires env + operator token before writing the subtitle file.

No live input. No VTS. No ElevenLabs. No OBS API. No mouse/click. No submit.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from nana.runtime.stream_event_core import ingest_stream_event_preview
from nana.runtime.stream_public_output import build_public_subtitle_payload
from nana.runtime.stream_response_preview import build_stream_response_preview


# ---------------------------------------------------------------------------
# Env gate constants
# ---------------------------------------------------------------------------
_PIPELINE_WRITE_ENV = "NANA_OSU_RESULT_SUBTITLE_PIPELINE_WRITE_ENABLED"
_APPROVED_TOKEN = "I_APPROVE_OSU_RESULT_SUBTITLE_PIPELINE_WRITE"


# ---------------------------------------------------------------------------
# Result flag parser (same as used by existing preview commands)
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


def _parse_activity_state(text: str) -> str:
    """Parse --activity-state from command text."""
    for token in str(text or "").split()[1:]:
        if token.startswith("--activity-state="):
            return token.split("=", 1)[1].strip()
    return "result"


def _parse_path_flag(text: str) -> str | None:
    """Parse --path=<path> from command text."""
    for token in str(text or "").split()[1:]:
        if token.startswith("--path="):
            return token.split("=", 1)[1].strip()
    return None


def _parse_token(text: str) -> str | None:
    """Parse --operator-approval-token from command text."""
    for token in str(text or "").split()[1:]:
        if token.startswith("--operator-approval-token="):
            return token.split("=", 1)[1].strip()
    return None


def _is_pipeline_write_env_enabled() -> bool:
    """Check if env gate allows pipeline write."""
    return os.environ.get(_PIPELINE_WRITE_ENV, "").strip() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def build_result_subtitle_pipeline_preview(text: str = "") -> dict[str, Any]:
    """
    Build a full result -> subtitle pipeline preview.

    Parses result flags, builds core event, builds companion response, builds public
    subtitle payload. Does NOT write the file.

    Args:
        text: Command text containing --score, --accuracy, --misses, --combo,
              --max-combo, --rank, --passed, --activity-state, --path flags.

    Returns:
        A pipeline preview dict:
        {
            "decision": "result_subtitle_pipeline_preview_only_no_write",
            "source_game": str,
            "activity_state": str,
            "stream_mode": str,
            "speak_allowed": bool,
            "response_style": str,
            "line": str,
            "output_path": str,
            "file_write": False,
            "pipeline_write": False,
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
        }
    """
    result_fields = _parse_result_flags(text)
    activity_state = _parse_activity_state(text)
    output_path_override = _parse_path_flag(text)

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

    # Build core event
    safe_event: dict[str, Any] = {
        "game": "osu",
        "activity_state": activity_state,
        "stream_mode": stream_mode,
        "speak_allowed": speak_allowed,
        "song_title": "unknown",
        "difficulty": "unknown",
    }
    for key in ("score", "accuracy", "misses", "combo", "max_combo", "rank", "passed"):
        if key in result_fields:
            safe_event[key] = result_fields[key]

    # Core ingest
    core_preview = ingest_stream_event_preview(safe_event)

    # Companion response
    companion_preview = build_stream_response_preview(core_preview)

    # Public subtitle payload
    payload = build_public_subtitle_payload(
        companion_preview,
        output_path=output_path_override,
    )

    # Override decision to reflect pipeline
    payload["decision"] = "result_subtitle_pipeline_preview_only_no_write"
    payload["pipeline_write"] = False
    payload["response_intent"] = companion_preview.get("response_intent", "unknown")
    payload["response_style"] = companion_preview.get("response_style", "unknown")
    payload["performance_tags"] = list(companion_preview.get("performance_tags", ()))

    return payload


def build_result_subtitle_pipeline_write(text: str = "") -> dict[str, Any]:
    """
    Gated write of the result subtitle pipeline.

    Same chain as preview, but writes the subtitle file ONLY if:
      - env NANA_OSU_RESULT_SUBTITLE_PIPELINE_WRITE_ENABLED=1
      - operator approval token matches exactly
      - stream_mode == talk_ready
      - speak_allowed == True
      - line is non-empty

    Args:
        text: Command text containing --score, --accuracy, --misses, --combo,
              --max-combo, --rank, --passed, --activity-state, --path,
              --operator-approval-token flags.

    Returns:
        A pipeline write result dict:
        {
            "decision": "result_subtitle_pipeline_file_written" | "result_subtitle_pipeline_hold",
            "hold_reason": str | None,
            "source_game": str,
            "activity_state": str,
            "stream_mode": str,
            "speak_allowed": bool,
            "response_style": str,
            "line": str,
            "output_path": str,
            "bytes_written": int | None,
            "file_write": bool,
            "pipeline_write": bool,
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
        }
    """
    operator_token = _parse_token(text)
    token_ok = operator_token == _APPROVED_TOKEN
    env_enabled = _is_pipeline_write_env_enabled()

    # Build preview (same chain)
    preview = build_result_subtitle_pipeline_preview(text)

    output_path_str = preview["output_path"]
    line = preview["line"]
    stream_mode = preview["stream_mode"]
    speak_allowed = preview["speak_allowed"]

    def make_result(
        decision: str,
        hold_reason: str | None,
        bytes_written: int | None,
        file_write: bool,
        pipeline_write: bool,
    ) -> dict[str, Any]:
        return {
            "decision": decision,
            "hold_reason": hold_reason,
            "source_game": preview["source_game"],
            "activity_state": preview["activity_state"],
            "stream_mode": stream_mode,
            "speak_allowed": speak_allowed,
            "response_style": preview.get("response_style", "unknown"),
            "line": line,
            "output_path": output_path_str,
            "bytes_written": bytes_written,
            "file_write": file_write,
            "pipeline_write": pipeline_write,
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
        }

    # Check env gate
    if not env_enabled:
        return make_result("result_subtitle_pipeline_hold", "missing_env", None, False, False)

    # Check token gate
    if not token_ok:
        return make_result("result_subtitle_pipeline_hold", "missing_token", None, False, False)

    # Check stream mode gate
    if stream_mode != "talk_ready":
        return make_result("result_subtitle_pipeline_hold", "stream_mode_not_talk_ready", None, False, False)

    # Check speak gate
    if not speak_allowed:
        return make_result("result_subtitle_pipeline_hold", "speak_not_allowed", None, False, False)

    # Check line non-empty
    if not line:
        return make_result("result_subtitle_pipeline_hold", "empty_line", None, False, False)

    # All gates pass — write file directly with atomic write
    resolved_path = Path(output_path_str)
    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = resolved_path.with_suffix(resolved_path.suffix + ".tmp")
        tmp_path.write_text(line, encoding="utf-8")
        os.replace(tmp_path, resolved_path)
        bytes_written = len(line.encode("utf-8"))
    except OSError:
        return make_result("result_subtitle_pipeline_hold", "file_write_error", None, False, False)

    return make_result("result_subtitle_pipeline_file_written", None, bytes_written, True, True)
