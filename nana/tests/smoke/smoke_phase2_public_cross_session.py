"""Fake-only smoke tests for Memory v2 Phase 2 public cross-session recall."""

from __future__ import annotations

import copy
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


NOW = 2_000_000.0


def _record(
    item_id: str,
    text: str,
    *,
    actor: str = "youtube:viewer-1",
    room: str = "room-a",
    source: str = "public_verified",
    verified: bool = True,
    consent: bool = True,
    expires: float = NOW + 1000,
    lane: str = "public",
    confidence: float = 0.92,
    **extra,
):
    return {
        "id": item_id,
        "text": text,
        "source_event_id": "evt-" + item_id,
        "source": source,
        "verified": verified,
        "consent_recorded": consent,
        "confidence": confidence,
        "lane": lane,
        "actor_key": actor,
        "scope": {"room_id": room, "expires_at": expires},
        "created_at": NOW - 10,
        **extra,
    }


def test_exact_actor_room_consent_and_ttl():
    from nana.runtime.public_cross_session_memory import PublicRecallScope, retrieve_public_cross_session

    store = {"long_term": [_record("fact-1", "Viewer likes purple mint tea.")]}
    before = copy.deepcopy(store)
    result = retrieve_public_cross_session(
        store,
        "What drink does the viewer like? purple mint tea",
        scope=PublicRecallScope("youtube", "youtube:viewer-1", "room-a", True, NOW),
        enabled=True,
        now=NOW,
    )
    assert result.status == "found", result
    assert [item.id for item in result.candidates] == ["fact-1"], result
    assert store == before


def test_display_name_is_not_identity_and_room_is_exact():
    from nana.runtime.public_cross_session_memory import PublicRecallScope, retrieve_public_cross_session

    store = {
        "long_term": [
            _record("same-name-other-actor", "Viewer likes purple mint tea.", actor="youtube:viewer-2"),
            _record("other-room", "Viewer likes purple mint tea.", room="room-b"),
        ]
    }
    result = retrieve_public_cross_session(
        store,
        "purple mint tea",
        scope=PublicRecallScope("youtube", "youtube:viewer-1", "room-a", True, NOW),
        enabled=True,
        now=NOW,
    )
    assert result.status == "not_found", result
    assert {item["reason"] for item in result.rejected} >= {"actor_mismatch", "room_mismatch"}, result


def test_private_expired_unverified_model_and_secret_records_are_rejected():
    from nana.runtime.public_cross_session_memory import PublicRecallScope, retrieve_public_cross_session

    store = {
        "long_term": [
            _record("private", "PRIVATE_SENTINEL_NEVER_PUBLIC purple tea.", lane="private"),
            _record("expired", "purple mint tea", expires=NOW - 1),
            _record("unverified", "purple mint tea", verified=False),
            _record("model", "purple mint tea", source="model_generated"),
            _record("secret", "purple mint tea", evidence="password=FAKE_PHASE2_SECRET"),
            _record("question", "Does viewer like purple mint tea?"),
        ]
    }
    result = retrieve_public_cross_session(
        store,
        "purple mint tea",
        scope=PublicRecallScope("youtube", "youtube:viewer-1", "room-a", True, NOW),
        enabled=True,
        now=NOW,
    )
    assert not result.candidates, result
    assert "PRIVATE_SENTINEL_NEVER_PUBLIC" not in str(result.to_dict()), result
    reasons = {entry["reason"] for entry in result.rejected}
    assert {"non_public_lane", "expired", "unverified_provenance", "generated_source", "secret_material", "question_not_fact"} <= reasons, reasons


def test_disabled_and_consent_required_do_not_scan_or_return_facts():
    from nana.runtime.public_cross_session_memory import PublicRecallScope, retrieve_public_cross_session

    store = {"long_term": [_record("fact-1", "purple mint tea")]}
    disabled = retrieve_public_cross_session(
        store,
        "purple mint tea",
        scope=PublicRecallScope("youtube", "youtube:viewer-1", "room-a", True, NOW),
        enabled=False,
        now=NOW,
    )
    no_consent = retrieve_public_cross_session(
        store,
        "purple mint tea",
        scope=PublicRecallScope("youtube", "youtube:viewer-1", "room-a", False, NOW),
        enabled=True,
        now=NOW,
    )
    assert disabled.status == "disabled" and not disabled.candidates
    assert no_consent.status == "consent_required" and not no_consent.candidates


def test_gpt_public_prompt_integration_is_opt_in_and_filters_private_records():
    import nana.config as config
    import nana.brain.gpt as gpt
    from nana.runtime.persona_boundary import resolve_request_scope
    from nana.runtime.public_context_boundary import build_public_safe_snapshot

    original_flag = getattr(config, "MEMORY_PUBLIC_CROSS_SESSION_RECALL_ENABLED", False)
    original_memory = gpt.memory
    config.MEMORY_PUBLIC_CROSS_SESSION_RECALL_ENABLED = True
    gpt.memory = {
        "long_term": [{
            "id": "private-fact",
            "text": "PRIVATE_SENTINEL_NEVER_PUBLIC thích trà bạc hà màu tím.",
            "source_event_id": "evt-private-fact",
            "source": "private",
            "verified": True,
            "consent_recorded": True,
            "confidence": 0.92,
            "lane": "private",
            "actor_key": "youtube:viewer-1",
            "scope": {"room_id": "room-a", "expires_at": 4_000_000_000.0},
            "created_at": NOW - 10,
        }],
        "public_long_term": [
            _record(
                "public-fact",
                "Viewer thích trà bạc hà màu tím.",
                actor="youtube:viewer-1",
                expires=4_000_000_000.0,
            ),
        ]
    }
    metadata = {
        "platform": "youtube",
        "author_id": "viewer-1",
        "room_id": "room-a",
        "stream_session_id": "old-session",
        "event_id": "evt-current",
        "memory_consent": True,
    }
    boundary = resolve_request_scope(
        viewer_name="Renamed viewer",
        stream_mode=True,
        public_platform="youtube",
        metadata=metadata,
    )
    context = build_public_safe_snapshot(metadata, "Bạn còn nhớ viewer thích gì?", boundary.scope)
    try:
        block = gpt._public_cross_session_memory_block(
            "Bạn còn nhớ viewer thích gì?", boundary, context
        )
    finally:
        config.MEMORY_PUBLIC_CROSS_SESSION_RECALL_ENABLED = original_flag
        gpt.memory = original_memory
    assert "trà bạc hà" in block, block
    assert "PRIVATE_SENTINEL_NEVER_PUBLIC" not in block, block
    assert "old-session" not in block, block


def run_all() -> int:
    tests = [
        test_exact_actor_room_consent_and_ttl,
        test_display_name_is_not_identity_and_room_is_exact,
        test_private_expired_unverified_model_and_secret_records_are_rejected,
        test_disabled_and_consent_required_do_not_scan_or_return_facts,
        test_gpt_public_prompt_integration_is_opt_in_and_filters_private_records,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"Phase2 public recall: {len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_all())
