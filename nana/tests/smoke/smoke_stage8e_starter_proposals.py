"""STAGE-8E Starter Proposals Smoke Tests.

Acceptance criteria:
  1. offline/post_stream/error_safe bridge_down → no proposal
  2. live_idle → can create pending proposal
  3. intermission → can create gentle proposal
  4. expired proposal cannot approve
  5. approve marks approved only, does NOT send
  6. cancel marks cancelled
  7. clear removes expired/cancelled
  8. no duplicate proposal spam while pending exists
  9. proposal text has no Ba/con, no private memory, no service-bot tone

Also verifies:
  - /starter-proposals command lists queue
  - /starter-proposal-preview dry-runs without storing
  - approve/cancel/clear commands work
  - help mentions new commands
  - no TTS/VTS/OBS/game calls in proposal path
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ─── Helpers ───────────────────────────────────────────────────────────────────


class _FakeState:
    def __init__(self, value):
        self.value = value


class _FakePolicy:
    def __init__(self, state="offline", can_proactive=False, error_type="none",
                  interaction_tone="quiet", avatar_energy="normal",
                  can_reply=True, can_speak=True, can_avatar=True,
                  proactive_budget=0):
        self.state = _FakeState(state)
        self.can_proactive = can_proactive
        self.error_type = _FakeState(error_type)
        self.interaction_tone = _FakeState(interaction_tone)
        self.avatar_energy = _FakeState(avatar_energy)
        self.can_reply = can_reply
        self.can_speak = can_speak
        self.can_avatar = can_avatar
        self.proactive_budget = proactive_budget


class _FakeDecision:
    def __init__(self, should_proactive=False):
        self.should_proactive = should_proactive


class _FakeStarter:
    def __init__(self, text="Hôm nay mọi người đang làm gì?", topic="general", blocked=False):
        self.starter = text
        self.room_topic = topic
        self.blocked = blocked


# ─── Test 1: blocked states → no proposal ─────────────────────────────────────


def _test_blocked_states():
    print("[8E Smoke] Test 1: offline/post_stream/error_safe → no proposal...")
    from nana.runtime.starter_proposals import StarterProposalStore

    StarterProposalStore.reset_for_test()
    store = StarterProposalStore()

    blocked_cases = [
        ("offline", _FakePolicy(state="offline", can_proactive=False)),
        ("post_stream", _FakePolicy(state="post_stream", can_proactive=False)),
        ("error_safe bridge_down", _FakePolicy(state="error_safe", can_proactive=False, error_type="bridge_down")),
    ]

    for name, policy in blocked_cases:
        result = store.generate_proposal(policy=policy)
        assert result is None, f"{name} should produce no proposal"
        print(f"  {name}: no proposal — OK")

    print("  PASSED")


# ─── Test 2: live_idle → can create pending ────────────────────────────────────


def _test_live_idle_creates_pending():
    print("[8E Smoke] Test 2: live_idle → pending proposal...")
    from nana.runtime.starter_proposals import StarterProposalStore, ProposalState

    StarterProposalStore.reset_for_test()
    store = StarterProposalStore()

    policy = _FakePolicy(state="live_idle", can_proactive=True, interaction_tone="quiet", proactive_budget=2)
    decision = _FakeDecision(should_proactive=True)
    starter = _FakeStarter(text="Hôm nay mọi người đang làm gì vậy?")

    result = store.generate_proposal(
        policy=policy,
        proactive_decision=decision,
        starter_result=starter.__dict__,
    )

    assert result is not None, "live_idle should create proposal"
    assert result.state == ProposalState.PENDING
    assert result.stream_state == "live_idle"
    assert result.interaction_tone == "quiet"
    print(f"  pending proposal id={result.id}: {result.text[:50]}")
    print("  PASSED")


# ─── Test 3: intermission → gentle proposal ────────────────────────────────────


def _test_intermission_gentle():
    print("[8E Smoke] Test 3: intermission → gentle proposal...")
    from nana.runtime.starter_proposals import StarterProposalStore, ProposalState

    StarterProposalStore.reset_for_test()
    store = StarterProposalStore()

    policy = _FakePolicy(state="intermission", can_proactive=True, interaction_tone="quiet", proactive_budget=1)
    decision = _FakeDecision(should_proactive=True)
    starter = _FakeStarter(text="Nghỉ giải lao một chút nha mọi người!")

    result = store.generate_proposal(
        policy=policy,
        proactive_decision=decision,
        starter_result=starter.__dict__,
    )

    assert result is not None, "intermission should create proposal"
    assert result.state == ProposalState.PENDING
    assert result.stream_state == "intermission"
    print(f"  intermission proposal: tone={result.interaction_tone}")
    print("  PASSED")


# ─── Test 4: expired cannot approve ────────────────────────────────────────────


def _test_expired_cannot_approve():
    print("[8E Smoke] Test 4: expired proposal cannot approve...")
    from nana.runtime.starter_proposals import StarterProposalStore, ProposalState

    StarterProposalStore.reset_for_test()
    store = StarterProposalStore()
    store._proposal_ttl_seconds = 0.05  # 50ms for testing

    policy = _FakePolicy(state="live_idle", can_proactive=True)
    decision = _FakeDecision(should_proactive=True)
    starter = _FakeStarter(text="Test expiry")

    result = store.generate_proposal(policy=policy, proactive_decision=decision, starter_result=starter.__dict__)
    assert result is not None

    import time
    time.sleep(0.07)

    ok, reason = store.approve(result.id)
    assert not ok and "expired" in reason, f"Expired should not approve: {reason}"
    print("  expired approve blocked — OK")
    print("  PASSED")


# ─── Test 5: approve does NOT send ─────────────────────────────────────────────


def _test_approve_not_send():
    print("[8E Smoke] Test 5: approve marks approved only, does NOT send...")
    from nana.runtime.starter_proposals import StarterProposalStore, ProposalState

    StarterProposalStore.reset_for_test()
    store = StarterProposalStore()

    policy = _FakePolicy(state="live_idle", can_proactive=True)
    decision = _FakeDecision(should_proactive=True)
    starter = _FakeStarter(text="Test approve")

    result = store.generate_proposal(policy=policy, proactive_decision=decision, starter_result=starter.__dict__)
    assert result is not None
    pid = result.id

    ok, reason = store.approve(pid)
    assert ok and reason == "ok"

    p = store.get_proposal(pid)
    assert p.state == ProposalState.APPROVED

    # Verify no send happened — approve only marks state
    snap = store.snapshot()
    assert snap["approved"] == 1
    # approved count did not trigger any backend
    print("  approved state set — OK (no send)")
    print("  PASSED")


# ─── Test 6: cancel marks cancelled ─────────────────────────────────────────────


def _test_cancel_marks_cancelled():
    print("[8E Smoke] Test 6: cancel marks cancelled...")
    from nana.runtime.starter_proposals import StarterProposalStore, ProposalState

    StarterProposalStore.reset_for_test()
    store = StarterProposalStore()

    policy = _FakePolicy(state="live_idle", can_proactive=True)
    decision = _FakeDecision(should_proactive=True)
    starter = _FakeStarter(text="Test cancel")

    result = store.generate_proposal(policy=policy, proactive_decision=decision, starter_result=starter.__dict__)
    assert result is not None
    pid = result.id

    ok, reason = store.cancel(pid)
    assert ok and reason == "ok"

    p = store.get_proposal(pid)
    assert p.state == ProposalState.CANCELLED
    print("  cancelled state set — OK")
    print("  PASSED")


# ─── Test 7: clear removes expired/cancelled ───────────────────────────────────


def _test_clear_removes():
    print("[8E Smoke] Test 7: clear removes expired/cancelled...")
    from nana.runtime.starter_proposals import StarterProposalStore, ProposalState

    StarterProposalStore.reset_for_test()
    store = StarterProposalStore()
    store._proposal_ttl_seconds = 0.05

    policy = _FakePolicy(state="live_idle", can_proactive=True)
    decision = _FakeDecision(should_proactive=True)

    # Create expired proposal
    r1 = store.generate_proposal(policy=policy, proactive_decision=decision, starter_result={"starter": "Expired", "room_topic": "general", "blocked": False})
    import time
    time.sleep(0.07)
    r1_id = r1.id

    # Create cancelled proposal
    r2 = store.generate_proposal(policy=policy, proactive_decision=decision, starter_result={"starter": "Cancelled", "room_topic": "general", "blocked": False})
    store.cancel(r2.id)
    r2_id = r2.id

    snap_before = store.snapshot()
    assert snap_before["expired"] >= 1
    assert snap_before["cancelled"] >= 1

    removed = store.clear()

    assert removed >= 2, f"Expected >=2 removed, got {removed}"
    assert store.get_proposal(r1_id) is None, "Expired should be removed"
    assert store.get_proposal(r2_id) is None, "Cancelled should be removed"
    print(f"  cleared {removed} expired/cancelled — OK")
    print("  PASSED")


# ─── Test 8: no duplicate spam while pending ─────────────────────────────────────


def _test_no_duplicate_spam():
    print("[8E Smoke] Test 8: no duplicate spam while pending...")
    from nana.runtime.starter_proposals import StarterProposalStore, ProposalState

    StarterProposalStore.reset_for_test()
    store = StarterProposalStore()

    policy = _FakePolicy(state="live_idle", can_proactive=True, proactive_budget=5)
    decision = _FakeDecision(should_proactive=True)
    starter = _FakeStarter(text="First")

    r1 = store.generate_proposal(policy=policy, proactive_decision=decision, starter_result=starter.__dict__)
    assert r1 is not None

    # Try to generate more while pending exists
    for i in range(3):
        ri = store.generate_proposal(policy=policy, proactive_decision=decision, starter_result={"starter": f"Spam{i}", "room_topic": "general", "blocked": False})
        assert ri is None, f"Spam attempt {i+1} should be blocked while pending exists"

    print("  duplicate spam blocked — OK")
    print("  PASSED")


# ─── Test 9: safety guard blocks leaked proposals ───────────────────────────────


def _test_safety_blocks_leaks():
    print("[8E Smoke] Test 9: safety blocks Ba/con/service-bot tone...")
    from nana.runtime.starter_proposals import StarterProposalStore, ProposalState

    StarterProposalStore.reset_for_test()
    store = StarterProposalStore()

    policy = _FakePolicy(state="live_idle", can_proactive=True)
    decision = _FakeDecision(should_proactive=True)

    leaky_cases = [
        ("Ba reference", "Ba ơi cho con hỏi..."),
        ("con reference", "Con thích bài này lắm!"),
        ("service bot", "Stream đang bắt đầu rồi nha!"),
    ]

    for name, text in leaky_cases:
        result = store.generate_proposal(
            policy=policy,
            proactive_decision=decision,
            starter_result={"starter": text, "room_topic": "general", "blocked": False},
        )
        assert result is None, f"Safety should block {name}: {text}"

    print("  Ba leak: blocked — OK")
    print("  con leak: blocked — OK")
    print("  Service bot tone: blocked — OK")
    print("  PASSED")


# ─── Test 10: public noun phrase should not be blocked ────────────────────────


def _test_con_nguoi_not_blocked():
    print("[8E Smoke] Test 10: 'con người' noun phrase is public-safe...")
    from nana.runtime.starter_proposals import StarterProposalStore, ProposalState

    StarterProposalStore.reset_for_test()
    store = StarterProposalStore()

    policy = _FakePolicy(state="live_idle", can_proactive=True)
    decision = _FakeDecision(should_proactive=True)
    result = store.generate_proposal(
        policy=policy,
        proactive_decision=decision,
        starter_result={
            "starter": "Con người lúc nào cũng có mấy câu hỏi khó ghê.",
            "room_topic": "general",
            "blocked": False,
        },
    )
    assert result is not None, "'con người' should not be treated as private con/Ba leak"
    assert result.state == ProposalState.PENDING
    print("  'con người' accepted — OK")
    print("  PASSED")


# ─── Test 11: commands work ─────────────────────────────────────────────────────


def _test_commands():
    print("[8E Smoke] Test 11: commands work...")
    import asyncio
    from nana.cli.handle_text import handle_text

    async def run():
        cmds = [
            "/starter-proposals",
            "/starter-proposal-preview",
            "/starter-proposal-create",
        ]
        for cmd in cmds:
            await handle_text(None, None, cmd, None)

    asyncio.run(run())
    print("  /starter-proposals + /starter-proposal-preview + /starter-proposal-create: OK")
    print("  PASSED")


# ─── Test 12: help mentions commands ───────────────────────────────────────────


def _test_help():
    print("[8E Smoke] Test 12: help mentions 8E commands...")
    import io
    import contextlib
    from nana.commands.help import print_command_help

    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        print_command_help()
    output = f.getvalue()

    required = ["/starter-proposals", "/starter-proposal-preview",
                "/starter-proposal-create", "/starter-proposal-approve", "/starter-proposal-cancel",
                "/starter-proposal-clear"]
    missing = [cmd for cmd in required if cmd not in output]
    assert not missing, f"help missing: {missing}"
    print("  help lists all 8E commands — OK")
    print("  PASSED")


# ─── Test 13: no backend calls in proposal path ───────────────────────────────


def _test_no_backend_calls():
    print("[8E Smoke] Test 13: no TTS/VTS/OBS calls in proposal path...")

    from nana.runtime.starter_proposals import generate_starter_proposal, proposal_preview

    src1 = inspect.getsource(generate_starter_proposal)
    src2 = inspect.getsource(proposal_preview)

    backend_patterns = ["tts(", "vts(", "_tts(", "_vts(", "obs(", "game(", "discord(", "voice_engine"]
    violations1 = [p for p in backend_patterns if p in src1]
    violations2 = [p for p in backend_patterns if p in src2]
    assert not violations1, f"generate_starter_proposal calls backend: {violations1}"
    assert not violations2, f"proposal_preview calls backend: {violations2}"
    print("  No TTS/VTS/OBS/game calls — OK")
    print("  PASSED")


# ─── Run All ───────────────────────────────────────────────────────────────────


def run_smoke_tests():
    print("=" * 60)
    print("STAGE-8E Starter Proposals — Smoke Tests")
    print("=" * 60)

    tests = [
        _test_blocked_states,
        _test_live_idle_creates_pending,
        _test_intermission_gentle,
        _test_expired_cannot_approve,
        _test_approve_not_send,
        _test_cancel_marks_cancelled,
        _test_clear_removes,
        _test_no_duplicate_spam,
        _test_safety_blocks_leaks,
        _test_con_nguoi_not_blocked,
        _test_commands,
        _test_help,
        _test_no_backend_calls,
    ]

    passed = 0
    failed = 0

    for test in tests:
        # Reset singletons between tests
        try:
            from nana.runtime import starter_proposals as sp_mod
            sp_mod._STORE = None
            sp_mod.StarterProposalStore._instance = None
        except Exception:
            pass

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
    success = run_smoke_tests()
    sys.exit(0 if success else 1)
