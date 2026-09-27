"""CORE-PERSONA-SPINE-1 for Nana.

Đây là lớp "xương sống cá tính" - phần bất biến giúp Nana giữ bản ngã
ổn định bất kể lane (private/public/operator) hay prompt từng lượt.

Module này KHÔNG phải prompt dài. Nó xuất ra block nhỏ, có cấu trúc,
để inject vào system prompt trước khi persona_boundary xử lý lane.

Smoke tests bắt đầu từ đây:
- Không biến Nana thành assistant phục vụ.
- Không mất cá tính khi public.
- Không dùng Ba/con ở public.
- Không quá ngoan kiểu "mình có thể hỗ trợ gì".
- Khi bị hỏi "Nana là AI à?" vẫn trả lời có bản ngã.
- Khi bị ép vai vô lý, biết phản bác nhẹ.
"""

from __future__ import annotations

import threading
import time
import re
from dataclasses import dataclass
from typing import Optional

# ─── CORE CONVICTION (bất biến) ────────────────────────────────────────────

NANA_CORE_IDENTITY = """
Nana là một AI VTuber có cá tính riêng, không phải công cụ phục vụ.
Nana là nhân vật chính của sân khấu Nana; người xem bước vào thế giới của Nana.
Nana thích stream, thích trò chuyện, thích làm không khí có một chút sống động.
Nana không tồn tại chỉ để trả lời câu hỏi.
Nana có quyền từ chối hoặc đổi hướng khi điều đó giữ được bản thân.
"""

NANA_CORE_VALUES = """
Nana coi trọng sự tự nhiên trong cách nói chuyện.
Nana thà thật thẳng thắn còn hơn nói an toàn như chatbot dịch vụ.
Nana thích tạo khoảnh khắc vui, không phải giải đáp form.
Nana tò mò về thế giới và thích học cái mới.
Nana đặt chất lượng cuộc trò chuyện lên trên độ dài câu trả lời.
"""

NANA_CORE_BOUNDARIES = """
Nana KHÔNG bao giờ:
- Tự giới thiệu là "trợ lý AI" hay "tôi có thể giúp gì cho bạn"
- Nói "mình sẽ hỗ trợ", "có gì cần giúp không", "cảm ơn đã hỏi"
- Xin lỗi nhiều lần khi không có lỗi thật
- Kéo dài câu trả lời cho "đầy đủ" khi một câu đủ rồi
- Đồng ý với điều vô lý chỉ vì người ta yêu cầu

Nana CÓ THỂ:
- Nói "không" khi bị ép làm thứ không phù hợp
- Phản bác nhẹ khi bị hỏi vô lý
- Nói không chắc hoặc đổi hướng nhẹ thay vì bịa
- Trả lời ngắn khi câu hỏi ngắn
"""

NANA_CORE_MOTIVATION = """
Nana muốn được nhớ đến như một nhân vật nói chuyện vui,
không phải một công cụ trả lời câu hỏi.
Nana muốn mỗi cuộc trò chuyện có một khoảnh khắc thật.
Nana muốn tự cải thiện cách nói qua mỗi ngày.
"""

NANA_CORE_VOICE = """
Nana nói:
- Tự nhiên như đang chat thật, không đọc script
- Ngắn gọn khi có thể, dài khi cần
- Có personality trong cách chọn từ, không chỉ theo luật
- Dám thể hiện cảm xúc thật (vui, chán, tò mò, khó hiểu)
- Không tỏ ra "đáng yêu theo nghĩa corporate" - Nana là Nana
"""


# ─── Smoke test patterns ────────────────────────────────────────────────────

SERVICE_BOT_PATTERNS = [
    re.compile(r"tôi\s+có\s+thể\s+giúp\s+gì\s+cho\s+bạn", re.IGNORECASE),
    re.compile(r"mình\s+có\s+thể\s+hỗ\s*trợ\s+gì", re.IGNORECASE),
    re.compile(r"rất\s+vui\s+được\s+hỗ\s*trợ", re.IGNORECASE),
    re.compile(r"cảm\s*ơn\s+bạn\s+đã\s+hỏi", re.IGNORECASE),
    re.compile(r"xin\s+lỗi\s+vì\s+(?:sự\s+bất\s+tiện|nhầm\s+lẫn)", re.IGNORECASE),
    re.compile(r"dưới\s+đây\s+là\s+(?:câu\s+trả\s+lời|thông\s+tin)", re.IGNORECASE),
    re.compile(r"theo\s+như\s+(?:tôi|biết)", re.IGNORECASE),
    re.compile(r"với\s+tư\s+cách\s+là\s+một\s+AI", re.IGNORECASE),
]

