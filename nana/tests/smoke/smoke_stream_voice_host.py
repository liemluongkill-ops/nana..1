"""Focused provider-free smoke for the Stream V1 voice host composer."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
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
    if event == "open" and args:
        raw_path = args[0]
        if isinstance(raw_path, (str, bytes)):
            path = str(raw_path).replace("\\", "/").lower()
            protected = (
                "/nana/data/" in path
                or path.endswith("/.env")
                or path.endswith("/nana/config.py")
            )
            if protected:
                raise AssertionError("voice host smoke blocked production data/config access")
        mode = args[1] if len(args) > 1 else ""
        flags = args[2] if len(args) > 2 else 0
        writable_mode = isinstance(mode, str) and any(mark in mode for mark in "wax+")
        writable_flags = isinstance(flags, int) and bool(
            flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC)
        )
        if writable_mode or writable_flags:
            raise AssertionError("voice host smoke blocked file write")
    if event in {"socket.connect", "subprocess.Popen", "os.system"}:
        raise AssertionError("voice host smoke blocked network/process access")


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

_ENV_BEFORE = dict(os.environ)


from nana.tests.smoke.smoke_stream_cum3_youtube_publish import _artifact
from nana.tests.smoke.smoke_stream_cum4_host import (
    FLAGS,
    NOW,
    _RaisingPublisher,
    _host,
    _message,
)


VOICE_FLAGS = {
    **FLAGS,
    "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "0",
    "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED": "1",
}


class _Playback:
    def __init__(self, outcomes=("delivered",)):
        self.outcomes = deque(outcomes)
        self.calls = []
        self.spoken_attempts = set()
        self.speech_calls = 0
        self.active = False
        self.halted = False

    def snapshot(self):
        return {"active": self.active, "halted": self.halted}

    def play_generated(self, artifact, *, attempt_id, now):
        from nana.runtime.stream_cum5_voice_playback import CUM5PlaybackResult

        self.calls.append((artifact, attempt_id, now))
        status = self.outcomes.popleft() if self.outcomes else "delivered"
        reasons = {
            "delivered": "playback_completed",
            "interrupted": "sink_interrupted",
            "halted": "uncertain_stop",
        }
        if status == "delivered" and attempt_id not in self.spoken_attempts:
            self.spoken_attempts.add(attempt_id)
            self.speech_calls += 1
        return CUM5PlaybackResult(status, reasons[status])


class _FailingModel:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("provider unavailable")


class _SequenceHost:
    def __init__(self, results):
        self.results = deque(results)
        self.calls = []

    def snapshot(self):
        return {"queued": len(self.results), "halted": False, "halt_reason": ""}

    def generate_next(self, *, now, full_replies_only=False):
        self.calls.append((now, full_replies_only))
        return self.results.popleft()


class _BlockingHost(_SequenceHost):
    def __init__(self, result):
        super().__init__([result])
        self.entered = threading.Event()
        self.release = threading.Event()

    def generate_next(self, *, now, full_replies_only=False):
        self.calls.append((now, full_replies_only))
        self.entered.set()
        if not self.release.wait(2):
            raise AssertionError("blocking_host_timeout")
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


def test_full_reply_generates_and_plays_without_text_publisher() -> None:
    from nana.runtime.stream_voice_host import PublicVoiceHost

    host, model, sender = _host(["full_reply"], publisher=None)
    playback = _Playback()
    voice = PublicVoiceHost(host=host, playback=playback)

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        host.ingest_response(
            {"items": [_message(0, "Yumi oi?")]},
            received_at=NOW,
            now=NOW,
        )
        result = voice.process_next(now=NOW)

    assert result.host_result.status == "generated"
    assert result.host_result.response_artifact is not None
    assert result.playback_result is not None
    assert result.playback_result.status == "delivered"
    assert len(playback.calls) == 1 and playback.speech_calls == 1
    artifact, attempt_id, requested_at = playback.calls[0]
    assert artifact is result.host_result.response_artifact
    assert attempt_id.startswith("voice-")
    assert attempt_id != artifact.source_attempt_id
    assert requested_at == NOW
    assert len(model.calls) == 1 and sender.calls == []


def test_ack_and_social_skip_are_silent_on_voice_path() -> None:
    from nana.runtime.stream_voice_host import PublicVoiceHost

    host, model, sender = _host(["skip", "ack_only"], publisher=None)
    playback = _Playback()
    voice = PublicVoiceHost(host=host, playback=playback)

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        admitted = host.ingest_response(
            {"items": [_message(0, "noise"), _message(1, "hmm")]},
            received_at=NOW + 1,
            now=NOW + 1,
        )
        result = voice.process_next(now=NOW + 1)

    assert admitted.skipped == 1 and admitted.queued == 1
    assert result.host_result.status == "skipped"
    assert result.host_result.reason_code == "ack_only_silent"
    assert result.host_result.skipped == 1
    assert result.playback_result is None
    assert model.calls == [] and playback.calls == [] and sender.calls == []
    assert host.snapshot()["queued"] == 0


def test_playback_busy_preflight_does_not_consume_job() -> None:
    from nana.runtime.stream_voice_host import PublicVoiceHost

    host, model, _sender = _host(["full_reply"], publisher=None)
    playback = _Playback()
    playback.active = True
    voice = PublicVoiceHost(host=host, playback=playback)

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        host.ingest_response(
            {"items": [_message(0, "wait for voice?")]},
            received_at=NOW,
            now=NOW,
        )
        held = voice.process_next(now=NOW)
        playback.active = False
        delivered = voice.process_next(now=NOW + 1)

    assert held.host_result.status == "held"
    assert held.host_result.reason_code == "playback_busy"
    assert held.host_result.queued == 1 and held.playback_result is None
    assert delivered.playback_result is not None
    assert delivered.playback_result.status == "delivered"
    assert len(model.calls) == 1 and len(playback.calls) == 1


def test_failed_generation_never_calls_playback() -> None:
    from nana.runtime.stream_voice_host import PublicVoiceHost

    model = _FailingModel()
    host, _model, sender = _host(["full_reply"], publisher=None, model=model)
    playback = _Playback()
    voice = PublicVoiceHost(host=host, playback=playback)

    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        host.ingest_response(
            {"items": [_message(0, "model fails?")]},
            received_at=NOW,
            now=NOW,
        )
        result = voice.process_next(now=NOW)

    assert result.host_result.status == "failed"
    assert result.host_result.reason_code == "provider_error"
    assert result.playback_result is None
    assert len(model.calls) == 1
    assert playback.calls == [] and sender.calls == []


def test_duplicate_artifact_reuses_attempt_and_does_not_respeak() -> None:
    from nana.runtime.stream_voice_host import PublicVoiceHost

    artifact = _artifact(output_id="voice-output-stable")
    other = replace(artifact, output_id="voice-output-other")
    host = _SequenceHost([
        _generated_result(artifact),
        _generated_result(artifact),
        _generated_result(other),
    ])
    playback = _Playback(("delivered", "delivered", "delivered"))
    voice = PublicVoiceHost(host=host, playback=playback)

    first = voice.process_next(now=NOW)
    duplicate = voice.process_next(now=NOW + 1)
    distinct = voice.process_next(now=NOW + 2)

    assert first.playback_result is not None
    assert duplicate.playback_result is not None
    assert distinct.playback_result is not None
    attempts = [call[1] for call in playback.calls]
    assert attempts[0] == attempts[1]
    assert attempts[0] != attempts[2]
    assert playback.speech_calls == 2
    assert [full_only for _now, full_only in host.calls] == [True, True, True]


def test_parallel_step_is_held_before_second_generation() -> None:
    from nana.runtime.stream_voice_host import PublicVoiceHost

    host = _BlockingHost(_generated_result(_artifact(output_id="voice-output-blocked")))
    playback = _Playback()
    voice = PublicVoiceHost(host=host, playback=playback)
    first_result = []

    worker = threading.Thread(
        target=lambda: first_result.append(voice.process_next(now=NOW)),
        daemon=True,
    )
    worker.start()
    assert host.entered.wait(1)
    held = voice.process_next(now=NOW + 1)
    host.release.set()
    worker.join(2)

    assert not worker.is_alive()
    assert held.host_result.status == "held"
    assert held.host_result.reason_code == "voice_host_busy"
    assert held.playback_result is None
    assert len(host.calls) == 1
    assert first_result[0].playback_result is not None
    assert first_result[0].playback_result.status == "delivered"


def test_interrupted_or_halted_playback_latches_without_text_fallback() -> None:
    from nana.runtime.stream_voice_host import PublicVoiceHost

    for playback_status in ("interrupted", "halted"):
        publisher = _RaisingPublisher()
        host, model, sender = _host(
            ["full_reply", "full_reply"],
            publisher=publisher,
        )
        playback = _Playback((playback_status,))
        voice = PublicVoiceHost(host=host, playback=playback)

        with patch.dict(os.environ, VOICE_FLAGS, clear=False):
            host.ingest_response(
                {"items": [_message(0, "first?"), _message(1, "second?")]},
                received_at=NOW + 1,
                now=NOW + 1,
            )
            failed = voice.process_next(now=NOW + 1)
            stopped = voice.process_next(now=NOW + 2)

        assert failed.host_result.status == "halted"
        assert failed.host_result.response_artifact is not None
        assert failed.playback_result is not None
        assert failed.playback_result.status == playback_status
        assert stopped.host_result.status == "halted"
        assert stopped.playback_result is None
        assert host.snapshot()["queued"] == 1
        assert len(model.calls) == 1 and len(playback.calls) == 1
        assert publisher.calls == [] and sender.calls == []


def main() -> None:
    tests = (
        test_full_reply_generates_and_plays_without_text_publisher,
        test_ack_and_social_skip_are_silent_on_voice_path,
        test_playback_busy_preflight_does_not_consume_job,
        test_failed_generation_never_calls_playback,
        test_duplicate_artifact_reuses_attempt_and_does_not_respeak,
        test_parallel_step_is_held_before_second_generation,
        test_interrupted_or_halted_playback_latches_without_text_fallback,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    assert dict(os.environ) == _ENV_BEFORE
    assert "nana.memory" not in sys.modules
    assert "nana.config" not in sys.modules
    assert "nana.brain.llmgate_client" not in sys.modules
    assert not any(name.startswith("nana.voice.") for name in sys.modules)
    voice_package = sys.modules.get("nana.voice")
    assert voice_package is None or getattr(voice_package, "__file__", None) is None
    print(f"smoke_stream_voice_host: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
