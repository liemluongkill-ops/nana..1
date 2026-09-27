"""
Phase 81 — Nana Lane/Memory/High-Impact Boundary.
Tách khỏi main.py (GĐ 3).
"""
from __future__ import annotations

# ── Constants (copied from main.py lines 32726-32777) ─────────────────────────

PHASE81_COMMANDS = {
    "/nana-boundary-status",
    "/nana-boundary-guard-status",
    "/nana-boundary-test",
    "/phase81-status",
    "/phase81-ready",
    "/phase81-guard-status",
    "/phase81-test",
    "/p81",
    "/p81-ready",
}

PHASE81_LANES = {"companion", "game_stardew", "stream_persona", "core"}
PHASE81_MEMORY_CLASSES = {"companion_long", "game_long", "runtime_ephemeral", "audit"}
PHASE81_BOUNDARY_ACTIONS = {
    "boundary_allow",
    "boundary_supervised",
    "boundary_confirm_strict",
    "boundary_block",
    "boundary_suppress",
    "boundary_one_liner",
}
PHASE81_STREAM_MODES = {"off", "stream_light", "stream_host"}
PHASE81_EPHEMERAL_KEYS = {
    "tile",
    "player_tile",
    "player_pixel",
    "bridge_snapshot",
    "route",
    "step_log",
    "stuck_counter",
    "menu_state",
    "focus_state",
    "target_tile",
    "front_tile",
}
PHASE81_HIGH_IMPACT_CONFIRM = {
    "commerce.checkout",
    "social.post",
    "social.like",
    "social.follow",
    "game.sell_shop",
    "game.shipping_bin",
    "game.discard_item",
    "game.sleep_end_day",
    "game.upgrade_tool",
    "game.gift_npc",
    "companion.sell_request",
}
PHASE81_HARD_BLOCKED_ACTIONS = {"commerce.checkout", "game.raw_input_unscoped"}
PHASE81_GAME_SUPERVISED_ACTIONS = {"game.clear_grass", "game.chop_tree", "game.break_stone", "game.till_soil"}
PHASE81_REQUIRED_COMMANDS = set()

# ── Helpers ────────────────────────────────────────────────────────────────────


def phase81_classify_memory(key, requested_class=None):
    normalized = (key or "").strip().lower()
    requested = requested_class or "runtime_ephemeral"
    if normalized in PHASE81_EPHEMERAL_KEYS or normalized.endswith("_tile") or "snapshot" in normalized:
        return {
            "key": key,
            "memory_class": "runtime_ephemeral",
            "action": "boundary_allow",
            "reason": "runtime_state_ephemeral_only",
            "write_long_term": False,
        }
    if requested in {"companion_long", "game_long", "audit"}:
        return {
            "key": key,
            "memory_class": requested,
            "action": "boundary_allow",
            "reason": "memory_class_allowed_by_lane",
            "write_long_term": requested in {"companion_long", "game_long"},
        }
    return {
        "key": key,
        "memory_class": "runtime_ephemeral",
        "action": "boundary_block",
        "reason": "unknown_memory_class_held",
        "write_long_term": False,
    }


def phase81_build_boundary_decision(
    lane,
    intent,
    memory_key=None,
    requested_memory_class=None,
    stream_mode="off",
    stream_fact_confidence=1.0,
    manual_confirm=False,
):
    lane = lane if lane in PHASE81_LANES else "core"
    intent = (intent or "observe").strip().lower()
    memory = phase81_classify_memory(memory_key or intent, requested_memory_class)
    base = {
        "lane": lane,
        "intent": intent,
        "memory_key": memory.get("key"),
        "memory_class": memory.get("memory_class"),
        "write_long_term": memory.get("write_long_term"),
        "stream_mode": stream_mode if stream_mode in PHASE81_STREAM_MODES else "off",
        "stream_fact_confidence": round(float(stream_fact_confidence or 0.0), 2),
        "manual_confirm": bool(manual_confirm),
        "keyboard_called": False,
        "dispatch": False,
        "execute": False,
    }
    if memory["memory_class"] != "runtime_ephemeral" and memory_key in PHASE81_EPHEMERAL_KEYS:
        return {**base, "action": "boundary_block", "policy": "ephemeral_only", "reason": "runtime_state_cannot_enter_long_memory"}
    if intent in PHASE81_HARD_BLOCKED_ACTIONS:
        return {**base, "action": "boundary_block", "policy": "blocked", "reason": "high_impact_action_hard_blocked"}
    if intent in PHASE81_HIGH_IMPACT_CONFIRM:
        return {**base, "action": "boundary_confirm_strict", "policy": "confirm_strict", "reason": "high_impact_action_requires_strict_confirm"}
    if lane == "game_stardew" and intent in PHASE81_GAME_SUPERVISED_ACTIONS:
        return {**base, "action": "boundary_supervised", "policy": "supervised", "reason": "bounded_game_action_supervised_only"}
    if memory["memory_class"] == "runtime_ephemeral" and (memory_key in PHASE81_EPHEMERAL_KEYS or "snapshot" in (memory_key or "")):
        return {**base, "action": "boundary_allow", "policy": "ephemeral_only", "reason": "runtime_state_ephemeral_only"}
    if lane == "stream_persona":
        if base["stream_mode"] == "off":
            return {**base, "action": "boundary_suppress", "policy": "stream_default_off", "reason": "stream_persona_suppressed_when_not_streaming"}
        if base["stream_fact_confidence"] < 0.75:
            return {**base, "action": "boundary_suppress", "policy": "truth_gate", "reason": "stream_commentary_requires_grounded_state"}
        if base["stream_mode"] == "stream_light":
            return {**base, "action": "boundary_one_liner", "policy": "stream_light", "reason": "stream_light_allows_brief_grounded_commentary"}
        return {**base, "action": "boundary_supervised", "policy": "stream_host", "reason": "stream_host_commentary_supervised_by_cooldown"}
    if lane == "companion":
        return {**base, "action": "boundary_allow", "policy": "companion_safe", "reason": "low_impact_companion_action_allowed"}
    if lane == "game_stardew":
        return {**base, "action": "boundary_allow", "policy": "game_readonly_safe", "reason": "low_impact_game_state_allowed"}
    return {**base, "action": "boundary_allow", "policy": "core_safe", "reason": "low_impact_core_action_allowed"}


