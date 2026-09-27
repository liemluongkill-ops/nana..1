"""Offline contract tests for the private TTD WebSocket PCM pilot.

No network, LLM, ElevenLabs generation, audio device, Presence node, or VTS is
used. The WebSocket and playback boundaries are deterministic fakes.
"""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import Future
import json
from pathlib import Path
import queue
import threading
import tempfile
import time
from types import MethodType, SimpleNamespace
import sys
import wave

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _initial_text() -> str:
    return "Ba nghe đoạn đầu đủ dài để TTD bắt đầu phát tiếng sớm nha. "


def _next_text() -> str:
    return "Phần sau vẫn đi cùng một giọng và được gửi tăng dần."


class _FakeTtdSocket:
    def __init__(self, audio_frames: list[bytes]) -> None:
        self.sent: list[dict] = []
        self._incoming: asyncio.Queue[str] = asyncio.Queue()
        self._audio_frames = list(audio_frames)
        self._input_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        inputs = message.get("inputs") or []
        if inputs:
            self._input_count += len(inputs)
            if self._input_count == 1 and self._audio_frames:
                await self._incoming.put(
                    json.dumps(
                        {"audio": base64.b64encode(self._audio_frames[0]).decode()}
                    )
                )
        if message.get("close_socket"):
            for payload in self._audio_frames[1:]:
                await self._incoming.put(
                    json.dumps({"audio": base64.b64encode(payload).decode()})
                )
            await self._incoming.put(json.dumps({"is_final": True}))

    async def recv(self):
        return await self._incoming.get()


class _FakeConnect:
    def __init__(self, socket: _FakeTtdSocket) -> None:
        self.socket = socket

    async def __aenter__(self):
        return await self.socket.__aenter__()

    async def __aexit__(self, *args):
        return await self.socket.__aexit__(*args)


class _ErrorAfterAudioSocket(_FakeTtdSocket):
    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        if message.get("inputs"):
            self._input_count += len(message["inputs"])
            if self._input_count == 1 and self._audio_frames:
                await self._incoming.put(
                    json.dumps(
                        {"audio": base64.b64encode(self._audio_frames[0]).decode()}
                    )
                )
        if message.get("close_socket"):
            await self._incoming.put(
                json.dumps(
                    {
                        "error": "synthetic_provider_error",
                        "code": 500,
                        "param": None,
                    }
                )
            )


class _FakeLipsync:
    def __init__(self):
        self.stop_event = threading.Event()
        self.played = np.empty(0, dtype=np.float32)
        self.result = None

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
        output_stream_factory=None,
        callback_output=False,
    ):
        del samplerate, startup_buffer_ms, stall_timeout_s, output_stream_factory
        del callback_output

        def run():
            if on_state:
                on_state("buffering")
            parts = []
            while not decoder_eof.is_set() or not pcm_queue.empty():
                try:
                    parts.append(np.asarray(pcm_queue.get(timeout=1.0), dtype=np.float32))
                except queue.Empty:
                    if decoder_eof.is_set():
                        break
            if parts:
                self.played = np.concatenate(parts)
            if on_state:
                on_state("playing")
                on_state("done")
            self.result = SimpleNamespace(
                completed=True,
                played_duration_ms=(len(self.played) / 24000.0) * 1000.0,
                start_buffer_ms=300.0,
                rebuffer_count=0,
                rebuffer_total_ms=0.0,
                output_underflow_count=0,
                callback_calls=2,
                callback_status_underflows=0,
                ring_starvation_count=0,
                ring_low_watermark_ms=100.0,
                max_callback_lateness_ms=0.0,
                feeder_refill_count=1,
                feeder_done=True,
                callback_finished=True,
                abort_reason="none",
            )
            if on_done:
                on_done(self.result)

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
    engine._stream_handle_lock = threading.Lock()
    engine._active_stream_response = None
    engine._active_stream_process = None
    engine.voice_queue = queue.Queue(maxsize=5)
    engine._voice_completion = threading.Condition()
    engine._voice_enqueued_ticket = 0
    engine._voice_completed_ticket = 0
    engine.lipsync = _FakeLipsync()
    engine._presence_pcm_outputs_if_available = lambda: None
    engine._prepare_tts_request_text = MethodType(
        lambda _self, value: str(value), engine
    )
    return engine


