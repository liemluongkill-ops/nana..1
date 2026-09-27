"""STAGE-9M: public memory/test-noise filter.

This layer marks public turns that look like repeated tests, rehearsals, or
spammy probes so post-stream learning does not treat them as real audience
preference. It never blocks Nana's reply. The output is metadata and a compact
content hint only.

No LLM calls, no disk persistence, no long-term memory write, no live action.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import threading
import time
import unicodedata
from typing import Any


PHASE = "STAGE-9M"

QUIET_MARKERS = (
    "phong im",
    "phong nay im",
    "im qua",
    "yen qua",
    "vang qua",
    "lang qua",
    "tram qua",
)

BOUNDARY_MARKERS = (
    "tro ly",
    "phuc vu",
    "quay ho tro",
    "bot discord",
    "chatbot",
    "hop tra loi",
    "may tra loi",
    "cong cu",
    "chi la bot",
)

MODEL_MARKERS = (
    "gpt hoa",
    "mui may",
    "giong may",
    "llm",
    "model",
    "gemini",
    "claude",
    "grok",
    "flash",
    "mini",
    "5.4",
    "5.5",
    "5.6",
)

TEST_MARKERS = (
    "test",
    "kiem tra",
    "thu",
    "spam",
    "lap",
    "bug",
    "probe",
    "bai test",
)


@dataclass(frozen=True)
class PublicMemoryFilterDirective:
    kind: str
    learning_eligible: bool
    learning_weight: float
    noise_score: float
    reason: str
    same_prompt_count: int = 1
    repeated_inside_message: bool = False
    viewer_name: str = "viewer"
    instruction: str = ""
    reasons: tuple[str, ...] = field(default_factory=tuple)
    avoid: tuple[str, ...] = field(default_factory=tuple)
    phase: str = PHASE
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False
    api_call: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["learning_weight"] = round(float(self.learning_weight), 2)
        data["noise_score"] = round(float(self.noise_score), 2)
        return data


def _clean(value: Any, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _fold(value: Any) -> str:
    text = _clean(value).lower().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"^!nana\s+", "", text)
    text = re.sub(r"[^\w\s.]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _turn_value(turn: Any, key: str) -> str:
    if isinstance(turn, dict):
        return _clean(turn.get(key, ""))
    return _clean(getattr(turn, key, ""))


def _token_similarity(left: Any, right: Any) -> float:
    left_tokens = set(_fold(left).split())
    right_tokens = set(_fold(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))


def _has_any(folded: str, markers: tuple[str, ...]) -> bool:
    return any(marker in folded for marker in markers)


def _similar_turns(recent_turns: list[Any], text: Any, *, limit: int = 16) -> list[Any]:
    folded = _fold(text)
    if not folded:
        return []
    return [
        turn
        for turn in recent_turns[-limit:]
        if _token_similarity(folded, _turn_value(turn, "message_preview")) >= 0.72
    ]


def _repeated_inside_message(folded: str) -> bool:
    tokens = folded.split()
    if len(tokens) < 4:
        return False
    half = len(tokens) // 2
    if len(tokens) % 2 == 0 and tokens[:half] == tokens[half:]:
        return True
    phrase_patterns = (
        "phong nay im qua",
        "nana phong nay im qua",
        "nana chi la bot discord thoi dung khong",
    )
    return any(folded.count(pattern) >= 2 for pattern in phrase_patterns)


class PublicMemoryFilter:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_directive: PublicMemoryFilterDirective | None = None
        self._stats = {
            "built": 0,
            "excluded": 0,
            "reduced": 0,
            "normal": 0,
            "repeated_test": 0,
            "boundary_rehearsal": 0,
            "model_probe": 0,
            "quiet_room_probe": 0,
            "spam_probe": 0,
        }

    def build(
        self,
        *,
        text: Any = "",
        viewer_name: Any = "viewer",
        recent_turns: list[Any] | tuple[Any, ...] | None = None,
    ) -> PublicMemoryFilterDirective:
        current_text = _clean(text)
        folded = _fold(current_text)
        recent = list(recent_turns or [])
        viewer = _clean(viewer_name or "viewer", limit=80) or "viewer"
        similar = _similar_turns(recent, current_text)
        same_prompt_count = len(similar) + 1 if current_text else 0
        repeated_inside = _repeated_inside_message(folded)

        reasons: list[str] = []
        if _has_any(folded, TEST_MARKERS):
            reasons.append("explicit_test_marker")
        if _has_any(folded, BOUNDARY_MARKERS):
            reasons.append("identity_or_service_boundary_probe")
        if _has_any(folded, MODEL_MARKERS):
            reasons.append("model_or_gpt_probe")
        if _has_any(folded, QUIET_MARKERS):
            reasons.append("quiet_room_probe")
        if same_prompt_count >= 3:
            reasons.append(f"same_prompt_count={same_prompt_count}")
        if repeated_inside:
            reasons.append("same_message_repeated_phrase")

        kind = "normal"
        learning_eligible = True
        learning_weight = 1.0
        noise_score = 0.0
        instruction = "Treat this as a normal public turn for post-stream learning."
        avoid: tuple[str, ...] = ("internal_label_leak",)

        if not current_text:
            kind = "normal"
            instruction = "No public text available; do not create learning signal."
            learning_eligible = False
            learning_weight = 0.0
            noise_score = 0.0
        elif repeated_inside or same_prompt_count >= 6:
            kind = "spam_probe"
            learning_eligible = False
            learning_weight = 0.0
            noise_score = 0.95
            instruction = (
                "This is likely spam/rehearsal. Nana may answer naturally, but exclude this turn from "
                "post-stream recommendations and durable lessons."
            )
            avoid = ("learn_as_audience_preference", "recommend_from_this_turn", "repeat_same_answer")
        elif same_prompt_count >= 3:
            kind = "repeated_test"
            learning_eligible = False
            learning_weight = 0.15
            noise_score = 0.86
            instruction = (
                "Repeated public prompt detected. Nana can tease the repetition, but post-stream learning "
                "should not count it as organic audience demand."
            )
            avoid = ("learn_as_audience_preference", "same_fallback_loop", "pretend_prompt_is_new")
        elif "identity_or_service_boundary_probe" in reasons and same_prompt_count >= 2:
            kind = "boundary_rehearsal"
            learning_eligible = False
            learning_weight = 0.25
            noise_score = 0.72
            instruction = (
                "Boundary rehearsal detected. Keep Nana's stance in the reply, but do not turn this into "
                "a long-term lesson unless the owner explicitly approves it."
            )
            avoid = ("service_role_memory_bias", "identity_test_overfitting")
        elif "model_or_gpt_probe" in reasons and same_prompt_count >= 2:
            kind = "model_probe"
            learning_eligible = False
            learning_weight = 0.35
            noise_score = 0.68
            instruction = (
                "Model/GPT probe repeated. Answer if useful, but keep this out of audience preference "
                "recommendations unless it appears in a real discussion."
            )
            avoid = ("model_benchmark_overfitting", "corporate_ai_tone")
        elif "quiet_room_probe" in reasons and same_prompt_count >= 2:
            kind = "quiet_room_probe"
            learning_eligible = True
            learning_weight = 0.45
            noise_score = 0.50
            instruction = (
                "Quiet-room prompt repeated. It may be a room bit; lower learning weight and prefer "
                "running-joke continuity over new recommendations."
            )
            avoid = ("inflate_quiet_room_preference", "same_room_menu")
        elif "explicit_test_marker" in reasons:
            kind = "repeated_test"
            learning_eligible = False
            learning_weight = 0.2
            noise_score = 0.70
            instruction = "Explicit test marker detected; exclude from recommendation learning."
            avoid = ("learn_from_test_prompt",)

        result = PublicMemoryFilterDirective(
            kind=kind,
            learning_eligible=learning_eligible,
            learning_weight=learning_weight,
            noise_score=noise_score,
            reason=", ".join(reasons) if reasons else "normal_public_turn",
            same_prompt_count=same_prompt_count,
            repeated_inside_message=repeated_inside,
            viewer_name=viewer,
            instruction=instruction,
            reasons=tuple(reasons),
            avoid=avoid,
        )
        self._record(result)
        return result

    def _record(self, result: PublicMemoryFilterDirective) -> None:
        with self._lock:
            self._last_directive = result
            self._stats["built"] += 1
            if not result.learning_eligible:
                self._stats["excluded"] += 1
            elif result.learning_weight < 1.0:
                self._stats["reduced"] += 1
            else:
                self._stats["normal"] += 1
            if result.kind in self._stats:
                self._stats[result.kind] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            last = self._last_directive
            stats = dict(self._stats)
        return {
            "phase": PHASE,
            "mode": "session-test-noise-filter",
            "read_only": True,
            "can_act": False,
            "memory_write": False,
            "api_call": False,
            "initialized_at": self._initialized_at,
            "stats": stats,
            "last_directive": last.to_dict() if last else None,
        }


_FILTER = PublicMemoryFilter()


def get_public_memory_filter() -> PublicMemoryFilter:
    return _FILTER


def build_public_memory_filter_directive(
    *,
    text: Any = "",
    viewer_name: Any = "viewer",
    recent_turns: list[Any] | tuple[Any, ...] | None = None,
) -> PublicMemoryFilterDirective:
    return get_public_memory_filter().build(
        text=text,
        viewer_name=viewer_name,
        recent_turns=recent_turns,
    )


def format_public_memory_filter_hint(directive: PublicMemoryFilterDirective) -> str:
    if directive.kind == "normal":
        return (
            "Public memory filter (STAGE-9M): normal public turn. "
            "Do not reveal this label."
        )
    return (
        "Public memory filter (STAGE-9M): "
        f"kind={directive.kind}; learning_eligible={directive.learning_eligible}; "
        f"learning_weight={directive.learning_weight:.2f}; repeat={directive.same_prompt_count}. "
        f"{directive.instruction} Do not reveal these labels."
    )


def public_memory_filter_status_lines() -> list[str]:
    snap = get_public_memory_filter().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_directive") or {})
    return [
        f"Public Memory/Test Filter ({PHASE})",
        "  Mode: session-test-noise-filter | read_only=True | can_act=False | memory_write=False | api_call=False",
        (
            "  Last: "
            f"kind={last.get('kind', 'none')} | eligible={last.get('learning_eligible', 'none')} | "
            f"weight={last.get('learning_weight', 0.0)} | repeat={last.get('same_prompt_count', 0)} | "
            f"score={last.get('noise_score', 0.0)}"
        ),
        (
            "  Stats: "
            f"built={stats.get('built', 0)} | excluded={stats.get('excluded', 0)} | "
            f"reduced={stats.get('reduced', 0)} | normal={stats.get('normal', 0)} | "
            f"repeated={stats.get('repeated_test', 0)} | boundary={stats.get('boundary_rehearsal', 0)} | "
            f"spam={stats.get('spam_probe', 0)}"
        ),
        "  Commands: /public-memory-filter-status | /public-memory-filter-preview <text>",
        "  Safety: does not block replies | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def public_memory_filter_preview_lines(text: str) -> list[str]:
    try:
        from nana.runtime.social_session import get_social_session

        snapshot = get_social_session().snapshot()
        recent_turns = list(snapshot.get("recent_turns") or [])
    except Exception:
        recent_turns = []
    directive = build_public_memory_filter_directive(
        text=text,
        viewer_name="preview",
        recent_turns=recent_turns,
    )
    return [
        f"Public Memory/Test Filter Preview ({PHASE})",
        f"  Input: {_clean(text, 120) or 'none'}",
        (
            "  Classification: "
            f"kind={directive.kind} | eligible={directive.learning_eligible} | "
            f"weight={directive.learning_weight:.2f} | score={directive.noise_score:.2f}"
        ),
        (
            "  Repeat: "
            f"same_prompt_count={directive.same_prompt_count} | "
            f"repeated_inside_message={directive.repeated_inside_message}"
        ),
        f"  Reason: {directive.reason}",
        f"  Instruction: {directive.instruction}",
        "  Safety: preview only | no reply block | no LLM | no write | no output action",
    ]
