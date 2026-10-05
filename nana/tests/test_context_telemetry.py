"""Behavioral tests for content-free Context Compiler telemetry."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, fields, replace
import hashlib
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))


import nana.runtime.context_compiler as context_compiler  # noqa: E402
import nana.runtime.context_telemetry as context_telemetry  # noqa: E402
from nana.runtime.context_telemetry import (  # noqa: E402
    BoundedPrefixTracker,
    ContextTelemetry,
    PrefixComparison,
    PrefixCompatibilityKey,
    PrefixFingerprint,
    compare_prefixes,
    emit_context_telemetry,
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
    CompiledMessage,
    ContextContractError,
    ContextPacket,
    ContextSection,
    Freshness,
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


SCOPE_A = "a" * 64
SCOPE_B = "b" * 64
SCOPE_C = "c" * 64


REGISTRY_MINIMUMS: dict[
    tuple[Lane, Route],
    tuple[tuple[str, Lifetime, SemanticRole], ...],
] = {
    (Lane.PRIVATE_OWNER, Route.INTERACTIVE): (
        ("core.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION),
        ("policy.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION),
        (
            "contract.output.private.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
        ),
    ),
    (Lane.PUBLIC_STAGE, Route.INTERACTIVE): (
        ("core.public.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION),
        ("policy.public.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION),
        (
            "contract.output.public.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
        ),
    ),
    (Lane.PRIVATE_OWNER, Route.AUTONOMY): (
        ("core.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION),
        ("policy.private.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION),
        ("policy.autonomy.v1", Lifetime.STATIC, SemanticRole.INSTRUCTION),
        (
            "contract.output.autonomy.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
        ),
    ),
}


def _public_scope() -> PublicEventScope:
    return PublicEventScope(
        platform="youtube",
        room_id="telemetry-room",
        stream_session_id="telemetry-stream",
        event_id="telemetry-event",
        display_name="Telemetry Viewer",
        identity=CanonicalPublicIdentity(
            platform="youtube",
            author_id="telemetry-author",
            actor_key="youtube:telemetry-author",
        ),
    )


def _compiled(
    *,
    lane: Lane = Lane.PRIVATE_OWNER,
    route: Route = Route.INTERACTIVE,
    request_id: str = "request-1",
    current_input: str = "hello",
    core_payload: str = "CORE",
    policy_payload: str | None = None,
    expression_payload: str | None = None,
    situation_payload: str | None = None,
    drop_expression: bool = False,
    resolved_provider: str | None = "fixture-provider",
    resolved_model: str | None = "fixture-model",
    config_revision: str = "compiler-config.v1",
    max_tokens: int = 100_000,
    max_chars: int = 100_000,
):
    raw = UnresolvedContextRequest(
        request_id=request_id,
        correlation_id=f"correlation:{request_id}",
        route=route,
        model="logical-model",
        current_input=current_input,
        viewer_name=None,
        stream_mode=lane is Lane.PUBLIC_STAGE,
        public_platform="youtube" if lane is Lane.PUBLIC_STAGE else None,
        caller_metadata={},
        bridge_system=False,
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
            Lane.PRIVATE_OWNER: "private_telemetry.v1",
            Lane.PUBLIC_STAGE: "public_telemetry.v1",
            Lane.OPERATOR_BACKSTAGE: "operator_telemetry.v1",
        }[lane],
        resolver_version="resolver.v1",
    )
    request = _make_resolved_context_request(
        unresolved=raw,
        scope=scope,
        public_context={},
        captured_wall_time=1_700_000_000.0,
        captured_monotonic_time=123.0,
        compiler_config_revision=config_revision,
    )

    specs = list(REGISTRY_MINIMUMS[(lane, route)])
    if expression_payload is not None:
        specs.append(
            (
                "expression.private.v1"
                if lane is Lane.PRIVATE_OWNER
                else "expression.public.v1",
                Lifetime.TURN,
                SemanticRole.STATE,
            )
        )
    if situation_payload is not None:
        specs.append(
            (
                "situation.current.v1",
                Lifetime.TURN,
                SemanticRole.STATE,
            )
        )
    sections = []
    for index, (section_id, lifetime, role) in enumerate(specs):
        payload = (
            core_payload
            if index == 0
            else policy_payload
            if section_id in {"policy.private.v1", "policy.public.v1"}
            and policy_payload is not None
            else expression_payload
            if section_id.startswith("expression.")
            else situation_payload
            if section_id == "situation.current.v1"
            else f"PAYLOAD:{section_id}"
        )
        sections.append(
            ContextSection(
                id=section_id,
                lifetime=lifetime,
                semantic_role=role,
                freshness=Freshness.FRESH,
                visibility=frozenset({lane}),
                source=SourceRef(
                    "fixture_source",
                    "fixture.adapter.v1",
                    "fixture.projection.v1",
                ),
                revision="fixture-source-r1",
                authority="fixture_authority",
                observed_at=100.0,
                expires_at=200.0,
                conflict_key=None,
                dedupe_key=f"dedupe:{section_id}",
                max_tokens=1_000,
                payload=payload,
                formatter_version="formatter.v1",
                required=index < len(REGISTRY_MINIMUMS[(lane, route)]),
                budget_class="fixture",
                semantic_status="active",
                provenance=(
                    ProvenanceRef("fixture_source", "event-1", section_id),
                ),
                relevance=1.0,
            )
        )
    packet = ContextPacket(
        schema_version=1,
        capture_id=f"capture:{request_id}",
        snapshot_revision=1,
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
    for section in sections:
        included = not (
            drop_expression and section.id.startswith("expression.")
        )
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
    resolved = ResolvedContextPacket(
        packet=packet,
        selected_sections=tuple(selected),
        decisions=tuple(decisions),
        total_estimated_tokens=0,
    )
    config = CompilerConfig(
        compiler_version=COMPILER_VERSION,
        revision=config_revision,
        resolved_provider=resolved_provider,
        resolved_model=resolved_model,
        budget_policy=BudgetPolicy(
            policy_id="fixture-budget.v1",
            revision="candidate.v1",
            status=BudgetPolicyStatus.SHADOW_CANDIDATE,
            max_estimated_tokens=max_tokens,
            max_characters=max_chars,
        ),
    )
    return ContextCompiler().compile(resolved, config)


def _fingerprint(compiled, scope: str = SCOPE_A) -> PrefixFingerprint:
    return PrefixFingerprint.from_compiled(
        compiled,
        comparison_scope_digest=scope,
    )


def _request_digest(request_id: str) -> str:
    return hashlib.sha256(request_id.encode("utf-8")).hexdigest()


def test_fingerprint_contains_digests_offsets_lengths_and_hashes_only() -> None:
    secret = "private-user-secret-7421"
    compiled = _compiled(current_input=secret, core_payload="private-core-secret")
    fingerprint = _fingerprint(compiled)
    field_names = {field.name for field in fields(PrefixFingerprint)}

    assert field_names.isdisjoint({"content", "messages", "payload", "compiled"})
    assert dict(fingerprint.slot_digests)["core.private.v1"] == hashlib.sha256(
        b"private-core-secret"
    ).hexdigest()
    assert len(fingerprint.slot_digests) == 4
    assert fingerprint.slot_digests[-1][0] == "current_input"
    assert len(fingerprint.slot_prefix_char_offsets) == len(
        fingerprint.slot_digests
    )
    assert len(fingerprint.slot_prefix_byte_offsets) == len(
        fingerprint.slot_digests
    )
    assert all(
        framed > content
        for framed, content in zip(
            fingerprint.framed_layer_char_lengths,
            (
                compiled.static_prefix_chars,
                compiled.durable_prefix_chars,
                compiled.session_prefix_chars,
            ),
        )
    )
    assert secret not in repr(fingerprint)
    assert "private-core-secret" not in repr(fingerprint)


def test_prefix_fingerprint_and_compatibility_key_are_frozen_and_slotted() -> None:
    fingerprint = _fingerprint(_compiled())
    assert not hasattr(fingerprint, "__dict__")
    assert not hasattr(fingerprint.compatibility_key, "__dict__")
    assert not hasattr(fingerprint, "request_id")
    assert fingerprint.request_id_digest == _request_digest("request-1")
    with pytest.raises(FrozenInstanceError):
        fingerprint.request_id_digest = "0" * 64  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        fingerprint.compatibility_key.resolved_model = "changed"  # type: ignore[misc]


def test_raw_unbounded_request_id_is_digested_before_tracker_retention() -> None:
    secret_request_id = "secret-room-request-" + "x" * 10_000
    fingerprint = _fingerprint(_compiled(request_id=secret_request_id))
    tracker = BoundedPrefixTracker(capacity=1)
    first = tracker.compare_and_replace(fingerprint)
    next_fingerprint = replace(
        fingerprint,
        request_id_digest=_request_digest("next-request"),
    )
    comparison = tracker.compare_and_replace(next_fingerprint)

    assert fingerprint.request_id_digest == _request_digest(secret_request_id)
    assert first.prior_compatible_request_digest is None
    assert comparison.prior_compatible_request_digest == _request_digest(
        secret_request_id
    )
    assert secret_request_id not in repr(fingerprint)
    assert secret_request_id not in repr(comparison)
    assert secret_request_id not in repr(tracker)
    assert secret_request_id not in repr(tracker.snapshot())


def test_no_prior_and_equal_comparisons_have_no_break_section() -> None:
    first = _fingerprint(_compiled(request_id="request-1"))
    second = _fingerprint(_compiled(request_id="request-2"))

    no_prior = compare_prefixes(None, first)
    equal = compare_prefixes(first, second)

    assert no_prior.status == "no_prior"
    assert no_prior.prior_compatible_request_digest is None
    assert no_prior.prefix_break_section_id is None
    assert equal.status == "compatible_equal"
    assert equal.prior_compatible_request_digest == _request_digest("request-1")
    assert equal.prefix_break_section_id is None
    assert equal.prior_exact_matching_prefix_chars == second.full_context_chars
    assert equal.prior_exact_matching_prefix_bytes == second.full_context_bytes


def test_changed_first_slot_reports_exact_framing_prefix_not_internal_lcp() -> None:
    previous = _fingerprint(_compiled(core_payload="CORE-A"))
    current = _fingerprint(_compiled(request_id="request-2", core_payload="CORE-B"))

    comparison = compare_prefixes(previous, current)

    assert comparison.status == "compatible_changed"
    assert comparison.prefix_break_section_id == "core.private.v1"
    assert comparison.prior_exact_matching_prefix_chars == len('[["system","')
    assert comparison.prior_exact_matching_prefix_bytes == len(b'[["system","')


def test_changed_later_slot_uses_complete_slot_boundary() -> None:
    previous = _fingerprint(_compiled())
    current = _fingerprint(
        _compiled(
            request_id="request-2",
            policy_payload="CHANGED POLICY",
        )
    )
    comparison = compare_prefixes(previous, current)
    policy_index = [slot[0] for slot in current.slot_digests].index(
        "policy.private.v1"
    )

    assert comparison.status == "compatible_changed"
    assert comparison.prefix_break_section_id == "policy.private.v1"
    assert comparison.prior_exact_matching_prefix_chars == (
        current.slot_prefix_char_offsets[policy_index]
    )


def test_fingerprint_rejects_forged_message_hash_pairs() -> None:
    compiled = _compiled()
    messages = list(compiled.messages)
    messages[0] = replace(messages[0], content="forged\n")
    forged = replace(compiled, messages=tuple(messages))

    with pytest.raises(ContextContractError, match="invalid_compiler_attestation"):
        _fingerprint(forged)


def test_user_only_change_reports_virtual_user_slot_and_full_slot_boundary() -> None:
    previous = _fingerprint(_compiled(current_input="first"))
    current = _fingerprint(_compiled(request_id="request-2", current_input="second"))
    comparison = compare_prefixes(previous, current)
    user_index = [slot[0] for slot in current.slot_digests].index("current_input")

    assert comparison.status == "compatible_changed"
    assert comparison.prefix_break_section_id == "current_input"
    assert comparison.prior_exact_matching_prefix_chars == (
        current.slot_prefix_char_offsets[user_index]
    )
    assert (
        comparison.prior_exact_matching_prefix_chars
        > current.framed_layer_char_lengths[2]
    )


def test_autonomy_user_change_reports_autonomy_trigger_slot() -> None:
    previous = _fingerprint(
        _compiled(route=Route.AUTONOMY, current_input="first")
    )
    current = _fingerprint(
        _compiled(
            route=Route.AUTONOMY,
            request_id="request-2",
            current_input="second",
        )
    )

    comparison = compare_prefixes(previous, current)
    assert comparison.status == "compatible_changed"
    assert comparison.prefix_break_section_id == "autonomy.trigger.v1"


def test_optional_slot_presence_change_breaks_at_that_registry_slot() -> None:
    previous = _fingerprint(_compiled(expression_payload="calm"))
    current = _fingerprint(_compiled(request_id="request-2"))

    comparison = compare_prefixes(previous, current)
    assert comparison.status == "compatible_changed"
    assert comparison.prefix_break_section_id == "expression.private.v1"
    assert comparison.prior_exact_matching_prefix_chars == (
        current.framed_layer_char_lengths[0]
    )
    assert comparison.prior_exact_matching_prefix_bytes == (
        current.framed_layer_byte_lengths[0]
    )


@pytest.mark.parametrize("remove_from_prior", [True, False])
def test_middle_optional_slot_change_includes_shared_literal_separator(
    remove_from_prior: bool,
) -> None:
    with_optional = _compiled(
        request_id="request-with",
        expression_payload="calm",
        situation_payload="steady",
    )
    without_optional = _compiled(
        request_id="request-without",
        situation_payload="steady",
    )
    with_literal = (
        b'[["system","CORE\\n\\nPAYLOAD:policy.private.v1'
        b'\\n\\nPAYLOAD:contract.output.private.v1\\n\\ncalm'
        b'\\n\\nsteady\\n"],["user","hello"]]'
    )
    without_literal = (
        b'[["system","CORE\\n\\nPAYLOAD:policy.private.v1'
        b'\\n\\nPAYLOAD:contract.output.private.v1\\n\\nsteady'
        b'\\n"],["user","hello"]]'
    )
    assert hashlib.sha256(with_literal).hexdigest() == with_optional.full_context_hash
    assert (
        hashlib.sha256(without_literal).hexdigest()
        == without_optional.full_context_hash
    )
    assert with_literal[:87] == without_literal[:87]
    assert with_literal[87] != without_literal[87]

    prior_compiled, current_compiled = (
        (with_optional, without_optional)
        if remove_from_prior
        else (without_optional, with_optional)
    )
    comparison = compare_prefixes(
        _fingerprint(prior_compiled),
        _fingerprint(current_compiled),
    )

    assert comparison.status == "compatible_changed"
    assert comparison.prefix_break_section_id == "expression.private.v1"
    assert comparison.prior_exact_matching_prefix_chars == 87
    assert comparison.prior_exact_matching_prefix_bytes == 87


def test_compiled_context_cannot_borrow_a_prior_fingerprint_scope() -> None:
    previous = _fingerprint(_compiled(current_input="first"))
    current = _compiled(request_id="request-2", current_input="second")

    with pytest.raises(ContextContractError, match="invalid_prefix_fingerprint"):
        compare_prefixes(previous, current)  # type: ignore[arg-type]

    independently_scoped = _fingerprint(current, SCOPE_B)
    assert compare_prefixes(previous, independently_scoped).status == "incompatible"


@pytest.mark.parametrize(
    "key_change",
    [
        {"resolved_provider": "other-provider"},
        {"resolved_model": "other-model"},
        {"wire_profile": "other-wire.v1"},
        {"compiler_version": "other-compiler.v1"},
        {"compiler_config_revision": "other-config.v1"},
    ],
)
def test_compatibility_key_changes_are_incompatible(
    key_change: dict[str, str],
) -> None:
    previous = _fingerprint(_compiled())
    changed_key = replace(previous.compatibility_key, **key_change)
    current = replace(
        previous,
        request_id_digest=_request_digest("request-2"),
        compatibility_key=changed_key,
    )

    comparison = compare_prefixes(previous, current)
    assert comparison.status == "incompatible"
    assert comparison.prior_compatible_request_digest is None
    assert comparison.prefix_break_section_id is None


def test_lane_and_route_changes_are_incompatible() -> None:
    private = _fingerprint(_compiled())
    public = _fingerprint(
        _compiled(lane=Lane.PUBLIC_STAGE, request_id="public-request")
    )
    autonomy = _fingerprint(
        _compiled(route=Route.AUTONOMY, request_id="autonomy-request")
    )

    assert compare_prefixes(private, public).status == "incompatible"
    assert compare_prefixes(private, autonomy).status == "incompatible"


@pytest.mark.parametrize(
    ("provider", "model"),
    [(None, "fixture-model"), ("fixture-provider", None), (None, None)],
)
def test_unknown_resolved_route_values_are_comparison_ineligible(
    provider: str | None,
    model: str | None,
) -> None:
    first = _fingerprint(
        _compiled(resolved_provider=provider, resolved_model=model)
    )
    second = replace(first, request_id_digest=_request_digest("request-2"))

    assert compare_prefixes(None, first).status == "no_prior"
    assert compare_prefixes(first, second).status == "incompatible"


def test_comparison_scope_digest_is_a_hard_isolation_boundary() -> None:
    first = _fingerprint(_compiled(), SCOPE_A)
    second = _fingerprint(_compiled(request_id="request-2"), SCOPE_B)

    assert compare_prefixes(first, second).status == "incompatible"


def test_prefix_comparison_break_field_is_valid_only_for_changed_status() -> None:
    with pytest.raises(ContextContractError, match="invalid_prefix_break_section_id"):
        PrefixComparison(
            status="compatible_equal",
            comparison_scope_digest=SCOPE_A,
            current_fingerprint_digest="c" * 64,
            prior_compatible_request_digest=_request_digest("request-1"),
            prior_full_context_hash="a" * 64,
            current_full_context_hash="b" * 64,
            prefix_break_section_id="current_input",
            prior_exact_matching_prefix_chars=1,
            prior_exact_matching_prefix_bytes=1,
            prior_exact_matching_prefix_tokens_est=1,
        )
    with pytest.raises(ContextContractError, match="invalid_prefix_break_section_id"):
        PrefixComparison(
            status="compatible_changed",
            comparison_scope_digest=SCOPE_A,
            current_fingerprint_digest="c" * 64,
            prior_compatible_request_digest=_request_digest("request-1"),
            prior_full_context_hash="a" * 64,
            current_full_context_hash="b" * 64,
            prefix_break_section_id=None,
            prior_exact_matching_prefix_chars=1,
            prior_exact_matching_prefix_bytes=1,
            prior_exact_matching_prefix_tokens_est=1,
        )


def test_tracker_compares_and_replaces_atomically_per_scope() -> None:
    tracker = BoundedPrefixTracker(capacity=4)
    first = _fingerprint(_compiled(request_id="request-1"))
    second = _fingerprint(_compiled(request_id="request-2"))

    assert tracker.compare_and_replace(first).status == "no_prior"
    comparison = tracker.compare_and_replace(second)
    assert comparison.status == "compatible_equal"
    assert comparison.prior_compatible_request_digest == _request_digest("request-1")


def test_tracker_never_compares_different_scopes() -> None:
    tracker = BoundedPrefixTracker(capacity=4)
    first = _fingerprint(_compiled(request_id="request-a"), SCOPE_A)
    second = _fingerprint(_compiled(request_id="request-b"), SCOPE_B)

    assert tracker.compare_and_replace(first).status == "no_prior"
    assert tracker.compare_and_replace(second).status == "no_prior"


def test_tracker_capacity_evicts_oldest_and_replace_refreshes_age() -> None:
    tracker = BoundedPrefixTracker(capacity=2)
    a1 = _fingerprint(_compiled(request_id="a1"), SCOPE_A)
    a2 = _fingerprint(_compiled(request_id="a2"), SCOPE_A)
    b = _fingerprint(_compiled(request_id="b"), SCOPE_B)
    c = _fingerprint(_compiled(request_id="c"), SCOPE_C)

    tracker.compare_and_replace(a1)
    tracker.compare_and_replace(b)
    tracker.compare_and_replace(a2)
    tracker.compare_and_replace(c)

    assert tracker.compare_and_replace(b).status == "no_prior"


def test_tracker_same_scope_concurrency_has_exactly_one_no_prior() -> None:
    tracker = BoundedPrefixTracker(capacity=2)
    base = _fingerprint(_compiled())
    fingerprints = [
        replace(base, request_id_digest=_request_digest(f"request-{index}"))
        for index in range(32)
    ]

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(tracker.compare_and_replace, fingerprints))

    assert sum(item.status == "no_prior" for item in statuses) == 1
    assert sum(item.status == "compatible_equal" for item in statuses) == 31


def test_tracker_rejects_non_sha_scope_without_echoing_it() -> None:
    fingerprint = _fingerprint(_compiled())
    bad = object.__new__(PrefixFingerprint)
    for field in fields(PrefixFingerprint):
        object.__setattr__(bad, field.name, getattr(fingerprint, field.name))
    object.__setattr__(bad, "comparison_scope_digest", "private-room-secret")

    tracker = BoundedPrefixTracker(capacity=1)
    with pytest.raises(ContextContractError, match="invalid_comparison_scope_digest") as caught:
        tracker.compare_and_replace(bad)
    assert "private-room-secret" not in str(caught.value)


def test_tracker_repr_and_snapshot_retain_no_raw_content() -> None:
    secret = "raw-private-input-9172"
    tracker = BoundedPrefixTracker(capacity=2)
    tracker.compare_and_replace(_fingerprint(_compiled(current_input=secret)))

    assert secret not in repr(tracker)
    assert secret not in repr(tracker.snapshot())
    assert all(isinstance(item, PrefixFingerprint) for item in tracker.snapshot())


class _RecordingSink:
    def __init__(self) -> None:
        self.receipts: list[ContextTelemetry] = []

    def emit(self, receipt: ContextTelemetry) -> None:
        self.receipts.append(receipt)


class _FailingSink:
    def emit(self, receipt: ContextTelemetry) -> None:
        raise RuntimeError("sink saw data but must not affect compilation")


class _DescriptorFailingSink:
    @property
    def emit(self):
        raise RuntimeError("descriptor-secret-must-not-escape")


def test_context_telemetry_is_frozen_manifest_only_snapshot() -> None:
    secret = "telemetry-user-secret-2001"
    compiled = _compiled(current_input=secret)
    fingerprint = _fingerprint(compiled)
    comparison = compare_prefixes(None, fingerprint)
    receipt = emit_context_telemetry(
        compiled,
        label="candidate_context",
        production_bound=False,
        fingerprint=fingerprint,
        comparison=comparison,
    )

    assert receipt.label == "candidate_context"
    assert receipt.production_bound is False
    assert receipt.manifest == compiled.manifest
    assert receipt.comparison == comparison
    assert {field.name for field in fields(ContextTelemetry)} == {
        "label",
        "production_bound",
        "manifest",
        "comparison",
    }
    assert not hasattr(receipt, "compiled")
    assert not hasattr(receipt, "messages")
    assert not hasattr(receipt, "payload")
    assert secret not in repr(receipt)
    assert not hasattr(receipt, "__dict__")
    with pytest.raises(FrozenInstanceError):
        receipt.label = "sent_context"  # type: ignore[misc]


def test_enabled_sink_receives_only_the_frozen_receipt() -> None:
    compiled = _compiled()
    sink = _RecordingSink()
    receipt = emit_context_telemetry(
        compiled,
        label="candidate_context",
        production_bound=False,
        sink=sink,
        enabled=True,
    )

    assert sink.receipts == [receipt]
    assert sink.receipts[0] is receipt


def test_disabled_sink_is_not_called_and_returns_same_metadata_shape() -> None:
    compiled = _compiled()
    sink = _RecordingSink()
    receipt = emit_context_telemetry(
        compiled,
        label="candidate_context",
        production_bound=False,
        sink=sink,
        enabled=False,
    )

    assert sink.receipts == []
    assert receipt.manifest == compiled.manifest


def test_failing_sink_cannot_change_or_block_compiled_bytes_and_hashes() -> None:
    compiled = _compiled(current_input="immutable raw input")
    messages_before = compiled.messages
    hashes_before = (
        compiled.static_prefix_hash,
        compiled.durable_prefix_hash,
        compiled.session_prefix_hash,
        compiled.full_context_hash,
    )

    receipt = emit_context_telemetry(
        compiled,
        label="candidate_context",
        production_bound=False,
        sink=_FailingSink(),
        enabled=True,
    )

    assert receipt.manifest.full_context_hash == compiled.full_context_hash
    assert compiled.messages == messages_before
    assert (
        compiled.static_prefix_hash,
        compiled.durable_prefix_hash,
        compiled.session_prefix_hash,
        compiled.full_context_hash,
    ) == hashes_before


def test_telemetry_snapshots_manifest_instead_of_retaining_compiled_object() -> None:
    compiled = _compiled()
    receipt = emit_context_telemetry(
        compiled,
        label="candidate_context",
        production_bound=False,
    )
    object.__setattr__(compiled.manifest, "model", "mutated-after-emission")

    assert receipt.manifest.model == "logical-model"


def test_telemetry_revalidates_mutated_compiled_records() -> None:
    compiled = _compiled()
    object.__setattr__(compiled.manifest, "budget_enforced", True)

    with pytest.raises(ContextContractError):
        emit_context_telemetry(
            compiled,
            label="candidate_context",
            production_bound=False,
        )


def test_invalid_telemetry_label_and_flags_fail_without_raw_echo() -> None:
    compiled = _compiled()
    with pytest.raises(ContextContractError, match="invalid_telemetry_label") as caught:
        emit_context_telemetry(
            compiled,
            label="secret-user-content",
            production_bound=False,
        )
    assert "secret-user-content" not in str(caught.value)

    with pytest.raises(ContextContractError, match="invalid_production_bound"):
        emit_context_telemetry(
            compiled,
            label="candidate_context",
            production_bound=1,  # type: ignore[arg-type]
        )


def test_label_and_production_bound_truth_is_enforced() -> None:
    compiled = _compiled()
    with pytest.raises(ContextContractError, match="invalid_telemetry_binding"):
        emit_context_telemetry(
            compiled,
            label="candidate_context",
            production_bound=True,
        )
    with pytest.raises(ContextContractError, match="invalid_telemetry_binding"):
        emit_context_telemetry(
            compiled,
            label="sent_context",
            production_bound=True,
        )


def test_candidate_receipt_cannot_be_forged_by_direct_construction() -> None:
    compiled = _compiled()
    with pytest.raises(ContextContractError, match="invalid_telemetry_receipt"):
        ContextTelemetry(
            label="candidate_context",
            production_bound=False,
            manifest=compiled.manifest,
            comparison=None,
        )


def test_sent_context_factory_hashes_exact_legacy_messages_without_retaining_them() -> None:
    system = "legacy system\n"
    user = "legacy user"
    messages = (
        CompiledMessage("system", system),
        CompiledMessage("user", user),
    )
    receipt = context_telemetry.build_sent_context_telemetry(
        messages,
        request_id="request.sent-1",
        correlation_id="correlation.sent-1",
        capture_id="capture.sent-1",
        snapshot_revision=4,
        model="logical-model",
        resolved_model="provider-model",
        resolved_provider="fixture-provider",
        lane=Lane.PRIVATE_OWNER,
        route=Route.INTERACTIVE,
    )

    assert receipt.label == "sent_context"
    assert receipt.production_bound is True
    assert isinstance(receipt.manifest, context_telemetry.SentContextManifest)
    assert receipt.manifest.wire_profile == "legacy_system_user.v1"
    assert receipt.manifest.full_context_hash == (
        "213447c8a6da44b4d4e100e6bdcd68018708e5a45c36047c3120368e961ee364"
    )
    assert receipt.manifest.input_chars == 25
    assert receipt.manifest.input_tokens_est == 9
    assert not hasattr(receipt, "messages")
    assert system not in repr(receipt)
    assert user not in repr(receipt)


def test_sent_context_emitter_uses_metadata_only_receipt_and_best_effort_sink() -> None:
    messages = (
        CompiledMessage("system", "legacy system\n"),
        CompiledMessage("user", "legacy user"),
    )
    receipt = context_telemetry.emit_sent_context_telemetry(
        messages,
        request_id="request.sent-1",
        correlation_id="correlation.sent-1",
        capture_id="capture.sent-1",
        snapshot_revision=4,
        model="logical-model",
        resolved_model="provider-model",
        resolved_provider="fixture-provider",
        lane=Lane.PRIVATE_OWNER,
        route=Route.INTERACTIVE,
        sink=_DescriptorFailingSink(),
    )

    assert receipt.label == "sent_context"
    assert receipt.manifest.full_context_hash == (
        "213447c8a6da44b4d4e100e6bdcd68018708e5a45c36047c3120368e961ee364"
    )


def test_sent_context_safe_metadata_rejects_unbounded_secret_ids_without_echo() -> None:
    secret_id = "secret raw request " + "x" * 1_000
    with pytest.raises(ContextContractError, match="invalid_safe_metadata") as caught:
        context_telemetry.build_sent_context_telemetry(
            (
                CompiledMessage("system", "legacy\n"),
                CompiledMessage("user", "hello"),
            ),
            request_id=secret_id,
            correlation_id="correlation.sent-1",
            capture_id="capture.sent-1",
            snapshot_revision=0,
            model="logical-model",
            resolved_model=None,
            resolved_provider=None,
            lane=Lane.PRIVATE_OWNER,
            route=Route.INTERACTIVE,
        )
    assert secret_id not in str(caught.value)


def test_sink_descriptor_failure_is_best_effort_for_candidate_receipts() -> None:
    compiled = _compiled()
    receipt = emit_context_telemetry(
        compiled,
        label="candidate_context",
        production_bound=False,
        sink=_DescriptorFailingSink(),
    )
    assert receipt.manifest.full_context_hash == compiled.full_context_hash


def test_candidate_emitter_rejects_derived_input_total_forgery() -> None:
    compiled = _compiled()
    object.__setattr__(
        compiled.manifest,
        "input_chars",
        compiled.manifest.input_chars + 1,
    )

    with pytest.raises(ContextContractError):
        emit_context_telemetry(
            compiled,
            label="candidate_context",
            production_bound=False,
        )


def test_shared_verifier_rejects_inconsistent_truth_even_if_reattested() -> None:
    compiled = _compiled()
    object.__setattr__(
        compiled.manifest,
        "input_chars",
        compiled.manifest.input_chars + 1,
    )
    context_compiler._attest_compiled_context(compiled)

    with pytest.raises(ContextContractError, match="compiled_truth_mismatch"):
        emit_context_telemetry(
            compiled,
            label="candidate_context",
            production_bound=False,
        )


@pytest.mark.parametrize("boundary", ["fingerprint", "telemetry"])
def test_shared_verifier_rejects_included_section_token_forgery(
    boundary: str,
) -> None:
    compiled = _compiled()
    row = compiled.manifest.sections[0]
    object.__setattr__(row, "token_estimate", row.token_estimate + 1)

    with pytest.raises(ContextContractError):
        if boundary == "fingerprint":
            _fingerprint(compiled)
        else:
            emit_context_telemetry(
                compiled,
                label="candidate_context",
                production_bound=False,
            )


def test_candidate_emitter_rejects_layer_hash_forgery_even_when_fields_agree() -> None:
    compiled = _compiled()
    forged_hash = "f" * 64
    object.__setattr__(compiled, "static_prefix_hash", forged_hash)
    object.__setattr__(compiled.manifest, "static_prefix_hash", forged_hash)

    with pytest.raises(ContextContractError):
        emit_context_telemetry(
            compiled,
            label="candidate_context",
            production_bound=False,
        )


def test_dropped_row_sizes_are_protected_by_content_free_compiler_attestation() -> None:
    secret = "dropped-private-secret"
    compiled = _compiled(
        expression_payload=secret,
        drop_expression=True,
    )
    dropped = next(row for row in compiled.manifest.sections if not row.included)
    object.__setattr__(dropped, "chars", dropped.chars + 500)

    with pytest.raises(ContextContractError, match="invalid_compiler_attestation"):
        emit_context_telemetry(
            compiled,
            label="candidate_context",
            production_bound=False,
        )
    assert secret not in repr(context_compiler._COMPILED_ATTESTATIONS)
    assert all(
        type(object_id) is int and type(digest) is bytes and len(digest) == 32
        for object_id, digest in context_compiler._COMPILED_ATTESTATIONS.items()
    )


@pytest.mark.parametrize(
    "status",
    ["no_prior", "compatible_equal", "compatible_changed", "incompatible"],
)
def test_public_candidate_receipt_flow_snapshots_verified_comparison(
    status: str,
) -> None:
    current_input = "current" if status == "compatible_changed" else "same"
    current_compiled = _compiled(
        request_id=f"request-current-{status}",
        current_input=current_input,
    )
    current = _fingerprint(current_compiled, SCOPE_A)
    if status == "no_prior":
        previous = None
    elif status == "compatible_equal":
        previous = _fingerprint(
            _compiled(request_id="request-prior-equal", current_input="same"),
            SCOPE_A,
        )
    elif status == "compatible_changed":
        previous = _fingerprint(
            _compiled(request_id="request-prior-changed", current_input="prior"),
            SCOPE_A,
        )
    else:
        previous = _fingerprint(
            _compiled(request_id="request-prior-incompatible", current_input="same"),
            SCOPE_B,
        )
    comparison = compare_prefixes(previous, current)
    assert comparison.status == status
    assert len(comparison.current_fingerprint_digest) == 64
    assert comparison.current_fingerprint_digest == (
        comparison.current_fingerprint_digest.lower()
    )
    original_manifest = current_compiled.manifest

    receipt = emit_context_telemetry(
        current_compiled,
        label="candidate_context",
        production_bound=False,
        fingerprint=current,
        comparison=comparison,
    )

    assert current_compiled.manifest is original_manifest
    assert current_compiled.manifest.prefix_comparison_status == "no_prior"
    assert current_compiled.manifest.prior_compatible_request_id is None
    assert receipt.manifest is not current_compiled.manifest
    assert receipt.manifest.prefix_comparison_status == comparison.status
    assert (
        receipt.manifest.prior_compatible_request_id
        == comparison.prior_compatible_request_digest
    )
    assert receipt.manifest.prefix_break_section_id == comparison.prefix_break_section_id
    assert receipt.manifest.prior_exact_matching_prefix_chars == (
        comparison.prior_exact_matching_prefix_chars
    )
    assert receipt.manifest.prior_exact_matching_prefix_tokens_est == (
        comparison.prior_exact_matching_prefix_tokens_est
    )
    assert receipt.comparison == comparison
    assert not hasattr(receipt, "fingerprint")
    assert not hasattr(receipt, "compiled")
    assert not hasattr(receipt, "messages")
    assert not hasattr(receipt, "payloads")


def test_candidate_receipt_without_comparison_remains_no_prior() -> None:
    compiled = _compiled()
    receipt = emit_context_telemetry(
        compiled,
        label="candidate_context",
        production_bound=False,
    )

    assert receipt.manifest is not compiled.manifest
    assert receipt.manifest.prefix_comparison_status == "no_prior"
    assert receipt.comparison is None


def test_candidate_comparison_requires_independently_scoped_fingerprint() -> None:
    compiled = _compiled()
    comparison = compare_prefixes(None, _fingerprint(compiled))

    with pytest.raises(ContextContractError, match="missing_prefix_fingerprint"):
        emit_context_telemetry(
            compiled,
            label="candidate_context",
            production_bound=False,
            comparison=comparison,
        )


@pytest.mark.parametrize(
    "mismatch",
    ["request", "compatibility", "hash", "layer_length", "full_length"],
)
def test_candidate_receipt_rejects_fingerprint_unrelated_to_compiled_truth(
    mismatch: str,
) -> None:
    compiled = _compiled()
    fingerprint = _fingerprint(compiled, SCOPE_A)
    comparison = compare_prefixes(None, fingerprint)
    if mismatch == "request":
        fingerprint = replace(fingerprint, request_id_digest="f" * 64)
    elif mismatch == "compatibility":
        fingerprint = replace(
            fingerprint,
            compatibility_key=replace(
                fingerprint.compatibility_key,
                resolved_model="other-model",
            ),
        )
    elif mismatch == "hash":
        fingerprint = replace(fingerprint, full_context_hash="f" * 64)
    elif mismatch == "layer_length":
        fingerprint = replace(
            fingerprint,
            framed_layer_char_lengths=tuple(
                value + 1 for value in fingerprint.framed_layer_char_lengths
            ),
        )
    else:
        fingerprint = replace(
            fingerprint,
            full_context_chars=fingerprint.full_context_chars + 1,
        )

    with pytest.raises(ContextContractError, match="telemetry_fingerprint_mismatch"):
        emit_context_telemetry(
            compiled,
            label="candidate_context",
            production_bound=False,
            fingerprint=fingerprint,
            comparison=comparison,
        )


def test_candidate_receipt_rejects_comparison_scope_mismatch() -> None:
    compiled = _compiled()
    fingerprint_a = _fingerprint(compiled, SCOPE_A)
    fingerprint_b = _fingerprint(compiled, SCOPE_B)
    comparison_a = compare_prefixes(None, fingerprint_a)

    with pytest.raises(ContextContractError, match="telemetry_comparison_mismatch"):
        emit_context_telemetry(
            compiled,
            label="candidate_context",
            production_bound=False,
            fingerprint=fingerprint_b,
            comparison=comparison_a,
        )


@pytest.mark.parametrize("key_difference", ["config", "provider", "model"])
def test_real_equal_comparison_cannot_attach_to_same_bytes_with_different_key(
    key_difference: str,
) -> None:
    foreign: dict[str, object] = {}
    target: dict[str, object] = {}
    if key_difference == "config":
        foreign["config_revision"] = "compiler-config.v2"
        target["config_revision"] = "compiler-config.v1"
    elif key_difference == "provider":
        foreign["resolved_provider"] = "provider-b"
        target["resolved_provider"] = "provider-a"
    else:
        foreign["resolved_model"] = "model-b"
        target["resolved_model"] = "model-a"

    foreign_previous = _compiled(request_id="foreign-prior", **foreign)
    foreign_current = _compiled(request_id="foreign-current", **foreign)
    previous_fingerprint = _fingerprint(foreign_previous, SCOPE_A)
    foreign_fingerprint = _fingerprint(foreign_current, SCOPE_A)
    comparison = compare_prefixes(previous_fingerprint, foreign_fingerprint)
    assert comparison.status == "compatible_equal"

    target_compiled = _compiled(request_id="target-current", **target)
    target_fingerprint = _fingerprint(target_compiled, SCOPE_A)
    assert target_compiled.messages == foreign_current.messages
    assert target_compiled.full_context_hash == foreign_current.full_context_hash
    assert target_fingerprint.compatibility_key != foreign_fingerprint.compatibility_key

    with pytest.raises(ContextContractError, match="telemetry_comparison_mismatch"):
        emit_context_telemetry(
            target_compiled,
            label="candidate_context",
            production_bound=False,
            fingerprint=target_fingerprint,
            comparison=comparison,
        )


def test_candidate_receipt_rejects_forged_comparison_copy() -> None:
    compiled = _compiled()
    fingerprint = _fingerprint(compiled)
    comparison = compare_prefixes(None, fingerprint)
    forged = replace(comparison, current_full_context_hash="f" * 64)

    with pytest.raises(
        ContextContractError,
        match="invalid_prefix_comparison_attestation",
    ):
        emit_context_telemetry(
            compiled,
            label="candidate_context",
            production_bound=False,
            fingerprint=fingerprint,
            comparison=forged,
        )


def test_budget_metadata_only_change_preserves_telemetry_hash_truth() -> None:
    generous = _compiled(max_tokens=100_000, max_chars=100_000)
    tiny = _compiled(max_tokens=1, max_chars=1)
    first = emit_context_telemetry(
        generous,
        label="candidate_context",
        production_bound=False,
    )
    second = emit_context_telemetry(
        tiny,
        label="candidate_context",
        production_bound=False,
    )

    assert generous.messages == tiny.messages
    assert first.manifest.full_context_hash == second.manifest.full_context_hash
    assert first.manifest.candidate_overflow is False
    assert second.manifest.candidate_overflow is True


def test_prefix_compatibility_key_type_rejects_raw_strings_for_enums() -> None:
    with pytest.raises(ContextContractError, match="invalid_compatibility_key"):
        PrefixCompatibilityKey(
            resolved_provider="provider",
            resolved_model="model",
            lane="private_owner",  # type: ignore[arg-type]
            route=Route.INTERACTIVE,
            wire_profile="legacy_system_user.v1",
            compiler_version=COMPILER_VERSION,
            compiler_config_revision="compiler-config.v1",
        )