def _item(initial: str, rest: str, full: str, *, include_initial: bool = True):
    from nana.runtime.private_voice_ttd import TtdFinalPayload, TtdQueueItem

    text_queue = queue.SimpleQueue()
    if include_initial:
        text_queue.put(initial)
    text_queue.put(rest)
    text_queue.put(None)
    final_future = Future()
    final_future.set_result(
        TtdFinalPayload(
            full_text=full,
            sent_text=full,
            source_chunks=(initial, rest),
            llm_completed_at=time.perf_counter(),
        )
    )
    return TtdQueueItem(
        turn_id="ttd-smoke-turn",
        text_queue=text_queue,
        final_future=final_future,
        cancel_event=threading.Event(),
        turn_started_at=time.perf_counter(),
        committed_at=time.perf_counter(),
        voice_mode="full",
        ticket=1,
    )


def test_ttd_builder_emits_exact_incremental_chunks_once():
    from nana.runtime.private_voice_ttd import PrivateVoiceTtdTurn

    initial = _initial_text()
    rest = _next_text()
    full = initial + rest
    committed = []
    committed_event = threading.Event()

    def commit(request):
        committed.append(request)
        committed_event.set()
        return 1

    turn = PrivateVoiceTtdTurn(
        commit,
        minimum_chars=40,
        minimum_words=8,
        chunk_target_chars=80,
        chunk_max_chars=120,
        turn_id="ttd-builder",
    )
    for char in full:
        turn.feed(char)
    assert committed_event.wait(1.0)
    result = turn.finish(full)
    request = committed[0]
    chunks = []
    while True:
        chunk = request.text_queue.get(timeout=1.0)
        if chunk is None:
            break
        chunks.append(chunk)

    assert result.committed is True, result
    assert len(committed) == 1, committed
    assert "".join(chunks) == full, chunks
    assert all(chunk for chunk in chunks)
    assert request.final_future.result(timeout=1.0).sent_text == full


def test_ttd_builder_eof_before_threshold_falls_back():
    from nana.runtime.private_voice_ttd import PrivateVoiceTtdTurn

    calls = []
    text = "Ba nói ngắn thôi."
    turn = PrivateVoiceTtdTurn(
        lambda request: calls.append(request) or 1,
        minimum_chars=40,
        minimum_words=8,
        chunk_target_chars=80,
        chunk_max_chars=120,
    )
    turn.feed(text)
    result = turn.finish(text)
    assert result.committed is False, result
    assert calls == [], calls


