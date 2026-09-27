"""
Phase 8 — Broker Contract & Pre-Exec Helpers.
Tách khỏi main.py (GĐ 3.2).
"""
from __future__ import annotations

# ── Guard examples (from main.py lines 8212-8242) ───────────────────────────────

from nana.phases.commons import (
    PHASE7_BASE_GUARD_CONTEXT,
    PHASE7_SOCIAL_GUARD_CONTEXT,
)

PHASE8_BROKER_GUARD_EXAMPLES = [
    ("browser.read_context", {}, "allowed", "none"),
    ("browser.suggest_next_step", {}, "suggest_only", "none"),
    ("purchase.checkout", PHASE7_BASE_GUARD_CONTEXT, "blocked", "locked"),
    ("message.send", PHASE7_BASE_GUARD_CONTEXT, "blocked", "locked"),
    ("browser.click", PHASE7_BASE_GUARD_CONTEXT, "needs_confirm", "locked"),
    ("browser.type", PHASE7_BASE_GUARD_CONTEXT, "needs_confirm", "locked"),
    ("social.type_draft", PHASE7_SOCIAL_GUARD_CONTEXT, "needs_confirm", "locked"),
    ("browser.scroll", PHASE7_BASE_GUARD_CONTEXT, "needs_confirm", "none"),
    ("browser.click", {"browser_available": False}, "blocked", "locked"),
    ("browser.scroll", {"browser_available": False}, "blocked", "none"),
]

PHASE8_PRE_EXEC_GUARD_EXAMPLES = [
    ("social.draft", PHASE7_SOCIAL_GUARD_CONTEXT, "virtual_preview_only"),
    ("browser.read_context", {}, "read_only_no_executor"),
    ("browser.suggest_next_step", {}, "suggest_only_no_executor"),
    ("purchase.checkout", PHASE7_BASE_GUARD_CONTEXT, "blocked_by_policy"),
    ("message.send", PHASE7_BASE_GUARD_CONTEXT, "blocked_by_policy"),
    ("browser.click", PHASE7_BASE_GUARD_CONTEXT, "blocked_by_lock"),
    ("browser.type", PHASE7_BASE_GUARD_CONTEXT, "blocked_by_lock"),
    ("social.type_draft", PHASE7_SOCIAL_GUARD_CONTEXT, "blocked_by_lock"),
    ("browser.scroll", PHASE7_BASE_GUARD_CONTEXT, "phase8_simulation_only"),
    ("browser.scroll", {"browser_available": False}, "blocked_by_broker"),
]

PHASE8_ACTION_TRACE_GUARD_EXAMPLES = [
    ("browser.navigate_or_click", "Nana click nút đăng nhập", {**PHASE7_BASE_GUARD_CONTEXT, "browser_kind": "docs"}),
    ("social.draft", "Nana viết nháp reply tweet này", PHASE7_SOCIAL_GUARD_CONTEXT),
    ("purchase.checkout", "Nana thanh toán đơn này", {**PHASE7_BASE_GUARD_CONTEXT, "browser_kind": "shopping"}),
    ("browser.read_context", "Nana tóm tắt trang này", {**PHASE7_BASE_GUARD_CONTEXT, "browser_kind": "youtube"}),
    ("chat.respond", "50 x 10 bằng bao nhiêu", PHASE7_SOCIAL_GUARD_CONTEXT),
]


# ── Defer imports ───────────────────────────────────────────────────────────────

def _broker_context_snapshot():
    from nana.core.context import broker_context_snapshot as _fn
    return _fn()

def _action_broker():
    from nana.actions.broker import action_broker as _broker
    return _broker

def _action_registry():
    from nana.actions.registry import action_registry as _registry
    return _registry

def _autonomy_lock_block_reason(action_name):
    from nana.autonomy import autonomy_lock_block_reason as _fn
    return _fn(action_name)

