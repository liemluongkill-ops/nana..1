"""CORE-MEMORY-1 — Nana Memory Spine.

Layered on top of nana/memory.py. Adds:
- SessionSummary: compressed session context, persisted across restarts.
- MemoryItem: structured memory objects with type, tags, confidence, importance.
- MemoryClassifier: keyword-based type classifier (no LLM).
- MemorySpine: store, update, delete, pin, retrieve, session-summary, commands.

Design constraints:
- Backward-compatible with existing memory["long_term"] plain-string entries.
- No new persistence format — writes to memory["long_term"] list (dicts).
- No embeddings by default (gated by NANA_USE_EMBEDDINGS=1 env var).
- No LLM for classification — keyword-only.
- No new dependencies beyond stdlib.

Phase: CORE-MEMORY-1 (hybrid, core-commands-only).
"""

from __future__ import annotations

import os
import copy
import re
import time
import threading
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Optional

# Reuse existing secrets pattern from awareness_memory
_SECRET_PATTERNS = (
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"(?:password|passwd|pwd|secret|token)\s*[:=]?\s*", re.IGNORECASE),
    re.compile(r"sk[-_]"),
    re.compile(r"ghp[-_]"),
    re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----"),
)


# ─── Constants ────────────────────────────────────────────────────────────────

MEMORY_TYPES = (
    "preference",
    "project_fact",
    "relationship",
    "routine",
    "technical_decision",
    "ephemeral",
)
IMPORTANCE_LEVELS = ("low", "medium", "high")
DECAY_POLICIES = ("eternal", "session", "decay")

DEFAULT_SESSION_SUMMARY_INTERVAL = 10  # turns between session summaries


# ─── MemoryItem dataclass ───────────────────────────────────────────────────────

@dataclass
class MemoryItem:
    """Structured memory object.

    Fields:
        id: Short uuid4 (first 8 chars).
        type: preference | project_fact | relationship | routine | technical_decision | ephemeral.
        text: Human-readable summary of the memory.
        source: chat | awareness | system | user.
        confidence: 0.0–1.0. Higher = more certain.
        importance: low | medium | high.
        created_at: time.time() at creation.
        updated_at: time.time() at last update.
        last_used_at: time.time() of last retrieval.
        tags: List of semantic tags for retrieval.
        evidence: Raw excerpt or reference (e.g. file:line or chat timestamp).
        decay_policy: eternal | session | decay.
        pinned: If True, immune to decay and auto-cleanup.
    """
    type: str
    text: str
    source: str = "chat"
    confidence: float = 0.7
    importance: str = "medium"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    tags: list[str] = field(default_factory=list)
    evidence: str = ""
    decay_policy: str = "eternal"
    pinned: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    source_event_id: str = ""
    lane: str = "private"
    actor_key: str = ""
    scope: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.type not in MEMORY_TYPES:
            self.type = "ephemeral"
        if self.importance not in IMPORTANCE_LEVELS:
            self.importance = "medium"
        if self.decay_policy not in DECAY_POLICIES:
            self.decay_policy = "eternal"
        self.confidence = max(0.0, min(1.0, self.confidence))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "text": self.text,
            "source": self.source,
            "confidence": self.confidence,
            "importance": self.importance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_used_at": self.last_used_at,
            "tags": list(self.tags),
            "evidence": self.evidence,
            "decay_policy": self.decay_policy,
            "pinned": self.pinned,
            "source_event_id": self.source_event_id,
            "lane": self.lane,
            "actor_key": self.actor_key,
            "scope": copy.deepcopy(self.scope),
        }

    def age_days(self, now: Optional[float] = None) -> float:
        now = now or time.time()
        return (now - self.created_at) / 86400.0


def load_item(raw: dict) -> MemoryItem:
    """Load from dict. Handles both new MemoryItem dicts and legacy plain-string entries.

    Legacy format (plain str): converted to ephemeral MemoryItem.
    New format (dict with 'type'): converted to MemoryItem.
    """
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError("Empty legacy string")
        return MemoryItem(type="ephemeral", text=text, source="legacy")

    try:
        return MemoryItem(
            id=str(raw.get("id", uuid.uuid4().hex[:8])),
            type=str(raw.get("type", "ephemeral")),
            text=str(raw.get("text", "")),
            source=str(raw.get("source", "chat")),
            confidence=float(raw.get("confidence", 0.7)),
            importance=str(raw.get("importance", "medium")),
            created_at=float(raw.get("created_at", time.time())),
            updated_at=float(raw.get("updated_at", time.time())),
            last_used_at=float(raw.get("last_used_at", time.time())),
            tags=list(raw.get("tags", [])),
            evidence=str(raw.get("evidence", "")),
            decay_policy=str(raw.get("decay_policy", "eternal")),
            pinned=bool(raw.get("pinned", False)),
            source_event_id=str(raw.get("source_event_id", "")),
            lane=str(raw.get("lane", "private")),
            actor_key=str(raw.get("actor_key", "")),
            scope=copy.deepcopy(raw.get("scope", {})),
        )
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Cannot load MemoryItem from {raw!r}: {exc}")


