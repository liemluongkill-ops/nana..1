"""Audience and immutable capture contracts for autonomous context."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
import hashlib
import math
from types import MappingProxyType
from threading import RLock
import uuid
import weakref

from nana.runtime.context_contracts import (
    BudgetPolicy,
    BudgetPolicyStatus,
    ContextContractError,
    ContextSection,
    Freshness,
    FrozenMapping,
    Lane,
    Lifetime,
    Route,
    SemanticRole,
    SourceRef,
    UnresolvedContextRequest,
    _make_resolved_context_request,
    _make_resolved_context_scope,
    _require_canonical_public_scope,
    _structural_digest,
    freeze_payload,
    require_resolved_context_request,
)


_AUDIENCE_SEAL = object()
_SNAPSHOT_SEAL = object()
_ATTESTATION_CAPACITY = 1_024
_ATTESTATION_LOCK = RLock()
_AUDIENCE_ATTESTATIONS: OrderedDict[
    int,
    tuple[weakref.ReferenceType[object], bytes],
] = OrderedDict()
_SNAPSHOT_ATTESTATIONS: OrderedDict[
    int,
    tuple[weakref.ReferenceType[object], bytes],
] = OrderedDict()
_COMPILER_CONFIG_REVISION = "compiler-config.v1"
_AUTONOMY_BUDGET_REVISION = "autonomy.measurement.2026-10-03"
_AUTONOMY_MODES = frozenset({"idle_banter", "observer_aware", "stream_host"})

_PRIVATE_SLOT_LIMITS = MappingProxyType(
    {
        "core.private.v1": (1_000, 4_000),
        "policy.private.v1": (500, 2_000),
        "policy.autonomy.v1": (300, 1_200),
        "contract.output.autonomy.v1": (300, 1_200),
        "expression.private.v1": (300, 900),
        "situation.current.v1": (500, 1_500),
        "autonomy.trigger.v1": (256, 768),
    }
)
_PUBLIC_SLOT_LIMITS = MappingProxyType(
    {
        "core.public.v1": (1_000, 4_000),
        "policy.public.v1": (500, 2_000),
        "policy.autonomy.v1": (300, 1_200),
        "contract.output.autonomy.v1": (300, 1_200),
        "expression.public.v1": (300, 900),
        "situation.current.v1": (500, 1_500),
        "autonomy.trigger.v1": (256, 768),
    }
)


@dataclass(frozen=True, slots=True)
class PrivateOwnerAutonomyAuthority:
    """Explicit composition authority for the owner's background loop."""


_PUBLIC_STATE_KEYS = frozenset(
    {
        "platform",
        "room_id",
        "stream_session_id",
        "event_id",
        "display_name",
        "active_zone",
        "active_app",
        "time",
        "user_is_typing",
        "game_active",
        "command_in_flight",
        "audio_busy",
        "scene_relevance",
        "silence_duration_s",
        "silence_window_s",
        "forced_mode",
        "jitter_value",
        "web_context",
        "attention_window",
        "stream_stage_policy_gate",
    }
)


@dataclass(frozen=True, slots=True)
class PublicAutonomyAuthority:
    """Canonical public scope plus caller-owned, allowlisted current state."""

    scope: object
    caller_state: FrozenMapping

    def __post_init__(self) -> None:
        scope = _require_canonical_public_scope(self.scope)
        if not isinstance(self.caller_state, Mapping):
            raise ContextContractError("invalid_public_autonomy_state")
        unknown = set(self.caller_state) - _PUBLIC_STATE_KEYS
        if unknown:
            raise ContextContractError("invalid_public_autonomy_state")
        expected = {
            "platform": scope.platform,
            "room_id": scope.room_id,
            "stream_session_id": scope.stream_session_id,
            "event_id": scope.event_id,
            "display_name": scope.display_name,
        }
        for key, value in expected.items():
            if key in self.caller_state and self.caller_state[key] != value:
                raise ContextContractError("public_autonomy_scope_mismatch")
        frozen = freeze_payload(dict(self.caller_state))
        if not isinstance(frozen, FrozenMapping):
            raise ContextContractError("invalid_public_autonomy_state")
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "caller_state", frozen)


@dataclass(frozen=True, slots=True, init=False, weakref_slot=True)
class ResolvedAutonomyAudience:
    lane: Lane
    interaction_scope: str
    public_scope: object | None
    caller_state: FrozenMapping
    _seal: object = field(repr=False, compare=False)

    def __init__(self) -> None:
        raise ContextContractError("invalid_autonomy_audience")


