"""Offline smoke for the bounded one-turn CUM0-CUM5 owner-live runner."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NOW = 1_700_002_000.0
FLAGS = {
    "NANA_STREAM_CUM0_ENABLED": "1",
    "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "1",
    "NANA_STREAM_CUM2_RESPONSE_ENABLED": "1",
    "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "1",
    "NANA_STREAM_CUM4_HOST_ENABLED": "1",
    "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED": "1",
}


def _message(message_id: str, text: str, *, author_id: str = "yt-viewer") -> dict:
    published = datetime.fromtimestamp(NOW, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "id": message_id,
        "snippet": {
            "type": "textMessageEvent",
            "liveChatId": "yt-owner-live-room",
            "authorChannelId": author_id,
            "publishedAt": published,
            "displayMessage": text,
        },
        "authorDetails": {"displayName": "Owner"},
    }


def _policy():
    from nana.runtime.stream_state import (
        AvatarEnergy, ErrorType, InteractionTone, StreamPolicy, StreamState,
        ViewerExpectation,
    )

    return StreamPolicy(
        state=StreamState.LIVE_ACTIVE, reason="owner-live-smoke", can_proactive=False,
        can_auto_send=False, can_use_private_memory=False, proactive_budget=0,
        interaction_tone=InteractionTone.QUIET, avatar_energy=AvatarEnergy.LOW,
        viewer_expectation=ViewerExpectation.MUTED, error_type=ErrorType.NONE,
        can_reply=True, can_speak=True, can_avatar=False,
    )


class _PolicySource:
    def get_policy(self):
        return _policy()


class _Social:
    def observe(self, **_kwargs):
        from nana.runtime.social_session import SocialDecision

        return SocialDecision(
            action="full_reply", reason="owner-live-smoke", event_type="text",
            chat_velocity=0.0, priority_score=1, should_call_llm=True, reply_text="",
        )


class _Model:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return "This is exactly one public reply.", "complete"


class _Sender:
    def __init__(self):
        self.calls = []

    def send_text(self, live_chat_id, text):
        from nana.runtime.stream_cum3_youtube_publish import YouTubeSendResult

        self.calls.append((live_chat_id, text))
        return YouTubeSendResult("published", "youtube_acknowledged", 200, "yt-nana-reply-1")


class _InputSender:
    def __init__(self):
        self.calls = []

    def send_text(self, live_chat_id, text):
        from nana.runtime.stream_cum3_youtube_publish import YouTubeSendResult

        self.calls.append((live_chat_id, text))
        return YouTubeSendResult("published", "youtube_acknowledged", 200, "yt-owner-question-1")


class _Transport:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def list_messages(self, live_chat_id, *, page_token=None):
        self.calls.append((live_chat_id, page_token))
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


class _Recorder:
    def __init__(self):
        self.records = []

    def record_delivery(self, record):
        self.records.append(record)
        return True


class _DelegatingRecorder:
    def __init__(self):
        self.calls = []

    def record_delivery(self, record):
        self.calls.append(record)
        return True


class _Port:
    def __init__(self, mode="completed"):
        self.calls = []
        self.mode = mode

    def ready(self):
        from nana.runtime.stream_cum5_voice_playback import PlaybackReadiness

        return PlaybackReadiness(True, "ready")

    def play(self, request, *, should_continue, on_first_audio):
        from nana.runtime.stream_cum5_voice_playback import AudioCompletionResult, FirstAudioEvidence

        assert should_continue() is True
        self.calls.append(request)
        assert on_first_audio(FirstAudioEvidence(
            request.playback_id, request.content_sha256, NOW + 0.1, 128,
        )) is True
        if self.mode == "interrupted":
            return AudioCompletionResult(
                "interrupted", False, 1, 1, 0, 1, len(request.artifact.text), 3,
                40.0, "playback_error", request.playback_id, request.content_sha256, NOW + 0.2, True,
            )
        return AudioCompletionResult(
            "completed", True, 1, 1, 1, 0, len(request.artifact.text), 0,
            100.0, "none", request.playback_id, request.content_sha256, NOW + 0.2, True,
        )

    def cancel(self, _playback_id):
        from nana.runtime.stream_cum5_voice_playback import PlaybackStopResult

        return PlaybackStopResult("confirmed_stopped", "stopped")


class _HostProbe:
    def __init__(self, host):
        self.host = host
        self.ingest_calls = []
        self.process_calls = 0

    def ingest_response(self, response, **kwargs):
        self.ingest_calls.append((response, kwargs))
        return self.host.ingest_response(response, **kwargs)

    def process_next(self, **kwargs):
        self.process_calls += 1
        return self.host.process_next(**kwargs)

    def snapshot(self):
        return self.host.snapshot()


def _host_and_controller(*, port=None):
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.stream_cum0_contract import StreamContractLedger
    from nana.runtime.stream_cum2_response import PublicResponseGenerator
    from nana.runtime.stream_cum3_youtube_publish import YouTubeCum3Publisher
    from nana.runtime.stream_cum4_host import YouTubeTextHost
    from nana.runtime.stream_cum5_voice_playback import PublicVoicePlaybackController
    from nana.runtime.youtube_chat_cum1 import YouTubeChatCum1, YouTubeLiveSession
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress

    model, sender, port, recorder = _Model(), _Sender(), port or _Port(), _Recorder()
    host = _HostProbe(YouTubeTextHost(
        cum1=YouTubeChatCum1(
            session=YouTubeLiveSession("yt-owner-live-room", "yt-owner-live-session"),
            policy_source=_PolicySource(), ingress=YouTubeChatIngress(actionable_limit=1),
            ledger=StreamContractLedger(),
        ),
        generator=PublicResponseGenerator(caller=model, session_context=SocialSessionCache()),
        publisher=YouTubeCum3Publisher(sender=sender, min_send_interval_seconds=0),
        social_session=_Social(), min_publish_interval_seconds=0,
    ))
    controller = PublicVoicePlaybackController(
        playback_port_factory=lambda: port, policy_source=_PolicySource(),
        active_session_id="yt-owner-live-session", delivery_recorder=recorder,
    )
    return host, controller, model, sender, port, recorder


def test_gate_off_does_not_inject_or_create_playback_side_effects() -> None:
    from nana.tools.canary_stream_cum5_owner_live import run_owner_live_turn

    input_sender = _InputSender()
    with patch.dict(os.environ, {**FLAGS, "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED": "0"}, clear=False):
        result = run_owner_live_turn(
            video_id="owner-live-video", marker="OWNER-LIVE-RED", transport=None,
            input_sender=input_sender, host_factory=lambda _chat: (_ for _ in ()).throw(AssertionError("host created")),
            playback_factory=lambda _host: (_ for _ in ()).throw(AssertionError("playback created")),
            clock=lambda: NOW,
        )
    assert result["status"] == "disabled" and result["reason_code"] == "cum5_disabled"
    assert input_sender.calls == []


def test_scoped_flags_require_initially_off_enable_only_during_scope_and_restore_exactly() -> None:
    from nana.tools.canary_stream_cum5_owner_live import _scoped_cum_flags

    original = {name: os.environ.get(name) for name in FLAGS}
    try:
        for name in FLAGS:
            os.environ.pop(name, None)
        with _scoped_cum_flags():
            assert all(os.environ[name] == "1" for name in FLAGS)
        assert all(name not in os.environ for name in FLAGS)
        os.environ["NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED"] = "1"
        try:
            with _scoped_cum_flags():
                raise AssertionError("enabled flag entered scoped runner")
        except RuntimeError as exc:
            assert str(exc) == "flags_must_start_off"
        assert os.environ["NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED"] == "1"
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_recording_delivery_recorder_delegates_and_preserves_delivery_order() -> None:
    from nana.tools.canary_stream_cum5_owner_live import RecordingDeliveryRecorder

    delegate = _DelegatingRecorder()
    recorder = RecordingDeliveryRecorder(delegate)
    records = [type("Record", (), {"revision": revision})() for revision in (0, 1, 2, 3)]
    assert all(recorder.record_delivery(record) for record in records)
    assert delegate.calls == records
    assert recorder.records == records
    assert [record.revision for record in recorder.records] == [0, 1, 2, 3]


def test_integrity_baseline_tracks_recursive_voice_cache_and_mouth_cursor() -> None:
    from nana.tools.canary_stream_cum5_owner_live import capture_integrity, integrity_unchanged

    class Mouth:
        def __init__(self):
            self.cursor = 7

        def snapshot(self):
            return {"cursor": self.cursor}

    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        cached = root / "data" / "voice_cache" / "nested" / "reply.bin"
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"before")
        mouth = Mouth()
        baseline = capture_integrity(root, mouth)
        assert integrity_unchanged(root, mouth, baseline) == {
            "production_data_unchanged": True,
            "avatar_mouth_unchanged": True,
        }
        cached.write_bytes(b"after")
        assert integrity_unchanged(root, mouth, baseline)["production_data_unchanged"] is False
        cached.write_bytes(b"before")
        mouth.cursor += 1
        assert integrity_unchanged(root, mouth, baseline)["avatar_mouth_unchanged"] is False


def test_one_fresh_reply_runs_one_model_publish_voice_and_reflection_ingest_only() -> None:
    from nana.tools.canary_stream_cum5_owner_live import run_owner_live_turn

    host, controller, model, sender, port, recorder = _host_and_controller()
    input_sender = _InputSender()
    transport = _Transport([
        {"items": [], "nextPageToken": "boot"},
        {"items": [_message("yt-owner-question-1", "OWNER-LIVE-RED how are you?")], "nextPageToken": "question"},
        {"items": [_message("yt-nana-reply-1", "This is exactly one public reply.", author_id="nana")], "nextPageToken": "reflected"},
    ])
    with patch.dict(os.environ, FLAGS, clear=False):
        result = run_owner_live_turn(
            video_id="owner-live-video", marker="OWNER-LIVE-RED", transport=transport,
            input_sender=input_sender, host_factory=lambda _chat: host,
            playback_factory=lambda _host: controller, clock=lambda: NOW,
            live_chat_id="yt-owner-live-room",
        )
    assert result["status"] == "accepted", result
    assert result["input_count"] == result["model_count"] == result["publish_count"] == result["tts_count"] == 1
    assert result["playback_status"] == "delivered"
    assert result["delivery_revisions"] == [0, 1, 2, 3]
    assert result["reflected_self_ignored"] == 1
    assert host.process_calls == 1
    assert len(host.ingest_calls) == 3
    assert len(model.calls) == len(sender.calls) == len(port.calls) == 1
    assert [record.state for record in recorder.records] == ["generated", "published", "playback_started", "delivered"]
    assert result["avatar_calls"] == result["obs_calls"] == result["subtitle_calls"] == result["memory_writes"] == 0


def test_provider_deadlines_wait_before_question_and_reflection_without_shortening_interval() -> None:
    from nana.tools.canary_stream_cum5_owner_live import run_owner_live_turn

    class Monotonic:
        def __init__(self):
            self.value = 0.0

        def __call__(self):
            return self.value

    monotonic = Monotonic()
    sleeps = []

    def sleeper(seconds):
        sleeps.append(seconds)
        monotonic.value += seconds

    host, controller, _model, _sender, _port, _recorder = _host_and_controller()
    transport = _Transport([
        {"items": [], "nextPageToken": "boot", "pollingIntervalMillis": 10000},
        {"items": [_message("yt-owner-question-1", "OWNER-LIVE-RED cadence?")], "nextPageToken": "question", "pollingIntervalMillis": 20000},
        {"items": [_message("yt-nana-reply-1", "This is exactly one public reply.", author_id="nana")], "nextPageToken": "reflected", "pollingIntervalMillis": 20000},
    ])
    with patch.dict(os.environ, FLAGS, clear=False):
        result = run_owner_live_turn(
            video_id="owner-live-video", marker="OWNER-LIVE-RED", transport=transport,
            input_sender=_InputSender(), host_factory=lambda _chat: host,
            playback_factory=lambda _host: controller, clock=lambda: NOW,
            monotonic_clock=monotonic, sleep=sleeper, live_chat_id="yt-owner-live-room",
        )
    assert result["status"] == "accepted", result
    assert sleeps == [10.0, 20.0]
    assert monotonic.value == 30.0


def test_interrupted_voice_failure_retains_observed_counts_and_delivery_revisions() -> None:
    from nana.tools.canary_stream_cum5_owner_live import run_owner_live_turn

    host, controller, _model, _sender, _port, recorder = _host_and_controller(port=_Port("interrupted"))
    transport = _Transport([
        {"items": [], "nextPageToken": "boot"},
        {"items": [_message("yt-owner-question-1", "OWNER-LIVE-RED interrupted?")], "nextPageToken": "question"},
    ])
    counts = {"input": 1, "model": 1, "publish": 1, "tts": 1, "sink": 4}
    with patch.dict(os.environ, FLAGS, clear=False):
        result = run_owner_live_turn(
            video_id="owner-live-video", marker="OWNER-LIVE-RED", transport=transport,
            input_sender=_InputSender(), host_factory=lambda _chat: host,
            playback_factory=lambda _host: controller, clock=lambda: NOW,
            live_chat_id="yt-owner-live-room", observed_counts=counts, delivery_recorder=recorder,
        )
    assert result["reason_code"] == "voice_not_delivered_playback_error"
    assert result["input_count"] == result["model_count"] == result["publish_count"] == result["tts_count"] == 1
    assert result["sink_write_calls"] == 4 and result["playback_status"] == "interrupted"
    assert result["delivery_revisions"] == [0, 1, 2, 3]


def test_reflection_failure_after_delivery_retains_observed_counts_and_delivery_revisions() -> None:
    from nana.tools.canary_stream_cum5_owner_live import run_owner_live_turn

    host, controller, _model, _sender, _port, recorder = _host_and_controller()
    transport = _Transport([
        {"items": [], "nextPageToken": "boot"},
        {"items": [_message("yt-owner-question-1", "OWNER-LIVE-RED reflection?")], "nextPageToken": "question"},
        RuntimeError("reflection transport failure"),
    ])
    counts = {"input": 1, "model": 1, "publish": 1, "tts": 1, "sink": 8}
    with patch.dict(os.environ, FLAGS, clear=False):
        result = run_owner_live_turn(
            video_id="owner-live-video", marker="OWNER-LIVE-RED", transport=transport,
            input_sender=_InputSender(), host_factory=lambda _chat: host,
            playback_factory=lambda _host: controller, clock=lambda: NOW,
            live_chat_id="yt-owner-live-room", observed_counts=counts, delivery_recorder=recorder,
        )
    assert result["reason_code"] == "reflection_poll_failed"
    assert result["input_count"] == result["model_count"] == result["publish_count"] == result["tts_count"] == 1
    assert result["sink_write_calls"] == 8 and result["playback_status"] == "delivered"
    assert result["delivery_revisions"] == [0, 1, 2, 3]


def test_nonfresh_publication_fails_closed_before_voice_or_reflection() -> None:
    from nana.tools.canary_stream_cum5_owner_live import run_owner_live_turn
    from nana.runtime.stream_cum4_host import HostResult

    class Host:
        def __init__(self):
            self.ingest_calls = 0
            self.process_calls = 0

        def ingest_response(self, *_args, **_kwargs):
            self.ingest_calls += 1
            return HostResult("queued", "response_ingested")

        def process_next(self, **_kwargs):
            self.process_calls += 1
            return HostResult("failed", "unknown_outcome")

        def snapshot(self):
            return {"stats": {"self_ignored": 0}}

    host = Host()
    input_sender = _InputSender()
    transport = _Transport([{"items": [], "nextPageToken": "boot"}, {"items": [_message("yt-owner-question-1", "OWNER-LIVE-RED hi")]}])
    with patch.dict(os.environ, FLAGS, clear=False):
        result = run_owner_live_turn(
            video_id="owner-live-video", marker="OWNER-LIVE-RED", transport=transport,
            input_sender=input_sender, host_factory=lambda _chat: host,
            playback_factory=lambda _host: (_ for _ in ()).throw(AssertionError("voice invoked")),
            clock=lambda: NOW, live_chat_id="yt-owner-live-room",
        )
    assert result["status"] == "rejected" and result["reason_code"] == "publish_not_fresh"
    assert host.process_calls == 1 and host.ingest_calls == 2


def main() -> None:
    tests = (
        test_gate_off_does_not_inject_or_create_playback_side_effects,
        test_scoped_flags_require_initially_off_enable_only_during_scope_and_restore_exactly,
        test_recording_delivery_recorder_delegates_and_preserves_delivery_order,
        test_integrity_baseline_tracks_recursive_voice_cache_and_mouth_cursor,
        test_one_fresh_reply_runs_one_model_publish_voice_and_reflection_ingest_only,
        test_provider_deadlines_wait_before_question_and_reflection_without_shortening_interval,
        test_interrupted_voice_failure_retains_observed_counts_and_delivery_revisions,
        test_reflection_failure_after_delivery_retains_observed_counts_and_delivery_revisions,
        test_nonfresh_publication_fails_closed_before_voice_or_reflection,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"smoke_stream_cum5_owner_live: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
