"""Behavioral tests for the pure, injected Context Runtime boundary."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))


from nana.runtime.context_adapters import (  # noqa: E402
    OperatorSourceView,
    PrivateSourceView,
    PublicSourceView,
    SourceBatch,
)
from nana.runtime.context_compiler import (  # noqa: E402
    COMPILER_VERSION,
    CompilerConfig,
    ContextCompiler,
)
from nana.runtime.context_contracts import (  # noqa: E402
    BudgetPolicy,
    BudgetPolicyStatus,
    CollectionDecision,
    ContextContractError,
    ContextPacket,
    ContextSection,
    Freshness,
    FrozenMapping,
    Lane,
    Lifetime,
    ProvenanceRef,
    ResolvedContextRequest,
    Route,
    SemanticRole,
    SourceRef,
    UnresolvedContextRequest,
)
from nana.runtime.context_runtime import (  # noqa: E402
    ContextRuntime,
    LaneAwareContextCollector,
    LaneResolver,
    TrustedIngress,
)
from nana.runtime.public_context_boundary import PublicEventScope  # noqa: E402
from nana.runtime.public_identity import CanonicalPublicIdentity  # noqa: E402


CAPTURED_WALL_TIME = 1_700_000_000.0
CAPTURED_MONOTONIC_TIME = 1_234.5
COMPILER_CONFIG_REVISION = "compiler-config.v1"
PRIVATE_SENTINEL = "PRIVATE-TASK5-LEAK-SENTINEL"


class StaticIngressAuthority:
    def __init__(self, ingress: object) -> None:
        self.ingress = ingress
        self.calls = 0
        self.requests: list[UnresolvedContextRequest] = []

    def authorize(self, request: UnresolvedContextRequest) -> object:
        self.calls += 1
        self.requests.append(request)
        return self.ingress


class FakeAdapter:
    def __init__(self, batch: SourceBatch) -> None:
        self.batch = batch
        self.calls = 0

    def read(self, request: ResolvedContextRequest) -> SourceBatch:
        self.calls += 1
        assert isinstance(request, ResolvedContextRequest)
        return self.batch


class AdapterTripwire:
    def __init__(self) -> None:
        self.calls = 0

    def read(self, _request: ResolvedContextRequest) -> SourceBatch:
        self.calls += 1
        raise AssertionError("private adapter tripwire was read")


def _public_scope(
    *,
    platform: str = "youtube",
    room_id: str = "room-task5",
    stream_session_id: str = "stream-task5",
    event_id: str = "event-task5",
    display_name: str = "Fixture Viewer",
    author_id: str = "author-task5",
) -> PublicEventScope:
    return PublicEventScope(
        platform=platform,
        room_id=room_id,
        stream_session_id=stream_session_id,
        event_id=event_id,
        display_name=display_name,
        identity=CanonicalPublicIdentity(
            platform=platform,
            author_id=author_id,
            actor_key=f"{platform}:{author_id}",
        ),
    )


def _raw_request(
    *,
    route: Route = Route.INTERACTIVE,
    public: bool = False,
    operator: bool = False,
    metadata: object | None = None,
    grounding_intent: bool = False,
    temporal_intent: bool = False,
) -> UnresolvedContextRequest:
    return UnresolvedContextRequest(
        request_id="request-task5",
        correlation_id="correlation-task5",
        route=route,
        model="fixture-model",
        current_input="hello from task 5",
        viewer_name="Fixture Viewer" if public else None,
        stream_mode=public,
        public_platform="youtube" if public else None,
        caller_metadata={} if metadata is None else metadata,
        bridge_system=operator,
        story_mode=False,
        casual_mode=True,
        temporal_intent=temporal_intent,
        grounding_intent=grounding_intent,
    )


def _resolver(ingress: object) -> LaneResolver:
    return LaneResolver(
        authority=StaticIngressAuthority(ingress),
        wall_clock=lambda: CAPTURED_WALL_TIME,
        monotonic_clock=lambda: CAPTURED_MONOTONIC_TIME,
        compiler_config_revision=COMPILER_CONFIG_REVISION,
    )


def _resolve_public(
    *,
    route: Route = Route.INTERACTIVE,
    metadata: object | None = None,
    grounding_intent: bool = False,
) -> ResolvedContextRequest:
    scope = _public_scope()
    return _resolver(TrustedIngress(Lane.PUBLIC_STAGE, scope)).resolve(
        _raw_request(
            route=route,
            public=True,
            metadata=metadata,
            grounding_intent=grounding_intent,
        )
    )


def _resolve_private(
    *,
    route: Route = Route.INTERACTIVE,
    temporal_intent: bool = False,
) -> ResolvedContextRequest:
    return _resolver(TrustedIngress(Lane.PRIVATE_OWNER, None)).resolve(
        _raw_request(route=route, temporal_intent=temporal_intent)
    )


def _resolve_operator() -> ResolvedContextRequest:
    return _resolver(TrustedIngress(Lane.OPERATOR_BACKSTAGE, None)).resolve(
        _raw_request(operator=True)
    )


def _section(
    section_id: str,
    lifetime: Lifetime,
    role: SemanticRole,
    *,
    lane: Lane,
    owner: str,
    revision: str,
    payload: object | None = None,
    freshness: Freshness = Freshness.FRESH,
    observed_at: float | None = CAPTURED_WALL_TIME - 10,
    expires_at: float | None = CAPTURED_WALL_TIME + 100,
    authority: str | None = None,
    conflict_key: str | None = None,
    dedupe_key: str | None = None,
    required: bool = False,
    semantic_status: str | None = "active",
    relevance: float | None = 1.0,
) -> ContextSection:
    return ContextSection(
        id=section_id,
        lifetime=lifetime,
        semantic_role=role,
        freshness=freshness,
        visibility=frozenset({lane}),
        source=SourceRef(
            owner=owner,
            adapter=f"{owner}.adapter.v1",
            projection=f"{section_id}.projection.v1",
        ),
        revision=revision,
        authority=authority,
        observed_at=observed_at,
        expires_at=expires_at,
        conflict_key=conflict_key,
        dedupe_key=dedupe_key or f"dedupe:{section_id}",
        max_tokens=2_000,
        payload=f"payload:{section_id}" if payload is None else payload,
        formatter_version="formatter.v1",
        required=required,
        budget_class="fixture",
        semantic_status=semantic_status,
        provenance=(ProvenanceRef(owner, "event-1", section_id),),
        relevance=relevance,
    )


def _batch(owner: str, *sections: ContextSection) -> SourceBatch:
    return SourceBatch(owner=owner, revision=f"{owner}-r1", sections=sections)


def _empty_adapter(owner: str) -> FakeAdapter:
    return FakeAdapter(_batch(owner))


def _core_sections(lane: Lane, route: Route, owner: str = "core_projection") -> tuple[ContextSection, ...]:
    if lane is Lane.PRIVATE_OWNER:
        policy = "policy.private.v1"
        core = "core.private.v1"
        output = (
            "contract.output.autonomy.v1"
            if route is Route.AUTONOMY
            else "contract.output.private.v1"
        )
    elif lane is Lane.PUBLIC_STAGE:
        policy = (
            "policy.public.livestream.v1"
            if route is Route.YOUTUBE_CUM2
            else "policy.public.v1"
        )
        core = "core.public.v1"
        output = (
            "contract.output.cum2.v1"
            if route is Route.YOUTUBE_CUM2
            else "contract.output.autonomy.v1"
            if route is Route.AUTONOMY
            else "contract.output.public.v1"
        )
    else:
        policy = "policy.operator.v1"
        core = "core.operator.v1"
        output = "contract.output.operator.v1"

    section_ids = [core, policy]
    if route is Route.AUTONOMY:
        section_ids.append("policy.autonomy.v1")
    section_ids.append(output)
    return tuple(
        _section(
            section_id,
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            lane=lane,
            owner=owner,
            revision=f"{owner}-r1",
            required=True,
        )
        for section_id in section_ids
    )


def _public_sources(
    *,
    route: Route = Route.INTERACTIVE,
    grounding_adapter: object | None = None,
) -> tuple[PublicSourceView, dict[str, object]]:
    core = FakeAdapter(_batch("core_projection", *_core_sections(Lane.PUBLIC_STAGE, route)))
    public_scope = FakeAdapter(
        _batch(
            "public_scope",
            _section(
                "public.request_context.v1",
                Lifetime.TURN,
                SemanticRole.STATE,
                lane=Lane.PUBLIC_STAGE,
                owner="public_scope",
                revision="public_scope-r1",
            ),
        )
    )
    public_session = FakeAdapter(
        _batch(
            "social_session",
            _section(
                "continuity.public_room.v1",
                Lifetime.SESSION,
                SemanticRole.HISTORY,
                lane=Lane.PUBLIC_STAGE,
                owner="social_session",
                revision="social_session-r1",
            ),
        )
    )
    grounding = grounding_adapter or _empty_adapter("public_grounding")
    expression = FakeAdapter(
        _batch(
            "public_expression",
            _section(
                "expression.public.v1",
                Lifetime.TURN,
                SemanticRole.STATE,
                lane=Lane.PUBLIC_STAGE,
                owner="public_expression",
                revision="public_expression-r1",
            ),
        )
    )
    sources = PublicSourceView(
        core=core,
        public_scope=public_scope,
        public_session=public_session,
        public_grounding=grounding,
        expression=expression,
    )
    return sources, {
        "core": core,
        "public_scope": public_scope,
        "public_session": public_session,
        "public_grounding": grounding,
        "expression": expression,
    }


def _private_sources(*, route: Route = Route.INTERACTIVE) -> tuple[PrivateSourceView, dict[str, object]]:
    core = FakeAdapter(_batch("core_projection", *_core_sections(Lane.PRIVATE_OWNER, route)))
    owner_identity = _empty_adapter("owner_identity")
    private_memory = _empty_adapter("memory_v2")
    private_session = _empty_adapter("session_checkpoint")
    runtime_state = FakeAdapter(
        _batch(
            "runtime_context",
            _section(
                "situation.current.v1",
                Lifetime.TURN,
                SemanticRole.STATE,
                lane=Lane.PRIVATE_OWNER,
                owner="runtime_context",
                revision="runtime_context-r1",
            ),
        )
    )
    expression = FakeAdapter(
        _batch(
            "private_expression",
            _section(
                "expression.private.v1",
                Lifetime.TURN,
                SemanticRole.STATE,
                lane=Lane.PRIVATE_OWNER,
                owner="private_expression",
                revision="private_expression-r1",
            ),
        )
    )
    sources = PrivateSourceView(
        core=core,
        owner_identity=owner_identity,
        private_memory=private_memory,
        private_session=private_session,
        runtime_state=runtime_state,
        expression=expression,
    )
    return sources, {
        "core": core,
        "owner_identity": owner_identity,
        "private_memory": private_memory,
        "private_session": private_session,
        "runtime_state": runtime_state,
        "expression": expression,
    }


def _collector(**changes: object) -> LaneAwareContextCollector:
    values = {
        "capture_id_factory": lambda: "capture-task5",
        "snapshot_revision_factory": lambda: 7,
    }
    values.update(changes)
    return LaneAwareContextCollector(**values)


def _decision(packet: ContextPacket, source: str) -> CollectionDecision:
    return next(row for row in packet.collection_receipt if row.source == source)


def _packet(
    request: ResolvedContextRequest,
    *sections: ContextSection,
) -> ContextPacket:
    owners: dict[str, str] = {}
    for section in sections:
        prior = owners.setdefault(section.source.owner, section.revision)
        assert prior == section.revision
    return ContextPacket(
        schema_version=1,
        capture_id="capture-selection",
        snapshot_revision=11,
        request=request,
        source_revisions=tuple(sorted(owners.items())),
        sections=sections,
        collection_receipt=tuple(
            CollectionDecision(owner, "ALLOW_READ", "lane_policy", revision)
            for owner, revision in sorted(owners.items())
        ),
    )


def _budget_policy(
    *,
    status: BudgetPolicyStatus = BudgetPolicyStatus.SHADOW_CANDIDATE,
    max_tokens: int = 100_000,
    max_characters: int = 100_000,
) -> BudgetPolicy:
    return BudgetPolicy(
        policy_id="task5-measurement.v1",
        revision="candidate.v1",
        status=status,
        max_estimated_tokens=max_tokens,
        max_characters=max_characters,
    )


def test_resolver_requires_explicit_trusted_ingress_authority() -> None:
    resolver = LaneResolver(
        wall_clock=lambda: CAPTURED_WALL_TIME,
        monotonic_clock=lambda: CAPTURED_MONOTONIC_TIME,
        compiler_config_revision=COMPILER_CONFIG_REVISION,
    )

    with pytest.raises(ContextContractError, match="trusted_ingress_required"):
        resolver.resolve(_raw_request())


def test_caller_metadata_cannot_spoof_private_lane_for_public_ingress() -> None:
    resolved = _resolve_public(metadata={"lane": "private_owner"})

    assert resolved.scope.lane is Lane.PUBLIC_STAGE
    assert resolved.scope.interaction_scope == "public_viewer"
    assert resolved.scope.policy_id == "public.interactive.v1"


def test_public_resolution_drops_raw_metadata_and_private_values() -> None:
    resolved = _resolve_public(
        metadata={
            "lane": "private_owner",
            "private_memory": PRIVATE_SENTINEL,
            "public_metadata": {
                "topic": "Task 5 topic",
                "private_debug": PRIVATE_SENTINEL,
            },
        }
    )

    assert not hasattr(resolved, "caller_metadata")
    assert not hasattr(resolved, "unresolved")
    assert isinstance(resolved.public_context, FrozenMapping)
    assert "caller_metadata" not in resolved.public_context
    assert PRIVATE_SENTINEL not in repr(resolved)
    assert PRIVATE_SENTINEL not in repr(resolved.public_context)
    assert resolved.public_context["public_metadata"]["topic"] == "Task 5 topic"


@pytest.mark.parametrize(
    ("metadata", "code"),
    [
        ({"platform": "discord"}, "trusted_ingress_mismatch"),
        ({"channel_id": "other-room"}, "trusted_ingress_mismatch"),
        ({"session_id": "other-session"}, "trusted_ingress_mismatch"),
        ({"message_id": "other-event"}, "trusted_ingress_mismatch"),
        ({"user_id": "other-author"}, "trusted_ingress_mismatch"),
        ({"author_name": "Other Viewer"}, "trusted_ingress_mismatch"),
        (
            {"room_id": "room-task5", "channel_id": "other-room"},
            "trusted_ingress_mismatch",
        ),
    ],
)
def test_raw_public_identity_aliases_cannot_conflict_with_trusted_scope(
    metadata: object,
    code: str,
) -> None:
    with pytest.raises(ContextContractError, match=code):
        _resolve_public(metadata=metadata)


def test_incomplete_or_legacy_public_scope_is_rejected() -> None:
    incomplete = _public_scope(room_id="public", stream_session_id="legacy")

    with pytest.raises(ContextContractError, match="invalid_lane_route_scope"):
        _resolver(TrustedIngress(Lane.PUBLIC_STAGE, incomplete)).resolve(
            _raw_request(public=True)
        )


def test_unknown_trusted_lane_is_rejected() -> None:
    with pytest.raises(ContextContractError, match="unknown_lane"):
        _resolver(TrustedIngress("mystery", None)).resolve(_raw_request())  # type: ignore[arg-type]


def test_invalid_lane_route_scope_is_rejected_by_resolver() -> None:
    with pytest.raises(ContextContractError, match="invalid_lane_route_scope"):
        _resolver(TrustedIngress(Lane.PUBLIC_STAGE, _public_scope())).resolve(
            _raw_request(route=Route.PRIVATE_FAST, public=True)
        )


def test_operator_requires_trusted_operator_ingress_and_bridge_hint() -> None:
    resolved = _resolve_operator()
    assert resolved.scope.lane is Lane.OPERATOR_BACKSTAGE
    assert resolved.scope.interaction_scope == "bridge_system"
    assert resolved.scope.public_scope is None

    with pytest.raises(ContextContractError, match="trusted_ingress_mismatch"):
        _resolver(TrustedIngress(Lane.OPERATOR_BACKSTAGE, None)).resolve(
            _raw_request(operator=False)
        )


def test_capability_views_are_frozen_and_lane_specific() -> None:
    assert [field.name for field in fields(PrivateSourceView)] == [
        "core",
        "owner_identity",
        "private_memory",
        "private_session",
        "runtime_state",
        "expression",
    ]
    assert [field.name for field in fields(PublicSourceView)] == [
        "core",
        "public_scope",
        "public_session",
        "public_grounding",
        "expression",
    ]
    public_sources, _ = _public_sources()
    assert not hasattr(public_sources, "private_memory")
    assert not hasattr(public_sources, "owner_identity")
    assert not hasattr(public_sources, "runtime_state")
    with pytest.raises(FrozenInstanceError):
        public_sources.core = AdapterTripwire()  # type: ignore[misc]


def test_public_collection_never_touches_private_adapter_tripwire() -> None:
    private_tripwire = AdapterTripwire()
    public_sources, calls = _public_sources()
    collector = _collector(
        public_sources=public_sources,
        private_sources=private_tripwire,
    )

    packet = collector.collect(_resolve_public())

    assert private_tripwire.calls == 0
    assert _decision(packet, "private_memory").decision == "DENY_NOT_READ"
    assert _decision(packet, "private_memory").reason == "public_lane_denied"
    assert calls["public_grounding"].calls == 0
    assert _decision(packet, "public_grounding").decision == "CONDITIONAL_SKIP"


def test_public_grounding_reads_only_with_scope_consent_and_intent() -> None:
    grounding = FakeAdapter(
        _batch(
            "public_grounding",
            _section(
                "memory.public_grounding.v1",
                Lifetime.TURN,
                SemanticRole.EVIDENCE,
                lane=Lane.PUBLIC_STAGE,
                owner="public_grounding",
                revision="public_grounding-r1",
            ),
        )
    )
    public_sources, _ = _public_sources(grounding_adapter=grounding)
    packet = _collector(
        public_sources=public_sources,
        public_grounding_enabled=True,
    ).collect(
        _resolve_public(
            metadata={"memory_consent": True},
            grounding_intent=True,
        )
    )

    assert grounding.calls == 1
    assert _decision(packet, "public_grounding").decision == "CONDITIONAL_READ"


def test_public_grounding_default_off_skips_before_adapter_read() -> None:
    grounding_tripwire = AdapterTripwire()
    public_sources, _ = _public_sources(grounding_adapter=grounding_tripwire)

    packet = _collector(public_sources=public_sources).collect(
        _resolve_public(
            metadata={"memory_consent": True},
            grounding_intent=True,
        )
    )

    assert grounding_tripwire.calls == 0
    assert _decision(packet, "public_grounding").decision == "CONDITIONAL_SKIP"


def test_cum2_never_reads_public_grounding_not_present_in_registry() -> None:
    grounding_tripwire = AdapterTripwire()
    public_sources, _ = _public_sources(
        route=Route.YOUTUBE_CUM2,
        grounding_adapter=grounding_tripwire,
    )
    packet = _collector(public_sources=public_sources).collect(
        _resolve_public(
            route=Route.YOUTUBE_CUM2,
            metadata={"memory_consent": True},
            grounding_intent=True,
        )
    )

    assert grounding_tripwire.calls == 0
    assert _decision(packet, "public_grounding").decision == "DENY_NOT_READ"


def test_private_autonomy_applies_narrow_policy_without_memory_or_session_reads() -> None:
    private_sources, calls = _private_sources(route=Route.AUTONOMY)
    packet = _collector(private_sources=private_sources).collect(
        _resolve_private(route=Route.AUTONOMY)
    )

    assert calls["private_memory"].calls == 0
    assert calls["private_session"].calls == 0
    assert _decision(packet, "private_memory").decision == "DENY_NOT_READ"
    assert _decision(packet, "private_session").decision == "DENY_NOT_READ"
    assert calls["owner_identity"].calls == 1
    assert calls["runtime_state"].calls == 1


def test_public_autonomy_has_no_private_or_grounding_capability_reads() -> None:
    private_tripwire = AdapterTripwire()
    grounding_tripwire = AdapterTripwire()
    public_sources, _ = _public_sources(
        route=Route.AUTONOMY,
        grounding_adapter=grounding_tripwire,
    )
    packet = _collector(
        public_sources=public_sources,
        private_sources=private_tripwire,
    ).collect(_resolve_public(route=Route.AUTONOMY))

    assert private_tripwire.calls == 0
    assert grounding_tripwire.calls == 0
    assert _decision(packet, "private_memory").decision == "DENY_NOT_READ"
    assert _decision(packet, "public_grounding").decision == "DENY_NOT_READ"


def test_operator_policy_skips_diagnostic_state_without_explicit_capability() -> None:
    core = FakeAdapter(
        _batch(
            "operator_core",
            *_core_sections(Lane.OPERATOR_BACKSTAGE, Route.INTERACTIVE, "operator_core"),
        )
    )
    runtime_tripwire = AdapterTripwire()
    expression = _empty_adapter("operator_expression")
    operator_sources = OperatorSourceView(
        core=core,
        runtime_state=runtime_tripwire,
        expression=expression,
    )

    packet = _collector(operator_sources=operator_sources).collect(_resolve_operator())

    assert runtime_tripwire.calls == 0
    assert _decision(packet, "runtime_state").decision == "CONDITIONAL_SKIP"
    assert _decision(packet, "private_memory").decision == "DENY_NOT_READ"


@pytest.mark.parametrize("value", [_raw_request(), object.__new__(ResolvedContextRequest)])
def test_collector_rejects_unresolved_or_forged_request_before_adapter_calls(
    value: object,
) -> None:
    tripwire = AdapterTripwire()
    collector = _collector(
        private_sources=tripwire,
        public_sources=tripwire,
        operator_sources=tripwire,
    )

    expected = "unresolved_context_request" if type(value) is UnresolvedContextRequest else "invalid_resolution_seal"
    with pytest.raises(ContextContractError, match=expected):
        collector.collect(value)  # type: ignore[arg-type]
    assert tripwire.calls == 0


def test_mutated_genuine_request_is_rejected_before_adapter_calls() -> None:
    resolved = _resolve_public()
    object.__setattr__(resolved, "route", Route.PRIVATE_FAST)
    tripwire = AdapterTripwire()

    with pytest.raises(ContextContractError, match="invalid_resolution_seal"):
        _collector(
            private_sources=tripwire,
            public_sources=tripwire,
            operator_sources=tripwire,
        ).collect(resolved)
    assert tripwire.calls == 0


def test_private_fast_profile_fails_before_any_adapter_read() -> None:
    tripwire = AdapterTripwire()
    request = _resolve_private(route=Route.PRIVATE_FAST)

    with pytest.raises(ContextContractError, match="unsupported_collector_profile"):
        _collector(private_sources=tripwire).collect(request)
    assert tripwire.calls == 0


def test_duplicate_true_owner_batches_fail_instead_of_overwriting_receipts() -> None:
    core = FakeAdapter(_batch("same-owner", *_core_sections(Lane.PUBLIC_STAGE, Route.INTERACTIVE, "same-owner")))
    duplicate = _empty_adapter("same-owner")
    sources = PublicSourceView(
        core=core,
        public_scope=duplicate,
        public_session=_empty_adapter("social_session"),
        public_grounding=_empty_adapter("public_grounding"),
        expression=_empty_adapter("public_expression"),
    )

    with pytest.raises(ContextContractError, match="duplicate_source_id"):
        _collector(public_sources=sources).collect(_resolve_public())


def test_true_owner_cannot_collide_with_another_policy_capability_id() -> None:
    core = FakeAdapter(
        _batch(
            "public_scope",
            *_core_sections(
                Lane.PUBLIC_STAGE,
                Route.INTERACTIVE,
                "public_scope",
            ),
        )
    )
    public_scope = _empty_adapter("canonical_public_scope")
    sources = PublicSourceView(
        core=core,
        public_scope=public_scope,
        public_session=_empty_adapter("social_session"),
        public_grounding=_empty_adapter("public_grounding"),
        expression=_empty_adapter("public_expression"),
    )

    with pytest.raises(ContextContractError, match="duplicate_source_id"):
        _collector(public_sources=sources).collect(_resolve_public())
    assert public_scope.calls == 0


def test_default_snapshot_revision_is_process_monotonic() -> None:
    public_sources, _ = _public_sources()
    collector = LaneAwareContextCollector(public_sources=public_sources)

    first = collector.collect(_resolve_public())
    second = collector.collect(_resolve_public())

    assert second.snapshot_revision > first.snapshot_revision >= 1


def test_source_batch_requires_true_owner_and_revision_coherence() -> None:
    section = _section(
        "memory.retrieval.v1",
        Lifetime.TURN,
        SemanticRole.EVIDENCE,
        lane=Lane.PRIVATE_OWNER,
        owner="memory_v2",
        revision="memory-r1",
    )

    with pytest.raises(ContextContractError, match="invalid_source_revision"):
        SourceBatch(owner="private_memory", revision="memory-r1", sections=(section,))


def test_runtime_rejects_legacy_approved_metadata_as_enforcement_authority() -> None:
    request = _resolve_private()
    packet = _packet(
        request,
        _section(
            "core.private.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            lane=Lane.PRIVATE_OWNER,
            owner="core",
            revision="core-r1",
            required=True,
        ),
    )
    approved = _budget_policy(
        status=BudgetPolicyStatus.APPROVED,
        max_tokens=1,
        max_characters=1,
    )
    assert approved.status is BudgetPolicyStatus.APPROVED

    with pytest.raises(ContextContractError, match="budget_policy_unapproved"):
        ContextRuntime().resolve(packet, approved, enforce_budget=True)


def test_budget_policy_values_and_approved_status_are_measurement_only() -> None:
    request = _resolve_private()
    section = _section(
        "situation.current.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="runtime_context",
        revision="runtime-r1",
    )
    packet = _packet(request, section)

    small = ContextRuntime().resolve(
        packet,
        _budget_policy(max_tokens=1, max_characters=1),
    )
    approved = ContextRuntime().resolve(
        packet,
        _budget_policy(
            status=BudgetPolicyStatus.APPROVED,
            max_tokens=999_999,
            max_characters=999_999,
        ),
    )

    assert small == approved


def test_runtime_excludes_expired_irrelevant_and_future_observation_sections() -> None:
    request = _resolve_private()
    expired = _section(
        "situation.current.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="runtime_context",
        revision="runtime-r1",
        freshness=Freshness.EXPIRED,
        expires_at=CAPTURED_WALL_TIME - 1,
    )
    irrelevant = _section(
        "continuity.summary.v1",
        Lifetime.SESSION,
        SemanticRole.HISTORY,
        lane=Lane.PRIVATE_OWNER,
        owner="session_checkpoint",
        revision="session-r1",
        relevance=0.0,
    )
    future = _section(
        "situation.temporal.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="awareness",
        revision="awareness-r1",
        observed_at=CAPTURED_WALL_TIME + 3,
        expires_at=CAPTURED_WALL_TIME + 100,
    )

    resolved = ContextRuntime().resolve(
        _packet(request, expired, irrelevant, future),
        _budget_policy(),
    )
    by_id = {row.section_id: row for row in resolved.decisions}

    assert by_id[expired.id].decision == "expired"
    assert by_id[irrelevant.id].decision == "irrelevant"
    assert by_id[future.id].decision == "invalid"
    assert resolved.selected_sections == ()


@pytest.mark.parametrize(
    ("request_factory", "section_id", "lane"),
    [
        (_resolve_private, "situation.current.v1", Lane.PRIVATE_OWNER),
        (_resolve_operator, "situation.operator_status.v1", Lane.OPERATOR_BACKSTAGE),
    ],
)
def test_current_state_slots_require_observation_time_even_when_marked_fresh(
    request_factory: object,
    section_id: str,
    lane: Lane,
) -> None:
    request = request_factory()  # type: ignore[operator]
    current = _section(
        section_id,
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=lane,
        owner="runtime_context",
        revision="runtime-r1",
        freshness=Freshness.FRESH,
        observed_at=None,
        expires_at=None,
    )

    resolved = ContextRuntime().resolve(
        _packet(request, current),
        _budget_policy(),
    )

    assert resolved.selected_sections == ()
    assert resolved.decisions[0].decision == "invalid"
    assert resolved.decisions[0].reason == "invalid_section"


def test_missing_observation_time_remains_valid_for_static_and_expression_slots() -> None:
    request = _resolve_private()
    static = _section(
        "core.private.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        lane=Lane.PRIVATE_OWNER,
        owner="core_projection",
        revision="core-r1",
        observed_at=None,
        expires_at=None,
        required=True,
    )
    expression = _section(
        "expression.private.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="private_expression",
        revision="expression-r1",
        observed_at=None,
        expires_at=None,
    )

    resolved = ContextRuntime().resolve(
        _packet(request, expression, static),
        _budget_policy(),
    )

    assert {section.id for section in resolved.selected_sections} == {
        static.id,
        expression.id,
    }


def test_runtime_revalidates_visibility_on_entry() -> None:
    request = _resolve_private()
    section = _section(
        "situation.current.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="runtime_context",
        revision="runtime-r1",
    )
    packet = _packet(request, section)
    object.__setattr__(packet.sections[0], "visibility", frozenset({Lane.PUBLIC_STAGE}))

    with pytest.raises(ContextContractError, match="invalid_visibility"):
        ContextRuntime().resolve(packet, _budget_policy())


def test_conflicts_compete_only_for_same_fact_state_subject_and_role() -> None:
    request = _resolve_private()
    winner = _section(
        "situation.current.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="runtime_context",
        revision="runtime-r1",
        payload={"subject": "owner", "subject_role": "user", "canonical_value": "verified"},
        authority="verified_current_state",
        conflict_key="hardware.gpu.current",
    )
    loser = _section(
        "situation.temporal.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="awareness",
        revision="awareness-r1",
        payload={"subject": "owner", "subject_role": "user", "canonical_value": "older"},
        authority="active_durable_fact",
        conflict_key="hardware.gpu.current",
    )
    other_subject = _section(
        "continuity.open_loops.v1",
        Lifetime.SESSION,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="session_checkpoint",
        revision="session-r1",
        payload={"subject": "viewer", "subject_role": "user", "canonical_value": "other"},
        authority="active_durable_fact",
        conflict_key="hardware.gpu.current",
    )

    resolved = ContextRuntime().resolve(
        _packet(request, loser, other_subject, winner),
        _budget_policy(),
    )
    by_id = {row.section_id: row for row in resolved.decisions}

    assert by_id[winner.id].decision == "included"
    assert by_id[loser.id].decision == "conflict_loser"
    assert by_id[loser.id].related_section_id == winner.id
    assert by_id[other_subject.id].decision == "included"


def test_required_retention_does_not_override_fact_truth_precedence() -> None:
    request = _resolve_private()
    required_old = _section(
        "continuity.open_loops.v1",
        Lifetime.SESSION,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="session_checkpoint",
        revision="session-r1",
        payload={"subject": "owner", "subject_role": "user", "canonical_value": "old"},
        authority="active_durable_fact",
        conflict_key="hardware.gpu.current",
        required=True,
    )
    verified_current = _section(
        "situation.current.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="runtime_context",
        revision="runtime-r1",
        payload={"subject": "owner", "subject_role": "user", "canonical_value": "current"},
        authority="verified_current_state",
        conflict_key="hardware.gpu.current",
    )

    resolved = ContextRuntime().resolve(
        _packet(request, required_old, verified_current),
        _budget_policy(),
    )
    by_id = {row.section_id: row for row in resolved.decisions}

    assert by_id[verified_current.id].decision == "included"
    assert by_id[required_old.id].decision == "conflict_loser"
    assert by_id[required_old.id].related_section_id == verified_current.id


@pytest.mark.parametrize("semantic_status", ["historical", "superseded"])
def test_non_active_fact_requires_explicit_temporal_intent(
    semantic_status: str,
) -> None:
    historical = _section(
        "situation.temporal.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="awareness",
        revision="awareness-r1",
        semantic_status=semantic_status,
        freshness=Freshness.STALE,
    )

    ordinary = ContextRuntime().resolve(
        _packet(_resolve_private(), historical),
        _budget_policy(),
    )
    temporal = ContextRuntime().resolve(
        _packet(_resolve_private(temporal_intent=True), historical),
        _budget_policy(),
    )

    assert ordinary.decisions[0].decision == "irrelevant"
    assert temporal.decisions[0].decision == "irrelevant"
    assert temporal.decisions[0].reason == "not_relevant"


def test_only_source_selected_history_can_enter_temporal_section_resolution() -> None:
    selected_history = _section(
        "situation.temporal.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="awareness",
        revision="awareness-r1",
        semantic_status="historical",
        freshness=Freshness.STALE,
        payload={"temporal_selected": True, "text": "typed historical state"},
    )
    ordinary = ContextRuntime().resolve(
        _packet(_resolve_private(), selected_history),
        _budget_policy(),
    )
    temporal = ContextRuntime().resolve(
        _packet(_resolve_private(temporal_intent=True), selected_history),
        _budget_policy(),
    )
    assert ordinary.decisions[0].decision == "irrelevant"
    assert temporal.decisions[0].decision == "included"


def test_historical_fact_uses_shared_authority_vocabulary_when_source_selected() -> None:
    active = _section(
        "memory.retrieval.v1",
        Lifetime.TURN,
        SemanticRole.FACT,
        lane=Lane.PRIVATE_OWNER,
        owner="memory_v2",
        revision="memory-r1",
        payload={
            "subject": "Ba",
            "subject_role": "owner",
            "canonical_value": "current",
        },
        authority="active_durable_fact",
        conflict_key="hardware.gpu.current",
    )
    historical = _section(
        "situation.temporal.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="awareness",
        revision="awareness-r1",
        payload={
            "subject": "Ba",
            "subject_role": "owner",
            "canonical_value": "old",
            "temporal_selected": True,
        },
        authority="historical_fact",
        conflict_key="hardware.gpu.current",
        semantic_status="historical",
        freshness=Freshness.STALE,
    )
    resolved = ContextRuntime().resolve(
        _packet(_resolve_private(temporal_intent=True), historical, active),
        _budget_policy(),
    )
    decisions = {item.section_id: item for item in resolved.decisions}
    assert decisions[active.id].decision == "included"
    assert decisions[historical.id].decision == "conflict_loser"


def test_instruction_and_evidence_never_enter_global_conflict_priority() -> None:
    request = _resolve_private()
    instruction = _section(
        "policy.private.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        lane=Lane.PRIVATE_OWNER,
        owner="core",
        revision="core-r1",
        payload={"subject": "owner", "subject_role": "user"},
        authority="low",
        conflict_key="shared.key",
    )
    evidence = _section(
        "memory.retrieval.v1",
        Lifetime.TURN,
        SemanticRole.EVIDENCE,
        lane=Lane.PRIVATE_OWNER,
        owner="memory_v2",
        revision="memory-r1",
        payload={"subject": "owner", "subject_role": "user"},
        authority="explicit_verified_correction",
        conflict_key="shared.key",
    )

    resolved = ContextRuntime().resolve(
        _packet(request, evidence, instruction),
        _budget_policy(),
    )

    assert {section.id for section in resolved.selected_sections} == {
        instruction.id,
        evidence.id,
    }


def test_semantic_dedupe_is_deterministic_and_records_winner() -> None:
    request = _resolve_private()
    winner = _section(
        "continuity.summary.v1",
        Lifetime.SESSION,
        SemanticRole.HISTORY,
        lane=Lane.PRIVATE_OWNER,
        owner="session_checkpoint",
        revision="session-r1",
        dedupe_key="turn:17",
        relevance=0.9,
    )
    duplicate = _section(
        "continuity.recent_turns.v1",
        Lifetime.SESSION,
        SemanticRole.HISTORY,
        lane=Lane.PRIVATE_OWNER,
        owner="recent_turns",
        revision="turns-r1",
        dedupe_key="turn:17",
        relevance=0.2,
    )

    first = ContextRuntime().resolve(
        _packet(request, duplicate, winner),
        _budget_policy(),
    )
    second = ContextRuntime().resolve(
        _packet(request, winner, duplicate),
        _budget_policy(),
    )
    first_rows = {row.section_id: row for row in first.decisions}
    second_rows = {row.section_id: row for row in second.decisions}

    assert first.selected_sections == second.selected_sections == (winner,)
    assert first_rows[duplicate.id].decision == "duplicate"
    assert first_rows[duplicate.id].related_section_id == winner.id
    assert first_rows == second_rows


def test_history_dedupe_ignores_subject_shaped_payload_fields() -> None:
    request = _resolve_private()
    winner = _section(
        "continuity.summary.v1",
        Lifetime.SESSION,
        SemanticRole.HISTORY,
        lane=Lane.PRIVATE_OWNER,
        owner="session_checkpoint",
        revision="session-r1",
        payload={"subject": "owner", "subject_role": "user", "event": "turn-17"},
        dedupe_key="turn:17",
        relevance=0.9,
    )
    duplicate = _section(
        "continuity.recent_turns.v1",
        Lifetime.SESSION,
        SemanticRole.HISTORY,
        lane=Lane.PRIVATE_OWNER,
        owner="recent_turns",
        revision="turns-r1",
        payload={"subject": "viewer", "subject_role": "user", "event": "turn-17"},
        dedupe_key="turn:17",
        relevance=0.2,
    )

    resolved = ContextRuntime().resolve(
        _packet(request, duplicate, winner),
        _budget_policy(),
    )
    by_id = {row.section_id: row for row in resolved.decisions}

    assert resolved.selected_sections == (winner,)
    assert by_id[duplicate.id].decision == "duplicate"
    assert by_id[duplicate.id].related_section_id == winner.id


@pytest.mark.parametrize(
    ("first_subject", "second_subject"),
    [
        ("owner", "viewer"),
        ("Viewer-A", "viewer-a"),
    ],
)
def test_fact_state_dedupe_keeps_incompatible_exact_subject_identities(
    first_subject: str,
    second_subject: str,
) -> None:
    request = _resolve_private()
    first = _section(
        "situation.current.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="runtime_context",
        revision="runtime-r1",
        payload={"subject": first_subject, "subject_role": "user", "value": "one"},
        conflict_key=None,
        dedupe_key="state:shared-key",
    )
    second = _section(
        "situation.temporal.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="awareness",
        revision="awareness-r1",
        payload={"subject": second_subject, "subject_role": "user", "value": "two"},
        conflict_key=None,
        dedupe_key="state:shared-key",
    )

    resolved = ContextRuntime().resolve(
        _packet(request, first, second),
        _budget_policy(),
    )

    assert {section.id for section in resolved.selected_sections} == {
        first.id,
        second.id,
    }
    assert all(row.decision == "included" for row in resolved.decisions)


def test_fact_state_dedupe_requires_explicit_subject_compatibility() -> None:
    request = _resolve_private()
    first = _section(
        "situation.current.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="runtime_context",
        revision="runtime-r1",
        payload={"value": "one"},
        conflict_key=None,
        dedupe_key="state:unknown-subject",
    )
    second = _section(
        "situation.temporal.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="awareness",
        revision="awareness-r1",
        payload={"value": "two"},
        conflict_key=None,
        dedupe_key="state:unknown-subject",
    )

    resolved = ContextRuntime().resolve(
        _packet(request, first, second),
        _budget_policy(),
    )

    assert {section.id for section in resolved.selected_sections} == {
        first.id,
        second.id,
    }
    assert all(row.decision == "included" for row in resolved.decisions)


def test_fact_state_dedupe_still_collapses_exact_compatible_subjects() -> None:
    request = _resolve_private()
    winner = _section(
        "situation.current.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="runtime_context",
        revision="runtime-r1",
        payload={"subject": "owner", "subject_role": "user", "canonical_value": "one"},
        conflict_key=None,
        dedupe_key="state:owner-key",
        relevance=0.9,
    )
    duplicate = _section(
        "situation.temporal.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        owner="awareness",
        revision="awareness-r1",
        payload={"subject": "owner", "subject_role": "user", "canonical_value": "one"},
        conflict_key=None,
        dedupe_key="state:owner-key",
        relevance=0.2,
    )

    resolved = ContextRuntime().resolve(
        _packet(request, duplicate, winner),
        _budget_policy(),
    )
    by_id = {row.section_id: row for row in resolved.decisions}

    assert resolved.selected_sections == (winner,)
    assert by_id[duplicate.id].decision == "duplicate"
    assert by_id[duplicate.id].related_section_id == winner.id


def test_public_fake_collect_resolve_compile_flow_uses_supported_registry() -> None:
    private_tripwire = AdapterTripwire()
    public_sources, _ = _public_sources()
    packet = _collector(
        public_sources=public_sources,
        private_sources=private_tripwire,
    ).collect(_resolve_public(metadata={"lane": "private_owner"}))
    resolved = ContextRuntime().resolve(packet, _budget_policy())
    config = CompilerConfig(
        compiler_version=COMPILER_VERSION,
        revision=COMPILER_CONFIG_REVISION,
        resolved_provider="fixture-provider",
        resolved_model="fixture-model",
        budget_policy=BudgetPolicy(
            policy_id="public-shadow.v1",
            revision="candidate.v1",
            status=BudgetPolicyStatus.SHADOW_CANDIDATE,
            max_estimated_tokens=100_000,
            max_characters=100_000,
        ),
    )

    compiled = ContextCompiler().compile(resolved, config)

    assert private_tripwire.calls == 0
    assert compiled.lane is Lane.PUBLIC_STAGE
    assert compiled.route is Route.INTERACTIVE
    assert [message.role for message in compiled.messages] == ["system", "user"]
    assert PRIVATE_SENTINEL not in "".join(message.content for message in compiled.messages)
    assert compiled.manifest.budget_enforced is False
