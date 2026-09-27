"""Offline smoke for the combined LLM + ElevenLabs latency status."""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _FakeVoice:
    def snapshot(self):
        return {
            "last_presence_pcm_status": "none",
            "last_presence_pcm_first_byte_ms": None,
            "last_stream_playback_state": "done",
            "last_stream_time_to_first_byte_ms": 1700.0,
            "last_stream_time_to_first_audio_ms": 2200.0,
            "last_audio_played_duration_ms": 4200.0,
            "last_tts_text_len": 80,
        }


def _fake_llm_snapshot():
    return {
        "model": "gpt-5.6-terra",
        "stream": True,
        "prompt_chars": 14000,
        "max_tokens": 266,
        "first_text_ms": 1900.0,
        "tail_after_first_ms": 600.0,
        "total_ms": 2500.0,
    }


def test_combined_snapshot_math_and_output():
    import nana.brain.llmgate_client as client
    from nana.core.status_voice import (
        interaction_latency_snapshot,
        print_interaction_latency_status,
    )

    old_snapshot = client.llmgate_transport_snapshot
    client.llmgate_transport_snapshot = _fake_llm_snapshot
    try:
        snap = interaction_latency_snapshot(_FakeVoice())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            print_interaction_latency_status(_FakeVoice())
    finally:
        client.llmgate_transport_snapshot = old_snapshot

    assert snap["combined"]["earliest_provider_byte_ms"] == 3600.0, snap
    assert snap["combined"]["serial_provider_byte_ms"] == 4200.0, snap
    assert snap["combined"]["earliest_audible_ms"] == 4100.0, snap
    assert snap["combined"]["serial_audible_ms"] == 4700.0, snap
    rendered = output.getvalue()
    assert "Interaction Latency Status" in rendered, rendered
    assert "first_text=1900ms" in rendered, rendered
    assert "first_byte=1700ms" in rendered, rendered
    assert "earliest=3600ms" in rendered, rendered
    assert "serial=4200ms" in rendered, rendered
    assert "Combined audible window" in rendered, rendered
    assert "earliest=4100ms" in rendered, rendered
    assert "serial=4700ms" in rendered, rendered
    assert "no LLM, ElevenLabs, TTS, or playback call" in rendered, rendered


def test_command_registry_help_and_public_firewall():
    from nana.cli.voice_commands import handle_voice_command
    from nana.commands.help import print_command_help
    from nana.commands.registry import KNOWN_SLASH_COMMANDS
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    import nana.brain.llmgate_client as client

    old_snapshot = client.llmgate_transport_snapshot
    client.llmgate_transport_snapshot = _fake_llm_snapshot
    try:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            handled = handle_voice_command(
                _FakeVoice(),
                "/interaction-latency-status",
            )
    finally:
        client.llmgate_transport_snapshot = old_snapshot

    assert handled is True, output.getvalue()
    assert "Combined provider-byte window" in output.getvalue()
    assert "/interaction-latency-status" in KNOWN_SLASH_COMMANDS

    help_output = io.StringIO()
    with contextlib.redirect_stdout(help_output):
        print_command_help()
    assert "/interaction-latency-status" in help_output.getvalue()

    guard = get_public_stage_identity_guard()
    assert (
        guard.classify_public_input("/interaction-latency-status")
        == "backstage_command"
    )


def main():
    tests = [
        test_combined_snapshot_math_and_output,
        test_command_registry_help_and_public_firewall,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"smoke_interaction_latency_status: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
