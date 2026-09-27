import hashlib
import copy
import json
import re
import threading
import time
import uuid
import unicodedata
from pathlib import Path

from .config import CHAT_HISTORY_PATH, MEMORY_PATH
from .runtime.memory_persistence import AtomicMemoryWriter, WriteReceipt
from .runtime.history_privacy import (
    contains_history_secret,
    redact_history_text,
    sanitize_history_files,
    sanitize_history_state,
)

memory_lock = threading.RLock()
memory_file_lock = threading.Lock()
memory_action_lock = threading.Lock()

MEMORY_ACTION_TTL_SECONDS = 15 * 60
_memory_action_next_id = 1
_pending_memory_action = None

DEFAULT_MEMORY = {
    "profile": {"name": "", "location": ""},
    "long_term": [],
    "memory_labels": {},
    "short_term": [],
    "chat_log": [],
    "session_checkpoint": {},
    "emotion": {"affection": 0.5, "annoyance": 0.0, "playfulness": 0.5},
    "mood_continuity": {
        "phase": "STAGE-9A",
        "energy": 0.5,
        "warmth": 0.55,
        "playfulness": 0.48,
        "focus": 0.5,
        "tension": 0.18,
        "mood": "steady",
        "anchor": "default",
        "last_reason": "default",
        "last_source": "memory_default",
        "last_lane": "unknown",
        "last_update": 0.0,
        "last_decay": 0.0,
        "session_events": 0,
        "persisted_events": 0,
    },
    "persona": {
        "mode": "chill",
        "personality_intensity": 35,
        "target_intensity": 35,
        "manual_until": 0.0,
        "last_decay": 0.0,
        "last_reset": 0.0,
        "residue_level": 0,
        "residue_sources": {},
        "last_reason": "default",
        "transition_log": [],
        "silence": {
            "window_start": 0.0,
            "ambient_count": 0,
            "ambient_limit": 3,
        },
        "presence": {
            "enabled": True,
            "rhythm": "available",
            "last_reason": "default",
            "last_update": 0.0,
            "quiet_until": 0.0,
        },
    },
    "post_stream_lessons": {
        "phase": "STAGE-9C-B",
        "lessons": [],
        "dismissed": [],
        "audit_log": [],
    },
    "last_annoyance": 0,
}

MEMORY_BLACKLIST = ["mệt", "buồn ngủ", "ăn", "uống", "haha", "=))", "hehe"]
MEMORY_KEYWORDS = [
    "tên",
    "thích",
    "ghét",
    "muốn",
    "sinh",
    "ở",
    "sống",
    "làm",
    "học",
    "tuổi",
    "nhà",
    "nhớ",
    "nhớ kỹ",
    "máy tính",
    "cấu hình",
    "cpu",
    "gpu",
    "main",
    "ram",
    "ssd",
    "hdd",
    "rtx",
    "gtx",
    "intel",
    "amd",
    "asus",
]
EXPLICIT_MEMORY_WRITE_MARKERS = [
    "nhớ kỹ",
    "nhớ kĩ",
    "nho ky",
    "nho ki",
    "ghi nhớ",
    "ghi nho",
    "lưu memory",
    "luu memory",
    "lưu vào memory",
    "luu vao memory",
    "lưu trí nhớ",
    "luu tri nho",
    "nhớ nha",
    "nhớ nhé",
    "nho nha",
    "nho nhe",
    "đừng quên",
    "dung quen",
]
QUESTION_ENDINGS = (
    " không",
    " khong",
    " ko",
    " k",
    " hông",
    " hong",
    " khum",
    " chưa",
    " chua",
    " nhỉ",
    " nhi",
    " hả",
    " ha",
    " à",
    " a",
    " chứ",
    " chu",
)
QUESTION_STARTERS = (
    "ai ",
    "cái gì",
    "cai gi",
    "gì ",
    "gi ",
    "tại sao",
    "tai sao",
    "vì sao",
    "vi sao",
    "khi nào",
    "khi nao",
    "bao giờ",
    "bao gio",
    "bao lâu",
    "bao lau",
    "ở đâu",
    "o dau",
    "như nào",
    "nhu nao",
    "thế nào",
    "the nao",
    "có phải",
    "co phai",
    "nana có",
    "nana co",
    "con có",
    "con co",
    "ông có",
    "ong co",
    "ba có",
    "ba co",
)


def _merge_defaults(data):
    merged = copy.deepcopy(DEFAULT_MEMORY)
    merged.update(data or {})
    merged["profile"] = {**DEFAULT_MEMORY["profile"], **merged.get("profile", {})}
    merged["emotion"] = {**DEFAULT_MEMORY["emotion"], **merged.get("emotion", {})}
    merged["mood_continuity"] = {**DEFAULT_MEMORY["mood_continuity"], **merged.get("mood_continuity", {})}
    merged["persona"] = {**DEFAULT_MEMORY["persona"], **merged.get("persona", {})}
    merged["post_stream_lessons"] = {
        **DEFAULT_MEMORY["post_stream_lessons"],
        **merged.get("post_stream_lessons", {}),
    }
    for key in ("long_term", "short_term", "chat_log"):
        merged.setdefault(key, [])
    merged["memory_labels"] = dict(merged.get("memory_labels", {}))
    return merged


def load_memory(path: Path = MEMORY_PATH):
    # Existing invalid primary+backup must fail closed, never become defaults.
    return _merge_defaults(_HistorySafeMemoryWriter(path).load())


class _HistorySafeMemoryWriter(AtomicMemoryWriter):
    """Mask current/previous history before use, recovery and backup rotation."""

    def _read(self, path):
        return sanitize_history_state(super()._read(path))

    def submit(self, snapshot):
        return super().submit(sanitize_history_state(snapshot))


def save_memory(data, path: Path = MEMORY_PATH):
    with memory_lock:
        snapshot = _merge_defaults(sanitize_history_state(data))
        snapshot.pop("snapshot_revision", None)
        writer = _memory_writer if Path(path) == Path(MEMORY_PATH) else _HistorySafeMemoryWriter(path)
        return writer.submit(snapshot)


_memory_writer = _HistorySafeMemoryWriter(MEMORY_PATH)
memory = load_memory()


