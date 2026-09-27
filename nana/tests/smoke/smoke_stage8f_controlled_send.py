"""STAGE-8F Smoke Tests: Controlled Starter Send.

Verifies that approved proposals can be sent only through an explicit kill-switched outbox.
No live Discord connection, no TTS/VTS/OBS/game input.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fake_policy(state="live_idle", can_proactive=True, error_type="none", interaction_tone="quiet",
                 can_reply=True, can_speak=True, can_avatar=True, can_auto_send=False,
                 can_use_private_memory=False):
    from nana.runtime.stream_state import (
        StreamPolicy, StreamState, ErrorType, InteractionTone, AvatarEnergy, ViewerExpectation,
    )

    state_map = {
        "offline": StreamState.OFFLINE,
        "live_idle": StreamState.LIVE_IDLE,
        "live_active": StreamState.LIVE_ACTIVE,
        "intermission": StreamState.INTERMISSION,
        "post_stream": StreamState.POST_STREAM,
        "error_safe": StreamState.ERROR_SAFE,
    }
    error_map = {
        "none": ErrorType.NONE,
        "bridge_down": ErrorType.BRIDGE_DOWN,
        "voice_error": ErrorType.VOICE_ERROR,
        "vts_disconnect": ErrorType.VTS_DISCONNECT,
    }
    tone_map = {
        "muted": InteractionTone.MUTED,
        "quiet": InteractionTone.QUIET,
        "casual": InteractionTone.CASUAL,
        "focused": InteractionTone.FOCUSED,
    }
    return StreamPolicy(
        state=state_map[state],
        reason="smoke",
        can_proactive=can_proactive,
        can_auto_send=can_auto_send,
        can_use_private_memory=can_use_private_memory,
        proactive_budget=1,
        interaction_tone=tone_map[interaction_tone],
        avatar_energy=AvatarEnergy.LOW,
        viewer_expectation=ViewerExpectation.NORMAL,
        error_type=error_map[error_type],
        can_reply=can_reply,
        can_speak=can_speak,
        can_avatar=can_avatar,
    )


def _make_approved_proposal(text="Hôm nay mọi người đang làm gì vậy?", *, ttl_seconds=300.0):
    from nana.runtime.starter_proposals import StarterProposalStore, get_proposal_store

    StarterProposalStore.reset_for_test()
    store = get_proposal_store()
    store._proposal_ttl_seconds = ttl_seconds

    class FakeDecision:
        should_proactive = True

    result = store.generate_proposal(
        policy=_fake_policy(),
        proactive_decision=FakeDecision(),
        starter_result={"starter": text, "room_topic": "general", "blocked": False},
    )
    assert result is not None, "proposal must exist"
    ok, reason = store.approve(result.id)
    assert ok, reason
    return store, result


def _reset_controller(*, outbox_dir: Path, channel_id: str = "1234567890", enabled: bool = False):
    from nana.runtime.starter_send import StarterSendController

    StarterSendController.reset_for_test()
    controller = StarterSendController(
        outbox_dir=outbox_dir,
        channel_id=channel_id,
        enabled=enabled,
        cooldown_seconds=60.0,
        quota_window_seconds=600.0,
        quota_max=2,
    )
    controller._policy_ok = lambda: (True, "ok", _fake_policy())  # type: ignore[method-assign]
    StarterSendController._instance = controller  # type: ignore[attr-defined]
    return controller


def _test_disabled_blocks_send():
    print("[8F Smoke] Test 1: disabled send blocks outbox write...")
    from nana.runtime.starter_send import get_starter_send_controller

    with tempfile.TemporaryDirectory() as tmp:
        outbox_dir = Path(tmp)
        _reset_controller(outbox_dir=outbox_dir, enabled=False)
        store, proposal = _make_approved_proposal()
        result = get_starter_send_controller().send_proposal(proposal.id, dry_run=False)
        assert not result.ok and result.reason == "send_disabled", result
        assert not any(outbox_dir.glob("*.json")), "outbox should stay empty"
        current = store.get_proposal(proposal.id)
        assert current.state == "approved", current.state
    print("  PASSED")


def _test_enabled_send_writes_outbox_and_marks_sent():
    print("[8F Smoke] Test 2: enabled send writes outbox and marks sent...")
    from nana.runtime.starter_send import get_starter_send_controller
    from nana.runtime.starter_proposals import ProposalState

    with tempfile.TemporaryDirectory() as tmp:
        outbox_dir = Path(tmp)
        _reset_controller(outbox_dir=outbox_dir, enabled=True)
        store, proposal = _make_approved_proposal()
        controller = get_starter_send_controller()
        result = controller.send_proposal(proposal.id, dry_run=False)
        assert result.ok, result.reason
        files = list(outbox_dir.glob("*.json"))
        assert len(files) == 1, files
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["proposal_id"] == proposal.id
        assert payload["text"] == proposal.text
        assert payload["channel_id"] == "1234567890"
        assert "guild_id" in payload
        assert "channel_name" in payload
        current = store.get_proposal(proposal.id)
        assert current.state == ProposalState.SENT, current.state
        assert current.outbox_event_id == result.event_id
    print("  PASSED")


def _test_pending_expired_and_policy_blocks():
    print("[8F Smoke] Test 3: pending/expired/policy blocks...")
    from nana.runtime.starter_send import get_starter_send_controller
    from nana.runtime.starter_proposals import StarterProposalStore, ProposalState, get_proposal_store

    with tempfile.TemporaryDirectory() as tmp:
        outbox_dir = Path(tmp)
        controller = _reset_controller(outbox_dir=outbox_dir, enabled=True)
        StarterProposalStore.reset_for_test()
        store = get_proposal_store()
        store._proposal_ttl_seconds = 0.01

        # pending proposal cannot send
        pending = store.generate_proposal(
            policy=_fake_policy(),
            proactive_decision=type("D", (), {"should_proactive": True})(),
            starter_result={"starter": "Hôm nay mọi người đang làm gì?", "room_topic": "general", "blocked": False},
        )
        assert pending is not None
        result = controller.send_proposal(pending.id, dry_run=False)
        assert result.ok is False and "not_approved" in result.reason
        store.cancel(pending.id)

        # approved but expired cannot send
        approved = store.generate_proposal(
            policy=_fake_policy(),
            proactive_decision=type("D", (), {"should_proactive": True})(),
            starter_result={"starter": "Mọi người có bài nào đang nghe không?", "room_topic": "general", "blocked": False},
        )
        assert approved is not None
        ok, reason = store.approve(approved.id)
        assert ok, reason
        approved_row = store.get_proposal(approved.id)
        approved_row.expires_at = approved_row.created_at - 1.0
        result2 = controller.send_proposal(approved.id, dry_run=False)
        assert not result2.ok and result2.reason == "proposal_expired"
        assert approved_row.state == ProposalState.EXPIRED

        # offline policy blocks send
        _reset_controller(outbox_dir=outbox_dir, enabled=True)
        controller = get_starter_send_controller()
        controller.channel_id = "1234567890"
        controller.enabled = True
        store2, proposal2 = _make_approved_proposal()
        controller._policy_ok = lambda: (False, "stream_state_blocked:offline", None)  # type: ignore[method-assign]
        result3 = controller.send_proposal(proposal2.id, dry_run=False)
        assert not result3.ok and "stream_state_blocked" in result3.reason
    print("  PASSED")


def _test_cooldown_and_quota():
    print("[8F Smoke] Test 4: cooldown and quota...")
    from nana.runtime.starter_send import get_starter_send_controller

    with tempfile.TemporaryDirectory() as tmp:
        outbox_dir = Path(tmp)
        _reset_controller(outbox_dir=outbox_dir, enabled=True)
        controller = get_starter_send_controller()
        store, proposal = _make_approved_proposal()
        first = controller.send_proposal(proposal.id, dry_run=False)
        assert first.ok, first.reason

        # second immediate send should hit cooldown
        store2, proposal2 = _make_approved_proposal(text="Ai còn ở đây không?")
        second = controller.send_proposal(proposal2.id, dry_run=False)
        assert not second.ok and second.reason.startswith("cooldown"), second.reason

        # dry run should still work
        preview = controller.send_proposal(proposal2.id, dry_run=True)
        assert preview.ok and preview.dry_run
    print("  PASSED")


def _test_bridge_parser_and_move():
    print("[8F Smoke] Test 5: bridge outbox parser + move helpers...")
    from nana_discord_bridge.nana_discord_bridge.outbox import DiscordOutboxEvent, move_outbox_file

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "event.json"
        path.write_text(
            json.dumps(
                {
                    "event_id": "starter-123",
                    "proposal_id": "p-1",
                    "text": "Xin chào mọi người",
                    "channel_id": "1234567890",
                    "guild_id": "9876543210",
                    "channel_name": "chung",
                    "created_at": 1.0,
                    "auto_send": False,
                    "phase": "STAGE-8F",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        event = DiscordOutboxEvent.from_path(path)
        assert event.event_id == "starter-123"
        assert event.channel_id == 1234567890
        assert event.guild_id == 9876543210
        assert event.channel_name == "chung"
        moved = move_outbox_file(path, "sent")
        assert moved.exists()
    print("  PASSED")


def _test_status_and_help_surface():
    print("[8F Smoke] Test 6: status/help surface mentions 8F commands...")
    from nana.runtime.starter_send import starter_send_status_lines

    lines = starter_send_status_lines()
    joined = "\n".join(lines)
    assert "/starter-send-enable" in joined
    assert "/starter-proposal-send <id>" in joined
    print("  PASSED")


def run_smoke_tests() -> bool:
    tests = [
        _test_disabled_blocks_send,
        _test_enabled_send_writes_outbox_and_marks_sent,
        _test_pending_expired_and_policy_blocks,
        _test_cooldown_and_quota,
        _test_bridge_parser_and_move,
        _test_status_and_help_surface,
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
