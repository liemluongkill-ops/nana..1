"""Provider-free smoke for the bounded read-only Stream voice runner."""

from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
NANA_ROOT = ROOT / "nana"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if "nana" not in sys.modules:
    nana_package = types.ModuleType("nana")
    nana_package.__path__ = [str(NANA_ROOT)]
    sys.modules["nana"] = nana_package
if "nana.runtime" not in sys.modules:
    runtime_package = types.ModuleType("nana.runtime")
    runtime_package.__path__ = [str(NANA_ROOT / "runtime")]
    sys.modules["nana.runtime"] = runtime_package
if "nana.tools" not in sys.modules:
    tools_package = types.ModuleType("nana.tools")
    tools_package.__path__ = [str(NANA_ROOT / "tools")]
    sys.modules["nana.tools"] = tools_package


VOICE_FLAGS = {
    "NANA_STREAM_CUM0_ENABLED": "1",
    "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "1",
    "NANA_STREAM_CUM2_RESPONSE_ENABLED": "1",
    "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "0",
    "NANA_STREAM_CUM4_HOST_ENABLED": "1",
    "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED": "1",
}


def _page(token: str, *, interval_ms: int, items=()):
    return {
        "items": list(items),
        "nextPageToken": token,
        "pollingIntervalMillis": interval_ms,
    }


class _Monotonic:
    def __init__(self, value=0.0):
        self.value = float(value)

    def __call__(self):
        return self.value


class _Control:
    def __init__(self, *, stop_on_wait=False):
        self.stop_requested = False
        self.stop_reason = ""
        self.stop_on_wait = stop_on_wait
        self.deadlines = []
        self.wait_calls = []
        self.events = []

    def request_stop(self, reason="operator_stop"):
        if not self.stop_requested:
            self.stop_requested = True
            self.stop_reason = reason
        return True

    def stage(self, state, **metrics):
        self.events.append((state, dict(metrics)))

    def snapshot(self):
        return {
            "state": self.events[-1][0] if self.events else "connecting",
            "stop_requested": self.stop_requested,
            "stop_reason": self.stop_reason,
        }

    def wait(self, timeout_seconds):
        self.wait_calls.append(timeout_seconds)
        if self.stop_on_wait:
            self.request_stop()
        return self.stop_requested

    def set_deadline(self, deadline, *, clock=None):
        self.deadlines.append((deadline, clock))


class _Transport:
    def __init__(self, pages):
        self.pages = list(pages)
        self.resolve_calls = []
        self.poll_calls = []

    def resolve_live_chat_id(self, video_id):
        self.resolve_calls.append(video_id)
        return "yt-live-chat"

    def list_messages(self, live_chat_id, *, page_token=None):
        self.poll_calls.append((live_chat_id, page_token))
        page = self.pages.pop(0)
        if isinstance(page, BaseException):
            raise page
        return page


class _InnerHost:
    def __init__(self):
        self.ingested = []

    def ingest_response(self, response, **kwargs):
        self.ingested.append((response, kwargs))
        return SimpleNamespace(status="queued", reason_code="response_ingested")


class _VoiceHost:
    def __init__(self, results, observed=None):
        self.host = _InnerHost()
        self.results = list(results)
        self.process_calls = []
        self.observed = observed

    def process_next(self, *, now):
        self.process_calls.append(now)
        if self.observed is not None:
            self.observed["model_requests"] += 1
            self.observed["playback_dispatches"] += 1
            self.observed["tts_provider_requests"] += 1
            self.observed["local_audio_sink_writes"] += 3
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class _QueueingInnerHost:
    def __init__(self, *, max_queue=16):
        self.max_queue = max_queue
        self.ingested = []
        self.queue = []
        self.seen = set()
        self.stats = {"queued": 0, "dropped": 0, "skipped": 0}

    def snapshot(self):
        return {
            "queued": len(self.queue),
            "stats": dict(self.stats),
            "halted": False,
            "halt_reason": "",
        }

    def ingest_response(self, response, **kwargs):
        self.ingested.append((response, kwargs))
        if response.get("offlineAt"):
            return SimpleNamespace(
                status="halted",
                reason_code="provider_offline",
                queued=len(self.queue),
                dropped=0,
                skipped=0,
            )
        queued = dropped = skipped = 0
        for item in response.get("items", ()):
            event_id = str(item.get("id") or "")
            if not event_id or event_id in self.seen:
                continue
            self.seen.add(event_id)
            if kwargs.get("bootstrap"):
                continue
            if item.get("action") == "skip":
                skipped += 1
                self.stats["skipped"] += 1
                continue
            if len(self.queue) >= self.max_queue:
                dropped += 1
                self.stats["dropped"] += 1
                continue
            self.queue.append(item)
            queued += 1
            self.stats["queued"] += 1
        return SimpleNamespace(
            status="queued",
            reason_code="response_ingested",
            queued=queued,
            dropped=dropped,
            skipped=skipped,
        )


class _QueueingVoiceHost:
    def __init__(self, *, max_queue=16, observed=None):
        self.host = _QueueingInnerHost(max_queue=max_queue)
        self.process_calls = []
        self.processed_ids = []
        self.observed = observed

    def process_next(self, *, now):
        self.process_calls.append(now)
        item = self.host.queue.pop(0)
        self.processed_ids.append(item["id"])
        if self.observed is not None:
            self.observed["model_requests"] += 1
            self.observed["playback_dispatches"] += 1
            self.observed["tts_provider_requests"] += 1
            self.observed["local_audio_sink_writes"] += 1
        return _delivered_result(
            output_id=f"voice-{item['id']}",
            queued=len(self.host.queue),
        )


