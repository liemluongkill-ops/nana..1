"""Private PC voice overlap contract.

This module owns the thread-safe handoff between the LLM stream and the single
blocking VoiceEngine worker. It never calls an LLM, TTS provider, playback, or
Presence output itself.
"""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
import threading
import time
import uuid
from typing import Callable

from nana.runtime.private_voice_receipts import PrivateVoiceContext


_SENTENCE_ENDINGS = frozenset(".!?。！？")
_BOUNDARY_CLOSERS = frozenset("\"'”’)]}")


@dataclass(frozen=True)
class OverlapTailPayload:
    full_text: str
    tail_text: str
    split_offset: int
    llm_completed_at: float


@dataclass(frozen=True)
class OverlapCommitRequest:
    turn_id: str
    lead_text: str
    split_offset: int
    tail_future: Future
    cancel_event: threading.Event
    turn_started_at: float
    lead_committed_at: float
    receipt_context: PrivateVoiceContext | None = None
    receipt_id: str | None = None


@dataclass(frozen=True)
class OverlapQueueItem:
    turn_id: str
    lead_text: str
    split_offset: int
    tail_future: Future
    cancel_event: threading.Event
    turn_started_at: float
    lead_committed_at: float
    voice_mode: str = "full"
    ticket: int = 0
    receipt_context: PrivateVoiceContext | None = None
    receipt_id: str | None = None


@dataclass(frozen=True)
class OverlapFinishResult:
    committed: bool
    ticket: int
    status: str
    reason: str
    full_chars: int
    lead_chars: int
    tail_chars: int


def _tags_closed(text: str) -> bool:
    """Fail closed for an unfinished square-bracket audio direction."""

    in_tag = False
    for char in str(text or ""):
        if char == "[" and not in_tag:
            in_tag = True
        elif char == "]" and in_tag:
            in_tag = False
    return not in_tag


def _candidate_end(text: str, *, minimum: int, maximum: int) -> tuple[int | None, str]:
    in_tag = False
    for index, char in enumerate(text):
        if char == "[" and not in_tag:
            in_tag = True
            continue
        if char == "]" and in_tag:
            in_tag = False
            continue
        if char not in _SENTENCE_ENDINGS or in_tag:
            continue
        prefix_chars = len(text[: index + 1].strip())
        if prefix_chars < minimum:
            continue
        if prefix_chars <= maximum:
            return index + 1, "eligible_sentence_boundary"
        return None, "boundary_exceeds_max"
    if len(text.strip()) > maximum:
        return None, "no_boundary_within_max"
    return None, "waiting_boundary"