def save_memory_async():
    """Compatibility facade: ordered synchronous commit, no daemon lifecycle.

    Keep the memory lock through snapshot and commit so a delayed caller cannot
    write an older snapshot after a newer save. Callers may inspect the receipt.
    """
    with memory_lock:
        snapshot = sanitize_history_state(memory)
        for key in ("short_term", "chat_log", "session_checkpoint", "session_summary"):
            if key in snapshot:
                memory[key] = copy.deepcopy(snapshot[key])
        snapshot["last_interaction"] = time.time()
        receipt = save_memory(snapshot)
        if receipt.committed:
            memory["schema_version"] = receipt.schema_version
            memory["snapshot_revision"] = receipt.snapshot_revision
        return receipt


def flush_memory():
    return _memory_writer.flush()


def store_explicit_fact(text, *, source_event_id, source="private", evidence=""):
    if contains_history_secret(text) or contains_history_secret(evidence):
        raise ValueError("Secrets cannot be stored as durable memory")
    from .runtime.memory_spine import MemorySpine
    with memory_lock:
        return MemorySpine(memory, writer=_memory_writer).store_explicit_fact(
            text, source_event_id=source_event_id, source=source, evidence=evidence)


def pin_memory(item_id):
    from .runtime.memory_spine import MemorySpine
    with memory_lock:
        return MemorySpine(memory, writer=_memory_writer).pin_memory(item_id)


def unpin_memory(item_id):
    from .runtime.memory_spine import MemorySpine
    with memory_lock:
        return MemorySpine(memory, writer=_memory_writer).unpin_memory(item_id)


def delete_memory_ids(item_ids):
    from .runtime.memory_spine import MemorySpine
    with memory_lock:
        return MemorySpine(memory, writer=_memory_writer).delete_memory_ids(item_ids)


def save_chat_log(line):
    with memory_file_lock:
        CHAT_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CHAT_HISTORY_PATH.open("a", encoding="utf-8") as file:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            file.write(f"[{timestamp}] {redact_history_text(line)}\n")


def load_recent_chat(n=20):
    try:
        with memory_file_lock, CHAT_HISTORY_PATH.open("r", encoding="utf-8") as file:
            lines = redact_history_text(file.read()).splitlines(keepends=True)
        filtered = [line for line in lines if "USER:" in line or "NANA:" in line]
        return "".join(filtered[-n:])
    except Exception as exc:
        print("Lỗi đọc chat_history:", exc)
        return ""


def migrate_history_privacy_files(*, apply=False):
    """Explicit history scrub; no migration runs on normal module import."""
    with memory_lock, memory_file_lock:
        return sanitize_history_files(MEMORY_PATH, CHAT_HISTORY_PATH, apply=apply)


def extract_important(text, *, source_event_id=None, source="private"):
    should_save, _reason, _details = memory_importance_decision(text)
    if not should_save:
        return

    clean_text = str(text or "").strip()
    date = time.strftime("%Y-%m-%d")
    source_event_id = source_event_id or "private:" + uuid.uuid4().hex
    if _details.get("explicit"):
        # Legacy private callers receive a fresh turn ID; scoped callers supply it.
        try:
            item = store_explicit_fact(clean_text,
                source_event_id=source_event_id,
                source=source, evidence=f"extract_important:{date}")
        except (ValueError, OSError) as exc:
            print(f"Memory explicit save not committed: {exc}")
            return None
        print(f"🧠 Nhớ quan trọng: {clean_text}")
        return item
    try:
        from nana.runtime.memory_spine import get_memory_spine

        lowered = clean_text.lower()
        explicit = bool(_details.get("explicit"))
        item_type = None
        if explicit and any(
            marker in lowered
            for marker in ("thích", "thich", "ghét", "ghet", "muốn", "muon", "không thích", "khong thich")
        ):
            item_type = "preference"

        with memory_lock:
            spine = get_memory_spine(memory)
            spine.store_item(
                clean_text,
                item_type=item_type,
                importance="high" if explicit else "medium",
                source="chat",
                confidence=0.9 if explicit else 0.75,
                evidence=f"extract_important:{date}",
                source_event_id=source_event_id,
            )
        save_memory_async()
        print(f"🧠 Nhớ quan trọng: {clean_text}")
        return
    except Exception:
        # Legacy fallback keeps Nana usable if the Spine module is unavailable.
        entry = f"[{date}] {clean_text}"
        with memory_lock:
            if entry not in memory["long_term"]:
                memory["long_term"].append(entry)
        save_memory_async()
        print(f"🧠 Nhớ quan trọng: {clean_text}")


def memory_importance_decision(text):
    lowered = str(text or "").lower().strip()
    if not lowered:
        return False, "empty", {"explicit": False, "keywords": [], "blacklist": []}

    if contains_history_secret(text):
        return False, "secret", {"explicit": False, "keywords": [], "blacklist": []}

    explicit = has_explicit_memory_write_intent(lowered)
    keyword_hits = [keyword for keyword in MEMORY_KEYWORDS if keyword in lowered]
    blacklist_hits = [item for item in MEMORY_BLACKLIST if item in lowered]
    details = {"explicit": explicit, "keywords": keyword_hits, "blacklist": blacklist_hits}
    if not explicit and is_rehearsal_or_sample_memory(lowered):
        return False, "rehearsal_or_sample", details
    if not explicit and is_transient_memory_candidate(lowered):
        return False, "transient_or_question", details

    words = lowered.split()
    if not explicit and len(words) < 6:
        return False, "too_short", details

    if blacklist_hits and not explicit:
        return False, "blacklist", details

    if explicit:
        return True, "explicit_memory_write", details
    if not keyword_hits:
        return False, "no_memory_keyword", details
    return True, "keyword_match", details


def is_rehearsal_or_sample_memory(lowered):
    """Skip prompt/reply rehearsal text so long-term memory does not learn tests."""
    text = str(lowered or "").strip()
    if not text:
        return False
    if "nana sẽ nói thế này" in text:
        return True
    if "nana sẽ trả lời" in text and any(marker in text for marker in ("public", "viewer", "người xem", "khán giả")):
        return True
    if "nana là nana" in text and any(marker in text for marker in ("không nhận vai trợ lý", "không phải quầy hỗ trợ", "trợ lý phục vụ")):
        return True
    if any(marker in text for marker in ("trả lời sao", "nói sao")) and any(marker in text for marker in ("viewer", "public", "người xem", "khán giả")):
        return True
    return False