def _delivered_result(*, output_id="voice-output", queued=0):
    return SimpleNamespace(
        host_result=SimpleNamespace(
            status="generated",
            reason_code="generated",
            action="full_reply",
            queued=queued,
            skipped=0,
            dropped=0,
            response_artifact=SimpleNamespace(output_id=output_id),
        ),
        playback_result=SimpleNamespace(status="delivered", reason_code="playback_completed"),
    )


def _idle_result():
    return SimpleNamespace(
        host_result=SimpleNamespace(
            status="idle",
            reason_code="queue_empty",
            action="",
            response_artifact=None,
        ),
        playback_result=None,
    )


def test_multi_message_page_drains_without_another_youtube_read() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    observed = {
        "model_requests": 0,
        "playback_dispatches": 0,
        "tts_provider_requests": 0,
        "local_audio_sink_writes": 0,
    }
    transport = _Transport([
        _page("bootstrap", interval_ms=1_000, items=[{"id": "old"}]),
        _page(
            "fresh",
            interval_ms=30_000,
            items=[{"id": "m1"}, {"id": "m2"}, {"id": "m3"}],
        ),
    ])
    voice_host = _QueueingVoiceHost(observed=observed)
    control = _Control()
    monotonic = _Monotonic()

    def sleeper(seconds):
        monotonic.value += seconds

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=3,
            max_polls=2,
            timeout_seconds=60.0,
            wall_clock=lambda: 1_700_000_500.0,
            monotonic_clock=monotonic,
            sleep=sleeper,
            observed_counts=observed,
            control=control,
        )

    assert result.status == "completed", result
    assert result.reason_code == "turn_limit"
    assert result.turns_delivered == 3
    assert result.poll_requests == 2
    assert result.youtube_read_requests == 3
    assert result.host_process_calls == 3
    assert result.queued == 0 and result.skipped == 0 and result.dropped == 0
    assert voice_host.processed_ids == ["m1", "m2", "m3"]
    assert transport.poll_calls == [("yt-live-chat", None), ("yt-live-chat", "bootstrap")]
    assert control.deadlines == [(60.0, monotonic)]
    assert [state for state, _metrics in control.events][-2:] == ["stopping", "stopped"]
    assert control.events[-1][1]["turns_delivered"] == 3


def test_each_drained_job_refreshes_wall_time_for_stale_queue_checks() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    wall = {"value": 100.0}

    class AdvancingVoiceHost(_QueueingVoiceHost):
        def process_next(self, *, now):
            result = super().process_next(now=now)
            wall["value"] += 31.0
            return result

    transport = _Transport([
        _page("bootstrap", interval_ms=1_000),
        _page("fresh", interval_ms=1_000, items=[{"id": "m1"}, {"id": "m2"}]),
    ])
    voice_host = AdvancingVoiceHost()
    monotonic = _Monotonic()

    def sleeper(seconds):
        monotonic.value += seconds

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=2,
            max_polls=2,
            timeout_seconds=90.0,
            wall_clock=lambda: wall["value"],
            monotonic_clock=monotonic,
            sleep=sleeper,
        )

    assert result.reason_code == "turn_limit", result
    assert voice_host.process_calls == [100.0, 131.0]


def test_control_stop_interrupts_production_wait_before_another_read() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    transport = _Transport([_page("bootstrap", interval_ms=1)])
    voice_host = _QueueingVoiceHost()
    control = _Control(stop_on_wait=True)
    monotonic = _Monotonic()
    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=3,
            max_polls=3,
            timeout_seconds=60.0,
            monotonic_clock=monotonic,
            control=control,
        )

    assert result.status == "stopped", result
    assert result.reason_code == "operator_stop"
    assert result.poll_requests == 1
    assert transport.poll_calls == [("yt-live-chat", None)]
    assert control.wait_calls == [0.001]
    assert [state for state, _metrics in control.events][-2:] == ["stopping", "stopped"]


def test_safe_transport_failures_keep_precise_read_reason_codes() -> None:
    from nana.runtime.youtube_chat_transport import YouTubeChatTransportError
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    cases = (
        ("liveChatEnded", "live_chat_ended", "stopped"),
        ("quotaExceeded", "youtube_quota_exceeded", "error"),
        ("rateLimitExceeded", "youtube_rate_limited", "error"),
        ("YouTube REST request failed", "youtube_transport_error", "error"),
    )
    for safe_message, expected_reason, expected_state in cases:
        transport = _Transport([
            _page("bootstrap", interval_ms=1_000),
            YouTubeChatTransportError(safe_message),
        ])
        voice_host = _QueueingVoiceHost()
        control = _Control()
        monotonic = _Monotonic()

        def sleeper(seconds):
            monotonic.value += seconds

        with patch.dict(os.environ, VOICE_FLAGS, clear=False):
            result = run_bounded_voice_host(
                video_id="video-1",
                transport=transport,
                voice_host_factory=lambda _chat_id: voice_host,
                max_turns=3,
                max_polls=2,
                timeout_seconds=60.0,
                monotonic_clock=monotonic,
                sleep=sleeper,
                control=control,
            )

        assert result.reason_code == expected_reason, result
        assert result.poll_requests == 2
        assert result.turns_delivered == 0
        assert control.events[-1][0] == expected_state
        assert safe_message not in json.dumps(result.to_dict())


