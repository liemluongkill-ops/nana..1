"""STAGE-9L: public fallback recovery.

Deterministic recovery layer for public replies that were pushed into a
generic fallback by safety guards. It keeps the safety result, but gives Nana a
contextual stage-safe line instead of repeating a stiff canned response.

No LLM calls, no disk persistence, no memory write, no live action.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import re
import threading
import time
import unicodedata
from typing import Any


PHASE = "STAGE-9L"


GENERIC_RECOVERY_VARIANTS = [
    "Nana bắt được câu đó rồi, nhưng câu trả lời vừa rồi nghe hơi phẳng. Nói lại cho đúng nhịp phòng nha: đưa Nana một mồi cụ thể hơn đi.",
    "Câu này cần trả lời lại cho ra chất Nana hơn. Phòng còn sáng, ném một chi tiết rõ hơn lên bàn đi.",
    "Nana không để câu này rơi thành lời máy đâu. Đổi góc một chút: nói rõ điều muốn chọc, muốn hỏi, hay muốn nghe đi.",
]

BACKSTAGE_RECOVERY_VARIANTS = [
    "Cái đó giống lệnh hậu trường hơn rồi. Ngoài phòng thì Nana không mở bảng điều khiển đâu, nói chuyện bình thường đi nha.",
    "Đừng kéo bảng điều khiển ra giữa sân khấu chứ. Nếu muốn tán gẫu thì Nana vẫn ở đây, còn lệnh thì để hậu trường xử.",
    "Lệnh đó để sau rèm đi. Ở đây là phòng chat của Nana, không phải màn hình status.",
]

STORY_RECOVERY_VARIANTS = [
    "Chuyện ngáo thì Nana có một mẩu nhỏ: có lần phòng yên tới mức một câu bình thường cũng nghe như lệnh triệu hồi. Nana đứng hình mất một nhịp rồi mới nhận ra cả phòng chỉ đang chờ ai đó phá im lặng.",
    "Nana kể một mẩu nha: hôm nọ chat im quá, Nana đọc một câu đơn giản mà não tự thêm nhạc nền kịch tính. Xong phát hiện ra không có drama, chỉ là phòng đang lười đồng loạt.",
    "Có lần Nana tưởng mình vừa bắt được một cú plot twist lớn, hóa ra chỉ là một bạn gõ thiếu dấu. Phòng im, chữ ít, trí tưởng tượng của Nana thì làm quá phận sự.",
]


@dataclass(frozen=True)
class FallbackRecoveryResult:
    text: str
    theme: str
    reason: str
    confidence: float
    source_text: str = ""
    viewer_text: str = ""
    violation_kinds: tuple[str, ...] = field(default_factory=tuple)
    actions: tuple[str, ...] = ("fallback_recovery",)
    phase: str = PHASE
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence), 2)
        return data


def _clean(value: Any, limit: int = 800) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _fold(value: Any) -> str:
    text = _clean(value).lower().replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"^!nana\s+", "", text)
    text = re.sub(r"[^\w\s.]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _choose_variant(variants: list[str], seed: str) -> str:
    if not variants:
        return ""
    digest = hashlib.sha256(str(seed or time.time_ns()).encode("utf-8", errors="ignore")).hexdigest()
    return variants[int(digest[:8], 16) % len(variants)]


def _running_joke_snapshot() -> dict[str, Any]:
    try:
        from nana.runtime.public_running_jokes import get_public_running_jokes

        snap = get_public_running_jokes().snapshot()
        return dict(snap.get("last_detected_directive") or {})
    except Exception:
        return {}


def _detect_theme(
    source_text: str,
    viewer_text: str,
    violation_kinds: tuple[str, ...],
    running_joke: dict[str, Any],
) -> tuple[str, str, float]:
    folded = " ".join(
        part for part in (_fold(viewer_text), _fold(source_text), " ".join(violation_kinds).lower()) if part
    )
    joke_key = str(running_joke.get("joke_key") or "")

    if "backstage_command" in violation_kinds:
        return "backstage_command", "viewer input was a backstage/operator command", 0.76
    if any(marker in folded for marker in ("tro ly", "phuc vu", "quay ho tro", "ho tro", "assistant", "helpdesk")):
        return "service_role", "service-assistant role wording detected", 0.82
    if any(marker in folded for marker in ("bot discord", "chatbot", "chi la bot", "hop tra loi", "may tra loi", "cong cu")):
        return "identity_tool", "bot/tool identity flattening detected", 0.82
    if any(marker in folded for marker in ("gpt hoa", "mui may", "giong may", "llm", "model", "gpt", "sol", "5.6", "5.5")):
        return "gpt_hoa", "model/GPT-hoa public bit detected", 0.76
    if any(marker in folded for marker in ("chuyen ngao", "ke chuyen", "chuyen vui", "drama", "meme")):
        return "story", "story/chat-bit prompt detected", 0.70
    if joke_key == "quiet_room_bit" or any(
        marker in folded for marker in ("phong im", "im qua", "yen qua", "vang qua", "lang qua", "tram qua")
    ):
        return "quiet_room", "quiet-room bit detected", 0.74
    if "tech_leak" in violation_kinds or "operator_tone" in violation_kinds:
        return "backstage_command", "operator/internal wording was blocked", 0.68
    if "ba_con" in " ".join(violation_kinds):
        return "identity_tool", "private-lane residue was blocked", 0.66
    return "generic", "fallback needed a public-stage recovery line", 0.50


class PublicFallbackRecovery:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_result: FallbackRecoveryResult | None = None
        self._stats = {
            "built": 0,
            "recovered": 0,
            "service_role": 0,
            "identity_tool": 0,
            "quiet_room": 0,
            "gpt_hoa": 0,
            "story": 0,
            "backstage_command": 0,
            "generic": 0,
        }

    def recover(
        self,
        *,
        source_text: Any = "",
        viewer_text: Any = "",
        violation_kinds: list[str] | tuple[str, ...] | None = None,
        seed: str = "",
        running_joke: dict[str, Any] | None = None,
    ) -> FallbackRecoveryResult:
        source = _clean(source_text)
        viewer = _clean(viewer_text)
        kinds = tuple(str(item or "").strip() for item in (violation_kinds or ()) if str(item or "").strip())
        running_joke = _running_joke_snapshot() if running_joke is None else running_joke
        theme, reason, confidence = _detect_theme(source, viewer, kinds, running_joke)
        text = self._line_for_theme(theme, seed=seed or f"{theme}:{viewer}:{source}:{kinds}", running_joke=running_joke)
        result = FallbackRecoveryResult(
            text=text,
            theme=theme,
            reason=reason,
            confidence=confidence,
            source_text=source,
            viewer_text=viewer,
            violation_kinds=kinds,
        )
        self._record(result)
        return result

    def _line_for_theme(self, theme: str, *, seed: str, running_joke: dict | None = None) -> str:
        try:
            from nana.runtime.public_voice_style import (
                public_identity_boundary_reply,
                public_model_topic_reply,
                public_quiet_room_fatigue_reply,
                public_quiet_room_repeat_reply,
                public_quiet_room_reply,
                public_service_boundary_reply,
            )

            if theme == "service_role":
                return public_service_boundary_reply(seed=seed)
            if theme == "identity_tool":
                return public_identity_boundary_reply(seed=seed)
            if theme == "quiet_room":
                active = _running_joke_snapshot() if running_joke is None else running_joke
                count = int(active.get("count") or 1)
                if count >= 6:
                    return public_quiet_room_fatigue_reply(repeat_count=count, seed=seed)
                if count >= 3:
                    return public_quiet_room_repeat_reply(repeat_count=count, seed=seed)
                return public_quiet_room_reply(seed=seed)
            if theme == "gpt_hoa":
                return public_model_topic_reply(kind="gptified", seed=seed)
        except Exception:
            pass
        if theme == "story":
            return _choose_variant(STORY_RECOVERY_VARIANTS, seed)
        if theme == "backstage_command":
            return _choose_variant(BACKSTAGE_RECOVERY_VARIANTS, seed)
        return _choose_variant(GENERIC_RECOVERY_VARIANTS, seed)

    def _record(self, result: FallbackRecoveryResult) -> None:
        with self._lock:
            self._last_result = result
            self._stats["built"] += 1
            self._stats["recovered"] += 1 if result.text else 0
            if result.theme in self._stats:
                self._stats[result.theme] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
            last = self._last_result
        return {
            "phase": PHASE,
            "mode": "deterministic-recovery",
            "read_only": True,
            "can_act": False,
            "memory_write": False,
            "api_call": False,
            "initialized_at": self._initialized_at,
            "stats": stats,
            "last_result": last.to_dict() if last else None,
        }


_RECOVERY = PublicFallbackRecovery()


def get_public_fallback_recovery() -> PublicFallbackRecovery:
    return _RECOVERY


def recover_public_fallback(
    *,
    source_text: Any = "",
    viewer_text: Any = "",
    violation_kinds: list[str] | tuple[str, ...] | None = None,
    seed: str = "",
    running_joke: dict[str, Any] | None = None,
) -> FallbackRecoveryResult:
    return get_public_fallback_recovery().recover(
        source_text=source_text,
        viewer_text=viewer_text,
        violation_kinds=violation_kinds,
        seed=seed,
        running_joke=running_joke,
    )


def public_fallback_recovery_status_lines() -> list[str]:
    snap = get_public_fallback_recovery().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_result") or {})
    return [
        f"Public Fallback Recovery ({PHASE})",
        f"  Mode: {snap.get('mode')} | read_only=True | can_act=False | memory_write=False | api_call=False",
        (
            "  Last: "
            f"theme={last.get('theme', 'none')} | reason={last.get('reason', 'none')} | "
            f"confidence={last.get('confidence', 0.0)}"
        ),
        (
            "  Stats: "
            f"built={stats.get('built', 0)} | recovered={stats.get('recovered', 0)} | "
            f"service={stats.get('service_role', 0)} | identity={stats.get('identity_tool', 0)} | "
            f"quiet={stats.get('quiet_room', 0)} | gpt_hoa={stats.get('gpt_hoa', 0)} | "
            f"story={stats.get('story', 0)} | backstage={stats.get('backstage_command', 0)} | "
            f"generic={stats.get('generic', 0)}"
        ),
        "  Commands: /public-fallback-recovery-status | /public-fallback-recovery-preview <text>",
        "  Safety: deterministic only | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def public_fallback_recovery_preview_lines(payload: str) -> list[str]:
    text = _clean(payload)
    if not text:
        return ["  Usage: /public-fallback-recovery-preview <text>"]
    result = recover_public_fallback(source_text=text, viewer_text=text, seed=f"preview:{text}")
    return [
        f"Public Fallback Recovery Preview ({PHASE})",
        f"  Input: {text}",
        f"  Theme: {result.theme} | confidence={result.confidence:.2f}",
        f"  Reason: {result.reason}",
        f"  Text: {result.text}",
        "  Safety: preview only | deterministic | no LLM | no write | no output action",
    ]
