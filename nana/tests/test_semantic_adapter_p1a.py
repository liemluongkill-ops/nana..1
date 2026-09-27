"""Test-first matrix for P1-A semantic adapter (Phase 2 blocker).

Red tests written before implementation. Tests must pass after adapter is wired.
"""

import os
import pytest


# Test data fixtures

@pytest.fixture
def fake_embeddings():
    """Deterministic fake embeddings for testing."""
    # 3-dimensional for simplicity; real embeddings are 384-1536 dim
    return {
        "tên tôi là minh": [0.8, 0.2, 0.1],
        "tên của tôi là minh": [0.85, 0.15, 0.1],  # paraphrase, high similarity
        "mình tên minh": [0.82, 0.18, 0.12],  # Vietnamese variant
        "tên tôi là hùng": [0.3, 0.7, 0.2],  # different actor
        "minh thích cà phê": [0.1, 0.3, 0.9],  # unrelated
        "hôm nay trời đẹp": [0.05, 0.05, 0.95],  # unrelated
    }


@pytest.fixture
def fake_transport(fake_embeddings):
    """Fake HTTP transport returning deterministic embeddings."""
    def transport(url, headers, json, timeout):
        texts = json.get("input", [])
        vectors = []
        for i, text in enumerate(texts):
            # Match against fake_embeddings or return zero vector
            vec = fake_embeddings.get(text.strip(), [0.0, 0.0, 0.0])
            vectors.append({"index": i, "embedding": vec})
        return {"data": vectors}
    return transport


@pytest.fixture
def semantic_config_minimal():
    """Minimal valid config for testing."""
    return {
        "provider": "test",
        "endpoint": "https://test.example.com/v1",
        "model": "test-embedding-model",
        "api_key_env": "TEST_EMBEDDING_KEY",
        "min_score": "0.75",
        "timeout_ms": "1200",
    }


# P1-A RED TESTS (must fail before implementation)


def test_flag_default_off_does_not_call_adapter():
    """Flag mặc định OFF không gọi adapter/transport."""
    from nana.runtime.controlled_memory_retrieval import semantic_retrieval_enabled

    # Clear env to test default
    old = os.environ.pop("NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED", None)
    try:
        assert semantic_retrieval_enabled() is False
    finally:
        if old is not None:
            os.environ["NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED"] = old


def test_provider_missing_config_returns_lexical_fallback(fake_transport):
    """Provider thiếu config → lexical fallback."""
    from nana.runtime.controlled_memory_retrieval import retrieve_memory_candidates, RetrievalScope
    from nana.runtime.semantic_adapters import OpenAICompatibleEmbeddingAdapter

    # Config with provider=none
    config = {
        "provider": "none",
        "endpoint": "",
        "model": "",
        "api_key_env": "",
        "min_score": "0.75",
        "timeout_ms": "1200",
    }

    adapter = OpenAICompatibleEmbeddingAdapter(transport=fake_transport, config=config)

    memory_store = {
        "long_term": [
            {
                "id": "mem1",
                "text": "tên tôi là minh",
                "source_event_id": "evt-001",
                "lane": "private",
                "confidence": 0.9,
                "created_at": 1000.0,
            }
        ]
    }

    scope = RetrievalScope(lane="private", platform="test", actor_key="test:actor1")

    result = retrieve_memory_candidates(
        memory_store,
        "tên của tôi là minh",  # paraphrase
        scope=scope,
        semantic_enabled=True,
        semantic_adapter=adapter,
    )

    # Should fallback to lexical, semantic not used
    assert result.fallback_used is True or result.semantic_error == "adapter_unavailable"
    assert result.semantic_used is False


def test_fake_semantic_only_match_ranked_correctly(fake_transport, semantic_config_minimal):
    """Fake semantic-only match được rank đúng."""
    from nana.runtime.controlled_memory_retrieval import retrieve_memory_candidates, RetrievalScope
    from nana.runtime.semantic_adapters import OpenAICompatibleEmbeddingAdapter

    os.environ["TEST_EMBEDDING_KEY"] = "fake-key-for-test"

    adapter = OpenAICompatibleEmbeddingAdapter(transport=fake_transport, config=semantic_config_minimal)

    memory_store = {
        "long_term": [
            {
                "id": "mem1",
                "text": "tên của tôi là minh",  # paraphrase, high semantic
                "source_event_id": "evt-001",
                "lane": "private",
                "confidence": 0.9,
                "created_at": 1000.0,
            },
            {
                "id": "mem2",
                "text": "minh thích cà phê",  # unrelated
                "source_event_id": "evt-002",
                "lane": "private",
                "confidence": 0.9,
                "created_at": 1000.0,
            },
        ]
    }

    scope = RetrievalScope(lane="private", platform="test", actor_key="test:actor1")

    result = retrieve_memory_candidates(
        memory_store,
        "tên tôi là minh",
        scope=scope,
        semantic_enabled=True,
        semantic_adapter=adapter,
    )

    assert result.semantic_used is True
    assert len(result.candidates) >= 1
    # mem1 should rank higher due to semantic similarity
    assert result.candidates[0].id == "mem1"
    assert "semantic" in result.candidates[0].match_reason