def test_one_transient_transport_failure_retries_then_delivers() -> None:
    from nana.runtime.youtube_chat_transport import YouTubeChatTransportError
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    transport = _Transport([
        _page("bootstrap", interval_ms=2_000),
        YouTubeChatTransportError("YouTube REST request failed"),
        _page("fresh", interval_ms=2_000, items=[{"id": "after-retry"}]),
    ])
    voice_host = _QueueingVoiceHost()
    control = _Control()
    monotonic = _Monotonic()
    sleeps = []

    def sleeper(seconds):
        sleeps.append(seconds)
        monotonic.value += seconds

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=1,
            max_polls=3,
            timeout_seconds=60.0,
            monotonic_clock=monotonic,
            sleep=sleeper,
            control=control,
        )

    assert result.status == "completed", result
    assert result.reason_code == "turn_limit"
    assert result.turns_delivered == 1
    assert result.poll_requests == 3
    assert result.youtube_read_requests == 4
    assert result.host_ingest_calls == 2
    assert voice_host.processed_ids == ["after-retry"]
    assert sleeps == [2.0, 2.0]
    assert control.events[-1][0] == "stopped"


def test_duplicate_pages_drain_once_then_provider_offline_stops_cleanly() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    transport = _Transport([
        _page("bootstrap", interval_ms=1_000),
        _page("page-1", interval_ms=1_000, items=[{"id": "m1"}, {"id": "m2"}]),
        _page("page-2", interval_ms=1_000, items=[{"id": "m2"}, {"id": "m3"}]),
        {
            "items": [],
            "nextPageToken": "offline",
            "pollingIntervalMillis": 1_000,
            "offlineAt": "2026-09-24T00:00:00Z",
        },
    ])
    voice_host = _QueueingVoiceHost()
    control = _Control()
    monotonic = _Monotonic()

    def sleeper(seconds):
        monotonic.value += seconds

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=50,
            max_polls=4,
            timeout_seconds=60.0,
            monotonic_clock=monotonic,
            sleep=sleeper,
            control=control,
        )

    assert result.status == "stopped", result
    assert result.reason_code == "provider_offline"
    assert result.turns_delivered == 3
    assert result.poll_requests == 4
    assert result.queued == 0
    assert voice_host.processed_ids == ["m1", "m2", "m3"]
    assert control.events[-1][0] == "stopped"
    assert control.events[-1][1]["turns_delivered"] == 3


def test_stop_requested_by_fresh_read_prevents_ingest_or_new_work() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    control = _Control()

    class StopAfterFreshRead(_Transport):
        def list_messages(self, live_chat_id, *, page_token=None):
            result = super().list_messages(live_chat_id, page_token=page_token)
            if page_token is not None:
                control.request_stop("operator_stop")
            return result

    transport = StopAfterFreshRead([
        _page("bootstrap", interval_ms=1_000),
        _page("fresh", interval_ms=1_000, items=[{"id": "must-not-ingest"}]),
    ])
    voice_host = _QueueingVoiceHost()
    monotonic = _Monotonic()

    def sleeper(seconds):
        monotonic.value += seconds

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=3,
            max_polls=2,
            timeout_seconds=60.0,
            monotonic_clock=monotonic,
            sleep=sleeper,
            control=control,
        )

    assert result.reason_code == "operator_stop", result
    assert result.poll_requests == 2
    assert result.host_ingest_calls == 1
    assert result.host_process_calls == 0
    assert len(voice_host.host.ingested) == 1


def test_turn_limit_stops_before_next_queued_job() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    transport = _Transport([
        _page("bootstrap", interval_ms=1_000),
        _page(
            "fresh",
            interval_ms=1_000,
            items=[{"id": "m1"}, {"id": "m2"}, {"id": "m3"}],
        ),
    ])
    voice_host = _QueueingVoiceHost()
    control = _Control()
    monotonic = _Monotonic()

    def sleeper(seconds):
        monotonic.value += seconds

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=2,
            max_polls=2,
            timeout_seconds=60.0,
            monotonic_clock=monotonic,
            sleep=sleeper,
            control=control,
        )

    assert result.status == "completed", result
    assert result.reason_code == "turn_limit"
    assert result.turns_delivered == 2
    assert result.queued == 1
    assert voice_host.processed_ids == ["m1", "m2"]
    assert [item["id"] for item in voice_host.host.queue] == ["m3"]


def test_poll_limit_finishes_without_processing_an_empty_queue() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    transport = _Transport([
        _page("bootstrap", interval_ms=1_000),
        _page("fresh", interval_ms=1_000),
    ])
    voice_host = _QueueingVoiceHost()
    control = _Control()
    monotonic = _Monotonic()
    injected_sleeps = []

    def sleeper(seconds):
        injected_sleeps.append(seconds)
        monotonic.value += seconds

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=3,
            max_polls=2,
            timeout_seconds=60.0,
            monotonic_clock=monotonic,
            sleep=sleeper,
            control=control,
        )

    assert result.status == "stopped", result
    assert result.reason_code == "poll_limit"
    assert result.host_process_calls == 0
    assert injected_sleeps == [1.0]
    assert control.wait_calls == []
    assert [state for state, _metrics in control.events][-2:] == ["stopping", "stopped"]


