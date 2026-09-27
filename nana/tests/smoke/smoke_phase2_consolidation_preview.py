"""Phase 2 consolidation preview smoke tests.

This script is intentionally self-contained: it passes in-memory snapshots to
the preview module and rejects any attempt to touch Nana's production files or
external services.
"""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _record(record_id: str, text: str, event: str, **extra):
    value = {
        "id": record_id,
        "type": "project_fact",
        "text": text,
        "source": "user",
        "source_event_id": event,
        "lane": "private_owner",
        "confidence": 0.9,
        "created_at": 10.0,
        "updated_at": 10.0,
        "tags": ["hardware", "fact"],
        "platform": "youtube",
    }
    value.update(extra)
    return value


def _test_flag_is_explicitly_off_by_default():
    from nana.runtime.memory_consolidation_phase2 import build_consolidation_preview

    previous = os.environ.pop("NANA_MEMORY_CONSOLIDATION_PREVIEW_ENABLED", None)
    try:
        report = build_consolidation_preview({"long_term": [_record("x", "safe", "e-x")]})
    finally:
        if previous is not None:
            os.environ["NANA_MEMORY_CONSOLIDATION_PREVIEW_ENABLED"] = previous
    assert report.enabled is False, report
    assert report.status == "disabled", report
    assert report.actions == (), report
    assert report.memory_write is False and report.can_act is False, report


def _test_duplicate_and_correction_are_proposals_only():
    from nana.runtime.memory_consolidation_phase2 import build_consolidation_preview

    records = [
        _record("dup-keep", "Ba uses RTX 4070", "evt-1", confidence=0.95, pinned=True, updated_at=20.0),
        _record("dup-drop", "ba uses rtx 4070.", "evt-2", confidence=0.75, updated_at=19.0),
        _record("color-blue", "Ba likes blue", "evt-3", fact_key="favorite-color"),
        _record("color-red", "Ba likes red", "evt-4", fact_key="favorite-color"),
    ]
    before = copy.deepcopy(records)
    report = build_consolidation_preview({"long_term": records}, enabled=True, now=100.0)
    assert report.status == "preview_ready", report
    assert report.eligible_count == 4, report
    kinds = {action.action_type for action in report.actions}
    assert "merge_duplicate" in kinds, report.actions
    assert "review_correction" in kinds, report.actions
    duplicate = next(action for action in report.actions if action.action_type == "merge_duplicate")
    assert duplicate.keep_id == "dup-keep", duplicate
    assert set(duplicate.candidate_ids) == {"dup-keep", "dup-drop"}, duplicate
    correction = next(action for action in report.actions if action.action_type == "review_correction")
    assert set(correction.candidate_ids) == {"color-blue", "color-red"}, correction
    assert report.after_digest != report.before_digest, report
    assert report.rollback_export["apply_supported"] is False, report.rollback_export
    assert {item["id"] for item in report.rollback_export["records"]} == {
        "dup-keep",
        "dup-drop",
    }, report.rollback_export
    assert records == before, "preview mutated caller-owned snapshot"
    assert report.read_only and not report.memory_write and not report.can_act


def _test_rejection_firewall():
    from nana.runtime.memory_consolidation_phase2 import build_consolidation_preview

    records = [
        "legacy string",
        _record("no-event", "safe fact", "",),
        _record("low-confidence", "safe fact", "evt-low", confidence=0.1),
        _record("secret", "password=FAKE_ONLY_NOT_REAL", "evt-secret"),
        _record("question", "What is Ba GPU?", "evt-question"),
        _record("model", "assistant-generated answer", "evt-model", source="assistant"),
        _record("temp", "temporary note", "evt-temp", type="ephemeral"),
        _record("session", "session note", "evt-session", decay_policy="session"),
        _record("expired", "old fact", "evt-expired", expires_at=10.0),
        _record("bad-public", "public fact", "evt-public", lane="public", room_id="room-a"),
    ]
    report = build_consolidation_preview(records, enabled=True, now=100.0)
    reasons = {item.reason for item in report.rejected}
    assert report.eligible_count == 0, report
    assert {
        "missing_provenance",
        "low_confidence",
        "secret_detected",
        "question_or_unconfirmed",
        "model_generated",
        "temporary_or_session",
        "expired",
        "public_scope_missing",
    } <= reasons, reasons
    assert all("FAKE_ONLY" not in str(item.to_dict()) for item in report.rejected)


def _test_scope_boundary_prevents_cross_lane_merge():
    from nana.runtime.memory_consolidation_phase2 import build_consolidation_preview

    records = [
        _record("private", "same fact", "evt-private", lane="private_owner"),
        _record("operator", "same fact", "evt-operator", lane="operator_backstage"),
        _record(
            "public-a",
            "same fact",
            "evt-public-a",
            lane="public",
            actor_key="youtube:author-a",
            room_id="room-a",
            verified=True,
            consent_recorded=True,
        ),
        _record(
            "public-b",
            "same fact",
            "evt-public-b",
            lane="public",
            actor_key="youtube:author-a",
            room_id="room-b",
            verified=True,
            consent_recorded=True,
        ),
    ]
    report = build_consolidation_preview(records, enabled=True, now=100.0)
    assert not any(len(action.candidate_ids) > 1 for action in report.actions), report.actions


def _test_repeat_is_deterministic_and_status_is_read_only():
    from nana.runtime.memory_consolidation_phase2 import (
        build_consolidation_preview,
        consolidation_preview_lines,
        consolidation_preview_status_lines,
    )

    records = [
        _record("a", "same", "evt-a", updated_at=2.0),
        _record("b", "same", "evt-b", updated_at=1.0),
    ]
    first = build_consolidation_preview({"long_term": records}, enabled=True, now=100.0)
    second = build_consolidation_preview({"long_term": records}, enabled=True, now=100.0)
    assert first.to_dict() == second.to_dict(), (first.to_dict(), second.to_dict())
    status = "\n".join(consolidation_preview_status_lines(first))
    preview = "\n".join(consolidation_preview_lines(first))
    assert "preview-only" in status and "read_only=True" in status, status
    assert "apply_supported=False" in preview, preview
    assert "memory_write=False" in status, status


def _test_bound_limits_are_reported_without_mutation():
    from nana.runtime.memory_consolidation_phase2 import build_consolidation_preview

    records = [_record(str(index), f"fact-{index}", f"evt-{index}") for index in range(4)]
    original = copy.deepcopy(records)
    report = build_consolidation_preview(records, enabled=True, max_records=2, now=100.0)
    assert report.eligible_count == 2, report
    assert any(item.reason == "limit_exceeded" for item in report.rejected), report.rejected
    assert records == original


def run_all() -> int:
    print("=" * 64)
    print("MEMORY-V2 Phase 2 Consolidation Preview — Smoke Tests")
    print("=" * 64)
    tests = [
        _test_flag_is_explicitly_off_by_default,
        _test_duplicate_and_correction_are_proposals_only,
        _test_rejection_firewall,
        _test_scope_boundary_prevents_cross_lane_merge,
        _test_repeat_is_deterministic_and_status_is_read_only,
        _test_bound_limits_are_reported_without_mutation,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL: {test.__name__}: {type(exc).__name__}: {exc}")
    print("=" * 64)
    print(f"Results: {len(tests) - failed} passed, {failed} failed")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_all())
