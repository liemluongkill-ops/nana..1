"""Bounded, RAM-only visuals for receipted private playback.

No HTTP endpoint, PCM buffer, reply text or global avatar gateway is exposed.
The Core loop samples latest state; audio callbacks never enqueue websocket work.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
import math
import threading
import time
from uuid import uuid4

from .private_voice_receipts import PrivateVoiceContext, PrivateVoiceReceiptBinding
from .avatar_mouth_stream import pcm_level_to_envelope, STALE_AFTER_MS

MOUTH_TTL_MS = STALE_AFTER_MS
POLL_SECONDS = .04
ROW_LIMIT = 128
ROW_TTL_SECONDS = 1800
TERMINAL = frozenset({'delivered', 'failed', 'unknown'})


def _silence():
    return {'open': 0., 'energy': 0., 'viseme': 'sil', 'speaking': False}


def age_visual_event(event, age_ms):
    """Consume queue residence time before emitting a latest-only frame."""
    result = deepcopy(event)
    payload = result['payload']
    age_ms = max(0, int(age_ms))
    payload['expires_in_ms'] = max(0, payload['expires_in_ms'] - age_ms)
    if not payload['expires_in_ms']:
        payload['mouth'] = _silence()
    expression = payload['expression']
    if expression is not None:
        expression['expires_in_ms'] = max(0, expression['expires_in_ms'] - age_ms)
        if not expression['expires_in_ms']:
            payload['expression'] = None
    return result


@dataclass
class _VisualTurn:
    context: PrivateVoiceContext
    created_at: float
    receipt_id: str | None = None
    ticket: int | None = None
    receipt_revision: int = 0
    voice_state: str = 'idle'
    open_value: float = 0.
    sample_at: float = -math.inf
    considered: bool = False
    cue: str = ''
    expression: dict | None = None
    expression_until: float = 0.
    expression_attempted: bool = False
    sequence: int = 0
    terminal_sent: bool = False


class PrivateAvatarSignals:
    def __init__(self, receipt_ledger, *, clock=None):
        self._ledger = receipt_ledger
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._rows = OrderedDict()
        self._last_expression_at = -math.inf
        self._closed = False

    def begin_turn(self, context):
        if not isinstance(context, PrivateVoiceContext):
            return False
        with self._lock:
            if self._closed:
                return False
            now = self._clock()
            self._prune(now)
            if context in self._rows:
                return True
            if len(self._rows) >= ROW_LIMIT:
                victim = next((key for key, row in self._rows.items()
                               if row.voice_state in TERMINAL), None)
                if victim is None:
                    return False
                del self._rows[victim]
            self._rows[context] = _VisualTurn(context, now)
            return True

    def on_receipt(self, receipt):
        with self._lock:
            row = self._rows.get(getattr(receipt, 'context', None))
            if self._closed or row is None:
                return
            # A caller cannot invent speaking, ticket ownership or a revision.
            if self._ledger.current(receipt.receipt_id) != receipt:
                return
            self._apply_receipt(row, receipt)

    def _apply_receipt(self, row, receipt):
        if (row.voice_state in TERMINAL or receipt.revision <= row.receipt_revision
                or row.receipt_id not in (None, receipt.receipt_id)
                or row.ticket not in (None, receipt.engine_ticket)):
            return
        row.receipt_id, row.ticket = receipt.receipt_id, receipt.engine_ticket
        row.receipt_revision, row.voice_state = receipt.revision, receipt.state
        if receipt.state == 'speaking':
            self._activate_expression(row)
        elif receipt.state in TERMINAL:
            row.open_value = 0.
            row.sample_at = -math.inf
            row.expression = None
            row.cue = ''

    def publish_pcm_level(self, binding, level):
        if not isinstance(binding, PrivateVoiceReceiptBinding) or binding.receipt_ledger is not self._ledger:
            return False
        with self._lock:
            row = self._rows.get(binding.receipt_context)
            current = self._ledger.current(binding.receipt_id)
            if (self._closed or row is None or current is None or current.state != 'speaking'
                    or current.context != binding.receipt_context or current.engine_ticket != binding.ticket
                    or row.receipt_id != binding.receipt_id or row.ticket != binding.ticket
                    or row.voice_state != 'speaking'):
                return False
            # Same normalization as the accepted 5173 PCM envelope; Unity owns
            # its 40% mouth cap, smoothing and blendshape application.
            row.open_value = pcm_level_to_envelope(level)
            row.sample_at = self._clock()
            return True

    def publish_reply(self, context, reply):
        from .avatar_reaction_policy import select_reply_cue
        with self._lock:
            row = self._rows.get(context)
            if self._closed or row is None or row.considered or row.voice_state in TERMINAL:
                return False
            row.considered = True
            row.cue = select_reply_cue(reply).action
            if not row.cue:
                return False
            if row.voice_state == 'speaking':
                self._activate_expression(row)
            return True

    def _activate_expression(self, row):
        if not row.cue or row.expression_attempted:
            return
        from .avatar_reaction_policy import REPLY_ACTIONS, REPLY_COOLDOWN_SECONDS
        row.expression_attempted = True
        now = self._clock()
        if row.cue not in REPLY_ACTIONS or now - self._last_expression_at < REPLY_COOLDOWN_SECONDS:
            row.cue = ''  # Cooldown drops, never queues a surprise reaction.
            return
        row.expression = {'event_id': str(uuid4()), 'action': row.cue, 'duration_ms': 2000}
        row.expression_until = now + 2.
        self._last_expression_at = now

    def collect(self):
        """Sample bounded current frames; never replay a queue of old PCM."""
        with self._lock:
            if self._closed:
                return []
            now = self._clock()
            self._prune(now)
            result = []
            for row in self._rows.values():
                if row.receipt_id is None or row.terminal_sent:
                    continue
                current = self._ledger.current(row.receipt_id)
                if current is None:
                    continue
                self._apply_receipt(row, current)
                mouth = _silence()
                remaining = 0
                expression = None
                if row.voice_state == 'speaking':
                    age = (now - row.sample_at) * 1000
                    if 0 <= age < MOUTH_TTL_MS:
                        remaining = max(1, int(MOUTH_TTL_MS - age))
                        if row.open_value > .001:
                            mouth = dict(open=row.open_value, energy=0., viseme='aa', speaking=True)
                    if row.expression and now < row.expression_until:
                        expression = dict(row.expression, expires_in_ms=max(1, int((row.expression_until-now)*1000)))
                row.sequence += 1
                result.append((row.context, row.sequence, {
                    'playback_id': row.receipt_id, 'voice_state': row.voice_state,
                    'expires_in_ms': remaining, 'mouth': mouth, 'expression': expression,
                }))
                if row.voice_state in TERMINAL:
                    row.terminal_sent = True
            return result

    def close(self):
        with self._lock:
            self._closed = True
            self._rows.clear()

    def _prune(self, now):
        for context, row in tuple(self._rows.items()):
            if now - row.created_at > ROW_TTL_SECONDS:
                del self._rows[context]
