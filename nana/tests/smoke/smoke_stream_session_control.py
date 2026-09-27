"""Focused provider-free smoke for Stream V1 foreground session control."""
from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import sys
import threading
import types
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def _audit_guard(event, args):
    if event == "open" and args and not isinstance(args[0], int):
        path = str(args[0]).replace("\\", "/").lower()
        mode = str(args[1]) if len(args) > 1 else ""
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        protected = "/nana/data/" in path or path.endswith("/.env")
        writable = any(mark in mode for mark in "wax+") or bool(
            flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC)
        )
        if protected or writable:
            raise AssertionError("session control smoke blocked file access")
    if event in {"socket.connect", "subprocess.Popen", "os.system"}:
        raise AssertionError("session control smoke blocked network/process access")


sys.addaudithook(_audit_guard)

PACKAGE_ROOT = ROOT / "nana"
for package_name, package_path in (
    ("nana", PACKAGE_ROOT),
    ("nana.runtime", PACKAGE_ROOT / "runtime"),
    ("nana.tests", PACKAGE_ROOT / "tests"),
    ("nana.tests.smoke", PACKAGE_ROOT / "tests" / "smoke"),
):
    package = types.ModuleType(package_name)
    package.__package__ = package_name
    package.__path__ = [str(package_path)]
    sys.modules[package_name] = package


from nana.tests.smoke.smoke_stream_cum5_voice_playback import (  # noqa: E402
    NOW,
    VOICE_FLAGS,
    _controller,
    _handoff,
)


class _Clock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


class _ControlledPort:
    def __init__(self, control, *, stop_before_audio: bool) -> None:
        self.control = control
        self.stop_before_audio = stop_before_audio
        self.first_audio_accepted = False
        self.continue_after_stop = None
        self.cancel_calls = []

    def ready(self):
        from nana.runtime.stream_cum5_voice_playback import PlaybackReadiness

        return PlaybackReadiness(True, "ready")

    def play(self, request, *, should_continue, on_first_audio):
        from nana.runtime.stream_cum5_voice_playback import (
            AudioCompletionResult,
            FirstAudioEvidence,
        )

        if self.stop_before_audio:
            self.control.request_stop("operator_stop")
            assert should_continue() is False
            return AudioCompletionResult(
                "cancelled", False, 1, 1, 0, 1,
                len(request.artifact.text), len(request.artifact.text), 0.0,
                "session_stop_before_audio", request.playback_id,
                request.content_sha256, request.requested_at + 0.1, True,
            )

        self.first_audio_accepted = on_first_audio(FirstAudioEvidence(
            request.playback_id,
            request.content_sha256,
            request.requested_at + 0.1,
            320,
        )) is True
        assert self.first_audio_accepted
        self.control.request_stop("operator_stop")
        self.continue_after_stop = should_continue()
        return AudioCompletionResult(
            "completed", True, 1, 1, 1, 0,
            len(request.artifact.text), 0, 400.0, "none",
            request.playback_id, request.content_sha256,
            request.requested_at + 0.2, True,
        )

    def cancel(self, playback_id):
        from nana.runtime.stream_cum5_voice_playback import PlaybackStopResult

        self.cancel_calls.append(playback_id)
        return PlaybackStopResult("confirmed_stopped", "session_stopped")


class _Playback:
    def __init__(self) -> None:
        self.calls = []

    def snapshot(self):
        return {"active": False, "halted": False}

    def play_generated(self, artifact, *, attempt_id, now):
        self.calls.append((artifact, attempt_id, now))
        raise AssertionError("late generation reached playback")


class _BlockingHost:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = []
        self.entered = threading.Event()
        self.release = threading.Event()

    def snapshot(self):
        return {"queued": 1}

    def generate_next(self, *, now, full_replies_only=False, max_job_age_seconds=None):
        self.calls.append((now, full_replies_only, max_job_age_seconds))
        self.entered.set()
        if not self.release.wait(2):
            raise AssertionError("blocking_host_timeout")
        return self.result


class _SequenceHost:
    def __init__(self, results) -> None:
        self.results = deque(results)
        self.calls = []

    def snapshot(self):
        return {"queued": len(self.results)}

    def generate_next(self, *, now, full_replies_only=False, **kwargs):
        self.calls.append((now, full_replies_only, kwargs))
        return self.results.popleft()


def _generated_result(artifact):
    from nana.runtime.stream_cum4_host import HostResult

    return HostResult(
        "generated",
        "generated",
        action="full_reply",
        public_turn=object(),
        response_artifact=artifact,
    )


