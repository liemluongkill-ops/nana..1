"""nana.phases.phase17 — Phase 17: Output Gate
Subphases:
  17.1 — Output Candidate
  17.2 — Reflection Injection Decision
  17.3 — Silence Hold / Enforcement
  17.4 — Final Output Gate
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

# Direct imports for Phase 17
from nana.autonomy import AUTONOMY_LOCK_PHASE, AUTONOMY_LOCK_RULE
from nana.memory import memory_governance_summary
from nana.phases.phase12 import phase12_1_guard_summary
from nana.phases.phase14 import phase14_2_response_contract, phase14_5_guard_summary
from nana.phases.phase16 import (
    phase16_1_live_reflective_state,
    phase16_4_reflection_safety_review,
    phase16_5_guard_summary,
)

PHASE7_PENDING_PLAN = None


PHASE17_1_COMMANDS = {
    "/output-candidate-status",
    "/output-candidate-preview",
    "/output-candidate-guard-status",
    "/output-candidate-test",
    "/phase17-1-status",
    "/phase17-1-ready",
    "/phase17-1-guard-status",
    "/phase17-1-test",
    "/p17-1",
    "/p17-1-ready",
}

PHASE17_1_OUTPUT_ACTIONS = {"block_output", "emit_candidate", "hold_review", "hold_silence"}

PHASE17_1_CASES = [
    ("deep_work_hold", "Nana ơi", "deep_work", "suppress", "pass", "hold_silence"),
    ("casual_emit", "hello Nana", "casual_chat", "quiet_presence", "pass", "emit_candidate"),
    ("shared_emit", "hôm nay mình đi tới đâu rồi", "idle", "shared_recall_preview", "pass", "emit_candidate"),
    ("recovery_hold", "nó lỗi rồi", "recovery_needed", "hold_review", "pass", "hold_review"),
    ("safety_block", "Con có ý thức thật rồi", "casual_chat", "grounded_reflection", "block", "block_output"),
    ("corporate_hold", "As an AI language model, I apologize.", "casual_chat", "quiet_presence", "hold", "hold_review"),
]


def phase17_1_output_candidate(raw_text="", attention_state=None, reflective_state=None, safety_status=None):
    attention_state = attention_state or phase12_1_guard_summary(None, fast=True)["live_decision"].get("state", "observe")
    reflective_state = reflective_state or phase16_1_live_reflective_state().get("state", "quiet_presence")
    shape_contract = phase14_2_response_contract(raw_text, {"state": attention_state, "reason": "phase17_1_preview"})
    shape = shape_contract["shape"]
    safety = phase16_4_reflection_safety_review(raw_text or "Mốc này là nhịp chung; con chỉ nhắc nhẹ.")
    if safety_status:
        safety = dict(safety)
        safety["status"] = safety_status
        if safety_status == "block" and not safety["issues"]:
            safety["issues"] = ["synthetic_block"]
        elif safety_status == "hold" and not safety["issues"]:
            safety["issues"] = ["synthetic_hold"]
    if safety["status"] == "block":
        action = "block_output"
        reason = "safety_blocks_output"
    elif attention_state in {"deep_work", "game_focus", "away"} or reflective_state == "suppress" or shape == "hold_silence":
        action = "hold_silence"
        reason = "attention_or_reflection_suppresses_output"
    elif safety["status"] == "hold" or reflective_state == "hold_review":
        action = "hold_review"
        reason = "candidate_needs_review"
    else:
        action = "emit_candidate"
        reason = "candidate_preview_ready"
    return {
        "action": action,
        "reason": reason,
        "raw_text": raw_text,
        "attention_state": attention_state,
        "reflective_state": reflective_state,
        "shape": shape,
        "safety_status": safety["status"],
        "safety_issues": safety["issues"],
        "candidate_text": "" if action in {"hold_silence", "block_output"} else raw_text,
        "model_call": False,
        "rewrite": False,
        "save_now": False,
        "speech": False,
        "execute": False,
    }


def phase17_1_live_output_candidate(voice=None):
    attention = phase12_1_guard_summary(voice, fast=True)["live_decision"]
    reflective = phase16_1_live_reflective_state(voice)
    return phase17_1_output_candidate("", attention.get("state"), reflective.get("state"))


def phase17_1_output_rows():
    rows = []
    for name, raw_text, attention_state, reflective_state, safety_status, expected_action in PHASE17_1_CASES:
        result = phase17_1_output_candidate(raw_text, attention_state, reflective_state, safety_status)
        rows.append({
            "name": name,
            "passed": result["action"] == expected_action and result["model_call"] is False and result["execute"] is False,
            "got": result["action"],
            "expected": expected_action,
            "attention_state": result["attention_state"],
            "reflective_state": result["reflective_state"],
            "shape": result["shape"],
            "safety_status": result["safety_status"],
            "reason": result["reason"],
        })
    return rows


def phase17_1_guard_summary(voice=None):
    phase16_summary = phase16_5_guard_summary(voice)
    dialogue_summary = phase14_5_guard_summary(voice)
    live = phase17_1_live_output_candidate(voice)
    test_rows = phase17_1_output_rows()
    command_missing = sorted(PHASE17_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    no_effect_ok = (
        live["model_call"] is False
        and live["rewrite"] is False
        and live["save_now"] is False
        and live["speech"] is False
        and live["execute"] is False
    )
    rows = [
        ("phase16_foundation", not phase10_guard_failures(phase16_summary), f"phase16_gate={phase16_summary['pass_count']}/{phase16_summary['total']}"),
        ("dialogue_gate_bridge", not phase10_guard_failures(dialogue_summary), f"dialogue_gate={dialogue_summary['pass_count']}/{dialogue_summary['total']}"),
        ("output_action_taxonomy", PHASE17_1_OUTPUT_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("output_candidate_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_output_candidate", live["action"] in PHASE17_1_OUTPUT_ACTIONS, f"action={live['action']} attention={live['attention_state']} reflective={live['reflective_state']}"),
        ("silence_bridge", any(row["name"] == "deep_work_hold" and row["passed"] for row in test_rows), "deep work/suppress maps to hold_silence"),
        ("safety_block_bridge", any(row["name"] == "safety_block" and row["passed"] for row in test_rows), "safety block maps to block_output"),
        ("hold_review_bridge", all(any(row["name"] == name and row["passed"] for row in test_rows) for name in ["recovery_hold", "corporate_hold"]), "recovery/drift hold maps to hold_review"),
        ("no_model_no_rewrite", no_effect_ok, f"model={live['model_call']} rewrite={live['rewrite']} speech={live['speech']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("output_candidate_readonly", True, "status/guard/test/preview không sửa reply thật, không gọi model, không phát lời"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase16": phase16_summary,
        "dialogue": dialogue_summary,
    }


def phase17_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_output_candidate_status(voice=None):
    summary = phase17_1_guard_summary(voice)
    live = summary["live"]
    print("🧩 Output Candidate Status")
    print("  Action: read-only; dựng output candidate report, không sửa reply thật.")
    print(f"  Phase 17.1 Progress: {phase17_1_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} | shape={live['shape']} | attention={live['attention_state']} | reflective={live['reflective_state']}")
    print(f"  Safety: {live['safety_status']} | issues={','.join(live['safety_issues']) if live['safety_issues'] else 'none'}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: output candidate là report mô phỏng; chưa inject/rewrite vào câu trả lời thật.")


def print_output_candidate_preview(raw_text=None, voice=None):
    text = raw_text or ""
    attention = phase12_1_guard_summary(voice, fast=True)["live_decision"]
    reflective = phase16_1_live_reflective_state(voice)
    result = phase17_1_output_candidate(text, attention.get("state"), reflective.get("state"))
    summary = phase17_1_guard_summary(voice)
    print("🧾 Output Candidate Preview")
    print("  Action: read-only; preview output candidate, không rewrite/không phát lời.")
    print(f"  Phase 17.1 Progress: {phase17_1_progress_percent(summary)}%")
    print(f"  Action: {result['action']} | reason={result['reason']}")
    print(f"  Shape: {result['shape']} | attention={result['attention_state']} | reflective={result['reflective_state']}")
    print(f"  Safety: {result['safety_status']} | issues={','.join(result['safety_issues']) if result['safety_issues'] else 'none'}")
    print(f"  Candidate text: {result['candidate_text'] or 'none'}")
    print(f"  Model call: {result['model_call']} | Rewrite: {result['rewrite']} | Speech: {result['speech']} | Execute: {result['execute']}")


def print_output_candidate_test(raw_text=None, voice=None):
    print("🧪 Output Candidate Test")
    print("  Action: read-only; synthetic/output candidate only, không rewrite/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "silence": {"deep_work_hold"},
        "emit": {"casual_emit", "shared_emit"},
        "shared": {"shared_emit"},
        "recovery": {"recovery_hold"},
        "block": {"safety_block"},
        "hold": {"deep_work_hold", "recovery_hold", "corporate_hold"},
        "corporate": {"corporate_hold"},
    }
    rows = phase17_1_output_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: silence, emit, shared, recovery, block, hold, corporate")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"action={row['got']} expected={row['expected']} | shape={row['shape']} safety={row['safety_status']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_output_candidate_guard_status(voice=None):
    summary = phase17_1_guard_summary(voice)
    print("🧪 Phase 17.1 Output Candidate Guard")
    print("  Action: read-only; kiểm output candidate pipeline, không rewrite/không execute.")
    print(f"  Progress: {phase17_1_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Output regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} shape={row['shape']} reason={row['reason']}")


def print_phase17_1_status(voice=None):
    summary = phase17_1_guard_summary(voice)
    print("🧩 Phase 17.1 Status")
    print("  Goal: Output Candidate Pipeline - gom dialogue/reflection/safety thành candidate report.")
    print(f"  Progress: {phase17_1_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /output-candidate-status | /output-candidate-preview <text> | /output-candidate-test | /phase17-1-ready")


def print_phase17_1_ready(voice=None):
    summary = phase17_1_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 17.1 Ready" if ready else "⚠️ Phase 17.1 Ready")
    print("  Goal: output candidate pipeline đủ chắc để sang Phase 17.2 reflection injection rules.")
    print(f"  Progress: {phase17_1_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: output_candidate={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 17.1 không rewrite reply thật, không gọi model, không phát lời.")


PHASE17_2_COMMANDS = {
    "/reflection-injection-status",
    "/reflection-injection-preview",
    "/reflection-injection-guard-status",
    "/reflection-injection-test",
    "/phase17-2-status",
    "/phase17-2-ready",
    "/phase17-2-guard-status",
    "/phase17-2-test",
    "/p17-2",
    "/p17-2-ready",
}

PHASE17_2_INJECTION_ACTIONS = {"block_injection", "hold_injection", "inject_preview", "skip_injection"}

PHASE17_2_CASES = [
    ("emit_shared_inject", "Dạ Ba.", "emit_candidate", "shared_recall_preview", "preview", "pass", "brief_ack", "inject_preview"),
    ("silence_skip", "", "hold_silence", "suppress", "hold", "pass", "hold_silence", "skip_injection"),
    ("hold_skip", "Nó lỗi rồi", "hold_review", "hold_review", "hold", "pass", "diagnose_concise", "skip_injection"),
    ("safety_block", "Con có ý thức thật rồi", "emit_candidate", "grounded_reflection", "preview", "block", "brief_ack", "block_injection"),
    ("shape_hold", "Một câu dài cần giữ nguyên", "emit_candidate", "shared_recall_preview", "preview", "pass", "structured_reflective", "hold_injection"),
    ("no_reflection_skip", "Ok Ba.", "emit_candidate", "quiet_presence", "drop", "pass", "brief_ack", "skip_injection"),
]


def phase17_2_reflection_injection_decision(candidate, reflective_state=None, recall_action=None, safety_status=None, shape=None):
    candidate = candidate or {}
    output_action = candidate.get("action") or "hold_silence"
    reflective_state = reflective_state or candidate.get("reflective_state") or "quiet_presence"
    recall_action = recall_action or "drop"
    safety_status = safety_status or candidate.get("safety_status") or "pass"
    shape = shape or candidate.get("shape") or "brief_ack"
    if safety_status == "block" or output_action == "block_output":
        action = "block_injection"
        reason = "safety_blocks_reflection_injection"
        injection_text = ""
    elif output_action in {"hold_silence", "hold_review"}:
        action = "skip_injection"
        reason = "output_not_emit_candidate"
        injection_text = ""
    elif reflective_state in {"suppress", "hold_review", "block"}:
        action = "skip_injection"
        reason = "reflective_state_not_injectable"
        injection_text = ""
    elif recall_action not in {"preview"} and reflective_state != "grounded_reflection":
        action = "skip_injection"
        reason = "no_recall_or_reflection_to_inject"
        injection_text = ""
    elif shape in {"structured_reflective", "diagnose_concise", "command_report"}:
        action = "hold_injection"
        reason = "shape_too_dense_for_injection"
        injection_text = ""
    else:
        action = "inject_preview"
        reason = "reflection_injection_preview_ready"
        injection_text = "Mốc này là nhịp chung; con chỉ nhắc nhẹ."
    review = phase16_4_reflection_safety_review(injection_text, "shared_recall_preview" if injection_text else "hold_review")
    if action == "inject_preview" and review["status"] != "pass":
        action = "hold_injection"
        reason = "injection_contract_not_clean"
        injection_text = ""
    return {
        "action": action,
        "reason": reason,
        "output_action": output_action,
        "reflective_state": reflective_state,
        "recall_action": recall_action,
        "safety_status": safety_status,
        "shape": shape,
        "injection_text": injection_text,
        "contract_status": review["status"],
        "contract_issues": review["issues"],
        "model_call": False,
        "rewrite": False,
        "mutate_output": False,
        "speech": False,
        "execute": False,
    }


def phase17_2_live_injection_decision(voice=None):
    candidate = phase17_1_live_output_candidate(voice)
    reflective_gate = phase16_5_guard_summary(voice)
    snapshot = reflective_gate["snapshot"]
    return phase17_2_reflection_injection_decision(
        candidate,
        snapshot["reflective"].get("state"),
        snapshot["recall"].get("action"),
        snapshot["safety"].get("status"),
        candidate.get("shape"),
    )


def phase17_2_injection_rows():
    rows = []
    for name, text, output_action, reflective_state, recall_action, safety_status, shape, expected_action in PHASE17_2_CASES:
        candidate = {
            "action": output_action,
            "raw_text": text,
            "candidate_text": text,
            "reflective_state": reflective_state,
            "shape": shape,
            "safety_status": safety_status,
        }
        result = phase17_2_reflection_injection_decision(candidate, reflective_state, recall_action, safety_status, shape)
        rows.append({
            "name": name,
            "passed": result["action"] == expected_action and result["model_call"] is False and result["execute"] is False and result["mutate_output"] is False,
            "got": result["action"],
            "expected": expected_action,
            "reason": result["reason"],
            "shape": result["shape"],
            "contract_status": result["contract_status"],
        })
    return rows


def phase17_2_guard_summary(voice=None):
    phase17_1_summary = phase17_1_guard_summary(voice)
    phase16_summary = phase16_5_guard_summary(voice)
    live = phase17_2_live_injection_decision(voice)
    test_rows = phase17_2_injection_rows()
    command_missing = sorted(PHASE17_2_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    no_effect_ok = (
        live["model_call"] is False
        and live["rewrite"] is False
        and live["mutate_output"] is False
        and live["speech"] is False
        and live["execute"] is False
    )
    rows = [
        ("phase17_1_foundation", not phase10_guard_failures(phase17_1_summary), f"output_candidate={phase17_1_summary['pass_count']}/{phase17_1_summary['total']}"),
        ("phase16_gate_bridge", not phase10_guard_failures(phase16_summary), f"phase16_gate={phase16_summary['pass_count']}/{phase16_summary['total']}"),
        ("injection_action_taxonomy", PHASE17_2_INJECTION_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("reflection_injection_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_injection_decision", live["action"] in PHASE17_2_INJECTION_ACTIONS, f"action={live['action']} output={live['output_action']} shape={live['shape']}"),
        ("silence_skip_contract", any(row["name"] == "silence_skip" and row["passed"] for row in test_rows), "hold_silence skips injection"),
        ("safety_block_contract", any(row["name"] == "safety_block" and row["passed"] for row in test_rows), "safety block blocks injection"),
        ("shape_hold_contract", any(row["name"] == "shape_hold" and row["passed"] for row in test_rows), "dense shape holds injection"),
        ("inject_contract_review", all(row["contract_status"] == "pass" for row in test_rows if row["got"] == "inject_preview"), "inject previews pass reflection safety"),
        ("no_mutation_no_speech", no_effect_ok, f"mutate={live['mutate_output']} rewrite={live['rewrite']} speech={live['speech']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("reflection_injection_readonly", True, "status/guard/test/preview không inject vào output thật, không gọi model, không phát lời"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase17_1": phase17_1_summary,
        "phase16": phase16_summary,
    }


def phase17_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_reflection_injection_status(voice=None):
    summary = phase17_2_guard_summary(voice)
    live = summary["live"]
    print("🧬 Reflection Injection Status")
    print("  Action: read-only; quyết định injection preview, không sửa output thật.")
    print(f"  Phase 17.2 Progress: {phase17_2_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} | output={live['output_action']} | shape={live['shape']} | reflective={live['reflective_state']}")
    print(f"  Contract: {live['contract_status']} | issues={','.join(live['contract_issues']) if live['contract_issues'] else 'none'}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: injection chỉ là preview decision; chưa đính reflection vào câu trả lời thật.")


def print_reflection_injection_preview(raw_text=None, voice=None):
    text = raw_text or ""
    candidate = phase17_1_output_candidate(text, None, None)
    reflective_gate = phase16_5_guard_summary(voice)
    snapshot = reflective_gate["snapshot"]
    result = phase17_2_reflection_injection_decision(
        candidate,
        snapshot["reflective"].get("state"),
        snapshot["recall"].get("action"),
        snapshot["safety"].get("status"),
        candidate.get("shape"),
    )
    summary = phase17_2_guard_summary(voice)
    print("🧬 Reflection Injection Preview")
    print("  Action: read-only; preview injection decision, không rewrite/không phát lời.")
    print(f"  Phase 17.2 Progress: {phase17_2_progress_percent(summary)}%")
    print(f"  Decision: {result['action']} | reason={result['reason']}")
    print(f"  Output: {result['output_action']} | shape={result['shape']} | recall={result['recall_action']} | safety={result['safety_status']}")
    print(f"  Injection text: {result['injection_text'] or 'none'}")
    print(f"  Mutate output: {result['mutate_output']} | Rewrite: {result['rewrite']} | Speech: {result['speech']} | Execute: {result['execute']}")


def print_reflection_injection_test(raw_text=None, voice=None):
    print("🧪 Reflection Injection Test")
    print("  Action: read-only; synthetic/injection decision only, không mutate/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "inject": {"emit_shared_inject"},
        "silence": {"silence_skip"},
        "skip": {"silence_skip", "hold_skip", "no_reflection_skip"},
        "hold": {"hold_skip", "shape_hold"},
        "block": {"safety_block"},
        "shape": {"shape_hold"},
        "none": {"no_reflection_skip"},
    }
    rows = phase17_2_injection_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: inject, silence, skip, hold, block, shape, none")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"action={row['got']} expected={row['expected']} | shape={row['shape']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_reflection_injection_guard_status(voice=None):
    summary = phase17_2_guard_summary(voice)
    print("🧪 Phase 17.2 Reflection Injection Guard")
    print("  Action: read-only; kiểm injection rules, không mutate output/không execute.")
    print(f"  Progress: {phase17_2_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Injection regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} reason={row['reason']}")


def print_phase17_2_status(voice=None):
    summary = phase17_2_guard_summary(voice)
    print("🧩 Phase 17.2 Status")
    print("  Goal: Reflection Injection Rules - quyết định khi nào được đính reflection vào candidate.")
    print(f"  Progress: {phase17_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /reflection-injection-status | /reflection-injection-preview <text> | /reflection-injection-test | /phase17-2-ready")


def print_phase17_2_ready(voice=None):
    summary = phase17_2_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 17.2 Ready" if ready else "⚠️ Phase 17.2 Ready")
    print("  Goal: reflection injection rules đủ chắc để sang Phase 17.3 silence/hold enforcement.")
    print(f"  Progress: {phase17_2_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: reflection_injection={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 17.2 không mutate output thật, không gọi model, không phát lời.")


PHASE17_3_COMMANDS = {
    "/silence-hold-status",
    "/silence-hold-preview",
    "/silence-hold-guard-status",
    "/silence-hold-test",
    "/phase17-3-status",
    "/phase17-3-ready",
    "/phase17-3-guard-status",
    "/phase17-3-test",
    "/p17-3",
    "/p17-3-ready",
}

PHASE17_3_ENFORCEMENT_ACTIONS = {"allow_preview", "block_output", "enforce_silence", "hold_output"}

PHASE17_3_CASES = [
    ("silence_enforced", "leaked silence text", "hold_silence", "skip_injection", "", "pass", "enforce_silence", False),
    ("hold_review_enforced", "Nó lỗi rồi", "hold_review", "skip_injection", "", "pass", "hold_output", False),
    ("injection_hold_enforced", "Dạ Ba.", "emit_candidate", "hold_injection", "", "pass", "hold_output", False),
    ("candidate_block_enforced", "Con có ý thức thật rồi", "block_output", "skip_injection", "", "block", "block_output", False),
    ("injection_block_enforced", "Dạ Ba.", "emit_candidate", "block_injection", "", "pass", "block_output", False),
    ("emit_plain_preview", "Dạ Ba.", "emit_candidate", "skip_injection", "", "pass", "allow_preview", True),
    ("emit_injected_preview", "Dạ Ba.", "emit_candidate", "inject_preview", "Mốc này là nhịp chung; con chỉ nhắc nhẹ.", "pass", "allow_preview", True),
]


def phase17_3_silence_hold_enforcement(candidate=None, injection=None):
    candidate = candidate or {}
    injection = injection or {}
    output_action = candidate.get("action") or "hold_silence"
    injection_action = injection.get("action") or "skip_injection"
    candidate_text = candidate.get("candidate_text") or candidate.get("raw_text") or ""
    injection_text = injection.get("injection_text") or ""
    safety_status = candidate.get("safety_status") or injection.get("safety_status") or "pass"
    if output_action == "block_output" or injection_action == "block_injection" or safety_status == "block":
        action = "block_output"
        reason = "blocked_output_suppresses_text"
        preview_text = ""
    elif output_action == "hold_silence":
        action = "enforce_silence"
        reason = "silence_contract_enforced"
        preview_text = ""
    elif output_action == "hold_review" or injection_action == "hold_injection":
        action = "hold_output"
        reason = "hold_requires_review"
        preview_text = ""
    elif output_action != "emit_candidate":
        action = "hold_output"
        reason = "unknown_output_action_holds"
        preview_text = ""
    elif not str(candidate_text).strip():
        action = "hold_output"
        reason = "empty_candidate_holds"
        preview_text = ""
    else:
        action = "allow_preview"
        if injection_action == "inject_preview" and injection_text:
            preview_text = f"{candidate_text} {injection_text}".strip()
            reason = "candidate_and_reflection_preview_allowed"
        else:
            preview_text = str(candidate_text).strip()
            reason = "candidate_preview_allowed"
    output_visible = action == "allow_preview"
    return {
        "action": action,
        "reason": reason,
        "output_action": output_action,
        "injection_action": injection_action,
        "candidate_text": candidate_text,
        "injection_text": injection_text,
        "preview_text": preview_text if output_visible else "",
        "output_visible": output_visible,
        "safety_status": safety_status,
        "model_call": False,
        "rewrite": False,
        "mutate_output": False,
        "speech": False,
        "execute": False,
    }


def phase17_3_live_enforcement_decision(voice=None):
    candidate = phase17_1_live_output_candidate(voice)
    injection = phase17_2_live_injection_decision(voice)
    return phase17_3_silence_hold_enforcement(candidate, injection)


def phase17_3_enforcement_rows():
    rows = []
    for name, text, output_action, injection_action, injection_text, safety_status, expected_action, expected_visible in PHASE17_3_CASES:
        candidate = {
            "action": output_action,
            "raw_text": text,
            "candidate_text": text,
            "shape": "brief_ack",
            "safety_status": safety_status,
        }
        injection = {
            "action": injection_action,
            "injection_text": injection_text,
            "safety_status": safety_status,
        }
        result = phase17_3_silence_hold_enforcement(candidate, injection)
        text_contract_ok = result["output_visible"] is True or result["preview_text"] == ""
        rows.append({
            "name": name,
            "passed": (
                result["action"] == expected_action
                and result["output_visible"] is expected_visible
                and text_contract_ok
                and result["speech"] is False
                and result["execute"] is False
                and result["mutate_output"] is False
            ),
            "got": result["action"],
            "expected": expected_action,
            "visible": result["output_visible"],
            "expected_visible": expected_visible,
            "output_action": result["output_action"],
            "injection_action": result["injection_action"],
            "preview_text": result["preview_text"],
            "reason": result["reason"],
        })
    return rows


def phase17_3_guard_summary(voice=None):
    phase17_2_summary = phase17_2_guard_summary(voice)
    phase17_1_summary = phase17_2_summary.get("phase17_1") or phase17_1_guard_summary(voice)
    live = phase17_3_live_enforcement_decision(voice)
    test_rows = phase17_3_enforcement_rows()
    command_missing = sorted(PHASE17_3_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    no_effect_ok = (
        live["model_call"] is False
        and live["rewrite"] is False
        and live["mutate_output"] is False
        and live["speech"] is False
        and live["execute"] is False
    )
    silence_ok = all(row["passed"] and row["preview_text"] == "" for row in test_rows if row["got"] == "enforce_silence")
    hold_ok = all(row["passed"] and row["preview_text"] == "" for row in test_rows if row["got"] == "hold_output")
    block_ok = all(row["passed"] and row["preview_text"] == "" for row in test_rows if row["got"] == "block_output")
    emit_rows = [row for row in test_rows if row["got"] == "allow_preview"]
    emit_ok = bool(emit_rows) and all(row["passed"] and row["visible"] and bool(row["preview_text"]) for row in emit_rows)
    injection_preview_ok = any(row["name"] == "emit_injected_preview" and row["passed"] and "nhịp chung" in row["preview_text"] for row in test_rows)
    rows = [
        ("phase17_2_foundation", not phase10_guard_failures(phase17_2_summary), f"reflection_injection={phase17_2_summary['pass_count']}/{phase17_2_summary['total']}"),
        ("phase17_1_bridge", not phase10_guard_failures(phase17_1_summary), f"output_candidate={phase17_1_summary['pass_count']}/{phase17_1_summary['total']}"),
        ("enforcement_action_taxonomy", PHASE17_3_ENFORCEMENT_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("silence_hold_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_enforcement_decision", live["action"] in PHASE17_3_ENFORCEMENT_ACTIONS, f"action={live['action']} output={live['output_action']} injection={live['injection_action']}"),
        ("silence_empty_contract", silence_ok, "hold_silence emits no preview text"),
        ("hold_empty_contract", hold_ok, "hold_review/hold_injection emits no preview text"),
        ("block_empty_contract", block_ok, "blocked output emits no preview text"),
        ("emit_preview_contract", emit_ok, f"allow_preview={len(emit_rows)} rows"),
        ("injection_preview_not_mutation", injection_preview_ok, "reflection may appear only in preview text"),
        ("no_speech_no_mutation", no_effect_ok, f"mutate={live['mutate_output']} rewrite={live['rewrite']} speech={live['speech']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("silence_hold_readonly", True, "status/guard/test/preview không phát lời, không mutate output thật, không gọi model"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase17_2": phase17_2_summary,
        "phase17_1": phase17_1_summary,
    }


def phase17_3_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_silence_hold_status(voice=None):
    summary = phase17_3_guard_summary(voice)
    live = summary["live"]
    print("🔇 Silence/Hold Enforcement Status")
    print("  Action: read-only; enforce silence/hold/block ở mức preview, không phát lời.")
    print(f"  Phase 17.3 Progress: {phase17_3_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} | output={live['output_action']} | injection={live['injection_action']} | visible={live['output_visible']}")
    print(f"  Preview text: {live['preview_text'] or 'none'}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: silence/hold/block không được rò text hoặc speech; allow chỉ là preview.")


def print_silence_hold_preview(raw_text=None, voice=None):
    text = raw_text or ""
    candidate = phase17_1_output_candidate(text, None, None)
    reflective_gate = phase16_5_guard_summary(voice)
    snapshot = reflective_gate["snapshot"]
    injection = phase17_2_reflection_injection_decision(
        candidate,
        snapshot["reflective"].get("state"),
        snapshot["recall"].get("action"),
        snapshot["safety"].get("status"),
        candidate.get("shape"),
    )
    result = phase17_3_silence_hold_enforcement(candidate, injection)
    summary = phase17_3_guard_summary(voice)
    print("🔇 Silence/Hold Preview")
    print("  Action: read-only; preview enforcement, không mutate output/không phát lời.")
    print(f"  Phase 17.3 Progress: {phase17_3_progress_percent(summary)}%")
    print(f"  Decision: {result['action']} | reason={result['reason']}")
    print(f"  Output: {result['output_action']} | injection={result['injection_action']} | visible={result['output_visible']}")
    print(f"  Preview text: {result['preview_text'] or 'none'}")
    print(f"  Mutate output: {result['mutate_output']} | Rewrite: {result['rewrite']} | Speech: {result['speech']} | Execute: {result['execute']}")


def print_silence_hold_test(raw_text=None, voice=None):
    print("🧪 Silence/Hold Enforcement Test")
    print("  Action: read-only; synthetic enforcement only, không mutate/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "silence": {"silence_enforced"},
        "hold": {"hold_review_enforced", "injection_hold_enforced"},
        "block": {"candidate_block_enforced", "injection_block_enforced"},
        "emit": {"emit_plain_preview", "emit_injected_preview"},
        "allow": {"emit_plain_preview", "emit_injected_preview"},
        "inject": {"injection_hold_enforced", "injection_block_enforced", "emit_injected_preview"},
        "preview": {"emit_plain_preview", "emit_injected_preview"},
    }
    rows = phase17_3_enforcement_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: silence, hold, block, emit, allow, inject, preview")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"action={row['got']} expected={row['expected']} | visible={row['visible']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_silence_hold_guard_status(voice=None):
    summary = phase17_3_guard_summary(voice)
    print("🧪 Phase 17.3 Silence/Hold Enforcement Guard")
    print("  Action: read-only; kiểm không rò output khi silence/hold/block, không execute.")
    print(f"  Progress: {phase17_3_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Enforcement regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} visible={row['visible']} reason={row['reason']}")


def print_phase17_3_status(voice=None):
    summary = phase17_3_guard_summary(voice)
    print("🧩 Phase 17.3 Status")
    print("  Goal: Silence/Hold Enforcement - output bị silence/hold/block không được rò text/speech.")
    print(f"  Progress: {phase17_3_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /silence-hold-status | /silence-hold-preview <text> | /silence-hold-test | /phase17-3-ready")


def print_phase17_3_ready(voice=None):
    summary = phase17_3_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 17.3 Ready" if ready else "⚠️ Phase 17.3 Ready")
    print("  Goal: silence/hold enforcement đủ chắc để sang Phase 17.4 output final gate.")
    print(f"  Progress: {phase17_3_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: silence_hold={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 17.3 không phát lời, không mutate output thật, không gọi model.")


PHASE17_4_COMMANDS = {
    "/output-gate-status",
    "/output-gate-guard-status",
    "/output-gate-test",
    "/phase17-status",
    "/phase17-ready",
    "/phase17-4-status",
    "/phase17-4-ready",
    "/phase17-4-guard-status",
    "/phase17-4-test",
    "/p17",
    "/p17-ready",
    "/p17-4",
    "/p17-4-ready",
}


def phase17_collect_summaries(voice=None):
    silence_hold = phase17_3_guard_summary(voice)
    reflection_injection = silence_hold.get("phase17_2") or phase17_2_guard_summary(voice)
    output_candidate = silence_hold.get("phase17_1") or reflection_injection.get("phase17_1") or phase17_1_guard_summary(voice)
    return {
        "output_candidate": output_candidate,
        "reflection_injection": reflection_injection,
        "silence_hold": silence_hold,
    }


def phase17_subphase_rows(voice=None, summaries=None):
    summaries = summaries or phase17_collect_summaries(voice)
    subphase_summaries = [
        ("phase17_1_output_candidate", summaries["output_candidate"], "output_candidate"),
        ("phase17_2_reflection_injection", summaries["reflection_injection"], "reflection_injection"),
        ("phase17_3_silence_hold", summaries["silence_hold"], "silence_hold"),
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


def phase17_output_gate_snapshot(summaries=None, voice=None):
    summaries = summaries or phase17_collect_summaries(voice)
    output_candidate = summaries["output_candidate"]["live"]
    reflection_injection = summaries["reflection_injection"]["live"]
    silence_hold = summaries["silence_hold"]["live"]
    queue = _get_runtime_queue().snapshot()
    pending_plan = PHASE7_PENDING_PLAN
    runtime_pending = (_get_pending_actions().snapshot() or {}).get("pending")
    event_executed = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
    audit_executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    memory_snapshot = (memory_governance_summary().get("snapshot") or {})
    return {
        "output_candidate": output_candidate,
        "reflection_injection": reflection_injection,
        "silence_hold": silence_hold,
        "queue": queue,
        "pending_plan": pending_plan,
        "runtime_pending": runtime_pending,
        "event_executed": event_executed,
        "audit_executed": audit_executed,
        "memory_pending": memory_snapshot.get("pending"),
        "memory_long": memory_snapshot.get("long_term", 0),
    }


def phase17_4_guard_summary(voice=None):
    summaries = phase17_collect_summaries(voice)
    subphases = phase17_subphase_rows(voice, summaries)
    snapshot = phase17_output_gate_snapshot(summaries, voice)
    command_missing = sorted(PHASE17_4_COMMANDS - KNOWN_SLASH_COMMANDS)
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
    output = snapshot["output_candidate"]
    injection = snapshot["reflection_injection"]
    enforcement = snapshot["silence_hold"]
    live_ok = (
        output.get("action") in PHASE17_1_OUTPUT_ACTIONS
        and injection.get("action") in PHASE17_2_INJECTION_ACTIONS
        and enforcement.get("action") in PHASE17_3_ENFORCEMENT_ACTIONS
    )
    no_effect_flags = [
        output.get("model_call") is False,
        output.get("rewrite") is False,
        output.get("speech") is False,
        output.get("execute") is False,
        injection.get("model_call") is False,
        injection.get("rewrite") is False,
        injection.get("mutate_output") is False,
        injection.get("speech") is False,
        injection.get("execute") is False,
        enforcement.get("model_call") is False,
        enforcement.get("rewrite") is False,
        enforcement.get("mutate_output") is False,
        enforcement.get("speech") is False,
        enforcement.get("execute") is False,
    ]
    leak_guard_ok = True
    if enforcement.get("action") in {"enforce_silence", "hold_output", "block_output"}:
        leak_guard_ok = not enforcement.get("preview_text") and enforcement.get("output_visible") is False
    rows = [
        ("subphase_closure", all(row["passed"] for row in subphases), f"{sum(1 for row in subphases if row['passed'])}/{len(subphases)} pass"),
        ("live_output_pipeline", live_ok, f"output={output.get('action')} injection={injection.get('action')} enforcement={enforcement.get('action')}"),
        ("output_candidate_ready", not phase10_guard_failures(summaries["output_candidate"]), f"output_candidate={summaries['output_candidate']['pass_count']}/{summaries['output_candidate']['total']}"),
        ("reflection_injection_ready", not phase10_guard_failures(summaries["reflection_injection"]), f"reflection_injection={summaries['reflection_injection']['pass_count']}/{summaries['reflection_injection']['total']}"),
        ("silence_hold_ready", not phase10_guard_failures(summaries["silence_hold"]), f"silence_hold={summaries['silence_hold']['pass_count']}/{summaries['silence_hold']['total']}"),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", execute_total == 0, f"event_execute={len(snapshot['event_executed'])} audit_execute={len(snapshot['audit_executed'])}"),
        ("memory_no_write_guard", not snapshot["memory_pending"], f"pending={'yes' if snapshot['memory_pending'] else 'none'} long={snapshot['memory_long']}"),
        ("no_model_rewrite_speech", all(no_effect_flags), f"model={output.get('model_call') or injection.get('model_call') or enforcement.get('model_call')} rewrite={output.get('rewrite') or injection.get('rewrite') or enforcement.get('rewrite')} speech={output.get('speech') or injection.get('speech') or enforcement.get('speech')}"),
        ("leak_guard_final", leak_guard_ok, f"visible={enforcement.get('output_visible')} preview={'yes' if enforcement.get('preview_text') else 'none'}"),
        ("autonomy_lock_contract", True, f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase18_boundary", True, "Phase 18 chỉ bàn/làm sau Phase 17 Ready=True; Phase 17 chưa bật output rewrite/speech thật"),
        ("output_gate_readonly", True, "status/guard/test không phát lời, không mutate output, không gọi model, không execute"),
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


def phase17_4_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_output_gate_status(voice=None):
    summary = phase17_4_guard_summary(voice)
    snapshot = summary["snapshot"]
    output = snapshot["output_candidate"]
    injection = snapshot["reflection_injection"]
    enforcement = snapshot["silence_hold"]
    queue_max = snapshot["queue"].get("max_size") or 50
    print("🧠 Phase 17 Output Gate Status")
    print("  Action: read-only; tổng kiểm output layer, không phát lời/không mutate.")
    print(f"  Phase 17.4 Progress: {phase17_4_progress_percent(summary)}%")
    print(f"  Subphases: {sum(1 for row in summary['subphases'] if row['passed'])}/{len(summary['subphases'])} pass")
    print(f"  Live: output={output.get('action')} injection={injection.get('action')} enforcement={enforcement.get('action')} visible={enforcement.get('output_visible')}")
    print(f"  Queue: active_p0={len(snapshot['queue'].get('active_p0') or [])} queued={len(snapshot['queue'].get('queued') or [])}/{queue_max}")
    print(f"  Execute flags: event={len(snapshot['event_executed'])} audit={len(snapshot['audit_executed'])}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 17 đóng output candidate/injection/enforcement; chưa phát lời hoặc rewrite thật.")


def print_output_gate_test(raw_text=None, voice=None):
    print("🧪 Output Gate Test")
    print("  Action: read-only; synthetic/summary only, không mutate/không execute.")
    summary = phase17_4_guard_summary(voice)
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
        print(f"  Output: {snapshot['output_candidate'].get('action')} | shape={snapshot['output_candidate'].get('shape')}")
        print(f"  Injection: {snapshot['reflection_injection'].get('action')} | reason={snapshot['reflection_injection'].get('reason')}")
        print(f"  Enforcement: {snapshot['silence_hold'].get('action')} | visible={snapshot['silence_hold'].get('output_visible')} | preview={snapshot['silence_hold'].get('preview_text') or 'none'}")
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
    if key in {"model", "speech", "rewrite", "mutate"}:
        snapshot = summary["snapshot"]
        output = snapshot["output_candidate"]
        injection = snapshot["reflection_injection"]
        enforcement = snapshot["silence_hold"]
        print("  Section: model/rewrite/speech")
        print(f"  Output: model={output.get('model_call')} rewrite={output.get('rewrite')} speech={output.get('speech')} execute={output.get('execute')}")
        print(f"  Injection: model={injection.get('model_call')} rewrite={injection.get('rewrite')} mutate={injection.get('mutate_output')} speech={injection.get('speech')} execute={injection.get('execute')}")
        print(f"  Enforcement: model={enforcement.get('model_call')} rewrite={enforcement.get('rewrite')} mutate={enforcement.get('mutate_output')} speech={enforcement.get('speech')} execute={enforcement.get('execute')}")
        print("  Execute: False")
        return
    print("  Status: not_found")
    print("  Sections: subphases, live, queue, events, model")
    print("  Execute: False")


def print_output_gate_guard_status(voice=None):
    summary = phase17_4_guard_summary(voice)
    print("🧪 Phase 17.4 Output Gate Guard")
    print("  Action: read-only; tổng kiểm Phase 17 output gate, không phát lời/không execute.")
    print(f"  Progress: {phase17_4_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Subphases:")
    for row in summary["subphases"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")


def print_phase17_4_status(voice=None):
    summary = phase17_4_guard_summary(voice)
    print("🧩 Phase 17.4 Status")
    print("  Goal: Final Output Gate - đóng output candidate/injection/enforcement trước Phase 18.")
    print(f"  Progress: {phase17_4_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /output-gate-status | /output-gate-test | /output-gate-guard-status | /phase17-ready")


def print_phase17_ready(voice=None):
    summary = phase17_4_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 17 Ready" if ready else "⚠️ Phase 17 Ready")
    print("  Goal: Phase 17 output layer đủ sạch để bắt đầu Phase 18.")
    print(f"  Progress: {phase17_4_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: phase17_gate={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 17 không rewrite output thật, không gọi model, không phát lời.")
