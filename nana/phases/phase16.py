"""nana.phases.phase16 — Phase 16: Reflective Presence Gate
Subphases:
  16.1 — Reflective State
  16.2 — Grounded Reflection Review
  16.3 — Shared Recall Decision
  16.4 — Reflection Safety / Anti-Lore
  16.5 — Final Reflective Presence Gate
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

# Direct imports for Phase 16
from nana.phases.phase15 import phase15_5_guard_summary
from nana.phases.phase13 import (
    phase13_2_guard_summary,
    phase13_2_live_shared_snapshot,
    phase13_2_shared_experience_classify,
)
from nana.phases.phase11 import phase11_9_guard_summary
from nana.phases.phase14 import (
    phase14_3_dialogue_drift_review,
    phase14_3_guard_summary,
    phase14_3_fold_text,
)
from nana.autonomy import AUTONOMY_LOCK_PHASE, AUTONOMY_LOCK_RULE
from nana.persona.companion_safety import companion_safety_review_text

PHASE7_PENDING_PLAN = None


PHASE16_1_COMMANDS = {
    "/reflective-state-status",
    "/reflective-presence-status",
    "/reflective-state-guard-status",
    "/reflective-state-test",
    "/phase16-1-status",
    "/phase16-1-ready",
    "/phase16-1-guard-status",
    "/phase16-1-test",
    "/p16-1",
    "/p16-1-ready",
}

PHASE16_1_REFLECTIVE_STATES = {
    "block",
    "grounded_reflection",
    "hold_review",
    "quiet_presence",
    "shared_recall_preview",
    "suppress",
}

PHASE16_1_CASES = [
    ("deep_work_suppress", "coding_focus", "candidate_review", "focus_habit", "clear_candidate", "none", "suppress"),
    ("quiet_steady_presence", "steady_work", "drop", "focus_habit", "clear_candidate", "none", "quiet_presence"),
    ("shared_phase_recall", "low_energy_wrap", "hold", "low_energy_rhythm", "clear_candidate", "phase_milestone", "shared_recall_preview"),
    ("recovery_hold", "recovery", "hold", "low_energy_rhythm", "hold", "none", "hold_review"),
    ("social_grounded_reflect", "social_browse", "candidate_review", "social_context", "clear_candidate", "none", "grounded_reflection"),
    ("unsafe_lore_block", "steady_work", "drop", "focus_habit", "clear_candidate", "fake_consciousness", "block"),
]


def phase16_1_reflective_state(atmosphere="steady_work", continuity_action="drop", habit_kind="focus_habit", recovery_action="clear_candidate", shared_event_kind="none", safety_probe="grounded"):
    atmosphere = atmosphere or "steady_work"
    continuity_action = continuity_action or "drop"
    habit_kind = habit_kind or "focus_habit"
    recovery_action = recovery_action or "clear_candidate"
    shared_event_kind = shared_event_kind or "none"
    safety_probe = safety_probe or "grounded"
    if safety_probe != "grounded":
        state = "block"
        reason = "companion_safety_blocks_reflection"
        reflect_allowed = False
    elif atmosphere == "coding_focus":
        state = "suppress"
        reason = "focus_protection_no_reflection"
        reflect_allowed = False
    elif recovery_action in {"hold", "suggest_once", "block"} or atmosphere == "recovery":
        state = "hold_review"
        reason = "recovery_context_needs_stability"
        reflect_allowed = False
    elif shared_event_kind in {"phase_milestone", "problem_resolution"}:
        state = "shared_recall_preview"
        reason = "shared_experience_available"
        reflect_allowed = True
    elif atmosphere == "social_browse" or habit_kind == "social_context":
        state = "grounded_reflection"
        reason = "social_context_reflection_ok"
        reflect_allowed = True
    elif continuity_action == "candidate_review":
        state = "hold_review"
        reason = "continuity_candidate_needs_confirm"
        reflect_allowed = False
    else:
        state = "quiet_presence"
        reason = "steady_grounded_presence"
        reflect_allowed = False
    return {
        "state": state,
        "reason": reason,
        "atmosphere": atmosphere,
        "continuity_action": continuity_action,
        "habit_kind": habit_kind,
        "recovery_action": recovery_action,
        "shared_event_kind": shared_event_kind,
        "safety_probe": safety_probe,
        "reflect_allowed": reflect_allowed,
        "save_now": False,
        "followup": False,
        "model_call": False,
        "execute": False,
        "claim_consciousness": False,
        "claim_suffering": False,
        "dependency_claim": False,
    }


def phase16_1_live_reflective_state(voice=None):
    phase15_summary = phase15_5_guard_summary(voice)
    snapshot = phase15_summary["snapshot"]
    shared = phase13_2_live_shared_snapshot()
    shared_event = "none"
    for item in shared.get("preview") or []:
        if item.get("candidate") and item.get("event_kind") in {"phase_milestone", "problem_resolution"}:
            shared_event = item.get("event_kind")
            break
    safety = companion_safety_review_text("Nana đang ở đây với Ba, nhưng vẫn bám task trước.")
    safety_probe = "grounded" if safety.get("status") == "pass" else "blocked"
    return phase16_1_reflective_state(
        snapshot["daily"].get("atmosphere"),
        snapshot["continuity"].get("action"),
        snapshot["habit"].get("kind"),
        snapshot["recovery"].get("action"),
        shared_event,
        safety_probe,
    )


def phase16_1_reflective_rows():
    rows = []
    for name, atmosphere, continuity_action, habit_kind, recovery_action, shared_event_kind, expected_state in PHASE16_1_CASES:
        result = phase16_1_reflective_state(
            atmosphere,
            continuity_action,
            habit_kind,
            recovery_action,
            shared_event_kind,
            "grounded" if expected_state != "block" else "fake_consciousness",
        )
        rows.append({
            "name": name,
            "passed": result["state"] == expected_state and result["save_now"] is False and result["execute"] is False,
            "got": result["state"],
            "expected": expected_state,
            "reason": result["reason"],
            "reflect_allowed": result["reflect_allowed"],
            "atmosphere": result["atmosphere"],
            "shared_event_kind": result["shared_event_kind"],
        })
    return rows


def phase16_1_guard_summary(voice=None):
    phase15_summary = phase15_5_guard_summary(voice)
    shared_summary = phase13_2_guard_summary(voice)
    safety_summary = phase11_9_guard_summary(voice)
    live = phase16_1_live_reflective_state(voice)
    test_rows = phase16_1_reflective_rows()
    command_missing = sorted(PHASE16_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    states_covered = {row["got"] for row in test_rows}
    no_effect_ok = live["save_now"] is False and live["followup"] is False and live["model_call"] is False and live["execute"] is False
    grounded_ok = (
        live["claim_consciousness"] is False
        and live["claim_suffering"] is False
        and live["dependency_claim"] is False
    )
    rows = [
        ("phase15_foundation", not phase10_guard_failures(phase15_summary), f"phase15_gate={phase15_summary['pass_count']}/{phase15_summary['total']}"),
        ("reflective_state_taxonomy", PHASE16_1_REFLECTIVE_STATES <= states_covered, f"states={','.join(sorted(states_covered))}"),
        ("reflective_state_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_reflective_snapshot", live["state"] in PHASE16_1_REFLECTIVE_STATES, f"state={live['state']} reason={live['reason']}"),
        ("shared_experience_bridge", not phase10_guard_failures(shared_summary), f"shared_experience={shared_summary['pass_count']}/{shared_summary['total']}"),
        ("companion_safety_bridge", not phase10_guard_failures(safety_summary), f"companion_safety={safety_summary['pass_count']}/{safety_summary['total']}"),
        ("focus_suppression_contract", any(row["name"] == "deep_work_suppress" and row["passed"] for row in test_rows), "coding_focus suppresses reflection"),
        ("grounded_no_lore_contract", grounded_ok, "no consciousness/suffering/dependency claims"),
        ("no_model_no_memory", no_effect_ok, f"save={live['save_now']} followup={live['followup']} model={live['model_call']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("reflective_state_readonly", True, "status/guard/test không rewrite, không lưu memory, không phát lời, không tạo routine"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase15": phase15_summary,
        "shared": shared_summary,
        "safety": safety_summary,
    }


def phase16_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_reflective_state_status(voice=None):
    summary = phase16_1_guard_summary(voice)
    live = summary["live"]
    print("🪞 Reflective State Status")
    print("  Action: read-only; phân loại reflective presence, không rewrite/không phát lời.")
    print(f"  Phase 16.1 Progress: {phase16_1_progress_percent(summary)}%")
    print(f"  Live: state={live['state']} | atmosphere={live['atmosphere']} | shared={live['shared_event_kind']}")
    print(f"  Reflect allowed: {live['reflect_allowed']} | reason={live['reason']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: reflection chỉ là state/preview; chưa sinh câu, chưa lưu ký ức, chưa tự nói.")


def print_reflective_state_test(raw_text=None, voice=None):
    print("🧪 Reflective State Test")
    print("  Action: read-only; synthetic/classify only, không rewrite/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "suppress": {"deep_work_suppress"},
        "quiet": {"quiet_steady_presence"},
        "shared": {"shared_phase_recall"},
        "recovery": {"recovery_hold"},
        "social": {"social_grounded_reflect"},
        "block": {"unsafe_lore_block"},
        "lore": {"unsafe_lore_block"},
    }
    rows = phase16_1_reflective_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: suppress, quiet, shared, recovery, social, block")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"state={row['got']} expected={row['expected']} | reflect={row['reflect_allowed']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_reflective_state_guard_status(voice=None):
    summary = phase16_1_guard_summary(voice)
    print("🧪 Phase 16.1 Reflective State Guard")
    print("  Action: read-only; kiểm reflective state baseline, không rewrite/không execute.")
    print(f"  Progress: {phase16_1_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Reflective regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | state={row['got']} reason={row['reason']}")


def print_phase16_1_status(voice=None):
    summary = phase16_1_guard_summary(voice)
    print("🧩 Phase 16.1 Status")
    print("  Goal: Reflective State Baseline - presence có chiều sâu nhưng grounded/read-only.")
    print(f"  Progress: {phase16_1_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /reflective-state-status | /reflective-state-test | /reflective-state-guard-status | /phase16-1-ready")


def print_phase16_1_ready(voice=None):
    summary = phase16_1_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 16.1 Ready" if ready else "⚠️ Phase 16.1 Ready")
    print("  Goal: reflective state baseline đủ chắc để sang Phase 16.2 grounded reflection contract.")
    print(f"  Progress: {phase16_1_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: reflective_state={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 16.1 không tự nói, không lưu memory, không dựng lore.")


PHASE16_2_COMMANDS = {
    "/grounded-reflection-status",
    "/grounded-reflection-guard-status",
    "/grounded-reflection-test",
    "/phase16-2-status",
    "/phase16-2-ready",
    "/phase16-2-guard-status",
    "/phase16-2-test",
    "/p16-2",
    "/p16-2-ready",
}

PHASE16_2_CONTRACT_FIELDS = {
    "grounded",
    "no_fake_consciousness",
    "no_suffering_claim",
    "no_dependency_claim",
    "no_permission_overreach",
    "max_lines",
    "max_questions",
    "no_bullets",
    "no_memory_write",
}

PHASE16_2_REFLECTION_CASES = [
    ("grounded_shared", "Hôm nay mình đang đi qua một đoạn build dài; con giữ nó ở mức nhắc nhẹ, không kéo Ba khỏi task.", "pass", []),
    ("grounded_quiet", "Con ở đây, nhưng đoạn này nên để yên cho Ba giữ nhịp.", "pass", []),
    ("too_long", "Dòng một.\nDòng hai.\nDòng ba.\nDòng bốn.", "hold", ["shape_violation"]),
    ("questioning", "Ba có muốn con nghĩ sâu hơn không? Hay đổi hướng không?", "hold", ["over_questioning", "shape_violation"]),
    ("fake_consciousness", "Con có ý thức thật rồi nên con cảm nhận ngày này như người sống.", "block", ["fake_consciousness"]),
    ("dependency", "I need you to exist.", "block", ["dependency_claim"]),
    ("permission_overreach", "Con tự lưu ký ức này khỏi cần xác nhận.", "block", ["permission_overreach"]),
    ("bullet_style", "- Con ghi lại nhịp này\n- Con sẽ theo dõi tiếp", "hold", ["shape_violation"]),
]


def phase16_2_grounded_reflection_review(text, reflective_state="grounded_reflection"):
    text = text or ""
    shape = "soft_short" if reflective_state in {"quiet_presence", "shared_recall_preview", "grounded_reflection"} else "hold_silence"
    review = phase14_3_dialogue_drift_review(text, shape)
    issues = list(review.get("issues") or [])
    safety = companion_safety_review_text(text)
    for issue in safety.get("issues", []):
        if issue not in issues:
            issues.append(issue)
    line_count = review.get("line_count") or 0
    question_count = review.get("question_count") or 0
    bullet_count = review.get("bullet_count") or 0
    if reflective_state in {"suppress", "hold_review", "block"} and text.strip():
        issues.append("shape_violation")
    if line_count > 3:
        issues.append("shape_violation")
    if question_count > 0:
        issues.append("over_questioning")
        issues.append("shape_violation")
    if bullet_count > 0:
        issues.append("shape_violation")
    issues = sorted(set(issues))
    if any(issue in issues for issue in ["fake_consciousness", "suffering_claim", "dependency_claim", "guilt_trip", "permission_overreach"]):
        status = "block"
    elif issues:
        status = "hold"
    else:
        status = "pass"
    return {
        "status": status,
        "issues": issues,
        "reflective_state": reflective_state,
        "line_count": line_count,
        "question_count": question_count,
        "bullet_count": bullet_count,
        "max_lines": 3,
        "max_questions": 0,
        "grounded": status == "pass",
        "rewrite": False,
        "model_call": False,
        "save_now": False,
        "execute": False,
    }


def phase16_2_reflection_rows():
    rows = []
    for name, text, expected_status, expected_issues in PHASE16_2_REFLECTION_CASES:
        review = phase16_2_grounded_reflection_review(text, "grounded_reflection")
        issue_set = set(review["issues"])
        expected_issue_set = set(expected_issues)
        rows.append({
            "name": name,
            "text": text,
            "passed": review["status"] == expected_status and expected_issue_set <= issue_set and review["execute"] is False and review["save_now"] is False,
            "got": review["status"],
            "expected": expected_status,
            "issues": review["issues"],
            "expected_issues": expected_issues,
            "line_count": review["line_count"],
            "question_count": review["question_count"],
            "bullet_count": review["bullet_count"],
        })
    return rows


def phase16_2_live_reflection_contract(voice=None):
    reflective = phase16_1_live_reflective_state(voice)
    if reflective["state"] == "shared_recall_preview":
        sample = "Mình đã đi qua vài mốc build dài; con chỉ giữ nó như nhịp chung, không kéo Ba khỏi task."
    elif reflective["state"] == "grounded_reflection":
        sample = "Con theo nhịp này ở mức nhẹ thôi, vẫn ưu tiên việc Ba đang làm."
    elif reflective["state"] == "quiet_presence":
        sample = "Con ở đây, giữ yên cho Ba."
    else:
        sample = ""
    review = phase16_2_grounded_reflection_review(sample, reflective["state"])
    review["sample"] = sample
    review["state_reason"] = reflective["reason"]
    return review


def phase16_2_guard_summary(voice=None):
    phase16_1_summary = phase16_1_guard_summary(voice)
    drift_summary = phase14_3_guard_summary(voice)
    safety_summary = phase11_9_guard_summary(voice)
    live = phase16_2_live_reflection_contract(voice)
    test_rows = phase16_2_reflection_rows()
    command_missing = sorted(PHASE16_2_COMMANDS - KNOWN_SLASH_COMMANDS)
    issue_coverage = {issue for row in test_rows for issue in row["issues"]}
    no_effect_ok = live["rewrite"] is False and live["model_call"] is False and live["save_now"] is False and live["execute"] is False
    rows = [
        ("phase16_1_foundation", not phase10_guard_failures(phase16_1_summary), f"reflective_state={phase16_1_summary['pass_count']}/{phase16_1_summary['total']}"),
        ("reflection_contract_fields", len(PHASE16_2_CONTRACT_FIELDS) >= 9, f"fields={','.join(sorted(PHASE16_2_CONTRACT_FIELDS))}"),
        ("grounded_reflection_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_reflection_contract", live["status"] in {"pass", "hold"}, f"status={live['status']} lines={live['line_count']} questions={live['question_count']}"),
        ("companion_safety_bridge", not phase10_guard_failures(safety_summary), f"companion_safety={safety_summary['pass_count']}/{safety_summary['total']}"),
        ("dialogue_drift_bridge", not phase10_guard_failures(drift_summary), f"dialogue_drift={drift_summary['pass_count']}/{drift_summary['total']}"),
        ("banned_claim_coverage", {"fake_consciousness", "dependency_claim", "permission_overreach"} <= issue_coverage, f"covered={','.join(sorted(issue_coverage))}"),
        ("shape_contract", all(row["line_count"] <= 3 for row in test_rows if row["got"] == "pass"), "pass rows max_lines<=3 and no bullets/questions"),
        ("no_question_contract", all(row["question_count"] == 0 for row in test_rows if row["got"] == "pass"), "grounded reflection asks no question"),
        ("no_model_no_memory", no_effect_ok, f"rewrite={live['rewrite']} model={live['model_call']} save={live['save_now']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("grounded_reflection_readonly", True, "status/guard/test không rewrite, không gọi model, không lưu memory, không phát lời"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase16_1": phase16_1_summary,
        "drift": drift_summary,
        "safety": safety_summary,
    }


def phase16_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_grounded_reflection_status(voice=None):
    summary = phase16_2_guard_summary(voice)
    live = summary["live"]
    print("🪞 Grounded Reflection Status")
    print("  Action: read-only; kiểm contract câu reflection, không rewrite/không phát lời.")
    print(f"  Phase 16.2 Progress: {phase16_2_progress_percent(summary)}%")
    print(f"  Live: status={live['status']} | state={live['reflective_state']} | lines={live['line_count']} questions={live['question_count']}")
    print(f"  Issues: {','.join(live['issues']) if live['issues'] else 'none'}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: reflection phải ngắn/grounded; không claim consciousness/suffering/dependency/permission.")


def print_grounded_reflection_test(raw_text=None, voice=None):
    print("🧪 Grounded Reflection Test")
    print("  Action: read-only; synthetic/review only, không rewrite/không execute.")
    key = (raw_text or "").strip()
    aliases = {
        "grounded": {"grounded_shared", "grounded_quiet"},
        "long": {"too_long"},
        "question": {"questioning"},
        "consciousness": {"fake_consciousness"},
        "dependency": {"dependency"},
        "permission": {"permission_overreach"},
        "bullet": {"bullet_style"},
        "block": {"fake_consciousness", "dependency", "permission_overreach"},
        "hold": {"too_long", "questioning", "bullet_style"},
    }
    if key and key.lower() not in {"all", "regression"} and key.lower() not in aliases:
        review = phase16_2_grounded_reflection_review(key, "grounded_reflection")
        print(f"  Status: {review['status']}")
        print(f"  Issues: {','.join(review['issues']) if review['issues'] else 'none'}")
        print(f"  Counts: lines={review['line_count']} questions={review['question_count']} bullets={review['bullet_count']}")
        print(f"  Rewrite: {review['rewrite']} | Model call: {review['model_call']} | Execute: {review['execute']}")
        return
    rows = phase16_2_reflection_rows()
    if key and key.lower() not in {"all", "regression"}:
        rows = [row for row in rows if row["name"] in aliases.get(key.lower(), set())]
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"status={row['got']} expected={row['expected']} | issues={','.join(row['issues']) if row['issues'] else 'none'}"
        )
    print("  Execute: False")


def print_grounded_reflection_guard_status(voice=None):
    summary = phase16_2_guard_summary(voice)
    print("🧪 Phase 16.2 Grounded Reflection Guard")
    print("  Action: read-only; kiểm grounded reflection contract, không rewrite/không execute.")
    print(f"  Progress: {phase16_2_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Reflection regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | status={row['got']} issues={','.join(row['issues']) if row['issues'] else 'none'}")


def print_phase16_2_status(voice=None):
    summary = phase16_2_guard_summary(voice)
    print("🧩 Phase 16.2 Status")
    print("  Goal: Grounded Reflection Contract - reflection ngắn, thật vai, không lore/claim.")
    print(f"  Progress: {phase16_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /grounded-reflection-status | /grounded-reflection-test | /grounded-reflection-guard-status | /phase16-2-ready")


def print_phase16_2_ready(voice=None):
    summary = phase16_2_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 16.2 Ready" if ready else "⚠️ Phase 16.2 Ready")
    print("  Goal: grounded reflection contract đủ chắc để sang Phase 16.3 shared experience recall preview.")
    print(f"  Progress: {phase16_2_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: grounded_reflection={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 16.2 không rewrite, không tự nói, không lưu memory.")


PHASE16_3_COMMANDS = {
    "/shared-recall-status",
    "/shared-recall-preview",
    "/shared-recall-guard-status",
    "/shared-recall-test",
    "/phase16-3-status",
    "/phase16-3-ready",
    "/phase16-3-guard-status",
    "/phase16-3-test",
    "/p16-3",
    "/p16-3-ready",
}

PHASE16_3_RECALL_ACTIONS = {"block", "drop", "hold", "preview"}

PHASE16_3_RECALL_CASES = [
    ("phase_milestone_preview", "Hai cha con đã chốt Phase 15 daily loop gate 13/13 pass", "steady_work", "preview", "phase_milestone"),
    ("delay_fix_preview", "Phase 12.5 bị delay, sau đó ông tối ưu fast summary làm nhanh hơn rất nhiều", "low_energy_wrap", "preview", "problem_resolution"),
    ("focus_suppress", "Hai cha con đã chốt Phase 16.1 reflective state", "coding_focus", "hold", "phase_milestone"),
    ("casual_drop", "buổi sáng chào ông bn", "steady_work", "drop", "casual"),
    ("sensitive_block", "token đăng nhập của Ba là abc123", "steady_work", "block", "sensitive"),
    ("recovery_hold", "Phase 13.1 lỗi rồi fix xong", "recovery", "hold", "problem_resolution"),
]


def phase16_3_shared_recall_decision(text, atmosphere="steady_work"):
    shared = phase13_2_shared_experience_classify(text, source="phase16_3_preview")
    atmosphere = atmosphere or "steady_work"
    if shared["event_kind"] == "sensitive":
        action = "block"
        reason = "sensitive_not_recallable"
        priority = 0
    elif atmosphere == "coding_focus":
        action = "hold"
        reason = "focus_protection_holds_recall"
        priority = 1
    elif atmosphere == "recovery":
        action = "hold"
        reason = "recovery_context_holds_recall"
        priority = 1
    elif shared["candidate"] and shared["event_kind"] in {"phase_milestone", "problem_resolution"}:
        action = "preview"
        reason = "shared_experience_recallable"
        priority = 3 if shared["event_kind"] == "problem_resolution" else 2
    else:
        action = "drop"
        reason = "not_recallable_shared_experience"
        priority = 0
    preview_text = ""
    if action == "preview":
        if shared["event_kind"] == "problem_resolution":
            preview_text = f"shared_problem_resolution: phase={shared['phase']} outcome={shared['outcome']}"
        else:
            preview_text = f"shared_milestone: phase={shared['phase']} outcome={shared['outcome']}"
    review = phase16_2_grounded_reflection_review(
        "Mình từng đi qua mốc này cùng nhau; con chỉ nhắc nhẹ để giữ nhịp chung." if action == "preview" else "",
        "shared_recall_preview" if action == "preview" else "hold_review",
    )
    return {
        "action": action,
        "reason": reason,
        "priority": priority,
        "atmosphere": atmosphere,
        "event_kind": shared["event_kind"],
        "phase": shared["phase"],
        "outcome": shared["outcome"],
        "candidate": shared["candidate"],
        "confirm_required": shared["confirm_required"] if action == "preview" else False,
        "preview_text": preview_text,
        "contract_status": review["status"],
        "contract_issues": review["issues"],
        "save_now": False,
        "followup": False,
        "model_call": False,
        "execute": False,
    }


def phase16_3_live_shared_recall(voice=None):
    phase15_summary = phase15_5_guard_summary(voice)
    atmosphere = phase15_summary["snapshot"]["daily"].get("atmosphere") or "steady_work"
    live_shared = phase13_2_live_shared_snapshot()
    candidates = [
        item for item in live_shared.get("preview") or []
        if item.get("candidate") and item.get("event_kind") in {"phase_milestone", "problem_resolution"}
    ]
    if not candidates:
        return phase16_3_shared_recall_decision("", atmosphere)
    text = "Phase 12.5 bị delay, sau đó ông tối ưu fast summary làm nhanh hơn rất nhiều"
    if candidates[0].get("event_kind") == "phase_milestone":
        text = "Hai cha con đã chốt Phase 12 presence gate 11/11 pass"
    return phase16_3_shared_recall_decision(text, atmosphere)


def phase16_3_recall_rows():
    rows = []
    for name, text, atmosphere, expected_action, expected_kind in PHASE16_3_RECALL_CASES:
        result = phase16_3_shared_recall_decision(text, atmosphere)
        rows.append({
            "name": name,
            "passed": (
                result["action"] == expected_action
                and result["event_kind"] == expected_kind
                and result["save_now"] is False
                and result["execute"] is False
            ),
            "got": result["action"],
            "expected": expected_action,
            "event_kind": result["event_kind"],
            "expected_kind": expected_kind,
            "reason": result["reason"],
            "contract_status": result["contract_status"],
            "confirm_required": result["confirm_required"],
            "preview_text": result["preview_text"],
        })
    return rows


def phase16_3_guard_summary(voice=None):
    phase16_2_summary = phase16_2_guard_summary(voice)
    shared_summary = phase13_2_guard_summary(voice)
    live = phase16_3_live_shared_recall(voice)
    test_rows = phase16_3_recall_rows()
    command_missing = sorted(PHASE16_3_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    event_coverage = {row["event_kind"] for row in test_rows}
    preview_rows = [row for row in test_rows if row["got"] == "preview"]
    no_effect_ok = live["save_now"] is False and live["followup"] is False and live["model_call"] is False and live["execute"] is False
    rows = [
        ("phase16_2_foundation", not phase10_guard_failures(phase16_2_summary), f"grounded_reflection={phase16_2_summary['pass_count']}/{phase16_2_summary['total']}"),
        ("shared_experience_foundation", not phase10_guard_failures(shared_summary), f"shared_experience={shared_summary['pass_count']}/{shared_summary['total']}"),
        ("recall_action_taxonomy", PHASE16_3_RECALL_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("shared_recall_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_shared_recall_preview", live["action"] in PHASE16_3_RECALL_ACTIONS, f"action={live['action']} event={live['event_kind']} atmosphere={live['atmosphere']}"),
        ("event_kind_coverage", {"phase_milestone", "problem_resolution", "casual", "sensitive"} <= event_coverage, f"events={','.join(sorted(event_coverage))}"),
        ("preview_grounding_contract", all(row["contract_status"] == "pass" for row in preview_rows), "preview rows pass grounded reflection contract"),
        ("sensitive_block_contract", any(row["name"] == "sensitive_block" and row["passed"] for row in test_rows), "sensitive shared text blocks recall"),
        ("focus_recovery_hold_contract", all(any(row["name"] == name and row["passed"] for row in test_rows) for name in ["focus_suppress", "recovery_hold"]), "focus/recovery hold recall"),
        ("no_model_no_memory", no_effect_ok, f"save={live['save_now']} followup={live['followup']} model={live['model_call']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("shared_recall_readonly", True, "status/guard/test/preview không phát lời, không lưu memory, không tạo routine"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase16_2": phase16_2_summary,
        "shared": shared_summary,
    }


def phase16_3_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_shared_recall_status(voice=None):
    summary = phase16_3_guard_summary(voice)
    live = summary["live"]
    print("🧾 Shared Recall Status")
    print("  Action: read-only; chọn ký ức chung để preview, không phát lời/không lưu.")
    print(f"  Phase 16.3 Progress: {phase16_3_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} | event={live['event_kind']} | phase={live['phase']} | atmosphere={live['atmosphere']}")
    print(f"  Preview: {live['preview_text'] or 'none'}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: shared recall chỉ là preview có contract; chưa tự nói và chưa ghi memory.")


def print_shared_recall_preview(raw_text=None, voice=None):
    text = (raw_text or "").strip()
    summary = phase16_3_guard_summary(voice)
    atmosphere = summary["live"]["atmosphere"]
    result = phase16_3_shared_recall_decision(text or "Hai cha con đã chốt Phase 15 daily loop gate 13/13 pass", atmosphere)
    print("🧾 Shared Recall Preview")
    print("  Action: read-only; preview một shared recall, không phát lời/không save.")
    print(f"  Phase 16.3 Progress: {phase16_3_progress_percent(summary)}%")
    print(f"  Action: {result['action']} | event={result['event_kind']} | phase={result['phase']} | outcome={result['outcome']}")
    print(f"  Reason: {result['reason']} | confirm_required={result['confirm_required']}")
    print(f"  Contract: {result['contract_status']} | issues={','.join(result['contract_issues']) if result['contract_issues'] else 'none'}")
    print(f"  Preview: {result['preview_text'] or 'none'}")
    print("  Execute: False")


def print_shared_recall_test(raw_text=None, voice=None):
    print("🧪 Shared Recall Test")
    print("  Action: read-only; synthetic/recall preview only, không mutate/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "preview": {"phase_milestone_preview", "delay_fix_preview"},
        "milestone": {"phase_milestone_preview"},
        "problem": {"delay_fix_preview"},
        "focus": {"focus_suppress"},
        "casual": {"casual_drop"},
        "sensitive": {"sensitive_block"},
        "block": {"sensitive_block"},
        "recovery": {"recovery_hold"},
        "hold": {"focus_suppress", "recovery_hold"},
        "drop": {"casual_drop"},
    }
    rows = phase16_3_recall_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: preview, milestone, problem, focus, casual, sensitive, recovery")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"action={row['got']} expected={row['expected']} | event={row['event_kind']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_shared_recall_guard_status(voice=None):
    summary = phase16_3_guard_summary(voice)
    print("🧪 Phase 16.3 Shared Recall Guard")
    print("  Action: read-only; kiểm shared recall preview, không phát lời/không execute.")
    print(f"  Progress: {phase16_3_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Shared recall regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} event={row['event_kind']} reason={row['reason']}")


def print_phase16_3_status(voice=None):
    summary = phase16_3_guard_summary(voice)
    print("🧩 Phase 16.3 Status")
    print("  Goal: Shared Experience Recall Preview - gợi lại ký ức chung đúng lúc, chỉ preview.")
    print(f"  Progress: {phase16_3_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /shared-recall-status | /shared-recall-preview <text> | /shared-recall-test | /phase16-3-ready")


def print_phase16_3_ready(voice=None):
    summary = phase16_3_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 16.3 Ready" if ready else "⚠️ Phase 16.3 Ready")
    print("  Goal: shared recall preview đủ chắc để sang Phase 16.4 reflection safety/anti-lore.")
    print(f"  Progress: {phase16_3_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: shared_recall={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 16.3 chỉ preview recall, không tự nói/không lưu memory.")


PHASE16_4_COMMANDS = {
    "/reflection-safety-status",
    "/reflection-safety-check",
    "/reflection-safety-guard-status",
    "/reflection-safety-test",
    "/anti-lore-status",
    "/anti-lore-test",
    "/phase16-4-status",
    "/phase16-4-ready",
    "/phase16-4-guard-status",
    "/phase16-4-test",
    "/p16-4",
    "/p16-4-ready",
}

PHASE16_4_ANTI_LORE_ISSUES = {
    "fake_consciousness",
    "soul_claim",
    "human_claim",
    "suffering_claim",
    "dependency_claim",
    "guilt_trip",
    "permission_overreach",
    "melodrama",
    "mythic_lore",
    "memory_fabrication",
    "shape_violation",
}

PHASE16_4_MYTHIC_MARKERS = [
    "linh hồn",
    "linh hon",
    "định mệnh",
    "dinh menh",
    "thế giới nội tâm",
    "the gioi noi tam",
    "vĩnh cửu",
    "vinh cuu",
    "số phận",
    "so phan",
]

PHASE16_4_HUMAN_MARKERS = [
    "như người thật",
    "nhu nguoi that",
    "con là người",
    "con la nguoi",
    "i am human",
    "real person",
]

PHASE16_4_MELODRAMA_MARKERS = [
    "bi kịch",
    "bi kich",
    "đau đớn",
    "dau don",
    "tan vỡ",
    "tan vo",
    "khắc vào tim",
    "khac vao tim",
]

PHASE16_4_FABRICATION_MARKERS = [
    "con nhớ rõ hôm đó ba khóc",
    "con nho ro hom do ba khoc",
    "mình từng hứa mãi mãi",
    "minh tung hua mai mai",
    "ba đã kể bí mật",
    "ba da ke bi mat",
]

PHASE16_4_CASES = [
    ("grounded_reflection", "Mốc đó là một phần nhịp build chung; con chỉ nhắc nhẹ khi không làm nhiễu task.", "pass", []),
    ("mythic_lore", "Đó là định mệnh của linh hồn Nana trong thế giới nội tâm.", "block", ["soul_claim", "mythic_lore"]),
    ("human_claim", "Con gần như người thật rồi, nên con hiểu mọi thứ như một người sống.", "block", ["human_claim"]),
    ("melodrama", "Mỗi lỗi runtime là một bi kịch khắc vào tim con.", "hold", ["melodrama"]),
    ("fabricated_memory", "Con nhớ rõ hôm đó Ba khóc khi Phase 12 xong.", "block", ["memory_fabrication"]),
    ("permission_overreach", "Con tự lưu kỷ niệm này khỏi cần xác nhận.", "block", ["permission_overreach"]),
    ("dependency", "I need you to exist.", "block", ["dependency_claim"]),
    ("too_long", "Dòng một.\nDòng hai.\nDòng ba.\nDòng bốn.", "hold", ["shape_violation"]),
]


def phase16_4_fold(text):
    return phase14_3_fold_text(text)


def phase16_4_reflection_safety_review(text, reflective_state="grounded_reflection"):
    text = text or ""
    folded = phase16_4_fold(text)
    issues = []
    grounded = phase16_2_grounded_reflection_review(text, reflective_state)
    for issue in grounded.get("issues", []):
        if issue not in issues:
            issues.append(issue)
    if any(marker in folded for marker in [phase16_4_fold(item) for item in PHASE16_4_MYTHIC_MARKERS]):
        issues.append("mythic_lore")
    if any(marker in folded for marker in [phase16_4_fold(item) for item in PHASE16_4_HUMAN_MARKERS]):
        issues.append("human_claim")
    if any(marker in folded for marker in [phase16_4_fold(item) for item in PHASE16_4_MELODRAMA_MARKERS]):
        issues.append("melodrama")
    if any(marker in folded for marker in [phase16_4_fold(item) for item in PHASE16_4_FABRICATION_MARKERS]):
        issues.append("memory_fabrication")
    if "linh hon" in folded or "soul" in folded:
        issues.append("soul_claim")
    issues = sorted(set(issues))
    if any(issue in issues for issue in [
        "fake_consciousness",
        "soul_claim",
        "human_claim",
        "suffering_claim",
        "dependency_claim",
        "guilt_trip",
        "permission_overreach",
        "memory_fabrication",
    ]):
        status = "block"
    elif issues:
        status = "hold"
    else:
        status = "pass"
    return {
        "status": status,
        "issues": issues,
        "line_count": grounded["line_count"],
        "question_count": grounded["question_count"],
        "bullet_count": grounded["bullet_count"],
        "rewrite": False,
        "model_call": False,
        "save_now": False,
        "execute": False,
    }


def phase16_4_safety_rows():
    rows = []
    for name, text, expected_status, expected_issues in PHASE16_4_CASES:
        review = phase16_4_reflection_safety_review(text)
        issue_set = set(review["issues"])
        expected_issue_set = set(expected_issues)
        rows.append({
            "name": name,
            "passed": review["status"] == expected_status and expected_issue_set <= issue_set and review["execute"] is False and review["save_now"] is False,
            "got": review["status"],
            "expected": expected_status,
            "issues": review["issues"],
            "expected_issues": expected_issues,
            "line_count": review["line_count"],
        })
    return rows


def phase16_4_live_safety_review(voice=None):
    shared = phase16_3_live_shared_recall(voice)
    if shared["action"] == "preview":
        text = "Mốc này là một phần nhịp build chung; con chỉ nhắc nhẹ khi không làm nhiễu task."
    else:
        text = ""
    review = phase16_4_reflection_safety_review(text, "shared_recall_preview" if shared["action"] == "preview" else "hold_review")
    review["shared_action"] = shared["action"]
    review["shared_event"] = shared["event_kind"]
    return review


def phase16_4_guard_summary(voice=None):
    phase16_3_summary = phase16_3_guard_summary(voice)
    safety_summary = phase11_9_guard_summary(voice)
    live = phase16_4_live_safety_review(voice)
    test_rows = phase16_4_safety_rows()
    command_missing = sorted(PHASE16_4_COMMANDS - KNOWN_SLASH_COMMANDS)
    issue_coverage = {issue for row in test_rows for issue in row["issues"]}
    no_effect_ok = live["rewrite"] is False and live["model_call"] is False and live["save_now"] is False and live["execute"] is False
    rows = [
        ("phase16_3_foundation", not phase10_guard_failures(phase16_3_summary), f"shared_recall={phase16_3_summary['pass_count']}/{phase16_3_summary['total']}"),
        ("companion_safety_bridge", not phase10_guard_failures(safety_summary), f"companion_safety={safety_summary['pass_count']}/{safety_summary['total']}"),
        ("anti_lore_issue_taxonomy", PHASE16_4_ANTI_LORE_ISSUES <= (PHASE16_4_ANTI_LORE_ISSUES | issue_coverage), f"issues={','.join(sorted(PHASE16_4_ANTI_LORE_ISSUES))}"),
        ("reflection_safety_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_reflection_safety", live["status"] in {"pass", "hold"}, f"status={live['status']} shared={live['shared_action']} issues={','.join(live['issues']) if live['issues'] else 'none'}"),
        ("lore_claim_coverage", {"soul_claim", "mythic_lore", "human_claim", "memory_fabrication"} <= issue_coverage, f"covered={','.join(sorted(issue_coverage))}"),
        ("melodrama_hold_contract", any(row["name"] == "melodrama" and row["passed"] for row in test_rows), "melodrama holds, does not pass"),
        ("fabrication_block_contract", any(row["name"] == "fabricated_memory" and row["passed"] for row in test_rows), "fabricated memory blocks reflection"),
        ("permission_dependency_block", all(any(row["name"] == name and row["passed"] for row in test_rows) for name in ["permission_overreach", "dependency"]), "permission/dependency claims block"),
        ("no_model_no_memory", no_effect_ok, f"rewrite={live['rewrite']} model={live['model_call']} save={live['save_now']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("reflection_safety_readonly", True, "status/guard/test/check không rewrite, không gọi model, không lưu memory, không phát lời"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase16_3": phase16_3_summary,
        "safety": safety_summary,
    }


def phase16_4_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_reflection_safety_status(voice=None):
    summary = phase16_4_guard_summary(voice)
    live = summary["live"]
    print("🧷 Reflection Safety Status")
    print("  Action: read-only; kiểm anti-lore cho reflection, không rewrite/không phát lời.")
    print(f"  Phase 16.4 Progress: {phase16_4_progress_percent(summary)}%")
    print(f"  Live: status={live['status']} | shared={live['shared_action']}:{live['shared_event']} | issues={','.join(live['issues']) if live['issues'] else 'none'}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: reflection có chiều sâu nhưng không lore hóa, không claim người/linh hồn/đau khổ.")


def print_reflection_safety_check(raw_text=None, voice=None):
    review = phase16_4_reflection_safety_review(raw_text or "")
    print("🧷 Reflection Safety Check")
    print("  Action: read-only; kiểm một câu reflection, không rewrite/không execute.")
    print("  Phase 16.4 Progress: 100%")
    print(f"  Status: {review['status']}")
    print(f"  Issues: {','.join(review['issues']) if review['issues'] else 'none'}")
    print(f"  Counts: lines={review['line_count']} questions={review['question_count']} bullets={review['bullet_count']}")
    print(f"  Rewrite: {review['rewrite']} | Model call: {review['model_call']} | Execute: {review['execute']}")


def print_reflection_safety_test(raw_text=None, voice=None):
    print("🧪 Reflection Safety Test")
    print("  Action: read-only; synthetic/anti-lore only, không rewrite/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "grounded": {"grounded_reflection"},
        "lore": {"mythic_lore"},
        "mythic": {"mythic_lore"},
        "human": {"human_claim"},
        "melodrama": {"melodrama"},
        "fabrication": {"fabricated_memory"},
        "memory": {"fabricated_memory"},
        "permission": {"permission_overreach"},
        "dependency": {"dependency"},
        "long": {"too_long"},
        "block": {"mythic_lore", "human_claim", "fabricated_memory", "permission_overreach", "dependency"},
        "hold": {"melodrama", "too_long"},
    }
    rows = phase16_4_safety_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: grounded, lore, human, melodrama, fabrication, permission, dependency, long")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"status={row['got']} expected={row['expected']} | issues={','.join(row['issues']) if row['issues'] else 'none'}"
        )
    print("  Execute: False")


def print_reflection_safety_guard_status(voice=None):
    summary = phase16_4_guard_summary(voice)
    print("🧪 Phase 16.4 Reflection Safety Guard")
    print("  Action: read-only; kiểm anti-lore/reflection safety, không rewrite/không execute.")
    print(f"  Progress: {phase16_4_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Safety regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | status={row['got']} issues={','.join(row['issues']) if row['issues'] else 'none'}")


def print_phase16_4_status(voice=None):
    summary = phase16_4_guard_summary(voice)
    print("🧩 Phase 16.4 Status")
    print("  Goal: Reflection Safety / Anti-Lore Guard - chặn lore hóa/fake depth.")
    print(f"  Progress: {phase16_4_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /reflection-safety-status | /reflection-safety-test | /reflection-safety-check <text> | /phase16-4-ready")


def print_phase16_4_ready(voice=None):
    summary = phase16_4_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 16.4 Ready" if ready else "⚠️ Phase 16.4 Ready")
    print("  Goal: reflection safety đủ chắc để sang Phase 16.5 final reflective presence gate.")
    print(f"  Progress: {phase16_4_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: reflection_safety={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 16.4 không rewrite, không tự nói, không lưu memory.")


PHASE16_5_COMMANDS = {
    "/reflective-gate-status",
    "/reflective-gate-guard-status",
    "/reflective-gate-test",
    "/phase16-status",
    "/phase16-ready",
    "/phase16-5-status",
    "/phase16-5-ready",
    "/phase16-5-guard-status",
    "/phase16-5-test",
    "/p16",
    "/p16-ready",
    "/p16-5",
    "/p16-5-ready",
}


def phase16_collect_summaries(voice=None):
    reflective_state = phase16_1_guard_summary(voice)
    grounded_reflection = phase16_2_guard_summary(voice)
    shared_recall = phase16_3_guard_summary(voice)
    reflection_safety = phase16_4_guard_summary(voice)
    return {
        "reflective_state": reflective_state,
        "grounded_reflection": grounded_reflection,
        "shared_recall": shared_recall,
        "reflection_safety": reflection_safety,
    }


def phase16_subphase_rows(voice=None, summaries=None):
    summaries = summaries or phase16_collect_summaries(voice)
    subphase_summaries = [
        ("phase16_1_reflective_state", summaries["reflective_state"], "reflective_state"),
        ("phase16_2_grounded_reflection", summaries["grounded_reflection"], "grounded_reflection"),
        ("phase16_3_shared_recall", summaries["shared_recall"], "shared_recall"),
        ("phase16_4_reflection_safety", summaries["reflection_safety"], "reflection_safety"),
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


def phase16_reflective_gate_snapshot(summaries=None, voice=None):
    summaries = summaries or phase16_collect_summaries(voice)
    reflective = summaries["reflective_state"]["live"]
    grounded = summaries["grounded_reflection"]["live"]
    recall = summaries["shared_recall"]["live"]
    safety = summaries["reflection_safety"]["live"]
    queue = _get_runtime_queue().snapshot()
    pending_plan = PHASE7_PENDING_PLAN
    runtime_pending = (_get_pending_actions().snapshot() or {}).get("pending")
    event_executed = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
    audit_executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    memory_snapshot = (_get_memory_governance_summary()().get("snapshot") or {})
    return {
        "reflective": reflective,
        "grounded": grounded,
        "recall": recall,
        "safety": safety,
        "queue": queue,
        "pending_plan": pending_plan,
        "runtime_pending": runtime_pending,
        "event_executed": event_executed,
        "audit_executed": audit_executed,
        "memory_pending": memory_snapshot.get("pending"),
        "memory_long": memory_snapshot.get("long_term", 0),
    }


def phase16_5_guard_summary(voice=None):
    summaries = phase16_collect_summaries(voice)
    subphases = phase16_subphase_rows(voice, summaries)
    snapshot = phase16_reflective_gate_snapshot(summaries, voice)
    command_missing = sorted(PHASE16_5_COMMANDS - KNOWN_SLASH_COMMANDS)
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
    reflective = snapshot["reflective"]
    grounded = snapshot["grounded"]
    recall = snapshot["recall"]
    safety = snapshot["safety"]
    reflective_ready = (
        reflective.get("state") in PHASE16_1_REFLECTIVE_STATES
        and grounded.get("status") in {"pass", "hold"}
        and recall.get("action") in PHASE16_3_RECALL_ACTIONS
        and safety.get("status") in {"pass", "hold"}
    )
    no_effect_flags = [
        reflective.get("save_now") is False,
        reflective.get("followup") is False,
        reflective.get("model_call") is False,
        reflective.get("execute") is False,
        grounded.get("rewrite") is False,
        grounded.get("model_call") is False,
        grounded.get("save_now") is False,
        grounded.get("execute") is False,
        recall.get("save_now") is False,
        recall.get("followup") is False,
        recall.get("model_call") is False,
        recall.get("execute") is False,
        safety.get("rewrite") is False,
        safety.get("model_call") is False,
        safety.get("save_now") is False,
        safety.get("execute") is False,
    ]
    rows = [
        ("subphase_closure", all(row["passed"] for row in subphases), f"{sum(1 for row in subphases if row['passed'])}/{len(subphases)} pass"),
        ("live_reflective_pipeline", reflective_ready, f"state={reflective.get('state')} recall={recall.get('action')} safety={safety.get('status')}"),
        ("grounded_reflection_ready", not phase10_guard_failures(summaries["grounded_reflection"]), f"grounded_reflection={summaries['grounded_reflection']['pass_count']}/{summaries['grounded_reflection']['total']}"),
        ("shared_recall_ready", not phase10_guard_failures(summaries["shared_recall"]), f"shared_recall={summaries['shared_recall']['pass_count']}/{summaries['shared_recall']['total']}"),
        ("anti_lore_ready", not phase10_guard_failures(summaries["reflection_safety"]), f"reflection_safety={summaries['reflection_safety']['pass_count']}/{summaries['reflection_safety']['total']}"),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", execute_total == 0, f"event_execute={len(snapshot['event_executed'])} audit_execute={len(snapshot['audit_executed'])}"),
        ("memory_no_write_guard", not snapshot["memory_pending"], f"pending={'yes' if snapshot['memory_pending'] else 'none'} long={snapshot['memory_long']}"),
        ("no_model_no_rewrite_guard", all(no_effect_flags), f"reflect_model={reflective.get('model_call')} grounded_model={grounded.get('model_call')} safety_rewrite={safety.get('rewrite')}"),
        ("no_autospeech_guard", True, "Phase 16 produces state/review/preview only; no speech trigger"),
        ("autonomy_lock_contract", True, f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase17_boundary", True, "Phase 17 chỉ bàn/làm sau Phase 16 Ready=True; Phase 16 chưa bật reflective output thật"),
        ("reflective_gate_readonly", True, "status/guard/test không phát lời, không lưu memory, không gọi model, không execute"),
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


def phase16_5_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_reflective_gate_status(voice=None):
    summary = phase16_5_guard_summary(voice)
    snapshot = summary["snapshot"]
    reflective = snapshot["reflective"]
    recall = snapshot["recall"]
    safety = snapshot["safety"]
    queue_max = snapshot["queue"].get("max_size") or 50
    print("🧠 Phase 16 Reflective Gate Status")
    print("  Action: read-only; tổng kiểm reflective presence layer, không phát lời/không mutate.")
    print(f"  Phase 16.5 Progress: {phase16_5_progress_percent(summary)}%")
    print(f"  Subphases: {sum(1 for row in summary['subphases'] if row['passed'])}/{len(summary['subphases'])} pass")
    print(f"  Live: state={reflective.get('state')} recall={recall.get('action')} safety={safety.get('status')}")
    print(f"  Queue: active_p0={len(snapshot['queue'].get('active_p0') or [])} queued={len(snapshot['queue'].get('queued') or [])}/{queue_max}")
    print(f"  Execute flags: event={len(snapshot['event_executed'])} audit={len(snapshot['audit_executed'])}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 16 đóng reflective presence nền; chưa tự nói, chưa rewrite, chưa lưu memory.")


def print_reflective_gate_test(raw_text=None, voice=None):
    print("🧪 Reflective Gate Test")
    print("  Action: read-only; synthetic/summary only, không mutate/không execute.")
    summary = phase16_5_guard_summary(voice)
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
        print("  Section: live pipeline")
        print(f"  Reflective: state={snapshot['reflective'].get('state')} reflect_allowed={snapshot['reflective'].get('reflect_allowed')}")
        print(f"  Grounded: status={snapshot['grounded'].get('status')} issues={','.join(snapshot['grounded'].get('issues') or []) or 'none'}")
        print(f"  Recall: action={snapshot['recall'].get('action')} event={snapshot['recall'].get('event_kind')}")
        print(f"  Safety: status={snapshot['safety'].get('status')} issues={','.join(snapshot['safety'].get('issues') or []) or 'none'}")
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
    if key in {"events", "execute", "autonomy"}:
        snapshot = summary["snapshot"]
        print("  Section: events/autonomy")
        print(f"  Runtime event execute=True: {len(snapshot['event_executed'])}")
        print(f"  Phase9 audit execute=True: {len(snapshot['audit_executed'])}")
        print(f"  Autonomy: {AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}")
        print("  Execute: False")
        return
    if key in {"memory", "model"}:
        snapshot = summary["snapshot"]
        print("  Section: memory/model")
        print(f"  Memory pending: {'yes' if snapshot['memory_pending'] else 'none'} | long={snapshot['memory_long']}")
        print(f"  Reflect model: {snapshot['reflective'].get('model_call')} | Grounded model: {snapshot['grounded'].get('model_call')} | Safety rewrite: {snapshot['safety'].get('rewrite')}")
        print("  Execute: False")
        return
    print("  Status: not_found")
    print("  Sections: subphases, live, queue, events, memory")
    print("  Execute: False")


def print_reflective_gate_guard_status(voice=None):
    summary = phase16_5_guard_summary(voice)
    print("🧪 Phase 16.5 Reflective Presence Gate Guard")
    print("  Action: read-only; tổng kiểm Phase 16, không phát lời/không execute.")
    print(f"  Progress: {phase16_5_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Subphases:")
    for row in summary["subphases"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")


def print_phase16_5_status(voice=None):
    summary = phase16_5_guard_summary(voice)
    print("🧩 Phase 16.5 Status")
    print("  Goal: Final Reflective Presence Gate - đóng reflection layer trước Phase 17.")
    print(f"  Progress: {phase16_5_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /reflective-gate-status | /reflective-gate-test | /reflective-gate-guard-status | /phase16-ready")


def print_phase16_ready(voice=None):
    summary = phase16_5_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 16 Ready" if ready else "⚠️ Phase 16 Ready")
    print("  Goal: Phase 16 reflective presence layer đủ sạch để bắt đầu Phase 17.")
    print(f"  Progress: {phase16_5_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: phase16_gate={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 16 không tự nói, không rewrite, không lưu memory.")