def memory_filter_preview_report(text):
    should_save, reason, details = memory_importance_decision(text)
    keywords = details.get("keywords") or []
    blacklist = details.get("blacklist") or []
    return [
        "🧠 Memory Filter Test",
        "  Action: read-only; không lưu memory.",
        f"  Status: {'would_save' if should_save else 'would_skip'}",
        f"  Reason: {reason}",
        f"  Explicit write: {details.get('explicit', False)}",
        f"  Keywords: {', '.join(keywords) if keywords else 'none'}",
        f"  Blacklist: {', '.join(blacklist) if blacklist else 'none'}",
        f"  Source: {redact_history_text(text)}",
    ]


def is_transient_memory_candidate(lowered):
    if lowered.startswith("/"):
        return True
    if has_explicit_memory_write_intent(lowered):
        return False
    # Requests for an example/explanation are conversation, not owner facts.
    folded = ''.join(ch for ch in unicodedata.normalize('NFD', lowered)
                     if unicodedata.category(ch) != 'Mn').replace('đ', 'd')
    if re.search(r'\b(?:chi tiet (?:trong )?(?:cau )?chuyen|trong cau chuyen|dang viet truyen|cau chuyen tam)\b', folded):
        return True
    if re.match(r'^(?:(?:nana|con|em)[,\s]+)?(?:hay\s+)?(?:ke ten|neu mot|giai thich|tra loi)\b', folded):
        return True
    if is_memory_lookup_question(lowered):
        return True
    transient_patterns = [
        "đang mở trang gì",
        "mở trang gì",
        "trang gì",
        "chỉ muốn nói chuyện nhẹ",
        "chi muon noi chuyen nhe",
        "chỉ là chat thường",
        "chi la chat thuong",
        "muốn kiểm tra",
        "muon kiem tra",
        "kiểm tra nana",
        "kiem tra nana",
        "test chat",
        "thử phản ứng",
        "thu phan ung",
        "thử trả lời",
        "thu tra loi",
        "mấy giờ",
        "sinh nhật",
        "ngày sinh",
        "năm sinh",
        "sinh ngày",
        "khi nào",
        "bây giờ là",
        "hôm nay là",
        "có biết mở youtube",
        "biết mở youtube",
        "làm sao giúp mình code",
        "phase 3 được",
    ]
    question_words = ["không", "chưa", "nhỉ", "à", "hả", "?"]
    if any(pattern in lowered for pattern in transient_patterns):
        return True
    if is_conversational_question(lowered):
        return True
    if any(word in lowered for word in question_words) and any(
        state_word in lowered
        for state_word in ["đang", "hiện tại", "bây giờ", "mở", "chạy", "giúp"]
    ):
        return True
    return False


def has_explicit_memory_write_intent(lowered):
    if not any(marker in lowered for marker in EXPLICIT_MEMORY_WRITE_MARKERS):
        return False
    from .runtime.memory_grounding import is_memory_save_status_question
    if not is_memory_save_status_question(lowered):
        return True
    # A status inquiry may quote a write verb without authorizing a new write.
    # Keep mixed turns valid when they contain a fresh affirmative instruction.
    normalized = unicodedata.normalize('NFD', lowered)
    folded = ''.join(ch for ch in normalized if unicodedata.category(ch) != 'Mn').replace('đ', 'd')
    for clause in re.split(r'(?<=[.!?])\s+|\n+', folded):
        clause = clause.strip()
        instruction = re.match(
            r'^(?:(?:nana|con|em)[,\s]+)?(?:(?:hay|vui long|lam on)\s+)?'
            r'(?:nho ky|nho ki|ghi nho|dung quen|luu(?: vao)? (?:memory|tri nho|bo nho))\b',
            clause,
        )
        if instruction and (
            not is_conversational_question(clause) or ':' in clause
            or re.search(r'\b(?:hay|vui long|lam on)\b', instruction.group())
            or re.search(r'\bduoc khong[?!.\s]*$', clause)
        ):
            return True
        if '?' not in clause and re.search(r'\bnho (?:nha|nhe)[.!\s]*$', clause):
            return True
    return False


def is_conversational_question(lowered):
    cleaned = lowered.strip()
    if not cleaned:
        return False
    if "?" in cleaned:
        return True
    if cleaned.startswith(QUESTION_STARTERS):
        return True
    if cleaned.endswith(QUESTION_ENDINGS):
        return True
    return False


def is_memory_lookup_question(lowered):
    lookup_markers = ["gì", "nào", "nhỉ", "không", "khong", "ko", "hông", "hong", "chưa", "chua", "?", "bao nhiêu"]
    memory_subjects = ["máy", "máy tính", "cấu hình", "cpu", "gpu", "main", "ram", "tên", "tuổi", "sinh nhật", "ngày sinh", "năm sinh"]
    teaching_markers = ["nhớ", "nhớ kỹ", "dùng", "là", "của mình là", "của tôi là"]
    if not any(marker in lowered for marker in lookup_markers):
        return False
    if not any(subject in lowered for subject in memory_subjects):
        return False
    return not any(marker in lowered for marker in teaching_markers)


def memory_health_report():
    with memory_lock:
        long_term = list(memory.get("long_term", []))
        short_term = list(memory.get("short_term", []))
        chat_log = list(memory.get("chat_log", []))
        persona = dict(memory.get("persona", {}))
    long_chars = sum(len(str(item)) for item in long_term)
    short_chars = sum(len(str(item)) for item in short_term)
    chat_chars = sum(len(str(item)) for item in chat_log)
    vibe_heavy = [item for item in long_term if is_vibe_heavy_memory(str(item))]
    stale_like = [item for item in long_term if is_stale_like_memory(str(item))]
    lines = [
        "🧠 Memory Health",
        f"  Long-term: {len(long_term)}/50 item(s), {long_chars} chars",
        f"  Short-term: {len(short_term)}/16 item(s), {short_chars} chars",
        f"  Chat log: {len(chat_log)}/50 item(s), {chat_chars} chars",
        f"  Persona memory: mode={persona.get('mode')} residue={persona.get('residue_level')} sources={persona.get('residue_sources') or {}}",
        f"  Vibe-heavy long-term: {len(vibe_heavy)}",
        f"  Stale-like long-term: {len(stale_like)}",
        "  Action: read-only; chưa xóa/chưa nén memory.",
    ]
    if vibe_heavy[:3]:
        lines.append("  Vibe samples:")
        for item in vibe_heavy[:3]:
            lines.append(f"    - {trim_memory_line(item)}")
    if stale_like[:3]:
        lines.append("  Stale samples:")
        for item in stale_like[:3]:
            lines.append(f"    - {trim_memory_line(item)}")
    return lines


