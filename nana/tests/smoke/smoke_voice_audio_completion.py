"""Deterministic smoke tests for CORE-VOICE-AUDIO-COMPLETION-1.

No ElevenLabs, sounddevice, Discord, VTS, OBS, or game input is used.
"""

from __future__ import annotations

import contextlib
import io
import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeLipsync:
    def __init__(
        self,
        *,
        callback=True,
        playback_success=True,
        duration_seconds=0.01,
        callback_delay_seconds=0.0,
    ):
        self.callback = callback
        self.playback_success = playback_success
        self.duration_seconds = duration_seconds
        self.callback_delay_seconds = callback_delay_seconds
        self.stop_event = threading.Event()
        self.prepared = []
        self.played = []
        self.callbacks = 0
        self.stop_calls = 0

    def prepare_audio_bytes(self, audio):
        if audio == b"decode-error":
            return None
        self.prepared.append(audio)
        return SimpleNamespace(data=audio, samplerate=44100, duration_seconds=self.duration_seconds)

    def play_prepared_audio_nonblocking(self, prepared, on_done=None):
        self.played.append(prepared.data)
        if self.callback and on_done is not None:
            def finish():
                if self.callback_delay_seconds > 0:
                    time.sleep(self.callback_delay_seconds)
                self.callbacks += 1
                on_done(self.playback_success)

            if self.callback_delay_seconds > 0:
                threading.Thread(target=finish, daemon=True).start()
            else:
                finish()
        return None

    def stop(self):
        self.stop_calls += 1
        self.stop_event.set()


def _make_engine(*, fetch=None, lipsync=None):
    from nana.voice.engine import VoiceEngine

    engine = VoiceEngine.__new__(VoiceEngine)
    engine.state_lock = threading.Lock()
    engine.state = {"last_error": None, "last_tts_max_seam_wait_ms": 0.0}
    engine._shutdown_event = threading.Event()
    engine._shutdown_lock = threading.Lock()
    engine.lipsync = lipsync or FakeLipsync()
    engine.voice_queue = queue.Queue(maxsize=5)
    # This suite verifies the provider/full-file completion path independently
    # of the production-default HTTP streaming transport.
    engine._http_streaming_pilot_selected = lambda text, voice_mode: False
    engine._private_overlap_pcm_route_available = lambda: False
    if fetch is not None:
        engine._tts_fetch_audio = fetch
    return engine


