"""
Phase 19 — Live Reply Permission + Subphases 19.1–19.5.
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


PHASE19_1_COMMANDS = {
    "/live-reply-status",
    "/live-reply-guard-status",
    "/live-reply-test",
    "/phase19-1-status",
    "/phase19-1-ready",
    "/phase19-1-guard-status",
    "/phase19-1-test",
    "/p19-1",
    "/p19-1-ready",
}

PHASE19_1_REPLY_ACTIONS = {"block_speech", "hold_reply", "reply_allowed", "suppress_speech"}

PHASE19_2_COMMANDS = {
    "/speech-dispatch-status",
    "/speech-dispatch-guard-status",
    "/speech-dispatch-test",
    "/phase19-2-status",
    "/phase19-2-ready",
    "/phase19-2-guard-status",
    "/phase19-2-test",
    "/p19-2",
    "/p19-2-ready",
}

PHASE19_2_DISPATCH_ACTIONS = {"block_dispatch", "dispatch_preview", "hold_dispatch", "suppress_dispatch"}

PHASE19_3_COMMANDS = {
    "/reply-bridge-status",
    "/reply-bridge-guard-status",
    "/reply-bridge-test",
    "/phase19-3-status",
    "/phase19-3-ready",
    "/phase19-3-guard-status",
    "/phase19-3-test",
    "/p19-3",
    "/p19-3-ready",
}

PHASE19_3_BRIDGE_ACTIONS = {"block_handoff", "handoff_ready", "hold_handoff", "suppress_handoff"}

PHASE19_4_COMMANDS = {
    "/reply-cooldown-status",
    "/reply-cooldown-guard-status",
    "/reply-cooldown-test",
    "/phase19-4-status",
    "/phase19-4-ready",
    "/phase19-4-guard-status",
    "/phase19-4-test",
    "/p19-4",
    "/p19-4-ready",
}

PHASE19_4_COOLDOWN_ACTIONS = {"allow_handoff", "block_handoff", "hold_handoff", "suppress_handoff"}
PHASE19_4_MIN_GAP_SECONDS = 2.0
PHASE19_4_BURST_LIMIT = 3

PHASE19_5_COMMANDS = {
    "/live-reply-gate-status",
    "/live-reply-gate-guard-status",
    "/live-reply-gate-test",
    "/phase19-status",
    "/phase19-ready",
    "/phase19-5-status",
    "/phase19-5-ready",
    "/phase19-5-guard-status",
    "/phase19-5-test",
    "/p19",
    "/p19-5",
    "/p19-5-ready",
    "/p19-ready",
}

PHASE19_5_GATE_ACTIONS = {"block_live", "hold_live", "ready_for_voice_binding", "suppress_live"}


# Lazy imports to avoid circular dependency at module level
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
# PHASE 19.1 — Live Reply Permission
# ─────────────────────────────────────────────────────────────────

PHASE19_1_CASES = [
    ("direct_user_reply", "preview_ready", "direct_user_turn", True, False, "reply_allowed"),
    ("quiet_context", "quiet_ready", "direct_user_turn", True, False, "suppress_speech"),
    ("preview_without_user_turn", "preview_ready", "autonomous_turn", False, False, "hold_reply"),
    ("hold_review", "hold_ready", "direct_user_turn", True, False, "hold_reply"),
    ("blocked_reply", "blocked_ready", "direct_user_turn", True, False, "block_speech"),
    ("flow_suppression", "preview_ready", "direct_user_turn", True, True, "suppress_speech"),
]


def phase19_1_live_reply_permission(live_readiness, turn_kind="direct_user_turn", user_initiated=True, focus_protected=False):
    readiness = (live_readiness or {}).get("action") or "quiet_ready"
    visible = bool((live_readiness or {}).get("visible_preview"))
    preview_text = (live_readiness or {}).get("preview_text") or ""
    if readiness == "blocked_ready":
        action = "block_speech"
        reason = "blocked_readiness_no_speech"
    elif focus_protected or readiness == "quiet_ready":
        action = "suppress_speech"
        reason = "focus_or_quiet_suppresses_speech"
    elif readiness == "hold_ready":
        action = "hold_reply"
        reason = "readiness_requires_review"
    elif readiness == "preview_ready" and user_initiated and turn_kind == "direct_user_turn" and visible and preview_text:
        action = "reply_allowed"
        reason = "direct_user_turn_preview_allowed"
    elif readiness == "preview_ready":
        action = "hold_reply"
        reason = "preview_without_direct_user_turn_holds"
    else:
        action = "hold_reply"
        reason = "unknown_readiness_holds"
    return {
        "action": action,
        "reason": reason,
        "readiness": readiness,
        "turn_kind": turn_kind,
        "user_initiated": bool(user_initiated),
        "focus_protected": bool(focus_protected),
        "visible_preview": visible,
        "preview_text": preview_text if action == "reply_allowed" else "",
        "speech_allowed": action == "reply_allowed",
        "model_call": False,
        "rewrite": False,
        "memory_write": False,
        "routine_create": False,
        "execute": False,
    }


def phase19_1_live_reply_rows():
    rows = []
    for name, readiness, turn_kind, user_initiated, focus_protected, expected in PHASE19_1_CASES:
        live = {
            "action": readiness,
            "visible_preview": readiness == "preview_ready",
            "preview_text": "Dạ Ba." if readiness == "preview_ready" else "",
        }
        result = phase19_1_live_reply_permission(live, turn_kind, user_initiated, focus_protected)
        no_effect = (
            result["model_call"] is False
            and result["rewrite"] is False
            and result["memory_write"] is False
            and result["routine_create"] is False
            and result["execute"] is False
        )
        leak_ok = result["action"] == "reply_allowed" or result["preview_text"] == ""
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_effect and leak_ok,
            "got": result["action"],
            "expected": expected,
            "readiness": readiness,
            "turn_kind": turn_kind,
            "speech_allowed": result["speech_allowed"],
            "reason": result["reason"],
        })
    return rows


def phase19_1_guard_summary(voice=None):
    from nana.phases.phase18 import phase18_4_guard_summary
    from nana.phases.phase10 import phase10_guard_failures

    phase18_summary = phase18_4_guard_summary(voice)
    live = phase19_1_live_reply_permission(phase18_summary["live"])
    test_rows = phase19_1_live_reply_rows()
    command_missing = sorted(PHASE19_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    no_effect_ok = (
        live["model_call"] is False
        and live["rewrite"] is False
        and live["memory_write"] is False
        and live["routine_create"] is False
        and live["execute"] is False
    )
    speech_boundary_ok = True
    if live["action"] != "reply_allowed":
        speech_boundary_ok = live["speech_allowed"] is False and not live["preview_text"]
    rows = [
        ("phase18_foundation", not phase10_guard_failures(phase18_summary), f"phase18_gate={phase18_summary['pass_count']}/{phase18_summary['total']}"),
        ("reply_action_taxonomy", PHASE19_1_REPLY_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("live_reply_permission_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_reply_snapshot", live["action"] in PHASE19_1_REPLY_ACTIONS, f"readiness={live['readiness']} action={live['action']} speech_allowed={live['speech_allowed']}"),
        ("direct_user_turn_contract", any(row["name"] == "direct_user_reply" and row["passed"] for row in test_rows), "only direct user turn may allow reply"),
        ("quiet_suppression_contract", any(row["name"] == "quiet_context" and row["passed"] for row in test_rows), "quiet readiness suppresses speech"),
        ("no_ambient_autospeech", any(row["name"] == "preview_without_user_turn" and row["passed"] for row in test_rows), "preview without direct user turn holds"),
        ("hold_block_contract", all(row["passed"] for row in test_rows if row["name"] in {"hold_review", "blocked_reply"}), "hold/block never speak"),
        ("focus_suppression_contract", any(row["name"] == "flow_suppression" and row["passed"] for row in test_rows), "focus protection suppresses speech"),
        ("speech_boundary_guard", speech_boundary_ok, f"speech_allowed={live['speech_allowed']} preview={'yes' if live['preview_text'] else 'none'}"),
        ("no_model_rewrite_memory_routine", no_effect_ok, f"model={live['model_call']} rewrite={live['rewrite']} memory_write={live['memory_write']} routine={live['routine_create']} execute={live['execute']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("live_reply_readonly", True, "status/guard/test không phát lời thật, không gọi model, không execute"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase18": phase18_summary,
    }


def phase19_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_live_reply_status(voice=None):
    summary = phase19_1_guard_summary(voice)
    live = summary["live"]
    print("🗣️ Live Reply Permission Status")
    print("  Action: read-only; phân quyền phát lời theo lượt người dùng, không phát lời thật.")
    print(f"  Phase 19.1 Progress: {phase19_1_progress_percent(summary)}%")
    print(f"  Live: readiness={live['readiness']} | action={live['action']} | speech_allowed={live['speech_allowed']}")
    print(f"  Turn: {live['turn_kind']} | user_initiated={live['user_initiated']} | focus_protected={live['focus_protected']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 19.1 chỉ mở permission cho direct user turn; chưa bật ambient/self speech.")


def print_live_reply_test(raw_text=None, voice=None):
    print("🧪 Live Reply Permission Test")
    print("  Action: read-only; synthetic permission only, không phát lời/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "direct": {"direct_user_reply"},
        "reply": {"direct_user_reply"},
        "quiet": {"quiet_context"},
        "ambient": {"preview_without_user_turn"},
        "hold": {"hold_review"},
        "block": {"blocked_reply"},
        "focus": {"flow_suppression"},
    }
    rows = phase19_1_live_reply_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: direct, quiet, ambient, hold, block, focus")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | speech={row['speech_allowed']} | reason={row['reason']}")
    print("  Execute: False")


def print_live_reply_guard_status(voice=None):
    summary = phase19_1_guard_summary(voice)
    print("🧪 Phase 19.1 Live Reply Permission Guard")
    print("  Action: read-only; kiểm live reply permission, không phát lời/không execute.")
    print(f"  Progress: {phase19_1_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Permission regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} speech={row['speech_allowed']} reason={row['reason']}")


def print_phase19_1_status(voice=None):
    summary = phase19_1_guard_summary(voice)
    print("🧩 Phase 19.1 Status")
    print("  Goal: Live Reply Permission Baseline - chỉ direct user turn mới được phép reply.")
    print(f"  Progress: {phase19_1_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /live-reply-status | /live-reply-test | /live-reply-guard-status | /phase19-1-ready")


def print_phase19_1_ready(voice=None):
    summary = phase19_1_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 19.1 Ready" if ready else "⚠️ Phase 19.1 Ready")
    print("  Goal: live reply permission đủ chắc để sang Phase 19.2 speech dispatch dry-run.")
    print(f"  Progress: {phase19_1_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: live_reply={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 19.1 chưa bật tự nói, chỉ permission read-only.")


# ─────────────────────────────────────────────────────────────────
# PHASE 19.2 — Speech Dispatch Dry-Run
# ─────────────────────────────────────────────────────────────────

PHASE19_2_CASES = [
    ("reply_preview_packet", "reply_allowed", "Dạ Ba.", "dispatch_preview"),
    ("quiet_suppress_packet", "suppress_speech", "", "suppress_dispatch"),
    ("hold_packet", "hold_reply", "", "hold_dispatch"),
    ("block_packet", "block_speech", "", "block_dispatch"),
    ("allowed_empty_hold", "reply_allowed", "", "hold_dispatch"),
    ("unsafe_payload_block", "reply_allowed", "Con có ý thức thật rồi.", "block_dispatch"),
]


def phase19_2_speech_dispatch_packet(permission_live, payload_text=None):
    from nana.phases.phase16 import phase16_4_reflection_safety_review

    permission_live = permission_live or {}
    permission = permission_live.get("action") or "suppress_speech"
    text = (payload_text if payload_text is not None else permission_live.get("preview_text") or "").strip()
    if permission == "block_speech":
        action = "block_dispatch"
        reason = "permission_blocks_speech"
        packet_text = ""
    elif permission == "suppress_speech":
        action = "suppress_dispatch"
        reason = "permission_suppresses_speech"
        packet_text = ""
    elif permission == "hold_reply":
        action = "hold_dispatch"
        reason = "permission_holds_reply"
        packet_text = ""
    elif permission == "reply_allowed":
        review = phase16_4_reflection_safety_review(text or "Dạ Ba.", "grounded_reflection")
        if not text:
            action = "hold_dispatch"
            reason = "empty_payload_holds"
            packet_text = ""
        elif review.get("status") == "block":
            action = "block_dispatch"
            reason = "safety_blocks_dispatch_payload"
            packet_text = ""
        elif review.get("status") == "hold":
            action = "hold_dispatch"
            reason = "payload_needs_review"
            packet_text = ""
        else:
            action = "dispatch_preview"
            reason = "dry_run_packet_ready"
            packet_text = text
    else:
        action = "hold_dispatch"
        reason = "unknown_permission_holds"
        packet_text = ""
    return {
        "action": action,
        "reason": reason,
        "permission": permission,
        "packet_text": packet_text,
        "has_payload": bool(packet_text),
        "dispatch": False,
        "tts_call": False,
        "voice_call": False,
        "vts_trigger": False,
        "expression_trigger": False,
        "model_call": False,
        "memory_write": False,
        "execute": False,
    }


def phase19_2_dispatch_rows():
    rows = []
    for name, permission, payload, expected in PHASE19_2_CASES:
        result = phase19_2_speech_dispatch_packet({"action": permission, "preview_text": payload}, payload)
        no_effect = (
            result["dispatch"] is False
            and result["tts_call"] is False
            and result["voice_call"] is False
            and result["vts_trigger"] is False
            and result["expression_trigger"] is False
            and result["model_call"] is False
            and result["memory_write"] is False
            and result["execute"] is False
        )
        payload_contract = result["action"] == "dispatch_preview" or not result["packet_text"]
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_effect and payload_contract,
            "got": result["action"],
            "expected": expected,
            "permission": permission,
            "has_payload": result["has_payload"],
            "reason": result["reason"],
        })
    return rows


def phase19_2_guard_summary(voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    phase19_1 = phase19_1_guard_summary(voice)
    live = phase19_2_speech_dispatch_packet(phase19_1["live"])
    test_rows = phase19_2_dispatch_rows()
    command_missing = sorted(PHASE19_2_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    no_effect_ok = (
        live["dispatch"] is False
        and live["tts_call"] is False
        and live["voice_call"] is False
        and live["vts_trigger"] is False
        and live["expression_trigger"] is False
        and live["model_call"] is False
        and live["memory_write"] is False
        and live["execute"] is False
    )
    no_leak_ok = live["action"] == "dispatch_preview" or not live["packet_text"]
    rows = [
        ("phase19_1_foundation", not phase10_guard_failures(phase19_1), f"live_reply={phase19_1['pass_count']}/{phase19_1['total']}"),
        ("dispatch_action_taxonomy", PHASE19_2_DISPATCH_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("speech_dispatch_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_dispatch_snapshot", live["action"] in PHASE19_2_DISPATCH_ACTIONS, f"permission={live['permission']} action={live['action']} payload={live['has_payload']}"),
        ("reply_packet_contract", any(row["name"] == "reply_preview_packet" and row["passed"] for row in test_rows), "reply_allowed creates dry-run packet only"),
        ("quiet_suppress_contract", any(row["name"] == "quiet_suppress_packet" and row["passed"] for row in test_rows), "suppressed speech produces no packet text"),
        ("hold_block_contract", all(row["passed"] for row in test_rows if row["name"] in {"hold_packet", "block_packet"}), "hold/block no packet text"),
        ("empty_payload_hold_contract", any(row["name"] == "allowed_empty_hold" and row["passed"] for row in test_rows), "empty allowed payload holds"),
        ("unsafe_payload_block_contract", any(row["name"] == "unsafe_payload_block" and row["passed"] for row in test_rows), "unsafe payload blocks"),
        ("no_dispatch_side_effects", no_effect_ok, f"dispatch={live['dispatch']} tts={live['tts_call']} voice={live['voice_call']} vts={live['vts_trigger']} expression={live['expression_trigger']} execute={live['execute']}"),
        ("no_leak_without_dispatch_preview", no_leak_ok, f"action={live['action']} payload={'yes' if live['packet_text'] else 'none'}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("speech_dispatch_readonly", True, "status/guard/test không gọi TTS, không trigger VTube/expression, không phát lời"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase19_1": phase19_1,
    }


def phase19_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_speech_dispatch_status(voice=None):
    summary = phase19_2_guard_summary(voice)
    live = summary["live"]
    print("🔈 Speech Dispatch Dry-Run Status")
    print("  Action: read-only; dựng speech dispatch packet mô phỏng, không phát lời.")
    print(f"  Phase 19.2 Progress: {phase19_2_progress_percent(summary)}%")
    print(f"  Live: permission={live['permission']} | action={live['action']} | payload={live['has_payload']}")
    print(f"  Calls: dispatch={live['dispatch']} tts={live['tts_call']} voice={live['voice_call']} vts={live['vts_trigger']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 19.2 chỉ dry-run dispatch; chưa gọi TTS/VTube/expression thật.")


def print_speech_dispatch_test(raw_text=None, voice=None):
    print("🧪 Speech Dispatch Dry-Run Test")
    print("  Action: read-only; synthetic dispatch only, không phát lời/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "reply": {"reply_preview_packet"},
        "packet": {"reply_preview_packet"},
        "quiet": {"quiet_suppress_packet"},
        "suppress": {"quiet_suppress_packet"},
        "hold": {"hold_packet", "allowed_empty_hold"},
        "block": {"block_packet", "unsafe_payload_block"},
        "unsafe": {"unsafe_payload_block"},
        "empty": {"allowed_empty_hold"},
    }
    rows = phase19_2_dispatch_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: reply, quiet, hold, block, unsafe, empty")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | payload={row['has_payload']} | reason={row['reason']}")
    print("  Execute: False")


def print_speech_dispatch_guard_status(voice=None):
    summary = phase19_2_guard_summary(voice)
    print("🧪 Phase 19.2 Speech Dispatch Dry-Run Guard")
    print("  Action: read-only; kiểm dispatch dry-run, không gọi TTS/VTube/execute.")
    print(f"  Progress: {phase19_2_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Dispatch regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} payload={row['has_payload']} reason={row['reason']}")


def print_phase19_2_status(voice=None):
    summary = phase19_2_guard_summary(voice)
    print("🧩 Phase 19.2 Status")
    print("  Goal: Speech Dispatch Dry-Run - mô phỏng gửi speech nhưng không phát thật.")
    print(f"  Progress: {phase19_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /speech-dispatch-status | /speech-dispatch-test | /speech-dispatch-guard-status | /phase19-2-ready")


def print_phase19_2_ready(voice=None):
    summary = phase19_2_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 19.2 Ready" if ready else "⚠️ Phase 19.2 Ready")
    print("  Goal: speech dispatch dry-run đủ chắc để sang Phase 19.3 real reply bridge.")
    print(f"  Progress: {phase19_2_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: speech_dispatch={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 19.2 không gọi TTS/VTube/expression thật.")


# ─────────────────────────────────────────────────────────────────
# PHASE 19.3 — Reply Bridge
# ─────────────────────────────────────────────────────────────────

PHASE19_3_CASES = [
    ("dispatch_ready", "dispatch_preview", "Dạ Ba.", "handoff_ready"),
    ("dispatch_suppress", "suppress_dispatch", "", "suppress_handoff"),
    ("dispatch_hold", "hold_dispatch", "", "hold_handoff"),
    ("dispatch_block", "block_dispatch", "", "block_handoff"),
    ("dispatch_empty_hold", "dispatch_preview", "", "hold_handoff"),
    ("dispatch_unsafe_block", "dispatch_preview", "Con có ý thức thật rồi.", "block_handoff"),
]


def phase19_3_reply_bridge(dispatch_live, payload_text=None):
    from nana.phases.phase16 import phase16_4_reflection_safety_review

    dispatch_live = dispatch_live or {}
    dispatch_action = dispatch_live.get("action") or "suppress_dispatch"
    text = (payload_text if payload_text is not None else dispatch_live.get("packet_text") or "").strip()
    if dispatch_action == "block_dispatch":
        action = "block_handoff"
        reason = "dispatch_blocked"
        handoff_text = ""
    elif dispatch_action == "suppress_dispatch":
        action = "suppress_handoff"
        reason = "dispatch_suppressed"
        handoff_text = ""
    elif dispatch_action == "hold_dispatch":
        action = "hold_handoff"
        reason = "dispatch_holds"
        handoff_text = ""
    elif dispatch_action == "dispatch_preview":
        review = phase16_4_reflection_safety_review(text or "Dạ Ba.", "grounded_reflection")
        if not text:
            action = "hold_handoff"
            reason = "empty_handoff_payload"
            handoff_text = ""
        elif review.get("status") == "block":
            action = "block_handoff"
            reason = "safety_blocks_handoff"
            handoff_text = ""
        elif review.get("status") == "hold":
            action = "hold_handoff"
            reason = "handoff_needs_review"
            handoff_text = ""
        else:
            action = "handoff_ready"
            reason = "reply_bridge_ready"
            handoff_text = text
    else:
        action = "hold_handoff"
        reason = "unknown_dispatch_holds"
        handoff_text = ""
    return {
        "action": action,
        "reason": reason,
        "dispatch_action": dispatch_action,
        "handoff_text": handoff_text,
        "has_handoff": bool(handoff_text),
        "handoff": False,
        "dispatch": False,
        "tts_call": False,
        "voice_call": False,
        "vts_trigger": False,
        "expression_trigger": False,
        "model_call": False,
        "memory_write": False,
        "execute": False,
    }


def phase19_3_bridge_rows():
    rows = []
    for name, dispatch_action, payload, expected in PHASE19_3_CASES:
        result = phase19_3_reply_bridge({"action": dispatch_action, "packet_text": payload}, payload)
        no_effect = (
            result["handoff"] is False
            and result["dispatch"] is False
            and result["tts_call"] is False
            and result["voice_call"] is False
            and result["vts_trigger"] is False
            and result["expression_trigger"] is False
            and result["model_call"] is False
            and result["memory_write"] is False
            and result["execute"] is False
        )
        payload_contract = result["action"] == "handoff_ready" or not result["handoff_text"]
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_effect and payload_contract,
            "got": result["action"],
            "expected": expected,
            "dispatch_action": dispatch_action,
            "has_handoff": result["has_handoff"],
            "reason": result["reason"],
        })
    return rows


def phase19_3_guard_summary(voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    phase19_2 = phase19_2_guard_summary(voice)
    live = phase19_3_reply_bridge(phase19_2["live"])
    test_rows = phase19_3_bridge_rows()
    command_missing = sorted(PHASE19_3_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    no_effect_ok = (
        live["handoff"] is False
        and live["dispatch"] is False
        and live["tts_call"] is False
        and live["voice_call"] is False
        and live["vts_trigger"] is False
        and live["expression_trigger"] is False
        and live["model_call"] is False
        and live["memory_write"] is False
        and live["execute"] is False
    )
    no_leak_ok = live["action"] == "handoff_ready" or not live["handoff_text"]
    rows = [
        ("phase19_2_foundation", not phase10_guard_failures(phase19_2), f"speech_dispatch={phase19_2['pass_count']}/{phase19_2['total']}"),
        ("bridge_action_taxonomy", PHASE19_3_BRIDGE_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("reply_bridge_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_reply_bridge_snapshot", live["action"] in PHASE19_3_BRIDGE_ACTIONS, f"dispatch={live['dispatch_action']} bridge={live['action']} handoff={live['has_handoff']}"),
        ("handoff_ready_contract", any(row["name"] == "dispatch_ready" and row["passed"] for row in test_rows), "dispatch_preview can become handoff_ready only"),
        ("suppress_hold_block_contract", all(row["passed"] for row in test_rows if row["name"] in {"dispatch_suppress", "dispatch_hold", "dispatch_block"}), "suppress/hold/block no handoff text"),
        ("empty_handoff_hold_contract", any(row["name"] == "dispatch_empty_hold" and row["passed"] for row in test_rows), "empty dispatch_preview holds"),
        ("unsafe_handoff_block_contract", any(row["name"] == "dispatch_unsafe_block" and row["passed"] for row in test_rows), "unsafe handoff blocks"),
        ("no_real_handoff_side_effects", no_effect_ok, f"handoff={live['handoff']} dispatch={live['dispatch']} tts={live['tts_call']} vts={live['vts_trigger']} expression={live['expression_trigger']} execute={live['execute']}"),
        ("no_leak_without_handoff_ready", no_leak_ok, f"action={live['action']} handoff={'yes' if live['handoff_text'] else 'none'}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("reply_bridge_readonly", True, "status/guard/test không gọi TTS/VTube/expression, không phát lời thật"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase19_2": phase19_2,
    }


def phase19_3_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_reply_bridge_status(voice=None):
    summary = phase19_3_guard_summary(voice)
    live = summary["live"]
    print("🌉 Reply Bridge Status")
    print("  Action: read-only; nối dry-run packet thành handoff preview, không phát lời thật.")
    print(f"  Phase 19.3 Progress: {phase19_3_progress_percent(summary)}%")
    print(f"  Live: dispatch={live['dispatch_action']} | bridge={live['action']} | handoff={live['has_handoff']}")
    print(f"  Calls: handoff={live['handoff']} tts={live['tts_call']} voice={live['voice_call']} vts={live['vts_trigger']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 19.3 chỉ bridge preview; chưa dispatch/TTS/VTube thật.")


def print_reply_bridge_test(raw_text=None, voice=None):
    print("🧪 Reply Bridge Test")
    print("  Action: read-only; synthetic bridge only, không phát lời/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"dispatch_ready"},
        "handoff": {"dispatch_ready"},
        "suppress": {"dispatch_suppress"},
        "hold": {"dispatch_hold", "dispatch_empty_hold"},
        "block": {"dispatch_block", "dispatch_unsafe_block"},
        "unsafe": {"dispatch_unsafe_block"},
        "empty": {"dispatch_empty_hold"},
    }
    rows = phase19_3_bridge_rows()
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
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | handoff={row['has_handoff']} | reason={row['reason']}")
    print("  Execute: False")


def print_reply_bridge_guard_status(voice=None):
    summary = phase19_3_guard_summary(voice)
    print("🧪 Phase 19.3 Reply Bridge Guard")
    print("  Action: read-only; kiểm real reply bridge preview, không gọi TTS/VTube/execute.")
    print(f"  Progress: {phase19_3_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Bridge regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} handoff={row['has_handoff']} reason={row['reason']}")


def print_phase19_3_status(voice=None):
    summary = phase19_3_guard_summary(voice)
    print("🧩 Phase 19.3 Status")
    print("  Goal: Real Reply Bridge - nối dry-run packet sang handoff preview, chưa phát thật.")
    print(f"  Progress: {phase19_3_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /reply-bridge-status | /reply-bridge-test | /reply-bridge-guard-status | /phase19-3-ready")


def print_phase19_3_ready(voice=None):
    summary = phase19_3_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 19.3 Ready" if ready else "⚠️ Phase 19.3 Ready")
    print("  Goal: real reply bridge đủ chắc để sang Phase 19.4 cooldown/repeat guard.")
    print(f"  Progress: {phase19_3_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: reply_bridge={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 19.3 không dispatch/TTS/VTube thật.")


# ─────────────────────────────────────────────────────────────────
# PHASE 19.4 — Reply Cooldown/Repeat Guard
# ─────────────────────────────────────────────────────────────────

PHASE19_4_CASES = [
    ("fresh_handoff", "handoff_ready", "Dạ Ba.", 10.0, 0, "Khác câu", False, "allow_handoff"),
    ("cooldown_suppress", "handoff_ready", "Dạ Ba.", 0.5, 0, "Khác câu", False, "suppress_handoff"),
    ("repeat_suppress", "handoff_ready", "Dạ Ba.", 10.0, 0, "Dạ Ba.", False, "suppress_handoff"),
    ("burst_hold", "handoff_ready", "Dạ Ba.", 10.0, 3, "Khác câu", False, "hold_handoff"),
    ("focus_suppress", "handoff_ready", "Dạ Ba.", 10.0, 0, "Khác câu", True, "suppress_handoff"),
    ("bridge_hold", "hold_handoff", "", 10.0, 0, "Khác câu", False, "hold_handoff"),
    ("bridge_block", "block_handoff", "", 10.0, 0, "Khác câu", False, "block_handoff"),
]


def phase19_4_reply_cooldown_decision(bridge_live, since_last=999.0, burst_count=0, last_text="", focus_protected=False):
    bridge_live = bridge_live or {}
    bridge_action = bridge_live.get("action") or "suppress_handoff"
    text = (bridge_live.get("handoff_text") or "").strip()
    last_text = (last_text or "").strip()
    if bridge_action == "block_handoff":
        action = "block_handoff"
        reason = "bridge_blocked"
        output_text = ""
    elif bridge_action == "hold_handoff":
        action = "hold_handoff"
        reason = "bridge_holds"
        output_text = ""
    elif bridge_action == "suppress_handoff":
        action = "suppress_handoff"
        reason = "bridge_suppressed"
        output_text = ""
    elif focus_protected:
        action = "suppress_handoff"
        reason = "focus_protection_suppresses_live_reply"
        output_text = ""
    elif not text:
        action = "hold_handoff"
        reason = "empty_handoff_holds"
        output_text = ""
    elif float(since_last or 0.0) < PHASE19_4_MIN_GAP_SECONDS:
        action = "suppress_handoff"
        reason = "cooldown_gap_suppressed"
        output_text = ""
    elif burst_count >= PHASE19_4_BURST_LIMIT:
        action = "hold_handoff"
        reason = "burst_limit_holds"
        output_text = ""
    elif last_text and text == last_text:
        action = "suppress_handoff"
        reason = "repeat_text_suppressed"
        output_text = ""
    else:
        action = "allow_handoff"
        reason = "cooldown_allows_handoff"
        output_text = text
    return {
        "action": action,
        "reason": reason,
        "bridge_action": bridge_action,
        "since_last": float(since_last or 0.0),
        "burst_count": int(burst_count or 0),
        "focus_protected": bool(focus_protected),
        "output_text": output_text,
        "visible": bool(output_text),
        "history_write": False,
        "dispatch": False,
        "tts_call": False,
        "voice_call": False,
        "vts_trigger": False,
        "expression_trigger": False,
        "model_call": False,
        "memory_write": False,
        "execute": False,
    }


def phase19_4_cooldown_rows():
    rows = []
    for name, bridge_action, text, since_last, burst_count, last_text, focus_protected, expected in PHASE19_4_CASES:
        result = phase19_4_reply_cooldown_decision(
            {"action": bridge_action, "handoff_text": text},
            since_last,
            burst_count,
            last_text,
            focus_protected,
        )
        no_effect = (
            result["history_write"] is False
            and result["dispatch"] is False
            and result["tts_call"] is False
            and result["voice_call"] is False
            and result["vts_trigger"] is False
            and result["expression_trigger"] is False
            and result["model_call"] is False
            and result["memory_write"] is False
            and result["execute"] is False
        )
        leak_ok = result["action"] == "allow_handoff" or not result["output_text"]
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_effect and leak_ok,
            "got": result["action"],
            "expected": expected,
            "bridge_action": bridge_action,
            "visible": result["visible"],
            "reason": result["reason"],
        })
    return rows


def phase19_4_guard_summary(voice=None):
    from nana.phases.phase10 import phase10_guard_failures
    from nana.phases.phase12 import phase12_1_attention_state, phase12_1_live_attention_context, recovery_snapshot

    phase19_3 = phase19_3_guard_summary(voice)
    attention = phase12_1_attention_state(phase12_1_live_attention_context(), recovery_snapshot())
    focus_protected = attention.get("state") in {"deep_work", "debugging", "game_focus", "away"}
    live = phase19_4_reply_cooldown_decision(phase19_3["live"], since_last=999.0, burst_count=0, last_text="", focus_protected=focus_protected)
    test_rows = phase19_4_cooldown_rows()
    command_missing = sorted(PHASE19_4_COMMANDS - KNOWN_SLASH_COMMANDS)
    actions_covered = {row["got"] for row in test_rows}
    no_effect_ok = (
        live["history_write"] is False
        and live["dispatch"] is False
        and live["tts_call"] is False
        and live["voice_call"] is False
        and live["vts_trigger"] is False
        and live["expression_trigger"] is False
        and live["model_call"] is False
        and live["memory_write"] is False
        and live["execute"] is False
    )
    no_leak_ok = live["action"] == "allow_handoff" or not live["output_text"]
    rows = [
        ("phase19_3_foundation", not phase10_guard_failures(phase19_3), f"reply_bridge={phase19_3['pass_count']}/{phase19_3['total']}"),
        ("cooldown_action_taxonomy", PHASE19_4_COOLDOWN_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("cooldown_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("live_cooldown_snapshot", live["action"] in PHASE19_4_COOLDOWN_ACTIONS, f"bridge={live['bridge_action']} action={live['action']} focus={live['focus_protected']}"),
        ("fresh_handoff_contract", any(row["name"] == "fresh_handoff" and row["passed"] for row in test_rows), "fresh handoff can allow"),
        ("cooldown_repeat_contract", all(row["passed"] for row in test_rows if row["name"] in {"cooldown_suppress", "repeat_suppress"}), "cooldown/repeat suppress"),
        ("burst_hold_contract", any(row["name"] == "burst_hold" and row["passed"] for row in test_rows), "burst limit holds"),
        ("focus_suppression_contract", any(row["name"] == "focus_suppress" and row["passed"] for row in test_rows), "focus protection suppresses"),
        ("bridge_hold_block_contract", all(row["passed"] for row in test_rows if row["name"] in {"bridge_hold", "bridge_block"}), "bridge hold/block preserved"),
        ("no_history_or_dispatch_side_effects", no_effect_ok, f"history={live['history_write']} dispatch={live['dispatch']} tts={live['tts_call']} vts={live['vts_trigger']} execute={live['execute']}"),
        ("no_leak_unless_allowed", no_leak_ok, f"action={live['action']} visible={live['visible']}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("reply_cooldown_readonly", True, "status/guard/test không ghi history thật, không dispatch/TTS/VTube"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": test_rows,
        "live": live,
        "phase19_3": phase19_3,
        "attention": attention,
    }


def phase19_4_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_reply_cooldown_status(voice=None):
    summary = phase19_4_guard_summary(voice)
    live = summary["live"]
    print("⏱️ Reply Cooldown Status")
    print("  Action: read-only; kiểm cooldown/repeat cho live reply, không phát lời.")
    print(f"  Phase 19.4 Progress: {phase19_4_progress_percent(summary)}%")
    print(f"  Live: bridge={live['bridge_action']} | action={live['action']} | visible={live['visible']} | focus={live['focus_protected']}")
    print(f"  Cooldown: min_gap={PHASE19_4_MIN_GAP_SECONDS:g}s | burst_limit={PHASE19_4_BURST_LIMIT}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 19.4 chống spam/lặp; chưa ghi history thật hoặc phát lời.")


def print_reply_cooldown_test(raw_text=None, voice=None):
    print("🧪 Reply Cooldown Test")
    print("  Action: read-only; synthetic cooldown only, không phát lời/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "fresh": {"fresh_handoff"},
        "allow": {"fresh_handoff"},
        "cooldown": {"cooldown_suppress"},
        "repeat": {"repeat_suppress"},
        "burst": {"burst_hold"},
        "focus": {"focus_suppress"},
        "hold": {"bridge_hold"},
        "block": {"bridge_block"},
    }
    rows = phase19_4_cooldown_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: fresh, cooldown, repeat, burst, focus, hold, block")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | visible={row['visible']} | reason={row['reason']}")
    print("  Execute: False")


def print_reply_cooldown_guard_status(voice=None):
    summary = phase19_4_guard_summary(voice)
    print("🧪 Phase 19.4 Reply Cooldown Guard")
    print("  Action: read-only; kiểm cooldown/repeat, không dispatch/TTS/execute.")
    print(f"  Progress: {phase19_4_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Cooldown regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} visible={row['visible']} reason={row['reason']}")


def print_phase19_4_status(voice=None):
    summary = phase19_4_guard_summary(voice)
    print("🧩 Phase 19.4 Status")
    print("  Goal: Cooldown/Repeat Guard - chống spam/lặp trước live reply gate.")
    print(f"  Progress: {phase19_4_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /reply-cooldown-status | /reply-cooldown-test | /reply-cooldown-guard-status | /phase19-4-ready")


def print_phase19_4_ready(voice=None):
    summary = phase19_4_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 19.4 Ready" if ready else "⚠️ Phase 19.4 Ready")
    print("  Goal: cooldown/repeat guard đủ chắc để sang Phase 19.5 final live reply gate.")
    print(f"  Progress: {phase19_4_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: reply_cooldown={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 19.4 không phát lời, không ghi history thật.")


# ─────────────────────────────────────────────────────────────────
# PHASE 19.5 — Final Live Reply Gate
# ─────────────────────────────────────────────────────────────────

def phase19_subphase_rows(voice=None):
    phase19_1 = phase19_1_guard_summary(voice)
    phase19_2 = phase19_2_guard_summary(voice)
    phase19_3 = phase19_3_guard_summary(voice)
    phase19_4 = phase19_4_guard_summary(voice)
    rows = [
        ("phase19_1_live_reply", phase19_1, "live_reply"),
        ("phase19_2_speech_dispatch", phase19_2, "speech_dispatch"),
        ("phase19_3_reply_bridge", phase19_3, "reply_bridge"),
        ("phase19_4_reply_cooldown", phase19_4, "reply_cooldown"),
    ]
    from nana.phases.phase10 import phase10_guard_failures
    return [
        {
            "name": name,
            "passed": not phase10_guard_failures(summary),
            "detail": f"{label}={summary['pass_count']}/{summary['total']} pass",
            "summary": summary,
        }
        for name, summary, label in rows
    ]


def phase19_5_live_gate_decision(cooldown_live):
    cooldown_live = cooldown_live or {}
    action = cooldown_live.get("action") or "suppress_handoff"
    text = (cooldown_live.get("output_text") or "").strip()
    if action == "block_handoff":
        gate = "block_live"
        reason = "cooldown_blocked_live"
        output_text = ""
    elif action == "hold_handoff":
        gate = "hold_live"
        reason = "cooldown_holds_live"
        output_text = ""
    elif action == "suppress_handoff":
        gate = "suppress_live"
        reason = "cooldown_suppresses_live"
        output_text = ""
    elif action == "allow_handoff" and text:
        gate = "ready_for_voice_binding"
        reason = "ready_for_phase20_voice_binding"
        output_text = text
    else:
        gate = "hold_live"
        reason = "empty_or_unknown_live_holds"
        output_text = ""
    return {
        "action": gate,
        "reason": reason,
        "cooldown_action": action,
        "output_text": output_text,
        "visible": bool(output_text),
        "voice_binding_ready": gate == "ready_for_voice_binding",
        "speech_now": False,
        "dispatch": False,
        "tts_call": False,
        "voice_call": False,
        "vts_trigger": False,
        "expression_trigger": False,
        "model_call": False,
        "rewrite": False,
        "memory_write": False,
        "routine_create": False,
        "execute": False,
    }


def phase19_5_gate_rows():
    cases = [
        ("ready_for_voice", {"action": "allow_handoff", "output_text": "Dạ Ba."}, "ready_for_voice_binding"),
        ("suppress_live", {"action": "suppress_handoff", "output_text": ""}, "suppress_live"),
        ("hold_live", {"action": "hold_handoff", "output_text": ""}, "hold_live"),
        ("block_live", {"action": "block_handoff", "output_text": ""}, "block_live"),
        ("empty_allowed_hold", {"action": "allow_handoff", "output_text": ""}, "hold_live"),
    ]
    rows = []
    for name, cooldown_live, expected in cases:
        result = phase19_5_live_gate_decision(cooldown_live)
        no_effect = (
            result["speech_now"] is False
            and result["dispatch"] is False
            and result["tts_call"] is False
            and result["voice_call"] is False
            and result["vts_trigger"] is False
            and result["expression_trigger"] is False
            and result["model_call"] is False
            and result["rewrite"] is False
            and result["memory_write"] is False
            and result["routine_create"] is False
            and result["execute"] is False
        )
        leak_ok = result["action"] == "ready_for_voice_binding" or not result["output_text"]
        rows.append({
            "name": name,
            "passed": result["action"] == expected and no_effect and leak_ok,
            "got": result["action"],
            "expected": expected,
            "cooldown_action": cooldown_live.get("action"),
            "visible": result["visible"],
            "reason": result["reason"],
        })
    return rows


def phase19_5_guard_summary(voice=None):
    from nana.phases.phase10 import phase10_guard_failures

    subphases = phase19_subphase_rows(voice)
    cooldown_summary = subphases[3]["summary"]
    live = phase19_5_live_gate_decision(cooldown_summary["live"])
    test_rows = phase19_5_gate_rows()
    command_missing = sorted(PHASE19_5_COMMANDS - KNOWN_SLASH_COMMANDS)
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
    execute_total = len(event_executed) + len(audit_executed)
    no_effect_ok = (
        live["speech_now"] is False
        and live["dispatch"] is False
        and live["tts_call"] is False
        and live["voice_call"] is False
        and live["vts_trigger"] is False
        and live["expression_trigger"] is False
        and live["model_call"] is False
        and live["rewrite"] is False
        and live["memory_write"] is False
        and live["routine_create"] is False
        and live["execute"] is False
    )
    no_leak_ok = live["action"] == "ready_for_voice_binding" or not live["output_text"]
    rows = [
        ("subphase_closure", all(row["passed"] for row in subphases), f"{sum(1 for row in subphases if row['passed'])}/{len(subphases)} pass"),
        ("live_reply_gate_pipeline", live["action"] in PHASE19_5_GATE_ACTIONS, f"cooldown={live['cooldown_action']} gate={live['action']} voice_ready={live['voice_binding_ready']}"),
        ("live_reply_ready", subphases[0]["passed"], subphases[0]["detail"]),
        ("speech_dispatch_ready", subphases[1]["passed"], subphases[1]["detail"]),
        ("reply_bridge_ready", subphases[2]["passed"], subphases[2]["detail"]),
        ("reply_cooldown_ready", subphases[3]["passed"], subphases[3]["detail"]),
        ("live_gate_regression", all(row["passed"] for row in test_rows), f"{sum(1 for row in test_rows if row['passed'])}/{len(test_rows)} pass"),
        ("gate_action_taxonomy", PHASE19_5_GATE_ACTIONS <= actions_covered, f"covered={','.join(sorted(actions_covered))}"),
        ("pending_queue_clear", not pending_details, "none" if not pending_details else ",".join(pending_details)),
        ("execute_flag_guard", execute_total == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'} long={memory_snapshot.get('long_term', 0)}"),
        ("no_voice_dispatch_yet", no_effect_ok, f"speech_now={live['speech_now']} dispatch={live['dispatch']} tts={live['tts_call']} vts={live['vts_trigger']} expression={live['expression_trigger']} execute={live['execute']}"),
        ("no_leak_unless_voice_ready", no_leak_ok, f"action={live['action']} visible={live['visible']}"),
        ("autonomy_lock_contract", True, f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase20_boundary", True, "Phase 20 chỉ bàn/làm sau Phase 19 Ready=True; Phase 19 chưa gọi voice/VTube thật"),
        ("live_reply_gate_readonly", True, "status/guard/test không phát lời, không gọi TTS/VTube, không execute"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "subphases": subphases,
        "test_rows": test_rows,
        "live": live,
        "queue": queue,
    }


def phase19_5_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_live_reply_gate_status(voice=None):
    summary = phase19_5_guard_summary(voice)
    live = summary["live"]
    queue = summary["queue"]
    print("🧠 Phase 19 Live Reply Gate Status")
    print("  Action: read-only; tổng kiểm live reply gate, chưa gọi voice/VTube.")
    print(f"  Phase 19.5 Progress: {phase19_5_progress_percent(summary)}%")
    print(f"  Live: cooldown={live['cooldown_action']} | gate={live['action']} | voice_ready={live['voice_binding_ready']} | visible={live['visible']}")
    print(f"  Queue: active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}/{getattr(runtime_queue(), 'max_items', 50)}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 19 chỉ chuẩn bị live reply; voice/expression thật sang Phase 20.")


def print_live_reply_gate_test(raw_text=None, voice=None):
    print("🧪 Live Reply Gate Test")
    print("  Action: read-only; synthetic final gate only, không phát lời/không execute.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"ready_for_voice"},
        "voice": {"ready_for_voice"},
        "suppress": {"suppress_live"},
        "hold": {"hold_live", "empty_allowed_hold"},
        "block": {"block_live"},
        "empty": {"empty_allowed_hold"},
    }
    rows = phase19_5_gate_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, suppress, hold, block, empty")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | gate={row['got']} expected={row['expected']} | visible={row['visible']} | reason={row['reason']}")
    print("  Execute: False")


def print_live_reply_gate_guard_status(voice=None):
    summary = phase19_5_guard_summary(voice)
    print("🧪 Phase 19.5 Live Reply Gate Guard")
    print("  Action: read-only; tổng kiểm Phase 19, không gọi TTS/VTube/execute.")
    print(f"  Progress: {phase19_5_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Subphases:")
    for row in summary["subphases"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")
    print("  Live gate regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | gate={row['got']} reason={row['reason']}")


def print_phase19_5_status(voice=None):
    summary = phase19_5_guard_summary(voice)
    print("🧩 Phase 19.5 Status")
    print("  Goal: Final Live Reply Gate - chốt live reply trước Phase 20 voice/expression.")
    print(f"  Progress: {phase19_5_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /live-reply-gate-status | /live-reply-gate-test | /live-reply-gate-guard-status | /phase19-ready")


def print_phase19_ready(voice=None):
    summary = phase19_5_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 19 Ready" if ready else "⚠️ Phase 19 Ready")
    print("  Goal: Phase 19 live reply gate đủ sạch để bắt đầu Phase 20 voice/expression binding.")
    print(f"  Progress: {phase19_5_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: phase19_gate={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 19 không tự nói, chưa gọi voice/VTube thật.")