def test_control_stop_during_model_is_not_reported_as_late_playback_error() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    control = _Control()

    class BlockingVoiceHost(_QueueingVoiceHost):
        def process_next(self, *, now):
            control.request_stop("operator_stop")
            return SimpleNamespace(
                host_result=SimpleNamespace(
                    status="stopped",
                    reason_code="operator_stop",
                    queued=1,
                ),
                playback_result=None,
            )

    transport = _Transport([
        _page("bootstrap", interval_ms=1_000),
        _page("fresh", interval_ms=1_000, items=[{"id": "late-model"}]),
    ])
    voice_host = BlockingVoiceHost()
    monotonic = _Monotonic()

    def sleeper(seconds):
        monotonic.value += seconds

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=3,
            max_polls=2,
            timeout_seconds=60.0,
            monotonic_clock=monotonic,
            sleep=sleeper,
            control=control,
        )

    assert result.status == "stopped", result
    assert result.reason_code == "operator_stop"
    assert result.turns_delivered == 0
    assert result.playback_statuses == ()


def test_control_stop_with_interrupted_playback_is_graceful() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    control = _Control()

    class InterruptingVoiceHost(_VoiceHost):
        def process_next(self, *, now):
            control.request_stop("operator_stop")
            return super().process_next(now=now)

    transport = _Transport([
        _page("bootstrap", interval_ms=1_000),
        _page("fresh", interval_ms=1_000, items=[{"id": "interrupted"}]),
    ])
    voice_host = InterruptingVoiceHost([
        SimpleNamespace(
            host_result=SimpleNamespace(
                status="stopped",
                reason_code="voice_playback_interrupted",
                queued=0,
            ),
            playback_result=SimpleNamespace(
                status="interrupted",
                reason_code="session_stop_before_audio",
            ),
        )
    ])

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=3,
            max_polls=2,
            timeout_seconds=60.0,
            control=control,
        )

    assert result.reason_code == "operator_stop", result
    assert result.playback_statuses == ("interrupted",)


def test_bootstrap_is_context_only_and_full_provider_interval_is_respected() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    observed = {
        "model_requests": 0,
        "playback_dispatches": 0,
        "tts_provider_requests": 0,
        "local_audio_sink_writes": 0,
    }
    transport = _Transport([
        _page("bootstrap", interval_ms=15_000, items=[{"id": "old-message"}]),
        _page("fresh", interval_ms=22_000, items=[{"id": "fresh-message"}]),
    ])
    voice_host = _VoiceHost([_delivered_result()], observed)
    monotonic = _Monotonic()
    sleeps = []

    def sleeper(seconds):
        sleeps.append(seconds)
        monotonic.value += seconds

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=1,
            max_polls=2,
            timeout_seconds=60.0,
            wall_clock=lambda: 1_700_000_500.0,
            monotonic_clock=monotonic,
            sleep=sleeper,
            observed_counts=observed,
        )

    assert result.status == "completed", result
    assert result.reason_code == "turn_limit"
    assert result.turns_delivered == 1
    assert result.poll_requests == 2
    assert result.youtube_read_requests == 3  # resolve plus two list calls
    assert sleeps == [15.0]
    assert voice_host.process_calls == [1_700_000_500.0]
    assert [entry[1]["bootstrap"] for entry in voice_host.host.ingested] == [True, False]
    assert result.model_requests == 1
    assert result.playback_dispatches == 1
    assert result.tts_provider_requests == 1
    assert result.local_audio_sink_writes == 3
    assert result.playback_statuses == ("delivered",)


def test_next_poll_past_deadline_stops_without_shortened_sleep_or_network() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    transport = _Transport([_page("bootstrap", interval_ms=121_000)])
    voice_host = _VoiceHost([_idle_result()])
    monotonic = _Monotonic()
    sleeps = []
    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=1,
            max_polls=40,
            timeout_seconds=120.0,
            wall_clock=lambda: 1_700_000_500.0,
            monotonic_clock=monotonic,
            sleep=lambda seconds: sleeps.append(seconds),
        )

    assert result.status == "stopped", result
    assert result.reason_code == "timeout_before_next_poll"
    assert result.poll_requests == 1
    assert transport.poll_calls == [("yt-live-chat", None)]
    assert sleeps == []
    assert voice_host.process_calls == []


def test_resolution_that_exhausts_deadline_never_starts_bootstrap_poll() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    monotonic = _Monotonic()

    class SlowResolveTransport(_Transport):
        def resolve_live_chat_id(self, video_id):
            result = super().resolve_live_chat_id(video_id)
            monotonic.value = 5.1
            return result

    transport = SlowResolveTransport([_page("must-not-read", interval_ms=1_000)])
    voice_host = _VoiceHost([_idle_result()])
    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=1,
            max_polls=2,
            timeout_seconds=5.0,
            monotonic_clock=monotonic,
        )

    assert result.status == "stopped", result
    assert result.reason_code == "timeout"
    assert result.youtube_resolve_requests == 1
    assert result.poll_requests == 0
    assert transport.poll_calls == []
    assert voice_host.host.ingested == []


