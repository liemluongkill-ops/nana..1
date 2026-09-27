"""Conversation starters for public social chat (STAGE-7E-C).

Read-only starter planner: generates topic-aware suggestions when the public room
is idle. No auto-send — only preview via /social-starter-preview.

No disk persistence, no private owner memory, no TTS/VTS/OBS, no game input.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any

from nana.runtime.social_session import (
    ACTION_FULL_REPLY,
    ACTION_ACK_ONLY,
    ACTION_SKIP,
    EVENT_TEXT,
    EVENT_QUESTION,
    EVENT_STORY_REQUEST,
    EVENT_GREETING,
    get_social_session,
    infer_topic,
)


PHASE = "STAGE-7E-C"
DEFAULT_IDLE_THRESHOLD_SECONDS = 120.0
DEFAULT_COOLDOWN_SECONDS = 300.0
DEFAULT_STARTER_LIMIT = 3
DEFAULT_STARTER_MAX_CHARS = 220


# Topic-aware starter prompts — public-safe, VTuber-flavored.
# Each entry: (topic_tag, cooldown_multiplier, starter_prompt)
_STARTER_POOL = [
    ("general", 1.0, "Bạn là Nana, một AI VTuber đang trò chuyện với khán giả trên kênh chat. Phòng chat đang khá im lặng. Hãy gợi một câu chuyện hoặc câu hỏi nhẹ nhàng, tự nhiên để khán giả tham gia. Không spam, không hỏi quá sâu, không đề cập backend, runtime, code, hay chuyện riêng của owner. Ưu tiên chủ đề giải trí, âm nhạc, hoặc trải nghiệm xem stream."),
    ("music", 1.2, "Bạn là Nana, một AI VTuber. Phòng chat đang im và bạn muốn gợi chuyện về âm nhạc một cách tự nhiên. Hãy hỏi khán giả xem họ đang nghe nhạc gì gần đây, hoặc chia sẻ một bài hát yêu thích. Không spam, giữ tone vui vẻ và thân thiện."),
    ("stream", 1.0, "Bạn là Nana, một AI VTuber. Phòng chat đang khá yên, bạn muốn hỏi khán giả một câu nhẹ nhàng về trải nghiệm xem stream của họ — ví dụ họ thường xem stream vào lúc nào, hay kênh nào họ hay ghé thăm. Không spam, không hỏi quá nhiều."),
    ("fun", 1.5, "Bạn là Nana, một AI VTuber đang nói chuyện với khán giả. Phòng chat im lặng và bạn muốn tạo một khoảnh khắc vui nhẹ nhàng. Hãy chia sẻ một suy nghĩ vui, một câu hỏi thú vị, hoặc một trò nhỏ để khán giả cười. Không toxic, không nhạy cảm, không spam."),
    ("food", 1.3, "Bạn là Nana, một AI VTuber. Phòng chat đang yên và bạn muốn gợi chuyện về đồ ăn — món ăn vặt khi xem stream, món họ thích nấu, hoặc món họ muốn thử. Giữ tone vui vẻ và tự nhiên, không spam."),
]

_FALLBACK_STARTERS = {
    "general": "Phòng chat yên ghê. Mọi người đang xem stream kiểu vừa làm việc, vừa ăn vặt, hay nằm chill vậy?",
    "music": "Nãy giờ Nana hơi tò mò: dạo này mọi người đang nghe bài nào nhiều nhất vậy?",
    "stream": "Mọi người thường xem stream lúc nào nhất, buổi trưa tranh thủ hay tối nằm chill?",
    "fun": "Cho Nana hỏi vui một câu nha: hôm nay mọi người gặp chuyện gì buồn cười nhất?",
    "food": "Nếu vừa xem stream vừa ăn vặt thì mọi người chọn món gì vậy?",
}


def _get_idle_seconds() -> float:
    """Return seconds since the last public chat activity."""
    try:
        snap = get_social_session().snapshot()
        last_decision = snap.get("last_decision") or {}
        last_ts = last_decision.get("timestamp") or 0.0
        if not last_ts:
            return float("inf")
        return time.time() - last_ts
    except Exception:
        return float("inf")


def _get_chat_velocity() -> float:
    """Return current chat velocity (messages/second)."""
    try:
        snap = get_social_session().snapshot()
        return float(snap.get("chat_velocity_per_second") or 0.0)
    except Exception:
        return 0.0


def _get_room_topic() -> str:
    """Return current room topic tag."""
    try:
        snap = get_social_session().snapshot()
        topic = snap.get("room_topic") or ""
        if not topic:
            return "general"
        return topic.lower()
    except Exception:
        return "general"


def _get_last_addressed_viewer() -> str | None:
    """Return last addressed viewer name, or None."""
    try:
        snap = get_social_session().snapshot()
        return snap.get("last_addressed_viewer")
    except Exception:
        return None


def _get_skipped_light_reactions() -> int:
    try:
        snap = get_social_session().snapshot()
        return int(snap.get("skipped_light_reactions") or 0)
    except Exception:
        return 0


class SocialStarterCache:
    """In-memory conversation starter planner for public chat (STAGE-7E-C).

    Read-only: generates suggestions, does NOT send to chat.
    """

    def __init__(
        self,
        *,
        idle_threshold_seconds: float = DEFAULT_IDLE_THRESHOLD_SECONDS,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        starter_limit: int = DEFAULT_STARTER_LIMIT,
        max_chars: int = DEFAULT_STARTER_MAX_CHARS,
    ) -> None:
        self.idle_threshold_seconds = float(idle_threshold_seconds)
        self.cooldown_seconds = float(cooldown_seconds)
        self.starter_limit = int(starter_limit)
        self.max_chars = int(max_chars)
        self._lock = threading.Lock()
        self._last_starter_time: float = 0.0
        self._last_starter_text: str = ""
        self._last_starter_reason: str = ""
        self._starter_count: int = 0
        self._blocked_reason: str = ""
        self._is_blocked: bool = False

    def _compute_blocked(self) -> tuple[bool, str]:
        """Check all gates; returns (is_blocked, reason)."""
        # Gate 0: STAGE-8A stream policy (read-only, no send).
        # Block proactive starters if stream policy forbids proactive.
        try:
            from nana.runtime.stream_state import get_stream_state
            from nana.runtime.proactive_engine import get_proactive_engine

            policy = get_stream_state().get_policy()
            if not policy.can_proactive:
                return True, f"stream_policy/offline ({policy.state.value})"

            decision = get_proactive_engine().should_proactive(
                policy=policy,
                chat_velocity=0.0,
                last_proactive_age_s=999.0,
                last_nana_chat_age_s=999.0,
            )
            if not decision.should_proactive:
                return True, f"stream_policy/{decision.reason}"
        except Exception as exc:
            return True, f"stream_policy_error:{type(exc).__name__}"

        # Gate 1: idle threshold
        idle = _get_idle_seconds()
        if idle < self.idle_threshold_seconds:
            return True, f"not_idle (idle={idle:.0f}s, threshold={self.idle_threshold_seconds:.0f}s)"

        # Gate 2: cooldown
        now = time.time()
        if now - self._last_starter_time < self.cooldown_seconds:
            remaining = self.cooldown_seconds - (now - self._last_starter_time)
            return True, f"cooldown (remaining={remaining:.0f}s)"

        # Gate 3: fast chat blocks starter
        velocity = _get_chat_velocity()
        if velocity >= 0.5:
            return True, f"fast_chat (velocity={velocity:.2f}/s)"

        # Gate 4: recent reply blocks
        last_addressed = _get_last_addressed_viewer()
        if last_addressed:
            recent_turns_count = 0
            try:
                snap = get_social_session().snapshot()
                recent_turns_count = snap.get("recent_turns_count") or 0
            except Exception:
                pass
            if recent_turns_count >= 2:
                return True, f"recent_reply (last_addressed={last_addressed}, recent_turns={recent_turns_count})"

        return False, ""

    def _select_starter_prompt(self) -> str:
        """Select starter prompt based on room topic."""
        topic = _get_room_topic()
        best_prompt = _STARTER_POOL[0][2]
        best_mult = 999.0
        for tag, mult, prompt in _STARTER_POOL:
            if tag in topic or topic in tag:
                if mult < best_mult:
                    best_mult = mult
                    best_prompt = prompt
        return best_prompt

    def _fallback_starter(self) -> str:
        topic = _get_room_topic()
        try:
            from nana.runtime.public_voice_style import public_quiet_starter_reply

            return public_quiet_starter_reply(topic=topic, seed=f"starter:{topic}:{self._starter_count}")
        except Exception:
            pass
        for key, starter in _FALLBACK_STARTERS.items():
            if key in topic or topic in key:
                return starter
        return _FALLBACK_STARTERS["general"]

    def _sanitize_starter(self, value: Any) -> str:
        text = str(value or "").strip()
        text = re.sub(r"^(?:\([^()\n]{0,220}\)\s*)+", " ", text)
        text = re.sub(r"\*[^*\n]{0,180}\*", " ", text)
        text = re.sub(r"\[[^\]\n]{0,80}\]", " ", text)
        text = text.replace('"', "").replace("“", "").replace("”", "")
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return self._fallback_starter()
        sentences = re.split(r"(?<=[.!?。！？])\s+", text)
        picked: list[str] = []
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            candidate = " ".join([*picked, sentence]).strip()
            if len(candidate) <= self.max_chars:
                picked.append(sentence)
            else:
                break
            if len(picked) >= 2:
                break
        text = " ".join(picked).strip() or text
        if len(text) > self.max_chars:
            text = text[: max(0, self.max_chars - 1)].rstrip(" ,.;:") + "…"
        if len(text) > self.max_chars or "\n" in text or "*" in text:
            text = self._fallback_starter()
        return text

    def generate_starter(self) -> dict[str, Any]:
        """Generate one conversation starter if all gates pass."""
        is_blocked, reason = self._compute_blocked()
        now = time.time()

        with self._lock:
            if is_blocked:
                self._is_blocked = True
                self._blocked_reason = reason
                return {
                    "starter": None,
                    "blocked": True,
                    "blocked_reason": reason,
                    "eligible": False,
                    "starter_count": self._starter_count,
                    "cooldown_remaining": max(0.0, self.cooldown_seconds - (now - self._last_starter_time)),
                    "idle_seconds": _get_idle_seconds(),
                    "chat_velocity": _get_chat_velocity(),
                    "room_topic": _get_room_topic(),
                }

            self._is_blocked = False
            self._blocked_reason = ""

            prompt = self._select_starter_prompt()
            try:
                from nana.brain.llmgate_client import call_llmgate_messages
                content, _ = call_llmgate_messages(
                    "nana-banter",
                    [{"role": "user", "content": prompt}],
                    max_tokens=120,
                    temperature=0.8,
                )
                starter = self._sanitize_starter(content)
                if not starter:
                    starter = self._fallback_starter()
            except Exception:
                starter = self._fallback_starter()

            self._last_starter_time = now
            self._last_starter_text = starter
            self._last_starter_reason = reason
            self._starter_count += 1

            return {
                "starter": starter,
                "blocked": False,
                "blocked_reason": "",
                "eligible": True,
                "starter_count": self._starter_count,
                "cooldown_remaining": 0.0,
                "idle_seconds": _get_idle_seconds(),
                "chat_velocity": _get_chat_velocity(),
                "room_topic": _get_room_topic(),
            }

    def preview(self) -> dict[str, Any]:
        """Preview one starter (always tries, even when blocked)."""
        result = self.generate_starter()
        return result

    def snapshot(self) -> dict[str, Any]:
        is_blocked, reason = self._compute_blocked()
        now = time.time()
        return {
            "phase": PHASE,
            "read_only": True,
            "can_act": False,
            "idle_threshold_seconds": self.idle_threshold_seconds,
            "cooldown_seconds": self.cooldown_seconds,
            "max_chars": self.max_chars,
            "is_blocked": is_blocked,
            "blocked_reason": reason,
            "last_starter_age": None if not self._last_starter_time else now - self._last_starter_time,
            "last_starter_preview": _truncate(self._last_starter_text, 80),
            "last_starter_reason": self._last_starter_reason,
            "starter_count": self._starter_count,
            "idle_seconds": _get_idle_seconds(),
            "chat_velocity": _get_chat_velocity(),
            "room_topic": _get_room_topic(),
            "last_addressed_viewer": _get_last_addressed_viewer(),
            "skipped_light_reactions": _get_skipped_light_reactions(),
            "safety": {
                "can_act": False,
                "auto_send": False,
                "preview_only": True,
            },
        }


def _truncate(value: str, limit: int = 80) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


_STARTER_CACHE = SocialStarterCache()


def get_social_starter() -> SocialStarterCache:
    return _STARTER_CACHE


def social_starter_status_lines() -> list[str]:
    snap = get_social_starter().snapshot()
    blocked = snap.get("blocked_reason") or "none" if snap.get("is_blocked") else "not_blocked"
    idle = snap.get("idle_seconds")
    idle_text = f"{float(idle):.0f}s" if idle != float("inf") else "never"
    cooldown = snap.get("cooldown_remaining") or 0.0
    cooldown_text = f"{float(cooldown):.0f}s" if cooldown > 0 else "ready"
    last_age = snap.get("last_starter_age")
    last_age_text = f"{float(last_age):.0f}s ago" if last_age else "never"
    lines = [
        "💬 Social Starter Planner",
        f"  Mode: {PHASE} | read_only=True | can_act=False | auto_send=False",
        (
            f"  Gates: blocked={snap.get('is_blocked')} | reason={blocked} | "
            f"idle={idle_text} | cooldown={cooldown_text}"
        ),
        (
            f"  Thresholds: idle_threshold={snap.get('idle_threshold_seconds'):.0f}s | "
            f"cooldown={snap.get('cooldown_seconds'):.0f}s | "
            f"max_chars={snap.get('max_chars')}"
        ),
        (
            f"  Context: room_topic={snap.get('room_topic') or 'none'} | "
            f"velocity={snap.get('chat_velocity', 0.0):.2f}/s | "
            f"last_addressed={snap.get('last_addressed_viewer') or 'none'}"
        ),
        (
            f"  Stats: starters_shown={snap.get('starter_count')} | "
            f"last={last_age_text} | preview='{snap.get('last_starter_preview') or 'none'}'"
        ),
        (
            "  Safety: auto_send=False | preview_only=True | "
            "no_private_memory | no_disk_persistence | no_Ba_con_leak"
        ),
        "  Live verify: /social-starter-status | /social-starter-preview",
    ]
    return lines
