"""Smoke tests for voice playback prebuffer/seam timing.

Read-only: no live Nana runtime, ElevenLabs, playback, TTS, VTS, OBS,
Discord, memory write, or game input is used.  The tests call the
sequencer with fake fetch/playback hooks only.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _FakeLipsync:
    def __init__(self, play_seconds: float = 0.03):
        self.play_seconds = play_seconds
        self.played = []

    def play_audio_nonblocking(self, audio, on_done=None):
        self.played.append(audio)

        def _finish():
            time.sleep(self.play_seconds)
            if on_done is not None:
                on_done()

        threading.Thread(target=_finish, daemon=True).start()


def _make_engine(chunks, delays):
    from nana.voice.engine import VoiceEngine

    engine = VoiceEngine.__new__(VoiceEngine)
    engine.state_lock = threading.Lock()
    engine.state = {}
    engine.lipsync = _FakeLipsync()
    engine._split_tts_text = lambda text, **kwargs: list(chunks)

    def _fake_fetch(chunk, profile):
        time.sleep(float(delays.get(chunk, 0.0)))
        return f"audio-{chunk}".encode("utf-8")

    engine._tts_fetch_audio = _fake_fetch
    return engine


def _test_multichunk_prebuffers_second_chunk_before_playback():
    print("[Voice Prebuffer Smoke] Test 1: prebuffers chunk 0+1 before playback...")
    engine = _make_engine(["A", "B"], {"A": 0.01, "B": 0.20})

    engine._tts_and_lipsync("synthetic multi chunk text", voice_mode="full")

    assert engine.lipsync.played == [b"audio-A", b"audio-B"], engine.lipsync.played
    assert engine.state.get("last_tts_strategy") == "sequencer_play", engine.state
    assert engine.state.get("last_tts_chunks") == 2, engine.state
    assert engine.state.get("last_tts_prebuffer_chunks") == 2, engine.state
    assert 100.0 <= float(engine.state.get("last_tts_prebuffer_ms") or 0.0) <= 800.0, engine.state
    assert float(engine.state.get("last_tts_max_seam_wait_ms") or 0.0) < 100.0, engine.state
    assert engine.state.get("last_tts_playback_policy") == "prebuffer", engine.state
    assert engine.state.get("last_tts_seam_grade") in {"good", "none"}, engine.state
    print("  PASSED")


def _test_single_chunk_skips_prebuffer():
    print("[Voice Prebuffer Smoke] Test 2: single chunk skips prebuffer...")
    engine = _make_engine(["A"], {"A": 0.01})

    engine._tts_and_lipsync("synthetic single chunk text", voice_mode="full")

    assert engine.lipsync.played == [b"audio-A"], engine.lipsync.played
    assert engine.state.get("last_tts_chunks") == 1, engine.state
    assert engine.state.get("last_tts_prebuffer_chunks") == 0, engine.state
    assert float(engine.state.get("last_tts_prebuffer_ms") or 0.0) == 0.0, engine.state
    assert engine.state.get("last_tts_playback_policy") == "immediate", engine.state
    print("  PASSED")


def _test_full_policy_prebuffer_fail_uses_full_batch():
    print("[Voice Prebuffer Smoke] Test 3: full policy prebuffer fail triggers full-batch fallback...")
    engine = _make_engine(["A", "B"], {"A": 0.01, "B": 1.20})

    engine._tts_and_lipsync("synthetic delayed private chunks " * 30, voice_mode="full")

    assert engine.lipsync.played == [b"audio-A", b"audio-B"], engine.lipsync.played
    assert engine.state.get("last_tts_playback_policy") == "full_batch", engine.state
    assert engine.state.get("last_tts_prebuffer_chunks") == 2, engine.state
    assert float(engine.state.get("last_tts_max_seam_wait_ms") or 0.0) < 100.0, engine.state
    assert engine.state.get("last_tts_seam_grade") in {"good", "none"}, engine.state
    print("  PASSED")


def _test_full_policy_chunk_sizing_uses_larger_limit():
    print("[Voice Prebuffer Smoke] Test 4: full policy uses the larger private chunk limit...")
    from nana.voice.engine import VOICE_CHUNK_MAX_CHARS, VOICE_STORY_CHUNK_MAX_CHARS, VoiceEngine

    engine = VoiceEngine.__new__(VoiceEngine)
    text = " ".join(["đoạn văn thử nghiệm"] * 45)
    chat_chunks = engine._split_tts_text(text, max_chars=VOICE_CHUNK_MAX_CHARS)
    full_chunks = engine._split_tts_text(text, max_chars=engine._chunk_limit_for_mode("full"))

    assert VOICE_STORY_CHUNK_MAX_CHARS >= VOICE_CHUNK_MAX_CHARS, (VOICE_STORY_CHUNK_MAX_CHARS, VOICE_CHUNK_MAX_CHARS)
    assert engine._chunk_limit_for_mode("full") == VOICE_STORY_CHUNK_MAX_CHARS
    assert len(full_chunks) <= len(chat_chunks), (len(full_chunks), len(chat_chunks))
    assert all(len(chunk) <= VOICE_STORY_CHUNK_MAX_CHARS for chunk in full_chunks), full_chunks
    assert all(len(chunk) <= VOICE_CHUNK_MAX_CHARS for chunk in chat_chunks), chat_chunks
    print("  PASSED")


def main():
    print("Voice Playback Prebuffer - Smoke Tests")
    import nana.voice.engine as engine_module

    old_single_request = engine_module.VOICE_FULL_SINGLE_REQUEST_ENABLED
    engine_module.VOICE_FULL_SINGLE_REQUEST_ENABLED = False
    try:
        _test_multichunk_prebuffers_second_chunk_before_playback()
        _test_single_chunk_skips_prebuffer()
        _test_full_policy_prebuffer_fail_uses_full_batch()
        _test_full_policy_chunk_sizing_uses_larger_limit()
    finally:
        engine_module.VOICE_FULL_SINGLE_REQUEST_ENABLED = old_single_request
    print("Voice Playback Prebuffer - PASS (4/4)")


if __name__ == "__main__":
    main()
