"""Phase 2 controlled retrieval smoke tests.

The harness is fake-only: records are in-memory dictionaries and the semantic
adapter is an injected test double.  No provider, network, or production
memory path is touched.
"""

from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


NOW = 1_700_000_000.0
PRIVATE_SENTINEL = "PRIVATE_OWNER_SENTINEL_NEVER_PUBLIC"


def _record(
    item_id: str,
    text: str,
    *,
    lane: str = "private",
    actor_key: str = "",
    room_id: str = "",
    source_event_id: str = "event-1",
    confidence: float = 0.9,
    created_at: float = NOW - 60,
    expires_at: float | None = None,
    tags: list[str] | None = None,
    verified: bool = True,
    consent: bool = True,
    item_type: str = "project_fact",
    source: str = "user",
    pinned: bool = False,
) -> dict:
    value = {
        "id": item_id,
        "type": item_type,
        "text": text,
        "source": source,
        "source_event_id": source_event_id,
        "confidence": confidence,
        "importance": "high",
        "created_at": created_at,
        "updated_at": created_at,
        "tags": list(tags or []),
        "pinned": pinned,
        "lane": lane,
        "actor_key": actor_key,
        "scope": {
            "platform": "youtube",
            "room_id": room_id,
            "verified": verified,
            "consent": consent,
        },
    }
    if expires_at is not None:
        value["expires_at"] = expires_at
    return value


class _FakeSemanticAdapter:
    def __init__(self, scores=None, *, error: Exception | None = None, elapsed: float = 0.0):
        self.scores = dict(scores or {})
        self.error = error
        self.elapsed = elapsed
        self.calls = 0
        self.received_ids: list[str] = []

    def score(self, query, candidates, timeout_ms=100):
        self.calls += 1
        self.received_ids = [str(item.get("id")) for item in candidates]
        if self.error:
            raise self.error
        # The implementation measures elapsed monotonic time itself; this
        # marker lets the test double advertise a deterministic timeout.
        return {"scores": self.scores, "elapsed_ms": self.elapsed}


