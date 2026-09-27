"""Private session checkpoint smoke tests; no production data or providers."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[3]
CHECKPOINT_PATH = ROOT / "nana" / "runtime" / "session_checkpoint.py"
PERSISTENCE_PATH = ROOT / "nana" / "runtime" / "memory_persistence.py"
RABBIT = "TEMP-RABBIT-481: chú thỏ đội mũ cam ăn bánh hình sao."


def _guard(event, args):
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        path = str(args[0]).replace("\\", "/").lower()
        if "/nana/data/" in path or path.endswith("/.env"):
            raise AssertionError("checkpoint smoke blocked production data access")
    if event in {"socket.connect", "subprocess.Popen", "os.system"}:
        raise AssertionError("checkpoint smoke blocked live process/network access")


sys.addaudithook(_guard)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


for _package_name, _package_path in (("nana", ROOT / "nana"), ("nana.runtime", ROOT / "nana/runtime")):
    _package = types.ModuleType(_package_name)
    _package.__path__ = [str(_package_path)]
    sys.modules[_package_name] = _package
checkpoint = _load("nana.runtime.checkpoint_under_test", CHECKPOINT_PATH)
persistence = _load("checkpoint_persistence_under_test", PERSISTENCE_PATH)


def _memory():
    return {
        "profile": {},
        "long_term": [{"id": "durable-1", "text": "DURABLE-SENTINEL"}],
        "short_term": [],
        "chat_log": [],
        "schema_version": 2,
    }


def _record(store, number, user_text, *, now=None, **kwargs):
    return checkpoint.record_private_turn(
        store,
        user_text=user_text,
        nana_text=f"Nana response {number}",
        event_id=f"private-{number}",
        now=float(number if now is None else now),
        **kwargs,
    )


class SessionCheckpointTests(unittest.TestCase):
    def test_compacts_at_eight_and_retains_story_after_seventeen_detours(self):
        store = _memory()
        durable_before = copy.deepcopy(store["long_term"])
        _record(store, 1, RABBIT)
        for number in range(2, 19):
            _record(store, number, f"Câu hỏi rẽ nhánh số {number} là gì?")

        state = store["session_checkpoint"]
        self.assertEqual(state["checkpoint_schema_version"], 1)
        self.assertEqual(state["compacted_through_turn"], 16)
        self.assertEqual(state["checkpoint_revision"], 18)
        self.assertTrue(any(RABBIT in item["text"] for item in state["anchors"]))
        prompt = checkpoint.format_private_checkpoint_prompt(store, now=18.0)
        self.assertIn(RABBIT, prompt)
        self.assertIn("temporary", prompt.lower())
        self.assertNotIn("DURABLE-SENTINEL", prompt)
        self.assertEqual(store["long_term"], durable_before)

    def test_duplicate_is_idempotent_and_conflicting_reuse_is_rejected(self):
        store = _memory()
        _record(store, 1, RABBIT)
        before = copy.deepcopy(store)
        result = _record(store, 1, RABBIT, now=2.0)
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(store, before)

    def test_full_bounded_ledger_does_not_evict_duplicate_ids(self):
        store = _memory()
        for number in range(1, checkpoint.MAX_EVENT_LEDGER + 1):
            result = _record(
                store,
                number,
                f"Meaningful branch detail {number} remains tracked.",
                now=float(number),
            )
            self.assertEqual(result["status"], "recorded")
        before = copy.deepcopy(store)
        duplicate = _record(
            store,
            1,
            "Meaningful branch detail 1 remains tracked.",
            now=checkpoint.MAX_EVENT_LEDGER + 1.0,
        )
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(store, before)
        capacity = _record(
            store,
            checkpoint.MAX_EVENT_LEDGER + 1,
            "A new meaningful event cannot enter a full ledger.",
            now=checkpoint.MAX_EVENT_LEDGER + 2.0,
        )
        self.assertEqual(capacity["status"], "capacity_reached")
        self.assertEqual(store, before)
        with self.assertRaises(ValueError):
            _record(store, 1, "different payload", now=3.0)
        self.assertEqual(store, before)

    def test_secret_failure_public_and_trivial_content_are_excluded(self):
        store = _memory()
        fixture_openai = "sk-" + "session-secret-481"
        fixture_password = "moon-" + "secret-742"
        fixture_github = "ghp_" + "session_private_963"
        _record(
            store,
            1,
            "Chi tiết an toàn là chiếc hộp xanh. OPENAI_API_KEY=" + fixture_openai,
        )
        _record(store, 2, "password=" + fixture_password)
        _record(store, 3, fixture_github)
        _record(store, 4, "failed payload", outcome="failed")
        _record(store, 5, "fallback payload", fallback=True)
        _record(store, 6, "public payload", lane="public_stage")
        _record(store, 7, "xin chào")
        serialized = json.dumps(store.get("session_checkpoint", {}), ensure_ascii=False)
        prompt = checkpoint.format_private_checkpoint_prompt(store, now=7.0)
        for secret in (fixture_openai, fixture_password, fixture_github):
            self.assertNotIn(secret, serialized)
            self.assertNotIn(secret, prompt)
        self.assertIn("Chi tiết an toàn là chiếc hộp xanh", prompt)
        self.assertNotIn("failed payload", serialized)
        self.assertNotIn("fallback payload", serialized)
        self.assertNotIn("public payload", serialized)
        self.assertNotIn("xin chào", serialized)

    def test_expired_malformed_and_unknown_schema_fail_closed_without_mutation(self):
        for state in (
            {"checkpoint_schema_version": 99},
            {"checkpoint_schema_version": 1, "session_id": "bad"},
        ):
            store = _memory()
            store["session_checkpoint"] = copy.deepcopy(state)
            before = copy.deepcopy(store)
            self.assertEqual(checkpoint.format_private_checkpoint_prompt(store, now=10.0), "")
            self.assertEqual(store, before)

        store = _memory()
        _record(store, 1, RABBIT)
        before = copy.deepcopy(store)
        self.assertEqual(checkpoint.format_private_checkpoint_prompt(store, now=float("nan")), "")
        self.assertEqual(store, before)

        store = _memory()
        _record(store, 1, RABBIT, now=1.0)
        before = copy.deepcopy(store)
        self.assertEqual(
            checkpoint.format_private_checkpoint_prompt(
                store,
                now=1.0 + checkpoint.CHECKPOINT_TTL_SECONDS + 1,
            ),
            "",
        )
        self.assertEqual(store, before)
        _record(store, 2, "Chi tiết mới là chiếc vé màu bạc.", now=50_000.0)
        self.assertNotEqual(store["session_checkpoint"]["session_id"], before["session_checkpoint"]["session_id"])

    def test_prompt_and_state_are_bounded_with_anchor_budget_reserved(self):
        store = _memory()
        _record(store, 1, RABBIT)
        for number in range(2, 19):
            text = (f"Câu hỏi rẽ nhánh số {number} " + "x" * 500 + "?")
            _record(store, number, text)
        state = store["session_checkpoint"]
        self.assertLessEqual(len(state["anchors"]), checkpoint.MAX_ANCHORS)
        self.assertLessEqual(len(state["pending_turns"]), checkpoint.COMPACT_EVERY_TURNS - 1)
        self.assertLessEqual(len(state["event_ledger"]), checkpoint.MAX_EVENT_LEDGER)
        prompt = checkpoint.format_private_checkpoint_prompt(store, now=40.0, max_chars=600)
        self.assertLessEqual(len(prompt), 600)
        self.assertIn("TEMP-RABBIT-481", prompt)

    def test_out_of_order_timestamp_does_not_rollback_checkpoint(self):
        store = _memory()
        _record(store, 1, RABBIT, now=100.0)
        _record(store, 2, "Chi tiết mới là chiếc vé bạc.", now=50.0)
        state = store["session_checkpoint"]
        self.assertGreaterEqual(state["updated_at"], 100.0)
        self.assertTrue(checkpoint.format_private_checkpoint_prompt(store, now=100.0))

    def test_common_secret_forms_are_redacted(self):
        store = _memory()
        secrets = (
            "Authorization: Bearer " + "fixture-bearer-value",
            "token=" + "fixture-token-value",
            "eyJ" + "hbGciOiJIUzI1NiJ9.payload.signature",
            "-----BEGIN " + "PRIVATE KEY----- ABC -----END " + "PRIVATE KEY-----",
        )
        for number, secret in enumerate(secrets, 1):
            _record(store, number, f"Chi tiết an toàn {number}: {secret}")
        raw = json.dumps(store["session_checkpoint"], ensure_ascii=False)
        prompt = checkpoint.format_private_checkpoint_prompt(store)
        for secret in secrets:
            self.assertNotIn(secret, raw)
            self.assertNotIn(secret, prompt)

    def test_evidence_contains_user_anchor_and_source_event_only(self):
        store = _memory()
        _record(store, 1, RABBIT)
        for number in range(2, 9):
            _record(store, number, f"Câu hỏi rẽ nhánh số {number}?")
        candidates = checkpoint.private_checkpoint_evidence_candidates(
            store,
            "chú thỏ đội mũ cam ăn bánh hình sao",
            now=9.0,
        )
        self.assertEqual(len(candidates), 1)
        self.assertIn(RABBIT, candidates[0]["text"])
        self.assertEqual(candidates[0]["source"], "private_session_checkpoint")
        self.assertEqual(candidates[0]["source_event_id"], "private-1")
        self.assertNotIn("Nana response", candidates[0]["text"])

    def test_open_loop_is_retained_as_anchor(self):
        store = _memory()
        _record(store, 1, "Phần grounding vẫn đang dở, câu hỏi còn lại là sao?")
        for number in range(2, 9):
            _record(store, number, f"Câu hỏi rẽ nhánh số {number}?")
        anchors = store["session_checkpoint"]["anchors"]
        self.assertTrue(any(item["kind"] == "open_loop" for item in anchors), anchors)
        self.assertIn("open_loop", checkpoint.format_private_checkpoint_prompt(store, now=9.0))

    def test_tiny_budget_fails_closed_without_slicing_data(self):
        store = _memory()
        _record(store, 1, RABBIT)
        self.assertEqual(checkpoint.format_private_checkpoint_prompt(store, now=2.0, max_chars=20), "")

    def test_anchor_retention_is_bounded_but_survives_longer_session(self):
        store = _memory()
        _record(store, 1, RABBIT)
        for number in range(2, 49):
            _record(store, number, f"Câu chuyện nhánh {number}: chi tiết đang dở.")
        state = store["session_checkpoint"]
        self.assertLessEqual(len(state["anchors"]), checkpoint.MAX_ANCHORS)
        self.assertTrue(any(RABBIT in item["text"] for item in state["anchors"]))
        self.assertIn(RABBIT, checkpoint.format_private_checkpoint_prompt(store, now=49.0))

    def test_pending_turn_is_evidence_before_first_compaction(self):
        store = _memory()
        _record(store, 1, RABBIT)
        for number in range(2, 5):
            _record(store, number, f"Câu hỏi rẽ nhánh số {number}?")
        self.assertEqual(store["session_checkpoint"]["anchors"], [])
        candidates = checkpoint.private_checkpoint_evidence_candidates(
            store,
            "chú thỏ đội mũ cam ăn bánh hình sao",
            now=5.0,
        )
        self.assertEqual(candidates[0]["source_event_id"], "private-1")
        self.assertIn(RABBIT, candidates[0]["text"])

    def test_pending_turn_supports_elliptical_recall_before_compaction(self):
        store = _memory()
        _record(store, 1, "Lúc nãy Ba kể một câu chuyện: chú thỏ đội mũ cam.")
        _record(store, 2, "Câu hỏi rẽ nhánh số 2?")
        candidates = checkpoint.private_checkpoint_evidence_candidates(
            store,
            "Lúc nãy Ba đã kể một chi tiết; con vật gì vậy?",
            now=3.0,
        )
        self.assertTrue(candidates)
        self.assertEqual(candidates[0]["source_event_id"], "private-1")

    def test_natural_recall_variants_get_checkpoint_scores(self):
        store = _memory()
        _record(store, 1, RABBIT)
        for number in range(2, 9):
            _record(store, number, f"Câu hỏi rẽ nhánh số {number}?")
        for query in (
            "Ba kể gì về chú thỏ?",
            "Nana có nhớ chi tiết chú thỏ không?",
            "Con nhớ chú thỏ có màu gì không?",
            "Tiếp tục chi tiết câu chuyện đang dở.",
            "Vừa kể gì vậy?",
        ):
            with self.subTest(query=query):
                candidates = checkpoint.private_checkpoint_evidence_candidates(
                    store, query, now=9.0
                )
                self.assertTrue(candidates, query)
                self.assertGreaterEqual(candidates[0]["match_score"], 0.75)

    def test_formatter_and_evidence_re_redact_valid_legacy_payloads(self):
        store = _memory()
        _record(store, 1, RABBIT)
        state = store["session_checkpoint"]
        state["pending_turns"][0]["user_text"] = (
            "Chi tiết an toàn là hộp xanh; password=legacy-secret-777"
        )
        state["pending_turns"][0]["nana_text"] = "Đã nghe; sk-legacy-secret-888"
        prompt = checkpoint.format_private_checkpoint_prompt(store, now=2.0)
        candidates = checkpoint.private_checkpoint_evidence_candidates(
            store,
            "chi tiết hộp xanh",
            now=2.0,
        )
        serialized = json.dumps(candidates, ensure_ascii=False)
        self.assertIn("Chi tiết an toàn là hộp xanh", prompt)
        self.assertNotIn("legacy-secret", prompt)
        self.assertNotIn("legacy-secret", serialized)

    def test_real_atomic_restart_recovery_and_legacy_compatibility(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "memory.json"
            store = _memory()
            store["custom"] = {"keep": True}
            store["session_summary"] = {"legacy": "unchanged"}
            _record(store, 1, RABBIT)
            for number in range(2, 9):
                _record(store, number, f"Câu hỏi rẽ nhánh số {number}?")
            checkpoint_revision = store["session_checkpoint"]["checkpoint_revision"]

            writer = persistence.AtomicMemoryWriter(path)
            receipt_one = writer.submit(store)
            self.assertTrue(receipt_one.committed)
            restarted = persistence.AtomicMemoryWriter(path).load()
            self.assertEqual(restarted["session_checkpoint"]["checkpoint_revision"], checkpoint_revision)
            self.assertNotEqual(receipt_one.snapshot_revision, checkpoint_revision)
            self.assertIn(RABBIT, checkpoint.format_private_checkpoint_prompt(restarted, now=9.0))
            self.assertEqual(restarted["long_term"], store["long_term"])
            self.assertEqual(restarted["session_summary"], {"legacy": "unchanged"})
            self.assertEqual(restarted["custom"], {"keep": True})

            restarted.pop("snapshot_revision", None)
            restarted["custom"] = {"keep": "second"}
            receipt_two = writer.submit(restarted)
            self.assertTrue(receipt_two.committed)
            path.write_text("{corrupt", encoding="utf-8")
            recovered_writer = persistence.AtomicMemoryWriter(path)
            recovered = recovered_writer.load()
            self.assertTrue(recovered_writer.last_receipt.recovered)
            self.assertIn(RABBIT, checkpoint.format_private_checkpoint_prompt(recovered, now=9.0))

            legacy = _memory()
            legacy["session_summary"] = {"legacy": "preserve"}
            before = copy.deepcopy(legacy)
            self.assertEqual(checkpoint.format_private_checkpoint_prompt(legacy, now=1.0), "")
            self.assertEqual(legacy, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