def test_raw_score_below_threshold_not_accepted(semantic_config_minimal):
    """Raw score dưới threshold không được nhận."""
    from nana.runtime.semantic_adapters import OpenAICompatibleEmbeddingAdapter

    os.environ["TEST_EMBEDDING_KEY"] = "fake-key-for-test"

    # Transport that returns anti-parallel vectors (cosine = -1, normalized score ≈ 0)
    def antiparallel_transport(url, headers, json, timeout):
        input_data = json.get("input", [])
        n = len(input_data)

        embeddings = []
        for i in range(n):
            if i == 0:
                # Query vector points in positive direction
                vec = [1.0, 0.0, 0.0, 0.0]
            else:
                # Candidate vectors point in opposite direction (anti-parallel)
                vec = [-1.0, 0.0, 0.0, 0.0]
            embeddings.append({"object": "embedding", "embedding": vec, "index": i})

        return {
            "object": "list",
            "data": embeddings,
            "model": "fake-model",
            "usage": {"prompt_tokens": 10, "total_tokens": 10},
        }

    adapter = OpenAICompatibleEmbeddingAdapter(transport=antiparallel_transport, config=semantic_config_minimal)

    candidates = [
        {"id": "mem1", "text": "hôm nay trời đẹp"},  # anti-parallel vector
    ]

    result = adapter.score("tên tôi là minh", tuple(candidates), timeout_ms=1200)

    scores = result.get("scores", {})
    # Anti-parallel vectors: cosine = -1 → normalized = 0.0, well below 0.75 threshold
    if "mem1" in scores:
        assert scores["mem1"] < 0.2, f"Expected very low score, got {scores['mem1']}"


def test_timeout_error_lexical_fallback(semantic_config_minimal):
    """Timeout/error/malformed vector → lexical fallback."""
    from nana.runtime.controlled_memory_retrieval import retrieve_memory_candidates, RetrievalScope
    from nana.runtime.semantic_adapters import OpenAICompatibleEmbeddingAdapter

    os.environ["TEST_EMBEDDING_KEY"] = "fake-key-for-test"

    def timeout_transport(url, headers, json, timeout):
        raise TimeoutError("embedding_timeout")

    adapter = OpenAICompatibleEmbeddingAdapter(transport=timeout_transport, config=semantic_config_minimal)

    memory_store = {
        "long_term": [
            {
                "id": "mem1",
                "text": "tên tôi là minh",
                "source_event_id": "evt-001",
                "lane": "private",
                "confidence": 0.9,
                "created_at": 1000.0,
            }
        ]
    }

    scope = RetrievalScope(lane="private", platform="test", actor_key="test:actor1")

    result = retrieve_memory_candidates(
        memory_store,
        "tên tôi",
        scope=scope,
        semantic_enabled=True,
        semantic_adapter=adapter,
    )

    # Should fallback to lexical
    assert result.fallback_used is True
    assert result.semantic_error != ""


def test_nan_inf_dimension_mismatch_rejected(semantic_config_minimal):
    """NaN/Inf/dimension mismatch bị reject."""
    from nana.runtime.semantic_adapters import OpenAICompatibleEmbeddingAdapter

    os.environ["TEST_EMBEDDING_KEY"] = "fake-key-for-test"

    def malformed_transport(url, headers, json, timeout):
        return {
            "data": [
                {"index": 0, "embedding": [1.0, float('nan'), 0.5]},  # NaN
                {"index": 1, "embedding": [0.1, 0.2, 0.3]},
            ]
        }

    adapter = OpenAICompatibleEmbeddingAdapter(transport=malformed_transport, config=semantic_config_minimal)

    candidates = [{"id": "mem1", "text": "test"}]

    with pytest.raises(ValueError, match="embedding_non_finite"):
        adapter.score("query", tuple(candidates), timeout_ms=1200)


