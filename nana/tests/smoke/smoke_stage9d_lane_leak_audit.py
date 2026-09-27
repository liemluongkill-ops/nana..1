"""STAGE-9D smoke tests: lane leak audit.

Audit-only. No LLM, no writes, no live actions.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_audit_passes_core_lane_checks():
    print("[9D Smoke] Test 1: audit passes core lane checks...")
    from nana.runtime.lane_leak_audit import run_lane_leak_audit

    report = run_lane_leak_audit()
    names = {check.name: check for check in report.checks}
    assert names["public_boundary_memory_policy"].passed, names
    assert names["public_memory_filters_private_only"].passed, names
    assert names["public_firewall_blocks_backstage_audit_commands"].passed, names
    assert report.critical == 0, report.to_dict()
    print("  PASSED")


def _test_status_and_audit_lines_are_read_only():
    print("[9D Smoke] Test 2: status/audit lines are read-only...")
    from nana.runtime.lane_leak_audit import lane_leak_audit_lines, lane_leak_status_lines

    status = "\n".join(lane_leak_status_lines())
    audit = "\n".join(lane_leak_audit_lines())
    assert "STAGE-9D" in status, status
    assert "read_only=True" in status, status
    assert "no memory write" in status, status
    assert "Checks:" in audit, audit
    print("  PASSED")


def _test_stage_help_and_public_firewall_surface():
    print("[9D Smoke] Test 3: stage/help/firewall mention lane audit...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/lane-leak-audit" in help_output, help_output

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Lane leak audit:" in stage, stage

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/lane-leak-audit") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9D Lane Leak Audit — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_audit_passes_core_lane_checks,
        _test_status_and_audit_lines_are_read_only,
        _test_stage_help_and_public_firewall_surface,
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
