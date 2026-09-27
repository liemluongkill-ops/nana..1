"""STAGE-8C/8J: Reactive Avatar Director for Nana VTuber.

Reads StreamPolicy (8A) + external context → decides avatar expression.
Does NOT animate — only returns an AvatarDirective.

Inputs:
    - StreamPolicy from 8A
    - chat_event: "none" | "mention" | "message" | "emoji" | "starter" | "highlight" | "subscription"
    - last_expression: str
    - last_expression_age_s: float

Output:
    AvatarDirective:
        - can_avatar: bool
        - expression: str (VTS expression name)
        - body_language: str
        - duration_s: float
        - reason: str

Expression map:
    - casual → happy/excited/normal
    - gentle → soft/relaxed/normal
    - alert → surprised/concerned/awake
    - dormant → neutral/minimal

Note: 8C is the last decision point before the VTS renderer.
If can_avatar=False, expression is skipped regardless of other conditions.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from nana.runtime.stream_state import (
    StreamPolicy,
    StreamState,
    AvatarEnergy,
    InteractionTone,
    get_stream_state,
)


PHASE = "STAGE-8J"


# ─── Avatar Expressions (VTS expression names) ─────────────────────────────────


# Standard VTS expression names used by Nana
VTS_EXPRESSIONS = {
    "neutral": "neutral",
    "happy": "happy",
    "excited": "excited",
    "surprised": "surprised",
    "concerned": "concerned",
    "sleepy": "sleepy",
    "relaxed": "relaxed",
    "minimal": "neutral",
    "normal": "neutral",
}


# ─── Body Language ─────────────────────────────────────────────────────────────


BODY_LANGUAGE = {
    "neutral": "neutral",
    "active": "active",
    "expressive": "expressive",
    "relaxed": "relaxed",
    "minimal": "minimal",
    "dormant": "minimal",
}


# ─── Directive ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AvatarDirective:
    """Immutable avatar directive from 8C."""

    can_avatar: bool          # policy allows avatar
    expression: str           # VTS expression name
    body_language: str        # body language descriptor
    duration_s: float         # how long to hold expression
    reason: str              # why this directive

    def to_dict(self) -> dict:
        return {
            "can_avatar": self.can_avatar,
            "expression": self.expression,
            "body_language": self.body_language,
            "duration_s": self.duration_s,
            "reason": self.reason,
        }


# ─── Expression Selection ───────────────────────────────────────────────────────


# Canonical public chat events. Aliases are normalized before selection so
# later layers can pass platform-specific names without learning this map.
_EVENT_ALIASES = {
    "": "none",
    "chat": "message",
    "chat_message": "message",
    "reply": "message",
    "chat_reply": "message",
    "emoji_only": "emoji",
    "emoji": "emoji",
    "sticker": "emoji",
    "reaction": "emoji",
    "emote": "emoji",
    "starter": "starter",
    "auto_starter": "starter",
    "social_starter": "starter",
    "proposal": "starter",
    "sub": "subscription",
    "subscriber": "subscription",
    "follow": "subscription",
    "follower": "subscription",
    "gift": "subscription",
    "member": "subscription",
    "milestone": "highlight",
    "superchat": "highlight",
    "command": "command",
    "status": "command",
}

_KNOWN_EVENTS = {
    "none",
    "mention",
    "message",
    "emoji",
    "starter",
    "highlight",
    "subscription",
    "command",
}


def _normalize_event(chat_event: str) -> str:
    raw = (chat_event or "none").strip().lower()
    normalized = _EVENT_ALIASES.get(raw, raw)
    if normalized not in _KNOWN_EVENTS:
        return "message"
    return normalized


# Mapping: (interaction_tone, energy, normalized_chat_event) -> expression
_EXPRESSION_MAP = {
    # Casual tone
    (InteractionTone.CASUAL, AvatarEnergy.HIGH): {
        "none": "excited",
        "mention": "excited",
        "message": "happy",
        "emoji": "excited",
        "starter": "happy",
        "highlight": "excited",
        "subscription": "excited",
        "command": "happy",
    },
    (InteractionTone.CASUAL, AvatarEnergy.NORMAL): {
        "none": "happy",
        "mention": "happy",
        "message": "happy",
        "emoji": "excited",
        "starter": "happy",
        "highlight": "excited",
        "subscription": "excited",
        "command": "relaxed",
    },
    (InteractionTone.CASUAL, AvatarEnergy.LOW): {
        "none": "relaxed",
        "mention": "happy",
        "message": "relaxed",
        "emoji": "happy",
        "starter": "relaxed",
        "highlight": "happy",
        "subscription": "happy",
        "command": "relaxed",
    },

    # Focused tone
    (InteractionTone.FOCUSED, AvatarEnergy.NORMAL): {
        "none": "neutral",
        "mention": "neutral",
        "message": "neutral",
        "emoji": "relaxed",
        "starter": "neutral",
        "highlight": "concerned",
        "subscription": "happy",
        "command": "neutral",
    },

    # Quiet tone
    (InteractionTone.QUIET, AvatarEnergy.LOW): {
        "none": "relaxed",
        "mention": "relaxed",
        "message": "relaxed",
        "emoji": "happy",
        "starter": "relaxed",
        "highlight": "happy",
        "subscription": "happy",
        "command": "relaxed",
    },

    # Muted tone
    (InteractionTone.MUTED, AvatarEnergy.DORMANT): {
        "none": "neutral",
        "mention": "neutral",
        "message": "neutral",
        "emoji": "neutral",
        "starter": "neutral",
        "highlight": "neutral",
        "subscription": "neutral",
        "command": "neutral",
    },
}

# Body language per energy level
_BODY_LANGUAGE_MAP = {
    AvatarEnergy.HIGH: "expressive",
    AvatarEnergy.NORMAL: "active",
    AvatarEnergy.LOW: "relaxed",
    AvatarEnergy.DORMANT: "minimal",
}

# Duration per chat event (seconds)
_DURATION_MAP = {
    "none": 3.0,
    "mention": 4.0,
    "message": 2.5,
    "emoji": 2.2,
    "starter": 3.5,
    "highlight": 5.0,
    "subscription": 6.0,
    "command": 1.5,
}

_EVENT_BODY_LANGUAGE_OVERRIDES = {
    "emoji": "active",
    "highlight": "expressive",
    "subscription": "expressive",
    "starter": "relaxed",
    "command": "minimal",
}

# Minimum seconds between expression changes
_MIN_EXPRESSION_INTERVAL_S = 2.0


# ─── Avatar Director ───────────────────────────────────────────────────────────


class AvatarDirector:
    """Singleton reactive avatar director.

    Thread-safe. Stateless aside from expression timing.
    """

    _instance: Optional["AvatarDirector"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_expression: str = "neutral"
        self._last_expression_at: float = 0.0
        self._expression_count: int = 0

    @classmethod
    def get_instance(cls) -> "AvatarDirector":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── Main API ───────────────────────────────────────────────────────

    def get_directive(
        self,
        policy: Optional[StreamPolicy] = None,
        chat_event: str = "none",
        last_expression: Optional[str] = None,
        last_expression_age_s: Optional[float] = None,
    ) -> AvatarDirective:
        """Get avatar directive for current context.

        Args:
            policy: StreamPolicy from 8A. If None, fetches current.
            chat_event: Current chat event type.
                - "none": no recent chat activity
                - "mention": someone @mentioned Nana
                - "message": regular chat message
                - "emoji"/"emoji_only"/"sticker": light reaction
                - "starter"/"auto_starter": Nana-initiated starter
                - "highlight": subscriber/follow highlight
                - "subscription": new subscription
            last_expression: Last expression used (to avoid rapid switching).
            last_expression_age_s: Seconds since last expression change.

        Returns:
            AvatarDirective with can_avatar, expression, body_language, duration_s.
        """
        now = time.time()
        policy = policy or get_stream_state().get_policy()

        if last_expression is None:
            with self._lock:
                last_expression = self._last_expression

        if last_expression_age_s is None:
            with self._lock:
                last_expression_age_s = now - self._last_expression_at if self._last_expression_at else 999.0

        # ── Gate 1: Policy ─────────────────────────────────────────────
        if not policy.can_avatar:
            return AvatarDirective(
                can_avatar=False,
                expression="neutral",
                body_language="minimal",
                duration_s=0.0,
                reason=f"policy.can_avatar=False (state={policy.state.value})",
            )

        # ── Gate 2: Rapid expression switching ─────────────────────────
        if last_expression_age_s < _MIN_EXPRESSION_INTERVAL_S and last_expression != "neutral":
            return AvatarDirective(
                can_avatar=True,
                expression=last_expression,
                body_language="minimal",
                duration_s=_MIN_EXPRESSION_INTERVAL_S - last_expression_age_s,
                reason=f"rapid_switch_guard — keep {last_expression} for {_MIN_EXPRESSION_INTERVAL_S - last_expression_age_s:.1f}s more",
            )

        # ── Gate 3: Energy dormant → minimal ───────────────────────────
        if policy.avatar_energy == AvatarEnergy.DORMANT:
            return AvatarDirective(
                can_avatar=True,
                expression="neutral",
                body_language="minimal",
                duration_s=5.0,
                reason="avatar_energy=dormant — minimal expression",
            )

        # ── Select expression ──────────────────────────────────────────
        normalized_event = _normalize_event(chat_event)
        expression = self._select_expression(policy, normalized_event)
        body_language = self._select_body_language(policy, normalized_event)
        duration = _DURATION_MAP.get(normalized_event, 3.0)

        # Adjust duration based on energy
        if policy.avatar_energy == AvatarEnergy.HIGH:
            duration = min(duration * 1.2, 8.0)
        elif policy.avatar_energy == AvatarEnergy.LOW:
            duration = min(duration * 0.8, 4.0)

        event_note = normalized_event
        if normalized_event != (chat_event or "none").strip().lower():
            event_note = f"{chat_event}->{normalized_event}"

        return AvatarDirective(
            can_avatar=True,
            expression=expression,
            body_language=body_language,
            duration_s=duration,
            reason=f"ok (tone={policy.interaction_tone.value}, energy={policy.avatar_energy.value}, event={event_note})",
        )

    def record_expression(self, expression: str) -> None:
        """Call after expression is applied to renderer."""
        with self._lock:
            self._last_expression = expression
            self._last_expression_at = time.time()
            self._expression_count += 1

    def reset(self) -> None:
        with self._lock:
            self._last_expression = "neutral"
            self._last_expression_at = 0.0
            self._expression_count = 0

    # ── Internal ──────────────────────────────────────────────────────

    def _select_expression(
        self,
        policy: StreamPolicy,
        chat_event: str,
    ) -> str:
        """Select best expression for context."""

        key = (policy.interaction_tone, policy.avatar_energy)
        expr_map = _EXPRESSION_MAP.get(key)

        if expr_map is None:
            # Fallback: use interaction_tone only
            fallback_map = {
                InteractionTone.CASUAL: {"none": "happy", "mention": "happy", "message": "happy", "emoji": "happy", "starter": "happy", "highlight": "excited", "subscription": "excited", "command": "relaxed"},
                InteractionTone.FOCUSED: {"none": "neutral", "mention": "neutral", "message": "neutral", "emoji": "relaxed", "starter": "neutral", "highlight": "concerned", "subscription": "happy", "command": "neutral"},
                InteractionTone.QUIET: {"none": "relaxed", "mention": "relaxed", "message": "relaxed", "emoji": "happy", "starter": "relaxed", "highlight": "happy", "subscription": "happy", "command": "relaxed"},
                InteractionTone.MUTED: {"none": "neutral", "mention": "neutral", "message": "neutral", "emoji": "neutral", "starter": "neutral", "highlight": "neutral", "subscription": "neutral", "command": "neutral"},
            }
            expr_map = fallback_map.get(policy.interaction_tone, {"none": "neutral", "mention": "neutral", "message": "neutral", "emoji": "neutral", "starter": "neutral", "highlight": "neutral", "subscription": "neutral", "command": "neutral"})

        return expr_map.get(chat_event, "neutral")

    def _select_body_language(
        self,
        policy: StreamPolicy,
        chat_event: str,
    ) -> str:
        """Select body language, with event overrides softened by energy."""
        base = _BODY_LANGUAGE_MAP.get(policy.avatar_energy, "neutral")
        override = _EVENT_BODY_LANGUAGE_OVERRIDES.get(chat_event)
        if not override:
            return base
        if policy.avatar_energy == AvatarEnergy.LOW and override == "expressive":
            return "active"
        if policy.avatar_energy == AvatarEnergy.DORMANT:
            return "minimal"
        return override

    # ── Status ────────────────────────────────────────────────────────

    def status(self) -> dict:
        with self._lock:
            now = time.time()
            last_age = now - self._last_expression_at if self._last_expression_at else None
            return {
                "last_expression": self._last_expression,
                "last_expression_age_s": last_age,
                "expression_count": self._expression_count,
            }

    def status_lines(self) -> list[str]:
        s = self.status()
        age = f"{s['last_expression_age_s']:.1f}s" if s['last_expression_age_s'] else "never"
        return [
            f"🎭 Avatar Director ({PHASE})",
            f"  Last expression: {s['last_expression']} ({age} ago)",
            f"  Expressions applied: {s['expression_count']}",
            f"  Expression map: tone+energy+event -> expression, aliases normalized",
            f"  Rapid switch guard: {_MIN_EXPRESSION_INTERVAL_S}s minimum between changes",
            "  Output: AvatarDirective with can_avatar, expression, body_language, duration_s",
        ]


# ─── Module-level Singleton ───────────────────────────────────────────────────


_DIRECTOR: Optional[AvatarDirector] = None
_DIRECTOR_LOCK = threading.Lock()


def get_avatar_director() -> AvatarDirector:
    global _DIRECTOR
    if _DIRECTOR is None:
        with _DIRECTOR_LOCK:
            if _DIRECTOR is None:
                _DIRECTOR = AvatarDirector()
    return _DIRECTOR


def get_avatar_directive(**kwargs) -> AvatarDirective:
    """Convenience wrapper."""
    return get_avatar_director().get_directive(**kwargs)


def avatar_director_status() -> list[str]:
    """Get director status."""
    return get_avatar_director().status_lines()


def avatar_director_preview_lines(chat_event: str = "message") -> list[str]:
    """Dry-run the current stream policy through 8C without touching VTS."""
    policy = get_stream_state().get_policy()
    directive = get_avatar_director().get_directive(
        policy=policy,
        chat_event=chat_event,
    )
    return [
        f"🎭 Avatar Director Preview ({PHASE})",
        f"  Policy state: {policy.state.value} | tone={policy.interaction_tone.value} | energy={policy.avatar_energy.value}",
        f"  Chat event: {chat_event}",
        f"  Can avatar: {directive.can_avatar}",
        f"  Expression candidate: {directive.expression}",
        f"  Body language: {directive.body_language} | duration={directive.duration_s:.1f}s",
        f"  Reason: {directive.reason}",
        "  Safety: preview_only=True | no VTS call | no OBS/game input",
    ]


# ─── Smoke Tests ─────────────────────────────────────────────────────────────


def _smoke_test_policy_gates() -> bool:
    """Verify 8C respects policy gates."""
    print("[8C Smoke] Testing policy gates...")
    director = AvatarDirector()

    from nana.runtime.stream_state import StreamPolicy, StreamState, ErrorType, InteractionTone, AvatarEnergy, ViewerExpectation

    # can_avatar=False
    policy = StreamPolicy(
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
        can_avatar=False,  # policy says no
    )

    directive = director.get_directive(policy=policy)
    assert not directive.can_avatar, "policy.can_avatar=False should block"
    assert directive.expression == "neutral"
    print("  can_avatar=False: correctly blocked")

    # can_avatar=True but energy=DORMANT
    policy2 = StreamPolicy(
        state=StreamState.LIVE_IDLE,
        reason="smoke",
        can_proactive=True,
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=1,
        interaction_tone=InteractionTone.QUIET,
        avatar_energy=AvatarEnergy.DORMANT,  # dormant
        viewer_expectation=ViewerExpectation.MUTED,
        error_type=ErrorType.NONE,
        can_reply=True,
        can_speak=True,
        can_avatar=True,
    )

    directive = director.get_directive(policy=policy2)
    assert directive.can_avatar, "can_avatar=True should be allowed"
    assert directive.expression == "neutral", "DORMANT energy should be neutral"
    assert directive.body_language == "minimal", "DORMANT should be minimal"
    print("  energy=DORMANT: correctly minimal expression")

    print("[8C Smoke] Policy gates: PASSED")
    return True


def _smoke_test_expression_map() -> bool:
    """Verify expression selection maps correctly."""
    print("[8C Smoke] Testing expression map...")

    from nana.runtime.stream_state import StreamPolicy, StreamState, ErrorType, InteractionTone, AvatarEnergy, ViewerExpectation

    director = AvatarDirector()

    # Casual + HIGH energy + subscription
    policy = StreamPolicy(
        state=StreamState.LIVE_ACTIVE,
        reason="smoke",
        can_proactive=True,
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=3,
        interaction_tone=InteractionTone.CASUAL,
        avatar_energy=AvatarEnergy.HIGH,
        viewer_expectation=ViewerExpectation.NORMAL,
        error_type=ErrorType.NONE,
        can_reply=True,
        can_speak=True,
        can_avatar=True,
    )

    cases = [
        ("none", "excited"),
        ("mention", "excited"),
        ("message", "happy"),
        ("highlight", "excited"),
        ("subscription", "excited"),
    ]

    for event, expected in cases:
        d = director.get_directive(policy=policy, chat_event=event)
        assert d.can_avatar, f"casual+HIGH+{event} should allow avatar"
        assert d.expression == expected, f"casual+HIGH+{event} -> {expected}, got {d.expression}"
        assert d.body_language == "expressive", f"HIGH energy should be expressive, got {d.body_language}"
        print(f"  casual+HIGH+{event}: expression={d.expression} ✓")

    print("[8C Smoke] Expression map: PASSED")
    return True


def _smoke_test_rapid_switch_guard() -> bool:
    """Verify rapid expression switching is blocked."""
    print("[8C Smoke] Testing rapid switch guard...")

    from nana.runtime.stream_state import StreamPolicy, StreamState, ErrorType, InteractionTone, AvatarEnergy, ViewerExpectation

    director = AvatarDirector()

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

    # Just changed expression 0.5s ago
    d = director.get_directive(policy=policy, chat_event="highlight", last_expression="happy", last_expression_age_s=0.5)
    assert d.expression == "happy", "Should keep last expression when switching too fast"
    assert "rapid_switch_guard" in d.reason
    print(f"  rapid switch: correctly kept {d.expression} for {d.duration_s:.1f}s more")

    print("[8C Smoke] Rapid switch guard: PASSED")
    return True


def _smoke_test_body_language() -> bool:
    """Verify body language follows energy level."""
    print("[8C Smoke] Testing body language...")

    from nana.runtime.stream_state import StreamPolicy, StreamState, ErrorType, InteractionTone, AvatarEnergy, ViewerExpectation

    director = AvatarDirector()

    for energy, expected_bl in [
        (AvatarEnergy.HIGH, "expressive"),
        (AvatarEnergy.NORMAL, "active"),
        (AvatarEnergy.LOW, "relaxed"),
        (AvatarEnergy.DORMANT, "minimal"),
    ]:
        policy = StreamPolicy(
            state=StreamState.LIVE_ACTIVE,
            reason="smoke",
            can_proactive=True,
            can_auto_send=False,
            can_use_private_memory=False,
            proactive_budget=3,
            interaction_tone=InteractionTone.CASUAL,
            avatar_energy=energy,
            viewer_expectation=ViewerExpectation.NORMAL,
            error_type=ErrorType.NONE,
            can_reply=True,
            can_speak=True,
            can_avatar=True,
        )
        d = director.get_directive(policy=policy, last_expression_age_s=10.0)
        assert d.body_language == expected_bl, f"energy={energy.value} -> body_language={expected_bl}, got {d.body_language}"
        print(f"  energy={energy.value}: body_language={d.body_language} ✓")

    print("[8C Smoke] Body language: PASSED")
    return True


def _smoke_test_event_tone_polish() -> bool:
    """Verify 8J event aliases and quiet-room expression polish."""
    print("[8J Smoke] Testing event-tone polish...")

    from nana.runtime.stream_state import StreamPolicy, StreamState, ErrorType, InteractionTone, AvatarEnergy, ViewerExpectation

    director = AvatarDirector()

    quiet_policy = StreamPolicy(
        state=StreamState.LIVE_IDLE,
        reason="smoke",
        can_proactive=True,
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=1,
        interaction_tone=InteractionTone.QUIET,
        avatar_energy=AvatarEnergy.LOW,
        viewer_expectation=ViewerExpectation.NORMAL,
        error_type=ErrorType.NONE,
        can_reply=True,
        can_speak=True,
        can_avatar=True,
    )

    message = director.get_directive(policy=quiet_policy, chat_event="message", last_expression_age_s=10.0)
    assert message.expression == "relaxed", f"quiet message should be relaxed, got {message.expression}"
    assert message.body_language == "relaxed", message.body_language

    emoji = director.get_directive(policy=quiet_policy, chat_event="emoji_only", last_expression_age_s=10.0)
    assert emoji.expression == "happy", f"emoji_only should normalize to happy, got {emoji.expression}"
    assert emoji.body_language == "active", emoji.body_language
    assert "emoji_only->emoji" in emoji.reason, emoji.reason

    starter = director.get_directive(policy=quiet_policy, chat_event="auto_starter", last_expression_age_s=10.0)
    assert starter.expression == "relaxed", f"auto_starter should normalize to relaxed, got {starter.expression}"
    assert starter.duration_s >= 2.5, starter.duration_s
    assert "auto_starter->starter" in starter.reason, starter.reason

    casual_policy = StreamPolicy(
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
    highlight = director.get_directive(policy=casual_policy, chat_event="superchat", last_expression_age_s=10.0)
    assert highlight.expression == "excited", f"superchat should normalize to highlight/excited, got {highlight.expression}"
    assert "superchat->highlight" in highlight.reason, highlight.reason

    print("[8J Smoke] Event-tone polish: PASSED")
    return True


def run_smoke_tests() -> bool:
    """Run all 8C smoke tests."""
    print("=" * 60)
    print("STAGE-8C/8J Avatar Director — Smoke Tests")
    print("=" * 60)

    tests = [
        _smoke_test_policy_gates,
        _smoke_test_expression_map,
        _smoke_test_rapid_switch_guard,
        _smoke_test_body_language,
        _smoke_test_event_tone_polish,
    ]

    passed = 0
    failed = 0

    for test in tests:
        # Isolate instances
        global _DIRECTOR
        _DIRECTOR = None
        AvatarDirector._instance = None

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
