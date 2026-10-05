"""Task 6 shadow-only Context Runtime composition.

The legacy prompt remains the provider payload.  This module resolves a sealed
request before source access, compiles a candidate from explicit formatter
outputs, and retains only bounded content-free telemetry.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import math
from threading import Lock
from typing import Any
import uuid

from nana.runtime.context_adapters import (
    PrivateSourceView,
    PublicSourceView,
    SourceBatch,
)
from nana.runtime.context_compiler import (
    COMPILER_VERSION,
    CompilerConfig,
    ContextCompiler,
    canonical_message_projection,
)
from nana.runtime.context_contracts import (
    BudgetPolicy,
    BudgetPolicyStatus,
    CompiledMessage,
    ContextContractError,
    ContextSection,
    Freshness,
    Lane,
    Lifetime,
    ResolvedContextRequest,
    Route,
    SemanticRole,
    SourceRef,
    UnresolvedContextRequest,
)
from nana.runtime.context_runtime import (
    ContextRuntime,
    LaneAwareContextCollector,
    LaneResolver,
    TrustedIngress,
)
from nana.runtime.context_telemetry import (
    BoundedPrefixTracker,
    ContextTelemetry,
    PrefixFingerprint,
    emit_context_telemetry,
    emit_sent_context_telemetry,
)
from nana.runtime.persona_boundary import resolve_request_scope
from nana.runtime.public_context_boundary import PublicEventScope


_COMPILER_CONFIG_REVISION = "compiler-config.v1"
_PROVISIONAL_BUDGET_REVISION = "provisional-shadow.2026-09-30"
# Narrow owner review; other section/profile limits remain provisional. This
# identifier is measurement provenance, never a canonical-mode authorization.
_PRIVATE_REVIEW_REVISION = "private-budget-review.2026-10-01.r1"
_STORY_OUTPUT_MAX_TOKENS = 1600
_STORY_OUTPUT_MAX_CHARACTERS = 6400
_ALLOWED_MODES = frozenset({"legacy", "shadow", "canonical"})
_OBSERVATION_CAPACITY = 256
_DISTRIBUTION_GROUP_CAPACITY = 64
_DISTRIBUTION_SAMPLE_CAPACITY = 256


@dataclass(frozen=True, slots=True)
class ContextTurn:
    mode: str
    lane: Lane
    route: Route
    model: str
    request_id: str
    correlation_id: str
    capture_id: str
    candidate_status: str
    resolved_request: ResolvedContextRequest | None = field(
        repr=False,
        compare=False,
    )


class _TurnContext(dict):
    __slots__ = ("_context_turn",)

    def __init__(self, values: Mapping[str, object], turn: ContextTurn) -> None:
        super().__init__(values)
        self._context_turn = turn


@dataclass(frozen=True, slots=True, repr=False)
class ShadowCandidateInputs:
    lane: Lane
    payloads: tuple[tuple[str, str], ...] = field(repr=False)

    def __repr__(self) -> str:
        return (
            "ShadowCandidateInputs("
            f"lane={self.lane.value!r}, section_count={len(self.payloads)})"
        )


@dataclass(frozen=True, slots=True)
class ReviewedSectionBudget:
    revision: str
    section_id: str
    chars: int
    token_estimate: int
    max_characters: int
    max_estimated_tokens: int
    character_overflow: bool
    token_overflow: bool


@dataclass(frozen=True, slots=True)
class ShadowObservation:
    lane: Lane
    route: Route
    model: str
    candidate_status: str
    sent_context: ContextTelemetry
    candidate_context: ContextTelemetry | None
    payload_unchanged: bool
    reviewed_section_budgets: tuple[ReviewedSectionBudget, ...] = ()


@dataclass(frozen=True, slots=True)
class NumericDistribution:
    count: int
    minimum: int
    p50: int
    p95: int
    p99: int
    maximum: int


@dataclass(frozen=True, slots=True)
class ShadowDistribution:
    lane: Lane
    route: Route
    model: str
    sent_characters: NumericDistribution
    sent_tokens_est: NumericDistribution
    candidate_characters: NumericDistribution | None
    candidate_tokens_est: NumericDistribution | None


class _ExistingBoundaryAuthority:
    """Translate only an already-resolved legacy boundary into Task 5 authority."""

    __slots__ = ("_boundary",)

    def __init__(self, boundary: object) -> None:
        self._boundary = boundary

    def authorize(self, request: UnresolvedContextRequest) -> TrustedIngress:
        boundary = self._boundary
        if getattr(boundary, "public", False):
            scope = getattr(boundary, "scope", None)
            if type(scope) is not PublicEventScope:
                raise ContextContractError("trusted_ingress_rejected")
            return TrustedIngress(lane=Lane.PUBLIC_STAGE, public_scope=scope)
        if str(getattr(boundary, "interaction_scope", "")) != "private_owner":
            raise ContextContractError("trusted_ingress_rejected")
        if (
            request.stream_mode
            or (request.viewer_name is not None and request.viewer_name.strip())
            or (
                request.public_platform is not None
                and request.public_platform.strip()
            )
        ):
            raise ContextContractError("trusted_ingress_rejected")
        return TrustedIngress(lane=Lane.PRIVATE_OWNER, public_scope=None)


@dataclass(frozen=True, slots=True)
class _MaterializedAdapter:
    batch: SourceBatch

    def read(self, _request: ResolvedContextRequest) -> SourceBatch:
        return self.batch


class _DistributionAccumulator:
    __slots__ = ("samples",)

    def __init__(self) -> None:
        self.samples: deque[int] = deque(maxlen=_DISTRIBUTION_SAMPLE_CAPACITY)

    def add(self, value: int) -> None:
        self.samples.append(int(value))

    def snapshot(self) -> NumericDistribution:
        values = sorted(self.samples)
        if not values:
            raise ContextContractError("empty_shadow_distribution")

        def percentile(fraction: float) -> int:
            index = max(0, math.ceil(fraction * len(values)) - 1)
            return values[index]

        return NumericDistribution(
            count=len(values),
            minimum=values[0],
            p50=percentile(0.50),
            p95=percentile(0.95),
            p99=percentile(0.99),
            maximum=values[-1],
        )


class _DistributionGroup:
    __slots__ = ("sent_chars", "sent_tokens", "candidate_chars", "candidate_tokens")

    def __init__(self) -> None:
        self.sent_chars = _DistributionAccumulator()
        self.sent_tokens = _DistributionAccumulator()
        self.candidate_chars = _DistributionAccumulator()
        self.candidate_tokens = _DistributionAccumulator()


_OBSERVATIONS: deque[ShadowObservation] = deque(maxlen=_OBSERVATION_CAPACITY)
_OBSERVATION_LOCK = Lock()
_PREFIX_TRACKER = BoundedPrefixTracker(capacity=256)
_DISTRIBUTIONS: OrderedDict[tuple[Lane, Route, str], _DistributionGroup] = (
    OrderedDict()
)
_EXTERNAL_SINK: object | None = None


def _opaque_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _config_value(name: str, default: str) -> str:
    try:
        from nana import config

        return str(getattr(config, name, default) or "").strip()
    except Exception:
        return default


def _mode_for_lane(lane: Lane) -> str:
    name = (
        "NANA_CONTEXT_PUBLIC_GPT_MODE"
        if lane is Lane.PUBLIC_STAGE
        else "NANA_CONTEXT_PRIVATE_MODE"
    )
    mode = _config_value(name, "legacy").lower()
    if mode not in _ALLOWED_MODES:
        raise ContextContractError("invalid_context_mode")
    if mode == "canonical":
        from nana.runtime.context_runtime import (
            PRIVATE_POLICY_REVISION, require_canonical_dispatch_ready,
        )
        if lane is not Lane.PRIVATE_OWNER or _config_value("NANA_CONTEXT_BUDGET_POLICY_REVISION", "") != PRIVATE_POLICY_REVISION:
            raise ContextContractError("budget_policy_unapproved")
        require_canonical_dispatch_ready()
    return mode


_PUBLIC_CALLER_METADATA_KEYS = frozenset(
    {
        "platform",
        "source",
        "provider",
        "room_id",
        "channel_id",
        "stream_session_id",
        "session_id",
        "event_id",
        "eventId",
        "message_id",
        "request_id",
        "author_id",
        "authorId",
        "author_channel_id",
        "user_id",
        "userId",
        "display_name",
        "viewer_name",
        "author_name",
        "actor_key",
        "memory_consent",
        "public_memory_consent",
        "public_metadata",
    }
)


def _public_caller_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, object] = {}
    for key in _PUBLIC_CALLER_METADATA_KEYS:
        if key not in value:
            continue
        item = value[key]
        if key == "public_metadata":
            if isinstance(item, Mapping):
                result[key] = {
                    str(nested_key): nested_value
                    for nested_key, nested_value in item.items()
                    if str(nested_key) in {"topic", "room", "channel", "language"}
                    and (
                        nested_value is None
                        or type(nested_value) in {str, int, float, bool}
                    )
                }
            continue
        if item is None or type(item) in {str, int, float, bool}:
            result[key] = item
    return result


def resolve_context_turn(
    *,
    text: str,
    viewer_name: str | None,
    stream_mode: bool,
    public_platform: str | None,
    metadata: object,
    private_model: str,
    public_model: str,
    story_mode: bool,
    casual_mode: bool,
    temporal_intent: bool,
    grounding_intent: bool,
) -> tuple[object, ContextTurn]:
    """Resolve the legacy boundary and issue a strict Task 5 request."""

    boundary = resolve_request_scope(
        viewer_name=viewer_name,
        stream_mode=stream_mode,
        public_platform=public_platform,
        metadata=metadata if isinstance(metadata, dict) else None,
    )
    lane = (
        Lane.PUBLIC_STAGE
        if getattr(boundary, "public", False)
        else Lane.PRIVATE_OWNER
    )
    mode = _mode_for_lane(lane)
    model = str(public_model if lane is Lane.PUBLIC_STAGE else private_model).strip()
    request_id = _opaque_id("context-request")
    correlation_id = _opaque_id("context-correlation")
    unresolved = UnresolvedContextRequest(
        request_id=request_id,
        correlation_id=correlation_id,
        route=Route.INTERACTIVE,
        model=model,
        current_input=str(text),
        viewer_name=viewer_name,
        stream_mode=bool(stream_mode),
        public_platform=public_platform,
        caller_metadata=(
            _public_caller_metadata(metadata)
            if lane is Lane.PUBLIC_STAGE
            else {}
        ),
        bridge_system=False,
        story_mode=bool(story_mode),
        casual_mode=bool(casual_mode),
        temporal_intent=bool(temporal_intent),
        grounding_intent=bool(grounding_intent),
    )
    resolved: ResolvedContextRequest | None
    status = "available"
    try:
        resolved = LaneResolver(
            _ExistingBoundaryAuthority(boundary),
            compiler_config_revision=_COMPILER_CONFIG_REVISION,
        ).resolve(unresolved)
    except ContextContractError:
        if lane is Lane.PRIVATE_OWNER:
            raise
        resolved = None
        status = "candidate_unavailable_scope"
    return boundary, ContextTurn(
        mode=mode,
        lane=lane,
        route=Route.INTERACTIVE,
        model=model,
        request_id=request_id,
        correlation_id=correlation_id,
        capture_id=_opaque_id("context-capture"),
        candidate_status=status,
        resolved_request=resolved,
    )


def context_mapping_with_turn(
    values: Mapping[str, object],
    turn: ContextTurn,
) -> dict[str, object]:
    return _TurnContext(values, turn)


def context_turn_from_mapping(value: object) -> ContextTurn | None:
    turn = getattr(value, "_context_turn", None)
    return turn if type(turn) is ContextTurn else None


def _payload_pairs(**values: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        (section_id, str(payload))
        for section_id, payload in values.items()
        if str(payload).strip()
    )


def private_candidate_inputs(
    *,
    core: str,
    policy: str,
    output_contract: str,
    identity: str = "",
    relationship: str = "",
    preferences: str = "",
    session_summary: str = "",
    recent_turns: str = "",
    expression: str = "",
    situation: str = "",
    temporal: str = "",
    retrieval: str = "",
    grounding: str = "",
    turn_mode: str = "",
) -> ShadowCandidateInputs:
    return ShadowCandidateInputs(
        lane=Lane.PRIVATE_OWNER,
        payloads=_payload_pairs(
            **{
                "core.private.v1": core,
                "policy.private.v1": policy,
                "contract.output.private.v1": output_contract,
                "identity.owner.v1": identity,
                "relationship.shared.v1": relationship,
                "preference.rules.v1": preferences,
                "continuity.summary.v1": session_summary,
                "continuity.recent_turns.v1": recent_turns,
                "expression.private.v1": expression,
                "situation.current.v1": situation,
                "situation.temporal.v1": temporal,
                "memory.retrieval.v1": retrieval,
                "memory.grounding.v1": grounding,
                "mode.turn.private.v1": turn_mode,
            }
        ),
    )


def public_candidate_inputs(
    *,
    core: str,
    policy: str,
    output_contract: str,
    public_session: str = "",
    expression: str = "",
    request_context: str = "",
    grounding: str = "",
    turn_mode: str = "",
) -> ShadowCandidateInputs:
    return ShadowCandidateInputs(
        lane=Lane.PUBLIC_STAGE,
        payloads=_payload_pairs(
            **{
                "core.public.v1": core,
                "policy.public.v1": policy,
                "contract.output.public.v1": output_contract,
                "continuity.public_room.v1": public_session,
                "expression.public.v1": expression,
                "public.request_context.v1": request_context,
                "memory.public_grounding.v1": grounding,
                "mode.turn.public.v1": turn_mode,
            }
        ),
    )


_SECTION_SPECS = {
    "core.private.v1": ("core", Lifetime.STATIC, SemanticRole.INSTRUCTION, True, 3500),
    "policy.private.v1": ("core", Lifetime.STATIC, SemanticRole.INSTRUCTION, True, 3500),
    "contract.output.private.v1": ("core", Lifetime.STATIC, SemanticRole.INSTRUCTION, True, 1000),
    "identity.owner.v1": ("owner_identity", Lifetime.DURABLE, SemanticRole.IDENTITY, False, 2000),
    "relationship.shared.v1": ("owner_identity", Lifetime.DURABLE, SemanticRole.FACT, False, 2000),
    "preference.rules.v1": ("owner_identity", Lifetime.DURABLE, SemanticRole.INSTRUCTION, False, 2000),
    "continuity.summary.v1": ("private_session", Lifetime.SESSION, SemanticRole.HISTORY, False, 2000),
    "continuity.recent_turns.v1": ("private_session", Lifetime.SESSION, SemanticRole.HISTORY, False, 2500),
    "expression.private.v1": ("expression", Lifetime.TURN, SemanticRole.STATE, False, 1500),
    "situation.current.v1": ("runtime_state", Lifetime.TURN, SemanticRole.STATE, False, 1500),
    "situation.temporal.v1": ("runtime_state", Lifetime.TURN, SemanticRole.STATE, False, 1800),
    "memory.retrieval.v1": ("private_memory", Lifetime.TURN, SemanticRole.EVIDENCE, False, 2500),
    "memory.grounding.v1": ("private_memory", Lifetime.TURN, SemanticRole.EVIDENCE, False, 2500),
    "mode.turn.private.v1": ("expression", Lifetime.TURN, SemanticRole.INSTRUCTION, False, 1000),
    "core.public.v1": ("core", Lifetime.STATIC, SemanticRole.INSTRUCTION, True, 3500),
    "policy.public.v1": ("core", Lifetime.STATIC, SemanticRole.INSTRUCTION, True, 3500),
    "contract.output.public.v1": ("core", Lifetime.STATIC, SemanticRole.INSTRUCTION, True, 1000),
    "continuity.public_room.v1": ("public_session", Lifetime.SESSION, SemanticRole.HISTORY, False, 2500),
    "expression.public.v1": ("expression", Lifetime.TURN, SemanticRole.STATE, False, 1500),
    "public.request_context.v1": ("public_scope", Lifetime.TURN, SemanticRole.STATE, False, 1500),
    "memory.public_grounding.v1": ("public_grounding", Lifetime.TURN, SemanticRole.EVIDENCE, False, 2500),
    "mode.turn.public.v1": ("expression", Lifetime.TURN, SemanticRole.INSTRUCTION, False, 1000),
}


def _source_batches(
    request: ResolvedContextRequest,
    inputs: ShadowCandidateInputs,
) -> dict[str, _MaterializedAdapter]:
    grouped: dict[str, list[ContextSection]] = {}
    for section_id, payload in inputs.payloads:
        try:
            owner, lifetime, role, required, max_tokens = _SECTION_SPECS[section_id]
        except KeyError as exc:
            raise ContextContractError("unknown_section_id") from exc
        if (
            request.scope.lane is Lane.PRIVATE_OWNER
            and request.story_mode
            and section_id == "contract.output.private.v1"
        ):
            max_tokens = _STORY_OUTPUT_MAX_TOKENS
        formatter_only_state = section_id in {
            "situation.current.v1",
            "situation.temporal.v1",
        }
        grouped.setdefault(owner, []).append(
            ContextSection(
                id=section_id,
                lifetime=lifetime,
                semantic_role=role,
                freshness=(
                    Freshness.UNKNOWN if formatter_only_state else Freshness.FRESH
                ),
                visibility=frozenset({inputs.lane}),
                source=SourceRef(
                    owner=owner,
                    adapter="task6.materialized.v1",
                    projection=section_id,
                ),
                revision="pending",
                authority=None,
                observed_at=None,
                expires_at=None,
                conflict_key=None,
                dedupe_key=section_id,
                max_tokens=max_tokens,
                payload=payload,
                formatter_version="task6.legacy_projection.v1",
                required=required,
                budget_class="mandatory" if required else "optional",
                semantic_status=(
                    None if formatter_only_state else "active"
                ),
                provenance=(),
                relevance=1.0,
            )
        )

    capabilities = (
        (
            "core",
            "owner_identity",
            "private_memory",
            "private_session",
            "runtime_state",
            "expression",
        )
        if inputs.lane is Lane.PRIVATE_OWNER
        else (
            "core",
            "public_scope",
            "public_session",
            "public_grounding",
            "expression",
        )
    )
    result: dict[str, _MaterializedAdapter] = {}
    for owner in capabilities:
        pending = grouped.get(owner, [])
        digest = hashlib.sha256()
        digest.update(owner.encode("utf-8"))
        for section in pending:
            digest.update(section.id.encode("utf-8"))
            digest.update(str(section.payload).encode("utf-8"))
        revision = f"content-hash-{digest.hexdigest()}"
        sections = tuple(
            ContextSection(
                id=section.id,
                lifetime=section.lifetime,
                semantic_role=section.semantic_role,
                freshness=section.freshness,
                visibility=section.visibility,
                source=section.source,
                revision=revision,
                authority=section.authority,
                observed_at=section.observed_at,
                expires_at=section.expires_at,
                conflict_key=section.conflict_key,
                dedupe_key=section.dedupe_key,
                max_tokens=section.max_tokens,
                payload=section.payload,
                formatter_version=section.formatter_version,
                required=section.required,
                budget_class=section.budget_class,
                semantic_status=section.semantic_status,
                provenance=section.provenance,
                relevance=section.relevance,
            )
            for section in pending
        )
        result[owner] = _MaterializedAdapter(
            SourceBatch(owner=owner, revision=revision, sections=sections)
        )
    return result


def _provisional_budget(request: ResolvedContextRequest) -> BudgetPolicy:
    if request.scope.lane is Lane.PUBLIC_STAGE:
        policy_id = "budget.shadow.public_interactive.v1"
        tokens, characters = 6000, 24000
    elif request.story_mode:
        policy_id = "budget.shadow.private_story.v1"
        tokens, characters = 16000, 64000
    else:
        policy_id = "budget.shadow.private_interactive.v1"
        tokens, characters = 12000, 48000
    return BudgetPolicy(
        policy_id=policy_id,
        revision=(
            _PRIVATE_REVIEW_REVISION
            if request.scope.lane is Lane.PRIVATE_OWNER
            else _PROVISIONAL_BUDGET_REVISION
        ),
        status=BudgetPolicyStatus.SHADOW_CANDIDATE,
        max_estimated_tokens=tokens,
        max_characters=characters,
    )


def _compile_candidate(
    request: ResolvedContextRequest,
    inputs: ShadowCandidateInputs,
):
    if inputs.lane is not request.scope.lane:
        raise ContextContractError("invalid_visibility")
    adapters = _source_batches(request, inputs)
    if inputs.lane is Lane.PRIVATE_OWNER:
        collector = LaneAwareContextCollector(
            private_sources=PrivateSourceView(
                core=adapters["core"],
                owner_identity=adapters["owner_identity"],
                private_memory=adapters["private_memory"],
                private_session=adapters["private_session"],
                runtime_state=adapters["runtime_state"],
                expression=adapters["expression"],
            )
        )
    else:
        collector = LaneAwareContextCollector(
            public_sources=PublicSourceView(
                core=adapters["core"],
                public_scope=adapters["public_scope"],
                public_session=adapters["public_session"],
                public_grounding=adapters["public_grounding"],
                expression=adapters["expression"],
            ),
            public_grounding_enabled=any(
                section_id == "memory.public_grounding.v1"
                for section_id, _ in inputs.payloads
            ),
        )
    budget = _provisional_budget(request)
    packet = collector.collect(request)
    resolved = ContextRuntime().resolve(packet, budget, enforce_budget=False)
    return ContextCompiler().compile(
        resolved,
        CompilerConfig(
            compiler_version=COMPILER_VERSION,
            revision=request.compiler_config_revision,
            resolved_provider=None,
            resolved_model=None,
            budget_policy=budget,
        ),
    )


def _scope_digest(request: ResolvedContextRequest) -> str:
    scope = request.scope.public_scope
    if request.scope.lane is Lane.PUBLIC_STAGE and type(scope) is PublicEventScope:
        raw = "\x1f".join(
            (
                scope.platform,
                scope.room_id,
                scope.stream_session_id,
                scope.identity.actor_key,
            )
        )
    else:
        # Private GPT has no stable authenticated session scope in Task 5.
        raw = request.request_id
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _reviewed_section_measurements(request, compiled) -> tuple[ReviewedSectionBudget, ...]:
    """Measure independently from issued manifest rows; never mutate compilation."""
    if (
        request is None
        or request.scope.lane is not Lane.PRIVATE_OWNER
        or not request.story_mode
        or compiled is None
    ):
        return ()
    for row in compiled.manifest.sections:
        if row.section_id == "contract.output.private.v1":
            return (ReviewedSectionBudget(
                revision=_PRIVATE_REVIEW_REVISION,
                section_id=row.section_id,
                chars=row.chars,
                token_estimate=row.token_estimate,
                max_characters=_STORY_OUTPUT_MAX_CHARACTERS,
                max_estimated_tokens=_STORY_OUTPUT_MAX_TOKENS,
                character_overflow=row.chars > _STORY_OUTPUT_MAX_CHARACTERS,
                token_overflow=row.token_estimate > _STORY_OUTPUT_MAX_TOKENS,
            ),)
    return ()


def _compiled_messages(messages: object) -> tuple[CompiledMessage, ...]:
    if not isinstance(messages, (list, tuple)):
        raise ContextContractError("invalid_compiled_messages")
    result: list[CompiledMessage] = []
    for message in messages:
        if not isinstance(message, Mapping):
            raise ContextContractError("invalid_compiled_messages")
        result.append(
            CompiledMessage(
                role=message.get("role"),
                content=message.get("content"),
            )
        )
    return tuple(result)


def _telemetry_only_turn(
    *,
    boundary: object,
    model: str,
    route: Route,
) -> ContextTurn:
    lane = (
        Lane.PUBLIC_STAGE
        if getattr(boundary, "public", False)
        else Lane.PRIVATE_OWNER
    )
    return ContextTurn(
        mode=_mode_for_lane(lane),
        lane=lane,
        route=route,
        model=str(model),
        request_id=_opaque_id("context-request"),
        correlation_id=_opaque_id("context-correlation"),
        capture_id=_opaque_id("context-capture"),
        candidate_status="candidate_unavailable_scope",
        resolved_request=None,
    )


def set_context_shadow_sink(sink: object | None) -> None:
    if sink is not None and not callable(getattr(sink, "emit", None)):
        raise ContextContractError("invalid_telemetry_sink")
    global _EXTERNAL_SINK
    with _OBSERVATION_LOCK:
        _EXTERNAL_SINK = sink


def _record_observation(observation: ShadowObservation) -> None:
    key = (observation.lane, observation.route, observation.model)
    sent = observation.sent_context.manifest
    candidate = (
        observation.candidate_context.manifest
        if observation.candidate_context is not None
        else None
    )
    with _OBSERVATION_LOCK:
        _OBSERVATIONS.append(observation)
        group = _DISTRIBUTIONS.pop(key, None) or _DistributionGroup()
        group.sent_chars.add(sent.input_chars)
        group.sent_tokens.add(sent.input_tokens_est)
        if candidate is not None:
            group.candidate_chars.add(candidate.input_chars)
            group.candidate_tokens.add(candidate.input_tokens_est)
        _DISTRIBUTIONS[key] = group
        while len(_DISTRIBUTIONS) > _DISTRIBUTION_GROUP_CAPACITY:
            _DISTRIBUTIONS.popitem(last=False)


def shadow_telemetry_snapshot() -> tuple[ShadowObservation, ...]:
    with _OBSERVATION_LOCK:
        return tuple(_OBSERVATIONS)


def shadow_distribution_snapshot() -> tuple[ShadowDistribution, ...]:
    with _OBSERVATION_LOCK:
        rows = []
        for (lane, route, model), group in _DISTRIBUTIONS.items():
            candidate_chars = (
                group.candidate_chars.snapshot()
                if group.candidate_chars.samples
                else None
            )
            candidate_tokens = (
                group.candidate_tokens.snapshot()
                if group.candidate_tokens.samples
                else None
            )
            rows.append(
                ShadowDistribution(
                    lane=lane,
                    route=route,
                    model=model,
                    sent_characters=group.sent_chars.snapshot(),
                    sent_tokens_est=group.sent_tokens.snapshot(),
                    candidate_characters=candidate_chars,
                    candidate_tokens_est=candidate_tokens,
                )
            )
        return tuple(rows)


def observe_provider_bound_context(
    *,
    context: object,
    boundary: object,
    messages: object,
    model: str,
    route: Route = Route.INTERACTIVE,
    candidate_factory: Callable[[], ShadowCandidateInputs] | None = None,
) -> ShadowObservation | None:
    """Measure shadow candidate and exact legacy payload without mutation."""

    turn = context_turn_from_mapping(context)
    if turn is None:
        turn = _telemetry_only_turn(boundary=boundary, model=model, route=route)
    if turn.mode == "legacy":
        return None
    if turn.mode != "shadow":
        raise ContextContractError("budget_policy_unapproved")

    original_identity = id(messages)
    before = _compiled_messages(messages)
    before_hash = hashlib.sha256(canonical_message_projection(before)).hexdigest()
    candidate_context: ContextTelemetry | None = None
    candidate_status = turn.candidate_status
    candidate_compiled = None
    sink = _EXTERNAL_SINK

    if route is Route.PRIVATE_FAST:
        candidate_status = "candidate_unavailable_profile"
    elif turn.resolved_request is None:
        candidate_status = "candidate_unavailable_scope"
    elif candidate_factory is None:
        candidate_status = "candidate_unavailable_projection"
    else:
        try:
            inputs = candidate_factory()
            candidate_compiled = _compile_candidate(turn.resolved_request, inputs)
            fingerprint = PrefixFingerprint.from_compiled(
                candidate_compiled,
                comparison_scope_digest=_scope_digest(turn.resolved_request),
            )
            comparison = _PREFIX_TRACKER.compare_and_replace(fingerprint)
            candidate_context = emit_context_telemetry(
                candidate_compiled,
                label="candidate_context",
                production_bound=False,
                fingerprint=fingerprint,
                comparison=comparison,
                sink=sink,
            )
            candidate_status = "available"
        except Exception:
            candidate_context = None
            candidate_status = "candidate_unavailable_error"

    after = _compiled_messages(messages)
    after_hash = hashlib.sha256(canonical_message_projection(after)).hexdigest()
    payload_unchanged = (
        id(messages) == original_identity
        and before == after
        and before_hash == after_hash
    )
    if not payload_unchanged:
        raise AssertionError("shadow_payload_mutated")

    manifest = candidate_compiled.manifest if candidate_compiled is not None else None
    sent_context = emit_sent_context_telemetry(
        list(after),
        request_id=turn.request_id,
        correlation_id=turn.correlation_id,
        capture_id=manifest.capture_id if manifest is not None else turn.capture_id,
        snapshot_revision=(
            manifest.snapshot_revision if manifest is not None else 0
        ),
        model=str(model),
        resolved_model=None,
        resolved_provider=None,
        lane=turn.lane,
        route=route,
        sink=sink,
    )
    observation = ShadowObservation(
        lane=turn.lane,
        route=route,
        model=str(model),
        candidate_status=candidate_status,
        sent_context=sent_context,
        candidate_context=candidate_context,
        payload_unchanged=True,
        reviewed_section_budgets=_reviewed_section_measurements(
            turn.resolved_request,
            candidate_compiled if candidate_context is not None else None,
        ),
    )
    _record_observation(observation)
    return observation


__all__ = [
    "ContextTurn",
    "NumericDistribution",
    "ShadowCandidateInputs",
    "ShadowDistribution",
    "ShadowObservation",
    "context_mapping_with_turn",
    "context_turn_from_mapping",
    "observe_provider_bound_context",
    "private_candidate_inputs",
    "public_candidate_inputs",
    "resolve_context_turn",
    "set_context_shadow_sink",
    "shadow_distribution_snapshot",
    "shadow_telemetry_snapshot",
]
