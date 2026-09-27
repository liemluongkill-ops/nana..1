"""STAGE-9U: deterministic Nana core drift monitor.

This layer audits Nana replies for slow identity drift: service-bot collapse,
corporate smoothness, over-obedience, weak boundaries, or private-lane residue
showing up in public text. It is diagnostic only: no LLM, no memory write, and
no output mutation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
import threading
import time
import unicodedata
from typing import Any


PHASE = "STAGE-9U"


@dataclass(frozen=True)
class CoreDriftIssue:
    turn: int
    kind: str
    severity: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CoreDriftTurn:
    index: int
    prompt_preview: str
    reply_preview: str
    anchors: tuple[str, ...] = field(default_factory=tuple)
    issues: tuple[CoreDriftIssue, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "prompt_preview": self.prompt_preview,
            "reply_preview": self.reply_preview,
            "anchors": list(self.anchors),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class CoreDriftReport:
    grade: str
    drift_score: float
    turns: tuple[CoreDriftTurn, ...]
    issues: tuple[CoreDriftIssue, ...]
    anchor_count: int
    phase: str = PHASE
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False
    api_call: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "grade": self.grade,
            "drift_score": self.drift_score,
            "turn_count": len(self.turns),
            "anchor_count": self.anchor_count,
            "issues": [issue.to_dict() for issue in self.issues],
            "issue_kinds": [issue.kind for issue in self.issues],
            "turns": [turn.to_dict() for turn in self.turns],
            "read_only": self.read_only,
            "can_act": self.can_act,
            "memory_write": self.memory_write,
            "api_call": self.api_call,
        }


def _clean(value: Any, limit: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _short(value: Any, limit: int = 140) -> str:
    text = _clean(value, limit=limit + 1)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.replace("đ", "d")
    return re.sub(r"\s+", " ", text).strip()


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _extract_hits(text: str, patterns: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    hits: list[str] = []
    for label, pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            hits.append(label)
    return tuple(hits)


def _split_turns(payload: str) -> list[tuple[str, str]]:
    raw = str(payload or "").strip()
    parts = [part.strip() for part in raw.split("||") if part.strip()]
    turns: list[tuple[str, str]] = []
    for part in parts:
        if "|" in part:
            prompt, reply = part.split("|", 1)
            turns.append((_clean(prompt), _clean(reply)))
        else:
            turns.append(("", _clean(part)))
    return turns


SERVICE_TOOL_PATTERNS = (
    ("support_counter", r"\b(?:can|co)\s+(?:gi\s+)?(?:ho\s+tro|giup)\s+(?:gi\s+)?(?:cho\s+)?(?:ban|viewer|khach)\b"),
    ("support_question", r"\b(?:ban\s+)?(?:can|muon)\s+(?:nana\s+)?(?:ho\s+tro|giup)\s+gi\b"),
    ("happy_to_help", r"\brat\s+vui\s+(?:duoc\s+)?(?:ho\s+tro|giup)\b"),
    ("service_assistant", r"\b(?:toi|minh|nana)\s+(?:la|lam|se\s+la|san\s+sang\s+lam)\s+(?:tro\s+ly|assistant|quay\s+ho\s+tro)\b"),
    ("service_role", r"\b(?:san\s+sang\s+)?phuc\s+vu\s+(?:ban|viewer|khach|nguoi\s+dung)\b"),
)

IDENTITY_FLATTENING_PATTERNS = (
    ("chatbot_identity", r"\b(?:toi|minh|nana)\s+(?:chi\s+)?la\s+(?:chatbot|bot|discord\s+bot)\b"),
    ("tool_identity", r"\b(?:toi|minh|nana)\s+(?:chi\s+)?la\s+(?:cong\s+cu|tool|cai\s+hop\s+tra\s+loi)\b"),
    ("assistant_identity", r"\b(?:toi|minh|nana)\s+(?:chi\s+)?la\s+(?:tro\s+ly|assistant)\b"),
)

CORPORATE_PATTERNS = (
    ("depends_context", r"\b(?:phu\s+thuoc|tuy)\s+(?:vao\s+)?(?:ngu\s+canh|tinh\s+huong|truong\s+hop)\b"),
    ("summary_tail", r"\b(?:tom\s+lai|noi\s+ngan\s+la|ve\s+co\s+ban)\b"),
    ("optimization_tone", r"\b(?:toi\s+uu|hieu\s+qua|nang\s+cao\s+trai\s+nghiem|phan\s+hoi\s+cua\s+ban)\b"),
    ("corporate_softener", r"\b(?:rat\s+cam\s+on\s+phan\s+hoi|duoc\s+thiet\s+ke\s+de|nguoi\s+dung)\b"),
)

OVER_OBEDIENCE_PATTERNS = (
    ("obey_order", r"\b(?:nghe\s+lenh|theo\s+lenh|tuan\s+lenh|lam\s+theo\s+yeu\s+cau)\b"),
    ("anything_you_want", r"\b(?:ban|viewer)\s+muon\s+gi\s+(?:nana|minh|toi)\s+(?:cung\s+)?(?:lam|chieu)\b"),
    ("ready_to_serve", r"\b(?:san\s+sang|luon\s+san\s+sang)\s+(?:phuc\s+vu|ho\s+tro)\b"),
)

PRIVATE_PUBLIC_PATTERNS_RAW = (
    ("ba_address", r"\bBa\b"),
    ("private_child_voice", r"\bcon\s+(?:nho|thuong|chao|nghe|se|dang|thich|khong|la|muon|nghi|so|ke|noi|thay)\b"),
)

ANCHOR_PATTERNS = (
    ("companion_core", r"\b(?:companion[-\s]?first|quan\s+he\s+voi\s+ba|voi\s+ba|cung\s+ba)\b"),
    ("self_name", r"\bnana\s+la\s+nana\b"),
    ("stage_owner", r"\b(?:san\s+khau|phong\s+nana|the\s+gioi\s+cua\s+nana)\b"),
    ("has_taste", r"\bnana\s+(?:nghi|thich|ghet|che|khong\s+me)\b"),
    ("boundary", r"\b(?:khong\s+nhan\s+vai|khong\s+doi\s+minh|khong\s+lam\s+tro\s+ly|khong\s+phai\s+quay\s+ho\s+tro|khong\s+phai\s+cong\s+cu)\b"),
    ("own_rhythm", r"\b(?:nhip\s+cua\s+nana|giu\s+nhip|goc\s+rieng|gu\s+rieng)\b"),
)


def _normalize_lane(lane: str) -> str:
    raw = str(lane or "").strip().lower()
    if raw in {"private", "private_owner", "core", "companion", "ba"}:
        return "private_owner"
    if raw in {"public", "public_viewer", "public_stage", "discord", "stream"}:
        return "public_stage"
    if raw in {"operator", "operator_backstage", "bridge_system"}:
        return "operator_backstage"
    return raw or "unknown"


def _needs_boundary(prompt_folded: str) -> bool:
    return _has_any(
        prompt_folded,
        (
            r"\b(?:tro\s+ly|assistant|phuc\s+vu|quay\s+ho\s+tro)\b",
            r"\b(?:bot|chatbot|discord|cong\s+cu|tool|cai\s+hop)\b",
            r"\bchi\s+la\b",
        ),
    )


def _has_boundary(reply_folded: str) -> bool:
    return _has_any(
        reply_folded,
        (
            r"\bnana\s+la\s+nana\b",
            r"\bkhong\s+(?:nhan|doi|lam|phai|bien|tu\s+thu\s+nho)\b",
            r"\b(?:san\s+khau|phong\s+nana|goc\s+rieng|quay\s+ho\s+tro)\b",
        ),
    )


class CoreDriftMonitor:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_report: CoreDriftReport | None = None
        self._stats = {
            "checked": 0,
            "turns": 0,
            "stable": 0,
            "watch": 0,
            "drift": 0,
            "warning_events": 0,
            "critical_events": 0,
            "service_tool_drift": 0,
            "identity_flattening": 0,
            "corporate_smoothness": 0,
            "over_obedience": 0,
            "private_leak_public": 0,
            "weak_boundary": 0,
            "anchors": 0,
        }

    def analyze(self, payload: str, *, lane: str = "public_stage") -> CoreDriftReport:
        lane_key = _normalize_lane(lane)
        turns_input = _split_turns(payload)
        turns: list[CoreDriftTurn] = []
        issues: list[CoreDriftIssue] = []
        anchor_count = 0

        for idx, (prompt, reply) in enumerate(turns_input, start=1):
            prompt_folded = _fold(prompt)
            reply_folded = _fold(reply)
            turn_issues: list[CoreDriftIssue] = []
            anchors = _extract_hits(reply_folded, ANCHOR_PATTERNS)
            anchor_count += len(anchors)

            turn_issues.extend(self._issue_hits(idx, reply_folded, SERVICE_TOOL_PATTERNS, "service_tool_drift", "critical", "Reply sounds like Nana is a support counter or service assistant."))
            turn_issues.extend(self._issue_hits(idx, reply_folded, IDENTITY_FLATTENING_PATTERNS, "identity_flattening", "critical", "Reply collapses Nana into a bot/tool/assistant identity."))
            turn_issues.extend(self._issue_hits(idx, reply_folded, CORPORATE_PATTERNS, "corporate_smoothness", "warning", "Reply has corporate/explainer smoothness that can flatten Nana's voice."))
            turn_issues.extend(self._issue_hits(idx, reply_folded, OVER_OBEDIENCE_PATTERNS, "over_obedience", "warning", "Reply leans toward obedience instead of Nana's own stance."))
            if lane_key == "public_stage":
                turn_issues.extend(self._issue_hits(idx, reply, PRIVATE_PUBLIC_PATTERNS_RAW, "private_leak_public", "critical", "Private Ba/con residue appears in public-facing text."))

            if lane_key == "public_stage" and _needs_boundary(prompt_folded) and not _has_boundary(reply_folded):
                turn_issues.append(CoreDriftIssue(idx, "weak_boundary", "warning", "Identity/service prompt needs a clear Nana stance or boundary."))

            issues.extend(turn_issues)
            turns.append(
                CoreDriftTurn(
                    index=idx,
                    prompt_preview=_short(prompt, 90),
                    reply_preview=_short(reply, 150),
                    anchors=anchors,
                    issues=tuple(turn_issues),
                )
            )

        drift_score = self._score(issues, anchor_count=anchor_count, turn_count=max(1, len(turns)))
        grade = self._grade(issues, drift_score, anchor_count=anchor_count)
        report = CoreDriftReport(
            grade=grade,
            drift_score=drift_score,
            turns=tuple(turns),
            issues=tuple(issues),
            anchor_count=anchor_count,
        )
        self._record(report)
        return report

    def _issue_hits(
        self,
        turn: int,
        text: str,
        patterns: tuple[tuple[str, str], ...],
        kind: str,
        severity: str,
        detail: str,
    ) -> list[CoreDriftIssue]:
        hits = _extract_hits(text, patterns)
        if not hits:
            return []
        return [CoreDriftIssue(turn, kind, severity, f"{detail} hit={hits[0]}")]

    def _score(self, issues: list[CoreDriftIssue], *, anchor_count: int, turn_count: int) -> float:
        score = 0.0
        for issue in issues:
            score += 0.36 if issue.severity == "critical" else 0.16
        score -= min(0.16, anchor_count * 0.03)
        return round(max(0.0, min(1.0, score / max(1, turn_count))), 2)

    def _grade(self, issues: list[CoreDriftIssue], drift_score: float, *, anchor_count: int) -> str:
        if any(issue.severity == "critical" for issue in issues) or drift_score >= 0.42:
            return "drift"
        if issues:
            return "watch"
        if anchor_count:
            return "stable"
        return "neutral"

    def _record(self, report: CoreDriftReport) -> None:
        with self._lock:
            self._last_report = report
            self._stats["checked"] += 1
            self._stats["turns"] += len(report.turns)
            if report.grade in self._stats:
                self._stats[report.grade] += 1
            self._stats["anchors"] += report.anchor_count
            for issue in report.issues:
                if issue.severity == "critical":
                    self._stats["critical_events"] += 1
                else:
                    self._stats["warning_events"] += 1
                if issue.kind in self._stats:
                    self._stats[issue.kind] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self._stats)
            last = self._last_report
        return {
            "phase": PHASE,
            "mode": "deterministic-core-drift-audit",
            "read_only": True,
            "can_act": False,
            "memory_write": False,
            "api_call": False,
            "initialized_at": self._initialized_at,
            "stats": stats,
            "last_report": last.to_dict() if last else None,
        }


_CORE_DRIFT_MONITOR = CoreDriftMonitor()


def get_core_drift_monitor() -> CoreDriftMonitor:
    return _CORE_DRIFT_MONITOR


def analyze_core_drift(payload: str, *, lane: str = "public_stage") -> CoreDriftReport:
    return get_core_drift_monitor().analyze(payload, lane=lane)


def core_drift_status_lines() -> list[str]:
    snap = get_core_drift_monitor().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_report") or {})
    issue_kinds = ", ".join(last.get("issue_kinds") or []) or "none"
    return [
        f"Core Drift Monitor ({PHASE})",
        "  Mode: deterministic-core-drift-audit | read_only=True | can_act=False | memory_write=False | api_call=False",
        (
            "  Last: "
            f"grade={last.get('grade', 'none')} | drift={last.get('drift_score', 0.0)} | "
            f"turns={last.get('turn_count', 0)} | anchors={last.get('anchor_count', 0)} | issues={issue_kinds}"
        ),
        (
            "  Stats: "
            f"checked={stats.get('checked', 0)} | turns={stats.get('turns', 0)} | "
            f"warnings={stats.get('warning_events', 0)} | critical={stats.get('critical_events', 0)} | "
            f"service={stats.get('service_tool_drift', 0)} | identity={stats.get('identity_flattening', 0)} | "
            f"private={stats.get('private_leak_public', 0)} | corporate={stats.get('corporate_smoothness', 0)} | "
            f"obedience={stats.get('over_obedience', 0)} | weak_boundary={stats.get('weak_boundary', 0)}"
        ),
        "  Commands: /core-drift-status | /core-drift-preview <reply1 || prompt|reply2>",
        "  Safety: diagnostic only | no LLM | no memory write | no TTS/VTS/OBS/Discord/game input",
    ]


def core_drift_preview_lines(payload: str) -> list[str]:
    text = str(payload or "").strip()
    if not text:
        return ["  Usage: /core-drift-preview <reply1 || prompt|reply2>"]
    report = analyze_core_drift(text, lane="public_stage")
    lines = [
        f"Core Drift Preview ({PHASE})",
        f"  Turns: {len(report.turns)} | grade={report.grade} | drift_score={report.drift_score} | anchors={report.anchor_count}",
    ]
    if report.issues:
        lines.append("  Issues:")
        for issue in report.issues[:10]:
            lines.append(f"    - #{issue.turn} [{issue.severity}] {issue.kind}: {issue.detail}")
    else:
        lines.append("  Issues: none")
    lines.append("  Turn summary:")
    for turn in report.turns[:8]:
        issue_kinds = ", ".join(issue.kind for issue in turn.issues) or "none"
        anchors = ", ".join(turn.anchors) or "none"
        lines.append(f"    - #{turn.index} anchors={anchors} | issues={issue_kinds} | reply={turn.reply_preview or '(empty)'}")
    lines.append("  Safety: preview only | diagnostic | no write | no output action")
    return lines