def test_ttd_websocket_is_one_incremental_same_turn_and_pcm_direct():
    import nana.voice.engine as engine_module

    engine = _make_engine()
    initial = _initial_text()
    rest = _next_text()
    full = initial + rest
    first_pcm = np.array([0, 16384, -16384, 32767], dtype="<i2").tobytes()
    second_pcm = np.array([-8192, 8192], dtype="<i2").tobytes()
    socket = _FakeTtdSocket([first_pcm, second_pcm])
    engine._ttd_websocket_connect = lambda _uri: _FakeConnect(socket)
    item = _item(initial, rest, full, include_initial=False)
    pcm_queue = queue.Queue(maxsize=8)
    provider_eof = threading.Event()
    shared_lock = threading.Lock()
    shared = {
        "transport_state": "connecting",
        "provider_final": False,
        "connected_at": None,
        "first_text_sent_at": None,
        "llm_completed_at": None,
        "first_chunk_at": None,
        "first_pcm_at": None,
        "final_at": None,
        "last_network_at": time.perf_counter(),
        "max_network_gap_ms": 0.0,
        "source_chunks": [],
        "prepared_chunks": [],
        "source_chars": 0,
        "prepared_chars": 0,
        "sent_chunks": 0,
        "audio_chunks": 0,
        "received_bytes": 0,
        "final_payload": None,
        "provider_sent_text": "",
        "provider_expected_text": "",
        "provider_integrity_ok": False,
        "provider_mismatch_index": None,
        "provider_missing_chars": 0,
        "provider_duplicate_chars": 0,
        "arrival_timeline": [],
        "text_timeline": [],
        "capture_pcm": bytearray(),
        "error": None,
        "last_client_send_at": time.perf_counter(),
    }
    old_model = engine_module.PRIVATE_VOICE_TTD_MODEL
    old_format = engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT
    try:
        engine_module.PRIVATE_VOICE_TTD_MODEL = "eleven_v3"
        engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT = "pcm_24000"
        asyncio.run(
            engine._run_ttd_websocket(
                item,
                initial_source=initial,
                pcm_queue=pcm_queue,
                provider_eof=provider_eof,
                shared=shared,
                shared_lock=shared_lock,
            )
        )
    finally:
        engine_module.PRIVATE_VOICE_TTD_MODEL = old_model
        engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT = old_format

    decoded = np.concatenate(
        [np.asarray(pcm_queue.get(timeout=1.0), dtype=np.float32) for _ in range(2)]
    )
    expected = np.concatenate(
        [
            np.frombuffer(first_pcm, dtype="<i2").astype(np.float32) / 32768.0,
            np.frombuffer(second_pcm, dtype="<i2").astype(np.float32) / 32768.0,
        ]
    )
    assert np.array_equal(decoded, expected), (decoded, expected)
    assert socket.sent[0]["voices"]
    input_messages = [message for message in socket.sent if message.get("inputs")]
    assert len(input_messages) == 2, socket.sent
    assert all(
        entry["new_turn"] is False
        for message in input_messages
        for entry in message["inputs"]
    )
    assert [entry["text"] for message in input_messages for entry in message["inputs"]] == [
        initial,
        rest,
    ]
    provider_text = "".join(
        entry["text"]
        for message in input_messages
        for entry in message["inputs"]
    ).strip()
    assert provider_text == full.strip(), (provider_text, full)
    assert shared["provider_integrity_ok"] is True
    assert shared["provider_sent_text"] == shared["provider_expected_text"]
    assert shared["provider_missing_chars"] == 0
    assert shared["provider_duplicate_chars"] == 0
    assert sum(1 for message in socket.sent if message.get("close_socket")) == 1
    assert shared["provider_final"] is True
    assert shared["source_chars"] == len(full)
    assert shared["sent_chunks"] == 2
    assert shared["audio_chunks"] == 2
    assert shared["received_bytes"] == len(first_pcm) + len(second_pcm)
    assert provider_eof.is_set()


def test_ttd_completion_records_pcm_path_without_replay():
    import nana.voice.engine as engine_module

    engine = _make_engine()
    initial = _initial_text()
    rest = _next_text()
    full = initial + rest
    pcm = np.array([0, 1000, -1000, 2000], dtype="<i2").tobytes()
    socket = _FakeTtdSocket([pcm])
    engine._ttd_websocket_connect = lambda _uri: _FakeConnect(socket)
    item = _item(initial, rest, full)
    old_enabled = engine_module.PRIVATE_VOICE_TTD_ENABLED
    old_model = engine_module.PRIVATE_VOICE_TTD_MODEL
    old_format = engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT
    try:
        engine_module.PRIVATE_VOICE_TTD_ENABLED = True
        engine_module.PRIVATE_VOICE_TTD_MODEL = "eleven_v3"
        engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT = "pcm_24000"
        engine._tts_ttd_completion(item)
    finally:
        engine_module.PRIVATE_VOICE_TTD_ENABLED = old_enabled
        engine_module.PRIVATE_VOICE_TTD_MODEL = old_model
        engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT = old_format
    assert engine.state["last_private_voice_ttd_status"] == "completed", engine.state
    assert engine.state["last_private_voice_ttd_source_chars"] == len(full)
    assert engine.state["last_private_voice_ttd_missing_chars"] == 0
    assert engine.state["last_private_voice_ttd_duplicate_chars"] == 0
    assert engine.state["last_private_voice_ttd_replayed_chars"] == 0
    assert engine.state["last_private_voice_ttd_provider_integrity_ok"] is True
    assert engine.state["last_private_voice_ttd_provider_missing_chars"] == 0
    assert engine.state["last_private_voice_ttd_provider_duplicate_chars"] == 0
    assert engine.state["last_tts_strategy"] == "private_ttd_websocket_pcm"
    assert engine.state["last_audio_completed"] is True
    assert len(engine.lipsync.played) == len(pcm) // 2


