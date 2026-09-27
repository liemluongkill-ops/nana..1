"""STAGE-9C-B smoke tests: owner-approved post-stream lessons.

No LLM, no config writes, no auto-apply, no TTS/VTS/OBS/Discord/game.
Memory writes are isolated to memory["post_stream_lessons"].
"""

from __future__ import annotations

import io
import importlib
import json
import sys
import tempfile
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _reply_payload(index: int, *, action: str = "ack_only", reply_text: str | None = None) -> dict:
    return {
        "request_id": f"reply-{index}",
        "ok": True,
        "status": "ok",
        "reply_text": reply_text or f"Nana thấy rồi, dễ thương đó nha #{index}.",
        "metadata": {
            "source": "discord",
            "social_session": {
                "action": action,
                "reason": "smoke",
                "event_type": "emoji_only",
                "room_vibe": "quiet_room",
                "director_mode": "reactor",
                "response_shape": "short_reply",
            },
            "avatar_event": {
                "event": "emoji_only",
                "source": "discord",
                "viewer_name": "linhcute2746",
            },
            "public_quality": {"actions": ["clean"], "violations": []},
            "stage_identity": {"actions": ["passed"], "violations": [], "firewalled": False},
        },
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
    replies = root / "replies"
    sent = root / "outbox" / "sent"
    for index in range(8):
        _write_json(replies / f"ack{index}.json", _reply_payload(index))
    for index in range(4):
        _write_json(sent / f"starter{index}.json", _outbox_payload(index))
    return temp, replies, sent


@contextmanager
def _isolated_memory_and_review():
    memory_mod = importlib.import_module("nana.memory")
    lessons = importlib.import_module("nana.runtime.post_stream_lessons")

    temp, replies, sent = _make_temp_dataset()
    original_memory = memory_mod.memory
    fake_memory = memory_mod._merge_defaults({})
    try:
        memory_mod.memory = fake_memory
        lessons.memory = fake_memory
        with patch("nana.runtime.post_stream_review.DEFAULT_REPLIES_DIR", replies), patch(
            "nana.runtime.post_stream_review.DEFAULT_OUTBOX_SENT_DIR", sent
        ), patch("nana.runtime.session_review_adapter.DEFAULT_REPLIES_DIR", replies), patch(
            "nana.runtime.session_review_adapter.DEFAULT_OUTBOX_SENT_DIR", sent
        ), patch("nana.runtime.post_stream_lessons.save_memory_async", lambda: None):
            yield fake_memory
    finally:
        lessons.memory = original_memory
        memory_mod.memory = original_memory
        temp.cleanup()


def _first_recommendation_id(min_confidence: float = 0.0) -> str:
    from nana.runtime.post_stream_lessons import list_recommendation_payloads

    for rec in list_recommendation_payloads():
        if float(rec.get("confidence") or 0.0) >= min_confidence:
            return str(rec["recommendation_id"])
    raise AssertionError("No recommendation found")


def _first_low_confidence_recommendation_id() -> str:
    from nana.runtime.post_stream_lessons import list_recommendation_payloads

    for rec in list_recommendation_payloads():
        if float(rec.get("confidence") or 0.0) < 0.5:
            return str(rec["recommendation_id"])
    raise AssertionError("No low-confidence recommendation found")


def _test_recommendations_surface_ids():
    print("[9C-B Smoke] Test 1: recommendation surface exposes stable ids...")
    with _isolated_memory_and_review():
        from nana.runtime.post_stream_lessons import recommendation_lines, list_recommendation_payloads

        recs = list_recommendation_payloads()
        assert recs, recs
        assert all(rec.get("recommendation_id") for rec in recs), recs
        lines = "\n".join(recommendation_lines())
        assert "STAGE-9C-B" in lines, lines
        assert "/post-stream-approve" in lines, lines
    print("  PASSED")


def _test_low_confidence_requires_override():
    print("[9C-B Smoke] Test 2: low-confidence approval requires explicit override...")
    with _isolated_memory_and_review():
        from nana.runtime.post_stream_lessons import approve_recommendation

        rec_id = _first_low_confidence_recommendation_id()
        result = approve_recommendation(rec_id)
        assert result.ok is False, result
        assert result.reason == "low_confidence_requires_override", result
        override = approve_recommendation(rec_id, override_low_confidence=True)
        assert override.ok is True, override
        assert override.memory_write is True, override
    print("  PASSED")


def _test_approve_writes_lesson_lane_only():
    print("[9C-B Smoke] Test 3: approve writes lesson lane only...")
    with _isolated_memory_and_review() as mem:
        from nana.runtime.post_stream_lessons import approve_recommendation, lesson_snapshot

        before_mood = dict(mem.get("mood_continuity") or {})
        before_persona = dict(mem.get("persona") or {})
        rec_id = _first_recommendation_id(0.5)
        result = approve_recommendation(rec_id)
        assert result.ok is True, result
        snap = lesson_snapshot()
        assert len(snap["active_lessons"]) == 1, snap
        lesson = snap["active_lessons"][0]
        assert lesson["source_recommendation_id"] == result.recommendation_id, lesson
        assert lesson["review_after"] > lesson["applied_at"], lesson
        assert lesson["relevance_decay_days"] == 45, lesson
        assert lesson["revoked"] is False, lesson
        assert mem.get("mood_continuity") == before_mood, mem.get("mood_continuity")
        assert mem.get("persona") == before_persona, mem.get("persona")
    print("  PASSED")


def _test_reject_writes_dismissed_audit():
    print("[9C-B Smoke] Test 4: reject writes dismissed audit...")
    with _isolated_memory_and_review():
        from nana.runtime.post_stream_lessons import reject_recommendation, lesson_snapshot

        rec_id = _first_recommendation_id()
        result = reject_recommendation(rec_id, reason="not_useful")
        assert result.ok is True, result
        snap = lesson_snapshot()
        assert len(snap["dismissed"]) == 1, snap
        assert snap["dismissed"][0]["reason"] == "not_useful", snap
        assert snap["audit_log"][-1]["action"] == "rejected", snap["audit_log"]
    print("  PASSED")


def _test_undo_revokes_without_deleting_audit():
    print("[9C-B Smoke] Test 5: undo revokes lesson without deleting audit...")
    with _isolated_memory_and_review():
        from nana.runtime.post_stream_lessons import approve_recommendation, revoke_lesson, lesson_snapshot

        rec_id = _first_recommendation_id(0.5)
        approved = approve_recommendation(rec_id)
        assert approved.ok, approved
        before = lesson_snapshot()
        audit_before = len(before["audit_log"])
        result = revoke_lesson(approved.lesson_id, reason="changed_mind")
        assert result.ok is True, result
        after = lesson_snapshot()
        assert len(after["active_lessons"]) == 0, after
        assert len(after["revoked_lessons"]) == 1, after
        assert after["revoked_lessons"][0]["revoke_reason"] == "changed_mind", after
        assert len(after["audit_log"]) == audit_before + 1, after["audit_log"]
        assert after["audit_log"][-1]["action"] == "revoked", after["audit_log"]
    print("  PASSED")


def _test_status_history_stale_and_stage_help_firewall():
    print("[9C-B Smoke] Test 6: status/history/stage/help/firewall surfaces exist...")
    with _isolated_memory_and_review():
        from nana.commands.help import print_command_help
        from nana.core.status import print_stage_status
        from nana.runtime.post_stream_lessons import lesson_history_lines, lesson_stale_lines, lesson_status_lines
        from nana.runtime.public_stage_identity import get_public_stage_identity_guard

        assert "STAGE-9C-B" in "\n".join(lesson_status_lines())
        assert "Lesson History" in "\n".join(lesson_history_lines())
        assert "Lesson Review Queue" in "\n".join(lesson_stale_lines())

        buf = io.StringIO()
        with redirect_stdout(buf):
            print_stage_status()
        stage = buf.getvalue()
        assert "Post-stream lessons:" in stage, stage

        buf = io.StringIO()
        with redirect_stdout(buf):
            print_command_help()
        help_output = buf.getvalue()
        assert "/post-stream-approve" in help_output, help_output
        assert "/lesson-history" in help_output, help_output

        guard = get_public_stage_identity_guard()
        for command in ("/post-stream-approve abc", "/post-stream-reject abc", "/lesson-history"):
            assert guard.classify_public_input(command) == "backstage_command", command
    print("  PASSED")


def run_smoke_tests() -> bool:
    print("=" * 60)
    print("STAGE-9C-B Post-stream Lessons — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_recommendations_surface_ids,
        _test_low_confidence_requires_override,
        _test_approve_writes_lesson_lane_only,
        _test_reject_writes_dismissed_audit,
        _test_undo_revokes_without_deleting_audit,
        _test_status_history_stale_and_stage_help_firewall,
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
