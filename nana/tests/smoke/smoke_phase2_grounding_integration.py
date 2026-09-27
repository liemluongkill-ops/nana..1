"""Phase 2 retrieval integration smoke for MemoryEvidence (fake-only)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _Adapter:
    def __init__(self):
        self.calls = 0

    def score(self, query, candidates, timeout_ms=100):
        self.calls += 1
        return {str(candidates[0]["id"]): 0.95} if candidates else {}


def _install_fake_spine(records):
    module = types.ModuleType("nana.runtime.memory_spine")
    module.get_memory_spine = lambda: types.SimpleNamespace(_memory={"long_term": records})
    return module


def test_private_grounding_uses_controlled_boundary_when_enabled():
    import nana.config as config
    import nana.runtime.memory_grounding as grounding

    original_flag = getattr(config, "MEMORY_SEMANTIC_RETRIEVAL_ENABLED", False)
    original_module = sys.modules.get("nana.runtime.memory_spine")
    records = [{
        "id": "fact-1",
        "type": "project_fact",
        "text": "Ba thích trà sen.",
        "source": "user",
        "source_event_id": "evt-1",
        "confidence": 0.95,
        "lane": "private_owner",
        "created_at": 100.0,
        "tags": ["trà"],
    }]
    adapter = _Adapter()
    config.MEMORY_SEMANTIC_RETRIEVAL_ENABLED = True
    sys.modules["nana.runtime.memory_spine"] = _install_fake_spine(records)
    try:
        evidence = grounding.EvidenceBuilder("private_owner", semantic_adapter=adapter).build(
            "trà sen", "Ba thích trà sen"
        )
    finally:
        config.MEMORY_SEMANTIC_RETRIEVAL_ENABLED = original_flag
        if original_module is None:
            sys.modules.pop("nana.runtime.memory_spine", None)
        else:
            sys.modules["nana.runtime.memory_spine"] = original_module
    assert adapter.calls == 1, adapter.calls
    assert evidence.status == "found", evidence
    assert evidence.source_event_ids == ["evt-1"], evidence


def test_public_grounding_never_enters_private_controlled_spine():
    import nana.config as config
    import nana.runtime.memory_grounding as grounding

    original_flag = getattr(config, "MEMORY_SEMANTIC_RETRIEVAL_ENABLED", False)
    original_module = sys.modules.get("nana.runtime.memory_spine")
    config.MEMORY_SEMANTIC_RETRIEVAL_ENABLED = True
    forbidden = types.ModuleType("nana.runtime.memory_spine")
    forbidden.get_memory_spine = lambda: (_ for _ in ()).throw(AssertionError("public touched private spine"))
    sys.modules["nana.runtime.memory_spine"] = forbidden
    try:
        evidence = grounding.EvidenceBuilder("public_stage").build(
            "private owner fact", "private owner fact?"
        )
    finally:
        config.MEMORY_SEMANTIC_RETRIEVAL_ENABLED = original_flag
        if original_module is None:
            sys.modules.pop("nana.runtime.memory_spine", None)
        else:
            sys.modules["nana.runtime.memory_spine"] = original_module
    assert evidence.status in {"user_claim_only", "not_found"}, evidence


def test_private_prompt_wrapper_uses_phase2_only_when_flag_is_on():
    import nana.config as config
    import nana.brain.gpt as gpt

    original_flag = getattr(config, "MEMORY_SEMANTIC_RETRIEVAL_ENABLED", False)
    original_spine = gpt._spine_singleton
    config.MEMORY_SEMANTIC_RETRIEVAL_ENABLED = True
    gpt._spine_singleton = type("FakeSpine", (), {
        "_memory": {"long_term": [{
            "id": "prompt-fact",
            "type": "project_fact",
            "text": "Ba thích trà sen.",
            "source": "user",
            "source_event_id": "evt-prompt",
            "confidence": 0.9,
            "lane": "private_owner",
            "tags": ["trà"],
            "created_at": 100.0,
        }]},
    })()
    try:
        block = gpt._private_memory_retrieval_block("trà sen")
    finally:
        config.MEMORY_SEMANTIC_RETRIEVAL_ENABLED = original_flag
        gpt._spine_singleton = original_spine
    assert "prompt-fact" in block and "evt-prompt" in block, block
    assert "Ba thích trà sen" in block, block


def run_all() -> int:
    tests = [
        test_private_grounding_uses_controlled_boundary_when_enabled,
        test_public_grounding_never_enters_private_controlled_spine,
        test_private_prompt_wrapper_uses_phase2_only_when_flag_is_on,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"Phase2 grounding integration: {len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_all())
