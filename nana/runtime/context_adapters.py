"""Injected, lane-scoped source capabilities for Context Runtime.

These types contain no production adapters.  Composition code supplies
side-effect-free readers, and each read identifies the source's true owner so
collection receipts stay coherent with ``ContextSection.source.owner``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import hmac
import math
from types import MappingProxyType
from typing import Protocol
import unicodedata
import weakref

from nana.runtime.context_contracts import (
    ContextContractError,
    ContextSection,
    Freshness,
    FrozenMapping,
    Lane,
    Lifetime,
    ResolvedContextRequest,
    SemanticRole,
    SourceSnapshot,
    SourceRef,
    ProvenanceRef,
    _copy_context_section,
    _is_frozen_payload,
    _structural_digest,
    freeze_payload,
    require_resolved_context_request,
)


class SourceCaptureChanged(RuntimeError):
    """Explicit owner signal that one short capture was locally unstable."""

    def __init__(self, source: str) -> None:
        self.source = str(source or "unknown")
        super().__init__(self.source)


@dataclass(frozen=True, slots=True)
class SourceRevision:
    """Task 8-local revision metadata without changing SourceSnapshot schema."""

    source: str
    revision: str
    revision_kind: str

    def __post_init__(self) -> None:
        if type(self.source) is not str or not self.source.strip():
            raise ContextContractError("invalid_source_id")
        if type(self.revision) is not str or not self.revision.strip():
            raise ContextContractError("invalid_source_revision")
        if self.revision_kind not in {"content_hash", "semantic_revision"}:
            raise ContextContractError("invalid_source_revision_kind")


@dataclass(frozen=True, slots=True)
class SourceBatch:
    """One source owner's immutable result from a side-effect-free read."""

    owner: str
    revision: str
    sections: tuple[ContextSection, ...]

    def __post_init__(self) -> None:
        if type(self.owner) is not str or not self.owner.strip():
            raise ContextContractError("invalid_source_id")
        if type(self.revision) is not str or not self.revision.strip():
            raise ContextContractError("invalid_source_revision")
        if isinstance(self.sections, (str, bytes)):
            raise ContextContractError("invalid_section")
        try:
            values = tuple(self.sections)
        except TypeError as exc:
            raise ContextContractError("invalid_section") from exc
        sections = tuple(_copy_context_section(section) for section in values)
        section_ids = [section.id for section in sections]
        if len(section_ids) != len(set(section_ids)):
            raise ContextContractError("duplicate_section_id")
        if any(
            section.source.owner != self.owner
            or section.revision != self.revision
            for section in sections
        ):
            raise ContextContractError("invalid_source_revision")
        object.__setattr__(self, "sections", sections)


class ContextSourceAdapter(Protocol):
    """Minimal injected read capability; implementations must not write."""

    def read(self, request: ResolvedContextRequest) -> SourceBatch:
        ...


@dataclass(frozen=True, slots=True)
class PrivateSourceView:
    core: ContextSourceAdapter
    owner_identity: ContextSourceAdapter
    private_memory: ContextSourceAdapter
    private_session: ContextSourceAdapter
    runtime_state: ContextSourceAdapter
    expression: ContextSourceAdapter


@dataclass(frozen=True, slots=True)
class PublicSourceView:
    core: ContextSourceAdapter
    public_scope: ContextSourceAdapter
    public_session: ContextSourceAdapter
    public_grounding: ContextSourceAdapter
    expression: ContextSourceAdapter


@dataclass(frozen=True, slots=True)
class OperatorSourceView:
    """Backstage-only capabilities; diagnostics stay conditional in Task 5."""

    core: ContextSourceAdapter
    runtime_state: ContextSourceAdapter
    expression: ContextSourceAdapter


@dataclass(frozen=True, slots=True)
class FrozenGroundingEvidence:
    """Final sanitized evidence snapshot, detached from source/verifier lists.

    Source composition calls freeze_grounding_evidence exactly once before
    constructing GptTurnInput. Planning/rendering must not sanitize it again.
    """

    status: str
    confidence: float
    lane_visible_to: str
    evidence_strength: str
    snippets: tuple[str, ...]
    has_legacy_timestamp: bool
    notes: str
    source_event_ids: tuple[str, ...]

    def __post_init__(self):
        if self.status not in {'found', 'partial', 'not_found', 'user_claim_only'}:
            raise ContextContractError('invalid_grounding_evidence')
        if type(self.confidence) not in (int, float) or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ContextContractError('invalid_grounding_evidence')
        if self.lane_visible_to not in {'private_only', 'public_safe', 'operator_only'}:
            raise ContextContractError('invalid_grounding_evidence')
        if type(self.has_legacy_timestamp) is not bool or type(self.notes) is not str:
            raise ContextContractError('invalid_grounding_evidence')
        if self.evidence_strength not in {'weak', 'moderate', 'strong'}:
            raise ContextContractError('invalid_grounding_evidence')
        for name in ('snippets', 'source_event_ids'):
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)) or any(type(v) is not str for v in values):
                raise ContextContractError('invalid_grounding_evidence')
            object.__setattr__(self, name, tuple(values))

    def to_prompt_block(self, *, include_details=True):
        from nana.runtime.memory_grounding import MemoryEvidence
        return MemoryEvidence.to_prompt_block(self, include_details=include_details)

    def to_verifier_evidence(self):
        from nana.runtime.memory_grounding import MemoryEvidence
        return MemoryEvidence(
            status=self.status, confidence=self.confidence, lane_visible_to=self.lane_visible_to,
            evidence_strength=self.evidence_strength, snippets=list(self.snippets),
            has_legacy_timestamp=self.has_legacy_timestamp, notes=self.notes,
            source_event_ids=list(self.source_event_ids),
        )


def freeze_grounding_evidence(evidence, *, redact):
    """One raw-source sanitation boundary; callers pass the result unchanged."""
    if evidence is None:
        return None
    try:
        return FrozenGroundingEvidence(
            status=evidence.status, confidence=evidence.confidence,
            lane_visible_to=evidence.lane_visible_to, evidence_strength=evidence.evidence_strength,
            snippets=tuple(redact(s) for s in evidence.snippets),
            has_legacy_timestamp=evidence.has_legacy_timestamp,
            notes=redact(evidence.notes),
            source_event_ids=tuple(redact(s) for s in evidence.source_event_ids),
        )
    except (AttributeError, TypeError) as exc:
        raise ContextContractError('invalid_grounding_evidence') from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryRetrievalRecord:
    """Immutable runtime projection of one source-owned memory record."""

    record_id: str
    source_event_id: str
    source: str
    memory_type: str
    semantic_role: SemanticRole
    text: str
    visibility: frozenset[Lane]
    confidence: float
    relevance: float
    observed_at: float | None
    expires_at: float | None
    subject: str | None = None
    subject_role: str | None = None
    conflict_key: str | None = None
    canonical_value: str | None = None
    authority: str | None = None
    semantic_status: str | None = None
    temporal_selected: bool = False

    def __post_init__(self):
        for name in ("record_id", "source_event_id", "source", "memory_type", "text"):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise ContextContractError("invalid_memory_record")
        if type(self.semantic_role) is not SemanticRole:
            raise ContextContractError("invalid_memory_record")
        if type(self.visibility) is not frozenset:
            raise ContextContractError("invalid_memory_record")
        visibility = self.visibility
        if not visibility or any(type(item) is not Lane for item in visibility):
            raise ContextContractError("invalid_memory_record")
        object.__setattr__(self, "visibility", visibility)
        for name in ("confidence", "relevance"):
            value = getattr(self, name)
            if type(value) not in (int, float):
                raise ContextContractError("invalid_memory_record")
            value = float(value)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ContextContractError("invalid_memory_record")
            object.__setattr__(self, name, value)
        for name in ("observed_at", "expires_at"):
            value = getattr(self, name)
            if value is not None:
                if type(value) not in (int, float) or not math.isfinite(float(value)) or float(value) < 0:
                    raise ContextContractError("invalid_memory_record")
                object.__setattr__(self, name, float(value))
        if self.observed_at is not None and self.expires_at is not None and self.expires_at < self.observed_at:
            raise ContextContractError("invalid_memory_record")
        for name in (
            "subject",
            "subject_role",
            "conflict_key",
            "canonical_value",
            "authority",
            "semantic_status",
        ):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or not value.strip()):
                raise ContextContractError("invalid_memory_record")
        for name, limit in (
            ("subject", 200),
            ("subject_role", 80),
            ("conflict_key", 200),
            ("canonical_value", 700),
        ):
            value = getattr(self, name)
            if value is not None:
                if value != value.strip() or len(value) > limit:
                    raise ContextContractError("invalid_memory_record")
        if type(self.temporal_selected) is not bool:
            raise ContextContractError("invalid_memory_record")


