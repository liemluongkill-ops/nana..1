"""
Stream Public Output — Runtime boundary for writing public subtitle text to a local file.

Receives a companion-level response preview and builds a payload for OBS text file output.
Writes the subtitle line to a local file only when all gates pass:
  - env NANA_OSU_PUBLIC_SUBTITLE_WRITE_ENABLED=1
  - operator approval token matches exactly
  - stream_mode == talk_ready
  - speak_allowed == True
  - line_candidate is non-empty

No live input. No VTS. No ElevenLabs. No OBS API. No mouse/click. No submit.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Env gate constants
# ---------------------------------------------------------------------------
_PUBLIC_SUBTITLE_WRITE_ENV = "NANA_OSU_PUBLIC_SUBTITLE_WRITE_ENABLED"
_PUBLIC_SUBTITLE_WRITE_TOKEN = "I_APPROVE_OSU_PUBLIC_SUBTITLE_WRITE"
_PUBLIC_SUBTITLE_CLEAR_ENV = "NANA_OSU_PUBLIC_SUBTITLE_CLEAR_ENABLED"
_PUBLIC_SUBTITLE_CLEAR_TOKEN = "I_APPROVE_OSU_PUBLIC_SUBTITLE_CLEAR"

# Default output path: package-local runtime data folder.
_DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "data"
_DEFAULT_OUTPUT_FILENAME = "public_subtitle.txt"


def _default_output_path() -> Path:
    """Return stable default output path for public subtitle."""
    return _DEFAULT_OUTPUT_DIR / _DEFAULT_OUTPUT_FILENAME


def _is_write_env_enabled() -> bool:
    """Check if env gate allows writing."""
    return os.environ.get(_PUBLIC_SUBTITLE_WRITE_ENV, "").strip() in {"1", "true", "yes", "on"}


def _check_token(token: str | None) -> bool:
    """Check if operator approval token matches exactly."""
    return str(token or "").strip() == _PUBLIC_SUBTITLE_WRITE_TOKEN


def _sanitize_line(line: str) -> str:
    """
    Sanitize a subtitle line for safe file output.

    Rules:
    - Strip leading/trailing whitespace
    - Replace CR/LF with spaces
    - Collapse multiple spaces to single space
    - Return empty string if input is empty/whitespace-only
    """
    if not line:
        return ""
    # Replace any CR, LF, or CRLF with space
    sanitized = line.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    # Collapse multiple spaces
    while "  " in sanitized:
        sanitized = sanitized.replace("  ", " ")
    return sanitized.strip()


# ---------------------------------------------------------------------------
# Main helpers
# ---------------------------------------------------------------------------

def build_public_subtitle_payload(
    response_preview: dict[str, Any],
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    Build a public subtitle output payload from a companion response preview.

    This function is pure — it does NOT write any file.

    Args:
        response_preview: The output of build_stream_response_preview().
            Expected keys:
            - decision: str
            - source_game: str
            - activity_state: str
            - stream_mode: str
            - speak_allowed: bool
            - line_candidate: str
            - performance_tags: list[str]
            - response_style: str
            - response_intent: str
            - voice_call: bool
            - vts_call: bool
            - subtitle_call: bool
            - obs_call: bool
            - real_input: bool
            - submit: bool
            - private_data: str
        output_path: Optional override path for the output file.
            If None, uses the default path.

    Returns:
        A public subtitle payload dict:
        {
            "decision": "public_subtitle_preview_only_no_write",
            "source_game": str,
            "activity_state": str,
            "stream_mode": str,
            "speak_allowed": bool,
            "line": str,           # sanitized line_candidate
            "output_path": str,     # resolved output path
            "file_write": False,   # always False for preview
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
            "private_data": "filtered",
        }

    Safety guarantees:
    - subtitle_call: always False
    - voice_call: always False
    - vts_call: always False
    - obs_call: always False
    - real_input: always False
    - submit: always False
    - private_data: always "filtered"
    - No file write
    - No OpenAI/model calls
    - No ElevenLabs calls
    - No VTS calls
    - No OBS API calls
    """
    source_game = str(response_preview.get("source_game") or "unknown")
    activity_state = str(response_preview.get("activity_state") or "unknown")
    stream_mode = str(response_preview.get("stream_mode") or "quiet_waiting")
    speak_allowed = bool(response_preview.get("speak_allowed"))
    line_candidate = str(response_preview.get("line_candidate") or "")

    resolved_path = str(output_path if output_path is not None else _default_output_path())

    sanitized_line = _sanitize_line(line_candidate)

    return {
        "decision": "public_subtitle_preview_only_no_write",
        "source_game": source_game,
        "activity_state": activity_state,
        "stream_mode": stream_mode,
        "speak_allowed": speak_allowed,
        "line": sanitized_line,
        "output_path": resolved_path,
        "file_write": False,
        "subtitle_call": False,
        "voice_call": False,
        "vts_call": False,
        "obs_call": False,
        "real_input": False,
        "submit": False,
        "private_data": "filtered",
    }


