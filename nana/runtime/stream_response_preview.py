"""
Stream Response Preview — companion-level line preview for osu! result/menu/break states.

Runtime boundary: pure deterministic template. No model calls, no VTS, no ElevenLabs.

Receives a core preview (from stream_event_core) and returns a companion-level
response preview with a deterministic line candidate based on performance tags.

No live input. No VTS. No ElevenLabs. No OBS. No mouse/click. No submit.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Response style constants
# ---------------------------------------------------------------------------
_STYLE_SILENT = "silent"
_STYLE_WAITING = "waiting"
_STYLE_RECOVER = "recover"
_STYLE_REVIEW_WEAK = "review_weak_result"
_STYLE_REVIEW_STRONG = "review_strong_result"
_STYLE_REVIEW_PASSED = "review_passed_result"


# ---------------------------------------------------------------------------
# Deterministic template lines (Vietnamese, no private data)
# ---------------------------------------------------------------------------

def _make_style(style: str, tags: list[str]) -> str:
    """Return deterministic line candidate and style based on tags and style."""
    if style == _STYLE_SILENT:
        return ""

    if style == _STYLE_WAITING:
        return ""

    if style == _STYLE_RECOVER:
        return "Ván này rơi rồi, mình reset nhịp lại đã."

    if style == _STYLE_REVIEW_WEAK:
        return "Ván này hơi nhiều miss, mình giữ nhịp lại cho chắc hơn."

    if style == _STYLE_REVIEW_STRONG:
        return "Ván này sạch và ổn đấy, giữ phong độ này là đẹp."

    if style == _STYLE_REVIEW_PASSED:
        return "Qua được rồi, nhưng mình vẫn còn chỗ để tối ưu."

    return ""


# ---------------------------------------------------------------------------
# Core entry point
# ---------------------------------------------------------------------------

def build_stream_response_preview(event_or_core_preview: dict[str, Any]) -> dict[str, Any]:
    """
    Build companion-level response preview from a core event preview.

    Args:
        event_or_core_preview: A core preview dict from ingest_stream_event_preview,
            or a result-specific event dict. Expected keys:
            - stream_mode: str  (talk_ready | silent_play | quiet_waiting)
            - speak_allowed: bool
            - performance_tags: list[str]
            - source_game: str  (optional, defaults to "unknown")
            - activity_state: str  (optional)
            - (other fields pass through)

    Returns:
        A companion response preview dict:
        {
            "decision": "companion_response_preview_only_no_output",
            "source_game": str,
            "activity_state": str,
            "stream_mode": str,
            "speak_allowed": bool,
            "response_intent": str,   # "review_result" | "waiting" | "silent"
            "response_style": str,    # "silent" | "waiting" | "recover" |
                                     # "review_weak_result" | "review_strong_result" |
                                     # "review_passed_result"
            "line_candidate": str,   # deterministic template line or ""
            "performance_tags": list[str],
            "private_data": "filtered",
            "voice_call": False,
            "vts_call": False,
            "subtitle_call": False,
            "obs_call": False,
            "real_input": False,
            "submit": False,
        }

    Safety guarantees:
    - voice_call: always False
    - vts_call: always False
    - subtitle_call: always False
    - obs_call: always False
    - real_input: always False
    - submit: always False
    - private_data: always "filtered"
    - No OpenAI/model calls
    - No ElevenLabs calls
    - No VTS calls
    - No OBS calls
    """
    # Extract from nested core preview structure
    stream_mode = str(event_or_core_preview.get("stream_mode") or "quiet_waiting")
    speak_allowed = bool(event_or_core_preview.get("speak_allowed"))

    # performance_tags may be in the core preview directly
    tags: list[str] = list(event_or_core_preview.get("performance_tags") or [])

    # Source game
    source_game = str(event_or_core_preview.get("source_game") or "unknown")

    # Activity state
    activity_state = str(event_or_core_preview.get("activity_state") or "unknown")

    # Determine response style and line
    if stream_mode == "quiet_waiting":
        style = _STYLE_WAITING
        line = ""
        response_intent = "waiting"
    elif not speak_allowed or stream_mode == "silent_play":
        style = _STYLE_SILENT
        line = ""
        response_intent = "silent"
    elif speak_allowed and stream_mode == "talk_ready":
        # Result/menu/break with data: select template by tags
        line, style = _select_result_template(tags)
        response_intent = "review_result"
    else:
        style = _STYLE_WAITING
        line = ""
        response_intent = "waiting"

    return {
        "decision": "companion_response_preview_only_no_output",
        "source_game": source_game,
        "activity_state": activity_state,
        "stream_mode": stream_mode,
        "speak_allowed": speak_allowed,
        "response_intent": response_intent,
        "response_style": style,
        "line_candidate": line,
        "performance_tags": list(tags),
        "private_data": "filtered",
        "voice_call": False,
        "vts_call": False,
        "subtitle_call": False,
        "obs_call": False,
        "real_input": False,
        "submit": False,
    }


def _select_result_template(tags: list[str]) -> tuple[str, str]:
    """
    Select deterministic template line and style based on performance tags.

    Priority:
    1. failed -> recover
    2. high_miss_count OR low_accuracy -> review_weak_result
    3. clean_run AND good_accuracy AND strong_result -> review_strong_result
    4. passed (normal) -> review_passed_result
    5. no data -> waiting

    Returns:
        (line_candidate, response_style)
    """
    tag_set = set(tags)

    # Priority 1: failed
    if "failed" in tag_set:
        return (
            "Ván này rơi rồi, mình reset nhịp lại đã.",
            _STYLE_RECOVER,
        )

    # Priority 2: weak result (high miss count OR low accuracy)
    if "high_miss_count" in tag_set or "low_accuracy" in tag_set:
        return (
            "Ván này hơi nhiều miss, mình giữ nhịp lại cho chắc hơn.",
            _STYLE_REVIEW_WEAK,
        )

    # Priority 3: strong result (clean run AND good accuracy AND strong rank)
    if "clean_run" in tag_set and "good_accuracy" in tag_set and "strong_result" in tag_set:
        return (
            "Ván này sạch và ổn đấy, giữ phong độ này là đẹp.",
            _STYLE_REVIEW_STRONG,
        )

    # Priority 4: passed (normal)
    if "passed" in tag_set:
        return (
            "Qua được rồi, nhưng mình vẫn còn chỗ để tối ưu.",
            _STYLE_REVIEW_PASSED,
        )

    # Priority 5: no recognized tag — waiting
    return ("", _STYLE_WAITING)
