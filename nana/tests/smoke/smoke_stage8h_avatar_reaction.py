"""STAGE-8H smoke tests: avatar reaction wiring.

No real VTS, OBS, or game calls. This only verifies reaction plans.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _policy(*, can_avatar=True, tone="quiet", energy="low"):
    from nana.runtime.stream_state import (
        AvatarEnergy,
        ErrorType,
        InteractionTone,
        StreamPolicy,
        StreamState,
        ViewerExpectation,
    )

    return StreamPolicy(
        state=StreamState.LIVE_IDLE if can_avatar else StreamState.OFFLINE,
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
        can_speak=False,
        can_avatar=can_avatar,
    )


def _directive(*, can_avatar=True, expression="happy", duration=2.5):
    from nana.runtime.avatar_director import AvatarDirective

    return AvatarDirective(
        can_avatar=can_avatar,
        expression=expression,
        body_language="relaxed",
        duration_s=duration,
        reason="smoke",
    )


def _reset():
    from nana.runtime.avatar_reactor import AvatarReactionController

    AvatarReactionController.reset_for_test()
    return AvatarReactionController(
        enabled=True,
        cooldown_seconds=30.0,
        allow_vts_call=False,
    )


def _test_policy_block():
    print("[8H Smoke] Test 1: directive can_avatar false blocks...")
    controller = _reset()
    plan = controller.build_plan(directive=_directive(can_avatar=False), dry_run=True)
    assert not plan.ok, plan
    assert not plan.vts_call, plan
    print("  PASSED")


def _test_preview_maps_expression():
    print("[8H Smoke] Test 2: preview maps expression to hotkey...")
    controller = _reset()
    plan = controller.build_plan(directive=_directive(expression="happy"), dry_run=True)
    assert plan.ok and plan.action == "would_react", plan
    assert plan.hotkey, plan
    assert not plan.vts_call, plan
    print("  PASSED")


def _test_apply_preview_no_vts_call():
    print("[8H Smoke] Test 3: apply-preview still does not call VTS...")
    controller = _reset()
    plan = controller.build_plan(directive=_directive(expression="excited"), dry_run=False)
    assert plan.ok and plan.action == "planned", plan
    assert plan.reason == "vts_disabled_preview_plan", plan
    assert not plan.vts_call, plan
    snap = controller.snapshot()
    assert snap["stats"]["planned"] == 1, snap
    assert snap["stats"]["vts_call"] == 0, snap
    print("  PASSED")


def _test_cooldown_blocks_second_apply():
    print("[8H Smoke] Test 4: cooldown blocks second apply-preview...")
    controller = _reset()
    first = controller.build_plan(directive=_directive(expression="happy"), dry_run=False)
    assert first.ok, first
    second = controller.build_plan(directive=_directive(expression="excited"), dry_run=False)
    assert not second.ok and second.reason.startswith("cooldown"), second
    print("  PASSED")


def _test_commands_and_status_lines():
    print("[8H Smoke] Test 5: commands/status lines exist...")
    from nana.commands.help import print_command_help
    from nana.runtime.avatar_reactor import (
        avatar_reaction_apply_preview_lines,
        avatar_reaction_preview_lines,
        avatar_reaction_status_lines,
    )

    status = "\n".join(avatar_reaction_status_lines())
    preview = "\n".join(avatar_reaction_preview_lines("message"))
    apply_preview = "\n".join(avatar_reaction_apply_preview_lines("message"))
    assert "/avatar-reaction-status" in status
    assert "VTS call: False" in preview
    assert "VTS call: False" in apply_preview
    assert callable(print_command_help)
    print("  PASSED")


def _test_public_firewall_blocks_reaction_commands():
    print("[8H Smoke] Test 6: public firewall blocks avatar reaction commands...")
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    guard = get_public_stage_identity_guard()
    for command in (
        "/avatar-reaction-status",
        "/avatar-reactor-status",
        "/avatar-reaction-preview message",
        "/avatar-reactor-preview message",
        "/avatar-reaction-apply-preview message",
        "/avatar-reactor-apply-preview message",
    ):
        assert guard.classify_public_input(command) == "backstage_command", command
    print("  PASSED")


def run_smoke_tests() -> bool:
    print("=" * 60)
    print("STAGE-8H Avatar Reaction Wiring — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_policy_block,
        _test_preview_maps_expression,
        _test_apply_preview_no_vts_call,
        _test_cooldown_blocks_second_apply,
        _test_commands_and_status_lines,
        _test_public_firewall_blocks_reaction_commands,
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
