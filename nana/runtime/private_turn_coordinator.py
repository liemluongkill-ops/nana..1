"""Bounded admission and idempotent coordination for private web turns.

This module is deliberately provider- and transport-neutral.  It owns the
web-turn ledger and invokes only the already-unlocked dispatcher after it has
acquired the shared Core turn lock.  The HTTP/WebSocket server can inject an
event sink and a session close seam without making this module aware of
aiohttp or any model/voice implementation.
"""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import hashlib
import inspect
import time
from typing import Any, Literal

from .private_web_chat_protocol import (
    PROTOCOL_NAME,
    SessionContext,
    build_runtime_state,
    build_server_event,
)


GLOBAL_ACTIVE_TURNS = 1
PENDING_SESSION_SLOTS = 1
LOCK_WAIT_SECONDS = 5.0
OBSERVATION_DEADLINE_SECONDS = 90.0
WALL_DEADLINE_SECONDS = 120.0
TURN_LEDGER_LIMIT = 128
TURN_LEDGER_TTL_SECONDS = 30.0 * 60.0

_TERMINAL_STATES = frozenset({"complete", "failed", "unknown"})
_TURN_STATES = frozenset(
    {"accepted", "thinking", "generated", "complete", "failed", "unknown"}
)
_VOICE_STATES = frozenset(
    {"idle", "queued", "speaking", "delivered", "failed", "unknown"}
)


@dataclass(frozen=True)
class TurnKey:
    """Stable idempotency identity for one browser turn."""

    server_epoch: str
    client_instance_id: str
    turn_id: str

    @property
    def epoch(self) -> str:
        return self.server_epoch


@dataclass(frozen=True)
class TurnSnapshot:
    """Immutable, redacted state returned by coordinator operations."""

    key: TurnKey
    session_id: str
    correlation_id: str
    normalized_text_sha256: str
    revision: int
    turn_state: str
    voice_state: str
    final_text: str
    reason_code: str | None

    @property
    def turn_id(self) -> str:
        return self.key.turn_id

    @property
    def server_epoch(self) -> str:
        return self.key.server_epoch

    @property
    def client_instance_id(self) -> str:
        return self.key.client_instance_id


@dataclass(frozen=True)
class PrivateTurnIdentity:
    """Identity passed to a future typed private-turn observer."""

    server_epoch: str
    session_id: str
    client_instance_id: str
    turn_id: str
    correlation_id: str


@dataclass(frozen=True)
class PrivateTurnResult:
    """Provider-neutral result expected from the canonical dispatcher."""

    should_exit: bool
    status: str
    final_text: str
    reason_code: str | None
    voice_ticket: int | None


