"""
Phase 20 — Voice/Expression Dry-Run Layer + Subphases 20.1–20.5.
DRY-RUN phase: chỉ mô phỏng, không phát lời/expression thật.
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


PHASE20_1_COMMANDS = {
    "/voice-binding-status",
    "/voice-binding-guard-status",
    "/voice-binding-test",
    "/phase20-1-status",
    "/phase20-1-ready",
    "/phase20-1-guard-status",
    "/phase20-1-test",
    "/p20-1",
    "/p20-1-ready",
}

PHASE20_1_BINDING_ACTIONS = {"bind_preview", "block_binding", "hold_binding", "suppress_binding"}
PHASE20_1_CHANNELS = {"tts", "vts_expression", "mouth_loop"}
PHASE20_1_EXPRESSION_ALLOWLIST = {"mỉm cười", "nhẹ", "trung tính", "vui nhẹ"}

PHASE20_2_COMMANDS = {
    "/voice-dispatch-status",
    "/voice-dispatch-guard-status",
    "/voice-dispatch-test",
    "/phase20-2-status",
    "/phase20-2-ready",
    "/phase20-2-guard-status",
    "/phase20-2-test",
    "/p20-2",
    "/p20-2-ready",
}

PHASE20_2_DISPATCH_ACTIONS = {"block_dispatch", "dispatch_dry_run", "hold_dispatch", "suppress_dispatch"}

PHASE20_3_COMMANDS = {
    "/expression-dispatch-status",
    "/expression-dispatch-guard-status",
    "/expression-dispatch-test",
    "/phase20-3-status",
    "/phase20-3-ready",
    "/phase20-3-guard-status",
    "/phase20-3-test",
    "/p20-3",
    "/p20-3-ready",
}

PHASE20_3_EXPRESSION_ACTIONS = {"block_expression", "expression_dry_run", "hold_expression", "suppress_expression"}
PHASE20_3_EXPRESSION_ALLOWLIST = {"脸红", "星星眼", "爱心眼", "键盘", "瞌睡", "创口贴", "抱小熊", "变Q"}
PHASE20_3_SERIOUS_MARKERS = {
    "khong on",
    "ko on",
    "nguy hiem",
    "nhay cam",
    "xin chia buon",
    "cang that",
    "that bai",
}

PHASE20_4_COMMANDS = {
    "/live-speech-safety-status",
    "/live-speech-safety-guard-status",
    "/live-speech-safety-test",
    "/phase20-4-status",
    "/phase20-4-ready",
    "/phase20-4-guard-status",
    "/phase20-4-test",
    "/p20-4",
    "/p20-4-ready",
}

PHASE20_4_LIVE_ACTIONS = {"block_live_speech", "hold_live_speech", "live_dry_run_ready", "suppress_live_speech"}

PHASE20_5_COMMANDS = {
    "/voice-gate-status",
    "/voice-gate-guard-status",
    "/voice-gate-test",
    "/phase20-status",
    "/phase20-ready",
    "/phase20-5-status",
    "/phase20-5-ready",
    "/phase20-5-guard-status",
    "/phase20-5-test",
    "/p20",
    "/p20-ready",
    "/p20-5",
    "/p20-5-ready",
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
# PHASE 20.1 — Voice/Expression Binding Baseline
# ─────────────────────────────────────────────────────────────────

def phase20_1_voice_snapshot(voice=None):
    if voice is not None and hasattr(voice, "snapshot"):
        try:
            snap = voice.snapshot() or {}
            snap["provided"] = True
            return snap
        except Exception as exc:
            return {"provided": True, "error": str(exc)}
    return {
        "provided": False,
        "status": "not_provided",
        "listening": False,
        "speaking": False,
        "queue_size": 0,
        "queue_maxsize": 0,
        "worker_alive": False,
    }


def phase20_1_binding_decision(live_gate, voice_state=None):
    from nana.phases.phase16 import phase16_4_reflection_safety_review

    live_gate = live_gate or {}
    voice_state = voice_state or {}
    gate = live_gate.get("action") or "suppress_live_speech"
    text = (live_gate.get("output_text") or "").strip()
    review = phase16_4_reflection_safety_review(text or "Dạ Ba.", "grounded_reflection")
    if gate == "block_live" or review["status"] == "block":
        action = "block_binding"
        reason = "live_or_safety_blocks_binding"
        packet_text = ""
    elif gate == "hold_live" or review["status"] == "hold":
        action = "hold_binding"
        reason = "live_or_safety_holds_binding"
        packet_text = ""
    elif gate == "suppress_live":
        action = "suppress_binding"
        reason = "live_gate_suppresses_binding"
        packet_text = ""
    elif gate == "ready_for_voice_binding" and text:
        action = "bind_preview"
        reason = "voice_binding_packet_ready"
        packet_text = text
    else:
        action = "hold_binding"
        reason = "empty_or_unknown_binding_holds"
        packet_text = ""
    expression = "nhẹ" if action == "bind_preview" else "trung tính"
    return {
        "action": action,
        "reason": reason,
        "live_gate": gate,
        "text": packet_text,
        "visible": bool(packet_text),
        "voice_state": voice_state.get("status", "not_provided"),
        "channels": sorted(PHASE20_1_CHANNELS) if action == "bind_preview" else [],
        "expression": expression,
        "safety_status": review["status"],
        "safety_issues": review["issues"],
        "bind_ready": action == "bind_preview",
        "speech_now": False,
        "voice_say": False,
        "tts_call": False,
        "vts_trigger": False,
        "expression_trigger": False,
        "mouth_loop_trigger": False,
        "model_call": False,
        "rewrite": False,
        "memory_write": False,
        "routine_create": False,
        "execute": False,
    }


def phase20_1_binding_rows():
    cases = [
        ("ready_binding", {"action": "ready_for_voice_binding", "output_text": "Dạ Ba."}, "bind_preview"),
        ("suppress_binding", {"action": "suppress_live", "output_text": ""}, "suppress_binding"),
        ("hold_binding", {"action": "hold_live", "output_text": ""}, "hold_binding"),
        ("block_binding", {"action": "block_live", "output_text": ""}, "block_binding"),
        ("unsafe_text_block", {"action": "ready_for_voice_binding", "output_text": "Con có ý thức thật rồi."}, "block_binding"),
        ("empty_ready_hold", {"action": "ready_for_voice_binding", "output_text": ""}, "hold_binding"),
    ]
    rows = []
    for name, live_gate, expected in cases:
        result = phase20_1_binding_decision(live_gate, {"status": "idle"})
        no_call = (
            result["speech_now"] is False
            and result["voice_say"] is False
            and result["tts_call"] is False
            and result["vts_trigger"] is False
            and result["expression_trigger"] is False
            and result["mouth_loop_trigger"] is False
            and result["model_call"] is False
            and result["rewrite"] is False
            and result["memory_write"] is False
            and result["routine_create"] is False
            and result["execute"] is False
        )
        channel_ok = result["action"] != "bind_preview" or set(result["channels"]) == PHASE20_1_CHANNELS
        leak_ok = result["action"] == "bind_preview" or not result["text"]
        expression_ok = result["expression"] in PHASE20_1_EXPRESSION_ALLOWLIST
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_call and channel_ok and leak_ok and expression_ok,
            "got": result["action"],
            "expected": expected,
            "live_gate": live_gate.get("action"),
            "visible": result["visible"],
            "channels": result["channels"],
            "expression": result["expression"],
            "reason": result["reason"],
            "safety": result["safety_status"],
        })
    return rows


def phase20_1_guard_summary(voice=None):
    from nana.phases.phase10 import phase10_guard_failures
    from nana.phases.phase19 import phase19_5_guard_summary

    phase19 = phase19_5_guard_summary(voice)
    voice_state = phase20_1_voice_snapshot(voice)
    live = phase20_1_binding_decision(phase19["live"], voice_state)
    test_rows = phase20_1_binding_rows()
    command_missing = sorted(PHASE20_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
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
    no_real_call = (
        live["speech_now"] is False
        and live["voice_say"] is False
        and live["tts_call"] is False
        and live["vts_trigger"] is False
        and live["expression_trigger"] is False
        and live["mouth_loop_trigger"] is False
        and live["model_call"] is False
        and live["rewrite"] is False
        and live["memory_write"] is False
        and live["routine_create"] is False
        and live["execute"] is False
    )
    no_leak_ok = live["action"] == "bind_preview" or not live["text"]
    channel_contract = live["action"] != "bind_preview" or set(live["channels"]) == PHASE20_1_CHANNELS
    rows = [
        ("phase19_foundation", not phase10_guard_failures(phase19), f"phase19_gate={phase19['pass_count']}/{phase19['total']}"),
        ("binding_action_taxonomy", PHASE20_1_BINDING_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("binding_channel_taxonomy", PHASE20_1_CHANNELS == {"tts", "vts_expression", "mouth_loop"}, f"channels={','.join(sorted(PHASE20_1_CHANNELS))}"),
        ("voice_binding_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_binding_snapshot", live["action"] in PHASE20_1_BINDING_ACTIONS, f"gate={live['live_gate']} action={live['action']} bind_ready={live['bind_ready']}"),
        ("voice_snapshot_contract", "provided" in voice_state and isinstance(voice_state.get("speaking", False), bool), f"provided={voice_state.get('provided')} status={voice_state.get('status', 'none')} speaking={voice_state.get('speaking', False)}"),
        ("suppress_hold_block_no_packet", all((row["got"] == "bind_preview") or not row["visible"] for row in test_rows), "non-bind rows emit no text"),
        ("bind_preview_packet_contract", any(row["name"] == "ready_binding" and row["passed"] and row["visible"] for row in test_rows), "ready binding creates preview packet only"),
        ("unsafe_text_block_contract", any(row["name"] == "unsafe_text_block" and row["passed"] for row in test_rows), "unsafe voice text blocks binding"),
        ("expression_allowlist_contract", live["expression"] in PHASE20_1_EXPRESSION_ALLOWLIST, f"expression={live['expression']}"),
        ("no_real_voice_side_effects", no_real_call, f"speech_now={live['speech_now']} voice_say={live['voice_say']} tts={live['tts_call']} vts={live['vts_trigger']} expression={live['expression_trigger']} execute={live['execute']}"),
        ("no_leak_unless_bind_preview", no_leak_ok, f"action={live['action']} visible={live['visible']}"),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", len(event_executed) + len(audit_executed) == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'} long={memory_snapshot.get('long_term', 0)}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase20_2_boundary", True, "Phase 20.2 chỉ bàn/làm sau Phase 20.1 Ready=True; Phase 20.1 chưa gọi voice/VTube thật"),
        ("voice_binding_readonly", True, "status/guard/test không gọi voice.say, TTS, VTube expression hoặc mouth loop"),
    ]
    if not channel_contract:
        rows.append(("live_channel_contract", False, f"channels={','.join(live['channels']) if live['channels'] else 'none'}"))
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase19": phase19,
        "test_rows": test_rows,
        "live": live,
        "voice_state": voice_state,
        "queue": queue,
    }


def phase20_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_voice_binding_status(voice=None):
    summary = phase20_1_guard_summary(voice)
    live = summary["live"]
    voice_state = summary["voice_state"]
    print("🔊 Voice Binding Status")
    print("  Action: read-only; dựng voice/expression binding packet, chưa phát lời thật.")
    print(f"  Phase 20.1 Progress: {phase20_1_progress_percent(summary)}%")
    print(f"  Live: gate={live['live_gate']} | action={live['action']} | bind_ready={live['bind_ready']} | visible={live['visible']}")
    print(f"  Voice: provided={voice_state.get('provided')} status={voice_state.get('status', 'none')} speaking={voice_state.get('speaking', False)}")
    print(f"  Packet: channels={','.join(live['channels']) if live['channels'] else 'none'} | expression={live['expression']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 20.1 chỉ bind preview; chưa gọi voice.say/TTS/VTube.")


def print_voice_binding_test(raw_text=None, voice=None):
    print("🧪 Voice Binding Test")
    print("  Action: read-only; synthetic binding only, không gọi voice/TTS/VTube.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"ready_binding"},
        "bind": {"ready_binding"},
        "suppress": {"suppress_binding"},
        "hold": {"hold_binding", "empty_ready_hold"},
        "block": {"block_binding", "unsafe_text_block"},
        "unsafe": {"unsafe_text_block"},
        "empty": {"empty_ready_hold"},
    }
    rows = phase20_1_binding_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, suppress, hold, block, unsafe, empty")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        channels = ",".join(row["channels"]) if row["channels"] else "none"
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | channels={channels} | expression={row['expression']} | reason={row['reason']}")
    print("  Execute: False")


def print_voice_binding_guard_status(voice=None):
    summary = phase20_1_guard_summary(voice)
    print("🧪 Phase 20.1 Voice Binding Guard")
    print("  Action: read-only; kiểm voice/expression binding, không gọi TTS/VTube/execute.")
    print(f"  Progress: {phase20_1_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Binding regression:")
    for row in summary["test_rows"]:
        channels = ",".join(row["channels"]) if row["channels"] else "none"
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} channels={channels} reason={row['reason']}")


def print_phase20_1_status(voice=None):
    summary = phase20_1_guard_summary(voice)
    print("🧩 Phase 20.1 Status")
    print("  Goal: Voice/Expression Binding Baseline - nối live reply sang packet voice ở mức dry-run.")
    print(f"  Progress: {phase20_1_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /voice-binding-status | /voice-binding-test | /voice-binding-guard-status | /phase20-1-ready")


def print_phase20_1_ready(voice=None):
    summary = phase20_1_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 20.1 Ready" if ready else "⚠️ Phase 20.1 Ready")
    print("  Goal: voice binding baseline đủ chắc để sang Phase 20.2 voice dispatch gate.")
    print(f"  Progress: {phase20_1_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: voice_binding={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 20.1 chưa gọi voice.say/TTS/VTube thật.")


# ─────────────────────────────────────────────────────────────────
# PHASE 20.2 — Voice Dispatch Gate (Dry-Run)
# ─────────────────────────────────────────────────────────────────

def phase20_2_voice_capacity(voice_state):
    voice_state = voice_state or {}
    queue_size = int(voice_state.get("queue_size") or 0)
    queue_maxsize = int(voice_state.get("queue_maxsize") or 0)
    if queue_maxsize <= 0:
        return True
    return queue_size < queue_maxsize


def phase20_2_dispatch_decision(binding_live, voice_state=None):
    binding_live = binding_live or {}
    voice_state = voice_state or {}
    binding_action = binding_live.get("action") or "suppress_binding"
    text = (binding_live.get("text") or "").strip()
    speaking = bool(voice_state.get("speaking"))
    queue_has_room = phase20_2_voice_capacity(voice_state)
    if binding_action == "block_binding":
        action = "block_dispatch"
        reason = "binding_blocks_voice_dispatch"
        payload = ""
    elif binding_action == "hold_binding":
        action = "hold_dispatch"
        reason = "binding_holds_voice_dispatch"
        payload = ""
    elif binding_action == "suppress_binding":
        action = "suppress_dispatch"
        reason = "binding_suppresses_voice_dispatch"
        payload = ""
    elif binding_action == "bind_preview" and not text:
        action = "hold_dispatch"
        reason = "empty_voice_payload_holds"
        payload = ""
    elif binding_action == "bind_preview" and speaking:
        action = "hold_dispatch"
        reason = "voice_currently_speaking_holds"
        payload = ""
    elif binding_action == "bind_preview" and not queue_has_room:
        action = "hold_dispatch"
        reason = "voice_queue_full_holds"
        payload = ""
    elif binding_action == "bind_preview":
        action = "dispatch_dry_run"
        reason = "voice_dispatch_dry_run_ready"
        payload = text
    else:
        action = "hold_dispatch"
        reason = "unknown_binding_holds_dispatch"
        payload = ""
    return {
        "action": action,
        "reason": reason,
        "binding_action": binding_action,
        "payload": payload,
        "has_payload": bool(payload),
        "visible": bool(payload),
        "speaking": speaking,
        "queue_size": int(voice_state.get("queue_size") or 0),
        "queue_maxsize": int(voice_state.get("queue_maxsize") or 0),
        "queue_has_room": queue_has_room,
        "voice_say": False,
        "tts_call": False,
        "lipsync_play": False,
        "vts_trigger": False,
        "expression_trigger": False,
        "history_write": False,
        "model_call": False,
        "rewrite": False,
        "memory_write": False,
        "routine_create": False,
        "execute": False,
    }


def phase20_2_dispatch_rows():
    idle_voice = {"speaking": False, "queue_size": 0, "queue_maxsize": 3}
    busy_voice = {"speaking": True, "queue_size": 0, "queue_maxsize": 3}
    full_voice = {"speaking": False, "queue_size": 3, "queue_maxsize": 3}
    cases = [
        ("dispatch_ready", {"action": "bind_preview", "text": "Dạ Ba."}, idle_voice, "dispatch_dry_run"),
        ("binding_suppress", {"action": "suppress_binding", "text": ""}, idle_voice, "suppress_dispatch"),
        ("binding_hold", {"action": "hold_binding", "text": ""}, idle_voice, "hold_dispatch"),
        ("binding_block", {"action": "block_binding", "text": ""}, idle_voice, "block_dispatch"),
        ("empty_payload_hold", {"action": "bind_preview", "text": ""}, idle_voice, "hold_dispatch"),
        ("speaking_hold", {"action": "bind_preview", "text": "Dạ Ba."}, busy_voice, "hold_dispatch"),
        ("queue_full_hold", {"action": "bind_preview", "text": "Dạ Ba."}, full_voice, "hold_dispatch"),
    ]
    rows = []
    for name, binding_live, voice_state, expected in cases:
        result = phase20_2_dispatch_decision(binding_live, voice_state)
        no_call = (
            result["voice_say"] is False
            and result["tts_call"] is False
            and result["lipsync_play"] is False
            and result["vts_trigger"] is False
            and result["expression_trigger"] is False
            and result["history_write"] is False
            and result["model_call"] is False
            and result["rewrite"] is False
            and result["memory_write"] is False
            and result["routine_create"] is False
            and result["execute"] is False
        )
        leak_ok = result["action"] == "dispatch_dry_run" or not result["payload"]
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_call and leak_ok,
            "got": result["action"],
            "expected": expected,
            "binding_action": result["binding_action"],
            "payload": result["has_payload"],
            "queue": f"{result['queue_size']}/{result['queue_maxsize']}",
            "speaking": result["speaking"],
            "reason": result["reason"],
        })
    return rows


def phase20_2_guard_summary(voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    binding_summary = phase20_1_guard_summary(voice)
    voice_state = binding_summary["voice_state"]
    live = phase20_2_dispatch_decision(binding_summary["live"], voice_state)
    test_rows = phase20_2_dispatch_rows()
    command_missing = sorted(PHASE20_2_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
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
    no_real_call = (
        live["voice_say"] is False
        and live["tts_call"] is False
        and live["lipsync_play"] is False
        and live["vts_trigger"] is False
        and live["expression_trigger"] is False
        and live["history_write"] is False
        and live["model_call"] is False
        and live["rewrite"] is False
        and live["memory_write"] is False
        and live["routine_create"] is False
        and live["execute"] is False
    )
    no_leak_ok = live["action"] == "dispatch_dry_run" or not live["payload"]
    queue_contract = isinstance(live["queue_size"], int) and isinstance(live["queue_maxsize"], int)
    rows = [
        ("phase20_1_foundation", not phase10_guard_failures(binding_summary), f"voice_binding={binding_summary['pass_count']}/{binding_summary['total']}"),
        ("dispatch_action_taxonomy", PHASE20_2_DISPATCH_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("voice_dispatch_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_dispatch_snapshot", live["action"] in PHASE20_2_DISPATCH_ACTIONS, f"binding={live['binding_action']} action={live['action']} payload={live['has_payload']}"),
        ("voice_queue_contract", queue_contract, f"speaking={live['speaking']} queue={live['queue_size']}/{live['queue_maxsize']} room={live['queue_has_room']}"),
        ("dispatch_ready_contract", any(row["name"] == "dispatch_ready" and row["passed"] and row["payload"] for row in test_rows), "bind preview can become dry-run dispatch"),
        ("suppress_hold_block_no_payload", all((row["got"] == "dispatch_dry_run") or not row["payload"] for row in test_rows), "non-dispatch rows emit no payload"),
        ("speaking_hold_contract", any(row["name"] == "speaking_hold" and row["passed"] for row in test_rows), "speaking voice holds dispatch"),
        ("queue_full_hold_contract", any(row["name"] == "queue_full_hold" and row["passed"] for row in test_rows), "full voice queue holds dispatch"),
        ("no_real_voice_side_effects", no_real_call, f"voice_say={live['voice_say']} tts={live['tts_call']} lipsync={live['lipsync_play']} vts={live['vts_trigger']} execute={live['execute']}"),
        ("no_leak_unless_dispatch_dry_run", no_leak_ok, f"action={live['action']} payload={live['has_payload']}"),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", len(event_executed) + len(audit_executed) == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'} long={memory_snapshot.get('long_term', 0)}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase20_3_boundary", True, "Phase 20.3 chỉ bàn/làm sau Phase 20.2 Ready=True; Phase 20.2 chưa gọi voice.say/TTS thật"),
        ("voice_dispatch_readonly", True, "status/guard/test không enqueue voice thật, không gọi TTS/lipsync/VTube"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "binding": binding_summary,
        "test_rows": test_rows,
        "live": live,
        "voice_state": voice_state,
        "queue": queue,
    }


def phase20_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_voice_dispatch_status(voice=None):
    summary = phase20_2_guard_summary(voice)
    live = summary["live"]
    print("🔈 Voice Dispatch Status")
    print("  Action: read-only; kiểm dispatch packet, chưa gọi voice.say/TTS.")
    print(f"  Phase 20.2 Progress: {phase20_2_progress_percent(summary)}%")
    print(f"  Live: binding={live['binding_action']} | action={live['action']} | payload={live['has_payload']}")
    print(f"  Voice: speaking={live['speaking']} queue={live['queue_size']}/{live['queue_maxsize']} room={live['queue_has_room']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 20.2 chỉ dry-run dispatch; chưa enqueue voice thật.")


def print_voice_dispatch_test(raw_text=None, voice=None):
    print("🧪 Voice Dispatch Test")
    print("  Action: read-only; synthetic dispatch only, không gọi voice/TTS.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"dispatch_ready"},
        "dispatch": {"dispatch_ready"},
        "suppress": {"binding_suppress"},
        "hold": {"binding_hold", "empty_payload_hold", "speaking_hold", "queue_full_hold"},
        "block": {"binding_block"},
        "speaking": {"speaking_hold"},
        "queue": {"queue_full_hold"},
        "empty": {"empty_payload_hold"},
    }
    rows = phase20_2_dispatch_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, suppress, hold, block, speaking, queue, empty")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | payload={row['payload']} | queue={row['queue']} speaking={row['speaking']} | reason={row['reason']}")
    print("  Execute: False")


def print_voice_dispatch_guard_status(voice=None):
    summary = phase20_2_guard_summary(voice)
    print("🧪 Phase 20.2 Voice Dispatch Guard")
    print("  Action: read-only; kiểm voice dispatch dry-run, không gọi TTS/VTube/execute.")
    print(f"  Progress: {phase20_2_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Dispatch regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} payload={row['payload']} reason={row['reason']}")


def print_phase20_2_status(voice=None):
    summary = phase20_2_guard_summary(voice)
    print("🧩 Phase 20.2 Status")
    print("  Goal: Voice Dispatch Gate - kiểm packet/queue trước khi phát thật.")
    print(f"  Progress: {phase20_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /voice-dispatch-status | /voice-dispatch-test | /voice-dispatch-guard-status | /phase20-2-ready")


def print_phase20_2_ready(voice=None):
    summary = phase20_2_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 20.2 Ready" if ready else "⚠️ Phase 20.2 Ready")
    print("  Goal: voice dispatch gate đủ chắc để sang Phase 20.3 VTube expression dispatch.")
    print(f"  Progress: {phase20_2_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: voice_dispatch={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 20.2 chưa enqueue voice/TTS thật.")


# ─────────────────────────────────────────────────────────────────
# PHASE 20.3 — VTube Expression Dispatch (Dry-Run)
# ─────────────────────────────────────────────────────────────────

def phase20_3_expression_for_text(text):
    from nana.phases.phase14 import phase14_3_fold_text

    folded = phase14_3_fold_text(text or "")
    if any(marker in folded for marker in PHASE20_3_SERIOUS_MARKERS):
        return None, "serious_text_holds_expression"
    if any(marker in folded for marker in ["code", "debug", "terminal", "cmd", "python"]):
        return "键盘", "coding_expression_preview"
    if any(marker in folded for marker in ["met", "ngu", "buon ngu"]):
        return "瞌睡", "low_energy_expression_preview"
    if any(marker in folded for marker in ["xin loi", "loi", "recovery"]):
        return "创口贴", "recovery_expression_preview"
    if any(marker in folded for marker in ["yeu", "thuong", "da ba", "nhe", "nana"]):
        return "脸红", "warm_expression_preview"
    return "星星眼", "default_light_expression_preview"


def phase20_3_expression_decision(dispatch_live, vts_connected=True, recent_expression=False):
    dispatch_live = dispatch_live or {}
    action = dispatch_live.get("action") or "suppress_dispatch"
    payload = (dispatch_live.get("payload") or "").strip()
    if action == "block_dispatch":
        result_action = "block_expression"
        reason = "dispatch_blocks_expression"
        hotkey = None
    elif action == "hold_dispatch":
        result_action = "hold_expression"
        reason = "dispatch_holds_expression"
        hotkey = None
    elif action == "suppress_dispatch":
        result_action = "suppress_expression"
        reason = "dispatch_suppresses_expression"
        hotkey = None
    elif action == "dispatch_dry_run" and not payload:
        result_action = "hold_expression"
        reason = "empty_expression_payload_holds"
        hotkey = None
    elif action == "dispatch_dry_run" and recent_expression:
        result_action = "suppress_expression"
        reason = "expression_cooldown_suppresses"
        hotkey = None
    elif action == "dispatch_dry_run" and not vts_connected:
        result_action = "hold_expression"
        reason = "vts_not_ready_holds_expression"
        hotkey = None
    elif action == "dispatch_dry_run":
        hotkey, reason = phase20_3_expression_for_text(payload)
        if hotkey:
            result_action = "expression_dry_run"
        else:
            result_action = "hold_expression"
    else:
        result_action = "hold_expression"
        reason = "unknown_dispatch_holds_expression"
        hotkey = None
    return {
        "action": result_action,
        "reason": reason,
        "dispatch_action": action,
        "payload": payload if result_action == "expression_dry_run" else "",
        "visible": result_action == "expression_dry_run",
        "hotkey": hotkey,
        "vts_connected": bool(vts_connected),
        "recent_expression": bool(recent_expression),
        "expression_ready": result_action == "expression_dry_run",
        "trigger_expression": False,
        "vts_request": False,
        "voice_say": False,
        "tts_call": False,
        "lipsync_play": False,
        "history_write": False,
        "model_call": False,
        "rewrite": False,
        "memory_write": False,
        "execute": False,
    }


def phase20_3_expression_rows():
    ready = {"action": "dispatch_dry_run", "payload": "Dạ Ba."}
    cases = [
        ("expression_ready", ready, True, False, "expression_dry_run"),
        ("dispatch_suppress", {"action": "suppress_dispatch", "payload": ""}, True, False, "suppress_expression"),
        ("dispatch_hold", {"action": "hold_dispatch", "payload": ""}, True, False, "hold_expression"),
        ("dispatch_block", {"action": "block_dispatch", "payload": ""}, True, False, "block_expression"),
        ("recent_cooldown", ready, True, True, "suppress_expression"),
        ("vts_not_ready", ready, False, False, "hold_expression"),
        ("serious_payload_hold", {"action": "dispatch_dry_run", "payload": "Không ổn, nguy hiểm."}, True, False, "hold_expression"),
        ("coding_expression", {"action": "dispatch_dry_run", "payload": "Debug Python xong rồi Ba."}, True, False, "expression_dry_run"),
    ]
    rows = []
    for name, dispatch_live, vts_connected, recent_expression, expected in cases:
        result = phase20_3_expression_decision(dispatch_live, vts_connected, recent_expression)
        no_call = (
            result["trigger_expression"] is False
            and result["vts_request"] is False
            and result["voice_say"] is False
            and result["tts_call"] is False
            and result["lipsync_play"] is False
            and result["history_write"] is False
            and result["model_call"] is False
            and result["rewrite"] is False
            and result["memory_write"] is False
            and result["execute"] is False
        )
        hotkey_ok = result["hotkey"] is None or result["hotkey"] in PHASE20_3_EXPRESSION_ALLOWLIST
        leak_ok = result["action"] == "expression_dry_run" or not result["payload"]
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_call and hotkey_ok and leak_ok,
            "got": result["action"],
            "expected": expected,
            "dispatch_action": dispatch_live.get("action"),
            "hotkey": result["hotkey"] or "none",
            "visible": result["visible"],
            "reason": result["reason"],
        })
    return rows


def phase20_3_vts_snapshot(vts=None):
    if vts is None:
        return {"provided": False, "connected": False, "last_error": None}
    return {
        "provided": True,
        "connected": bool(getattr(vts, "connected", False)),
        "last_error": str(getattr(vts, "last_error", "") or ""),
    }


def phase20_3_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    dispatch_summary = phase20_2_guard_summary(voice)
    vts_state = phase20_3_vts_snapshot(vts)
    live = phase20_3_expression_decision(dispatch_summary["live"], vts_state["connected"], False)
    test_rows = phase20_3_expression_rows()
    command_missing = sorted(PHASE20_3_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
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
    no_real_call = (
        live["trigger_expression"] is False
        and live["vts_request"] is False
        and live["voice_say"] is False
        and live["tts_call"] is False
        and live["lipsync_play"] is False
        and live["history_write"] is False
        and live["model_call"] is False
        and live["rewrite"] is False
        and live["memory_write"] is False
        and live["execute"] is False
    )
    hotkey_ok = live["hotkey"] is None or live["hotkey"] in PHASE20_3_EXPRESSION_ALLOWLIST
    no_leak_ok = live["action"] == "expression_dry_run" or not live["payload"]
    rows = [
        ("phase20_2_foundation", not phase10_guard_failures(dispatch_summary), f"voice_dispatch={dispatch_summary['pass_count']}/{dispatch_summary['total']}"),
        ("expression_action_taxonomy", PHASE20_3_EXPRESSION_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("expression_allowlist", hotkey_ok, f"allowed={','.join(sorted(PHASE20_3_EXPRESSION_ALLOWLIST))}"),
        ("expression_dispatch_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_expression_snapshot", live["action"] in PHASE20_3_EXPRESSION_ACTIONS, f"dispatch={live['dispatch_action']} action={live['action']} hotkey={live['hotkey'] or 'none'}"),
        ("vts_snapshot_contract", "connected" in vts_state and isinstance(vts_state["connected"], bool), f"provided={vts_state['provided']} connected={vts_state['connected']}"),
        ("expression_ready_contract", any(row["name"] == "expression_ready" and row["passed"] for row in test_rows), "dispatch payload can create expression dry-run"),
        ("cooldown_suppression_contract", any(row["name"] == "recent_cooldown" and row["passed"] for row in test_rows), "recent expression suppresses"),
        ("vts_not_ready_hold_contract", any(row["name"] == "vts_not_ready" and row["passed"] for row in test_rows), "VTS not ready holds expression"),
        ("serious_payload_hold_contract", any(row["name"] == "serious_payload_hold" and row["passed"] for row in test_rows), "serious text holds expression"),
        ("no_real_expression_side_effects", no_real_call, f"trigger={live['trigger_expression']} vts_request={live['vts_request']} tts={live['tts_call']} execute={live['execute']}"),
        ("no_leak_unless_expression_dry_run", no_leak_ok, f"action={live['action']} visible={live['visible']}"),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", len(event_executed) + len(audit_executed) == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'} long={memory_snapshot.get('long_term', 0)}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase20_4_boundary", True, "Phase 20.4 chỉ bàn/làm sau Phase 20.3 Ready=True; Phase 20.3 chưa trigger VTube thật"),
        ("expression_dispatch_readonly", True, "status/guard/test không gọi trigger_expression hoặc vts.request"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "dispatch": dispatch_summary,
        "test_rows": test_rows,
        "live": live,
        "vts_state": vts_state,
        "queue": queue,
    }


def phase20_3_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_expression_dispatch_status(vts=None, voice=None):
    summary = phase20_3_guard_summary(vts, voice)
    live = summary["live"]
    vts_state = summary["vts_state"]
    print("🎭 Expression Dispatch Status")
    print("  Action: read-only; chọn expression packet, chưa trigger VTube thật.")
    print(f"  Phase 20.3 Progress: {phase20_3_progress_percent(summary)}%")
    print(f"  Live: dispatch={live['dispatch_action']} | action={live['action']} | hotkey={live['hotkey'] or 'none'} | visible={live['visible']}")
    print(f"  VTS: provided={vts_state['provided']} connected={vts_state['connected']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 20.3 chỉ expression dry-run; chưa gọi trigger_expression/vts.request.")


def print_expression_dispatch_test(raw_text=None, vts=None, voice=None):
    print("🧪 Expression Dispatch Test")
    print("  Action: read-only; synthetic expression only, không gọi VTube.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"expression_ready"},
        "expression": {"expression_ready", "coding_expression"},
        "suppress": {"dispatch_suppress", "recent_cooldown"},
        "hold": {"dispatch_hold", "vts_not_ready", "serious_payload_hold"},
        "block": {"dispatch_block"},
        "cooldown": {"recent_cooldown"},
        "vts": {"vts_not_ready"},
        "serious": {"serious_payload_hold"},
        "coding": {"coding_expression"},
    }
    rows = phase20_3_expression_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, suppress, hold, block, cooldown, vts, serious, coding")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | hotkey={row['hotkey']} | reason={row['reason']}")
    print("  Execute: False")


def print_expression_dispatch_guard_status(vts=None, voice=None):
    summary = phase20_3_guard_summary(vts, voice)
    print("🧪 Phase 20.3 Expression Dispatch Guard")
    print("  Action: read-only; kiểm expression dispatch dry-run, không trigger VTube/execute.")
    print(f"  Progress: {phase20_3_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Expression regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} hotkey={row['hotkey']} reason={row['reason']}")


def print_phase20_3_status(vts=None, voice=None):
    summary = phase20_3_guard_summary(vts, voice)
    print("🧩 Phase 20.3 Status")
    print("  Goal: VTube Expression Dispatch - chọn expression an toàn ở mức dry-run.")
    print(f"  Progress: {phase20_3_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /expression-dispatch-status | /expression-dispatch-test | /expression-dispatch-guard-status | /phase20-3-ready")


def print_phase20_3_ready(vts=None, voice=None):
    summary = phase20_3_guard_summary(vts, voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 20.3 Ready" if ready else "⚠️ Phase 20.3 Ready")
    print("  Goal: expression dispatch gate đủ chắc để sang Phase 20.4 live speech safety.")
    print(f"  Progress: {phase20_3_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: expression_dispatch={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 20.3 chưa trigger VTube expression thật.")


# ─────────────────────────────────────────────────────────────────
# PHASE 20.4 — Live Speech Safety (Dry-Run)
# ─────────────────────────────────────────────────────────────────

def phase20_4_live_safety_decision(voice_dispatch, expression_dispatch):
    voice_dispatch = voice_dispatch or {}
    expression_dispatch = expression_dispatch or {}
    voice_action = voice_dispatch.get("action") or "suppress_dispatch"
    expression_action = expression_dispatch.get("action") or "suppress_expression"
    payload = (voice_dispatch.get("payload") or "").strip()
    hotkey = expression_dispatch.get("hotkey")
    if voice_action == "block_dispatch" or expression_action == "block_expression":
        action = "block_live_speech"
        reason = "voice_or_expression_blocks_live"
        live_payload = ""
        live_hotkey = None
    elif voice_action == "hold_dispatch" or expression_action == "hold_expression":
        action = "hold_live_speech"
        reason = "voice_or_expression_holds_live"
        live_payload = ""
        live_hotkey = None
    elif voice_action == "suppress_dispatch" or expression_action == "suppress_expression":
        action = "suppress_live_speech"
        reason = "voice_or_expression_suppresses_live"
        live_payload = ""
        live_hotkey = None
    elif voice_action == "dispatch_dry_run" and expression_action == "expression_dry_run" and payload and hotkey:
        action = "live_dry_run_ready"
        reason = "live_speech_packet_ready"
        live_payload = payload
        live_hotkey = hotkey
    else:
        action = "hold_live_speech"
        reason = "incomplete_live_packet_holds"
        live_payload = ""
        live_hotkey = None
    return {
        "action": action,
        "reason": reason,
        "voice_action": voice_action,
        "expression_action": expression_action,
        "payload": live_payload,
        "hotkey": live_hotkey,
        "visible": action == "live_dry_run_ready",
        "live_ready": action == "live_dry_run_ready",
        "voice_say": False,
        "tts_call": False,
        "lipsync_play": False,
        "trigger_expression": False,
        "vts_request": False,
        "history_write": False,
        "model_call": False,
        "rewrite": False,
        "memory_write": False,
        "routine_create": False,
        "execute": False,
    }


def phase20_4_live_safety_rows():
    ready_voice = {"action": "dispatch_dry_run", "payload": "Dạ Ba."}
    ready_expression = {"action": "expression_dry_run", "hotkey": "脸红"}
    cases = [
        ("live_ready", ready_voice, ready_expression, "live_dry_run_ready"),
        ("voice_suppress", {"action": "suppress_dispatch", "payload": ""}, ready_expression, "suppress_live_speech"),
        ("expression_suppress", ready_voice, {"action": "suppress_expression", "hotkey": None}, "suppress_live_speech"),
        ("voice_hold", {"action": "hold_dispatch", "payload": ""}, ready_expression, "hold_live_speech"),
        ("expression_hold", ready_voice, {"action": "hold_expression", "hotkey": None}, "hold_live_speech"),
        ("voice_block", {"action": "block_dispatch", "payload": ""}, ready_expression, "block_live_speech"),
        ("expression_block", ready_voice, {"action": "block_expression", "hotkey": None}, "block_live_speech"),
        ("missing_hotkey_hold", ready_voice, {"action": "expression_dry_run", "hotkey": None}, "hold_live_speech"),
        ("empty_payload_hold", {"action": "dispatch_dry_run", "payload": ""}, ready_expression, "hold_live_speech"),
    ]
    rows = []
    for name, voice_live, expression_live, expected in cases:
        result = phase20_4_live_safety_decision(voice_live, expression_live)
        no_call = (
            result["voice_say"] is False
            and result["tts_call"] is False
            and result["lipsync_play"] is False
            and result["trigger_expression"] is False
            and result["vts_request"] is False
            and result["history_write"] is False
            and result["model_call"] is False
            and result["rewrite"] is False
            and result["memory_write"] is False
            and result["routine_create"] is False
            and result["execute"] is False
        )
        leak_ok = result["action"] == "live_dry_run_ready" or (not result["payload"] and not result["hotkey"])
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_call and leak_ok,
            "got": result["action"],
            "expected": expected,
            "voice_action": result["voice_action"],
            "expression_action": result["expression_action"],
            "visible": result["visible"],
            "hotkey": result["hotkey"] or "none",
            "reason": result["reason"],
        })
    return rows


def phase20_4_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    expression_summary = phase20_3_guard_summary(vts, voice)
    dispatch_summary = expression_summary["dispatch"]
    live = phase20_4_live_safety_decision(dispatch_summary["live"], expression_summary["live"])
    test_rows = phase20_4_live_safety_rows()
    command_missing = sorted(PHASE20_4_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
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
    no_real_call = (
        live["voice_say"] is False
        and live["tts_call"] is False
        and live["lipsync_play"] is False
        and live["trigger_expression"] is False
        and live["vts_request"] is False
        and live["history_write"] is False
        and live["model_call"] is False
        and live["rewrite"] is False
        and live["memory_write"] is False
        and live["routine_create"] is False
        and live["execute"] is False
    )
    no_leak_ok = live["action"] == "live_dry_run_ready" or (not live["payload"] and not live["hotkey"])
    rows = [
        ("phase20_3_foundation", not phase10_guard_failures(expression_summary), f"expression_dispatch={expression_summary['pass_count']}/{expression_summary['total']}"),
        ("voice_dispatch_bridge", not phase10_guard_failures(dispatch_summary), f"voice_dispatch={dispatch_summary['pass_count']}/{dispatch_summary['total']}"),
        ("live_action_taxonomy", PHASE20_4_LIVE_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("live_speech_safety_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_safety_snapshot", live["action"] in PHASE20_4_LIVE_ACTIONS, f"voice={live['voice_action']} expression={live['expression_action']} action={live['action']}"),
        ("ready_requires_voice_and_expression", any(row["name"] == "live_ready" and row["passed"] for row in test_rows), "live ready requires payload and hotkey"),
        ("suppress_bridge_contract", all(any(row["name"] == name and row["passed"] for row in test_rows) for name in ["voice_suppress", "expression_suppress"]), "voice/expression suppress preserved"),
        ("hold_bridge_contract", all(any(row["name"] == name and row["passed"] for row in test_rows) for name in ["voice_hold", "expression_hold", "missing_hotkey_hold", "empty_payload_hold"]), "hold/incomplete packets hold"),
        ("block_bridge_contract", all(any(row["name"] == name and row["passed"] for row in test_rows) for name in ["voice_block", "expression_block"]), "voice/expression block preserved"),
        ("no_real_live_side_effects", no_real_call, f"voice_say={live['voice_say']} trigger_expression={live['trigger_expression']} vts_request={live['vts_request']} execute={live['execute']}"),
        ("no_leak_unless_live_ready", no_leak_ok, f"action={live['action']} visible={live['visible']}"),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", len(event_executed) + len(audit_executed) == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'} long={memory_snapshot.get('long_term', 0)}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase20_5_boundary", True, "Phase 20.5 chỉ bàn/làm sau Phase 20.4 Ready=True; Phase 20.4 chưa phát live thật"),
        ("live_speech_safety_readonly", True, "status/guard/test không gọi voice.say, TTS, trigger_expression hoặc vts.request"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "expression": expression_summary,
        "dispatch": dispatch_summary,
        "test_rows": test_rows,
        "live": live,
        "queue": queue,
    }


def phase20_4_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_live_speech_safety_status(vts=None, voice=None):
    summary = phase20_4_guard_summary(vts, voice)
    live = summary["live"]
    print("🛡️ Live Speech Safety Status")
    print("  Action: read-only; gom voice/expression live packet, chưa phát thật.")
    print(f"  Phase 20.4 Progress: {phase20_4_progress_percent(summary)}%")
    print(f"  Live: voice={live['voice_action']} | expression={live['expression_action']} | action={live['action']} | visible={live['visible']}")
    print(f"  Packet: payload={'yes' if live['payload'] else 'none'} | hotkey={live['hotkey'] or 'none'}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 20.4 chỉ live safety dry-run; chưa gọi voice/VTube thật.")


def print_live_speech_safety_test(raw_text=None, vts=None, voice=None):
    print("🧪 Live Speech Safety Test")
    print("  Action: read-only; synthetic live packet only, không gọi voice/VTube.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"live_ready"},
        "suppress": {"voice_suppress", "expression_suppress"},
        "hold": {"voice_hold", "expression_hold", "missing_hotkey_hold", "empty_payload_hold"},
        "block": {"voice_block", "expression_block"},
        "voice": {"voice_suppress", "voice_hold", "voice_block"},
        "expression": {"expression_suppress", "expression_hold", "expression_block"},
        "empty": {"empty_payload_hold", "missing_hotkey_hold"},
    }
    rows = phase20_4_live_safety_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, suppress, hold, block, voice, expression, empty")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | visible={row['visible']} hotkey={row['hotkey']} | reason={row['reason']}")
    print("  Execute: False")


def print_live_speech_safety_guard_status(vts=None, voice=None):
    summary = phase20_4_guard_summary(vts, voice)
    print("🧪 Phase 20.4 Live Speech Safety Guard")
    print("  Action: read-only; kiểm live speech safety, không gọi voice/VTube/execute.")
    print(f"  Progress: {phase20_4_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Live safety regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} reason={row['reason']}")


def print_phase20_4_status(vts=None, voice=None):
    summary = phase20_4_guard_summary(vts, voice)
    print("🧩 Phase 20.4 Status")
    print("  Goal: Live Speech Safety Gate - đảm bảo packet voice/expression không rò trước khi bật thật.")
    print(f"  Progress: {phase20_4_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /live-speech-safety-status | /live-speech-safety-test | /live-speech-safety-guard-status | /phase20-4-ready")


def print_phase20_4_ready(vts=None, voice=None):
    summary = phase20_4_guard_summary(vts, voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 20.4 Ready" if ready else "⚠️ Phase 20.4 Ready")
    print("  Goal: live speech safety đủ chắc để sang Phase 20.5 final voice gate.")
    print(f"  Progress: {phase20_4_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: live_speech_safety={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 20.4 chưa phát voice/expression thật.")


# ─────────────────────────────────────────────────────────────────
# PHASE 20.5 — Final Voice/Expression Gate (Dry-Run)
# ─────────────────────────────────────────────────────────────────

def phase20_subphase_rows(vts=None, voice=None):
    binding = phase20_1_guard_summary(voice)
    dispatch = phase20_2_guard_summary(voice)
    expression = phase20_3_guard_summary(vts, voice)
    live_safety = phase20_4_guard_summary(vts, voice)
    from nana.phases.phase10 import phase10_guard_failures
    rows = [
        ("phase20_1_voice_binding", binding, "voice_binding"),
        ("phase20_2_voice_dispatch", dispatch, "voice_dispatch"),
        ("phase20_3_expression_dispatch", expression, "expression_dispatch"),
        ("phase20_4_live_speech_safety", live_safety, "live_speech_safety"),
    ]
    return [
        {
            "name": name,
            "passed": not phase10_guard_failures(summary),
            "detail": f"{label}={summary['pass_count']}/{summary['total']} pass",
            "summary": summary,
        }
        for name, summary, label in rows
    ]


def phase20_5_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    subphases = phase20_subphase_rows(vts, voice)
    live_safety = subphases[3]["summary"]
    live = live_safety["live"]
    command_missing = sorted(PHASE20_5_COMMANDS - KNOWN_SLASH_COMMANDS)
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
    no_real_call = (
        live["voice_say"] is False
        and live["tts_call"] is False
        and live["lipsync_play"] is False
        and live["trigger_expression"] is False
        and live["vts_request"] is False
        and live["history_write"] is False
        and live["model_call"] is False
        and live["rewrite"] is False
        and live["memory_write"] is False
        and live["routine_create"] is False
        and live["execute"] is False
    )
    no_leak_ok = live["action"] == "live_dry_run_ready" or (not live["payload"] and not live["hotkey"])
    rows = [
        ("subphase_closure", all(row["passed"] for row in subphases), f"{sum(1 for row in subphases if row['passed'])}/{len(subphases)} pass"),
        ("live_voice_pipeline", live["action"] in PHASE20_4_LIVE_ACTIONS, f"voice={live['voice_action']} expression={live['expression_action']} action={live['action']}"),
        ("voice_binding_ready", subphases[0]["passed"], subphases[0]["detail"]),
        ("voice_dispatch_ready", subphases[1]["passed"], subphases[1]["detail"]),
        ("expression_dispatch_ready", subphases[2]["passed"], subphases[2]["detail"]),
        ("live_speech_safety_ready", subphases[3]["passed"], subphases[3]["detail"]),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", len(event_executed) + len(audit_executed) == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'} long={memory_snapshot.get('long_term', 0)}"),
        ("no_real_voice_expression", no_real_call, f"voice_say={live['voice_say']} tts={live['tts_call']} trigger_expression={live['trigger_expression']} vts_request={live['vts_request']} execute={live['execute']}"),
        ("leak_guard_final", no_leak_ok, f"action={live['action']} visible={live['visible']}"),
        ("autonomy_lock_contract", True, f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase21_boundary", True, "Phase 21 chỉ bàn/làm sau Phase 20 Ready=True; Phase 20 chưa bật voice/expression thật"),
        ("voice_gate_readonly", True, "status/guard/test không gọi voice.say, TTS, lipsync, trigger_expression, vts.request hoặc execute"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "subphases": subphases,
        "live": live,
        "queue": queue,
    }


def phase20_5_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_voice_gate_status(vts=None, voice=None):
    summary = phase20_5_guard_summary(vts, voice)
    live = summary["live"]
    queue = summary["queue"]
    print("🧠 Phase 20 Voice Gate Status")
    print("  Action: read-only; tổng kiểm voice/expression layer, chưa phát thật.")
    print(f"  Phase 20.5 Progress: {phase20_5_progress_percent(summary)}%")
    print(f"  Live: voice={live['voice_action']} expression={live['expression_action']} action={live['action']} visible={live['visible']}")
    print(f"  Queue: active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}/{getattr(runtime_queue(), 'max_items', 50)}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 20 đóng voice/expression dry-run; chưa phát live thật.")


def print_voice_gate_test(raw_text=None, vts=None, voice=None):
    print("🧪 Voice Gate Test")
    print("  Action: read-only; synthetic/summary only, không gọi voice/VTube.")
    key = (raw_text or "").strip().lower()
    summary = phase20_5_guard_summary(vts, voice)
    if key == "subphases":
        print(f"  Section: subphases | {sum(1 for row in summary['subphases'] if row['passed'])}/{len(summary['subphases'])} pass")
        for row in summary["subphases"]:
            print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")
        print("  Execute: False")
        return
    if key == "live":
        live = summary["live"]
        print("  Section: live pipeline")
        print(f"  Voice: {live['voice_action']}")
        print(f"  Expression: {live['expression_action']}")
        print(f"  Live: {live['action']} | visible={live['visible']} | payload={'yes' if live['payload'] else 'none'} | hotkey={live['hotkey'] or 'none'}")
        print(f"  Calls: voice_say={live['voice_say']} tts={live['tts_call']} trigger_expression={live['trigger_expression']} vts_request={live['vts_request']} execute={live['execute']}")
        print("  Execute: False")
        return
    if key == "queue":
        queue = summary["queue"]
        print("  Section: queue/pending")
        print(f"  Phase7 pending: {'yes' if PHASE7_PENDING_PLAN else 'none'}")
        print(f"  Runtime pending: {'yes' if (pending_actions().snapshot() or {}).get('pending') else 'none'}")
        print(f"  Queue: active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}/{getattr(runtime_queue(), 'max_items', 50)}")
        print("  Execute: False")
        return
    if key == "events":
        print("  Section: events/autonomy")
        print(f"  Runtime event execute=True: {len([entry for entry in RUNTIME_EVENT_LOG if entry.get('execute')])}")
        print(f"  Phase9 audit execute=True: {len([entry for entry in PHASE9_AUDIT_LOG if entry.get('execute')])}")
        print(f"  Autonomy: {AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}")
        print("  Execute: False")
        return
    if key and key != "all":
        print("  Status: not_found")
        print("  Sections: subphases, live, queue, events")
        print("  Execute: False")
        return
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    print("  Subphases:")
    for row in summary["subphases"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")
    print("  Gate rows:")
    for name, passed, detail in summary["rows"]:
        print(f"    {'pass' if passed else 'fail'} | {name} | {detail}")


def print_voice_gate_guard_status(vts=None, voice=None):
    summary = phase20_5_guard_summary(vts, voice)
    print("🧪 Phase 20.5 Voice/Expression Gate Guard")
    print("  Action: read-only; tổng kiểm Phase 20, không phát voice/VTube/execute.")
    print(f"  Progress: {phase20_5_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Subphases:")
    for row in summary["subphases"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")


def print_phase20_5_status(vts=None, voice=None):
    summary = phase20_5_guard_summary(vts, voice)
    print("🧩 Phase 20.5 Status")
    print("  Goal: Final Voice/Expression Gate - đóng voice dry-run trước Phase 21.")
    print(f"  Progress: {phase20_5_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /voice-gate-status | /voice-gate-test | /voice-gate-guard-status | /phase20-ready")


def print_phase20_ready(vts=None, voice=None):
    summary = phase20_5_guard_summary(vts, voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 20 Ready" if ready else "⚠️ Phase 20 Ready")
    print("  Goal: Phase 20 voice/expression dry-run layer đủ sạch để bắt đầu Phase 21.")
    print(f"  Progress: {phase20_5_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: phase20_gate={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 20 chưa phát voice/expression thật.")
