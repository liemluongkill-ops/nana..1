"""Pure STREAM V1 CUM 0 ingress contract.

This module validates an adapter-built public scope, authorizes a fresh live
event through an injected policy source, and keeps a bounded in-memory ledger.
It performs no file, network, process, provider, model, audio, avatar, or sink
operation. The only intentional mutation is the ledger map.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import math
import os
import re
import threading
import time
import unicodedata
from typing import TYPE_CHECKING, Any, Mapping, Protocol, runtime_checkable

from nana.runtime.public_context_boundary import PublicEventScope
from nana.runtime.public_identity import CanonicalPublicIdentity
from nana.runtime.stream_state import StreamPolicy, StreamState

if TYPE_CHECKING:
    from nana.runtime.social_session import PublicTurn


CUM0_CORRELATION_PREFIX = "sc0-"
CUM0_FINGERPRINT_VERSION = "stream-cum0-fp-v1"
CUM0_MAX_ID_CHARS = 256
CUM0_MAX_EVENT_TYPE_CHARS = 64
CUM0_MAX_SOURCE_CHARS = 64
CUM0_MAX_ADAPTER_ID_CHARS = 96
CUM0_MAX_FINGERPRINT_CHARS = 64
CUM0_MAX_TEXT_PREVIEW_CHARS = 280
CUM0_MAX_LEDGER_ENTRIES = 256
CUM0_LEDGER_TTL_SECONDS = 900.0
CUM0_MAX_EVENT_AGE_SECONDS = 30.0
CUM0_MAX_FUTURE_SKEW_SECONDS = 5.0

_UNIT_SEPARATOR = "\x1f"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_UUID_COMPACT = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
_RESERVED_EXACT = frozenset({"", "public", "legacy", "event", "public_chat", "unknown", "unknown-event"})
_RESERVED_PREFIXES = ("derived-", "anonymous:", "fallback-", "synthetic-", "generated-")
_TERMINAL_PROVIDER_EVENTS = frozenset(
    {"tombstone", "chat_ended_event", "user_banned_event", "invalid_type", "ended", "banned"}
)
_SUPPORTED_PROVIDER_EVENTS = frozenset({"text_message_event", "text"})
_ENVELOPE_SEAL = object()
_MONOTONIC_CLOCK = time.monotonic


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def is_cum0_enabled() -> bool:
    """Read the dedicated gate without importing application configuration."""

    return _env_flag("NANA_STREAM_CUM0_ENABLED", False)


def _bounded(value: Any, *, limit: int, reason: str) -> str:
    if not isinstance(value, str):
        raise ValueError(reason)
    text = value.strip()
    if not text or len(text) > limit or _UNIT_SEPARATOR in text:
        raise ValueError(reason)
    return text


def _is_reserved(value: str) -> bool:
    folded = value.casefold()
    return folded in _RESERVED_EXACT or any(folded.startswith(prefix) for prefix in _RESERVED_PREFIXES)


def _looks_like_uuid(value: str) -> bool:
    """Recognize locally generated UUID-shaped provider message identifiers."""

    candidate = value.strip()
    lowered = candidate.casefold()
    if lowered.startswith("urn:uuid:"):
        candidate = candidate[9:]
    if candidate.startswith("{") and candidate.endswith("}"):
        candidate = candidate[1:-1]
    return _UUID.fullmatch(candidate) is not None or _UUID_COMPACT.fullmatch(candidate) is not None


def _validate_scope(scope: Any) -> str | None:
    if not isinstance(scope, PublicEventScope):
        return "missing_scope"
    identity = scope.identity
    if not isinstance(identity, CanonicalPublicIdentity):
        return "missing_actor_id"
    try:
        platform = _bounded(scope.platform, limit=CUM0_MAX_ID_CHARS, reason="missing_scope")
        room_id = _bounded(scope.room_id, limit=CUM0_MAX_ID_CHARS, reason="missing_room_id")
        session_id = _bounded(scope.stream_session_id, limit=CUM0_MAX_ID_CHARS, reason="missing_session_id")
        event_id = _bounded(scope.event_id, limit=CUM0_MAX_ID_CHARS, reason="missing_provider_event_id")
        author_id = _bounded(identity.author_id, limit=CUM0_MAX_ID_CHARS, reason="missing_actor_id")
        identity_platform = _bounded(identity.platform, limit=CUM0_MAX_ID_CHARS, reason="missing_actor_id")
        actor_key = _bounded(identity.actor_key, limit=CUM0_MAX_ID_CHARS, reason="missing_actor_id")
    except ValueError as exc:
        return str(exc)

    if (
        scope.platform != platform
        or scope.room_id != room_id
        or scope.stream_session_id != session_id
        or scope.event_id != event_id
        or identity.platform != identity_platform
        or identity.author_id != author_id
        or identity.actor_key != actor_key
    ):
        return "event_identity_conflict"

    if any(_is_reserved(value) for value in (platform, room_id, session_id, event_id, author_id, identity_platform)):
        if _is_reserved(room_id):
            return "missing_room_id"
        if _is_reserved(session_id):
            return "missing_session_id"
        if _is_reserved(author_id) or _is_reserved(identity_platform):
            return "missing_actor_id"
        return "fallback_event_id"
    if _looks_like_uuid(event_id):
        return "fallback_event_id"
    if identity_platform != platform:
        return "event_identity_conflict"
    if actor_key != f"{platform}:{author_id}":
        return "event_identity_conflict"
    return None


def normalize_sanitized_text(text: Any) -> str:
    """Normalize public text before hashing; reject empty/oversized values."""

    if not isinstance(text, str):
        raise ValueError("invalid_text")
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", text).strip())
    if not normalized or len(normalized) > CUM0_MAX_TEXT_PREVIEW_CHARS:
        raise ValueError("invalid_text")
    return normalized


def _provider_event_type(value: Any) -> str:
    try:
        event_type = _bounded(value, limit=CUM0_MAX_EVENT_TYPE_CHARS, reason="unsupported_event_type")
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if event_type != value:
        raise ValueError("unsupported_event_type")
    if event_type not in _SUPPORTED_PROVIDER_EVENTS:
        if event_type in _TERMINAL_PROVIDER_EVENTS:
            raise RuntimeError("terminal_provider_event")
        raise ValueError("unsupported_event_type")
    return event_type


def _finite_epoch(value: Any, reason: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(reason)
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError(reason)
    return numeric


def _finite_monotonic(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("monotonic_clock_invalid")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise ValueError("monotonic_clock_invalid")
    return numeric


def _validate_freshness(*, received_at: Any, created_at: Any, now: Any) -> str | None:
    try:
        received = _finite_epoch(received_at, "nonfinite_timestamp")
        created = _finite_epoch(created_at, "nonfinite_timestamp")
        current = _finite_epoch(now, "nonfinite_timestamp")
    except ValueError as exc:
        return str(exc)
    if received - current > CUM0_MAX_FUTURE_SKEW_SECONDS or created - current > CUM0_MAX_FUTURE_SKEW_SECONDS:
        return "future_event"
    if current - received > CUM0_MAX_EVENT_AGE_SECONDS or current - created > CUM0_MAX_EVENT_AGE_SECONDS:
        return "stale_event"
    if created - received > CUM0_MAX_FUTURE_SKEW_SECONDS:
        return "future_event"
    return None


def _provenance_values(provenance: Any) -> tuple[str, str, str] | None:
    if isinstance(provenance, (Mapping, str, bytes, bytearray, bool)):
        return None
    try:
        raw_source = getattr(provenance, "source")
        raw_adapter_id = getattr(provenance, "adapter_id")
        raw_provider = getattr(provenance, "provider")
        source = _bounded(raw_source, limit=CUM0_MAX_SOURCE_CHARS, reason="untrusted_adapter")
        adapter_id = _bounded(raw_adapter_id, limit=CUM0_MAX_ADAPTER_ID_CHARS, reason="untrusted_adapter")
        provider = _bounded(raw_provider, limit=CUM0_MAX_SOURCE_CHARS, reason="untrusted_adapter")
        verify = getattr(provenance, "verify")
    except Exception:
        return None
    if (source, adapter_id, provider) != (raw_source, raw_adapter_id, raw_provider):
        return None
    if not callable(verify) or any(_is_reserved(value) for value in (source, adapter_id, provider)):
        return None
    return source, adapter_id, provider


def _verify_provenance(
    provenance: Any,
    scope: PublicEventScope,
    provider_event_type: str,
    provider_stream_state: str,
    offline_at: float | None,
) -> tuple[str, str, str] | None:
    values = _provenance_values(provenance)
    if values is None:
        return None
    if values[2] != scope.platform:
        return None
    try:
        verified = provenance.verify(
            scope=scope,
            provider_event_type=provider_event_type,
            provider_stream_state=provider_stream_state,
            offline_at=offline_at,
        )
    except Exception:
        return None
    return values if verified is True else None


def compute_correlation_id(scope: PublicEventScope) -> str:
    """Derive a stable correlation from canonical event identity only."""

    error = _validate_scope(scope)
    if error:
        raise ValueError(error)
    material = _UNIT_SEPARATOR.join((scope.platform, scope.room_id, scope.stream_session_id, scope.event_id))
    return CUM0_CORRELATION_PREFIX + sha256(material.encode("utf-8")).hexdigest()


def compute_content_fingerprint(
    scope: PublicEventScope,
    provider_event_type: str,
    sanitized_text: str,
    provider_created_at: float,
) -> str:
    """Compute the reconnect-stable CUM 0 content digest."""

    error = _validate_scope(scope)
    if error:
        raise ValueError(error)
    event_type = _provider_event_type(provider_event_type)
    normalized = normalize_sanitized_text(sanitized_text)
    created = _finite_epoch(provider_created_at, "nonfinite_timestamp")
    material = _UNIT_SEPARATOR.join(
        (
            CUM0_FINGERPRINT_VERSION,
            scope.platform,
            scope.room_id,
            scope.stream_session_id,
            scope.event_id,
            scope.identity.author_id,
            scope.identity.actor_key,
            event_type,
            normalized,
            f"{created:.6f}",
        )
    )
    return sha256(material.encode("utf-8")).hexdigest()


@runtime_checkable
class TrustedAdapterProvenance(Protocol):
    """Read-only attestation capability supplied by an adapter/orchestrator."""

    source: str
    adapter_id: str
    provider: str

    def verify(
        self,
        *,
        scope: PublicEventScope,
        provider_event_type: str,
        provider_stream_state: str,
        offline_at: float | None,
    ) -> bool:
        ...


class EnvelopeBuildStatus(str, Enum):
    VALIDATED = "validated"
    REJECTED = "rejected"
    HELD = "held"


class AuthorizationStatus(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    HELD = "held"


@dataclass(frozen=True)
class StreamEnvelope:
    scope: PublicEventScope
    correlation_id: str
    event_id_origin: str
    provider: str
    event_type: str
    provider_event_type: str
    source: str
    adapter_id: str
    lifecycle: str
    provider_stream_state: str
    offline_at: float | None
    ingress_attempt_id: str
    revision: int
    received_at: float
    created_at: float
    fingerprint: str
    # These fields are deliberately private and are populated only by the
    # builder after the injected provenance verifier succeeds.  They carry no
    # raw text or provider object; the seal/binding pair is an internal
    # integrity boundary for the admission path.
    _provenance_binding: tuple[str, ...] | None = field(default=None, init=False, repr=False, compare=False)
    _provenance_seal: object = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        error = _validate_scope(self.scope)
        if error:
            raise ValueError(error)
        for value, limit, reason in (
            (self.correlation_id, CUM0_MAX_ID_CHARS, "invalid_correlation"),
            (self.provider, CUM0_MAX_SOURCE_CHARS, "invalid_provider"),
            (self.provider_event_type, CUM0_MAX_EVENT_TYPE_CHARS, "unsupported_event_type"),
            (self.source, CUM0_MAX_SOURCE_CHARS, "untrusted_adapter"),
            (self.adapter_id, CUM0_MAX_ADAPTER_ID_CHARS, "untrusted_adapter"),
            (self.ingress_attempt_id, CUM0_MAX_ID_CHARS, "invalid_attempt"),
        ):
            _bounded(value, limit=limit, reason=reason)
        if self.event_id_origin != "provider":
            raise ValueError("fallback_event_id")
        if self.event_type != "text" or self.lifecycle != "received":
            raise ValueError("invalid_lifecycle")
        if self.provider_stream_state != "live" or self.offline_at is not None:
            raise ValueError("provider_offline")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("invalid_revision")
        _finite_epoch(self.received_at, "nonfinite_timestamp")
        _finite_epoch(self.created_at, "nonfinite_timestamp")
        if not _HEX64.fullmatch(self.fingerprint):
            raise ValueError("fingerprint_compute_error")
        if self.correlation_id != compute_correlation_id(self.scope):
            raise ValueError("invalid_correlation")

    @property
    def provider_event_id(self) -> str:
        return self.scope.event_id

    @property
    def actor_id(self) -> str:
        return self.scope.identity.author_id

    @property
    def actor_key(self) -> str:
        return self.scope.identity.actor_key

    @property
    def platform(self) -> str:
        return self.scope.platform

    @property
    def room_id(self) -> str:
        return self.scope.room_id

    @property
    def stream_session_id(self) -> str:
        return self.scope.stream_session_id

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.platform, self.room_id, self.stream_session_id, self.provider_event_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "provider_event_id": self.provider_event_id,
            "event_id_origin": self.event_id_origin,
            "actor_id": self.actor_id,
            "actor_key": self.actor_key,
            "platform": self.platform,
            "provider": self.provider,
            "room_id": self.room_id,
            "stream_session_id": self.stream_session_id,
            "event_type": self.event_type,
            "provider_event_type": self.provider_event_type,
            "source": self.source,
            "adapter_id": self.adapter_id,
            "lifecycle": self.lifecycle,
            "provider_stream_state": self.provider_stream_state,
            "offline_at": self.offline_at,
            "ingress_attempt_id": self.ingress_attempt_id,
            "revision": self.revision,
            "received_at": self.received_at,
            "created_at": self.created_at,
            "fingerprint": self.fingerprint,
        }


def _make_provenance_binding(
    *,
    scope: PublicEventScope,
    source: str,
    adapter_id: str,
    provider: str,
    provider_event_type: str,
    ingress_attempt_id: str,
    revision: int,
    received_at: float,
    created_at: float,
    fingerprint: str,
) -> tuple[str, ...]:
    """Create the immutable metadata binding issued with a built envelope."""

    return (
        scope.platform,
        scope.room_id,
        scope.stream_session_id,
        scope.event_id,
        scope.identity.platform,
        scope.identity.author_id,
        scope.identity.actor_key,
        source,
        adapter_id,
        provider,
        provider_event_type,
        "text",
        "received",
        "live",
        ingress_attempt_id,
        str(revision),
        f"{received_at:.17g}",
        f"{created_at:.17g}",
        fingerprint,
    )


def _validate_envelope_integrity(envelope: Any, *, check_received_binding: bool = False) -> str | None:
    """Validate builder-issued projections before any policy is consulted."""

    if not isinstance(envelope, StreamEnvelope):
        return "envelope_invalid"
    scope_error = _validate_scope(envelope.scope)
    if scope_error:
        return scope_error
    binding = getattr(envelope, "_provenance_binding", None)
    if getattr(envelope, "_provenance_seal", None) is not _ENVELOPE_SEAL:
        return "untrusted_adapter"
    if not isinstance(binding, tuple) or len(binding) != 19 or not all(isinstance(item, str) for item in binding):
        return "untrusted_adapter"

    scope_projection = (
        envelope.scope.platform,
        envelope.scope.room_id,
        envelope.scope.stream_session_id,
        envelope.scope.event_id,
        envelope.scope.identity.platform,
        envelope.scope.identity.author_id,
        envelope.scope.identity.actor_key,
    )
    if scope_projection != binding[:7]:
        return "untrusted_adapter"
    if (envelope.source, envelope.adapter_id, envelope.provider) != binding[7:10]:
        return "untrusted_adapter"

    if envelope.event_id_origin != "provider":
        return "fallback_event_id"
    try:
        _bounded(envelope.provider, limit=CUM0_MAX_SOURCE_CHARS, reason="untrusted_adapter")
        _bounded(envelope.source, limit=CUM0_MAX_SOURCE_CHARS, reason="untrusted_adapter")
        _bounded(envelope.adapter_id, limit=CUM0_MAX_ADAPTER_ID_CHARS, reason="untrusted_adapter")
        _bounded(envelope.ingress_attempt_id, limit=CUM0_MAX_ID_CHARS, reason="invalid_attempt")
    except ValueError as exc:
        return str(exc)
    if envelope.event_type != "text":
        return "unsupported_event_type"
    if envelope.lifecycle != "received":
        return "envelope_invalid"
    try:
        canonical_provider_type = _provider_event_type(envelope.provider_event_type)
    except RuntimeError:
        return "provider_offline"
    except ValueError:
        return "unsupported_event_type"
    if canonical_provider_type != envelope.provider_event_type:
        return "unsupported_event_type"
    if envelope.provider_event_type != binding[10]:
        return "content_mismatch"
    if envelope.event_type != binding[11] or envelope.lifecycle != binding[12]:
        return "envelope_invalid"
    if envelope.provider_stream_state != binding[13] or envelope.provider_stream_state != "live":
        return "provider_offline"
    if envelope.offline_at is not None:
        try:
            _finite_epoch(envelope.offline_at, "nonfinite_timestamp")
        except ValueError as exc:
            return str(exc)
        return "provider_offline"
    if type(envelope.revision) is not int or envelope.revision < 0:
        return "invalid_revision"
    if envelope.ingress_attempt_id != binding[14]:
        return "invalid_attempt"
    if str(envelope.revision) != binding[15]:
        return "invalid_revision"
    try:
        _finite_epoch(envelope.received_at, "nonfinite_timestamp")
        created = _finite_epoch(envelope.created_at, "nonfinite_timestamp")
    except ValueError as exc:
        return str(exc)
    if check_received_binding and f"{envelope.received_at:.17g}" != binding[16]:
        return "content_mismatch"
    if f"{created:.17g}" != binding[17]:
        return "content_mismatch"
    if not _HEX64.fullmatch(envelope.fingerprint) or envelope.fingerprint != binding[18]:
        return "content_mismatch"
    try:
        expected_correlation = compute_correlation_id(envelope.scope)
    except ValueError as exc:
        return str(exc)
    if envelope.correlation_id != expected_correlation:
        return "invalid_correlation"
    return None


@dataclass(frozen=True)
class EnvelopeBuildResult:
    status: EnvelopeBuildStatus
    reason_code: str
    envelope: StreamEnvelope | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason_code": self.reason_code,
            "envelope": self.envelope.to_dict() if self.envelope else None,
        }


def _build_result(status: EnvelopeBuildStatus, reason: str) -> EnvelopeBuildResult:
    return EnvelopeBuildResult(status, reason, None)


def build_stream_envelope(
    scope: PublicEventScope,
    provenance: TrustedAdapterProvenance,
    sanitized_text: str,
    provider_event_type: str,
    provider_stream_state: str,
    offline_at: float | None,
    ingress_attempt_id: str,
    revision: int,
    received_at: float,
    created_at: float,
    now: float,
) -> EnvelopeBuildResult:
    """Validate one trusted adapter event and compute its internal digest."""

    scope_error = _validate_scope(scope)
    if scope_error:
        return _build_result(EnvelopeBuildStatus.REJECTED, scope_error)
    try:
        normalized = normalize_sanitized_text(sanitized_text)
    except ValueError as exc:
        return _build_result(EnvelopeBuildStatus.REJECTED, str(exc))
    try:
        provider_type = _provider_event_type(provider_event_type)
    except RuntimeError as exc:
        return _build_result(EnvelopeBuildStatus.HELD, str(exc))
    except ValueError as exc:
        return _build_result(EnvelopeBuildStatus.REJECTED, str(exc))

    if not isinstance(provider_stream_state, str):
        return _build_result(EnvelopeBuildStatus.REJECTED, "provider_offline")
    stream_state = provider_stream_state
    if stream_state in {"offline", "timeout", "ended", "chat_ended"}:
        return _build_result(EnvelopeBuildStatus.HELD, "provider_offline")
    if stream_state != "live":
        return _build_result(EnvelopeBuildStatus.HELD, "provider_offline")
    if offline_at is not None:
        try:
            _finite_epoch(offline_at, "nonfinite_timestamp")
        except ValueError as exc:
            return _build_result(EnvelopeBuildStatus.REJECTED, str(exc))
        return _build_result(EnvelopeBuildStatus.HELD, "provider_offline")
    try:
        attempt = _bounded(ingress_attempt_id, limit=CUM0_MAX_ID_CHARS, reason="invalid_attempt")
    except ValueError as exc:
        return _build_result(EnvelopeBuildStatus.REJECTED, str(exc))
    if attempt != ingress_attempt_id:
        return _build_result(EnvelopeBuildStatus.REJECTED, "invalid_attempt")
    if type(revision) is not int or revision < 0:
        return _build_result(EnvelopeBuildStatus.REJECTED, "invalid_revision")
    freshness_error = _validate_freshness(received_at=received_at, created_at=created_at, now=now)
    if freshness_error:
        return _build_result(
            EnvelopeBuildStatus.HELD if freshness_error in {"stale_event", "future_event"} else EnvelopeBuildStatus.REJECTED,
            freshness_error,
        )
    provenance_values = _verify_provenance(provenance, scope, provider_type, "live", None)
    if provenance_values is None:
        return _build_result(EnvelopeBuildStatus.REJECTED, "untrusted_adapter")
    source, adapter_id, provider = provenance_values
    try:
        received = _finite_epoch(received_at, "nonfinite_timestamp")
        created = _finite_epoch(created_at, "nonfinite_timestamp")
        fingerprint = compute_content_fingerprint(scope, provider_type, normalized, created)
        envelope = StreamEnvelope(
            scope=scope,
            correlation_id=compute_correlation_id(scope),
            event_id_origin="provider",
            provider=provider,
            event_type="text",
            provider_event_type=provider_type,
            source=source,
            adapter_id=adapter_id,
            lifecycle="received",
            provider_stream_state="live",
            offline_at=None,
            ingress_attempt_id=attempt,
            revision=revision,
            received_at=received,
            created_at=created,
            fingerprint=fingerprint,
        )
        object.__setattr__(
            envelope,
            "_provenance_binding",
            _make_provenance_binding(
                scope=scope,
                source=source,
                adapter_id=adapter_id,
                provider=provider,
                provider_event_type=provider_type,
                ingress_attempt_id=attempt,
                revision=revision,
                received_at=received,
                created_at=created,
                fingerprint=fingerprint,
            ),
        )
        object.__setattr__(envelope, "_provenance_seal", _ENVELOPE_SEAL)
    except ValueError as exc:
        return _build_result(EnvelopeBuildStatus.REJECTED, str(exc))
    return EnvelopeBuildResult(EnvelopeBuildStatus.VALIDATED, "validated", envelope)


@runtime_checkable
class ReadOnlyPolicySource(Protocol):
    def get_policy(self) -> StreamPolicy:
        ...


@dataclass(frozen=True)
class AuthorizationDecision:
    status: AuthorizationStatus
    reason_code: str
    policy_state: StreamState | None
    policy_snapshot_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason_code": self.reason_code,
            "policy_state": self.policy_state.value if self.policy_state else None,
            "policy_snapshot_id": self.policy_snapshot_id,
        }


def _deny(reason: str, *, held: bool = False, state: StreamState | None = None) -> AuthorizationDecision:
    return AuthorizationDecision(
        AuthorizationStatus.HELD if held else AuthorizationStatus.DENIED,
        reason,
        state,
        None,
    )


def authorize_ingress(
    envelope: StreamEnvelope,
    policy_source: ReadOnlyPolicySource,
    active_session_id: str | None,
    now: float,
) -> AuthorizationDecision:
    """Authorize only a fresh live event from an injected policy source."""

    if not is_cum0_enabled():
        return _deny("cum0_disabled")
    integrity_error = _validate_envelope_integrity(envelope)
    if integrity_error:
        return _deny(integrity_error)
    try:
        active = _bounded(active_session_id, limit=CUM0_MAX_ID_CHARS, reason="active_session_missing")
    except ValueError:
        return _deny("active_session_missing")
    if active != active_session_id:
        return _deny("session_mismatch")
    if active != envelope.stream_session_id:
        return _deny("session_mismatch")
    freshness_error = _validate_freshness(
        received_at=envelope.received_at,
        created_at=envelope.created_at,
        now=now,
    )
    if freshness_error:
        return _deny(freshness_error, held=freshness_error in {"stale_event", "future_event"})
    received_binding_error = _validate_envelope_integrity(envelope, check_received_binding=True)
    if received_binding_error:
        return _deny(received_binding_error)
    if not isinstance(policy_source, ReadOnlyPolicySource):
        return _deny("policy_source_invalid")
    try:
        policy = policy_source.get_policy()
    except Exception:
        return _deny("policy_read_error")
    if not isinstance(policy, StreamPolicy) or not isinstance(policy.state, StreamState):
        return _deny("policy_source_invalid")
    if policy.state not in {StreamState.LIVE_IDLE, StreamState.LIVE_ACTIVE}:
        return _deny("state_not_live", state=policy.state)
    if policy.can_reply is not True:
        return _deny("policy_reply_not_allowed", state=policy.state)
    return AuthorizationDecision(AuthorizationStatus.ALLOWED, "authorized", policy.state, None)


@dataclass(frozen=True)
class _AuthorizedIngress:
    envelope: StreamEnvelope
    envelope_key: tuple[str, str, str, str]
    fingerprint: str
    ingress_attempt_id: str
    revision: int
    policy_state: StreamState
    active_session_id: str
    issued_at: float
    _seal: object


_GRANT_SEAL = object()


@dataclass(frozen=True)
class _GrantBinding:
    grant: _AuthorizedIngress
    envelope: StreamEnvelope
    envelope_key: tuple[str, str, str, str]
    fingerprint: str
    ingress_attempt_id: str
    revision: int
    policy_state: StreamState
    active_session_id: str
    issued_at: float


_GRANT_REGISTRY: dict[int, _GrantBinding] = {}
_GRANT_REGISTRY_LOCK = threading.RLock()


def _grant_is_live(grant: Any) -> bool:
    with _GRANT_REGISTRY_LOCK:
        binding = _GRANT_REGISTRY.get(id(grant))
        return (
            isinstance(grant, _AuthorizedIngress)
            and grant._seal is _GRANT_SEAL
            and isinstance(binding, _GrantBinding)
            and binding.grant is grant
        )


def _grant_is_consistent(grant: _AuthorizedIngress) -> bool:
    """Ensure the private grant still binds every admission input."""

    with _GRANT_REGISTRY_LOCK:
        binding = _GRANT_REGISTRY.get(id(grant))
        try:
            if (
                not isinstance(grant, _AuthorizedIngress)
                or grant._seal is not _GRANT_SEAL
                or not isinstance(binding, _GrantBinding)
                or binding.grant is not grant
                or binding.envelope is not grant.envelope
            ):
                return False
            if (
                binding.envelope_key != grant.envelope_key
                or binding.fingerprint != grant.fingerprint
                or binding.ingress_attempt_id != grant.ingress_attempt_id
                or binding.revision != grant.revision
                or binding.policy_state is not grant.policy_state
                or binding.active_session_id != grant.active_session_id
                or binding.issued_at != grant.issued_at
            ):
                return False
            if not isinstance(grant.envelope, StreamEnvelope):
                return False
            if _validate_envelope_integrity(grant.envelope, check_received_binding=True) is not None:
                return False
            if grant.envelope_key != grant.envelope.key:
                return False
            if grant.fingerprint != grant.envelope.fingerprint:
                return False
            if grant.ingress_attempt_id != grant.envelope.ingress_attempt_id:
                return False
            if grant.revision != grant.envelope.revision:
                return False
            if grant.policy_state not in {StreamState.LIVE_IDLE, StreamState.LIVE_ACTIVE}:
                return False
            if grant.active_session_id != grant.envelope.stream_session_id:
                return False
            _finite_epoch(grant.issued_at, "nonfinite_timestamp")
        except (AttributeError, TypeError, ValueError):
            return False
        return True


@dataclass(frozen=True)
class AdmissionResult:
    status: str
    reason_code: str
    envelope_key: tuple[str, str, str, str] | None
    revision: int | None
    idempotent: bool
    public_turn: "PublicTurn | None" = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "envelope_key": self.envelope_key,
            "revision": self.revision,
            "idempotent": self.idempotent,
            "public_turn": self.public_turn.to_dict() if self.public_turn is not None else None,
        }


@dataclass(frozen=True)
class _LedgerEntry:
    fingerprint: str
    ingress_attempt_id: str
    revision: int
    admitted_at: float


def _project_public_turn(
    grant: _AuthorizedIngress,
    sanitized_text: str,
    monotonic_value: float,
) -> "PublicTurn | None":
    """Create a session-only turn only from a live module-issued grant."""

    if not _grant_is_consistent(grant):
        return None
    try:
        monotonic_timestamp = _finite_monotonic(monotonic_value)
        normalized = normalize_sanitized_text(sanitized_text)
        expected = compute_content_fingerprint(
            grant.envelope.scope,
            grant.envelope.provider_event_type,
            normalized,
            grant.envelope.created_at,
        )
    except ValueError:
        return None
    if expected != grant.fingerprint or expected != grant.envelope.fingerprint:
        return None
    from nana.runtime.social_session import PublicTurn

    return PublicTurn(
        timestamp=grant.envelope.received_at,
        monotonic=monotonic_timestamp,
        viewer_name=grant.envelope.scope.display_name,
        event_type="text",
        director_mode="chill",
        message_preview=normalized[:96],
        scope=grant.envelope.scope,
        revision=grant.envelope.revision,
        attempt_id=grant.ingress_attempt_id,
    )


class StreamContractLedger:
    """Bounded process-local admission ledger with no persistence."""

    def __init__(
        self,
        *,
        max_entries: int = CUM0_MAX_LEDGER_ENTRIES,
        ttl_seconds: float = CUM0_LEDGER_TTL_SECONDS,
    ) -> None:
        if type(max_entries) is not int or not 1 <= max_entries <= CUM0_MAX_LEDGER_ENTRIES:
            raise ValueError("invalid_ledger_limit")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):
            raise ValueError("invalid_ledger_ttl")
        ttl = float(ttl_seconds)
        if not math.isfinite(ttl) or ttl <= 0 or ttl > CUM0_LEDGER_TTL_SECONDS:
            raise ValueError("invalid_ledger_ttl")
        self.max_entries = max_entries
        self.ttl_seconds = ttl
        self._entries: dict[tuple[str, str, str, str], _LedgerEntry] = {}
        self._lock = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl_seconds,
                "read_only": True,
                "memory_write": False,
                "output_write": False,
            }

    def purge(self, now: float) -> int:
        current = _finite_epoch(now, "nonfinite_timestamp")
        with self._lock:
            expired = [
                key for key, entry in self._entries.items()
                if current - entry.admitted_at > self.ttl_seconds
            ]
            for key in expired:
                self._entries.pop(key, None)
            return len(expired)

    def authorize_and_accept(
        self,
        envelope: StreamEnvelope,
        sanitized_text: str,
        policy_source: ReadOnlyPolicySource,
        active_session_id: str | None,
        now: float,
    ) -> AdmissionResult:
        """Authorize, seal, and admit one event through the sole public path."""

        key = None
        revision = None
        if isinstance(envelope, StreamEnvelope):
            try:
                key = envelope.key
                revision = envelope.revision
            except Exception:
                key = None
                revision = None
        if not is_cum0_enabled():
            return AdmissionResult("rejected", "cum0_disabled", key, revision, False)
        if not isinstance(envelope, StreamEnvelope):
            return AdmissionResult("rejected", "envelope_invalid", key, revision, False)
        integrity_error = _validate_envelope_integrity(envelope)
        if integrity_error:
            return AdmissionResult("rejected", integrity_error, key, revision, False)
        try:
            normalized = normalize_sanitized_text(sanitized_text)
            recomputed = compute_content_fingerprint(
                envelope.scope,
                envelope.provider_event_type,
                normalized,
                envelope.created_at,
            )
        except ValueError as exc:
            return AdmissionResult("rejected", str(exc), key, revision, False)
        if recomputed != envelope.fingerprint:
            return AdmissionResult("rejected", "content_mismatch", key, revision, False)
        decision = authorize_ingress(envelope, policy_source, active_session_id, now)
        if decision.status is not AuthorizationStatus.ALLOWED:
            status = "held" if decision.status is AuthorizationStatus.HELD else "rejected"
            return AdmissionResult(status, decision.reason_code, key, revision, False)
        try:
            issued = _finite_epoch(now, "nonfinite_timestamp")
            active = _bounded(active_session_id, limit=CUM0_MAX_ID_CHARS, reason="active_session_missing")
        except ValueError as exc:
            return AdmissionResult("rejected", str(exc), key, revision, False)
        grant = _AuthorizedIngress(
            envelope=envelope,
            envelope_key=envelope.key,
            fingerprint=recomputed,
            ingress_attempt_id=envelope.ingress_attempt_id,
            revision=envelope.revision,
            policy_state=decision.policy_state,
            active_session_id=active,
            issued_at=issued,
            _seal=_GRANT_SEAL,
        )
        with self._lock:
            self.purge(issued)
            previous_entry = self._entries.get(grant.envelope_key)
            with _GRANT_REGISTRY_LOCK:
                _GRANT_REGISTRY[id(grant)] = _GrantBinding(
                    grant=grant,
                    envelope=envelope,
                    envelope_key=grant.envelope_key,
                    fingerprint=grant.fingerprint,
                    ingress_attempt_id=grant.ingress_attempt_id,
                    revision=grant.revision,
                    policy_state=grant.policy_state,
                    active_session_id=grant.active_session_id,
                    issued_at=grant.issued_at,
                )

            def restore_previous_entry() -> None:
                if previous_entry is None:
                    self._entries.pop(grant.envelope_key, None)
                else:
                    self._entries[grant.envelope_key] = previous_entry

            try:
                admitted = self._accept_authorized(grant, now=issued, active_session_id=active)
                if admitted.status != "accepted":
                    return admitted
                try:
                    monotonic_value = _finite_monotonic(_MONOTONIC_CLOCK())
                except Exception:
                    restore_previous_entry()
                    return AdmissionResult(
                        "rejected", "monotonic_clock_invalid", key, revision, False
                    )
                try:
                    turn = _project_public_turn(grant, normalized, monotonic_value)
                except Exception:
                    turn = None
                if turn is None:
                    restore_previous_entry()
                    return AdmissionResult("rejected", "projection_failed", key, revision, False)
                return AdmissionResult(
                    admitted.status,
                    admitted.reason_code,
                    admitted.envelope_key,
                    admitted.revision,
                    admitted.idempotent,
                    turn,
                )
            finally:
                with _GRANT_REGISTRY_LOCK:
                    _GRANT_REGISTRY.pop(id(grant), None)

    def _accept_authorized(
        self,
        grant: Any,
        *,
        now: float,
        active_session_id: str,
    ) -> AdmissionResult:
        if not _grant_is_consistent(grant):
            return AdmissionResult("rejected", "unauthorized", None, None, False)
        if active_session_id != grant.active_session_id:
            return AdmissionResult("rejected", "session_mismatch", grant.envelope_key, grant.revision, False)
        try:
            current = _finite_epoch(now, "nonfinite_timestamp")
        except ValueError as exc:
            return AdmissionResult("rejected", str(exc), grant.envelope_key, grant.revision, False)
        if current != grant.issued_at:
            return AdmissionResult("rejected", "unauthorized", None, None, False)
        with self._lock:
            self.purge(current)
            key = grant.envelope_key
            existing = self._entries.get(key)
            if existing is not None:
                if existing.fingerprint != grant.fingerprint:
                    return AdmissionResult("rejected", "event_conflict", key, grant.revision, False)
                if grant.revision < existing.revision:
                    return AdmissionResult("rejected", "stale_revision", key, existing.revision, False)
                if grant.revision == existing.revision:
                    if grant.ingress_attempt_id == existing.ingress_attempt_id:
                        return AdmissionResult("duplicate", "idempotent_duplicate", key, existing.revision, True)
                    return AdmissionResult("rejected", "attempt_reuse", key, existing.revision, False)
                if grant.ingress_attempt_id == existing.ingress_attempt_id:
                    return AdmissionResult("rejected", "attempt_reuse", key, existing.revision, False)
            elif len(self._entries) >= self.max_entries:
                return AdmissionResult("rejected", "ledger_full", key, grant.revision, False)
            self._entries[key] = _LedgerEntry(
                fingerprint=grant.fingerprint,
                ingress_attempt_id=grant.ingress_attempt_id,
                revision=grant.revision,
                admitted_at=current,
            )
            return AdmissionResult("accepted", "admitted", key, grant.revision, False)


__all__ = [
    "AdmissionResult",
    "AuthorizationDecision",
    "AuthorizationStatus",
    "CUM0_CORRELATION_PREFIX",
    "CUM0_FINGERPRINT_VERSION",
    "CUM0_MAX_ADAPTER_ID_CHARS",
    "CUM0_LEDGER_TTL_SECONDS",
    "CUM0_MAX_EVENT_AGE_SECONDS",
    "CUM0_MAX_FUTURE_SKEW_SECONDS",
    "CUM0_MAX_LEDGER_ENTRIES",
    "CUM0_MAX_EVENT_TYPE_CHARS",
    "CUM0_MAX_FINGERPRINT_CHARS",
    "CUM0_MAX_ID_CHARS",
    "CUM0_MAX_SOURCE_CHARS",
    "CUM0_MAX_TEXT_PREVIEW_CHARS",
    "EnvelopeBuildResult",
    "EnvelopeBuildStatus",
    "ReadOnlyPolicySource",
    "StreamContractLedger",
    "StreamEnvelope",
    "TrustedAdapterProvenance",
    "authorize_ingress",
    "build_stream_envelope",
    "compute_content_fingerprint",
    "compute_correlation_id",
    "is_cum0_enabled",
    "normalize_sanitized_text",
]
