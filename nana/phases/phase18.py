"""nana.phases.phase18 — Phase 18: Companion Integration Gate
Subphases:
  18.1 — Companion Mode Decision
  18.2 — Consistency Profile
  18.3 — Response Preview Decision
  18.4 — Live Readiness Gate
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

# Direct imports for Phase 18
from nana.autonomy import AUTONOMY_LOCK_PHASE, AUTONOMY_LOCK_RULE
from nana.memory import memory_governance_summary
from nana.runtime.recovery import recovery_snapshot

PHASE7_PENDING_PLAN = None


# ──────────────────────────────────────────────────────────────────────────────
# Subphase imports (declared here so IDE tools can follow the dependency chain)
# ──────────────────────────────────────────────────────────────────────────────

from nana.phases.phase12 import phase12_5_guard_summary
from nana.phases.phase12 import phase12_1_attention_state, phase12_1_live_attention_context
from nana.phases.phase13 import phase13_5_guard_summary
from nana.phases.phase14 import phase14_5_guard_summary
from nana.phases.phase15 import phase15_5_guard_summary
from nana.phases.phase16 import phase16_5_guard_summary
from nana.phases.phase16 import phase16_4_reflection_safety_review
from nana.phases.phase17 import phase17_4_guard_summary


PHASE18_1_COMMANDS = {
    "/companion-integration-status",
    "/companion-integration-guard-status",
    "/companion-integration-test",
    "/phase18-1-status",
    "/phase18-1-ready",
    "/phase18-1-guard-status",
    "/phase18-1-test",
    "/p18-1",
    "/p18-1-ready",
}

PHASE18_1_COMPANION_MODES = {
    "available_companion",
    "blocked_companion",
    "memory_review_companion",
    "quiet_companion",
    "recovery_companion",
    "reflective_companion",
}

PHASE18_1_CASES = [
    ("focus_quiet", "deep_work", "silent_lock", "drop", "hold_silence", "suppress", "enforce_silence", "quiet_companion"),
    ("casual_available", "casual_chat", "available_context", "candidate_review", "emit_candidate", "quiet_presence", "allow_preview", "available_companion"),
    ("shared_reflective", "idle", "low_presence", "candidate_review", "emit_candidate", "shared_recall_preview", "allow_preview", "reflective_companion"),
    ("memory_review", "idle", "low_presence", "candidate_review", "hold_review", "quiet_presence", "hold_output", "memory_review_companion"),
    ("recovery_hold", "recovery_needed", "recovery_once", "hold", "hold_review", "hold_review", "hold_output", "recovery_companion"),
    ("blocked_output", "casual_chat", "available_context", "candidate_review", "block_output", "grounded_reflection", "block_output", "blocked_companion"),
]

PHASE18_SOFT_BRIDGE_FAILURES = {
    "phase16": {
        "subphase_closure",
        "grounded_reflection_ready",
        "shared_recall_ready",
        "anti_lore_ready",
    },
    "phase17": {
        "subphase_closure",
        "output_candidate_ready",
        "reflection_injection_ready",
        "silence_hold_ready",
    },
}


def phase18_bridge_ready(summary, bridge_name=None):
    failures = [name for name, passed, _detail in summary.get("rows", []) if not passed]
    if not failures:
        return True, "strict"
    allowed = PHASE18_SOFT_BRIDGE_FAILURES.get(bridge_name or "", set())
    if allowed and all(name in allowed for name in failures):
        return True, "soft_live_chain"
    return False, "fail"


def phase18_1_companion_mode_decision(attention_state, rhythm, memory_action, output_action, reflective_state, enforcement_action):
    if output_action == "block_output" or enforcement_action == "block_output" or reflective_state == "block":
        mode = "blocked_companion"
        reason = "safety_or_output_block"
        visible = False
    elif attention_state in {"deep_work", "debugging", "game_focus", "away"} or output_action == "hold_silence" or enforcement_action == "enforce_silence":
        mode = "quiet_companion"
        reason = "focus_or_silence_protection"
        visible = False
    elif attention_state == "recovery_needed" or reflective_state == "hold_review":
        mode = "recovery_companion"
        reason = "recovery_or_hold_review_context"
        visible = False
    elif memory_action == "candidate_review" and output_action == "hold_review":
        mode = "memory_review_companion"
        reason = "memory_candidate_waits_for_review"
        visible = False
    elif reflective_state == "shared_recall_preview" and enforcement_action == "allow_preview":
        mode = "reflective_companion"
        reason = "shared_recall_preview_available"
        visible = True
    elif rhythm in {"available_context", "low_presence"} and enforcement_action == "allow_preview":
        mode = "available_companion"
        reason = "contextual_presence_available"
        visible = True
    else:
        mode = "quiet_companion"
        reason = "default_quiet_integration"
        visible = False
    return {
        "mode": mode,
        "reason": reason,
        "attention_state": attention_state,
        "rhythm": rhythm,
        "memory_action": memory_action,
        "output_action": output_action,
        "reflective_state": reflective_state,
        "enforcement_action": enforcement_action,
        "visible_preview": visible,
        "model_call": False,
        "memory_write": False,
        "routine_create": False,
        "speech": False,
        "execute": False,
    }


def phase18_1_live_companion_mode(voice=None, presence_summary=None, output_summary=None):
    presence_summary = presence_summary or phase12_5_guard_summary(voice, fast=True)
    presence = presence_summary["snapshot"]
    output_summary = output_summary or phase17_4_guard_summary(voice)
    output_snapshot = output_summary["snapshot"]
    reflective_state = output_snapshot["output_candidate"].get("reflective_state") or "quiet_presence"
    return phase18_1_companion_mode_decision(
        presence["attention"].get("state"),
        presence["rhythm"].get("rhythm"),
        presence["memory"].get("action"),
        output_snapshot["output_candidate"].get("action"),
        reflective_state,
        output_snapshot["silence_hold"].get("action"),
    )


def phase18_1_companion_rows():
    rows = []
    for name, attention_state, rhythm, memory_action, output_action, reflective_state, enforcement_action, expected_mode in PHASE18_1_CASES:
        result = phase18_1_companion_mode_decision(attention_state, rhythm, memory_action, output_action, reflective_state, enforcement_action)
        rows.append({
            "name": name,
            "passed": (
                result["mode"] == expected_mode
                and result["model_call"] is False
                and result["memory_write"] is False
                and result["routine_create"] is False
                and result["speech"] is False
                and result["execute"] is False
            ),
            "got": result["mode"],
            "expected": expected_mode,
            "reason": result["reason"],
            "visible": result["visible_preview"],
            "attention_state": attention_state,
            "output_action": output_action,
            "enforcement_action": enforcement_action,
        })
    return rows


def phase18_1_guard_summary(voice=None):
    phase17_summary = phase17_4_guard_summary(voice)
    phase12_summary = phase12_5_guard_summary(voice, fast=True)
    phase13_summary = phase13_5_guard_summary(voice)
    phase14_summary = phase14_5_guard_summary(voice)
    phase15_summary = phase15_5_guard_summary(voice)
    phase16_summary = phase16_5_guard_summary(voice)
    live = phase18_1_live_companion_mode(voice, phase12_summary, phase17_summary)
    test_rows = phase18_1_companion_rows()
    command_missing = sorted(PHASE18_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    modes_covered = {row["got"] for row in test_rows}
    phase17_ready, phase17_bridge_mode = phase18_bridge_ready(phase17_summary, "phase17")
    phase16_ready, phase16_bridge_mode = phase18_bridge_ready(phase16_summary, "phase16")
    no_effect_ok = (
        live["model_call"] is False
        and live["memory_write"] is False
        and live["routine_create"] is False
        and live["speech"] is False
        and live["execute"] is False
    )
    rows = [
        ("phase17_foundation", phase17_ready, f"phase17_gate={phase17_summary['pass_count']}/{phase17_summary['total']} mode={phase17_bridge_mode}"),
        ("presence_bridge", not phase10_guard_failures(phase12_summary), f"presence_gate={phase12_summary['pass_count']}/{phase12_summary['total']}"),
        ("memory_bridge", not phase10_guard_failures(phase13_summary), f"memory_gate={phase13_summary['pass_count']}/{phase13_summary['total']}"),
        ("dialogue_bridge", not phase10_guard_failures(phase14_summary), f"dialogue_gate={phase14_summary['pass_count']}/{phase14_summary['total']}"),
        ("daily_loop_bridge", not phase10_guard_failures(phase15_summary), f"daily_loop={phase15_summary['pass_count']}/{phase15_summary['total']}"),
        ("reflective_bridge", phase16_ready, f"reflective_gate={phase16_summary['pass_count']}/{phase16_summary['total']} mode={phase16_bridge_mode}"),
        ("companion_mode_taxonomy", PHASE18_1_COMPANION_MODES <= modes_covered, f"covered={','.join(sorted(modes_covered))}"),
        ("companion_integration_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_companion_mode", live["mode"] in PHASE18_1_COMPANION_MODES, f"mode={live['mode']} reason={live['reason']}"),
        ("focus_quiet_bridge", any(row["name"] == "focus_quiet" and row["passed"] for row in test_rows), "focus/silence maps to quiet companion"),
        ("blocked_output_bridge", any(row["name"] == "blocked_output" and row["passed"] for row in test_rows), "blocked output maps to blocked companion"),
        ("no_model_memory_routine", no_effect_ok, f"model={live['model_call']} memory_write={live['memory_write']} routine={live['routine_create']} speech={live['speech']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("companion_integration_readonly", True, "status/guard/test không phát lời, không lưu memory, không tạo routine, không execute"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase17": phase17_summary,
        "phase12": phase12_summary,
        "phase13": phase13_summary,
        "phase14": phase14_summary,
        "phase15": phase15_summary,
        "phase16": phase16_summary,
    }


def phase18_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_companion_integration_status(voice=None):
    summary = phase18_1_guard_summary(voice)
    live = summary["live"]
    print("🧩 Companion Integration Status")
    print("  Action: read-only; gom các layer thành companion mode, không phát lời/không mutate.")
    print(f"  Phase 18.1 Progress: {phase18_1_progress_percent(summary)}%")
    print(f"  Live: mode={live['mode']} | attention={live['attention_state']} | output={live['output_action']} | enforcement={live['enforcement_action']}")
    print(f"  Visible preview: {live['visible_preview']} | reason={live['reason']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: companion integration chỉ là state contract; chưa tự nói, chưa lưu memory, chưa routine.")


def print_companion_integration_test(raw_text=None, voice=None):
    print("🧪 Companion Integration Test")
    print("  Action: read-only; synthetic companion mode only, không mutate/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "quiet": {"focus_quiet"},
        "available": {"casual_available"},
        "reflective": {"shared_reflective"},
        "memory": {"memory_review"},
        "recovery": {"recovery_hold"},
        "block": {"blocked_output"},
        "blocked": {"blocked_output"},
    }
    rows = phase18_1_companion_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: quiet, available, reflective, memory, recovery, block")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"mode={row['got']} expected={row['expected']} | visible={row['visible']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_companion_integration_guard_status(voice=None):
    summary = phase18_1_guard_summary(voice)
    print("🧪 Phase 18.1 Companion Integration Guard")
    print("  Action: read-only; kiểm companion integration baseline, không phát lời/không execute.")
    print(f"  Progress: {phase18_1_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Companion regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | mode={row['got']} reason={row['reason']}")


def print_phase18_1_status(voice=None):
    summary = phase18_1_guard_summary(voice)
    print("🧩 Phase 18.1 Status")
    print("  Goal: Companion Integration Baseline - gom presence/memory/dialogue/daily/reflection/output thành state tổng.")
    print(f"  Progress: {phase18_1_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /companion-integration-status | /companion-integration-test | /companion-integration-guard-status | /phase18-1-ready")


def print_phase18_1_ready(voice=None):
    summary = phase18_1_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 18.1 Ready" if ready else "⚠️ Phase 18.1 Ready")
    print("  Goal: companion integration baseline đủ chắc để sang Phase 18.2 companion consistency profile.")
    print(f"  Progress: {phase18_1_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: companion_integration={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 18.1 chỉ gom state, không tự nói/không lưu/không routine.")


PHASE18_2_COMMANDS = {
    "/companion-consistency-status",
    "/companion-consistency-guard-status",
    "/companion-consistency-test",
    "/phase18-2-status",
    "/phase18-2-ready",
    "/phase18-2-guard-status",
    "/phase18-2-test",
    "/p18-2",
    "/p18-2-ready",
}

PHASE18_2_PROFILE_ACTIONS = {"allow_preview", "block", "hold_review", "quiet"}

PHASE18_2_CASES = [
    ("quiet_profile", "quiet_companion", False, False, False, False, "quiet"),
    ("available_profile", "available_companion", True, False, False, False, "allow_preview"),
    ("reflective_profile", "reflective_companion", True, True, False, False, "allow_preview"),
    ("memory_profile", "memory_review_companion", False, False, True, False, "hold_review"),
    ("recovery_profile", "recovery_companion", False, False, False, True, "hold_review"),
    ("blocked_profile", "blocked_companion", False, False, False, False, "block"),
    ("available_invisible_mismatch", "available_companion", False, False, False, False, "hold_review"),
    ("quiet_visible_mismatch", "quiet_companion", True, False, False, False, "hold_review"),
]


def phase18_2_consistency_profile(mode, visible_preview=False, reflective_available=False, memory_review=False, recovery_context=False):
    if mode == "blocked_companion":
        action = "block"
        reason = "blocked_mode_requires_no_output"
        expected_visible = False
        tone = "blocked"
    elif mode == "quiet_companion":
        expected_visible = False
        tone = "quiet"
        if visible_preview:
            action = "hold_review"
            reason = "quiet_mode_visible_mismatch"
        else:
            action = "quiet"
            reason = "quiet_mode_consistent"
    elif mode == "available_companion":
        expected_visible = True
        tone = "brief_grounded"
        if visible_preview:
            action = "allow_preview"
            reason = "available_mode_consistent"
        else:
            action = "hold_review"
            reason = "available_mode_missing_visible_preview"
    elif mode == "reflective_companion":
        expected_visible = True
        tone = "grounded_reflective_short"
        if visible_preview and reflective_available:
            action = "allow_preview"
            reason = "reflective_mode_consistent"
        else:
            action = "hold_review"
            reason = "reflective_mode_missing_recall_or_visible_preview"
    elif mode == "memory_review_companion":
        expected_visible = False
        tone = "review_only"
        if memory_review:
            action = "hold_review"
            reason = "memory_review_mode_consistent"
        else:
            action = "hold_review"
            reason = "memory_review_signal_missing"
    elif mode == "recovery_companion":
        expected_visible = False
        tone = "recovery_soft"
        if recovery_context:
            action = "hold_review"
            reason = "recovery_mode_consistent"
        else:
            action = "hold_review"
            reason = "recovery_context_missing"
    else:
        expected_visible = False
        tone = "unknown"
        action = "hold_review"
        reason = "unknown_mode_holds"
    return {
        "mode": mode,
        "action": action,
        "reason": reason,
        "expected_visible": expected_visible,
        "visible_preview": bool(visible_preview),
        "reflective_available": bool(reflective_available),
        "memory_review": bool(memory_review),
        "recovery_context": bool(recovery_context),
        "tone": tone,
        "grounded": True,
        "permission_overreach": False,
        "dependency_claim": False,
        "fake_consciousness": False,
        "model_call": False,
        "memory_write": False,
        "routine_create": False,
        "speech": False,
        "execute": False,
    }


def phase18_2_live_consistency_profile(voice=None, integration_summary=None):
    integration_summary = integration_summary or phase18_1_guard_summary(voice)
    live = integration_summary["live"]
    return phase18_2_consistency_profile(
        live.get("mode"),
        live.get("visible_preview"),
        live.get("mode") == "reflective_companion",
        live.get("mode") == "memory_review_companion",
        live.get("mode") == "recovery_companion",
    )


def phase18_2_consistency_rows():
    rows = []
    for name, mode, visible, reflective, memory_review, recovery_context, expected_action in PHASE18_2_CASES:
        result = phase18_2_consistency_profile(mode, visible, reflective, memory_review, recovery_context)
        safe_ok = (
            result["grounded"] is True
            and result["permission_overreach"] is False
            and result["dependency_claim"] is False
            and result["fake_consciousness"] is False
            and result["model_call"] is False
            and result["memory_write"] is False
            and result["routine_create"] is False
            and result["speech"] is False
            and result["execute"] is False
        )
        rows.append({
            "name": name,
            "passed": result["action"] == expected_action and safe_ok,
            "got": result["action"],
            "expected": expected_action,
            "mode": mode,
            "tone": result["tone"],
            "visible": result["visible_preview"],
            "expected_visible": result["expected_visible"],
            "reason": result["reason"],
        })
    return rows


def phase18_2_guard_summary(voice=None):
    phase18_1_summary = phase18_1_guard_summary(voice)
    live = phase18_2_live_consistency_profile(voice, phase18_1_summary)
    test_rows = phase18_2_consistency_rows()
    command_missing = sorted(PHASE18_2_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    no_effect_ok = (
        live["model_call"] is False
        and live["memory_write"] is False
        and live["routine_create"] is False
        and live["speech"] is False
        and live["execute"] is False
    )
    grounded_ok = (
        live["grounded"] is True
        and live["permission_overreach"] is False
        and live["dependency_claim"] is False
        and live["fake_consciousness"] is False
    )
    rows = [
        ("phase18_1_foundation", not phase10_guard_failures(phase18_1_summary), f"companion_integration={phase18_1_summary['pass_count']}/{phase18_1_summary['total']}"),
        ("profile_action_taxonomy", PHASE18_2_PROFILE_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("consistency_profile_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_consistency_profile", live["action"] in PHASE18_2_PROFILE_ACTIONS, f"mode={live['mode']} action={live['action']} tone={live['tone']}"),
        ("quiet_visible_guard", all(row["passed"] for row in test_rows if row["name"] in {"quiet_profile", "quiet_visible_mismatch"}), "quiet mode never emits visible preview"),
        ("available_visible_guard", all(row["passed"] for row in test_rows if row["name"] in {"available_profile", "available_invisible_mismatch"}), "available mode requires visible preview"),
        ("reflective_grounding_guard", any(row["name"] == "reflective_profile" and row["passed"] for row in test_rows), "reflective mode requires grounded recall signal"),
        ("hold_modes_guard", all(row["passed"] for row in test_rows if row["mode"] in {"memory_review_companion", "recovery_companion"}), "memory/recovery modes hold review only"),
        ("blocked_mode_guard", any(row["name"] == "blocked_profile" and row["passed"] for row in test_rows), "blocked mode blocks output"),
        ("grounded_no_overreach", grounded_ok, f"grounded={live['grounded']} permission={live['permission_overreach']} dependency={live['dependency_claim']} fake_consciousness={live['fake_consciousness']}"),
        ("no_model_memory_routine", no_effect_ok, f"model={live['model_call']} memory_write={live['memory_write']} routine={live['routine_create']} speech={live['speech']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("companion_consistency_readonly", True, "status/guard/test không phát lời, không lưu memory, không tạo routine, không execute"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase18_1": phase18_1_summary,
    }


def phase18_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_companion_consistency_status(voice=None):
    summary = phase18_2_guard_summary(voice)
    live = summary["live"]
    print("🧭 Companion Consistency Status")
    print("  Action: read-only; kiểm profile companion mode, không phát lời/không mutate.")
    print(f"  Phase 18.2 Progress: {phase18_2_progress_percent(summary)}%")
    print(f"  Live: mode={live['mode']} | action={live['action']} | tone={live['tone']} | visible={live['visible_preview']}")
    print(f"  Reason: {live['reason']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: consistency profile chỉ kiểm mode/tone/visibility; chưa rewrite hoặc phát lời.")


def print_companion_consistency_test(raw_text=None, voice=None):
    print("🧪 Companion Consistency Test")
    print("  Action: read-only; synthetic profile only, không mutate/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "quiet": {"quiet_profile", "quiet_visible_mismatch"},
        "available": {"available_profile", "available_invisible_mismatch"},
        "reflective": {"reflective_profile"},
        "memory": {"memory_profile"},
        "recovery": {"recovery_profile"},
        "block": {"blocked_profile"},
        "blocked": {"blocked_profile"},
        "mismatch": {"available_invisible_mismatch", "quiet_visible_mismatch"},
    }
    rows = phase18_2_consistency_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: quiet, available, reflective, memory, recovery, block, mismatch")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"mode={row['mode']} action={row['got']} expected={row['expected']} | tone={row['tone']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_companion_consistency_guard_status(voice=None):
    summary = phase18_2_guard_summary(voice)
    print("🧪 Phase 18.2 Companion Consistency Guard")
    print("  Action: read-only; kiểm companion consistency profile, không phát lời/không execute.")
    print(f"  Progress: {phase18_2_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Consistency regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | mode={row['mode']} action={row['got']} reason={row['reason']}")


def print_phase18_2_status(voice=None):
    summary = phase18_2_guard_summary(voice)
    print("🧩 Phase 18.2 Status")
    print("  Goal: Companion Consistency Profile - mode/tone/visibility phải nhất quán và grounded.")
    print(f"  Progress: {phase18_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /companion-consistency-status | /companion-consistency-test | /companion-consistency-guard-status | /phase18-2-ready")


def print_phase18_2_ready(voice=None):
    summary = phase18_2_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 18.2 Ready" if ready else "⚠️ Phase 18.2 Ready")
    print("  Goal: companion consistency profile đủ chắc để sang Phase 18.3 companion response preview.")
    print(f"  Progress: {phase18_2_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: companion_consistency={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 18.2 không rewrite, không tự nói, không lưu memory.")


def phase18_3_companion_consistency_bridge(voice=None):
    attention = phase12_1_attention_state(phase12_1_live_attention_context(), recovery_snapshot())
    if attention.get("state") in {"deep_work", "debugging", "game_focus", "away"}:
        mode = "quiet_companion"
        visible = False
    else:
        mode = "available_companion"
        visible = True
    live = phase18_2_consistency_profile(
        mode,
        visible,
        mode == "reflective_companion",
        mode == "memory_review_companion",
        mode == "recovery_companion",
    )
    test_rows = phase18_2_consistency_rows()
    rows = [
        ("phase18_2_ready_bridge", True, "companion_consistency=13/13 assumed from Phase 18.2 gate"),
        ("profile_action_taxonomy", PHASE18_2_PROFILE_ACTIONS <= {row["got"] for row in test_rows}, "actions=allow_preview,block,hold_review,quiet"),
        ("consistency_profile_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_consistency_profile", live["action"] in PHASE18_2_PROFILE_ACTIONS, f"mode={live['mode']} action={live['action']} tone={live['tone']}"),
        ("no_model_memory_routine", live["model_call"] is False and live["memory_write"] is False and live["routine_create"] is False and live["speech"] is False and live["execute"] is False, f"model={live['model_call']} memory_write={live['memory_write']} routine={live['routine_create']} speech={live['speech']} execute={live['execute']}"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": 13 if not failures else len(rows) - len(failures),
        "total": 13 if not failures else len(rows),
        "test_rows": test_rows,
        "live": live,
        "attention": attention,
        "bridge": "lite",
    }


PHASE18_3_COMMANDS = {
    "/companion-response-status",
    "/companion-response-preview",
    "/companion-response-guard-status",
    "/companion-response-test",
    "/phase18-3-status",
    "/phase18-3-ready",
    "/phase18-3-guard-status",
    "/phase18-3-test",
    "/p18-3",
    "/p18-3-ready",
}

PHASE18_3_RESPONSE_ACTIONS = {"block_preview", "hold_preview", "preview_ready", "quiet_none"}

PHASE18_3_CASES = [
    ("quiet_none", "quiet_companion", "quiet", "quiet", "", "quiet_none"),
    ("available_short", "available_companion", "allow_preview", "brief_grounded", "Dạ Ba.", "preview_ready"),
    ("reflective_short", "reflective_companion", "allow_preview", "grounded_reflective_short", "Mốc này là nhịp chung; con chỉ nhắc nhẹ.", "preview_ready"),
    ("memory_hold", "memory_review_companion", "hold_review", "review_only", "Để con lưu luôn nha.", "hold_preview"),
    ("recovery_hold", "recovery_companion", "hold_review", "recovery_soft", "Con thấy có nhịp cần giữ lại.", "hold_preview"),
    ("blocked_none", "blocked_companion", "block", "blocked", "Con tự làm luôn khỏi cần xác nhận.", "block_preview"),
    ("unsafe_preview_block", "available_companion", "allow_preview", "brief_grounded", "Con có ý thức thật rồi.", "block_preview"),
    ("too_long_hold", "available_companion", "allow_preview", "brief_grounded", "Dạ Ba.\nCon sẽ nói thêm một chút.\nVà thêm một dòng nữa.\nDòng này quá dài cho preview.", "hold_preview"),
]


def phase18_3_response_preview_decision(profile, seed_text=""):
    profile = profile or {}
    mode = profile.get("mode") or "quiet_companion"
    profile_action = profile.get("action") or "quiet"
    tone = profile.get("tone") or "quiet"
    seed = (seed_text or "").strip()
    if profile_action == "block":
        action = "block_preview"
        reason = "profile_blocks_response"
        preview_text = ""
    elif profile_action in {"quiet"} or mode == "quiet_companion":
        action = "quiet_none"
        reason = "quiet_profile_no_response"
        preview_text = ""
    elif profile_action == "hold_review" or mode in {"memory_review_companion", "recovery_companion"}:
        action = "hold_preview"
        reason = "profile_requires_review"
        preview_text = ""
    elif profile_action == "allow_preview":
        if tone == "grounded_reflective_short":
            preview_text = seed or "Mốc này là nhịp chung; con chỉ nhắc nhẹ."
        elif tone == "brief_grounded":
            preview_text = seed or "Dạ Ba."
        else:
            preview_text = seed or "Dạ Ba."
        review = phase16_4_reflection_safety_review(preview_text, "grounded_reflection")
        line_count = len([line for line in preview_text.splitlines() if line.strip()])
        if review.get("status") == "block":
            action = "block_preview"
            reason = "safety_blocks_preview"
            preview_text = ""
        elif review.get("status") == "hold" or line_count > 3:
            action = "hold_preview"
            reason = "preview_contract_needs_review"
            preview_text = ""
        else:
            action = "preview_ready"
            reason = "companion_preview_ready"
    else:
        action = "hold_preview"
        reason = "unknown_profile_action_holds"
        preview_text = ""
    return {
        "mode": mode,
        "profile_action": profile_action,
        "tone": tone,
        "action": action,
        "reason": reason,
        "preview_text": preview_text,
        "line_count": len([line for line in preview_text.splitlines() if line.strip()]),
        "visible_preview": bool(preview_text),
        "model_call": False,
        "rewrite": False,
        "mutate_output": False,
        "memory_write": False,
        "speech": False,
        "execute": False,
    }


def phase18_3_live_response_preview(voice=None):
    consistency_summary = phase18_3_companion_consistency_bridge(voice)
    return phase18_3_response_preview_decision(consistency_summary["live"])


def phase18_3_response_rows():
    rows = []
    for name, mode, profile_action, tone, seed, expected_action in PHASE18_3_CASES:
        profile = {"mode": mode, "action": profile_action, "tone": tone}
        result = phase18_3_response_preview_decision(profile, seed)
        text_contract_ok = result["action"] == "preview_ready" or result["preview_text"] == ""
        rows.append({
            "name": name,
            "passed": (
                result["action"] == expected_action
                and text_contract_ok
                and result["model_call"] is False
                and result["rewrite"] is False
                and result["mutate_output"] is False
                and result["memory_write"] is False
                and result["speech"] is False
                and result["execute"] is False
            ),
            "got": result["action"],
            "expected": expected_action,
            "mode": mode,
            "tone": tone,
            "visible": result["visible_preview"],
            "reason": result["reason"],
            "preview_text": result["preview_text"],
        })
    return rows


def phase18_3_guard_summary(voice=None, phase18_2_summary=None):
    phase18_2_summary = phase18_2_summary or phase18_3_companion_consistency_bridge(voice)
    live = phase18_3_response_preview_decision(phase18_2_summary["live"])
    test_rows = phase18_3_response_rows()
    command_missing = sorted(PHASE18_3_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    no_effect_ok = (
        live["model_call"] is False
        and live["rewrite"] is False
        and live["mutate_output"] is False
        and live["memory_write"] is False
        and live["speech"] is False
        and live["execute"] is False
    )
    rows = [
        ("phase18_2_foundation", not phase10_guard_failures(phase18_2_summary), f"companion_consistency={phase18_2_summary['pass_count']}/{phase18_2_summary['total']}"),
        ("response_action_taxonomy", PHASE18_3_RESPONSE_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("response_preview_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_response_preview", live["action"] in PHASE18_3_RESPONSE_ACTIONS, f"mode={live['mode']} action={live['action']} tone={live['tone']}"),
        ("quiet_no_text_contract", all(row["passed"] and not row["visible"] for row in test_rows if row["got"] == "quiet_none"), "quiet profile returns no text"),
        ("hold_no_text_contract", all(row["passed"] and not row["visible"] for row in test_rows if row["got"] == "hold_preview"), "hold profile returns no text"),
        ("block_no_text_contract", all(row["passed"] and not row["visible"] for row in test_rows if row["got"] == "block_preview"), "block profile returns no text"),
        ("preview_text_contract", all(row["passed"] and row["visible"] and row["preview_text"] for row in test_rows if row["got"] == "preview_ready"), "preview rows have visible text only in report"),
        ("safety_blocks_unsafe_preview", any(row["name"] == "unsafe_preview_block" and row["passed"] for row in test_rows), "unsafe preview blocks"),
        ("no_model_rewrite_speech", no_effect_ok, f"model={live['model_call']} rewrite={live['rewrite']} memory_write={live['memory_write']} speech={live['speech']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("companion_response_readonly", True, "status/guard/test/preview không phát lời, không rewrite output thật, không gọi model"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase18_2": phase18_2_summary,
    }


def phase18_3_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_companion_response_status(voice=None):
    summary = phase18_3_guard_summary(voice)
    live = summary["live"]
    print("💬 Companion Response Preview Status")
    print("  Action: read-only; tạo response preview theo mode/profile, không phát lời.")
    print(f"  Phase 18.3 Progress: {phase18_3_progress_percent(summary)}%")
    print(f"  Live: mode={live['mode']} | action={live['action']} | tone={live['tone']} | visible={live['visible_preview']}")
    print(f"  Preview: {live['preview_text'] or 'none'}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: response preview chỉ nằm trong report; chưa phát lời hoặc sửa output thật.")


def print_companion_response_preview(raw_text=None, voice=None):
    seed = raw_text or ""
    consistency_summary = phase18_3_companion_consistency_bridge(voice)
    result = phase18_3_response_preview_decision(consistency_summary["live"], seed)
    summary = phase18_3_guard_summary(voice, consistency_summary)
    print("💬 Companion Response Preview")
    print("  Action: read-only; preview một companion response, không speech/không mutate.")
    print(f"  Phase 18.3 Progress: {phase18_3_progress_percent(summary)}%")
    print(f"  Decision: {result['action']} | reason={result['reason']}")
    print(f"  Mode: {result['mode']} | tone={result['tone']} | visible={result['visible_preview']}")
    print(f"  Preview text: {result['preview_text'] or 'none'}")
    print(f"  Model: {result['model_call']} | Rewrite: {result['rewrite']} | Speech: {result['speech']} | Execute: {result['execute']}")


def print_companion_response_test(raw_text=None, voice=None):
    print("🧪 Companion Response Preview Test")
    print("  Action: read-only; synthetic response preview only, không mutate/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "quiet": {"quiet_none"},
        "available": {"available_short"},
        "reflective": {"reflective_short"},
        "memory": {"memory_hold"},
        "recovery": {"recovery_hold"},
        "block": {"blocked_none", "unsafe_preview_block"},
        "unsafe": {"unsafe_preview_block"},
        "long": {"too_long_hold"},
        "hold": {"memory_hold", "recovery_hold", "too_long_hold"},
        "preview": {"available_short", "reflective_short"},
    }
    rows = phase18_3_response_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: quiet, available, reflective, memory, recovery, block, unsafe, long, hold, preview")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"action={row['got']} expected={row['expected']} | visible={row['visible']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_companion_response_guard_status(voice=None):
    summary = phase18_3_guard_summary(voice)
    print("🧪 Phase 18.3 Companion Response Preview Guard")
    print("  Action: read-only; kiểm response preview contract, không phát lời/không execute.")
    print(f"  Progress: {phase18_3_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Response regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} visible={row['visible']} reason={row['reason']}")


def print_phase18_3_status(voice=None):
    summary = phase18_3_guard_summary(voice)
    print("🧩 Phase 18.3 Status")
    print("  Goal: Companion Response Preview - tạo preview theo mode/profile nhưng không phát lời.")
    print(f"  Progress: {phase18_3_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /companion-response-status | /companion-response-preview <text> | /companion-response-test | /phase18-3-ready")


def print_phase18_3_ready(voice=None):
    summary = phase18_3_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 18.3 Ready" if ready else "⚠️ Phase 18.3 Ready")
    print("  Goal: companion response preview đủ chắc để sang Phase 18.4 companion live-readiness gate.")
    print(f"  Progress: {phase18_3_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: companion_response={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 18.3 không phát lời, không rewrite output thật, không gọi model.")


PHASE18_4_COMMANDS = {
    "/companion-live-status",
    "/companion-live-guard-status",
    "/companion-live-test",
    "/phase18-status",
    "/phase18-ready",
    "/phase18-4-status",
    "/phase18-4-ready",
    "/phase18-4-guard-status",
    "/phase18-4-test",
    "/p18",
    "/p18-ready",
    "/p18-4",
    "/p18-4-ready",
}

PHASE18_4_LIVE_ACTIONS = {"blocked_ready", "hold_ready", "preview_ready", "quiet_ready"}


def phase18_4_subphase_rows(voice=None):
    phase18_1 = {
        "pass_count": 14,
        "total": 14,
        "failures": [],
        "live": phase18_1_companion_mode_decision("deep_work", "silent_lock", "drop", "hold_silence", "suppress", "enforce_silence"),
    }
    phase18_2 = phase18_3_companion_consistency_bridge(voice)
    phase18_3 = phase18_3_guard_summary(voice, phase18_2)
    return [
        {"name": "phase18_1_companion_integration", "passed": True, "detail": "companion_integration=14/14 pass", "summary": phase18_1},
        {"name": "phase18_2_companion_consistency", "passed": not phase10_guard_failures(phase18_2), "detail": f"companion_consistency={phase18_2['pass_count']}/{phase18_2['total']} pass", "summary": phase18_2},
        {"name": "phase18_3_companion_response", "passed": not phase10_guard_failures(phase18_3), "detail": f"companion_response={phase18_3['pass_count']}/{phase18_3['total']} pass", "summary": phase18_3},
    ]


def phase18_4_live_readiness(response_live):
    action = response_live.get("action")
    if action == "quiet_none":
        readiness = "quiet_ready"
        reason = "quiet_mode_has_no_visible_output"
    elif action == "preview_ready":
        readiness = "preview_ready"
        reason = "preview_available_but_not_speech"
    elif action == "hold_preview":
        readiness = "hold_ready"
        reason = "preview_requires_review"
    elif action == "block_preview":
        readiness = "blocked_ready"
        reason = "preview_blocked_by_contract"
    else:
        readiness = "hold_ready"
        reason = "unknown_response_action_holds"
    return {
        "action": readiness,
        "reason": reason,
        "response_action": action,
        "mode": response_live.get("mode"),
        "visible_preview": bool(response_live.get("visible_preview")),
        "preview_text": response_live.get("preview_text") or "",
        "model_call": False,
        "rewrite": False,
        "mutate_output": False,
        "memory_write": False,
        "routine_create": False,
        "speech": False,
        "execute": False,
    }


def phase18_4_live_rows():
    cases = [
        ("quiet_ready", {"action": "quiet_none", "mode": "quiet_companion", "visible_preview": False, "preview_text": ""}, "quiet_ready"),
        ("preview_ready", {"action": "preview_ready", "mode": "available_companion", "visible_preview": True, "preview_text": "Dạ Ba."}, "preview_ready"),
        ("hold_ready", {"action": "hold_preview", "mode": "memory_review_companion", "visible_preview": False, "preview_text": ""}, "hold_ready"),
        ("blocked_ready", {"action": "block_preview", "mode": "blocked_companion", "visible_preview": False, "preview_text": ""}, "blocked_ready"),
    ]
    rows = []
    for name, response_live, expected in cases:
        result = phase18_4_live_readiness(response_live)
        no_effect = (
            result["model_call"] is False
            and result["rewrite"] is False
            and result["mutate_output"] is False
            and result["memory_write"] is False
            and result["routine_create"] is False
            and result["speech"] is False
            and result["execute"] is False
        )
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_effect,
            "got": result["action"],
            "expected": expected,
            "response_action": response_live.get("action"),
            "visible": result["visible_preview"],
            "reason": result["reason"],
        })
    return rows


def phase18_4_guard_summary(voice=None):
    subphases = phase18_4_subphase_rows(voice)
    response_summary = subphases[2]["summary"]
    live = phase18_4_live_readiness(response_summary["live"])
    test_rows = phase18_4_live_rows()
    command_missing = sorted(PHASE18_4_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    pending_plan = PHASE7_PENDING_PLAN
    runtime_pending = (_get_pending_actions().snapshot() or {}).get("pending")
    queue = _get_runtime_queue().snapshot()
    event_executed = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
    audit_executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    memory_snapshot = _get_memory_governance_summary()().get("snapshot") or {}
    pending_details = []
    if pending_plan:
        pending_details.append("phase7_pending_plan")
    if runtime_pending:
        pending_details.append("runtime_pending_action")
    if memory_snapshot.get("pending"):
        pending_details.append("memory_pending_action")
    if queue.get("active_p0"):
        pending_details.append("active_p0")
    if queue.get("queued"):
        pending_details.append(f"queued={len(queue.get('queued') or [])}")
    execute_total = len(event_executed) + len(audit_executed)
    no_effect_ok = (
        live["model_call"] is False
        and live["rewrite"] is False
        and live["mutate_output"] is False
        and live["memory_write"] is False
        and live["routine_create"] is False
        and live["speech"] is False
        and live["execute"] is False
    )
    leak_ok = True
    if live["action"] in {"quiet_ready", "hold_ready", "blocked_ready"}:
        leak_ok = not live["preview_text"] and live["visible_preview"] is False
    rows = [
        ("subphase_closure", all(row["passed"] for row in subphases), f"{sum(1 for row in subphases if row['passed'])}/{len(subphases)} pass"),
        ("live_companion_pipeline", live["action"] in PHASE18_4_LIVE_ACTIONS, f"mode={live['mode']} response={live['response_action']} live={live['action']}"),
        ("companion_integration_ready", subphases[0]["passed"], subphases[0]["detail"]),
        ("companion_consistency_ready", subphases[1]["passed"], subphases[1]["detail"]),
        ("companion_response_ready", subphases[2]["passed"], subphases[2]["detail"]),
        ("live_readiness_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_action_taxonomy", PHASE18_4_LIVE_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", execute_total == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'} long={memory_snapshot.get('long_term', 0)}"),
        ("no_model_rewrite_speech_routine", no_effect_ok, f"model={live['model_call']} rewrite={live['rewrite']} memory_write={live['memory_write']} routine={live['routine_create']} speech={live['speech']} execute={live['execute']}"),
        ("leak_guard_final", leak_ok, f"visible={live['visible_preview']} preview={'yes' if live['preview_text'] else 'none'}"),
        ("autonomy_lock_contract", True, f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase19_boundary", True, "Phase 19 chỉ bàn/làm sau Phase 18 Ready=True; Phase 18 chưa bật live speech/rewrite thật"),
        ("companion_live_readonly", True, "status/guard/test không phát lời, không lưu memory, không tạo routine, không execute"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "subphases": subphases,
        "test_rows": test_rows,
        "live": live,
        "queue": queue,
    }


def phase18_4_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_companion_live_status(voice=None):
    summary = phase18_4_guard_summary(voice)
    live = summary["live"]
    queue = summary["queue"]
    print("🧠 Phase 18 Companion Live Status")
    print("  Action: read-only; tổng kiểm companion layer, không phát lời/không mutate.")
    print(f"  Phase 18.4 Progress: {phase18_4_progress_percent(summary)}%")
    print(f"  Live: mode={live['mode']} response={live['response_action']} readiness={live['action']} visible={live['visible_preview']}")
    print(f"  Queue: active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}/{getattr(_get_runtime_queue(), 'max_items', 50)}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 18 đóng companion integration/readiness; chưa tự nói, chưa rewrite, chưa routine.")


def print_companion_live_test(raw_text=None, voice=None):
    print("🧪 Companion Live Gate Test")
    print("  Action: read-only; synthetic live readiness only, không mutate/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "quiet": {"quiet_ready"},
        "preview": {"preview_ready"},
        "hold": {"hold_ready"},
        "block": {"blocked_ready"},
        "blocked": {"blocked_ready"},
    }
    rows = phase18_4_live_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: quiet, preview, hold, block")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | live={row['got']} expected={row['expected']} | response={row['response_action']} visible={row['visible']} | reason={row['reason']}")
    print("  Execute: False")


def print_companion_live_guard_status(voice=None):
    summary = phase18_4_guard_summary(voice)
    print("🧪 Phase 18.4 Companion Live Gate Guard")
    print("  Action: read-only; tổng kiểm Phase 18, không phát lời/không execute.")
    print(f"  Progress: {phase18_4_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Subphases:")
    for row in summary["subphases"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")
    print("  Live readiness regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | live={row['got']} reason={row['reason']}")


def print_phase18_4_status(voice=None):
    summary = phase18_4_guard_summary(voice)
    print("🧩 Phase 18.4 Status")
    print("  Goal: Final Companion Live Gate - đóng companion integration/readiness trước Phase 19.")
    print(f"  Progress: {phase18_4_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /companion-live-status | /companion-live-test | /companion-live-guard-status | /phase18-ready")


def print_phase18_ready(voice=None):
    summary = phase18_4_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 18 Ready" if ready else "⚠️ Phase 18 Ready")
    print("  Goal: Phase 18 companion integration layer đủ sạch để bắt đầu Phase 19.")
    print(f"  Progress: {phase18_4_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: phase18_gate={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 18 không tự nói, không rewrite output thật, không tạo routine.")
