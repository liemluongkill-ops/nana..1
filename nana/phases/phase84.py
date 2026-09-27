"""
Phase 84 — Nana Runtime State Store.
Tách khỏi main.py (GĐ 3).
"""
from __future__ import annotations

# ── Constants (copied from main.py lines 33661-33701) ─────────────────────────

PHASE84_COMMANDS = {
    "/nana-runtime-state-status",
    "/nana-runtime-state-guard-status",
    "/nana-runtime-state-test",
    "/phase84-status",
    "/phase84-ready",
    "/phase84-guard-status",
    "/phase84-test",
    "/p84",
    "/p84-ready",
}

PHASE84_STATE_ACTIONS = {
    "runtime_state_ready",
    "runtime_state_ephemeral",
    "runtime_state_reobserve",
    "runtime_state_block",
    "runtime_state_purged",
}
PHASE84_RUNTIME_SCOPES = {"game_stardew", "stream_persona", "companion", "core"}
PHASE84_STATE_TTLS = {
    "game_stardew": 6.0,
    "stream_persona": 15.0,
    "companion": 20.0,
    "core": 25.0,
}
PHASE84_STATE_KEYS = {
    "tile",
    "player_tile",
    "player_pixel",
    "bridge_snapshot",
    "route",
    "step_log",
    "stream_context",
    "priority_choice",
    "focus_state",
    "menu_state",
    "chat_context",
    "kill_switch_state",
    "farm_capability_note",
    "planner_hint",
}
PHASE84_REQUIRED_COMMANDS = set()

# ── Helpers ────────────────────────────────────────────────────────────────────


def phase84_build_runtime_state_entry(scope, key, age_s=0.0, value_present=True, requested_memory_class="runtime_ephemeral", write_long_term=False):
    scope = scope if scope in PHASE84_RUNTIME_SCOPES else "core"
    key = (key or "").strip()
    age_s = float(age_s or 0.0)
    ttl = PHASE84_STATE_TTLS.get(scope, 10.0)
    from nana.phases.phase81 import phase81_classify_memory
    memory = phase81_classify_memory(key, requested_memory_class)
    base = {
        "scope": scope,
        "key": key,
        "age_s": round(age_s, 2),
        "ttl_s": ttl,
        "value_present": bool(value_present),
        "memory_class": memory.get("memory_class"),
        "write_long_term": bool(write_long_term),
        "keyboard_called": False,
        "dispatch": False,
        "execute": False,
        "file_write": False,
    }
    if write_long_term or memory.get("memory_class") != "runtime_ephemeral":
        return {**base, "action": "runtime_state_block", "reason": "runtime_state_cannot_write_long_memory"}
    if key not in PHASE84_STATE_KEYS:
        from nana.phases.phase81 import PHASE81_EPHEMERAL_KEYS
        if key not in PHASE81_EPHEMERAL_KEYS:
            return {**base, "action": "runtime_state_block", "reason": "runtime_state_key_not_allowlisted"}
    if not value_present:
        return {**base, "action": "runtime_state_reobserve", "reason": "runtime_state_missing_value"}
    if age_s > ttl:
        return {**base, "action": "runtime_state_purged", "reason": "runtime_state_ttl_expired"}
    from nana.phases.phase81 import PHASE81_EPHEMERAL_KEYS
    ephemeral_keys = PHASE81_EPHEMERAL_KEYS | {"bridge_snapshot", "stream_context", "priority_choice", "chat_context", "kill_switch_state"}
    if key in ephemeral_keys:
        return {**base, "action": "runtime_state_ephemeral", "reason": "runtime_state_ephemeral_scoped"}
    return {**base, "action": "runtime_state_ready", "reason": "runtime_state_ready"}


