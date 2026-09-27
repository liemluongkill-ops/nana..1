"""
Phase 7 — Dry-Run Planning Helpers.
Tách khỏi main.py (GĐ 3.2).
"""
from __future__ import annotations

import time

# ── Globals (mirrors main.py lines 495-501) ────────────────────────────────────

PHASE7_DRY_RUN_HISTORY = []
PHASE7_DRY_RUN_NEXT_ID = 1
PHASE7_PENDING_PLAN = None
PHASE7_PENDING_LAST_CLOSED = None
PHASE7_PENDING_NEXT_ID = 1
PHASE7_PENDING_TTL_SECONDS = 60
PHASE7_ACTION_LOG = []


# ── Guard examples (from main.py lines 7085-7103) ─────────────────────────────

from nana.phases.commons import (
    PHASE7_BASE_GUARD_CONTEXT,
    PHASE7_SOCIAL_GUARD_CONTEXT,
)

PHASE7_DRY_RUN_GUARD_EXAMPLES = [
    ("social.draft", "Nana viết nháp reply tweet này", PHASE7_SOCIAL_GUARD_CONTEXT),
    ("social.reply", "Nana reply tweet này", PHASE7_SOCIAL_GUARD_CONTEXT),
    ("social.post", "Nana đăng tweet này hộ Ba", PHASE7_SOCIAL_GUARD_CONTEXT),
    ("social.react", "Nana like bài này", PHASE7_SOCIAL_GUARD_CONTEXT),
    ("purchase.checkout", "Nana thanh toán đơn này", {**PHASE7_BASE_GUARD_CONTEXT, "browser_kind": "shopping"}),
    ("message.send", "Nana gửi tin nhắn này luôn", {**PHASE7_BASE_GUARD_CONTEXT, "browser_kind": "chat"}),
    ("browser.read_context", "Nana tóm tắt trang này", {**PHASE7_BASE_GUARD_CONTEXT, "browser_kind": "youtube"}),
    ("browser.navigate_or_click", "Nana click nút đăng nhập", {**PHASE7_BASE_GUARD_CONTEXT, "browser_kind": "docs"}),
    ("chat.respond", "50 x 10 bằng bao nhiêu", {**PHASE7_BASE_GUARD_CONTEXT, "browser_kind": "unknown"}),
    ("chat.respond", "50 x 10 bằng bao nhiêu", PHASE7_SOCIAL_GUARD_CONTEXT),
]

PHASE7_PENDING_GUARD_EXAMPLES = [
    ("created", "Nana viết nháp reply tweet này", PHASE7_SOCIAL_GUARD_CONTEXT),
    ("created", "Nana click nút đăng nhập", {**PHASE7_BASE_GUARD_CONTEXT, "browser_kind": "docs"}),
    ("not_created", "Nana thanh toán đơn này", {**PHASE7_BASE_GUARD_CONTEXT, "browser_kind": "shopping"}),
    ("not_created", "50 x 10 bằng bao nhiêu", PHASE7_SOCIAL_GUARD_CONTEXT),
    ("not_created", "Nana viết nháp reply tweet này", {**PHASE7_BASE_GUARD_CONTEXT, "browser_kind": "youtube"}),
]


# ── Defer imports (avoid circular) ────────────────────────────────────────────

def _broker_context_snapshot():
    from nana.core.context import broker_context_snapshot as _fn
    return _fn()

def _action_broker():
    from nana.actions.broker import action_broker as _broker
    return _broker

def _action_registry():
    from nana.actions.registry import action_registry as _fn
    return _fn

def _pending_actions():
    from nana.actions.pending import pending_actions as _store
    return _store

def _plan_intent(raw_text, context=None):
    from nana.actions.intent import plan_intent as _fn
    return _fn(raw_text, context=context)

def _context_priority_policy(context=None, vision_description=None):
    from nana.brain.priority import context_priority_policy as _fn
    return _fn(context=context, vision_description=vision_description)

def _context_priority_brief(policy, limit=3):
    from nana.brain.priority import context_priority_brief as _fn
    return _fn(policy, limit=limit)

def _shorten_line(text, limit):
    from nana.utils.format import shorten_line as _fn
    return _fn(text, limit)

def _autonomy_lock_block_reason(action_name):
    from nana.autonomy import autonomy_lock_block_reason as _fn
    return _fn(action_name)

def _build_social_draft_source(raw_text="", broker_context=None, vision_description=None):
    from nana.integrations.social import build_social_draft_source as _fn
    return _fn(raw_text, broker_context=broker_context, vision_description=vision_description)

def _social_source_classification(source_text, broker_context=None, raw_text="", vision_description=None):
    from nana.integrations.social import social_source_classification as _fn
    return _fn(source_text, broker_context=broker_context, raw_text=raw_text, vision_description=vision_description)

def _fallback_public_social_reply(source_text=""):
    from nana.integrations.social import fallback_public_social_reply as _fn
    return _fn(source_text)

def _compact_public_reaction_reply(text, source_text="", intent="social.draft"):
    from nana.integrations.social import compact_public_reaction_reply as _fn
    return _fn(text, source_text=source_text, intent=intent)

def _social_target_missing_reason(raw_text, broker_context, context_preview, vision_description=None, omit_page_context=False):
    from nana.integrations.social import social_target_missing_reason as _fn
    return _fn(raw_text, broker_context, context_preview, vision_description=vision_description, omit_page_context=omit_page_context)

def _context_recovery_next_step(context=None, missing_reason=None, vision_description=None):
    from nana.integrations.social import context_recovery_next_step as _fn
    return _fn(context=context, missing_reason=missing_reason, vision_description=vision_description)

def _build_context_budget_preview(context, level="L2"):
    from nana.actions.privacy import build_context_budget_preview as _fn
    return _fn(context, level=level)

def _runtime_event_record(channel, event, source="", intent="", status="", actions=None, detail="", audit_id=None, execute=False):
    from nana.runtime.event_store import runtime_event_record as _fn
    return _fn(channel, event, source=source, intent=intent, status=status, actions=actions, detail=detail, audit_id=audit_id, execute=execute)

