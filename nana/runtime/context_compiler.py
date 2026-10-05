"""Pure deterministic compilation for Nana Context Runtime packets.

The compiler is intentionally unwired.  It performs no collection, provider,
transport, persistence, or budget-enforcement work.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import hashlib
import hmac
import json
import math
from threading import Lock
from typing import Any
import unicodedata

from nana.runtime.context_contracts import (
    BudgetPolicy,
    CompilationManifest,
    CompiledContext,
    CompiledMessage,
    ContextContractError,
    FrozenMapping,
    FrozenPayload,
    Lane,
    Lifetime,
    ResolvedContextPacket,
    Route,
    SelectionDecision,
    SemanticRole,
)
from nana.runtime.context_budget import (
    BudgetPolicyV1,
    copy_budget_policy_v1,
    require_budget_enforcement_attestation,
)


COMPILER_VERSION = "nana-context-compiler.v1"
WIRE_PROFILE = "legacy_system_user.v1"

_COMPILED_ATTESTATION_CAPACITY = 4_096
_COMPILED_ATTESTATIONS: OrderedDict[int, bytes] = OrderedDict()
_COMPILED_ATTESTATION_LOCK = Lock()


def _update_attestation_digest(digest: Any, value: object) -> None:
    def add(tag: bytes, payload: bytes = b"") -> None:
        digest.update(tag)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)

    if value is None:
        add(b"N")
    elif isinstance(value, Enum):
        add(b"E", f"{type(value).__module__}.{type(value).__qualname__}".encode())
        _update_attestation_digest(digest, value.value)
    elif type(value) is bool:
        add(b"B", b"1" if value else b"0")
    elif type(value) is int:
        add(b"I", str(value).encode("ascii"))
    elif type(value) is float:
        if not math.isfinite(value):
            raise ContextContractError("invalid_compiled_attestation")
        add(b"F", value.hex().encode("ascii"))
    elif type(value) is str:
        add(b"S", value.encode("utf-8"))
    elif type(value) is tuple:
        add(b"T", len(value).to_bytes(8, "big"))
        for item in value:
            _update_attestation_digest(digest, item)
    elif is_dataclass(value) and not isinstance(value, type):
        add(b"D", f"{type(value).__module__}.{type(value).__qualname__}".encode())
        for item in fields(value):
            add(b"K", item.name.encode("ascii"))
            _update_attestation_digest(digest, getattr(value, item.name))
    else:
        raise ContextContractError("invalid_compiled_attestation")


def _compiled_attestation_digest(value: CompiledContext) -> bytes:
    digest = hashlib.sha256()
    _update_attestation_digest(digest, value)
    return digest.digest()


def _attest_compiled_context(value: CompiledContext) -> None:
    object_id = id(value)
    fingerprint = _compiled_attestation_digest(value)
    with _COMPILED_ATTESTATION_LOCK:
        _COMPILED_ATTESTATIONS.pop(object_id, None)
        _COMPILED_ATTESTATIONS[object_id] = fingerprint
        while len(_COMPILED_ATTESTATIONS) > _COMPILED_ATTESTATION_CAPACITY:
            _COMPILED_ATTESTATIONS.popitem(last=False)


def _require_compiled_attestation(value: object) -> None:
    if type(value) is not CompiledContext:
        raise ContextContractError("invalid_compiler_attestation")
    try:
        fingerprint = _compiled_attestation_digest(value)
    except (AttributeError, TypeError, ContextContractError) as exc:
        raise ContextContractError("invalid_compiler_attestation") from exc
    with _COMPILED_ATTESTATION_LOCK:
        expected = _COMPILED_ATTESTATIONS.get(id(value))
    if expected is None or not hmac.compare_digest(expected, fingerprint):
        raise ContextContractError("invalid_compiler_attestation")


@dataclass(frozen=True, slots=True)
class CompilerConfig:
    """Selected-route metadata and measurement or enforced private policy."""

    compiler_version: str
    revision: str
    resolved_provider: str | None
    resolved_model: str | None
    budget_policy: BudgetPolicy | BudgetPolicyV1

    def __post_init__(self) -> None:
        if self.compiler_version != COMPILER_VERSION:
            raise ContextContractError("unsupported_compiler_version")
        _require_identifier(self.revision, "invalid_compiler_config_revision")
        _require_optional_identifier(
            self.resolved_provider,
            "invalid_resolved_provider",
        )
        _require_optional_identifier(self.resolved_model, "invalid_resolved_model")
        if type(self.budget_policy) is BudgetPolicy:
            try:
                policy = BudgetPolicy(
                    policy_id=self.budget_policy.policy_id,
                    revision=self.budget_policy.revision,
                    status=self.budget_policy.status,
                    max_estimated_tokens=self.budget_policy.max_estimated_tokens,
                    max_characters=self.budget_policy.max_characters,
                )
            except AttributeError as exc:
                raise ContextContractError("invalid_budget_policy") from exc
        elif type(self.budget_policy) is BudgetPolicyV1:
            policy = copy_budget_policy_v1(self.budget_policy)
        else:
            raise ContextContractError("invalid_budget_policy")
        object.__setattr__(self, "budget_policy", policy)


@dataclass(frozen=True, slots=True)
class _RegistrySlot:
    section_id: str
    lifetime: Lifetime
    semantic_role: SemanticRole
    required: bool = False


def _slot(
    section_id: str,
    lifetime: Lifetime,
    semantic_role: SemanticRole,
    *,
    required: bool = False,
) -> _RegistrySlot:
    return _RegistrySlot(section_id, lifetime, semantic_role, required)


_PRIVATE_INTERACTIVE = (
    _slot(
        "core.private.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "policy.private.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "contract.output.private.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot("identity.owner.v1", Lifetime.DURABLE, SemanticRole.IDENTITY),
    _slot("relationship.shared.v1", Lifetime.DURABLE, SemanticRole.FACT),
    _slot(
        "preference.rules.v1",
        Lifetime.DURABLE,
        SemanticRole.INSTRUCTION,
    ),
    _slot("continuity.summary.v1", Lifetime.SESSION, SemanticRole.HISTORY),
    _slot("continuity.open_loops.v1", Lifetime.SESSION, SemanticRole.STATE),
    _slot(
        "continuity.recent_turns.v1",
        Lifetime.SESSION,
        SemanticRole.HISTORY,
    ),
    _slot("expression.private.v1", Lifetime.TURN, SemanticRole.STATE),
    _slot("situation.current.v1", Lifetime.TURN, SemanticRole.STATE),
    _slot("situation.temporal.v1", Lifetime.TURN, SemanticRole.STATE),
    _slot("memory.retrieval.v1", Lifetime.TURN, SemanticRole.EVIDENCE),
    _slot("memory.grounding.v1", Lifetime.TURN, SemanticRole.EVIDENCE),
    _slot("mode.turn.private.v1", Lifetime.TURN, SemanticRole.INSTRUCTION),
)

_PUBLIC_INTERACTIVE = (
    _slot(
        "core.public.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "policy.public.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "contract.output.public.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "continuity.public_room.v1",
        Lifetime.SESSION,
        SemanticRole.HISTORY,
    ),
    _slot("expression.public.v1", Lifetime.TURN, SemanticRole.STATE),
    _slot("public.request_context.v1", Lifetime.TURN, SemanticRole.STATE),
    _slot(
        "memory.public_grounding.v1",
        Lifetime.TURN,
        SemanticRole.EVIDENCE,
    ),
    _slot("mode.turn.public.v1", Lifetime.TURN, SemanticRole.INSTRUCTION),
)

_PUBLIC_CUM2 = (
    _slot(
        "core.public.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "policy.public.livestream.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "contract.output.cum2.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "continuity.public_room.v1",
        Lifetime.SESSION,
        SemanticRole.HISTORY,
    ),
    _slot("expression.public.v1", Lifetime.TURN, SemanticRole.STATE),
    _slot("public.request_context.v1", Lifetime.TURN, SemanticRole.STATE),
)

_OPERATOR_INTERACTIVE = (
    _slot(
        "core.operator.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "policy.operator.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "contract.output.operator.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot("expression.operator.v1", Lifetime.TURN, SemanticRole.STATE),
    _slot("operator.request_context.v1", Lifetime.TURN, SemanticRole.STATE),
    _slot("situation.operator_status.v1", Lifetime.TURN, SemanticRole.STATE),
)

_PRIVATE_AUTONOMY = (
    _slot(
        "core.private.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "policy.private.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "policy.autonomy.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "contract.output.autonomy.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot("expression.private.v1", Lifetime.TURN, SemanticRole.STATE),
    _slot("situation.current.v1", Lifetime.TURN, SemanticRole.STATE),
)

_PUBLIC_AUTONOMY = (
    _slot(
        "core.public.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "policy.public.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "policy.autonomy.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot(
        "contract.output.autonomy.v1",
        Lifetime.STATIC,
        SemanticRole.INSTRUCTION,
        required=True,
    ),
    _slot("expression.public.v1", Lifetime.TURN, SemanticRole.STATE),
    _slot("situation.current.v1", Lifetime.TURN, SemanticRole.STATE),
)

_REGISTRIES: dict[tuple[Lane, Route], tuple[_RegistrySlot, ...]] = {
    (Lane.PRIVATE_OWNER, Route.INTERACTIVE): _PRIVATE_INTERACTIVE,
    (Lane.PUBLIC_STAGE, Route.INTERACTIVE): _PUBLIC_INTERACTIVE,
    (Lane.PUBLIC_STAGE, Route.YOUTUBE_CUM2): _PUBLIC_CUM2,
    (Lane.OPERATOR_BACKSTAGE, Route.INTERACTIVE): _OPERATOR_INTERACTIVE,
    (Lane.PRIVATE_OWNER, Route.AUTONOMY): _PRIVATE_AUTONOMY,
    (Lane.PUBLIC_STAGE, Route.AUTONOMY): _PUBLIC_AUTONOMY,
}


def _require_identifier(value: object, code: str) -> str:
    if type(value) is not str or not value.strip():
        raise ContextContractError(code)
    return value


def _require_optional_identifier(value: object, code: str) -> str | None:
    if value is None:
        return None
    return _require_identifier(value, code)


def _copy_config(value: object) -> CompilerConfig:
    if type(value) is not CompilerConfig:
        raise ContextContractError("invalid_compiler_config")
    try:
        return CompilerConfig(
            compiler_version=value.compiler_version,
            revision=value.revision,
            resolved_provider=value.resolved_provider,
            resolved_model=value.resolved_model,
            budget_policy=value.budget_policy,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_compiler_config") from exc


def _copy_resolved_packet(value: object) -> ResolvedContextPacket:
    if type(value) is not ResolvedContextPacket:
        raise ContextContractError("invalid_resolved_context_packet")
    try:
        return ResolvedContextPacket(
            packet=value.packet,
            selected_sections=value.selected_sections,
            decisions=value.decisions,
            total_estimated_tokens=value.total_estimated_tokens,
            budget_enforced=value.budget_enforced,
            budget_policy_id=value.budget_policy_id,
            budget_policy_revision=value.budget_policy_revision,
            budget_enforcement_version=value.budget_enforcement_version,
            budget_initial_overflow=value.budget_initial_overflow,
            budget_shedding=value.budget_shedding,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_resolved_context_packet") from exc


def _normalize_text(value: str) -> str:
    return unicodedata.normalize(
        "NFC",
        value.replace("\r\n", "\n").replace("\r", "\n"),
    )


def _render_top_level_text(value: str) -> str:
    lines = [line.rstrip(" \t") for line in _normalize_text(value).split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _json_string(value: str) -> str:
    return json.dumps(
        _normalize_text(value),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _render_structured(value: FrozenPayload) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ContextContractError("non_finite_number")
        return f"{value:.6f}"
    if type(value) is str:
        return _json_string(value)
    if type(value) is tuple:
        return "[" + ",".join(_render_structured(item) for item in value) + "]"
    if type(value) is FrozenMapping:
        return "{" + ",".join(
            f"{_json_string(key)}:{_render_structured(item)}"
            for key, item in value._items
        ) + "}"
    raise ContextContractError("unsupported_frozen_payload")


def _render_payload(value: FrozenPayload) -> str:
    if type(value) is str:
        return _render_top_level_text(value)
    return _render_structured(value)


def estimate_tokens_v1(text: str) -> int:
    """Return a deterministic estimate, never a provider-token upper bound."""

    if type(text) is not str:
        raise ContextContractError("invalid_estimator_input")
    normalized = unicodedata.normalize("NFC", text)
    return max(
        math.ceil(len(normalized) / 3),
        math.ceil(len(normalized.encode("utf-8")) / 4),
    )


def canonical_message_projection(
    messages: tuple[CompiledMessage, ...] | list[CompiledMessage],
) -> bytes:
    """Project the exact v1 message tuple into canonical UTF-8 JSON bytes."""

    try:
        values = tuple(messages)
    except TypeError as exc:
        raise ContextContractError("invalid_wire_profile") from exc
    if len(values) != 2 or [getattr(item, "role", None) for item in values] != [
        "system",
        "user",
    ]:
        raise ContextContractError("invalid_wire_profile")
    copied: list[CompiledMessage] = []
    for item in values:
        if type(item) is not CompiledMessage:
            raise ContextContractError("invalid_compiled_messages")
        try:
            copied.append(CompiledMessage(role=item.role, content=item.content))
        except AttributeError as exc:
            raise ContextContractError("invalid_compiled_messages") from exc
    return json.dumps(
        [[message.role, message.content] for message in copied],
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_system_prefix_projection(system_prefix: str) -> bytes:
    encoded = _json_string(system_prefix).encode("utf-8")
    return b'[["system",' + encoded[:-1]


def _canonical_user_content_prefix(system_content: str) -> bytes:
    return (
        b'[["system",'
        + _json_string(system_content).encode("utf-8")
        + b'],["user","'
    )


def registry_slot_ids(lane: Lane, route: Route) -> tuple[str, ...]:
    """Return the frozen canonical slot order for a supported v1 profile."""

    registry = _REGISTRIES.get((lane, route))
    if registry is None:
        raise ContextContractError("unsupported_compiler_profile")
    return tuple(slot.section_id for slot in registry)


def virtual_user_slot_id(route: Route) -> str:
    if route is Route.AUTONOMY:
        return "autonomy.trigger.v1"
    return "current_input"


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _prefix_metrics(system_prefix: str) -> tuple[str, int, int, int]:
    projection = _canonical_system_prefix_projection(system_prefix)
    return (
        _hash(projection),
        len(system_prefix),
        len(system_prefix.encode("utf-8")),
        estimate_tokens_v1(system_prefix),
    )


class ContextCompiler:
    """Compile an already-resolved packet without side effects."""

    def compile(
        self,
        packet: ResolvedContextPacket,
        config: CompilerConfig,
    ) -> CompiledContext:
        selected_config = _copy_config(config)
        if type(selected_config.budget_policy) is BudgetPolicyV1:
            require_budget_enforcement_attestation(
                packet,
                selected_config.budget_policy,
            )
        elif getattr(packet, "budget_enforced", False):
            raise ContextContractError("invalid_budget_enforcement_attestation")
        resolved = _copy_resolved_packet(packet)
        request = resolved.packet.request
        if selected_config.revision != request.compiler_config_revision:
            raise ContextContractError("compiler_config_revision_mismatch")

        lane = request.scope.lane
        route = request.route
        registry = _REGISTRIES.get((lane, route))
        if registry is None:
            raise ContextContractError("unsupported_compiler_profile")

        registry_by_id = {slot.section_id: slot for slot in registry}
        candidate_by_id = {
            section.id: section for section in resolved.packet.sections
        }
        decision_by_id = {
            decision.section_id: decision for decision in resolved.decisions
        }

        for section in resolved.packet.sections:
            if section.id == "autonomy.trigger.v1":
                raise ContextContractError("virtual_user_section_forbidden")
            slot = registry_by_id.get(section.id)
            if slot is None:
                raise ContextContractError("unknown_section_id")
            if section.lifetime is not slot.lifetime:
                raise ContextContractError("invalid_section_lifetime")
            if section.semantic_role is not slot.semantic_role:
                raise ContextContractError("invalid_section_role")

        missing_required = [
            slot.section_id
            for slot in registry
            if slot.required and slot.section_id not in candidate_by_id
        ]
        if missing_required:
            raise ContextContractError("missing_required_section")

        for slot in registry:
            section = candidate_by_id.get(slot.section_id)
            if section is None:
                continue
            decision = decision_by_id[slot.section_id]
            if not decision.included and (slot.required or section.required):
                raise ContextContractError("required_section_dropped")

        rendered: dict[str, str] = {
            section.id: _render_payload(section.payload)
            for section in resolved.packet.sections
        }
        for section in resolved.packet.sections:
            if decision_by_id[section.id].included and not rendered[section.id]:
                raise ContextContractError("empty_selected_section")

        manifest_rows: list[SelectionDecision] = []
        system_parts: list[str] = []
        system_prefix = ""
        layer_content: dict[Lifetime, str] = {}
        for slot in registry:
            section = candidate_by_id.get(slot.section_id)
            if section is None:
                continue
            decision = decision_by_id[slot.section_id]
            rendered_section = rendered[slot.section_id]
            manifest_rows.append(
                SelectionDecision(
                    section_id=decision.section_id,
                    decision=decision.decision,
                    reason=decision.reason,
                    source=decision.source,
                    lifetime=decision.lifetime,
                    semantic_role=decision.semantic_role,
                    revision=decision.revision,
                    formatter_version=decision.formatter_version,
                    budget_class=decision.budget_class,
                    chars=len(rendered_section),
                    token_estimate=estimate_tokens_v1(rendered_section),
                    related_section_id=decision.related_section_id,
                )
            )
            if not decision.included:
                continue
            if system_parts:
                system_prefix += "\n\n"
            system_parts.append(rendered_section)
            system_prefix += rendered_section
            if slot.lifetime in {
                Lifetime.STATIC,
                Lifetime.DURABLE,
                Lifetime.SESSION,
            }:
                layer_content[slot.lifetime] = system_prefix

        static_content = layer_content[Lifetime.STATIC]
        durable_content = layer_content.get(Lifetime.DURABLE, static_content)
        session_content = layer_content.get(Lifetime.SESSION, durable_content)
        static_hash, static_chars, _, static_tokens = _prefix_metrics(static_content)
        durable_hash, durable_chars, _, durable_tokens = _prefix_metrics(
            durable_content
        )
        session_hash, session_chars, _, session_tokens = _prefix_metrics(
            session_content
        )

        system_content = system_prefix + "\n"
        user_content = _render_top_level_text(request.current_input)
        if not user_content:
            raise ContextContractError("empty_current_input")
        messages = (
            CompiledMessage(role="system", content=system_content),
            CompiledMessage(role="user", content=user_content),
        )
        projection = canonical_message_projection(messages)
        full_hash = _hash(projection)
        input_chars = len(system_content) + len(user_content)
        input_tokens = estimate_tokens_v1(system_content) + estimate_tokens_v1(
            user_content
        )

        budget = selected_config.budget_policy
        if resolved.budget_enforced:
            candidate_overflow = resolved.budget_initial_overflow
        else:
            candidate_overflow = (
                input_chars > budget.max_characters
                or input_tokens > budget.max_estimated_tokens
                or any(
                    estimate_tokens_v1(rendered[section.id]) > section.max_tokens
                    for section in resolved.packet.sections
                )
            )
        selected_count = sum(row.included for row in manifest_rows)
        manifest = CompilationManifest(
            request_id=request.request_id,
            correlation_id=request.correlation_id,
            capture_id=resolved.packet.capture_id,
            snapshot_revision=resolved.packet.snapshot_revision,
            model=request.model,
            resolved_model=selected_config.resolved_model,
            resolved_provider=selected_config.resolved_provider,
            lane=lane,
            route=route,
            collection_policy_id=request.scope.policy_id,
            budget_policy_id=budget.policy_id,
            budget_policy_revision=budget.revision,
            budget_policy_status=budget.status,
            budget_enforced=resolved.budget_enforced,
            candidate_overflow=candidate_overflow,
            compiler_version=selected_config.compiler_version,
            compiler_config_revision=selected_config.revision,
            wire_profile=WIRE_PROFILE,
            static_prefix_hash=static_hash,
            durable_prefix_hash=durable_hash,
            session_prefix_hash=session_hash,
            full_context_hash=full_hash,
            prior_compatible_request_id=None,
            prefix_break_section_id=None,
            prefix_comparison_status="no_prior",
            source_revisions=tuple(
                sorted(resolved.packet.source_revisions, key=lambda item: item[0])
            ),
            collection=tuple(
                sorted(
                    resolved.packet.collection_receipt,
                    key=lambda item: item.source,
                )
            ),
            sections=tuple(manifest_rows),
            input_chars=input_chars,
            input_tokens_est=input_tokens,
            static_prefix_chars=static_chars,
            static_prefix_tokens_est=static_tokens,
            durable_prefix_chars=durable_chars,
            durable_prefix_tokens_est=durable_tokens,
            session_prefix_chars=session_chars,
            session_prefix_tokens_est=session_tokens,
            prior_exact_matching_prefix_chars=0,
            prior_exact_matching_prefix_tokens_est=0,
            selected_sections=selected_count,
            dropped_sections=len(manifest_rows) - selected_count,
            budget_enforcement_version=resolved.budget_enforcement_version,
            budget_initial_overflow=resolved.budget_initial_overflow,
            budget_shedding=resolved.budget_shedding,
        )
        compiled = CompiledContext(
            compiler_version=selected_config.compiler_version,
            lane=lane,
            route=route,
            model=request.model,
            messages=messages,
            static_prefix_hash=static_hash,
            durable_prefix_hash=durable_hash,
            session_prefix_hash=session_hash,
            full_context_hash=full_hash,
            static_prefix_chars=static_chars,
            durable_prefix_chars=durable_chars,
            session_prefix_chars=session_chars,
            manifest=manifest,
        )
        _attest_compiled_context(compiled)
        return compiled


__all__ = [
    "COMPILER_VERSION",
    "WIRE_PROFILE",
    "CompilerConfig",
    "ContextCompiler",
    "canonical_message_projection",
    "estimate_tokens_v1",
    "registry_slot_ids",
    "virtual_user_slot_id",
]