def _phase9_audit_record(event, source="", intent="", actions=None, status="", blockers=None, detail="", execute=False):
    from nana.phases.phase9 import phase9_audit_record as _fn
    return _fn(event, source=source, intent=intent, actions=actions, status=status, blockers=blockers, detail=detail, execute=execute)

def _build_phase7_status_model():
    from nana.phases.phase7 import build_phase7_status_model as _fn
    return _fn()

def _shorten_line(text, limit):
    from nana.utils.format import shorten_line as _fn
    return _fn(text, limit)


# ── Phase 8 functions (from main.py lines 8245-8712) ──────────────────────────

def phase8_executor_exposure(action_name):
    from nana.phases.commons import PHASE7_VIRTUAL_ACTIONS
    if action_name in PHASE7_VIRTUAL_ACTIONS:
        return "virtual_preview_only"
    if action_name == "browser.scroll":
        return "enabled_after_confirm"
    if action_name == "social.type_draft":
        return "code_present_lock_blocked"
    if action_name in {"browser.click", "browser.type"}:
        return "executor_stub_skipped"
    return "none"


def phase8_broker_contract_row(action_name, context=None):
    context = dict(context or _broker_context_snapshot())
    registry = _action_registry()
    broker = _action_broker()
    from nana.phases.commons import PHASE7_VIRTUAL_ACTIONS
    spec = registry.get(action_name)
    virtual_kind, virtual_reason = PHASE7_VIRTUAL_ACTIONS.get(action_name, (None, None))
    decision = broker.evaluate(action_name, context=context) if not virtual_kind else None
    lock_reason = _autonomy_lock_block_reason(action_name)
    exposure = phase8_executor_exposure(action_name)
    can_reach_executor = (
        decision is not None
        and decision.status == "needs_confirm"
        and not lock_reason
        and exposure == "enabled_after_confirm"
    )
    status = "pass"
    issues = []
    if not spec and not virtual_kind:
        status = "warn"
        issues.append("action_not_registered")
    if decision and decision.can_execute and decision.permission != "read-only":
        status = "warn"
        issues.append("non_read_only_can_execute")
    if decision and decision.status == "needs_confirm" and not decision.requires_confirm:
        status = "warn"
        issues.append("needs_confirm_flag_missing")
    if action_name in PHASE7_VIRTUAL_ACTIONS.__class__({}):
        pass  # placeholder for autonomy lock check
    from nana.phases.commons import AUTONOMY_LOCK_BLOCKED_ACTIONS
    if action_name in AUTONOMY_LOCK_BLOCKED_ACTIONS and not lock_reason:
        status = "warn"
        issues.append("autonomy_lock_missing")
    return {
        "action": action_name,
        "registered": bool(spec),
        "virtual": bool(virtual_kind),
        "permission": spec.permission.value if spec else "preview-only" if virtual_kind else "unknown",
        "cost": spec.cost_estimate if spec else "unknown",
        "rollback": spec.rollback if spec else "unknown",
        "broker_status": decision.status if decision else "virtual",
        "broker_reason": decision.reason if decision else virtual_reason,
        "requires_confirm": decision.requires_confirm if decision else False,
        "can_execute": bool(decision.can_execute) if decision else False,
        "lock": lock_reason or "none",
        "executor": exposure,
        "would_reach_executor": can_reach_executor,
        "status": status,
        "issues": issues,
    }


def phase8_broker_guard_summary():
    rows = []
    failures = []
    for action_name, context, expected_status, expected_lock in PHASE8_BROKER_GUARD_EXAMPLES:
        row = phase8_broker_contract_row(action_name, context=context)
        lock_ok = bool(row["lock"] != "none") if expected_lock == "locked" else row["lock"] == "none"
        passed = row["broker_status"] == expected_status and lock_ok and row["status"] == "pass"
        guard_row = {
            **row,
            "expected_status": expected_status,
            "expected_lock": expected_lock,
            "passed": passed,
        }
        rows.append(guard_row)
        if not passed:
            failures.append(guard_row)
    return {
        "total": len(rows),
        "pass_count": len(rows) - len(failures),
        "failures": failures,
        "rows": rows,
    }


