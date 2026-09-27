"""STAGE-9AA: voice span planner.

Preview-only metadata for reviewing how a completed Nana reply could be
grouped by narrative role and delivery tone.  This module never inserts
ElevenLabs tags into TTS text and never calls the voice runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import threading
import time
import unicodedata
from typing import Any

from nana.core.format import shorten_line


PHASE = "STAGE-9AA"


@dataclass(frozen=True)
class VoiceSpan:
    span_id: int
    role: str
    tone: str
    energy: str
    pause: str
    tag_hint: str
    text: str
    chars: int
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VoiceSpanPlan:
    original_text: str
    original_chars: int
    spans: tuple[VoiceSpan, ...]
    span_count: int
    phase: str = PHASE
    mode: str = "voice-span-planner"
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False
    api_call: bool = False
    llm_call: bool = False
    tts_call: bool = False
    voice_engine_call: bool = False
    vts_call: bool = False
    obs_call: bool = False
    discord_call: bool = False
    game_input: bool = False
    inserts_tags: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["spans"] = [span.to_dict() for span in self.spans]
        return data


def _clean(value: Any, limit: int = 12000) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _ascii_lower(value: Any) -> str:
    text = str(value or "").replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return stripped.lower()


def _sentence_units(text: str) -> list[str]:
    cleaned = _clean(text)
    if not cleaned:
        return []
    pieces = [piece.strip() for piece in re.split(r"(?<=[.!?…。！？])\s+", cleaned) if piece.strip()]
    return pieces or [cleaned]


def _join(units: list[str]) -> str:
    return " ".join(unit.strip() for unit in units if unit and unit.strip()).strip()


def _starts_with_any(text: str, prefixes: tuple[str, ...]) -> bool:
    return any(text.startswith(prefix) for prefix in prefixes)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _is_intentional_short(text: str, role: str) -> bool:
    normalized = _ascii_lower(text).strip(" .!?…")
    if len(text.strip()) > 28:
        return False
    return role in {"opening", "tease", "ending"} and bool(
        normalized in {"u", "um", "a", "ha ha", "haha"}
        or normalized.startswith(("u ", "um ", "ha ha", "haha"))
    )


def _classify_unit(text: str, *, index: int, total: int) -> dict[str, Any]:
    normalized = _ascii_lower(text)
    first = index == 0
    last = index == max(0, total - 1)

    boundary_hit = (
        ("nana" in normalized and _contains_any(normalized, ("tro ly", "phuc vu", "ho tro", "quay", "bot", "chatbot")))
        or _contains_any(normalized, ("bot discord", "hop tra loi", "cai hop tra loi", "chi la bot"))
    )
    if boundary_hit:
        return {
            "role": "boundary",
            "tone": "firm",
            "energy": "medium",
            "pause": "short",
            "tag_hint": "[firmly]",
            "confidence": 0.86,
            "reason": "identity or service-boundary language",
        }

    tease_hit = _contains_any(
        normalized,
        ("haha", "ha ha", "=))", "ngao", "cuoi", "dua", "treu", "meme", "drama"),
    )
    if tease_hit:
        return {
            "role": "tease" if not last else "ending",
            "tone": "playful",
            "energy": "high",
            "pause": "short" if not last else "medium",
            "tag_hint": "[playfully]",
            "confidence": 0.78,
            "reason": "playful lift or joke marker",
        }

    if first and _starts_with_any(
        normalized.strip(),
        ("u", "um", "duoc", "ne", "ba biet", "nana ke", "ok", "roi"),
    ):
        return {
            "role": "opening",
            "tone": "warm",
            "energy": "medium",
            "pause": "short",
            "tag_hint": "[warmly]",
            "confidence": 0.72,
            "reason": "opening beat",
        }

    reflection_hit = _contains_any(
        normalized,
        (
            "co mot",
            "mot toi",
            "mot dem",
            "that hon",
            "met",
            "buon",
            "lang",
            "yen",
            "nho",
            "cam giac",
            "cmd",
            "log",
            "bug",
            "man hinh",
            "can phong",
        ),
    )
    if reflection_hit:
        return {
            "role": "reflection",
            "tone": "soft" if _contains_any(normalized, ("met", "buon", "lang", "yen")) else "grounded",
            "energy": "low" if _contains_any(normalized, ("met", "buon", "lang", "yen")) else "medium",
            "pause": "medium",
            "tag_hint": "[thoughtfully]",
            "confidence": 0.76,
            "reason": "reflective or personal-story language",
        }

    explanation_hit = _contains_any(
        normalized,
        ("thuc ra", "tuc la", "nghia la", "boi vi", "vi ", "nen ", "neu ", "do la"),
    )
    if explanation_hit:
        return {
            "role": "explanation",
            "tone": "grounded",
            "energy": "medium",
            "pause": "short",
            "tag_hint": "[thoughtfully]",
            "confidence": 0.68,
            "reason": "explanatory connector",
        }

    if last and _contains_any(normalized, ("nha", "nhe", "do", "thoi", "roi")):
        return {
            "role": "ending",
            "tone": "warm",
            "energy": "medium",
            "pause": "long",
            "tag_hint": "[warmly]",
            "confidence": 0.62,
            "reason": "closing cadence",
        }

    return {
        "role": "neutral",
        "tone": "warm",
        "energy": "medium",
        "pause": "short",
        "tag_hint": "none",
        "confidence": 0.50,
        "reason": "no strong tone shift detected",
    }


def _make_span(span_id: int, text: str, meta: dict[str, Any]) -> VoiceSpan:
    cleaned = _clean(text)
    return VoiceSpan(
        span_id=span_id,
        role=str(meta.get("role") or "neutral"),
        tone=str(meta.get("tone") or "warm"),
        energy=str(meta.get("energy") or "medium"),
        pause=str(meta.get("pause") or "short"),
        tag_hint=str(meta.get("tag_hint") or "none"),
        text=cleaned,
        chars=len(cleaned),
        confidence=round(float(meta.get("confidence") or 0.0), 2),
        reason=str(meta.get("reason") or "none"),
    )


def _merge_span(prev: VoiceSpan, text: str, meta: dict[str, Any]) -> VoiceSpan:
    merged_text = _join([prev.text, text])
    confidence = round((prev.confidence + float(meta.get("confidence") or prev.confidence)) / 2.0, 2)
    reason = prev.reason
    new_reason = str(meta.get("reason") or "")
    if new_reason and new_reason != reason:
        reason = f"{reason}; merged {new_reason}"
    return VoiceSpan(
        span_id=prev.span_id,
        role=prev.role,
        tone=prev.tone,
        energy=prev.energy,
        pause=prev.pause,
        tag_hint=prev.tag_hint,
        text=merged_text,
        chars=len(merged_text),
        confidence=confidence,
        reason=reason,
    )


def build_voice_span_plan(text: Any) -> VoiceSpanPlan:
    cleaned = _clean(text)
    if not cleaned:
        plan = VoiceSpanPlan(original_text="", original_chars=0, spans=(), span_count=0)
        get_voice_span_planner().record(plan)
        return plan

    units = _sentence_units(cleaned)
    spans: list[VoiceSpan] = []
    total = len(units)
    for index, unit in enumerate(units):
        meta = _classify_unit(unit, index=index, total=total)
        if not spans:
            spans.append(_make_span(1, unit, meta))
            continue

        previous = spans[-1]
        same_delivery = previous.role == meta["role"] and previous.tone == meta["tone"]
        tiny_nonbeat = len(unit) < 60 and not _is_intentional_short(unit, str(meta["role"]))
        previous_tiny = previous.chars < 70 and not _is_intentional_short(previous.text, previous.role)

        merge_tiny_current = tiny_nonbeat and meta["role"] == "neutral" and previous.role != "boundary"
        merge_tiny_previous = previous_tiny and previous.role == "neutral" and meta["role"] != "boundary"

        if same_delivery or merge_tiny_current or merge_tiny_previous:
            spans[-1] = _merge_span(previous, unit, meta)
        else:
            spans.append(_make_span(len(spans) + 1, unit, meta))

    normalized_spans = tuple(
        VoiceSpan(
            span_id=i,
            role=span.role,
            tone=span.tone,
            energy=span.energy,
            pause=span.pause,
            tag_hint=span.tag_hint,
            text=span.text,
            chars=span.chars,
            confidence=span.confidence,
            reason=span.reason,
        )
        for i, span in enumerate(spans, start=1)
    )
    plan = VoiceSpanPlan(
        original_text=cleaned,
        original_chars=len(cleaned),
        spans=normalized_spans,
        span_count=len(normalized_spans),
    )
    get_voice_span_planner().record(plan)
    return plan


class VoiceSpanPlanner:
    _instance: "VoiceSpanPlanner | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_plan: VoiceSpanPlan | None = None
        self._stats: dict[str, int] = {
            "built": 0,
            "empty": 0,
            "spans": 0,
            "boundary": 0,
            "reflection": 0,
            "tease": 0,
            "tag_hints": 0,
        }

    @classmethod
    def get_instance(cls) -> "VoiceSpanPlanner":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_test(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def record(self, plan: VoiceSpanPlan) -> None:
        with self._lock:
            self._stats["built"] += 1
            if not plan.original_text:
                self._stats["empty"] += 1
            self._stats["spans"] += plan.span_count
            for span in plan.spans:
                if span.role in self._stats:
                    self._stats[span.role] += 1
                if span.tag_hint != "none":
                    self._stats["tag_hints"] += 1
            self._last_plan = plan

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            last = self._last_plan.to_dict() if self._last_plan else {}
            return {
                "phase": PHASE,
                "mode": "voice-span-planner",
                "uptime_seconds": max(0.0, time.time() - self._initialized_at),
                "read_only": True,
                "can_act": False,
                "memory_write": False,
                "api_call": False,
                "llm_call": False,
                "tts_call": False,
                "voice_engine_call": False,
                "inserts_tags": False,
                "last_plan": last,
                "stats": dict(self._stats),
            }


def get_voice_span_planner() -> VoiceSpanPlanner:
    return VoiceSpanPlanner.get_instance()


def voice_span_status_lines() -> list[str]:
    snap = get_voice_span_planner().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_plan") or {})
    spans = list(last.get("spans") or [])
    roles = ",".join(str(span.get("role")) for span in spans) or "none"
    tones = ",".join(str(span.get("tone")) for span in spans) or "none"
    return [
        f"Voice Span Planner ({PHASE})",
        "  Mode: voice-span-planner | read_only=True | can_act=False | tts_call=False | voice_engine_call=False",
        "  Invariant: tag_hint=metadata_only | inserts_tags=False | background=False | mode_a_unchanged=True",
        (
            "  Last: "
            f"spans={last.get('span_count', 0)} | chars={last.get('original_chars', 0)} | "
            f"roles={roles} | tones={tones}"
        ),
        (
            "  Stats: "
            f"built={stats.get('built', 0)} | spans={stats.get('spans', 0)} | "
            f"boundary={stats.get('boundary', 0)} | reflection={stats.get('reflection', 0)} | "
            f"tease={stats.get('tease', 0)} | tag_hints={stats.get('tag_hints', 0)}"
        ),
        "  Commands: /voice-span-status | /voice-span-preview <text>",
        "  Safety: preview/status only | no LLM | no TTS/VoiceEngine/VTS/OBS/Discord/game input | no memory write",
    ]


def voice_span_preview_lines(payload: str) -> list[str]:
    text = _clean(payload)
    if not text:
        return ["  Usage: /voice-span-preview <text>"]
    plan = build_voice_span_plan(text)
    lines = [
        f"Voice Span Preview ({PHASE})",
        (
            "  Plan: "
            f"spans={plan.span_count} | chars={plan.original_chars} | "
            "tag_hint=metadata_only | inserts_tags=False"
        ),
    ]
    for span in plan.spans:
        lines.append(
            "  "
            f"#{span.span_id} role={span.role} | tone={span.tone} | energy={span.energy} | "
            f"pause={span.pause} | tag_hint={span.tag_hint} | chars={span.chars} | "
            f"confidence={span.confidence:.2f} | reason={shorten_line(span.reason, 90)}"
        )
        lines.append(f"     text={shorten_line(span.text, 160)}")
    lines.extend(
        [
            "  Rule: span hints are review metadata only; ElevenLabs tags are not inserted into TTS text.",
            "  Safety: preview only | no LLM | no TTS call | no VoiceEngine call | no output action",
        ]
    )
    return lines
