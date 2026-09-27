"""
Phase 85 — Nana Command/Policy Map + Level 5 Roadmap.
Tách khỏi main.py (GĐ 3).
"""
from __future__ import annotations

# ── Constants (copied from main.py lines 33915-33963) ─────────────────────────

PHASE85_COMMANDS = {
    "/nana-policy-map-status",
    "/nana-policy-map-guard-status",
    "/nana-policy-map-test",
    "/nana-level5-map",
    "/phase85-status",
    "/phase85-ready",
    "/phase85-guard-status",
    "/phase85-test",
    "/p85",
    "/p85-ready",
}

PHASE85_MAP_ACTIONS = {
    "policy_map_ready",
    "policy_map_hold",
    "policy_map_block",
}
PHASE85_REQUIRED_COMMANDS = set()

PHASE85_COMMAND_POLICY_MAP = {
    "/nana-boundary-status": {"lane": "core", "mode": "readonly", "policy": "allow"},
    "/nana-boundary-test": {"lane": "core", "mode": "readonly", "policy": "allow"},
    "/nana-stream-mode": {"lane": "stream_persona", "mode": "runtime_toggle", "policy": "allow"},
    "/nana-stream-test": {"lane": "stream_persona", "mode": "readonly", "policy": "allow"},
    "/nana-priority-test": {"lane": "core", "mode": "readonly", "policy": "allow"},
    "/nana-runtime-state-test": {"lane": "core", "mode": "readonly", "policy": "allow"},
    "/stardew-final-test": {"lane": "game_stardew", "mode": "readonly", "policy": "allow"},
    "/stardew-objective-confirm": {"lane": "game_stardew", "mode": "record_only", "policy": "confirm"},
    "/stardew-objective-step-confirm": {"lane": "game_stardew", "mode": "single_step_record", "policy": "confirm"},
    "/stardew-auto-run-confirm": {"lane": "game_stardew", "mode": "bounded_run", "policy": "confirm"},
    "/stardew-tool-confirm": {"lane": "game_stardew", "mode": "tool_input", "policy": "confirm_strict"},
    "/stardew-real-step-confirm": {"lane": "game_stardew", "mode": "raw_input", "policy": "confirm_strict"},
    "/action-confirm": {"lane": "companion", "mode": "external_action", "policy": "confirm_strict"},
    "/draft-confirm": {"lane": "companion", "mode": "social_post", "policy": "confirm_strict"},
    "/memory-confirm": {"lane": "companion", "mode": "long_memory_write", "policy": "confirm"},
    "/memory-status": {"lane": "companion", "mode": "readonly", "policy": "allow"},
    "/stardew-future-mine-combat": {"lane": "game_stardew", "mode": "future_scope", "policy": "hold"},
}

PHASE85_LEVEL5_BRANCHES = [
    {"phase": "86-90", "branch": "natural_farm_planner", "lane": "game_stardew", "policy": "dry_run_then_confirm", "scope": "clear/chop/break/till route choices"},
    {"phase": "91-95", "branch": "bounded_farm_loop", "lane": "game_stardew", "policy": "confirm_each_cycle", "scope": "1-2 cycle objective loop"},
    {"phase": "96-100", "branch": "crop_daily_routine", "lane": "game_stardew", "policy": "confirm_high_impact", "scope": "water/till/plant minimal routine"},
    {"phase": "101-105", "branch": "inventory_shipping_safety", "lane": "game_stardew", "policy": "confirm_strict", "scope": "shipping/sell/discard/chest holds"},
    {"phase": "106-110", "branch": "time_energy_end_day", "lane": "game_stardew", "policy": "confirm_strict", "scope": "energy/time/sleep/end-day"},
    {"phase": "111-115", "branch": "controlled_expansions", "lane": "game_stardew", "policy": "hold_by_default", "scope": "NPC/mine/fishing/forage future branches"},
    {"phase": "116-120", "branch": "stream_persona_hardening", "lane": "stream_persona", "policy": "default_off_grounded", "scope": "stream host style without chatter spam"},
]

# ── Helpers ────────────────────────────────────────────────────────────────────


