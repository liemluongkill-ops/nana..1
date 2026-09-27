"""Session review, post-stream lessons, and audit command router."""

from __future__ import annotations

from collections.abc import Iterable

from nana.runtime.context_budget_audit import context_budget_audit_lines, context_budget_status_lines
from nana.runtime.lane_leak_audit import lane_leak_audit_lines, lane_leak_status_lines
from nana.runtime.memory_consolidation_preview import (
    memory_consolidation_preview_lines,
    memory_consolidation_status_lines,
)
from nana.runtime.post_stream_lessons import (
    approve_lines as post_stream_approve_lines,
    lesson_history_lines,
    lesson_stale_lines,
    lesson_status_lines,
    recommendation_lines as post_stream_recommendation_lines,
    reject_lines as post_stream_reject_lines,
    undo_lines as post_stream_undo_lines,
)
from nana.runtime.post_stream_review import post_stream_review_lines, post_stream_review_status_lines
from nana.runtime.session_review_adapter import (
    session_review_preview_lines,
    session_review_status_lines,
)


def _print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        print(line)


def _arg_after_space(text: str, default: str = "") -> str:
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else default


def handle_review_audit_command(text: str, text_lower: str | None = None) -> bool:
    """Handle STAGE-9C/9D/9E/9F review and audit command surfaces."""

    text_lower = text_lower or text.lower()

    if text_lower in {"/session-review-status"}:
        _print_lines(session_review_status_lines())
        return True

    if text_lower in {"/session-review-preview", "/post-stream-review-preview"}:
        _print_lines(session_review_preview_lines())
        return True

    if text_lower in {"/post-stream-review-status"}:
        _print_lines(post_stream_review_status_lines())
        return True

    if text_lower in {"/post-stream-review"}:
        _print_lines(post_stream_review_lines())
        return True

    if text_lower in {"/post-stream-lessons-status", "/lesson-status"}:
        _print_lines(lesson_status_lines())
        return True

    if text_lower in {"/post-stream-recommendations", "/post-stream-recs", "/lesson-recommendations"}:
        _print_lines(post_stream_recommendation_lines())
        return True

    if text_lower.startswith("/post-stream-approve ") or text_lower == "/post-stream-approve":
        _print_lines(post_stream_approve_lines(_arg_after_space(text)))
        return True

    if text_lower.startswith("/post-stream-reject ") or text_lower == "/post-stream-reject":
        _print_lines(post_stream_reject_lines(_arg_after_space(text)))
        return True

    if text_lower.startswith("/post-stream-undo ") or text_lower == "/post-stream-undo":
        _print_lines(post_stream_undo_lines(_arg_after_space(text)))
        return True

    if text_lower in {"/lesson-history", "/post-stream-lesson-history"}:
        _print_lines(lesson_history_lines())
        return True

    if text_lower in {"/lesson-stale", "/lesson-review-queue"}:
        _print_lines(lesson_stale_lines())
        return True

    if text_lower in {"/lane-leak-status"}:
        _print_lines(lane_leak_status_lines())
        return True

    if text_lower in {"/lane-leak-audit"}:
        _print_lines(lane_leak_audit_lines())
        return True

    if text_lower in {"/memory-consolidation-status", "/memory-audit-status"}:
        _print_lines(memory_consolidation_status_lines())
        return True

    if text_lower in {"/memory-consolidation-preview", "/memory-audit-preview"}:
        _print_lines(memory_consolidation_preview_lines())
        return True

    if text_lower.startswith("/context-budget-status"):
        _print_lines(context_budget_status_lines(_arg_after_space(text, "public_stage")))
        return True

    if text_lower.startswith("/context-budget-audit"):
        _print_lines(context_budget_audit_lines(_arg_after_space(text, "public_stage")))
        return True

    return False
