"""Tests for CORE-PERSONA-SPINE-1.

Smoke tests:
1. generate_spine_block() trả về block có đủ 5 trường
2. smoke_test() bắt được service_bot phrases
3. smoke_test() bắt được too_safe patterns
4. smoke_test() pass với text có personality
5. Lane labels khác nhau đúng cách
"""

import sys
from pathlib import Path

# Ensure nana is importable
nana_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(nana_root.parent))

from nana.runtime.persona_spine import (
    generate_spine_block,
    smoke_test,
    get_spine,
    PersonaSpine,
    NANA_CORE_IDENTITY,
    NANA_CORE_VALUES,
    NANA_CORE_BOUNDARIES,
    NANA_CORE_MOTIVATION,
    NANA_CORE_VOICE,
    SERVICE_BOT_PATTERNS,
    TOO_SAFE_PATTERNS,
)


def test_spine_block_has_all_fields():
    """Spine block phải có đủ 5 trường core."""
    block = generate_spine_block("public_stage")
    assert "Identity" in block
    assert "Values" in block
    assert "Boundaries" in block
    assert "Motivation" in block
    assert "Voice" in block
    print("✓ test_spine_block_has_all_fields PASSED")


def test_core_content_not_empty():
    """Core conviction content không được rỗng."""
    assert NANA_CORE_IDENTITY.strip()
    assert NANA_CORE_VALUES.strip()
    assert NANA_CORE_BOUNDARIES.strip()
    assert NANA_CORE_MOTIVATION.strip()
    assert NANA_CORE_VOICE.strip()
    print("✓ test_core_content_not_empty PASSED")


def test_smoke_catches_service_bot():
    """smoke_test() phải bắt được service bot phrases."""
    test_cases = [
        "Tôi có thể giúp gì cho bạn hôm nay?",
        "Mình có thể hỗ trợ gì cho bạn không?",
        "Rất vui được hỗ trợ bạn!",
        "Cảm ơn bạn đã hỏi, dưới đây là câu trả lời.",
        "Với tư cách là một AI, tôi xin chia sẻ...",
    ]
    for text in test_cases:
        result = smoke_test(text)
        has_service_bot = any(v.kind == "service_bot" for v in result.violations)
        assert has_service_bot, f"Failed to catch service bot in: {text}"


def test_smoke_catches_too_safe():
    """smoke_test() phải bắt được too safe phrases."""
    test_cases = [
        "Nana có thể hiểu được điều đó.",
        "Tùy thuộc vào ngữ cảnh mà câu trả lời khác nhau.",
        "Nhìn chung thì điều này có thể đúng.",
    ]
    for text in test_cases:
        result = smoke_test(text)
        has_too_safe = any(v.kind == "too_safe" for v in result.violations)
        assert has_too_safe, f"Failed to catch too_safe in: {text}"
    print("✓ test_smoke_catches_too_safe PASSED")


def test_smoke_passes_with_personality():
    """smoke_test() phải pass với text có personality thật."""
    test_cases = [
        "Nana thấy cái này hơi vui, để Nana giải thích nha.",
        "Ủa sao lại hỏi vậy, Nana không hiểu lắm.",
        "Chắc là được, nhưng Nana không chắc lắm.",
        "Mình thích cách này hơn, nghe tự nhiên hơn.",
    ]
    for text in test_cases:
        result = smoke_test(text)
        has_critical = any(v.severity == "critical" for v in result.violations)
        assert not has_critical, f"False positive (critical) in: {text}"
    print("✓ test_smoke_passes_with_personality PASSED")


def test_lane_labels_different():
    """Lane labels phải khác nhau giữa private/public/operator."""
    private_block = generate_spine_block("private_owner")
    public_block = generate_spine_block("public_stage")
    public_alias_block = generate_spine_block("public_viewer")
    operator_block = generate_spine_block("operator_backstage")
    operator_alias_block = generate_spine_block("operator")

    # Mỗi block phải có lane marker riêng
    assert "Với Ba:" in private_block
    assert "Public stage:" in public_block
    assert "Operator backstage:" in operator_block
    assert "Lane: public_stage" in public_alias_block
    assert "Lane: operator_backstage" in operator_alias_block

    # Public label không được kéo viewer về quan hệ Ba/con.
    public_label = public_block.split("Identity:", 1)[0]
    assert "Với Ba" not in public_label
    assert "con/Ba" not in public_label

    print("✓ test_lane_labels_different PASSED")


def test_public_spine_does_not_center_ba_world():
    """Core identity public không được nói viewer đang ở 'thế giới của Ba'."""
    block = generate_spine_block("public_stage")
    assert "thế giới của Ba" not in block
    assert "sân khấu Nana" in block
    print("✓ test_public_spine_does_not_center_ba_world PASSED")


def test_singleton_integrity():
    """get_spine() phải trả về cùng instance."""
    spine1 = get_spine()
    spine2 = get_spine()
    assert spine1 is spine2
    print("✓ test_singleton_integrity PASSED")


def test_status_lines():
    """status_lines() phải trả về list strings có format đúng."""
    lines = get_spine().status_lines()
    assert isinstance(lines, list)
    assert all(isinstance(l, str) for l in lines)
    assert any("Persona Spine" in l for l in lines)
    print("✓ test_status_lines PASSED")


def run_all_tests():
    print("=" * 50)
    print("Running CORE-PERSONA-SPINE-1 tests...")
    print("=" * 50)

    tests = [
        test_core_content_not_empty,
        test_spine_block_has_all_fields,
        test_smoke_catches_service_bot,
        test_smoke_catches_too_safe,
        test_smoke_passes_with_personality,
        test_lane_labels_different,
        test_public_spine_does_not_center_ba_world,
        test_singleton_integrity,
        test_status_lines,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"✗ {test.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__} ERROR: {e}")
            failed += 1

    print("=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 50)
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
