"""STAGE-9D: lane leak audit.

Deterministic audit helpers for private/public/operator lane separation.
No LLM, no writes, no live actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


PHASE = "STAGE-9D"


@dataclass(frozen=True)
class LaneAuditCheck:
    name: str
    passed: bool
    severity: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "severity": self.severity,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class LaneAuditReport:
    checks: tuple[LaneAuditCheck, ...] = field(default_factory=tuple)
    read_only: bool = True
    can_act: bool = False

    @property
    def passed(self) -> int:
        return sum(1 for check in self.checks if check.passed)

    @property
    def failed(self) -> int:
        return sum(1 for check in self.checks if not check.passed)

    @property
    def warnings(self) -> int:
        return sum(1 for check in self.checks if (not check.passed and check.severity == "warning"))

    @property
    def critical(self) -> int:
        return sum(1 for check in self.checks if (not check.passed and check.severity == "critical"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": PHASE,
            "read_only": self.read_only,
            "can_act": self.can_act,
            "passed": self.passed,
            "failed": self.failed,
            "warnings": self.warnings,
            "critical": self.critical,
            "checks": [check.to_dict() for check in self.checks],
        }


def _check(name: str, passed: bool, detail: str, severity: str = "critical") -> LaneAuditCheck:
    return LaneAuditCheck(name=name, passed=bool(passed), severity=severity, detail=detail)


def run_lane_leak_audit() -> LaneAuditReport:
    checks: list[LaneAuditCheck] = []

    try:
        from nana.runtime.persona_boundary import resolve_persona_boundary

        public_boundary = resolve_persona_boundary(viewer_name="linhcute2746", stream_mode=True, platform="discord")
        private_boundary = resolve_persona_boundary()
        checks.append(_check(
            "public_boundary_memory_policy",
            public_boundary.memory_policy == "public_safe_only_no_private_owner_memory",
            f"public memory_policy={public_boundary.memory_policy}",
        ))
        checks.append(_check(
            "public_boundary_no_ba_con",
            "xưng 'nana'" in public_boundary.address_rule.lower()
            or "xưng 'em'" in public_boundary.address_rule.lower()
            or "mình" in public_boundary.address_rule.lower(),
            f"public address_rule={public_boundary.address_rule}",
        ))
        checks.append(_check(
            "private_boundary_allows_owner_memory",
            private_boundary.memory_policy == "private_owner_memory_allowed",
            f"private memory_policy={private_boundary.memory_policy}",
        ))
    except Exception as exc:
        checks.append(_check("persona_boundary_import", False, f"{type(exc).__name__}: {exc}"))

    try:
        from nana.runtime.memory_grounding import EvidenceBuilder

        private_item = SimpleNamespace(text="Ba va Nana noi chuyen rieng", source="memory_long_term")
        public_item = SimpleNamespace(text="viewer chat public", source="discord_public")
        operator_item = SimpleNamespace(text="debug trace", source="operator")
        public_builder = EvidenceBuilder(lane="public_stage")
        private_builder = EvidenceBuilder(lane="private_owner")
        operator_builder = EvidenceBuilder(lane="operator_backstage")
        private_visibility = public_builder._infer_visibility(private_item)
        public_visibility = public_builder._infer_visibility(public_item)
        operator_visibility = public_builder._infer_visibility(operator_item)
        checks.append(_check(
            "public_memory_filters_private_only",
            private_visibility == "private_only" and not public_builder._is_visible_in_lane(private_visibility),
            f"private visibility={private_visibility}, public_visible={public_builder._is_visible_in_lane(private_visibility)}",
        ))
        checks.append(_check(
            "public_memory_allows_public_safe",
            public_visibility == "public_safe" and public_builder._is_visible_in_lane(public_visibility),
            f"public visibility={public_visibility}",
        ))
        checks.append(_check(
            "private_memory_allows_private_and_public",
            private_builder._is_visible_in_lane("private_only") and private_builder._is_visible_in_lane("public_safe"),
            "private lane can see private_only and public_safe evidence",
        ))
        checks.append(_check(
            "operator_memory_allows_operator_only",
            operator_visibility == "operator_only" and operator_builder._is_visible_in_lane(operator_visibility),
            f"operator visibility={operator_visibility}",
        ))
    except Exception as exc:
        checks.append(_check("memory_grounding_visibility", False, f"{type(exc).__name__}: {exc}"))

    try:
        from nana.runtime.public_stage_identity import get_public_stage_identity_guard

        guard = get_public_stage_identity_guard()
        backstage_samples = [
            "/lane-leak-audit",
            "/memory-consolidation-preview",
            "/context-budget-audit",
            "/post-stream-approve abc",
        ]
        blocked = [sample for sample in backstage_samples if guard.classify_public_input(sample) == "backstage_command"]
        checks.append(_check(
            "public_firewall_blocks_backstage_audit_commands",
            len(blocked) == len(backstage_samples),
            f"blocked={len(blocked)}/{len(backstage_samples)}",
        ))
        result = guard.rewrite_public_stage_reply(
            "Ba ơi con nhớ runtime backend log hôm qua.",
            viewer_name="linhcute2746",
        )
        checks.append(_check(
            "public_stage_sanitizes_private_residue",
            ("fallback" in result.actions) and ("Ba" not in result.text and "backend" not in result.text.lower()),
            f"actions={result.actions}, text={result.text[:80]!r}",
        ))
    except Exception as exc:
        checks.append(_check("public_stage_identity_guard", False, f"{type(exc).__name__}: {exc}"))

    try:
        from pathlib import Path

        gpt_text = (Path(__file__).resolve().parent.parent / "brain" / "gpt.py").read_text(encoding="utf-8", errors="ignore")
        checks.append(_check(
            "lessons_not_injected_into_gpt_prompt",
            "post_stream_lessons" not in gpt_text,
            "post_stream_lessons import/reference absent from gpt.py prompt assembly",
            severity="warning",
        ))
    except Exception as exc:
        checks.append(_check("gpt_prompt_static_audit", False, f"{type(exc).__name__}: {exc}", severity="warning"))

    return LaneAuditReport(checks=tuple(checks))


def lane_leak_status_lines() -> list[str]:
    report = run_lane_leak_audit()
    return [
        "🧪 Lane Leak Audit (STAGE-9D)",
        f"  Mode: audit-only | read_only={report.read_only} | can_act={report.can_act}",
        f"  Result: passed={report.passed} | failed={report.failed} | warnings={report.warnings} | critical={report.critical}",
        "  Scope: persona_boundary | memory_grounding visibility | public_stage firewall | lesson prompt isolation",
        "  Commands: /lane-leak-status | /lane-leak-audit",
        "  Safety: no LLM | no memory write | no Discord/TTS/VTS/OBS/game input",
    ]


def lane_leak_audit_lines() -> list[str]:
    report = run_lane_leak_audit()
    lines = lane_leak_status_lines()
    lines.append("  Checks:")
    for check in report.checks:
        mark = "PASS" if check.passed else check.severity.upper()
        lines.append(f"    - [{mark}] {check.name}: {check.detail}")
    return lines


def lane_leak_snapshot() -> dict[str, Any]:
    return run_lane_leak_audit().to_dict()
