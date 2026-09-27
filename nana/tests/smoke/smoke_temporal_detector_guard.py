"""Smoke tests for temporal/browser detector guardrails."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_model_opinion_question_is_not_temporal_or_browser():
    print("[Temporal Guard] Test 1: model opinion question is not temporal/browser...")
    import nana.brain.gpt as gpt

    text = "Ba vừa thấy gpt 5.6 vừa ra đấy con có thích dùng loại đấy không?"
    assert gpt.is_temporal_question(text) is False
    assert gpt.is_browser_question(text) is False
    print("  PASSED")


def _test_real_temporal_questions_still_match():
    print("[Temporal Guard] Test 2: real temporal questions still match...")
    import nana.brain.gpt as gpt

    positives = [
        "Nãy giờ Ba đang xem gì vậy?",
        "Lúc nãy mình nói về cái gì ấy nhỉ?",
        "Ba vừa làm gì?",
    ]
    for text in positives:
        assert gpt.is_temporal_question(text) is True, text
    print("  PASSED")


def _test_prompt_response_label_is_stripped():
    print("[Temporal Guard] Test 3: prompt response labels are stripped...")
    import nana.brain.gpt as gpt

    assert gpt.strip_prompt_response_label("Casual response: Ừa, cái đó ổn đó Ba.") == "Ừa, cái đó ổn đó Ba."
    assert gpt.finalize_reply("**Casual response:** Ừ, nội dung đó vẫn còn nằm trên máy nè.", casual_mode=False) == "Ừ, nội dung đó vẫn còn nằm trên máy nè."
    print("  PASSED")


def _test_gpt_style_artifacts_are_cleaned():
    print("[Temporal Guard] Test 4: GPT-style artifacts are cleaned...")
    import nana.brain.gpt as gpt

    raw = (
        "Có chứ Ba. Nếu 5. 6 thật sự hiểu ngữ cảnh tốt hơn thì con thích đấy. "
        "Nói gọn là: con thích nếu nó làm con khôn hơn thật, chứ không phải chỉ mới theo kiểu quảng cáo."
    )
    cleaned = gpt.finalize_reply(raw, casual_mode=False)
    assert "5.6" in cleaned
    assert "5. 6" not in cleaned
    assert "Nói gọn là" not in cleaned
    assert "kiểu treo bảng quảng cáo" in cleaned
    print("  PASSED")


def _test_streaming_cleaner_keeps_version_numbers_intact():
    print("[Temporal Guard] Test 5: streaming cleaner keeps version numbers intact...")
    import nana.brain.gpt as gpt

    raw = (
        "Có Ba. Nếu 5. 6 thật sự bám ngữ cảnh tốt hơn thì con thích đấy. "
        "Nói gọn là con thích cái nào làm con thông minh hơn."
    )
    cleaner = gpt.StreamingReplySurfaceSanitizer()
    streamed = "".join(cleaner.feed(ch) for ch in raw) + cleaner.flush()
    assert "5.6" in streamed
    assert "5. 6" not in streamed
    assert "Nói gọn là" not in streamed
    print("  PASSED")


def _test_standalone_summary_fragment_is_cleaned():
    print("[Temporal Guard] Test 6: standalone summary fragments are cleaned...")
    import nana.brain.gpt as gpt

    fragment = "   Nói gọn là con thích hiệu quả thật, không thích chỉ lấy mác xịn."
    cleaned = gpt.normalize_model_artifacts(fragment)
    assert "Nói gọn là" not in cleaned
    assert cleaned.startswith("Con thích hiệu quả thật"), cleaned
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("Temporal Detector Guard — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_model_opinion_question_is_not_temporal_or_browser,
        _test_real_temporal_questions_still_match,
        _test_prompt_response_label_is_stripped,
        _test_gpt_style_artifacts_are_cleaned,
        _test_streaming_cleaner_keeps_version_numbers_intact,
        _test_standalone_summary_fragment_is_cleaned,
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