def memory_status_report():
    with memory_lock:
        long_term = list(memory.get("long_term", []))
        short_term = list(memory.get("short_term", []))
        chat_log = list(memory.get("chat_log", []))
        persona = dict(memory.get("persona", {}))
        labels = dict(memory.get("memory_labels", {}))

    counts = {"keep": 0, "review": 0, "stale-like": 0, "vibe-heavy": 0}
    for item in long_term:
        category, _reason = classify_long_term_memory(item)
        counts[category] = counts.get(category, 0) + 1

    active_keys = {memory_item_key(item) for item in long_term}
    active_labels = sum(1 for key in labels if key in active_keys)
    orphan_labels = len(labels) - active_labels
    pending = _get_pending_memory_action()
    pending_text = "none"
    if pending:
        remaining = max(0, int(pending["expires_at"] - time.time()))
        pending_text = f"{pending['kind']} #{pending['id']} ({len(pending.get('items', []))} item, {remaining}s)"

    return [
        "🧠 Memory Status",
        f"  Long-term: {len(long_term)}/50 | keep={counts.get('keep', 0)} review={counts.get('review', 0)} stale={counts.get('stale-like', 0)} vibe={counts.get('vibe-heavy', 0)}",
        f"  Short-term: {len(short_term)}/16 | Chat log: {len(chat_log)}/50",
        f"  Labels: active={active_labels} orphan={orphan_labels}",
        f"  Pending action: {pending_text}",
        f"  Persona: mode={persona.get('mode')} residue={persona.get('residue_level')} sources={persona.get('residue_sources') or {}}",
        "  Commands: /memory-review | /memory-labels | /memory-compact-preview | /memory-action",
        "  Action: read-only; không đổi memory.",
    ]


def memory_review_report(limit=20):
    with memory_lock:
        long_term = list(memory.get("long_term", []))
    rows = []
    counts = {"keep": 0, "review": 0, "stale-like": 0, "vibe-heavy": 0}
    for index, item in enumerate(long_term, start=1):
        category, reason = classify_long_term_memory(item)
        counts[category] = counts.get(category, 0) + 1
        rows.append((index, category, reason, trim_memory_line(item, limit=150)))
    lines = [
        "🧠 Memory Review",
        f"  Long-term items: {len(long_term)}/50",
        "  Counts: "
        f"keep={counts.get('keep', 0)}, "
        f"review={counts.get('review', 0)}, "
        f"stale-like={counts.get('stale-like', 0)}, "
        f"vibe-heavy={counts.get('vibe-heavy', 0)}",
        "  Action: preview only; chưa xóa/chưa nén memory.",
    ]
    if not rows:
        lines.append("  Items: none")
        return lines
    lines.append(f"  Showing: {min(limit, len(rows))}/{len(rows)}")
    for index, category, reason, text in rows[:limit]:
        lines.append(f"  #{index} | {category} | {reason}")
        lines.append(f"    {text}")
    return lines


def memory_labels_report(limit=20):
    with memory_lock:
        long_term = list(memory.get("long_term", []))
        labels = dict(memory.get("memory_labels", {}))

    active_keys = {memory_item_key(item): (index, str(item)) for index, item in enumerate(long_term, start=1)}
    active_rows = []
    orphan_rows = []
    for key, label in labels.items():
        decision = label.get("decision") if isinstance(label, dict) else str(label)
        updated_at = label.get("updated_at") if isinstance(label, dict) else None
        updated = "-"
        if updated_at:
            updated = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(updated_at))
        if key in active_keys:
            index, item = active_keys[key]
            active_rows.append((index, decision, updated, item))
        else:
            orphan_rows.append((key, decision, updated))

    lines = [
        "🏷️ Memory Labels",
        f"  Labels: {len(labels)}",
        f"  Active: {len(active_rows)}",
        f"  Orphan: {len(orphan_rows)}",
        "  Action: read-only; không đổi memory.",
    ]
    if active_rows:
        lines.append("  Active labels:")
        for index, decision, updated, item in active_rows[:limit]:
            lines.append(f"    #{index} | {decision} | {updated} | {trim_memory_line(item)}")
    if orphan_rows:
        lines.append("  Orphan labels:")
        for key, decision, updated in orphan_rows[:limit]:
            lines.append(f"    {key[:10]}... | {decision} | {updated}")
    return lines


def memory_compact_plan_report(limit=20):
    with memory_lock:
        long_term = list(memory.get("long_term", []))
    groups = {"keep": [], "review": [], "stale-like": [], "vibe-heavy": []}
    for index, item in enumerate(long_term, start=1):
        category, reason = classify_long_term_memory(item)
        groups.setdefault(category, []).append((index, reason, str(item)))

    keep_count = len(groups.get("keep", []))
    review_count = len(groups.get("review", []))
    drop_count = len(groups.get("stale-like", [])) + len(groups.get("vibe-heavy", []))
    lines = [
        "🧠 Memory Compact Plan",
        "  Mode: preview only",
        f"  Keep as-is: {keep_count}",
        f"  Needs Ba review: {review_count}",
        f"  Candidate to drop/avoid long-term: {drop_count}",
        "  Rule: chỉ compact khi Ba duyệt; không tự xóa memory.",
    ]
    if groups.get("keep"):
        lines.append("  Keep:")
        for index, reason, item in groups["keep"][:limit]:
            lines.append(f"    #{index} | {reason} | {trim_memory_line(item)}")
    if groups.get("review"):
        lines.append("  Review:")
        for index, reason, item in groups["review"][:limit]:
            lines.append(f"    #{index} | {reason} | {trim_memory_line(item)}")
    for category in ["stale-like", "vibe-heavy"]:
        if not groups.get(category):
            continue
        label = "Drop candidates" if category == "stale-like" else "Vibe cleanup candidates"
        lines.append(f"  {label}:")
        for index, reason, item in groups[category][:limit]:
            lines.append(f"    #{index} | {reason} | {trim_memory_line(item)}")
    return lines


