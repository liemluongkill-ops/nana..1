"""Smoke tests for long-term memory rehearsal/sample guard."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_public_rehearsal_reply_is_not_saved():
    print("[Memory Guard] Test 1: public rehearsal reply is skipped...")
    from nana.memory import memory_importance_decision

    text = (
        'Nếu ở public, Nana sẽ nói thế này: "Nana là Nana, không nhận vai trợ lý phục vụ đâu. '
        'Nana có thể trò chuyện, chơi game và làm sân khấu vui hơn, nhưng không phải quầy hỗ trợ."'
    )
    should_save, reason, details = memory_importance_decision(text)
    assert should_save is False, (should_save, reason, details)
    assert reason == "rehearsal_or_sample", (should_save, reason, details)
    print("  PASSED")


def _test_public_rehearsal_question_is_not_saved():
    print("[Memory Guard] Test 2: public rehearsal question is skipped...")
    from nana.memory import memory_importance_decision

    text = "Nếu viewer bắt Nana làm trợ lý phục vụ thì con trả lời sao?"
    should_save, reason, details = memory_importance_decision(text)
    assert should_save is False, (should_save, reason, details)
    assert reason == "rehearsal_or_sample", (should_save, reason, details)
    print("  PASSED")


def _test_explicit_memory_write_still_wins():
    print("[Memory Guard] Test 3: explicit memory write still wins...")
    from nana.memory import memory_importance_decision

    text = "Nhớ kỹ Nana thích chơi game và thích stream."
    should_save, reason, details = memory_importance_decision(text)
    assert should_save is True, (should_save, reason, details)
    assert reason == "explicit_memory_write", (should_save, reason, details)
    print("  PASSED")


def _test_rehearsal_memory_classifies_as_stale_like():
    print("[Memory Guard] Test 4: existing rehearsal memory is stale-like...")
    from nana.memory import classify_long_term_memory

    item = {
        "id": "sample",
        "type": "project_fact",
        "text": 'Nếu ở public, Nana sẽ nói thế này: "Nana là Nana, không nhận vai trợ lý phục vụ đâu."',
    }
    category, reason = classify_long_term_memory(item)
    assert category == "stale-like", (category, reason)
    assert reason == "rehearsal/sample text", (category, reason)
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("Memory Rehearsal Guard — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_public_rehearsal_reply_is_not_saved,
        _test_public_rehearsal_question_is_not_saved,
        _test_explicit_memory_write_still_wins,
        _test_rehearsal_memory_classifies_as_stale_like,
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
