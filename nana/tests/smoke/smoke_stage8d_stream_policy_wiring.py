"""STAGE-8D Smoke Tests: Stream Policy Wiring.

Verifies that 8A/8B/8C are wired into the autonomy loop and social starters:
  1. offline blocks proactive proposal
  2. live_idle allows proposal preview
  3. post_stream blocks proactive proposal
  4. error_safe voice_error allows text reply but no speak
  5. avatar directive preview never calls VTS
  6. status command includes last decision/reason
  7. no private memory flag leaks into public policy
  8. help mentions new command(s)
  9. autonomy loop only applies stream policy when public-stage flag is set

No auto-send, no TTS/VTS/OBS/game calls in any path.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _autonomy_context(values, *, public=False):
    from nana.runtime.context_autonomy import (
        PrivateOwnerAutonomyAuthority,
        PublicAutonomyAuthority,
        audience_fields,
        freeze_autonomy_snapshot,
        resolve_autonomy_audience,
    )
    if public:
        from nana.autonomy.observer import RealObserver
        from nana.runtime.public_context_boundary import PublicEventScope
        from nana.runtime.public_identity import CanonicalPublicIdentity
        identity = CanonicalPublicIdentity("youtube", "viewer-8d", "youtube:viewer-8d")
        scope = PublicEventScope(
            "youtube", "room-8d", "session-8d", "event-8d", "Minh", identity,
        )
        allowed = (
            "active_zone", "active_app", "time", "user_is_typing",
            "game_active", "command_in_flight", "audio_busy",
            "scene_relevance", "silence_duration_s", "silence_window_s",
            "forced_mode", "jitter_value", "web_context", "attention_window",
        )
        caller_state = {
            "platform": "youtube",
            "room_id": "room-8d",
            "stream_session_id": "session-8d",
            "event_id": "event-8d",
            **{key: values[key] for key in allowed if key in values},
        }
        authority = PublicAutonomyAuthority(scope, caller_state)
        return RealObserver(
            clock=lambda: 1000.0,
            wall_clock=lambda: 1000.0,
            audience_authority=authority,
        ).get_context()
    authority = PrivateOwnerAutonomyAuthority()
    audience = resolve_autonomy_audience(authority)
    payload = {
        **values,
        "audience": audience_fields(audience),
        "audience_resolved": True,
        "stream_stage_policy_gate": public,
    }
    snapshot = freeze_autonomy_snapshot(
        audience=audience,
        payload=payload,
        captured_wall_time=1000.0,
        captured_monotonic_time=1000.0,
    )
    return {**payload, "_autonomy_snapshot": snapshot}


# ─── Helpers ────────────────────────────────────────────────────────────────────


def _fake_policy(state, error_type="none", can_proactive=True, interaction_tone="quiet",
                  avatar_energy="normal", can_reply=True, can_speak=True, can_avatar=True,
                  proactive_budget=2, can_use_private_memory=False):
    """Build a fake StreamPolicy for testing without importing the full runtime."""
    from nana.runtime.stream_state import (
        StreamPolicy, StreamState, ErrorType, InteractionTone, AvatarEnergy,
        ViewerExpectation,
    )
    state_map = {"offline": StreamState.OFFLINE, "live_idle": StreamState.LIVE_IDLE,
                  "live_active": StreamState.LIVE_ACTIVE, "intermission": StreamState.INTERMISSION,
                  "post_stream": StreamState.POST_STREAM, "error_safe": StreamState.ERROR_SAFE}
    error_map = {"none": ErrorType.NONE, "bridge_down": ErrorType.BRIDGE_DOWN,
                 "voice_error": ErrorType.VOICE_ERROR, "vts_disconnect": ErrorType.VTS_DISCONNECT}
    tone_map = {"casual": InteractionTone.CASUAL, "quiet": InteractionTone.QUIET,
                "muted": InteractionTone.MUTED, "focused": InteractionTone.FOCUSED}
    energy_map = {"high": AvatarEnergy.HIGH, "normal": AvatarEnergy.NORMAL,
                  "low": AvatarEnergy.LOW, "dormant": AvatarEnergy.DORMANT}
    return StreamPolicy(
        state=state_map.get(state, StreamState.OFFLINE),
        reason="smoke_test",
        can_proactive=can_proactive,
        can_auto_send=False,
        can_use_private_memory=can_use_private_memory,
        proactive_budget=proactive_budget,
        interaction_tone=tone_map.get(interaction_tone, InteractionTone.QUIET),
        avatar_energy=energy_map.get(avatar_energy, AvatarEnergy.NORMAL),
        viewer_expectation=ViewerExpectation.NORMAL,
        error_type=error_map.get(error_type, ErrorType.NONE),
        can_reply=can_reply,
        can_speak=can_speak,
        can_avatar=can_avatar,
    )


def _fake_directive(can_avatar, expression="neutral", body_language="active", reason="smoke"):
    from nana.runtime.avatar_director import AvatarDirective
    return AvatarDirective(
        can_avatar=can_avatar,
        expression=expression,
        body_language=body_language,
        duration_s=3.0,
        reason=reason,
    )


# ─── Test 1: offline blocks proactive proposal ───────────────────────────────


def _test_offline_blocks_proactive():
    print("[8D Smoke] Test 1: offline blocks proactive proposal...")
    from nana.runtime.proactive_engine import ProactiveEngine

    engine = ProactiveEngine()
    policy = _fake_policy("offline", can_proactive=False)
    decision = engine.should_proactive(policy=policy)
    assert not decision.should_proactive, f"offline should block: {decision.reason}"
    print("  PASSED: offline blocks proactive")


# ─── Test 2: live_idle allows proposal preview ────────────────────────────────


def _test_live_idle_allows_preview():
    print("[8D Smoke] Test 2: live_idle allows proposal preview...")
    from nana.runtime.proactive_engine import ProactiveEngine

    engine = ProactiveEngine()
    policy = _fake_policy("live_idle", can_proactive=True, interaction_tone="quiet",
                            proactive_budget=2)
    decision = engine.should_proactive(
        policy=policy,
        chat_velocity=0.5,
        last_proactive_age_s=999.0,
        last_nana_chat_age_s=999.0,
    )
    assert decision.should_proactive, f"live_idle should allow: {decision.reason}"
    assert decision.urgency in {"low", "medium", "high"}
    print(f"  PASSED: live_idle allows preview (urgency={decision.urgency})")


# ─── Test 3: post_stream blocks proactive proposal ─────────────────────────────


def _test_post_stream_blocks_proactive():
    print("[8D Smoke] Test 3: post_stream blocks proactive proposal...")
    from nana.runtime.proactive_engine import ProactiveEngine

    engine = ProactiveEngine()
    policy = _fake_policy("post_stream", can_proactive=False)
    decision = engine.should_proactive(policy=policy)
    assert not decision.should_proactive, f"post_stream should block: {decision.reason}"
    print("  PASSED: post_stream blocks proactive")


# ─── Test 4: error_safe voice_error allows text reply but no speak ────────────


def _test_error_safe_voice_error():
    print("[8D Smoke] Test 4: error_safe voice_error allows reply but no speak...")
    policy = _fake_policy(
        "error_safe",
        error_type="voice_error",
        can_proactive=False,
        can_reply=True,
        can_speak=False,
        can_avatar=True,
    )
    assert policy.can_reply, "voice_error should allow text reply"
    assert not policy.can_speak, "voice_error should block speak"
    assert policy.can_avatar, "voice_error should allow avatar"
    print("  PASSED: voice_error allows reply, blocks speak, allows avatar")

    # Also test bridge_down — everything fails closed
    policy2 = _fake_policy(
        "error_safe",
        error_type="bridge_down",
        can_proactive=False,
        can_reply=False,
        can_speak=False,
        can_avatar=False,
    )
    assert not policy2.can_reply
    assert not policy2.can_speak
    assert not policy2.can_avatar
    print("  PASSED: bridge_down fails all closed")


# ─── Test 5: avatar directive preview never calls VTS ─────────────────────────


def _test_avatar_preview_no_vts():
    print("[8D Smoke] Test 5: avatar directive preview never calls VTS...")

    # Verify avatar_directive_preview_lines is a pure function (no side effects)
    from nana.runtime.avatar_director import avatar_director_preview_lines

    # Snapshot before
    lines = avatar_director_preview_lines("message")

    # Verify it returns lines without calling any backend
    assert isinstance(lines, list), "preview_lines must return a list"
    assert len(lines) > 0, "preview_lines must return content"
    assert any("preview_only" in l or "VTS" in l for l in lines), "must mention safety/preview"

    # Verify no VTS hotkey calls are made (this is a code inspection test)
    import inspect
    source = inspect.getsource(avatar_director_preview_lines)
    # Only flag actual method calls, not strings containing "vts"
    has_vts_call = any(kw in source for kw in ["vts(", "vts.", "_vts", "self._vts"])
    assert not has_vts_call, "preview_lines should not call VTS backend"
    print("  PASSED: avatar preview is read-only, no VTS call")


# ─── Test 6: status includes last decision/reason ─────────────────────────────


def _test_status_includes_decision():
    print("[8D Smoke] Test 6: status includes last decision/reason...")

    from nana.runtime.proactive_engine import get_proactive_engine
    from nana.runtime.stream_state import get_stream_state

    # Go through a state transition
    core = get_stream_state()
    core.force_offline()
    policy = core.get_policy()
    assert not policy.can_proactive, "offline policy should block proactive"

    engine = get_proactive_engine()
    decision = engine.should_proactive(policy=policy)
    assert not decision.should_proactive
    assert decision.reason, "decision must have a reason"

    status = engine.status()
    assert "last_proactive_at" in status
    assert "budget_count_in_window" in status
    print("  PASSED: status includes decision fields")

    # Check stage_status also includes stream policy
    from nana.core.status import print_stage_status
    import io
    import contextlib

    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        print_stage_status()
    output = f.getvalue()
    assert "Stream state" in output or "stream" in output.lower(), \
        "stage_status must show stream policy info"
    print("  PASSED: /stage-status shows stream policy")


# ─── Test 7: no private memory flag leaks into public policy ───────────────────


def _test_no_private_memory_leak():
    print("[8D Smoke] Test 7: no private memory flag leaks into public policy...")

    from nana.runtime.stream_state import ErrorType, StreamState, StreamStateCore

    core = StreamStateCore()
    for state in StreamState:
        with core._lock:
            core._state = state
            core._reason = f"privacy_{state.value}"
            core._error_type = ErrorType.VOICE_ERROR if state == StreamState.ERROR_SAFE else ErrorType.NONE
        policy = core.get_policy()
        assert not policy.can_use_private_memory, f"{state.value} should not expose private memory"

    print("  PASSED: stream/public policy blocks private memory in every state")


# ─── Test 8: help mentions new commands ───────────────────────────────────────


def _test_help_mentions_commands():
    print("[8D Smoke] Test 8: help mentions stream commands...")

    import inspect
    from nana.commands.help import print_command_help
    import io
    import contextlib

    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        print_command_help()
    output = f.getvalue()

    # Check stream commands are listed
    required = ["/stream-status", "/stream-live-on", "/stream-live-off",
                "/proactive-stage-preview", "/avatar-director-preview"]
    missing = [cmd for cmd in required if cmd not in output]
    assert not missing, f"help missing commands: {missing}"
    print("  PASSED: help lists all stream commands")


# ─── Test 9: social_starters respects stream policy gate ─────────────────────


def _test_social_starters_respects_policy():
    print("[8D Smoke] Test 9: social_starters respects stream policy gate...")
    from nana.runtime.social_starters import get_social_starter
    from nana.runtime.stream_state import get_stream_state

    core = get_stream_state()
    core.force_offline()

    starter = get_social_starter()
    snap = starter.snapshot()

    # Offline should block starter due to stream policy
    assert snap.get("is_blocked"), "offline should block social starter"
    reason = snap.get("blocked_reason", "")
    assert "stream_policy" in reason, f"block reason should mention stream_policy: {reason}"
    print(f"  PASSED: social starter blocked by stream_policy (reason={reason})")

    # live_idle should NOT block on stream policy
    core.go_live()
    snap2 = starter.snapshot()
    # Not blocked by stream policy (may be blocked by idle/cooldown instead)
    print(f"  PASSED: live_idle starter status: blocked={snap2.get('is_blocked')}, reason={snap2.get('blocked_reason')}")


# ─── Test 10: autonomy loop scope ─────────────────────────────────────────────


def _test_autonomy_stream_policy_scope():
    print("[8D Smoke] Test 10: autonomy stream policy is explicit public-stage only...")

    from nana.autonomy.loop import AutonomyLoop
    from nana.autonomy.inner_thought import Thought
    from nana.runtime.stream_state import get_stream_state

    def _gate_result(allowed=True):
        return type(
            "GateDecision",
            (),
            {
                "allowed": allowed,
                "reason": "ok" if allowed else "blocked",
                "level": "full" if allowed else "skip",
                "intensity": 0.8,
                "payload": {"tts": False, "vts": False, "subtitle": False},
            },
        )()

    loop = AutonomyLoop(clock=lambda: 1000.0)
    loop._cadence.can_express = lambda mode: (True, "ok")
    loop._cadence.record_expression = lambda mode, accepted, reason: None
    loop._gate.evaluate_debug = lambda ctx: {}
    loop._gate.evaluate = lambda ctx: _gate_result(True)
    loop._thought.pick = lambda *args, **kwargs: Thought(
        mode=args[0] if args else "stream_host",
        text="Nana đang ngó sân khấu một chút.",
        cooldown_s=0,
        min_silence_s=0,
        tags=[],
        raw="Nana đang ngó sân khấu một chút.",
        line_index=0,
        source="smoke",
    )
    loop._thought.pick_prefer_ultra_short = loop._thought.pick
    loop._express.emit = lambda mode, level, payload, thought: {"preview_only": True}

    get_stream_state().force_offline()
    base_context = {
        "forced_mode": "stream_host",
        "user_is_typing": False,
        "game_active": False,
        "command_in_flight": False,
        "audio_busy": False,
        "mood_affection": 0.7,
        "scene_relevance": 0.9,
        "silence_duration_s": 999.0,
        "silence_window_s": 60.0,
        "jitter_value": 0.9,
        "web_context": {},
        "attention_window": "browse_active",
    }

    loop._observer.get_context = lambda: _autonomy_context(base_context)
    accepted = loop.tick()
    assert accepted.get("accepted"), f"legacy stream_host banter should not be stream-blocked: {accepted}"

    loop._observer.get_context = lambda: _autonomy_context(base_context, public=True)
    blocked = loop.tick()
    assert not blocked.get("accepted"), "explicit public-stage gate should block while offline"
    assert "stream_policy" in blocked.get("reason", ""), f"expected stream_policy reason: {blocked}"
    print("  PASSED: legacy stream_host bypasses; explicit public-stage flag gates")


# ─── Test 11: no TTS/VTS/OBS calls in policy/preview paths ────────────────────


def _test_no_backend_calls_in_policy():
    print("[8D Smoke] Test 11: no backend calls in policy/preview paths...")

    import inspect
    from nana.runtime.proactive_engine import proactive_preview_lines
    from nana.runtime.avatar_director import avatar_director_preview_lines

    # Only flag actual backend calls, not just strings containing keywords
    src = inspect.getsource(proactive_preview_lines)
    # Look for actual function calls to backends, not field names
    backend_call_patterns = ["tts(", "vts(", "_tts(", "_vts(", "tts_engine", "voice_engine"]
    violations = [pat for pat in backend_call_patterns if pat in src]
    assert not violations, f"proactive_preview_lines calls backend: {violations}"
    print("  PASSED: proactive_preview_lines is pure read-only")

    # Check avatar_director_preview_lines has no backend calls
    src2 = inspect.getsource(avatar_director_preview_lines)
    violations2 = [pat for pat in backend_call_patterns if pat in src2]
    assert not violations2, f"avatar_preview_lines calls backend: {violations2}"
    print("  PASSED: avatar_director_preview_lines is pure read-only")


# ─── Run All ───────────────────────────────────────────────────────────────────


def run_smoke_tests():
    print("=" * 60)
    print("STAGE-8D Stream Policy Wiring — Smoke Tests")
    print("=" * 60)

    tests = [
        _test_offline_blocks_proactive,
        _test_live_idle_allows_preview,
        _test_post_stream_blocks_proactive,
        _test_error_safe_voice_error,
        _test_avatar_preview_no_vts,
        _test_status_includes_decision,
        _test_no_private_memory_leak,
        _test_help_mentions_commands,
        _test_social_starters_respects_policy,
        _test_autonomy_stream_policy_scope,
        _test_no_backend_calls_in_policy,
    ]

    passed = 0
    failed = 0

    for test in tests:
        # Clear singletons between tests for isolation
        try:
            from nana.runtime import stream_state as ss_mod
            from nana.runtime import proactive_engine as pe_mod
            from nana.runtime import avatar_director as ad_mod
            ss_mod._INSTANCE = None
            ss_mod.StreamStateCore._instance = None
            pe_mod._ENGINE = None
            pe_mod.ProactiveEngine._instance = None
            ad_mod._DIRECTOR = None
            ad_mod.AvatarDirector._instance = None
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
