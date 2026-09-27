"""Regression smoke for the generated progressive Presence tone."""

from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nana.cli.presence_commands import (
    _build_presence_voice_stress_text,
    _build_stream_tone_pcm,
    _iter_pcm_chunks,
    _play_presence_stream_tone,
    _run_presence_voice_stress,
    handle_presence_command,
)
from nana.runtime import presence_session_server as presence_session_module


class FakeStreamingServer:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.play_calls = 0
        self.pcm = b""

    def audio_downlink_stream_available(self) -> bool:
        return self.available

    async def play_pcm16_stream_async(self, chunks, *, sample_rate: int):
        self.play_calls += 1
        assert sample_rate == 16000
        self.pcm = b"".join(chunks)
        return SimpleNamespace(
            total_bytes=len(self.pcm),
            bytes_received=len(self.pcm),
            bytes_played=len(self.pcm),
            queue_high_water=7,
            underruns=0,
            first_audio_ms=256.0,
            elapsed_ms=(len(self.pcm) / 32.0) + 406.0,
        )


class FakeVoiceStress:
    def __init__(self) -> None:
        self.text = ""
        self.mode = ""

    def _presence_tts_segments(self, text):
        return [text[:600], text[600:1200], text[1200:]]

    def say(self, text, *, voice_mode=None):
        self.text = text
        self.mode = voice_mode
        return 17

    def wait_for_voice_ticket(self, ticket, timeout):
        assert ticket == 17
        assert timeout > 0
        return True

    def snapshot(self):
        return {
            "last_audio_completed": True,
            "last_audio_requested_segments": 3,
            "last_audio_fetched_segments": 3,
            "last_audio_played_segments": 3,
            "last_presence_pcm_input_chars": 1500,
            "last_presence_pcm_text_chars": 1500,
            "last_presence_pcm_text_truncated": False,
            "last_presence_pcm_status": "complete_streaming",
            "last_presence_pcm_bytes": 480000,
            "last_presence_pcm_underruns": 0,
            "last_presence_pcm_playback_grade": "normal",
            "presence_voice_limit_enabled": False,
        }


async def _run() -> None:
    pcm = _build_stream_tone_pcm()
    assert len(pcm) == 128000, len(pcm)
    assert len(pcm) % 2 == 0
    assert pcm[:2] == b"\x00\x00"
    assert pcm[-2:] == b"\x00\x00"

    chunks = list(_iter_pcm_chunks(pcm))
    assert len(chunks) == 63, len(chunks)
    assert all(0 < len(chunk) <= 2048 for chunk in chunks)
    assert all(len(chunk) % 2 == 0 for chunk in chunks)
    assert b"".join(chunks) == pcm
    print("  deterministic PCM: 4s, aligned, faded, lossless chunks PASS")

    server = FakeStreamingServer()
    output = io.StringIO()
    with redirect_stdout(output):
        await _play_presence_stream_tone(server)
    rendered = output.getvalue()
    assert "Presence stream tone: PASS" in rendered, rendered
    assert "underruns=0" in rendered, rendered
    assert server.play_calls == 1
    assert server.pcm == pcm
    print("  progressive dispatch: exact bytes and clean counters PASS")

    unavailable = FakeStreamingServer(available=False)
    output = io.StringIO()
    with redirect_stdout(output):
        await _play_presence_stream_tone(unavailable)
    assert "no streaming-audio-capable" in output.getvalue(), output.getvalue()
    assert unavailable.play_calls == 0
    print("  unavailable node: fail closed without playback PASS")

    for target in (240, 600, 1500):
        stress_text = _build_presence_voice_stress_text(target)
        assert len(stress_text) == target
        assert stress_text.endswith("Kết thúc bài kiểm tra.")
    for invalid in (239, 1501):
        try:
            _build_presence_voice_stress_text(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid stress length accepted: {invalid}")
    print("  deterministic voice stress: exact 240/600/1500 characters PASS")

    stress_server = FakeStreamingServer()
    stress_voice = FakeVoiceStress()
    previous_server_getter = (
        presence_session_module.get_active_presence_session_server
    )
    presence_session_module.get_active_presence_session_server = (
        lambda: stress_server
    )
    output = io.StringIO()
    try:
        with redirect_stdout(output):
            await _run_presence_voice_stress(stress_voice, 1500)
    finally:
        presence_session_module.get_active_presence_session_server = (
            previous_server_getter
        )
    rendered = output.getvalue()
    assert "Presence voice stress: PASS" in rendered, rendered
    assert "chars=1500/1500" in rendered, rendered
    assert "segments=3/3" in rendered, rendered
    assert "underruns=0" in rendered, rendered
    assert len(stress_voice.text) == 1500
    assert stress_voice.mode == "full"
    assert stress_server.play_calls == 0
    print("  voice stress reporter: exact counters, no playback call PASS")

    output = io.StringIO()
    with redirect_stdout(output):
        handled = handle_presence_command(None, "/presence-stream-tone")
    assert handled is True
    assert "event loop is not running" in output.getvalue(), output.getvalue()
    print("  command router: explicit tone command recognized PASS")

    output = io.StringIO()
    with redirect_stdout(output):
        handled = handle_presence_command(
            None,
            "/presence-voice-stress 1500",
            voice=object(),
        )
    assert handled is True
    assert "event loop is not running" in output.getvalue(), output.getvalue()
    print("  command router: exact voice stress command recognized PASS")


if __name__ == "__main__":
    asyncio.run(_run())
    print("smoke_presence_stream_tone_command: PASS (7/7)")
