"""Behavioral tests for the pure deterministic Context Compiler."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import math
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))


import nana.runtime.context_compiler as context_compiler  # noqa: E402
from nana.runtime.context_compiler import (  # noqa: E402
    COMPILER_VERSION,
    WIRE_PROFILE,
    CompilerConfig,
    ContextCompiler,
    canonical_message_projection,
    estimate_tokens_v1,
)
from nana.runtime.context_contracts import (  # noqa: E402
    BudgetPolicy,
    BudgetPolicyStatus,
    CollectionDecision,
    CompiledMessage,
    ContextContractError,
    ContextPacket,
    ContextSection,
    Freshness,
    FrozenMapping,
    Lane,
    Lifetime,
    ProvenanceRef,
    ResolvedContextPacket,
    Route,
    SelectionDecision,
    SemanticRole,
    SourceRef,
    UnresolvedContextRequest,
    _make_resolved_context_request,
    _make_resolved_context_scope,
)
from nana.runtime.public_context_boundary import PublicEventScope  # noqa: E402
from nana.runtime.public_identity import CanonicalPublicIdentity  # noqa: E402


REGISTRIES: dict[
    tuple[Lane, Route],
    tuple[tuple[str, Lifetime, SemanticRole, bool], ...],
] = {
    (Lane.PRIVATE_OWNER, Route.INTERACTIVE): (
        ("core.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, True),
        ("policy.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, True),
        (
            "contract.output.private.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            True,
        ),
        ("identity.owner.v1", Lifetime.DURABLE, SemanticRole.IDENTITY, False),
        ("relationship.shared.v1", Lifetime.DURABLE, SemanticRole.FACT, False),
        (
            "preference.rules.v1",
            Lifetime.DURABLE,
            SemanticRole.INSTRUCTION,
            False,
        ),
        ("continuity.summary.v1", Lifetime.SESSION, SemanticRole.HISTORY, False),
        (
            "continuity.open_loops.v1",
            Lifetime.SESSION,
            SemanticRole.STATE,
            False,
        ),
        (
            "continuity.recent_turns.v1",
            Lifetime.SESSION,
            SemanticRole.HISTORY,
            False,
        ),
        ("expression.private.v1", Lifetime.TURN, SemanticRole.STATE, False),
        ("situation.current.v1", Lifetime.TURN, SemanticRole.STATE, False),
        ("situation.temporal.v1", Lifetime.TURN, SemanticRole.STATE, False),
        ("memory.retrieval.v1", Lifetime.TURN, SemanticRole.EVIDENCE, False),
        ("memory.grounding.v1", Lifetime.TURN, SemanticRole.EVIDENCE, False),
        (
            "mode.turn.private.v1",
            Lifetime.TURN,
            SemanticRole.INSTRUCTION,
            False,
        ),
    ),
    (Lane.PUBLIC_STAGE, Route.INTERACTIVE): (
        ("core.public.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, True),
        ("policy.public.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, True),
        (
            "contract.output.public.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            True,
        ),
        (
            "continuity.public_room.v1",
            Lifetime.SESSION,
            SemanticRole.HISTORY,
            False,
        ),
        ("expression.public.v1", Lifetime.TURN, SemanticRole.STATE, False),
        (
            "public.request_context.v1",
            Lifetime.TURN,
            SemanticRole.STATE,
            False,
        ),
        (
            "memory.public_grounding.v1",
            Lifetime.TURN,
            SemanticRole.EVIDENCE,
            False,
        ),
        (
            "mode.turn.public.v1",
            Lifetime.TURN,
            SemanticRole.INSTRUCTION,
            False,
        ),
    ),
    (Lane.PUBLIC_STAGE, Route.YOUTUBE_CUM2): (
        ("core.public.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, True),
        (
            "policy.public.livestream.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            True,
        ),
        (
            "contract.output.cum2.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            True,
        ),
        (
            "continuity.public_room.v1",
            Lifetime.SESSION,
            SemanticRole.HISTORY,
            False,
        ),
        ("expression.public.v1", Lifetime.TURN, SemanticRole.STATE, False),
        (
            "public.request_context.v1",
            Lifetime.TURN,
            SemanticRole.STATE,
            False,
        ),
    ),
    (Lane.OPERATOR_BACKSTAGE, Route.INTERACTIVE): (
        ("core.operator.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, True),
        (
            "policy.operator.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            True,
        ),
        (
            "contract.output.operator.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            True,
        ),
        (
            "expression.operator.v1",
            Lifetime.TURN,
            SemanticRole.STATE,
            False,
        ),
        (
            "operator.request_context.v1",
            Lifetime.TURN,
            SemanticRole.STATE,
            False,
        ),
        (
            "situation.operator_status.v1",
            Lifetime.TURN,
            SemanticRole.STATE,
            False,
        ),
    ),
    (Lane.PRIVATE_OWNER, Route.AUTONOMY): (
        ("core.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, True),
        ("policy.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, True),
        (
            "policy.autonomy.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            True,
        ),
        (
            "contract.output.autonomy.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            True,
        ),
        ("expression.private.v1", Lifetime.TURN, SemanticRole.STATE, False),
        ("situation.current.v1", Lifetime.TURN, SemanticRole.STATE, False),
    ),
    (Lane.PUBLIC_STAGE, Route.AUTONOMY): (
        ("core.public.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, True),
        ("policy.public.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION, True),
        (
            "policy.autonomy.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            True,
        ),
        (
            "contract.output.autonomy.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            True,
        ),
        ("expression.public.v1", Lifetime.TURN, SemanticRole.STATE, False),
        ("situation.current.v1", Lifetime.TURN, SemanticRole.STATE, False),
    ),
}


def _public_scope() -> PublicEventScope:
    return PublicEventScope(
        platform="youtube",
        room_id="room-compiler",
        stream_session_id="stream-compiler",
        event_id="event-compiler",
        display_name="Compiler Viewer",
        identity=CanonicalPublicIdentity(
            platform="youtube",
            author_id="author-compiler",
            actor_key="youtube:author-compiler",
        ),
    )


def _request(
    *,
    lane: Lane,
    route: Route,
    current_input: str = "hello",
    request_id: str = "request-compiler",
    model: str = "logical-model",
    config_revision: str = "compiler-config.v1",
):
    raw = UnresolvedContextRequest(
        request_id=request_id,
        correlation_id=f"correlation:{request_id}",
        route=route,
        model=model,
        current_input=current_input,
        viewer_name=None,
        stream_mode=lane is Lane.PUBLIC_STAGE,
        public_platform="youtube" if lane is Lane.PUBLIC_STAGE else None,
        caller_metadata={},
        bridge_system=lane is Lane.OPERATOR_BACKSTAGE,
        story_mode=False,
        casual_mode=True,
        temporal_intent=False,
        grounding_intent=False,
    )
    scope = _make_resolved_context_scope(
        lane=lane,
        interaction_scope={
            Lane.PRIVATE_OWNER: "private_owner",
            Lane.PUBLIC_STAGE: "public_viewer",
            Lane.OPERATOR_BACKSTAGE: "bridge_system",
        }[lane],
        public_scope=_public_scope() if lane is Lane.PUBLIC_STAGE else None,
        policy_id={
            Lane.PRIVATE_OWNER: "private_compiler.v1",
            Lane.PUBLIC_STAGE: "public_compiler.v1",
            Lane.OPERATOR_BACKSTAGE: "operator_compiler.v1",
        }[lane],
        resolver_version="resolver.v1",
    )
    return _make_resolved_context_request(
        unresolved=raw,
        scope=scope,
        public_context={},
        captured_wall_time=1_700_000_000.0,
        captured_monotonic_time=1_234.5,
        compiler_config_revision=config_revision,
    )


def _section(
    section_id: str,
    lifetime: Lifetime,
    semantic_role: SemanticRole,
    *,
    lane: Lane,
    payload: object,
    required: bool,
    max_tokens: int = 1_000,
) -> ContextSection:
    return ContextSection(
        id=section_id,
        lifetime=lifetime,
        semantic_role=semantic_role,
        freshness=Freshness.FRESH,
        visibility=frozenset({lane}),
        source=SourceRef(
            owner="fixture_source",
            adapter="fixture.adapter.v1",
            projection="fixture.projection.v1",
        ),
        revision="fixture-source-r1",
        authority="fixture_authority",
        observed_at=100.0,
        expires_at=200.0,
        conflict_key=None,
        dedupe_key=f"dedupe:{section_id}",
        max_tokens=max_tokens,
        payload=payload,
        formatter_version="formatter.v1",
        required=required,
        budget_class="fixture",
        semantic_status="active",
        provenance=(ProvenanceRef("fixture_source", "event-1", section_id),),
        relevance=1.0,
    )


def _resolved_packet(
    *,
    lane: Lane = Lane.PRIVATE_OWNER,
    route: Route = Route.INTERACTIVE,
    candidate_ids: tuple[str, ...] | None = None,
    payloads: dict[str, object] | None = None,
    dropped_ids: frozenset[str] = frozenset(),
    required_overrides: dict[str, bool] | None = None,
    lifetime_overrides: dict[str, Lifetime] | None = None,
    role_overrides: dict[str, SemanticRole] | None = None,
    max_tokens_overrides: dict[str, int] | None = None,
    extra_sections: tuple[ContextSection, ...] = (),
    current_input: str = "hello",
    total_estimated_tokens: int = 0,
    request_id: str = "request-compiler",
) -> ResolvedContextPacket:
    registry = REGISTRIES.get(
        (lane, route),
        REGISTRIES[(Lane.PRIVATE_OWNER, Route.INTERACTIVE)],
    )
    by_id = {slot[0]: slot for slot in registry}
    if candidate_ids is None:
        candidate_ids = tuple(slot[0] for slot in registry if slot[3])
    payload_values = {} if payloads is None else payloads
    required_values = {} if required_overrides is None else required_overrides
    lifetime_values = {} if lifetime_overrides is None else lifetime_overrides
    role_values = {} if role_overrides is None else role_overrides
    max_token_values = {} if max_tokens_overrides is None else max_tokens_overrides

    sections = []
    for section_id in candidate_ids:
        slot = by_id[section_id]
        sections.append(
            _section(
                section_id,
                lifetime_values.get(section_id, slot[1]),
                role_values.get(section_id, slot[2]),
                lane=lane,
                payload=payload_values.get(section_id, f"payload:{section_id}"),
                required=required_values.get(section_id, slot[3]),
                max_tokens=max_token_values.get(section_id, 1_000),
            )
        )
    sections.extend(extra_sections)

    request = _request(
        lane=lane,
        route=route,
        current_input=current_input,
        request_id=request_id,
    )
    packet = ContextPacket(
        schema_version=1,
        capture_id=f"capture:{request_id}",
        snapshot_revision=7,
        request=request,
        source_revisions=(("fixture_source", "fixture-source-r1"),),
        sections=tuple(sections),
        collection_receipt=(
            CollectionDecision(
                source="fixture_source",
                decision="ALLOW_READ",
                reason="lane_policy",
                source_revision="fixture-source-r1",
            ),
        ),
    )
    decisions = []
    selected = []
    for section in packet.sections:
        included = section.id not in dropped_ids
        if included:
            selected.append(section)
        decisions.append(
            SelectionDecision(
                section_id=section.id,
                decision="included" if included else "irrelevant",
                reason=(
                    "required"
                    if included and section.required
                    else "selected"
                    if included
                    else "not_relevant"
                ),
                source=section.source,
                lifetime=section.lifetime,
                semantic_role=section.semantic_role,
                revision=section.revision,
                formatter_version=section.formatter_version,
                budget_class=section.budget_class,
                chars=None,
                token_estimate=None,
            )
        )
    return ResolvedContextPacket(
        packet=packet,
        selected_sections=tuple(reversed(selected)),
        decisions=tuple(reversed(decisions)),
        total_estimated_tokens=total_estimated_tokens,
    )


def _config(
    *,
    max_tokens: int = 100_000,
    max_chars: int = 100_000,
    policy_revision: str = "candidate.v1",
    status: BudgetPolicyStatus = BudgetPolicyStatus.SHADOW_CANDIDATE,
    resolved_provider: str | None = "fixture-provider",
    resolved_model: str | None = "fixture-model",
) -> CompilerConfig:
    return CompilerConfig(
        compiler_version=COMPILER_VERSION,
        revision="compiler-config.v1",
        resolved_provider=resolved_provider,
        resolved_model=resolved_model,
        budget_policy=BudgetPolicy(
            policy_id="fixture-budget.v1",
            revision=policy_revision,
            status=status,
            max_estimated_tokens=max_tokens,
            max_characters=max_chars,
        ),
    )


def _compile(**packet_changes: object):
    return ContextCompiler().compile(_resolved_packet(**packet_changes), _config())


def test_literal_golden_projection_and_hashes_are_exact() -> None:
    packet = _resolved_packet(
        payloads={
            "core.private.v1": "CORE",
            "policy.private.v1": {"b": True, "a": 1.25},
            "contract.output.private.v1": "OUTPUT",
        },
    )
    compiled = ContextCompiler().compile(packet, _config())

    expected_projection = (
        b'[["system","CORE\\n\\n{\\"a\\":1.250000,\\"b\\":true}'
        b'\\n\\nOUTPUT\\n"],["user","hello"]]'
    )
    assert compiled.messages == (
        CompiledMessage(
            role="system",
            content='CORE\n\n{"a":1.250000,"b":true}\n\nOUTPUT\n',
        ),
        CompiledMessage(role="user", content="hello"),
    )
    assert canonical_message_projection(compiled.messages) == expected_projection
    assert compiled.static_prefix_hash == (
        "a9e0bef0dbce1069e8c5ff84ba3f7fa1d7f42cbbf8bb81c8dfedfbc1776d868f"
    )
    assert compiled.durable_prefix_hash == compiled.static_prefix_hash
    assert compiled.session_prefix_hash == compiled.static_prefix_hash
    assert compiled.full_context_hash == (
        "406697b15c7f9bbf3b4c61692a9594b2f18dcd40c4c53f4f615ca307299cb8f8"
    )
    assert compiled.static_prefix_chars == 37
    assert compiled.manifest.static_prefix_tokens_est == 13
    assert compiled.manifest.input_chars == 43
    assert compiled.manifest.input_tokens_est == 15


def test_wire_profile_is_exactly_system_plus_current_user() -> None:
    compiled = _compile(current_input="sole user content")

    assert [message.role for message in compiled.messages] == ["system", "user"]
    assert compiled.messages[1].content == "sole user content"
    assert compiled.manifest.wire_profile == "legacy_system_user.v1"
    assert WIRE_PROFILE == "legacy_system_user.v1"
    assert not hasattr(context_compiler, "APPEND_FRIENDLY_PROFILE")
    with pytest.raises(TypeError):
        CompilerConfig(  # type: ignore[call-arg]
            compiler_version=COMPILER_VERSION,
            revision="compiler-config.v1",
            resolved_provider="provider",
            resolved_model="model",
            budget_policy=_config().budget_policy,
            wire_profile="append_friendly.v1",
        )


def test_text_renderer_normalizes_nfc_lf_and_terminal_whitespace() -> None:
    nfd = _resolved_packet(
        payloads={"core.private.v1": "  Cafe\u0301  \r\nline\t\r\n\r\n"},
        current_input="Qe\u0301 \r\n\r\n",
    )
    nfc = _resolved_packet(
        payloads={"core.private.v1": "  Caf\u00e9\nline"},
        current_input="Q\u00e9",
    )

    first = ContextCompiler().compile(nfd, _config())
    second = ContextCompiler().compile(nfc, _config())

    assert first.messages == second.messages
    assert first.messages[0].content.startswith("  Caf\u00e9\nline\n\n")
    assert first.messages[1].content == "Q\u00e9"
    assert "\r" not in first.messages[0].content
    assert first.full_context_hash == second.full_context_hash


def test_structured_renderer_is_typed_compact_and_escaped() -> None:
    compiled = _compile(
        payloads={
            "policy.private.v1": {
                "z": "quote\" slash\\ control\x01 nonascii \u96ea",
                "a": (True, None, 2, 1.5),
            }
        }
    )

    expected = (
        '{"a":[true,null,2,1.500000],'
        '"z":"quote\\\" slash\\\\ control\\u0001 nonascii \u96ea"}'
    )
    assert expected in compiled.messages[0].content
    assert "1.500000" in compiled.messages[0].content
    assert "2.000000" not in compiled.messages[0].content


def test_mapping_insertion_order_does_not_change_bytes() -> None:
    first = _compile(
        payloads={"policy.private.v1": {"z": 3, "a": 1, "m": 2}}
    )
    second = _compile(
        payloads={"policy.private.v1": {"m": 2, "z": 3, "a": 1}}
    )

    assert first.messages == second.messages
    assert first.full_context_hash == second.full_context_hash


def test_typed_tuple_order_remains_semantically_significant() -> None:
    first = _compile(payloads={"policy.private.v1": ("first", "second")})
    second = _compile(payloads={"policy.private.v1": ("second", "first")})

    assert first.messages != second.messages
    assert first.full_context_hash != second.full_context_hash


def test_top_level_section_ids_are_metadata_only_without_synthetic_headings() -> None:
    compiled = _compile(
        payloads={
            "core.private.v1": "CORE PAYLOAD",
            "policy.private.v1": "POLICY PAYLOAD",
            "contract.output.private.v1": "OUTPUT PAYLOAD",
        }
    )

    assert compiled.messages[0].content == (
        "CORE PAYLOAD\n\nPOLICY PAYLOAD\n\nOUTPUT PAYLOAD\n"
    )
    assert "core.private.v1" not in compiled.messages[0].content


def test_estimator_v1_uses_normalized_character_and_utf8_lengths() -> None:
    assert estimate_tokens_v1("") == 0
    assert estimate_tokens_v1("abcdefg") == 3
    assert estimate_tokens_v1("\u96ea\u96ea\u96ea\u96ea") == 3
    assert estimate_tokens_v1("e\u0301") == estimate_tokens_v1("\u00e9") == 1


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_compiler_revalidates_and_rejects_forged_nonfinite_payloads(
    value: float,
) -> None:
    packet = _resolved_packet(payloads={"core.private.v1": {"value": 1.0}})
    payload = packet.packet.sections[0].payload
    assert isinstance(payload, FrozenMapping)
    object.__setattr__(payload, "_items", (("value", value),))

    with pytest.raises(ContextContractError, match="non_finite_number"):
        ContextCompiler().compile(packet, _config())


@pytest.mark.parametrize("lane_route", tuple(REGISTRIES))
def test_each_supported_registry_is_exact_and_manifest_ordered(
    lane_route: tuple[Lane, Route],
) -> None:
    lane, route = lane_route
    registry = REGISTRIES[lane_route]
    ids = tuple(slot[0] for slot in registry)
    compiled = _compile(
        lane=lane,
        route=route,
        candidate_ids=tuple(reversed(ids)),
    )

    assert tuple(row.section_id for row in compiled.manifest.sections) == ids
    assert compiled.manifest.selected_sections == len(ids)


def test_private_fast_fails_closed_without_registry_aliasing() -> None:
    packet = _resolved_packet(
        lane=Lane.PRIVATE_OWNER,
        route=Route.PRIVATE_FAST,
    )

    with pytest.raises(ContextContractError, match="unsupported_compiler_profile"):
        ContextCompiler().compile(packet, _config())


@pytest.mark.parametrize("dropped", [False, True])
def test_unknown_candidates_fail_even_when_dropped(dropped: bool) -> None:
    unknown = _section(
        "unknown.private.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        payload="secret candidate payload",
        required=False,
    )
    packet = _resolved_packet(
        extra_sections=(unknown,),
        dropped_ids=frozenset({unknown.id}) if dropped else frozenset(),
    )

    with pytest.raises(ContextContractError, match="unknown_section_id") as caught:
        ContextCompiler().compile(packet, _config())
    assert "secret candidate payload" not in str(caught.value)


def test_public_registry_cannot_admit_private_section_ids() -> None:
    private = _section(
        "identity.owner.v1",
        Lifetime.DURABLE,
        SemanticRole.IDENTITY,
        lane=Lane.PUBLIC_STAGE,
        payload="private identity",
        required=False,
    )
    packet = _resolved_packet(
        lane=Lane.PUBLIC_STAGE,
        route=Route.INTERACTIVE,
        extra_sections=(private,),
        dropped_ids=frozenset({private.id}),
    )

    with pytest.raises(ContextContractError, match="unknown_section_id"):
        ContextCompiler().compile(packet, _config())


@pytest.mark.parametrize(
    ("lifetime", "role", "error"),
    [
        (Lifetime.TURN, SemanticRole.INSTRUCTION, "invalid_section_lifetime"),
        (Lifetime.STATIC, SemanticRole.STATE, "invalid_section_role"),
    ],
)
def test_registry_rejects_wrong_lifetime_or_semantic_role(
    lifetime: Lifetime,
    role: SemanticRole,
    error: str,
) -> None:
    packet = _resolved_packet(
        lifetime_overrides={"core.private.v1": lifetime},
        role_overrides={"core.private.v1": role},
    )

    with pytest.raises(ContextContractError, match=error):
        ContextCompiler().compile(packet, _config())


def test_registry_requires_structural_core_policy_and_output() -> None:
    packet = _resolved_packet(
        candidate_ids=("policy.private.v1", "contract.output.private.v1"),
    )

    with pytest.raises(ContextContractError, match="missing_required_section"):
        ContextCompiler().compile(packet, _config())


def test_registry_required_slot_cannot_be_dropped_even_if_row_flag_is_false() -> None:
    packet = _resolved_packet(
        dropped_ids=frozenset({"core.private.v1"}),
        required_overrides={"core.private.v1": False},
    )

    with pytest.raises(ContextContractError, match="required_section_dropped"):
        ContextCompiler().compile(packet, _config())


def test_any_candidate_marked_required_cannot_be_dropped() -> None:
    ids = tuple(slot[0] for slot in REGISTRIES[(Lane.PRIVATE_OWNER, Route.INTERACTIVE)])
    packet = _resolved_packet(
        candidate_ids=ids,
        dropped_ids=frozenset({"expression.private.v1"}),
        required_overrides={"expression.private.v1": True},
    )

    with pytest.raises(ContextContractError, match="required_section_dropped"):
        ContextCompiler().compile(packet, _config())


def test_duplicate_candidate_is_rejected_again_at_compiler_entry() -> None:
    packet = _resolved_packet()
    first = packet.packet.sections[0]
    object.__setattr__(packet.packet, "sections", (first, first))

    with pytest.raises(ContextContractError, match="duplicate_section_id"):
        ContextCompiler().compile(packet, _config())


def test_mutated_sealed_request_is_rejected_again_at_compiler_entry() -> None:
    packet = _resolved_packet()
    object.__setattr__(packet.packet.request, "current_input", "forged secret")

    with pytest.raises(ContextContractError, match="invalid_resolution_seal") as caught:
        ContextCompiler().compile(packet, _config())
    assert "forged secret" not in str(caught.value)


def test_real_autonomy_trigger_section_is_rejected_and_request_is_virtual_user() -> None:
    compiled = _compile(
        lane=Lane.PRIVATE_OWNER,
        route=Route.AUTONOMY,
        current_input="  wake now  \r\n",
    )
    assert compiled.messages[1] == CompiledMessage("user", "  wake now")
    assert "autonomy.trigger.v1" not in compiled.messages[0].content

    real_trigger = _section(
        "autonomy.trigger.v1",
        Lifetime.TURN,
        SemanticRole.STATE,
        lane=Lane.PRIVATE_OWNER,
        payload="forged trigger",
        required=True,
    )
    packet = _resolved_packet(
        lane=Lane.PRIVATE_OWNER,
        route=Route.AUTONOMY,
        extra_sections=(real_trigger,),
    )
    with pytest.raises(ContextContractError, match="virtual_user_section_forbidden"):
        ContextCompiler().compile(packet, _config())


def test_dropped_candidates_are_rendered_for_safe_manifest_sizes_in_registry_order() -> None:
    ids = tuple(slot[0] for slot in REGISTRIES[(Lane.PRIVATE_OWNER, Route.INTERACTIVE)])
    packet = _resolved_packet(
        candidate_ids=tuple(reversed(ids)),
        payloads={"expression.private.v1": "DROP"},
        dropped_ids=frozenset({"expression.private.v1"}),
    )
    compiled = ContextCompiler().compile(packet, _config())
    rows = {row.section_id: row for row in compiled.manifest.sections}

    assert tuple(row.section_id for row in compiled.manifest.sections) == ids
    assert rows["expression.private.v1"].included is False
    assert rows["expression.private.v1"].chars == 4
    assert rows["expression.private.v1"].token_estimate == 2
    assert "DROP" not in compiled.messages[0].content


def test_source_and_collection_receipt_coherence_is_retained() -> None:
    packet = _resolved_packet()
    compiled = ContextCompiler().compile(packet, _config())

    assert compiled.manifest.source_revisions == packet.packet.source_revisions
    assert compiled.manifest.collection == packet.packet.collection_receipt
    assert all(
        row.source.owner == "fixture_source"
        and row.revision == "fixture-source-r1"
        for row in compiled.manifest.sections
    )


def test_equivalent_shuffled_source_metadata_produces_identical_manifests() -> None:
    base = _resolved_packet()
    owner_by_id = {
        "core.private.v1": "z_source",
        "policy.private.v1": "a_source",
        "contract.output.private.v1": "a_source",
    }
    revision_by_owner = {"a_source": "a-r1", "z_source": "z-r1"}
    sections = tuple(
        replace(
            section,
            source=SourceRef(
                owner=owner_by_id[section.id],
                adapter="fixture.adapter.v1",
                projection="fixture.projection.v1",
            ),
            revision=revision_by_owner[owner_by_id[section.id]],
            provenance=(
                ProvenanceRef(owner_by_id[section.id], "event-1", section.id),
            ),
        )
        for section in base.packet.sections
    )

    def packet_with_order(source_order: tuple[str, str]) -> ResolvedContextPacket:
        revisions = tuple(
            (source, revision_by_owner[source]) for source in source_order
        )
        receipt = tuple(
            CollectionDecision(
                source=source,
                decision="ALLOW_READ",
                reason="lane_policy",
                source_revision=revision_by_owner[source],
            )
            for source in source_order
        )
        packet = ContextPacket(
            schema_version=base.packet.schema_version,
            capture_id=base.packet.capture_id,
            snapshot_revision=base.packet.snapshot_revision,
            request=base.packet.request,
            source_revisions=revisions,
            sections=sections,
            collection_receipt=receipt,
        )
        decisions = tuple(
            SelectionDecision(
                section_id=section.id,
                decision="included",
                reason="required",
                source=section.source,
                lifetime=section.lifetime,
                semantic_role=section.semantic_role,
                revision=section.revision,
                formatter_version=section.formatter_version,
                budget_class=section.budget_class,
                chars=None,
                token_estimate=None,
            )
            for section in sections
        )
        return ResolvedContextPacket(
            packet=packet,
            selected_sections=sections,
            decisions=decisions,
            total_estimated_tokens=0,
        )

    first = ContextCompiler().compile(
        packet_with_order(("z_source", "a_source")),
        _config(),
    )
    second = ContextCompiler().compile(
        packet_with_order(("a_source", "z_source")),
        _config(),
    )

    assert first.messages == second.messages
    assert first.manifest == second.manifest
    assert first.manifest.source_revisions == (
        ("a_source", "a-r1"),
        ("z_source", "z-r1"),
    )
    assert tuple(item.source for item in first.manifest.collection) == (
        "a_source",
        "z_source",
    )


def test_claimed_packet_total_is_never_trusted_for_final_totals() -> None:
    first = ContextCompiler().compile(
        _resolved_packet(total_estimated_tokens=0),
        _config(),
    )
    second = ContextCompiler().compile(
        _resolved_packet(total_estimated_tokens=999_999),
        _config(),
    )

    assert first.messages == second.messages
    assert first.manifest.input_chars == second.manifest.input_chars
    assert first.manifest.input_tokens_est == second.manifest.input_tokens_est
    assert first.full_context_hash == second.full_context_hash


def test_empty_durable_and_session_layers_inherit_static_boundary_exactly() -> None:
    compiled = _compile()

    assert compiled.static_prefix_hash == compiled.durable_prefix_hash
    assert compiled.durable_prefix_hash == compiled.session_prefix_hash
    assert compiled.static_prefix_chars == compiled.durable_prefix_chars
    assert compiled.durable_prefix_chars == compiled.session_prefix_chars


def test_manifest_counts_normalized_message_content_not_projection_framing() -> None:
    ids = (
        "core.private.v1",
        "policy.private.v1",
        "contract.output.private.v1",
        "identity.owner.v1",
        "continuity.summary.v1",
        "expression.private.v1",
    )
    payloads = dict(zip(ids, ("A", "B", "C", "D", "E", "F")))
    compiled = _compile(
        candidate_ids=ids,
        payloads=payloads,
        current_input="U",
    )

    assert compiled.messages[0].content == "A\n\nB\n\nC\n\nD\n\nE\n\nF\n"
    assert compiled.static_prefix_chars == 7
    assert compiled.durable_prefix_chars == 10
    assert compiled.session_prefix_chars == 13
    assert compiled.manifest.input_chars == 18
    assert compiled.manifest.input_tokens_est == 7


def test_layer_change_matrix_matches_cumulative_boundaries() -> None:
    ids = tuple(slot[0] for slot in REGISTRIES[(Lane.PRIVATE_OWNER, Route.INTERACTIVE)])
    base_payloads = {section_id: f"value:{section_id}" for section_id in ids}

    def changed(section_id: str | None = None, *, user: str = "hello"):
        payloads = dict(base_payloads)
        if section_id is not None:
            payloads[section_id] = f"changed:{section_id}"
        return _compile(
            candidate_ids=ids,
            payloads=payloads,
            current_input=user,
        )

    base = changed()
    static = changed("core.private.v1")
    durable = changed("identity.owner.v1")
    session = changed("continuity.summary.v1")
    turn = changed("expression.private.v1")
    user = changed(user="different input")

    assert static.static_prefix_hash != base.static_prefix_hash
    assert static.durable_prefix_hash != base.durable_prefix_hash
    assert static.session_prefix_hash != base.session_prefix_hash
    assert static.full_context_hash != base.full_context_hash
    assert durable.static_prefix_hash == base.static_prefix_hash
    assert durable.durable_prefix_hash != base.durable_prefix_hash
    assert durable.session_prefix_hash != base.session_prefix_hash
    assert durable.full_context_hash != base.full_context_hash
    assert session.static_prefix_hash == base.static_prefix_hash
    assert session.durable_prefix_hash == base.durable_prefix_hash
    assert session.session_prefix_hash != base.session_prefix_hash
    assert session.full_context_hash != base.full_context_hash
    for changed_context in (turn, user):
        assert changed_context.static_prefix_hash == base.static_prefix_hash
        assert changed_context.durable_prefix_hash == base.durable_prefix_hash
        assert changed_context.session_prefix_hash == base.session_prefix_hash
        assert changed_context.full_context_hash != base.full_context_hash


def test_budget_candidates_change_only_metadata_and_never_content_or_hashes() -> None:
    packet = _resolved_packet()
    generous = ContextCompiler().compile(
        packet,
        _config(max_tokens=100_000, max_chars=100_000),
    )
    tiny = ContextCompiler().compile(
        packet,
        _config(
            max_tokens=1,
            max_chars=1,
            policy_revision="candidate.tiny",
            status=BudgetPolicyStatus.APPROVED,
        ),
    )

    assert generous.messages == tiny.messages
    assert (
        generous.static_prefix_hash,
        generous.durable_prefix_hash,
        generous.session_prefix_hash,
        generous.full_context_hash,
    ) == (
        tiny.static_prefix_hash,
        tiny.durable_prefix_hash,
        tiny.session_prefix_hash,
        tiny.full_context_hash,
    )
    assert generous.manifest.candidate_overflow is False
    assert tiny.manifest.candidate_overflow is True
    assert generous.manifest.budget_enforced is tiny.manifest.budget_enforced is False
    assert generous.manifest.selected_sections == tiny.manifest.selected_sections
    assert generous.manifest.dropped_sections == tiny.manifest.dropped_sections


@pytest.mark.parametrize("dropped", [False, True])
def test_per_candidate_max_tokens_is_metadata_only_overflow_even_when_dropped(
    dropped: bool,
) -> None:
    ids = tuple(slot[0] for slot in REGISTRIES[(Lane.PRIVATE_OWNER, Route.INTERACTIVE)])
    drop_ids = frozenset({"expression.private.v1"}) if dropped else frozenset()
    common = {
        "candidate_ids": ids,
        "payloads": {"expression.private.v1": "four"},
        "dropped_ids": drop_ids,
    }
    within = ContextCompiler().compile(
        _resolved_packet(**common, max_tokens_overrides={"expression.private.v1": 2}),
        _config(),
    )
    overflow = ContextCompiler().compile(
        _resolved_packet(**common, max_tokens_overrides={"expression.private.v1": 1}),
        _config(),
    )

    assert within.messages == overflow.messages
    assert within.full_context_hash == overflow.full_context_hash
    assert within.manifest.candidate_overflow is False
    assert overflow.manifest.candidate_overflow is True
    assert overflow.manifest.selected_sections == within.manifest.selected_sections
    assert overflow.manifest.dropped_sections == within.manifest.dropped_sections


@pytest.mark.parametrize("payload", ["", " \t\r\n\r\n"])
def test_selected_section_rendering_empty_fails_closed(payload: str) -> None:
    packet = _resolved_packet(payloads={"core.private.v1": payload})

    with pytest.raises(ContextContractError, match="empty_selected_section"):
        ContextCompiler().compile(packet, _config())


@pytest.mark.parametrize("current_input", ["", " \t\r\n\r\n"])
def test_empty_normalized_current_input_fails_closed(current_input: str) -> None:
    packet = _resolved_packet(current_input=current_input)

    with pytest.raises(ContextContractError, match="empty_current_input"):
        ContextCompiler().compile(packet, _config())


def test_dropped_optional_empty_candidate_remains_zero_size_metadata() -> None:
    ids = tuple(slot[0] for slot in REGISTRIES[(Lane.PRIVATE_OWNER, Route.INTERACTIVE)])
    compiled = ContextCompiler().compile(
        _resolved_packet(
            candidate_ids=ids,
            payloads={"expression.private.v1": " \t\r\n"},
            dropped_ids=frozenset({"expression.private.v1"}),
        ),
        _config(),
    )
    row = next(
        item
        for item in compiled.manifest.sections
        if item.section_id == "expression.private.v1"
    )

    assert row.included is False
    assert row.chars == 0
    assert row.token_estimate == 0


def test_compiler_config_is_frozen_slotted_and_revision_must_match_request() -> None:
    config = _config()
    assert not hasattr(config, "__dict__")
    with pytest.raises(FrozenInstanceError):
        config.revision = "changed"  # type: ignore[misc]

    bad = replace(config, revision="other-config.v1")
    with pytest.raises(ContextContractError, match="compiler_config_revision_mismatch"):
        ContextCompiler().compile(_resolved_packet(), bad)


def test_canonical_projection_helper_rejects_non_v1_message_shapes() -> None:
    with pytest.raises(ContextContractError, match="invalid_wire_profile"):
        canonical_message_projection((CompiledMessage("system", "only"),))