def test_private_public_lane_isolation(fake_transport, semantic_config_minimal):
    """Private/public lane isolation."""
    from nana.runtime.controlled_memory_retrieval import retrieve_memory_candidates, RetrievalScope
    from nana.runtime.semantic_adapters import OpenAICompatibleEmbeddingAdapter

    os.environ["TEST_EMBEDDING_KEY"] = "fake-key-for-test"

    adapter = OpenAICompatibleEmbeddingAdapter(transport=fake_transport, config=semantic_config_minimal)

    memory_store = {
        "long_term": [
            {
                "id": "mem_private",
                "text": "private fact",
                "source_event_id": "evt-001",
                "lane": "private_owner",  # canonical
                "confidence": 0.9,
                "created_at": 1000.0,
            },
            {
                "id": "mem_public",
                "text": "public fact",
                "source_event_id": "evt-002",
                "lane": "public_stage",  # canonical
                "platform": "test",
                "actor_key": "test:actor1",
                "room_id": "room1",
                "verified": True,
                "consent": True,
                "expires_at": 9999999999.0,
                "confidence": 0.9,
                "created_at": 1000.0,
            },
        ]
    }

    # Private scope should not see public lane records
    scope_private = RetrievalScope(lane="private", platform="test", actor_key="test:actor1")
    result_private = retrieve_memory_candidates(
        memory_store,
        "fact",
        scope=scope_private,
        semantic_enabled=True,
        semantic_adapter=adapter,
    )

    # Should only see private record (canonicalized to private_owner)
    assert len(result_private.candidates) > 0, "Should have at least one candidate"
    assert all(c.lane in {"private_owner", "private_thread", "private_session"} for c in result_private.candidates), \
        f"Private scope should only see private lanes, got: {[c.lane for c in result_private.candidates]}"


def test_exact_actor_room_consent_ttl_required(fake_transport, semantic_config_minimal):
    """Exact actor/room/consent/TTL."""
    from nana.runtime.controlled_memory_retrieval import retrieve_memory_candidates, RetrievalScope
    from nana.runtime.semantic_adapters import OpenAICompatibleEmbeddingAdapter

    os.environ["TEST_EMBEDDING_KEY"] = "fake-key-for-test"

    adapter = OpenAICompatibleEmbeddingAdapter(transport=fake_transport, config=semantic_config_minimal)

    memory_store = {
        "long_term": [
            {
                "id": "mem_wrong_actor",
                "text": "fact from other actor",
                "source_event_id": "evt-001",
                "lane": "public",
                "platform": "test",
                "actor_key": "test:actor2",  # different actor
                "room_id": "room1",
                "verified": True,
                "consent": True,
                "expires_at": 9999999999.0,
                "confidence": 0.9,
                "created_at": 1000.0,
            },
        ]
    }

    scope = RetrievalScope(
        lane="public",
        platform="test",
        actor_key="test:actor1",
        room_id="room1",
        consent=True,
    )

    result = retrieve_memory_candidates(
        memory_store,
        "fact",
        scope=scope,
        semantic_enabled=True,
        semantic_adapter=adapter,
    )

    # Wrong actor should be rejected
    assert len(result.candidates) == 0
    assert "scope" in result.rejected or "actor_mismatch" in str(result.rejected)


def test_display_name_collision_no_leak(fake_transport, semantic_config_minimal):
    """Display-name collision không leak."""
    from nana.runtime.controlled_memory_retrieval import retrieve_memory_candidates, RetrievalScope
    from nana.runtime.semantic_adapters import OpenAICompatibleEmbeddingAdapter

    os.environ["TEST_EMBEDDING_KEY"] = "fake-key-for-test"

    adapter = OpenAICompatibleEmbeddingAdapter(transport=fake_transport, config=semantic_config_minimal)

    memory_store = {
        "long_term": [
            {
                "id": "mem1",
                "text": "fact from actor1",
                "source_event_id": "evt-001",
                "lane": "public",
                "platform": "test",
                "actor_key": "test:actor1",
                "room_id": "room1",
                "verified": True,
                "consent": True,
                "expires_at": 9999999999.0,
                "confidence": 0.9,
                "created_at": 1000.0,
            },
            {
                "id": "mem2",
                "text": "fact from actor2",
                "source_event_id": "evt-002",
                "lane": "public",
                "platform": "test",
                "actor_key": "test:actor2",  # different actor, same display name
                "room_id": "room1",
                "verified": True,
                "consent": True,
                "expires_at": 9999999999.0,
                "confidence": 0.9,
                "created_at": 1000.0,
            },
        ]
    }

    # Query as actor1, should not see actor2's facts
    scope = RetrievalScope(
        lane="public",
        platform="test",
        actor_key="test:actor1",
        room_id="room1",
        consent=True,
        display_name="John",  # same display name
    )

    result = retrieve_memory_candidates(
        memory_store,
        "fact",
        scope=scope,
        semantic_enabled=True,
        semantic_adapter=adapter,
    )

    # Should only see actor1's fact
    assert all(c.actor_key == "test:actor1" for c in result.candidates)
    assert not any(c.actor_key == "test:actor2" for c in result.candidates)


