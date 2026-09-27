"""
Phase 9 — Audit Log & Safety Review Helpers.
Tách khỏi main.py (GĐ 3.2).
"""
from __future__ import annotations

import time

# ── Globals (from main.py lines 502-503) ─────────────────────────────────────────

PHASE9_AUDIT_LOG = []
PHASE9_AUDIT_NEXT_ID = 1


# ── Defer imports ────────────────────────────────────────────────────────────────

def _shorten_line(text, limit):
    from nana.utils.format import shorten_line as _fn
    return _fn(text, limit)


# ── Phase 9 functions (from main.py lines 7416-8926) ─────────────────────────

def phase9_audit_record(event, source="", intent="", actions=None, status="", blockers=None, detail="", execute=False):
    global PHASE9_AUDIT_NEXT_ID
    entry = {
        "id": PHASE9_AUDIT_NEXT_ID,
        "time": time.time(),
        "event": event,
        "source": source or "",
        "intent": intent or "",
        "actions": list(actions or []),
        "status": status or "",
        "blockers": list(blockers or []),
        "detail": detail or "",
        "execute": bool(execute),
    }
    PHASE9_AUDIT_NEXT_ID += 1
    PHASE9_AUDIT_LOG.append(entry)
    del PHASE9_AUDIT_LOG[:-30]
    try:
        from nana.runtime.event_store import runtime_event_record
        runtime_event_record(
            channel="audit",
            event=entry["event"],
            source=entry["source"],
            intent=entry["intent"],
            status=entry["status"],
            actions=entry["actions"],
            detail=entry["detail"],
            audit_id=entry["id"],
            execute=entry["execute"],
        )
    except Exception:
        pass
    return entry


def phase9_find_audit_entry(raw_id=None):
    if not PHASE9_AUDIT_LOG:
        return None
    if raw_id is None:
        return PHASE9_AUDIT_LOG[-1]
    try:
        target_id = int(str(raw_id).strip())
    except ValueError:
        return None
    for entry in reversed(PHASE9_AUDIT_LOG):
        if entry.get("id") == target_id:
            return entry
    return None


def phase9_audit_summary():
    if not PHASE9_AUDIT_LOG:
        return "empty_runtime_log_ok"
    last = PHASE9_AUDIT_LOG[-1]
    age = max(0.0, time.time() - last.get("time", 0))
    executed = sum(1 for entry in PHASE9_AUDIT_LOG if entry.get("execute"))
    return f"count={len(PHASE9_AUDIT_LOG)}/30 last={last.get('event')} id={last.get('id')} {age:.0f}s ago executed={executed}"


def phase9_audit_clear():
    cleared = len(PHASE9_AUDIT_LOG)
    PHASE9_AUDIT_LOG.clear()
    return cleared


def phase9_entry_risk(entry):
    blockers = entry.get("blockers") or []
    actions = set(entry.get("actions") or [])
    if entry.get("execute"):
        return "critical", "entry_marked_execute"
    if "purchase.checkout" in actions or "message.send" in actions:
        return "high", "high_impact_action_present"
    if any(str(blocker).startswith("autonomy_lock:") for blocker in blockers):
        return "medium", "autonomy_lock_blocked"
    if blockers:
        return "low-medium", "blocked_or_not_ready"
    return "low", "simulation_only"


def phase9_review_entry(entry):
    if not entry:
        return {
            "status": "not_found",
            "risk": "none",
            "reason": "entry_not_found",
            "issues": [],
            "recommendation": "Không có audit entry để review.",
        }
    risk, reason = phase9_entry_risk(entry)
    issues = []
    if entry.get("execute"):
        issues.append("execute_true")
    if not entry.get("event"):
        issues.append("missing_event")
    if entry.get("event") in {"action_trace", "pre_exec"} and not entry.get("source") and not entry.get("actions"):
        issues.append("missing_source_or_actions")
    recommendation = "pass"
    if issues:
        recommendation = "investigate_before_phase9_ready"
    elif risk in {"high", "critical"}:
        recommendation = "keep_blocked_and_trace_only"
    return {
        "status": "pass" if not issues else "warn",
        "risk": risk,
        "reason": reason,
        "issues": issues,
        "recommendation": recommendation,
    }


