"""Frozen, side-effect-free contracts for Nana's future Context Runtime.

This module defines data boundaries only.  It performs no collection,
selection, compilation, telemetry, provider, or persistence work.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import hmac
import math
import re
from typing import Any, TypeAlias
import unicodedata
import weakref

from nana.runtime.public_context_boundary import PublicEventScope
from nana.runtime.public_identity import CanonicalPublicIdentity


class ContextContractError(ValueError):
    """A fail-closed Context Runtime contract violation."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Lane(str, Enum):
    PRIVATE_OWNER = "private_owner"
    PUBLIC_STAGE = "public_stage"
    OPERATOR_BACKSTAGE = "operator_backstage"


class Route(str, Enum):
    INTERACTIVE = "interactive"
    PRIVATE_FAST = "private_fast"
    YOUTUBE_CUM2 = "youtube_cum2"
    AUTONOMY = "autonomy"


class Lifetime(str, Enum):
    STATIC = "static"
    DURABLE = "durable"
    SESSION = "session"
    TURN = "turn"


class SemanticRole(str, Enum):
    INSTRUCTION = "instruction"
    IDENTITY = "identity"
    FACT = "fact"
    EVIDENCE = "evidence"
    STATE = "state"
    HISTORY = "history"


class Freshness(str, Enum):
    FRESH = "fresh"
    WARM = "warm"
    STALE = "stale"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class BudgetPolicyStatus(str, Enum):
    SHADOW_CANDIDATE = "shadow_candidate"
    APPROVED = "approved"


@dataclass(frozen=True, slots=True)
class FrozenMapping(Mapping[str, "FrozenPayload"]):
    """Tuple-backed mapping with stable key order and no mutable backing map."""

    _items: tuple[tuple[str, "FrozenPayload"], ...]

    def __post_init__(self) -> None:
        if type(self._items) is not tuple:
            raise ContextContractError("unsupported_frozen_payload")
        keys: list[str] = []
        for entry in self._items:
            if type(entry) is not tuple or len(entry) != 2 or type(entry[0]) is not str:
                raise ContextContractError("unsupported_frozen_payload")
            key, value = entry
            if unicodedata.normalize("NFC", key) != key or not _is_frozen_payload(value):
                raise ContextContractError("unsupported_frozen_payload")
            keys.append(key)
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ContextContractError("unsupported_frozen_payload")

    def __getitem__(self, key: str) -> "FrozenPayload":
        for item_key, value in self._items:
            if item_key == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        contents = ", ".join(f"{key!r}: {value!r}" for key, value in self._items)
        return f"FrozenMapping({{{contents}}})"


FrozenScalar: TypeAlias = None | bool | int | float | str
FrozenPayload: TypeAlias = (
    FrozenScalar
    | FrozenMapping
    | tuple["FrozenPayload", ...]
)


def _is_frozen_payload(value: object) -> bool:
    if value is None or type(value) in (bool, int, str):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is FrozenMapping:
        return all(_is_frozen_payload(item) for _, item in value._items)
    if type(value) is tuple:
        return all(_is_frozen_payload(item) for item in value)
    return False


def _canonical_frozen_sort_key(value: FrozenPayload) -> tuple[object, ...]:
    if value is None:
        return ("none",)
    if type(value) is bool:
        return ("bool", "1" if value else "0")
    if type(value) is int:
        return ("int", str(value))
    if type(value) is float:
        return ("float", value.hex())
    if type(value) is str:
        return ("str", value)
    if type(value) is FrozenMapping:
        return (
            "mapping",
            tuple(
                (key, _canonical_frozen_sort_key(item))
                for key, item in value._items
            ),
        )
    if type(value) is tuple:
        return ("tuple", tuple(_canonical_frozen_sort_key(item) for item in value))
    raise ContextContractError("unsupported_frozen_payload")


def _update_structural_digest(digest: Any, value: object) -> None:
    def add(tag: bytes, payload: bytes = b"") -> None:
        digest.update(tag)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)

    if value is None:
        add(b"N")
    elif type(value) is bool:
        add(b"B", b"1" if value else b"0")
    elif type(value) is int:
        add(b"I", str(value).encode("ascii"))
    elif type(value) is float:
        if not math.isfinite(value):
            raise ContextContractError("non_finite_number")
        add(b"F", value.hex().encode("ascii"))
    elif type(value) is str:
        add(b"S", value.encode("utf-8"))
    elif isinstance(value, Enum):
        add(b"E", f"{type(value).__module__}.{type(value).__qualname__}".encode("ascii"))
        _update_structural_digest(digest, value.value)
    elif type(value) is FrozenMapping:
        add(b"M", len(value._items).to_bytes(8, "big"))
        for key, item in value._items:
            _update_structural_digest(digest, key)
            _update_structural_digest(digest, item)
    elif type(value) is tuple:
        add(b"T", len(value).to_bytes(8, "big"))
        for item in value:
            _update_structural_digest(digest, item)
    else:
        raise ContextContractError("unsupported_fingerprint_value")


def _structural_digest(value: object) -> bytes:
    digest = hashlib.sha256()
    _update_structural_digest(digest, value)
    return digest.digest()


def freeze_payload(value: object) -> FrozenPayload:
    """Copy JSON-like fixture data into a recursively immutable value."""

    return _freeze_payload(value, active=set())


def _freeze_payload(value: object, *, active: set[int]) -> FrozenPayload:
    if value is None or type(value) in (bool, str):
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ContextContractError("non_finite_number")
        return value
    if type(value) is FrozenMapping:
        object_id = id(value)
        if object_id in active:
            raise ContextContractError("cyclic_frozen_payload")
        active.add(object_id)
        try:
            entries = value._items
            if type(entries) is not tuple:
                raise ContextContractError("unsupported_frozen_payload")
            normalized: dict[str, object] = {}
            for entry in entries:
                if type(entry) is not tuple or len(entry) != 2:
                    raise ContextContractError("unsupported_frozen_payload")
                key, item = entry
                if type(key) is not str:
                    raise ContextContractError("invalid_mapping_key")
                normalized_key = unicodedata.normalize("NFC", key)
                if normalized_key in normalized:
                    raise ContextContractError("duplicate_mapping_key")
                normalized[normalized_key] = item
            items = tuple(
                (key, _freeze_payload(item, active=active))
                for key, item in sorted(normalized.items(), key=lambda pair: pair[0])
            )
            return FrozenMapping(items)
        except AttributeError as exc:
            raise ContextContractError("unsupported_frozen_payload") from exc
        finally:
            active.remove(object_id)

    if type(value) is dict:
        object_id = id(value)
        if object_id in active:
            raise ContextContractError("cyclic_frozen_payload")
        active.add(object_id)
        try:
            if any(type(key) is not str for key in value):
                raise ContextContractError("invalid_mapping_key")
            normalized: dict[str, object] = {}
            for key, item in value.items():
                normalized_key = unicodedata.normalize("NFC", key)
                if normalized_key in normalized:
                    raise ContextContractError("duplicate_mapping_key")
                normalized[normalized_key] = item
            items = tuple(
                (key, _freeze_payload(item, active=active))
                for key, item in sorted(normalized.items(), key=lambda pair: pair[0])
            )
            return FrozenMapping(items)
        finally:
            active.remove(object_id)

    if type(value) in (list, tuple):
        object_id = id(value)
        if object_id in active:
            raise ContextContractError("cyclic_frozen_payload")
        active.add(object_id)
        try:
            return tuple(_freeze_payload(item, active=active) for item in value)
        finally:
            active.remove(object_id)

    if type(value) in (set, frozenset):
        object_id = id(value)
        if object_id in active:
            raise ContextContractError("cyclic_frozen_payload")
        active.add(object_id)
        try:
            frozen_items = tuple(_freeze_payload(item, active=active) for item in value)
            return tuple(sorted(frozen_items, key=_canonical_frozen_sort_key))
        finally:
            active.remove(object_id)

    raise ContextContractError("unsupported_frozen_payload")


