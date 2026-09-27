"""
Stream Speak Gate — decides whether Nana may speak/commentate during osu! gameplay.

Runtime boundary guard: gates the companion/stream output layer.
No live input, no VTS, no ElevenLabs calls.
"""

from __future__ import annotations

from typing import Any


ACTIVE_GAMEPLAY_STATUSES = {"tracking", "fast_jump", "dense_pattern"}
NON_GAMEPLAY_STATUSES = {"idle"}
UNKNOWN_STATUSES = {"uncalibrated", "stale", "unavailable"}


def evaluate_stream_speak_gate(event: dict[str, Any]) -> dict[str, Any]:
    """
    Evaluate the stream speak gate for a given event.

    Args:
        event: A dict that must contain at minimum an "osu_status" key.
               Expected keys (all optional for the gate itself):
               - osu_status: str — the current aim/gameplay status
               - reaction: str
               - song_title: str
               - difficulty: str
               - safe_hint: str
               - line: str — the raw preview line

    Returns:
        A dict with the gate decision fields:
        - stream_mode: str  — silent_play | talk_ready | quiet_waiting
        - speak_allowed: bool
        - voice_allowed: bool
        - subtitle_allowed: bool
        - reason: str
    """
    status = str(event.get("osu_status") or "")

    if status in ACTIVE_GAMEPLAY_STATUSES:
        return {
            "stream_mode": "silent_play",
            "speak_allowed": False,
            "voice_allowed": False,
            "subtitle_allowed": False,
            "reason": "active_gameplay_silence",
        }

    if status in NON_GAMEPLAY_STATUSES:
        return {
            "stream_mode": "talk_ready",
            "speak_allowed": True,
            "voice_allowed": False,
            "subtitle_allowed": True,
            "reason": "non_gameplay_talk_allowed",
        }

    # Unknown / stale / uncalibrated — default to quiet waiting
    return {
        "stream_mode": "quiet_waiting",
        "speak_allowed": False,
        "voice_allowed": False,
        "subtitle_allowed": False,
        "reason": "unknown_state_quiet_waiting",
    }
