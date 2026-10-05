import io
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

from nana.runtime.logger import log_event


EDGE_FADE_MS = 10.0


@dataclass(frozen=True)
class PreparedAudio:
    data: object
    samplerate: int
    duration_seconds: float


@dataclass(frozen=True)
class PcmStreamPlaybackResult:
    completed: bool
    played_samples: int
    played_duration_ms: float
    start_buffer_ms: float
    rebuffer_count: int
    rebuffer_total_ms: float
    abort_reason: str = "none"
    refill_count: int = 0
    output_write_calls: int = 0
    output_underflow_count: int = 0
    playback_buffer_low_watermark_ms: float | None = None
    max_feed_gap_ms: float = 0.0
    last_underflow_buffer_ms: float | None = None
    last_underflow_queue_depth: int | None = None
    last_underflow_decoder_done: bool | None = None
    callback_calls: int = 0
    callback_status_underflows: int = 0
    ring_starvation_count: int = 0
    ring_low_watermark_ms: float | None = None
    max_callback_lateness_ms: float = 0.0
    feeder_refill_count: int = 0
    feeder_done: bool = False
    callback_finished: bool = False


@dataclass(frozen=True)
class ReceiptedPlaybackResult:
    """Per-call local sink evidence for the bounded public playback lane."""

    completed: bool
    first_audio_started: bool
    written_frames: int
    played_samples: int
    played_duration_ms: float
    abort_reason: str = "none"
    stop_confirmed: bool = True


class _SpscPcmRing:
    """Preallocated single-producer/single-consumer float32 ring."""

    def __init__(self, capacity_samples):
        capacity = max(1, int(capacity_samples))
        self._data = np.zeros(capacity, dtype=np.float32)
        self._capacity = capacity
        self._read_sequence = 0
        self._write_sequence = 0

    @property
    def capacity_samples(self):
        return self._capacity

    @property
    def available_samples(self):
        return max(0, self._write_sequence - self._read_sequence)

    @property
    def free_samples(self):
        return max(0, self._capacity - self.available_samples)

    def write(self, samples):
        pcm = np.asarray(samples, dtype=np.float32).reshape(-1)
        count = min(len(pcm), self.free_samples)
        if count <= 0:
            return 0
        offset = self._write_sequence % self._capacity
        first = min(count, self._capacity - offset)
        self._data[offset:offset + first] = pcm[:first]
        second = count - first
        if second > 0:
            self._data[:second] = pcm[first:first + second]
        self._write_sequence += count
        return count

    def read_into(self, output):
        target = np.asarray(output, dtype=np.float32).reshape(-1)
        count = min(len(target), self.available_samples)
        if count <= 0:
            return 0
        offset = self._read_sequence % self._capacity
        first = min(count, self._capacity - offset)
        target[:first] = self._data[offset:offset + first]
        second = count - first
        if second > 0:
            target[first:first + second] = self._data[:second]
        self._read_sequence += count
        return count


def _smooth_audio_edges(data, samplerate, fade_ms=EDGE_FADE_MS):
    """Apply a tiny PCM fade at chunk edges to avoid click/pop seams."""
    if data is None or len(data) == 0:
        return data
    try:
        fade_samples = int(float(samplerate) * float(fade_ms) / 1000.0)
    except Exception:
        return data
    if fade_samples <= 1:
        return data

    sample_count = len(data)
    fade_samples = min(fade_samples, max(1, sample_count // 2))
    if fade_samples <= 1:
        return data

    smoothed = np.asarray(data, dtype=np.float32).copy()
    ramp_in = np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    ramp_out = np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
    if smoothed.ndim == 1:
        smoothed[:fade_samples] *= ramp_in
        smoothed[-fade_samples:] *= ramp_out
    else:
        shape = (fade_samples,) + (1,) * (smoothed.ndim - 1)
        smoothed[:fade_samples, ...] *= ramp_in.reshape(shape)
        smoothed[-fade_samples:, ...] *= ramp_out.reshape(shape)
    return smoothed


def _smooth_stream_edge(data, samplerate, *, fade_in=False, fade_out=False, fade_ms=EDGE_FADE_MS):
    """Fade only the utterance boundary, never each streamed PCM packet."""
    if data is None or len(data) == 0 or not (fade_in or fade_out):
        return data
    try:
        fade_samples = int(float(samplerate) * float(fade_ms) / 1000.0)
    except Exception:
        return data
    fade_samples = min(fade_samples, len(data))
    if fade_samples <= 1:
        return data
    smoothed = np.asarray(data, dtype=np.float32).copy()
    if fade_in:
        smoothed[:fade_samples] *= np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
    if fade_out:
        smoothed[-fade_samples:] *= np.linspace(1.0, 0.0, fade_samples, dtype=np.float32)
    return smoothed


def _emit_pcm_level(callback, level):
    if callback is None:
        return False
    try:
        callback(float(level))
    except Exception:
        # An optional per-playback observer cannot affect audio delivery.
        pass
    return True


class LipsyncManager:
    def __init__(self):
        self.mouth = 0.0
        self._mouth_update_callback = None
        self._pcm_level_callback = None
        self.lipsync_thread = None
        self.stop_event = threading.Event()
        self.audio_lock = threading.Lock()

    def set_mouth_update_callback(self, callback):
        self._mouth_update_callback = callback if callable(callback) else None

    def set_pcm_level_callback(self, callback):
        """Observe played PCM amplitude before the legacy 2D mouth gain/clipping."""
        self._pcm_level_callback = callback if callable(callback) else None

    def _set_mouth(self, value, *, pcm_level=0.0):
        try:
            normalized = max(0.0, min(1.0, float(value or 0.0)))
        except (TypeError, ValueError):
            normalized = 0.0
        self.mouth = normalized
        pcm_callback = self._pcm_level_callback
        if pcm_callback is not None:
            try:
                pcm_callback(pcm_level)
            except Exception:
                # An optional avatar observer must never interrupt the audio callback.
                self._pcm_level_callback = None
        callback = self._mouth_update_callback
        if callback is not None:
            try:
                callback(normalized)
            except Exception as exc:
                log_event("voice", f"Avatar mouth callback failed: {exc}")

    def start(self, audio_path):
        self.stop()
        self.stop_event.clear()

        def run():
            self.play_blocking(audio_path)

        self.lipsync_thread = threading.Thread(target=run, daemon=True)
        self.lipsync_thread.start()

    def play_blocking(self, audio_path):
        self.stop_event.clear()
        data, samplerate = self._read_audio(audio_path)
        self._play_samples(data, samplerate)

    def play_audio_data(self, audio_bytes):
        """Play raw audio bytes (mp3 or pcm) directly — no temp file. Blocks until done."""
        self.stop_event.clear()
        try:
            data, samplerate = self._decode_audio_bytes(audio_bytes)
        except Exception as exc:
            log_event("voice", f"Lipsync decode audio_bytes failed: {exc}")
            print(f"Lipsync decode error: {exc}")
            return
        if data is None:
            return
        self._play_samples(data, samplerate)

    def play_audio_nonblocking(self, audio_bytes, on_done=None):
        """Non-blocking variant: streams audio in a background thread and
        fires ``on_done()`` after the mouth-fade tail finishes. Returns
        immediately so the sequencer can prepare the next chunk.

        The lipsync stays purely PCM-driven (it only knows about the byte
        stream; chunk index is the sequencer's concern).
        """
        def _runner():
            try:
                data, samplerate = self._decode_audio_bytes(audio_bytes)
                if data is None:
                    return
                self._play_samples(data, samplerate)
            except Exception as exc:
                log_event("voice", f"Lipsync non-blocking playback error: {exc}")
                print(f"Lipsync error: {exc}")
            finally:
                if on_done is not None:
                    try:
                        on_done()
                    except Exception as exc:
                        log_event("voice", f"Lipsync on_done callback error: {exc}")

        self.stop_event.clear()
        thread = threading.Thread(target=_runner, daemon=True, name="lipsync-nonblocking")
        thread.start()

    def prepare_audio_bytes(self, audio_bytes):
        """Decode once so the voice engine can use the real playback duration."""
        try:
            data, samplerate = self._decode_audio_bytes(audio_bytes)
        except Exception as exc:
            log_event("voice", f"Lipsync prepare audio failed: {exc}")
            return None
        if data is None or samplerate is None or len(data) == 0:
            return None
        try:
            duration_seconds = float(len(data)) / float(samplerate)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        if duration_seconds <= 0:
            return None
        return PreparedAudio(data=data, samplerate=int(samplerate), duration_seconds=duration_seconds)

    def play_prepared_audio_nonblocking(self, prepared, on_done=None):
        """Play pre-decoded audio and report whether all samples were written."""

        def _runner():
            success = False
            try:
                success = bool(self._play_samples(prepared.data, prepared.samplerate))
            except Exception as exc:
                log_event("voice", f"Lipsync prepared playback error: {exc}")
                print(f"Lipsync error: {exc}")
            finally:
                if on_done is not None:
                    try:
                        on_done(success)
                    except Exception as exc:
                        log_event("voice", f"Lipsync prepared on_done callback error: {exc}")

        self.stop_event.clear()
        thread = threading.Thread(target=_runner, daemon=True, name="lipsync-prepared")
        self.lipsync_thread = thread
        thread.start()
        return thread

    def play_prepared_audio_receipted(
        self,
        prepared,
        *,
        before_first_audio=None,
        on_first_audio=None,
        on_sink_frame=None,
        on_pcm_level=None,
        emit_mouth=True,
        output_stream_factory=None,
    ):
        """Synchronously play one prepared buffer with exact first-write evidence.

        This entry point is intentionally separate from the legacy nonblocking
        path. It is used by Stream CUM5 so one request maps to one terminal
        result and cannot be coalesced with private voice work.
        """

        self.stop_event.clear()
        return self._play_samples_receipted(
            prepared.data,
            prepared.samplerate,
            before_first_audio=before_first_audio,
            on_first_audio=on_first_audio,
            on_sink_frame=on_sink_frame,
            on_pcm_level=on_pcm_level,
            emit_mouth=emit_mouth,
            output_stream_factory=output_stream_factory,
        )

    def play_pcm_stream_nonblocking(
        self,
        pcm_queue,
        decoder_eof,
        *,
        samplerate=44100,
        startup_buffer_ms=300,
        stall_timeout_s=30,
        on_state=None,
        on_done=None,
        on_first_audio=None,
        on_pcm_level=None,
        output_stream_factory=None,
        callback_output=False,
    ):
        """Play streamed PCM through one OutputStream with fixed buffering."""

        def _runner():
            try:
                playback_method = (
                    self._play_pcm_callback_queue
                    if callback_output
                    else self._play_pcm_queue
                )
                result = playback_method(
                    pcm_queue,
                    decoder_eof,
                    samplerate=int(samplerate),
                    startup_buffer_ms=int(startup_buffer_ms),
                    stall_timeout_s=float(stall_timeout_s),
                    on_state=on_state,
                    on_first_audio=on_first_audio,
                    on_pcm_level=on_pcm_level,
                    output_stream_factory=output_stream_factory,
                )
            except Exception as exc:
                log_event("voice", f"PCM stream runner error: {exc}")
                result = PcmStreamPlaybackResult(
                    completed=False,
                    played_samples=0,
                    played_duration_ms=0.0,
                    start_buffer_ms=0.0,
                    rebuffer_count=0,
                    rebuffer_total_ms=0.0,
                    abort_reason="playback_error",
                )
            if on_done is not None:
                try:
                    on_done(result)
                except Exception as exc:
                    log_event("voice", f"PCM stream on_done callback error: {exc}")

        self.stop_event.clear()
        thread = threading.Thread(target=_runner, daemon=True, name="lipsync-pcm-stream")
        self.lipsync_thread = thread
        thread.start()
        return thread

    def _play_pcm_callback_queue(
        self,
        pcm_queue,
        decoder_eof,
        *,
        samplerate,
        startup_buffer_ms,
        stall_timeout_s,
        on_state=None,
        on_first_audio=None,
        on_pcm_level=None,
        output_stream_factory=None,
    ):
        """Play streamed PCM from a prebuffered SPSC ring via PortAudio callback."""
        frame_size = max(1, int(samplerate * 0.02))
        target_samples = max(frame_size, int(samplerate * max(0, startup_buffer_ms) / 1000.0))
        ring_capacity = max(target_samples * 4, int(samplerate))
        ring = _SpscPcmRing(ring_capacity)
        fade_samples = max(0, int(float(samplerate) * EDGE_FADE_MS / 1000.0))
        fade_in_ramp = (
            np.linspace(0.0, 1.0, fade_samples, dtype=np.float32)
            if fade_samples > 1
            else None
        )
        feeder_done_event = threading.Event()
        feeder_stop_event = threading.Event()
        playback_started_event = threading.Event()
        callback_finished_event = threading.Event()
        feeder_state = {
            "clean": False,
            "error": None,
            "refills": 0,
            "last_progress_at": time.perf_counter(),
        }
        callback_state = {
            "calls": 0,
            "status_underflows": 0,
            "starvations": 0,
            "ring_low_watermark_ms": None,
            "max_lateness_ms": 0.0,
            "last_at": None,
            "last_frames": None,
            "played_samples": 0,
            "previous_mouth": 0.0,
            "rebuffering": False,
            "rebuffer_started_at": None,
            "rebuffer_total_ms": 0.0,
            "draining": False,
            "stop_requested": False,
            "last_underflow_buffer_ms": None,
            "last_underflow_queue_depth": None,
            "last_underflow_decoder_done": None,
            "pcm_level_emitted": False,
        }

        def _emit_state(state):
            if on_state is not None:
                try:
                    on_state(state)
                except Exception as exc:
                    log_event("voice", f"PCM callback state callback error: {exc}")

        def _write_ring(pcm):
            offset = 0
            while offset < len(pcm):
                if self.stop_event.is_set() or feeder_stop_event.is_set():
                    return False
                if playback_started_event.is_set():
                    write_limit = ring.free_samples
                else:
                    write_limit = min(
                        ring.free_samples,
                        max(0, target_samples - ring.available_samples),
                    )
                if write_limit <= 0:
                    time.sleep(0.001)
                    continue
                written = ring.write(pcm[offset:offset + write_limit])
                if written > 0:
                    offset += written
                    feeder_state["last_progress_at"] = time.perf_counter()
                    continue
                time.sleep(0.001)
            return True

        def _apply_fade_in(pcm, fade_position):
            if fade_in_ramp is None or fade_position >= fade_samples or len(pcm) == 0:
                return fade_position
            count = min(len(pcm), fade_samples - fade_position)
            pcm[:count] *= fade_in_ramp[fade_position:fade_position + count]
            return fade_position + count

        def _feeder():
            tail = np.empty(0, dtype=np.float32)
            fade_position = 0
            try:
                while not self.stop_event.is_set() and not feeder_stop_event.is_set():
                    try:
                        item = pcm_queue.get(timeout=0.02)
                    except queue.Empty:
                        if decoder_eof.is_set() and pcm_queue.empty():
                            break
                        continue
                    pcm = np.asarray(item, dtype=np.float32).reshape(-1)
                    if len(pcm) == 0:
                        continue
                    feeder_state["refills"] += 1
                    feeder_state["last_progress_at"] = time.perf_counter()
                    combined = np.concatenate((tail, pcm)) if len(tail) else pcm.copy()
                    if fade_samples > 1 and len(combined) <= fade_samples:
                        tail = combined
                        continue
                    if fade_samples > 1:
                        emit = combined[:-fade_samples].copy()
                        tail = combined[-fade_samples:].copy()
                    else:
                        emit = combined
                        tail = np.empty(0, dtype=np.float32)
                    fade_position = _apply_fade_in(emit, fade_position)
                    if not _write_ring(emit):
                        return

                if self.stop_event.is_set() or feeder_stop_event.is_set():
                    return
                final_pcm = tail.copy()
                fade_position = _apply_fade_in(final_pcm, fade_position)
                del fade_position
                if len(final_pcm):
                    final_pcm = _smooth_stream_edge(
                        final_pcm,
                        samplerate,
                        fade_out=True,
                    )
                    if not _write_ring(final_pcm):
                        return
                feeder_state["clean"] = bool(
                    not feeder_stop_event.is_set()
                    and decoder_eof.is_set()
                    and pcm_queue.empty()
                )
            except Exception as exc:
                feeder_state["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                feeder_done_event.set()

        feeder_thread = threading.Thread(
            target=_feeder,
            daemon=True,
            name="lipsync-pcm-ring-feeder",
        )
        _emit_state("buffering")
        feeder_thread.start()

        def _stop_feeder(timeout=1.0):
            feeder_stop_event.set()
            playback_started_event.set()
            feeder_thread.join(timeout=timeout)
            return feeder_done_event.is_set()

        while ring.available_samples < target_samples and not feeder_done_event.is_set():
            if self.stop_event.is_set():
                _stop_feeder()
                return PcmStreamPlaybackResult(
                    False,
                    0,
                    0.0,
                    (ring.available_samples / float(samplerate)) * 1000.0,
                    0,
                    0.0,
                    "shutdown_cancelled",
                    feeder_done=feeder_done_event.is_set(),
                    feeder_refill_count=feeder_state["refills"],
                )
            if time.perf_counter() - feeder_state["last_progress_at"] >= stall_timeout_s:
                _stop_feeder()
                return PcmStreamPlaybackResult(
                    False,
                    0,
                    0.0,
                    (ring.available_samples / float(samplerate)) * 1000.0,
                    0,
                    0.0,
                    "pcm_stall_timeout",
                    feeder_done=feeder_done_event.is_set(),
                    feeder_refill_count=feeder_state["refills"],
                )
            time.sleep(0.002)

        start_buffer_ms = (ring.available_samples / float(samplerate)) * 1000.0
        if ring.available_samples == 0 and feeder_done_event.is_set():
            _stop_feeder()
            reason = "pcm_feeder_error" if feeder_state["error"] else "decoder_empty"
            return PcmStreamPlaybackResult(
                False,
                0,
                0.0,
                start_buffer_ms,
                0,
                0.0,
                reason,
                feeder_done=True,
                feeder_refill_count=feeder_state["refills"],
            )

        def _finished_callback():
            callback_finished_event.set()

        observed_status_underflows = 0

        def _capture_underflow_queue_depth():
            nonlocal observed_status_underflows
            current = int(callback_state["status_underflows"])
            if current <= observed_status_underflows:
                return
            try:
                callback_state["last_underflow_queue_depth"] = int(pcm_queue.qsize())
            except Exception:
                callback_state["last_underflow_queue_depth"] = None
            observed_status_underflows = current

        first_audio_sent = False
        def _note_first_audio(count):
            nonlocal first_audio_sent
            if count > 0 and not first_audio_sent:
                first_audio_sent = True
                if on_first_audio is not None:
                    try:
                        on_first_audio()
                    except Exception:
                        pass

        def _audio_callback(outdata, frames, time_info, status):
            del time_info
            now = time.perf_counter()
            previous_at = callback_state["last_at"]
            previous_frames = callback_state["last_frames"]
            if previous_at is not None and previous_frames is not None:
                actual_interval_ms = (now - previous_at) * 1000.0
                expected_interval_ms = (previous_frames / float(samplerate)) * 1000.0
                callback_state["max_lateness_ms"] = max(
                    callback_state["max_lateness_ms"],
                    max(0.0, actual_interval_ms - expected_interval_ms),
                )
            callback_state["last_at"] = now
            callback_state["last_frames"] = int(frames)
            callback_state["calls"] += 1
            outdata.fill(0.0)

            if bool(getattr(status, "output_underflow", False)):
                callback_state["status_underflows"] += 1
                callback_state["last_underflow_buffer_ms"] = (
                    ring.available_samples / float(samplerate)
                ) * 1000.0
                callback_state["last_underflow_decoder_done"] = bool(decoder_eof.is_set())

            if self.stop_event.is_set():
                raise sd.CallbackAbort

            available = ring.available_samples
            feeder_finished = feeder_done_event.is_set()
            if not feeder_finished:
                current_ms = (available / float(samplerate)) * 1000.0
                current_low = callback_state["ring_low_watermark_ms"]
                if current_low is None or current_ms < current_low:
                    callback_state["ring_low_watermark_ms"] = current_ms

            channel = outdata[:, 0] if getattr(outdata, "ndim", 1) > 1 else outdata

            if callback_state["rebuffering"]:
                if feeder_finished or available >= target_samples:
                    callback_state["rebuffering"] = False
                    started = callback_state["rebuffer_started_at"]
                    if started is not None:
                        callback_state["rebuffer_total_ms"] += (now - started) * 1000.0
                    callback_state["rebuffer_started_at"] = None
                else:
                    return

            if available < frames and not feeder_finished:
                read_count = ring.read_into(channel)
                callback_state["played_samples"] += read_count
                _note_first_audio(read_count)
                if read_count > 0:
                    volume = float(np.abs(channel[:read_count]).mean())
                    callback_state["pcm_level_emitted"] = _emit_pcm_level(
                        on_pcm_level,
                        volume,
                    ) or callback_state["pcm_level_emitted"]
                    mouth = min(volume * 18, 1.0)
                    mouth = callback_state["previous_mouth"] * 0.8 + mouth * 0.2
                    callback_state["previous_mouth"] = mouth
                    self._set_mouth(mouth, pcm_level=volume)
                callback_state["starvations"] += 1
                callback_state["rebuffering"] = True
                callback_state["rebuffer_started_at"] = now
                return

            read_count = ring.read_into(channel)
            callback_state["played_samples"] += read_count
            _note_first_audio(read_count)
            if read_count > 0:
                volume = float(np.abs(channel[:read_count]).mean())
                callback_state["pcm_level_emitted"] = _emit_pcm_level(
                    on_pcm_level,
                    volume,
                ) or callback_state["pcm_level_emitted"]
                mouth = min(volume * 18, 1.0)
                mouth = callback_state["previous_mouth"] * 0.8 + mouth * 0.2
                callback_state["previous_mouth"] = mouth
                self._set_mouth(mouth, pcm_level=volume)

            if feeder_finished and ring.available_samples == 0:
                callback_state["draining"] = True
                callback_state["stop_requested"] = True
                raise sd.CallbackStop

        stream_factory = output_stream_factory or sd.OutputStream
        abort_reason = "none"
        last_reported_state = "playing"
        stream = None
        try:
            with self.audio_lock:
                with stream_factory(
                    samplerate=samplerate,
                    channels=1,
                    dtype="float32",
                    blocksize=0,
                    callback=_audio_callback,
                    finished_callback=_finished_callback,
                ) as stream:
                    playback_started_event.set()
                    _emit_state("playing")
                    while not callback_finished_event.wait(timeout=0.01):
                        _capture_underflow_queue_depth()
                        if self.stop_event.is_set():
                            abort_reason = "shutdown_cancelled"
                            abort = getattr(stream, "abort", None)
                            if callable(abort):
                                abort()
                            break
                        if feeder_state["error"]:
                            abort_reason = "pcm_feeder_error"
                            abort = getattr(stream, "abort", None)
                            if callable(abort):
                                abort()
                            break
                        if (
                            not feeder_done_event.is_set()
                            and ring.available_samples == 0
                            and time.perf_counter() - feeder_state["last_progress_at"] >= stall_timeout_s
                        ):
                            abort_reason = "pcm_stall_timeout"
                            abort = getattr(stream, "abort", None)
                            if callable(abort):
                                abort()
                            break
                        desired_state = (
                            "draining"
                            if callback_state["draining"]
                            else "rebuffering"
                            if callback_state["rebuffering"]
                            else "playing"
                        )
                        if desired_state != last_reported_state:
                            _emit_state(desired_state)
                            last_reported_state = desired_state
                    if abort_reason != "none" and not callback_finished_event.is_set():
                        callback_finished_event.wait(timeout=0.5)
                    _capture_underflow_queue_depth()
        except Exception as exc:
            del stream
            if self.stop_event.is_set():
                abort_reason = "shutdown_cancelled"
            else:
                abort_reason = "playback_error"
                log_event("voice", f"PCM callback playback error: {exc}")
        finally:
            _capture_underflow_queue_depth()
            _stop_feeder(timeout=2.0)
            for _ in range(3):
                self._set_mouth(self.mouth * 0.5)
                time.sleep(0.01)
            self._set_mouth(0.0)
            if callback_state["pcm_level_emitted"]:
                _emit_pcm_level(on_pcm_level, 0.0)

        if abort_reason == "none" and self.stop_event.is_set():
            abort_reason = "shutdown_cancelled"
        elif abort_reason == "none" and feeder_state["error"]:
            abort_reason = "pcm_feeder_error"

        feeder_clean = bool(feeder_state["clean"] and feeder_done_event.is_set())
        callback_finished = callback_finished_event.is_set()
        completed = bool(
            abort_reason == "none"
            and feeder_clean
            and ring.available_samples == 0
            and pcm_queue.empty()
            and callback_state["stop_requested"]
            and callback_finished
        )
        if completed:
            _emit_state("done")
        elif abort_reason == "shutdown_cancelled":
            _emit_state("cancelled")
        else:
            _emit_state("aborted")
            if abort_reason == "none":
                abort_reason = "incomplete_callback_playback"

        low_watermark = callback_state["ring_low_watermark_ms"]
        return PcmStreamPlaybackResult(
            completed=completed,
            played_samples=callback_state["played_samples"],
            played_duration_ms=(callback_state["played_samples"] / float(samplerate)) * 1000.0,
            start_buffer_ms=round(start_buffer_ms, 1),
            rebuffer_count=callback_state["starvations"],
            rebuffer_total_ms=round(callback_state["rebuffer_total_ms"], 1),
            abort_reason=abort_reason,
            refill_count=0,
            output_write_calls=0,
            output_underflow_count=callback_state["status_underflows"],
            playback_buffer_low_watermark_ms=None,
            max_feed_gap_ms=0.0,
            last_underflow_buffer_ms=(
                None
                if callback_state["last_underflow_buffer_ms"] is None
                else round(callback_state["last_underflow_buffer_ms"], 1)
            ),
            last_underflow_queue_depth=callback_state["last_underflow_queue_depth"],
            last_underflow_decoder_done=callback_state["last_underflow_decoder_done"],
            callback_calls=callback_state["calls"],
            callback_status_underflows=callback_state["status_underflows"],
            ring_starvation_count=callback_state["starvations"],
            ring_low_watermark_ms=(None if low_watermark is None else round(low_watermark, 1)),
            max_callback_lateness_ms=round(callback_state["max_lateness_ms"], 1),
            feeder_refill_count=feeder_state["refills"],
            feeder_done=feeder_done_event.is_set(),
            callback_finished=callback_finished,
        )

    def _play_pcm_queue(
        self,
        pcm_queue,
        decoder_eof,
        *,
        samplerate,
        startup_buffer_ms,
        stall_timeout_s,
        on_state=None,
        on_first_audio=None,
        on_pcm_level=None,
        output_stream_factory=None,
    ):
        buffers = deque()
        buffered_samples = 0
        played_samples = 0
        refill_count = 0
        rebuffer_count = 0
        rebuffer_total_ms = 0.0
        output_write_calls = 0
        output_underflow_count = 0
        playback_buffer_low_watermark_ms = None
        max_feed_gap_ms = 0.0
        last_underflow_buffer_ms = None
        last_underflow_queue_depth = None
        last_underflow_decoder_done = None
        first_frame = True
        last_progress_at = time.perf_counter()
        last_write_ended_at = None
        frame_size = max(1, int(samplerate * 0.02))
        target_samples = max(frame_size, int(samplerate * max(0, startup_buffer_ms) / 1000.0))

        def _emit_state(state):
            if on_state is not None:
                try:
                    on_state(state)
                except Exception as exc:
                    log_event("voice", f"PCM stream state callback error: {exc}")

        def _append_pcm(item):
            nonlocal buffered_samples, last_progress_at
            if item is None:
                return
            pcm = np.asarray(item, dtype=np.float32).reshape(-1)
            if len(pcm) == 0:
                return
            buffers.append(pcm)
            buffered_samples += len(pcm)
            last_progress_at = time.perf_counter()

        def _fill_to(target):
            while buffered_samples < target:
                if self.stop_event.is_set():
                    return "cancelled"
                if decoder_eof.is_set() and pcm_queue.empty():
                    return "eof"
                if time.perf_counter() - last_progress_at >= stall_timeout_s:
                    return "pcm_stall_timeout"
                try:
                    item = pcm_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                _append_pcm(item)
            return "ready"

        def _top_up_nowait(target):
            added_samples = 0
            while buffered_samples < target:
                try:
                    item = pcm_queue.get_nowait()
                except queue.Empty:
                    break
                before = buffered_samples
                _append_pcm(item)
                added_samples += max(0, buffered_samples - before)
            return added_samples

        def _take_samples(count):
            nonlocal buffered_samples
            parts = []
            remaining = min(count, buffered_samples)
            while remaining > 0 and buffers:
                current = buffers.popleft()
                if len(current) <= remaining:
                    parts.append(current)
                    remaining -= len(current)
                    buffered_samples -= len(current)
                else:
                    parts.append(current[:remaining])
                    buffers.appendleft(current[remaining:])
                    buffered_samples -= remaining
                    remaining = 0
            if not parts:
                return np.empty(0, dtype=np.float32)
            if len(parts) == 1:
                return parts[0]
            return np.concatenate(parts)

        _emit_state("buffering")
        fill_state = _fill_to(target_samples)
        start_buffer_ms = (buffered_samples / float(samplerate)) * 1000.0
        if fill_state == "cancelled":
            return PcmStreamPlaybackResult(False, 0, 0.0, start_buffer_ms, 0, 0.0, "shutdown_cancelled")
        if fill_state == "pcm_stall_timeout":
            return PcmStreamPlaybackResult(False, 0, 0.0, start_buffer_ms, 0, 0.0, fill_state)
        if buffered_samples == 0 and decoder_eof.is_set():
            return PcmStreamPlaybackResult(False, 0, 0.0, 0.0, 0, 0.0, "decoder_empty")

        stream_factory = output_stream_factory or sd.OutputStream
        completed = False
        abort_reason = "none"
        previous_mouth = 0.0
        pcm_level_emitted = False
        try:
            with self.audio_lock:
                with stream_factory(samplerate=samplerate, channels=1, dtype="float32") as stream:
                    _emit_state("playing")
                    while True:
                        if self.stop_event.is_set():
                            abort_reason = "shutdown_cancelled"
                            break

                        decoder_drained = decoder_eof.is_set() and pcm_queue.empty()
                        if not decoder_drained:
                            added_samples = _top_up_nowait(target_samples)
                            if added_samples > 0:
                                refill_count += 1
                            decoder_drained = decoder_eof.is_set() and pcm_queue.empty()

                        if not decoder_eof.is_set():
                            current_buffer_ms = (buffered_samples / float(samplerate)) * 1000.0
                            if (
                                playback_buffer_low_watermark_ms is None
                                or current_buffer_ms < playback_buffer_low_watermark_ms
                            ):
                                playback_buffer_low_watermark_ms = current_buffer_ms

                        if (
                            buffered_samples < frame_size
                            and pcm_queue.empty()
                            and not decoder_eof.is_set()
                        ):
                            rebuffer_count += 1
                            rebuffer_started = time.perf_counter()
                            _emit_state("rebuffering")
                            fill_state = _fill_to(target_samples)
                            rebuffer_total_ms += (time.perf_counter() - rebuffer_started) * 1000.0
                            if fill_state == "cancelled":
                                abort_reason = "shutdown_cancelled"
                                break
                            if fill_state == "pcm_stall_timeout":
                                abort_reason = fill_state
                                break
                            _emit_state("playing")

                        decoder_drained = decoder_eof.is_set() and pcm_queue.empty()
                        if buffered_samples == 0 and decoder_drained:
                            completed = True
                            break
                        if buffered_samples == 0:
                            continue

                        final_frame = decoder_drained and buffered_samples <= frame_size
                        if final_frame:
                            _emit_state("draining")
                        frame = _take_samples(frame_size)
                        frame = _smooth_stream_edge(
                            frame,
                            samplerate,
                            fade_in=first_frame,
                            fade_out=final_frame,
                        )
                        first_frame = False
                        volume = float(np.abs(frame).mean()) if len(frame) else 0.0
                        mouth = min(volume * 18, 1.0)
                        mouth = previous_mouth * 0.8 + mouth * 0.2
                        previous_mouth = mouth
                        self._set_mouth(mouth, pcm_level=volume)
                        write_started_at = time.perf_counter()
                        if last_write_ended_at is not None:
                            feed_gap_ms = (write_started_at - last_write_ended_at) * 1000.0
                            max_feed_gap_ms = max(max_feed_gap_ms, feed_gap_ms)
                        underflowed = bool(stream.write(frame.reshape(-1, 1)))
                        output_write_calls += 1
                        if output_write_calls == 1 and on_first_audio is not None:
                            try:
                                on_first_audio()
                            except Exception:
                                pass
                        if on_pcm_level is not None:
                            pcm_level_emitted = _emit_pcm_level(
                                on_pcm_level,
                                volume,
                            ) or pcm_level_emitted
                        if underflowed:
                            output_underflow_count += 1
                            last_underflow_buffer_ms = (
                                buffered_samples / float(samplerate)
                            ) * 1000.0
                            try:
                                last_underflow_queue_depth = int(pcm_queue.qsize())
                            except Exception:
                                last_underflow_queue_depth = None
                            last_underflow_decoder_done = bool(decoder_eof.is_set())
                        last_write_ended_at = time.perf_counter()
                        played_samples += len(frame)
        except Exception as exc:
            log_event("voice", f"PCM stream playback error: {exc}")
            abort_reason = "playback_error"
        finally:
            for _ in range(3):
                self._set_mouth(self.mouth * 0.5)
                time.sleep(0.01)
            self._set_mouth(0.0)
            if pcm_level_emitted:
                _emit_pcm_level(on_pcm_level, 0.0)

        if completed:
            _emit_state("done")
            abort_reason = "none"
        elif abort_reason == "shutdown_cancelled":
            _emit_state("cancelled")
        else:
            _emit_state("aborted")
        return PcmStreamPlaybackResult(
            completed=completed,
            played_samples=played_samples,
            played_duration_ms=(played_samples / float(samplerate)) * 1000.0,
            start_buffer_ms=round(start_buffer_ms, 1),
            rebuffer_count=rebuffer_count,
            rebuffer_total_ms=round(rebuffer_total_ms, 1),
            abort_reason=abort_reason,
            refill_count=refill_count,
            output_write_calls=output_write_calls,
            output_underflow_count=output_underflow_count,
            playback_buffer_low_watermark_ms=(
                None
                if playback_buffer_low_watermark_ms is None
                else round(playback_buffer_low_watermark_ms, 1)
            ),
            max_feed_gap_ms=round(max_feed_gap_ms, 1),
            last_underflow_buffer_ms=(
                None if last_underflow_buffer_ms is None else round(last_underflow_buffer_ms, 1)
            ),
            last_underflow_queue_depth=last_underflow_queue_depth,
            last_underflow_decoder_done=last_underflow_decoder_done,
        )

    def _decode_audio_bytes(self, audio_bytes):
        """Decode raw audio bytes (mp3 or pcm) into (float32 ndarray, samplerate)."""
        if audio_bytes is None or len(audio_bytes) == 0:
            return None, None
        try:
            data, samplerate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
            return data, samplerate
        except Exception:
            pass
        try:
            from pydub import AudioSegment
        except ImportError:
            log_event("voice", "pydub not installed, cannot decode audio bytes")
            print("🔇 Cần cài pydub: pip install pydub")
            return None, None
        try:
            audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3").set_channels(1)
            samples = np.array(audio.get_array_of_samples())
            max_int = float(1 << (8 * audio.sample_width - 1))
            data = samples.astype(np.float32) / max_int
            samplerate = audio.frame_rate
            return data, samplerate
        except Exception as exc:
            log_event("voice", f"Lipsync decode audio_bytes failed: {exc}")
            print(f"Lipsync decode error: {exc}")
            return None, None

    def _play_samples(self, data, samplerate):
        if data is None or len(data) == 0:
            return False
        if len(data.shape) > 1:
            data = data[:, 0]
        data = data.astype(np.float32, copy=False)
        max_value = float(np.max(np.abs(data)))
        if max_value > 0:
            data = (data / max_value) * 0.9
        data = _smooth_audio_edges(data, samplerate)
        try:
            frame_size = int(samplerate * 0.02)
            previous = 0.0
            completed = True
            with self.audio_lock:
                with sd.OutputStream(samplerate=samplerate, channels=1, dtype="float32") as stream:
                    for index in range(0, len(data), frame_size):
                        if self.stop_event.is_set():
                            completed = False
                            break
                        frame = data[index:index + frame_size]
                        if len(frame) < frame_size:
                            frame = np.pad(frame, (0, frame_size - len(frame)))
                        volume = float(np.abs(frame).mean())
                        mouth = min(volume * 18, 1.0)
                        mouth = previous * 0.8 + mouth * 0.2
                        previous = mouth
                        self._set_mouth(mouth, pcm_level=volume)
                        stream.write(frame.reshape(-1, 1))
            # Short mouth-fade only (no audio silence).
            for _ in range(3):
                if self.stop_event.is_set():
                    completed = False
                    break
                self._set_mouth(self.mouth * 0.5)
                time.sleep(0.01)
            self._set_mouth(0.0)
            return completed
        except Exception as exc:
            log_event("voice", f"Lipsync playback error: {exc}")
            print(f"Lipsync error: {exc}")
            self._set_mouth(0.0)
            return False

    def _play_samples_receipted(
        self,
        data,
        samplerate,
        *,
        before_first_audio=None,
        on_first_audio=None,
        on_sink_frame=None,
        on_pcm_level=None,
        emit_mouth=True,
        output_stream_factory=None,
    ):
        """Play one buffer and report only writes accepted by the local sink."""

        if data is None or len(data) == 0:
            return ReceiptedPlaybackResult(
                False, False, 0, 0, 0.0, "empty_audio", True
            )
        if len(data.shape) > 1:
            data = data[:, 0]
        data = data.astype(np.float32, copy=False)
        max_value = float(np.max(np.abs(data)))
        if max_value > 0:
            data = (data / max_value) * 0.9
        data = _smooth_audio_edges(data, samplerate)
        frame_size = max(1, int(samplerate * 0.02))
        stream_factory = output_stream_factory or sd.OutputStream
        first_audio_started = False
        written_frames = 0
        played_samples = 0
        abort_reason = "none"
        previous = 0.0
        sink_closed_cleanly = False
        pcm_level_emitted = False
        try:
            with self.audio_lock:
                with stream_factory(
                    samplerate=samplerate,
                    channels=1,
                    dtype="float32",
                ) as stream:
                    for index in range(0, len(data), frame_size):
                        if self.stop_event.is_set():
                            abort_reason = "shutdown_cancelled"
                            break
                        source_frame = data[index:index + frame_size]
                        source_samples = len(source_frame)
                        if source_samples <= 0:
                            continue
                        if not first_audio_started and before_first_audio is not None:
                            try:
                                allowed = before_first_audio() is True
                            except Exception:
                                allowed = False
                            if not allowed:
                                abort_reason = "before_first_audio_rejected"
                                break
                        frame = source_frame
                        if len(frame) < frame_size:
                            frame = np.pad(frame, (0, frame_size - len(frame)))
                        stream.write(frame.reshape(-1, 1))
                        written_frames += 1
                        played_samples += source_samples
                        if (
                            emit_mouth
                            or on_sink_frame is not None
                            or on_pcm_level is not None
                        ):
                            volume = float(np.abs(frame).mean())
                            mouth = min(volume * 18, 1.0)
                            mouth = previous * 0.8 + mouth * 0.2
                            previous = mouth
                            speaking = mouth > 0.001
                            if on_sink_frame is not None:
                                try:
                                    on_sink_frame(
                                        open_value=mouth if speaking else 0.0,
                                        energy=min(max(volume, 0.0), 1.0) if speaking else 0.0,
                                        viseme="aa" if speaking else "sil",
                                        speaking=speaking,
                                    )
                                except Exception:
                                    # Visual observation is never audio-delivery evidence.
                                    pass
                        if not first_audio_started:
                            first_audio_started = True
                            if on_first_audio is not None:
                                try:
                                    accepted = on_first_audio(int(frame.nbytes)) is True
                                except Exception:
                                    accepted = False
                                if not accepted:
                                    abort_reason = "first_audio_receipt_rejected"
                                    break
                        if on_pcm_level is not None:
                            pcm_level_emitted = _emit_pcm_level(
                                on_pcm_level,
                                volume,
                            ) or pcm_level_emitted
                        if emit_mouth:
                            self._set_mouth(mouth, pcm_level=volume)
                sink_closed_cleanly = True
        except Exception as exc:
            log_event("voice", f"Receipted playback error: {exc}")
            abort_reason = "playback_error"
        finally:
            if emit_mouth:
                for _ in range(3):
                    self._set_mouth(self.mouth * 0.5)
                    time.sleep(0.01)
                self._set_mouth(0.0)
            if pcm_level_emitted:
                _emit_pcm_level(on_pcm_level, 0.0)

        completed = bool(
            abort_reason == "none"
            and first_audio_started
            and played_samples == len(data)
        )
        return ReceiptedPlaybackResult(
            completed=completed,
            first_audio_started=first_audio_started,
            written_frames=written_frames,
            played_samples=played_samples,
            played_duration_ms=round(
                (played_samples / float(samplerate)) * 1000.0,
                1,
            ),
            abort_reason="none" if completed else abort_reason or "playback_incomplete",
            stop_confirmed=sink_closed_cleanly,
        )

    def _read_audio(self, audio_path):
        path = Path(audio_path)
        if path.suffix.lower() == ".pcm":
            raw = np.fromfile(path, dtype=np.int16)
            return raw.astype(np.float32) / 32768.0, 44100
        try:
            return sf.read(audio_path, dtype="float32")
        except Exception:
            if path.suffix.lower() != ".mp3":
                raise
            return self._read_mp3_with_pydub(path)

    def _read_mp3_with_pydub(self, path):
        try:
            from pydub import AudioSegment
        except ImportError as exc:
            raise RuntimeError("Không đọc được MP3 bằng soundfile; cài pydub/ffmpeg hoặc đổi ELEVEN_OUTPUT_FORMAT") from exc

        audio = AudioSegment.from_file(path, format="mp3").set_channels(1)
        samples = np.array(audio.get_array_of_samples())
        max_int = float(1 << (8 * audio.sample_width - 1))
        return samples.astype(np.float32) / max_int, audio.frame_rate

    def stop(self):
        if self.lipsync_thread and self.lipsync_thread.is_alive():
            self.stop_event.set()
            self.lipsync_thread.join(timeout=2)
        try:
            sd.stop()
        except Exception:
            pass
        self._set_mouth(0.0)

    def cancel_receipted_playback(self):
        """Stop a synchronous receipted playback without relying on its thread."""

        self.stop_event.set()
        try:
            sd.stop()
        except Exception:
            pass
