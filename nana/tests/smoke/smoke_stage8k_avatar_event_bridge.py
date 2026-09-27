"""STAGE-8K smoke tests: avatar event metadata bridge.

No real Discord, VTS, OBS, TTS, or game calls. This verifies that public
message metadata can flow into avatar event names consumed by 8J/8H/8I.
"""

from __future__ import annotations

import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reset():
    from nana.runtime.avatar_event_bridge import AvatarEventBridge

    AvatarEventBridge.reset_for_test()


def _test_classifier():
    print("[8K Smoke] Test 1: classifier recognizes message/emoji/sticker/highlight...")
    from nana.runtime.avatar_event_bridge import classify_avatar_event

    assert classify_avatar_event(text="hello") == "message"
    assert classify_avatar_event(text="😀") == "emoji_only"
    assert classify_avatar_event(text="<:GCH_sweat:1249411977659547679>") == "emoji_only"
    assert classify_avatar_event(text="", metadata={"stickers": [{"id": "1"}]}) == "sticker"
    assert classify_avatar_event(text="thanks", event_type="superchat") == "highlight"
    assert classify_avatar_event(text="new member", event_type="member") == "subscription"
    assert classify_avatar_event(text="quiet", event_type="auto_starter", source="starter_proposal") == "auto_starter"
    print("  PASSED")


def _test_manual_observe_and_preview():
    print("[8K Smoke] Test 2: manual observe updates status/preview...")
    from nana.runtime.avatar_event_bridge import get_avatar_event_bridge, avatar_event_preview_lines

    _reset()
    bridge = get_avatar_event_bridge()
    record = bridge.observe_public_event(text="😀", viewer_name="linh", reason="smoke")
    assert record.event == "emoji_only", record
    text = "\n".join(avatar_event_preview_lines())
    assert "/avatar-director-preview emoji_only" in text, text
    assert "no VTS" in text, text
    print("  PASSED")


def _test_external_bridge_records_avatar_event():
    print("[8K Smoke] Test 3: external bridge records avatar_event metadata...")
    from nana.runtime.avatar_event_bridge import AvatarEventBridge, get_avatar_event_bridge
    from nana.runtime.external_bridge import ExternalBridgeRequest, ExternalBridgeRuntime
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.viewer_chat import ViewerChatQueue

    AvatarEventBridge.reset_for_test()
    runtime = ExternalBridgeRuntime(
        enabled=True,
        queue=ViewerChatQueue(rate_limit_max=99),
        social_session=SocialSessionCache(),
    )
    request = ExternalBridgeRequest.from_payload(
        {
            "request_id": "8k-emoji",
            "source": "discord",
            "event_type": "message",
            "text": "<:GCH_sweat:1249411977659547679>",
            "author_name": "linhcute2746",
            "metadata": {"message_id": "8k1", "route": {"chat_channel_name": "chung"}},
        }
    )
    reply = runtime.process_request(request, responder=lambda _request: "Nana thấy rồi nè.")
    avatar_event = reply.get("metadata", {}).get("avatar_event") or {}
    assert avatar_event.get("event") == "emoji_only", avatar_event
    assert get_avatar_event_bridge().last_event().event == "emoji_only"
    print("  PASSED")


def _test_starter_send_records_auto_starter():
    print("[8K Smoke] Test 4: starter send records auto_starter event...")
    from nana.runtime.avatar_event_bridge import AvatarEventBridge, get_avatar_event_bridge
    from nana.runtime.starter_proposals import StarterProposalStore, get_proposal_store
    from nana.runtime.starter_send import StarterSendController
    from nana.runtime.stream_state import get_stream_state

    AvatarEventBridge.reset_for_test()
    StarterProposalStore.reset_for_test()
    store = get_proposal_store()
    stream = get_stream_state()
    stream.force_offline()
    stream.go_live()
    proposal = store.generate_proposal(
        proactive_decision=SimpleNamespace(should_proactive=True),
        starter_result={
            "starter": "Ưm... phòng chat yên quá nhỉ?",
            "room_topic": "general",
            "blocked": False,
        },
    )
    assert proposal is not None
    ok, reason = store.approve(proposal.id)
    assert ok, reason
    controller = StarterSendController(
        enabled=True,
        channel_id="123",
        outbox_dir=Path(tempfile.mkdtemp(prefix="nana-8k-outbox-")),
        cooldown_seconds=0.0,
        quota_max=99,
    )
    result = controller.send_proposal(proposal.id)
    assert result.ok, result
    assert get_avatar_event_bridge().last_event().event == "auto_starter"
    print("  PASSED")


def _test_stage_status_reads_last_event():
    print("[8K Smoke] Test 5: stage-status uses last avatar event...")
    from nana.core.status import print_stage_status
    from nana.runtime.avatar_event_bridge import AvatarEventBridge, get_avatar_event_bridge
    from nana.runtime.stream_state import get_stream_state

    AvatarEventBridge.reset_for_test()
    stream = get_stream_state()
    stream.force_offline()
    stream.go_live()
    get_avatar_event_bridge().observe_public_event(text="😀", viewer_name="linh", reason="smoke")
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    output = buf.getvalue()
    assert "Avatar event:" in output, output
    assert "event=emoji_only" in output, output
    assert "Avatar director:" in output, output
    print("  PASSED")


def _test_public_firewall_blocks_commands():
    print("[8K Smoke] Test 6: public firewall blocks 8K commands...")
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    guard = get_public_stage_identity_guard()
    for command in (
        "/avatar-event-status",
        "/avatar-event-preview",
        "/avatar-event-test 😀",
        "/avatar-events-status",
    ):
        assert guard.classify_public_input(command) == "backstage_command", command
    print("  PASSED")


def run_smoke_tests() -> bool:
    print("=" * 60)
    print("STAGE-8K Avatar Event Metadata Bridge — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_classifier,
        _test_manual_observe_and_preview,
        _test_external_bridge_records_avatar_event,
        _test_starter_send_records_auto_starter,
        _test_stage_status_reads_last_event,
        _test_public_firewall_blocks_commands,
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
