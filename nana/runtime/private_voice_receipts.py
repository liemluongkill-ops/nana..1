"""Correlated, provider-neutral voice receipts for private web turns.

The ledger is deliberately independent from VoiceEngine and transport code.
It records only identity, ticket and delivery state; speech text and provider
payloads never enter this module.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import threading
import time
import math
from typing import Callable, Literal
from uuid import UUID, uuid4


RECEIPT_LEDGER_LIMIT = 128
RECEIPT_TTL_SECONDS = 30.0 * 60.0
_RECEIPT_STATES = frozenset({"queued", "speaking", "delivered", "failed", "unknown"})
_TERMINAL_STATES = frozenset({"delivered", "failed", "unknown"})


class ReceiptLedgerFull(RuntimeError):
    """Raised when the bounded ledger has no terminal row available to evict."""


class ReceiptNotFound(KeyError):
    """Raised when a receipt handle does not identify a retained row."""


class ReceiptConflict(ValueError):
    """Raised when one turn attempts to create a second voice receipt."""


def _canonical_uuid(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid_{field}")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"invalid_{field}") from None
    if str(parsed) != value:
        raise ValueError(f"invalid_{field}")
    return value


def _safe_reason(value: object, default: str) -> str:
    allowed = {'queued', 'first_audio', 'audio_complete', 'audio_failed', 'voice_failed',
               'provider_failed', 'unknown_outcome', 'stop_unconfirmed', 'shutdown_timeout',
               'first_audio_unproven', 'queue_full', 'failed', 'unknown'}
    return value if isinstance(value, str) and value in allowed else default


@dataclass(frozen=True)
class PrivateVoiceContext:
    """The four canonical IDs that bind a voice attempt to one private turn."""

    server_epoch: str
    session_id: str
    turn_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        for name in ("server_epoch", "session_id", "turn_id", "correlation_id"):
            _canonical_uuid(getattr(self, name), name)


@dataclass(frozen=True)
class PrivateVoiceReceipt:
    """Immutable observable snapshot; no text or provider data is retained."""

    receipt_id: str
    context: PrivateVoiceContext
    engine_ticket: int
    revision: int
    state: Literal["queued", "speaking", "delivered", "failed", "unknown"]
    reason_code: str
    timestamp: float

    def __post_init__(self) -> None:
        _canonical_uuid(self.receipt_id, "receipt_id")
        if not isinstance(self.context, PrivateVoiceContext):
            raise TypeError("context must be PrivateVoiceContext")
        if type(self.engine_ticket) is not int or self.engine_ticket < 1:
            raise ValueError("invalid_engine_ticket")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("invalid_receipt_revision")
        if self.state not in _RECEIPT_STATES:
            raise ValueError("invalid_receipt_state")
        if not isinstance(self.reason_code, str) or not self.reason_code:
            raise ValueError("invalid_receipt_reason")
        if not isinstance(self.timestamp, (int, float)):
            raise ValueError("invalid_receipt_timestamp")


@dataclass
class PrivateVoiceReceiptBinding:
    """In-memory bridge carried by one VoiceEngine queue item.

    ``stop_confirmed`` is mutable only inside the Core worker so an ambiguous
    sink stop can be downgraded to ``unknown`` at terminal mapping time.
    """

    receipt_context: PrivateVoiceContext
    receipt_id: str
    receipt_ledger: "PrivateVoiceReceiptLedger"
    ticket: int
    stop_confirmed: bool = True


@dataclass
class _ReceiptRecord:
    context: PrivateVoiceContext
    engine_ticket: int
    receipt_id: str
    events: list[PrivateVoiceReceipt]

    @property
    def current(self) -> PrivateVoiceReceipt:
        return self.events[-1]

    @property
    def terminal(self) -> bool:
        return self.current.state in _TERMINAL_STATES


class PrivateVoiceReceiptLedger:
    """Thread-safe bounded state machine for one receipt per private turn."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] | None = None,
        event_sink: Callable[[PrivateVoiceReceipt], object] | None = None,
        max_tickets: int = RECEIPT_LEDGER_LIMIT,
        ttl_seconds: float = RECEIPT_TTL_SECONDS,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        if type(max_tickets) is not int or not 1 <= max_tickets <= RECEIPT_LEDGER_LIMIT:
            raise ValueError("invalid_receipt_ledger_limit")
        if type(ttl_seconds) not in (int, float) or not math.isfinite(ttl_seconds) or not 0 < ttl_seconds <= RECEIPT_TTL_SECONDS:
            raise ValueError("invalid_receipt_ttl")
        if event_sink is not None and not callable(event_sink):
            raise TypeError("event_sink must be callable")
        self._clock = clock or time.monotonic
        self._event_sink = event_sink
        self._max_tickets = int(max_tickets)
        self._ttl_seconds = float(ttl_seconds)
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._lock = threading.RLock()
        self._records: OrderedDict[str, _ReceiptRecord] = OrderedDict()
        self._by_context: dict[PrivateVoiceContext, str] = {}
        self._sink_failures = 0

    @property
    def sink_failures(self) -> int:
        with self._lock:
            return int(self._sink_failures)

    @property
    def max_tickets(self) -> int:
        return self._max_tickets

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def queued(
        self,
        context: PrivateVoiceContext,
        *,
        engine_ticket: int,
    ) -> PrivateVoiceReceipt:
        """Record one enqueue event, returning the existing row on retry."""

        self._validate_context_ticket(context, engine_ticket)
        with self._lock:
            self._prune_locked()
            existing_id = self._by_context.get(context)
            if existing_id is not None:
                existing = self._records.get(existing_id)
                if existing is not None:
                    if existing.engine_ticket != engine_ticket:
                        raise ReceiptConflict("voice_receipt_ticket_conflict")
                    return existing.current
                self._by_context.pop(context, None)

            self._ensure_capacity_locked()
            receipt_id = _canonical_uuid(str(self._id_factory()), "receipt_id")
            timestamp = float(self._clock())
            snapshot = PrivateVoiceReceipt(
                receipt_id=receipt_id,
                context=context,
                engine_ticket=engine_ticket,
                revision=1,
                state="queued",
                reason_code="queued",
                timestamp=timestamp,
            )
            record = _ReceiptRecord(context, engine_ticket, receipt_id, [snapshot])
            self._records[receipt_id] = record
            self._by_context[context] = receipt_id
        self._notify(snapshot)
        return snapshot

    def first_audio(
        self,
        receipt: PrivateVoiceReceipt | str,
        *,
        engine_ticket: int,
        context: PrivateVoiceContext | None = None,
    ) -> bool:
        """Advance queued -> speaking only for the exact ticket/context."""

        return self._transition(
            receipt,
            engine_ticket=engine_ticket,
            context=context,
            target_state="speaking",
            reason_code="first_audio",
        )

    def complete(
        self,
        receipt: PrivateVoiceReceipt | str,
        *,
        engine_ticket: int,
        completed: bool | None,
        context: PrivateVoiceContext | None = None,
        reason_code: str | None = None,
    ) -> bool:
        """Map confirmed completion/failure/ambiguity to one terminal state."""

        if completed is True:
            target = "delivered"
            reason = reason_code or "audio_complete"
        elif completed is False:
            target = "failed"
            reason = reason_code or "audio_failed"
        elif completed is None:
            target = "unknown"
            reason = reason_code or "unknown_outcome"
        else:
            raise TypeError("completed must be bool or None")
        return self._transition(
            receipt,
            engine_ticket=engine_ticket,
            context=context,
            target_state=target,
            reason_code=reason,
        )

    def fail(
        self,
        receipt: PrivateVoiceReceipt | str,
        *,
        engine_ticket: int,
        reason_code: str = "voice_failed",
        context: PrivateVoiceContext | None = None,
    ) -> bool:
        return self.complete(
            receipt,
            engine_ticket=engine_ticket,
            context=context,
            completed=False,
            reason_code=reason_code,
        )

    def unknown(
        self,
        receipt: PrivateVoiceReceipt | str,
        *,
        engine_ticket: int,
        reason_code: str = "unknown_outcome",
        context: PrivateVoiceContext | None = None,
    ) -> bool:
        return self.complete(
            receipt,
            engine_ticket=engine_ticket,
            context=context,
            completed=None,
            reason_code=reason_code,
        )

    def current(self, receipt: PrivateVoiceReceipt | str) -> PrivateVoiceReceipt | None:
        with self._lock:
            record = self._lookup_locked(receipt, required=False)
            return None if record is None else record.current

    def events(self, receipt: PrivateVoiceReceipt | str) -> tuple[PrivateVoiceReceipt, ...]:
        with self._lock:
            record = self._lookup_locked(receipt, required=True)
            return tuple(record.events)

    def active(self):
        with self._lock:
            return tuple(record.current for record in self._records.values() if not record.terminal)

    def clear(self):
        with self._lock:
            self._records.clear()
            self._by_context.clear()

    def expire_active(self, reason='unknown_outcome'):
        for receipt in self.active():
            self.unknown(receipt, engine_ticket=receipt.engine_ticket, context=receipt.context, reason_code=reason)

    def prune(self) -> int:
        with self._lock:
            before = len(self._records)
            self._prune_locked()
            return before - len(self._records)

    def _transition(
        self,
        receipt: PrivateVoiceReceipt | str,
        *,
        engine_ticket: int,
        context: PrivateVoiceContext | None,
        target_state: str,
        reason_code: str,
    ) -> bool:
        if type(engine_ticket) is not int or engine_ticket < 1:
            return False
        if target_state not in _RECEIPT_STATES:
            return False
        snapshot: PrivateVoiceReceipt | None = None
        with self._lock:
            record = self._lookup_locked(receipt, required=False)
            if record is None or record.engine_ticket != engine_ticket:
                return False
            if context is not None and context != record.context:
                return False
            current = record.current
            if current.state in _TERMINAL_STATES:
                return False
            if target_state == "speaking" and current.state != "queued":
                return False
            if target_state == "delivered" and current.state != "speaking":
                # Delivery without first-audio evidence is never accepted.
                return False
            if target_state in {"failed", "unknown"} and current.state not in {
                "queued",
                "speaking",
            }:
                return False
            snapshot = PrivateVoiceReceipt(
                receipt_id=current.receipt_id,
                context=current.context,
                engine_ticket=current.engine_ticket,
                revision=current.revision + 1,
                state=target_state,  # type: ignore[arg-type]
                reason_code=_safe_reason(reason_code, target_state),
                timestamp=float(self._clock()),
            )
            record.events.append(snapshot)
            self._records.move_to_end(record.receipt_id)
        self._notify(snapshot)
        return True

    def _validate_context_ticket(self, context: PrivateVoiceContext, engine_ticket: int) -> None:
        if not isinstance(context, PrivateVoiceContext):
            raise TypeError("context must be PrivateVoiceContext")
        if type(engine_ticket) is not int or engine_ticket < 1:
            raise ValueError("invalid_engine_ticket")

    def _lookup_locked(
        self,
        receipt: PrivateVoiceReceipt | str,
        *,
        required: bool,
    ) -> _ReceiptRecord | None:
        receipt_id = receipt.receipt_id if isinstance(receipt, PrivateVoiceReceipt) else receipt
        if not isinstance(receipt_id, str):
            if required:
                raise ReceiptNotFound("invalid_receipt_id")
            return None
        record = self._records.get(receipt_id)
        if record is None and required:
            raise ReceiptNotFound(receipt_id)
        return record

    def _ensure_capacity_locked(self) -> None:
        while len(self._records) >= self._max_tickets:
            victim_id = next(
                (
                    receipt_id
                    for receipt_id, record in self._records.items()
                    if record.terminal
                ),
                None,
            )
            if victim_id is None:
                raise ReceiptLedgerFull("voice_receipt_ledger_full")
            victim = self._records.pop(victim_id)
            if self._by_context.get(victim.context) == victim_id:
                self._by_context.pop(victim.context, None)

    def _prune_locked(self) -> None:
        now = float(self._clock())
        expired: list[str] = []
        for receipt_id, record in self._records.items():
            if record.terminal and now - record.current.timestamp >= self._ttl_seconds:
                expired.append(receipt_id)
        for receipt_id in expired:
            record = self._records.pop(receipt_id, None)
            if record is not None and self._by_context.get(record.context) == receipt_id:
                self._by_context.pop(record.context, None)

    def _notify(self, snapshot: PrivateVoiceReceipt | None) -> None:
        if snapshot is None or self._event_sink is None:
            return
        try:
            self._event_sink(snapshot)
        except Exception:
            with self._lock:
                self._sink_failures += 1


__all__ = [
    "RECEIPT_LEDGER_LIMIT",
    "RECEIPT_TTL_SECONDS",
    "PrivateVoiceContext",
    "PrivateVoiceReceipt",
    "PrivateVoiceReceiptBinding",
    "PrivateVoiceReceiptLedger",
    "ReceiptConflict",
    "ReceiptLedgerFull",
    "ReceiptNotFound",
]
