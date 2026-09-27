"""STAGE-8L smoke tests: live avatar hook.

No real VTS, OBS, TTS, Discord, or game calls. This only verifies that latest
public avatar-event metadata can become a reaction plan through existing gates.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reset():
    from nana.runtime.avatar_event_bridge import AvatarEventBridge
    from nana.runtime.avatar_live_hook import AvatarLiveHook
    from nana.runtime.avatar_reactor import AvatarReactionController
    from nana.runtime.stream_state import get_stream_state

    AvatarEventBridge.reset_for_test()
    AvatarLiveHook.reset_for_test()
    AvatarReactionController.reset_for_test()
    stream = get_stream_state()
    stream.force_offline()
    stream.go_live()


def _observe_emoji():
    from nana.runtime.avatar_event_bridge import get_avatar_event_bridge

    return get_avatar_event_bridge().observe_public_event(
        text="😀",
        event_type="message",
        source="discord",
        viewer_name="linhcute2746",
        request_id=f"smoke-{time.time()}",
        reason="smoke",
    )


def _test_preview_latest_event_no_action():
    print("[8L Smoke] Test 1: preview latest event without action...")
    from nana.runtime.avatar_live_hook import get_avatar_live_hook

    _reset()
    _observe_emoji()
    result = get_avatar_live_hook().evaluate_latest(dry_run=True)
    assert result.ok, result
    assert result.event == "emoji_only", result
    assert result.hotkey, result
    assert not result.vts_call, result
    snap = get_avatar_live_hook().snapshot()
    assert snap["stats"]["preview"] == 1, snap
    assert snap["stats"]["planned"] == 0, snap
    print("  PASSED")


def _test_plan_latest_event_records_no_vts():
    print("[8L Smoke] Test 2: plan latest event records, still no VTS...")
    from nana.runtime.avatar_live_hook import get_avatar_live_hook

    _reset()
    _observe_emoji()
    result = get_avatar_live_hook().evaluate_latest(dry_run=False)
    assert result.ok and result.action == "planned", result
    assert result.reason == "reaction_plan_recorded_no_vts", result
    assert not result.vts_call, result
    snap = get_avatar_live_hook().snapshot()
    assert snap["stats"]["planned"] == 1, snap
    print("  PASSED")


def _test_duplicate_plan_blocked():
    print("[8L Smoke] Test 3: duplicate non-dry-run plan is blocked...")
    from nana.runtime.avatar_live_hook import get_avatar_live_hook

    _reset()
    _observe_emoji()
    hook = get_avatar_live_hook()
    first = hook.evaluate_latest(dry_run=False)
    second = hook.evaluate_latest(dry_run=False)
    assert first.ok, first
    assert not second.ok and second.reason == "duplicate_event", second
    print("  PASSED")


def _test_stale_event_blocked():
    print("[8L Smoke] Test 4: stale event is blocked...")
    from nana.runtime.avatar_event_bridge import AvatarEventRecord, EVENT_EMOJI_ONLY, get_avatar_event_bridge
    from nana.runtime.avatar_live_hook import get_avatar_live_hook

    _reset()
    bridge = get_avatar_event_bridge()
    with bridge._lock:
        bridge._last_event = AvatarEventRecord(
            event=EVENT_EMOJI_ONLY,
            source="discord",
            viewer_name="linh",
            created_at=time.time() - 999.0,
            reason="smoke_stale",
        )
    result = get_avatar_live_hook().evaluate_latest(dry_run=True)
    assert not result.ok and result.reason.startswith("stale_event"), result
    print("  PASSED")


def _test_external_bridge_auto_plans_hook():
    print("[8L Smoke] Test 5: external bridge auto-plans hook for accepted public event...")
    from nana.runtime.avatar_event_bridge import AvatarEventBridge
    from nana.runtime.avatar_live_hook import AvatarLiveHook, get_avatar_live_hook
    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.viewer_chat import ViewerChatQueue

    _reset()
    AvatarEventBridge.reset_for_test()
    AvatarLiveHook.reset_for_test()
    runtime = ExternalBridgeRuntime(
        enabled=True,
        queue=ViewerChatQueue(rate_limit_max=99),
        social_session=SocialSessionCache(),
    )
    request = ExternalBridgeRequest.from_payload(
        {
            "request_id": "8l-emoji",
            "source": "discord",
            "event_type": "message",
            "text": "<:GCH_sweat:1249411977659547679>",
            "author_name": "linhcute2746",
            "metadata": {"message_id": "8l1", "route": {"chat_channel_name": "chung"}},
        }
    )
    reply = runtime.process_request(request, responder=lambda _request: "Nana thấy rồi nè.")
    assert reply["metadata"]["avatar_event"]["event"] == "emoji_only", reply
    snap = get_avatar_live_hook().snapshot()
    last = snap["last_result"]
    assert snap["stats"]["planned"] == 1, snap
    assert last["event"] == "emoji_only", last
    assert last["vts_call"] is False, last
    print("  PASSED")


def _test_commands_and_firewall():
    print("[8L Smoke] Test 6: commands/status/firewall exist...")
    from nana.commands.help import print_command_help
    from nana.runtime.avatar_live_hook import (
        avatar_live_hook_plan_lines,
        avatar_live_hook_preview_lines,
        avatar_live_hook_status_lines,
    )
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    _reset()
    _observe_emoji()
    status = "\n".join(avatar_live_hook_status_lines())
    preview = "\n".join(avatar_live_hook_preview_lines())
    plan = "\n".join(avatar_live_hook_plan_lines())
    assert "/avatar-live-hook-status" in status, status
    assert "VTS call: False" in preview, preview
    assert "VTS call: False" in plan, plan
    assert callable(print_command_help)

    guard = get_public_stage_identity_guard()
    for command in (
        "/avatar-live-hook-status",
        "/avatar-hook-status",
        "/avatar-live-hook-preview",
        "/avatar-hook-preview",
        "/avatar-live-hook-plan",
        "/avatar-hook-plan",
    ):
        assert guard.classify_public_input(command) == "backstage_command", command
    print("  PASSED")


def run_smoke_tests() -> bool:
    print("=" * 60)
    print("STAGE-8L Avatar Live Hook — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_preview_latest_event_no_action,
        _test_plan_latest_event_records_no_vts,
        _test_duplicate_plan_blocked,
        _test_stale_event_blocked,
        _test_external_bridge_auto_plans_hook,
        _test_commands_and_firewall,
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
