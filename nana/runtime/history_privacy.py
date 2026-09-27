"""Shared secret masking at conversational history boundaries.

This module has no runtime/config imports and no import-time I/O. Masking is
deliberately limited to recognizable credentials; it is not a general PII
classifier. Durable facts are not rewritten by this history-only migration.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata


_SECRET_LABEL = (
    r"(?:openai_api_key|api[_ -]?key|apikey|authorization|credential|"
    r"password|passwd|pwd|passphrase|private[_ -]?key|secret|"
    r"access[_ -]?token|auth[_ -]?token|token|otp|"
    r"mật khẩu|mat khau|mã xác thực|ma xac thuc)"
)
_PRIVATE_KEY_RE = re.compile(
    r"(?is)-----BEGIN\s+(?:[A-Z0-9]+\s+)?PRIVATE\s+KEY-----.*?"
    r"(?:-----END\s+(?:[A-Z0-9]+\s+)?PRIVATE\s+KEY-----|$)"
)
_SECRET_LABEL_RE = re.compile(r"(?i)(?<!redacted )\b" + _SECRET_LABEL + r"\b")
_SECRET_HEAD_RE = re.compile(
    r"(?i)(?<!redacted )\b(?P<label>" + _SECRET_LABEL + r")\b"
    r"(?P<descriptor>[^.!?\r\n;:=]{0,90}?)(?P<connector>:|=|\bis\b|\blà\b|\bla\b)\s*"
)
_SECRET_TOKEN_RE = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9_]{8,}|"
    r"github_pat_[a-z0-9_]{8,}|glpat-[a-z0-9_-]{8,}|"
    r"xox[baprs]-[a-z0-9-]{8,}|AKIA[A-Z0-9]{12,})\b"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\b")


def _fold(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFD", value.lower())
                   if unicodedata.category(char) != "Mn").replace("đ", "d")


def _is_definition(head, text: str, tail: str) -> bool:
    descriptor = _fold(head.group("descriptor")).strip(" \t\"'")
    prefix = _fold(text[max(0, head.start() - 40):head.start()])
    # These are properties of a credential/token, not credential values.
    if re.match(r"^(?:manager|management|budget|limit|count|window|usage|cost)\b", descriptor):
        return True
    if _fold(head.group("label")) == "token" and re.search(
        r"(?:ngan sach|gioi han|so luong|budget|count|limit)\s*$", prefix
    ):
        return True
    if head.group("connector") in {":", "="}:
        return False
    value = _fold(tail).strip()
    if re.match(r"^(?:gi|what)[?!.\s]*$", value):
        return True
    # Bare labels can introduce an explanation. Ownership/descriptors instead
    # make this an actual supplied value, even when its words sound ordinary.
    if descriptor or re.search(r"\b(?:my|your|our|his|her|its)\s*$", prefix):
        return False
    return bool(re.match(
        r"^(?:(?:mot|a|an|the)\s+)?(?:don vi\b|unit\b|chuoi ky tu\b|"
        r"string of characters\b|credential\b|software\b|"
        r"used (?:for|to)\b|dung de\b|duoc dung de\b)", value
    ))


def _credential_spans(value: str) -> list[tuple[int, int]]:
    spans = []
    covered_until = 0
    for label in _SECRET_LABEL_RE.finditer(value):
        if label.start() < covered_until:
            continue
        head = _SECRET_HEAD_RE.match(value, label.start())
        if head is None:
            continue
        tail = value[head.end():]
        if not tail or tail.startswith("[redacted") or _is_definition(head, value, tail):
            continue
        if tail[0] in "\"'":
            quote = tail[0]
            quoted = re.match(re.escape(quote) + r"(?:\\.|[^" + re.escape(quote) + r"])*" + re.escape(quote), tail)
            # An unfinished quoted credential remains private to end of input.
            length = quoted.end() if quoted else len(tail)
        elif re.match(r"(?i)(?:bearer|basic)\s+", tail):
            credential = re.match(r"(?i)(?:bearer|basic)\s+[^\s;]+", tail)
            length = credential.end() if credential else len(tail.splitlines()[0])
        elif (head.group("connector") not in {":", "="}
              and _fold(head.group("label")) in {"password", "passwd", "pwd", "passphrase", "mat khau"}):
            # Unquoted natural password phrases may contain spaces. Keep the
            # whole phrase private, but preserve the following sentence. Dots
            # and exclamation marks inside one credential are never split.
            boundary = re.search(r"[.!?](?=\s|$)|[\r\n;]", tail)
            length = boundary.end() if boundary else len(tail)
        else:
            credential = re.match(r"[^\s;]+", tail)
            if credential is None:
                continue
            length = credential.end()
        end = head.end() + length
        spans.append((head.start(), end))
        covered_until = end
    return spans


def _redact_fragment(value: str) -> str:
    for start, end in reversed(_credential_spans(value)):
        value = value[:start] + "[redacted secret]" + value[end:]
    value = _BEARER_RE.sub("Bearer [redacted secret]", value)
    value = _JWT_RE.sub("[redacted jwt]", value)
    return _SECRET_TOKEN_RE.sub("[redacted secret]", value)


def redact_history_text(text: str) -> str:
    """Mask recognizable credentials without truncating benign conversation.

    Process complete multiline text before line selection, so PEM bodies cannot
    reappear when callers keep only USER/NANA lines. Credential parsing ignores
    existing markers as labels/values and repeated boundaries are idempotent.
    """
    # Do not split around existing markers: one inside a PEM or quoted password
    # must not expose the following portion of that credential.
    value = _PRIVATE_KEY_RE.sub("[redacted private key]", str(text or ""))
    return _redact_fragment(value)


def contains_history_secret(text: str) -> bool:
    value = str(text or "")
    return redact_history_text(value) != value


def _scrub_values(value):
    if isinstance(value, str):
        return redact_history_text(value)
    if isinstance(value, list):
        if value and all(isinstance(item, str) for item in value):
            # Legacy snapshots sometimes kept pasted PEM/password lines in
            # adjacent entries. Detect their full spans before masking entries,
            # preserving list shape and all text outside the secret span.
            joined = "\n".join(value)
            spans = [(match.start(), match.end()) for match in _PRIVATE_KEY_RE.finditer(joined)]
            spans.extend((start, end) for start, end in _credential_spans(joined)
                         if "\n" in joined[start:end])
            result = []
            offset = 0
            for item in value:
                end = offset + len(item)
                ranges = sorted((max(start, offset) - offset, min(stop, end) - offset)
                                for start, stop in spans if start < end and stop > offset)
                merged = []
                for start, stop in ranges:
                    if merged and start <= merged[-1][1]:
                        merged[-1] = (merged[-1][0], max(stop, merged[-1][1]))
                    else:
                        merged.append((start, stop))
                safe = item
                for start, stop in reversed(merged):
                    safe = safe[:start] + "[redacted secret]" + safe[stop:]
                result.append(redact_history_text(safe))
                offset = end + 1
            return result
        return [_scrub_values(item) for item in value]
    if isinstance(value, dict):
        return {key: _scrub_values(item) for key, item in value.items()}
    return copy.deepcopy(value)


def sanitize_history_state(snapshot: dict) -> dict:
    """Copy a snapshot and mask chat-only fields, preserving IDs and facts."""
    if not isinstance(snapshot, dict):
        raise TypeError("history snapshot must be a dict")
    result = copy.deepcopy(snapshot)
    for key in ("short_term", "chat_log", "session_summary"):
        if key in result:
            result[key] = _scrub_values(result[key])
    checkpoint = result.get("session_checkpoint")
    if isinstance(checkpoint, dict):
        for collection in ("pending_turns", "anchors"):
            items = checkpoint.get(collection)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                for key in ("user_text", "nana_text", "text"):
                    if isinstance(item.get(key), str):
                        item[key] = redact_history_text(item[key])
    return result


def _atomic_write_text(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".privacy.tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sanitize_history_files(memory_path, chat_history_path, *, apply: bool = False) -> dict:
    """Explicit atomic migration; caller must quiesce concurrent runtime writes.

    Validate and stage every existing file before writing any. Replace only
    files that contain recognizable chat secrets; preserve snapshot revisions,
    durable data and unrelated keys. No raw backup is produced. Reports contain
    hashes and counts, never history content. Per-file replacement is atomic;
    an OS failure between files is recoverable by rerunning this idempotent job.
    """
    from .memory_persistence import AtomicMemoryWriter

    primary = Path(memory_path).resolve()
    archive = Path(chat_history_path).resolve()
    previous = primary.with_name(primary.name + ".previous")
    if len({primary, previous, archive}) != 3:
        raise ValueError("memory and history migration paths must be distinct")
    staged = []
    results = []
    for path, kind in ((primary, "current_snapshot"), (previous, "previous_snapshot"), (archive, "chat_history")):
        if not path.exists():
            continue
        if not path.is_file():
            raise ValueError("history migration target must be a file")
        before_bytes = path.read_bytes()
        before = before_bytes.decode("utf-8")
        if kind == "chat_history":
            after = redact_history_text(before)
        else:
            data = AtomicMemoryWriter._validate(json.loads(before))
            sanitized = sanitize_history_state(data)
            after = json.dumps(sanitized, ensure_ascii=False, indent=2, allow_nan=False) if sanitized != data else before
        changed = after != before
        results.append({
            "kind": kind,
            "changed": changed,
            "sha256_before": hashlib.sha256(before_bytes).hexdigest(),
            "sha256_after": hashlib.sha256(after.encode("utf-8")).hexdigest(),
        })
        if changed:
            staged.append((path, after, before_bytes))
    if apply:
        # Detect edits made while staging instead of overwriting fresh data.
        for path, _, before_bytes in staged:
            if path.read_bytes() != before_bytes:
                raise RuntimeError("history migration target changed; retry while runtime is stopped")
        for path, after, _ in staged:
            _atomic_write_text(path, after)
    return {"applied": bool(apply), "changed_files": len(staged), "files": results}