def memory_compact_preview_report(limit=20):
    global _memory_action_next_id, _pending_memory_action
    with memory_lock:
        long_term = list(memory.get("long_term", []))

    candidates = []
    review_rows = []
    for index, item in enumerate(long_term, start=1):
        category, reason = classify_long_term_memory(item)
        if category in {"stale-like", "vibe-heavy"}:
            candidates.append(
                {
                    "index": index,
                    "category": category,
                    "reason": reason,
                    "item": str(item),
                    "item_id": item.get("id") if isinstance(item, dict) else None,
                }
            )
        elif category == "review":
            review_rows.append((index, reason, str(item)))

    lines = [
        "🧠 Memory Compact Preview",
        "  Mode: confirm-gated",
        f"  Safe cleanup candidates: {len(candidates)}",
        f"  Needs Ba review, not touched: {len(review_rows)}",
        "  Rule: chỉ xóa stale/vibe-heavy rõ ràng; mục review không tự động xóa.",
    ]
    if review_rows:
        lines.append("  Review only:")
        for index, reason, item in review_rows[:limit]:
            lines.append(f"    #{index} | {reason} | {trim_memory_line(item)}")

    if not candidates:
        with memory_action_lock:
            _pending_memory_action = None
        lines.append("  Pending action: none")
        lines.append("  Result: chưa có mục nào đủ chắc để auto cleanup.")
        return lines

    now = time.time()
    with memory_action_lock:
        action_id = _memory_action_next_id
        _memory_action_next_id += 1
        _pending_memory_action = {
            "id": action_id,
            "kind": "memory.compact_cleanup",
            "created_at": now,
            "expires_at": now + MEMORY_ACTION_TTL_SECONDS,
            "items": candidates,
        }

    lines.append("  Cleanup candidates:")
    for row in candidates[:limit]:
        lines.append(
            f"    #{row['index']} | {row['category']} | "
            f"{row['reason']} | {trim_memory_line(row['item'])}"
        )
    lines.append(f"  Pending action ID: {action_id}")
    lines.append(f"  Confirm: /memory-confirm {action_id}")
    lines.append(f"  Cancel: /memory-cancel {action_id}")
    return lines


def memory_pending_action_report():
    action = _get_pending_memory_action()
    lines = ["🧠 Memory Pending Action"]
    if not action:
        lines.append("  Pending: none")
        return lines
    remaining = max(0, int(action["expires_at"] - time.time()))
    lines.extend(
        [
            f"  ID: {action['id']}",
            f"  Action: {action['kind']}",
            f"  Items: {len(action.get('items', []))}",
            f"  Expires in: {remaining}s",
            "  Would do: xóa đúng các long-term memory đã chọn trong preview.",
            f"  Confirm: /memory-confirm {action['id']}",
            f"  Cancel: /memory-cancel {action['id']}",
        ]
    )
    for row in action.get("items", [])[:10]:
        lines.append(
            f"    #{row['index']} | {row['category']} | {trim_memory_line(row['item'])}"
        )
    return lines


def _save_command_or_restore(before):
    """Caller holds memory_lock through mutation, persistence, and rollback."""
    try:
        receipt = save_memory_async()
        if receipt.committed:
            return True
    except (OSError, ValueError, TypeError):
        pass
    memory.clear()
    memory.update(before)
    return False


def memory_confirm_action(action_id):
    global _pending_memory_action
    action = _get_pending_memory_action()
    if not action:
        return ["🧠 Memory Confirm", "  Status: no_pending_action"]
    if str(action.get("id")) != str(action_id).strip():
        return [
            "🧠 Memory Confirm",
            "  Status: id_mismatch",
            f"  Pending ID: {action.get('id')}",
        ]

    with memory_lock:
        before = copy.deepcopy(memory)
        long_term = list(memory.get("long_term", []))
        selected_ids = {row["item_id"] for row in action.get("items", []) if row.get("item_id")}
        # Legacy strings have no persisted ID. Bind them to the exact reviewed
        # slot+value and fail closed if that slot changed before confirmation.
        legacy_slots = {row.get("index"): row.get("item") for row in action.get("items", []) if not row.get("item_id")}
        removed_items = [item for index, item in enumerate(long_term, start=1)
                         if (isinstance(item, dict) and item.get("id") in selected_ids)
                         or (index in legacy_slots and str(item) == legacy_slots[index])]
        remaining = [item for index, item in enumerate(long_term, start=1)
                     if not ((isinstance(item, dict) and item.get("id") in selected_ids)
                             or (index in legacy_slots and str(item) == legacy_slots[index]))]
        removed = len(long_term) - len(remaining)
        memory["long_term"] = remaining
        labels = dict(memory.get("memory_labels", {}))
        for target in removed_items:
            labels.pop(memory_item_key(target), None)
        memory["memory_labels"] = labels
        if removed and not _save_command_or_restore(before):
            return ["🧠 Memory Confirm", "  Status: save_failed", "  Removed: 0",
                    "  Note: memory restored; pending action retained for retry."]

    with memory_action_lock:
        _pending_memory_action = None

    return [
        "🧠 Memory Confirm",
        "  Status: applied",
        f"  Action: {action['kind']}",
        f"  Removed: {removed}",
        "  Safety: confirm_required, exact_match_only, selected_items_only",
    ]


def memory_cancel_action(action_id=None):
    global _pending_memory_action
    action = _get_pending_memory_action()
    if not action:
        return ["🧠 Memory Cancel", "  Status: no_pending_action"]
    if action_id is not None and str(action.get("id")) != str(action_id).strip():
        return [
            "🧠 Memory Cancel",
            "  Status: id_mismatch",
            f"  Pending ID: {action.get('id')}",
        ]
    with memory_action_lock:
        _pending_memory_action = None
    return [
        "🧠 Memory Cancel",
        "  Status: canceled",
        f"  ID: {action.get('id')}",
    ]