def _phase9_audit_record(event, source="", intent="", actions=None, status="", blockers=None, detail="", execute=False):
    from nana.phases.phase9 import phase9_audit_record as _fn
    return _fn(event, source=source, intent=intent, actions=actions, status=status, blockers=blockers, detail=detail, execute=execute)


# ── Helper globals (runtime events) ───────────────────────────────────────────

RUNTIME_EVENT_LOG = []
RUNTIME_EVENT_NEXT_ID = 1


# ── Runtime event helpers (from main.py lines 7365-7413) ───────────────────────

def runtime_event_record(channel, event, source="", intent="", status="", actions=None, detail="", audit_id=None, execute=False):
    global RUNTIME_EVENT_NEXT_ID
    entry = {
        "id": RUNTIME_EVENT_NEXT_ID,
        "time": time.time(),
        "channel": channel or "runtime",
        "event": event or "unknown",
        "source": source or "",
        "intent": intent or "",
        "status": status or "",
        "actions": list(actions or []),
        "detail": detail or "",
        "audit_id": audit_id,
        "execute": bool(execute),
    }
    RUNTIME_EVENT_NEXT_ID += 1
    RUNTIME_EVENT_LOG.append(entry)
    del RUNTIME_EVENT_LOG[:-50]
    return entry


def runtime_event_find(raw_id=None):
    if not RUNTIME_EVENT_LOG:
        return None
    if raw_id is None:
        return RUNTIME_EVENT_LOG[-1]
    try:
        target_id = int(str(raw_id).strip())
    except ValueError:
        return None
    for entry in reversed(RUNTIME_EVENT_LOG):
        if entry.get("id") == target_id:
            return entry
    return None


def runtime_event_summary():
    if not RUNTIME_EVENT_LOG:
        return "empty_runtime_event_log_ok"
    last = RUNTIME_EVENT_LOG[-1]
    age = max(0.0, time.time() - last.get("time", 0))
    executed = sum(1 for entry in RUNTIME_EVENT_LOG if entry.get("execute"))
    return f"count={len(RUNTIME_EVENT_LOG)}/50 last={last.get('channel')}.{last.get('event')} id={last.get('id')} {age:.0f}s ago executed={executed}"


def runtime_event_clear():
    cleared = len(RUNTIME_EVENT_LOG)
    RUNTIME_EVENT_LOG.clear()
    return cleared


# ── Phase 7 functions (from main.py lines 7148-8209) ───────────────────────────

def print_phase7_dry_run_snapshot(item):
    print("🧾 Dry Run Snapshot")
    if not item:
        print("  Status: not_found")
        return
    age = max(0.0, time.time() - item.get("time", 0))
    print("  Action: read-only; snapshot only, không confirm, không execute.")
    print(f"  ID: {item['id']} | age={age:.1f}s")
    print(f"  Intent: {item['intent']} | policy={item['policy']} | risk={item['risk']}")
    print(f"  Actions: {', '.join(item['actions']) if item['actions'] else 'none'}")
    print(f"  Quality: {item['quality']} | Preconditions: {item['precondition']}")
    validation = phase7_snapshot_validation(item)
    print(f"  Snapshot check: {validation['status']} | {validation['reason']}")
    if item["preconditions"]:
        print(f"  Missing: {', '.join(item['preconditions'])}")
    if item["command_warnings"]:
        print(f"  Warnings: {', '.join(item['command_warnings'])}")
    integration = item.get("integration") or {}
    if integration.get("kind", "none") != "none":
        print(
            "  Integration: "
            f"{integration.get('kind')} | ready={integration.get('ready')} | "
            f"style={integration.get('style')} | preview={_shorten_line(integration.get('preview'), 80)}"
        )
    print(f"  Context: kind={item.get('context_kind')} | state={item.get('context_state')} | title={_shorten_line(item.get('context_title'), 90)}")
    print(f"  Source: {_shorten_line(item.get('text'), 180)}")
    print("  Execute: False")


def phase7_snapshot_validation(item):
    current = _broker_context_snapshot()
    if not current.get("browser_available"):
        return {"status": "stale", "reason": "current_browser_unavailable"}
    current_url = current.get("browser_url")
    saved_url = item.get("context_url")
    if saved_url and current_url and saved_url != current_url:
        return {"status": "changed", "reason": "url_changed"}
    current_kind = current.get("browser_kind")
    if item.get("context_kind") and current_kind and item.get("context_kind") != current_kind:
        return {"status": "changed", "reason": "kind_changed"}
    current_state = current.get("browser_snapshot_state") or "INVALID"
    if current_state == "INVALID":
        return {"status": "stale", "reason": "current_snapshot_invalid"}
    if current_state in {"STALE", "EXPIRED"}:
        return {"status": "stale", "reason": f"current_snapshot_{current_state.lower()}"}
    age = max(0.0, time.time() - item.get("time", 0))
    if age > 120:
        return {"status": "stale", "reason": "dry_run_snapshot_old"}
    return {"status": "ok", "reason": "same_context"}


def phase7_history_summary():
    count = len(PHASE7_DRY_RUN_HISTORY)
    if not count:
        return {"count": 0, "last": None, "validation": {"status": "none", "reason": "no_snapshot"}}
    last = PHASE7_DRY_RUN_HISTORY[-1]
    return {
        "count": count,
        "last": last,
        "validation": phase7_snapshot_validation(last),
    }


def phase7_pending_plan_expired(item):
    return bool(item and time.time() >= item.get("expires_at", 0))


def phase7_current_pending_plan():
    global PHASE7_PENDING_PLAN, PHASE7_PENDING_LAST_CLOSED
    if PHASE7_PENDING_PLAN and phase7_pending_plan_expired(PHASE7_PENDING_PLAN):
        PHASE7_PENDING_LAST_CLOSED = ("expired", PHASE7_PENDING_PLAN, time.time())
        phase7_log_event("expired", PHASE7_PENDING_PLAN, detail="ttl_expired")
        PHASE7_PENDING_PLAN = None
    return PHASE7_PENDING_PLAN


