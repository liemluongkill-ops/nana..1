"""STAGE-9C-A1 smoke tests: recommendation-only post-stream review.

No LLM, no memory/config write, no auto-apply, no TTS/VTS/OBS/Discord/game.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _reply_payload(
    *,
    request_id: str,
    action: str = "full_reply",
    social_event: str = "message",
    avatar_event: str = "message",
    reply_text: str = "Nana nghe đây nè.",
    quality_violations: list | None = None,
    stage_violations: list | None = None,
    public_memory_filter: dict | None = None,
) -> dict:
    metadata = {
        "source": "discord",
        "event_type": "message",
        "social_session": {
            "action": action,
            "reason": "smoke",
            "event_type": social_event,
            "chat_velocity": 0.05,
            "room_vibe": "quiet_room",
            "director_mode": "answer",
            "response_shape": "short_reply",
        },
        "avatar_event": {
            "event": avatar_event,
            "source": "discord",
            "viewer_name": "linhcute2746",
            "channel": "chung",
            "created_at": 1234.0,
        },
        "public_quality": {
            "actions": ["clean"],
            "violations": quality_violations or [],
        },
        "stage_identity": {
            "actions": ["passed"],
            "firewalled": bool(stage_violations),
            "violations": stage_violations or [],
        },
    }
    if public_memory_filter is not None:
        metadata["public_memory_filter"] = public_memory_filter
    return {
        "request_id": request_id,
        "ok": True,
        "status": "ok",
        "reply_text": reply_text,
        "metadata": metadata,
    }


def _outbox_payload(index: int) -> dict:
    return {
        "event_id": f"starter-{index}",
        "proposal_id": f"proposal-{index}",
        "text": f"Ưm... phòng mình yên quá nhỉ? #{index}",
        "channel_id": "1518954968240095245",
        "channel_name": "chung",
        "source": "starter_proposal",
        "phase": "STAGE-8F",
        "created_at": 1235.0 + index,
        "stream_state": "live_idle",
        "interaction_tone": "quiet",
        "auto_send": False,
        "approved_by_owner": True,
    }


def _make_temp_dataset():
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    return temp, root / "replies", root / "outbox" / "sent"


def _test_generates_recommendations_with_evidence():
    print("[9C-A1 Smoke] Test 1: generate recommendations with evidence snippets...")
    from nana.runtime.post_stream_review import build_post_stream_review

    temp, replies, sent = _make_temp_dataset()
    try:
        for index in range(4):
            _write_json(
                replies / f"ack{index}.json",
                _reply_payload(
                    request_id=f"ack-{index}",
                    action="ack_only",
                    social_event="emoji_only",
                    avatar_event="emoji_only",
                    reply_text=f"Nana thấy icon rồi nè #{index}.",
                ),
            )
        for index in range(4):
            _write_json(sent / f"starter{index}.json", _outbox_payload(index))
        review = build_post_stream_review(replies_dir=replies, outbox_sent_dir=sent)
        assert review.generated is True, review
        assert review.reason == "recommendations_ready", review
        assert len(review.recommendations) >= 2, review
        for recommendation in review.recommendations:
            assert recommendation.evidence_snippets, recommendation
            assert recommendation.confidence > 0.0, recommendation
            assert recommendation.evidence_count >= 1, recommendation
            assert recommendation.evidence_sources, recommendation
            assert all(snippet.startswith("[") for snippet in recommendation.evidence_snippets), recommendation
            assert recommendation.auto_apply is False, recommendation
            assert recommendation.read_only is True, recommendation
        assert review.memory_write is False, review
        assert review.can_act is False, review
        print("  PASSED")
    finally:
        temp.cleanup()


def _test_single_occurrence_is_observation_only():
    print("[9C-A1 Smoke] Test 2: single occurrence does not emit recommendation...")
    from nana.runtime.post_stream_review import build_post_stream_review

    temp, replies, sent = _make_temp_dataset()
    try:
        _write_json(
            replies / "one.json",
            _reply_payload(
                request_id="one",
                action="ack_only",
                social_event="emoji_only",
                avatar_event="emoji_only",
                reply_text="Nana thấy rồi.",
            ),
        )
        _write_json(sent / "one_starter.json", _outbox_payload(1))
        review = build_post_stream_review(replies_dir=replies, outbox_sent_dir=sent)
        assert len(review.recommendations) == 0, review
        assert "No pattern reached" in "\n".join(review.observations), review
        print("  PASSED")
    finally:
        temp.cleanup()


def _test_guard_issues_recommendation_requires_three():
    print("[9C-A1 Smoke] Test 3: repeated guard issues create persona guard recommendation...")
    from nana.runtime.post_stream_review import build_post_stream_review

    temp, replies, sent = _make_temp_dataset()
    try:
        for index in range(3):
            _write_json(
                replies / f"guard{index}.json",
                _reply_payload(
                    request_id=f"guard-{index}",
                    reply_text=f"Có gì cần hỗ trợ không bạn #{index}",
                    quality_violations=[{"kind": "service_bot_tone"}],
                ),
            )
        review = build_post_stream_review(replies_dir=replies, outbox_sent_dir=sent)
        assert any(rec.type == "persona_guard" for rec in review.recommendations), review
        guard_rec = next(rec for rec in review.recommendations if rec.type == "persona_guard")
        assert guard_rec.occurrences == 3, guard_rec
        assert guard_rec.evidence_snippets, guard_rec
        print("  PASSED")
    finally:
        temp.cleanup()


def _test_evidence_is_sanitized_and_unique():
    print("[9C-A1 Smoke] Test 4: evidence strips stage direction and counts unique snippets...")
    from nana.runtime.post_stream_review import build_post_stream_review

    temp, replies, sent = _make_temp_dataset()
    try:
        for index in range(3):
            _write_json(
                sent / f"starter{index}.json",
                {
                    **_outbox_payload(index),
                    "text": "(Nana nghiêng đầu, nhìn vào màn hình) Mọi người đang làm gì vậy?",
                },
            )
        review = build_post_stream_review(replies_dir=replies, outbox_sent_dir=sent)
        rec = next(item for item in review.recommendations if item.type == "proactive_starter")
        assert rec.evidence_count == 1, rec
        assert len(rec.evidence_snippets) == 1, rec
        assert "[outbox][stage_dir_stripped]" in rec.evidence_snippets[0], rec
        assert "Nana nghiêng đầu" not in rec.evidence_snippets[0], rec
        print("  PASSED")
    finally:
        temp.cleanup()


def _test_reply_density_evidence_prefers_organic_turns():
    print("[9C-A1 Smoke] Test 5: reply density evidence prefers organic turns over quiet-room probes...")
    from nana.runtime.post_stream_review import build_post_stream_review

    temp, replies, sent = _make_temp_dataset()
    try:
        quiet_filter = {
            "kind": "normal",
            "learning_eligible": True,
            "learning_weight": 1.0,
            "noise_score": 0.0,
            "reasons": ["quiet_room_probe"],
        }
        for index in range(3):
            _write_json(
                replies / f"quiet{index}.json",
                _reply_payload(
                    request_id=f"quiet-{index}",
                    social_event="quiet_room_probe",
                    reply_text=f"Phòng hơi lặng rồi đó. Nana rải một mồi nhỏ #{index}.",
                    public_memory_filter=quiet_filter,
                ),
            )
        for index in range(3):
            _write_json(
                replies / f"organic{index}.json",
                _reply_payload(
                    request_id=f"organic-{index}",
                    reply_text=f"Nana kể tiếp chuyện ngáo trong phòng hôm nay #{index}.",
                ),
            )
        review = build_post_stream_review(replies_dir=replies, outbox_sent_dir=sent)
        rec = next(item for item in review.recommendations if item.type == "reply_density")
        evidence = "\n".join(rec.evidence_snippets)
        assert "chuyện ngáo" in evidence, rec
        assert "Phòng hơi lặng" not in evidence, rec
        print("  PASSED")
    finally:
        temp.cleanup()


def _test_correlation_warning_when_evidence_overlaps():
    print("[9C-A1 Smoke] Test 6: overlapping evidence creates correlation warnings...")
    from nana.runtime.post_stream_review import build_post_stream_review

    temp, replies, sent = _make_temp_dataset()
    try:
        for index in range(4):
            _write_json(
                replies / f"emoji{index}.json",
                _reply_payload(
                    request_id=f"emoji-{index}",
                    action="ack_only",
                    social_event="emoji_only",
                    avatar_event="emoji_only",
                    reply_text="Nana thấy rồi, dễ thương đó nha.",
                ),
            )
        review = build_post_stream_review(replies_dir=replies, outbox_sent_dir=sent)
        correlated = [rec for rec in review.recommendations if rec.correlation_warning]
        assert len(correlated) >= 2, review
        assert any("share more than 50%" in item for item in review.observations), review.observations
        print("  PASSED")
    finally:
        temp.cleanup()


def _test_empty_review_is_safe():
    print("[9C-A1 Smoke] Test 7: empty review is safe and read-only...")
    from nana.runtime.post_stream_review import build_post_stream_review

    temp, replies, sent = _make_temp_dataset()
    try:
        replies.mkdir(parents=True, exist_ok=True)
        sent.mkdir(parents=True, exist_ok=True)
        review = build_post_stream_review(replies_dir=replies, outbox_sent_dir=sent)
        assert review.recommendations == (), review
        assert review.can_act is False, review
        assert review.auto_apply is False, review
        assert review.memory_write is False, review
        assert "No structured session artifacts" in "\n".join(review.observations), review
        print("  PASSED")
    finally:
        temp.cleanup()


def _test_status_and_review_lines_are_safe():
    print("[9C-A1 Smoke] Test 8: status/review lines expose recommendation-only safety...")
    from nana.runtime.post_stream_review import post_stream_review_lines, post_stream_review_status_lines

    status = "\n".join(post_stream_review_status_lines())
    review = "\n".join(post_stream_review_lines())
    assert "STAGE-9C-A1" in status, status
    assert "recommendation_only" in status, status
    assert "auto_apply=False" in status, status
    assert "nothing is applied automatically" in review, review
    assert "no write" in review, review
    print("  PASSED")


def _test_stage_help_and_firewall():
    print("[9C-A1 Smoke] Test 9: stage/help/firewall mention post-stream review...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Post-stream review:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/post-stream-review" in help_output, help_output

    guard = get_public_stage_identity_guard()
    for command in ("/post-stream-review", "/post-stream-review-status"):
        assert guard.classify_public_input(command) == "backstage_command", command
    print("  PASSED")


def run_smoke_tests() -> bool:
    print("=" * 60)
    print("STAGE-9C-A1 Post-stream Review — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_generates_recommendations_with_evidence,
        _test_single_occurrence_is_observation_only,
        _test_guard_issues_recommendation_requires_three,
        _test_evidence_is_sanitized_and_unique,
        _test_reply_density_evidence_prefers_organic_turns,
        _test_correlation_warning_when_evidence_overlaps,
        _test_empty_review_is_safe,
        _test_status_and_review_lines_are_safe,
        _test_stage_help_and_firewall,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as exc:
            print(f"  FAILED: {exc}")
            failed += 1
        except Exception as exc:
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            failed += 1
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_smoke_tests() else 1)
