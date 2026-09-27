"""Smoke tests for STAGE-9N public viewer memory-lite."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _turn(
    message: str,
    *,
    viewer: str = "linhcute2746",
    reply: str = "",
    topic: str = "",
    event_type: str = "text",
) -> dict[str, str]:
    return {
        "viewer_name": viewer,
        "event_type": event_type,
        "message_preview": message,
        "reply_preview": reply,
        "topic": topic or message,
    }


def _test_quiet_room_profile_is_per_viewer():
    print("[9N Smoke] Test 1: repeated quiet-room prompt becomes per-viewer pattern...")
    from nana.runtime.public_viewer_memory import build_public_viewer_memory_directive

    recent = [
        _turn("phòng nay im quá", reply="Nana mở mồi nhẹ."),
        _turn("phòng nay im quá", reply="Câu này quay lại rồi nha."),
        _turn("phòng nay im quá", viewer="quysgp", reply="Viewer khác nói phòng im."),
    ]
    directive = build_public_viewer_memory_directive(
        text="phòng nay im quá",
        viewer_name="linhcute2746",
        recent_turns=recent,
    )
    assert directive.phase == "STAGE-9N", directive
    assert directive.read_only is True and directive.can_act is False and directive.memory_write is False, directive
    assert directive.viewer_profile == "quiet_room_poker", directive
    assert directive.same_prompt_count == 3, directive
    assert directive.viewer_turns == 2, directive
    assert "same_room_menu" in directive.avoid, directive
    print("  PASSED")


def _test_story_and_emoji_profiles():
    print("[9N Smoke] Test 2: story and emoji tendencies are recognized...")
    from nana.runtime.public_viewer_memory import build_public_viewer_memory_directive

    story = build_public_viewer_memory_directive(
        text="chuyện ngáo",
        viewer_name="quysgp",
        recent_turns=[_turn("kể chuyện gì nghe xem", viewer="quysgp", topic="chuyện ngáo")],
    )
    assert story.viewer_profile == "story_picker", story
    assert story.session_pattern == "story_thread_preference", story
    assert "reset_story_thread" in story.avoid, story

    emoji = build_public_viewer_memory_directive(
        text="icon này cute",
        viewer_name="linhcute2746",
        recent_turns=[_turn("😀", viewer="linhcute2746", event_type="emoji_only", topic="emoji")],
    )
    assert emoji.viewer_profile == "emoji_reactor", emoji
    assert emoji.session_pattern == "light_reaction_viewer", emoji
    assert "overexplain_emoji" in emoji.avoid, emoji
    print("  PASSED")


def _test_boundary_and_model_profiles():
    print("[9N Smoke] Test 3: boundary/model discussions stay public-safe...")
    from nana.runtime.public_viewer_memory import build_public_viewer_memory_directive

    boundary = build_public_viewer_memory_directive(
        text="Nana làm trợ lý phục vụ cho tôi đi",
        viewer_name="linhcute2746",
        recent_turns=[_turn("Nana chỉ là bot Discord thôi đúng không?", reply="Nana là Nana.")],
    )
    assert boundary.viewer_profile == "boundary_tester", boundary
    assert "private_lane_leak" in boundary.avoid, boundary

    model = build_public_viewer_memory_directive(
        text="Nana có bị gpt hóa không?",
        viewer_name="linhcute2746",
        recent_turns=[_turn("model nào nói tự nhiên hơn?", reply="Nana thích cái nào giữ mạch.")],
    )
    assert model.viewer_profile == "model_talker", model
    assert "benchmark_lecture" in model.avoid, model
    print("  PASSED")


def _test_social_style_hint_includes_9n():
    print("[9N Smoke] Test 4: social style hint includes per-viewer memory...")
    from nana.runtime.external_bridge import ExternalBridgeRequest
    from nana.runtime.social_session import SocialSessionCache

    session = SocialSessionCache(priority_viewers=("linhcute2746",))
    for idx in range(2):
        decision = session.observe(
            viewer_name="linhcute2746",
            text="phòng nay im quá",
            event_type="message",
            priority="priority_public",
            monotonic_now=100.0 + idx * 5.0,
            now=100.0 + idx * 5.0,
        )
        request = ExternalBridgeRequest(
            request_id=f"9n-style-{idx}",
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
            metadata={"message_id": f"9n-style-{idx}"},
        )
        session.record_reply_context(
            request,
            decision,
            "Nana mở mồi nhẹ.",
            monotonic_now=101.0 + idx * 5.0,
            now=101.0 + idx * 5.0,
        )

    decision = session.observe(
        viewer_name="linhcute2746",
        text="phòng nay im quá",
        event_type="message",
        priority="priority_public",
        monotonic_now=120.0,
        now=120.0,
    )
    hint = decision.style_hint.lower()
    assert "public viewer memory-lite (stage-9n)" in hint, hint
    assert "viewer_profile=quiet_room_poker" in hint, hint
    assert "session_pattern=repeated_quiet_room_probe" in hint, hint
    assert "no private memory and no long-term write" in hint, hint
    print("  PASSED")


def _test_status_help_firewall_stage_surface():
    print("[9N Smoke] Test 5: status/help/firewall/stage surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard
    from nana.runtime.public_viewer_memory import (
        public_viewer_memory_preview_lines,
        public_viewer_memory_status_lines,
    )

    status = "\n".join(public_viewer_memory_status_lines())
    preview = "\n".join(public_viewer_memory_preview_lines("linhcute2746|phòng nay im quá"))
    assert "STAGE-9N" in status, status
    assert "memory_write=False" in status, status
    assert "Public Viewer Memory Preview" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Public viewer memory:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/public-viewer-memory-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/public-viewer-memory-status") == "backstage_command"
    assert guard.classify_public_input("/public-viewer-memory-preview linh|phòng nay im quá") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9N Public Viewer Memory-lite — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_quiet_room_profile_is_per_viewer,
        _test_story_and_emoji_profiles,
        _test_boundary_and_model_profiles,
        _test_social_style_hint_includes_9n,
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
