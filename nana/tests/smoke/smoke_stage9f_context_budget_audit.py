"""STAGE-9F smoke tests: context budget audit.

Estimate-only. No LLM and no prompt mutation.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_public_budget_has_expected_sections():
    print("[9F Smoke] Test 1: public budget has expected sections...")
    from nana.runtime.context_budget_audit import build_context_budget_audit

    report = build_context_budget_audit("public_stage")
    names = {section.name: section for section in report.sections}
    for name in ("core_self", "persona_spine", "persona_boundary", "mood_continuity", "memory_grounding", "public_stage_identity"):
        assert name in names, names
        assert names[name].chars > 0, names[name]
    assert names["public_stage_identity"].injected is True, names["public_stage_identity"]
    assert names["post_stream_lessons"].injected is False, names["post_stream_lessons"]
    assert report.total_tokens_est > 0, report
    print("  PASSED")


def _test_private_budget_does_not_count_public_stage_block():
    print("[9F Smoke] Test 2: private budget does not count public-stage block...")
    from nana.runtime.context_budget_audit import build_context_budget_audit

    report = build_context_budget_audit("private_owner")
    public_stage = next(section for section in report.sections if section.name == "public_stage_identity")
    assert public_stage.injected is False, public_stage
    print("  PASSED")


def _test_lines_stage_help_and_firewall_surface():
    print("[9F Smoke] Test 3: lines/stage/help/firewall surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.context_budget_audit import context_budget_audit_lines, context_budget_status_lines
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    status = "\n".join(context_budget_status_lines())
    audit = "\n".join(context_budget_audit_lines())
    assert "STAGE-9F" in status, status
    assert "estimate-only" in status, status
    assert "Sections:" in audit, audit

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Context budget audit:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/context-budget-audit" in help_output, help_output

    assert get_public_stage_identity_guard().classify_public_input("/context-budget-audit") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9F Context Budget Audit — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_public_budget_has_expected_sections,
        _test_private_budget_does_not_count_public_stage_block,
        _test_lines_stage_help_and_firewall_surface,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"  FAILED: {type(exc).__name__}: {exc}")
    passed = len(tests) - failed
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_all())
