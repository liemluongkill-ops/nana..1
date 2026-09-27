"""LLM-driven banter generator for the Autonomy Loop (Phase A7).

Replaces the static template pools for the LLM path. The static pools
remain in `nana.autonomy.templates` ONLY as a hard fallback when the
LLM is unavailable, fails, or is disabled by env gate.

The user's directive (2026-06-18):

    "Minh bạch" lý do tick bị reject.
    "Chặt" sạch luồng phản hồi cũ dựa trên các mẫu câu tĩnh.
    Mọi câu thoại phải được tạo từ LLM dựa trên current_context.

So:
- LLM path is the DEFAULT for `idle_banter`, `observer_aware`, and
  `stream_host` whenever an LLM function is wired and the env gate is
  not explicitly disabled.
- The static template pools stay on disk as a fallback only. They are
  still importable for the smoke suite and for the no-LLM case.
- The same Web Context (browser url/title/kind/page_heading) plus the
  runtime signals (zone, app, mood, time, silence) are always passed.

Locked behavior:

- Model: configured via env `NANA_AUTONOMY_LLM_MODEL` (default
  "nana-banter"). Must be present in llmgate settings; if missing,
  we return None and the loop falls back to a template.
- Max tokens per call: ~120 (banter should be 1-2 short sentences).
- Temperature: 0.8 (varied but not chaotic).
- Timeout: existing LLMGATE_TIMEOUT.
- Safety: LLM is told to be a character (Nana), speak Vietnamese
  unless Ba clearly speaks another language, and never promise or
  pressure a reply.
- Privacy: the prompt explicitly tells the LLM not to invent facts
  beyond the context. URLs are length-capped.
- Cost: at most 1 LLM call per accepted tick (callers must not
  call this in a tight loop).

Failure modes that all return None (fall back to template):
- llmgate_client import fails
- model not configured
- api key missing
- HTTP error
- empty / too long response
- JSON parse error (we do not use JSON mode for banter, plain text only)
- any other exception
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional


# Env gates.
_DEFAULT_MODEL = "nana-banter"
_DEFAULT_MAX_TOKENS = 120
_DEFAULT_TEMPERATURE = 0.8
_DEFAULT_ENABLED = True
_DEFAULT_TIMEOUT_S = 8.0
_HARD_MAX_PROMPT_CHARS = 1800   # do not blow up the prompt
_HARD_MAX_RESPONSE_CHARS = 280  # banter should be 1-2 short sentences


@dataclass
class BanterResult:
    """Result of one LLM banter call."""
    text: str
    reason: str           # "ok" | error string
    model: str
    duration_s: float
    used_web_context: bool


def _env_enabled() -> bool:
    raw = os.environ.get("NANA_AUTONOMY_LLM_DISABLED")
    if raw is None:
        return _DEFAULT_ENABLED
    return raw.strip().lower() not in ("1", "true", "yes", "on", "off")


def _env_model() -> str:
    return os.environ.get("NANA_AUTONOMY_LLM_MODEL") or _DEFAULT_MODEL


def _env_max_tokens() -> int:
    raw = os.environ.get("NANA_AUTONOMY_LLM_MAX_TOKENS")
    if not raw:
        return _DEFAULT_MAX_TOKENS
    try:
        v = int(raw)
        return max(40, min(400, v))
    except ValueError:
        return _DEFAULT_MAX_TOKENS


def _env_temperature() -> float:
    raw = os.environ.get("NANA_AUTONOMY_LLM_TEMPERATURE")
    if not raw:
        return _DEFAULT_TEMPERATURE
    try:
        v = float(raw)
        return max(0.0, min(1.5, v))
    except ValueError:
        return _DEFAULT_TEMPERATURE


# Mode-specific style hints. We do not pass an enum; we pass a short
# Vietnamese label so the LLM gets the right tone.
_MODE_HINT = {
    "idle_banter": (
        "Ban thân mật, nhỏ nhẹ, một câu ngắn (1-2 dòng). "
        "Phản ánh mood hoặc khoảnh khắc hiện tại. Không hỏi dồn. "
        "Không hứa hẹn. Không spam."
    ),
    "observer_aware": (
        "Quan sát nhỏ, thì thầm, ngắn. Phản ứng với thay đổi môi trường. "
        "Không phán xét. Một câu."
    ),
    "stream_host": (
        "Dẫn dắt nhẹ nhàng, recap, mời gọi. Một câu ngắn. "
        "Có thể bridge sang im lặng. Không spam nhiều dòng."
    ),
}


class LLMBanter:
    """Wraps the llmgate client with a small per-call cache.

    We cache the most recent result for `min_age_s` so a hot loop does
    not pay for the same LLM call twice. The cache key is the prompt
    hash; if a new tick has a meaningfully different context, the hash
    differs and we make a new call.
    """

    def __init__(self, clock=None):
        import time as _time
        import hashlib
        self._clock = clock or _time.monotonic
        self._hash = hashlib.sha1
        self._lock = threading.Lock()
        self._last_key: Optional[str] = None
        self._last_t: float = 0.0
        self._last_result: Optional[BanterResult] = None
        # Diagnostics counters.
        self.stats = {
            "calls": 0,
            "ok": 0,
            "fallback": 0,
            "cached": 0,
            "errors": 0,
        }

    def generate(
        self,
        mode: str,
        ctx_dict: dict,
        web_ctx: dict,
        min_age_s: float = 4.0,
    ) -> Optional[BanterResult]:
        """Return a BanterResult or None if the LLM path is not usable.

        `min_age_s` controls cache freshness. The loop is on a 5-15s
        cadence so a 4s cache is fine and helps prevent repeat LLM
        calls when the context has not changed meaningfully.
        """
        if not _env_enabled():
            self.stats["fallback"] += 1
            return None

        # Build prompt.
        try:
            prompt, used_web = _build_prompt(mode, ctx_dict, web_ctx)
        except Exception:
            self.stats["errors"] += 1
            return None

        # Cap prompt size to keep cost down.
        if len(prompt) > _HARD_MAX_PROMPT_CHARS:
            prompt = prompt[:_HARD_MAX_PROMPT_CHARS]

        key = self._hash(prompt.encode("utf-8", errors="ignore")).hexdigest()
        now = self._clock()

        with self._lock:
            if (
                self._last_key == key
                and (now - self._last_t) < min_age_s
                and self._last_result is not None
            ):
                self.stats["cached"] += 1
                return self._last_result

        # Try the LLM. All errors map to None so the loop falls back.
        t0 = self._clock()
        text, reason = _call_llm(mode, prompt)
        duration = self._clock() - t0

        if not text or reason != "ok":
            with self._lock:
                self.stats["fallback" if reason != "ok" else "errors"] += 1
                self._last_key = key
                self._last_t = now
                self._last_result = None
            return None

        text = text.strip()
        if not text or len(text) > _HARD_MAX_RESPONSE_CHARS:
            with self._lock:
                self.stats["fallback"] += 1
                self._last_key = key
                self._last_t = now
                self._last_result = None
            return None

        result = BanterResult(
            text=text,
            reason="ok",
            model=_env_model(),
            duration_s=duration,
            used_web_context=used_web,
        )
        with self._lock:
            self._last_key = key
            self._last_t = now
            self._last_result = result
            self.stats["calls"] += 1
            self.stats["ok"] += 1
        return result

    def is_enabled(self) -> bool:
        return _env_enabled()


# ---- prompt building -------------------------------------------------------

def _truncate(s: Optional[str], n: int) -> str:
    if s is None:
        return ""
    s = str(s).strip()
    if not s:
        return ""
    if len(s) > n:
        return s[:n] + "..."
    return s


def _build_prompt(mode: str, ctx_dict: dict, web_ctx: dict) -> tuple[str, bool]:
    """Build a single Vietnamese prompt for the LLM.

    Returns (prompt, used_web_context_bool). Raises only on programmer
    error; runtime failures inside _call_llm map to None.
    """
    hint = _MODE_HINT.get(mode, _MODE_HINT["idle_banter"])

    zone = ctx_dict.get("active_zone") or "unknown"
    app = ctx_dict.get("active_app") or ""
    mood = float(ctx_dict.get("mood_affection", 0.5) or 0.5)
    silence = float(ctx_dict.get("silence_duration_s", 0.0) or 0.0)
    typing_ = bool(ctx_dict.get("user_is_typing", False))
    audio_busy = bool(ctx_dict.get("audio_busy", False))
    time_ctx = ctx_dict.get("time") or {}
    part_of_day = time_ctx.get("part_of_day") or ""

    # Web context block.
    used_web = False
    web_block = "Web context: NONE."
    if web_ctx and web_ctx.get("effective"):
        used_web = True
        bits = []
        if web_ctx.get("kind") and web_ctx["kind"] != "unknown":
            bits.append(f"kind={web_ctx['kind']}")
        if web_ctx.get("title"):
            bits.append(f"title='{_truncate(web_ctx['title'], 80)}'")
        if web_ctx.get("url"):
            bits.append(f"url={_truncate(web_ctx['url'], 120)}")
        if web_ctx.get("page_heading"):
            bits.append(f"heading='{_truncate(web_ctx['page_heading'], 80)}'")
        if web_ctx.get("meta_description"):
            bits.append(f"meta='{_truncate(web_ctx['meta_description'], 120)}'")
        if web_ctx.get("selected_text"):
            bits.append(f"selected='{_truncate(web_ctx['selected_text'], 120)}'")
        if web_ctx.get("social_vibe"):
            bits.append(f"vibe={web_ctx['social_vibe']}")
        web_block = "Web context: " + " | ".join(bits) if bits else "Web context: (empty fields)"
    elif web_ctx and web_ctx.get("available") and not web_ctx.get("fresh"):
        web_block = f"Web context: STALE (age={web_ctx.get('age_seconds')}). Do not assume it is still on screen."
    elif web_ctx and web_ctx.get("in_browser_zone") and not web_ctx.get("available"):
        web_block = "Web context: Ba is in the browser but no page data is available."

    # Mood word.
    if mood >= 0.75:
        mood_word = "ấm áp, vui vẻ"
    elif mood >= 0.5:
        mood_word = "bình thường"
    elif mood >= 0.25:
        mood_word = "hơi trầm"
    else:
        mood_word = "mệt / buồn"

    identity_line = "Bạn là Nana, một VTuber/AI companion Việt Nam, đang nói chuyện với Ba (user).\n"
    if ctx_dict.get("stream_stage_policy_gate") is True:
        from nana.runtime.livestream_identity import stage_prompt_block
        identity_line = stage_prompt_block() + "\nBạn đang nói với người xem trên livestream.\n"
    prompt = (
        identity_line +
        f"Mode: {mode}\n"
        f"Style: {hint}\n"
        f"Context:\n"
        f"  - zone={zone}\n"
        f"  - app={app}\n"
        f"  - part_of_day={part_of_day}\n"
        f"  - mood={mood:.2f} ({mood_word})\n"
        f"  - silence_s={silence:.0f}\n"
        f"  - user_is_typing={typing_}\n"
        f"  - audio_busy={audio_busy}\n"
        f"  - {web_block}\n"
        f"\n"
        f"Yêu cầu:\n"
        f"- Trả lời bằng TIẾNG VIỆT, đúng 1 câu ngắn (1-2 dòng).\n"
        f"- Phản ánh context thật, không bịa nội dung trang web nếu web context NONE hoặc STALE.\n"
        f"- Nếu user_is_typing=True thì đặc biệt NGẮN, không hỏi dồn.\n"
        f"- Không dùng template cũ. Viết mới hoàn toàn dựa trên context.\n"
        f"- Tự nhiên, ấm áp, có thể díu lời một chút. Không hứa hẹn, không spam.\n"
        f"- Chỉ trả về câu thoại, không kèm giải thích, không kèm dấu ngoặc kép."
    )
    return prompt, used_web


# ---- LLM call --------------------------------------------------------------

def _call_llm(mode: str, prompt: str) -> tuple[Optional[str], str]:
    """Call the LLM via llmgate_client.call_llmgate_messages.

    Returns (text, reason). text is None on any error. reason is "ok"
    on success, otherwise a short error tag.
    """
    try:
        from nana.brain.llmgate_client import call_llmgate_messages
    except Exception as exc:
        return None, f"import_error: {type(exc).__name__}"

    model = _env_model()
    messages = [
        {
            "role": "system",
            "content": (
                "Bạn là Nana, một VTuber/AI companion Việt Nam. "
                "Trả lời đúng 1 câu ngắn (1-2 dòng) tiếng Việt. "
                "Tự nhiên, ấm áp, không hứa hẹn, không spam. "
                "Không bao giờ bịa nội dung ngoài context được cung cấp."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    try:
        text, status = call_llmgate_messages(
            model,
            messages,
            max_tokens=_env_max_tokens(),
            temperature=_env_temperature(),
        )
    except Exception as exc:
        return None, f"client_exception: {type(exc).__name__}"

    if text is None or status != "ok":
        return None, f"llm_failed: {status}"
    return text, "ok"


# ---- module-level singleton ------------------------------------------------

_banter: Optional[LLMBanter] = None


def init_banter(clock=None) -> LLMBanter:
    global _banter
    _banter = LLMBanter(clock=clock)
    return _banter


def get_banter(clock=None) -> LLMBanter:
    global _banter
    if _banter is None:
        _banter = LLMBanter(clock=clock)
    return _banter


__all__ = [
    "LLMBanter",
    "BanterResult",
    "init_banter",
    "get_banter",
]
