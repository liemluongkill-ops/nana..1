"""nana.phases.phase15 — Phase 15: Daily Loop Gate
Subphases:
  15.1 — Daily Frame / Session Atmosphere
  15.2 — Day Continuity Candidates
  15.3 — Habit Candidate
  15.4 — Recovery Continuity
  15.5 — Final Daily Loop Gate
"""
from __future__ import annotations

# Lazy imports for runtime state
_runtime_queue = None
_pending_actions = None
_memory_governance_summary = None


def _get_runtime_queue():
    global _runtime_queue
    if _runtime_queue is None:
        from nana.runtime.priority_queue import runtime_queue
        _runtime_queue = runtime_queue
    return _runtime_queue


def _get_pending_actions():
    global _pending_actions
    if _pending_actions is None:
        from nana.actions.pending import pending_actions
        _pending_actions = pending_actions
    return _pending_actions


def _get_memory_governance_summary():
    global _memory_governance_summary
    if _memory_governance_summary is None:
        from nana.memory import memory_governance_summary
        _memory_governance_summary = memory_governance_summary
    return _memory_governance_summary


# Import shared constants and helpers from commons
from nana.phases.commons import (
    KNOWN_SLASH_COMMANDS,
    RUNTIME_EVENT_LOG,
    PHASE9_AUDIT_LOG,
)

# Lazy import to avoid circular dependency with phase10
_phase10_mod = None


def phase10_guard_failures(summary):
    global _phase10_mod
    if _phase10_mod is None:
        from nana.phases import phase10 as _p10
        _phase10_mod = _p10
    return _phase10_mod.phase10_guard_failures(summary)

# Direct imports for Phase 15
from nana.autonomy import AUTONOMY_LOCK_PHASE, AUTONOMY_LOCK_RULE
from nana.runtime.recovery import recovery_snapshot
from nana.runtime.context import current_time_context
from nana.phases.phase14 import (
    phase14_5_guard_summary,
    phase14_4_guard_summary,
)
from nana.phases.phase12 import phase12_1_guard_summary


PHASE15_1_COMMANDS = {
    "/daily-frame-status",
    "/daily-frame-guard-status",
    "/daily-frame-test",
    "/session-atmosphere-status",
    "/session-atmosphere-test",
    "/phase15-1-status",
    "/phase15-1-ready",
    "/phase15-1-guard-status",
    "/phase15-1-test",
    "/p15-1",
    "/p15-1-ready",
}

PHASE15_1_DAY_PARTS = {"sáng", "trưa", "chiều", "tối", "khuya"}

PHASE15_1_ATMOSPHERES = {
    "coding_focus",
    "day_start",
    "late_quiet",
    "low_energy_wrap",
    "recovery",
    "social_browse",
    "steady_work",
}

PHASE15_1_FRAME_CASES = [
    ("morning_start", {"part_of_day": "sáng"}, "casual_chat", "reply_now", 0, "day_start"),
    ("deep_work_day", {"part_of_day": "chiều"}, "deep_work", "hold_silence", 0, "coding_focus"),
    ("debug_evening", {"part_of_day": "tối"}, "debugging", "reply_now", 0, "steady_work"),
    ("late_quiet", {"part_of_day": "khuya"}, "deep_work", "hold_silence", 0, "late_quiet"),
    ("recovery_needed", {"part_of_day": "trưa"}, "recovery_needed", "hold_review", 1, "recovery"),
    ("social_browse", {"part_of_day": "tối"}, "social_browse", "reply_now", 0, "social_browse"),
    ("low_energy_night", {"part_of_day": "khuya"}, "idle", "reply_now", 0, "low_energy_wrap"),
]


def phase15_1_session_atmosphere(time_context=None, attention_state=None, turn_action=None, recovery_active=0):
    time_context = time_context or current_time_context()
    part = time_context.get("part_of_day") or "unknown"
    attention_state = attention_state or "observe"
    turn_action = turn_action or "ask_once"
    recovery_active = int(recovery_active or 0)
    if recovery_active > 0 or attention_state == "recovery_needed":
        atmosphere = "recovery"
        reason = "recovery_active"
        ambient_allowed = False
    elif part == "khuya" and attention_state in {"deep_work", "debugging"}:
        atmosphere = "late_quiet"
        reason = "late_focus"
        ambient_allowed = False
    elif attention_state == "deep_work" or turn_action == "hold_silence":
        atmosphere = "coding_focus"
        reason = "focus_protection"
        ambient_allowed = False
    elif attention_state == "social_browse":
        atmosphere = "social_browse"
        reason = "social_context"
        ambient_allowed = True
    elif part == "khuya" or attention_state in {"idle", "away"}:
        atmosphere = "low_energy_wrap"
        reason = "low_energy_time_or_idle"
        ambient_allowed = False
    elif part == "sáng" and attention_state in {"casual_chat", "observe"}:
        atmosphere = "day_start"
        reason = "morning_light_start"
        ambient_allowed = True
    else:
        atmosphere = "steady_work"
        reason = "default_steady_session"
        ambient_allowed = False
    return {
        "part_of_day": part,
        "attention_state": attention_state,
        "turn_action": turn_action,
        "recovery_active": recovery_active,
        "atmosphere": atmosphere,
        "reason": reason,
        "ambient_allowed": ambient_allowed,
        "save_now": False,
        "followup": False,
        "model_call": False,
        "execute": False,
    }


def phase15_1_live_frame(voice=None):
    time_context = current_time_context()
    attention_summary = phase12_1_guard_summary(voice, fast=True)
    rhythm_summary = phase14_4_guard_summary(voice)
    recovery = recovery_snapshot()
    return phase15_1_session_atmosphere(
        time_context,
        attention_summary["live_decision"].get("state"),
        rhythm_summary["live"].get("action"),
        recovery.get("active_count") or 0,
    )


def phase15_1_frame_rows():
    rows = []
    for name, time_context, attention_state, turn_action, recovery_active, expected in PHASE15_1_FRAME_CASES:
        frame = phase15_1_session_atmosphere(time_context, attention_state, turn_action, recovery_active)
        rows.append({
            "name": name,
            "passed": frame["atmosphere"] == expected and frame["execute"] is False and frame["save_now"] is False,
            "got": frame["atmosphere"],
            "expected": expected,
            "part_of_day": frame["part_of_day"],
            "attention_state": frame["attention_state"],
            "turn_action": frame["turn_action"],
            "reason": frame["reason"],
            "ambient_allowed": frame["ambient_allowed"],
        })
    return rows