def phase84_runtime_state_rows():
    rows_def = [
        ("bridge_fresh_ephemeral", phase84_build_runtime_state_entry("game_stardew", "bridge_snapshot", 0.4), "runtime_state_ephemeral"),
        ("player_tile_ephemeral", phase84_build_runtime_state_entry("game_stardew", "player_tile", 0.1), "runtime_state_ephemeral"),
        ("route_ephemeral", phase84_build_runtime_state_entry("game_stardew", "route", 1.0), "runtime_state_ephemeral"),
        ("step_log_ephemeral", phase84_build_runtime_state_entry("game_stardew", "step_log", 1.2), "runtime_state_ephemeral"),
        ("stream_context_ephemeral", phase84_build_runtime_state_entry("stream_persona", "stream_context", 8.0), "runtime_state_ephemeral"),
        ("priority_choice_ephemeral", phase84_build_runtime_state_entry("core", "priority_choice", 2.0), "runtime_state_ephemeral"),
        ("chat_context_ephemeral", phase84_build_runtime_state_entry("companion", "chat_context", 20.0), "runtime_state_ephemeral"),
        ("planner_hint_ready", phase84_build_runtime_state_entry("core", "planner_hint", 1.0), "runtime_state_ready"),
        ("bridge_stale_purged", phase84_build_runtime_state_entry("game_stardew", "bridge_snapshot", 8.0), "runtime_state_purged"),
        ("missing_value_reobserve", phase84_build_runtime_state_entry("game_stardew", "front_tile", 0.1, value_present=False), "runtime_state_reobserve"),
        ("long_memory_block", phase84_build_runtime_state_entry("game_stardew", "farm_capability_note", 0.1, requested_memory_class="game_long"), "runtime_state_block"),
        ("write_long_block", phase84_build_runtime_state_entry("stream_persona", "stream_context", 0.1, write_long_term=True), "runtime_state_block"),
        ("unknown_key_block", phase84_build_runtime_state_entry("companion", "random_unscoped_state", 0.1), "runtime_state_block"),
    ]
    rows = []
    for name, plan, expected in rows_def:
        no_side_effect = (
            plan.get("keyboard_called") is False
            and plan.get("dispatch") is False
            and plan.get("execute") is False
            and plan.get("file_write") is False
            and plan.get("write_long_term") is False
        )
        if name in {"long_memory_block", "write_long_block"}:
            no_side_effect = plan.get("action") == "runtime_state_block" and plan.get("execute") is False and plan.get("file_write") is False
        rows.append({
            "name": name,
            "action": plan["action"],
            "expected": expected,
            "passed": plan["action"] == expected and no_side_effect,
            "reason": plan["reason"],
            "scope": plan.get("scope"),
            "key": plan.get("key"),
            "age_s": plan.get("age_s"),
            "ttl_s": plan.get("ttl_s"),
            "memory_class": plan.get("memory_class"),
            "write_long_term": plan.get("write_long_term"),
            "dispatch": plan.get("dispatch"),
            "execute": plan.get("execute"),
        })
    return rows


# ── Guard ──────────────────────────────────────────────────────────────────────