def write_public_subtitle_file(
    payload: dict[str, Any],
    path: str | Path | None = None,
    *,
    write_env_name: str | None = None,
) -> dict[str, Any]:
    """
    Write the subtitle line to a local text file.

    This function evaluates the gate and writes the file ONLY if:
      - env NANA_OSU_PUBLIC_SUBTITLE_WRITE_ENABLED=1, unless write_env_name
        overrides it
      - operator approval token matches exactly (checked via _check_token before calling)
      - stream_mode == talk_ready
      - speak_allowed == True
      - line_candidate is non-empty

    If any gate fails, returns a hold payload with file_write=False.

    If line is empty, returns a hold payload with file_write=False.

    Uses atomic write: write to temp file, then replace.

    Args:
        payload: The payload returned by build_public_subtitle_payload().
            Must contain: stream_mode, speak_allowed, line, output_path, source_game,
            activity_state, voice_call, vts_call, obs_call, real_input, submit, private_data.
        path: Optional override path. If None, uses payload["output_path"].
        write_env_name: optional lifecycle/specialized env gate name.

    Returns:
        A result dict:
        {
            "decision": "public_subtitle_file_written" | "public_subtitle_hold",
            "output_path": str,
            "file_write": True | False,
            "bytes_written": int | None,
            "hold_reason": str | None,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
            "private_data": "filtered",
        }

    Safety guarantees:
    - voice_call: always False
    - vts_call: always False
    - obs_call: always False
    - real_input: always False
    - submit: always False
    - private_data: always "filtered"
    - No OpenAI/model calls
    - No ElevenLabs calls
    - No VTS calls
    - No OBS API calls
    """
    # Extract gate conditions from payload
    stream_mode = str(payload.get("stream_mode") or "quiet_waiting")
    speak_allowed = bool(payload.get("speak_allowed"))
    line = _sanitize_line(str(payload.get("line") or ""))

    # Resolve output path
    resolved_path = Path(str(path)) if path is not None else Path(str(payload.get("output_path") or _default_output_path()))

    # Check env gate
    env_name = write_env_name or _PUBLIC_SUBTITLE_WRITE_ENV
    env_enabled = os.environ.get(env_name, "").strip() in {"1", "true", "yes", "on"}
    if not env_enabled:
        return {
            "decision": "public_subtitle_hold",
            "output_path": str(resolved_path),
            "file_write": False,
            "bytes_written": None,
            "hold_reason": "missing_env",
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
            "private_data": "filtered",
        }

    # Check token gate (caller must validate token before calling)
    # If called without token check, default to hold
    # (The command handler should check token before calling this function)
    # For safety, we require the payload to carry a token_gated flag from the caller
    token_gated = bool(payload.get("_token_gated", False))
    if not token_gated:
        return {
            "decision": "public_subtitle_hold",
            "output_path": str(resolved_path),
            "file_write": False,
            "bytes_written": None,
            "hold_reason": "missing_token",
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
            "private_data": "filtered",
        }

    # Check stream mode gate
    if stream_mode != "talk_ready":
        return {
            "decision": "public_subtitle_hold",
            "output_path": str(resolved_path),
            "file_write": False,
            "bytes_written": None,
            "hold_reason": "stream_mode_not_talk_ready",
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
            "private_data": "filtered",
        }

    # Check speak gate
    if not speak_allowed:
        return {
            "decision": "public_subtitle_hold",
            "output_path": str(resolved_path),
            "file_write": False,
            "bytes_written": None,
            "hold_reason": "speak_not_allowed",
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
            "private_data": "filtered",
        }

    # Check line non-empty
    if not line:
        return {
            "decision": "public_subtitle_hold",
            "output_path": str(resolved_path),
            "file_write": False,
            "bytes_written": None,
            "hold_reason": "empty_line",
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
            "private_data": "filtered",
        }

    # All gates pass — write file
    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = resolved_path.with_suffix(resolved_path.suffix + ".tmp")
        tmp_path.write_text(line, encoding="utf-8")
        os.replace(tmp_path, resolved_path)
        bytes_written = len(line.encode("utf-8"))
        decision = "public_subtitle_file_written"
    except OSError:
        return {
            "decision": "public_subtitle_hold",
            "output_path": str(resolved_path),
            "file_write": False,
            "bytes_written": None,
            "hold_reason": "file_write_error",
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
            "private_data": "filtered",
        }

    return {
        "decision": decision,
        "output_path": str(resolved_path),
        "file_write": True,
        "bytes_written": bytes_written,
        "hold_reason": None,
        "subtitle_call": False,
        "voice_call": False,
        "vts_call": False,
        "obs_call": False,
        "real_input": False,
        "submit": False,
        "private_data": "filtered",
    }


