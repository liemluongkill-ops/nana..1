"""Deterministic smoke for CORE-RUNTIME-LIFECYCLE-1B.

No live Nana process or external backend is started. All owned tasks, threads,
voice, VTS, and bridge behavior use local fakes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from nana.cli import app
from nana.autonomy import AutonomyLoop, AutonomyState
from nana.runtime import external_bridge


class FakeOwnedLoop:
    def __init__(self, handles, *, start_thread=True):
        self.handles = handles
        self.stop_count = 0
        self._stop = threading.Event()
        self.thread = None
        if start_thread:
            self.thread = threading.Thread(
                target=self._stop.wait,
                daemon=True,
                name="lifecycle-smoke-autonomy",
            )
            self.thread.start()

    def stop(self, join_timeout_s=None):
        assert self.handles.shutdown_event.is_set()
        self.stop_count += 1
        self._stop.set()
        if self.thread is not None:
            self.thread.join(timeout=join_timeout_s)
            return not self.thread.is_alive()
        return True


class FakeVoice:
    def __init__(self, verify=None):
        self.shutdown_count = 0
        self.verify = verify

    def shutdown(self):
        if self.verify is not None:
            self.verify()
        self.shutdown_count += 1


class FakeVTS:
    def __init__(self, voice):
        self.voice = voice
        self.close_count = 0

    async def close(self):
        assert self.voice.shutdown_count == 1
        self.close_count += 1


class FakePresenceSession:
    def __init__(self):
        self.stop_count = 0
        self.stopped = threading.Event()

    def stop(self):
        self.stop_count += 1
        self.stopped.set()


class FakeNanaWebLauncher:
    def __init__(self):
        self.close_count = 0

    def close(self):
        self.close_count += 1


async def _never_until_cancelled(signal_seen, cancelled_seen):
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        assert signal_seen.is_set()
        cancelled_seen.set()
        raise


def test_normal_shutdown_and_idempotence():
    async def scenario():
        handles = app._RuntimeHandles()
        owned_loop = FakeOwnedLoop(handles)
        bridge_signal = asyncio.Event()
        bridge_finished = asyncio.Event()
        pulse_finished = asyncio.Event()
        mouth_finished = asyncio.Event()
        presence_session_finished = asyncio.Event()
        presence_session = FakePresenceSession()
        web_launcher = FakeNanaWebLauncher()
        handles.nana_web_launcher = web_launcher

        async def owned_worker(finished):
            try:
                await asyncio.Event().wait()
            finally:
                assert bridge_signal.is_set()
                finished.set()

        async def graceful_bridge():
            await bridge_signal.wait()
            bridge_finished.set()

        async def presence_session_worker():
            try:
                await asyncio.Event().wait()
            finally:
                assert presence_session.stopped.is_set()
                presence_session_finished.set()

        handles.pulse_task = asyncio.create_task(owned_worker(pulse_finished))
        handles.vts_mouth_task = asyncio.create_task(owned_worker(mouth_finished))
        handles.bridge_task = asyncio.create_task(graceful_bridge())
        handles.presence_session_server = presence_session
        handles.presence_session_task = asyncio.create_task(
            presence_session_worker()
        )
        await asyncio.sleep(0)

        poller = threading.Thread(
            target=handles.shutdown_event.wait,
            daemon=True,
            name="lifecycle-smoke-poller",
        )
        with handles.poller_lock:
            handles.poller_threads.add(poller)
        poller.start()

        def stop_bridge():
            assert owned_loop.stop_count == 1
            bridge_signal.set()

        def verify_voice_shutdown():
            assert web_launcher.close_count == 1
            assert handles.pulse_task.done()
            assert handles.vts_mouth_task.done()
            assert handles.bridge_task.done()
            assert handles.presence_session_task.done()
            assert not poller.is_alive()

        voice = FakeVoice(verify=verify_voice_shutdown)
        vts = FakeVTS(voice)
        await app._shutdown_runtime(
            handles,
            voice=voice,
            vts=vts,
            autonomy_loop=owned_loop,
            stop_bridge=stop_bridge,
            bridge_grace_seconds=0.1,
        )

        assert handles.shutdown_complete is True
        assert pulse_finished.is_set() and mouth_finished.is_set()
        assert bridge_finished.is_set()
        assert presence_session_finished.is_set()
        assert handles.pulse_task.done()
        assert handles.vts_mouth_task.done()
        assert handles.bridge_task.done()
        assert handles.presence_session_task.done()
        assert presence_session.stop_count == 1
        assert web_launcher.close_count == 1
        assert owned_loop.thread is not None and not owned_loop.thread.is_alive()
        assert not handles.poller_threads
        assert voice.shutdown_count == 1
        assert vts.close_count == 1
        assert web_launcher.close_count == 1
        assert presence_session.stop_count == 1

        await app._shutdown_runtime(
            handles,
            voice=voice,
            vts=vts,
            autonomy_loop=owned_loop,
            stop_bridge=stop_bridge,
            bridge_grace_seconds=0.1,
        )
        assert owned_loop.stop_count == 1
        assert voice.shutdown_count == 1
        assert vts.close_count == 1

    asyncio.run(scenario())
    print("  normal_start_shutdown_pass=True")
    print("  shutdown_twice_pass=True")
    print("  autonomy_thread_alive=False")
    print("  pulse_task_done=True")
    print("  vts_mouth_task_done=True")
    print("  bridge_task_done=True")
    print("  presence_session_task_done=True")
    print("  registered_poller_threads_alive=0")


def test_partial_start_cleanup():
    async def scenario():
        handles = app._RuntimeHandles()
        owned_loop = FakeOwnedLoop(handles, start_thread=False)
        pulse_finished = asyncio.Event()

        async def partial_pulse():
            try:
                await asyncio.Event().wait()
            finally:
                pulse_finished.set()

        handles.pulse_task = asyncio.create_task(partial_pulse())
        await asyncio.sleep(0)
        stop_count = 0

        def stop_bridge():
            nonlocal stop_count
            stop_count += 1

        await app._shutdown_runtime(
            handles,
            voice=None,
            vts=None,
            autonomy_loop=owned_loop,
            stop_bridge=stop_bridge,
            bridge_grace_seconds=0.01,
        )
        assert pulse_finished.is_set()
        assert handles.pulse_task.done()
        assert handles.vts_mouth_task is None
        assert handles.bridge_task is None
        assert stop_count == 1

        await app._shutdown_runtime(
            handles,
            voice=None,
            vts=None,
            autonomy_loop=owned_loop,
            stop_bridge=stop_bridge,
            bridge_grace_seconds=0.01,
        )
        assert stop_count == 1

    asyncio.run(scenario())
    print("  partial_start_cleanup_pass=True")


def test_bridge_forced_cancel_after_grace():
    async def scenario():
        handles = app._RuntimeHandles()
        owned_loop = FakeOwnedLoop(handles, start_thread=False)
        signal_seen = asyncio.Event()
        cancelled_seen = asyncio.Event()
        handles.bridge_task = asyncio.create_task(
            _never_until_cancelled(signal_seen, cancelled_seen)
        )
        await asyncio.sleep(0)

        await app._shutdown_runtime(
            handles,
            voice=None,
            vts=None,
            autonomy_loop=owned_loop,
            stop_bridge=signal_seen.set,
            bridge_grace_seconds=0.01,
        )
        assert signal_seen.is_set()
        assert cancelled_seen.is_set()
        assert handles.bridge_task.done()
        assert handles.bridge_task.cancelled()

    asyncio.run(scenario())
    print("  bridge_forced_cancel_fallback=True")


def test_poller_shutdown_calls_on_done_once():
    handles = app._RuntimeHandles()
    snapshot_seen = threading.Event()
    callback_lock = threading.Lock()
    callback_count = 0

    class SpeakingVoice:
        def say(self, _text):
            return None

        def snapshot(self):
            snapshot_seen.set()
            return {"speaking": True}

    def on_done():
        nonlocal callback_count
        with callback_lock:
            callback_count += 1

    thread = app._real_autonomy_tts(
        SpeakingVoice(),
        "lifecycle smoke",
        on_done,
        runtime_handles=handles,
    )
    assert thread is not None
    assert snapshot_seen.wait(timeout=1.0)
    handles.shutdown_event.set()
    thread.join(timeout=1.0)
    app._join_registered_pollers(handles)

    assert not thread.is_alive()
    assert callback_count == 1
    assert not handles.poller_threads
    print("  on_done_call_count=1")


def test_bridge_stop_is_signal_only():
    async def scenario():
        class FakeBridgeRuntime:
            def __init__(self):
                self.stop_count = 0

            def request_stop(self):
                self.stop_count += 1

        old_runtime = external_bridge._RUNTIME
        old_task = external_bridge._TASK
        fake_runtime = FakeBridgeRuntime()
        task = asyncio.create_task(asyncio.Event().wait())
        external_bridge._RUNTIME = fake_runtime
        external_bridge._TASK = task
        try:
            returned = external_bridge.stop_external_bridge_worker()
            assert returned is task
            assert fake_runtime.stop_count == 1
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            external_bridge._RUNTIME = old_runtime
            external_bridge._TASK = old_task

    asyncio.run(scenario())
    print("  bridge_stop_signal_before_cancel=True")


def _start_blocked_autonomy_loop():
    loop = AutonomyLoop()
    entered = threading.Event()
    release = threading.Event()

    def blocked_tick():
        entered.set()
        release.wait(timeout=5.0)
        return {"accepted": False, "reason": "released"}

    loop._do_tick = blocked_tick
    loop.start()
    assert entered.wait(timeout=1.0)
    return loop, release


def test_autonomy_stop_timeout_then_retry():
    loop, release = _start_blocked_autonomy_loop()
    started = time.monotonic()
    first_result = loop.stop(join_timeout_s=0.01)
    elapsed = time.monotonic() - started

    assert first_result is False
    assert loop._thread is not None and loop._thread.is_alive()
    assert loop.state == AutonomyState.STOPPING
    assert elapsed < 0.5

    release.set()
    second_result = loop.stop(join_timeout_s=1.0)
    assert second_result is True
    assert not loop._thread.is_alive()
    assert loop.state == AutonomyState.STOPPED
    print(
        "  first_stop=False | thread_alive=True | state=stopping | "
        "retry_stop=True | final_thread_alive=False | final_state=stopped"
    )


def test_unexpected_autonomy_exit_can_restart_once():
    loop = AutonomyLoop()
    first_tick_entered = threading.Event()
    second_tick_entered = threading.Event()
    release_second_tick = threading.Event()
    tick_count = 0
    thread_exceptions = []
    original_excepthook = threading.excepthook

    def controlled_tick():
        nonlocal tick_count
        tick_count += 1
        if tick_count == 1:
            first_tick_entered.set()
            raise RuntimeError("deterministic lifecycle smoke failure")
        second_tick_entered.set()
        release_second_tick.wait(timeout=1.0)
        return {"accepted": False, "reason": "released"}

    loop._do_tick = controlled_tick
    threading.excepthook = lambda args: thread_exceptions.append(args.exc_value)
    try:
        loop.start()
        first_thread = loop._thread
        assert first_thread is not None
        assert first_tick_entered.wait(timeout=1.0)
        first_thread.join(timeout=1.0)
        assert not first_thread.is_alive()
        assert loop.state == AutonomyState.STOPPED
        assert len(thread_exceptions) == 1
        assert isinstance(thread_exceptions[0], RuntimeError)

        loop.start()
        second_thread = loop._thread
        assert second_thread is not None and second_thread is not first_thread
        assert second_tick_entered.wait(timeout=1.0)
        loop.start()
        assert loop._thread is second_thread
        assert second_thread.is_alive()

        release_second_tick.set()
        assert loop.stop(join_timeout_s=1.0) is True
        assert not second_thread.is_alive()
        assert loop.state == AutonomyState.STOPPED
    finally:
        threading.excepthook = original_excepthook
        release_second_tick.set()
        loop.stop(join_timeout_s=1.0)

    print(
        "  unexpected_exit_state=stopped | restart_thread_new=True | "
        "second_start_idempotent=True | final_stop_joined=True"
    )


def test_shutdown_waits_for_autonomy_completion():
    async def scenario():
        handles = app._RuntimeHandles()
        loop, release = _start_blocked_autonomy_loop()
        voice = FakeVoice()
        vts = FakeVTS(voice)

        try:
            await app._shutdown_runtime(
                handles,
                voice=voice,
                vts=vts,
                autonomy_loop=loop,
                stop_bridge=lambda: None,
                bridge_grace_seconds=0.0,
                autonomy_join_timeout_s=0.01,
                poller_join_timeout_s=0.01,
            )
        except RuntimeError as exc:
            assert "autonomy_thread_alive" in str(exc)
        else:
            raise AssertionError("shutdown must report blocked autonomy thread")

        assert handles.shutdown_complete is False
        assert loop._thread is not None and loop._thread.is_alive()
        assert loop.state == AutonomyState.STOPPING
        assert voice.shutdown_count == 0
        assert vts.close_count == 0

        release.set()
        completed = await app._shutdown_runtime(
            handles,
            voice=voice,
            vts=vts,
            autonomy_loop=loop,
            stop_bridge=lambda: None,
            bridge_grace_seconds=0.0,
            autonomy_join_timeout_s=1.0,
            poller_join_timeout_s=0.01,
        )
        assert completed is True
        assert handles.shutdown_complete is True
        assert not loop._thread.is_alive()
        assert loop.state == AutonomyState.STOPPED
        assert voice.shutdown_count == 1
        assert vts.close_count == 1

    asyncio.run(scenario())
    print("  shutdown_complete_only_after_autonomy_thread_exit=True")


def test_shutdown_waits_for_registered_pollers():
    async def scenario():
        handles = app._RuntimeHandles()
        owned_loop = FakeOwnedLoop(handles, start_thread=False)
        release = threading.Event()
        poller = threading.Thread(
            target=release.wait,
            daemon=True,
            name="lifecycle-smoke-stubborn-poller",
        )
        with handles.poller_lock:
            handles.poller_threads.add(poller)
        poller.start()

        voice = FakeVoice()
        vts = FakeVTS(voice)
        try:
            await app._shutdown_runtime(
                handles,
                voice=voice,
                vts=vts,
                autonomy_loop=owned_loop,
                stop_bridge=lambda: None,
                bridge_grace_seconds=0.0,
                autonomy_join_timeout_s=0.01,
                poller_join_timeout_s=0.01,
            )
        except RuntimeError as exc:
            assert "poller_threads_alive" in str(exc)
        else:
            raise AssertionError("shutdown must report registered live poller")

        assert handles.shutdown_complete is False
        assert poller.is_alive()
        assert voice.shutdown_count == 0
        assert vts.close_count == 0

        release.set()
        completed = await app._shutdown_runtime(
            handles,
            voice=voice,
            vts=vts,
            autonomy_loop=owned_loop,
            stop_bridge=lambda: None,
            bridge_grace_seconds=0.0,
            autonomy_join_timeout_s=0.01,
            poller_join_timeout_s=1.0,
        )
        assert completed is True
        assert handles.shutdown_complete is True
        assert not poller.is_alive()
        assert not handles.poller_threads
        assert voice.shutdown_count == 1
        assert vts.close_count == 1

    asyncio.run(scenario())
    print("  shutdown_complete_only_after_registered_pollers_exit=True")


def main():
    tests = [
        test_normal_shutdown_and_idempotence,
        test_partial_start_cleanup,
        test_bridge_forced_cancel_after_grace,
        test_poller_shutdown_calls_on_done_once,
        test_bridge_stop_is_signal_only,
        test_autonomy_stop_timeout_then_retry,
        test_unexpected_autonomy_exit_can_restart_once,
        test_shutdown_waits_for_autonomy_completion,
        test_shutdown_waits_for_registered_pollers,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_core_runtime_lifecycle: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