def test_idle_stop_deadline_terminal_and_sanitized_status() -> None:
    from nana.runtime.stream_session_control import StreamSessionControl

    clock = _Clock(10.0)
    emitted = []
    control = StreamSessionControl(status_sink=emitted.append, clock=clock)

    assert control.wait(0.0) is False
    assert control.stage(
        "waiting",
        poll_requests=2,
        queued=1,
        reason_code="poll_complete",
        raw_text="PRIVATE MESSAGE",
        scope={"actor": "private-user"},
        api_key="private-key",
    ) is None
    snapshot = control.snapshot()
    assert snapshot["state"] == "waiting"
    assert snapshot["poll_requests"] == 2 and snapshot["queued"] == 1
    assert snapshot["reason_code"] == "poll_complete"
    assert "PRIVATE" not in repr(snapshot)
    assert "private-user" not in repr(snapshot)
    assert "private-key" not in repr(snapshot)

    control.set_deadline(12.0, clock=clock)
    clock.value = 12.0
    assert control.stop_requested is True
    assert control.stop_reason == "session_timeout"
    assert control.wait(0.0) is True
    assert control.snapshot()["state"] == "stopping"

    control.stage("stopped", reason_code="session_timeout", turns_delivered=1)
    control.stage("thinking", model_requests=99, raw_text="late secret")
    terminal = control.snapshot()
    assert terminal["state"] == "stopped"
    assert terminal["turns_delivered"] == 1
    assert terminal.get("model_requests") != 99
    assert all(set(item).issubset({
        "state", "reason_code", "stop_requested", "stop_reason", "updated_at",
        "poll_requests", "turns_delivered", "queued", "skipped", "dropped",
        "model_requests", "playback_dispatches", "tts_provider_requests",
        "local_audio_sink_writes",
    }) for item in emitted)


def test_status_sink_is_nonthrowing_and_fifo_across_threads() -> None:
    from nana.runtime.stream_session_control import StreamSessionControl

    first_entered = threading.Event()
    release_first = threading.Event()
    emitted = []

    def sink(snapshot):
        emitted.append(snapshot["poll_requests"])
        if snapshot["poll_requests"] == 1:
            first_entered.set()
            if not release_first.wait(2):
                raise AssertionError("status_sink_timeout")

    control = StreamSessionControl(status_sink=sink)
    first = threading.Thread(
        target=lambda: control.stage("waiting", poll_requests=1), daemon=True
    )
    second = threading.Thread(
        target=lambda: control.stage("thinking", poll_requests=2), daemon=True
    )
    first.start()
    assert first_entered.wait(1)
    second.start()
    second.join(0.05)
    assert emitted == [1]
    release_first.set()
    first.join(1)
    second.join(1)
    assert not first.is_alive() and not second.is_alive()
    assert emitted == [1, 2]

    failing = StreamSessionControl(status_sink=lambda _snapshot: (_ for _ in ()).throw(
        RuntimeError("status output unavailable")
    ))
    failing.stage("waiting", poll_requests=1)
    failing.request_stop()
    assert failing.stop_requested is True


def test_stop_during_tts_rejects_first_audio_with_truthful_interruption() -> None:
    from nana.runtime.stream_session_control import SessionPlaybackPort, StreamSessionControl

    artifact, _publish = _handoff()
    control = StreamSessionControl()
    raw_port = _ControlledPort(control, stop_before_audio=True)
    controller, _port, factory, recorder = _controller(
        port=SessionPlaybackPort(raw_port, control)
    )

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = controller.play_generated(
            artifact,
            attempt_id="voice-session-stop-before-audio",
            now=NOW + 1,
        )

    assert result.status == "interrupted"
    assert result.start_evidence is None
    assert [record.state for record in recorder.records] == ["generated", "interrupted"]
    assert raw_port.first_audio_accepted is False
    assert factory.calls == 1
    assert control.snapshot()["state"] == "stopping"


def test_stop_after_first_audio_allows_completion_and_keeps_stopping_visible() -> None:
    from nana.runtime.stream_session_control import SessionPlaybackPort, StreamSessionControl

    artifact, _publish = _handoff(output_id="voice-session-completion")
    emitted = []
    control = StreamSessionControl(status_sink=emitted.append)
    raw_port = _ControlledPort(control, stop_before_audio=False)
    controller, _port, _factory, recorder = _controller(
        port=SessionPlaybackPort(raw_port, control)
    )

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = controller.play_generated(
            artifact,
            attempt_id="voice-session-finish-current",
            now=NOW + 1,
        )

    assert result.status == "delivered"
    assert raw_port.first_audio_accepted is True
    assert raw_port.continue_after_stop is True
    assert [record.state for record in recorder.records] == [
        "generated", "playback_started", "delivered",
    ]
    assert [item["state"] for item in emitted] == [
        "preparing_audio", "speaking", "stopping",
    ]
    assert control.snapshot()["state"] == "stopping"


