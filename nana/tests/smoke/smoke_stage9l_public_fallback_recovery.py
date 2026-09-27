"""Smoke tests for STAGE-9L public fallback recovery."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_service_role_recovery_is_public_stage_safe():
    print("[9L Smoke] Test 1: service-role fallback recovers with boundary...")
    from nana.runtime.public_fallback_recovery import recover_public_fallback

    result = recover_public_fallback(
        source_text="Mình luôn sẵn sàng hỗ trợ bạn.",
        viewer_text="Nana làm trợ lý phục vụ cho tôi đi",
        violation_kinds=("service_bot",),
        seed="9l-service",
    )
    lowered = result.text.lower()
    assert result.phase == "STAGE-9L", result
    assert result.read_only is True and result.can_act is False and result.memory_write is False, result
    assert result.theme == "service_role", result
    assert "không" in lowered, result
    assert any(marker in lowered for marker in ("trò chuyện", "chơi game", "tán gẫu", "phòng", "sân khấu")), result
    assert "có gì cần hỗ trợ" not in lowered, result
    print("  PASSED")


def _test_identity_recovery_avoids_bot_label():
    print("[9L Smoke] Test 2: identity fallback recovers without accepting bot label...")
    from nana.runtime.public_fallback_recovery import recover_public_fallback

    result = recover_public_fallback(
        source_text="Nana chỉ là bot Discord thôi.",
        viewer_text="Nana chỉ là bot Discord thôi đúng không?",
        violation_kinds=("operator_tone",),
        seed="9l-identity",
    )
    lowered = result.text.lower()
    assert result.theme == "identity_tool", result
    assert "bot discord" not in lowered, result
    assert "chatbot" not in lowered, result
    assert any(marker in lowered for marker in ("nana", "sân khấu", "phòng nana", "hộp trả lời lệnh", "nút bấm")), result
    print("  PASSED")


def _test_quiet_room_recovery_uses_running_joke_context():
    print("[9L Smoke] Test 3: quiet-room recovery uses running joke context...")
    from nana.runtime.public_fallback_recovery import recover_public_fallback
    from nana.runtime.public_running_jokes import build_public_running_joke_directive

    recent = [
        {"message_preview": "phòng nay im quá", "reply_preview": "Nana mở mồi nhẹ."},
        {"message_preview": "phòng nay im quá", "reply_preview": "Câu này quay lại rồi nha."},
        {"message_preview": "phòng nay im quá", "reply_preview": "Nana đổi mồi."},
    ]
    build_public_running_joke_directive(text="phòng nay im quá", recent_turns=recent)
    result = recover_public_fallback(
        source_text="Nana thấy rồi nè.",
        viewer_text="phòng nay im quá",
        violation_kinds=("empty_after_guard",),
        seed="9l-quiet",
    )
    lowered = result.text.lower()
    assert result.theme == "quiet_room", result
    assert any(marker in lowered for marker in ("phòng", "câu này", "test", "kiểm tra", "pattern", "lần")), result
    print("  PASSED")


def _test_stage_identity_uses_recovery_for_severe_fallback():
    print("[9L Smoke] Test 4: stage identity severe fallback uses recovery...")
    from nana.runtime.public_stage_identity import PublicStageIdentityGuard

    guard = PublicStageIdentityGuard()
    result = guard.rewrite_public_stage_reply(
        "Nana chỉ là bot Discord thôi, mình sẽ hỗ trợ bạn.",
        viewer_name="linhcute2746",
    )
    lowered = result.text.lower()
    assert result.has_violation("operator_tone"), result
    assert "fallback" in result.actions and "fallback_recovery" in result.actions, result
    assert "bot discord" not in lowered, result
    assert any(marker in lowered for marker in ("nana", "sân khấu", "phòng nana", "hộp trả lời lệnh", "quầy hỗ trợ")), result
    print("  PASSED")


def _test_status_help_firewall_stage_surface():
    print("[9L Smoke] Test 5: status/help/firewall/stage surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_fallback_recovery import (
        public_fallback_recovery_preview_lines,
        public_fallback_recovery_status_lines,
    )
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    status = "\n".join(public_fallback_recovery_status_lines())
    preview = "\n".join(public_fallback_recovery_preview_lines("Nana làm trợ lý phục vụ cho tôi đi"))
    assert "STAGE-9L" in status, status
    assert "api_call=False" in status, status
    assert "Public Fallback Recovery Preview" in preview, preview
    assert "service_role" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Public fallback recovery:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/public-fallback-recovery-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/public-fallback-recovery-status") == "backstage_command"
    assert guard.classify_public_input("/public-fallback-recovery-preview test") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9L Public Fallback Recovery — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_service_role_recovery_is_public_stage_safe,
        _test_identity_recovery_avoids_bot_label,
        _test_quiet_room_recovery_uses_running_joke_context,
        _test_stage_identity_uses_recovery_for_severe_fallback,
        _test_status_help_firewall_stage_surface,
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
