"""Provider-free async smoke for the private turn coordinator.

The smoke uses only an injected asyncio lock, fake dispatcher and event sink.
It never invokes a model, memory/history, voice, browser, OBS, network or
persistent-data boundary.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from nana.runtime.private_turn_coordinator import (  # noqa: E402
    OBSERVATION_DEADLINE_SECONDS,
    TURN_LEDGER_LIMIT,
    WALL_DEADLINE_SECONDS,
    LOCK_WAIT_SECONDS,
    PrivateTurnCoordinator,
    PrivateTurnResult,
    TurnKey,
)
from nana.runtime.private_web_chat_protocol import (  # noqa: E402
    ClientFrame,
    PROTOCOL_NAME,
    SessionContext,
)


EPOCH = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_EPOCH = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
SESSION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
CLIENT_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

SESSION = SessionContext(EPOCH, SESSION_ID, CLIENT_ID)


def submit_frame(
    turn_id: str,
    text: str,
    *,
    correlation_id: str | None = None,
    session_id: str = SESSION_ID,
) -> ClientFrame:
    return ClientFrame(
        message_type="chat.submit",
        server_epoch=EPOCH,
        session_id=session_id,
        turn_id=turn_id,
        correlation_id=correlation_id
        or "22222222-2222-4222-8222-222222222222",
        revision=0,
        payload={"text": text},
    )


def reconcile_frame(
    *,
    turn_server_epoch: str = EPOCH,
    turn_id: str = "11111111-1111-4111-8111-111111111111",
    correlation_id: str = "22222222-2222-4222-8222-222222222222",
    revision: int = 0,
) -> ClientFrame:
    return ClientFrame(
        message_type="chat.reconcile",
        server_epoch=EPOCH,
        session_id=SESSION_ID,
        turn_id=turn_id,
        correlation_id=correlation_id,
        revision=revision,
        payload={
            "turn_server_epoch": turn_server_epoch,
            "client_instance_id": CLIENT_ID,
            "turn_id": turn_id,
            "correlation_id": correlation_id,
            "revision": revision,
        },
    )


def make_coordinator(
    *,
    dispatch_turn_unlocked,
    event_sink=None,
    turn_lock: asyncio.Lock | None = None,
    **kwargs: Any,
) -> PrivateTurnCoordinator:
    return PrivateTurnCoordinator(
        turn_lock=turn_lock or asyncio.Lock(),
        dispatch_turn_unlocked=dispatch_turn_unlocked,
        server_epoch=EPOCH,
        event_sink=event_sink,
        **kwargs,
    )


async def wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(0)


async def test_duplicate_submit_returns_snapshot_without_dispatch() -> None:
    calls: list[str] = []

    async def dispatch_unlocked(text, observer):
        calls.append(text)
        return PrivateTurnResult(False, "complete", "reply", None, 7)

    coordinator = make_coordinator(dispatch_turn_unlocked=dispatch_unlocked)
    turn_id = "11111111-1111-4111-8111-111111111111"
    first = await coordinator.submit(SESSION, submit_frame(turn_id, "hello"))
    second = await coordinator.submit(SESSION, submit_frame(turn_id, "hello"))
    assert first.key.turn_id == second.key.turn_id
    assert calls == ["hello"]
    assert second.turn_state == "complete"
    await coordinator.drain(0.1)


async def test_acceptance_event_precedes_thinking_and_revision_starts_at_one() -> None:
    events: list[dict[str, Any]] = []
    release = asyncio.Event()

    async def dispatch_unlocked(text, observer):
        await release.wait()
        return PrivateTurnResult(False, "complete", "done", None, None)

    def sink(session, event):
        events.append(dict(event))

    coordinator = make_coordinator(dispatch_turn_unlocked=dispatch_unlocked, event_sink=sink)
    turn_id = "11111111-1111-4111-8111-111111111111"
    accepted = await coordinator.submit(SESSION, submit_frame(turn_id, "hello"))
    assert accepted.turn_state == "accepted"
    assert events[0]["type"] == "turn.state"
    assert events[0]["revision"] == 1
    assert events[0]["payload"] == {"state": "accepted"}
    release.set()
    await coordinator.drain(1.0)
    states = [event["payload"]["state"] for event in events if event["type"] == "turn.state"]
    assert states[:2] == ["accepted", "thinking"]
    assert states[-2:] == ["generated", "complete"]
    assert [event["revision"] for event in events] == sorted(
        event["revision"] for event in events
    )


async def test_one_pending_session_slot_rejects_second_turn() -> None:
    release = asyncio.Event()

    async def dispatch_unlocked(text, observer):
        await release.wait()
        return PrivateTurnResult(False, "complete", text, None, None)

    coordinator = make_coordinator(dispatch_turn_unlocked=dispatch_unlocked)
    first_task = asyncio.create_task(
        coordinator.submit(
            SESSION,
            submit_frame("11111111-1111-4111-8111-111111111111", "first"),
        )
    )
    await asyncio.sleep(0)
    second = await coordinator.submit(
        SESSION,
        submit_frame(
            "33333333-3333-4333-8333-333333333333",
            "second",
            correlation_id="44444444-4444-4444-8444-444444444444",
        ),
    )
    assert second.turn_state == "failed"
    assert second.reason_code == "session_queue_full"
    release.set()
    await first_task
    await coordinator.drain(1.0)


async def test_global_lock_wait_is_five_seconds_and_does_not_dispatch() -> None:
    lock = asyncio.Lock()
    await lock.acquire()
    calls: list[str] = []

    async def dispatch_unlocked(text, observer):
        calls.append(text)
        return PrivateTurnResult(False, "complete", text, None, None)

    coordinator = make_coordinator(
        dispatch_turn_unlocked=dispatch_unlocked,
        turn_lock=lock,
        lock_wait_seconds=0.02,
    )
    result = await coordinator.submit(
        SESSION,
        submit_frame("11111111-1111-4111-8111-111111111111", "blocked"),
    )
    assert LOCK_WAIT_SECONDS == 5.0
    assert result.turn_state == "accepted"
    await wait_until(
        lambda: (
            coordinator.lookup(
                (EPOCH, CLIENT_ID, "11111111-1111-4111-8111-111111111111")
            )
            or result
        ).turn_state
        == "failed"
    )
    result = coordinator.lookup((EPOCH, CLIENT_ID, "11111111-1111-4111-8111-111111111111"))
    assert result is not None
    assert result.turn_state == "failed"
    assert result.reason_code == "core_busy"
    assert calls == []
    lock.release()
    await coordinator.drain(0.1)


async def test_terminal_observer_and_dispatch_return_emit_one_terminal_sequence() -> None:
    events: list[dict[str, Any]] = []

    async def dispatch_unlocked(text, observer):
        result = PrivateTurnResult(False, "complete", "reply", None, None)
        observer.on_terminal(result)
        return result

    def sink(session, event):
        events.append(dict(event))

    coordinator = make_coordinator(
        dispatch_turn_unlocked=dispatch_unlocked,
        event_sink=sink,
    )
    await coordinator.submit(
        SESSION,
        submit_frame("11111111-1111-4111-8111-111111111111", "hello"),
    )
    await coordinator.drain(1.0)

    states = [
        event["payload"]["state"]
        for event in events
        if event["type"] == "turn.state"
    ]
    assert states == ["accepted", "thinking", "generated", "complete"]
    assert [event["type"] for event in events].count("assistant.final") == 1
    terminal = coordinator.lookup(
        (EPOCH, CLIENT_ID, "11111111-1111-4111-8111-111111111111")
    )
    assert terminal is not None
    assert terminal.turn_state == "complete"
    assert terminal.final_text == "reply"


async def test_observer_terminal_result_cannot_be_replaced_by_dispatch_return() -> None:
    events: list[dict[str, Any]] = []

    async def dispatch_unlocked(text, observer):
        observer.on_terminal(
            PrivateTurnResult(False, "failed", "", "provider_failed", None)
        )
        return PrivateTurnResult(False, "complete", "late success", None, None)

    def sink(session, event):
        events.append(dict(event))

    coordinator = make_coordinator(
        dispatch_turn_unlocked=dispatch_unlocked,
        event_sink=sink,
    )
    turn_id = "11111111-1111-4111-8111-111111111111"
    await coordinator.submit(SESSION, submit_frame(turn_id, "hello"))
    await coordinator.drain(1.0)

    states = [
        event["payload"]["state"]
        for event in events
        if event["type"] == "turn.state"
    ]
    assert states == ["accepted", "thinking", "failed"]
    terminal = coordinator.lookup((EPOCH, CLIENT_ID, turn_id))
    assert terminal is not None
    assert terminal.turn_state == "failed"
    assert terminal.reason_code == "provider_failed"
    assert terminal.final_text == ""


async def test_dispatch_receives_unlocked_seam_without_nonreentrant_deadlock() -> None:
    lock = asyncio.Lock()
    entered = asyncio.Event()

    async def unlocked_dispatch(text, observer):
        entered.set()
        assert lock.locked(), "coordinator must own the shared lock at dispatch"
        return PrivateTurnResult(False, "complete", text, None, None)

    coordinator = make_coordinator(
        dispatch_turn_unlocked=unlocked_dispatch,
        turn_lock=lock,
        lock_wait_seconds=0.03,
    )
    result = await coordinator.submit(
        SESSION,
        submit_frame("11111111-1111-4111-8111-111111111111", "deadlock"),
    )
    assert entered.is_set()
    assert result.turn_state == "accepted"
    await coordinator.drain(1.0)
    terminal = coordinator.lookup(
        (EPOCH, CLIENT_ID, "11111111-1111-4111-8111-111111111111")
    )
    assert terminal is not None and terminal.turn_state == "complete"
    assert not lock.locked()


async def test_observation_deadline_quarantines_without_releasing_lock_or_dispatch_task() -> None:
    release = asyncio.Event()
    calls: list[str] = []

    async def dispatch_unlocked(text, observer):
        calls.append(text)
        await release.wait()
        return PrivateTurnResult(False, "complete", "late", None, None)

    coordinator = make_coordinator(
        dispatch_turn_unlocked=dispatch_unlocked,
        observation_deadline_seconds=0.02,
        wall_deadline_seconds=0.08,
    )
    accepted = await coordinator.submit(
        SESSION,
        submit_frame("11111111-1111-4111-8111-111111111111", "slow"),
    )
    assert accepted.turn_state == "accepted"
    await wait_until(lambda: coordinator.runtime_state(SESSION)["chat_available"] is False)
    state = coordinator.runtime_state(SESSION)
    assert state["reason_code"] == "web_turn_stuck"
    assert calls == ["slow"]
    release.set()
    await coordinator.drain(1.0)


async def test_wall_deadline_closes_session_with_4010_and_never_retries() -> None:
    release = asyncio.Event()

    @dataclass
    class ClosableSession:
        session_id: str = SESSION_ID
        client_instance_id: str = CLIENT_ID
        server_epoch: str = EPOCH
        close_calls: list[tuple[int, str]] | None = None

        async def close(self, code: int, reason: str | None = None):
            if self.close_calls is None:
                self.close_calls = []
            self.close_calls.append((code, reason or ""))

    session = ClosableSession(close_calls=[])

    async def dispatch_unlocked(text, observer):
        await release.wait()
        return PrivateTurnResult(False, "complete", text, None, None)

    coordinator = make_coordinator(
        dispatch_turn_unlocked=dispatch_unlocked,
        observation_deadline_seconds=0.01,
        wall_deadline_seconds=0.03,
    )
    await coordinator.submit(
        session,
        submit_frame("11111111-1111-4111-8111-111111111111", "stuck"),
    )
    await wait_until(lambda: bool(session.close_calls), timeout=1.0)
    assert session.close_calls == [(4010, "web_turn_timeout")]
    assert coordinator.runtime_state(session)["chat_available"] is False
    release.set()
    await coordinator.drain(1.0)


async def test_current_epoch_reconcile_returns_snapshot_without_dispatch() -> None:
    calls: list[str] = []
    release = asyncio.Event()

    async def dispatch_unlocked(text, observer):
        calls.append(text)
        await release.wait()
        return PrivateTurnResult(False, "complete", "reply", None, None)

    coordinator = make_coordinator(dispatch_turn_unlocked=dispatch_unlocked)
    turn_id = "11111111-1111-4111-8111-111111111111"
    await coordinator.submit(SESSION, submit_frame(turn_id, "hello"))
    result = await coordinator.reconcile(SESSION, reconcile_frame(turn_id=turn_id))
    assert result.key.turn_id == turn_id
    assert result.turn_state in {"accepted", "thinking"}
    assert calls == ["hello"]
    release.set()
    await coordinator.drain(1.0)


async def test_old_epoch_reconcile_is_unknown_and_never_dispatched() -> None:
    calls: list[str] = []

    async def dispatch_unlocked(text, observer):
        calls.append(text)
        return PrivateTurnResult(False, "complete", "reply", None, 7)

    coordinator = make_coordinator(dispatch_turn_unlocked=dispatch_unlocked)
    result = await coordinator.reconcile(
        SESSION,
        reconcile_frame(
            turn_server_epoch=OTHER_EPOCH,
            turn_id="11111111-1111-4111-8111-111111111111",
        ),
    )
    assert result.turn_state == "unknown"
    assert result.reason_code == "epoch_changed"
    assert calls == []


async def test_conflicting_reuse_is_rejected_without_second_dispatch() -> None:
    calls: list[str] = []

    async def dispatch_unlocked(text, observer):
        calls.append(text)
        return PrivateTurnResult(False, "complete", "reply", None, None)

    coordinator = make_coordinator(dispatch_turn_unlocked=dispatch_unlocked)
    turn_id = "11111111-1111-4111-8111-111111111111"
    await coordinator.submit(SESSION, submit_frame(turn_id, "hello"))
    conflict = await coordinator.submit(
        SESSION,
        submit_frame(
            turn_id,
            "different",
            correlation_id="44444444-4444-4444-8444-444444444444",
        ),
    )
    assert conflict.turn_state == "failed"
    assert conflict.reason_code == "turn_id_conflict"
    assert calls == ["hello"]


async def test_terminal_ledger_evicts_oldest_at_128_entries() -> None:
    async def dispatch_unlocked(text, observer):
        return PrivateTurnResult(False, "complete", text, None, None)

    coordinator = make_coordinator(dispatch_turn_unlocked=dispatch_unlocked)
    for index in range(TURN_LEDGER_LIMIT):
        turn_id = f"00000000-0000-4000-8000-{index + 1:012d}"
        correlation_id = f"00000000-0000-4000-8001-{index + 1:012d}"
        session = SessionContext(
            EPOCH,
            f"40000000-0000-4000-8000-{index + 1:012d}",
            f"50000000-0000-4000-8000-{index + 1:012d}",
        )
        frame = ClientFrame(
            message_type="chat.submit",
            server_epoch=EPOCH,
            session_id=session.session_id,
            turn_id=turn_id,
            correlation_id=correlation_id,
            revision=0,
            payload={"text": f"text-{index}"},
        )
        await coordinator.submit(
            session,
            frame,
        )
    await coordinator.drain(1.0)
    await coordinator.submit(
        SessionContext(
            EPOCH,
            "ffffffff-ffff-4fff-8fff-ffffffffffff",
            "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
        ),
        ClientFrame(
            message_type="chat.submit",
            server_epoch=EPOCH,
            session_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
            turn_id="ffffffff-ffff-4fff-8fff-ffffffffffff",
            correlation_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            revision=0,
            payload={"text": "last"},
        ),
    )
    assert coordinator.ledger_size() == TURN_LEDGER_LIMIT
    assert coordinator.lookup(
        (EPOCH, "50000000-0000-4000-8000-000000000001", "00000000-0000-4000-8000-000000000001")
    ) is None


async def test_terminal_eviction_preserves_newer_pending_slot_for_same_session() -> None:
    release = asyncio.Event()

    async def dispatch_unlocked(text, observer):
        if text == "first":
            return PrivateTurnResult(False, "complete", text, None, None)
        await release.wait()
        return PrivateTurnResult(False, "complete", text, None, None)

    coordinator = make_coordinator(
        dispatch_turn_unlocked=dispatch_unlocked,
        ledger_limit=2,
        lock_wait_seconds=1.0,
    )
    first_turn_id = "11111111-1111-4111-8111-111111111111"
    await coordinator.submit(SESSION, submit_frame(first_turn_id, "first"))
    await coordinator.drain(1.0)

    second_turn_id = "33333333-3333-4333-8333-333333333333"
    second = await coordinator.submit(
        SESSION,
        submit_frame(
            second_turn_id,
            "second",
            correlation_id="44444444-4444-4444-8444-444444444444",
        ),
    )
    assert second.turn_state == "accepted"

    other_session = SessionContext(
        EPOCH,
        "55555555-5555-4555-8555-555555555555",
        "66666666-6666-4666-8666-666666666666",
    )
    third = await coordinator.submit(
        other_session,
        submit_frame(
            "77777777-7777-4777-8777-777777777777",
            "third",
            correlation_id="88888888-8888-4888-8888-888888888888",
            session_id=other_session.session_id,
        ),
    )
    assert third.turn_state == "accepted"
    assert coordinator.lookup((EPOCH, CLIENT_ID, first_turn_id)) is None

    rejected = await coordinator.submit(
        SESSION,
        submit_frame(
            "99999999-9999-4999-8999-999999999999",
            "fourth",
            correlation_id="aaaaaaaa-0000-4000-8000-000000000001",
        ),
    )
    assert rejected.turn_state == "failed"
    assert rejected.reason_code == "session_queue_full"

    release.set()
    await coordinator.drain(1.0)


async def test_old_worker_cleanup_preserves_replacement_pending_slot() -> None:
    old_terminal = asyncio.Event()
    release_old = asyncio.Event()
    release_pending = asyncio.Event()

    async def dispatch_unlocked(text, observer):
        if text == "old":
            result = PrivateTurnResult(False, "complete", text, None, None)
            observer.on_terminal(result)
            old_terminal.set()
            await release_old.wait()
            return result
        await release_pending.wait()
        return PrivateTurnResult(False, "complete", text, None, None)

    coordinator = make_coordinator(
        dispatch_turn_unlocked=dispatch_unlocked,
        ledger_limit=2,
        lock_wait_seconds=0.01,
    )
    old_turn_id = "11111111-1111-4111-8111-111111111111"
    await coordinator.submit(SESSION, submit_frame(old_turn_id, "old"))
    await wait_until(old_terminal.is_set)
    old_record = coordinator._records[TurnKey(EPOCH, CLIENT_ID, old_turn_id)]
    assert old_record.task is not None
    old_task = old_record.task

    filler_session = SessionContext(
        EPOCH,
        "10000000-0000-4000-8000-000000000001",
        "20000000-0000-4000-8000-000000000001",
    )
    filler_turn_id = "30000000-0000-4000-8000-000000000001"
    await coordinator.submit(
        filler_session,
        submit_frame(
            filler_turn_id,
            "filler",
            correlation_id="40000000-0000-4000-8000-000000000001",
            session_id=filler_session.session_id,
        ),
    )
    await wait_until(
        lambda: (
            coordinator.lookup((EPOCH, filler_session.client_instance_id, filler_turn_id))
            is not None
            and coordinator.lookup(
                (EPOCH, filler_session.client_instance_id, filler_turn_id)
            ).turn_state
            == "failed"
        )
    )

    coordinator.lock_wait_seconds = 1.0
    trigger_session = SessionContext(
        EPOCH,
        "50000000-0000-4000-8000-000000000001",
        "60000000-0000-4000-8000-000000000001",
    )
    trigger = await coordinator.submit(
        trigger_session,
        submit_frame(
            "70000000-0000-4000-8000-000000000001",
            "trigger",
            correlation_id="80000000-0000-4000-8000-000000000001",
            session_id=trigger_session.session_id,
        ),
    )
    assert trigger.turn_state == "accepted"

    replacement = await coordinator.submit(
        SESSION,
        submit_frame(
            "90000000-0000-4000-8000-000000000001",
            "replacement",
            correlation_id="a0000000-0000-4000-8000-000000000001",
        ),
    )
    assert replacement.turn_state == "accepted"

    release_old.set()
    await asyncio.wait_for(old_task, timeout=1.0)
    rejected = await coordinator.submit(
        SESSION,
        submit_frame(
            "b0000000-0000-4000-8000-000000000001",
            "too-many",
            correlation_id="c0000000-0000-4000-8000-000000000001",
        ),
    )
    assert rejected.turn_state == "failed"
    assert rejected.reason_code == "session_queue_full"

    release_pending.set()
    await coordinator.drain(1.0)


async def test_terminal_entry_expires_at_thirty_minute_ttl() -> None:
    now = [0.0]

    async def dispatch_unlocked(text, observer):
        return PrivateTurnResult(False, "complete", text, None, None)

    coordinator = make_coordinator(
        dispatch_turn_unlocked=dispatch_unlocked,
        clock=lambda: now[0],
    )
    turn_id = "11111111-1111-4111-8111-111111111111"
    await coordinator.submit(SESSION, submit_frame(turn_id, "terminal"))
    await coordinator.drain(1.0)

    now[0] = 1_799.999
    assert coordinator.ledger_size() == 1
    now[0] = 1_800.0
    assert coordinator.ledger_size() == 0
    assert coordinator.lookup((EPOCH, CLIENT_ID, turn_id)) is None


async def test_terminal_unresolved_ttl_expiry_releases_owned_pending_slot() -> None:
    now = [0.0]
    old_terminal = asyncio.Event()
    release_old = asyncio.Event()

    async def dispatch_unlocked(text, observer):
        if text == "old":
            result = PrivateTurnResult(False, "complete", text, None, None)
            observer.on_terminal(result)
            old_terminal.set()
            await release_old.wait()
            return result
        return PrivateTurnResult(False, "complete", text, None, None)

    coordinator = make_coordinator(
        dispatch_turn_unlocked=dispatch_unlocked,
        clock=lambda: now[0],
    )
    await coordinator.submit(
        SESSION,
        submit_frame("11111111-1111-4111-8111-111111111111", "old"),
    )
    await wait_until(old_terminal.is_set)

    now[0] = 1_800.0
    assert coordinator.ledger_size() == 0
    replacement = await coordinator.submit(
        SESSION,
        submit_frame(
            "33333333-3333-4333-8333-333333333333",
            "replacement",
            correlation_id="44444444-4444-4444-8444-444444444444",
        ),
    )
    assert replacement.turn_state == "accepted"

    release_old.set()
    await coordinator.drain(1.0)


async def test_old_worker_cleanup_preserves_same_key_replacement_slot() -> None:
    now = [0.0]
    old_terminal = asyncio.Event()
    replacement_started = asyncio.Event()
    release_old = asyncio.Event()
    release_replacement = asyncio.Event()

    async def dispatch_unlocked(text, observer):
        if text == "old":
            result = PrivateTurnResult(False, "complete", text, None, None)
            observer.on_terminal(result)
            old_terminal.set()
            await release_old.wait()
            return result
        replacement_started.set()
        await release_replacement.wait()
        return PrivateTurnResult(False, "complete", text, None, None)

    coordinator = make_coordinator(
        dispatch_turn_unlocked=dispatch_unlocked,
        clock=lambda: now[0],
    )
    turn_id = "11111111-1111-4111-8111-111111111111"
    await coordinator.submit(SESSION, submit_frame(turn_id, "old"))
    await wait_until(old_terminal.is_set)
    old_record = coordinator._records[TurnKey(EPOCH, CLIENT_ID, turn_id)]
    assert old_record.task is not None
    old_task = old_record.task

    now[0] = 1_800.0
    assert coordinator.ledger_size() == 0
    replacement = await coordinator.submit(
        SESSION,
        submit_frame(
            turn_id,
            "replacement",
            correlation_id="44444444-4444-4444-8444-444444444444",
        ),
    )
    assert replacement.turn_state == "accepted"

    release_old.set()
    await asyncio.wait_for(old_task, timeout=1.0)
    await wait_until(replacement_started.is_set)
    rejected = await coordinator.submit(
        SESSION,
        submit_frame(
            "33333333-3333-4333-8333-333333333333",
            "too-many",
            correlation_id="55555555-5555-4555-8555-555555555555",
        ),
    )
    assert rejected.turn_state == "failed"
    assert rejected.reason_code == "session_queue_full"

    release_replacement.set()
    await coordinator.drain(1.0)


async def test_nonterminal_entry_is_retained_past_thirty_minutes() -> None:
    now = [0.0]
    release = asyncio.Event()

    async def dispatch_unlocked(text, observer):
        await release.wait()
        return PrivateTurnResult(False, "complete", text, None, None)

    coordinator = make_coordinator(
        dispatch_turn_unlocked=dispatch_unlocked,
        clock=lambda: now[0],
    )
    turn_id = "11111111-1111-4111-8111-111111111111"
    await coordinator.submit(SESSION, submit_frame(turn_id, "pending"))

    now[0] = 1_800.0
    assert coordinator.ledger_size() == 1
    pending = coordinator.lookup((EPOCH, CLIENT_ID, turn_id))
    assert pending is not None
    assert pending.turn_state not in {"complete", "failed", "unknown"}

    release.set()
    await coordinator.drain(1.0)


async def test_all_nonterminal_ledger_entries_fail_closed_as_ledger_full() -> None:
    release = asyncio.Event()

    async def dispatch_unlocked(text, observer):
        await release.wait()
        return PrivateTurnResult(False, "complete", text, None, None)

    coordinator = make_coordinator(dispatch_turn_unlocked=dispatch_unlocked)
    # Distinct sessions supply one pending slot each while sharing one epoch ledger.
    for index in range(TURN_LEDGER_LIMIT):
        session = SessionContext(
            EPOCH,
            f"00000000-0000-4000-8000-{index + 1:012d}",
            f"10000000-0000-4000-8000-{index + 1:012d}",
        )
        turn_id = f"20000000-0000-4000-8000-{index + 1:012d}"
        frame = ClientFrame(
            message_type="chat.submit",
            server_epoch=EPOCH,
            session_id=session.session_id,
            turn_id=turn_id,
            correlation_id=f"30000000-0000-4000-8000-{index + 1:012d}",
            revision=0,
            payload={"text": f"pending-{index}"},
        )
        await coordinator.submit(session, frame)
    extra = SessionContext(
        EPOCH,
        "ffffffff-ffff-4fff-8fff-ffffffffffff",
        "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    )
    extra_frame = ClientFrame(
        message_type="chat.submit",
        server_epoch=EPOCH,
        session_id=extra.session_id,
        turn_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        correlation_id="cccccccc-cccc-4ccc-8ccc-cccccccccccd",
        revision=0,
        payload={"text": "extra"},
    )
    result = await coordinator.submit(extra, extra_frame)
    assert result.turn_state == "failed"
    assert result.reason_code == "ledger_full"
    release.set()
    await coordinator.drain(1.0)


async def test_begin_shutdown_closes_admission_and_drain_is_bounded() -> None:
    release = asyncio.Event()

    async def dispatch_unlocked(text, observer):
        await release.wait()
        return PrivateTurnResult(False, "complete", text, None, None)

    coordinator = make_coordinator(dispatch_turn_unlocked=dispatch_unlocked)
    await coordinator.submit(
        SESSION,
        submit_frame("11111111-1111-4111-8111-111111111111", "pending"),
    )
    coordinator.begin_shutdown()
    rejected = await coordinator.submit(
        SESSION,
        submit_frame("33333333-3333-4333-8333-333333333333", "late"),
    )
    assert rejected.turn_state == "failed"
    assert rejected.reason_code == "shutting_down"
    await coordinator.drain(0.01)
    assert coordinator.runtime_state(SESSION)["chat_available"] is False
    release.set()
    await coordinator.drain(1.0)


async def test_runtime_state_is_exact_redacted_allowlist() -> None:
    async def dispatch_unlocked(text, observer):
        return PrivateTurnResult(False, "complete", "reply", None, None)

    coordinator = make_coordinator(dispatch_turn_unlocked=dispatch_unlocked)
    state = coordinator.runtime_state(SESSION)
    assert set(state) == {
        "protocol",
        "server_epoch",
        "session_id",
        "revision",
        "core_status",
        "chat_available",
        "reason_code",
        "active_turn_state",
        "voice_state",
        "queue_depth",
        "capabilities",
    }
    assert "provider" not in repr(state).lower()
    assert "memory" not in repr(state).lower()


async def test_late_correlated_voice_survives_text_completion():
    from dataclasses import replace
    from nana.runtime.private_voice_receipts import PrivateVoiceContext, PrivateVoiceReceiptLedger
    events = []
    async def dispatch(text, observer):
        return PrivateTurnResult(False, 'complete', 'fixture final', None, None)
    core = PrivateTurnCoordinator(asyncio.Lock(), dispatch, EPOCH, lambda _s, e: events.append(e))
    frame = submit_frame('11111111-1111-4111-8111-111111111111', 'fixture')
    await core.submit(SESSION, frame)
    await core.drain(.1)
    voice = PrivateVoiceReceiptLedger(event_sink=core.on_private_voice_receipt)
    receipt = voice.queued(PrivateVoiceContext(EPOCH, SESSION_ID, frame.turn_id, frame.correlation_id), engine_ticket=4)
    voice.first_audio(receipt, engine_ticket=4)
    voice.complete(receipt, engine_ticket=4, completed=True)
    assert [e['payload']['state'] for e in events if e['type'] == 'voice.state'] == ['queued', 'speaking', 'delivered']
    assert core.lookup(TurnKey(EPOCH, CLIENT_ID, frame.turn_id)).turn_state == 'complete'
    before = len(events)
    core.on_private_voice_receipt(replace(receipt, context=replace(receipt.context, correlation_id=CLIENT_ID)))
    assert len(events) == before


async def test_reconnect_snapshot_owns_revisions_and_receives_late_voice():
    from nana.runtime.private_voice_receipts import PrivateVoiceContext, PrivateVoiceReceiptLedger
    calls, events = [], []
    async def dispatch(text, observer):
        calls.append(text)
        return PrivateTurnResult(False, 'complete', 'fixture final', None, None)
    core = PrivateTurnCoordinator(asyncio.Lock(), dispatch, EPOCH, lambda s, e: events.append((s, e)))
    frame = submit_frame('11111111-1111-4111-8111-111111111111', 'fixture')
    await core.submit(SESSION, frame)
    await core.drain(.1)
    second = SessionContext(EPOCH, 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee', CLIENT_ID)
    result = await core.reconcile(second, reconcile_frame(turn_id=frame.turn_id))
    core.publish_snapshot(second, result)
    assert events[-1][1]['type'] == 'turn.snapshot'
    assert events[-1][1]['session_id'] == second.session_id
    assert events[-1][1]['payload']['text'] == 'fixture final'
    rev = events[-1][1]['revision']
    voice = PrivateVoiceReceiptLedger(event_sink=core.on_private_voice_receipt)
    receipt = voice.queued(PrivateVoiceContext(EPOCH, SESSION_ID, frame.turn_id, frame.correlation_id), engine_ticket=9)
    voice.first_audio(receipt, engine_ticket=9)
    voice.complete(receipt, engine_ticket=9, completed=True)
    assert events[-1][0] is second
    assert events[-1][1]['session_id'] == second.session_id
    assert events[-1][1]['revision'] == rev + 3
    assert calls == ['fixture']


async def run() -> None:
    tests = [
        test_reconnect_snapshot_owns_revisions_and_receives_late_voice,
        test_late_correlated_voice_survives_text_completion,
        test_duplicate_submit_returns_snapshot_without_dispatch,
        test_acceptance_event_precedes_thinking_and_revision_starts_at_one,
        test_one_pending_session_slot_rejects_second_turn,
        test_global_lock_wait_is_five_seconds_and_does_not_dispatch,
        test_terminal_observer_and_dispatch_return_emit_one_terminal_sequence,
        test_observer_terminal_result_cannot_be_replaced_by_dispatch_return,
        test_dispatch_receives_unlocked_seam_without_nonreentrant_deadlock,
        test_observation_deadline_quarantines_without_releasing_lock_or_dispatch_task,
        test_wall_deadline_closes_session_with_4010_and_never_retries,
        test_current_epoch_reconcile_returns_snapshot_without_dispatch,
        test_old_epoch_reconcile_is_unknown_and_never_dispatched,
        test_conflicting_reuse_is_rejected_without_second_dispatch,
        test_terminal_ledger_evicts_oldest_at_128_entries,
        test_terminal_eviction_preserves_newer_pending_slot_for_same_session,
        test_old_worker_cleanup_preserves_replacement_pending_slot,
        test_terminal_entry_expires_at_thirty_minute_ttl,
        test_terminal_unresolved_ttl_expiry_releases_owned_pending_slot,
        test_old_worker_cleanup_preserves_same_key_replacement_slot,
        test_nonterminal_entry_is_retained_past_thirty_minutes,
        test_all_nonterminal_ledger_entries_fail_closed_as_ledger_full,
        test_begin_shutdown_closes_admission_and_drain_is_bounded,
        test_runtime_state_is_exact_redacted_allowlist,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        await test()
    print(f"smoke_private_turn_coordinator: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(run())
