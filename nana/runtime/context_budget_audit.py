"""STAGE-9F: prompt/context budget audit.

Rough deterministic section-size estimator for prompt contributors. It does not
call the model and does not change prompt assembly.
"""

from __future__ import annotations

import math
from threading import Lock
from dataclasses import dataclass, field
from typing import Any


PHASE = "STAGE-9F"
_COMPILED_REPORTS = {}
_COMPILED_REPORT_LOCK = Lock()


@dataclass(frozen=True)
class BudgetSection:
    name: str
    chars: int
    tokens_est: int
    source: str
    injected: bool
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "chars": self.chars,
            "tokens_est": self.tokens_est,
            "source": self.source,
            "injected": self.injected,
            "note": self.note,
        }


@dataclass(frozen=True)
class ContextBudgetReport:
    sections: tuple[BudgetSection, ...] = field(default_factory=tuple)
    warning_threshold_tokens: int = 6000
    read_only: bool = True
    can_act: bool = False
    manifest_total_chars: int | None = None
    manifest_total_tokens: int | None = None
    measurement: str = "legacy_estimate"

    @property
    def total_chars(self) -> int:
        if self.manifest_total_chars is not None:
            return self.manifest_total_chars
        return sum(section.chars for section in self.sections if section.injected)

    @property
    def total_tokens_est(self) -> int:
        if self.manifest_total_tokens is not None:
            return self.manifest_total_tokens
        return sum(section.tokens_est for section in self.sections if section.injected)

    @property
    def warnings(self) -> int:
        return int(self.total_tokens_est >= self.warning_threshold_tokens)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "phase": PHASE,
            "total_chars": self.total_chars,
            "total_tokens_est": self.total_tokens_est,
            "warning_threshold_tokens": self.warning_threshold_tokens,
            "warnings": self.warnings,
            "read_only": self.read_only,
            "can_act": self.can_act,
            "sections": [section.to_dict() for section in self.sections],
        }
        if self.measurement != 'legacy_estimate':
            result['measurement'] = self.measurement
        return result


def _estimate_tokens(text: str) -> int:
    return int(math.ceil(len(text or "") / 4.0))


def _section(name: str, text: str, source: str, injected: bool = True, note: str = "") -> BudgetSection:
    chars = len(text or "")
    return BudgetSection(
        name=name,
        chars=chars,
        tokens_est=_estimate_tokens(text or ""),
        source=source,
        injected=injected,
        note=note,
    )


def record_compiled_budget(compiled):
    """Retain a bounded metadata report only, never messages or evidence."""
    report = build_context_budget_audit(compiled.lane.value, compiled=compiled)
    with _COMPILED_REPORT_LOCK:
        _COMPILED_REPORTS[compiled.lane.value] = report


