"""Smoke tests for STAGE-9I public conversation director."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_quiet_room_builds_warmup_directive():
    print("[9I Smoke] Test 1: quiet-room report builds warmup directive...")
    from nana.runtime.public_conversation_director import build_public_conversation_directive

    directive = build_public_conversation_directive(text="phòng nay im quá", room_vibe="quiet_room")
    assert directive.thread == "room_warmup", directive
    assert directive.viewer_intent == "quiet_room_report", directive
    assert directive.response_move == "offer_concrete_choices", directive
    assert directive.read_only is True and directive.can_act is False, directive
    print("  PASSED")


def _test_selected_story_continues_existing_thread():
    print("[9I Smoke] Test 2: viewer-selected story continues current thread...")
    from nana.runtime.public_conversation_director import build_public_conversation_directive

    recent = [
        {
            "message_preview": "phòng nay im quá",
            "reply_preview": "Nana mở mồi nhé: game đang cày, bài nhạc cứu mood, hay chuyện ngáo hôm nay?",
        }
    ]
    directive = build_public_conversation_directive(text="chuyện ngáo", recent_turns=recent)
    assert directive.thread == "selected_story_thread", directive
    assert directive.viewer_intent == "story_choice", directive
    assert directive.response_move == "continue_selected_topic", directive
    assert "generic_quiet_room_prompt" in directive.avoid, directive
    assert "reopen_choice_menu" in directive.avoid, directive
    print("  PASSED")


def _test_repeat_quiet_room_builds_repeat_directive():
    print("[9I Smoke] Test 3: repeated quiet-room prompt builds repeat directive...")
    from nana.runtime.public_conversation_director import build_public_conversation_directive

    directive = build_public_conversation_directive(text="phòng nay im quá", repeat_count=4)
    assert directive.thread == "repeat_test", directive
    assert directive.response_move == "tease_repeat_pattern", directive
    assert "same_choice_menu" in directive.avoid, directive
    fatigue = build_public_conversation_directive(text="phòng nay im quá", repeat_count=7)
    assert fatigue.thread == "repeat_test", fatigue
    assert fatigue.response_move == "cooldown_or_tease_repeat", fatigue
    print("  PASSED")


def _test_social_style_hint_includes_9i_directive():
    print("[9I Smoke] Test 4: social style hint includes 9I selected-topic directive...")
    import time

    from nana.runtime.external_bridge import ExternalBridgeRequest
    from nana.runtime.social_session import SocialSessionCache

    session = SocialSessionCache(priority_viewers=("linhcute2746",))
    first = session.observe(
        viewer_name="linhcute2746",
        text="phòng nay im quá",
        event_type="message",
        priority="priority_public",
        monotonic_now=100.0,
        now=100.0,
    )
    request = ExternalBridgeRequest(
        request_id="9i-style-hint-1",
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
        metadata={"message_id": "9i-style-hint-1"},
    )
    session.record_reply_context(
        request,
        first,
        "Nana mở mồi nhé: game đang cày, bài nhạc cứu mood, hay chuyện ngáo hôm nay?",
        monotonic_now=101.0,
        now=101.0,
    )

    second = session.observe(
        viewer_name="linhcute2746",
        text="chuyện ngáo",
        event_type="message",
        priority="priority_public",
        monotonic_now=110.0,
        now=110.0,
    )
    hint = second.style_hint.lower()
    assert "public conversation director (stage-9i)" in hint, hint
    assert "conversation_thread=selected_story_thread" in hint, hint
    assert "response_move=continue_selected_topic" in hint, hint
    assert "do not reopen the quiet-room choice menu" in hint, hint
    print("  PASSED")


def _test_external_bridge_metadata_carries_9i_without_api():
    print("[9I Smoke] Test 5: external bridge carries 9I directive with responder stub...")
    import tempfile
    import time

    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.viewer_chat import ViewerChatQueue

    runtime = ExternalBridgeRuntime(
        request_dir=Path(tempfile.mkdtemp()) / "requests",
        reply_dir=Path(tempfile.mkdtemp()) / "replies",
        enabled=True,
        queue=ViewerChatQueue(duplicate_window_seconds=-1.0, rate_limit_max=99),
        social_session=SocialSessionCache(priority_viewers=("linhcute2746",)),
    )

    first = ExternalBridgeRequest(
        request_id="9i-bridge-1",
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
        metadata={"message_id": "9i-bridge-1", "route": {"chat_channel_name": "chung"}},
    )
    runtime.process_request(
        first,
        responder=lambda _request: "Nana mở mồi nhé: game đang cày, bài nhạc cứu mood, hay chuyện ngáo hôm nay?",
    )

    second = ExternalBridgeRequest(
        request_id="9i-bridge-2",
        source="discord",
        event_type="message",
        text="chuyện ngáo",
        guild_id=1,
        channel_id=2,
        voice_channel_id=None,
        author_id=3,
        author_name="linhcute2746",
        audio_target="discord_voice",
        local_playback=False,
        created_at=time.time(),
        metadata={"message_id": "9i-bridge-2", "route": {"chat_channel_name": "chung"}},
    )
    reply = runtime.process_request(second, responder=lambda _request: "Chuyện ngáo thì Nana kể một mẩu nhỏ.")
    style_hint = reply["metadata"]["social_session"]["style_hint"].lower()
    assert "public conversation director (stage-9i)" in style_hint, style_hint
    assert "continue_selected_topic" in style_hint, style_hint
    assert reply["reply_text"], reply
    print("  PASSED")


def _test_status_help_firewall_surface():
    print("[9I Smoke] Test 6: status/help/firewall surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_conversation_director import (
        public_conversation_preview_lines,
        public_conversation_status_lines,
    )
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    status = "\n".join(public_conversation_status_lines())
    preview = "\n".join(public_conversation_preview_lines("chuyện ngáo"))
    assert "STAGE-9I" in status, status
    assert "read_only=True" in status, status
    assert "Public Conversation Preview" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Public conversation director:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/public-conversation-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/public-conversation-status") == "backstage_command"
    assert guard.classify_public_input("/public-conversation-preview chuyện ngáo") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9I Public Conversation Director — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_quiet_room_builds_warmup_directive,
        _test_selected_story_continues_existing_thread,
        _test_repeat_quiet_room_builds_repeat_directive,
        _test_social_style_hint_includes_9i_directive,
        _test_external_bridge_metadata_carries_9i_without_api,
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
