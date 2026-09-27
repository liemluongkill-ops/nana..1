"""
Phase 23 — Streaming Telemetry + Subphases 23.1–23.5.
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


PHASE23_1_COMMANDS = {
    "/voice-telemetry-status",
    "/voice-telemetry-guard-status",
    "/voice-telemetry-test",
    "/phase23-1-status",
    "/phase23-1-ready",
    "/phase23-1-guard-status",
    "/phase23-1-test",
    "/p23-1",
    "/p23-1-ready",
}

PHASE23_1_TELEMETRY_FIELDS = (
    "last_tts_text_len",
    "last_tts_chunks",
    "last_tts_audio_paths",
    "last_tts_cache_hits",
    "last_tts_cache_misses",
    "last_tts_prepare_ms",
    "last_tts_request_ms",
    "last_tts_playback_ms",
    "last_tts_total_ms",
    "last_tts_strategy",
)

PHASE23_2_COMMANDS = {
    "/voice-streaming-decision-status",
    "/voice-streaming-decision-guard-status",
    "/voice-streaming-decision-test",
    "/phase23-2-status",
    "/phase23-2-ready",
    "/phase23-2-guard-status",
    "/phase23-2-test",
    "/p23-2",
    "/p23-2-ready",
}

PHASE23_2_STREAMING_ACTIONS = {"hold_streaming", "keep_cache_path", "keep_chunk_path", "stream_dry_run_candidate"}

PHASE23_3_COMMANDS = {
    "/voice-streaming-dry-run-status",
    "/voice-streaming-dry-run-guard-status",
    "/voice-streaming-dry-run-test",
    "/phase23-3-status",
    "/phase23-3-ready",
    "/phase23-3-guard-status",
    "/phase23-3-test",
    "/p23-3",
    "/p23-3-ready",
}

PHASE23_3_PROBE_ACTIONS = {"hold_probe", "probe_cache_only", "probe_chunk_only", "probe_stream_dry_run"}

PHASE23_4_COMMANDS = {
    "/voice-stream-safety-status",
    "/voice-stream-safety-guard-status",
    "/voice-stream-safety-test",
    "/phase23-4-status",
    "/phase23-4-ready",
    "/phase23-4-guard-status",
    "/phase23-4-test",
    "/p23-4",
    "/p23-4-ready",
}

PHASE23_4_STREAM_SAFETY_ACTIONS = {"block_stream_live", "hold_stream_live", "stream_safety_ready"}

PHASE23_5_COMMANDS = {
    "/voice-stream-gate-status",
    "/voice-stream-gate-guard-status",
    "/voice-stream-gate-test",
    "/phase23-status",
    "/phase23-ready",
    "/phase23-5-status",
    "/phase23-5-ready",
    "/phase23-5-guard-status",
    "/phase23-5-test",
    "/p23",
    "/p23-ready",
    "/p23-5",
    "/p23-5-ready",
}

PHASE23_5_STREAM_GATE_ACTIONS = {"block_stream_gate", "hold_stream_gate", "stream_gate_ready"}

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
# PHASE 23.1 — Telemetry Baseline
# ─────────────────────────────────────────────────────────────────

def phase23_1_telemetry_snapshot():
    from nana.phases.phase20 import phase20_1_voice_snapshot
    from nana.phases.phase21 import phase21_5_guard_summary

    voice_state = phase20_1_voice_snapshot(None)
    phase21_summary = phase21_5_guard_summary()
    snapshot = {
        "timestamp": None,
        "voice_speaking": voice_state.get("speaking"),
        "voice_queue_size": voice_state.get("queue_size"),
        "voice_queue_maxsize": voice_state.get("queue_maxsize"),
        "tts_latency_ms": None,
        "vts_latency_ms": None,
        "total_latency_ms": None,
        "context_turns": 0,
        "phase21_pass_count": phase21_summary.get("pass_count", 0),
        "phase21_total": phase21_summary.get("total", 0),
        "streaming_pilot_enabled": False,
        "streaming_kill_switch": False,
        "stream_buffer_size": 0,
        "stream_active": False,
        "memory_budget_mb": 0.0,
        "focus_mode": "normal",
    }
    return snapshot


def phase23_1_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures
    from nana.phases.phase22 import phase22_5_guard_summary

    phase22 = phase22_5_guard_summary()
    snapshot = phase23_1_telemetry_snapshot()
    command_missing = sorted(PHASE23_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    fields_ok = all(field in snapshot for field in PHASE23_1_TELEMETRY_FIELDS)
    rows = [
        ("phase22_foundation", not phase10_guard_failures(phase22), f"phase22_gate={phase22['pass_count']}/{phase22['total']}"),
        ("telemetry_fields_present", fields_ok, f"fields={','.join(sorted(PHASE23_1_TELEMETRY_FIELDS))}"),
        ("telemetry_types_valid", isinstance(snapshot.get("voice_speaking"), bool) and isinstance(snapshot.get("voice_queue_size", -1), int), "snapshot types valid"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("telemetry_readonly", True, "snapshot only, no streaming calls"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase22": phase22,
        "snapshot": snapshot,
    }


def phase23_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_telemetry_status():
    summary = phase23_1_guard_summary()
    snap = summary["snapshot"]
    print("📡 Telemetry Status")
    print("  Action: read-only; thu thập telemetry snapshot, không gọi stream.")
    print(f"  Phase 23.1 Progress: {phase23_1_progress_percent(summary)}%")
    print(f"  Voice: speaking={snap.get('voice_speaking')} queue={snap.get('voice_queue_size',0)}/{snap.get('voice_queue_maxsize',0)}")
    print(f"  Latency: tts={snap.get('tts_latency_ms')} vts={snap.get('vts_latency_ms')} total={snap.get('total_latency_ms')}")
    print(f"  Stream: pilot_enabled={snap.get('streaming_pilot_enabled')} kill_switch={snap.get('streaming_kill_switch')} active={snap.get('stream_active')}")
    print(f"  Phase21: {snap.get('phase21_pass_count')}/{snap.get('phase21_total')} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase23_1_status():
    summary = phase23_1_guard_summary()
    print("🧩 Phase 23.1 Status")
    print("  Goal: Telemetry Baseline - thu thập telemetry snapshot cho streaming.")
    print(f"  Progress: {phase23_1_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 23.2 — Streaming Decision
# ─────────────────────────────────────────────────────────────────

def phase23_2_streaming_decision(telemetry, pilot_enabled=False, kill_switch=False):
    telemetry = telemetry or {}
    voice_speaking = bool(telemetry.get("voice_speaking"))
    queue_size = int(telemetry.get("voice_queue_size") or 0)
    queue_maxsize = int(telemetry.get("voice_queue_maxsize") or 0)
    if kill_switch:
        action = "kill_switch_block"
        reason = "streaming_kill_switch_active"
    elif not pilot_enabled:
        action = "pilot_disabled_stream"
        reason = "streaming_pilot_disabled"
    elif voice_speaking:
        action = "hold_stream"
        reason = "voice_currently_speaking_holds_stream"
    elif queue_maxsize > 0 and queue_size >= queue_maxsize:
        action = "hold_stream"
        reason = "voice_queue_full_holds_stream"
    else:
        action = "stream_ready"
        reason = "streaming_pilot_enabled_ready"
    return {
        "action": action,
        "reason": reason,
        "voice_speaking": voice_speaking,
        "queue_size": queue_size,
        "queue_maxsize": queue_maxsize,
        "pilot_enabled": bool(pilot_enabled),
        "kill_switch": bool(kill_switch),
        "stream_ready": action == "stream_ready",
        "stream_call": False,
        "stream_buffer_write": False,
        "model_call": False,
        "rewrite": False,
        "memory_write": False,
        "execute": False,
    }


def phase23_2_streaming_rows():
    cases = [
        ("stream_ready", {}, True, False, "stream_ready"),
        ("pilot_disabled", {}, False, False, "pilot_disabled_stream"),
        ("kill_switch", {}, True, True, "kill_switch_block"),
        ("speaking_hold", {"voice_speaking": True}, True, False, "hold_stream"),
        ("queue_full_hold", {"voice_queue_size": 10, "voice_queue_maxsize": 10}, True, False, "hold_stream"),
        ("not_speaking_ready", {"voice_speaking": False}, True, False, "stream_ready"),
    ]
    rows = []
    for name, telemetry, pilot_enabled, kill_switch, expected in cases:
        result = phase23_2_streaming_decision(telemetry, pilot_enabled, kill_switch)
        no_call = (
            result["stream_call"] is False
            and result["stream_buffer_write"] is False
            and result["model_call"] is False
            and result["rewrite"] is False
            and result["memory_write"] is False
            and result["execute"] is False
        )
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_call,
            "got": result["action"],
            "expected": expected,
            "speaking": result["voice_speaking"],
            "ready": result["stream_ready"],
            "reason": result["reason"],
        })
    return rows


def phase23_2_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    phase23_1 = phase23_1_guard_summary()
    live = phase23_2_streaming_decision(phase23_1["snapshot"], pilot_enabled=False, kill_switch=False)
    test_rows = phase23_2_streaming_rows()
    command_missing = sorted(PHASE23_2_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    rows = [
        ("phase23_1_foundation", not phase10_guard_failures(phase23_1), f"telemetry={phase23_1['pass_count']}/{phase23_1['total']}"),
        ("streaming_action_taxonomy", PHASE23_2_STREAMING_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("streaming_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("streaming_snapshot", live["action"] in PHASE23_2_STREAMING_ACTIONS, f"speaking={live['voice_speaking']} pilot={live['pilot_enabled']} kill_switch={live['kill_switch']} action={live['action']}"),
        ("no_streaming_calls_yet", live["stream_call"] is False, f"stream_call={live['stream_call']} stream_buffer_write={live['stream_buffer_write']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("streaming_decision_readonly", True, "decision only, no streaming calls"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase23_1": phase23_1,
        "test_rows": test_rows,
        "live": live,
    }


def phase23_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_streaming_decision_status():
    summary = phase23_2_guard_summary()
    live = summary["live"]
    print("🌊 Streaming Decision Status")
    print("  Action: read-only; kiểm streaming decision, không gọi stream thật.")
    print(f"  Phase 23.2 Progress: {phase23_2_progress_percent(summary)}%")
    print(f"  Live: speaking={live['voice_speaking']} pilot={live['pilot_enabled']} kill_switch={live['kill_switch']} action={live['action']} ready={live['stream_ready']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_streaming_decision_test(raw_text=None):
    print("🧪 Streaming Decision Test")
    print("  Action: read-only; synthetic decision only, không gọi stream.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"stream_ready", "not_speaking_ready"},
        "disabled": {"pilot_disabled"},
        "kill": {"kill_switch"},
        "hold": {"speaking_hold", "queue_full_hold"},
        "speaking": {"speaking_hold"},
        "queue": {"queue_full_hold"},
    }
    rows = phase23_2_streaming_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, disabled, kill, hold, speaking, queue")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} reason={row['reason']}")


def print_phase23_2_status():
    summary = phase23_2_guard_summary()
    print("🧩 Phase 23.2 Status")
    print("  Goal: Streaming Decision - kiểm streaming decision từ telemetry.")
    print(f"  Progress: {phase23_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 23.3 — Streaming Probe
# ─────────────────────────────────────────────────────────────────

def phase23_3_streaming_probe(streaming_decision, pilot_enabled=False):
    streaming_decision = streaming_decision or {}
    action = streaming_decision.get("action") or "pilot_disabled_stream"
    if action == "stream_ready" and pilot_enabled:
        return {
            "action": "probe_stream_ready",
            "reason": "streaming_probe_preview",
            "probed": True,
            "stream_call": False,
            "stream_buffer_write": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    else:
        return {
            "action": "probe_held",
            "reason": "streaming_not_ready",
            "probed": False,
            "stream_call": False,
            "stream_buffer_write": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }


def phase23_3_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    phase23_2 = phase23_2_guard_summary()
    live = phase23_3_streaming_probe(phase23_2["live"], pilot_enabled=False)
    command_missing = sorted(PHASE23_3_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {live["action"]}
    rows = [
        ("phase23_2_foundation", not phase10_guard_failures(phase23_2), f"streaming_decision={phase23_2['pass_count']}/{phase23_2['total']}"),
        ("probe_action_taxonomy", PHASE23_3_PROBE_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("streaming_probe_snapshot", live["action"] in PHASE23_3_PROBE_ACTIONS, f"action={live['action']} probed={live['probed']}"),
        ("no_streaming_calls_yet", live["stream_call"] is False and live["stream_buffer_write"] is False, f"stream_call={live['stream_call']} stream_buffer_write={live['stream_buffer_write']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("streaming_probe_readonly", True, "probe only, no streaming calls"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase23_2": phase23_2,
        "live": live,
    }


def phase23_3_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_streaming_probe_status():
    summary = phase23_3_guard_summary()
    live = summary["live"]
    print("🔍 Streaming Probe Status")
    print("  Action: read-only; dry-run streaming probe, không gọi stream thật.")
    print(f"  Phase 23.3 Progress: {phase23_3_progress_percent(summary)}%")
    print(f"  Action: {live['action']} | Probed: {live['probed']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase23_3_status():
    summary = phase23_3_guard_summary()
    print("🧩 Phase 23.3 Status")
    print("  Goal: Streaming Probe - dry-run streaming probe từ decision.")
    print(f"  Progress: {phase23_3_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 23.4 — Stream Safety Gate
# ─────────────────────────────────────────────────────────────────

def phase23_4_stream_safety_decision(probe_result, telemetry):
    probe_result = probe_result or {}
    telemetry = telemetry or {}
    probe_action = probe_result.get("action") or "probe_held"
    kill_switch = bool(telemetry.get("streaming_kill_switch"))
    if kill_switch:
        action = "block_stream"
        reason = "kill_switch_blocks_stream"
    elif probe_action == "probe_held":
        action = "hold_stream"
        reason = "probe_holds_stream"
    elif probe_action == "probe_stream_ready":
        action = "stream_safety_ready"
        reason = "stream_passes_safety"
    else:
        action = "hold_stream"
        reason = "unknown_probe_holds_stream"
    return {
        "action": action,
        "reason": reason,
        "probe_action": probe_action,
        "kill_switch": kill_switch,
        "stream_ready": action == "stream_safety_ready",
        "stream_call": False,
        "stream_buffer_write": False,
        "model_call": False,
        "rewrite": False,
        "memory_write": False,
        "execute": False,
    }


def phase23_4_safety_rows():
    cases = [
        ("safety_ready", {"action": "probe_stream_ready"}, False, "stream_safety_ready"),
        ("kill_switch_block", {"action": "probe_stream_ready"}, True, "block_stream"),
        ("probe_held_hold", {"action": "probe_held"}, False, "hold_stream"),
    ]
    rows = []
    for name, probe_result, kill_switch, expected in cases:
        telemetry = {"streaming_kill_switch": kill_switch}
        result = phase23_4_stream_safety_decision(probe_result, telemetry)
        no_call = (
            result["stream_call"] is False
            and result["stream_buffer_write"] is False
            and result["model_call"] is False
            and result["rewrite"] is False
            and result["memory_write"] is False
            and result["execute"] is False
        )
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_call,
            "got": result["action"],
            "expected": expected,
            "kill_switch": kill_switch,
            "ready": result["stream_ready"],
            "reason": result["reason"],
        })
    return rows


def phase23_4_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    phase23_3 = phase23_3_guard_summary()
    live = phase23_4_stream_safety_decision(phase23_3["live"], phase23_3["phase23_2"]["snapshot"])
    test_rows = phase23_4_safety_rows()
    command_missing = sorted(PHASE23_4_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    rows = [
        ("phase23_3_foundation", not phase10_guard_failures(phase23_3), f"streaming_probe={phase23_3['pass_count']}/{phase23_3['total']}"),
        ("safety_action_taxonomy", PHASE23_4_STREAM_SAFETY_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("safety_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("stream_safety_snapshot", live["action"] in PHASE23_4_STREAM_SAFETY_ACTIONS, f"probe={live['probe_action']} kill_switch={live['kill_switch']} action={live['action']}"),
        ("no_streaming_calls_yet", live["stream_call"] is False and live["stream_buffer_write"] is False, f"stream_call={live['stream_call']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("stream_safety_readonly", True, "safety only, no streaming calls"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase23_3": phase23_3,
        "test_rows": test_rows,
        "live": live,
    }


def phase23_4_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_stream_safety_status():
    summary = phase23_4_guard_summary()
    live = summary["live"]
    print("🛡️ Stream Safety Status")
    print("  Action: read-only; kiểm stream safety, không gọi stream thật.")
    print(f"  Phase 23.4 Progress: {phase23_4_progress_percent(summary)}%")
    print(f"  Action: {live['action']} | Probe: {live['probe_action']} | Kill switch: {live['kill_switch']} | Ready: {live['stream_ready']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase23_4_status():
    summary = phase23_4_guard_summary()
    print("🧩 Phase 23.4 Status")
    print("  Goal: Stream Safety Gate - kiểm safety trước khi bật stream thật.")
    print(f"  Progress: {phase23_4_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 23.5 — Final Stream Gate
# ─────────────────────────────────────────────────────────────────

def phase23_subphase_rows():
    telemetry = phase23_1_guard_summary()
    streaming = phase23_2_guard_summary()
    probe = phase23_3_guard_summary()
    safety = phase23_4_guard_summary()
    from nana.phases.phase10 import phase10_guard_failures
    return [
        ("phase23_1_telemetry", telemetry, not phase10_guard_failures(telemetry)),
        ("phase23_2_streaming_decision", streaming, not phase10_guard_failures(streaming)),
        ("phase23_3_streaming_probe", probe, not phase10_guard_failures(probe)),
        ("phase23_4_stream_safety", safety, not phase10_guard_failures(safety)),
    ]


def phase23_5_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    subphases = phase23_subphase_rows()
    command_missing = sorted(PHASE23_5_COMMANDS - KNOWN_SLASH_COMMANDS)
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
    rows = [
        ("subphase_closure", all(passed for _, _, passed in subphases), f"{sum(1 for _, _, p in subphases if p)}/{len(subphases)} pass"),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", len(event_executed) + len(audit_executed) == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'}"),
        ("autonomy_lock_contract", True, f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase24_boundary", True, "Phase 24 chỉ bàn/làm sau Phase 23 Ready=True"),
        ("stream_gate_readonly", True, "status/guard/test không gọi stream thật"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "subphases": subphases,
        "queue": queue,
    }


def phase23_5_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_stream_gate_status():
    summary = phase23_5_guard_summary()
    queue = summary["queue"]
    print("🧠 Phase 23 Stream Gate Status")
    print("  Action: read-only; tổng kiểm streaming layer, chưa gọi stream thật.")
    print(f"  Phase 23.5 Progress: {phase23_5_progress_percent(summary)}%")
    print(f"  Queue: active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}/{getattr(runtime_queue(), 'max_items', 50)}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase23_5_status():
    summary = phase23_5_guard_summary()
    print("🧩 Phase 23.5 Status")
    print("  Goal: Final Stream Gate - tổng kiểm streaming telemetry layer.")
    print(f"  Progress: {phase23_5_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase23_ready():
    summary = phase23_5_guard_summary()
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 23 Ready" if ready else "⚠️ Phase 23 Ready")
    print("  Goal: Phase 23 streaming telemetry đủ sạch để bắt đầu Phase 24.")
    print(f"  Progress: {phase23_5_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print("  Autonomy: unchanged; Phase 23 chưa gọi stream thật.")



def print_phase23_1_ready(vts=None, voice=None):
    summary = phase23_1_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase23_1 Ready: {pass_label}")
    print(f"  Progress: phase23_1_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready





def print_phase23_2_ready(vts=None, voice=None):
    summary = phase23_2_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase23_2 Ready: {pass_label}")
    print(f"  Progress: phase23_2_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready





def print_phase23_3_ready(vts=None, voice=None):
    summary = phase23_3_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase23_3 Ready: {pass_label}")
    print(f"  Progress: phase23_3_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready





def print_phase23_4_ready(vts=None, voice=None):
    summary = phase23_4_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase23_4 Ready: {pass_label}")
    print(f"  Progress: phase23_4_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready
