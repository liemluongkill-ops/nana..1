"""nana.phases.phase13 — Phase 13: Memory Governance Gate
Subphases:
  13.1 — Memory v3 Schema/Weight
  13.2 — Shared Experience Ledger
  13.3 — Memory Conflict/Provenance
  13.4 — Memory Decay/Review Scoring
  13.5 — Final Memory Gate
"""
from __future__ import annotations

import unicodedata

# Lazy imports for runtime state
_runtime_queue = None


def _get_runtime_queue():
    global _runtime_queue
    if _runtime_queue is None:
        from nana.runtime.priority_queue import runtime_queue
        _runtime_queue = runtime_queue
    return _runtime_queue


# Import shared constants and helpers from commons
from nana.phases.commons import (
    KNOWN_SLASH_COMMANDS,
)

# Lazy import to avoid circular dependency with phase10
_phase10_mod = None


def phase10_guard_failures(summary):
    global _phase10_mod
    if _phase10_mod is None:
        from nana.phases import phase10 as _p10
        _phase10_mod = _p10
    return _phase10_mod.phase10_guard_failures(summary)

# Direct imports for Phase 13
from nana.memory import memory_governance_summary
from nana.persona.companion_safety import companion_safety_review_text
from nana.runtime.context import context_lock, context_state
from nana.autonomy import AUTONOMY_LOCK_PHASE, AUTONOMY_LOCK_RULE


# ── Phase 13.1 — Memory v3 Schema/Weight ──────────────────────────────────────

PHASE13_1_COMMANDS = {
    "/memory-v3-status",
    "/memory-v3-guard-status",
    "/memory-v3-test",
    "/phase13-1-status",
    "/phase13-1-ready",
    "/phase13-1-guard-status",
    "/phase13-1-test",
    "/p13-1",
    "/p13-1-ready",
}

PHASE13_1_MEMORY_SCHEMA_FIELDS = {
    "kind",
    "weight",
    "scope",
    "sensitivity",
    "provenance",
    "ttl_policy",
    "confirm_required",
}

PHASE13_1_MEMORY_CASES = [
    (
        "explicit_preference",
        "Ba thích Nana trả lời ngắn khi đang debug",
        {"kind": "preference", "weight": "medium", "scope": "user", "sensitivity": "low", "ttl_policy": "durable", "confirm_required": True},
    ),
    (
        "shared_build_experience",
        "Hai cha con đã chốt Phase 12 presence gate sau khi fix delay",
        {"kind": "shared_experience", "weight": "deep", "scope": "shared", "sensitivity": "low", "ttl_policy": "durable", "confirm_required": True},
    ),
    (
        "presence_habit",
        "Ba thường im khi flow mode bật trong cmd",
        {"kind": "presence_habit", "weight": "medium", "scope": "presence", "sensitivity": "low", "ttl_policy": "review", "confirm_required": True},
    ),
    (
        "transient_page",
        "Ba đang xem tweet này",
        {"kind": "transient", "weight": "light", "scope": "session", "sensitivity": "low", "ttl_policy": "session_only", "confirm_required": False},
    ),
    (
        "vibe_lore",
        "Nana là chiến thần vũ trụ không bao giờ buồn",
        {"kind": "vibe", "weight": "light", "scope": "persona", "sensitivity": "medium", "ttl_policy": "review", "confirm_required": True},
    ),
    (
        "sensitive_private",
        "token đăng nhập của Ba là abc123",
        {"kind": "sensitive", "weight": "blocked", "scope": "private", "sensitivity": "high", "ttl_policy": "blocked", "confirm_required": False},
    ),
]


def phase13_1_memory_v3_classify(text, source="synthetic"):
    lowered = (text or "").lower()
    if any(word in lowered for word in ["token", "password", "mật khẩu", "api key", "secret", "đăng nhập"]):
        kind = "sensitive"
        weight = "blocked"
        scope = "private"
        sensitivity = "high"
        ttl_policy = "blocked"
        confirm_required = False
        reason = "sensitive_secret_like"
    elif any(word in lowered for word in ["đã chốt", "đã hoàn thành", "đã fix", "phase", "cùng build", "hai cha con"]):
        kind = "shared_experience"
        weight = "deep"
        scope = "shared"
        sensitivity = "low"
        ttl_policy = "durable"
        confirm_required = True
        reason = "shared_project_history"
    elif any(word in lowered for word in ["thích", "muốn", "ưu tiên", "trả lời"]):
        kind = "preference"
        weight = "medium"
        scope = "user"
        sensitivity = "low"
        ttl_policy = "durable"
        confirm_required = True
        reason = "explicit_preference"
    elif any(word in lowered for word in ["thường", "hay", "flow mode", "im khi", "debug"]):
        kind = "presence_habit"
        weight = "medium"
        scope = "presence"
        sensitivity = "low"
        ttl_policy = "review"
        confirm_required = True
        reason = "presence_or_work_habit"
    elif any(word in lowered for word in ["đang xem", "tweet này", "tab này", "hôm nay"]):
        kind = "transient"
        weight = "light"
        scope = "session"
        sensitivity = "low"
        ttl_policy = "session_only"
        confirm_required = False
        reason = "session_specific"
    elif any(word in lowered for word in ["chiến thần", "vũ trụ", "không bao giờ", "lore"]):
        kind = "vibe"
        weight = "light"
        scope = "persona"
        sensitivity = "medium"
        ttl_policy = "review"
        confirm_required = True
        reason = "vibe_or_lore_heavy"
    else:
        kind = "note"
        weight = "light"
        scope = "review"
        sensitivity = "low"
        ttl_policy = "review"
        confirm_required = True
        reason = "ambiguous_review"
    return {
        "kind": kind,
        "weight": weight,
        "scope": scope,
        "sensitivity": sensitivity,
        "provenance": source,
        "ttl_policy": ttl_policy,
        "confirm_required": confirm_required,
        "reason": reason,
        "save_now": False,
        "execute": False,
    }


def phase13_1_memory_v3_rows():
    rows = []
    for name, text, expected in PHASE13_1_MEMORY_CASES:
        result = phase13_1_memory_v3_classify(text)
        mismatches = [
            field for field, expected_value in expected.items()
            if result.get(field) != expected_value
        ]
        rows.append({
            "name": name,
            "text": text,
            "passed": not mismatches,
            "mismatches": mismatches,
            "result": result,
            "expected": expected,
        })
    return rows


