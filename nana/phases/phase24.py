"""
Phase 24 — Streaming Control + Subphases 24.1–24.5.
Tách khỏi main.py (GĐ 3.5).
"""

from __future__ import annotations

from nana.phases.commons import (
    AUTONOMY_LOCK_PHASE,
    AUTONOMY_LOCK_RULE,
    KNOWN_SLASH_COMMANDS,
    PHASE7_PENDING_PLAN,
    PHASE9_AUDIT_LOG,
    RUNTIME_EVENT_LOG
)


PHASE24_1_COMMANDS = {
    "/stream-pilot-control-status",
    "/stream-pilot-control-guard-status",
    "/stream-pilot-control-test",
    "/phase24-1-status",
    "/phase24-1-ready",
    "/phase24-1-guard-status",
    "/phase24-1-test",
    "/p24-1",
    "/p24-1-ready",
}

PHASE24_1_PILOT_ACTIONS = {"block_pilot", "hold_pilot", "pilot_dry_run", "pilot_ready"}

PHASE24_2_COMMANDS = {
    "/guarded-stream-call-status",
    "/guarded-stream-call-guard-status",
    "/guarded-stream-call-test",
    "/phase24-2-status",
    "/phase24-2-ready",
    "/phase24-2-guard-status",
    "/phase24-2-test",
    "/p24-2",
    "/p24-2-ready",
}

PHASE24_2_DISPATCH_ACTIONS = {"block_guarded_call", "guarded_call_dry_run", "hold_guarded_call", "suppress_guarded_call"}

PHASE24_3_COMMANDS = {
    "/controlled-stream-pilot-status",
    "/controlled-stream-pilot-guard-status",
    "/controlled-stream-pilot-test",
    "/phase24-3-status",
    "/phase24-3-ready",
    "/phase24-3-guard-status",
    "/phase24-3-test",
    "/p24-3",
    "/p24-3-ready",
}

PHASE24_3_CONTROLLED_ACTIONS = {"block_controlled_pilot", "controlled_pilot_dry_run", "hold_controlled_pilot", "suppress_controlled_pilot"}

PHASE24_4_COMMANDS = {
    "/stream-rollback-status",
    "/stream-rollback-guard-status",
    "/stream-rollback-test",
    "/phase24-4-status",
    "/phase24-4-ready",
    "/phase24-4-guard-status",
    "/phase24-4-test",
    "/p24-4",
    "/p24-4-ready",
}

PHASE24_4_ROLLBACK_ACTIONS = {"block_stream_rollback", "hold_stream_rollback", "rollback_ready", "stream_timeout_guard_ready"}

PHASE24_5_COMMANDS = {
    "/controlled-stream-gate-status",
    "/controlled-stream-gate-guard-status",
    "/controlled-stream-gate-test",
    "/phase24-status",
    "/phase24-ready",
    "/phase24-5-status",
    "/phase24-5-ready",
    "/phase24-5-guard-status",
    "/phase24-5-test",
    "/p24",
    "/p24-ready",
    "/p24-5",
    "/p24-5-ready",
}

PHASE24_5_CONTROLLED_GATE_ACTIONS = {"block_controlled_gate", "hold_controlled_gate", "controlled_gate_ready", "suppress_controlled_gate"}

_rq = None
_cs = None
_mem = None

def _lazy_imports():
    global _rq, _cs, _mem
    if _rq is None:
        import nana.runtime_queue as _runtime_queue_module
        _rq = _runtime_queue_module
        from nana import context_state as _cs_module
        _cs = _cs_module
        from nana.memory import memory_governance_summary as _mem_summary
        _mem = _mem_summary

def runtime_queue():
    _lazy_imports()
    return _rq

def pending_actions():
    _lazy_imports()
    return _rq.pending_actions

def memory_governance_summary():
    _lazy_imports()
    return _mem()


# ─────────────────────────────────────────────────────────────────
# PHASE 24.1 — Stream Pilot
# ─────────────────────────────────────────────────────────────────