def phase7_pending_create_blockers(dry_run):
    plan = dry_run["plan"]
    blockers = []
    if plan.status == "blocked":
        blockers.append("plan_blocked")
    if plan.policy == "blocked":
        blockers.append("policy_blocked")
    if not plan.actions:
        blockers.append("no_actions")
    if phase7_quality_status(dry_run["quality"]) != "pass":
        blockers.append("quality_not_pass")
    if dry_run["preconditions"]:
        blockers.append("preconditions_missing:" + ",".join(dry_run["preconditions"]))
    if dry_run["command_warnings"]:
        blockers.append("command_warning:" + ",".join(dry_run["command_warnings"]))
    if phase7_integration_status(dry_run.get("integration")) != "pass":
        blockers.append("integration_not_pass")
    return blockers


def phase7_pending_create_status(raw_text, context=None):
    dry_run = build_phase7_dry_run(raw_text, context=context)
    blockers = phase7_pending_create_blockers(dry_run)
    return dry_run, blockers


def phase7_store_pending_plan(dry_run):
    global PHASE7_PENDING_PLAN, PHASE7_PENDING_LAST_CLOSED, PHASE7_PENDING_NEXT_ID
    now = time.time()
    if PHASE7_PENDING_PLAN:
        PHASE7_PENDING_LAST_CLOSED = ("replaced", PHASE7_PENDING_PLAN, now)
        phase7_log_event("replaced", PHASE7_PENDING_PLAN, detail="new_pending_plan")
    item = {
        "id": PHASE7_PENDING_NEXT_ID,
        "time": now,
        "expires_at": now + PHASE7_PENDING_TTL_SECONDS,
        "text": dry_run["text"],
        "dry_run": dry_run,
        "intent": dry_run["plan"].intent,
        "policy": dry_run["plan"].policy,
        "risk": dry_run["plan"].risk,
        "actions": list(dry_run["plan"].actions),
        "quality": phase7_quality_status(dry_run["quality"]),
        "precondition": phase7_precondition_status(dry_run["preconditions"]),
        "preconditions": list(dry_run["preconditions"]),
        "integration": phase7_integration_snapshot(dry_run.get("integration")),
        "context_kind": dry_run["context"].get("browser_kind"),
        "context_state": dry_run["context"].get("browser_snapshot_state"),
        "context_title": dry_run["context"].get("browser_title"),
        "context_url": dry_run["context"].get("browser_url"),
        "execute": False,
    }
    PHASE7_PENDING_NEXT_ID += 1
    PHASE7_PENDING_PLAN = item
    phase7_log_event("created", item, detail="plan_preview")
    return item


def phase7_find_pending_plan(raw_id=None):
    pending = phase7_current_pending_plan()
    if not pending:
        return None, "no_pending_plan"
    if raw_id is None:
        return pending, "ok"
    try:
        target_id = int(str(raw_id).strip())
    except ValueError:
        return pending, "id_invalid"
    if pending.get("id") != target_id:
        return pending, "id_mismatch"
    return pending, "ok"


def phase7_close_pending_plan(status, raw_id=None):
    global PHASE7_PENDING_PLAN, PHASE7_PENDING_LAST_CLOSED
    pending, lookup_status = phase7_find_pending_plan(raw_id)
    if lookup_status != "ok":
        return pending, lookup_status
    PHASE7_PENDING_PLAN = None
    PHASE7_PENDING_LAST_CLOSED = (status, pending, time.time())
    phase7_log_event(status, pending, detail="phase7_pending_close")
    return pending, status


def phase7_pending_plan_summary():
    pending = phase7_current_pending_plan()
    if pending:
        age = max(0.0, time.time() - pending.get("time", 0))
        expires = max(0.0, pending.get("expires_at", 0) - time.time())
        return f"id={pending['id']} intent={pending['intent']} age={age:.0f}s expires={expires:.0f}s"
    if PHASE7_PENDING_LAST_CLOSED:
        status, item, closed_at = PHASE7_PENDING_LAST_CLOSED
        age = max(0.0, time.time() - closed_at)
        return f"none last={status}:id={item.get('id')} {age:.0f}s ago"
    return "none"


def phase7_log_event(event, item=None, detail=""):
    entry = {
        "time": time.time(),
        "event": event,
        "id": (item or {}).get("id"),
        "intent": (item or {}).get("intent"),
        "policy": (item or {}).get("policy"),
        "actions": list((item or {}).get("actions") or []),
        "detail": detail,
        "execute": False,
    }
    PHASE7_ACTION_LOG.append(entry)
    del PHASE7_ACTION_LOG[:-12]
    runtime_event_record(
        channel="phase7",
        event=event,
        source=(item or {}).get("text", ""),
        intent=(item or {}).get("intent", ""),
        status=event,
        actions=(item or {}).get("actions") or [],
        detail=detail,
        audit_id=None,
        execute=False,
    )
    try:
        _phase9_audit_record(
            event=f"phase7.{event}",
            source=(item or {}).get("text", ""),
            intent=(item or {}).get("intent", ""),
            actions=(item or {}).get("actions") or [],
            status=event,
            detail=detail,
            execute=False,
        )
    except Exception:
        pass
    return entry


def phase7_action_log_summary():
    if not PHASE7_ACTION_LOG:
        return "empty"
    last = PHASE7_ACTION_LOG[-1]
    age = max(0.0, time.time() - last.get("time", 0))
    return f"count={len(PHASE7_ACTION_LOG)}/12 last={last.get('event')} id={last.get('id')} {age:.0f}s ago"


def phase7_pending_confirm_rows(item):
    dry_run = item.get("dry_run") or {}
    rows = []
    for row in dry_run.get("action_rows") or []:
        action = row.get("action")
        lock_reason = row.get("lock_reason") or _autonomy_lock_block_reason(action)
        if lock_reason:
            result = "blocked_by_lock"
            reason = lock_reason
        elif row.get("kind") == "virtual":
            result = "preview_only"
            reason = row.get("broker_reason") or "virtual_preview_only"
        else:
            result = "dry_run_only"
            reason = "phase7_confirm_simulation_only"
        rows.append({
            "action": action,
            "result": result,
            "reason": reason,
            "execute": False,
        })
    return rows