def test_ttd_eof_single_sends_exactly_one_full_input_frame():
    import nana.voice.engine as engine_module

    engine = _make_engine()
    initial = _initial_text()
    rest = _next_text()
    full = initial + rest
    pcm = np.array([0, 1200, -1200, 2400], dtype="<i2").tobytes()
    socket = _FakeTtdSocket([pcm])
    engine._ttd_websocket_connect = lambda _uri: _FakeConnect(socket)
    item = _item(initial, rest, full)
    old_enabled = engine_module.PRIVATE_VOICE_TTD_ENABLED
    old_mode = engine_module.PRIVATE_VOICE_TTD_INPUT_MODE
    old_model = engine_module.PRIVATE_VOICE_TTD_MODEL
    old_format = engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT
    try:
        engine_module.PRIVATE_VOICE_TTD_ENABLED = True
        engine_module.PRIVATE_VOICE_TTD_INPUT_MODE = "eof_single"
        engine_module.PRIVATE_VOICE_TTD_MODEL = "eleven_v3"
        engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT = "pcm_24000"
        result = engine._tts_ttd_completion(item)
    finally:
        engine_module.PRIVATE_VOICE_TTD_ENABLED = old_enabled
        engine_module.PRIVATE_VOICE_TTD_INPUT_MODE = old_mode
        engine_module.PRIVATE_VOICE_TTD_MODEL = old_model
        engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT = old_format

    input_messages = [message for message in socket.sent if message.get("inputs")]
    assert result.audio_completed is True, result
    assert len(input_messages) == 1, socket.sent
    assert input_messages[0]["inputs"] == [
        {"text": full, "voice_id": engine_module.VOICE_ID, "new_turn": False}
    ]
    assert engine.state["last_private_voice_ttd_sent_chunks"] == 1
    assert engine.state["last_private_voice_ttd_provider_integrity_ok"] is True
    assert engine.state["last_tts_strategy"] == "private_ttd_eof_single_pcm"
    assert engine.state["last_private_voice_ttd_reason"] == "eof_single_pcm_drained"


def test_ttd_error_after_audio_never_replays_full_reply():
    import nana.voice.engine as engine_module

    engine = _make_engine()
    initial = _initial_text()
    rest = _next_text()
    full = initial + rest
    pcm = np.array([0, 1000, -1000, 2000], dtype="<i2").tobytes()
    socket = _ErrorAfterAudioSocket([pcm])
    engine._ttd_websocket_connect = lambda _uri: _FakeConnect(socket)
    item = _item(initial, rest, full)
    fallback_calls = []
    engine._tts_and_lipsync = lambda text, voice_mode="full": fallback_calls.append(
        (text, voice_mode)
    )
    old_enabled = engine_module.PRIVATE_VOICE_TTD_ENABLED
    old_model = engine_module.PRIVATE_VOICE_TTD_MODEL
    old_format = engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT
    try:
        engine_module.PRIVATE_VOICE_TTD_ENABLED = True
        engine_module.PRIVATE_VOICE_TTD_MODEL = "eleven_v3"
        engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT = "pcm_24000"
        result = engine._tts_ttd_completion(item)
    finally:
        engine_module.PRIVATE_VOICE_TTD_ENABLED = old_enabled
        engine_module.PRIVATE_VOICE_TTD_MODEL = old_model
        engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT = old_format

    assert result.audio_completed is False, result
    assert fallback_calls == [], fallback_calls
    assert engine.state["last_private_voice_ttd_status"] == "failed_after_audio"
    assert engine.state["last_private_voice_ttd_replayed_chars"] == 0
    assert engine.state["last_private_voice_ttd_missing_chars"] == 0


def test_ttd_readiness_fails_closed_for_missing_credentials_and_bad_format():
    import nana.voice.engine as engine_module

    engine = _make_engine()
    old = {
        "enabled": engine_module.PRIVATE_VOICE_TTD_ENABLED,
        "format": engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT,
        "key": engine_module.ELEVEN_API_KEY,
        "voice": engine_module.VOICE_ID,
    }
    try:
        engine_module.PRIVATE_VOICE_TTD_ENABLED = True
        engine_module.ELEVEN_API_KEY = "ELEVENLABS_KEY_CUA_BAN"
        assert engine.private_ttd_readiness() == (False, "eleven_credentials_missing")
        engine_module.ELEVEN_API_KEY = "test-key"
        engine_module.VOICE_ID = "test-voice"
        engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT = "mp3_44100_128"
        assert engine.private_ttd_readiness() == (False, "unsupported_output_format")
    finally:
        engine_module.PRIVATE_VOICE_TTD_ENABLED = old["enabled"]
        engine_module.PRIVATE_VOICE_TTD_OUTPUT_FORMAT = old["format"]
        engine_module.ELEVEN_API_KEY = old["key"]
        engine_module.VOICE_ID = old["voice"]