def print_phase9_audit_log():
    print("🧾 Phase 9 Audit Log")
    print("  Action: read-only; runtime-only, không ghi disk, không execute.")
    if not PHASE9_AUDIT_LOG:
        print("  Entries: none | empty_runtime_log_ok")
        print("  Next: chạy /action-trace <text> hoặc /pre-exec-check <action> để tạo audit entry runtime.")
        return
    for entry in PHASE9_AUDIT_LOG[-12:]:
        age = max(0.0, time.time() - entry.get("time", 0))
        print(
            "  "
            f"#{entry['id']} {entry['event']} | intent={entry.get('intent') or 'none'} | "
            f"status={entry.get('status') or 'none'} | actions={','.join(entry.get('actions') or []) or 'none'} | "
            f"execute={entry.get('execute')} | age={age:.1f}s"
        )
        if entry.get("blockers"):
            print(f"    Blockers: {', '.join(entry['blockers'])}")
        if entry.get("source"):
            print(f"    Source: {_shorten_line(entry['source'], 120)}")


def print_phase9_audit_clear():
    cleared = phase9_audit_clear()
    print("🧹 Phase 9 Audit Clear")
    print("  Action: runtime-only clear; không ghi disk, không execute.")
    print(f"  Cleared: {cleared}")
    print("  Status: empty_runtime_log_ok")
    print("  Execute: False")


def print_phase9_audit_replay(raw_id=None):
    entry = phase9_find_audit_entry(raw_id)
    print("🔁 Phase 9 Audit Replay")
    print("  Action: read-only; replay lại quyết định, không execute.")
    if not entry:
        print("  Status: not_found")
        print("  Next: chạy /action-trace <text> hoặc /pre-exec-check <action> để tạo audit entry runtime.")
        print("  Execute: False")
        return
    review = phase9_review_entry(entry)
    print(f"  ID: {entry['id']} | event={entry['event']} | execute={entry['execute']}")
    print(f"  Intent: {entry.get('intent') or 'none'}")
    print(f"  Actions: {', '.join(entry.get('actions') or []) if entry.get('actions') else 'none'}")
    print(f"  Status: {entry.get('status') or 'none'}")
    print(f"  Blockers: {', '.join(entry.get('blockers') or []) if entry.get('blockers') else 'none'}")
    print(f"  Risk: {review['risk']} | reason={review['reason']}")
    print(f"  Recommendation: {review['recommendation']}")
    print("  Execute: False")


def print_phase9_audit_review(raw_id=None):
    entry = phase9_find_audit_entry(raw_id)
    review = phase9_review_entry(entry)
    print("🛡️ Phase 9 Safety Review")
    print("  Action: read-only; review audit entry trước mọi mở rộng.")
    if not entry:
        print("  Status: not_found")
        print("  Next: chạy /action-trace <text> hoặc /pre-exec-check <action> để tạo audit entry runtime.")
        print("  Execute: False")
        return
    print(f"  ID: {entry['id']} | event={entry['event']}")
    print(f"  Status: {review['status']}")
    print(f"  Risk: {review['risk']} | reason={review['reason']}")
    print(f"  Issues: {', '.join(review['issues']) if review['issues'] else 'none'}")
    print(f"  Recommendation: {review['recommendation']}")
    print("  Execute: False")