def build_context_budget_audit(lane: str = "public_stage", *, compiled=None) -> ContextBudgetReport:
    if compiled is not None:
        from nana.runtime.context_telemetry import build_candidate_context_telemetry
        manifest = build_candidate_context_telemetry(compiled).manifest
        if manifest.lane.value != lane:
            from nana.runtime.context_contracts import ContextContractError
            raise ContextContractError('audit_lane_mismatch')
        return ContextBudgetReport(
            sections=tuple(BudgetSection(row.section_id, row.chars, row.token_estimate,
                                        row.source.owner, row.included, row.decision)
                           for row in manifest.sections),
            manifest_total_chars=manifest.input_chars,
            manifest_total_tokens=manifest.input_tokens_est,
            measurement='compiled_manifest',
        )
    if lane == 'private_owner':
        from nana import config
        if getattr(config, 'NANA_CONTEXT_PRIVATE_MODE', 'legacy') == 'canonical':
            with _COMPILED_REPORT_LOCK:
                report = _COMPILED_REPORTS.get(lane)
            if report is None:
                from nana.runtime.context_contracts import ContextContractError
                raise ContextContractError('canonical_manifest_unavailable')
            return report
    sections: list[BudgetSection] = []

    try:
        from nana.runtime.core_self import generate_core_self_block

        sections.append(_section("core_self", generate_core_self_block(lane), "core_self.generate_core_self_block"))
    except Exception as exc:
        sections.append(_section("core_self", "", "core_self", False, f"error={type(exc).__name__}: {exc}"))

    try:
        from nana.runtime.persona_spine import generate_spine_block

        sections.append(_section("persona_spine", generate_spine_block(lane), "persona_spine.generate_spine_block"))
    except Exception as exc:
        sections.append(_section("persona_spine", "", "persona_spine", False, f"error={type(exc).__name__}: {exc}"))

    try:
        from nana.runtime.public_voice_style import generate_public_voice_block

        injected = lane in {"public_stage", "public_viewer", "public"}
        sections.append(_section("public_voice_style", generate_public_voice_block(lane), "public_voice_style.generate_public_voice_block", injected=injected))
    except Exception as exc:
        sections.append(_section("public_voice_style", "", "public_voice_style", False, f"error={type(exc).__name__}: {exc}"))

    try:
        from nana.runtime.persona_boundary import resolve_persona_boundary

        if lane in {"public_stage", "public_viewer", "public"}:
            boundary = resolve_persona_boundary(viewer_name="audit_viewer", stream_mode=True, platform="discord")
        else:
            boundary = resolve_persona_boundary()
        sections.append(_section("persona_boundary", boundary.prompt_block, "persona_boundary.resolve_persona_boundary"))
    except Exception as exc:
        sections.append(_section("persona_boundary", "", "persona_boundary", False, f"error={type(exc).__name__}: {exc}"))

    try:
        from nana.runtime.mood_continuity import format_mood_prompt_block

        sections.append(_section("mood_continuity", format_mood_prompt_block(lane), "mood_continuity.format_mood_prompt_block"))
    except Exception as exc:
        sections.append(_section("mood_continuity", "", "mood_continuity", False, f"error={type(exc).__name__}: {exc}"))

    try:
        from nana.runtime.intention_planner import get_intention_planner

        plan = get_intention_planner().snapshot().get("last_plan") or {}
        hint = str(plan.get("prompt_hint") or "")
        text = "\n".join([
            "INTENTION PLANNER (STAGE-9B)",
            f"- active={plan.get('category')}:{plan.get('intention')}",
            f"- priority={plan.get('priority')} tone={plan.get('tone')}",
            f"- hint={hint}",
            "- This is content bias only, not action permission.",
        ])
        sections.append(_section("intention_planner", text, "intention_planner.snapshot"))
    except Exception as exc:
        sections.append(_section("intention_planner", "", "intention_planner", False, f"error={type(exc).__name__}: {exc}"))

    try:
        from nana.runtime.memory_grounding import MemoryEvidence

        evidence = MemoryEvidence(
            status="user_claim_only",
            confidence=0.25,
            lane_visible_to="public_safe" if lane.startswith("public") else "private_only",
            snippets=[],
            notes="audit sample",
        )
        sections.append(_section("memory_grounding", evidence.to_prompt_block(), "memory_grounding.MemoryEvidence"))
    except Exception as exc:
        sections.append(_section("memory_grounding", "", "memory_grounding", False, f"error={type(exc).__name__}: {exc}"))

    try:
        from nana.runtime.public_stage_identity import build_public_stage_prompt_block

        injected = lane in {"public_stage", "public_viewer", "public"}
        text = build_public_stage_prompt_block(viewer_name="audit_viewer", stage_mood="audit", room_topic="quiet_room")
        sections.append(_section("public_stage_identity", text, "public_stage_identity.build_public_stage_prompt_block", injected=injected))
    except Exception as exc:
        sections.append(_section("public_stage_identity", "", "public_stage_identity", False, f"error={type(exc).__name__}: {exc}"))

    sections.append(_section(
        "post_stream_lessons",
        "",
        "post_stream_lessons",
        injected=False,
        note="lessons are stored in memory lane only; not injected into prompt by STAGE-9C-B",
    ))

    return ContextBudgetReport(sections=tuple(sections))


def context_budget_status_lines(lane: str = "public_stage") -> list[str]:
    report = build_context_budget_audit(lane)
    return [
        "📏 Context Budget Audit (STAGE-9F)",
        f"  Mode: {'compiled-manifest' if report.measurement == 'compiled_manifest' else 'estimate-only'} | lane={lane} | read_only={report.read_only} | can_act={report.can_act}",
        f"  Total injected estimate: chars={report.total_chars} | tokens≈{report.total_tokens_est} | threshold={report.warning_threshold_tokens}",
        f"  Sections: {len(report.sections)} | warnings={report.warnings}",
        "  Commands: /context-budget-status [lane] | /context-budget-audit [lane]",
        "  Safety: no LLM | no prompt mutation | no memory/config write",
    ]


def context_budget_audit_lines(lane: str = "public_stage") -> list[str]:
    report = build_context_budget_audit(lane)
    lines = context_budget_status_lines(lane)
    lines.append("  Sections:")
    for section in report.sections:
        state = "injected" if section.injected else "not_injected"
        note = f" | {section.note}" if section.note else ""
        lines.append(
            f"    - {section.name}: {state} | chars={section.chars} | tokens≈{section.tokens_est} | source={section.source}{note}"
        )
    if report.warnings:
        lines.append("  Warning: estimated prompt sections are above threshold; consider compression before expanding prompts.")
    return lines


def context_budget_snapshot(lane: str = "public_stage") -> dict[str, Any]:
    return build_context_budget_audit(lane).to_dict()