def memory_keep_report(index_text):
    index = _parse_memory_index(index_text)
    if index is None:
        return ["🧠 Memory Keep", "  Status: invalid_index", "  Usage: /memory-keep <index>"]
    with memory_lock:
        before = copy.deepcopy(memory)
        long_term = list(memory.get("long_term", []))
        if index < 1 or index > len(long_term):
            return ["🧠 Memory Keep", "  Status: index_out_of_range", f"  Long-term items: {len(long_term)}"]
        raw = long_term[index - 1]
        if isinstance(raw, str):
            from .runtime.memory_spine import load_item
            raw = load_item(raw).to_dict()
            memory["long_term"][index - 1] = raw
        raw.setdefault("id", uuid.uuid4().hex[:8])
        raw["pinned"] = True
        item = raw
        key = memory_item_key(item)
        labels = dict(memory.get("memory_labels", {}))
        labels[key] = {"decision": "keep", "updated_at": time.time()}
        memory["memory_labels"] = labels
        if not _save_command_or_restore(before):
            return ["🧠 Memory Keep", "  Status: save_failed", "  Note: memory restored; keep was not saved."]
    return [
        "🧠 Memory Keep",
        "  Status: marked_keep",
        f"  Index: {index}",
        f"  Item: {trim_memory_line(item)}",
        "  Note: pinned=True; nội dung memory được giữ nguyên.",
    ]


def memory_unkeep_report(index_text):
    index = _parse_memory_index(index_text)
    if index is None:
        return ["🧠 Memory Unkeep", "  Status: invalid_index", "  Usage: /memory-unkeep <index>"]
    with memory_lock:
        before = copy.deepcopy(memory)
        long_term = list(memory.get("long_term", []))
        if index < 1 or index > len(long_term):
            return ["🧠 Memory Unkeep", "  Status: index_out_of_range", f"  Long-term items: {len(long_term)}"]
        raw = long_term[index - 1]
        item = raw
        key = memory_item_key(item)
        labels = dict(memory.get("memory_labels", {}))
        existed = labels.pop(key, None) is not None
        if isinstance(raw, dict) and raw.get("pinned"):
            existed = True
            raw["pinned"] = False
        memory["memory_labels"] = labels
        if existed and not _save_command_or_restore(before):
            return ["🧠 Memory Unkeep", "  Status: save_failed", "  Note: memory restored; unkeep was not saved."]
    return [
        "🧠 Memory Unkeep",
        "  Status: unmarked" if existed else "  Status: no_keep_label",
        f"  Index: {index}",
        f"  Item: {trim_memory_line(item)}",
    ]


def memory_drop_preview_report(index_text):
    global _memory_action_next_id, _pending_memory_action
    index = _parse_memory_index(index_text)
    if index is None:
        return ["🧠 Memory Drop Preview", "  Status: invalid_index", "  Usage: /memory-drop-preview <index>"]
    with memory_lock:
        long_term = list(memory.get("long_term", []))
    if index < 1 or index > len(long_term):
        return ["🧠 Memory Drop Preview", "  Status: index_out_of_range", f"  Long-term items: {len(long_term)}"]

    raw = long_term[index - 1]
    item = str(raw)
    category, reason = classify_long_term_memory(raw)
    if category == "keep":
        return [
            "🧠 Memory Drop Preview",
            "  Status: blocked_keep_item",
            f"  Index: {index}",
            f"  Item: {trim_memory_line(item)}",
            "  Note: mục này đang được phân loại keep; dùng /memory-unkeep trước nếu Ba thật sự muốn xét lại.",
        ]

    now = time.time()
    with memory_action_lock:
        action_id = _memory_action_next_id
        _memory_action_next_id += 1
        _pending_memory_action = {
            "id": action_id,
            "kind": "memory.drop_selected",
            "created_at": now,
            "expires_at": now + MEMORY_ACTION_TTL_SECONDS,
            "items": [
                {
                    "index": index,
                    "category": category,
                    "reason": f"manual_review: {reason}",
                    "item": item,
                    "item_id": raw.get("id") if isinstance(raw, dict) else None,
                }
            ],
        }
    return [
        "🧠 Memory Drop Preview",
        "  Status: pending_confirm",
        f"  ID: {action_id}",
        f"  Index: {index}",
        f"  Category: {category}",
        f"  Item: {trim_memory_line(item)}",
        f"  Confirm: /memory-confirm {action_id}",
        f"  Cancel: /memory-cancel {action_id}",
    ]


def _get_pending_memory_action():
    global _pending_memory_action
    with memory_action_lock:
        action = _pending_memory_action
        if not action:
            return None
        if time.time() > action.get("expires_at", 0):
            _pending_memory_action = None
            return None
        return {
            **action,
            "items": [dict(item) for item in action.get("items", [])],
        }


def classify_long_term_memory(item):
    if isinstance(item, dict) and item.get("pinned"):
        return "keep", "Ba pinned memory"
    text = str(item.get("text", "")) if isinstance(item, dict) else str(item or "")
    lowered = text.lower()
    label = memory_item_label(item)
    if label == "keep":
        return "keep", "Ba marked keep"
    if is_rehearsal_or_sample_memory(lowered):
        return "stale-like", "rehearsal/sample text"
    if is_transient_memory_candidate(lowered):
        return "stale-like", "transient/chat question"
    if is_vibe_heavy_memory(text):
        return "vibe-heavy", "social/lore tone may drift persona"
    if is_stale_like_memory(text):
        return "stale-like", "time/session-specific context"
    durable_markers = [
        "thích",
        "thich",
        "ghét",
        "ghet",
        "muốn",
        "muon",
        "máy tính",
        "may tinh",
        "cấu hình",
        "cau hinh",
        "cpu",
        "gpu",
        "ram",
        "ssd",
        "rtx",
        "gtx",
        "tên",
        "ten",
        "sinh",
        "nhà",
        "nha",
    ]
    if any(marker in lowered for marker in durable_markers):
        return "keep", "durable user preference/fact"
    return "review", "not obviously durable"