def read_public_subtitle_status(
    path: str | Path | None = None,
    stale_ms: int = 10000,
) -> dict[str, Any]:
    """
    Read public subtitle file status for OBS Text Source monitoring.

    This function is pure read-only — it does NOT write any file.

    Args:
        path: Optional path override. If None, uses the default path.
        stale_ms: Stale threshold in milliseconds. File is considered stale if
            age_ms > stale_ms. Default is 10000 (10 seconds).

    Returns:
        A status dict:
        {
            "decision": "public_subtitle_status_only_no_write",
            "path": str,
            "exists": bool,
            "bytes": int | None,
            "age_ms": int | None,
            "fresh": bool,
            "stale_threshold_ms": int,
            "line": str,            # sanitized first line or empty
            "file_write": False,
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
        }

    Status classification:
    - missing file: exists=False, fresh=False, line=""
    - empty file: exists=True, bytes=0, fresh=True if recent, line=""
    - fresh file: exists=True, bytes>0, fresh=True
    - stale file: exists=True, bytes>0, fresh=False (age_ms > stale_ms)

    Safety guarantees:
    - file_write: always False
    - subtitle_call: always False
    - voice_call: always False
    - vts_call: always False
    - obs_call: always False
    - real_input: always False
    - submit: always False
    - No OpenAI/model calls
    - No ElevenLabs calls
    - No VTS calls
    - No OBS API calls
    """
    resolved_path = Path(str(path)) if path is not None else _default_output_path()
    resolved_path_str = str(resolved_path)
    stale_threshold_ms = int(stale_ms)

    if not resolved_path.exists():
        return {
            "decision": "public_subtitle_status_only_no_write",
            "path": resolved_path_str,
            "exists": False,
            "bytes": None,
            "age_ms": None,
            "fresh": False,
            "stale_threshold_ms": stale_threshold_ms,
            "line": "",
            "file_write": False,
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
        }

    # File exists — gather stats
    stat = resolved_path.stat()
    size_bytes = stat.st_size
    age_ms = int((time.time() - stat.st_mtime) * 1000)
    fresh = age_ms <= stale_threshold_ms

    # Read first line only (OBS reads one line at a time)
    try:
        raw_bytes = resolved_path.read_bytes()
        # Decode up to 4096 bytes to get the first line
        decoded = raw_bytes[:4096].decode("utf-8", errors="replace")
        first_line = decoded.split("\n", 1)[0].split("\r", 1)[0]
        line = _sanitize_line(first_line)
    except Exception:
        line = ""

    return {
        "decision": "public_subtitle_status_only_no_write",
        "path": resolved_path_str,
        "exists": True,
        "bytes": size_bytes,
        "age_ms": age_ms,
        "fresh": fresh,
        "stale_threshold_ms": stale_threshold_ms,
        "line": line,
        "file_write": False,
        "subtitle_call": False,
        "voice_call": False,
        "vts_call": False,
        "obs_call": False,
        "real_input": False,
        "submit": False,
    }


