"""STAGE-8B: Proactive Conversation Manager for Nana VTuber.

Reads StreamPolicy (8A) → decides when Nana can initiate conversation.
Does NOT send or act — only returns a ProactiveDecision.

Inputs:
    - StreamPolicy from 8A
    - chat_velocity (real-time)
    - last_proactive_age_s
    - recent_topics (to avoid repetition)

Output:
    ProactiveDecision:
        - can_proactive: bool
        - should_proactive: bool
        - reason: str
        - urgency: "low" | "medium" | "high"
        - suggested_tone: str
        - cooldown_until_s: float (if rejected)

Signals to consume:
    - policy.can_proactive, policy.can_auto_send
    - policy.proactive_budget, policy.interaction_tone
    - chat_velocity
    - last_proactive_age_s

Note: 8B only decides "should Nana speak unprompted right now?"
The actual content (what she says) comes from the autonomy loop.
The actual delivery comes from the chat surface.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from nana.runtime.stream_state import (
    StreamPolicy,
    StreamState,
    InteractionTone,
    get_stream_state,
)


# ─── Decision ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProactiveDecision:
    """Immutable decision from 8B."""

    can_proactive: bool       # policy allows it
    should_proactive: bool    # 8B says yes
    reason: str              # why

    urgency: str             # "low" | "medium" | "high"
    suggested_tone: str       # "casual" | "gentle" | "alert"
    cooldown_until_s: float   # if rejected, earliest next attempt

    def to_dict(self) -> dict:
        return {
            "can_proactive": self.can_proactive,
            "should_proactive": self.should_proactive,
            "reason": self.reason,
            "urgency": self.urgency,
            "suggested_tone": self.suggested_tone,
            "cooldown_until_s": self.cooldown_until_s,
        }


# ─── Budget Tracker ────────────────────────────────────────────────────────────


@dataclass
class ProactiveBudget:
    """Tracks proactive messages in the current window."""

    window_seconds: float = 60.0
    recent_times: list[float] = field(default_factory=list)

    def count_in_window(self, now: float) -> int:
        cutoff = now - self.window_seconds
        self.recent_times = [t for t in self.recent_times if t > cutoff]
        return len(self.recent_times)

    def record(self, now: float) -> None:
        self.recent_times.append(now)

    def remaining(self, now: float, budget: int) -> int:
        return max(0, budget - self.count_in_window(now))

    def cooldown_seconds(self, now: float, budget: int) -> float:
        rem = self.remaining(now, budget)
        if rem > 0:
            return 0.0
        if not self.recent_times:
            return 0.0
        oldest_in_window = min(self.recent_times)
        elapsed = now - oldest_in_window
        return max(0.0, self.window_seconds - elapsed)


# ─── Cooldown Windows ─────────────────────────────────────────────────────────


# Minimum seconds between proactive attempts (per tone)
_COOLDOWN_PER_TONE = {
    "casual": 45.0,
    "gentle": 60.0,
    "alert": 30.0,
}

# Minimum seconds since last Nana chat (any source)
_MIN_GAP_BETWEEN_CHAT_S = 20.0

# Chat velocity at which 8B goes quiet even if policy allows
_CHAT_FLOWING_QUIET_THRESHOLD = 8.0  # msgs/min — chat is busy, don't interrupt


# ─── Decision Engine ──────────────────────────────────────────────────────────


class ProactiveEngine:
    """Singleton proactive decision engine.

    Thread-safe. Stateless aside from budget tracking.
    """

    _instance: Optional["ProactiveEngine"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._budget = ProactiveBudget()
        self._last_decision_at: float = 0.0
        self._last_proactive_at: float = 0.0
        self._last_chat_at: float = 0.0
        self._recent_topics: list[str] = []
        self._decision_log: list[dict] = []

    @classmethod
    def get_instance(cls) -> "ProactiveEngine":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── Main API ─────────────────────────────────────────────────────────

    def should_proactive(
        self,
        policy: Optional[StreamPolicy] = None,
        chat_velocity: float = 0.0,
        last_proactive_age_s: Optional[float] = None,
        recent_topics: Optional[list[str]] = None,
        last_nana_chat_age_s: Optional[float] = None,
    ) -> ProactiveDecision:
        """Evaluate whether Nana should proactively speak now.

        Args:
            policy: StreamPolicy from 8A. If None, fetches current.
            chat_velocity: Current messages-per-minute in chat.
            last_proactive_age_s: Seconds since last proactive Nana message.
            recent_topics: Recent topics to avoid repetition.
            last_nana_chat_age_s: Seconds since last Nana chat (any source).

        Returns:
            ProactiveDecision with can_proactive, should_proactive, reason, etc.
        """
        now = time.time()
        policy = policy or get_stream_state().get_policy()

        if last_proactive_age_s is None:
            last_proactive_age_s = now - self._last_proactive_at if self._last_proactive_at else 999.0

        if last_nana_chat_age_s is None:
            last_nana_chat_age_s = now - self._last_chat_at if self._last_chat_at else 999.0

        topics = recent_topics or list(self._recent_topics)

        # ── Step 1: Policy gate ─────────────────────────────────────────
        if not policy.can_proactive:
            return self._reject(
                code="policy_forbids",
                detail=f"policy.state={policy.state.value}, can_proactive=False",
                urgency="low",
                can_proactive=False,
                now=now,
            )

        # ── Step 2: Interaction tone gate ──────────────────────────────
        if policy.interaction_tone == InteractionTone.MUTED:
            return self._reject(
                code="tone_muted",
                detail="interaction_tone=muted (owner in flow)",
                urgency="low",
                can_proactive=True,
                now=now,
            )

        if policy.interaction_tone == InteractionTone.FOCUSED:
            # Only very high urgency can break focused mode
            return self._reject(
                code="tone_focused",
                detail="interaction_tone=focused — only reply to direct address",
                urgency="low",
                can_proactive=True,
                now=now,
            )

        # ── Step 3: Budget gate ───────────────────────────────────────
        remaining = self._budget.remaining(now, policy.proactive_budget)
        if remaining <= 0:
            cooldown = self._budget.cooldown_seconds(now, policy.proactive_budget)
            return self._reject(
                code="budget_exhausted",
                detail=f"proactive_budget={policy.proactive_budget}/60s exhausted",
                urgency="low",
                cooldown_until_s=now + cooldown,
                can_proactive=True,
                now=now,
            )

        # ── Step 4: Chat flow gate ────────────────────────────────────
        if chat_velocity >= _CHAT_FLOWING_QUIET_THRESHOLD:
            return self._reject(
                code="chat_flowing",
                detail=f"chat_velocity={chat_velocity:.1f} msg/min — chat busy, don't interrupt",
                urgency="low",
                can_proactive=True,
                now=now,
            )

        # ── Step 5: Gap gate ──────────────────────────────────────────
        if last_nana_chat_age_s < _MIN_GAP_BETWEEN_CHAT_S:
            return self._reject(
                code="too_recent_chat",
                detail=f"last_nana_chat {last_nana_chat_age_s:.0f}s ago — need gap",
                urgency="low",
                cooldown_until_s=now + (_MIN_GAP_BETWEEN_CHAT_S - last_nana_chat_age_s),
                can_proactive=True,
                now=now,
            )

        # ── Step 6: Topic repetition gate ──────────────────────────────
        # TODO(8B): integrate with social_session to check recent topics
        # For now, 8B is permissive unless topic is literally the same
        # (handled at content selection layer, not here)

        # ── Step 7: Urgency evaluation ────────────────────────────────
        urgency, tone = self._evaluate_urgency(
            policy=policy,
            chat_velocity=chat_velocity,
            last_proactive_age_s=last_proactive_age_s,
            last_nana_chat_age_s=last_nana_chat_age_s,
            topics=topics,
        )

        # ── Step 8: Proactive! ───────────────────────────────────────
        return ProactiveDecision(
            can_proactive=True,
            should_proactive=True,
            reason="ok",
            urgency=urgency,
            suggested_tone=tone,
            cooldown_until_s=0.0,
        )

    def record_proactive(self, topic: Optional[str] = None) -> None:
        """Call after Nana successfully sends a proactive message."""
        now = time.time()
        with self._lock:
            self._last_proactive_at = now
            self._budget.record(now)
            self._last_chat_at = now
            if topic:
                self._recent_topics.append(topic)
                if len(self._recent_topics) > 10:
                    self._recent_topics = self._recent_topics[-10:]

    def record_chat(self) -> None:
        """Call whenever Nana chats (reply or proactive)."""
        with self._lock:
            self._last_chat_at = time.time()

    def reset(self) -> None:
        """Reset budget and history."""
        with self._lock:
            self._budget = ProactiveBudget()
            self._last_proactive_at = 0.0
            self._last_chat_at = 0.0
            self._recent_topics = []
            self._decision_log = []

    # ── Internal ────────────────────────────────────────────────────────

    def _reject(
        self,
        code: str,
        detail: str,
        urgency: str,
        can_proactive: bool,
        cooldown_until_s: float = 0.0,
        now: Optional[float] = None,
    ) -> ProactiveDecision:
        return ProactiveDecision(
            can_proactive=can_proactive,
            should_proactive=False,
            reason=detail,
            urgency=urgency,
            suggested_tone="gentle",
            cooldown_until_s=cooldown_until_s,
        )

    def _evaluate_urgency(
        self,
        policy: StreamPolicy,
        chat_velocity: float,
        last_proactive_age_s: float,
        last_nana_chat_age_s: float,
        topics: list[str],
    ) -> tuple[str, str]:
        """Determine urgency level and suggested tone."""

        tone = "gentle"
        urgency = "low"

        state = policy.state

        # ── State-based urgency ─────────────────────────────────────
        if state == StreamState.LIVE_ACTIVE:
            if chat_velocity >= 5.0:
                urgency = "high"
                tone = "casual"
            elif chat_velocity >= 3.0:
                urgency = "medium"
                tone = "casual"
            else:
                urgency = "low"
                tone = "gentle"

        elif state == StreamState.LIVE_IDLE:
            # Chat quiet — Nana can gently prompt
            if last_proactive_age_s >= 180.0:
                urgency = "medium"
                tone = "gentle"
            else:
                urgency = "low"
                tone = "gentle"

        elif state == StreamState.INTERMISSION:
            # Keep atmosphere — light only
            urgency = "low"
            tone = "gentle"

        elif state == StreamState.POST_STREAM:
            # Wrap-up only — urgency depends on how long since stream ended
            urgency = "low"
            tone = "gentle"

        # ── Interaction tone refinement ───────────────────────────────
        if policy.interaction_tone == InteractionTone.CASUAL:
            tone = "casual"

        return urgency, tone

    # ── Status ──────────────────────────────────────────────────────────

    def status(self) -> dict:
        with self._lock:
            now = time.time()
            budget_count = self._budget.count_in_window(now)
            return {
                "last_proactive_at": self._last_proactive_at,
                "last_proactive_age_s": now - self._last_proactive_at if self._last_proactive_at else None,
                "last_chat_at": self._last_chat_at,
                "last_chat_age_s": now - self._last_chat_at if self._last_chat_at else None,
                "budget_count_in_window": budget_count,
                "recent_topics_count": len(self._recent_topics),
                "recent_topics": list(self._recent_topics[-5:]),
                "decision_log_size": len(self._decision_log),
            }

    def status_lines(self) -> list[str]:
        s = self.status()
        proactive_age = f"{s['last_proactive_age_s']:.0f}s" if s['last_proactive_age_s'] else "never"
        chat_age = f"{s['last_chat_age_s']:.0f}s" if s['last_chat_age_s'] else "never"
        return [
            "💬 Proactive Engine (STAGE-8B)",
            f"  Last proactive: {proactive_age} ago",
            f"  Last chat: {chat_age} ago",
            f"  Budget in window: {s['budget_count_in_window']}/N per 60s",
            f"  Recent topics: {s['recent_topics'] or 'none'}",
            "  Gates: policy.can_proactive | tone!=muted | budget>0 | gap>=20s | chat_not_flowing",
            "  Output: should_proactive(True/False) + urgency + suggested_tone",
        ]


# ─── Module-level Singleton ───────────────────────────────────────────────────


_ENGINE: Optional[ProactiveEngine] = None
_ENGINE_LOCK = threading.Lock()


def get_proactive_engine() -> ProactiveEngine:
    global _ENGINE
    if _ENGINE is None:
        with _ENGINE_LOCK:
            if _ENGINE is None:
                _ENGINE = ProactiveEngine()
    return _ENGINE


def should_proactive(**kwargs) -> ProactiveDecision:
    """Convenience wrapper."""
    return get_proactive_engine().should_proactive(**kwargs)


def proactive_decision_summary() -> list[str]:
    """Get engine status."""
    return get_proactive_engine().status_lines()


def proactive_preview_lines() -> list[str]:
    """Dry-run the current stream policy through 8B without sending anything."""
    policy = get_stream_state().get_policy()
    decision = get_proactive_engine().should_proactive(
        policy=policy,
        chat_velocity=0.0,
        last_proactive_age_s=999.0,
        last_nana_chat_age_s=999.0,
    )
    return [
        "💬 Proactive Preview (STAGE-8B)",
        f"  Policy state: {policy.state.value} | tone={policy.interaction_tone.value} | budget={policy.proactive_budget}",
        f"  Can proactive: {decision.can_proactive}",
        f"  Should proactive: {decision.should_proactive}",
        f"  Reason: {decision.reason}",
        f"  Urgency: {decision.urgency} | suggested_tone={decision.suggested_tone}",
        "  Safety: preview_only=True | no chat send | no TTS/VTS/OBS/game input",
    ]


# ─── Smoke Tests ─────────────────────────────────────────────────────────────


def _smoke_test_policy_gates() -> bool:
    """Verify 8B respects policy gates."""
    print("[8B Smoke] Testing policy gates...")
    engine = ProactiveEngine()

    # Fake policy: can_proactive=False
    from nana.runtime.stream_state import StreamPolicy, StreamState, ErrorType, InteractionTone, AvatarEnergy, ViewerExpectation

    offline_policy = StreamPolicy(
        state=StreamState.OFFLINE,
        reason="smoke",
        can_proactive=False,
        can_auto_send=False,
        can_use_private_memory=True,
        proactive_budget=0,
        interaction_tone=InteractionTone.MUTED,
        avatar_energy=AvatarEnergy.DORMANT,
        viewer_expectation=ViewerExpectation.MUTED,
        error_type=ErrorType.NONE,
        can_reply=False,
        can_speak=False,
        can_avatar=False,
    )

    decision = engine.should_proactive(policy=offline_policy)
    assert not decision.should_proactive, "Offline should not proactive"
    assert not decision.can_proactive, "Decision must mirror policy.can_proactive=False"
    print("  offline (can_proactive=False): correctly rejected")

    # Can proactive but muted
    muted_policy = StreamPolicy(
        state=StreamState.LIVE_ACTIVE,
        reason="smoke",
        can_proactive=True,
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=3,
        interaction_tone=InteractionTone.MUTED,
        avatar_energy=AvatarEnergy.NORMAL,
        viewer_expectation=ViewerExpectation.NORMAL,
        error_type=ErrorType.NONE,
        can_reply=True,
        can_speak=True,
        can_avatar=True,
    )

    decision = engine.should_proactive(policy=muted_policy)
    assert not decision.should_proactive, "Muted tone should not proactive"
    print("  live_active (tone=muted): correctly rejected")

    print("[8B Smoke] Policy gates: PASSED")
    return True


def _smoke_test_budget_tracking() -> bool:
    """Verify budget enforcement."""
    print("[8B Smoke] Testing budget tracking...")
    engine = ProactiveEngine()

    from nana.runtime.stream_state import StreamPolicy, StreamState, ErrorType, InteractionTone, AvatarEnergy, ViewerExpectation

    policy = StreamPolicy(
        state=StreamState.LIVE_ACTIVE,
        reason="smoke",
        can_proactive=True,
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=2,
        interaction_tone=InteractionTone.CASUAL,
        avatar_energy=AvatarEnergy.NORMAL,
        viewer_expectation=ViewerExpectation.NORMAL,
        error_type=ErrorType.NONE,
        can_reply=True,
        can_speak=True,
        can_avatar=True,
    )

    # First two should succeed
    for i in range(2):
        decision = engine.should_proactive(policy=policy, last_proactive_age_s=999.0, last_nana_chat_age_s=999.0)
        assert decision.should_proactive, f"Attempt {i+1} should be allowed (budget remaining)"
        engine.record_proactive()

    # Third should be rejected (budget exhausted)
    decision = engine.should_proactive(policy=policy, last_proactive_age_s=999.0, last_nana_chat_age_s=999.0)
    assert not decision.should_proactive, f"Budget exhausted — should reject"
    assert "budget_exhausted" in decision.reason or "exhausted" in decision.reason
    print("  Budget enforcement: correctly rejects when exhausted")

    print("[8B Smoke] Budget tracking: PASSED")
    return True


def _smoke_test_chat_gap() -> bool:
    """Verify gap enforcement between Nana chats."""
    print("[8B Smoke] Testing chat gap...")
    engine = ProactiveEngine()

    from nana.runtime.stream_state import StreamPolicy, StreamState, ErrorType, InteractionTone, AvatarEnergy, ViewerExpectation

    policy = StreamPolicy(
        state=StreamState.LIVE_IDLE,
        reason="smoke",
        can_proactive=True,
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=5,
        interaction_tone=InteractionTone.QUIET,
        avatar_energy=AvatarEnergy.LOW,
        viewer_expectation=ViewerExpectation.MUTED,
        error_type=ErrorType.NONE,
        can_reply=True,
        can_speak=True,
        can_avatar=True,
    )

    # Chat just happened (5s ago) — should reject
    decision = engine.should_proactive(policy=policy, last_proactive_age_s=999.0, last_nana_chat_age_s=5.0)
    assert not decision.should_proactive, "Chat too recent — should reject"
    assert "too_recent" in decision.reason or "gap" in decision.reason
    print("  Gap gate: correctly rejects when chat too recent")

    print("[8B Smoke] Chat gap: PASSED")
    return True


def _smoke_test_live_active_approval() -> bool:
    """Verify 8B approves proactive in live_active with good conditions."""
    print("[8B Smoke] Testing live_active approval...")
    engine = ProactiveEngine()

    from nana.runtime.stream_state import StreamPolicy, StreamState, ErrorType, InteractionTone, AvatarEnergy, ViewerExpectation

    policy = StreamPolicy(
        state=StreamState.LIVE_ACTIVE,
        reason="smoke",
        can_proactive=True,
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=3,
        interaction_tone=InteractionTone.CASUAL,
        avatar_energy=AvatarEnergy.NORMAL,
        viewer_expectation=ViewerExpectation.NORMAL,
        error_type=ErrorType.NONE,
        can_reply=True,
        can_speak=True,
        can_avatar=True,
    )

    decision = engine.should_proactive(
        policy=policy,
        chat_velocity=4.0,  # moderate, not overwhelming
        last_proactive_age_s=120.0,
        last_nana_chat_age_s=60.0,
    )
    assert decision.should_proactive, f"live_active with good conditions should approve: {decision.reason}"
    assert decision.urgency in {"low", "medium", "high"}
    assert decision.suggested_tone in {"casual", "gentle", "alert"}
    print(f"  live_active approval: OK (urgency={decision.urgency}, tone={decision.suggested_tone})")

    print("[8B Smoke] Live active approval: PASSED")
    return True


def run_smoke_tests() -> bool:
    """Run all 8B smoke tests."""
    print("=" * 60)
    print("STAGE-8B Proactive Engine — Smoke Tests")
    print("=" * 60)

    # Fresh instance per test
    tests = [
        _smoke_test_policy_gates,
        _smoke_test_budget_tracking,
        _smoke_test_chat_gap,
        _smoke_test_live_active_approval,
    ]

    passed = 0
    failed = 0

    for test in tests:
        # Isolate instances
        global _ENGINE
        _ENGINE = None
        ProactiveEngine._instance = None

        try:
            if test():
                passed += 1
            else:
                failed += 1
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
