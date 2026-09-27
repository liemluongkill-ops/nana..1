"""Incremental private-PC Text-to-Dialogue turn contract.

This module only coordinates exact text from the LLM producer to one voice
worker item. It never opens a WebSocket, calls ElevenLabs, or plays audio.
"""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
import queue
import re
import threading
import time
import uuid
from typing import Callable


_SENTENCE_ENDINGS = frozenset(".!?。！？")
_PHRASE_ENDINGS = frozenset(",;:")
_SAFE_BOUNDARIES = _SENTENCE_ENDINGS | _PHRASE_ENDINGS


@dataclass(frozen=True)
class TtdFinalPayload:
    full_text: str
    sent_text: str
    source_chunks: tuple[str, ...]
    llm_completed_at: float


@dataclass(frozen=True)
class TtdCommitRequest:
    turn_id: str
    text_queue: queue.SimpleQueue
    final_future: Future
    cancel_event: threading.Event
    turn_started_at: float
    committed_at: float


@dataclass(frozen=True)
class TtdQueueItem:
    turn_id: str
    text_queue: queue.SimpleQueue
    final_future: Future
    cancel_event: threading.Event
    turn_started_at: float
    committed_at: float
    voice_mode: str = "full"
    ticket: int = 0


@dataclass(frozen=True)
class TtdFinishResult:
    committed: bool
    ticket: int
    status: str
    reason: str
    full_chars: int
    sent_chars: int
    chunks: int


def _tags_closed(text: str) -> bool:
    in_tag = False
    for char in str(text or ""):
        if char == "[" and not in_tag:
            in_tag = True
        elif char == "]" and in_tag:
            in_tag = False
    return not in_tag


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", str(text or "").strip()))


def _safe_boundary(text: str) -> bool:
    if not text or not _tags_closed(text):
        return False
    return text[-1].isspace() or text[-1] in _SAFE_BOUNDARIES


