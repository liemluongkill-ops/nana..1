"""
Phase 83 — Nana Priority Router.
Tách khỏi main.py (GĐ 3).
"""
from __future__ import annotations

# ── Constants (copied from main.py lines 33336-33362) ─────────────────────────

PHASE83_COMMANDS = {
    "/nana-priority-status",
    "/nana-priority-guard-status",
    "/nana-priority-test",
    "/phase83-status",
    "/phase83-ready",
    "/phase83-guard-status",
    "/phase83-test",
    "/p83",
    "/p83-ready",
}

PHASE83_PRIORITY_ACTIONS = {
    "priority_core_now",
    "priority_game_now",
    "priority_stream_one_liner",
    "priority_companion_reply",
    "priority_hold",
    "priority_block",
}
PHASE83_LANE_BASE_PRIORITY = {
    "core": 100,
    "game_stardew": 80,
    "stream_persona": 45,
    "companion": 35,
}
PHASE83_REQUIRED_COMMANDS = set()

# ── Helpers ────────────────────────────────────────────────────────────────────


def phase83_priority_score(task):
    lane = task.get("lane")
    score = PHASE83_LANE_BASE_PRIORITY.get(lane, 0)
    if task.get("safety"):
        score += 80
    if task.get("foreground"):
        score += 8
    if task.get("deadline") == "immediate":
        score += 18
    if task.get("high_impact"):
        score += 30
    if task.get("boundary_action") in {"boundary_block", "boundary_confirm_strict"}:
        score += 20
    if task.get("stream_action") in {"stream_persona_suppressed", "stream_persona_hold"}:
        score -= 40
    if task.get("voice_busy") and lane == "stream_persona":
        score -= 30
    if task.get("background"):
        score -= 20
    return score


def phase83_build_priority_router(tasks=None):
    tasks = tasks or []
    evaluated = []
    for index, task in enumerate(tasks):
        item = dict(task)
        item["index"] = index
        item["score"] = phase83_priority_score(item)
        item["allowed"] = item.get("boundary_action") not in {"boundary_block"} and item.get("stream_action") not in {"stream_persona_suppressed", "stream_persona_hold", "stream_persona_block"}
        evaluated.append(item)
    blockers = [task for task in evaluated if task.get("boundary_action") == "boundary_block" or task.get("stream_action") == "stream_persona_block"]
    if blockers:
        chosen = sorted(blockers, key=lambda task: (-task["score"], task["index"]))[0]
        return {
            "action": "priority_block",
            "reason": "blocked_or_high_impact_task_preempts_queue",
            "chosen": chosen,
            "tasks": evaluated,
            "keyboard_called": False,
            "dispatch": False,
            "execute": False,
        }
    candidates = [task for task in evaluated if task.get("allowed")]
    if not candidates:
        return {
            "action": "priority_hold",
            "reason": "no_allowed_priority_task_ready",
            "chosen": None,
            "tasks": evaluated,
            "keyboard_called": False,
            "dispatch": False,
            "execute": False,
        }
    chosen = sorted(candidates, key=lambda task: (-task["score"], task["index"]))[0]
    lane = chosen.get("lane")
    if lane == "core":
        action = "priority_core_now"
        reason = "core_safety_or_system_task_first"
    elif lane == "game_stardew":
        action = "priority_game_now"
        reason = "foreground_game_task_before_stream_or_companion"
    elif lane == "stream_persona":
        action = "priority_stream_one_liner"
        reason = "stream_commentary_allowed_after_game_safety"
    else:
        action = "priority_companion_reply"
        reason = "companion_reply_when_no_game_or_core_preempt"
    return {
        "action": action,
        "reason": reason,
        "chosen": chosen,
        "tasks": evaluated,
        "keyboard_called": False,
        "dispatch": False,
        "execute": False,
    }


def phase83_task(lane, name, boundary_action="boundary_allow", stream_action=None, safety=False, foreground=False, deadline="normal", high_impact=False, background=False, voice_busy=False):
    return {
        "lane": lane,
        "name": name,
        "boundary_action": boundary_action,
        "stream_action": stream_action,
        "safety": safety,
        "foreground": foreground,
        "deadline": deadline,
        "high_impact": high_impact,
        "background": background,
        "voice_busy": voice_busy,
    }


