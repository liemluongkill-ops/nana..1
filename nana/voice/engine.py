import asyncio
import base64
import hashlib
import io
import json
import math
import os
import queue
import random
import re as _re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from urllib.parse import urlencode
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import keyboard
import numpy as np
import requests
import speech_recognition as sr
import websockets

from nana.config import (
    DEBUG_NO_TTS,
    ELEVEN_API_KEY,
    ELEVEN_OUTPUT_FORMAT,
    MIC_DEVICE_INDEX,
    MIN_VOICE_SECONDS,
    PRIVATE_VOICE_OVERLAP_COALESCE_MS,
    PRIVATE_VOICE_OVERLAP_ENABLED,
    PRIVATE_VOICE_OVERLAP_MAX_CHARS,
    PRIVATE_VOICE_OVERLAP_MIN_CHARS,
    PRIVATE_VOICE_OVERLAP_PCM_ENABLED,
    PRIVATE_VOICE_OVERLAP_PCM_NETWORK_CHUNK_BYTES,
    PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT,
    PRIVATE_VOICE_OVERLAP_PCM_START_BUFFER_MS,
    PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S,
    PRIVATE_VOICE_OVERLAP_TAIL_TIMEOUT_S,
    PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS,
    PRIVATE_VOICE_TTD_CHUNK_TARGET_CHARS,
    PRIVATE_VOICE_TTD_CAPTURE_DIR,
    PRIVATE_VOICE_TTD_CAPTURE_ENABLED,
    PRIVATE_VOICE_TTD_ENABLED,
    PRIVATE_VOICE_TTD_INPUT_MODE,
    PRIVATE_VOICE_TTD_MIN_CHARS,
    PRIVATE_VOICE_TTD_MIN_WORDS,
    PRIVATE_VOICE_TTD_MODEL,
    PRIVATE_VOICE_TTD_OUTPUT_FORMAT,
    PRIVATE_VOICE_TTD_START_BUFFER_MS,
    PRIVATE_VOICE_TTD_TIMEOUT_S,
    PRESENCE_TTS_STREAMING_ENABLED,
    VOICE_ID,
    VOICE_CACHE_DIR,
    VOICE_CACHE_ENABLED,
    VOICE_CACHE_MAX_TEXT_CHARS,
    VOICE_CHUNKING_ENABLED,
    VOICE_CHUNK_MAX_CHARS,
    VOICE_HTTP_KEEPALIVE_ENABLED,
    VOICE_HTTP_POOL_MAXSIZE,
    VOICE_STREAMING_DRY_RUN_ENABLED,
    VOICE_STREAMING_DIRECT_ONLY,
    VOICE_STREAMING_ENABLED,
    VOICE_STREAMING_KILL_SWITCH,
    VOICE_STREAMING_PILOT_ENABLED,
    VOICE_TEST_MODE,
)
from nana.runtime.logger import log_event
from nana.runtime.avatar_mouth_stream import get_avatar_mouth_stream
from nana.runtime.private_voice_overlap import (
    OverlapCommitRequest,
    OverlapQueueItem,
    OverlapTailPayload,
)
from nana.runtime.private_voice_ttd import (
    TtdCommitRequest,
    TtdFinalPayload,
    TtdQueueItem,
)
from nana.voice.inline_audio_tags import (
    INLINE_AUDIO_TAG_MAX,
    INLINE_AUDIO_TAGS_ENABLED,
    render_inline_audio_tags,
)
from nana.voice.lipsync import LipsyncManager

def _bounded_env_int(name, default, low, high):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = int(default)
    return max(int(low), min(int(high), value))


_VOICE_HTTP_LOCAL = threading.local()


