"""Bounded Phase 2 memory-consolidation preview.

This module is deliberately a *proposal generator*, not a mutator.  It reads
an already supplied memory snapshot, filters records that have trustworthy
provenance and scope, and returns deterministic duplicate/correction actions.
No Nana singleton, writer, provider, or production file is imported or touched
by default.  Callers must explicitly pass a snapshot and opt in to the preview
flag (or set ``NANA_MEMORY_CONSOLIDATION_PREVIEW_ENABLED=1``).

The preview is intentionally conservative:

* legacy strings and records without ``source_event_id`` are rejected;
* generated/model text, questions, secrets, temporary/session records, and
  expired records are rejected;
* exact duplicates may be *proposed* for merge only inside the same lane and
  identity scope;
* differing records sharing an explicit ``fact_key``/``subject`` are reported
  as ``review_correction`` candidates, never auto-superseded.

Phase 2 has no apply path.  ``after_digest`` and ``rollback_export`` describe
the hypothetical duplicate merge so an operator can review it safely.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

try:  # Keep this module importable in isolated smoke-test package stubs.
    from .history_privacy import contains_history_secret
except Exception:  # pragma: no cover - defensive fallback for old installs
    _SECRET_RE = re.compile(
        r"(?i)\b(?:password|passwd|api[_ -]?key|secret|token)\b\s*[:=]"
    )

    def contains_history_secret(value: str) -> bool:
        return bool(_SECRET_RE.search(str(value or "")))


PHASE = "MEMORY-V2-PHASE2-CONSOLIDATION"
PREVIEW_ENV = "NANA_MEMORY_CONSOLIDATION_PREVIEW_ENABLED"
SCHEMA_VERSION = 1
DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_MAX_RECORDS = 256
DEFAULT_ACTION_LIMIT = 64

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})
_QUESTION_WORDS = frozenset(
    {
        "ai",
        "có",
        "co",
        "gì",
        "gi",
        "how",
        "is",
        "nao",
        "nào",
        "what",
        "why",
        "when",
        "where",
        "who",
        "which",
        "làm",
        "lam",
        "tại",
        "tai",
    }
)
_MODEL_SOURCES = frozenset(
    {
        "assistant",
        "chatgpt",
        "generated",
        "gpt",
        "llm",
        "model",
        "nana",
        "system_generated",
    }
)
_TEMPORARY_TAGS = frozenset(
    {"ephemeral", "temporary", "temp", "session", "checkpoint", "transient"}
)
_ALLOWED_LANES = frozenset(
    {"private", "private_owner", "operator_backstage", "public", "public_stage"}
)


def _flag_enabled(value: bool | None) -> bool:
    if value is not None:
        return bool(value)
    return os.getenv(PREVIEW_ENV, "0").strip().lower() in _TRUE_VALUES


def consolidation_preview_enabled() -> bool:
    """Return the explicit environment flag; defaults to ``False``."""

    return _flag_enabled(None)


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        char for char in unicodedata.normalize("NFD", text)
        if unicodedata.category(char) != "Mn"
    ).replace("đ", "d")


def _canonical_text(value: Any) -> str:
    """Canonical key for exact duplicate detection, preserving no raw text."""

    folded = _fold(value)
    folded = re.sub(r"[^\w\s]", " ", folded, flags=re.UNICODE)
    return " ".join(folded.split())


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _scope_value(raw: Mapping[str, Any], key: str) -> str:
    direct = raw.get(key)
    if direct in (None, "") and key == "room_id":
        direct = raw.get("channel_id") or raw.get("room_channel")
    if direct not in (None, ""):
        return str(direct).strip()
    scope = raw.get("scope")
    if isinstance(scope, Mapping):
        value = scope.get(key)
        if value in (None, "") and key == "room_id":
            value = scope.get("room") or scope.get("channel_id") or scope.get("room_key")
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _lane(raw: Mapping[str, Any]) -> str:
    return str(raw.get("lane") or "").strip().lower()


def _scope_key(raw: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    """Return a merge boundary; session IDs are intentionally excluded."""

    lane = _lane(raw)
    platform = _scope_value(raw, "platform")
    actor = str(raw.get("actor_key") or _scope_value(raw, "actor_key") or "").strip()
    room = _scope_value(raw, "room_id")
    # A public fact without stable actor/room is never merged with another.
    if lane in {"public", "public_stage"} and (not actor or not room):
        return (lane, platform, f"missing-actor:{actor}", f"missing-room:{room}", "")
    return (lane, platform, actor, room, str(raw.get("type") or "").strip().lower())


def _is_question(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return False
    if "?" in value or "？" in value:
        return True
    first = _fold(value).split(" ", 1)[0]
    if first in _QUESTION_WORDS:
        return True
    # Common Vietnamese yes/no questions often have a declarative prefix.
    folded = _fold(value)
    return bool(re.search(r"(?:\bchua\b|\bkhong\s*\?)$", folded))


def _is_model_generated(raw: Mapping[str, Any]) -> bool:
    for key in ("model_generated", "generated_by_model", "assistant_generated"):
        if bool(raw.get(key)):
            return True
    values = [
        raw.get("source"),
        raw.get("origin"),
        raw.get("created_by"),
        raw.get("author"),
        raw.get("provider"),
        raw.get("generation"),
    ]
    for value in values:
        token = _fold(value).replace("-", "_").replace(" ", "_")
        if (
            token in _MODEL_SOURCES
            or token.startswith(("model_", "assistant_", "gpt", "gemini", "claude", "llm_"))
        ):
            return True
    return False


def _is_temporary(raw: Mapping[str, Any]) -> bool:
    if str(raw.get("type") or "").strip().lower() == "ephemeral":
        return True
    if str(raw.get("decay_policy") or "").strip().lower() == "session":
        return True
    if bool(raw.get("temporary")) or bool(raw.get("transient")):
        return True
    scope = raw.get("scope")
    if isinstance(scope, Mapping):
        if str(scope.get("kind") or scope.get("lifecycle") or "").lower() in {
            "session",
            "temporary",
            "transient",
        }:
            return True
    tags = {_fold(tag) for tag in (raw.get("tags") or []) if str(tag).strip()}
    return bool(tags & _TEMPORARY_TAGS)


def _expiry_values(raw: Mapping[str, Any]) -> list[Any]:
    values = [raw.get("expires_at"), raw.get("valid_until")]
    scope = raw.get("scope")
    if isinstance(scope, Mapping):
        values.extend((scope.get("expires_at"), scope.get("valid_until")))
    return [value for value in values if value not in (None, "")]


def _eligibility(
    raw: Mapping[str, Any], *, now: float, min_confidence: float
) -> str | None:
    record_id = str(raw.get("id") or "").strip()
    if not record_id:
        return "missing_id"
    text = str(raw.get("text") or "").strip()
    if not text:
        return "empty_text"
    if not str(raw.get("source_event_id") or "").strip():
        return "missing_provenance"
    lane = _lane(raw)
    if lane not in _ALLOWED_LANES:
        return "missing_or_invalid_lane"
    confidence = _safe_float(raw.get("confidence"))
    if confidence is None or confidence < min_confidence:
        return "low_confidence"
    if contains_history_secret(text) or contains_history_secret(str(raw.get("evidence") or "")):
        return "secret_detected"
    if _is_question(text):
        return "question_or_unconfirmed"
    if _is_model_generated(raw):
        return "model_generated"
    if _is_temporary(raw):
        return "temporary_or_session"
    expiry_values = _expiry_values(raw)
    for expiry_value in expiry_values:
        expires_at = _safe_float(expiry_value)
        if expires_at is None:
            return "invalid_expiry"
        if expires_at <= now:
            return "expired"
    if lane in {"public", "public_stage"}:
        actor = str(raw.get("actor_key") or _scope_value(raw, "actor_key") or "").strip()
        room = _scope_value(raw, "room_id")
        platform = _scope_value(raw, "platform").lower()
        if not actor or not room:
            return "public_scope_missing"
        if not platform or not actor.startswith(platform + ":"):
            return "public_actor_not_namespaced"
        provenance = raw.get("provenance")
        verified = raw.get("verified") is True
        consent = raw.get("consent") is True or raw.get("consent_recorded") is True
        if isinstance(provenance, Mapping):
            verified = verified or provenance.get("verified") is True
            consent = consent or provenance.get("consent") is True
        if not verified:
            return "public_unverified"
        if not consent:
            return "public_consent_missing"
    return None


def _extract_records(source: Any) -> list[Any]:
    if source is None:
        return []
    if isinstance(source, Mapping):
        value = source.get("long_term", [])
    elif hasattr(source, "snapshot") and callable(source.snapshot):
        snapshot = source.snapshot()
        value = snapshot.get("long_term", []) if isinstance(snapshot, Mapping) else []
    else:
        value = source
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return []
    return list(value)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _digest(records: Sequence[Any]) -> str:
    payload = json.dumps(
        _jsonable(list(records)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _action_id(action_type: str, ids: Sequence[str]) -> str:
    payload = f"{action_type}|{'|'.join(sorted(str(item) for item in ids))}"
    return "c2-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _winner_key(entry: tuple[int, Mapping[str, Any]]) -> tuple[Any, ...]:
    index, raw = entry
    confidence = _safe_float(raw.get("confidence")) or 0.0
    updated = _safe_float(raw.get("updated_at")) or 0.0
    created = _safe_float(raw.get("created_at")) or 0.0
    return (bool(raw.get("pinned")), confidence, updated, created, -index, str(raw.get("id")))


def _family_key(raw: Mapping[str, Any]) -> str:
    for key in ("fact_key", "memory_key", "subject", "topic_key"):
        value = _canonical_text(raw.get(key))
        if value:
            return f"field:{value}"
    tags = sorted({_canonical_text(tag) for tag in (raw.get("tags") or []) if _canonical_text(tag)})
    if len(tags) >= 2:
        return "tags:" + "|".join(tags)
    return ""


@dataclass(frozen=True)
class ConsolidationAction:
    """A proposed action; it is never applied by this module."""

    action_id: str
    action_type: str
    candidate_ids: tuple[str, ...]
    keep_id: str = ""
    reason: str = ""
    source_event_ids: tuple[str, ...] = field(default_factory=tuple)
    preview_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "candidate_ids": list(self.candidate_ids),
            "keep_id": self.keep_id,
            "reason": self.reason,
            "source_event_ids": list(self.source_event_ids),
            "preview_only": self.preview_only,
        }


@dataclass(frozen=True)
class RejectedRecord:
    index: int
    record_id: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"index": self.index, "record_id": self.record_id, "reason": self.reason}


@dataclass(frozen=True)
class ConsolidationPreviewReport:
    enabled: bool
    status: str
    before_digest: str = ""
    after_digest: str = ""
    eligible_count: int = 0
    rejected_count: int = 0
    actions: tuple[ConsolidationAction, ...] = field(default_factory=tuple)
    rejected: tuple[RejectedRecord, ...] = field(default_factory=tuple)
    rollback_export: dict[str, Any] = field(default_factory=dict)
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False
    schema_version: int = SCHEMA_VERSION
    max_records: int = DEFAULT_MAX_RECORDS
    min_confidence: float = DEFAULT_MIN_CONFIDENCE

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": PHASE,
            "schema_version": self.schema_version,
            "enabled": self.enabled,
            "status": self.status,
            "before_digest": self.before_digest,
            "after_digest": self.after_digest,
            "eligible_count": self.eligible_count,
            "rejected_count": self.rejected_count,
            "actions": [item.to_dict() for item in self.actions],
            "rejected": [item.to_dict() for item in self.rejected],
            "rollback_export": copy.deepcopy(self.rollback_export),
            "read_only": self.read_only,
            "can_act": self.can_act,
            "memory_write": self.memory_write,
            "max_records": self.max_records,
            "min_confidence": self.min_confidence,
        }


def _disabled_report() -> ConsolidationPreviewReport:
    return ConsolidationPreviewReport(enabled=False, status="disabled")


def build_consolidation_preview(
    memory_source: Any = None,
    *,
    enabled: bool | None = None,
    now: float | None = None,
    max_records: int = DEFAULT_MAX_RECORDS,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    action_limit: int = DEFAULT_ACTION_LIMIT,
) -> ConsolidationPreviewReport:
    """Build a deterministic, read-only consolidation proposal.

    ``memory_source`` may be a ``{"long_term": [...]}`` snapshot, a list of
    records, or an object exposing a ``snapshot()`` method.  Passing ``None``
    is intentionally an empty input; this function never imports the global
    Nana memory singleton.
    """

    if not _flag_enabled(enabled):
        return _disabled_report()
    try:
        bounded_limit = max(1, min(int(max_records), 10_000))
    except (TypeError, ValueError):
        bounded_limit = DEFAULT_MAX_RECORDS
    try:
        confidence_floor = max(0.0, min(float(min_confidence), 1.0))
    except (TypeError, ValueError):
        confidence_floor = DEFAULT_MIN_CONFIDENCE
    try:
        action_cap = max(1, min(int(action_limit), 512))
    except (TypeError, ValueError):
        action_cap = DEFAULT_ACTION_LIMIT
    current_time = time.time() if now is None else float(now)
    raw_records = _extract_records(memory_source)
    before_digest = _digest(raw_records)

    entries: list[tuple[int, Mapping[str, Any]]] = []
    rejected: list[RejectedRecord] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_records):
        if index >= bounded_limit:
            rejected.append(
                RejectedRecord(index=index, record_id=str(getattr(raw, "get", lambda *_: "")("id") or ""), reason="limit_exceeded")
            )
            continue
        if not isinstance(raw, Mapping):
            rejected.append(RejectedRecord(index=index, record_id=f"legacy-{index}", reason="missing_provenance"))
            continue
        reason = _eligibility(raw, now=current_time, min_confidence=confidence_floor)
        record_id = str(raw.get("id") or "").strip()
        if reason is None and record_id in seen_ids:
            reason = "duplicate_id"
        if reason:
            rejected.append(RejectedRecord(index=index, record_id=record_id, reason=reason))
            continue
        seen_ids.add(record_id)
        entries.append((index, copy.deepcopy(dict(raw))))

    duplicate_groups: dict[tuple[Any, ...], list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for entry in entries:
        index, raw = entry
        duplicate_groups[_scope_key(raw) + (_canonical_text(raw.get("text")),)].append(entry)

    duplicate_actions: list[ConsolidationAction] = []
    for group in duplicate_groups.values():
        if len(group) < 2 or not _canonical_text(group[0][1].get("text")):
            continue
        ordered = sorted(group, key=_winner_key, reverse=True)
        winner = ordered[0][1]
        ids = tuple(str(item[1].get("id") or "") for item in ordered)
        loser_ids = tuple(item for item in ids[1:] if item)
        if not loser_ids:
            continue
        action = ConsolidationAction(
            action_id=_action_id("merge_duplicate", ids),
            action_type="merge_duplicate",
            candidate_ids=ids,
            keep_id=str(winner.get("id") or ""),
            reason="exact canonical text and identical lane/identity scope",
            source_event_ids=tuple(str(item[1].get("source_event_id") or "") for item in ordered),
        )
        duplicate_actions.append(action)

    families: dict[tuple[Any, ...], list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for entry in entries:
        index, raw = entry
        family = _family_key(raw)
        if family:
            families[_scope_key(raw)[:4] + (family,)].append(entry)
    correction_actions: list[ConsolidationAction] = []
    for group in families.values():
        distinct: dict[str, tuple[int, Mapping[str, Any]]] = {}
        for entry in group:
            key = _canonical_text(entry[1].get("text"))
            if key:
                distinct.setdefault(key, entry)
        if len(distinct) < 2:
            continue
        ordered = sorted(group, key=_winner_key, reverse=True)
        ids = tuple(str(item[1].get("id") or "") for item in ordered if item[1].get("id"))
        if len(ids) < 2:
            continue
        action = ConsolidationAction(
            action_id=_action_id("review_correction", ids),
            action_type="review_correction",
            candidate_ids=ids,
            keep_id=str(ordered[0][1].get("id") or ""),
            reason="conflicting text shares an explicit fact/topic key; owner review required",
            source_event_ids=tuple(str(item[1].get("source_event_id") or "") for item in ordered),
        )
        if action.action_id not in {item.action_id for item in duplicate_actions}:
            correction_actions.append(action)

    actions = sorted(
        duplicate_actions + correction_actions,
        key=lambda item: (item.action_type, item.action_id),
    )[:action_cap]
    retained_duplicate_actions = [
        item for item in actions if item.action_type == "merge_duplicate"
    ]
    removed_ids: set[str] = set()
    rollback_records: dict[str, Mapping[str, Any]] = {}
    eligible_by_id = {
        str(raw.get("id") or ""): raw for _, raw in entries
    }
    for action in retained_duplicate_actions:
        removed_ids.update(item for item in action.candidate_ids if item != action.keep_id)
        for record_id in action.candidate_ids:
            raw = eligible_by_id.get(record_id)
            if raw is not None:
                rollback_records[record_id] = copy.deepcopy(raw)
    # Preserve malformed/legacy entries in the hypothetical snapshot; the
    # preview never proposes deleting them, it only reports why they were
    # rejected.
    hypothetical = []
    for raw in raw_records:
        if isinstance(raw, Mapping) and str(raw.get("id") or "") in removed_ids:
            continue
        hypothetical.append(copy.deepcopy(raw))
    after_digest = _digest(hypothetical)
    rollback_export = {
        "format": "memory-consolidation-rollback-v1",
        "schema_version": SCHEMA_VERSION,
        "before_digest": before_digest,
        "after_digest": after_digest,
        "action_ids": [item.action_id for item in actions if item.action_type == "merge_duplicate"],
        "records": [rollback_records[key] for key in sorted(rollback_records)],
        "apply_supported": False,
        "read_only": True,
    }
    return ConsolidationPreviewReport(
        enabled=True,
        status="preview_ready" if actions else "no_actions",
        before_digest=before_digest,
        after_digest=after_digest,
        eligible_count=len(entries),
        rejected_count=len(rejected),
        actions=tuple(actions),
        rejected=tuple(rejected),
        rollback_export=rollback_export,
        max_records=bounded_limit,
        min_confidence=confidence_floor,
    )


# Descriptive aliases keep the handoff vocabulary stable for callers.
build_memory_phase2_consolidation_preview = build_consolidation_preview
build_memory_consolidation_phase2_preview = build_consolidation_preview


def consolidation_preview_status_lines(
    report: ConsolidationPreviewReport | None = None,
) -> list[str]:
    report = report or build_consolidation_preview()
    return [
        f"{PHASE}",
        f"  Status: {report.status} | enabled={report.enabled}",
        f"  Mode: preview-only | read_only={report.read_only} | can_act={report.can_act} | memory_write={report.memory_write}",
        f"  Records: eligible={report.eligible_count} | rejected={report.rejected_count} | actions={len(report.actions)}",
        f"  Digests: before={report.before_digest[:12] or 'none'} | after={report.after_digest[:12] or 'none'}",
        "  Safety: no apply | no delete | no compact | no provider/model calls",
    ]


def consolidation_preview_lines(
    report: ConsolidationPreviewReport | None = None,
) -> list[str]:
    report = report or build_consolidation_preview()
    lines = consolidation_preview_status_lines(report)
    lines.append("  Candidate actions:")
    if report.actions:
        for action in report.actions:
            lines.append(
                f"    - {action.action_type} | id={action.action_id} | candidates={list(action.candidate_ids)} | keep={action.keep_id}"
            )
    else:
        lines.append("    - none")
    lines.append("  Rejected records:")
    if report.rejected:
        for rejected in report.rejected[:16]:
            lines.append(f"    - index={rejected.index} id={rejected.record_id or 'unknown'} | {rejected.reason}")
    else:
        lines.append("    - none")
    lines.append("  Rollback: export available for proposed duplicate removals; apply_supported=False")
    return lines


memory_phase2_consolidation_status_lines = consolidation_preview_status_lines
memory_phase2_consolidation_preview_lines = consolidation_preview_lines


__all__ = [
    "ConsolidationAction",
    "ConsolidationPreviewReport",
    "RejectedRecord",
    "build_consolidation_preview",
    "build_memory_phase2_consolidation_preview",
    "build_memory_consolidation_phase2_preview",
    "consolidation_preview_enabled",
    "consolidation_preview_lines",
    "consolidation_preview_status_lines",
    "memory_phase2_consolidation_preview_lines",
    "memory_phase2_consolidation_status_lines",
]