def phase85_map_command_policy(command):
    info = PHASE85_COMMAND_POLICY_MAP.get(command)
    if not info:
        return {
            "command": command,
            "lane": "core",
            "mode": "unknown",
            "policy": "block",
            "action": "policy_map_block",
            "reason": "command_not_in_policy_map",
            "dispatch": False,
            "execute": False,
        }
    policy = info["policy"]
    if policy in {"allow", "confirm", "confirm_strict"}:
        action = "policy_map_ready"
        reason = "command_policy_mapped"
    elif policy == "hold":
        action = "policy_map_hold"
        reason = "future_scope_policy_holds_action"
    else:
        action = "policy_map_hold"
        reason = "command_policy_holds_action"
    return {
        "command": command,
        "lane": info["lane"],
        "mode": info["mode"],
        "policy": policy,
        "action": action,
        "reason": reason,
        "dispatch": False,
        "execute": False,
    }


def phase85_policy_map_rows():
    cases = [
        ("boundary_readonly_mapped", "/nana-boundary-test", "policy_map_ready", "allow"),
        ("stream_toggle_mapped", "/nana-stream-mode", "policy_map_ready", "allow"),
        ("runtime_readonly_mapped", "/nana-runtime-state-test", "policy_map_ready", "allow"),
        ("game_confirm_mapped", "/stardew-objective-confirm", "policy_map_ready", "confirm"),
        ("game_single_step_mapped", "/stardew-objective-step-confirm", "policy_map_ready", "confirm"),
        ("game_raw_confirm_strict_mapped", "/stardew-real-step-confirm", "policy_map_ready", "confirm_strict"),
        ("tool_confirm_strict_mapped", "/stardew-tool-confirm", "policy_map_ready", "confirm_strict"),
        ("companion_action_confirm_strict_mapped", "/action-confirm", "policy_map_ready", "confirm_strict"),
        ("social_confirm_strict_mapped", "/draft-confirm", "policy_map_ready", "confirm_strict"),
        ("memory_confirm_mapped", "/memory-confirm", "policy_map_ready", "confirm"),
        ("future_scope_hold", "/stardew-future-mine-combat", "policy_map_hold", "hold"),
        ("unknown_command_block", "/unknown-danger-command", "policy_map_block", "block"),
    ]
    rows = []
    for name, command, expected_action, expected_policy in cases:
        plan = phase85_map_command_policy(command)
        no_side_effect = plan.get("dispatch") is False and plan.get("execute") is False
        rows.append({
            "name": name,
            "command": command,
            "action": plan["action"],
            "expected": expected_action,
            "policy": plan["policy"],
            "expected_policy": expected_policy,
            "passed": plan["action"] == expected_action and plan["policy"] == expected_policy and no_side_effect,
            "reason": plan["reason"],
            "lane": plan["lane"],
            "mode": plan["mode"],
            "dispatch": plan["dispatch"],
            "execute": plan["execute"],
        })
    return rows


def phase85_level5_rows():
    from nana.phases.phase81 import PHASE81_LANES
    rows = []
    for item in PHASE85_LEVEL5_BRANCHES:
        lane_ok = item["lane"] in PHASE81_LANES
        policy_ok = item["policy"] in {"dry_run_then_confirm", "confirm_each_cycle", "confirm_high_impact", "confirm_strict", "hold_by_default", "default_off_grounded"}
        rows.append({
            "name": item["branch"],
            "phase": item["phase"],
            "lane": item["lane"],
            "policy": item["policy"],
            "scope": item["scope"],
            "action": "policy_map_ready" if lane_ok and policy_ok else "policy_map_hold",
            "passed": lane_ok and policy_ok,
            "reason": "level5_branch_mapped" if lane_ok and policy_ok else "level5_branch_mapping_incomplete",
        })
    return rows


# ── Guard ──────────────────────────────────────────────────────────────────────