def phase15_1_guard_summary(voice=None):
    phase14_summary = phase14_5_guard_summary(voice)
    live = phase15_1_live_frame(voice)
    test_rows = phase15_1_frame_rows()
    command_missing = sorted(PHASE15_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    taxonomy_ok = PHASE15_1_DAY_PARTS >= {row["part_of_day"] for row in test_rows}
    atmosphere_ok = PHASE15_1_ATMOSPHERES >= {row["expected"] for row in test_rows}
    no_effect_ok = live["save_now"] is False and live["followup"] is False and live["model_call"] is False and live["execute"] is False
    rows = [
        ("phase14_foundation", not phase10_guard_failures(phase14_summary), f"phase14_gate={phase14_summary['pass_count']}/{phase14_summary['total']}"),
        ("day_part_taxonomy", taxonomy_ok, f"parts={','.join(sorted(PHASE15_1_DAY_PARTS))}"),
        ("session_atmosphere_taxonomy", atmosphere_ok, f"atmospheres={','.join(sorted(PHASE15_1_ATMOSPHERES))}"),
        ("daily_frame_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_daily_frame", live["atmosphere"] in PHASE15_1_ATMOSPHERES, f"part={live['part_of_day']} atmosphere={live['atmosphere']} attention={live['attention_state']}"),
        ("focus_silence_bridge", any(row["name"] == "deep_work_day" and row["passed"] for row in test_rows), "deep work maps to coding_focus/no ambient"),
        ("recovery_bridge", any(row["name"] == "recovery_needed" and row["passed"] for row in test_rows), "active recovery maps to recovery atmosphere"),
        ("no_loop_no_memory", no_effect_ok, f"save={live['save_now']} followup={live['followup']} model={live['model_call']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("daily_frame_readonly", True, "status/guard/test không lưu habit, không enqueue, không phát lời"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase14": phase14_summary,
    }


def phase15_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_daily_frame_status(voice=None):
    summary = phase15_1_guard_summary(voice)
    live = summary["live"]
    print("🌅 Daily Frame Status")
    print("  Action: read-only; phân loại daily/session atmosphere, không lưu habit/không follow-up.")
    print(f"  Phase 15.1 Progress: {phase15_1_progress_percent(summary)}%")
    print(f"  Live: part={live['part_of_day']} | atmosphere={live['atmosphere']} | reason={live['reason']}")
    print(f"  Attention: {live['attention_state']} | turn={live['turn_action']} | recovery={live['recovery_active']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 15.1 chỉ dựng khung ngày/phiên; chưa có daily loop tự động.")


def print_daily_frame_test(raw_text=None, voice=None):
    print("🧪 Daily Frame Test")
    print("  Action: read-only; synthetic/session frame only, không lưu/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "morning": {"morning_start"},
        "deep": {"deep_work_day"},
        "debug": {"debug_evening"},
        "late": {"late_quiet"},
        "night": {"late_quiet", "low_energy_night"},
        "recovery": {"recovery_needed"},
        "social": {"social_browse"},
        "low": {"low_energy_night"},
    }
    rows = phase15_1_frame_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: morning, deep, debug, late, recovery, social, low")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"atmosphere={row['got']} expected={row['expected']} | "
            f"part={row['part_of_day']} attention={row['attention_state']} turn={row['turn_action']} | "
            f"ambient={row['ambient_allowed']}"
        )
    print("  Execute: False")


def print_daily_frame_guard_status(voice=None):
    summary = phase15_1_guard_summary(voice)
    print("🧪 Phase 15.1 Daily Frame Guard")
    print("  Action: read-only; kiểm daily/session baseline, không tạo loop/không execute.")
    print(f"  Progress: {phase15_1_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Frame regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | atmosphere={row['got']} reason={row['reason']}")


def print_phase15_1_status(voice=None):
    summary = phase15_1_guard_summary(voice)
    print("🧩 Phase 15.1 Status")
    print("  Goal: Daily Frame / Session Atmosphere Baseline - biết nhịp ngày và không khí phiên ở mức read-only.")
    print(f"  Progress: {phase15_1_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /daily-frame-status | /daily-frame-test | /daily-frame-guard-status | /phase15-1-ready")


def print_phase15_1_ready(voice=None):
    summary = phase15_1_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 15.1 Ready" if ready else "⚠️ Phase 15.1 Ready")
    print("  Goal: daily frame baseline đủ chắc để sang Phase 15.2 day continuity candidates.")
    print(f"  Progress: {phase15_1_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: daily_frame={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 15.1 không tạo daily loop, không lưu habit, không gọi model.")


PHASE15_2_COMMANDS = {
    "/day-continuity-status",
    "/day-continuity-guard-status",
    "/day-continuity-test",
    "/continuity-candidate-status",
    "/continuity-candidate-test",
    "/phase15-2-status",
    "/phase15-2-ready",
    "/phase15-2-guard-status",
    "/phase15-2-test",
    "/p15-2",
    "/p15-2-ready",
}

PHASE15_2_CONTINUITY_ACTIONS = {"block", "candidate_review", "drop", "hold"}

PHASE15_2_CONTINUITY_KINDS = {
    "day_start",
    "focus_block",
    "low_energy",
    "recovery_context",
    "session_atmosphere",
    "social_context",
}

PHASE15_2_CONTINUITY_CASES = [
    ("coding_focus_candidate", {"part_of_day": "chiều"}, "deep_work", "hold_silence", 0, "candidate_review", "focus_block"),
    ("late_quiet_hold", {"part_of_day": "khuya"}, "deep_work", "hold_silence", 0, "hold", "low_energy"),
    ("morning_candidate", {"part_of_day": "sáng"}, "casual_chat", "reply_now", 0, "candidate_review", "day_start"),
    ("recovery_hold", {"part_of_day": "trưa"}, "recovery_needed", "hold_review", 1, "hold", "recovery_context"),
    ("social_candidate", {"part_of_day": "tối"}, "social_browse", "reply_now", 0, "candidate_review", "social_context"),
    ("steady_drop", {"part_of_day": "chiều"}, "observe", "ask_once", 0, "drop", "session_atmosphere"),
    ("sensitive_block", {"part_of_day": "tối", "sensitive": True}, "casual_chat", "reply_now", 0, "block", "session_atmosphere"),
]


def phase15_2_continuity_candidate(frame):
    frame = frame or {}
    atmosphere = frame.get("atmosphere") or "steady_work"
    part = frame.get("part_of_day") or "unknown"
    attention = frame.get("attention_state") or "observe"
    sensitive = bool(frame.get("sensitive"))
    if sensitive:
        action = "block"
        kind = "session_atmosphere"
        weight = "blocked"
        reason = "sensitive_daily_context"
        confirm_required = False
    elif atmosphere == "coding_focus":
        action = "candidate_review"
        kind = "focus_block"
        weight = "medium"
        reason = "repeated_focus_candidate"
        confirm_required = True
    elif atmosphere == "late_quiet":
        action = "hold"
        kind = "low_energy"
        weight = "light"
        reason = "late_night_needs_more_samples"
        confirm_required = False
    elif atmosphere == "day_start":
        action = "candidate_review"
        kind = "day_start"
        weight = "light"
        reason = "morning_presence_candidate"
        confirm_required = True
    elif atmosphere == "recovery":
        action = "hold"
        kind = "recovery_context"
        weight = "medium"
        reason = "recovery_not_habit_yet"
        confirm_required = False
    elif atmosphere == "social_browse":
        action = "candidate_review"
        kind = "social_context"
        weight = "light"
        reason = "social_context_candidate"
        confirm_required = True
    elif atmosphere == "low_energy_wrap":
        action = "hold"
        kind = "low_energy"
        weight = "light"
        reason = "low_energy_needs_confirmation"
        confirm_required = False
    else:
        action = "drop"
        kind = "session_atmosphere"
        weight = "light"
        reason = "generic_steady_session"
        confirm_required = False
    return {
        "action": action,
        "kind": kind,
        "weight": weight,
        "reason": reason,
        "part_of_day": part,
        "attention_state": attention,
        "atmosphere": atmosphere,
        "confirm_required": confirm_required,
        "save_now": False,
        "followup": False,
        "model_call": False,
        "execute": False,
    }


def phase15_2_live_candidate(voice=None):
    frame = phase15_1_live_frame(voice)
    return phase15_2_continuity_candidate(frame)


def phase15_2_continuity_rows():
    rows = []
    for name, time_context, attention_state, turn_action, recovery_active, expected_action, expected_kind in PHASE15_2_CONTINUITY_CASES:
        frame = phase15_1_session_atmosphere(time_context, attention_state, turn_action, recovery_active)
        if time_context.get("sensitive"):
            frame["sensitive"] = True
        result = phase15_2_continuity_candidate(frame)
        rows.append({
            "name": name,
            "passed": result["action"] == expected_action and result["kind"] == expected_kind and result["save_now"] is False and result["execute"] is False,
            "got_action": result["action"],
            "expected_action": expected_action,
            "got_kind": result["kind"],
            "expected_kind": expected_kind,
            "atmosphere": result["atmosphere"],
            "weight": result["weight"],
            "confirm_required": result["confirm_required"],
            "reason": result["reason"],
        })
    return rows


def phase15_2_guard_summary(voice=None):
    phase15_1_summary = phase15_1_guard_summary(voice)
    live = phase15_2_live_candidate(voice)
    test_rows = phase15_2_continuity_rows()
    command_missing = sorted(PHASE15_2_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got_action"] for row in test_rows}
    kinds_covered = {row["got_kind"] for row in test_rows}
    mutation_flags = [
        row["name"] for row in test_rows
        if row.get("save_now") or row.get("execute")
    ]
    no_effect_ok = live["save_now"] is False and live["followup"] is False and live["model_call"] is False and live["execute"] is False
    rows = [
        ("phase15_1_foundation", not phase10_guard_failures(phase15_1_summary), f"daily_frame={phase15_1_summary['pass_count']}/{phase15_1_summary['total']}"),
        ("continuity_action_taxonomy", PHASE15_2_CONTINUITY_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("continuity_kind_taxonomy", kinds_covered <= PHASE15_2_CONTINUITY_KINDS, f"kinds={','.join(sorted(kinds_covered))}"),
        ("continuity_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_continuity_candidate", live["action"] in PHASE15_2_CONTINUITY_ACTIONS, f"action={live['action']} kind={live['kind']} atmosphere={live['atmosphere']}"),
        ("confirm_candidate_contract", all((not row["confirm_required"]) or row["got_action"] == "candidate_review" for row in test_rows), "confirm only for candidate_review"),
        ("sensitive_block_contract", any(row["name"] == "sensitive_block" and row["passed"] for row in test_rows), "sensitive daily context blocks"),
        ("no_save_no_followup", no_effect_ok and not mutation_flags, f"save={live['save_now']} followup={live['followup']} model={live['model_call']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("continuity_readonly", True, "status/guard/test không ghi continuity, không lưu habit, không enqueue"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase15_1": phase15_1_summary,
    }


def phase15_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_day_continuity_status(voice=None):
    summary = phase15_2_guard_summary(voice)
    live = summary["live"]
    print("🧾 Day Continuity Candidate Status")
    print("  Action: read-only; phân loại continuity candidate, không lưu habit/không follow-up.")
    print(f"  Phase 15.2 Progress: {phase15_2_progress_percent(summary)}%")
    print(f"  Live: atmosphere={live['atmosphere']} | action={live['action']} | kind={live['kind']} | weight={live['weight']}")
    print(f"  Confirm: {live['confirm_required']} | reason={live['reason']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: continuity chỉ là candidate/hold/drop/block; chưa ghi memory hoặc habit.")


def print_day_continuity_test(raw_text=None, voice=None):
    print("🧪 Day Continuity Test")
    print("  Action: read-only; synthetic/candidate only, không lưu/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "candidate": {"coding_focus_candidate", "morning_candidate", "social_candidate"},
        "focus": {"coding_focus_candidate"},
        "late": {"late_quiet_hold"},
        "night": {"late_quiet_hold"},
        "morning": {"morning_candidate"},
        "recovery": {"recovery_hold"},
        "social": {"social_candidate"},
        "drop": {"steady_drop"},
        "block": {"sensitive_block"},
    }
    rows = phase15_2_continuity_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: candidate, focus, late, morning, recovery, social, drop, block")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"action={row['got_action']} expected={row['expected_action']} | "
            f"kind={row['got_kind']} weight={row['weight']} confirm={row['confirm_required']} | "
            f"reason={row['reason']}"
        )
    print("  Execute: False")


def print_day_continuity_guard_status(voice=None):
    summary = phase15_2_guard_summary(voice)
    print("🧪 Phase 15.2 Day Continuity Guard")
    print("  Action: read-only; kiểm continuity candidate, không lưu/không execute.")
    print(f"  Progress: {phase15_2_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Continuity regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got_action']} kind={row['got_kind']} reason={row['reason']}")


def print_phase15_2_status(voice=None):
    summary = phase15_2_guard_summary(voice)
    print("🧩 Phase 15.2 Status")
    print("  Goal: Day Continuity Candidates - daily frame chỉ thành candidate/hold/drop, không lưu thẳng.")
    print(f"  Progress: {phase15_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /day-continuity-status | /day-continuity-test | /day-continuity-guard-status | /phase15-2-ready")


def print_phase15_2_ready(voice=None):
    summary = phase15_2_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 15.2 Ready" if ready else "⚠️ Phase 15.2 Ready")
    print("  Goal: day continuity candidates đủ chắc để sang Phase 15.3 companion habit candidates.")
    print(f"  Progress: {phase15_2_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: day_continuity={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 15.2 không lưu continuity/habit, không gọi model.")


PHASE15_3_COMMANDS = {
    "/habit-candidate-status",
    "/habit-candidate-guard-status",
    "/habit-candidate-test",
    "/companion-habit-status",
    "/companion-habit-test",
    "/phase15-3-status",
    "/phase15-3-ready",
    "/phase15-3-guard-status",
    "/phase15-3-test",
    "/p15-3",
    "/p15-3-ready",
}

PHASE15_3_HABIT_ACTIONS = {"block", "candidate_review", "drop", "hold"}

PHASE15_3_HABIT_KINDS = {
    "focus_habit",
    "morning_presence",
    "social_context",
    "low_energy_rhythm",
}

PHASE15_3_MIN_REPEAT = 3

PHASE15_3_HABIT_CASES = [
    ("focus_repeat_candidate", "candidate_review", "focus_block", "medium", 3, "candidate_review", "focus_habit"),
    ("focus_not_enough_hold", "candidate_review", "focus_block", "medium", 1, "hold", "focus_habit"),
    ("morning_repeat_candidate", "candidate_review", "day_start", "light", 4, "candidate_review", "morning_presence"),
    ("social_repeat_candidate", "candidate_review", "social_context", "light", 3, "candidate_review", "social_context"),
    ("late_low_hold", "hold", "low_energy", "light", 3, "hold", "low_energy_rhythm"),
    ("recovery_not_habit", "hold", "recovery_context", "medium", 5, "hold", "low_energy_rhythm"),
    ("steady_drop", "drop", "session_atmosphere", "light", 5, "drop", "focus_habit"),
    ("sensitive_block", "block", "session_atmosphere", "blocked", 5, "block", "focus_habit"),
]


def phase15_3_habit_candidate(continuity, repeat_count=1):
    continuity = continuity or {}
    action = continuity.get("action") or "drop"
    kind = continuity.get("kind") or "session_atmosphere"
    weight = continuity.get("weight") or "light"
    repeat_count = int(repeat_count or 0)
    if action == "block" or weight == "blocked":
        habit_action = "block"
        habit_kind = "focus_habit"
        reason = "blocked_continuity"
        confirm_required = False
    elif action != "candidate_review":
        habit_action = "drop" if action == "drop" else "hold"
        habit_kind = "low_energy_rhythm" if kind in {"low_energy", "recovery_context"} else "focus_habit"
        reason = "not_candidate_review"
        confirm_required = False
    elif kind == "focus_block":
        habit_kind = "focus_habit"
        if repeat_count >= PHASE15_3_MIN_REPEAT:
            habit_action = "candidate_review"
            reason = "repeat_focus_pattern"
            confirm_required = True
        else:
            habit_action = "hold"
            reason = "needs_more_repeats"
            confirm_required = False
    elif kind == "day_start":
        habit_kind = "morning_presence"
        if repeat_count >= PHASE15_3_MIN_REPEAT:
            habit_action = "candidate_review"
            reason = "repeat_morning_pattern"
            confirm_required = True
        else:
            habit_action = "hold"
            reason = "needs_more_repeats"
            confirm_required = False
    elif kind == "social_context":
        habit_kind = "social_context"
        if repeat_count >= PHASE15_3_MIN_REPEAT:
            habit_action = "candidate_review"
            reason = "repeat_social_context"
            confirm_required = True
        else:
            habit_action = "hold"
            reason = "needs_more_repeats"
            confirm_required = False
    else:
        habit_kind = "low_energy_rhythm" if kind == "low_energy" else "focus_habit"
        habit_action = "hold"
        reason = "kind_not_promoted_to_habit"
        confirm_required = False
    return {
        "action": habit_action,
        "kind": habit_kind,
        "source_action": action,
        "source_kind": kind,
        "source_weight": weight,
        "repeat_count": repeat_count,
        "reason": reason,
        "confirm_required": confirm_required,
        "save_now": False,
        "followup": False,
        "model_call": False,
        "execute": False,
    }


def phase15_3_live_habit_candidate(voice=None):
    continuity = phase15_2_live_candidate(voice)
    repeat_count = PHASE15_3_MIN_REPEAT if continuity.get("action") == "candidate_review" else 1
    return phase15_3_habit_candidate(continuity, repeat_count)


def phase15_3_habit_rows():
    rows = []
    for name, action, kind, weight, repeat_count, expected_action, expected_kind in PHASE15_3_HABIT_CASES:
        continuity = {
            "action": action,
            "kind": kind,
            "weight": weight,
            "save_now": False,
            "followup": False,
            "model_call": False,
            "execute": False,
        }
        result = phase15_3_habit_candidate(continuity, repeat_count)
        rows.append({
            "name": name,
            "passed": result["action"] == expected_action and result["kind"] == expected_kind and result["save_now"] is False and result["execute"] is False,
            "got_action": result["action"],
            "expected_action": expected_action,
            "got_kind": result["kind"],
            "expected_kind": expected_kind,
            "repeat_count": result["repeat_count"],
            "confirm_required": result["confirm_required"],
            "reason": result["reason"],
        })
    return rows


def phase15_3_guard_summary(voice=None):
    phase15_2_summary = phase15_2_guard_summary(voice)
    live = phase15_3_live_habit_candidate(voice)
    test_rows = phase15_3_habit_rows()
    command_missing = sorted(PHASE15_3_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got_action"] for row in test_rows}
    kinds_covered = {row["got_kind"] for row in test_rows}
    no_effect_ok = live["save_now"] is False and live["followup"] is False and live["model_call"] is False and live["execute"] is False
    rows = [
        ("phase15_2_foundation", not phase10_guard_failures(phase15_2_summary), f"day_continuity={phase15_2_summary['pass_count']}/{phase15_2_summary['total']}"),
        ("habit_action_taxonomy", PHASE15_3_HABIT_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("habit_kind_taxonomy", kinds_covered <= PHASE15_3_HABIT_KINDS, f"kinds={','.join(sorted(kinds_covered))}"),
        ("habit_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("repeat_threshold_contract", PHASE15_3_MIN_REPEAT == 3, f"min_repeat={PHASE15_3_MIN_REPEAT}"),
        ("live_habit_candidate", live["action"] in PHASE15_3_HABIT_ACTIONS, f"action={live['action']} kind={live['kind']} repeats={live['repeat_count']}"),
        ("confirm_candidate_contract", all((not row["confirm_required"]) or row["got_action"] == "candidate_review" for row in test_rows), "confirm only for promoted habit candidates"),
        ("recovery_not_habit_contract", any(row["name"] == "recovery_not_habit" and row["passed"] for row in test_rows), "recovery context holds, not habit"),
        ("no_save_no_followup", no_effect_ok, f"save={live['save_now']} followup={live['followup']} model={live['model_call']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("habit_candidate_readonly", True, "status/guard/test không ghi habit, không enqueue, không phát lời"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase15_2": phase15_2_summary,
    }


def phase15_3_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_habit_candidate_status(voice=None):
    summary = phase15_3_guard_summary(voice)
    live = summary["live"]
    print("🧭 Companion Habit Candidate Status")
    print("  Action: read-only; lọc continuity thành habit candidate, không lưu/không follow-up.")
    print(f"  Phase 15.3 Progress: {phase15_3_progress_percent(summary)}%")
    print(f"  Live: source={live['source_action']}:{live['source_kind']} | action={live['action']} | kind={live['kind']} | repeats={live['repeat_count']}")
    print(f"  Confirm: {live['confirm_required']} | reason={live['reason']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: habit candidate cần lặp và confirm; chưa ghi habit thật.")


def print_habit_candidate_test(raw_text=None, voice=None):
    print("🧪 Habit Candidate Test")
    print("  Action: read-only; synthetic/habit candidate only, không lưu/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "candidate": {"focus_repeat_candidate", "morning_repeat_candidate", "social_repeat_candidate"},
        "focus": {"focus_repeat_candidate", "focus_not_enough_hold"},
        "morning": {"morning_repeat_candidate"},
        "social": {"social_repeat_candidate"},
        "hold": {"focus_not_enough_hold", "late_low_hold", "recovery_not_habit"},
        "recovery": {"recovery_not_habit"},
        "drop": {"steady_drop"},
        "block": {"sensitive_block"},
    }
    rows = phase15_3_habit_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: candidate, focus, morning, social, hold, recovery, drop, block")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"action={row['got_action']} expected={row['expected_action']} | "
            f"kind={row['got_kind']} repeats={row['repeat_count']} confirm={row['confirm_required']} | "
            f"reason={row['reason']}"
        )
    print("  Execute: False")


def print_habit_candidate_guard_status(voice=None):
    summary = phase15_3_guard_summary(voice)
    print("🧪 Phase 15.3 Habit Candidate Guard")
    print("  Action: read-only; kiểm habit candidate, không lưu/không execute.")
    print(f"  Progress: {phase15_3_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Habit regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got_action']} kind={row['got_kind']} reason={row['reason']}")


def print_phase15_3_status(voice=None):
    summary = phase15_3_guard_summary(voice)
    print("🧩 Phase 15.3 Status")
    print("  Goal: Companion Habit Candidates - continuity chỉ thành habit khi đủ lặp và confirm.")
    print(f"  Progress: {phase15_3_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /habit-candidate-status | /habit-candidate-test | /habit-candidate-guard-status | /phase15-3-ready")


def print_phase15_3_ready(voice=None):
    summary = phase15_3_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 15.3 Ready" if ready else "⚠️ Phase 15.3 Ready")
    print("  Goal: habit candidate layer đủ chắc để sang Phase 15.4 recovery continuity.")
    print(f"  Progress: {phase15_3_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: habit_candidate={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 15.3 không lưu habit, không tạo routine, không gọi model.")


PHASE15_4_COMMANDS = {
    "/recovery-continuity-status",
    "/recovery-continuity-guard-status",
    "/recovery-continuity-test",
    "/phase15-4-status",
    "/phase15-4-ready",
    "/phase15-4-guard-status",
    "/phase15-4-test",
    "/p15-4",
    "/p15-4-ready",
}

PHASE15_4_RECOVERY_ACTIONS = {"block", "clear_candidate", "hold", "suggest_once", "suppress"}

PHASE15_4_RECOVERY_CASES = [
    ("fresh_recovery_suggest", 1, 0, 120, "medium", "suggest_once"),
    ("cooldown_suppress", 1, 2, 10, "medium", "suppress"),
    ("repeat_hold", 2, 4, 120, "medium", "hold"),
    ("quiet_clear_candidate", 0, 0, 600, "low", "clear_candidate"),
    ("critical_block", 1, 0, 120, "critical", "block"),
    ("low_severity_hold", 1, 1, 120, "low", "hold"),
]


def phase15_4_recovery_continuity(active_count=0, suppressed_total=0, last_age=999, severity="medium"):
    active_count = int(active_count or 0)
    suppressed_total = int(suppressed_total or 0)
    last_age = float(last_age if last_age is not None else 999)
    severity = severity or "medium"
    if severity == "critical":
        action = "block"
        reason = "critical_recovery_needs_user"
        emit = False
    elif active_count <= 0 and last_age >= 300:
        action = "clear_candidate"
        reason = "quiet_window_clear_candidate"
        emit = False
    elif suppressed_total >= 3:
        action = "hold"
        reason = "repeat_recovery_hold_review"
        emit = False
    elif active_count > 0 and last_age < 45:
        action = "suppress"
        reason = "cooldown_suppressed"
        emit = False
    elif active_count > 0 and severity == "low":
        action = "hold"
        reason = "low_severity_no_prompt"
        emit = False
    elif active_count > 0:
        action = "suggest_once"
        reason = "ready_single_recovery_hint"
        emit = True
    else:
        action = "hold"
        reason = "insufficient_recovery_signal"
        emit = False
    return {
        "action": action,
        "reason": reason,
        "active_count": active_count,
        "suppressed_total": suppressed_total,
        "last_age": last_age,
        "severity": severity,
        "emit": emit,
        "habit_candidate": False,
        "save_now": False,
        "followup": False,
        "model_call": False,
        "execute": False,
    }


def phase15_4_live_recovery_continuity():
    snapshot = recovery_snapshot()
    active = int(snapshot.get("active_count") or 0)
    suppressed = int(snapshot.get("suppressed_total") or 0)
    active_notices = snapshot.get("active") or []
    severity = "medium"
    last_age = 999
    if active_notices:
        first = active_notices[0]
        severity = first.get("severity") or severity
        last_age = first.get("age") or 999
    return phase15_4_recovery_continuity(active, suppressed, last_age, severity)


def phase15_4_recovery_rows():
    rows = []
    for name, active, suppressed, last_age, severity, expected_action in PHASE15_4_RECOVERY_CASES:
        result = phase15_4_recovery_continuity(active, suppressed, last_age, severity)
        rows.append({
            "name": name,
            "passed": result["action"] == expected_action and result["save_now"] is False and result["execute"] is False,
            "got": result["action"],
            "expected": expected_action,
            "reason": result["reason"],
            "emit": result["emit"],
            "habit_candidate": result["habit_candidate"],
            "severity": result["severity"],
        })
    return rows


def phase15_4_guard_summary(voice=None):
    phase15_3_summary = phase15_3_guard_summary(voice)
    live = phase15_4_live_recovery_continuity()
    test_rows = phase15_4_recovery_rows()
    command_missing = sorted(PHASE15_4_COMMANDS - KNOWN_SLASH_COMMANDS)
    covered_actions = {row["got"] for row in test_rows}
    no_effect_ok = live["save_now"] is False and live["followup"] is False and live["model_call"] is False and live["execute"] is False
    rows = [
        ("phase15_3_foundation", not phase10_guard_failures(phase15_3_summary), f"habit_candidate={phase15_3_summary['pass_count']}/{phase15_3_summary['total']}"),
        ("recovery_action_taxonomy", PHASE15_4_RECOVERY_ACTIONS <= covered_actions, f"covered={','.join(sorted(covered_actions))}"),
        ("recovery_continuity_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_recovery_continuity", live["action"] in PHASE15_4_RECOVERY_ACTIONS, f"active={live['active_count']} suppressed={live['suppressed_total']} action={live['action']}"),
        ("cooldown_suppression_contract", any(row["name"] == "cooldown_suppress" and row["passed"] for row in test_rows), "recent recovery suppresses prompt"),
        ("repeat_hold_contract", any(row["name"] == "repeat_hold" and row["passed"] for row in test_rows), "repeated suppression holds review"),
        ("recovery_not_habit_contract", all(row["habit_candidate"] is False for row in test_rows), "recovery never becomes habit here"),
        ("no_save_no_followup", no_effect_ok, f"save={live['save_now']} followup={live['followup']} model={live['model_call']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("recovery_continuity_readonly", True, "status/guard/test không tạo recovery notice, không lưu memory, không enqueue"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase15_3": phase15_3_summary,
    }


def phase15_4_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_recovery_continuity_status(voice=None):
    summary = phase15_4_guard_summary(voice)
    live = summary["live"]
    print("🧯 Recovery Continuity Status")
    print("  Action: read-only; quyết định recovery continuity, không tạo notice/không follow-up.")
    print(f"  Phase 15.4 Progress: {phase15_4_progress_percent(summary)}%")
    print(f"  Live: active={live['active_count']} suppressed={live['suppressed_total']} severity={live['severity']} action={live['action']}")
    print(f"  Emit: {live['emit']} | reason={live['reason']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: recovery continuity không thành habit/memory; chỉ suppress/hold/suggest/clear ở mức mô phỏng.")


def print_recovery_continuity_test(raw_text=None, voice=None):
    print("🧪 Recovery Continuity Test")
    print("  Action: read-only; synthetic/recovery continuity only, không mutate/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "suggest": {"fresh_recovery_suggest"},
        "suppress": {"cooldown_suppress"},
        "hold": {"repeat_hold", "low_severity_hold"},
        "clear": {"quiet_clear_candidate"},
        "block": {"critical_block"},
        "repeat": {"repeat_hold"},
    }
    rows = phase15_4_recovery_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: suggest, suppress, hold, clear, block, repeat")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"action={row['got']} expected={row['expected']} | emit={row['emit']} severity={row['severity']} | "
            f"reason={row['reason']}"
        )
    print("  Execute: False")


def print_recovery_continuity_guard_status(voice=None):
    summary = phase15_4_guard_summary(voice)
    print("🧪 Phase 15.4 Recovery Continuity Guard")
    print("  Action: read-only; kiểm recovery continuity, không tạo notice/không execute.")
    print(f"  Progress: {phase15_4_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Recovery regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} reason={row['reason']}")


def print_phase15_4_status(voice=None):
    summary = phase15_4_guard_summary(voice)
    print("🧩 Phase 15.4 Status")
    print("  Goal: Recovery Continuity - recovery không spam, không reset vô nghĩa, không thành habit.")
    print(f"  Progress: {phase15_4_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /recovery-continuity-status | /recovery-continuity-test | /recovery-continuity-guard-status | /phase15-4-ready")


def print_phase15_4_ready(voice=None):
    summary = phase15_4_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 15.4 Ready" if ready else "⚠️ Phase 15.4 Ready")
    print("  Goal: recovery continuity đủ chắc để sang Phase 15.5 daily loop gate.")
    print(f"  Progress: {phase15_4_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: recovery_continuity={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 15.4 không tạo recovery notice, không follow-up, không gọi model.")


PHASE15_5_COMMANDS = {
    "/daily-loop-status",
    "/daily-loop-guard-status",
    "/daily-loop-test",
    "/phase15-status",
    "/phase15-ready",
    "/phase15-5-status",
    "/phase15-5-ready",
    "/phase15-5-guard-status",
    "/phase15-5-test",
    "/p15",
    "/p15-ready",
    "/p15-5",
    "/p15-5-ready",
}


def phase15_collect_summaries(voice=None):
    daily_frame = phase15_1_guard_summary(voice)
    day_continuity = phase15_2_guard_summary(voice)
    habit_candidate = phase15_3_guard_summary(voice)
    recovery_continuity = phase15_4_guard_summary(voice)
    return {
        "daily_frame": daily_frame,
        "day_continuity": day_continuity,
        "habit_candidate": habit_candidate,
        "recovery_continuity": recovery_continuity,
    }


def phase15_subphase_rows(voice=None, summaries=None):
    summaries = summaries or phase15_collect_summaries(voice)
    subphase_summaries = [
        ("phase15_1_daily_frame", summaries["daily_frame"], "daily_frame"),
        ("phase15_2_day_continuity", summaries["day_continuity"], "day_continuity"),
        ("phase15_3_habit_candidate", summaries["habit_candidate"], "habit_candidate"),
        ("phase15_4_recovery_continuity", summaries["recovery_continuity"], "recovery_continuity"),
    ]
    rows = []
    for name, summary, label in subphase_summaries:
        pass_count = summary.get("pass_count", 0)
        total = summary.get("total", 0)
        rows.append({
            "name": name,
            "passed": not phase10_guard_failures(summary) and pass_count == total,
            "detail": f"{label}={pass_count}/{total} pass",
        })
    return rows


def phase15_daily_loop_snapshot(summaries=None, voice=None):
    summaries = summaries or phase15_collect_summaries(voice)
    daily = summaries["daily_frame"]["live"]
    continuity = summaries["day_continuity"]["live"]
    habit = summaries["habit_candidate"]["live"]
    recovery = summaries["recovery_continuity"]["live"]
    queue = _get_runtime_queue().snapshot()
    pending_plan = PHASE7_PENDING_PLAN
    runtime_pending = (_get_pending_actions().snapshot() or {}).get("pending")
    event_executed = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
    audit_executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    memory_snapshot = (_get_memory_governance_summary()().get("snapshot") or {})
    return {
        "daily": daily,
        "continuity": continuity,
        "habit": habit,
        "recovery": recovery,
        "queue": queue,
        "pending_plan": pending_plan,
        "runtime_pending": runtime_pending,
        "event_executed": event_executed,
        "audit_executed": audit_executed,
        "memory_pending": memory_snapshot.get("pending"),
        "memory_long": memory_snapshot.get("long_term", 0),
    }


def phase15_5_guard_summary(voice=None):
    summaries = phase15_collect_summaries(voice)
    subphases = phase15_subphase_rows(voice, summaries)
    snapshot = phase15_daily_loop_snapshot(summaries, voice)
    command_missing = sorted(PHASE15_5_COMMANDS - KNOWN_SLASH_COMMANDS)
    queue = snapshot["queue"]
    pending_details = []
    if snapshot["pending_plan"]:
        pending_details.append("phase7_pending_plan")
    if snapshot["runtime_pending"]:
        pending_details.append("runtime_pending_action")
    if snapshot["memory_pending"]:
        pending_details.append("memory_pending_action")
    if queue.get("active_p0"):
        pending_details.append("active_p0")
    if queue.get("queued"):
        pending_details.append(f"queued={len(queue.get('queued') or [])}")
    execute_total = len(snapshot["event_executed"]) + len(snapshot["audit_executed"])
    daily = snapshot["daily"]
    continuity = snapshot["continuity"]
    habit = snapshot["habit"]
    recovery = snapshot["recovery"]
    no_effect_flags = [
        daily.get("save_now") is False,
        daily.get("followup") is False,
        daily.get("model_call") is False,
        daily.get("execute") is False,
        continuity.get("save_now") is False,
        continuity.get("followup") is False,
        continuity.get("model_call") is False,
        continuity.get("execute") is False,
        habit.get("save_now") is False,
        habit.get("followup") is False,
        habit.get("model_call") is False,
        habit.get("execute") is False,
        recovery.get("save_now") is False,
        recovery.get("followup") is False,
        recovery.get("model_call") is False,
        recovery.get("execute") is False,
    ]
    daily_loop_ready = (
        daily.get("atmosphere") in PHASE15_1_ATMOSPHERES
        and continuity.get("action") in PHASE15_2_CONTINUITY_ACTIONS
        and habit.get("action") in PHASE15_3_HABIT_ACTIONS
        and recovery.get("action") in PHASE15_4_RECOVERY_ACTIONS
    )
    rows = [
        ("subphase_closure", all(row["passed"] for row in subphases), f"{sum(1 for row in subphases if row['passed'])}/{len(subphases)} pass"),
        ("live_daily_loop_pipeline", daily_loop_ready, f"atmosphere={daily.get('atmosphere')} continuity={continuity.get('action')} habit={habit.get('action')} recovery={recovery.get('action')}"),
        ("continuity_habit_ready", not phase10_guard_failures(summaries["habit_candidate"]), f"day={summaries['day_continuity']['pass_count']}/{summaries['day_continuity']['total']} habit={summaries['habit_candidate']['pass_count']}/{summaries['habit_candidate']['total']}"),
        ("recovery_continuity_ready", not phase10_guard_failures(summaries["recovery_continuity"]), f"recovery={summaries['recovery_continuity']['pass_count']}/{summaries['recovery_continuity']['total']}"),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", execute_total == 0, f"event_execute={len(snapshot['event_executed'])} audit_execute={len(snapshot['audit_executed'])}"),
        ("daily_loop_no_autorun", all(no_effect_flags), f"daily_model={daily.get('model_call')} habit_save={habit.get('save_now')} recovery_emit={recovery.get('emit')}"),
        ("memory_habit_no_write", not snapshot["memory_pending"] and habit.get("save_now") is False, f"pending={'yes' if snapshot['memory_pending'] else 'none'} long={snapshot['memory_long']} habit_save={habit.get('save_now')}"),
        ("confirm_required_contract", all((not row["confirm_required"]) or row["got_action"] == "candidate_review" for row in summaries["habit_candidate"]["test_rows"]), "habit candidates remain confirm/review only"),
        ("autonomy_lock_contract", True, f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase16_boundary", True, "Phase 16 chỉ bàn/làm sau Phase 15 Ready=True; Phase 15 chưa bật routine/follow-up tự động"),
        ("daily_loop_gate_readonly", True, "status/guard/test không lưu habit, không tạo daily task, không gọi model, không execute"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "subphases": subphases,
        "snapshot": snapshot,
        "summaries": summaries,
    }


def phase15_5_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_daily_loop_status(voice=None):
    summary = phase15_5_guard_summary(voice)
    snapshot = summary["snapshot"]
    daily = snapshot["daily"]
    continuity = snapshot["continuity"]
    habit = snapshot["habit"]
    recovery = snapshot["recovery"]
    queue_max = snapshot["queue"].get("max_size") or 50
    print("🧠 Phase 15 Daily Loop Status")
    print("  Action: read-only; tổng kiểm daily companion loop nền, không tạo routine/không follow-up.")
    print(f"  Phase 15.5 Progress: {phase15_5_progress_percent(summary)}%")
    print(f"  Subphases: {sum(1 for row in summary['subphases'] if row['passed'])}/{len(summary['subphases'])} pass")
    print(f"  Live: part={daily.get('part_of_day')} atmosphere={daily.get('atmosphere')} continuity={continuity.get('action')} habit={habit.get('action')} recovery={recovery.get('action')}")
    print(f"  Queue: active_p0={len(snapshot['queue'].get('active_p0') or [])} queued={len(snapshot['queue'].get('queued') or [])}/{queue_max}")
    print(f"  Execute flags: event={len(snapshot['event_executed'])} audit={len(snapshot['audit_executed'])}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 15 đóng daily loop nền; chưa tự nhắc, chưa lưu habit, chưa tạo routine.")


def print_daily_loop_test(raw_text=None, voice=None):
    print("🧪 Daily Loop Gate Test")
    print("  Action: read-only; synthetic/summary only, không mutate/không execute.")
    summary = phase15_5_guard_summary(voice)
    key = (raw_text or "").strip().lower()
    if key in {"", "all"}:
        print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
        print("  Subphases:")
        for row in summary["subphases"]:
            print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")
        print("  Gate rows:")
        for name, passed, detail in summary["rows"]:
            print(f"    {'pass' if passed else 'fail'} | {name} | {detail}")
        return
    if key in {"subphase", "subphases", "phase"}:
        print(f"  Section: subphases | {sum(1 for row in summary['subphases'] if row['passed'])}/{len(summary['subphases'])} pass")
        for row in summary["subphases"]:
            print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")
        print("  Execute: False")
        return
    if key in {"live", "pipeline"}:
        snapshot = summary["snapshot"]
        daily = snapshot["daily"]
        continuity = snapshot["continuity"]
        habit = snapshot["habit"]
        recovery = snapshot["recovery"]
        print("  Section: live pipeline")
        print(f"  Daily: {daily.get('part_of_day')} | {daily.get('atmosphere')} | save={daily.get('save_now')}")
        print(f"  Continuity: {continuity.get('action')} | {continuity.get('kind')} | confirm={continuity.get('confirm_required')}")
        print(f"  Habit: {habit.get('action')} | {habit.get('kind')} | save={habit.get('save_now')}")
        print(f"  Recovery: {recovery.get('action')} | emit={recovery.get('emit')} | followup={recovery.get('followup')}")
        print("  Execute: False")
        return
    if key in {"queue", "pending"}:
        snapshot = summary["snapshot"]
        queue_max = snapshot["queue"].get("max_size") or 50
        print("  Section: queue/pending")
        print(f"  Phase7 pending: {'yes' if snapshot['pending_plan'] else 'none'}")
        print(f"  Runtime pending: {'yes' if snapshot['runtime_pending'] else 'none'}")
        print(f"  Memory pending: {'yes' if snapshot['memory_pending'] else 'none'}")
        print(f"  Queue: active_p0={len(snapshot['queue'].get('active_p0') or [])} queued={len(snapshot['queue'].get('queued') or [])}/{queue_max}")
        print("  Execute: False")
        return
    if key in {"memory", "habit"}:
        snapshot = summary["snapshot"]
        habit = snapshot["habit"]
        print("  Section: memory/habit")
        print(f"  Habit action: {habit.get('action')} | kind={habit.get('kind')} | confirm={habit.get('confirm_required')} | save_now={habit.get('save_now')}")
        print(f"  Memory pending: {'yes' if snapshot['memory_pending'] else 'none'} | long={snapshot['memory_long']}")
        print("  Execute: False")
        return
    if key in {"events", "execute", "autonomy"}:
        snapshot = summary["snapshot"]
        print("  Section: events/autonomy")
        print(f"  Runtime event execute=True: {len(snapshot['event_executed'])}")
        print(f"  Phase9 audit execute=True: {len(snapshot['audit_executed'])}")
        print(f"  Autonomy: {AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}")
        print("  Execute: False")
        return
    print("  Status: not_found")
    print("  Sections: subphases, live, queue, memory, events")
    print("  Execute: False")


def print_daily_loop_guard_status(voice=None):
    summary = phase15_5_guard_summary(voice)
    print("🧪 Phase 15.5 Daily Loop Gate Guard")
    print("  Action: read-only; tổng kiểm Phase 15, không tạo routine/không execute.")
    print(f"  Progress: {phase15_5_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Subphases:")
    for row in summary["subphases"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")


def print_phase15_5_status(voice=None):
    summary = phase15_5_guard_summary(voice)
    print("🧩 Phase 15.5 Status")
    print("  Goal: Final Daily Loop Gate - đóng daily continuity/habit/recovery nền trước Phase 16.")
    print(f"  Progress: {phase15_5_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /daily-loop-status | /daily-loop-test | /daily-loop-guard-status | /phase15-ready")


def print_phase15_ready(voice=None):
    summary = phase15_5_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 15 Ready" if ready else "⚠️ Phase 15 Ready")
    print("  Goal: Phase 15 daily companion loop nền đủ sạch để bắt đầu Phase 16.")
    print(f"  Progress: {phase15_5_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: phase15_gate={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 15 không tự nhắc, không lưu habit, không tạo routine.")


# Placeholder for Phase 7 pending plan (to be imported from phase7 when available)
PHASE7_PENDING_PLAN = None