def test_slow_setup_or_fresh_poll_never_dispatches_after_deadline() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    monotonic = _Monotonic()
    transport = _Transport([_page("must-not-read", interval_ms=1_000)])

    def slow_factory(_chat_id):
        monotonic.value = 5.1
        return _VoiceHost([_idle_result()])

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        setup_result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=slow_factory,
            max_turns=1,
            max_polls=2,
            timeout_seconds=5.0,
            monotonic_clock=monotonic,
        )
    assert setup_result.reason_code == "timeout"
    assert setup_result.poll_requests == 0
    assert transport.poll_calls == []

    monotonic.value = 0.0

    class SlowFreshPollTransport(_Transport):
        def list_messages(self, live_chat_id, *, page_token=None):
            result = super().list_messages(live_chat_id, page_token=page_token)
            if page_token is not None:
                monotonic.value = 5.1
            return result

    transport = SlowFreshPollTransport([
        _page("bootstrap", interval_ms=1_000),
        _page("fresh", interval_ms=1_000, items=[{"id": "fresh-message"}]),
    ])
    voice_host = _VoiceHost([_delivered_result()])

    def sleeper(seconds):
        monotonic.value += seconds

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        poll_result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=1,
            max_polls=2,
            timeout_seconds=5.0,
            monotonic_clock=monotonic,
            sleep=sleeper,
        )
    assert poll_result.reason_code == "timeout"
    assert poll_result.poll_requests == 2
    assert voice_host.process_calls == []
    assert len(voice_host.host.ingested) == 1


def test_invalid_bounds_and_wrong_gates_fail_before_factories_or_io() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    calls = []
    transport = _Transport([])
    for values in (
        {"max_turns": 0, "max_polls": 1, "timeout_seconds": 1.0},
        {"max_turns": 1, "max_polls": 0, "timeout_seconds": 1.0},
        {"max_turns": 1, "max_polls": 1, "timeout_seconds": math.inf},
        {"max_turns": 1, "max_polls": 1, "timeout_seconds": math.nan},
    ):
        with patch.dict(os.environ, VOICE_FLAGS, clear=False):
            result = run_bounded_voice_host(
                video_id="video-1",
                transport=transport,
                voice_host_factory=lambda _chat_id: calls.append(True),
                **values,
            )
        assert result.status == "rejected", result
        assert result.reason_code == "invalid_bounds"

    wrong = dict(VOICE_FLAGS)
    wrong["NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED"] = "1"
    with patch.dict(os.environ, wrong, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: calls.append(True),
        )
    assert result.status == "blocked", result
    assert result.reason_code == "cum3_must_be_disabled"
    assert calls == []
    assert transport.resolve_calls == []
    assert transport.poll_calls == []


def test_exception_result_retains_observed_counts_and_statuses() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    observed = {
        "model_requests": 0,
        "playback_dispatches": 0,
        "tts_provider_requests": 0,
        "local_audio_sink_writes": 0,
    }
    transport = _Transport([
        _page("bootstrap", interval_ms=1_000),
        _page("fresh", interval_ms=1_000, items=[{"id": "fresh-message"}]),
    ])
    voice_host = _VoiceHost([RuntimeError("fixture host failure")], observed)
    monotonic = _Monotonic()

    def sleeper(seconds):
        monotonic.value += seconds

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=1,
            max_polls=2,
            timeout_seconds=10.0,
            wall_clock=lambda: 1_700_000_500.0,
            monotonic_clock=monotonic,
            sleep=sleeper,
            observed_counts=observed,
        )

    assert result.status == "stopped", result
    assert result.reason_code == "voice_host_error"
    assert result.poll_requests == 2
    assert result.youtube_read_requests == 3
    assert result.model_requests == 1
    assert result.playback_dispatches == 1
    assert result.tts_provider_requests == 1
    assert result.local_audio_sink_writes == 3
    assert result.playback_statuses == ()


def test_keyboard_interrupt_retains_observed_work_without_playback_receipt() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    observed = {
        "model_requests": 0,
        "playback_dispatches": 0,
        "tts_provider_requests": 0,
        "local_audio_sink_writes": 0,
    }
    transport = _Transport([
        _page("bootstrap", interval_ms=1_000),
        _page("fresh", interval_ms=1_000, items=[{"id": "fresh-message"}]),
    ])
    voice_host = _VoiceHost([KeyboardInterrupt()], observed)
    monotonic = _Monotonic()

    def sleeper(seconds):
        monotonic.value += seconds

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=1,
            max_polls=2,
            timeout_seconds=10.0,
            wall_clock=lambda: 1_700_000_500.0,
            monotonic_clock=monotonic,
            sleep=sleeper,
            observed_counts=observed,
        )

    assert result.status == "stopped", result
    assert result.reason_code == "keyboard_interrupt"
    assert result.poll_requests == 2
    assert result.youtube_read_requests == 3
    assert result.host_ingest_calls == 2
    assert result.host_process_calls == 1
    assert result.elapsed_seconds == 1.0
    assert result.model_requests == 1
    assert result.playback_dispatches == 1
    assert result.tts_provider_requests == 1
    assert result.local_audio_sink_writes == 3
    assert result.playback_statuses == ()


