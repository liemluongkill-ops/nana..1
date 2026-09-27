"""Deterministic smoke tests for CORE-VOICE-HTTP-STREAMING-PILOT-1.

No ElevenLabs API, FFmpeg process, sounddevice output, Discord, VTS, OBS, or
game input is used. HTTP, decoder pipes, and OutputStream are all fakes.
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

import numpy as np
import requests
import sounddevice as sd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeResponse:
    def __init__(self, items=(), *, status_code=200, hold_open=False):
        self.items = list(items)
        self.status_code = status_code
        self.hold_open = hold_open
        self.closed = threading.Event()

    def iter_content(self, chunk_size=None):
        del chunk_size
        for item in self.items:
            if self.closed.is_set():
                return
            if isinstance(item, BaseException):
                raise item
            if callable(item):
                item = item()
            if item:
                yield bytes(item)
        if self.hold_open:
            self.closed.wait(timeout=3.0)

    def close(self):
        self.closed.set()


class FakeDecoderStdin:
    def __init__(self, output_queue, terminated):
        self.output_queue = output_queue
        self.terminated = terminated
        self.closed = False
        self.writes = []

    def write(self, payload):
        if self.closed or self.terminated.is_set():
            raise BrokenPipeError("fake decoder input closed")
        data = bytes(payload)
        self.writes.append(data)
        self.output_queue.put(data)
        return len(data)

    def close(self):
        if not self.closed:
            self.closed = True
            self.output_queue.put(None)


class FakeDecoderStdout:
    def __init__(self, output_queue):
        self.output_queue = output_queue
        self.pending = b""
        self.eof = False

    def read(self, count):
        while not self.pending and not self.eof:
            item = self.output_queue.get(timeout=3.0)
            if item is None:
                self.eof = True
                break
            self.pending += bytes(item)
        if not self.pending:
            return b""
        result = self.pending[:count]
        self.pending = self.pending[count:]
        return result


class FakeDecoderProcess:
    """Passes fake PCM bytes through the same stdin/stdout contract as FFmpeg."""

    def __init__(self, *, return_code=0):
        self.output_queue = queue.Queue()
        self.terminated = threading.Event()
        self.return_code = int(return_code)
        self.stdin = FakeDecoderStdin(self.output_queue, self.terminated)
        self.stdout = FakeDecoderStdout(self.output_queue)

    def poll(self):
        return self.return_code if self.stdin.closed or self.terminated.is_set() else None

    def wait(self, timeout=None):
        del timeout
        return self.return_code

    def terminate(self):
        self.terminated.set()
        self.stdin.close()

    def kill(self):
        self.terminate()


class FakeOutputStream:
    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        self.owner.entered.set()
        return self

    def __exit__(self, *args):
        self.owner.exited.set()

    def write(self, frame):
        if self.owner.write_delay_s:
            time.sleep(self.owner.write_delay_s)
        write_index = self.owner.write_calls
        self.owner.writes.append(np.asarray(frame, dtype=np.float32).reshape(-1).copy())
        self.owner.write_calls += 1
        return write_index in self.owner.underflow_write_indexes


class FakeOutputFactory:
    def __init__(self, *, write_delay_s=0.0, underflow_write_indexes=()):
        self.write_delay_s = float(write_delay_s)
        self.underflow_write_indexes = {int(index) for index in underflow_write_indexes}
        self.entered = threading.Event()
        self.exited = threading.Event()
        self.writes = []
        self.write_calls = 0

    def __call__(self, **kwargs):
        assert kwargs["channels"] == 1, kwargs
        assert kwargs["dtype"] == "float32", kwargs
        return FakeOutputStream(self)

    def samples(self):
        if not self.writes:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(self.writes)


class FakeCallbackStatus:
    def __init__(self, *, output_underflow=False):
        self.output_underflow = bool(output_underflow)


class FakeCallbackOutputStream:
    def __init__(self, owner, **kwargs):
        self.owner = owner
        self.callback = kwargs["callback"]
        self.finished_callback = kwargs["finished_callback"]
        self.samplerate = int(kwargs["samplerate"])
        self.frames = owner.frames or max(1, int(self.samplerate * 0.02))
        self.abort_event = threading.Event()
        self.thread = None

    def __enter__(self):
        self.owner.entered.set()
        self.thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="fake-portaudio-callback",
        )
        self.thread.start()
        return self

    def __exit__(self, *args):
        del args
        if self.thread is not None and self.thread.is_alive():
            self.abort()
            self.thread.join(timeout=1.0)
        self.owner.exited.set()
        return False

    def abort(self):
        self.abort_event.set()

    def _run(self):
        try:
            while not self.abort_event.is_set():
                index = self.owner.callback_calls
                outdata = np.full((self.frames, 1), np.nan, dtype=np.float32)
                status = FakeCallbackStatus(
                    output_underflow=index in self.owner.underflow_callback_indexes
                )
                callback_stop = False
                callback_abort = False
                try:
                    self.callback(outdata, self.frames, None, status)
                except sd.CallbackStop:
                    callback_stop = True
                except sd.CallbackAbort:
                    callback_abort = True
                if not callback_abort:
                    self.owner.outputs.append(outdata.reshape(-1).copy())
                self.owner.callback_calls += 1
                if callback_stop or callback_abort:
                    break
                if self.abort_event.wait(timeout=self.owner.callback_interval_s):
                    break
        finally:
            self.owner.finished_callback_calls += 1
            self.finished_callback()
            self.owner.finished.set()


class FakeCallbackOutputFactory:
    def __init__(
        self,
        *,
        callback_interval_s=0.001,
        frames=None,
        underflow_callback_indexes=(),
    ):
        self.callback_interval_s = float(callback_interval_s)
        self.frames = None if frames is None else int(frames)
        self.underflow_callback_indexes = {
            int(index) for index in underflow_callback_indexes
        }
        self.entered = threading.Event()
        self.exited = threading.Event()
        self.finished = threading.Event()
        self.outputs = []
        self.callback_calls = 0
        self.finished_callback_calls = 0

    def __call__(self, **kwargs):
        assert kwargs["channels"] == 1, kwargs
        assert kwargs["dtype"] == "float32", kwargs
        assert callable(kwargs["callback"]), kwargs
        assert callable(kwargs["finished_callback"]), kwargs
        return FakeCallbackOutputStream(self, **kwargs)

    def samples(self):
        if not self.outputs:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(self.outputs)


class CallbackMethodAuditQueue:
    """Records any queue method invoked from the fake PortAudio callback."""

    def __init__(self):
        self._queue = queue.Queue()
        self.callback_method_calls = []

    def __getattr__(self, name):
        target = getattr(self._queue, name)
        if not callable(target):
            return target

        def audited(*args, **kwargs):
            if threading.current_thread().name == "fake-portaudio-callback":
                self.callback_method_calls.append(name)
            return target(*args, **kwargs)

        return audited


class FailingCallbackOutputFactory:
    def __call__(self, **kwargs):
        del kwargs
        raise RuntimeError("deterministic stream-open failure")


class FakeLegacyLipsync:
    def __init__(self):
        self.stop_event = threading.Event()
        self.played = []

    def prepare_audio_bytes(self, audio):
        return SimpleNamespace(data=audio, samplerate=1000, duration_seconds=0.01)

    def play_prepared_audio_nonblocking(self, prepared, on_done=None):
        self.played.append(prepared.data)
        if on_done:
            on_done(True)

    def stop(self):
        self.stop_event.set()


def _pcm_bytes(value, samples):
    return np.full(samples, value, dtype="<f4").tobytes()


def _make_engine(response=None, *, output_factory=None, legacy_fetch=None, decoder_return_code=0):
    from nana.voice.engine import VoiceEngine
    from nana.voice.lipsync import LipsyncManager

    engine = VoiceEngine.__new__(VoiceEngine)
    engine.state_lock = threading.Lock()
    engine.state = {"last_error": None, "last_tts_max_seam_wait_ms": 0.0}
    engine._shutdown_event = threading.Event()
    engine._shutdown_lock = threading.Lock()
    engine._stream_handle_lock = threading.Lock()
    engine._active_stream_response = None
    engine._active_stream_process = None
    engine._tts_semaphore = threading.BoundedSemaphore(3)
    engine.voice_queue = queue.Queue(maxsize=5)
    engine.lipsync = LipsyncManager() if output_factory is not None else FakeLegacyLipsync()
    engine._stream_output_stream_factory = output_factory
    engine._resolve_stream_ffmpeg_path = lambda: "fake-ffmpeg"
    process_box = {}

    def spawn(_path):
        process = FakeDecoderProcess(return_code=decoder_return_code)
        process_box["process"] = process
        return process

    engine._spawn_streaming_decoder = spawn
    if response is not None:
        engine._open_streaming_tts_response = lambda text, profile: response
    if legacy_fetch is not None:
        engine._tts_fetch_audio = legacy_fetch
    engine._test_process_box = process_box
    return engine


@contextlib.contextmanager
def _module_settings(**updates):
    import nana.voice.engine as engine_module

    defaults = {
        "VOICE_STREAMING_ENABLED": True,
        "VOICE_STREAMING_PILOT_ENABLED": True,
        "VOICE_STREAMING_KILL_SWITCH": False,
        "VOICE_STREAMING_DIRECT_ONLY": True,
        "VOICE_STREAM_CALLBACK_OUTPUT_ENABLED": False,
        "PRIVATE_VOICE_OVERLAP_PCM_ENABLED": False,
        "VOICE_FULL_SINGLE_REQUEST_ENABLED": True,
        "ELEVENLABS_SINGLE_REQUEST_MAX_CHARS": 4500,
        "VOICE_HTTP_STREAM_SAMPLE_RATE": 1000,
        "VOICE_HTTP_STREAM_START_BUFFER_MS": 300,
        "VOICE_HTTP_STREAM_STALL_TIMEOUT_S": 1.0,
        "VOICE_HTTP_STREAM_PCM_READ_BYTES": 400,
        "VOICE_HTTP_STREAM_NETWORK_CHUNK_BYTES": 400,
        "VOICE_HTTP_STREAM_PCM_QUEUE_CHUNKS": 16,
    }
    defaults.update(updates)
    old = {name: getattr(engine_module, name) for name in defaults}
    try:
        for name, value in defaults.items():
            setattr(engine_module, name, value)
        yield engine_module
    finally:
        for name, value in old.items():
            setattr(engine_module, name, value)


@contextlib.contextmanager
def _no_real_sd_stop():
    import nana.voice.lipsync as lipsync_module

    old = lipsync_module.sd.stop
    lipsync_module.sd.stop = lambda: None
    try:
        yield
    finally:
        lipsync_module.sd.stop = old


def _run_stream(response, *, samples_factory=None, text="x" * 500):
    factory = samples_factory or FakeOutputFactory()
    engine = _make_engine(response, output_factory=factory)
    with _module_settings():
        result = engine._tts_and_lipsync(text, voice_mode="full")
    return engine, factory, result


def test_default_flags_off_keep_legacy_pipeline():
    calls = []
    engine = _make_engine(legacy_fetch=lambda text, profile: calls.append(text) or b"legacy")
    engine._resolve_stream_ffmpeg_path = lambda: (_ for _ in ()).throw(AssertionError("pilot preflight ran"))
    with _module_settings(
        VOICE_STREAMING_ENABLED=False,
        VOICE_STREAMING_PILOT_ENABLED=False,
        VOICE_STREAMING_KILL_SWITCH=True,
    ):
        result = engine._tts_and_lipsync("legacy full voice", voice_mode="full")
    assert result.audio_completed is True, result
    assert calls == ["legacy full voice"], calls
    assert engine.state["last_tts_strategy"] == "provider_single_request", engine.state


def test_pilot_gate_is_full_only_and_provider_sized():
    engine = _make_engine()
    with _module_settings():
        assert engine._http_streaming_pilot_selected("x" * 500, "full") is True
        assert engine._http_streaming_pilot_selected("x" * 500, "story") is False
        assert engine._http_streaming_pilot_selected("x" * 500, "chat") is False
        assert engine._http_streaming_pilot_selected("x" * 4501, "full") is False
    with _module_settings(VOICE_STREAMING_KILL_SWITCH=True):
        assert engine._http_streaming_pilot_selected("x" * 500, "full") is False


def test_ffmpeg_preflight_failure_rolls_back_before_request():
    legacy_calls = []
    engine = _make_engine(legacy_fetch=lambda text, profile: legacy_calls.append(text) or b"legacy")
    engine._resolve_stream_ffmpeg_path = lambda: None
    engine._open_streaming_tts_response = lambda text, profile: (_ for _ in ()).throw(
        AssertionError("provider stream request ran before FFmpeg preflight")
    )
    with _module_settings():
        result = engine._tts_and_lipsync("x" * 500, voice_mode="full")
    assert result.audio_completed is True, result
    assert legacy_calls == ["x" * 500], len(legacy_calls)
    assert engine.state["last_streaming_reason"] == "ffmpeg_preflight_failed_before_request", engine.state


def test_fake_http_decoder_and_playback_preserve_order():
    response = FakeResponse([_pcm_bytes(0.1, 200), _pcm_bytes(0.2, 200)])
    engine, factory, result = _run_stream(response)
    played = factory.samples()
    assert result.audio_completed is True, result
    assert len(played) == 400, len(played)
    assert 0.08 < float(played[50]) < 0.12, played[50]
    assert 0.18 < float(played[250]) < 0.22, played[250]
    assert engine.state["last_tts_strategy"] == "http_stream", engine.state
    assert engine.state["last_stream_received_bytes"] == 1600, engine.state


def test_decoder_partial_sample_after_pcm_never_completes():
    response = FakeResponse([_pcm_bytes(0.1, 350) + b"X"])
    engine, factory, result = _run_stream(response)
    assert len(factory.samples()) == 350, len(factory.samples())
    assert result.audio_completed is False, result
    assert result.state == "aborted", result
    assert result.abort_reason == "decoder_partial_sample", result
    assert engine.state["last_stream_decoder_eof"] is False, engine.state


def test_decoder_nonzero_after_pcm_never_completes():
    response = FakeResponse([_pcm_bytes(0.1, 350)])
    factory = FakeOutputFactory()
    engine = _make_engine(response, output_factory=factory, decoder_return_code=7)
    with _module_settings():
        result = engine._tts_and_lipsync("x" * 500, voice_mode="full")
    assert len(factory.samples()) == 350, len(factory.samples())
    assert result.audio_completed is False, result
    assert result.state == "aborted", result
    assert result.abort_reason == "decoder_error", result
    assert engine.state["last_stream_decoder_eof"] is False, engine.state


def test_playback_error_stops_full_pcm_queue_decoder_thread():
    class FailingOutputStream:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def write(self, frame):
            del frame
            raise RuntimeError("forced_output_failure")

    class FailingOutputFactory:
        def __call__(self, **kwargs):
            del kwargs
            return FailingOutputStream()

    response = FakeResponse([_pcm_bytes(0.1, 100) for _ in range(80)])
    engine = _make_engine(response, output_factory=FailingOutputFactory())
    with _module_settings(VOICE_HTTP_STREAM_PCM_QUEUE_CHUNKS=2):
        result = engine._tts_and_lipsync("x" * 500, voice_mode="full")
    assert result.audio_completed is False, result
    assert result.state == "aborted", result
    assert result.abort_reason == "playback_error", result
    live_stream_threads = [
        thread.name
        for thread in threading.enumerate()
        if thread.name in {"tts-http-stream-decoder", "tts-http-stream-reader"}
    ]
    assert live_stream_threads == [], live_stream_threads


def test_playback_waits_for_fixed_start_buffer():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.ones(350, dtype=np.float32))
    decoder_eof = threading.Event()
    factory = FakeOutputFactory()

    def finish_decoder():
        assert factory.entered.wait(timeout=1.0)
        decoder_eof.set()

    threading.Thread(target=finish_decoder, daemon=True).start()
    result = manager._play_pcm_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=factory,
    )
    assert result.completed is True, result
    assert result.start_buffer_ms >= 300.0, result


def test_short_audio_starts_at_decoder_eof_before_300ms():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.ones(120, dtype=np.float32))
    decoder_eof = threading.Event()
    decoder_eof.set()
    factory = FakeOutputFactory()
    result = manager._play_pcm_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=factory,
    )
    assert result.completed is True, result
    assert 100.0 <= result.start_buffer_ms < 300.0, result
    assert result.played_samples == 120, result


def test_continuous_top_up_is_not_true_rebuffer():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.ones(350, dtype=np.float32) * 0.1)
    pcm_queue.put(np.ones(320, dtype=np.float32) * 0.2)
    decoder_eof = threading.Event()
    decoder_eof.set()
    factory = FakeOutputFactory()
    result = manager._play_pcm_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=factory,
    )
    assert result.completed is True, result
    assert result.refill_count >= 1, result
    assert result.rebuffer_count == 0, result
    assert result.output_underflow_count == 0, result
    assert result.output_write_calls == len(factory.writes), result


def test_steady_producer_top_up_does_not_rebuffer():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.ones(300, dtype=np.float32) * 0.1)
    decoder_eof = threading.Event()
    factory = FakeOutputFactory(write_delay_s=0.004)

    def producer():
        assert factory.entered.wait(timeout=1.0)
        for _ in range(5):
            time.sleep(0.005)
            pcm_queue.put(np.ones(100, dtype=np.float32) * 0.2)
        decoder_eof.set()

    threading.Thread(target=producer, daemon=True).start()
    result = manager._play_pcm_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=factory,
    )
    assert result.completed is True, result
    assert result.refill_count >= 1, result
    assert result.rebuffer_count == 0, result
    assert result.output_underflow_count == 0, result
    assert result.played_samples == 800, result


def test_output_underflow_is_counted_without_false_failure():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.ones(600, dtype=np.float32) * 0.1)
    decoder_eof = threading.Event()
    decoder_eof.set()
    factory = FakeOutputFactory(underflow_write_indexes={1, 3})
    result = manager._play_pcm_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=factory,
    )
    assert result.completed is True, result
    assert result.output_underflow_count == 2, result
    assert result.output_write_calls == len(factory.writes), result
    assert result.last_underflow_buffer_ms is not None, result
    assert result.last_underflow_queue_depth == 0, result
    assert result.last_underflow_decoder_done is True, result


def test_output_underflow_telemetry_reaches_engine_state():
    response = FakeResponse([_pcm_bytes(0.1, 600)])
    factory = FakeOutputFactory(underflow_write_indexes={2})
    engine, _factory, result = _run_stream(response, samples_factory=factory)
    assert result.audio_completed is True, result
    assert engine.state["last_stream_output_write_calls"] == len(factory.writes), engine.state
    assert engine.state["last_stream_output_underflow_count"] == 1, engine.state
    assert engine.state["last_stream_last_underflow_buffer_ms"] is not None, engine.state
    assert engine.state["last_stream_last_underflow_queue_depth"] >= 0, engine.state
    assert engine.state["last_stream_last_underflow_decoder_done"] is True, engine.state


def test_low_watermark_excludes_final_drain():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.ones(600, dtype=np.float32) * 0.1)
    decoder_eof = threading.Event()
    factory = FakeOutputFactory(write_delay_s=0.002)

    def finish_decoder():
        assert factory.entered.wait(timeout=1.0)
        time.sleep(0.012)
        decoder_eof.set()

    threading.Thread(target=finish_decoder, daemon=True).start()
    result = manager._play_pcm_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=factory,
    )
    assert result.completed is True, result
    assert result.rebuffer_count == 0, result
    assert result.playback_buffer_low_watermark_ms is not None, result
    assert result.playback_buffer_low_watermark_ms > 0.0, result


def test_http_eof_is_not_completion_until_pcm_drains():
    response = FakeResponse([_pcm_bytes(0.2, 600)])
    factory = FakeOutputFactory(write_delay_s=0.01)
    engine = _make_engine(response, output_factory=factory)
    result_box = {}

    def run():
        with _module_settings():
            result_box["result"] = engine._tts_and_lipsync("x" * 500, voice_mode="full")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.perf_counter() + 2.0
    while not engine.state.get("last_stream_provider_eof") and time.perf_counter() < deadline:
        time.sleep(0.005)
    assert engine.state.get("last_stream_provider_eof") is True, engine.state
    assert thread.is_alive(), "provider EOF incorrectly completed the job"
    assert engine.state.get("last_audio_completed") is False, engine.state
    thread.join(timeout=3.0)
    assert result_box["result"].audio_completed is True, result_box


def test_underflow_rebuffers_and_then_completes():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.ones(320, dtype=np.float32) * 0.1)
    decoder_eof = threading.Event()
    factory = FakeOutputFactory()

    def producer():
        assert factory.entered.wait(timeout=1.0)
        time.sleep(0.05)
        pcm_queue.put(np.ones(320, dtype=np.float32) * 0.2)
        decoder_eof.set()

    threading.Thread(target=producer, daemon=True).start()
    result = manager._play_pcm_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=factory,
    )
    assert result.completed is True, result
    assert result.rebuffer_count >= 1, result
    assert result.refill_count == 0, result
    assert result.rebuffer_total_ms >= 20.0, result
    assert result.played_samples == 640, result


def test_network_stall_aborts_with_explicit_reason():
    response = FakeResponse(
        [_pcm_bytes(0.1, 100), requests.exceptions.ReadTimeout("fake read stall")]
    )
    engine, _factory, result = _run_stream(response)
    assert result.audio_completed is False, result
    assert result.state == "aborted", result
    assert result.abort_reason == "network_stall_timeout", result
    assert engine.state["last_stream_transport_state"] == "error", engine.state
    assert engine.state["last_stream_provider_eof"] is False, engine.state


def test_shutdown_receiving_and_playing_are_cancelled():
    for initial_samples, wait_for_playing in ((50, False), (600, True)):
        response = FakeResponse([_pcm_bytes(0.1, initial_samples)], hold_open=True)
        factory = FakeOutputFactory(write_delay_s=0.01 if wait_for_playing else 0.0)
        engine = _make_engine(response, output_factory=factory)
        result_box = {}

        def run():
            with _module_settings():
                result_box["result"] = engine._tts_and_lipsync("x" * 500, voice_mode="full")

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        deadline = time.perf_counter() + 2.0
        while time.perf_counter() < deadline:
            receiving = engine.state.get("last_stream_transport_state") == "receiving"
            playing = engine.state.get("last_stream_playback_state") == "playing"
            if receiving and (playing if wait_for_playing else True):
                break
            time.sleep(0.005)
        with _no_real_sd_stop():
            engine.shutdown()
        thread.join(timeout=3.0)
        assert not thread.is_alive(), "cancelled stream did not return"
        result = result_box["result"]
        assert result.state == "cancelled", result
        assert result.abort_reason == "shutdown_cancelled", result
        writes_after_shutdown = len(factory.writes)
        time.sleep(0.05)
        assert len(factory.writes) == writes_after_shutdown, "PCM played after shutdown"
        assert engine.state["last_stream_transport_state"] == "cancelled", engine.state
        assert engine.state["last_stream_playback_state"] == "cancelled", engine.state


def test_request_failure_never_replays_through_legacy_path():
    legacy_calls = []
    response = FakeResponse(status_code=500)
    factory = FakeOutputFactory()
    engine = _make_engine(
        response,
        output_factory=factory,
        legacy_fetch=lambda text, profile: legacy_calls.append(text) or b"legacy",
    )
    with _module_settings():
        result = engine._tts_and_lipsync("x" * 500, voice_mode="full")
    assert result.state == "provider_error", result
    assert result.audio_completed is False, result
    assert result.abort_reason == "provider_http_500", result
    assert legacy_calls == [], legacy_calls
    assert factory.writes == [], factory.writes


def test_final_drain_sets_completion_and_status_telemetry():
    from nana.core.status_voice import print_voice_status

    response = FakeResponse([_pcm_bytes(0.15, 150)])
    engine, _factory, result = _run_stream(response, text="x" * 150)
    assert result.audio_completed is True, result
    assert result.remaining_chars == 0, result
    assert engine.state["last_stream_provider_eof"] is True, engine.state
    assert engine.state["last_stream_decoder_eof"] is True, engine.state
    assert engine.state["last_stream_playback_state"] == "done", engine.state
    assert engine.state["last_stream_abort_reason"] == "none", engine.state

    runtime = dict(engine.state)
    runtime.update(
        status="idle",
        speaking=False,
        listening=False,
        queue_size=0,
        queue_maxsize=5,
        worker_alive=True,
        voice_http_stream_start_buffer_ms=300,
        voice_http_stream_stall_timeout_s=45,
    )
    engine.snapshot = lambda: runtime
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        print_voice_status(engine)
    rendered = output.getvalue()
    assert "Stream lifecycle:" in rendered, rendered
    assert "transport=eof" in rendered, rendered
    assert "playback=done" in rendered, rendered
    assert "provider_eof=True" in rendered, rendered
    assert "decoder_eof=True" in rendered, rendered
    assert "Stream latency:" in rendered, rendered
    assert "Stream buffer:" in rendered, rendered
    assert "Output feed:" in rendered, rendered
    assert "underflows=0" in rendered, rendered


def test_callback_ring_preserves_order_across_wraparound():
    from nana.voice.lipsync import _SpscPcmRing

    ring = _SpscPcmRing(8)
    assert ring.write(np.arange(6, dtype=np.float32)) == 6
    first = np.empty(4, dtype=np.float32)
    assert ring.read_into(first) == 4
    assert np.array_equal(first, np.arange(4, dtype=np.float32)), first
    assert ring.write(np.arange(6, 12, dtype=np.float32)) == 6
    wrapped = np.empty(8, dtype=np.float32)
    assert ring.read_into(wrapped) == 8
    assert np.array_equal(wrapped, np.arange(4, 12, dtype=np.float32)), wrapped
    assert ring.available_samples == 0


def test_callback_ring_absorbs_67ms_feeder_stall():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.full(400, 0.1, dtype=np.float32))
    decoder_eof = threading.Event()
    factory = FakeCallbackOutputFactory(callback_interval_s=0.02, frames=20)

    def producer():
        assert factory.entered.wait(timeout=1.0)
        time.sleep(0.067)
        pcm_queue.put(np.full(400, 0.2, dtype=np.float32))
        decoder_eof.set()

    producer_thread = threading.Thread(target=producer, daemon=True)
    producer_thread.start()
    result = manager._play_pcm_callback_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=factory,
    )
    producer_thread.join(timeout=1.0)
    assert result.completed is True, result
    assert result.played_samples == 800, result
    assert result.ring_starvation_count == 0, result
    assert result.callback_status_underflows == 0, result
    assert result.feeder_done is True, result
    assert result.callback_finished is True, result


def test_callback_status_underflow_is_counted_exactly():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.full(500, 0.1, dtype=np.float32))
    decoder_eof = threading.Event()
    decoder_eof.set()
    factory = FakeCallbackOutputFactory(
        callback_interval_s=0.001,
        frames=20,
        underflow_callback_indexes={1, 3},
    )
    result = manager._play_pcm_callback_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=factory,
    )
    assert result.completed is True, result
    assert result.callback_status_underflows == 2, result
    assert result.output_underflow_count == 2, result
    assert result.ring_starvation_count == 0, result
    assert result.played_samples == 500, result


def test_callback_ring_starvation_zero_fills_once_and_recovers():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.full(320, 0.25, dtype=np.float32))
    decoder_eof = threading.Event()
    factory = FakeCallbackOutputFactory(callback_interval_s=0.02, frames=20)

    def producer():
        assert factory.entered.wait(timeout=1.0)
        time.sleep(0.65)
        pcm_queue.put(np.full(320, 0.5, dtype=np.float32))
        decoder_eof.set()

    producer_thread = threading.Thread(target=producer, daemon=True)
    producer_thread.start()
    result = manager._play_pcm_callback_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=factory,
    )
    producer_thread.join(timeout=1.0)
    silent_blocks = [block for block in factory.outputs if np.all(block == 0.0)]
    assert result.completed is True, result
    assert result.ring_starvation_count == 1, result
    assert result.callback_status_underflows == 0, result
    assert np.allclose(factory.outputs[15][:10], 0.25), factory.outputs[15]
    assert np.array_equal(
        factory.outputs[15][10:],
        np.zeros(10, dtype=np.float32),
    ), factory.outputs[15]
    assert silent_blocks, "starvation did not zero-fill the callback output"
    assert not any(np.isnan(block).any() for block in factory.outputs), "uninitialized samples escaped"
    assert result.played_samples == 640, result


def test_callback_final_partial_block_fades_real_samples_then_finishes():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.ones(335, dtype=np.float32))
    decoder_eof = threading.Event()
    decoder_eof.set()
    factory = FakeCallbackOutputFactory(callback_interval_s=0.001, frames=20)
    result = manager._play_pcm_callback_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=factory,
    )
    output = factory.samples()
    assert result.completed is True, result
    assert result.played_samples == 335, result
    assert len(output) == 340, len(output)
    assert output[0] == 0.0, output[:10]
    assert np.isclose(output[9], 1.0), output[:10]
    assert np.allclose(output[10:325], 1.0), output[10:325]
    assert np.isclose(output[325], 1.0), output[325:335]
    assert output[334] == 0.0, output[325:335]
    assert np.array_equal(output[335:], np.zeros(5, dtype=np.float32)), output[335:]
    assert result.callback_finished is True, result
    assert factory.finished_callback_calls == 1, factory.finished_callback_calls


def test_callback_cancellation_stops_callback_and_feeder():
    import nana.voice.lipsync as lipsync_module
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.full(500, 0.1, dtype=np.float32))
    decoder_eof = threading.Event()
    factory = FakeCallbackOutputFactory(callback_interval_s=0.02, frames=20)
    done = threading.Event()
    result_box = {}
    old_stop = lipsync_module.sd.stop
    lipsync_module.sd.stop = lambda: None
    try:
        manager.play_pcm_stream_nonblocking(
            pcm_queue,
            decoder_eof,
            samplerate=1000,
            startup_buffer_ms=300,
            stall_timeout_s=1.0,
            output_stream_factory=factory,
            callback_output=True,
            on_done=lambda result: (result_box.setdefault("result", result), done.set()),
        )
        assert factory.entered.wait(timeout=1.0)
        manager.stop()
        assert done.wait(timeout=2.0)
    finally:
        lipsync_module.sd.stop = old_stop
    result = result_box["result"]
    assert result.completed is False, result
    assert result.abort_reason == "shutdown_cancelled", result
    assert result.feeder_done is True, result
    assert result.callback_finished is True, result
    assert factory.finished_callback_calls == 1, factory.finished_callback_calls
    leaked = [
        thread.name
        for thread in threading.enumerate()
        if thread.name in {"lipsync-pcm-ring-feeder", "fake-portaudio-callback"}
    ]
    assert leaked == [], leaked


def test_callback_startup_gate_caps_preloaded_5000ms_at_300ms():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.full(5000, 0.1, dtype=np.float32))
    decoder_eof = threading.Event()
    decoder_eof.set()
    factory = FakeCallbackOutputFactory(callback_interval_s=0.001, frames=20)
    result = manager._play_pcm_callback_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=factory,
    )
    assert result.completed is True, result
    assert result.start_buffer_ms == 300.0, result
    assert result.played_samples == 5000, result
    assert result.feeder_done is True, result


def test_callback_stream_open_failure_stops_feeder_without_thread_leak():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.full(5000, 0.1, dtype=np.float32))
    decoder_eof = threading.Event()
    decoder_eof.set()
    result = manager._play_pcm_callback_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=FailingCallbackOutputFactory(),
    )
    assert result.completed is False, result
    assert result.abort_reason == "playback_error", result
    assert result.feeder_done is True, result
    leaked = [
        thread.name
        for thread in threading.enumerate()
        if thread.name == "lipsync-pcm-ring-feeder"
    ]
    assert leaked == [], leaked


def test_callback_never_invokes_pcm_queue_methods():
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = CallbackMethodAuditQueue()
    pcm_queue.put(np.full(500, 0.1, dtype=np.float32))
    decoder_eof = threading.Event()
    decoder_eof.set()
    factory = FakeCallbackOutputFactory(
        callback_interval_s=0.001,
        frames=20,
        underflow_callback_indexes={1, 3},
    )
    result = manager._play_pcm_callback_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=300,
        stall_timeout_s=1.0,
        output_stream_factory=factory,
    )
    assert result.completed is True, result
    assert result.callback_status_underflows == 2, result
    assert pcm_queue.callback_method_calls == [], pcm_queue.callback_method_calls


def test_callback_lateness_subtracts_expected_callback_period():
    import nana.voice.lipsync as lipsync_module
    from nana.voice.lipsync import LipsyncManager

    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.full(500, 0.1, dtype=np.float32))
    decoder_eof = threading.Event()
    decoder_eof.set()
    factory = FakeCallbackOutputFactory(callback_interval_s=0.001, frames=20)
    real_perf_counter = lipsync_module.time.perf_counter
    callback_clock = {"ticks": 0}
    callback_clock_lock = threading.Lock()
    callback_base = real_perf_counter()

    def deterministic_perf_counter():
        if threading.current_thread().name != "fake-portaudio-callback":
            return real_perf_counter()
        with callback_clock_lock:
            value = callback_base + callback_clock["ticks"] * 0.030
            callback_clock["ticks"] += 1
            return value

    lipsync_module.time.perf_counter = deterministic_perf_counter
    try:
        result = manager._play_pcm_callback_queue(
            pcm_queue,
            decoder_eof,
            samplerate=1000,
            startup_buffer_ms=300,
            stall_timeout_s=1.0,
            output_stream_factory=factory,
        )
    finally:
        lipsync_module.time.perf_counter = real_perf_counter
    assert result.completed is True, result
    assert result.max_callback_lateness_ms == 10.0, result


def test_callback_flag_off_keeps_blocking_output_path():
    response = FakeResponse([_pcm_bytes(0.1, 500)])
    factory = FakeOutputFactory()
    engine = _make_engine(response, output_factory=factory)
    with _module_settings(VOICE_STREAM_CALLBACK_OUTPUT_ENABLED=False):
        result = engine._tts_and_lipsync("x" * 500, voice_mode="full")
    assert result.audio_completed is True, result
    assert factory.write_calls > 0, factory.write_calls
    assert engine.state["last_stream_callback_calls"] == 0, engine.state
    assert engine.state["last_tts_playback_policy"] == "http_stream", engine.state


def test_callback_metrics_reach_engine_state_and_status():
    from nana.core.status_voice import print_voice_status

    response = FakeResponse([_pcm_bytes(0.1, 600)])
    factory = FakeCallbackOutputFactory(callback_interval_s=0.001, frames=20)
    engine = _make_engine(response, output_factory=factory)
    with _module_settings(VOICE_STREAM_CALLBACK_OUTPUT_ENABLED=True):
        result = engine._tts_and_lipsync("x" * 500, voice_mode="full")
    assert result.audio_completed is True, result
    assert engine.state["last_stream_callback_calls"] == factory.callback_calls, engine.state
    assert engine.state["last_stream_callback_calls"] > 0, engine.state
    assert engine.state["last_stream_callback_status_underflows"] == 0, engine.state
    assert engine.state["last_stream_ring_starvation_count"] == 0, engine.state
    assert engine.state["last_stream_feeder_done"] is True, engine.state
    assert engine.state["last_stream_callback_finished"] is True, engine.state
    assert engine.state["last_stream_output_write_calls"] == 0, engine.state
    assert engine.state["last_stream_output_underflow_count"] == 0, engine.state
    assert engine.state["last_tts_playback_policy"] == "http_stream_callback", engine.state

    runtime = dict(engine.state)
    runtime.update(
        status="idle",
        speaking=False,
        listening=False,
        queue_size=0,
        queue_maxsize=5,
        worker_alive=True,
        voice_stream_callback_output_enabled=True,
        voice_http_stream_start_buffer_ms=300,
        voice_http_stream_stall_timeout_s=45,
    )
    engine.snapshot = lambda: runtime
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        print_voice_status(engine)
    rendered = output.getvalue()
    assert "callback_output=True" in rendered, rendered
    assert "Callback output:" in rendered, rendered
    assert "ring_starvations=0" in rendered, rendered
    assert "max_callback_lateness=" in rendered, rendered
    assert "max_callback_gap=" not in rendered, rendered
    assert "feeder_done=True" in rendered, rendered
    assert "callback_finished=True" in rendered, rendered


def main():
    tests = [
        test_default_flags_off_keep_legacy_pipeline,
        test_pilot_gate_is_full_only_and_provider_sized,
        test_ffmpeg_preflight_failure_rolls_back_before_request,
        test_fake_http_decoder_and_playback_preserve_order,
        test_decoder_partial_sample_after_pcm_never_completes,
        test_decoder_nonzero_after_pcm_never_completes,
        test_playback_error_stops_full_pcm_queue_decoder_thread,
        test_playback_waits_for_fixed_start_buffer,
        test_short_audio_starts_at_decoder_eof_before_300ms,
        test_continuous_top_up_is_not_true_rebuffer,
        test_steady_producer_top_up_does_not_rebuffer,
        test_output_underflow_is_counted_without_false_failure,
        test_output_underflow_telemetry_reaches_engine_state,
        test_low_watermark_excludes_final_drain,
        test_http_eof_is_not_completion_until_pcm_drains,
        test_underflow_rebuffers_and_then_completes,
        test_network_stall_aborts_with_explicit_reason,
        test_shutdown_receiving_and_playing_are_cancelled,
        test_request_failure_never_replays_through_legacy_path,
        test_final_drain_sets_completion_and_status_telemetry,
        test_callback_ring_preserves_order_across_wraparound,
        test_callback_ring_absorbs_67ms_feeder_stall,
        test_callback_status_underflow_is_counted_exactly,
        test_callback_ring_starvation_zero_fills_once_and_recovers,
        test_callback_final_partial_block_fades_real_samples_then_finishes,
        test_callback_cancellation_stops_callback_and_feeder,
        test_callback_startup_gate_caps_preloaded_5000ms_at_300ms,
        test_callback_stream_open_failure_stops_feeder_without_thread_leak,
        test_callback_never_invokes_pcm_queue_methods,
        test_callback_lateness_subtracts_expected_callback_period,
        test_callback_flag_off_keeps_blocking_output_path,
        test_callback_metrics_reach_engine_state_and_status,
    ]
    print("CORE-VOICE-HTTP-STREAMING-PILOT-1 Smoke")
    for test in tests:
        test()
        print(f"  PASS: {test.__name__}")
    print(f"Result: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