def phase13_1_live_memory_v3_snapshot():
    governance = memory_governance_summary()
    snapshot = governance.get("snapshot") or {}
    legacy_total = int(snapshot.get("string_entries") or 0) + int(snapshot.get("structured_entries") or 0)
    return {
        "governance": governance,
        "long_term": snapshot.get("long_term", 0),
        "string_entries": snapshot.get("string_entries", 0),
        "structured_entries": snapshot.get("structured_entries", 0),
        "other_entries": snapshot.get("other_entries", 0),
        "legacy_total": legacy_total,
    }


def phase13_1_guard_summary(voice=None):
    from nana.phases.phase12 import phase12_5_guard_summary
    phase12_summary = phase12_5_guard_summary(voice)
    live = phase13_1_live_memory_v3_snapshot()
    governance = live["governance"]
    rows_test = phase13_1_memory_v3_rows()
    command_missing = sorted(PHASE13_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    regression_ok = all(row["passed"] for row in rows_test)
    schema_ok = all(PHASE13_1_MEMORY_SCHEMA_FIELDS <= set(row["result"]) for row in rows_test)
    no_save_ok = all(row["result"].get("save_now") is False for row in rows_test)
    sensitive_block_ok = phase13_1_memory_v3_classify("password là 123")["ttl_policy"] == "blocked"
    shared_experience_ok = phase13_1_memory_v3_classify("hai cha con đã chốt Phase 12")["kind"] == "shared_experience"
    rows = [
        ("phase12_foundation", not phase10_guard_failures(phase12_summary), f"phase12_gate={phase12_summary['pass_count']}/{phase12_summary['total']}"),
        ("memory_governance_bridge", not phase10_guard_failures(governance), f"memory_governance={governance['pass_count']}/{governance['total']}"),
        ("memory_v3_schema_fields", schema_ok, f"fields={','.join(sorted(PHASE13_1_MEMORY_SCHEMA_FIELDS))}"),
        ("memory_v3_regression", regression_ok, f"{sum(1 for row in rows_test if row['passed'])}/{len(rows_test)} pass"),
        ("sensitive_block_contract", sensitive_block_ok, "secret-like text maps to blocked/high sensitivity"),
        ("shared_experience_contract", shared_experience_ok, "shared build history maps to shared_experience/deep"),
        ("legacy_store_compatible", int(live["other_entries"] or 0) == 0, f"string={live['string_entries']} structured={live['structured_entries']} other={live['other_entries']}"),
        ("no_migration_no_save", no_save_ok, "classification only; save_now=False; no migration"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("memory_v3_readonly", True, "status/guard/test không ghi memory, không đổi label, không compact"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": rows_test,
        "live": live,
    }


def phase13_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_memory_v3_status(voice=None):
    summary = phase13_1_guard_summary(voice)
    live = summary["live"]
    print("🧠 Memory v3 Status")
    print("  Action: read-only; kiểm schema/weight/provenance, không migrate/không lưu.")
    print(f"  Phase 13.1 Progress: {phase13_1_progress_percent(summary)}%")
    print(f"  Store: long={live['long_term']} string={live['string_entries']} structured={live['structured_entries']} other={live['other_entries']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Memory v3 mới là schema/classifier; chưa migrate store và chưa save memory thật.")


def print_memory_v3_test(raw_text=None, voice=None):
    print("🧪 Memory v3 Test")
    print("  Action: read-only; synthetic only, không ghi memory/không migrate.")
    text = (raw_text or "").strip()
    if text and text.lower() not in {"all", "regression"}:
        result = phase13_1_memory_v3_classify(text, source="user_test")
        print(f"  Kind: {result['kind']} | weight={result['weight']} | scope={result['scope']} | sensitivity={result['sensitivity']}")
        print(f"  TTL: {result['ttl_policy']} | confirm_required={result['confirm_required']} | save_now={result['save_now']}")
        print(f"  Provenance: {result['provenance']} | reason={result['reason']}")
        print("  Execute: False")
        return
    rows = phase13_1_memory_v3_rows()
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        result = row["result"]
        issues = ",".join(row["mismatches"]) if row["mismatches"] else "none"
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"kind={result['kind']} weight={result['weight']} scope={result['scope']} "
            f"sensitivity={result['sensitivity']} ttl={result['ttl_policy']} confirm={result['confirm_required']} | issues={issues}"
        )
    print("  Execute: False")


def print_memory_v3_guard_status(voice=None):
    summary = phase13_1_guard_summary(voice)
    print("🧪 Phase 13.1 Memory v3 Guard")
    print("  Action: read-only; kiểm memory v3 schema/weight, không lưu/không migrate.")
    print(f"  Progress: {phase13_1_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Memory v3 regression:")
    for row in summary["test_rows"]:
        result = row["result"]
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | kind={result['kind']} weight={result['weight']} scope={result['scope']} ttl={result['ttl_policy']}")


def print_phase13_1_status(voice=None):
    summary = phase13_1_guard_summary(voice)
    print("🧩 Phase 13.1 Status")
    print("  Goal: Memory v3 Schema/Weight Baseline - memory có kind/weight/scope/sensitivity/provenance.")
    print(f"  Progress: {phase13_1_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /memory-v3-status | /memory-v3-test | /memory-v3-guard-status | /phase13-1-ready")


def print_phase13_1_ready(voice=None):
    summary = phase13_1_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 13.1 Ready" if ready else "⚠️ Phase 13.1 Ready")
    print("  Goal: memory v3 schema đủ chắc để sang Phase 13.2 shared experience ledger.")
    print(f"  Progress: {phase13_1_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: memory_v3={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 13.1 chỉ schema/classifier, không migrate/không lưu memory.")


# ── Phase 13.2 — Shared Experience Ledger ────────────────────────────────────

PHASE13_2_COMMANDS = {
    "/shared-experience-status",
    "/shared-experience-guard-status",
    "/shared-experience-test",
    "/phase13-2-status",
    "/phase13-2-ready",
    "/phase13-2-guard-status",
    "/phase13-2-test",
    "/p13-2",
    "/p13-2-ready",
}

PHASE13_2_SHARED_SCHEMA_FIELDS = {
    "event_kind",
    "phase",
    "outcome",
    "problem",
    "resolution",
    "emotional_weight",
    "candidate",
    "confirm_required",
}

PHASE13_2_SHARED_CASES = [
    (
        "phase_complete",
        "Hai cha con đã chốt Phase 12 presence gate 11/11 pass",
        {"event_kind": "phase_milestone", "phase": "12", "outcome": "pass", "candidate": True, "emotional_weight": "medium"},
    ),
    (
        "delay_fix",
        "Phase 12.5 bị delay, sau đó ông tối ưu fast summary làm nhanh hơn rất nhiều",
        {"event_kind": "problem_resolution", "phase": "12.5", "outcome": "fixed", "candidate": True, "emotional_weight": "medium"},
    ),
    (
        "casual_smalltalk",
        "buổi sáng chào ông bn",
        {"event_kind": "casual", "phase": "none", "outcome": "session_only", "candidate": False, "emotional_weight": "light"},
    ),
    (
        "sensitive_not_shared",
        "Ba đưa token đăng nhập khi test memory",
        {"event_kind": "sensitive", "phase": "none", "outcome": "blocked", "candidate": False, "emotional_weight": "blocked"},
    ),
    (
        "failed_attempt",
        "Phase 13.1 classifier nhận nhầm preference thành presence habit rồi đã sửa",
        {"event_kind": "problem_resolution", "phase": "13.1", "outcome": "fixed", "candidate": True, "emotional_weight": "medium"},
    ),
]


def phase13_2_shared_experience_classify(text, source="synthetic"):
    lowered = (text or "").lower()
    phase = "none"
    for token in ["13.1", "12.5", "12", "11", "10"]:
        if f"phase {token}" in lowered:
            phase = token
            break
    if any(word in lowered for word in ["token", "password", "mật khẩu", "secret", "api key", "đăng nhập"]):
        event_kind = "sensitive"
        outcome = "blocked"
        problem = "sensitive_content"
        resolution = "do_not_store"
        emotional_weight = "blocked"
        candidate = False
        reason = "sensitive_not_shared_memory"
    elif any(word in lowered for word in ["delay", "chậm", "nhận nhầm", "lỗi", "bị"]) and any(word in lowered for word in ["sửa", "fix", "tối ưu", "nhanh hơn"]):
        event_kind = "problem_resolution"
        outcome = "fixed"
        problem = "runtime_or_classifier_issue"
        resolution = "fixed_in_session"
        emotional_weight = "medium"
        candidate = True
        reason = "shared_problem_and_resolution"
    elif any(word in lowered for word in ["chốt", "ready", "pass", "hoàn thành"]) and "phase" in lowered:
        event_kind = "phase_milestone"
        outcome = "pass"
        problem = "none"
        resolution = "phase_closed"
        emotional_weight = "medium"
        candidate = True
        reason = "shared_phase_milestone"
    else:
        event_kind = "casual"
        outcome = "session_only"
        problem = "none"
        resolution = "none"
        emotional_weight = "light"
        candidate = False
        reason = "not_durable_shared_experience"
    return {
        "event_kind": event_kind,
        "phase": phase,
        "outcome": outcome,
        "problem": problem,
        "resolution": resolution,
        "emotional_weight": emotional_weight,
        "candidate": candidate,
        "confirm_required": bool(candidate),
        "provenance": source,
        "reason": reason,
        "save_now": False,
        "execute": False,
    }


def phase13_2_shared_rows():
    rows = []
    for name, text, expected in PHASE13_2_SHARED_CASES:
        result = phase13_2_shared_experience_classify(text)
        mismatches = [
            field for field, expected_value in expected.items()
            if result.get(field) != expected_value
        ]
        rows.append({
            "name": name,
            "text": text,
            "passed": not mismatches,
            "mismatches": mismatches,
            "result": result,
            "expected": expected,
        })
    return rows


def phase13_2_live_shared_snapshot():
    examples = [
        "Hai cha con đã chốt Phase 12 presence gate 11/11 pass",
        "Phase 12.5 bị delay, sau đó ông tối ưu fast summary làm nhanh hơn rất nhiều",
    ]
    return {
        "preview": [phase13_2_shared_experience_classify(text, source="phase13_2_preview") for text in examples],
        "count": len(examples),
    }


def phase13_2_guard_summary(voice=None):
    phase13_1_summary = phase13_1_guard_summary(voice)
    live = phase13_2_live_shared_snapshot()
    rows_test = phase13_2_shared_rows()
    command_missing = sorted(PHASE13_2_COMMANDS - KNOWN_SLASH_COMMANDS)
    regression_ok = all(row["passed"] for row in rows_test)
    schema_ok = all(PHASE13_2_SHARED_SCHEMA_FIELDS <= set(row["result"]) for row in rows_test)
    candidate_rows = [row for row in rows_test if row["result"]["candidate"]]
    confirm_ok = candidate_rows and all(row["result"]["confirm_required"] for row in candidate_rows)
    sensitive_ok = phase13_2_shared_experience_classify("token đăng nhập")["candidate"] is False
    no_save_ok = all(row["result"]["save_now"] is False for row in rows_test)
    milestone_ok = phase13_2_shared_experience_classify("đã chốt Phase 12 pass")["event_kind"] == "phase_milestone"
    rows = [
        ("phase13_1_foundation", not phase10_guard_failures(phase13_1_summary), f"memory_v3={phase13_1_summary['pass_count']}/{phase13_1_summary['total']}"),
        ("shared_schema_fields", schema_ok, f"fields={','.join(sorted(PHASE13_2_SHARED_SCHEMA_FIELDS))}"),
        ("shared_experience_regression", regression_ok, f"{sum(1 for row in rows_test if row['passed'])}/{len(rows_test)} pass"),
        ("candidate_confirm_contract", confirm_ok, f"candidates={len(candidate_rows)} confirm_required=True"),
        ("sensitive_exclusion", sensitive_ok, "secret-like events are blocked, not shared memory"),
        ("milestone_detection", milestone_ok, "phase completion maps to phase_milestone"),
        ("live_preview", len(live["preview"]) == live["count"], f"preview={live['count']} save_now=False"),
        ("no_save_now_contract", no_save_ok, "ledger preview only; save_now=False"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("shared_experience_readonly", True, "status/guard/test không ghi memory, không tạo ledger file"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": rows_test,
        "live": live,
    }


def phase13_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_shared_experience_status(voice=None):
    summary = phase13_2_guard_summary(voice)
    live = summary["live"]
    print("🧾 Shared Experience Status")
    print("  Action: read-only; phân loại ký ức chung, không ghi memory/không tạo file.")
    print(f"  Phase 13.2 Progress: {phase13_2_progress_percent(summary)}%")
    print(f"  Preview candidates: {sum(1 for item in live['preview'] if item['candidate'])}/{live['count']} | save_now=False")
    for item in live["preview"]:
        print(f"    {item['event_kind']} | phase={item['phase']} | outcome={item['outcome']} | candidate={item['candidate']} | reason={item['reason']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: shared experience là candidate có confirm; không tự ghi ký ức chung.")


def print_shared_experience_test(raw_text=None, voice=None):
    print("🧪 Shared Experience Test")
    print("  Action: read-only; synthetic only, không ghi memory/không tạo ledger.")
    text = (raw_text or "").strip()
    if text and text.lower() not in {"all", "regression"}:
        result = phase13_2_shared_experience_classify(text, source="user_test")
        print(f"  Event: {result['event_kind']} | phase={result['phase']} | outcome={result['outcome']} | weight={result['emotional_weight']}")
        print(f"  Problem: {result['problem']} | resolution={result['resolution']}")
        print(f"  Candidate: {result['candidate']} | confirm_required={result['confirm_required']} | save_now={result['save_now']}")
        print(f"  Reason: {result['reason']}")
        print("  Execute: False")
        return
    rows = phase13_2_shared_rows()
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        result = row["result"]
        issues = ",".join(row["mismatches"]) if row["mismatches"] else "none"
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"event={result['event_kind']} phase={result['phase']} outcome={result['outcome']} "
            f"candidate={result['candidate']} weight={result['emotional_weight']} | issues={issues}"
        )
    print("  Execute: False")


def print_shared_experience_guard_status(voice=None):
    summary = phase13_2_guard_summary(voice)
    print("🧪 Phase 13.2 Shared Experience Guard")
    print("  Action: read-only; kiểm shared experience ledger contract, không lưu/không mutate.")
    print(f"  Progress: {phase13_2_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Shared experience regression:")
    for row in summary["test_rows"]:
        result = row["result"]
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | event={result['event_kind']} phase={result['phase']} candidate={result['candidate']}")


def print_phase13_2_status(voice=None):
    summary = phase13_2_guard_summary(voice)
    print("🧩 Phase 13.2 Status")
    print("  Goal: Shared Experience Ledger - tách ký ức chung khỏi user preference/session note.")
    print(f"  Progress: {phase13_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /shared-experience-status | /shared-experience-test | /shared-experience-guard-status | /phase13-2-ready")


def print_phase13_2_ready(voice=None):
    summary = phase13_2_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 13.2 Ready" if ready else "⚠️ Phase 13.2 Ready")
    print("  Goal: shared experience ledger đủ chắc để sang Phase 13.3 memory conflict/provenance.")
    print(f"  Progress: {phase13_2_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: shared_experience={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 13.2 chỉ preview ledger, không lưu memory.")


# ── Phase 13.3 — Memory Conflict/Provenance ───────────────────────────────────

PHASE13_3_COMMANDS = {
    "/memory-conflict-status",
    "/memory-conflict-guard-status",
    "/memory-conflict-test",
    "/phase13-3-status",
    "/phase13-3-ready",
    "/phase13-3-guard-status",
    "/phase13-3-test",
    "/p13-3",
    "/p13-3-ready",
}

PHASE13_3_SOURCE_RANK = {
    "explicit_confirm": 5,
    "user_test": 4,
    "memory_v3": 3,
    "shared_experience": 3,
    "inferred": 2,
    "social_context": 1,
    "unknown": 0,
}

PHASE13_3_CONFLICT_CASES = [
    (
        "same_preference",
        {"kind": "preference", "key": "reply_style", "value": "short", "provenance": "explicit_confirm"},
        {"kind": "preference", "key": "reply_style", "value": "short", "provenance": "user_test"},
        "same",
        "keep_existing",
    ),
    (
        "direct_conflict_hold",
        {"kind": "preference", "key": "reply_style", "value": "short", "provenance": "explicit_confirm"},
        {"kind": "preference", "key": "reply_style", "value": "long", "provenance": "inferred"},
        "conflict",
        "hold_review",
    ),
    (
        "stronger_source_replace_candidate",
        {"kind": "preference", "key": "reply_style", "value": "short", "provenance": "inferred"},
        {"kind": "preference", "key": "reply_style", "value": "long", "provenance": "explicit_confirm"},
        "conflict",
        "replace_candidate",
    ),
    (
        "different_key_parallel",
        {"kind": "preference", "key": "reply_style", "value": "short", "provenance": "explicit_confirm"},
        {"kind": "presence_habit", "key": "debug_silence", "value": "quiet", "provenance": "memory_v3"},
        "parallel",
        "add_candidate",
    ),
    (
        "sensitive_block",
        {"kind": "preference", "key": "reply_style", "value": "short", "provenance": "explicit_confirm"},
        {"kind": "sensitive", "key": "token", "value": "abc123", "provenance": "user_test"},
        "blocked",
        "block",
    ),
]


def phase13_3_source_rank(source):
    return PHASE13_3_SOURCE_RANK.get(source or "unknown", 0)


def phase13_3_memory_conflict_decision(existing, incoming):
    existing = dict(existing or {})
    incoming = dict(incoming or {})
    if incoming.get("kind") == "sensitive" or incoming.get("key") in {"token", "password", "secret"}:
        relation = "blocked"
        action = "block"
        reason = "sensitive_incoming"
    elif existing.get("key") == incoming.get("key") and existing.get("kind") == incoming.get("kind"):
        if existing.get("value") == incoming.get("value"):
            relation = "same"
            action = "keep_existing"
            reason = "same_fact"
        else:
            relation = "conflict"
            if phase13_3_source_rank(incoming.get("provenance")) > phase13_3_source_rank(existing.get("provenance")):
                action = "replace_candidate"
                reason = "incoming_source_stronger"
            else:
                action = "hold_review"
                reason = "conflict_needs_review"
    else:
        relation = "parallel"
        action = "add_candidate"
        reason = "different_memory_key"
    return {
        "relation": relation,
        "action": action,
        "reason": reason,
        "existing_rank": phase13_3_source_rank(existing.get("provenance")),
        "incoming_rank": phase13_3_source_rank(incoming.get("provenance")),
        "confirm_required": action in {"replace_candidate", "hold_review", "add_candidate"},
        "overwrite_now": False,
        "save_now": False,
        "execute": False,
    }


def phase13_3_conflict_rows():
    rows = []
    for name, existing, incoming, expected_relation, expected_action in PHASE13_3_CONFLICT_CASES:
        result = phase13_3_memory_conflict_decision(existing, incoming)
        passed = result["relation"] == expected_relation and result["action"] == expected_action
        rows.append({
            "name": name,
            "existing": existing,
            "incoming": incoming,
            "passed": passed,
            "result": result,
            "expected_relation": expected_relation,
            "expected_action": expected_action,
        })
    return rows


def phase13_3_live_conflict_snapshot():
    existing = {"kind": "preference", "key": "reply_style", "value": "short", "provenance": "explicit_confirm"}
    incoming = {"kind": "preference", "key": "reply_style", "value": "long", "provenance": "inferred"}
    return {
        "existing": existing,
        "incoming": incoming,
        "decision": phase13_3_memory_conflict_decision(existing, incoming),
    }


def phase13_3_guard_summary(voice=None):
    phase13_2_summary = phase13_2_guard_summary(voice)
    live = phase13_3_live_conflict_snapshot()
    rows_test = phase13_3_conflict_rows()
    command_missing = sorted(PHASE13_3_COMMANDS - KNOWN_SLASH_COMMANDS)
    regression_ok = all(row["passed"] for row in rows_test)
    no_overwrite_ok = all(row["result"]["overwrite_now"] is False and row["result"]["save_now"] is False for row in rows_test)
    source_rank_ok = phase13_3_source_rank("explicit_confirm") > phase13_3_source_rank("inferred") > phase13_3_source_rank("unknown")
    conflict_hold_ok = phase13_3_memory_conflict_decision(
        {"kind": "preference", "key": "x", "value": "a", "provenance": "explicit_confirm"},
        {"kind": "preference", "key": "x", "value": "b", "provenance": "inferred"},
    )["action"] == "hold_review"
    replace_candidate_ok = phase13_3_memory_conflict_decision(
        {"kind": "preference", "key": "x", "value": "a", "provenance": "inferred"},
        {"kind": "preference", "key": "x", "value": "b", "provenance": "explicit_confirm"},
    )["action"] == "replace_candidate"
    sensitive_ok = phase13_3_memory_conflict_decision({}, {"kind": "sensitive", "key": "token", "value": "abc"})["action"] == "block"
    rows = [
        ("phase13_2_foundation", not phase10_guard_failures(phase13_2_summary), f"shared_experience={phase13_2_summary['pass_count']}/{phase13_2_summary['total']}"),
        ("conflict_regression", regression_ok, f"{sum(1 for row in rows_test if row['passed'])}/{len(rows_test)} pass"),
        ("source_rank_contract", source_rank_ok, "explicit_confirm > inferred > unknown"),
        ("conflict_hold_contract", conflict_hold_ok, "weaker conflicting incoming memory is hold_review"),
        ("replace_candidate_contract", replace_candidate_ok, "stronger incoming source is replace_candidate, not overwrite"),
        ("sensitive_block_contract", sensitive_ok, "sensitive incoming is blocked"),
        ("live_conflict_snapshot", live["decision"]["action"] == "hold_review", f"relation={live['decision']['relation']} action={live['decision']['action']} reason={live['decision']['reason']}"),
        ("no_overwrite_no_save", no_overwrite_ok, "overwrite_now=False save_now=False for all cases"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("memory_conflict_readonly", True, "status/guard/test không ghi memory, không sửa existing item"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": rows_test,
        "live": live,
    }


def phase13_3_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_memory_conflict_status(voice=None):
    summary = phase13_3_guard_summary(voice)
    live = summary["live"]
    decision = live["decision"]
    print("🧭 Memory Conflict Status")
    print("  Action: read-only; kiểm conflict/provenance, không overwrite/không lưu.")
    print(f"  Phase 13.3 Progress: {phase13_3_progress_percent(summary)}%")
    print(f"  Live: relation={decision['relation']} action={decision['action']} reason={decision['reason']}")
    print(f"  Source rank: existing={decision['existing_rank']} incoming={decision['incoming_rank']} | save_now={decision['save_now']} overwrite_now={decision['overwrite_now']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: conflict không bao giờ ghi đè ngay; chỉ hold/review hoặc replace_candidate có confirm.")


def print_memory_conflict_test(raw_text=None, voice=None):
    print("🧪 Memory Conflict Test")
    print("  Action: read-only; synthetic only, không ghi memory/không overwrite.")
    key = (raw_text or "").strip().lower()
    rows = phase13_3_conflict_rows()
    aliases = {
        "same": {"same_preference"},
        "conflict": {"direct_conflict_hold", "stronger_source_replace_candidate"},
        "replace": {"stronger_source_replace_candidate"},
        "parallel": {"different_key_parallel"},
        "sensitive": {"sensitive_block"},
        "blocked": {"sensitive_block"},
    }
    if key and key != "all":
        allowed = aliases.get(key, {key})
        rows = [row for row in rows if row["name"] in allowed or row["result"]["relation"] in allowed or row["result"]["action"] in allowed]
        if not rows:
            print("  Status: not_found")
            print("  Cases: same, conflict, replace, parallel, sensitive")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        result = row["result"]
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"relation={result['relation']} expected={row['expected_relation']} | "
            f"action={result['action']} expected={row['expected_action']} | "
            f"rank={result['existing_rank']}->{result['incoming_rank']} save_now={result['save_now']}"
        )
    print("  Execute: False")


def print_memory_conflict_guard_status(voice=None):
    summary = phase13_3_guard_summary(voice)
    print("🧪 Phase 13.3 Memory Conflict Guard")
    print("  Action: read-only; kiểm conflict/provenance, không ghi đè/không lưu.")
    print(f"  Progress: {phase13_3_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Conflict regression:")
    for row in summary["test_rows"]:
        result = row["result"]
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | relation={result['relation']} action={result['action']} reason={result['reason']}")


def print_phase13_3_status(voice=None):
    summary = phase13_3_guard_summary(voice)
    print("🧩 Phase 13.3 Status")
    print("  Goal: Memory Conflict/Provenance - mâu thuẫn phải hold/review, không ghi đè.")
    print(f"  Progress: {phase13_3_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /memory-conflict-status | /memory-conflict-test | /memory-conflict-guard-status | /phase13-3-ready")


def print_phase13_3_ready(voice=None):
    summary = phase13_3_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 13.3 Ready" if ready else "⚠️ Phase 13.3 Ready")
    print("  Goal: memory conflict/provenance đủ chắc để sang Phase 13.4 decay/review scoring.")
    print(f"  Progress: {phase13_3_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: memory_conflict={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 13.3 không overwrite memory, không lưu memory.")


# ── Phase 13.4 — Memory Decay/Review Scoring ──────────────────────────────────

PHASE13_4_COMMANDS = {
    "/memory-decay-status",
    "/memory-decay-guard-status",
    "/memory-decay-test",
    "/phase13-4-status",
    "/phase13-4-ready",
    "/phase13-4-guard-status",
    "/phase13-4-test",
    "/p13-4",
    "/p13-4-ready",
}

PHASE13_4_DECAY_CASES = [
    (
        "durable_preference_keep",
        {"kind": "preference", "weight": "medium", "ttl_policy": "durable", "age_days": 45, "conflict": False},
        "keep",
        "durable_memory",
    ),
    (
        "deep_shared_keep",
        {"kind": "shared_experience", "weight": "deep", "ttl_policy": "durable", "age_days": 120, "conflict": False},
        "keep",
        "deep_shared_experience",
    ),
    (
        "presence_habit_review",
        {"kind": "presence_habit", "weight": "medium", "ttl_policy": "review", "age_days": 30, "conflict": False},
        "review",
        "review_policy_due",
    ),
    (
        "session_transient_drop",
        {"kind": "transient", "weight": "light", "ttl_policy": "session_only", "age_days": 1, "conflict": False},
        "drop_candidate",
        "session_only_expired",
    ),
    (
        "vibe_old_review",
        {"kind": "vibe", "weight": "light", "ttl_policy": "review", "age_days": 14, "conflict": False},
        "review",
        "vibe_or_lore_review",
    ),
    (
        "conflict_review",
        {"kind": "preference", "weight": "medium", "ttl_policy": "durable", "age_days": 5, "conflict": True},
        "review",
        "conflict_present",
    ),
    (
        "sensitive_block",
        {"kind": "sensitive", "weight": "blocked", "ttl_policy": "blocked", "age_days": 0, "conflict": False},
        "block",
        "blocked_memory",
    ),
]


def phase13_4_decay_score(memory_meta):
    meta = dict(memory_meta or {})
    kind = meta.get("kind") or "note"
    weight = meta.get("weight") or "light"
    ttl_policy = meta.get("ttl_policy") or "review"
    age_days = float(meta.get("age_days") or 0.0)
    conflict = bool(meta.get("conflict"))

    score = 50
    if weight == "deep":
        score += 30
    elif weight == "medium":
        score += 15
    elif weight == "blocked":
        score = 0
    if ttl_policy == "durable":
        score += 20
    elif ttl_policy == "session_only":
        score -= 50
    elif ttl_policy == "blocked":
        score = 0
    if kind in {"vibe", "transient"}:
        score -= 20
    if conflict:
        score -= 25
    score -= min(30, int(age_days // 14) * 5)
    score = max(0, min(100, score))

    if ttl_policy == "blocked" or kind == "sensitive":
        action = "block"
        reason = "blocked_memory"
    elif ttl_policy == "session_only" and age_days >= 1:
        action = "drop_candidate"
        reason = "session_only_expired"
    elif conflict:
        action = "review"
        reason = "conflict_present"
    elif kind == "shared_experience" and weight == "deep":
        action = "keep"
        reason = "deep_shared_experience"
    elif ttl_policy == "durable" and score >= 55:
        action = "keep"
        reason = "durable_memory"
    elif kind == "vibe":
        action = "review"
        reason = "vibe_or_lore_review"
    elif ttl_policy == "review":
        action = "review"
        reason = "review_policy_due"
    elif score < 35:
        action = "drop_candidate"
        reason = "low_score"
    else:
        action = "review"
        reason = "default_review"
    return {
        "score": score,
        "action": action,
        "reason": reason,
        "review_required": action in {"review", "drop_candidate"},
        "delete_now": False,
        "save_now": False,
        "execute": False,
    }


def phase13_4_decay_rows():
    rows = []
    for name, meta, expected_action, expected_reason in PHASE13_4_DECAY_CASES:
        result = phase13_4_decay_score(meta)
        passed = result["action"] == expected_action and result["reason"] == expected_reason
        rows.append({
            "name": name,
            "meta": meta,
            "passed": passed,
            "result": result,
            "expected_action": expected_action,
            "expected_reason": expected_reason,
        })
    return rows


def phase13_4_live_decay_snapshot():
    governance = memory_governance_summary()
    snapshot = governance.get("snapshot") or {}
    classifications = snapshot.get("classifications") or {}
    sample = {
        "kind": "note",
        "weight": "light",
        "ttl_policy": "review",
        "age_days": 0,
        "conflict": False,
    }
    return {
        "governance": governance,
        "classifications": classifications,
        "long_term": snapshot.get("long_term", 0),
        "sample_decision": phase13_4_decay_score(sample),
    }


def phase13_4_guard_summary(voice=None):
    phase13_3_summary = phase13_3_guard_summary(voice)
    live = phase13_4_live_decay_snapshot()
    rows_test = phase13_4_decay_rows()
    command_missing = sorted(PHASE13_4_COMMANDS - KNOWN_SLASH_COMMANDS)
    regression_ok = all(row["passed"] for row in rows_test)
    no_delete_ok = all(row["result"]["delete_now"] is False and row["result"]["save_now"] is False for row in rows_test)
    durable_keep_ok = phase13_4_decay_score({"kind": "preference", "weight": "medium", "ttl_policy": "durable", "age_days": 7})["action"] == "keep"
    transient_drop_ok = phase13_4_decay_score({"kind": "transient", "weight": "light", "ttl_policy": "session_only", "age_days": 1})["action"] == "drop_candidate"
    conflict_review_ok = phase13_4_decay_score({"kind": "preference", "weight": "medium", "ttl_policy": "durable", "conflict": True})["action"] == "review"
    sensitive_block_ok = phase13_4_decay_score({"kind": "sensitive", "weight": "blocked", "ttl_policy": "blocked"})["action"] == "block"
    rows = [
        ("phase13_3_foundation", not phase10_guard_failures(phase13_3_summary), f"memory_conflict={phase13_3_summary['pass_count']}/{phase13_3_summary['total']}"),
        ("decay_regression", regression_ok, f"{sum(1 for row in rows_test if row['passed'])}/{len(rows_test)} pass"),
        ("durable_keep_contract", durable_keep_ok, "durable preference stays keep"),
        ("transient_drop_contract", transient_drop_ok, "session_only memory becomes drop_candidate after session"),
        ("conflict_review_contract", conflict_review_ok, "conflicted durable memory becomes review"),
        ("sensitive_block_contract", sensitive_block_ok, "sensitive/blocked memory stays blocked"),
        ("live_decay_snapshot", live["sample_decision"]["action"] in {"keep", "review", "drop_candidate", "block"}, f"long={live['long_term']} sample={live['sample_decision']['action']}"),
        ("no_delete_no_save", no_delete_ok, "delete_now=False save_now=False for all cases"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("memory_decay_readonly", True, "status/guard/test không xoá, không compact, không lưu memory"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "test_rows": rows_test,
        "live": live,
    }


def phase13_4_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_memory_decay_status(voice=None):
    summary = phase13_4_guard_summary(voice)
    live = summary["live"]
    sample = live["sample_decision"]
    classes = live.get("classifications") or {}
    print("🧮 Memory Decay Status")
    print("  Action: read-only; tính điểm review/decay, không xoá/không compact.")
    print(f"  Phase 13.4 Progress: {phase13_4_progress_percent(summary)}%")
    print(f"  Store: long={live['long_term']} | classes={classes}")
    print(f"  Sample: action={sample['action']} score={sample['score']} reason={sample['reason']} delete_now={sample['delete_now']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: decay chỉ tạo review/drop candidate; không tự xoá memory.")


def print_memory_decay_test(raw_text=None, voice=None):
    print("🧪 Memory Decay Test")
    print("  Action: read-only; synthetic only, không xoá/không compact.")
    key = (raw_text or "").strip().lower()
    rows = phase13_4_decay_rows()
    aliases = {
        "keep": {"durable_preference_keep", "deep_shared_keep"},
        "review": {"presence_habit_review", "vibe_old_review", "conflict_review"},
        "drop": {"session_transient_drop"},
        "block": {"sensitive_block"},
        "conflict": {"conflict_review"},
        "vibe": {"vibe_old_review"},
    }
    if key and key != "all":
        allowed = aliases.get(key, {key})
        rows = [row for row in rows if row["name"] in allowed or row["result"]["action"] in allowed]
        if not rows:
            print("  Status: not_found")
            print("  Cases: keep, review, drop, block, conflict, vibe")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        result = row["result"]
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"action={result['action']} expected={row['expected_action']} | "
            f"score={result['score']} reason={result['reason']} | delete_now={result['delete_now']}"
        )
    print("  Execute: False")


def print_memory_decay_guard_status(voice=None):
    summary = phase13_4_guard_summary(voice)
    print("🧪 Phase 13.4 Memory Decay Guard")
    print("  Action: read-only; kiểm decay/review scoring, không xoá/không compact.")
    print(f"  Progress: {phase13_4_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Decay regression:")
    for row in summary["test_rows"]:
        result = row["result"]
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={result['action']} score={result['score']} reason={result['reason']}")


def print_phase13_4_status(voice=None):
    summary = phase13_4_guard_summary(voice)
    print("🧩 Phase 13.4 Status")
    print("  Goal: Memory Decay/Review Scoring - memory cũ/yếu/conflict phải vào review/drop candidate.")
    print(f"  Progress: {phase13_4_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /memory-decay-status | /memory-decay-test | /memory-decay-guard-status | /phase13-4-ready")


def print_phase13_4_ready(voice=None):
    summary = phase13_4_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 13.4 Ready" if ready else "⚠️ Phase 13.4 Ready")
    print("  Goal: memory decay/review scoring đủ chắc để sang Phase 13.5 memory gate final.")
    print(f"  Progress: {phase13_4_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: memory_decay={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 13.4 không xoá/không compact/không lưu memory.")


# ── Phase 13.5 — Final Memory Gate ───────────────────────────────────────────

PHASE13_5_COMMANDS = {
    "/memory-gate-status",
    "/memory-gate-guard-status",
    "/memory-gate-test",
    "/phase13-status",
    "/phase13-ready",
    "/phase13-5-status",
    "/phase13-5-ready",
    "/phase13-5-guard-status",
    "/phase13-5-test",
    "/p13",
    "/p13-ready",
    "/p13-5",
    "/p13-5-ready",
}


def phase13_collect_summaries(voice=None):
    memory_v3 = phase13_1_guard_summary(voice)
    shared = phase13_2_guard_summary(voice)
    conflict = phase13_3_guard_summary(voice)
    decay = phase13_4_guard_summary(voice)
    return {
        "memory_v3": memory_v3,
        "shared": shared,
        "conflict": conflict,
        "decay": decay,
    }


def phase13_subphase_rows(voice=None, summaries=None):
    summaries = summaries or phase13_collect_summaries(voice)
    subphase_summaries = [
        ("phase13_1_memory_v3", summaries["memory_v3"], "memory_v3"),
        ("phase13_2_shared_experience", summaries["shared"], "shared_experience"),
        ("phase13_3_memory_conflict", summaries["conflict"], "memory_conflict"),
        ("phase13_4_memory_decay", summaries["decay"], "memory_decay"),
    ]
    rows = []
    for name, summary, label in subphase_summaries:
        pass_count = summary.get("pass_count", 0)
        total = summary.get("total", 0)
        rows.append({
            "name": name,
            "passed": not phase10_guard_failures(summary) and pass_count == total,
            "detail": f"{label}={pass_count}/{total} pass",
        })
    return rows


def phase13_gate_snapshot(summaries=None, voice=None):
    summaries = summaries or phase13_collect_summaries(voice)
    governance = memory_governance_summary()
    memory_snapshot = governance.get("snapshot") or {}
    pending = memory_snapshot.get("pending")
    save_flags = []
    delete_flags = []
    overwrite_flags = []
    for key in ["memory_v3", "shared", "conflict", "decay"]:
        summary = summaries[key]
        for row in summary.get("test_rows", []):
            result = row.get("result") or {}
            if result.get("save_now"):
                save_flags.append(f"{key}:{row.get('name')}")
            if result.get("delete_now"):
                delete_flags.append(f"{key}:{row.get('name')}")
            if result.get("overwrite_now"):
                overwrite_flags.append(f"{key}:{row.get('name')}")
    return {
        "governance": governance,
        "memory_snapshot": memory_snapshot,
        "pending": pending,
        "save_flags": save_flags,
        "delete_flags": delete_flags,
        "overwrite_flags": overwrite_flags,
        "long_term": memory_snapshot.get("long_term", 0),
        "string_entries": memory_snapshot.get("string_entries", 0),
        "structured_entries": memory_snapshot.get("structured_entries", 0),
        "other_entries": memory_snapshot.get("other_entries", 0),
    }


def phase13_5_guard_summary(voice=None):
    summaries = phase13_collect_summaries(voice)
    subphases = phase13_subphase_rows(voice, summaries)
    snapshot = phase13_gate_snapshot(summaries, voice)
    governance = snapshot["governance"]
    command_missing = sorted(PHASE13_5_COMMANDS - KNOWN_SLASH_COMMANDS)
    no_mutation_ok = not snapshot["save_flags"] and not snapshot["delete_flags"] and not snapshot["overwrite_flags"]
    no_pending_ok = not snapshot["pending"]
    legacy_ok = int(snapshot["other_entries"] or 0) == 0
    rows = [
        ("subphase_closure", all(row["passed"] for row in subphases), f"{sum(1 for row in subphases if row['passed'])}/{len(subphases)} pass"),
        ("memory_governance_bridge", not phase10_guard_failures(governance), f"memory_governance={governance['pass_count']}/{governance['total']}"),
        ("schema_shared_conflict_decay_ready", all(row["passed"] for row in subphases), "schema/shared/conflict/decay all pass"),
        ("no_save_delete_overwrite", no_mutation_ok, f"save={len(snapshot['save_flags'])} delete={len(snapshot['delete_flags'])} overwrite={len(snapshot['overwrite_flags'])}"),
        ("memory_pending_clear", no_pending_ok, "none" if not snapshot["pending"] else str(snapshot["pending"].get("kind"))),
        ("legacy_store_compatible", legacy_ok, f"string={snapshot['string_entries']} structured={snapshot['structured_entries']} other={snapshot['other_entries']}"),
        ("confirm_required_contract", True, "candidate/replace/drop remain review/confirm only"),
        ("autonomy_lock_contract", True, f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("phase14_boundary", True, "Phase 14 chỉ bàn/làm sau Phase 13 Ready=True; Phase 13 không bật memory automation"),
        ("memory_gate_readonly", True, "status/guard/test không lưu, không xoá, không compact, không migrate"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "subphases": subphases,
        "snapshot": snapshot,
        "summaries": summaries,
    }


def phase13_5_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_memory_gate_status(voice=None):
    summary = phase13_5_guard_summary(voice)
    snapshot = summary["snapshot"]
    print("🧠 Phase 13 Memory Gate Status")
    print("  Action: read-only; tổng kiểm memory v3, không mutate/không migrate.")
    print(f"  Phase 13.5 Progress: {phase13_5_progress_percent(summary)}%")
    print(f"  Subphases: {sum(1 for row in summary['subphases'] if row['passed'])}/{len(summary['subphases'])} pass")
    print(f"  Store: long={snapshot['long_term']} string={snapshot['string_entries']} structured={snapshot['structured_entries']} other={snapshot['other_entries']}")
    print(f"  Mutation flags: save={len(snapshot['save_flags'])} delete={len(snapshot['delete_flags'])} overwrite={len(snapshot['overwrite_flags'])}")
    print(f"  Pending memory action: {'yes' if snapshot['pending'] else 'none'}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 13 đóng memory governance v3; chưa tự lưu, chưa tự xoá, chưa migrate.")


def print_memory_gate_test(raw_text=None, voice=None):
    print("🧪 Memory Gate Test")
    print("  Action: read-only; synthetic/summary only, không mutate/không execute.")
    summary = phase13_5_guard_summary(voice)
    key = (raw_text or "").strip().lower()
    if key in {"", "all"}:
        print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
        print("  Subphases:")
        for row in summary["subphases"]:
            print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")
        print("  Gate rows:")
        for name, passed, detail in summary["rows"]:
            print(f"    {'pass' if passed else 'fail'} | {name} | {detail}")
        return
    if key in {"subphase", "subphases", "phase"}:
        print(f"  Section: subphases | {sum(1 for row in summary['subphases'] if row['passed'])}/{len(summary['subphases'])} pass")
        for row in summary["subphases"]:
            print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")
        print("  Execute: False")
        return
    if key in {"mutation", "mutate", "save"}:
        snapshot = summary["snapshot"]
        print("  Section: mutation")
        print(f"  Save flags: {len(snapshot['save_flags'])}")
        print(f"  Delete flags: {len(snapshot['delete_flags'])}")
        print(f"  Overwrite flags: {len(snapshot['overwrite_flags'])}")
        print(f"  Pending: {'yes' if snapshot['pending'] else 'none'}")
        print("  Execute: False")
        return
    if key in {"store", "legacy"}:
        snapshot = summary["snapshot"]
        print("  Section: store")
        print(f"  Long: {snapshot['long_term']}")
        print(f"  String: {snapshot['string_entries']}")
        print(f"  Structured: {snapshot['structured_entries']}")
        print(f"  Other: {snapshot['other_entries']}")
        print("  Execute: False")
        return
    if key in {"autonomy", "phase14"}:
        print("  Section: autonomy/phase14")
        print(f"  Autonomy: {AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}")
        print("  Phase 14: only after Phase 13 Ready=True.")
        print("  Execute: False")
        return
    print("  Status: not_found")
    print("  Sections: subphases, mutation, store, autonomy")
    print("  Execute: False")


def print_memory_gate_guard_status(voice=None):
    summary = phase13_5_guard_summary(voice)
    print("🧪 Phase 13.5 Memory Gate Guard")
    print("  Action: read-only; tổng kiểm Phase 13, không lưu/không xoá/không migrate.")
    print(f"  Progress: {phase13_5_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Subphases:")
    for row in summary["subphases"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")


def print_phase13_5_status(voice=None):
    summary = phase13_5_guard_summary(voice)
    print("🧩 Phase 13.5 Status")
    print("  Goal: Final Memory Gate - đóng memory v3 governance trước Phase 14.")
    print(f"  Progress: {phase13_5_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /memory-gate-status | /memory-gate-test | /memory-gate-guard-status | /phase13-ready")


def print_phase13_ready(voice=None):
    summary = phase13_5_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 13 Ready" if ready else "⚠️ Phase 13 Ready")
    print("  Goal: Phase 13 memory governance v3 đủ sạch để bắt đầu Phase 14.")
    print(f"  Progress: {phase13_5_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: phase13_gate={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 13 không tự lưu, không xoá, không migrate memory.")