def _voice_http_post(url, **kwargs):
    """POST through a bounded per-thread keep-alive pool when enabled."""

    if not VOICE_HTTP_KEEPALIVE_ENABLED:
        response = requests.post(url, **kwargs)
        response._nana_session_request_index = 0
        response._nana_session_reused_hint = False
        return response

    session = getattr(_VOICE_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=min(4, int(VOICE_HTTP_POOL_MAXSIZE)),
            pool_maxsize=int(VOICE_HTTP_POOL_MAXSIZE),
            max_retries=0,
            pool_block=False,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _VOICE_HTTP_LOCAL.session = session
        _VOICE_HTTP_LOCAL.request_count = 0
    request_index = int(getattr(_VOICE_HTTP_LOCAL, "request_count", 0)) + 1
    _VOICE_HTTP_LOCAL.request_count = request_index
    response = session.post(url, **kwargs)
    response._nana_session_request_index = request_index
    response._nana_session_reused_hint = request_index > 1
    return response


# ElevenLabs may advertise a higher concurrency ceiling than it tolerates in
# bursty story playback. Default to a gentler value and allow local override.
ELEVENLABS_MAX_CONCURRENT = _bounded_env_int("NANA_ELEVENLABS_MAX_CONCURRENT", 3, 1, 5)
ELEVENLABS_RATE_LIMIT_RETRIES = 4  # 0.5s, 1s, 2s, 4s backoff
VOICE_PREBUFFER_ENABLED = os.getenv("NANA_VOICE_PREBUFFER_ENABLED", "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
}
VOICE_PREBUFFER_CHUNKS = _bounded_env_int("NANA_VOICE_PREBUFFER_CHUNKS", 2, 1, 4)
VOICE_PREBUFFER_MAX_MS = _bounded_env_int("NANA_VOICE_PREBUFFER_MAX_MS", 800, 0, 3000)
VOICE_STORY_CHUNK_MAX_CHARS = _bounded_env_int("NANA_VOICE_STORY_CHUNK_MAX_CHARS", 360, 160, 1200)
VOICE_FULL_BATCH_ENABLED = os.getenv("NANA_VOICE_FULL_BATCH_ENABLED", "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
}
VOICE_FULL_BATCH_SEAM_BAD_MS = _bounded_env_int("NANA_VOICE_FULL_BATCH_SEAM_BAD_MS", 700, 200, 3000)
VOICE_FULL_BATCH_PREBUFFER_EXTRA_MS = _bounded_env_int("NANA_VOICE_FULL_BATCH_PREBUFFER_EXTRA_MS", 2000, 0, 5000)
VOICE_FULL_SINGLE_REQUEST_ENABLED = os.getenv("NANA_VOICE_FULL_SINGLE_REQUEST_ENABLED", "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
}
# Official eleven_v3 limit is 5,000 characters per TTS request. Keep a
# conservative margin for provider-side text normalization.
# https://elevenlabs.io/docs/overview/models#character-limits
ELEVENLABS_SINGLE_REQUEST_MAX_CHARS = _bounded_env_int(
    "NANA_ELEVENLABS_SINGLE_REQUEST_MAX_CHARS", 4500, 1000, 5000
)
VOICE_FETCH_STALL_TIMEOUT_S = _bounded_env_int("NANA_VOICE_FETCH_STALL_TIMEOUT_S", 45, 5, 180)
VOICE_PLAYBACK_GRACE_S = _bounded_env_int("NANA_VOICE_PLAYBACK_GRACE_S", 5, 1, 30)
VOICE_PLAYBACK_FALLBACK_TIMEOUT_S = _bounded_env_int(
    "NANA_VOICE_PLAYBACK_FALLBACK_TIMEOUT_S", 300, 30, 900
)
VOICE_HTTP_STREAM_START_BUFFER_MS = _bounded_env_int(
    "NANA_VOICE_HTTP_STREAM_START_BUFFER_MS", 300, 100, 1000
)
VOICE_HTTP_STREAM_CONNECT_TIMEOUT_S = _bounded_env_int(
    "NANA_VOICE_HTTP_STREAM_CONNECT_TIMEOUT_S", 10, 3, 60
)
VOICE_HTTP_STREAM_STALL_TIMEOUT_S = _bounded_env_int(
    "NANA_VOICE_HTTP_STREAM_STALL_TIMEOUT_S", 45, 5, 180
)
VOICE_HTTP_STREAM_NETWORK_CHUNK_BYTES = _bounded_env_int(
    "NANA_VOICE_HTTP_STREAM_NETWORK_CHUNK_BYTES", 8192, 1024, 131072
)
PRESENCE_PCM_STREAM_NETWORK_CHUNK_BYTES = _bounded_env_int(
    # 2 KiB is about 64 ms of PCM16/16 kHz mono. Keep Presence provider reads
    # well below the node's 8 KiB (256 ms) prebuffer so network jitter cannot
    # consume the entire refill margin before requests yields another block.
    "NANA_PRESENCE_PCM_STREAM_NETWORK_CHUNK_BYTES", 2048, 1024, 131072
)
PRESENCE_PCM_STREAM_PREFETCH_CHUNKS = _bounded_env_int(
    # 32 x 2 KiB is about 2.0 seconds of PCM16/16 kHz mono. The producer
    # continuously drains the provider response into this bounded queue so
    # node credit pacing does not stop HTTP reads and expose provider jitter.
    "NANA_PRESENCE_PCM_STREAM_PREFETCH_CHUNKS", 32, 8, 128
)
PRESENCE_PCM_STREAM_PREFETCH_ENABLED = os.getenv(
    "NANA_PRESENCE_PCM_STREAM_PREFETCH_ENABLED",
    "0",
).strip().lower() not in {"0", "false", "off", "no"}
# Presence playback is bounded twice: the node keeps one stream in a bounded
# PSRAM buffer and the server rejects a stream above the same 4 MiB ceiling.
# Segmenting is the production safety mechanism. The whole-reply text limit is
# an explicit test/policy switch and stays transparent by default.
PRESENCE_VOICE_LIMIT_ENABLED = os.getenv(
    "NANA_PRESENCE_VOICE_LIMIT_ENABLED",
    "0",
).strip().lower() not in {"0", "false", "off", "no"}
PRESENCE_VOICE_MAX_CHARS = _bounded_env_int(
    "NANA_PRESENCE_VOICE_MAX_CHARS", 1500, 240, 2000
)
PRESENCE_TTS_SEGMENT_MAX_CHARS = _bounded_env_int(
    "NANA_PRESENCE_TTS_SEGMENT_MAX_CHARS", 600, 240, 900
)
VOICE_HTTP_STREAM_PCM_READ_BYTES = _bounded_env_int(
    "NANA_VOICE_HTTP_STREAM_PCM_READ_BYTES", 16384, 1024, 131072
)
VOICE_HTTP_STREAM_SAMPLE_RATE = 44100
VOICE_HTTP_STREAM_PCM_QUEUE_CHUNKS = _bounded_env_int(
    "NANA_VOICE_HTTP_STREAM_PCM_QUEUE_CHUNKS", 64, 8, 256
)
VOICE_HTTP_STREAM_FFMPEG_PATH = os.getenv("NANA_VOICE_HTTP_STREAM_FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg"
VOICE_STREAM_CALLBACK_OUTPUT_ENABLED = os.getenv(
    "NANA_VOICE_STREAM_CALLBACK_OUTPUT_ENABLED",
    "1",
).strip().lower() not in {"0", "false", "off", "no"}


def _pcm_sample_rate(output_format):
    match = _re.fullmatch(r"pcm_(\d+)", str(output_format or "").strip().lower())
    if match is None:
        return 0
    return int(match.group(1))


def _first_text_mismatch(left, right):
    left = str(left or "")
    right = str(right or "")
    for index, (left_char, right_char) in enumerate(zip(left, right)):
        if left_char != right_char:
            return index
    if len(left) != len(right):
        return min(len(left), len(right))
    return None


def _pcm_silence_summary(
    pcm_bytes,
    sample_rate,
    *,
    window_ms=20,
    silence_dbfs=-45.0,
    minimum_silence_ms=200,
):
    payload = bytes(pcm_bytes or b"")
    if not payload or len(payload) % 2 or int(sample_rate) <= 0:
        return {
            "window_ms": int(window_ms),
            "threshold_dbfs": float(silence_dbfs),
            "minimum_silence_ms": int(minimum_silence_ms),
            "span_count": 0,
            "total_silence_ms": 0.0,
            "longest_silence_ms": 0.0,
            "spans": [],
        }
    samples = np.frombuffer(payload, dtype="<i2").astype(np.float32) / 32768.0
    window_samples = max(1, int(int(sample_rate) * int(window_ms) / 1000.0))
    threshold = 10.0 ** (float(silence_dbfs) / 20.0)
    silent_windows = []
    for offset in range(0, len(samples), window_samples):
        window = samples[offset : offset + window_samples]
        if len(window) == 0:
            continue
        rms = float(np.sqrt(np.mean(np.square(window), dtype=np.float64)))
        silent_windows.append(rms <= threshold)

    minimum_windows = max(1, math.ceil(int(minimum_silence_ms) / int(window_ms)))
    spans = []
    start = None
    for index, silent in enumerate([*silent_windows, False]):
        if silent and start is None:
            start = index
        elif not silent and start is not None:
            if index - start >= minimum_windows:
                start_ms = start * int(window_ms)
                end_ms = min(
                    (index * int(window_ms)),
                    (len(samples) / float(sample_rate)) * 1000.0,
                )
                spans.append(
                    {
                        "start_ms": round(start_ms, 1),
                        "end_ms": round(end_ms, 1),
                        "duration_ms": round(end_ms - start_ms, 1),
                    }
                )
            start = None
    total_silence_ms = sum(span["duration_ms"] for span in spans)
    longest_silence_ms = max(
        (span["duration_ms"] for span in spans),
        default=0.0,
    )
    return {
        "window_ms": int(window_ms),
        "threshold_dbfs": float(silence_dbfs),
        "minimum_silence_ms": int(minimum_silence_ms),
        "span_count": len(spans),
        "total_silence_ms": round(total_silence_ms, 1),
        "longest_silence_ms": round(longest_silence_ms, 1),
        "spans": spans,
    }

# Dynamic ceiling for the whole reply: 8s baseline + 8s per chunk.
# Because the sequencer plays chunks sequentially, total wall time
# ≈ sum(playback_time_i) + final fetch delay. With TTS averaging ~3s
# per chunk and playback roughly matching, 8s/chunk is the safe headroom
# that won't truncate long stories while still failing fast on hangs.
TTS_DYNAMIC_TIMEOUT_BASE_S = 8.0
TTS_DYNAMIC_TIMEOUT_PER_CHUNK_S = 8.0

# When a chunk fails permanently, Nana plays one of these to soften the
# semantic break before going silent. Picked at random per failure.
TTS_FALLBACK_PHRASES = [
    "À, con quên mất nãy con định nói gì ấy nhỉ... thôi để con kể lại sau vậy, Ba thông cảm nha!",
    "Úi, con bị trục trặc một chút xíu. Ba đợi con giây lát nha, con sẽ kể tiếp!",
    "Ôi, con bị ngơ một lát. Để con hồi phục lại rồi mình nói chuyện tiếp nha Ba!",
]


class MicrophoneOpenError(RuntimeError):
    pass


class PresenceTTSStreamingUnavailable(RuntimeError):
    pass


def _microphone_target(device_index):
    return "system default" if device_index is None else f"index {device_index}"


@contextmanager
def _open_microphone_source(device_index):
    """Open SpeechRecognition's microphone without losing PyAudio errors."""

    target = _microphone_target(device_index)
    try:
        source = sr.Microphone(device_index=device_index)
    except Exception as exc:
        raise MicrophoneOpenError(
            f"Cannot prepare microphone {target}: {type(exc).__name__}: {exc}"
        ) from exc

    audio = source.pyaudio_module.PyAudio()
    source.audio = audio
    try:
        try:
            device_info = (
                audio.get_default_input_device_info()
                if device_index is None
                else audio.get_device_info_by_index(device_index)
            )
        except Exception as exc:
            raise MicrophoneOpenError(
                f"Cannot inspect microphone {target}: {type(exc).__name__}: {exc}"
            ) from exc

        if int(device_info.get("maxInputChannels") or 0) < 1:
            name = str(device_info.get("name") or "unknown")
            raise MicrophoneOpenError(
                f"Microphone {target} is not an input device: {name}"
            )

        try:
            raw_stream = audio.open(
                input_device_index=device_index,
                channels=1,
                format=source.format,
                rate=source.SAMPLE_RATE,
                frames_per_buffer=source.CHUNK,
                input=True,
            )
        except Exception as exc:
            raise MicrophoneOpenError(
                f"Cannot open microphone {target}: {type(exc).__name__}: {exc}"
            ) from exc

        source.stream = sr.Microphone.MicrophoneStream(raw_stream)
        yield source, dict(device_info)
    finally:
        try:
            if source.stream is not None:
                try:
                    source.stream.close()
                except Exception:
                    pass
        finally:
            source.stream = None
            source.audio = None
            try:
                audio.terminate()
            except Exception:
                pass


@dataclass(frozen=True)
class VoiceQueueItem:
    text: str
    voice_mode: str = "chat"
    ticket: int = 0


@dataclass(frozen=True)
class AudioCompletionResult:
    state: str
    audio_completed: bool
    requested_segments: int
    fetched_segments: int
    played_segments: int
    unplayed_segments: int
    original_chars: int
    remaining_chars: int
    played_duration_ms: float
    abort_reason: str = "none"
    stop_confirmed: bool = True


@dataclass(frozen=True)
class PCMTranscriptionResult:
    text: str
    confidence: float | None
    alternatives: int
    backend: str = "google"

# Voice tone profiles — map theo audio tag trong text
VOICE_TONE_PROFILES = {
    "default":    {"stability": 0.75, "speed": 1.05},
    "happy":      {"stability": 0.45, "speed": 1.12},
    "sad":        {"stability": 0.85, "speed": 0.95},
    "caring":     {"stability": 0.55, "speed": 1.00},
    "nervous":    {"stability": 0.40, "speed": 1.15},
    "excited":    {"stability": 0.35, "speed": 1.18},
    "thoughtful": {"stability": 0.80, "speed": 0.98},
    "surprised":  {"stability": 0.50, "speed": 1.12},
    "annoyed":    {"stability": 0.55, "speed": 1.05},
}

_AUDIO_TAG_TONE_MAP = {
    "happy": "happy",
    "mischievously": "happy",
    "sarcastic": "happy",
    "cute": "happy",
    "impressed": "happy",
    "delighted": "happy",
    "amazed": "happy",
    "sighs": "sad",
    "sad": "sad",
    "crying": "sad",
    "whisper": "caring",
    "warmly": "caring",
    "sympathetic": "caring",
    "reassuring": "caring",
    "relaxed": "caring",
    "muttering": "caring",
    "nervous": "nervous",
    "alarmed": "nervous",
    "sheepishly": "nervous",
    "desperately": "nervous",
    "cautiously": "nervous",
    "panicking": "nervous",
    "excited": "excited",
    "energetic": "excited",
    "dramatically": "excited",
    "thoughtful": "thoughtful",
    "curious": "thoughtful",
    "professional": "thoughtful",
    "questioning": "thoughtful",
    "deadpan": "thoughtful",
    "casual": "thoughtful",
    "confidently": "thoughtful",
    "surprised": "surprised",
    "confused": "surprised",
    "annoyed": "annoyed",
    "frustrated": "annoyed",
    "dismissive": "annoyed",
    "angry": "annoyed",
    "appalled": "annoyed",
}


def _detect_tone_from_text(text):
    """Detect dominant tone profile from audio tag in text."""
    if not text:
        return "default"
    rendered = render_inline_audio_tags(text)
    for tag in rendered.tags:
        tone = _AUDIO_TAG_TONE_MAP.get(tag)
        if tone:
            return tone
    return "default"


def _strip_audio_tags(text):
    """Remove recognized audio directions for display/plain-text uses."""
    return render_inline_audio_tags(text).display_text


def _prepare_tts_text(text):
    """Normalize Nana's inline directions for the active Eleven v3 adapter."""
    return render_inline_audio_tags(text).tts_text


class VoiceEngine:
    def __init__(self, *, avatar_mouth_enabled=True, start_worker=True):
        self.recognizer = sr.Recognizer()
        self._recognizer_lock = threading.Lock()
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = True
        self.noise_calibrated = False
        self.lipsync = LipsyncManager()
        self.avatar_mouth_stream = (
            get_avatar_mouth_stream() if avatar_mouth_enabled else None
        )
        if self.avatar_mouth_stream is not None:
            self.lipsync.set_pcm_level_callback(
                self.avatar_mouth_stream.publish_pcm_level
            )
        self.voice_queue = queue.Queue(maxsize=5)
        self._voice_completion = threading.Condition()
        self._voice_enqueued_ticket = 0
        self._voice_completed_ticket = 0
        # Global ceiling on ElevenLabs calls in flight — guards against
        # concurrent_limit_exceeded (subscription caps at 5).
        self._tts_semaphore = threading.BoundedSemaphore(ELEVENLABS_MAX_CONCURRENT)
        self._shutdown_event = threading.Event()
        self._shutdown_lock = threading.Lock()
        self._public_playback_lock = threading.Lock()
        self._public_playback_done = threading.Event()
        self._public_playback_done.set()
        self._stream_handle_lock = threading.Lock()
        self._active_stream_response = None
        self._active_stream_process = None
        self._presence_output_lock = threading.Lock()
        self._presence_output_available_fn = None
        self._presence_pcm_output_fn = None
        self._presence_pcm_stream_output_fn = None
        self._presence_pcm_stream_available_fn = None
        self.state_lock = threading.Lock()
        self.state = {
            "status": "idle",
            "listening": False,
            "speaking": False,
            "microphone_mode": "system_default" if MIC_DEVICE_INDEX is None else "explicit",
            "microphone_configured_index": MIC_DEVICE_INDEX,
            "microphone_active_index": None,
            "microphone_active_name": None,
            "microphone_open_status": "not_tested",
            "idle_lipsync_stop_applied_total": 0,
            "idle_lipsync_stop_skipped_total": 0,
            "last_listen_start": 0.0,
            "last_listen_end": 0.0,
            "last_speak_start": 0.0,
            "last_speak_end": 0.0,
            "last_error": None,
            "queued_total": 0,
            "dropped_total": 0,
            "drop_old_when_full": True,
            "cache_hits": 0,
            "cache_misses": 0,
            "chunked_total": 0,
            "last_tts_strategy": "none",
            "last_tts_text_len": 0,
            "last_tts_chunks": 0,
            "last_tts_audio_paths": 0,
            "last_tts_cache_hits": 0,
            "last_tts_cache_misses": 0,
            "last_tts_prepare_ms": None,
            "last_tts_request_ms": None,
            "last_tts_playback_ms": None,
            "last_tts_total_ms": None,
            "last_tts_prebuffer_chunks": 0,
            "last_tts_prebuffer_ms": None,
            "last_tts_seam_wait_ms": None,
            "last_tts_max_seam_wait_ms": None,
            "last_tts_avg_seam_wait_ms": None,
            "last_tts_seam_grade": "none",
            "last_tts_playback_policy": "none",
            "last_tts_seam_cause": "none",
            "last_tts_voice_mode": "chat",
            "last_tts_chunk_max_chars": VOICE_CHUNK_MAX_CHARS,
            "last_inline_tags_enabled": INLINE_AUDIO_TAGS_ENABLED,
            "last_inline_tag_count": 0,
            "last_inline_tags": (),
            "last_inline_tag_aliases": 0,
            "last_inline_tag_blocked": 0,
            "last_inline_tag_overflow": 0,
            "last_inline_tag_unknown": 0,
            "last_inline_tag_input_chars": 0,
            "last_inline_tag_tts_chars": 0,
            "last_audio_state": "none",
            "last_audio_completed": False,
            "last_audio_requested_segments": 0,
            "last_audio_fetched_segments": 0,
            "last_audio_played_segments": 0,
            "last_audio_unplayed_segments": 0,
            "last_audio_original_chars": 0,
            "last_audio_remaining_chars": 0,
            "last_audio_played_duration_ms": 0.0,
            "last_audio_abort_reason": "none",
            "last_audio_playback_watchdog": "none",
            "last_audio_playback_timeout_ms": 0.0,
            "private_voice_overlap_eligible_total": 0,
            "private_voice_overlap_committed_total": 0,
            "private_voice_overlap_bypassed_total": 0,
            "private_voice_overlap_completed_total": 0,
            "private_voice_overlap_failed_total": 0,
            "private_voice_overlap_pcm_completed_total": 0,
            "private_voice_overlap_pcm_fallback_total": 0,
            "private_voice_overlap_pcm_failed_total": 0,
            "last_private_voice_overlap_status": "none",
            "last_private_voice_overlap_reason": "none",
            "last_private_voice_overlap_turn_id": "none",
            "last_private_voice_overlap_full_chars": 0,
            "last_private_voice_overlap_lead_chars": 0,
            "last_private_voice_overlap_tail_chars": 0,
            "last_private_voice_overlap_split_offset": 0,
            "last_private_voice_overlap_missing_chars": 0,
            "last_private_voice_overlap_duplicate_chars": 0,
            "last_private_voice_overlap_lead_play_count": 0,
            "last_private_voice_overlap_tail_play_count": 0,
            "last_private_voice_overlap_tail_segment_count": 0,
            "last_private_voice_overlap_tail_retry_count": 0,
            "last_private_voice_overlap_lead_committed_ms": None,
            "last_private_voice_overlap_llm_completed_ms": None,
            "last_private_voice_overlap_lead_fetch_started_ms": None,
            "last_private_voice_overlap_lead_fetch_completed_ms": None,
            "last_private_voice_overlap_tail_fetch_started_ms": None,
            "last_private_voice_overlap_tail_fetch_completed_ms": None,
            "last_private_voice_overlap_first_audio_ms": None,
            "last_private_voice_overlap_lead_playback_ended_ms": None,
            "last_private_voice_overlap_seam_wait_ms": None,
            "last_private_voice_overlap_true_overlap": False,
            "last_private_voice_overlap_pcm_status": "none",
            "last_private_voice_overlap_pcm_reason": "none",
            "last_private_voice_overlap_pcm_output_format": (
                PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT
            ),
            "last_private_voice_overlap_pcm_lead_chunks": 0,
            "last_private_voice_overlap_pcm_tail_chunks": 0,
            "last_private_voice_overlap_pcm_lead_bytes": 0,
            "last_private_voice_overlap_pcm_tail_bytes": 0,
            "last_private_voice_overlap_pcm_lead_first_byte_ms": None,
            "last_private_voice_overlap_pcm_tail_first_byte_ms": None,
            "last_private_voice_overlap_pcm_lead_eof_ms": None,
            "last_private_voice_overlap_pcm_tail_eof_ms": None,
            "last_private_voice_overlap_pcm_tail_buffered_before_lead_eof": False,
            "last_private_voice_overlap_pcm_fallback_used": False,
            "last_private_voice_overlap_pcm_error": None,
            "private_voice_ttd_eligible_total": 0,
            "private_voice_ttd_committed_total": 0,
            "private_voice_ttd_bypassed_total": 0,
            "private_voice_ttd_completed_total": 0,
            "private_voice_ttd_failed_total": 0,
            "private_voice_ttd_fallback_total": 0,
            "last_private_voice_ttd_status": "none",
            "last_private_voice_ttd_reason": "none",
            "last_private_voice_ttd_turn_id": "none",
            "last_private_voice_ttd_model": PRIVATE_VOICE_TTD_MODEL,
            "last_private_voice_ttd_input_mode": PRIVATE_VOICE_TTD_INPUT_MODE,
            "last_private_voice_ttd_output_format": PRIVATE_VOICE_TTD_OUTPUT_FORMAT,
            "last_private_voice_ttd_source_chars": 0,
            "last_private_voice_ttd_prepared_chars": 0,
            "last_private_voice_ttd_provider_expected_chars": 0,
            "last_private_voice_ttd_provider_sent_chars": 0,
            "last_private_voice_ttd_provider_integrity_ok": False,
            "last_private_voice_ttd_provider_mismatch_index": None,
            "last_private_voice_ttd_provider_missing_chars": 0,
            "last_private_voice_ttd_provider_duplicate_chars": 0,
            "last_private_voice_ttd_sent_chunks": 0,
            "last_private_voice_ttd_audio_chunks": 0,
            "last_private_voice_ttd_received_bytes": 0,
            "last_private_voice_ttd_missing_chars": 0,
            "last_private_voice_ttd_duplicate_chars": 0,
            "last_private_voice_ttd_replayed_chars": 0,
            "last_private_voice_ttd_committed_ms": None,
            "last_private_voice_ttd_connected_ms": None,
            "last_private_voice_ttd_first_text_sent_ms": None,
            "last_private_voice_ttd_llm_completed_ms": None,
            "last_private_voice_ttd_first_chunk_ms": None,
            "last_private_voice_ttd_first_pcm_ms": None,
            "last_private_voice_ttd_first_audio_ms": None,
            "last_private_voice_ttd_final_ms": None,
            "last_private_voice_ttd_max_network_gap_ms": 0.0,
            "last_private_voice_ttd_speed_omitted": True,
            "last_private_voice_ttd_error": None,
            "last_private_voice_ttd_capture_path": None,
            "last_private_voice_ttd_capture_report_path": None,
            "last_private_voice_ttd_capture_bytes": 0,
            "last_private_voice_ttd_silence_spans": 0,
            "last_private_voice_ttd_silence_total_ms": 0.0,
            "last_private_voice_ttd_longest_silence_ms": 0.0,
            "presence_pcm_output_configured": False,
            "presence_pcm_output_available": False,
            "last_presence_pcm_status": "none",
            "last_presence_pcm_stream_id": None,
            "last_presence_pcm_bytes": 0,
            "last_presence_pcm_provider_bytes": 0,
            "last_presence_pcm_text_chars": 0,
            "last_presence_pcm_prepared_chars": 0,
            "last_presence_pcm_input_chars": 0,
            "last_presence_pcm_text_truncated": False,
            "last_presence_pcm_omitted_chars": 0,
            "last_presence_pcm_provider_duration_ms": 0.0,
            "last_presence_pcm_ms_per_prepared_char": None,
            "last_presence_pcm_segment_bytes": (),
            "last_presence_pcm_underruns": None,
            "last_presence_pcm_elapsed_ms": None,
            "last_presence_pcm_streaming": False,
            "last_presence_pcm_first_audio_ms": None,
            "last_presence_pcm_first_byte_ms": None,
            "last_presence_pcm_network_chunks": 0,
            "last_presence_pcm_network_bytes": 0,
            "last_presence_pcm_max_network_gap_ms": 0.0,
            "last_presence_pcm_prefetch_high_water": 0,
            "last_presence_pcm_prefetch_starvations": 0,
            "last_presence_pcm_max_prefetch_wait_ms": 0.0,
            "last_presence_pcm_source_mode": "pull_safe",
            "last_presence_pcm_playback_ratio": None,
            "last_presence_pcm_playback_grade": "none",
            "last_presence_pcm_error": None,
            "streaming_dry_run_packets": 0,
            "last_streaming_action": "none",
            "last_streaming_reason": "none",
            "last_stream_transport_state": "idle",
            "last_stream_playback_state": "idle",
            "last_stream_time_to_first_byte_ms": None,
            "last_stream_time_to_first_pcm_ms": None,
            "last_stream_time_to_first_audio_ms": None,
            "last_stream_received_bytes": 0,
            "last_stream_start_buffer_ms": 0.0,
            "last_stream_refill_count": 0,
            "last_stream_rebuffer_count": 0,
            "last_stream_rebuffer_total_ms": 0.0,
            "last_stream_output_write_calls": 0,
            "last_stream_output_underflow_count": 0,
            "last_stream_playback_buffer_low_watermark_ms": None,
            "last_stream_max_feed_gap_ms": 0.0,
            "last_stream_last_underflow_buffer_ms": None,
            "last_stream_last_underflow_queue_depth": None,
            "last_stream_last_underflow_decoder_done": None,
            "last_stream_callback_calls": 0,
            "last_stream_callback_status_underflows": 0,
            "last_stream_ring_starvation_count": 0,
            "last_stream_ring_low_watermark_ms": None,
            "last_stream_max_callback_lateness_ms": 0.0,
            "last_stream_feeder_refill_count": 0,
            "last_stream_feeder_done": False,
            "last_stream_callback_finished": False,
            "last_stream_max_network_gap_ms": 0.0,
            "last_stream_provider_eof": False,
            "last_stream_decoder_eof": False,
            "last_stream_abort_reason": "none",
            "last_stream_ffmpeg_ready": False,
            "last_voice_http_session_request_index": 0,
            "last_voice_http_session_reused_hint": False,
        }
        self.worker = None
        if start_worker:
            self.worker = threading.Thread(target=self._voice_worker, daemon=True)
            self.worker.start()

    def _update_state(self, **updates):
        with self.state_lock:
            self.state.update(updates)
            return dict(self.state)

    def _increment_state(self, key, amount=1):
        with self.state_lock:
            self.state[key] = int(self.state.get(key) or 0) + amount
            return self.state[key]

    def stop_lipsync_if_idle(self):
        """Let external owners settle lipsync without cancelling active voice."""

        with self.state_lock:
            if self.state.get("speaking") or self.state.get("listening"):
                self.state["idle_lipsync_stop_skipped_total"] = (
                    int(self.state.get("idle_lipsync_stop_skipped_total") or 0) + 1
                )
                return False
            self.lipsync.stop()
            self.state["idle_lipsync_stop_applied_total"] = (
                int(self.state.get("idle_lipsync_stop_applied_total") or 0) + 1
            )
            return True

    def snapshot(self):
        with self.state_lock:
            state = dict(self.state)
        state.update(
            {
                "queue_size": self.voice_queue.qsize(),
                "queue_maxsize": self.voice_queue.maxsize,
                "worker_alive": bool(self.worker is not None and self.worker.is_alive()),
                "voice_enqueued_ticket": self.latest_voice_ticket(),
                "voice_completed_ticket": self.completed_voice_ticket(),
                "voice_cache_enabled": VOICE_CACHE_ENABLED,
                "voice_cache_dir": str(VOICE_CACHE_DIR),
                "voice_chunking_enabled": VOICE_CHUNKING_ENABLED,
                "voice_chunk_max_chars": VOICE_CHUNK_MAX_CHARS,
                "voice_streaming_dry_run_enabled": VOICE_STREAMING_DRY_RUN_ENABLED,
                "voice_streaming_enabled": VOICE_STREAMING_ENABLED,
                "voice_streaming_pilot_enabled": VOICE_STREAMING_PILOT_ENABLED,
                "voice_streaming_kill_switch": VOICE_STREAMING_KILL_SWITCH,
                "voice_streaming_direct_only": VOICE_STREAMING_DIRECT_ONLY,
                "voice_stream_callback_output_enabled": VOICE_STREAM_CALLBACK_OUTPUT_ENABLED,
                "voice_http_stream_start_buffer_ms": VOICE_HTTP_STREAM_START_BUFFER_MS,
                "voice_http_stream_connect_timeout_s": VOICE_HTTP_STREAM_CONNECT_TIMEOUT_S,
                "voice_http_stream_stall_timeout_s": VOICE_HTTP_STREAM_STALL_TIMEOUT_S,
                "voice_http_stream_network_chunk_bytes": VOICE_HTTP_STREAM_NETWORK_CHUNK_BYTES,
                "voice_http_keepalive_enabled": VOICE_HTTP_KEEPALIVE_ENABLED,
                "voice_http_pool_maxsize": VOICE_HTTP_POOL_MAXSIZE,
                "presence_pcm_stream_network_chunk_bytes": PRESENCE_PCM_STREAM_NETWORK_CHUNK_BYTES,
                "presence_pcm_stream_prefetch_chunks": PRESENCE_PCM_STREAM_PREFETCH_CHUNKS,
                "presence_pcm_stream_prefetch_enabled": PRESENCE_PCM_STREAM_PREFETCH_ENABLED,
                "presence_voice_limit_enabled": PRESENCE_VOICE_LIMIT_ENABLED,
                "presence_voice_max_chars": PRESENCE_VOICE_MAX_CHARS,
                "presence_tts_segment_max_chars": PRESENCE_TTS_SEGMENT_MAX_CHARS,
                "voice_http_stream_sample_rate": VOICE_HTTP_STREAM_SAMPLE_RATE,
                "voice_prebuffer_enabled": VOICE_PREBUFFER_ENABLED,
                "voice_prebuffer_chunks": VOICE_PREBUFFER_CHUNKS,
                "voice_prebuffer_max_ms": VOICE_PREBUFFER_MAX_MS,
                "voice_story_chunk_max_chars": VOICE_STORY_CHUNK_MAX_CHARS,
                "voice_full_batch_enabled": VOICE_FULL_BATCH_ENABLED,
                "voice_full_batch_seam_bad_ms": VOICE_FULL_BATCH_SEAM_BAD_MS,
                "voice_full_batch_extra_ms": VOICE_FULL_BATCH_PREBUFFER_EXTRA_MS,
                "voice_full_single_request_enabled": VOICE_FULL_SINGLE_REQUEST_ENABLED,
                "elevenlabs_single_request_max_chars": ELEVENLABS_SINGLE_REQUEST_MAX_CHARS,
                "voice_fetch_stall_timeout_s": VOICE_FETCH_STALL_TIMEOUT_S,
                "voice_playback_grace_s": VOICE_PLAYBACK_GRACE_S,
                "voice_playback_fallback_timeout_s": VOICE_PLAYBACK_FALLBACK_TIMEOUT_S,
                "voice_inline_audio_tags_enabled": INLINE_AUDIO_TAGS_ENABLED,
                "voice_inline_audio_tag_max": INLINE_AUDIO_TAG_MAX,
                "private_voice_overlap_enabled": PRIVATE_VOICE_OVERLAP_ENABLED,
                "private_voice_overlap_min_chars": PRIVATE_VOICE_OVERLAP_MIN_CHARS,
                "private_voice_overlap_max_chars": PRIVATE_VOICE_OVERLAP_MAX_CHARS,
                "private_voice_overlap_coalesce_ms": PRIVATE_VOICE_OVERLAP_COALESCE_MS,
                "private_voice_overlap_tail_timeout_s": PRIVATE_VOICE_OVERLAP_TAIL_TIMEOUT_S,
                "private_voice_overlap_pcm_enabled": PRIVATE_VOICE_OVERLAP_PCM_ENABLED,
                "private_voice_overlap_pcm_output_format": (
                    PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT
                ),
                "private_voice_overlap_pcm_sample_rate": _pcm_sample_rate(
                    PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT
                ),
                "private_voice_overlap_pcm_start_buffer_ms": (
                    PRIVATE_VOICE_OVERLAP_PCM_START_BUFFER_MS
                ),
                "private_voice_overlap_pcm_network_chunk_bytes": (
                    PRIVATE_VOICE_OVERLAP_PCM_NETWORK_CHUNK_BYTES
                ),
                "private_voice_overlap_pcm_timeout_s": (
                    PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S
                ),
                "private_voice_ttd_enabled": PRIVATE_VOICE_TTD_ENABLED,
                "private_voice_ttd_model": PRIVATE_VOICE_TTD_MODEL,
                "private_voice_ttd_input_mode": PRIVATE_VOICE_TTD_INPUT_MODE,
                "private_voice_ttd_output_format": PRIVATE_VOICE_TTD_OUTPUT_FORMAT,
                "private_voice_ttd_sample_rate": _pcm_sample_rate(
                    PRIVATE_VOICE_TTD_OUTPUT_FORMAT
                ),
                "private_voice_ttd_min_chars": PRIVATE_VOICE_TTD_MIN_CHARS,
                "private_voice_ttd_min_words": PRIVATE_VOICE_TTD_MIN_WORDS,
                "private_voice_ttd_chunk_max_chars": PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS,
                "private_voice_ttd_chunk_target_chars": PRIVATE_VOICE_TTD_CHUNK_TARGET_CHARS,
                "private_voice_ttd_start_buffer_ms": PRIVATE_VOICE_TTD_START_BUFFER_MS,
                "private_voice_ttd_timeout_s": PRIVATE_VOICE_TTD_TIMEOUT_S,
                "private_voice_ttd_capture_enabled": PRIVATE_VOICE_TTD_CAPTURE_ENABLED,
                "private_voice_ttd_capture_dir": str(PRIVATE_VOICE_TTD_CAPTURE_DIR),
            }
        )
        return state

    def _prepare_tts_request_text(self, text):
        rendered = render_inline_audio_tags(text)
        self._update_state(
            last_inline_tags_enabled=rendered.enabled,
            last_inline_tag_count=rendered.tag_count,
            last_inline_tags=rendered.tags,
            last_inline_tag_aliases=rendered.alias_normalized_count,
            last_inline_tag_blocked=rendered.blocked_count,
            last_inline_tag_overflow=rendered.overflow_count,
            last_inline_tag_unknown=rendered.unknown_count,
            last_inline_tag_input_chars=len(rendered.original_text),
            last_inline_tag_tts_chars=len(rendered.tts_text),
        )
        return rendered.tts_text

    def _prepare_ttd_request_text(self, text):
        """Render a TTD frame without deleting its source boundary spacing."""

        source = str(text or "")
        rendered = render_inline_audio_tags(source)
        self._update_state(
            last_inline_tags_enabled=rendered.enabled,
            last_inline_tag_count=rendered.tag_count,
            last_inline_tags=rendered.tags,
            last_inline_tag_aliases=rendered.alias_normalized_count,
            last_inline_tag_blocked=rendered.blocked_count,
            last_inline_tag_overflow=rendered.overflow_count,
            last_inline_tag_unknown=rendered.unknown_count,
            last_inline_tag_input_chars=len(rendered.original_text),
            last_inline_tag_tts_chars=len(rendered.tts_text),
        )
        prepared = str(rendered.tts_text or "")
        if prepared and source[:1].isspace() and not prepared[:1].isspace():
            prepared = " " + prepared
        if prepared and source[-1:].isspace() and not prepared[-1:].isspace():
            prepared += " "
        return prepared

    def say(self, text, *, voice_mode=None):
        if not text or not text.strip():
            return 0
        ticket = self._next_voice_ticket()
        item = VoiceQueueItem(
            str(text),
            self._normalize_voice_mode(voice_mode),
            ticket,
        )
        # Blocking put with a long timeout — acts as backpressure so the
        # queue keeps acting as a steady-state pre-roll buffer (≈5 chunks)
        # instead of dropping chunks whenever LLM outpaces the TTS worker.
        # The worker pops a chunk every ~5-8s, so 30s is well above any
        # realistic stall. Only fall back to drop if the queue is wedged.
        deadline = time.time() + 30.0
        while True:
            try:
                self.voice_queue.put(item, timeout=max(0.05, deadline - time.time()))
                self._increment_state("queued_total")
                return ticket
            except queue.Full:
                if time.time() >= deadline:
                    self._increment_state("dropped_total")
                    log_event("voice", f"Voice queue wedged >30s, dropping chunk: {text[:80]}")
                    print(f"🔇 Voice queue đầy quá lâu, bỏ chunk mới: {text[:60]}")
                    self._mark_voice_ticket_completed(ticket)
                    return ticket
                # worker still hasn't freed a slot — keep waiting

    def private_overlap_readiness(self):
        if not PRIVATE_VOICE_OVERLAP_ENABLED:
            return False, "disabled"
        if DEBUG_NO_TTS or VOICE_TEST_MODE:
            return False, "voice_disabled"
        if self._shutdown_requested():
            return False, "shutdown"
        if self.voice_queue.qsize() != 0:
            return False, "queue_not_empty"
        if self.latest_voice_ticket() != self.completed_voice_ticket():
            return False, "voice_ticket_in_flight"
        with self.state_lock:
            if self.state.get("speaking"):
                return False, "already_speaking"
            if self.state.get("listening"):
                return False, "currently_listening"
        if self._presence_pcm_outputs_if_available() is not None:
            return False, "presence_available"
        return True, "ready"

    def record_private_overlap_bypass(self, reason):
        self._increment_state("private_voice_overlap_bypassed_total")
        self._update_state(
            last_private_voice_overlap_status="bypassed",
            last_private_voice_overlap_reason=str(reason or "unknown"),
            last_private_voice_overlap_full_chars=0,
            last_private_voice_overlap_lead_chars=0,
            last_private_voice_overlap_tail_chars=0,
            last_private_voice_overlap_split_offset=0,
            last_private_voice_overlap_missing_chars=0,
            last_private_voice_overlap_duplicate_chars=0,
            last_private_voice_overlap_lead_play_count=0,
            last_private_voice_overlap_tail_play_count=0,
            last_private_voice_overlap_tail_segment_count=0,
            last_private_voice_overlap_tail_retry_count=0,
            last_private_voice_overlap_lead_committed_ms=None,
            last_private_voice_overlap_llm_completed_ms=None,
            last_private_voice_overlap_lead_fetch_started_ms=None,
            last_private_voice_overlap_lead_fetch_completed_ms=None,
            last_private_voice_overlap_tail_fetch_started_ms=None,
            last_private_voice_overlap_tail_fetch_completed_ms=None,
            last_private_voice_overlap_first_audio_ms=None,
            last_private_voice_overlap_lead_playback_ended_ms=None,
            last_private_voice_overlap_seam_wait_ms=None,
            last_private_voice_overlap_true_overlap=False,
        )

    def private_ttd_readiness(self):
        if not PRIVATE_VOICE_TTD_ENABLED:
            return False, "disabled"
        if DEBUG_NO_TTS or VOICE_TEST_MODE:
            return False, "voice_disabled"
        if not ELEVEN_API_KEY.strip() or ELEVEN_API_KEY == "ELEVENLABS_KEY_CUA_BAN":
            return False, "eleven_credentials_missing"
        if not str(VOICE_ID or "").strip():
            return False, "voice_id_missing"
        if not VOICE_STREAMING_ENABLED or VOICE_STREAMING_KILL_SWITCH:
            return False, "streaming_unavailable"
        if not VOICE_STREAM_CALLBACK_OUTPUT_ENABLED:
            return False, "callback_output_disabled"
        if PRIVATE_VOICE_TTD_MODEL not in {
            "eleven_v3",
            "eleven_v3_conversational",
        }:
            return False, "unsupported_model"
        if PRIVATE_VOICE_TTD_INPUT_MODE not in {"incremental", "eof_single"}:
            return False, "unsupported_input_mode"
        if _pcm_sample_rate(PRIVATE_VOICE_TTD_OUTPUT_FORMAT) <= 0:
            return False, "unsupported_output_format"
        if self._shutdown_requested():
            return False, "shutdown"
        if self.voice_queue.qsize() != 0:
            return False, "queue_not_empty"
        if self.latest_voice_ticket() != self.completed_voice_ticket():
            return False, "voice_ticket_in_flight"
        with self.state_lock:
            if self.state.get("speaking"):
                return False, "already_speaking"
            if self.state.get("listening"):
                return False, "currently_listening"
        if self._presence_pcm_outputs_if_available() is not None:
            return False, "presence_available"
        return True, "ready"

    def record_private_ttd_bypass(self, reason):
        self._increment_state("private_voice_ttd_bypassed_total")
        self._update_state(
            last_private_voice_ttd_status="bypassed",
            last_private_voice_ttd_reason=str(reason or "unknown"),
            last_private_voice_ttd_turn_id="none",
            last_private_voice_ttd_source_chars=0,
            last_private_voice_ttd_prepared_chars=0,
            last_private_voice_ttd_provider_expected_chars=0,
            last_private_voice_ttd_provider_sent_chars=0,
            last_private_voice_ttd_provider_integrity_ok=False,
            last_private_voice_ttd_provider_mismatch_index=None,
            last_private_voice_ttd_provider_missing_chars=0,
            last_private_voice_ttd_provider_duplicate_chars=0,
            last_private_voice_ttd_sent_chunks=0,
            last_private_voice_ttd_audio_chunks=0,
            last_private_voice_ttd_received_bytes=0,
            last_private_voice_ttd_missing_chars=0,
            last_private_voice_ttd_duplicate_chars=0,
            last_private_voice_ttd_replayed_chars=0,
            last_private_voice_ttd_committed_ms=None,
            last_private_voice_ttd_connected_ms=None,
            last_private_voice_ttd_first_text_sent_ms=None,
            last_private_voice_ttd_llm_completed_ms=None,
            last_private_voice_ttd_first_chunk_ms=None,
            last_private_voice_ttd_first_pcm_ms=None,
            last_private_voice_ttd_first_audio_ms=None,
            last_private_voice_ttd_final_ms=None,
            last_private_voice_ttd_max_network_gap_ms=0.0,
            last_private_voice_ttd_error=None,
            last_private_voice_ttd_capture_path=None,
            last_private_voice_ttd_capture_report_path=None,
            last_private_voice_ttd_capture_bytes=0,
            last_private_voice_ttd_silence_spans=0,
            last_private_voice_ttd_silence_total_ms=0.0,
            last_private_voice_ttd_longest_silence_ms=0.0,
        )

    def say_ttd(self, request, *, voice_mode="full"):
        if not isinstance(request, TtdCommitRequest):
            raise TypeError("request must be TtdCommitRequest")
        ready, reason = self.private_ttd_readiness()
        if not ready:
            self.record_private_ttd_bypass(reason)
            return 0
        ticket = self._next_voice_ticket()
        item = TtdQueueItem(
            turn_id=request.turn_id,
            text_queue=request.text_queue,
            final_future=request.final_future,
            cancel_event=request.cancel_event,
            turn_started_at=request.turn_started_at,
            committed_at=request.committed_at,
            voice_mode=self._normalize_voice_mode(voice_mode),
            ticket=ticket,
        )
        try:
            self.voice_queue.put_nowait(item)
        except queue.Full:
            self._increment_state("dropped_total")
            self._mark_voice_ticket_completed(ticket)
            self.record_private_ttd_bypass("queue_race_full")
            return 0
        self._increment_state("queued_total")
        self._increment_state("private_voice_ttd_eligible_total")
        self._increment_state("private_voice_ttd_committed_total")
        self._update_state(
            last_private_voice_ttd_status="queued",
            last_private_voice_ttd_reason="threshold_committed",
            last_private_voice_ttd_turn_id=item.turn_id,
            last_private_voice_ttd_model=PRIVATE_VOICE_TTD_MODEL,
            last_private_voice_ttd_input_mode=PRIVATE_VOICE_TTD_INPUT_MODE,
            last_private_voice_ttd_output_format=PRIVATE_VOICE_TTD_OUTPUT_FORMAT,
            last_private_voice_ttd_committed_ms=round(
                max(0.0, item.committed_at - item.turn_started_at) * 1000.0,
                1,
            ),
        )
        return ticket

    def say_overlap(self, request, *, voice_mode="full"):
        if not isinstance(request, OverlapCommitRequest):
            raise TypeError("request must be OverlapCommitRequest")
        ready, reason = self.private_overlap_readiness()
        if not ready:
            self.record_private_overlap_bypass(reason)
            return 0
        ticket = self._next_voice_ticket()
        item = OverlapQueueItem(
            turn_id=request.turn_id,
            lead_text=request.lead_text,
            split_offset=request.split_offset,
            tail_future=request.tail_future,
            cancel_event=request.cancel_event,
            turn_started_at=request.turn_started_at,
            lead_committed_at=request.lead_committed_at,
            voice_mode=self._normalize_voice_mode(voice_mode),
            ticket=ticket,
        )
        try:
            self.voice_queue.put_nowait(item)
        except queue.Full:
            self._increment_state("dropped_total")
            self._mark_voice_ticket_completed(ticket)
            self.record_private_overlap_bypass("queue_race_full")
            return 0
        self._increment_state("queued_total")
        self._increment_state("private_voice_overlap_eligible_total")
        self._increment_state("private_voice_overlap_committed_total")
        self._update_state(
            last_private_voice_overlap_status="queued",
            last_private_voice_overlap_reason="lead_committed",
            last_private_voice_overlap_turn_id=item.turn_id,
            last_private_voice_overlap_full_chars=0,
            last_private_voice_overlap_lead_chars=len(item.lead_text),
            last_private_voice_overlap_tail_chars=0,
            last_private_voice_overlap_split_offset=item.split_offset,
            last_private_voice_overlap_lead_committed_ms=round(
                max(0.0, item.lead_committed_at - item.turn_started_at) * 1000.0,
                1,
            ),
        )
        return ticket

    def _next_voice_ticket(self):
        condition = self._voice_completion
        with condition:
            self._voice_enqueued_ticket += 1
            return self._voice_enqueued_ticket

    def latest_voice_ticket(self):
        condition = getattr(self, "_voice_completion", None)
        if condition is None:
            return 0
        with condition:
            return int(getattr(self, "_voice_enqueued_ticket", 0))

    def completed_voice_ticket(self):
        condition = getattr(self, "_voice_completion", None)
        if condition is None:
            return 0
        with condition:
            return int(getattr(self, "_voice_completed_ticket", 0))

    def _mark_voice_ticket_completed(self, ticket):
        ticket = int(ticket or 0)
        condition = getattr(self, "_voice_completion", None)
        if ticket <= 0 or condition is None:
            return
        with condition:
            if ticket > self._voice_completed_ticket:
                self._voice_completed_ticket = ticket
            condition.notify_all()

    def wait_for_voice_ticket(self, ticket, timeout=None):
        ticket = int(ticket or 0)
        if ticket <= 0:
            return True
        condition = getattr(self, "_voice_completion", None)
        if condition is None:
            return False
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        with condition:
            while self._voice_completed_ticket < ticket:
                if deadline is None:
                    condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    return False
                condition.wait(remaining)
            return True

    def set_presence_pcm_output(
        self,
        *,
        available_fn=None,
        playback_fn=None,
        stream_available_fn=None,
        stream_playback_fn=None,
    ):
        """Install or clear the bounded Presence-node PCM output adapter."""

        clearing = (
            available_fn is None
            and playback_fn is None
            and stream_available_fn is None
            and stream_playback_fn is None
        )
        if not clearing and (
            not callable(available_fn)
            or not callable(playback_fn)
            or (
                stream_available_fn is not None
                and not callable(stream_available_fn)
            )
            or (
                stream_playback_fn is not None
                and not callable(stream_playback_fn)
            )
        ):
            raise TypeError(
                "available_fn and playback_fn must be callable; stream callbacks are optional"
            )
        lock = getattr(self, "_presence_output_lock", None)
        if lock is None:
            self._presence_output_available_fn = available_fn
            self._presence_pcm_output_fn = playback_fn
            self._presence_pcm_stream_available_fn = stream_available_fn
            self._presence_pcm_stream_output_fn = stream_playback_fn
        else:
            with lock:
                self._presence_output_available_fn = available_fn
                self._presence_pcm_output_fn = playback_fn
                self._presence_pcm_stream_available_fn = stream_available_fn
                self._presence_pcm_stream_output_fn = stream_playback_fn
        self._update_state(
            presence_pcm_output_configured=not clearing,
            presence_pcm_output_available=False,
            last_presence_pcm_error=None,
        )

    def _presence_pcm_outputs_if_available(self):
        lock = getattr(self, "_presence_output_lock", None)
        if lock is None:
            available_fn = getattr(self, "_presence_output_available_fn", None)
            playback_fn = getattr(self, "_presence_pcm_output_fn", None)
            stream_available_fn = getattr(
                self, "_presence_pcm_stream_available_fn", None
            )
            stream_playback_fn = getattr(
                self, "_presence_pcm_stream_output_fn", None
            )
        else:
            with lock:
                available_fn = self._presence_output_available_fn
                playback_fn = self._presence_pcm_output_fn
                stream_available_fn = getattr(
                    self, "_presence_pcm_stream_available_fn", None
                )
                stream_playback_fn = getattr(
                    self, "_presence_pcm_stream_output_fn", None
                )
        if not callable(available_fn) or not callable(playback_fn):
            return None
        try:
            available = bool(available_fn())
        except Exception as exc:
            self._update_state(
                presence_pcm_output_available=False,
                last_presence_pcm_error=f"{type(exc).__name__}: {exc}",
            )
            log_event("voice", f"Presence PCM availability check failed: {exc}")
            return None
        self._update_state(presence_pcm_output_available=available)
        if not available:
            return None
        streaming = False
        if callable(stream_playback_fn):
            try:
                streaming = (
                    True
                    if not callable(stream_available_fn)
                    else bool(stream_available_fn())
                )
            except Exception as exc:
                log_event("voice", f"Presence PCM streaming availability failed: {exc}")
                streaming = False
        return playback_fn, (stream_playback_fn if streaming else None)

    def _presence_pcm_playback_if_available(self):
        """Compatibility wrapper for callers that only need buffered PCM."""
        outputs = self._presence_pcm_outputs_if_available()
        if outputs is None:
            return None
        return outputs[0]

    def shutdown(self):
        with self._shutdown_lock:
            self._shutdown_event.set()
        self._cancel_active_http_stream()
        self._update_state(status="shutdown")
        self.lipsync.stop()
        worker = getattr(self, "worker", None)
        if worker is not None and worker.is_alive():
            self.voice_queue.put(None)
            worker.join(timeout=2.0)

    def _shutdown_requested(self):
        event = getattr(self, "_shutdown_event", None)
        return bool(event is not None and event.is_set())

    def _set_active_stream_handles(self, *, response=None, process=None):
        lock = getattr(self, "_stream_handle_lock", None)
        if lock is None:
            self._active_stream_response = response
            self._active_stream_process = process
            return
        with lock:
            if response is not None:
                self._active_stream_response = response
            if process is not None:
                self._active_stream_process = process

    def _clear_active_stream_handles(self, *, response=None, process=None):
        lock = getattr(self, "_stream_handle_lock", None)

        def _clear():
            if response is None or getattr(self, "_active_stream_response", None) is response:
                self._active_stream_response = None
            if process is None or getattr(self, "_active_stream_process", None) is process:
                self._active_stream_process = None

        if lock is None:
            _clear()
            return
        with lock:
            _clear()

    def _cancel_active_http_stream(self):
        lock = getattr(self, "_stream_handle_lock", None)
        if lock is None:
            response = getattr(self, "_active_stream_response", None)
            process = getattr(self, "_active_stream_process", None)
        else:
            with lock:
                response = getattr(self, "_active_stream_response", None)
                process = getattr(self, "_active_stream_process", None)
                self._active_stream_response = None
                self._active_stream_process = None
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        if process is not None:
            try:
                if process.poll() is None:
                    process.terminate()
            except Exception:
                pass

    def _filter_recognized_text(self, text, *, source_label):
        text = str(text or "").strip()
        if len(text) <= 1:
            return ""
        blacklist = ["ghiền mì gõ", "la la school", "subscribe", "video tiếp theo"]
        if any(item in text.lower() for item in blacklist):
            log_event("voice", f"Blocked junk transcript ({source_label}): {text}")
            print("🚫 Chặn transcript rác")
            return ""
        return text

    def _recognize_audio_data(self, audio, *, source_label="microphone"):
        with self._recognizer_lock:
            text = self.recognizer.recognize_google(
                audio, language="vi-VN"
            ).strip()
        text = self._filter_recognized_text(text, source_label=source_label)
        if not text:
            return ""
        log_event("voice", f"Voice recognized ({source_label}): {text}")
        return text

    def _recognize_audio_data_detailed(self, audio, *, source_label):
        with self._recognizer_lock:
            response = self.recognizer.recognize_google(
                audio,
                language="vi-VN",
                show_all=True,
            )

        alternatives = []
        if isinstance(response, dict):
            alternatives = list(response.get("alternative") or [])
        elif isinstance(response, list):
            alternatives = list(response)
        elif isinstance(response, str):
            alternatives = [{"transcript": response}]

        selected_text = ""
        selected_confidence = None
        for alternative in alternatives:
            if not isinstance(alternative, dict):
                continue
            candidate = self._filter_recognized_text(
                alternative.get("transcript", ""),
                source_label=source_label,
            )
            if not candidate:
                continue
            selected_text = candidate
            raw_confidence = alternative.get("confidence")
            try:
                selected_confidence = (
                    float(raw_confidence) if raw_confidence is not None else None
                )
            except (TypeError, ValueError):
                selected_confidence = None
            break

        if selected_text:
            confidence_label = (
                f"{selected_confidence:.3f}"
                if selected_confidence is not None
                else "unavailable"
            )
            log_event(
                "voice",
                f"Voice recognized ({source_label}): {selected_text} "
                f"| confidence={confidence_label}",
            )
        return PCMTranscriptionResult(
            text=selected_text,
            confidence=selected_confidence,
            alternatives=len(alternatives),
        )

    def transcribe_pcm(self, pcm, *, sample_rate=16000, sample_width=2):
        """Recognize raw mono PCM received from an external Nana body."""
        if not isinstance(pcm, (bytes, bytearray, memoryview)):
            raise TypeError("pcm must be bytes-like")
        if sample_rate < 8000 or sample_rate > 48000 or sample_width not in (1, 2, 3, 4):
            raise ValueError("unsupported PCM format")
        payload = bytes(pcm)
        if not payload or len(payload) % sample_width:
            return ""

        self._update_state(
            status="recognizing",
            listening=True,
            last_listen_start=time.time(),
            last_error=None,
        )
        try:
            audio = sr.AudioData(payload, int(sample_rate), int(sample_width))
            return self._recognize_audio_data(audio, source_label="presence_node")
        except sr.UnknownValueError:
            log_event("voice", "Presence-node recognition returned unknown value")
            self._update_state(last_error="unknown_value")
            return ""
        except Exception as exc:
            log_event("voice", f"Presence-node recognition failed: {exc}")
            self._update_state(last_error=str(exc))
            return ""
        finally:
            self._update_state(
                status="idle",
                listening=False,
                last_listen_end=time.time(),
            )

    def transcribe_pcm_detailed(self, pcm, *, sample_rate=16000, sample_width=2):
        """Recognize Presence PCM and retain provider confidence when available."""
        if not isinstance(pcm, (bytes, bytearray, memoryview)):
            raise TypeError("pcm must be bytes-like")
        if sample_rate < 8000 or sample_rate > 48000 or sample_width not in (1, 2, 3, 4):
            raise ValueError("unsupported PCM format")
        payload = bytes(pcm)
        if not payload or len(payload) % sample_width:
            return PCMTranscriptionResult("", None, 0)

        self._update_state(
            status="recognizing",
            listening=True,
            last_listen_start=time.time(),
            last_error=None,
        )
        try:
            audio = sr.AudioData(payload, int(sample_rate), int(sample_width))
            return self._recognize_audio_data_detailed(
                audio,
                source_label="presence_node",
            )
        except sr.UnknownValueError:
            log_event("voice", "Presence-node recognition returned unknown value")
            self._update_state(last_error="unknown_value")
            return PCMTranscriptionResult("", None, 0)
        except Exception as exc:
            log_event("voice", f"Presence-node recognition failed: {exc}")
            self._update_state(last_error=str(exc))
            return PCMTranscriptionResult("", None, 0)
        finally:
            self._update_state(
                status="idle",
                listening=False,
                last_listen_end=time.time(),
            )

    def listen_voice(self):
        self._update_state(
            status="listening",
            listening=True,
            last_listen_start=time.time(),
            last_error=None,
        )
        try:
            log_event("voice", "Voice listen started")
            print("🟢 Nana đang nghe... thả ESC để gửi")
            with _open_microphone_source(MIC_DEVICE_INDEX) as (source, device_info):
                self._update_state(
                    microphone_active_index=device_info.get("index"),
                    microphone_active_name=str(device_info.get("name") or "unknown"),
                    microphone_open_status="ready",
                )
                if not self.noise_calibrated:
                    self.recognizer.adjust_for_ambient_noise(source, duration=1)
                    self.noise_calibrated = True
                    print("🎛 Noise calibrated")
                frames = []
                started_at = time.time()
                while keyboard.is_pressed("esc"):
                    if time.time() - started_at > 30:
                        return ""
                    chunk = source.stream.read(source.CHUNK)
                    frames.append(chunk)
                duration = time.time() - started_at
                if duration < MIN_VOICE_SECONDS or not frames:
                    log_event("voice", f"Voice too short: {duration:.2f}s")
                    print("🔇 Voice quá ngắn")
                    return ""
                raw_data = b"".join(frames)
                audio = sr.AudioData(raw_data, source.SAMPLE_RATE, source.SAMPLE_WIDTH)
            self._update_state(status="recognizing")
            print("🧠 Đang nhận diện giọng nói...")
            return self._recognize_audio_data(audio)
        except sr.UnknownValueError:
            log_event("voice", "Voice recognition returned unknown value")
            self._update_state(last_error="unknown_value")
            return ""
        except MicrophoneOpenError as exc:
            log_event("voice", f"Voice input error: {exc}")
            self._update_state(
                last_error=str(exc),
                microphone_open_status="error",
            )
            print("❌ Voice input lỗi:", exc)
            return ""
        except Exception as exc:
            log_event("voice", f"Voice error: {exc}")
            self._update_state(last_error=str(exc))
            print("❌ Voice lỗi:", exc)
            return ""
        finally:
            self._update_state(
                status="idle",
                listening=False,
                last_listen_end=time.time(),
            )

    def _voice_worker(self):
        while True:
            item = self.voice_queue.get()
            if item is None:
                self.voice_queue.task_done()
                break
            if isinstance(item, TtdQueueItem):
                try:
                    self._process_ttd_queue_item(item)
                finally:
                    self._mark_voice_ticket_completed(item.ticket)
                    self.voice_queue.task_done()
                continue
            if isinstance(item, OverlapQueueItem):
                try:
                    self._process_overlap_queue_item(item)
                finally:
                    self._mark_voice_ticket_completed(item.ticket)
                    self.voice_queue.task_done()
                continue
            text, voice_mode = self._unpack_voice_item(item)
            consumed_items = 1
            completed_ticket = self._voice_item_ticket(item)
            # Briefly wait to coalesce short flushed chunks into one TTS call.
            # The LLM streaming pushes many short voice.say() calls — without
            # this wait each one is its own ElevenLabs roundtrip, leaving a
            # ~1s gap between voice chunks.
            if len(text) < 200:
                deadline = time.time() + 0.15
                while time.time() < deadline:
                    try:
                        extra = self.voice_queue.get(timeout=max(0.01, deadline - time.time()))
                    except queue.Empty:
                        break
                    if extra is None:
                        self.voice_queue.task_done()
                        self.voice_queue.put(None)  # re-arm sentinel
                        break
                    consumed_items += 1
                    completed_ticket = max(
                        completed_ticket,
                        self._voice_item_ticket(extra),
                    )
                    extra_text, extra_mode = self._unpack_voice_item(extra)
                    if extra_text and extra_text.strip():
                        text = text.rstrip() + " " + extra_text.strip()
                        if voice_mode == "chat" and extra_mode != "chat":
                            voice_mode = extra_mode
            try:
                self._update_state(
                    status="talking",
                    speaking=True,
                    last_speak_start=time.time(),
                    last_error=None,
                )
                presence_outputs = self._presence_pcm_outputs_if_available()
                if presence_outputs is None:
                    completion = self._tts_and_lipsync(text, voice_mode=voice_mode)
                else:
                    presence_playback, presence_stream_playback = presence_outputs
                    completion = self._tts_to_presence_pcm(
                        text,
                        voice_mode=voice_mode,
                        playback_fn=presence_playback,
                        stream_playback_fn=presence_stream_playback,
                    )
                if completion is not None and not completion.audio_completed:
                    log_event(
                        "voice",
                        f"Voice job ended incomplete: state={completion.state} "
                        f"played={completion.played_segments}/{completion.requested_segments} "
                        f"reason={completion.abort_reason}",
                    )
            except Exception as exc:
                log_event("voice", f"Voice worker failed: {type(exc).__name__}: {exc}")
                self._update_state(last_error=f"{type(exc).__name__}: {exc}")
            finally:
                self._update_state(
                    status="shutdown" if self._shutdown_requested() else "idle",
                    speaking=False,
                    last_speak_end=time.time(),
                )
                self._mark_voice_ticket_completed(completed_ticket)
                for _ in range(consumed_items):
                    self.voice_queue.task_done()

    def _process_ttd_queue_item(self, item):
        self._update_state(
            status="talking",
            speaking=True,
            last_speak_start=time.time(),
            last_error=None,
            last_private_voice_ttd_status="running",
            last_private_voice_ttd_reason="opening_socket",
        )
        completion = None
        try:
            presence_outputs = self._presence_pcm_outputs_if_available()
            if presence_outputs is None:
                completion = self._tts_ttd_completion(item)
            else:
                payload = self._wait_ttd_final_payload(item)
                presence_playback, presence_stream_playback = presence_outputs
                completion = self._tts_to_presence_pcm(
                    payload.full_text,
                    voice_mode=item.voice_mode,
                    playback_fn=presence_playback,
                    stream_playback_fn=presence_stream_playback,
                )
                self._increment_state("private_voice_ttd_bypassed_total")
                self._update_state(
                    last_private_voice_ttd_status="presence_fallback",
                    last_private_voice_ttd_reason="presence_became_available",
                    last_private_voice_ttd_source_chars=len(payload.full_text),
                )
            if completion is not None and not completion.audio_completed:
                log_event(
                    "voice",
                    "Private TTD job ended incomplete: "
                    f"state={completion.state} reason={completion.abort_reason}",
                )
        except Exception as exc:
            self._increment_state("private_voice_ttd_failed_total")
            self._update_state(
                last_error=f"{type(exc).__name__}: {exc}",
                last_private_voice_ttd_status="failed",
                last_private_voice_ttd_reason=f"{type(exc).__name__}: {exc}",
                last_private_voice_ttd_error=f"{type(exc).__name__}: {exc}",
            )
            log_event(
                "voice",
                f"Private TTD worker failed: {type(exc).__name__}: {exc}",
            )
        finally:
            self._update_state(
                status="shutdown" if self._shutdown_requested() else "idle",
                speaking=False,
                last_speak_end=time.time(),
            )

    def _process_overlap_queue_item(self, item):
        self._update_state(
            status="talking",
            speaking=True,
            last_speak_start=time.time(),
            last_error=None,
            last_private_voice_overlap_status="running",
            last_private_voice_overlap_reason="lead_fetch",
        )
        completion = None
        try:
            presence_outputs = self._presence_pcm_outputs_if_available()
            if presence_outputs is None:
                completion = self._tts_overlap_completion(item)
            else:
                payload = self._wait_overlap_tail_payload(item)
                presence_playback, presence_stream_playback = presence_outputs
                completion = self._tts_to_presence_pcm(
                    payload.full_text,
                    voice_mode=item.voice_mode,
                    playback_fn=presence_playback,
                    stream_playback_fn=presence_stream_playback,
                )
                self._increment_state("private_voice_overlap_bypassed_total")
                self._update_state(
                    last_private_voice_overlap_status="presence_fallback",
                    last_private_voice_overlap_reason="presence_became_available",
                    last_private_voice_overlap_full_chars=len(payload.full_text),
                    last_private_voice_overlap_tail_chars=len(payload.tail_text),
                )
            if completion is not None and not completion.audio_completed:
                log_event(
                    "voice",
                    "Private overlap job ended incomplete: "
                    f"state={completion.state} reason={completion.abort_reason}",
                )
        except Exception as exc:
            self._increment_state("private_voice_overlap_failed_total")
            self._update_state(
                last_error=f"{type(exc).__name__}: {exc}",
                last_private_voice_overlap_status="failed",
                last_private_voice_overlap_reason=f"{type(exc).__name__}: {exc}",
            )
            log_event(
                "voice",
                f"Private overlap worker failed: {type(exc).__name__}: {exc}",
            )
        finally:
            self._update_state(
                status="shutdown" if self._shutdown_requested() else "idle",
                speaking=False,
                last_speak_end=time.time(),
            )

    def _open_presence_pcm_stream_response(self, text, profile):
        """Open a raw PCM stream and keep the TTS slot until it is drained."""

        semaphore = getattr(self, "_tts_semaphore", None)
        acquired = False
        if semaphore is not None:
            semaphore.acquire()
            acquired = True
        started_at = time.perf_counter()
        try:
            response = self._voice_http_post(
                self._streaming_tts_url(),
                params={"output_format": "pcm_16000"},
                json={
                    "text": self._prepare_tts_request_text(text),
                    "model_id": self._pick_tts_model(text),
                    "voice_settings": profile,
                },
                headers={
                    "xi-api-key": ELEVEN_API_KEY,
                    "Content-Type": "application/json",
                    "Accept": "application/octet-stream",
                },
                stream=True,
                timeout=(
                    VOICE_HTTP_STREAM_CONNECT_TIMEOUT_S,
                    VOICE_HTTP_STREAM_STALL_TIMEOUT_S,
                ),
            )
            self._update_state(
                last_tts_request_ms=round(
                    (time.perf_counter() - started_at) * 1000.0,
                    1,
                )
            )
            if int(getattr(response, "status_code", 0) or 0) != 200:
                status = int(getattr(response, "status_code", 0) or 0)
                body = str(getattr(response, "text", "") or "")[:300]
                try:
                    response.close()
                finally:
                    if acquired and semaphore is not None:
                        semaphore.release()
                raise PresenceTTSStreamingUnavailable(
                    f"provider_http_{status or 'error'}: {body}"
                )
            return response
        except PresenceTTSStreamingUnavailable:
            raise
        except Exception:
            if acquired and semaphore is not None:
                semaphore.release()
            raise

    def _iter_presence_pcm_response_pull(self, response, started_at, metrics):
        """Conservative provider iterator used by the production Presence path."""

        yielded = False
        remainder = b""
        metrics.setdefault("prefetch_high_water", 0)
        metrics.setdefault("prefetch_starvations", 0)
        metrics.setdefault("max_prefetch_wait_ms", 0.0)
        self._update_state(
            last_presence_pcm_source_mode="pull_safe",
            last_presence_pcm_prefetch_high_water=0,
            last_presence_pcm_prefetch_starvations=0,
            last_presence_pcm_max_prefetch_wait_ms=0.0,
        )
        try:
            content = iter(
                response.iter_content(
                    chunk_size=PRESENCE_PCM_STREAM_NETWORK_CHUNK_BYTES
                )
            )
            while True:
                read_started = time.perf_counter()
                try:
                    payload = next(content)
                except StopIteration:
                    break
                now = time.perf_counter()
                if not payload:
                    continue
                read_wait_ms = (now - read_started) * 1000.0
                if metrics.get("first_byte_at") is None:
                    metrics["first_byte_at"] = now
                    self._update_state(
                        last_presence_pcm_first_byte_ms=round(
                            (now - started_at) * 1000.0,
                            1,
                        )
                    )
                else:
                    metrics["max_gap_ms"] = max(
                        float(metrics.get("max_gap_ms", 0.0)),
                        read_wait_ms,
                    )
                metrics["last_payload_at"] = now
                metrics["chunks"] = int(metrics.get("chunks", 0)) + 1
                metrics["bytes"] = int(metrics.get("bytes", 0)) + len(payload)
                self._update_state(
                    last_presence_pcm_network_chunks=int(metrics["chunks"]),
                    last_presence_pcm_network_bytes=int(metrics["bytes"]),
                    last_presence_pcm_max_network_gap_ms=round(
                        float(metrics["max_gap_ms"]),
                        1,
                    ),
                )

                data = remainder + bytes(payload)
                aligned = len(data) - (len(data) % 2)
                if aligned:
                    remainder = data[aligned:]
                    yielded = True
                    yield data[:aligned]
                else:
                    remainder = data

            if remainder:
                if not yielded:
                    raise PresenceTTSStreamingUnavailable(
                        "provider returned an unaligned/empty PCM stream"
                    )
                raise RuntimeError("provider PCM stream ended on an odd byte")
            if not yielded:
                raise PresenceTTSStreamingUnavailable(
                    "provider returned an empty PCM stream"
                )
        except PresenceTTSStreamingUnavailable:
            raise
        except Exception as exc:
            if not yielded:
                raise PresenceTTSStreamingUnavailable(
                    f"provider stream produced no PCM: {type(exc).__name__}: {exc}"
                ) from exc
            raise
        finally:
            response.close()

    def _iter_presence_pcm_response(self, response, started_at, metrics=None):
        yielded = False
        if metrics is None:
            metrics = {
                "chunks": 0,
                "bytes": 0,
                "max_gap_ms": 0.0,
                "last_payload_at": None,
                "first_byte_at": None,
                "prefetch_high_water": 0,
                "prefetch_starvations": 0,
                "max_prefetch_wait_ms": 0.0,
            }
        metrics.setdefault("prefetch_high_water", 0)
        metrics.setdefault("prefetch_starvations", 0)
        metrics.setdefault("max_prefetch_wait_ms", 0.0)

        if not PRESENCE_PCM_STREAM_PREFETCH_ENABLED:
            yield from self._iter_presence_pcm_response_pull(
                response,
                started_at,
                metrics,
            )
            return

        self._update_state(last_presence_pcm_source_mode="prefetch_experimental")

        source_queue = queue.Queue(maxsize=PRESENCE_PCM_STREAM_PREFETCH_CHUNKS)
        stop_event = threading.Event()

        def update_source_state():
            self._update_state(
                last_presence_pcm_network_chunks=int(metrics["chunks"]),
                last_presence_pcm_network_bytes=int(metrics["bytes"]),
                last_presence_pcm_max_network_gap_ms=round(
                    float(metrics["max_gap_ms"]),
                    1,
                ),
                last_presence_pcm_prefetch_high_water=int(
                    metrics["prefetch_high_water"]
                ),
                last_presence_pcm_prefetch_starvations=int(
                    metrics["prefetch_starvations"]
                ),
                last_presence_pcm_max_prefetch_wait_ms=round(
                    float(metrics["max_prefetch_wait_ms"]),
                    1,
                ),
            )

        def queue_item(kind, value):
            while not stop_event.is_set():
                try:
                    source_queue.put((kind, value), timeout=0.1)
                    return True
                except queue.Full:
                    continue
            return False

        def read_provider():
            remainder = b""
            produced = False
            try:
                content = iter(
                    response.iter_content(
                        chunk_size=PRESENCE_PCM_STREAM_NETWORK_CHUNK_BYTES
                    )
                )
                while not stop_event.is_set():
                    read_started = time.perf_counter()
                    try:
                        payload = next(content)
                    except StopIteration:
                        break
                    now = time.perf_counter()
                    if not payload:
                        continue

                    # This is provider read blocking only. Queue backpressure is
                    # deliberately outside the measurement, unlike the old
                    # pull-based inter-payload metric which also counted node
                    # playback-credit pacing.
                    read_wait_ms = (now - read_started) * 1000.0
                    if metrics.get("first_byte_at") is None:
                        metrics["first_byte_at"] = now
                        self._update_state(
                            last_presence_pcm_first_byte_ms=round(
                                (now - started_at) * 1000.0,
                                1,
                            )
                        )
                    else:
                        metrics["max_gap_ms"] = max(
                            float(metrics.get("max_gap_ms", 0.0)),
                            read_wait_ms,
                        )
                    metrics["last_payload_at"] = now
                    metrics["chunks"] = int(metrics.get("chunks", 0)) + 1
                    metrics["bytes"] = int(metrics.get("bytes", 0)) + len(payload)

                    data = remainder + bytes(payload)
                    aligned = len(data) - (len(data) % 2)
                    if aligned:
                        produced = True
                        remainder = data[aligned:]
                        if not queue_item("data", data[:aligned]):
                            return
                        metrics["prefetch_high_water"] = max(
                            int(metrics.get("prefetch_high_water", 0)),
                            source_queue.qsize(),
                        )
                    else:
                        remainder = data
                    update_source_state()

                if stop_event.is_set():
                    return
                if remainder:
                    error = (
                        PresenceTTSStreamingUnavailable(
                            "provider returned an unaligned/empty PCM stream"
                        )
                        if not produced
                        else RuntimeError("provider PCM stream ended on an odd byte")
                    )
                    queue_item("error", error)
                elif not produced:
                    queue_item(
                        "error",
                        PresenceTTSStreamingUnavailable(
                            "provider returned an empty PCM stream"
                        ),
                    )
                else:
                    queue_item("end", None)
            except Exception as exc:
                queue_item("error", exc)

        producer = threading.Thread(
            target=read_provider,
            name="nana-presence-pcm-prefetch",
            daemon=True,
        )
        producer.start()
        try:
            while True:
                wait_started = time.perf_counter()
                kind, value = source_queue.get()
                wait_ms = (time.perf_counter() - wait_started) * 1000.0
                if yielded:
                    metrics["max_prefetch_wait_ms"] = max(
                        float(metrics.get("max_prefetch_wait_ms", 0.0)),
                        wait_ms,
                    )
                    if wait_ms >= 5.0:
                        metrics["prefetch_starvations"] = int(
                            metrics.get("prefetch_starvations", 0)
                        ) + 1
                    update_source_state()

                if kind == "data":
                    yielded = True
                    yield value
                    continue
                if kind == "end":
                    break
                if kind != "error":
                    raise RuntimeError(f"unknown Presence PCM source item: {kind}")
                if isinstance(value, PresenceTTSStreamingUnavailable):
                    raise value
                if not yielded:
                    raise PresenceTTSStreamingUnavailable(
                        "provider stream produced no PCM: "
                        f"{type(value).__name__}: {value}"
                    ) from value
                raise value
        except PresenceTTSStreamingUnavailable:
            raise
        except Exception as exc:
            if not yielded:
                raise PresenceTTSStreamingUnavailable(
                    f"provider stream produced no PCM: {type(exc).__name__}: {exc}"
                ) from exc
            raise
        finally:
            stop_event.set()
            response.close()
            producer.join(timeout=1.0)

    def _tts_to_presence_pcm_streaming(
        self,
        raw_text,
        *,
        voice_mode,
        stream_playback_fn,
    ):
        """Stream one or more ElevenLabs PCM responses directly to Presence."""

        started_at = time.perf_counter()
        segments = self._presence_tts_segments(raw_text)
        prepared_segments = [
            render_inline_audio_tags(segment).tts_text.strip()
            for segment in segments
        ]
        prepared_chars = sum(len(segment) for segment in prepared_segments)
        request_ms = 0.0
        playback_ms = 0.0
        fetched_segments = 0
        played_segments = 0
        total_pcm_bytes = 0
        provider_segment_bytes = []
        last_stream_id = None
        last_underruns = None
        played_duration_ms = 0.0
        network_metrics = {
            "chunks": 0,
            "bytes": 0,
            "max_gap_ms": 0.0,
            "last_payload_at": None,
            "first_byte_at": None,
            "prefetch_high_water": 0,
            "prefetch_starvations": 0,
            "max_prefetch_wait_ms": 0.0,
        }

        self._update_state(
            last_tts_strategy="presence_pcm16_streaming",
            last_tts_text_len=len(raw_text),
            last_tts_chunks=len(segments),
            last_tts_voice_mode=self._normalize_voice_mode(voice_mode),
            last_tts_playback_policy="presence_session_streaming_pcm",
            last_presence_pcm_status="rendering_stream",
            last_presence_pcm_stream_id=None,
            last_presence_pcm_bytes=0,
            last_presence_pcm_provider_bytes=0,
            last_presence_pcm_text_chars=len(raw_text),
            last_presence_pcm_prepared_chars=prepared_chars,
            last_presence_pcm_provider_duration_ms=0.0,
            last_presence_pcm_ms_per_prepared_char=None,
            last_presence_pcm_segment_bytes=(),
            last_presence_pcm_underruns=None,
            last_presence_pcm_elapsed_ms=None,
            last_presence_pcm_streaming=True,
            last_presence_pcm_first_audio_ms=None,
            last_presence_pcm_first_byte_ms=None,
            last_presence_pcm_network_chunks=0,
            last_presence_pcm_network_bytes=0,
            last_presence_pcm_max_network_gap_ms=0.0,
            last_presence_pcm_prefetch_high_water=0,
            last_presence_pcm_prefetch_starvations=0,
            last_presence_pcm_max_prefetch_wait_ms=0.0,
            last_presence_pcm_source_mode=(
                "prefetch_experimental"
                if PRESENCE_PCM_STREAM_PREFETCH_ENABLED
                else "pull_safe"
            ),
            last_presence_pcm_playback_ratio=None,
            last_presence_pcm_playback_grade="none",
            last_presence_pcm_error=None,
        )

        try:
            for segment_index, segment in enumerate(segments, start=1):
                tone = _detect_tone_from_text(segment)
                profile = VOICE_TONE_PROFILES.get(
                    tone,
                    VOICE_TONE_PROFILES["default"],
                )
                request_started = time.perf_counter()
                response = self._open_presence_pcm_stream_response(
                    segment,
                    profile,
                )
                request_ms += (time.perf_counter() - request_started) * 1000.0
                pcm_stream = self._iter_presence_pcm_response(
                    response,
                    request_started,
                    network_metrics,
                )
                playback_started = time.perf_counter()
                semaphore = getattr(self, "_tts_semaphore", None)
                try:
                    playback = stream_playback_fn(
                        pcm_stream,
                        sample_rate=16000,
                    )
                finally:
                    close = getattr(pcm_stream, "close", None)
                    if callable(close):
                        close()
                    response.close()
                    if semaphore is not None:
                        semaphore.release()
                playback_ms += (time.perf_counter() - playback_started) * 1000.0
                if not bool(getattr(playback, "completed", False)):
                    raise RuntimeError("Presence node reported incomplete streaming playback")

                fetched_segments += 1
                played_segments += 1
                pcm_bytes = int(getattr(playback, "total_bytes", 0) or 0)
                total_pcm_bytes += pcm_bytes
                provider_segment_bytes.append(pcm_bytes)
                played_duration_ms += pcm_bytes / 32.0
                last_stream_id = getattr(playback, "stream_id", None)
                last_underruns = int(getattr(playback, "underruns", 0) or 0)
                first_audio_ms = getattr(playback, "first_audio_ms", None)
                if first_audio_ms is not None:
                    self._update_state(
                        last_presence_pcm_first_audio_ms=round(
                            float(first_audio_ms),
                            1,
                        )
                    )
                log_event(
                    "voice",
                    "Presence PCM streaming segment: "
                    f"index={segment_index}/{len(segments)} "
                    f"chars={len(segment)} bytes={pcm_bytes} "
                    f"duration_ms={pcm_bytes / 32.0:.1f} "
                    f"first_audio_ms={first_audio_ms}",
                )

            result = self._audio_completion_result(
                state="completed",
                segments=segments,
                fetched_segments=fetched_segments,
                played_segments=played_segments,
                original_chars=len(raw_text),
                played_duration_ms=played_duration_ms,
            )
            self._update_state(
                last_tts_request_ms=round(request_ms, 1),
                last_tts_playback_ms=round(playback_ms, 1),
                last_tts_total_ms=round(
                    (time.perf_counter() - started_at) * 1000.0,
                    1,
                ),
                last_presence_pcm_status="complete_streaming",
                last_presence_pcm_stream_id=last_stream_id,
                last_presence_pcm_bytes=total_pcm_bytes,
                last_presence_pcm_provider_bytes=total_pcm_bytes,
                last_presence_pcm_provider_duration_ms=round(
                    total_pcm_bytes / 32.0,
                    1,
                ),
                last_presence_pcm_ms_per_prepared_char=(
                    None
                    if prepared_chars <= 0
                    else round((total_pcm_bytes / 32.0) / prepared_chars, 2)
                ),
                last_presence_pcm_segment_bytes=tuple(provider_segment_bytes),
                last_presence_pcm_underruns=last_underruns,
                last_presence_pcm_elapsed_ms=round(playback_ms, 1),
                last_presence_pcm_network_chunks=network_metrics["chunks"],
                last_presence_pcm_network_bytes=network_metrics["bytes"],
                last_presence_pcm_max_network_gap_ms=round(
                    float(network_metrics["max_gap_ms"]),
                    1,
                ),
                last_presence_pcm_prefetch_high_water=network_metrics[
                    "prefetch_high_water"
                ],
                last_presence_pcm_prefetch_starvations=network_metrics[
                    "prefetch_starvations"
                ],
                last_presence_pcm_max_prefetch_wait_ms=round(
                    float(network_metrics["max_prefetch_wait_ms"]),
                    1,
                ),
                last_presence_pcm_playback_ratio=(
                    None
                    if played_duration_ms <= 0.0
                    else round(playback_ms / played_duration_ms, 2)
                ),
                last_presence_pcm_playback_grade=(
                    "stretched"
                    if played_duration_ms > 0.0
                    and playback_ms - played_duration_ms >= 1000.0
                    and playback_ms / played_duration_ms >= 1.6
                    else "delayed"
                    if played_duration_ms > 0.0
                    and playback_ms - played_duration_ms >= 1000.0
                    and playback_ms / played_duration_ms >= 1.3
                    else "normal"
                ),
                last_presence_pcm_error=None,
            )
            log_event(
                "voice",
                "Presence PCM streaming complete: "
                f"chars={len(raw_text)} segments={played_segments} "
                f"bytes={total_pcm_bytes} duration_ms={total_pcm_bytes / 32.0:.1f} "
                f"network_chunks={network_metrics['chunks']} "
                f"max_provider_wait_ms={network_metrics['max_gap_ms']:.1f} "
                f"prefetch_high_water={network_metrics['prefetch_high_water']} "
                f"prefetch_starvations={network_metrics['prefetch_starvations']} "
                f"underruns={last_underruns}",
            )
            return self._record_audio_completion(result)
        except PresenceTTSStreamingUnavailable:
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            result = self._audio_completion_result(
                state="playback_error" if fetched_segments else "provider_error",
                segments=segments,
                fetched_segments=fetched_segments,
                played_segments=played_segments,
                original_chars=len(raw_text),
                played_duration_ms=played_duration_ms,
                abort_reason="presence_pcm_stream_failed",
            )
            self._update_state(
                last_error=error,
                last_tts_request_ms=round(request_ms, 1),
                last_tts_playback_ms=round(playback_ms, 1),
                last_tts_total_ms=round(
                    (time.perf_counter() - started_at) * 1000.0,
                    1,
                ),
                last_presence_pcm_status="failed_streaming",
                last_presence_pcm_stream_id=last_stream_id,
                last_presence_pcm_bytes=total_pcm_bytes,
                last_presence_pcm_provider_bytes=total_pcm_bytes,
                last_presence_pcm_provider_duration_ms=round(
                    total_pcm_bytes / 32.0,
                    1,
                ),
                last_presence_pcm_ms_per_prepared_char=(
                    None
                    if prepared_chars <= 0
                    else round((total_pcm_bytes / 32.0) / prepared_chars, 2)
                ),
                last_presence_pcm_segment_bytes=tuple(provider_segment_bytes),
                last_presence_pcm_underruns=last_underruns,
                last_presence_pcm_elapsed_ms=round(playback_ms, 1),
                last_presence_pcm_network_chunks=network_metrics["chunks"],
                last_presence_pcm_network_bytes=network_metrics["bytes"],
                last_presence_pcm_max_network_gap_ms=round(
                    float(network_metrics["max_gap_ms"]),
                    1,
                ),
                last_presence_pcm_prefetch_high_water=network_metrics[
                    "prefetch_high_water"
                ],
                last_presence_pcm_prefetch_starvations=network_metrics[
                    "prefetch_starvations"
                ],
                last_presence_pcm_max_prefetch_wait_ms=round(
                    float(network_metrics["max_prefetch_wait_ms"]),
                    1,
                ),
                last_presence_pcm_playback_ratio=(
                    None
                    if played_duration_ms <= 0.0
                    else round(playback_ms / played_duration_ms, 2)
                ),
                last_presence_pcm_playback_grade="failed",
                last_presence_pcm_error=error,
            )
            log_event("voice", f"Presence PCM streaming failed: {error}")
            print(f"Presence PCM streaming failed: {error}")
            return self._record_audio_completion(result)

    def _tts_to_presence_pcm(
        self,
        text,
        *,
        voice_mode,
        playback_fn,
        stream_playback_fn=None,
    ):
        """Render ElevenLabs PCM and drain it on one LAN node.

        The streaming path is attempted only when the node advertises the
        optional stream capability. A provider/format rejection falls back to
        the established complete-payload path before any local replay occurs.
        """

        if DEBUG_NO_TTS or VOICE_TEST_MODE:
            return None
        input_text = str(text or "").strip()
        if not input_text:
            return None

        raw_text, truncated = self._bound_presence_text(input_text)
        self._update_state(
            last_presence_pcm_input_chars=len(input_text),
            last_presence_pcm_text_truncated=truncated,
            last_presence_pcm_omitted_chars=max(0, len(input_text) - len(raw_text)),
        )
        if truncated:
            log_event(
                "voice",
                "Presence voice text bounded: "
                f"input_chars={len(input_text)} "
                f"sent_chars={len(raw_text)} "
                f"limit={PRESENCE_VOICE_MAX_CHARS}",
            )

        if PRESENCE_TTS_STREAMING_ENABLED and callable(stream_playback_fn):
            try:
                return self._tts_to_presence_pcm_streaming(
                    raw_text,
                    voice_mode=voice_mode,
                    stream_playback_fn=stream_playback_fn,
                )
            except PresenceTTSStreamingUnavailable as exc:
                log_event(
                    "voice",
                    f"Presence PCM streaming unavailable; fallback=buffered: {exc}",
                )
                self._update_state(
                    last_presence_pcm_error=f"stream_fallback: {exc}",
                )

        started_at = time.perf_counter()
        segments = self._presence_tts_segments(raw_text)
        prepared_segments = [
            render_inline_audio_tags(segment).tts_text.strip()
            for segment in segments
        ]
        prepared_chars = sum(len(segment) for segment in prepared_segments)
        fetched_segments = 0
        played_segments = 0
        played_duration_ms = 0.0
        total_pcm_bytes = 0
        provider_pcm_bytes = 0
        provider_segment_bytes = []
        last_stream_id = None
        last_underruns = None
        request_ms = 0.0
        playback_ms = 0.0
        error = None

        self._update_state(
            last_tts_strategy="presence_pcm16",
            last_tts_text_len=len(raw_text),
            last_tts_chunks=len(segments),
            last_tts_voice_mode=self._normalize_voice_mode(voice_mode),
            last_tts_playback_policy="presence_session_bounded_pcm",
            last_presence_pcm_status="rendering",
            last_presence_pcm_stream_id=None,
            last_presence_pcm_bytes=0,
            last_presence_pcm_provider_bytes=0,
            last_presence_pcm_text_chars=len(raw_text),
            last_presence_pcm_prepared_chars=prepared_chars,
            last_presence_pcm_provider_duration_ms=0.0,
            last_presence_pcm_ms_per_prepared_char=None,
            last_presence_pcm_segment_bytes=(),
            last_presence_pcm_underruns=None,
            last_presence_pcm_elapsed_ms=None,
            last_presence_pcm_streaming=False,
            last_presence_pcm_first_audio_ms=None,
            last_presence_pcm_first_byte_ms=None,
            last_presence_pcm_network_chunks=0,
            last_presence_pcm_network_bytes=0,
            last_presence_pcm_max_network_gap_ms=0.0,
            last_presence_pcm_prefetch_high_water=0,
            last_presence_pcm_prefetch_starvations=0,
            last_presence_pcm_max_prefetch_wait_ms=0.0,
            last_presence_pcm_source_mode="buffered_fallback",
            last_presence_pcm_playback_ratio=None,
            last_presence_pcm_playback_grade="none",
            last_presence_pcm_error=None,
        )

        try:
            for segment_index, segment in enumerate(segments, start=1):
                request_started = time.perf_counter()
                pcm = self.tts_to_pcm16(segment, sample_rate=16000)
                request_ms += (time.perf_counter() - request_started) * 1000.0
                if not pcm:
                    raise RuntimeError("ElevenLabs returned no PCM payload")
                fetched_segments += 1
                pcm_bytes = len(pcm)
                provider_pcm_bytes += pcm_bytes
                provider_segment_bytes.append(pcm_bytes)
                log_event(
                    "voice",
                    "Presence PCM provider segment: "
                    f"index={segment_index}/{len(segments)} "
                    f"chars={len(segment)} "
                    f"prepared_chars={len(prepared_segments[segment_index - 1])} "
                    f"bytes={pcm_bytes} duration_ms={pcm_bytes / 32.0:.1f}",
                )

                playback_started = time.perf_counter()
                playback = playback_fn(pcm, sample_rate=16000)
                playback_ms += (time.perf_counter() - playback_started) * 1000.0
                if not bool(getattr(playback, "completed", False)):
                    raise RuntimeError("Presence node reported incomplete playback")

                total_pcm_bytes += pcm_bytes
                played_duration_ms += pcm_bytes / 32.0
                played_segments += 1
                last_stream_id = getattr(playback, "stream_id", None)
                last_underruns = int(getattr(playback, "underruns", 0) or 0)

            result = self._audio_completion_result(
                state="completed",
                segments=segments,
                fetched_segments=fetched_segments,
                played_segments=played_segments,
                original_chars=len(raw_text),
                played_duration_ms=played_duration_ms,
            )
            self._update_state(
                last_tts_request_ms=round(request_ms, 1),
                last_tts_playback_ms=round(playback_ms, 1),
                last_tts_total_ms=round(
                    (time.perf_counter() - started_at) * 1000.0,
                    1,
                ),
                last_presence_pcm_status="complete",
                last_presence_pcm_stream_id=last_stream_id,
                last_presence_pcm_bytes=total_pcm_bytes,
                last_presence_pcm_provider_bytes=provider_pcm_bytes,
                last_presence_pcm_provider_duration_ms=round(
                    provider_pcm_bytes / 32.0,
                    1,
                ),
                last_presence_pcm_ms_per_prepared_char=(
                    None
                    if prepared_chars <= 0
                    else round((provider_pcm_bytes / 32.0) / prepared_chars, 2)
                ),
                last_presence_pcm_segment_bytes=tuple(provider_segment_bytes),
                last_presence_pcm_underruns=last_underruns,
                last_presence_pcm_elapsed_ms=round(playback_ms, 1),
                last_presence_pcm_error=None,
            )
            log_event(
                "voice",
                "Presence PCM complete: "
                f"chars={len(raw_text)} prepared_chars={prepared_chars} "
                f"segments={played_segments} provider_bytes={provider_pcm_bytes} "
                f"played_bytes={total_pcm_bytes} duration_ms={provider_pcm_bytes / 32.0:.1f} "
                f"underruns={last_underruns}",
            )
            return self._record_audio_completion(result)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            state = "provider_error" if fetched_segments == 0 else "playback_error"
            result = self._audio_completion_result(
                state=state,
                segments=segments,
                fetched_segments=fetched_segments,
                played_segments=played_segments,
                original_chars=len(raw_text),
                played_duration_ms=played_duration_ms,
                abort_reason="presence_pcm_failed",
            )
            self._update_state(
                last_error=error,
                last_tts_request_ms=round(request_ms, 1),
                last_tts_playback_ms=round(playback_ms, 1),
                last_tts_total_ms=round(
                    (time.perf_counter() - started_at) * 1000.0,
                    1,
                ),
                last_presence_pcm_status="failed",
                last_presence_pcm_stream_id=last_stream_id,
                last_presence_pcm_bytes=total_pcm_bytes,
                last_presence_pcm_provider_bytes=provider_pcm_bytes,
                last_presence_pcm_provider_duration_ms=round(
                    provider_pcm_bytes / 32.0,
                    1,
                ),
                last_presence_pcm_ms_per_prepared_char=(
                    None
                    if prepared_chars <= 0
                    else round((provider_pcm_bytes / 32.0) / prepared_chars, 2)
                ),
                last_presence_pcm_segment_bytes=tuple(provider_segment_bytes),
                last_presence_pcm_underruns=last_underruns,
                last_presence_pcm_elapsed_ms=round(playback_ms, 1),
                last_presence_pcm_error=error,
            )
            log_event(
                "voice",
                "Presence PCM output failed: "
                f"{error} | chars={len(raw_text)} prepared_chars={prepared_chars} "
                f"provider_bytes={provider_pcm_bytes} played_bytes={total_pcm_bytes}",
            )
            print(f"Presence PCM output failed: {error}")
            return self._record_audio_completion(result)

    def _normalize_voice_mode(self, voice_mode):
        mode = str(voice_mode or "chat").strip().lower()
        return mode or "chat"

    def _bound_presence_text(self, text):
        """Return a safe Presence reply without cutting through a sentence."""

        raw = str(text or "").strip()
        if not PRESENCE_VOICE_LIMIT_ENABLED:
            return raw, False
        if len(raw) <= PRESENCE_VOICE_MAX_CHARS:
            return raw, False
        chunks = self._split_tts_text(
            raw,
            max_chars=PRESENCE_VOICE_MAX_CHARS,
            force_chunking=True,
        )
        bounded = (chunks[0] if chunks else raw[:PRESENCE_VOICE_MAX_CHARS]).strip()
        return bounded[:PRESENCE_VOICE_MAX_CHARS].rstrip(), True

    def _presence_tts_segments(self, text):
        """Split Presence TTS into streams that fit the node's 4 MiB buffer."""

        return self._split_tts_text(
            str(text or "").strip(),
            max_chars=PRESENCE_TTS_SEGMENT_MAX_CHARS,
            force_chunking=True,
        )

    def _unpack_voice_item(self, item):
        if isinstance(item, VoiceQueueItem):
            return item.text, self._normalize_voice_mode(item.voice_mode)
        return str(item or ""), "chat"

    @staticmethod
    def _voice_item_ticket(item):
        if isinstance(item, VoiceQueueItem):
            return int(item.ticket or 0)
        return 0

    def _is_full_voice_mode(self, voice_mode):
        return self._normalize_voice_mode(voice_mode) in {"full", "story", "manual"}

    def _chunk_limit_for_mode(self, voice_mode):
        if self._is_full_voice_mode(voice_mode):
            return VOICE_STORY_CHUNK_MAX_CHARS
        return VOICE_CHUNK_MAX_CHARS

    def _seam_grade(self, wait_ms):
        if wait_ms is None:
            return "none"
        wait = float(wait_ms)
        if wait <= 250:
            return "good"
        if wait <= 450:
            return "acceptable"
        if wait <= 700:
            return "warn"
        if wait <= 1000:
            return "bad"
        return "critical"

    def _audio_completion_result(
        self,
        *,
        state,
        segments,
        fetched_segments,
        played_segments,
        original_chars,
        played_duration_ms,
        abort_reason="none",
        stop_confirmed=True,
    ):
        requested = len(segments)
        played = max(0, min(int(played_segments), requested))
        completed = state == "completed" and played == requested
        played_chars = sum(len(segment) for segment in segments[:played])
        remaining_chars = 0 if completed else max(0, int(original_chars) - played_chars)
        return AudioCompletionResult(
            state=str(state),
            audio_completed=completed,
            requested_segments=requested,
            fetched_segments=max(0, min(int(fetched_segments), requested)),
            played_segments=played,
            unplayed_segments=max(0, requested - played),
            original_chars=max(0, int(original_chars)),
            remaining_chars=remaining_chars,
            played_duration_ms=round(max(0.0, float(played_duration_ms)), 1),
            abort_reason=str(abort_reason or "none"),
            stop_confirmed=bool(stop_confirmed),
        )

    def _record_audio_completion(self, result):
        if result is None:
            return None
        self._update_state(
            last_audio_state=result.state,
            last_audio_completed=result.audio_completed,
            last_audio_requested_segments=result.requested_segments,
            last_audio_fetched_segments=result.fetched_segments,
            last_audio_played_segments=result.played_segments,
            last_audio_unplayed_segments=result.unplayed_segments,
            last_audio_original_chars=result.original_chars,
            last_audio_remaining_chars=result.remaining_chars,
            last_audio_played_duration_ms=result.played_duration_ms,
            last_audio_abort_reason=result.abort_reason,
        )
        return result

    def _provider_segments_for_full_voice(self, text):
        raw = str(text or "").strip()
        if not raw:
            return []
        return self._split_tts_text(
            raw,
            max_chars=ELEVENLABS_SINGLE_REQUEST_MAX_CHARS,
            force_chunking=True,
        )

    def _http_streaming_pilot_selected(self, text, voice_mode):
        raw = str(text or "").strip()
        return bool(
            VOICE_STREAMING_ENABLED
            and VOICE_STREAMING_PILOT_ENABLED
            and not VOICE_STREAMING_KILL_SWITCH
            and VOICE_STREAMING_DIRECT_ONLY
            and self._normalize_voice_mode(voice_mode) == "full"
            and raw
            and len(raw) <= ELEVENLABS_SINGLE_REQUEST_MAX_CHARS
        )

    def _resolve_stream_ffmpeg_path(self):
        candidate = str(VOICE_HTTP_STREAM_FFMPEG_PATH or "ffmpeg").strip()
        if not candidate:
            return None
        path = Path(candidate).expanduser()
        if path.is_file():
            return str(path.resolve())
        return shutil.which(candidate)

    def _spawn_streaming_decoder(self, ffmpeg_path):
        command = [
            str(ffmpeg_path),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "mp3",
            "-i",
            "pipe:0",
            "-vn",
            "-acodec",
            "pcm_f32le",
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            str(VOICE_HTTP_STREAM_SAMPLE_RATE),
            "pipe:1",
        ]
        kwargs = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.DEVNULL,
            "bufsize": 0,
        }
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if create_no_window:
            kwargs["creationflags"] = create_no_window
        try:
            return subprocess.Popen(command, **kwargs)
        except Exception as exc:
            log_event("voice", f"HTTP stream FFmpeg preflight failed: {exc}")
            return None

    def _open_streaming_tts_response(self, text, profile):
        data = {
            "text": _prepare_tts_text(text),
            "model_id": self._pick_tts_model(text),
            "voice_settings": profile,
        }
        headers = {
            "xi-api-key": ELEVEN_API_KEY,
            "Content-Type": "application/json",
            "Accept": self._accept_for_output_format(ELEVEN_OUTPUT_FORMAT),
        }
        params = {"output_format": ELEVEN_OUTPUT_FORMAT} if ELEVEN_OUTPUT_FORMAT else None
        return self._voice_http_post(
            self._streaming_tts_url(),
            params=params,
            json=data,
            headers=headers,
            stream=True,
            timeout=(VOICE_HTTP_STREAM_CONNECT_TIMEOUT_S, VOICE_HTTP_STREAM_STALL_TIMEOUT_S),
        )

    def _close_stream_decoder(self, process, *, terminate=False):
        if process is None:
            return
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        except Exception:
            pass
        if terminate:
            try:
                if process.poll() is None:
                    process.terminate()
            except Exception:
                pass
        try:
            process.wait(timeout=1.0)
        except Exception:
            try:
                if process.poll() is None:
                    process.kill()
            except Exception:
                pass

    def _reset_http_stream_telemetry(self, *, ffmpeg_ready):
        self._update_state(
            last_stream_transport_state="connecting" if ffmpeg_ready else "preflight_failed",
            last_stream_playback_state="buffering" if ffmpeg_ready else "idle",
            last_stream_time_to_first_byte_ms=None,
            last_stream_time_to_first_pcm_ms=None,
            last_stream_time_to_first_audio_ms=None,
            last_stream_received_bytes=0,
            last_stream_start_buffer_ms=0.0,
            last_stream_refill_count=0,
            last_stream_rebuffer_count=0,
            last_stream_rebuffer_total_ms=0.0,
            last_stream_output_write_calls=0,
            last_stream_output_underflow_count=0,
            last_stream_playback_buffer_low_watermark_ms=None,
            last_stream_max_feed_gap_ms=0.0,
            last_stream_last_underflow_buffer_ms=None,
            last_stream_last_underflow_queue_depth=None,
            last_stream_last_underflow_decoder_done=None,
            last_stream_callback_calls=0,
            last_stream_callback_status_underflows=0,
            last_stream_ring_starvation_count=0,
            last_stream_ring_low_watermark_ms=None,
            last_stream_max_callback_lateness_ms=0.0,
            last_stream_feeder_refill_count=0,
            last_stream_feeder_done=False,
            last_stream_callback_finished=False,
            last_stream_max_network_gap_ms=0.0,
            last_stream_provider_eof=False,
            last_stream_decoder_eof=False,
            last_stream_abort_reason="none" if ffmpeg_ready else "ffmpeg_preflight_failed",
            last_stream_ffmpeg_ready=bool(ffmpeg_ready),
        )

    def _tts_http_streaming_completion(self, text, profile, voice_mode, process, *, original_chars=None):
        """Stream one provider response through one FFmpeg and OutputStream."""
        started_at = time.perf_counter()
        raw_text = str(text or "")
        original_chars = len(raw_text) if original_chars is None else max(0, int(original_chars))
        segments = [raw_text]
        pcm_queue = queue.Queue(maxsize=VOICE_HTTP_STREAM_PCM_QUEUE_CHUNKS)
        provider_eof = threading.Event()
        decoder_done = threading.Event()
        decoder_clean_eof = threading.Event()
        transport_done = threading.Event()
        playback_done = threading.Event()
        stream_stop = threading.Event()
        shared_lock = threading.Lock()
        shared = {
            "received_bytes": 0,
            "first_byte_at": None,
            "first_pcm_at": None,
            "first_audio_at": None,
            "last_network_progress_at": started_at,
            "max_network_gap_ms": 0.0,
            "abort_reason": "none",
            "transport_state": "connecting",
            "playback_state": "buffering",
            "response": None,
            "playback_result": None,
        }

        def _set_abort(reason):
            if not reason or reason == "none":
                return
            with shared_lock:
                if shared["abort_reason"] == "none":
                    shared["abort_reason"] = str(reason)
            stream_stop.set()

        def _set_transport_state(state):
            with shared_lock:
                shared["transport_state"] = str(state)
            self._update_state(last_stream_transport_state=str(state))

        def _set_playback_state(state):
            now = time.perf_counter()
            with shared_lock:
                shared["playback_state"] = str(state)
                if state == "playing" and shared["first_audio_at"] is None:
                    shared["first_audio_at"] = now
                first_audio_at = shared["first_audio_at"]
            self._update_state(
                last_stream_playback_state=str(state),
                last_stream_time_to_first_audio_ms=(
                    None if first_audio_at is None else round((first_audio_at - started_at) * 1000.0, 1)
                ),
            )

        def _put_pcm(pcm):
            while not self._shutdown_requested() and not stream_stop.is_set():
                try:
                    pcm_queue.put(pcm, timeout=0.05)
                    return True
                except queue.Full:
                    continue
            return False

        def _decoder_reader():
            remainder = b""
            decoder_failed = False
            stdout_eof = False
            try:
                stdout = getattr(process, "stdout", None)
                if stdout is None:
                    raise RuntimeError("ffmpeg_stdout_missing")
                while not self._shutdown_requested() and not stream_stop.is_set():
                    chunk = stdout.read(VOICE_HTTP_STREAM_PCM_READ_BYTES)
                    if not chunk:
                        stdout_eof = True
                        break
                    payload = remainder + bytes(chunk)
                    aligned = len(payload) - (len(payload) % 4)
                    if aligned <= 0:
                        remainder = payload
                        continue
                    pcm = np.frombuffer(payload[:aligned], dtype="<f4").astype(np.float32, copy=True)
                    remainder = payload[aligned:]
                    if len(pcm) == 0:
                        continue
                    now = time.perf_counter()
                    with shared_lock:
                        if shared["first_pcm_at"] is None:
                            shared["first_pcm_at"] = now
                        first_pcm_at = shared["first_pcm_at"]
                    self._update_state(
                        last_stream_time_to_first_pcm_ms=round((first_pcm_at - started_at) * 1000.0, 1)
                    )
                    if not _put_pcm(pcm):
                        break
                if remainder and not self._shutdown_requested() and not stream_stop.is_set():
                    decoder_failed = True
                    _set_abort("decoder_partial_sample")
                if (
                    stdout_eof
                    and not remainder
                    and not decoder_failed
                    and not self._shutdown_requested()
                    and not stream_stop.is_set()
                ):
                    return_code = process.wait(timeout=1.0)
                    if int(return_code or 0) != 0:
                        decoder_failed = True
                        _set_abort("decoder_error")
                    else:
                        decoder_clean_eof.set()
                        self._update_state(last_stream_decoder_eof=True)
            except Exception as exc:
                if not self._shutdown_requested():
                    decoder_failed = True
                    _set_abort("decoder_error")
                    log_event("voice", f"HTTP stream decoder failed: {exc}")
            finally:
                decoder_done.set()
                if decoder_failed:
                    _set_playback_state("aborted")

        def _write_decoder_input(payload):
            stdin = getattr(process, "stdin", None)
            if stdin is None:
                raise RuntimeError("ffmpeg_stdin_missing")
            view = memoryview(payload)
            while view and not self._shutdown_requested() and not stream_stop.is_set():
                written = stdin.write(view)
                if written is None:
                    written = len(view)
                if written <= 0:
                    raise BrokenPipeError("ffmpeg_stdin_closed")
                view = view[written:]
            return not view

        def _transport_reader():
            response = None
            semaphore = getattr(self, "_tts_semaphore", None)
            acquired = False
            try:
                if semaphore is not None:
                    while not self._shutdown_requested() and not stream_stop.is_set():
                        acquired = semaphore.acquire(timeout=0.1)
                        if acquired:
                            break
                    if not acquired:
                        if self._shutdown_requested():
                            _set_abort("shutdown_cancelled")
                            _set_transport_state("cancelled")
                        return
                if self._shutdown_requested() or stream_stop.is_set():
                    if self._shutdown_requested():
                        _set_abort("shutdown_cancelled")
                        _set_transport_state("cancelled")
                    return

                response = self._open_streaming_tts_response(raw_text, profile)
                if stream_stop.is_set() and not self._shutdown_requested():
                    response.close()
                    return
                if self._shutdown_requested():
                    _set_abort("shutdown_cancelled")
                    _set_transport_state("cancelled")
                    response.close()
                    return
                shutdown_lock = getattr(self, "_shutdown_lock", None)
                if shutdown_lock is None:
                    if self._shutdown_requested():
                        response.close()
                        _set_abort("shutdown_cancelled")
                        _set_transport_state("cancelled")
                        return
                    self._set_active_stream_handles(response=response, process=process)
                else:
                    with shutdown_lock:
                        if self._shutdown_requested():
                            response.close()
                            _set_abort("shutdown_cancelled")
                            _set_transport_state("cancelled")
                            return
                        self._set_active_stream_handles(response=response, process=process)
                with shared_lock:
                    shared["response"] = response

                status_code = int(getattr(response, "status_code", 0) or 0)
                if status_code != 200:
                    _set_abort(f"provider_http_{status_code or 'error'}")
                    _set_transport_state("error")
                    return

                _set_transport_state("receiving")
                for payload in response.iter_content(chunk_size=VOICE_HTTP_STREAM_NETWORK_CHUNK_BYTES):
                    if self._shutdown_requested() or stream_stop.is_set():
                        if not self._shutdown_requested():
                            return
                        _set_abort("shutdown_cancelled")
                        _set_transport_state("cancelled")
                        return
                    if not payload:
                        continue
                    now = time.perf_counter()
                    with shared_lock:
                        gap_ms = (now - shared["last_network_progress_at"]) * 1000.0
                        shared["max_network_gap_ms"] = max(shared["max_network_gap_ms"], gap_ms)
                        shared["last_network_progress_at"] = now
                        shared["received_bytes"] += len(payload)
                        if shared["first_byte_at"] is None:
                            shared["first_byte_at"] = now
                        first_byte_at = shared["first_byte_at"]
                        received_bytes = shared["received_bytes"]
                        max_gap_ms = shared["max_network_gap_ms"]
                    self._update_state(
                        last_stream_time_to_first_byte_ms=round((first_byte_at - started_at) * 1000.0, 1),
                        last_stream_received_bytes=received_bytes,
                        last_stream_max_network_gap_ms=round(max_gap_ms, 1),
                    )
                    if not _write_decoder_input(payload):
                        return

                with shared_lock:
                    transport_abort = shared["abort_reason"]
                if not self._shutdown_requested() and transport_abort == "none":
                    provider_eof.set()
                    self._update_state(last_stream_provider_eof=True)
                    _set_transport_state("eof")
            except requests.exceptions.ReadTimeout as exc:
                if self._shutdown_requested():
                    _set_abort("shutdown_cancelled")
                    _set_transport_state("cancelled")
                else:
                    _set_abort("network_stall_timeout")
                    _set_transport_state("error")
                    log_event("voice", f"HTTP stream network stall: {exc}")
            except Exception as exc:
                if self._shutdown_requested():
                    _set_abort("shutdown_cancelled")
                    _set_transport_state("cancelled")
                else:
                    _set_abort("network_error")
                    _set_transport_state("error")
                    log_event("voice", f"HTTP stream transport failed: {exc}")
            finally:
                try:
                    stdin = getattr(process, "stdin", None)
                    if stdin is not None and not stdin.closed:
                        stdin.close()
                except Exception:
                    pass
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
                if acquired:
                    semaphore.release()
                transport_done.set()

        def _on_playback_done(result):
            with shared_lock:
                shared["playback_result"] = result
            playback_done.set()

        self._reset_http_stream_telemetry(ffmpeg_ready=True)
        self._set_active_stream_handles(process=process)
        self._record_audio_completion(
            self._audio_completion_result(
                state="created",
                segments=segments,
                fetched_segments=0,
                played_segments=0,
                original_chars=original_chars,
                played_duration_ms=0.0,
            )
        )
        self._update_state(
            last_streaming_action="http_stream_pilot",
            last_streaming_reason="private_full_voice",
            last_audio_playback_watchdog="stream_stall",
            last_audio_playback_timeout_ms=float(VOICE_HTTP_STREAM_STALL_TIMEOUT_S * 1000),
        )

        decoder_thread = threading.Thread(
            target=_decoder_reader,
            daemon=True,
            name="tts-http-stream-decoder",
        )
        transport_thread = threading.Thread(
            target=_transport_reader,
            daemon=True,
            name="tts-http-stream-reader",
        )
        decoder_thread.start()

        shutdown_lock = getattr(self, "_shutdown_lock", None)
        if shutdown_lock is None:
            can_start_playback = not self._shutdown_requested()
            if can_start_playback:
                self.lipsync.play_pcm_stream_nonblocking(
                    pcm_queue,
                    decoder_done,
                    samplerate=VOICE_HTTP_STREAM_SAMPLE_RATE,
                    startup_buffer_ms=VOICE_HTTP_STREAM_START_BUFFER_MS,
                    stall_timeout_s=VOICE_HTTP_STREAM_STALL_TIMEOUT_S,
                    on_state=_set_playback_state,
                    on_done=_on_playback_done,
                    output_stream_factory=getattr(self, "_stream_output_stream_factory", None),
                    callback_output=VOICE_STREAM_CALLBACK_OUTPUT_ENABLED,
                )
        else:
            with shutdown_lock:
                can_start_playback = not self._shutdown_requested()
                if can_start_playback:
                    self.lipsync.play_pcm_stream_nonblocking(
                        pcm_queue,
                        decoder_done,
                        samplerate=VOICE_HTTP_STREAM_SAMPLE_RATE,
                        startup_buffer_ms=VOICE_HTTP_STREAM_START_BUFFER_MS,
                        stall_timeout_s=VOICE_HTTP_STREAM_STALL_TIMEOUT_S,
                        on_state=_set_playback_state,
                        on_done=_on_playback_done,
                        output_stream_factory=getattr(self, "_stream_output_stream_factory", None),
                        callback_output=VOICE_STREAM_CALLBACK_OUTPUT_ENABLED,
                    )

        if can_start_playback:
            transport_thread.start()
        else:
            _set_abort("shutdown_cancelled")
            _set_transport_state("cancelled")
            _set_playback_state("cancelled")
            decoder_done.set()
            playback_done.set()

        network_watchdog_fired = False
        while not playback_done.wait(timeout=0.05):
            if self._shutdown_requested():
                _set_abort("shutdown_cancelled")
                self._cancel_active_http_stream()
                self.lipsync.stop()
                break
            with shared_lock:
                transport_state = shared["transport_state"]
                last_progress_at = shared["last_network_progress_at"]
            if (
                transport_state in {"connecting", "receiving"}
                and time.perf_counter() - last_progress_at > VOICE_HTTP_STREAM_STALL_TIMEOUT_S
            ):
                network_watchdog_fired = True
                _set_abort("network_stall_timeout")
                _set_transport_state("error")
                self._cancel_active_http_stream()
                break
            with shared_lock:
                current_abort = shared["abort_reason"]
            if current_abort != "none" and not transport_done.is_set():
                self._cancel_active_http_stream()
                break

        if not playback_done.is_set():
            cleanup_deadline = time.perf_counter() + 2.0
            while not playback_done.wait(timeout=0.05) and time.perf_counter() < cleanup_deadline:
                if self._shutdown_requested():
                    self.lipsync.stop()
                    break
            if not playback_done.is_set():
                self.lipsync.stop()

        with shared_lock:
            early_playback_result = shared["playback_result"]
        if early_playback_result is not None and not early_playback_result.completed:
            _set_abort(early_playback_result.abort_reason or "playback_error")
            if not transport_done.is_set():
                self._cancel_active_http_stream()

        transport_thread.join(timeout=1.0) if transport_thread.ident is not None else None
        decoder_thread.join(timeout=1.0)

        with shared_lock:
            playback_result = shared["playback_result"]
            abort_reason = shared["abort_reason"]
            received_bytes = shared["received_bytes"]
            first_byte_at = shared["first_byte_at"]
            first_pcm_at = shared["first_pcm_at"]
            first_audio_at = shared["first_audio_at"]
            max_network_gap_ms = shared["max_network_gap_ms"]
            transport_state = shared["transport_state"]
            playback_state = shared["playback_state"]

        if self._shutdown_requested():
            abort_reason = "shutdown_cancelled"
            transport_state = "cancelled"
            playback_state = "cancelled"
        elif network_watchdog_fired:
            abort_reason = "network_stall_timeout"

        provider_finished = provider_eof.is_set()
        decoder_finished = decoder_done.is_set()
        decoder_succeeded = decoder_clean_eof.is_set()
        pcm_drained = pcm_queue.empty()
        playback_finished = bool(playback_result is not None and playback_result.completed)
        completed = bool(
            abort_reason == "none"
            and transport_state == "eof"
            and provider_finished
            and decoder_finished
            and decoder_succeeded
            and pcm_drained
            and playback_finished
        )

        if completed:
            final_state = "completed"
            abort_reason = "none"
            transport_state = "eof"
            playback_state = "done"
        elif abort_reason == "shutdown_cancelled":
            final_state = "cancelled"
        elif str(abort_reason).startswith("provider_http_"):
            final_state = "provider_error"
        else:
            final_state = "aborted"
            if abort_reason == "none":
                abort_reason = getattr(playback_result, "abort_reason", "incomplete_stream") or "incomplete_stream"

        played_duration_ms = float(getattr(playback_result, "played_duration_ms", 0.0) or 0.0)
        start_buffer_ms = float(getattr(playback_result, "start_buffer_ms", 0.0) or 0.0)
        refill_count = int(getattr(playback_result, "refill_count", 0) or 0)
        rebuffer_count = int(getattr(playback_result, "rebuffer_count", 0) or 0)
        rebuffer_total_ms = float(getattr(playback_result, "rebuffer_total_ms", 0.0) or 0.0)
        output_write_calls = int(getattr(playback_result, "output_write_calls", 0) or 0)
        output_underflow_count = int(getattr(playback_result, "output_underflow_count", 0) or 0)
        playback_buffer_low_watermark_ms = getattr(
            playback_result,
            "playback_buffer_low_watermark_ms",
            None,
        )
        max_feed_gap_ms = float(getattr(playback_result, "max_feed_gap_ms", 0.0) or 0.0)
        last_underflow_buffer_ms = getattr(playback_result, "last_underflow_buffer_ms", None)
        last_underflow_queue_depth = getattr(playback_result, "last_underflow_queue_depth", None)
        last_underflow_decoder_done = getattr(playback_result, "last_underflow_decoder_done", None)
        callback_calls = int(getattr(playback_result, "callback_calls", 0) or 0)
        callback_status_underflows = int(
            getattr(playback_result, "callback_status_underflows", 0) or 0
        )
        ring_starvation_count = int(
            getattr(playback_result, "ring_starvation_count", 0) or 0
        )
        ring_low_watermark_ms = getattr(playback_result, "ring_low_watermark_ms", None)
        max_callback_lateness_ms = float(
            getattr(playback_result, "max_callback_lateness_ms", 0.0) or 0.0
        )
        feeder_refill_count = int(
            getattr(playback_result, "feeder_refill_count", 0) or 0
        )
        feeder_done = bool(getattr(playback_result, "feeder_done", False))
        callback_finished = bool(getattr(playback_result, "callback_finished", False))
        total_ms = (time.perf_counter() - started_at) * 1000.0
        self._update_state(
            last_stream_transport_state=transport_state,
            last_stream_playback_state=playback_state,
            last_stream_time_to_first_byte_ms=(
                None if first_byte_at is None else round((first_byte_at - started_at) * 1000.0, 1)
            ),
            last_stream_time_to_first_pcm_ms=(
                None if first_pcm_at is None else round((first_pcm_at - started_at) * 1000.0, 1)
            ),
            last_stream_time_to_first_audio_ms=(
                None if first_audio_at is None else round((first_audio_at - started_at) * 1000.0, 1)
            ),
            last_stream_received_bytes=received_bytes,
            last_stream_start_buffer_ms=round(start_buffer_ms, 1),
            last_stream_refill_count=refill_count,
            last_stream_rebuffer_count=rebuffer_count,
            last_stream_rebuffer_total_ms=round(rebuffer_total_ms, 1),
            last_stream_output_write_calls=output_write_calls,
            last_stream_output_underflow_count=output_underflow_count,
            last_stream_playback_buffer_low_watermark_ms=playback_buffer_low_watermark_ms,
            last_stream_max_feed_gap_ms=round(max_feed_gap_ms, 1),
            last_stream_last_underflow_buffer_ms=last_underflow_buffer_ms,
            last_stream_last_underflow_queue_depth=last_underflow_queue_depth,
            last_stream_last_underflow_decoder_done=last_underflow_decoder_done,
            last_stream_callback_calls=callback_calls,
            last_stream_callback_status_underflows=callback_status_underflows,
            last_stream_ring_starvation_count=ring_starvation_count,
            last_stream_ring_low_watermark_ms=ring_low_watermark_ms,
            last_stream_max_callback_lateness_ms=round(max_callback_lateness_ms, 1),
            last_stream_feeder_refill_count=feeder_refill_count,
            last_stream_feeder_done=feeder_done,
            last_stream_callback_finished=callback_finished,
            last_stream_max_network_gap_ms=round(max_network_gap_ms, 1),
            last_stream_provider_eof=provider_finished,
            last_stream_decoder_eof=decoder_succeeded,
            last_stream_abort_reason=abort_reason,
            last_tts_prepare_ms=(
                None if first_audio_at is None else round((first_audio_at - started_at) * 1000.0, 1)
            ),
            last_tts_playback_ms=round(played_duration_ms, 1),
            last_tts_total_ms=round(total_ms, 1),
            last_tts_prebuffer_chunks=1 if start_buffer_ms > 0 else 0,
            last_tts_prebuffer_ms=round(start_buffer_ms, 1),
            last_tts_seam_wait_ms=None,
            last_tts_max_seam_wait_ms=0.0,
            last_tts_avg_seam_wait_ms=None,
            last_tts_seam_grade="none",
            last_tts_playback_policy=(
                "http_stream_callback"
                if VOICE_STREAM_CALLBACK_OUTPUT_ENABLED
                else "http_stream"
            ),
            last_tts_seam_cause="none",
            last_tts_strategy="http_stream",
            last_tts_audio_paths=1 if completed else 0,
            last_tts_text_len=original_chars,
            last_tts_chunks=1,
            last_tts_voice_mode=voice_mode,
            last_tts_chunk_max_chars=ELEVENLABS_SINGLE_REQUEST_MAX_CHARS,
            last_tts_request_ms=(
                None if first_byte_at is None else round((first_byte_at - started_at) * 1000.0, 1)
            ),
        )
        result = self._audio_completion_result(
            state=final_state,
            segments=segments,
            fetched_segments=1 if provider_finished else 0,
            played_segments=1 if completed else 0,
            original_chars=original_chars,
            played_duration_ms=played_duration_ms,
            abort_reason=abort_reason,
        )
        self._record_audio_completion(result)
        if not completed:
            self._cancel_active_http_stream()
        self._clear_active_stream_handles(process=process)
        self._close_stream_decoder(process, terminate=not completed)
        if not result.audio_completed:
            print(
                "🔇 Audio stream chưa hoàn tất: "
                f"transport={transport_state} | playback={playback_state} | "
                f"remaining_chars={result.remaining_chars} | reason={abort_reason}"
            )
        return result

    @staticmethod
    def _ttd_elapsed_ms(item, at=None):
        point = time.perf_counter() if at is None else float(at)
        return round(max(0.0, point - item.turn_started_at) * 1000.0, 1)

    def _wait_ttd_final_payload(self, item):
        if item.cancel_event.is_set() or self._shutdown_requested():
            raise RuntimeError("ttd_cancelled")
        try:
            payload = item.final_future.result(timeout=float(PRIVATE_VOICE_TTD_TIMEOUT_S))
        except FutureTimeoutError as exc:
            raise TimeoutError("ttd_final_timeout") from exc
        if item.cancel_event.is_set() or self._shutdown_requested():
            raise RuntimeError("ttd_cancelled")
        if not isinstance(payload, TtdFinalPayload):
            raise TypeError("ttd_final_payload_invalid")
        if payload.sent_text != payload.full_text:
            raise ValueError("ttd_final_exact_text_mismatch")
        if "".join(payload.source_chunks) != payload.full_text:
            raise ValueError("ttd_final_chunk_mismatch")
        return payload

    def _ttd_websocket_uri(self):
        query = urlencode(
            {
                "model_id": PRIVATE_VOICE_TTD_MODEL,
                "output_format": PRIVATE_VOICE_TTD_OUTPUT_FORMAT,
            }
        )
        return f"wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input?{query}"

    def _ttd_websocket_connect(self, uri):
        return websockets.connect(
            uri,
            additional_headers={"xi-api-key": ELEVEN_API_KEY},
            open_timeout=float(VOICE_HTTP_STREAM_CONNECT_TIMEOUT_S),
            close_timeout=5.0,
            ping_interval=None,
        )

    def _write_ttd_capture(
        self,
        item,
        *,
        pcm_bytes,
        sample_rate,
        arrival_timeline,
        text_timeline,
        source_text,
        provider_sent_text,
        provider_expected_text,
    ):
        payload = bytes(pcm_bytes or b"")
        if not PRIVATE_VOICE_TTD_CAPTURE_ENABLED or not payload:
            return None, None, _pcm_silence_summary(b"", sample_rate)
        capture_dir = Path(PRIVATE_VOICE_TTD_CAPTURE_DIR)
        capture_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
        stem = f"ttd-{stamp}-{str(item.turn_id)[:12]}"
        wav_path = capture_dir / f"{stem}.wav"
        report_path = capture_dir / f"{stem}.json"
        wav_temp = capture_dir / f".{stem}.wav.tmp"
        report_temp = capture_dir / f".{stem}.json.tmp"
        silence = _pcm_silence_summary(payload, sample_rate)
        with wave.open(str(wav_temp), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(int(sample_rate))
            wav_file.writeframes(payload)
        report = {
            "schema_version": 1,
            "turn_id": str(item.turn_id),
            "model": PRIVATE_VOICE_TTD_MODEL,
            "input_mode": PRIVATE_VOICE_TTD_INPUT_MODE,
            "output_format": PRIVATE_VOICE_TTD_OUTPUT_FORMAT,
            "sample_rate": int(sample_rate),
            "pcm_bytes": len(payload),
            "audio_duration_ms": round(
                len(payload) / (2.0 * float(sample_rate)) * 1000.0,
                1,
            ),
            "source_chars": len(str(source_text or "")),
            "provider_sent_chars": len(str(provider_sent_text or "")),
            "provider_expected_chars": len(str(provider_expected_text or "")),
            "source_sha256": hashlib.sha256(
                str(source_text or "").encode("utf-8")
            ).hexdigest(),
            "provider_sent_sha256": hashlib.sha256(
                str(provider_sent_text or "").encode("utf-8")
            ).hexdigest(),
            "provider_expected_sha256": hashlib.sha256(
                str(provider_expected_text or "").encode("utf-8")
            ).hexdigest(),
            "arrival_timeline": list(arrival_timeline or ()),
            "text_timeline": list(text_timeline or ()),
            "silence": silence,
            "contains_text": False,
        }
        report_temp.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(wav_temp, wav_path)
        os.replace(report_temp, report_path)
        return str(wav_path), str(report_path), silence

    async def _run_ttd_websocket(
        self,
        item,
        *,
        initial_source,
        pcm_queue,
        provider_eof,
        shared,
        shared_lock,
        eof_payload=None,
    ):
        uri = self._ttd_websocket_uri()
        remainder = b""

        def _shared_update(**values):
            with shared_lock:
                shared.update(values)

        async def _put_pcm(pcm):
            queue_before = pcm_queue.qsize()
            while not self._shutdown_requested() and not item.cancel_event.is_set():
                try:
                    pcm_queue.put_nowait(pcm)
                    return True, queue_before, pcm_queue.qsize()
                except queue.Full:
                    await asyncio.sleep(0.005)
            return False, queue_before, pcm_queue.qsize()

        async with self._ttd_websocket_connect(uri) as websocket:
            connected_at = time.perf_counter()
            _shared_update(
                connected_at=connected_at,
                last_network_at=connected_at,
                transport_state="connected",
            )
            self._update_state(
                last_private_voice_ttd_connected_ms=self._ttd_elapsed_ms(
                    item, connected_at
                ),
                last_private_voice_ttd_status="connected",
                last_private_voice_ttd_reason="voice_registered",
            )

            initial_profile = VOICE_TONE_PROFILES.get(
                _detect_tone_from_text(initial_source),
                VOICE_TONE_PROFILES["default"],
            )
            await websocket.send(
                json.dumps(
                    {
                        "voices": [VOICE_ID],
                        "voice_settings": {
                            "stability": float(initial_profile.get("stability", 0.5))
                        },
                    }
                )
            )

            async def _send_source(source):
                source = str(source or "")
                prepared = self._prepare_ttd_request_text(source)
                with shared_lock:
                    shared["source_chunks"].append(source)
                    shared["prepared_chunks"].append(prepared)
                    shared["source_chars"] += len(source)
                    shared["prepared_chars"] += len(prepared)
                if not prepared:
                    return
                await websocket.send(
                    json.dumps(
                        {
                            "inputs": [
                                {
                                    "text": prepared,
                                    "voice_id": VOICE_ID,
                                    "new_turn": False,
                                }
                            ]
                        }
                    )
                )
                sent_at = time.perf_counter()
                with shared_lock:
                    shared["sent_chunks"] += 1
                    shared["last_client_send_at"] = sent_at
                    if shared["first_text_sent_at"] is None:
                        shared["first_text_sent_at"] = sent_at
                    first_text_sent_at = shared["first_text_sent_at"]
                    sent_chunks = shared["sent_chunks"]
                    source_chars = shared["source_chars"]
                    prepared_chars = shared["prepared_chars"]
                    shared["text_timeline"].append(
                        {
                            "index": int(sent_chunks),
                            "turn_elapsed_ms": self._ttd_elapsed_ms(
                                item, sent_at
                            ),
                            "source_chars": len(source),
                            "prepared_chars": len(prepared),
                            "source_leading_space": bool(
                                source[:1].isspace()
                            ),
                            "source_trailing_space": bool(
                                source[-1:].isspace()
                            ),
                            "prepared_leading_space": bool(
                                prepared[:1].isspace()
                            ),
                            "prepared_trailing_space": bool(
                                prepared[-1:].isspace()
                            ),
                            "cumulative_source_chars": int(source_chars),
                            "cumulative_prepared_chars": int(
                                prepared_chars
                            ),
                        }
                    )
                self._update_state(
                    last_private_voice_ttd_first_text_sent_ms=self._ttd_elapsed_ms(
                        item, first_text_sent_at
                    ),
                    last_private_voice_ttd_sent_chunks=sent_chunks,
                    last_private_voice_ttd_source_chars=source_chars,
                    last_private_voice_ttd_prepared_chars=prepared_chars,
                    last_private_voice_ttd_status="streaming_text",
                    last_private_voice_ttd_reason="same_turn_incremental_input",
                )

            async def _finalize_payload(payload):
                with shared_lock:
                    source_text = "".join(shared["source_chunks"])
                    provider_sent_text = "".join(
                        shared["prepared_chunks"]
                    ).strip()
                if source_text != payload.full_text:
                    raise ValueError("ttd_sent_source_mismatch")
                provider_expected_text = render_inline_audio_tags(
                    payload.full_text
                ).tts_text
                provider_mismatch_index = _first_text_mismatch(
                    provider_sent_text,
                    provider_expected_text,
                )
                provider_integrity_ok = provider_mismatch_index is None
                provider_missing_chars = max(
                    0,
                    len(provider_expected_text) - len(provider_sent_text),
                )
                provider_duplicate_chars = max(
                    0,
                    len(provider_sent_text) - len(provider_expected_text),
                )
                _shared_update(
                    final_payload=payload,
                    llm_completed_at=payload.llm_completed_at,
                    provider_sent_text=provider_sent_text,
                    provider_expected_text=provider_expected_text,
                    provider_integrity_ok=provider_integrity_ok,
                    provider_mismatch_index=provider_mismatch_index,
                    provider_missing_chars=provider_missing_chars,
                    provider_duplicate_chars=provider_duplicate_chars,
                )
                self._update_state(
                    last_private_voice_ttd_llm_completed_ms=self._ttd_elapsed_ms(
                        item, payload.llm_completed_at
                    ),
                    last_private_voice_ttd_provider_expected_chars=len(
                        provider_expected_text
                    ),
                    last_private_voice_ttd_provider_sent_chars=len(
                        provider_sent_text
                    ),
                    last_private_voice_ttd_provider_integrity_ok=(
                        provider_integrity_ok
                    ),
                    last_private_voice_ttd_provider_mismatch_index=(
                        provider_mismatch_index
                    ),
                    last_private_voice_ttd_provider_missing_chars=(
                        provider_missing_chars
                    ),
                    last_private_voice_ttd_provider_duplicate_chars=(
                        provider_duplicate_chars
                    ),
                )
                if not provider_integrity_ok:
                    raise ValueError("ttd_provider_text_mismatch")
                await websocket.send(json.dumps({"close_socket": True}))

            async def _sender():
                while True:
                    if self._shutdown_requested() or item.cancel_event.is_set():
                        raise RuntimeError("ttd_cancelled")
                    try:
                        source = item.text_queue.get_nowait()
                    except queue.Empty:
                        with shared_lock:
                            last_client_send_at = float(
                                shared.get("last_client_send_at", time.perf_counter())
                            )
                        if time.perf_counter() - last_client_send_at >= 10.0:
                            await websocket.send(
                                json.dumps({"keep_alive": True})
                            )
                            with shared_lock:
                                shared["last_client_send_at"] = time.perf_counter()
                        await asyncio.sleep(0.005)
                        continue
                    if source is None:
                        payload = self._wait_ttd_final_payload(item)
                        await _finalize_payload(payload)
                        return
                    await _send_source(source)

            sender_task = None
            if PRIVATE_VOICE_TTD_INPUT_MODE == "eof_single":
                payload = eof_payload or self._wait_ttd_final_payload(item)
                await _send_source(payload.full_text)
                await _finalize_payload(payload)
            else:
                await _send_source(initial_source)
                sender_task = asyncio.create_task(_sender())
            try:
                while True:
                    if self._shutdown_requested() or item.cancel_event.is_set():
                        raise RuntimeError("ttd_cancelled")
                    if sender_task is not None and sender_task.done():
                        sender_error = sender_task.exception()
                        if sender_error is not None:
                            raise sender_error
                    try:
                        raw = await asyncio.wait_for(websocket.recv(), timeout=0.1)
                    except asyncio.TimeoutError:
                        with shared_lock:
                            last_network_at = shared["last_network_at"]
                        if (
                            time.perf_counter() - last_network_at
                            > float(PRIVATE_VOICE_TTD_TIMEOUT_S)
                        ):
                            raise TimeoutError("ttd_network_stall_timeout")
                        continue

                    now = time.perf_counter()
                    message = json.loads(raw)
                    if message.get("error"):
                        error_name = str(message.get("error") or "provider_error")
                        raise RuntimeError(f"ttd_provider_error:{error_name}")

                    encoded = message.get("audio")
                    if encoded:
                        audio_bytes = base64.b64decode(encoded, validate=True)
                        with shared_lock:
                            previous_network_at = shared["last_network_at"]
                            gap_ms = max(0.0, now - previous_network_at) * 1000.0
                            shared["max_network_gap_ms"] = max(
                                shared["max_network_gap_ms"], gap_ms
                            )
                            shared["last_network_at"] = now
                            shared["audio_chunks"] += 1
                            shared["received_bytes"] += len(audio_bytes)
                            if shared["first_chunk_at"] is None:
                                shared["first_chunk_at"] = now
                            first_chunk_at = shared["first_chunk_at"]
                            audio_chunks = shared["audio_chunks"]
                            received_bytes = shared["received_bytes"]
                            max_gap_ms = shared["max_network_gap_ms"]
                            if PRIVATE_VOICE_TTD_CAPTURE_ENABLED:
                                shared["capture_pcm"].extend(audio_bytes)

                        payload = remainder + audio_bytes
                        aligned = len(payload) - (len(payload) % 2)
                        remainder = payload[aligned:]
                        if aligned:
                            pcm = (
                                np.frombuffer(payload[:aligned], dtype="<i2")
                                .astype(np.float32)
                                / 32768.0
                            )
                            if len(pcm):
                                with shared_lock:
                                    if shared["first_pcm_at"] is None:
                                        shared["first_pcm_at"] = now
                                    first_pcm_at = shared["first_pcm_at"]
                                enqueue_started_at = time.perf_counter()
                                queued, queue_before, queue_after = await _put_pcm(pcm)
                                enqueue_wait_ms = (
                                    time.perf_counter() - enqueue_started_at
                                ) * 1000.0
                                if not queued:
                                    raise RuntimeError("ttd_pcm_cancelled")
                                chunk_audio_ms = (
                                    len(audio_bytes)
                                    / (2.0 * float(_pcm_sample_rate(
                                        PRIVATE_VOICE_TTD_OUTPUT_FORMAT
                                    )))
                                    * 1000.0
                                )
                                since_first_chunk_ms = max(
                                    0.0,
                                    now - first_chunk_at,
                                ) * 1000.0
                                cumulative_audio_ms = (
                                    received_bytes
                                    / (2.0 * float(_pcm_sample_rate(
                                        PRIVATE_VOICE_TTD_OUTPUT_FORMAT
                                    )))
                                    * 1000.0
                                )
                                with shared_lock:
                                    shared["arrival_timeline"].append(
                                        {
                                            "index": int(audio_chunks),
                                            "turn_elapsed_ms": self._ttd_elapsed_ms(
                                                item, now
                                            ),
                                            "gap_ms": round(gap_ms, 1),
                                            "chunk_bytes": len(audio_bytes),
                                            "chunk_audio_ms": round(
                                                chunk_audio_ms, 1
                                            ),
                                            "cumulative_audio_ms": round(
                                                cumulative_audio_ms, 1
                                            ),
                                            "since_first_chunk_ms": round(
                                                since_first_chunk_ms, 1
                                            ),
                                            "supply_lead_ms": round(
                                                cumulative_audio_ms
                                                - since_first_chunk_ms,
                                                1,
                                            ),
                                            "pcm_queue_before": int(
                                                queue_before
                                            ),
                                            "pcm_queue_after": int(queue_after),
                                            "enqueue_wait_ms": round(
                                                enqueue_wait_ms, 1
                                            ),
                                        }
                                    )
                            else:
                                first_pcm_at = None
                        else:
                            first_pcm_at = None

                        self._update_state(
                            last_private_voice_ttd_first_chunk_ms=self._ttd_elapsed_ms(
                                item, first_chunk_at
                            ),
                            last_private_voice_ttd_first_pcm_ms=(
                                None
                                if first_pcm_at is None
                                else self._ttd_elapsed_ms(item, first_pcm_at)
                            ),
                            last_private_voice_ttd_audio_chunks=audio_chunks,
                            last_private_voice_ttd_received_bytes=received_bytes,
                            last_private_voice_ttd_max_network_gap_ms=round(
                                max_gap_ms, 1
                            ),
                            last_private_voice_ttd_status="receiving_audio",
                            last_private_voice_ttd_reason="pcm24_direct",
                        )

                    if message.get("is_final"):
                        if remainder:
                            raise ValueError("ttd_partial_pcm_sample")
                        final_at = time.perf_counter()
                        _shared_update(
                            final_at=final_at,
                            transport_state="eof",
                            provider_final=True,
                        )
                        self._update_state(
                            last_private_voice_ttd_final_ms=self._ttd_elapsed_ms(
                                item, final_at
                            )
                        )
                        provider_eof.set()
                        break
            finally:
                if sender_task is not None and not sender_task.done():
                    sender_task.cancel()
                if sender_task is not None:
                    try:
                        await sender_task
                    except asyncio.CancelledError:
                        pass

    def _tts_ttd_completion(self, item):
        started_at = time.perf_counter()
        sample_rate = _pcm_sample_rate(PRIVATE_VOICE_TTD_OUTPUT_FORMAT)
        if sample_rate <= 0:
            raise ValueError("ttd_output_format_invalid")
        try:
            initial_source = item.text_queue.get(timeout=1.0)
        except queue.Empty as exc:
            raise TimeoutError("ttd_initial_text_timeout") from exc
        if not initial_source:
            raise ValueError("ttd_initial_text_missing")
        eof_payload = None
        if PRIVATE_VOICE_TTD_INPUT_MODE == "eof_single":
            eof_payload = self._wait_ttd_final_payload(item)
            initial_source = eof_payload.full_text

        pcm_queue = queue.Queue(maxsize=VOICE_HTTP_STREAM_PCM_QUEUE_CHUNKS)
        provider_eof = threading.Event()
        playback_done = threading.Event()
        shared_lock = threading.Lock()
        shared = {
            "transport_state": "connecting",
            "provider_final": False,
            "playback_state": "buffering",
            "playback_result": None,
            "connected_at": None,
            "first_text_sent_at": None,
            "llm_completed_at": None,
            "first_chunk_at": None,
            "first_pcm_at": None,
            "first_audio_at": None,
            "final_at": None,
            "last_network_at": started_at,
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
            "last_client_send_at": started_at,
        }

        def _on_state(state):
            now = time.perf_counter()
            with shared_lock:
                shared["playback_state"] = str(state)
                if state == "playing" and shared["first_audio_at"] is None:
                    shared["first_audio_at"] = now
                first_audio_at = shared["first_audio_at"]
            if first_audio_at is not None:
                self._update_state(
                    last_private_voice_ttd_first_audio_ms=self._ttd_elapsed_ms(
                        item, first_audio_at
                    )
                )

        def _on_done(result):
            with shared_lock:
                shared["playback_result"] = result
            playback_done.set()

        self._record_audio_completion(
            self._audio_completion_result(
                state="created",
                segments=[str(initial_source)],
                fetched_segments=0,
                played_segments=0,
                original_chars=len(str(initial_source)),
                played_duration_ms=0.0,
            )
        )
        self._update_state(
            last_private_voice_ttd_status="connecting",
            last_private_voice_ttd_reason=(
                "ttd_eof_single_pcm"
                if PRIVATE_VOICE_TTD_INPUT_MODE == "eof_single"
                else "ttd_websocket_pcm"
            ),
            last_private_voice_ttd_error=None,
            last_private_voice_ttd_provider_expected_chars=0,
            last_private_voice_ttd_provider_sent_chars=0,
            last_private_voice_ttd_provider_integrity_ok=False,
            last_private_voice_ttd_provider_mismatch_index=None,
            last_private_voice_ttd_provider_missing_chars=0,
            last_private_voice_ttd_provider_duplicate_chars=0,
            last_private_voice_ttd_capture_path=None,
            last_private_voice_ttd_capture_report_path=None,
            last_private_voice_ttd_capture_bytes=0,
            last_private_voice_ttd_silence_spans=0,
            last_private_voice_ttd_silence_total_ms=0.0,
            last_private_voice_ttd_longest_silence_ms=0.0,
            last_streaming_action="ttd_websocket_pilot",
            last_streaming_reason="private_incremental_voice",
            last_stream_transport_state="connecting",
            last_stream_playback_state="buffering",
            last_stream_time_to_first_byte_ms=None,
            last_stream_time_to_first_pcm_ms=None,
            last_stream_time_to_first_audio_ms=None,
            last_stream_received_bytes=0,
            last_stream_abort_reason="none",
            last_audio_playback_watchdog="stream_stall",
            last_audio_playback_timeout_ms=float(PRIVATE_VOICE_TTD_TIMEOUT_S * 1000),
        )

        self.lipsync.play_pcm_stream_nonblocking(
            pcm_queue,
            provider_eof,
            samplerate=sample_rate,
            startup_buffer_ms=PRIVATE_VOICE_TTD_START_BUFFER_MS,
            stall_timeout_s=PRIVATE_VOICE_TTD_TIMEOUT_S,
            on_state=_on_state,
            on_done=_on_done,
            output_stream_factory=getattr(self, "_stream_output_stream_factory", None),
            callback_output=True,
        )

        transport_error = None
        try:
            asyncio.run(
                self._run_ttd_websocket(
                    item,
                    initial_source=str(initial_source),
                    pcm_queue=pcm_queue,
                    provider_eof=provider_eof,
                    shared=shared,
                    shared_lock=shared_lock,
                    eof_payload=eof_payload,
                )
            )
        except Exception as exc:
            transport_error = exc
            with shared_lock:
                shared["error"] = f"{type(exc).__name__}: {exc}"
                shared["transport_state"] = (
                    "cancelled"
                    if item.cancel_event.is_set() or self._shutdown_requested()
                    else "error"
                )
            self._update_state(
                last_private_voice_ttd_error=f"{type(exc).__name__}: {exc}",
                last_private_voice_ttd_reason=f"{type(exc).__name__}: {exc}",
            )
        finally:
            provider_eof.set()

        playback_deadline = time.perf_counter() + float(
            VOICE_PLAYBACK_FALLBACK_TIMEOUT_S
        )
        while not playback_done.wait(timeout=0.05):
            if self._shutdown_requested() or time.perf_counter() >= playback_deadline:
                self.lipsync.stop()
                break
        if not playback_done.is_set():
            playback_done.wait(timeout=2.0)

        with shared_lock:
            playback_result = shared["playback_result"]
            transport_state = shared["transport_state"]
            provider_final = bool(shared["provider_final"])
            playback_state = shared["playback_state"]
            connected_at = shared["connected_at"]
            first_text_sent_at = shared["first_text_sent_at"]
            first_chunk_at = shared["first_chunk_at"]
            first_pcm_at = shared["first_pcm_at"]
            first_audio_at = shared["first_audio_at"]
            max_network_gap_ms = shared["max_network_gap_ms"]
            source_text = "".join(shared["source_chunks"])
            source_chars = shared["source_chars"]
            prepared_chars = shared["prepared_chars"]
            sent_chunks = shared["sent_chunks"]
            audio_chunks = shared["audio_chunks"]
            received_bytes = shared["received_bytes"]
            final_payload = shared["final_payload"]
            provider_sent_text = shared["provider_sent_text"]
            provider_expected_text = shared["provider_expected_text"]
            provider_integrity_ok = bool(shared["provider_integrity_ok"])
            provider_mismatch_index = shared["provider_mismatch_index"]
            provider_missing_chars = int(shared["provider_missing_chars"] or 0)
            provider_duplicate_chars = int(
                shared["provider_duplicate_chars"] or 0
            )
            arrival_timeline = list(shared["arrival_timeline"])
            text_timeline = list(shared["text_timeline"])
            capture_pcm = bytes(shared["capture_pcm"])
            error_text = shared["error"]

        if final_payload is None and item.final_future.done():
            try:
                final_payload = self._wait_ttd_final_payload(item)
            except Exception:
                final_payload = None
        full_text = (
            final_payload.full_text
            if isinstance(final_payload, TtdFinalPayload)
            else source_text
        )
        missing_chars = max(0, len(full_text) - len(source_text))
        duplicate_chars = max(0, len(source_text) - len(full_text))
        exact_text = bool(source_text == full_text and full_text)
        capture_path = None
        capture_report_path = None
        silence_summary = _pcm_silence_summary(b"", sample_rate)
        try:
            (
                capture_path,
                capture_report_path,
                silence_summary,
            ) = self._write_ttd_capture(
                item,
                pcm_bytes=capture_pcm,
                sample_rate=sample_rate,
                arrival_timeline=arrival_timeline,
                text_timeline=text_timeline,
                source_text=full_text,
                provider_sent_text=provider_sent_text,
                provider_expected_text=provider_expected_text,
            )
        except Exception as exc:
            log_event(
                "voice",
                f"Private TTD capture write failed: {type(exc).__name__}: {exc}",
            )
        playback_completed = bool(
            playback_result is not None and playback_result.completed
        )
        completed = bool(
            transport_error is None
            and provider_final
            and transport_state == "eof"
            and playback_completed
            and exact_text
            and provider_integrity_ok
        )

        if transport_error is not None and first_audio_at is None:
            try:
                payload = self._wait_ttd_final_payload(item)
            except Exception:
                payload = None
            if payload is not None and not item.cancel_event.is_set():
                self._increment_state("private_voice_ttd_fallback_total")
                self._update_state(
                    last_private_voice_ttd_status="fallback_full_before_audio",
                    last_private_voice_ttd_reason=error_text or "ttd_failed_before_audio",
                    last_private_voice_ttd_source_chars=len(payload.full_text),
                    last_private_voice_ttd_missing_chars=0,
                    last_private_voice_ttd_duplicate_chars=0,
                    last_private_voice_ttd_replayed_chars=0,
                )
                return self._tts_and_lipsync(
                    payload.full_text,
                    voice_mode=item.voice_mode,
                )

        if completed:
            final_state = "completed"
            abort_reason = "none"
            self._increment_state("private_voice_ttd_completed_total")
            ttd_status = "completed"
            ttd_reason = (
                "eof_single_pcm_drained"
                if PRIVATE_VOICE_TTD_INPUT_MODE == "eof_single"
                else "incremental_pcm_drained"
            )
        elif item.cancel_event.is_set() or self._shutdown_requested():
            final_state = "cancelled"
            abort_reason = "ttd_cancelled"
            self._increment_state("private_voice_ttd_failed_total")
            ttd_status = "cancelled"
            ttd_reason = abort_reason
        else:
            final_state = "provider_error" if transport_error else "aborted"
            abort_reason = error_text or getattr(
                playback_result, "abort_reason", "incomplete_ttd_stream"
            )
            self._increment_state("private_voice_ttd_failed_total")
            ttd_status = "failed_after_audio" if first_audio_at is not None else "failed"
            ttd_reason = str(abort_reason)

        played_duration_ms = float(
            getattr(playback_result, "played_duration_ms", 0.0) or 0.0
        )
        start_buffer_ms = float(
            getattr(playback_result, "start_buffer_ms", 0.0) or 0.0
        )
        total_ms = (time.perf_counter() - started_at) * 1000.0
        self._update_state(
            last_private_voice_ttd_status=ttd_status,
            last_private_voice_ttd_reason=ttd_reason,
            last_private_voice_ttd_source_chars=source_chars,
            last_private_voice_ttd_prepared_chars=prepared_chars,
            last_private_voice_ttd_provider_expected_chars=len(
                provider_expected_text
            ),
            last_private_voice_ttd_provider_sent_chars=len(provider_sent_text),
            last_private_voice_ttd_provider_integrity_ok=provider_integrity_ok,
            last_private_voice_ttd_provider_mismatch_index=(
                provider_mismatch_index
            ),
            last_private_voice_ttd_provider_missing_chars=(
                provider_missing_chars
            ),
            last_private_voice_ttd_provider_duplicate_chars=(
                provider_duplicate_chars
            ),
            last_private_voice_ttd_sent_chunks=sent_chunks,
            last_private_voice_ttd_audio_chunks=audio_chunks,
            last_private_voice_ttd_received_bytes=received_bytes,
            last_private_voice_ttd_missing_chars=missing_chars,
            last_private_voice_ttd_duplicate_chars=duplicate_chars,
            last_private_voice_ttd_replayed_chars=0,
            last_private_voice_ttd_connected_ms=(
                None if connected_at is None else self._ttd_elapsed_ms(item, connected_at)
            ),
            last_private_voice_ttd_first_text_sent_ms=(
                None
                if first_text_sent_at is None
                else self._ttd_elapsed_ms(item, first_text_sent_at)
            ),
            last_private_voice_ttd_first_chunk_ms=(
                None if first_chunk_at is None else self._ttd_elapsed_ms(item, first_chunk_at)
            ),
            last_private_voice_ttd_first_pcm_ms=(
                None if first_pcm_at is None else self._ttd_elapsed_ms(item, first_pcm_at)
            ),
            last_private_voice_ttd_first_audio_ms=(
                None if first_audio_at is None else self._ttd_elapsed_ms(item, first_audio_at)
            ),
            last_private_voice_ttd_max_network_gap_ms=round(max_network_gap_ms, 1),
            last_private_voice_ttd_capture_path=capture_path,
            last_private_voice_ttd_capture_report_path=capture_report_path,
            last_private_voice_ttd_capture_bytes=len(capture_pcm),
            last_private_voice_ttd_silence_spans=int(
                silence_summary.get("span_count", 0) or 0
            ),
            last_private_voice_ttd_silence_total_ms=float(
                silence_summary.get("total_silence_ms", 0.0) or 0.0
            ),
            last_private_voice_ttd_longest_silence_ms=float(
                silence_summary.get("longest_silence_ms", 0.0) or 0.0
            ),
            last_stream_transport_state=transport_state,
            last_stream_playback_state=("done" if completed else playback_state),
            last_stream_time_to_first_byte_ms=(
                None
                if first_chunk_at is None
                else round((first_chunk_at - started_at) * 1000.0, 1)
            ),
            last_stream_time_to_first_pcm_ms=(
                None
                if first_pcm_at is None
                else round((first_pcm_at - started_at) * 1000.0, 1)
            ),
            last_stream_time_to_first_audio_ms=(
                None
                if first_audio_at is None
                else round((first_audio_at - started_at) * 1000.0, 1)
            ),
            last_stream_received_bytes=received_bytes,
            last_stream_start_buffer_ms=round(start_buffer_ms, 1),
            last_stream_rebuffer_count=int(
                getattr(playback_result, "rebuffer_count", 0) or 0
            ),
            last_stream_rebuffer_total_ms=round(
                float(getattr(playback_result, "rebuffer_total_ms", 0.0) or 0.0),
                1,
            ),
            last_stream_output_underflow_count=int(
                getattr(playback_result, "output_underflow_count", 0) or 0
            ),
            last_stream_callback_calls=int(
                getattr(playback_result, "callback_calls", 0) or 0
            ),
            last_stream_callback_status_underflows=int(
                getattr(playback_result, "callback_status_underflows", 0) or 0
            ),
            last_stream_ring_starvation_count=int(
                getattr(playback_result, "ring_starvation_count", 0) or 0
            ),
            last_stream_ring_low_watermark_ms=getattr(
                playback_result, "ring_low_watermark_ms", None
            ),
            last_stream_max_callback_lateness_ms=round(
                float(getattr(playback_result, "max_callback_lateness_ms", 0.0) or 0.0),
                1,
            ),
            last_stream_feeder_refill_count=int(
                getattr(playback_result, "feeder_refill_count", 0) or 0
            ),
            last_stream_feeder_done=bool(
                getattr(playback_result, "feeder_done", False)
            ),
            last_stream_callback_finished=bool(
                getattr(playback_result, "callback_finished", False)
            ),
            last_stream_max_network_gap_ms=round(max_network_gap_ms, 1),
            last_stream_provider_eof=provider_final,
            last_stream_decoder_eof=provider_final,
            last_stream_abort_reason=str(abort_reason),
            last_stream_ffmpeg_ready=False,
            last_tts_prepare_ms=(
                None
                if first_audio_at is None
                else round((first_audio_at - started_at) * 1000.0, 1)
            ),
            last_tts_playback_ms=round(played_duration_ms, 1),
            last_tts_total_ms=round(total_ms, 1),
            last_tts_prebuffer_chunks=1 if start_buffer_ms > 0 else 0,
            last_tts_prebuffer_ms=round(start_buffer_ms, 1),
            last_tts_seam_wait_ms=None,
            last_tts_max_seam_wait_ms=0.0,
            last_tts_avg_seam_wait_ms=None,
            last_tts_seam_grade="none",
            last_tts_playback_policy="ttd_pcm_callback",
            last_tts_seam_cause="none",
            last_tts_strategy=(
                "private_ttd_eof_single_pcm"
                if PRIVATE_VOICE_TTD_INPUT_MODE == "eof_single"
                else "private_ttd_websocket_pcm"
            ),
            last_tts_audio_paths=1 if completed else 0,
            last_tts_text_len=len(full_text),
            last_tts_chunks=sent_chunks,
            last_tts_voice_mode=item.voice_mode,
            last_tts_chunk_max_chars=PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS,
            last_tts_request_ms=(
                None
                if first_chunk_at is None
                else round((first_chunk_at - started_at) * 1000.0, 1)
            ),
        )
        result = self._audio_completion_result(
            state=final_state,
            segments=[full_text or str(initial_source)],
            fetched_segments=1 if received_bytes > 0 else 0,
            played_segments=1 if completed else 0,
            original_chars=len(full_text),
            played_duration_ms=played_duration_ms,
            abort_reason=str(abort_reason),
        )
        return self._record_audio_completion(result)

    @staticmethod
    def _overlap_elapsed_ms(item, at=None):
        point = time.perf_counter() if at is None else float(at)
        return round(max(0.0, point - item.turn_started_at) * 1000.0, 1)

    def _wait_overlap_tail_payload(self, item):
        if item.cancel_event.is_set() or self._shutdown_requested():
            raise RuntimeError("overlap_cancelled")
        try:
            payload = item.tail_future.result(
                timeout=float(PRIVATE_VOICE_OVERLAP_TAIL_TIMEOUT_S)
            )
        except FutureTimeoutError as exc:
            raise TimeoutError("overlap_tail_timeout") from exc
        if item.cancel_event.is_set() or self._shutdown_requested():
            raise RuntimeError("overlap_cancelled")
        if not isinstance(payload, OverlapTailPayload):
            raise TypeError("overlap_tail_payload_invalid")
        if int(payload.split_offset) != int(item.split_offset):
            raise ValueError("overlap_split_offset_mismatch")
        if payload.full_text[: item.split_offset] != item.lead_text:
            raise ValueError("overlap_lead_prefix_mismatch")
        if payload.full_text[item.split_offset :] != payload.tail_text:
            raise ValueError("overlap_tail_suffix_mismatch")
        if item.lead_text + payload.tail_text != payload.full_text:
            raise ValueError("overlap_exact_join_failed")
        return payload

    def _play_overlap_audio(self, item, audio, *, role):
        prepared = self.lipsync.prepare_audio_bytes(audio)
        if prepared is None:
            return False, 0.0, None, "decode_error"
        done = threading.Event()
        playback_success = {"value": False}

        def _on_done(success):
            playback_success["value"] = bool(success)
            done.set()

        try:
            decoded_duration_s = float(prepared.duration_seconds)
        except (AttributeError, TypeError, ValueError):
            decoded_duration_s = 0.0
        duration_known = math.isfinite(decoded_duration_s) and decoded_duration_s > 0.0
        timeout_s = (
            decoded_duration_s + VOICE_PLAYBACK_GRACE_S
            if duration_known
            else float(VOICE_PLAYBACK_FALLBACK_TIMEOUT_S)
        )
        timeout_s = max(1.0, timeout_s)
        play_started_at = time.perf_counter()
        if role == "lead":
            self._update_state(
                last_private_voice_overlap_first_audio_ms=self._overlap_elapsed_ms(
                    item, play_started_at
                ),
                last_private_voice_overlap_lead_play_count=1,
                last_private_voice_overlap_status="lead_playing",
                last_private_voice_overlap_reason="lead_audio_started",
            )
        self.lipsync.play_prepared_audio_nonblocking(prepared, on_done=_on_done)
        deadline = time.perf_counter() + timeout_s
        while not done.is_set():
            if item.cancel_event.is_set() or self._shutdown_requested():
                self.lipsync.stop()
                return False, 0.0, time.perf_counter(), "cancelled"
            remaining = deadline - time.perf_counter()
            if remaining <= 0.0:
                self.lipsync.stop()
                return False, 0.0, time.perf_counter(), "playback_timeout"
            done.wait(timeout=min(0.1, remaining))
        ended_at = time.perf_counter()
        elapsed_ms = (ended_at - play_started_at) * 1000.0
        duration_ms = decoded_duration_s * 1000.0 if duration_known else elapsed_ms
        if not playback_success["value"]:
            return False, duration_ms, ended_at, "playback_error"
        return True, duration_ms, ended_at, "none"

    def _tts_overlap_streaming_lead_completion(self, item, process):
        """Stream the lead while provider-prefetching ordered tail packets."""

        started_at = time.perf_counter()
        lead_text = str(item.lead_text or "")
        lead_profile = VOICE_TONE_PROFILES.get(
            _detect_tone_from_text(lead_text),
            VOICE_TONE_PROFILES["default"],
        )
        tail_condition = threading.Condition()
        tail_fetch_slots = threading.BoundedSemaphore(
            max(1, int(ELEVENLABS_MAX_CONCURRENT) - 1)
        )
        tail_state = {
            "payload": None,
            "segments": [],
            "audio": {},
            "errors": {},
            "retry_count": 0,
            "fetch_started_at": None,
            "fetch_completed_at": None,
            "done": False,
            "coordinator_error": None,
        }

        def _tail_coordinator():
            try:
                payload = self._wait_overlap_tail_payload(item)
                tail_segments = self._split_tts_text(
                    payload.tail_text,
                    max_chars=PRIVATE_VOICE_OVERLAP_MAX_CHARS,
                    force_chunking=True,
                )
                with tail_condition:
                    tail_state["payload"] = payload
                    tail_state["segments"] = tail_segments
                    tail_condition.notify_all()
                if not tail_segments:
                    return
                fetch_started_at = time.perf_counter()
                with tail_condition:
                    tail_state["fetch_started_at"] = fetch_started_at
                self._update_state(
                    last_private_voice_overlap_tail_fetch_started_ms=self._overlap_elapsed_ms(
                        item, fetch_started_at
                    ),
                    last_private_voice_overlap_status="tail_fetching",
                    last_private_voice_overlap_reason="streaming_lead_tail_prefetch",
                )

                def _fetch_tail_segment(index, text):
                    audio = None
                    for attempt in range(2):
                        if item.cancel_event.is_set() or self._shutdown_requested():
                            break
                        profile = VOICE_TONE_PROFILES.get(
                            _detect_tone_from_text(text),
                            VOICE_TONE_PROFILES["default"],
                        )
                        try:
                            with tail_fetch_slots:
                                audio = self._tts_fetch_audio(text, profile)
                        except Exception as exc:
                            log_event(
                                "voice",
                                f"Private overlap streamed-tail segment {index} raised: {exc}",
                            )
                            audio = None
                        if audio:
                            break
                        if attempt == 0:
                            with tail_condition:
                                tail_state["retry_count"] += 1
                    with tail_condition:
                        if audio:
                            tail_state["audio"][index] = audio
                        else:
                            tail_state["errors"][index] = "tail_fetch_failed"
                        tail_condition.notify_all()

                fetch_threads = [
                    threading.Thread(
                        target=_fetch_tail_segment,
                        args=(index, text),
                        daemon=True,
                        name=f"tts-overlap-stream-tail-{index}",
                    )
                    for index, text in enumerate(tail_segments)
                ]
                for thread in fetch_threads:
                    thread.start()
                for thread in fetch_threads:
                    thread.join()
                with tail_condition:
                    tail_state["fetch_completed_at"] = time.perf_counter()
                    tail_condition.notify_all()
            except Exception as exc:
                with tail_condition:
                    tail_state["coordinator_error"] = (
                        f"{type(exc).__name__}: {exc}"
                    )
                    tail_condition.notify_all()
            finally:
                with tail_condition:
                    tail_state["done"] = True
                    tail_condition.notify_all()

        self._update_state(
            last_private_voice_overlap_status="lead_streaming",
            last_private_voice_overlap_reason="http_stream_lead",
            last_private_voice_overlap_lead_play_count=0,
            last_private_voice_overlap_tail_play_count=0,
            last_private_voice_overlap_tail_segment_count=0,
            last_private_voice_overlap_tail_retry_count=0,
            last_private_voice_overlap_llm_completed_ms=None,
            last_private_voice_overlap_lead_fetch_started_ms=None,
            last_private_voice_overlap_lead_fetch_completed_ms=None,
            last_private_voice_overlap_tail_fetch_started_ms=None,
            last_private_voice_overlap_tail_fetch_completed_ms=None,
            last_private_voice_overlap_first_audio_ms=None,
            last_private_voice_overlap_lead_playback_ended_ms=None,
            last_private_voice_overlap_seam_wait_ms=None,
            last_private_voice_overlap_missing_chars=0,
            last_private_voice_overlap_duplicate_chars=0,
            last_private_voice_overlap_true_overlap=False,
        )
        tail_thread = threading.Thread(
            target=_tail_coordinator,
            daemon=True,
            name=f"tts-overlap-stream-coordinator-{item.turn_id[:8]}",
        )
        tail_thread.start()

        lead_stream_started_at = time.perf_counter()
        self._update_state(
            last_private_voice_overlap_lead_fetch_started_ms=self._overlap_elapsed_ms(
                item, lead_stream_started_at
            )
        )
        lead_result = self._tts_http_streaming_completion(
            lead_text,
            lead_profile,
            item.voice_mode,
            process,
            original_chars=len(lead_text),
        )
        lead_ended_at = time.perf_counter()
        with self.state_lock:
            stream_first_byte_ms = self.state.get(
                "last_stream_time_to_first_byte_ms"
            )
            stream_first_audio_ms = self.state.get(
                "last_stream_time_to_first_audio_ms"
            )
        lead_start_turn_ms = self._overlap_elapsed_ms(
            item, lead_stream_started_at
        )
        actual_first_audio_ms = (
            None
            if stream_first_audio_ms is None
            else round(lead_start_turn_ms + float(stream_first_audio_ms), 1)
        )
        actual_first_byte_ms = (
            None
            if stream_first_byte_ms is None
            else round(lead_start_turn_ms + float(stream_first_byte_ms), 1)
        )
        self._update_state(
            last_private_voice_overlap_lead_fetch_completed_ms=actual_first_byte_ms,
            last_private_voice_overlap_first_audio_ms=actual_first_audio_ms,
            last_private_voice_overlap_lead_playback_ended_ms=self._overlap_elapsed_ms(
                item, lead_ended_at
            ),
            last_private_voice_overlap_lead_play_count=(
                1 if lead_result.audio_completed else 0
            ),
        )
        if not lead_result.audio_completed:
            item.cancel_event.set()
            self._increment_state("private_voice_overlap_failed_total")
            self._update_state(
                last_private_voice_overlap_status="failed_before_tail",
                last_private_voice_overlap_reason=(
                    lead_result.abort_reason or "lead_stream_failed"
                ),
            )
            return lead_result

        wait_deadline = time.perf_counter() + float(
            PRIVATE_VOICE_OVERLAP_TAIL_TIMEOUT_S + VOICE_FETCH_STALL_TIMEOUT_S
        )
        with tail_condition:
            while (
                tail_state["payload"] is None
                and tail_state["coordinator_error"] is None
                and not tail_state["done"]
            ):
                remaining = wait_deadline - time.perf_counter()
                if remaining <= 0.0:
                    break
                tail_condition.wait(timeout=min(0.1, remaining))
            payload = tail_state["payload"]
            coordinator_error = tail_state["coordinator_error"]
            tail_segments = list(tail_state["segments"])

        if payload is None:
            reason = coordinator_error or "tail_payload_timeout"
            self._increment_state("private_voice_overlap_failed_total")
            self._update_state(
                last_private_voice_overlap_status="failed_after_lead",
                last_private_voice_overlap_reason=reason,
            )
            result = self._audio_completion_result(
                state="provider_error",
                segments=[lead_text],
                fetched_segments=1,
                played_segments=1,
                original_chars=len(lead_text),
                played_duration_ms=lead_result.played_duration_ms,
                abort_reason="tail_payload_failed",
            )
            return self._record_audio_completion(result)

        full_text = payload.full_text
        tail_text = payload.tail_text
        segments = [lead_text, *tail_segments]
        self._update_state(
            last_private_voice_overlap_full_chars=len(full_text),
            last_private_voice_overlap_tail_chars=len(tail_text),
            last_private_voice_overlap_tail_segment_count=len(tail_segments),
            last_private_voice_overlap_llm_completed_ms=self._overlap_elapsed_ms(
                item, payload.llm_completed_at
            ),
        )

        played_segments = 1
        played_duration_ms = float(lead_result.played_duration_ms or 0.0)
        seam_wait_ms = None
        tail_error = None
        for index, _tail_text in enumerate(tail_segments):
            with tail_condition:
                while (
                    index not in tail_state["audio"]
                    and index not in tail_state["errors"]
                    and tail_state["coordinator_error"] is None
                ):
                    remaining = wait_deadline - time.perf_counter()
                    if remaining <= 0.0:
                        break
                    tail_condition.wait(timeout=min(0.1, remaining))
                audio = tail_state["audio"].get(index)
                segment_error = tail_state["errors"].get(index)
                coordinator_error = tail_state["coordinator_error"]
            if not audio:
                tail_error = (
                    segment_error
                    or coordinator_error
                    or "tail_segment_timeout"
                )
                break
            if index == 0:
                seam_wait_ms = max(
                    0.0,
                    (time.perf_counter() - lead_ended_at) * 1000.0,
                )
                self._update_state(
                    last_private_voice_overlap_seam_wait_ms=round(
                        seam_wait_ms, 1
                    )
                )
            tail_ok, duration_ms, _ended_at, playback_error = (
                self._play_overlap_audio(
                    item,
                    audio,
                    role="tail",
                )
            )
            if not tail_ok:
                tail_error = playback_error
                break
            played_segments += 1
            played_duration_ms += duration_ms
            self._update_state(last_private_voice_overlap_tail_play_count=1)

        with tail_condition:
            retry_count = int(tail_state["retry_count"])
            tail_fetch_started_at = tail_state["fetch_started_at"]
            tail_fetch_completed_at = tail_state["fetch_completed_at"]
            fetched_tail_count = len(tail_state["audio"])
        true_overlap = bool(
            tail_fetch_started_at is not None
            and tail_fetch_started_at < lead_ended_at
        )
        self._update_state(
            last_private_voice_overlap_tail_retry_count=retry_count,
            last_private_voice_overlap_tail_fetch_completed_ms=(
                None
                if tail_fetch_completed_at is None
                else self._overlap_elapsed_ms(item, tail_fetch_completed_at)
            ),
            last_private_voice_overlap_true_overlap=true_overlap,
            last_private_voice_overlap_missing_chars=0,
            last_private_voice_overlap_duplicate_chars=0,
        )
        if tail_error:
            self._increment_state("private_voice_overlap_failed_total")
            self._update_state(
                last_private_voice_overlap_status="failed_after_lead",
                last_private_voice_overlap_reason=tail_error,
            )
            result = self._audio_completion_result(
                state="provider_error",
                segments=segments,
                fetched_segments=1 + fetched_tail_count,
                played_segments=played_segments,
                original_chars=len(full_text),
                played_duration_ms=played_duration_ms,
                abort_reason=tail_error,
            )
            return self._record_audio_completion(result)

        self._increment_state("private_voice_overlap_completed_total")
        self._update_state(
            last_private_voice_overlap_status="completed",
            last_private_voice_overlap_reason="streamed_lead_tail_drained",
            last_tts_prepare_ms=round(
                max(
                    0.0,
                    (time.perf_counter() - started_at) * 1000.0
                    - played_duration_ms,
                ),
                1,
            ),
            last_tts_playback_ms=round(played_duration_ms, 1),
            last_tts_total_ms=round(
                (time.perf_counter() - started_at) * 1000.0, 1
            ),
            last_tts_prebuffer_chunks=1,
            last_tts_prebuffer_ms=float(
                stream_first_audio_ms or 0.0
            ),
            last_tts_seam_wait_ms=(
                None if seam_wait_ms is None else round(seam_wait_ms, 1)
            ),
            last_tts_max_seam_wait_ms=(
                None if seam_wait_ms is None else round(seam_wait_ms, 1)
            ),
            last_tts_avg_seam_wait_ms=(
                None if seam_wait_ms is None else round(seam_wait_ms, 1)
            ),
            last_tts_seam_grade=self._seam_grade(seam_wait_ms),
            last_tts_playback_policy="one_streamed_lead_tail",
            last_tts_seam_cause=(
                "none"
                if seam_wait_ms is None
                or seam_wait_ms <= VOICE_FULL_BATCH_SEAM_BAD_MS
                else "tail_fetch"
            ),
            last_tts_strategy="private_voice_overlap_streaming_lead",
            last_tts_audio_paths=played_segments,
            last_tts_text_len=len(full_text),
            last_tts_chunks=len(segments),
            last_tts_voice_mode=item.voice_mode,
            last_tts_chunk_max_chars=PRIVATE_VOICE_OVERLAP_MAX_CHARS,
        )
        result = self._audio_completion_result(
            state="completed",
            segments=segments,
            fetched_segments=len(segments),
            played_segments=played_segments,
            original_chars=len(full_text),
            played_duration_ms=played_duration_ms,
        )
        return self._record_audio_completion(result)

    def _private_overlap_pcm_route_available(self):
        return bool(
            PRIVATE_VOICE_OVERLAP_ENABLED
            and PRIVATE_VOICE_OVERLAP_PCM_ENABLED
            and VOICE_STREAMING_ENABLED
            and not VOICE_STREAMING_KILL_SWITCH
            and VOICE_STREAM_CALLBACK_OUTPUT_ENABLED
            and _pcm_sample_rate(PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT) > 0
        )

    def _open_overlap_pcm_stream_response(self, text, profile):
        """Open one HTTP raw-PCM response while holding one provider slot."""

        semaphore = getattr(self, "_tts_semaphore", None)
        acquired = False
        if semaphore is not None:
            acquired = semaphore.acquire(
                timeout=float(PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S)
            )
            if not acquired:
                raise TimeoutError("overlap_pcm_provider_slot_timeout")
        request_started_at = time.perf_counter()
        try:
            response = self._voice_http_post(
                self._streaming_tts_url(),
                params={
                    "output_format": PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT
                },
                json={
                    "text": self._prepare_tts_request_text(text),
                    "model_id": self._pick_tts_model(text),
                    "voice_settings": profile,
                },
                headers={
                    "xi-api-key": ELEVEN_API_KEY,
                    "Content-Type": "application/json",
                    "Accept": "application/octet-stream",
                },
                stream=True,
                timeout=(
                    VOICE_HTTP_STREAM_CONNECT_TIMEOUT_S,
                    PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S,
                ),
            )
            if int(getattr(response, "status_code", 0) or 0) != 200:
                status = int(getattr(response, "status_code", 0) or 0)
                body = str(getattr(response, "text", "") or "")[:300]
                response.close()
                raise RuntimeError(
                    f"overlap_pcm_provider_http_{status or 'error'}: {body}"
                )
            return response, acquired, request_started_at
        except Exception:
            if acquired and semaphore is not None:
                semaphore.release()
            raise

    def _drain_overlap_pcm_response(
        self,
        opened,
        *,
        sink,
        stop_event,
        metrics,
    ):
        """Decode a raw PCM16 HTTP body into float32 chunks for one sink."""

        response, acquired, request_started_at = opened
        semaphore = getattr(self, "_tts_semaphore", None)
        remainder = b""
        yielded = False
        last_payload_at = None
        try:
            for payload in response.iter_content(
                chunk_size=PRIVATE_VOICE_OVERLAP_PCM_NETWORK_CHUNK_BYTES
            ):
                if (
                    stop_event.is_set()
                    or self._shutdown_requested()
                ):
                    raise RuntimeError("overlap_pcm_cancelled")
                if not payload:
                    continue
                now = time.perf_counter()
                if metrics.get("first_byte_at") is None:
                    metrics["first_byte_at"] = now
                elif last_payload_at is not None:
                    metrics["max_gap_ms"] = max(
                        float(metrics.get("max_gap_ms", 0.0)),
                        (now - last_payload_at) * 1000.0,
                    )
                last_payload_at = now
                metrics["chunks"] = int(metrics.get("chunks", 0)) + 1
                metrics["bytes"] = int(metrics.get("bytes", 0)) + len(payload)

                data = remainder + bytes(payload)
                aligned = len(data) - (len(data) % 2)
                remainder = data[aligned:]
                if not aligned:
                    continue
                pcm = (
                    np.frombuffer(data[:aligned], dtype="<i2")
                    .astype(np.float32)
                    / 32768.0
                )
                sink(pcm)
                yielded = True
            if remainder:
                raise RuntimeError("overlap_pcm_partial_sample")
            if not yielded:
                raise RuntimeError("overlap_pcm_empty_response")
            metrics["eof_at"] = time.perf_counter()
            return metrics
        finally:
            try:
                response.close()
            finally:
                if acquired and semaphore is not None:
                    semaphore.release()
                metrics["request_started_at"] = request_started_at

    def _tts_http_pcm_single_completion(
        self,
        text,
        profile,
        voice_mode,
        *,
        original_chars=None,
    ):
        """Stream one complete HTTP PCM response through one callback."""

        sample_rate = _pcm_sample_rate(PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT)
        if sample_rate <= 0:
            return None
        started_at = time.perf_counter()
        raw_text = str(text or "")
        original_chars = (
            len(raw_text)
            if original_chars is None
            else max(0, int(original_chars))
        )
        segments = [raw_text]
        pcm_queue = queue.Queue(maxsize=VOICE_HTTP_STREAM_PCM_QUEUE_CHUNKS)
        provider_eof = threading.Event()
        playback_done = threading.Event()
        route_stop = threading.Event()
        shared_lock = threading.Lock()
        shared = {
            "first_audio_at": None,
            "playback_state": "buffering",
            "playback_result": None,
        }
        metrics = {
            "first_byte_at": None,
            "eof_at": None,
            "chunks": 0,
            "bytes": 0,
            "max_gap_ms": 0.0,
        }

        def _put_pcm(pcm):
            while True:
                if route_stop.is_set() or self._shutdown_requested():
                    raise RuntimeError("http_pcm_single_cancelled")
                if playback_done.is_set():
                    raise RuntimeError("http_pcm_single_playback_stopped")
                try:
                    pcm_queue.put(pcm, timeout=0.05)
                    return
                except queue.Full:
                    continue

        def _on_state(state):
            now = time.perf_counter()
            with shared_lock:
                shared["playback_state"] = str(state)
                if state == "playing" and shared["first_audio_at"] is None:
                    shared["first_audio_at"] = now
                first_audio_at = shared["first_audio_at"]
            self._update_state(
                last_stream_playback_state=str(state),
                last_stream_time_to_first_audio_ms=(
                    None
                    if first_audio_at is None
                    else round((first_audio_at - started_at) * 1000.0, 1)
                ),
            )

        def _on_done(result):
            with shared_lock:
                shared["playback_result"] = result
            playback_done.set()

        self._reset_http_stream_telemetry(ffmpeg_ready=True)
        self._record_audio_completion(
            self._audio_completion_result(
                state="created",
                segments=segments,
                fetched_segments=0,
                played_segments=0,
                original_chars=original_chars,
                played_duration_ms=0.0,
            )
        )
        self._update_state(
            last_private_voice_overlap_pcm_status="single_streaming",
            last_private_voice_overlap_pcm_reason="http_pcm_single_request",
            last_private_voice_overlap_pcm_output_format=(
                PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT
            ),
            last_private_voice_overlap_pcm_lead_chunks=0,
            last_private_voice_overlap_pcm_tail_chunks=0,
            last_private_voice_overlap_pcm_lead_bytes=0,
            last_private_voice_overlap_pcm_tail_bytes=0,
            last_private_voice_overlap_pcm_lead_first_byte_ms=None,
            last_private_voice_overlap_pcm_tail_first_byte_ms=None,
            last_private_voice_overlap_pcm_lead_eof_ms=None,
            last_private_voice_overlap_pcm_tail_eof_ms=None,
            last_private_voice_overlap_pcm_tail_buffered_before_lead_eof=False,
            last_private_voice_overlap_pcm_fallback_used=False,
            last_private_voice_overlap_pcm_error=None,
            last_streaming_action="private_http_pcm_single_pilot",
            last_streaming_reason="short_full_response_pcm",
            last_stream_transport_state="connecting",
            last_stream_playback_state="buffering",
            last_stream_abort_reason="none",
            last_stream_ffmpeg_ready=False,
            last_audio_playback_watchdog="stream_stall",
            last_audio_playback_timeout_ms=float(
                PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S * 1000
            ),
        )
        self.lipsync.play_pcm_stream_nonblocking(
            pcm_queue,
            provider_eof,
            samplerate=sample_rate,
            startup_buffer_ms=PRIVATE_VOICE_OVERLAP_PCM_START_BUFFER_MS,
            stall_timeout_s=PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S,
            on_state=_on_state,
            on_done=_on_done,
            output_stream_factory=getattr(
                self, "_stream_output_stream_factory", None
            ),
            callback_output=True,
        )

        transport_error = None
        active_response = None
        try:
            opened = self._open_overlap_pcm_stream_response(raw_text, profile)
            active_response = opened[0]
            self._set_active_stream_handles(response=active_response)
            self._update_state(last_stream_transport_state="receiving")
            self._drain_overlap_pcm_response(
                opened,
                sink=_put_pcm,
                stop_event=route_stop,
                metrics=metrics,
            )
        except Exception as exc:
            transport_error = f"{type(exc).__name__}: {exc}"
        finally:
            if active_response is not None:
                self._clear_active_stream_handles(response=active_response)
            provider_eof.set()

        playback_deadline = time.perf_counter() + float(
            VOICE_PLAYBACK_FALLBACK_TIMEOUT_S
        )
        while not playback_done.wait(timeout=0.05):
            if self._shutdown_requested() or time.perf_counter() >= playback_deadline:
                route_stop.set()
                self.lipsync.stop()
                break
        if not playback_done.is_set():
            playback_done.wait(timeout=2.0)

        with shared_lock:
            first_audio_at = shared["first_audio_at"]
            playback_state = shared["playback_state"]
            playback_result = shared["playback_result"]
        playback_completed = bool(
            playback_result is not None and playback_result.completed
        )
        completed = bool(
            transport_error is None
            and metrics["eof_at"] is not None
            and playback_completed
        )
        if (
            not completed
            and first_audio_at is None
            and not self._shutdown_requested()
        ):
            self._increment_state("private_voice_overlap_pcm_fallback_total")
            self._update_state(
                last_private_voice_overlap_pcm_status="single_fallback_before_audio",
                last_private_voice_overlap_pcm_reason=(
                    transport_error
                    or getattr(
                        playback_result,
                        "abort_reason",
                        "single_pcm_failed_before_audio",
                    )
                ),
                last_private_voice_overlap_pcm_fallback_used=True,
                last_private_voice_overlap_pcm_error=transport_error,
            )
            return None

        first_byte_at = metrics["first_byte_at"]
        eof_at = metrics["eof_at"]
        played_duration_ms = float(
            getattr(playback_result, "played_duration_ms", 0.0) or 0.0
        )
        start_buffer_ms = float(
            getattr(playback_result, "start_buffer_ms", 0.0) or 0.0
        )
        rebuffer_count = int(
            getattr(playback_result, "rebuffer_count", 0) or 0
        )
        rebuffer_total_ms = float(
            getattr(playback_result, "rebuffer_total_ms", 0.0) or 0.0
        )
        abort_reason = "none" if completed else str(
            transport_error
            or getattr(
                playback_result,
                "abort_reason",
                "incomplete_http_pcm_single",
            )
        )
        self._update_state(
            last_private_voice_overlap_pcm_status=(
                "single_completed" if completed else "single_failed_after_audio"
            ),
            last_private_voice_overlap_pcm_reason=(
                "single_http_pcm_drained" if completed else abort_reason
            ),
            last_private_voice_overlap_pcm_lead_chunks=int(metrics["chunks"]),
            last_private_voice_overlap_pcm_lead_bytes=int(metrics["bytes"]),
            last_private_voice_overlap_pcm_lead_first_byte_ms=(
                None
                if first_byte_at is None
                else round((first_byte_at - started_at) * 1000.0, 1)
            ),
            last_private_voice_overlap_pcm_lead_eof_ms=(
                None
                if eof_at is None
                else round((eof_at - started_at) * 1000.0, 1)
            ),
            last_private_voice_overlap_pcm_error=(
                None if completed else abort_reason
            ),
            last_stream_transport_state=("eof" if completed else "error"),
            last_stream_playback_state=("done" if completed else playback_state),
            last_stream_time_to_first_byte_ms=(
                None
                if first_byte_at is None
                else round((first_byte_at - started_at) * 1000.0, 1)
            ),
            last_stream_time_to_first_pcm_ms=(
                None
                if first_byte_at is None
                else round((first_byte_at - started_at) * 1000.0, 1)
            ),
            last_stream_time_to_first_audio_ms=(
                None
                if first_audio_at is None
                else round((first_audio_at - started_at) * 1000.0, 1)
            ),
            last_stream_received_bytes=int(metrics["bytes"]),
            last_stream_start_buffer_ms=round(start_buffer_ms, 1),
            last_stream_rebuffer_count=rebuffer_count,
            last_stream_rebuffer_total_ms=round(rebuffer_total_ms, 1),
            last_stream_output_underflow_count=int(
                getattr(playback_result, "output_underflow_count", 0) or 0
            ),
            last_stream_callback_calls=int(
                getattr(playback_result, "callback_calls", 0) or 0
            ),
            last_stream_callback_status_underflows=int(
                getattr(playback_result, "callback_status_underflows", 0) or 0
            ),
            last_stream_ring_starvation_count=int(
                getattr(playback_result, "ring_starvation_count", 0) or 0
            ),
            last_stream_ring_low_watermark_ms=getattr(
                playback_result, "ring_low_watermark_ms", None
            ),
            last_stream_max_callback_lateness_ms=round(
                float(
                    getattr(playback_result, "max_callback_lateness_ms", 0.0)
                    or 0.0
                ),
                1,
            ),
            last_stream_feeder_refill_count=int(
                getattr(playback_result, "feeder_refill_count", 0) or 0
            ),
            last_stream_feeder_done=bool(
                getattr(playback_result, "feeder_done", False)
            ),
            last_stream_callback_finished=bool(
                getattr(playback_result, "callback_finished", False)
            ),
            last_stream_max_network_gap_ms=round(
                float(metrics["max_gap_ms"]), 1
            ),
            last_stream_provider_eof=eof_at is not None,
            last_stream_decoder_eof=provider_eof.is_set(),
            last_stream_abort_reason=abort_reason,
            last_stream_ffmpeg_ready=False,
            last_tts_prepare_ms=(
                None
                if first_audio_at is None
                else round((first_audio_at - started_at) * 1000.0, 1)
            ),
            last_tts_request_ms=(
                None
                if first_byte_at is None
                else round((first_byte_at - started_at) * 1000.0, 1)
            ),
            last_tts_playback_ms=round(played_duration_ms, 1),
            last_tts_total_ms=round(
                (time.perf_counter() - started_at) * 1000.0, 1
            ),
            last_tts_prebuffer_chunks=(1 if start_buffer_ms > 0 else 0),
            last_tts_prebuffer_ms=round(start_buffer_ms, 1),
            last_tts_seam_wait_ms=None,
            last_tts_max_seam_wait_ms=0.0,
            last_tts_avg_seam_wait_ms=None,
            last_tts_seam_grade="none",
            last_tts_playback_policy="http_pcm_single_callback",
            last_tts_seam_cause="none",
            last_tts_strategy="private_voice_http_pcm_single",
            last_tts_audio_paths=1 if first_audio_at is not None else 0,
            last_tts_text_len=len(raw_text),
            last_tts_chunks=1,
            last_tts_voice_mode=voice_mode,
            last_tts_chunk_max_chars=ELEVENLABS_SINGLE_REQUEST_MAX_CHARS,
        )
        if completed:
            self._increment_state("private_voice_overlap_pcm_completed_total")
        else:
            self._increment_state("private_voice_overlap_pcm_failed_total")
        result = self._audio_completion_result(
            state=(
                "completed"
                if completed
                else (
                    "cancelled"
                    if self._shutdown_requested()
                    else "provider_error"
                )
            ),
            segments=segments,
            fetched_segments=1 if eof_at is not None else 0,
            played_segments=1 if completed else 0,
            original_chars=original_chars,
            played_duration_ms=played_duration_ms,
            abort_reason=abort_reason,
        )
        return self._record_audio_completion(result)

    def _tts_overlap_pcm_completion(self, item):
        """Play HTTP lead and prefetched tail PCM through one callback."""

        sample_rate = _pcm_sample_rate(PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT)
        if sample_rate <= 0:
            return None

        started_at = time.perf_counter()
        lead_text = str(item.lead_text or "")
        pcm_queue = queue.Queue(maxsize=VOICE_HTTP_STREAM_PCM_QUEUE_CHUNKS)
        tail_pcm_queue = queue.Queue()
        provider_eof = threading.Event()
        playback_done = threading.Event()
        route_stop = threading.Event()
        shared_lock = threading.Lock()
        tail_lock = threading.Lock()
        shared = {
            "first_audio_at": None,
            "playback_state": "buffering",
            "playback_result": None,
        }
        tail_state = {
            "payload": None,
            "segments": [],
            "fetch_started_at": None,
            "first_byte_at": None,
            "eof_at": None,
            "chunks": 0,
            "bytes": 0,
            "max_gap_ms": 0.0,
            "retry_count": 0,
            "error": None,
            "done": False,
        }
        lead_metrics = {
            "first_byte_at": None,
            "eof_at": None,
            "chunks": 0,
            "bytes": 0,
            "max_gap_ms": 0.0,
        }

        def _put_main_pcm(pcm):
            while True:
                if (
                    route_stop.is_set()
                    or item.cancel_event.is_set()
                    or self._shutdown_requested()
                ):
                    raise RuntimeError("overlap_pcm_cancelled")
                if playback_done.is_set():
                    raise RuntimeError("overlap_pcm_playback_stopped")
                try:
                    pcm_queue.put(pcm, timeout=0.05)
                    return
                except queue.Full:
                    continue

        def _put_tail_pcm(pcm):
            if route_stop.is_set():
                raise RuntimeError("overlap_pcm_cancelled")
            tail_pcm_queue.put(pcm)

        def _on_playback_state(state):
            now = time.perf_counter()
            with shared_lock:
                shared["playback_state"] = str(state)
                if state == "playing" and shared["first_audio_at"] is None:
                    shared["first_audio_at"] = now
                first_audio_at = shared["first_audio_at"]
            self._update_state(
                last_stream_playback_state=str(state),
                last_stream_time_to_first_audio_ms=(
                    None
                    if first_audio_at is None
                    else round((first_audio_at - started_at) * 1000.0, 1)
                ),
                last_private_voice_overlap_first_audio_ms=(
                    None
                    if first_audio_at is None
                    else self._overlap_elapsed_ms(item, first_audio_at)
                ),
                last_private_voice_overlap_lead_play_count=(
                    1 if first_audio_at is not None else 0
                ),
            )

        def _on_playback_done(result):
            with shared_lock:
                shared["playback_result"] = result
            playback_done.set()

        def _tail_coordinator():
            try:
                payload = self._wait_overlap_tail_payload(item)
                segments = self._provider_segments_for_full_voice(
                    payload.tail_text
                )
                with tail_lock:
                    tail_state["payload"] = payload
                    tail_state["segments"] = segments
                if route_stop.is_set() or not segments:
                    return
                fetch_started_at = time.perf_counter()
                with tail_lock:
                    tail_state["fetch_started_at"] = fetch_started_at
                self._update_state(
                    last_private_voice_overlap_tail_fetch_started_ms=(
                        self._overlap_elapsed_ms(item, fetch_started_at)
                    ),
                    last_private_voice_overlap_status="tail_fetching",
                    last_private_voice_overlap_reason="http_pcm_tail_prefetch",
                    last_private_voice_overlap_pcm_status="tail_fetching",
                    last_private_voice_overlap_pcm_reason="http_pcm_tail_prefetch",
                )
                for segment in segments:
                    profile = VOICE_TONE_PROFILES.get(
                        _detect_tone_from_text(segment),
                        VOICE_TONE_PROFILES["default"],
                    )
                    segment_bytes_before = int(tail_state["bytes"])
                    for attempt in range(2):
                        if route_stop.is_set():
                            return
                        metrics = {
                            "first_byte_at": None,
                            "eof_at": None,
                            "chunks": 0,
                            "bytes": 0,
                            "max_gap_ms": 0.0,
                        }
                        try:
                            opened = self._open_overlap_pcm_stream_response(
                                segment,
                                profile,
                            )
                            self._drain_overlap_pcm_response(
                                opened,
                                sink=_put_tail_pcm,
                                stop_event=route_stop,
                                metrics=metrics,
                            )
                            with tail_lock:
                                if tail_state["first_byte_at"] is None:
                                    tail_state["first_byte_at"] = metrics[
                                        "first_byte_at"
                                    ]
                                tail_state["chunks"] += int(metrics["chunks"])
                                tail_state["bytes"] += int(metrics["bytes"])
                                tail_state["max_gap_ms"] = max(
                                    float(tail_state["max_gap_ms"]),
                                    float(metrics["max_gap_ms"]),
                                )
                                tail_state["eof_at"] = metrics["eof_at"]
                            break
                        except Exception:
                            emitted = int(metrics.get("bytes", 0) or 0)
                            if attempt == 0 and emitted == 0:
                                with tail_lock:
                                    tail_state["retry_count"] += 1
                                continue
                            raise
                    if int(tail_state["bytes"]) <= segment_bytes_before:
                        raise RuntimeError("overlap_pcm_tail_empty")
            except Exception as exc:
                with tail_lock:
                    tail_state["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                with tail_lock:
                    tail_state["done"] = True

        self._reset_http_stream_telemetry(ffmpeg_ready=True)
        self._record_audio_completion(
            self._audio_completion_result(
                state="created",
                segments=[lead_text],
                fetched_segments=0,
                played_segments=0,
                original_chars=len(lead_text),
                played_duration_ms=0.0,
            )
        )
        self._update_state(
            last_private_voice_overlap_status="lead_streaming",
            last_private_voice_overlap_reason="http_pcm24_lead",
            last_private_voice_overlap_lead_play_count=0,
            last_private_voice_overlap_tail_play_count=0,
            last_private_voice_overlap_tail_segment_count=0,
            last_private_voice_overlap_tail_retry_count=0,
            last_private_voice_overlap_llm_completed_ms=None,
            last_private_voice_overlap_lead_fetch_started_ms=(
                self._overlap_elapsed_ms(item, started_at)
            ),
            last_private_voice_overlap_lead_fetch_completed_ms=None,
            last_private_voice_overlap_tail_fetch_started_ms=None,
            last_private_voice_overlap_tail_fetch_completed_ms=None,
            last_private_voice_overlap_first_audio_ms=None,
            last_private_voice_overlap_lead_playback_ended_ms=None,
            last_private_voice_overlap_seam_wait_ms=None,
            last_private_voice_overlap_missing_chars=0,
            last_private_voice_overlap_duplicate_chars=0,
            last_private_voice_overlap_true_overlap=False,
            last_private_voice_overlap_pcm_status="lead_streaming",
            last_private_voice_overlap_pcm_reason="http_pcm24_lead",
            last_private_voice_overlap_pcm_output_format=(
                PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT
            ),
            last_private_voice_overlap_pcm_lead_chunks=0,
            last_private_voice_overlap_pcm_tail_chunks=0,
            last_private_voice_overlap_pcm_lead_bytes=0,
            last_private_voice_overlap_pcm_tail_bytes=0,
            last_private_voice_overlap_pcm_lead_first_byte_ms=None,
            last_private_voice_overlap_pcm_tail_first_byte_ms=None,
            last_private_voice_overlap_pcm_lead_eof_ms=None,
            last_private_voice_overlap_pcm_tail_eof_ms=None,
            last_private_voice_overlap_pcm_tail_buffered_before_lead_eof=False,
            last_private_voice_overlap_pcm_fallback_used=False,
            last_private_voice_overlap_pcm_error=None,
            last_streaming_action="private_overlap_http_pcm_pilot",
            last_streaming_reason="one_callback_lead_tail_pcm",
            last_stream_transport_state="connecting",
            last_stream_playback_state="buffering",
            last_stream_abort_reason="none",
            last_stream_ffmpeg_ready=False,
            last_audio_playback_watchdog="stream_stall",
            last_audio_playback_timeout_ms=float(
                PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S * 1000
            ),
        )

        tail_thread = threading.Thread(
            target=_tail_coordinator,
            daemon=True,
            name=f"tts-overlap-pcm-tail-{item.turn_id[:8]}",
        )
        tail_thread.start()
        self.lipsync.play_pcm_stream_nonblocking(
            pcm_queue,
            provider_eof,
            samplerate=sample_rate,
            startup_buffer_ms=PRIVATE_VOICE_OVERLAP_PCM_START_BUFFER_MS,
            stall_timeout_s=PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S,
            on_state=_on_playback_state,
            on_done=_on_playback_done,
            output_stream_factory=getattr(
                self, "_stream_output_stream_factory", None
            ),
            callback_output=True,
        )

        lead_error = None
        try:
            lead_profile = VOICE_TONE_PROFILES.get(
                _detect_tone_from_text(lead_text),
                VOICE_TONE_PROFILES["default"],
            )
            opened = self._open_overlap_pcm_stream_response(
                lead_text,
                lead_profile,
            )
            self._update_state(last_stream_transport_state="receiving")
            self._drain_overlap_pcm_response(
                opened,
                sink=_put_main_pcm,
                stop_event=route_stop,
                metrics=lead_metrics,
            )
        except Exception as exc:
            lead_error = f"{type(exc).__name__}: {exc}"

        lead_eof_at = lead_metrics.get("eof_at")
        if lead_metrics.get("first_byte_at") is not None:
            lead_first_byte_at = lead_metrics["first_byte_at"]
            self._update_state(
                last_private_voice_overlap_lead_fetch_completed_ms=(
                    self._overlap_elapsed_ms(item, lead_first_byte_at)
                ),
                last_private_voice_overlap_pcm_lead_first_byte_ms=(
                    self._overlap_elapsed_ms(item, lead_first_byte_at)
                ),
                last_stream_time_to_first_byte_ms=round(
                    (lead_first_byte_at - started_at) * 1000.0, 1
                ),
                last_stream_time_to_first_pcm_ms=round(
                    (lead_first_byte_at - started_at) * 1000.0, 1
                ),
            )
        self._update_state(
            last_private_voice_overlap_pcm_lead_chunks=int(
                lead_metrics["chunks"]
            ),
            last_private_voice_overlap_pcm_lead_bytes=int(
                lead_metrics["bytes"]
            ),
            last_private_voice_overlap_pcm_lead_eof_ms=(
                None
                if lead_eof_at is None
                else self._overlap_elapsed_ms(item, lead_eof_at)
            ),
        )

        if lead_error is None:
            while True:
                with tail_lock:
                    tail_done = bool(tail_state["done"])
                    tail_error = tail_state["error"]
                try:
                    tail_pcm = tail_pcm_queue.get(timeout=0.05)
                except queue.Empty:
                    if tail_done:
                        break
                    if playback_done.is_set():
                        lead_error = "RuntimeError: overlap_pcm_playback_stopped"
                        route_stop.set()
                        break
                    continue
                try:
                    _put_main_pcm(tail_pcm)
                finally:
                    tail_pcm_queue.task_done()
            if tail_error is not None:
                lead_error = tail_error

        if lead_error is not None:
            route_stop.set()
        provider_eof.set()

        playback_deadline = time.perf_counter() + float(
            VOICE_PLAYBACK_FALLBACK_TIMEOUT_S
        )
        while not playback_done.wait(timeout=0.05):
            if self._shutdown_requested() or time.perf_counter() >= playback_deadline:
                route_stop.set()
                self.lipsync.stop()
                break
        if not playback_done.is_set():
            playback_done.wait(timeout=2.0)

        with shared_lock:
            first_audio_at = shared["first_audio_at"]
            playback_result = shared["playback_result"]
            playback_state = shared["playback_state"]
        with tail_lock:
            payload = tail_state["payload"]
            tail_segments = list(tail_state["segments"])
            tail_fetch_started_at = tail_state["fetch_started_at"]
            tail_first_byte_at = tail_state["first_byte_at"]
            tail_eof_at = tail_state["eof_at"]
            tail_chunks = int(tail_state["chunks"])
            tail_bytes = int(tail_state["bytes"])
            tail_max_gap_ms = float(tail_state["max_gap_ms"])
            tail_retry_count = int(tail_state["retry_count"])
            tail_error = tail_state["error"]
            tail_done = bool(tail_state["done"])

        playback_completed = bool(
            playback_result is not None and playback_result.completed
        )
        if (
            not playback_completed
            and first_audio_at is None
            and not item.cancel_event.is_set()
            and not self._shutdown_requested()
        ):
            route_stop.set()
            self._increment_state("private_voice_overlap_pcm_fallback_total")
            self._update_state(
                last_private_voice_overlap_pcm_status="fallback_before_audio",
                last_private_voice_overlap_pcm_reason=(
                    lead_error
                    or tail_error
                    or getattr(
                        playback_result,
                        "abort_reason",
                        "pcm_playback_failed_before_audio",
                    )
                ),
                last_private_voice_overlap_pcm_fallback_used=True,
                last_private_voice_overlap_pcm_error=(lead_error or tail_error),
            )
            return None

        full_text = payload.full_text if payload is not None else lead_text
        tail_text = payload.tail_text if payload is not None else ""
        logical_segments = [lead_text] + ([tail_text] if tail_text else [])
        exact_text = bool(
            payload is not None
            and lead_text + tail_text == full_text
            and len(full_text) == len(lead_text) + len(tail_text)
        )
        completed = bool(
            lead_error is None
            and tail_error is None
            and tail_done
            and lead_eof_at is not None
            and (not tail_text or tail_eof_at is not None)
            and playback_completed
            and exact_text
        )
        played_duration_ms = float(
            getattr(playback_result, "played_duration_ms", 0.0) or 0.0
        )
        start_buffer_ms = float(
            getattr(playback_result, "start_buffer_ms", 0.0) or 0.0
        )
        rebuffer_count = int(
            getattr(playback_result, "rebuffer_count", 0) or 0
        )
        rebuffer_total_ms = float(
            getattr(playback_result, "rebuffer_total_ms", 0.0) or 0.0
        )
        lead_duration_ms = (
            int(lead_metrics["bytes"]) / (2.0 * float(sample_rate)) * 1000.0
        )
        lead_playback_ended_at = (
            None
            if first_audio_at is None
            else first_audio_at + lead_duration_ms / 1000.0
        )
        true_overlap = bool(
            tail_fetch_started_at is not None
            and lead_playback_ended_at is not None
            and tail_fetch_started_at < lead_playback_ended_at
        )
        tail_buffered_before_lead_eof = bool(
            tail_first_byte_at is not None
            and lead_eof_at is not None
            and tail_first_byte_at < lead_eof_at
        )
        seam_wait_ms = 0.0 if rebuffer_count == 0 else rebuffer_total_ms
        max_network_gap_ms = max(
            float(lead_metrics["max_gap_ms"]),
            tail_max_gap_ms,
        )
        total_bytes = int(lead_metrics["bytes"]) + tail_bytes
        abort_reason = "none"
        if not completed:
            abort_reason = str(
                lead_error
                or tail_error
                or getattr(
                    playback_result,
                    "abort_reason",
                    "incomplete_overlap_pcm",
                )
            )

        self._update_state(
            last_private_voice_overlap_full_chars=len(full_text),
            last_private_voice_overlap_tail_chars=len(tail_text),
            last_private_voice_overlap_tail_segment_count=len(tail_segments),
            last_private_voice_overlap_tail_retry_count=tail_retry_count,
            last_private_voice_overlap_llm_completed_ms=(
                None
                if payload is None
                else self._overlap_elapsed_ms(item, payload.llm_completed_at)
            ),
            last_private_voice_overlap_tail_fetch_completed_ms=(
                None
                if tail_eof_at is None
                else self._overlap_elapsed_ms(item, tail_eof_at)
            ),
            last_private_voice_overlap_first_audio_ms=(
                None
                if first_audio_at is None
                else self._overlap_elapsed_ms(item, first_audio_at)
            ),
            last_private_voice_overlap_lead_playback_ended_ms=(
                None
                if lead_playback_ended_at is None
                else self._overlap_elapsed_ms(item, lead_playback_ended_at)
            ),
            last_private_voice_overlap_seam_wait_ms=round(seam_wait_ms, 1),
            last_private_voice_overlap_missing_chars=(0 if exact_text else max(0, len(full_text) - len(lead_text + tail_text))),
            last_private_voice_overlap_duplicate_chars=(0 if exact_text else max(0, len(lead_text + tail_text) - len(full_text))),
            last_private_voice_overlap_lead_play_count=(
                1 if first_audio_at is not None else 0
            ),
            last_private_voice_overlap_tail_play_count=(
                1 if completed and tail_text else 0
            ),
            last_private_voice_overlap_true_overlap=true_overlap,
            last_private_voice_overlap_pcm_lead_chunks=int(
                lead_metrics["chunks"]
            ),
            last_private_voice_overlap_pcm_tail_chunks=tail_chunks,
            last_private_voice_overlap_pcm_lead_bytes=int(
                lead_metrics["bytes"]
            ),
            last_private_voice_overlap_pcm_tail_bytes=tail_bytes,
            last_private_voice_overlap_pcm_tail_first_byte_ms=(
                None
                if tail_first_byte_at is None
                else self._overlap_elapsed_ms(item, tail_first_byte_at)
            ),
            last_private_voice_overlap_pcm_tail_eof_ms=(
                None
                if tail_eof_at is None
                else self._overlap_elapsed_ms(item, tail_eof_at)
            ),
            last_private_voice_overlap_pcm_tail_buffered_before_lead_eof=(
                tail_buffered_before_lead_eof
            ),
            last_private_voice_overlap_pcm_error=(
                None if completed else abort_reason
            ),
            last_stream_transport_state=("eof" if completed else "error"),
            last_stream_playback_state=("done" if completed else playback_state),
            last_stream_received_bytes=total_bytes,
            last_stream_start_buffer_ms=round(start_buffer_ms, 1),
            last_stream_rebuffer_count=rebuffer_count,
            last_stream_rebuffer_total_ms=round(rebuffer_total_ms, 1),
            last_stream_output_underflow_count=int(
                getattr(playback_result, "output_underflow_count", 0) or 0
            ),
            last_stream_callback_calls=int(
                getattr(playback_result, "callback_calls", 0) or 0
            ),
            last_stream_callback_status_underflows=int(
                getattr(playback_result, "callback_status_underflows", 0) or 0
            ),
            last_stream_ring_starvation_count=int(
                getattr(playback_result, "ring_starvation_count", 0) or 0
            ),
            last_stream_ring_low_watermark_ms=getattr(
                playback_result, "ring_low_watermark_ms", None
            ),
            last_stream_max_callback_lateness_ms=round(
                float(
                    getattr(playback_result, "max_callback_lateness_ms", 0.0)
                    or 0.0
                ),
                1,
            ),
            last_stream_feeder_refill_count=int(
                getattr(playback_result, "feeder_refill_count", 0) or 0
            ),
            last_stream_feeder_done=bool(
                getattr(playback_result, "feeder_done", False)
            ),
            last_stream_callback_finished=bool(
                getattr(playback_result, "callback_finished", False)
            ),
            last_stream_max_network_gap_ms=round(max_network_gap_ms, 1),
            last_stream_provider_eof=bool(
                lead_eof_at is not None and (not tail_text or tail_eof_at is not None)
            ),
            last_stream_decoder_eof=provider_eof.is_set(),
            last_stream_abort_reason=abort_reason,
            last_stream_ffmpeg_ready=False,
            last_tts_prepare_ms=(
                None
                if first_audio_at is None
                else round((first_audio_at - started_at) * 1000.0, 1)
            ),
            last_tts_request_ms=(
                None
                if lead_metrics.get("first_byte_at") is None
                else round(
                    (lead_metrics["first_byte_at"] - started_at) * 1000.0,
                    1,
                )
            ),
            last_tts_playback_ms=round(played_duration_ms, 1),
            last_tts_total_ms=round(
                (time.perf_counter() - started_at) * 1000.0, 1
            ),
            last_tts_prebuffer_chunks=(1 if start_buffer_ms > 0 else 0),
            last_tts_prebuffer_ms=round(start_buffer_ms, 1),
            last_tts_seam_wait_ms=round(seam_wait_ms, 1),
            last_tts_max_seam_wait_ms=round(seam_wait_ms, 1),
            last_tts_avg_seam_wait_ms=round(seam_wait_ms, 1),
            last_tts_seam_grade=self._seam_grade(seam_wait_ms),
            last_tts_playback_policy="one_callback_lead_tail_pcm",
            last_tts_seam_cause=(
                "none" if rebuffer_count == 0 else "pcm_rebuffer"
            ),
            last_tts_strategy="private_voice_overlap_http_pcm",
            last_tts_audio_paths=1 if first_audio_at is not None else 0,
            last_tts_text_len=len(full_text),
            last_tts_chunks=len(logical_segments),
            last_tts_voice_mode=item.voice_mode,
            last_tts_chunk_max_chars=ELEVENLABS_SINGLE_REQUEST_MAX_CHARS,
        )

        if completed:
            self._increment_state("private_voice_overlap_completed_total")
            self._increment_state("private_voice_overlap_pcm_completed_total")
            self._update_state(
                last_private_voice_overlap_status="completed",
                last_private_voice_overlap_reason="http_pcm_lead_tail_drained",
                last_private_voice_overlap_pcm_status="completed",
                last_private_voice_overlap_pcm_reason=(
                    "one_callback_lead_tail_drained"
                ),
            )
            final_state = "completed"
            played_segments = len(logical_segments)
        else:
            self._increment_state("private_voice_overlap_failed_total")
            self._increment_state("private_voice_overlap_pcm_failed_total")
            self._update_state(
                last_private_voice_overlap_status="failed_after_audio",
                last_private_voice_overlap_reason=abort_reason,
                last_private_voice_overlap_pcm_status="failed_after_audio",
                last_private_voice_overlap_pcm_reason=abort_reason,
            )
            final_state = (
                "cancelled"
                if item.cancel_event.is_set() or self._shutdown_requested()
                else "provider_error"
            )
            played_segments = 1 if first_audio_at is not None else 0

        result = self._audio_completion_result(
            state=final_state,
            segments=logical_segments,
            fetched_segments=(
                len(logical_segments)
                if lead_eof_at is not None
                and (not tail_text or tail_eof_at is not None)
                else (1 if lead_eof_at is not None else 0)
            ),
            played_segments=played_segments,
            original_chars=len(full_text),
            played_duration_ms=played_duration_ms,
            abort_reason=abort_reason,
        )
        return self._record_audio_completion(result)

    def _tts_overlap_completion(self, item):
        if self._private_overlap_pcm_route_available():
            pcm_result = self._tts_overlap_pcm_completion(item)
            if pcm_result is not None:
                return pcm_result
            self._update_state(
                last_private_voice_overlap_pcm_fallback_used=True,
                last_private_voice_overlap_status="pcm_fallback",
                last_private_voice_overlap_reason="pcm_failed_before_audio",
            )
        ffmpeg_path = self._resolve_stream_ffmpeg_path()
        process = (
            self._spawn_streaming_decoder(ffmpeg_path)
            if ffmpeg_path
            else None
        )
        if process is not None:
            return self._tts_overlap_streaming_lead_completion(item, process)
        self._update_state(
            last_private_voice_overlap_reason="buffered_lead_fallback",
        )
        started_at = time.perf_counter()
        lead_text = str(item.lead_text or "")
        segments = [lead_text]
        lead_fetch_started_at = time.perf_counter()
        self._update_state(
            last_private_voice_overlap_status="lead_fetching",
            last_private_voice_overlap_reason="provider_request",
            last_private_voice_overlap_lead_fetch_started_ms=self._overlap_elapsed_ms(
                item, lead_fetch_started_at
            ),
            last_private_voice_overlap_lead_play_count=0,
            last_private_voice_overlap_tail_play_count=0,
            last_private_voice_overlap_tail_segment_count=0,
            last_private_voice_overlap_tail_retry_count=0,
            last_private_voice_overlap_llm_completed_ms=None,
            last_private_voice_overlap_tail_fetch_started_ms=None,
            last_private_voice_overlap_tail_fetch_completed_ms=None,
            last_private_voice_overlap_first_audio_ms=None,
            last_private_voice_overlap_lead_playback_ended_ms=None,
            last_private_voice_overlap_seam_wait_ms=None,
            last_private_voice_overlap_missing_chars=0,
            last_private_voice_overlap_duplicate_chars=0,
            last_private_voice_overlap_true_overlap=False,
        )
        if item.cancel_event.is_set() or self._shutdown_requested():
            result = self._audio_completion_result(
                state="cancelled",
                segments=segments,
                fetched_segments=0,
                played_segments=0,
                original_chars=len(lead_text),
                played_duration_ms=0.0,
                abort_reason="overlap_cancelled",
            )
            return self._record_audio_completion(result)

        lead_profile = VOICE_TONE_PROFILES.get(
            _detect_tone_from_text(lead_text),
            VOICE_TONE_PROFILES["default"],
        )
        try:
            lead_audio = self._tts_fetch_audio(lead_text, lead_profile)
        except Exception as exc:
            log_event("voice", f"Private overlap lead fetch raised: {exc}")
            lead_audio = None
        lead_fetch_completed_at = time.perf_counter()
        self._update_state(
            last_private_voice_overlap_lead_fetch_completed_ms=self._overlap_elapsed_ms(
                item, lead_fetch_completed_at
            )
        )

        if not lead_audio:
            try:
                payload = self._wait_overlap_tail_payload(item)
            except Exception as exc:
                self._increment_state("private_voice_overlap_failed_total")
                self._update_state(
                    last_private_voice_overlap_status="failed_before_audio",
                    last_private_voice_overlap_reason=f"{type(exc).__name__}: {exc}",
                )
                result = self._audio_completion_result(
                    state="provider_error",
                    segments=segments,
                    fetched_segments=0,
                    played_segments=0,
                    original_chars=len(lead_text),
                    played_duration_ms=0.0,
                    abort_reason="lead_fetch_failed",
                )
                return self._record_audio_completion(result)
            self._increment_state("private_voice_overlap_bypassed_total")
            self._update_state(
                last_private_voice_overlap_status="fallback_full_before_audio",
                last_private_voice_overlap_reason="lead_fetch_failed",
                last_private_voice_overlap_full_chars=len(payload.full_text),
                last_private_voice_overlap_tail_chars=len(payload.tail_text),
                last_private_voice_overlap_llm_completed_ms=self._overlap_elapsed_ms(
                    item, payload.llm_completed_at
                ),
            )
            return self._tts_and_lipsync(payload.full_text, voice_mode=item.voice_mode)

        tail_state = {
            "payload": None,
            "segments": [],
            "audio": {},
            "error": None,
            "retry_count": 0,
            "fetch_started_at": None,
            "fetch_completed_at": None,
        }
        tail_lock = threading.Lock()
        tail_done = threading.Event()

        def _tail_coordinator():
            try:
                payload = self._wait_overlap_tail_payload(item)
                tail_segments = self._provider_segments_for_full_voice(
                    payload.tail_text
                )
                with tail_lock:
                    tail_state["payload"] = payload
                    tail_state["segments"] = tail_segments
                if not tail_segments:
                    return
                fetch_started_at = time.perf_counter()
                with tail_lock:
                    tail_state["fetch_started_at"] = fetch_started_at
                self._update_state(
                    last_private_voice_overlap_tail_fetch_started_ms=self._overlap_elapsed_ms(
                        item, fetch_started_at
                    ),
                    last_private_voice_overlap_status="tail_fetching",
                    last_private_voice_overlap_reason="llm_tail_resolved",
                )

                def _fetch_tail_segment(index, text):
                    audio = None
                    for attempt in range(2):
                        if item.cancel_event.is_set() or self._shutdown_requested():
                            return
                        profile = VOICE_TONE_PROFILES.get(
                            _detect_tone_from_text(text),
                            VOICE_TONE_PROFILES["default"],
                        )
                        try:
                            audio = self._tts_fetch_audio(text, profile)
                        except Exception as exc:
                            log_event(
                                "voice",
                                f"Private overlap tail segment {index} raised: {exc}",
                            )
                            audio = None
                        if audio:
                            break
                        if attempt == 0:
                            with tail_lock:
                                tail_state["retry_count"] += 1
                    with tail_lock:
                        tail_state["audio"][index] = audio

                fetch_threads = [
                    threading.Thread(
                        target=_fetch_tail_segment,
                        args=(index, text),
                        daemon=True,
                        name=f"tts-overlap-tail-{index}",
                    )
                    for index, text in enumerate(tail_segments)
                ]
                for thread in fetch_threads:
                    thread.start()
                for thread in fetch_threads:
                    thread.join()
                with tail_lock:
                    if any(
                        not tail_state["audio"].get(index)
                        for index in range(len(tail_segments))
                    ):
                        tail_state["error"] = "tail_fetch_failed"
                    tail_state["fetch_completed_at"] = time.perf_counter()
            except Exception as exc:
                with tail_lock:
                    tail_state["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                tail_done.set()

        tail_thread = threading.Thread(
            target=_tail_coordinator,
            daemon=True,
            name=f"tts-overlap-tail-coordinator-{item.turn_id[:8]}",
        )
        tail_thread.start()

        lead_ok, lead_duration_ms, lead_ended_at, lead_error = (
            self._play_overlap_audio(item, lead_audio, role="lead")
        )
        if lead_ended_at is not None:
            self._update_state(
                last_private_voice_overlap_lead_playback_ended_ms=self._overlap_elapsed_ms(
                    item, lead_ended_at
                )
            )
        if not lead_ok:
            item.cancel_event.set()
            self._increment_state("private_voice_overlap_failed_total")
            self._update_state(
                last_private_voice_overlap_status="failed",
                last_private_voice_overlap_reason=lead_error,
            )
            result = self._audio_completion_result(
                state="cancelled" if lead_error == "cancelled" else "aborted",
                segments=segments,
                fetched_segments=1,
                played_segments=0,
                original_chars=len(lead_text),
                played_duration_ms=lead_duration_ms,
                abort_reason=lead_error,
            )
            return self._record_audio_completion(result)

        wait_timeout = float(
            PRIVATE_VOICE_OVERLAP_TAIL_TIMEOUT_S + VOICE_FETCH_STALL_TIMEOUT_S
        )
        if not tail_done.wait(timeout=wait_timeout):
            self._increment_state("private_voice_overlap_failed_total")
            self._update_state(
                last_private_voice_overlap_status="failed",
                last_private_voice_overlap_reason="tail_fetch_timeout",
            )
            result = self._audio_completion_result(
                state="provider_error",
                segments=segments,
                fetched_segments=1,
                played_segments=1,
                original_chars=len(lead_text),
                played_duration_ms=lead_duration_ms,
                abort_reason="tail_fetch_timeout",
            )
            return self._record_audio_completion(result)

        with tail_lock:
            payload = tail_state["payload"]
            tail_segments = list(tail_state["segments"])
            tail_audio = dict(tail_state["audio"])
            tail_error = tail_state["error"]
            tail_retry_count = int(tail_state["retry_count"])
            tail_fetch_started_at = tail_state["fetch_started_at"]
            tail_fetch_completed_at = tail_state["fetch_completed_at"]
        if payload is None:
            tail_error = tail_error or "tail_payload_missing"
            full_text = lead_text
            tail_text = ""
            llm_completed_at = None
        else:
            full_text = payload.full_text
            tail_text = payload.tail_text
            llm_completed_at = payload.llm_completed_at
        segments = [lead_text, *tail_segments]
        true_overlap = bool(
            tail_fetch_started_at is not None
            and lead_ended_at is not None
            and tail_fetch_started_at < lead_ended_at
        )
        self._update_state(
            last_private_voice_overlap_full_chars=len(full_text),
            last_private_voice_overlap_tail_chars=len(tail_text),
            last_private_voice_overlap_tail_segment_count=len(tail_segments),
            last_private_voice_overlap_tail_retry_count=tail_retry_count,
            last_private_voice_overlap_llm_completed_ms=(
                None
                if llm_completed_at is None
                else self._overlap_elapsed_ms(item, llm_completed_at)
            ),
            last_private_voice_overlap_tail_fetch_completed_ms=(
                None
                if tail_fetch_completed_at is None
                else self._overlap_elapsed_ms(item, tail_fetch_completed_at)
            ),
            last_private_voice_overlap_true_overlap=true_overlap,
            last_private_voice_overlap_missing_chars=0,
            last_private_voice_overlap_duplicate_chars=0,
        )
        if tail_error:
            self._increment_state("private_voice_overlap_failed_total")
            self._update_state(
                last_private_voice_overlap_status="failed_after_lead",
                last_private_voice_overlap_reason=tail_error,
            )
            fetched_tail = sum(1 for audio in tail_audio.values() if audio)
            result = self._audio_completion_result(
                state="provider_error",
                segments=segments,
                fetched_segments=1 + fetched_tail,
                played_segments=1,
                original_chars=len(full_text),
                played_duration_ms=lead_duration_ms,
                abort_reason="tail_fetch_failed",
            )
            return self._record_audio_completion(result)

        played_segments = 1
        played_duration_ms = lead_duration_ms
        seam_wait_ms = None
        for index, _tail_text in enumerate(tail_segments):
            if item.cancel_event.is_set() or self._shutdown_requested():
                tail_error = "cancelled"
                break
            if index == 0 and lead_ended_at is not None:
                seam_wait_ms = max(
                    0.0,
                    (time.perf_counter() - lead_ended_at) * 1000.0,
                )
                self._update_state(
                    last_private_voice_overlap_seam_wait_ms=round(
                        seam_wait_ms, 1
                    )
                )
            tail_ok, duration_ms, _ended_at, playback_error = (
                self._play_overlap_audio(
                    item,
                    tail_audio[index],
                    role="tail",
                )
            )
            if not tail_ok:
                tail_error = playback_error
                break
            played_segments += 1
            played_duration_ms += duration_ms
            self._update_state(last_private_voice_overlap_tail_play_count=1)

        if tail_error:
            self._increment_state("private_voice_overlap_failed_total")
            self._update_state(
                last_private_voice_overlap_status="failed_after_lead",
                last_private_voice_overlap_reason=tail_error,
            )
            result = self._audio_completion_result(
                state="cancelled" if tail_error == "cancelled" else "aborted",
                segments=segments,
                fetched_segments=len(segments),
                played_segments=played_segments,
                original_chars=len(full_text),
                played_duration_ms=played_duration_ms,
                abort_reason=tail_error,
            )
            return self._record_audio_completion(result)

        self._increment_state("private_voice_overlap_completed_total")
        self._update_state(
            last_private_voice_overlap_status="completed",
            last_private_voice_overlap_reason="exact_lead_tail_drained",
            last_private_voice_overlap_tail_play_count=(1 if tail_segments else 0),
            last_tts_prepare_ms=round(
                max(0.0, (time.perf_counter() - started_at) * 1000.0 - played_duration_ms),
                1,
            ),
            last_tts_playback_ms=round(played_duration_ms, 1),
            last_tts_total_ms=round(
                (time.perf_counter() - started_at) * 1000.0, 1
            ),
            last_tts_prebuffer_chunks=(1 if lead_audio else 0),
            last_tts_prebuffer_ms=0.0,
            last_tts_seam_wait_ms=(
                None if seam_wait_ms is None else round(seam_wait_ms, 1)
            ),
            last_tts_max_seam_wait_ms=(
                None if seam_wait_ms is None else round(seam_wait_ms, 1)
            ),
            last_tts_avg_seam_wait_ms=(
                None if seam_wait_ms is None else round(seam_wait_ms, 1)
            ),
            last_tts_seam_grade=self._seam_grade(seam_wait_ms),
            last_tts_playback_policy="one_lead_tail",
            last_tts_seam_cause=(
                "none"
                if seam_wait_ms is None or seam_wait_ms <= VOICE_FULL_BATCH_SEAM_BAD_MS
                else "tail_fetch"
            ),
            last_tts_strategy="private_voice_overlap",
            last_tts_audio_paths=played_segments,
            last_tts_text_len=len(full_text),
            last_tts_chunks=len(segments),
            last_tts_voice_mode=item.voice_mode,
            last_tts_chunk_max_chars=ELEVENLABS_SINGLE_REQUEST_MAX_CHARS,
        )
        result = self._audio_completion_result(
            state="completed",
            segments=segments,
            fetched_segments=len(segments),
            played_segments=played_segments,
            original_chars=len(full_text),
            played_duration_ms=played_duration_ms,
        )
        return self._record_audio_completion(result)

    def _tts_full_provider_completion(self, text, profile, voice_mode, *, original_chars=None):
        """Provider-sized full voice with explicit fetch/playback completion."""
        started_at = time.perf_counter()
        original_chars = len(str(text or "")) if original_chars is None else max(0, int(original_chars))
        segments = self._provider_segments_for_full_voice(text)
        if not segments:
            result = self._audio_completion_result(
                state="completed",
                segments=[],
                fetched_segments=0,
                played_segments=0,
                original_chars=0,
                played_duration_ms=0.0,
            )
            return self._record_audio_completion(result)

        if self._shutdown_requested():
            result = self._audio_completion_result(
                state="cancelled",
                segments=segments,
                fetched_segments=0,
                played_segments=0,
                original_chars=original_chars,
                played_duration_ms=0.0,
                abort_reason="shutdown_cancelled",
            )
            return self._record_audio_completion(result)

        strategy = "provider_single_request" if len(segments) == 1 else "provider_segments"
        self._record_audio_completion(
            self._audio_completion_result(
                state="created",
                segments=segments,
                fetched_segments=0,
                played_segments=0,
                original_chars=original_chars,
                played_duration_ms=0.0,
            )
        )
        log_event(
            "voice",
            f"TTS full provider path: chars={original_chars} segments={len(segments)} "
            f"request_max={ELEVENLABS_SINGLE_REQUEST_MAX_CHARS}",
        )

        audio_queue = queue.Queue(maxsize=len(segments))
        stop_flag = threading.Event()

        def _fetch_segment(idx):
            if stop_flag.is_set() or self._shutdown_requested():
                return
            try:
                audio = self._tts_fetch_audio(segments[idx], profile)
            except Exception as exc:
                log_event("voice", f"TTS provider segment {idx} raised: {exc}")
                audio = None
            if not (stop_flag.is_set() or self._shutdown_requested()):
                audio_queue.put((idx, audio))

        fetch_threads = [
            threading.Thread(target=_fetch_segment, args=(idx,), daemon=True, name=f"tts-provider-{idx}")
            for idx in range(len(segments))
        ]
        for thread in fetch_threads:
            thread.start()

        reorder = {}
        fetched_segments = 0
        played_segments = 0
        played_duration_ms = 0.0
        seam_waits = []
        last_segment_end_at = None
        abort_state = None
        abort_reason = "none"
        fetch_progress_at = time.perf_counter()

        self._record_audio_completion(
            self._audio_completion_result(
                state="fetching",
                segments=segments,
                fetched_segments=fetched_segments,
                played_segments=played_segments,
                original_chars=original_chars,
                played_duration_ms=played_duration_ms,
            )
        )

        try:
            for expected_idx in range(len(segments)):
                while expected_idx not in reorder:
                    if self._shutdown_requested():
                        abort_state = "cancelled"
                        abort_reason = "shutdown_cancelled"
                        break
                    remaining = VOICE_FETCH_STALL_TIMEOUT_S - (time.perf_counter() - fetch_progress_at)
                    if remaining <= 0:
                        abort_state = "provider_error"
                        abort_reason = "fetch_stall_timeout"
                        break
                    try:
                        idx, audio = audio_queue.get(timeout=min(0.1, remaining))
                    except queue.Empty:
                        continue
                    if self._shutdown_requested():
                        abort_state = "cancelled"
                        abort_reason = "shutdown_cancelled"
                        break
                    fetch_progress_at = time.perf_counter()
                    if audio is None:
                        abort_state = "provider_error"
                        abort_reason = "provider_error"
                        break
                    reorder[idx] = audio
                    fetched_segments += 1
                    self._record_audio_completion(
                        self._audio_completion_result(
                            state="fetching",
                            segments=segments,
                            fetched_segments=fetched_segments,
                            played_segments=played_segments,
                            original_chars=original_chars,
                            played_duration_ms=played_duration_ms,
                        )
                    )
                if abort_state is not None:
                    break

                if self._shutdown_requested():
                    abort_state = "cancelled"
                    abort_reason = "shutdown_cancelled"
                    break
                audio = reorder.pop(expected_idx)
                prepared = self.lipsync.prepare_audio_bytes(audio)
                if prepared is None:
                    abort_state = "aborted"
                    abort_reason = "decode_error"
                    break
                if self._shutdown_requested():
                    abort_state = "cancelled"
                    abort_reason = "shutdown_cancelled"
                    break

                self._record_audio_completion(
                    self._audio_completion_result(
                        state="ready",
                        segments=segments,
                        fetched_segments=fetched_segments,
                        played_segments=played_segments,
                        original_chars=original_chars,
                        played_duration_ms=played_duration_ms,
                    )
                )

                if last_segment_end_at is not None:
                    seam_waits.append(max(0.0, (time.perf_counter() - last_segment_end_at) * 1000.0))

                playback_done = threading.Event()
                playback_success = {"value": False}

                def _on_done(success):
                    playback_success["value"] = bool(success)
                    playback_done.set()

                try:
                    decoded_duration_s = float(prepared.duration_seconds)
                except (AttributeError, TypeError, ValueError):
                    decoded_duration_s = 0.0
                duration_known = math.isfinite(decoded_duration_s) and decoded_duration_s > 0.0
                if duration_known:
                    playback_timeout = decoded_duration_s + VOICE_PLAYBACK_GRACE_S
                    playback_watchdog = "decoded_duration"
                else:
                    playback_timeout = float(VOICE_PLAYBACK_FALLBACK_TIMEOUT_S)
                    playback_watchdog = "safe_fallback"
                playback_timeout = max(1.0, playback_timeout)
                self._update_state(
                    last_audio_playback_watchdog=playback_watchdog,
                    last_audio_playback_timeout_ms=round(playback_timeout * 1000.0, 1),
                )
                self._record_audio_completion(
                    self._audio_completion_result(
                        state="playing",
                        segments=segments,
                        fetched_segments=fetched_segments,
                        played_segments=played_segments,
                        original_chars=original_chars,
                        played_duration_ms=played_duration_ms,
                    )
                )
                play_started = time.perf_counter()
                shutdown_lock = getattr(self, "_shutdown_lock", None)
                if shutdown_lock is None:
                    if self._shutdown_requested():
                        abort_state = "cancelled"
                        abort_reason = "shutdown_cancelled"
                        break
                    self.lipsync.play_prepared_audio_nonblocking(prepared, on_done=_on_done)
                else:
                    with shutdown_lock:
                        if self._shutdown_requested():
                            abort_state = "cancelled"
                            abort_reason = "shutdown_cancelled"
                            break
                        self.lipsync.play_prepared_audio_nonblocking(prepared, on_done=_on_done)

                playback_deadline = time.perf_counter() + playback_timeout
                while not playback_done.is_set():
                    if self._shutdown_requested():
                        abort_state = "cancelled"
                        abort_reason = "shutdown_cancelled"
                        self.lipsync.stop()
                        break
                    remaining_playback = playback_deadline - time.perf_counter()
                    if remaining_playback <= 0:
                        abort_state = "aborted"
                        abort_reason = "playback_timeout"
                        self.lipsync.stop()
                        break
                    playback_done.wait(timeout=min(0.1, remaining_playback))
                if abort_state is not None:
                    break
                last_segment_end_at = time.perf_counter()
                if not playback_success["value"]:
                    if self.lipsync.stop_event.is_set():
                        abort_state = "cancelled"
                        abort_reason = "playback_cancelled"
                    else:
                        abort_state = "aborted"
                        abort_reason = "playback_error"
                    break
                playback_elapsed_ms = (time.perf_counter() - play_started) * 1000.0
                played_duration_ms += (
                    decoded_duration_s * 1000.0 if duration_known else playback_elapsed_ms
                )
                played_segments += 1

            stop_flag.set()
            if abort_state is None and played_segments == len(segments):
                final_state = "completed"
                abort_reason = "none"
            else:
                final_state = abort_state or "aborted"
                if abort_reason == "none":
                    abort_reason = "incomplete_playback"

            max_seam_wait_ms = max(seam_waits) if seam_waits else 0.0
            avg_seam_wait_ms = (sum(seam_waits) / len(seam_waits)) if seam_waits else None
            total_ms = (time.perf_counter() - started_at) * 1000.0
            self._update_state(
                last_tts_prepare_ms=round(total_ms - played_duration_ms, 1),
                last_tts_playback_ms=round(played_duration_ms, 1),
                last_tts_total_ms=round(total_ms, 1),
                last_tts_prebuffer_chunks=fetched_segments,
                last_tts_prebuffer_ms=0.0,
                last_tts_seam_wait_ms=None if not seam_waits else round(seam_waits[-1], 1),
                last_tts_max_seam_wait_ms=round(max_seam_wait_ms, 1),
                last_tts_avg_seam_wait_ms=None if avg_seam_wait_ms is None else round(avg_seam_wait_ms, 1),
                last_tts_seam_grade=self._seam_grade(max_seam_wait_ms if seam_waits else None),
                last_tts_playback_policy=strategy,
                last_tts_seam_cause="none" if max_seam_wait_ms <= VOICE_FULL_BATCH_SEAM_BAD_MS else "download",
                last_tts_strategy=strategy,
                last_tts_audio_paths=played_segments,
                last_tts_text_len=original_chars,
                last_tts_chunks=len(segments),
                last_tts_voice_mode=voice_mode,
                last_tts_chunk_max_chars=ELEVENLABS_SINGLE_REQUEST_MAX_CHARS,
            )
            result = self._audio_completion_result(
                state=final_state,
                segments=segments,
                fetched_segments=fetched_segments,
                played_segments=played_segments,
                original_chars=original_chars,
                played_duration_ms=played_duration_ms,
                abort_reason=abort_reason,
            )
            self._record_audio_completion(result)
            if not result.audio_completed:
                print(
                    "🔇 Audio chưa hoàn tất: "
                    f"state={result.state} | played={result.played_segments}/{result.requested_segments} | "
                    f"remaining_chars={result.remaining_chars} | reason={result.abort_reason}"
                )
            return result
        except Exception as exc:
            stop_flag.set()
            log_event("voice", f"TTS full provider completion error: {exc}")
            self._update_state(last_error=str(exc))
            result = self._audio_completion_result(
                state="provider_error",
                segments=segments,
                fetched_segments=fetched_segments,
                played_segments=played_segments,
                original_chars=original_chars,
                played_duration_ms=played_duration_ms,
                abort_reason="internal_error",
            )
            self._record_audio_completion(result)
            print(
                "🔇 Audio chưa hoàn tất: "
                f"state={result.state} | played={result.played_segments}/{result.requested_segments} | "
                f"remaining_chars={result.remaining_chars} | reason={result.abort_reason}"
            )
            return result

    def _tts_and_lipsync(self, text, voice_mode="chat"):
        """TTS + playback with explicit audio-completion truth.

        Pipeline:
          1. Full voice uses provider-sized segments; text within the provider
             capability is one request and one playback.
          2. The rollback/chat path splits text into legacy chunks and spawns
             one fetch thread per chunk (semaphore-bounded to 3).
             Threads push ``(idx, audio_bytes)`` into a shared queue as
             soon as ElevenLabs returns. Order of arrival is *not* the
             original chunk order — short chunks finish first.
          3. The sequencer loop reads from that queue, but only plays a
             chunk when its idx matches ``next_expected_idx``. Out-of-
             order chunks are parked in a reorder dict.
          4. Playback uses ``play_audio_nonblocking`` so the sequencer
             can immediately drain the next ready chunk.
          5. Full voice completion requires the final playback callback.
             Legacy rollback retains its historical dynamic timeout.
        """
        started_at = time.perf_counter()
        playback_ms = 0.0
        total_chunks = 0
        prebuffer_ms = 0.0
        prebuffer_ready_chunks = 0
        last_seam_wait_ms = None
        max_seam_wait_ms = 0.0
        seam_waits = []
        last_chunk_end_at = None
        playback_policy = "none"
        seam_cause = "none"
        chunks = []
        self._update_state(
            last_audio_playback_watchdog="none",
            last_audio_playback_timeout_ms=0.0,
        )
        try:
            if DEBUG_NO_TTS or VOICE_TEST_MODE:
                return
            if not text.strip():
                return
            source_original_chars = len(str(text or ""))
            text = self._prepare_tts_request_text(text)
            if not text.strip():
                return
            voice_mode = self._normalize_voice_mode(voice_mode)
            full_voice = self._is_full_voice_mode(voice_mode)
            chunk_limit = self._chunk_limit_for_mode(voice_mode)
            log_event("voice", f"TTS sequencer-play text length={len(text)} mode={voice_mode} chunk_limit={chunk_limit}")
            tone = _detect_tone_from_text(text)
            profile = VOICE_TONE_PROFILES.get(tone, VOICE_TONE_PROFILES["default"])

            if full_voice and VOICE_FULL_SINGLE_REQUEST_ENABLED:
                if (
                    self._private_overlap_pcm_route_available()
                    and len(text) <= ELEVENLABS_SINGLE_REQUEST_MAX_CHARS
                ):
                    pcm_result = self._tts_http_pcm_single_completion(
                        text,
                        profile,
                        voice_mode,
                        original_chars=source_original_chars,
                    )
                    if pcm_result is not None:
                        return pcm_result
                if self._http_streaming_pilot_selected(text, voice_mode):
                    ffmpeg_path = self._resolve_stream_ffmpeg_path()
                    if ffmpeg_path:
                        process = self._spawn_streaming_decoder(ffmpeg_path)
                    else:
                        process = None
                    if process is not None:
                        if self._shutdown_requested():
                            self._close_stream_decoder(process, terminate=True)
                            segments = [str(text or "")]
                            result = self._audio_completion_result(
                                state="cancelled",
                                segments=segments,
                                fetched_segments=0,
                                played_segments=0,
                                original_chars=source_original_chars,
                                played_duration_ms=0.0,
                                abort_reason="shutdown_cancelled",
                            )
                            return self._record_audio_completion(result)
                        return self._tts_http_streaming_completion(
                            text,
                            profile,
                            voice_mode,
                            process,
                            original_chars=source_original_chars,
                        )

                    # Preflight happens before the provider request, so this is
                    # the only point where the rollback path is allowed.
                    self._reset_http_stream_telemetry(ffmpeg_ready=False)
                    self._update_state(
                        last_streaming_action="provider_single_request_rollback",
                        last_streaming_reason="ffmpeg_preflight_failed_before_request",
                    )
                return self._tts_full_provider_completion(
                    text,
                    profile,
                    voice_mode,
                    original_chars=source_original_chars,
                )

            chunks = self._split_tts_text(text, max_chars=chunk_limit)
            if not chunks:
                return

            timeout_s = TTS_DYNAMIC_TIMEOUT_BASE_S + TTS_DYNAMIC_TIMEOUT_PER_CHUNK_S * len(chunks)
            log_event("voice", f"TTS sequencer: chunks={len(chunks)} dynamic_timeout={timeout_s:.1f}s")

            audio_queue: queue.Queue = queue.Queue(maxsize=len(chunks))
            stop_flag = threading.Event()
            failure_event = threading.Event()

            def _fetch_chunk(idx: int) -> None:
                if stop_flag.is_set() or failure_event.is_set():
                    return
                try:
                    data = self._tts_fetch_audio(chunks[idx], profile)
                except Exception as exc:
                    log_event("voice", f"TTS fetch chunk {idx} raised: {exc}")
                    data = None
                if stop_flag.is_set() or failure_event.is_set():
                    return
                audio_queue.put((idx, data))

            fetch_threads = [
                threading.Thread(target=_fetch_chunk, args=(i,), daemon=True, name=f"tts-fetch-{i}")
                for i in range(len(chunks))
            ]
            for t in fetch_threads:
                t.start()

            # Sequencer state
            reorder: dict = {}
            next_expected_idx = 0
            pending = len(chunks)
            playback_done = threading.Event()

            def _on_chunk_done():
                # Fires inside the lipsync thread after fade-tail completes.
                playback_done.set()

            def _play_chunk(idx: int, audio: bytes) -> None:
                nonlocal total_chunks, playback_ms, last_chunk_end_at, last_seam_wait_ms, max_seam_wait_ms, seam_cause
                if last_chunk_end_at is not None:
                    seam_wait = max(0.0, (time.perf_counter() - last_chunk_end_at) * 1000)
                    last_seam_wait_ms = seam_wait
                    max_seam_wait_ms = max(max_seam_wait_ms, seam_wait)
                    seam_waits.append(seam_wait)
                    if seam_wait > VOICE_FULL_BATCH_SEAM_BAD_MS and seam_cause in {"none", "unknown"}:
                        seam_cause = "waiting_next_chunk"
                    log_event("voice", f"TTS sequencer: seam wait before chunk {idx}: {seam_wait:.1f}ms")
                playback_done.clear()
                play_started = time.perf_counter()
                self.lipsync.play_audio_nonblocking(audio, on_done=_on_chunk_done)
                # Block this sequencer turn on lipsync actually finishing,
                # so chunks play in order even if the user doesn't
                # externally gate the reply.
                playback_done.wait()
                playback_ms += (time.perf_counter() - play_started) * 1000
                last_chunk_end_at = time.perf_counter()
                total_chunks += 1
                log_event("voice", f"TTS sequencer: chunk {idx} played")

            deadline = time.perf_counter() + timeout_s

            if VOICE_PREBUFFER_ENABLED and len(chunks) > 1 and VOICE_PREBUFFER_MAX_MS > 0:
                playback_policy = "prebuffer"
                prebuffer_started = time.perf_counter()
                prebuffer_target = min(len(chunks), VOICE_PREBUFFER_CHUNKS)
                prebuffer_deadline = min(deadline, prebuffer_started + (VOICE_PREBUFFER_MAX_MS / 1000.0))
                while True:
                    prebuffer_ready_chunks = sum(1 for i in range(prebuffer_target) if i in reorder)
                    if prebuffer_ready_chunks >= prebuffer_target:
                        break
                    remaining = min(deadline, prebuffer_deadline) - time.perf_counter()
                    if remaining <= 0:
                        break
                    try:
                        idx, audio = audio_queue.get(timeout=remaining)
                    except queue.Empty:
                        break
                    log_event(
                        "voice",
                        f"TTS prebuffer: got idx={idx} target={prebuffer_target} "
                        f"audio={'ok' if audio else 'None'} ready={prebuffer_ready_chunks}",
                    )
                    if audio is None:
                        failure_event.set()
                        log_event("voice", f"TTS prebuffer: chunk {idx} failed permanently, playing fallback")
                        self._play_fallback_phrase()
                        stop_flag.set()
                        break
                    reorder[idx] = audio
                prebuffer_ms = (time.perf_counter() - prebuffer_started) * 1000
                prebuffer_ready_chunks = sum(1 for i in range(prebuffer_target) if i in reorder)
                log_event(
                    "voice",
                    f"TTS prebuffer: ready={prebuffer_ready_chunks}/{prebuffer_target} "
                    f"wait={prebuffer_ms:.1f}ms",
                )
            elif len(chunks) <= 1:
                playback_policy = "immediate"
            else:
                playback_policy = "sequencer"

            previous_bad_seam = False
            with self.state_lock:
                try:
                    previous_bad_seam = float(self.state.get("last_tts_max_seam_wait_ms") or 0.0) > VOICE_FULL_BATCH_SEAM_BAD_MS
                except (TypeError, ValueError):
                    previous_bad_seam = False
            prebuffer_target = min(len(chunks), VOICE_PREBUFFER_CHUNKS) if len(chunks) > 1 else 0
            prebuffer_failed = len(chunks) > 1 and prebuffer_ready_chunks < min(2, prebuffer_target or 2)
            high_chunk_risk = len(chunks) >= 4
            long_story_risk = len(text) > chunk_limit
            full_batch_risk = bool(
                full_voice
                and len(chunks) > 1
                and prebuffer_failed
                and (previous_bad_seam or high_chunk_risk or long_story_risk)
            )

            if VOICE_FULL_BATCH_ENABLED and full_batch_risk and not (stop_flag.is_set() or failure_event.is_set()):
                playback_policy = "full_batch"
                seam_cause = "waiting_next_chunk"
                batch_deadline = min(deadline, time.perf_counter() + (VOICE_FULL_BATCH_PREBUFFER_EXTRA_MS / 1000.0))
                log_event(
                    "voice",
                    f"TTS full-batch fallback: chunks={len(chunks)} previous_bad={previous_bad_seam} "
                    f"high_chunk_risk={high_chunk_risk} long_story_risk={long_story_risk} "
                    f"prebuffer_ready={prebuffer_ready_chunks}/{prebuffer_target}",
                )
                while len(reorder) < len(chunks):
                    remaining = batch_deadline - time.perf_counter()
                    if remaining <= 0:
                        break
                    try:
                        idx, audio = audio_queue.get(timeout=remaining)
                    except queue.Empty:
                        break
                    if audio is None:
                        failure_event.set()
                        log_event("voice", f"TTS full-batch: chunk {idx} failed permanently, playing fallback")
                        self._play_fallback_phrase()
                        stop_flag.set()
                        break
                    reorder[idx] = audio
                prebuffer_ready_chunks = len(reorder)
                log_event(
                    "voice",
                    f"TTS full-batch fallback: ready={prebuffer_ready_chunks}/{len(chunks)}",
                )
                if prebuffer_ready_chunks < len(chunks):
                    playback_policy = "prebuffer_timeout"
                    seam_cause = "download"
                    log_event(
                        "voice",
                        f"TTS full-batch fallback incomplete; continuing sequencer ready={prebuffer_ready_chunks}/{len(chunks)}",
                    )
            elif prebuffer_failed and len(chunks) > 1:
                playback_policy = "prebuffer_timeout"
                seam_cause = "download"

            while pending > 0:
                if stop_flag.is_set() or failure_event.is_set():
                    break
                if next_expected_idx in reorder:
                    buffered = reorder.pop(next_expected_idx)
                    _play_chunk(next_expected_idx, buffered)
                    next_expected_idx += 1
                    pending -= 1
                    continue
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    log_event("voice", f"TTS sequencer: dynamic timeout {timeout_s:.1f}s reached, aborting")
                    print(f"⏳ TTS sequencer: vượt timeout {timeout_s:.1f}s, hủy các chunk còn lại")
                    stop_flag.set()
                    break
                try:
                    idx, audio = audio_queue.get(timeout=remaining)
                except queue.Empty:
                    log_event("voice", f"TTS sequencer: queue empty for {remaining:.1f}s, stopping")
                    stop_flag.set()
                    break

                log_event(
                    "voice",
                    f"TTS sequencer: got idx={idx} (expected={next_expected_idx}) "
                    f"audio={'ok' if audio else 'None'} reorder_size={len(reorder)} pending={pending}",
                )

                if audio is None:
                    # Permanent chunk failure — play fallback, then stop.
                    failure_event.set()
                    log_event("voice", f"TTS sequencer: chunk {idx} failed permanently, playing fallback")
                    self._play_fallback_phrase()
                    stop_flag.set()
                    break

                if idx == next_expected_idx:
                    _play_chunk(idx, audio)
                    next_expected_idx += 1
                    pending -= 1
                    # Drain anything that arrived ahead of its turn.
                    while next_expected_idx in reorder and pending > 0:
                        buffered = reorder.pop(next_expected_idx)
                        _play_chunk(next_expected_idx, buffered)
                        next_expected_idx += 1
                        pending -= 1
                else:
                    # Out-of-order arrival — park it and wait for the gap to fill.
                    reorder[idx] = audio
                    log_event(
                        "voice",
                        f"TTS sequencer: chunk {idx} buffered (waiting for {next_expected_idx}), "
                        f"buffer_size={len(reorder)}",
                    )

            stop_flag.set()
            # Best-effort: tell any straggler fetchers to bail.
            failure_event.set()

            prepare_ms = (time.perf_counter() - started_at) * 1000
            total_ms = (time.perf_counter() - started_at) * 1000
            avg_seam_wait_ms = (sum(seam_waits) / len(seam_waits)) if seam_waits else None
            seam_grade = self._seam_grade(max_seam_wait_ms if seam_waits else None)
            self._update_state(
                last_tts_prepare_ms=round(prepare_ms, 1),
                last_tts_playback_ms=round(playback_ms, 1),
                last_tts_total_ms=round(total_ms, 1),
                last_tts_prebuffer_chunks=prebuffer_ready_chunks,
                last_tts_prebuffer_ms=round(prebuffer_ms, 1),
                last_tts_seam_wait_ms=None if last_seam_wait_ms is None else round(last_seam_wait_ms, 1),
                last_tts_max_seam_wait_ms=round(max_seam_wait_ms, 1),
                last_tts_avg_seam_wait_ms=None if avg_seam_wait_ms is None else round(avg_seam_wait_ms, 1),
                last_tts_seam_grade=seam_grade,
                last_tts_playback_policy=playback_policy,
                last_tts_seam_cause=seam_cause,
                last_tts_strategy="sequencer_play",
                last_tts_audio_paths=total_chunks,
                last_tts_text_len=source_original_chars,
                last_tts_chunks=len(chunks),
                last_tts_voice_mode=voice_mode,
                last_tts_chunk_max_chars=chunk_limit,
            )
            result = self._audio_completion_result(
                state="completed" if pending == 0 else "aborted",
                segments=chunks,
                fetched_segments=min(len(chunks), total_chunks + len(reorder)),
                played_segments=total_chunks,
                original_chars=source_original_chars,
                played_duration_ms=playback_ms,
                abort_reason="none" if pending == 0 else "legacy_sequencer_incomplete",
            )
            self._record_audio_completion(result)
            if pending > 0:
                log_event("voice", f"TTS sequencer: finished with {pending} chunks unplayed (likely timeout/failure)")
            return result
        except Exception as exc:
            log_event("voice", f"TTS/lipsync error: {exc}")
            self._update_state(last_error=str(exc))
            print(f"TTS error: {exc}")
            result = self._audio_completion_result(
                state="provider_error",
                segments=chunks,
                fetched_segments=total_chunks,
                played_segments=total_chunks,
                original_chars=len(str(text or "")),
                played_duration_ms=playback_ms,
                abort_reason="internal_error",
            )
            return self._record_audio_completion(result)

    def _play_fallback_phrase(self):
        """Play a random humanizing recovery phrase to soften a chunk failure."""
        if not TTS_FALLBACK_PHRASES:
            return
        phrase = random.choice(TTS_FALLBACK_PHRASES)
        log_event("voice", f"TTS fallback phrase: {phrase}")
        print(f"🩹 {phrase}")
        try:
            data = self._tts_fetch_audio(phrase, VOICE_TONE_PROFILES["default"])
        except Exception as exc:
            log_event("voice", f"TTS fallback fetch failed: {exc}")
            return
        if data is None:
            log_event("voice", "TTS fallback: ElevenLabs returned None, skipping audio")
            return
        try:
            self.lipsync.play_audio_data(data)
        except Exception as exc:
            log_event("voice", f"TTS fallback playback failed: {exc}")
        except Exception as exc:
            log_event("voice", f"TTS/lipsync error: {exc}")
            self._update_state(last_error=str(exc))
            print(f"TTS error: {exc}")

    def tts_to_audio_paths(self, text):
        text = self._prepare_tts_request_text(text)
        before_hits = int(self.state.get("cache_hits") or 0)
        before_misses = int(self.state.get("cache_misses") or 0)
        chunks = self._split_tts_text(text)
        if len(chunks) > 1:
            self._increment_state("chunked_total")
            self._update_state(last_tts_strategy="chunk_then_play")
            log_event("voice", f"TTS chunked text: chunks={len(chunks)} length={len(text)}")
        else:
            self._update_state(last_tts_strategy="cache_first" if self._should_cache_text(text) else "single_request")
        self._update_state(
            last_tts_text_len=len(text or ""),
            last_tts_chunks=len(chunks),
        )

        audio_paths = []
        for chunk in chunks:
            path = self.tts_to_wav(chunk)
            if not path:
                continue
            audio_paths.append(path)
        after_hits = int(self.state.get("cache_hits") or 0)
        after_misses = int(self.state.get("cache_misses") or 0)
        self._update_state(
            last_tts_cache_hits=max(0, after_hits - before_hits),
            last_tts_cache_misses=max(0, after_misses - before_misses),
        )
        return audio_paths

    def tts_to_wav(self, text):
        try:
            if not ELEVEN_API_KEY.strip() or ELEVEN_API_KEY == "ELEVENLABS_KEY_CUA_BAN":
                log_event("voice", "Missing ELEVEN_API_KEY")
                print("🔇 Chưa có ELEVEN_API_KEY")
                return None
            tone = _detect_tone_from_text(text)
            profile = VOICE_TONE_PROFILES.get(tone, VOICE_TONE_PROFILES["default"])
            model_id = self._pick_tts_model(text)
            clean_text = self._prepare_tts_request_text(text)
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
            data = {
                "text": clean_text,
                "model_id": model_id,
                "voice_settings": profile,
            }
            cached_path = self._lookup_voice_cache(text, ELEVEN_OUTPUT_FORMAT)
            if cached_path:
                self._increment_state("cache_hits")
                log_event("voice", f"Voice cache hit: {cached_path.name}")
                return str(cached_path)
            if self._should_cache_text(text):
                self._increment_state("cache_misses")
            response, output_format = self._post_tts(url, data, ELEVEN_OUTPUT_FORMAT)
            if response is None:
                return None
            suffix = self._suffix_for_output_format(output_format)
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(response.content)
            tmp.close()
            cached_path = self._write_voice_cache(text, output_format, response.content)
            if cached_path:
                try:
                    os.remove(tmp.name)
                except Exception:
                    pass
                log_event("voice", f"ElevenLabs audio cached: format={output_format}, suffix={suffix}, bytes={len(response.content)}")
                return str(cached_path)
            log_event("voice", f"ElevenLabs audio saved: format={output_format}, suffix={suffix}, bytes={len(response.content)}")
            return tmp.name
        except Exception as exc:
            log_event("voice", f"ElevenLabs request failed: {exc}")
            print("🔇 lỗi TTS:", exc)
            return None

    def tts_to_pcm16(self, text, *, sample_rate=16000):
        """Render Nana speech as headerless mono signed 16-bit PCM."""
        if sample_rate not in (16000, 22050, 24000, 44100):
            raise ValueError("unsupported PCM sample rate")
        if not text or not str(text).strip():
            return b""
        if not ELEVEN_API_KEY.strip() or ELEVEN_API_KEY == "ELEVENLABS_KEY_CUA_BAN":
            log_event("voice", "Missing ELEVEN_API_KEY for PCM output")
            return None

        tone = _detect_tone_from_text(text)
        profile = VOICE_TONE_PROFILES.get(tone, VOICE_TONE_PROFILES["default"])
        clean_text = self._prepare_tts_request_text(str(text))
        output_format = f"pcm_{sample_rate}"
        response, actual_format = self._post_tts(
            f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
            {
                "text": clean_text,
                "model_id": self._pick_tts_model(str(text)),
                "voice_settings": profile,
            },
            output_format,
        )
        if response is None:
            return None
        if actual_format != output_format:
            log_event(
                "voice",
                f"Presence-node PCM rejected fallback format: {actual_format}",
            )
            return None
        payload = bytes(response.content)
        if not payload or len(payload) % 2:
            log_event("voice", "Presence-node PCM response had invalid byte length")
            return None
        return payload

    def _tts_post_tts_with_model(self, text, profile, model_id):
        clean_text = _prepare_tts_text(text)
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
        data = {
            "text": clean_text,
            "model_id": model_id,
            "voice_settings": profile,
        }
        return self._post_tts(url, data, ELEVEN_OUTPUT_FORMAT)

    def _pick_tts_model(self, text):
        """Chỉ dùng eleven_v3 (chất lượng cao, Ba không thích flash)."""
        return "eleven_v3"

    def _tts_fetch_audio(self, text, profile):
        """Fetch raw audio bytes from ElevenLabs (no cache, no file)."""
        model_id = self._pick_tts_model(text)
        response, _ = self._tts_post_tts_with_model(text, profile, model_id)
        if response:
            return response.content
        return None

    def _tts_fetch_public_audio_once_exact(self, text, profile):
        """Fetch one exact public utterance without retries or format fallback."""

        if not ELEVEN_API_KEY.strip() or ELEVEN_API_KEY == "ELEVENLABS_KEY_CUA_BAN":
            return None, "eleven_credentials_missing"
        if not str(VOICE_ID or "").strip():
            return None, "voice_id_missing"
        payload = {
            "text": text,
            "model_id": self._pick_tts_model(text),
            "voice_settings": profile,
        }
        headers = {
            "xi-api-key": ELEVEN_API_KEY,
            "Content-Type": "application/json",
            "Accept": self._accept_for_output_format(ELEVEN_OUTPUT_FORMAT),
        }
        params = (
            {"output_format": ELEVEN_OUTPUT_FORMAT}
            if ELEVEN_OUTPUT_FORMAT
            else None
        )
        semaphore = getattr(self, "_tts_semaphore", None)
        acquired = False
        try:
            if semaphore is not None:
                semaphore.acquire()
                acquired = True
            response = self._voice_http_post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}",
                params=params,
                json=payload,
                headers=headers,
                timeout=30,
                allow_redirects=False,
            )
        except Exception as exc:
            log_event("voice", f"Public exact TTS request failed: {type(exc).__name__}")
            return None, "provider_error"
        finally:
            if acquired:
                semaphore.release()
        status = int(getattr(response, "status_code", 0) or 0)
        if status != 200:
            log_event("voice", f"Public exact TTS rejected: status={status or 'error'}")
            close = getattr(response, "close", None)
            if callable(close):
                close()
            return None, f"provider_http_{status or 'error'}"
        audio = bytes(getattr(response, "content", b"") or b"")
        close = getattr(response, "close", None)
        if callable(close):
            close()
        if not audio:
            return None, "provider_empty_audio"
        return audio, "none"

    def play_public_once(
        self,
        text,
        *,
        max_text_chars=200,
        before_provider=None,
        before_first_audio=None,
        on_first_audio=None,
        output_stream_factory=None,
    ):
        """Synchronously fetch and play one exact public reply with receipts."""

        source = text if isinstance(text, str) else ""
        segments = [source] if source else []
        valid_bound = (
            type(max_text_chars) is int
            and 1 <= max_text_chars <= 1200
        )
        if (
            not valid_bound
            or not source
            or source != source.strip()
            or len(source) > max_text_chars
        ):
            return self._record_audio_completion(
                self._audio_completion_result(
                    state="provider_error",
                    segments=segments,
                    fetched_segments=0,
                    played_segments=0,
                    original_chars=len(source),
                    played_duration_ms=0.0,
                    abort_reason="invalid_public_text",
                )
            )
        lock = getattr(self, "_public_playback_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._public_playback_lock = lock
        if not lock.acquire(blocking=False):
            return self._record_audio_completion(
                self._audio_completion_result(
                    state="aborted",
                    segments=segments,
                    fetched_segments=0,
                    played_segments=0,
                    original_chars=len(source),
                    played_duration_ms=0.0,
                    abort_reason="public_playback_busy",
                )
            )
        done = getattr(self, "_public_playback_done", None)
        if done is None:
            done = threading.Event()
            self._public_playback_done = done
        done.clear()
        try:
            if self._shutdown_requested():
                result = self._audio_completion_result(
                    state="cancelled",
                    segments=segments,
                    fetched_segments=0,
                    played_segments=0,
                    original_chars=len(source),
                    played_duration_ms=0.0,
                    abort_reason="shutdown_cancelled",
                )
                return self._record_audio_completion(result)
            profile = VOICE_TONE_PROFILES.get(
                _detect_tone_from_text(source),
                VOICE_TONE_PROFILES["default"],
            )
            if before_provider is not None:
                try:
                    provider_allowed = before_provider() is True
                except Exception:
                    provider_allowed = False
                if not provider_allowed:
                    result = self._audio_completion_result(
                        state="cancelled",
                        segments=segments,
                        fetched_segments=0,
                        played_segments=0,
                        original_chars=len(source),
                        played_duration_ms=0.0,
                        abort_reason="before_provider_rejected",
                    )
                    return self._record_audio_completion(result)
            audio, fetch_reason = self._tts_fetch_public_audio_once_exact(
                source,
                profile,
            )
            if not audio:
                result = self._audio_completion_result(
                    state="provider_error",
                    segments=segments,
                    fetched_segments=0,
                    played_segments=0,
                    original_chars=len(source),
                    played_duration_ms=0.0,
                    abort_reason=fetch_reason,
                )
                return self._record_audio_completion(result)
            if self._shutdown_requested():
                result = self._audio_completion_result(
                    state="cancelled",
                    segments=segments,
                    fetched_segments=1,
                    played_segments=0,
                    original_chars=len(source),
                    played_duration_ms=0.0,
                    abort_reason="shutdown_cancelled",
                )
                return self._record_audio_completion(result)
            prepared = self.lipsync.prepare_audio_bytes(audio)
            if prepared is None:
                result = self._audio_completion_result(
                    state="provider_error",
                    segments=segments,
                    fetched_segments=1,
                    played_segments=0,
                    original_chars=len(source),
                    played_duration_ms=0.0,
                    abort_reason="decode_error",
                )
                return self._record_audio_completion(result)
            playback = self.lipsync.play_prepared_audio_receipted(
                prepared,
                before_first_audio=before_first_audio,
                on_first_audio=on_first_audio,
                emit_mouth=False,
                output_stream_factory=output_stream_factory,
            )
            state = "completed" if playback.completed else (
                "cancelled"
                if playback.abort_reason in {
                    "before_first_audio_rejected",
                    "shutdown_cancelled",
                }
                else "aborted"
            )
            result = self._audio_completion_result(
                state=state,
                segments=segments,
                fetched_segments=1,
                played_segments=1 if playback.completed else 0,
                original_chars=len(source),
                played_duration_ms=playback.played_duration_ms,
                abort_reason=playback.abort_reason,
                stop_confirmed=playback.stop_confirmed,
            )
            self._update_state(
                last_tts_strategy="public_exact_once",
                last_tts_text_len=len(source),
                last_tts_chunks=1,
                last_tts_voice_mode="public_full",
                last_tts_audio_paths=1,
            )
            return self._record_audio_completion(result)
        finally:
            done.set()
            lock.release()

    def cancel_public_playback(self, timeout=2.0):
        """Best-effort stop with an explicit confirmation result."""

        try:
            self.lipsync.cancel_receipted_playback()
        except Exception:
            return False
        done = getattr(self, "_public_playback_done", None)
        return True if done is None else bool(done.wait(timeout=max(0.0, float(timeout))))

    def _post_tts(self, url, data, output_format):
        # Acquire the global semaphore so we never exceed ElevenLabs'
        # concurrent-request cap. Released in the finally block so it
        # returns to the pool on any error path (including retries).
        self._tts_semaphore.acquire()
        try:
            formats_to_try = [output_format]
            if output_format:
                formats_to_try.append(None)

            # Outer loop: retry on 429 (concurrent_limit_exceeded / rate_limit).
            # Inner loop: walk the format fallback list.
            backoff = 0.5
            for attempt in range(ELEVENLABS_RATE_LIMIT_RETRIES + 1):
                rate_limited = False
                for candidate in formats_to_try:
                    headers = {
                        "xi-api-key": ELEVEN_API_KEY,
                        "Content-Type": "application/json",
                        "Accept": self._accept_for_output_format(candidate),
                    }
                    params = {"output_format": candidate} if candidate else None
                    started_at = time.perf_counter()
                    response = self._voice_http_post(
                        url,
                        params=params,
                        json=data,
                        headers=headers,
                        timeout=30,
                    )
                    self._update_state(last_tts_request_ms=round((time.perf_counter() - started_at) * 1000, 1))
                    if response.status_code == 200:
                        return response, candidate or "default"

                    body = response.text
                    log_event("voice", f"ElevenLabs error {response.status_code} format={candidate}: {body[:300]}")
                    # 429 = rate-limit / concurrent-limit → backoff and retry.
                    if response.status_code == 429 and attempt < ELEVENLABS_RATE_LIMIT_RETRIES:
                        rate_limited = True
                        break  # out of format loop; retry whole call after backoff
                    if "output_format_not_allowed" in body and candidate:
                        print(f"🔇 ElevenLabs không cho format {candidate}, thử format mặc định...")
                        continue

                    print("🔇 lỗi TTS:", body)
                    return None, None

                if rate_limited:
                    log_event("voice", f"ElevenLabs rate-limited, backing off {backoff:.1f}s (attempt {attempt+1}/{ELEVENLABS_RATE_LIMIT_RETRIES})")
                    print(f"⏳ ElevenLabs rate-limited, đợi {backoff:.1f}s rồi thử lại...")
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 4.0)
                    continue

            return None, None
        finally:
            self._tts_semaphore.release()

    def build_streaming_dry_run_packet(self, text, reason="manual_probe"):
        raw = (text or "").strip()
        if not VOICE_STREAMING_DRY_RUN_ENABLED:
            action = "stream_dry_run_disabled"
            packet_reason = "streaming_dry_run_disabled"
        elif not raw:
            action = "hold_stream_dry_run"
            packet_reason = "empty_text_holds_streaming"
        elif VOICE_STREAMING_ENABLED:
            action = "streaming_requires_next_gate"
            packet_reason = "real_streaming_flag_requires_next_gate"
        else:
            action = "stream_dry_run_ready"
            packet_reason = "streaming_packet_ready_without_api_call"

        packet = {
            "action": action,
            "reason": packet_reason,
            "request_reason": reason,
            "text_len": len(raw),
            "endpoint": self._streaming_tts_url(),
            "model_id": "eleven_v3",
            "output_format": ELEVEN_OUTPUT_FORMAT or "default",
            "accept": self._accept_for_output_format(ELEVEN_OUTPUT_FORMAT),
            "streaming_enabled": False,
            "tts_call": False,
            "voice_say": False,
            "play_audio": False,
            "execute": False,
            "rollback_path": "tts_to_audio_paths",
        }
        self._increment_state("streaming_dry_run_packets")
        self._update_state(last_streaming_action=action, last_streaming_reason=packet_reason)
        return packet

    def _streaming_tts_url(self):
        return f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}/stream"

    def _voice_http_post(self, url, **kwargs):
        response = _voice_http_post(url, **kwargs)
        self._update_state(
            last_voice_http_session_request_index=int(
                getattr(response, "_nana_session_request_index", 0) or 0
            ),
            last_voice_http_session_reused_hint=bool(
                getattr(response, "_nana_session_reused_hint", False)
            ),
        )
        return response

    def _normalize_cache_text(self, text):
        return " ".join(_prepare_tts_text(text or "").strip().lower().split())

    def _should_cache_text(self, text):
        normalized = self._normalize_cache_text(text)
        if not VOICE_CACHE_ENABLED or not normalized:
            return False
        return len(normalized) <= VOICE_CACHE_MAX_TEXT_CHARS

    def _voice_cache_key(self, text, output_format):
        normalized = self._normalize_cache_text(text)
        source = f"{VOICE_ID}|eleven_v3|{output_format or 'default'}|{normalized}"
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:24]

    def _voice_cache_path(self, text, output_format):
        suffix = self._suffix_for_output_format(output_format or "default")
        return VOICE_CACHE_DIR / f"{self._voice_cache_key(text, output_format)}{suffix}"

    def _lookup_voice_cache(self, text, output_format):
        if not self._should_cache_text(text):
            return None
        formats = [output_format]
        if output_format:
            formats.append("default")
        for candidate in formats:
            path = self._voice_cache_path(text, candidate)
            if path.exists() and path.stat().st_size > 0:
                return path
        return None

    def _write_voice_cache(self, text, output_format, content):
        if not self._should_cache_text(text) or not content:
            return None
        temp_path = None
        try:
            VOICE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            final_path = self._voice_cache_path(text, output_format)
            with tempfile.NamedTemporaryFile(delete=False, dir=VOICE_CACHE_DIR, suffix=final_path.suffix) as tmp:
                tmp.write(content)
                temp_path = tmp.name
            os.replace(temp_path, final_path)
            return final_path
        except Exception as exc:
            if temp_path:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            log_event("voice", f"Voice cache write failed: {exc}")
            return None

    def _is_cached_audio_path(self, audio_path):
        try:
            path = Path(audio_path).resolve()
            cache_dir = VOICE_CACHE_DIR.resolve()
            try:
                path.relative_to(cache_dir)
                return True
            except ValueError:
                return False
        except Exception:
            return False

    def _split_tts_text(self, text, max_chars=None, force_chunking=False):
        clean = (text or "").strip()
        if not clean:
            return []
        limit = int(max_chars or VOICE_CHUNK_MAX_CHARS)
        limit = max(1, limit)
        if (not VOICE_CHUNKING_ENABLED and not force_chunking) or len(clean) <= limit:
            return [clean]

        pieces = [piece.strip() for piece in _re.split(r"(?<=[.!?。！？])\s+", clean) if piece.strip()]
        if not pieces:
            pieces = [clean]

        chunks = []
        current = ""
        for piece in pieces:
            if len(piece) > limit:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(self._split_long_piece(piece, max_chars=limit))
                continue
            candidate = f"{current} {piece}".strip() if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = piece
        if current:
            chunks.append(current)
        return chunks or [clean]

    def _split_long_piece(self, text, max_chars=None):
        limit = int(max_chars or VOICE_CHUNK_MAX_CHARS)
        limit = max(1, limit)
        words = text.split()
        if not words:
            return [text[index:index + limit].strip() for index in range(0, len(text), limit) if text[index:index + limit].strip()]

        chunks = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip() if current else word
            if len(candidate) <= limit:
                current = candidate
                continue
            if current:
                chunks.append(current)
            if len(word) > limit:
                chunks.extend(word[index:index + limit] for index in range(0, len(word), limit))
                current = ""
            else:
                current = word
        if current:
            chunks.append(current)
        return chunks

    def _accept_for_output_format(self, output_format):
        if output_format and output_format.startswith("pcm_"):
            return "application/octet-stream"
        if output_format and output_format.startswith("mp3_"):
            return "audio/mpeg"
        return "audio/mpeg"

    def _suffix_for_output_format(self, output_format):
        if output_format.startswith("pcm_"):
            return ".pcm"
        if output_format.startswith("mp3_") or output_format == "default":
            return ".mp3"
        return ".audio"
