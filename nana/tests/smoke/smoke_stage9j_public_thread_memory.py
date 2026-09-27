"""Smoke tests for STAGE-9J public thread memory-lite."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _quiet_turn(reply: str = "Nana mở mồi nhé: game, nhạc, hay chuyện ngáo?") -> dict[str, str]:
    return {
        "viewer_name": "linhcute2746",
        "event_type": "text",
        "message_preview": "phòng nay im quá",
        "reply_preview": reply,
        "topic": "phòng nay im quá",
    }


def _test_repeat_prompt_builds_awareness():
    print("[9J Smoke] Test 1: third repeat builds repeat awareness...")
    from nana.runtime.public_thread_memory import build_public_thread_memory_directive

    recent = [
        _quiet_turn("Phòng hơi lặng rồi đó. Nana rải một mồi nhỏ."),
        _quiet_turn("Câu này vòng lại rồi nha. Nana đổi mồi."),
    ]
    directive = build_public_thread_memory_directive(
        text="phòng nay im quá",
        viewer_name="linhcute2746",
        recent_turns=recent,
    )
    assert directive.phase == "STAGE-9J", directive
    assert directive.read_only is True and directive.can_act is False and directive.memory_write is False, directive
    assert directive.same_prompt_count == 3, directive
    assert directive.response_memory == "repeat_awareness", directive
    assert "same_answer_again" in directive.avoid, directive
    print("  PASSED")


def _test_repeat_fatigue_after_many_repeats():
    print("[9J Smoke] Test 2: many repeats build fatigue directive...")
    from nana.runtime.public_thread_memory import build_public_thread_memory_directive

    recent = [_quiet_turn(f"variant {idx}") for idx in range(6)]
    directive = build_public_thread_memory_directive(
        text="!nana phòng nay im quá",
        viewer_name="linhcute2746",
        recent_turns=recent,
    )
    assert directive.same_prompt_count == 7, directive
    assert directive.response_memory == "repeat_fatigue", directive
    assert "pretend_prompt_is_new" in directive.avoid, directive
    print("  PASSED")


def _test_topic_continuation_uses_recent_public_thread():
    print("[9J Smoke] Test 3: selected story continues public thread...")
    from nana.runtime.public_thread_memory import build_public_thread_memory_directive

    recent = [
        _quiet_turn("Nana mở mồi nhé: game đang cày, bài nhạc cứu mood, hay chuyện ngáo hôm nay?")
    ]
    directive = build_public_thread_memory_directive(
        text="chuyện ngáo",
        viewer_name="linhcute2746",
        recent_turns=recent,
    )
    assert directive.response_memory == "continue_public_thread", directive
    assert directive.active_thread != "none", directive
    assert "restart_thread" in directive.avoid, directive
    print("  PASSED")


def _test_social_style_hint_includes_9j_repeat_memory():
    print("[9J Smoke] Test 4: social style hint includes 9J repeat memory...")
    import time

    from nana.runtime.external_bridge import ExternalBridgeRequest
    from nana.runtime.social_session import SocialSessionCache

    session = SocialSessionCache(priority_viewers=("linhcute2746",))
    for idx in range(2):
        decision = session.observe(
            viewer_name="linhcute2746",
            text="phòng nay im quá",
            event_type="message",
            priority="priority_public",
            monotonic_now=100.0 + idx * 10.0,
            now=100.0 + idx * 10.0,
        )
        request = ExternalBridgeRequest(
            request_id=f"9j-repeat-{idx}",
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
            metadata={"message_id": f"9j-repeat-{idx}"},
        )
        session.record_reply_context(
            request,
            decision,
            "Nana mở mồi nhé: game, nhạc, hay chuyện ngáo?",
            monotonic_now=101.0 + idx * 10.0,
            now=101.0 + idx * 10.0,
        )

    third = session.observe(
        viewer_name="linhcute2746",
        text="phòng nay im quá",
        event_type="message",
        priority="priority_public",
        monotonic_now=130.0,
        now=130.0,
    )
    hint = third.style_hint.lower()
    assert "public thread memory-lite (stage-9j)" in hint, hint
    assert "response_memory=repeat_awareness" in hint, hint
    assert "same_prompt_count=3" in hint, hint
    assert "no private memory and no long-term write" in hint, hint
    print("  PASSED")


def _test_status_help_firewall_surface():
    print("[9J Smoke] Test 5: status/help/firewall surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard
    from nana.runtime.public_thread_memory import (
        public_thread_memory_preview_lines,
        public_thread_memory_status_lines,
    )

    status = "\n".join(public_thread_memory_status_lines())
    preview = "\n".join(public_thread_memory_preview_lines("phòng nay im quá"))
    assert "STAGE-9J" in status, status
    assert "memory_write=False" in status, status
    assert "Public Thread Memory Preview" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Public thread memory:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/public-thread-memory-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/public-thread-memory-status") == "backstage_command"
    assert guard.classify_public_input("/public-thread-memory-preview phòng nay im quá") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9J Public Thread Memory-lite — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_repeat_prompt_builds_awareness,
        _test_repeat_fatigue_after_many_repeats,
        _test_topic_continuation_uses_recent_public_thread,
        _test_social_style_hint_includes_9j_repeat_memory,
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
