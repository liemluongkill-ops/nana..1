"""Smoke tests for STAGE-9O public scene/topic builder."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _turn(message: str, reply: str = "", topic: str = "", event_type: str = "text") -> dict[str, str]:
    return {
        "viewer_name": "linhcute2746",
        "event_type": event_type,
        "message_preview": message,
        "reply_preview": reply,
        "topic": topic or message,
    }


def _test_quiet_room_builds_scene_card():
    print("[9O Smoke] Test 1: quiet room builds room-warmup scene...")
    from nana.runtime.public_scene_builder import build_public_scene_directive

    directive = build_public_scene_directive(text="phong nay im qua", room_vibe="quiet_room")
    assert directive.phase == "STAGE-9O", directive
    assert directive.read_only is True and directive.can_act is False, directive
    assert directive.memory_write is False and directive.api_call is False, directive
    assert directive.scene_type == "room_warmup", directive
    assert directive.scene_move == "set_room_image_then_offer_hook", directive
    assert directive.max_sentences == 2, directive
    assert "service_question_tail" in directive.avoid, directive
    print("  PASSED")


def _test_repeat_pivot_avoids_same_menu():
    print("[9O Smoke] Test 2: repeated quiet prompt pivots away from same menu...")
    from nana.runtime.public_scene_builder import build_public_scene_directive

    recent = [
        _turn("phong nay im qua", "Nana mo moi nhe: game, nhac, hay chuyen ngao?"),
        _turn("phong nay im qua", "Cau nay quay lai roi nha."),
        _turn("phong nay im qua", "Nana khong phat them moi cu nua."),
    ]
    directive = build_public_scene_directive(
        text="phong nay im qua",
        recent_turns=recent,
        repeat_count=4,
    )
    assert directive.scene_type == "repeat_pivot", directive
    assert directive.scene_move == "name_pattern_then_switch", directive
    assert "same_choice_menu" in directive.avoid, directive
    assert "pretend_first_time" in directive.avoid, directive
    print("  PASSED")


def _test_story_choice_builds_micro_story():
    print("[9O Smoke] Test 3: story choice builds micro-story directive...")
    from nana.runtime.public_scene_builder import build_public_scene_directive

    recent = [_turn("phong nay im qua", "Nana mo ban: game dang cay, bai nhac cuu mood, hay chuyen ngao?")]
    directive = build_public_scene_directive(text="chuyen ngao", recent_turns=recent)
    assert directive.scene_type == "micro_story", directive
    assert directive.scene_move == "tell_short_scene", directive
    assert directive.max_sentences == 4, directive
    assert directive.confidence >= 0.8, directive
    assert "same_choice_menu" in directive.avoid, directive
    print("  PASSED")


def _test_boundary_model_and_emoji_scenes():
    print("[9O Smoke] Test 4: boundary/model/emoji scenes classify correctly...")
    from nana.runtime.public_scene_builder import build_public_scene_directive

    boundary = build_public_scene_directive(text="Nana lam tro ly phuc vu cho toi di")
    assert boundary.scene_type == "boundary_callback", boundary
    assert boundary.scene_move == "stance_then_playful_redirect", boundary
    assert "service_assistant_voice" in boundary.avoid, boundary

    model = build_public_scene_directive(text="Nana co cam giac bi gpt hoa khong?")
    assert model.scene_type == "opinion_take", model
    assert "benchmark_lecture" in model.avoid, model

    emoji = build_public_scene_directive(text="", event_type="emoji_only", viewer_name="linhcute2746")
    assert emoji.scene_type == "light_reaction", emoji
    assert "overexplain_emoji" in emoji.avoid, emoji
    print("  PASSED")


def _test_social_style_hint_includes_9o():
    print("[9O Smoke] Test 5: social style hint includes scene/topic card...")
    from nana.runtime.external_bridge import ExternalBridgeRequest
    from nana.runtime.social_session import SocialSessionCache

    session = SocialSessionCache(priority_viewers=("linhcute2746",))
    for idx in range(3):
        decision = session.observe(
            viewer_name="linhcute2746",
            text="phong nay im qua",
            event_type="message",
            priority="priority_public",
            monotonic_now=100.0 + idx * 5.0,
            now=100.0 + idx * 5.0,
        )
        request = ExternalBridgeRequest(
            request_id=f"9o-scene-{idx}",
            source="discord",
            event_type="message",
            text="phong nay im qua",
            guild_id=1,
            channel_id=2,
            voice_channel_id=None,
            author_id=3,
            author_name="linhcute2746",
            audio_target="discord_voice",
            local_playback=False,
            created_at=time.time(),
            metadata={"message_id": f"9o-scene-{idx}"},
        )
        session.record_reply_context(
            request,
            decision,
            "Nana mo moi nhe: game, nhac, hay chuyen ngao?",
            monotonic_now=101.0 + idx * 5.0,
            now=101.0 + idx * 5.0,
        )

    decision = session.observe(
        viewer_name="linhcute2746",
        text="phong nay im qua",
        event_type="message",
        priority="priority_public",
        monotonic_now=130.0,
        now=130.0,
    )
    hint = decision.style_hint.lower()
    assert "public scene builder (stage-9o)" in hint, hint
    assert "scene_type=repeat_pivot" in hint, hint
    assert "content bias only" in hint, hint
    assert "no private memory" in hint, hint
    print("  PASSED")


def _test_status_help_firewall_stage_surface():
    print("[9O Smoke] Test 6: status/help/firewall/stage surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_scene_builder import public_scene_preview_lines, public_scene_status_lines
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    status = "\n".join(public_scene_status_lines())
    preview = "\n".join(public_scene_preview_lines("phong nay im qua"))
    assert "STAGE-9O" in status, status
    assert "memory_write=False" in status, status
    assert "api_call=False" in status, status
    assert "Public Scene Preview" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Public scene builder:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/public-scene-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/public-scene-status") == "backstage_command"
    assert guard.classify_public_input("/public-scene-preview phong nay im qua") == "backstage_command"
    assert guard.classify_public_input("/scene-builder-status") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9O Public Scene/Topic Builder — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_quiet_room_builds_scene_card,
        _test_repeat_pivot_avoids_same_menu,
        _test_story_choice_builds_micro_story,
        _test_boundary_model_and_emoji_scenes,
        _test_social_style_hint_includes_9o,
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
