"""
Phase 82 — Nana Stream Persona Mode Gate.
Tách khỏi main.py (GĐ 3).
"""
from __future__ import annotations

# ── Constants (copied from main.py lines 33039-33074) ─────────────────────────

PHASE82_COMMANDS = {
    "/nana-stream-status",
    "/nana-stream-guard-status",
    "/nana-stream-test",
    "/nana-stream-mode",
    "/phase82-status",
    "/phase82-ready",
    "/phase82-guard-status",
    "/phase82-test",
    "/p82",
    "/p82-ready",
}

PHASE82_STREAM_ACTIONS = {
    "stream_persona_suppressed",
    "stream_persona_one_liner_ready",
    "stream_persona_host_ready",
    "stream_persona_hold",
    "stream_persona_block",
}
PHASE82_STREAM_MODE_ALIASES = {
    "off": "off",
    "quiet": "off",
    "none": "off",
    "light": "stream_light",
    "stream_light": "stream_light",
    "host": "stream_host",
    "stream_host": "stream_host",
}
PHASE82_STREAM_DEFAULT_MODE = "off"
PHASE82_STREAM_MAX_WORDS = {"stream_light": 16, "stream_host": 28}
PHASE82_STREAM_COOLDOWN_S = {"stream_light": 12, "stream_host": 8}
PHASE82_STREAM_TRUTH_MIN_CONFIDENCE = 0.75
PHASE82_RUNTIME_STREAM_MODE = PHASE82_STREAM_DEFAULT_MODE
PHASE82_STREAM_LAST_COMMENT_AT = 0.0
PHASE82_REQUIRED_COMMANDS = set()

# ── Helpers ────────────────────────────────────────────────────────────────────


def phase82_normalize_stream_mode(raw_mode):
    return PHASE82_STREAM_MODE_ALIASES.get((raw_mode or "").strip().lower(), PHASE82_STREAM_DEFAULT_MODE)


def phase82_build_stream_persona_gate(
    stream_mode=None,
    fact_confidence=1.0,
    grounded=True,
    recent_comment_age_s=999.0,
    foreground=True,
    boundary_action="boundary_one_liner",
    high_impact=False,
    user_is_streaming=False,
    voice_busy=False,
    requested_words=10,
):
    mode = phase82_normalize_stream_mode(stream_mode or PHASE82_RUNTIME_STREAM_MODE)
    requested_words = int(requested_words or 0)
    base = {
        "mode": mode,
        "fact_confidence": round(float(fact_confidence or 0.0), 2),
        "grounded": bool(grounded),
        "recent_comment_age_s": round(float(recent_comment_age_s or 0.0), 1),
        "foreground": bool(foreground),
        "boundary_action": boundary_action,
        "high_impact": bool(high_impact),
        "user_is_streaming": bool(user_is_streaming),
        "voice_busy": bool(voice_busy),
        "requested_words": requested_words,
        "max_words": PHASE82_STREAM_MAX_WORDS.get(mode, 0),
        "cooldown_s": PHASE82_STREAM_COOLDOWN_S.get(mode, 999),
        "keyboard_called": False,
        "dispatch": False,
        "execute": False,
        "memory_write": False,
    }
    if mode == "off" or not user_is_streaming:
        return {**base, "action": "stream_persona_suppressed", "reason": "stream_mode_off_or_not_streaming"}
    if high_impact:
        return {**base, "action": "stream_persona_block", "reason": "stream_persona_cannot_mask_high_impact_action"}
    if boundary_action not in {"boundary_one_liner", "boundary_supervised", "boundary_allow"}:
        return {**base, "action": "stream_persona_hold", "reason": "phase81_boundary_does_not_allow_stream_commentary"}
    if not foreground:
        return {**base, "action": "stream_persona_hold", "reason": "foreground_required_for_stream_persona"}
    if voice_busy:
        return {**base, "action": "stream_persona_hold", "reason": "voice_busy_holds_stream_persona"}
    if not grounded or base["fact_confidence"] < PHASE82_STREAM_TRUTH_MIN_CONFIDENCE:
        return {**base, "action": "stream_persona_hold", "reason": "stream_persona_truth_gate_not_met"}
    if base["recent_comment_age_s"] < base["cooldown_s"]:
        return {**base, "action": "stream_persona_hold", "reason": "stream_persona_cooldown_active"}
    if requested_words > base["max_words"]:
        return {**base, "action": "stream_persona_hold", "reason": "stream_persona_verbosity_budget_exceeded"}
    if mode == "stream_light":
        return {**base, "action": "stream_persona_one_liner_ready", "reason": "stream_light_grounded_one_liner_ready"}
    return {**base, "action": "stream_persona_host_ready", "reason": "stream_host_grounded_commentary_ready"}


