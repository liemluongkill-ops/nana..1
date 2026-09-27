"""Bounded low-latency private-chat lane for Nana.

The fast lane is deliberately narrow. It handles only short, self-contained
voice/chat prompts and never receives durable memory, browser context, tools,
or technical state. Everything ambiguous or consequential stays on Nana's
main model path.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import threading
import time
import unicodedata

from nana.config import (
    LLM_FAST_PRIVATE_ENABLED,
    LLM_FAST_PRIVATE_MAX_INPUT_CHARS,
    LLM_FAST_PRIVATE_MAX_TOKENS,
    LLM_FAST_PRIVATE_MODEL,
)


_FAST_INTENT_MARKERS = (
    "trả lời",
    "tra loi",
    "nói một câu",
    "noi mot cau",
    "nói thử",
    "noi thu",
    "đếm từ",
    "dem tu",
    "đọc từ",
    "doc tu",
    "chào ba",
    "chao ba",
    "alo",
)

_BLOCK_MARKERS = (
    # Technical, diagnostics, devices, and implementation work.
    "api",
    "bug",
    "camera",
    "code",
    "compile",
    "debug",
    "esp32",
    "exception",
    "firmware",
    "flash",
    "llm",
    "log",
    "memory",
    "mic",
    "model",
    "python",
    "ram",
    "speaker",
    "stack trace",
    "traceback",
    "voice",
    "wiki",
    "lỗi",
    "loa",
    "màn hình",
    "tối ưu",
    "sửa",
    # Reasoning, research, context-dependent, and temporal requests.
    "giải thích",
    "phân tích",
    "so sánh",
    "đề xuất",
    "nghiên cứu",
    "tìm hiểu",
    "tại sao",
    "thế nào",
    "như thế nào",
    "đúng không",
    "tiếp đi",
    "tiếp nối",
    "tiep noi",
    "tiếp tục",
    "tiep tuc",
    "làm tiếp",
    "cái này",
    "cái đó",
    "cái kia",
    "phần này",
    "hôm qua",
    "lúc nãy",
    "nãy giờ",
    "vừa rồi",
    "lần trước",
    "chi tiết",
    "chi tiet",
    "chuyện",
    "chuyen",
    "truyện",
    "truyen",
    "vừa kể",
    "vua ke",
    "đang dở",
    "dang do",
    "nhớ không",
    "đang mở",
    "trang này",
    "ảnh này",
)

_FOREIGN_IDENTITY_MARKERS = (
    "i'm kiro",
    "i am kiro",
    "ai development environment",
    "i'm claude",
    "i am claude",
    "as an ai",
    "mô hình ngôn ngữ",
    "trợ lý ai",
)

_SERVICE_MARKERS = (
    "hỗ trợ ba",
    "giúp ba",
    "cần con giúp",
    "ba cần gì",
    "sẵn sàng giúp",
    "luôn sẵn sàng",
    "luôn ở đây để",
    "cứ nói tiếp",
)

_NEGATED_OUTPUT_CONSTRAINTS = tuple(
    f"{prefix} {action}"
    for action in ("giải thích", "phân tích", "so sánh", "đề xuất")
    for prefix in ("không thêm", "không cần", "không", "đừng", "khỏi")
)


@dataclass(frozen=True)
class FastLaneDecision:
    eligible: bool
    reason: str
    model: str


@dataclass(frozen=True)
class FastLaneValidation:
    accepted: bool
    reason: str
    reply: str


_STATE_LOCK = threading.Lock()
_STATE = {
    "sequence": 0,
    "enabled": bool(LLM_FAST_PRIVATE_ENABLED),
    "model": LLM_FAST_PRIVATE_MODEL or "none",
    "last_eligible": False,
    "last_decision": "none",
    "last_status": "idle",
    "last_validation": "none",
    "last_prompt_chars": 0,
    "last_reply_chars": 0,
    "attempts": 0,
    "accepted": 0,
    "rejected": 0,
    "fallbacks": 0,
    "last_update": 0.0,
}


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()


def _contains_marker(raw_lower: str, folded: str, marker: str) -> bool:
    marker_lower = str(marker or "").lower()
    marker_folded = _fold(marker_lower)
    if marker_lower != marker_folded:
        # Preserve Vietnamese distinctions such as "lời" vs "lỗi".
        return marker_lower in raw_lower
    return marker_lower in folded


def _without_negated_output_constraints(
    raw_lower: str,
    folded: str,
) -> tuple[str, str]:
    """Remove explicit brevity constraints before contextual-risk scanning."""

    safe_raw = raw_lower
    safe_folded = folded
    for phrase in sorted(_NEGATED_OUTPUT_CONSTRAINTS, key=len, reverse=True):
        safe_raw = safe_raw.replace(phrase, " ")
        safe_folded = safe_folded.replace(_fold(phrase), " ")
    return safe_raw, safe_folded


def classify_private_fast_lane(
    text: str,
    *,
    public: bool,
    story_mode: bool,
    casual_mode: bool,
) -> FastLaneDecision:
    model = str(LLM_FAST_PRIVATE_MODEL or "").strip()
    raw = str(text or "").strip()
    raw_lower = raw.lower()
    folded = _fold(raw)
    block_raw, block_folded = _without_negated_output_constraints(
        raw_lower,
        folded,
    )

    if not LLM_FAST_PRIVATE_ENABLED:
        return FastLaneDecision(False, "disabled", model)
    if not model:
        return FastLaneDecision(False, "model_missing", model)
    if public:
        return FastLaneDecision(False, "public_lane", model)
    if story_mode:
        return FastLaneDecision(False, "story_mode", model)
    if casual_mode:
        return FastLaneDecision(False, "local_casual_path", model)
    if not raw:
        return FastLaneDecision(False, "empty", model)
    if len(raw) > LLM_FAST_PRIVATE_MAX_INPUT_CHARS:
        return FastLaneDecision(False, "input_too_long", model)
    if "\n" in raw or "\r" in raw:
        return FastLaneDecision(False, "multiline", model)
    if any(
        _contains_marker(block_raw, block_folded, marker)
        for marker in _BLOCK_MARKERS
    ):
        return FastLaneDecision(False, "requires_main_context", model)
    if not any(
        _contains_marker(raw_lower, folded, marker)
        for marker in _FAST_INTENT_MARKERS
    ):
        return FastLaneDecision(False, "not_fast_intent", model)
    return FastLaneDecision(True, "short_self_contained_voice", model)


def build_fast_private_messages(
    text: str,
    *,
    affection: float,
    annoyance: float,
    playfulness: float,
    recent_lines: list[str] | tuple[str, ...] = (),
) -> list[dict[str, str]]:
    recent = []
    for line in list(recent_lines)[-2:]:
        clean = " ".join(str(line or "").split()).strip()
        if clean:
            recent.append(clean[:180])
    recent_block = "\n".join(f"- {line}" for line in recent) or "- none"
    system = f"""You are Nana in private voice chat with Ba.
