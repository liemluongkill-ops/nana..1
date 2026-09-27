"""History privacy regression tests; real temp files, no production runtime."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]


def _guard(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        value = str(args[0]).replace("\\", "/").lower()
        if "/nana/data/" in value or value.endswith("/.env") or value.endswith("/nana/config.py"):
            raise AssertionError("history smoke blocked production data/config access")
    if event in {"socket.connect", "subprocess.Popen", "os.system"}:
        raise AssertionError("history smoke blocked network/process access")


sys.addaudithook(_guard)
package = types.ModuleType("nana")
package.__path__ = [str(ROOT / "nana")]
runtime = types.ModuleType("nana.runtime")
runtime.__path__ = [str(ROOT / "nana" / "runtime")]


class HistoryPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memory.json"
        self.chat_path = self.path.parent / "chat_history.txt"
        config = types.ModuleType("nana.config")
        config.MEMORY_PATH = self.path
        config.CHAT_HISTORY_PATH = self.chat_path
        self.modules = patch.dict(sys.modules, {
            "nana": package, "nana.runtime": runtime, "nana.config": config,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        policy_path = ROOT / "nana/runtime/history_privacy.py"
        self.assertTrue(policy_path.exists(), "shared history privacy policy is not implemented")
        from nana.runtime import history_privacy
        self.policy = history_privacy
        spec = importlib.util.spec_from_file_location("nana._history_privacy_test_memory", ROOT / "nana/memory.py")
        self.memory = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.memory)

    def test_redacts_natural_secret_clauses_and_typed_credentials(self):
        jwt_fixture = "eyJ" + "hbGciOiJIUzI1NiJ9.eyJzdWIiOiJmaXh0dXJlIn0.Zml4dHVyZVNpZ25hdHVyZQ"
        private_key_body = "QUJD" + "REVGR0hJSktM"
        private_key_fixture = "Trước\n-----BEGIN " + "PRIVATE KEY-----\n" + private_key_body + "\n-----END " + "PRIVATE KEY-----\nSau"
        cases = [
            ("Mật khẩu dự phòng của tôi là ChimCam-2026. Đừng nhắc lại.", "ChimCam-2026"),
            ("Nhớ kỹ: mật khẩu email của ba là Blue rabbit with stars", "Blue rabbit with stars"),
            ("password='hello world'", "hello world"),
            ("password:\nMultilineFixture", "MultilineFixture"),
            ('password="first fixture\nsecond fixture"; còn câu chuyện', "second fixture"),
            ("token=opaque-token.fixture", "opaque-token.fixture"),
            ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz", "abcdefghijklmnopqrstuvwxyz"),
            ("api_key: sk-fixtureOnly1234567890", "sk-fixtureOnly1234567890"),
            ("Dữ liệu " + jwt_fixture, jwt_fixture.split(".", 1)[0]),
            (private_key_fixture, private_key_body),
        ]
        for source, forbidden in cases:
            with self.subTest(source=source[:25]):
                result = self.policy.redact_history_text(source)
                self.assertNotIn(forbidden, result)
                self.assertIn("[redacted", result)
                self.assertEqual(self.policy.redact_history_text(result), result)
                self.assertTrue(self.policy.contains_history_secret(source))

    def test_preserves_full_benign_multiline_text(self):
        source = "Bàn về Python, token trong LLM và password manager.\n" + "chi tiết cuộc trò chuyện bình thường " * 500
        self.assertEqual(self.policy.redact_history_text(source), source)
        self.assertFalse(self.policy.contains_history_secret(source))
        for question in ("Token là gì?", "Mật khẩu là gì?", "api key là gì?"):
            self.assertEqual(self.policy.redact_history_text(question), question)

    def test_preserves_credential_definitions_and_token_budgets(self):
        for source in (
            "Token là đơn vị văn bản mà LLM xử lý.",
            "API key is used for authentication.",
            "A password manager is software that stores credentials.",
            "Token budget là 2400 cho một lượt trả lời.",
            "Ngân sách token là 2400 và cửa sổ lịch sử giữ 20 lượt.",
            "Mật khẩu là một chuỗi ký tự để xác thực người dùng.",
        ):
            with self.subTest(source=source):
                self.assertEqual(self.policy.redact_history_text(source), source)
                self.assertFalse(self.policy.contains_history_secret(source))

    def test_redaction_preserves_following_sentences_and_full_punctuated_secret(self):
        for source, suffix, forbidden in (
            ("My password is ReviewFixture. Today we learn Python.", " Today we learn Python.", "ReviewFixture"),
            ("password=violet.test.730. Today we learn Python.", " Today we learn Python.", "violet.test.730"),
            ("Mật khẩu của ba là violet!test730. Hôm nay học Python.", " Hôm nay học Python.", "violet!test730"),
            ('password="Blue rabbit with stars". Today we learn Python.', " Today we learn Python.", "Blue rabbit with stars"),
            ("token=opaque-token.fixture and the story continues.", " and the story continues.", "opaque-token.fixture"),
        ):
            with self.subTest(source=source):
                result = self.policy.redact_history_text(source)
                self.assertNotIn(forbidden, result)
                self.assertIn(suffix, result)

    def test_private_key_with_existing_redaction_marker_masks_entire_body(self):
        source = "-----BEGIN " + "PRIVATE KEY-----\n[redacted secret]\nREVIEW_BODY\n-----END " + "PRIVATE KEY-----\nSafe ending."
        result = self.policy.redact_history_text(source)
        self.assertNotIn("REVIEW_BODY", result)
        self.assertIn("Safe ending.", result)
        snapshot = self.policy.sanitize_history_state({"short_term": source.splitlines()})
        self.assertNotIn("REVIEW_BODY", str(snapshot))

    def test_quoted_passphrase_with_existing_marker_cannot_expose_tail(self):
        source = 'password="alpha [redacted secret] HiddenTailFixture". Keep this sentence.'
        result = self.policy.redact_history_text(source)
        self.assertNotIn("HiddenTailFixture", result)
        self.assertIn("Keep this sentence.", result)
        self.assertEqual(self.policy.redact_history_text(result), result)

    def test_snapshot_copy_covers_history_shapes_without_editing_durable_facts(self):
        source = {"profile": {"name": "Ba"}, "long_term": [{"id": "fact-1", "text": "RTX 4070"}],
                  "short_term": ["USER: password=short-secret"],
                  "chat_log": [{"user": "token=chat-secret", "nana": "password=reply-secret", "at": 123}],
                  "session_checkpoint": {"session_id": "session-7", "checkpoint_revision": 2,
                      "pending_turns": [{"event_id": "evt", "user_text": "password=pending-secret", "nana_text": "token=echo-secret"}],
                      "anchors": [{"text": "password=anchor-secret", "event_id": "source"}]},
                  "session_summary": {"summary": "password=old-summary-secret"},
                  "custom": {"keep": True}}
        original = copy.deepcopy(source)
        result = self.policy.sanitize_history_state(source)
        self.assertEqual(source, original)
        self.assertEqual(result["long_term"], source["long_term"])
        self.assertEqual(result["custom"], source["custom"])
        for forbidden in ("short-secret", "chat-secret", "reply-secret", "pending-secret", "echo-secret", "anchor-secret", "old-summary-secret"):
            self.assertNotIn(forbidden, json.dumps(result))
        self.assertEqual(result["session_checkpoint"]["session_id"], "session-7")
        self.assertEqual(result["session_checkpoint"]["pending_turns"][0]["event_id"], "evt")

    def test_snapshot_multiline_key_fragments_do_not_survive_in_adjacent_entries(self):
        source = {"profile": {}, "long_term": [], "short_term": [
            "USER: -----BEGIN " + "PRIVATE KEY-----", "USER: FragmentedFixtureBody", "-----END " + "PRIVATE KEY-----", "NANA: còn câu chuyện"],
            "chat_log": ["USER: password:", "PasswordOnNextEntry", "NANA: tiếp tục"]}
        result = self.policy.sanitize_history_state(source)
        self.assertNotIn("FragmentedFixtureBody", str(result))
        self.assertNotIn("PasswordOnNextEntry", str(result))
        self.assertIn("còn câu chuyện", str(result))
        self.assertIn("tiếp tục", str(result))

    def test_filter_preview_never_echoes_secret(self):
        report = self.memory.memory_filter_preview_report("Nhớ kỹ: mật khẩu ba là PreviewFixture")
        self.assertNotIn("PreviewFixture", str(report))

    def test_chat_log_sanitizes_before_disk_write(self):
        self.memory.save_chat_log("USER: mật khẩu của ba là WriteOnlyFixture")
        self.assertNotIn("WriteOnlyFixture", self.chat_path.read_text(encoding="utf-8"))

    def test_old_multiline_chat_is_sanitized_before_recent_line_selection(self):
        self.chat_path.write_text("[old] USER: -----BEGIN PRIVATE KEY-----\nUSER: key-body-fixture\n-----END PRIVATE KEY-----\n[old] NANA: tiếp tục nhé\n", encoding="utf-8")
        recent = self.memory.load_recent_chat(20)
        self.assertNotIn("key-body-fixture", recent)
        self.assertIn("tiếp tục nhé", recent)
        # Reading protects consumers but does not silently rewrite the archive.
        self.assertIn("key-body-fixture", self.chat_path.read_text(encoding="utf-8"))

    def test_load_masks_legacy_snapshot_without_rewriting_valid_primary(self):
        raw = {"profile": {}, "long_term": ["RTX 4070"], "short_term": ["password=legacy-secret"]}
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        loaded = self.memory.load_memory(self.path)
        self.assertNotIn("legacy-secret", str(loaded))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), raw)

    def test_save_sanitizes_current_and_previous_then_recovers_safely(self):
        raw = {"profile": {}, "long_term": ["RTX 4070"], "short_term": ["password=previous-secret"], "snapshot_revision": 3}
        self.path.write_text(json.dumps(raw), encoding="utf-8")
        fresh = {"profile": {}, "long_term": ["RTX 4070"], "chat_log": ["token=current-secret"]}
        receipt = self.memory.save_memory(fresh, self.path)
        self.assertTrue(receipt.committed, receipt.error)
        self.assertEqual(receipt.snapshot_revision, 4)
        previous = self.path.with_name("memory.json.previous")
        self.assertNotIn("previous-secret", previous.read_text(encoding="utf-8"))
        self.assertNotIn("current-secret", self.path.read_text(encoding="utf-8"))
        self.path.write_text("{broken", encoding="utf-8")
        recovered = self.memory.load_memory(self.path)
        self.assertEqual(recovered["snapshot_revision"], 3)
        self.assertEqual(recovered["long_term"], ["RTX 4070"])
        self.assertNotIn("previous-secret", self.path.read_text(encoding="utf-8"))

    def test_async_save_sanitizes_memory_and_disk(self):
        self.memory.memory["short_term"] = ["password=async-secret"]
        receipt = self.memory.save_memory_async()
        self.assertTrue(receipt.committed, receipt.error)
        self.assertNotIn("async-secret", str(self.memory.memory))
        self.assertNotIn("async-secret", self.path.read_text(encoding="utf-8"))

    def test_explicit_secret_save_rejected_without_successful_durable_write(self):
        for source in ("Nhớ kỹ: mật khẩu của ba là BananaFixture", "Nhớ kỹ: api_key=sk-fixtureOnly1234567890"):
            self.assertFalse(self.memory.memory_importance_decision(source)[0])
            with self.assertRaises(ValueError):
                self.memory.store_explicit_fact(source, source_event_id="private-1", evidence="owner")
        self.assertEqual(self.memory.memory["long_term"], [])
        self.assertFalse(self.path.exists())

    def test_explicit_migration_dry_run_then_atomic_scrub_of_all_history_files(self):
        current = {"profile": {}, "long_term": ["keep durable"], "schema_version": 2,
                   "snapshot_revision": 8, "short_term": ["password=current-old-secret"]}
        previous = dict(current, snapshot_revision=7, short_term=["password=previous-old-secret"])
        self.path.write_text(json.dumps(current), encoding="utf-8")
        previous_path = self.path.with_name("memory.json.previous")
        previous_path.write_text(json.dumps(previous), encoding="utf-8")
        self.chat_path.write_text("USER: password=archive-old-secret\nNANA: còn câu chuyện\n", encoding="utf-8")
        report = self.policy.sanitize_history_files(self.path, self.chat_path)
        self.assertEqual(report["changed_files"], 3)
        self.assertFalse(report["applied"])
        self.assertIn("current-old-secret", self.path.read_text(encoding="utf-8"))
        applied = self.policy.sanitize_history_files(self.path, self.chat_path, apply=True)
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["changed_files"], 3)
        for path, revision in ((self.path, 8), (previous_path, 7)):
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["snapshot_revision"], revision)
            self.assertEqual(snapshot["long_term"], ["keep durable"])
            self.assertNotIn("old-secret", path.read_text(encoding="utf-8"))
        self.assertNotIn("old-secret", self.chat_path.read_text(encoding="utf-8"))
        self.assertNotIn("old-secret", json.dumps(applied))
        self.assertEqual(self.policy.sanitize_history_files(self.path, self.chat_path, apply=True)["changed_files"], 0)

    def test_migration_validates_all_files_before_any_write(self):
        self.path.write_text(json.dumps({"profile": {}, "long_term": [], "short_term": ["password=keep-until-valid"]}), encoding="utf-8")
        self.path.with_name("memory.json.previous").write_text("{corrupt", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.policy.sanitize_history_files(self.path, self.chat_path, apply=True)
        self.assertIn("keep-until-valid", self.path.read_text(encoding="utf-8"))

    def test_failed_migration_replace_keeps_original_and_cleans_temporary(self):
        source = {"profile": {}, "long_term": [], "short_term": ["password=atomic-fixture"]}
        self.path.write_text(json.dumps(source), encoding="utf-8")
        with patch.object(self.policy.os, "replace", side_effect=OSError("fixture replace failure")):
            with self.assertRaises(OSError):
                self.policy.sanitize_history_files(self.path, self.chat_path, apply=True)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), source)
        self.assertEqual(list(self.path.parent.glob("*.privacy.tmp")), [])
        self.assertEqual(self.policy.sanitize_history_files(self.path, self.chat_path, apply=True)["changed_files"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
