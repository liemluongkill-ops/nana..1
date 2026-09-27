"""Smoke tests for STAGE-9W stream readiness status.

Read-only: no live Nana runtime, Discord, TTS, VTS, OBS, API, memory write, or
game input is used.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_snapshot_is_read_only_and_covers_expected_checks():
    print("[9W Smoke] Test 1: stream readiness snapshot is read-only...")
    from nana.runtime.stream_ready_status import stream_ready_snapshot

    snap = stream_ready_snapshot()
    assert snap["phase"] == "STAGE-9W", snap
    assert snap["read_only"] is True, snap
    assert snap["can_act"] is False, snap
    assert snap["api_call"] is False, snap
    assert snap["memory_write"] is False, snap
    assert snap["tts_call"] is False, snap
    assert snap["vts_call"] is False, snap
    assert snap["obs_call"] is False, snap
    assert snap["discord_call"] is False, snap
    assert snap["game_input"] is False, snap
    assert dict(snap.get("signal_refresh") or {}).get("applied") is True, snap
    assert "timeline" in snap, snap

    keys = {check["key"] for check in snap["checks"]}
    expected = {
        "core_identity",
        "public_voice",
        "public_memory_filter",
        "public_avatar_reaction",
        "discord_bridge",
        "voice",
        "voice_delivery",
        "subtitle",
        "vts",
        "stream_state",
    }
    assert expected <= keys, keys
    print("  PASSED")


def _test_status_lines_are_human_readable():
    print("[9W Smoke] Test 2: status lines expose overall readiness...")
    from nana.runtime.stream_ready_status import stream_ready_status_lines

    lines = stream_ready_status_lines()
    text = "\n".join(lines)
    assert "Stream Ready Status (STAGE-9W)" in text, text
    assert "rehearsal_ready=" in text, text
    assert "live_ready=" in text, text
    assert "lifecycle=" in text, text
    assert "Stream timeline:" in text, text
    assert "Core identity" in text, text
    assert "Public avatar cue" in text, text
    assert "Discord bridge" in text, text
    assert "Voice delivery" in text, text
    assert "/stream-ready-status" in text, text
    assert "no TTS/VTS/OBS/Discord/API/game input" in text, text
    print("  PASSED")


def _test_lifecycle_projection_maps_raw_states():
    print("[9W Smoke] Test 3: lifecycle projection maps raw states...")
    from nana.runtime.stream_state import StreamSignals, StreamState, project_stream_lifecycle

    assert project_stream_lifecycle(StreamState.OFFLINE, StreamSignals()) == "offline"
    assert (
        project_stream_lifecycle(StreamState.OFFLINE, StreamSignals(bridge_running=True))
        == "rehearsal_candidate"
    )
    assert project_stream_lifecycle(StreamState.LIVE_IDLE, StreamSignals()) == "live"
    assert project_stream_lifecycle(StreamState.LIVE_ACTIVE, StreamSignals()) == "live"
    assert project_stream_lifecycle(StreamState.INTERMISSION, StreamSignals()) == "live_intermission"
    assert project_stream_lifecycle(StreamState.POST_STREAM, StreamSignals()) == "ending"
    assert project_stream_lifecycle(StreamState.ERROR_SAFE, StreamSignals()) == "error_safe"
    print("  PASSED")


def _test_signal_refresh_updates_snapshot_without_transition():
    print("[9W Smoke] Test 4: signal refresh updates snapshot metadata...")
    from nana.runtime.stream_signal_refresh import refresh_stream_signals
    from nana.runtime.stream_state import get_stream_state

    core = get_stream_state()
    before = core.get_state()
    refresh = refresh_stream_signals(reason="smoke_stage9w")
    after = core.get_state()
    status = core.status()
    last_signals = dict(status.get("last_signals") or {})

    assert refresh.applied is True, refresh
    assert before == after, (before, after)
    assert status.get("raw_state") == after.value, status
    assert status.get("lifecycle"), status
    assert last_signals.get("signal_source") == "smoke_stage9w", last_signals
    print("  PASSED")


def _test_status_router_handles_stream_ready_command():
    print("[9W Smoke] Test 5: status router handles /stream-ready-status...")
    from nana.cli.status_commands import handle_status_command

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_status_command(None, None, "/stream-ready-status")
    out = buf.getvalue()
    assert handled is True, out
    assert "Stream Ready Status" in out, out
    assert "Safety:" in out, out
    print("  PASSED")


def _test_help_and_registry_truth_include_command():
    print("[9W Smoke] Test 6: help and registry truth include command...")
    from nana.commands.help import print_command_help
    from nana.commands.registry import KNOWN_SLASH_COMMANDS
    from nana.commands.router_manifest import classify_command_truth

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    out = buf.getvalue()
    assert "/stream-ready-status" in out, out
    assert "/stream-ready-status" in KNOWN_SLASH_COMMANDS
    assert classify_command_truth("/stream-ready-status", known=True).status == "live"
    assert classify_command_truth("/stream-readiness-status", known=True).status == "live"
    assert classify_command_truth("/stream-preflight-status", known=True).status == "live"
    print("  PASSED")


def main():
    print("STAGE-9W Stream Ready Status - Smoke Tests")
    _test_snapshot_is_read_only_and_covers_expected_checks()
    _test_status_lines_are_human_readable()
    _test_lifecycle_projection_maps_raw_states()
    _test_signal_refresh_updates_snapshot_without_transition()
    _test_status_router_handles_stream_ready_command()
    _test_help_and_registry_truth_include_command()
    print("[9W Smoke] All tests passed.")


if __name__ == "__main__":
    main()
