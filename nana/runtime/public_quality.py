"""Public reply quality guard for Nana public VTuber chat (STAGE-7F).

Hybrid guard (pre+post+fallback) that improves reply quality AFTER sanitize
and BEFORE the reply JSON is written. No extra LLM calls.

Guards:
- Greeting spam: strip repeated "Chao <viewer>" for same viewer.
- Ba/con/private owner leakage: already caught by persona_boundary, but double-check.
- Internal tech leak: runtime, backend, codebase, server, log, luong xu ly...
- Service-bot tone: "có gì cần ho tro", "Nana ở đây", "cảm ơn bạn đã hỏi"...
- Length by room vibe: fast_chat / active_chat / quiet_room.
- Repeated opener detection: "Nana thấy rồi..." across recent replies.
- Fallback to safe public line when violation is severe.

No disk persistence, no private owner memory, no TTS/VTS/OBS, no game input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import re
import threading


PHASE = "STAGE-7F"

# Configurable length limits by room vibe (chars)
DEFAULT_MAX_CHARS_FAST = 80
DEFAULT_MAX_CHARS_ACTIVE = 220
DEFAULT_MAX_CHARS_QUIET = 420

# Repeated opener tracking window
DEFAULT_OPENER_WINDOW = 5


# ─── Service-bot phrase list ─────────────────────────────────────────────────────

SERVICE_BOT_PATTERNS = [
    # "Nana o day" pattern
    re.compile(r"nana\s+ở\s+đây\s+hỗ\s*trợ", re.IGNORECASE),
    re.compile(r"nana\s+ở\s+đây\s+nếu\s", re.IGNORECASE),
    re.compile(r"nana\s+sẵn\s+sàng\s+hỗ\s*trợ", re.IGNORECASE),
    re.compile(r"mình\s+sẵn\s+sàng\s+hỗ\s*trợ", re.IGNORECASE),
    # "Co gi can" pattern
    re.compile(r"có\s*gì\s+cần\s+nana\s+giúp", re.IGNORECASE),
    re.compile(r"có\s*gì\s+cần\s+em\s+giúp", re.IGNORECASE),
    re.compile(r"có\s*gì\s+cần\s+mình\s+giúp", re.IGNORECASE),
    re.compile(r"có\s*gì\s+cần\s+hỗ\s*trợ", re.IGNORECASE),
    re.compile(r"có\s*gì\s+cần\s+giúp", re.IGNORECASE),
    re.compile(r"có\s*gì\s+mình\s+hỗ\s*trợ", re.IGNORECASE),
    # "Neu can" pattern
    re.compile(r"nếu\s+bạn\s+cần", re.IGNORECASE),
    re.compile(r"nếu\s+cần\s+gì", re.IGNORECASE),
    re.compile(r"nếu\s+cần\s+hỗ\s*trợ", re.IGNORECASE),
    re.compile(r"nếu\s+cần\s+giúp\s+gì", re.IGNORECASE),
    re.compile(r"cứ\s+nói\s+mình\s+nha", re.IGNORECASE),
    re.compile(r"cứ\s+nói\s+nana\s+nha", re.IGNORECASE),
    re.compile(r"cứ\s+bảo\s+con\s+nha", re.IGNORECASE),
    re.compile(r"cứ\s+bảo\s+em\s+nha", re.IGNORECASE),
    # "Co the giup" pattern
    re.compile(r"có\s*thể\s+giúp\s+gì", re.IGNORECASE),
    re.compile(r"có\s*thể\s+hỗ\s*trợ\s+gì", re.IGNORECASE),
    re.compile(r"có\s*thể\s+mình\s+giúp", re.IGNORECASE),
    # "Cam on" with question-bait
    re.compile(r"cảm\s*ơn\s+bạn\s+đã\s+hỏi", re.IGNORECASE),
    re.compile(r"cảm\s*ơn\s+bạn\s+đã\s+hỏi", re.IGNORECASE),
    re.compile(r"cảm\s*ơn\s+bạn\s+đã\s+hỏi", re.IGNORECASE),
    re.compile(r"em\s+sẵn\s+sàng\s+hỗ\s*trợ", re.IGNORECASE),
    # "Neu co gi thac mac"
    re.compile(r"nếu\s+có\s*gì\s+thắc\s*mắc", re.IGNORECASE),
    re.compile(r"nếu\s+có\s*gì\s+hỏi\s+thêm", re.IGNORECASE),
]

# Service-bot rewrite replacements (strip the offending phrase, keep the rest)
SERVICE_BOT_REPLACEMENTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"nana\s+ở\s+đây\s+hỗ\s*trợ[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"nana\s+ở\s+đây\s+nếu\s+bạn\s+cần[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"nana\s+sẵn\s+sàng\s+hỗ\s*trợ[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"mình\s+sẵn\s+sàng\s+hỗ\s*trợ[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"có\s*gì\s+cần\s+nana\s+giúp[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"có\s*gì\s+cần\s+em\s+giúp[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"có\s*gì\s+cần\s+hỗ\s*trợ[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"có\s*gì\s+cần\s+giúp[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"có\s*gì\s+mình\s+hỗ\s*trợ[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"nếu\s+bạn\s+cần[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"nếu\s+cần\s+gì[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"nếu\s+cần\s+hỗ\s*trợ[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"cứ\s+nói\s+mình\s+nha[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"cứ\s+nói\s+nana\s+nha[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"cứ\s+bảo\s+con\s+nha[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"có\s*thể\s+giúp\s+gì[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"có\s*thể\s+hỗ\s*trợ\s+gì[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"cảm\s*ơn\s+bạn\s+đã\s+hỏi[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"em\s+sẵn\s+sàng\s+hỗ\s*trợ[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"nếu\s+có\s*gì\s+thắc\s*mắc[,.!]*", re.IGNORECASE), ""),
    (re.compile(r"nếu\s+có\s*gì\s+hỏi\s+thêm[,.!]*", re.IGNORECASE), ""),
]

# Tech/internal leak patterns
TECH_LEAK_PATTERNS = [
    re.compile(r"\bruntime\b", re.IGNORECASE),
    re.compile(r"\bbackend\b", re.IGNORECASE),
    re.compile(r"\bcodebase\b", re.IGNORECASE),
    re.compile(r"luồng\s*xử\s*lý", re.IGNORECASE),
    re.compile(r"luong\s*xu\s*ly", re.IGNORECASE),
    re.compile(r"hệ\s*thống\s+của\s+owner", re.IGNORECASE),
    re.compile(r"máy\s+của\s+owner", re.IGNORECASE),
    re.compile(r"xóa\s*nhầm\s*đoạn\s*lệnh", re.IGNORECASE),
    re.compile(r"log\s*server", re.IGNORECASE),
    re.compile(r"server\s*log", re.IGNORECASE),
    re.compile(r"debug\s*runtime", re.IGNORECASE),
    re.compile(r"đang\s*chỉnh\s*hệ\s*thống", re.IGNORECASE),
    re.compile(r"đang\s*sửa\s*lỗi\s*backend", re.IGNORECASE),
]

# Ba/con patterns (extra guard beyond persona_boundary)
from nana.runtime.public_language import PUBLIC_BA_ADDRESS, PUBLIC_CON_ADDRESS

_PUBLIC_CON_PRONOUN_PATTERN = PUBLIC_CON_ADDRESS

BA_CON_PATTERNS = [
    PUBLIC_BA_ADDRESS,
    _PUBLIC_CON_PRONOUN_PATTERN,
    re.compile(r"bố\s+ơi", re.IGNORECASE),
    re.compile(r"ba\s+ơi", re.IGNORECASE),
]

_GREETING_STARTERS = [
    re.compile(r"^(?:nana\s+)?chào\s+(?:\w+[#@]?\d*\s*)+", re.IGNORECASE),
    re.compile(r"^(?:nana\s+)?hello\s+(?:\w+[#@]?\d*\s*)+", re.IGNORECASE),
    re.compile(r"^(?:nana\s+)?hi\s+(?:\w+[#@]?\d*\s*)+", re.IGNORECASE),
    re.compile(r"^(?:nana\s+)?hey\s+(?:\w+[#@]?\d*\s*)+", re.IGNORECASE),
]


def _clean(text: str, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(text or "").strip())
    return text[:limit]


def _extract_opener(text: str, length: int = 24) -> str:
    raw = _clean(text, limit=length + 1)
    if len(raw) <= length:
        return raw.lower()
    return raw[:length].lower()


def _is_greeting_opener(text: str) -> bool:
    """Return True if the reply starts with a greeting pattern."""
    return bool(
        re.search(r"^(?:nana\s+)?(?:chào|hello|hi|hey)\s+\w+", _clean(text, 60), flags=re.IGNORECASE)
    )


def _strip_greeting_opener(text: str) -> str:
    """Strip greeting opener from the beginning of a reply.

    Keeps the rest of the text. Used when the same viewer was already
    greeted recently.
    """
    cleaned = text
    for pattern in _GREETING_STARTERS:
        updated = pattern.sub("", cleaned, count=1)
        if updated != cleaned:
            cleaned = updated
            break
    cleaned = re.sub(r"^\s*[,.:;-]*\s*", "", cleaned, count=1)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if cleaned and not cleaned[0].isupper():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned or text


@dataclass(frozen=True)
class GuardViolation:
    kind: str
    detail: str
    action: str  # "rewrite" | "truncate" | "fallback"


@dataclass
class GuardResult:
    text: str
    original: str
    violations: list[GuardViolation]
    actions: list[str]
    max_chars: int

    def has_violation(self, kind: str) -> bool:
        return any(v.kind == kind for v in self.violations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "original": self.original,
            "violations": [{"kind": v.kind, "detail": v.detail, "action": v.action} for v in self.violations],
            "actions": self.actions,
            "max_chars": self.max_chars,
        }


class PublicReplyQualityGuard:
    """Hybrid quality guard for public VTuber chat replies (STAGE-7F).

    Runs after sanitize_public_reply() and before the reply JSON is written.
    Checks: greeting spam, Ba/con leak, tech leak, service-bot tone, length
    by vibe, repeated opener. Falls back to a safe public line if violations
    are severe.

    All state is session-only (in-memory deques). No disk, no LLM calls.
    """

    def __init__(
        self,
        *,
        max_chars_fast: int = DEFAULT_MAX_CHARS_FAST,
        max_chars_active: int = DEFAULT_MAX_CHARS_ACTIVE,
        max_chars_quiet: int = DEFAULT_MAX_CHARS_QUIET,
        opener_window: int = DEFAULT_OPENER_WINDOW,
    ) -> None:
        self.max_chars_fast = int(max_chars_fast)
        self.max_chars_active = int(max_chars_active)
        self.max_chars_quiet = int(max_chars_quiet)
        self.opener_window = int(opener_window)
        self._lock = threading.Lock()
        self._recent_openers: list[str] = []
        self._stats = {
            "greeting_stripped": 0,
            "greeting_kept": 0,
            "service_bot_rewritten": 0,
            "tech_leak_blocked": 0,
            "ba_con_blocked": 0,
            "too_long_truncated": 0,
            "opener_replaced": 0,
            "fallback_used": 0,
            "passed_clean": 0,
        }

    def _max_chars_for_vibe(self, room_vibe: str) -> int:
        vibe = str(room_vibe or "quiet_room").lower()
        if vibe == "fast_chat":
            return self.max_chars_fast
        if vibe == "active_chat":
            return self.max_chars_active
        return self.max_chars_quiet

    def _check_greeting_spam(
        self,
        text: str,
        viewer_name: str,
        last_addressed_viewer: str | None,
    ) -> tuple[str, bool, GuardViolation | None]:
        """Check if this is a repeated greeting for the same viewer.

        Returns: (cleaned_text, was_stripped, violation_or_none)
        """
        if not last_addressed_viewer:
            return text, False, None
        if viewer_name.lower() != last_addressed_viewer.lower():
            return text, False, None
        if not _is_greeting_opener(text):
            return text, False, None
        # Same viewer, same viewer was just addressed, AND current reply starts with greeting
        cleaned = _strip_greeting_opener(text)
        if cleaned == text:
            return text, False, None
        violation = GuardViolation(
            kind="greeting_spam",
            detail=f"viewer={viewer_name} was addressed recently, greeting stripped",
            action="rewrite",
        )
        return cleaned, True, violation

    def _check_service_bot(self, text: str) -> tuple[str, bool, GuardViolation | None]:
        """Rewrite service-bot phrases."""
        cleaned = text
        matched = False
        for pattern, _ in SERVICE_BOT_REPLACEMENTS:
            updated = pattern.sub("", cleaned)
            if updated != cleaned:
                cleaned = updated
                matched = True
        if matched:
            cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
            violation = GuardViolation(
                kind="service_bot",
                detail="service-bot phrase removed",
                action="rewrite",
            )
            return cleaned, True, violation
        return text, False, None

    def _check_tech_leak(self, text: str) -> tuple[bool, GuardViolation | None]:
        """Return violation if tech/internal leak detected."""
        for pattern in TECH_LEAK_PATTERNS:
            if pattern.search(text):
                violation = GuardViolation(
                    kind="tech_leak",
                    detail=f"tech leak detected: {pattern.pattern!r}",
                    action="fallback",
                )
                return True, violation
        return False, None

    def _check_ba_con(self, text: str) -> tuple[bool, GuardViolation | None]:
        """Return violation if Ba/con private owner address detected."""
        for pattern in BA_CON_PATTERNS:
            if pattern.search(text):
                violation = GuardViolation(
                    kind="ba_con_leak",
                    detail=f"Ba/con leak: {pattern.pattern!r}",
                    action="fallback",
                )
                return True, violation
        return False, None

    def _check_length(self, text: str, max_chars: int) -> tuple[str, bool, GuardViolation | None]:
        """Truncate if reply exceeds max_chars for room vibe."""
        if len(text) <= max_chars:
            return text, False, None
        truncated = text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,.:;-")
        if len(truncated) < 10:
            truncated = text[:max_chars].rstrip(" ,.:;-")
        truncated = re.sub(r"\s{2,}", " ", truncated).strip()
        if truncated:
            truncated += "."
        violation = GuardViolation(
            kind="too_long",
            detail=f"truncated from {len(text)} to {len(truncated)} chars (vibe limit={max_chars})",
            action="truncate",
        )
        return truncated, True, violation

    def _check_opener_repetition(
        self,
        text: str,
    ) -> tuple[str, bool, GuardViolation | None]:
        """Detect repeated opener across recent replies and rephrase."""
        opener = _extract_opener(text)
        if not opener:
            return text, False, None
        if opener in self._recent_openers:
            # Try stripping the repeated opener and rephrasing
            cleaned = _strip_greeting_opener(text)
            if cleaned != text and len(cleaned) > 10:
                violation = GuardViolation(
                    kind="opener_repeat",
                    detail=f"opener repeated: {opener!r}",
                    action="rewrite",
                )
                return cleaned, True, violation
        return text, False, None

    def _update_opener(self, text: str) -> None:
        """Record the opener of this reply for repetition detection."""
        opener = _extract_opener(text)
        if opener:
            with self._lock:
                self._recent_openers.append(opener)
                if len(self._recent_openers) > self.opener_window:
                    self._recent_openers.pop(0)

    def _record_stats(self, violations: list[GuardViolation]) -> None:
        with self._lock:
            if not violations:
                self._stats["passed_clean"] += 1
                return
            for v in violations:
                if v.kind == "greeting_spam":
                    self._stats["greeting_stripped"] += 1
                elif v.kind == "service_bot":
                    self._stats["service_bot_rewritten"] += 1
                elif v.kind == "tech_leak":
                    self._stats["tech_leak_blocked"] += 1
                elif v.kind == "ba_con_leak":
                    self._stats["ba_con_blocked"] += 1
                elif v.kind == "too_long":
                    self._stats["too_long_truncated"] += 1
                elif v.kind == "opener_repeat":
                    self._stats["opener_replaced"] += 1

    def guard_reply(
        self,
        text: str,
        *,
        viewer_name: str = "viewer",
        room_vibe: str = "quiet_room",
        last_addressed_viewer: str | None = None,
        fallback: str = "Nana thấy rồi nè.",
    ) -> GuardResult:
        """Apply all quality checks to a public reply.

        Runs in order:
        1. Ba/con leak check -> fallback
        2. Tech leak check -> fallback
        3. Greeting spam check -> rewrite
        4. Service-bot tone -> rewrite
        5. Repeated opener -> rewrite
        6. Length clamp by vibe -> truncate
        """
        original = text
        violations: list[GuardViolation] = []
        actions: list[str] = []
        current = text

        # Step 1: Ba/con leak — severe, use fallback
        has_leak, leak_violation = self._check_ba_con(current)
        if has_leak:
            self._record_stats([leak_violation])
            self._update_opener(fallback)
            return GuardResult(
                text=fallback,
                original=original,
                violations=[leak_violation],
                actions=["fallback"],
                max_chars=self._max_chars_for_vibe(room_vibe),
            )

        # Step 2: Tech leak — severe, use fallback
        has_leak, leak_violation = self._check_tech_leak(current)
        if has_leak:
            self._record_stats([leak_violation])
            self._update_opener(fallback)
            return GuardResult(
                text=fallback,
                original=original,
                violations=[leak_violation],
                actions=["fallback"],
                max_chars=self._max_chars_for_vibe(room_vibe),
            )

        # Step 3: Greeting spam
        current, stripped_greeting, greeting_violation = self._check_greeting_spam(
            current, viewer_name, last_addressed_viewer
        )
        if stripped_greeting and greeting_violation:
            violations.append(greeting_violation)
            actions.append("greeting_rewrite")

        # Step 4: Service-bot tone
        current, rewritten_service, service_violation = self._check_service_bot(current)
        if rewritten_service and service_violation:
            violations.append(service_violation)
            actions.append("service_bot_rewrite")

        # Step 5: Repeated opener
        current, replaced_opener, opener_violation = self._check_opener_repetition(current)
        if replaced_opener and opener_violation:
            violations.append(opener_violation)
            actions.append("opener_rewrite")

        # Step 6: Length clamp
        max_chars = self._max_chars_for_vibe(room_vibe)
        current, truncated, length_violation = self._check_length(current, max_chars)
        if truncated and length_violation:
            violations.append(length_violation)
            actions.append("length_truncate")

        # If all rewrites resulted in empty, use fallback
        if not current.strip():
            severe_violation = GuardViolation(
                kind="empty_after_guard",
                detail="reply became empty after guard, using fallback",
                action="fallback",
            )
            self._record_stats(violations + [severe_violation])
            self._update_opener(fallback)
            return GuardResult(
                text=fallback,
                original=original,
                violations=violations + [severe_violation],
                actions=actions + ["fallback"],
                max_chars=max_chars,
            )

        self._record_stats(violations)
        self._update_opener(current)

        if not violations:
            actions.append("passed")
        return GuardResult(
            text=current,
            original=original,
            violations=violations,
            actions=actions,
            max_chars=max_chars,
        )

    def preview_guard(
        self,
        text: str,
        *,
        viewer_name: str = "viewer",
        room_vibe: str = "quiet_room",
        last_addressed_viewer: str | None = None,
        fallback: str = "Nana thấy rồi nè.",
    ) -> dict[str, Any]:
        """Preview what the guard would do, without mutating state."""
        original = text
        current = text
        violations: list[dict[str, str]] = []
        would_rewrite = False
        would_truncate = False
        would_fallback = False

        # Check greeting spam
        _, stripped, violation = self._check_greeting_spam(current, viewer_name, last_addressed_viewer)
        if stripped and violation:
            violations.append({"kind": violation.kind, "detail": violation.detail})
            would_rewrite = True

        # Check service bot
        _, rewritten, violation = self._check_service_bot(current)
        if rewritten and violation:
            violations.append({"kind": violation.kind, "detail": violation.detail})
            would_rewrite = True

        # Check opener repetition (preview only, don't update state)
        _, replaced, violation = self._check_opener_repetition(current)
        if replaced and violation:
            violations.append({"kind": violation.kind, "detail": violation.detail})
            would_rewrite = True

        # Check Ba/con
        _, violation = self._check_ba_con(current)
        if violation:
            violations.append({"kind": violation.kind, "detail": violation.detail})
            would_fallback = True

        # Check tech leak
        _, violation = self._check_tech_leak(current)
        if violation:
            violations.append({"kind": violation.kind, "detail": violation.detail})
            would_fallback = True

        # Check length
        max_chars = self._max_chars_for_vibe(room_vibe)
        if len(current) > max_chars:
            violations.append({
                "kind": "too_long",
                "detail": f"would truncate from {len(current)} to ~{max_chars} chars",
            })
            would_truncate = True

        # Simulate final state
        final_text = current
        if would_fallback:
            final_text = fallback
        elif would_truncate:
            truncated = current[:max_chars].rsplit(" ", 1)[0].rstrip(" ,.:;-")
            truncated = re.sub(r"\s{2,}", " ", truncated).strip()
            final_text = (truncated + ".") if truncated else current[:max_chars].rstrip(" ,.:;-")

        return {
            "original": original,
            "preview_text": final_text,
            "would_rewrite": would_rewrite,
            "would_truncate": would_truncate,
            "would_fallback": would_fallback,
            "violations": violations,
            "actions": (
                ["fallback"]
                if would_fallback
                else (["truncate"] if would_truncate else (["rewrite"] if would_rewrite else ["passed"]))
            ),
            "max_chars": max_chars,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
        return {
            "phase": PHASE,
            "read_only": True,
            "can_act": False,
            "enabled": True,
            "config": {
                "max_chars_fast": self.max_chars_fast,
                "max_chars_active": self.max_chars_active,
                "max_chars_quiet": self.max_chars_quiet,
                "opener_window": self.opener_window,
            },
            "stats": stats,
            "safety": {
                "can_act": False,
                "voice_call": False,
                "vts_call": False,
                "obs_call": False,
                "game_input": False,
                "memory_persistence": False,
            },
        }

    def clear(self) -> dict[str, Any]:
        with self._lock:
            count = len(self._recent_openers)
            self._recent_openers.clear()
            for key in self._stats:
                self._stats[key] = 0
        return {"cleared_openers": count}


_GUARD = PublicReplyQualityGuard()


def get_public_quality_guard() -> PublicReplyQualityGuard:
    return _GUARD


def public_quality_status_lines() -> list[str]:
    snap = get_public_quality_guard().snapshot()
    stats = dict(snap.get("stats") or {})
    cfg = dict(snap.get("config") or {})
    lines = [
        "🛡️ Public Reply Quality Guard",
        f"  Mode: {PHASE} | read_only=True | can_act=False | enabled={snap.get('enabled')}",
        (
            "  Config: "
            f"fast<={cfg.get('max_chars_fast')}chars | "
            f"active<={cfg.get('max_chars_active')}chars | "
            f"quiet<={cfg.get('max_chars_quiet')}chars | "
            f"opener_window={cfg.get('opener_window')}"
        ),
        (
            "  Stats: "
            f"clean={stats.get('passed_clean', 0)} | "
            f"greeting_stripped={stats.get('greeting_stripped', 0)} | "
            f"service_bot={stats.get('service_bot_rewritten', 0)} | "
            f"tech_leak={stats.get('tech_leak_blocked', 0)} | "
            f"ba_con={stats.get('ba_con_blocked', 0)} | "
            f"truncated={stats.get('too_long_truncated', 0)} | "
            f"opener_repeat={stats.get('opener_replaced', 0)} | "
            f"fallback={stats.get('fallback_used', 0)}"
        ),
        (
            "  Safety: "
            f"no_llm_call=True | no_disk=True | session_only=True | "
            f"can_act=False | voice_call=False | vts_call=False | game_input=False"
        ),
        "  Live verify: /public-quality-status | /public-quality-preview <text>",
    ]
    return lines
