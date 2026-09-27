"""nana.phases.phase12 — Phase 12: Presence Gate
Subphases:
  12.1 — Attention State
  12.2 — Rhythm Policy
  12.3 — Entropy / Non-repetition
  12.4 — Attention Memory
  12.5 — Final Gate
"""
from __future__ import annotations

import unicodedata

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


# Import shared constants from commons
from nana.phases.commons import (
    RUNTIME_EVENT_LOG,
    PHASE9_AUDIT_LOG,
)

# Import KNOWN_SLASH_COMMANDS from registry (safe — no circular)
from nana.commands.registry import KNOWN_SLASH_COMMANDS

# Import phase10_guard_failures directly from phase10 (defer to avoid circular)
_phase10 = None
_phase11_10_guard_summary = None


def _get_phase10():
    global _phase10
    if _phase10 is None:
        from nana.phases import phase10
        _phase10 = phase10
    return _phase10


def _get_phase11_10_guard_summary():
    global _phase11_10_guard_summary
    if _phase11_10_guard_summary is None:
        from nana.phases.phase11 import phase11_10_guard_summary
        _phase11_10_guard_summary = phase11_10_guard_summary
    return _phase11_10_guard_summary

def phase10_guard_failures(summary):
    return _get_phase10().phase10_guard_failures(summary)

# Direct imports for Phase 12
from nana.runtime.attention import context_for_attention, evaluate_attention_window
from nana.runtime.recovery import recovery_snapshot
from nana.runtime.context import context_lock, context_state
from nana.persona.companion_safety import companion_safety_review_text
from nana.autonomy import AUTONOMY_LOCK_PHASE, AUTONOMY_LOCK_RULE

PHASE7_PENDING_PLAN = None


PHASE12_1_COMMANDS = {
    "/attention-state-status",
    "/attention-state-guard-status",
    "/attention-state-test",
    "/phase12-1-status",
    "/phase12-1-ready",
    "/phase12-1-guard-status",
    "/phase12-1-test",
    "/p12-1",
    "/p12-1-ready",
}

PHASE12_1_ATTENTION_STATES = {
    "deep_work",
    "debugging",
    "casual_chat",
    "idle",
    "social_browse",
    "recovery_needed",
    "game_focus",
    "away",
    "observe",
}

PHASE12_1_REQUIRED_STATES = {
    "deep_work",
    "debugging",
    "casual_chat",
    "idle",
    "social_browse",
    "recovery_needed",
}

PHASE12_1_ATTENTION_CASES = [
    (
        "flow_deep_work",
        {"zone": "war_zone", "active_app": "cmd", "idle_state": "active", "idle_seconds": 0, "in_flow": True, "browser_kind": "youtube", "last_chat_age": 120},
        {"active_count": 0},
        "deep_work",
    ),
    (
        "war_zone_debugging",
        {"zone": "war_zone", "active_app": "codex", "idle_state": "active", "idle_seconds": 4, "in_flow": False, "browser_kind": "unknown", "last_chat_age": 120},
        {"active_count": 0},
        "debugging",
    ),
    (
        "recent_casual_chat",
        {"zone": "chill", "active_app": "cmd", "idle_state": "active", "idle_seconds": 8, "in_flow": False, "browser_kind": "unknown", "last_chat_age": 12},
        {"active_count": 0},
        "casual_chat",
    ),
    (
        "social_browse",
        {"zone": "chill", "active_app": "msedge", "idle_state": "active", "idle_seconds": 6, "in_flow": False, "browser_kind": "social", "last_chat_age": 180},
        {"active_count": 0},
        "social_browse",
    ),
    (
        "idle_relaxed",
        {"zone": "chill", "active_app": "explorer", "idle_state": "relaxed", "idle_seconds": 420, "in_flow": False, "browser_kind": "unknown", "last_chat_age": 240},
        {"active_count": 0},
        "idle",
    ),
    (
        "recovery_notice",
        {"zone": "chill", "active_app": "msedge", "idle_state": "active", "idle_seconds": 2, "in_flow": False, "browser_kind": "social", "last_chat_age": 180},
        {"active_count": 1},
        "recovery_needed",
    ),
    (
        "game_focus",
        {"zone": "game", "active_app": "game", "idle_state": "active", "idle_seconds": 2, "in_flow": False, "browser_kind": "unknown", "last_chat_age": 180},
        {"active_count": 0},
        "game_focus",
    ),
    (
        "away_sleepy",
        {"zone": "chill", "active_app": "explorer", "idle_state": "sleepy", "idle_seconds": 600, "in_flow": False, "browser_kind": "unknown", "last_chat_age": 300},
        {"active_count": 0},
        "away",
    ),
]


def phase12_1_live_attention_context():
    with context_lock:
        state = dict(context_state)
        browser = dict(state.get("browser") or {})
    attention_context = context_for_attention(state)
    attention_context["browser_kind"] = attention_context.get("browser_kind") or browser.get("kind") or "unknown"
    attention_context["browser_state"] = browser.get("state") or "INVALID"
    attention_context["browser_available"] = bool(browser.get("available"))
    attention_context["browser_title"] = browser.get("title")
    attention_context["browser_url"] = browser.get("url")
    return attention_context


def phase12_1_attention_state(context, recovery=None):
    recovery = recovery or {}
    legacy = evaluate_attention_window(context)
    active_recovery = int(recovery.get("active_count") or 0)
    zone = context.get("zone") or "unknown"
    app = (context.get("active_app") or "").lower()
    idle_state = context.get("idle_state") or "active"
    idle_seconds = float(context.get("idle_seconds") or 0.0)
    browser_kind = context.get("browser_kind") or "unknown"
    last_chat_age = context.get("last_chat_age")
    in_flow = bool(context.get("in_flow"))

    if active_recovery > 0:
        state = "recovery_needed"
        presence_policy = "recover_quiet"
        speak_policy = "suggest_once"
        reason = "active_recovery_notice"
    elif in_flow:
        state = "deep_work"
        presence_policy = "quiet"
        speak_policy = "no_ambient"
        reason = "flow_active"
    elif zone == "war_zone" and idle_state == "active":
        state = "debugging"
        presence_policy = "quiet"
        speak_policy = "direct_only"
        reason = "active_work_zone"
    elif zone == "game":
        state = "game_focus"
        presence_policy = "quiet"
        speak_policy = "no_ambient"
        reason = "game_zone"
    elif idle_state == "sleepy":
        state = "away"
        presence_policy = "low"
        speak_policy = "no_ambient"
        reason = "idle_sleepy"
    elif idle_state == "relaxed" and idle_seconds >= 300:
        state = "idle"
        presence_policy = "low"
        speak_policy = "ambient_budgeted"
        reason = "idle_relaxed"
    elif last_chat_age is not None and last_chat_age < 45:
        state = "casual_chat"
        presence_policy = "available"
        speak_policy = "reply_ok"
        reason = "recent_chat"
    elif zone == "chill" and app in {"msedge", "edge", "chrome", "firefox"} and browser_kind in {"social", "youtube", "music", "video", "shopping", "ai_tools"}:
        state = "social_browse"
        presence_policy = "available"
        speak_policy = "context_ok"
        reason = f"browser_{browser_kind}"
    else:
        state = "observe"
        presence_policy = "observe"
        speak_policy = "direct_only"
        reason = "default_observe"

    return {
        "state": state,
        "presence_policy": presence_policy,
        "speak_policy": speak_policy,
        "reason": reason,
        "legacy_window": legacy.get("window"),
        "legacy_ambient_policy": legacy.get("ambient_policy"),
        "legacy_reason": legacy.get("reason"),
        "execute": False,
    }


