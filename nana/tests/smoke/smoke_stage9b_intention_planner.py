"""STAGE-9B smoke tests: Intention Planner.

No LLM, no chat send, no TTS/VTS/OBS/Discord, and no game input.
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
    from nana.runtime.intention_planner import IntentionPlanner
    from nana.runtime.mood_continuity import MoodContinuity
    from nana.runtime.stream_state import get_stream_state

    MoodContinuity.reset_for_test()
    IntentionPlanner.reset_for_test()
    try:
        from nana.runtime import social_session

        social_session._SOCIAL_SESSION = social_session.SocialSessionCache()
    except Exception:
        pass
    stream = get_stream_state()
    stream.force_offline()


def _test_offline_blocks_intention():
    print("[9B Smoke] Test 1: offline yields standby_observe...")
    from nana.runtime.intention_planner import get_intention_planner

    _reset()
    plan = get_intention_planner().plan(dry_run=True)
    assert plan.intention == "standby_observe", plan
    assert plan.category == "observer", plan
    assert plan.priority == "low", plan
    assert plan.priority_rank == 1, plan
    assert not plan.can_act and not plan.can_send, plan
    print("  PASSED")


def _test_live_quiet_yields_topic_seed():
    print("[9B Smoke] Test 2: live quiet room yields topic seed...")
    from nana.runtime.intention_planner import get_intention_planner
    from nana.runtime.stream_state import get_stream_state

    _reset()
    get_stream_state().go_live()
    plan = get_intention_planner().plan(dry_run=True)
    assert plan.category in {"social_warmup", "stream_host"}, plan
    assert plan.priority in {"medium", "low"}, plan
    assert "action" not in plan.prompt_hint.lower(), plan
    print("  PASSED")


def _test_tense_mood_yields_recover_composure():
    print("[9B Smoke] Test 3: tense focused mood yields recover_composure...")
    from nana.runtime.intention_planner import get_intention_planner
    from nana.runtime.mood_continuity import get_mood_continuity
    from nana.runtime.stream_state import get_stream_state

    _reset()
    get_stream_state().go_live()
    mood = get_mood_continuity()
    for sample in (
        "bridge bug error fail hỏng loạn runtime",
        "debug lỗi token bridge error fail",
        "vts error fail hỏng loạn debug",
        "api exception traceback bug sai",
    ):
        mood.observe_text(sample, lane="private_owner", source="smoke", persist=False)
    plan = get_intention_planner().plan(dry_run=True)
    assert plan.intention == "recover_composure", plan
    assert plan.category == "observer", plan
    assert plan.priority == "high", plan
    assert plan.tone == "focused", plan
    print("  PASSED")


def _test_conflict_resolution_prefers_high_priority():
    print("[9B Smoke] Test 4: conflict resolution prefers high priority...")
    from nana.runtime.intention_planner import get_intention_planner
    from nana.runtime.mood_continuity import get_mood_continuity
    from nana.runtime.social_session import get_social_session
    from nana.runtime.stream_state import get_stream_state

    _reset()
    get_stream_state().go_live()
    social = get_social_session()
    for index in range(20):
        social.observe(
            platform="discord",
            channel="chung",
            viewer_name=f"viewer{index}",
            text="Nana ơi",
            monotonic_now=2000.0 + index * 0.2,
        )
    mood = get_mood_continuity()
    for sample in (
        "bridge bug error fail hỏng loạn runtime",
        "debug lỗi token bridge error fail",
        "vts error fail hỏng loạn debug",
        "api exception traceback bug sai",
    ):
        mood.observe_text(sample, lane="private_owner", source="smoke", persist=False)
    plan = get_intention_planner().plan(dry_run=True)
    assert plan.priority == "high", plan
    assert plan.intention in {"recover_composure", "host_room_momentum"}, plan
    assert len(plan.conflicts) >= 1, plan
    print("  PASSED")


def _test_expiry_visible_and_demotes_stale_intention():
    print("[9B Smoke] Test 5: stale intentions expire visibly...")
    from nana.runtime.intention_planner import INTENTION_TTL_SECONDS, get_intention_planner
    from nana.runtime.stream_state import get_stream_state

    _reset()
    get_stream_state().go_live()
    planner = get_intention_planner()
    plan = planner.plan(dry_run=True)
    key = f"{plan.category}:{plan.intention}"
    with planner._lock:
        planner._active_since_by_key[key] = 1.0
    stale = planner.plan(dry_run=True)
    assert any(
        item.get("reason") == f"expired_after_{INTENTION_TTL_SECONDS:.0f}s"
        for item in stale.inactive
    ), stale
    print("  PASSED")


def _test_refresh_stores_plan_without_action():
    print("[9B Smoke] Test 6: refresh stores plan without action...")
    from nana.runtime.intention_planner import get_intention_planner
    from nana.runtime.stream_state import get_stream_state

    _reset()
    get_stream_state().go_live()
    planner = get_intention_planner()
    plan = planner.plan(dry_run=False)
    snap = planner.snapshot()
    assert snap["stats"]["planned"] == 1, snap
    assert snap["last_plan"]["intention"] == plan.intention, snap
    assert snap["safety"]["can_act"] is False, snap
    assert snap["safety"]["game_input"] is False, snap
    print("  PASSED")


def _test_status_stage_help_and_firewall():
    print("[9B Smoke] Test 7: status/stage/help/firewall mention intention...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.intention_planner import intention_preview_lines, intention_refresh_lines, intention_status_lines
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    _reset()
    status = "\n".join(intention_status_lines())
    preview = "\n".join(intention_preview_lines())
    refresh = "\n".join(intention_refresh_lines())
    assert "STAGE-9B" in status, status
    assert "Active:" in status, status
    assert "Inactive:" in status, status
    assert "no LLM/TTS/VTS/OBS/Discord/game" in status, status
    assert "Stored: False" in preview, preview
    assert "Stored: True" in refresh, refresh

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    assert "Intention planner:" in buf.getvalue(), buf.getvalue()

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    assert "/intention-status" in buf.getvalue(), buf.getvalue()

    guard = get_public_stage_identity_guard()
    for command in ("/intention-status", "/intention-preview", "/intention-refresh", "/planner-status"):
        assert guard.classify_public_input(command) == "backstage_command", command
    print("  PASSED")


def run_smoke_tests() -> bool:
    print("=" * 60)
    print("STAGE-9B Intention Planner — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_offline_blocks_intention,
        _test_live_quiet_yields_topic_seed,
        _test_tense_mood_yields_recover_composure,
        _test_conflict_resolution_prefers_high_priority,
        _test_expiry_visible_and_demotes_stale_intention,
        _test_refresh_stores_plan_without_action,
        _test_status_stage_help_and_firewall,
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