@dataclass(frozen=True, slots=True, init=False, weakref_slot=True)
class AutonomyObserverSnapshot:
    capture_id: str
    snapshot_revision: int
    audience: ResolvedAutonomyAudience
    captured_wall_time: float
    captured_monotonic_time: float
    payload: FrozenMapping
    _seal: object = field(repr=False, compare=False)

    def __init__(self) -> None:
        raise ContextContractError("invalid_autonomy_snapshot")


def _scope_fingerprint_fields(scope: object | None) -> object:
    if scope is None:
        return None
    return (
        scope.platform,
        scope.room_id,
        scope.stream_session_id,
        scope.event_id,
        scope.display_name,
        scope.identity.platform,
        scope.identity.author_id,
        scope.identity.actor_key,
    )


def _audience_fingerprint(audience: ResolvedAutonomyAudience) -> bytes:
    return _structural_digest(
        (
            "autonomy.audience.v1",
            audience.lane,
            audience.interaction_scope,
            _scope_fingerprint_fields(audience.public_scope),
            audience.caller_state,
        )
    )


def _snapshot_fingerprint(snapshot: AutonomyObserverSnapshot) -> bytes:
    return _structural_digest(
        (
            "autonomy.snapshot.v1",
            snapshot.capture_id,
            snapshot.snapshot_revision,
            id(snapshot.audience),
            snapshot.captured_wall_time,
            snapshot.captured_monotonic_time,
            snapshot.payload,
        )
    )


def _register_attestation(value, registry, fingerprint: bytes) -> None:
    object_id = id(value)

    def cleanup(reference) -> None:
        with _ATTESTATION_LOCK:
            current = registry.get(object_id)
            if current is not None and current[0] is reference:
                registry.pop(object_id, None)

    reference = weakref.ref(value, cleanup)
    with _ATTESTATION_LOCK:
        registry[object_id] = (reference, fingerprint)
        registry.move_to_end(object_id)
        while len(registry) > _ATTESTATION_CAPACITY:
            registry.popitem(last=False)


def _require_attestation(value, registry, fingerprint_factory) -> None:
    with _ATTESTATION_LOCK:
        entry = registry.get(id(value))
        if entry is None or entry[0]() is not value:
            raise ContextContractError("invalid_autonomy_snapshot")
        expected = entry[1]
    try:
        actual = fingerprint_factory(value)
    except Exception as exc:
        raise ContextContractError("invalid_autonomy_snapshot") from exc
    if actual != expected:
        raise ContextContractError("invalid_autonomy_snapshot")


def _resolved_private_audience() -> ResolvedAutonomyAudience:
    audience = object.__new__(ResolvedAutonomyAudience)
    object.__setattr__(audience, "lane", Lane.PRIVATE_OWNER)
    object.__setattr__(audience, "interaction_scope", "private_owner")
    object.__setattr__(audience, "public_scope", None)
    object.__setattr__(audience, "caller_state", freeze_payload({}))
    object.__setattr__(audience, "_seal", _AUDIENCE_SEAL)
    _register_attestation(
        audience,
        _AUDIENCE_ATTESTATIONS,
        _audience_fingerprint(audience),
    )
    return audience


def _resolved_public_audience(
    authority: PublicAutonomyAuthority,
) -> ResolvedAutonomyAudience:
    audience = object.__new__(ResolvedAutonomyAudience)
    object.__setattr__(audience, "lane", Lane.PUBLIC_STAGE)
    object.__setattr__(audience, "interaction_scope", "public_viewer")
    object.__setattr__(audience, "public_scope", authority.scope)
    object.__setattr__(audience, "caller_state", authority.caller_state)
    object.__setattr__(audience, "_seal", _AUDIENCE_SEAL)
    _register_attestation(
        audience,
        _AUDIENCE_ATTESTATIONS,
        _audience_fingerprint(audience),
    )
    return audience


def resolve_autonomy_audience(authority: object) -> ResolvedAutonomyAudience:
    """Resolve only an explicit authority object; raw labels are never trusted."""

    if type(authority) is PrivateOwnerAutonomyAuthority:
        return _resolved_private_audience()
    if type(authority) is PublicAutonomyAuthority:
        # Re-run validation against a fresh exact copy before issuing authority.
        validated = PublicAutonomyAuthority(
            scope=authority.scope,
            caller_state=authority.caller_state,
        )
        return _resolved_public_audience(validated)
    raise ContextContractError("trusted_autonomy_audience_required")