def phase85_guard_summary(vts=None, voice=None):
    from nana.main import (
        PHASE9_AUDIT_LOG,
        RUNTIME_EVENT_LOG,
        KNOWN_SLASH_COMMANDS,
        memory_governance_summary,
        runtime_queue,
        phase_progress_percent,
    )
    from nana.phases.phase84 import phase84_guard_summary

    phase84 = phase84_guard_summary(vts, voice)
    policy_rows = phase85_policy_map_rows()
    level_rows = phase85_level5_rows()
    actions = {row["action"] for row in policy_rows} | {row["action"] for row in level_rows}
    command_missing = sorted(PHASE85_REQUIRED_COMMANDS - KNOWN_SLASH_COMMANDS)
    memory_snapshot = memory_governance_summary().get("snapshot") or {}
    queue = runtime_queue.snapshot()
    event_executed = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
    audit_executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    no_side_effect = all(not row.get("dispatch", False) and row.get("execute", False) is False for row in policy_rows)
    mapped_commands_known = all(
        row["command"] in KNOWN_SLASH_COMMANDS
        or row["command"].startswith("/unknown")
        or row["command"].startswith("/stardew-future-")
        for row in policy_rows
    )
    rows = [
        ("phase84_foundation", not phase84["failures"], f"nana_runtime_state={phase84['pass_count']}/{phase84['total']}"),
        ("policy_action_taxonomy", PHASE85_MAP_ACTIONS <= actions, f"covered={','.join(sorted(actions))}"),
        ("policy_map_regression", all(row["passed"] for row in policy_rows), f"{sum(1 for row in policy_rows if row['passed'])}/{len(policy_rows)} pass"),
        ("level5_map_regression", all(row["passed"] for row in level_rows), f"{sum(1 for row in level_rows if row['passed'])}/{len(level_rows)} pass"),
        ("command_known_contract", mapped_commands_known, "mapped commands exist or are explicit unknown block probes"),
        ("readonly_command_contract", all(any(row["name"] == name and row["passed"] for row in policy_rows) for name in {"boundary_readonly_mapped", "runtime_readonly_mapped"}), "readonly commands mapped allow-only"),
        ("game_confirm_contract", all(any(row["name"] == name and row["passed"] for row in policy_rows) for name in {"game_confirm_mapped", "game_single_step_mapped"}), "game confirm commands mapped"),
        ("raw_input_strict_contract", all(any(row["name"] == name and row["passed"] for row in policy_rows) for name in {"game_raw_confirm_strict_mapped", "tool_confirm_strict_mapped"}), "raw/tool input mapped confirm_strict"),
        ("companion_high_impact_contract", all(any(row["name"] == name and row["passed"] for row in policy_rows) for name in {"companion_action_confirm_strict_mapped", "social_confirm_strict_mapped"}), "companion external/social actions confirm_strict"),
        ("memory_confirm_contract", any(row["name"] == "memory_confirm_mapped" and row["passed"] for row in policy_rows), "long memory writes still confirm"),
        ("future_scope_hold_contract", any(row["name"] == "future_scope_hold" and row["passed"] for row in policy_rows), "future branches hold by default"),
        ("unknown_block_contract", any(row["name"] == "unknown_command_block" and row["passed"] for row in policy_rows), "unknown commands block by policy map"),
        ("level5_branch_contract", len(level_rows) == 7 and all(row["passed"] for row in level_rows), "Level 5 branch map covers 86-120"),
        ("stream_policy_contract", any(row["name"] == "stream_persona_hardening" and row["passed"] for row in level_rows), "stream persona remains default_off_grounded"),
        ("readonly_contract", no_side_effect, "Phase 85 policy map does not dispatch/input"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'} long={memory_snapshot.get('long_term', 0)}"),
        ("pending_queue_clear", not queue.get("queued") and not queue.get("active_p0"), f"active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}"),
        ("execute_flag_guard", len(event_executed) + len(audit_executed) == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("phase86_boundary", True, "Phase 86 mới natural farm planner dry-run dựa trên map này"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase84": phase84,
        "policy_rows": policy_rows,
        "level_rows": level_rows,
    }


# ── Print helpers ──────────────────────────────────────────────────────────────


def print_nana_policy_map_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase85_guard_summary(vts, voice)
    print("🗺️ Nana Policy Map Status")
    print("  Action: command/policy/Level 5 map; status không input.")
    print(f"  Phase 85 Progress: {phase_progress_percent(summary)}%")
    print("  Map: lane -> command -> policy -> Level 5 branch")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'fail'} | {detail}")


def print_nana_policy_map_test(raw_text=None, vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase85_guard_summary(vts, voice)
    rows = summary["policy_rows"]
    arg = (raw_text or "").strip().lower()
    print("🧪 Nana Policy Map Test")
    print("  Action: synthetic command/policy map only; không dispatch/không input.")
    if arg in {"level5", "roadmap", "branches"}:
        rows2 = summary["level_rows"]
        print(f"  Summary: {sum(1 for row in rows2 if row['passed'])}/{len(rows2)} pass")
        for row in rows2:
            print(f"  {'pass' if row['passed'] else 'fail'} | {row['phase']} | {row['name']} | lane={row['lane']} policy={row['policy']} scope={row['scope']} | reason={row['reason']}")
        print("  Execute: False")
        return
    aliases = {
        "readonly": {"boundary_readonly_mapped", "runtime_readonly_mapped"},
        "game": {"game_confirm_mapped", "game_single_step_mapped", "game_raw_confirm_strict_mapped", "tool_confirm_strict_mapped"},
        "companion": {"companion_action_confirm_strict_mapped", "social_confirm_strict_mapped", "memory_confirm_mapped"},
        "strict": {"game_raw_confirm_strict_mapped", "tool_confirm_strict_mapped", "companion_action_confirm_strict_mapped", "social_confirm_strict_mapped"},
        "unknown": {"unknown_command_block"},
        "stream": {"stream_toggle_mapped"},
        "memory": {"memory_confirm_mapped"},
        "future": {"future_scope_hold"},
    }
    filtered = rows
    if arg:
        wanted = aliases.get(arg)
        if wanted:
            filtered = [row for row in rows if row["name"] in wanted]
        else:
            filtered = [row for row in rows if arg in row["name"].lower() or arg in row["command"].lower() or arg in row["policy"].lower()]
    print(f"  Summary: {sum(1 for row in filtered if row['passed'])}/{len(filtered)} pass")
    for row in filtered:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | command={row['command']} "
            f"lane={row['lane']} mode={row['mode']} policy={row['policy']} expected={row['expected_policy']} "
            f"dispatch={row['dispatch']} execute={row['execute']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_nana_level5_map(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase85_guard_summary(vts, voice)
    print("🎮 Nana Game Level 5 Map")
    print("  Action: Level 5 branch roadmap; status không input.")
    for row in summary["level_rows"]:
        print(f"  Phase {row['phase']} | {row['name']} | lane={row['lane']} | policy={row['policy']}")
        print(f"    Scope: {row['scope']}")


def print_nana_policy_map_guard_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase85_guard_summary(vts, voice)
    print("🧪 Phase 85 Nana Policy Map Guard")
    print("  Action: tổng kiểm command/policy/Level 5 map; guard không input.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Policy regression:")
    for row in summary["policy_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | command={row['command']} policy={row['policy']} reason={row['reason']}")
    print("  Level 5 branches:")
    for row in summary["level_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['phase']} | {row['name']} | policy={row['policy']}")


def print_phase85_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase85_guard_summary(vts, voice)
    print("🧩 Phase 85 Status")
    print("  Goal: Nana Command/Policy Map - bản đồ quyền cho Level 5 game + stream.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print("  Companion Overall: mapped policy foundation")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'fail'} | {detail}")
    print("  Commands: /nana-policy-map-status | /nana-policy-map-test | /nana-level5-map | /phase85-ready")


def print_phase85_ready(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase85_guard_summary(vts, voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 85 Ready" if ready else "⚠️ Phase 85 Ready")
    print("  Goal: policy map đủ sạch để sang natural farm planner dry-run.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print("  Companion Overall: Level 5 map ready")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: nana_policy_map={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: policy-only; Level 5 branches mapped, chưa dispatch.")
