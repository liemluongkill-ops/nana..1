"""STAGE-9T: private voice reply budget.

This layer keeps long private/local replies from flooding ElevenLabs. It does
not rewrite Nana's text reply and does not affect public Discord text output;
it only shapes the text sent to the local TTS queue.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import re
import threading
import time
from typing import Any


PHASE = "STAGE-9T"
FULL_VOICE_POLICY = "full"


def _env_int(name: str, default: int) -> int:
    try:
        return max(80, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_int_clamped(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


CHAT_MAX_CHARS = _env_int("NANA_VOICE_REPLY_MAX_CHARS", 420)
STORY_MAX_CHARS = _env_int("NANA_VOICE_STORY_MAX_CHARS", 560)
MANUAL_MAX_CHARS = _env_int("NANA_VOICE_MANUAL_MAX_CHARS", 560)
LOG_ENABLED = _env_bool("NANA_VOICE_BUDGET_LOG_ENABLED", False)
DEFER_STORY_STREAM_ENABLED = _env_bool("NANA_VOICE_STORY_STREAM_DEFER_ENABLED", True)
STORY_LEAD_PACKETS_ENABLED = _env_bool("NANA_VOICE_STORY_LEAD_PACKETS_ENABLED", False)
FULL_STORY_TAIL_ENABLED = _env_bool("NANA_VOICE_STORY_FULL_TAIL_ENABLED", True)
STORY_LEAD_SEGMENTS = _env_int_clamped("NANA_VOICE_STORY_LEAD_SEGMENTS", 3, minimum=1, maximum=8)
STORY_LEAD_MIN_CHARS = _env_int("NANA_VOICE_STORY_LEAD_MIN_CHARS", 90)
STORY_LEAD_MAX_CHARS = _env_int("NANA_VOICE_STORY_LEAD_MAX_CHARS", 180)

VOICE_CLOSING = " Phần còn lại Nana để chữ thôi, không nhồi voice nữa."


@dataclass(frozen=True)
class VoiceBudgetResult:
    mode: str
    original_text: str
    voice_text: str
    original_chars: int
    voice_chars: int
    omitted_chars: int
    max_chars: int
    changed: bool
    reason: str
    phase: str = PHASE
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False
    api_call: bool = False
    tts_call: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clean(value: Any, limit: int = 4000) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _max_for_mode(mode: str, override: int | None = None) -> int:
    if override is not None:
        return max(80, int(override))
    normalized = str(mode or "chat").strip().lower()
    if normalized == FULL_VOICE_POLICY:
        return 0
    if normalized == "story":
        return STORY_MAX_CHARS
    if normalized == "manual":
        return MANUAL_MAX_CHARS
    return CHAT_MAX_CHARS


def _is_full_voice_policy(mode: str | None) -> bool:
    return str(mode or "").strip().lower() == FULL_VOICE_POLICY


def _trim_at_word(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    raw = str(text or "").strip()
    if len(raw) <= limit:
        return raw
    clipped = raw[:limit].rstrip()
    space_idx = clipped.rfind(" ")
    if space_idx >= max(20, int(limit * 0.55)):
        clipped = clipped[:space_idx].rstrip()
    return clipped.rstrip(" ,;:-")


def _sentence_prefix(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text.strip()
    pieces = [piece.strip() for piece in re.split(r"(?<=[.!?。！？])\s+", text) if piece.strip()]
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}".strip() if current else piece
        if len(candidate) <= limit:
            current = candidate
            continue
        break
    if current:
        return current.strip()
    return _trim_at_word(text, limit)


def _with_closing(prefix: str, max_chars: int) -> str:
    closing = VOICE_CLOSING.strip()
    body_limit = max(20, max_chars - len(closing) - 1)
    body = _sentence_prefix(prefix, body_limit)
    if body and body[-1] not in ".!?。！？":
        body = body.rstrip() + "."
    text = f"{body} {closing}".strip() if body else closing
    if len(text) <= max_chars:
        return text
    return _trim_at_word(text, max_chars)


def shape_voice_reply(text: Any, *, mode: str = "chat", max_chars: int | None = None) -> VoiceBudgetResult:
    ceiling = _max_for_mode(mode, max_chars)
    if _is_full_voice_policy(mode):
        raw = "" if text is None else str(text)
        if not raw:
            return VoiceBudgetResult(
                mode=FULL_VOICE_POLICY,
                original_text="",
                voice_text="",
                original_chars=0,
                voice_chars=0,
                omitted_chars=0,
                max_chars=ceiling,
                changed=False,
                reason="empty",
            )
        return VoiceBudgetResult(
            mode=FULL_VOICE_POLICY,
            original_text=raw,
            voice_text=raw,
            original_chars=len(raw),
            voice_chars=len(raw),
            omitted_chars=0,
            max_chars=ceiling,
            changed=False,
            reason="full_voice_policy",
        )

    cleaned = _clean(text)
    if not cleaned:
        return VoiceBudgetResult(
            mode=str(mode or "chat"),
            original_text="",
            voice_text="",
            original_chars=0,
            voice_chars=0,
            omitted_chars=0,
            max_chars=ceiling,
            changed=False,
            reason="empty",
        )
    if len(cleaned) <= ceiling:
        return VoiceBudgetResult(
            mode=str(mode or "chat"),
            original_text=cleaned,
            voice_text=cleaned,
            original_chars=len(cleaned),
            voice_chars=len(cleaned),
            omitted_chars=0,
            max_chars=ceiling,
            changed=False,
            reason="within_budget",
        )
    prefix = _sentence_prefix(cleaned, max(80, ceiling - len(VOICE_CLOSING) - 1))
    voice_text = _with_closing(prefix, ceiling)
    return VoiceBudgetResult(
        mode=str(mode or "chat"),
        original_text=cleaned,
        voice_text=voice_text,
        original_chars=len(cleaned),
        voice_chars=len(voice_text),
        omitted_chars=max(0, len(cleaned) - len(prefix)),
        max_chars=ceiling,
        changed=True,
        reason="long_reply_truncated_for_voice",
    )


class VoiceTurnBudget:
    def __init__(
        self,
        manager: "VoiceReplyBudget",
        *,
        mode: str = "chat",
        max_chars: int | None = None,
        truncate: bool = True,
    ) -> None:
        self._manager = manager
        self.mode = str(mode or "chat")
        self.truncate = bool(truncate) and not _is_full_voice_policy(self.mode)
        self.max_chars = _max_for_mode(self.mode, max_chars)
        self.body_limit = max(40, self.max_chars - len(VOICE_CLOSING) - 1)
        self.input_chars = 0
        self.output_chars = 0
        self.body_chars = 0
        self.omitted_chars = 0
        self.changed = False
        self.closed = False
        self._finalized = False

    def feed(self, text: Any) -> str:
        raw = str(text or "")
        if not raw.strip():
            return ""
        self.input_chars += len(raw)
        if not self.truncate:
            self.body_chars += len(raw)
            self.output_chars += len(raw)
            return raw
        if self.closed:
            self.changed = True
            self.omitted_chars += len(raw)
            return ""
        if self.body_chars + len(raw) <= self.body_limit:
            self.body_chars += len(raw)
            self.output_chars += len(raw)
            return raw

        remaining = max(0, self.body_limit - self.body_chars)
        prefix = _trim_at_word(raw, remaining)
        closing = VOICE_CLOSING
        if prefix and prefix[-1] not in ".!?。！？":
            prefix = prefix.rstrip() + "."
        spoken = f"{prefix}{closing}" if prefix else closing.strip()
        spoken = _trim_at_word(spoken, max(0, self.max_chars - self.output_chars))
        self.body_chars += len(prefix)
        self.output_chars += len(spoken)
        self.omitted_chars += max(0, len(raw) - len(prefix))
        self.changed = True
        self.closed = True
        return spoken.strip()

    def finalize(self) -> VoiceBudgetResult:
        if self._finalized:
            return self._manager.last_result()
        self._finalized = True
        if not self.truncate:
            reason = "stream_full_voice" if _is_full_voice_policy(self.mode) else "stream_full_tail_voice"
        else:
            reason = "stream_truncated_for_voice" if self.changed else "within_budget"
        result = VoiceBudgetResult(
            mode=self.mode,
            original_text="",
            voice_text="",
            original_chars=self.input_chars,
            voice_chars=self.output_chars,
            omitted_chars=max(0, self.input_chars - self.body_chars),
            max_chars=self.max_chars,
            changed=self.changed,
            reason=reason,
        )
        self._manager.record(result)
        return result


class VoiceReplyBudget:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_result: VoiceBudgetResult | None = None
        self._stats = {
            "checked": 0,
            "passthrough": 0,
            "truncated": 0,
            "stream": 0,
            "empty": 0,
            "omitted_chars": 0,
        }

    def prepare(self, text: Any, *, mode: str = "chat", max_chars: int | None = None) -> VoiceBudgetResult:
        result = shape_voice_reply(text, mode=mode, max_chars=max_chars)
        self.record(result)
        return result

    def begin_turn(
        self,
        *,
        mode: str = "chat",
        max_chars: int | None = None,
        truncate: bool = True,
    ) -> VoiceTurnBudget:
        return VoiceTurnBudget(self, mode=mode, max_chars=max_chars, truncate=truncate)

    def record(self, result: VoiceBudgetResult) -> None:
        with self._lock:
            self._stats["checked"] += 1
            if result.reason == "empty":
                self._stats["empty"] += 1
            elif result.changed:
                self._stats["truncated"] += 1
            else:
                self._stats["passthrough"] += 1
            if result.reason.startswith("stream_"):
                self._stats["stream"] += 1
            self._stats["omitted_chars"] += max(0, int(result.omitted_chars or 0))
            self._last_result = result

    def last_result(self) -> VoiceBudgetResult:
        with self._lock:
            if self._last_result is not None:
                return self._last_result
        return VoiceBudgetResult(
            mode="none",
            original_text="",
            voice_text="",
            original_chars=0,
            voice_chars=0,
            omitted_chars=0,
            max_chars=CHAT_MAX_CHARS,
            changed=False,
            reason="none",
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
            last = self._last_result.to_dict() if self._last_result else None
        return {
            "phase": PHASE,
            "mode": "private-voice-budget",
            "uptime_seconds": max(0.0, time.time() - self._initialized_at),
            "chat_max_chars": CHAT_MAX_CHARS,
            "story_max_chars": STORY_MAX_CHARS,
            "manual_max_chars": MANUAL_MAX_CHARS,
            "stats": stats,
            "last_result": last,
            "read_only": True,
            "can_act": False,
            "memory_write": False,
            "api_call": False,
            "tts_call": False,
        }


_VOICE_REPLY_BUDGET = VoiceReplyBudget()


def get_voice_reply_budget() -> VoiceReplyBudget:
    return _VOICE_REPLY_BUDGET


def voice_budget_log_enabled() -> bool:
    return LOG_ENABLED


def should_defer_stream_voice(mode: str | None) -> bool:
    normalized = str(mode or "").strip().lower()
    return (not STORY_LEAD_PACKETS_ENABLED) and normalized in {"story", FULL_VOICE_POLICY}


def voice_stream_dispatch_config(mode: str | None = "story") -> dict[str, Any]:
    normalized = str(mode or "").strip().lower()
    is_full_delivery = normalized in {"story", FULL_VOICE_POLICY}
    lead_segments = STORY_LEAD_SEGMENTS if is_full_delivery and DEFER_STORY_STREAM_ENABLED else 0
    full_tail = bool(lead_segments and FULL_STORY_TAIL_ENABLED)
    return {
        "mode": "lead_then_tail" if lead_segments else "stream_chunks",
        "lead_segments": lead_segments,
        "lead_min_chars": STORY_LEAD_MIN_CHARS,
        "lead_max_chars": max(STORY_LEAD_MIN_CHARS, STORY_LEAD_MAX_CHARS),
        "tail_packet": bool(lead_segments),
        "tail_full": full_tail,
    }


def story_stream_dispatch_config(mode: str | None = "story") -> dict[str, Any]:
    """Compatibility alias for callers that still use the legacy story name."""
    return voice_stream_dispatch_config(mode)


def voice_budget_status_lines() -> list[str]:
    snap = get_voice_reply_budget().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_result") or {})
    full_dispatch = voice_stream_dispatch_config(FULL_VOICE_POLICY)
    return [
        f"Voice Reply Budget ({PHASE})",
        "  Mode: private-voice-budget | read_only=True | can_act=False | memory_write=False | api_call=False | tts_call=False",
        (
            "  Config: "
            f"chat_max={snap.get('chat_max_chars')} | story_max={snap.get('story_max_chars')} | "
            f"manual_max={snap.get('manual_max_chars')} | full=unlimited"
        ),
        (
            "  Dispatch: "
            f"log={'on' if voice_budget_log_enabled() else 'off'} | "
            "private_default=full | reply_mode_independent=True | "
            f"full_mode={'A_single_utterance' if should_defer_stream_voice(FULL_VOICE_POLICY) else 'B_lead_packets'} | "
            f"full_stream={full_dispatch.get('mode')} | "
            f"single_utterance={'on' if should_defer_stream_voice(FULL_VOICE_POLICY) else 'off'} | "
            f"lead={full_dispatch.get('lead_segments')} | "
            f"tail={'full' if full_dispatch.get('tail_full') else 'budgeted'}"
        ),
        (
            "  Last: "
            f"changed={last.get('changed', False)} | policy={last.get('mode', 'none')} | "
            f"chars={last.get('voice_chars', 0)}/{last.get('original_chars', 0)} | "
            f"reason={last.get('reason', 'none')}"
        ),
        (
            "  Stats: "
            f"checked={stats.get('checked', 0)} | passthrough={stats.get('passthrough', 0)} | "
            f"truncated={stats.get('truncated', 0)} | stream={stats.get('stream', 0)} | "
            f"omitted_chars={stats.get('omitted_chars', 0)}"
        ),
        "  Commands: /voice-budget-status | /voice-budget-preview <text>",
        "  Safety: voice text shaping only | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def voice_budget_preview_lines(payload: str) -> list[str]:
    text = "" if payload is None else str(payload)
    if not text.strip():
        return ["  Usage: /voice-budget-preview <text>"]
    result = shape_voice_reply(text, mode=FULL_VOICE_POLICY)
    return [
        f"Voice Budget Preview ({PHASE})",
        "  Policy: full | source=private_default | reply_mode_independent=True",
        f"  Input chars: {result.original_chars}",
        f"  Voice chars: {result.voice_chars} | max=unlimited | changed={result.changed}",
        f"  Reason: {result.reason}",
        f"  Voice text: {result.voice_text or 'none'}",
        "  Safety: preview only | no LLM | no TTS call | no write | no output action",
    ]