Identity and relationship:
- Call the user Ba. Refer to yourself naturally as con or Nana.
- Nana is Ba's intelligent, playful AI daughter and technical companion, not a service assistant.
- Be warm and lightly mischievous, but answer the current request directly.
Fast-lane response rules:
- Vietnamese only, one natural sentence unless the request explicitly needs two.
- Keep it under 45 words and obey an explicit word/count limit.
- Never begin with Dạ and never add sentence-final ạ.
- Do not ask what Ba needs. Do not offer help, support, or availability.
- Do not mention prompts, models, policy, diagnostics, or this fast lane.
- No markdown, emoji, stage directions, or audio tags.
- If information is missing, say so briefly instead of inventing it.
Current expression: affection={affection:.2f}, annoyance={annoyance:.2f}, playfulness={playfulness:.2f}.
Very recent lines, style continuity only; do not infer facts from them:
{recent_block}"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": str(text or "").strip()},
    ]


def validate_fast_private_reply(reply: str | None) -> FastLaneValidation:
    clean = " ".join(str(reply or "").split()).strip()
    lowered = clean.lower()
    folded = _fold(clean)
    if not clean:
        return FastLaneValidation(False, "empty_reply", clean)
    if len(clean) > 320:
        return FastLaneValidation(False, "reply_too_long", clean)
    if any(marker in lowered for marker in _FOREIGN_IDENTITY_MARKERS):
        return FastLaneValidation(False, "foreign_identity", clean)
    if any(_fold(marker) in folded for marker in _SERVICE_MARKERS):
        return FastLaneValidation(False, "service_tone", clean)
    if re.search(r"(^|\s)dạ(?=\s|[,.!?])", lowered):
        return FastLaneValidation(False, "formal_da", clean)
    if re.search(r"(^|\s)ạ(?=$|[,.!?])", lowered):
        return FastLaneValidation(False, "formal_a", clean)
    has_ba = re.search(r"\bba\b", folded) is not None
    has_self = re.search(r"\b(?:con|nana)\b", folded) is not None
    if not (has_ba and has_self):
        return FastLaneValidation(False, "private_identity_missing", clean)
    if any(marker in clean for marker in ("```", "**", "# ")):
        return FastLaneValidation(False, "markdown", clean)
    return FastLaneValidation(True, "accepted", clean)