def require_autonomy_audience(value: object) -> ResolvedAutonomyAudience:
    if (
        type(value) is not ResolvedAutonomyAudience
        or getattr(value, "_seal", None) is not _AUDIENCE_SEAL
    ):
        raise ContextContractError("invalid_autonomy_audience")
    try:
        _require_attestation(value, _AUDIENCE_ATTESTATIONS, _audience_fingerprint)
    except ContextContractError as exc:
        raise ContextContractError("invalid_autonomy_audience") from exc
    if not isinstance(value.caller_state, FrozenMapping):
        raise ContextContractError("invalid_autonomy_audience")
    if value.lane is Lane.PRIVATE_OWNER:
        if (
            value.interaction_scope != "private_owner"
            or value.public_scope is not None
            or len(value.caller_state) != 0
        ):
            raise ContextContractError("invalid_autonomy_audience")
    elif value.lane is Lane.PUBLIC_STAGE:
        if value.interaction_scope != "public_viewer":
            raise ContextContractError("invalid_autonomy_audience")
        _require_canonical_public_scope(value.public_scope)
    else:
        raise ContextContractError("invalid_autonomy_audience")
    return value


def audience_fields(audience: object) -> dict[str, object]:
    resolved = require_autonomy_audience(audience)
    public_scope = resolved.public_scope
    return {
        "lane": resolved.lane.value,
        "interaction_scope": resolved.interaction_scope,
        "public_scope": (
            None
            if public_scope is None
            else {
                "platform": public_scope.platform,
                "room_id": public_scope.room_id,
                "stream_session_id": public_scope.stream_session_id,
                "event_id": public_scope.event_id,
                "display_name": public_scope.display_name,
            }
        ),
    }


def freeze_autonomy_snapshot(
    *,
    audience: object,
    payload: Mapping[str, object],
    captured_wall_time: float,
    captured_monotonic_time: float,
) -> AutonomyObserverSnapshot:
    resolved = require_autonomy_audience(audience)
    if not isinstance(payload, Mapping):
        raise ContextContractError("invalid_autonomy_snapshot")
    for value in (captured_wall_time, captured_monotonic_time):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0.0
        ):
            raise ContextContractError("invalid_autonomy_snapshot")
    frozen = freeze_payload(dict(payload))
    if not isinstance(frozen, FrozenMapping):
        raise ContextContractError("invalid_autonomy_snapshot")
    snapshot = object.__new__(AutonomyObserverSnapshot)
    object.__setattr__(snapshot, "capture_id", f"autonomy-{uuid.uuid4().hex}")
    object.__setattr__(snapshot, "snapshot_revision", 1)
    object.__setattr__(snapshot, "audience", resolved)
    object.__setattr__(snapshot, "captured_wall_time", float(captured_wall_time))
    object.__setattr__(
        snapshot,
        "captured_monotonic_time",
        float(captured_monotonic_time),
    )
    object.__setattr__(snapshot, "payload", frozen)
    object.__setattr__(snapshot, "_seal", _SNAPSHOT_SEAL)
    _register_attestation(
        snapshot,
        _SNAPSHOT_ATTESTATIONS,
        _snapshot_fingerprint(snapshot),
    )
    return snapshot


def require_autonomy_snapshot(value: object) -> AutonomyObserverSnapshot:
    if (
        type(value) is not AutonomyObserverSnapshot
        or getattr(value, "_seal", None) is not _SNAPSHOT_SEAL
    ):
        raise ContextContractError("invalid_autonomy_snapshot")
    _require_attestation(value, _SNAPSHOT_ATTESTATIONS, _snapshot_fingerprint)
    audience = require_autonomy_audience(value.audience)
    if not isinstance(value.payload, FrozenMapping):
        raise ContextContractError("invalid_autonomy_snapshot")
    expected_audience = freeze_payload(audience_fields(audience))
    if (
        value.payload.get("audience_resolved") is not True
        or value.payload.get("audience") != expected_audience
    ):
        raise ContextContractError("invalid_autonomy_snapshot")
    from nana.runtime.livestream_identity import is_livestream_source

    expected_stage = (
        audience.lane is Lane.PUBLIC_STAGE
        and is_livestream_source(audience.public_scope.platform)
    )
    if value.payload.get("stream_stage_policy_gate") is not expected_stage:
        raise ContextContractError("invalid_autonomy_snapshot")
    return value