# Durable data domain: this compatibility alias carries no delivery state.
MemoryRecord = MemoryItem


def _tokenize(text: str) -> set[str]:
    """Tokenize Vietnamese-friendly text for lightweight retrieval.

    Plain split() leaves punctuation attached, so a query like "tự nhiên"
    would miss memory text containing "tự nhiên,". Keep this intentionally
    small and dependency-free.
    """
    normalized = unicodedata.normalize("NFC", str(text or "")).lower()
    return set(re.findall(r"\w+", normalized, flags=re.UNICODE))


# ─── SessionSummary dataclass ──────────────────────────────────────────────────

@dataclass
class SessionSummary:
    """Compressed summary of a Nana session.

    Generated periodically (every N turns) or on session end.
    Persisted in memory["session_summary"].
    """
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    session_start: float = field(default_factory=time.time)
    session_end: float = field(default_factory=time.time)
    text: str = ""  # "Ba debug Stardew adapter, rồi xem YouTube về Porter Robinson cover"
    topics: list[str] = field(default_factory=list)  # ["stardew", "youtube", "porter-robinson"]
    key_decisions: list[str] = field(default_factory=list)
    mood_trend: str = "neutral"  # productive | chill | frustrated | neutral
    turn_count: int = 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "session_start": self.session_start,
            "session_end": self.session_end,
            "text": self.text,
            "topics": list(self.topics),
            "key_decisions": list(self.key_decisions),
            "mood_trend": self.mood_trend,
            "turn_count": self.turn_count,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> SessionSummary:
        return cls(
            session_id=str(raw.get("session_id", uuid.uuid4().hex[:8])),
            session_start=float(raw.get("session_start", time.time())),
            session_end=float(raw.get("session_end", time.time())),
            text=str(raw.get("text", "")),
            topics=list(raw.get("topics", [])),
            key_decisions=list(raw.get("key_decisions", [])),
            mood_trend=str(raw.get("mood_trend", "neutral")),
            turn_count=int(raw.get("turn_count", 0)),
        )


# ─── MemoryClassifier ─────────────────────────────────────────────────────────