def _text_of_length(length):
    sentence = "Nana đang nói một đoạn hoàn chỉnh để kiểm tra audio completion thật sự. "
    return (sentence * ((length // len(sentence)) + 2))[:length]


def _with_module_settings(**updates):
    import nana.voice.engine as engine_module

    old = {name: getattr(engine_module, name) for name in updates}
    for name, value in updates.items():
        setattr(engine_module, name, value)

    class Restore:
        def __enter__(self):
            return engine_module

        def __exit__(self, *args):
            for name, value in old.items():
                setattr(engine_module, name, value)

    return Restore()


def test_2189_chars_are_one_provider_request():
    calls = []

    def fetch(text, profile):
        calls.append(text)
        return text.encode("utf-8")

    lipsync = FakeLipsync()
    engine = _make_engine(fetch=fetch, lipsync=lipsync)
    text = _text_of_length(2189)
    result = engine._tts_and_lipsync(text, voice_mode="full")

    assert len(calls) == 1, len(calls)
    assert calls[0] == text
    assert len(lipsync.played) == 1, lipsync.played
    assert lipsync.callbacks == 1, lipsync.callbacks
    assert result.state == "completed", result
    assert result.audio_completed is True, result
    assert result.requested_segments == result.fetched_segments == result.played_segments == 1, result
    assert result.remaining_chars == 0, result
    assert engine.state.get("last_tts_strategy") == "provider_single_request", engine.state
    assert engine.state.get("last_audio_completed") is True, engine.state


def test_above_provider_limit_uses_large_ordered_segments():
    calls = []

    def fetch(text, profile):
        calls.append(text)
        return text.encode("utf-8")

    lipsync = FakeLipsync()
    engine = _make_engine(fetch=fetch, lipsync=lipsync)
    text = _text_of_length(2600)
    with _with_module_settings(
        ELEVENLABS_SINGLE_REQUEST_MAX_CHARS=1000,
        VOICE_CHUNKING_ENABLED=False,
    ):
        expected = engine._provider_segments_for_full_voice(text)
        result = engine._tts_and_lipsync(text, voice_mode="full")

    assert 2 <= len(expected) <= 3, len(expected)
    assert len(calls) == len(expected), (len(calls), len(expected))
    assert all(len(segment) <= 1000 for segment in expected), [len(segment) for segment in expected]
    assert lipsync.played == [segment.encode("utf-8") for segment in expected], lipsync.played
    assert lipsync.callbacks == len(expected), lipsync.callbacks
    assert result.audio_completed is True, result
    assert result.played_segments == result.requested_segments == len(expected), result
    assert result.remaining_chars == 0, result


def test_old_total_deadline_does_not_abort_progressing_full_audio():
    lipsync = FakeLipsync(duration_seconds=95.0, callback_delay_seconds=0.05)
    engine = _make_engine(fetch=lambda text, profile: b"audio", lipsync=lipsync)
    started_at = time.perf_counter()
    with _with_module_settings(TTS_DYNAMIC_TIMEOUT_BASE_S=0.0, TTS_DYNAMIC_TIMEOUT_PER_CHUNK_S=0.0):
        result = engine._tts_and_lipsync(_text_of_length(2189), voice_mode="full")

    assert time.perf_counter() - started_at >= 0.04
    assert lipsync.callbacks == 1, lipsync.callbacks
    assert result.audio_completed is True, result
    assert result.state == "completed", result
    assert result.abort_reason == "none", result
    assert result.played_duration_ms == 95000.0, result


def test_provider_failure_is_not_false_completion():
    engine = _make_engine(fetch=lambda text, profile: None, lipsync=FakeLipsync())
    result = engine._tts_and_lipsync(_text_of_length(500), voice_mode="full")

    assert result.audio_completed is False, result
    assert result.state == "provider_error", result
    assert result.fetched_segments == 0, result
    assert result.played_segments == 0, result
    assert result.remaining_chars == 500, result
    assert result.abort_reason == "provider_error", result
    assert engine.state.get("last_error") is None, engine.state


def test_playback_callback_timeout_is_aborted():
    lipsync = FakeLipsync(callback=False, duration_seconds=0.0)
    engine = _make_engine(fetch=lambda text, profile: b"audio", lipsync=lipsync)
    with _with_module_settings(
        VOICE_PLAYBACK_GRACE_S=0,
        VOICE_PLAYBACK_FALLBACK_TIMEOUT_S=0,
    ):
        result = engine._tts_and_lipsync(_text_of_length(500), voice_mode="full")

    assert result.audio_completed is False, result
    assert result.state == "aborted", result
    assert result.fetched_segments == 1, result
    assert result.played_segments == 0, result
    assert result.abort_reason == "playback_timeout", result
    assert lipsync.stop_calls == 1, lipsync.stop_calls


def test_unknown_duration_uses_reported_safe_fallback():
    lipsync = FakeLipsync(duration_seconds=None)
    engine = _make_engine(fetch=lambda text, profile: b"audio", lipsync=lipsync)
    with _with_module_settings(VOICE_PLAYBACK_FALLBACK_TIMEOUT_S=30):
        result = engine._tts_and_lipsync(_text_of_length(500), voice_mode="full")

    assert result.audio_completed is True, result
    assert engine.state.get("last_audio_playback_watchdog") == "safe_fallback", engine.state
    assert engine.state.get("last_audio_playback_timeout_ms") == 30000.0, engine.state


def test_shutdown_during_blocked_fetch_never_starts_playback():
    fetch_started = threading.Event()
    release_fetch = threading.Event()
    lipsync = FakeLipsync()

    def blocked_fetch(text, profile):
        fetch_started.set()
        release_fetch.wait(timeout=2.0)
        return b"audio-after-shutdown"

    engine = _make_engine(fetch=blocked_fetch, lipsync=lipsync)
    engine.state.update(
        last_audio_playback_watchdog="stale_watchdog",
        last_audio_playback_timeout_ms=99999.0,
    )
    text = _text_of_length(2189)
    result_box = {}

    def run_job():
        result_box["result"] = engine._tts_and_lipsync(text, voice_mode="full")

    job = threading.Thread(target=run_job, daemon=True)
    job.start()
    assert fetch_started.wait(timeout=1.0), "fetch did not start"
    assert engine.state.get("last_audio_playback_watchdog") == "none", engine.state
    assert engine.state.get("last_audio_playback_timeout_ms") == 0.0, engine.state

    engine.shutdown()
    release_fetch.set()
    job.join(timeout=2.0)

    assert not job.is_alive(), "cancelled voice job did not return"
    result = result_box.get("result")
    assert result is not None, result_box
    assert result.state == "cancelled", result
    assert result.audio_completed is False, result
    assert result.played_segments == 0, result
    assert result.requested_segments == 1, result
    assert result.remaining_chars == len(text), result
    assert result.abort_reason == "shutdown_cancelled", result
    assert lipsync.played == [], lipsync.played
    assert lipsync.callbacks == 0, lipsync.callbacks


def test_shutdown_during_playback_returns_cancelled():
    lipsync = FakeLipsync(duration_seconds=10.0, callback_delay_seconds=0.5)
    engine = _make_engine(fetch=lambda text, profile: b"audio", lipsync=lipsync)
    text = _text_of_length(500)
    result_box = {}

    def run_job():
        result_box["result"] = engine._tts_and_lipsync(text, voice_mode="full")

    job = threading.Thread(target=run_job, daemon=True)
    job.start()
    deadline = time.perf_counter() + 1.0
    while not lipsync.played and time.perf_counter() < deadline:
        time.sleep(0.01)
    assert lipsync.played == [b"audio"], lipsync.played

    engine.shutdown()
    job.join(timeout=2.0)

    assert not job.is_alive(), "playback cancellation did not return"
    result = result_box.get("result")
    assert result is not None, result_box
    assert result.state == "cancelled", result
    assert result.audio_completed is False, result
    assert result.played_segments == 0, result
    assert result.remaining_chars == len(text), result
    assert result.abort_reason == "shutdown_cancelled", result


def test_worker_idle_preserves_terminal_completion_state():
    from nana.voice.engine import AudioCompletionResult, VoiceQueueItem

    for state, completed in (("completed", True), ("aborted", False)):
        engine = _make_engine(lipsync=FakeLipsync())
        result = AudioCompletionResult(
            state=state,
            audio_completed=completed,
            requested_segments=1,
            fetched_segments=1,
            played_segments=1 if completed else 0,
            unplayed_segments=0 if completed else 1,
            original_chars=220,
            remaining_chars=0 if completed else 220,
            played_duration_ms=25.0 if completed else 0.0,
            abort_reason="none" if completed else "playback_timeout",
        )

        def fake_tts(text, voice_mode="chat", _result=result):
            engine._record_audio_completion(_result)
            return _result

        engine._tts_and_lipsync = fake_tts
        engine.voice_queue.put(VoiceQueueItem("x" * 220, "full"))
        engine.voice_queue.put(None)
        engine._voice_worker()

        assert engine.state.get("status") == "idle", engine.state
        assert engine.state.get("last_audio_state") == state, engine.state
        assert engine.state.get("last_audio_completed") is completed, engine.state


def test_voice_status_exposes_completion_truth():
    from nana.core.status_voice import print_voice_status

    class StatusVoice:
        def snapshot(self):
            return {
                "status": "idle",
                "speaking": False,
                "listening": False,
                "queue_size": 0,
                "queue_maxsize": 5,
                "worker_alive": True,
                "voice_full_single_request_enabled": True,
                "elevenlabs_single_request_max_chars": 4500,
                "voice_story_chunk_max_chars": 360,
                "last_audio_state": "aborted",
                "last_audio_completed": False,
                "last_audio_requested_segments": 2,
                "last_audio_fetched_segments": 2,
                "last_audio_played_segments": 1,
                "last_audio_remaining_chars": 500,
                "last_audio_played_duration_ms": 42000.0,
                "last_audio_abort_reason": "playback_timeout",
                "last_error": None,
            }

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        print_voice_status(StatusVoice())
    rendered = output.getvalue()
    assert "Audio completion:" in rendered, rendered
    assert "state=aborted" in rendered, rendered
    assert "completed=False" in rendered, rendered
    assert "played=1/2" in rendered, rendered
    assert "remaining_chars=500" in rendered, rendered
    assert "abort=playback_timeout" in rendered, rendered
    assert "watchdog=" in rendered, rendered
    assert "Last error: none" in rendered, rendered


def main():
    tests = [
        test_2189_chars_are_one_provider_request,
        test_above_provider_limit_uses_large_ordered_segments,
        test_old_total_deadline_does_not_abort_progressing_full_audio,
        test_provider_failure_is_not_false_completion,
        test_playback_callback_timeout_is_aborted,
        test_unknown_duration_uses_reported_safe_fallback,
        test_shutdown_during_blocked_fetch_never_starts_playback,
        test_shutdown_during_playback_returns_cancelled,
        test_worker_idle_preserves_terminal_completion_state,
        test_voice_status_exposes_completion_truth,
    ]
    print("CORE-VOICE-AUDIO-COMPLETION-1 Smoke")
    for test in tests:
        test()
        print(f"  PASS: {test.__name__}")
    print(f"Result: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
