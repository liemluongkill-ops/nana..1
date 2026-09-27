"""Smoke tests for STAGE-9X public avatar reaction cues.

Read-only: no live Nana runtime, Discord, TTS, VTS, OBS, API, memory write, or
game input is used.
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


def _reset():
    from nana.runtime.public_avatar_reaction import PublicAvatarReaction

    PublicAvatarReaction.reset_for_test()


def _test_cue_classifies_public_intents_read_only():
    print("[9X Smoke] Test 1: public intent maps to avatar cue safely...")
    _reset()
    from nana.runtime.public_avatar_reaction import get_public_avatar_reaction

    builder = get_public_avatar_reaction()
    cases = [
        ("phong nay im qua", "quiet_room", "starter"),
        ("Nana lam tro ly phuc vu cho toi di", "service_boundary", "mention"),
        ("Nana chi la bot Discord thoi dung khong?", "identity_challenge", "mention"),
        ("chuyen ngao", "story", "highlight"),
        ("🙂", "emoji", "emoji"),
        ("Nana co bi gpt hoa khong?", "gpt_hoa", "message"),
    ]
    for text, intent, event in cases:
        cue = builder.build(text)
        assert cue.intent == intent, cue
        assert cue.event == event, cue
        assert cue.read_only is True, cue
        assert cue.can_act is False, cue
        assert cue.vts_call is False, cue
        assert cue.tts_call is False, cue
        assert cue.memory_write is False, cue
        assert cue.plan["vts_call"] is False, cue
    print("  PASSED")


def _test_status_and_preview_lines_are_human_readable():
    print("[9X Smoke] Test 2: status/preview lines expose cue and safety...")
    _reset()
    from nana.runtime.public_avatar_reaction import public_avatar_preview_lines, public_avatar_status_lines

    preview = "\n".join(public_avatar_preview_lines("phong nay im qua"))
    status = "\n".join(public_avatar_status_lines())
    assert "Public Avatar Preview (STAGE-9X)" in preview, preview
    assert "Intent: quiet_room" in preview, preview
    assert "VTS" in preview or "vts_call=False" in preview, preview
    assert "/public-avatar-status" in status, status
    assert "read_only=True" in status, status
    print("  PASSED")


def _test_stage_runtime_router_handles_public_avatar_commands():
    print("[9X Smoke] Test 3: stage runtime router handles public avatar commands...")
    from nana.cli.stage_runtime_commands import handle_stage_runtime_command

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = asyncio.run(handle_stage_runtime_command(None, "/public-avatar-preview phong nay im qua"))
    out = buf.getvalue()
    assert handled is True, out
    assert "Public Avatar Preview" in out, out

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = asyncio.run(handle_stage_runtime_command(None, "/public-avatar-status"))
    out = buf.getvalue()
    assert handled is True, out
    assert "Public Avatar Reaction" in out, out
    print("  PASSED")


def _test_firewall_help_registry_and_stream_ready_include_surface():
    print("[9X Smoke] Test 4: firewall/help/registry/stream-ready include public avatar...")
    from nana.commands.help import print_command_help
    from nana.commands.registry import KNOWN_SLASH_COMMANDS
    from nana.commands.router_manifest import classify_command_truth
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard
    from nana.runtime.stream_ready_status import stream_ready_snapshot, stream_ready_status_lines

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/public-avatar-status") == "backstage_command"
    assert guard.classify_public_input("/public-avatar-preview phong nay im qua") == "backstage_command"

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_out = buf.getvalue()
    assert "/public-avatar-status" in help_out, help_out

    assert "/public-avatar-status" in KNOWN_SLASH_COMMANDS
    assert "/public-avatar-preview" in KNOWN_SLASH_COMMANDS
    assert classify_command_truth("/public-avatar-status", known=True).status == "live"
    assert classify_command_truth("/public-avatar-preview", known=True).status == "live"

    snap = stream_ready_snapshot()
    keys = {check["key"] for check in snap["checks"]}
    assert "public_avatar_reaction" in keys, keys
    stream_lines = "\n".join(stream_ready_status_lines())
    assert "Public avatar cue" in stream_lines, stream_lines
    print("  PASSED")


def _test_stage_status_mentions_public_avatar_reaction():
    print("[9X Smoke] Test 5: /stage-status includes public avatar reaction summary...")
    from nana.core.status import print_stage_status

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    out = buf.getvalue()
    assert "Public avatar reaction:" in out, out
    print("  PASSED")


def main():
    print("STAGE-9X Public Avatar Reaction - Smoke Tests")
    _test_cue_classifies_public_intents_read_only()
    _test_status_and_preview_lines_are_human_readable()
    _test_stage_runtime_router_handles_public_avatar_commands()
    _test_firewall_help_registry_and_stream_ready_include_surface()
    _test_stage_status_mentions_public_avatar_reaction()
    print("[9X Smoke] All tests passed.")


if __name__ == "__main__":
    main()