def test_external_host_and_playback_reasons_are_sanitized() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    def run(result):
        transport = _Transport([
            _page("bootstrap", interval_ms=1_000),
            _page("fresh", interval_ms=1_000, items=[{"id": "fresh-message"}]),
        ])
        voice_host = _VoiceHost([result])
        monotonic = _Monotonic()

        def sleeper(seconds):
            monotonic.value += seconds

        with patch.dict(os.environ, VOICE_FLAGS, clear=False):
            return run_bounded_voice_host(
                video_id="video-1",
                transport=transport,
                voice_host_factory=lambda _chat_id: voice_host,
                max_turns=1,
                max_polls=2,
                timeout_seconds=10.0,
                monotonic_clock=monotonic,
                sleep=sleeper,
            )

    interrupted = run(SimpleNamespace(
        host_result=SimpleNamespace(
            status="halted",
            reason_code="voice_playback_interrupted",
            response_artifact=SimpleNamespace(output_id="voice-output"),
        ),
        playback_result=SimpleNamespace(
            status="interrupted",
            reason_code="private token=do-not-print",
        ),
    ))
    assert interrupted.reason_code == "voice_playback_interrupted"
    assert interrupted.last_host_reason == "voice_playback_interrupted"
    assert interrupted.playback_statuses == ("interrupted",)

    failed = run(SimpleNamespace(
        host_result=SimpleNamespace(
            status="failed",
            reason_code="private token=do-not-print",
            response_artifact=None,
        ),
        playback_result=None,
    ))
    assert failed.reason_code == "voice_host_stopped"
    assert failed.last_host_reason == "voice_host_stopped"
    assert "private" not in json.dumps(failed.to_dict())


def test_main_off_path_is_direct_file_safe_and_inert() -> None:
    runner = NANA_ROOT / "tools" / "run_stream_voice_host.py"
    environment = os.environ.copy()
    environment.update({name: "0" for name in VOICE_FLAGS})
    environment["NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED"] = "0"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    with tempfile.TemporaryDirectory() as temp_dir:
        guard = Path(temp_dir) / "sitecustomize.py"
        guard.write_text(
            (
                "import os\n"
                "import sys\n"
                f"_data = os.path.normcase(os.path.abspath({str(NANA_ROOT / 'data')!r}))\n"
                "def _audit(event, args):\n"
                "    if event == 'open' and args:\n"
                "        path = os.path.normcase(os.path.abspath(os.fspath(args[0])))\n"
                "        if os.path.basename(path).lower() == '.env' or path == _data or path.startswith(_data + os.sep):\n"
                "            raise RuntimeError('forbidden production file access')\n"
                "    if event == 'socket.connect':\n"
                "        raise RuntimeError('forbidden network call')\n"
                "sys.addaudithook(_audit)\n"
            ),
            encoding="ascii",
        )
        environment["PYTHONPATH"] = os.pathsep.join((temp_dir, str(ROOT)))
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(runner),
                "--video-id",
                "video-never-contact",
            ],
            cwd=str(ROOT),
            env=environment,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

    assert completed.returncode == 2, completed
    records = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    assert len(records) == 1, completed.stdout
    assert records[0]["status"] == "blocked"
    assert records[0]["reason_code"] == "stream_gate_disabled"
    assert records[0]["youtube_read_requests"] == 0
    assert records[0]["model_requests"] == 0
    assert records[0]["playback_dispatches"] == 0
    assert completed.stderr == ""


def test_explicit_read_auth_never_crosses_credential_sources() -> None:
    from nana.tools.run_stream_voice_host import _select_read_credentials

    oauth_loads = []
    api = _select_read_credentials(
        "api-key",
        environ={
            "NANA_YOUTUBE_API_KEY": "fixture-api-key",
            "NANA_YOUTUBE_OAUTH_TOKEN": "fixture-oauth-token",
        },
        oauth_token_loader=lambda: oauth_loads.append(True) or "unexpected",
    )
    assert api.api_key == "fixture-api-key"
    assert api.oauth_token is None
    assert oauth_loads == []

    oauth = _select_read_credentials(
        "oauth",
        environ={
            "NANA_YOUTUBE_API_KEY": "fixture-api-key",
            "NANA_YOUTUBE_OAUTH_TOKEN": "fixture-oauth-token",
        },
        oauth_token_loader=lambda: oauth_loads.append(True) or "unexpected",
    )
    assert oauth.api_key is None
    assert oauth.oauth_token == "fixture-oauth-token"
    assert oauth_loads == []

    try:
        _select_read_credentials(
            "oauth",
            environ={},
            oauth_token_loader=lambda: oauth_loads.append(True) or "unexpected",
        )
    except Exception as exc:
        assert "provide exactly one" in str(exc)
    else:
        raise AssertionError("oauth mode silently fell back to the OAuth cache")
    assert oauth_loads == []

    cached = _select_read_credentials(
        "oauth-cache",
        environ={"NANA_YOUTUBE_OAUTH_TOKEN": "must-be-ignored"},
        oauth_token_loader=lambda: oauth_loads.append(True) or "cached-fixture-token",
    )
    assert cached.oauth_token == "cached-fixture-token"
    assert oauth_loads == [True]


def test_live_runtime_loads_config_then_rechecks_gates_before_credentials() -> None:
    from nana.tools import run_stream_voice_host as runner
    import nana.runtime.youtube_chat_transport as transport_module

    order = []

    class FakeTransport:
        def __init__(self, credentials):
            order.append(("transport", credentials))

    with patch.object(
        runner,
        "_load_runtime_config",
        side_effect=lambda: order.append("config"),
    ), patch.object(
        runner,
        "_gate_reason",
        side_effect=lambda: order.append("gate") or None,
    ), patch.object(
        runner,
        "_select_read_credentials",
        side_effect=lambda _mode: order.append("credentials") or "fixture-credentials",
    ), patch.object(
        transport_module,
        "YouTubeRestChatTransport",
        FakeTransport,
    ):
        transport, host_factory = runner._build_live_runtime(
            video_id="video-1",
            read_auth="api-key",
            observed_counts={},
        )

    assert isinstance(transport, FakeTransport)
    assert callable(host_factory)
    assert order == [
        "config",
        "gate",
        "credentials",
        ("transport", "fixture-credentials"),
    ]

    credential_calls = []
    with patch.object(runner, "_load_runtime_config", return_value=None), patch.object(
        runner,
        "_gate_reason",
        return_value="cum3_must_be_disabled",
    ), patch.object(
        runner,
        "_select_read_credentials",
        side_effect=lambda _mode: credential_calls.append(True),
    ):
        try:
            runner._build_live_runtime(
                video_id="video-1",
                read_auth="api-key",
                observed_counts={},
            )
        except runner._LiveRuntimeSetupError as exc:
            assert exc.reason_code == "cum3_must_be_disabled"
        else:
            raise AssertionError("post-config gate change was accepted")
    assert credential_calls == []


