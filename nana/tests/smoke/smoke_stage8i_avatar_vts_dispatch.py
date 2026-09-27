"""STAGE-8I smoke tests: controlled avatar VTS dispatch gate.

Real VTS is never used. The tests inject a fake dispatch function and verify
that only the explicit enabled+ready path may call it.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeVTS:
    def __init__(self, *, ready=True, connected=True):
        self.ready = ready
        self.connected = connected
        self.calls = []


def _plan(*, ok=True, hotkey="星星眼", expression="happy"):
    from nana.runtime.avatar_reactor import AvatarReactionPlan

    return AvatarReactionPlan(
        ok=ok,
        action="planned" if ok else "blocked",
        reason="smoke",
        expression=expression,
        hotkey=hotkey,
        body_language="relaxed",
        duration_s=2.0,
        chat_event="message",
        dry_run=False,
        vts_call=False,
    )


def _gate(*, enabled=False, ready=True, connected=True, cooldown=30.0):
    from nana.runtime.avatar_vts_dispatch import AvatarVTSDispatchGate

    async def fake_dispatch(vts, hotkey, request_id):
        vts.calls.append({"hotkey": hotkey, "request_id": request_id})
        return {"ok": True}

    def fake_snapshot(vts):
        return {
            "ready": ready,
            "connected": connected,
            "auth_status": 2 if ready else None,
            "auth_status_label": "authenticated" if ready else "unknown",
        }

    return AvatarVTSDispatchGate(
        enabled=enabled,
        cooldown_seconds=cooldown,
        dispatch_fn=fake_dispatch,
        snapshot_fn=fake_snapshot,
    )


def _test_preview_never_calls_vts():
    print("[8I Smoke] Test 1: preview never calls VTS...")
    gate = _gate(enabled=True)
    vts = FakeVTS()
    result = gate.preview(plan=_plan())
    assert result.ok and result.action == "would_dispatch", result
    assert not result.vts_call, result
    assert vts.calls == [], vts.calls
    print("  PASSED")


async def _test_disabled_blocks_dispatch():
    print("[8I Smoke] Test 2: disabled gate blocks dispatch...")
    gate = _gate(enabled=False)
    vts = FakeVTS()
    result = await gate.dispatch(vts=vts, plan=_plan())
    assert not result.ok and result.reason == "dispatch_disabled", result
    assert not result.vts_call, result
    assert vts.calls == [], vts.calls
    print("  PASSED")


async def _test_no_hotkey_blocks_dispatch():
    print("[8I Smoke] Test 3: no hotkey blocks dispatch...")
    gate = _gate(enabled=True)
    vts = FakeVTS()
    result = await gate.dispatch(vts=vts, plan=_plan(hotkey="", expression="neutral"))
    assert not result.ok and result.reason == "no_hotkey", result
    assert vts.calls == [], vts.calls
    print("  PASSED")


async def _test_not_ready_blocks_dispatch():
    print("[8I Smoke] Test 4: VTS not ready blocks dispatch...")
    gate = _gate(enabled=True, ready=False, connected=False)
    vts = FakeVTS(ready=False, connected=False)
    result = await gate.dispatch(vts=vts, plan=_plan())
    assert not result.ok and result.reason == "vts_not_connected", result
    assert vts.calls == [], vts.calls
    print("  PASSED")


async def _test_enabled_ready_dispatches_once():
    print("[8I Smoke] Test 5: enabled+ready dispatches one hotkey...")
    gate = _gate(enabled=True, ready=True, connected=True, cooldown=30.0)
    vts = FakeVTS()
    result = await gate.dispatch(vts=vts, plan=_plan(hotkey="星星眼"))
    assert result.ok and result.vts_call, result
    assert result.action == "sent", result
    assert len(vts.calls) == 1, vts.calls
    assert vts.calls[0]["hotkey"] == "星星眼", vts.calls
    second = await gate.dispatch(vts=vts, plan=_plan(hotkey="爱心眼"))
    assert not second.ok and second.reason.startswith("cooldown"), second
    assert len(vts.calls) == 1, vts.calls
    print("  PASSED")


def _test_commands_and_firewall():
    print("[8I Smoke] Test 6: commands/status/firewall exist...")
    from nana.commands.help import print_command_help
    from nana.runtime.avatar_vts_dispatch import (
        avatar_vts_disable_lines,
        avatar_vts_enable_lines,
        avatar_vts_preview_lines,
        avatar_vts_status_lines,
    )
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    assert callable(print_command_help)
    assert "Avatar VTS Dispatch Gate" in "\n".join(avatar_vts_status_lines())
    assert "ENABLED" in "\n".join(avatar_vts_enable_lines())
    assert "DISABLED" in "\n".join(avatar_vts_disable_lines())
    assert "VTS call: False" in "\n".join(avatar_vts_preview_lines("message"))
    guard = get_public_stage_identity_guard()
    for command in (
        "/avatar-vts-status",
        "/avatar-vts-enable",
        "/avatar-vts-disable",
        "/avatar-vts-preview message",
        "/avatar-vts-dispatch message",
        "/avatar-dispatch-status",
    ):
        assert guard.classify_public_input(command) == "backstage_command", command
    print("  PASSED")


def run_smoke_tests() -> bool:
    import asyncio

    print("=" * 60)
    print("STAGE-8I Avatar VTS Dispatch Gate — Smoke Tests")
    print("=" * 60)
    sync_tests = [
        _test_preview_never_calls_vts,
        _test_commands_and_firewall,
    ]
    async_tests = [
        _test_disabled_blocks_dispatch,
        _test_no_hotkey_blocks_dispatch,
        _test_not_ready_blocks_dispatch,
        _test_enabled_ready_dispatches_once,
    ]
    passed = 0
    failed = 0
    for test in sync_tests:
        try:
            test()
            passed += 1
        except AssertionError as exc:
            print(f"  FAILED: {exc}")
            failed += 1
        except Exception as exc:
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            failed += 1
    for test in async_tests:
        try:
            asyncio.run(test())
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