class MemoryClassifier:
    """Keyword-based memory type classifier. No LLM.

    Uses positive and negative keyword lists per type.
    Returns the type with the highest net score, or "ephemeral" if no clear winner.
    """

    # Positive = points toward type. Negative = points away from type.
    _PATTERNS: dict[str, tuple[list[str], list[str]]] = {
        "preference": (
            [
                "thích", "thich", "ghét", "ghet", "muốn", "muon", "không thích",
                "khong thich", "偏好", "sở thích", "so thich", "yêu thích",
                "yeu thich", "dị ứng", "di ung", "ghosting",
            ],
            [
                "haha", "ok", "ừm", "ừ", "okay", "fine", "lol", ":)", "thôi",
            ],
        ),
        "project_fact": (
            [
                "dự án", "du an", "project", "code", "git", "commit", "fix",
                "bug", "build", "deploy", "test", "smoke", "adapter", "module",
                "class", "function", "file", "runtime", "brain", "nana",
            ],
            [],
        ),
        "relationship": (
            [
                "xưng", "xung", "gọi", "goi", "vibe", "tên", "ten", "Ba", "Na",
                "Na Na", "ông", "ong", "chủ nhân", "chu nhan", "người yêu",
                "nguoi yeu", "vợ", "vo", "chồng", "chong",
            ],
            [],
        ),
        "routine": (
            [
                "lúc nào", "luc nao", "giờ", "gio", "thường", "thuong", "hay",
                "mỗi khi", "moi khi", "hàng ngày", "hang ngay", "buổi",
                "buoi", "sáng", "sang", "tối", "toi", "trưa", "trua",
            ],
            [],
        ),
        "technical_decision": (
            [
                "quyết định", "quyet dinh", "chọn", "chon", "dùng", "dung",
                "thay vì", "thay vi", "architect", "design", "pattern",
                "approach", "architecture", "framework", "thư viện", "thu vien",
                "library", "stack", "đổi sang", "doi sang", "migrate",
            ],
            [],
        ),
    }

    # Ephemeral: skip if matches these
    _EPHEMERAL_SIGNALS = (
        re.compile(r"^[\s\n]*$"),
        re.compile(r"^[\W_]+$"),
    )
    _EPHEMERAL_PATTERNS = (
        "haha", "lol", ":)", "=))", "ok mà", "ok ma", "whatever",
        "hehe", "huhu", "T^T", ":((", "chào", "chao", "hiện tại", "hien tai",
        "bây giờ", "bay gio", "lúc này", "luc nay", "đang làm gì",
        "dang lam gi",
    )

    def classify(self, text: str, importance_hint: str = "medium") -> str:
        """Classify text into a memory type.

        Args:
            text: Raw text to classify.
            importance_hint: "low" | "medium" | "high" from source importance.

        Returns:
            One of: preference | project_fact | relationship | routine | technical_decision | ephemeral
        """
        lowered = unicodedata.normalize("NFC", text).lower()

        # Fast ephemeral guards
        for pat in self._EPHEMERAL_SIGNALS:
            if pat.match(lowered):
                return "ephemeral"
        if any(sig in lowered for sig in self._EPHEMERAL_PATTERNS):
            return "ephemeral"

        # Score each type
        best_type = "ephemeral"
        best_score = 0.0

        for mem_type, (positive, negative) in self._PATTERNS.items():
            score = 0.0
            for kw in positive:
                if kw in lowered:
                    score += 1.0
            for kw in negative:
                if kw in lowered:
                    score -= 0.8
            if score > best_score:
                best_score = score
                best_type = mem_type

        if best_score < 0.5:
            return "ephemeral"

        # Importance boost: high importance hint bumps ephemeral-ish scores
        if importance_hint == "high" and best_score >= 0.5 and best_type == "ephemeral":
            # Re-score with higher threshold for high-importance sources
            if any(kw in lowered for kw in ["nhớ", "nho", "lưu", "luu", "ghi", "ghi"]):
                return "preference"

        return best_type

    def infer_tags(self, text: str, mem_type: str) -> list[str]:
        """Infer semantic tags from text and type.

        These are simple keyword-based tags, not embeddings.
        """
        lowered = unicodedata.normalize("NFC", text).lower()
        tags: list[str] = []

        # Type-based tags
        type_tag_map = {
            "preference": ["dialogue", "persona"],
            "project_fact": ["project", "technical"],
            "relationship": ["identity", "voice"],
            "routine": ["routine"],
            "technical_decision": ["architecture", "technical"],
            "ephemeral": ["ephemeral"],
        }
        tags.extend(type_tag_map.get(mem_type, []))

        # Context tags from keyword detection
        context_tags = {
            "youtube": ["youtube", "media"],
            "github": ["github", "version-control"],
            "stardew": ["stardew", "game"],
            "cursor": ["cursor", "ide"],
            "code": ["code", "technical"],
            "python": ["python", "technical"],
            "chat": ["chat"],
            "browser": ["browser"],
        }
        for kw, tag_list in context_tags.items():
            if kw in lowered:
                for t in tag_list:
                    if t not in tags:
                        tags.append(t)

        return tags


# ─── MemorySpine ───────────────────────────────────────────────────────────────

@dataclass
class _ScoredItem:
    item: MemoryItem
    score: float
    match_reason: str


