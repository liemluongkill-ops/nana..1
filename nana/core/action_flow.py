"""Action proposal flow for the new Nana runtime.

This module replaces the tiny action-proposal dependency that still pointed at
``main_cut.py``. It keeps the same command-facing behavior: propose, print a
broker decision, create a pending action only when the broker requires
confirmation, and never execute anything by itself.
"""
from __future__ import annotations

import time

from nana.actions.broker import action_broker
from nana.actions.pending import pending_actions
from nana.actions.plan import build_action_plan
from nana.actions.suggest import build_next_step_suggestion
from nana.autonomy.lock import autonomy_lock_block_reason
from nana.core.context import broker_context_snapshot


def _print_browser_state():
    from nana.runtime.browser_refresh import print_browser_state

    print_browser_state()


def print_broker_decision(action_name, force_edge=False):
    broker_context = broker_context_snapshot(force_edge=force_edge)
    decision = action_broker.evaluate(action_name, context=broker_context)
    print_broker_decision_details(decision, broker_context)


def propose_action(action_name, force_edge=False):
    broker_context = broker_context_snapshot(force_edge=force_edge)
    decision = action_broker.evaluate(action_name, context=broker_context)
    print_broker_decision_details(decision, broker_context)

    if decision.status == "suggest_only":
        print_next_step_suggestion(force_edge=force_edge)
        return

    if decision.status == "allowed" and decision.action == "browser.read_context":
        _print_browser_state()
        return

    if decision.status != "needs_confirm":
        print("  Pending: not created")
        return

    lock_reason = autonomy_lock_block_reason(decision.action)
    if lock_reason:
        print("  Pending: not created")
        print("  Guard: autonomy_lock")
        print(f"  Reason: {lock_reason}")
        return

    pending = pending_actions.propose(
        action=decision.action,
        reason=decision.reason,
        context=broker_context,
        ttl_seconds=60,
    )
    print("🧾 Pending Action Created")
    print(f"  ID: {pending.id}")
    print(f"  Action: {pending.action}")
    print(f"  Expires in: {pending.expires_in_seconds():.1f}s")
    print(f"  Confirm: /action-confirm {pending.id}")
    print(f"  Cancel: /action-cancel {pending.id}")
    print("  Preview: /action-plan")
    print("  Note: confirm sẽ chạy executor nếu action được mở, còn lại chỉ dry-run.")


def print_broker_decision_details(decision, broker_context):
    age = broker_context.get("browser_age_seconds")
    age_text = "None" if age is None else f"{age:.1f}s"
    print("🧯 Broker Decision")
    print(f"  Action: {decision.action}")
    print(f"  Status: {decision.status}")
    print(f"  Permission: {decision.permission}")
    print(f"  Requires confirm: {decision.requires_confirm}")
    print(f"  Can execute: {decision.can_execute}")
    print(f"  Reason: {decision.reason}")
    print(
        "  Context: "
        f"active_app={broker_context.get('active_app')} | "
        f"edge_active={broker_context.get('active_app_is_edge')} | "
        f"debug_force_edge={broker_context.get('debug_force_edge')}"
    )
    print(
        "  Browser: "
        f"available={broker_context.get('browser_available')} | "
        f"fresh={broker_context.get('browser_fresh')} | "
        f"age={age_text} | "
        f"kind={broker_context.get('browser_kind')}"
    )


def print_next_step_suggestion(force_edge=False):
    context = broker_context_snapshot(force_edge=force_edge)
    suggestion = build_next_step_suggestion(context)
    print("🧭 Next Step")
    print(f"  Topic: {suggestion.topic}")
    print(f"  Line: {suggestion.line}")
    print(f"  Why: {suggestion.rationale}")
    print(f"  Try: {suggestion.try_command}")
    print(f"  Safety: {', '.join(suggestion.safety)}")


def current_context_for_pending(pending):
    force_edge = bool((pending.context or {}).get("debug_force_edge"))
    return broker_context_snapshot(force_edge=force_edge)


def print_pending_action():
    snapshot = pending_actions.snapshot()
    pending = snapshot.get("pending")
    last_closed = snapshot.get("last_closed")
    print("🧾 Pending Action")
    if pending:
        print(f"  ID: {pending.id}")
        print(f"  Action: {pending.action}")
        print(f"  Reason: {pending.reason}")
        print(f"  Age: {pending.age_seconds():.1f}s")
        print(f"  Expires in: {pending.expires_in_seconds():.1f}s")
        print(f"  Confirm: /action-confirm {pending.id}")
        print(f"  Cancel: /action-cancel {pending.id}")
        return
    print("  Pending: none")
    if last_closed:
        status, action, closed_at = last_closed
        age = max(0.0, time.time() - closed_at)
        print(f"  Last closed: {status} | {action.action} | {age:.1f}s ago")


def print_action_plan(plan):
    print("🧪 Action Plan")
    print(f"  Mode: {plan.mode}")
    print(f"  Action: {plan.action}")
    print(f"  Risk: {plan.risk}")
    print(f"  Target kind: {plan.browser_kind}")
    print(f"  Target title: {plan.target_title}")
    print(f"  Target URL: {plan.target_url}")
    if plan.draft_text:
        print(f"  Draft: {_shorten_line(plan.draft_text, 180)}")
    print(f"  Would do: {plan.would_do}")
    print(f"  Safety: {', '.join(plan.safety)}")
    print(f"  Checks: {', '.join(plan.checks)}")


def print_pending_action_plan():
    pending = pending_actions.current()
    if not pending:
        print("🧪 Action Plan")
        print("  Pending: none")
        return
    plan = build_action_plan(pending, current_context=current_context_for_pending(pending))
    print_action_plan(plan)


def parse_action_id(raw):
    if raw is None:
        return None
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        print(f"⚠️ Action ID không hợp lệ: {text}")
        return None


def confirm_pending_action(action_id=None):
    pending, status = pending_actions.confirm(action_id)
    print("🧾 Pending Action Confirm")
    print(f"  Status: {status}")
    if pending:
        print(f"  ID: {pending.id}")
        print(f"  Action: {pending.action}")
    print("  Execute: False")
    print("  Note: new runtime confirm closes the pending action; executor remains guarded.")


def cancel_pending_action(action_id=None):
    pending, status = pending_actions.cancel(action_id)
    print("🧾 Pending Action Cancel")
    print(f"  Status: {status}")
    if pending:
        print(f"  ID: {pending.id}")
        print(f"  Action: {pending.action}")


def _shorten_line(text, limit):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
