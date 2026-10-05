"""Provider-free smoke for receipt-bound private avatar PCM levels."""

from __future__ import annotations

from pathlib import Path
import queue
import sys
import threading
from types import SimpleNamespace
import types

import numpy as np
import sounddevice as sd


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

package = types.ModuleType("nana")
package.__path__ = [str(ROOT / "nana")]
sys.modules["nana"] = package


def _audit(event, args) -> None:
    if event == "open" and args and isinstance(args[0], (str, bytes)):
        path = str(args[0]).replace("\\", "/").lower()
        if path.endswith("/.env") or "/nana/data/" in path or "/runtime_logs/" in path:
            raise AssertionError(f"production data access forbidden: {path}")
    if event == "socket.connect":
        address = args[1] if len(args) > 1 else None
        host = address[0] if isinstance(address, tuple) and address else None
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError(f"non-loopback network forbidden: {address!r}")


sys.addaudithook(_audit)

original_read_text = Path.read_text


def _fixture_read_text(path, *args, **kwargs):
    if path.name == ".env":
        raise OSError("private avatar voice smoke does not load .env")
    return original_read_text(path, *args, **kwargs)


Path.read_text = _fixture_read_text
try:
    __import__("nana.config")
finally:
    Path.read_text = original_read_text

logger = types.ModuleType("nana.runtime.logger")
logger.log_event = lambda *_args, **_kwargs: None
sys.modules["nana.runtime.logger"] = logger

from nana.runtime.private_voice_receipts import (  # noqa: E402
    PrivateVoiceContext,
    PrivateVoiceReceiptBinding,
    PrivateVoiceReceiptLedger,
)
from nana.voice.lipsync import LipsyncManager, PreparedAudio  # noqa: E402


def _binding(ticket: int, turn_id: str) -> PrivateVoiceReceiptBinding:
    context = PrivateVoiceContext(
        server_epoch="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        session_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        turn_id=turn_id,
        correlation_id=f"{ticket:08d}-cccc-4ccc-8ccc-cccccccccccc",
    )
    ledger = PrivateVoiceReceiptLedger()
    receipt = ledger.queued(context, engine_ticket=ticket)
    return PrivateVoiceReceiptBinding(
        receipt_context=context,
        receipt_id=receipt.receipt_id,
        receipt_ledger=ledger,
        ticket=ticket,
    )


class _Signals:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    def publish_pcm_level(self, binding, level) -> None:
        self.calls.append((binding, float(level)))
        if self.fail:
            raise RuntimeError("fake private avatar observer failure")


class _WriteStream:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def write(self, frame):
        if self.fail:
            raise OSError("fake sink failure")
        self.writes.append(np.asarray(frame, dtype=np.float32).copy())
        return False


class _CallbackStatus:
    output_underflow = False


class _CallbackStream:
    def __init__(self, owner, **kwargs) -> None:
        self.owner = owner
        self.callback = kwargs["callback"]
        self.finished_callback = kwargs["finished_callback"]
        self.frames = 20
        self.abort_event = threading.Event()
        self.thread = None

    def __enter__(self):
        self.owner.entered.set()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_args):
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        return False

    def abort(self):
        self.abort_event.set()

    def _run(self):
        try:
            while not self.abort_event.is_set():
                outdata = np.full((self.frames, 1), np.nan, dtype=np.float32)
                stopped = False
                try:
                    self.callback(outdata, self.frames, None, _CallbackStatus())
                except sd.CallbackStop:
                    stopped = True
                except sd.CallbackAbort:
                    break
                self.owner.outputs.append(outdata.reshape(-1).copy())
                if stopped:
                    break
        finally:
            self.finished_callback()


class _CallbackFactory:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.outputs = []

    def __call__(self, **kwargs):
        return _CallbackStream(self, **kwargs)


def test_engine_callbacks_capture_exact_receipt_and_only_private_playback() -> None:
    from nana.voice.engine import VoiceEngine

    old_binding = _binding(1, "11111111-1111-4111-8111-111111111111")
    new_binding = _binding(2, "22222222-2222-4222-8222-222222222222")
    signals = _Signals()
    engine = VoiceEngine.__new__(VoiceEngine)
    engine.set_private_avatar_signals(signals)

    old_options = engine._private_pcm_receipt_options(old_binding)
    new_options = engine._private_pcm_receipt_options(new_binding)
    new_options["on_pcm_level"](0.4)
    old_options["on_pcm_level"](0.2)

    assert signals.calls == [(new_binding, 0.4), (old_binding, 0.2)]
    assert engine._private_pcm_receipt_options(None) == {}
    engine.set_private_avatar_signals(None)
    assert set(engine._private_pcm_receipt_options(old_binding)) == {"on_first_audio"}

    class _Lipsync:
        def play_prepared_audio_receipted(self, _prepared, **kwargs):
            assert kwargs["on_first_audio"](4) is True
            kwargs["on_pcm_level"](0.3)
            return SimpleNamespace(completed=True, stop_confirmed=True)

    engine.lipsync = _Lipsync()
    engine._shutdown_requested = lambda: False
    engine.set_private_avatar_signals(signals)
    result = engine._private_prepared_playback(
        SimpleNamespace(duration_seconds=0.01),
        old_binding,
    )
    assert result.completed is True
    assert signals.calls[-1] == (old_binding, 0.3)