class PrivateVoiceTtdTurn:
    """Commit one TTD job and feed it exact, ordered source chunks."""

    def __init__(
        self,
        commit_callback: Callable[[TtdCommitRequest], int],
        *,
        minimum_chars: int,
        minimum_words: int,
        chunk_target_chars: int,
        chunk_max_chars: int,
        turn_started_at: float | None = None,
        turn_id: str | None = None,
    ) -> None:
        self.turn_id = str(turn_id or uuid.uuid4().hex)
        self.turn_started_at = (
            time.perf_counter() if turn_started_at is None else float(turn_started_at)
        )
        self.minimum_chars = max(1, int(minimum_chars))
        self.minimum_words = max(1, int(minimum_words))
        self.chunk_target_chars = max(1, int(chunk_target_chars))
        self.chunk_max_chars = max(self.minimum_chars, int(chunk_max_chars))
        self.chunk_target_chars = min(
            self.chunk_target_chars,
            self.chunk_max_chars,
        )
        self.text_queue: queue.SimpleQueue = queue.SimpleQueue()
        self.final_future: Future = Future()
        self.cancel_event = threading.Event()
        self._commit_callback = commit_callback
        self._lock = threading.RLock()
        self._buffer = ""
        self._emitted_offset = 0
        self._source_chunks: list[str] = []
        self._finished = False
        self._committed = False
        self._ticket = 0
        self._reason = "waiting_threshold"

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
            self._buffer += raw
            if not self._committed:
                self._maybe_commit_locked()
            if self._committed:
                self._maybe_emit_locked()

    def _maybe_commit_locked(self) -> None:
        if self._committed or not _safe_boundary(self._buffer):
            return
        stripped_chars = len(self._buffer.strip())
        if stripped_chars < self.minimum_chars:
            self._reason = "waiting_chars"
            return
        if _word_count(self._buffer) < self.minimum_words:
            self._reason = "waiting_words"
            return
        if stripped_chars > self.chunk_max_chars:
            self._reason = "initial_chunk_exceeds_max"
            return

        committed_at = time.perf_counter()
        request = TtdCommitRequest(
            turn_id=self.turn_id,
            text_queue=self.text_queue,
            final_future=self.final_future,
            cancel_event=self.cancel_event,
            turn_started_at=self.turn_started_at,
            committed_at=committed_at,
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
        self._reason = "ttd_committed"
        self._emit_to_queue_locked(self._buffer)

    def _maybe_emit_locked(self) -> None:
        pending = self._buffer[self._emitted_offset :]
        if not pending or not _safe_boundary(pending):
            return
        pending_chars = len(pending.strip())
        if pending_chars <= 0:
            return
        last = pending[-1]
        sentence_ready = (
            last in _SENTENCE_ENDINGS
            and pending_chars >= self.chunk_target_chars
        )
        phrase_ready = (
            last in _PHRASE_ENDINGS
            and pending_chars >= max(self.minimum_chars, self.chunk_target_chars)
        )
        size_ready = pending_chars >= max(
            min(80, self.chunk_max_chars),
            self.chunk_target_chars,
        )
        max_ready = pending_chars >= self.chunk_max_chars
        if sentence_ready or phrase_ready or size_ready or max_ready:
            self._emit_to_queue_locked(pending)

    def _emit_to_queue_locked(self, chunk: str) -> None:
        if not chunk:
            return
        self.text_queue.put(chunk)
        self._source_chunks.append(chunk)
        self._emitted_offset += len(chunk)

    def finish(self, full_text: str) -> TtdFinishResult:
        full = str(full_text or "")
        with self._lock:
            self._finished = True
            if not self._committed:
                return TtdFinishResult(
                    committed=False,
                    ticket=0,
                    status="fallback_full",
                    reason=self._reason if self._reason != "waiting_threshold" else "llm_completed_before_commit",
                    full_chars=len(full),
                    sent_chars=0,
                    chunks=0,
                )
            if full != self._buffer:
                return self._fail_locked("ttd_stream_text_mismatch", len(full))

            remainder = self._buffer[self._emitted_offset :]
            if remainder:
                if not _tags_closed(remainder):
                    return self._fail_locked("audio_tag_open_at_eof", len(full))
                self._emit_to_queue_locked(remainder)

            sent = "".join(self._source_chunks)
            if sent != full:
                return self._fail_locked("ttd_exact_text_mismatch", len(full))

            if not self.final_future.done():
                self.final_future.set_result(
                    TtdFinalPayload(
                        full_text=full,
                        sent_text=sent,
                        source_chunks=tuple(self._source_chunks),
                        llm_completed_at=time.perf_counter(),
                    )
                )
            self.text_queue.put(None)
            return TtdFinishResult(
                committed=True,
                ticket=self._ticket,
                status="finalized",
                reason="exact_stream_closed",
                full_chars=len(full),
                sent_chars=len(sent),
                chunks=len(self._source_chunks),
            )

    def _fail_locked(self, reason: str, full_chars: int) -> TtdFinishResult:
        self.cancel_event.set()
        if not self.final_future.done():
            self.final_future.set_exception(ValueError(reason))
        self.text_queue.put(None)
        self._reason = reason
        return TtdFinishResult(
            committed=True,
            ticket=self._ticket,
            status="failed",
            reason=reason,
            full_chars=full_chars,
            sent_chars=sum(len(chunk) for chunk in self._source_chunks),
            chunks=len(self._source_chunks),
        )

    def abort(self, reason: str = "stream_aborted") -> bool:
        with self._lock:
            self._finished = True
            self.cancel_event.set()
            if self._committed and not self.final_future.done():
                self.final_future.set_exception(RuntimeError(str(reason)))
                self.text_queue.put(None)
            self._reason = str(reason)
            return self._committed


__all__ = [
    "PrivateVoiceTtdTurn",
    "TtdCommitRequest",
    "TtdFinalPayload",
    "TtdFinishResult",
    "TtdQueueItem",
]