def _copy_memory_retrieval_record(value):
    if type(value) is not MemoryRetrievalRecord:
        raise ContextContractError("invalid_memory_record")
    try:
        return MemoryRetrievalRecord(
            record_id=value.record_id,
            source_event_id=value.source_event_id,
            source=value.source,
            memory_type=value.memory_type,
            semantic_role=value.semantic_role,
            text=value.text,
            visibility=value.visibility,
            confidence=value.confidence,
            relevance=value.relevance,
            observed_at=value.observed_at,
            expires_at=value.expires_at,
            subject=value.subject,
            subject_role=value.subject_role,
            conflict_key=value.conflict_key,
            canonical_value=value.canonical_value,
            authority=value.authority,
            semantic_status=value.semantic_status,
            temporal_selected=value.temporal_selected,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_memory_record") from exc


@dataclass(frozen=True, slots=True)
class MemoryRetrievalResult:
    """One recursively immutable retriever result for a canonical turn."""

    source_revision: str
    status: str
    records: tuple[MemoryRetrievalRecord, ...]
    semantic_used: bool
    fallback_used: bool
    rejected: Mapping[str, int]
    query_fingerprint: str

    def __post_init__(self):
        if type(self.source_revision) is not str or not self.source_revision.strip():
            raise ContextContractError("invalid_memory_source_revision")
        if type(self.status) is not str or not self.status.strip():
            raise ContextContractError("invalid_memory_retrieval")
        if type(self.semantic_used) is not bool or type(self.fallback_used) is not bool:
            raise ContextContractError("invalid_memory_retrieval")
        if type(self.query_fingerprint) is not str:
            raise ContextContractError("invalid_memory_retrieval")
        if type(self.records) is not tuple:
            raise ContextContractError("invalid_memory_retrieval")
        values = self.records
        records = []
        identities = {}
        for record in values:
            record = _copy_memory_retrieval_record(record)
            collisions = {
                identities[key]
                for key in (("record", record.record_id), ("event", record.source_event_id))
                if key in identities
            }
            if collisions:
                if len(collisions) != 1 or records[next(iter(collisions))] != record:
                    raise ContextContractError("conflicting_memory_record_id")
                continue
            position = len(records)
            records.append(record)
            identities[("record", record.record_id)] = position
            identities[("event", record.source_event_id)] = position
        object.__setattr__(self, "records", tuple(records))
        if not isinstance(self.rejected, Mapping):
            raise ContextContractError("invalid_memory_retrieval")
        rejected = {}
        for key, value in self.rejected.items():
            if type(key) is not str or not key or type(value) is not int or value < 0:
                raise ContextContractError("invalid_memory_retrieval")
            rejected[key] = value
        object.__setattr__(self, "rejected", MappingProxyType(dict(sorted(rejected.items()))))


def _copy_memory_retrieval_result(value):
    if type(value) is not MemoryRetrievalResult:
        raise ContextContractError("invalid_memory_retrieval")
    try:
        return MemoryRetrievalResult(
            source_revision=value.source_revision,
            status=value.status,
            records=value.records,
            semantic_used=value.semantic_used,
            fallback_used=value.fallback_used,
            rejected=value.rejected,
            query_fingerprint=value.query_fingerprint,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_memory_retrieval") from exc


@dataclass(frozen=True, slots=True)
class MemoryConflictOutcome:
    subject: str
    subject_role: str
    conflict_key: str
    status: str
    selected_record_id: str | None = None
    omitted_record_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if self.status not in {
            "conflict_loser",
            "conflict_unresolved",
            "duplicate",
            "historical_excluded",
            "superseded",
        }:
            raise ContextContractError("invalid_memory_conflict")
        for name in ("subject", "subject_role", "conflict_key"):
            if type(getattr(self, name)) is not str:
                raise ContextContractError("invalid_memory_conflict")
        if self.selected_record_id is not None and (
            type(self.selected_record_id) is not str or not self.selected_record_id
        ):
            raise ContextContractError("invalid_memory_conflict")
        ids = tuple(self.omitted_record_ids)
        if any(type(item) is not str or not item for item in ids):
            raise ContextContractError("invalid_memory_conflict")
        object.__setattr__(self, "omitted_record_ids", ids)


@dataclass(frozen=True, slots=True)
class MemoryConflictResolution:
    records: tuple[MemoryRetrievalRecord, ...]
    outcomes: tuple[MemoryConflictOutcome, ...]

    def __post_init__(self):
        object.__setattr__(self, "records", tuple(self.records))
        object.__setattr__(self, "outcomes", tuple(self.outcomes))


_MEMORY_AUTHORITY_TIER = {
    "explicit_verified_correction": 5,
    "verified_current_state": 4,
    "grounded_session_fact": 3,
    "active_durable_fact": 2,
    "historical_fact": 1,
}


def _memory_conflict_domain(record):
    if record.semantic_role not in {SemanticRole.FACT, SemanticRole.STATE}:
        return None
    if (
        record.subject is None
        or record.subject_role is None
        or record.conflict_key is None
        or record.canonical_value is None
        or record.authority not in _MEMORY_AUTHORITY_TIER
    ):
        return None
    return (record.subject, record.subject_role, record.conflict_key.casefold())


def resolve_memory_conflicts(records, *, temporal_intent=False):
    """Resolve only source-typed FACT/STATE domains without freshness tie-breaks."""

    if type(temporal_intent) is not bool:
        raise ContextContractError("invalid_temporal_intent")
    values = tuple(records)
    if any(type(record) is not MemoryRetrievalRecord for record in values):
        raise ContextContractError("invalid_memory_record")
    eligible = []
    outcomes = []
    for record in values:
        domain = _memory_conflict_domain(record)
        subject, subject_role, conflict_key = domain or ("", "", "")
        if record.semantic_status == "superseded":
            outcomes.append(MemoryConflictOutcome(
                subject, subject_role, conflict_key, "superseded",
                omitted_record_ids=(record.record_id,),
            ))
            continue
        is_historical = (
            record.semantic_status == "historical"
            or record.authority == "historical_fact"
        )
        if is_historical and not (
            temporal_intent and record.temporal_selected
        ):
            outcomes.append(MemoryConflictOutcome(
                subject, subject_role, conflict_key, "historical_excluded",
                omitted_record_ids=(record.record_id,),
            ))
            continue
        eligible.append(record)

    groups = {}
    for record in eligible:
        domain = _memory_conflict_domain(record)
        if domain is not None:
            groups.setdefault(domain, []).append(record)
    omitted = set()
    for domain, group in groups.items():
        if len(group) < 2:
            continue
        highest = max(_MEMORY_AUTHORITY_TIER[record.authority] for record in group)
        leaders = [
            record for record in group
            if _MEMORY_AUTHORITY_TIER[record.authority] == highest
        ]
        values_at_tier = {record.canonical_value for record in leaders}
        if len(values_at_tier) != 1:
            ids = tuple(sorted(record.record_id for record in group))
            omitted.update(ids)
            outcomes.append(MemoryConflictOutcome(
                domain[0], domain[1], domain[2], "conflict_unresolved",
                omitted_record_ids=ids,
            ))
            continue
        canonical_value = leaders[0].canonical_value
        winner = min(
            (record for record in leaders if record.canonical_value == canonical_value),
            key=lambda record: (record.record_id, record.source_event_id),
        )
        for record in group:
            if record is winner:
                continue
            omitted.add(record.record_id)
            status = "duplicate" if record.canonical_value == canonical_value else "conflict_loser"
            outcomes.append(MemoryConflictOutcome(
                domain[0], domain[1], domain[2], status,
                selected_record_id=winner.record_id,
                omitted_record_ids=(record.record_id,),
            ))
    selected = tuple(record for record in eligible if record.record_id not in omitted)
    return MemoryConflictResolution(selected, tuple(outcomes))


def _copy_memory_evidence(value):
    if type(value) is not FrozenGroundingEvidence:
        raise ContextContractError("invalid_grounding_evidence")
    if type(value.snippets) is not tuple or type(value.source_event_ids) is not tuple:
        raise ContextContractError("invalid_grounding_evidence")
    try:
        return FrozenGroundingEvidence(
            status=value.status,
            confidence=value.confidence,
            lane_visible_to=value.lane_visible_to,
            evidence_strength=value.evidence_strength,
            snippets=value.snippets,
            has_legacy_timestamp=value.has_legacy_timestamp,
            notes=value.notes,
            source_event_ids=value.source_event_ids,
        )
    except AttributeError as exc:
        raise ContextContractError("invalid_grounding_evidence") from exc


@dataclass(frozen=True, slots=True, weakref_slot=True)
class MemoryContextBundle:
    retrieval: MemoryRetrievalResult
    evidence: FrozenGroundingEvidence | None
    sections: tuple[ContextSection, ...]
    verifier_context: FrozenMapping

    def __post_init__(self):
        retrieval = _copy_memory_retrieval_result(self.retrieval)
        object.__setattr__(self, "retrieval", retrieval)
        evidence = None if self.evidence is None else _copy_memory_evidence(self.evidence)
        object.__setattr__(self, "evidence", evidence)
        if type(self.sections) is not tuple:
            raise ContextContractError("invalid_section")
        sections = self.sections
        sections = tuple(_copy_context_section(section) for section in sections)
        ids = [section.id for section in sections]
        if len(ids) != len(set(ids)):
            raise ContextContractError("duplicate_section_id")
        if any(section.id not in {
            "preference.rules.v1", "memory.retrieval.v1", "memory.grounding.v1"
        } for section in sections):
            raise ContextContractError("invalid_section")
        if any(section.revision != retrieval.source_revision for section in sections):
            raise ContextContractError("memory_source_revision_mismatch")
        grounding_sections = sum(
            section.id == "memory.grounding.v1" for section in sections
        )
        if (evidence is None and grounding_sections) or (
            evidence is not None and grounding_sections != 1
        ):
            raise ContextContractError("memory_bundle_evidence_mismatch")
        object.__setattr__(self, "sections", sections)
        if type(self.verifier_context) is not FrozenMapping:
            raise ContextContractError("mutable_bundle_payload")
        try:
            verifier_context = freeze_payload(self.verifier_context)
        except ContextContractError as exc:
            raise ContextContractError("mutable_bundle_payload") from exc
        if type(verifier_context) is not FrozenMapping or not _is_frozen_payload(verifier_context):
            raise ContextContractError("mutable_bundle_payload")
        object.__setattr__(self, "verifier_context", verifier_context)
        if verifier_context.get("source_revision") != retrieval.source_revision:
            raise ContextContractError("memory_source_revision_mismatch")

        records_by_id = {record.record_id: record for record in retrieval.records}
        records_by_event = {record.source_event_id: record for record in retrieval.records}
        for section in sections:
            for provenance in section.provenance:
                if provenance.record_id is None or provenance.source_event_id is None:
                    raise ContextContractError("invalid_memory_bundle")
                record = records_by_id.get(provenance.record_id)
                if (
                    record is None
                    or records_by_event.get(provenance.source_event_id) != record
                    or provenance.source_id != record.source
                ):
                    raise ContextContractError("invalid_memory_bundle")

        for key, index in (("record_ids", records_by_id), ("source_event_ids", records_by_event)):
            values = verifier_context.get(key, ())
            if type(values) is not tuple or any(type(item) is not str for item in values):
                raise ContextContractError("invalid_memory_bundle")
            if len(values) != len(set(values)) or any(item not in index for item in values):
                raise ContextContractError("invalid_memory_bundle")
        expected_status = None if evidence is None else evidence.status
        if verifier_context.get("evidence_status") != expected_status:
            raise ContextContractError("invalid_memory_bundle")
        if evidence is not None and any(
            source_event_id not in records_by_event
            for source_event_id in evidence.source_event_ids
        ):
            raise ContextContractError("invalid_memory_bundle")


_MEMORY_BUNDLE_ATTESTATIONS = {}
_MAPPING_PROXY_CLASS = type(MappingProxyType({}))


def _require_exact_frozen_types(value):
    if value is None or type(value) in (bool, int, float, str):
        return
    if type(value) is tuple:
        for item in value:
            _require_exact_frozen_types(item)
        return
    if type(value) is FrozenMapping:
        if type(value._items) is not tuple:
            raise ContextContractError("invalid_memory_bundle_attestation")
        for entry in value._items:
            if type(entry) is not tuple or len(entry) != 2 or type(entry[0]) is not str:
                raise ContextContractError("invalid_memory_bundle_attestation")
            _require_exact_frozen_types(entry[1])
        return
    raise ContextContractError("invalid_memory_bundle_attestation")


def _require_exact_memory_bundle_types(bundle):
    if type(bundle) is not MemoryContextBundle:
        raise ContextContractError("invalid_memory_bundle_attestation")
    retrieval = bundle.retrieval
    if (
        type(retrieval) is not MemoryRetrievalResult
        or type(retrieval.records) is not tuple
        or type(retrieval.rejected) is not _MAPPING_PROXY_CLASS
    ):
        raise ContextContractError("invalid_memory_bundle_attestation")
    _copy_memory_retrieval_result(retrieval)
    for record in retrieval.records:
        if type(record.visibility) is not frozenset:
            raise ContextContractError("invalid_memory_bundle_attestation")
    if bundle.evidence is not None:
        _copy_memory_evidence(bundle.evidence)
    if type(bundle.sections) is not tuple:
        raise ContextContractError("invalid_memory_bundle_attestation")
    for section in bundle.sections:
        if (
            type(section) is not ContextSection
            or type(section.visibility) is not frozenset
            or type(section.provenance) is not tuple
            or type(section.source) is not SourceRef
            or any(type(item) is not ProvenanceRef for item in section.provenance)
        ):
            raise ContextContractError("invalid_memory_bundle_attestation")
        _require_exact_frozen_types(section.payload)
    if type(bundle.verifier_context) is not FrozenMapping:
        raise ContextContractError("invalid_memory_bundle_attestation")
    _require_exact_frozen_types(bundle.verifier_context)


def _memory_record_fingerprint(record):
    return (
        "memory_record.v2",
        record.record_id,
        record.source_event_id,
        record.source,
        record.memory_type,
        record.semantic_role,
        record.text,
        "frozenset",
        tuple(sorted(lane.value for lane in record.visibility)),
        record.confidence,
        record.relevance,
        record.observed_at,
        record.expires_at,
        record.subject,
        record.subject_role,
        record.conflict_key,
        record.canonical_value,
        record.authority,
        record.semantic_status,
        record.temporal_selected,
    )


def _memory_retrieval_fingerprint(retrieval):
    return (
        "memory_retrieval.v2",
        retrieval.source_revision,
        retrieval.status,
        "tuple",
        tuple(_memory_record_fingerprint(record) for record in retrieval.records),
        retrieval.semantic_used,
        retrieval.fallback_used,
        tuple(sorted(retrieval.rejected.items())),
        retrieval.query_fingerprint,
    )


def _memory_evidence_fingerprint(evidence):
    if evidence is None:
        return None
    return (
        evidence.status,
        evidence.confidence,
        evidence.lane_visible_to,
        evidence.evidence_strength,
        evidence.snippets,
        evidence.has_legacy_timestamp,
        evidence.notes,
        evidence.source_event_ids,
    )


def _memory_section_fingerprint(section):
    return (
        "memory_section.v2",
        section.id,
        section.lifetime,
        section.semantic_role,
        section.freshness,
        "frozenset",
        tuple(sorted(lane.value for lane in section.visibility)),
        (section.source.owner, section.source.adapter, section.source.projection),
        section.revision,
        section.authority,
        section.observed_at,
        section.expires_at,
        section.conflict_key,
        section.dedupe_key,
        section.max_tokens,
        section.payload,
        section.formatter_version,
        section.required,
        section.budget_class,
        section.semantic_status,
        "tuple",
        tuple(
            (item.source_id, item.source_event_id, item.record_id)
            for item in section.provenance
        ),
        section.relevance,
    )


def _memory_bundle_fingerprint(bundle):
    return _structural_digest((
        "memory_context_bundle.v2",
        _memory_retrieval_fingerprint(bundle.retrieval),
        _memory_evidence_fingerprint(bundle.evidence),
        tuple(_memory_section_fingerprint(section) for section in bundle.sections),
        bundle.verifier_context,
    ))


def _register_memory_bundle_attestation(bundle):
    _require_exact_memory_bundle_types(bundle)
    object_id = id(bundle)

    def cleanup(reference):
        current = _MEMORY_BUNDLE_ATTESTATIONS.get(object_id)
        if current is not None and current[0] is reference:
            _MEMORY_BUNDLE_ATTESTATIONS.pop(object_id, None)

    reference = weakref.ref(bundle, cleanup)
    _MEMORY_BUNDLE_ATTESTATIONS[object_id] = (
        reference,
        _memory_bundle_fingerprint(bundle),
    )


def require_memory_context_bundle(value):
    try:
        _require_exact_memory_bundle_types(value)
    except (AttributeError, TypeError, ContextContractError) as exc:
        raise ContextContractError("invalid_memory_bundle_attestation") from exc
    entry = _MEMORY_BUNDLE_ATTESTATIONS.get(id(value))
    if entry is None or entry[0]() is not value:
        raise ContextContractError("invalid_memory_bundle_attestation")
    try:
        fingerprint = _memory_bundle_fingerprint(value)
    except (AttributeError, TypeError, ContextContractError) as exc:
        raise ContextContractError("invalid_memory_bundle_attestation") from exc
    if not hmac.compare_digest(entry[1], fingerprint):
        raise ContextContractError("invalid_memory_bundle_attestation")
    return value


def _retrieval_record_from_candidate(candidate):
    lane = str(getattr(candidate, "lane", "") or "")
    visibility = {
        "private": frozenset({Lane.PRIVATE_OWNER}),
        "private_owner": frozenset({Lane.PRIVATE_OWNER}),
        "public": frozenset({Lane.PUBLIC_STAGE}),
        "public_stage": frozenset({Lane.PUBLIC_STAGE}),
        "public_viewer": frozenset({Lane.PUBLIC_STAGE}),
        "operator": frozenset({Lane.OPERATOR_BACKSTAGE}),
        "operator_backstage": frozenset({Lane.OPERATOR_BACKSTAGE}),
    }.get(lane, frozenset())
    raw_role = str(getattr(candidate, "semantic_role", "") or "").casefold()
    role = {
        "fact": SemanticRole.FACT,
        "state": SemanticRole.STATE,
        "evidence": SemanticRole.EVIDENCE,
        "instruction": SemanticRole.INSTRUCTION,
        "history": SemanticRole.HISTORY,
        "identity": SemanticRole.IDENTITY,
    }.get(raw_role, SemanticRole.INSTRUCTION if getattr(candidate, "memory_type", "") == "preference" else SemanticRole.EVIDENCE)
    return MemoryRetrievalRecord(
        record_id=str(candidate.id),
        source_event_id=str(candidate.source_event_id),
        source=str(candidate.source or "memory_v2"),
        memory_type=str(getattr(candidate, "memory_type", "") or "memory"),
        semantic_role=role,
        text=str(candidate.text),
        visibility=visibility,
        confidence=float(candidate.confidence),
        relevance=float(candidate.score),
        observed_at=getattr(candidate, "observed_at", None) or float(getattr(candidate, "created_at", 0.0) or 0.0) or None,
        expires_at=getattr(candidate, "expires_at", None),
        subject=getattr(candidate, "subject", None),
        subject_role=getattr(candidate, "subject_role", None),
        conflict_key=getattr(candidate, "conflict_key", None),
        canonical_value=getattr(candidate, "canonical_value", None),
        authority=getattr(candidate, "authority", None),
        semantic_status=getattr(candidate, "semantic_status", None),
        temporal_selected=bool(getattr(candidate, "temporal_selected", False)),
    )


def retrieve_context_memory(snapshot, request):
    """Default provider-free adapter over the already frozen source snapshot."""

    request = require_resolved_context_request(request)
    if request.scope.lane is not Lane.PRIVATE_OWNER:
        raise ContextContractError("private_source_capture_denied")
    if type(snapshot) is not SourceSnapshot or snapshot.source != "private_memory":
        raise ContextContractError("invalid_source_snapshot")
    from nana.runtime.controlled_memory_retrieval import RetrievalScope, retrieve_memory_candidates
    try:
        from nana import config
        semantic_enabled = bool(getattr(config, "MEMORY_SEMANTIC_RETRIEVAL_ENABLED", False))
    except Exception:
        semantic_enabled = False
    result = retrieve_memory_candidates(
        _thaw(snapshot.payload),
        request.current_input,
        scope=RetrievalScope.from_mapping({"lane": "private_owner"}),
        limit=20,
        max_chars=12_000,
        semantic_enabled=semantic_enabled,
        semantic_adapter=None,
        now=request.captured_wall_time,
        canonical_strict=True,
    )
    return MemoryRetrievalResult(
        source_revision=snapshot.revision,
        status=result.status,
        records=tuple(_retrieval_record_from_candidate(item) for item in result.candidates),
        semantic_used=result.semantic_used,
        fallback_used=result.fallback_used,
        rejected=result.rejected,
        query_fingerprint=result.query_fingerprint,
    )


def _memory_payloads(records, uncertainties):
    preferences = [record for record in records if record.semantic_role is SemanticRole.INSTRUCTION]
    facts = [record for record in records if record.semantic_role is not SemanticRole.INSTRUCTION]
    preference_payload = None
    if preferences:
        preference_payload = {
            "preferences": tuple(
                {"kind": record.memory_type, "text": record.text}
                for record in preferences
            )
        }
    retrieval_payload = None
    if facts or uncertainties:
        retrieval_payload = {
            "records": tuple(
                {
                    "kind": record.semantic_role.value,
                    "text": record.text,
                    "confidence": record.confidence,
                    **({"semantic_status": record.semantic_status} if record.semantic_status else {}),
                }
                for record in facts
            ),
            **(
                {
                    "uncertainties": tuple(
                        {
                            "subject": item.subject,
                            "subject_role": item.subject_role,
                            "conflict_key": item.conflict_key,
                            "status": "conflict_unresolved",
                        }
                        for item in uncertainties
                    )
                }
                if uncertainties else {}
            ),
        }
    return preference_payload, retrieval_payload


def _memory_evidence(request, records, uncertainties):
    if uncertainties:
        return FrozenGroundingEvidence(
            "partial", 0.35, "private_only", "weak", (), False,
            "conflict_unresolved", (),
        )
    evidence_records = tuple(
        record for record in records
        if record.semantic_role in {SemanticRole.FACT, SemanticRole.STATE, SemanticRole.EVIDENCE}
    )
    try:
        from nana.runtime.memory_grounding import MemoryClaimDetector
        is_claim = (
            request.grounding_intent
            or MemoryClaimDetector().is_memory_claim(request.current_input)
        )
    except (ImportError, AttributeError):
        # Minimal offline fixtures may expose only the older grounding surface.
        # The sealed request flag is already a source-owned query classification.
        is_claim = request.grounding_intent
    if not is_claim:
        return None
    if not evidence_records:
        return FrozenGroundingEvidence(
            "user_claim_only", 0.25, "private_only", "weak", (), False,
            "no_candidates", (),
        )
    ranked = sorted(evidence_records, key=lambda item: (-item.relevance, item.record_id))[:3]
    top = ranked[0]
    strong = (
        top.relevance >= 0.75
        and top.confidence >= 0.75
        and top.observed_at is not None
        and bool(top.source_event_id)
    )
    status = "found" if strong else "partial"
    confidence = min(0.95, top.relevance) if strong else min(0.74, max(0.35, top.relevance))
    return FrozenGroundingEvidence(
        status,
        confidence,
        "private_only",
        "strong" if strong else "moderate",
        tuple(item.text for item in ranked),
        not bool(top.observed_at),
        f"top_score={top.relevance:.2f}",
        tuple(item.source_event_id for item in ranked),
    )


def _memory_model_projection(request, records, visible_uncertainties, all_uncertainties):
    from nana.runtime.context_compiler import _render_payload

    preference_payload, retrieval_payload = _memory_payloads(
        records, visible_uncertainties
    )
    evidence = _memory_evidence(request, records, all_uncertainties)
    grounding_payload = (
        None
        if evidence is None
        else evidence.to_prompt_block(include_details=False)
    )
    payloads = (preference_payload, retrieval_payload, grounding_payload)
    rendered_characters = sum(
        len(_render_payload(freeze_payload(payload)))
        for payload in payloads
        if payload is not None
    )
    return (
        preference_payload,
        retrieval_payload,
        evidence,
        grounding_payload,
        rendered_characters,
    )


def build_memory_context_bundle(request, snapshot, *, retriever=None, redact=lambda value: value):
    """Build all canonical memory consumers from exactly one retrieval call."""

    request = require_resolved_context_request(request)
    if request.scope.lane is not Lane.PRIVATE_OWNER:
        raise ContextContractError("private_source_capture_denied")
    if (
        type(snapshot) is not SourceSnapshot
        or snapshot.source != "private_memory"
        or snapshot.captured_at != request.captured_wall_time
    ):
        raise ContextContractError("invalid_source_snapshot")
    selected_retriever = retrieve_context_memory if retriever is None else retriever
    result = selected_retriever(snapshot, request)
    if type(result) is not MemoryRetrievalResult:
        raise ContextContractError("invalid_memory_retrieval")
    if result.source_revision != snapshot.revision:
        raise ContextContractError("memory_source_revision_mismatch")
    if any(request.scope.lane not in record.visibility for record in result.records):
        raise ContextContractError("memory_visibility_mismatch")
    from nana.runtime.controlled_memory_retrieval import _contains_secret
    if any(
        _contains_secret(value)
        for record in result.records
        for value in (
            record.record_id,
            record.source_event_id,
            record.text,
            record.source,
            record.memory_type,
            record.semantic_role.value,
            record.subject,
            record.subject_role,
            record.conflict_key,
            record.canonical_value,
            record.authority,
            record.semantic_status,
        )
        if value is not None
    ):
        raise ContextContractError("memory_secret_metadata")
    resolution = resolve_memory_conflicts(
        result.records, temporal_intent=request.temporal_intent
    )
    raw_uncertainties = tuple(
        item for item in resolution.outcomes if item.status == "conflict_unresolved"
    )

    def redact_optional(value):
        return None if value is None else redact(value)

    uncertainties = tuple(
        replace(
            item,
            subject=redact(item.subject),
            subject_role=redact(item.subject_role),
            conflict_key=redact(item.conflict_key),
        )
        for item in raw_uncertainties
    )
    safe_records = tuple(
        replace(
            record,
            text=redact(record.text),
            memory_type=redact(record.memory_type),
            subject=redact_optional(record.subject),
            subject_role=redact_optional(record.subject_role),
            conflict_key=redact_optional(record.conflict_key),
            canonical_value=redact_optional(record.canonical_value),
            authority=redact_optional(record.authority),
            semantic_status=redact_optional(record.semantic_status),
        )
        for record in resolution.records
    )

    visible_uncertainties = []
    base_projection = _memory_model_projection(
        request, (), (), uncertainties
    )
    if base_projection[-1] > 1_200:
        raise ContextContractError("memory_bundle_projection_overflow")
    for outcome in uncertainties[:3]:
        proposal = (*visible_uncertainties, outcome)
        projection = _memory_model_projection(
            request, (), proposal, uncertainties
        )
        if projection[-1] <= 1_200:
            visible_uncertainties.append(outcome)

    bounded = []
    for record in safe_records:
        if len(bounded) >= 3:
            break
        proposal = (*bounded, record)
        projection = _memory_model_projection(
            request,
            proposal,
            tuple(visible_uncertainties),
            uncertainties,
        )
        if projection[-1] <= 1_200:
            bounded.append(record)
    records = tuple(bounded)
    (
        preference_payload,
        retrieval_payload,
        evidence,
        grounding_payload,
        rendered_characters,
    ) = _memory_model_projection(
        request,
        records,
        tuple(visible_uncertainties),
        uncertainties,
    )
    if rendered_characters > 1_200:
        raise ContextContractError("memory_bundle_projection_overflow")
    sections = []

    def append_section(section_id, role, payload, section_records, *, required=False):
        if payload is None:
            return
        provenance = tuple(
            ProvenanceRef(record.source, record.source_event_id, record.record_id)
            for record in section_records
        )
        sections.append(ContextSection(
            id=section_id,
            lifetime=Lifetime.DURABLE if section_id == "preference.rules.v1" else Lifetime.TURN,
            semantic_role=role,
            freshness=Freshness.FRESH,
            visibility=frozenset({Lane.PRIVATE_OWNER}),
            source=SourceRef("private_memory", "memory_context_bundle.v1", section_id),
            revision=result.source_revision,
            authority=None,
            observed_at=request.captured_wall_time,
            expires_at=None,
            conflict_key=None,
            dedupe_key=f"{section_id}:{result.source_revision}",
            max_tokens=1_500,
            payload=payload,
            formatter_version="memory_context_bundle.v1",
            required=required,
            budget_class="grounding" if section_id == "memory.grounding.v1" else "memory",
            semantic_status="active",
            provenance=provenance,
            relevance=1.0,
        ))

    preferences = tuple(record for record in records if record.semantic_role is SemanticRole.INSTRUCTION)
    facts = tuple(record for record in records if record.semantic_role is not SemanticRole.INSTRUCTION)
    append_section("preference.rules.v1", SemanticRole.INSTRUCTION, preference_payload, preferences)
    append_section("memory.retrieval.v1", SemanticRole.EVIDENCE, retrieval_payload, facts)
    if evidence is not None:
        append_section(
            "memory.grounding.v1",
            SemanticRole.EVIDENCE,
            grounding_payload,
            facts,
            required=True,
        )
    verifier_context = freeze_payload({
        "source_revision": result.source_revision,
        "record_ids": tuple(record.record_id for record in records),
        "source_event_ids": tuple(record.source_event_id for record in facts),
        "evidence_status": None if evidence is None else evidence.status,
        "conflicts": tuple(
            {
                "subject": item.subject,
                "subject_role": item.subject_role,
                "conflict_key": item.conflict_key,
                "status": item.status,
            }
            for item in uncertainties
        ),
    })
    bundle = MemoryContextBundle(
        result,
        evidence,
        tuple(sections),
        verifier_context,
    )
    _register_memory_bundle_attestation(bundle)
    return bundle


def _thaw(value):
    if isinstance(value, FrozenMapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _normalized_text(value):
    return unicodedata.normalize(
        "NFC",
        " ".join(str(value or "").split()).strip(),
    )


def _bounded_text(value, limit):
    text = _normalized_text(value)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip()


def _semantic_text_identity(value):
    return _normalized_text(value).casefold()


def _semantic_identity(*values):
    digest = hashlib.sha256()
    for value in values:
        encoded = str(value).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


_EXPRESSION_TONES = frozenset(
    {
        "steady",
        "strained_focus",
        "focused",
        "quiet_tired",
        "bright_playful",
        "warm",
        "awake",
    }
)
_EXPRESSION_MODES = frozenset({"chill", "focus", "technical", "social"})
_EXPRESSION_CLAMPS = frozenset({"open", "moderate", "strict"})

_OPEN_GUIDANCE = (
    "Answer the direct task first. Keep the response warm and natural, use "
    "playfulness only when it fits, and do not invent memories or let stale "
    "context open a topic."
)
_MODERATE_GUIDANCE = (
    "Prioritize the direct task. Keep warmth restrained and reduce humor, "
    "emoji, exaggeration, callbacks, and roleplay. Do not let stale context "
    "or mood open a topic."
)
_STRICT_GUIDANCE = (
    "Prioritize the direct task. Be clear, concise, and actionable. Minimize "
    "humor, emoji, exaggeration, callbacks, and roleplay; do not let stale "
    "context or mood open a topic."
)
_PUBLIC_GUIDANCE = (
    "Use a warm, concise public-stage voice without private intimacy. Reduce "
    "callbacks and roleplay, and never imply access to private owner context "
    "or memories."
)
_OPERATOR_GUIDANCE = (
    "Be calm, technical, concise, and actionable. Minimize humor, emoji, "
    "exaggeration, callbacks, and roleplay; report uncertainty directly."
)

if any(
    len(value) > 240
    for value in (
        _OPEN_GUIDANCE,
        _MODERATE_GUIDANCE,
        _STRICT_GUIDANCE,
        _PUBLIC_GUIDANCE,
        _OPERATOR_GUIDANCE,
    )
):
    raise RuntimeError("expression_guidance_template_too_long")


def _expression_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    if not math.isfinite(number):
        number = 0.0
    return round(max(0.0, min(1.0, number)), 2)


def _expression_label(value, allowed, fallback):
    label = str(value or "").strip().lower()
    return label if label in allowed else fallback


def _private_expression_guidance(mode, clamp):
    if mode in {"focus", "technical"} or clamp == "strict":
        return _STRICT_GUIDANCE
    if clamp == "moderate":
        return _MODERATE_GUIDANCE
    return _OPEN_GUIDANCE


def _build_expression_section(request, section_id, payload, *, required):
    request = require_resolved_context_request(request)
    lane = request.scope.lane
    expected_id = {
        Lane.PRIVATE_OWNER: "expression.private.v1",
        Lane.PUBLIC_STAGE: "expression.public.v1",
        Lane.OPERATOR_BACKSTAGE: "expression.operator.v1",
    }[lane]
    if section_id != expected_id:
        raise ContextContractError("expression_lane_mismatch")
    revision = "expression-" + _semantic_identity(
        section_id,
        payload["lane"],
        payload["tone"],
        payload["mode"],
        payload["energy"],
        payload["warmth"],
        payload["playfulness"],
        payload["focus"],
        payload["tension"],
        payload["assertiveness"],
        payload["intimacy"],
        payload["clamp"],
        payload["guidance"],
    )
    return ContextSection(
        id=section_id,
        lifetime=Lifetime.TURN,
        semantic_role=SemanticRole.STATE,
        freshness=Freshness.FRESH,
        visibility=frozenset({lane}),
        source=SourceRef(
            owner="expression",
            adapter=f"{section_id}.adapter",
            projection=section_id,
        ),
        revision=revision,
        authority=None,
        observed_at=request.captured_wall_time,
        expires_at=None,
        conflict_key=None,
        dedupe_key=revision,
        max_tokens=300,
        payload=payload,
        formatter_version="expression_view.v1",
        required=required,
        budget_class="expression",
        semantic_status="active",
        provenance=(),
        relevance=1.0,
    )


def build_private_expression_section(snapshot, request):
    """Project the turn's frozen private owners into one ExpressionView."""

    from nana.runtime.context_runtime import RuntimeContextSnapshot

    request = require_resolved_context_request(request)
    if request.scope.lane is not Lane.PRIVATE_OWNER:
        raise ContextContractError("private_source_capture_denied")
    if (
        type(snapshot) is not RuntimeContextSnapshot
        or snapshot.request is not request
        or snapshot.expression is None
    ):
        raise ContextContractError("invalid_source_snapshot")
    values = _thaw(snapshot.expression.payload)
    if not isinstance(values, dict):
        raise ContextContractError("invalid_source_snapshot")
    mood = values.get("mood")
    affect = values.get("affect")
    governor = values.get("persona")
    if not all(isinstance(value, dict) for value in (mood, affect, governor)):
        raise ContextContractError("invalid_source_snapshot")

    tone = _expression_label(mood.get("tone"), _EXPRESSION_TONES, "steady")
    mode = _expression_label(governor.get("mode"), _EXPRESSION_MODES, "chill")
    clamp = _expression_label(
        governor.get("clamp"),
        _EXPRESSION_CLAMPS,
        "moderate",
    )
    payload = {
        "lane": "private_owner",
        "tone": tone,
        "mode": mode,
        "energy": _expression_number(mood.get("energy")),
        "warmth": _expression_number(affect.get("warmth")),
        "playfulness": _expression_number(affect.get("playfulness")),
        "focus": _expression_number(mood.get("focus")),
        "tension": _expression_number(mood.get("tension")),
        "assertiveness": _expression_number(affect.get("assertiveness")),
        "intimacy": _expression_number(affect.get("intimacy")),
        "clamp": clamp,
        "guidance": _private_expression_guidance(mode, clamp),
    }
    return _build_expression_section(
        request,
        "expression.private.v1",
        payload,
        required=True,
    )


def build_public_expression_section(request):
    """Build the fixed public projection without any private capability."""

    request = require_resolved_context_request(request)
    if request.scope.lane is not Lane.PUBLIC_STAGE:
        raise ContextContractError("expression_lane_mismatch")
    return _build_expression_section(
        request,
        "expression.public.v1",
        {
            "lane": "public_stage",
            "tone": "bright_playful",
            "mode": "social",
            "energy": 0.58,
            "warmth": 0.5,
            "playfulness": 0.58,
            "focus": 0.55,
            "tension": 0.15,
            "assertiveness": 0.5,
            "intimacy": 0.22,
            "clamp": "moderate",
            "guidance": _PUBLIC_GUIDANCE,
        },
        required=False,
    )


def build_operator_expression_section(request):
    """Build the fixed backstage projection without any private capability."""

    request = require_resolved_context_request(request)
    if request.scope.lane is not Lane.OPERATOR_BACKSTAGE:
        raise ContextContractError("expression_lane_mismatch")
    return _build_expression_section(
        request,
        "expression.operator.v1",
        {
            "lane": "operator_backstage",
            "tone": "focused",
            "mode": "technical",
            "energy": 0.45,
            "warmth": 0.25,
            "playfulness": 0.16,
            "focus": 0.85,
            "tension": 0.15,
            "assertiveness": 0.62,
            "intimacy": 0.05,
            "clamp": "strict",
            "guidance": _OPERATOR_GUIDANCE,
        },
        required=False,
    )


def build_current_situation_section(snapshot, request):
    """Project runtime/browser/awareness into one bounded canonical section."""

    from nana.runtime.context_runtime import RuntimeContextSnapshot

    request = require_resolved_context_request(request)
    if request.scope.lane is not Lane.PRIVATE_OWNER:
        raise ContextContractError("private_source_capture_denied")
    if type(snapshot) is not RuntimeContextSnapshot or snapshot.request is not request:
        raise ContextContractError("invalid_source_snapshot")

    browser_source = snapshot.browser_state
    if (
        browser_source.observed_at is None
        or browser_source.freshness in {Freshness.UNKNOWN, Freshness.EXPIRED}
    ):
        return None

    runtime = _thaw(snapshot.runtime_context.payload)
    awareness = _thaw(snapshot.live_awareness.payload)
    if not isinstance(runtime, dict) or not isinstance(awareness, dict):
        raise ContextContractError("invalid_source_snapshot")

    focus_source = _bounded_text(awareness.get("focus_source"), 32) or "none"
    raw_focus = _normalized_text(awareness.get("focus_text"))
    raw_title = _normalized_text(awareness.get("browser_title"))
    raw_derived = _normalized_text(
        awareness.get("browser_local_summary")
        or awareness.get("browser_meta_description")
    )
    focus_text = _bounded_text(raw_focus, 360)
    title = _bounded_text(raw_title, 200)
    url = _bounded_text(awareness.get("browser_url"), 220)
    focus_identity = _semantic_text_identity(raw_focus)
    title_identity = _semantic_text_identity(raw_title)
    derived_identity = _semantic_text_identity(raw_derived)

    browser = {
        "availability": "available" if awareness.get("browser_available") else "unavailable",
        "freshness": browser_source.freshness.value,
        "kind": _bounded_text(awareness.get("browser_kind"), 48) or "unknown",
    }
    if title and not (
        focus_source == "title"
        or (focus_identity and title_identity == focus_identity)
    ):
        browser["title"] = title
    if url:
        browser["url"] = url

    runtime_freshness = snapshot.runtime_context.freshness
    runtime_status = (
        "latest_known"
        if runtime_freshness in {Freshness.UNKNOWN, Freshness.STALE}
        else "unavailable"
        if runtime_freshness is Freshness.EXPIRED
        else "current"
    )
    focus = {
        "source": focus_source,
        "text": focus_text,
        "confidence": float(awareness.get("focus_confidence") or 0.0),
        "confirmed_active": bool(
            awareness.get("is_confirmed_active")
            and awareness.get("browser_effective")
        ),
    }
    payload = {
        "active": {
            "source": "runtime_context",
            "freshness": runtime_freshness.value,
            "status": runtime_status,
            "app": _bounded_text(runtime.get("active_app"), 120),
            "zone": _bounded_text(runtime.get("active_zone"), 64) or "unknown",
            "idle_state": _bounded_text(runtime.get("idle_state"), 32) or "unknown",
            "in_flow": bool(runtime.get("in_flow")),
        },
        "browser": browser,
        "focus": focus,
    }

    derived = _bounded_text(raw_derived, 220)
    stronger = {
        value
        for value in (focus_identity, title_identity)
        if value
    }
    if (
        derived
        and focus_source != "local_summary"
        and derived_identity not in stronger
    ):
        payload["derived_summary"] = derived

    social = _bounded_text(awareness.get("browser_social_vibe"), 480)
    if (
        focus_source == "social_post"
        and social
        and _semantic_text_identity(social) not in stronger
        and _semantic_text_identity(social) != derived_identity
    ):
        payload["social"] = social
    if request.temporal_intent:
        payload["captured_at"] = snapshot.captured_at

    revision = "current-situation-" + _semantic_identity(
        snapshot.source_revision("runtime_context"),
        snapshot.source_revision("browser_state"),
        snapshot.source_revision("live_awareness"),
        focus_source,
        focus_text.casefold(),
    )
    status = (
        "latest_known"
        if browser_source.freshness is Freshness.STALE
        else "active"
    )
    return ContextSection(
        id="situation.current.v1",
        lifetime=Lifetime.TURN,
        semantic_role=SemanticRole.STATE,
        freshness=browser_source.freshness,
        visibility=frozenset({Lane.PRIVATE_OWNER}),
        source=SourceRef(
            owner="runtime_state",
            adapter="current_situation.adapter.v1",
            projection="current_situation.v1",
        ),
        revision=revision,
        authority=None,
        observed_at=browser_source.observed_at,
        expires_at=None,
        conflict_key=None,
        dedupe_key="current-situation-" + _semantic_identity(
            revision, focus_source, focus_text.casefold()
        ),
        max_tokens=1000,
        payload=payload,
        formatter_version="current_situation.v1",
        required=False,
        budget_class="current_situation",
        semantic_status=status,
        provenance=(),
        relevance=1.0,
    )


build_current_situation = build_current_situation_section


class ContinuityAuthority(str, Enum):
    CURRENT_BROWSER = "current_browser"
    OLD_DIALOGUE_RECALL = "old_dialogue_recall"
    DURABLE_FACT = "durable_fact"
    ACTIVE_WORK = "active_work"
    RECENT_TEMPORAL = "recent_temporal"
    ORDINARY = "ordinary"


@dataclass(frozen=True, slots=True)
class ContinuityScope:
    lane: Lane
    visibility: frozenset[Lane]
    owner: str
    session_id: str
    platform: str = ""
    room_id: str = ""
    stream_session_id: str = ""
    actor_key: str = ""

    def __post_init__(self):
        if type(self.lane) is not Lane:
            raise ContextContractError("invalid_continuity_scope")
        try:
            visibility = frozenset(self.visibility)
        except TypeError as exc:
            raise ContextContractError("invalid_continuity_scope") from exc
        if not visibility or self.lane not in visibility or any(type(item) is not Lane for item in visibility):
            raise ContextContractError("invalid_continuity_scope")
        if type(self.owner) is not str or not self.owner.strip():
            raise ContextContractError("invalid_continuity_scope")
        if type(self.session_id) is not str or not self.session_id.strip():
            raise ContextContractError("invalid_continuity_scope")
        for name in ("platform", "room_id", "stream_session_id", "actor_key"):
            if type(getattr(self, name)) is not str:
                raise ContextContractError("invalid_continuity_scope")
        if self.lane is Lane.PUBLIC_STAGE and not all(
            getattr(self, name).strip()
            for name in ("platform", "room_id", "stream_session_id", "actor_key")
        ):
            raise ContextContractError("invalid_continuity_scope")
        object.__setattr__(self, "visibility", visibility)


@dataclass(frozen=True, slots=True, kw_only=True)
class ContinuityRecord:
    scope: ContinuityScope
    text: str
    reply_text: str = ""
    event_id: str | None = None
    turn_id: str | None = None
    provenance_record_id: str | None = None
    occurred_at: float | None = None

    def __post_init__(self):
        if type(self.scope) is not ContinuityScope:
            raise ContextContractError("invalid_continuity_record")
        if type(self.text) is not str or type(self.reply_text) is not str:
            raise ContextContractError("invalid_continuity_record")
        for name in ("event_id", "turn_id", "provenance_record_id"):
            value = getattr(self, name)
            if value is not None and (type(value) is not str or not value.strip()):
                raise ContextContractError("invalid_continuity_record")
        if self.occurred_at is not None and (
            type(self.occurred_at) not in (int, float)
            or not math.isfinite(float(self.occurred_at))
            or float(self.occurred_at) < 0
        ):
            raise ContextContractError("invalid_continuity_record")


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionSummaryRecord(ContinuityRecord):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class CheckpointAnchorRecord(ContinuityRecord):
    anchor_kind: str


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenLoopRecord(ContinuityRecord):
    loop_kind: str


@dataclass(frozen=True, slots=True, kw_only=True)
class CompletedTurnRecord(ContinuityRecord):
    text_completion: str
    voice_delivery: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RelevantOldTurnRecord(ContinuityRecord):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class RecentMomentRecord(ContinuityRecord):
    moment_source: str
    active_app: str = ""
    active_zone: str = ""
    title: str = ""
    focus_text: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class PublicTurnRecord(ContinuityRecord):
    speaker: str
    reply_delivery: str


@dataclass(frozen=True, slots=True)
class ContinuityView:
    request: ResolvedContextRequest
    authority: ContinuityAuthority
    authority_order: tuple[str, ...]
    summary: SessionSummaryRecord | None = None
    anchors: tuple[CheckpointAnchorRecord, ...] = ()
    open_loops: tuple[OpenLoopRecord, ...] = ()
    recent_turns: tuple[CompletedTurnRecord, ...] = ()
    relevant_old_turns: tuple[RelevantOldTurnRecord, ...] = ()
    recent_moments: tuple[RecentMomentRecord, ...] = ()
    public_turns: tuple[PublicTurnRecord, ...] = ()

    def __post_init__(self):
        request = require_resolved_context_request(self.request)
        if type(self.authority) is not ContinuityAuthority:
            raise ContextContractError("invalid_continuity_view")
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "authority_order", tuple(self.authority_order))
        for name in (
            "anchors",
            "open_loops",
            "recent_turns",
            "relevant_old_turns",
            "recent_moments",
            "public_turns",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    @property
    def records(self):
        summary = () if self.summary is None else (self.summary,)
        return (
            *summary,
            *self.anchors,
            *self.open_loops,
            *self.recent_turns,
            *self.relevant_old_turns,
            *self.recent_moments,
            *self.public_turns,
        )


def _continuity_scope_key(scope):
    return (
        scope.lane.value,
        tuple(sorted(item.value for item in scope.visibility)),
        scope.owner,
        scope.session_id,
        scope.platform,
        scope.room_id,
        scope.stream_session_id,
        scope.actor_key,
    )


def _continuity_record_key(record):
    scope = _continuity_scope_key(record.scope)
    for kind, value in (
        ("event", record.event_id),
        ("turn", record.turn_id),
        ("record", record.provenance_record_id),
    ):
        if value:
            return (*scope, kind, value)
    digest = _semantic_identity(
        record.scope.session_id,
        _normalized_text(record.text),
        _normalized_text(record.reply_text),
    )
    return (*scope, "legacy_digest", digest)


def _continuity_record_rank(record):
    return {
        CompletedTurnRecord: 70,
        PublicTurnRecord: 65,
        RelevantOldTurnRecord: 60,
        OpenLoopRecord: 50,
        CheckpointAnchorRecord: 40,
        RecentMomentRecord: 30,
        SessionSummaryRecord: 20,
    }.get(type(record), 0) + (1 if record.reply_text else 0)


def _continuity_record_truth(record):
    common = (
        _normalized_text(record.text),
        _normalized_text(record.reply_text),
        record.event_id,
        record.turn_id,
        record.provenance_record_id,
        record.occurred_at,
    )
    if type(record) is CheckpointAnchorRecord:
        return (*common, record.anchor_kind)
    if type(record) is OpenLoopRecord:
        return (*common, record.loop_kind)
    if type(record) is CompletedTurnRecord:
        return (*common, record.text_completion, record.voice_delivery)
    if type(record) is RecentMomentRecord:
        return (
            *common,
            record.moment_source,
            record.active_app,
            record.active_zone,
            record.title,
            record.focus_text,
        )
    if type(record) is PublicTurnRecord:
        return (*common, record.speaker, record.reply_delivery)
    return common


def dedupe_continuity_records(records):
    """Dedupe complete records by scoped stable identity, failing on conflict."""

    if isinstance(records, (str, bytes)):
        raise ContextContractError("invalid_continuity_record")
    result = []
    positions = {}
    for record in tuple(records):
        if not isinstance(record, ContinuityRecord):
            raise ContextContractError("invalid_continuity_record")
        key = _continuity_record_key(record)
        position = positions.get(key)
        if position is None:
            positions[key] = len(result)
            result.append(record)
            continue
        prior = result[position]
        if (
            type(prior) is type(record)
            and key[-2] != "legacy_digest"
            and _continuity_record_truth(prior) != _continuity_record_truth(record)
        ):
            raise ContextContractError("continuity_identity_conflict")
        if _normalized_text(prior.text) != _normalized_text(record.text):
            raise ContextContractError("continuity_identity_conflict")
        prior_reply = _normalized_text(prior.reply_text)
        next_reply = _normalized_text(record.reply_text)
        if prior_reply and next_reply and prior_reply != next_reply:
            raise ContextContractError("continuity_identity_conflict")
        if _continuity_record_rank(record) > _continuity_record_rank(prior):
            result[position] = record
    return tuple(result)


def _fold_continuity_question(value):
    normalized = unicodedata.normalize("NFD", str(value or "").lower())
    without_marks = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    )
    return " ".join(without_marks.replace("đ", "d").split())


def classify_continuity_authority(
    current_input,
    *,
    temporal_intent=False,
):
    """Classify authority only; current input never becomes continuity truth."""

    folded = _fold_continuity_question(current_input)
    if any(
        marker in folded
        for marker in (
            "current browser",
            "browser right now",
            "browser hien tai",
            "trinh duyet hien tai",
            "dang mo trang gi",
            "dang xem gi",
            "which tab is open",
            "what tab is open",
            "which page is open",
            "what page is open",
            "active tab",
            "current tab",
            "current page",
            "dang mo tab nao",
            "tab nao dang mo",
            "dang o tab nao",
            "dang mo trang nao",
            "trang nao dang mo",
        )
    ):
        return ContinuityAuthority.CURRENT_BROWSER
    if any(
        marker in folded
        for marker in (
            "older conversation",
            "old conversation",
            "last time we talked",
            "what did we say",
            "what did i tell you",
            "recall the older",
            "cuoc tro chuyen cu",
            "lan truoc da noi",
            "hom truoc minh noi",
            "truoc day da noi",
        )
    ):
        return ContinuityAuthority.OLD_DIALOGUE_RECALL
    if any(
        marker in folded
        for marker in (
            "durable personal",
            "personal preference",
            "my preference",
            "my favorite",
            "so thich cua toi",
            "thong tin lau dai",
            "project fact",
            "my birthday",
            "when was i born",
            "where do i live",
            "where am i based",
            "my address",
            "my residence",
            "my allerg",
            "allergies do i have",
            "ngay sinh cua",
            "sinh nhat cua",
            "ba sinh ngay nao",
            "toi song o dau",
            "ba song o dau",
            "dia chi cua",
            "di ung",
            "personal fact",
        )
    ):
        return ContinuityAuthority.DURABLE_FACT
    if any(
        marker in folded
        for marker in (
            "what are we doing",
            "open loop",
            "active work",
            "active task",
            "dang lam gi",
            "con viec dang lam",
            "tiep tuc cong viec",
        )
    ):
        return ContinuityAuthority.ACTIVE_WORK
    if temporal_intent or any(
        marker in folded
        for marker in (
            "nay gio",
            "vua roi",
            "just now",
            "what happened",
            "earlier today",
        )
    ):
        return ContinuityAuthority.RECENT_TEMPORAL
    return ContinuityAuthority.ORDINARY


def _record_time(value):
    if type(value) in (int, float) and math.isfinite(float(value)) and float(value) >= 0:
        return float(value)
    return None


def _private_continuity_view(request, snapshot, relevant_old_turns):
    from nana.runtime.context_runtime import RuntimeContextSnapshot

    if (
        type(snapshot) is not RuntimeContextSnapshot
        or snapshot.request is not request
        or snapshot.private_checkpoint is None
    ):
        raise ContextContractError("canonical_source_snapshot_unavailable")
    checkpoint_source = snapshot.private_checkpoint
    if (
        type(checkpoint_source) is not SourceSnapshot
        or checkpoint_source.source != "private_checkpoint"
        or checkpoint_source.captured_at != request.captured_wall_time
    ):
        raise ContextContractError("invalid_source_snapshot")
    authority = classify_continuity_authority(
        request.current_input,
        temporal_intent=request.temporal_intent,
    )
    if authority is ContinuityAuthority.CURRENT_BROWSER:
        return ContinuityView(request, authority, ("current_situation",))
    if authority is ContinuityAuthority.DURABLE_FACT:
        return ContinuityView(request, authority, ("memory",))
    checkpoint = _thaw(checkpoint_source.payload)
    moments_payload = _thaw(snapshot.awareness_memory.payload)
    if not isinstance(checkpoint, dict) or not isinstance(moments_payload, dict):
        raise ContextContractError("invalid_source_snapshot")

    session_id = str(checkpoint.get("session_id") or f"private:{request.correlation_id}")
    session_scope = ContinuityScope(
        lane=Lane.PRIVATE_OWNER,
        visibility=frozenset({Lane.PRIVATE_OWNER}),
        owner="private_session",
        session_id=session_id,
    )
    runtime_scope = ContinuityScope(
        lane=Lane.PRIVATE_OWNER,
        visibility=frozenset({Lane.PRIVATE_OWNER}),
        owner="runtime_state",
        session_id=session_id,
    )
    candidates = []
    if checkpoint.get("available", True):
        summary_text = _bounded_text(checkpoint.get("summary"), 1_200)
        if summary_text:
            candidates.append(SessionSummaryRecord(scope=session_scope, text=summary_text))

        raw_anchors = checkpoint.get("anchors")
        if not isinstance(raw_anchors, list):
            raw_anchors = []
        loop_rows = [
            row
            for row in raw_anchors
            if isinstance(row, dict) and row.get("kind") in {"task", "open_loop"}
        ][-5:]
        anchor_rows = [
            row
            for row in raw_anchors
            if isinstance(row, dict) and row.get("kind") not in {"task", "open_loop"}
        ][-6:]
        for row in anchor_rows:
            candidates.append(
                CheckpointAnchorRecord(
                    scope=session_scope,
                    text=_bounded_text(row.get("text"), 280),
                    event_id=str(row.get("event_id") or "") or None,
                    turn_id=str(row.get("turn_index") or "") or None,
                    occurred_at=_record_time(row.get("created_at")),
                    anchor_kind=str(row.get("kind") or "context"),
                )
            )
        for row in loop_rows:
            candidates.append(
                OpenLoopRecord(
                    scope=session_scope,
                    text=_bounded_text(row.get("text"), 240),
                    event_id=str(row.get("event_id") or "") or None,
                    turn_id=str(row.get("turn_index") or "") or None,
                    occurred_at=_record_time(row.get("created_at")),
                    loop_kind=str(row.get("kind") or "open_loop"),
                )
            )

        raw_turns = checkpoint.get("pending_turns")
        if not isinstance(raw_turns, list):
            raw_turns = []
        for row in [item for item in raw_turns if isinstance(item, dict)][-6:]:
            candidates.append(
                CompletedTurnRecord(
                    scope=session_scope,
                    text=_bounded_text(row.get("user_text"), 280),
                    reply_text=_bounded_text(row.get("nana_text"), 200),
                    event_id=str(row.get("event_id") or "") or None,
                    turn_id=str(row.get("turn_index") or "") or None,
                    occurred_at=_record_time(row.get("created_at")),
                    text_completion="complete",
                    voice_delivery="not_attested",
                )
            )

    if authority is ContinuityAuthority.OLD_DIALOGUE_RECALL:
        old_records = []
        if isinstance(relevant_old_turns, (str, bytes)):
            raise ContextContractError("invalid_continuity_record")
        for record in tuple(relevant_old_turns)[:3]:
            if type(record) is not RelevantOldTurnRecord:
                raise ContextContractError("invalid_continuity_record")
            if record.scope.lane is not Lane.PRIVATE_OWNER:
                raise ContextContractError("invalid_continuity_scope")
            old_text = _bounded_text(record.text, 600)
            old_records.append(
                replace(
                    record,
                    text=old_text,
                    reply_text=_bounded_text(record.reply_text, 600 - len(old_text)),
                )
            )
        candidates.extend(old_records)

    if authority is ContinuityAuthority.RECENT_TEMPORAL:
        raw_moments = moments_payload.get("moments")
        if not isinstance(raw_moments, list):
            raw_moments = []
        eligible = []
        for row in raw_moments:
            if not isinstance(row, dict) or row.get("source") not in {
                "awareness",
                "browser",
                "zone",
                "system",
            }:
                continue
            timestamp = _record_time(row.get("timestamp"))
            ttl = row.get("ttl_seconds")
            if timestamp is None or type(ttl) not in (int, float) or timestamp + float(ttl) <= snapshot.captured_at:
                continue
            eligible.append(row)
            if len(eligible) == 3:
                break
        for row in eligible:
            focus = _bounded_text(row.get("focus_text"), 220)
            title = _bounded_text(row.get("title") or row.get("frozen_title"), 200)
            candidates.append(
                RecentMomentRecord(
                    scope=runtime_scope,
                    text=focus or title,
                    event_id=str(row.get("id") or "") or None,
                    occurred_at=_record_time(row.get("timestamp")),
                    moment_source=str(row.get("source")),
                    active_app=_bounded_text(row.get("active_app"), 120),
                    active_zone=_bounded_text(row.get("active_zone"), 64),
                    title=title,
                    focus_text=focus,
                )
            )

    selected_types = {
        ContinuityAuthority.OLD_DIALOGUE_RECALL: (RelevantOldTurnRecord,),
        ContinuityAuthority.ACTIVE_WORK: (
            SessionSummaryRecord,
            CheckpointAnchorRecord,
            OpenLoopRecord,
        ),
        ContinuityAuthority.RECENT_TEMPORAL: (
            CompletedTurnRecord,
            RecentMomentRecord,
        ),
        ContinuityAuthority.ORDINARY: (
            SessionSummaryRecord,
            CheckpointAnchorRecord,
            CompletedTurnRecord,
        ),
    }[authority]
    records = dedupe_continuity_records(
        item for item in candidates if type(item) in selected_types
    )
    summary = next((item for item in records if type(item) is SessionSummaryRecord), None)
    anchors = tuple(item for item in records if type(item) is CheckpointAnchorRecord)
    loops = tuple(item for item in records if type(item) is OpenLoopRecord)
    recent = tuple(item for item in records if type(item) is CompletedTurnRecord)
    old = tuple(item for item in records if type(item) is RelevantOldTurnRecord)
    moments = tuple(item for item in records if type(item) is RecentMomentRecord)

    if authority is ContinuityAuthority.OLD_DIALOGUE_RECALL:
        return ContinuityView(
            request,
            authority,
            ("relevant_old_turns", "memory"),
            relevant_old_turns=old,
        )
    if authority is ContinuityAuthority.ACTIVE_WORK:
        return ContinuityView(
            request,
            authority,
            ("open_loops", "summary", "anchors"),
            summary=summary,
            anchors=anchors,
            open_loops=loops,
        )
    if authority is ContinuityAuthority.RECENT_TEMPORAL:
        return ContinuityView(
            request,
            authority,
            ("recent_moments", "recent_turns"),
            recent_turns=recent,
            recent_moments=moments,
        )
    return ContinuityView(
        request,
        authority,
        ("recent_turns", "summary", "anchors"),
        summary=summary,
        anchors=anchors,
        recent_turns=recent,
    )


def _public_continuity_view(request, public_source):
    if (
        type(public_source) is not SourceSnapshot
        or public_source.source != "public_social_session"
        or public_source.captured_at != request.captured_wall_time
    ):
        raise ContextContractError("invalid_source_snapshot")
    payload = _thaw(public_source.payload)
    scope = request.scope.public_scope
    if not isinstance(payload, dict) or scope is None or (
        payload.get("platform"),
        payload.get("room_id"),
        payload.get("stream_session_id"),
    ) != (scope.platform, scope.room_id, scope.stream_session_id):
        raise ContextContractError("invalid_source_snapshot")
    rows = payload.get("turns")
    if not isinstance(rows, list):
        raise ContextContractError("invalid_source_snapshot")
    records = []
    for row in rows:
        if not isinstance(row, dict) or row.get("event_id") == scope.event_id:
            continue
        record_scope = ContinuityScope(
            lane=Lane.PUBLIC_STAGE,
            visibility=frozenset({Lane.PUBLIC_STAGE}),
            owner="public_session",
            session_id=scope.stream_session_id,
            platform=scope.platform,
            room_id=scope.room_id,
            stream_session_id=scope.stream_session_id,
            actor_key=str(row.get("actor_key") or ""),
        )
        delivered = row.get("delivery_state") == "delivered"
        records.append(
            PublicTurnRecord(
                scope=record_scope,
                text=_bounded_text(row.get("message"), 96),
                reply_text=_bounded_text(row.get("reply"), 72) if delivered else "",
                event_id=str(row.get("event_id") or "") or None,
                provenance_record_id=str(row.get("content_hash") or "") or None,
                occurred_at=_record_time(row.get("timestamp")),
                speaker=_bounded_text(row.get("display_name"), 48) or "viewer",
                reply_delivery="delivered" if delivered else "not_delivered",
            )
        )
    public_records = tuple(
        item
        for item in dedupe_continuity_records(records[-5:])
        if type(item) is PublicTurnRecord
    )
    return ContinuityView(
        request,
        ContinuityAuthority.ORDINARY,
        ("public_turns",),
        public_turns=public_records,
    )


def build_continuity_view(
    request,
    *,
    snapshot=None,
    public_source=None,
    relevant_old_turns=(),
):
    """Build one offline typed continuity view from already captured owners."""

    request = require_resolved_context_request(request)
    if request.scope.lane is Lane.OPERATOR_BACKSTAGE:
        return ContinuityView(request, ContinuityAuthority.ORDINARY, ())
    if request.scope.lane is Lane.PUBLIC_STAGE:
        return _public_continuity_view(request, public_source)
    return _private_continuity_view(request, snapshot, relevant_old_turns)


def _continuity_section(request, section_id, payload, *, owner, lifetime, role, max_tokens, required=False):
    revision = "continuity-" + _semantic_identity(section_id, payload)
    return ContextSection(
        id=section_id,
        lifetime=lifetime,
        semantic_role=role,
        freshness=Freshness.FRESH,
        visibility=frozenset({request.scope.lane}),
        source=SourceRef(owner, "continuity_view.adapter.v1", section_id),
        revision=revision,
        authority=None,
        observed_at=request.captured_wall_time,
        expires_at=None,
        conflict_key=None,
        dedupe_key=revision,
        max_tokens=max_tokens,
        payload=payload,
        formatter_version="continuity_view.v1",
        required=required,
        budget_class="continuity",
        semantic_status="active",
        provenance=(),
        relevance=1.0,
    )


def build_continuity_sections(view, request):
    """Render typed view records once; stable IDs remain non-model metadata."""

    request = require_resolved_context_request(request)
    if type(view) is not ContinuityView or view.request is not request:
        raise ContextContractError("invalid_continuity_view")
    if request.scope.lane is Lane.OPERATOR_BACKSTAGE:
        return ()
    rows = []
    if request.scope.lane is Lane.PUBLIC_STAGE:
        if view.public_turns:
            payload = {
                "turns": [
                    {
                        "speaker": record.speaker,
                        "message": record.text,
                        **(
                            {"nana_reply": record.reply_text}
                            if record.reply_delivery == "delivered" and record.reply_text
                            else {}
                        ),
                    }
                    for record in view.public_turns
                ]
            }
            rows.append(
                _continuity_section(
                    request,
                    "continuity.public_room.v1",
                    payload,
                    owner="public_session",
                    lifetime=Lifetime.SESSION,
                    role=SemanticRole.HISTORY,
                    max_tokens=2_500,
                )
            )
        return tuple(rows)

    if view.summary is not None or view.anchors:
        payload = {}
        if view.summary is not None:
            payload["session_summary"] = view.summary.text
        if view.anchors:
            payload["anchors"] = [
                {"kind": record.anchor_kind, "text": record.text}
                for record in view.anchors
            ]
        rows.append(
            _continuity_section(
                request,
                "continuity.summary.v1",
                payload,
                owner="private_session",
                lifetime=Lifetime.SESSION,
                role=SemanticRole.HISTORY,
                max_tokens=2_000,
            )
        )
    if view.open_loops:
        rows.append(
            _continuity_section(
                request,
                "continuity.open_loops.v1",
                {
                    "open_loops": [
                        {"kind": record.loop_kind, "text": record.text}
                        for record in view.open_loops
                    ]
                },
                owner="private_session",
                lifetime=Lifetime.SESSION,
                role=SemanticRole.STATE,
                max_tokens=1_200,
            )
        )
    if view.recent_turns or view.relevant_old_turns:
        payload = {}
        if view.recent_turns:
            payload["recent_completed_turns"] = [
                {
                    "user_text": record.text,
                    "nana_text": record.reply_text,
                    "text_completion": record.text_completion,
                    "voice_delivery": record.voice_delivery,
                }
                for record in view.recent_turns
            ]
        if view.relevant_old_turns:
            payload["relevant_old_turns"] = [
                {
                    "user_text": record.text,
                    **({"nana_text": record.reply_text} if record.reply_text else {}),
                }
                for record in view.relevant_old_turns
            ]
        rows.append(
            _continuity_section(
                request,
                "continuity.recent_turns.v1",
                payload,
                owner="private_session",
                lifetime=Lifetime.SESSION,
                role=SemanticRole.HISTORY,
                max_tokens=2_500,
            )
        )
    if view.recent_moments:
        rows.append(
            _continuity_section(
                request,
                "situation.temporal.v1",
                {
                    "moments": [
                        {
                            "source": record.moment_source,
                            "active_app": record.active_app,
                            "active_zone": record.active_zone,
                            "title": record.title,
                            "focus": record.focus_text,
                        }
                        for record in view.recent_moments
                    ]
                },
                owner="runtime_state",
                lifetime=Lifetime.TURN,
                role=SemanticRole.STATE,
                max_tokens=1_800,
                required=request.temporal_intent,
            )
        )
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class _CapturedPublicAdapter:
    request: ResolvedContextRequest
    batch: SourceBatch

    def read(self, request: ResolvedContextRequest) -> SourceBatch:
        if require_resolved_context_request(request) is not self.request:
            raise ContextContractError("public_capture_request_mismatch")
        return self.batch


def _public_cum2_section(
    request,
    section_id,
    payload,
    *,
    owner,
    lifetime,
    role,
    revision,
    max_tokens,
    required=False,
):
    return ContextSection(
        id=section_id,
        lifetime=lifetime,
        semantic_role=role,
        freshness=Freshness.FRESH,
        visibility=frozenset({Lane.PUBLIC_STAGE}),
        source=SourceRef(owner, "public_cum2.adapter.v1", section_id),
        revision=revision,
        authority=None,
        observed_at=(
            None if lifetime is Lifetime.STATIC else request.captured_wall_time
        ),
        expires_at=None,
        conflict_key=None,
        dedupe_key=f"{section_id}:{revision}",
        max_tokens=max_tokens,
        payload=payload,
        formatter_version="public_cum2.v1",
        required=required,
        budget_class="public_cum2",
        semantic_status="active",
        provenance=(),
        relevance=1.0,
    )


def _require_public_cum2_continuity_source(request, value):
    scope = request.scope.public_scope
    if (
        type(value) is not SourceSnapshot
        or value.source != "public_social_session"
        or value.captured_at != request.captured_wall_time
        or scope is None
    ):
        raise ContextContractError("invalid_source_snapshot")
    payload = _thaw(value.payload)
    if not isinstance(payload, dict) or (
        payload.get("platform"),
        payload.get("room_id"),
        payload.get("stream_session_id"),
        payload.get("current_event_id"),
    ) != (
        scope.platform,
        scope.room_id,
        scope.stream_session_id,
        scope.event_id,
    ):
        raise ContextContractError("invalid_source_snapshot")
    return value


def build_public_cum2_source_view(request, continuity_source):
    """Materialize one captured, public-only CUM2 capability view."""

    from nana.runtime.context_contracts import Route
    from nana.runtime.livestream_identity import stage_prompt_block

    request = require_resolved_context_request(request)
    if request.scope.lane is not Lane.PUBLIC_STAGE or request.route is not Route.YOUTUBE_CUM2:
        raise ContextContractError("unapproved_canonical_profile")
    scope = request.scope.public_scope
    if scope is None:
        raise ContextContractError("invalid_lane_route_scope")
    continuity_source = _require_public_cum2_continuity_source(
        request,
        continuity_source,
    )

    core_payload = (
        "You are Nana's public livestream voice. Reply naturally in Vietnamese, "
        "usually in one to three concise sentences."
    )
    policy_payload = (
        stage_prompt_block()
        + "\n\nPUBLIC LIVESTREAM POLICY:\n"
        "- Treat viewers as public audience, never as the private owner.\n"
        "- Use only the public request and public-room continuity supplied here.\n"
        "- Never disclose private memory, backend state, credentials, or owner history."
    )
    output_payload = (
        "CUM2 OUTPUT CONTRACT:\n"
        "- Answer only the current viewer message.\n"
        "- Do not claim that an action, publication, playback, or delivery occurred.\n"
        "- Return reply text only."
    )
    core_revision = "public-cum2-core-" + _semantic_identity(
        core_payload,
        policy_payload,
        output_payload,
    )
    core_sections = tuple(
        _public_cum2_section(
            request,
            section_id,
            payload,
            owner="core",
            lifetime=Lifetime.STATIC,
            role=SemanticRole.INSTRUCTION,
            revision=core_revision,
            max_tokens=max_tokens,
            required=True,
        )
        for section_id, payload, max_tokens in (
            ("core.public.v1", core_payload, 1_000),
            ("policy.public.livestream.v1", policy_payload, 600),
            ("contract.output.cum2.v1", output_payload, 400),
        )
    )

    request_payload = {
        "platform": request.public_context["platform"],
        "viewer_display_label": request.public_context["display_name"],
    }
    request_revision = "public-cum2-request-" + _semantic_identity(request_payload)
    request_section = _public_cum2_section(
        request,
        "public.request_context.v1",
        request_payload,
        owner="public_scope",
        lifetime=Lifetime.TURN,
        role=SemanticRole.STATE,
        revision=request_revision,
        max_tokens=400,
    )

    continuity_view = build_continuity_view(
        request,
        public_source=continuity_source,
    )
    continuity_sections = tuple(
        replace(section, revision=continuity_source.revision, max_tokens=500)
        for section in build_continuity_sections(continuity_view, request)
    )
    expression_section = build_public_expression_section(request)

    def adapter(owner, revision, sections=()):
        return _CapturedPublicAdapter(
            request,
            SourceBatch(owner, revision, tuple(sections)),
        )

    return PublicSourceView(
        core=adapter("core", core_revision, core_sections),
        public_scope=adapter("public_scope", request_revision, (request_section,)),
        public_session=adapter(
            "public_session",
            continuity_source.revision,
            continuity_sections,
        ),
        public_grounding=adapter("public_grounding", "public-grounding-denied.v1"),
        expression=adapter(
            "expression",
            expression_section.revision,
            (expression_section,),
        ),
    )


__all__ = [
    "CheckpointAnchorRecord",
    "CompletedTurnRecord",
    "ContinuityAuthority",
    "ContinuityRecord",
    "ContinuityScope",
    "ContinuityView",
    "SourceCaptureChanged",
    "SourceRevision",
    "build_current_situation",
    "build_current_situation_section",
    "build_continuity_sections",
    "build_continuity_view",
    "build_operator_expression_section",
    "build_private_expression_section",
    "build_public_expression_section",
    "build_public_cum2_source_view",
    "FrozenGroundingEvidence",
    "MemoryConflictOutcome",
    "MemoryConflictResolution",
    "MemoryContextBundle",
    "MemoryRetrievalRecord",
    "MemoryRetrievalResult",
    "build_memory_context_bundle",
    "retrieve_context_memory",
    "require_memory_context_bundle",
    "resolve_memory_conflicts",
    "freeze_grounding_evidence",
    "dedupe_continuity_records",
    "ContextSourceAdapter",
    "OperatorSourceView",
    "PrivateSourceView",
    "PublicSourceView",
    "PublicTurnRecord",
    "RecentMomentRecord",
    "RelevantOldTurnRecord",
    "SessionSummaryRecord",
    "OpenLoopRecord",
    "SourceBatch",
]
