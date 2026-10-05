"""Private BudgetPolicyV1 enforcement at the pure runtime/compiler boundary."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import gc
from pathlib import Path
import sys
import weakref

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))


from nana.runtime import context_budget as context_budget_module  # noqa: E402
from nana.runtime.context_budget import (  # noqa: E402
    POLICY_ARTIFACT_SHA256,
    POLICY_REVISION,
    BudgetPolicyV1,
    BudgetSectionLimitV1,
    ReviewedModelCapabilityV1,
    RetentionClass,
    SourceBudgetUnitV1,
    SourceSectionProjectionV1,
    approved_private_budget_policy_v1,
)
from nana.runtime.context_compiler import (  # noqa: E402
    COMPILER_VERSION,
    CompilerConfig,
    ContextCompiler,
    canonical_message_projection,
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
    Route,
    SemanticRole,
    SourceRef,
    UnresolvedContextRequest,
)
from nana.runtime.context_runtime import (  # noqa: E402
    ContextRuntime,
    GptTurnInput,
    LaneResolver,
    PRIVATE_POLICY_REVISION,
    TrustedIngress,
    build_turn_plan,
)
from nana.runtime.context_telemetry import emit_context_telemetry  # noqa: E402


CAPTURED_AT = 1_700_000_000.0
CONFIG_REVISION = "compiler-config.v1"

SLOTS = {
    "core.private.v1": (Lifetime.STATIC, SemanticRole.INSTRUCTION),
    "policy.private.v1": (Lifetime.STATIC, SemanticRole.INSTRUCTION),
    "contract.output.private.v1": (Lifetime.STATIC, SemanticRole.INSTRUCTION),
    "identity.owner.v1": (Lifetime.DURABLE, SemanticRole.IDENTITY),
    "relationship.shared.v1": (Lifetime.DURABLE, SemanticRole.FACT),
    "preference.rules.v1": (Lifetime.DURABLE, SemanticRole.INSTRUCTION),
    "continuity.summary.v1": (Lifetime.SESSION, SemanticRole.HISTORY),
    "continuity.open_loops.v1": (Lifetime.SESSION, SemanticRole.STATE),
    "continuity.recent_turns.v1": (Lifetime.SESSION, SemanticRole.HISTORY),
    "expression.private.v1": (Lifetime.TURN, SemanticRole.STATE),
    "situation.current.v1": (Lifetime.TURN, SemanticRole.STATE),
    "situation.temporal.v1": (Lifetime.TURN, SemanticRole.STATE),
    "memory.retrieval.v1": (Lifetime.TURN, SemanticRole.EVIDENCE),
    "memory.grounding.v1": (Lifetime.TURN, SemanticRole.EVIDENCE),
    "mode.turn.private.v1": (Lifetime.TURN, SemanticRole.INSTRUCTION),
}

DEFAULT_RETENTION = {
    "core.private.v1": (RetentionClass.REGISTRY_REQUIRED, None),
    "policy.private.v1": (RetentionClass.REGISTRY_REQUIRED, None),
    "contract.output.private.v1": (RetentionClass.REGISTRY_REQUIRED, None),
    "identity.owner.v1": (RetentionClass.MINIMUM_REQUIRED, 4),
    "relationship.shared.v1": (RetentionClass.OPTIONAL, 2),
    "preference.rules.v1": (RetentionClass.OPTIONAL, 1),
    "continuity.summary.v1": (RetentionClass.CONDITIONAL, 4),
    "continuity.open_loops.v1": (RetentionClass.CONDITIONAL, 4),
    "continuity.recent_turns.v1": (RetentionClass.CONDITIONAL, 2),
    "expression.private.v1": (RetentionClass.MINIMUM_REQUIRED, 1),
    "situation.current.v1": (RetentionClass.CONDITIONAL, 3),
    "situation.temporal.v1": (RetentionClass.CONDITIONAL, 3),
    "memory.retrieval.v1": (RetentionClass.CONDITIONAL, 5),
    "memory.grounding.v1": (RetentionClass.CONDITIONAL, 5),
    "mode.turn.private.v1": (RetentionClass.CONDITIONAL, None),
}


class _PrivateAuthority:
    def authorize(self, _request):
        return TrustedIngress(Lane.PRIVATE_OWNER, None)


def _request(
    *,
    current_input: str = "u",
    story_mode: bool = False,
    route: Route = Route.INTERACTIVE,
):
    unresolved = UnresolvedContextRequest(
        "budget-request",
        "budget-correlation",
        route,
        "fixture-model",
        current_input,
        None,
        False,
        None,
        {},
        False,
        story_mode,
        False,
        False,
        False,
    )
    return LaneResolver(
        _PrivateAuthority(),
        wall_clock=lambda: CAPTURED_AT,
        monotonic_clock=lambda: 123.0,
        compiler_config_revision=CONFIG_REVISION,
    ).resolve(unresolved)


def _section(
    section_id: str,
    payload: object,
    *,
    required: bool = False,
    owner: str | None = None,
    max_tokens: int = 100_000,
    relevance: float = 1.0,
) -> ContextSection:
    lifetime, role = SLOTS[section_id]
    if owner is None:
        owner = (
            "core"
            if lifetime is Lifetime.STATIC
            else "owner_identity"
            if section_id == "identity.owner.v1"
            else "expression"
            if section_id == "expression.private.v1"
            else section_id.split(".")[0]
        )
    revision = f"{owner}-r1"
    return ContextSection(
        id=section_id,
        lifetime=lifetime,
        semantic_role=role,
        freshness=Freshness.FRESH,
        visibility=frozenset({Lane.PRIVATE_OWNER}),
        source=SourceRef(owner, f"{owner}.adapter.v1", section_id),
        revision=revision,
        authority=None,
        observed_at=CAPTURED_AT - 1,
        expires_at=None,
        conflict_key=None,
        dedupe_key=f"dedupe:{section_id}",
        max_tokens=max_tokens,
        payload=payload,
        formatter_version="fixture.v1",
        required=required,
        budget_class="fixture",
        semantic_status="active",
        provenance=(),
        relevance=relevance,
    )


def _base_sections(*extras: ContextSection) -> tuple[ContextSection, ...]:
    return (
        _section("core.private.v1", "A", required=True),
        _section("policy.private.v1", "B", required=True),
        _section("contract.output.private.v1", "C", required=True),
        _section("identity.owner.v1", "D"),
        _section("expression.private.v1", "E"),
        *extras,
    )


def _packet(sections: tuple[ContextSection, ...], *, request=None) -> ContextPacket:
    request = request or _request()
    revisions: dict[str, str] = {}
    for section in sections:
        assert revisions.setdefault(section.source.owner, section.revision) == section.revision
    return ContextPacket(
        schema_version=1,
        capture_id="budget-capture",
        snapshot_revision=1,
        request=request,
        source_revisions=tuple(sorted(revisions.items())),
        sections=sections,
        collection_receipt=tuple(
            CollectionDecision(owner, "ALLOW_READ", "lane_policy", revision)
            for owner, revision in sorted(revisions.items())
        ),
    )


def _fixture_policy(
    sections: tuple[ContextSection, ...],
    *,
    global_tokens: int = 100_000,
    global_chars: int = 100_000,
    input_tokens: int = 100_000,
    input_chars: int = 100_000,
    limits: dict[str, tuple[int, int]] | None = None,
    retention: dict[str, tuple[RetentionClass, int | None]] | None = None,
    output_reserve: int = 10,
    framing: int = 2,
) -> BudgetPolicyV1:
    limit_values = limits or {}
    retention_values = {**DEFAULT_RETENTION, **(retention or {})}
    rows = []
    for section in sections:
        token_limit, char_limit = limit_values.get(section.id, (100_000, 100_000))
        retention_class, stage = retention_values[section.id]
        rows.append(
            BudgetSectionLimitV1(
                section.id,
                token_limit,
                char_limit,
                retention_class,
                stage,
            )
        )
    return BudgetPolicyV1(
        profile_id="synthetic.private.v1",
        revision="synthetic-budget.v1",
        artifact_sha256="0" * 64,
        status=BudgetPolicyStatus.SHADOW_CANDIDATE,
        lane=Lane.PRIVATE_OWNER,
        route=Route.INTERACTIVE,
        story_mode=False,
        max_estimated_tokens=global_tokens,
        max_characters=global_chars,
        current_input_max_estimated_tokens=input_tokens,
        current_input_max_characters=input_chars,
        output_reserve_tokens=output_reserve,
        framing_margin_tokens=framing,
        section_limits=tuple(rows),
    )


def _enforce(
    sections: tuple[ContextSection, ...],
    policy: BudgetPolicyV1,
    *,
    request=None,
    projections: tuple[SourceSectionProjectionV1, ...] = (),
    capability=None,
    require_capacity: bool = False,
    requested_output_tokens: int = 5,
    required_section_ids: frozenset[str] | None = None,
):
    packet = _packet(sections, request=request)
    return ContextRuntime().resolve(
        packet,
        policy,
        enforce_budget=True,
        allow_synthetic_budget=True,
        source_projections=projections,
        model_capability=capability,
        require_model_capacity=require_capacity,
        requested_output_tokens=requested_output_tokens,
        required_section_ids=required_section_ids,
    )


def _compile(resolved, policy):
    return ContextCompiler().compile(
        resolved,
        CompilerConfig(
            COMPILER_VERSION,
            CONFIG_REVISION,
            None,
            None,
            policy,
        ),
    )


def _thaw(value):
    if isinstance(value, FrozenMapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_thaw(item) for item in value)
    return value


def test_approved_lookup_is_exact_private_normal_story_and_hash_bound() -> None:
    normal = approved_private_budget_policy_v1(_request())
    story = approved_private_budget_policy_v1(_request(story_mode=True))

    assert (normal.revision, normal.artifact_sha256) == (
        "budget-policy-v1.review.2026-10-01.r1",
        "ED0DE1F88A856689A49BC2904F9BEF4C649C87F3638F4EF5844401FDD975E652",
    )
    assert normal.revision == POLICY_REVISION
    assert normal.artifact_sha256 == POLICY_ARTIFACT_SHA256
    assert (normal.profile_id, normal.max_estimated_tokens, normal.max_characters) == (
        "private_interactive.v1",
        12_000,
        48_000,
    )
    assert (story.profile_id, story.max_estimated_tokens, story.max_characters) == (
        "private_story.v1",
        16_000,
        64_000,
    )
    assert (normal.current_input_max_estimated_tokens, normal.current_input_max_characters) == (8_000, 24_000)
    assert (story.output_reserve_tokens, story.framing_margin_tokens) == (1_600, 512)
    table = {
        row.section_id: (row.max_estimated_tokens, row.max_characters)
        for row in story.section_limits
    }
    assert table == {
        "core.private.v1": (2500, 10000),
        "policy.private.v1": (1000, 4000),
        "contract.output.private.v1": (1600, 6400),
        "identity.owner.v1": (1000, 4000),
        "relationship.shared.v1": (400, 1600),
        "preference.rules.v1": (600, 2400),
        "continuity.summary.v1": (800, 1600),
        "continuity.open_loops.v1": (600, 1800),
        "continuity.recent_turns.v1": (2500, 8000),
        "expression.private.v1": (500, 1600),
        "situation.current.v1": (1000, 4000),
        "situation.temporal.v1": (1800, 5400),
        "memory.retrieval.v1": (1000, 1600),
        "memory.grounding.v1": (1500, 4400),
        "mode.turn.private.v1": (600, 1600),
    }
    with pytest.raises(ContextContractError, match="unapproved_canonical_profile"):
        approved_private_budget_policy_v1(_request(route=Route.PRIVATE_FAST))


def test_direct_policy_copy_cannot_substitute_for_approved_lookup() -> None:
    sections = _base_sections()
    approved = approved_private_budget_policy_v1(_request())
    forged = replace(approved, max_estimated_tokens=1)

    with pytest.raises(ContextContractError, match="budget_policy_unapproved"):
        ContextRuntime().resolve(_packet(sections), forged, enforce_budget=True)


@pytest.mark.parametrize(
    ("tokens", "chars", "expected_optional"),
    [(7, 18, True), (6, 18, False), (7, 17, False)],
)
def test_exact_global_count_includes_separators_and_final_newline(
    tokens: int,
    chars: int,
    expected_optional: bool,
) -> None:
    optional = _section("preference.rules.v1", "F")
    sections = _base_sections(optional)
    policy = _fixture_policy(sections, global_tokens=tokens, global_chars=chars)

    compiled = _compile(_enforce(sections, policy), policy)

    expected_system = "A\n\nB\n\nC\n\nD\n\nF\n\nE\n" if expected_optional else "A\n\nB\n\nC\n\nD\n\nE\n"
    assert compiled.messages[0].content == expected_system
    assert compiled.messages[1].content == "u"
    assert compiled.manifest.input_chars == (18 if expected_optional else 15)
    assert compiled.manifest.input_tokens_est == (7 if expected_optional else 6)
    assert compiled.manifest.budget_enforced is True


def test_current_input_token_and_character_limits_are_independent() -> None:
    sections = _base_sections()
    char_policy = _fixture_policy(sections, input_tokens=100, input_chars=3)
    token_policy = _fixture_policy(sections, input_tokens=1, input_chars=100)

    with pytest.raises(ContextContractError, match="context_input_too_large"):
        _enforce(sections, char_policy, request=_request(current_input="abcd"))
    with pytest.raises(ContextContractError, match="context_input_too_large"):
        _enforce(sections, token_policy, request=_request(current_input="😀😀"))


def test_section_token_and_character_limits_are_independent_and_equality_passes() -> None:
    optional = _section("preference.rules.v1", "abcd")
    sections = _base_sections(optional)
    equal = _fixture_policy(sections, limits={optional.id: (2, 4)})
    char_only = _fixture_policy(sections, limits={optional.id: (100, 3)})
    emoji = replace(optional, payload="😀😀")
    emoji_sections = _base_sections(emoji)
    token_only = _fixture_policy(emoji_sections, limits={emoji.id: (1, 100)})

    assert optional in _enforce(sections, equal).selected_sections
    assert optional not in _enforce(sections, char_only).selected_sections
    assert emoji not in _enforce(emoji_sections, token_only).selected_sections


def test_static_overflow_and_pinned_minimum_overflow_fail_before_provider() -> None:
    oversized_core = _section("core.private.v1", "AAAA", required=True)
    sections = (oversized_core, *_base_sections()[1:])
    static_policy = _fixture_policy(sections, limits={oversized_core.id: (100, 3)})
    pinned_policy = _fixture_policy(_base_sections(), global_tokens=5, global_chars=100)
    provider_calls = 0

    def would_call_provider():
        nonlocal provider_calls
        provider_calls += 1

    with pytest.raises(ContextContractError, match="compiler_config_overflow"):
        _enforce(sections, static_policy)
    with pytest.raises(ContextContractError, match="context_budget_exceeded"):
        plan = _compile(_enforce(_base_sections(), pinned_policy), pinned_policy)
        would_call_provider()
        assert plan
    assert provider_calls == 0


def test_invalid_optional_section_is_validated_before_it_can_be_shed() -> None:
    bad = replace(
        _section("preference.rules.v1", "large optional"),
        id="unknown.optional.v1",
    )
    sections = _base_sections(bad)
    policy = _fixture_policy(_base_sections(), global_tokens=1, global_chars=1)

    with pytest.raises(ContextContractError, match="unknown_section_id"):
        _enforce(sections, policy)


def test_shedding_uses_s1_before_s5_and_preserves_required_sections() -> None:
    preference = _section("preference.rules.v1", "PREF")
    retrieval = _section("memory.retrieval.v1", "MEM", owner="retrieval_fixture")
    sections = _base_sections(preference, retrieval)
    policy = _fixture_policy(sections, global_tokens=100, global_chars=21)

    resolved = _enforce(sections, policy)
    compiled = _compile(resolved, policy)

    assert "PREF" not in compiled.messages[0].content
    assert "MEM" in compiled.messages[0].content
    assert {item.id for item in resolved.selected_sections} >= {
        "core.private.v1",
        "policy.private.v1",
        "contract.output.private.v1",
        "identity.owner.v1",
        "expression.private.v1",
        "memory.retrieval.v1",
    }
    assert resolved.budget_shedding[0].section_id == "preference.rules.v1"
    assert resolved.budget_shedding[0].stage == 1


def test_source_issued_repeated_records_shed_whole_units_in_source_order() -> None:
    original = _section(
        "preference.rules.v1",
        {"records": ("alpha", "beta", "gamma")},
    )
    sections = _base_sections(original)
    retained_literal = '{"records":["beta","gamma"]}'
    policy = _fixture_policy(
        sections,
        limits={original.id: (100, len(retained_literal))},
    )
    units = tuple(
        SourceBudgetUnitV1(
            payload=value,
            unit_id=None,
            relevance=0.5,
            observed_at=CAPTURED_AT,
            pinned=False,
            kind="preference_extra",
        )
        for value in ("alpha", "beta", "gamma")
    )

    def project(retained):
        return replace(
            original,
            payload={"records": tuple(unit.payload for unit in retained)},
        )

    projection = SourceSectionProjectionV1(original.id, units, project)
    resolved = _enforce(sections, policy, projections=(projection,))
    selected = next(item for item in resolved.selected_sections if item.id == original.id)

    assert _thaw(selected.payload) == {"records": ("beta", "gamma")}
    assert [item.source_index for item in resolved.budget_shedding] == [0]
    assert resolved.budget_shedding[0].unit_id is None
    assert retained_literal in _compile(resolved, policy).messages[0].content


@pytest.mark.parametrize("required", [False, True])
def test_required_or_source_pinned_unit_prevents_whole_section_drop(
    required: bool,
) -> None:
    original = _section(
        "preference.rules.v1",
        {"records": ("PINNED",)},
        required=required,
    )
    sections = _base_sections(original)
    policy = _fixture_policy(sections, global_tokens=100, global_chars=15)
    unit = SourceBudgetUnitV1(
        payload="PINNED",
        unit_id="pinned-record",
        relevance=0.0,
        observed_at=None,
        pinned=True,
        kind="preference_extra",
    )
    projection = SourceSectionProjectionV1(
        original.id,
        (unit,),
        lambda retained: replace(
            original,
            payload={"records": tuple(item.payload for item in retained)},
        ),
    )

    with pytest.raises(ContextContractError, match="context_budget_exceeded"):
        _enforce(sections, policy, projections=(projection,))


def test_typed_conditional_requirement_cannot_lose_its_last_unit() -> None:
    original = _section(
        "preference.rules.v1",
        {"records": ("BINDING",)},
    )
    sections = _base_sections(original)
    policy = _fixture_policy(sections, global_tokens=100, global_chars=15)
    unit = SourceBudgetUnitV1(
        payload="BINDING",
        unit_id="binding-rule",
        relevance=0.0,
        observed_at=None,
        pinned=False,
        kind="preference_extra",
    )
    projection = SourceSectionProjectionV1(
        original.id,
        (unit,),
        lambda retained: (
            replace(
                original,
                payload={"records": tuple(item.payload for item in retained)},
            )
            if retained
            else None
        ),
    )

    with pytest.raises(
        ContextContractError,
        match="context_required_section_unavailable",
    ):
        _enforce(
            sections,
            policy,
            projections=(projection,),
            required_section_ids=frozenset({original.id}),
        )


@pytest.mark.parametrize(
    ("section_id", "required", "kind"),
    [
        ("identity.owner.v1", False, "identity_detail"),
        ("preference.rules.v1", True, "preference_extra"),
    ],
)
def test_required_projection_without_explicit_minimum_cannot_become_empty_shell(
    section_id: str,
    required: bool,
    kind: str,
) -> None:
    original = _section(
        section_id,
        {"records": ("detail",)},
        required=required,
    )
    if section_id == "identity.owner.v1":
        sections = tuple(
            original if item.id == section_id else item
            for item in _base_sections()
        )
    else:
        sections = _base_sections(original)
    empty_literal = '{"records":[]}'
    policy = _fixture_policy(
        sections,
        limits={section_id: (100, len(empty_literal))},
    )
    unit = SourceBudgetUnitV1(
        payload="detail",
        unit_id="optional-detail",
        relevance=0.0,
        observed_at=None,
        pinned=False,
        kind=kind,
    )
    projection = SourceSectionProjectionV1(
        section_id,
        (unit,),
        lambda retained: replace(
            original,
            payload={"records": tuple(item.payload for item in retained)},
        ),
    )

    with pytest.raises(
        ContextContractError,
        match="context_required_section_unavailable",
    ):
        _enforce(sections, policy, projections=(projection,))


def test_explicit_source_minimum_survives_while_optional_extras_shed() -> None:
    original = _section(
        "preference.rules.v1",
        {"records": ("BINDING", "extra-one", "extra-two")},
    )
    sections = _base_sections(original)
    minimum_literal = '{"records":["BINDING"]}'
    policy = _fixture_policy(
        sections,
        limits={original.id: (100, len(minimum_literal))},
    )
    units = (
        SourceBudgetUnitV1(
            "BINDING",
            "binding-rule",
            1.0,
            CAPTURED_AT,
            True,
            "preference_extra",
        ),
        SourceBudgetUnitV1(
            "extra-one",
            "extra-one",
            0.0,
            CAPTURED_AT - 2,
            False,
            "preference_extra",
        ),
        SourceBudgetUnitV1(
            "extra-two",
            "extra-two",
            0.1,
            CAPTURED_AT - 1,
            False,
            "preference_extra",
        ),
    )
    projection = SourceSectionProjectionV1(
        original.id,
        units,
        lambda retained: replace(
            original,
            payload={"records": tuple(item.payload for item in retained)},
        ),
    )

    resolved = _enforce(
        sections,
        policy,
        projections=(projection,),
        required_section_ids=frozenset({original.id}),
    )
    retained = next(
        item for item in resolved.selected_sections if item.id == original.id
    )

    assert _thaw(retained.payload) == {"records": ("BINDING",)}
    assert [item.unit_id for item in resolved.budget_shedding] == [
        "extra-one",
        "extra-two",
    ]


def test_required_relationship_and_opaque_continuity_cannot_be_shed() -> None:
    relationship = _section("relationship.shared.v1", "RELATION", required=True)
    continuity = _section("continuity.recent_turns.v1", "TURN")
    sections = _base_sections(relationship, continuity)
    policy = _fixture_policy(sections, global_tokens=100, global_chars=20)

    with pytest.raises(ContextContractError, match="context_budget_exceeded"):
        _enforce(sections, policy)


def test_task11_memory_sections_remain_coupled_without_source_reducer() -> None:
    preference = _section("preference.rules.v1", "MEM-PREF", owner="private_memory")
    retrieval = _section("memory.retrieval.v1", "MEM-FACT", owner="private_memory")
    sections = _base_sections(preference, retrieval)
    policy = _fixture_policy(sections, global_tokens=100, global_chars=20)

    with pytest.raises(ContextContractError, match="context_budget_exceeded"):
        _enforce(sections, policy)


def test_model_capacity_requires_reviewed_counter_and_applies_reserves() -> None:
    sections = _base_sections()
    policy = _fixture_policy(sections, output_reserve=3, framing=2)

    with pytest.raises(ContextContractError, match="model_context_capacity_unavailable"):
        _enforce(
            sections,
            policy,
            require_capacity=True,
            requested_output_tokens=3,
        )

    capability = ReviewedModelCapabilityV1(
        provider="fixture-provider",
        model="fixture-model",
        revision="fixture-capability.v1",
        context_window_tokens=10,
        provider_input_limit_tokens=None,
        max_input_characters=None,
        count_input_tokens=lambda messages: 6,
    )
    with pytest.raises(ContextContractError, match="context_budget_exceeded"):
        _enforce(
            sections,
            policy,
            capability=capability,
            require_capacity=True,
            requested_output_tokens=3,
        )


def test_compiler_requires_budget_attestation_instead_of_caller_boolean() -> None:
    sections = _base_sections()
    legacy_policy = BudgetPolicy(
        "legacy.measurement.v1",
        "legacy-r1",
        BudgetPolicyStatus.SHADOW_CANDIDATE,
        100_000,
        100_000,
    )
    unresolved = ContextRuntime().resolve(_packet(sections), legacy_policy)
    forged = replace(
        unresolved,
        budget_enforced=True,
        budget_policy_id="synthetic.private.v1",
        budget_policy_revision=POLICY_REVISION,
        budget_enforcement_version="budget-enforcement.v1",
    )
    policy = _fixture_policy(sections)

    with pytest.raises(ContextContractError, match="invalid_budget_enforcement_attestation"):
        _compile(forged, policy)


def test_approved_policy_attestation_rejects_deterministic_identity_substitution() -> None:
    issued = approved_private_budget_policy_v1(_request())
    impostor = replace(issued)
    registry = context_budget_module._APPROVED_POLICY_ATTESTATIONS
    registry[id(impostor)] = registry[id(issued)]
    try:
        with pytest.raises(ContextContractError, match="budget_policy_unapproved"):
            context_budget_module.copy_budget_policy_v1(impostor)
    finally:
        registry.pop(id(impostor), None)


def test_enforcement_attestation_rejects_deterministic_identity_substitution() -> None:
    sections = _base_sections()
    policy = _fixture_policy(sections)
    issued = _enforce(sections, policy)
    impostor = replace(issued)
    registry = context_budget_module._ENFORCEMENT_ATTESTATIONS
    registry[id(impostor)] = registry[id(issued)]
    try:
        with pytest.raises(
            ContextContractError,
            match="invalid_budget_enforcement_attestation",
        ):
            _compile(impostor, policy)
    finally:
        registry.pop(id(impostor), None)


def test_attestation_registries_do_not_retain_policy_or_private_packet() -> None:
    policy = approved_private_budget_policy_v1(_request())
    policy_reference = weakref.ref(policy)
    del policy
    gc.collect()
    assert policy_reference() is None

    sections = _base_sections()
    fixture_policy = _fixture_policy(sections)
    enforced = _enforce(sections, fixture_policy)
    packet_reference = weakref.ref(enforced)
    del enforced
    gc.collect()
    assert packet_reference() is None


def test_budget_attestation_is_bounded_and_safe_under_concurrent_issuance() -> None:
    def compile_one(index: int) -> tuple[bool, str]:
        request = _request(current_input=f"parallel-{index}")
        sections = _base_sections()
        policy = approved_private_budget_policy_v1(request)
        resolved = ContextRuntime().resolve(
            _packet(sections, request=request),
            policy,
            enforce_budget=True,
            requested_output_tokens=5,
        )
        compiled = _compile(resolved, policy)
        return compiled.manifest.budget_enforced, compiled.messages[1].content

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(compile_one, range(8)))

    assert results == tuple((True, f"parallel-{index}") for index in range(8))


def test_enforcement_preserves_legacy_bytes_and_telemetry_cannot_change_them() -> None:
    sections = _base_sections()
    fixture = _fixture_policy(sections)
    enforced = _compile(_enforce(sections, fixture), fixture)
    legacy_policy = BudgetPolicy(
        "legacy.measurement.v1",
        "legacy-r1",
        BudgetPolicyStatus.SHADOW_CANDIDATE,
        100_000,
        100_000,
    )
    legacy_resolved = ContextRuntime().resolve(_packet(sections), legacy_policy)
    legacy = _compile(legacy_resolved, legacy_policy)
    before = canonical_message_projection(enforced.messages)

    class FailingSink:
        def emit(self, _receipt):
            raise RuntimeError("synthetic sink failure")

    off = emit_context_telemetry(
        enforced,
        label="candidate_context",
        production_bound=False,
        sink=FailingSink(),
        enabled=False,
    )
    on = emit_context_telemetry(
        enforced,
        label="candidate_context",
        production_bound=False,
        sink=FailingSink(),
        enabled=True,
    )

    assert legacy.messages == enforced.messages
    assert legacy.manifest.budget_enforced is False
    assert enforced.manifest.budget_enforced is True
    assert off.manifest.budget_shedding == on.manifest.budget_shedding == ()
    assert canonical_message_projection(enforced.messages) == before


def test_private_turn_plan_uses_approved_enforcement_but_live_gate_stays_closed() -> None:
    request = _request()
    sections = _base_sections()
    plan = build_turn_plan(
        GptTurnInput(
            request,
            sections,
            None,
            "fixture-model",
            5,
            PRIVATE_POLICY_REVISION,
        ),
        redact=lambda value: value,
    )
    detached = plan.transport_messages()
    detached[0]["content"] = "changed"

    assert plan.compiled.manifest.budget_enforced is True
    assert plan.compiled.manifest.budget_policy_id == "private_interactive.v1"
    assert plan.compiled.messages[0].content != "changed"


def test_preference_only_private_memory_does_not_invent_grounding_dependency() -> None:
    request = _request()
    preference = _section(
        "preference.rules.v1",
        {"records": ("Keep answers concise",)},
        owner="private_memory",
    )

    plan = build_turn_plan(
        GptTurnInput(
            request,
            _base_sections(preference),
            None,
            "fixture-model",
            5,
            PRIVATE_POLICY_REVISION,
        ),
        redact=lambda value: value,
    )

    assert plan.compiled.manifest.budget_enforced is True
    assert next(
        item
        for item in plan.compiled.manifest.sections
        if item.section_id == "preference.rules.v1"
    ).included
    assert all(
        item.section_id != "memory.grounding.v1"
        for item in plan.compiled.manifest.sections
    )