class ControlledRetrievalContract(unittest.TestCase):
    def setUp(self):
        self.old_flag = os.environ.pop("NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED", None)
        from nana.runtime import controlled_memory_retrieval as retrieval
        self.retrieval = retrieval

    def tearDown(self):
        if self.old_flag is None:
            os.environ.pop("NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED", None)
        else:
            os.environ["NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED"] = self.old_flag

    def _scope(self, lane="private_owner", **overrides):
        values = {
            "lane": lane,
            "platform": "youtube",
            "actor_key": "youtube:viewer-7",
            "room_id": "room-main",
            "consent": True,
        }
        values.update(overrides)
        return self.retrieval.RetrievalScope.from_mapping(values)

    def test_default_off_never_calls_semantic_adapter(self):
        adapter = _FakeSemanticAdapter({"semantic-only": 0.99})
        store = {"long_term": [_record("semantic-only", "Ba thích món phở", tags=["ẩm thực"])]}
        result = self.retrieval.retrieve_memory_candidates(
            store,
            "món ăn yêu thích",
            scope=self._scope(),
            semantic_adapter=adapter,
            now=NOW,
        )
        self.assertFalse(result.semantic_used)
        self.assertEqual(adapter.calls, 0)
        # The lexical baseline remains available while the semantic adapter is
        # disabled; the important contract is that no adapter call occurred.
        self.assertEqual(result.status, "ok")

    def test_explicit_flag_enables_injected_semantic_adapter(self):
        os.environ["NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED"] = "1"
        adapter = _FakeSemanticAdapter({"semantic-only": 0.92})
        store = {"long_term": [_record("semantic-only", "Ba thích món phở", tags=["ẩm thực"])]}
        result = self.retrieval.retrieve_memory_candidates(
            store,
            "món ăn yêu thích",
            scope=self._scope(),
            semantic_adapter=adapter,
            now=NOW,
        )
        self.assertTrue(result.semantic_used)
        self.assertEqual(adapter.calls, 1)
        self.assertEqual([item.id for item in result.candidates], ["semantic-only"])

    def test_explicit_false_overrides_environment_opt_in(self):
        os.environ["NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED"] = "1"
        adapter = _FakeSemanticAdapter({"semantic-only": 0.92})
        store = {"long_term": [_record("semantic-only", "Ba thích món phở", tags=["ẩm thực"])]}
        result = self.retrieval.retrieve_memory_candidates(
            store,
            "món ăn yêu thích",
            scope=self._scope(),
            semantic_enabled=False,
            semantic_adapter=adapter,
            now=NOW,
        )
        self.assertFalse(result.semantic_used)
        self.assertEqual(adapter.calls, 0)

    def test_public_scope_requires_exact_platform_actor_room_and_consent(self):
        records = [
            _record(
                "good",
                "Viewer likes rhythm games",
                lane="public",
                actor_key="youtube:viewer-7",
                room_id="room-main",
                source_event_id="public-event-good",
            ),
            _record(
                "wrong-actor",
                "Viewer likes rhythm games",
                lane="public",
                actor_key="youtube:viewer-8",
                room_id="room-main",
                source_event_id="public-event-actor",
            ),
            _record(
                "wrong-room",
                "Viewer likes rhythm games",
                lane="public",
                actor_key="youtube:viewer-7",
                room_id="room-other",
                source_event_id="public-event-room",
            ),
            _record(
                "wrong-platform",
                "Viewer likes rhythm games",
                lane="public",
                actor_key="twitch:viewer-7",
                room_id="room-main",
                source_event_id="public-event-platform",
            ),
            _record(
                "no-consent",
                "Viewer likes rhythm games",
                lane="public",
                actor_key="youtube:viewer-7",
                room_id="room-main",
                source_event_id="public-event-no-consent",
                consent=False,
            ),
        ]
        result = self.retrieval.retrieve_memory_candidates(
            {"long_term": records},
            "viewer rhythm games",
            scope=self._scope("public_stage"),
            now=NOW,
        )
        self.assertEqual([item.id for item in result.candidates], ["good"])
        self.assertGreaterEqual(result.rejected.get("scope", 0), 3)
        self.assertGreaterEqual(result.rejected.get("consent", 0), 1)

    def test_display_name_is_not_identity(self):
        record = _record(
            "stable",
            "Viewer likes jazz",
            lane="public",
            actor_key="youtube:viewer-7",
            room_id="room-main",
            source_event_id="public-event-stable",
        )
        scope = self._scope("public_stage", display_name="A DIFFERENT DISPLAY NAME")
        result = self.retrieval.retrieve_memory_candidates(
            {"long_term": [record]}, "likes jazz", scope=scope, now=NOW
        )
        self.assertEqual([item.id for item in result.candidates], ["stable"])

    def test_private_scope_never_returns_public_or_operator_records(self):
        records = [
            _record("private", "Ba uses a private editor", source_event_id="private-event"),
            _record(
                "public",
                "Public room fact",
                lane="public",
                actor_key="youtube:viewer-7",
                room_id="room-main",
                source_event_id="public-event",
            ),
            _record(
                "operator",
                "Operator-only diagnostic",
                lane="operator_backstage",
                source_event_id="operator-event",
            ),
        ]
        result = self.retrieval.retrieve_memory_candidates(
            {"long_term": records}, "editor fact", scope=self._scope(), now=NOW
        )
        self.assertEqual([item.id for item in result.candidates], ["private"])
        self.assertNotIn("PRIVATE", " ".join(item.text for item in result.candidates))

    def test_invalid_provenance_secret_ephemeral_low_confidence_and_expired_are_rejected(self):
        records = [
            _record("missing-provenance", "Ba likes tea", source_event_id=""),
            _record("secret", "api_key=do-not-return", source_event_id="secret-event"),
            _record("ephemeral", "Ba likes tea", source_event_id="ephemeral-event", item_type="ephemeral"),
            _record("low-confidence", "Ba likes tea", source_event_id="low-event", confidence=0.2),
            _record("expired", "Ba likes tea", source_event_id="expired-event", expires_at=NOW - 1),
            _record("good", "Ba likes tea", source_event_id="good-event"),
        ]
        result = self.retrieval.retrieve_memory_candidates(
            {"long_term": records}, "Ba likes tea", scope=self._scope(), now=NOW
        )
        self.assertEqual([item.id for item in result.candidates], ["good"])
        for key in ("provenance", "secret", "ephemeral", "confidence", "expired"):
            self.assertGreaterEqual(result.rejected.get(key, 0), 1, key)

    def test_semantic_failure_and_timeout_fall_back_to_lexical(self):
        os.environ["NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED"] = "1"
        store = {
            "long_term": [
                _record("lexical", "Ba thích cà phê", source_event_id="lexical-event", tags=["cà phê"]),
                _record("semantic", "Ba thích trà", source_event_id="semantic-event", tags=["trà"]),
            ]
        }
        failing = _FakeSemanticAdapter(error=RuntimeError("provider unavailable"))
        failed = self.retrieval.retrieve_memory_candidates(
            store, "cà phê", scope=self._scope(), semantic_adapter=failing, now=NOW
        )
        self.assertTrue(failed.fallback_used)
        self.assertEqual([item.id for item in failed.candidates], ["lexical"])

        timed = _FakeSemanticAdapter({"semantic": 0.99}, elapsed=9999)
        timeout = self.retrieval.retrieve_memory_candidates(
            store,
            "cà phê",
            scope=self._scope(),
            semantic_adapter=timed,
            timeout_ms=10,
            now=NOW,
        )
        self.assertTrue(timeout.fallback_used)
        self.assertEqual([item.id for item in timeout.candidates], ["lexical"])

    def test_public_lane_never_invokes_semantic_adapter(self):
        os.environ["NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED"] = "1"
        adapter = _FakeSemanticAdapter({"good": 1.0})
        record = _record(
            "good",
            "Viewer likes music",
            lane="public",
            actor_key="youtube:viewer-7",
            room_id="room-main",
            source_event_id="public-event",
        )
        result = self.retrieval.retrieve_memory_candidates(
            {"long_term": [record]},
            "unrelated paraphrase",
            scope=self._scope("public_stage"),
            semantic_adapter=adapter,
            now=NOW,
        )
        self.assertEqual(adapter.calls, 0)
        self.assertFalse(result.semantic_used)
        self.assertEqual(result.status, "empty")

    def test_status_question_is_read_only_and_makes_no_adapter_call(self):
        os.environ["NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED"] = "1"
        adapter = _FakeSemanticAdapter({"fact": 1.0})
        record = _record("fact", "Ba likes tea", source_event_id="fact-event")
        result = self.retrieval.retrieve_memory_candidates(
            {"long_term": [record]},
            "memory save status?",
            scope=self._scope(),
            semantic_adapter=adapter,
            now=NOW,
        )
        self.assertEqual(adapter.calls, 0)
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.candidates, ())

    def test_top_k_and_total_character_budget_are_bounded_and_deterministic(self):
        records = [
            _record(f"item-{i}", "Ba likes tea " + ("x" * 40), source_event_id=f"event-{i}")
            for i in range(5)
        ]
        first = self.retrieval.retrieve_memory_candidates(
            {"long_term": records},
            "Ba likes tea",
            scope=self._scope(),
            limit=2,
            max_chars=55,
            now=NOW,
        )
        second = self.retrieval.retrieve_memory_candidates(
            {"long_term": copy.deepcopy(records)},
            "Ba likes tea",
            scope=self._scope(),
            limit=2,
            max_chars=55,
            now=NOW,
        )
        self.assertLessEqual(len(first.candidates), 2)
        self.assertLessEqual(sum(len(item.text) for item in first.candidates), 55)
        self.assertEqual([item.id for item in first.candidates], [item.id for item in second.candidates])

    def test_retrieval_does_not_mutate_store(self):
        record = _record("stable", "Ba likes tea", source_event_id="stable-event")
        store = {"long_term": [record]}
        before = copy.deepcopy(store)
        self.retrieval.retrieve_memory_candidates(
            store, "Ba likes tea", scope=self._scope(), now=NOW
        )
        self.assertEqual(store, before)


if __name__ == "__main__":
    unittest.main()