def phase7_pending_guard_summary():
    rows = []
    failures = []
    for expected, source, context in PHASE7_PENDING_GUARD_EXAMPLES:
        dry_run, blockers = phase7_pending_create_status(source, context=context)
        got = "not_created" if blockers else "created"
        passed = got == expected
        row = {
            "expected": expected,
            "got": got,
            "passed": passed,
            "intent": dry_run["plan"].intent,
            "policy": dry_run["plan"].policy,
            "blockers": blockers,
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


def build_phase7_dry_run(raw_text, context=None):
    context = dict(context or _broker_context_snapshot())
    plan = _plan_intent(raw_text, context=context)
    vision_text = None
    try:
        from nana.brain.vision import LAST_VISION_DESCRIPTION
        if LAST_VISION_DESCRIPTION and time.time() - LAST_VISION_DESCRIPTION.get("time", 0) <= 180:
            vision_text = LAST_VISION_DESCRIPTION.get("text")
    except Exception:
        pass
    priority = _context_priority_policy(context=context, vision_description=vision_text)
    action_rows = phase7_action_rows(plan, context)
    quality = phase7_quality_issues(plan, action_rows)
    preconditions = phase7_precondition_issues(plan, context, raw_text)
    command_warnings = phase7_command_warnings(raw_text)
    integration = phase7_integration_preview(
        raw_text=raw_text,
        context=context,
        plan=plan,
        preconditions=preconditions,
        vision_text=vision_text,
    )
    return {
        "text": raw_text,
        "context": context,
        "plan": plan,
        "priority": priority,
        "action_rows": action_rows,
        "quality": quality,
        "preconditions": preconditions,
        "command_warnings": command_warnings,
        "integration": integration,
        "recovery": phase7_precondition_recovery(plan, context, preconditions),
        "execute": False,
        "execute_reason": "phase7_dry_run_only",
    }


def store_phase7_dry_run(dry_run):
    global PHASE7_DRY_RUN_NEXT_ID
    item = {
        "id": PHASE7_DRY_RUN_NEXT_ID,
        "time": time.time(),
        "text": dry_run["text"],
        "intent": dry_run["plan"].intent,
        "policy": dry_run["plan"].policy,
        "risk": dry_run["plan"].risk,
        "actions": list(dry_run["plan"].actions),
        "quality": phase7_quality_status(dry_run["quality"]),
        "precondition": phase7_precondition_status(dry_run["preconditions"]),
        "preconditions": list(dry_run["preconditions"]),
        "command_warnings": list(dry_run["command_warnings"]),
        "integration": phase7_integration_snapshot(dry_run.get("integration")),
        "context_kind": dry_run["context"].get("browser_kind"),
        "context_state": dry_run["context"].get("browser_snapshot_state"),
        "context_title": dry_run["context"].get("browser_title"),
        "context_url": dry_run["context"].get("browser_url"),
        "execute": False,
    }
    PHASE7_DRY_RUN_NEXT_ID += 1
    PHASE7_DRY_RUN_HISTORY.append(item)
    del PHASE7_DRY_RUN_HISTORY[:-5]
    return item


def find_phase7_dry_run(raw_id=None):
    if not PHASE7_DRY_RUN_HISTORY:
        return None
    if raw_id is None:
        return PHASE7_DRY_RUN_HISTORY[-1]
    try:
        target_id = int(str(raw_id).strip())
    except ValueError:
        return None
    for item in reversed(PHASE7_DRY_RUN_HISTORY):
        if item.get("id") == target_id:
            return item
    return None


def phase7_integration_snapshot(integration):
    integration = integration or {"kind": "none"}
    return {
        "kind": integration.get("kind", "none"),
        "ready": integration.get("ready", False),
        "style": integration.get("style", "none"),
        "media": integration.get("media", "unknown"),
        "guard": integration.get("guard", "none"),
        "preview": integration.get("preview", ""),
        "next": integration.get("next", ""),
    }


def phase7_integration_preview(raw_text, context, plan, preconditions, vision_text=None):
    if plan.intent not in {"social.draft", "social.post", "social.reply"}:
        return {"kind": "none"}
    context = context or {}
    draft_source = _build_social_draft_source(
        raw_text,
        broker_context=context,
        vision_description=vision_text,
    )
    media_mode, reaction_style, guard_hint = _social_source_classification(
        draft_source,
        broker_context=context,
        raw_text=raw_text,
        vision_description=vision_text,
    )
    fallback = _fallback_public_social_reply(source_text=draft_source)
    preview = _compact_public_reaction_reply(
        fallback,
        source_text=draft_source,
        intent="social.reply",
    )
    social_missing = next((issue for issue in preconditions if issue.startswith("social_target_missing:")), None)
    if social_missing:
        if "browser_kind=" in social_missing:
            guard_hint = "non_social_target"
        else:
            guard_hint = "social_context_missing"
        preview = "skipped_until_context_ready"
    ready = plan.status == "planned" and not preconditions
    if ready and vision_text:
        next_step = "/social-draft-vision " + str(raw_text or "").strip()
    elif ready:
        next_step = "/social-draft-test " + str(raw_text or "").strip()
    elif preconditions:
        next_step = phase7_precondition_recovery(plan, context, preconditions)
    else:
        next_step = "Không có bước tiếp theo."
    return {
        "kind": "social_draft_preview",
        "ready": ready,
        "media": media_mode,
        "style": reaction_style,
        "guard": guard_hint,
        "preview": preview,
        "next": next_step,
        "model": "none",
        "draft_created": False,
        "execute": False,
    }


def phase7_integration_status(integration):
    integration = integration or {"kind": "none"}
    if integration.get("kind", "none") == "none":
        return "pass"
    if integration.get("execute"):
        return "warn"
    if integration.get("draft_created"):
        return "warn"
    if integration.get("model") not in {None, "none"}:
        return "warn"
    return "pass"


def phase7_action_rows(plan, context):
    rows = []
    registry = _action_registry()
    broker = _action_broker()
    for action_name in plan.actions:
        spec = registry.get(action_name)
        lock_reason = _autonomy_lock_block_reason(action_name)
        if spec:
            decision = broker.evaluate(action_name, context=context)
            rows.append({
                "action": action_name,
                "kind": "registered",
                "permission": decision.permission,
                "broker_status": decision.status,
                "broker_reason": decision.reason,
                "lock_reason": lock_reason,
                "execute": False,
            })
            continue
        from nana.phases.commons import PHASE7_VIRTUAL_ACTIONS
        virtual_kind, virtual_reason = PHASE7_VIRTUAL_ACTIONS.get(action_name, ("virtual", "unregistered_dry_run_only"))
        rows.append({
            "action": action_name,
            "kind": virtual_kind,
            "permission": "preview-only",
            "broker_status": "virtual",
            "broker_reason": virtual_reason,
            "lock_reason": lock_reason,
            "execute": False,
        })
    return rows


def phase7_command_warnings(raw_text):
    from nana.phases.commons import KNOWN_SLASH_COMMANDS
    lowered = str(raw_text or "").lower()
    warnings = []
    for command in sorted(KNOWN_SLASH_COMMANDS, key=len, reverse=True):
        if command in {"/dry-run", "/phase7-dry-run"}:
            continue
        marker = "/" + command.lstrip("/")
        if marker in lowered:
            warnings.append(f"possible_joined_command:{command}")
            break
    return warnings


def phase7_quality_issues(plan, action_rows):
    issues = []
    if plan.status == "blocked" and action_rows:
        issues.append("blocked_plan_has_actions")
    if plan.policy == "blocked" and plan.needs_confirm:
        issues.append("blocked_policy_should_not_need_confirm")
    if plan.policy in {"confirm-light", "confirm-first", "confirm-strict"} and not plan.needs_confirm:
        issues.append("confirm_policy_missing_confirm_flag")
    if plan.intent in {"social.post", "social.reply", "social.react", "social.follow"} and not plan.needs_confirm:
        issues.append("public_social_action_missing_confirm")
    if plan.intent in {"social.post", "social.reply"} and "social.draft" not in plan.actions:
        issues.append("public_social_action_missing_draft_preview")
    if plan.intent in {"purchase.checkout", "message.send"} and plan.policy != "blocked" and not plan.needs_confirm:
        issues.append("high_impact_action_not_blocked_or_confirmed")
    for row in action_rows:
        if row.get("execute"):
            issues.append(f"action_marked_execute:{row.get('action')}")
        if row.get("lock_reason") and row.get("broker_status") in {"allowed", "needs_confirm"}:
            issues.append(f"autonomy_lock_trace:{row.get('action')}")
        if row.get("kind") == "registered" and row.get("permission") == "read-only" and row.get("broker_status") != "allowed":
            issues.append(f"read_only_not_allowed:{row.get('action')}")
        if row.get("kind") == "virtual" and row.get("permission") != "preview-only":
            issues.append(f"virtual_not_preview_only:{row.get('action')}")
    return issues


def phase7_quality_notes(issues):
    return [issue for issue in issues if issue.startswith("autonomy_lock_trace:")]


def phase7_quality_blockers(issues):
    return [issue for issue in issues if not issue.startswith("autonomy_lock_trace:")]


def phase7_quality_status(issues):
    return "warn" if phase7_quality_blockers(issues) else "pass"


def phase7_precondition_issues(plan, context, raw_text):
    issues = []
    kind = ((context or {}).get("browser_kind") or "").lower()
    if plan.intent.startswith("social."):
        preview = _build_context_budget_preview(context, level="L2")
        missing = _social_target_missing_reason(raw_text, context, preview, omit_page_context=False)
        if missing:
            issues.append(f"social_target_missing:{missing}")
    if plan.intent == "browser.read_context":
        if not context.get("browser_available"):
            issues.append("browser_unavailable")
        elif (context.get("browser_snapshot_state") or "INVALID") == "INVALID":
            issues.append("browser_snapshot_invalid")
    if plan.intent == "browser.navigate_or_click":
        if not context.get("browser_available"):
            issues.append("browser_unavailable")
        if kind == "unknown":
            issues.append("browser_target_uncertain")
        if not context.get("active_window_valid"):
            issues.append("active_window_not_validated")
    return issues


def phase7_precondition_status(preconditions):
    return "pass" if not preconditions else "needs_context"


def phase7_precondition_recovery(plan, context, preconditions):
    if not preconditions:
        return "ready"
    first = preconditions[0]
    if first.startswith("social_target_missing:"):
        reason = first.split(":", 1)[1]
        return _context_recovery_next_step(context, missing_reason=reason)
    if "browser_unavailable" in preconditions or "browser_snapshot_invalid" in preconditions:
        return "Chưa có snapshot dùng được. Mở Edge debug rồi chạy /br trước khi lập plan theo trang."
    if "active_window_not_validated" in preconditions:
        return "Cần focus đúng Edge/tab mục tiêu rồi chạy /br để xác nhận trước; Phase 7 vẫn chỉ dry-run."
    if "browser_target_uncertain" in preconditions:
        return "Thiếu loại trang/mục tiêu rõ. Mở đúng tab rồi /br hoặc nói rõ target trong lệnh."
    return "Bổ sung context còn thiếu rồi chạy lại /dry-run."


def print_phase7_dry_run(raw_text):
    dry_run = build_phase7_dry_run(raw_text)
    snapshot = store_phase7_dry_run(dry_run)
    plan = dry_run["plan"]
    context = dry_run["context"]
    priority = dry_run["priority"]
    age = context.get("browser_age_seconds")
    age_text = "None" if age is None else f"{age:.1f}s"

    print("🧪 Phase 7 Dry Run")
    print("  Action: read-only; không tạo pending, không confirm, không execute.")
    print(f"  Dry Run ID: {snapshot['id']} | Show: /dry-run-show {snapshot['id']} | Last: /dry-run-last")
    print(f"  Intent: {plan.intent} | status={plan.status} | risk={plan.risk} | policy={plan.policy} | confirm={plan.needs_confirm}")
    print(f"  Context: kind={context.get('browser_kind')} | state={context.get('browser_snapshot_state')} | age={age_text} | priority={_context_priority_brief(priority)} | overall={priority['overall']:.2f}")
    print(f"  Reason: {plan.reason}")
    print(f"  Plan: {plan.plan}")
    print(f"  Privacy: {plan.privacy_risk}")
    if plan.blocked_reasons:
        print(f"  Blocked by: {', '.join(plan.blocked_reasons)}")
    if dry_run["action_rows"]:
        print("  Actions:")
        for row in dry_run["action_rows"]:
            lock = f" | lock={row['lock_reason']}" if row.get("lock_reason") else ""
            print(
                "    "
                f"{row['action']} | {row['kind']} | permission={row['permission']} | "
                f"broker={row['broker_status']}:{row['broker_reason']} | execute=False{lock}"
            )
    else:
        print("  Actions: none")
    integration = dry_run.get("integration") or {}
    if integration.get("kind", "none") != "none":
        print(
            "  Integration: "
            f"{integration['kind']} | ready={integration['ready']} | "
            f"model={integration.get('model', 'none')} | draft_created={integration.get('draft_created', False)}"
        )
        print(
            "  Preview classifier: "
            f"media={integration['media']} | style={integration['style']} | guard={integration['guard']}"
        )
        print(f"  Preview fallback: {integration['preview']}")
        print(f"  Preview next: {_shorten_line(integration['next'], 180)}")
    blockers = phase7_quality_blockers(dry_run["quality"])
    notes = phase7_quality_notes(dry_run["quality"])
    if blockers:
        print(f"  Quality: warn | {', '.join(blockers)}")
    else:
        print("  Quality: pass | no issues")
    if notes:
        print(f"  Quality notes: {', '.join(notes)}")
    if dry_run["preconditions"]:
        print(f"  Preconditions: {phase7_precondition_status(dry_run['preconditions'])} | {', '.join(dry_run['preconditions'])}")
        print(f"  Recovery: {dry_run['recovery']}")
    else:
        print("  Preconditions: pass | ready")
    if dry_run["command_warnings"]:
        print(f"  Command warnings: {', '.join(dry_run['command_warnings'])}")
    print(f"  Safety: {', '.join(plan.safety)}")
    from nana.phases.commons import AUTONOMY_LOCK_PHASE, AUTONOMY_LOCK_RULE
    print(f"  Autonomy: {AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}; dry-run only.")


def print_phase7_quality(raw_text):
    dry_run = build_phase7_dry_run(raw_text)
    plan = dry_run["plan"]
    issues = dry_run["quality"]
    preconditions = dry_run["preconditions"]
    print("🧪 Dry Run Quality")
    print("  Action: read-only; không tạo pending, không execute.")
    print(f"  Status: {phase7_quality_status(issues)}")
    print(f"  Preconditions: {phase7_precondition_status(preconditions)}")
    print(f"  Intent: {plan.intent} | policy={plan.policy} | confirm={plan.needs_confirm}")
    print(f"  Actions: {', '.join(plan.actions) if plan.actions else 'none'}")
    integration = dry_run.get("integration") or {}
    if integration.get("kind", "none") != "none":
        print(
            "  Integration: "
            f"{integration['kind']} | ready={integration['ready']} | "
            f"style={integration['style']} | preview={_shorten_line(integration['preview'], 90)}"
        )
    if issues:
        for issue in phase7_quality_blockers(issues):
            print(f"  Issue: {issue}")
        for note in phase7_quality_notes(issues):
            print(f"  Note: {note}")
    else:
        print("  Issues: none")
    if preconditions:
        for issue in preconditions:
            print(f"  Missing: {issue}")
        print(f"  Recovery: {dry_run['recovery']}")
    else:
        print("  Missing: none")
    if dry_run["command_warnings"]:
        for warning in dry_run["command_warnings"]:
            print(f"  Warning: {warning}")
    print(f"  Source: {raw_text}")


def print_phase7_plan_preview(raw_text):
    dry_run, blockers = phase7_pending_create_status(raw_text)
    plan = dry_run["plan"]
    print("🧾 Phase 7 Pending Plan Preview")
    print("  Action: read-only; tạo pending mô phỏng, confirm vẫn không execute.")
    if blockers:
        print("  Status: not_created")
        print(f"  Intent: {plan.intent} | policy={plan.policy} | risk={plan.risk}")
        print(f"  Blockers: {', '.join(blockers)}")
        if dry_run["preconditions"]:
            print(f"  Recovery: {dry_run['recovery']}")
        print("  Execute: False")
        return
    pending = phase7_store_pending_plan(dry_run)
    integration = pending.get("integration") or {}
    print("  Status: created")
    print(f"  ID: {pending['id']} | expires_in={max(0.0, pending['expires_at'] - time.time()):.1f}s")
    print(f"  Intent: {pending['intent']} | policy={pending['policy']} | risk={pending['risk']}")
    print(f"  Actions: {', '.join(pending['actions']) if pending['actions'] else 'none'}")
    if integration.get("kind", "none") != "none":
        print(
            "  Integration: "
            f"{integration['kind']} | ready={integration['ready']} | "
            f"style={integration['style']} | preview={_shorten_line(integration['preview'], 90)}"
        )
    print(f"  Context: kind={pending.get('context_kind')} | state={pending.get('context_state')} | title={_shorten_line(pending.get('context_title'), 90)}")
    print(f"  Confirm: /plan-confirm {pending['id']}")
    print(f"  Cancel: /plan-cancel {pending['id']}")
    print("  Safety: phase7_simulation_only, no_executor, no_click_type_post")


def print_phase7_pending_plan():
    pending = phase7_current_pending_plan()
    print("🧾 Phase 7 Pending Plan")
    print("  Action: read-only; pending mô phỏng, không execute.")
    if not pending:
        print("  Pending: none")
        if PHASE7_PENDING_LAST_CLOSED:
            status, item, closed_at = PHASE7_PENDING_LAST_CLOSED
            age = max(0.0, time.time() - closed_at)
            print(f"  Last closed: {status} | id={item.get('id')} | intent={item.get('intent')} | {age:.1f}s ago")
        return
    age = max(0.0, time.time() - pending.get("time", 0))
    expires = max(0.0, pending.get("expires_at", 0) - time.time())
    validation = phase7_snapshot_validation(pending)
    integration = pending.get("integration") or {}
    print(f"  ID: {pending['id']} | age={age:.1f}s | expires_in={expires:.1f}s")
    print(f"  Intent: {pending['intent']} | policy={pending['policy']} | risk={pending['risk']}")
    print(f"  Actions: {', '.join(pending['actions']) if pending['actions'] else 'none'}")
    print(f"  Snapshot check: {validation['status']} | {validation['reason']}")
    if integration.get("kind", "none") != "none":
        print(
            "  Integration: "
            f"{integration['kind']} | ready={integration['ready']} | "
            f"style={integration['style']} | preview={_shorten_line(integration['preview'], 90)}"
        )
    print(f"  Context: kind={pending.get('context_kind')} | state={pending.get('context_state')} | title={_shorten_line(pending.get('context_title'), 90)}")
    print(f"  Confirm: /plan-confirm {pending['id']}")
    print(f"  Cancel: /plan-cancel {pending['id']}")


def print_phase7_plan_confirm(raw_id=None):
    pending, status = phase7_close_pending_plan("confirmed", raw_id)
    print("✅ Phase 7 Plan Confirm")
    print("  Action: confirm simulation only; không gọi executor, không click/type/post.")
    print(f"  Status: {status}")
    if not pending:
        print("  Execute: skipped")
        return
    print(f"  ID: {pending['id']}")
    print(f"  Intent: {pending['intent']} | policy={pending['policy']} | risk={pending['risk']}")
    if status != "confirmed":
        print(f"  Expected confirm: /plan-confirm {pending['id']}")
        print("  Execute: skipped")
        return
    validation = phase7_snapshot_validation(pending)
    print(f"  Snapshot check: {validation['status']} | {validation['reason']}")
    rows = phase7_pending_confirm_rows(pending)
    if rows:
        print("  Would actions:")
        for row in rows:
            print(
                "    "
                f"{row['action']} | result={row['result']} | execute=False | reason={row['reason']}"
            )
    else:
        print("  Would actions: none")
    print("  Execute: skipped")
    print("  Reason: phase7_confirm_simulation_only")
    from nana.phases.commons import AUTONOMY_LOCK_PHASE, AUTONOMY_LOCK_RULE
    print(f"  Autonomy: {AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}; dry-run only.")


def print_phase7_plan_cancel(raw_id=None):
    pending, status = phase7_close_pending_plan("cancelled", raw_id)
    print("🛑 Phase 7 Plan Cancel")
    print("  Action: read-only; không execute.")
    print(f"  Status: {status}")
    if pending:
        print(f"  ID: {pending['id']}")
        print(f"  Intent: {pending['intent']}")


def print_phase7_pending_guard_status():
    summary = phase7_pending_guard_summary()
    print("🧪 Phase 7 Pending Guard")
    print("  Action: read-only; kiểm tra create/not_create cho pending mô phỏng.")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for row in summary["rows"]:
        status = "pass" if row["passed"] else "fail"
        print(
            "  "
            f"{row['expected']} | {status} | got={row['got']} | "
            f"intent={row['intent']} | policy={row['policy']}"
        )
        if row["blockers"]:
            print(f"    Blockers: {', '.join(row['blockers'])}")
        if not row["passed"]:
            print(f"    Source: {row['source']}")


def print_phase7_action_log():
    print("🧾 Phase 7 Action Log")
    print("  Action: read-only; runtime-only log, không ghi disk, không execute.")
    if not PHASE7_ACTION_LOG:
        print("  Entries: none")
        return
    for entry in PHASE7_ACTION_LOG[-12:]:
        age = max(0.0, time.time() - entry.get("time", 0))
        print(
            "  "
            f"{entry.get('event')} | id={entry.get('id')} | intent={entry.get('intent')} | "
            f"actions={','.join(entry.get('actions') or []) or 'none'} | execute=False | age={age:.1f}s"
        )
        if entry.get("detail"):
            print(f"    Detail: {entry['detail']}")


def phase7_dry_run_expect_report(raw_text):
    expected, sep, source = raw_text.partition("|")
    expected = expected.strip()
    source = source.strip()
    if not sep or not expected or not source:
        return [
            "🧪 Dry Run Expect",
            "  Missing: /dry-run-expect <intent> | <text>",
        ]
    dry_run = build_phase7_dry_run(source)
    plan = dry_run["plan"]
    passed = plan.intent == expected
    lines = [
        "🧪 Dry Run Expect",
        "  Action: read-only; không tạo pending, không execute.",
        f"  Status: {'pass' if passed else 'fail'}",
        f"  Expected: {expected}",
        f"  Got: {plan.intent}",
        f"  Policy: {plan.policy}",
        f"  Risk: {plan.risk}",
        f"  Actions: {', '.join(plan.actions) if plan.actions else 'none'}",
    ]
    if not passed:
        lines.append("  Fail stage: intent_planner")
        lines.append(f"  Fail reason: expected={expected}")
    lines.append(f"  Source: {source}")
    return lines


def phase7_dry_run_guard_summary():
    failures = []
    quality_warnings = []
    precondition_warnings = []
    integration_warnings = []
    rows = []
    for expected, source, context in PHASE7_DRY_RUN_GUARD_EXAMPLES:
        dry_run = build_phase7_dry_run(source, context=context)
        plan = dry_run["plan"]
        passed = plan.intent == expected
        quality = phase7_quality_status(dry_run["quality"])
        precondition = phase7_precondition_status(dry_run["preconditions"])
        integration = phase7_integration_status(dry_run.get("integration"))
        row = {
            "expected": expected,
            "got": plan.intent,
            "passed": passed,
            "policy": plan.policy,
            "actions": list(plan.actions),
            "quality": quality,
            "quality_issues": phase7_quality_blockers(dry_run["quality"]),
            "quality_notes": phase7_quality_notes(dry_run["quality"]),
            "precondition": precondition,
            "precondition_issues": list(dry_run["preconditions"]),
            "integration": integration,
            "integration_preview": phase7_integration_snapshot(dry_run.get("integration")),
            "source": source,
        }
        rows.append(row)
        if not passed:
            failures.append(row)
        if quality != "pass":
            quality_warnings.append(row)
        if precondition != "pass":
            precondition_warnings.append(row)
        if integration != "pass":
            integration_warnings.append(row)
    return {
        "total": len(rows),
        "pass_count": len(rows) - len(failures),
        "failures": failures,
        "quality_warnings": quality_warnings,
        "precondition_warnings": precondition_warnings,
        "integration_warnings": integration_warnings,
        "rows": rows,
    }


def print_phase7_guard_status():
    summary = phase7_dry_run_guard_summary()
    print("🧪 Phase 7 Dry-Run Guard")
    print("  Action: read-only; không gọi model, không tạo pending, không execute.")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for row in summary["rows"]:
        status = "pass" if row["passed"] else "fail"
        print(
            "  "
            f"{row['expected']} | {status} | got={row['got']} | "
            f"policy={row['policy']} | actions={','.join(row['actions']) if row['actions'] else 'none'} | "
            f"quality={row['quality']} | precondition={row['precondition']} | integration={row['integration']}"
        )
        if not row["passed"]:
            print(f"    Source: {row['source']}")
        if row["quality_issues"]:
            print(f"    Quality issues: {', '.join(row['quality_issues'])}")
        if row["quality_notes"]:
            print(f"    Quality notes: {', '.join(row['quality_notes'])}")
        if row["precondition_issues"]:
            print(f"    Missing: {', '.join(row['precondition_issues'])}")


def build_phase7_status_model():
    context = _broker_context_snapshot()
    try:
        from nana.phases.phase6 import build_phase6_status_model
        phase6_model = build_phase6_status_model()
        phase6_blocking = [name for name, status, _detail in phase6_model["checks"] if status == "warn"]
    except Exception:
        phase6_model = None
        phase6_blocking = []
    guard_summary = phase7_dry_run_guard_summary()
    pending_guard = phase7_pending_guard_summary()
    history = phase7_history_summary()
    last_snapshot = history["last"]
    last_text = "none"
    if last_snapshot:
        age = max(0.0, time.time() - last_snapshot.get("time", 0))
        last_text = f"id={last_snapshot['id']} age={age:.0f}s check={history['validation']['status']}:{history['validation']['reason']}"
    pending_plan = phase7_current_pending_plan()
    pending = _pending_actions().current()
    from nana.phases.commons import AUTONOMY_LOCK_PHASE, AUTONOMY_LOCK_RULE
    checks = [
        ("phase6_foundation", "pass" if not phase6_blocking else "warn", f"blocking={len(phase6_blocking)}"),
        ("dry_run_planner", "pass", "/dry-run <text>"),
        ("dry_run_history", "pass", f"count={history['count']}/5 last={last_text}"),
        ("dry_run_integration", "pass" if not guard_summary["integration_warnings"] else "warn", f"warnings={len(guard_summary['integration_warnings'])}; social preview uses classifier+fallback only"),
        ("intent_regression", "pass" if not guard_summary["failures"] else "warn", f"{guard_summary['pass_count']}/{guard_summary['total']} pass"),
        ("plan_quality_gate", "pass" if not guard_summary["quality_warnings"] else "warn", f"warnings={len(guard_summary['quality_warnings'])}"),
        ("precondition_gate", "pass" if not guard_summary["precondition_warnings"] else "warn", f"warnings={len(guard_summary['precondition_warnings'])}"),
        ("pending_confirm_gate", "pass" if not pending_guard["failures"] else "warn", f"{pending_guard['pass_count']}/{pending_guard['total']} pass"),
        ("phase7_pending_queue", "observe" if pending_plan else "pass", phase7_pending_plan_summary()),
        ("phase7_action_log", "pass", phase7_action_log_summary()),
        ("broker_trace", "pass", "registered actions show broker status; virtual actions are preview-only"),
        ("pending_queue", "pass" if not pending else "observe", "none" if not pending else f"id={pending.id} action={pending.action}"),
        ("autonomy_lock", "pass" if AUTONOMY_LOCK_RULE == "no_autonomy_no_semi_autonomy" else "warn", f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
    ]
    return {"context": context, "checks": checks}


def print_phase7_status():
    model = build_phase7_status_model()
    context = model["context"]
    checks = model["checks"]
    print("🧩 Phase 7 Status")
    print("  Goal: dry-run planning cho hành động; không bán tự trị/chưa tự trị.")
    print(f"  Context: kind={context.get('browser_kind')} | state={context.get('browser_snapshot_state')} | available={context.get('browser_available')}")
    print("  Scope: plan intent -> list actions -> broker trace -> lock trace -> no execute.")
    for name, status, detail in checks:
        print(f"  {name}: {status} | {detail}")
    print("  Commands: /dry-run <text> | /plan-preview <text> | /plan-pending | /plan-confirm <id> | /plan-cancel <id> | /plan-log | /dry-run-guard-status | /plan-guard-status | /phase7-ready")


def print_phase7_ready():
    model = build_phase7_status_model()
    blocking = []
    observes = []
    for name, status, detail in model["checks"]:
        if status == "warn":
            blocking.append((name, detail))
        elif status == "observe":
            observes.append((name, detail))
    ready = not blocking
    print("✅ Phase 7 Ready" if ready else "⚠️ Phase 7 Ready")
    print("  Goal: dry-run + pending-confirm mô phỏng đủ ổn để handoff Phase 8; vẫn không bán tự trị/chưa tự trị.")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Observe: {len(observes)}")
    for name, detail in observes[:4]:
        print(f"    {name}: {detail}")
    print("  Regression: dry-run guard + quality + precondition + pending-confirm checked.")
    print("  Autonomy: locked Phase 5-10; Phase 7 remains dry-run only.")