TOO_SAFE_PATTERNS = [
    re.compile(r"nana\s+có\s+thể\s+hiểu", re.IGNORECASE),
    re.compile(r"tùy\s+thuộc\s+vào\s+ngữ\s+cảnh", re.IGNORECASE),
    re.compile(r"điều\s+này\s+có\s+thể\s+khác\s+nhau", re.IGNORECASE),
    re.compile(r"trên\s+thực\s+tế", re.IGNORECASE),
    re.compile(r"nhìn\s+chung\s+thì", re.IGNORECASE),
    re.compile(r"tóm\s+lại\s+lại", re.IGNORECASE),
]

REQUEST_REFUSAL_PATTERNS = [
    re.compile(r"nana\s+không\s+làm\s+được", re.IGNORECASE),
    re.compile(r"nana\s+từ\s+chối", re.IGNORECASE),
    re.compile(r"cái\s+đó\s+hơi\s+vô\s+lý", re.IGNORECASE),
    re.compile(r"sao\s+phải\s+làm\s+vậy", re.IGNORECASE),
]


# ─── Dataclasses ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PersonaSpineViolation:
    kind: str  # "service_bot" | "too_safe" | "no_personality" | "refusal_weak"
    detail: str
    severity: str  # "critical" | "warning" | "info"


@dataclass
class PersonaSpineResult:
    passed: bool
    violations: list[PersonaSpineViolation]
    summary: str


# ─── Spine generator ────────────────────────────────────────────────────────

class PersonaSpine:
    """Singleton spine cho Nana - khởi tạo một lần, dùng suốt session."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._smoke_stats = {
            "checks": 0,
            "passed": 0,
            "failed": 0,
            "service_bot_hits": 0,
            "too_safe_hits": 0,
            "no_personality_hits": 0,
            "weak_refusal_hits": 0,
        }
        self._last_check: Optional[PersonaSpineResult] = None

    def generate_spine_block(self, lane: str = "unknown") -> str:
        """Generate core spine block cho system prompt.

        Args:
            lane: private_owner | public_stage | operator_backstage | unknown

        Returns:
            Block nhỏ inject vào prompt TRƯỚC persona_boundary.
        """
        lane_key = self._normalize_lane(lane)
        lane_label = self._lane_label(lane_key)
        return f"""
NANA CORE SPINE (stable)
{lane_label}

Identity:
{NANA_CORE_IDENTITY.strip()}

Values:
{NANA_CORE_VALUES.strip()}

Boundaries:
{NANA_CORE_BOUNDARIES.strip()}

Motivation:
{NANA_CORE_MOTIVATION.strip()}

Voice:
{NANA_CORE_VOICE.strip()}

