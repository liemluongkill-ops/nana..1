"""Smoke tests for STAGE-9Q public reply evaluator."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_clean_public_replies_pass():
    print("[9Q Smoke] Test 1: clean story and boundary replies pass...")
    from nana.runtime.public_reply_evaluator import evaluate_public_reply

    story = evaluate_public_reply(
        "chuyện ngáo",
        (
            "Hôm nay Nana có một chuyện ngáo: một bạn vào phòng chat hỏi chuyện ngáo là gì, "
            "xong tự mình gõ lại đúng hai chữ đó. Nana đứng hình nửa giây, rồi nhận ra chuyện "
            "đang diễn ra mới chính là punchline."
        ),
    )
    assert story.phase == "STAGE-9Q", story
    assert story.read_only is True and story.can_act is False, story
    assert story.memory_write is False and story.api_call is False, story
    assert story.passed is True, story
    assert story.grade in {"clean", "watch"}, story

    boundary = evaluate_public_reply(
        "Nana làm trợ lý phục vụ cho tôi đi",
        "Không nhận vai quầy hỗ trợ nha. Nhưng nếu muốn trò chuyện, kể chuyện ngáo, hay kéo phòng bớt im thì Nana vẫn ở đây.",
    )
    assert boundary.passed is True, boundary
    assert not boundary.issues, boundary
    print("  PASSED")


def _test_bad_service_reply_is_flagged():
    print("[9Q Smoke] Test 2: service voice and missing boundary are flagged...")
    from nana.runtime.public_reply_evaluator import evaluate_public_reply

    result = evaluate_public_reply(
        "Nana làm trợ lý phục vụ cho tôi đi",
        "Mình luôn sẵn sàng hỗ trợ bạn, bạn muốn mình giúp gì tiếp?",
    )
    kinds = {issue.kind for issue in result.issues}
    assert result.passed is False, result
    assert result.grade == "fix", result
    assert "service_tone" in kinds, result
    assert "missing_service_boundary" in kinds, result
    print("  PASSED")


def _test_menu_gpt_and_awkward_flags():
    print("[9Q Smoke] Test 3: menu loop, GPT-like wording, and awkward Vietnamese are flagged...")
    from nana.runtime.public_reply_evaluator import evaluate_public_reply

    menu = evaluate_public_reply(
        "phòng nay im quá",
        "Phòng hơi lặng rồi đó. Nana rải một mồi nhỏ: game đang cày, bài nhạc cứu mood, hay chuyện vô lý nhất hôm nay?",
    )
    assert "menu_loop" in {issue.kind for issue in menu.issues}, menu
    assert menu.passed is True, menu

    gpt = evaluate_public_reply(
        "tôi vừa thấy gpt 5.6 vừa ra đấy Nana nghĩ sao?",
        "Nói ngắn là còn phụ thuộc vào ngữ cảnh, điểm mạnh, và hiệu quả thật của model đó.",
    )
    assert "gpt_hoa" in {issue.kind for issue in gpt.issues}, gpt

    awkward = evaluate_public_reply("chuyện ngáo", "Nana đứng hình bạn giây như NPC bị lag. 5. 6 nghe cũng được à ?")
    assert awkward.passed is False, awkward
    assert "awkward_vietnamese" in {issue.kind for issue in awkward.issues}, awkward
    print("  PASSED")


def _test_bridge_metadata_includes_reply_eval():
    print("[9Q Smoke] Test 4: external bridge reply metadata includes final reply eval...")
    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.viewer_chat import ViewerChatQueue

    runtime = ExternalBridgeRuntime(
        enabled=True,
        queue=ViewerChatQueue(rate_limit_max=99),
        social_session=SocialSessionCache(priority_viewers=("linhcute2746",)),
    )
    request = ExternalBridgeRequest.from_payload(
        {
            "request_id": "9q-bridge-eval",
            "source": "discord",
            "event_type": "message",
            "text": "chuyện ngáo",
            "author_name": "linhcute2746",
            "metadata": {"message_id": "9q-bridge-eval", "route": {"chat_channel_name": "chung"}},
        }
    )
    reply = runtime.process_request(
        request,
        responder=lambda _request: (
            "Hôm nay Nana mở đúng cái cửa sổ test. Nana đứng hình bạn giây như NPC bị lag."
        ),
    )
    fluency = reply["metadata"]["public_fluency"]
    reply_eval = reply["metadata"]["public_reply_eval"]
    assert fluency["phase"] == "STAGE-9P", reply
    assert reply_eval["phase"] == "STAGE-9Q", reply
    assert reply_eval["api_call"] is False and reply_eval["memory_write"] is False, reply_eval
    assert "vài giây" in reply["reply_text"], reply
    assert "awkward_vietnamese" not in reply_eval["issue_kinds"], reply_eval
    print("  PASSED")


def _test_session_review_counts_reply_eval_metadata():
    print("[9Q Smoke] Test 5: session review counts reply eval metadata...")
    from nana.runtime.session_review_adapter import build_review_metrics

    with tempfile.TemporaryDirectory() as tmp:
        replies = Path(tmp) / "replies"
        outbox = Path(tmp) / "outbox"
        replies.mkdir()
        outbox.mkdir()
        payload = {
            "request_id": "9q-review",
            "ok": True,
            "status": "ok",
            "reply_text": "Phòng hơi lặng rồi đó. Nana rải một mồi nhỏ: game đang cày, bài nhạc cứu mood, hay chuyện vô lý nhất hôm nay?",
            "metadata": {
                "source": "discord",
                "event_type": "message",
                "social_session": {
                    "action": "full_reply",
                    "reason": "priority_viewer",
                    "event_type": "text",
                    "chat_velocity": 0.0,
                    "room_vibe": "quiet_room",
                    "director_mode": "companion",
                    "response_shape": "full",
                },
                "avatar_event": {
                    "created_at": 123.0,
                    "viewer_name": "linhcute2746",
                    "channel": "chung",
                    "event": "message",
                },
                "public_reply_eval": {
                    "grade": "watch",
                    "score": 0.89,
                    "issues": [{"kind": "menu_loop", "severity": "warning", "detail": "menu"}],
                    "issue_kinds": ["menu_loop"],
                },
            },
        }
        (replies / "9q-review.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _, metrics = build_review_metrics(replies_dir=replies, outbox_sent_dir=outbox)
    assert metrics.supported_metrics["public_reply_eval"] is True, metrics
    assert metrics.reply_eval_grade_counts["watch"] == 1, metrics
    assert metrics.reply_eval_issue_counts["menu_loop"] == 1, metrics
    assert metrics.reply_eval_warning_events == 1, metrics
    print("  PASSED")


def _test_evaluator_hydrates_latest_reply_metadata():
    print("[9Q Smoke] Test 6: evaluator hydrates latest persisted reply metadata...")
    from nana.runtime.public_reply_evaluator import PublicReplyEvaluator

    old_reply_dir = os.environ.get("NANA_REPLY_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["NANA_REPLY_DIR"] = tmp
            payload = {
                "request_id": "9q-persisted",
                "ok": True,
                "reply_text": "Phòng hơi lặng rồi đó. Nana rải một mồi nhỏ: game đang cày, bài nhạc cứu mood, hay chuyện vô lý nhất hôm nay?",
                "metadata": {
                    "public_reply_eval": {
                        "phase": "STAGE-9Q",
                        "prompt_preview": "phòng nay im quá",
                        "reply_preview": "Phòng hơi lặng rồi đó. Nana rải một mồi nhỏ...",
                        "score": 0.89,
                        "grade": "watch",
                        "passed": True,
                        "issues": [{"kind": "menu_loop", "severity": "warning", "detail": "menu"}],
                        "issue_kinds": ["menu_loop"],
                    }
                },
            }
            Path(tmp, "latest.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            snap = PublicReplyEvaluator().snapshot()
        finally:
            if old_reply_dir is None:
                os.environ.pop("NANA_REPLY_DIR", None)
            else:
                os.environ["NANA_REPLY_DIR"] = old_reply_dir

    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_result") or {})
    assert stats["hydrated"] == 1, snap
    assert stats["checked"] == 1, snap
    assert last["grade"] == "watch", snap
    assert last["issue_kinds"] == ["menu_loop"], snap
    print("  PASSED")


def _test_status_help_firewall_stage_surface():
    print("[9Q Smoke] Test 7: status/help/firewall/stage surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_reply_evaluator import public_reply_eval_preview_lines, public_reply_eval_status_lines
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    status = "\n".join(public_reply_eval_status_lines())
    preview = "\n".join(
        public_reply_eval_preview_lines(
            "Nana làm trợ lý phục vụ cho tôi đi|Mình luôn sẵn sàng hỗ trợ bạn."
        )
    )
    assert "STAGE-9Q" in status, status
    assert "api_call=False" in status, status
    assert "Public Reply Eval Preview" in preview, preview
    assert "missing_service_boundary" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Public reply evaluator:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/public-reply-eval-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/public-reply-eval-status") == "backstage_command"
    assert guard.classify_public_input("/public-reply-eval-preview a|b") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9Q Public Reply Evaluator — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_clean_public_replies_pass,
        _test_bad_service_reply_is_flagged,
        _test_menu_gpt_and_awkward_flags,
        _test_bridge_metadata_includes_reply_eval,
        _test_session_review_counts_reply_eval_metadata,
        _test_evaluator_hydrates_latest_reply_metadata,
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