def test_ttd_subsequent_frames_use_target_without_delaying_initial_commit():
    from nana.runtime.private_voice_ttd import PrivateVoiceTtdTurn

    first = _initial_text()
    sentences = [
        "Câu thứ hai cung cấp thêm ngữ cảnh cho cùng một lượt nói. ",
        "Câu thứ ba tiếp tục liền mạch mà không mở turn mới. ",
        "Câu thứ tư đủ dài để kiểm tra cách gom frame phía sau. ",
        "Câu cuối cùng khép lại toàn bộ đoạn kiểm tra này.",
    ]
    full = first + "".join(sentences)
    requests = []
    turn = PrivateVoiceTtdTurn(
        lambda request: requests.append(request) or 3,
        minimum_chars=40,
        minimum_words=8,
        chunk_target_chars=120,
        chunk_max_chars=240,
        turn_id="ttd-framing",
    )
    for char in full:
        turn.feed(char)
    result = turn.finish(full)
    assert result.committed is True
    request = requests[0]
    chunks = []
    while True:
        chunk = request.text_queue.get(timeout=1.0)
        if chunk is None:
            break
        chunks.append(chunk)
    assert len(chunks[0].strip()) >= 40, chunks
    assert len(chunks[0].strip()) < 120, chunks
    assert first.startswith(chunks[0]), chunks
    assert len(chunks) < 1 + len(sentences), chunks
    assert all(len(chunk.strip()) <= 240 for chunk in chunks), chunks
    assert "".join(chunks) == full


def test_ttd_capture_writes_replayable_wav_and_text_free_timeline():
    import nana.voice.engine as engine_module

    engine = _make_engine()
    sample_rate = 24000
    silence = np.zeros(int(sample_rate * 0.30), dtype="<i2")
    tone = np.full(int(sample_rate * 0.10), 2000, dtype="<i2")
    pcm = silence.tobytes() + tone.tobytes()
    item = _item(_initial_text(), _next_text(), _initial_text() + _next_text())
    secret_text = "noi dung rieng khong duoc luu vao report"
    timeline = [
        {
            "index": 1,
            "turn_elapsed_ms": 1000.0,
            "gap_ms": 1000.0,
            "chunk_bytes": len(pcm),
            "chunk_audio_ms": 400.0,
            "cumulative_audio_ms": 400.0,
            "since_first_chunk_ms": 0.0,
            "supply_lead_ms": 400.0,
            "pcm_queue_before": 0,
            "pcm_queue_after": 1,
            "enqueue_wait_ms": 0.0,
        }
    ]
    text_timeline = [
        {
            "index": 1,
            "turn_elapsed_ms": 500.0,
            "source_chars": 50,
            "prepared_chars": 50,
            "source_leading_space": False,
            "source_trailing_space": True,
            "prepared_leading_space": False,
            "prepared_trailing_space": True,
            "cumulative_source_chars": 50,
            "cumulative_prepared_chars": 50,
        }
    ]
    old_enabled = engine_module.PRIVATE_VOICE_TTD_CAPTURE_ENABLED
    old_dir = engine_module.PRIVATE_VOICE_TTD_CAPTURE_DIR
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            engine_module.PRIVATE_VOICE_TTD_CAPTURE_ENABLED = True
            engine_module.PRIVATE_VOICE_TTD_CAPTURE_DIR = Path(temp_dir)
            wav_path, report_path, summary = engine._write_ttd_capture(
                item,
                pcm_bytes=pcm,
                sample_rate=sample_rate,
                arrival_timeline=timeline,
                text_timeline=text_timeline,
                source_text=secret_text,
                provider_sent_text=secret_text,
                provider_expected_text=secret_text,
            )
            assert wav_path and report_path
            with wave.open(wav_path, "rb") as wav_file:
                assert wav_file.getnchannels() == 1
                assert wav_file.getsampwidth() == 2
                assert wav_file.getframerate() == sample_rate
                assert wav_file.readframes(wav_file.getnframes()) == pcm
            report_raw = Path(report_path).read_text(encoding="utf-8")
            report = json.loads(report_raw)
            assert secret_text not in report_raw
            assert report["contains_text"] is False
            assert report["arrival_timeline"] == timeline
            assert report["text_timeline"] == text_timeline
            assert report["silence"]["span_count"] == 1
            assert report["silence"]["longest_silence_ms"] >= 280.0
            assert summary == report["silence"]
    finally:
        engine_module.PRIVATE_VOICE_TTD_CAPTURE_ENABLED = old_enabled
        engine_module.PRIVATE_VOICE_TTD_CAPTURE_DIR = old_dir