def _require_identifier(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextContractError("invalid_id")
    return value


def _require_optional_identifier(value: object) -> str | None:
    if value is None:
        return None
    return _require_identifier(value)


def _require_text(value: object, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextContractError(code)
    return value


_COLLECTION_REASON_CODES = frozenset(
    {
        "lane_policy",
        "condition_met",
        "condition_not_met",
        "source_unavailable",
        "source_error",
        "public_lane_denied",
    }
)
_SELECTION_REASON_CODES = frozenset(
    {
        "selected",
        "lane_visibility",
        "expired",
        "not_relevant",
        "conflict_loser",
        "conflict_unresolved",
        "duplicate",
        "section_budget",
        "global_budget",
        "invalid_section",
        "required",
    }
)


def _require_metadata_reason(
    value: object,
    allowed: frozenset[str],
    code: str,
) -> str:
    if type(value) is not str or value not in allowed:
        raise ContextContractError(code)
    return value


def _require_bool(value: object, code: str) -> bool:
    if type(value) is not bool:
        raise ContextContractError(code)
    return value


def _require_nonnegative_int(value: object, code: str = "invalid_total") -> int:
    if type(value) is not int or value < 0:
        raise ContextContractError(code)
    return value


def _require_positive_int(value: object, code: str = "invalid_total") -> int:
    if type(value) is not int or value <= 0:
        raise ContextContractError(code)
    return value


def _require_timestamp(value: object, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if type(value) not in (int, float):
        raise ContextContractError("invalid_timestamp")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ContextContractError("invalid_timestamp")
    return number


def _require_enum(value: object, enum_type: type[Enum], code: str) -> None:
    if not isinstance(value, enum_type):
        raise ContextContractError(code)


def _freeze_mapping_payload(value: object) -> FrozenMapping:
    frozen = freeze_payload(value)
    if not isinstance(frozen, FrozenMapping):
        raise ContextContractError("invalid_frozen_mapping")
    return frozen


def _freeze_source_revisions(value: object) -> tuple[tuple[str, str], ...]:
    if isinstance(value, (str, bytes)):
        raise ContextContractError("invalid_source_revision")
    try:
        entries = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ContextContractError("invalid_source_revision") from exc

    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, (tuple, list)) or len(entry) != 2:
            raise ContextContractError("invalid_source_revision")
        source = _require_identifier(entry[0])
        revision = _require_identifier(entry[1])
        if source in seen:
            raise ContextContractError("duplicate_source_id")
        seen.add(source)
        result.append((source, revision))
    return tuple(result)


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ContextContractError("invalid_hash")
    return value


@dataclass(frozen=True, slots=True)
class UnresolvedContextRequest:
    request_id: str
    correlation_id: str
    route: Route
    model: str
    current_input: str
    viewer_name: str | None
    stream_mode: bool
    public_platform: str | None
    caller_metadata: FrozenPayload
    bridge_system: bool
    story_mode: bool
    casual_mode: bool
    temporal_intent: bool
    grounding_intent: bool

    def __post_init__(self) -> None:
        _require_identifier(self.request_id)
        _require_identifier(self.correlation_id)
        _require_enum(self.route, Route, "invalid_route")
        _require_identifier(self.model)
        if not isinstance(self.current_input, str):
            raise ContextContractError("invalid_current_input")
        if self.viewer_name is not None and not isinstance(self.viewer_name, str):
            raise ContextContractError("invalid_viewer_name")
        if self.public_platform is not None and not isinstance(self.public_platform, str):
            raise ContextContractError("invalid_public_platform")
        for name in (
            "stream_mode",
            "bridge_system",
            "story_mode",
            "casual_mode",
            "temporal_intent",
            "grounding_intent",
        ):
            _require_bool(getattr(self, name), f"invalid_{name}")
        object.__setattr__(
            self,
            "caller_metadata",
            _freeze_mapping_payload(self.caller_metadata),
        )


_RESOLUTION_SEAL = object()


@dataclass(frozen=True, slots=True, init=False, weakref_slot=True)
class ResolvedContextScope:
    lane: Lane
    interaction_scope: str
    public_scope: PublicEventScope | None
    policy_id: str
    resolver_version: str
    _resolution_seal: object = field(repr=False, compare=False, hash=False)

    def __init__(self) -> None:
        raise ContextContractError("invalid_resolution_seal")


@dataclass(frozen=True, slots=True, init=False, weakref_slot=True)
class ResolvedContextRequest:
    request_id: str
    correlation_id: str
    scope: ResolvedContextScope
    route: Route
    model: str
    current_input: str
    public_context: FrozenPayload
    story_mode: bool
    casual_mode: bool
    temporal_intent: bool
    grounding_intent: bool
    captured_wall_time: float
    captured_monotonic_time: float
    compiler_config_revision: str
    _resolution_seal: object = field(repr=False, compare=False, hash=False)

    def __init__(self) -> None:
        raise ContextContractError("invalid_resolution_seal")


_SCOPE_ATTESTATIONS: dict[
    int,
    tuple[weakref.ReferenceType[ResolvedContextScope], bytes],
] = {}
_REQUEST_ATTESTATIONS: dict[
    int,
    tuple[weakref.ReferenceType[ResolvedContextRequest], bytes],
] = {}


def _scope_fingerprint(scope: ResolvedContextScope) -> bytes:
    public_scope = scope.public_scope
    if public_scope is None:
        public_fields: object = None
    else:
        identity = public_scope.identity
        public_fields = (
            public_scope.platform,
            public_scope.room_id,
            public_scope.stream_session_id,
            public_scope.event_id,
            public_scope.display_name,
            identity.platform,
            identity.author_id,
            identity.actor_key,
        )
    return _structural_digest(
        (
            "resolved_scope.v1",
            scope.lane,
            scope.interaction_scope,
            public_fields,
            scope.policy_id,
            scope.resolver_version,
        )
    )


def _request_fingerprint(request: ResolvedContextRequest) -> bytes:
    return _structural_digest(
        (
            "resolved_request.v1",
            request.request_id,
            request.correlation_id,
            id(request.scope),
            request.route,
            request.model,
            request.current_input,
            request.public_context,
            request.story_mode,
            request.casual_mode,
            request.temporal_intent,
            request.grounding_intent,
            request.captured_wall_time,
            request.captured_monotonic_time,
            request.compiler_config_revision,
        )
    )


def _register_attestation(
    value: ResolvedContextScope | ResolvedContextRequest,
    registry: dict[int, tuple[weakref.ReferenceType[Any], bytes]],
    fingerprint: bytes,
) -> None:
    object_id = id(value)

    def cleanup(reference: weakref.ReferenceType[Any]) -> None:
        current = registry.get(object_id)
        if current is not None and current[0] is reference:
            registry.pop(object_id, None)

    reference = weakref.ref(value, cleanup)
    registry[object_id] = (reference, fingerprint)


def _require_attestation(
    value: ResolvedContextScope | ResolvedContextRequest,
    registry: dict[int, tuple[weakref.ReferenceType[Any], bytes]],
    fingerprint_factory: Any,
) -> None:
    entry = registry.get(id(value))
    if entry is None or entry[0]() is not value:
        raise ContextContractError("invalid_resolution_seal")
    try:
        current_fingerprint = fingerprint_factory(value)
    except (AttributeError, TypeError, ContextContractError) as exc:
        raise ContextContractError("invalid_resolution_seal") from exc
    if not hmac.compare_digest(entry[1], current_fingerprint):
        raise ContextContractError("invalid_resolution_seal")


_LANE_INTERACTION_SCOPES = {
    Lane.PRIVATE_OWNER: "private_owner",
    Lane.PUBLIC_STAGE: "public_viewer",
    Lane.OPERATOR_BACKSTAGE: "bridge_system",
}

_LANE_POLICY_PREFIXES = {
    Lane.PRIVATE_OWNER: ("private_", "private."),
    Lane.PUBLIC_STAGE: ("public_", "public."),
    Lane.OPERATOR_BACKSTAGE: ("operator_", "operator."),
}

_LANE_ROUTES = {
    Lane.PRIVATE_OWNER: frozenset(
        {Route.INTERACTIVE, Route.PRIVATE_FAST, Route.AUTONOMY}
    ),
    Lane.PUBLIC_STAGE: frozenset(
        {Route.INTERACTIVE, Route.YOUTUBE_CUM2, Route.AUTONOMY}
    ),
    Lane.OPERATOR_BACKSTAGE: frozenset({Route.INTERACTIVE}),
}

_PUBLIC_SCOPE_MAX_CHARS = 256
_PUBLIC_SCOPE_RESERVED = frozenset(
    {
        "",
        "public",
        "legacy",
        "event",
        "viewer",
        "public_chat",
        "unknown",
        "unknown-event",
    }
)
_PUBLIC_SCOPE_RESERVED_PREFIXES = (
    "derived-",
    "anonymous:",
    "fallback-",
    "synthetic-",
    "generated-",
    "request-",
)


def _require_public_scope_text(value: object, *, allow_reserved: bool = False) -> str:
    if type(value) is not str:
        raise ContextContractError("invalid_lane_route_scope")
    if (
        not value
        or value != value.strip()
        or len(value) > _PUBLIC_SCOPE_MAX_CHARS
        or any(ord(character) < 32 for character in value)
    ):
        raise ContextContractError("invalid_lane_route_scope")
    folded = value.casefold()
    if not allow_reserved and (
        folded in _PUBLIC_SCOPE_RESERVED
        or any(folded.startswith(prefix) for prefix in _PUBLIC_SCOPE_RESERVED_PREFIXES)
    ):
        raise ContextContractError("invalid_lane_route_scope")
    return value


def _require_canonical_public_scope(value: object) -> PublicEventScope:
    if type(value) is not PublicEventScope:
        raise ContextContractError("invalid_lane_route_scope")
    try:
        platform = _require_public_scope_text(value.platform)
        _require_public_scope_text(value.room_id)
        _require_public_scope_text(value.stream_session_id)
        _require_public_scope_text(value.event_id)
        _require_public_scope_text(value.display_name)
        identity = value.identity
        if type(identity) is not CanonicalPublicIdentity:
            raise ContextContractError("invalid_lane_route_scope")
        identity_platform = _require_public_scope_text(identity.platform)
        author_id = _require_public_scope_text(identity.author_id)
        actor_key = _require_public_scope_text(
            identity.actor_key,
            allow_reserved=True,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_lane_route_scope") from exc
    if identity_platform != platform or actor_key != f"{identity_platform}:{author_id}":
        raise ContextContractError("invalid_lane_route_scope")
    return value


def _validate_scope_fields(scope: ResolvedContextScope) -> None:
    if type(scope) is not ResolvedContextScope:
        raise ContextContractError("invalid_resolution_seal")
    _require_enum(scope.lane, Lane, "invalid_lane_route_scope")
    if scope.interaction_scope != _LANE_INTERACTION_SCOPES[scope.lane]:
        raise ContextContractError("invalid_lane_route_scope")
    _require_identifier(scope.policy_id)
    if not scope.policy_id.casefold().startswith(_LANE_POLICY_PREFIXES[scope.lane]):
        raise ContextContractError("invalid_lane_route_scope")
    _require_identifier(scope.resolver_version)
    if scope.lane is Lane.PUBLIC_STAGE:
        _require_canonical_public_scope(scope.public_scope)
    elif scope.public_scope is not None:
        raise ContextContractError("invalid_lane_route_scope")


def _make_resolved_context_scope(
    *,
    lane: Lane,
    interaction_scope: str,
    public_scope: PublicEventScope | None,
    policy_id: str,
    resolver_version: str,
) -> ResolvedContextScope:
    if not isinstance(lane, Lane):
        raise ContextContractError("invalid_lane_route_scope")
    scope = object.__new__(ResolvedContextScope)
    object.__setattr__(scope, "lane", lane)
    object.__setattr__(scope, "interaction_scope", interaction_scope)
    object.__setattr__(scope, "public_scope", public_scope)
    object.__setattr__(scope, "policy_id", policy_id)
    object.__setattr__(scope, "resolver_version", resolver_version)
    object.__setattr__(scope, "_resolution_seal", _RESOLUTION_SEAL)
    _validate_scope_fields(scope)
    _register_attestation(scope, _SCOPE_ATTESTATIONS, _scope_fingerprint(scope))
    return scope


def validate_lane_route_scope(scope: ResolvedContextScope, route: Route) -> None:
    if (
        type(scope) is not ResolvedContextScope
        or getattr(scope, "_resolution_seal", None) is not _RESOLUTION_SEAL
    ):
        raise ContextContractError("invalid_resolution_seal")
    _require_attestation(scope, _SCOPE_ATTESTATIONS, _scope_fingerprint)
    try:
        _validate_scope_fields(scope)
        if not isinstance(route, Route) or route not in _LANE_ROUTES[scope.lane]:
            raise ContextContractError("invalid_lane_route_scope")
        if (
            route is Route.YOUTUBE_CUM2
            and scope.public_scope is not None
            and scope.public_scope.platform.casefold() != "youtube"
        ):
            raise ContextContractError("invalid_lane_route_scope")
    except AttributeError as exc:
        raise ContextContractError("invalid_resolution_seal") from exc


def _make_resolved_context_request(
    *,
    unresolved: UnresolvedContextRequest,
    scope: ResolvedContextScope,
    public_context: object,
    captured_wall_time: float,
    captured_monotonic_time: float,
    compiler_config_revision: str,
) -> ResolvedContextRequest:
    if not isinstance(unresolved, UnresolvedContextRequest):
        raise ContextContractError("unresolved_context_request")
    if getattr(scope, "_resolution_seal", None) is not _RESOLUTION_SEAL:
        raise ContextContractError("invalid_resolution_seal")
    validate_lane_route_scope(scope, unresolved.route)
    frozen_public_context = _freeze_mapping_payload(public_context)
    wall_time = _require_timestamp(captured_wall_time)
    monotonic_time = _require_timestamp(captured_monotonic_time)
    config_revision = _require_identifier(compiler_config_revision)

    request = object.__new__(ResolvedContextRequest)
    object.__setattr__(request, "request_id", unresolved.request_id)
    object.__setattr__(request, "correlation_id", unresolved.correlation_id)
    object.__setattr__(request, "scope", scope)
    object.__setattr__(request, "route", unresolved.route)
    object.__setattr__(request, "model", unresolved.model)
    object.__setattr__(request, "current_input", unresolved.current_input)
    object.__setattr__(request, "public_context", frozen_public_context)
    object.__setattr__(request, "story_mode", unresolved.story_mode)
    object.__setattr__(request, "casual_mode", unresolved.casual_mode)
    object.__setattr__(request, "temporal_intent", unresolved.temporal_intent)
    object.__setattr__(request, "grounding_intent", unresolved.grounding_intent)
    object.__setattr__(request, "captured_wall_time", wall_time)
    object.__setattr__(request, "captured_monotonic_time", monotonic_time)
    object.__setattr__(request, "compiler_config_revision", config_revision)
    object.__setattr__(request, "_resolution_seal", _RESOLUTION_SEAL)
    _register_attestation(
        request,
        _REQUEST_ATTESTATIONS,
        _request_fingerprint(request),
    )
    return require_resolved_context_request(request)


def require_resolved_context_request(value: object) -> ResolvedContextRequest:
    if type(value) is not ResolvedContextRequest:
        raise ContextContractError("unresolved_context_request")
    if (
        getattr(value, "_resolution_seal", None) is not _RESOLUTION_SEAL
    ):
        raise ContextContractError("invalid_resolution_seal")
    _require_attestation(value, _REQUEST_ATTESTATIONS, _request_fingerprint)
    scope = getattr(value, "scope", None)
    if (
        type(scope) is not ResolvedContextScope
        or getattr(scope, "_resolution_seal", None) is not _RESOLUTION_SEAL
    ):
        raise ContextContractError("invalid_resolution_seal")
    _require_attestation(scope, _SCOPE_ATTESTATIONS, _scope_fingerprint)
    try:
        _require_identifier(value.request_id)
        _require_identifier(value.correlation_id)
        _require_enum(value.route, Route, "invalid_route")
        _require_identifier(value.model)
        if not isinstance(value.current_input, str):
            raise ContextContractError("invalid_current_input")
        if not isinstance(value.public_context, FrozenMapping):
            raise ContextContractError("invalid_frozen_mapping")
        if not _is_frozen_payload(value.public_context):
            raise ContextContractError("unsupported_frozen_payload")
        for name in (
            "story_mode",
            "casual_mode",
            "temporal_intent",
            "grounding_intent",
        ):
            _require_bool(getattr(value, name), f"invalid_{name}")
        _require_timestamp(value.captured_wall_time)
        _require_timestamp(value.captured_monotonic_time)
        _require_identifier(value.compiler_config_revision)
    except AttributeError as exc:
        raise ContextContractError("invalid_resolution_seal") from exc
    validate_lane_route_scope(scope, value.route)
    return value


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    source: str
    revision: str
    observed_at: float | None
    captured_at: float
    freshness: Freshness
    payload: FrozenPayload

    def __post_init__(self) -> None:
        _require_identifier(self.source)
        _require_identifier(self.revision)
        observed_at = _require_timestamp(self.observed_at, optional=True)
        captured_at = _require_timestamp(self.captured_at)
        if observed_at is not None and observed_at > captured_at:
            raise ContextContractError("invalid_timestamp_order")
        _require_enum(self.freshness, Freshness, "invalid_freshness")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "captured_at", captured_at)
        object.__setattr__(self, "payload", freeze_payload(self.payload))


@dataclass(frozen=True, slots=True)
class SourceRef:
    owner: str
    adapter: str
    projection: str

    def __post_init__(self) -> None:
        _require_identifier(self.owner)
        _require_identifier(self.adapter)
        _require_identifier(self.projection)


def _copy_source_ref(value: object) -> SourceRef:
    if type(value) is not SourceRef:
        raise ContextContractError("invalid_source_ref")
    try:
        return SourceRef(
            owner=value.owner,
            adapter=value.adapter,
            projection=value.projection,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_source_ref") from exc


@dataclass(frozen=True, slots=True)
class ProvenanceRef:
    source_id: str
    source_event_id: str | None
    record_id: str | None

    def __post_init__(self) -> None:
        _require_identifier(self.source_id)
        _require_optional_identifier(self.source_event_id)
        _require_optional_identifier(self.record_id)


def _copy_provenance_ref(value: object) -> ProvenanceRef:
    if type(value) is not ProvenanceRef:
        raise ContextContractError("invalid_provenance")
    try:
        return ProvenanceRef(
            source_id=value.source_id,
            source_event_id=value.source_event_id,
            record_id=value.record_id,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_provenance") from exc


@dataclass(frozen=True, slots=True)
class ContextSection:
    id: str
    lifetime: Lifetime
    semantic_role: SemanticRole
    freshness: Freshness
    visibility: frozenset[Lane]
    source: SourceRef
    revision: str
    authority: str | None
    observed_at: float | None
    expires_at: float | None
    conflict_key: str | None
    dedupe_key: str
    max_tokens: int
    payload: FrozenPayload
    formatter_version: str
    required: bool
    budget_class: str
    semantic_status: str | None
    provenance: tuple[ProvenanceRef, ...]
    relevance: float | None

    def __post_init__(self) -> None:
        _require_identifier(self.id)
        _require_enum(self.lifetime, Lifetime, "invalid_lifetime")
        _require_enum(self.semantic_role, SemanticRole, "invalid_semantic_role")
        _require_enum(self.freshness, Freshness, "invalid_freshness")

        if isinstance(self.visibility, (str, bytes)):
            raise ContextContractError("invalid_visibility")
        try:
            visibility = frozenset(self.visibility)
        except TypeError as exc:
            raise ContextContractError("invalid_visibility") from exc
        if not visibility or any(not isinstance(lane, Lane) for lane in visibility):
            raise ContextContractError("invalid_visibility")
        object.__setattr__(self, "visibility", visibility)

        object.__setattr__(self, "source", _copy_source_ref(self.source))
        _require_identifier(self.revision)
        _require_optional_identifier(self.authority)
        observed_at = _require_timestamp(self.observed_at, optional=True)
        expires_at = _require_timestamp(self.expires_at, optional=True)
        if (
            observed_at is not None
            and expires_at is not None
            and expires_at < observed_at
        ):
            raise ContextContractError("invalid_timestamp_order")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "expires_at", expires_at)
        _require_optional_identifier(self.conflict_key)
        _require_identifier(self.dedupe_key)
        _require_positive_int(self.max_tokens, "invalid_max_tokens")
        object.__setattr__(self, "payload", freeze_payload(self.payload))
        _require_identifier(self.formatter_version)
        _require_bool(self.required, "invalid_required")
        _require_identifier(self.budget_class)
        _require_optional_identifier(self.semantic_status)

        try:
            provenance = tuple(self.provenance)
        except TypeError as exc:
            raise ContextContractError("invalid_provenance") from exc
        object.__setattr__(
            self,
            "provenance",
            tuple(_copy_provenance_ref(item) for item in provenance),
        )

        if self.relevance is not None:
            if type(self.relevance) not in (int, float):
                raise ContextContractError("invalid_relevance")
            relevance = float(self.relevance)
            if not math.isfinite(relevance) or not 0.0 <= relevance <= 1.0:
                raise ContextContractError("invalid_relevance")
            object.__setattr__(self, "relevance", relevance)


def _copy_context_section(value: object) -> ContextSection:
    if type(value) is not ContextSection:
        raise ContextContractError("invalid_section")
    try:
        return ContextSection(
            id=value.id,
            lifetime=value.lifetime,
            semantic_role=value.semantic_role,
            freshness=value.freshness,
            visibility=value.visibility,
            source=value.source,
            revision=value.revision,
            authority=value.authority,
            observed_at=value.observed_at,
            expires_at=value.expires_at,
            conflict_key=value.conflict_key,
            dedupe_key=value.dedupe_key,
            max_tokens=value.max_tokens,
            payload=value.payload,
            formatter_version=value.formatter_version,
            required=value.required,
            budget_class=value.budget_class,
            semantic_status=value.semantic_status,
            provenance=value.provenance,
            relevance=value.relevance,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_section") from exc


_COLLECTION_DECISIONS = frozenset(
    {"ALLOW_READ", "CONDITIONAL_READ", "CONDITIONAL_SKIP", "DENY_NOT_READ"}
)


@dataclass(frozen=True, slots=True)
class CollectionDecision:
    source: str
    decision: str
    reason: str
    source_revision: str | None

    def __post_init__(self) -> None:
        _require_identifier(self.source)
        if self.decision not in _COLLECTION_DECISIONS:
            raise ContextContractError("invalid_collection_decision")
        _require_metadata_reason(
            self.reason,
            _COLLECTION_REASON_CODES,
            "invalid_collection_reason",
        )
        _require_optional_identifier(self.source_revision)
        if self.decision in {"ALLOW_READ", "CONDITIONAL_READ"}:
            if self.source_revision is None:
                raise ContextContractError("invalid_source_revision")
        elif self.source_revision is not None:
            raise ContextContractError("invalid_source_revision")


def _copy_collection_decision(value: object) -> CollectionDecision:
    if type(value) is not CollectionDecision:
        raise ContextContractError("invalid_collection_decision")
    try:
        return CollectionDecision(
            source=value.source,
            decision=value.decision,
            reason=value.reason,
            source_revision=value.source_revision,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_collection_decision") from exc


def _validate_collection_revisions(
    source_revisions: tuple[tuple[str, str], ...],
    receipt: tuple[CollectionDecision, ...],
) -> None:
    revisions = dict(source_revisions)
    reads = {
        decision.source: decision.source_revision
        for decision in receipt
        if decision.decision in {"ALLOW_READ", "CONDITIONAL_READ"}
    }
    if set(reads) != set(revisions):
        raise ContextContractError("invalid_source_revision")
    if any(reads[source] != revision for source, revision in revisions.items()):
        raise ContextContractError("invalid_source_revision")


def _validate_materialized_source_revisions(
    source_revisions: tuple[tuple[str, str], ...],
    rows: tuple[Any, ...],
) -> None:
    revisions = dict(source_revisions)
    try:
        if any(
            row.source.owner not in revisions
            or row.revision != revisions[row.source.owner]
            for row in rows
        ):
            raise ContextContractError("invalid_source_revision")
    except (AttributeError, KeyError) as exc:
        raise ContextContractError("invalid_source_revision") from exc


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    policy_id: str
    revision: str
    status: BudgetPolicyStatus
    max_estimated_tokens: int
    max_characters: int

    def __post_init__(self) -> None:
        _require_identifier(self.policy_id)
        _require_identifier(self.revision)
        if not isinstance(self.status, BudgetPolicyStatus):
            raise ContextContractError("invalid_budget_status")
        _require_positive_int(self.max_estimated_tokens, "invalid_budget_limit")
        _require_positive_int(self.max_characters, "invalid_budget_limit")

@dataclass(frozen=True, slots=True)
class ContextPacket:
    schema_version: int
    capture_id: str
    snapshot_revision: int
    request: ResolvedContextRequest
    source_revisions: tuple[tuple[str, str], ...]
    sections: tuple[ContextSection, ...]
    collection_receipt: tuple[CollectionDecision, ...]

    def __post_init__(self) -> None:
        _require_positive_int(self.schema_version, "invalid_schema_version")
        _require_identifier(self.capture_id)
        _require_nonnegative_int(self.snapshot_revision, "invalid_snapshot_revision")
        request = require_resolved_context_request(self.request)
        source_revisions = _freeze_source_revisions(self.source_revisions)
        object.__setattr__(self, "source_revisions", source_revisions)

        try:
            section_values = tuple(self.sections)
        except TypeError as exc:
            raise ContextContractError("invalid_section") from exc
        sections = tuple(_copy_context_section(section) for section in section_values)
        section_ids = [section.id for section in sections]
        if len(section_ids) != len(set(section_ids)):
            raise ContextContractError("duplicate_section_id")
        if any(request.scope.lane not in section.visibility for section in sections):
            raise ContextContractError("invalid_visibility")
        object.__setattr__(self, "sections", sections)

        try:
            receipt_values = tuple(self.collection_receipt)
        except TypeError as exc:
            raise ContextContractError("invalid_collection_decision") from exc
        receipt = tuple(
            _copy_collection_decision(item)
            for item in receipt_values
        )
        receipt_sources = [item.source for item in receipt]
        if len(receipt_sources) != len(set(receipt_sources)):
            raise ContextContractError("duplicate_source_id")
        object.__setattr__(self, "collection_receipt", receipt)
        _validate_collection_revisions(source_revisions, receipt)
        _validate_materialized_source_revisions(source_revisions, sections)


def _copy_context_packet(value: object) -> ContextPacket:
    if type(value) is not ContextPacket:
        raise ContextContractError("invalid_context_packet")
    try:
        return ContextPacket(
            schema_version=value.schema_version,
            capture_id=value.capture_id,
            snapshot_revision=value.snapshot_revision,
            request=value.request,
            source_revisions=value.source_revisions,
            sections=value.sections,
            collection_receipt=value.collection_receipt,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_context_packet") from exc


_SELECTION_DECISIONS = frozenset(
    {
        "included",
        "denied",
        "expired",
        "irrelevant",
        "conflict_loser",
        "conflict_unresolved",
        "duplicate",
        "section_budget",
        "global_budget",
        "invalid",
    }
)


@dataclass(frozen=True, slots=True)
class SelectionDecision:
    section_id: str
    decision: str
    reason: str
    source: SourceRef
    lifetime: Lifetime
    semantic_role: SemanticRole
    revision: str
    formatter_version: str
    budget_class: str
    chars: int | None = None
    token_estimate: int | None = None
    related_section_id: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.section_id)
        if self.decision not in _SELECTION_DECISIONS:
            raise ContextContractError("invalid_selection_decision")
        _require_metadata_reason(
            self.reason,
            _SELECTION_REASON_CODES,
            "invalid_selection_reason",
        )
        object.__setattr__(self, "source", _copy_source_ref(self.source))
        _require_enum(self.lifetime, Lifetime, "invalid_lifetime")
        _require_enum(self.semantic_role, SemanticRole, "invalid_semantic_role")
        _require_identifier(self.revision)
        _require_identifier(self.formatter_version)
        _require_identifier(self.budget_class)
        if self.chars is not None:
            _require_nonnegative_int(self.chars, "invalid_total")
        if self.token_estimate is not None:
            _require_nonnegative_int(self.token_estimate, "invalid_total")
        _require_optional_identifier(self.related_section_id)
        if self.decision in {"conflict_loser", "duplicate"}:
            if (
                self.related_section_id is None
                or self.related_section_id == self.section_id
            ):
                raise ContextContractError("invalid_related_section_id")
        elif self.related_section_id is not None:
            raise ContextContractError("invalid_related_section_id")

    @property
    def included(self) -> bool:
        return self.decision == "included"


def _copy_selection_decision(value: object) -> SelectionDecision:
    if type(value) is not SelectionDecision:
        raise ContextContractError("invalid_selection_decision")
    try:
        return SelectionDecision(
            section_id=value.section_id,
            decision=value.decision,
            reason=value.reason,
            source=value.source,
            lifetime=value.lifetime,
            semantic_role=value.semantic_role,
            revision=value.revision,
            formatter_version=value.formatter_version,
            budget_class=value.budget_class,
            chars=value.chars,
            token_estimate=value.token_estimate,
            related_section_id=value.related_section_id,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_selection_decision") from exc


@dataclass(frozen=True, slots=True)
class BudgetSheddingDecision:
    """Content-free record of one whole section or source-issued unit removal."""

    section_id: str
    unit_id: str | None
    source_index: int | None
    reason: str
    stage: int

    def __post_init__(self) -> None:
        _require_identifier(self.section_id)
        _require_optional_identifier(self.unit_id)
        if self.source_index is not None:
            _require_nonnegative_int(self.source_index, "invalid_budget_shedding")
        if self.unit_id is None and self.source_index is None:
            pass
        elif self.source_index is None:
            raise ContextContractError("invalid_budget_shedding")
        if self.reason not in {"section_budget", "global_budget"}:
            raise ContextContractError("invalid_budget_shedding")
        if type(self.stage) is not int or not 1 <= self.stage <= 5:
            raise ContextContractError("invalid_budget_shedding")


def _copy_budget_shedding_decision(value: object) -> BudgetSheddingDecision:
    if type(value) is not BudgetSheddingDecision:
        raise ContextContractError("invalid_budget_shedding")
    try:
        return BudgetSheddingDecision(
            section_id=value.section_id,
            unit_id=value.unit_id,
            source_index=value.source_index,
            reason=value.reason,
            stage=value.stage,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_budget_shedding") from exc


@dataclass(frozen=True, slots=True, weakref_slot=True)
class ResolvedContextPacket:
    packet: ContextPacket
    selected_sections: tuple[ContextSection, ...]
    decisions: tuple[SelectionDecision, ...]
    total_estimated_tokens: int
    budget_enforced: bool = False
    budget_policy_id: str | None = None
    budget_policy_revision: str | None = None
    budget_enforcement_version: str | None = None
    budget_initial_overflow: bool = False
    budget_shedding: tuple[BudgetSheddingDecision, ...] = ()

    def __post_init__(self) -> None:
        packet = _copy_context_packet(self.packet)
        object.__setattr__(self, "packet", packet)
        try:
            selected_values = tuple(self.selected_sections)
            decision_values = tuple(self.decisions)
        except TypeError as exc:
            raise ContextContractError("selection_decision_mismatch") from exc
        selected = tuple(_copy_context_section(section) for section in selected_values)
        decisions = tuple(
            _copy_selection_decision(decision)
            for decision in decision_values
        )

        candidate_by_id = {section.id: section for section in packet.sections}
        selected_ids = [section.id for section in selected]
        decision_ids = [decision.section_id for decision in decisions]
        if (
            len(selected_ids) != len(set(selected_ids))
            or len(decision_ids) != len(set(decision_ids))
            or set(decision_ids) != set(candidate_by_id)
            or any(
                section.id not in candidate_by_id
                or section != candidate_by_id[section.id]
                for section in selected
            )
        ):
            raise ContextContractError("selection_decision_mismatch")
        included_ids = {
            decision.section_id for decision in decisions if decision.included
        }
        if included_ids != set(selected_ids):
            raise ContextContractError("selection_decision_mismatch")
        for decision in decisions:
            section = candidate_by_id[decision.section_id]
            if (
                (
                    decision.related_section_id is not None
                    and decision.related_section_id not in candidate_by_id
                )
                or
                decision.source != section.source
                or decision.lifetime is not section.lifetime
                or decision.semantic_role is not section.semantic_role
                or decision.revision != section.revision
                or decision.formatter_version != section.formatter_version
                or decision.budget_class != section.budget_class
            ):
                raise ContextContractError("selection_decision_mismatch")
        _require_nonnegative_int(
            self.total_estimated_tokens,
            "invalid_total_estimated_tokens",
        )
        _require_bool(self.budget_enforced, "invalid_budget_enforced")
        _require_bool(self.budget_initial_overflow, "invalid_candidate_overflow")
        try:
            shedding_values = tuple(self.budget_shedding)
        except TypeError as exc:
            raise ContextContractError("invalid_budget_shedding") from exc
        shedding = tuple(
            _copy_budget_shedding_decision(item) for item in shedding_values
        )
        if any(item.section_id not in candidate_by_id for item in shedding):
            raise ContextContractError("invalid_budget_shedding")
        if self.budget_enforced:
            _require_identifier(self.budget_policy_id)
            _require_identifier(self.budget_policy_revision)
            if self.budget_enforcement_version != "budget-enforcement.v1":
                raise ContextContractError("invalid_budget_enforcement")
        elif (
            self.budget_policy_id is not None
            or self.budget_policy_revision is not None
            or self.budget_enforcement_version is not None
            or self.budget_initial_overflow
            or shedding
        ):
            raise ContextContractError("invalid_budget_enforcement")
        object.__setattr__(self, "selected_sections", selected)
        object.__setattr__(self, "decisions", decisions)
        object.__setattr__(self, "budget_shedding", shedding)


@dataclass(frozen=True, slots=True)
class CompiledMessage:
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"system", "user"}:
            raise ContextContractError("invalid_message_role")
        if not isinstance(self.content, str):
            raise ContextContractError("invalid_message_content")


def _copy_compiled_message(value: object) -> CompiledMessage:
    if type(value) is not CompiledMessage:
        raise ContextContractError("invalid_compiled_messages")
    try:
        return CompiledMessage(role=value.role, content=value.content)
    except AttributeError as exc:
        raise ContextContractError("invalid_compiled_messages") from exc


_PREFIX_COMPARISON_STATUSES = frozenset(
    {"no_prior", "compatible_equal", "compatible_changed", "incompatible"}
)


@dataclass(frozen=True, slots=True)
class CompilationManifest:
    request_id: str
    correlation_id: str
    capture_id: str
    snapshot_revision: int
    model: str
    resolved_model: str | None
    resolved_provider: str | None
    lane: Lane
    route: Route
    collection_policy_id: str
    budget_policy_id: str
    budget_policy_revision: str
    budget_policy_status: BudgetPolicyStatus
    budget_enforced: bool
    candidate_overflow: bool
    compiler_version: str
    compiler_config_revision: str
    wire_profile: str
    static_prefix_hash: str
    durable_prefix_hash: str
    session_prefix_hash: str
    full_context_hash: str
    prior_compatible_request_id: str | None
    prefix_break_section_id: str | None
    prefix_comparison_status: str
    source_revisions: tuple[tuple[str, str], ...]
    collection: tuple[CollectionDecision, ...]
    sections: tuple[SelectionDecision, ...]
    input_chars: int
    input_tokens_est: int
    static_prefix_chars: int
    static_prefix_tokens_est: int
    durable_prefix_chars: int
    durable_prefix_tokens_est: int
    session_prefix_chars: int
    session_prefix_tokens_est: int
    prior_exact_matching_prefix_chars: int
    prior_exact_matching_prefix_tokens_est: int
    selected_sections: int
    dropped_sections: int
    budget_enforcement_version: str | None = None
    budget_initial_overflow: bool = False
    budget_shedding: tuple[BudgetSheddingDecision, ...] = ()

    def __post_init__(self) -> None:
        for identifier in (
            self.request_id,
            self.correlation_id,
            self.capture_id,
            self.model,
            self.compiler_version,
            self.compiler_config_revision,
            self.wire_profile,
        ):
            _require_identifier(identifier)
        _require_optional_identifier(self.resolved_model)
        _require_optional_identifier(self.resolved_provider)
        _require_nonnegative_int(self.snapshot_revision, "invalid_snapshot_revision")
        _require_enum(self.lane, Lane, "invalid_lane_route_scope")
        _require_enum(self.route, Route, "invalid_lane_route_scope")
        if self.route not in _LANE_ROUTES[self.lane]:
            raise ContextContractError("invalid_lane_route_scope")
        _require_identifier(self.collection_policy_id)
        if not self.collection_policy_id.casefold().startswith(
            _LANE_POLICY_PREFIXES[self.lane]
        ):
            raise ContextContractError("invalid_lane_route_scope")
        _require_identifier(self.budget_policy_id)
        _require_identifier(self.budget_policy_revision)
        if not isinstance(self.budget_policy_status, BudgetPolicyStatus):
            raise ContextContractError("invalid_budget_status")
        _require_bool(self.budget_enforced, "invalid_budget_enforced")
        _require_bool(self.candidate_overflow, "invalid_candidate_overflow")
        _require_bool(self.budget_initial_overflow, "invalid_candidate_overflow")
        try:
            shedding_values = tuple(self.budget_shedding)
        except TypeError as exc:
            raise ContextContractError("invalid_budget_shedding") from exc
        shedding = tuple(
            _copy_budget_shedding_decision(item) for item in shedding_values
        )
        if self.budget_enforced:
            if self.budget_enforcement_version != "budget-enforcement.v1":
                raise ContextContractError("invalid_budget_enforcement")
        elif (
            self.budget_enforcement_version is not None
            or self.budget_initial_overflow
            or shedding
        ):
            raise ContextContractError("invalid_budget_enforcement")

        for digest in (
            self.static_prefix_hash,
            self.durable_prefix_hash,
            self.session_prefix_hash,
            self.full_context_hash,
        ):
            _require_sha256(digest)

        _require_optional_identifier(self.prior_compatible_request_id)
        _require_optional_identifier(self.prefix_break_section_id)
        if self.prefix_comparison_status not in _PREFIX_COMPARISON_STATUSES:
            raise ContextContractError("invalid_prefix_comparison_status")
        if self.prefix_comparison_status == "compatible_changed":
            if (
                self.prior_compatible_request_id is None
                or self.prefix_break_section_id is None
            ):
                raise ContextContractError("invalid_prefix_break_section_id")
        elif self.prefix_break_section_id is not None:
            raise ContextContractError("invalid_prefix_break_section_id")

        source_revisions = _freeze_source_revisions(self.source_revisions)
        object.__setattr__(self, "source_revisions", source_revisions)
        try:
            collection_values = tuple(self.collection)
            section_values = tuple(self.sections)
        except TypeError as exc:
            raise ContextContractError("invalid_manifest_metadata") from exc
        collection = tuple(
            _copy_collection_decision(item)
            for item in collection_values
        )
        sections = tuple(
            _copy_selection_decision(item)
            for item in section_values
        )
        if any(
            item.chars is None or item.token_estimate is None
            for item in sections
        ):
            raise ContextContractError("invalid_manifest_section")
        collection_sources = [item.source for item in collection]
        if len(collection_sources) != len(set(collection_sources)):
            raise ContextContractError("duplicate_source_id")
        section_ids = [item.section_id for item in sections]
        if len(section_ids) != len(set(section_ids)):
            raise ContextContractError("duplicate_section_id")
        section_id_set = set(section_ids)
        if any(
            item.related_section_id is not None
            and item.related_section_id not in section_id_set
            for item in sections
        ):
            raise ContextContractError("invalid_related_section_id")
        if any(item.section_id not in section_id_set for item in shedding):
            raise ContextContractError("invalid_budget_shedding")
        object.__setattr__(self, "collection", collection)
        object.__setattr__(self, "sections", sections)
        object.__setattr__(self, "budget_shedding", shedding)
        _validate_collection_revisions(source_revisions, collection)
        _validate_materialized_source_revisions(source_revisions, sections)

        total_names = (
            "input_chars",
            "input_tokens_est",
            "static_prefix_chars",
            "static_prefix_tokens_est",
            "durable_prefix_chars",
            "durable_prefix_tokens_est",
            "session_prefix_chars",
            "session_prefix_tokens_est",
            "prior_exact_matching_prefix_chars",
            "prior_exact_matching_prefix_tokens_est",
            "selected_sections",
            "dropped_sections",
        )
        for name in total_names:
            _require_nonnegative_int(getattr(self, name), "invalid_total")

        if not (
            self.static_prefix_chars
            <= self.durable_prefix_chars
            <= self.session_prefix_chars
            <= self.input_chars
            and self.static_prefix_tokens_est
            <= self.durable_prefix_tokens_est
            <= self.session_prefix_tokens_est
            <= self.input_tokens_est
        ):
            raise ContextContractError("invalid_total")
        included_count = sum(item.included for item in sections)
        if (
            self.selected_sections != included_count
            or self.dropped_sections != len(sections) - included_count
        ):
            raise ContextContractError("invalid_total")


def _copy_compilation_manifest(value: object) -> CompilationManifest:
    if type(value) is not CompilationManifest:
        raise ContextContractError("invalid_compilation_manifest")
    try:
        return CompilationManifest(
            request_id=value.request_id,
            correlation_id=value.correlation_id,
            capture_id=value.capture_id,
            snapshot_revision=value.snapshot_revision,
            model=value.model,
            resolved_model=value.resolved_model,
            resolved_provider=value.resolved_provider,
            lane=value.lane,
            route=value.route,
            collection_policy_id=value.collection_policy_id,
            budget_policy_id=value.budget_policy_id,
            budget_policy_revision=value.budget_policy_revision,
            budget_policy_status=value.budget_policy_status,
            budget_enforced=value.budget_enforced,
            candidate_overflow=value.candidate_overflow,
            compiler_version=value.compiler_version,
            compiler_config_revision=value.compiler_config_revision,
            wire_profile=value.wire_profile,
            static_prefix_hash=value.static_prefix_hash,
            durable_prefix_hash=value.durable_prefix_hash,
            session_prefix_hash=value.session_prefix_hash,
            full_context_hash=value.full_context_hash,
            prior_compatible_request_id=value.prior_compatible_request_id,
            prefix_break_section_id=value.prefix_break_section_id,
            prefix_comparison_status=value.prefix_comparison_status,
            source_revisions=value.source_revisions,
            collection=value.collection,
            sections=value.sections,
            input_chars=value.input_chars,
            input_tokens_est=value.input_tokens_est,
            static_prefix_chars=value.static_prefix_chars,
            static_prefix_tokens_est=value.static_prefix_tokens_est,
            durable_prefix_chars=value.durable_prefix_chars,
            durable_prefix_tokens_est=value.durable_prefix_tokens_est,
            session_prefix_chars=value.session_prefix_chars,
            session_prefix_tokens_est=value.session_prefix_tokens_est,
            prior_exact_matching_prefix_chars=value.prior_exact_matching_prefix_chars,
            prior_exact_matching_prefix_tokens_est=(
                value.prior_exact_matching_prefix_tokens_est
            ),
            selected_sections=value.selected_sections,
            dropped_sections=value.dropped_sections,
            budget_enforcement_version=value.budget_enforcement_version,
            budget_initial_overflow=value.budget_initial_overflow,
            budget_shedding=value.budget_shedding,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_compilation_manifest") from exc


@dataclass(frozen=True, slots=True, weakref_slot=True)
class CompiledContext:
    compiler_version: str
    lane: Lane
    route: Route
    model: str
    messages: tuple[CompiledMessage, ...]
    static_prefix_hash: str
    durable_prefix_hash: str
    session_prefix_hash: str
    full_context_hash: str
    static_prefix_chars: int
    durable_prefix_chars: int
    session_prefix_chars: int
    manifest: CompilationManifest

    def __post_init__(self) -> None:
        _require_identifier(self.compiler_version)
        _require_enum(self.lane, Lane, "invalid_lane_route_scope")
        _require_enum(self.route, Route, "invalid_lane_route_scope")
        if self.route not in _LANE_ROUTES[self.lane]:
            raise ContextContractError("invalid_lane_route_scope")
        _require_identifier(self.model)
        try:
            message_values = tuple(self.messages)
        except TypeError as exc:
            raise ContextContractError("invalid_compiled_messages") from exc
        if not message_values:
            raise ContextContractError("invalid_compiled_messages")
        messages = tuple(_copy_compiled_message(item) for item in message_values)
        object.__setattr__(self, "messages", messages)

        for digest in (
            self.static_prefix_hash,
            self.durable_prefix_hash,
            self.session_prefix_hash,
            self.full_context_hash,
        ):
            _require_sha256(digest)
        for count in (
            self.static_prefix_chars,
            self.durable_prefix_chars,
            self.session_prefix_chars,
        ):
            _require_nonnegative_int(count, "invalid_total")
        if not (
            self.static_prefix_chars
            <= self.durable_prefix_chars
            <= self.session_prefix_chars
        ):
            raise ContextContractError("invalid_total")
        manifest = _copy_compilation_manifest(self.manifest)
        object.__setattr__(self, "manifest", manifest)
        if (
            manifest.wire_profile != "legacy_system_user.v1"
            or [message.role for message in messages] != ["system", "user"]
        ):
            raise ContextContractError("invalid_wire_profile")

        expected = (
            self.compiler_version,
            self.lane,
            self.route,
            self.model,
            self.static_prefix_hash,
            self.durable_prefix_hash,
            self.session_prefix_hash,
            self.full_context_hash,
            self.static_prefix_chars,
            self.durable_prefix_chars,
            self.session_prefix_chars,
        )
        actual = (
            manifest.compiler_version,
            manifest.lane,
            manifest.route,
            manifest.model,
            manifest.static_prefix_hash,
            manifest.durable_prefix_hash,
            manifest.session_prefix_hash,
            manifest.full_context_hash,
            manifest.static_prefix_chars,
            manifest.durable_prefix_chars,
            manifest.session_prefix_chars,
        )
        if actual != expected:
            raise ContextContractError("compiled_manifest_mismatch")


__all__ = [
    "BudgetPolicy",
    "BudgetPolicyStatus",
    "BudgetSheddingDecision",
    "CollectionDecision",
    "CompilationManifest",
    "CompiledContext",
    "CompiledMessage",
    "ContextContractError",
    "ContextPacket",
    "ContextSection",
    "Freshness",
    "FrozenMapping",
    "FrozenPayload",
    "Lane",
    "Lifetime",
    "ProvenanceRef",
    "ResolvedContextPacket",
    "ResolvedContextRequest",
    "ResolvedContextScope",
    "Route",
    "SelectionDecision",
    "SemanticRole",
    "SourceRef",
    "SourceSnapshot",
    "UnresolvedContextRequest",
    "freeze_payload",
    "require_resolved_context_request",
    "validate_lane_route_scope",
]