class MemorySpine:
    """Central memory interface layered on top of nana/memory.py.

    Provides structured memory storage, session summary, retrieval, and reporting.
    All memory items are stored in memory["long_term"] as dicts (via MemoryItem.to_dict).
    Session summaries are stored in memory["session_summary"] (single dict).

    Backward-compatible: existing plain-string entries in long_term are loaded
    as ephemeral MemoryItem via load_item().
    """

    def __init__(self, memory_store: dict, session_summary_interval: int = DEFAULT_SESSION_SUMMARY_INTERVAL, *, writer=None):
        """Initialize with a reference to the nana/memory.py memory dict."""
        self._store_adapter = None if isinstance(memory_store, dict) else memory_store
        self._memory = memory_store if self._store_adapter is None else memory_store.snapshot()
        self._writer = writer
        self._mutation_lock = threading.RLock()
        self._classifier = MemoryClassifier()
        self._session_summary_interval = session_summary_interval
        self._turn_counter = 0
        self._session_start = time.time()
        self._session_id = uuid.uuid4().hex[:8]
        self._initialized = False

    def _ensure_initialized(self):
        """Lazy init: convert and lightly repair legacy long-term entries."""
        if self._initialized:
            return
        self._initialized = True
        long_term = self._memory.get("long_term", [])
        converted = 0
        upgraded = 0
        for i, entry in enumerate(long_term):
            if isinstance(entry, str):
                text = entry.strip()
                item_type = self._classifier.classify(text, "high")
                tags = self._classifier.infer_tags(text, item_type)
                long_term[i] = MemoryItem(
                    type=item_type,
                    text=text,
                    source="legacy",
                    confidence=0.65,
                    importance="high" if item_type != "ephemeral" else "medium",
                    tags=tags,
                    evidence="legacy_long_term",
                    decay_policy="eternal",
                ).to_dict()
                converted += 1
            elif isinstance(entry, dict):
                try:
                    item = load_item(entry)
                except ValueError:
                    continue
                if item.source == "legacy" and item.type == "ephemeral" and not _contains_secrets(item.text):
                    inferred_type = self._classifier.classify(item.text, "high")
                    if inferred_type != "ephemeral":
                        item.type = inferred_type
                        item.importance = "high" if item.importance == "medium" else item.importance
                        item.tags = self._classifier.infer_tags(item.text, inferred_type)
                        item.decay_policy = "eternal"
                        long_term[i] = item.to_dict()
                        upgraded += 1
        if converted:
            print(f"[MemorySpine] Converted {converted} legacy string entries to MemoryItem")
        if upgraded:
            print(f"[MemorySpine] Upgraded {upgraded} legacy ephemeral entries to durable MemoryItem")

    @property
    def turn_counter(self) -> int:
        return self._turn_counter

    def increment_turn(self) -> int:
        self._turn_counter += 1
        return self._turn_counter

    # ── Store / Update / Delete ──────────────────────────────────────────────

    def _publish_snapshot(self, snapshot, *, verify_item=None):
        """Commit before exposing changes; adapters need only snapshot/replace."""
        if self._writer is not None:
            snapshot = copy.deepcopy(snapshot)
            snapshot.setdefault("profile", {})
            snapshot.setdefault("schema_version", 1)
            snapshot.pop("snapshot_revision", None)
            receipt = self._writer.submit(snapshot)
            if not receipt.committed:
                raise OSError(getattr(receipt, "error", "Memory snapshot did not commit"))
            snapshot["snapshot_revision"] = receipt.snapshot_revision
            if verify_item is not None:
                # Use a fresh disk load, then retrieval, before acknowledging save.
                # load changes last_receipt to a read receipt, so retain the commit
                # receipt on the writer visible to the explicit-save caller.
                loaded = self._writer.load()
                if (loaded.get("snapshot_revision") != receipt.snapshot_revision
                        or loaded.get("schema_version") != receipt.schema_version):
                    raise OSError("Committed memory snapshot changed during verification")
                matches = MemorySpine(copy.deepcopy(loaded)).retrieve(
                    verify_item.text, limit=max(5, len(loaded["long_term"])), allow_embeddings=False)
                if not any(item.id == verify_item.id and item.text == verify_item.text
                           and item.source_event_id == verify_item.source_event_id
                           and item.source == verify_item.source and item.evidence == verify_item.evidence for item in matches):
                    raise OSError("Committed memory failed restart retrieval verification")
                self._writer.last_receipt = receipt
        elif verify_item is not None:
            raise OSError("Explicit durable save requires a persistence writer")
        self._memory.clear()
        self._memory.update(snapshot)
        if self._store_adapter is not None:
            self._store_adapter.replace(snapshot)

    def store_explicit_fact(self, text, *, source_event_id, source, evidence) -> MemoryRecord:
        """Store a private explicit fact, acknowledging only verified durability."""
        with self._mutation_lock:
            return self._store_explicit_fact(text, source_event_id=source_event_id, source=source, evidence=evidence)

    def _store_explicit_fact(self, text, *, source_event_id, source, evidence):
        text = str(text or "").strip()
        source_event_id = str(source_event_id or "").strip()
        if not text or not str(source_event_id or "").strip():
            raise ValueError("Explicit facts require text and source_event_id")
        if source not in {"private", "chat", "user", "owner", "system"}:
            raise ValueError("Public input cannot write private explicit facts")
        if _contains_secrets(text) or _contains_secrets(str(evidence)):
            raise ValueError("Secrets are session-only and cannot be explicitly persisted")
        snapshot = copy.deepcopy(self._memory)
        # Repeated source events keep the original immutable provenance and ID.
        for raw in snapshot.get("long_term", []):
            if isinstance(raw, dict) and raw.get("source_event_id") == source_event_id:
                if raw.get("text") != text:
                    raise ValueError("Conflicting explicit fact for source_event_id")
                item = load_item(raw)
                self._publish_snapshot(snapshot, verify_item=item)
                return item
        item_type = self._classifier.classify(text, "high")
        if item_type == "ephemeral":
            item_type = "project_fact"
        item = MemoryRecord(type=item_type, text=text, source=source,
                            source_event_id=str(source_event_id), evidence=evidence,
                            importance="high", confidence=0.9, decay_policy="eternal",
                            tags=self._classifier.infer_tags(text, item_type))
        snapshot.setdefault("long_term", []).insert(0, item.to_dict())
        self._publish_snapshot(snapshot, verify_item=item)
        return item

    def _set_pin_exact(self, item_id, pinned):
        with self._mutation_lock:
            return self._set_pin_locked(item_id, pinned)

    def _set_pin_locked(self, item_id, pinned):
        snapshot = copy.deepcopy(self._memory)
        for raw in snapshot.get("long_term", []):
            if isinstance(raw, dict) and raw.get("id") == item_id:
                raw["pinned"] = pinned
                self._publish_snapshot(snapshot)
                return [item_id]
        return []

    def pin_memory(self, item_id):
        return self._set_pin_exact(item_id, True)

    def unpin_memory(self, item_id):
        return self._set_pin_exact(item_id, False)

    def delete_memory_ids(self, item_ids):
        with self._mutation_lock:
            return self._delete_memory_ids_locked(item_ids)

    def _delete_memory_ids_locked(self, item_ids):
        targets = set(item_ids)
        snapshot = copy.deepcopy(self._memory)
        affected = [raw["id"] for raw in snapshot.get("long_term", [])
                    if isinstance(raw, dict) and raw.get("id") in targets]
        if affected:
            snapshot["long_term"] = [raw for raw in snapshot["long_term"]
                                     if not (isinstance(raw, dict) and raw.get("id") in targets)]
            self._publish_snapshot(snapshot)
        return affected

    def store_item(
        self,
        text: str,
        item_type: Optional[str] = None,
        importance: str = "medium",
        source: str = "chat",
        confidence: float = 0.7,
        tags: Optional[list[str]] = None,
        evidence: str = "",
        decay_policy: str = "eternal",
        pinned: bool = False,
        source_event_id: str = "",
    ) -> MemoryItem:
        """Store a new memory item. Returns the created MemoryItem.

        If item_type is None, uses MemoryClassifier to infer it.
        Duplicates (same text within 24h) are merged into the existing item.
        Omitted provenance remains unknown for legacy direct callers. Duplicate
        updates preserve the original source_event_id rather than invent history.
        """
        self._ensure_initialized()
        now = time.time()

        if item_type is None:
            item_type = self._classifier.classify(text, importance)

        if tags is None:
            tags = self._classifier.infer_tags(text, item_type)

        # Secret hygiene: mark ephemeral before duplicate check
        if _contains_secrets(text):
            item_type = "ephemeral"
            decay_policy = "session"

        # Merge duplicate: same text within 24h → update existing
        existing = self._find_by_text(text, hours=24)
        if existing is not None:
            existing.updated_at = now
            existing.last_used_at = now
            # Re-classify if new call has a real type and existing was ephemeral due to secrets
            if item_type != "ephemeral" and existing.type == "ephemeral":
                existing.type = item_type
            existing.confidence = max(existing.confidence, confidence)
            # Merge tags (union)
            for t in tags:
                if t not in existing.tags:
                    existing.tags.append(t)
            self._sync_to_memory(existing)
            if self._store_adapter is not None:
                self._store_adapter.replace(self._memory)
            return existing

        item = MemoryItem(
            type=item_type,
            text=text,
            source=source,
            confidence=confidence,
            importance=importance,
            created_at=now,
            updated_at=now,
            last_used_at=now,
            tags=tags,
            evidence=evidence,
            decay_policy=decay_policy,
            pinned=pinned,
            source_event_id=source_event_id,
        )

        self._memory.setdefault("long_term", [])
        self._memory["long_term"].insert(0, item.to_dict())
        self._enforce_limit()
        if self._store_adapter is not None:
            self._store_adapter.replace(self._memory)
        return item

    def update_item(self, item_id: str, **fields) -> Optional[MemoryItem]:
        """Partial update. Returns updated MemoryItem or None if not found."""
        long_term = self._memory.get("long_term", [])
        for entry in long_term:
            if isinstance(entry, dict) and entry.get("id") == item_id:
                now = time.time()
                entry["updated_at"] = now
                for key, val in fields.items():
                    if key in ("type", "text", "source", "confidence", "importance",
                               "tags", "evidence", "decay_policy", "pinned"):
                        entry[key] = val
                item = load_item(entry)
                if self._store_adapter is not None:
                    self._store_adapter.replace(self._memory)
                return item
        return None

    def delete_item(self, item_id: str) -> bool:
        """Delete by id. Returns True if found and removed."""
        return bool(self.delete_memory_ids([item_id]))

    def get_item(self, item_id: str) -> Optional[MemoryItem]:
        """Get by id. Returns MemoryItem or None."""
        long_term = self._memory.get("long_term", [])
        for entry in long_term:
            if isinstance(entry, dict) and entry.get("id") == item_id:
                return load_item(entry)
        return None

    def pin_item(self, item_id: str) -> bool:
        """Pin an item. Pinned items are immune to decay."""
        return bool(self.pin_memory(item_id))

    def unpin_item(self, item_id: str) -> bool:
        """Unpin an item."""
        return bool(self.unpin_memory(item_id))

    # ── Retrieval ────────────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        limit: int = 5,
        recency_weight: float = 0.3,
        relevance_weight: float = 0.5,
        importance_weight: float = 0.2,
        *,
        allow_embeddings: bool = True,
    ) -> list[MemoryItem]:
        """Retrieve relevant memory items scored by recency + relevance + importance.

        Retrieval is hybrid:
        1. Tag match → high relevance boost.
        2. Keyword token overlap → moderate relevance.
        3. Embedding fallback (NANA_USE_EMBEDDINGS=1) only if tag+keyword ambiguous.

        Args:
            query: User query string.
            limit: Max items to return.
            recency_weight: Weight for recency score (0-1).
            relevance_weight: Weight for relevance score (0-1).
            importance_weight: Weight for importance score (0-1).

        Returns:
            List of MemoryItem, sorted by weighted score descending.
        """
        self._ensure_initialized()
        now = time.time()
        long_term = self._memory.get("long_term", [])
        query_lower = unicodedata.normalize("NFC", query).lower()
        query_tokens = _tokenize(query_lower)

        scored: list[_ScoredItem] = []

        for raw in long_term:
            try:
                item = load_item(raw)
            except ValueError:
                continue

            if item.type == "ephemeral" and not item.pinned:
                continue

            # Skip if session-only and we are not in that session
            if item.decay_policy == "session":
                # Session items without pinned are only accessible within 24h of creation
                if item.age_days(now) > 1.0:
                    continue

            # Recency score: fresh = higher (decays after 7 days)
            age_days = item.age_days(now)
            recency_score = max(0.0, 1.0 - (age_days / 7.0)) if age_days <= 7 else 0.0

            # Relevance score: tag match + keyword overlap
            relevance_score = 0.0
            match_reason = ""

            # Tag match (exact)
            item_tag_set = set(t.lower() for t in item.tags)
            tag_overlap = query_tokens & item_tag_set
            if tag_overlap:
                relevance_score += 0.6 * (len(tag_overlap) / max(len(query_tokens), 1))
                match_reason = f"tag:{','.join(tag_overlap)}"

            # Keyword token overlap in text
            item_text_tokens = _tokenize(item.text)
            text_overlap = query_tokens & item_text_tokens
            if text_overlap:
                overlap_bonus = 0.4 * (len(text_overlap) / max(len(query_tokens), 1))
                relevance_score += overlap_bonus
                if not match_reason:
                    match_reason = f"keyword:{','.join(text_overlap)}"

            # Embedding fallback (env-gated)
            if allow_embeddings and relevance_score < 0.1 and _use_embeddings():
                emb_score = self._embedding_score(item, query, query_lower)
                if emb_score > 0.3:
                    relevance_score = emb_score
                    match_reason = "embedding"

            if relevance_score < 0.05:
                continue

            # Importance score
            importance_map = {"low": 0.0, "medium": 0.5, "high": 1.0}
            importance_score = importance_map.get(item.importance, 0.0)
            if item.pinned:
                importance_score = 1.0  # pinned always high importance

            # Weighted total
            total = (
                recency_weight * recency_score
                + relevance_weight * relevance_score
                + importance_weight * importance_score
            )

            scored.append(_ScoredItem(item=item, score=total, match_reason=match_reason))

        scored.sort(key=lambda x: x.score, reverse=True)
        return [s.item for s in scored[:limit]]

    def _embedding_score(self, item: MemoryItem, query: str, query_lower: str) -> float:
        """Compute embedding cosine similarity. Returns 0.0 if unavailable."""
        try:
            from nana.brain.gpt import create_embedding
        except Exception:
            return 0.0
        try:
            import numpy as np
        except Exception:
            return 0.0
        try:
            vec_item = create_embedding(item.text)
            vec_query = create_embedding(query)
            dot = float(np.dot(vec_item, vec_query))
            norm_item = float(np.linalg.norm(vec_item))
            norm_query = float(np.linalg.norm(vec_query))
            if norm_item == 0 or norm_query == 0:
                return 0.0
            return max(0.0, min(1.0, (dot / (norm_item * norm_query) + 1) / 2))
        except Exception:
            return 0.0

    # ── Session Summary ──────────────────────────────────────────────────────

    def generate_session_summary(self, chat_turns: int = 0) -> SessionSummary:
        """Generate a session summary from recent long_term entries and turn count.

        This is a code-only summarization — no LLM call in the base phase.
        LLM-based summarization can be added as phase 2.

        For now, generates a simple extractive summary:
        - Takes latest N long_term items by type.
        - Joins their text into a brief summary.
        """
        self._ensure_initialized()
        now = time.time()
        long_term = self._memory.get("long_term", [])

        # Collect recent high-importance items
        recent_items: list[MemoryItem] = []
        for raw in long_term[:20]:
            try:
                item = load_item(raw)
                if item.age_days(now) <= 1.0:  # within last 24h
                    recent_items.append(item)
            except ValueError:
                continue

        # Extract topics from tags
        all_tags: dict[str, int] = {}
        for item in recent_items:
            for tag in item.tags:
                all_tags[tag] = all_tags.get(tag, 0) + 1
        top_topics = sorted(all_tags, key=all_tags.get, reverse=True)[:5]

        # Build summary text from high-importance items
        summary_parts: list[str] = []
        for item in recent_items:
            if item.importance == "high" and len(summary_parts) < 5:
                summary_parts.append(item.text[:100])

        summary_text = "; ".join(summary_parts) if summary_parts else "No significant events."

        # Mood trend from emotion if available
        mood_trend = "neutral"
        emotion = self._memory.get("emotion", {})
        if emotion:
            affection = emotion.get("affection", 0.5)
            annoyance = emotion.get("annoyance", 0.0)
            if affection > 0.7 and annoyance < 0.2:
                mood_trend = "chill"
            elif affection < 0.3 or annoyance > 0.5:
                mood_trend = "frustrated"
            elif affection > 0.6 and annoyance < 0.3:
                mood_trend = "productive"

        summary = SessionSummary(
            session_id=self._session_id,
            session_start=self._session_start,
            session_end=now,
            text=summary_text,
            topics=top_topics,
            key_decisions=[],
            mood_trend=mood_trend,
            turn_count=chat_turns or self._turn_counter,
        )

        # Persist to memory
        self._memory["session_summary"] = summary.to_dict()
        return summary

    # ── Report Formatters ────────────────────────────────────────────────────

    def build_retrieval_block(self, query: str, limit: int = 5) -> str:
        """Build a formatted prompt block of relevant memories for injection."""
        items = self.retrieve(query, limit=limit)
        if not items:
            return ""
        lines = ["[RELEVANT MEMORY]", "---"]
        for i, item in enumerate(items, 1):
            lines.append(f"  [{i}] ({item.type}, {item.importance}) {item.text}")
            if item.tags:
                lines.append(f"      tags: {', '.join(item.tags)}")
        lines.append("---")
        return "\n".join(lines)

    def format_status_report(self) -> list[str]:
        """Format /memory-status output."""
        self._ensure_initialized()
        long_term = self._memory.get("long_term", [])
        session_summary = self._memory.get("session_summary")

        # Count by type
        counts: dict[str, int] = {}
        pinned_count = 0
        total_items = 0
        for raw in long_term:
            try:
                item = load_item(raw)
                total_items += 1
                counts[item.type] = counts.get(item.type, 0) + 1
                if item.pinned:
                    pinned_count += 1
            except ValueError:
                total_items += 1
                counts["legacy"] = counts.get("legacy", 0) + 1

        lines = [
            "🧠 CORE-MEMORY-1 Status",
            "---",
            f"  Total items: {total_items}",
            f"  Pinned: {pinned_count}",
            f"  Session turn: {self._turn_counter}",
            "  By type:",
        ]
        for t in MEMORY_TYPES:
            if counts.get(t):
                lines.append(f"    {t}: {counts[t]}")

        if session_summary:
            ss = SessionSummary.from_dict(session_summary)
            age_min = int((time.time() - ss.session_start) / 60)
            lines.extend([
                "---",
                f"  Session [{ss.session_id[:8]}]: {age_min}m ago, {ss.turn_count} turns",
                f"  Mood: {ss.mood_trend}",
                f"  Topics: {', '.join(ss.topics) if ss.topics else 'none'}",
                f"  Summary: {ss.text[:120]}",
            ])

        lines.append("---")
        return lines

    def format_search_report(self, query: str) -> list[str]:
        """Format /memory-search output."""
        items = self.retrieve(query, limit=10)
        if not items:
            return ["🧠 Memory Search", f'  No results for: "{query}"', "  (try fewer keywords or /memory-status)"]
        lines = [f"🧠 Memory Search: {query}", f"  Found {len(items)} results:", "---"]
        for i, item in enumerate(items, 1):
            age = item.age_days()
            age_str = f"{age:.1f}d ago" if age >= 1 else f"{int(age * 24)}h ago"
            lines.append(
                f"  [{i}] {item.type} | {item.importance} | {age_str}"
            )
            lines.append(f"      {item.text[:120]}")
            if item.tags:
                lines.append(f"      tags: {', '.join(item.tags)}")
        lines.append("---")
        return lines

    def export_memory(self) -> dict:
        """Full structured export for /memory-export."""
        self._ensure_initialized()
        long_term = self._memory.get("long_term", [])
        items: list[dict] = []
        for raw in long_term:
            try:
                items.append(load_item(raw).to_dict())
            except ValueError:
                items.append({"raw": str(raw)})

        session_summary = self._memory.get("session_summary")
        return {
            "core_memory_version": "1.0",
            "phase": "CORE-MEMORY-1",
            "exported_at": time.time(),
            "total_items": len(items),
            "turn_counter": self._turn_counter,
            "session_id": self._session_id,
            "session_summary": session_summary,
            "items": items,
        }

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _find_by_text(self, text: str, hours: int = 24) -> Optional[MemoryItem]:
        """Find existing item with same text within N hours."""
        cutoff = time.time() - (hours * 3600)
        long_term = self._memory.get("long_term", [])
        for raw in long_term:
            try:
                item = load_item(raw)
                if item.text == text and item.created_at >= cutoff:
                    return item
            except ValueError:
                continue
        return None

    def _sync_to_memory(self, item: MemoryItem):
        """Sync a loaded MemoryItem back to memory["long_term"] dict list."""
        long_term = self._memory.get("long_term", [])
        for i, raw in enumerate(long_term):
            if raw.get("id") == item.id:
                long_term[i] = item.to_dict()
                return
        long_term.insert(0, item.to_dict())

    def _enforce_limit(self):
        """Cap long_term at 50 items, evicting oldest ephemeral non-pinned items first."""
        long_term = self._memory.get("long_term", [])
        while len(long_term) > 50:
            # Find oldest non-pinned ephemeral item
            oldest_idx = -1
            oldest_time = float("inf")
            for i, raw in enumerate(long_term):
                if not isinstance(raw, dict) or raw.get("source") == "legacy":
                    continue
                try:
                    item = load_item(raw)
                    if item.type == "ephemeral" and not item.pinned and item.created_at < oldest_time:
                        oldest_time = item.created_at
                        oldest_idx = i
                except ValueError:
                    continue
            if oldest_idx == -1:
                break
            long_term.pop(oldest_idx)

    def reset_session(self):
        """Reset session counters and start new session."""
        self._turn_counter = 0
        self._session_start = time.time()
        self._session_id = uuid.uuid4().hex[:8]