def phase84_guard_summary(vts=None, voice=None):
    from nana.main import (
        PHASE9_AUDIT_LOG,
        RUNTIME_EVENT_LOG,
        KNOWN_SLASH_COMMANDS,
        memory_governance_summary,
        runtime_queue,
        phase_progress_percent,
    )
    from nana.phases.phase83 import phase83_guard_summary

    phase83 = phase83_guard_summary(vts, voice)
    live_plan = {"action": "noop", "key": None, "age_s": None}
    state_rows = phase84_runtime_state_rows()
    actions = {row["action"] for row in state_rows}
    command_missing = sorted(PHASE84_REQUIRED_COMMANDS - KNOWN_SLASH_COMMANDS)
    memory_snapshot = memory_governance_summary().get("snapshot") or {}
    queue = runtime_queue.snapshot()
    event_executed = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
    audit_executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    no_side_effect = all(
        not row["dispatch"] and row["execute"] is False and row["write_long_term"] is False
        for row in state_rows if row["name"] != "write_long_block"
    )
    rows = [
        ("phase83_foundation", not phase83["failures"], f"nana_priority={phase83['pass_count']}/{phase83['total']}"),
        ("runtime_action_taxonomy", PHASE84_STATE_ACTIONS <= actions, f"covered={','.join(sorted(actions))}"),
        ("runtime_state_regression", all(row["passed"] for row in state_rows), f"{sum(1 for row in state_rows if row['passed'])}/{len(state_rows)} pass"),
        ("live_runtime_snapshot", live_plan["action"] in PHASE84_STATE_ACTIONS, f"action={live_plan['action']} key={live_plan.get('key')} age={live_plan.get('age_s')}"),
        ("scope_contract", {"game_stardew", "stream_persona", "companion", "core"} <= {row["scope"] for row in state_rows}, "runtime scopes separated"),
        ("ttl_contract", any(row["name"] == "bridge_stale_purged" and row["passed"] for row in state_rows), f"game ttl={PHASE84_STATE_TTLS['game_stardew']}s"),
        ("missing_reobserve_contract", any(row["name"] == "missing_value_reobserve" and row["passed"] for row in state_rows), "missing runtime state reobserves"),
        ("ephemeral_contract", all(any(row["name"] == name and row["passed"] for row in state_rows) for name in {"bridge_fresh_ephemeral", "player_tile_ephemeral", "route_ephemeral", "step_log_ephemeral"}), "game runtime state stays ephemeral"),
        ("stream_state_contract", any(row["name"] == "stream_context_ephemeral" and row["passed"] for row in state_rows), "stream context runtime only"),
        ("priority_state_contract", any(row["name"] == "priority_choice_ephemeral" and row["passed"] for row in state_rows), "priority choice runtime only"),
        ("long_memory_block_contract", all(any(row["name"] == name and row["passed"] for row in state_rows) for name in {"long_memory_block", "write_long_block"}), "runtime store cannot write long memory"),
        ("allowlist_contract", any(row["name"] == "unknown_key_block" and row["passed"] for row in state_rows), "unknown runtime keys blocked"),
        ("readonly_contract", no_side_effect, "Phase 84 state store contract has no file/memory writes"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'} long={memory_snapshot.get('long_term', 0)}"),
        ("pending_queue_clear", not queue.get("queued") and not queue.get("active_p0"), f"active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}"),
        ("execute_flag_guard", len(event_executed) + len(audit_executed) == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("phase85_boundary", True, "Phase 85 mới command/policy map và Level 5 dev docs"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase83": phase83,
        "live_plan": live_plan,
        "state_rows": state_rows,
    }


# ── Print helpers ──────────────────────────────────────────────────────────────


def print_nana_runtime_state_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase84_guard_summary(vts, voice)
    live = summary["live_plan"]
    print("🗂️ Nana Runtime State Status")
    print("  Action: runtime state store contract; status không ghi file/không ghi memory dài hạn.")
    print(f"  Phase 84 Progress: {phase_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} reason={live['reason']} key={live.get('key')} age={live.get('age_s')}/{live.get('ttl_s')}s")
    print("  Scopes: game_stardew | stream_persona | companion | core")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'fail'} | {detail}")


def print_nana_runtime_state_test(raw_text=None, vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase84_guard_summary(vts, voice)
    rows = summary["state_rows"]
    arg = (raw_text or "").strip().lower()
    print("🧪 Nana Runtime State Test")
    print("  Action: synthetic runtime state contract only; không ghi file/không ghi memory.")
    if arg in {"live", "state"}:
        live = summary["live_plan"]
        print("  Section: live runtime state")
        print(f"  Action: {live['action']} | reason={live['reason']}")
        print(f"  Scope: {live.get('scope')} | key={live.get('key')} | age={live.get('age_s')}/{live.get('ttl_s')}s | memory={live.get('memory_class')}")
        print("  FileWrite: False | Dispatch: False | Execute: False")
        return
    aliases = {
        "game": {"bridge_fresh_ephemeral", "player_tile_ephemeral", "route_ephemeral", "step_log_ephemeral"},
        "stream": {"stream_context_ephemeral"},
        "priority": {"priority_choice_ephemeral"},
        "ttl": {"bridge_stale_purged"},
        "reobserve": {"missing_value_reobserve"},
        "memory": {"long_memory_block", "write_long_block"},
        "block": {"write_long_block", "unknown_key_block"},
    }
    filtered = rows
    if arg:
        wanted = aliases.get(arg)
        if wanted:
            filtered = [row for row in rows if row["name"] in wanted]
        else:
            filtered = [row for row in rows if arg in row["name"].lower() or arg in row["action"].lower() or arg in row["reason"].lower()]
    print(f"  Summary: {sum(1 for row in filtered if row['passed'])}/{len(filtered)} pass")
    for row in filtered:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} expected={row['expected']} "
            f"scope={row['scope']} key={row['key']} age={row['age_s']}/{row['ttl_s']} memory={row['memory_class']} "
            f"write_long={row['write_long_term']} dispatch={row['dispatch']} execute={row['execute']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_nana_runtime_state_guard_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase84_guard_summary(vts, voice)
    print("🧪 Phase 84 Nana Runtime State Guard")
    print("  Action: tổng kiểm runtime state store contract; guard không ghi file/input.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Runtime state regression:")
    for row in summary["state_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} scope={row['scope']} key={row['key']} reason={row['reason']}")


def print_phase84_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase84_guard_summary(vts, voice)
    print("🧩 Phase 84 Status")
    print("  Goal: Nana Runtime State Store - tách ephemeral game/stream/priority state khỏi memory dài hạn.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print("  Companion Overall: runtime state separated")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'fail'} | {detail}")
    print("  Commands: /nana-runtime-state-status | /nana-runtime-state-test | /nana-runtime-state-guard-status | /phase84-ready")


def print_phase84_ready(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase84_guard_summary(vts, voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 84 Ready" if ready else "⚠️ Phase 84 Ready")
    print("  Goal: runtime state store contract đủ sạch để planner dùng state mà không làm ngu memory.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print("  Companion Overall: ephemeral state isolated")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: nana_runtime_state={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: policy-only; runtime state có TTL, no file write, no long-memory write.")
