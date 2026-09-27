"""Regression smoke for progressive Presence PCM source telemetry."""

from __future__ import annotations

from pathlib import Path
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nana.voice.engine as voice_engine_module
from nana.voice.engine import VoiceEngine


class FakeResponse:
    def __init__(self) -> None:
        self.closed = False
        self.chunk_size = None

    def iter_content(self, *, chunk_size: int):
        self.chunk_size = chunk_size
        yield b"\x01"
        yield b"\x02\x03"
        yield b"\x04"

    def close(self) -> None:
        self.closed = True


engine = VoiceEngine.__new__(VoiceEngine)
engine.state_lock = threading.Lock()
engine.state = {}
response = FakeResponse()
metrics = {
    "chunks": 0,
    "bytes": 0,
    "max_gap_ms": 0.0,
    "last_payload_at": None,
    "first_byte_at": None,
}

chunks = list(
    engine._iter_presence_pcm_response(
        response,
        time.perf_counter(),
        metrics,
    )
)

assert b"".join(chunks) == b"\x01\x02\x03\x04", chunks
assert response.chunk_size == 2048, response.chunk_size
assert response.closed is True
assert metrics["chunks"] == 3, metrics
assert metrics["bytes"] == 4, metrics
assert engine.state["last_presence_pcm_network_chunks"] == 3, engine.state
assert engine.state["last_presence_pcm_network_bytes"] == 4, engine.state
assert engine.state["last_presence_pcm_first_byte_ms"] >= 0.0, engine.state
assert engine.state["last_presence_pcm_max_network_gap_ms"] >= 0.0, engine.state

print("smoke_presence_pcm_source_telemetry: PASS (alignment + 2048B read + counters)")

original_prefetch_enabled = voice_engine_module.PRESENCE_PCM_STREAM_PREFETCH_ENABLED
voice_engine_module.PRESENCE_PCM_STREAM_PREFETCH_ENABLED = True


class PrefetchResponse:
    def __init__(self) -> None:
        self.closed = False
        self.read_count = 0
        self.all_read = threading.Event()

    def iter_content(self, *, chunk_size: int):
        assert chunk_size == 2048
        for index in range(10):
            self.read_count += 1
            yield bytes((index + 1, 0))
        self.all_read.set()

    def close(self) -> None:
        self.closed = True


prefetch_response = PrefetchResponse()
prefetch_metrics = {
    "chunks": 0,
    "bytes": 0,
    "max_gap_ms": 0.0,
    "last_payload_at": None,
    "first_byte_at": None,
}
stream = engine._iter_presence_pcm_response(
    prefetch_response,
    time.perf_counter(),
    prefetch_metrics,
)
first = next(stream)
assert first == b"\x01\x00", first
assert prefetch_response.all_read.wait(1.0), "provider was not read ahead"
assert prefetch_response.read_count == 10, prefetch_response.read_count
assert prefetch_metrics["prefetch_high_water"] >= 2, prefetch_metrics
assert b"".join([first, *stream]) == b"".join(
    bytes((index + 1, 0)) for index in range(10)
)
assert prefetch_response.closed is True

print("smoke_presence_pcm_source_telemetry: PASS (bounded producer read-ahead)")


class DelayedResponse:
    def __init__(self) -> None:
        self.closed = False
        self.release_second = threading.Event()

    def iter_content(self, *, chunk_size: int):
        assert chunk_size == 2048
        yield b"\x11\x00"
        assert self.release_second.wait(1.0)
        yield b"\x22\x00"

    def close(self) -> None:
        self.closed = True


delayed_response = DelayedResponse()
delayed_metrics = {
    "chunks": 0,
    "bytes": 0,
    "max_gap_ms": 0.0,
    "last_payload_at": None,
    "first_byte_at": None,
}
delayed_stream = engine._iter_presence_pcm_response(
    delayed_response,
    time.perf_counter(),
    delayed_metrics,
)
assert next(delayed_stream) == b"\x11\x00"
release_timer = threading.Timer(0.05, delayed_response.release_second.set)
release_timer.start()
assert next(delayed_stream) == b"\x22\x00"
assert list(delayed_stream) == []
release_timer.join(timeout=1.0)
assert delayed_response.closed is True
assert delayed_metrics["max_gap_ms"] >= 20.0, delayed_metrics
assert delayed_metrics["prefetch_starvations"] >= 1, delayed_metrics
assert delayed_metrics["max_prefetch_wait_ms"] >= 20.0, delayed_metrics
voice_engine_module.PRESENCE_PCM_STREAM_PREFETCH_ENABLED = original_prefetch_enabled

print("smoke_presence_pcm_source_telemetry: PASS (provider wait + starvation telemetry)")
