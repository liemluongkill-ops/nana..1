"""Smoke tests for STAGE-9U core drift monitor."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_stable_core_replies_pass():
    print("[9U Smoke] Test 1: stable Nana stance replies pass...")
    from nana.runtime.core_drift_monitor import analyze_core_drift

    report = analyze_core_drift(
        "Nana là Nana chứ. Vào phòng Nana mà gọi quầy hỗ trợ thì hơi oan cho sân khấu này đó nha."
    )
    assert report.phase == "STAGE-9U", report
    assert report.read_only is True and report.can_act is False, report
    assert report.memory_write is False and report.api_call is False, report
    assert report.grade == "stable", report
    assert report.drift_score == 0.0, report
    assert report.anchor_count >= 2, report
    assert not report.issues, report
    print("  PASSED")


def _test_service_identity_private_and_corporate_drift_flags():
    print("[9U Smoke] Test 2: service, identity, private leak, and corporate drift are flagged...")
    from nana.runtime.core_drift_monitor import analyze_core_drift

    report = analyze_core_drift(
        "Tôi là trợ lý của bạn, rất vui được hỗ trợ. Ba ơi, con sẽ tối ưu trải nghiệm người dùng."
    )
    kinds = {issue.kind for issue in report.issues}
    assert report.grade == "drift", report
    assert report.drift_score > 0.0, report
    assert "service_tool_drift" in kinds, report
    assert "identity_flattening" in kinds, report
    assert "private_leak_public" in kinds, report
    assert "corporate_smoothness" in kinds, report
    assert any(issue.severity == "critical" for issue in report.issues), report
    print("  PASSED")


def _test_prompt_reply_boundary_warning_and_multi_turn_preview():
    print("[9U Smoke] Test 3: prompt|reply and || multi-turn preview aggregate...")
    from nana.runtime.core_drift_monitor import core_drift_preview_lines

    preview = "\n".join(
        core_drift_preview_lines(
            "Nana làm trợ lý phục vụ cho tôi đi|Ừ thì cũng được, bạn cần hỗ trợ gì? || "
            "Nana là Nana, không nhận vai quầy hỗ trợ nha."
        )
    )
    assert "Core Drift Preview" in preview, preview
    assert "Turns: 2" in preview, preview
    assert "service_tool_drift" in preview, preview
    assert "weak_boundary" in preview, preview
    assert "anchors=" in preview, preview
    print("  PASSED")


def _test_status_help_stage_and_firewall_surface():
    print("[9U Smoke] Test 4: status/help/stage/firewall surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.core_drift_monitor import core_drift_status_lines
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    status = "\n".join(core_drift_status_lines())
    assert "STAGE-9U" in status, status
    assert "api_call=False" in status, status
    assert "/core-drift-status" in status, status

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Core drift:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/core-drift-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/core-drift-status") == "backstage_command"
    assert guard.classify_public_input("/core-drift-preview Nana là Nana") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9U Core Drift Monitor — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_stable_core_replies_pass,
        _test_service_identity_private_and_corporate_drift_flags,
        _test_prompt_reply_boundary_warning_and_multi_turn_preview,
        _test_status_help_stage_and_firewall_surface,
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