# ─── Secret detection ─────────────────────────────────────────────────────────

_SECRET_RE = re.compile(
    r"|".join(p.pattern for p in _SECRET_PATTERNS),
    re.IGNORECASE,
)


def _contains_secrets(text: str) -> bool:
    from .history_privacy import contains_history_secret
    return bool(_SECRET_RE.search(text)) or contains_history_secret(text)


def _use_embeddings() -> bool:
    return os.environ.get("NANA_USE_EMBEDDINGS", "0") == "1"


def format_prompt_memory_rules(long_term: list, limit: int = 50) -> str:
    """Render mixed legacy/structured long-term memory safely for prompt rules.

    CORE-MEMORY-1 stores `memory["long_term"]` as dicts, while older prompt
    code expected plain strings. This formatter keeps the old prompt surface
    compatible without leaking session-only/secret/ephemeral structured items.
    Legacy plain strings are preserved unless they look like secrets.
    """
    lines: list[str] = []
    for raw in list(long_term or [])[:limit]:
        try:
            item = load_item(raw)
        except ValueError:
            text = str(raw or "").strip()
            if text and not _contains_secrets(text):
                lines.append(f"- {text}")
            continue

        text = (item.text or "").strip()
        if not text or _contains_secrets(text):
            continue

        # Structured ephemeral memories are not durable prompt rules. Legacy
        # strings were historically long-term facts, so preserve them.
        if item.type == "ephemeral" and item.source != "legacy" and not item.pinned:
            continue
        if item.decay_policy == "session" and not item.pinned:
            continue

        if item.source == "legacy":
            lines.append(f"- {text}")
        else:
            lines.append(f"- [{item.type}/{item.importance}] {text}")

    return "\n".join(lines)


# ─── Module-level singleton ────────────────────────────────────────────────────

_spine: Optional[MemorySpine] = None


def get_memory_spine(memory_store: Optional[dict] = None) -> MemorySpine:
    """Lazy singleton accessor. Call with memory dict on first use."""
    global _spine
    if _spine is None:
        if memory_store is None:
            from nana.memory import memory
            memory_store = memory
        _spine = MemorySpine(memory_store)
    return _spine


def reset_memory_spine():
    """Reset singleton (for testing)."""
    global _spine
    _spine = None
