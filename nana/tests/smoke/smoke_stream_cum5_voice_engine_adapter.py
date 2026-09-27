"""Provider/audio-free smoke for the CUM5 VoiceEngine adapter."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import types
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
NANA_ROOT = ROOT / "nana"


def _namespace(name: str, directory: Path) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = [str(directory)]
    return module


def _module(name: str, **values) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__.update(values)
    return module


def _forbid_real_audio(*_args, **_kwargs):
    raise AssertionError("adapter smoke attempted real audio output")


class _FakeCallbackAbort(Exception):
    pass


class _FakeCallbackStop(Exception):
    pass


sys.modules["nana"] = _namespace("nana", NANA_ROOT)
sys.modules["nana.runtime"] = _namespace("nana.runtime", NANA_ROOT / "runtime")
sys.modules["nana.voice"] = _namespace("nana.voice", NANA_ROOT / "voice")
sys.modules["sounddevice"] = _module(
    "sounddevice",
    CallbackAbort=_FakeCallbackAbort,
    CallbackStop=_FakeCallbackStop,
    OutputStream=_forbid_real_audio,
    stop=lambda: None,
)
sys.modules["keyboard"] = _module("keyboard", is_pressed=lambda _key: False)
sys.modules["nana.runtime.logger"] = _module(
    "nana.runtime.logger",
    log_event=lambda *_args, **_kwargs: None,
)
sys.modules["nana.config"] = _module(
    "nana.config",
    BASE_DIR=NANA_ROOT / "%TEMP%" / "cum5-adapter-smoke",
    DEBUG_NO_TTS=True,
    ELEVEN_API_KEY="",
    ELEVEN_OUTPUT_FORMAT="mp3_44100_128",
    MIC_DEVICE_INDEX=None,
    MIN_VOICE_SECONDS=0.25,
    PRIVATE_VOICE_OVERLAP_COALESCE_MS=150,
    PRIVATE_VOICE_OVERLAP_ENABLED=False,
    PRIVATE_VOICE_OVERLAP_MAX_CHARS=140,
    PRIVATE_VOICE_OVERLAP_MIN_CHARS=45,
    PRIVATE_VOICE_OVERLAP_PCM_ENABLED=False,
    PRIVATE_VOICE_OVERLAP_PCM_NETWORK_CHUNK_BYTES=4096,
    PRIVATE_VOICE_OVERLAP_PCM_OUTPUT_FORMAT="pcm_24000",
    PRIVATE_VOICE_OVERLAP_PCM_START_BUFFER_MS=300,
    PRIVATE_VOICE_OVERLAP_PCM_TIMEOUT_S=45,
    PRIVATE_VOICE_OVERLAP_TAIL_TIMEOUT_S=35,
    PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS=240,
    PRIVATE_VOICE_TTD_CHUNK_TARGET_CHARS=120,
    PRIVATE_VOICE_TTD_CAPTURE_DIR=NANA_ROOT / "%TEMP%" / "cum5-ttd-capture",
    PRIVATE_VOICE_TTD_CAPTURE_ENABLED=False,
    PRIVATE_VOICE_TTD_ENABLED=False,
    PRIVATE_VOICE_TTD_INPUT_MODE="incremental",
    PRIVATE_VOICE_TTD_MIN_CHARS=40,
    PRIVATE_VOICE_TTD_MIN_WORDS=8,
    PRIVATE_VOICE_TTD_MODEL="eleven_v3",
    PRIVATE_VOICE_TTD_OUTPUT_FORMAT="pcm_24000",
    PRIVATE_VOICE_TTD_START_BUFFER_MS=300,
    PRIVATE_VOICE_TTD_TIMEOUT_S=45,
    PRESENCE_TTS_STREAMING_ENABLED=False,
    VOICE_ID="",
    VOICE_CACHE_DIR=NANA_ROOT / "%TEMP%" / "cum5-voice-cache",
    VOICE_CACHE_ENABLED=False,
    VOICE_CACHE_MAX_TEXT_CHARS=64,
    VOICE_CHUNKING_ENABLED=False,
    VOICE_CHUNK_MAX_CHARS=160,
    VOICE_HTTP_KEEPALIVE_ENABLED=False,
    VOICE_HTTP_POOL_MAXSIZE=1,
    VOICE_STREAMING_DRY_RUN_ENABLED=True,
    VOICE_STREAMING_DIRECT_ONLY=True,
    VOICE_STREAMING_ENABLED=False,
    VOICE_STREAMING_KILL_SWITCH=True,
    VOICE_STREAMING_PILOT_ENABLED=False,
    VOICE_TEST_MODE=True,
)
_SIDE_EFFECT_VIOLATIONS: list[str] = []


def _audit(event, args):
    if event == "open" and args and not isinstance(args[0], int):
        path = str(args[0]).replace("\\", "/").lower()
        mode = str(args[1]) if len(args) > 1 else ""
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
        forbidden_read = "/nana/data/" in path or path.endswith("/.env")
        forbidden_write = (
            any(marker in mode for marker in ("w", "a", "x", "+"))
            or bool(flags & write_flags)
        )
        if forbidden_read or forbidden_write:
            _SIDE_EFFECT_VIOLATIONS.append(f"{event}:{path}:{mode}")
            raise AssertionError(f"forbidden adapter smoke file access: {path}")
    if event in {"socket.connect", "subprocess.Popen", "os.system", "os.spawn"}:
        _SIDE_EFFECT_VIOLATIONS.append(event)
        raise AssertionError(f"forbidden adapter smoke side effect: {event}")


sys.addaudithook(_audit)


class _Stream:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def write(self, frame):
        if self.fail:
            raise RuntimeError("fake write failure")
        self.writes.append(np.asarray(frame).copy())
        return False


def test_lipsync_receipt_occurs_after_first_write_and_mouth_stays_off():
    from nana.voice.lipsync import LipsyncManager, PreparedAudio

    manager = LipsyncManager()
    manager._set_mouth = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("public playback touched mouth output")
    )
    stream = _Stream()
    ordering = []

    def before():
        ordering.append(("before", len(stream.writes)))
        return True

    def started(frame_bytes):
        ordering.append(("started", len(stream.writes), frame_bytes))
        return True

    prepared = PreparedAudio(np.ones(105, dtype=np.float32), 1000, 0.105)
    result = manager.play_prepared_audio_receipted(
        prepared,
        before_first_audio=before,
        on_first_audio=started,
        emit_mouth=False,
        output_stream_factory=lambda **_kwargs: stream,
    )

    assert result.completed is True, result
    assert result.first_audio_started is True
    assert result.written_frames == len(stream.writes) > 1
    assert ordering[0] == ("before", 0), ordering
    assert ordering[1][0:2] == ("started", 1), ordering
    assert ordering[1][2] > 0
    assert len([item for item in ordering if item[0] == "started"]) == 1


def test_lipsync_gate_rejection_and_write_error_never_emit_start():
    from nana.voice.lipsync import LipsyncManager, PreparedAudio

    prepared = PreparedAudio(np.ones(40, dtype=np.float32), 1000, 0.04)
    manager = LipsyncManager()
    blocked_stream = _Stream()
    starts = []
    blocked = manager.play_prepared_audio_receipted(
        prepared,
        before_first_audio=lambda: False,
        on_first_audio=lambda size: starts.append(size) or True,
        emit_mouth=False,
        output_stream_factory=lambda **_kwargs: blocked_stream,
    )
    assert blocked.completed is False
    assert blocked.abort_reason == "before_first_audio_rejected"
    assert blocked_stream.writes == [] and starts == []

    failed_stream = _Stream(fail=True)
    failed = manager.play_prepared_audio_receipted(
        prepared,
        before_first_audio=lambda: True,
        on_first_audio=lambda size: starts.append(size) or True,
        emit_mouth=False,
        output_stream_factory=lambda **_kwargs: failed_stream,
    )
    assert failed.completed is False
    assert failed.abort_reason == "playback_error"
    assert failed.stop_confirmed is False
    assert starts == []


def _bare_engine():
    from nana.voice.engine import VoiceEngine

    engine = VoiceEngine.__new__(VoiceEngine)
    engine.state_lock = threading.Lock()
    engine.state = {"last_error": None, "last_tts_max_seam_wait_ms": 0.0}
    engine._shutdown_event = threading.Event()
    engine._shutdown_lock = threading.Lock()
    engine._public_playback_lock = threading.Lock()
    engine._public_playback_done = threading.Event()
    engine._public_playback_done.set()
    engine._tts_semaphore = threading.BoundedSemaphore(1)
    return engine


def test_exact_provider_path_is_one_request_without_rewrite_or_fallback():
    import nana.voice.engine as module

    engine = _bare_engine()
    old = (module.ELEVEN_API_KEY, module.VOICE_ID, module.ELEVEN_OUTPUT_FORMAT)
    module.ELEVEN_API_KEY = "fake-key"
    module.VOICE_ID = "fake-voice"
    module.ELEVEN_OUTPUT_FORMAT = "mp3_44100_128"
    try:
        for status, content in ((200, b"audio"), (429, b""), (400, b"")):
            calls = []

            def fake_post(url, **kwargs):
                calls.append((url, kwargs))
                return SimpleNamespace(status_code=status, content=content)

            engine._voice_http_post = fake_post
            exact = "[laughs] Chuỗi này phải giữ nguyên 100%."
            audio, reason = engine._tts_fetch_public_audio_once_exact(
                exact,
                {"stability": 0.5},
            )
            assert len(calls) == 1, calls
            assert calls[0][1]["json"]["text"] == exact
            assert calls[0][1]["allow_redirects"] is False
            assert calls[0][1]["params"] == {"output_format": "mp3_44100_128"}
            if status == 200:
                assert audio == b"audio" and reason == "none"
            else:
                assert audio is None and reason == f"provider_http_{status}"
    finally:
        module.ELEVEN_API_KEY, module.VOICE_ID, module.ELEVEN_OUTPUT_FORMAT = old


class _ReceiptedLipsync:
    def __init__(self):
        self.calls = []

    def prepare_audio_bytes(self, audio):
        self.calls.append(("prepare", audio))
        return SimpleNamespace(data=audio, samplerate=44100, duration_seconds=0.1)

    def play_prepared_audio_receipted(self, prepared, **kwargs):
        from nana.voice.lipsync import ReceiptedPlaybackResult

        self.calls.append(("play", prepared.data, kwargs["emit_mouth"]))
        assert kwargs["emit_mouth"] is False
        assert kwargs["before_first_audio"]() is True
        assert kwargs["on_first_audio"](320) is True
        return ReceiptedPlaybackResult(True, True, 1, 4410, 100.0, "none", True)

    def cancel_receipted_playback(self):
        self.calls.append(("cancel",))


def test_engine_public_once_is_nonqueued_exact_and_per_call():
    engine = _bare_engine()
    lipsync = _ReceiptedLipsync()
    engine.lipsync = lipsync
    bomb = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("public exact path entered a forbidden legacy route")
    )
    engine.say = bomb
    engine._presence_pcm_outputs_if_available = bomb
    engine._play_fallback_phrase = bomb
    engine._lookup_voice_cache = bomb
    engine.tts_to_wav = bomb
    fetches = []
    engine._tts_fetch_public_audio_once_exact = (
        lambda text, profile: (fetches.append(text) or b"audio", "none")
    )
    starts = []
    text = "[happy] Nana giữ đúng câu đã đăng."
    result = engine.play_public_once(
        text,
        before_first_audio=lambda: True,
        on_first_audio=lambda size: starts.append(size) or True,
    )
    assert fetches == [text]
    assert starts == [320]
    assert result.state == "completed" and result.audio_completed is True
    assert result.requested_segments == result.fetched_segments == result.played_segments == 1
    assert result.remaining_chars == 0
    assert lipsync.calls == [("prepare", b"audio"), ("play", b"audio", False)]

    blocked = _bare_engine()
    blocked.lipsync = _ReceiptedLipsync()
    blocked._tts_fetch_public_audio_once_exact = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("provider called after dynamic gate closed")
    )
    blocked_result = blocked.play_public_once(
        text,
        before_provider=lambda: False,
        before_first_audio=lambda: True,
        on_first_audio=lambda _size: True,
    )
    assert blocked_result.state == "cancelled"
    assert blocked_result.abort_reason == "before_provider_rejected"


def _request():
    from nana.runtime.public_context_boundary import PublicEventScope
    from nana.runtime.public_delivery_state import PublicDeliveryRecord
    from nana.runtime.public_identity import CanonicalPublicIdentity
    from nana.runtime.stream_cum0_contract import compute_correlation_id
    from nana.runtime.stream_cum2_response import ResponseArtifact
    from nana.runtime.stream_cum5_voice_playback import PublicVoicePlaybackRequest

    identity = CanonicalPublicIdentity("youtube", "viewer", "youtube:viewer")
    scope = PublicEventScope(
        "youtube",
        "yt-live-chat-room-1",
        "yt-stream-session-1",
        "LCC-provider-message-1",
        "Viewer",
        identity,
    )
    text = "Nana nói đúng một câu."
    artifact = ResponseArtifact(
        scope,
        compute_correlation_id(scope),
        0,
        "ingress-attempt",
        "output",
        text,
        "nana-public",
        10.0,
    )
    generated = PublicDeliveryRecord(
        scope.event_id, "output", "generated", "delivery-attempt", 0,
        text[:96], scope, 11.0,
    )
    published = replace(generated, state="published", revision=1, updated_at=12.0)
    import hashlib

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return PublicVoicePlaybackRequest(
        artifact,
        generated,
        published,
        "published",
        "youtube_acknowledged",
        "provider-message",
        artifact.correlation_id,
        "cum5-playback",
        digest,
        "local_speaker",
        13.0,
    )


def _request_for_mode(text: str, delivery_mode: str):
    import hashlib

    request = _request()
    artifact = replace(request.artifact, text=text)
    generated = replace(
        request.generated_record,
        text_preview=text[:96],
        delivery_mode=delivery_mode,
    )
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if delivery_mode == "voice_only":
        return replace(
            request,
            artifact=artifact,
            generated_record=generated,
            published_record=None,
            publish_status="",
            publish_reason="",
            provider_message_id="",
            playback_id=f"voice-bound-{len(text)}",
            content_sha256=digest,
        )
    return replace(
        request,
        artifact=artifact,
        generated_record=generated,
        published_record=replace(
            request.published_record,
            text_preview=text[:96],
        ),
        playback_id=f"legacy-bound-{len(text)}",
        content_sha256=digest,
    )


def test_adapter_applies_mode_bound_to_real_engine_before_provider():
    import nana.voice.engine as module

    from nana.runtime.stream_cum5_voice_engine_adapter import VoiceEnginePublicPlaybackPort

    default_engine = _bare_engine()
    default_provider_calls = []
    default_engine._tts_fetch_public_audio_once_exact = (
        lambda *_args, **_kwargs: default_provider_calls.append(True)
    )
    default_result = default_engine.play_public_once("d" * 201)
    invalid_bound_result = default_engine.play_public_once(
        "bounded",
        max_text_chars=1201,
    )
    assert default_result.state == "provider_error"
    assert default_result.abort_reason == "invalid_public_text"
    assert invalid_bound_result.state == "provider_error"
    assert invalid_bound_result.abort_reason == "invalid_public_text"
    assert default_provider_calls == []

    def run(request):
        engine = _bare_engine()
        engine.lipsync = _ReceiptedLipsync()
        provider_calls = []

        def fake_post(url, **kwargs):
            provider_calls.append((url, kwargs))
            return SimpleNamespace(status_code=200, content=b"audio")

        engine._voice_http_post = fake_post
        starts = []
        port = VoiceEnginePublicPlaybackPort(
            engine_factory=lambda: engine,
            clock=lambda: 14.0,
        )
        result = port.play(
            request,
            should_continue=lambda: True,
            on_first_audio=lambda evidence: starts.append(evidence) or True,
        )
        return result, provider_calls, starts, engine.lipsync.calls

    old = (module.ELEVEN_API_KEY, module.VOICE_ID, module.ELEVEN_OUTPUT_FORMAT)
    module.ELEVEN_API_KEY = "fake-key"
    module.VOICE_ID = "fake-voice"
    module.ELEVEN_OUTPUT_FORMAT = "mp3_44100_128"
    try:
        legacy_result, legacy_calls, legacy_starts, legacy_sink = run(
            _request_for_mode("l" * 201, "published_then_voice")
        )
        voice_text = "v" * 1200
        voice_result, voice_calls, voice_starts, voice_sink = run(
            _request_for_mode(voice_text, "voice_only")
        )
        oversized_result, oversized_calls, oversized_starts, oversized_sink = run(
            _request_for_mode("x" * 1201, "voice_only")
        )
    finally:
        module.ELEVEN_API_KEY, module.VOICE_ID, module.ELEVEN_OUTPUT_FORMAT = old

    assert legacy_result.state == "failed"
    assert legacy_result.abort_reason == "invalid_public_text"
    assert legacy_calls == legacy_starts == legacy_sink == []
    assert voice_result.state == "completed" and voice_result.audio_completed is True
    assert len(voice_calls) == 1 and voice_calls[0][1]["json"]["text"] == voice_text
    assert len(voice_starts) == 1
    assert voice_sink == [("prepare", b"audio"), ("play", b"audio", False)]
    assert oversized_result.state == "failed"
    assert oversized_result.abort_reason == "invalid_public_text"
    assert oversized_calls == oversized_starts == oversized_sink == []


def test_request_requires_explicit_voice_mode_for_absent_publication():
    request = _request()
    rejected_legacy = False
    try:
        replace(
            request,
            published_record=None,
            publish_status="",
            publish_reason="",
            provider_message_id="",
        )
    except ValueError:
        rejected_legacy = True
    assert rejected_legacy is True

    voice_generated = replace(
        request.generated_record,
        delivery_mode="voice_only",
    )
    voice = replace(
        request,
        generated_record=voice_generated,
        published_record=None,
        publish_status="",
        publish_reason="",
        provider_message_id="",
    )
    assert voice.generated_record.delivery_mode == "voice_only"
    assert voice.published_record is None

    rejected_voice_publication = False
    try:
        replace(
            request,
            generated_record=voice_generated,
        )
    except ValueError:
        rejected_voice_publication = True
    assert rejected_voice_publication is True


class _AdapterEngine:
    def __init__(self, *, gate_allowed=True):
        self.calls = []
        self.gate_allowed = gate_allowed
        self.cancelled = 0
        self.provider_calls = 0

    def play_public_once(self, text, **kwargs):
        from nana.voice.engine import AudioCompletionResult

        self.calls.append(text)
        if not kwargs["before_provider"]():
            return AudioCompletionResult(
                "cancelled", False, 1, 0, 0, 1, len(text), len(text),
                0.0, "before_provider_rejected",
            )
        self.provider_calls += 1
        if not kwargs["before_first_audio"]() or not self.gate_allowed:
            return AudioCompletionResult(
                "cancelled", False, 1, 1, 0, 1, len(text), len(text),
                0.0, "before_first_audio_rejected",
            )
        assert kwargs["on_first_audio"](320) is True
        return AudioCompletionResult(
            "completed", True, 1, 1, 1, 0, len(text), 0, 100.0, "none",
        )

    def cancel_public_playback(self, timeout=2.0):
        self.cancelled += 1
        return True


class _UncertainEngine(_AdapterEngine):
    def play_public_once(self, text, **kwargs):
        from nana.voice.engine import AudioCompletionResult

        self.calls.append(text)
        assert kwargs["before_provider"]() is True
        self.provider_calls += 1
        assert kwargs["before_first_audio"]() is True
        assert kwargs["on_first_audio"](320) is True
        return AudioCompletionResult(
            "aborted",
            False,
            1,
            1,
            0,
            1,
            len(text),
            len(text),
            20.0,
            "playback_error",
            False,
        )

    def cancel_public_playback(self, timeout=2.0):
        self.cancelled += 1
        return False


def test_adapter_maps_one_engine_result_and_correlated_start():
    from nana.runtime.stream_cum5_voice_engine_adapter import VoiceEnginePublicPlaybackPort

    request = _request()
    engine = _AdapterEngine()
    created = []
    port = VoiceEnginePublicPlaybackPort(
        engine_factory=lambda: created.append(engine) or engine,
        clock=lambda: 14.0,
    )
    starts = []
    assert port.ready().ready is True
    assert created == []
    result = port.play(
        request,
        should_continue=lambda: True,
        on_first_audio=lambda evidence: starts.append(evidence) or True,
    )
    assert created == [engine] and engine.calls == [request.artifact.text]
    assert engine.provider_calls == 1
    assert len(starts) == 1
    assert starts[0].playback_id == request.playback_id
    assert starts[0].content_sha256 == request.content_sha256
    assert result.state == "completed" and result.audio_completed is True
    assert result.stop_confirmed is True
    assert result.playback_id == request.playback_id


def test_adapter_rechecks_gate_before_provider():
    from nana.runtime.stream_cum5_voice_engine_adapter import VoiceEnginePublicPlaybackPort

    request = _request()
    engine = _AdapterEngine()
    checks = iter((True, True, False))
    port = VoiceEnginePublicPlaybackPort(
        engine_factory=lambda: engine,
        clock=lambda: 14.0,
    )
    starts = []
    result = port.play(
        request,
        should_continue=lambda: next(checks),
        on_first_audio=lambda evidence: starts.append(evidence) or True,
    )
    assert result.state == "cancelled"
    assert result.abort_reason == "before_provider_rejected"
    assert result.fetched_segments == result.played_segments == 0
    assert engine.provider_calls == 0 and starts == []


def test_adapter_latches_pre_engine_cancel_and_cleans_factory_failure():
    from nana.runtime.stream_cum5_voice_engine_adapter import VoiceEnginePublicPlaybackPort

    request = _request()
    entered = threading.Event()
    release = threading.Event()
    factory_calls = []
    port = VoiceEnginePublicPlaybackPort(
        engine_factory=lambda: factory_calls.append(True) or _AdapterEngine(),
        clock=lambda: 14.0,
    )
    result_box = {}

    def should_continue():
        entered.set()
        release.wait(timeout=2.0)
        return True

    thread = threading.Thread(
        target=lambda: result_box.setdefault(
            "result",
            port.play(
                request,
                should_continue=should_continue,
                on_first_audio=lambda _evidence: True,
            ),
        )
    )
    thread.start()
    assert entered.wait(timeout=1.0)
    stopped = port.cancel(request.playback_id)
    release.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert stopped.confirmed_stopped is True
    assert result_box["result"].state == "cancelled"
    assert factory_calls == []

    failing = VoiceEnginePublicPlaybackPort(
        engine_factory=lambda: (_ for _ in ()).throw(RuntimeError("factory failed")),
        clock=lambda: 14.0,
    )
    first = failing.play(
        request,
        should_continue=lambda: True,
        on_first_audio=lambda _evidence: True,
    )
    second = failing.play(
        request,
        should_continue=lambda: True,
        on_first_audio=lambda _evidence: True,
    )
    assert first.state == second.state == "ambiguous"
    assert first.stop_confirmed is second.stop_confirmed is False
    assert failing.cancel(request.playback_id).confirmed_stopped is False


def test_public_engine_can_skip_private_worker_and_avatar_callback():
    from nana.voice.engine import VoiceEngine

    engine = VoiceEngine(avatar_mouth_enabled=False, start_worker=False)
    try:
        assert engine.worker is None
        assert engine.avatar_mouth_stream is None
        assert engine.lipsync._pcm_level_callback is None
    finally:
        engine.shutdown()


def test_controller_and_adapter_complete_one_correlated_attempt():
    from nana.runtime.stream_cum3_youtube_publish import YouTubePublishResult
    from nana.runtime.stream_cum5_voice_engine_adapter import VoiceEnginePublicPlaybackPort
    from nana.runtime.stream_cum5_voice_playback import PublicVoicePlaybackController
    from nana.runtime.stream_state import (
        AvatarEnergy,
        ErrorType,
        InteractionTone,
        StreamPolicy,
        StreamState,
        ViewerExpectation,
    )

    request = _request()
    publish = YouTubePublishResult(
        "published",
        "youtube_acknowledged",
        request.published_record,
        request.provider_message_id,
        200,
        request.correlation_id,
        request.generated_record,
        request.published_record,
    )
    policy = StreamPolicy(
        StreamState.LIVE_ACTIVE,
        "cum5-adapter-smoke",
        False,
        False,
        False,
        0,
        InteractionTone.QUIET,
        AvatarEnergy.LOW,
        ViewerExpectation.MUTED,
        ErrorType.NONE,
        True,
        True,
        False,
    )

    class PolicySource:
        def get_policy(self):
            return policy

    class Recorder:
        def __init__(self):
            self.records = []

        def record_delivery(self, record):
            self.records.append(record)
            return True

    engine = _AdapterEngine()
    port = VoiceEnginePublicPlaybackPort(
        engine_factory=lambda: engine,
        clock=lambda: 14.0,
    )
    recorder = Recorder()
    controller = PublicVoicePlaybackController(
        playback_port_factory=lambda: port,
        policy_source=PolicySource(),
        active_session_id=request.artifact.scope.stream_session_id,
        delivery_recorder=recorder,
    )
    flags = {
        "NANA_STREAM_CUM0_ENABLED": "1",
        "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "1",
        "NANA_STREAM_CUM2_RESPONSE_ENABLED": "1",
        "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "1",
        "NANA_STREAM_CUM4_HOST_ENABLED": "1",
        "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED": "1",
    }
    with patch.dict(os.environ, flags, clear=False):
        result = controller.play_published(
            request.artifact,
            publish,
            now=request.requested_at,
        )
    assert result.status == "delivered", result
    assert [record.state for record in recorder.records] == [
        "generated",
        "published",
        "playback_started",
        "delivered",
    ]
    assert engine.calls == [request.artifact.text]
    assert engine.provider_calls == 1


def test_controller_and_adapter_complete_generated_voice_attempt():
    from nana.runtime.stream_cum5_voice_engine_adapter import VoiceEnginePublicPlaybackPort
    from nana.runtime.stream_cum5_voice_playback import PublicVoicePlaybackController
    from nana.runtime.stream_state import (
        AvatarEnergy,
        ErrorType,
        InteractionTone,
        StreamPolicy,
        StreamState,
        ViewerExpectation,
    )

    request = _request()
    policy = StreamPolicy(
        StreamState.LIVE_ACTIVE,
        "cum5-adapter-voice-smoke",
        False,
        False,
        False,
        0,
        InteractionTone.QUIET,
        AvatarEnergy.LOW,
        ViewerExpectation.MUTED,
        ErrorType.NONE,
        True,
        True,
        False,
    )

    class PolicySource:
        def get_policy(self):
            return policy

    class Recorder:
        def __init__(self):
            self.records = []

        def record_delivery(self, record):
            self.records.append(record)
            return True

    engine = _AdapterEngine()
    port = VoiceEnginePublicPlaybackPort(
        engine_factory=lambda: engine,
        clock=lambda: 14.0,
    )
    recorder = Recorder()
    controller = PublicVoicePlaybackController(
        playback_port_factory=lambda: port,
        policy_source=PolicySource(),
        active_session_id=request.artifact.scope.stream_session_id,
        delivery_recorder=recorder,
    )
    flags = {
        "NANA_STREAM_CUM0_ENABLED": "1",
        "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "1",
        "NANA_STREAM_CUM2_RESPONSE_ENABLED": "1",
        "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "0",
        "NANA_STREAM_CUM4_HOST_ENABLED": "1",
        "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED": "1",
    }
    with patch.dict(os.environ, flags, clear=False):
        result = controller.play_generated(
            request.artifact,
            attempt_id="voice-adapter-attempt",
            now=request.requested_at,
        )
    assert result.status == "delivered", result
    assert [record.state for record in recorder.records] == [
        "generated",
        "playback_started",
        "delivered",
    ]
    assert [record.revision for record in recorder.records] == [0, 1, 2]
    assert all(record.delivery_mode == "voice_only" for record in recorder.records)
    assert engine.calls == [request.artifact.text]
    assert engine.provider_calls == 1


def test_uncertain_sink_never_becomes_interrupted_or_delivered():
    from nana.runtime.stream_cum3_youtube_publish import YouTubePublishResult
    from nana.runtime.stream_cum5_voice_engine_adapter import VoiceEnginePublicPlaybackPort
    from nana.runtime.stream_cum5_voice_playback import PublicVoicePlaybackController
    from nana.runtime.stream_state import (
        AvatarEnergy, ErrorType, InteractionTone, StreamPolicy, StreamState,
        ViewerExpectation,
    )

    request = _request()
    publish = YouTubePublishResult(
        "published", "youtube_acknowledged", request.published_record,
        request.provider_message_id, 200, request.correlation_id,
        request.generated_record, request.published_record,
    )
    policy = StreamPolicy(
        StreamState.LIVE_ACTIVE, "uncertain-smoke", False, False, False, 0,
        InteractionTone.QUIET, AvatarEnergy.LOW, ViewerExpectation.MUTED,
        ErrorType.NONE, True, True, False,
    )

    class PolicySource:
        def get_policy(self):
            return policy

    class Recorder:
        def __init__(self):
            self.records = []
        def record_delivery(self, record):
            self.records.append(record)
            return True

    engine = _UncertainEngine()
    port = VoiceEnginePublicPlaybackPort(
        engine_factory=lambda: engine,
        clock=lambda: 14.0,
    )
    recorder = Recorder()
    controller = PublicVoicePlaybackController(
        playback_port_factory=lambda: port,
        policy_source=PolicySource(),
        active_session_id=request.artifact.scope.stream_session_id,
        delivery_recorder=recorder,
    )
    flags = {
        "NANA_STREAM_CUM0_ENABLED": "1",
        "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "1",
        "NANA_STREAM_CUM2_RESPONSE_ENABLED": "1",
        "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "1",
        "NANA_STREAM_CUM4_HOST_ENABLED": "1",
        "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED": "1",
    }
    with patch.dict(os.environ, flags, clear=False):
        result = controller.play_published(
            request.artifact, publish, now=request.requested_at,
        )
    assert result.status == "halted", result
    assert result.delivery_record.state == "playback_started"
    assert [record.state for record in recorder.records] == [
        "generated", "published", "playback_started",
    ]
    assert engine.cancelled == 1


def main():
    tests = (
        test_lipsync_receipt_occurs_after_first_write_and_mouth_stays_off,
        test_lipsync_gate_rejection_and_write_error_never_emit_start,
        test_exact_provider_path_is_one_request_without_rewrite_or_fallback,
        test_engine_public_once_is_nonqueued_exact_and_per_call,
        test_adapter_maps_one_engine_result_and_correlated_start,
        test_adapter_rechecks_gate_before_provider,
        test_adapter_latches_pre_engine_cancel_and_cleans_factory_failure,
        test_request_requires_explicit_voice_mode_for_absent_publication,
        test_adapter_applies_mode_bound_to_real_engine_before_provider,
        test_public_engine_can_skip_private_worker_and_avatar_callback,
        test_controller_and_adapter_complete_one_correlated_attempt,
        test_controller_and_adapter_complete_generated_voice_attempt,
        test_uncertain_sink_never_becomes_interrupted_or_delivered,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    assert _SIDE_EFFECT_VIOLATIONS == [], _SIDE_EFFECT_VIOLATIONS
    print(f"smoke_stream_cum5_voice_engine_adapter: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
