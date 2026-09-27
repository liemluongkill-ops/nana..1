"""STAGE-9S: public quiet-room rhythm.

This layer gives short "room is quiet" public prompts a small scene and a
single hook before the heavier repeat guards kick in. It is deterministic:
no LLM call, no memory write, and no action permission.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re
import threading
import time
import unicodedata
from typing import Any


PHASE = "STAGE-9S"

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

FIRST_SCENE_VARIANTS = [
    "Phòng đang yên như có ai đặt tay lên nút pause. Nana gõ nhẹ lên bàn: hôm nay có khoảnh khắc nào đáng kể không?",
    "Yên tới mức Nana nghe được cái chat thở nhẹ. Thả một chi tiết lạ hôm nay đi, Nana kéo thành chuyện.",
    "Phòng lặng nhưng chưa tắt đèn đâu. Nana dựng một góc nhỏ: ai có mảnh chuyện vừa buồn cười vừa sai sai không?",
    "Im thật, nhưng Nana vẫn thấy đèn phòng còn sáng. Ném cho Nana một mẩu chuyện lệch nhịp hôm nay đi.",
]

FRESH_SCENE_VARIANTS = [
    "Nana không mở lại menu cũ nữa. Lần này chỉ cần một mảnh thôi: thứ gì làm mọi người đứng hình hôm nay?",
    "Phòng vẫn yên, nhưng Nana đổi góc nhé. Kể một chi tiết nhỏ mà nếu bỏ qua thì hơi phí đi.",
    "Yên thì yên, nhưng đừng để câu này thành nhạc nền lặp. Nana hỏi thẳng: hôm nay có gì kỳ kỳ không?",
    "Nana nghe tiếng phòng im rồi. Giờ đổi nhịp: ai có một câu chuyện bé xíu nhưng đáng kể thì thả xuống bàn.",
]


@dataclass(frozen=True)
class PublicQuietRoomRhythmDirective:
    input_text: str
    mode: str
    repeat_count: int
    text: str
    instruction: str
    avoid: tuple[str, ...]
    confidence: float
    phase: str = PHASE
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False
    api_call: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    text = re.sub(r"^!nana\s+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_quiet_room_text(value: Any) -> bool:
    folded = _fold(value)
    return bool(folded and any(marker in folded for marker in QUIET_MARKERS))


def _choose_variant(variants: list[str], *, seed: str = "") -> str:
    if not variants:
        return ""
    digest = hashlib.sha256(str(seed or time.time_ns()).encode("utf-8", errors="ignore")).hexdigest()
    return variants[int(digest[:8], 16) % len(variants)]


class PublicQuietRoomRhythm:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_directive: PublicQuietRoomRhythmDirective | None = None
        self._stats = {
            "built": 0,
            "quiet": 0,
            "first_scene": 0,
            "fresh_scene": 0,
            "repeat_defer": 0,
            "none": 0,
        }

    def build(
        self,
        *,
        text: Any = "",
        repeat_count: int = 1,
        feedback_mode: str = "none",
        seed: str = "",
    ) -> PublicQuietRoomRhythmDirective:
        cleaned = _clean(text)
        count = max(1, int(repeat_count or 1))
        feedback = str(feedback_mode or "none").strip().lower()
        if not _is_quiet_room_text(cleaned):
            directive = PublicQuietRoomRhythmDirective(
                input_text=cleaned,
                mode="none",
                repeat_count=count,
                text="",
                instruction="No quiet-room rhythm directive is needed for this turn.",
                avoid=("internal_label_leak",),
                confidence=0.0,
            )
            self._record(directive)
            return directive

        if count >= 3:
            directive = PublicQuietRoomRhythmDirective(
                input_text=cleaned,
                mode="repeat_defer",
                repeat_count=count,
                text="",
                instruction="Let repeat-awareness handle this turn; avoid pretending it is the first quiet-room prompt.",
                avoid=("same_choice_menu", "pretend_first_time", "begging_for_chat"),
                confidence=0.72,
            )
            self._record(directive)
            return directive

        if count >= 2 or feedback == "avoid_menu_loop":
            mode = "fresh_scene"
            variants = FRESH_SCENE_VARIANTS
            instruction = "Use a fresh room image and one specific hook; do not reopen the old game/music/story menu."
            confidence = 0.76
        else:
            mode = "first_scene"
            variants = FIRST_SCENE_VARIANTS
            instruction = "Paint one small room image, then offer one concrete hook without sounding like a menu."
            confidence = 0.70

        reply = _choose_variant(variants, seed=seed or f"{mode}:{cleaned}:{count}:{time.time_ns()}")
        directive = PublicQuietRoomRhythmDirective(
            input_text=cleaned,
            mode=mode,
            repeat_count=count,
            text=reply,
            instruction=instruction,
            avoid=("same_choice_menu", "service_question_tail", "begging_for_chat"),
            confidence=confidence,
        )
        self._record(directive)
        return directive

    def reply(
        self,
        *,
        text: Any = "",
        repeat_count: int = 1,
        feedback_mode: str = "none",
        seed: str = "",
    ) -> str:
        directive = self.build(
            text=text,
            repeat_count=repeat_count,
            feedback_mode=feedback_mode,
            seed=seed,
        )
        return directive.text

    def _record(self, directive: PublicQuietRoomRhythmDirective) -> None:
        with self._lock:
            self._stats["built"] += 1
            if directive.mode == "none":
                self._stats["none"] += 1
            else:
                self._stats["quiet"] += 1
                self._stats[directive.mode] = self._stats.get(directive.mode, 0) + 1
            self._last_directive = directive

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
            last = self._last_directive.to_dict() if self._last_directive else None
        return {
            "phase": PHASE,
            "mode": "deterministic-quiet-room-rhythm",
            "uptime_seconds": max(0.0, time.time() - self._initialized_at),
            "stats": stats,
            "last_directive": last,
            "read_only": True,
            "can_act": False,
            "memory_write": False,
            "api_call": False,
        }


_QUIET_ROOM_RHYTHM = PublicQuietRoomRhythm()


def get_public_quiet_room_rhythm() -> PublicQuietRoomRhythm:
    return _QUIET_ROOM_RHYTHM


def build_public_quiet_room_rhythm_directive(
    *,
    text: Any = "",
    repeat_count: int = 1,
    feedback_mode: str = "none",
    seed: str = "",
) -> PublicQuietRoomRhythmDirective:
    return get_public_quiet_room_rhythm().build(
        text=text,
        repeat_count=repeat_count,
        feedback_mode=feedback_mode,
        seed=seed,
    )


def public_quiet_room_rhythm_reply(
    *,
    text: Any = "",
    repeat_count: int = 1,
    feedback_mode: str = "none",
    seed: str = "",
) -> str:
    return get_public_quiet_room_rhythm().reply(
        text=text,
        repeat_count=repeat_count,
        feedback_mode=feedback_mode,
        seed=seed,
    )


def public_quiet_room_status_lines() -> list[str]:
    snap = get_public_quiet_room_rhythm().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_directive") or {})
    return [
        f"Public Quiet-Room Rhythm ({PHASE})",
        "  Mode: deterministic-quiet-room-rhythm | read_only=True | can_act=False | memory_write=False | api_call=False",
        (
            "  Last: "
            f"mode={last.get('mode', 'none')} | repeat={last.get('repeat_count', 0)} | "
            f"confidence={last.get('confidence', 0.0)}"
        ),
        (
            "  Stats: "
            f"built={stats.get('built', 0)} | quiet={stats.get('quiet', 0)} | "
            f"first={stats.get('first_scene', 0)} | fresh={stats.get('fresh_scene', 0)} | "
            f"repeat_defer={stats.get('repeat_defer', 0)}"
        ),
        "  Commands: /public-quiet-room-status | /public-quiet-room-preview <text>",
        "  Safety: deterministic only | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def public_quiet_room_preview_lines(payload: str) -> list[str]:
    text = _clean(payload)
    if not text:
        return ["  Usage: /public-quiet-room-preview <text>"]
    try:
        from nana.runtime.social_session import get_social_session

        decision = dict(get_social_session().snapshot().get("last_decision") or {})
        style_hint = str(decision.get("style_hint") or "")
        match = re.search(r"similar_question_count_this_session=(\d+)", style_hint)
        repeat_count = int(match.group(1)) if match else 1
    except Exception:
        repeat_count = 1
    try:
        from nana.runtime.public_reply_feedback import get_public_reply_feedback

        feedback = dict(get_public_reply_feedback().snapshot().get("last_directive") or {})
        feedback_mode = str(feedback.get("mode") or "none")
    except Exception:
        feedback_mode = "none"
    directive = build_public_quiet_room_rhythm_directive(
        text=text,
        repeat_count=repeat_count,
        feedback_mode=feedback_mode,
        seed=f"preview:{text}:{repeat_count}:{feedback_mode}",
    )
    avoid = ", ".join(directive.avoid) if directive.avoid else "none"
    return [
        f"Public Quiet-Room Preview ({PHASE})",
        f"  Input: {text}",
        f"  Mode: {directive.mode} | repeat={directive.repeat_count} | confidence={directive.confidence}",
        f"  Instruction: {directive.instruction}",
        f"  Avoid: {avoid}",
        f"  Text: {directive.text or 'none'}",
        "  Safety: preview only | deterministic | no write | no output action",
    ]
