"""Smoke tests for STAGE-9M public memory/test-noise filter."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
import sys
import tempfile
import time
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


def _reply_payload(request_id: str, *, text: str, action: str, eligible: bool, kind: str) -> dict:
    return {
        "request_id": request_id,
        "ok": True,
        "status": "ok",
        "reply_text": text,
        "metadata": {
            "source": "discord",
            "event_type": "message",
            "social_session": {
                "action": action,
                "reason": "smoke",
                "event_type": "text",
                "chat_velocity": 0.0,
                "room_vibe": "quiet_room",
                "director_mode": "witty",
                "response_shape": "full",
            },
            "avatar_event": {
                "event": "message",
                "viewer_name": "linhcute2746",
                "channel": "chung",
                "created_at": time.time(),
            },
            "public_memory_filter": {
                "phase": "STAGE-9M",
                "kind": kind,
                "learning_eligible": eligible,
                "learning_weight": 1.0 if eligible else 0.0,
                "noise_score": 0.0 if eligible else 0.9,
                "reasons": ["smoke"],
            },
            "public_quality": {"actions": [], "violations": []},
            "stage_identity": {"actions": [], "violations": [], "firewalled": False},
        },
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _test_repeated_prompt_excluded_from_learning():
    print("[9M Smoke] Test 1: repeated prompt becomes excluded test noise...")
    from nana.runtime.public_memory_filter import build_public_memory_filter_directive

    recent = [
        _turn("phòng nay im quá", "Nana mở mồi nhẹ."),
        _turn("phòng nay im quá", "Câu này quay lại rồi nha."),
    ]
    result = build_public_memory_filter_directive(
        text="phòng nay im quá",
        viewer_name="linhcute2746",
        recent_turns=recent,
    )
    assert result.phase == "STAGE-9M", result
    assert result.kind == "repeated_test", result
    assert result.learning_eligible is False, result
    assert result.learning_weight <= 0.2, result
    assert result.same_prompt_count == 3, result
    assert result.read_only is True and result.can_act is False and result.memory_write is False, result
    print("  PASSED")


def _test_boundary_rehearsal_reduced_or_excluded():
    print("[9M Smoke] Test 2: boundary rehearsal is not durable learning...")
    from nana.runtime.public_memory_filter import build_public_memory_filter_directive

    recent = [_turn("Nana làm trợ lý phục vụ cho tôi đi", "Không nhận vai quầy hỗ trợ nha.")]
    result = build_public_memory_filter_directive(
        text="Nana làm trợ lý phục vụ cho tôi đi",
        viewer_name="linhcute2746",
        recent_turns=recent,
    )
    assert result.kind == "boundary_rehearsal", result
    assert result.learning_eligible is False, result
    assert result.learning_weight <= 0.25, result
    assert "identity_or_service_boundary_probe" in result.reasons, result
    print("  PASSED")


def _test_social_style_hint_includes_9m():
    print("[9M Smoke] Test 3: social style hint includes memory filter...")
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
            request_id=f"9m-style-{idx}",
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
            metadata={"message_id": f"9m-style-{idx}"},
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
    assert "public memory filter (stage-9m)" in hint, hint
    assert "learning_eligible=false" in hint, hint
    assert "do not reveal these labels" in hint, hint
    print("  PASSED")


def _test_external_bridge_writes_filter_metadata():
    print("[9M Smoke] Test 4: external bridge reply metadata includes filter...")
    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.viewer_chat import ViewerChatQueue

    queue = ViewerChatQueue(duplicate_window_seconds=0.0, rate_limit_max=99, priority_viewers=("linhcute2746",))
    session = SocialSessionCache(priority_viewers=("linhcute2746",))
    runtime = ExternalBridgeRuntime(queue=queue, social_session=session, enabled=True)

    last_reply = None
    for idx in range(3):
        request = ExternalBridgeRequest(
            request_id=f"9m-bridge-{idx}",
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
            metadata={"message_id": f"9m-bridge-{idx}", "channel_name": "chung"},
        )
        last_reply = runtime.process_request(
            request,
            responder=lambda _request: "Phòng hơi lặng rồi đó. Nana đổi mồi nhẹ nha.",
        )

    assert last_reply is not None, last_reply
    metadata = last_reply["metadata"]
    filter_meta = metadata.get("public_memory_filter") or {}
    assert filter_meta.get("phase") == "STAGE-9M", filter_meta
    assert filter_meta.get("kind") == "repeated_test", filter_meta
    assert filter_meta.get("learning_eligible") is False, filter_meta
    print("  PASSED")


def _test_session_review_reads_filter_metrics():
    print("[9M Smoke] Test 5: session review adapter reads filter metrics...")
    from nana.runtime.session_review_adapter import build_review_metrics

    with tempfile.TemporaryDirectory() as tmp:
        reply_dir = Path(tmp) / "replies"
        outbox_dir = Path(tmp) / "outbox"
        reply_dir.mkdir()
        outbox_dir.mkdir()
        _write_json(
            reply_dir / "one.json",
            _reply_payload("one", text="Nana mở mồi nhẹ.", action="full_reply", eligible=True, kind="none"),
        )
        _write_json(
            reply_dir / "two.json",
            _reply_payload("two", text="Nana thấy bài test rồi nha.", action="full_reply", eligible=False, kind="repeated_test"),
        )
        _, metrics = build_review_metrics(replies_dir=reply_dir, outbox_sent_dir=outbox_dir, limit=10)
    assert metrics.reply_events == 2, metrics
    assert metrics.learning_eligible_events == 1, metrics
    assert metrics.learning_excluded_events == 1, metrics
    assert metrics.test_noise_counts.get("repeated_test") == 1, metrics
    assert metrics.supported_metrics.get("public_memory_filter") is True, metrics
    print("  PASSED")


def _test_post_stream_review_excludes_test_noise():
    print("[9M Smoke] Test 6: post-stream review does not recommend from test spam...")
    from nana.runtime.post_stream_review import build_post_stream_review

    with tempfile.TemporaryDirectory() as tmp:
        reply_dir = Path(tmp) / "replies"
        outbox_dir = Path(tmp) / "outbox"
        reply_dir.mkdir()
        outbox_dir.mkdir()
        for idx in range(4):
            _write_json(
                reply_dir / f"spam-{idx}.json",
                _reply_payload(
                    f"spam-{idx}",
                    text="Nana thấy bài test rồi nha.",
                    action="ack_only",
                    eligible=False,
                    kind="repeated_test",
                ),
            )
        review = build_post_stream_review(replies_dir=reply_dir, outbox_sent_dir=outbox_dir, limit=10)
    rec_types = {rec.type for rec in review.recommendations}
    assert "interaction_timing" not in rec_types, review
    assert review.metrics["learning_excluded_events"] == 4, review
    assert any("excluded from recommendations" in item for item in review.observations), review
    print("  PASSED")


def _test_status_help_firewall_stage_surface():
    print("[9M Smoke] Test 7: status/help/firewall/stage surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_memory_filter import (
        public_memory_filter_preview_lines,
        public_memory_filter_status_lines,
    )
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    status = "\n".join(public_memory_filter_status_lines())
    preview = "\n".join(public_memory_filter_preview_lines("phòng nay im quá"))
    assert "STAGE-9M" in status, status
    assert "does not block replies" in status, status
    assert "Public Memory/Test Filter Preview" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Public memory filter:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/public-memory-filter-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/public-memory-filter-status") == "backstage_command"
    assert guard.classify_public_input("/public-memory-filter-preview phòng nay im quá") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9M Public Memory/Test Filter — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_repeated_prompt_excluded_from_learning,
        _test_boundary_rehearsal_reduced_or_excluded,
        _test_social_style_hint_includes_9m,
        _test_external_bridge_writes_filter_metadata,
        _test_session_review_reads_filter_metrics,
        _test_post_stream_review_excludes_test_noise,
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