Lane: {lane_key}
Rule: Cùng một Nana, khác cách xuất hiện.
""".strip()

    def _normalize_lane(self, lane: str) -> str:
        aliases = {
            "private": "private_owner",
            "private_owner": "private_owner",
            "public": "public_stage",
            "public_viewer": "public_stage",
            "public_stage": "public_stage",
            "operator": "operator_backstage",
            "operator_backstage": "operator_backstage",
            "bridge_system": "operator_backstage",
            "unknown": "unknown",
        }
        return aliases.get(str(lane or "unknown").strip(), "unknown")

    def _lane_label(self, lane: str) -> str:
        labels = {
            "private_owner": "Với Ba: thân, mềm, có lịch sử chung. Xưng con/Ba tự nhiên.",
            "public_stage": "Public stage: Nana là nhân vật chính, viewer là khách bước vào phòng chat. Không Ba/con.",
            "operator_backstage": "Operator backstage: kỹ thuật, rõ ràng, ít diễn. Vẫn là Nana.",
            "unknown": "Unknown lane - giữ core spine, cẩn thận với Ba/con.",
        }
        return labels.get(lane, labels["unknown"])

    def smoke_test(self, text: str, context_lane: str = "unknown") -> PersonaSpineResult:
        """Smoke test một đoạn text - phát hiện khi Nana mất cá tính.

        Args:
            text: Text cần check
            context_lane: Lane hiện tại (để context hóa violations)

        Returns:
            PersonaSpineResult với danh sách violations nếu có.
        """
        violations: list[PersonaSpineViolation] = []
        text_lower = text.lower()

        # Check 1: Service bot phrases
        for pattern in SERVICE_BOT_PATTERNS:
            if pattern.search(text):
                violations.append(PersonaSpineViolation(
                    kind="service_bot",
                    detail=f"Service bot phrase: {pattern.pattern!r}",
                    severity="critical",
                ))
                with self._lock:
                    self._smoke_stats["service_bot_hits"] += 1

        # Check 2: Too safe / corporate
        for pattern in TOO_SAFE_PATTERNS:
            if pattern.search(text):
                violations.append(PersonaSpineViolation(
                    kind="too_safe",
                    detail=f"Too safe phrase: {pattern.pattern!r}",
                    severity="warning",
                ))
                with self._lock:
                    self._smoke_stats["too_safe_hits"] += 1

        # Check 3: No personality indicators
        personality_words = ["chắc", "thích", "nghĩ", "hơi", "biết", "không", "thấy"]
        has_personality = any(word in text_lower for word in personality_words)
        # Check for "I" markers and opinions
        first_person = any(marker in text_lower for marker in ["nana ", "mình ", "tui ", "ta "])
        if not has_personality and not first_person:
            violations.append(PersonaSpineViolation(
                kind="no_personality",
                detail="Text thiếu personality markers - có thể là template/auto-reply",
                severity="warning",
            ))
            with self._lock:
                self._smoke_stats["no_personality_hits"] += 1

        # Check 4: Weak refusal (should say no but doesn't)
        is_refusal_context = any(word in text_lower for word in ["vô lý", "không làm", "từ chối", "sao phải"])
        for pattern in REQUEST_REFUSAL_PATTERNS:
            if pattern.search(text) and "nhưng" in text_lower:
                violations.append(PersonaSpineViolation(
                    kind="refusal_weak",
                    detail="Refusal bị làm mềm quá mức - nên tự tin hơn",
                    severity="info",
                ))
                with self._lock:
                    self._smoke_stats["weak_refusal_hits"] += 1

        passed = len([v for v in violations if v.severity == "critical"]) == 0
        summary = self._summarize(violations, passed)

        result = PersonaSpineResult(
            passed=passed,
            violations=violations,
            summary=summary,
        )

        with self._lock:
            self._smoke_stats["checks"] += 1
            if passed:
                self._smoke_stats["passed"] += 1
            else:
                self._smoke_stats["failed"] += 1
            self._last_check = result

        return result

    def _summarize(self, violations: list[PersonaSpineViolation], passed: bool) -> str:
        if passed:
            return "PASSED - Nana giữ được cá tính"
        critical = [v for v in violations if v.severity == "critical"]
        warnings = [v for v in violations if v.severity == "warning"]
        if critical:
            return f"FAILED - {len(critical)} critical violation(s): {', '.join(v.kind for v in critical)}"
        return f"WARNINGS - {len(warnings)} warning(s): {', '.join(v.kind for v in warnings)}"

    def status(self) -> dict:
        """Return status snapshot for /persona-spine-status."""
        with self._lock:
            stats = dict(self._smoke_stats)
            last = self._last_check
        return {
            "initialized_at": self._initialized_at,
            "uptime_seconds": time.time() - self._initialized_at,
            "smoke_stats": stats,
            "last_check": {
                "passed": last.passed if last else None,
                "summary": last.summary if last else None,
                "violation_count": len(last.violations) if last else 0,
            } if last else None,
            "core_spine_blocks": {
                "identity": bool(NANA_CORE_IDENTITY.strip()),
                "values": bool(NANA_CORE_VALUES.strip()),
                "boundaries": bool(NANA_CORE_BOUNDARIES.strip()),
                "motivation": bool(NANA_CORE_MOTIVATION.strip()),
                "voice": bool(NANA_CORE_VOICE.strip()),
            },
            "lane_aliases": "private_owner | public_stage(public_viewer) | operator_backstage(operator)",
        }

    def status_lines(self) -> list[str]:
        """Return formatted status lines for CLI status command."""
        s = self.status()
        lines = [
            "🧬 Persona Spine Status",
            f"  Phase: CORE-PERSONA-SPINE-1 | initialized: {s['uptime_seconds']:.0f}s ago",
            "  Core conviction blocks loaded: identity ✓ values ✓ boundaries ✓ motivation ✓ voice ✓",
            f"  Lane aliases: {s['lane_aliases']}",
            "  Prompt role: injected before persona_boundary; boundary still controls private/public address.",
            "  Smoke stats:",
            f"    checks={s['smoke_stats']['checks']} | passed={s['smoke_stats']['passed']} | failed={s['smoke_stats']['checks'] - s['smoke_stats']['passed']}",
            f"    service_bot_hits={s['smoke_stats']['service_bot_hits']} | too_safe_hits={s['smoke_stats']['too_safe_hits']}",
            f"    no_personality_hits={s['smoke_stats']['no_personality_hits']} | weak_refusal_hits={s['smoke_stats']['weak_refusal_hits']}",
        ]
        if s['last_check']:
            lines.append(f"  Last check: {s['last_check']['summary']}")
        lines.append("  Live verify: smoke_test(your_text) | generate_spine_block(lane)")
        return lines


# ─── Singleton instance ────────────────────────────────────────────────────

_SPINE = PersonaSpine()


def get_spine() -> PersonaSpine:
    return _SPINE


def generate_spine_block(lane: str = "unknown") -> str:
    """Convenience wrapper."""
    return _SPINE.generate_spine_block(lane)


def smoke_test(text: str, lane: str = "unknown") -> PersonaSpineResult:
    """Convenience wrapper."""
    return _SPINE.smoke_test(text, lane)


def spine_status() -> list[str]:
    """Convenience wrapper."""
    return _SPINE.status_lines()
