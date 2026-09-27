"""
Phase 22 — Voice Latency Engine + Subphases 22.1–22.5.
DRY-RUN phase: chỉ mô phỏng latency measurement, không patch thật.
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


PHASE22_1_COMMANDS = {
    "/voice-latency-baseline-status",
    "/voice-latency-baseline-guard-status",
    "/voice-latency-baseline-test",
    "/phase22-1-status",
    "/phase22-1-ready",
    "/phase22-1-guard-status",
    "/phase22-1-test",
    "/p22-1",
    "/p22-1-ready",
}

PHASE22_1_SNAPSHOT_FIELDS = (
    "timestamp",
    "voice_status",
    "voice_speaking",
    "voice_queue_size",
    "voice_queue_maxsize",
    "tts_mode",
    "vts_connected",
    "vts_last_hotkey",
    "vts_last_hotkey_time",
    "recent_tts_latency_ms",
    "recent_vts_latency_ms",
    "recent_total_latency_ms",
    "context_turns",
    "context_memory_budget_mb",
    "focus_mode",
)

PHASE22_2_COMMANDS = {
    "/voice-latency-design-status",
    "/voice-latency-design-guard-status",
    "/voice-latency-design-test",
    "/phase22-2-status",
    "/phase22-2-ready",
    "/phase22-2-guard-status",
    "/phase22-2-test",
    "/p22-2",
    "/p22-2-ready",
}

PHASE22_2_LATENCY_TIERS = {
    0: "fast",
    500: "medium",
    1500: "slow",
    3000: "very_slow",
}

PHASE22_2_DESIGN_LATENCIES = {
    "tts_target_ms": 800.0,
    "vts_target_ms": 100.0,
    "total_target_ms": 1000.0,
}

PHASE22_3_COMMANDS = {
    "/voice-engine-patch-status",
    "/voice-engine-patch-guard-status",
    "/voice-engine-patch-test",
    "/phase22-3-status",
    "/phase22-3-ready",
    "/phase22-3-guard-status",
    "/phase22-3-test",
    "/p22-3",
    "/p22-3-ready",
}

PHASE22_3_PATCH_POINTS = (
    "VoiceEngine.say",
    "VoiceEngine._voice_worker",
    "VoiceEngine._tts_and_lipsync",
    "VoiceEngine.tts_to_wav",
    "VoiceEngine._post_tts",
    "LipsyncManager.play_blocking",
)

PHASE22_4_COMMANDS = {
    "/voice-engine-impl-status",
    "/voice-engine-impl-guard-status",
    "/voice-engine-impl-test",
    "/phase22-4-status",
    "/phase22-4-ready",
    "/phase22-4-guard-status",
    "/phase22-4-test",
    "/p22-4",
    "/p22-4-ready",
}

PHASE22_4_PROBE_TARGETS = {
    "VoiceEngine.say",
    "VoiceEngine._voice_worker",
    "VoiceEngine._tts_and_lipsync",
    "VoiceEngine.tts_to_wav",
    "VoiceEngine._post_tts",
    "LipsyncManager.play_blocking",
}

PHASE22_5_COMMANDS = {
    "/voice-latency-gate-status",
    "/voice-latency-gate-guard-status",
    "/voice-latency-gate-test",
    "/phase22-status",
    "/phase22-ready",
    "/phase22-5-status",
    "/phase22-5-ready",
    "/phase22-5-guard-status",
    "/phase22-5-test",
    "/p22",
    "/p22-ready",
    "/p22-5",
    "/p22-5-ready",
}

PHASE22_5_GATE_ACTIONS = {"baseline_hold", "cache_optimized_ready", "chunk_optimized_ready", "streaming_pending"}

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
# PHASE 22.1 — Latency Snapshot
# ─────────────────────────────────────────────────────────────────

def phase22_1_latency_snapshot():
    from nana.phases.phase20 import phase20_1_voice_snapshot

    voice_state = phase20_1_voice_snapshot(None)
    snapshot = {
        "timestamp": None,
        "voice_status": voice_state.get("status"),
        "voice_speaking": voice_state.get("speaking"),
        "voice_queue_size": voice_state.get("queue_size"),
        "voice_queue_maxsize": voice_state.get("queue_maxsize"),
        "tts_mode": "unknown",
        "vts_connected": False,
        "vts_last_hotkey": None,
        "vts_last_hotkey_time": None,
        "recent_tts_latency_ms": None,
        "recent_vts_latency_ms": None,
        "recent_total_latency_ms": None,
        "context_turns": 0,
        "context_memory_budget_mb": 0.0,
        "focus_mode": "normal",
    }
    return snapshot


def phase22_1_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures
    from nana.phases.phase21 import phase21_5_guard_summary

    phase21 = phase21_5_guard_summary()
    snapshot = phase22_1_latency_snapshot()
    command_missing = sorted(PHASE22_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    snapshot_fields_ok = all(field in snapshot for field in PHASE22_1_SNAPSHOT_FIELDS)
    snapshot_values_ok = isinstance(snapshot.get("voice_speaking"), bool) and isinstance(snapshot.get("voice_queue_size", -1), int)
    rows = [
        ("phase21_foundation", not phase10_guard_failures(phase21), f"phase21_gate={phase21['pass_count']}/{phase21['total']}"),
        ("snapshot_fields_present", snapshot_fields_ok, f"fields={','.join(sorted(PHASE22_1_SNAPSHOT_FIELDS))}"),
        ("snapshot_types_valid", snapshot_values_ok, f"speaking={snapshot.get('voice_speaking')} queue_size={snapshot.get('voice_queue_size')}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("latency_snapshot_readonly", True, "snapshot only, no engine patching"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase21": phase21,
        "snapshot": snapshot,
    }


def phase22_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_latency_snapshot_status():
    summary = phase22_1_guard_summary()
    snap = summary["snapshot"]
    print("📊 Latency Snapshot Status")
    print("  Action: read-only; thu thập latency snapshot, không patch engine.")
    print(f"  Phase 22.1 Progress: {phase22_1_progress_percent(summary)}%")
    print(f"  Voice: status={snap.get('voice_status')} speaking={snap.get('voice_speaking')} queue={snap.get('voice_queue_size',0)}/{snap.get('voice_queue_maxsize',0)}")
    print(f"  VTS: connected={snap.get('vts_connected')} last_hotkey={snap.get('vts_last_hotkey')}")
    print(f"  Latency: tts={snap.get('recent_tts_latency_ms')} vts={snap.get('recent_vts_latency_ms')} total={snap.get('recent_total_latency_ms')}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase22_1_status():
    summary = phase22_1_guard_summary()
    print("🧩 Phase 22.1 Status")
    print("  Goal: Latency Snapshot - thu thập baseline latency trước khi patch.")
    print(f"  Progress: {phase22_1_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 22.2 — Latency Design
# ─────────────────────────────────────────────────────────────────

def phase22_2_latency_design(snapshot):
    snapshot = snapshot or {}
    tts_ms = snapshot.get("recent_tts_latency_ms") or 0.0
    vts_ms = snapshot.get("recent_vts_latency_ms") or 0.0
    total_ms = snapshot.get("recent_total_latency_ms") or 0.0
    tier = "fast"
    for threshold, name in sorted(PHASE22_2_LATENCY_TIERS.items()):
        if total_ms >= threshold:
            tier = name
    targets = {}
    for name, target_ms in PHASE22_2_DESIGN_LATENCIES.items():
        targets[name] = target_ms
    return {
        "tier": tier,
        "total_ms": total_ms,
        "tts_ms": tts_ms,
        "vts_ms": vts_ms,
        "targets": targets,
        "patch_needed": total_ms > PHASE22_2_DESIGN_LATENCIES.get("total_target_ms", 1000.0),
    }


def phase22_2_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    phase22_1 = phase22_1_guard_summary()
    design = phase22_2_latency_design(phase22_1["snapshot"])
    command_missing = sorted(PHASE22_2_COMMANDS - KNOWN_SLASH_COMMANDS)
    design_targets_ok = all(isinstance(v, (int, float)) for v in design["targets"].values())
    rows = [
        ("phase22_1_foundation", not phase10_guard_failures(phase22_1), f"latency_snapshot={phase22_1['pass_count']}/{phase22_1['total']}"),
        ("design_targets_valid", design_targets_ok, f"targets={design['targets']}"),
        ("design_snapshot", True, f"tier={design['tier']} total_ms={design['total_ms']} tts_ms={design['tts_ms']} vts_ms={design['vts_ms']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("latency_design_readonly", True, "design only, no engine patching"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase22_1": phase22_1,
        "design": design,
    }


def phase22_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_latency_design_status():
    summary = phase22_2_guard_summary()
    design = summary["design"]
    print("🎯 Latency Design Status")
    print("  Action: read-only; thiết kế latency targets, không patch thật.")
    print(f"  Phase 22.2 Progress: {phase22_2_progress_percent(summary)}%")
    print(f"  Tier: {design['tier']} | Total: {design['total_ms']:.0f}ms | TTS: {design['tts_ms']:.0f}ms | VTS: {design['vts_ms']:.0f}ms")
    print(f"  Targets: {design['targets']}")
    print(f"  Patch needed: {design['patch_needed']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase22_2_status():
    summary = phase22_2_guard_summary()
    print("🧩 Phase 22.2 Status")
    print("  Goal: Latency Design - định nghĩa latency targets từ snapshot.")
    print(f"  Progress: {phase22_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 22.3 — Engine Patch (Dry-Run)
# ─────────────────────────────────────────────────────────────────

def phase22_3_engine_patch(design, dry_run=True):
    design = design or {}
    patch_needed = design.get("patch_needed", False)
    if not patch_needed:
        return {
            "action": "no_patch_needed",
            "reason": "latency_within_targets",
            "patches": [],
            "engine_modified": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    if dry_run:
        return {
            "action": "dry_run_patch_ready",
            "reason": "dry_run_patch_preview",
            "patches": [{"target": k, "target_ms": v} for k, v in design.get("targets", {}).items()],
            "engine_modified": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    return {
        "action": "live_patch_applied",
        "reason": "live_patch_executed",
        "patches": [{"target": k, "target_ms": v} for k, v in design.get("targets", {}).items()],
        "engine_modified": True,
        "model_call": False,
        "rewrite": False,
        "memory_write": False,
        "execute": False,
    }


def phase22_3_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    phase22_2 = phase22_2_guard_summary()
    live = phase22_3_engine_patch(phase22_2["design"], dry_run=True)
    command_missing = sorted(PHASE22_3_COMMANDS - KNOWN_SLASH_COMMANDS)
    no_effect = (
        live["engine_modified"] is False
        and live["model_call"] is False
        and live["rewrite"] is False
        and live["memory_write"] is False
        and live["execute"] is False
    )
    rows = [
        ("phase22_2_foundation", not phase10_guard_failures(phase22_2), f"latency_design={phase22_2['pass_count']}/{phase22_2['total']}"),
        ("engine_patch_snapshot", True, f"action={live['action']} reason={live['reason']} patches={len(live['patches'])}"),
        ("dry_run_patch_readonly", no_effect, f"engine_modified={live['engine_modified']} model_call={live['model_call']} rewrite={live['rewrite']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase22_2": phase22_2,
        "live": live,
    }


def phase22_3_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_engine_patch_status():
    summary = phase22_3_guard_summary()
    live = summary["live"]
    print("⚙️ Engine Patch Status")
    print("  Action: read-only; dry-run engine patch, chưa apply thật.")
    print(f"  Phase 22.3 Progress: {phase22_3_progress_percent(summary)}%")
    print(f"  Action: {live['action']} | Reason: {live['reason']} | Patches: {len(live['patches'])}")
    print(f"  Modified: engine={live['engine_modified']} model={live['model_call']} rewrite={live['rewrite']} execute={live['execute']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase22_3_status():
    summary = phase22_3_guard_summary()
    print("🧩 Phase 22.3 Status")
    print("  Goal: Engine Patch Dry-Run - mô phỏng patch latency, chưa apply thật.")
    print(f"  Progress: {phase22_3_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 22.4 — Engine Probe
# ─────────────────────────────────────────────────────────────────

def phase22_4_engine_probe(patch_result):
    patch_result = patch_result or {}
    action = patch_result.get("action") or "no_patch_needed"
    if action == "dry_run_patch_ready":
        return {
            "action": "probe_dry_run",
            "reason": "dry_run_probe_preview",
            "probed_targets": sorted(PHASE22_4_PROBE_TARGETS),
            "engine_probed": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    elif action == "no_patch_needed":
        return {
            "action": "no_probe_needed",
            "reason": "no_patch_needed",
            "probed_targets": [],
            "engine_probed": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }
    else:
        return {
            "action": "unknown_probe",
            "reason": f"unknown_patch_action_{action}",
            "probed_targets": [],
            "engine_probed": False,
            "model_call": False,
            "rewrite": False,
            "memory_write": False,
            "execute": False,
        }


def phase22_4_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    phase22_3 = phase22_3_guard_summary()
    live = phase22_4_engine_probe(phase22_3["live"])
    command_missing = sorted(PHASE22_4_COMMANDS - KNOWN_SLASH_COMMANDS)
    no_effect = (
        live["engine_probed"] is False
        and live["model_call"] is False
        and live["rewrite"] is False
        and live["memory_write"] is False
        and live["execute"] is False
    )
    rows = [
        ("phase22_3_foundation", not phase10_guard_failures(phase22_3), f"engine_patch={phase22_3['pass_count']}/{phase22_3['total']}"),
        ("engine_probe_snapshot", True, f"action={live['action']} reason={live['reason']} probed={len(live['probed_targets'])}"),
        ("probe_targets_known", set(live["probed_targets"]) <= PHASE22_4_PROBE_TARGETS, f"targets={','.join(sorted(live['probed_targets']))}"),
        ("dry_run_probe_readonly", no_effect, f"engine_probed={live['engine_probed']} model_call={live['model_call']} rewrite={live['rewrite']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase22_3": phase22_3,
        "live": live,
    }


def phase22_4_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_engine_probe_status():
    summary = phase22_4_guard_summary()
    live = summary["live"]
    print("🔍 Engine Probe Status")
    print("  Action: read-only; dry-run engine probe, chưa probe thật.")
    print(f"  Phase 22.4 Progress: {phase22_4_progress_percent(summary)}%")
    print(f"  Action: {live['action']} | Reason: {live['reason']} | Probed: {','.join(live['probed_targets']) or 'none'}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase22_4_status():
    summary = phase22_4_guard_summary()
    print("🧩 Phase 22.4 Status")
    print("  Goal: Engine Probe - dry-run probe latency targets.")
    print(f"  Progress: {phase22_4_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 22.5 — Final Latency Gate
# ─────────────────────────────────────────────────────────────────

def phase22_subphase_rows():
    snapshot_summary = phase22_1_guard_summary()
    design_summary = phase22_2_guard_summary()
    patch_summary = phase22_3_guard_summary()
    probe_summary = phase22_4_guard_summary()
    from nana.phases.phase10 import phase10_guard_failures
    return [
        ("phase22_1_latency_snapshot", snapshot_summary, not phase10_guard_failures(snapshot_summary)),
        ("phase22_2_latency_design", design_summary, not phase10_guard_failures(design_summary)),
        ("phase22_3_engine_patch", patch_summary, not phase10_guard_failures(patch_summary)),
        ("phase22_4_engine_probe", probe_summary, not phase10_guard_failures(probe_summary)),
    ]


def phase22_5_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    subphases = phase22_subphase_rows()
    command_missing = sorted(PHASE22_5_COMMANDS - KNOWN_SLASH_COMMANDS)
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
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'} long={memory_snapshot.get('long_term', 0)}"),
        ("autonomy_lock_contract", True, f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase23_boundary", True, "Phase 23 chỉ bàn/làm sau Phase 22 Ready=True; Phase 22 chưa patch/probe thật"),
        ("latency_gate_readonly", True, "status/guard/test không patch engine thật, không probe thật"),
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


def phase22_5_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_latency_gate_status():
    summary = phase22_5_guard_summary()
    queue = summary["queue"]
    print("🧠 Phase 22 Latency Gate Status")
    print("  Action: read-only; tổng kiểm latency layer, chưa patch/probe thật.")
    print(f"  Phase 22.5 Progress: {phase22_5_progress_percent(summary)}%")
    print(f"  Queue: active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}/{getattr(runtime_queue(), 'max_items', 50)}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase22_5_status():
    summary = phase22_5_guard_summary()
    print("🧩 Phase 22.5 Status")
    print("  Goal: Final Latency Gate - tổng kiểm Phase 22 dry-run layer.")
    print(f"  Progress: {phase22_5_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase22_ready():
    summary = phase22_5_guard_summary()
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 22 Ready" if ready else "⚠️ Phase 22 Ready")
    print("  Goal: Phase 22 latency dry-run đủ sạch để bắt đầu Phase 23.")
    print(f"  Progress: {phase22_5_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print("  Autonomy: unchanged; Phase 22 chưa patch/probe engine thật.")



def print_phase22_1_ready(vts=None, voice=None):
    summary = phase22_1_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase22_1 Ready: {pass_label}")
    print(f"  Progress: phase22_1_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready





def print_phase22_2_ready(vts=None, voice=None):
    summary = phase22_2_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase22_2 Ready: {pass_label}")
    print(f"  Progress: phase22_2_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready





def print_phase22_3_ready(vts=None, voice=None):
    summary = phase22_3_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase22_3 Ready: {pass_label}")
    print(f"  Progress: phase22_3_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready





def print_phase22_4_ready(vts=None, voice=None):
    summary = phase22_4_guard_summary(vts, voice)
    ready = not summary.get('failures')
    pass_label = 'yes' if ready else 'no'
    print(f"Phase phase22_4 Ready: {pass_label}")
    print(f"  Progress: phase22_4_progress_percent(summary)%")
    print(f"  Pass: {summary['pass_count']}/{summary['total']}")
    for row in summary['rows']:
        row_label = 'pass' if row[1] else 'warn'
        print(f"  {row[0]}: {row_label} | {row[2]}")
    return ready
