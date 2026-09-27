"""STAGE-9A smoke tests: Mood Continuity Core.

No LLM, no TTS, no VTS, no OBS, no Discord send, and no game input.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reset():
    from nana.runtime.mood_continuity import MoodContinuity

    MoodContinuity.reset_for_test()
    try:
        from nana.runtime import social_session

        social_session._SOCIAL_SESSION = social_session.SocialSessionCache()
    except Exception:
        pass


def _test_default_status():
    print("[9A Smoke] Test 1: default status is steady and read-only...")
    from nana.runtime.mood_continuity import get_mood_continuity, mood_status_lines

    _reset()
    snap = get_mood_continuity().snapshot()
    assert snap.mood == "steady", snap
    assert snap.internal_mood["mood"] == "steady", snap
    assert snap.social_temperature["state"] == "unknown", snap
    lines = "\n".join(mood_status_lines())
    assert "STAGE-9A-v2" in lines, lines
    assert "Internal mood:" in lines, lines
    assert "Social temperature:" in lines, lines
    assert "no TTS/VTS/OBS/Discord/game" in lines, lines
    print("  PASSED")


def _test_positive_text_changes_warmth_playfulness():
    print("[9A Smoke] Test 2: positive text lifts warmth/playfulness...")
    from nana.runtime.mood_continuity import get_mood_continuity

    _reset()
    mood = get_mood_continuity()
    before = mood.snapshot()
    after = mood.observe_text("haha Nana cute quá, nói chuyện vui ghê", lane="public_stage", source="smoke", persist=False)
    assert after.warmth > before.warmth, (before, after)
    assert after.playfulness > before.playfulness, (before, after)
    assert "positive_chat" in after.last_reason, after
    assert after.social_temperature["state"] == "unknown", after
    print("  PASSED")


def _test_technical_friction_increases_focus_tension():
    print("[9A Smoke] Test 3: technical/friction text raises focus/tension...")
    from nana.runtime.mood_continuity import get_mood_continuity

    _reset()
    mood = get_mood_continuity()
    before = mood.snapshot()
    after = mood.observe_text("bridge bị bug error loạn rồi ông ơi", lane="private_owner", source="smoke", persist=False)
    assert after.focus > before.focus, (before, after)
    assert after.tension > before.tension, (before, after)
    assert "technical_focus" in after.last_reason, after
    assert "friction" in after.last_reason, after
    print("  PASSED")


def _test_decay_returns_toward_baseline():
    print("[9A Smoke] Test 4: decay drifts toward baseline...")
    from nana.runtime.mood_continuity import BASELINE, get_mood_continuity

    _reset()
    mood = get_mood_continuity()
    mood.observe_text("bug error fail hỏng loạn", lane="private_owner", source="smoke", persist=False)
    with mood._lock:
        mood._state["last_decay"] -= 60 * 60
        before_tension = mood._state["tension"]
    after = mood.snapshot()
    assert after.tension < before_tension, (before_tension, after)
    assert after.tension >= BASELINE["tension"], after
    print("  PASSED")


def _test_prompt_blocks_are_lane_safe():
    print("[9A Smoke] Test 5: prompt blocks are lane-safe...")
    from nana.runtime.mood_continuity import format_mood_prompt_block

    _reset()
    public_block = format_mood_prompt_block("public_stage")
    private_block = format_mood_prompt_block("private_owner")
    assert "MOOD CONTINUITY" in public_block, public_block
    assert "SOCIAL TEMPERATURE" in public_block, public_block
    assert "DERIVED FROM social_session" in public_block, public_block
    assert "Public stage" in public_block, public_block
    assert "Ba/con" not in public_block, public_block
    assert "Private owner lane" in private_block, private_block
    print("  PASSED")


def _test_social_temperature_comes_from_social_session():
    print("[9A Smoke] Test 6: social temperature is derived from social_session...")
    from nana.runtime.mood_continuity import get_mood_continuity
    from nana.runtime.social_session import PRIORITY_PUBLIC_LABEL, get_social_session

    _reset()
    mood = get_mood_continuity()
    mood.observe_text("Nana hôm nay vui ghê haha", lane="private_owner", source="smoke", persist=False)
    before = mood.snapshot()
    assert before.social_temperature["state"] == "unknown", before

    social = get_social_session()
    for index in range(8):
        social.observe(
            platform="discord",
            channel="chung",
            viewer_name=f"viewer{index}",
            text="hello Nana",
            priority=PRIORITY_PUBLIC_LABEL if index == 0 else "normal",
        )
    after = mood.snapshot()
    assert after.social_temperature["state"] in {"active", "fast", "stable", "cooling"}, after.social_temperature
    assert after.social_temperature["source"] == "social_session", after.social_temperature
    assert after.warmth == before.warmth, (before, after)
    print("  PASSED")


def _test_gpt_prompt_injection_helper():
    print("[9A Smoke] Test 7: gpt mood prompt helper returns block...")
    from nana.brain.gpt import _mood_prompt_for_boundary
    from nana.runtime.persona_boundary import resolve_persona_boundary

    _reset()
    public_boundary = resolve_persona_boundary(viewer_name="linhcute2746", stream_mode=True)
    block = _mood_prompt_for_boundary(public_boundary)
    assert "STAGE-9A" in block, block
    assert "SOCIAL TEMPERATURE" in block, block
    assert "Public stage" in block, block
    print("  PASSED")


def _test_stage_status_and_help_surface():
    print("[9A Smoke] Test 8: stage-status/help surface mention mood...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status

    _reset()
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    output = buf.getvalue()
    assert "Mood continuity:" in output, output
    assert "social=" in output, output
    assert "/mood-status" in output, output

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/mood-status" in help_output, help_output
    print("  PASSED")


def _test_public_firewall_blocks_commands():
    print("[9A Smoke] Test 9: public firewall blocks mood commands...")
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    guard = get_public_stage_identity_guard()
    for command in (
        "/mood-status",
        "/mood-preview public_stage",
        "/mood-test Nana hôm nay vui",
        "/mood-reset",
    ):
        assert guard.classify_public_input(command) == "backstage_command", command
    print("  PASSED")


def run_smoke_tests() -> bool:
    print("=" * 60)
    print("STAGE-9A Mood Continuity Core — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_default_status,
        _test_positive_text_changes_warmth_playfulness,
        _test_technical_friction_increases_focus_tension,
        _test_decay_returns_toward_baseline,
        _test_prompt_blocks_are_lane_safe,
        _test_social_temperature_comes_from_social_session,
        _test_gpt_prompt_injection_helper,
        _test_stage_status_and_help_surface,
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