def test_live_runtime_wires_one_control_and_generator_delivery_recorder() -> None:
    from nana.runtime.stream_session_control import SessionPlaybackPort, StreamSessionControl
    from nana.tools import run_stream_voice_host as runner
    import nana.runtime.youtube_chat_transport as transport_module

    class FakeTransport:
        def __init__(self, credentials):
            self.credentials = credentials

    control = StreamSessionControl(clock=lambda: 0.0)
    with patch.object(runner, "_load_runtime_config", return_value=None), patch.object(
        runner,
        "_gate_reason",
        return_value=None,
    ), patch.object(
        runner,
        "_select_read_credentials",
        return_value="fixture-credentials",
    ), patch.object(
        transport_module,
        "YouTubeRestChatTransport",
        FakeTransport,
    ):
        transport, host_factory = runner._build_live_runtime(
            video_id="video-1",
            read_auth="api-key",
            observed_counts={},
            control=control,
        )
        voice_host = host_factory("yt-live-chat")

    assert isinstance(transport, FakeTransport)
    assert voice_host.control is control
    controller = voice_host.playback._controller
    assert controller._delivery_recorder is voice_host.host.generator.delivery_recorder
    playback_port = controller._playback_port_factory()
    assert isinstance(playback_port, SessionPlaybackPort)
    assert playback_port._control is control


def test_live_runtime_visual_bridge_is_default_off_and_session_owned() -> None:
    from nana.runtime.stream_session_control import StreamSessionControl
    from nana.tools import run_stream_voice_host as runner
    import nana.runtime.youtube_chat_transport as transport_module

    class FakeTransport:
        def __init__(self, credentials):
            self.credentials = credentials

    class FakeVisualRuntime:
        def __init__(self, session_id):
            self.session_id = session_id
            self.store = object()
            self.starts = 0
            self.shutdowns = 0

        def start(self):
            self.starts += 1
            return True

        def shutdown(self):
            self.shutdowns += 1

    assert runner._public_visual_signals_enabled({}) is False
    assert runner._public_visual_signals_enabled({
        "NANA_STREAM_PUBLIC_VISUAL_SIGNALS_ENABLED": "1"
    }) is True

    runtime_calls = []
    control = StreamSessionControl(clock=lambda: 0.0)
    with patch.dict(
        os.environ,
        {**VOICE_FLAGS, "NANA_STREAM_PUBLIC_VISUAL_SIGNALS_ENABLED": "1"},
        clear=False,
    ), patch.object(
        runner,
        "_load_runtime_config",
        return_value=None,
    ), patch.object(
        runner,
        "_gate_reason",
        return_value=None,
    ), patch.object(
        runner,
        "_select_read_credentials",
        return_value="fixture-credentials",
    ), patch.object(
        transport_module,
        "YouTubeRestChatTransport",
        FakeTransport,
    ):
        _transport, host_factory = runner._build_live_runtime(
            video_id="video-1",
            read_auth="api-key",
            observed_counts={},
            control=control,
            public_visual_runtime_factory=lambda session_id: (
                runtime_calls.append(FakeVisualRuntime(session_id)) or runtime_calls[-1]
            ),
        )
        voice_host = host_factory("yt-live-chat")

    assert len(runtime_calls) == 1
    visual_runtime = runtime_calls[0]
    assert visual_runtime.session_id.startswith("youtube-voice-")
    assert visual_runtime.starts == 1 and visual_runtime.shutdowns == 0
    playback_port = voice_host.playback._controller._playback_port_factory()
    assert playback_port._port._visual_signal_store is visual_runtime.store
    assert playback_port._port._engine_factory is not None
    voice_host.shutdown()
    voice_host.shutdown()
    assert visual_runtime.shutdowns == 1

    disabled_calls = []
    with patch.dict(
        os.environ,
        {**VOICE_FLAGS, "NANA_STREAM_PUBLIC_VISUAL_SIGNALS_ENABLED": "0"},
        clear=False,
    ), patch.object(
        runner,
        "_load_runtime_config",
        return_value=None,
    ), patch.object(
        runner,
        "_gate_reason",
        return_value=None,
    ), patch.object(
        runner,
        "_select_read_credentials",
        return_value="fixture-credentials",
    ), patch.object(
        transport_module,
        "YouTubeRestChatTransport",
        FakeTransport,
    ):
        _transport, disabled_factory = runner._build_live_runtime(
            video_id="video-1",
            read_auth="api-key",
            observed_counts={},
            public_visual_runtime_factory=lambda session_id: disabled_calls.append(session_id),
        )
        disabled_host = disabled_factory("yt-live-chat")
    assert disabled_calls == []
    disabled_port = disabled_host.playback._controller._playback_port_factory()
    assert disabled_port._visual_signal_store is None