def test_prepared_pcm_level_is_sink_bound_raw_and_observer_safe() -> None:
    manager = LipsyncManager()
    global_levels = []
    manager.set_pcm_level_callback(global_levels.append)
    private_levels = []
    stream = _WriteStream()

    def private_level(level) -> None:
        private_levels.append((len(stream.writes), float(level)))

    result = manager.play_prepared_audio_receipted(
        PreparedAudio(np.full(40, 0.25, dtype=np.float32), 1000, 0.04),
        on_first_audio=lambda _size: True,
        on_pcm_level=private_level,
        output_stream_factory=lambda **_kwargs: stream,
    )
    assert result.completed is True
    assert [count for count, _level in private_levels[:-1]] == [1, 2]
    assert [level for _count, level in private_levels[:-1]] == global_levels[:2]
    assert private_levels[-1] == (2, 0.0)

    public_count = len(private_levels)
    public_result = manager.play_prepared_audio_receipted(
        PreparedAudio(np.full(20, 0.5, dtype=np.float32), 1000, 0.02),
        on_first_audio=lambda _size: True,
        emit_mouth=False,
        output_stream_factory=lambda **_kwargs: _WriteStream(),
    )
    assert public_result.completed is True
    assert len(private_levels) == public_count

    failed_levels = []
    failed = manager.play_prepared_audio_receipted(
        PreparedAudio(np.full(20, 0.5, dtype=np.float32), 1000, 0.02),
        on_first_audio=lambda _size: True,
        on_pcm_level=failed_levels.append,
        output_stream_factory=lambda **_kwargs: _WriteStream(fail=True),
    )
    assert failed.completed is False and failed.abort_reason == "playback_error"
    assert failed_levels == []

    broken_calls = []

    def broken(level) -> None:
        broken_calls.append(float(level))
        raise RuntimeError("observer failed")

    observer_failure = manager.play_prepared_audio_receipted(
        PreparedAudio(np.full(20, 0.5, dtype=np.float32), 1000, 0.02),
        on_first_audio=lambda _size: True,
        on_pcm_level=broken,
        output_stream_factory=lambda **_kwargs: _WriteStream(),
    )
    assert observer_failure.completed is True
    assert broken_calls[-1] == 0.0


def test_nonblocking_pcm_level_reaches_blocking_output_after_write() -> None:
    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.full(20, 0.25, dtype=np.float32))
    decoder_eof = threading.Event()
    decoder_eof.set()
    stream = _WriteStream()
    levels = []
    done = threading.Event()
    result_box = {}

    manager.play_pcm_stream_nonblocking(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=0,
        stall_timeout_s=1.0,
        on_pcm_level=lambda level: levels.append((len(stream.writes), float(level))),
        on_done=lambda result: (result_box.setdefault("result", result), done.set()),
        output_stream_factory=lambda **_kwargs: stream,
    )
    assert done.wait(timeout=2.0)
    assert result_box["result"].completed is True
    assert levels[0][0] == 1 and levels[0][1] > 0.0
    assert levels[-1] == (1, 0.0)


def test_callback_pcm_level_is_output_bound_and_terminally_silent() -> None:
    manager = LipsyncManager()
    pcm_queue = queue.Queue()
    pcm_queue.put(np.full(40, 0.4, dtype=np.float32))
    decoder_eof = threading.Event()
    decoder_eof.set()
    factory = _CallbackFactory()
    levels = []

    result = manager._play_pcm_callback_queue(
        pcm_queue,
        decoder_eof,
        samplerate=1000,
        startup_buffer_ms=0,
        stall_timeout_s=1.0,
        on_pcm_level=lambda level: levels.append(
            (factory.entered.is_set(), float(level))
        ),
        output_stream_factory=factory,
    )
    assert result.completed is True
    assert levels[0][0] is True and levels[0][1] > 0.0
    assert levels[-1] == (True, 0.0)


def main() -> None:
    tests = [
        test_engine_callbacks_capture_exact_receipt_and_only_private_playback,
        test_prepared_pcm_level_is_sink_bound_raw_and_observer_safe,
        test_nonblocking_pcm_level_reaches_blocking_output_after_write,
        test_callback_pcm_level_is_output_bound_and_terminally_silent,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_private_avatar_voice: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