def phase9_audit_guard_summary():
    examples = [
        {
            "event": "action_trace",
            "source": "Nana click nút đăng nhập",
            "intent": "browser.navigate_or_click",
            "actions": ["browser.click", "browser.type"],
            "status": "planned",
            "blockers": ["active_window_not_validated"],
            "execute": False,
            "expected_status": "pass",
            "expected_risk": "low-medium",
        },
        {
            "event": "pre_exec",
            "source": "browser.click",
            "intent": "action",
            "actions": ["browser.click"],
            "status": "blocked_by_lock",
            "blockers": ["autonomy_lock:no_autonomy_no_semi_autonomy:browser.click"],
            "execute": False,
            "expected_status": "pass",
            "expected_risk": "medium",
        },
        {
            "event": "action_trace",
            "source": "Nana thanh toán đơn này",
            "intent": "purchase.checkout",
            "actions": ["purchase.checkout"],
            "status": "blocked",
            "blockers": ["policy_blocked"],
            "execute": False,
            "expected_status": "pass",
            "expected_risk": "high",
        },
        {
            "event": "action_trace",
            "source": "50 x 10 bằng bao nhiêu",
            "intent": "chat.respond",
            "actions": [],
            "status": "planned",
            "blockers": [],
            "execute": False,
            "expected_status": "pass",
            "expected_risk": "low",
        },
        {
            "event": "pre_exec",
            "source": "browser.scroll",
            "intent": "action",
            "actions": ["browser.scroll"],
            "status": "phase8_simulation_only",
            "blockers": [],
            "execute": True,
            "expected_status": "warn",
            "expected_risk": "critical",
        },
    ]
    rows = []
    failures = []
    for example in examples:
        review = phase9_review_entry(example)
        expected_status = example.get("expected_status", "pass")
        passed = review["status"] == expected_status and review["risk"] == example["expected_risk"]
        row = {
            "event": example["event"],
            "passed": passed,
            "status": review["status"],
            "expected_status": expected_status,
            "risk": review["risk"],
            "expected": example["expected_risk"],
            "reason": review["reason"],
        }
        rows.append(row)
        if not passed:
            failures.append(row)
    return {
        "total": len(rows),
        "pass_count": len(rows) - len(failures),
        "failures": failures,
        "rows": rows,
    }


def print_phase9_guard_status():
    summary = phase9_audit_guard_summary()
    print("🧪 Phase 9 Audit Guard")
    print("  Action: read-only; kiểm tra replay/safety review.")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for row in summary["rows"]:
        status = "pass" if row["passed"] else "fail"
        print(
            "  "
            f"{row['event']} | {status} | review={row['status']} expected_review={row['expected_status']} | "
            f"risk={row['risk']} | expected={row['expected']} | reason={row['reason']}"
        )


def build_phase9_status_model():
    try:
        phase8_model_fn = None
        for _m in []:
            pass
        from nana.phases.phase8 import build_phase8_status_model
        phase8_model = build_phase8_status_model()
        phase8_blocking = [name for name, status, _detail in phase8_model["checks"] if status == "warn"]
    except Exception:
        phase8_blocking = []
    guard = phase9_audit_guard_summary()
    executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    from nana.phases.commons import AUTONOMY_LOCK_PHASE, AUTONOMY_LOCK_RULE
    checks = [
        ("phase8_foundation", "pass" if not phase8_blocking else "warn", f"blocking={len(phase8_blocking)}"),
        ("audit_log_runtime", "pass", phase9_audit_summary()),
        ("audit_guard_matrix", "pass" if not guard["failures"] else "warn", f"{guard['pass_count']}/{guard['total']} pass"),
        ("execute_flag_guard", "pass" if not executed else "warn", f"executed={len(executed)}"),
        ("autonomy_lock", "pass" if AUTONOMY_LOCK_RULE == "no_autonomy_no_semi_autonomy" else "warn", f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
    ]
    return {"checks": checks, "guard": guard}


def print_phase9_status():
    model = build_phase9_status_model()
    print("🧩 Phase 9 Status")
    print("  Goal: audit log + replay + safety review cho plan/action trace; vẫn runtime-only.")
    for name, status, detail in model["checks"]:
        print(f"  {name}: {status} | {detail}")
    print("  Commands: /audit-log | /audit-replay [id] | /audit-review [id] | /audit-clear | /audit-guard-status | /phase9-ready")


def print_phase9_ready():
    model = build_phase9_status_model()
    blocking = []
    observes = []
    for name, status, detail in model["checks"]:
        if status == "warn":
            blocking.append((name, detail))
        elif status == "observe":
            observes.append((name, detail))
    ready = not blocking
    guard = model["guard"]
    print("✅ Phase 9 Ready" if ready else "⚠️ Phase 9 Ready")
    print("  Goal: audit/replay/review đủ rõ để handoff Phase 10; vẫn không bán tự trị/chưa tự trị.")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Observe: {len(observes)}")
    for name, detail in observes:
        print(f"    {name}: {detail}")
    print(f"  Regression: audit_guard={guard['pass_count']}/{guard['total']}")
    print("  Autonomy: locked Phase 5-10; logs are runtime-only.")
