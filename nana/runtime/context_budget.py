"""Pure private BudgetPolicyV1 lookup and deterministic local enforcement."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import hmac
import math
import re
from threading import Lock
import weakref

from nana.runtime.context_contracts import (
    BudgetPolicy,
    BudgetPolicyStatus,
    BudgetSheddingDecision,
    CompiledMessage,
    ContextContractError,
    ContextPacket,
    ContextSection,
    FrozenMapping,
    Lane,
    ResolvedContextPacket,
    Route,
    SelectionDecision,
    freeze_payload,
    require_resolved_context_request,
)


POLICY_REVISION = "budget-policy-v1.review.2026-10-01.r1"
POLICY_ARTIFACT_SHA256 = (
    "ED0DE1F88A856689A49BC2904F9BEF4C649C87F3638F4EF5844401FDD975E652"
)
BUDGET_ENFORCEMENT_VERSION = "budget-enforcement.v1"

_SHA256_RE = re.compile(r"^[0-9A-Fa-f]{64}$")
_ATTESTATION_CAPACITY = 4_096
_ATTESTATION_LOCK = Lock()
_APPROVED_POLICY_ATTESTATIONS: OrderedDict[
    int,
    tuple[weakref.ReferenceType[BudgetPolicyV1], bytes],
] = OrderedDict()
_ENFORCEMENT_ATTESTATIONS: OrderedDict[
    int,
    tuple[weakref.ReferenceType[ResolvedContextPacket], bytes, bytes],
] = OrderedDict()


def _identifier(value: object, code: str) -> str:
    if type(value) is not str or not value.strip():
        raise ContextContractError(code)
    return value


def _positive(value: object, code: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContextContractError(code)
    return value


def _optional_positive(value: object, code: str) -> int | None:
    if value is None:
        return None
    return _positive(value, code)


class RetentionClass(str, Enum):
    REGISTRY_REQUIRED = "R"
    MINIMUM_REQUIRED = "M"
    CONDITIONAL = "C"
    OPTIONAL = "O"


@dataclass(frozen=True, slots=True)
class BudgetSectionLimitV1:
    section_id: str
    max_estimated_tokens: int
    max_characters: int
    retention: RetentionClass
    shedding_stage: int | None

    def __post_init__(self) -> None:
        _identifier(self.section_id, "invalid_budget_policy")
        _positive(self.max_estimated_tokens, "invalid_budget_limit")
        _positive(self.max_characters, "invalid_budget_limit")
        if type(self.retention) is not RetentionClass:
            raise ContextContractError("invalid_budget_policy")
        if self.shedding_stage is not None and (
            type(self.shedding_stage) is not int
            or not 1 <= self.shedding_stage <= 5
        ):
            raise ContextContractError("invalid_budget_policy")
        if self.retention is RetentionClass.OPTIONAL and self.shedding_stage is None:
            raise ContextContractError("invalid_budget_policy")


@dataclass(frozen=True, slots=True, weakref_slot=True)
class BudgetPolicyV1:
    profile_id: str
    revision: str
    artifact_sha256: str
    status: BudgetPolicyStatus
    lane: Lane
    route: Route
    story_mode: bool
    max_estimated_tokens: int
    max_characters: int
    current_input_max_estimated_tokens: int
    current_input_max_characters: int
    output_reserve_tokens: int
    framing_margin_tokens: int
    section_limits: tuple[BudgetSectionLimitV1, ...]

    def __post_init__(self) -> None:
        _identifier(self.profile_id, "invalid_budget_policy")
        _identifier(self.revision, "invalid_budget_policy")
        if type(self.artifact_sha256) is not str or not _SHA256_RE.fullmatch(
            self.artifact_sha256
        ):
            raise ContextContractError("invalid_budget_policy")
        if type(self.status) is not BudgetPolicyStatus:
            raise ContextContractError("invalid_budget_status")
        if self.lane is not Lane.PRIVATE_OWNER or self.route is not Route.INTERACTIVE:
            raise ContextContractError("unapproved_canonical_profile")
        if type(self.story_mode) is not bool:
            raise ContextContractError("invalid_budget_policy")
        for value in (
            self.max_estimated_tokens,
            self.max_characters,
            self.current_input_max_estimated_tokens,
            self.current_input_max_characters,
            self.output_reserve_tokens,
            self.framing_margin_tokens,
        ):
            _positive(value, "invalid_budget_limit")
        if type(self.section_limits) is not tuple:
            raise ContextContractError("invalid_budget_policy")
        rows = tuple(_copy_section_limit(item) for item in self.section_limits)
        ids = [item.section_id for item in rows]
        if not rows or len(ids) != len(set(ids)):
            raise ContextContractError("invalid_budget_policy")
        object.__setattr__(self, "section_limits", rows)

    @property
    def policy_id(self) -> str:
        return self.profile_id


def _copy_section_limit(value: object) -> BudgetSectionLimitV1:
    if type(value) is not BudgetSectionLimitV1:
        raise ContextContractError("invalid_budget_policy")
    try:
        return BudgetSectionLimitV1(
            value.section_id,
            value.max_estimated_tokens,
            value.max_characters,
            value.retention,
            value.shedding_stage,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_budget_policy") from exc


@dataclass(frozen=True, slots=True)
class SourceBudgetUnitV1:
    payload: object
    unit_id: str | None
    relevance: float | None
    observed_at: float | None
    pinned: bool
    kind: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", freeze_payload(self.payload))
        if self.unit_id is not None:
            _identifier(self.unit_id, "invalid_source_projection")
        if self.relevance is not None:
            if type(self.relevance) not in (int, float):
                raise ContextContractError("invalid_source_projection")
            relevance = float(self.relevance)
            if not math.isfinite(relevance) or not 0.0 <= relevance <= 1.0:
                raise ContextContractError("invalid_source_projection")
            object.__setattr__(self, "relevance", relevance)
        if self.observed_at is not None:
            if type(self.observed_at) not in (int, float):
                raise ContextContractError("invalid_source_projection")
            observed = float(self.observed_at)
            if not math.isfinite(observed) or observed < 0:
                raise ContextContractError("invalid_source_projection")
            object.__setattr__(self, "observed_at", observed)
        if type(self.pinned) is not bool:
            raise ContextContractError("invalid_source_projection")
        if self.kind not in _UNIT_STAGES:
            raise ContextContractError("invalid_source_projection")


@dataclass(frozen=True, slots=True)
class SourceSectionProjectionV1:
    section_id: str
    units: tuple[SourceBudgetUnitV1, ...]
    project: Callable[[tuple[SourceBudgetUnitV1, ...]], ContextSection | None]

    def __post_init__(self) -> None:
        _identifier(self.section_id, "invalid_source_projection")
        if type(self.units) is not tuple or not self.units:
            raise ContextContractError("invalid_source_projection")
        if any(type(item) is not SourceBudgetUnitV1 for item in self.units):
            raise ContextContractError("invalid_source_projection")
        ids = [item.unit_id for item in self.units if item.unit_id is not None]
        if len(ids) != len(set(ids)) or not callable(self.project):
            raise ContextContractError("invalid_source_projection")


@dataclass(frozen=True, slots=True)
class ReviewedModelCapabilityV1:
    provider: str
    model: str
    revision: str
    context_window_tokens: int
    provider_input_limit_tokens: int | None
    max_input_characters: int | None
    count_input_tokens: Callable[[tuple[CompiledMessage, ...]], int]

    def __post_init__(self) -> None:
        _identifier(self.provider, "invalid_model_capability")
        _identifier(self.model, "invalid_model_capability")
        _identifier(self.revision, "invalid_model_capability")
        _positive(self.context_window_tokens, "invalid_model_capability")
        _optional_positive(
            self.provider_input_limit_tokens,
            "invalid_model_capability",
        )
        _optional_positive(self.max_input_characters, "invalid_model_capability")
        if not callable(self.count_input_tokens):
            raise ContextContractError("invalid_model_capability")


_UNIT_STAGES = {
    "preference_extra": 1,
    "expression_extra": 1,
    "relationship_history": 2,
    "old_turn": 2,
    "recent_turn": 2,
    "temporal_moment": 3,
    "situation_field": 3,
    "summary_detail": 4,
    "identity_detail": 4,
    "open_loop": 4,
    "retrieval_record": 5,
    "grounding_record": 5,
}


def _policy_digest(policy: BudgetPolicyV1) -> bytes:
    return hashlib.sha256(repr(policy).encode("utf-8")).digest()


def _remove_approved_policy_attestation(
    object_id: int,
    reference: weakref.ReferenceType[BudgetPolicyV1],
) -> None:
    with _ATTESTATION_LOCK:
        current = _APPROVED_POLICY_ATTESTATIONS.get(object_id)
        if current is not None and current[0] is reference:
            _APPROVED_POLICY_ATTESTATIONS.pop(object_id, None)


def _attest_approved_policy(policy: BudgetPolicyV1) -> BudgetPolicyV1:
    object_id = id(policy)
    reference = weakref.ref(
        policy,
        lambda expired, key=object_id: _remove_approved_policy_attestation(
            key,
            expired,
        ),
    )
    with _ATTESTATION_LOCK:
        _APPROVED_POLICY_ATTESTATIONS.pop(object_id, None)
        _APPROVED_POLICY_ATTESTATIONS[object_id] = (
            reference,
            _policy_digest(policy),
        )
        while len(_APPROVED_POLICY_ATTESTATIONS) > _ATTESTATION_CAPACITY:
            _APPROVED_POLICY_ATTESTATIONS.popitem(last=False)
    return policy


def _is_attested_approved_policy(policy: BudgetPolicyV1) -> bool:
    with _ATTESTATION_LOCK:
        expected = _APPROVED_POLICY_ATTESTATIONS.get(id(policy))
        return (
            expected is not None
            and expected[0]() is policy
            and hmac.compare_digest(expected[1], _policy_digest(policy))
        )


def copy_budget_policy_v1(value: object) -> BudgetPolicyV1:
    if type(value) is not BudgetPolicyV1:
        raise ContextContractError("invalid_budget_policy")
    try:
        copied = BudgetPolicyV1(
            value.profile_id,
            value.revision,
            value.artifact_sha256,
            value.status,
            value.lane,
            value.route,
            value.story_mode,
            value.max_estimated_tokens,
            value.max_characters,
            value.current_input_max_estimated_tokens,
            value.current_input_max_characters,
            value.output_reserve_tokens,
            value.framing_margin_tokens,
            value.section_limits,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_budget_policy") from exc
    if value.status is BudgetPolicyStatus.APPROVED:
        if not _is_attested_approved_policy(value):
            raise ContextContractError("budget_policy_unapproved")
        _attest_approved_policy(copied)
    return copied


def _row(
    section_id: str,
    tokens: int,
    chars: int,
    retention: RetentionClass,
    stage: int | None,
) -> BudgetSectionLimitV1:
    return BudgetSectionLimitV1(section_id, tokens, chars, retention, stage)


def approved_private_budget_policy_v1(request: object) -> BudgetPolicyV1:
    """Return the exact hash-bound owner-approved private profile."""

    resolved = require_resolved_context_request(request)
    if (
        resolved.scope.lane is not Lane.PRIVATE_OWNER
        or resolved.route is not Route.INTERACTIVE
    ):
        raise ContextContractError("unapproved_canonical_profile")
    story = resolved.story_mode
    rows = (
        _row("core.private.v1", 2500, 10000, RetentionClass.REGISTRY_REQUIRED, None),
        _row("policy.private.v1", 1000, 4000, RetentionClass.REGISTRY_REQUIRED, None),
        _row(
            "contract.output.private.v1",
            1600 if story else 1000,
            6400 if story else 4000,
            RetentionClass.REGISTRY_REQUIRED,
            None,
        ),
        _row("identity.owner.v1", 1000, 4000, RetentionClass.MINIMUM_REQUIRED, 4),
        _row("relationship.shared.v1", 400, 1600, RetentionClass.OPTIONAL, 2),
        _row("preference.rules.v1", 600, 2400, RetentionClass.OPTIONAL, 1),
        _row("continuity.summary.v1", 800, 1600, RetentionClass.CONDITIONAL, 4),
        _row("continuity.open_loops.v1", 600, 1800, RetentionClass.CONDITIONAL, 4),
        _row("continuity.recent_turns.v1", 2500, 8000, RetentionClass.CONDITIONAL, 2),
        _row("expression.private.v1", 500, 1600, RetentionClass.MINIMUM_REQUIRED, 1),
        _row("situation.current.v1", 1000, 4000, RetentionClass.CONDITIONAL, 3),
        _row("situation.temporal.v1", 1800, 5400, RetentionClass.CONDITIONAL, 3),
        _row("memory.retrieval.v1", 1000, 1600, RetentionClass.CONDITIONAL, 5),
        _row("memory.grounding.v1", 1500, 4400, RetentionClass.CONDITIONAL, 5),
        _row("mode.turn.private.v1", 600, 1600, RetentionClass.CONDITIONAL, None),
    )
    return _attest_approved_policy(
        BudgetPolicyV1(
            "private_story.v1" if story else "private_interactive.v1",
            POLICY_REVISION,
            POLICY_ARTIFACT_SHA256,
            BudgetPolicyStatus.APPROVED,
            Lane.PRIVATE_OWNER,
            Route.INTERACTIVE,
            story,
            16000 if story else 12000,
            64000 if story else 48000,
            8000,
            24000,
            1600 if story else 1000,
            512,
            rows,
        )
    )


def _measurement_policy(policy: BudgetPolicyV1) -> BudgetPolicy:
    return BudgetPolicy(
        policy.profile_id,
        policy.revision,
        policy.status,
        policy.max_estimated_tokens,
        policy.max_characters,
    )


def _unenforced(value: ResolvedContextPacket) -> ResolvedContextPacket:
    return ResolvedContextPacket(
        value.packet,
        value.selected_sections,
        value.decisions,
        value.total_estimated_tokens,
    )


def _preview(value: ResolvedContextPacket, policy: BudgetPolicyV1):
    from nana.runtime.context_compiler import (
        COMPILER_VERSION,
        CompilerConfig,
        ContextCompiler,
    )

    request = value.packet.request
    return ContextCompiler().compile(
        _unenforced(value),
        CompilerConfig(
            COMPILER_VERSION,
            request.compiler_config_revision,
            None,
            None,
            _measurement_policy(policy),
        ),
    )


def _rebuild(
    base: ResolvedContextPacket,
    sections: list[ContextSection],
    decisions: dict[str, SelectionDecision],
) -> ResolvedContextPacket:
    from nana.runtime.context_compiler import _render_payload, estimate_tokens_v1

    packet = ContextPacket(
        base.packet.schema_version,
        base.packet.capture_id,
        base.packet.snapshot_revision,
        base.packet.request,
        base.packet.source_revisions,
        tuple(sections),
        base.packet.collection_receipt,
    )
    ordered_decisions = tuple(decisions[item.id] for item in sections)
    selected = tuple(
        item for item in sections if decisions[item.id].included
    )
    return ResolvedContextPacket(
        packet,
        selected,
        ordered_decisions,
        sum(estimate_tokens_v1(_render_payload(item.payload)) for item in selected),
    )


def _section_fits(section: ContextSection, limit: BudgetSectionLimitV1) -> bool:
    from nana.runtime.context_compiler import _render_payload, estimate_tokens_v1

    rendered = _render_payload(section.payload)
    return (
        len(rendered) <= limit.max_characters
        and estimate_tokens_v1(rendered)
        <= min(limit.max_estimated_tokens, section.max_tokens)
    )


def _projection_output(
    original: ContextSection,
    projected: object,
) -> ContextSection | None:
    if projected is None:
        return None
    if type(projected) is not ContextSection:
        raise ContextContractError("invalid_source_projection")
    protected = (
        "id",
        "lifetime",
        "semantic_role",
        "freshness",
        "visibility",
        "source",
        "revision",
        "authority",
        "observed_at",
        "expires_at",
        "conflict_key",
        "dedupe_key",
        "max_tokens",
        "formatter_version",
        "required",
        "budget_class",
        "semantic_status",
    )
    if any(getattr(projected, name) != getattr(original, name) for name in protected):
        raise ContextContractError("invalid_source_projection")
    return projected


def _has_semantic_content(value: object) -> bool:
    if value is None:
        return False
    if type(value) is str:
        return bool(value.strip())
    if type(value) is tuple:
        return any(_has_semantic_content(item) for item in value)
    if type(value) is FrozenMapping:
        return any(_has_semantic_content(item) for item in value.values())
    return type(value) in {bool, int, float}


def _shedding_key(
    *,
    stage: int,
    kind: str,
    relevance: float | None,
    observed_at: float | None,
    slot_index: int,
    unit_id: str | None,
    source_index: int,
    whole_section: bool,
) -> tuple[object, ...]:
    observed = observed_at if observed_at is not None else -1.0
    missing_time = 0 if observed_at is None else 1
    relevance_value = relevance if relevance is not None else 0.0
    if stage == 2:
        group = 1 if kind == "recent_turn" else 0
    else:
        group = 0
    if (stage == 2 and kind == "recent_turn") or (
        stage == 3 and kind == "temporal_moment"
    ):
        primary = (float(missing_time), observed, relevance_value)
    else:
        primary = (relevance_value, float(missing_time), observed)
    identity = (
        (0, unit_id, source_index)
        if unit_id is not None
        else (1, "", source_index)
    )
    return (
        stage,
        group,
        *primary,
        slot_index,
        *identity,
        1 if whole_section else 0,
    )


def _unit_key(
    unit: SourceBudgetUnitV1,
    source_index: int,
    stage: int,
    slot_index: int,
) -> tuple[object, ...]:
    return _shedding_key(
        stage=stage,
        kind=unit.kind,
        relevance=unit.relevance,
        observed_at=unit.observed_at,
        slot_index=slot_index,
        unit_id=unit.unit_id,
        source_index=source_index,
        whole_section=False,
    )


def _whole_section_key(
    section: ContextSection,
    stage: int,
    slot_index: int,
) -> tuple[object, ...]:
    kind = {
        "continuity.recent_turns.v1": "recent_turn",
        "relationship.shared.v1": "relationship_history",
        "situation.temporal.v1": "temporal_moment",
    }.get(section.id, "summary_detail")
    return _shedding_key(
        stage=stage,
        kind=kind,
        relevance=section.relevance,
        observed_at=section.observed_at,
        slot_index=slot_index,
        unit_id=section.id,
        source_index=-1,
        whole_section=True,
    )


def _drop_decision(
    decision: SelectionDecision,
    reason: str,
) -> SelectionDecision:
    return replace(
        decision,
        decision=reason,
        reason=reason,
        chars=None,
        token_estimate=None,
        related_section_id=None,
    )


def _capacity_limits(
    policy: BudgetPolicyV1,
    request_model: str,
    capability: ReviewedModelCapabilityV1 | None,
    resolved_model: str | None,
    require_model_capacity: bool,
) -> tuple[int, int]:
    if capability is None:
        if require_model_capacity:
            raise ContextContractError("model_context_capacity_unavailable")
        return policy.max_estimated_tokens, policy.max_characters
    capacity_model = request_model if resolved_model is None else resolved_model
    if type(capacity_model) is not str or not capacity_model.strip():
        raise ContextContractError("model_context_capacity_unavailable")
    if (
        type(capability) is not ReviewedModelCapabilityV1
        or capability.model != capacity_model
    ):
        raise ContextContractError("model_context_capacity_unavailable")
    remainder = (
        capability.context_window_tokens
        - policy.output_reserve_tokens
        - policy.framing_margin_tokens
    )
    if remainder <= 0:
        raise ContextContractError("context_budget_exceeded")
    token_limit = min(policy.max_estimated_tokens, remainder)
    if capability.provider_input_limit_tokens is not None:
        token_limit = min(token_limit, capability.provider_input_limit_tokens)
    char_limit = policy.max_characters
    if capability.max_input_characters is not None:
        char_limit = min(char_limit, capability.max_input_characters)
    return token_limit, char_limit


def _messages_fit(
    compiled: object,
    token_limit: int,
    char_limit: int,
    policy: BudgetPolicyV1,
    capability: ReviewedModelCapabilityV1 | None,
) -> bool:
    manifest = compiled.manifest
    if manifest.input_tokens_est > token_limit or manifest.input_chars > char_limit:
        return False
    if capability is None:
        return True
    try:
        counted = capability.count_input_tokens(compiled.messages)
    except Exception as exc:
        raise ContextContractError("model_context_capacity_unavailable") from exc
    if type(counted) is not int or counted <= 0:
        raise ContextContractError("model_context_capacity_unavailable")
    trusted_limit = capability.context_window_tokens - policy.output_reserve_tokens
    if capability.provider_input_limit_tokens is not None:
        trusted_limit = min(trusted_limit, capability.provider_input_limit_tokens)
    return counted <= trusted_limit


def _enforcement_digest(value: ResolvedContextPacket, policy: BudgetPolicyV1) -> bytes:
    return hashlib.sha256(
        (repr(value) + "\0" + repr(policy)).encode("utf-8")
    ).digest()


def _remove_enforcement_attestation(
    object_id: int,
    reference: weakref.ReferenceType[ResolvedContextPacket],
) -> None:
    with _ATTESTATION_LOCK:
        current = _ENFORCEMENT_ATTESTATIONS.get(object_id)
        if current is not None and current[0] is reference:
            _ENFORCEMENT_ATTESTATIONS.pop(object_id, None)


def _attest_enforcement(
    value: ResolvedContextPacket,
    policy: BudgetPolicyV1,
) -> None:
    object_id = id(value)
    reference = weakref.ref(
        value,
        lambda expired, key=object_id: _remove_enforcement_attestation(
            key,
            expired,
        ),
    )
    with _ATTESTATION_LOCK:
        _ENFORCEMENT_ATTESTATIONS.pop(object_id, None)
        _ENFORCEMENT_ATTESTATIONS[object_id] = (
            reference,
            _enforcement_digest(value, policy),
            _policy_digest(policy),
        )
        while len(_ENFORCEMENT_ATTESTATIONS) > _ATTESTATION_CAPACITY:
            _ENFORCEMENT_ATTESTATIONS.popitem(last=False)


def require_budget_enforcement_attestation(
    value: object,
    policy: object,
) -> None:
    if type(value) is not ResolvedContextPacket or type(policy) is not BudgetPolicyV1:
        raise ContextContractError("invalid_budget_enforcement_attestation")
    if (
        not value.budget_enforced
        or value.budget_policy_id != policy.profile_id
        or value.budget_policy_revision != policy.revision
        or value.budget_enforcement_version != BUDGET_ENFORCEMENT_VERSION
    ):
        raise ContextContractError("invalid_budget_enforcement_attestation")
    actual_value = _enforcement_digest(value, policy)
    actual_policy = _policy_digest(policy)
    with _ATTESTATION_LOCK:
        expected = _ENFORCEMENT_ATTESTATIONS.get(id(value))
        if (
            expected is None
            or expected[0]() is not value
            or not hmac.compare_digest(expected[1], actual_value)
            or not hmac.compare_digest(expected[2], actual_policy)
        ):
            raise ContextContractError("invalid_budget_enforcement_attestation")


def enforce_budget_v1(
    selected: ResolvedContextPacket,
    policy: BudgetPolicyV1,
    *,
    allow_synthetic_budget: bool = False,
    source_projections: tuple[SourceSectionProjectionV1, ...] = (),
    model_capability: ReviewedModelCapabilityV1 | None = None,
    resolved_model: str | None = None,
    require_model_capacity: bool = False,
    requested_output_tokens: int | None = None,
    required_section_ids: frozenset[str] | None = None,
) -> ResolvedContextPacket:
    """Enforce one explicit private policy without source rereads or provider work."""

    if type(allow_synthetic_budget) is not bool or type(require_model_capacity) is not bool:
        raise ContextContractError("invalid_budget_enforcement")
    if type(selected) is not ResolvedContextPacket or selected.budget_enforced:
        raise ContextContractError("invalid_budget_enforcement")
    policy = copy_budget_policy_v1(policy)
    if policy.status is BudgetPolicyStatus.APPROVED:
        if not _is_attested_approved_policy(policy):
            raise ContextContractError("budget_policy_unapproved")
    elif not allow_synthetic_budget:
        raise ContextContractError("budget_policy_unapproved")
    request = selected.packet.request
    if (
        request.scope.lane is not policy.lane
        or request.route is not policy.route
        or request.story_mode is not policy.story_mode
    ):
        raise ContextContractError("budget_policy_unapproved")
    if requested_output_tokens is None:
        requested_output_tokens = policy.output_reserve_tokens
    if (
        type(requested_output_tokens) is not int
        or not 0 < requested_output_tokens <= policy.output_reserve_tokens
    ):
        raise ContextContractError("invalid_output_reserve")

    from nana.runtime.context_compiler import (
        _render_top_level_text,
        estimate_tokens_v1,
        registry_slot_ids,
    )

    user_content = _render_top_level_text(request.current_input)
    if (
        len(user_content) > policy.current_input_max_characters
        or estimate_tokens_v1(user_content)
        > policy.current_input_max_estimated_tokens
    ):
        raise ContextContractError("context_input_too_large")

    initial = _preview(selected, policy)
    limits = {item.section_id: item for item in policy.section_limits}
    selected_ids = {item.id for item in selected.selected_sections}
    if any(item.id not in limits for item in selected.packet.sections):
        raise ContextContractError("invalid_budget_policy")
    for row in policy.section_limits:
        if row.retention is RetentionClass.MINIMUM_REQUIRED and row.section_id not in selected_ids:
            raise ContextContractError("context_required_section_unavailable")
    if required_section_ids is None:
        conditional_required = set()
        if request.temporal_intent:
            conditional_required.add("situation.temporal.v1")
        if request.grounding_intent:
            conditional_required.add("memory.grounding.v1")
        if request.story_mode or request.casual_mode:
            conditional_required.add("mode.turn.private.v1")
    else:
        if type(required_section_ids) is not frozenset or any(
            type(section_id) is not str or section_id not in limits
            for section_id in required_section_ids
        ):
            raise ContextContractError("invalid_required_sections")
        conditional_required = set(required_section_ids)
    if not conditional_required.issubset(selected_ids):
        raise ContextContractError("context_required_state_unavailable")
    mandatory_ids = set(conditional_required)
    mandatory_ids.update(
        section.id for section in selected.selected_sections if section.required
    )
    mandatory_ids.update(
        row.section_id
        for row in policy.section_limits
        if row.retention
        in {RetentionClass.REGISTRY_REQUIRED, RetentionClass.MINIMUM_REQUIRED}
        and row.section_id in selected_ids
    )

    if type(source_projections) is not tuple:
        raise ContextContractError("invalid_source_projection")
    projections: dict[str, SourceSectionProjectionV1] = {}
    retained_units: dict[str, list[int]] = {}
    for projection in source_projections:
        if type(projection) is not SourceSectionProjectionV1:
            raise ContextContractError("invalid_source_projection")
        if projection.section_id in projections or projection.section_id not in limits:
            raise ContextContractError("invalid_source_projection")
        stage = limits[projection.section_id].shedding_stage
        if stage is None or any(_UNIT_STAGES[item.kind] != stage for item in projection.units):
            raise ContextContractError("invalid_source_projection")
        pinned_units = tuple(item for item in projection.units if item.pinned)
        if projection.section_id in mandatory_ids and not pinned_units:
            raise ContextContractError("context_required_section_unavailable")
        if pinned_units:
            original = next(
                item
                for item in selected.packet.sections
                if item.id == projection.section_id
            )
            try:
                minimum_value = projection.project(pinned_units)
            except Exception as exc:
                raise ContextContractError("invalid_source_projection") from exc
            minimum = _projection_output(original, minimum_value)
            if minimum is None or not _has_semantic_content(minimum.payload):
                raise ContextContractError("context_required_section_unavailable")
        projections[projection.section_id] = projection
        retained_units[projection.section_id] = list(range(len(projection.units)))

    token_limit, char_limit = _capacity_limits(
        policy,
        request.model,
        model_capability,
        resolved_model,
        require_model_capacity,
    )
    initial_section_overflow = any(
        not _section_fits(section, limits[section.id])
        for section in selected.selected_sections
    )
    initial_overflow = initial_section_overflow or not _messages_fit(
        initial,
        token_limit,
        char_limit,
        policy,
        model_capability,
    )

    sections = list(selected.packet.sections)
    decisions = {item.section_id: item for item in selected.decisions}
    shedding: list[BudgetSheddingDecision] = []
    slot_order = {
        section_id: index
        for index, section_id in enumerate(
            registry_slot_ids(request.scope.lane, request.route)
        )
    }

    def section_by_id(section_id: str) -> ContextSection:
        return next(item for item in sections if item.id == section_id)

    def is_included(section_id: str) -> bool:
        return decisions[section_id].included

    def pinned_whole(section: ContextSection) -> bool:
        row = limits[section.id]
        no_reducer = section.id not in projections
        projection = projections.get(section.id)
        has_pinned_unit = projection is not None and any(
            projection.units[index].pinned
            for index in retained_units[section.id]
        )
        return (
            row.retention is RetentionClass.REGISTRY_REQUIRED
            or section.id in mandatory_ids
            or section.required
            or has_pinned_unit
            or (row.retention in {RetentionClass.MINIMUM_REQUIRED, RetentionClass.CONDITIONAL} and no_reducer)
            or (section.source.owner == "private_memory" and no_reducer)
        )

    def replace_section(projected: ContextSection) -> None:
        for index, item in enumerate(sections):
            if item.id == projected.id:
                sections[index] = projected
                return
        raise ContextContractError("invalid_source_projection")

    def drop_whole(section: ContextSection, reason: str) -> None:
        row = limits[section.id]
        if section.id in mandatory_ids:
            raise ContextContractError("context_required_section_unavailable")
        if pinned_whole(section) or row.retention is not RetentionClass.OPTIONAL:
            raise ContextContractError("context_budget_exceeded")
        decisions[section.id] = _drop_decision(decisions[section.id], reason)
        shedding.append(
            BudgetSheddingDecision(
                section.id,
                None,
                None,
                reason,
                row.shedding_stage,
            )
        )

    def droppable_unit(section_id: str) -> int | None:
        projection = projections.get(section_id)
        if projection is None:
            return None
        row = limits[section_id]
        eligible = [
            index
            for index in retained_units[section_id]
            if not projection.units[index].pinned
        ]
        if not eligible:
            return None
        return min(
            eligible,
            key=lambda index: _unit_key(
                projection.units[index],
                index,
                row.shedding_stage,
                slot_order[section_id],
            ),
        )

    def drop_unit(section_id: str, source_index: int, reason: str) -> None:
        projection = projections[section_id]
        row = limits[section_id]
        original = section_by_id(section_id)
        retained_units[section_id].remove(source_index)
        retained = tuple(
            projection.units[index] for index in retained_units[section_id]
        )
        try:
            projected_value = projection.project(retained)
        except Exception as exc:
            raise ContextContractError("invalid_source_projection") from exc
        projected = _projection_output(original, projected_value)
        unit = projection.units[source_index]
        shedding.append(
            BudgetSheddingDecision(
                section_id,
                unit.unit_id,
                source_index,
                reason,
                row.shedding_stage,
            )
        )
        if projected is None:
            if section_id in mandatory_ids:
                raise ContextContractError("context_required_section_unavailable")
            if row.retention is not RetentionClass.OPTIONAL or original.required:
                raise ContextContractError("context_budget_exceeded")
            decisions[section_id] = _drop_decision(decisions[section_id], reason)
        else:
            has_retained_minimum = any(
                projection.units[index].pinned
                for index in retained_units[section_id]
            )
            if (
                (section_id in mandatory_ids or has_retained_minimum)
                and not _has_semantic_content(projected.payload)
            ):
                raise ContextContractError("context_required_section_unavailable")
            replace_section(projected)

    for section_id in tuple(item.id for item in selected.selected_sections):
        while is_included(section_id) and not _section_fits(
            section_by_id(section_id),
            limits[section_id],
        ):
            section = section_by_id(section_id)
            row = limits[section_id]
            if row.retention is RetentionClass.REGISTRY_REQUIRED:
                raise ContextContractError("compiler_config_overflow")
            unit_index = droppable_unit(section_id)
            if unit_index is not None:
                drop_unit(section_id, unit_index, "section_budget")
            elif row.retention is RetentionClass.OPTIONAL and not pinned_whole(section):
                drop_whole(section, "section_budget")
            else:
                raise ContextContractError("context_budget_exceeded")

    current = _rebuild(selected, sections, decisions)
    compiled = _preview(current, policy)
    while not _messages_fit(
        compiled,
        token_limit,
        char_limit,
        policy,
        model_capability,
    ):
        candidates: list[tuple[tuple[object, ...], str, int | None]] = []
        for section in current.selected_sections:
            row = limits[section.id]
            if row.shedding_stage is None:
                continue
            unit_index = droppable_unit(section.id)
            if unit_index is not None:
                unit = projections[section.id].units[unit_index]
                candidates.append(
                    (
                        _unit_key(
                            unit,
                            unit_index,
                            row.shedding_stage,
                            slot_order[section.id],
                        ),
                        section.id,
                        unit_index,
                    )
                )
            elif row.retention is RetentionClass.OPTIONAL and not pinned_whole(section):
                candidates.append(
                    (
                        _whole_section_key(
                            section,
                            row.shedding_stage,
                            slot_order[section.id],
                        ),
                        section.id,
                        None,
                    )
                )
        if not candidates:
            raise ContextContractError("context_budget_exceeded")
        _, section_id, unit_index = min(candidates, key=lambda item: item[0])
        if unit_index is None:
            drop_whole(section_by_id(section_id), "global_budget")
        else:
            drop_unit(section_id, unit_index, "global_budget")
        current = _rebuild(selected, sections, decisions)
        compiled = _preview(current, policy)

    final_ids = {section.id for section in current.selected_sections}
    if not mandatory_ids.issubset(final_ids):
        raise ContextContractError("context_required_section_unavailable")
    if any(
        not _has_semantic_content(section.payload)
        for section in current.selected_sections
        if section.id in mandatory_ids
    ):
        raise ContextContractError("context_required_section_unavailable")

    enforced = ResolvedContextPacket(
        current.packet,
        current.selected_sections,
        current.decisions,
        current.total_estimated_tokens,
        budget_enforced=True,
        budget_policy_id=policy.profile_id,
        budget_policy_revision=policy.revision,
        budget_enforcement_version=BUDGET_ENFORCEMENT_VERSION,
        budget_initial_overflow=initial_overflow,
        budget_shedding=tuple(shedding),
    )
    _attest_enforcement(enforced, policy)
    return enforced


__all__ = [
    "BUDGET_ENFORCEMENT_VERSION",
    "POLICY_ARTIFACT_SHA256",
    "POLICY_REVISION",
    "BudgetPolicyV1",
    "BudgetSectionLimitV1",
    "RetentionClass",
    "ReviewedModelCapabilityV1",
    "SourceBudgetUnitV1",
    "SourceSectionProjectionV1",
    "approved_private_budget_policy_v1",
]
