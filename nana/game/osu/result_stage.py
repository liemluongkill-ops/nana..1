"""
osu! Result Stage — Phase 20.

Staged manual result helper for osu! result subtitle pipeline.
User stages result once, then auto-preview/write commands reuse staged result.

Schema:
{
  "schema": "nana.osu.staged_result.v1",
  "written_at": "...iso...",
  "activity_state": "result",
  "source": "manual_stage",
  "result_fields": { score, accuracy, misses, combo, max_combo, rank, passed },
  "song": "MP3 or unknown",
  "difficulty": "plow & nemi's hard or unknown",
  "private_data": "filtered",
  "voice_call": false,
  "vts_call": false,
  "obs_call": false,
  "real_input": false,
  "submit": false
}

Quick stage shortcuts (Phase 20):
- /osu-result-stage-quick: minimal flags (accuracy, misses, rank required)
- /osu-result-stage-strong: preset (accuracy=99.2, misses=0, rank=S)
- /osu-result-stage-weak: preset (accuracy=85.0, misses=50, rank=C)
- /osu-result-stage-fail: preset (accuracy=50.0, misses=200, rank=F)

No live input. No VTS. No ElevenLabs. No OBS API. No mouse/click. No submit.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# Default path for staged result JSON
DEFAULT_STAGED_RESULT_PATH = Path(__file__).resolve().parent / "data" / "staged_result.json"

# Default stale threshold in milliseconds
DEFAULT_STALE_MS = 600000  # 10 minutes


# --------------------------------------------------------------------------
# Schema constants
# --------------------------------------------------------------------------
_SCHEMA_NAME = "nana.osu.staged_result.v1"


# --------------------------------------------------------------------------
# Result flag parser (same pattern as result_source.py)
# --------------------------------------------------------------------------


def _is_result_bool_true(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes", "on"}


def parse_stage_flags(text: str) -> dict[str, Any]:
    """
    Parse staged result flags from command text.

    Supports:
      --score=<int>
      --accuracy=<float>
      --misses=<int>
      --combo=<int>
      --max-combo=<int>
      --rank=<S/A/B/C/D/F>
      --passed=true|false
      --activity-state=<result/menu/break>
      --path=<path>
      --stale-ms=<ms>

    Returns dict of parsed fields.
    """
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
            values["passed"] = _is_result_bool_true(raw_value)
        elif name == "activity-state":
            values["activity_state"] = raw_value.strip()
        elif name == "path":
            values["path"] = raw_value.strip()
        elif name == "stale-ms":
            values["stale_ms"] = int(raw_value)
    return values


# --------------------------------------------------------------------------
# Quick stage presets (Phase 20)
# --------------------------------------------------------------------------


_RESULT_PRESETS = {
    "strong": {
        "accuracy": 99.2,
        "misses": 0,
        "rank": "S",
        "passed": True,
    },
    "weak": {
        "accuracy": 85.0,
        "misses": 50,
        "rank": "C",
        "passed": True,
    },
    "fail": {
        "accuracy": 50.0,
        "misses": 200,
        "rank": "F",
        "passed": False,
    },
}


def build_quick_stage_fields(
    text: str,
    preset: str | None = None,
) -> dict[str, Any]:
    """
    Build result_fields dict for quick stage commands.

    Args:
        text: command text with optional --accuracy, --misses, --rank,
              --score, --combo, --max-combo, --passed flags
        preset: optional preset name ("strong", "weak", "fail") for defaults

    Defaults:
    - passed=True unless rank=F or --passed=false
    - score=0 if omitted
    - combo/max_combo omitted if not provided

    Returns dict with accuracy, misses, rank, optional score/combo/max_combo/passed.
    """
    # Start with preset defaults if given
    if preset and preset in _RESULT_PRESETS:
        result_fields: dict[str, Any] = dict(_RESULT_PRESETS[preset])
    else:
        result_fields = {}

    # Parse explicit flags (these override preset defaults)
    parsed = parse_stage_flags(text)

    # Map parsed flags to result_fields keys
    if "accuracy" in parsed:
        result_fields["accuracy"] = parsed["accuracy"]
    if "misses" in parsed:
        result_fields["misses"] = parsed["misses"]
    if "rank" in parsed:
        result_fields["rank"] = parsed["rank"]

    # Optional fields
    if "score" in parsed:
        result_fields["score"] = parsed["score"]
    if "combo" in parsed:
        result_fields["combo"] = parsed["combo"]
    if "max_combo" in parsed:
        result_fields["max_combo"] = parsed["max_combo"]

    if "score" not in result_fields:
        result_fields["score"] = 0

    # passed logic: explicit flag wins, else auto from rank
    if "passed" in parsed:
        result_fields["passed"] = parsed["passed"]
    elif result_fields.get("rank") == "F":
        result_fields["passed"] = False
    elif "passed" not in result_fields:
        # Default passed=True for non-F ranks unless explicitly set
        result_fields["passed"] = True

    return result_fields


# --------------------------------------------------------------------------
# Stage read/write helpers
# --------------------------------------------------------------------------


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> Path:
    """Write JSON atomically (write to .tmp then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
    return path


def write_staged_result(
    result_fields: dict[str, Any],
    activity_state: str = "result",
    song: str = "unknown",
    difficulty: str = "unknown",
    output_path: Path | None = None,
) -> dict[str, Any]:
    """
    Write staged result to JSON file.

    Args:
        result_fields: dict with score, accuracy, misses, combo, max_combo, rank, passed
        activity_state: default "result"
        song: song title, default "unknown"
        difficulty: difficulty name, default "unknown"
        output_path: optional path override

    Returns:
        {
            "decision": "result_stage_written",
            "path": Path,
            "written": True,
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "voice_call": False,
        }
    """
    path = output_path or DEFAULT_STAGED_RESULT_PATH

    if not result_fields:
        return {
            "decision": "result_stage_hold",
            "hold_reason": "missing_result_fields",
            "path": str(path),
            "written": False,
            "file_write": False,
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "voice_call": False,
        }

    payload = {
        "schema": _SCHEMA_NAME,
        "written_at": time.time(),
        "activity_state": activity_state,
        "source": "manual_stage",
        "result_fields": {k: v for k, v in result_fields.items() if v is not None},
        "song": song,
        "difficulty": difficulty,
        "private_data": "filtered",
        "voice_call": False,
        "vts_call": False,
        "obs_call": False,
        "real_input": False,
        "submit": False,
    }

    try:
        _write_json_atomic(path, payload)
        written = True
    except OSError:
        written = False

    return {
        "decision": "result_stage_written" if written else "result_stage_write_error",
        "path": str(path),
        "written": written,
        "file_write": written,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "obs_call": False,
        "voice_call": False,
    }


def read_staged_result(
    output_path: Path | None = None,
    stale_ms: int = DEFAULT_STALE_MS,
) -> dict[str, Any]:
    """
    Read staged result from JSON file.

    Args:
        output_path: optional path override
        stale_ms: stale threshold in milliseconds, default 600000 (10 minutes)

    Returns:
        {
            "exists": bool,
            "fresh": bool,
            "stale": bool,
            "stale_reason": str | None,
            "path": str,
            "age_ms": int | None,
            "written_at": float | None,
            "activity_state": str | None,
            "source": str | None,
            "result_fields": dict | None,
            "song": str | None,
            "difficulty": str | None,
            "schema_valid": bool,
            "real_input": False,
            "submit": False,
        }
    """
    path = output_path or DEFAULT_STAGED_RESULT_PATH

    # Check existence
    if not path.exists():
        return {
            "exists": False,
            "fresh": False,
            "stale": False,
            "stale_reason": None,
            "path": str(path),
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

    # Read file
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
    except (OSError, json.JSONDecodeError):
        return {
            "exists": True,
            "fresh": False,
            "stale": True,
            "stale_reason": "staged_result_parse_error",
            "path": str(path),
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

    # Validate schema
    schema = data.get("schema", "")
    if schema != _SCHEMA_NAME:
        return {
            "exists": True,
            "fresh": False,
            "stale": True,
            "stale_reason": "staged_result_invalid_schema",
            "path": str(path),
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

    # Check freshness
    written_at = data.get("written_at")
    if written_at is None:
        return {
            "exists": True,
            "fresh": False,
            "stale": True,
            "stale_reason": "staged_result_missing_written_at",
            "path": str(path),
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

    now = time.time()
    age_ms = int((now - written_at) * 1000)
    is_fresh = age_ms <= stale_ms
    stale_reason = None if is_fresh else f"staged_result_stale:{age_ms}ms>{stale_ms}ms"

    return {
        "exists": True,
        "fresh": is_fresh,
        "stale": not is_fresh,
        "stale_reason": stale_reason,
        "path": str(path),
        "age_ms": age_ms,
        "written_at": written_at,
        "activity_state": data.get("activity_state"),
        "source": data.get("source"),
        "result_fields": data.get("result_fields"),
        "song": data.get("song"),
        "difficulty": data.get("difficulty"),
        "schema_valid": True,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "obs_call": False,
        "voice_call": False,
    }


def clear_staged_result(
    operator_approval_token: str | None = None,
    output_path: Path | None = None,
    *,
    clear_env_name: str | None = None,
    approved_token: str | None = None,
) -> dict[str, Any]:
    """
    Clear staged result file.

    Gates:
    - env NANA_OSU_RESULT_STAGE_CLEAR_ENABLED=1, unless clear_env_name overrides it
    - operator approval token matches exactly I_APPROVE_OSU_RESULT_STAGE_CLEAR,
      unless approved_token overrides it

    Args:
        operator_approval_token: exact token string
        output_path: optional path override
        clear_env_name: optional lifecycle/specialized env gate name
        approved_token: optional lifecycle/specialized approval token

    Returns:
        {
            "decision": "result_stage_cleared" | "result_stage_hold",
            "hold_reason": str | None,
            "path": str,
            "cleared": bool,
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "voice_call": False,
        }
    """
    APPROVED_TOKEN = approved_token or "I_APPROVE_OSU_RESULT_STAGE_CLEAR"
    CLEAR_ENV = clear_env_name or "NANA_OSU_RESULT_STAGE_CLEAR_ENABLED"

    path = output_path or DEFAULT_STAGED_RESULT_PATH

    # Check env gate
    env_enabled = os.environ.get(CLEAR_ENV, "").strip() in {"1", "true", "yes", "on"}
    if not env_enabled:
        return {
            "decision": "result_stage_hold",
            "hold_reason": "missing_env",
            "path": str(path),
            "cleared": False,
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "voice_call": False,
        }

    # Check token gate
    token_ok = operator_approval_token == APPROVED_TOKEN
    if not token_ok:
        return {
            "decision": "result_stage_hold",
            "hold_reason": "missing_token",
            "path": str(path),
            "cleared": False,
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "voice_call": False,
        }

    # All gates pass — clear file
    try:
        if path.exists():
            path.unlink()
        cleared = True
    except OSError:
        cleared = False

    return {
        "decision": "result_stage_cleared" if cleared else "result_stage_clear_error",
        "hold_reason": None,
        "path": str(path),
        "cleared": cleared,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "obs_call": False,
        "voice_call": False,
    }


# --------------------------------------------------------------------------
# Staged result snapshot (for merge_result_sources integration)
# --------------------------------------------------------------------------


def get_staged_result_snapshot(
    stale_ms: int = DEFAULT_STALE_MS,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """
    Get staged result as a snapshot dict compatible with merge_result_sources.

    Returns:
        {
            "available": bool,
            "unavailable_reason": str | None,
            "source": "staged_manual",
            "activity_state": str | None,
            "result_fields": dict | None,
            "song_title": str | None,
            "difficulty": str | None,
        }

    Available only if:
    - File exists
    - Schema valid
    - Not stale
    - Has result_fields
    """
    status = read_staged_result(output_path=output_path, stale_ms=stale_ms)

    if not status["exists"]:
        return {
            "available": False,
            "unavailable_reason": "staged_result_not_found",
            "source": "staged_manual",
            "activity_state": None,
            "result_fields": None,
            "song_title": None,
            "difficulty": None,
        }

    if status["stale"]:
        return {
            "available": False,
            "unavailable_reason": status["stale_reason"],
            "source": "staged_manual",
            "activity_state": None,
            "result_fields": None,
            "song_title": None,
            "difficulty": None,
        }

    if not status["schema_valid"]:
        return {
            "available": False,
            "unavailable_reason": "staged_result_invalid_schema",
            "source": "staged_manual",
            "activity_state": None,
            "result_fields": None,
            "song_title": None,
            "difficulty": None,
        }

    result_fields = status["result_fields"]
    if not result_fields:
        return {
            "available": False,
            "unavailable_reason": "staged_result_empty_fields",
            "source": "staged_manual",
            "activity_state": None,
            "result_fields": None,
            "song_title": None,
            "difficulty": None,
        }

    return {
        "available": True,
        "unavailable_reason": None,
        "source": "staged_manual",
        "activity_state": status["activity_state"],
        "result_fields": result_fields,
        "song_title": status["song"],
        "difficulty": status["difficulty"],
    }


# --------------------------------------------------------------------------
# Print helpers for registry commands
# --------------------------------------------------------------------------


def print_stage_result_write(result: dict[str, Any]) -> None:
    print("osu! Result Stage")
    print(f"  Decision: {result['decision']}")
    if result.get("hold_reason"):
        print(f"  Hold reason: {result['hold_reason']}")
    print(f"  Path: {result['path']}")
    print(f"  Written: {str(result['written']).lower()}")
    print(f"  File write: {str(result.get('file_write', result['written'])).lower()}")
    print(f"  Real input: {result['real_input']}")
    print(f"  Submit: {result['submit']}")
    print(f"  VTS call: {result['vts_call']}")
    print(f"  OBS call: {result['obs_call']}")
    print(f"  Voice call: {result['voice_call']}")


def print_stage_result_status(status: dict[str, Any]) -> None:
    print("osu! Result Stage Status")
    print(f"  Decision: stage_status_only_no_write")
    print(f"  Path: {status['path']}")
    print(f"  Exists: {str(status['exists']).lower()}")
    print(f"  Fresh: {str(status['fresh']).lower()}")
    print(f"  Stale: {str(status['stale']).lower()}")
    if status["stale_reason"]:
        print(f"  Stale reason: {status['stale_reason']}")
    if status["age_ms"] is not None:
        print(f"  Age ms: {status['age_ms']}")
    if status["written_at"] is not None:
        import time as time_module

        readable = time_module.ctime(status["written_at"])
        print(f"  Written at: {readable}")
    if status["activity_state"]:
        print(f"  Activity state: {status['activity_state']}")
    if status["result_fields"]:
        fields = status["result_fields"]
        if fields.get("score") is not None:
            print(f"  score: {fields['score']}")
        if fields.get("accuracy") is not None:
            print(f"  accuracy: {fields['accuracy']}")
        if fields.get("misses") is not None:
            print(f"  misses: {fields['misses']}")
        if fields.get("combo") is not None:
            print(f"  combo: {fields['combo']}")
        if fields.get("max_combo") is not None:
            print(f"  max_combo: {fields['max_combo']}")
        if fields.get("rank") is not None:
            print(f"  rank: {fields['rank']}")
        if fields.get("passed") is not None:
            print(f"  passed: {fields['passed']}")
    if status["song"]:
        print(f"  song: {status['song']}")
    if status["difficulty"]:
        print(f"  difficulty: {status['difficulty']}")
    print(f"  File write: False")
    print(f"  Voice call: {status.get('voice_call', False)}")
    print(f"  VTS call: {status.get('vts_call', False)}")
    print(f"  OBS call: {status.get('obs_call', False)}")
    print(f"  Real input: {status['real_input']}")
    print(f"  Submit: {status['submit']}")


def print_stage_result_clear(result: dict[str, Any]) -> None:
    print("osu! Result Stage Clear")
    print(f"  Decision: {result['decision']}")
    if result.get("hold_reason"):
        print(f"  Hold reason: {result['hold_reason']}")
    print(f"  Path: {result['path']}")
    print(f"  Cleared: {str(result['cleared']).lower()}")
    print(f"  File write: {str(result['cleared']).lower()}")
    print(f"  Real input: {result['real_input']}")
    print(f"  Submit: {result['submit']}")
    print(f"  VTS call: {result['vts_call']}")
    print(f"  OBS call: {result['obs_call']}")
    print(f"  Voice call: {result['voice_call']}")


def print_quick_stage_result(result: dict[str, Any], preset: str | None = None) -> None:
    """
    Print output for quick stage commands.

    Args:
        result: write_staged_result output dict
        preset: preset name if applicable (None for /osu-result-stage-quick)
    """
    title = "osu! Result Stage Quick"
    if preset:
        title = f"osu! Result Stage {preset.title()}"
    print(title)
    print(f"  Decision: {result['decision']}")
    print(f"  Path: {result['path']}")
    print(f"  Written: {str(result['written']).lower()}")
    print(f"  File write: {str(result.get('file_write', result['written'])).lower()}")
    print(f"  Real input: {result['real_input']}")
    print(f"  Submit: {result['submit']}")
    print(f"  VTS call: {result['vts_call']}")
    print(f"  OBS call: {result['obs_call']}")
    print(f"  Voice call: {result['voice_call']}")
