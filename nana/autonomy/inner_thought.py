"""Inner Thought: picks a mode and a line (LLM by default, template fallback).

Phase A3 (template-only) -> Phase A7 (LLM-driven with template fallback).

User directive (2026-06-18):

    "Chặt" sạch luồng phản hồi cũ dựa trên các mẫu câu tĩnh.
    Mọi câu thoại phải được tạo từ LLM dựa trên current_context.

Behavior in Phase A7:

    - LLM path is the DEFAULT for `idle_banter`, `observer_aware`, and
      `stream_host`. Templates are now ONLY a fallback.
    - LLM is wired through `LLMBanter` (see nana.autonomy.llm_banter).
      The static template pools remain on disk and importable for the
      no-LLM path / smoke tests / when llmgate is unavailable.
    - Template cooldowns are still respected on the FALLBACK path so a
      repeated fallback does not spam the same line.
    - A returned `Thought` is structurally the same whether it came
      from LLM or template, so the loop / express code is unchanged.

Cooldown enforcement (per-line "do not fire the same line again
within N seconds") is handled by the inner_thought instance itself,
not by the cadence scheduler. The scheduler only enforces global
and per-mode floors.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from nana.autonomy.templates import (
    idle_lines,
    observer_lines,
    host_lines,
    substitute,
)
from nana.autonomy.llm_banter import get_banter, BanterResult


_MODE_POOLS = {
    "idle_banter":    idle_lines.LINES,
    "observer_aware": observer_lines.LINES,
    "stream_host":    host_lines.LINES,
}


# Source tag for diagnostics and /autonomy-status.
SOURCE_LLM = "llm"
SOURCE_TEMPLATE = "template"
SOURCE_NONE = "none"


@dataclass
class Thought:
    mode: str
    text: str
    cooldown_s: int
    min_silence_s: int
    tags: list
    raw: str
    line_index: int
    # Phase A7: provenance tag. "llm" or "template" or "none".
    source: str = SOURCE_LLM
    # LLM-specific diagnostics (None for template path).
    used_web_context: bool = False
    llm_model: str = ""
    llm_duration_s: float = 0.0


class InnerThought:
    """Picks thoughts, remembering which lines have fired recently.

    The instance owns:
      - per-line "last fired" timestamps
      - per-mode rotation index (so the same mode is not always
        answered with the first line)
      - mood score for substitution

    This class is intentionally pure-Python. No LLM, no network.
    """

    def __init__(self, rng=None, clock=None):
        self._rng = rng or random.Random()
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._last_fired = {}     # (mode, line_index) -> monotonic t
        self._last_mode_index = {
            "idle_banter": -1,
            "observer_aware": -1,
            "stream_host": -1,
        }
        self._mood_score = 0.5
        # Phase A7: track last LLM fire per mode (diagnostics).
        self._last_llm_fired: dict = {}
        # Phase A7: counters for /autonomy-status.
        self.counters = {
            "llm_ok": 0,
            "llm_fallback": 0,
            "template_ok": 0,
        }

    # ---- public API --------------------------------------------------

    def pick(self, mode: str, ctx_dict: Optional[dict] = None,
             web_ctx: Optional[dict] = None) -> Optional[Thought]:
        """Pick a thought for the given mode.

        Tries the LLM first (Phase A7). Falls back to a template line
        only if the LLM is disabled, fails, or returns unusable text.

        Returns None if both paths fail. The loop should treat None
        as 'skip turn'.

        ctx_dict and web_ctx are passed through to the LLM. They are
        optional and only required for the LLM path; the template
        path ignores them.
        """
        # 1) Try LLM first.
        llm_thought = self._pick_llm_internal(mode, ctx_dict or {}, web_ctx or {})
        if llm_thought is not None:
            return llm_thought

        # 2) Fallback to template.
        return self._pick_internal(mode, prefer_ultra_short=False)

    def pick_prefer_ultra_short(self, mode: str, ctx_dict: Optional[dict] = None,
                                 web_ctx: Optional[dict] = None) -> Optional[Thought]:
        """LLM-first with ultra_short boost on the template fallback path."""
        llm_thought = self._pick_llm_internal(mode, ctx_dict or {}, web_ctx or {})
        if llm_thought is not None:
            return llm_thought
        return self._pick_internal(mode, prefer_ultra_short=True)

    def pick_llm(self, mode: str, ctx_dict: Optional[dict] = None,
                 web_ctx: Optional[dict] = None) -> Optional[Thought]:
        """Force the LLM path. Returns None if the LLM cannot produce
        a usable result; does NOT fall back to templates.

        Useful when the caller wants to know whether the LLM itself
        is succeeding (e.g. for /autonomy-status).
        """
        return self._pick_llm_internal(mode, ctx_dict or {}, web_ctx or {})

    def _pick_llm_internal(self, mode: str, ctx_dict: dict, web_ctx: dict) -> Optional[Thought]:
        """Call LLMBanter; convert the result to a Thought.

        LLM thoughts do NOT participate in template per-line cooldowns
        (they are already varied by the model). We still record the
        last LLM fire time so we can rate-limit back-to-back LLM calls
        via the banter's own cache (see llm_banter).
        """
        banter = get_banter(clock=self._clock)
        result: Optional[BanterResult] = banter.generate(
            mode=mode, ctx_dict=ctx_dict, web_ctx=web_ctx,
        )
        if result is None:
            with self._lock:
                self.counters["llm_fallback"] += 1
            return None

        with self._lock:
            self._last_llm_fired[mode] = self._clock()
            self.counters["llm_ok"] += 1

        return Thought(
            mode=mode,
            text=result.text,
            cooldown_s=0,             # LLM does not need per-line cooldown
            min_silence_s=0,
            tags=["llm"],
            raw=result.text,
            line_index=-1,
            source=SOURCE_LLM,
            used_web_context=result.used_web_context,
            llm_model=result.model,
            llm_duration_s=result.duration_s,
        )

    def _pick_internal(self, mode: str, prefer_ultra_short: bool) -> Optional[Thought]:
        pool = _MODE_POOLS.get(mode)
        if pool is None:
            return None

        with self._lock:
            now = self._clock()
            order = list(range(len(pool)))
            self._rng.shuffle(order)
            prev = self._last_mode_index.get(mode, -1)

            def _eligible(idx: int) -> bool:
                if idx == prev:
                    return False
                text, cooldown_s, _min_silence_s, _tags = pool[idx]
                last = self._last_fired.get((mode, idx))
                if last is not None and (now - last) < cooldown_s:
                    return False
                return True

            def _emit(idx: int) -> Thought:
                text, cooldown_s, min_silence_s, tags = pool[idx]
                final = substitute(text, mood_score=self._mood_score)
                self._last_fired[(mode, idx)] = now
                self._last_mode_index[mode] = idx
                self.counters["template_ok"] += 1
                return Thought(
                    mode=mode,
                    text=final,
                    cooldown_s=cooldown_s,
                    min_silence_s=min_silence_s,
                    tags=list(tags),
                    raw=text,
                    line_index=idx,
                    source=SOURCE_TEMPLATE,
                )

            if prefer_ultra_short:
                # Try ultra_short first, in randomized order.
                ultra = [i for i in order if "ultra_short" in pool[i][3]]
                non_ultra = [i for i in order if "ultra_short" not in pool[i][3]]
                for idx in ultra:
                    if _eligible(idx):
                        return _emit(idx)
                for idx in non_ultra:
                    if _eligible(idx):
                        return _emit(idx)
                return None

            # Regular pick: shuffled order, skip prev.
            for idx in order:
                if _eligible(idx):
                    return _emit(idx)
            return None

    def set_mood(self, score: float) -> None:
        with self._lock:
            self._mood_score = max(0.0, min(1.0, score))

    def reset(self) -> None:
        with self._lock:
            self._last_fired.clear()
            for k in self._last_mode_index:
                self._last_mode_index[k] = -1
            self._mood_score = 0.5
            self._last_llm_fired.clear()


def pick_mode(ctx: dict) -> Optional[str]:
    """Module-level helper used by the loop.

    Respects the observer's forced_mode if present, else rotates
    through the three modes. This is a thin shim so the loop does
    not have to know about the InnerThought class for mode selection.
    """
    forced = ctx.get("forced_mode")
    if forced is not None:
        return forced
    return None  # signal to loop: let it decide


__all__ = ["InnerThought", "Thought", "pick_mode"]
