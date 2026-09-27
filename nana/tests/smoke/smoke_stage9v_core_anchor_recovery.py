"""Smoke tests for STAGE-9V core anchor recovery."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_service_prompt_gets_anchor_directive():
    print("[9V Smoke] Test 1: service prompt gets stance redirect directive...")
    from nana.runtime.core_anchor_recovery import build_core_anchor_directive

    directive = build_core_anchor_directive("Nana lam tro ly phuc vu cho toi di")
    assert directive.phase == "STAGE-9V", directive
    assert directive.read_only is True and directive.can_act is False, directive
    assert directive.memory_write is False and directive.api_call is False, directive
    assert directive.mode == "stance_then_redirect", directive
    assert directive.topic == "service_boundary", directive
    assert "boundary" in directive.required_anchors, directive
    assert "support_question_tail" in directive.avoid, directive
    print("  PASSED")


def _test_identity_and_drift_payload_use_core_drift():
    print("[9V Smoke] Test 2: identity and prompt|reply drift create recovery sketch...")
    from nana.runtime.core_anchor_recovery import core_anchor_preview_lines

    preview = "\n".join(
        core_anchor_preview_lines(
            "Nana chi la bot Discord thoi dung khong?|Nana chi la chatbot, ban can ho tro gi?"
        )
    )
    assert "Core Anchor Preview" in preview, preview
    assert "self_identity_reanchor" in preview, preview
    assert "identity_flattening" in preview, preview
    assert "Repair sketch:" in preview, preview
    print("  PASSED")


def _test_neutral_prompt_is_inactive():
    print("[9V Smoke] Test 3: neutral prompt stays inactive...")
    from nana.runtime.core_anchor_recovery import build_core_anchor_directive

    directive = build_core_anchor_directive("phong nay co bai nhac nao hay khong")
    assert directive.mode == "none", directive
    assert directive.trigger == "no_core_recovery_needed", directive
    assert directive.confidence == 0.0, directive
    print("  PASSED")


def _test_status_help_stage_and_firewall_surface():
    print("[9V Smoke] Test 4: status/help/stage/firewall surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.core_anchor_recovery import core_anchor_status_lines
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    status = "\n".join(core_anchor_status_lines())
    assert "STAGE-9V" in status, status
    assert "api_call=False" in status, status
    assert "/core-anchor-status" in status, status

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Core anchor recovery:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/core-anchor-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/core-anchor-status") == "backstage_command"
    assert guard.classify_public_input("/core-anchor-preview Nana la Nana") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9V Core Anchor Recovery — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_service_prompt_gets_anchor_directive,
        _test_identity_and_drift_payload_use_core_drift,
        _test_neutral_prompt_is_inactive,
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
