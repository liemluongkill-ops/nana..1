import itertools
import threading
import time
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SocialDraft:
    id: int
    intent: str
    policy: str
    draft: str
    request: str
    model: str | None = None
    context_level: str | None = None
    source_text: str | None = None
    reaction_style: str | None = None
    guard_hint: str | None = None
    context_priority: str | None = None
    needs_confirm: bool = False
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    status: str = "pending"

    def age_seconds(self):
        return max(0.0, time.time() - self.created_at)

    def expires_in_seconds(self):
        return max(0.0, self.expires_at - time.time())

    def expired(self):
        return bool(self.expires_at and time.time() >= self.expires_at)


class SocialDraftStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._drafts = {}
        self._last_closed = None

    def add(
        self,
        intent,
        policy,
        draft,
        request,
        model=None,
        context_level=None,
        source_text=None,
        reaction_style=None,
        guard_hint=None,
        context_priority=None,
        needs_confirm=False,
        ttl_seconds=900,
    ):
        now = time.time()
        item = SocialDraft(
            id=next(self._ids),
            intent=intent,
            policy=policy,
            draft=draft,
            request=request,
            model=model,
            context_level=context_level,
            source_text=source_text,
            reaction_style=reaction_style,
            guard_hint=guard_hint,
            context_priority=context_priority,
            needs_confirm=needs_confirm,
            created_at=now,
            expires_at=now + ttl_seconds,
        )
        with self._lock:
            self._expire_locked()
            self._drafts[item.id] = item
        return item

    def get(self, draft_id):
        with self._lock:
            self._expire_locked()
            return self._drafts.get(draft_id)

    def list_pending(self):
        with self._lock:
            self._expire_locked()
            return sorted(self._drafts.values(), key=lambda item: item.id)

    def confirm(self, draft_id):
        with self._lock:
            self._expire_locked()
            item = self._drafts.pop(draft_id, None)
            if not item:
                return None, "no_draft"
            closed = replace_status(item, "confirmed")
            self._last_closed = ("confirmed", closed, time.time())
            return closed, "confirmed"

    def cancel(self, draft_id):
        with self._lock:
            self._expire_locked()
            item = self._drafts.pop(draft_id, None)
            if not item:
                return None, "no_draft"
            closed = replace_status(item, "cancelled")
            self._last_closed = ("cancelled", closed, time.time())
            return closed, "cancelled"

    def snapshot(self):
        with self._lock:
            self._expire_locked()
            return {
                "pending": sorted(self._drafts.values(), key=lambda item: item.id),
                "last_closed": self._last_closed,
            }

    def _expire_locked(self):
        now = time.time()
        expired_ids = [draft_id for draft_id, item in self._drafts.items() if item.expired()]
        for draft_id in expired_ids:
            item = self._drafts.pop(draft_id)
            self._last_closed = ("expired", replace_status(item, "expired"), now)


def replace_status(item, status):
    return SocialDraft(
        id=item.id,
        intent=item.intent,
        policy=item.policy,
        draft=item.draft,
        request=item.request,
        model=item.model,
        context_level=item.context_level,
        source_text=item.source_text,
        reaction_style=item.reaction_style,
        guard_hint=item.guard_hint,
        context_priority=item.context_priority,
        needs_confirm=item.needs_confirm,
        created_at=item.created_at,
        expires_at=item.expires_at,
        status=status,
    )


social_drafts = SocialDraftStore()
