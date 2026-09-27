"""STAGE-9E: memory consolidation preview.

Read-only memory health/consolidation analysis. This module never creates a
pending memory action and never deletes or rewrites memory.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from nana.memory import classify_long_term_memory, memory, memory_item_key, memory_lock, trim_memory_line


PHASE = "STAGE-9E"


@dataclass(frozen=True)
class MemoryConsolidationReport:
    long_count: int
    long_limit: int
    duplicate_groups: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    categories: dict[str, int] = field(default_factory=dict)
    review_candidates: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    lesson_counts: dict[str, int] = field(default_factory=dict)
    stale_lessons: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": PHASE,
            "long_count": self.long_count,
            "long_limit": self.long_limit,
            "duplicate_groups": list(self.duplicate_groups),
            "categories": dict(self.categories),
            "review_candidates": list(self.review_candidates),
            "lesson_counts": dict(self.lesson_counts),
            "stale_lessons": list(self.stale_lessons),
            "read_only": self.read_only,
            "can_act": self.can_act,
            "memory_write": self.memory_write,
        }


def _lesson_state(lesson: dict[str, Any], now: float) -> str:
    if lesson.get("revoked"):
        return "revoked"
    try:
        review_after = float(lesson.get("review_after") or 0.0)
    except (TypeError, ValueError):
        review_after = 0.0
    if review_after and review_after <= now:
        return "stale"
    return "active"


def build_memory_consolidation_report(limit: int = 8) -> MemoryConsolidationReport:
    with memory_lock:
        long_term = list(memory.get("long_term") or [])
        lessons_root = dict(memory.get("post_stream_lessons") or {})
        lessons = [dict(item) for item in lessons_root.get("lessons", []) if isinstance(item, dict)]

    key_to_items: dict[str, list[tuple[int, str]]] = {}
    categories: dict[str, int] = {}
    review_candidates: list[dict[str, Any]] = []

    for index, item in enumerate(long_term, 1):
        text = str(item or "")
        key_to_items.setdefault(memory_item_key(text), []).append((index, text))
        category, reason = classify_long_term_memory(text)
        categories[category] = categories.get(category, 0) + 1
        if category in {"review", "stale-like", "vibe-heavy"} and len(review_candidates) < limit:
            review_candidates.append({
                "index": index,
                "category": category,
                "reason": reason,
                "text": trim_memory_line(text, 100),
            })

    duplicate_groups: list[dict[str, Any]] = []
    for key, items in key_to_items.items():
        if len(items) <= 1:
            continue
        duplicate_groups.append({
            "key": key[:10],
            "count": len(items),
            "indexes": [idx for idx, _ in items[:8]],
            "sample": trim_memory_line(items[0][1], 100),
        })
    duplicate_groups.sort(key=lambda item: (-int(item["count"]), item["key"]))

    now = time.time()
    lesson_counts = {"active": 0, "stale": 0, "revoked": 0}
    stale_lessons: list[dict[str, Any]] = []
    for lesson in lessons:
        state = _lesson_state(lesson, now)
        lesson_counts[state] = lesson_counts.get(state, 0) + 1
        if state == "stale" and len(stale_lessons) < limit:
            stale_lessons.append({
                "lesson_id": lesson.get("lesson_id"),
                "type": lesson.get("type"),
                "review_after": lesson.get("review_after"),
                "insight": trim_memory_line(lesson.get("insight"), 100),
            })

    return MemoryConsolidationReport(
        long_count=len(long_term),
        long_limit=50,
        duplicate_groups=tuple(duplicate_groups[:limit]),
        categories=categories,
        review_candidates=tuple(review_candidates),
        lesson_counts=lesson_counts,
        stale_lessons=tuple(stale_lessons),
    )


def _category_summary(categories: dict[str, int]) -> str:
    if not categories:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(categories.items()))


def memory_consolidation_status_lines() -> list[str]:
    report = build_memory_consolidation_report()
    return [
        "🧹 Memory Consolidation Preview (STAGE-9E)",
        f"  Mode: preview-only | read_only={report.read_only} | can_act={report.can_act} | memory_write={report.memory_write}",
        f"  Long-term: {report.long_count}/{report.long_limit} | categories[{_category_summary(report.categories)}]",
        f"  Duplicates: groups={len(report.duplicate_groups)} | review_candidates={len(report.review_candidates)}",
        "  Lessons: "
        f"active={report.lesson_counts.get('active', 0)} | stale={report.lesson_counts.get('stale', 0)} | "
        f"revoked={report.lesson_counts.get('revoked', 0)}",
        "  Commands: /memory-consolidation-status | /memory-consolidation-preview",
        "  Safety: no delete | no compact | no pending action | no config write",
    ]


def memory_consolidation_preview_lines() -> list[str]:
    report = build_memory_consolidation_report()
    lines = memory_consolidation_status_lines()
    lines.append("  Duplicate groups:")
    if report.duplicate_groups:
        for group in report.duplicate_groups:
            lines.append(f"    - count={group['count']} | indexes={group['indexes']} | {group['sample']}")
    else:
        lines.append("    - none")
    lines.append("  Review candidates:")
    if report.review_candidates:
        for item in report.review_candidates:
            lines.append(f"    - #{item['index']} [{item['category']}] {item['reason']} | {item['text']}")
    else:
        lines.append("    - none")
    if report.stale_lessons:
        lines.append("  Stale lessons:")
        for lesson in report.stale_lessons:
            lines.append(f"    - {lesson['lesson_id']} | {lesson['type']} | {lesson['insight']}")
    lines.append("  Next: owner can choose prune/keep later; this command only reports.")
    return lines


def memory_consolidation_snapshot() -> dict[str, Any]:
    return build_memory_consolidation_report().to_dict()
