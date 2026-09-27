"""STAGE-9C-A0 smoke tests: session review data adapter.

Metrics-only. No recommendations, no LLM, no memory/config writes, no TTS/VTS,
OBS, Discord, or game input.
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
    request_id: str = "req-1",
    action: str = "full_reply",
    social_event: str = "message",
    avatar_event: str = "message",
    room_vibe: str = "quiet_room",
    director_mode: str = "answer",
    response_shape: str = "short_reply",
    reply_text: str = "Nana nghe đây nè.",
    quality_violations: list | None = None,
    stage_violations: list | None = None,
) -> dict:
    return {
        "request_id": request_id,
        "ok": True,
        "status": "ok",
        "reply_text": reply_text,
        "metadata": {
            "source": "discord",
            "event_type": "message",
            "social_session": {
                "action": action,
                "reason": "smoke",
                "event_type": social_event,
                "chat_velocity": 0.05,
                "room_vibe": room_vibe,
                "director_mode": director_mode,
                "response_shape": response_shape,
            },
            "avatar_event": {
                "event": avatar_event,
                "source": "discord",
                "viewer_name": "linhcute2746",
                "channel": "chung",
                "created_at": 1234.0,
            },
            "viewer_queue": {"viewer_name": "linhcute2746"},
            "public_quality": {
                "actions": ["clean"],
                "violations": quality_violations or [],
            },
            "stage_identity": {
                "actions": ["passed"],
                "firewalled": bool(stage_violations),
                "violations": stage_violations or [],
            },
        },
    }


def _outbox_payload() -> dict:
    return {
        "event_id": "starter-1",
        "proposal_id": "proposal-1",
        "text": "Ưm... phòng mình yên quá nhỉ?",
        "channel_id": "1518954968240095245",
        "channel_name": "chung",
        "source": "starter_proposal",
        "phase": "STAGE-8F",
        "created_at": 1235.0,
        "stream_state": "live_idle",
        "interaction_tone": "quiet",
        "auto_send": False,
        "approved_by_owner": True,
    }


def _make_temp_dataset():
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    replies = root / "replies"
    sent = root / "outbox" / "sent"
    return temp, replies, sent


def _test_parse_reply_event_schema():
    print("[9C-A0 Smoke] Test 1: parse reply json into SessionEvent...")
    from nana.runtime.session_review_adapter import parse_reply_file

    temp, replies, _sent = _make_temp_dataset()
    try:
        path = replies / "reply1.json"
        _write_json(path, _reply_payload(action="ack_only", social_event="emoji_only", avatar_event="emoji_only"))
        event, error = parse_reply_file(path)
        assert error == "", error
        assert event is not None, error
        assert event.social_action == "ack_only", event
        assert event.social_event_type == "emoji_only", event
        assert event.avatar_event == "emoji_only", event
        assert event.viewer_name == "linhcute2746", event
        assert event.missing_fields == (), event
        print("  PASSED")
    finally:
        temp.cleanup()


def _test_parse_outbox_event_schema():
    print("[9C-A0 Smoke] Test 2: parse sent outbox json into OutboxEvent...")
    from nana.runtime.session_review_adapter import parse_outbox_file

    temp, _replies, sent = _make_temp_dataset()
    try:
        path = sent / "starter-1.json"
        _write_json(path, _outbox_payload())
        event, error = parse_outbox_file(path)
        assert error == "", error
        assert event is not None, error
        assert event.event_id == "starter-1", event
        assert event.proposal_id == "proposal-1", event
        assert event.channel_name == "chung", event
        assert event.approved_by_owner is True, event
        print("  PASSED")
    finally:
        temp.cleanup()


def _test_metrics_are_deterministic_and_supported_only():
    print("[9C-A0 Smoke] Test 3: compute deterministic metrics from supported fields...")
    from nana.runtime.session_review_adapter import build_review_metrics

    temp, replies, sent = _make_temp_dataset()
    try:
        _write_json(replies / "full.json", _reply_payload(action="full_reply", social_event="message"))
        _write_json(replies / "ack.json", _reply_payload(action="ack_only", social_event="emoji_only", avatar_event="emoji_only"))
        _write_json(replies / "skip.json", _reply_payload(action="skip", social_event="sticker", avatar_event="sticker"))
        _write_json(sent / "starter.json", _outbox_payload())
        _dataset, metrics = build_review_metrics(replies_dir=replies, outbox_sent_dir=sent)
        assert metrics.reply_events == 3, metrics
        assert metrics.outbox_events == 1, metrics
        assert metrics.action_counts["full_reply"] == 1, metrics
        assert metrics.action_counts["ack_only"] == 1, metrics
        assert metrics.action_counts["skip"] == 1, metrics
        assert metrics.full_reply_ratio == 0.333, metrics
        assert metrics.ack_ratio == 0.333, metrics
        assert metrics.skip_ratio == 0.333, metrics
        assert metrics.emoji_sticker_ratio == 0.667, metrics
        assert metrics.supported_metrics["social_session"] is True, metrics
        assert metrics.recommendations_emitted == 0, metrics
        print("  PASSED")
    finally:
        temp.cleanup()


def _test_corrupt_json_is_counted_not_crashing():
    print("[9C-A0 Smoke] Test 4: corrupt json is counted, not fatal...")
    from nana.runtime.session_review_adapter import build_review_metrics

    temp, replies, sent = _make_temp_dataset()
    try:
        replies.mkdir(parents=True, exist_ok=True)
        (replies / "bad.json").write_text("{not json", encoding="utf-8")
        _write_json(sent / "starter.json", _outbox_payload())
        dataset, metrics = build_review_metrics(replies_dir=replies, outbox_sent_dir=sent)
        assert metrics.corrupt_files == 1, metrics
        assert len(dataset.corrupt_files) == 1, dataset
        assert metrics.outbox_events == 1, metrics
        print("  PASSED")
    finally:
        temp.cleanup()


def _test_missing_fields_become_unknown():
    print("[9C-A0 Smoke] Test 5: missing fields become unknown/default...")
    from nana.runtime.session_review_adapter import build_review_metrics

    temp, replies, sent = _make_temp_dataset()
    try:
        _write_json(replies / "legacy.json", {"request_id": "legacy", "ok": True})
        _write_json(sent / "legacy_outbox.json", {"event_id": "starter-legacy"})
        dataset, metrics = build_review_metrics(replies_dir=replies, outbox_sent_dir=sent)
        assert metrics.reply_events == 1, metrics
        assert metrics.outbox_events == 1, metrics
        assert metrics.missing_field_files == 2, metrics
        assert dataset.reply_events[0].social_action == "unknown", dataset.reply_events[0]
        assert dataset.outbox_events[0].proposal_id == "unknown", dataset.outbox_events[0]
        print("  PASSED")
    finally:
        temp.cleanup()


def _test_empty_session_has_zero_metrics_no_recommendations():
    print("[9C-A0 Smoke] Test 6: empty session is safe and emits no recommendations...")
    from nana.runtime.session_review_adapter import build_review_metrics

    temp, replies, sent = _make_temp_dataset()
    try:
        replies.mkdir(parents=True, exist_ok=True)
        sent.mkdir(parents=True, exist_ok=True)
        _dataset, metrics = build_review_metrics(replies_dir=replies, outbox_sent_dir=sent)
        assert metrics.reply_events == 0, metrics
        assert metrics.outbox_events == 0, metrics
        assert metrics.full_reply_ratio is None, metrics
        assert metrics.recommendations_emitted == 0, metrics
        assert metrics.can_act is False, metrics
        print("  PASSED")
    finally:
        temp.cleanup()


def _test_status_and_preview_are_read_only():
    print("[9C-A0 Smoke] Test 7: status/preview surface is read-only and recommendation-free...")
    from nana.runtime.session_review_adapter import session_review_preview_lines, session_review_status_lines

    status = "\n".join(session_review_status_lines())
    preview = "\n".join(session_review_preview_lines())
    assert "STAGE-9C-A0" in status, status
    assert "metrics-only" in status, status
    assert "recommendations=0" in status, status
    assert "no LLM" in preview, preview
    assert "no auto-apply" in preview, preview
    print("  PASSED")


def _test_stage_help_and_firewall():
    print("[9C-A0 Smoke] Test 8: stage/help/firewall mention session review...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Session review adapter:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/session-review-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    for command in (
        "/session-review-status",
        "/session-review-preview",
        "/post-stream-review-status",
        "/post-stream-review-preview",
        "/post-stream-review",
    ):
        assert guard.classify_public_input(command) == "backstage_command", command
    print("  PASSED")


def run_smoke_tests() -> bool:
    print("=" * 60)
    print("STAGE-9C-A0 Session Review Data Adapter — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_parse_reply_event_schema,
        _test_parse_outbox_event_schema,
        _test_metrics_are_deterministic_and_supported_only,
        _test_corrupt_json_is_counted_not_crashing,
        _test_missing_fields_become_unknown,
        _test_empty_session_has_zero_metrics_no_recommendations,
        _test_status_and_preview_are_read_only,
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