def test_stop_inside_accepted_first_audio_receipt_still_allows_completion() -> None:
    from nana.runtime.stream_session_control import SessionPlaybackPort, StreamSessionControl

    artifact, _publish = _handoff(output_id="voice-session-stop-in-receipt")
    control = StreamSessionControl()

    class StopOnStartRecorder:
        def __init__(self):
            self.records = []

        def record_delivery(self, record):
            self.records.append(record)
            if record.state == "playback_started":
                control.request_stop("operator_stop")
            return True

    recorder = StopOnStartRecorder()
    raw_port = _ControlledPort(control, stop_before_audio=False)
    controller, _port, _factory, _recorder = _controller(
        port=SessionPlaybackPort(raw_port, control),
        recorder=recorder,
    )

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = controller.play_generated(
            artifact,
            attempt_id="voice-session-receipt-race",
            now=NOW + 1,
        )

    assert result.status == "delivered"
    assert raw_port.first_audio_accepted is True
    assert raw_port.continue_after_stop is True
    assert [record.state for record in recorder.records] == [
        "generated", "playback_started", "delivered",
    ]
    assert control.snapshot()["state"] == "stopping"


def test_late_generation_and_idle_stop_never_dispatch_playback_or_next_job() -> None:
    from nana.runtime.stream_session_control import StreamSessionControl
    from nana.runtime.stream_voice_host import PublicVoiceHost

    artifact, _publish = _handoff(output_id="voice-session-late-model")
    emitted = []
    control = StreamSessionControl(status_sink=emitted.append)
    host = _BlockingHost(_generated_result(artifact))
    playback = _Playback()
    voice = PublicVoiceHost(host=host, playback=playback, control=control)
    results = []

    worker = threading.Thread(
        target=lambda: results.append(voice.process_next(now=NOW + 1)),
        daemon=True,
    )
    worker.start()
    assert host.entered.wait(1)
    control.request_stop("operator_stop")
    host.release.set()
    worker.join(2)

    assert not worker.is_alive()
    assert results[0].host_result.status == "stopped"
    assert results[0].host_result.reason_code == "operator_stop"
    assert results[0].playback_result is None
    assert playback.calls == []
    assert host.calls == [(NOW + 1, True, 30.0)]
    assert [item["state"] for item in emitted] == ["thinking", "stopping"]

    stopped_again = voice.process_next(now=NOW + 2)
    assert stopped_again.host_result.status == "stopped"
    assert stopped_again.host_result.reason_code == "operator_stop"
    assert len(host.calls) == 1


def test_ambiguous_playback_halt_is_not_masked_by_session_stop() -> None:
    from nana.runtime.stream_cum5_voice_playback import CUM5PlaybackResult
    from nana.runtime.stream_session_control import StreamSessionControl
    from nana.runtime.stream_voice_host import PublicVoiceHost

    artifact, _publish = _handoff(output_id="voice-session-ambiguous")
    control = StreamSessionControl()
    host = _SequenceHost([_generated_result(artifact)])

    class AmbiguousPlayback:
        def snapshot(self):
            return {"active": False, "halted": False}

        def play_generated(self, _artifact, *, attempt_id, now):
            control.request_stop("operator_stop")
            return CUM5PlaybackResult("halted", "uncertain_stop")

    voice = PublicVoiceHost(host=host, playback=AmbiguousPlayback(), control=control)
    result = voice.process_next(now=NOW + 1)

    assert result.host_result.status == "halted"
    assert result.host_result.reason_code == "uncertain_stop"
    assert result.playback_result is not None
    assert result.playback_result.status == "halted"


def test_legacy_host_without_control_keeps_old_generate_signature() -> None:
    from nana.runtime.stream_voice_host import PublicVoiceHost

    artifact, _publish = _handoff(output_id="voice-session-legacy")

    class LegacyHost(_SequenceHost):
        def generate_next(self, *, now, full_replies_only=False):
            self.calls.append((now, full_replies_only))
            return self.results.popleft()

    class DeliveredPlayback:
        def __init__(self):
            self.calls = []

        def snapshot(self):
            return {"active": False, "halted": False}

        def play_generated(self, artifact, *, attempt_id, now):
            from nana.runtime.stream_cum5_voice_playback import CUM5PlaybackResult

            self.calls.append((artifact, attempt_id, now))
            return CUM5PlaybackResult("delivered", "playback_completed")

    host = LegacyHost([_generated_result(artifact)])
    playback = DeliveredPlayback()
    result = PublicVoiceHost(host=host, playback=playback).process_next(now=NOW + 1)

    assert result.playback_result is not None
    assert result.playback_result.status == "delivered"
    assert host.calls == [(NOW + 1, True)]
    assert len(playback.calls) == 1


def main() -> None:
    tests = (
        test_idle_stop_deadline_terminal_and_sanitized_status,
        test_status_sink_is_nonthrowing_and_fifo_across_threads,
        test_stop_during_tts_rejects_first_audio_with_truthful_interruption,
        test_stop_after_first_audio_allows_completion_and_keeps_stopping_visible,
        test_stop_inside_accepted_first_audio_receipt_still_allows_completion,
        test_late_generation_and_idle_stop_never_dispatch_playback_or_next_job,
        test_ambiguous_playback_halt_is_not_masked_by_session_stop,
        test_legacy_host_without_control_keeps_old_generate_signature,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"smoke_stream_session_control: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
