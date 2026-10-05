"""Content-free prefix comparison and telemetry receipts for Context Runtime."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
import hashlib
import math
import re
from threading import Lock
from typing import Any, Protocol

from nana.runtime.context_compiler import (
    WIRE_PROFILE,
    _canonical_system_prefix_projection,
    _canonical_user_content_prefix,
    _require_compiled_attestation,
    canonical_message_projection,
    estimate_tokens_v1,
    registry_slot_ids,
    virtual_user_slot_id,
)
from nana.runtime.context_contracts import (
    CompilationManifest,
    CompiledContext,
    CompiledMessage,
    ContextContractError,
    Lane,
    Lifetime,
    Route,
    SelectionDecision,
)


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_COMPARISON_STATUSES = frozenset(
    {"no_prior", "compatible_equal", "compatible_changed", "incompatible"}
)
_TELEMETRY_LABELS = frozenset({"candidate_context", "sent_context"})
_SAFE_METADATA_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,127}")
_TRANSPORT_STATUSES = frozenset(
    {"complete", "complete_with_invalid_events", "failed", "cancelled"}
)
_USAGE_STATUSES = frozenset({"missing", "valid", "invalid", "unsupported"})
_PREFIX_COMPARISON_ATTESTATION_CAPACITY = 4_096
_PREFIX_COMPARISON_ATTESTATIONS: OrderedDict[int, bytes] = OrderedDict()
_PREFIX_COMPARISON_ATTESTATION_LOCK = Lock()
_SENT_ROUTES = {
    Lane.PRIVATE_OWNER: frozenset(
        {Route.INTERACTIVE, Route.PRIVATE_FAST, Route.AUTONOMY}
    ),
    Lane.PUBLIC_STAGE: frozenset(
        {Route.INTERACTIVE, Route.YOUTUBE_CUM2, Route.AUTONOMY}
    ),
    Lane.OPERATOR_BACKSTAGE: frozenset({Route.INTERACTIVE}),
}


def _require_identifier(value: object, code: str) -> str:
    if type(value) is not str or not value.strip():
        raise ContextContractError(code)
    return value


def _require_optional_identifier(value: object, code: str) -> str | None:
    if value is None:
        return None
    return _require_identifier(value, code)


def _require_sha256(value: object, code: str = "invalid_hash") -> str:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        raise ContextContractError(code)
    return value


def _require_nonnegative(value: object, code: str) -> int:
    if type(value) is not int or value < 0:
        raise ContextContractError(code)
    return value


def _require_nonnegative_float(value: object, code: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
        raise ContextContractError(code)
    return float(value)


def _require_safe_metadata(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or _SAFE_METADATA_PATTERN.fullmatch(value) is None:
        raise ContextContractError("invalid_safe_metadata")
    return value


def _digest_identifier(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _update_content_free_digest(digest: Any, value: object) -> None:
    def add(tag: bytes, payload: bytes = b"") -> None:
        digest.update(tag)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)

    if value is None:
        add(b"N")
    elif type(value) is int:
        add(b"I", str(value).encode("ascii"))
    elif type(value) is str:
        add(b"S", value.encode("utf-8"))
    elif type(value) is tuple:
        add(b"T", len(value).to_bytes(8, "big"))
        for item in value:
            _update_content_free_digest(digest, item)
    else:
        raise ContextContractError("invalid_prefix_fingerprint")


@dataclass(frozen=True, slots=True)
class TransportReceipt:
    """Validated metadata-only observation of one compiled provider request."""

    request_id: str
    correlation_id: str
    full_context_hash: str
    logical_model: str
    resolved_model: str
    observed_provider: str | None
    stream: bool
    status: str
    error_code: str | None
    http_status: int | None
    first_text_ms: float | None
    full_response_ms: float | None
    total_ms: float
    output_chars: int
    chunk_count: int
    provider_usage_present: bool
    usage_status: str
    input_tokens: int | None
    cached_tokens: int | None
    cache_write_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None

    def __post_init__(self) -> None:
        _require_safe_metadata(self.request_id)
        _require_safe_metadata(self.correlation_id)
        _require_sha256(self.full_context_hash, "invalid_full_context_hash")
        _require_safe_metadata(self.logical_model)
        _require_safe_metadata(self.resolved_model)
        _require_safe_metadata(self.observed_provider, optional=True)
        if type(self.stream) is not bool:
            raise ContextContractError("invalid_transport_stream")
        if self.status not in _TRANSPORT_STATUSES:
            raise ContextContractError("invalid_transport_status")
        _require_safe_metadata(self.error_code, optional=True)
        if self.http_status is not None and (
            type(self.http_status) is not int or not 100 <= self.http_status <= 599
        ):
            raise ContextContractError("invalid_http_status")
        if self.first_text_ms is not None:
            object.__setattr__(
                self,
                "first_text_ms",
                _require_nonnegative_float(self.first_text_ms, "invalid_first_text_ms"),
            )
        if self.full_response_ms is not None:
            object.__setattr__(
                self,
                "full_response_ms",
                _require_nonnegative_float(
                    self.full_response_ms, "invalid_full_response_ms"
                ),
            )
        object.__setattr__(
            self,
            "total_ms",
            _require_nonnegative_float(self.total_ms, "invalid_total_ms"),
        )
        _require_nonnegative(self.output_chars, "invalid_output_chars")
        _require_nonnegative(self.chunk_count, "invalid_chunk_count")
        if self.stream:
            if self.full_response_ms is not None:
                raise ContextContractError("invalid_stream_latency")
        elif self.first_text_ms is not None:
            raise ContextContractError("invalid_sync_latency")
        if type(self.provider_usage_present) is not bool:
            raise ContextContractError("invalid_provider_usage_present")
        if self.usage_status not in _USAGE_STATUSES:
            raise ContextContractError("invalid_usage_status")
        usage_values = (
            self.input_tokens,
            self.cached_tokens,
            self.cache_write_tokens,
            self.output_tokens,
            self.total_tokens,
        )
        for value in usage_values:
            if value is not None:
                _require_nonnegative(value, "invalid_usage_value")
        if not self.provider_usage_present:
            if self.usage_status != "missing" or any(
                value is not None for value in usage_values
            ):
                raise ContextContractError("invalid_usage_status")
        elif self.usage_status == "missing":
            raise ContextContractError("invalid_usage_status")
        if self.usage_status in {"invalid", "unsupported"} and any(
            value is not None for value in usage_values
        ):
            raise ContextContractError("invalid_usage_status")


@dataclass(frozen=True, slots=True)
class PrefixCompatibilityKey:
    resolved_provider: str | None
    resolved_model: str | None
    lane: Lane
    route: Route
    wire_profile: str
    compiler_version: str
    compiler_config_revision: str

    def __post_init__(self) -> None:
        _require_optional_identifier(
            self.resolved_provider,
            "invalid_compatibility_key",
        )
        _require_optional_identifier(
            self.resolved_model,
            "invalid_compatibility_key",
        )
        if type(self.lane) is not Lane or type(self.route) is not Route:
            raise ContextContractError("invalid_compatibility_key")
        for value in (
            self.wire_profile,
            self.compiler_version,
            self.compiler_config_revision,
        ):
            _require_identifier(value, "invalid_compatibility_key")


def _copy_compatibility_key(value: object) -> PrefixCompatibilityKey:
    if type(value) is not PrefixCompatibilityKey:
        raise ContextContractError("invalid_compatibility_key")
    try:
        return PrefixCompatibilityKey(
            resolved_provider=value.resolved_provider,
            resolved_model=value.resolved_model,
            lane=value.lane,
            route=value.route,
            wire_profile=value.wire_profile,
            compiler_version=value.compiler_version,
            compiler_config_revision=value.compiler_config_revision,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_compatibility_key") from exc


@dataclass(frozen=True, slots=True)
class _VerifiedCompiled:
    compiled: CompiledContext
    projection: bytes
    projection_text: str
    system_content: str
    user_content: str
    included_slices: tuple[tuple[SelectionDecision, str], ...]
    layer_contents: tuple[str, str, str]
    layer_projections: tuple[bytes, bytes, bytes]


@dataclass(frozen=True, slots=True)
class PrefixFingerprint:
    request_id_digest: str
    comparison_scope_digest: str
    compatibility_key: PrefixCompatibilityKey
    slot_digests: tuple[tuple[str, str], ...]
    slot_prefix_char_offsets: tuple[int, ...]
    slot_prefix_byte_offsets: tuple[int, ...]
    slot_prefix_end_char_offsets: tuple[int, ...]
    slot_prefix_end_byte_offsets: tuple[int, ...]
    framed_layer_char_lengths: tuple[int, int, int]
    framed_layer_byte_lengths: tuple[int, int, int]
    static_prefix_hash: str
    durable_prefix_hash: str
    session_prefix_hash: str
    full_context_hash: str
    full_context_chars: int
    full_context_bytes: int

    def __post_init__(self) -> None:
        _require_sha256(self.request_id_digest, "invalid_request_id_digest")
        _require_sha256(
            self.comparison_scope_digest,
            "invalid_comparison_scope_digest",
        )
        key = _copy_compatibility_key(self.compatibility_key)
        object.__setattr__(self, "compatibility_key", key)

        if type(self.slot_digests) is not tuple:
            raise ContextContractError("invalid_prefix_fingerprint")
        pairs: list[tuple[str, str]] = []
        for entry in self.slot_digests:
            if type(entry) is not tuple or len(entry) != 2:
                raise ContextContractError("invalid_prefix_fingerprint")
            section_id = _require_identifier(
                entry[0],
                "invalid_prefix_fingerprint",
            )
            digest = _require_sha256(entry[1])
            pairs.append((section_id, digest))
        identifiers = [item[0] for item in pairs]
        if len(identifiers) != len(set(identifiers)):
            raise ContextContractError("invalid_prefix_fingerprint")

        expected_order = registry_slot_ids(key.lane, key.route) + (
            virtual_user_slot_id(key.route),
        )
        positions = {section_id: index for index, section_id in enumerate(expected_order)}
        if (
            not identifiers
            or identifiers[-1] != expected_order[-1]
            or any(section_id not in positions for section_id in identifiers)
            or [positions[section_id] for section_id in identifiers]
            != sorted(positions[section_id] for section_id in identifiers)
        ):
            raise ContextContractError("invalid_prefix_fingerprint")

        char_offsets = _copy_offsets(self.slot_prefix_char_offsets)
        byte_offsets = _copy_offsets(self.slot_prefix_byte_offsets)
        end_char_offsets = _copy_offsets(self.slot_prefix_end_char_offsets)
        end_byte_offsets = _copy_offsets(self.slot_prefix_end_byte_offsets)
        if (
            len(char_offsets) != len(pairs)
            or len(byte_offsets) != len(pairs)
            or len(end_char_offsets) != len(pairs)
            or len(end_byte_offsets) != len(pairs)
            or tuple(sorted(char_offsets)) != char_offsets
            or tuple(sorted(byte_offsets)) != byte_offsets
            or tuple(sorted(end_char_offsets)) != end_char_offsets
            or tuple(sorted(end_byte_offsets)) != end_byte_offsets
            or any(start > end for start, end in zip(char_offsets, end_char_offsets))
            or any(start > end for start, end in zip(byte_offsets, end_byte_offsets))
        ):
            raise ContextContractError("invalid_prefix_fingerprint")
        object.__setattr__(self, "slot_digests", tuple(pairs))
        object.__setattr__(self, "slot_prefix_char_offsets", char_offsets)
        object.__setattr__(self, "slot_prefix_byte_offsets", byte_offsets)
        object.__setattr__(self, "slot_prefix_end_char_offsets", end_char_offsets)
        object.__setattr__(self, "slot_prefix_end_byte_offsets", end_byte_offsets)

        char_lengths = _copy_layer_lengths(self.framed_layer_char_lengths)
        byte_lengths = _copy_layer_lengths(self.framed_layer_byte_lengths)
        object.__setattr__(self, "framed_layer_char_lengths", char_lengths)
        object.__setattr__(self, "framed_layer_byte_lengths", byte_lengths)
        for digest in (
            self.static_prefix_hash,
            self.durable_prefix_hash,
            self.session_prefix_hash,
            self.full_context_hash,
        ):
            _require_sha256(digest)
        _require_nonnegative(
            self.full_context_chars,
            "invalid_prefix_fingerprint",
        )
        _require_nonnegative(
            self.full_context_bytes,
            "invalid_prefix_fingerprint",
        )
        if (
            char_lengths[-1] > self.full_context_chars
            or byte_lengths[-1] > self.full_context_bytes
            or char_offsets[-1] > self.full_context_chars
            or byte_offsets[-1] > self.full_context_bytes
            or end_char_offsets[-1] > self.full_context_chars
            or end_byte_offsets[-1] > self.full_context_bytes
        ):
            raise ContextContractError("invalid_prefix_fingerprint")

    @classmethod
    def from_compiled(
        cls,
        compiled: CompiledContext,
        *,
        comparison_scope_digest: str,
    ) -> "PrefixFingerprint":
        verified = _verify_compiled_context(compiled)
        value = verified.compiled
        _require_sha256(
            comparison_scope_digest,
            "invalid_comparison_scope_digest",
        )
        slot_digests: list[tuple[str, str]] = []
        char_offsets: list[int] = []
        byte_offsets: list[int] = []
        end_char_offsets: list[int] = []
        end_byte_offsets: list[int] = []
        prefix_content = ""
        for index, (row, rendered) in enumerate(verified.included_slices):
            if index:
                prefix_content += "\n\n"
            prefix_projection = _canonical_system_prefix_projection(prefix_content)
            char_offsets.append(len(prefix_projection.decode("utf-8")))
            byte_offsets.append(len(prefix_projection))
            prefix_content += rendered
            end_projection = _canonical_system_prefix_projection(prefix_content)
            end_char_offsets.append(len(end_projection.decode("utf-8")))
            end_byte_offsets.append(len(end_projection))
            slot_digests.append(
                (row.section_id, hashlib.sha256(rendered.encode("utf-8")).hexdigest())
            )
        framed_layer_char_lengths = tuple(
            len(prefix.decode("utf-8")) for prefix in verified.layer_projections
        )
        framed_layer_byte_lengths = tuple(
            len(prefix) for prefix in verified.layer_projections
        )
        user_prefix = _canonical_user_content_prefix(verified.system_content)
        char_offsets.append(len(user_prefix.decode("utf-8")))
        byte_offsets.append(len(user_prefix))
        user_end_prefix = verified.projection[:-3]
        end_char_offsets.append(len(user_end_prefix.decode("utf-8")))
        end_byte_offsets.append(len(user_end_prefix))
        slot_digests.append(
            (
                virtual_user_slot_id(value.route),
                hashlib.sha256(verified.user_content.encode("utf-8")).hexdigest(),
            )
        )
        manifest = value.manifest
        return cls(
            request_id_digest=_digest_identifier(manifest.request_id),
            comparison_scope_digest=comparison_scope_digest,
            compatibility_key=PrefixCompatibilityKey(
                resolved_provider=manifest.resolved_provider,
                resolved_model=manifest.resolved_model,
                lane=value.lane,
                route=value.route,
                wire_profile=manifest.wire_profile,
                compiler_version=value.compiler_version,
                compiler_config_revision=manifest.compiler_config_revision,
            ),
            slot_digests=tuple(slot_digests),
            slot_prefix_char_offsets=tuple(char_offsets),
            slot_prefix_byte_offsets=tuple(byte_offsets),
            slot_prefix_end_char_offsets=tuple(end_char_offsets),
            slot_prefix_end_byte_offsets=tuple(end_byte_offsets),
            framed_layer_char_lengths=framed_layer_char_lengths,
            framed_layer_byte_lengths=framed_layer_byte_lengths,
            static_prefix_hash=value.static_prefix_hash,
            durable_prefix_hash=value.durable_prefix_hash,
            session_prefix_hash=value.session_prefix_hash,
            full_context_hash=value.full_context_hash,
            full_context_chars=len(verified.projection_text),
            full_context_bytes=len(verified.projection),
        )


def _copy_offsets(value: object) -> tuple[int, ...]:
    if type(value) is not tuple:
        raise ContextContractError("invalid_prefix_fingerprint")
    if any(type(item) is not int or item < 0 for item in value):
        raise ContextContractError("invalid_prefix_fingerprint")
    return value


def _copy_layer_lengths(value: object) -> tuple[int, int, int]:
    if type(value) is not tuple or len(value) != 3:
        raise ContextContractError("invalid_prefix_fingerprint")
    if any(type(item) is not int or item < 0 for item in value):
        raise ContextContractError("invalid_prefix_fingerprint")
    if not value[0] <= value[1] <= value[2]:
        raise ContextContractError("invalid_prefix_fingerprint")
    return value


def _copy_compiled(value: object) -> CompiledContext:
    if type(value) is not CompiledContext:
        raise ContextContractError("invalid_compiled_context")
    try:
        return CompiledContext(
            compiler_version=value.compiler_version,
            lane=value.lane,
            route=value.route,
            model=value.model,
            messages=value.messages,
            static_prefix_hash=value.static_prefix_hash,
            durable_prefix_hash=value.durable_prefix_hash,
            session_prefix_hash=value.session_prefix_hash,
            full_context_hash=value.full_context_hash,
            static_prefix_chars=value.static_prefix_chars,
            durable_prefix_chars=value.durable_prefix_chars,
            session_prefix_chars=value.session_prefix_chars,
            manifest=value.manifest,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_compiled_context") from exc


def _verify_manifest_prefix_relationships(manifest: CompilationManifest) -> None:
    status = manifest.prefix_comparison_status
    if status in {"no_prior", "incompatible"}:
        if (
            manifest.prior_compatible_request_id is not None
            or manifest.prefix_break_section_id is not None
            or manifest.prior_exact_matching_prefix_chars != 0
            or manifest.prior_exact_matching_prefix_tokens_est != 0
        ):
            raise ContextContractError("compiled_prefix_comparison_mismatch")
        return
    if manifest.prior_compatible_request_id is None:
        raise ContextContractError("compiled_prefix_comparison_mismatch")
    if status == "compatible_equal":
        if manifest.prefix_break_section_id is not None:
            raise ContextContractError("compiled_prefix_comparison_mismatch")
        return
    allowed_breaks = set(registry_slot_ids(manifest.lane, manifest.route))
    allowed_breaks.add(virtual_user_slot_id(manifest.route))
    if manifest.prefix_break_section_id not in allowed_breaks:
        raise ContextContractError("compiled_prefix_comparison_mismatch")


def _verify_compiled_context(value: object) -> _VerifiedCompiled:
    _require_compiled_attestation(value)
    compiled = _copy_compiled(value)
    manifest = compiled.manifest
    if (
        manifest.compiler_version != compiled.compiler_version
        or manifest.lane is not compiled.lane
        or manifest.route is not compiled.route
        or manifest.model != compiled.model
        or manifest.wire_profile != WIRE_PROFILE
    ):
        raise ContextContractError("compiled_truth_mismatch")
    if manifest.source_revisions != tuple(
        sorted(manifest.source_revisions, key=lambda item: item[0])
    ) or manifest.collection != tuple(
        sorted(manifest.collection, key=lambda item: item.source)
    ):
        raise ContextContractError("compiled_truth_mismatch")

    projection = canonical_message_projection(compiled.messages)
    if hashlib.sha256(projection).hexdigest() != compiled.full_context_hash:
        raise ContextContractError("compiled_truth_mismatch")
    projection_text = projection.decode("utf-8")
    system_content = compiled.messages[0].content
    user_content = compiled.messages[1].content
    if (
        not system_content.endswith("\n")
        or len(system_content) + len(user_content) != manifest.input_chars
        or estimate_tokens_v1(system_content) + estimate_tokens_v1(user_content)
        != manifest.input_tokens_est
    ):
        raise ContextContractError("compiled_truth_mismatch")

    registry_ids = registry_slot_ids(compiled.lane, compiled.route)
    positions = {section_id: index for index, section_id in enumerate(registry_ids)}
    row_positions = [positions.get(row.section_id, -1) for row in manifest.sections]
    if any(position < 0 for position in row_positions) or row_positions != sorted(
        row_positions
    ):
        raise ContextContractError("compiled_truth_mismatch")
    if (
        manifest.selected_sections
        != sum(row.included for row in manifest.sections)
        or manifest.dropped_sections
        != sum(not row.included for row in manifest.sections)
    ):
        raise ContextContractError("compiled_truth_mismatch")

    body = system_content[:-1]
    included_slices: list[tuple[SelectionDecision, str]] = []
    layer_content: dict[Lifetime, str] = {}
    consumed = 0
    prefix_content = ""
    included_rows = tuple(row for row in manifest.sections if row.included)
    for index, row in enumerate(included_rows):
        if row.chars is None or row.token_estimate is None:
            raise ContextContractError("compiled_truth_mismatch")
        if index:
            if body[consumed : consumed + 2] != "\n\n":
                raise ContextContractError("compiled_truth_mismatch")
            consumed += 2
            prefix_content += "\n\n"
        rendered = body[consumed : consumed + row.chars]
        if (
            not rendered
            or len(rendered) != row.chars
            or estimate_tokens_v1(rendered) != row.token_estimate
        ):
            raise ContextContractError("compiled_truth_mismatch")
        consumed += row.chars
        prefix_content += rendered
        included_slices.append((row, rendered))
        if row.lifetime in {
            Lifetime.STATIC,
            Lifetime.DURABLE,
            Lifetime.SESSION,
        }:
            layer_content[row.lifetime] = prefix_content
    if consumed != len(body):
        raise ContextContractError("compiled_truth_mismatch")

    static_content = layer_content.get(Lifetime.STATIC)
    if static_content is None:
        raise ContextContractError("compiled_truth_mismatch")
    durable_content = layer_content.get(Lifetime.DURABLE, static_content)
    session_content = layer_content.get(Lifetime.SESSION, durable_content)
    layer_contents = (static_content, durable_content, session_content)
    layer_projections = tuple(
        _canonical_system_prefix_projection(content) for content in layer_contents
    )
    if tuple(hashlib.sha256(item).hexdigest() for item in layer_projections) != (
        compiled.static_prefix_hash,
        compiled.durable_prefix_hash,
        compiled.session_prefix_hash,
    ):
        raise ContextContractError("compiled_truth_mismatch")
    if tuple(len(content) for content in layer_contents) != (
        compiled.static_prefix_chars,
        compiled.durable_prefix_chars,
        compiled.session_prefix_chars,
    ):
        raise ContextContractError("compiled_truth_mismatch")
    if tuple(estimate_tokens_v1(content) for content in layer_contents) != (
        manifest.static_prefix_tokens_est,
        manifest.durable_prefix_tokens_est,
        manifest.session_prefix_tokens_est,
    ):
        raise ContextContractError("compiled_truth_mismatch")
    _verify_manifest_prefix_relationships(manifest)
    return _VerifiedCompiled(
        compiled=compiled,
        projection=projection,
        projection_text=projection_text,
        system_content=system_content,
        user_content=user_content,
        included_slices=tuple(included_slices),
        layer_contents=layer_contents,
        layer_projections=layer_projections,
    )


def require_compiled_transport_integrity(
    compiled: CompiledContext,
    wire_messages: object,
) -> None:
    """Reject any role/content/order change to an issued compiled context."""

    _require_compiled_attestation(compiled)
    if type(wire_messages) is not list or len(wire_messages) != 2:
        raise ContextContractError("compiled_transport_hash_mismatch")
    copied: list[CompiledMessage] = []
    for message in wire_messages:
        if type(message) is not dict or set(message) != {"role", "content"}:
            raise ContextContractError("compiled_transport_hash_mismatch")
        role = message.get("role")
        content = message.get("content")
        if type(role) is not str or type(content) is not str:
            raise ContextContractError("compiled_transport_hash_mismatch")
        try:
            copied.append(CompiledMessage(role=role, content=content))
        except ContextContractError as exc:
            raise ContextContractError("compiled_transport_hash_mismatch") from exc
    try:
        projection = canonical_message_projection(copied)
    except ContextContractError as exc:
        raise ContextContractError("compiled_transport_hash_mismatch") from exc
    digest = hashlib.sha256(projection).hexdigest()
    if (
        digest != compiled.full_context_hash
        or digest != compiled.manifest.full_context_hash
        or tuple(copied) != compiled.messages
    ):
        raise ContextContractError("compiled_transport_hash_mismatch")


def prepare_compiled_transport_messages(
    compiled: CompiledContext,
) -> list[dict[str, str]]:
    """Validate compiler issuance and return a detached exact wire copy."""

    verified = _verify_compiled_context(compiled)
    messages = [
        {"role": message.role, "content": message.content}
        for message in verified.compiled.messages
    ]
    require_compiled_transport_integrity(compiled, messages)
    return messages


def _copy_fingerprint(value: object) -> PrefixFingerprint:
    if type(value) is not PrefixFingerprint:
        raise ContextContractError("invalid_prefix_fingerprint")
    try:
        return PrefixFingerprint(
            request_id_digest=value.request_id_digest,
            comparison_scope_digest=value.comparison_scope_digest,
            compatibility_key=value.compatibility_key,
            slot_digests=value.slot_digests,
            slot_prefix_char_offsets=value.slot_prefix_char_offsets,
            slot_prefix_byte_offsets=value.slot_prefix_byte_offsets,
            slot_prefix_end_char_offsets=value.slot_prefix_end_char_offsets,
            slot_prefix_end_byte_offsets=value.slot_prefix_end_byte_offsets,
            framed_layer_char_lengths=value.framed_layer_char_lengths,
            framed_layer_byte_lengths=value.framed_layer_byte_lengths,
            static_prefix_hash=value.static_prefix_hash,
            durable_prefix_hash=value.durable_prefix_hash,
            session_prefix_hash=value.session_prefix_hash,
            full_context_hash=value.full_context_hash,
            full_context_chars=value.full_context_chars,
            full_context_bytes=value.full_context_bytes,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_prefix_fingerprint") from exc


def _prefix_fingerprint_digest(value: PrefixFingerprint) -> str:
    key = value.compatibility_key
    structural_value = (
        "nana-prefix-fingerprint.v1",
        value.request_id_digest,
        value.comparison_scope_digest,
        (
            key.resolved_provider,
            key.resolved_model,
            key.lane.value,
            key.route.value,
            key.wire_profile,
            key.compiler_version,
            key.compiler_config_revision,
        ),
        value.slot_digests,
        value.slot_prefix_char_offsets,
        value.slot_prefix_byte_offsets,
        value.slot_prefix_end_char_offsets,
        value.slot_prefix_end_byte_offsets,
        value.framed_layer_char_lengths,
        value.framed_layer_byte_lengths,
        (
            value.static_prefix_hash,
            value.durable_prefix_hash,
            value.session_prefix_hash,
            value.full_context_hash,
        ),
        value.full_context_chars,
        value.full_context_bytes,
    )
    digest = hashlib.sha256()
    _update_content_free_digest(digest, structural_value)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class PrefixComparison:
    status: str
    comparison_scope_digest: str
    current_fingerprint_digest: str
    prior_compatible_request_digest: str | None
    prior_full_context_hash: str | None
    current_full_context_hash: str
    prefix_break_section_id: str | None
    prior_exact_matching_prefix_chars: int
    prior_exact_matching_prefix_bytes: int
    prior_exact_matching_prefix_tokens_est: int

    def __post_init__(self) -> None:
        if self.status not in _COMPARISON_STATUSES:
            raise ContextContractError("invalid_prefix_comparison_status")
        _require_sha256(
            self.comparison_scope_digest,
            "invalid_comparison_scope_digest",
        )
        _require_sha256(
            self.current_fingerprint_digest,
            "invalid_prefix_fingerprint_digest",
        )
        if self.prior_compatible_request_digest is not None:
            _require_sha256(
                self.prior_compatible_request_digest,
                "invalid_request_id_digest",
            )
        if self.prior_full_context_hash is not None:
            _require_sha256(self.prior_full_context_hash)
        _require_sha256(self.current_full_context_hash)
        _require_optional_identifier(
            self.prefix_break_section_id,
            "invalid_prefix_break_section_id",
        )
        for value in (
            self.prior_exact_matching_prefix_chars,
            self.prior_exact_matching_prefix_bytes,
            self.prior_exact_matching_prefix_tokens_est,
        ):
            _require_nonnegative(value, "invalid_prefix_length")

        if self.status == "compatible_changed":
            if (
                self.prior_compatible_request_digest is None
                or self.prior_full_context_hash is None
                or self.prefix_break_section_id is None
            ):
                raise ContextContractError("invalid_prefix_break_section_id")
        elif self.prefix_break_section_id is not None:
            raise ContextContractError("invalid_prefix_break_section_id")
        if self.status in {"no_prior", "incompatible"}:
            if (
                self.prior_compatible_request_digest is not None
                or self.prior_full_context_hash is not None
                or any(
                (
                    self.prior_exact_matching_prefix_chars,
                    self.prior_exact_matching_prefix_bytes,
                    self.prior_exact_matching_prefix_tokens_est,
                )
                )
            ):
                raise ContextContractError("invalid_prefix_comparison")
        elif (
            self.prior_compatible_request_digest is None
            or self.prior_full_context_hash is None
        ):
            raise ContextContractError("invalid_request_id_digest")
        if self.prior_exact_matching_prefix_tokens_est != _estimate_from_lengths(
            self.prior_exact_matching_prefix_chars,
            self.prior_exact_matching_prefix_bytes,
        ):
            raise ContextContractError("invalid_prefix_length")


def _prefix_comparison_attestation_digest(value: PrefixComparison) -> bytes:
    digest = hashlib.sha256()
    for item in (
        value.status,
        value.comparison_scope_digest,
        value.current_fingerprint_digest,
        value.prior_compatible_request_digest,
        value.prior_full_context_hash,
        value.current_full_context_hash,
        value.prefix_break_section_id,
        value.prior_exact_matching_prefix_chars,
        value.prior_exact_matching_prefix_bytes,
        value.prior_exact_matching_prefix_tokens_est,
    ):
        if item is None:
            payload = b"N"
        elif type(item) is int:
            payload = b"I" + str(item).encode("ascii")
        else:
            payload = b"S" + item.encode("utf-8")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.digest()


def _attest_prefix_comparison(value: PrefixComparison) -> PrefixComparison:
    object_id = id(value)
    fingerprint = _prefix_comparison_attestation_digest(value)
    with _PREFIX_COMPARISON_ATTESTATION_LOCK:
        _PREFIX_COMPARISON_ATTESTATIONS.pop(object_id, None)
        _PREFIX_COMPARISON_ATTESTATIONS[object_id] = fingerprint
        while (
            len(_PREFIX_COMPARISON_ATTESTATIONS)
            > _PREFIX_COMPARISON_ATTESTATION_CAPACITY
        ):
            _PREFIX_COMPARISON_ATTESTATIONS.popitem(last=False)
    return value


def _require_prefix_comparison_attestation(value: object) -> None:
    if type(value) is not PrefixComparison:
        raise ContextContractError("invalid_prefix_comparison_attestation")
    fingerprint = _prefix_comparison_attestation_digest(value)
    with _PREFIX_COMPARISON_ATTESTATION_LOCK:
        expected = _PREFIX_COMPARISON_ATTESTATIONS.get(id(value))
    if expected is None or expected != fingerprint:
        raise ContextContractError("invalid_prefix_comparison_attestation")


def _copy_comparison(value: object) -> PrefixComparison:
    if type(value) is not PrefixComparison:
        raise ContextContractError("invalid_prefix_comparison")
    try:
        return PrefixComparison(
            status=value.status,
            comparison_scope_digest=value.comparison_scope_digest,
            current_fingerprint_digest=value.current_fingerprint_digest,
            prior_compatible_request_digest=value.prior_compatible_request_digest,
            prior_full_context_hash=value.prior_full_context_hash,
            current_full_context_hash=value.current_full_context_hash,
            prefix_break_section_id=value.prefix_break_section_id,
            prior_exact_matching_prefix_chars=value.prior_exact_matching_prefix_chars,
            prior_exact_matching_prefix_bytes=value.prior_exact_matching_prefix_bytes,
            prior_exact_matching_prefix_tokens_est=(
                value.prior_exact_matching_prefix_tokens_est
            ),
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_prefix_comparison") from exc


def _empty_comparison(
    status: str,
    current: PrefixFingerprint,
) -> PrefixComparison:
    return _attest_prefix_comparison(
        PrefixComparison(
            status=status,
            comparison_scope_digest=current.comparison_scope_digest,
            current_fingerprint_digest=_prefix_fingerprint_digest(current),
            prior_compatible_request_digest=None,
            prior_full_context_hash=None,
            current_full_context_hash=current.full_context_hash,
            prefix_break_section_id=None,
            prior_exact_matching_prefix_chars=0,
            prior_exact_matching_prefix_bytes=0,
            prior_exact_matching_prefix_tokens_est=0,
        )
    )


def _estimate_from_lengths(chars: int, byte_count: int) -> int:
    return max(math.ceil(chars / 3), math.ceil(byte_count / 4))


def _start_offset_for_slot(
    fingerprint: PrefixFingerprint,
    section_id: str,
    order: tuple[str, ...],
) -> tuple[int, int]:
    identifiers = [entry[0] for entry in fingerprint.slot_digests]
    offsets = {
        identifier: (char_offset, byte_offset)
        for identifier, char_offset, byte_offset in zip(
            identifiers,
            fingerprint.slot_prefix_char_offsets,
            fingerprint.slot_prefix_byte_offsets,
        )
    }
    if section_id in offsets:
        return offsets[section_id]
    start = order.index(section_id)
    for later in order[start + 1 :]:
        if later in offsets:
            return offsets[later]
    return fingerprint.full_context_chars, fingerprint.full_context_bytes


def _end_offsets(fingerprint: PrefixFingerprint) -> dict[str, tuple[int, int]]:
    return {
        identifier: (char_offset, byte_offset)
        for (identifier, _), char_offset, byte_offset in zip(
            fingerprint.slot_digests,
            fingerprint.slot_prefix_end_char_offsets,
            fingerprint.slot_prefix_end_byte_offsets,
        )
    }


def _matching_offset_before_break(
    prior: PrefixFingerprint,
    present: PrefixFingerprint,
    break_id: str,
    order: tuple[str, ...],
) -> tuple[int, int]:
    prior_digests = dict(prior.slot_digests)
    present_digests = dict(present.slot_digests)
    if break_id in prior_digests and break_id in present_digests:
        prior_offset = _start_offset_for_slot(prior, break_id, order)
        present_offset = _start_offset_for_slot(present, break_id, order)
        return min(prior_offset[0], present_offset[0]), min(
            prior_offset[1],
            present_offset[1],
        )

    break_position = order.index(break_id)
    virtual_user = order[-1]
    prior_next = next(
        (
            section_id
            for section_id in order[break_position:]
            if section_id != virtual_user and section_id in prior_digests
        ),
        None,
    )
    present_next = next(
        (
            section_id
            for section_id in order[break_position:]
            if section_id != virtual_user and section_id in present_digests
        ),
        None,
    )
    if prior_next is not None and present_next is not None:
        prior_offset = _start_offset_for_slot(prior, prior_next, order)
        present_offset = _start_offset_for_slot(present, present_next, order)
        return min(prior_offset[0], present_offset[0]), min(
            prior_offset[1],
            present_offset[1],
        )

    prior_ends = _end_offsets(prior)
    present_ends = _end_offsets(present)
    for section_id in reversed(order[:break_position]):
        if (
            section_id in prior_ends
            and section_id in present_ends
            and prior_digests[section_id] == present_digests[section_id]
        ):
            return min(prior_ends[section_id][0], present_ends[section_id][0]), min(
                prior_ends[section_id][1],
                present_ends[section_id][1],
            )
    opening = b'[["system","'
    return len(opening.decode("utf-8")), len(opening)


def compare_prefixes(
    previous: PrefixFingerprint | None,
    current: PrefixFingerprint,
) -> PrefixComparison:
    """Compare content-free fingerprints without retaining either input."""

    prior = None if previous is None else _copy_fingerprint(previous)
    present = _copy_fingerprint(current)
    if previous is None:
        return _empty_comparison("no_prior", present)
    assert prior is not None
    if (
        prior.comparison_scope_digest != present.comparison_scope_digest
        or prior.compatibility_key != present.compatibility_key
        or prior.compatibility_key.resolved_provider is None
        or prior.compatibility_key.resolved_model is None
        or present.compatibility_key.resolved_provider is None
        or present.compatibility_key.resolved_model is None
    ):
        return _empty_comparison("incompatible", present)

    hashes_equal = (
        prior.static_prefix_hash,
        prior.durable_prefix_hash,
        prior.session_prefix_hash,
        prior.full_context_hash,
    ) == (
        present.static_prefix_hash,
        present.durable_prefix_hash,
        present.session_prefix_hash,
        present.full_context_hash,
    )
    if hashes_equal and prior.slot_digests == present.slot_digests:
        chars = min(prior.full_context_chars, present.full_context_chars)
        byte_count = min(prior.full_context_bytes, present.full_context_bytes)
        return _attest_prefix_comparison(
            PrefixComparison(
                status="compatible_equal",
                comparison_scope_digest=present.comparison_scope_digest,
                current_fingerprint_digest=_prefix_fingerprint_digest(present),
                prior_compatible_request_digest=prior.request_id_digest,
                prior_full_context_hash=prior.full_context_hash,
                current_full_context_hash=present.full_context_hash,
                prefix_break_section_id=None,
                prior_exact_matching_prefix_chars=chars,
                prior_exact_matching_prefix_bytes=byte_count,
                prior_exact_matching_prefix_tokens_est=_estimate_from_lengths(
                    chars,
                    byte_count,
                ),
            )
        )

    key = present.compatibility_key
    order = registry_slot_ids(key.lane, key.route) + (
        virtual_user_slot_id(key.route),
    )
    prior_digests = dict(prior.slot_digests)
    present_digests = dict(present.slot_digests)
    break_id = next(
        (
            section_id
            for section_id in order
            if prior_digests.get(section_id) != present_digests.get(section_id)
        ),
        virtual_user_slot_id(key.route),
    )
    chars, byte_count = _matching_offset_before_break(
        prior,
        present,
        break_id,
        order,
    )
    return _attest_prefix_comparison(
        PrefixComparison(
            status="compatible_changed",
            comparison_scope_digest=present.comparison_scope_digest,
            current_fingerprint_digest=_prefix_fingerprint_digest(present),
            prior_compatible_request_digest=prior.request_id_digest,
            prior_full_context_hash=prior.full_context_hash,
            current_full_context_hash=present.full_context_hash,
            prefix_break_section_id=break_id,
            prior_exact_matching_prefix_chars=chars,
            prior_exact_matching_prefix_bytes=byte_count,
            prior_exact_matching_prefix_tokens_est=_estimate_from_lengths(
                chars,
                byte_count,
            ),
        )
    )


class BoundedPrefixTracker:
    """Atomic, bounded prior-fingerprint tracking by opaque scope digest."""

    __slots__ = ("_capacity", "_entries", "_lock")

    def __init__(self, *, capacity: int) -> None:
        if type(capacity) is not int or capacity <= 0:
            raise ContextContractError("invalid_tracker_capacity")
        self._capacity = capacity
        self._entries: OrderedDict[str, PrefixFingerprint] = OrderedDict()
        self._lock = Lock()

    def compare_and_replace(self, current: PrefixFingerprint) -> PrefixComparison:
        present = _copy_fingerprint(current)
        scope = present.comparison_scope_digest
        with self._lock:
            prior = self._entries.pop(scope, None)
            comparison = compare_prefixes(prior, present)
            self._entries[scope] = present
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
            return comparison

    def snapshot(self) -> tuple[PrefixFingerprint, ...]:
        with self._lock:
            return tuple(_copy_fingerprint(item) for item in self._entries.values())

    def __repr__(self) -> str:
        with self._lock:
            size = len(self._entries)
        return f"BoundedPrefixTracker(capacity={self._capacity}, size={size})"


@dataclass(frozen=True, slots=True)
class SentContextManifest:
    request_id: str
    correlation_id: str
    capture_id: str
    snapshot_revision: int
    model: str
    resolved_model: str | None
    resolved_provider: str | None
    lane: Lane
    route: Route
    wire_profile: str
    full_context_hash: str
    input_chars: int
    input_tokens_est: int

    def __post_init__(self) -> None:
        for value in (
            self.request_id,
            self.correlation_id,
            self.capture_id,
            self.model,
        ):
            _require_safe_metadata(value)
        _require_safe_metadata(self.resolved_model, optional=True)
        _require_safe_metadata(self.resolved_provider, optional=True)
        _require_nonnegative(self.snapshot_revision, "invalid_snapshot_revision")
        if (
            type(self.lane) is not Lane
            or type(self.route) is not Route
            or self.route not in _SENT_ROUTES[self.lane]
        ):
            raise ContextContractError("invalid_lane_route_scope")
        if self.wire_profile != WIRE_PROFILE:
            raise ContextContractError("invalid_wire_profile")
        _require_sha256(self.full_context_hash)
        _require_nonnegative(self.input_chars, "invalid_total")
        _require_nonnegative(self.input_tokens_est, "invalid_total")


def _copy_sent_manifest(value: object) -> SentContextManifest:
    if type(value) is not SentContextManifest:
        raise ContextContractError("invalid_sent_context_manifest")
    try:
        return SentContextManifest(
            request_id=value.request_id,
            correlation_id=value.correlation_id,
            capture_id=value.capture_id,
            snapshot_revision=value.snapshot_revision,
            model=value.model,
            resolved_model=value.resolved_model,
            resolved_provider=value.resolved_provider,
            lane=value.lane,
            route=value.route,
            wire_profile=value.wire_profile,
            full_context_hash=value.full_context_hash,
            input_chars=value.input_chars,
            input_tokens_est=value.input_tokens_est,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_sent_context_manifest") from exc


@dataclass(frozen=True, slots=True, init=False)
class ContextTelemetry:
    """A factory-issued metadata-only snapshot suitable for a telemetry sink."""

    label: str
    production_bound: bool
    manifest: CompilationManifest | SentContextManifest
    comparison: PrefixComparison | None

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise ContextContractError("invalid_telemetry_receipt")


def _validate_manifest_comparison(
    manifest: CompilationManifest,
    comparison: PrefixComparison | None,
) -> PrefixComparison | None:
    if comparison is None:
        if manifest.prefix_comparison_status != "no_prior":
            raise ContextContractError("telemetry_comparison_mismatch")
        return None
    copied = _copy_comparison(comparison)
    if (
        copied.current_full_context_hash != manifest.full_context_hash
        or copied.status != manifest.prefix_comparison_status
        or copied.prefix_break_section_id != manifest.prefix_break_section_id
        or copied.prior_compatible_request_digest
        != manifest.prior_compatible_request_id
        or copied.prior_exact_matching_prefix_chars
        != manifest.prior_exact_matching_prefix_chars
        or copied.prior_exact_matching_prefix_tokens_est
        != manifest.prior_exact_matching_prefix_tokens_est
    ):
        raise ContextContractError("telemetry_comparison_mismatch")
    return copied


def _verify_candidate_fingerprint(
    compiled: CompiledContext,
    fingerprint: object,
) -> PrefixFingerprint:
    copied = _copy_fingerprint(fingerprint)
    expected = PrefixFingerprint.from_compiled(
        compiled,
        comparison_scope_digest=copied.comparison_scope_digest,
    )
    if copied != expected:
        raise ContextContractError("telemetry_fingerprint_mismatch")
    return copied


def _candidate_manifest_snapshot(
    compiled: CompiledContext,
    manifest: CompilationManifest,
    *,
    fingerprint: PrefixFingerprint | None,
    comparison: PrefixComparison | None,
) -> tuple[CompilationManifest, PrefixComparison | None]:
    if comparison is None:
        if fingerprint is not None:
            _verify_candidate_fingerprint(compiled, fingerprint)
        return replace(manifest), None
    if fingerprint is None:
        raise ContextContractError("missing_prefix_fingerprint")
    verified_fingerprint = _verify_candidate_fingerprint(compiled, fingerprint)
    _require_prefix_comparison_attestation(comparison)
    copied_comparison = _copy_comparison(comparison)
    if (
        copied_comparison.comparison_scope_digest
        != verified_fingerprint.comparison_scope_digest
        or copied_comparison.current_fingerprint_digest
        != _prefix_fingerprint_digest(verified_fingerprint)
        or copied_comparison.current_full_context_hash
        != verified_fingerprint.full_context_hash
    ):
        raise ContextContractError("telemetry_comparison_mismatch")
    snapshot = replace(
        manifest,
        prior_compatible_request_id=(
            copied_comparison.prior_compatible_request_digest
        ),
        prefix_break_section_id=copied_comparison.prefix_break_section_id,
        prefix_comparison_status=copied_comparison.status,
        prior_exact_matching_prefix_chars=(
            copied_comparison.prior_exact_matching_prefix_chars
        ),
        prior_exact_matching_prefix_tokens_est=(
            copied_comparison.prior_exact_matching_prefix_tokens_est
        ),
    )
    return snapshot, copied_comparison


def _make_context_telemetry(
    *,
    label: str,
    production_bound: bool,
    manifest: CompilationManifest | SentContextManifest,
    comparison: PrefixComparison | None,
) -> ContextTelemetry:
    if type(label) is not str or label not in _TELEMETRY_LABELS:
        raise ContextContractError("invalid_telemetry_label")
    if type(production_bound) is not bool:
        raise ContextContractError("invalid_production_bound")
    if label == "candidate_context":
        if production_bound or type(manifest) is not CompilationManifest:
            raise ContextContractError("invalid_telemetry_binding")
        try:
            copied_manifest = replace(manifest)
        except (AttributeError, TypeError) as exc:
            raise ContextContractError("invalid_compilation_manifest") from exc
        copied_comparison = _validate_manifest_comparison(
            copied_manifest,
            comparison,
        )
    else:
        if not production_bound or type(manifest) is not SentContextManifest:
            raise ContextContractError("invalid_telemetry_binding")
        if comparison is not None:
            raise ContextContractError("telemetry_comparison_mismatch")
        copied_manifest = _copy_sent_manifest(manifest)
        copied_comparison = None

    receipt = object.__new__(ContextTelemetry)
    object.__setattr__(receipt, "label", label)
    object.__setattr__(receipt, "production_bound", production_bound)
    object.__setattr__(receipt, "manifest", copied_manifest)
    object.__setattr__(receipt, "comparison", copied_comparison)
    return receipt


class ContextTelemetrySink(Protocol):
    def emit(self, receipt: ContextTelemetry) -> None: ...


def _emit_best_effort(
    receipt: ContextTelemetry,
    sink: ContextTelemetrySink | None,
    *,
    enabled: bool,
) -> None:
    if not enabled or sink is None:
        return
    try:
        emit = getattr(sink, "emit")
        if callable(emit):
            emit(receipt)
    except Exception:
        pass


def build_candidate_context_telemetry(
    compiled: CompiledContext,
    *,
    fingerprint: PrefixFingerprint | None = None,
    comparison: PrefixComparison | None = None,
) -> ContextTelemetry:
    verified = _verify_compiled_context(compiled)
    manifest, copied_comparison = _candidate_manifest_snapshot(
        compiled,
        verified.compiled.manifest,
        fingerprint=fingerprint,
        comparison=comparison,
    )
    return _make_context_telemetry(
        label="candidate_context",
        production_bound=False,
        manifest=manifest,
        comparison=copied_comparison,
    )


def emit_context_telemetry(
    compiled: CompiledContext,
    *,
    label: str,
    production_bound: bool,
    fingerprint: PrefixFingerprint | None = None,
    comparison: PrefixComparison | None = None,
    sink: ContextTelemetrySink | None = None,
    enabled: bool = True,
) -> ContextTelemetry:
    """Create a receipt and best-effort emit it without affecting compilation."""

    if type(enabled) is not bool:
        raise ContextContractError("invalid_telemetry_enabled")
    if type(label) is not str or label not in _TELEMETRY_LABELS:
        raise ContextContractError("invalid_telemetry_label")
    if type(production_bound) is not bool:
        raise ContextContractError("invalid_production_bound")
    if label != "candidate_context" or production_bound:
        raise ContextContractError("invalid_telemetry_binding")
    receipt = build_candidate_context_telemetry(
        compiled,
        fingerprint=fingerprint,
        comparison=comparison,
    )
    _emit_best_effort(receipt, sink, enabled=enabled)
    return receipt


def build_sent_context_telemetry(
    messages: tuple[CompiledMessage, ...] | list[CompiledMessage],
    *,
    request_id: str,
    correlation_id: str,
    capture_id: str,
    snapshot_revision: int,
    model: str,
    resolved_model: str | None,
    resolved_provider: str | None,
    lane: Lane,
    route: Route,
) -> ContextTelemetry:
    values = tuple(messages)
    projection = canonical_message_projection(values)
    manifest = SentContextManifest(
        request_id=request_id,
        correlation_id=correlation_id,
        capture_id=capture_id,
        snapshot_revision=snapshot_revision,
        model=model,
        resolved_model=resolved_model,
        resolved_provider=resolved_provider,
        lane=lane,
        route=route,
        wire_profile=WIRE_PROFILE,
        full_context_hash=hashlib.sha256(projection).hexdigest(),
        input_chars=sum(len(message.content) for message in values),
        input_tokens_est=sum(estimate_tokens_v1(message.content) for message in values),
    )
    return _make_context_telemetry(
        label="sent_context",
        production_bound=True,
        manifest=manifest,
        comparison=None,
    )


def emit_sent_context_telemetry(
    messages: tuple[CompiledMessage, ...] | list[CompiledMessage],
    *,
    request_id: str,
    correlation_id: str,
    capture_id: str,
    snapshot_revision: int,
    model: str,
    resolved_model: str | None,
    resolved_provider: str | None,
    lane: Lane,
    route: Route,
    sink: ContextTelemetrySink | None = None,
    enabled: bool = True,
) -> ContextTelemetry:
    if type(enabled) is not bool:
        raise ContextContractError("invalid_telemetry_enabled")
    receipt = build_sent_context_telemetry(
        messages,
        request_id=request_id,
        correlation_id=correlation_id,
        capture_id=capture_id,
        snapshot_revision=snapshot_revision,
        model=model,
        resolved_model=resolved_model,
        resolved_provider=resolved_provider,
        lane=lane,
        route=route,
    )
    _emit_best_effort(receipt, sink, enabled=enabled)
    return receipt


__all__ = [
    "BoundedPrefixTracker",
    "ContextTelemetry",
    "ContextTelemetrySink",
    "PrefixComparison",
    "PrefixCompatibilityKey",
    "PrefixFingerprint",
    "SentContextManifest",
    "TransportReceipt",
    "build_candidate_context_telemetry",
    "build_sent_context_telemetry",
    "compare_prefixes",
    "emit_context_telemetry",
    "emit_sent_context_telemetry",
    "prepare_compiled_transport_messages",
    "require_compiled_transport_integrity",
]