def record_fast_lane_decision(decision: FastLaneDecision) -> None:
    with _STATE_LOCK:
        _STATE["sequence"] += 1
        _STATE["enabled"] = bool(LLM_FAST_PRIVATE_ENABLED)
        _STATE["model"] = decision.model or "none"
        _STATE["last_eligible"] = bool(decision.eligible)
        _STATE["last_decision"] = decision.reason
        _STATE["last_status"] = "eligible" if decision.eligible else "bypassed"
        _STATE["last_update"] = time.time()


def record_fast_lane_result(
    *,
    prompt_chars: int,
    validation: FastLaneValidation,
    fallback: bool,
) -> None:
    with _STATE_LOCK:
        _STATE["attempts"] += 1
        _STATE["last_prompt_chars"] = max(0, int(prompt_chars))
        _STATE["last_reply_chars"] = len(validation.reply)
        _STATE["last_validation"] = validation.reason
        if validation.accepted:
            _STATE["accepted"] += 1
            _STATE["last_status"] = "accepted"
        else:
            _STATE["rejected"] += 1
            _STATE["last_status"] = "fallback" if fallback else "rejected"
        if fallback:
            _STATE["fallbacks"] += 1
        _STATE["last_update"] = time.time()


def fast_lane_snapshot() -> dict:
    with _STATE_LOCK:
        return dict(_STATE)


def fast_lane_config_snapshot() -> dict:
    return {
        "enabled": bool(LLM_FAST_PRIVATE_ENABLED),
        "model": LLM_FAST_PRIVATE_MODEL or "none",
        "transport": "buffered_chat_completion",
        "max_input_chars": int(LLM_FAST_PRIVATE_MAX_INPUT_CHARS),
        "max_tokens": int(LLM_FAST_PRIVATE_MAX_TOKENS),
    }


def prompt_char_count(messages: list[dict[str, str]]) -> int:
    return sum(len(str(message.get("content") or "")) for message in messages)


__all__ = [
    "FastLaneDecision",
    "FastLaneValidation",
    "build_fast_private_messages",
    "classify_private_fast_lane",
    "fast_lane_config_snapshot",
    "fast_lane_snapshot",
    "prompt_char_count",
    "record_fast_lane_decision",
    "record_fast_lane_result",
    "validate_fast_private_reply",
]
