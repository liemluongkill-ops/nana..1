"""
Stream Event Core — Runtime boundary: Nana core ingest preview for game events.

Receives only public-safe fields from game stream layer.
Returns a core decision preview with performance tags, speak gate enforcement,
and all safety flags set to False.

No live input. No VTS. No ElevenLabs. No mouse/click. No submit.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Public-safe input field whitelist
# ---------------------------------------------------------------------------
ALLOWED_INPUT_FIELDS = {
    "game",
    "activity_state",
    "stream_mode",
    "speak_allowed",
    "subtitle_allowed",
    "voice_allowed",
    "song_title",
    "difficulty",
    "artist",
    "reaction",
    "safe_hint",
    "osu_status",
    "score",
    "accuracy",
    "combo",
    "max_combo",
    "misses",
    "rank",
    "passed",
}


def _filter_public_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Strip all keys not in ALLOWED_INPUT_FIELDS."""
    return {k: v for k, v in raw.items() if k in ALLOWED_INPUT_FIELDS}


# ---------------------------------------------------------------------------
# Performance tag logic
# ---------------------------------------------------------------------------

def _compute_performance_tags(event: dict[str, Any]) -> list[str]:
    tags: list[str] = []

    # Accuracy
    accuracy = event.get("accuracy")
    if accuracy is not None:
        try:
            acc = float(accuracy)
            if acc >= 98.0:
                tags.append("good_accuracy")
            elif acc < 95.0:
                tags.append("low_accuracy")
        except (TypeError, ValueError):
            pass

    # Misses
    misses = event.get("misses")
    if misses is not None:
        try:
            m = int(misses)
            if m >= 10:
                tags.append("high_miss_count")
            elif m == 0:
                tags.append("clean_run")
        except (TypeError, ValueError):
            pass

    # Rank
    rank = str(event.get("rank") or "").upper().strip()
    if rank in {"S", "SS", "X"}:
        tags.append("strong_result")
    elif rank in {"C", "D", "F"}:
        tags.append("weak_result")

    # Passed
    passed = event.get("passed")
    if passed is True or passed == "True" or passed == "true":
        tags.append("passed")
    elif passed is False or passed == "False" or passed == "false":
        tags.append("failed")

    return tags


# ---------------------------------------------------------------------------
# Speak gate enforcement for line_candidate
# ---------------------------------------------------------------------------

def _enforce_speak_gate(event: dict[str, Any]) -> dict[str, Any]:
    """
    Enforce speak gate on an already-built event.

    Rules:
    - stream_mode == "silent_play" OR speak_allowed == False:
        line_candidate -> None or ""
        voice_call -> False
        subtitle_allowed -> False
    - stream_mode == "talk_ready" AND speak_allowed == True:
        line_candidate stays (None or placeholder); no voice call
    """
    stream_mode = str(event.get("stream_mode") or "")
    speak_allowed = bool(event.get("speak_allowed"))

    if stream_mode == "silent_play" or not speak_allowed:
        event["line_candidate"] = None if stream_mode == "silent_play" else ""
        event["voice_call"] = False
        event["subtitle_allowed"] = False
    else:
        # talk_ready / quiet_waiting with speak_allowed=True
        # keep line_candidate as-is (None or placeholder); no voice
        event["voice_call"] = False

    return event


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

# States that should NOT use silent_play — they may talk/review
_RESULT_LIKE_STATES = {"result", "menu", "break"}


def ingest_stream_event_preview(raw_event: dict[str, Any]) -> dict[str, Any]:
    """
    Ingest a public-safe game event and return a core decision preview.

    Args:
        raw_event: A dict that may contain any fields, but only
                   ALLOWED_INPUT_FIELDS are consumed.

    Returns:
        A preview dict:
        {
            "decision": "core_event_preview_only_no_output",
            "received": True/False,
            "source_game": str,
            "activity_state": str,
            "stream_mode": str,
            "speak_allowed": bool,
            "performance_tags": list[str],
            "private_data": "filtered",
            "line_candidate": None or "",
            "voice_call": False,
            "vts_call": False,
            "real_input": False,
            "submit": False,
        }

    Safety guarantees:
    - voice_call: always False
    - vts_call: always False
    - real_input: always False
    - submit: always False
    - private_data: always "filtered"
    """
    if not isinstance(raw_event, dict) or not raw_event:
        return _empty_preview()

    event = _filter_public_fields(raw_event)

    if not event:
        return _empty_preview()

    game = str(event.get("game") or "unknown")
    activity_state = str(event.get("activity_state") or "unknown")
    stream_mode = str(event.get("stream_mode") or "quiet_waiting")
    speak_allowed = bool(event.get("speak_allowed"))

    # Build base preview
    preview: dict[str, Any] = {
        "decision": "core_event_preview_only_no_output",
        "received": True,
        "source_game": game,
        "activity_state": activity_state,
        "stream_mode": stream_mode,
        "speak_allowed": speak_allowed,
        "performance_tags": _compute_performance_tags(event),
        "private_data": "filtered",
        "line_candidate": None,
        "voice_call": False,
        "vts_call": False,
        "real_input": False,
        "submit": False,
    }

    # Apply speak gate enforcement
    _enforce_speak_gate(preview)

    return preview


def _empty_preview() -> dict[str, Any]:
    return {
        "decision": "core_event_preview_only_no_output",
        "received": False,
        "source_game": "unknown",
        "activity_state": "unknown",
        "stream_mode": "quiet_waiting",
        "speak_allowed": False,
        "performance_tags": [],
        "private_data": "filtered",
        "line_candidate": None,
        "voice_call": False,
        "vts_call": False,
        "real_input": False,
        "submit": False,
    }