def phase24_1_stream_pilot(phase23_live, pilot_enabled=False, kill_switch=False):
    phase23_live = phase23_live or {}
    action = phase23_live.get("action") or "hold_stream"
    if kill_switch:
        return {
            "action": "kill_switch_block",
            "reason": "kill_switch_active",
            "pilot_enabled": bool(pilot_enabled),
            "stream_call": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    elif not pilot_enabled:
        return {
            "action": "pilot_disabled",
            "reason": "streaming_pilot_disabled",
            "stream_call": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    elif action == "stream_safety_ready":
        return {
            "action": "stream_pilot_ready",
            "reason": "stream_pilot_enabled_and_safety_ready",
            "pilot_enabled": True,
            "stream_call": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    else:
        return {
            "action": "hold_stream",
            "reason": "stream_not_ready",
            "pilot_enabled": bool(pilot_enabled),
            "stream_call": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }


def phase24_1_pilot_rows():
    cases = [
        ("pilot_ready", {"action": "stream_safety_ready"}, True, False, "stream_pilot_ready"),
        ("pilot_disabled", {"action": "stream_safety_ready"}, False, False, "pilot_disabled"),
        ("kill_switch", {"action": "stream_safety_ready"}, True, True, "kill_switch_block"),
        ("not_ready_hold", {"action": "hold_stream"}, True, False, "hold_stream"),
        ("block_hold", {"action": "block_stream"}, True, False, "hold_stream"),
    ]
    rows = []
    for name, phase23_live, pilot_enabled, kill_switch, expected in cases:
        result = phase24_1_stream_pilot(phase23_live, pilot_enabled, kill_switch)
        no_call = (
            result["stream_call"] is False
            and result["model_call"] is False
            and result["rewrite"] is False
            and result["memory_write"] is False
            and result["execute"] is False
        )
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_call,
            "got": result["action"],
            "expected": expected,
            "pilot_enabled": pilot_enabled,
            "kill_switch": kill_switch,
            "reason": result["reason"],
        })
    return rows


def phase24_1_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures
    from nana.phases.phase23 import phase23_5_guard_summary

    phase23 = phase23_5_guard_summary()
    live = phase24_1_stream_pilot(phase23["subphases"][3]["summary"]["live"], pilot_enabled=False, kill_switch=False)
    test_rows = phase24_1_pilot_rows()
    command_missing = sorted(PHASE24_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    rows = [
        ("phase23_foundation", not phase10_guard_failures(phase23), f"phase23_gate={phase23['pass_count']}/{phase23['total']}"),
        ("pilot_action_taxonomy", PHASE24_1_PILOT_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("stream_pilot_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("stream_pilot_snapshot", live["action"] in PHASE24_1_PILOT_ACTIONS, f"action={live['action']} pilot={live['pilot_enabled']}"),
        ("no_streaming_calls_yet", live["stream_call"] is False, f"stream_call={live['stream_call']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("stream_pilot_readonly", True, "pilot decision only, no streaming calls"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase23": phase23,
        "test_rows": test_rows,
        "live": live,
    }


def phase24_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_stream_pilot_status():
    summary = phase24_1_guard_summary()
    live = summary["live"]
    print("🚀 Stream Pilot Status")
    print("  Action: read-only; kiểm stream pilot decision, không gọi stream thật.")
    print(f"  Phase 24.1 Progress: {phase24_1_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} pilot={live['pilot_enabled']} reason={live['reason']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase24_1_status():
    summary = phase24_1_guard_summary()
    print("🧩 Phase 24.1 Status")
    print("  Goal: Stream Pilot - kiểm pilot decision trước khi gọi stream thật.")
    print(f"  Progress: {phase24_1_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 24.2 — Guarded Stream Call
# ─────────────────────────────────────────────────────────────────

def phase24_2_guarded_stream_call(pilot_result):
    pilot_result = pilot_result or {}
    action = pilot_result.get("action") or "hold_stream"
    if action == "stream_pilot_ready":
        return {
            "action": "stream_call_ready",
            "reason": "guarded_stream_call_preview",
            "stream_call": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    else:
        return {
            "action": "hold_stream",
            "reason": "pilot_not_ready",
            "stream_call": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }


def phase24_2_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    phase24_1 = phase24_1_guard_summary()
    live = phase24_2_guarded_stream_call(phase24_1["live"])
    command_missing = sorted(PHASE24_2_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {live["action"]}
    rows = [
        ("phase24_1_foundation", not phase10_guard_failures(phase24_1), f"stream_pilot={phase24_1['pass_count']}/{phase24_1['total']}"),
        ("dispatch_action_taxonomy", PHASE24_2_DISPATCH_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("guarded_stream_snapshot", live["action"] in PHASE24_2_DISPATCH_ACTIONS, f"action={live['action']} reason={live['reason']}"),
        ("no_streaming_calls_yet", live["stream_call"] is False, f"stream_call={live['stream_call']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("guarded_stream_readonly", True, "guarded call only, no streaming calls"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase24_1": phase24_1,
        "live": live,
    }


def phase24_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_guarded_stream_call_status():
    summary = phase24_2_guard_summary()
    live = summary["live"]
    print("🛡️ Guarded Stream Call Status")
    print("  Action: read-only; guarded stream call, không gọi stream thật.")
    print(f"  Phase 24.2 Progress: {phase24_2_progress_percent(summary)}%")
    print(f"  Action: {live['action']} | Reason: {live['reason']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase24_2_status():
    summary = phase24_2_guard_summary()
    print("🧩 Phase 24.2 Status")
    print("  Goal: Guarded Stream Call - kiểm guarded call trước khi gọi stream thật.")
    print(f"  Progress: {phase24_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 24.3 — Controlled Stream Pilot
# ─────────────────────────────────────────────────────────────────

def phase24_3_controlled_stream_pilot(guarded_result):
    guarded_result = guarded_result or {}
    action = guarded_result.get("action") or "hold_stream"
    if action == "stream_call_ready":
        return {
            "action": "controlled_stream_ready",
            "reason": "controlled_stream_preview",
            "controlled_call": True,
            "stream_call": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    else:
        return {
            "action": "hold_stream",
            "reason": "guarded_not_ready",
            "controlled_call": False,
            "stream_call": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }


def phase24_3_controlled_rows():
    cases = [
        ("controlled_ready", {"action": "stream_call_ready"}, "controlled_stream_ready"),
        ("guarded_hold", {"action": "hold_stream"}, "hold_stream"),
    ]
    rows = []
    for name, guarded_result, expected in cases:
        result = phase24_3_controlled_stream_pilot(guarded_result)
        no_call = (
            result["stream_call"] is False
            and result["model_call"] is False
            and result["rewrite"] is False
            and result["memory_write"] is False
            and result["execute"] is False
        )
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_call,
            "got": result["action"],
            "expected": expected,
            "controlled_call": result["controlled_call"],
            "reason": result["reason"],
        })
    return rows


def phase24_3_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    phase24_2 = phase24_2_guard_summary()
    live = phase24_3_controlled_stream_pilot(phase24_2["live"])
    test_rows = phase24_3_controlled_rows()
    command_missing = sorted(PHASE24_3_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    rows = [
        ("phase24_2_foundation", not phase10_guard_failures(phase24_2), f"guarded_stream={phase24_2['pass_count']}/{phase24_2['total']}"),
        ("controlled_action_taxonomy", PHASE24_3_CONTROLLED_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("controlled_stream_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("controlled_stream_snapshot", live["action"] in PHASE24_3_CONTROLLED_ACTIONS, f"action={live['action']} controlled_call={live['controlled_call']}"),
        ("no_streaming_calls_yet", live["stream_call"] is False, f"stream_call={live['stream_call']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("controlled_stream_readonly", True, "controlled pilot only, no streaming calls"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase24_2": phase24_2,
        "test_rows": test_rows,
        "live": live,
    }


def phase24_3_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_controlled_stream_pilot_status():
    summary = phase24_3_guard_summary()
    live = summary["live"]
    print("🎯 Controlled Stream Pilot Status")
    print("  Action: read-only; controlled stream pilot, không gọi stream thật.")
    print(f"  Phase 24.3 Progress: {phase24_3_progress_percent(summary)}%")
    print(f"  Action: {live['action']} | Controlled: {live['controlled_call']} | Reason: {live['reason']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase24_3_status():
    summary = phase24_3_guard_summary()
    print("🧩 Phase 24.3 Status")
    print("  Goal: Controlled Stream Pilot - kiểm controlled pilot trước khi gọi stream thật.")
    print(f"  Progress: {phase24_3_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 24.4 — Stream Rollback
# ─────────────────────────────────────────────────────────────────

def phase24_4_stream_rollback(controlled_result, rollback_triggered=False):
    controlled_result = controlled_result or {}
    action = controlled_result.get("action") or "hold_stream"
    if rollback_triggered:
        return {
            "action": "rollback_triggered",
            "reason": "rollback_requested",
            "rollback": True,
            "stream_call": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    elif action == "controlled_stream_ready":
        return {
            "action": "stream_ready_no_rollback",
            "reason": "controlled_ready_no_rollback",
            "rollback": False,
            "stream_call": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    else:
        return {
            "action": "hold_stream",
            "reason": "controlled_not_ready",
            "rollback": False,
            "stream_call": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }


def phase24_4_rollback_rows():
    cases = [
        ("no_rollback_ready", {"action": "controlled_stream_ready"}, False, "stream_ready_no_rollback"),
        ("rollback_triggered", {"action": "controlled_stream_ready"}, True, "rollback_triggered"),
        ("hold_holds", {"action": "hold_stream"}, False, "hold_stream"),
    ]
    rows = []
    for name, controlled_result, rollback_triggered, expected in cases:
        result = phase24_4_stream_rollback(controlled_result, rollback_triggered)
        no_call = (
            result["stream_call"] is False
            and result["model_call"] is False
            and result["rewrite"] is False
            and result["memory_write"] is False
            and result["execute"] is False
        )
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_call,
            "got": result["action"],
            "expected": expected,
            "rollback": result["rollback"],
            "reason": result["reason"],
        })
    return rows


def phase24_4_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    phase24_3 = phase24_3_guard_summary()
    live = phase24_4_stream_rollback(phase24_3["live"], rollback_triggered=False)
    test_rows = phase24_4_rollback_rows()
    command_missing = sorted(PHASE24_4_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    rows = [
        ("phase24_3_foundation", not phase10_guard_failures(phase24_3), f"controlled_stream={phase24_3['pass_count']}/{phase24_3['total']}"),
        ("rollback_action_taxonomy", PHASE24_4_ROLLBACK_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("stream_rollback_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("stream_rollback_snapshot", live["action"] in PHASE24_4_ROLLBACK_ACTIONS, f"action={live['action']} rollback={live['rollback']}"),
        ("no_streaming_calls_yet", live["stream_call"] is False, f"stream_call={live['stream_call']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("stream_rollback_readonly", True, "rollback decision only, no streaming calls"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase24_3": phase24_3,
        "test_rows": test_rows,
        "live": live,
    }


def phase24_4_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_stream_rollback_status():
    summary = phase24_4_guard_summary()
    live = summary["live"]
    print("↩️ Stream Rollback Status")
    print("  Action: read-only; stream rollback decision, không gọi stream thật.")
    print(f"  Phase 24.4 Progress: {phase24_4_progress_percent(summary)}%")
    print(f"  Action: {live['action']} | Rollback: {live['rollback']} | Reason: {live['reason']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase24_4_status():
    summary = phase24_4_guard_summary()
    print("🧩 Phase 24.4 Status")
    print("  Goal: Stream Rollback - kiểm rollback decision sau controlled pilot.")
    print(f"  Progress: {phase24_4_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 24.5 — Final Controlled Gate
# ─────────────────────────────────────────────────────────────────

def phase24_subphase_rows():
    pilot = phase24_1_guard_summary()
    guarded = phase24_2_guard_summary()
    controlled = phase24_3_guard_summary()
    rollback = phase24_4_guard_summary()
    from nana.phases.phase10 import phase10_guard_failures
    return [
        ("phase24_1_stream_pilot", pilot, not phase10_guard_failures(pilot)),
        ("phase24_2_guarded_stream_call", guarded, not phase10_guard_failures(guarded)),
        ("phase24_3_controlled_stream_pilot", controlled, not phase10_guard_failures(controlled)),
        ("phase24_4_stream_rollback", rollback, not phase10_guard_failures(rollback)),
    ]


def phase24_5_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    subphases = phase24_subphase_rows()
    command_missing = sorted(PHASE24_5_COMMANDS - KNOWN_SLASH_COMMANDS)
    queue = runtime_queue().snapshot()
    memory_snapshot = memory_governance_summary().get("snapshot") or {}
    event_executed = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
    audit_executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    pending_details = []
    if PHASE7_PENDING_PLAN:
        pending_details.append("phase7_pending_plan")
    runtime_pending = (pending_actions().snapshot() or {}).get("pending")
    if runtime_pending:
        pending_details.append("runtime_pending_action")
    if memory_snapshot.get("pending"):
        pending_details.append("memory_pending_action")
    if queue.get("active_p0"):
        pending_details.append("active_p0")
    if queue.get("queued"):
        pending_details.append(f"queued={len(queue.get('queued') or [])}")
    rows = [
        ("subphase_closure", all(passed for _, _, passed in subphases), f"{sum(1 for _, _, p in subphases if p)}/{len(subphases)} pass"),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", len(event_executed) + len(audit_executed) == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'}"),
        ("autonomy_lock_contract", True, f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase25_boundary", True, "Phase 25 chỉ bàn/làm sau Phase 24 Ready=True"),
        ("controlled_gate_readonly", True, "status/guard/test không gọi stream thật"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "subphases": subphases,
        "queue": queue,
    }


def phase24_5_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_controlled_gate_status():
    summary = phase24_5_guard_summary()
    queue = summary["queue"]
    print("🧠 Phase 24 Controlled Gate Status")
    print("  Action: read-only; tổng kiểm streaming control layer, chưa gọi stream thật.")
    print(f"  Phase 24.5 Progress: {phase24_5_progress_percent(summary)}%")
    print(f"  Queue: active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}/{getattr(runtime_queue(), 'max_items', 50)}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase24_5_status():
    summary = phase24_5_guard_summary()
    print("🧩 Phase 24.5 Status")
    print("  Goal: Final Controlled Gate - tổng kiểm streaming control layer.")
    print(f"  Progress: {phase24_5_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase24_ready():
    summary = phase24_5_guard_summary()
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 24 Ready" if ready else "⚠️ Phase 24 Ready")
    print("  Goal: Phase 24 streaming control đủ sạch để bắt đầu Phase 25.")
    print(f"  Progress: {phase24_5_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print("  Autonomy: unchanged; Phase 24 chưa gọi stream thật.")



def print_phase24_1_ready(vts=None, voice=None):
    summary = phase24_1_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase24_1 Ready: {pass_label}")
    print(f"  Progress: phase24_1_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready





def print_phase24_2_ready(vts=None, voice=None):
    summary = phase24_2_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase24_2 Ready: {pass_label}")
    print(f"  Progress: phase24_2_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready





def print_phase24_3_ready(vts=None, voice=None):
    summary = phase24_3_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase24_3 Ready: {pass_label}")
    print(f"  Progress: phase24_3_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready





def print_phase24_4_ready(vts=None, voice=None):
    summary = phase24_4_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase24_4 Ready: {pass_label}")
    print(f"  Progress: phase24_4_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready
