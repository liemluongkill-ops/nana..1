"""Behavioral tests for the inert Context Runtime contract layer."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
import gc
import math
import os
from pathlib import Path
import subprocess
import sys
import weakref

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))


import nana.runtime.context_contracts as context_contracts  # noqa: E402
from nana.runtime.context_contracts import (  # noqa: E402
    BudgetPolicy,
    BudgetPolicyStatus,
    CollectionDecision,
    CompilationManifest,
    CompiledContext,
    CompiledMessage,
    ContextContractError,
    ContextPacket,
    ContextSection,
    Freshness,
    Lane,
    Lifetime,
    ProvenanceRef,
    ResolvedContextPacket,
    ResolvedContextRequest,
    ResolvedContextScope,
    Route,
    SelectionDecision,
    SemanticRole,
    SourceRef,
    SourceSnapshot,
    UnresolvedContextRequest,
    _make_resolved_context_request,
    _make_resolved_context_scope,
    freeze_payload,
    require_resolved_context_request,
)
from nana.runtime.public_context_boundary import PublicEventScope  # noqa: E402
from nana.runtime.public_identity import CanonicalPublicIdentity  # noqa: E402


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _public_scope() -> PublicEventScope:
    identity = CanonicalPublicIdentity(
        platform="youtube",
        author_id="author-1",
        actor_key="youtube:author-1",
    )
    return PublicEventScope(
        platform="youtube",
        room_id="room-1",
        stream_session_id="stream-1",
        event_id="event-1",
        display_name="Fixture Viewer",
        identity=identity,
    )


def _raw_request(
    *,
    route: Route = Route.INTERACTIVE,
    metadata: object | None = None,
) -> UnresolvedContextRequest:
    return UnresolvedContextRequest(
        request_id="request-1",
        correlation_id="correlation-1",
        route=route,
        model="fixture-model",
        current_input="hello",
        viewer_name=None,
        stream_mode=False,
        public_platform=None,
        caller_metadata={} if metadata is None else metadata,
        bridge_system=False,
        story_mode=False,
        casual_mode=True,
        temporal_intent=False,
        grounding_intent=False,
    )


def _resolved_request(
    *,
    lane: Lane = Lane.PRIVATE_OWNER,
    route: Route = Route.INTERACTIVE,
    public_scope: PublicEventScope | None = None,
    public_context: object | None = None,
) -> ResolvedContextRequest:
    interaction_scope = {
        Lane.PRIVATE_OWNER: "private_owner",
        Lane.PUBLIC_STAGE: "public_viewer",
        Lane.OPERATOR_BACKSTAGE: "bridge_system",
    }[lane]
    scope = _make_resolved_context_scope(
        lane=lane,
        interaction_scope=interaction_scope,
        public_scope=public_scope,
        policy_id=f"{lane.value}.v1",
        resolver_version="resolver.v1",
    )
    return _make_resolved_context_request(
        unresolved=_raw_request(route=route),
        scope=scope,
        public_context={} if public_context is None else public_context,
        captured_wall_time=1_700_000_000.0,
        captured_monotonic_time=1234.5,
        compiler_config_revision="compiler-config.v1",
    )


def _section(
    section_id: str = "section-1",
    *,
    visibility: object | None = None,
    payload: object | None = None,
    observed_at: float | None = 100.0,
    expires_at: float | None = 200.0,
    relevance: float | None = 0.5,
) -> ContextSection:
    return ContextSection(
        id=section_id,
        lifetime=Lifetime.TURN,
        semantic_role=SemanticRole.STATE,
        freshness=Freshness.FRESH,
        visibility={Lane.PRIVATE_OWNER} if visibility is None else visibility,
        source=SourceRef(
            owner="awareness",
            adapter="awareness.v1",
            projection="current_situation.v1",
        ),
        revision="source-revision-1",
        authority="source_state",
        observed_at=observed_at,
        expires_at=expires_at,
        conflict_key="current.situation",
        dedupe_key=f"dedupe:{section_id}",
        max_tokens=200,
        payload={"nested": "value"} if payload is None else payload,
        formatter_version="formatter.v1",
        required=False,
        budget_class="current_situation",
        semantic_status="active",
        provenance=(
            ProvenanceRef(
                source_id="awareness",
                source_event_id="event-1",
                record_id="record-1",
            ),
        ),
        relevance=relevance,
    )


def _packet(*sections: ContextSection) -> ContextPacket:
    request = _resolved_request()
    packet_sections = sections or (_section(),)
    return ContextPacket(
        schema_version=1,
        capture_id="capture-1",
        snapshot_revision=1,
        request=request,
        source_revisions=(("awareness", "source-revision-1"),),
        sections=packet_sections,
        collection_receipt=(
            CollectionDecision(
                source="awareness",
                decision="ALLOW_READ",
                reason="lane_policy",
                source_revision="source-revision-1",
            ),
        ),
    )


def _selection_decision(
    section: ContextSection | None = None,
    *,
    decision: str = "included",
    reason: str = "selected",
    chars: int | None = 20,
    token_estimate: int | None = 8,
    related_section_id: str | None = None,
) -> SelectionDecision:
    selected = _section() if section is None else section
    return SelectionDecision(
        section_id=selected.id,
        decision=decision,
        reason=reason,
        source=selected.source,
        lifetime=selected.lifetime,
        semantic_role=selected.semantic_role,
        revision=selected.revision,
        formatter_version=selected.formatter_version,
        chars=chars,
        token_estimate=token_estimate,
        budget_class=selected.budget_class,
        related_section_id=related_section_id,
    )


def _manifest(**changes: object) -> CompilationManifest:
    values: dict[str, object] = {
        "request_id": "request-1",
        "correlation_id": "correlation-1",
        "capture_id": "capture-1",
        "snapshot_revision": 1,
        "model": "fixture-model",
        "resolved_model": None,
        "resolved_provider": "fixture-provider",
        "lane": Lane.PRIVATE_OWNER,
        "route": Route.INTERACTIVE,
        "collection_policy_id": "private_owner.v1",
        "budget_policy_id": "private_interactive.v1",
        "budget_policy_revision": "candidate.2026-10-01",
        "budget_policy_status": BudgetPolicyStatus.SHADOW_CANDIDATE,
        "budget_enforced": False,
        "candidate_overflow": False,
        "compiler_version": "compiler.v1",
        "compiler_config_revision": "compiler-config.v1",
        "wire_profile": "legacy_system_user.v1",
        "static_prefix_hash": SHA_A,
        "durable_prefix_hash": SHA_B,
        "session_prefix_hash": SHA_C,
        "full_context_hash": SHA_D,
        "prior_compatible_request_id": None,
        "prefix_break_section_id": None,
        "prefix_comparison_status": "no_prior",
        "source_revisions": (("awareness", "source-revision-1"),),
        "collection": (
            CollectionDecision(
                source="awareness",
                decision="ALLOW_READ",
                reason="lane_policy",
                source_revision="source-revision-1",
            ),
        ),
        "sections": (_selection_decision(),),
        "input_chars": 200,
        "input_tokens_est": 80,
        "static_prefix_chars": 40,
        "static_prefix_tokens_est": 16,
        "durable_prefix_chars": 80,
        "durable_prefix_tokens_est": 32,
        "session_prefix_chars": 120,
        "session_prefix_tokens_est": 48,
        "prior_exact_matching_prefix_chars": 0,
        "prior_exact_matching_prefix_tokens_est": 0,
        "selected_sections": 1,
        "dropped_sections": 0,
    }
    values.update(changes)
    return CompilationManifest(**values)


def _compiled() -> CompiledContext:
    return CompiledContext(
        compiler_version="compiler.v1",
        lane=Lane.PRIVATE_OWNER,
        route=Route.INTERACTIVE,
        model="fixture-model",
        messages=[
            CompiledMessage(role="system", content="system context\n"),
            CompiledMessage(role="user", content="hello"),
        ],
        static_prefix_hash=SHA_A,
        durable_prefix_hash=SHA_B,
        session_prefix_hash=SHA_C,
        full_context_hash=SHA_D,
        static_prefix_chars=40,
        durable_prefix_chars=80,
        session_prefix_chars=120,
        manifest=_manifest(),
    )


def test_enum_wire_values_are_stable_lowercase_strings() -> None:
    assert [member.value for member in Lane] == [
        "private_owner",
        "public_stage",
        "operator_backstage",
    ]
    assert [member.value for member in Route] == [
        "interactive",
        "private_fast",
        "youtube_cum2",
        "autonomy",
    ]
    assert [member.value for member in Lifetime] == [
        "static",
        "durable",
        "session",
        "turn",
    ]
    assert [member.value for member in SemanticRole] == [
        "instruction",
        "identity",
        "fact",
        "evidence",
        "state",
        "history",
    ]
    assert [member.value for member in Freshness] == [
        "fresh",
        "warm",
        "stale",
        "expired",
        "unknown",
    ]
    assert [member.value for member in BudgetPolicyStatus] == [
        "shadow_candidate",
        "approved",
    ]


def test_resolved_types_reject_direct_construction_and_have_init_disabled() -> None:
    assert ResolvedContextScope.__dataclass_params__.init is False
    assert ResolvedContextRequest.__dataclass_params__.init is False

    with pytest.raises(ContextContractError, match="invalid_resolution_seal"):
        ResolvedContextScope()
    with pytest.raises(ContextContractError, match="invalid_resolution_seal"):
        ResolvedContextRequest()


def test_unresolved_request_is_rejected_at_the_trust_boundary() -> None:
    with pytest.raises(ContextContractError, match="unresolved_context_request"):
        require_resolved_context_request(_raw_request())


@pytest.mark.parametrize("seal", [None, object()])
def test_forged_scope_is_rejected_before_use(seal: object | None) -> None:
    forged = object.__new__(ResolvedContextScope)
    object.__setattr__(forged, "lane", Lane.PRIVATE_OWNER)
    object.__setattr__(forged, "interaction_scope", "private_owner")
    object.__setattr__(forged, "public_scope", None)
    object.__setattr__(forged, "policy_id", "private_owner.v1")
    object.__setattr__(forged, "resolver_version", "resolver.v1")
    if seal is not None:
        object.__setattr__(forged, "_resolution_seal", seal)

    request = _resolved_request()
    object.__setattr__(request, "scope", forged)

    with pytest.raises(ContextContractError, match="invalid_resolution_seal"):
        require_resolved_context_request(request)


def test_incomplete_scope_with_a_stolen_seal_fails_with_contract_error() -> None:
    request = _resolved_request()
    forged = object.__new__(ResolvedContextScope)
    object.__setattr__(
        forged,
        "_resolution_seal",
        request.scope._resolution_seal,
    )
    object.__setattr__(request, "scope", forged)

    with pytest.raises(ContextContractError, match="invalid_resolution_seal"):
        require_resolved_context_request(request)


def test_forged_scope_with_copied_real_seal_and_fields_is_rejected() -> None:
    request = _resolved_request()
    genuine = request.scope
    forged = object.__new__(ResolvedContextScope)
    for contract_field in fields(ResolvedContextScope):
        object.__setattr__(
            forged,
            contract_field.name,
            getattr(genuine, contract_field.name),
        )
    object.__setattr__(request, "scope", forged)

    with pytest.raises(ContextContractError, match="invalid_resolution_seal"):
        require_resolved_context_request(request)


@pytest.mark.parametrize("seal", [None, object()])
def test_forged_request_around_genuine_scope_is_rejected(seal: object | None) -> None:
    genuine = _resolved_request()
    forged = object.__new__(ResolvedContextRequest)
    for name in (
        "request_id",
        "correlation_id",
        "scope",
        "route",
        "model",
        "current_input",
        "public_context",
        "story_mode",
        "casual_mode",
        "temporal_intent",
        "grounding_intent",
        "captured_wall_time",
        "captured_monotonic_time",
        "compiler_config_revision",
    ):
        object.__setattr__(forged, name, getattr(genuine, name))
    if seal is not None:
        object.__setattr__(forged, "_resolution_seal", seal)

    with pytest.raises(ContextContractError, match="invalid_resolution_seal"):
        require_resolved_context_request(forged)


def test_forged_request_with_copied_real_seal_and_fields_is_rejected() -> None:
    genuine = _resolved_request()
    forged = object.__new__(ResolvedContextRequest)
    for contract_field in fields(ResolvedContextRequest):
        object.__setattr__(
            forged,
            contract_field.name,
            getattr(genuine, contract_field.name),
        )

    with pytest.raises(ContextContractError, match="invalid_resolution_seal"):
        require_resolved_context_request(forged)


def test_factory_attestation_detects_coherent_post_factory_mutation() -> None:
    request = _resolved_request()
    object.__setattr__(request, "request_id", "request-mutated")
    with pytest.raises(ContextContractError, match="invalid_resolution_seal"):
        require_resolved_context_request(request)

    request = _resolved_request()
    object.__setattr__(request.scope, "policy_id", "private_mutated.v1")
    with pytest.raises(ContextContractError, match="invalid_resolution_seal"):
        require_resolved_context_request(request)


def test_factory_attestation_registry_is_digest_only_and_cleans_up_on_gc() -> None:
    assert hasattr(context_contracts, "_SCOPE_ATTESTATIONS")
    assert hasattr(context_contracts, "_REQUEST_ATTESTATIONS")

    def issue_request() -> tuple[int, int, weakref.ReferenceType[object], weakref.ReferenceType[object]]:
        request = _resolved_request()
        scope = request.scope
        request_entry = context_contracts._REQUEST_ATTESTATIONS[id(request)]
        scope_entry = context_contracts._SCOPE_ATTESTATIONS[id(scope)]
        for entry in (request_entry, scope_entry):
            assert len(entry) == 2
            assert isinstance(entry[0], weakref.ReferenceType)
            assert isinstance(entry[1], bytes)
            assert len(entry[1]) == 32
        return id(request), id(scope), weakref.ref(request), weakref.ref(scope)

    request_id, scope_id, request_ref, scope_ref = issue_request()
    gc.collect()

    assert request_ref() is None
    assert scope_ref() is None
    assert request_id not in context_contracts._REQUEST_ATTESTATIONS
    assert scope_id not in context_contracts._SCOPE_ATTESTATIONS


def test_resolved_request_validator_never_leaks_attribute_errors() -> None:
    forged = object.__new__(ResolvedContextRequest)
    with pytest.raises(ContextContractError, match="invalid_resolution_seal"):
        require_resolved_context_request(forged)


def test_resolved_request_requires_exact_type_and_revalidates_fields() -> None:
    genuine = _resolved_request()

    class RequestSubclass(ResolvedContextRequest):
        __slots__ = ()

    subclass = object.__new__(RequestSubclass)
    for contract_field in fields(ResolvedContextRequest):
        object.__setattr__(
            subclass,
            contract_field.name,
            getattr(genuine, contract_field.name),
        )
    with pytest.raises(ContextContractError, match="unresolved_context_request"):
        require_resolved_context_request(subclass)

    object.__setattr__(genuine, "captured_wall_time", math.inf)
    with pytest.raises(ContextContractError, match="invalid_resolution_seal"):
        require_resolved_context_request(genuine)


@pytest.mark.parametrize(
    ("lane", "route", "has_public_scope", "allowed"),
    [
        (Lane.PRIVATE_OWNER, Route.INTERACTIVE, False, True),
        (Lane.PRIVATE_OWNER, Route.PRIVATE_FAST, False, True),
        (Lane.PRIVATE_OWNER, Route.AUTONOMY, False, True),
        (Lane.PRIVATE_OWNER, Route.YOUTUBE_CUM2, False, False),
        (Lane.PRIVATE_OWNER, Route.INTERACTIVE, True, False),
        (Lane.PUBLIC_STAGE, Route.INTERACTIVE, True, True),
        (Lane.PUBLIC_STAGE, Route.YOUTUBE_CUM2, True, True),
        (Lane.PUBLIC_STAGE, Route.AUTONOMY, True, True),
        (Lane.PUBLIC_STAGE, Route.PRIVATE_FAST, True, False),
        (Lane.PUBLIC_STAGE, Route.INTERACTIVE, False, False),
        (Lane.OPERATOR_BACKSTAGE, Route.INTERACTIVE, False, True),
        (Lane.OPERATOR_BACKSTAGE, Route.PRIVATE_FAST, False, False),
        (Lane.OPERATOR_BACKSTAGE, Route.YOUTUBE_CUM2, False, False),
        (Lane.OPERATOR_BACKSTAGE, Route.AUTONOMY, False, False),
        (Lane.OPERATOR_BACKSTAGE, Route.INTERACTIVE, True, False),
    ],
)
def test_lane_route_public_scope_matrix(
    lane: Lane,
    route: Route,
    has_public_scope: bool,
    allowed: bool,
) -> None:
    scope = _public_scope() if has_public_scope else None
    if allowed:
        request = _resolved_request(lane=lane, route=route, public_scope=scope)
        assert require_resolved_context_request(request) is request
    else:
        with pytest.raises(ContextContractError, match="invalid_lane_route_scope"):
            _resolved_request(lane=lane, route=route, public_scope=scope)


def test_youtube_cum2_requires_a_youtube_public_scope() -> None:
    identity = CanonicalPublicIdentity(
        platform="twitch",
        author_id="author-1",
        actor_key="twitch:author-1",
    )
    twitch_scope = replace(_public_scope(), platform="twitch", identity=identity)
    with pytest.raises(ContextContractError, match="invalid_lane_route_scope"):
        _resolved_request(
            lane=Lane.PUBLIC_STAGE,
            route=Route.YOUTUBE_CUM2,
            public_scope=twitch_scope,
        )


def test_scope_factory_rejects_a_caller_lane_string() -> None:
    with pytest.raises(ContextContractError, match="invalid_lane_route_scope"):
        _make_resolved_context_scope(
            lane="private_owner",
            interaction_scope="private_owner",
            public_scope=None,
            policy_id="private_owner.v1",
            resolver_version="resolver.v1",
        )


@pytest.mark.parametrize(
    ("lane", "interaction_scope"),
    [
        (Lane.PRIVATE_OWNER, "public_viewer"),
        (Lane.PUBLIC_STAGE, "private_owner"),
        (Lane.OPERATOR_BACKSTAGE, "private_owner"),
    ],
)
def test_interaction_scope_must_match_the_resolved_lane(
    lane: Lane,
    interaction_scope: str,
) -> None:
    with pytest.raises(ContextContractError, match="invalid_lane_route_scope"):
        _make_resolved_context_scope(
            lane=lane,
            interaction_scope=interaction_scope,
            public_scope=_public_scope() if lane is Lane.PUBLIC_STAGE else None,
            policy_id=f"{lane.value}.v1",
            resolver_version="resolver.v1",
        )


@pytest.mark.parametrize(
    ("lane", "policy_id"),
    [
        (Lane.PRIVATE_OWNER, "public_interactive.v1"),
        (Lane.PUBLIC_STAGE, "private_interactive.v1"),
        (Lane.OPERATOR_BACKSTAGE, "public_operator.v1"),
    ],
)
def test_policy_family_must_match_the_resolved_lane(
    lane: Lane,
    policy_id: str,
) -> None:
    with pytest.raises(ContextContractError, match="invalid_lane_route_scope"):
        _make_resolved_context_scope(
            lane=lane,
            interaction_scope={
                Lane.PRIVATE_OWNER: "private_owner",
                Lane.PUBLIC_STAGE: "public_viewer",
                Lane.OPERATOR_BACKSTAGE: "bridge_system",
            }[lane],
            public_scope=_public_scope() if lane is Lane.PUBLIC_STAGE else None,
            policy_id=policy_id,
            resolver_version="resolver.v1",
        )


@pytest.mark.parametrize(
    ("field_name", "fallback"),
    [
        ("room_id", "public"),
        ("stream_session_id", "legacy"),
        ("event_id", "event"),
    ],
)
def test_public_scope_rejects_synthesized_legacy_placeholders(
    field_name: str,
    fallback: str,
) -> None:
    public_scope = replace(_public_scope(), **{field_name: fallback})
    with pytest.raises(ContextContractError, match="invalid_lane_route_scope"):
        _make_resolved_context_scope(
            lane=Lane.PUBLIC_STAGE,
            interaction_scope="public_viewer",
            public_scope=public_scope,
            policy_id="public_stage.v1",
            resolver_version="resolver.v1",
        )


@pytest.mark.parametrize(
    "public_scope",
    [
        replace(_public_scope(), room_id=" room-1 "),
        replace(_public_scope(), display_name=""),
        replace(_public_scope(), display_name="viewer"),
        replace(_public_scope(), display_name="legacy"),
        replace(_public_scope(), event_id="x" * 257),
        replace(_public_scope(), platform=1.0),
        replace(
            _public_scope(),
            identity=CanonicalPublicIdentity(
                platform="twitch",
                author_id="author-1",
                actor_key="twitch:author-1",
            ),
        ),
        replace(
            _public_scope(),
            identity=CanonicalPublicIdentity(
                platform="youtube",
                author_id="author-1",
                actor_key="youtube:someone-else",
            ),
        ),
        replace(
            _public_scope(),
            identity=CanonicalPublicIdentity(
                platform="youtube",
                author_id="anonymous:event-1",
                actor_key="youtube:anonymous:event-1",
            ),
        ),
        replace(
            _public_scope(),
            platform="public_chat",
            identity=CanonicalPublicIdentity(
                platform="public_chat",
                author_id="author-1",
                actor_key="public_chat:author-1",
            ),
        ),
        replace(_public_scope(), event_id="fallback-event-1"),
        replace(_public_scope(), event_id="request-generated-event"),
    ],
)
def test_public_scope_rejects_malformed_or_noncanonical_values(
    public_scope: PublicEventScope,
) -> None:
    with pytest.raises(ContextContractError, match="invalid_lane_route_scope"):
        _make_resolved_context_scope(
            lane=Lane.PUBLIC_STAGE,
            interaction_scope="public_viewer",
            public_scope=public_scope,
            policy_id="public_stage.v1",
            resolver_version="resolver.v1",
        )


def test_public_scope_and_identity_require_exact_contract_types() -> None:
    class ScopeSubclass(PublicEventScope):
        pass

    class IdentitySubclass(CanonicalPublicIdentity):
        pass

    scope = _public_scope()
    scope_subclass = ScopeSubclass(
        scope.platform,
        scope.room_id,
        scope.stream_session_id,
        scope.event_id,
        scope.display_name,
        scope.identity,
    )
    identity_subclass = IdentitySubclass(
        scope.identity.platform,
        scope.identity.author_id,
        scope.identity.actor_key,
    )

    for malformed in (
        scope_subclass,
        replace(scope, identity=identity_subclass),
    ):
        with pytest.raises(ContextContractError, match="invalid_lane_route_scope"):
            _make_resolved_context_scope(
                lane=Lane.PUBLIC_STAGE,
                interaction_scope="public_viewer",
                public_scope=malformed,
                policy_id="public_stage.v1",
                resolver_version="resolver.v1",
            )


def test_scope_factory_converts_incomplete_public_scope_to_contract_error() -> None:
    forged = object.__new__(PublicEventScope)
    object.__setattr__(forged, "platform", "youtube")

    with pytest.raises(ContextContractError, match="invalid_lane_route_scope"):
        _make_resolved_context_scope(
            lane=Lane.PUBLIC_STAGE,
            interaction_scope="public_viewer",
            public_scope=forged,
            policy_id="public_stage.v1",
            resolver_version="resolver.v1",
        )


def test_resolved_request_has_no_raw_routing_or_unresolved_fields() -> None:
    field_names = {field.name for field in fields(ResolvedContextRequest)}
    assert field_names.isdisjoint(
        {
            "unresolved",
            "viewer_name",
            "stream_mode",
            "bridge_system",
            "caller_metadata",
        }
    )
    request = _resolved_request()
    for forbidden in (
        "unresolved",
        "viewer_name",
        "stream_mode",
        "bridge_system",
        "caller_metadata",
    ):
        assert not hasattr(request, forbidden)


def test_resolved_factory_copies_fields_without_retaining_raw_inputs() -> None:
    metadata = {"lane": "public_stage", "nested": ["original"]}
    public_context = {"display_name": "Viewer", "tags": ["one"]}
    raw = _raw_request(route=Route.INTERACTIVE, metadata=metadata)
    scope = _make_resolved_context_scope(
        lane=Lane.PUBLIC_STAGE,
        interaction_scope="public_viewer",
        public_scope=_public_scope(),
        policy_id="public_stage.v1",
        resolver_version="resolver.v1",
    )
    resolved = _make_resolved_context_request(
        unresolved=raw,
        scope=scope,
        public_context=public_context,
        captured_wall_time=1_700_000_000.0,
        captured_monotonic_time=1234.5,
        compiler_config_revision="compiler-config.v1",
    )

    metadata["nested"].append("mutated")
    public_context["tags"].append("mutated")

    assert resolved.request_id == "request-1"
    assert resolved.route is Route.INTERACTIVE
    assert resolved.public_context["tags"] == ("one",)
    assert raw.caller_metadata["nested"] == ("original",)
    assert not hasattr(resolved, "caller_metadata")
    assert not hasattr(resolved, "unresolved")


def test_recursive_freeze_copies_nested_values_and_sorts_mapping_keys() -> None:
    raw = {
        "z": [{"beta": {3, 1}, "alpha": ["kept"]}],
        "a": {"second": 2, "first": 1},
    }
    frozen = freeze_payload(raw)
    raw["z"][0]["alpha"].append("mutated")
    raw["a"]["third"] = 3

    assert tuple(frozen) == ("a", "z")
    assert tuple(frozen["a"]) == ("first", "second")
    assert frozen["z"][0]["alpha"] == ("kept",)
    assert frozen["z"][0]["beta"] == (1, 3)

    with pytest.raises(TypeError):
        frozen["new"] = "value"
    with pytest.raises(TypeError):
        frozen["z"][0]["alpha"][0] = "changed"


def test_recursive_freeze_recopies_existing_frozen_mapping() -> None:
    first = freeze_payload({"nested": ["value"]})
    second = freeze_payload(first)

    assert second is not first
    assert second["nested"] == ("value",)


def test_recursive_freeze_repairs_mutated_frozen_mapping_without_retaining_list() -> None:
    frozen = freeze_payload({"nested": ["original"]})
    live_list = ["mutated-source"]
    object.__setattr__(frozen, "_items", (("nested", live_list),))

    repaired = freeze_payload(frozen)
    live_list.append("later-mutation")

    assert repaired is not frozen
    assert repaired["nested"] == ("mutated-source",)


@pytest.mark.parametrize(
    "value",
    [
        type("DictSubclass", (dict,), {})({"key": "value"}),
        type("ListSubclass", (list,), {})(["value"]),
        type("TupleSubclass", (tuple,), {})(("value",)),
        type("SetSubclass", (set,), {})({"value"}),
        type("StringSubclass", (str,), {})("value"),
    ],
)
def test_recursive_freeze_rejects_builtin_container_and_string_subclasses(
    value: object,
) -> None:
    with pytest.raises(ContextContractError, match="unsupported_frozen_payload"):
        freeze_payload(value)


def test_recursive_freeze_rejects_frozen_mapping_subclass() -> None:
    class FrozenMappingSubclass(context_contracts.FrozenMapping):
        pass

    value = FrozenMappingSubclass((("key", "value"),))
    with pytest.raises(ContextContractError, match="unsupported_frozen_payload"):
        freeze_payload(value)


def test_set_freezing_is_stable_across_hash_seeds() -> None:
    script = (
        "from nana.runtime.context_contracts import freeze_payload; "
        "print('|'.join(freeze_payload({'items': {'zeta', 'alpha', 'mu'}})['items']))"
    )
    outputs: list[str] = []
    for seed in ("1", "987654"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-B", "-c", script],
            cwd=ROOT.parent,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(completed.stdout.strip())

    assert outputs == ["alpha|mu|zeta", "alpha|mu|zeta"]


def test_context_payload_is_deeply_immutable() -> None:
    section = _section(payload={"nested": {"items": ["value"]}})

    with pytest.raises(TypeError):
        section.payload["nested"] = "changed"
    with pytest.raises(TypeError):
        section.payload["nested"]["items"][0] = "changed"


class _MutableFixtureObject:
    def __init__(self) -> None:
        self.value = "mutable"


@pytest.mark.parametrize(
    "value",
    [_MutableFixtureObject(), bytearray(b"mutable")],
)
def test_recursive_freeze_rejects_unsupported_mutable_objects(value: object) -> None:
    with pytest.raises(ContextContractError, match="unsupported_frozen_payload"):
        freeze_payload({"value": value})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_recursive_freeze_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ContextContractError, match="non_finite_number"):
        freeze_payload({"value": value})


def test_recursive_freeze_rejects_cycles_and_non_string_mapping_keys() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(ContextContractError, match="cyclic_frozen_payload"):
        freeze_payload(cyclic)
    with pytest.raises(ContextContractError, match="invalid_mapping_key"):
        freeze_payload({1: "not-a-string-key"})


def test_recursive_freeze_rejects_nfc_duplicate_mapping_keys() -> None:
    with pytest.raises(ContextContractError, match="duplicate_mapping_key"):
        freeze_payload({"\u00e9": 1, "e\u0301": 2})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -1.0])
def test_timestamps_must_be_finite_and_non_negative(value: float) -> None:
    with pytest.raises(ContextContractError, match="invalid_timestamp"):
        SourceSnapshot(
            source="awareness",
            revision="revision-1",
            observed_at=None,
            captured_at=value,
            freshness=Freshness.UNKNOWN,
            payload={},
        )


def test_timestamp_order_is_validated() -> None:
    with pytest.raises(ContextContractError, match="invalid_timestamp_order"):
        SourceSnapshot(
            source="awareness",
            revision="revision-1",
            observed_at=11.0,
            captured_at=10.0,
            freshness=Freshness.FRESH,
            payload={},
        )
    with pytest.raises(ContextContractError, match="invalid_timestamp_order"):
        _section(observed_at=20.0, expires_at=10.0)


def test_boolean_values_are_not_accepted_as_numeric_totals_or_timestamps() -> None:
    with pytest.raises(ContextContractError, match="invalid_timestamp"):
        SourceSnapshot(
            source="awareness",
            revision="revision-1",
            observed_at=None,
            captured_at=True,
            freshness=Freshness.UNKNOWN,
            payload={},
        )
    with pytest.raises(ContextContractError, match="invalid_budget_limit"):
        BudgetPolicy(
            policy_id="candidate.v1",
            revision="revision-1",
            status=BudgetPolicyStatus.SHADOW_CANDIDATE,
            max_estimated_tokens=True,
            max_characters=40,
        )
    with pytest.raises(ContextContractError, match="invalid_total"):
        _manifest(input_chars=True)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, -0.1, 1.1])
def test_relevance_must_be_a_finite_unit_interval(value: float) -> None:
    with pytest.raises(ContextContractError, match="invalid_relevance"):
        _section(relevance=value)


def test_context_packet_rejects_duplicate_section_and_source_ids() -> None:
    first = _section("duplicate")
    second = _section("duplicate")
    with pytest.raises(ContextContractError, match="duplicate_section_id"):
        _packet(first, second)

    packet = _packet()
    with pytest.raises(ContextContractError, match="duplicate_source_id"):
        replace(
            packet,
            source_revisions=(
                ("awareness", "revision-1"),
                ("awareness", "revision-2"),
            ),
        )

    decision = packet.collection_receipt[0]
    with pytest.raises(ContextContractError, match="duplicate_source_id"):
        replace(packet, collection_receipt=(decision, decision))


def test_context_packet_revalidates_forged_nested_records() -> None:
    base_section = _section()

    forged_source = object.__new__(SourceRef)
    object.__setattr__(forged_source, "owner", "awareness")
    object.__setattr__(base_section, "source", forged_source)
    with pytest.raises(ContextContractError):
        _packet(base_section)

    section = _section()
    forged_provenance = object.__new__(ProvenanceRef)
    object.__setattr__(forged_provenance, "source_id", "awareness")
    object.__setattr__(section, "provenance", (forged_provenance,))
    with pytest.raises(ContextContractError):
        _packet(section)

    packet = _packet()
    forged_receipt = object.__new__(CollectionDecision)
    object.__setattr__(forged_receipt, "source", "awareness")
    with pytest.raises(ContextContractError):
        replace(packet, collection_receipt=(forged_receipt,))


def test_context_packet_recopies_nested_payloads() -> None:
    section = _section(payload={"items": ["original"]})
    live_list = ["forged"]
    object.__setattr__(section.payload, "_items", (("items", live_list),))

    packet = _packet(section)
    live_list.append("later")

    assert packet.sections[0] is not section
    assert packet.sections[0].payload["items"] == ("forged",)


def test_visibility_requires_lanes_and_must_include_the_request_lane() -> None:
    with pytest.raises(ContextContractError, match="invalid_visibility"):
        _section(visibility=set())
    with pytest.raises(ContextContractError, match="invalid_visibility"):
        _section(visibility={"private_owner"})

    private_invisible = _section(visibility={Lane.PUBLIC_STAGE})
    with pytest.raises(ContextContractError, match="invalid_visibility"):
        _packet(private_invisible)


def test_visibility_is_copied_to_a_frozenset() -> None:
    visibility = {Lane.PRIVATE_OWNER}
    section = _section(visibility=visibility)
    visibility.add(Lane.PUBLIC_STAGE)

    assert section.visibility == frozenset({Lane.PRIVATE_OWNER})


def test_budget_policy_status_is_metadata_and_never_authorizes_enforcement() -> None:
    candidate = BudgetPolicy(
        policy_id="private_interactive.v1",
        revision="candidate.2026-10-01",
        status=BudgetPolicyStatus.SHADOW_CANDIDATE,
        max_estimated_tokens=12_000,
        max_characters=48_000,
    )
    approved = replace(
        candidate,
        revision="approved.2026-10-01",
        status=BudgetPolicyStatus.APPROVED,
    )

    for policy in (candidate, approved):
        assert not hasattr(policy, "is_approved")
        assert not hasattr(policy, "allows_enforcement")
        assert not hasattr(policy, "authorize_enforcement")
    assert not hasattr(context_contracts, "budget_policy_allows_enforcement")
    assert not hasattr(context_contracts, "require_budget_enforcement")


def test_manifest_cannot_claim_budget_enforcement_without_v1_metadata() -> None:
    with pytest.raises(ContextContractError, match="invalid_budget_enforcement"):
        _manifest(
            budget_policy_status=BudgetPolicyStatus.APPROVED,
            budget_enforced=True,
        )


def test_manifest_records_candidate_overflow_as_metadata_only() -> None:
    manifest = _manifest(candidate_overflow=True)
    assert manifest.candidate_overflow is True
    assert manifest.budget_enforced is False


@pytest.mark.parametrize(
    "reason",
    [
        "raw user input",
        "Viewer said hello",
        "viewer_said_hello",
        "arbitrary_code",
        "secret:credential",
        "secret-token",
        "sk-proj-abc123",
        "api_key:credential",
        "raw_input:private_text",
        "raw-input:private",
        "x" * 129,
    ],
)
def test_collection_reason_rejects_free_form_or_sensitive_content(reason: str) -> None:
    with pytest.raises(ContextContractError, match="invalid_collection_reason"):
        CollectionDecision(
            source="awareness",
            decision="ALLOW_READ",
            reason=reason,
            source_revision="source-revision-1",
        )


@pytest.mark.parametrize(
    "reason",
    [
        "raw user input",
        "Viewer said hello",
        "viewer_said_hello",
        "arbitrary_code",
        "secret:credential",
        "secret-token",
        "sk-proj-abc123",
        "password:credential",
        "user_input:private_text",
        "raw-input:private",
        "x" * 129,
    ],
)
def test_selection_reason_rejects_free_form_or_sensitive_content(reason: str) -> None:
    with pytest.raises(ContextContractError, match="invalid_selection_reason"):
        _selection_decision(reason=reason)


def test_collection_reason_accepts_only_approved_policy_codes() -> None:
    cases = (
        ("lane_policy", "ALLOW_READ", "source-revision-1"),
        ("condition_met", "CONDITIONAL_READ", "source-revision-1"),
        ("condition_not_met", "CONDITIONAL_SKIP", None),
        ("source_unavailable", "CONDITIONAL_SKIP", None),
        ("source_error", "CONDITIONAL_SKIP", None),
        ("public_lane_denied", "DENY_NOT_READ", None),
    )
    for reason, decision, revision in cases:
        receipt = CollectionDecision(
            source=f"source_{reason}",
            decision=decision,
            reason=reason,
            source_revision=revision,
        )
        assert receipt.reason == reason


def test_selection_reason_accepts_only_approved_policy_codes() -> None:
    cases = (
        ("selected", "included", None),
        ("required", "included", None),
        ("lane_visibility", "denied", None),
        ("expired", "expired", None),
        ("not_relevant", "irrelevant", None),
        ("conflict_loser", "conflict_loser", "winner"),
        ("duplicate", "duplicate", "winner"),
        ("section_budget", "section_budget", None),
        ("global_budget", "global_budget", None),
        ("invalid_section", "invalid", None),
    )
    for reason, decision, related_id in cases:
        row = _selection_decision(
            decision=decision,
            reason=reason,
            related_section_id=related_id,
        )
        assert row.reason == reason


def test_status_and_identifier_validation_fail_closed() -> None:
    with pytest.raises(ContextContractError, match="invalid_id"):
        SourceRef(owner="", adapter="adapter.v1", projection="projection.v1")
    with pytest.raises(ContextContractError, match="invalid_collection_decision"):
        CollectionDecision(
            source="awareness",
            decision="READ_THEN_DROP",
            reason="invalid",
            source_revision=None,
        )
    with pytest.raises(ContextContractError, match="invalid_selection_decision"):
        _selection_decision(decision="truncate", reason="invalid")
    with pytest.raises(ContextContractError, match="invalid_budget_status"):
        BudgetPolicy(
            policy_id="policy.v1",
            revision="revision-1",
            status="approved",
            max_estimated_tokens=1,
            max_characters=1,
        )


def test_manifest_validates_hashes_comparison_status_and_totals() -> None:
    with pytest.raises(ContextContractError, match="invalid_hash"):
        _manifest(full_context_hash="not-a-sha256")
    with pytest.raises(ContextContractError, match="invalid_hash"):
        _manifest(full_context_hash="A" * 64)
    with pytest.raises(ContextContractError, match="invalid_prefix_comparison_status"):
        _manifest(prefix_comparison_status="same_enough")
    with pytest.raises(ContextContractError, match="invalid_prefix_break_section_id"):
        _manifest(
            prefix_comparison_status="compatible_changed",
            prior_compatible_request_id="request-0",
            prefix_break_section_id=None,
        )
    with pytest.raises(ContextContractError, match="invalid_total"):
        _manifest(input_chars=-1)
    with pytest.raises(ContextContractError, match="invalid_total"):
        _manifest(selected_sections=0, dropped_sections=0)


@pytest.mark.parametrize(
    ("status", "break_id"),
    [
        ("compatible_equal", None),
        ("compatible_changed", "current_input"),
    ],
)
def test_manifest_accepts_framed_prior_prefix_counts_above_session_content(
    status: str,
    break_id: str | None,
) -> None:
    manifest = _manifest(
        prefix_comparison_status=status,
        prior_compatible_request_id="d" * 64,
        prefix_break_section_id=break_id,
        prior_exact_matching_prefix_chars=150,
        prior_exact_matching_prefix_tokens_est=60,
    )

    assert manifest.prior_exact_matching_prefix_chars == 150
    assert manifest.prior_exact_matching_prefix_chars > manifest.session_prefix_chars
    assert manifest.prior_exact_matching_prefix_tokens_est == 60
    assert (
        manifest.prior_exact_matching_prefix_tokens_est
        > manifest.session_prefix_tokens_est
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "prior_exact_matching_prefix_chars",
        "prior_exact_matching_prefix_tokens_est",
    ],
)
def test_manifest_still_rejects_negative_framed_prior_prefix_counts(
    field_name: str,
) -> None:
    with pytest.raises(ContextContractError, match="invalid_total"):
        _manifest(**{field_name: -1})


def test_manifest_rejects_duplicate_source_and_section_ids() -> None:
    with pytest.raises(ContextContractError, match="duplicate_source_id"):
        _manifest(
            source_revisions=(
                ("awareness", "revision-1"),
                ("awareness", "revision-2"),
            )
        )
    with pytest.raises(ContextContractError, match="duplicate_section_id"):
        section = _section("section-1")
        _manifest(
            sections=(
                _selection_decision(section),
                _selection_decision(
                    section,
                    decision="duplicate",
                    reason="duplicate",
                    related_section_id="section-0",
                ),
            ),
            selected_sections=1,
            dropped_sections=1,
        )


def test_manifest_collection_policy_family_must_match_lane() -> None:
    with pytest.raises(ContextContractError, match="invalid_lane_route_scope"):
        _manifest(collection_policy_id="public_interactive.v1")


def test_manifest_related_section_must_target_another_manifest_row() -> None:
    winner = _section("winner")
    duplicate = _section("duplicate")
    with pytest.raises(ContextContractError, match="invalid_related_section_id"):
        _manifest(
            sections=(
                _selection_decision(winner),
                _selection_decision(
                    duplicate,
                    decision="duplicate",
                    reason="duplicate",
                    related_section_id="phantom",
                ),
            ),
            selected_sections=1,
            dropped_sections=1,
        )


def test_manifest_source_revisions_match_read_receipts_and_sections() -> None:
    mismatched_read = CollectionDecision(
        source="awareness",
        decision="ALLOW_READ",
        reason="lane_policy",
        source_revision="other-revision",
    )
    with pytest.raises(ContextContractError, match="invalid_source_revision"):
        _manifest(collection=(mismatched_read,))

    with pytest.raises(ContextContractError, match="invalid_source_revision"):
        _manifest(
            source_revisions=(
                ("awareness", "source-revision-1"),
                ("unused_source", "source-revision-1"),
            )
        )

    row = _selection_decision()
    other_source = replace(
        row,
        source=SourceRef("other_source", "other.v1", "other.v1"),
    )
    with pytest.raises(ContextContractError, match="invalid_source_revision"):
        _manifest(sections=(other_source,))

    denied = CollectionDecision(
        source="awareness",
        decision="DENY_NOT_READ",
        reason="public_lane_denied",
        source_revision=None,
    )
    with pytest.raises(ContextContractError, match="invalid_source_revision"):
        _manifest(
            source_revisions=(),
            collection=(denied,),
        )

    revision_mismatch = replace(row, revision="other-revision")
    with pytest.raises(ContextContractError, match="invalid_source_revision"):
        _manifest(sections=(revision_mismatch,))


def test_context_packet_source_revisions_match_receipts_and_sections() -> None:
    packet = _packet()
    mismatched_read = replace(
        packet.collection_receipt[0],
        source_revision="other-revision",
    )
    with pytest.raises(ContextContractError, match="invalid_source_revision"):
        replace(packet, collection_receipt=(mismatched_read,))

    other_source_section = replace(
        packet.sections[0],
        source=SourceRef("other_source", "other.v1", "other.v1"),
    )
    with pytest.raises(ContextContractError, match="invalid_source_revision"):
        replace(packet, sections=(other_source_section,))

    denied = CollectionDecision(
        source="awareness",
        decision="DENY_NOT_READ",
        reason="public_lane_denied",
        source_revision=None,
    )
    with pytest.raises(ContextContractError, match="invalid_source_revision"):
        replace(
            packet,
            source_revisions=(),
            collection_receipt=(denied,),
        )

    revision_mismatch = replace(
        packet.sections[0],
        revision="other-revision",
    )
    with pytest.raises(ContextContractError, match="invalid_source_revision"):
        replace(packet, sections=(revision_mismatch,))


def test_denied_and_skipped_sources_have_no_revision_or_sections() -> None:
    receipt = (
        CollectionDecision(
            source="private_memory",
            decision="DENY_NOT_READ",
            reason="public_lane_denied",
            source_revision=None,
        ),
        CollectionDecision(
            source="optional_awareness",
            decision="CONDITIONAL_SKIP",
            reason="condition_not_met",
            source_revision=None,
        ),
    )
    packet = ContextPacket(
        schema_version=1,
        capture_id="capture-empty",
        snapshot_revision=1,
        request=_resolved_request(),
        source_revisions=(),
        sections=(),
        collection_receipt=receipt,
    )
    manifest = _manifest(
        source_revisions=(),
        collection=receipt,
        sections=(),
        selected_sections=0,
        dropped_sections=0,
    )

    assert packet.sections == ()
    assert manifest.sections == ()


def test_selection_decision_validates_relationships_and_rendered_totals() -> None:
    section = _section()
    with pytest.raises(ContextContractError, match="invalid_related_section_id"):
        _selection_decision(
            section,
            decision="duplicate",
            reason="duplicate",
            related_section_id=None,
        )
    with pytest.raises(ContextContractError, match="invalid_related_section_id"):
        _selection_decision(section, related_section_id="other-section")
    with pytest.raises(ContextContractError, match="invalid_total"):
        _selection_decision(section, chars=-1)


def test_related_selection_decision_must_reference_a_candidate_section() -> None:
    winner = _section("winner")
    duplicate = _section("duplicate")
    packet = _packet(winner, duplicate)
    decisions = (
        _selection_decision(winner),
        _selection_decision(
            duplicate,
            decision="duplicate",
            reason="duplicate",
            related_section_id="not-a-candidate",
        ),
    )
    with pytest.raises(ContextContractError, match="selection_decision_mismatch"):
        ResolvedContextPacket(
            packet=packet,
            selected_sections=(winner,),
            decisions=decisions,
            total_estimated_tokens=10,
        )


def test_manifest_requires_complete_rendered_section_metadata() -> None:
    with pytest.raises(ContextContractError, match="invalid_manifest_section"):
        _manifest(sections=(_selection_decision(chars=None),))
    with pytest.raises(ContextContractError, match="invalid_manifest_section"):
        _manifest(sections=(_selection_decision(token_estimate=None),))


def test_manifest_carries_content_free_policy_and_provider_metadata() -> None:
    manifest = _manifest()
    assert manifest.resolved_provider == "fixture-provider"
    assert manifest.collection_policy_id == "private_owner.v1"
    assert manifest.budget_policy_id == "private_interactive.v1"
    assert manifest.budget_policy_revision == "candidate.2026-10-01"
    assert manifest.budget_policy_status is BudgetPolicyStatus.SHADOW_CANDIDATE
    assert manifest.budget_enforced is False
    assert manifest.candidate_overflow is False
    decision = manifest.sections[0]
    assert decision.source.owner == "awareness"
    assert decision.lifetime is Lifetime.TURN
    assert decision.semantic_role is SemanticRole.STATE
    assert decision.revision == "source-revision-1"
    assert decision.formatter_version == "formatter.v1"
    assert decision.chars == 20
    assert decision.token_estimate == 8
    assert decision.budget_class == "current_situation"


def test_compiled_messages_and_manifest_are_frozen_copies() -> None:
    compiled = _compiled()
    assert isinstance(compiled.messages, tuple)

    with pytest.raises(FrozenInstanceError):
        compiled.messages[0].content = "changed"
    with pytest.raises(FrozenInstanceError):
        compiled.manifest.full_context_hash = SHA_A
    with pytest.raises(FrozenInstanceError):
        compiled.route = Route.AUTONOMY


def test_compiled_context_rejects_hash_or_metadata_mismatch() -> None:
    compiled = _compiled()
    with pytest.raises(ContextContractError, match="compiled_manifest_mismatch"):
        replace(compiled, full_context_hash=SHA_A)
    with pytest.raises(ContextContractError, match="compiled_manifest_mismatch"):
        replace(compiled, route=Route.AUTONOMY)


def test_compiled_context_revalidates_mutated_manifest() -> None:
    compiled = _compiled()
    object.__setattr__(compiled.manifest, "budget_enforced", True)

    with pytest.raises(ContextContractError, match="invalid_budget_enforcement"):
        replace(compiled, manifest=compiled.manifest)


def test_manifest_revalidates_mutated_decision_and_collection_rows() -> None:
    decision = _selection_decision()
    object.__setattr__(decision, "reason", "raw private input")
    with pytest.raises(ContextContractError, match="invalid_selection_reason"):
        _manifest(sections=(decision,))

    collection = CollectionDecision(
        source="awareness",
        decision="ALLOW_READ",
        reason="lane_policy",
        source_revision="source-revision-1",
    )
    object.__setattr__(collection, "reason", "secret:credential")
    with pytest.raises(ContextContractError, match="invalid_collection_reason"):
        _manifest(collection=(collection,))


def test_compiled_contract_is_exactly_legacy_system_plus_user() -> None:
    with pytest.raises(ContextContractError, match="invalid_message_role"):
        CompiledMessage(role="assistant", content="not permitted in v1")

    compiled = _compiled()
    alternate_manifest = replace(compiled.manifest, wire_profile="alternate.v1")
    with pytest.raises(ContextContractError, match="invalid_wire_profile"):
        replace(compiled, manifest=alternate_manifest)

    with pytest.raises(ContextContractError, match="invalid_wire_profile"):
        replace(
            compiled,
            messages=(CompiledMessage("system", "context"),),
        )


def test_resolved_packet_requires_one_decision_per_candidate() -> None:
    packet = _packet()
    with pytest.raises(ContextContractError, match="selection_decision_mismatch"):
        ResolvedContextPacket(
            packet=packet,
            selected_sections=packet.sections,
            decisions=(),
            total_estimated_tokens=10,
        )
    with pytest.raises(ContextContractError, match="selection_decision_mismatch"):
        ResolvedContextPacket(
            packet=packet,
            selected_sections=packet.sections,
            decisions=(
                _selection_decision(
                    packet.sections[0],
                    decision="denied",
                    reason="lane_visibility",
                ),
            ),
            total_estimated_tokens=10,
        )


def test_resolved_packet_allows_unknown_precompile_render_sizes() -> None:
    packet = _packet()
    decision = _selection_decision(
        packet.sections[0],
        chars=None,
        token_estimate=None,
    )
    resolved = ResolvedContextPacket(
        packet=packet,
        selected_sections=packet.sections,
        decisions=(decision,),
        total_estimated_tokens=10,
    )
    assert resolved.decisions[0].chars is None
    assert resolved.decisions[0].token_estimate is None
    assert resolved.decisions[0].included is True


def test_resolved_packet_revalidates_mutated_nested_records() -> None:
    packet = _packet()
    decision = _selection_decision(packet.sections[0], chars=None, token_estimate=None)
    forged_source = object.__new__(SourceRef)
    object.__setattr__(forged_source, "owner", "awareness")
    object.__setattr__(packet.sections[0], "source", forged_source)
    object.__setattr__(decision, "source", forged_source)

    with pytest.raises(ContextContractError):
        ResolvedContextPacket(
            packet=packet,
            selected_sections=packet.sections,
            decisions=(decision,),
            total_estimated_tokens=10,
        )


def test_all_contract_records_are_frozen_and_slotted() -> None:
    packet = _packet()
    resolved_packet = ResolvedContextPacket(
        packet=packet,
        selected_sections=packet.sections,
        decisions=(
            _selection_decision(packet.sections[0], chars=None, token_estimate=None),
        ),
        total_estimated_tokens=10,
    )
    instances = (
        _raw_request(),
        packet.request.scope,
        packet.request,
        SourceSnapshot(
            source="awareness",
            revision="revision-1",
            observed_at=10.0,
            captured_at=10.0,
            freshness=Freshness.FRESH,
            payload={"state": "focused"},
        ),
        SourceRef("awareness", "awareness.v1", "current_situation.v1"),
        ProvenanceRef("awareness", "event-1", "record-1"),
        packet.sections[0],
        packet.collection_receipt[0],
        BudgetPolicy(
            policy_id="candidate.v1",
            revision="revision-1",
            status=BudgetPolicyStatus.SHADOW_CANDIDATE,
            max_estimated_tokens=10,
            max_characters=40,
        ),
        packet,
        resolved_packet.decisions[0],
        resolved_packet,
        CompiledMessage("system", "context"),
        _manifest(),
        _compiled(),
    )

    for instance in instances:
        assert is_dataclass(instance)
        assert not hasattr(instance, "__dict__")
        first_field = fields(instance)[0].name
        with pytest.raises(FrozenInstanceError):
            setattr(instance, first_field, getattr(instance, first_field))