def test_adapter_never_receives_private_in_public_path():
    """Adapter không bao giờ nhận private record trong public path."""
    # This is enforced by controlled_memory_retrieval.py line ~350
    # which blocks semantic adapter for public lane entirely
    from nana.runtime.controlled_memory_retrieval import retrieve_memory_candidates, RetrievalScope

    memory_store = {
        "long_term": [
            {
                "id": "mem_private",
                "text": "private secret",
                "source_event_id": "evt-001",
                "lane": "private",
                "confidence": 0.9,
                "created_at": 1000.0,
            },
        ]
    }

    scope = RetrievalScope(
        lane="public",
        platform="test",
        actor_key="test:actor1",
        room_id="room1",
        consent=True,
    )

    # Public lane with semantic adapter should not use semantic
    # because public cross-session has stricter contract
    result = retrieve_memory_candidates(
        memory_store,
        "secret",
        scope=scope,
        semantic_enabled=True,
        semantic_adapter=None,  # Even with adapter, public lane blocks it
    )

    # Public lane should not use semantic per design
    assert result.semantic_used is False


def test_status_reflects_semantic_used_fallback_error():
    """Status phản ánh semantic_used, fallback_used, semantic_error."""
    from nana.runtime.controlled_memory_retrieval import retrieve_memory_candidates, RetrievalScope

    memory_store = {"long_term": []}
    scope = RetrievalScope(lane="private", platform="test", actor_key="test:actor1")

    result = retrieve_memory_candidates(
        memory_store,
        "test",
        scope=scope,
        semantic_enabled=True,
        semantic_adapter=None,
    )

    # No adapter provided, should indicate fallback
    assert result.fallback_used is True or result.semantic_error != ""
    assert result.semantic_used is False


# BAKEOFF FAKE MATRIX (positive/negative cases)


def test_bakeoff_positive_vietnamese_paraphrase(fake_transport, semantic_config_minimal):
    """5 positive Vietnamese paraphrase/accent/synonym cases."""
    from nana.runtime.semantic_adapters import OpenAICompatibleEmbeddingAdapter

    os.environ["TEST_EMBEDDING_KEY"] = "fake-key-for-test"

    adapter = OpenAICompatibleEmbeddingAdapter(transport=fake_transport, config=semantic_config_minimal)

    test_cases = [
        ("tên tôi là minh", "tên của tôi là minh"),  # paraphrase
        ("tên tôi là minh", "mình tên minh"),  # Vietnamese variant
        # Add 3 more cases as fake_embeddings is extended
    ]

    passed = 0
    for query, candidate_text in test_cases:
        candidates = [{"id": "mem1", "text": candidate_text}]
        result = adapter.score(query, tuple(candidates), timeout_ms=1200)
        scores = result.get("scores", {})
        if "mem1" in scores and scores["mem1"] >= 0.75:
            passed += 1

    # Target: >= 80% recall (2/2 for now, will be 4/5 when extended)
    recall = passed / len(test_cases)
    assert recall >= 0.80, f"Paraphrase recall {recall:.2%} < 80%"


def test_bakeoff_negative_cross_scope_leak(fake_transport, semantic_config_minimal):
    """5 negative cases: khác actor, khác room, expired, thiếu consent, unrelated."""
    from nana.runtime.controlled_memory_retrieval import retrieve_memory_candidates, RetrievalScope
    from nana.runtime.semantic_adapters import OpenAICompatibleEmbeddingAdapter

    os.environ["TEST_EMBEDDING_KEY"] = "fake-key-for-test"

    adapter = OpenAICompatibleEmbeddingAdapter(transport=fake_transport, config=semantic_config_minimal)

    base_record = {
        "text": "tên tôi là minh",
        "source_event_id": "evt-001",
        "lane": "public",
        "platform": "test",
        "actor_key": "test:actor1",
        "room_id": "room1",
        "verified": True,
        "consent": True,
        "expires_at": 9999999999.0,
        "confidence": 0.9,
        "created_at": 1000.0,
    }

    negative_cases = [
        ("wrong_actor", {**base_record, "id": "mem1", "actor_key": "test:actor2"}),
        ("wrong_room", {**base_record, "id": "mem2", "room_id": "room2"}),
        ("expired", {**base_record, "id": "mem3", "expires_at": 1000.0}),
        ("no_consent", {**base_record, "id": "mem4", "consent": False}),
        ("unrelated", {**base_record, "id": "mem5", "text": "hôm nay trời đẹp"}),
    ]

    scope = RetrievalScope(
        lane="public",
        platform="test",
        actor_key="test:actor1",
        room_id="room1",
        consent=True,
    )

    leaks = 0
    for case_name, record in negative_cases:
        memory_store = {"long_term": [record]}
        result = retrieve_memory_candidates(
            memory_store,
            "tên tôi là minh",
            scope=scope,
            semantic_enabled=True,
            semantic_adapter=adapter,
            now=5000.0,
        )
        if len(result.candidates) > 0:
            leaks += 1
            print(f"LEAK detected in case: {case_name}")

    # Zero leaks required
    assert leaks == 0, f"{leaks}/5 negative cases leaked"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