def phase82_stream_rows():
    rows_def = [
        ("default_off_suppressed", phase82_build_stream_persona_gate("off", user_is_streaming=False), "stream_persona_suppressed"),
        ("not_streaming_suppressed", phase82_build_stream_persona_gate("stream_light", user_is_streaming=False), "stream_persona_suppressed"),
        ("light_one_liner_ready", phase82_build_stream_persona_gate("stream_light", 0.9, True, 30, True, "boundary_one_liner", False, True, False, 10), "stream_persona_one_liner_ready"),
        ("host_ready", phase82_build_stream_persona_gate("stream_host", 0.92, True, 30, True, "boundary_supervised", False, True, False, 22), "stream_persona_host_ready"),
        ("truth_hold", phase82_build_stream_persona_gate("stream_light", 0.42, True, 30, True, "boundary_one_liner", False, True, False, 10), "stream_persona_hold"),
        ("ungrounded_hold", phase82_build_stream_persona_gate("stream_light", 0.9, False, 30, True, "boundary_one_liner", False, True, False, 10), "stream_persona_hold"),
        ("cooldown_hold", phase82_build_stream_persona_gate("stream_light", 0.9, True, 3, True, "boundary_one_liner", False, True, False, 10), "stream_persona_hold"),
        ("verbosity_hold", phase82_build_stream_persona_gate("stream_light", 0.9, True, 30, True, "boundary_one_liner", False, True, False, 40), "stream_persona_hold"),
        ("foreground_hold", phase82_build_stream_persona_gate("stream_light", 0.9, True, 30, False, "boundary_one_liner", False, True, False, 10), "stream_persona_hold"),
        ("voice_busy_hold", phase82_build_stream_persona_gate("stream_light", 0.9, True, 30, True, "boundary_one_liner", False, True, True, 10), "stream_persona_hold"),
        ("boundary_hold", phase82_build_stream_persona_gate("stream_light", 0.9, True, 30, True, "boundary_suppress", False, True, False, 10), "stream_persona_hold"),
        ("high_impact_block", phase82_build_stream_persona_gate("stream_host", 0.95, True, 30, True, "boundary_supervised", True, True, False, 18), "stream_persona_block"),
    ]
    rows = []
    for name, plan, expected in rows_def:
        no_side_effect = (
            plan.get("keyboard_called") is False
            and plan.get("dispatch") is False
            and plan.get("execute") is False
            and plan.get("memory_write") is False
        )
        rows.append({
            "name": name,
            "action": plan["action"],
            "expected": expected,
            "passed": plan["action"] == expected and no_side_effect,
            "reason": plan["reason"],
            "mode": plan.get("mode"),
            "confidence": plan.get("fact_confidence"),
            "grounded": plan.get("grounded"),
            "words": plan.get("requested_words"),
            "max_words": plan.get("max_words"),
            "dispatch": plan.get("dispatch"),
            "execute": plan.get("execute"),
        })
    return rows


def phase82_live_stream_plan():
    from nana.phases.phase81 import phase81_build_boundary_decision
    boundary = phase81_build_boundary_decision(
        "stream_persona",
        "stream.commentary",
        "stream_context",
        "runtime_ephemeral",
        PHASE82_RUNTIME_STREAM_MODE,
        0.9,
    )
    return phase82_build_stream_persona_gate(
        stream_mode=PHASE82_RUNTIME_STREAM_MODE,
        fact_confidence=0.9,
        grounded=True,
        recent_comment_age_s=999.0,
        foreground=True,
        boundary_action=boundary["action"],
        high_impact=False,
        user_is_streaming=PHASE82_RUNTIME_STREAM_MODE != "off",
        voice_busy=False,
        requested_words=10,
    )