def autonomy_snapshot_cache_key(value: object) -> str:
    """Return a content-free cache identity bound to lane and public scope."""

    snapshot = require_autonomy_snapshot(value)
    audience = snapshot.audience
    return _structural_digest(
        (
            "autonomy.cache.v1",
            audience.lane,
            _scope_fingerprint_fields(audience.public_scope),
        )
    ).hex()


def autonomy_slot_limits(lane: Lane) -> Mapping[str, tuple[int, int]]:
    if lane is Lane.PRIVATE_OWNER:
        return _PRIVATE_SLOT_LIMITS
    if lane is Lane.PUBLIC_STAGE:
        return _PUBLIC_SLOT_LIMITS
    raise ContextContractError("unsupported_autonomy_lane")


def provisional_autonomy_budget(lane: Lane) -> BudgetPolicy:
    if lane is Lane.PRIVATE_OWNER:
        suffix = "private"
    elif lane is Lane.PUBLIC_STAGE:
        suffix = "public"
    else:
        raise ContextContractError("unsupported_autonomy_lane")
    return BudgetPolicy(
        policy_id=f"budget.shadow.autonomy_{suffix}.v1",
        revision=_AUTONOMY_BUDGET_REVISION,
        status=BudgetPolicyStatus.SHADOW_CANDIDATE,
        max_estimated_tokens=3_000,
        max_characters=12_000,
    )


def _autonomy_trigger(mode: str, lane: Lane) -> str:
    if mode not in _AUTONOMY_MODES:
        raise ContextContractError("unsupported_autonomy_mode")
    trigger = (
        "autonomy.trigger.v1\n"
        f"mode={mode}\n"
        f"audience={lane.value}\n"
        "Generate one short autonomous line grounded only in the supplied context."
    )
    token_limit, char_limit = autonomy_slot_limits(lane)["autonomy.trigger.v1"]
    from nana.runtime.context_compiler import estimate_tokens_v1

    if len(trigger) > char_limit or estimate_tokens_v1(trigger) > token_limit:
        raise ContextContractError("autonomy_trigger_oversized")
    return trigger


def _resolved_autonomy_request(
    snapshot: AutonomyObserverSnapshot,
    *,
    mode: str,
    model: str,
):
    if type(model) is not str or not model.strip():
        raise ContextContractError("invalid_model")
    audience = require_autonomy_audience(snapshot.audience)
    lane = audience.lane
    trigger = _autonomy_trigger(mode, lane)
    scope = _make_resolved_context_scope(
        lane=lane,
        interaction_scope=audience.interaction_scope,
        public_scope=audience.public_scope,
        policy_id=(
            "private.autonomy.v1"
            if lane is Lane.PRIVATE_OWNER
            else "public.autonomy.v1"
        ),
        resolver_version="nana-context-autonomy-resolver.v1",
    )
    public_scope = audience.public_scope
    unresolved = UnresolvedContextRequest(
        request_id=f"request-{snapshot.capture_id}-{mode}",
        correlation_id=f"correlation-{snapshot.capture_id}",
        route=Route.AUTONOMY,
        model=model,
        current_input=trigger,
        viewer_name=(None if public_scope is None else public_scope.display_name),
        stream_mode=public_scope is not None,
        public_platform=(None if public_scope is None else public_scope.platform),
        caller_metadata=(
            {}
            if public_scope is None
            else {
                "platform": public_scope.platform,
                "room_id": public_scope.room_id,
                "stream_session_id": public_scope.stream_session_id,
                "event_id": public_scope.event_id,
                "display_name": public_scope.display_name,
                "author_id": public_scope.identity.author_id,
                "actor_key": public_scope.identity.actor_key,
            }
        ),
        bridge_system=False,
        story_mode=False,
        casual_mode=False,
        temporal_intent=False,
        grounding_intent=False,
    )
    public_context = (
        {}
        if public_scope is None
        else {
            "platform": public_scope.platform,
            "display_name": public_scope.display_name,
            "public_metadata": {},
        }
    )
    return _make_resolved_context_request(
        unresolved=unresolved,
        scope=scope,
        public_context=public_context,
        captured_wall_time=snapshot.captured_wall_time,
        captured_monotonic_time=snapshot.captured_monotonic_time,
        compiler_config_revision=_COMPILER_CONFIG_REVISION,
    )


def _bounded_text(value: object, limit: int) -> str:
    text = str(value or "").strip().replace("\x00", "")
    return text[:limit]


