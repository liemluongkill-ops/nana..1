"""STAGE-8J smoke tests: avatar event-tone mapping polish.

No real VTS, OBS, or game calls. This verifies that richer public chat events
produce renderer-ready directives/plans instead of falling back to neutral.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _policy(*, tone="quiet", energy="low"):
    from nana.runtime.stream_state import (
        AvatarEnergy,
        ErrorType,
        InteractionTone,
        StreamPolicy,
        StreamState,
        ViewerExpectation,
    )

    return StreamPolicy(
        state=StreamState.LIVE_IDLE,
        reason="smoke",
        can_proactive=True,
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=1,
        interaction_tone=getattr(InteractionTone, tone.upper()),
        avatar_energy=getattr(AvatarEnergy, energy.upper()),
        viewer_expectation=ViewerExpectation.NORMAL,
        error_type=ErrorType.NONE,
        can_reply=True,
        can_speak=True,
        can_avatar=True,
    )


def _test_quiet_message_not_neutral():
    print("[8J Smoke] Test 1: quiet public message is relaxed, not neutral...")
    from nana.runtime.avatar_director import AvatarDirector

    directive = AvatarDirector().get_directive(
        policy=_policy(tone="quiet", energy="low"),
        chat_event="message",
        last_expression_age_s=10.0,
    )
    assert directive.expression == "relaxed", directive
    assert directive.body_language == "relaxed", directive
    print("  PASSED")


def _test_emoji_alias_has_hotkey_plan():
    print("[8J Smoke] Test 2: emoji_only alias produces hotkey-ready reaction...")
    from nana.runtime.avatar_director import AvatarDirector
    from nana.runtime.avatar_reactor import AvatarReactionController

    directive = AvatarDirector().get_directive(
        policy=_policy(tone="quiet", energy="low"),
        chat_event="emoji_only",
        last_expression_age_s=10.0,
    )
    assert directive.expression == "happy", directive
    assert "emoji_only->emoji" in directive.reason, directive.reason

    AvatarReactionController.reset_for_test()
    controller = AvatarReactionController(enabled=True, cooldown_seconds=0.0, allow_vts_call=False)
    plan = controller.build_plan(chat_event="emoji_only", directive=directive, dry_run=True)
    assert plan.ok, plan
    assert plan.hotkey, plan
    assert not plan.vts_call, plan
    print("  PASSED")


def _test_starter_alias_is_gentle():
    print("[8J Smoke] Test 3: auto_starter alias uses gentle relaxed pose...")
    from nana.runtime.avatar_director import AvatarDirector

    directive = AvatarDirector().get_directive(
        policy=_policy(tone="quiet", energy="low"),
        chat_event="auto_starter",
        last_expression_age_s=10.0,
    )
    assert directive.expression == "relaxed", directive
    assert directive.body_language == "relaxed", directive
    assert directive.duration_s >= 2.5, directive
    assert "auto_starter->starter" in directive.reason, directive.reason
    print("  PASSED")


def _test_highlight_alias_is_expressive():
    print("[8J Smoke] Test 4: superchat alias maps to expressive highlight...")
    from nana.runtime.avatar_director import AvatarDirector

    directive = AvatarDirector().get_directive(
        policy=_policy(tone="casual", energy="normal"),
        chat_event="superchat",
        last_expression_age_s=10.0,
    )
    assert directive.expression == "excited", directive
    assert directive.body_language == "expressive", directive
    assert "superchat->highlight" in directive.reason, directive.reason
    print("  PASSED")


def _test_vts_preview_stays_dry_run():
    print("[8J Smoke] Test 5: VTS preview for emoji stays dry-run...")
    from nana.runtime.avatar_vts_dispatch import AvatarVTSDispatchGate

    gate = AvatarVTSDispatchGate(enabled=True, cooldown_seconds=0.0)
    result = gate.preview(chat_event="emoji_only")
    assert result.dry_run, result
    assert not result.vts_call, result
    print("  PASSED")


def _test_preview_lines_show_8j_phase():
    print("[8J Smoke] Test 6: preview lines expose STAGE-8J phase...")
    from nana.runtime.avatar_director import avatar_director_preview_lines

    text = "\n".join(avatar_director_preview_lines("emoji_only"))
    assert "STAGE-8J" in text, text
    assert "no VTS call" in text, text
    print("  PASSED")


def run_smoke_tests() -> bool:
    print("=" * 60)
    print("STAGE-8J Avatar Event-tone Mapping — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_quiet_message_not_neutral,
        _test_emoji_alias_has_hotkey_plan,
        _test_starter_alias_is_gentle,
        _test_highlight_alias_is_expressive,
        _test_vts_preview_stays_dry_run,
        _test_preview_lines_show_8j_phase,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as exc:
            print(f"  FAILED: {exc}")
            failed += 1
        except Exception as exc:
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            failed += 1
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_smoke_tests() else 1)