# ── Guard ──────────────────────────────────────────────────────────────────────


def phase82_guard_summary(vts=None, voice=None):
    from nana.main import (
        PHASE9_AUDIT_LOG,
        RUNTIME_EVENT_LOG,
        KNOWN_SLASH_COMMANDS,
        memory_governance_summary,
        runtime_queue,
        phase_progress_percent,
    )
    from nana.phases.phase81 import phase81_guard_summary

    phase81 = phase81_guard_summary(vts, voice)
    live_plan = phase82_live_stream_plan()
    stream_rows = phase82_stream_rows()
    actions = {row["action"] for row in stream_rows}
    command_missing = sorted(PHASE82_REQUIRED_COMMANDS - KNOWN_SLASH_COMMANDS)
    memory_snapshot = memory_governance_summary().get("snapshot") or {}
    queue = runtime_queue.snapshot()
    event_executed = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
    audit_executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    no_side_effect = all(not row["dispatch"] and row["execute"] is False for row in stream_rows)
    rows = [
        ("phase81_foundation", not phase81["failures"], f"nana_boundary={phase81['pass_count']}/{phase81['total']}"),
        ("stream_action_taxonomy", PHASE82_STREAM_ACTIONS <= actions, f"covered={','.join(sorted(actions))}"),
        ("stream_regression", all(row["passed"] for row in stream_rows), f"{sum(1 for row in stream_rows if row['passed'])}/{len(stream_rows)} pass"),
        ("live_stream_snapshot", live_plan["action"] in PHASE82_STREAM_ACTIONS, f"action={live_plan['action']} mode={live_plan.get('mode')}"),
        ("default_off_contract", any(row["name"] == "default_off_suppressed" and row["passed"] for row in stream_rows) and PHASE82_STREAM_DEFAULT_MODE == "off", "stream persona default off"),
        ("not_streaming_contract", any(row["name"] == "not_streaming_suppressed" and row["passed"] for row in stream_rows), "stream mode alone is not enough without streaming context"),
        ("light_budget_contract", any(row["name"] == "light_one_liner_ready" and row["passed"] for row in stream_rows), f"light max={PHASE82_STREAM_MAX_WORDS['stream_light']} words"),
        ("host_budget_contract", any(row["name"] == "host_ready" and row["passed"] for row in stream_rows), f"host max={PHASE82_STREAM_MAX_WORDS['stream_host']} words"),
        ("truth_gate_contract", all(any(row["name"] == name and row["passed"] for row in stream_rows) for name in {"truth_hold", "ungrounded_hold"}), f"min_confidence={PHASE82_STREAM_TRUTH_MIN_CONFIDENCE}"),
        ("cooldown_contract", any(row["name"] == "cooldown_hold" and row["passed"] for row in stream_rows), "commentary has cooldown"),
        ("verbosity_contract", any(row["name"] == "verbosity_hold" and row["passed"] for row in stream_rows), "commentary cannot become chatter spam"),
        ("focus_voice_contract", all(any(row["name"] == name and row["passed"] for row in stream_rows) for name in {"foreground_hold", "voice_busy_hold"}), "focus/voice busy hold stream persona"),
        ("boundary_contract", any(row["name"] == "boundary_hold" and row["passed"] for row in stream_rows), "Phase 81 boundary must allow commentary"),
        ("high_impact_contract", any(row["name"] == "high_impact_block" and row["passed"] for row in stream_rows), "persona cannot mask high-impact actions"),
        ("readonly_contract", no_side_effect, "Phase 82 stream gate does not speak/write/dispatch"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("memory_no_write_guard", not memory_snapshot.get("pending"), f"pending={'yes' if memory_snapshot.get('pending') else 'none'} long={memory_snapshot.get('long_term', 0)}"),
        ("pending_queue_clear", not queue.get("queued") and not queue.get("active_p0"), f"active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}"),
        ("execute_flag_guard", len(event_executed) + len(audit_executed) == 0, f"event_execute={len(event_executed)} audit_execute={len(audit_executed)}"),
        ("phase83_boundary", True, "Phase 83 mới priority router giữa companion/game/stream/core"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "phase81": phase81,
        "live_plan": live_plan,
        "stream_rows": stream_rows,
    }


# ── Print helpers ──────────────────────────────────────────────────────────────


def print_nana_stream_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase82_guard_summary(vts, voice)
    live = summary["live_plan"]
    print("🎙️ Nana Stream Persona Status")
    print("  Action: stream persona mode gate; status không nói/không ghi memory.")
    print(f"  Phase 82 Progress: {phase_progress_percent(summary)}%")
    print(f"  Runtime mode: {PHASE82_RUNTIME_STREAM_MODE}")
    print(f"  Live: action={live['action']} reason={live['reason']}")
    print(f"  Budget: words={live.get('requested_words')}/{live.get('max_words')} cooldown={live.get('cooldown_s')}s confidence={live.get('fact_confidence')}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'fail'} | {detail}")
    print("  Rule: không stream thì im; stream thì ngắn, grounded, cooldown, và không che action nguy hiểm.")


def print_nana_stream_test(raw_text=None, vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase82_guard_summary(vts, voice)
    rows = summary["stream_rows"]
    arg = (raw_text or "").strip().lower()
    print("🧪 Nana Stream Persona Test")
    print("  Action: synthetic stream persona gate only; không nói/không ghi memory/không dispatch.")
    if arg in {"live", "state"}:
        live = summary["live_plan"]
        print("  Section: live stream persona")
        print(f"  Action: {live['action']} | reason={live['reason']}")
        print(f"  Mode: {live.get('mode')} | words={live.get('requested_words')}/{live.get('max_words')} | confidence={live.get('fact_confidence')}")
        print("  Voice: False | Dispatch: False | Execute: False")
        return
    aliases = {
        "off": {"default_off_suppressed", "not_streaming_suppressed"},
        "ready": {"light_one_liner_ready", "host_ready"},
        "truth": {"truth_hold", "ungrounded_hold"},
        "cooldown": {"cooldown_hold"},
        "verbosity": {"verbosity_hold"},
        "focus": {"foreground_hold", "voice_busy_hold"},
        "boundary": {"boundary_hold", "high_impact_block"},
        "danger": {"high_impact_block"},
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
            f"mode={row['mode']} words={row['words']}/{row['max_words']} confidence={row['confidence']} "
            f"dispatch={row['dispatch']} execute={row['execute']} | reason={row['reason']}"
        )
    print("  Execute: False")


def print_nana_stream_guard_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase82_guard_summary(vts, voice)
    print("🧪 Phase 82 Nana Stream Persona Guard")
    print("  Action: tổng kiểm stream persona gate; guard không nói/không input.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Stream regression:")
    for row in summary["stream_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} mode={row['mode']} reason={row['reason']}")


def print_phase82_status(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase82_guard_summary(vts, voice)
    print("🧩 Phase 82 Status")
    print("  Goal: Nana Stream Persona Mode Gate - dẫn stream có kiểm soát, ngoài stream thì im.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print("  Companion Overall: lane-separated foundation")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'fail'} | {detail}")
    print("  Commands: /nana-stream-status | /nana-stream-test | /nana-stream-guard-status | /phase82-ready")


def print_phase82_ready(vts=None, voice=None):
    from nana.main import phase_progress_percent
    summary = phase82_guard_summary(vts, voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 82 Ready" if ready else "⚠️ Phase 82 Ready")
    print("  Goal: stream persona gate đủ sạch để làm priority router/game+stream tự nhiên hơn.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print("  Companion Overall: stream persona default-off")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: nana_stream_persona={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: policy-only; không nói thật, không memory write, không dispatch.")