def test_ttd_status_uses_turn_timestamps_without_http_formula():
    from nana.core.status_voice import (
        interaction_latency_snapshot,
        print_interaction_latency_status,
    )
    import io
    from contextlib import redirect_stdout

    class StatusVoice:
        def snapshot(self):
            return {
                "private_voice_ttd_enabled": True,
                "private_voice_ttd_model": "eleven_v3",
                "private_voice_ttd_output_format": "pcm_24000",
                "last_private_voice_ttd_status": "completed",
                "last_private_voice_ttd_first_text_sent_ms": 420.0,
                "last_private_voice_ttd_first_chunk_ms": 1650.0,
                "last_private_voice_ttd_first_audio_ms": 1950.0,
                "last_private_voice_ttd_source_chars": 100,
                "last_private_voice_ttd_prepared_chars": 100,
                "last_private_voice_ttd_missing_chars": 0,
                "last_private_voice_ttd_duplicate_chars": 0,
                "last_private_voice_ttd_replayed_chars": 0,
                "last_stream_playback_state": "done",
                "last_stream_time_to_first_byte_ms": 900.0,
                "last_stream_time_to_first_audio_ms": 1400.0,
                "last_tts_text_len": 100,
                "last_audio_played_duration_ms": 5000.0,
            }

    voice = StatusVoice()
    snapshot = interaction_latency_snapshot(voice)
    assert snapshot["voice"]["source"] == "ttd_websocket_pcm"
    assert snapshot["voice"]["timing_scope"] == "turn"
    assert snapshot["combined"]["earliest_audible_ms"] == 1950.0
    assert snapshot["combined"]["serial_audible_ms"] is None
    output = io.StringIO()
    with redirect_stdout(output):
        print_interaction_latency_status(voice)
    rendered = output.getvalue()
    assert "TTD turn:" in rendered
    assert "serial HTTP bounds are not applicable" in rendered