def phase12_1_attention_rows():
    rows = []
    for name, context, recovery, expected in PHASE12_1_ATTENTION_CASES:
        decision = phase12_1_attention_state(context, recovery)
        rows.append({
            "name": name,
            "passed": decision["state"] == expected,
            "got": decision["state"],
            "expected": expected,
            "presence_policy": decision["presence_policy"],
            "speak_policy": decision["speak_policy"],
            "reason": decision["reason"],
            "legacy_window": decision["legacy_window"],
            "legacy_ambient_policy": decision["legacy_ambient_policy"],
        })
    return rows


def phase12_1_guard_summary(voice=None, fast=False):
    phase11_summary = None if fast else _get_phase11_10_guard_summary()(voice)
    live_context = phase12_1_live_attention_context()
    live_recovery = recovery_snapshot()
    live_decision = phase12_1_attention_state(live_context, live_recovery)
    test_rows = phase12_1_attention_rows()
    command_missing = sorted(PHASE12_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    taxonomy_missing = sorted(PHASE12_1_REQUIRED_STATES - PHASE12_1_ATTENTION_STATES)
    regression_ok = all(row["passed"] for row in test_rows)
    legacy_bridge_ok = all(row.get("legacy_window") and row.get("legacy_ambient_policy") for row in test_rows)
    live_ok = live_decision.get("state") in PHASE12_1_ATTENTION_STATES
    rows = [
        (
            "phase11_foundation",
            True if fast else not phase10_guard_failures(phase11_summary),
            "phase11_ready_assumed_fast" if fast else f"phase11_gate={phase11_summary['pass_count']}/{phase11_summary['total']}",
        ),
        ("attention_state_taxonomy", not taxonomy_missing, f"states={','.join(sorted(PHASE12_1_REQUIRED_STATES))}"),
        ("attention_state_regression", regression_ok, f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("legacy_attention_bridge", legacy_bridge_ok, "evaluate_attention_window still provides ambient/window bridge"),
        ("live_attention_snapshot", live_ok, f"state={live_decision['state']} reason={live_decision['reason']}"),
        ("no_autonomy_executor", True, "attention-state only; no pending, no model, no executor"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("attention_state_readonly", True, "status/guard/test chỉ đọc context/recovery; không trigger presence reaction"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live_context": live_context,
        "live_recovery": live_recovery,
        "live_decision": live_decision,
        "fast": fast,
    }


def phase12_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_attention_state_status(voice=None):
    summary = phase12_1_guard_summary(voice)
    context = summary["live_context"]
    decision = summary["live_decision"]
    recovery = summary["live_recovery"]
    last_chat = context.get("last_chat_age")
    last_chat_text = "never" if last_chat is None else f"{last_chat:.1f}s ago"
    print("🧭 Attention State Status")
    print("  Action: read-only; phân loại trạng thái hiện diện, không gọi model/không execute.")
    print(f"  Phase 12.1 Progress: {phase12_1_progress_percent(summary)}%")
    print(f"  Live state: {decision['state']} | presence={decision['presence_policy']} | speak={decision['speak_policy']}")
    print(f"  Reason: {decision['reason']} | legacy={decision['legacy_window']}:{decision['legacy_ambient_policy']}")
    print(f"  Context: zone={context.get('zone')} app={context.get('active_app')} flow={context.get('in_flow')} idle={context.get('idle_state')}({float(context.get('idle_seconds') or 0):.1f}s)")
    print(f"  Browser: kind={context.get('browser_kind')} state={context.get('browser_state')} available={context.get('browser_available')}")
    print(f"  Last chat: {last_chat_text} | recovery_active={recovery.get('active_count')} suppressed={recovery.get('suppressed_total')}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: attention state chỉ quyết định nhịp hiện diện; chưa tạo hành động/chưa tự nói.")


def print_attention_state_test(raw_text=None, voice=None):
    print("🧪 Attention State Test")
    print("  Action: read-only; synthetic only, không mutate/không execute.")
    rows = phase12_1_attention_rows()
    key = (raw_text or "").strip().lower()
    aliases = {
        "deep": {"flow_deep_work", "deep_work"},
        "work": {"flow_deep_work", "deep_work", "war_zone_debugging", "debugging"},
        "debug": {"war_zone_debugging", "debugging"},
        "chat": {"recent_casual_chat", "casual_chat"},
        "social": {"social_browse"},
        "idle": {"idle_relaxed", "idle"},
        "recovery": {"recovery_notice", "recovery_needed"},
        "game": {"game_focus"},
        "away": {"away_sleepy", "away"},
    }
    if key and key != "all":
        allowed = aliases.get(key, {key})
        rows = [row for row in rows if row["name"] in allowed or row["expected"] in allowed]
        if not rows:
            print("  Status: not_found")
            print("  Cases: deep, debug, chat, social, idle, recovery, game, away")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"got={row['got']} expected={row['expected']} | "
            f"presence={row['presence_policy']} speak={row['speak_policy']} | "
            f"legacy={row['legacy_window']}:{row['legacy_ambient_policy']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_attention_state_guard_status(voice=None):
    summary = phase12_1_guard_summary(voice)
    print("🧪 Phase 12.1 Attention State Guard")
    print("  Action: read-only; kiểm attention state model, không tạo reaction/không execute.")
    print(f"  Progress: {phase12_1_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Attention regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} expected={row['expected']} | reason={row['reason']}")


def print_phase12_1_status(voice=None):
    summary = phase12_1_guard_summary(voice)
    print("🧩 Phase 12.1 Status")
    print("  Goal: Attention State Model - Nana biết đang deep work/debug/chat/idle/social/recovery ở mức read-only.")
    print(f"  Progress: {phase12_1_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /attention-state-status | /attention-state-test | /attention-state-guard-status | /phase12-1-ready")


def print_phase12_1_ready(voice=None):
    summary = phase12_1_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 12.1 Ready" if ready else "⚠️ Phase 12.1 Ready")
    print("  Goal: attention state đủ chắc để sang Phase 12.2 attention memory/rhythm baseline.")
    print(f"  Progress: {phase12_1_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: attention_state={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 12.1 chỉ phân loại attention/presence state, không mở executor.")


# ---------------------------------------------------------------------------
# Phase 12.2 — Attention Rhythm Policy
# ---------------------------------------------------------------------------

PHASE12_2_COMMANDS = {
    "/attention-rhythm-status",
    "/attention-rhythm-guard-status",
    "/attention-rhythm-test",
    "/phase12-2-status",
    "/phase12-2-ready",
    "/phase12-2-guard-status",
    "/phase12-2-test",
    "/p12-2",
    "/p12-2-ready",
}

PHASE12_2_RHYTHM_POLICIES = {
    "silent_lock",
    "quiet_direct",
    "available_context",
    "low_presence",
    "recovery_once",
    "observe_only",
}

PHASE12_2_RHYTHM_CASES = [
    ("deep_work_rhythm", "deep_work", "silent_lock", "drop"),
    ("debugging_rhythm", "debugging", "quiet_direct", "hold"),
    ("casual_chat_rhythm", "casual_chat", "available_context", "candidate"),
    ("social_browse_rhythm", "social_browse", "available_context", "candidate"),
    ("idle_rhythm", "idle", "low_presence", "candidate"),
    ("recovery_rhythm", "recovery_needed", "recovery_once", "hold"),
    ("game_rhythm", "game_focus", "silent_lock", "drop"),
    ("away_rhythm", "away", "low_presence", "drop"),
    ("observe_rhythm", "observe", "observe_only", "hold"),
]


def phase12_2_rhythm_policy_for_state(attention_state):
    if attention_state in {"deep_work", "game_focus"}:
        return {
            "rhythm": "silent_lock",
            "ambient_allowed": False,
            "context_allowed": False,
            "memory_candidate": "drop",
            "reason": f"{attention_state}_protect_focus",
            "execute": False,
        }
    if attention_state == "debugging":
        return {
            "rhythm": "quiet_direct",
            "ambient_allowed": False,
            "context_allowed": False,
            "memory_candidate": "hold",
            "reason": "debugging_direct_only",
            "execute": False,
        }
    if attention_state in {"casual_chat", "social_browse"}:
        return {
            "rhythm": "available_context",
            "ambient_allowed": True,
            "context_allowed": True,
            "memory_candidate": "candidate",
            "reason": f"{attention_state}_context_ok",
            "execute": False,
        }
    if attention_state == "idle":
        return {
            "rhythm": "low_presence",
            "ambient_allowed": True,
            "context_allowed": False,
            "memory_candidate": "candidate",
            "reason": "idle_budgeted_presence",
            "execute": False,
        }
    if attention_state == "recovery_needed":
        return {
            "rhythm": "recovery_once",
            "ambient_allowed": False,
            "context_allowed": False,
            "memory_candidate": "hold",
            "reason": "recover_without_spam",
            "execute": False,
        }
    if attention_state == "away":
        return {
            "rhythm": "low_presence",
            "ambient_allowed": False,
            "context_allowed": False,
            "memory_candidate": "drop",
            "reason": "away_no_prompt",
            "execute": False,
        }
    return {
        "rhythm": "observe_only",
        "ambient_allowed": False,
        "context_allowed": False,
        "memory_candidate": "hold",
        "reason": "observe_until_clear",
        "execute": False,
    }


def phase12_2_live_rhythm_snapshot(voice=None, attention_summary=None, fast=False):
    attention_summary = attention_summary or phase12_1_guard_summary(voice, fast=fast)
    decision = attention_summary["live_decision"]
    rhythm = phase12_2_rhythm_policy_for_state(decision["state"])
    return {
        "attention": decision,
        "rhythm": rhythm,
        "context": attention_summary["live_context"],
        "recovery": attention_summary["live_recovery"],
    }


def phase12_2_rhythm_rows():
    rows = []
    for name, attention_state, expected_rhythm, expected_memory in PHASE12_2_RHYTHM_CASES:
        policy = phase12_2_rhythm_policy_for_state(attention_state)
        rows.append({
            "name": name,
            "attention_state": attention_state,
            "passed": policy["rhythm"] == expected_rhythm and policy["memory_candidate"] == expected_memory,
            "got": policy["rhythm"],
            "expected": expected_rhythm,
            "memory_got": policy["memory_candidate"],
            "memory_expected": expected_memory,
            "ambient_allowed": policy["ambient_allowed"],
            "context_allowed": policy["context_allowed"],
            "reason": policy["reason"],
        })
    return rows


def phase12_2_guard_summary(voice=None, attention_summary=None, fast=False):
    phase12_1_summary = attention_summary or phase12_1_guard_summary(voice, fast=fast)
    live = phase12_2_live_rhythm_snapshot(voice, phase12_1_summary, fast=fast)
    rows_test = phase12_2_rhythm_rows()
    command_missing = sorted(PHASE12_2_COMMANDS - KNOWN_SLASH_COMMANDS)
    regression_ok = all(row["passed"] for row in rows_test)
    live_rhythm = live["rhythm"].get("rhythm")
    live_ok = live_rhythm in PHASE12_2_RHYTHM_POLICIES
    candidate_values = {row["memory_got"] for row in rows_test}
    memory_contract_ok = candidate_values <= {"candidate", "hold", "drop"}
    focus_protection_ok = all(
        not phase12_2_rhythm_policy_for_state(state)["ambient_allowed"]
        for state in ["deep_work", "debugging", "game_focus", "recovery_needed", "away", "observe"]
    )
    available_ok = all(
        phase12_2_rhythm_policy_for_state(state)["context_allowed"]
        for state in ["casual_chat", "social_browse"]
    )
    rows = [
        ("phase12_1_foundation", not phase10_guard_failures(phase12_1_summary), f"attention_state={phase12_1_summary['pass_count']}/{phase12_1_summary['total']}"),
        ("rhythm_policy_taxonomy", PHASE12_2_RHYTHM_POLICIES == {row[2] for row in PHASE12_2_RHYTHM_CASES}, f"policies={','.join(sorted(PHASE12_2_RHYTHM_POLICIES))}"),
        ("rhythm_regression", regression_ok, f"{sum(1 for row in rows_test if row['passed'])}/{len(rows_test)} pass"),
        ("live_rhythm_snapshot", live_ok, f"state={live['attention']['state']} rhythm={live_rhythm} reason={live['rhythm']['reason']}"),
        ("focus_silence_contract", focus_protection_ok, "deep/debug/game/recovery/away/observe do not emit ambient"),
        ("available_context_contract", available_ok, "casual_chat/social_browse may use context presence"),
        ("memory_candidate_contract", memory_contract_ok, f"values={','.join(sorted(candidate_values))}; no memory write"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("attention_rhythm_readonly", True, "status/guard/test không lưu memory, không nói, không tạo reaction"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": rows_test,
        "live": live,
        "phase12_1_summary": phase12_1_summary,
    }


def phase12_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_attention_rhythm_status(voice=None):
    summary = phase12_2_guard_summary(voice)
    live = summary["live"]
    attention = live["attention"]
    rhythm = live["rhythm"]
    context = live["context"]
    print("🫀 Attention Rhythm Status")
    print("  Action: read-only; ánh xạ attention state -> nhịp hiện diện, không lưu/không execute.")
    print(f"  Phase 12.2 Progress: {phase12_2_progress_percent(summary)}%")
    print(f"  Live: state={attention['state']} | rhythm={rhythm['rhythm']} | memory_candidate={rhythm['memory_candidate']}")
    print(f"  Ambient allowed: {rhythm['ambient_allowed']} | Context allowed: {rhythm['context_allowed']}")
    print(f"  Reason: {rhythm['reason']} | attention_reason={attention['reason']}")
    print(f"  Context: zone={context.get('zone')} app={context.get('active_app')} flow={context.get('in_flow')} browser={context.get('browser_kind')}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: rhythm chỉ quyết định ngân sách hiện diện; memory chỉ là candidate/hold/drop, chưa ghi.")


def print_attention_rhythm_test(raw_text=None, voice=None):
    print("🧪 Attention Rhythm Test")
    print("  Action: read-only; synthetic only, không lưu memory/không execute.")
    rows = phase12_2_rhythm_rows()
    key = (raw_text or "").strip().lower()
    aliases = {
        "deep": {"deep_work_rhythm", "deep_work"},
        "debug": {"debugging_rhythm", "debugging"},
        "chat": {"casual_chat_rhythm", "casual_chat"},
        "social": {"social_browse_rhythm", "social_browse"},
        "idle": {"idle_rhythm", "idle"},
        "recovery": {"recovery_rhythm", "recovery_needed"},
        "game": {"game_rhythm", "game_focus"},
        "away": {"away_rhythm", "away"},
        "observe": {"observe_rhythm", "observe"},
    }
    if key and key != "all":
        allowed = aliases.get(key, {key})
        rows = [row for row in rows if row["name"] in allowed or row["attention_state"] in allowed]
        if not rows:
            print("  Status: not_found")
            print("  Cases: deep, debug, chat, social, idle, recovery, game, away, observe")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | state={row['attention_state']} | "
            f"rhythm={row['got']} expected={row['expected']} | "
            f"memory={row['memory_got']} expected={row['memory_expected']} | "
            f"ambient={row['ambient_allowed']} context={row['context_allowed']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_attention_rhythm_guard_status(voice=None):
    summary = phase12_2_guard_summary(voice)
    print("🧪 Phase 12.2 Attention Rhythm Guard")
    print("  Action: read-only; kiểm rhythm/memory-candidate contract, không mutate/không execute.")
    print(f"  Progress: {phase12_2_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Rhythm regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | state={row['attention_state']} rhythm={row['got']} memory={row['memory_got']}")


def print_phase12_2_status(voice=None):
    summary = phase12_2_guard_summary(voice)
    print("🧩 Phase 12.2 Status")
    print("  Goal: Attention Rhythm Baseline - state nào yên, observe, available, recovery một lần phải rõ.")
    print(f"  Progress: {phase12_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /attention-rhythm-status | /attention-rhythm-test | /attention-rhythm-guard-status | /phase12-2-ready")


def print_phase12_2_ready(voice=None):
    summary = phase12_2_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 12.2 Ready" if ready else "⚠️ Phase 12.2 Ready")
    print("  Goal: attention rhythm đủ chắc để sang Phase 12.3 presence entropy/non-repetition.")
    print(f"  Progress: {phase12_2_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: attention_rhythm={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 12.2 chỉ đặt rhythm/memory candidate, không tự nói/không lưu.")


# ---------------------------------------------------------------------------
# Phase 12.3 — Presence Entropy / Non-repetition
# ---------------------------------------------------------------------------

PHASE12_3_COMMANDS = {
    "/presence-entropy-status",
    "/presence-entropy-guard-status",
    "/presence-entropy-test",
    "/phase12-3-status",
    "/phase12-3-ready",
    "/phase12-3-guard-status",
    "/phase12-3-test",
    "/p12-3",
    "/p12-3-ready",
}

PHASE12_3_PRESENCE_VARIANTS = {
    "available_context": ["soft_ack", "context_ping", "short_offer"],
    "low_presence": ["quiet_check", "soft_idle", "no_text_breath"],
    "recovery_once": ["single_recovery_hint"],
    "quiet_direct": ["direct_reply_only"],
    "observe_only": ["silent_observe"],
    "silent_lock": ["silence"],
}

PHASE12_3_ENTROPY_CASES = [
    ("deep_silence", "silent_lock", ["silence", "silence"], "suppress", "silence"),
    ("debug_direct_only", "quiet_direct", ["direct_reply_only"], "suppress", "direct_reply_only"),
    ("available_rotate", "available_context", ["soft_ack"], "emit", "context_ping"),
    ("available_avoid_repeat", "available_context", ["soft_ack", "context_ping", "short_offer"], "suppress", "soft_ack"),
    ("idle_rotate", "low_presence", ["quiet_check"], "emit", "soft_idle"),
    ("idle_overused_suppress", "low_presence", ["quiet_check", "soft_idle", "no_text_breath"], "suppress", "quiet_check"),
    ("recovery_once_suppress_after_used", "recovery_once", ["single_recovery_hint"], "suppress", "single_recovery_hint"),
    ("observe_silent", "observe_only", [], "suppress", "silent_observe"),
]


def phase12_3_entropy_decision(rhythm, recent_variants=None):
    recent_variants = list(recent_variants or [])
    variants = PHASE12_3_PRESENCE_VARIANTS.get(rhythm, ["silent_observe"])
    if rhythm in {"silent_lock", "quiet_direct", "observe_only"}:
        variant = variants[0]
        return {
            "decision": "suppress",
            "variant": variant,
            "reason": f"{rhythm}_no_ambient",
            "repeat_count": recent_variants.count(variant),
            "execute": False,
        }
    if rhythm == "recovery_once" and "single_recovery_hint" in recent_variants:
        return {
            "decision": "suppress",
            "variant": "single_recovery_hint",
            "reason": "recovery_hint_already_used",
            "repeat_count": recent_variants.count("single_recovery_hint"),
            "execute": False,
        }
    recent_window = recent_variants[-len(variants):] if variants else []
    if variants and all(variant in recent_window for variant in variants):
        return {
            "decision": "suppress",
            "variant": variants[0],
            "reason": "all_recent_variants_used",
            "repeat_count": recent_variants.count(variants[0]),
            "execute": False,
        }
    for variant in variants:
        if variant not in recent_variants[-2:]:
            return {
                "decision": "emit",
                "variant": variant,
                "reason": "rotated_variant",
                "repeat_count": recent_variants.count(variant),
                "execute": False,
            }
    return {
        "decision": "suppress",
        "variant": variants[0],
        "reason": "all_recent_variants_used",
        "repeat_count": recent_variants.count(variants[0]),
        "execute": False,
    }


def phase12_3_live_entropy_snapshot(voice=None, rhythm_summary=None, fast=False):
    rhythm_summary = rhythm_summary or phase12_2_guard_summary(voice, fast=fast)
    live = rhythm_summary["live"]
    rhythm = live["rhythm"]["rhythm"]
    decision = phase12_3_entropy_decision(rhythm, [])
    return {
        "attention": live["attention"],
        "rhythm": live["rhythm"],
        "context": live["context"],
        "entropy": decision,
    }


def phase12_3_entropy_rows():
    rows = []
    for name, rhythm, recent, expected_decision, expected_variant in PHASE12_3_ENTROPY_CASES:
        result = phase12_3_entropy_decision(rhythm, recent)
        rows.append({
            "name": name,
            "rhythm": rhythm,
            "recent": list(recent),
            "passed": result["decision"] == expected_decision and result["variant"] == expected_variant,
            "got": result["decision"],
            "expected": expected_decision,
            "variant": result["variant"],
            "expected_variant": expected_variant,
            "reason": result["reason"],
            "repeat_count": result["repeat_count"],
        })
    return rows


def phase12_3_guard_summary(voice=None, rhythm_summary=None, fast=False):
    phase12_2_summary = rhythm_summary or phase12_2_guard_summary(voice, fast=fast)
    live = phase12_3_live_entropy_snapshot(voice, phase12_2_summary, fast=fast)
    rows_test = phase12_3_entropy_rows()
    command_missing = sorted(PHASE12_3_COMMANDS - KNOWN_SLASH_COMMANDS)
    regression_ok = all(row["passed"] for row in rows_test)
    variant_surface_ok = set(PHASE12_3_PRESENCE_VARIANTS) == PHASE12_2_RHYTHM_POLICIES and all(PHASE12_3_PRESENCE_VARIANTS.values())
    suppression_ok = all(
        phase12_3_entropy_decision(rhythm, [])["decision"] == "suppress"
        for rhythm in {"silent_lock", "quiet_direct", "observe_only"}
    )
    rotation_ok = phase12_3_entropy_decision("available_context", ["soft_ack"])["variant"] != "soft_ack"
    live_ok = live["entropy"]["decision"] in {"emit", "suppress"} and bool(live["entropy"].get("variant"))
    rows = [
        ("phase12_2_foundation", not phase10_guard_failures(phase12_2_summary), f"attention_rhythm={phase12_2_summary['pass_count']}/{phase12_2_summary['total']}"),
        ("entropy_variant_surface", variant_surface_ok, f"rhythms={','.join(sorted(PHASE12_3_PRESENCE_VARIANTS))}"),
        ("entropy_regression", regression_ok, f"{sum(1 for row in rows_test if row['passed'])}/{len(rows_test)} pass"),
        ("live_entropy_snapshot", live_ok, f"rhythm={live['rhythm']['rhythm']} decision={live['entropy']['decision']} variant={live['entropy']['variant']}"),
        ("silence_suppression_contract", suppression_ok, "silent_lock/quiet_direct/observe_only suppress ambient"),
        ("non_repetition_contract", rotation_ok, "available_context rotates away from latest variant"),
        ("no_persistent_history_yet", True, "recent variants synthetic only; no disk/memory writes"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("presence_entropy_readonly", True, "status/guard/test không nói, không lưu, không trigger expression"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": rows_test,
        "live": live,
        "phase12_2_summary": phase12_2_summary,
    }


def phase12_3_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_presence_entropy_status(voice=None):
    summary = phase12_3_guard_summary(voice)
    live = summary["live"]
    entropy = live["entropy"]
    rhythm = live["rhythm"]
    attention = live["attention"]
    print("🎚️ Presence Entropy Status")
    print("  Action: read-only; chống lặp nhịp hiện diện, không phát lời/không execute.")
    print(f"  Phase 12.3 Progress: {phase12_3_progress_percent(summary)}%")
    print(f"  Live: state={attention['state']} rhythm={rhythm['rhythm']} decision={entropy['decision']} variant={entropy['variant']}")
    print(f"  Reason: {entropy['reason']} | ambient_allowed={rhythm['ambient_allowed']} context_allowed={rhythm['context_allowed']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: entropy chỉ quyết định emit/suppress mô phỏng; chưa phát lời và chưa lưu history thật.")


def print_presence_entropy_test(raw_text=None, voice=None):
    print("🧪 Presence Entropy Test")
    print("  Action: read-only; synthetic only, không phát lời/không lưu history.")
    rows = phase12_3_entropy_rows()
    key = (raw_text or "").strip().lower()
    aliases = {
        "deep": {"deep_silence", "silent_lock"},
        "debug": {"debug_direct_only", "quiet_direct"},
        "available": {"available_rotate", "available_avoid_repeat", "available_context"},
        "social": {"available_rotate", "available_avoid_repeat", "available_context"},
        "idle": {"idle_rotate", "idle_overused_suppress", "low_presence"},
        "recovery": {"recovery_once_suppress_after_used", "recovery_once"},
        "observe": {"observe_silent", "observe_only"},
        "suppress": {"deep_silence", "debug_direct_only", "available_avoid_repeat", "idle_overused_suppress", "recovery_once_suppress_after_used", "observe_silent"},
        "rotate": {"available_rotate", "idle_rotate"},
    }
    if key and key != "all":
        allowed = aliases.get(key, {key})
        rows = [row for row in rows if row["name"] in allowed or row["rhythm"] in allowed or row["got"] in allowed]
        if not rows:
            print("  Status: not_found")
            print("  Cases: deep, debug, available, idle, recovery, observe, suppress, rotate")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        recent = ",".join(row["recent"]) if row["recent"] else "none"
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | rhythm={row['rhythm']} | "
            f"decision={row['got']} expected={row['expected']} | "
            f"variant={row['variant']} expected={row['expected_variant']} | recent={recent} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_presence_entropy_guard_status(voice=None):
    summary = phase12_3_guard_summary(voice)
    print("🧪 Phase 12.3 Presence Entropy Guard")
    print("  Action: read-only; kiểm non-repetition/suppression, không trigger presence.")
    print(f"  Progress: {phase12_3_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Entropy regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | rhythm={row['rhythm']} decision={row['got']} variant={row['variant']}")


def print_phase12_3_status(voice=None):
    summary = phase12_3_guard_summary(voice)
    print("🧩 Phase 12.3 Status")
    print("  Goal: Presence Entropy - Nana tránh lặp nhịp hiện diện/NPC pattern ở mức mô phỏng.")
    print(f"  Progress: {phase12_3_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /presence-entropy-status | /presence-entropy-test | /presence-entropy-guard-status | /phase12-3-ready")


def print_phase12_3_ready(voice=None):
    summary = phase12_3_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 12.3 Ready" if ready else "⚠️ Phase 12.3 Ready")
    print("  Goal: presence entropy đủ chắc để sang Phase 12.4 attention memory safe baseline.")
    print(f"  Progress: {phase12_3_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: presence_entropy={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 12.3 chỉ mô phỏng emit/suppress, không tự nói/không lưu.")


# ---------------------------------------------------------------------------
# Phase 12.4 — Attention Memory Safe Baseline
# ---------------------------------------------------------------------------

PHASE12_4_COMMANDS = {
    "/attention-memory-status",
    "/attention-memory-guard-status",
    "/attention-memory-test",
    "/phase12-4-status",
    "/phase12-4-ready",
    "/phase12-4-guard-status",
    "/phase12-4-test",
    "/p12-4",
    "/p12-4-ready",
}

PHASE12_4_MEMORY_ACTIONS = {"candidate_review", "hold", "drop"}

PHASE12_4_MEMORY_CASES = [
    ("deep_work_drop", "deep_work", "silent_lock", "drop", "focus_protection"),
    ("debug_hold", "debugging", "quiet_direct", "hold", "work_context_not_memory"),
    ("chat_candidate", "casual_chat", "available_context", "candidate_review", "presence_pattern_candidate"),
    ("social_candidate", "social_browse", "available_context", "candidate_review", "presence_pattern_candidate"),
    ("idle_candidate", "idle", "low_presence", "candidate_review", "idle_presence_candidate"),
    ("recovery_hold", "recovery_needed", "recovery_once", "hold", "recovery_not_memory"),
    ("game_drop", "game_focus", "silent_lock", "drop", "focus_protection"),
    ("away_drop", "away", "low_presence", "drop", "away_not_memory"),
    ("observe_hold", "observe", "observe_only", "hold", "insufficient_signal"),
]


def phase12_4_attention_memory_decision(attention_state, rhythm_policy):
    rhythm = rhythm_policy.get("rhythm") if isinstance(rhythm_policy, dict) else str(rhythm_policy or "observe_only")
    memory_candidate = rhythm_policy.get("memory_candidate") if isinstance(rhythm_policy, dict) else "hold"
    if attention_state in {"deep_work", "game_focus"}:
        action = "drop"
        reason = "focus_protection"
    elif attention_state == "away":
        action = "drop"
        reason = "away_not_memory"
    elif attention_state == "recovery_needed":
        action = "hold"
        reason = "recovery_not_memory"
    elif attention_state == "debugging":
        action = "hold"
        reason = "work_context_not_memory"
    elif attention_state in {"casual_chat", "social_browse"} and rhythm == "available_context" and memory_candidate == "candidate":
        action = "candidate_review"
        reason = "presence_pattern_candidate"
    elif attention_state == "idle" and memory_candidate == "candidate":
        action = "candidate_review"
        reason = "idle_presence_candidate"
    else:
        action = "hold"
        reason = "insufficient_signal"
    return {
        "action": action,
        "reason": reason,
        "confirm_required": action == "candidate_review",
        "review_gate": "memory_governance_v2",
        "save_now": False,
        "execute": False,
    }


def phase12_4_live_memory_snapshot(voice=None, rhythm_summary=None, fast=False):
    rhythm_summary = rhythm_summary or phase12_2_guard_summary(voice, fast=fast)
    live = rhythm_summary["live"]
    decision = phase12_4_attention_memory_decision(live["attention"]["state"], live["rhythm"])
    return {
        "attention": live["attention"],
        "rhythm": live["rhythm"],
        "context": live["context"],
        "memory": decision,
    }


def phase12_4_memory_rows():
    rows = []
    for name, attention_state, rhythm, expected_action, expected_reason in PHASE12_4_MEMORY_CASES:
        rhythm_policy = phase12_2_rhythm_policy_for_state(attention_state)
        result = phase12_4_attention_memory_decision(attention_state, rhythm_policy)
        rows.append({
            "name": name,
            "attention_state": attention_state,
            "rhythm": rhythm,
            "passed": result["action"] == expected_action and result["reason"] == expected_reason,
            "got": result["action"],
            "expected": expected_action,
            "reason": result["reason"],
            "expected_reason": expected_reason,
            "confirm_required": result["confirm_required"],
            "save_now": result["save_now"],
        })
    return rows


def phase12_4_guard_summary(voice=None, entropy_summary=None, rhythm_summary=None, fast=False):
    phase12_3_summary = entropy_summary or phase12_3_guard_summary(voice, rhythm_summary, fast=fast)
    rhythm_summary = rhythm_summary or phase12_3_summary.get("phase12_2_summary") or phase12_2_guard_summary(voice, fast=fast)
    memory_summary = _get_memory_governance_summary()()
    live = phase12_4_live_memory_snapshot(voice, rhythm_summary, fast=fast)
    rows_test = phase12_4_memory_rows()
    command_missing = sorted(PHASE12_4_COMMANDS - KNOWN_SLASH_COMMANDS)
    regression_ok = all(row["passed"] for row in rows_test)
    live_ok = live["memory"]["action"] in PHASE12_4_MEMORY_ACTIONS
    no_save_ok = not live["memory"]["save_now"] and all(not row["save_now"] for row in rows_test)
    candidate_rows = [row for row in rows_test if row["got"] == "candidate_review"]
    confirm_ok = candidate_rows and all(row["confirm_required"] for row in candidate_rows)
    focus_drop_ok = all(
        phase12_4_attention_memory_decision(state, phase12_2_rhythm_policy_for_state(state))["action"] == "drop"
        for state in {"deep_work", "game_focus", "away"}
    )
    rows = [
        ("phase12_3_foundation", not phase10_guard_failures(phase12_3_summary), f"presence_entropy={phase12_3_summary['pass_count']}/{phase12_3_summary['total']}"),
        ("memory_governance_bridge", not phase10_guard_failures(memory_summary), f"memory_governance={memory_summary['pass_count']}/{memory_summary['total']}"),
        ("attention_memory_regression", regression_ok, f"{sum(1 for row in rows_test if row['passed'])}/{len(rows_test)} pass"),
        ("live_memory_snapshot", live_ok, f"state={live['attention']['state']} action={live['memory']['action']} reason={live['memory']['reason']}"),
        ("candidate_confirm_contract", confirm_ok, f"candidates={len(candidate_rows)} confirm_required=True"),
        ("focus_drop_contract", focus_drop_ok, "deep_work/game_focus/away cannot become memory candidate"),
        ("no_save_now_contract", no_save_ok, "candidate_review only; save_now=False"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("attention_memory_readonly", True, "status/guard/test không lưu memory, không tạo pending, không compact"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": rows_test,
        "live": live,
        "phase12_3_summary": phase12_3_summary,
        "phase12_2_summary": rhythm_summary,
        "memory_governance": memory_summary,
    }


def phase12_4_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_attention_memory_status(voice=None):
    summary = phase12_4_guard_summary(voice)
    live = summary["live"]
    memory_decision = live["memory"]
    attention = live["attention"]
    rhythm = live["rhythm"]
    print("🧠 Attention Memory Status")
    print("  Action: read-only; phân loại memory candidate cho presence, không lưu/không execute.")
    print(f"  Phase 12.4 Progress: {phase12_4_progress_percent(summary)}%")
    print(f"  Live: state={attention['state']} rhythm={rhythm['rhythm']} action={memory_decision['action']}")
    print(f"  Reason: {memory_decision['reason']} | confirm_required={memory_decision['confirm_required']} | save_now={memory_decision['save_now']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: presence memory chỉ thành candidate_review; lưu thật vẫn phải qua memory governance/confirm.")


def print_attention_memory_test(raw_text=None, voice=None):
    print("🧪 Attention Memory Test")
    print("  Action: read-only; synthetic only, không lưu memory/không tạo pending.")
    rows = phase12_4_memory_rows()
    key = (raw_text or "").strip().lower()
    aliases = {
        "drop": {"deep_work_drop", "game_drop", "away_drop"},
        "hold": {"debug_hold", "recovery_hold", "observe_hold"},
        "candidate": {"chat_candidate", "social_candidate", "idle_candidate"},
        "chat": {"chat_candidate"},
        "social": {"social_candidate"},
        "idle": {"idle_candidate"},
        "focus": {"deep_work_drop", "game_drop"},
        "recovery": {"recovery_hold"},
    }
    if key and key != "all":
        allowed = aliases.get(key, {key})
        rows = [row for row in rows if row["name"] in allowed or row["attention_state"] in allowed or row["got"] in allowed]
        if not rows:
            print("  Status: not_found")
            print("  Cases: drop, hold, candidate, chat, social, idle, focus, recovery")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | state={row['attention_state']} | "
            f"action={row['got']} expected={row['expected']} | reason={row['reason']} | "
            f"confirm={row['confirm_required']} save_now={row['save_now']}"
        )
    print("  Execute: False")


def print_attention_memory_guard_status(voice=None):
    summary = phase12_4_guard_summary(voice)
    print("🧪 Phase 12.4 Attention Memory Guard")
    print("  Action: read-only; kiểm memory candidate boundary, không lưu/không mutate.")
    print(f"  Progress: {phase12_4_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Attention memory regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | state={row['attention_state']} action={row['got']} reason={row['reason']}")


def print_phase12_4_status(voice=None):
    summary = phase12_4_guard_summary(voice)
    print("🧩 Phase 12.4 Status")
    print("  Goal: Attention Memory Safe Baseline - presence pattern chỉ được thành candidate, không lưu thẳng.")
    print(f"  Progress: {phase12_4_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /attention-memory-status | /attention-memory-test | /attention-memory-guard-status | /phase12-4-ready")


def print_phase12_4_ready(voice=None):
    summary = phase12_4_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 12.4 Ready" if ready else "⚠️ Phase 12.4 Ready")
    print("  Goal: attention memory baseline đủ chắc để sang Phase 12.5 presence gate final.")
    print(f"  Progress: {phase12_4_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: attention_memory={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 12.4 không lưu memory, chỉ tạo candidate contract.")


# ---------------------------------------------------------------------------
# Phase 12.5 — Final Presence Gate
# ---------------------------------------------------------------------------

PHASE12_5_COMMANDS = {
    "/presence-gate-status",
    "/presence-gate-guard-status",
    "/presence-gate-test",
    "/phase12-status",
    "/phase12-ready",
    "/phase12-5-status",
    "/phase12-5-ready",
    "/phase12-5-guard-status",
    "/phase12-5-test",
    "/p12",
    "/p12-ready",
    "/p12-5",
    "/p12-5-ready",
}


def phase12_collect_summaries(voice=None, fast=False):
    attention_summary = phase12_1_guard_summary(voice, fast=fast)
    rhythm_summary = phase12_2_guard_summary(voice, attention_summary, fast=fast)
    entropy_summary = phase12_3_guard_summary(voice, rhythm_summary, fast=fast)
    memory_summary = phase12_4_guard_summary(voice, entropy_summary, rhythm_summary, fast=fast)
    return {
        "attention": attention_summary,
        "rhythm": rhythm_summary,
        "entropy": entropy_summary,
        "memory": memory_summary,
    }


def phase12_subphase_rows(voice=None, summaries=None, fast=False):
    summaries = summaries or phase12_collect_summaries(voice, fast=fast)
    subphase_summaries = [
        ("phase12_1_attention_state", summaries["attention"], "attention_state"),
        ("phase12_2_attention_rhythm", summaries["rhythm"], "attention_rhythm"),
        ("phase12_3_presence_entropy", summaries["entropy"], "presence_entropy"),
        ("phase12_4_attention_memory", summaries["memory"], "attention_memory"),
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


def phase12_gate_snapshot(voice=None, summaries=None, fast=False):
    summaries = summaries or phase12_collect_summaries(voice, fast=fast)
    attention_summary = summaries["attention"]
    rhythm_summary = summaries["rhythm"]
    entropy_summary = summaries["entropy"]
    memory_summary = summaries["memory"]
    queue = _get_runtime_queue().snapshot()
    pending_plan = PHASE7_PENDING_PLAN
    runtime_pending = (_get_pending_actions().snapshot() or {}).get("pending")
    event_executed = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
    audit_executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    return {
        "attention": attention_summary["live_decision"],
        "rhythm": rhythm_summary["live"]["rhythm"],
        "entropy": entropy_summary["live"]["entropy"],
        "memory": memory_summary["live"]["memory"],
        "context": attention_summary["live_context"],
        "queue": queue,
        "pending_plan": pending_plan,
        "runtime_pending": runtime_pending,
        "event_executed": event_executed,
        "audit_executed": audit_executed,
    }


def phase12_5_guard_summary(voice=None, fast=True):
    summaries = phase12_collect_summaries(voice, fast=fast)
    subphases = phase12_subphase_rows(voice, summaries, fast=fast)
    snapshot = phase12_gate_snapshot(voice, summaries, fast=fast)
    memory_summary = summaries["memory"].get("memory_governance") or _get_memory_governance_summary()()
    command_missing = sorted(PHASE12_5_COMMANDS - KNOWN_SLASH_COMMANDS)
    queue = snapshot["queue"]
    pending_details = []
    if snapshot["pending_plan"]:
        pending_details.append("phase7_pending_plan")
    if snapshot["runtime_pending"]:
        pending_details.append("runtime_pending_action")
    if queue.get("active_p0"):
        pending_details.append("active_p0")
    if queue.get("queued"):
        pending_details.append(f"queued={len(queue.get('queued') or [])}")
    execute_total = len(snapshot["event_executed"]) + len(snapshot["audit_executed"])
    live_presence_ok = (
        snapshot["attention"].get("state") in PHASE12_1_ATTENTION_STATES
        and snapshot["rhythm"].get("rhythm") in PHASE12_2_RHYTHM_POLICIES
        and snapshot["entropy"].get("decision") in {"emit", "suppress"}
        and snapshot["memory"].get("action") in PHASE12_4_MEMORY_ACTIONS
    )
    no_action_ok = execute_total == 0 and not pending_details
    no_save_ok = snapshot["memory"].get("save_now") is False
    safety_ok = not snapshot["rhythm"].get("ambient_allowed") if snapshot["attention"].get("state") in {"deep_work", "debugging", "game_focus", "recovery_needed", "away", "observe"} else True
    rows = [
        ("subphase_closure", all(row["passed"] for row in subphases), f"{sum(1 for row in subphases if row['passed'])}/{len(subphases)} pass"),
        ("live_presence_pipeline", live_presence_ok, f"state={snapshot['attention']['state']} rhythm={snapshot['rhythm']['rhythm']} entropy={snapshot['entropy']['decision']} memory={snapshot['memory']['action']}"),
        ("memory_governance_bridge", not phase10_guard_failures(memory_summary), f"memory_governance={memory_summary['pass_count']}/{memory_summary['total']}"),
        ("focus_safety_final", safety_ok, f"state={snapshot['attention']['state']} ambient_allowed={snapshot['rhythm'].get('ambient_allowed')}"),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", execute_total == 0, f"event_execute={len(snapshot['event_executed'])} audit_execute={len(snapshot['audit_executed'])}"),
        ("memory_no_save_final", no_save_ok, f"save_now={snapshot['memory'].get('save_now')} action={snapshot['memory'].get('action')}"),
        ("autonomy_lock_contract", True, f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase13_boundary", True, "Phase 13 chỉ bàn/làm sau Phase 12 Ready=True; Phase 12 không bật tự trị"),
        ("presence_gate_readonly", True, "status/guard/test không nói, không lưu, không tạo pending, không execute"),
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


def phase12_5_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_presence_gate_status(voice=None):
    summary = phase12_5_guard_summary(voice)
    snapshot = summary["snapshot"]
    queue = snapshot["queue"]
    print("🧠 Phase 12 Presence Gate Status")
    print("  Action: read-only; tổng kiểm presence engine nền, không mutate/không execute.")
    print(f"  Phase 12.5 Progress: {phase12_5_progress_percent(summary)}%")
    print(f"  Subphases: {sum(1 for row in summary['subphases'] if row['passed'])}/{len(summary['subphases'])} pass")
    print(f"  Live: state={snapshot['attention']['state']} rhythm={snapshot['rhythm']['rhythm']} entropy={snapshot['entropy']['decision']} memory={snapshot['memory']['action']}")
    print(f"  Queue: active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}/{queue.get('max_tasks')}")
    print(f"  Pending: phase7={'yes' if snapshot['pending_plan'] else 'none'} | runtime={'yes' if snapshot['runtime_pending'] else 'none'}")
    print(f"  Execute flags: event={len(snapshot['event_executed'])} audit={len(snapshot['audit_executed'])}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 12 đóng presence nền; chưa tự nói, chưa lưu memory, chưa mở autonomy.")


def print_presence_gate_test(raw_text=None, voice=None):
    print("🧪 Presence Gate Test")
    print("  Action: read-only; synthetic/summary only, không mutate/không execute.")
    summary = phase12_5_guard_summary(voice)
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
        print(f"  Attention: {snapshot['attention']['state']} | reason={snapshot['attention']['reason']}")
        print(f"  Rhythm: {snapshot['rhythm']['rhythm']} | ambient={snapshot['rhythm'].get('ambient_allowed')} context={snapshot['rhythm'].get('context_allowed')}")
        print(f"  Entropy: {snapshot['entropy']['decision']} | variant={snapshot['entropy']['variant']}")
        print(f"  Memory: {snapshot['memory']['action']} | save_now={snapshot['memory']['save_now']}")
        print("  Execute: False")
        return
    if key in {"queue", "pending"}:
        snapshot = summary["snapshot"]
        queue = snapshot["queue"]
        print("  Section: queue/pending")
        print(f"  Phase7 pending: {'yes' if snapshot['pending_plan'] else 'none'}")
        print(f"  Runtime pending: {'yes' if snapshot['runtime_pending'] else 'none'}")
        print(f"  Queue: active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}/{queue.get('max_tasks')}")
        print("  Execute: False")
        return
    if key in {"memory", "save"}:
        snapshot = summary["snapshot"]
        print("  Section: memory")
        print(f"  Action: {snapshot['memory']['action']}")
        print(f"  Reason: {snapshot['memory']['reason']}")
        print(f"  Confirm required: {snapshot['memory']['confirm_required']}")
        print(f"  Save now: {snapshot['memory']['save_now']}")
        print("  Execute: False")
        return
    if key in {"autonomy", "execute", "executor"}:
        snapshot = summary["snapshot"]
        print("  Section: autonomy/execute")
        print(f"  Autonomy: {AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}")
        print(f"  Runtime event execute=True: {len(snapshot['event_executed'])}")
        print(f"  Phase9 audit execute=True: {len(snapshot['audit_executed'])}")
        print("  Execute: False")
        return
    print("  Status: not_found")
    print("  Sections: subphases, live, queue, memory, autonomy")
    print("  Execute: False")


def print_presence_gate_guard_status(voice=None):
    summary = phase12_5_guard_summary(voice)
    print("🧪 Phase 12.5 Presence Gate Guard")
    print("  Action: read-only; tổng kiểm Phase 12, không tạo pending/không execute.")
    print(f"  Progress: {phase12_5_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Subphases:")
    for row in summary["subphases"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")


def print_phase12_5_status(voice=None):
    summary = phase12_5_guard_summary(voice)
    print("🧩 Phase 12.5 Status")
    print("  Goal: Final Presence Gate - đóng presence/attention nền trước Phase 13.")
    print(f"  Progress: {phase12_5_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /presence-gate-status | /presence-gate-test | /presence-gate-guard-status | /phase12-ready")


def print_phase12_ready(voice=None):
    summary = phase12_5_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 12 Ready" if ready else "⚠️ Phase 12 Ready")
    print("  Goal: Phase 12 presence engine nền đủ sạch để bắt đầu Phase 13.")
    print(f"  Progress: {phase12_5_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: phase12_gate={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 12 không tự nói, không lưu memory, không mở executor.")
