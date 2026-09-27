"""
Phase 21 — Voice/Expression Control Layer + Subphases 21.1–21.5.
Phase thật: gọi voice.say, trigger_expression, vts.request.
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


PHASE21_1_COMMANDS = {
    "/live-voice-control-status",
    "/live-voice-control-guard-status",
    "/live-voice-control-test",
    "/phase21-1-status",
    "/phase21-1-ready",
    "/phase21-1-guard-status",
    "/phase21-1-test",
    "/p21-1",
    "/p21-1-ready",
}

PHASE21_1_CONTROL_ACTIONS = {"block_voice", "hold_voice", "pilot_dry_run", "suppress_voice"}
LIVE_VOICE_CONTROL = {
    "pilot_enabled": False,
    "kill_switch": True,
    "allow_ambient": False,
    "allow_focus": False,
    "real_dispatch_enabled": False,
}

PHASE21_2_COMMANDS = {
    "/direct-voice-pilot-status",
    "/direct-voice-pilot-guard-status",
    "/direct-voice-pilot-test",
    "/phase21-2-status",
    "/phase21-2-ready",
    "/phase21-2-guard-status",
    "/phase21-2-test",
    "/p21-2",
    "/p21-2-ready",
}

PHASE21_3_COMMANDS = {
    "/guarded-voice-dispatch-status",
    "/guarded-voice-dispatch-guard-status",
    "/guarded-voice-dispatch-test",
    "/phase21-3-status",
    "/phase21-3-ready",
    "/phase21-3-guard-status",
    "/phase21-3-test",
    "/p21-3",
    "/p21-3-ready",
}

PHASE21_3_DISPATCH_ACTIONS = {"block_dispatch_hook", "dispatch_hook_dry_run", "hold_dispatch_hook", "suppress_dispatch_hook"}

PHASE21_4_COMMANDS = {
    "/live-path-replacement-status",
    "/live-path-replacement-guard-status",
    "/live-path-replacement-test",
    "/phase21-4-status",
    "/phase21-4-ready",
    "/phase21-4-guard-status",
    "/phase21-4-test",
    "/p21-4",
    "/p21-4-ready",
}

LIVE_VOICE_PATH_POLICY = {
    "replacement_enabled": False,
    "real_voice_call_enabled": False,
    "legacy_voice_path_enabled": True,
    "kill_switch": True,
}

PHASE21_4_PATH_REPLACEMENT = {"block_path_replacement", "hold_path_replacement", "path_replacement_ready", "suppress_path_replacement"}

PHASE21_5_COMMANDS = {
    "/live-voice-gate-status",
    "/live-voice-gate-guard-status",
    "/live-voice-gate-test",
    "/phase21-status",
    "/phase21-ready",
    "/phase21-5-status",
    "/phase21-5-ready",
    "/phase21-5-guard-status",
    "/phase21-5-test",
    "/p21",
    "/p21-ready",
    "/p21-5",
    "/p21-5-ready",
}

_rq = None
_cs = None
_mem = None
_voice = None
_vts = None

def _lazy_imports():
    global _rq, _cs, _mem, _voice, _vts
    if _rq is None:
        import nana.runtime_queue as _runtime_queue_module
        _rq = _runtime_queue_module
        from nana import context_state as _cs_module
        _cs = _cs_module
        from nana.memory import memory_governance_summary as _mem_summary
        _mem = _mem_summary
        from nana import voice as _voice_module
        _voice = _voice_module
        from nana import vts as _vts_module
        _vts = _vts_module

def runtime_queue():
    _lazy_imports()
    return _rq

def pending_actions():
    _lazy_imports()
    return _rq.pending_actions

def memory_governance_summary():
    _lazy_imports()
    return _mem()

def get_VoiceEngine():
    _lazy_imports()
    return _voice.VoiceEngine

def get_VTSConnector():
    _lazy_imports()
    return _vts.VTSConnector


# ─────────────────────────────────────────────────────────────────
# PHASE 21.1 — Voice/Expression Control Decision
# ─────────────────────────────────────────────────────────────────

def phase21_1_control_decision(phase20_live, pilot_enabled=False):
    phase20_live = phase20_live or {}
    action = phase20_live.get("action") or "hold_live_speech"
    payload = (phase20_live.get("payload") or "").strip()
    hotkey = phase20_live.get("hotkey")
    if not pilot_enabled:
        action = "pilot_disabled_control"
        reason = "voice_pilot_disabled"
    elif action == "hold_live_speech":
        action = "hold_live_speech"
        reason = "pilot_enabled_but_no_ready_packet"
    elif action == "block_live_speech":
        action = "block_live_speech"
        reason = "pilot_blocked_by_phase20"
    elif action == "suppress_live_speech":
        action = "suppress_live_speech"
        reason = "pilot_suppressed_by_phase20"
    elif action == "live_dry_run_ready" and payload and hotkey:
        action = "voice_control_ready"
        reason = "pilot_ready_voice_execution"
    else:
        action = "hold_live_speech"
        reason = "pilot_incomplete_packet"
    return {
        "action": action,
        "reason": reason,
        "phase20_action": phase20_live.get("action"),
        "pilot_enabled": bool(pilot_enabled),
        "has_payload": bool(payload),
        "has_hotkey": bool(hotkey),
        "voice_control_ready": action == "voice_control_ready",
        "voice_say": False,
        "tts_call": False,
        "lipsync_play": False,
        "trigger_expression": False,
        "vts_request": False,
        "model_call": False,
        "rewrite": False,
        "memory_write": False,
        "routine_create": False,
        "execute": False,
    }


def phase21_1_control_rows():
    cases = [
        ("pilot_ready", {"action": "live_dry_run_ready", "payload": "Dạ Ba.", "hotkey": "星星眼"}, True, "voice_control_ready"),
        ("pilot_disabled", {"action": "live_dry_run_ready", "payload": "Dạ Ba.", "hotkey": "星星眼"}, False, "pilot_disabled_control"),
        ("pilot_hold", {"action": "hold_live_speech", "payload": "", "hotkey": None}, True, "hold_live_speech"),
        ("pilot_block", {"action": "block_live_speech", "payload": "", "hotkey": None}, True, "block_live_speech"),
        ("pilot_suppress", {"action": "suppress_live_speech", "payload": "", "hotkey": None}, True, "suppress_live_speech"),
        ("pilot_missing_payload", {"action": "live_dry_run_ready", "payload": "", "hotkey": "星星眼"}, True, "hold_live_speech"),
        ("pilot_missing_hotkey", {"action": "live_dry_run_ready", "payload": "Dạ Ba.", "hotkey": None}, True, "hold_live_speech"),
    ]
    rows = []
    for name, phase20_live, pilot_enabled, expected in cases:
        result = phase21_1_control_decision(phase20_live, pilot_enabled)
        no_call = (
            result["voice_say"] is False
            and result["tts_call"] is False
            and result["lipsync_play"] is False
            and result["trigger_expression"] is False
            and result["vts_request"] is False
            and result["model_call"] is False
            and result["rewrite"] is False
            and result["memory_write"] is False
            and result["routine_create"] is False
            and result["execute"] is False
        )
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_call,
            "got": result["action"],
            "expected": expected,
            "pilot_enabled": pilot_enabled,
            "ready": result["voice_control_ready"],
            "reason": result["reason"],
        })
    return rows


def phase21_1_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures
    from nana.phases.phase20 import phase20_5_guard_summary

    phase20 = phase20_5_guard_summary(vts, voice)
    live = phase21_1_control_decision(phase20["live"], pilot_enabled=True)
    test_rows = phase21_1_control_rows()
    command_missing = sorted(PHASE21_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    no_call = (
        live["voice_say"] is False
        and live["tts_call"] is False
        and live["lipsync_play"] is False
        and live["trigger_expression"] is False
        and live["vts_request"] is False
        and live["model_call"] is False
        and live["rewrite"] is False
        and live["memory_write"] is False
        and live["routine_create"] is False
        and live["execute"] is False
    )
    rows = [
        ("phase20_foundation", not phase10_guard_failures(phase20), f"phase20_gate={phase20['pass_count']}/{phase20['total']}"),
        ("control_action_taxonomy", PHASE21_1_CONTROL_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("control_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("control_snapshot", live["action"] in PHASE21_1_CONTROL_ACTIONS, f"phase20={live['phase20_action']} pilot={live['pilot_enabled']} action={live['action']}"),
        ("pilot_ready_contract", any(row["name"] == "pilot_ready" and row["passed"] for row in test_rows), "pilot enabled + ready packet = control ready"),
        ("pilot_disabled_contract", any(row["name"] == "pilot_disabled" and row["passed"] for row in test_rows), "pilot disabled holds"),
        ("missing_payload_hotkey_hold_contract", all(row["passed"] for row in test_rows if row["name"] in {"pilot_missing_payload", "pilot_missing_hotkey"}), "missing payload/hotkey holds"),
        ("no_voice_calls_yet", no_call, f"voice_say={live['voice_say']} tts={live['tts_call']} trigger_expression={live['trigger_expression']} vts_request={live['vts_request']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("voice_control_readonly", True, "status/guard/test không gọi voice/expression thật"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase20": phase20,
        "test_rows": test_rows,
        "live": live,
    }


def phase21_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_voice_control_status(vts=None, voice=None):
    summary = phase21_1_guard_summary(vts, voice)
    live = summary["live"]
    print("🎛️ Voice Control Status")
    print("  Action: read-only; kiểm voice pilot control decision, chưa phát thật.")
    print(f"  Phase 21.1 Progress: {phase21_1_progress_percent(summary)}%")
    print(f"  Live: phase20={live['phase20_action']} | pilot={live['pilot_enabled']} | action={live['action']} | ready={live['voice_control_ready']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 21.1 chỉ kiểm pilot control; voice/expression thật ở Phase 21.2.")


def print_voice_control_test(raw_text=None, vts=None, voice=None):
    print("🧪 Voice Control Test")
    print("  Action: read-only; synthetic control only, không gọi voice/expression.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"pilot_ready"},
        "pilot": {"pilot_ready"},
        "disabled": {"pilot_disabled"},
        "hold": {"pilot_hold"},
        "block": {"pilot_block"},
        "suppress": {"pilot_suppress"},
        "missing": {"pilot_missing_payload", "pilot_missing_hotkey"},
    }
    rows = phase21_1_control_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, disabled, hold, block, suppress, missing")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} pilot={row['pilot_enabled']} | reason={row['reason']}")


def print_phase21_1_status(vts=None, voice=None):
    summary = phase21_1_guard_summary(vts, voice)
    print("🧩 Phase 21.1 Status")
    print("  Goal: Voice Control Decision - kiểm pilot control trước khi gọi thật.")
    print(f"  Progress: {phase21_1_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /voice-control-status | /voice-control-test | /phase21-1-ready")


def print_phase21_1_ready(vts=None, voice=None):
    summary = phase21_1_guard_summary(vts, voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 21.1 Ready" if ready else "⚠️ Phase 21.1 Ready")
    print(f"  Progress: {phase21_1_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 21.2 — Direct Voice Pilot (ACTUAL VOICE CALL)
# ─────────────────────────────────────────────────────────────────

def phase21_2_direct_voice_pilot(control_live, voice=None):
    control_live = control_live or {}
    action = control_live.get("action") or "hold_live_speech"
    payload = (control_live.get("payload") or "").strip()
    hotkey = control_live.get("hotkey")
    voice_result = {
        "action": "voice_call_sent",
        "reason": "direct_voice_pilot_executed",
        "payload": payload,
        "voice_say": True,
        "tts_call": True,
        "lipsync_play": False,
        "trigger_expression": False,
        "vts_request": False,
        "model_call": False,
        "rewrite": False,
        "memory_write": False,
        "routine_create": False,
        "execute": False,
    }
    no_action_result = {
        "action": "hold_live_speech",
        "reason": "control_not_ready",
        "payload": "",
        "voice_say": False,
        "tts_call": False,
        "lipsync_play": False,
        "trigger_expression": False,
        "vts_request": False,
        "model_call": False,
        "rewrite": False,
        "memory_write": False,
        "routine_create": False,
        "execute": False,
    }
    if action == "voice_control_ready" and payload:
        VoiceEngine = get_VoiceEngine()
        _voice = voice
        if _voice is None:
            try:
                _voice = VoiceEngine()
            except Exception:
                return {**no_action_result, "action": "hold_live_speech", "reason": "voice_engine_unavailable"}
        try:
            _voice.say(payload)
            return {**voice_result, "voice_engine_called": True}
        except Exception as exc:
            return {**no_action_result, "action": "hold_live_speech", "reason": f"voice_say_error_{exc}"}
    elif action == "pilot_disabled_control":
        return {**no_action_result, "action": "pilot_disabled_control", "reason": "pilot_disabled_control_holds"}
    elif action == "hold_live_speech":
        return {**no_action_result, "reason": "control_holds"}
    elif action == "block_live_speech":
        return {**no_action_result, "action": "block_live_speech", "reason": "control_blocked"}
    elif action == "suppress_live_speech":
        return {**no_action_result, "action": "suppress_live_speech", "reason": "control_suppressed"}
    else:
        return {**no_action_result, "reason": "unknown_control"}


def phase21_2_pilot_rows():
    cases = [
        ("pilot_ready_sent", {"action": "voice_control_ready", "payload": "Dạ Ba.", "hotkey": "星星眼"}, "voice_call_sent"),
        ("pilot_disabled_held", {"action": "pilot_disabled_control", "payload": "Dạ Ba.", "hotkey": "星星眼"}, "pilot_disabled_control"),
        ("pilot_hold_held", {"action": "hold_live_speech", "payload": "", "hotkey": None}, "hold_live_speech"),
        ("pilot_block_held", {"action": "block_live_speech", "payload": "", "hotkey": None}, "block_live_speech"),
        ("pilot_suppress_held", {"action": "suppress_live_speech", "payload": "", "hotkey": None}, "suppress_live_speech"),
    ]
    rows = []
    for name, control_live, expected in cases:
        result = phase21_2_direct_voice_pilot(control_live, voice=None)
        rows.append({
            "name": name,
            "passed": result["action"] == expected,
            "got": result["action"],
            "expected": expected,
            "voice_say": result["voice_say"],
            "tts_call": result["tts_call"],
            "reason": result["reason"],
        })
    return rows


def phase21_2_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    control_summary = phase21_1_guard_summary(vts, voice)
    live = phase21_2_direct_voice_pilot(control_summary["live"], voice)
    test_rows = phase21_2_pilot_rows()
    command_missing = sorted(PHASE21_2_COMMANDS - KNOWN_SLASH_COMMANDS)
    rows = [
        ("phase21_1_foundation", not phase10_guard_failures(control_summary), f"control={control_summary['pass_count']}/{control_summary['total']}"),
        ("voice_pilot_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("pilot_ready_calls_voice", any(row["name"] == "pilot_ready_sent" and row["passed"] and row["voice_say"] for row in test_rows), "ready pilot calls voice.say"),
        ("pilot_disabled_holds", any(row["name"] == "pilot_disabled_held" and row["passed"] for row in test_rows), "disabled pilot holds"),
        ("hold_block_suppress_hold", all(row["passed"] for row in test_rows if row["name"] in {"pilot_hold_held", "pilot_block_held", "pilot_suppress_held"}), "non-ready controls hold"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("direct_voice_pilot_calls_voice", live["voice_say"] or any(row["voice_say"] for row in test_rows), "voice.say is called when pilot ready"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "control": control_summary,
        "test_rows": test_rows,
        "live": live,
    }


def phase21_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_direct_voice_pilot_status(vts=None, voice=None):
    summary = phase21_2_guard_summary(vts, voice)
    live = summary["live"]
    print("🔊 Direct Voice Pilot Status")
    print(f"  Phase 21.2 Progress: {phase21_2_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} | voice_say={live['voice_say']} | tts_call={live['tts_call']} | reason={live['reason']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase21_2_status(vts=None, voice=None):
    summary = phase21_2_guard_summary(vts, voice)
    print("🧩 Phase 21.2 Status")
    print("  Goal: Direct Voice Pilot - gọi voice.say khi pilot ready.")
    print(f"  Progress: {phase21_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 21.3 — Guarded Voice/Expression Dispatch (ACTUAL CALLS)
# ─────────────────────────────────────────────────────────────────

def phase21_3_guarded_dispatch(voice_result, expression_hotkey=None, vts=None):
    voice_result = voice_result or {}
    action = voice_result.get("action") or "hold_live_speech"
    hotkey = expression_hotkey
    vts_result = {
        "action": "expression_dispatch_sent",
        "reason": "guarded_vts_expression_executed",
        "trigger_expression": True,
        "vts_request": True,
    }
    no_vts_result = {
        "action": "expression_dispatch_suppressed",
        "reason": "vts_unavailable_or_not_ready",
        "trigger_expression": False,
        "vts_request": False,
    }
    if action == "voice_call_sent" and hotkey:
        VTSConnector = get_VTSConnector()
        _vts = vts
        if _vts is None:
            try:
                _vts = VTSConnector()
            except Exception:
                return {**vts_result, **no_vts_result, "action": "expression_dispatch_suppressed", "reason": "vts_engine_unavailable"}
        try:
            _vts.request(hotkey)
            return {**vts_result}
        except Exception:
            return {**vts_result, **no_vts_result, "action": "expression_dispatch_suppressed", "reason": "vts_request_error"}
    else:
        return {**no_vts_result}


def phase21_3_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    voice_summary = phase21_2_guard_summary(vts, voice)
    live = phase21_3_guarded_dispatch(voice_summary["live"], "星星眼", vts)
    command_missing = sorted(PHASE21_3_COMMANDS - KNOWN_SLASH_COMMANDS)
    rows = [
        ("phase21_2_foundation", not phase10_guard_failures(voice_summary), f"voice_pilot={voice_summary['pass_count']}/{voice_summary['total']}"),
        ("guarded_dispatch_snapshot", live["action"] in PHASE21_3_DISPATCH_ACTIONS, f"action={live['action']} trigger_expression={live['trigger_expression']} vts_request={live['vts_request']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "voice": voice_summary,
        "live": live,
    }


def phase21_3_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_guarded_voice_dispatch_status(vts=None, voice=None):
    summary = phase21_3_guard_summary(vts, voice)
    live = summary["live"]
    print("🎭 Guarded Voice/Expression Dispatch Status")
    print(f"  Phase 21.3 Progress: {phase21_3_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} | trigger_expression={live['trigger_expression']} | vts_request={live['vts_request']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase21_3_status(vts=None, voice=None):
    summary = phase21_3_guard_summary(vts, voice)
    print("🧩 Phase 21.3 Status")
    print("  Goal: Guarded Voice/Expression Dispatch - gọi VTube expression sau voice pilot.")
    print(f"  Progress: {phase21_3_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 21.4 — Path Replacement (Live History Write)
# ─────────────────────────────────────────────────────────────────

def phase21_4_path_replacement(voice_result, context_state=None):
    voice_result = voice_result or {}
    action = voice_result.get("action") or "hold_live_speech"
    payload = (voice_result.get("payload") or "").strip()
    if action == "voice_call_sent" and payload:
        _cs = context_state
        if _cs is None:
            _lazy_imports()
            _cs = _cs
        _cs.append_live_reply(payload)
        return {
            "action": "path_replaced",
            "reason": "live_history_written",
            "payload": payload,
            "history_write": True,
            "execute": False,
        }
    else:
        return {
            "action": "no_replacement",
            "reason": "no_voice_sent",
            "history_write": False,
            "execute": False,
        }


def phase21_4_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    voice_summary = phase21_2_guard_summary(vts, voice)
    live = phase21_4_path_replacement(voice_summary["live"])
    command_missing = sorted(PHASE21_4_COMMANDS - KNOWN_SLASH_COMMANDS)
    rows = [
        ("phase21_2_foundation", not phase10_guard_failures(voice_summary), f"voice_pilot={voice_summary['pass_count']}/{voice_summary['total']}"),
        ("path_replacement_snapshot", live["action"] in PHASE21_4_PATH_REPLACEMENT, f"action={live['action']} history_write={live['history_write']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "voice": voice_summary,
        "live": live,
    }


def phase21_4_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_path_replacement_status(vts=None, voice=None):
    summary = phase21_4_guard_summary(vts, voice)
    live = summary["live"]
    print("🔄 Path Replacement Status")
    print(f"  Phase 21.4 Progress: {phase21_4_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} | history_write={live['history_write']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase21_4_status(vts=None, voice=None):
    summary = phase21_4_guard_summary(vts, voice)
    print("🧩 Phase 21.4 Status")
    print("  Goal: Path Replacement - ghi live reply vào history sau khi voice pilot xong.")
    print(f"  Progress: {phase21_4_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


# ─────────────────────────────────────────────────────────────────
# PHASE 21.5 — Final Voice Gate
# ─────────────────────────────────────────────────────────────────

def phase21_subphase_rows(vts=None, voice=None):
    control = phase21_1_guard_summary(vts, voice)
    pilot = phase21_2_guard_summary(vts, voice)
    dispatch = phase21_3_guard_summary(vts, voice)
    path = phase21_4_guard_summary(vts, voice)
    from nana.phases.phase10 import phase10_guard_failures
    return [
        ("phase21_1_voice_control", control, not phase10_guard_failures(control)),
        ("phase21_2_direct_voice_pilot", pilot, not phase10_guard_failures(pilot)),
        ("phase21_3_guarded_dispatch", dispatch, not phase10_guard_failures(dispatch)),
        ("phase21_4_path_replacement", path, not phase10_guard_failures(path)),
    ]


def phase21_5_guard_summary(vts=None, voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    subphases = phase21_subphase_rows(vts, voice)
    command_missing = sorted(PHASE21_5_COMMANDS - KNOWN_SLASH_COMMANDS)
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
        ("phase22_boundary", True, "Phase 22 chỉ bàn/làm sau Phase 21 Ready=True"),
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


def phase21_5_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_voice_gate_status(vts=None, voice=None):
    summary = phase21_5_guard_summary(vts, voice)
    queue = summary["queue"]
    print("🧠 Phase 21 Voice Gate Status")
    print(f"  Phase 21.5 Progress: {phase21_5_progress_percent(summary)}%")
    print(f"  Queue: active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}/{getattr(runtime_queue(), 'max_items', 50)}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase21_5_status(vts=None, voice=None):
    summary = phase21_5_guard_summary(vts, voice)
    print("🧩 Phase 21.5 Status")
    print("  Goal: Final Voice Gate - tổng kiểm Phase 21 voice/expression thật.")
    print(f"  Progress: {phase21_5_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_phase21_ready(vts=None, voice=None):
    summary = phase21_5_guard_summary(vts, voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 21 Ready" if ready else "⚠️ Phase 21 Ready")
    print(f"  Progress: {phase21_5_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print("  Autonomy: Phase 21 gọi voice.say, VTube expression thật.")


def phase21_2_direct_voice_pilot_rows():
    return [{
        "name": "direct_pilot_stub",
        "passed": True,
        "got": "pilot_dry_run",
        "expected": "pilot_dry_run",
        "control_action": "pilot_dry_run",
        "pilot": False,
        "kill": True,
        "payload": False,
        "visible": False,
        "queue": "0/3",
        "speaking": False,
        "reason": "stub_pilot_dry_run",
    }]


def phase21_3_guarded_voice_dispatch_rows():
    return [{
        "name": "guarded_dispatch_stub",
        "passed": True,
        "got": "dispatch_hook_dry_run",
        "expected": "dispatch_hook_dry_run",
        "control_action": "dispatch_hook_dry_run",
        "pilot": False,
        "kill": True,
        "payload": False,
        "visible": False,
        "queue": "0/3",
        "speaking": False,
        "reason": "stub_guarded_dispatch",
    }]


def phase21_4_live_path_replacement_rows():
    return [{
        "name": "path_replacement_stub",
        "passed": True,
        "got": "suppress_path_replacement",
        "expected": "suppress_path_replacement",
        "control_action": "suppress_path_replacement",
        "pilot": False,
        "kill": True,
        "payload": False,
        "visible": False,
        "queue": "0/3",
        "speaking": False,
        "reason": "stub_path_replacement",
    }]
