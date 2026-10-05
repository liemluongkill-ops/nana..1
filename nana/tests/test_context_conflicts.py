"""Task 11 compatible-domain memory conflict contracts (offline only)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from nana.runtime import context_adapters, context_runtime
from nana.runtime.context_contracts import (
    BudgetPolicy,
    BudgetPolicyStatus,
    CollectionDecision,
    ContextPacket,
    ContextSection,
    Freshness,
    Lane,
    Lifetime,
    Route,
    SemanticRole,
    SourceRef,
    SourceSnapshot,
    UnresolvedContextRequest,
)


NOW = 30_000.0


class _Authority:
    def authorize(self, _request):
        return context_runtime.TrustedIngress(Lane.PRIVATE_OWNER, None)


def _request(text="Which GPU is current?", *, temporal=False):
    raw = UnresolvedContextRequest(
        "task11-conflict-request",
        "task11-conflict-correlation",
        Route.INTERACTIVE,
        "fake-private-model",
        text,
        None,
        False,
        None,
        {},
        False,
        False,
        False,
        temporal,
        True,
    )
    return context_runtime.LaneResolver(
        _Authority(), wall_clock=lambda: NOW, monotonic_clock=lambda: NOW / 2
    ).resolve(raw)


def _api():
    names = (
        "MemoryRetrievalResult",
        "MemoryRetrievalRecord",
        "build_memory_context_bundle",
        "resolve_memory_conflicts",
    )
    missing = [name for name in names if not hasattr(context_adapters, name)]
    assert not missing, "Task 11 conflict API missing: " + ", ".join(missing)
    return SimpleNamespace(**{name: getattr(context_adapters, name) for name in names})


def _record(api, record_id, value, authority, **overrides):
    values = {
        "record_id": record_id,
        "source_event_id": f"event-{record_id}",
        "source": "user",
        "memory_type": "project_fact",
        "semantic_role": SemanticRole.FACT,
        "text": f"Ba uses {value}",
        "visibility": frozenset({Lane.PRIVATE_OWNER}),
        "confidence": 0.9,
        "relevance": 0.9,
        "observed_at": NOW - 10,
        "expires_at": None,
        "subject": "Ba",
        "subject_role": "owner",
        "conflict_key": "hardware.gpu.current",
        "canonical_value": value,
        "authority": authority,
        "semantic_status": "active",
        "temporal_selected": False,
    }
    values.update(overrides)
    return api.MemoryRetrievalRecord(**values)


@pytest.mark.parametrize(
    ("higher", "lower"),
    (
        ("explicit_verified_correction", "verified_current_state"),
        ("verified_current_state", "grounded_session_fact"),
        ("grounded_session_fact", "active_durable_fact"),
        ("active_durable_fact", "historical_fact"),
    ),
)
def test_every_authority_tier_beats_only_the_tier_below(higher, lower):
    api = _api()
    high = _record(api, "high", "RTX 4070", higher, observed_at=1, relevance=0.1)
    low = _record(
        api,
        "low",
        "RTX 4090",
        lower,
        observed_at=NOW,
        relevance=1.0,
        semantic_status="historical" if lower == "historical_fact" else "active",
        temporal_selected=lower == "historical_fact",
    )
    resolution = api.resolve_memory_conflicts((low, high), temporal_intent=True)
    assert resolution.records == (high,)
    assert {item.status for item in resolution.outcomes} == {"conflict_loser"}


def test_fact_and_state_are_compatible_but_other_roles_never_compete():
    api = _api()
    fact = _record(api, "fact", "RTX 4070", "verified_current_state")
    state = _record(
        api,
        "state",
        "RTX 4090",
        "active_durable_fact",
        semantic_role=SemanticRole.STATE,
    )
    history = _record(
        api,
        "history",
        "RTX 2080",
        "explicit_verified_correction",
        semantic_role=SemanticRole.HISTORY,
    )
    resolution = api.resolve_memory_conflicts((state, history, fact), temporal_intent=False)
    assert resolution.records == (history, fact)


def test_missing_typed_domain_metadata_is_not_inferred_from_prose():
    api = _api()
    typed = _record(api, "typed", "RTX 4070", "active_durable_fact")
    prose_only = _record(
        api,
        "prose",
        "RTX 4090",
        "explicit_verified_correction",
        subject=None,
        subject_role=None,
        conflict_key=None,
        canonical_value=None,
    )
    resolution = api.resolve_memory_conflicts((typed, prose_only), temporal_intent=False)
    assert resolution.records == (typed, prose_only)
    assert resolution.outcomes == ()


def test_equal_canonical_value_dedupes_deterministically_across_fact_state():
    api = _api()
    second = _record(
        api,
        "z-record",
        "RTX 4070",
        "verified_current_state",
        semantic_role=SemanticRole.STATE,
    )
    first = _record(api, "a-record", "RTX 4070", "verified_current_state")
    one = api.resolve_memory_conflicts((second, first), temporal_intent=False)
    two = api.resolve_memory_conflicts((first, second), temporal_intent=False)
    assert one.records == two.records == (first,)
    assert one.outcomes[0].status == two.outcomes[0].status == "duplicate"


def test_equal_tier_contradiction_omits_domain_with_explicit_uncertainty():
    api = _api()
    older = _record(
        api,
        "a-record",
        "RTX 4070",
        "verified_current_state",
        observed_at=1,
        relevance=0.1,
    )
    newer = _record(
        api,
        "z-record",
        "RTX 4090",
        "verified_current_state",
        observed_at=NOW,
        relevance=1.0,
    )
    resolution = api.resolve_memory_conflicts((newer, older), temporal_intent=False)
    assert resolution.records == ()
    assert len(resolution.outcomes) == 1
    outcome = resolution.outcomes[0]
    assert outcome.status == "conflict_unresolved"
    assert outcome.conflict_key == "hardware.gpu.current"
    assert set(outcome.omitted_record_ids) == {"a-record", "z-record"}


def test_historical_requires_typed_temporal_selection_and_superseded_never_returns():
    api = _api()
    historical = _record(
        api,
        "historical",
        "RTX 2080",
        "historical_fact",
        semantic_status="historical",
    )
    selected_history = _record(
        api,
        "selected-history",
        "RTX 3070",
        "historical_fact",
        semantic_status="historical",
        temporal_selected=True,
        conflict_key="hardware.gpu.previous",
    )
    superseded = _record(
        api,
        "superseded",
        "GTX 1080",
        "explicit_verified_correction",
        semantic_status="superseded",
        conflict_key="hardware.gpu.oldest",
    )
    authority_history = _record(
        api,
        "authority-history",
        "RTX 3060",
        "historical_fact",
        semantic_status="active",
        temporal_selected=True,
        conflict_key="hardware.gpu.archived",
    )
    ordinary = api.resolve_memory_conflicts(
        (historical, selected_history, superseded, authority_history), temporal_intent=False
    )
    temporal = api.resolve_memory_conflicts(
        (historical, selected_history, superseded, authority_history), temporal_intent=True
    )
    assert ordinary.records == ()
    assert temporal.records == (selected_history, authority_history)
    assert {item.status for item in temporal.outcomes} >= {"historical_excluded", "superseded"}


@pytest.mark.parametrize(
    "query",
    (
        "Gia su Ba doi sang RTX 4090 thi sao?",
        "Neu Ba dung RTX 4090 thi sao?",
        "Nana co nho Ba dung RTX 4090 khong?",
    ),
)
def test_current_hypothetical_conditional_or_recall_text_never_creates_truth(query):
    api = _api()
    request = _request(query)
    snapshot = SourceSnapshot(
        source="private_memory",
        revision="memory-v2-empty",
        observed_at=NOW,
        captured_at=NOW,
        freshness=Freshness.FRESH,
        payload={"long_term": []},
    )
    result = api.MemoryRetrievalResult(
        "memory-v2-empty", "empty", (), False, False, {}, "empty-query"
    )
    bundle = api.build_memory_context_bundle(
        request,
        snapshot,
        retriever=lambda *_: result,
        redact=lambda value: value,
    )

    def required(section_id, lifetime, role, owner, payload):
        return ContextSection(
            id=section_id,
            lifetime=lifetime,
            semantic_role=role,
            freshness=Freshness.FRESH,
            visibility=frozenset({Lane.PRIVATE_OWNER}),
            source=SourceRef(owner, "task11.raw-input.v1", section_id),
            revision=f"{owner}-raw-input-r1",
            authority=None,
            observed_at=NOW,
            expires_at=None,
            conflict_key=None,
            dedupe_key=section_id,
            max_tokens=2_000,
            payload=payload,
            formatter_version="task11.raw-input.v1",
            required=True,
            budget_class="mandatory",
            semantic_status="active",
            provenance=(),
            relevance=1.0,
        )

    base = (
        required("core.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, "core", "CORE"),
        required("policy.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, "core", "POLICY"),
        required("contract.output.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, "core", "OUTPUT"),
        required("identity.owner.v1", Lifetime.DURABLE, SemanticRole.IDENTITY, "owner_identity", "OWNER"),
        required("expression.private.v1", Lifetime.TURN, SemanticRole.STATE, "expression", "TONE"),
    )
    plan = context_runtime.build_turn_plan(
        context_runtime.GptTurnInput(
            request=request,
            sections=(*base, *bundle.sections),
            evidence=None,
            model=request.model,
            max_output_tokens=200,
            budget_revision=context_runtime.PRIVATE_POLICY_REVISION,
            memory_bundle=bundle,
        ),
        redact=lambda value: value,
    )
    assert bundle.retrieval.records == ()
    assert not any(
        section.semantic_role in {SemanticRole.FACT, SemanticRole.STATE}
        for section in bundle.sections
    )
    assert "RTX 4090" not in plan.compiled.messages[0].content
    assert "canonical_value" not in plan.compiled.messages[0].content
    assert plan.compiled.messages[-1].content == query


def _section(section_id, role, value, authority, *, observed_at, relevance):
    owner = f"owner-{section_id}"
    return ContextSection(
        id=section_id,
        lifetime=Lifetime.TURN,
        semantic_role=role,
        freshness=Freshness.FRESH,
        visibility=frozenset({Lane.PRIVATE_OWNER}),
        source=SourceRef(owner, "task11.fixture.v1", section_id),
        revision=f"{owner}-r1",
        authority=authority,
        observed_at=observed_at,
        expires_at=None,
        conflict_key="hardware.gpu.current",
        dedupe_key=f"gpu-{value}",
        max_tokens=500,
        payload={
            "subject": "Ba",
            "subject_role": "owner",
            "canonical_value": value,
            "text": f"Ba uses {value}",
        },
        formatter_version="task11.fixture.v1",
        required=False,
        budget_class="memory",
        semantic_status="active",
        provenance=(),
        relevance=relevance,
    )


def _resolve_sections(sections):
    request = _request()
    owners = sorted(section.source.owner for section in sections)
    packet = ContextPacket(
        schema_version=1,
        capture_id="task11-conflict-capture",
        snapshot_revision=1,
        request=request,
        source_revisions=tuple((owner, f"{owner}-r1") for owner in owners),
        sections=tuple(sections),
        collection_receipt=tuple(
            CollectionDecision(owner, "ALLOW_READ", "lane_policy", f"{owner}-r1")
            for owner in owners
        ),
    )
    policy = BudgetPolicy(
        "task11-policy",
        "task11-policy-r1",
        BudgetPolicyStatus.APPROVED,
        10_000,
        40_000,
    )
    return context_runtime.ContextRuntime().resolve(packet, policy)


def test_section_resolver_treats_fact_state_as_one_domain_and_ignores_freshness():
    high = _section(
        "memory.retrieval.v1",
        SemanticRole.FACT,
        "RTX 4070",
        "verified_current_state",
        observed_at=1,
        relevance=0.1,
    )
    low = _section(
        "situation.current.v1",
        SemanticRole.STATE,
        "RTX 4090",
        "active_durable_fact",
        observed_at=NOW,
        relevance=1.0,
    )
    resolved = _resolve_sections((low, high))
    assert tuple(section.id for section in resolved.selected_sections) == (high.id,)


def test_section_resolver_reports_tied_contradiction_as_conflict_unresolved():
    first = _section(
        "memory.retrieval.v1",
        SemanticRole.FACT,
        "RTX 4070",
        "verified_current_state",
        observed_at=1,
        relevance=0.1,
    )
    second = _section(
        "situation.current.v1",
        SemanticRole.STATE,
        "RTX 4090",
        "verified_current_state",
        observed_at=NOW,
        relevance=1.0,
    )
    resolved = _resolve_sections((second, first))
    assert resolved.selected_sections == ()
    decisions = {decision.section_id: decision for decision in resolved.decisions}
    assert {decision.decision for decision in decisions.values()} == {"conflict_unresolved"}
    assert {decision.reason for decision in decisions.values()} == {"conflict_unresolved"}


def test_section_facts_missing_typed_conflict_metadata_do_not_truth_dedupe():
    first = replace(
        _section(
            "memory.retrieval.v1",
            SemanticRole.FACT,
            "RTX 4070",
            "verified_current_state",
            observed_at=1,
            relevance=0.1,
        ),
        authority=None,
        conflict_key=None,
        dedupe_key="untyped-gpu",
        payload={"subject": "Ba", "subject_role": "owner", "text": "RTX 4070"},
    )
    second = replace(
        _section(
            "relationship.shared.v1",
            SemanticRole.FACT,
            "RTX 4090",
            "verified_current_state",
            observed_at=NOW,
            relevance=1.0,
        ),
        authority=None,
        conflict_key=None,
        dedupe_key="untyped-gpu",
        payload={"subject": "Ba", "subject_role": "owner", "text": "RTX 4090"},
    )
    resolved = _resolve_sections((second, first))
    assert {section.id for section in resolved.selected_sections} == {first.id, second.id}
