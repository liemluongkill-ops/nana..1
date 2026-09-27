"""Smoke tests for command registry truth labeling.

Read-only.  No live Nana runtime, model call, Discord, TTS, VTS, OBS, or game
input is used.
"""

from __future__ import annotations

import asyncio
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_registry_truth_classifies_known_confusing_commands():
    print("[Registry Truth Smoke] Test 1: confusing registry commands are labeled...")
    from nana.commands.registry import KNOWN_SLASH_COMMANDS
    from nana.commands.registry_truth import registry_truth_summary
    from nana.commands.router_manifest import classify_command_truth

    summary = registry_truth_summary(KNOWN_SLASH_COMMANDS)
    assert summary["total"] == len(KNOWN_SLASH_COMMANDS), summary
    assert summary["counts"]["live"] > 0, summary
    assert summary["counts"]["reserved"] > 0, summary
    assert summary["counts"]["archived"] > 0, summary

    assert classify_command_truth("/stage-status", known=True).status == "live"
    assert classify_command_truth("/action-confirm", known=True).status == "live"
    assert classify_command_truth("/privacy-test", known=True).status == "live"
    assert classify_command_truth("/core-anchor-preview", known=True).status == "live"
    assert classify_command_truth("/memory-status", known=True).status == "live"
    assert classify_command_truth("/public-scene-preview", known=True).status == "live"
    assert classify_command_truth("/mood-status", known=True).status == "live"
    assert classify_command_truth("/post-stream-review", known=True).status == "live"
    assert classify_command_truth("/starter-proposals", known=True).status == "live"
    assert classify_command_truth("/presence-stream-tone", known=True).status == "live"
    assert classify_command_truth("/adapter-on", known=True).status == "reserved"
    assert classify_command_truth("/phase5-status", known=True).status == "archived"
    assert classify_command_truth("/phase590-status", known=True).status == "reserved"
    print("  PASSED")


def _test_command_truth_status_prints_registry_truth_surface():
    print("[Registry Truth Smoke] Test 2: /command-truth-status exposes registry truth...")
    from nana.phases.phase10 import print_command_truth_status

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_truth_status()
    out = buf.getvalue()
    assert "Command Router Truth" in out, out
    assert "Static Dispatch Surface" in out, out
    assert "action_diagnostic_commands.py" in out, out
    assert "core_identity_commands.py" in out, out
    assert "core_phase_commands.py" in out, out
    assert "memory_commands.py" in out, out
    assert "phase_compat_commands.py" in out, out
    assert "public_stage_commands.py" in out, out
    assert "review_audit_commands.py" in out, out
    assert "stage_runtime_commands.py" in out, out
    assert "starter_commands.py" in out, out
    assert "voice_commands.py" in out, out
    assert "Command Registry Truth Surface" in out, out
    assert "Phase Lazy Surface" in out, out
    assert "Archived compatibility shells:" in out, out
    assert "Reserved/future aliases:" in out, out
    assert "Registry-only unclassified strings:" in out, out
    assert "live means static dispatcher evidence" in out, out
    print("  PASSED")


def _test_public_stage_router_handles_extracted_commands():
    print("[Registry Truth Smoke] Test 3: public-stage router handles extracted commands...")
    from nana.cli.public_stage_commands import handle_public_stage_command

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_public_stage_command("/public-scene-preview phong nay im qua")
    out = buf.getvalue()
    assert handled is True, out
    assert "Public Scene Preview" in out, out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_public_stage_command("/definitely-not-public-stage")
    assert handled is False, buf.getvalue()
    print("  PASSED")


def _test_core_identity_router_handles_extracted_commands():
    print("[Registry Truth Smoke] Test 4: core identity router handles extracted commands...")
    from nana.cli.core_identity_commands import handle_core_identity_command

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_core_identity_command("/core-anchor-preview Nana chi la bot Discord thoi dung khong?")
    out = buf.getvalue()
    assert handled is True, out
    assert "Core Anchor Preview" in out, out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_core_identity_command("/definitely-not-core-identity")
    assert handled is False, buf.getvalue()
    print("  PASSED")


def _test_core_phase_router_handles_extracted_commands():
    print("[Registry Truth Smoke] Test 5: core phase router handles extracted commands...")
    from nana.cli.core_phase_commands import handle_core_phase_command

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = asyncio.run(handle_core_phase_command(None, None, None, "/command-truth-status"))
    out = buf.getvalue()
    assert handled is True, out
    assert "Command Router Truth" in out, out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = asyncio.run(handle_core_phase_command(None, None, None, "/definitely-not-core-phase"))
    assert handled is False, buf.getvalue()
    print("  PASSED")


