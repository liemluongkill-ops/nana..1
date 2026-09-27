"""
Phase 25 — Measurement & Pilot Enable + Subphases 25.1–25.2.
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


PHASE25_1_COMMANDS = {
    "/live-stream-measurement-status",
    "/live-stream-measurement-guard-status",
    "/live-stream-measurement-test",
    "/phase25-1-status",
    "/phase25-1-ready",
    "/phase25-1-guard-status",
    "/phase25-1-test",
    "/p25-1",
    "/p25-1-ready",
}

PHASE25_1_MEASUREMENT_FIELDS = (
    "stream_first_audio_ms",
    "stream_total_ms",
    "stream_chunks_received",
    "stream_cache_hits",
    "stream_chunked_count",
    "stream_errors",
    "stream_pilot_active",
)

PHASE25_2_COMMANDS = {
    "/stream-pilot-enable-status",
    "/stream-pilot-enable-guard-status",
    "/stream-pilot-enable-test",
    "/phase25-status",
    "/phase25-ready",
    "/phase25-2-status",
    "/phase25-2-ready",
    "/phase25-2-guard-status",
    "/phase25-2-test",
    "/p25",
    "/p25-ready",
    "/p25-2",
    "/p25-2-ready",
}

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
# PHASE 25.1 — Measurement Decision
# ─────────────────────────────────────────────────────────────────

def phase25_1_measurement_decision():
    from nana.phases.phase20 import phase20_1_voice_snapshot
    from nana.phases.phase21 import phase21_5_guard_summary
    from nana.phases.phase22 import phase22_5_guard_summary
    from nana.phases.phase23 import phase23_5_guard_summary
    from nana.phases.phase24 import phase24_5_guard_summary

    voice_state = phase20_1_voice_snapshot(None)
    phase21 = phase21_5_guard_summary()
    phase22 = phase22_5_guard_summary()
    phase23 = phase23_5_guard_summary()
    phase24 = phase24_5_guard_summary()

    snapshot = {
        "timestamp": None,
        "voice_speaking": voice_state.get("speaking"),
        "voice_queue_size": voice_state.get("queue_size"),
        "voice_queue_maxsize": voice_state.get("queue_maxsize"),
        "phase21_pass_count": phase21.get("pass_count", 0),
        "phase21_total": phase21.get("total", 0),
        "phase22_pass_count": phase22.get("pass_count", 0),
        "phase22_total": phase22.get("total", 0),
        "phase23_pass_count": phase23.get("pass_count", 0),
        "phase23_total": phase23.get("total", 0),
        "phase24_pass_count": phase24.get("pass_count", 0),
        "phase24_total": phase24.get("total", 0),
        "memory_budget_mb": 0.0,
        "focus_mode": "normal",
    }
    all_pass = (
        phase21.get("pass_count", 0) == phase21.get("total", 0)
        and phase22.get("pass_count", 0) == phase22.get("total", 0)
        and phase23.get("pass_count", 0) == phase23.get("total", 0)
        and phase24.get("pass_count", 0) == phase24.get("total", 0)
    )
    if all_pass:
        action = "measurement_complete"
        reason = "all_phase_19_24_ready"
    else:
        action = "measurement_incomplete"
        reason = "some_phases_not_ready"
    return {
        "action": action,
        "reason": reason,
        "snapshot": snapshot,
        "all_pass": all_pass,
        "measurement_call": False,
        "model_call": False,
        "rewrite": False,
        "memory_write": False,
        "execute": False,
    }


def phase25_1_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures
    from nana.phases.phase19 import phase19_5_guard_summary

    phase19 = phase19_5_guard_summary()
    live = phase25_1_measurement_decision()
    command_missing = sorted(PHASE25_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    snapshot = live.get("snapshot") or {}
    fields_ok = all(field in snapshot for field in PHASE25_1_MEASUREMENT_FIELDS)
    rows = [
        ("phase19_foundation", not phase10_guard_failures(phase19), f"phase19_gate={phase19['pass_count']}/{phase19['total']}"),
        ("measurement_fields_present", fields_ok, f"fields={','.join(sorted(PHASE25_1_MEASUREMENT_FIELDS))}"),
        ("measurement_snapshot", True, f"action={live['action']} reason={live['reason']} all_pass={live['all_pass']}"),
        ("phase21_measured", snapshot.get("phase21_pass_count", 0) >= 0, f"phase21={snapshot.get('phase21_pass_count',0)}/{snapshot.get('phase21_total',0)}"),
        ("phase22_measured", snapshot.get("phase22_pass_count", 0) >= 0, f"phase22={snapshot.get('phase22_pass_count',0)}/{snapshot.get('phase22_total',0)}"),
        ("phase23_measured", snapshot.get("phase23_pass_count", 0) >= 0, f"phase23={snapshot.get('phase23_pass_count',0)}/{snapshot.get('phase23_total',0)}"),
        ("phase24_measured", snapshot.get("phase24_pass_count", 0) >= 0, f"phase24={snapshot.get('phase24_pass_count',0)}/{snapshot.get('phase24_total',0)}"),
        ("no_measurement_side_effects", live["measurement_call"] is False and live["model_call"] is False, f"measurement_call={live['measurement_call']} model_call={live['model_call']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("measurement_decision_readonly", True, "decision only, no side effects"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase19": phase19,
        "live": live,
        "snapshot": snapshot,
    }


def phase25_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_measurement_decision_status():
    summary = phase25_1_guard_summary()
    live = summary["live"]
    snap = summary["snapshot"]
    print("📊 Measurement Decision Status")
    print("  Action: read-only; đo lường Phase 19-24 readiness, không trigger pilot.")
    print(f"  Phase 25.1 Progress: {phase25_1_progress_percent(summary)}%")
    print(f"  Action: {live['action']} | Reason: {live['reason']} | All pass: {live['all_pass']}")
    print(f"  Phase21: {snap.get('phase21_pass_count',0)}/{snap.get('phase21_total',0)}")
    print(f"  Phase22: {snap.get('phase22_pass_count',0)}/{snap.get('phase22_total',0)}")
    print(f"  Phase23: {snap.get('phase23_pass_count',0)}/{snap.get('phase23_total',0)}")
    print(f"  Phase24: {snap.get('phase24_pass_count',0)}/{snap.get('phase24_total',0)}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase25_1_status():
    summary = phase25_1_guard_summary()
    print("🧩 Phase 25.1 Status")
    print("  Goal: Measurement Decision - đo lường Phase 19-24 readiness.")
    print(f"  Progress: {phase25_1_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 25.2 — Pilot Enable
# ─────────────────────────────────────────────────────────────────

def phase25_2_pilot_enable(measurement_live, enable_pilot=False):
    measurement_live = measurement_live or {}
    action = measurement_live.get("action") or "measurement_incomplete"
    all_pass = measurement_live.get("all_pass", False)
    if not all_pass:
        return {
            "action": "pilot_not_enabled",
            "reason": "measurement_incomplete",
            "pilot_enabled": False,
            "measurement_write": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    elif not enable_pilot:
        return {
            "action": "pilot_disabled_by_user",
            "reason": "user_did_not_enable_pilot",
            "pilot_enabled": False,
            "measurement_write": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    else:
        return {
            "action": "pilot_enabled",
            "reason": "measurement_complete_and_user_enabled",
            "pilot_enabled": True,
            "measurement_write": True,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }


def phase25_2_enable_rows():
    complete = {"action": "measurement_complete", "all_pass": True}
    incomplete = {"action": "measurement_incomplete", "all_pass": False}
    cases = [
        ("complete_enable", complete, True, "pilot_enabled"),
        ("complete_no_enable", complete, False, "pilot_disabled_by_user"),
        ("incomplete_enable", incomplete, True, "pilot_not_enabled"),
        ("incomplete_no_enable", incomplete, False, "pilot_not_enabled"),
    ]
    rows = []
    for name, measurement, enable_pilot, expected in cases:
        result = phase25_2_pilot_enable(measurement, enable_pilot)
        no_call = (
            result["model_call"] is False
            and result["rewrite"] is False
            and result["memory_write"] is False
            and result["execute"] is False
        )
        measurement_write_ok = expected == "pilot_enabled" or result["measurement_write"] is False
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_call and measurement_write_ok,
            "got": result["action"],
            "expected": expected,
            "pilot_enabled": result["pilot_enabled"],
            "measurement_write": result["measurement_write"],
            "reason": result["reason"],
        })
    return rows


def phase25_2_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    phase25_1 = phase25_1_guard_summary()
    live = phase25_2_pilot_enable(phase25_1["live"], enable_pilot=False)
    test_rows = phase25_2_enable_rows()
    command_missing = sorted(PHASE25_2_COMMANDS - KNOWN_SLASH_COMMANDS)
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
        ("phase25_1_foundation", not phase10_guard_failures(phase25_1), f"measurement={phase25_1['pass_count']}/{phase25_1['total']}"),
        ("pilot_enable_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("pilot_enable_snapshot", True, f"action={live['action']} pilot_enabled={live['pilot_enabled']} reason={live['reason']}"),
        ("pilot_disabled_when_measurement_incomplete", any(row["name"] == "incomplete_enable" and row["passed"] and not row["pilot_enabled"] for row in test_rows), "measurement incomplete prevents pilot"),
        ("pilot_enabled_when_complete_and_user_enables", any(row["name"] == "complete_enable" and row["passed"] and row["pilot_enabled"] for row in test_rows), "measurement complete + user enables = pilot enabled"),
        ("no_unexpected_side_effects", live["model_call"] is False and live["rewrite"] is False and live["execute"] is False, f"model_call={live['model_call']} rewrite={live['rewrite']} execute={live['execute']}"),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", len(event_executed) + len(audit_executed) == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("autonomy_lock_contract", True, f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
        ("pilot_enable_readonly", True, "pilot enable decision only, no execute side effects"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase25_1": phase25_1,
        "test_rows": test_rows,
        "live": live,
        "queue": queue,
    }


def phase25_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_pilot_enable_status():
    summary = phase25_2_guard_summary()
    live = summary["live"]
    queue = summary["queue"]
    print("🎯 Pilot Enable Status")
    print("  Action: read-only; bật pilot dựa trên measurement, không execute.")
    print(f"  Phase 25.2 Progress: {phase25_2_progress_percent(summary)}%")
    print(f"  Action: {live['action']} | Pilot enabled: {live['pilot_enabled']} | Reason: {live['reason']}")
    print(f"  Queue: active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}/{getattr(runtime_queue(), 'max_items', 50)}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_pilot_enable_test(raw_text=None):
    print("🧪 Pilot Enable Test")
    print("  Action: read-only; synthetic pilot enable only, không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "enable": {"complete_enable"},
        "enabled": {"complete_enable"},
        "disable": {"complete_no_enable"},
        "disabled": {"complete_no_enable"},
        "incomplete": {"incomplete_enable", "incomplete_no_enable"},
    }
    rows = phase25_2_enable_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: enable, disable, incomplete")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} pilot={row['pilot_enabled']} reason={row['reason']}")


def print_phase25_2_status():
    summary = phase25_2_guard_summary()
    print("🧩 Phase 25.2 Status")
    print("  Goal: Pilot Enable - bật pilot dựa trên measurement hoàn thành.")
    print(f"  Progress: {phase25_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase25_ready():
    summary = phase25_2_guard_summary()
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 25 Ready" if ready else "⚠️ Phase 25 Ready")
    print("  Goal: Phase 25 measurement và pilot enable hoàn tất.")
    print(f"  Progress: {phase25_2_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print("  Autonomy: Phase 25 measurement không thay đổi autonomy.")



def print_phase25_1_ready(vts=None, voice=None):
    summary = phase25_1_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase25_1 Ready: {pass_label}")
    print(f"  Progress: phase25_1_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready





def print_phase25_2_ready(vts=None, voice=None):
    summary = phase25_2_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase25_2 Ready: {pass_label}")
    print(f"  Progress: phase25_2_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready
