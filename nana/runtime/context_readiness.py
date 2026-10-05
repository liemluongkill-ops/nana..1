"""Injected private route/capacity readiness below the chat boundary.

The shipped registry is intentionally empty. Trusted composition may create a
bounded registry from explicitly reviewed profiles, but a profile or copied
grant supplied by an ordinary caller never creates readiness.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import re
import threading
from urllib.parse import urlsplit
import weakref

from nana.runtime.context_budget import ReviewedModelCapabilityV1
from nana.runtime.context_contracts import (
    CompiledContext,
    ContextContractError,
    Lane,
    Route,
)


_SAFE_METADATA = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_READINESS_REVISION_MARKER = ".private-readiness."
_GLOBAL_BINDING_CAPACITY = 1_024
_REVIEWED_REASONING_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
_LEDGER_LOCK = threading.RLock()


def _identifier(value: object, code: str) -> str:
    if type(value) is not str or _SAFE_METADATA.fullmatch(value) is None:
        raise ContextContractError(code)
    return value


def _positive(value: object, code: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContextContractError(code)
    return value


def resolve_endpoint_identity_v1(endpoint: object) -> tuple[str, str]:
    """Return one normalized HTTP(S) endpoint and its content-free identity."""

    if type(endpoint) is not str or not endpoint.strip():
        raise ContextContractError("invalid_readiness_endpoint")
    try:
        parsed = urlsplit(endpoint.strip())
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ContextContractError("invalid_readiness_endpoint") from exc
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ContextContractError("invalid_readiness_endpoint")
    host = parsed.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = host if port is None else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    normalized = f"{parsed.scheme.lower()}://{authority}{path}"
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return normalized, fingerprint


def endpoint_fingerprint_v1(endpoint: object) -> str:
    """Hash one normalized endpoint without retaining it in readiness."""

    return resolve_endpoint_identity_v1(endpoint)[1]


@dataclass(frozen=True, slots=True)
class ReadinessAssessment:
    ready: bool
    reason: str

    def __post_init__(self) -> None:
        if type(self.ready) is not bool:
            raise ContextContractError("invalid_readiness_assessment")
        _identifier(self.reason, "invalid_readiness_assessment")


@dataclass(frozen=True, slots=True)
class ReviewedPrivateRouteV1:
    """One exact reviewed LLMGate route and its exact input counter."""

    lane: Lane
    route: Route
    logical_model: str
    transport_provider: str
    endpoint_fingerprint: str
    capability: ReviewedModelCapabilityV1
    max_completion_tokens: int
    counter_revision: str
    evidence_revision: str
    evidence_provenance: tuple[str, ...]
    route_revision: str
    reasoning_effort: str

    def __post_init__(self) -> None:
        if self.lane is not Lane.PRIVATE_OWNER:
            raise ContextContractError("invalid_readiness_lane")
        if self.route is not Route.INTERACTIVE:
            raise ContextContractError("invalid_readiness_route")
        _identifier(self.logical_model, "invalid_readiness_model")
        if self.transport_provider != "llmgate":
            raise ContextContractError("invalid_readiness_provider")
        if (
            type(self.endpoint_fingerprint) is not str
            or _SHA256.fullmatch(self.endpoint_fingerprint) is None
        ):
            raise ContextContractError("invalid_readiness_endpoint")
        if type(self.capability) is not ReviewedModelCapabilityV1:
            raise ContextContractError("invalid_readiness_capability")
        if self.capability.provider != self.transport_provider:
            raise ContextContractError("invalid_readiness_provider")
        if (self.logical_model == "gpt-5.6-terra") != (
            self.capability.model == "gpt-5.6-terra"
        ):
            raise ContextContractError("invalid_readiness_model")
        _positive(self.max_completion_tokens, "invalid_readiness_output_limit")
        if self.max_completion_tokens >= self.capability.context_window_tokens:
            raise ContextContractError("invalid_readiness_output_limit")
        _identifier(self.counter_revision, "invalid_readiness_counter")
        _identifier(self.evidence_revision, "invalid_readiness_evidence")
        _identifier(self.route_revision, "invalid_readiness_route")
        if (
            type(self.evidence_provenance) is not tuple
            or not self.evidence_provenance
        ):
            raise ContextContractError("invalid_readiness_evidence")
        for item in self.evidence_provenance:
            _identifier(item, "invalid_readiness_evidence")
        if self.reasoning_effort not in _REVIEWED_REASONING_EFFORTS or (
            self.capability.model == "gpt-5.6-terra"
            and self.reasoning_effort != "low"
        ):
            raise ContextContractError("invalid_readiness_reasoning")


def _route_semantic_digest(profile: ReviewedPrivateRouteV1) -> str:
    capability = profile.capability
    semantic = (
        "nana-private-readiness-route.v1",
        profile.lane.value,
        profile.route.value,
        profile.logical_model,
        profile.transport_provider,
        profile.endpoint_fingerprint,
        capability.provider,
        capability.model,
        capability.revision,
        capability.context_window_tokens,
        capability.provider_input_limit_tokens,
        capability.max_input_characters,
        profile.max_completion_tokens,
        profile.counter_revision,
        profile.evidence_revision,
        profile.evidence_provenance,
        profile.route_revision,
        profile.reasoning_effort,
    )
    encoded = json.dumps(semantic, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _profile_attestation(profile: ReviewedPrivateRouteV1) -> bytes:
    capability = profile.capability
    live = (
        _route_semantic_digest(profile),
        id(capability),
        id(capability.count_input_tokens),
    )
    return hashlib.sha256(repr(live).encode("utf-8")).digest()


def _profile_readiness_gap(profile: object) -> str | None:
    if type(profile) is not ReviewedPrivateRouteV1:
        return "readiness_route_unavailable"
    try:
        capability = profile.capability
        if type(capability) is not ReviewedModelCapabilityV1:
            return "readiness_capacity_unavailable"
        if not callable(capability.count_input_tokens):
            return "readiness_counter_unavailable"
        capacity_values = (
            capability.context_window_tokens,
            profile.max_completion_tokens,
        )
        if any(type(value) is not int or value <= 0 for value in capacity_values):
            return "readiness_capacity_unavailable"
        if profile.max_completion_tokens >= capability.context_window_tokens:
            return "readiness_capacity_unavailable"
        for optional in (
            capability.provider_input_limit_tokens,
            capability.max_input_characters,
        ):
            if optional is not None and (type(optional) is not int or optional <= 0):
                return "readiness_capacity_unavailable"
        if (
            profile.lane is not Lane.PRIVATE_OWNER
            or profile.route is not Route.INTERACTIVE
            or profile.transport_provider != "llmgate"
            or capability.provider != "llmgate"
            or _SHA256.fullmatch(profile.endpoint_fingerprint) is None
            or _SAFE_METADATA.fullmatch(profile.logical_model) is None
            or _SAFE_METADATA.fullmatch(capability.model) is None
            or (profile.logical_model == "gpt-5.6-terra") != (
                capability.model == "gpt-5.6-terra"
            )
            or profile.reasoning_effort not in _REVIEWED_REASONING_EFFORTS
            or (
                capability.model == "gpt-5.6-terra"
                and profile.reasoning_effort != "low"
            )
        ):
            return "readiness_route_unavailable"
    except (AttributeError, TypeError):
        return "readiness_route_unavailable"
    return None


@dataclass(frozen=True, slots=True, init=False, repr=False, eq=False)
class PrivateReadinessGrant:
    _registry: "PrivateReadinessRegistry"
    _profile: ReviewedPrivateRouteV1
    _serial: int

    def __repr__(self) -> str:
        return "PrivateReadinessGrant(<opaque>)"


@dataclass(frozen=True, slots=True)
class BoundPrivateRoute:
    logical_model: str
    resolved_model: str
    transport_provider: str
    endpoint_fingerprint: str
    max_completion_tokens: int
    reasoning_effort: str
    route_revision: str


@dataclass(frozen=True, slots=True)
class _GrantRecord:
    grant: PrivateReadinessGrant
    profile_attestation: bytes
    active: bool


@dataclass(frozen=True, slots=True)
class _BindingRecord:
    compiled_reference: weakref.ReferenceType[CompiledContext]
    registry: "PrivateReadinessRegistry"
    grant: PrivateReadinessGrant
    profile_attestation: bytes
    full_context_hash: str
    compiler_config_revision: str
    planned_output_tokens: int
    policy_output_reserve: int


_BOUND_CONTEXTS: OrderedDict[int, _BindingRecord] = OrderedDict()
_ISSUED_BINDINGS: OrderedDict[
    int,
    weakref.ReferenceType[CompiledContext],
] = OrderedDict()


def _remove_expired_binding(
    object_id: int,
    reference: weakref.ReferenceType[CompiledContext],
) -> None:
    with _LEDGER_LOCK:
        issued = _ISSUED_BINDINGS.get(object_id)
        if issued is reference:
            _ISSUED_BINDINGS.pop(object_id, None)
        record = _BOUND_CONTEXTS.get(object_id)
        if record is None or record.compiled_reference is not reference:
            return
        _BOUND_CONTEXTS.pop(object_id, None)
        if record.registry._bindings.get(object_id) is record:
            record.registry._bindings.pop(object_id, None)


class PrivateReadinessRegistry:
    """Bounded trusted issuer for an explicit tuple of reviewed profiles."""

    __slots__ = (
        "_profiles",
        "_profile_attestations",
        "_grant_capacity",
        "_binding_capacity",
        "_grants",
        "_bindings",
        "_serial",
    )

    def __init__(
        self,
        profiles: tuple[ReviewedPrivateRouteV1, ...] = (),
        *,
        grant_capacity: int = 64,
        binding_capacity: int = 256,
    ) -> None:
        if type(profiles) is not tuple or any(
            type(profile) is not ReviewedPrivateRouteV1 for profile in profiles
        ):
            raise ContextContractError("invalid_readiness_registry")
        _positive(grant_capacity, "invalid_readiness_registry")
        _positive(binding_capacity, "invalid_readiness_registry")
        indexed: dict[tuple[Lane, Route, str], ReviewedPrivateRouteV1] = {}
        attestations: dict[int, bytes] = {}
        for profile in profiles:
            key = (profile.lane, profile.route, profile.logical_model)
            if key in indexed:
                raise ContextContractError("duplicate_readiness_profile")
            indexed[key] = profile
            attestations[id(profile)] = _profile_attestation(profile)
        self._profiles = indexed
        self._profile_attestations = attestations
        self._grant_capacity = grant_capacity
        self._binding_capacity = binding_capacity
        self._grants: OrderedDict[int, _GrantRecord] = OrderedDict()
        self._bindings: OrderedDict[int, _BindingRecord] = OrderedDict()
        self._serial = 0

    @property
    def profile_count(self) -> int:
        return len(self._profiles)

    def __repr__(self) -> str:
        return (
            "PrivateReadinessRegistry("
            f"profiles={len(self._profiles)}, active_grants={len(self._grants)})"
        )

    def _assess_profile_eligibility(
        self,
        *,
        logical_model: str,
        lane: Lane,
        route: Route,
    ) -> ReadinessAssessment:
        if type(logical_model) is not str or not logical_model.strip():
            return ReadinessAssessment(False, "readiness_model_invalid")
        logical_matches = tuple(
            profile
            for profile in self._profiles.values()
            if profile.logical_model == logical_model
        )
        if not logical_matches:
            return ReadinessAssessment(False, "readiness_profile_missing")
        if not any(profile.lane is lane for profile in logical_matches):
            return ReadinessAssessment(False, "readiness_lane_mismatch")
        if not any(
            profile.lane is lane and profile.route is route
            for profile in logical_matches
        ):
            return ReadinessAssessment(False, "readiness_route_mismatch")
        profile = self._profiles[(lane, route, logical_model)]
        gap = _profile_readiness_gap(profile)
        if gap is not None:
            return ReadinessAssessment(False, gap)
        expected = self._profile_attestations.get(id(profile))
        try:
            current = _profile_attestation(profile)
        except (AttributeError, TypeError, ValueError):
            return ReadinessAssessment(False, "readiness_profile_changed")
        if expected != current:
            return ReadinessAssessment(False, "readiness_profile_changed")
        return ReadinessAssessment(True, "ready")

    def assess(
        self,
        *,
        logical_model: str,
        lane: Lane,
        route: Route,
    ) -> ReadinessAssessment:
        eligibility = self._assess_profile_eligibility(
            logical_model=logical_model,
            lane=lane,
            route=route,
        )
        if not eligibility.ready:
            return eligibility
        return ReadinessAssessment(
            False,
            "readiness_profile_reviewed_adoption_required",
        )

    def issue(
        self,
        *,
        logical_model: str,
        lane: Lane,
        route: Route,
    ) -> PrivateReadinessGrant:
        assessment = self._assess_profile_eligibility(
            logical_model=logical_model,
            lane=lane,
            route=route,
        )
        if not assessment.ready:
            raise ContextContractError(assessment.reason)
        profile = self._profiles[(lane, route, logical_model)]
        with _LEDGER_LOCK:
            self._serial += 1
            grant = object.__new__(PrivateReadinessGrant)
            object.__setattr__(grant, "_registry", self)
            object.__setattr__(grant, "_profile", profile)
            object.__setattr__(grant, "_serial", self._serial)
            self._grants[id(grant)] = _GrantRecord(
                grant,
                _profile_attestation(profile),
                True,
            )
            while len(self._grants) > self._grant_capacity:
                self._grants.popitem(last=False)
        return grant

    def revoke(self, grant: object) -> None:
        if type(grant) is not PrivateReadinessGrant:
            raise ContextContractError("readiness_grant_unavailable")
        with _LEDGER_LOCK:
            record = self._grants.get(id(grant))
            if record is None or record.grant is not grant:
                raise ContextContractError("readiness_grant_unavailable")
            self._grants[id(grant)] = _GrantRecord(
                grant,
                record.profile_attestation,
                False,
            )

    def _require_grant(self, grant: PrivateReadinessGrant) -> ReviewedPrivateRouteV1:
        with _LEDGER_LOCK:
            record = self._grants.get(id(grant))
            if record is None or record.grant is not grant:
                raise ContextContractError("readiness_grant_unavailable")
            if not record.active:
                raise ContextContractError("readiness_grant_revoked")
            try:
                profile = grant._profile
                registered = self._profiles.get(
                    (profile.lane, profile.route, profile.logical_model)
                )
            except (AttributeError, TypeError):
                raise ContextContractError("readiness_grant_unavailable") from None
            try:
                current_attestation = _profile_attestation(profile)
            except (AttributeError, TypeError, ValueError):
                raise ContextContractError("readiness_profile_changed") from None
            if (
                _profile_readiness_gap(profile) is not None
                or registered is not profile
                or self._profile_attestations.get(id(profile))
                != record.profile_attestation
                or record.profile_attestation != current_attestation
            ):
                raise ContextContractError("readiness_profile_changed")
            return profile

    def _bind(
        self,
        compiled: CompiledContext,
        grant: PrivateReadinessGrant,
        profile: ReviewedPrivateRouteV1,
        *,
        planned_output_tokens: int,
        policy_output_reserve: int,
    ) -> None:
        object_id = id(compiled)
        reference = weakref.ref(
            compiled,
            lambda expired, key=object_id: _remove_expired_binding(key, expired),
        )
        record = _BindingRecord(
            compiled_reference=reference,
            registry=self,
            grant=grant,
            profile_attestation=_profile_attestation(profile),
            full_context_hash=compiled.full_context_hash,
            compiler_config_revision=compiled.manifest.compiler_config_revision,
            planned_output_tokens=planned_output_tokens,
            policy_output_reserve=policy_output_reserve,
        )
        with _LEDGER_LOCK:
            issued = _ISSUED_BINDINGS.get(object_id)
            if issued is not None:
                if issued() is compiled:
                    raise ContextContractError("readiness_binding_already_issued")
                _ISSUED_BINDINGS.pop(object_id, None)
            if len(_ISSUED_BINDINGS) >= _GLOBAL_BINDING_CAPACITY:
                for expired_id, expired in tuple(_ISSUED_BINDINGS.items()):
                    if expired() is None:
                        _ISSUED_BINDINGS.pop(expired_id, None)
            if len(_ISSUED_BINDINGS) >= _GLOBAL_BINDING_CAPACITY:
                raise ContextContractError("readiness_binding_capacity_exhausted")
            _ISSUED_BINDINGS[object_id] = reference
            self._bindings[object_id] = record
            _BOUND_CONTEXTS[object_id] = record
            while len(self._bindings) > self._binding_capacity:
                expired_id, expired = self._bindings.popitem(last=False)
                if _BOUND_CONTEXTS.get(expired_id) is expired:
                    _BOUND_CONTEXTS.pop(expired_id, None)
            while len(_BOUND_CONTEXTS) > _GLOBAL_BINDING_CAPACITY:
                expired_id, expired = _BOUND_CONTEXTS.popitem(last=False)
                if expired.registry._bindings.get(expired_id) is expired:
                    expired.registry._bindings.pop(expired_id, None)


def require_private_readiness_grant(
    grant: object,
    *,
    logical_model: str,
    lane: Lane,
    route: Route,
    requested_output_tokens: int | None = None,
    policy_output_reserve: int | None = None,
) -> ReviewedPrivateRouteV1:
    if type(grant) is not PrivateReadinessGrant:
        raise ContextContractError("readiness_grant_unavailable")
    try:
        registry = grant._registry
    except AttributeError as exc:
        raise ContextContractError("readiness_grant_unavailable") from exc
    if type(registry) is not PrivateReadinessRegistry:
        raise ContextContractError("readiness_grant_unavailable")
    profile = registry._require_grant(grant)
    if logical_model != profile.logical_model:
        raise ContextContractError("readiness_model_mismatch")
    if lane is not profile.lane:
        raise ContextContractError("readiness_lane_mismatch")
    if route is not profile.route:
        raise ContextContractError("readiness_route_mismatch")
    if requested_output_tokens is not None:
        if (
            type(requested_output_tokens) is not int
            or requested_output_tokens <= 0
            or requested_output_tokens > profile.max_completion_tokens
        ):
            raise ContextContractError("readiness_output_limit_exceeded")
    if policy_output_reserve is not None:
        if type(policy_output_reserve) is not int or policy_output_reserve <= 0:
            raise ContextContractError("invalid_output_reserve")
        if profile.max_completion_tokens < policy_output_reserve:
            raise ContextContractError("readiness_output_capacity_unavailable")
    return profile


def private_readiness_compiler_revision(
    base_revision: object,
    grant: PrivateReadinessGrant,
) -> str:
    base = _identifier(base_revision, "invalid_compiler_config_revision")
    profile = require_private_readiness_grant(
        grant,
        logical_model=grant._profile.logical_model,
        lane=grant._profile.lane,
        route=grant._profile.route,
    )
    return f"{base}{_READINESS_REVISION_MARKER}{_route_semantic_digest(profile)}"


def bind_compiled_private_readiness(
    compiled: object,
    grant: PrivateReadinessGrant,
    *,
    requested_output_tokens: int,
    policy_output_reserve: int,
) -> BoundPrivateRoute:
    if type(compiled) is not CompiledContext:
        raise ContextContractError("readiness_binding_unavailable")
    from nana.runtime.context_compiler import _require_compiled_attestation

    _require_compiled_attestation(compiled)
    profile = require_private_readiness_grant(
        grant,
        logical_model=compiled.model,
        lane=compiled.lane,
        route=compiled.route,
        requested_output_tokens=requested_output_tokens,
        policy_output_reserve=policy_output_reserve,
    )
    manifest = compiled.manifest
    expected_suffix = _READINESS_REVISION_MARKER + _route_semantic_digest(profile)
    if (
        manifest.resolved_provider != profile.transport_provider
        or manifest.resolved_model != profile.capability.model
        or not manifest.compiler_config_revision.endswith(expected_suffix)
    ):
        raise ContextContractError("readiness_binding_mismatch")
    profile_registry = grant._registry
    profile_registry._bind(
        compiled,
        grant,
        profile,
        planned_output_tokens=requested_output_tokens,
        policy_output_reserve=policy_output_reserve,
    )
    return _bound_route(profile)


def _bound_route(profile: ReviewedPrivateRouteV1) -> BoundPrivateRoute:
    return BoundPrivateRoute(
        logical_model=profile.logical_model,
        resolved_model=profile.capability.model,
        transport_provider=profile.transport_provider,
        endpoint_fingerprint=profile.endpoint_fingerprint,
        max_completion_tokens=profile.max_completion_tokens,
        reasoning_effort=profile.reasoning_effort,
        route_revision=profile.route_revision,
    )


def require_compiled_private_readiness(
    compiled: object,
    *,
    requested_output_tokens: int,
    requested_reasoning_effort: str | None = None,
) -> BoundPrivateRoute | None:
    if type(compiled) is not CompiledContext:
        raise ContextContractError("readiness_binding_unavailable")
    from nana.runtime.context_compiler import _require_compiled_attestation

    _require_compiled_attestation(compiled)
    revision = compiled.manifest.compiler_config_revision
    with _LEDGER_LOCK:
        record = _BOUND_CONTEXTS.get(id(compiled))
    marked = _READINESS_REVISION_MARKER in revision
    if record is None:
        if marked:
            raise ContextContractError("readiness_binding_unavailable")
        return None
    if not marked or record.compiled_reference() is not compiled:
        raise ContextContractError("readiness_binding_mismatch")
    with _LEDGER_LOCK:
        if record.registry._bindings.get(id(compiled)) is not record:
            raise ContextContractError("readiness_binding_unavailable")
    profile = require_private_readiness_grant(
        record.grant,
        logical_model=compiled.model,
        lane=compiled.lane,
        route=compiled.route,
        requested_output_tokens=requested_output_tokens,
        policy_output_reserve=record.policy_output_reserve,
    )
    if requested_output_tokens > record.planned_output_tokens:
        raise ContextContractError("readiness_output_binding_mismatch")
    expected_suffix = _READINESS_REVISION_MARKER + _route_semantic_digest(profile)
    if (
        record.profile_attestation != _profile_attestation(profile)
        or record.full_context_hash != compiled.full_context_hash
        or record.compiler_config_revision != revision
        or not revision.endswith(expected_suffix)
        or compiled.manifest.resolved_provider != profile.transport_provider
        or compiled.manifest.resolved_model != profile.capability.model
    ):
        raise ContextContractError("readiness_binding_mismatch")
    if (
        requested_reasoning_effort is not None
        and requested_reasoning_effort != profile.reasoning_effort
    ):
        raise ContextContractError("readiness_reasoning_override_unsupported")
    return _bound_route(profile)


DEFAULT_PRIVATE_READINESS_REGISTRY = PrivateReadinessRegistry()


__all__ = [
    "BoundPrivateRoute",
    "DEFAULT_PRIVATE_READINESS_REGISTRY",
    "PrivateReadinessGrant",
    "PrivateReadinessRegistry",
    "ReadinessAssessment",
    "ReviewedPrivateRouteV1",
    "bind_compiled_private_readiness",
    "endpoint_fingerprint_v1",
    "private_readiness_compiler_revision",
    "resolve_endpoint_identity_v1",
    "require_compiled_private_readiness",
    "require_private_readiness_grant",
]
