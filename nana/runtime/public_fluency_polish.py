"""STAGE-9P: deterministic Vietnamese fluency polish for public replies.

This layer fixes small surface glitches after public safety guards:
- typo-like Vietnamese slips ("ban giay" -> "vài giây")
- awkward role wording ("không làm vai" -> "không nhận vai")
- leaked meta framing ("Nếu ở public, Nana sẽ nói thế này:")
- punctuation spacing from model output ("5. 6" -> "5.6")

It does not generate new content, call an LLM, write memory, or grant actions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import threading
import time
from typing import Any


PHASE = "STAGE-9P"


@dataclass(frozen=True)
class FluencyChange:
    kind: str
    before: str
    after: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PublicFluencyResult:
    text: str
    original: str
    changes: tuple[FluencyChange, ...] = field(default_factory=tuple)
    phase: str = PHASE
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False
    api_call: bool = False

    @property
    def changed(self) -> bool:
        return self.text != self.original

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "changed": self.changed,
            "actions": [change.kind for change in self.changes],
            "changes": [change.to_dict() for change in self.changes],
            "read_only": self.read_only,
            "can_act": self.can_act,
            "memory_write": self.memory_write,
            "api_call": self.api_call,
        }


def _clean(value: Any, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _snapshot(text: str, limit: int = 90) -> str:
    text = _clean(text, limit=limit + 1)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


class PublicFluencyPolisher:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_result: PublicFluencyResult | None = None
        self._stats = {
            "checked": 0,
            "changed": 0,
            "typo": 0,
            "awkward_role": 0,
            "meta_frame": 0,
            "label_prefix": 0,
            "punctuation": 0,
            "voice_tag": 0,
        }

    def polish(self, text: Any, *, viewer_text: Any = "", source: str = "public_reply") -> PublicFluencyResult:
        original = _clean(text)
        if not original:
            result = PublicFluencyResult(text="", original="")
            self._record(result)
            return result

        current = original
        changes: list[FluencyChange] = []

        current = self._strip_meta_frame(current, changes)
        current = self._strip_label_prefix(current, changes)
        current = self._strip_voice_tags(current, changes)
        current = self._fix_phrase_glitches(current, changes)
        current = self._fix_punctuation(current, changes)
        current = re.sub(r"\s{2,}", " ", current).strip()

        result = PublicFluencyResult(text=current, original=original, changes=tuple(changes))
        self._record(result)
        return result

    def _add_change(self, changes: list[FluencyChange], kind: str, before: str, after: str) -> None:
        if before == after:
            return
        changes.append(FluencyChange(kind=kind, before=_snapshot(before), after=_snapshot(after)))

    def _strip_meta_frame(self, text: str, changes: list[FluencyChange]) -> str:
        before = text
        patterns = (
            r"^\s*Nếu\s+ở\s+public\s*,?\s*Nana\s+sẽ\s+nói\s+thế\s+này\s*:\s*[\"“”']?",
            r"^\s*Ở\s+public\s*,?\s*Nana\s+sẽ\s+nói\s*:\s*[\"“”']?",
        )
        updated = before
        matched = False
        for pattern in patterns:
            next_text = re.sub(pattern, "", updated, flags=re.IGNORECASE)
            if next_text != updated:
                matched = True
            updated = next_text
        if matched:
            updated = re.sub(r"[\"“”']\s*$", "", updated).strip()
        self._add_change(changes, "meta_frame", before, updated)
        return updated

    def _strip_label_prefix(self, text: str, changes: list[FluencyChange]) -> str:
        before = text
        updated = re.sub(r"^\s*\*{0,2}\s*Casual\s+response\s*:\s*\*{0,2}\s*", "", before, flags=re.IGNORECASE)
        updated = re.sub(r"^\s*\*{0,2}\s*Public\s+reply\s*:\s*\*{0,2}\s*", "", updated, flags=re.IGNORECASE)
        updated = updated.strip()
        self._add_change(changes, "label_prefix", before, updated)
        return updated

    def _strip_voice_tags(self, text: str, changes: list[FluencyChange]) -> str:
        before = text
        updated = before
        for tag in ("chuckles", "laughs", "softly"):
            updated = re.sub(rf"\[\s*{tag}\s*\]\s*", "", updated, flags=re.IGNORECASE)
        updated = updated.strip()
        self._add_change(changes, "voice_tag", before, updated)
        return updated

    def _fix_phrase_glitches(self, text: str, changes: list[FluencyChange]) -> str:
        replacements: list[tuple[str, str, str]] = [
            (r"\bđứng\s+hình\s+bạn\s+giây\b", "đứng hình vài giây", "typo"),
            (r"\bbạn\s+giây\b", "vài giây", "typo"),
            (r"\bban\s+giay\b", "vài giây", "typo"),
            (r"\bNana\s+là\s+Nana\s+chủ\b", "Nana là Nana chứ", "typo"),
            (r"\bkhông\s+làm\s+vai\b", "không nhận vai", "awkward_role"),
            (r"\blàm\s+vai\s+(trợ\s+lý|phục\s+vụ|quầy\s+hỗ\s+trợ)\b", r"nhận vai \1", "awkward_role"),
            (r"\bkhông\s+làm\s+phòng\s+vui\s+hơn\b", "không làm phòng vui hơn", "typo"),
            (r"\bcửa\s+sổ\s+test\b", "cửa sổ thử nghiệm", "typo"),
        ]
        current = text
        for pattern, replacement, kind in replacements:
            before = current
            current = re.sub(pattern, replacement, current, flags=re.IGNORECASE)
            self._add_change(changes, kind, before, current)
        return current

    def _fix_punctuation(self, text: str, changes: list[FluencyChange]) -> str:
        before = text
        updated = re.sub(r"(\d)\.\s+(\d)", r"\1.\2", before)
        updated = re.sub(r"\s+([,.!?;:])", r"\1", updated)
        updated = re.sub(r"([!?]){2,}", r"\1", updated)
        updated = re.sub(r"\.{3,}", "...", updated)
        updated = re.sub(r"\s{2,}", " ", updated).strip()
        self._add_change(changes, "punctuation", before, updated)
        return updated

    def _record(self, result: PublicFluencyResult) -> None:
        with self._lock:
            self._last_result = result
            self._stats["checked"] += 1
            if result.changed:
                self._stats["changed"] += 1
            for change in result.changes:
                if change.kind in self._stats:
                    self._stats[change.kind] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            last = self._last_result
            stats = dict(self._stats)
        return {
            "phase": PHASE,
            "mode": "deterministic-vietnamese-polish",
            "read_only": True,
            "can_act": False,
            "memory_write": False,
            "api_call": False,
            "initialized_at": self._initialized_at,
            "stats": stats,
            "last_result": last.to_dict() if last else None,
            "last_text": _snapshot(last.text) if last else "",
        }


_PUBLIC_FLUENCY_POLISHER = PublicFluencyPolisher()


def get_public_fluency_polisher() -> PublicFluencyPolisher:
    return _PUBLIC_FLUENCY_POLISHER


def polish_public_vietnamese(text: Any, *, viewer_text: Any = "", source: str = "public_reply") -> PublicFluencyResult:
    return get_public_fluency_polisher().polish(text, viewer_text=viewer_text, source=source)


def public_fluency_status_lines() -> list[str]:
    snap = get_public_fluency_polisher().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_result") or {})
    actions = ", ".join(last.get("actions") or []) if last else "none"
    return [
        f"Public Vietnamese Fluency Polish ({PHASE})",
        "  Mode: deterministic-vietnamese-polish | read_only=True | can_act=False | memory_write=False | api_call=False",
        f"  Last: changed={last.get('changed', False)} | actions={actions} | text={snap.get('last_text') or 'none'}",
        (
            "  Stats: "
            f"checked={stats.get('checked', 0)} | changed={stats.get('changed', 0)} | "
            f"typo={stats.get('typo', 0)} | awkward_role={stats.get('awkward_role', 0)} | "
            f"meta_frame={stats.get('meta_frame', 0)} | punctuation={stats.get('punctuation', 0)}"
        ),
        "  Commands: /public-fluency-status | /public-fluency-preview <text>",
        "  Safety: deterministic only | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def public_fluency_preview_lines(payload: str) -> list[str]:
    text = _clean(payload)
    if not text:
        return ["  Usage: /public-fluency-preview <text>"]
    result = polish_public_vietnamese(text, source="preview")
    changes = result.to_dict().get("changes") or []
    lines = [
        f"Public Vietnamese Fluency Preview ({PHASE})",
        f"  Input: {result.original}",
        f"  Output: {result.text}",
        f"  Changed: {result.changed} | actions={', '.join(result.to_dict().get('actions') or []) or 'none'}",
    ]
    if changes:
        lines.append("  Changes:")
        for change in changes[:6]:
            lines.append(f"    - {change['kind']}: {change['before']} -> {change['after']}")
    lines.append("  Safety: preview only | deterministic | no write | no output action")
    return lines