def _section_payloads(snapshot: AutonomyObserverSnapshot) -> dict[str, object]:
    audience = snapshot.audience
    lane = audience.lane
    values = snapshot.payload
    if lane is Lane.PRIVATE_OWNER:
        core_id = "core.private.v1"
        policy_id = "policy.private.v1"
        expression_id = "expression.private.v1"
        core = (
            "You are Nana in a private owner autonomy moment. "
            "Speak naturally in Vietnamese without inventing context."
        )
        policy = (
            "PRIVATE AUTONOMY POLICY:\n"
            "- Address the private owner naturally.\n"
            "- Use only this captured current-state projection.\n"
            "- Do not claim an action, playback, or delivery occurred."
        )
    else:
        core_id = "core.public.v1"
        policy_id = "policy.public.v1"
        expression_id = "expression.public.v1"
        core = (
            "You are Nana's public AI VTuber voice. "
            "Speak naturally in Vietnamese without private-owner assumptions."
        )
        policy = (
            "PUBLIC AUTONOMY POLICY:\n"
            "- Treat the audience as public viewers, never as the private owner.\n"
            "- Use only the caller-owned public snapshot supplied here.\n"
            "- Never disclose private memory, owner state, credentials, or history."
        )
        from nana.runtime.livestream_identity import (
            is_livestream_source,
            stage_prompt_block,
        )

        if is_livestream_source(audience.public_scope.platform):
            policy = stage_prompt_block() + "\n\n" + policy

    mood_value = values.get("mood_affection")
    expression = {
        "audience": lane.value,
        "mood_affection": 0.5 if mood_value is None else float(mood_value),
        "user_is_typing": bool(values.get("user_is_typing", False)),
        "audio_busy": bool(values.get("audio_busy", False)),
    }
    time_state = values.get("time")
    web_state = values.get("web_context")
    web_effective = (
        isinstance(web_state, Mapping)
        and web_state.get("effective") is True
        and web_state.get("fresh", True) is not False
    )
    situation = {
        "audience": lane.value,
        "active_zone": _bounded_text(values.get("active_zone"), 80),
        "active_app": _bounded_text(values.get("active_app"), 160),
        "part_of_day": _bounded_text(
            time_state.get("part_of_day") if isinstance(time_state, Mapping) else "",
            40,
        ),
        "attention_window": _bounded_text(values.get("attention_window"), 80),
        "silence_duration_s": float(
            values.get("silence_duration_s", 0.0) or 0.0
        ),
        "web_kind": _bounded_text(
            web_state.get("kind") if web_effective else "",
            80,
        ),
        "web_title": _bounded_text(
            web_state.get("title") if web_effective else "",
            200,
        ),
    }
    return {
        core_id: core,
        policy_id: policy,
        "policy.autonomy.v1": (
            "AUTONOMY POLICY:\n"
            "- Produce at most one contextual line for the accepted tick.\n"
            "- Do not pressure the audience to reply or invent unseen facts."
        ),
        "contract.output.autonomy.v1": (
            "AUTONOMY OUTPUT CONTRACT:\n"
            "- Return reply text only, one or two short sentences.\n"
            "- Do not include analysis, metadata, or delivery claims."
        ),
        expression_id: expression,
        "situation.current.v1": situation,
    }


