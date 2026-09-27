"""P1-A integration checks for the real private retrieval hot path.

All providers are fake or unconfigured.  These tests must never perform a
network request or read/write production memory.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))


def _private_record(*, record_id: str = "private-1", text: str = "coffee preference"):
    return {
        "id": record_id,
        "text": text,
        "source_event_id": "event-private-1",
        "lane": "private_owner",
        "confidence": 0.95,
        "created_at": 1_000.0,
    }


def test_injected_adapter_config_owns_the_min_score_threshold():
    from nana.runtime.controlled_memory_retrieval import (
        RetrievalScope,
        retrieve_memory_candidates,
    )

    class FakeAdapter:
        is_configured = True
        config = SimpleNamespace(min_score=0.90)

        def score(self, _query, candidates, _timeout_ms):
            return {
                "scores": {str(candidate["id"]): 0.85 for candidate in candidates},
                "elapsed_ms": 0.0,
            }

    result = retrieve_memory_candidates(
        {"long_term": [_private_record(text="remembered animal is an otter")]},
        "which creature was mentioned",
        scope=RetrievalScope(lane="private_owner"),
        semantic_enabled=True,
        semantic_adapter=FakeAdapter(),
        now=2_000.0,
    )

    assert result.semantic_used is True
    assert result.candidates == ()
    assert result.status == "empty"


def test_private_gpt_hotpath_uses_factory_and_lexical_fallback(monkeypatch):
    from nana.brain import gpt
    from nana.runtime import semantic_adapters

    factory_calls = []

    monkeypatch.setattr(
        gpt,
        "_memory_phase2_flag",
        lambda name: name == "MEMORY_SEMANTIC_RETRIEVAL_ENABLED",
    )
    monkeypatch.setattr(
        gpt,
        "_get_spine",
        lambda: SimpleNamespace(_memory={"long_term": [_private_record()]}),
    )

    def no_provider_factory():
        factory_calls.append(True)
        return None

    monkeypatch.setattr(semantic_adapters, "build_semantic_adapter", no_provider_factory)

    block = gpt._private_memory_retrieval_block("coffee")

    assert factory_calls == [True]
    # A successful lexical result remains ``ok`` even though semantic scoring
    # was unavailable; the important contract is no provider call and a usable
    # deterministic candidate.
    assert "status: ok" in block
    assert "coffee preference" in block
    assert "semantic_used: False" in block


def test_private_gpt_hotpath_accepts_fake_semantic_match(monkeypatch):
    from nana.brain import gpt
    from nana.runtime import semantic_adapters

    class FakeAdapter:
        is_configured = True
        config = SimpleNamespace(min_score=0.75)

        def score(self, _query, candidates, _timeout_ms):
            return {
                "scores": {str(candidate["id"]): 0.95 for candidate in candidates},
                "elapsed_ms": 0.0,
            }

    monkeypatch.setattr(
        gpt,
        "_memory_phase2_flag",
        lambda name: name == "MEMORY_SEMANTIC_RETRIEVAL_ENABLED",
    )
    monkeypatch.setattr(
        gpt,
        "_get_spine",
        lambda: SimpleNamespace(
            _memory={"long_term": [_private_record(text="remembered animal is an otter")]}
        ),
    )
    monkeypatch.setattr(
        semantic_adapters,
        "build_semantic_adapter",
        lambda: FakeAdapter(),
    )

    block = gpt._private_memory_retrieval_block("which creature was mentioned")

    assert "status: ok" in block
    assert "remembered animal is an otter" in block
    assert "semantic_used: True" in block