def test_runner_closes_voice_host_on_terminal_return() -> None:
    from nana.tools.run_stream_voice_host import run_bounded_voice_host

    class ClosingVoiceHost(_QueueingVoiceHost):
        def __init__(self):
            super().__init__()
            self.shutdown_calls = 0

        def shutdown(self):
            self.shutdown_calls += 1

    transport = _Transport([
        _page("bootstrap", interval_ms=1_000),
        _page("fresh", interval_ms=30_000, items=[{"id": "m1"}]),
    ])
    voice_host = ClosingVoiceHost()
    monotonic = _Monotonic()

    def sleeper(seconds):
        monotonic.value += seconds

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = run_bounded_voice_host(
            video_id="video-1",
            transport=transport,
            voice_host_factory=lambda _chat_id: voice_host,
            max_turns=1,
            max_polls=2,
            timeout_seconds=60.0,
            wall_clock=lambda: 1_700_000_500.0,
            monotonic_clock=monotonic,
            sleep=sleeper,
        )
    assert result.status == "completed" and result.reason_code == "turn_limit"
    assert voice_host.shutdown_calls == 1


def test_live_runtime_sanitizes_credential_setup_failure() -> None:
    from nana.runtime.youtube_chat_transport import YouTubeChatTransportError
    from nana.tools import run_stream_voice_host as runner

    with patch.object(runner, "_load_runtime_config", return_value=None), patch.object(
        runner,
        "_gate_reason",
        return_value=None,
    ), patch.object(
        runner,
        "_select_read_credentials",
        side_effect=YouTubeChatTransportError("private credential detail"),
    ):
        try:
            runner._build_live_runtime(
                video_id="video-1",
                read_auth="api-key",
                observed_counts={},
            )
        except runner._LiveRuntimeSetupError as exc:
            assert exc.reason_code == "youtube_credentials_unavailable"
            assert "private" not in str(exc)
        else:
            raise AssertionError("credential validation failure escaped unsanitized")


def test_main_rejects_bad_cli_bounds_before_runtime_builder() -> None:
    from nana.tools import run_stream_voice_host as runner

    output = io.StringIO()
    with patch.dict(os.environ, VOICE_FLAGS, clear=False), patch.object(
        runner,
        "_build_live_runtime",
        side_effect=AssertionError("runtime constructed for invalid bounds"),
    ), redirect_stdout(output):
        exit_code = runner.main([
            "--video-id",
            "video-1",
            "--timeout-seconds",
            "nan",
        ])

    assert exit_code == 2
    result = json.loads(output.getvalue())
    assert result["status"] == "rejected"
    assert result["reason_code"] == "invalid_bounds"
    normalized_help = " ".join(runner._parser().format_help().split())
    assert "does not cancel an in-flight model or audio call" in normalized_help


def test_main_rejects_blank_video_before_runtime_builder() -> None:
    from nana.tools import run_stream_voice_host as runner

    output = io.StringIO()
    with patch.dict(os.environ, VOICE_FLAGS, clear=False), patch.object(
        runner,
        "_build_live_runtime",
        side_effect=AssertionError("runtime constructed for blank video ID"),
    ), redirect_stdout(output):
        exit_code = runner.main(["--video-id", "   "])

    assert exit_code == 2
    result = json.loads(output.getvalue())
    assert result["status"] == "rejected"
    assert result["reason_code"] == "video_id_required"
    assert result["youtube_read_requests"] == 0


def main() -> None:
    tests = (
        test_multi_message_page_drains_without_another_youtube_read,
        test_each_drained_job_refreshes_wall_time_for_stale_queue_checks,
        test_control_stop_interrupts_production_wait_before_another_read,
        test_safe_transport_failures_keep_precise_read_reason_codes,
        test_one_transient_transport_failure_retries_then_delivers,
        test_duplicate_pages_drain_once_then_provider_offline_stops_cleanly,
        test_stop_requested_by_fresh_read_prevents_ingest_or_new_work,
        test_turn_limit_stops_before_next_queued_job,
        test_poll_limit_finishes_without_processing_an_empty_queue,
        test_control_stop_during_model_is_not_reported_as_late_playback_error,
        test_control_stop_with_interrupted_playback_is_graceful,
        test_bootstrap_is_context_only_and_full_provider_interval_is_respected,
        test_next_poll_past_deadline_stops_without_shortened_sleep_or_network,
        test_resolution_that_exhausts_deadline_never_starts_bootstrap_poll,
        test_slow_setup_or_fresh_poll_never_dispatches_after_deadline,
        test_invalid_bounds_and_wrong_gates_fail_before_factories_or_io,
        test_exception_result_retains_observed_counts_and_statuses,
        test_keyboard_interrupt_retains_observed_work_without_playback_receipt,
        test_external_host_and_playback_reasons_are_sanitized,
        test_main_off_path_is_direct_file_safe_and_inert,
        test_explicit_read_auth_never_crosses_credential_sources,
        test_live_runtime_loads_config_then_rechecks_gates_before_credentials,
        test_live_runtime_wires_one_control_and_generator_delivery_recorder,
        test_live_runtime_visual_bridge_is_default_off_and_session_owned,
        test_runner_closes_voice_host_on_terminal_return,
        test_live_runtime_sanitizes_credential_setup_failure,
        test_main_rejects_bad_cli_bounds_before_runtime_builder,
        test_main_rejects_blank_video_before_runtime_builder,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"smoke_stream_voice_runner: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
