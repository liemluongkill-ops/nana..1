"""Minimal local event timeline for stream/public-flow diagnostics.

The timeline is diagnostic only. It records local events to an in-memory ring
and a local JSONL file; it never calls LLM/TTS/VTS/OBS/Discord or game input.
Do not persist raw viewer text or full reply text here; use counts, status,
and short deterministic hashes when correlation is needed.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
import json
import os
import threading
import time
from typing import Any


PHASE = "STAGE-9Z"
DEFAULT_TIMELINE_PATH = Path(__file__).resolve().parent / "data" / "stream_event_timeline.jsonl"
DEFAULT_RING_LIMIT = 300

_LOCK = threading.Lock()
_EVENTS: deque[dict[str, Any]] = deque(maxlen=DEFAULT_RING_LIMIT)
_SEQ = 0
_TOTAL_RECORDED = 0
_FILE_WRITES = 0
_FILE_ERRORS = 0
_LAST_WRITE_ERROR = ""


def _timeline_path() -> Path:
    raw = os.environ.get("NANA_STREAM_EVENT_TIMELINE_PATH")
    return Path(raw).expanduser() if raw else DEFAULT_TIMELINE_PATH


def _preview(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return _preview(value, 120)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _preview(value, 1000)
    if isinstance(value, dict):
        return {
            str(key)[:100]: _json_safe(item, depth=depth + 1)
            for key, item in list(value.items())[:40]
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item, depth=depth + 1) for item in list(value)[:40]]
    return _preview(value, 240)


def record_stream_event(kind: str, **fields: Any) -> dict[str, Any]:
    """Record one local diagnostic event.

    This function is deliberately fail-soft: caller behavior must not change if
    JSONL append fails.
    """

    global _SEQ, _TOTAL_RECORDED, _FILE_WRITES, _FILE_ERRORS, _LAST_WRITE_ERROR
    now = time.time()
    safe_kind = _preview(kind or "unknown", 80) or "unknown"
    safe_fields = {str(key)[:100]: _json_safe(value) for key, value in fields.items()}
    with _LOCK:
        _SEQ += 1
        _TOTAL_RECORDED += 1
        event = {
            "seq": _SEQ,
            "phase": PHASE,
            "kind": safe_kind,
            "timestamp": now,
            "iso_time": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now)),
            **safe_fields,
        }
        _EVENTS.append(event)
        path = _timeline_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                json.dump(event, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
            _FILE_WRITES += 1
            _LAST_WRITE_ERROR = ""
        except Exception as exc:
            _FILE_ERRORS += 1
            _LAST_WRITE_ERROR = f"{type(exc).__name__}: {exc}"
    return dict(event)


def clear_stream_event_timeline(*, remove_file: bool = False) -> dict[str, Any]:
    """Clear local diagnostic timeline state. Intended for smokes/tests."""

    global _SEQ, _TOTAL_RECORDED, _FILE_WRITES, _FILE_ERRORS, _LAST_WRITE_ERROR
    with _LOCK:
        cleared = len(_EVENTS)
        _EVENTS.clear()
        _SEQ = 0
        _TOTAL_RECORDED = 0
        _FILE_WRITES = 0
        _FILE_ERRORS = 0
        _LAST_WRITE_ERROR = ""
        path = _timeline_path()
        removed_file = False
        if remove_file:
            try:
                path.unlink(missing_ok=True)
                removed_file = True
            except Exception as exc:
                _FILE_ERRORS += 1
                _LAST_WRITE_ERROR = f"{type(exc).__name__}: {exc}"
        return {
            "cleared": cleared,
            "removed_file": removed_file,
            "path": str(path),
            "can_act": False,
        }


def stream_event_timeline_snapshot(limit: int = 30) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 30), 200))
    now = time.time()
    with _LOCK:
        recent = list(_EVENTS)[-safe_limit:]
        events = []
        for event in recent:
            item = dict(event)
            item["age_seconds"] = round(max(0.0, now - float(item.get("timestamp") or now)), 1)
            events.append(item)
        last = events[-1] if events else None
        return {
            "phase": PHASE,
            "mode": "append_only_local_diagnostic_timeline",
            "read_only": True,
            "can_act": False,
            "memory_write": False,
            "api_call": False,
            "llm_call": False,
            "tts_call": False,
            "vts_call": False,
            "obs_call": False,
            "discord_call": False,
            "game_input": False,
            "diagnostic_file_write": True,
            "path": str(_timeline_path()),
            "ring_limit": DEFAULT_RING_LIMIT,
            "count": len(_EVENTS),
            "total_recorded": _TOTAL_RECORDED,
            "file_writes": _FILE_WRITES,
            "file_errors": _FILE_ERRORS,
            "last_write_error": _LAST_WRITE_ERROR or "none",
            "last_event": last,
            "events": events,
        }


def _format_event(event: dict[str, Any]) -> str:
    request_id = event.get("request_id") or event.get("source_id") or "none"
    status = event.get("status") or event.get("action") or event.get("reason") or "none"
    age = event.get("age_seconds", 0.0)
    return (
        f"  - #{event.get('seq')} {event.get('kind')} | age={age:.1f}s | "
        f"request={request_id} | status={status}"
    )


def stream_event_timeline_status_lines(limit: int = 15) -> list[str]:
    snap = stream_event_timeline_snapshot(limit=limit)
    last = dict(snap.get("last_event") or {})
    last_text = (
        f"{last.get('kind')}:{last.get('age_seconds', 0.0):.1f}s"
        if last
        else "none"
    )
    lines = [
        f"Stream Event Timeline ({PHASE})",
        "  Mode: append-only local diagnostics | read_only=True | can_act=False",
        (
            "  Storage: "
            f"events={snap.get('count')}/{snap.get('ring_limit')} | "
            f"total={snap.get('total_recorded')} | jsonl={snap.get('path')}"
        ),
        (
            "  File: "
            f"writes={snap.get('file_writes')} | errors={snap.get('file_errors')} | "
            f"last_error={snap.get('last_write_error')}"
        ),
        f"  Last: {last_text}",
        "  Recent:",
    ]
    events = list(snap.get("events") or [])
    if events:
        lines.extend(_format_event(event) for event in events)
    else:
        lines.append("  - none")
    lines.append(
        "  Safety: no LLM/TTS/VTS/OBS/Discord/API/game input; diagnostic file write only; no memory write."
    )
    return lines
