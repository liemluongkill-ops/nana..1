"""STAGE-9O: public scene/topic builder.

This layer turns the current public turn into a compact scene card for Nana's
next reply. It is deterministic and directive-only: no LLM call, no memory
write, and no permission to send or act.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import threading
import time
import unicodedata
from typing import Any


PHASE = "STAGE-9O"

QUIET_MARKERS = (
    "phong im",
    "phong nay im",
    "im qua",
    "yen qua",
    "vang qua",
    "lang qua",
    "tram qua",
    "khong ai noi",
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
    "nghe gi",
)


@dataclass(frozen=True)
class PublicSceneDirective:
    scene_type: str
    scene_move: str
    topic: str
    reason: str
    confidence: float
    beat: str
    hook: str
    max_sentences: int = 2
    recent_motif: str = "none"
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


def _has_any(folded: str, markers: tuple[str, ...]) -> bool:
    return any(marker in folded for marker in markers)


def _turn_value(turn: Any, key: str) -> str:
    if isinstance(turn, dict):
        return _clean(turn.get(key, ""))
    return _clean(getattr(turn, key, ""))


def _infer_topic(text: Any, event_type: Any = "") -> str:
    folded = _fold(text)
    event = _fold(event_type)
    if not folded and not event:
        return "none"
    if event in {"emoji_only", "sticker"}:
        return "emoji"
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
    if len(folded.split()) <= 3 and folded:
        return "short_followup"
    return "open_chat"


def _recent_reply_text(recent_turns: list[Any]) -> str:
    for turn in reversed(recent_turns[-6:]):
        reply = _turn_value(turn, "reply_preview")
        if reply:
            return reply
    return ""


def _recent_motif(recent_turns: list[Any]) -> str:
    text = _fold(" ".join(_turn_value(turn, "reply_preview") for turn in recent_turns[-6:]))
    if not text:
        return "none"
    if "den" in text or "tat den" in text:
        return "light_room"
    if "nhip" in text or "trong" in text or "dong ho" in text:
        return "rhythm_clock"
    if "moi" in text or "chon nhanh" in text or "game" in text and "nhac" in text:
        return "choice_menu"
    if "quay ho tro" in text or "tro ly" in text:
        return "helpdesk_boundary"
    if "mui may" in text or "gpt" in text:
        return "machine_smell"
    return "none"


def _selected_story_from_recent(recent_turns: list[Any]) -> bool:
    recent_reply = _fold(_recent_reply_text(recent_turns))
    return bool(
        recent_reply
        and ("game" in recent_reply or "nhac" in recent_reply)
        and ("chuyen" in recent_reply or "ngao" in recent_reply)
    )


class PublicSceneBuilder:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_directive: PublicSceneDirective | None = None
        self._stats = {
            "built": 0,
            "quiet_scene": 0,
            "story_scene": 0,
            "boundary_scene": 0,
            "model_scene": 0,
            "repeat_scene": 0,
            "emoji_scene": 0,
            "open_scene": 0,
        }

    def build(
        self,
        *,
        text: Any = "",
        event_type: str = "text",
        room_vibe: str = "quiet_room",
        viewer_name: Any = "viewer",
        recent_turns: list[Any] | tuple[Any, ...] | None = None,
        repeat_count: int = 0,
    ) -> PublicSceneDirective:
        current_text = _clean(text)
        recent = list(recent_turns or [])
        topic = _infer_topic(current_text, event_type)
        motif = _recent_motif(recent)
        viewer = _clean(viewer_name, limit=80) or "viewer"
        stat_key = "open_scene"

        if topic == "quiet_room" and repeat_count >= 4:
            directive = PublicSceneDirective(
                scene_type="repeat_pivot",
                scene_move="name_pattern_then_switch",
                topic=topic,
                reason=f"quiet-room prompt repeated {repeat_count} times",
                confidence=0.88,
                beat="Call out the repeated quiet-room test as a room bit, then refuse to serve the same menu again.",
                hook="Ask for one new detail: a game, a weird moment, or a tiny confession from today.",
                max_sentences=2,
                recent_motif=motif,
                avoid=("same_choice_menu", "same_silence_metaphor", "pretend_first_time"),
            )
            stat_key = "repeat_scene"
        elif topic == "quiet_room":
            directive = PublicSceneDirective(
                scene_type="room_warmup",
                scene_move="set_room_image_then_offer_hook",
                topic=topic,
                reason="viewer reports a quiet public room",
                confidence=0.74 if room_vibe == "quiet_room" else 0.62,
                beat="Paint one small public-room image, like Nana noticing the room is quiet but still alive.",
                hook="Offer one concrete hook, not a generic support question.",
                max_sentences=2,
                recent_motif=motif,
                avoid=("begging_for_chat", "service_question_tail", "same_choice_menu"),
            )
            stat_key = "quiet_scene"
        elif topic == "story":
            directive = PublicSceneDirective(
                scene_type="micro_story",
                scene_move="tell_short_scene",
                topic=topic,
                reason="viewer asks for or chooses a story bit",
                confidence=0.86 if _selected_story_from_recent(recent) else 0.76,
                beat="Tell a tiny public-safe scene with one setup, one twist, and one punchline.",
                hook="Leave a small callback the room can answer, instead of reopening the choice menu.",
                max_sentences=4,
                recent_motif=motif,
                avoid=("backend_story", "generic_room_prompt", "same_choice_menu"),
            )
            stat_key = "story_scene"
        elif topic in {"service_boundary", "identity_boundary"}:
            directive = PublicSceneDirective(
                scene_type="boundary_callback",
                scene_move="stance_then_playful_redirect",
                topic=topic,
                reason=f"viewer is testing Nana's public boundary ({topic})",
                confidence=0.84,
                beat="State Nana's boundary in one clear line with light backbone.",
                hook="Redirect to chat, game, story, or room rhythm without sounding like a service counter.",
                max_sentences=3,
                recent_motif=motif,
                avoid=("harsh_shutdown", "service_assistant_voice", "private_lane_leak"),
            )
            stat_key = "boundary_scene"
        elif topic == "model_talk":
            directive = PublicSceneDirective(
                scene_type="opinion_take",
                scene_move="answer_with_taste",
                topic=topic,
                reason="viewer is discussing model/GPT-hoa quality",
                confidence=0.80,
                beat="Give Nana's taste: natural rhythm, context memory, and less glossy corporate speech.",
                hook="Invite a concrete comparison only if needed; do not become a benchmark table.",
                max_sentences=3,
                recent_motif=motif,
                avoid=("benchmark_lecture", "corporate_ai_tone", "generic_disclaimer"),
            )
            stat_key = "model_scene"
        elif topic == "emoji":
            directive = PublicSceneDirective(
                scene_type="light_reaction",
                scene_move="ack_then_tiny_callback",
                topic=topic,
                reason="viewer sent emoji/sticker-style input",
                confidence=0.70,
                beat=f"Acknowledge {viewer}'s light reaction as a public-room signal.",
                hook="Add one tiny tease or callback; do not expand into a full speech.",
                max_sentences=2,
                recent_motif=motif,
                avoid=("overexplain_emoji", "same_ack_line"),
            )
            stat_key = "emoji_scene"
        else:
            directive = PublicSceneDirective(
                scene_type="open_thread",
                scene_move="answer_then_small_hook",
                topic=topic,
                reason="no specialized scene needed",
                confidence=0.46,
                beat="Answer the viewer's actual message first.",
                hook="If useful, add one small public-stage hook tied to the topic.",
                max_sentences=2,
                recent_motif=motif,
                avoid=("generic_room_prompt_when_unrelated", "service_question_tail"),
            )

        self._record(directive, stat_key=stat_key)
        return directive

    def _record(self, directive: PublicSceneDirective, *, stat_key: str) -> None:
        with self._lock:
            self._last_directive = directive
            self._stats["built"] += 1
            if stat_key in self._stats:
                self._stats[stat_key] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            last = self._last_directive
            stats = dict(self._stats)
        return {
            "phase": PHASE,
            "mode": "scene-topic-directive",
            "read_only": True,
            "can_act": False,
            "memory_write": False,
            "api_call": False,
            "initialized_at": self._initialized_at,
            "stats": stats,
            "last_directive": last.to_dict() if last else None,
        }


_SCENE_BUILDER = PublicSceneBuilder()


def get_public_scene_builder() -> PublicSceneBuilder:
    return _SCENE_BUILDER


def build_public_scene_directive(
    *,
    text: Any = "",
    event_type: str = "text",
    room_vibe: str = "quiet_room",
    viewer_name: Any = "viewer",
    recent_turns: list[Any] | tuple[Any, ...] | None = None,
    repeat_count: int = 0,
) -> PublicSceneDirective:
    return get_public_scene_builder().build(
        text=text,
        event_type=event_type,
        room_vibe=room_vibe,
        viewer_name=viewer_name,
        recent_turns=recent_turns,
        repeat_count=repeat_count,
    )


def format_public_scene_hint(directive: PublicSceneDirective) -> str:
    avoid_text = ", ".join(directive.avoid) if directive.avoid else "none"
    return "\n".join(
        [
            f"Public scene builder ({PHASE}):",
            f"- scene_type={directive.scene_type}",
            f"- scene_move={directive.scene_move}",
            f"- topic={directive.topic}",
            f"- max_sentences={directive.max_sentences}",
            f"- recent_motif={directive.recent_motif}",
            f"- beat={directive.beat}",
            f"- hook={directive.hook}",
            f"- avoid={avoid_text}",
            "- This is content bias only; no private memory, no long-term write, and no permission to act.",
        ]
    )


def public_scene_status_lines() -> list[str]:
    snap = get_public_scene_builder().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_directive") or {})
    return [
        f"Public Scene Builder ({PHASE})",
        "  Mode: scene-topic-directive | read_only=True | can_act=False | memory_write=False | api_call=False",
        (
            "  Last: "
            f"scene={last.get('scene_type', 'none')} | move={last.get('scene_move', 'none')} | "
            f"topic={last.get('topic', 'none')} | confidence={last.get('confidence', 0.0)}"
        ),
        (
            "  Stats: "
            f"built={stats.get('built', 0)} | quiet={stats.get('quiet_scene', 0)} | "
            f"story={stats.get('story_scene', 0)} | boundary={stats.get('boundary_scene', 0)} | "
            f"model={stats.get('model_scene', 0)} | repeat={stats.get('repeat_scene', 0)}"
        ),
        "  Commands: /public-scene-status | /public-scene-preview <text>",
        "  Safety: content bias only | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def public_scene_preview_lines(payload: str) -> list[str]:
    text = _clean(payload)
    if not text:
        return ["  Usage: /public-scene-preview <text>"]
    try:
        from nana.runtime.social_session import get_social_session

        snapshot = get_social_session().snapshot()
        recent_turns = list(snapshot.get("recent_turns") or [])
        last = dict(snapshot.get("last_viewer") or {})
        viewer = last.get("display_name") or "viewer"
        vibe = dict(snapshot.get("last_decision") or {}).get("room_vibe") or "quiet_room"
    except Exception:
        recent_turns = []
        viewer = "viewer"
        vibe = "quiet_room"
    directive = build_public_scene_directive(
        text=text,
        room_vibe=vibe,
        viewer_name=viewer,
        recent_turns=recent_turns,
    )
    return [
        f"Public Scene Preview ({PHASE})",
        f"  Input: {text}",
        f"  Scene: {directive.scene_type} | move={directive.scene_move} | topic={directive.topic}",
        f"  Beat: {directive.beat}",
        f"  Hook: {directive.hook}",
        f"  Avoid: {', '.join(directive.avoid) if directive.avoid else 'none'}",
        f"  Confidence: {directive.confidence:.2f} | max_sentences={directive.max_sentences}",
        "  Safety: preview only | content bias only | no write | no output action",
    ]
