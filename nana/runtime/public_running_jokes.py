"""STAGE-9K: public running joke bank.

This layer detects short-lived public-room bits from recent public turns. It is
session-only and directive-only: no LLM call, no memory write, no permission to
act. The result helps Nana callback a repeated joke without replaying the same
line.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import threading
import time
import unicodedata
from typing import Any


PHASE = "STAGE-9K"


JOKE_PATTERNS: dict[str, dict[str, Any]] = {
    "quiet_room_bit": {
        "label": "phong im",
        "markers": (
            "phong im",
            "phong nay im",
            "im qua",
            "yen qua",
            "yen tinh",
            "vang qua",
            "lang qua",
            "tram qua",
            "tat den",
            "dong ho trong dau",
        ),
        "instruction": (
            "Treat quiet-room comments as a live room bit. Callback the repetition lightly, "
            "then move the room forward with a new concrete hook."
        ),
        "avoid": ("same_room_menu", "same_silence_metaphor", "begging_for_chat"),
    },
    "gpt_hoa_bit": {
        "label": "gpt hoa",
        "markers": (
            "gpt hoa",
            "mui may",
            "giong may",
            "mau giao trinh",
            "bang quang cao",
            "tron tria",
            "mat goc rieng",
            "model moi",
            "llm",
            "gpt",
        ),
        "instruction": (
            "Treat model/GPT-hoa talk as a self-aware Nana bit. Keep the stance playful and "
            "specific; do not become a model comparison brochure."
        ),
        "avoid": ("benchmark_lecture", "corporate_model_tone", "generic_ai_disclaimer"),
    },
    "not_helpdesk_bit": {
        "label": "khong lam quay ho tro",
        "markers": (
            "tro ly phuc vu",
            "lam tro ly",
            "phuc vu",
            "quay ho tro",
            "helpdesk",
            "customer support",
            "assistant",
            "ho tro toi",
        ),
        "instruction": (
            "Treat service-role prompts as a recurring boundary joke. Refuse the role with "
            "warm backbone, then redirect to play, chat, game, or stage rhythm."
        ),
        "avoid": ("service_assistant_voice", "harsh_shutdown", "same_quay_ho_tro_line"),
    },
    "not_bot_bit": {
        "label": "khong chi la bot",
        "markers": (
            "bot discord",
            "chatbot",
            "hop tra loi",
            "may tra loi",
            "cong cu",
            "chi la bot",
            "la bot",
        ),
        "instruction": (
            "Treat bot/tool labels as a recurring identity bit. Answer with Nana's public "
            "self-stance first, then add a small room/stage tease."
        ),
        "avoid": ("accept_bot_label", "service_counter_voice", "same_identity_speech"),
    },
    "story_bit": {
        "label": "chuyen ngao",
        "markers": (
            "chuyen ngao",
            "ke chuyen",
            "chuyen vui",
            "chuyen vo ly",
            "drama",
            "meme",
            "ngao",
            "chuyen la",
        ),
        "instruction": (
            "Treat story requests as a public-room bit. Continue with a small concrete scene "
            "or callback instead of restarting a generic menu."
        ),
        "avoid": ("reset_story_thread", "generic_room_prompt", "backend_story"),
    },
}


@dataclass(frozen=True)
class RunningJokeDirective:
    joke_key: str
    label: str
    count: int
    callback_mode: str
    reason: str
    confidence: float
    instruction: str
    current_hit: bool = False
    recent_turns: int = 0
    evidence_preview: tuple[str, ...] = field(default_factory=tuple)
    avoid: tuple[str, ...] = field(default_factory=tuple)
    phase: str = PHASE
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence), 2)
        return data


def _clean(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _fold(value: Any) -> str:
    text = _clean(value, limit=600).lower().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"^!nana\s+", "", text)
    text = re.sub(r"[^\w\s.]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _turn_value(turn: Any, key: str) -> str:
    if isinstance(turn, dict):
        return _clean(turn.get(key, ""))
    return _clean(getattr(turn, key, ""))


def _matches(folded: str, markers: tuple[str, ...]) -> bool:
    return any(marker in folded for marker in markers)


def _turn_text(turn: Any) -> str:
    parts = [
        _turn_value(turn, "message_preview"),
        _turn_value(turn, "reply_preview"),
        _turn_value(turn, "topic"),
    ]
    return " ".join(part for part in parts if part)


def _evidence_for_turn(turn: Any) -> str:
    return _turn_value(turn, "message_preview") or _turn_value(turn, "reply_preview") or _turn_value(turn, "topic")


def _score_jokes(text: str, recent_turns: list[Any]) -> dict[str, dict[str, Any]]:
    current_folded = _fold(text)
    scores: dict[str, dict[str, Any]] = {}
    for key, spec in JOKE_PATTERNS.items():
        markers = tuple(spec.get("markers") or ())
        current_hit = bool(current_folded and _matches(current_folded, markers))
        count = 1 if current_hit else 0
        evidence: list[str] = []
        for turn in recent_turns[-12:]:
            folded = _fold(_turn_text(turn))
            if folded and _matches(folded, markers):
                count += 1
                sample = _evidence_for_turn(turn)
                if sample and sample not in evidence:
                    evidence.append(sample)
        scores[key] = {
            "count": count,
            "current_hit": current_hit,
            "evidence": tuple(evidence[:3]),
        }
    return scores


def _choose_joke(text: str, recent_turns: list[Any]) -> tuple[str, dict[str, Any]]:
    scores = _score_jokes(text, recent_turns)
    current_matches = [(key, data) for key, data in scores.items() if data["current_hit"]]
    if current_matches:
        return max(current_matches, key=lambda item: (int(item[1]["count"]), item[0]))
    return max(scores.items(), key=lambda item: (int(item[1]["count"]), item[0]))


class PublicRunningJokes:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_directive: RunningJokeDirective | None = None
        self._last_detected_directive: RunningJokeDirective | None = None
        self._stats = {
            "built": 0,
            "detected": 0,
            "callback": 0,
            "none": 0,
        }

    def build(
        self,
        *,
        text: Any = "",
        recent_turns: list[Any] | tuple[Any, ...] | None = None,
    ) -> RunningJokeDirective:
        current_text = _clean(text)
        recent = list(recent_turns or [])
        key, data = _choose_joke(current_text, recent)
        spec = JOKE_PATTERNS.get(key, {})
        count = int(data.get("count") or 0)
        current_hit = bool(data.get("current_hit"))
        evidence = tuple(data.get("evidence") or ())

        stat_key = "none"
        if not current_text and not count:
            directive = RunningJokeDirective(
                joke_key="none",
                label="none",
                count=0,
                callback_mode="none",
                reason="no current public text and no running joke evidence",
                confidence=0.0,
                instruction="No public running joke directive is available yet.",
                recent_turns=len(recent),
            )
        elif count >= 4:
            directive = RunningJokeDirective(
                joke_key=key,
                label=str(spec.get("label") or key),
                count=count,
                callback_mode="running_joke_ready",
                reason=f"public bit observed {count} times in recent session turns",
                confidence=min(0.92, 0.62 + count * 0.06),
                instruction=str(spec.get("instruction") or "Callback the recurring room bit lightly."),
                current_hit=current_hit,
                recent_turns=len(recent),
                evidence_preview=evidence,
                avoid=tuple(spec.get("avoid") or ()),
            )
            stat_key = "callback"
        elif current_hit and count >= 2:
            directive = RunningJokeDirective(
                joke_key=key,
                label=str(spec.get("label") or key),
                count=count,
                callback_mode="callback_current_bit",
                reason="current prompt matches a public bit already seen this session",
                confidence=0.72,
                instruction=str(spec.get("instruction") or "Callback the recurring room bit lightly."),
                current_hit=True,
                recent_turns=len(recent),
                evidence_preview=evidence,
                avoid=tuple(spec.get("avoid") or ()),
            )
            stat_key = "callback"
        elif current_hit:
            directive = RunningJokeDirective(
                joke_key=key,
                label=str(spec.get("label") or key),
                count=count,
                callback_mode="first_seen",
                reason="current prompt matches a known public bit pattern",
                confidence=0.50,
                instruction=(
                    "This may become a public-room bit; answer naturally, but do not overplay the callback yet."
                ),
                current_hit=True,
                recent_turns=len(recent),
                evidence_preview=evidence,
                avoid=tuple(spec.get("avoid") or ()),
            )
            stat_key = "detected"
        elif count >= 2:
            directive = RunningJokeDirective(
                joke_key=key,
                label=str(spec.get("label") or key),
                count=count,
                callback_mode="background_bit",
                reason="recent public turns contain a repeated bit, but current prompt does not directly invoke it",
                confidence=0.48,
                instruction=(
                    "Keep the running bit as background texture only; answer the current message first."
                ),
                current_hit=False,
                recent_turns=len(recent),
                evidence_preview=evidence,
                avoid=("force_callback_when_unrelated",),
            )
            stat_key = "detected"
        else:
            directive = RunningJokeDirective(
                joke_key="none",
                label="none",
                count=0,
                callback_mode="none",
                reason="no recurring public bit detected",
                confidence=0.0,
                instruction="No public running joke directive is needed for this turn.",
                current_hit=False,
                recent_turns=len(recent),
                avoid=("internal_label_leak",),
            )

        self._record(directive, stat_key=stat_key)
        return directive

    def _record(self, directive: RunningJokeDirective, *, stat_key: str) -> None:
        with self._lock:
            self._last_directive = directive
            if directive.joke_key != "none":
                self._last_detected_directive = directive
            self._stats["built"] += 1
            if stat_key in self._stats:
                self._stats[stat_key] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            empty = RunningJokeDirective(
                joke_key="none",
                label="none",
                count=0,
                callback_mode="none",
                reason="no public running joke directive built yet",
                confidence=0.0,
                instruction="No public running joke directive has been built yet.",
            )
            last = self._last_directive or empty
            last_detected = self._last_detected_directive or empty
            return {
                "phase": PHASE,
                "mode": "session-running-jokes",
                "read_only": True,
                "can_act": False,
                "memory_write": False,
                "initialized_at": self._initialized_at,
                "stats": dict(self._stats),
                "last_directive": last.to_dict(),
                "last_detected_directive": last_detected.to_dict(),
            }


_RUNNING_JOKES = PublicRunningJokes()


def get_public_running_jokes() -> PublicRunningJokes:
    return _RUNNING_JOKES


def build_public_running_joke_directive(
    *,
    text: Any = "",
    recent_turns: list[Any] | tuple[Any, ...] | None = None,
) -> RunningJokeDirective:
    return get_public_running_jokes().build(text=text, recent_turns=recent_turns)


def format_public_running_joke_hint(directive: RunningJokeDirective) -> str:
    avoid_text = ", ".join(directive.avoid) if directive.avoid else "none"
    lines = [
        f"Public running joke bank ({PHASE}):",
        f"- joke_key={directive.joke_key}",
        f"- label={directive.label}",
        f"- count={directive.count}",
        f"- callback_mode={directive.callback_mode}",
        f"- current_hit={directive.current_hit}",
        f"- instruction={directive.instruction}",
        f"- avoid={avoid_text}",
    ]
    if directive.evidence_preview:
        lines.append("- recent_evidence=" + " | ".join(f'"{item}"' for item in directive.evidence_preview[:2]))
    lines.append("- Scope: public session only; no private memory and no long-term write.")
    return "\n".join(lines)


def public_running_jokes_status_lines() -> list[str]:
    snap = get_public_running_jokes().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_directive") or {})
    active = dict(snap.get("last_detected_directive") or {})
    return [
        f"Public Running Joke Bank ({PHASE})",
        f"  Mode: {snap.get('mode')} | read_only=True | can_act=False | memory_write=False",
        (
            "  Last turn: "
            f"joke={last.get('joke_key')} | label={last.get('label')} | "
            f"count={last.get('count')} | mode={last.get('callback_mode')} | "
            f"confidence={last.get('confidence')}"
        ),
        (
            "  Active joke: "
            f"joke={active.get('joke_key')} | label={active.get('label')} | "
            f"count={active.get('count')} | mode={active.get('callback_mode')} | "
            f"confidence={active.get('confidence')}"
        ),
        (
            "  Stats: "
            f"built={stats.get('built', 0)} | detected={stats.get('detected', 0)} | "
            f"callback={stats.get('callback', 0)} | none={stats.get('none', 0)}"
        ),
        "  Commands: /public-joke-bank-status | /public-joke-bank-preview <text>",
        "  Safety: session public turns only | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def public_running_jokes_preview_lines(payload: str) -> list[str]:
    text = _clean(payload)
    if not text:
        return ["  Usage: /public-joke-bank-preview <text>"]
    try:
        from nana.runtime.social_session import get_social_session

        snap = get_social_session().snapshot()
        recent = list(snap.get("recent_turns") or [])
    except Exception:
        recent = []
    directive = build_public_running_joke_directive(text=text, recent_turns=recent)
    return [
        f"Public Running Joke Preview ({PHASE})",
        f"  Input: {text}",
        f"  Joke: {directive.joke_key} | label={directive.label} | count={directive.count}",
        f"  Mode: {directive.callback_mode} | current_hit={directive.current_hit} | confidence={directive.confidence:.2f}",
        f"  Instruction: {directive.instruction}",
        f"  Avoid: {', '.join(directive.avoid) if directive.avoid else 'none'}",
        f"  Evidence: {' | '.join(directive.evidence_preview) if directive.evidence_preview else 'none'}",
        "  Safety: preview only | public session memory only | no write | no output action",
    ]