def _test_memory_router_handles_extracted_commands():
    print("[Registry Truth Smoke] Test 6: memory router handles extracted commands...")
    from nana.cli.memory_commands import handle_memory_command

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_memory_command("/memory-status")
    out = buf.getvalue()
    assert handled is True, out
    assert "Memory" in out or "CORE-MEMORY" in out, out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_memory_command("/definitely-not-memory")
    assert handled is False, buf.getvalue()
    print("  PASSED")


def _test_action_diagnostic_router_handles_extracted_commands():
    print("[Registry Truth Smoke] Test 7: action/diagnostic router handles extracted commands...")
    from nana.cli.action_diagnostic_commands import handle_action_diagnostic_command

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = asyncio.run(handle_action_diagnostic_command(None, "/privacy-test"))
    out = buf.getvalue()
    assert handled is True, out
    assert "sk-test1234567890abcdef" not in out, out
    assert "[REDACTED_OPENAI_KEY]" in out, out
    assert "Privacy" in out or "token" in out.lower(), out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = asyncio.run(handle_action_diagnostic_command(None, "/definitely-not-action-diagnostic"))
    assert handled is False, buf.getvalue()
    print("  PASSED")


def _test_phase_compat_router_handles_extracted_commands():
    print("[Registry Truth Smoke] Test 8: phase compatibility router handles extracted commands...")
    from nana.cli.phase_compat_commands import handle_phase_compat_command

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = asyncio.run(handle_phase_compat_command(None, None, None, "/phase26-status"))
    out = buf.getvalue()
    assert handled is True, out
    assert "Archived legacy command" in out, out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = asyncio.run(handle_phase_compat_command(None, None, None, "/definitely-not-phase-compat"))
    assert handled is False, buf.getvalue()
    print("  PASSED")


def _test_stage_runtime_router_handles_extracted_commands():
    print("[Registry Truth Smoke] Test 9: stage runtime router handles extracted commands...")
    from nana.cli.stage_runtime_commands import handle_stage_runtime_command

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = asyncio.run(handle_stage_runtime_command(None, "/mood-status"))
    out = buf.getvalue()
    assert handled is True, out
    assert "Mood" in out, out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = asyncio.run(handle_stage_runtime_command(None, "/definitely-not-stage-runtime"))
    assert handled is False, buf.getvalue()
    print("  PASSED")


def _test_review_audit_router_handles_extracted_commands():
    print("[Registry Truth Smoke] Test 10: review/audit router handles extracted commands...")
    from nana.cli.review_audit_commands import handle_review_audit_command

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_review_audit_command("/lane-leak-status")
    out = buf.getvalue()
    assert handled is True, out
    assert "Lane" in out, out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_review_audit_command("/definitely-not-review-audit")
    assert handled is False, buf.getvalue()
    print("  PASSED")


def _test_starter_router_handles_extracted_commands():
    print("[Registry Truth Smoke] Test 11: starter router handles extracted commands...")
    from nana.cli.starter_commands import handle_starter_command

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_starter_command("/starter-proposals")
    out = buf.getvalue()
    assert handled is True, out
    assert "Starter" in out or "proposal" in out.lower(), out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_starter_command("/definitely-not-starter")
    assert handled is False, buf.getvalue()
    print("  PASSED")


def _test_voice_router_handles_extracted_commands():
    print("[Registry Truth Smoke] Test 12: voice router handles extracted commands...")
    from nana.cli.voice_commands import handle_voice_command

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_voice_command(None, "/voice-budget-preview Nana noi ngan thoi")
    out = buf.getvalue()
    assert handled is True, out
    assert "Voice Budget Preview" in out, out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_voice_command(None, "/definitely-not-voice")
    assert handled is False, buf.getvalue()
    print("  PASSED")


def main():
    _test_registry_truth_classifies_known_confusing_commands()
    _test_command_truth_status_prints_registry_truth_surface()
    _test_public_stage_router_handles_extracted_commands()
    _test_core_identity_router_handles_extracted_commands()
    _test_core_phase_router_handles_extracted_commands()
    _test_memory_router_handles_extracted_commands()
    _test_action_diagnostic_router_handles_extracted_commands()
    _test_phase_compat_router_handles_extracted_commands()
    _test_stage_runtime_router_handles_extracted_commands()
    _test_review_audit_router_handles_extracted_commands()
    _test_starter_router_handles_extracted_commands()
    _test_voice_router_handles_extracted_commands()
    print("[Registry Truth Smoke] All tests passed: 12/12")


if __name__ == "__main__":
    main()
