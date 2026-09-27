"""Smoke tests for STAGE-9S public quiet-room rhythm."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _assert_no_old_menu(text: str) -> None:
    lowered = text.lower()
    assert "game đang cày" not in lowered, text
    assert "bài nhạc" not in lowered, text
    assert "chọn nhanh" not in lowered, text
    assert "mở mồi" not in lowered, text
    assert "rải một mồi" not in lowered, text


def _test_first_quiet_room_uses_scene_hook():
    print("[9S Smoke] Test 1: first quiet-room turn uses scene + one hook...")
    from nana.runtime.public_quiet_room_rhythm import build_public_quiet_room_rhythm_directive

    directive = build_public_quiet_room_rhythm_directive(
        text="phòng nay im quá",
        repeat_count=1,
        seed="9s-first",
    )
    assert directive.phase == "STAGE-9S", directive
    assert directive.read_only is True and directive.can_act is False, directive
    assert directive.memory_write is False and directive.api_call is False, directive
    assert directive.mode == "first_scene", directive
    assert directive.text, directive
    _assert_no_old_menu(directive.text)
    lowered = directive.text.lower()
    assert any(marker in lowered for marker in ("phòng", "nana", "yên", "lặng", "im")), directive
    assert any(marker in lowered for marker in ("khoảnh khắc", "chi tiết", "mảnh", "mẩu", "chuyện")), directive
    print("  PASSED")


def _test_second_quiet_room_uses_fresh_scene():
    print("[9S Smoke] Test 2: second quiet-room turn uses fresh scene...")
    from nana.runtime.public_quiet_room_rhythm import build_public_quiet_room_rhythm_directive

    directive = build_public_quiet_room_rhythm_directive(
        text="!nana phòng nay im quá",
        repeat_count=2,
        seed="9s-second",
    )
    assert directive.mode == "fresh_scene", directive
    assert directive.confidence >= 0.7, directive
    _assert_no_old_menu(directive.text)
    assert "same_choice_menu" in directive.avoid, directive
    print("  PASSED")


def _test_repeat_defers_to_repeat_guard():
    print("[9S Smoke] Test 3: repeated prompt defers to repeat guard...")
    from nana.runtime.public_quiet_room_rhythm import build_public_quiet_room_rhythm_directive

    directive = build_public_quiet_room_rhythm_directive(text="phòng nay im quá", repeat_count=3)
    assert directive.mode == "repeat_defer", directive
    assert directive.text == "", directive
    assert "pretend_first_time" in directive.avoid, directive
    print("  PASSED")


def _test_external_bridge_short_quiet_room_uses_9s_before_repeat():
    print("[9S Smoke] Test 4: external bridge uses 9S before repeat guard...")
    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.viewer_chat import ViewerChatQueue

    runtime = ExternalBridgeRuntime(
        enabled=True,
        queue=ViewerChatQueue(duplicate_window_seconds=-1.0, rate_limit_max=99),
        social_session=SocialSessionCache(priority_viewers=("linhcute2746",)),
    )
    replies: list[str] = []
    for idx in range(3):
        request = ExternalBridgeRequest.from_payload(
            {
                "request_id": f"9s-quiet-{idx}",
                "source": "discord",
                "event_type": "message",
                "text": "phòng nay im quá",
                "author_name": "linhcute2746",
                "metadata": {
                    "message_id": f"9s-quiet-{idx}",
                    "route": {"chat_channel_name": "chung"},
                },
            }
        )
        reply = runtime.process_request(request)
        assert reply["metadata"]["social_session"]["action"] == "full_reply", reply
        replies.append(reply["reply_text"])

    _assert_no_old_menu(replies[0])
    _assert_no_old_menu(replies[1])
    third = replies[2].lower()
    assert any(marker in third for marker in ("lần", "test", "kiểm tra", "pattern", "câu này", "hỏi tới")), replies
    print("  PASSED")


def _test_status_help_firewall_stage_surface():
    print("[9S Smoke] Test 5: status/help/firewall/stage surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_quiet_room_rhythm import (
        public_quiet_room_preview_lines,
        public_quiet_room_status_lines,
    )
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    status = "\n".join(public_quiet_room_status_lines())
    preview = "\n".join(public_quiet_room_preview_lines("phòng nay im quá"))
    assert "STAGE-9S" in status, status
    assert "memory_write=False" in status, status
    assert "api_call=False" in status, status
    assert "Public Quiet-Room Preview" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Public quiet-room rhythm:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/public-quiet-room-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/public-quiet-room-status") == "backstage_command"
    assert guard.classify_public_input("/public-quiet-room-preview phòng nay im quá") == "backstage_command"
    assert guard.classify_public_input("/room-rhythm-status") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9S Public Quiet-Room Rhythm — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_first_quiet_room_uses_scene_hook,
        _test_second_quiet_room_uses_fresh_scene,
        _test_repeat_defers_to_repeat_guard,
        _test_external_bridge_short_quiet_room_uses_9s_before_repeat,
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
