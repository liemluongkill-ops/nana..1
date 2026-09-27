"""Smoke the VoiceEngine -> Presence PCM adapter without live TTS or audio."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import queue
import sys
import threading


PY_ROOT = Path(__file__).resolve().parents[3]
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))


from nana.voice.engine import (
    PRESENCE_TTS_SEGMENT_MAX_CHARS,
    PRESENCE_VOICE_MAX_CHARS,
    PresenceTTSStreamingUnavailable,
    VoiceEngine,
    VoiceQueueItem,
)
from nana.cli.presence_commands import _build_presence_voice_stress_text
import nana.voice.engine as voice_engine_module


@dataclass(frozen=True)
class _PlaybackResult:
    stream_id: int = 7
    completed: bool = True
    underruns: int = 0


def _engine() -> VoiceEngine:
    engine = VoiceEngine.__new__(VoiceEngine)
    engine.state_lock = threading.Lock()
    engine.state = {}
    engine._presence_output_lock = threading.Lock()
    engine._presence_output_available_fn = None
    engine._presence_pcm_output_fn = None
    engine._shutdown_event = threading.Event()
    engine.voice_queue = queue.Queue()
    engine._voice_completion = threading.Condition()
    engine._voice_enqueued_ticket = 0
    engine._voice_completed_ticket = 0
    return engine


def _run_one(engine: VoiceEngine, text: str = "Nana Presence test") -> None:
    engine.voice_queue.put(VoiceQueueItem(text=text, voice_mode="chat"))
    engine.voice_queue.put(None)
    engine._voice_worker()


def test_online_node_owns_output() -> None:
    engine = _engine()
    local_calls: list[str] = []
    remote_calls: list[tuple[bytes, int]] = []
    pcm = b"\x10\x00" * 1600

    engine.tts_to_pcm16 = lambda text, sample_rate=16000: pcm
    engine._tts_and_lipsync = lambda text, voice_mode="chat": local_calls.append(text)

    def playback(payload, *, sample_rate):
        remote_calls.append((bytes(payload), int(sample_rate)))
        return _PlaybackResult()

    engine.set_presence_pcm_output(
        available_fn=lambda: True,
        playback_fn=playback,
    )
    _run_one(engine)

    assert local_calls == []
    assert remote_calls == [(pcm, 16000)]
    assert engine.state["last_presence_pcm_status"] == "complete"
    assert engine.state["last_presence_pcm_bytes"] == len(pcm)
    assert engine.state["last_presence_pcm_underruns"] == 0
    assert engine.state["last_audio_completed"] is True


def test_offline_node_preserves_local_output() -> None:
    engine = _engine()
    local_calls: list[str] = []
    remote_calls: list[bytes] = []
    engine._tts_and_lipsync = lambda text, voice_mode="chat": local_calls.append(text)
    engine.tts_to_pcm16 = lambda text, sample_rate=16000: b"not-used"
    engine.set_presence_pcm_output(
        available_fn=lambda: False,
        playback_fn=lambda payload, sample_rate=16000: remote_calls.append(payload),
    )
    _run_one(engine, "local fallback")

    assert local_calls == ["local fallback"]
    assert remote_calls == []
    assert engine.state["presence_pcm_output_available"] is False


def test_streaming_node_receives_provider_chunks_before_eof() -> None:
    engine = _engine()
    chunks = [b"\x01\x00" * 600, b"\x02\x00" * 700]
    streamed: list[bytes] = []

    class _Response:
        def iter_content(self, *, chunk_size):
            assert chunk_size > 0
            yield from chunks

        def close(self):
            return None

    engine._open_presence_pcm_stream_response = lambda text, profile: _Response()
    engine.tts_to_pcm16 = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("buffered renderer was used")
    )

    def stream_playback(source, *, sample_rate):
        assert sample_rate == 16000
        payload = b"".join(source)
        streamed.append(payload)
        return type(
            "Result",
            (),
            {
                "stream_id": 9,
                "completed": True,
                "underruns": 0,
                "total_bytes": len(payload),
                "first_audio_ms": 240.0,
            },
        )()

    engine.set_presence_pcm_output(
        available_fn=lambda: True,
        playback_fn=lambda *args, **kwargs: None,
        stream_available_fn=lambda: True,
        stream_playback_fn=stream_playback,
    )
    _run_one(engine, "stream Nana now")

    assert streamed == [b"".join(chunks)]
    assert engine.state["last_presence_pcm_status"] == "complete_streaming"
    assert engine.state["last_presence_pcm_streaming"] is True
    assert engine.state["last_presence_pcm_first_audio_ms"] == 240.0
    assert engine.state["last_audio_completed"] is True


def test_streaming_provider_rejection_falls_back_before_playback() -> None:
    engine = _engine()
    pcm = b"\x03\x00" * 800
    buffered: list[bytes] = []
    stream_calls: list[object] = []
    engine._open_presence_pcm_stream_response = lambda text, profile: (
        (_ for _ in ()).throw(PresenceTTSStreamingUnavailable("unsupported"))
    )
    engine.tts_to_pcm16 = lambda text, sample_rate=16000: pcm

    def buffered_playback(payload, *, sample_rate):
        buffered.append(bytes(payload))
        return _PlaybackResult()

    engine.set_presence_pcm_output(
        available_fn=lambda: True,
        playback_fn=buffered_playback,
        stream_available_fn=lambda: True,
        stream_playback_fn=lambda source, sample_rate=16000: stream_calls.append(source),
    )
    _run_one(engine, "fallback once")

    assert stream_calls == []
    assert buffered == [pcm]
    assert engine.state["last_presence_pcm_status"] == "complete"
    assert engine.state["last_presence_pcm_streaming"] is False


def test_long_presence_reply_is_bounded_and_segmented() -> None:
    engine = _engine()
    source_text = "Stable sentence for the bounded Presence test. " * 80
    provider_requests: list[str] = []
    played_payloads: list[bytes] = []

    class _Response:
        def iter_content(self, *, chunk_size):
            assert chunk_size > 0
            yield b"\x10\x00" * 1200

        def close(self):
            return None

    def open_response(text, profile):
        provider_requests.append(text)
        return _Response()

    def stream_playback(source, *, sample_rate):
        assert sample_rate == 16000
        payload = b"".join(source)
        played_payloads.append(payload)
        return type(
            "Result",
            (),
            {
                "stream_id": len(played_payloads),
                "completed": True,
                "underruns": 0,
                "total_bytes": len(payload),
                "first_audio_ms": 200.0,
            },
        )()

    engine._open_presence_pcm_stream_response = open_response
    engine.set_presence_pcm_output(
        available_fn=lambda: True,
        playback_fn=lambda *args, **kwargs: None,
        stream_available_fn=lambda: True,
        stream_playback_fn=stream_playback,
    )

    previous_streaming = voice_engine_module.PRESENCE_TTS_STREAMING_ENABLED
    previous_limit = voice_engine_module.PRESENCE_VOICE_LIMIT_ENABLED
    voice_engine_module.PRESENCE_TTS_STREAMING_ENABLED = True
    voice_engine_module.PRESENCE_VOICE_LIMIT_ENABLED = True
    try:
        result = engine._tts_to_presence_pcm(
            source_text,
            voice_mode="full",
            playback_fn=lambda *args, **kwargs: None,
            stream_playback_fn=stream_playback,
        )
    finally:
        voice_engine_module.PRESENCE_TTS_STREAMING_ENABLED = previous_streaming
        voice_engine_module.PRESENCE_VOICE_LIMIT_ENABLED = previous_limit

    assert result is not None and result.audio_completed
    assert len(source_text) > PRESENCE_VOICE_MAX_CHARS
    assert engine.state["last_presence_pcm_input_chars"] == len(source_text.strip())
    assert engine.state["last_presence_pcm_text_truncated"] is True
    assert engine.state["last_presence_pcm_omitted_chars"] > 0
    assert provider_requests
    assert len(provider_requests) == len(played_payloads)
    assert len(provider_requests) > 1
    assert max(len(text) for text in provider_requests) <= PRESENCE_TTS_SEGMENT_MAX_CHARS
    assert sum(len(text) for text in provider_requests) <= PRESENCE_VOICE_MAX_CHARS
    assert all(payload == b"\x10\x00" * 1200 for payload in played_payloads)
    assert engine.state["last_audio_played_segments"] == len(provider_requests)


def test_presence_voice_limit_is_transparent_when_disabled() -> None:
    engine = _engine()
    source_text = "Nana keeps every production sentence intact. " * 80
    assert len(source_text.strip()) > PRESENCE_VOICE_MAX_CHARS

    previous_limit = voice_engine_module.PRESENCE_VOICE_LIMIT_ENABLED
    voice_engine_module.PRESENCE_VOICE_LIMIT_ENABLED = False
    try:
        bounded, truncated = engine._bound_presence_text(source_text)
    finally:
        voice_engine_module.PRESENCE_VOICE_LIMIT_ENABLED = previous_limit

    assert bounded == source_text.strip()
    assert truncated is False


def test_exact_1500_character_stress_stream_is_complete_and_silent() -> None:
    engine = _engine()
    stress_text = _build_presence_voice_stress_text(1500)
    provider_requests: list[str] = []
    played_payloads: list[bytes] = []

    class _Response:
        def iter_content(self, *, chunk_size):
            assert chunk_size > 0
            yield b"\x20\x00" * 1200

        def close(self):
            return None

    def open_response(text, profile):
        provider_requests.append(text)
        return _Response()

    def stream_playback(source, *, sample_rate):
        assert sample_rate == 16000
        payload = b"".join(source)
        played_payloads.append(payload)
        return type(
            "Result",
            (),
            {
                "stream_id": len(played_payloads),
                "completed": True,
                "underruns": 0,
                "total_bytes": len(payload),
                "first_audio_ms": 200.0,
            },
        )()

    engine._open_presence_pcm_stream_response = open_response
    engine.set_presence_pcm_output(
        available_fn=lambda: True,
        playback_fn=lambda *args, **kwargs: None,
        stream_available_fn=lambda: True,
        stream_playback_fn=stream_playback,
    )

    previous_streaming = voice_engine_module.PRESENCE_TTS_STREAMING_ENABLED
    previous_limit = voice_engine_module.PRESENCE_VOICE_LIMIT_ENABLED
    voice_engine_module.PRESENCE_TTS_STREAMING_ENABLED = True
    voice_engine_module.PRESENCE_VOICE_LIMIT_ENABLED = False
    try:
        result = engine._tts_to_presence_pcm(
            stress_text,
            voice_mode="full",
            playback_fn=lambda *args, **kwargs: None,
            stream_playback_fn=stream_playback,
        )
    finally:
        voice_engine_module.PRESENCE_TTS_STREAMING_ENABLED = previous_streaming
        voice_engine_module.PRESENCE_VOICE_LIMIT_ENABLED = previous_limit

    assert len(stress_text) == 1500
    assert result is not None and result.audio_completed
    assert engine.state["last_presence_pcm_input_chars"] == 1500
    assert engine.state["last_presence_pcm_text_chars"] == 1500
    assert engine.state["last_presence_pcm_text_truncated"] is False
    assert engine.state["last_presence_pcm_omitted_chars"] == 0
    assert engine.state["last_presence_pcm_status"] == "complete_streaming"
    assert engine.state["last_presence_pcm_underruns"] == 0
    assert len(provider_requests) == len(played_payloads) > 1
    assert max(len(text) for text in provider_requests) <= PRESENCE_TTS_SEGMENT_MAX_CHARS
    assert sum(len(text) for text in provider_requests) >= 1490
    assert all(payload == b"\x20\x00" * 1200 for payload in played_payloads)
    assert engine.state["last_audio_played_segments"] == len(provider_requests)


def test_remote_failure_never_double_plays_locally() -> None:
    engine = _engine()
    local_calls: list[str] = []
    engine.tts_to_pcm16 = lambda text, sample_rate=16000: b"\x00\x00" * 800
    engine._tts_and_lipsync = lambda text, voice_mode="chat": local_calls.append(text)

    def fail_playback(payload, *, sample_rate):
        raise RuntimeError("synthetic link loss")

    engine.set_presence_pcm_output(
        available_fn=lambda: True,
        playback_fn=fail_playback,
    )
    _run_one(engine, "do not replay")

    assert local_calls == []
    assert engine.state["last_presence_pcm_status"] == "failed"
    assert "synthetic link loss" in engine.state["last_presence_pcm_error"]
    assert engine.state["last_audio_completed"] is False


def test_adapter_requires_a_complete_callback_pair() -> None:
    engine = _engine()
    try:
        engine.set_presence_pcm_output(available_fn=lambda: True)
    except TypeError:
        pass
    else:
        raise AssertionError("an incomplete Presence callback pair was accepted")


def test_voice_ticket_completes_only_after_playback_returns() -> None:
    engine = _engine()
    playback_started = threading.Event()
    release_playback = threading.Event()

    def local_playback(_text, voice_mode="chat"):
        playback_started.set()
        assert release_playback.wait(timeout=2.0)
        return None

    engine._tts_and_lipsync = local_playback
    worker = threading.Thread(target=engine._voice_worker)
    worker.start()
    ticket = engine.say("ticket drain test")
    assert ticket > 0
    assert playback_started.wait(timeout=1.0)
    assert engine.wait_for_voice_ticket(ticket, timeout=0.05) is False
    release_playback.set()
    assert engine.wait_for_voice_ticket(ticket, timeout=1.0) is True
    assert engine.completed_voice_ticket() >= ticket
    engine.voice_queue.put(None)
    worker.join(timeout=1.0)
    assert not worker.is_alive()


def main() -> None:
    tests = [
        test_online_node_owns_output,
        test_offline_node_preserves_local_output,
        test_streaming_node_receives_provider_chunks_before_eof,
        test_streaming_provider_rejection_falls_back_before_playback,
        test_long_presence_reply_is_bounded_and_segmented,
        test_presence_voice_limit_is_transparent_when_disabled,
        test_exact_1500_character_stress_stream_is_complete_and_silent,
        test_remote_failure_never_double_plays_locally,
        test_adapter_requires_a_complete_callback_pair,
        test_voice_ticket_completes_only_after_playback_returns,
    ]
    for index, test in enumerate(tests, start=1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_voice_presence_pcm_output: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
