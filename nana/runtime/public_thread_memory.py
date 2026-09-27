"""STAGE-9J: public thread memory-lite.

This layer derives a short-lived public-room memory from recent public turns.
It does not persist data, call an LLM, or grant permission to act. The output is
only a content directive for the next public reply.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import threading
import time
import unicodedata
from typing import Any


PHASE = "STAGE-9J"

QUIET_MARKERS = (
    "phong im",
    "phong nay im",
    "im qua",
    "vang qua",
    "yen tinh",
    "tram qua",
)

STORY_MARKERS = (
    "chuyen ngao",
    "ke chuyen",
    "ke nghe",
    "chuyen vui",
    "chuyen vo ly",
    "drama",
    "meme",
)

SERVICE_MARKERS = (
    "tro ly",
    "phuc vu",
    "quay ho tro",
    "assistant",
    "service",
)

IDENTITY_MARKERS = (
    "bot discord",
    "chatbot",
    "hop tra loi",
    "may tra loi",
    "cong cu",
)

MODEL_MARKERS = (
    "gpt",
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

GAME_MARKERS = (
    "game",
    "osu",
    "stardew",
    "minecraft",
    "valorant",
    "cay game",
)

MUSIC_MARKERS = (
    "nhac",
    "bai hat",
    "playlist",
    "cuu mood",
)


@dataclass(frozen=True)
class PublicThreadMemoryDirective:
    active_thread: str
    current_intent: str
    response_memory: str
    reason: str
    confidence: float
    instruction: str
    same_prompt_count: int = 1
    recent_turns: int = 0
    viewer_name: str = "viewer"
    last_similar_reply: str = ""
    last_public_topic: str = ""
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
    text = _clean(value, limit=500).lower().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"^!nana\s+", "", text)
    text = re.sub(r"[^\w\s.]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _has_any(folded: str, markers: tuple[str, ...]) -> bool:
    return any(marker in folded for marker in markers)


def _turn_value(turn: Any, key: str) -> str:
    if isinstance(turn, dict):
        return _clean(turn.get(key, ""))
    return _clean(getattr(turn, key, ""))


def _token_similarity(left: Any, right: Any) -> float:
    left_tokens = set(_fold(left).split())
    right_tokens = set(_fold(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / max(1, min(len(left_tokens), len(right_tokens)))


def _infer_intent(text: Any) -> str:
    folded = _fold(text)
    if not folded:
        return "none"
    if _has_any(folded, SERVICE_MARKERS):
        return "service_boundary"
    if _has_any(folded, IDENTITY_MARKERS):
        return "identity_boundary"
    if _has_any(folded, STORY_MARKERS):
        return "story_thread"
    if _has_any(folded, MODEL_MARKERS):
        return "model_discussion"
    if _has_any(folded, QUIET_MARKERS):
        return "quiet_room"
    if _has_any(folded, GAME_MARKERS):
        return "game_thread"
    if _has_any(folded, MUSIC_MARKERS):
        return "music_thread"
    if len(folded.split()) <= 3:
        return "short_followup"
    return "open_chat"


def _public_topic_from_turn(turn: Any) -> str:
    topic = _fold(_turn_value(turn, "topic"))
    if topic:
        return topic
    return _infer_intent(_turn_value(turn, "message_preview"))


def _last_public_topic(recent_turns: list[Any]) -> str:
    for turn in reversed(recent_turns):
        topic = _public_topic_from_turn(turn)
        if topic and topic not in {"none", "open_chat"}:
            return topic
    return "none"


def _similar_turns(recent_turns: list[Any], text: Any, *, limit: int = 10) -> list[Any]:
    folded = _fold(text)
    if not folded:
        return []
    return [
        turn
        for turn in recent_turns[-limit:]
        if _token_similarity(folded, _turn_value(turn, "message_preview")) >= 0.72
    ]


def _last_reply(turns: list[Any]) -> str:
    for turn in reversed(turns):
        reply = _turn_value(turn, "reply_preview")
        if reply:
            return reply
    return ""


class PublicThreadMemory:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_directive: PublicThreadMemoryDirective | None = None
        self._stats = {
            "built": 0,
            "repeat_detected": 0,
            "thread_continuity": 0,
            "boundary_context": 0,
            "empty": 0,
        }

    def build(
        self,
        *,
        text: Any = "",
        viewer_name: Any = "viewer",
        recent_turns: list[Any] | tuple[Any, ...] | None = None,
    ) -> PublicThreadMemoryDirective:
        current_text = _clean(text)
        recent = list(recent_turns or [])
        intent = _infer_intent(current_text)
        topic = _last_public_topic(recent)
        similar = _similar_turns(recent, current_text)
        same_prompt_count = len(similar) + 1 if current_text else 0
        last_similar_reply = _last_reply(similar)
        viewer = _clean(viewer_name or "viewer", limit=80) or "viewer"

        stat_key: str | None = None
        if not current_text:
            directive = PublicThreadMemoryDirective(
                active_thread=topic,
                current_intent="none",
                response_memory="empty",
                reason="no current public text",
                confidence=0.0,
                instruction="No public thread memory directive is available yet.",
                same_prompt_count=0,
                recent_turns=len(recent),
                viewer_name=viewer,
                last_public_topic=topic,
            )
            stat_key = "empty"
        elif same_prompt_count >= 6:
            directive = PublicThreadMemoryDirective(
                active_thread="repeat_prompt",
                current_intent=intent,
                response_memory="repeat_fatigue",
                reason=f"same public prompt repeated {same_prompt_count} times in recent turns",
                confidence=0.90,
                instruction=(
                    "Acknowledge the repeated pattern as a public-room bit, then stop replaying the same answer; "
                    "invite a different hook or demand one concrete detail."
                ),
                same_prompt_count=same_prompt_count,
                recent_turns=len(recent),
                viewer_name=viewer,
                last_similar_reply=last_similar_reply,
                last_public_topic=topic,
                avoid=("same_answer_again", "same_choice_menu", "pretend_prompt_is_new"),
            )
            stat_key = "repeat_detected"
        elif same_prompt_count >= 3:
            directive = PublicThreadMemoryDirective(
                active_thread="repeat_prompt",
                current_intent=intent,
                response_memory="repeat_awareness",
                reason=f"same public prompt repeated {same_prompt_count} times in recent turns",
                confidence=0.84,
                instruction=(
                    "Notice the repeated prompt lightly and vary the angle; make Nana aware of the pattern "
                    "instead of answering like this is the first time."
                ),
                same_prompt_count=same_prompt_count,
                recent_turns=len(recent),
                viewer_name=viewer,
                last_similar_reply=last_similar_reply,
                last_public_topic=topic,
                avoid=("same_answer_again", "same_room_motif", "same_choice_menu"),
            )
            stat_key = "repeat_detected"
        elif intent in {"story_thread", "game_thread", "music_thread", "short_followup"} and topic != "none":
            directive = PublicThreadMemoryDirective(
                active_thread=topic,
                current_intent=intent,
                response_memory="continue_public_thread",
                reason="viewer appears to continue an existing public thread",
                confidence=0.72,
                instruction=(
                    "Treat this as a continuation of the recent public thread; answer the current message "
                    "before opening a fresh menu."
                ),
                same_prompt_count=max(1, same_prompt_count),
                recent_turns=len(recent),
                viewer_name=viewer,
                last_similar_reply=last_similar_reply,
                last_public_topic=topic,
                avoid=("restart_thread", "generic_room_prompt"),
            )
            stat_key = "thread_continuity"
        elif intent in {"service_boundary", "identity_boundary"}:
            directive = PublicThreadMemoryDirective(
                active_thread=intent,
                current_intent=intent,
                response_memory="boundary_context",
                reason="viewer is testing Nana's public identity or role boundary",
                confidence=0.76,
                instruction=(
                    "Use Nana's public stance, but remember if this is part of a repeated test; "
                    "do not collapse into service wording."
                ),
                same_prompt_count=max(1, same_prompt_count),
                recent_turns=len(recent),
                viewer_name=viewer,
                last_similar_reply=last_similar_reply,
                last_public_topic=topic,
                avoid=("service_assistant_voice", "bot_label_acceptance"),
            )
            stat_key = "boundary_context"
        else:
            directive = PublicThreadMemoryDirective(
                active_thread=topic,
                current_intent=intent,
                response_memory="light_context",
                reason="no strong repeat or continuation pattern detected",
                confidence=0.42,
                instruction="Use the recent public room context lightly; do not overfit or reveal internal labels.",
                same_prompt_count=max(1, same_prompt_count),
                recent_turns=len(recent),
                viewer_name=viewer,
                last_similar_reply=last_similar_reply,
                last_public_topic=topic,
                avoid=("internal_label_leak",),
            )

        self._record(directive, stat_key=stat_key)
        return directive

    def _record(self, directive: PublicThreadMemoryDirective, *, stat_key: str | None = None) -> None:
        with self._lock:
            self._last_directive = directive
            self._stats["built"] += 1
            if stat_key and stat_key in self._stats:
                self._stats[stat_key] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            last = self._last_directive or PublicThreadMemoryDirective(
                active_thread="none",
                current_intent="none",
                response_memory="none",
                reason="no public thread memory built yet",
                confidence=0.0,
                instruction="No public thread memory directive has been built yet.",
            )
            return {
                "phase": PHASE,
                "mode": "session-memory-lite",
                "read_only": True,
                "can_act": False,
                "memory_write": False,
                "initialized_at": self._initialized_at,
                "stats": dict(self._stats),
                "last_directive": last.to_dict(),
            }


_THREAD_MEMORY = PublicThreadMemory()


def get_public_thread_memory() -> PublicThreadMemory:
    return _THREAD_MEMORY


def build_public_thread_memory_directive(
    *,
    text: Any = "",
    viewer_name: Any = "viewer",
    recent_turns: list[Any] | tuple[Any, ...] | None = None,
) -> PublicThreadMemoryDirective:
    return get_public_thread_memory().build(
        text=text,
        viewer_name=viewer_name,
        recent_turns=recent_turns,
    )


def format_public_thread_memory_hint(directive: PublicThreadMemoryDirective) -> str:
    avoid_text = ", ".join(directive.avoid) if directive.avoid else "none"
    lines = [
        f"Public thread memory-lite ({PHASE}):",
        f"- active_thread={directive.active_thread}",
        f"- current_intent={directive.current_intent}",
        f"- response_memory={directive.response_memory}",
        f"- same_prompt_count={directive.same_prompt_count}",
        f"- recent_turns={directive.recent_turns}",
        f"- last_public_topic={directive.last_public_topic}",
        f"- instruction={directive.instruction}",
        f"- avoid={avoid_text}",
    ]
    if directive.last_similar_reply:
        lines.append(f"- avoid_repeating_recent_reply=\"{directive.last_similar_reply}\"")
    lines.append("- Scope: public session memory only; no private memory and no long-term write.")
    return "\n".join(lines)


def public_thread_memory_status_lines() -> list[str]:
    snap = get_public_thread_memory().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_directive") or {})
    return [
        f"Public Thread Memory-lite ({PHASE})",
        f"  Mode: {snap.get('mode')} | read_only=True | can_act=False | memory_write=False",
        (
            "  Last: "
            f"thread={last.get('active_thread')} | intent={last.get('current_intent')} | "
            f"memory={last.get('response_memory')} | repeat={last.get('same_prompt_count')} | "
            f"confidence={last.get('confidence')}"
        ),
        (
            "  Stats: "
            f"built={stats.get('built', 0)} | repeat={stats.get('repeat_detected', 0)} | "
            f"continuity={stats.get('thread_continuity', 0)} | boundary={stats.get('boundary_context', 0)} | "
            f"empty={stats.get('empty', 0)}"
        ),
        "  Commands: /public-thread-memory-status | /public-thread-memory-preview <text>",
        "  Safety: session public turns only | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def public_thread_memory_preview_lines(payload: str) -> list[str]:
    text = _clean(payload)
    if not text:
        return ["  Usage: /public-thread-memory-preview <text>"]
    try:
        from nana.runtime.social_session import get_social_session

        snap = get_social_session().snapshot()
        recent = list(snap.get("recent_turns") or [])
        last = dict(snap.get("last_viewer") or {})
        viewer = last.get("display_name") or "viewer"
    except Exception:
        recent = []
        viewer = "viewer"
    directive = build_public_thread_memory_directive(
        text=text,
        viewer_name=viewer,
        recent_turns=recent,
    )
    return [
        f"Public Thread Memory Preview ({PHASE})",
        f"  Input: {text}",
        f"  Thread: {directive.active_thread} | intent={directive.current_intent} | memory={directive.response_memory}",
        f"  Repeat: {directive.same_prompt_count} | recent_turns={directive.recent_turns} | confidence={directive.confidence:.2f}",
        f"  Instruction: {directive.instruction}",
        f"  Avoid: {', '.join(directive.avoid) if directive.avoid else 'none'}",
        f"  Last similar reply: {directive.last_similar_reply or 'none'}",
        "  Safety: preview only | public session memory only | no write | no output action",
    ]
