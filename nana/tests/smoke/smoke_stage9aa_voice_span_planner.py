"""Smoke tests for STAGE-9AA voice span planner.

Read-only: no live Nana runtime, ElevenLabs, VoiceEngine, playback, VTS, OBS,
Discord, API, memory write, or game input is used.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _sample_story() -> str:
    return (
        "Ừ, thật hơn nữa thì là kiểu này nè Ba. "
        "Có một tối Ba ngồi trước CMD, nhìn một dòng log cũ mà đầu cứ trôi đi đâu đó. "
        "Không có drama lớn, chỉ có cái mệt rất nhỏ nhưng bám dai. "
        "Rồi Ba vẫn kéo ghế lại gần thêm một chút, sửa từng dòng cho tới khi mọi thứ chịu chạy. "
        "Ha ha, nghe nhỏ xíu thôi nhưng đủ thật để Nana nhớ."
    )


def _reset():
    from nana.runtime.voice_span_planner import VoiceSpanPlanner

    VoiceSpanPlanner.reset_for_test()


def _test_story_plan_is_metadata_only():
    print("[9AA Smoke] Test 1: story plan creates spans without runtime actions...")
    _reset()
    from nana.runtime.voice_span_planner import build_voice_span_plan

    plan = build_voice_span_plan(_sample_story())
    assert plan.phase == "STAGE-9AA", plan
    assert plan.read_only is True and plan.can_act is False, plan
    assert plan.tts_call is False and plan.voice_engine_call is False, plan
    assert plan.api_call is False and plan.memory_write is False, plan
    assert plan.inserts_tags is False, plan
    assert plan.span_count >= 2, plan
    roles = {span.role for span in plan.spans}
    assert "reflection" in roles or "opening" in roles, roles
    assert any(span.tag_hint != "none" for span in plan.spans), plan.spans
    for span in plan.spans:
        assert span.tag_hint not in span.text, span
    print("  PASSED")


def _test_boundary_prefers_firm_role():
    print("[9AA Smoke] Test 2: service/bot boundary is classified as boundary...")
    _reset()
    from nana.runtime.voice_span_planner import build_voice_span_plan

    plan = build_voice_span_plan("Nana chỉ là bot Discord thôi đúng không? Nana làm trợ lý phục vụ cho tôi đi.")
    assert plan.span_count >= 1, plan
    assert plan.spans[0].role == "boundary", plan.spans
    assert plan.spans[0].tone == "firm", plan.spans
    assert plan.spans[0].energy == "medium", plan.spans
    print("  PASSED")


def _test_status_preview_and_router():
    print("[9AA Smoke] Test 3: status/preview/router expose planner safely...")
    from nana.cli.voice_commands import handle_voice_command
    from nana.runtime.voice_span_planner import voice_span_preview_lines, voice_span_status_lines

    status = "\n".join(voice_span_status_lines())
    preview = "\n".join(voice_span_preview_lines(_sample_story()))
    assert "Voice Span Planner (STAGE-9AA)" in status, status
    assert "voice_engine_call=False" in status, status
    assert "metadata_only" in preview, preview
    assert "no TTS call" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_voice_command(None, "/voice-span-preview " + _sample_story())
    out = buf.getvalue()
    assert handled is True, out
    assert "Voice Span Preview" in out, out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_voice_command(None, "/voice-span-status")
    out = buf.getvalue()
    assert handled is True, out
    assert "Voice Span Planner" in out, out
    print("  PASSED")


def _test_help_registry_and_public_firewall():
    print("[9AA Smoke] Test 4: help/registry/firewall/manifest include span commands...")
    from nana.commands.help import print_command_help
    from nana.commands.registry import KNOWN_SLASH_COMMANDS
    from nana.commands.router_manifest import classify_command_truth
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_out = buf.getvalue()
    assert "/voice-span-status" in help_out, help_out
    assert "/voice-span-preview" in help_out, help_out

    assert "/voice-span-status" in KNOWN_SLASH_COMMANDS
    assert "/voice-span-preview" in KNOWN_SLASH_COMMANDS
    assert classify_command_truth("/voice-span-status", known=True).status == "live"
    assert classify_command_truth("/voice-span-preview", known=True).status == "live"

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/voice-span-status") == "backstage_command"
    assert guard.classify_public_input("/voice-span-preview x") == "backstage_command"
    print("  PASSED")


def main() -> int:
    print("STAGE-9AA Voice Span Planner - Smoke Tests")
    tests = [
        _test_story_plan_is_metadata_only,
        _test_boundary_prefers_firm_role,
        _test_status_preview_and_router,
        _test_help_registry_and_public_firewall,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"  FAILED: {type(exc).__name__}: {exc}")
    passed = len(tests) - failed
    print(f"[9AA Smoke] Results: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