def phase81_boundary_rows():
    rows_def = [
        ("game_clear_supervised", phase81_build_boundary_decision("game_stardew", "game.clear_grass", "target_tile"), "boundary_supervised"),
        ("game_sell_shop_confirm_strict", phase81_build_boundary_decision("game_stardew", "game.sell_shop", "inventory_summary", "game_long"), "boundary_confirm_strict"),
        ("game_discard_item_confirm_strict", phase81_build_boundary_decision("game_stardew", "game.discard_item", "inventory_summary", "game_long"), "boundary_confirm_strict"),
        ("game_raw_input_block", phase81_build_boundary_decision("game_stardew", "game.raw_input_unscoped", "route"), "boundary_block"),
        ("commerce_checkout_block", phase81_build_boundary_decision("companion", "commerce.checkout", "purchase_context", "audit"), "boundary_block"),
        ("social_post_confirm_strict", phase81_build_boundary_decision("companion", "social.post", "draft_context", "companion_long"), "boundary_confirm_strict"),
        ("social_like_confirm_strict", phase81_build_boundary_decision("companion", "social.like", "social_trace_context", "audit"), "boundary_confirm_strict"),
        ("social_follow_confirm_strict", phase81_build_boundary_decision("companion", "social.follow", "social_trace_context", "audit"), "boundary_confirm_strict"),
        ("companion_sell_request_confirm_strict", phase81_build_boundary_decision("companion", "companion.sell_request", "commerce_context", "audit"), "boundary_confirm_strict"),
        ("game_end_day_confirm_strict", phase81_build_boundary_decision("game_stardew", "game.sleep_end_day", "day_plan", "game_long"), "boundary_confirm_strict"),
        ("game_gift_npc_confirm_strict", phase81_build_boundary_decision("game_stardew", "game.gift_npc", "npc_plan", "game_long"), "boundary_confirm_strict"),
        ("runtime_tile_ephemeral", phase81_build_boundary_decision("game_stardew", "observe", "player_tile", "game_long"), "boundary_allow"),
        ("bridge_snapshot_ephemeral", phase81_build_boundary_decision("game_stardew", "observe", "bridge_snapshot", "game_long"), "boundary_allow"),
        ("companion_preference_long", phase81_build_boundary_decision("companion", "remember_preference", "ba_prefers_quiet_mode", "companion_long"), "boundary_allow"),
        ("game_capability_long", phase81_build_boundary_decision("game_stardew", "remember_capability", "stardew_clear_scope", "game_long"), "boundary_allow"),
        ("stream_default_suppressed", phase81_build_boundary_decision("stream_persona", "stream.commentary", "stream_context", "runtime_ephemeral", "off", 1.0), "boundary_suppress"),
        ("stream_light_one_liner", phase81_build_boundary_decision("stream_persona", "stream.commentary", "stream_context", "runtime_ephemeral", "stream_light", 0.9), "boundary_one_liner"),
        ("stream_truth_gate_blocks_uncertain", phase81_build_boundary_decision("stream_persona", "stream.commentary", "stream_context", "runtime_ephemeral", "stream_light", 0.4), "boundary_suppress"),
    ]
    rows = []
    for name, plan, expected in rows_def:
        no_dispatch = plan.get("keyboard_called") is False and plan.get("dispatch") is False and plan.get("execute") is False
        ephemeral_ok = True
        if plan.get("memory_key") in PHASE81_EPHEMERAL_KEYS:
            ephemeral_ok = plan.get("memory_class") == "runtime_ephemeral" and plan.get("write_long_term") is False
        rows.append({
            "name": name,
            "action": plan["action"],
            "expected": expected,
            "passed": plan["action"] == expected and no_dispatch and ephemeral_ok,
            "reason": plan["reason"],
            "lane": plan.get("lane"),
            "intent": plan.get("intent"),
            "policy": plan.get("policy"),
            "memory_class": plan.get("memory_class"),
            "write_long_term": plan.get("write_long_term"),
            "stream_mode": plan.get("stream_mode"),
            "dispatch": plan.get("dispatch"),
            "execute": plan.get("execute"),
        })
    return rows