def is_vibe_heavy_memory(text):
    lowered = text.lower()
    markers = [
        "haha",
        "hahaha",
        "tà đạo",
        "ta dao",
        "teo-lite",
        "gạt tàn",
        "gat tan",
        "chiến thần",
        "chien than",
        "moah",
        "roleplay",
        "nữ vương",
        "nu vuong",
    ]
    return any(marker in lowered for marker in markers)


def is_stale_like_memory(text):
    lowered = text.lower()
    markers = [
        "đang xem",
        "dang xem",
        "đang mở",
        "dang mo",
        "vừa nãy",
        "vua nay",
        "hôm nay",
        "hom nay",
        "tweet này",
        "tweet nay",
        "bài này",
        "bai nay",
        "draft",
        "phase",
    ]
    return any(marker in lowered for marker in markers)


def trim_memory_line(text, limit=120):
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3].rstrip() + "..."


def memory_item_key(text):
    if isinstance(text, dict):
        if text.get("id"):
            return "id:" + str(text["id"])
        text = text.get("text", "")
    normalized = " ".join(str(text or "").split()).lower()
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def memory_item_label(text):
    key = memory_item_key(text)
    with memory_lock:
        label = memory.get("memory_labels", {}).get(key)
    if isinstance(label, dict):
        return label.get("decision")
    if isinstance(label, str):
        return label
    return None


MEMORY_LIMITS = {
    "long_term": 50,
    "short_term": 16,
    "chat_log": 50,
}

MEMORY_GOVERNANCE_FILTER_CASES = [
    {
        "name": "skip_transient_question",
        "text": "nana có muốn làm ido vtube ko",
        "save": False,
        "reason": "transient_or_question",
    },
    {
        "name": "save_explicit_birth_fact",
        "text": "nana sinh ngày 30 tháng 4 năm 2026 nhớ nha nana",
        "save": True,
        "reason": "explicit_memory_write",
    },
    {
        "name": "skip_command_input",
        "text": "/br",
        "save": False,
        "reason": "transient_or_question",
    },
    {
        "name": "skip_math_question",
        "text": "50 x 10 bằng bao nhiêu vậy nana",
        "save": False,
        "reason": "no_memory_keyword",
    },
]

MEMORY_GOVERNANCE_CLASSIFY_CASES = [
    {
        "name": "durable_preference",
        "item": "[2026-05-20] Ba thích cà phê sữa đá",
        "category": "keep",
    },
    {
        "name": "transient_question",
        "item": "[2026-05-20] nana có muốn làm ido vtube ko",
        "category": "stale-like",
    },
    {
        "name": "vibe_heavy",
        "item": "[2026-05-20] haha teo-lite chiến thần",
        "category": "vibe-heavy",
    },
    {
        "name": "session_specific",
        "item": "[2026-05-20] đang xem tweet này",
        "category": "stale-like",
    },
    {
        "name": "ambiguous_note",
        "item": "[2026-05-20] ghi chú chung chung",
        "category": "review",
    },
]


def memory_governance_snapshot():
    with memory_lock:
        long_term = list(memory.get("long_term", []))
        short_term = list(memory.get("short_term", []))
        chat_log = list(memory.get("chat_log", []))
        labels = dict(memory.get("memory_labels", {}))

    active_keys = {memory_item_key(item) for item in long_term}
    active_labels = sum(1 for key in labels if key in active_keys)
    orphan_labels = len(labels) - active_labels
    label_issues = []
    for key, label in labels.items():
        if not isinstance(key, str) or not key:
            label_issues.append("invalid_key")
            continue
        if isinstance(label, dict):
            decision = label.get("decision")
            if decision not in {"keep", "review", "stale-like", "vibe-heavy", "drop"}:
                label_issues.append(f"{key[:8]}:bad_decision")
            updated_at = label.get("updated_at")
            if updated_at is not None and not isinstance(updated_at, (int, float)):
                label_issues.append(f"{key[:8]}:bad_updated_at")
        elif isinstance(label, str):
            if label not in {"keep", "review", "stale-like", "vibe-heavy", "drop"}:
                label_issues.append(f"{key[:8]}:bad_label")
        else:
            label_issues.append(f"{key[:8]}:bad_label_type")

    pending = _get_pending_memory_action()
    pending_status = "none"
    pending_issues = []
    if pending:
        pending_status = f"{pending.get('kind')}#{pending.get('id')}"
        for field in ("id", "kind", "created_at", "expires_at", "items"):
            if field not in pending:
                pending_issues.append(f"missing_{field}")
        if not isinstance(pending.get("items"), list):
            pending_issues.append("items_not_list")
        if pending.get("expires_at", 0) <= time.time():
            pending_issues.append("expired_not_cleared")

    string_entries = sum(1 for item in long_term if isinstance(item, str))
    structured_entries = sum(1 for item in long_term if isinstance(item, dict))
    other_entries = len(long_term) - string_entries - structured_entries
    classifications = {"keep": 0, "review": 0, "stale-like": 0, "vibe-heavy": 0}
    for item in long_term:
        category, _reason = classify_long_term_memory(item)
        classifications[category] = classifications.get(category, 0) + 1

    return {
        "long_term": len(long_term),
        "short_term": len(short_term),
        "chat_log": len(chat_log),
        "labels": len(labels),
        "active_labels": active_labels,
        "orphan_labels": orphan_labels,
        "label_issues": label_issues,
        "pending": pending,
        "pending_status": pending_status,
        "pending_issues": pending_issues,
        "string_entries": string_entries,
        "structured_entries": structured_entries,
        "other_entries": other_entries,
        "classifications": classifications,
    }


def memory_governance_filter_rows():
    rows = []
    for case in MEMORY_GOVERNANCE_FILTER_CASES:
        should_save, reason, details = memory_importance_decision(case["text"])
        passed = should_save == case["save"] and reason == case["reason"]
        rows.append({
            "name": case["name"],
            "text": case["text"],
            "passed": passed,
            "got": f"save={should_save} reason={reason}",
            "expected": f"save={case['save']} reason={case['reason']}",
            "details": details,
        })
    return rows


def memory_governance_classify_rows():
    rows = []
    for case in MEMORY_GOVERNANCE_CLASSIFY_CASES:
        category, reason = classify_long_term_memory(case["item"])
        passed = category == case["category"]
        rows.append({
            "name": case["name"],
            "item": case["item"],
            "passed": passed,
            "got": category,
            "expected": case["category"],
            "reason": reason,
        })
    return rows


