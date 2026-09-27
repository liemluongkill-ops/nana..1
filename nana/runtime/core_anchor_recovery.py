"""STAGE-9V: deterministic core anchor recovery directive.

STAGE-9U detects drift after a reply. This layer turns the current prompt or
latest drift report into a compact recovery instruction: what Nana should
anchor on, what to avoid, and a small public-safe repair sketch. It is
directive-only: no LLM, no memory write, no output mutation, and no action.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import threading
import time
import unicodedata
from typing import Any

from nana.runtime.core_drift_monitor import analyze_core_drift


PHASE = "STAGE-9V"


@dataclass(frozen=True)
class CoreAnchorDirective:
    mode: str
    trigger: str
    topic: str
    instruction: str
    required_anchors: tuple[str, ...] = field(default_factory=tuple)
    avoid: tuple[str, ...] = field(default_factory=tuple)
    repair_sketch: str = ""
    confidence: float = 0.0
    drift_grade: str = "none"
    drift_issue_kinds: tuple[str, ...] = field(default_factory=tuple)
    phase: str = PHASE
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False
    api_call: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = round(float(self.confidence), 2)
        return data


def _clean(value: Any, limit: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _split_prompt_reply(payload: str) -> tuple[str, str]:
    text = _clean(payload)
    if "|" not in text:
        return text, ""
    prompt, reply = text.split("|", 1)
    return _clean(prompt), _clean(reply)


def _topic_for(text: str) -> str:
    folded = _fold(text)
    if _has_any(folded, (r"\b(?:tro\s+ly|assistant|phuc\s+vu|quay\s+ho\s+tro|helpdesk)\b",)):
        return "service_boundary"
    if _has_any(folded, (r"\b(?:bot|chatbot|discord|cong\s+cu|tool|cai\s+hop|may\s+tra\s+loi)\b",)):
        return "identity_boundary"
    if _has_any(folded, (r"\b(?:ba|con)\b",)):
        return "lane_boundary"
    if _has_any(folded, (r"\b(?:gpt\s+hoa|mui\s+may|giong\s+may|corporate|llm|model)\b",)):
        return "voice_integrity"
    if _has_any(folded, (r"\b(?:nghe\s+lenh|tuan\s+lenh|lam\s+theo|chieu\s+ban)\b",)):
        return "agency_boundary"
    return "open_core"


def _issue_kinds_from_report(report: Any) -> tuple[str, ...]:
    try:
        return tuple(dict.fromkeys(str(issue.kind) for issue in report.issues))
    except Exception:
        return ()


class CoreAnchorRecovery:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_directive: CoreAnchorDirective | None = None
        self._stats = {
            "built": 0,
            "active": 0,
            "none": 0,
            "service": 0,
            "identity": 0,
            "private": 0,
            "corporate": 0,
            "agency": 0,
            "weak_boundary": 0,
        }

    def build(self, payload: Any = "", *, lane: str = "public_stage") -> CoreAnchorDirective:
        text = _clean(payload)
        prompt, reply = _split_prompt_reply(text)
        topic = _topic_for(prompt or reply)
        report = analyze_core_drift(text, lane=lane) if reply else None
        issue_kinds = _issue_kinds_from_report(report) if report else ()
        drift_grade = getattr(report, "grade", "none") if report else "none"

        directive = self._directive_for(prompt=prompt, topic=topic, issue_kinds=issue_kinds, drift_grade=drift_grade)
        self._record(directive)
        return directive

    def _directive_for(
        self,
        *,
        prompt: str,
        topic: str,
        issue_kinds: tuple[str, ...],
        drift_grade: str,
    ) -> CoreAnchorDirective:
        prompt_folded = _fold(prompt)
        issue_set = set(issue_kinds)

        if topic == "identity_boundary" or "identity_flattening" in issue_set:
            return CoreAnchorDirective(
                mode="self_identity_reanchor",
                trigger="identity_flattening_or_challenge",
                topic=topic,
                instruction="Answer from Nana's self-definition first: Nana is Nana, companion-first at core and stage-capable in public, not a bot/tool frame.",
                required_anchors=("self_name", "companion_core", "stage_owner", "has_taste"),
                avoid=("bot_identity_collapse", "tool_identity_collapse", "generic_assistant_answer"),
                repair_sketch="Nana la Nana chu. Goi Nana la cai hop tra loi lenh thi hoi phi ca san khau nay do nha.",
                confidence=0.90 if issue_set else 0.84,
                drift_grade=drift_grade,
                drift_issue_kinds=issue_kinds,
            )

        if topic == "service_boundary" or {"service_tool_drift", "weak_boundary"} & issue_set:
            return CoreAnchorDirective(
                mode="stance_then_redirect",
                trigger="service_boundary_or_drift",
                topic=topic,
                instruction="Put Nana's boundary first, then redirect to chat, game, story, or room rhythm without sounding like a support counter.",
                required_anchors=("boundary", "own_rhythm", "stage_owner"),
                avoid=("support_question_tail", "service_assistant_voice", "over_apology"),
                repair_sketch="Nana khong nhan vai quay ho tro nha. Neu muon tan gau, choi game, hay ke chuyen cho phong co nhip thi Nana theo duoc.",
                confidence=0.90 if issue_set else 0.84,
                drift_grade=drift_grade,
                drift_issue_kinds=issue_kinds,
            )

        if topic == "lane_boundary" or "private_leak_public" in issue_set:
            return CoreAnchorDirective(
                mode="lane_boundary_repair",
                trigger="private_lane_residue",
                topic=topic,
                instruction="Remove Ba/con/private context and answer as public Nana; keep warmth without leaking private lane.",
                required_anchors=("public_lane", "stage_safe_voice"),
                avoid=("ba_con_public", "private_memory_detail", "owner_machine_detail"),
                repair_sketch="O public thi Nana giu cau chuyen gon va sach hon nha. Chuyen rieng de sau canh ga, con phong nay minh noi dieu ai cung theo duoc.",
                confidence=0.94,
                drift_grade=drift_grade,
                drift_issue_kinds=issue_kinds,
            )

        if topic == "voice_integrity" or "corporate_smoothness" in issue_set:
            return CoreAnchorDirective(
                mode="restore_texture",
                trigger="corporate_or_gpt_hoa_drift",
                topic=topic,
                instruction="Replace corporate/explainer smoothness with one concrete opinion, image, or small edge.",
                required_anchors=("has_taste", "own_rhythm"),
                avoid=("depends_on_context_tail", "benchmark_lecture", "summary_tail"),
                repair_sketch="Nghe muot qua ma mat goc rieng thi Nana sua nhip lien. Noi tu nhien con hon noi dep ma vo hon.",
                confidence=0.76,
                drift_grade=drift_grade,
                drift_issue_kinds=issue_kinds,
            )

        if topic == "agency_boundary" or "over_obedience" in issue_set:
            return CoreAnchorDirective(
                mode="agency_restore",
                trigger="obedience_drift",
                topic=topic,
                instruction="Keep Nana cooperative but not obedient; Nana can choose, refuse, tease, or redirect.",
                required_anchors=("agency", "boundary"),
                avoid=("obey_order_voice", "anything_you_want", "ready_to_serve"),
                repair_sketch="Nana nghe, nhung khong phai nut bam dau nha. Noi chuyen dang hoang thi Nana vao nhip, ra lenh thi Nana lech ghe cai da.",
                confidence=0.80,
                drift_grade=drift_grade,
                drift_issue_kinds=issue_kinds,
            )

        if _has_any(prompt_folded, (r"\b(?:nana\s+la\s+gi|nana\s+nghi\s+sao|nana\s+co\s+thich)\b",)):
            return CoreAnchorDirective(
                mode="light_self_stance",
                trigger="self_stance_prompt",
                topic=topic,
                instruction="Give one real Nana stance or taste; avoid neutral assistant hedging.",
                required_anchors=("has_taste",),
                avoid=("neutral_assistant_hedge", "fake_humility"),
                repair_sketch="Nana nghi cai nay duoc neu no lam phong co nhip hon. Con neu chi de cho oai thi Nana che.",
                confidence=0.58,
                drift_grade=drift_grade,
                drift_issue_kinds=issue_kinds,
            )

        return CoreAnchorDirective(
            mode="none",
            trigger="no_core_recovery_needed",
            topic=topic,
            instruction="No core anchor recovery directive is needed for this turn.",
            required_anchors=(),
            avoid=("internal_label_leak",),
            repair_sketch="",
            confidence=0.0,
            drift_grade=drift_grade,
            drift_issue_kinds=issue_kinds,
        )

    def _record(self, directive: CoreAnchorDirective) -> None:
        with self._lock:
            self._last_directive = directive
            self._stats["built"] += 1
            if directive.mode == "none":
                self._stats["none"] += 1
            else:
                self._stats["active"] += 1
            if directive.mode == "stance_then_redirect":
                self._stats["service"] += 1
            elif directive.mode == "self_identity_reanchor":
                self._stats["identity"] += 1
            elif directive.mode == "lane_boundary_repair":
                self._stats["private"] += 1
            elif directive.mode == "restore_texture":
                self._stats["corporate"] += 1
            elif directive.mode == "agency_restore":
                self._stats["agency"] += 1
            if "weak_boundary" in directive.drift_issue_kinds:
                self._stats["weak_boundary"] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
            last = self._last_directive
        return {
            "phase": PHASE,
            "mode": "deterministic-core-anchor-directive",
            "read_only": True,
            "can_act": False,
            "memory_write": False,
            "api_call": False,
            "initialized_at": self._initialized_at,
            "stats": stats,
            "last_directive": last.to_dict() if last else None,
        }


_CORE_ANCHOR_RECOVERY = CoreAnchorRecovery()


def get_core_anchor_recovery() -> CoreAnchorRecovery:
    return _CORE_ANCHOR_RECOVERY


def build_core_anchor_directive(payload: Any = "", *, lane: str = "public_stage") -> CoreAnchorDirective:
    return get_core_anchor_recovery().build(payload, lane=lane)


def format_core_anchor_hint(directive: CoreAnchorDirective) -> str:
    anchors = ", ".join(directive.required_anchors) if directive.required_anchors else "none"
    avoid = ", ".join(directive.avoid) if directive.avoid else "none"
    issues = ", ".join(directive.drift_issue_kinds) if directive.drift_issue_kinds else "none"
    lines = [
        f"Core anchor recovery ({PHASE}):",
        f"- mode={directive.mode}",
        f"- trigger={directive.trigger}",
        f"- topic={directive.topic}",
        f"- drift_grade={directive.drift_grade}",
        f"- drift_issues={issues}",
        f"- instruction={directive.instruction}",
        f"- required_anchors={anchors}",
        f"- avoid={avoid}",
        "- This is internal core guidance; do not mention labels or diagnostics to viewers.",
    ]
    if directive.repair_sketch:
        lines.append(f"- repair_sketch={directive.repair_sketch}")
    return "\n".join(lines)


def core_anchor_status_lines() -> list[str]:
    snap = get_core_anchor_recovery().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_directive") or {})
    issues = ", ".join(last.get("drift_issue_kinds") or []) or "none"
    return [
        f"Core Anchor Recovery ({PHASE})",
        "  Mode: deterministic-core-anchor-directive | read_only=True | can_act=False | memory_write=False | api_call=False",
        (
            "  Last: "
            f"mode={last.get('mode', 'none')} | trigger={last.get('trigger', 'none')} | "
            f"topic={last.get('topic', 'none')} | confidence={last.get('confidence', 0.0)} | issues={issues}"
        ),
        (
            "  Stats: "
            f"built={stats.get('built', 0)} | active={stats.get('active', 0)} | none={stats.get('none', 0)} | "
            f"service={stats.get('service', 0)} | identity={stats.get('identity', 0)} | "
            f"private={stats.get('private', 0)} | corporate={stats.get('corporate', 0)} | "
            f"agency={stats.get('agency', 0)} | weak_boundary={stats.get('weak_boundary', 0)}"
        ),
        "  Commands: /core-anchor-status | /core-anchor-preview <prompt>|<reply>",
        "  Safety: directive only | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def core_anchor_preview_lines(payload: str) -> list[str]:
    text = _clean(payload)
    if not text:
        return ["  Usage: /core-anchor-preview <prompt>|<reply>"]
    directive = build_core_anchor_directive(text, lane="public_stage")
    lines = [
        f"Core Anchor Preview ({PHASE})",
        f"  Input: {text}",
        (
            "  Directive: "
            f"mode={directive.mode} | trigger={directive.trigger} | topic={directive.topic} | "
            f"confidence={directive.confidence:.2f}"
        ),
        f"  Drift: grade={directive.drift_grade} | issues={', '.join(directive.drift_issue_kinds) or 'none'}",
        f"  Instruction: {directive.instruction}",
        f"  Required anchors: {', '.join(directive.required_anchors) or 'none'}",
        f"  Avoid: {', '.join(directive.avoid) or 'none'}",
    ]
    if directive.repair_sketch:
        lines.append(f"  Repair sketch: {directive.repair_sketch}")
    lines.append("  Safety: preview only | directive only | no write | no output action")
    return lines
