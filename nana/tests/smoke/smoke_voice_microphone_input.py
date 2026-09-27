"""Deterministic microphone-open regression tests; no real audio is captured."""

from __future__ import annotations

import contextlib
import io
import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nana.config import _parse_microphone_device_index
from nana.voice import engine as engine_module


class FakeRawStream:
    def __init__(self, *, close_error=None):
        self.closed = False
        self.stopped = False
        self.close_error = close_error

    def is_stopped(self):
        return self.stopped

    def stop_stream(self):
        self.stopped = True

    def close(self):
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class FakeAudio:
    def __init__(self, info, *, open_error=None):
        self.info = dict(info)
        self.open_error = open_error
        self.open_calls = 0
        self.terminated = False
        self.raw_stream = FakeRawStream()

    def get_default_input_device_info(self):
        return dict(self.info)

    def get_device_info_by_index(self, _index):
        return dict(self.info)

    def open(self, **_kwargs):
        self.open_calls += 1
        if self.open_error is not None:
            raise self.open_error
        return self.raw_stream

    def terminate(self):
        self.terminated = True


def _fake_microphone_type(audio, seen_indices):
    class FakePyAudioModule:
        @staticmethod
        def PyAudio():
            return audio

    class FakeMicrophone:
        pyaudio_module = FakePyAudioModule
        MicrophoneStream = engine_module.sr.Microphone.MicrophoneStream

        def __init__(self, device_index=None):
            seen_indices.append(device_index)
            self.device_index = device_index
            self.format = 8
            self.SAMPLE_RATE = 44100
            self.SAMPLE_WIDTH = 2
            self.CHUNK = 1024
            self.audio = None
            self.stream = None

    return FakeMicrophone


def _with_fake_microphone(audio, callback):
    original = engine_module.sr.Microphone
    seen_indices = []
    engine_module.sr.Microphone = _fake_microphone_type(audio, seen_indices)
    try:
        callback(seen_indices)
    finally:
        engine_module.sr.Microphone = original


def test_device_index_parser_defaults_to_auto():
    assert _parse_microphone_device_index(None) is None
    assert _parse_microphone_device_index("") is None
    assert _parse_microphone_device_index("auto") is None
    assert _parse_microphone_device_index("default") is None
    assert _parse_microphone_device_index("7") == 7


def test_default_input_opens_and_closes_cleanly():
    audio = FakeAudio(
        {"index": 1, "name": "Default Mic", "maxInputChannels": 1}
    )

    def run(seen_indices):
        with engine_module._open_microphone_source(None) as (source, info):
            assert seen_indices == [None]
            assert source.stream is not None
            assert info["index"] == 1
        assert source.stream is None

    _with_fake_microphone(audio, run)
    assert audio.open_calls == 1
    assert audio.raw_stream.closed is True
    assert audio.terminated is True


def test_output_device_is_rejected_without_none_close():
    audio = FakeAudio(
        {"index": 3, "name": "Sound Mapper Output", "maxInputChannels": 0}
    )

    def run(_seen_indices):
        try:
            with engine_module._open_microphone_source(3):
                raise AssertionError("output-only device should not open")
        except engine_module.MicrophoneOpenError as exc:
            message = str(exc)
            assert "index 3" in message
            assert "not an input device" in message
        else:
            raise AssertionError("expected MicrophoneOpenError")

    _with_fake_microphone(audio, run)
    assert audio.open_calls == 0
    assert audio.terminated is True


def test_open_error_preserves_original_cause():
    audio = FakeAudio(
        {"index": 1, "name": "Busy Mic", "maxInputChannels": 1},
        open_error=OSError("device busy"),
    )

    def run(_seen_indices):
        try:
            with engine_module._open_microphone_source(None):
                raise AssertionError("busy device should not open")
        except engine_module.MicrophoneOpenError as exc:
            assert "device busy" in str(exc)
            assert isinstance(exc.__cause__, OSError)
        else:
            raise AssertionError("expected MicrophoneOpenError")

    _with_fake_microphone(audio, run)
    assert audio.terminated is True


def test_close_error_does_not_mask_body_error():
    audio = FakeAudio(
        {"index": 1, "name": "Default Mic", "maxInputChannels": 1}
    )
    audio.raw_stream = FakeRawStream(close_error=OSError("driver close failed"))

    def run(_seen_indices):
        try:
            with engine_module._open_microphone_source(None):
                raise ValueError("capture failed")
        except ValueError as exc:
            assert str(exc) == "capture failed"
        else:
            raise AssertionError("expected capture failure")

    _with_fake_microphone(audio, run)
    assert audio.raw_stream.closed is True
    assert audio.terminated is True


def test_listen_voice_reports_input_error():
    voice = engine_module.VoiceEngine.__new__(engine_module.VoiceEngine)
    voice.state_lock = threading.Lock()
    voice.state = {
        "status": "idle",
        "listening": False,
        "last_error": None,
        "microphone_open_status": "not_tested",
    }

    @contextlib.contextmanager
    def fail_open(_device_index):
        raise engine_module.MicrophoneOpenError("Cannot open microphone system default: device busy")
        yield

    original_open = engine_module._open_microphone_source
    original_log = engine_module.log_event
    engine_module._open_microphone_source = fail_open
    engine_module.log_event = lambda *_args, **_kwargs: None
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            result = voice.listen_voice()
    finally:
        engine_module._open_microphone_source = original_open
        engine_module.log_event = original_log

    assert result == ""
    assert voice.state["status"] == "idle"
    assert voice.state["listening"] is False
    assert voice.state["microphone_open_status"] == "error"
    assert "device busy" in voice.state["last_error"]
    assert "NoneType" not in output.getvalue()
    assert "Voice input lỗi" in output.getvalue()


def main():
    tests = [
        test_device_index_parser_defaults_to_auto,
        test_default_input_opens_and_closes_cleanly,
        test_output_device_is_rejected_without_none_close,
        test_open_error_preserves_original_cause,
        test_close_error_does_not_mask_body_error,
        test_listen_voice_reports_input_error,
    ]
    failed = 0
    print("Voice Microphone Input Smoke")
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL: {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"Result: {len(tests) - failed}/{len(tests)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