def memory_governance_summary():
    snapshot = memory_governance_snapshot()
    filter_rows = memory_governance_filter_rows()
    classify_rows = memory_governance_classify_rows()
    capacity_issues = []
    for key, limit in MEMORY_LIMITS.items():
        count = snapshot[key]
        if count > limit:
            capacity_issues.append(f"{key}={count}/{limit}")
    pending_ok = not snapshot["pending_issues"]
    confirm_ok = MEMORY_ACTION_TTL_SECONDS >= 60
    legacy_ok = snapshot["other_entries"] == 0
    filter_ok = all(row["passed"] for row in filter_rows)
    classify_ok = all(row["passed"] for row in classify_rows)
    label_ok = not snapshot["label_issues"]
    rows = [
        ("capacity_limits", not capacity_issues, "ok" if not capacity_issues else ",".join(capacity_issues)),
        ("filter_regression", filter_ok, f"{sum(1 for row in filter_rows if row['passed'])}/{len(filter_rows)} pass"),
        ("classification_regression", classify_ok, f"{sum(1 for row in classify_rows if row['passed'])}/{len(classify_rows)} pass"),
        ("label_integrity", label_ok, f"active={snapshot['active_labels']} orphan={snapshot['orphan_labels']} issues={','.join(snapshot['label_issues']) if snapshot['label_issues'] else 'none'}"),
        ("pending_action_contract", pending_ok, f"pending={snapshot['pending_status']} issues={','.join(snapshot['pending_issues']) if snapshot['pending_issues'] else 'none'}"),
        ("confirm_gate_contract", confirm_ok, f"ttl={MEMORY_ACTION_TTL_SECONDS}s confirm_required=True"),
        ("legacy_store_known", legacy_ok, f"string={snapshot['string_entries']} structured={snapshot['structured_entries']} other={snapshot['other_entries']}"),
        ("decay_policy_known", True, "no_auto_decay; cleanup is review/confirm-gated"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "snapshot": snapshot,
        "filter_rows": filter_rows,
        "classify_rows": classify_rows,
    }


def memory_governance_test_report(text=None):
    lines = [
        "🧪 Memory Governance Test",
        "  Action: read-only; không lưu, không compact, không đổi label.",
    ]
    if text:
        should_save, reason, details = memory_importance_decision(text)
        synthetic_item = f"[{time.strftime('%Y-%m-%d')}] {text}"
        category, classify_reason = classify_long_term_memory(synthetic_item)
        keywords = details.get("keywords") or []
        blacklist = details.get("blacklist") or []
        lines.extend([
            f"  Status: {'would_save' if should_save else 'would_skip'}",
            f"  Reason: {reason}",
            f"  Classification if stored: {category} | {classify_reason}",
            f"  Explicit write: {details.get('explicit', False)}",
            f"  Keywords: {', '.join(keywords) if keywords else 'none'}",
            f"  Blacklist: {', '.join(blacklist) if blacklist else 'none'}",
            f"  Source: {redact_history_text(text)}",
            "  Execute: False",
        ])
        return lines

    summary = memory_governance_summary()
    lines.append(f"  Filter regression: {sum(1 for row in summary['filter_rows'] if row['passed'])}/{len(summary['filter_rows'])} pass")
    for row in summary["filter_rows"]:
        lines.append(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} | expected={row['expected']}")
    lines.append(f"  Classification regression: {sum(1 for row in summary['classify_rows'] if row['passed'])}/{len(summary['classify_rows'])} pass")
    for row in summary["classify_rows"]:
        lines.append(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} | expected={row['expected']} | reason={row['reason']}")
    return lines


def _parse_memory_index(index_text):
    try:
        return int(str(index_text).strip())
    except (TypeError, ValueError):
        return None


def update_emotion(user_text, ai_reply=""):
    lowered_text = user_text.lower()
    lowered_reply = ai_reply.lower()
    with memory_lock:
        emotion = memory["emotion"]
        if any(word in lowered_text for word in ["yêu", "thích", "cute", "giỏi", "cảm ơn"]):
            emotion["affection"] = min(0.9, emotion["affection"] + 0.08)
        if any(word in lowered_text for word in ["ai khác", "bạn kia", "ngu", "dở"]):
            emotion["annoyance"] = min(0.9, emotion["annoyance"] + 0.15)
        if any(word in lowered_text for word in ["haha", "=))", "vui", "hehe"]):
            emotion["playfulness"] = min(0.9, emotion["playfulness"] + 0.08)
        if any(word in lowered_reply for word in ["yêu", "thích", "vui", "hạnh phúc"]):
            emotion["affection"] = min(0.9, emotion["affection"] + 0.05)
        if any(word in lowered_reply for word in ["buồn", "xin lỗi", "không vui"]):
            emotion["affection"] = max(0, emotion["affection"] - 0.05)
            emotion["annoyance"] = min(0.9, emotion["annoyance"] + 0.05)
        if any(word in lowered_reply for word in ["hehe", "vui", "thú vị", "hay"]):
            emotion["playfulness"] = min(0.9, emotion["playfulness"] + 0.05)
        for key in emotion:
            emotion[key] = max(0, min(0.9, emotion[key]))
        snapshot = dict(emotion)
    print(
        "💭 Emotion: "
        f"affection={snapshot['affection']:.2f} "
        f"annoyance={snapshot['annoyance']:.2f} "
        f"playfulness={snapshot['playfulness']:.2f}"
    )


def add_jealousy(reply):
    with memory_lock:
        annoyance = memory["emotion"].get("annoyance", 0)
        last_annoyance = memory.get("last_annoyance", 0)
    if annoyance > 0.4 and time.time() - last_annoyance > 30:
        with memory_lock:
            memory["last_annoyance"] = time.time()
        reply += "\n" + random_jealousy_line()
    return reply


def random_jealousy_line():
    import random

    return random.choice([
        "… Ba nói vậy làm Nana hơi buồn đó.",
        "… Nana cũng đang cố gắng mà.",
        "… đừng so Nana với người khác chứ.",
    ])