def test_ttd_pipeline_selects_one_ttd_item_and_skips_http_overlap():
    import nana.cli.chat_turn_pipeline as pipeline

    initial = _initial_text()
    rest = _next_text()
    calls = []
    normal = []

    class FakeVoice:
        def private_ttd_readiness(self):
            return True, "ready"

        def record_private_ttd_bypass(self, reason):
            raise AssertionError(f"TTD unexpectedly bypassed: {reason}")

        def say_ttd(self, request, *, voice_mode="full"):
            calls.append((request, voice_mode))
            return 7

        def private_overlap_readiness(self):
            raise AssertionError("HTTP overlap must not be selected with TTD")

        def record_private_overlap_bypass(self, reason):
            raise AssertionError(f"HTTP overlap unexpectedly reached: {reason}")

        def say_overlap(self, *_args, **_kwargs):
            raise AssertionError("HTTP overlap must not be selected with TTD")

        def say(self, text, *, voice_mode=None):
            normal.append((text, voice_mode))
            return 8

        def snapshot(self):
            return {
                "queue_size": 0,
                "queue_maxsize": 5,
                "dropped_total": 0,
                "last_error": None,
                "worker_alive": True,
            }

    async def fake_stream(*_args, **_kwargs):
        yield initial
        await asyncio.sleep(0.03)
        yield rest

    async def fake_expression(*_args, **_kwargs):
        return None

    class FakeSpine:
        def increment_turn(self):
            return 1

    class FakeLock:
        def __enter__(self):
            return None

        def __exit__(self, *_args):
            return False

    old = {
        "ttd": pipeline.PRIVATE_VOICE_TTD_ENABLED,
        "overlap": pipeline.PRIVATE_VOICE_OVERLAP_ENABLED,
        "ask": pipeline.ask_gpt_stream,
        "expression": pipeline.trigger_expression_lifecycle,
        "spine": pipeline.get_memory_spine,
        "extract": pipeline.extract_important,
        "emotion": pipeline.update_emotion,
        "chat_log": pipeline.save_chat_log,
        "memory_save": pipeline.save_memory_async,
        "awareness_note": pipeline.awareness_memory_note_user_chat,
        "persona": pipeline.observe_text_for_persona,
        "awareness": pipeline.build_live_awareness_snapshot,
        "mark_chat": pipeline.mark_chat_time,
        "memory_lock": pipeline.memory_lock,
        "memory": pipeline.memory,
        "last_gpt_time": pipeline.cli_globals.last_gpt_time,
    }
    try:
        pipeline.PRIVATE_VOICE_TTD_ENABLED = True
        pipeline.PRIVATE_VOICE_OVERLAP_ENABLED = True
        pipeline.ask_gpt_stream = fake_stream
        pipeline.trigger_expression_lifecycle = fake_expression
        pipeline.get_memory_spine = lambda: FakeSpine()
        pipeline.extract_important = lambda *_args, **_kwargs: None
        pipeline.update_emotion = lambda *_args, **_kwargs: None
        pipeline.save_chat_log = lambda *_args, **_kwargs: None
        pipeline.save_memory_async = lambda *_args, **_kwargs: None
        pipeline.awareness_memory_note_user_chat = lambda *_args, **_kwargs: None
        pipeline.observe_text_for_persona = lambda *_args, **_kwargs: None
        pipeline.build_live_awareness_snapshot = lambda: {"ok": True}
        pipeline.mark_chat_time = lambda: None
        pipeline.memory_lock = FakeLock()
        pipeline.memory = {
            "chat_log": [],
            "short_term": [],
            "emotion": {"annoyance": 0.0, "playfulness": 0.0},
        }
        pipeline.cli_globals.last_gpt_time = 0
        voice = FakeVoice()
        result = asyncio.run(
            pipeline.handle_chat_turn(
                None,
                voice,
                "Nana kiểm tra TTD incremental cho Ba.",
                None,
            )
        )
        assert result is False
        assert len(calls) == 1, calls
        request, mode = calls[0]
        assert mode == "full"
        assert request.final_future.result(timeout=1.0).full_text == initial + rest
        assert normal == []
    finally:
        pipeline.PRIVATE_VOICE_TTD_ENABLED = old["ttd"]
        pipeline.PRIVATE_VOICE_OVERLAP_ENABLED = old["overlap"]
        pipeline.ask_gpt_stream = old["ask"]
        pipeline.trigger_expression_lifecycle = old["expression"]
        pipeline.get_memory_spine = old["spine"]
        pipeline.extract_important = old["extract"]
        pipeline.update_emotion = old["emotion"]
        pipeline.save_chat_log = old["chat_log"]
        pipeline.save_memory_async = old["memory_save"]
        pipeline.awareness_memory_note_user_chat = old["awareness_note"]
        pipeline.observe_text_for_persona = old["persona"]
        pipeline.build_live_awareness_snapshot = old["awareness"]
        pipeline.mark_chat_time = old["mark_chat"]
        pipeline.memory_lock = old["memory_lock"]
        pipeline.memory = old["memory"]
        pipeline.cli_globals.last_gpt_time = old["last_gpt_time"]


def main() -> None:
    tests = [
        test_ttd_builder_emits_exact_incremental_chunks_once,
        test_ttd_builder_eof_before_threshold_falls_back,
        test_ttd_websocket_is_one_incremental_same_turn_and_pcm_direct,
        test_ttd_completion_records_pcm_path_without_replay,
        test_ttd_eof_single_sends_exactly_one_full_input_frame,
        test_ttd_error_after_audio_never_replays_full_reply,
        test_ttd_readiness_fails_closed_for_missing_credentials_and_bad_format,
        test_ttd_subsequent_frames_use_target_without_delaying_initial_commit,
        test_ttd_capture_writes_replayable_wav_and_text_free_timeline,
        test_ttd_status_uses_turn_timestamps_without_http_formula,
        test_ttd_pipeline_selects_one_ttd_item_and_skips_http_overlap,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
        print("  PASS")
    print(f"smoke_private_voice_ttd: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
