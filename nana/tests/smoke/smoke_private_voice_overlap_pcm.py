"""Offline smoke coverage for HTTP overlap with direct PCM playback.

No LLM, ElevenLabs, audio device, FFmpeg, Presence, VTS, or live Nana runtime
is used. HTTP responses and the callback player are deterministic fakes.
"""

from __future__ import annotations

from concurrent.futures import Future
from contextlib import contextmanager
import queue
import threading
import time
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _lead() -> str:
    return "Đây là câu đầu đủ dài để Nana bắt đầu nói sớm và rõ ràng cho Ba nghe. "


def _tail() -> str:
    return "Phần còn lại được tải trước bằng HTTP PCM và chỉ phát đúng một lần."


def _pcm(value: int, samples: int = 2400) -> bytes:
    return np.full(samples, value, dtype="<i2").tobytes()


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, parts):
        self.parts = list(parts)
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size > 0
        for part in self.parts:
            if isinstance(part, BaseException):
                raise part
            if callable(part):
                part = part()
            if part:
                yield bytes(part)

    def close(self):
        self.closed = True


class _FakePcmLipsync:
    def __init__(self):
        self.stop_event = threading.Event()
        self.pcm_calls = 0
        self.played = []

    def play_pcm_stream_nonblocking(
        self,
        pcm_queue,
        decoder_eof,
        *,
        samplerate,
        startup_buffer_ms,
        stall_timeout_s,
        on_state=None,
        on_done=None,
        **_kwargs,
    ):
        self.pcm_calls += 1
        self.stop_event.clear()

        def run():
            samples = 0
            started = False
            while not self.stop_event.is_set():
                try:
                    item = pcm_queue.get(timeout=0.01)
                except queue.Empty:
                    if decoder_eof.is_set() and pcm_queue.empty():
                        break
                    continue
                pcm = np.asarray(item, dtype=np.float32).reshape(-1).copy()
                self.played.append(pcm)
                samples += int(pcm.size)
                pcm_queue.task_done()
                if not started:
                    started = True
                    if on_state is not None:
                        on_state("playing")

            completed = bool(started and not self.stop_event.is_set())
            if on_state is not None:
                on_state("done" if completed else "aborted")
            result = SimpleNamespace(
                completed=completed,
                played_samples=samples,
                played_duration_ms=samples / float(samplerate) * 1000.0,
                start_buffer_ms=float(startup_buffer_ms if started else 0.0),
                rebuffer_count=0,
                rebuffer_total_ms=0.0,
                abort_reason="none" if completed else "decoder_empty",
                output_underflow_count=0,
                callback_calls=max(1, samples // max(1, samplerate // 50)),
                callback_status_underflows=0,
                ring_starvation_count=0,
                ring_low_watermark_ms=250.0 if started else None,
                max_callback_lateness_ms=0.0,
                feeder_refill_count=len(self.played),
                feeder_done=True,
                callback_finished=True,
            )
            if on_done is not None:
                on_done(result)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def stop(self):
        self.stop_event.set()


def _make_engine():
    from nana.voice.engine import VoiceEngine

    engine = VoiceEngine.__new__(VoiceEngine)
    engine.state_lock = threading.RLock()
    engine.state = {}
    engine._shutdown_event = threading.Event()
    engine._shutdown_lock = threading.Lock()
    engine._tts_semaphore = threading.BoundedSemaphore(3)
    engine.lipsync = _FakePcmLipsync()
    engine._stream_output_stream_factory = None
    engine._resolve_stream_ffmpeg_path = lambda: None
    return engine


def _item(*, turn_id="overlap-pcm"):
    from nana.runtime.private_voice_overlap import (
        OverlapQueueItem,
        OverlapTailPayload,
    )

    future = Future()
    started = time.perf_counter()
    item = OverlapQueueItem(
        turn_id=turn_id,
        lead_text=_lead(),
        split_offset=len(_lead()),
        tail_future=future,
        cancel_event=threading.Event(),
        turn_started_at=started,
        lead_committed_at=started,
        voice_mode="full",
        ticket=9,
    )
    future.set_result(
        OverlapTailPayload(
            full_text=_lead() + _tail(),
            tail_text=_tail(),
            split_offset=len(_lead()),
            llm_completed_at=time.perf_counter(),
        )
    )
    return item


@contextmanager
def _pcm_route_enabled():
    import nana.voice.engine as engine_module

    names = {
        "PRIVATE_VOICE_OVERLAP_ENABLED": True,
        "PRIVATE_VOICE_OVERLAP_PCM_ENABLED": True,
        "PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT": "pcm_24000",
        "VOICE_STREAMING_ENABLED": True,
        "VOICE_STREAMING_KILL_SWITCH": False,
        "VOICE_STREAM_CALLBACK_OUTPUT_ENABLED": True,
    }
    previous = {name: getattr(engine_module, name) for name in names}
    try:
        for name, value in names.items():
            setattr(engine_module, name, value)
        yield
    finally:
        for name, value in previous.items():
            setattr(engine_module, name, value)


def _bind_open(engine, behavior):
    def open_response(_self, text, _profile):
        response = behavior(text)
        return response, False, time.perf_counter()

    engine._open_overlap_pcm_stream_response = MethodType(
        open_response,
        engine,
    )


def _test_one_callback_preserves_lead_tail_order_and_prefetches() -> None:
    engine = _make_engine()
    tail_opened = threading.Event()
    lead_waiting = threading.Event()
    requests = []

    def wait_for_tail():
        lead_waiting.set()
        assert tail_opened.wait(1.0), "tail HTTP request did not overlap lead"
        return _pcm(1100)

    def behavior(text):
        requests.append(text)
        if text == _lead():
            return _FakeResponse([_pcm(1000), wait_for_tail])
        assert text == _tail(), text
        tail_opened.set()
        return _FakeResponse([_pcm(2000), _pcm(2100)])

    _bind_open(engine, behavior)
    with _pcm_route_enabled():
        result = engine._tts_overlap_completion(_item(turn_id="pcm-order"))

    assert lead_waiting.is_set()
    assert result.audio_completed is True, result
    assert requests.count(_lead()) == 1, requests
    assert requests.count(_tail()) == 1, requests
    assert engine.lipsync.pcm_calls == 1
    assert len(engine.lipsync.played) == 4, len(engine.lipsync.played)
    means = [float(chunk.mean()) for chunk in engine.lipsync.played]
    assert all(
        abs(actual - expected) < 0.0001
        for actual, expected in zip(
            means,
            [1000 / 32768, 1100 / 32768, 2000 / 32768, 2100 / 32768],
        )
    ), means
    snap = dict(engine.state)
    assert snap["last_tts_strategy"] == "private_voice_overlap_http_pcm"
    assert snap["last_tts_playback_policy"] == "one_callback_lead_tail_pcm"
    assert snap["last_private_voice_overlap_pcm_status"] == "completed"
    assert snap["last_private_voice_overlap_pcm_tail_buffered_before_lead_eof"] is True
    assert snap["last_private_voice_overlap_true_overlap"] is True
    assert snap["last_private_voice_overlap_missing_chars"] == 0
    assert snap["last_private_voice_overlap_duplicate_chars"] == 0
    assert snap["last_stream_ffmpeg_ready"] is False


def _test_http_request_uses_pcm24_stream_contract_and_releases_slot() -> None:
    import nana.voice.engine as engine_module

    engine = _make_engine()
    captured = []
    response = _FakeResponse([_pcm(1234, samples=32)])
    original_post = engine_module.requests.post
    original_format = engine_module.PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT
    original_keepalive = engine_module.VOICE_HTTP_KEEPALIVE_ENABLED

    def fake_post(url, **kwargs):
        captured.append((url, kwargs))
        return response

    try:
        engine_module.requests.post = fake_post
        engine_module.PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT = "pcm_24000"
        engine_module.VOICE_HTTP_KEEPALIVE_ENABLED = False
        opened = engine._open_overlap_pcm_stream_response(
            "Một câu thử PCM.",
            {"stability": 0.5},
        )
        chunks = []
        engine._drain_overlap_pcm_response(
            opened,
            sink=chunks.append,
            stop_event=threading.Event(),
            metrics={
                "first_byte_at": None,
                "eof_at": None,
                "chunks": 0,
                "bytes": 0,
                "max_gap_ms": 0.0,
            },
        )
    finally:
        engine_module.requests.post = original_post
        engine_module.PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT = original_format
        engine_module.VOICE_HTTP_KEEPALIVE_ENABLED = original_keepalive

    assert len(captured) == 1
    url, kwargs = captured[0]
    assert url.endswith("/stream"), url
    assert kwargs["params"] == {"output_format": "pcm_24000"}
    assert kwargs["headers"]["Accept"] == "application/octet-stream"
    assert kwargs["stream"] is True
    assert chunks and chunks[0].dtype == np.float32
    acquired = [engine._tts_semaphore.acquire(blocking=False) for _ in range(3)]
    assert acquired == [True, True, True], acquired
    for _ in acquired:
        engine._tts_semaphore.release()


def _test_voice_http_keepalive_reuses_one_thread_session() -> None:
    import nana.voice.engine as engine_module

    engine = _make_engine()
    sessions = []

    class _FakeSession:
        def __init__(self):
            self.calls = []
            self.mounts = []
            sessions.append(self)

        def mount(self, prefix, adapter):
            self.mounts.append((prefix, adapter))

        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return _FakeResponse([_pcm(1234, samples=8)])

    old_session_factory = engine_module.requests.Session
    old_enabled = engine_module.VOICE_HTTP_KEEPALIVE_ENABLED
    old_pool = engine_module.VOICE_HTTP_POOL_MAXSIZE
    local = engine_module._VOICE_HTTP_LOCAL
    old_local_session = getattr(local, "session", None)
    old_local_count = getattr(local, "request_count", None)
    try:
        engine_module.requests.Session = _FakeSession
        engine_module.VOICE_HTTP_KEEPALIVE_ENABLED = True
        engine_module.VOICE_HTTP_POOL_MAXSIZE = 8
        if hasattr(local, "session"):
            del local.session
        if hasattr(local, "request_count"):
            del local.request_count

        first = engine._voice_http_post("https://example.invalid/first")
        second = engine._voice_http_post("https://example.invalid/second")
    finally:
        engine_module.requests.Session = old_session_factory
        engine_module.VOICE_HTTP_KEEPALIVE_ENABLED = old_enabled
        engine_module.VOICE_HTTP_POOL_MAXSIZE = old_pool
        if old_local_session is None:
            if hasattr(local, "session"):
                del local.session
        else:
            local.session = old_local_session
        if old_local_count is None:
            if hasattr(local, "request_count"):
                del local.request_count
        else:
            local.request_count = old_local_count

    assert len(sessions) == 1, sessions
    assert len(sessions[0].calls) == 2, sessions[0].calls
    assert len(sessions[0].mounts) == 2, sessions[0].mounts
    assert first._nana_session_request_index == 1
    assert first._nana_session_reused_hint is False
    assert second._nana_session_request_index == 2
    assert second._nana_session_reused_hint is True
    assert engine.state["last_voice_http_session_request_index"] == 2
    assert engine.state["last_voice_http_session_reused_hint"] is True


def _test_short_full_response_uses_one_direct_pcm_callback() -> None:
    engine = _make_engine()
    text = "Con chúc Ba tối nay nghỉ ngơi thật vui và ngủ ngon nhé."

    def behavior(request_text):
        assert request_text == text, request_text
        return _FakeResponse([_pcm(1200), _pcm(1300)])

    _bind_open(engine, behavior)
    with _pcm_route_enabled():
        result = engine._tts_and_lipsync(text, voice_mode="full")

    assert result.audio_completed is True, result
    assert engine.lipsync.pcm_calls == 1
    assert len(engine.lipsync.played) == 2
    snap = dict(engine.state)
    assert snap["last_tts_strategy"] == "private_voice_http_pcm_single", snap
    assert snap["last_tts_playback_policy"] == "http_pcm_single_callback", snap
    assert snap["last_private_voice_overlap_pcm_status"] == "single_completed", snap
    assert snap["last_stream_ffmpeg_ready"] is False
    assert snap["last_stream_rebuffer_count"] == 0


def _test_short_pcm_failure_before_audio_returns_to_existing_transport() -> None:
    engine = _make_engine()
    text = "Con chúc Ba tối nay nghỉ ngơi thật vui và ngủ ngon nhé."
    fallback_calls = []

    def behavior(_request_text):
        return _FakeResponse([RuntimeError("single_pcm_provider_failed")])

    def fallback(_self, request_text, _profile, _mode, *, original_chars=None):
        fallback_calls.append((request_text, original_chars))
        return SimpleNamespace(audio_completed=True, state="completed")

    _bind_open(engine, behavior)
    engine._tts_full_provider_completion = MethodType(fallback, engine)
    with _pcm_route_enabled():
        result = engine._tts_and_lipsync(text, voice_mode="full")

    assert result.audio_completed is True
    assert fallback_calls == [(text, len(text))], fallback_calls
    assert engine.state["last_private_voice_overlap_pcm_fallback_used"] is True


def _test_short_pcm_failure_after_audio_never_calls_existing_transport() -> None:
    engine = _make_engine()
    text = "Con chúc Ba tối nay nghỉ ngơi thật vui và ngủ ngon nhé."
    fallback_calls = []

    def behavior(_request_text):
        return _FakeResponse(
            [_pcm(1400), RuntimeError("single_pcm_broke_after_audio")]
        )

    def fallback(_self, *_args, **_kwargs):
        fallback_calls.append(True)
        return SimpleNamespace(audio_completed=True, state="completed")

    _bind_open(engine, behavior)
    engine._tts_full_provider_completion = MethodType(fallback, engine)
    with _pcm_route_enabled():
        result = engine._tts_and_lipsync(text, voice_mode="full")

    assert result.audio_completed is False, result
    assert len(engine.lipsync.played) == 1
    assert fallback_calls == [], fallback_calls
    assert engine.state["last_private_voice_overlap_pcm_status"] == (
        "single_failed_after_audio"
    )


def _test_failure_before_audio_requests_old_route_fallback() -> None:
    engine = _make_engine()

    def behavior(text):
        if text == _lead():
            return _FakeResponse([RuntimeError("lead_transport_failed")])
        return _FakeResponse([_pcm(2000)])

    _bind_open(engine, behavior)
    result = engine._tts_overlap_pcm_completion(_item(turn_id="pcm-fallback"))

    assert result is None
    assert engine.lipsync.played == []
    assert engine.state["last_private_voice_overlap_pcm_fallback_used"] is True
    assert engine.state["private_voice_overlap_pcm_fallback_total"] == 1


def _test_failure_before_audio_runs_existing_overlap_once() -> None:
    engine = _make_engine()
    old_route_calls = []

    class _FallbackLipsync(_FakePcmLipsync):
        def prepare_audio_bytes(self, audio):
            return SimpleNamespace(
                data=audio,
                samplerate=1,
                duration_seconds=0.001,
            )

        def play_prepared_audio_nonblocking(self, prepared, on_done=None):
            self.played.append(prepared.data)
            if on_done is not None:
                on_done(True)

    engine.lipsync = _FallbackLipsync()

    def behavior(text):
        if text == _lead():
            return _FakeResponse([RuntimeError("lead_transport_failed")])
        return _FakeResponse([_pcm(2000)])

    def old_fetch(_self, text, _profile):
        old_route_calls.append(text)
        return b"old-lead" if text == _lead() else b"old-tail"

    engine._tts_fetch_audio = MethodType(old_fetch, engine)
    _bind_open(engine, behavior)
    with _pcm_route_enabled():
        result = engine._tts_overlap_completion(
            _item(turn_id="pcm-integrated-fallback")
        )

    assert result.audio_completed is True, result
    assert engine.lipsync.pcm_calls == 1
    assert old_route_calls == [_lead(), _tail()], old_route_calls
    assert engine.lipsync.played == [b"old-lead", b"old-tail"]
    assert engine.state["last_private_voice_overlap_pcm_fallback_used"] is True
    assert engine.state["last_private_voice_overlap_status"] == "completed"


def _test_failure_after_first_audio_never_replays_lead() -> None:
    engine = _make_engine()
    old_route_calls = []

    def behavior(text):
        if text == _lead():
            return _FakeResponse(
                [_pcm(1000), RuntimeError("lead_stream_broke_after_pcm")]
            )
        return _FakeResponse([_pcm(2000)])

    def old_fetch(_self, text, _profile):
        old_route_calls.append(text)
        return b"must-not-run"

    engine._tts_fetch_audio = MethodType(old_fetch, engine)
    _bind_open(engine, behavior)
    with _pcm_route_enabled():
        result = engine._tts_overlap_completion(
            _item(turn_id="pcm-no-replay")
        )

    assert result is not None and result.audio_completed is False, result
    assert engine.lipsync.pcm_calls == 1
    assert len(engine.lipsync.played) == 1
    assert old_route_calls == [], old_route_calls
    assert engine.state["last_private_voice_overlap_pcm_fallback_used"] is False
    assert engine.state["last_private_voice_overlap_pcm_status"] == "failed_after_audio"


def _test_flag_off_keeps_existing_overlap_route() -> None:
    import nana.voice.engine as engine_module

    engine = _make_engine()
    calls = []

    def old_fetch(_self, text, _profile):
        calls.append(text)
        return b"lead-audio" if text == _lead() else b"tail-audio"

    class _PreparedLipsync(_FakePcmLipsync):
        def prepare_audio_bytes(self, audio):
            return SimpleNamespace(
                data=audio,
                samplerate=1,
                duration_seconds=0.001,
            )

        def play_prepared_audio_nonblocking(self, prepared, on_done=None):
            self.played.append(prepared.data)
            if on_done is not None:
                on_done(True)

    engine.lipsync = _PreparedLipsync()
    engine._tts_fetch_audio = MethodType(old_fetch, engine)
    old_pcm_enabled = engine_module.PRIVATE_VOICE_OVERLAP_PCM_ENABLED
    try:
        engine_module.PRIVATE_VOICE_OVERLAP_PCM_ENABLED = False
        result = engine._tts_overlap_completion(_item(turn_id="pcm-off"))
    finally:
        engine_module.PRIVATE_VOICE_OVERLAP_PCM_ENABLED = old_pcm_enabled

    assert result.audio_completed is True, result
    assert calls == [_lead(), _tail()], calls
    assert engine.lipsync.pcm_calls == 0
    assert engine.state["last_tts_strategy"] == "private_voice_overlap"


def main() -> int:
    tests = [
        _test_one_callback_preserves_lead_tail_order_and_prefetches,
        _test_http_request_uses_pcm24_stream_contract_and_releases_slot,
        _test_voice_http_keepalive_reuses_one_thread_session,
        _test_short_full_response_uses_one_direct_pcm_callback,
        _test_short_pcm_failure_before_audio_returns_to_existing_transport,
        _test_short_pcm_failure_after_audio_never_calls_existing_transport,
        _test_failure_before_audio_requests_old_route_fallback,
        _test_failure_before_audio_runs_existing_overlap_once,
        _test_failure_after_first_audio_never_replays_lead,
        _test_flag_off_keeps_existing_overlap_route,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS smoke_private_voice_overlap_pcm ({len(tests)}/{len(tests)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
