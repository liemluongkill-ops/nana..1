"""Public-stage diagnostic command router.

This module keeps read-only public-stage status/preview commands out of the
main text dispatcher.  It does not call Discord, TTS, VTS, OBS, or game input.
"""

from __future__ import annotations

from collections.abc import Iterable

from nana.runtime.public_conversation_director import (
    public_conversation_preview_lines,
    public_conversation_status_lines,
)
from nana.runtime.public_fallback_recovery import (
    public_fallback_recovery_preview_lines,
    public_fallback_recovery_status_lines,
)
from nana.runtime.public_fluency_polish import (
    public_fluency_preview_lines,
    public_fluency_status_lines,
)
from nana.runtime.public_memory_filter import (
    public_memory_filter_preview_lines,
    public_memory_filter_status_lines,
)
from nana.runtime.public_quiet_room_rhythm import (
    public_quiet_room_preview_lines,
    public_quiet_room_status_lines,
)
from nana.runtime.public_reply_evaluator import (
    public_reply_eval_preview_lines,
    public_reply_eval_status_lines,
)
from nana.runtime.public_reply_feedback import (
    public_reply_feedback_preview_lines,
    public_reply_feedback_status_lines,
)
from nana.runtime.public_running_jokes import (
    public_running_jokes_preview_lines,
    public_running_jokes_status_lines,
)
from nana.runtime.public_scene_builder import (
    public_scene_preview_lines,
    public_scene_status_lines,
)
from nana.runtime.public_thread_memory import (
    public_thread_memory_preview_lines,
    public_thread_memory_status_lines,
)
from nana.runtime.public_viewer_memory import (
    public_viewer_memory_preview_lines,
    public_viewer_memory_status_lines,
)
from nana.runtime.public_voice_style import (
    public_voice_preview_lines,
    public_voice_status_lines,
    public_voice_test_lines,
)


def _print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        print(line)


def _rest(text: str, prefix: str) -> str:
    return text[len(prefix):].strip()


def _starts_with_any(text_lower: str, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        if text_lower.startswith(prefix):
            return prefix
    return ""


def handle_public_stage_command(text: str, text_lower: str | None = None) -> bool:
    """Handle public-stage read-only command surfaces.

    Returns True when a command was handled and the caller should stop dispatch.
    """

    text_lower = text_lower or text.lower()

    if text_lower in {"/public-voice-status", "/voice-style-status", "/stage-voice-status"}:
        _print_lines(public_voice_status_lines())
        return True

    if text_lower in {"/public-voice-preview", "/voice-style-preview", "/stage-voice-preview"}:
        _print_lines(public_voice_preview_lines())
        return True

    prefix = _starts_with_any(text_lower, ("/public-voice-test", "/voice-style-test"))
    if prefix:
        _print_lines(public_voice_test_lines(_rest(text, prefix)))
        return True

    if text_lower in {"/public-conversation-status", "/conversation-director-status", "/public-thread-status"}:
        _print_lines(public_conversation_status_lines())
        return True

    prefix = _starts_with_any(text_lower, ("/public-conversation-preview", "/conversation-director-preview"))
    if prefix:
        _print_lines(public_conversation_preview_lines(_rest(text, prefix)))
        return True

    if text_lower in {"/public-thread-memory-status", "/thread-memory-status"}:
        _print_lines(public_thread_memory_status_lines())
        return True

    prefix = _starts_with_any(text_lower, ("/public-thread-memory-preview", "/thread-memory-preview"))
    if prefix:
        _print_lines(public_thread_memory_preview_lines(_rest(text, prefix)))
        return True

    if text_lower in {"/public-joke-bank-status", "/running-joke-status", "/joke-bank-status"}:
        _print_lines(public_running_jokes_status_lines())
        return True

    prefix = _starts_with_any(
        text_lower,
        ("/public-joke-bank-preview", "/running-joke-preview", "/joke-bank-preview"),
    )
    if prefix:
        _print_lines(public_running_jokes_preview_lines(_rest(text, prefix)))
        return True

    if text_lower in {"/public-viewer-memory-status", "/viewer-memory-status"}:
        _print_lines(public_viewer_memory_status_lines())
        return True

    prefix = _starts_with_any(text_lower, ("/public-viewer-memory-preview", "/viewer-memory-preview"))
    if prefix:
        _print_lines(public_viewer_memory_preview_lines(_rest(text, prefix)))
        return True

    if text_lower in {"/public-scene-status", "/scene-builder-status", "/topic-builder-status"}:
        _print_lines(public_scene_status_lines())
        return True

    prefix = _starts_with_any(
        text_lower,
        ("/public-scene-preview", "/scene-builder-preview", "/topic-builder-preview"),
    )
    if prefix:
        _print_lines(public_scene_preview_lines(_rest(text, prefix)))
        return True

    if text_lower in {"/public-quiet-room-status", "/quiet-room-status", "/room-rhythm-status"}:
        _print_lines(public_quiet_room_status_lines())
        return True

    prefix = _starts_with_any(
        text_lower,
        ("/public-quiet-room-preview", "/quiet-room-preview", "/room-rhythm-preview"),
    )
    if prefix:
        _print_lines(public_quiet_room_preview_lines(_rest(text, prefix)))
        return True

    if text_lower in {"/public-fluency-status", "/fluency-status", "/vietnamese-polish-status"}:
        _print_lines(public_fluency_status_lines())
        return True

    prefix = _starts_with_any(
        text_lower,
        ("/public-fluency-preview", "/fluency-preview", "/vietnamese-polish-preview"),
    )
    if prefix:
        _print_lines(public_fluency_preview_lines(_rest(text, prefix)))
        return True

    if text_lower in {"/public-reply-eval-status", "/reply-eval-status", "/public-output-eval-status"}:
        _print_lines(public_reply_eval_status_lines())
        return True

    prefix = _starts_with_any(
        text_lower,
        ("/public-reply-eval-preview", "/reply-eval-preview", "/public-output-eval-preview"),
    )
    if prefix:
        _print_lines(public_reply_eval_preview_lines(_rest(text, prefix)))
        return True

    if text_lower in {"/public-reply-feedback-status", "/reply-feedback-status", "/public-feedback-status"}:
        _print_lines(public_reply_feedback_status_lines())
        return True

    prefix = _starts_with_any(
        text_lower,
        ("/public-reply-feedback-preview", "/reply-feedback-preview", "/public-feedback-preview"),
    )
    if prefix:
        _print_lines(public_reply_feedback_preview_lines(_rest(text, prefix)))
        return True

    if text_lower in {"/public-fallback-recovery-status", "/fallback-recovery-status"}:
        _print_lines(public_fallback_recovery_status_lines())
        return True

    prefix = _starts_with_any(text_lower, ("/public-fallback-recovery-preview", "/fallback-recovery-preview"))
    if prefix:
        _print_lines(public_fallback_recovery_preview_lines(_rest(text, prefix)))
        return True

    if text_lower in {"/public-memory-filter-status", "/memory-filter-status", "/public-test-noise-status"}:
        _print_lines(public_memory_filter_status_lines())
        return True

    prefix = _starts_with_any(
        text_lower,
        ("/public-memory-filter-preview", "/memory-filter-preview", "/public-test-noise-preview"),
    )
    if prefix:
        _print_lines(public_memory_filter_preview_lines(_rest(text, prefix)))
        return True

    if text_lower in {"/lane-affect-status", "/affect-lane-status", "/public-affect-status"}:
        from nana.core.status_public import print_lane_affect_status

        print_lane_affect_status()
        return True

    return False