class PrivateVoiceOverlapTurn:
    """Build exactly one immutable lead without blocking LLM consumption."""

    def __init__(
        self,
        commit_callback: Callable[[OverlapCommitRequest], int],
        *,
        minimum_chars: int,
        maximum_chars: int,
        coalesce_ms: int,
        turn_started_at: float | None = None,
        turn_id: str | None = None,
        receipt_context: PrivateVoiceContext | None = None,
    ) -> None:
        self.turn_id = str(turn_id or uuid.uuid4().hex)
        self.turn_started_at = (
            time.perf_counter() if turn_started_at is None else float(turn_started_at)
        )
        self.minimum_chars = max(1, int(minimum_chars))
        self.maximum_chars = max(self.minimum_chars, int(maximum_chars))
        self.coalesce_ms = max(0, int(coalesce_ms))
        self.tail_future: Future = Future()
        self.cancel_event = threading.Event()
        self._commit_callback = commit_callback
        self.receipt_context = receipt_context
        self._lock = threading.RLock()
        self._buffer = ""
        self._candidate_end: int | None = None
        self._timer: threading.Timer | None = None
        self._finished = False
        self._committed = False
        self._ticket = 0
        self._reason = "waiting_boundary"

    @property
    def committed(self) -> bool:
        with self._lock:
            return self._committed

    def feed(self, text: str) -> None:
        raw = str(text or "")
        if not raw:
            return
        with self._lock:
            if self._finished:
                return
            previous_length = len(self._buffer)
            self._buffer += raw
            if self._committed:
                return
            if self._candidate_end is not None:
                extension = self._buffer[self._candidate_end :]
                if extension and all(
                    char.isspace() or char in _BOUNDARY_CLOSERS
                    for char in extension
                ):
                    if len(self._buffer.strip()) <= self.maximum_chars:
                        self._candidate_end = len(self._buffer)
                return

            candidate_end, reason = _candidate_end(
                self._buffer,
                minimum=self.minimum_chars,
                maximum=self.maximum_chars,
            )
            self._reason = reason
            if candidate_end is None:
                return
            if candidate_end < previous_length:
                return
            if not _tags_closed(self._buffer[:candidate_end]):
                self._reason = "audio_tag_open"
                return
            self._candidate_end = candidate_end
            timer = threading.Timer(
                self.coalesce_ms / 1000.0,
                self._commit_candidate,
            )
            timer.daemon = True
            self._timer = timer
            timer.start()

    def _commit_candidate(self) -> None:
        with self._lock:
            if self._finished or self._committed or self._candidate_end is None:
                return
            lead_text = self._buffer[: self._candidate_end]
            if not lead_text.strip() or not _tags_closed(lead_text):
                self._reason = "invalid_candidate"
                return
            committed_at = time.perf_counter()
            request = OverlapCommitRequest(
                turn_id=self.turn_id,
                lead_text=lead_text,
                split_offset=self._candidate_end,
                tail_future=self.tail_future,
                cancel_event=self.cancel_event,
                turn_started_at=self.turn_started_at,
                lead_committed_at=committed_at,
                receipt_context=self.receipt_context,
            )
            try:
                ticket = int(self._commit_callback(request) or 0)
            except Exception:
                ticket = 0
            if ticket <= 0:
                self._reason = "voice_rejected_commit"
                return
            self._committed = True
            self._ticket = ticket
            self._reason = "lead_committed"

    def finish(self, full_text: str) -> OverlapFinishResult:
        full = str(full_text or "")
        with self._lock:
            self._finished = True
            if self._timer is not None:
                self._timer.cancel()
            if not self._committed:
                return OverlapFinishResult(
                    committed=False,
                    ticket=0,
                    status="fallback_full",
                    reason="llm_completed_before_commit",
                    full_chars=len(full),
                    lead_chars=0,
                    tail_chars=len(full),
                )
            if full != self._buffer:
                self.cancel_event.set()
                if not self.tail_future.done():
                    self.tail_future.set_exception(
                        ValueError("overlap_stream_text_mismatch")
                    )
                return OverlapFinishResult(
                    committed=True,
                    ticket=self._ticket,
                    status="failed",
                    reason="stream_text_mismatch",
                    full_chars=len(full),
                    lead_chars=int(self._candidate_end or 0),
                    tail_chars=0,
                )
            split_offset = int(self._candidate_end or 0)
            lead_text = full[:split_offset]
            tail_text = full[split_offset:]
            if not self.tail_future.done():
                self.tail_future.set_result(
                    OverlapTailPayload(
                        full_text=full,
                        tail_text=tail_text,
                        split_offset=split_offset,
                        llm_completed_at=time.perf_counter(),
                    )
                )
            return OverlapFinishResult(
                committed=True,
                ticket=self._ticket,
                status="tail_resolved",
                reason="exact_split",
                full_chars=len(full),
                lead_chars=len(lead_text),
                tail_chars=len(tail_text),
            )

    def abort(self, reason: str = "stream_aborted") -> bool:
        with self._lock:
            self._finished = True
            if self._timer is not None:
                self._timer.cancel()
            self.cancel_event.set()
            if self._committed and not self.tail_future.done():
                self.tail_future.set_exception(RuntimeError(str(reason)))
            self._reason = str(reason)
            return self._committed


__all__ = [
    "OverlapCommitRequest",
    "OverlapFinishResult",
    "OverlapQueueItem",
    "OverlapTailPayload",
    "PrivateVoiceOverlapTurn",
]
