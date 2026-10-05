"""Offline Task 9 composition contract smoke."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nana.runtime.nana_web_ownership import OwnershipResult  # noqa: E402


EPOCH = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class FakeLauncher:
    def ownership_lease(self):
        return object()


class FakeOwnership:
    def verify(self, _lease):
        return OwnershipResult(True, "trusted")


class FakeServer:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True
        return True

    async def stop(self, _drain_seconds=15.0):
        self.stopped = True

    def begin_shutdown(self):
        self.admission_closed = True

    def enqueue_event(self, session, event):
        return True


def test_default_off_does_not_construct_or_bind_bridge():
    from nana.cli import app

    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return FakeServer(**kwargs)

    runtime = app._build_private_web_chat_runtime(
        enabled=False,
        voice=object(),
        turn_lock=asyncio.Lock(),
        dispatch_turn_unlocked=lambda *_args, **_kwargs: None,
        launcher=FakeLauncher(),
        ownership_verifier=FakeOwnership(),
        server_factory=factory,
        server_epoch=EPOCH,
    )
    assert runtime is None
    assert calls == []


def test_enabled_composition_reuses_lock_dispatcher_and_single_voice():
    from nana.cli import app

    shared_lock = asyncio.Lock()
    dispatch = lambda *_args, **_kwargs: None
    calls = []

    def factory(**kwargs):
        calls.append(kwargs)
        return FakeServer(**kwargs)

    runtime = app._build_private_web_chat_runtime(
        enabled=True,
        voice=object(),
        turn_lock=shared_lock,
        dispatch_turn_unlocked=dispatch,
        launcher=FakeLauncher(),
        ownership_verifier=FakeOwnership(),
        server_factory=factory,
        server_epoch=EPOCH,
    )
    assert runtime is not None
    assert len(calls) == 1
    coordinator = runtime.coordinator
    assert coordinator.turn_lock is shared_lock
    assert coordinator.dispatch_turn_unlocked is dispatch
    assert runtime.voice is not None
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8767


def test_shutdown_waits_for_receipt_and_timeout_never_invents_delivery():
    from nana.runtime.private_web_chat_runtime import _build_private_web_chat_runtime
    from nana.runtime.private_voice_receipts import PrivateVoiceContext
    async def scenario(completes):
        runtime = _build_private_web_chat_runtime(enabled=True, voice=object(), turn_lock=asyncio.Lock(),
            dispatch_turn_unlocked=lambda *args: None, launcher=FakeLauncher(), ownership_verifier=FakeOwnership(),
            server_factory=FakeServer, server_epoch=EPOCH)
        await runtime.start()
        ledger = runtime.receipt_ledger
        receipt = ledger.queued(PrivateVoiceContext(EPOCH, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
            '11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'), engine_ticket=1)
        ledger.first_audio(receipt, engine_ticket=1)
        closed_states = []
        original_stop = runtime.server.stop
        async def record_stop(seconds):
            closed_states.append(ledger.current(receipt).state)
            await original_stop(seconds)
        runtime.server.stop = record_stop
        async def finish():
            await asyncio.sleep(.01)
            assert not runtime.server.stopped
            ledger.complete(receipt, engine_ticket=1, completed=True)
        finishing = asyncio.create_task(finish()) if completes else None
        await runtime.stop(.06)
        if finishing: await finishing
        assert closed_states == ['delivered' if completes else 'unknown']
        assert ledger.current(receipt) is None
        await runtime.stop(.06)
        assert len(closed_states) == 1
        assert runtime.server.admission_closed and runtime.server.stopped
    asyncio.run(scenario(True)); asyncio.run(scenario(False))


def main() -> None:
    tests = [
        test_default_off_does_not_construct_or_bind_bridge,
        test_enabled_composition_reuses_lock_dispatcher_and_single_voice,
        test_shutdown_waits_for_receipt_and_timeout_never_invents_delivery,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_private_web_chat_composition: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