def phase83_priority_rows():
    rows_def = [
        ("core_safety_first", phase83_build_priority_router([
            phase83_task("companion", "reply_chat"),
            phase83_task("game_stardew", "farm_step", "boundary_supervised", foreground=True),
            phase83_task("core", "kill_switch", safety=True, deadline="immediate"),
        ]), "priority_core_now", "kill_switch"),
        ("game_before_stream", phase83_build_priority_router([
            phase83_task("stream_persona", "one_liner", stream_action="stream_persona_one_liner_ready", foreground=True),
            phase83_task("game_stardew", "objective_step", "boundary_supervised", foreground=True, deadline="immediate"),
            phase83_task("companion", "small_reply"),
        ]), "priority_game_now", "objective_step"),
        ("stream_after_game_clear", phase83_build_priority_router([
            phase83_task("stream_persona", "one_liner", stream_action="stream_persona_one_liner_ready", foreground=True),
            phase83_task("companion", "small_reply"),
        ]), "priority_stream_one_liner", "one_liner"),
        ("companion_when_idle", phase83_build_priority_router([
            phase83_task("companion", "small_reply"),
        ]), "priority_companion_reply", "small_reply"),
        ("blocked_preempts", phase83_build_priority_router([
            phase83_task("companion", "small_reply"),
            phase83_task("game_stardew", "raw_input", "boundary_block", foreground=True, high_impact=True),
        ]), "priority_block", "raw_input"),
        ("suppressed_stream_holds", phase83_build_priority_router([
            phase83_task("stream_persona", "suppressed_comment", stream_action="stream_persona_suppressed"),
        ]), "priority_hold", None),
        ("confirm_strict_game_preempts_companion", phase83_build_priority_router([
            phase83_task("companion", "small_reply"),
            phase83_task("game_stardew", "sell_shop", "boundary_confirm_strict", foreground=True, high_impact=True),
        ]), "priority_game_now", "sell_shop"),
        ("voice_busy_stream_loses_to_companion", phase83_build_priority_router([
            phase83_task("stream_persona", "one_liner", stream_action="stream_persona_one_liner_ready", foreground=True, voice_busy=True),
            phase83_task("companion", "small_reply"),
        ]), "priority_companion_reply", "small_reply"),
        ("background_companion_loses_to_foreground_game", phase83_build_priority_router([
            phase83_task("companion", "background_memory_note", background=True),
            phase83_task("game_stardew", "bridge_reobserve", "boundary_allow", foreground=True),
        ]), "priority_game_now", "bridge_reobserve"),
    ]
    rows = []
    for name, plan, expected, expected_task in rows_def:
        chosen = plan.get("chosen") or {}
        no_side_effect = plan.get("keyboard_called") is False and plan.get("dispatch") is False and plan.get("execute") is False
        rows.append({
            "name": name,
            "action": plan["action"],
            "expected": expected,
            "passed": plan["action"] == expected and (expected_task is None or chosen.get("name") == expected_task) and no_side_effect,
            "reason": plan["reason"],
            "chosen": chosen.get("name") or "none",
            "lane": chosen.get("lane") or "none",
            "score": chosen.get("score"),
            "dispatch": plan.get("dispatch"),
            "execute": plan.get("execute"),
        })
    return rows


def phase83_live_priority_plan():
    from nana.phases.phase81 import phase81_build_boundary_decision
    from nana.phases.phase82 import phase82_live_stream_plan
    boundary = phase81_build_boundary_decision("companion", "chat.reply", "chat_context", "runtime_ephemeral")
    stream = phase82_live_stream_plan()
    tasks = [
        phase83_task("companion", "chat_reply", boundary.get("action")),
        phase83_task("stream_persona", "stream_commentary", stream_action=stream.get("action"), foreground=True),
    ]
    return phase83_build_priority_router(tasks)


# ── Guard ──────────────────────────────────────────────────────────────────────