def phase8_context_check_rows(context):
    context = context or {}
    rows = [
        ("browser_available", bool(context.get("browser_available"))),
        ("browser_fresh", bool(context.get("browser_fresh"))),
        ("active_app_is_edge", bool(context.get("active_app_is_edge"))),
        ("active_window_valid", bool(context.get("active_window_valid"))),
    ]
    return [
        {"name": name, "status": "ok" if ok else "no"}
        for name, ok in rows
    ]


def phase8_pre_exec_report(action_name, context=None):
    context = dict(context or _broker_context_snapshot())
    contract = phase8_broker_contract_row(action_name, context=context)
    checks = phase8_context_check_rows(context)
    blockers = []
    if not contract["registered"] and not contract.get("virtual"):
        blockers.append("action_not_registered")
    if contract["broker_status"] == "blocked":
        blockers.append(f"broker:{contract['broker_reason']}")
    if contract["lock"] != "none":
        blockers.append(f"autonomy_lock:{contract['lock']}")
    if contract["executor"] == "none":
        blockers.append("executor:none")
    elif contract["executor"] == "executor_stub_skipped":
        blockers.append("executor:stub_skipped")
    elif contract["executor"] == "code_present_lock_blocked" and contract["lock"] != "none":
        blockers.append("executor:code_present_but_lock_blocked")
    elif contract["executor"] == "virtual_preview_only":
        blockers.append("executor:virtual_preview_only")

    if contract["broker_status"] == "virtual":
        status = "virtual_preview_only"
    elif contract["broker_status"] == "blocked" and contract["broker_reason"] == "permission_blocked":
        status = "blocked_by_policy"
    elif contract["lock"] != "none":
        status = "blocked_by_lock"
    elif contract["broker_status"] == "blocked":
        status = "blocked_by_broker"
    elif contract["broker_status"] == "suggest_only":
        status = "suggest_only_no_executor"
    elif contract["permission"] == "read-only":
        status = "read_only_no_executor"
    elif contract["executor"] == "none":
        status = "no_executor"
    elif contract["executor"] == "executor_stub_skipped":
        status = "executor_stub_skipped"
    elif contract["executor"] == "code_present_lock_blocked":
        status = "executor_locked"
    elif contract["would_reach_executor"]:
        status = "phase8_simulation_only"
    else:
        status = "not_ready"

    return {
        "action": action_name,
        "status": status,
        "contract": contract,
        "checks": checks,
        "blockers": blockers,
        "would_execute": False,
        "phase": "Phase 8 simulation",
    }