class TurnCoordinatorError(ValueError):
    """Bounded coordinator input error with a safe reason code."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass
class _TurnRecord:
    key: TurnKey
    session: object
    session_id: str
    correlation_id: str
    source_text: str
    created_at: float
    snapshot: TurnSnapshot
    task: asyncio.Task[None] | None = None
    deadline_task: asyncio.Task[None] | None = None
    outcome_locked: bool = False
    final_emitted: bool = False
    generated_emitted: bool = False
    terminal_emitted: bool = False
    voice_ticket: int | None = None
    receipt_id: str | None = None
    receipt_revision: int = 0
    # Provisional value for the event builder. The server assigns the actual
    # session-wide wire sequence when enqueuing turn, snapshot and error events.
    event_sequence: int = 0
    started: asyncio.Event = None  # type: ignore[assignment]
    admission_ready: asyncio.Event = None  # type: ignore[assignment]

    def __repr__(self) -> str:  # Keep source text out of diagnostics/repr.
        return (
            f"_TurnRecord(key={self.key!r}, session_id={self.session_id!r}, "
            f"correlation_id={self.correlation_id!r}, state={self.snapshot.turn_state!r})"
        )


def _is_awaitable(value: object) -> bool:
    return inspect.isawaitable(value)


def _normalise_text(value: object) -> str:
    if not isinstance(value, str):
        raise TurnCoordinatorError("invalid_submit_payload")
    if not value.strip():
        raise TurnCoordinatorError("invalid_submit_payload")
    return value.strip().casefold()


def _text_digest(value: str) -> str:
    return hashlib.sha256(_normalise_text(value).encode("utf-8")).hexdigest()


def _safe_string(value: object, fallback: str = "") -> str:
    return value if isinstance(value, str) else fallback


class _TurnObserver:
    """Small synchronous observer seam passed into the canonical dispatcher."""

    def __init__(self, coordinator: "PrivateTurnCoordinator", record: _TurnRecord) -> None:
        self._coordinator = coordinator
        self._record = record
        self.identity = PrivateTurnIdentity(
            server_epoch=record.key.server_epoch,
            session_id=record.session_id,
            client_instance_id=record.key.client_instance_id,
            turn_id=record.key.turn_id,
            correlation_id=record.correlation_id,
        )

    def on_accepted(self, *_args: object, **_kwargs: object) -> None:
        return None

    @property
    def voice_ticket(self):
        return self._record.voice_ticket

    def on_thinking(self, *_args: object, **_kwargs: object) -> None:
        self._coordinator._on_observer_thinking(self._record)

    def on_text_delta(self, text: object = "", *args: object, **kwargs: object) -> None:
        revision = kwargs.get("revision")
        if revision is None and args and isinstance(args[0], int):
            revision = args[0]
        self._coordinator._on_observer_delta(self._record, _safe_string(text), revision)

    def on_text_final(self, text: object = "", *args: object, **kwargs: object) -> None:
        revision = kwargs.get("revision")
        if revision is None and args and isinstance(args[0], int):
            revision = args[0]
        self._coordinator._on_observer_final(self._record, _safe_string(text), revision)

    def on_voice_queued(self, ticket: object = None, *_args: object, **_kwargs: object) -> None:
        self._coordinator._on_observer_voice(self._record, "queued", voice_ticket=ticket)

    def on_voice_first_audio(self, ticket: object = None, *_args: object, **_kwargs: object) -> None:
        self._coordinator._on_observer_voice(self._record, "speaking", voice_ticket=ticket)

    def on_voice_complete(self, ticket: object = None, *_args: object, **_kwargs: object) -> None:
        self._coordinator._on_observer_voice(self._record, "delivered", voice_ticket=ticket)

    # Common aliases used by callback-oriented dispatch implementations.
    on_first_audio = on_voice_first_audio
    on_voice_first = on_voice_first_audio
    on_complete = on_voice_complete

    def on_voice_failed(self, reason_code: object = "voice_failed", *_args: object, **_kwargs: object) -> None:
        self._coordinator._on_observer_voice(
            self._record,
            "failed",
            _safe_string(reason_code, "voice_failed"),
        )

    def on_voice_unknown(self, reason_code: object = "unknown_outcome", *_args: object, **_kwargs: object) -> None:
        self._coordinator._on_observer_voice(
            self._record,
            "unknown",
            _safe_string(reason_code, "unknown_outcome"),
        )

    def on_terminal(self, result: object = None, *_args: object, **_kwargs: object) -> None:
        # The coordinator also consumes the dispatch return value.  This hook
        # exists for dispatchers that report terminality through the observer.
        if result is not None:
            self._coordinator._finish_from_result(self._record, result)


class PrivateTurnCoordinator:
    """Coordinate one bounded private web turn at a time.

    ``dispatch_turn_unlocked`` must be the dispatcher variant that does not
    acquire ``turn_lock`` itself.  The coordinator owns exactly one bounded
    acquisition around that call.
    """

    def __init__(
        self,
        turn_lock: asyncio.Lock | None = None,
        dispatch_turn_unlocked: Callable[[str, object], object] | None = None,
        server_epoch: str | None = None,
        event_sink: Callable[..., object] | None = None,
        *,
        lock: asyncio.Lock | None = None,
        global_lock: asyncio.Lock | None = None,
        sink: Callable[..., object] | None = None,
        clock: Callable[[], float] | None = None,
        lock_wait_seconds: float = LOCK_WAIT_SECONDS,
        observation_deadline_seconds: float = OBSERVATION_DEADLINE_SECONDS,
        wall_deadline_seconds: float = WALL_DEADLINE_SECONDS,
        ledger_limit: int = TURN_LEDGER_LIMIT,
        ledger_ttl_seconds: float = TURN_LEDGER_TTL_SECONDS,
        close_session: Callable[..., object] | None = None,
        dispatch_scope: Callable[..., object] | None = None,
        visual_event_sink: Callable[..., object] | None = None,
    ) -> None:
        selected_lock = turn_lock or lock or global_lock
        if selected_lock is None:
            selected_lock = asyncio.Lock()
        if not callable(dispatch_turn_unlocked):
            raise TypeError("dispatch_turn_unlocked is required")
        if not isinstance(server_epoch, str) or not server_epoch.strip():
            raise ValueError("server_epoch is required")
        if type(ledger_limit) is not int or ledger_limit < 1:
            raise ValueError("ledger_limit must be positive")
        if lock_wait_seconds < 0 or observation_deadline_seconds <= 0 or wall_deadline_seconds <= 0:
            raise ValueError("coordinator deadlines must be positive")
        if wall_deadline_seconds < observation_deadline_seconds:
            raise ValueError("wall deadline must not precede observation deadline")

        self.turn_lock = selected_lock
        self.dispatch_turn_unlocked = dispatch_turn_unlocked
        self.server_epoch = server_epoch
        self.event_sink = event_sink or sink
        self._clock = clock or time.monotonic
        self.lock_wait_seconds = float(lock_wait_seconds)
        self.observation_deadline_seconds = float(observation_deadline_seconds)
        self.wall_deadline_seconds = float(wall_deadline_seconds)
        self.ledger_limit = ledger_limit
        self.ledger_ttl_seconds = float(ledger_ttl_seconds)
        self._close_session_callback = close_session
        self._dispatch_scope = dispatch_scope
        self._visual_event_sink = visual_event_sink

        self._records: OrderedDict[TurnKey, _TurnRecord] = OrderedDict()
        self._session_pending: dict[tuple[str, str], _TurnRecord] = {}
        self._active_tasks: set[asyncio.Task[None]] = set()
        self._sink_tasks: set[asyncio.Task[None]] = set()
        self._ledger_lock = asyncio.Lock()
        self._admission_open = True
        self._quarantined = False
        self._shutting_down = False

    # ---- public admission/reconciliation API ---------------------------------

    async def submit(self, session: object, frame: object) -> TurnSnapshot:
        """Admit one submit, returning the latest immutable snapshot.

        The turn worker is scheduled before this coroutine yields.  Fast fake
        dispatchers therefore return a terminal snapshot, while a real
        provider/lock wait returns the committed ``accepted`` snapshot and
        continues in the background.
        """

        session_id, client_instance_id, session_epoch = self._session_values(session)
        turn_id = self._frame_value(frame, "turn_id")
        correlation_id = self._frame_value(frame, "correlation_id")
        text = self._payload_value(frame, "text")
        try:
            turn_id = self._required_string(turn_id, "invalid_uuid")
            correlation_id = self._required_string(correlation_id, "invalid_uuid")
            text = self._required_string(text, "invalid_submit_payload")
            digest = _text_digest(text)
        except TurnCoordinatorError as exc:
            return self._ephemeral_snapshot(
                session_id,
                client_instance_id,
                turn_id,
                correlation_id,
                "failed",
                exc.reason_code,
                text if isinstance(text, str) else "",
            )

        frame_epoch = self._frame_value(frame, "server_epoch", self.server_epoch)
        if frame_epoch != self.server_epoch or session_epoch not in {self.server_epoch, ""}:
            return self._ephemeral_snapshot(
                session_id,
                client_instance_id,
                turn_id,
                correlation_id,
                "unknown",
                "epoch_changed",
                text,
                epoch=_safe_string(frame_epoch, self.server_epoch),
            )

        key = TurnKey(self.server_epoch, client_instance_id, turn_id)
        session_slot = (self.server_epoch, session_id)
        async with self._ledger_lock:
            self._prune_locked()
            existing = self._records.get(key)
            if existing is not None:
                if (
                    existing.correlation_id == correlation_id
                    and existing.snapshot.normalized_text_sha256 == digest
                ):
                    return existing.snapshot
                return self._ephemeral_snapshot(
                    session_id,
                    client_instance_id,
                    turn_id,
                    correlation_id,
                    "failed",
                    "turn_id_conflict",
                    text,
                )

            if not self.admission_open:
                reason = "shutting_down" if self._shutting_down else "web_turn_stuck"
                return self._ephemeral_snapshot(
                    session_id,
                    client_instance_id,
                    turn_id,
                    correlation_id,
                    "failed",
                    reason,
                    text,
                )

            pending_record = self._session_pending.get(session_slot)
            if pending_record is not None:
                return self._ephemeral_snapshot(
                    session_id,
                    client_instance_id,
                    turn_id,
                    correlation_id,
                    "failed",
                    "session_queue_full",
                    text,
                )

            if len(self._records) >= self.ledger_limit:
                self._evict_one_terminal_locked()
            if len(self._records) >= self.ledger_limit:
                return self._ephemeral_snapshot(
                    session_id,
                    client_instance_id,
                    turn_id,
                    correlation_id,
                    "failed",
                    "ledger_full",
                    text,
                )

            accepted = TurnSnapshot(
                key=key,
                session_id=session_id,
                correlation_id=correlation_id,
                normalized_text_sha256=digest,
                revision=1,
                turn_state="accepted",
                voice_state="idle",
                final_text="",
                reason_code=None,
            )
            record = _TurnRecord(
                key=key,
                session=session,
                session_id=session_id,
                correlation_id=correlation_id,
                source_text=text,
                created_at=self._now(),
                snapshot=accepted,
                event_sequence=0,
                started=asyncio.Event(),
                admission_ready=asyncio.Event(),
            )
            self._records[key] = record
            self._session_pending[session_slot] = record

        self._emit_initial_accepted(record)
        record.task = asyncio.create_task(self._run_turn(record), name="nana-private-turn")
        self._active_tasks.add(record.task)
        await record.started.wait()
        await record.admission_ready.wait()
        await asyncio.sleep(0)
        # ``submit`` is an admission operation: always return the committed
        # revision-1 snapshot.  Thinking/terminal transitions are delivered
        # through the event sink and remain available from ``reconcile`` or
        # ``lookup``.  This keeps a WebSocket reader from waiting on Core
        # generation or the global lock.
        return accepted

    async def reconcile(self, session: object, frame: object) -> TurnSnapshot:
        """Return a current ledger snapshot without ever dispatching text."""

        session_id, client_instance_id, _session_epoch = self._session_values(session)
        turn_id = self._required_string(
            self._frame_value(frame, "turn_id"), "invalid_uuid"
        )
        correlation_id = self._required_string(
            self._frame_value(frame, "correlation_id"), "invalid_uuid"
        )
        payload = self._frame_payload(frame)
        turn_epoch = payload.get(
            "turn_server_epoch",
            self._frame_value(frame, "turn_server_epoch", self._frame_value(frame, "server_epoch", self.server_epoch)),
        )
        payload_client = payload.get("client_instance_id", client_instance_id)
        if payload_client != client_instance_id:
            return self._ephemeral_snapshot(
                session_id,
                client_instance_id,
                turn_id,
                correlation_id,
                "unknown",
                "client_instance_mismatch",
                "",
                epoch=_safe_string(turn_epoch, self.server_epoch),
            )
        if turn_epoch != self.server_epoch:
            return self._ephemeral_snapshot(
                session_id,
                client_instance_id,
                turn_id,
                correlation_id,
                "unknown",
                "epoch_changed",
                "",
                epoch=_safe_string(turn_epoch, self.server_epoch),
            )

        key = TurnKey(self.server_epoch, client_instance_id, turn_id)
        async with self._ledger_lock:
            self._prune_locked()
            record = self._records.get(key)
            if record is None:
                return self._ephemeral_snapshot(
                    session_id,
                    client_instance_id,
                    turn_id,
                    correlation_id,
                    "unknown",
                    "turn_not_found",
                    "",
                )
            if record.correlation_id != correlation_id:
                return self._ephemeral_snapshot(
                    session_id,
                    client_instance_id,
                    turn_id,
                    correlation_id,
                    "failed",
                    "turn_id_conflict",
                    "",
                )
            return record.snapshot

    def publish_snapshot(self, session: object, snapshot: TurnSnapshot) -> None:
        """Advance turn revision; transport owns wire order. Never redispatch."""
        sid, cid, _ = self._session_values(session)
        record = self._records.get(snapshot.key)
        payload = {
            'turn_server_epoch': snapshot.key.server_epoch,
            'turn_state': snapshot.turn_state,
            'voice_state': snapshot.voice_state,
            'text': snapshot.final_text or (getattr(record, '_partial_text', '') if record else ''),
            'delta_index': getattr(record, '_delta_index', 0) if record else 0,
            'reason_code': snapshot.reason_code,
        }
        if record is not None and record.snapshot is snapshot and record.key.client_instance_id == cid:
            record.session = session
            self._emit_event(record, 'turn.snapshot', payload)
        else:
            self._send_event(session, build_server_event(
                context=SessionContext(self.server_epoch, sid, cid),
                turn_id=snapshot.key.turn_id, correlation_id=snapshot.correlation_id,
                revision=1, event_sequence=1, message_type='turn.snapshot', payload=payload))

    def runtime_state(self, session: object) -> Mapping[str, object]:
        """Return only the protocol's exact redacted runtime-state allowlist."""

        session_id, client_instance_id, _session_epoch = self._session_values(session)
        record = self._session_pending.get((self.server_epoch, session_id))
        if record is None:
            record = next((r for r in reversed(self._records.values()) if r.key.client_instance_id == client_instance_id), None)
        if self._shutting_down:
            core_status = "shutting_down"
            chat_available = False
            reason_code = "shutting_down"
        elif self._quarantined:
            core_status = "degraded"
            chat_available = False
            reason_code = "web_turn_stuck"
        elif record is not None and record.snapshot.turn_state not in _TERMINAL_STATES:
            core_status = "busy"
            chat_available = True
            reason_code = None
        else:
            core_status = "ready"
            chat_available = self._admission_open
            reason_code = None if chat_available else "shutting_down"
        active_state = (
            record.snapshot.turn_state
            if record is not None and record.snapshot.turn_state in _TURN_STATES
            else None
        )
        voice_state = record.snapshot.voice_state if record is not None else "idle"
        queue_depth = 1 if record is not None and record.snapshot.turn_state not in _TERMINAL_STATES else 0
        context = SessionContext(self.server_epoch, session_id, client_instance_id)
        revision = max(1, record.snapshot.revision if record is not None else 1)
        try:
            return build_runtime_state(
                context=context,
                revision=revision,
                core_status=core_status,
                chat_available=chat_available,
                reason_code=reason_code,
                active_turn_state=active_state,
                voice_state=voice_state,
                queue_depth=queue_depth,
            )
        except Exception:
            # A malformed/injected session cannot expand the redacted shape.
            return {
                "protocol": PROTOCOL_NAME,
                "server_epoch": self.server_epoch,
                "session_id": session_id,
                "revision": revision,
                "core_status": core_status,
                "chat_available": chat_available,
                "reason_code": reason_code,
                "active_turn_state": active_state,
                "voice_state": voice_state,
                "queue_depth": queue_depth,
                "capabilities": {
                    "chat_submit": True,
                    "reconcile": True,
                    "runtime_state": True,
                    "microphone": False,
                    "settings": False,
                    "model_select": False,
                    "cancel": False,
                },
            }

    def begin_shutdown(self) -> None:
        """Close web admission without cancelling unresolved Core work."""

        self._admission_open = False
        self._shutting_down = True
        for record in tuple(self._records.values()):
            if record.snapshot.turn_state == 'accepted':
                self._finish_failed(record, 'shutting_down')

    async def drain(self, timeout: float) -> None:
        """Wait for coordinator workers up to ``timeout`` without force release."""

        bounded = max(0.0, float(timeout))
        deadline = asyncio.get_running_loop().time() + bounded
        while True:
            tasks = {task for task in self._active_tasks if not task.done()}
            self._active_tasks.intersection_update(tasks)
            if not tasks:
                break
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                for record in tuple(self._records.values()):
                    if record.snapshot.turn_state not in _TERMINAL_STATES:
                        record.outcome_locked = True
                        self._mark_unknown(record, "unknown_outcome")
                break
            done, _pending = await asyncio.wait(tasks, timeout=remaining)
            if not done and _pending:
                for record in tuple(self._records.values()):
                    if record.snapshot.turn_state not in _TERMINAL_STATES:
                        record.outcome_locked = True
                        self._mark_unknown(record, "unknown_outcome")
                break
        sink_tasks = {task for task in self._sink_tasks if not task.done()}
        if sink_tasks:
            remaining = max(0.0, deadline - asyncio.get_running_loop().time())
            if remaining:
                await asyncio.wait(sink_tasks, timeout=remaining)

    def clear(self):
        self._admission_open = False
        self._records.clear()
        self._session_pending.clear()
        self.event_sink = None

    def lookup(self, key: TurnKey | tuple[str, str, str]) -> TurnSnapshot | None:
        if not isinstance(key, TurnKey):
            try:
                key = TurnKey(*key)
            except (TypeError, ValueError):
                return None
        record = self._records.get(key)
        return record.snapshot if record is not None else None

    def publish_avatar_signal(self, context, sequence, payload) -> bool:
        """Route private motion to the owning authenticated session only.

        Visual sequence is independent from chat revision and receipt state.
        Reconciliation may rebind the record to a replacement session for the
        same browser; playback identity remains the original receipt identity.
        """
        record = next((row for row in self._records.values()
                       if row.key.server_epoch == context.server_epoch
                       and row.key.turn_id == context.turn_id
                       and row.session_id == context.session_id
                       and row.correlation_id == context.correlation_id), None)
        if (record is None or self._visual_event_sink is None
                or record.snapshot.turn_state in {'failed', 'unknown'}
                or record.receipt_id != payload.get('playback_id')
                or record.snapshot.voice_state != payload.get('voice_state')):
            return False
        session_id, _, _ = self._session_values(record.session)
        event = {
            'protocol': PROTOCOL_NAME, 'type': 'avatar.signal',
            'server_epoch': self.server_epoch, 'session_id': session_id,
            'turn_id': context.turn_id, 'correlation_id': context.correlation_id,
            'visual_sequence': sequence, 'payload': payload,
        }
        try:
            return bool(self._visual_event_sink(record.session, event))
        except Exception:
            return False  # Visual output cannot fail a chat or voice turn.

    def ledger_size(self) -> int:
        self._prune_locked()
        return len(self._records)

    @property
    def admission_open(self) -> bool:
        return self._admission_open and not self._quarantined and not self._shutting_down

    @property
    def quarantined(self) -> bool:
        return self._quarantined

    # ---- worker/deadline implementation --------------------------------------

    async def _run_turn(self, record: _TurnRecord) -> None:
        acquired = False
        record.started.set()
        # Admission is committed before waiting on the shared Core lock.  The
        # caller can observe ``accepted`` while this worker waits, and a
        # bounded lock timeout later transitions the same ledger row to
        # ``failed/core_busy``.
        record.admission_ready.set()
        record.deadline_task = asyncio.create_task(
            self._watch_deadlines(record), name="nana-private-turn-deadlines"
        )
        try:
            try:
                # Avoid wrapping an immediately available asyncio.Lock in a
                # wait_for task.  Besides reducing one scheduling turn, this
                # keeps fast fake dispatches deterministic for duplicate
                # admission tests while retaining the bounded wait path when
                # another Core lane owns the lock.
                if not self.turn_lock.locked():
                    await self.turn_lock.acquire()
                else:
                    await asyncio.wait_for(
                        self.turn_lock.acquire(), timeout=self.lock_wait_seconds
                    )
                acquired = True
            except asyncio.TimeoutError:
                self._finish_failed(record, "core_busy")
                return
            if record.outcome_locked:
                return
            self._emit_turn_state(record, "thinking")
            observer = _TurnObserver(self, record)
            try:
                scope = self._dispatch_scope(observer.identity) if self._dispatch_scope else nullcontext()
                with scope:
                    result = self.dispatch_turn_unlocked(record.source_text, observer)
                    if _is_awaitable(result):
                        result = await result
            except asyncio.CancelledError:
                raise
            except Exception:
                self._finish_failed(record, "provider_failed")
                return
            self._finish_from_result(record, result)
        finally:
            record.source_text = ''
            if acquired:
                self.turn_lock.release()
            if record.deadline_task is not None and not record.deadline_task.done():
                record.deadline_task.cancel()
                try:
                    await record.deadline_task
                except asyncio.CancelledError:
                    pass
            current = asyncio.current_task()
            if current is not None:
                self._active_tasks.discard(current)
            if record.snapshot.turn_state in _TERMINAL_STATES:
                self._remove_pending_if_owned(record)

    async def _watch_deadlines(self, record: _TurnRecord) -> None:
        try:
            await asyncio.sleep(self.observation_deadline_seconds)
            if record.snapshot.turn_state not in _TERMINAL_STATES:
                record.outcome_locked = True
                self._quarantined = True
                self._admission_open = False
                self._mark_unknown(record, "web_turn_stuck")
            remaining = self.wall_deadline_seconds - self.observation_deadline_seconds
            if remaining > 0:
                await asyncio.sleep(remaining)
            # ``unknown`` at the observation deadline is a web-visible
            # terminal snapshot, but the canonical dispatcher may still own
            # the shared lock.  The wall deadline must therefore close the
            # transport based on worker liveness, not only snapshot state.
            worker_pending = record.task is None or not record.task.done()
            if worker_pending:
                record.outcome_locked = True
                self._admission_open = False
                if record.snapshot.turn_state not in _TERMINAL_STATES:
                    self._mark_unknown(record, "web_turn_timeout")
                await self._close_session(record.session, 4010, "web_turn_timeout")
        except asyncio.CancelledError:
            raise

    # ---- state/event helpers ---------------------------------------------------

    def _on_observer_thinking(self, record: _TurnRecord) -> None:
        if record.snapshot.turn_state == "accepted" and not record.outcome_locked:
            self._emit_turn_state(record, "thinking")

    def _emit_initial_accepted(self, record: _TurnRecord) -> None:
        """Publish the already-committed revision-1 acceptance event."""

        record.event_sequence = 1
        try:
            context = SessionContext(
                self.server_epoch,
                record.session_id,
                record.key.client_instance_id,
            )
            event = build_server_event(
                context=context,
                turn_id=record.key.turn_id,
                correlation_id=record.correlation_id,
                revision=1,
                event_sequence=1,
                message_type="turn.state",
                payload={"state": "accepted"},
            )
        except Exception:
            event = {
                "protocol": PROTOCOL_NAME,
                "server_epoch": self.server_epoch,
                "session_id": record.session_id,
                "turn_id": record.key.turn_id,
                "correlation_id": record.correlation_id,
                "revision": 1,
                "event_sequence": 1,
                "type": "turn.state",
                "payload": {"state": "accepted"},
            }
        self._send_event(record.session, event)

    def _on_observer_delta(self, record: _TurnRecord, text: str, _revision: object = None) -> None:
        if record.outcome_locked or not text:
            return
        record._partial_text = getattr(record, '_partial_text', '') + text
        self._emit_event(record, "assistant.delta", {"delta_index": self._next_delta_index(record), "text": text})

    def _on_observer_final(self, record: _TurnRecord, text: str, _revision: object = None) -> None:
        if record.outcome_locked or record.final_emitted:
            return
        record.final_emitted = True
        self._set_final_text(record, text)
        self._emit_event(record, "assistant.final", {"text": text})
        if not record.generated_emitted:
            self._emit_turn_state(record, "generated")

    def _on_observer_voice(
        self,
        record: _TurnRecord,
        state: str,
        reason_code: str | None = None,
        *,
        voice_ticket: object = None,
    ) -> None:
        if state not in _VOICE_STATES:
            return
        transitions = {
            'idle': {'queued', 'failed', 'unknown'},
            'queued': {'speaking', 'failed', 'unknown'},
            'speaking': {'delivered', 'failed', 'unknown'},
        }
        if state not in transitions.get(record.snapshot.voice_state, set()):
            return
        payload: dict[str, object] = {"state": state}
        if reason_code is not None:
            payload["reason_code"] = reason_code
        if type(voice_ticket) is int and voice_ticket > 0:
            if record.voice_ticket is not None and record.voice_ticket != voice_ticket:
                return
            record.voice_ticket = voice_ticket
        self._emit_event(record, "voice.state", payload)

    def on_private_voice_receipt(self, receipt: object) -> None:
        """Apply a ledger receipt on the Core event loop.

        The receipt ledger never carries reply text. Matching uses the same
        session/turn/correlation identity that created the private turn.
        """

        context = getattr(receipt, "context", None)
        turn_id = getattr(context, "turn_id", None)
        session_id = getattr(context, "session_id", None)
        if not isinstance(turn_id, str) or not isinstance(session_id, str):
            return
        record = next(
            (
                item
                for item in self._records.values()
                if item.key.turn_id == turn_id and item.session_id == session_id
            ),
            None,
        )
        if record is None:
            return
        if (
            getattr(context, 'server_epoch', None) != self.server_epoch
            or getattr(context, 'correlation_id', None) != record.correlation_id
        ):
            return
        state = getattr(receipt, "state", None)
        reason = getattr(receipt, "reason_code", None)
        ticket = getattr(receipt, "engine_ticket", None)
        receipt_id = getattr(receipt, 'receipt_id', None)
        revision = getattr(receipt, 'revision', None)
        if type(ticket) is not int or ticket < 1 or type(revision) is not int:
            return
        if record.voice_ticket not in (None, ticket) or record.receipt_id not in (None, receipt_id):
            return
        if revision <= record.receipt_revision:
            return
        record.voice_ticket = ticket
        record.receipt_id = receipt_id
        record.receipt_revision = revision
        mapping = {
            "queued": "queued",
            "speaking": "speaking",
            "delivered": "delivered",
            "failed": "failed",
            "unknown": "unknown",
        }
        mapped = mapping.get(state)
        if mapped is not None:
            self._on_observer_voice(
                record,
                mapped,
                ('voice_failed' if mapped == 'failed' else 'unknown_outcome' if mapped == 'unknown' else None),
                voice_ticket=ticket,
            )

    def _finish_from_result(self, record: _TurnRecord, result: object) -> None:
        if (
            record.outcome_locked
            or record.terminal_emitted
            or record.snapshot.turn_state in _TERMINAL_STATES
        ):
            return
        parsed = self._coerce_result(result)
        status = parsed.status
        final_text = parsed.final_text
        if final_text is not None and not record.final_emitted:
            record.final_emitted = True
            self._set_final_text(record, final_text)
            self._emit_event(record, "assistant.final", {"text": final_text})
        elif final_text is not None:
            self._set_final_text(record, final_text)
        reason = parsed.reason_code
        if status in {"complete", "generated", "success", "ok"}:
            if not record.generated_emitted:
                self._emit_turn_state(record, "generated")
            if status != "generated":
                self._emit_turn_state(record, "complete")
        elif status in {"unknown", "indeterminate"}:
            self._finish_unknown(record, reason or "unknown_outcome")
        elif status in {"failed", "error", "rejected"}:
            self._finish_failed(record, reason or "provider_failed")
        else:
            # A dispatcher that returns an unrecognised result is failure, not
            # permission to invent a successful terminal turn.
            self._finish_failed(record, "provider_failed")

    def _finish_failed(self, record: _TurnRecord, reason_code: str) -> None:
        if record.snapshot.turn_state in _TERMINAL_STATES:
            return
        self._emit_turn_state(record, "failed", reason_code)

    def _finish_unknown(self, record: _TurnRecord, reason_code: str) -> None:
        if record.snapshot.turn_state in _TERMINAL_STATES:
            return
        record.outcome_locked = True
        self._emit_turn_state(record, "unknown", reason_code)

    def _mark_unknown(self, record: _TurnRecord, reason_code: str) -> None:
        if record.snapshot.turn_state not in _TERMINAL_STATES:
            self._finish_unknown(record, reason_code)

    def _emit_turn_state(
        self, record: _TurnRecord, state: str, reason_code: str | None = None
    ) -> None:
        if state not in _TURN_STATES:
            return
        if record.snapshot.turn_state in _TERMINAL_STATES:
            record.terminal_emitted = True
            return
        if record.snapshot.turn_state == state and (
            reason_code is None or record.snapshot.reason_code == reason_code
        ):
            return
        payload: dict[str, object] = {"state": state}
        if reason_code is not None:
            payload["reason_code"] = reason_code
        self._emit_event(record, "turn.state", payload, turn_state=state, reason_code=reason_code)
        if state == "generated":
            record.generated_emitted = True
        if state in _TERMINAL_STATES:
            record.terminal_emitted = True

    def _emit_event(
        self,
        record: _TurnRecord,
        message_type: str,
        payload: Mapping[str, object],
        *,
        turn_state: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        previous = record.snapshot
        next_revision = previous.revision + 1
        record.event_sequence += 1
        next_turn_state = turn_state or previous.turn_state
        next_voice_state = previous.voice_state
        final_text = previous.final_text
        if message_type == "voice.state":
            state = payload.get("state")
            if isinstance(state, str) and state in _VOICE_STATES:
                next_voice_state = state
        if message_type == "assistant.final":
            value = payload.get("text")
            if isinstance(value, str):
                final_text = value
        if message_type == "turn.state" and next_turn_state in _TERMINAL_STATES:
            record.outcome_locked = True
        record.snapshot = TurnSnapshot(
            key=previous.key,
            session_id=previous.session_id,
            correlation_id=previous.correlation_id,
            normalized_text_sha256=previous.normalized_text_sha256,
            revision=next_revision,
            turn_state=next_turn_state,
            voice_state=next_voice_state,
            final_text=final_text,
            reason_code=reason_code if reason_code is not None else previous.reason_code,
        )
        try:
            context = SessionContext(
                self.server_epoch,
                self._session_values(record.session)[0],
                record.key.client_instance_id,
            )
            event = build_server_event(
                context=context,
                turn_id=record.key.turn_id,
                correlation_id=record.correlation_id,
                revision=next_revision,
                event_sequence=record.event_sequence,
                message_type=message_type,
                payload=dict(payload),
            )
        except Exception:
            event = {
                "protocol": PROTOCOL_NAME,
                "server_epoch": self.server_epoch,
                "session_id": record.session_id,
                "turn_id": record.key.turn_id,
                "correlation_id": record.correlation_id,
                "revision": next_revision,
                "event_sequence": record.event_sequence,
                "type": message_type,
                "payload": dict(payload),
            }
        self._send_event(record.session, event)

    def _send_event(self, session: object, event: Mapping[str, object]) -> None:
        sink = self.event_sink
        if sink is None:
            return
        try:
            try:
                value = sink(session, dict(event))
            except TypeError:
                value = sink(dict(event))
            if _is_awaitable(value):
                task = asyncio.create_task(value, name="nana-private-turn-event")
                self._sink_tasks.add(task)
                task.add_done_callback(self._sink_tasks.discard)
        except Exception:
            # Observability cannot change the canonical turn result.
            return

    # ---- ledger / input helpers ------------------------------------------------

    def _prune_locked(self) -> None:
        now = self._now()
        for key, record in tuple(self._records.items()):
            if (
                record.snapshot.turn_state in _TERMINAL_STATES
                and record.snapshot.voice_state not in {'queued', 'speaking'}
                and now - record.created_at >= self.ledger_ttl_seconds
            ):
                removed = self._records.pop(key, None)
                if removed is record:
                    self._remove_pending_if_owned(record)
        self._evict_terminal_locked()

    def _evict_terminal_locked(self) -> None:
        while len(self._records) > self.ledger_limit:
            if not self._evict_one_terminal_locked():
                return

    def _evict_one_terminal_locked(self) -> bool:
        victim = next(
            (
                key
                for key, record in self._records.items()
                if record.snapshot.turn_state in _TERMINAL_STATES
                and record.snapshot.voice_state not in {'queued', 'speaking'}
            ),
            None,
        )
        if victim is None:
            return False
        record = self._records.pop(victim)
        self._remove_pending_if_owned(record)
        return True

    def _remove_pending_if_owned(self, record: _TurnRecord) -> None:
        session_slot = (record.key.server_epoch, record.session_id)
        if self._session_pending.get(session_slot) is record:
            self._session_pending.pop(session_slot, None)

    def _ephemeral_snapshot(
        self,
        session_id: str,
        client_instance_id: str,
        turn_id: object,
        correlation_id: object,
        turn_state: str,
        reason_code: str,
        text: str,
        *,
        epoch: str | None = None,
    ) -> TurnSnapshot:
        return TurnSnapshot(
            key=TurnKey(
                epoch or self.server_epoch,
                _safe_string(client_instance_id),
                _safe_string(turn_id),
            ),
            session_id=_safe_string(session_id),
            correlation_id=_safe_string(correlation_id),
            normalized_text_sha256=(
                _text_digest(text) if isinstance(text, str) and text.strip() else ""
            ),
            revision=1,
            turn_state=turn_state,
            voice_state="idle",
            final_text="",
            reason_code=reason_code,
        )

    @staticmethod
    def _coerce_result(value: object) -> PrivateTurnResult:
        if isinstance(value, PrivateTurnResult):
            return value
        if isinstance(value, Mapping):
            return PrivateTurnResult(
                bool(value.get("should_exit", False)),
                _safe_string(value.get("status"), "complete"),
                _safe_string(value.get("final_text", value.get("text", ""))),
                value.get("reason_code") if isinstance(value.get("reason_code"), str) else None,
                value.get("voice_ticket") if isinstance(value.get("voice_ticket"), int) else None,
            )
        return PrivateTurnResult(
            bool(getattr(value, "should_exit", False)),
            _safe_string(getattr(value, "status", None), "complete"),
            _safe_string(getattr(value, "final_text", getattr(value, "text", ""))),
            getattr(value, "reason_code", None)
            if isinstance(getattr(value, "reason_code", None), str)
            else None,
            getattr(value, "voice_ticket", None)
            if isinstance(getattr(value, "voice_ticket", None), int)
            else None,
        )

    @staticmethod
    def _required_string(value: object, reason_code: str) -> str:
        if not isinstance(value, str) or not value:
            raise TurnCoordinatorError(reason_code)
        return value

    @staticmethod
    def _frame_value(frame: object, name: str, default: object = None) -> object:
        if isinstance(frame, Mapping):
            if name in frame:
                return frame[name]
            if name == "message_type" and "type" in frame:
                return frame["type"]
            return default
        value = getattr(frame, name, default)
        if value is default and name == "message_type":
            value = getattr(frame, "type", default)
        return value

    @classmethod
    def _frame_payload(cls, frame: object) -> Mapping[str, object]:
        value = cls._frame_value(frame, "payload", {})
        return value if isinstance(value, Mapping) else {}

    @classmethod
    def _payload_value(cls, frame: object, name: str) -> object:
        payload = cls._frame_payload(frame)
        return payload.get(name)

    def _session_values(self, session: object) -> tuple[str, str, str]:
        if isinstance(session, SessionContext):
            return session.session_id, session.client_instance_id, session.server_epoch
        if isinstance(session, Mapping):
            session_id = session.get("session_id", session.get("provisional_session_id", ""))
            client_id = session.get("client_instance_id", "")
            epoch = session.get("server_epoch", self.server_epoch)
            return _safe_string(session_id), _safe_string(client_id), _safe_string(epoch)
        session_id = getattr(session, "session_id", None)
        if session_id is None:
            session_id = getattr(session, "confirmed_session_id", None)
        if session_id is None:
            session_id = getattr(session, "provisional_session_id", "")
        client_id = getattr(session, "client_instance_id", "")
        epoch = getattr(session, "server_epoch", self.server_epoch)
        return _safe_string(session_id), _safe_string(client_id), _safe_string(epoch)

    def _next_delta_index(self, record: _TurnRecord) -> int:
        # Delta indices are local to a turn and are independent from protocol
        # revisions.  Count only already-emitted delta events.
        count = getattr(record, "_delta_index", 0) + 1
        setattr(record, "_delta_index", count)
        return count

    def _set_final_text(self, record: _TurnRecord, text: str) -> None:
        snapshot = record.snapshot
        record.snapshot = TurnSnapshot(
            key=snapshot.key,
            session_id=snapshot.session_id,
            correlation_id=snapshot.correlation_id,
            normalized_text_sha256=snapshot.normalized_text_sha256,
            revision=snapshot.revision,
            turn_state=snapshot.turn_state,
            voice_state=snapshot.voice_state,
            final_text=text,
            reason_code=snapshot.reason_code,
        )

    def _now(self) -> float:
        try:
            return float(self._clock())
        except Exception:
            return time.monotonic()

    async def _close_session(self, session: object, code: int, reason: str) -> None:
        callback = self._close_session_callback
        if callback is None:
            callback = getattr(session, "close", None)
        if not callable(callback):
            callback = getattr(session, "close_session", None)
        if not callable(callback):
            return
        try:
            try:
                value = callback(code=code, reason=reason)
            except TypeError:
                try:
                    value = callback(code, reason)
                except TypeError:
                    value = callback(code)
            if _is_awaitable(value):
                await value
        except Exception:
            return


__all__ = [
    "GLOBAL_ACTIVE_TURNS",
    "LOCK_WAIT_SECONDS",
    "OBSERVATION_DEADLINE_SECONDS",
    "PENDING_SESSION_SLOTS",
    "PrivateTurnCoordinator",
    "PrivateTurnIdentity",
    "PrivateTurnResult",
    "TURN_LEDGER_LIMIT",
    "TURN_LEDGER_TTL_SECONDS",
    "TurnCoordinatorError",
    "TurnKey",
    "TurnSnapshot",
    "WALL_DEADLINE_SECONDS",
]
