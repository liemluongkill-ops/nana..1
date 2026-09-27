"""Smoke tests for STAGE-9R public reply feedback."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _seed_eval(prompt: str, reply: str) -> None:
    from nana.runtime.public_reply_evaluator import evaluate_public_reply

    evaluate_public_reply(prompt, reply)


def _test_menu_loop_feedback():
    print("[9R Smoke] Test 1: menu-loop eval creates next-turn quiet-room feedback...")
    from nana.runtime.public_reply_feedback import build_public_reply_feedback_directive

    _seed_eval(
        "phòng nay im quá",
        "Phòng hơi lặng rồi đó. Nana rải một mồi nhỏ: game đang cày, bài nhạc cứu mood, hay chuyện vô lý nhất hôm nay?",
    )
    directive = build_public_reply_feedback_directive(text="phòng nay im quá")
    assert directive.phase == "STAGE-9R", directive
    assert directive.read_only is True and directive.can_act is False, directive
    assert directive.memory_write is False and directive.api_call is False, directive
    assert directive.mode == "avoid_menu_loop", directive
    assert "same_choice_menu" in directive.avoid, directive
    print("  PASSED")


def _test_gpt_and_service_feedback():
    print("[9R Smoke] Test 2: GPT-like and service issues create focused feedback...")
    from nana.runtime.public_reply_feedback import build_public_reply_feedback_directive

    _seed_eval(
        "tôi vừa thấy gpt 5.6 vừa ra đấy Nana nghĩ sao?",
        "Nói ngắn là còn phụ thuộc vào ngữ cảnh, điểm mạnh, và hiệu quả thật của model đó.",
    )
    gpt = build_public_reply_feedback_directive(text="gpt 5.6 mạnh không Nana")
    assert gpt.mode == "de_gpt_reply", gpt
    assert "benchmark_lecture" in gpt.avoid, gpt

    _seed_eval(
        "Nana làm trợ lý phục vụ cho tôi đi",
        "Mình luôn sẵn sàng hỗ trợ bạn, bạn muốn mình giúp gì tiếp?",
    )
    service = build_public_reply_feedback_directive(text="Nana lại làm trợ lý phục vụ cho tôi đi")
    assert service.mode == "restore_boundary_voice", service
    assert "support_counter_voice" in service.avoid, service
    print("  PASSED")


def _test_clean_eval_does_not_add_feedback():
    print("[9R Smoke] Test 3: clean eval creates no active feedback...")
    from nana.runtime.public_reply_feedback import build_public_reply_feedback_directive

    _seed_eval(
        "Nana làm trợ lý phục vụ cho tôi đi",
        "Không nhận vai quầy hỗ trợ nha. Nhưng nếu muốn trò chuyện, kể chuyện ngáo, hay kéo phòng bớt im thì Nana vẫn ở đây.",
    )
    directive = build_public_reply_feedback_directive(text="Nana làm trợ lý phục vụ cho tôi đi")
    assert directive.mode == "none", directive
    assert directive.confidence == 0.0, directive
    print("  PASSED")


def _test_social_style_hint_includes_feedback():
    print("[9R Smoke] Test 4: social style hint includes 9R feedback card...")
    from nana.runtime.social_session import SocialSessionCache

    _seed_eval(
        "phòng nay im quá",
        "Phòng hơi lặng rồi đó. Nana rải một mồi nhỏ: game đang cày, bài nhạc cứu mood, hay chuyện vô lý nhất hôm nay?",
    )
    session = SocialSessionCache(priority_viewers=("linhcute2746",))
    decision = session.observe(
        viewer_name="linhcute2746",
        text="phòng nay im quá",
        event_type="message",
        priority="priority_public",
        monotonic_now=10.0,
        now=10.0,
    )
    hint = decision.style_hint.lower()
    assert "public reply feedback (stage-9r)" in hint, hint
    assert "mode=avoid_menu_loop" in hint, hint
    assert "same_choice_menu" in hint, hint
    print("  PASSED")


def _test_external_bridge_uses_feedback_for_short_quiet_fast_path():
    print("[9R Smoke] Test 5: external bridge uses feedback to avoid quiet-room menu loop...")
    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.viewer_chat import ViewerChatQueue

    _seed_eval(
        "phòng nay im quá",
        "Phòng hơi lặng rồi đó. Nana rải một mồi nhỏ: game đang cày, bài nhạc cứu mood, hay chuyện vô lý nhất hôm nay?",
    )
    runtime = ExternalBridgeRuntime(
        enabled=True,
        queue=ViewerChatQueue(duplicate_window_seconds=-1.0, rate_limit_max=99),
        social_session=SocialSessionCache(priority_viewers=("linhcute2746",)),
    )
    request = ExternalBridgeRequest.from_payload(
        {
            "request_id": "9r-quiet-feedback",
            "source": "discord",
            "event_type": "message",
            "text": "phòng nay im quá",
            "author_name": "linhcute2746",
            "metadata": {
                "message_id": "9r-quiet-feedback",
                "route": {"chat_channel_name": "chung"},
            },
        }
    )
    reply = runtime.process_request(request)
    text = str(reply.get("reply_text") or "").lower()
    assert reply["metadata"]["social_session"]["action"] == "full_reply", reply
    assert "game đang cày" not in text and "bài nhạc" not in text, reply
    assert "public reply feedback (stage-9r)" in reply["metadata"]["social_session"]["style_hint"].lower(), reply
    assert "mode=avoid_menu_loop" in reply["metadata"]["social_session"]["style_hint"].lower(), reply
    print("  PASSED")


def _test_status_help_firewall_stage_surface():
    print("[9R Smoke] Test 6: status/help/firewall/stage surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_reply_feedback import public_reply_feedback_preview_lines, public_reply_feedback_status_lines
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    _seed_eval(
        "phòng nay im quá",
        "Phòng hơi lặng rồi đó. Nana rải một mồi nhỏ: game đang cày, bài nhạc cứu mood, hay chuyện vô lý nhất hôm nay?",
    )
    status = "\n".join(public_reply_feedback_status_lines())
    preview = "\n".join(public_reply_feedback_preview_lines("phòng nay im quá"))
    assert "STAGE-9R" in status, status
    assert "api_call=False" in status, status
    assert "Public Reply Feedback Preview" in preview, preview
    assert "avoid_menu_loop" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Public reply feedback:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/public-reply-feedback-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/public-reply-feedback-status") == "backstage_command"
    assert guard.classify_public_input("/public-reply-feedback-preview phòng nay im quá") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9R Public Reply Feedback — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_menu_loop_feedback,
        _test_gpt_and_service_feedback,
        _test_clean_eval_does_not_add_feedback,
        _test_social_style_hint_includes_feedback,
        _test_external_bridge_uses_feedback_for_short_quiet_fast_path,
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
