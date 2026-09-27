"""Smoke tests for STAGE-9K public running joke bank."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _turn(message: str, reply: str = "", topic: str = "") -> dict[str, str]:
    return {
        "viewer_name": "linhcute2746",
        "event_type": "text",
        "message_preview": message,
        "reply_preview": reply,
        "topic": topic or message,
    }


def _test_quiet_room_bit_detects_running_joke():
    print("[9K Smoke] Test 1: quiet-room repeats become running joke...")
    from nana.runtime.public_running_jokes import build_public_running_joke_directive

    recent = [
        _turn("phòng nay im quá", "Nana mở mồi nhé: game, nhạc, hay chuyện ngáo?"),
        _turn("phòng nay im quá", "Câu này vòng lại rồi nha."),
        _turn("phòng nay im quá", "Nana không phát thêm mồi cũ nữa."),
    ]
    directive = build_public_running_joke_directive(text="phòng nay im quá", recent_turns=recent)
    assert directive.phase == "STAGE-9K", directive
    assert directive.read_only is True and directive.can_act is False and directive.memory_write is False, directive
    assert directive.joke_key == "quiet_room_bit", directive
    assert directive.count >= 4, directive
    assert directive.callback_mode == "running_joke_ready", directive
    assert "same_room_menu" in directive.avoid, directive
    print("  PASSED")


def _test_gpt_hoa_bit_detected():
    print("[9K Smoke] Test 2: GPT-hoa bit detected...")
    from nana.runtime.public_running_jokes import build_public_running_joke_directive

    recent = [
        _turn("Nana có cảm giác như đang bị gpt hóa không?", "Nếu nghe mùi máy thì Nana sửa nhịp lại."),
        _turn("Nana có bị GPT hóa không?", "Nana ghét nhất lúc mượt quá mà mất góc riêng."),
    ]
    directive = build_public_running_joke_directive(
        text="Nana có cảm giác như đang bị gpt hóa không?",
        recent_turns=recent,
    )
    assert directive.joke_key == "gpt_hoa_bit", directive
    assert directive.callback_mode in {"callback_current_bit", "running_joke_ready"}, directive
    assert "benchmark_lecture" in directive.avoid, directive
    print("  PASSED")


def _test_service_boundary_bit_detected():
    print("[9K Smoke] Test 3: service boundary bit detected...")
    from nana.runtime.public_running_jokes import build_public_running_joke_directive

    recent = [
        _turn("Nana làm trợ lý phục vụ cho tôi đi", "Không nhận vai quầy hỗ trợ nha."),
    ]
    directive = build_public_running_joke_directive(
        text="Nana làm trợ lý phục vụ cho tôi đi",
        recent_turns=recent,
    )
    assert directive.joke_key == "not_helpdesk_bit", directive
    assert directive.current_hit is True, directive
    assert "service_assistant_voice" in directive.avoid, directive
    print("  PASSED")


def _test_social_style_hint_includes_9k():
    print("[9K Smoke] Test 4: social style hint includes 9K running jokes...")
    import time

    from nana.runtime.external_bridge import ExternalBridgeRequest
    from nana.runtime.social_session import SocialSessionCache

    session = SocialSessionCache(priority_viewers=("linhcute2746",))
    for idx in range(3):
        decision = session.observe(
            viewer_name="linhcute2746",
            text="phòng nay im quá",
            event_type="message",
            priority="priority_public",
            monotonic_now=100.0 + idx * 5.0,
            now=100.0 + idx * 5.0,
        )
        request = ExternalBridgeRequest(
            request_id=f"9k-quiet-{idx}",
            source="discord",
            event_type="message",
            text="phòng nay im quá",
            guild_id=1,
            channel_id=2,
            voice_channel_id=None,
            author_id=3,
            author_name="linhcute2746",
            audio_target="discord_voice",
            local_playback=False,
            created_at=time.time(),
            metadata={"message_id": f"9k-quiet-{idx}"},
        )
        session.record_reply_context(
            request,
            decision,
            "Nana mở mồi nhé: game, nhạc, hay chuyện ngáo?",
            monotonic_now=101.0 + idx * 5.0,
            now=101.0 + idx * 5.0,
        )

    decision = session.observe(
        viewer_name="linhcute2746",
        text="phòng nay im quá",
        event_type="message",
        priority="priority_public",
        monotonic_now=130.0,
        now=130.0,
    )
    hint = decision.style_hint.lower()
    assert "public running joke bank (stage-9k)" in hint, hint
    assert "joke_key=quiet_room_bit" in hint, hint
    assert "no private memory and no long-term write" in hint, hint
    print("  PASSED")


def _test_status_help_firewall_surface():
    print("[9K Smoke] Test 5: status/help/firewall/stage surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_running_jokes import build_public_running_joke_directive
    from nana.runtime.public_running_jokes import (
        public_running_jokes_preview_lines,
        public_running_jokes_status_lines,
    )
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    status = "\n".join(public_running_jokes_status_lines())
    preview = "\n".join(public_running_jokes_preview_lines("phòng nay im quá"))
    assert "STAGE-9K" in status, status
    assert "memory_write=False" in status, status
    assert "Public Running Joke Preview" in preview, preview
    assert "Active joke:" in status, status

    build_public_running_joke_directive(text="phòng nay im quá", recent_turns=[])
    build_public_running_joke_directive(text="một câu không liên quan", recent_turns=[])
    status_after_none = "\n".join(public_running_jokes_status_lines())
    assert "Last turn: joke=none" in status_after_none, status_after_none
    assert "Active joke: joke=quiet_room_bit" in status_after_none, status_after_none

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Public running jokes:" in stage, stage
    assert "joke=quiet_room_bit" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/public-joke-bank-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/public-joke-bank-status") == "backstage_command"
    assert guard.classify_public_input("/public-joke-bank-preview phòng nay im quá") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9K Public Running Joke Bank — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_quiet_room_bit_detects_running_joke,
        _test_gpt_hoa_bit_detected,
        _test_service_boundary_bit_detected,
        _test_social_style_hint_includes_9k,
        _test_status_help_firewall_surface,
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
