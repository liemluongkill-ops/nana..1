import itertools
import threading
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PendingAction:
    id: int
    action: str
    reason: str
    context: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0

    def age_seconds(self):
        return max(0.0, time.time() - self.created_at)

    def expires_in_seconds(self):
        return max(0.0, self.expires_at - time.time())

    def expired(self):
        return time.time() >= self.expires_at


class PendingActionStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending = None
        self._last_closed = None

    def propose(self, action, reason, context=None, ttl_seconds=60):
        now = time.time()
        pending = PendingAction(
            id=next(self._ids),
            action=action,
            reason=reason,
            context=dict(context or {}),
            created_at=now,
            expires_at=now + ttl_seconds,
        )
        with self._lock:
            if self._pending:
                self._last_closed = ("replaced", self._pending, now)
            self._pending = pending
        return pending

    def current(self):
        with self._lock:
            self._expire_locked()
            return self._pending

    def confirm(self, action_id=None):
        with self._lock:
            self._expire_locked()
            if not self._pending:
                return None, "no_pending_action"
            if action_id is not None and action_id != self._pending.id:
                return self._pending, "id_mismatch"
            pending = self._pending
            self._pending = None
            self._last_closed = ("confirmed", pending, time.time())
            return pending, "confirmed"

    def cancel(self, action_id=None):
        with self._lock:
            self._expire_locked()
            if not self._pending:
                return None, "no_pending_action"
            if action_id is not None and action_id != self._pending.id:
                return self._pending, "id_mismatch"
            pending = self._pending
            self._pending = None
            self._last_closed = ("cancelled", pending, time.time())
            return pending, "cancelled"

    def snapshot(self):
        with self._lock:
            self._expire_locked()
            return {
                "pending": self._pending,
                "last_closed": self._last_closed,
            }

    def _expire_locked(self):
        if self._pending and self._pending.expired():
            self._last_closed = ("expired", self._pending, time.time())
            self._pending = None


pending_actions = PendingActionStore()
