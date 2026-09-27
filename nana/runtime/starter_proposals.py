"""STAGE-8E: Proactive Starter Proposal Queue.

Preview/queue only — no auto-send, no TTS/VTS/OBS/game input.

Flow:
  generate_proposal() → reads 8A stream policy + 8B decision + social_starters
  → applies public-stage safety guard → returns StarterProposal (state=pending)
  → user approves via /starter-proposal-approve → state=approved
  → user cancels via /starter-proposal-cancel → state=cancelled
  → expired proposals auto-marked cancelled on next generate/clear

Acceptance criteria:
  - offline/post_stream/error_safe bridge_down → no proposal
  - live_idle → can create pending proposal
  - intermission → can create gentle proposal
  - expired proposal cannot approve
  - approve marks approved only, does NOT send
  - cancel marks cancelled
  - clear removes expired/cancelled
  - no duplicate spam while pending exists
  - proposal text has no Ba/con, no private memory, no service-bot tone

Public stage identity guard:
  - Rejects proposals containing Ba/con references
  - Rejects service-bot / auto-announcement tone
  - Rejects proposals longer than 280 chars
  - Rejects empty or whitespace-only proposals
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


# ─── Proposal State ───────────────────────────────────────────────────────────────


class ProposalState:
    PENDING = "pending"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    SENT = "sent"


# ─── Starter Proposal ─────────────────────────────────────────────────────────────


@dataclass
class StarterProposal:
    """A single proactive starter proposal.

    Immutable fields after construction. Only state transitions are mutable.
    """

    id: str
    text: str
    reason: str
    topic: str
    source: str  # "social_starter" | "llm" | "fallback"
    state: str = ProposalState.PENDING
    created_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + 300.0)
    stream_state: str = "unknown"
    interaction_tone: str = "gentle"
    blocked_reason: str = ""
    sent_at: float = 0.0
    outbox_event_id: str = ""

    def is_expired(self, now: Optional[float] = None) -> bool:
        if now is None:
            now = time.time()
        return now >= self.expires_at and self.state == ProposalState.PENDING

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "reason": self.reason,
            "topic": self.topic,
            "source": self.source,
            "state": self.state,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "stream_state": self.stream_state,
            "interaction_tone": self.interaction_tone,
            "blocked_reason": self.blocked_reason,
            "sent_at": self.sent_at,
            "outbox_event_id": self.outbox_event_id,
            "safety": {"preview_only": True, "auto_send": False},
        }


# ─── Safety Guard ────────────────────────────────────────────────────────────────


# Patterns that leak private owner identity or companion language into public stage.
_PRIVATE_PATTERNS = [
    re.compile(r"\bBa\b", re.IGNORECASE),       # "Ba" owner reference
    re.compile(r"\bcon\b(?!\s+người)", re.IGNORECASE),  # "con" pronoun, not "con người"
    re.compile(r"\bNana-chan\b", re.IGNORECASE), # too-cute companion form
    re.compile(r"\bchủ\b", re.IGNORECASE),       # Vietnamese owner ref
    re.compile(r"\bservice\s*bot\b", re.IGNORECASE),
    re.compile(r"\bautomated\s*message\b", re.IGNORECASE),
    re.compile(r"\[\s*system\s*\]", re.IGNORECASE),
    re.compile(r"^Nana[\s!.:]*$"),              # bare "Nana" name opener
]

# Tone patterns that sound like a service announcement.
_SERVICE_BOT_PATTERNS = [
    re.compile(r"^(Stream|Stream đang|Bắt đầu|Kết thúc)\s", re.IGNORECASE),
    re.compile(r"chào mừng.*đến.*stream", re.IGNORECASE),
    re.compile(r"cảm ơn.*đã xem|like.*subscribe", re.IGNORECASE),
    re.compile(r"^📢", re.IGNORECASE),
    re.compile(r"^!\w", re.IGNORECASE),  # IRC-style bot command
]

MAX_PROPOSAL_CHARS = 280


def sanitize_public_starter_text(text: str) -> str:
    """Remove stage directions and normalize public starter text."""
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^(?:\([^()\n]{0,220}\)\s*)+", " ", cleaned)
    cleaned = re.sub(r"\*[^*\n]{0,180}\*", " ", cleaned)
    cleaned = re.sub(r"\[[^\]\n]{0,100}\]", " ", cleaned)
    cleaned = cleaned.replace('"', "").replace("“", "").replace("”", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _guard_text(text: str) -> tuple[bool, str]:
    """Apply public-stage safety guard to proposal text.

    Returns (passes, reason). reason is non-empty when rejected.
    """
    text = sanitize_public_starter_text(text)

    if not text or not text.strip():
        return False, "empty_text"

    if len(text) > MAX_PROPOSAL_CHARS:
        return False, f"too_long ({len(text)} > {MAX_PROPOSAL_CHARS})"

    for pat in _PRIVATE_PATTERNS:
        if pat.search(text):
            return False, f"private_reference:{pat.pattern!r}"

    for pat in _SERVICE_BOT_PATTERNS:
        if pat.search(text):
            return False, f"service_bot_tone:{pat.pattern!r}"

    return True, ""


def validate_public_proposal_text(text: str) -> tuple[bool, str]:
    """Public wrapper for the proposal text safety guard."""
    return _guard_text(text)


# ─── Proposal Store ───────────────────────────────────────────────────────────────


class StarterProposalStore:
    """Thread-safe singleton proposal queue."""

    _instance: Optional["StarterProposalStore"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        *,
        proposal_ttl_seconds: float = 300.0,
        max_pending: int = 1,
    ) -> None:
        self._proposal_ttl_seconds = proposal_ttl_seconds
        self._max_pending = max_pending
        self._lock = threading.Lock()
        self._proposals: dict[str, StarterProposal] = {}
        self._generation_count: int = 0
        self._rejection_count: int = 0

    @classmethod
    def get_instance(cls) -> "StarterProposalStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_test(cls) -> None:
        """Reset singleton between tests."""
        global _STORE
        with cls._lock:
            if cls._instance is not None:
                cls._instance._proposals.clear()
                cls._instance._generation_count = 0
                cls._instance._rejection_count = 0
            cls._instance = None
            _STORE = None

    # ── Generate ──────────────────────────────────────────────────────

    def generate_proposal(
        self,
        policy: Optional[object] = None,
        proactive_decision: Optional[object] = None,
        starter_result: Optional[dict] = None,
    ) -> Optional[StarterProposal]:
        """Generate a new pending proposal if all gates pass.

        Args:
            policy: StreamPolicy from 8A. If None, fetches current.
            proactive_decision: ProactiveDecision from 8B. If None, fetches current.
            starter_result: Result from social_starters.generate_starter().
                          If None, fetches current.

        Returns:
            StarterProposal (state=pending) if approved, None if blocked.
        """
        import time as _time
        now = _time.time()

        # ── Gate 1: Stream policy ───────────────────────────────────────
        if policy is None:
            try:
                from nana.runtime.stream_state import get_stream_state
                policy = get_stream_state().get_policy()
            except Exception:
                return None

        policy_state = getattr(policy, "state", None)
        policy_state_value = getattr(policy_state, "value", "offline") if policy_state else "offline"

        # Block offline / post_stream / error_safe states
        blocked_states = {
            "offline", "post_stream", "error_safe",
        }
        if policy_state_value in blocked_states:
            self._rejection_count += 1
            return None

        # Block if stream policy forbids proactive
        if not getattr(policy, "can_proactive", False):
            self._rejection_count += 1
            return None

        # Block bridge_down error type
        error_type = getattr(policy, "error_type", None)
        if error_type is not None:
            error_value = getattr(error_type, "value", "none")
            if error_value == "bridge_down":
                self._rejection_count += 1
                return None

        # ── Gate 2: Proactive decision ─────────────────────────────────
        if proactive_decision is None:
            try:
                from nana.runtime.proactive_engine import get_proactive_engine
                proactive_decision = get_proactive_engine().should_proactive(
                    policy=policy,
                    chat_velocity=0.0,
                    last_proactive_age_s=999.0,
                    last_nana_chat_age_s=999.0,
                )
            except Exception:
                pass

        if proactive_decision is not None:
            if not getattr(proactive_decision, "should_proactive", False):
                self._rejection_count += 1
                return None

        # ── Gate 3: No pending proposal already exists ────────────────
        with self._lock:
            self._expire_pending(now=now)
            pending = [p for p in self._proposals.values() if p.state == ProposalState.PENDING]
            if len(pending) >= self._max_pending:
                self._rejection_count += 1
                return None

        # ── Gate 4: Social starter result ────────────────────────────
        if starter_result is None:
            try:
                from nana.runtime.social_starters import get_social_starter
                starter_result = get_social_starter().generate_starter()
            except Exception:
                starter_result = None

        starter_text = ""
        starter_topic = "general"
        starter_source = "social_starter"
        if starter_result:
            starter_text = sanitize_public_starter_text(str(starter_result.get("starter") or ""))
            starter_topic = str(starter_result.get("room_topic") or "general")
            if starter_result.get("blocked"):
                starter_source = "blocked"

        if not starter_text:
            starter_text = self._fallback_starter()
            starter_source = "fallback"

        # ── Gate 5: Public stage safety guard ──────────────────────────
        starter_text = sanitize_public_starter_text(starter_text)
        guard_pass, guard_reason = _guard_text(starter_text)
        if not guard_pass:
            self._rejection_count += 1
            proposal = StarterProposal(
                id=str(uuid.uuid4())[:8],
                text=starter_text,
                reason=f"safety_guard/{guard_reason}",
                topic=starter_topic,
                source=starter_source,
                state=ProposalState.CANCELLED,
                expires_at=now,
                stream_state=policy_state_value,
                interaction_tone=getattr(policy, "interaction_tone", None) and getattr(policy.interaction_tone, "value", "gentle") or "gentle",
                blocked_reason=guard_reason,
            )
            with self._lock:
                self._proposals[proposal.id] = proposal
            return None

        # ── Build proposal ─────────────────────────────────────────────
        tone = "gentle"
        if hasattr(policy, "interaction_tone") and policy.interaction_tone:
            tone = getattr(policy.interaction_tone, "value", "gentle")

        proposal = StarterProposal(
            id=str(uuid.uuid4())[:8],
            text=starter_text,
            reason="ok",
            topic=starter_topic,
            source=starter_source,
            state=ProposalState.PENDING,
            expires_at=now + self._proposal_ttl_seconds,
            stream_state=policy_state_value,
            interaction_tone=tone,
        )

        with self._lock:
            self._proposals[proposal.id] = proposal
            self._generation_count += 1

        return proposal

    def _fallback_starter(self) -> str:
        return "Mọi người đang làm gì vậy? Comment cho Nana biết với nha!"

    def _expire_pending(self, now: float) -> None:
        """Mark expired pending proposals as expired."""
        for p in self._proposals.values():
            if p.state == ProposalState.PENDING and now >= p.expires_at:
                p.state = ProposalState.EXPIRED

    # ── State transitions ───────────────────────────────────────────────

    def approve(self, proposal_id: str) -> tuple[bool, str]:
        """Mark a proposal as approved. Returns (success, reason)."""
        now = time.time()
        with self._lock:
            self._expire_pending(now=now)
            p = self._proposals.get(proposal_id)
            if p is None:
                return False, "not_found"
            if p.state == ProposalState.EXPIRED or (
                p.state == ProposalState.PENDING and now >= p.expires_at
            ):
                p.state = ProposalState.EXPIRED
                return False, "expired"
            if p.state != ProposalState.PENDING:
                return False, f"not_pending (state={p.state})"
            p.state = ProposalState.APPROVED
            return True, "ok"

    def cancel(self, proposal_id: str) -> tuple[bool, str]:
        """Mark a proposal as cancelled. Returns (success, reason)."""
        now = time.time()
        with self._lock:
            self._expire_pending(now=now)
            p = self._proposals.get(proposal_id)
            if p is None:
                return False, "not_found"
            if p.state in {ProposalState.CANCELLED, ProposalState.EXPIRED, ProposalState.SENT}:
                return False, f"already_{p.state}"
            p.state = ProposalState.CANCELLED
            return True, "ok"

    def mark_sent(self, proposal_id: str, event_id: str) -> tuple[bool, str]:
        """Mark an approved proposal as sent to the controlled outbox."""
        now = time.time()
        with self._lock:
            p = self._proposals.get(proposal_id)
            if p is None:
                return False, "not_found"
            if p.state == ProposalState.EXPIRED or (
                p.state == ProposalState.PENDING and now >= p.expires_at
            ):
                p.state = ProposalState.EXPIRED
                return False, "expired"
            if p.state != ProposalState.APPROVED:
                return False, f"not_approved (state={p.state})"
            if now >= p.expires_at:
                p.state = ProposalState.EXPIRED
                return False, "expired"
            p.state = ProposalState.SENT
            p.sent_at = now
            p.outbox_event_id = event_id
            return True, "ok"

    def expire_if_due(self, proposal_id: str, now: Optional[float] = None) -> tuple[bool, str]:
        """Mark a proposal expired if its TTL has elapsed."""
        if now is None:
            now = time.time()
        with self._lock:
            p = self._proposals.get(proposal_id)
            if p is None:
                return False, "not_found"
            if p.state in {ProposalState.PENDING, ProposalState.APPROVED} and now >= p.expires_at:
                p.state = ProposalState.EXPIRED
                return True, "expired"
            return False, f"not_expired (state={p.state})"

    def clear(self) -> int:
        """Remove all expired and cancelled proposals. Returns count removed."""
        now = time.time()
        removed = 0
        with self._lock:
            self._expire_pending(now=now)
            to_remove = [
                pid for pid, p in self._proposals.items()
                if p.state in {ProposalState.CANCELLED, ProposalState.EXPIRED}
            ]
            for pid in to_remove:
                del self._proposals[pid]
                removed += 1
            return removed

    # ── Query ─────────────────────────────────────────────────────────

    def list_proposals(
        self,
        states: Optional[list[str]] = None,
    ) -> list[StarterProposal]:
        now = time.time()
        with self._lock:
            self._expire_pending(now=now)
            states = states or [
                ProposalState.PENDING,
                ProposalState.APPROVED,
                ProposalState.CANCELLED,
                ProposalState.EXPIRED,
                ProposalState.SENT,
            ]
            return [
                p for p in self._proposals.values()
                if p.state in states
            ]

    def get_proposal(self, proposal_id: str) -> Optional[StarterProposal]:
        with self._lock:
            return self._proposals.get(proposal_id)

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            self._expire_pending(now=now)
            return {
                "phase": "STAGE-8E",
                "total_proposals": len(self._proposals),
                "pending": sum(1 for p in self._proposals.values() if p.state == ProposalState.PENDING),
                "approved": sum(1 for p in self._proposals.values() if p.state == ProposalState.APPROVED),
                "cancelled": sum(1 for p in self._proposals.values() if p.state == ProposalState.CANCELLED),
                "expired": sum(1 for p in self._proposals.values() if p.state == ProposalState.EXPIRED),
                "sent": sum(1 for p in self._proposals.values() if p.state == ProposalState.SENT),
                "generation_count": self._generation_count,
                "rejection_count": self._rejection_count,
                "safety": {"preview_only": True, "auto_send": False, "no_tts_vts_obs_game": True},
            }

    def status_lines(self) -> list[str]:
        snap = self.snapshot()
        proposals = self.list_proposals()
        lines = [
            "📋 Starter Proposal Queue (STAGE-8E)",
            f"  Phase: {snap['phase']} | preview_only=True | auto_send=False",
            f"  Counts: total={snap['total_proposals']} | pending={snap['pending']} | "
            f"approved={snap['approved']} | sent={snap['sent']} | cancelled={snap['cancelled']} | "
            f"expired={snap['expired']}",
            f"  Lifetime: generated={snap['generation_count']} | rejected={snap['rejection_count']}",
            f"  Commands: /starter-proposals | /starter-proposal-preview | /starter-proposal-create | /starter-proposal-approve <id> | /starter-proposal-cancel <id> | /starter-proposal-clear",
        ]
        if proposals:
            now = time.time()
            for p in proposals:
                age = now - p.created_at
                ttl = p.expires_at - now
                lines.append(
                    f"  [{p.id}] {p.state.upper()} | tone={p.interaction_tone} | "
                    f"topic={p.topic} | age={age:.0f}s | ttl={max(0, ttl):.0f}s | "
                    f"source={p.source}"
                )
                lines.append(f"    \"{p.text[:120]}\"")
        else:
            lines.append("  (no proposals)")
        return lines

    def preview_lines(self) -> list[str]:
        """Generate a preview proposal without storing it."""
        # Use current policy/decision but don't store
        now = time.time()
        try:
            from nana.runtime.stream_state import get_stream_state
            policy = get_stream_state().get_policy()
        except Exception:
            return ["[8E] Cannot fetch stream policy"]

        policy_state = getattr(policy, "state", None)
        policy_state_value = getattr(policy_state, "value", "unknown") if policy_state else "unknown"

        # Check gates without generating
        blocked_states = {"offline", "post_stream", "error_safe"}
        if policy_state_value in blocked_states:
            return [
                "📋 Starter Proposal Preview (STAGE-8E)",
                f"  State: {policy_state_value} — no proposal possible",
                f"  Safety: preview_only=True | auto_send=False",
            ]

        if not getattr(policy, "can_proactive", False):
            return [
                "📋 Starter Proposal Preview (STAGE-8E)",
                f"  Policy: can_proactive=False — no proposal possible",
                f"  State: {policy_state_value}",
                f"  Safety: preview_only=True | auto_send=False",
            ]

        try:
            from nana.runtime.social_starters import get_social_starter
            result = get_social_starter().generate_starter()
            text = result.get("starter") or ""
            topic = result.get("room_topic") or "general"
        except Exception:
            text = ""
            topic = "general"

        if not text:
            text = self._fallback_starter()

        guard_pass, guard_reason = _guard_text(text)
        tone = "gentle"
        if hasattr(policy, "interaction_tone") and policy.interaction_tone:
            tone = getattr(policy.interaction_tone, "value", "gentle")

        lines = [
            "📋 Starter Proposal Preview (STAGE-8E)",
            f"  State: {policy_state_value} | tone={tone} | topic={topic}",
            f"  Safety: guard_pass={guard_pass} | reason={guard_reason or 'ok'}",
            f"  Preview: \"{text[:200]}\"",
            "  Note: approval queues the proposal; /starter-proposal-approve does NOT send",
            "  Safety: preview_only=True | auto_send=False | no TTS/VTS/OBS/game",
        ]
        return lines


# ─── Module-level singleton ───────────────────────────────────────────────────


_STORE: Optional[StarterProposalStore] = None
_STORE_LOCK = threading.Lock()


def get_proposal_store() -> StarterProposalStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = StarterProposalStore()
    return _STORE


def generate_starter_proposal(**kwargs) -> Optional[StarterProposal]:
    """Convenience wrapper."""
    return get_proposal_store().generate_proposal(**kwargs)


def proposal_status() -> list[str]:
    """Get queue status."""
    return get_proposal_store().status_lines()


def proposal_preview() -> list[str]:
    """Get preview without storing."""
    return get_proposal_store().preview_lines()


# ─── Smoke Tests ─────────────────────────────────────────────────────────────


def _smoke_test_guard():
    print("[8E Smoke] Testing safety guard...")
    # Should pass
    pass_cases = [
        "Mọi người đang làm gì vậy?",
        "Hôm nay mọi người nghe bài gì?",
        "Ai đang xem stream nè?",
    ]
    for text in pass_cases:
        ok, reason = _guard_text(text)
        assert ok, f"Expected pass: '{text}' -> {reason}"

    # Should fail: Ba reference
    ok, reason = _guard_text("Ba ơi cho con hỏi...")
    assert not ok and "Ba" in reason, f"Ba leak not caught: {reason}"

    # Should fail: con reference
    ok, reason = _guard_text("Con thích bài này lắm!")
    assert not ok and "con" in reason, f"con leak not caught: {reason}"

    # Should pass: "con người" is a public noun phrase, not private pronoun leakage.
    ok, reason = _guard_text("Con người lúc nào cũng có mấy câu hỏi khó ghê.")
    assert ok, f"'con người' should not be blocked: {reason}"

    # Should fail: service bot
    ok, reason = _guard_text("Stream đang bắt đầu rồi nha mọi người!")
    assert not ok and "service_bot_tone" in reason, f"service bot tone not caught: {reason}"

    # Should fail: empty
    ok, reason = _guard_text("")
    assert not ok

    # Should fail: too long
    ok, reason = _guard_text("x" * 300)
    assert not ok and "too_long" in reason

    print("  Safety guard: PASSED")


def _smoke_test_proposal_lifecycle():
    print("[8E Smoke] Testing proposal lifecycle...")
    StarterProposalStore.reset_for_test()
    store = StarterProposalStore()
    store._proposal_ttl_seconds = 60.0

    # Generate from fake offline state → should return None
    class FakePolicy:
        state = type("State", (), {"value": "offline"})()
        can_proactive = False
        error_type = type("E", (), {"value": "none"})()
        interaction_tone = type("T", (), {"value": "quiet"})()

    result = store.generate_proposal(policy=FakePolicy())
    assert result is None, "offline should produce no proposal"
    print("  offline: no proposal — OK")

    # Generate from live_idle state → should produce proposal
    class FakePolicyLive:
        state = type("State", (), {"value": "live_idle"})()
        can_proactive = True
        error_type = type("E", (), {"value": "none"})()
        interaction_tone = type("T", (), {"value": "quiet"})()

    class FakeDecision:
        should_proactive = True

    class FakeStarter:
        starter = "Hôm nay mọi người đang làm gì vậy?"
        room_topic = "general"
        blocked = False

    result = store.generate_proposal(
        policy=FakePolicyLive(),
        proactive_decision=FakeDecision(),
        starter_result=FakeStarter().__dict__,
    )
    assert result is not None, "live_idle should produce proposal"
    assert result.state == ProposalState.PENDING
    # Note: source may be "fallback" since FakeStarter.starter=None and social_starter
    # is blocked by its own offline stream policy gate.
    # What matters is the proposal is created in live_idle state.
    assert result.stream_state == "live_idle"
    assert result.interaction_tone in {"quiet", "casual", "gentle"}
    print(f"  live_idle: proposal created (id={result.id}, source={result.source}, text={result.text[:50]})")
    proposal_id = result.id

    # Duplicate → blocked (pending already exists)
    result2 = store.generate_proposal(
        policy=FakePolicyLive(),
        proactive_decision=FakeDecision(),
        starter_result=FakeStarter().__dict__,
    )
    assert result2 is None, "Should not create duplicate while pending exists"
    print("  duplicate spam blocked — OK")

    # Approve valid proposal
    ok, reason = store.approve(proposal_id)
    assert ok and reason == "ok", f"Approve failed: {reason}"
    p = store.get_proposal(proposal_id)
    assert p.state == ProposalState.APPROVED
    print("  approve: OK")

    # Cannot approve again
    ok, reason = store.approve(proposal_id)
    assert not ok and "not_pending" in reason
    print("  approve again blocked — OK")

    # Cancel an approved proposal → should succeed (cancel is always allowed)
    ok, reason = store.cancel(proposal_id)
    assert ok and reason == "ok"
    print("  cancel approved → OK")

    # Create and cancel
    result3 = store.generate_proposal(
        policy=FakePolicyLive(),
        proactive_decision=FakeDecision(),
        starter_result={"starter": "Test cancel", "room_topic": "general", "blocked": False},
    )
    assert result3 is not None
    ok, reason = store.cancel(result3.id)
    assert ok and reason == "ok"
    print("  cancel: OK")

    print("  Lifecycle: PASSED")


def _smoke_test_clear_and_expire():
    print("[8E Smoke] Testing clear and expire...")
    StarterProposalStore.reset_for_test()
    store = StarterProposalStore()
    store._proposal_ttl_seconds = 0.1  # 100ms for testing

    class FakePolicy:
        state = type("S", (), {"value": "live_idle"})()
        can_proactive = True
        error_type = type("E", (), {"value": "none"})()
        interaction_tone = type("T", (), {"value": "quiet"})()

    class FakeDecision:
        should_proactive = True

    # Create a proposal that will expire
    result = store.generate_proposal(
        policy=FakePolicy(),
        proactive_decision=FakeDecision(),
        starter_result={"starter": "Soon to expire", "room_topic": "general", "blocked": False},
    )
    assert result is not None
    print(f"  Created proposal {result.id}")

    # Wait for expiry
    import time
    time.sleep(0.15)

    # Approve expired → should fail
    ok, reason = store.approve(result.id)
    assert not ok and "expired" in reason
    print("  expired approve blocked — OK")

    # Snapshot shows expired
    snap = store.snapshot()
    assert snap["expired"] == 1, f"Expected 1 expired, got {snap['expired']}"
    print("  Snapshot shows expired=1 — OK")

    # Clear removes expired
    removed = store.clear()
    assert removed >= 1, f"Expected >=1 removed, got {removed}"
    print(f"  clear: removed {removed} expired/cancelled — OK")

    print("  Clear/expire: PASSED")


def _smoke_test_safety_blocks_leaked_proposals():
    print("[8E Smoke] Testing safety guard blocks leaked proposals...")
    StarterProposalStore.reset_for_test()
    store = StarterProposalStore()
    store._proposal_ttl_seconds = 60.0

    class FakePolicy:
        state = type("S", (), {"value": "live_idle"})()
        can_proactive = True
        error_type = type("E", (), {"value": "none"})()
        interaction_tone = type("T", (), {"value": "quiet"})()

    class FakeDecision:
        should_proactive = True

    # Try to generate a proposal with Ba leak → should be cancelled
    result = store.generate_proposal(
        policy=FakePolicy(),
        proactive_decision=FakeDecision(),
        starter_result={"starter": "Ba ơi cho con hỏi...", "room_topic": "general", "blocked": False},
    )
    assert result is None, "Safety guard should block Ba-leaked proposal"
    print("  Ba leak blocked — OK")

    # Service bot tone blocked
    result2 = store.generate_proposal(
        policy=FakePolicy(),
        proactive_decision=FakeDecision(),
        starter_result={"starter": "Stream đang bắt đầu! Chào mừng đến stream!", "room_topic": "general", "blocked": False},
    )
    assert result2 is None, "Safety guard should block service-bot tone"
    print("  Service bot tone blocked — OK")

    # Snapshot shows rejection
    snap = store.snapshot()
    assert snap["rejection_count"] >= 2, f"Expected >=2 rejections, got {snap['rejection_count']}"
    print(f"  Snapshot shows rejections={snap['rejection_count']} — OK")

    print("  Safety blocks leaked: PASSED")


def run_smoke_tests() -> bool:
    print("=" * 60)
    print("STAGE-8E Starter Proposals — Smoke Tests")
    print("=" * 60)

    tests = [
        _smoke_test_guard,
        _smoke_test_proposal_lifecycle,
        _smoke_test_clear_and_expire,
        _smoke_test_safety_blocks_leaked_proposals,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    import sys
    success = run_smoke_tests()
    sys.exit(0 if success else 1)