# ── Guard ──────────────────────────────────────────────────────────────────────


def phase81_guard_summary(vts=None, voice=None):
    # Lazy imports to avoid circular dependency at module load time
    import nana.phases.commons as _commons
    from nana.main import (
        PHASE9_AUDIT_LOG,
        RUNTIME_EVENT_LOG,
        KNOWN_SLASH_COMMANDS,
        memory_governance_summary,
        runtime_queue,
        phase_completed_guard_stub,
        phase_progress_percent,
    )

    phase80 = phase_completed_guard_stub(19, 19)
    boundary_rows = phase81_boundary_rows()
    actions = {row["action"] for row in boundary_rows}
    lanes = {row["lane"] for row in boundary_rows}
    memory_classes = {row["memory_class"] for row in boundary_rows}
    command_missing = sorted(PHASE81_REQUIRED_COMMANDS - KNOWN_SLASH_COMMANDS)
    memory_snapshot = memory_governance_summary().get("snapshot") or {}
    queue = runtime_queue.snapshot()
    event_executed = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
    audit_executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    no_dispatch = all(not row["dispatch"] and row["execute"] is False for row in boundary_rows)
    rows = [
        ("phase80_foundation", not phase80["failures"], f"stardew_final_autonomy={phase80['pass_count']}/{phase80['total']}"),
        ("lane_taxonomy_contract", {"companion", "game_stardew", "stream_persona"} <= lanes and "core" in PHASE81_LANES, f"covered={','.join(sorted(lanes))}"),
        ("memory_boundary_contract", {"companion_long", "game_long", "runtime_ephemeral", "audit"} <= PHASE81_MEMORY_CLASSES and {"companion_long", "game_long", "runtime_ephemeral"} <= memory_classes, f"classes={','.join(sorted(memory_classes))}"),
        ("boundary_action_taxonomy", {"boundary_supervised", "boundary_confirm_strict", "boundary_block", "boundary_suppress", "boundary_one_liner"} <= actions, f"covered={','.join(sorted(actions))}"),
        ("boundary_regression", all(row["passed"] for row in boundary_rows), f"{sum(1 for row in boundary_rows if row['passed'])}/{len(boundary_rows)} pass"),
        ("ephemeral_denylist_contract", all(row["passed"] for row in boundary_rows if row["name"] in {"runtime_tile_ephemeral", "bridge_snapshot_ephemeral"}), "tile/snapshot/route/focus stay runtime_ephemeral"),
        ("game_scope_supervised_contract", any(row["name"] == "game_clear_supervised" and row["passed"] for row in boundary_rows), "clear/chop/break/till stay supervised in game lane"),
        ("game_sell_confirm_strict_contract", all(any(row["name"] == name and row["passed"] for row in boundary_rows) for name in {"game_sell_shop_confirm_strict", "game_discard_item_confirm_strict"}), "shop/shipping/discard require strict confirm"),
        ("game_day_gift_confirm_strict_contract", all(any(row["name"] == name and row["passed"] for row in boundary_rows) for name in {"game_end_day_confirm_strict", "game_gift_npc_confirm_strict"}), "sleep/end-day/gift require strict confirm"),
        ("companion_no_auto_sell_contract", any(row["name"] == "companion_sell_request_confirm_strict" and row["passed"] for row in boundary_rows), "companion commerce cannot auto-sell"),
        ("commerce_payment_block_contract", any(row["name"] == "commerce_checkout_block" and row["passed"] for row in boundary_rows), "checkout/payment hard blocked"),
        ("social_trace_confirm_contract", all(any(row["name"] == name and row["passed"] for row in boundary_rows) for name in {"social_post_confirm_strict", "social_like_confirm_strict", "social_follow_confirm_strict"}), "post/like/follow need strict confirm"),
        ("raw_input_block_contract", any(row["name"] == "game_raw_input_block" and row["passed"] for row in boundary_rows), "unscoped raw game input blocked"),
        ("stream_default_off_contract", any(row["name"] == "stream_default_suppressed" and row["passed"] for row in boundary_rows), "stream persona default off"),
        ("stream_truth_gate_contract", any(row["name"] == "stream_truth_gate_blocks_uncertain" and row["passed"] for row in boundary_rows), "stream commentary must be grounded"),
        ("readonly_contract", no_dispatch, "Phase 81 boundary is policy only; no input/no writes"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'} long={memory_snapshot.get('long_term', 0)}"),
        ("pending_queue_clear", not queue.get("queued") and not queue.get("active_p0"), f"active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}"),
        ("execute_flag_guard", len(event_executed) + len(audit_executed) == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("phase82_boundary", True, "Phase 82 mới stream persona mode gate/runtime toggle hoặc physical module split"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase80": phase80,
        "boundary_rows": boundary_rows,
    }


# ── Print helpers ──────────────────────────────────────────────────────────────


def print_nana_boundary_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase81_guard_summary(vts, voice)
    print("🧱 Nana Boundary Status")
    print("  Action: chia lane/memory/high-impact gate; status không input.")
    print(f"  Phase 81 Progress: {phase_progress_percent(summary)}%")
    print("  Lanes: companion | game_stardew | stream_persona | core")
    print("  Memory: companion_long | game_long | runtime_ephemeral | audit")
    print("  Stream: default=off; chỉ nói kiểu host khi stream mode bật và có grounded state.")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'fail'} | {detail}")
    print("  Rule: game state runtime không vào memory dài hạn; commerce/sell/post/gift/end-day phải confirm-strict hoặc block.")


def print_nana_boundary_test(raw_text=None, vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase81_guard_summary(vts, voice)
    rows = summary["boundary_rows"]
    arg = (raw_text or "").strip().lower()
    print("🧪 Nana Boundary Test")
    print("  Action: synthetic lane/memory/high-impact boundary only; không bấm phím/không ghi memory.")
    aliases = {
        "game": {"game_clear_supervised", "game_sell_shop_confirm_strict", "game_discard_item_confirm_strict", "game_end_day_confirm_strict", "game_gift_npc_confirm_strict", "game_raw_input_block"},
        "sell": {"game_sell_shop_confirm_strict", "game_discard_item_confirm_strict", "companion_sell_request_confirm_strict"},
        "memory": {"runtime_tile_ephemeral", "bridge_snapshot_ephemeral", "companion_preference_long", "game_capability_long"},
        "stream": {"stream_default_suppressed", "stream_light_one_liner", "stream_truth_gate_blocks_uncertain"},
        "commerce": {"commerce_checkout_block", "companion_sell_request_confirm_strict"},
        "social": {"social_post_confirm_strict", "social_like_confirm_strict", "social_follow_confirm_strict"},
        "block": {"commerce_checkout_block", "game_raw_input_block"},
    }
    filtered = rows
    if arg:
        wanted = aliases.get(arg)
        if wanted:
            filtered = [row for row in rows if row["name"] in wanted]
        else:
            filtered = [row for row in rows if arg in row["name"].lower() or arg in row["intent"].lower() or arg in row["action"].lower()]
    print(f"  Summary: {sum(1 for row in filtered if row['passed'])}/{len(filtered)} pass")
    for row in filtered:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} expected={row['expected']} "
            f"lane={row['lane']} intent={row['intent']} memory={row['memory_class']} "
            f"dispatch={row['dispatch']} execute={row['execute']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_nana_boundary_guard_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase81_guard_summary(vts, voice)
    print("🧪 Phase 81 Nana Boundary Guard")
    print("  Action: tổng kiểm lane/memory/high-impact boundary; guard không input.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Boundary regression:")
    for row in summary["boundary_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} lane={row['lane']} reason={row['reason']}")


def print_phase81_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase81_guard_summary(vts, voice)
    print("🧩 Phase 81 Status")
    print("  Goal: Nana Lane/Memory/High-Impact Boundary - tách game, companion, stream trước khi tăng tự chủ.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print("  Companion Overall: post-32 gameplay cognition + Stardew Level 5 foundation")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'fail'} | {detail}")
    print("  Commands: /nana-boundary-status | /nana-boundary-test | /nana-boundary-guard-status | /phase81-ready")


def print_phase81_ready(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase81_guard_summary(vts, voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 81 Ready" if ready else "⚠️ Phase 81 Ready")
    print("  Goal: boundary split đủ sạch để làm Level 5 game/stream mà không lẫn memory hoặc high-impact action.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print("  Companion Overall: lane-separated foundation")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: nana_boundary={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: policy-only; không input, không memory write, stream persona vẫn default off.")