def clear_public_subtitle_file(
    path: str | Path | None = None,
    operator_approval_token: str | None = None,
    *,
    clear_env_name: str | None = None,
    approved_token: str | None = None,
) -> dict[str, Any]:
    """
    Clear the public subtitle file by writing an empty string.

    Gates (both must pass to write):
    - env NANA_OSU_PUBLIC_SUBTITLE_CLEAR_ENABLED=1, unless clear_env_name
      overrides it
    - operator approval token matches exactly, unless approved_token overrides it

    If any gate fails, returns a hold payload with file_write=False.

    Uses atomic write: write to temp file, then replace.
    Do NOT delete the file — write empty text so OBS handles it gracefully.

    Args:
        path: Optional path override. If None, uses the default path.
        operator_approval_token: The operator approval token.
        clear_env_name: optional lifecycle/specialized env gate name.
        approved_token: optional lifecycle/specialized approval token.

    Returns:
        A result dict:
        {
            "decision": "public_subtitle_cleared" | "public_subtitle_hold",
            "path": str,
            "file_write": True | False,
            "bytes_written": int | None,   # 0 on success
            "hold_reason": str | None,     # "missing_env", "missing_token", "file_write_error"
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
        }

    Safety guarantees:
    - subtitle_call: always False
    - voice_call: always False
    - vts_call: always False
    - obs_call: always False
    - real_input: always False
    - submit: always False
    - No OpenAI/model calls
    - No ElevenLabs calls
    - No VTS calls
    - No OBS API calls
    """
    resolved_path = Path(str(path)) if path is not None else _default_output_path()
    resolved_path_str = str(resolved_path)

    # Check env gate
    env_name = clear_env_name or _PUBLIC_SUBTITLE_CLEAR_ENV
    env_enabled = os.environ.get(env_name, "").strip() in {"1", "true", "yes", "on"}
    if not env_enabled:
        return {
            "decision": "public_subtitle_hold",
            "path": resolved_path_str,
            "file_write": False,
            "bytes_written": None,
            "hold_reason": "missing_env",
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
        }

    # Check token gate
    token = approved_token or _PUBLIC_SUBTITLE_CLEAR_TOKEN
    token_ok = str(operator_approval_token or "").strip() == token
    if not token_ok:
        return {
            "decision": "public_subtitle_hold",
            "path": resolved_path_str,
            "file_write": False,
            "bytes_written": None,
            "hold_reason": "missing_token",
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
        }

    # All gates pass — write empty string
    try:
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = resolved_path.with_suffix(resolved_path.suffix + ".tmp")
        tmp_path.write_text("", encoding="utf-8")
        os.replace(tmp_path, resolved_path)
    except OSError:
        return {
            "decision": "public_subtitle_hold",
            "path": resolved_path_str,
            "file_write": False,
            "bytes_written": None,
            "hold_reason": "file_write_error",
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
        }

    return {
        "decision": "public_subtitle_cleared",
        "path": resolved_path_str,
        "file_write": True,
        "bytes_written": 0,
        "hold_reason": None,
        "subtitle_call": False,
        "voice_call": False,
        "vts_call": False,
        "obs_call": False,
        "real_input": False,
        "submit": False,
    }