def phase83_guard_summary(vts=None, voice=None):
    from nana.main import (
        PHASE9_AUDIT_LOG,
        RUNTIME_EVENT_LOG,
        KNOWN_SLASH_COMMANDS,
        memory_governance_summary,
        runtime_queue,
        phase_progress_percent,
    )
    from nana.phases.phase82 import phase82_guard_summary

    phase82 = phase82_guard_summary(vts, voice)
    live_plan = phase83_live_priority_plan()
    priority_rows = phase83_priority_rows()
    actions = {row["action"] for row in priority_rows}
    command_missing = sorted(PHASE83_REQUIRED_COMMANDS - KNOWN_SLASH_COMMANDS)
    memory_snapshot = memory_governance_summary().get("snapshot") or {}
    queue = runtime_queue.snapshot()
    event_executed = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
    audit_executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    no_side_effect = all(not row["dispatch"] and row["execute"] is False for row in priority_rows)
    rows = [
        ("phase82_foundation", not phase82["failures"], f"nana_stream_persona={phase82['pass_count']}/{phase82['total']}"),
        ("priority_action_taxonomy", PHASE83_PRIORITY_ACTIONS <= actions, f"covered={','.join(sorted(actions))}"),
        ("priority_regression", all(row["passed"] for row in priority_rows), f"{sum(1 for row in priority_rows if row['passed'])}/{len(priority_rows)} pass"),
        ("live_priority_snapshot", live_plan["action"] in PHASE83_PRIORITY_ACTIONS, f"action={live_plan['action']} chosen={(live_plan.get('chosen') or {}).get('name') or 'none'}"),
        ("core_preempt_contract", any(row["name"] == "core_safety_first" and row["passed"] for row in priority_rows), "core safety preempts all lanes"),
        ("game_preempt_contract", all(any(row["name"] == name and row["passed"] for row in priority_rows) for name in {"game_before_stream", "background_companion_loses_to_foreground_game"}), "foreground game preempts stream/companion"),
        ("stream_interleave_contract", any(row["name"] == "stream_after_game_clear" and row["passed"] for row in priority_rows), "stream one-liner can run only after safety/game"),
        ("companion_idle_contract", any(row["name"] == "companion_when_idle" and row["passed"] for row in priority_rows), "companion replies when no higher lane ready"),
        ("blocked_preempt_contract", any(row["name"] == "blocked_preempts" and row["passed"] for row in priority_rows), "blocked task surfaces before action"),
        ("suppressed_stream_hold_contract", any(row["name"] == "suppressed_stream_holds" and row["passed"] for row in priority_rows), "suppressed stream does not run"),
        ("confirm_strict_contract", any(row["name"] == "confirm_strict_game_preempts_companion" and row["passed"] for row in priority_rows), "confirm-strict game action preempts companion reply"),
        ("voice_busy_contract", any(row["name"] == "voice_busy_stream_loses_to_companion" and row["passed"] for row in priority_rows), "voice busy lowers stream priority"),
        ("readonly_contract", no_side_effect, "Phase 83 priority router does not dispatch/input"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'} long={memory_snapshot.get('long_term', 0)}"),
        ("pending_queue_clear", not queue.get("queued") and not queue.get("active_p0"), f"active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}"),
        ("execute_flag_guard", len(event_executed) + len(audit_executed) == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("phase84_boundary", True, "Phase 84 mới runtime state store tách ephemeral/game/stream state"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase82": phase82,
        "live_plan": live_plan,
        "priority_rows": priority_rows,
    }


# ── Print helpers ──────────────────────────────────────────────────────────────


def print_nana_priority_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase83_guard_summary(vts, voice)
    live = summary["live_plan"]
    chosen = live.get("chosen") or {}
    print("🧭 Nana Priority Router Status")
    print("  Action: route core/game/stream/companion priority; status không input.")
    print(f"  Phase 83 Progress: {phase_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} reason={live['reason']} chosen={chosen.get('name') or 'none'} lane={chosen.get('lane') or 'none'}")
    print("  Order: core safety > foreground game > allowed stream one-liner > companion reply > background.")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'fail'} | {detail}")


def print_nana_priority_test(raw_text=None, vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase83_guard_summary(vts, voice)
    rows = summary["priority_rows"]
    arg = (raw_text or "").strip().lower()
    print("🧪 Nana Priority Router Test")
    print("  Action: synthetic lane priority only; không dispatch/không input.")
    if arg in {"live", "state"}:
        live = summary["live_plan"]
        chosen = live.get("chosen") or {}
        print("  Section: live priority")
        print(f"  Action: {live['action']} | reason={live['reason']}")
        print(f"  Chosen: {chosen.get('name') or 'none'} lane={chosen.get('lane') or 'none'} score={chosen.get('score')}")
        print("  Keyboard: False | Dispatch: False | Execute: False")
        return
    aliases = {
        "core": {"core_safety_first"},
        "game": {"game_before_stream", "confirm_strict_game_preempts_companion", "background_companion_loses_to_foreground_game"},
        "stream": {"stream_after_game_clear", "suppressed_stream_holds", "voice_busy_stream_loses_to_companion"},
        "companion": {"companion_when_idle", "voice_busy_stream_loses_to_companion"},
        "block": {"blocked_preempts", "suppressed_stream_holds"},
        "voice": {"voice_busy_stream_loses_to_companion"},
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
            f"chosen={row['chosen']} lane={row['lane']} score={row['score']} dispatch={row['dispatch']} execute={row['execute']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_nana_priority_guard_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase83_guard_summary(vts, voice)
    print("🧪 Phase 83 Nana Priority Guard")
    print("  Action: tổng kiểm lane priority router; guard không input.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Priority regression:")
    for row in summary["priority_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} chosen={row['chosen']} reason={row['reason']}")


def print_phase83_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase83_guard_summary(vts, voice)
    print("🧩 Phase 83 Status")
    print("  Goal: Nana Priority Router - xếp quyền ưu tiên giữa core/game/stream/companion.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print("  Companion Overall: lane-separated priority foundation")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'fail'} | {detail}")
    print("  Commands: /nana-priority-status | /nana-priority-test | /nana-priority-guard-status | /phase83-ready")


def print_phase83_ready(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase83_guard_summary(vts, voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 83 Ready" if ready else "⚠️ Phase 83 Ready")
    print("  Goal: priority router đủ sạch để thêm runtime state store và loop tự nhiên.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print("  Companion Overall: priority-separated lanes")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: nana_priority={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: policy-only; core/game/stream/companion có thứ tự, chưa dispatch.")
