"""STAGE-9I: public conversation director.

This layer reads the public-room turn history and produces a small content
directive for the next public reply. It is not an action gate: no API call, no
memory write, no Discord/VTS/TTS/OBS/game action.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import threading
import time
from typing import Any


PHASE = "STAGE-9I"

QUIET_MARKERS = (
    "phong im",
    "phong nay im",
    "im qua",
    "vang qua",
    "yen tinh",
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
    "ke di",
)

GAME_MARKERS = (
    "game",
    "osu",
    "stardew",
    "minecraft",
    "valorant",
    "lol",
    "cay game",
    "van game",
)

MUSIC_MARKERS = (
    "nhac",
    "bai hat",
    "nghe gi",
    "playlist",
    "cứu mood",
    "cuu mood",
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

CHOICE_REPLY_MARKERS = (
    "game",
    "nhac",
    "chuyen",
    "meme",
    "chọn nhanh",
    "chon nhanh",
    "ba lua chon",
    "lựa chọn",
    "lua chon",
)


@dataclass(frozen=True)
class PublicConversationDirective:
    thread: str
    viewer_intent: str
    response_move: str
    reason: str
    confidence: float
    instruction: str
    avoid: tuple[str, ...] = field(default_factory=tuple)
    phase: str = PHASE
    read_only: bool = True
    can_act: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence), 2)
        return data


def _clean(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _fold(value: Any) -> str:
    text = _clean(value, limit=500).lower()
    replacements = {
        "à": "a", "á": "a", "ả": "a", "ã": "a", "ạ": "a",
        "ă": "a", "ằ": "a", "ắ": "a", "ẳ": "a", "ẵ": "a", "ặ": "a",
        "â": "a", "ầ": "a", "ấ": "a", "ẩ": "a", "ẫ": "a", "ậ": "a",
        "è": "e", "é": "e", "ẻ": "e", "ẽ": "e", "ẹ": "e",
        "ê": "e", "ề": "e", "ế": "e", "ể": "e", "ễ": "e", "ệ": "e",
        "ì": "i", "í": "i", "ỉ": "i", "ĩ": "i", "ị": "i",
        "ò": "o", "ó": "o", "ỏ": "o", "õ": "o", "ọ": "o",
        "ô": "o", "ồ": "o", "ố": "o", "ổ": "o", "ỗ": "o", "ộ": "o",
        "ơ": "o", "ờ": "o", "ớ": "o", "ở": "o", "ỡ": "o", "ợ": "o",
        "ù": "u", "ú": "u", "ủ": "u", "ũ": "u", "ụ": "u",
        "ư": "u", "ừ": "u", "ứ": "u", "ử": "u", "ữ": "u", "ự": "u",
        "ỳ": "y", "ý": "y", "ỷ": "y", "ỹ": "y", "ỵ": "y",
        "đ": "d",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = re.sub(r"^!nana\s+", "", text)
    text = re.sub(r"[^\w\s.]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _has_any(folded: str, markers: tuple[str, ...]) -> bool:
    return any(_fold(marker) in folded for marker in markers)


def _turn_value(turn: Any, key: str) -> str:
    if isinstance(turn, dict):
        return _clean(turn.get(key, ""))
    return _clean(getattr(turn, key, ""))


def _recent_reply_offered_choices(recent_turns: list[Any]) -> bool:
    for turn in reversed(recent_turns[-4:]):
        reply = _fold(_turn_value(turn, "reply_preview"))
        if not reply:
            continue
        hits = sum(1 for marker in CHOICE_REPLY_MARKERS if _fold(marker) in reply)
        if hits >= 3 or ("game" in reply and "nhac" in reply and "chuyen" in reply):
            return True
    return False


def _recent_quiet_thread(recent_turns: list[Any]) -> bool:
    for turn in reversed(recent_turns[-4:]):
        message = _fold(_turn_value(turn, "message_preview"))
        reply = _fold(_turn_value(turn, "reply_preview"))
        if _has_any(message, QUIET_MARKERS) or _has_any(reply, QUIET_MARKERS):
            return True
    return False


def _classify_intent(text: str, *, event_type: str = "") -> str:
    folded = _fold(text)
    if _has_any(folded, SERVICE_MARKERS) and "nana" in folded:
        return "service_role_request"
    if "nana" in folded and _has_any(folded, IDENTITY_MARKERS):
        return "identity_challenge"
    if event_type == "story_request" or _has_any(folded, STORY_MARKERS):
        return "story_choice"
    if _has_any(folded, GAME_MARKERS):
        return "game_choice"
    if _has_any(folded, MUSIC_MARKERS):
        return "music_choice"
    if _has_any(folded, MODEL_MARKERS):
        return "model_topic"
    if _has_any(folded, QUIET_MARKERS):
        return "quiet_room_report"
    if len(folded.split()) <= 3 and folded:
        return "short_followup"
    return "open_chat"


class PublicConversationDirector:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_directive: PublicConversationDirective | None = None
        self._stats = {
            "built": 0,
            "selected_topic": 0,
            "repeat_thread": 0,
            "quiet_warmup": 0,
            "boundary": 0,
        }

    def build(
        self,
        *,
        text: Any = "",
        event_type: str = "text",
        room_vibe: str = "quiet_room",
        recent_turns: list[Any] | tuple[Any, ...] | None = None,
        repeat_count: int = 0,
    ) -> PublicConversationDirective:
        current_text = _clean(text)
        intent = _classify_intent(current_text, event_type=event_type)
        recent = list(recent_turns or [])
        has_choice_menu = _recent_reply_offered_choices(recent)
        has_quiet_thread = _recent_quiet_thread(recent)

        if intent == "service_role_request":
            directive = PublicConversationDirective(
                thread="public_boundary",
                viewer_intent=intent,
                response_move="stance_then_redirect",
                reason="viewer tried to assign Nana a service role",
                confidence=0.92,
                instruction="Refuse the service role with Nana's stance, then redirect to public chat/game/stage energy.",
                avoid=("service_counter_voice", "over_apology"),
            )
        elif intent == "identity_challenge":
            directive = PublicConversationDirective(
                thread="public_identity",
                viewer_intent=intent,
                response_move="stance_then_light_edge",
                reason="viewer challenged Nana's public identity",
                confidence=0.90,
                instruction="Answer with Nana's self-stance first, then a small stage-safe tease or room metaphor.",
                avoid=("bot_discord_label", "support_assistant_voice"),
            )
        elif intent in {"story_choice", "game_choice", "music_choice"} and (has_choice_menu or has_quiet_thread):
            thread = {
                "story_choice": "selected_story_thread",
                "game_choice": "selected_game_thread",
                "music_choice": "selected_music_thread",
            }[intent]
            label = {
                "story_choice": "story/chuyen ngao",
                "game_choice": "game",
                "music_choice": "music",
            }[intent]
            directive = PublicConversationDirective(
                thread=thread,
                viewer_intent=intent,
                response_move="continue_selected_topic",
                reason=f"viewer selected {label} from the current public thread",
                confidence=0.86,
                instruction=(
                    "Continue the selected thread directly. Do not reopen the quiet-room choice menu; "
                    "give one concrete beat that rewards the viewer's choice."
                ),
                avoid=("generic_quiet_room_prompt", "reopen_choice_menu", "same_game_music_story_menu"),
            )
        elif intent == "quiet_room_report":
            if repeat_count >= 6:
                directive = PublicConversationDirective(
                    thread="repeat_test",
                    viewer_intent=intent,
                    response_move="cooldown_or_tease_repeat",
                    reason=f"same quiet-room prompt repeated {repeat_count} times",
                    confidence=0.88,
                    instruction="Lightly acknowledge the repeated test and ask for a different hook; do not perform the old prompt again.",
                    avoid=("same_quiet_room_motif", "same_choice_menu"),
                )
                stat_key = "repeat_thread"
            elif repeat_count >= 3:
                directive = PublicConversationDirective(
                    thread="repeat_test",
                    viewer_intent=intent,
                    response_move="tease_repeat_pattern",
                    reason=f"same quiet-room prompt repeated {repeat_count} times",
                    confidence=0.82,
                    instruction="Notice the repeat pattern playfully and vary the angle instead of treating it as new.",
                    avoid=("same_quiet_room_motif", "same_choice_menu"),
                )
                stat_key = "repeat_thread"
            else:
                directive = PublicConversationDirective(
                    thread="room_warmup",
                    viewer_intent=intent,
                    response_move="offer_concrete_choices",
                    reason="viewer reports quiet room",
                    confidence=0.70 if room_vibe == "quiet_room" else 0.58,
                    instruction="Warm up the room with one concrete image or a compact set of choices.",
                    avoid=("begging_for_engagement", "service_question_tail"),
                )
                stat_key = "quiet_warmup"
            self._record(directive, stat_key=stat_key)
            return directive
        elif intent == "model_topic":
            directive = PublicConversationDirective(
                thread="model_discussion",
                viewer_intent=intent,
                response_move="answer_topic_with_taste",
                reason="viewer is discussing model/LLM quality",
                confidence=0.78,
                instruction="Answer the model topic with Nana's taste: naturalness, rhythm, context-following, and no corporate benchmark voice.",
                avoid=("generic_room_prompt", "benchmark_corporate_tone"),
            )
        elif intent == "short_followup" and (has_choice_menu or has_quiet_thread):
            directive = PublicConversationDirective(
                thread="followup_to_room_warmup",
                viewer_intent=intent,
                response_move="interpret_short_followup",
                reason="short viewer follow-up after Nana opened a public thread",
                confidence=0.62,
                instruction="Treat the short phrase as a reply to the current public thread; ask at most one clarifying question.",
                avoid=("restart_room_warmup", "generic_quiet_room_prompt"),
            )
        else:
            directive = PublicConversationDirective(
                thread="open_public_chat",
                viewer_intent=intent,
                response_move="continue_or_answer",
                reason="no specific public thread transition detected",
                confidence=0.45,
                instruction="Answer the actual message first, then keep the public-stage rhythm if a follow-up helps.",
                avoid=("generic_room_prompt_when_unrelated",),
            )

        stat_key = "selected_topic" if directive.response_move == "continue_selected_topic" else None
        if directive.thread in {"public_boundary", "public_identity"}:
            stat_key = "boundary"
        self._record(directive, stat_key=stat_key)
        return directive

    def _record(self, directive: PublicConversationDirective, *, stat_key: str | None = None) -> None:
        with self._lock:
            self._last_directive = directive
            self._stats["built"] += 1
            if stat_key and stat_key in self._stats:
                self._stats[stat_key] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            last = self._last_directive or PublicConversationDirective(
                thread="none",
                viewer_intent="none",
                response_move="none",
                reason="no directive built yet",
                confidence=0.0,
                instruction="No public conversation directive has been built yet.",
            )
            return {
                "phase": PHASE,
                "mode": "content-directive-only",
                "read_only": True,
                "can_act": False,
                "initialized_at": self._initialized_at,
                "stats": dict(self._stats),
                "last_directive": last.to_dict(),
            }


_DIRECTOR = PublicConversationDirector()


def get_public_conversation_director() -> PublicConversationDirector:
    return _DIRECTOR


def format_public_conversation_hint(directive: PublicConversationDirective) -> str:
    avoid_text = ", ".join(directive.avoid) if directive.avoid else "none"
    return "\n".join(
        [
            f"Public conversation director ({PHASE}):",
            f"- conversation_thread={directive.thread}",
            f"- viewer_intent={directive.viewer_intent}",
            f"- response_move={directive.response_move}",
            f"- confidence={directive.confidence:.2f}",
            f"- instruction={directive.instruction}",
            f"- avoid={avoid_text}",
            "- This is content bias only, not permission to send or act.",
        ]
    )


def build_public_conversation_directive(
    *,
    text: Any = "",
    event_type: str = "text",
    room_vibe: str = "quiet_room",
    recent_turns: list[Any] | tuple[Any, ...] | None = None,
    repeat_count: int = 0,
) -> PublicConversationDirective:
    return get_public_conversation_director().build(
        text=text,
        event_type=event_type,
        room_vibe=room_vibe,
        recent_turns=recent_turns,
        repeat_count=repeat_count,
    )


def public_conversation_status_lines() -> list[str]:
    snap = get_public_conversation_director().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_directive") or {})
    return [
        f"Public Conversation Director ({PHASE})",
        f"  Mode: {snap.get('mode')} | read_only=True | can_act=False",
        (
            "  Last: "
            f"thread={last.get('thread')} | intent={last.get('viewer_intent')} | "
            f"move={last.get('response_move')} | confidence={last.get('confidence')}"
        ),
        (
            "  Stats: "
            f"built={stats.get('built', 0)} | selected_topic={stats.get('selected_topic', 0)} | "
            f"repeat={stats.get('repeat_thread', 0)} | quiet={stats.get('quiet_warmup', 0)} | "
            f"boundary={stats.get('boundary', 0)}"
        ),
        "  Commands: /public-conversation-status | /public-conversation-preview <text>",
        "  Safety: content bias only | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def public_conversation_preview_lines(payload: str) -> list[str]:
    text = _clean(payload)
    if not text:
        return ["  Usage: /public-conversation-preview <text>"]
    try:
        from nana.runtime.social_session import get_social_session

        snap = get_social_session().snapshot()
        recent = list(snap.get("recent_turns") or [])
        last = dict(snap.get("last_decision") or {})
        room_vibe = str(last.get("room_vibe") or "quiet_room")
    except Exception:
        recent = []
        room_vibe = "quiet_room"
    directive = build_public_conversation_directive(
        text=text,
        room_vibe=room_vibe,
        recent_turns=recent,
    )
    return [
        f"Public Conversation Preview ({PHASE})",
        f"  Input: {text}",
        f"  Thread: {directive.thread} | intent={directive.viewer_intent} | move={directive.response_move}",
        f"  Confidence: {directive.confidence:.2f} | reason={directive.reason}",
        f"  Instruction: {directive.instruction}",
        f"  Avoid: {', '.join(directive.avoid) if directive.avoid else 'none'}",
        "  Safety: preview only | no LLM | no write | no output action",
    ]
