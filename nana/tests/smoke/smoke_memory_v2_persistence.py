"""Private persistence checks. Never import real config or access production data."""
import copy
import importlib
import json
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

ROOT = Path(__file__).resolve().parents[3]


def _guard(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = str(args[0]).replace("\\", "/").lower()
        if "/nana/data/" in path or path.endswith("/.env") or path.endswith("/nana/config.py"):
            raise AssertionError("Persistence smoke blocked production data/config access")
    if event in {"socket.connect", "subprocess.Popen", "os.system"}:
        raise AssertionError("Persistence smoke blocked live process/network access")


sys.addaudithook(_guard)
# Never execute Nana's package initializer (it starts production singletons).
package = types.ModuleType("nana")
package.__path__ = [str(ROOT / "nana")]
runtime_package = types.ModuleType("nana.runtime")
runtime_package.__path__ = [str(ROOT / "nana" / "runtime")]
with patch.dict(sys.modules, {"nana": package, "nana.runtime": runtime_package}):
    from nana.runtime.memory_spine import MemorySpine


class FakeStore:
    def __init__(self, data=None):
        self.data = copy.deepcopy(data or {"profile": {}, "long_term": []})

    def snapshot(self):
        return copy.deepcopy(self.data)

    def replace(self, data):
        self.data = copy.deepcopy(data)


class PersistenceTests(unittest.TestCase):
    def test_general_knowledge_imperative_is_not_a_durable_fact(self):
        for text in ('Kể tên một vật dẫn điện thường gặp.', 'Nêu một cách thư giãn mắt sau khi đọc lâu.'):
            self.assertFalse(self.module.memory_importance_decision(text)[0], text)

    def test_explicitly_temporary_story_is_not_a_durable_fact(self):
        for text in ('Chi tiết câu chuyện khác: chiếc đèn giấy có tên là Sao Nhỏ.',
                     'Mình đang viết truyện: chú mèo có tên là Mướp.'):
            self.assertFalse(self.module.memory_importance_decision(text)[0], text)
        self.assertTrue(self.module.memory_importance_decision(
            'Nhớ kỹ: Chi tiết câu chuyện khác: chiếc đèn giấy có tên là Sao Nhỏ.')[0])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "memory.json"
        config = types.ModuleType("nana.config")
        config.MEMORY_PATH = self.path
        config.CHAT_HISTORY_PATH = self.path.parent / "chat.json"
        # Load memory under a private module name with fake config installed first.
        spec = importlib.util.spec_from_file_location("nana._persistence_test_memory", Path(__file__).resolve().parents[2] / "memory.py")
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"nana": package, "nana.runtime": runtime_package, "nana.config": config}):
            spec.loader.exec_module(self.module)
        self.Writer = self.module.AtomicMemoryWriter
        self.package_patch = patch.dict(sys.modules, {"nana": package, "nana.runtime": runtime_package, "nana.config": config})
        self.package_patch.start()
        self.addCleanup(self.package_patch.stop)

    def snapshot(self, revision, schema=2):
        return {"profile": {}, "long_term": [], "schema_version": schema,
                "snapshot_revision": revision, "custom": {"value": revision}}

    def test_explicit_restart_provenance(self):
        writer = self.Writer(self.path)
        store = FakeStore()
        spine = MemorySpine(store, writer=writer)
        fact = "Đừng quên máy của Ba dùng RTX 4070"
        item = spine.store_explicit_fact(fact, source_event_id="private-7", source="private", evidence="owner explicit")
        receipt = writer.last_receipt
        self.assertTrue(receipt.committed)
        self.assertEqual(receipt.snapshot_revision, 1)
        restarted = MemorySpine(FakeStore(self.Writer(self.path).load()))
        found = next(entry for entry in restarted.retrieve(fact) if entry.id == item.id)
        self.assertEqual((found.text, found.source_event_id, found.evidence), (fact, "private-7", "owner explicit"))
        self.assertNotIn(found.type, ("ephemeral", "preference"))
        self.assertFalse({"state", "delivery_state", "attempt_id"} & found.to_dict().keys())

    def test_explicit_fallback_and_rejections(self):
        store = FakeStore()
        spine = MemorySpine(store, writer=self.Writer(self.path))
        item = spine.store_explicit_fact("ZX-104 = blue cedar", source_event_id="p-8", source="private", evidence="owner")
        self.assertNotEqual(item.type, "ephemeral")
        before = store.snapshot()
        for text, event, source in [("password=hidden", "p-9", "private"), ("a fact", "", "private"), ("a fact", "p-9", "youtube"), ("", "p-9", "private")]:
            with self.assertRaises(ValueError):
                spine.store_explicit_fact(text, source_event_id=event, source=source, evidence="owner")
            self.assertEqual(store.snapshot(), before)

    def test_exact_ids_preserve_many_neighbors_and_legacy(self):
        entries = [{"id": str(i), "type": "project_fact", "text": "same"} for i in range(65)] + ["legacy unchanged"]
        store = FakeStore({"profile": {}, "long_term": entries})
        spine = MemorySpine(store, writer=self.Writer(self.path))
        self.assertEqual(spine.pin_memory("30"), ["30"])
        self.assertTrue(store.snapshot()["long_term"][30]["pinned"])
        self.assertEqual(spine.unpin_memory("30"), ["30"])
        self.assertEqual(spine.delete_memory_ids(["30", "missing"]), ["30"])
        self.assertEqual(store.snapshot()["long_term"], entries[:30] + entries[31:])

    def test_recovery_repairs_primary_and_preserves_schema(self):
        writer = self.Writer(self.path)
        self.assertTrue(writer.submit(self.snapshot(1, 1)).committed)
        self.assertTrue(writer.submit(self.snapshot(2, 2)).committed)
        self.path.write_text("{broken", encoding="utf-8")
        recovery = self.Writer(self.path)
        loaded = recovery.load()
        self.assertEqual((loaded["schema_version"], loaded["snapshot_revision"]), (1, 1))
        self.assertTrue(recovery.last_receipt.recovered)
        self.assertFalse(recovery.last_receipt.committed)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), loaded)

    def test_replace_failure_and_failed_explicit_ack(self):
        writer = self.Writer(self.path)
        writer.submit(self.snapshot(1))
        def fail(*_args):
            raise OSError("injected replace failure")
        failing = self.Writer(self.path, replace_func=fail)
        self.assertFalse(failing.submit(self.snapshot(2)).committed)
        self.assertEqual(self.Writer(self.path).load()["snapshot_revision"], 1)
        store = FakeStore()
        with self.assertRaises(OSError):
            MemorySpine(store, writer=failing).store_explicit_fact("cedar blue", source_event_id="p-1", source="private", evidence="owner")
        self.assertEqual(store.snapshot()["long_term"], [])

    def test_both_corrupt_fail_closed(self):
        self.path.write_text("bad", encoding="utf-8")
        self.path.with_name("memory.json.previous").write_text("bad", encoding="utf-8")
        writer = self.Writer(self.path)
        with self.assertRaises(ValueError):
            writer.load()
        self.assertFalse(writer.submit(self.snapshot(1)).committed)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "bad")

    def test_snapshot_copy_and_stale_order(self):
        writer = self.Writer(self.path)
        initial = self.snapshot(1)
        writer.submit(initial)
        initial["custom"]["value"] = "mutated"
        self.assertEqual(writer.load()["custom"]["value"], 1)
        gate = threading.Event()
        results = []
        def old():
            gate.wait()
            results.append(writer.submit(self.snapshot(2)))
        thread = threading.Thread(target=old)
        thread.start()
        self.assertTrue(writer.submit(self.snapshot(3)).committed)
        gate.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(results[0].committed)
        self.assertEqual(writer.load()["snapshot_revision"], 3)
        writer.flush()

    def test_legacy_save_preserves_extra_fields(self):
        data = {"profile": {}, "long_term": ["legacy"], "custom": {"keep": True}}
        self.path.write_text(json.dumps(data), encoding="utf-8")
        writer = self.Writer(self.path)
        loaded = writer.load()
        self.assertEqual(loaded["schema_version"], 1)
        self.assertEqual(loaded["snapshot_revision"], 0)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), data)
        self.assertTrue(self.module.save_memory(loaded, self.path).committed)
        self.assertEqual(self.Writer(self.path).load()["custom"], {"keep": True})

    def test_legacy_load_adds_empty_private_session_checkpoint(self):
        legacy = {
            "profile": {},
            "long_term": ["legacy"],
            "session_summary": {"legacy": "preserve"},
            "custom": {"keep": True},
        }
        merged = self.module._merge_defaults(copy.deepcopy(legacy))
        self.assertEqual(merged["session_checkpoint"], {})
        self.assertEqual(merged["session_summary"], legacy["session_summary"])
        self.assertEqual(merged["custom"], legacy["custom"])
        self.assertEqual(legacy.get("session_checkpoint"), None)

    def test_legacy_commands_pin_and_selected_delete(self):
        entries = [{"id": str(i), "type": "project_fact", "text": "ambiguous note"} for i in range(65)]
        self.module.memory["long_term"] = copy.deepcopy(entries)
        self.module.memory_keep_report("31")
        self.assertTrue(self.module.memory["long_term"][30]["pinned"])
        self.module.memory_unkeep_report("31")
        self.assertFalse(self.module.memory["long_term"][30]["pinned"])
        self.module.memory_drop_preview_report("31")
        pending = self.module._get_pending_memory_action()
        self.module.memory_confirm_action(pending["id"])
        self.assertEqual(self.module.memory["long_term"], entries[:30] + entries[31:])

    def test_concurrent_explicit_saves_do_not_lose_facts(self):
        spine = MemorySpine(FakeStore(), writer=self.Writer(self.path))
        gate = threading.Barrier(3)
        errors = []
        def save(number):
            try:
                gate.wait(timeout=3)
                spine.store_explicit_fact(f"cedar {number}", source_event_id=f"p-{number}", source="private", evidence="owner")
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=save, args=(number,)) for number in (1, 2)]
        for thread in threads:
            thread.start()
        gate.wait(timeout=3)
        for thread in threads:
            thread.join(timeout=3)
        self.assertFalse(errors, errors)
        data = self.Writer(self.path).load()
        self.assertEqual(data["snapshot_revision"], 2)
        self.assertEqual({item["source_event_id"] for item in data["long_term"]}, {"p-1", "p-2"})

    def test_failed_primary_replace_keeps_valid_primary(self):
        self.Writer(self.path).submit(self.snapshot(1))
        import os
        def fail_primary(source, destination):
            if Path(destination) == self.path:
                raise OSError("interrupted primary replacement")
            os.replace(source, destination)
        failed = self.Writer(self.path, replace_func=fail_primary).submit(self.snapshot(2))
        self.assertFalse(failed.committed)
        self.assertEqual(self.Writer(self.path).load()["snapshot_revision"], 1)
        self.assertEqual(json.loads(self.path.with_name("memory.json.previous").read_text(encoding="utf-8"))["snapshot_revision"], 1)

    def test_legacy_guard_and_preview_regressions(self):
        # These existing scripts import memory dynamically. Bind our already
        # isolated module, so their fixtures cannot initialize production Nana.
        with patch.dict(sys.modules, {"nana.memory": self.module}):
            for filename in ("smoke_memory_rehearsal_guard.py", "smoke_stage9e_memory_consolidation_preview.py"):
                spec = importlib.util.spec_from_file_location("_isolated_regression", Path(__file__).parent / filename)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertEqual(module.run_all(), 0)

    def test_explicit_verification_never_uses_embeddings(self):
        store = FakeStore({"profile": {}, "long_term": [
            {"id": "neighbor", "type": "project_fact", "text": "unrelated astronomy", "source": "private"}
        ]})
        with patch.dict(os.environ, {"NANA_USE_EMBEDDINGS": "1"}), patch.object(
                MemorySpine, "_embedding_score", side_effect=AssertionError("Embedding callable must not run")) as embedding:
            item = MemorySpine(store, writer=self.Writer(self.path)).store_explicit_fact(
                "cedar blue", source_event_id="p-local", source="private", evidence="owner")
            self.assertEqual(item.text, "cedar blue")
            embedding.assert_not_called()

    def test_failed_command_saves_restore_state_and_report_failure(self):
        def fail(*_args):
            raise OSError("injected command replacement failure")
        for action in ("keep", "unkeep", "confirm"):
            with self.subTest(action=action):
                item = {"id": "selected", "type": "project_fact", "text": "ambiguous note", "pinned": action == "unkeep"}
                self.module.memory.clear()
                self.module.memory.update(self.module._merge_defaults({"long_term": ["legacy neighbor", item]}))
                if action == "unkeep":
                    self.module.memory["memory_labels"] = {"id:selected": {"decision": "keep"}}
                with patch.object(self.module, "_memory_writer", self.Writer(self.path)):
                    self.assertTrue(self.module.save_memory_async().committed)
                before = copy.deepcopy(self.module.memory)
                disk_before = self.path.read_bytes()
                pending_before = None
                if action == "confirm":
                    self.module.memory_drop_preview_report("2")
                    pending_before = self.module._get_pending_memory_action()
                with patch.object(self.module, "_memory_writer", self.Writer(self.path, replace_func=fail)):
                    if action == "confirm":
                        report = self.module.memory_confirm_action(pending_before["id"])
                    else:
                        command = self.module.memory_keep_report if action == "keep" else self.module.memory_unkeep_report
                        report = command("2")
                self.assertIn("Status: save_failed", "\n".join(report))
                self.assertEqual(self.module.memory, before)
                self.assertEqual(self.path.read_bytes(), disk_before)
                if pending_before is not None:
                    self.assertEqual(self.module._get_pending_memory_action(), pending_before)

    def test_failed_extract_has_no_persisted_success_message(self):
        def fail(*_args):
            raise OSError("injected explicit replacement failure")
        before = copy.deepcopy(self.module.memory)
        output = io.StringIO()
        with patch.object(self.module, "_memory_writer", self.Writer(self.path, replace_func=fail)), redirect_stdout(output):
            result = self.module.extract_important("Đừng quên máy của Ba dùng RTX 4070", source_event_id="p-failed")
        self.assertIsNone(result)
        self.assertIn("not committed", output.getvalue())
        self.assertNotIn("Nhớ quan trọng", output.getvalue())
        self.assertEqual(self.module.memory, before)

    def test_save_status_inquiry_never_persists_the_question(self):
        before = copy.deepcopy(self.module.memory)
        for question in (
            "Con đã ghi nhớ tên chậu cây của Ba vào bộ nhớ lâu dài chưa?",
            "Con đã ghi nhớ tên chậu cây của Ba chưa?",
            "Con đã nhớ kỹ mã vé tàu của Ba chưa?",
            "Ghi nhớ tên chậu cây vào bộ nhớ lâu dài chưa",
            "Nana có lưu memory tên chậu cây của Ba chưa?",
            "Con da ghi nho ten chau cay cua Ba vao bo nho lau dai chua?",
        ):
            with self.subTest(question=question), redirect_stdout(io.StringIO()):
                self.assertFalse(self.module.has_explicit_memory_write_intent(question.lower()))
                self.assertFalse(self.module.memory_importance_decision(question)[0])
                self.assertIsNone(self.module.extract_important(question, source_event_id="status-only"))
                self.assertEqual(self.module.memory, before)
                self.assertFalse(self.path.exists())

    def test_fresh_save_instruction_with_confirmation_still_commits(self):
        text = "Ghi nhớ: tên chậu cây của Ba là Sen. Con đã lưu vào bộ nhớ thành công chưa?"
        with redirect_stdout(io.StringIO()):
            item = self.module.extract_important(text, source_event_id="new-save-with-confirmation")
        self.assertIsNotNone(item)
        loaded = self.Writer(self.path).load()
        self.assertTrue(any(entry["id"] == item.id for entry in loaded["long_term"]))

    def test_automatic_extraction_preserves_turn_provenance(self):
        spine_module = importlib.import_module("nana.runtime.memory_spine")
        spine_module.reset_memory_spine()
        self.addCleanup(spine_module.reset_memory_spine)
        fact = "Máy tính của Ba dùng GPU RTX 4070."
        eligible, reason, details = self.module.memory_importance_decision(fact)
        self.assertTrue(eligible)
        self.assertEqual(reason, "keyword_match")
        self.assertFalse(details["explicit"])
        with redirect_stdout(io.StringIO()):
            self.module.extract_important(fact, source_event_id="private-turn-auto-7", source="private")
        saved = self.Writer(self.path).load()["long_term"]
        item = next(item for item in saved if item["text"] == fact)
        self.assertNotEqual(item["type"], "ephemeral")
        self.assertEqual(item["source_event_id"], "private-turn-auto-7")
        # Legacy direct callers and historical entries retain unknown provenance.
        self.assertEqual(spine_module.load_item("historical note").source_event_id, "")
        legacy = MemorySpine({"long_term": []}).store_item("legacy direct caller", item_type="project_fact")
        self.assertEqual(legacy.source_event_id, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