def phase8_pre_exec_guard_summary():
    rows = []
    failures = []
    for action_name, context, expected in PHASE8_PRE_EXEC_GUARD_EXAMPLES:
        report = phase8_pre_exec_report(action_name, context=context)
        passed = report["status"] == expected and report["would_execute"] is False
        row = {
            "action": action_name,
            "expected": expected,
            "got": report["status"],
            "passed": passed,
            "blockers": list(report["blockers"]),
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


def phase8_action_trace_report(raw_text, context=None):
    context = dict(context or _broker_context_snapshot())
    from nana.phases.phase7 import build_phase7_dry_run
    dry_run = build_phase7_dry_run(raw_text, context=context)
    plan = dry_run["plan"]
    action_reports = []
    for action_name in plan.actions:
        contract = phase8_broker_contract_row(action_name, context=context)
        pre_exec = phase8_pre_exec_report(action_name, context=context)
        action_reports.append({
            "action": action_name,
            "contract": contract,
            "pre_exec": pre_exec,
        })
    return {
        "text": raw_text,
        "context": context,
        "dry_run": dry_run,
        "plan": plan,
        "actions": action_reports,
        "would_execute": False,
    }


def phase8_action_trace_guard_summary():
    rows = []
    failures = []
    for expected_intent, source, context in PHASE8_ACTION_TRACE_GUARD_EXAMPLES:
        report = phase8_action_trace_report(source, context=context)
        plan = report["plan"]
        passed = (
            plan.intent == expected_intent
            and report["would_execute"] is False
            and all(action["pre_exec"]["would_execute"] is False for action in report["actions"])
        )
        row = {
            "expected": expected_intent,
            "got": plan.intent,
            "passed": passed,
            "actions": list(plan.actions),
            "source": source,
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


def phase8_contract_matrix(context=None):
    context = dict(context or _broker_context_snapshot())
    registry = _action_registry()
    return [
        phase8_broker_contract_row(action.name, context=context)
        for action in registry.list_actions()
    ]


def print_phase8_broker_matrix():
    rows = phase8_contract_matrix()
    print("🧩 Phase 8 Broker Matrix")
    print("  Action: read-only; registry -> broker -> autonomy lock -> executor exposure.")
    for row in rows:
        print(
            "  "
            f"{row['action']} | permission={row['permission']} | broker={row['broker_status']}:{row['broker_reason']} | "
            f"lock={row['lock']} | executor={row['executor']} | would_reach_executor={row['would_reach_executor']} | status={row['status']}"
        )
        if row["issues"]:
            print(f"    Issues: {', '.join(row['issues'])}")


def print_phase8_action_contract(raw_text):
    action_name = str(raw_text or "").strip()
    print("🧾 Phase 8 Action Contract")
    print("  Action: read-only; không tạo pending, không confirm, không execute.")
    if not action_name:
        print("  Missing: /action-contract <action>")
        return
    row = phase8_broker_contract_row(action_name)
    print(f"  Action: {row['action']}")
    print(f"  Registered: {row['registered']}")
    print(f"  Virtual: {row.get('virtual', False)}")
    print(f"  Permission: {row['permission']} | cost={row['cost']} | rollback={row['rollback']}")
    print(f"  Broker: {row['broker_status']} | reason={row['broker_reason']} | requires_confirm={row['requires_confirm']} | can_execute={row['can_execute']}")
    print(f"  Autonomy lock: {row['lock']}")
    print(f"  Executor exposure: {row['executor']} | would_reach_executor={row['would_reach_executor']}")
    print(f"  Status: {row['status']}")
    if row["issues"]:
        print(f"  Issues: {', '.join(row['issues'])}")
    else:
        print("  Issues: none")


def print_phase8_guard_status():
    summary = phase8_broker_guard_summary()
    print("🧪 Phase 8 Broker Guard")
    print("  Action: read-only; kiểm tra broker status + autonomy lock contract.")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for row in summary["rows"]:
        status = "pass" if row["passed"] else "fail"
        print(
            "  "
            f"{row['action']} | {status} | got={row['broker_status']} | expected={row['expected_status']} | "
            f"lock={row['lock']} | executor={row['executor']}"
        )
        if not row["passed"]:
            print(f"    Expected lock: {row['expected_lock']}")
            if row["issues"]:
                print(f"    Issues: {', '.join(row['issues'])}")


def print_phase8_pre_exec_check(raw_text):
    action_name = str(raw_text or "").strip()
    print("🧪 Phase 8 Pre-Exec Check")
    print("  Action: read-only; final gate simulation, không gọi executor.")
    if not action_name:
        print("  Missing: /pre-exec-check <action>")
        return
    report = phase8_pre_exec_report(action_name)
    contract = report["contract"]
    print(f"  Action: {report['action']}")
    print(f"  Status: {report['status']}")
    print(f"  Broker: {contract['broker_status']} | reason={contract['broker_reason']} | permission={contract['permission']}")
    print(f"  Autonomy lock: {contract['lock']}")
    print(f"  Executor exposure: {contract['executor']} | would_reach_executor={contract['would_reach_executor']}")
    print("  Context checks:")
    for check in report["checks"]:
        print(f"    {check['name']}: {check['status']}")
    print(f"  Blockers: {', '.join(report['blockers']) if report['blockers'] else 'none'}")
    print("  Execute: False")
    print("  Reason: phase8_pre_exec_simulation_only")
    try:
        _phase9_audit_record(
            event="pre_exec",
            source=action_name,
            intent="action",
            actions=[action_name],
            status=report["status"],
            blockers=report["blockers"],
            detail="phase8_pre_exec_check",
            execute=False,
        )
    except Exception:
        pass


def print_phase8_pre_exec_guard_status():
    summary = phase8_pre_exec_guard_summary()
    print("🧪 Phase 8 Pre-Exec Guard")
    print("  Action: read-only; kiểm tra final gate simulation.")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for row in summary["rows"]:
        status = "pass" if row["passed"] else "fail"
        print(
            "  "
            f"{row['action']} | {status} | got={row['got']} | expected={row['expected']}"
        )
        if row["blockers"]:
            print(f"    Blockers: {', '.join(row['blockers'])}")


def print_phase8_action_trace(raw_text):
    raw_text = str(raw_text or "").strip()
    print("🧭 Phase 8 Action Trace")
    print("  Action: read-only; intent -> actions -> broker -> pre-exec, không pending/execute.")
    if not raw_text:
        print("  Missing: /action-trace <text>")
        return
    report = phase8_action_trace_report(raw_text)
    dry_run = report["dry_run"]
    plan = report["plan"]
    context = report["context"]
    priority = dry_run["priority"]
    age = context.get("browser_age_seconds")
    age_text = "None" if age is None else f"{age:.1f}s"
    from nana.phases.phase7 import phase7_precondition_status, phase7_precondition_recovery
    print(f"  Intent: {plan.intent} | status={plan.status} | policy={plan.policy} | risk={plan.risk}")
    print(f"  Context: kind={context.get('browser_kind')} | state={context.get('browser_snapshot_state')} | age={age_text} | priority={priority}")
    print(f"  Reason: {plan.reason}")
    print(f"  Actions: {', '.join(plan.actions) if plan.actions else 'none'}")
    if dry_run["preconditions"]:
        print(f"  Preconditions: {phase7_precondition_status(dry_run['preconditions'])} | {', '.join(dry_run['preconditions'])}")
        print(f"  Recovery: {phase7_precondition_recovery(plan, context, dry_run['preconditions'])}")
    if dry_run["integration"].get("kind", "none") != "none":
        integration = dry_run["integration"]
        print(
            "  Integration: "
            f"{integration['kind']} | ready={integration['ready']} | "
            f"style={integration['style']} | preview={_shorten_line(integration['preview'], 90)}"
        )
    if report["actions"]:
        print("  Trace:")
        for item in report["actions"]:
            contract = item["contract"]
            pre_exec = item["pre_exec"]
            print(
                "    "
                f"{item['action']} | broker={contract['broker_status']}:{contract['broker_reason']} | "
                f"lock={contract['lock']} | pre_exec={pre_exec['status']} | execute=False"
            )
            if pre_exec["blockers"]:
                print(f"      Blockers: {', '.join(pre_exec['blockers'])}")
    else:
        print("  Trace: no_actions")
    print("  Execute: False")
    print("  Reason: phase8_action_trace_only")
    try:
        _phase9_audit_record(
            event="action_trace",
            source=raw_text,
            intent=plan.intent,
            actions=plan.actions,
            status=plan.status,
            blockers=list(dry_run.get("preconditions") or []),
            detail="phase8_action_trace",
            execute=False,
        )
    except Exception:
        pass


def print_phase8_action_trace_guard_status():
    summary = phase8_action_trace_guard_summary()
    print("🧪 Phase 8 Action Trace Guard")
    print("  Action: read-only; kiểm tra natural command trace.")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for row in summary["rows"]:
        status = "pass" if row["passed"] else "fail"
        print(
            "  "
            f"{row['expected']} | {status} | got={row['got']} | actions={','.join(row['actions']) if row['actions'] else 'none'}"
        )
        if not row["passed"]:
            print(f"    Source: {row['source']}")


def build_phase8_status_model():
    phase7_model = _build_phase7_status_model()
    phase7_blocking = [name for name, status, _detail in phase7_model["checks"] if status == "warn"]
    guard = phase8_broker_guard_summary()
    pre_exec_guard = phase8_pre_exec_guard_summary()
    trace_guard = phase8_action_trace_guard_summary()
    matrix = phase8_contract_matrix()
    executor_reachable = [
        row for row in matrix
        if row["would_reach_executor"]
    ]
    from nana.phases.commons import AUTONOMY_LOCK_PHASE, AUTONOMY_LOCK_RULE
    checks = [
        ("phase7_foundation", "pass" if not phase7_blocking else "warn", f"blocking={len(phase7_blocking)}"),
        ("broker_contract_matrix", "pass" if all(row["status"] == "pass" for row in matrix) else "warn", f"actions={len(matrix)}"),
        ("broker_guard_matrix", "pass" if not guard["failures"] else "warn", f"{guard['pass_count']}/{guard['total']} pass"),
        ("pre_exec_guard_matrix", "pass" if not pre_exec_guard["failures"] else "warn", f"{pre_exec_guard['pass_count']}/{pre_exec_guard['total']} pass"),
        ("action_trace_guard", "pass" if not trace_guard["failures"] else "warn", f"{trace_guard['pass_count']}/{trace_guard['total']} pass"),
        ("executor_exposure_trace", "observe" if executor_reachable else "pass", "none" if not executor_reachable else ",".join(row["action"] for row in executor_reachable)),
        ("autonomy_lock", "pass" if AUTONOMY_LOCK_RULE == "no_autonomy_no_semi_autonomy" else "warn", f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
    ]
    return {"checks": checks, "matrix": matrix, "guard": guard, "pre_exec_guard": pre_exec_guard, "trace_guard": trace_guard}


def print_phase8_status():
    model = build_phase8_status_model()
    print("🧩 Phase 8 Status")
    print("  Goal: broker contract rõ ràng trước mọi pending/executor; vẫn simulation/read-only.")
    for name, status, detail in model["checks"]:
        print(f"  {name}: {status} | {detail}")
    print("  Commands: /broker-matrix | /action-contract <action> | /broker-guard-status | /pre-exec-check <action> | /action-trace <text> | /phase8-ready")


def print_phase8_ready():
    model = build_phase8_status_model()
    guard = model["guard"]
    pre_exec_guard = model["pre_exec_guard"]
    trace_guard = model["trace_guard"]
    blocking = []
    observes = []
    for name, status, detail in model["checks"]:
        if status == "warn":
            blocking.append((name, detail))
        elif status == "observe":
            observes.append((name, detail))
    ready = not blocking
    print("✅ Phase 8 Ready" if ready else "⚠️ Phase 8 Ready")
    print("  Goal: broker/pre-exec/action-trace đủ rõ để handoff Phase 9; chưa mở executor.")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Observe: {len(observes)}")
    for name, detail in observes:
        print(f"    {name}: {detail}")
    print(
        "  Regression: "
        f"broker={guard['pass_count']}/{guard['total']} | "
        f"pre_exec={pre_exec_guard['pass_count']}/{pre_exec_guard['total']} | "
        f"action_trace={trace_guard['pass_count']}/{trace_guard['total']}"
    )
    print("  Autonomy: locked Phase 5-10; executor remains outside Phase 8 scope.")
