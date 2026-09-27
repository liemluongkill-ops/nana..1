"""STAGE-8G smoke tests: auto-lite starter scheduler.

No live Discord, no TTS/VTS/OBS/game input. Outbox writes go to a temp dir.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fake_policy(state="live_idle"):
    from nana.runtime.stream_state import (
        AvatarEnergy,
        ErrorType,
        InteractionTone,
        StreamPolicy,
        StreamState,
        ViewerExpectation,
    )

    state_map = {
        "offline": StreamState.OFFLINE,
        "live_idle": StreamState.LIVE_IDLE,
        "live_active": StreamState.LIVE_ACTIVE,
        "intermission": StreamState.INTERMISSION,
        "post_stream": StreamState.POST_STREAM,
    }
    return StreamPolicy(
        state=state_map[state],
        reason="smoke",
        can_proactive=state in {"live_idle", "intermission"},
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=1,
        interaction_tone=InteractionTone.QUIET,
        avatar_energy=AvatarEnergy.LOW,
        viewer_expectation=ViewerExpectation.MUTED,
        error_type=ErrorType.NONE,
        can_reply=state in {"live_idle", "intermission"},
        can_speak=False,
        can_avatar=False,
    )


class _FakeSocialStarter:
    def __init__(self, *, blocked=False, text="Hôm nay mọi người đang làm gì vậy?"):
        self.blocked = blocked
        self.text = text
        self.calls = 0

    def generate_starter(self):
        self.calls += 1
        if self.blocked:
            return {
                "starter": None,
                "blocked": True,
                "blocked_reason": "not_idle",
                "room_topic": "general",
            }
        return {
            "starter": self.text,
            "blocked": False,
            "blocked_reason": "",
            "room_topic": "general",
        }


def _install_fake_social_starter(fake):
    import nana.runtime.social_starters as social_starters

    social_starters._STARTER_CACHE = fake


def _reset_runtime(*, outbox_dir: Path, send_enabled: bool = True, scheduler_enabled: bool = True):
    from nana.runtime.starter_auto import StarterAutoScheduler
    from nana.runtime.starter_proposals import StarterProposalStore
    from nana.runtime.starter_send import StarterSendController

    StarterProposalStore.reset_for_test()
    StarterSendController.reset_for_test()
    StarterAutoScheduler.reset_for_test()

    send_controller = StarterSendController(
        outbox_dir=outbox_dir,
        channel_id="1234567890",
        enabled=send_enabled,
        cooldown_seconds=0.0,
        quota_window_seconds=600.0,
        quota_max=10,
    )
    send_controller._policy_ok = lambda: (True, "ok", _fake_policy())  # type: ignore[method-assign]
    StarterSendController._instance = send_controller  # type: ignore[attr-defined]

    scheduler = StarterAutoScheduler(
        enabled=scheduler_enabled,
        cooldown_seconds=120.0,
        quota_window_seconds=600.0,
        quota_max=2,
        tick_interval_seconds=999.0,
    )
    scheduler._policy_ok = lambda: (True, "ok", _fake_policy())  # type: ignore[method-assign]
    StarterAutoScheduler._instance = scheduler  # type: ignore[attr-defined]
    return scheduler, send_controller


def _test_disabled_blocks_without_mutation():
    print("[8G Smoke] Test 1: disabled scheduler blocks send...")
    from nana.runtime.starter_proposals import get_proposal_store

    with tempfile.TemporaryDirectory() as tmp:
        fake = _FakeSocialStarter()
        _install_fake_social_starter(fake)
        scheduler, _ = _reset_runtime(outbox_dir=Path(tmp), scheduler_enabled=False)
        result = scheduler.tick(dry_run=False)
        assert not result.ok and result.reason == "auto_disabled", result
        assert get_proposal_store().snapshot()["total_proposals"] == 0
        assert fake.calls == 0
        assert not list(Path(tmp).glob("*.json"))
    print("  PASSED")


def _test_preview_is_read_only():
    print("[8G Smoke] Test 2: preview is read-only...")
    from nana.runtime.starter_proposals import get_proposal_store

    with tempfile.TemporaryDirectory() as tmp:
        fake = _FakeSocialStarter()
        _install_fake_social_starter(fake)
        scheduler, _ = _reset_runtime(outbox_dir=Path(tmp), scheduler_enabled=True)
        result = scheduler.tick(dry_run=True)
        assert result.ok and result.action == "would_send", result
        assert get_proposal_store().snapshot()["total_proposals"] == 0
        assert fake.calls == 0
        assert not list(Path(tmp).glob("*.json"))
    print("  PASSED")


def _test_send_gate_must_be_enabled():
    print("[8G Smoke] Test 3: 8F send gate must be enabled...")
    from nana.runtime.starter_proposals import get_proposal_store

    with tempfile.TemporaryDirectory() as tmp:
        fake = _FakeSocialStarter()
        _install_fake_social_starter(fake)
        scheduler, _ = _reset_runtime(
            outbox_dir=Path(tmp),
            send_enabled=False,
            scheduler_enabled=True,
        )
        result = scheduler.tick(dry_run=False)
        assert not result.ok and result.reason == "starter_send_disabled", result
        assert get_proposal_store().snapshot()["total_proposals"] == 0
        assert fake.calls == 0
    print("  PASSED")


def _test_social_starter_block_does_not_fallback():
    print("[8G Smoke] Test 4: social starter block does not fallback...")
    from nana.runtime.starter_proposals import get_proposal_store

    with tempfile.TemporaryDirectory() as tmp:
        fake = _FakeSocialStarter(blocked=True)
        _install_fake_social_starter(fake)
        scheduler, _ = _reset_runtime(outbox_dir=Path(tmp), scheduler_enabled=True)
        result = scheduler.tick(dry_run=False)
        assert not result.ok and result.reason.startswith("social_starter_blocked"), result
        assert get_proposal_store().snapshot()["total_proposals"] == 0
        assert fake.calls == 1
    print("  PASSED")


def _test_success_writes_outbox_and_marks_sent():
    print("[8G Smoke] Test 5: success writes outbox and marks sent...")
    from nana.runtime.starter_proposals import get_proposal_store

    with tempfile.TemporaryDirectory() as tmp:
        outbox_dir = Path(tmp)
        fake = _FakeSocialStarter(text="Phòng mình yên ghê, ai còn thức không?")
        _install_fake_social_starter(fake)
        scheduler, _ = _reset_runtime(outbox_dir=outbox_dir, scheduler_enabled=True)
        result = scheduler.tick(dry_run=False)
        assert result.ok and result.action == "sent", result
        files = list(outbox_dir.glob("*.json"))
        assert len(files) == 1, files
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        assert payload["proposal_id"] == result.proposal_id
        assert payload["text"] == result.text
        snap = get_proposal_store().snapshot()
        assert snap["sent"] == 1, snap
    print("  PASSED")


def _test_stage_directions_are_stripped_before_send():
    print("[8G Smoke] Test 6: stage directions are stripped before send...")
    with tempfile.TemporaryDirectory() as tmp:
        outbox_dir = Path(tmp)
        fake = _FakeSocialStarter(
            text="(Nana nghiêng đầu, nhìn vào màn hình rồi mỉm cười) Mọi người còn thức không?"
        )
        _install_fake_social_starter(fake)
        scheduler, _ = _reset_runtime(outbox_dir=outbox_dir, scheduler_enabled=True)
        result = scheduler.tick(dry_run=False)
        assert result.ok and result.action == "sent", result
        assert result.text == "Mọi người còn thức không?", result
        payload = json.loads(next(outbox_dir.glob("*.json")).read_text(encoding="utf-8"))
        assert payload["text"] == "Mọi người còn thức không?", payload
        assert "(" not in payload["text"] and ")" not in payload["text"], payload
    print("  PASSED")


def _test_scheduler_cooldown_blocks_second_send():
    print("[8G Smoke] Test 7: scheduler cooldown blocks second send...")
    with tempfile.TemporaryDirectory() as tmp:
        outbox_dir = Path(tmp)
        fake = _FakeSocialStarter()
        _install_fake_social_starter(fake)
        scheduler, _ = _reset_runtime(outbox_dir=outbox_dir, scheduler_enabled=True)
        first = scheduler.tick(dry_run=False)
        assert first.ok, first
        second = scheduler.tick(dry_run=False)
        assert not second.ok and second.reason.startswith("auto_cooldown"), second
    print("  PASSED")


def _test_status_surface_mentions_commands():
    print("[8G Smoke] Test 8: status surface mentions commands...")
    from nana.runtime.starter_auto import starter_auto_status_lines

    lines = "\n".join(starter_auto_status_lines())
    assert "/starter-auto-enable" in lines
    assert "/starter-auto-worker-start" in lines
    print("  PASSED")


def run_smoke_tests() -> bool:
    print("=" * 60)
    print("STAGE-8G Auto-lite Starter Scheduler — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_disabled_blocks_without_mutation,
        _test_preview_is_read_only,
        _test_send_gate_must_be_enabled,
        _test_social_starter_block_does_not_fallback,
        _test_success_writes_outbox_and_marks_sent,
        _test_stage_directions_are_stripped_before_send,
        _test_scheduler_cooldown_blocks_second_send,
        _test_status_surface_mentions_commands,
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
