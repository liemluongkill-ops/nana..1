"""STAGE-9N: public viewer memory-lite.

This layer derives short-lived, per-viewer public context from recent public
turns. It helps Nana remember the viewer's session pattern without writing
durable memory or granting action permission.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import threading
import time
import unicodedata
from typing import Any


PHASE = "STAGE-9N"

QUIET_MARKERS = (
    "phong im",
    "phong nay im",
    "im qua",
    "yen qua",
    "vang qua",
    "lang qua",
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
    "ngao",
)

EMOJI_MARKERS = (
    "emoji",
    "icon",
    "sticker",
    "meme",
)

SERVICE_MARKERS = (
    "tro ly",
    "phuc vu",
    "quay ho tro",
    "assistant",
    "helpdesk",
)

IDENTITY_MARKERS = (
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
class PublicViewerMemoryDirective:
    viewer_name: str
    viewer_profile: str
    current_topic: str
    session_pattern: str
    reason: str
    confidence: float
    instruction: str
    viewer_turns: int = 0
    same_prompt_count: int = 1
    topic_count: int = 0
    last_viewer_message: str = ""
    last_viewer_reply: str = ""
    avoid: tuple[str, ...] = field(default_factory=tuple)
    phase: str = PHASE
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False
    api_call: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence), 2)
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


def _viewer_matches(turn: Any, viewer_name: Any, actor_key: str | None = None) -> bool:
    if actor_key is not None:
        turn_key = _turn_value(turn, "actor_key")
        if not turn_key:
            scope = turn.get("scope") if isinstance(turn, dict) else getattr(turn, "scope", None)
            if isinstance(scope, dict):
                turn_key = (scope.get("identity") or {}).get("actor_key", "")
            elif scope is not None:
                turn_key = scope.identity.actor_key
        return bool(actor_key and turn_key == actor_key)
    viewer = _fold(viewer_name)
    turn_viewer = _fold(_turn_value(turn, "viewer_name"))
    return bool(viewer and turn_viewer and viewer == turn_viewer)


def _has_any(folded: str, markers: tuple[str, ...]) -> bool:
    return any(marker in folded for marker in markers)


def _token_similarity(left: Any, right: Any) -> float:
    left_tokens = set(_fold(left).split())
    right_tokens = set(_fold(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))


def _infer_topic(text: Any, event_type: Any = "") -> str:
    folded = _fold(text)
    event = _fold(event_type)
    if not folded and not event:
        return "none"
    if event in {"emoji_only", "sticker"} or _has_any(folded, EMOJI_MARKERS):
        return "emoji_reaction"
    if _has_any(folded, SERVICE_MARKERS):
        return "service_boundary"
    if _has_any(folded, IDENTITY_MARKERS):
        return "identity_boundary"
    if _has_any(folded, STORY_MARKERS):
        return "story"
    if _has_any(folded, MODEL_MARKERS):
        return "model_talk"
    if _has_any(folded, QUIET_MARKERS):
        return "quiet_room"
    if _has_any(folded, GAME_MARKERS):
        return "game"
    if _has_any(folded, MUSIC_MARKERS):
        return "music"
    if "?" in _clean(text) or folded.endswith("khong") or folded.endswith("sao"):
        return "question"
    if len(folded.split()) <= 3:
        return "short_followup"
    return "open_chat"


def _topic_from_turn(turn: Any) -> str:
    topic = _infer_topic(_turn_value(turn, "topic"), _turn_value(turn, "event_type"))
    if topic not in {"none", "open_chat", "short_followup"}:
        return topic
    return _infer_topic(_turn_value(turn, "message_preview"), _turn_value(turn, "event_type"))


def _same_prompt_count(viewer_turns: list[Any], text: Any) -> int:
    if not _clean(text):
        return 0
    count = 1
    for turn in viewer_turns[-12:]:
        if _token_similarity(text, _turn_value(turn, "message_preview")) >= 0.72:
            count += 1
    return count


def _last_nonempty(values: list[str]) -> str:
    for value in reversed(values):
        if value:
            return value
    return ""


class PublicViewerMemory:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_directive: PublicViewerMemoryDirective | None = None
        self._stats = {
            "built": 0,
            "viewer_pattern": 0,
            "quiet": 0,
            "story": 0,
            "emoji": 0,
            "boundary": 0,
            "model": 0,
            "returning": 0,
            "empty": 0,
        }

    def build(
        self,
        *,
        text: Any = "",
        viewer_name: Any = "viewer",
        recent_turns: list[Any] | tuple[Any, ...] | None = None,
        actor_key: str | None = None,
    ) -> PublicViewerMemoryDirective:
        current_text = _clean(text)
        viewer = _clean(viewer_name or "viewer", limit=80) or "viewer"
        recent = list(recent_turns or [])
        viewer_turns = [turn for turn in recent[-20:] if _viewer_matches(turn, viewer, actor_key)]
        current_topic = _infer_topic(current_text)
        topics = [_topic_from_turn(turn) for turn in viewer_turns]
        topic_counts = {topic: topics.count(topic) for topic in set(topics)}
        topic_count = topic_counts.get(current_topic, 0) + (1 if current_topic != "none" else 0)
        boundary_count = (
            topic_counts.get("service_boundary", 0)
            + topic_counts.get("identity_boundary", 0)
            + (1 if current_topic in {"service_boundary", "identity_boundary"} else 0)
        )
        same_prompt_count = _same_prompt_count(viewer_turns, current_text)
        last_message = _last_nonempty([_turn_value(turn, "message_preview") for turn in viewer_turns])
        last_reply = _last_nonempty([_turn_value(turn, "reply_preview") for turn in viewer_turns])

        profile = "new_public_viewer"
        pattern = "none"
        reason = "no viewer-specific public pattern yet"
        confidence = 0.0
        instruction = "No public viewer memory-lite directive is needed yet."
        avoid: tuple[str, ...] = ("internal_label_leak",)
        stat_key = "empty"

        if not current_text and not viewer_turns:
            pass
        elif current_topic == "quiet_room" and (same_prompt_count >= 3 or topic_count >= 3):
            profile = "quiet_room_poker"
            pattern = "repeated_quiet_room_probe"
            reason = "same viewer is repeatedly poking the quiet-room bit"
            confidence = min(0.92, 0.62 + max(same_prompt_count, topic_count) * 0.06)
            instruction = (
                "This viewer keeps returning to the quiet-room bit. Tease the repetition lightly, "
                "then ask for one concrete new detail instead of replaying the same menu."
            )
            avoid = ("same_room_menu", "same_silence_metaphor", "pretend_viewer_is_new")
            stat_key = "quiet"
        elif current_topic == "story" and topic_count >= 2:
            profile = "story_picker"
            pattern = "story_thread_preference"
            reason = "same viewer keeps choosing or inviting story mode"
            confidence = min(0.88, 0.58 + topic_count * 0.07)
            instruction = (
                "This viewer is leaning into story mode. Continue with one concrete public-safe scene "
                "or callback before opening another menu."
            )
            avoid = ("reset_story_thread", "generic_room_prompt", "backend_story")
            stat_key = "story"
        elif current_topic == "emoji_reaction" and topic_count >= 2:
            profile = "emoji_reactor"
            pattern = "light_reaction_viewer"
            reason = "same viewer often reacts with emoji/sticker-style turns"
            confidence = min(0.84, 0.54 + topic_count * 0.06)
            instruction = (
                "This viewer tends to react lightly. Keep the reply playful and compact; "
                "do not turn every emoji into a full speech."
            )
            avoid = ("overexplain_emoji", "same_ack_line")
            stat_key = "emoji"
        elif current_topic in {"service_boundary", "identity_boundary"} and boundary_count >= 2:
            profile = "boundary_tester"
            pattern = "identity_or_service_boundary_test"
            reason = "same viewer is testing Nana's public identity or role boundary"
            confidence = min(0.90, 0.60 + boundary_count * 0.07)
            instruction = (
                "This viewer has tested Nana's public boundary before. Keep the stance, but vary the line "
                "and avoid sounding harsher just because the test repeats."
            )
            avoid = ("service_assistant_voice", "same_boundary_line", "private_lane_leak")
            stat_key = "boundary"
            topic_count = boundary_count
        elif current_topic == "model_talk" and topic_count >= 2:
            profile = "model_talker"
            pattern = "model_quality_discussion"
            reason = "same viewer is circling model/GPT-hoa talk"
            confidence = min(0.86, 0.56 + topic_count * 0.06)
            instruction = (
                "This viewer is discussing model quality. Be opinionated and specific, but do not become "
                "a benchmark brochure or generic AI disclaimer."
            )
            avoid = ("corporate_model_tone", "benchmark_lecture", "generic_ai_disclaimer")
            stat_key = "model"
        elif viewer_turns:
            profile = "returning_public_viewer"
            pattern = "light_session_familiarity"
            reason = "same viewer has recent public turns in this session"
            confidence = min(0.70, 0.36 + len(viewer_turns) * 0.04)
            instruction = (
                "Let the reply sound like it continues the same public-room session; do not restart with a "
                "first-time greeting."
            )
            avoid = ("first_time_greeting_loop", "internal_label_leak")
            stat_key = "returning"

        directive = PublicViewerMemoryDirective(
            viewer_name=viewer,
            viewer_profile=profile,
            current_topic=current_topic,
            session_pattern=pattern,
            reason=reason,
            confidence=confidence,
            instruction=instruction,
            viewer_turns=len(viewer_turns),
            same_prompt_count=same_prompt_count,
            topic_count=topic_count,
            last_viewer_message=last_message,
            last_viewer_reply=last_reply,
            avoid=avoid,
        )
        self._record(directive, stat_key=stat_key)
        return directive

    def _record(self, directive: PublicViewerMemoryDirective, *, stat_key: str) -> None:
        with self._lock:
            self._last_directive = directive
            self._stats["built"] += 1
            if directive.session_pattern != "none":
                self._stats["viewer_pattern"] += 1
            if stat_key in self._stats:
                self._stats[stat_key] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            last = self._last_directive
            stats = dict(self._stats)
        return {
            "phase": PHASE,
            "mode": "session-viewer-memory-lite",
            "read_only": True,
            "can_act": False,
            "memory_write": False,
            "api_call": False,
            "initialized_at": self._initialized_at,
            "stats": stats,
            "last_directive": last.to_dict() if last else None,
        }


_VIEWER_MEMORY = PublicViewerMemory()


def get_public_viewer_memory() -> PublicViewerMemory:
    return _VIEWER_MEMORY


def build_public_viewer_memory_directive(
    *,
    text: Any = "",
    viewer_name: Any = "viewer",
    recent_turns: list[Any] | tuple[Any, ...] | None = None,
    actor_key: str | None = None,
) -> PublicViewerMemoryDirective:
    return get_public_viewer_memory().build(
        text=text,
        viewer_name=viewer_name,
        recent_turns=recent_turns,
        actor_key=actor_key,
    )


def format_public_viewer_memory_hint(directive: PublicViewerMemoryDirective) -> str:
    avoid_text = ", ".join(directive.avoid) if directive.avoid else "none"
    if directive.session_pattern == "none":
        return (
            f"Public viewer memory-lite ({PHASE}): no strong per-viewer pattern yet. "
            "Use normal public continuity; do not reveal this label."
        )
    lines = [
        f"Public viewer memory-lite ({PHASE}):",
        f"- viewer_profile={directive.viewer_profile}",
        f"- current_topic={directive.current_topic}",
        f"- session_pattern={directive.session_pattern}",
        f"- viewer_turns={directive.viewer_turns}",
        f"- same_prompt_count={directive.same_prompt_count}",
        f"- topic_count={directive.topic_count}",
        f"- instruction={directive.instruction}",
        f"- avoid={avoid_text}",
    ]
    if directive.last_viewer_message:
        lines.append(f"- last_viewer_message=\"{directive.last_viewer_message}\"")
    if directive.last_viewer_reply:
        lines.append(f"- avoid_replaying_recent_reply=\"{directive.last_viewer_reply}\"")
    lines.append("- Scope: public session viewer memory only; no private memory and no long-term write.")
    return "\n".join(lines)


def public_viewer_memory_status_lines() -> list[str]:
    snap = get_public_viewer_memory().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_directive") or {})
    return [
        f"Public Viewer Memory-lite ({PHASE})",
        "  Mode: session-viewer-memory-lite | read_only=True | can_act=False | memory_write=False | api_call=False",
        (
            "  Last: "
            f"viewer={last.get('viewer_name', 'none')} | profile={last.get('viewer_profile', 'none')} | "
            f"topic={last.get('current_topic', 'none')} | pattern={last.get('session_pattern', 'none')} | "
            f"confidence={last.get('confidence', 0.0)}"
        ),
        (
            "  Stats: "
            f"built={stats.get('built', 0)} | patterns={stats.get('viewer_pattern', 0)} | "
            f"quiet={stats.get('quiet', 0)} | story={stats.get('story', 0)} | "
            f"emoji={stats.get('emoji', 0)} | boundary={stats.get('boundary', 0)} | "
            f"model={stats.get('model', 0)}"
        ),
        "  Commands: /public-viewer-memory-status | /public-viewer-memory-preview <viewer>|<text>",
        "  Safety: session public turns only | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def public_viewer_memory_preview_lines(payload: str) -> list[str]:
    raw = _clean(payload)
    if not raw:
        return ["  Usage: /public-viewer-memory-preview <viewer>|<text>"]
    viewer = "viewer"
    text = raw
    if "|" in raw:
        left, right = raw.split("|", 1)
        viewer = _clean(left, limit=80) or "viewer"
        text = _clean(right)
    try:
        from nana.runtime.social_session import get_social_session

        snapshot = get_social_session().snapshot()
        recent_turns = list(snapshot.get("recent_turns") or [])
        if viewer == "viewer":
            last = dict(snapshot.get("last_viewer") or {})
            viewer = last.get("display_name") or viewer
    except Exception:
        recent_turns = []
    directive = build_public_viewer_memory_directive(
        text=text,
        viewer_name=viewer,
        recent_turns=recent_turns,
    )
    return [
        f"Public Viewer Memory Preview ({PHASE})",
        f"  Viewer: {directive.viewer_name}",
        f"  Input: {_clean(text, 160) or 'none'}",
        (
            "  Pattern: "
            f"profile={directive.viewer_profile} | topic={directive.current_topic} | "
            f"session_pattern={directive.session_pattern} | confidence={directive.confidence:.2f}"
        ),
        (
            "  Counts: "
            f"viewer_turns={directive.viewer_turns} | same_prompt={directive.same_prompt_count} | "
            f"topic_count={directive.topic_count}"
        ),
        f"  Instruction: {directive.instruction}",
        f"  Avoid: {', '.join(directive.avoid) if directive.avoid else 'none'}",
        "  Safety: preview only | public session memory only | no write | no output action",
    ]
