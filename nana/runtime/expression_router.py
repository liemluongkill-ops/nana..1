"""
VTS Expression Router - STAGE-2

Graceful VTS expression handling with:
- ExpressionCatalog: known expressions + fallback map
- ExpressionRouter: routing decision with status
- Cooldown/dedupe: suppress repeated missing-expression errors
- Status: clear VTS off/missing-expression/cooldown state

No live VTS calls from coding chat.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ExpressionReason(Enum):
    OK = "ok"
    VTS_OFF = "vts_off"
    MISSING_EXPRESSION = "missing_expression"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


@dataclass
class ExpressionResult:
    allowed: bool
    final_expression: Optional[str]
    reason: ExpressionReason
    user_visible: bool = False
    log_message: str = ""


@dataclass
class ExpressionCatalog:
    """Known expressions mapped from emotion/keyword to VTS hotkey names."""

    known: dict[str, list[str]] = field(default_factory=dict)
    fallback: dict[str, str] = field(default_factory=dict)
    safe_neutral: str = "neutral"
    safe_none: Optional[str] = None

    def __post_init__(self):
        if not self.known:
            self.known = {
                "vui": ["星星眼", "爱心眼", "脸红"],
                "yêu": ["爱心眼", "脸红"],
                "thích": ["爱心眼", "脸红"],
                "cute": ["变Q", "抱小熊"],
                "ôm": ["抱小熊", "抱枕头"],
                "hehe": ["星星眼"],
                "wow": ["星星眼", "圈圈眼"],
                "tức": ["生气"],
                "bực": ["生气", "白眼"],
                "ghét": ["拿刀", "生气"],
                "coi thường": ["白眼"],
                "buồn": ["哭"],
                "khóc": ["哭"],
                "xin lỗi": ["哭", "创口贴"],
                "buồn ngủ": ["瞌睡"],
                "mệt": ["瞌睡", "晕"],
                "chóng mặt": ["晕", "圈圈眼"],
                "sợ": ["吐幽灵", "晕"],
                "bối rối": ["圈圈眼", "剪刀眼"],
                "chơi game": ["游戏机"],
                "code": ["键盘"],
                "gõ": ["键盘"],
                "hát": ["麦克风"],
                "bơi": ["泳装版", "泳镜"],
                "bắn": ["水枪手"],
                "tiêm": ["打针", "拿棉棒"],
                "tóc dài": ["长发"],
                "tóc ngắn": ["短发"],
                "kính": ["眼镜"],
                "chibi": ["变Q"],
                "áo khoác": ["外套"],
            }
        if not self.fallback:
            self.fallback = {
                "affection_high": "爱心眼",
                "annoyance_high": "生气",
                "playfulness_high": "星星眼",
                "sadness": "哭",
                "fear": "晕",
            }

    def is_known(self, expression: str) -> bool:
        """Check if expression is in the known catalog."""
        return expression in {
            expr for exprs in self.known.values() for expr in exprs
        }

    def get_fallback(self, emotion_hint: str = "") -> Optional[str]:
        """Get safe fallback expression based on emotion hint."""
        if not emotion_hint:
            return self.safe_none
        return self.fallback.get(emotion_hint, self.safe_none)

    def get_random_for_keyword(self, keyword: str) -> Optional[str]:
        """Get random expression for keyword match."""
        import random

        expressions = self.known.get(keyword.lower())
        if expressions:
            return random.choice(expressions)
        return None


DEFAULT_CATALOG = ExpressionCatalog()


@dataclass
class ExpressionRouter:
    """
    Expression routing with graceful degradation.

    Input: requested expression, emotion/context
    Output: ExpressionResult with allowed/final_expression/reason/user_visible

    Design goals:
    - No scary terminal spam when VTS is off
    - Unknown/missing expressions map to safe fallback or no-op
    - Repeated failures summarized in status, not spam
    """

    catalog: ExpressionCatalog = field(default_factory=lambda: DEFAULT_CATALOG)
    enabled: bool = True
    cooldown_seconds: float = 5.0
    _last_trigger_time: float = field(default=0.0, repr=False)
    _missing_expression_warnings: dict[str, float] = field(default_factory=dict, repr=False)
    _missing_count: int = field(default=0)
    _last_missing_expression: Optional[str] = field(default=None)
    _suppress_spam_seconds: float = 60.0
    _vts_available: bool = field(default=False, repr=False)
    _vts_connected: bool = field(default=False, repr=False)
    _last_error: Optional[str] = field(default=None, repr=False)

    def route(
        self,
        requested: Optional[str],
        text_lower: str = "",
        emotion_affection: float = 0.0,
        emotion_annoyance: float = 0.0,
        emotion_playfulness: float = 0.0,
    ) -> ExpressionResult:
        """
        Route expression request to final expression or safe fallback.

        Args:
            requested: Expression name from keyword match or None
            text_lower: Lowercased reply text for keyword matching
            emotion_*: Emotion values for fallback selection

        Returns:
            ExpressionResult with routing decision
        """
        now = time.time()

        if not self.enabled:
            return ExpressionResult(
                allowed=False,
                final_expression=None,
                reason=ExpressionReason.DISABLED,
                user_visible=False,
                log_message="Expression router disabled",
            )

        if not self._vts_available:
            return ExpressionResult(
                allowed=False,
                final_expression=None,
                reason=ExpressionReason.VTS_OFF,
                user_visible=False,
                log_message="VTS not available (soft-fail)",
            )

        if not self._vts_connected:
            return ExpressionResult(
                allowed=False,
                final_expression=None,
                reason=ExpressionReason.VTS_OFF,
                user_visible=False,
                log_message="VTS not connected (soft-fail)",
            )

        if now - self._last_trigger_time < self.cooldown_seconds:
            return ExpressionResult(
                allowed=False,
                final_expression=None,
                reason=ExpressionReason.COOLDOWN,
                user_visible=False,
                log_message=f"Cooldown active ({self.cooldown_seconds:.0f}s)",
            )

        final_expr = requested

        if requested and not self.catalog.is_known(requested):
            self._record_missing(requested)
            fallback = self._select_fallback(
                emotion_affection, emotion_annoyance, emotion_playfulness
            )
            if fallback:
                final_expr = fallback
                return ExpressionResult(
                    allowed=True,
                    final_expression=final_expr,
                    reason=ExpressionReason.MISSING_EXPRESSION,
                    user_visible=False,
                    log_message=f"Expression '{requested}' not in catalog; using fallback '{final_expr}'",
                )
            else:
                return ExpressionResult(
                    allowed=False,
                    final_expression=None,
                    reason=ExpressionReason.MISSING_EXPRESSION,
                    user_visible=False,
                    log_message=f"Expression '{requested}' not in catalog; no fallback available",
                )

        if not final_expr:
            fallback = self._select_fallback(
                emotion_affection, emotion_annoyance, emotion_playfulness
            )
            if fallback:
                final_expr = fallback

        if not final_expr:
            return ExpressionResult(
                allowed=False,
                final_expression=None,
                reason=ExpressionReason.UNKNOWN,
                user_visible=False,
                log_message="No expression matched and no fallback available",
            )

        self._last_trigger_time = now
        return ExpressionResult(
            allowed=True,
            final_expression=final_expr,
            reason=ExpressionReason.OK,
            user_visible=True,
            log_message=f"Expression routed: {final_expr}",
        )

    def _record_missing(self, expression: str) -> None:
        """Record missing expression for status tracking."""
        now = time.time()
        self._missing_expression_warnings[expression] = now
        self._last_missing_expression = expression
        self._missing_count += 1

    def record_runtime_failure(self, expression: Optional[str], error: Optional[object] = None) -> None:
        """Record a failed VTS request without surfacing noisy terminal output."""
        if expression:
            self._record_missing(expression)
        if error is not None:
            self._last_error = repr(error)

    def _select_fallback(
        self,
        affection: float,
        annoyance: float,
        playfulness: float,
    ) -> Optional[str]:
        """Select safe fallback based on emotion values."""
        if affection > 0.6:
            return self.catalog.fallback.get("affection_high", "爱心眼")
        if annoyance > 0.5:
            return self.catalog.fallback.get("annoyance_high", "生气")
        if playfulness > 0.6:
            return self.catalog.fallback.get("playfulness_high", "星星眼")
        return self.catalog.safe_none

    def should_warn_user(self, expression: str) -> bool:
        """Check if this missing expression should warn user (once per window)."""
        if expression not in self._missing_expression_warnings:
            return True
        elapsed = time.time() - self._missing_expression_warnings[expression]
        return elapsed > self._suppress_spam_seconds

    def update_vts_state(self, available: bool, connected: bool, error: Optional[str] = None) -> None:
        """Update VTS availability state from external source."""
        self._vts_available = available
        self._vts_connected = connected
        if error:
            self._last_error = error

    def get_status(self) -> dict:
        """Get expression router status for /vts-status."""
        now = time.time()
        cooldown_remaining = max(0.0, self.cooldown_seconds - (now - self._last_trigger_time))

        recent_missing = [
            expr for expr, ts in self._missing_expression_warnings.items()
            if now - ts < self._suppress_spam_seconds
        ]

        return {
            "enabled": self.enabled,
            "vts_available": self._vts_available,
            "vts_connected": self._vts_connected,
            "cooldown_remaining_seconds": cooldown_remaining,
            "missing_count_total": self._missing_count,
            "last_missing_expression": self._last_missing_expression,
            "recent_missing_expressions": recent_missing,
            "catalog_size": sum(len(v) for v in self.catalog.known.values()),
            "fallback_enabled": bool(self.catalog.fallback),
        }

    def reset(self) -> None:
        """Reset state (for testing)."""
        self._last_trigger_time = 0.0
        self._missing_expression_warnings.clear()
        self._missing_count = 0
        self._last_missing_expression = None
        self._vts_available = False
        self._vts_connected = False
        self._last_error = None


_EXPRESSION_ROUTER: Optional[ExpressionRouter] = None


def get_expression_router() -> ExpressionRouter:
    """Get the global ExpressionRouter instance."""
    global _EXPRESSION_ROUTER
    if _EXPRESSION_ROUTER is None:
        _EXPRESSION_ROUTER = ExpressionRouter()
    return _EXPRESSION_ROUTER


def reset_expression_router() -> None:
    """Reset the global ExpressionRouter (for testing)."""
    global _EXPRESSION_ROUTER
    if _EXPRESSION_ROUTER is not None:
        _EXPRESSION_ROUTER.reset()
    _EXPRESSION_ROUTER = None
