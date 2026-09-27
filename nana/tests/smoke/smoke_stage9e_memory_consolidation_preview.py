"""STAGE-9E smoke tests: memory consolidation preview.

Preview-only. It must not create pending actions or write memory.
"""

from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@contextmanager
def _isolated_memory():
    memory_mod = importlib.import_module("nana.memory")
    preview_mod = importlib.import_module("nana.runtime.memory_consolidation_preview")
    original_memory = memory_mod.memory
    fake_memory = memory_mod._merge_defaults({
        "long_term": [
            "Ba thích osu và Stardew.",
            "Ba thích osu và Stardew.",
            "Hôm nay Nana đang xem Discord.",
            "haha tà đạo gạt tàn.",
            "Một dòng chưa rõ có nên giữ không.",
        ],
        "post_stream_lessons": {
            "lessons": [
                {
                    "lesson_id": "lesson-active",
                    "type": "reply_density",
                    "insight": "Keep replies short.",
                    "applied_at": 1.0,
                    "review_after": 9999999999.0,
                    "revoked": False,
                },
                {
                    "lesson_id": "lesson-stale",
                    "type": "avatar_reaction",
                    "insight": "Old lesson needs review.",
                    "applied_at": 1.0,
                    "review_after": 2.0,
                    "revoked": False,
                },
                {
                    "lesson_id": "lesson-revoked",
                    "type": "starter",
                    "insight": "Revoked.",
                    "applied_at": 1.0,
                    "review_after": 2.0,
                    "revoked": True,
                },
            ]
        },
    })
    try:
        memory_mod.memory = fake_memory
        preview_mod.memory = fake_memory
        yield fake_memory
    finally:
        preview_mod.memory = original_memory
        memory_mod.memory = original_memory


def _test_preview_detects_duplicates_and_categories():
    print("[9E Smoke] Test 1: preview detects duplicates and categories...")
    with _isolated_memory():
        from nana.runtime.memory_consolidation_preview import build_memory_consolidation_report

        report = build_memory_consolidation_report()
        assert report.long_count == 5, report
        assert report.duplicate_groups, report
        assert report.categories.get("keep", 0) >= 1, report.categories
        assert report.categories.get("stale-like", 0) >= 1, report.categories
        assert report.categories.get("vibe-heavy", 0) >= 1, report.categories
        print("  PASSED")


def _test_lesson_stale_and_read_only_flags():
    print("[9E Smoke] Test 2: lessons stale state and read-only flags...")
    with _isolated_memory():
        from nana.runtime.memory_consolidation_preview import build_memory_consolidation_report

        report = build_memory_consolidation_report()
        assert report.read_only is True, report
        assert report.can_act is False, report
        assert report.memory_write is False, report
        assert report.lesson_counts["active"] == 1, report.lesson_counts
        assert report.lesson_counts["stale"] == 1, report.lesson_counts
        assert report.lesson_counts["revoked"] == 1, report.lesson_counts
        print("  PASSED")


def _test_lines_and_firewall_surface():
    print("[9E Smoke] Test 3: status/preview/firewall surface exists...")
    with _isolated_memory():
        from nana.runtime.memory_consolidation_preview import (
            memory_consolidation_preview_lines,
            memory_consolidation_status_lines,
        )
        from nana.runtime.public_stage_identity import get_public_stage_identity_guard

        status = "\n".join(memory_consolidation_status_lines())
        preview = "\n".join(memory_consolidation_preview_lines())
        assert "STAGE-9E" in status, status
        assert "preview-only" in status, status
        assert "Duplicate groups:" in preview, preview
        assert "Review candidates:" in preview, preview
        assert get_public_stage_identity_guard().classify_public_input("/memory-consolidation-preview") == "backstage_command"
        print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9E Memory Consolidation Preview — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_preview_detects_duplicates_and_categories,
        _test_lesson_stale_and_read_only_flags,
        _test_lines_and_firewall_surface,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"  FAILED: {type(exc).__name__}: {exc}")
    passed = len(tests) - failed
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_all())