def _autonomy_source_view(request, snapshot: AutonomyObserverSnapshot):
    from nana.runtime.context_adapters import (
        PrivateSourceView,
        PublicSourceView,
        SourceBatch,
    )
    from nana.runtime.context_compiler import _render_payload, estimate_tokens_v1

    lane = snapshot.audience.lane
    limits = autonomy_slot_limits(lane)
    payloads = _section_payloads(snapshot)
    specs: dict[str, tuple[str, Lifetime, SemanticRole, bool]] = {}
    core_id = "core.private.v1" if lane is Lane.PRIVATE_OWNER else "core.public.v1"
    policy_id = (
        "policy.private.v1" if lane is Lane.PRIVATE_OWNER else "policy.public.v1"
    )
    expression_id = (
        "expression.private.v1"
        if lane is Lane.PRIVATE_OWNER
        else "expression.public.v1"
    )
    for section_id in (core_id, policy_id, "policy.autonomy.v1", "contract.output.autonomy.v1"):
        specs[section_id] = ("core", Lifetime.STATIC, SemanticRole.INSTRUCTION, True)
    specs[expression_id] = ("expression", Lifetime.TURN, SemanticRole.STATE, False)
    specs["situation.current.v1"] = (
        "runtime_state" if lane is Lane.PRIVATE_OWNER else "public_scope",
        Lifetime.TURN,
        SemanticRole.STATE,
        False,
    )

    grouped: dict[str, list[tuple[str, object, Lifetime, SemanticRole, bool]]] = {}
    for section_id, payload in payloads.items():
        owner, lifetime, role, required = specs[section_id]
        token_limit, char_limit = limits[section_id]
        frozen_payload = freeze_payload(payload)
        rendered = _render_payload(frozen_payload)
        if len(rendered) > char_limit or estimate_tokens_v1(rendered) > token_limit:
            raise ContextContractError("autonomy_section_oversized")
        grouped.setdefault(owner, []).append(
            (section_id, frozen_payload, lifetime, role, required)
        )

    class CapturedAdapter:
        def __init__(self, batch):
            self.batch = batch

        def read(self, candidate_request):
            if require_resolved_context_request(candidate_request) is not request:
                raise ContextContractError("autonomy_capture_request_mismatch")
            return self.batch

    def adapter(owner: str):
        rows = grouped.get(owner, [])
        digest = hashlib.sha256()
        for section_id, payload, *_rest in rows:
            digest.update(section_id.encode("utf-8"))
            digest.update(b"\x00")
            digest.update(_render_payload(payload).encode("utf-8"))
            digest.update(b"\x00")
        revision = f"autonomy-{owner}-{digest.hexdigest()}"
        sections = tuple(
            ContextSection(
                id=section_id,
                lifetime=lifetime,
                semantic_role=role,
                freshness=Freshness.FRESH,
                visibility=frozenset({lane}),
                source=SourceRef(owner, "autonomy.snapshot.v1", section_id),
                revision=revision,
                authority=None,
                observed_at=(
                    None
                    if lifetime is Lifetime.STATIC
                    else snapshot.captured_wall_time
                ),
                expires_at=None,
                conflict_key=None,
                dedupe_key=section_id,
                max_tokens=limits[section_id][0],
                payload=payload,
                formatter_version="autonomy.v1",
                required=required,
                budget_class="mandatory" if required else "optional",
                semantic_status="active",
                provenance=(),
                relevance=1.0,
            )
            for section_id, payload, lifetime, role, required in rows
        )
        return CapturedAdapter(SourceBatch(owner, revision, sections))

    if lane is Lane.PRIVATE_OWNER:
        return PrivateSourceView(
            core=adapter("core"),
            owner_identity=adapter("owner_identity"),
            private_memory=adapter("private_memory"),
            private_session=adapter("private_session"),
            runtime_state=adapter("runtime_state"),
            expression=adapter("expression"),
        )
    return PublicSourceView(
        core=adapter("core"),
        public_scope=adapter("public_scope"),
        public_session=adapter("public_session"),
        public_grounding=adapter("public_grounding"),
        expression=adapter("expression"),
    )


def compile_autonomy_context(
    snapshot: object,
    *,
    mode: str,
    model: str,
):
    """Compile one already-captured autonomy snapshot without reading sources."""

    frozen = require_autonomy_snapshot(snapshot)
    request = _resolved_autonomy_request(frozen, mode=mode, model=model)
    source_view = _autonomy_source_view(request, frozen)
    from nana.runtime.context_adapters import PrivateSourceView
    from nana.runtime.context_compiler import (
        COMPILER_VERSION,
        CompilerConfig,
        ContextCompiler,
    )
    from nana.runtime.context_runtime import ContextRuntime, LaneAwareContextCollector

    collector_kwargs = (
        {"private_sources": source_view}
        if isinstance(source_view, PrivateSourceView)
        else {"public_sources": source_view}
    )
    packet = LaneAwareContextCollector(
        **collector_kwargs,
        capture_id_factory=lambda: frozen.capture_id,
        snapshot_revision_factory=lambda: frozen.snapshot_revision,
    ).collect(request)
    budget = provisional_autonomy_budget(request.scope.lane)
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


__all__ = [
    "AutonomyObserverSnapshot",
    "PrivateOwnerAutonomyAuthority",
    "PublicAutonomyAuthority",
    "ResolvedAutonomyAudience",
    "audience_fields",
    "autonomy_slot_limits",
    "autonomy_snapshot_cache_key",
    "compile_autonomy_context",
    "freeze_autonomy_snapshot",
    "provisional_autonomy_budget",
    "require_autonomy_audience",
    "require_autonomy_snapshot",
    "resolve_autonomy_audience",
]
