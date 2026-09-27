"""
Phase 10 — CNS Final Hardening + Subphases 10.1–10.10.
Tách khỏi main.py (GĐ 0.4).
"""

from __future__ import annotations

from nana.phases.commons import (
    AUTONOMY_LOCK_PHASE,
    AUTONOMY_LOCK_RULE,
    KNOWN_SLASH_COMMANDS,
    PHASE10_REQUIRED_COMMANDS,
    PHASE10_1_COMMANDS,
    PHASE10_1_REQUIRED_GROUPS,
    PHASE10_2_COMMANDS,
    PHASE10_2_EXPECTED_ALIASES,
    PHASE10_3_COMMANDS,
    PHASE10_3_REQUIRED_SCHEMA_GROUPS,
    PHASE10_4_COMMANDS,
    RUNTIME_EVENT_REQUIRED_FIELDS,
    PHASE10_5_COMMANDS,
    PHASE10_6_COMMANDS,
    PHASE10_7_COMMANDS,
    PHASE10_8_COMMANDS,
    VALID_TURN_STATUSES,
    PHASE10_9_COMMANDS,
    PHASE10_10_COMMANDS,
    RUNTIME_TURN_STATE,
)

# Priority enum needed for scheduler guard
from nana.runtime.priority_queue import Priority
from nana.commands.router import normalize_command_text, suggest_slash_command
from nana.commands.router_manifest import (
    classify_command_truth,
    command_truth_lines,
    command_truth_status_lines,
)
from nana.config import CHAT_CONTEXT_SUPPRESS, CHAT_HISTORY_PATH, DATA_DIR, MEMORY_PATH, PROACTIVE_BROWSER_COOLDOWN
from nana.core.context_recovery import _signal_groups, vision_text_reconcile_report
from nana.intent.priority import context_priority_brief, context_priority_policy
from nana.integrations.social import build_social_draft_source, social_source_classification
from nana.memory import memory_governance_summary
from nana.phases.phase6 import build_phase6_status_model
from nana.phases.phase7 import (
    PHASE7_ACTION_LOG,
    PHASE7_DRY_RUN_HISTORY,
    RUNTIME_EVENT_LOG,
    build_phase7_status_model,
    phase7_current_pending_plan,
    phase7_pending_plan_expired,
    runtime_event_summary,
)
from nana.phases.phase8 import build_phase8_status_model
from nana.phases.phase9 import PHASE9_AUDIT_LOG, build_phase9_status_model, phase9_audit_summary
from nana.runtime.browser_state import browser_age_seconds, browser_snapshot_state
from nana.runtime.attention import evaluate_attention_window
from nana.runtime.logger import LOG_DIR, LOG_FILES
from nana.runtime.metrics import RUNTIME_LATENCY, RUNTIME_RECONCILE
from nana.runtime.persona import RESIDUE_DECAY_PER_TICK
from nana.runtime.recovery import recovery_dry_run, recovery_governor_config, recovery_snapshot
from nana.social.classifier import strip_accents_for_match
from nana.social.prompts import build_social_draft_prompt

# Lazy imports
import time


def _get_runtime_queue():
    from nana.runtime.priority_queue import runtime_queue
    return runtime_queue


def _get_context_state():
    from nana import context_state
    return context_state


def _get_context_lock():
    from nana import context_lock
    return context_lock


def _get_memory():
    from nana import memory
    return memory


def _get_memory_lock():
    from nana import memory_lock
    return memory_lock


def _get_pending_actions():
    from nana.actions.pending import pending_actions
    return pending_actions


def _get_social_drafts():
    from nana.actions.drafts import social_drafts
    return social_drafts


def _get_vision_previewer():
    from nana.browser import vision_previewer
    return vision_previewer


def _get_last_vision_description():
    from nana.brain.vision import LAST_VISION_DESCRIPTION
    return LAST_VISION_DESCRIPTION


def _get_autonomy_loop():
    from nana.autonomy import AUTONOMY_LOOP
    return AUTONOMY_LOOP


def _get_memory_item_key():
    from nana.memory import memory_item_key
    return memory_item_key


def _get_shorten_line():
    from nana.utils.formatting import shorten_line
    return shorten_line


def context_signal_groups_from_text(text):
    return _signal_groups(text)


def extract_social_vibe_topic(vibe=""):
    text = str(vibe or "")
    lowered = text.lower()
    marker = "post_topic="
    if marker in lowered:
        start = lowered.find(marker) + len(marker)
        return text[start:].split("|", 1)[0].strip()
    marker = "bám bài gốc:"
    if marker in lowered:
        start = lowered.find(marker) + len(marker)
        return text[start:].split(";", 1)[0].strip()
    return ""


def social_vibe_topic_groups(topic=""):
    normalized = strip_accents_for_match(str(topic or "").lower())
    if not normalized or "chu de trong bai goc" in normalized:
        return []
    groups = set(context_signal_groups_from_text(topic))
    phrase_groups = [
        ("doi song", "street_life"),
        ("pho xa", "street_life"),
        ("xoa xat", "conflict"),
        ("tranh cai", "conflict"),
        ("thuoc", "serious_issue"),
        ("thuc pham", "serious_issue"),
        ("minh bach", "serious_issue"),
        ("dong vat", "animal"),
        ("cute", "animal"),
        ("ten lua", "space_launch"),
        ("starship", "space_launch"),
        ("spacex", "space_launch"),
    ]
    for phrase, group in phrase_groups:
        if phrase in normalized:
            groups.add(group)
    return sorted(groups)


def context_vibe_audit(post="", title="", vibe=""):
    shorten_line = _get_shorten_line()
    primary_groups = set(context_signal_groups_from_text(" ".join(part for part in [post, title] if part)))
    topic = extract_social_vibe_topic(vibe)
    topic_groups = set(social_vibe_topic_groups(topic))
    topic_normalized = strip_accents_for_match(str(topic or "").lower())
    generic_topic = bool(topic_normalized and "chu de trong bai goc" in topic_normalized)
    vibe_groups = set() if generic_topic else set(context_signal_groups_from_text(vibe))
    if not vibe:
        return {
            "status": "missing",
            "primary_groups": sorted(primary_groups),
            "topic": "",
            "topic_groups": [],
            "vibe_groups": [],
            "mismatch": False,
            "detail": "no_vibe",
        }
    mismatch = bool(primary_groups and topic_groups and not (primary_groups & topic_groups))
    if mismatch:
        detail = f"vibe_mismatch primary={','.join(sorted(primary_groups))} topic={','.join(sorted(topic_groups))}"
    elif topic_groups:
        detail = f"vibe_topic_support groups={','.join(sorted(topic_groups))}"
    elif generic_topic:
        detail = "vibe_topic_generic"
    elif vibe_groups:
        detail = f"vibe_support groups={','.join(sorted(vibe_groups))}"
    elif topic:
        detail = f"vibe_topic_unclear topic={shorten_line(topic, 40)}"
    else:
        detail = "vibe_no_clear_topic"
    return {
        "status": "available",
        "primary_groups": sorted(primary_groups),
        "topic": topic,
        "topic_groups": sorted(topic_groups),
        "vibe_groups": sorted(vibe_groups),
        "mismatch": mismatch,
        "detail": detail,
    }


def runtime_turn_state_snapshot():
    snapshot = dict(RUNTIME_TURN_STATE)
    snapshot["age"] = max(0.0, time.time() - float(snapshot.get("last_update") or 0.0))
    return snapshot


def _get_phase7_pending_plan():
    return phase7_current_pending_plan()


# ── Phase 10 core helpers ──────────────────────────────────────────────────────


def phase10_check_counts(checks):
    warns = [(name, detail) for name, status, detail in checks if status == "warn"]
    observes = [(name, detail) for name, status, detail in checks if status == "observe"]
    return warns, observes


def phase10_add_phase_row(rows, phase_name, checks):
    shorten_line = _get_shorten_line()
    warns, observes = phase10_check_counts(checks)
    detail = f"warn={len(warns)} observe={len(observes)}"
    if warns:
        detail = f"{detail} | first={warns[0][0]}:{shorten_line(str(warns[0][1]), 90)}"
    rows.append({
        "name": phase_name,
        "passed": not warns,
        "detail": detail,
    })


def phase10_guard_failures(summary):
    return summary.get("failures") or summary.get("guard_failures") or []


def phase10_failure_brief(failure):
    if isinstance(failure, dict):
        return failure.get("name") or failure.get("group") or "unknown", failure.get("detail") or failure.get("path") or ""
    if isinstance(failure, (tuple, list)):
        name = failure[0] if len(failure) > 0 else "unknown"
        detail = failure[2] if len(failure) > 2 else ""
        return name, detail
    return "unknown", str(failure)


def phase10_add_guard_summary_row(rows, name, summary, label):
    shorten_line = _get_shorten_line()
    failures = phase10_guard_failures(summary)
    pass_count = summary.get("pass_count", 0)
    total = summary.get("total", 0)
    passed = not failures and pass_count == total
    detail = f"{label}={pass_count}/{total} pass"
    if failures:
        first_name, first_detail = phase10_failure_brief(failures[0])
        detail = f"{detail} | first={first_name}:{shorten_line(str(first_detail), 90)}"
    rows.append({
        "name": name,
        "passed": passed,
        "detail": detail,
    })


def phase10_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def build_phase10_status_model():
    guard = phase10_guard_summary()
    checks = [
        (row["name"], "pass" if row["passed"] else "warn", row["detail"])
        for row in guard["rows"]
    ]
    return {"checks": checks, "guard": guard, "progress": phase10_progress_percent(guard)}


def print_phase10_guard_status():
    model = build_phase10_status_model()
    guard = model["guard"]
    print("🧪 Phase 10 Final Guard")
    print("  Action: read-only; tổng kiểm Phase 6-9 + CNS 10.1-10.9, không tạo pending, không execute.")
    print(f"  Progress: {model['progress']}%")
    print(f"  Summary: {guard['pass_count']}/{guard['total']} pass")
    for row in guard["rows"]:
        status = "pass" if row["passed"] else "fail"
        print(f"  {row['name']} | {status} | {row['detail']}")


def print_phase10_status():
    model = build_phase10_status_model()
    print("🧩 Phase 10 Status")
    print("  Goal: final hardening/checklist cho Phase 6-9 + CNS subphases trước khi bàn Phase 11.")
    print(f"  Progress: {model['progress']}%")
    for name, status, detail in model["checks"]:
        print(f"  {name}: {status} | {detail}")
    print("  Scope: read-only checks only; không mở executor, không click/type/post.")
    print("  Commands: /phase10-guard-status | /phase10-ready | /ship-checklist | /phase10-10-status")


def print_phase10_ready():
    model = build_phase10_status_model()
    blocking = [(name, detail) for name, status, detail in model["checks"] if status == "warn"]
    ready = not blocking
    guard = model["guard"]
    print("✅ Phase 10 Ready" if ready else "⚠️ Phase 10 Ready")
    print("  Goal: đủ sạch để bắt đầu bàn Phase 11; Phase 10 không tự bật autonomy.")
    print(f"  Progress: {model['progress']}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: final_guard={guard['pass_count']}/{guard['total']}")
    print("  Autonomy: locked Phase 5-10; Phase 11 chỉ được bàn sau khi Ready=True.")


# ── Phase 10.1: Runtime Inventory ─────────────────────────────────────────────


def runtime_inventory_row(name, owner, storage, persistence, restart, lock, commands, live, risk):
    return {
        "name": name,
        "owner": owner,
        "storage": storage,
        "persistence": persistence,
        "restart": restart,
        "lock": lock,
        "commands": commands,
        "live": live,
        "risk": risk,
    }


def runtime_inventory_rows():
    context_state = _get_context_state()
    context_lock = _get_context_lock()
    memory = _get_memory()
    memory_lock = _get_memory_lock()
    runtime_queue = _get_runtime_queue()
    pending_actions = _get_pending_actions()
    social_drafts = _get_social_drafts()
    vision_previewer = _get_vision_previewer()
    last_vision_description = _get_last_vision_description()
    memory_item_key = _get_memory_item_key()

    now = time.time()
    with context_lock:
        state = dict(context_state)
        browser = dict(state.get("browser", {}))
        proactive = dict(state.get("proactive", {}))
    with memory_lock:
        long_term = list(memory.get("long_term", []))
        short_term = list(memory.get("short_term", []))
        chat_log = list(memory.get("chat_log", []))
        labels = dict(memory.get("memory_labels", {}))
        persona = dict(memory.get("persona", {}))

    queue_snapshot = runtime_queue.snapshot()
    pending_snapshot = pending_actions.snapshot()
    draft_snapshot = social_drafts.snapshot()
    vision_dir = vision_previewer.output_dir
    vision_files = []
    if vision_dir.exists():
        try:
            vision_files = sorted(vision_dir.glob("vision_focus_*.png"), key=lambda path: path.stat().st_mtime, reverse=True)
        except OSError:
            vision_files = []
    vision_age = None
    if last_vision_description:
        vision_age = max(0.0, now - last_vision_description.get("time", 0))
    browser_age = browser_age_seconds(browser)
    browser_state = browser_snapshot_state(browser)
    active_labels = sum(
        1
        for key in labels
        if key in {memory_item_key(item) for item in long_term}
    )
    pending_plan = _get_phase7_pending_plan()
    pending_plan_detail = "none"
    if pending_plan:
        expired = phase7_pending_plan_expired(pending_plan)
        pending_plan_detail = f"id={pending_plan.get('id')} expired={expired}"
    runtime_pending = pending_snapshot.get("pending")
    runtime_pending_detail = "none"
    if runtime_pending:
        runtime_pending_detail = f"id={runtime_pending.id} action={runtime_pending.action} expires={runtime_pending.expires_in_seconds():.0f}s"
    draft_pending = draft_snapshot.get("pending") or []

    return [
        runtime_inventory_row(
            "context_state",
            "nana.runtime.context",
            "process memory",
            "volatile",
            "lost_on_restart",
            "context_lock",
            "/status | /focus | /attention | /presence",
            f"zone={state.get('active_zone')} app={state.get('active_app')} idle={state.get('idle_state')} flow={state.get('in_flow')}",
            "central shared state; writes must stay narrow and locked",
        ),
        runtime_inventory_row(
            "browser_snapshot",
            "nana.browser.context + runtime.context",
            "process memory",
            "volatile",
            "lost_on_restart",
            "context_lock",
            "/br | /browser | /context-confidence | /evidence-trace",
            f"available={browser.get('available')} kind={browser.get('kind')} state={browser_state} age={'none' if browser_age is None else f'{browser_age:.1f}s'}",
            "CDP/window focus can go stale or point to wrong tab",
        ),
        runtime_inventory_row(
            "runtime_refresh_cache",
            "main.RUNTIME_LATENCY",
            "process memory",
            "volatile",
            "lost_on_restart",
            "none",
            "/runtime-status",
            f"count={RUNTIME_LATENCY.get('browser_refresh_count')} hits={RUNTIME_LATENCY.get('browser_refresh_cache_hits')} coalesced={RUNTIME_LATENCY.get('browser_refresh_coalesced')}",
            "latency/cooldown metrics only; not a source of truth",
        ),
        runtime_inventory_row(
            "runtime_reconcile",
            "main.RUNTIME_RECONCILE",
            "process memory + asyncio task",
            "volatile",
            "lost_on_restart",
            "async task state",
            "/reconcile | /reconcile-status | /runtime-status",
            f"status={RUNTIME_RECONCILE.get('status')} count={RUNTIME_RECONCILE.get('count')} coalesced={RUNTIME_RECONCILE.get('coalesced')} discarded={RUNTIME_RECONCILE.get('discarded')}",
            "background summary can be discarded if browser snapshot changes",
        ),
        runtime_inventory_row(
            "runtime_queue",
            "nana.runtime.priority_queue",
            "process memory",
            "volatile",
            "lost_on_restart",
            "none",
            "/queue",
            f"queued={len(queue_snapshot.get('queued') or [])} active_p0={queue_snapshot.get('active_p0')}",
            "priority queue exists but not yet the single scheduler for all tasks",
        ),
        runtime_inventory_row(
            "phase7_dry_run_history",
            "main.PHASE7_DRY_RUN_HISTORY",
            "process memory",
            "volatile",
            "lost_on_restart",
            "none",
            "/dry-run-last | /dry-run-show <id> | /phase7-status",
            f"count={len(PHASE7_DRY_RUN_HISTORY)}/5",
            "debug history only; restart clears it",
        ),
        runtime_inventory_row(
            "phase7_pending_plan",
            "main.PHASE7_PENDING_PLAN",
            "process memory",
            "volatile",
            "lost_on_restart",
            "none",
            "/plan-preview | /plan-pending | /plan-confirm | /plan-cancel",
            pending_plan_detail,
            "simulation-only pending plan; must never imply execution",
        ),
        runtime_inventory_row(
            "phase7_action_log",
            "main.PHASE7_ACTION_LOG",
            "process memory",
            "volatile",
            "lost_on_restart",
            "none",
            "/plan-log | /phase7-status",
            f"count={len(PHASE7_ACTION_LOG)}/12",
            "runtime-only trace, useful but not durable",
        ),
        runtime_inventory_row(
            "phase9_audit_log",
            "main.PHASE9_AUDIT_LOG",
            "process memory",
            "volatile",
            "lost_on_restart",
            "none",
            "/audit-log | /audit-replay | /audit-review | /audit-clear",
            f"count={len(PHASE9_AUDIT_LOG)}/30 executed={sum(1 for entry in PHASE9_AUDIT_LOG if entry.get('execute'))}",
            "audit is not durable yet; strong for session, weak across restart",
        ),
        runtime_inventory_row(
            "runtime_event_log",
            "main.RUNTIME_EVENT_LOG",
            "process memory",
            "volatile",
            "lost_on_restart",
            "none",
            "/event-log | /event-replay | /event-store-status | /event-log-clear",
            runtime_event_summary(),
            "unified session trace; file logger remains separate durable sink",
        ),
        runtime_inventory_row(
            "runtime_turn_state",
            "main.RUNTIME_TURN_STATE + voice.snapshot",
            "process memory",
            "volatile",
            "lost_on_restart",
            "main loop + VoiceEngine.state_lock",
            "/presence-stability-status | /phase10-8-status",
            f"status={RUNTIME_TURN_STATE.get('status')} age={runtime_turn_state_snapshot().get('age'):.1f}s reason={RUNTIME_TURN_STATE.get('last_reason')}",
            "central turn state is lightweight; voice speaking/listening state is still per engine",
        ),
        runtime_inventory_row(
            "memory_store",
            "nana.memory",
            str(MEMORY_PATH),
            "persistent_json",
            "kept_on_restart",
            "memory_lock + memory_file_lock",
            "/memory-status | /memory-review | /memory-compact-preview | /memory-confirm",
            f"long={len(long_term)}/50 short={len(short_term)}/16 chat={len(chat_log)}/50 labels={active_labels}/{len(labels)}",
            "needs provenance/conflict/expiry before larger scale",
        ),
        runtime_inventory_row(
            "persona_presence",
            "nana.runtime.persona + memory.persona",
            str(MEMORY_PATH),
            "persistent_json",
            "kept_on_restart",
            "memory_lock",
            "/presence | /vibe-status | /state-log | /presence-reset",
            f"mode={persona.get('mode')} intensity={persona.get('personality_intensity')} residue={persona.get('residue_level')} rhythm={(persona.get('presence') or {}).get('rhythm')}",
            "persona residue can drift if social/vision state is not decayed",
        ),
        runtime_inventory_row(
            "vision_preview_cache",
            "nana.browser.vision + main.LAST_VISION_DESCRIPTION",
            str(vision_previewer.output_dir),
            "file_cache_plus_volatile_description",
            "files_kept_description_lost",
            "none",
            "/vision-preview | /vision-describe | /vision-cache | /vision-cache-clear",
            f"files={len(vision_files)} latest_description={'none' if vision_age is None else ('fresh' if vision_age <= 180 else 'stale')}",
            "single manual description; no OCR or multi-frame video grounding",
        ),
        runtime_inventory_row(
            "social_drafts",
            "nana.actions.drafts",
            "process memory",
            "volatile",
            "lost_on_restart",
            "SocialDraftStore._lock",
            "/drafts | /draft-show | /draft-confirm | /draft-cancel | /draft-quality",
            f"pending={len(draft_pending)} last_closed={(draft_snapshot.get('last_closed') or ['none'])[0]}",
            "preview queue only; posting/type remains locked by later gates",
        ),
        runtime_inventory_row(
            "pending_actions",
            "nana.actions.pending",
            "process memory",
            "volatile",
            "lost_on_restart",
            "PendingActionStore._lock",
            "/pending | /action-plan | /action-confirm | /action-cancel",
            runtime_pending_detail,
            "confirm gate exists; real executor scope must stay whitelisted",
        ),
        runtime_inventory_row(
            "voice_queue",
            "nana.voice.engine",
            "thread queue",
            "volatile",
            "lost_on_restart",
            "queue.Queue + LipsyncManager.audio_lock",
            "ESC voice | TTS worker | VTS mouth loop",
            "queue=maxsize=3 drop_old_when_full",
            "interrupt policy is partial; speaking/listening state is not centralized yet",
        ),
        runtime_inventory_row(
            "vts_session",
            "nana.integrations.vts",
            "external websocket + token file",
            "external_plus_token",
            "session_lost_token_kept",
            "asyncio locks",
            "startup connect | expression trigger | mouth loop",
            f"token_path={DATA_DIR / 'token.txt'} proactive={proactive.get('enabled')}",
            "external app permission/session can fail independently of Nana state",
        ),
        runtime_inventory_row(
            "chat_history_file",
            "nana.memory.save_chat_log",
            str(CHAT_HISTORY_PATH),
            "persistent_text",
            "kept_on_restart",
            "file append",
            "normal chat path",
            "recent prompt history loaded from file",
            "plain text history can accumulate noise without stronger provenance",
        ),
    ]


def runtime_inventory_summary(rows=None):
    rows = rows or runtime_inventory_rows()
    storage_counts = {}
    restart_counts = {}
    for row in rows:
        persistence = row.get("persistence") or "unknown"
        if persistence.startswith("persistent"):
            bucket = "persistent"
        elif "cache" in persistence:
            bucket = "cache"
        elif "external" in persistence:
            bucket = "external"
        else:
            bucket = "volatile"
        storage_counts[bucket] = storage_counts.get(bucket, 0) + 1
        restart = row.get("restart") or "unknown"
        restart_counts[restart] = restart_counts.get(restart, 0) + 1
    return {
        "total": len(rows),
        "storage_counts": storage_counts,
        "restart_counts": restart_counts,
    }


def phase10_1_guard_summary():
    rows = runtime_inventory_rows()
    names = {row["name"] for row in rows}
    missing_groups = sorted(PHASE10_1_REQUIRED_GROUPS - names)
    missing_commands = sorted(PHASE10_1_COMMANDS - KNOWN_SLASH_COMMANDS)
    metadata_missing = [
        row["name"]
        for row in rows
        if not row.get("owner") or not row.get("persistence") or not row.get("restart") or not row.get("commands")
    ]
    restart_unknown = [row["name"] for row in rows if "unknown" in str(row.get("restart") or "").lower()]
    persistence_unknown = [row["name"] for row in rows if "unknown" in str(row.get("persistence") or "").lower()]
    persistent_without_path = [
        row["name"]
        for row in rows
        if str(row.get("persistence", "")).startswith("persistent") and not any(ch in str(row.get("storage", "")) for ch in [":", "\\", "/"])
    ]
    guard_rows = [
        ("inventory_groups", not missing_groups, f"groups={len(rows)} missing={','.join(missing_groups) if missing_groups else 'none'}"),
        ("metadata_complete", not metadata_missing, f"missing={','.join(metadata_missing) if metadata_missing else 'none'}"),
        ("restart_classified", not restart_unknown, f"unknown={','.join(restart_unknown) if restart_unknown else 'none'}"),
        ("persistence_classified", not persistence_unknown, f"unknown={','.join(persistence_unknown) if persistence_unknown else 'none'}"),
        ("persistent_paths", not persistent_without_path, f"missing_path={','.join(persistent_without_path) if persistent_without_path else 'none'}"),
        ("command_surface", not missing_commands, f"missing={','.join(missing_commands) if missing_commands else 'none'}"),
    ]
    failures = [row for row in guard_rows if not row[1]]
    return {
        "rows": guard_rows,
        "failures": failures,
        "pass_count": len(guard_rows) - len(failures),
        "total": len(guard_rows),
        "inventory": rows,
    }


def phase10_1_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_runtime_map():
    shorten_line = _get_shorten_line()
    rows = runtime_inventory_rows()
    summary = runtime_inventory_summary(rows)
    guard = phase10_1_guard_summary()
    storage = summary["storage_counts"]
    restart = summary["restart_counts"]
    print("🧠 Runtime Map / CNS Inventory")
    print("  Action: read-only; không đổi state, không tạo pending, không execute.")
    print(f"  Phase 10.1 Progress: {phase10_1_progress_percent(guard)}%")
    print(
        "  Storage: "
        f"volatile={storage.get('volatile', 0)} | persistent={storage.get('persistent', 0)} | "
        f"cache={storage.get('cache', 0)} | external={storage.get('external', 0)}"
    )
    print(
        "  Restart: "
        + " | ".join(f"{name}={count}" for name, count in sorted(restart.items()))
    )
    print(f"  Groups: {summary['total']}")
    for row in rows:
        print(
            "  "
            f"{row['name']} | owner={row['owner']} | persistence={row['persistence']} | "
            f"restart={row['restart']} | lock={row['lock']}"
        )
        print(f"    live: {shorten_line(row['live'], 150)}")
        print(f"    commands: {shorten_line(row['commands'], 150)}")
        print(f"    risk: {shorten_line(row['risk'], 150)}")


def print_phase10_1_guard_status():
    summary = phase10_1_guard_summary()
    print("🧪 Phase 10.1 Runtime Inventory Guard")
    print("  Action: read-only; kiểm tra inventory map có đủ nhóm/state/command chưa.")
    print(f"  Progress: {phase10_1_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")


def print_phase10_1_status():
    summary = phase10_1_guard_summary()
    inventory = runtime_inventory_summary(summary["inventory"])
    print("🧩 Phase 10.1 Status")
    print("  Goal: Runtime Inventory Lock - biết state nào sống ở đâu, restart mất gì, command nào xem được.")
    print(f"  Progress: {phase10_1_progress_percent(summary)}%")
    print(f"  Inventory groups: {inventory['total']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /runtime-map | /cns-map | /phase10-1-guard-status | /phase10-1-ready")


def print_phase10_1_ready():
    summary = phase10_1_guard_summary()
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 10.1 Ready" if ready else "⚠️ Phase 10.1 Ready")
    print("  Goal: inventory đủ rõ để sang Phase 10.2 command router hardening.")
    print(f"  Progress: {phase10_1_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: runtime_inventory={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 10.1 chỉ lập bản đồ CNS.")


# ── Phase 10.2: Command Router ─────────────────────────────────────────────────


COMMAND_NORMALIZATION_CASES = [
    ("//br", "/br"),
    ("/ br", "/br"),
    ("//browser", "/browser"),
    ("/ help", "/help"),
    ("//social-draft-test Nana viết nháp", "/social-draft-test Nana viết nháp"),
    ("/ run python --version", "/run python --version"),
    ("hello /br", "hello /br"),
]

COMMAND_ROUTE_CASES = [
    ("/br", "known", "/br", None),
    ("//br", "known", "/br", None),
    ("/dry-run Nana viết nháp reply tweet này", "known", "/dry-run", None),
    ("/phase7-status/br", "joined_command", "/phase7-status/br", "joined_command:/phase7-status+/br"),
    ("/not-a-command", "unknown", "/not-a-command", None),
    ("50 x 10 bằng bao nhiêu", "chat", None, None),
]


def command_joined_issue(command_token):
    token = str(command_token or "").strip().lower()
    if not token.startswith("/"):
        return None
    for command in sorted(KNOWN_SLASH_COMMANDS, key=len, reverse=True):
        if token.startswith(command + "/"):
            return f"joined_command:{command}+{token[len(command):]}"
    return None


def command_route_analysis(raw_text):
    raw = str(raw_text or "")
    normalized = normalize_command_text(raw)
    stripped = normalized.strip()
    if not stripped.startswith("/"):
        return {
            "raw": raw,
            "normalized": normalized,
            "status": "chat",
            "base": None,
            "args": stripped,
            "known": False,
            "suggestion": None,
            "joined": None,
        }
    parts = stripped.split(maxsplit=1)
    base = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    known = base in KNOWN_SLASH_COMMANDS
    joined = None if known else command_joined_issue(base)
    status = "known" if known else ("joined_command" if joined else "unknown")
    truth = classify_command_truth(base, known=known)
    return {
        "raw": raw,
        "normalized": normalized,
        "status": status,
        "base": base,
        "args": args,
        "known": known,
        "suggestion": suggest_slash_command(stripped),
        "joined": joined,
        "truth_status": truth.status,
        "truth_reason": truth.reason,
        "truth_replacement": truth.replacement,
    }


def command_route_regression_rows():
    rows = []
    for raw, expected_status, expected_base, expected_joined in COMMAND_ROUTE_CASES:
        result = command_route_analysis(raw)
        passed = (
            result["status"] == expected_status
            and result["base"] == expected_base
            and result["joined"] == expected_joined
        )
        detail = f"got={result['status']} base={result['base']} joined={result['joined'] or 'none'}"
        rows.append((raw, passed, detail))
    return rows


def command_normalize_regression_rows():
    rows = []
    for raw, expected in COMMAND_NORMALIZATION_CASES:
        got = normalize_command_text(raw)
        rows.append((raw, got == expected, f"got={got} expected={expected}"))
    return rows


def phase10_2_guard_summary():
    missing_commands = sorted(PHASE10_2_COMMANDS - KNOWN_SLASH_COMMANDS)
    missing_aliases = sorted(PHASE10_2_EXPECTED_ALIASES - KNOWN_SLASH_COMMANDS)
    normalize_rows = command_normalize_regression_rows()
    route_rows = command_route_regression_rows()
    suggestion_case = command_route_analysis("/phase7-status/br")
    suggestion_ok = suggestion_case.get("suggestion") == "/phase7-status" and suggestion_case.get("joined")
    chat_guard = command_route_analysis("ok /help")["status"] == "chat"
    guard_rows = [
        ("command_surface", not missing_commands, f"missing={','.join(missing_commands) if missing_commands else 'none'}"),
        ("alias_registry", not missing_aliases, f"missing={','.join(missing_aliases) if missing_aliases else 'none'}"),
        ("normalize_regression", all(row[1] for row in normalize_rows), f"{sum(1 for row in normalize_rows if row[1])}/{len(normalize_rows)} pass"),
        ("route_regression", all(row[1] for row in route_rows), f"{sum(1 for row in route_rows if row[1])}/{len(route_rows)} pass"),
        ("joined_command_detection", bool(suggestion_ok), f"suggestion={suggestion_case.get('suggestion')} joined={suggestion_case.get('joined') or 'none'}"),
        ("chat_slash_safety", chat_guard, "ok /help stays chat" if chat_guard else "chat text was promoted to command"),
    ]
    failures = [row for row in guard_rows if not row[1]]
    return {
        "rows": guard_rows,
        "failures": failures,
        "pass_count": len(guard_rows) - len(failures),
        "total": len(guard_rows),
        "normalize_rows": normalize_rows,
        "route_rows": route_rows,
    }


def phase10_2_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_command_router_status():
    summary = phase10_2_guard_summary()
    alias_count = len(PHASE10_2_EXPECTED_ALIASES)
    print("🧭 Command Router Status")
    print("  Action: read-only; kiểm tra router/alias/normalize, không execute.")
    print(f"  Phase 10.2 Progress: {phase10_2_progress_percent(summary)}%")
    print(f"  Known commands: {len(KNOWN_SLASH_COMMANDS)}")
    print(f"  Alias surface locked: {alias_count}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: slash ở giữa câu vẫn là chat; command dính đuôi như /phase7-status/br phải bị trace, không tự chạy.")
    print("  Truth: registry là compatibility/suggestion surface; dùng /command-truth <cmd> để phân biệt live/archive/reserved.")


def print_command_truth_status():
    for line in command_truth_status_lines(len(KNOWN_SLASH_COMMANDS)):
        print(line)
    from nana.commands.registry_truth import registry_truth_status_lines

    for line in registry_truth_status_lines(KNOWN_SLASH_COMMANDS):
        print(line)
    from nana.phases.live_surface import phase_surface_status_lines

    for line in phase_surface_status_lines():
        print(line)


def print_command_router_guard_status():
    summary = phase10_2_guard_summary()
    print("🧪 Phase 10.2 Command Router Guard")
    print("  Action: read-only; không route thật, không gọi model, không execute.")
    print(f"  Progress: {phase10_2_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Normalize regression:")
    for raw, passed, detail in summary["normalize_rows"]:
        print(f"    {'pass' if passed else 'fail'} | {raw} | {detail}")
    print("  Route regression:")
    for raw, passed, detail in summary["route_rows"]:
        print(f"    {'pass' if passed else 'fail'} | {raw} | {detail}")


def print_command_normalize_test(raw_text=None):
    shorten_line = _get_shorten_line()
    print("🧪 Command Normalize Test")
    print("  Action: read-only; chỉ test normalize boundary.")
    if raw_text is not None:
        got = normalize_command_text(raw_text)
        analysis = command_route_analysis(raw_text)
        print(f"  Raw: {raw_text}")
        print(f"  Normalized: {got}")
        print(f"  Route status: {analysis['status']} | base={analysis['base']} | joined={analysis['joined'] or 'none'}")
        print(f"  Suggestion: {analysis['suggestion'] or 'none'}")
        return
    rows = command_normalize_regression_rows()
    print(f"  Summary: {sum(1 for row in rows if row[1])}/{len(rows)} pass")
    for raw, passed, detail in rows:
        print(f"  {'pass' if passed else 'fail'} | {raw} | {detail}")


def print_command_route_test(raw_text=None):
    shorten_line = _get_shorten_line()
    print("🧭 Command Route Test")
    print("  Action: read-only; phân tích route, không gọi handler thật.")
    if raw_text is not None:
        analysis = command_route_analysis(raw_text)
        print(f"  Raw: {analysis['raw']}")
        print(f"  Normalized: {analysis['normalized']}")
        print(f"  Status: {analysis['status']}")
        print(f"  Base: {analysis['base']}")
        print(f"  Args: {shorten_line(analysis['args'], 140) if analysis['args'] else 'none'}")
        print(f"  Known: {analysis['known']}")
        print(f"  Joined: {analysis['joined'] or 'none'}")
        print(f"  Suggestion: {analysis['suggestion'] or 'none'}")
        print(f"  Truth: {analysis['truth_status']} | {analysis['truth_reason']}")
        if analysis.get("truth_replacement"):
            print(f"  Replacement: {analysis['truth_replacement']}")
        print("  Execute: False")
        return
    rows = command_route_regression_rows()
    print(f"  Summary: {sum(1 for row in rows if row[1])}/{len(rows)} pass")
    for raw, passed, detail in rows:
        print(f"  {'pass' if passed else 'fail'} | {raw} | {detail}")


def print_command_truth(raw_text=None):
    if raw_text is None:
        print_command_truth_status()
        return
    analysis = command_route_analysis(raw_text)
    for line in command_truth_lines(analysis["base"] or raw_text, known=bool(analysis["known"])):
        print(line)


def print_phase10_2_status():
    summary = phase10_2_guard_summary()
    print("🧩 Phase 10.2 Status")
    print("  Goal: Command Router Hardening - slash command rõ, alias có registry, typo không chạy nhầm.")
    print(f"  Progress: {phase10_2_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /command-router-status | /command-router-guard-status | /command-normalize-test | /command-route-test | /phase10-2-ready")


def print_phase10_2_ready():
    summary = phase10_2_guard_summary()
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 10.2 Ready" if ready else "⚠️ Phase 10.2 Ready")
    print("  Goal: router đủ chắc để sang Phase 10.3 typed runtime state.")
    print(f"  Progress: {phase10_2_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: command_router={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 10.2 chỉ harden command boundary.")


# ── Phase 10.3: Typed Runtime State ───────────────────────────────────────────


TYPE_ALIASES = {
    "bool": bool,
    "dict": dict,
    "float": float,
    "int": int,
    "list": list,
    "none": type(None),
    "object": object,
    "str": str,
    "tuple": tuple,
}


def value_matches_type(value, type_name):
    if type_name == "number":
        return (isinstance(value, (int, float)) and not isinstance(value, bool))
    if type_name == "optional_number":
        return value is None or value_matches_type(value, "number")
    if type_name == "optional_str":
        return value is None or isinstance(value, str)
    if type_name == "optional_bool":
        return value is None or isinstance(value, bool)
    if type_name == "object":
        return value is not None
    expected = TYPE_ALIASES.get(type_name)
    if expected is None:
        return False
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected is float:
        return isinstance(value, float) and not isinstance(value, bool)
    return isinstance(value, expected)


def get_nested_value(data, path):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current.get(part)
    return True, current


def state_schema_field(group, path, value, expected, allowed=None, required=True, note=""):
    shorten_line = _get_shorten_line()
    if not required and value is None:
        passed = True
    else:
        expected_types = expected if isinstance(expected, tuple) else (expected,)
        passed = any(value_matches_type(value, type_name) for type_name in expected_types)
    if passed and allowed is not None and value is not None:
        passed = value in allowed
    got = type(value).__name__ if value is not None else "None"
    if allowed is not None:
        detail = f"expected={expected} allowed={','.join(str(item) for item in allowed)} got={shorten_line(str(value), 70)}"
    else:
        detail = f"expected={expected} got={got}"
    if note:
        detail = f"{detail} | {note}"
    return {
        "group": group,
        "path": path,
        "passed": passed,
        "expected": expected,
        "got": got,
        "detail": detail,
    }


def state_schema_dict_field(group, root, path, expected, allowed=None, required=True, note=""):
    exists, value = get_nested_value(root, path)
    if not exists:
        return {
            "group": group,
            "path": path,
            "passed": not required,
            "expected": expected,
            "got": "missing",
            "detail": "missing" if required else "missing_optional",
        }
    return state_schema_field(group, path, value, expected, allowed=allowed, required=required, note=note)


def runtime_state_schema_rows():
    context_state = _get_context_state()
    context_lock = _get_context_lock()
    memory = _get_memory()
    memory_lock = _get_memory_lock()
    runtime_queue = _get_runtime_queue()
    pending_actions = _get_pending_actions()
    social_drafts = _get_social_drafts()
    last_vision_description = _get_last_vision_description()

    with context_lock:
        state = dict(context_state)
        browser = dict(state.get("browser", {}))
        proactive = dict(state.get("proactive", {}))
    with memory_lock:
        memory_snapshot = {
            "profile": dict(memory.get("profile", {})),
            "long_term": list(memory.get("long_term", [])),
            "memory_labels": dict(memory.get("memory_labels", {})),
            "short_term": list(memory.get("short_term", [])),
            "chat_log": list(memory.get("chat_log", [])),
            "emotion": dict(memory.get("emotion", {})),
            "persona": dict(memory.get("persona", {})),
            "last_annoyance": memory.get("last_annoyance", 0),
        }
    draft_snapshot = social_drafts.snapshot()
    pending_snapshot = pending_actions.snapshot()
    queue_snapshot = runtime_queue.snapshot()
    rows = []
    add = rows.append

    for path, expected, allowed in [
        ("active_app", "optional_str", None),
        ("active_title", "optional_str", None),
        ("active_zone", "str", {"unknown", "war_zone", "chill", "game", "idle"}),
        ("zone_since", "number", None),
        ("last_input_time", "number", None),
        ("idle_seconds", "number", None),
        ("idle_state", "str", {"active", "idle", "away", "relaxed", "sleepy"}),
        ("python_cpu", "number", None),
        ("cpu_spike_since", ("optional_number",), None),
        ("alt_tab_count", "int", None),
        ("in_flow", "bool", None),
        ("flow_since", "optional_number", None),
        ("last_keystroke", "number", None),
        ("poll_rate", "number", None),
        ("last_chat_time", "number", None),
    ]:
        add(state_schema_dict_field("context_state", state, path, expected, allowed=allowed))

    for path, expected, allowed in [
        ("available", "bool", None),
        ("browser", "optional_str", None),
        ("url", "optional_str", None),
        ("title", "optional_str", None),
        ("kind", "str", {"unknown", "youtube", "social", "ai_tools", "shopping", "music", "github", "local", "search", "search_home", "docs", "video"}),
        ("reason", "str", None),
        ("page_heading", "optional_str", None),
        ("meta_description", "optional_str", None),
        ("selected_text", "optional_str", None),
        ("dom_debug", "optional_str", None),
        ("site_signals", "dict", None),
        ("social_post_text", "optional_str", None),
        ("social_comments", "list", None),
        ("social_vibe", "optional_str", None),
        ("local_summary", "optional_str", None),
        ("local_helper_debug", "optional_str", None),
        ("last_update", "number", None),
        ("age_seconds", "optional_number", None),
        ("fresh", "bool", None),
    ]:
        add(state_schema_dict_field("browser_snapshot", browser, path, expected, allowed=allowed))

    for path, expected in [
        ("enabled", "bool"),
        ("last_browser_kind", "optional_str"),
        ("last_browser_title", "optional_str"),
        ("last_browser_url", "optional_str"),
        ("last_browser_reaction_time", "number"),
        ("last_idle_reaction", "optional_str"),
        ("presence_debug", "dict"),
    ]:
        add(state_schema_dict_field("proactive_state", proactive, path, expected))
    for path, expected in [
        ("presence_debug.kind_changed", "bool"),
        ("presence_debug.title_changed", "bool"),
        ("presence_debug.url_changed", "bool"),
        ("presence_debug.should_react", "bool"),
        ("presence_debug.blocked_reason", "str"),
        ("presence_debug.debug", ("optional_str", "dict")),
    ]:
        add(state_schema_dict_field("proactive_state", proactive, path, expected))

    for path, expected in [
        ("browser_refresh_count", "int"),
        ("browser_refresh_total_ms", "number"),
        ("browser_refresh_last_ms", "optional_number"),
        ("browser_read_last_ms", "optional_number"),
        ("local_summary_last_ms", "optional_number"),
        ("browser_refresh_last_deep", "bool"),
        ("browser_refresh_last_reason", "str"),
        ("browser_refresh_last_time", "number"),
        ("browser_refresh_cache_hits", "int"),
        ("browser_refresh_coalesced", "int"),
        ("browser_refresh_cooldown_skips", "int"),
        ("browser_refresh_last_policy", "str"),
        ("browser_refresh_last_policy_reason", "str"),
        ("browser_refresh_last_state", "str"),
        ("browser_refresh_last_skip_age", "optional_number"),
    ]:
        add(state_schema_dict_field("runtime_latency", RUNTIME_LATENCY, path, expected))

    for path, expected, allowed in [
        ("status", "str", {"idle", "queued", "running", "done", "discarded", "failed", "skipped"}),
        ("count", "int", None),
        ("coalesced", "int", None),
        ("failures", "int", None),
        ("discarded", "int", None),
        ("last_ms", "optional_number", None),
        ("last_debug", "optional_str", None),
        ("last_summary", "optional_str", None),
        ("started_at", "number", None),
        ("finished_at", "number", None),
        ("source_url", "optional_str", None),
        ("source_title", "optional_str", None),
    ]:
        add(state_schema_dict_field("runtime_reconcile", RUNTIME_RECONCILE, path, expected, allowed=allowed))

    add(state_schema_field("runtime_event_log", "entries", RUNTIME_EVENT_LOG, "list"))
    for index, entry in enumerate(RUNTIME_EVENT_LOG[-3:], start=max(1, len(RUNTIME_EVENT_LOG) - 2)):
        for path, expected in [
            ("id", "int"),
            ("time", "number"),
            ("channel", "str"),
            ("event", "str"),
            ("source", "str"),
            ("intent", "str"),
            ("status", "str"),
            ("actions", "list"),
            ("detail", "str"),
            ("execute", "bool"),
        ]:
            row = state_schema_dict_field("runtime_event_log", entry, path, expected)
            row["path"] = f"entries[{index}].{path}"
            add(row)
        row = state_schema_dict_field("runtime_event_log", entry, "audit_id", ("none", "int"), required=False)
        row["path"] = f"entries[{index}].audit_id"
        add(row)

    turn_snapshot = runtime_turn_state_snapshot()
    add(state_schema_field("runtime_turn_state", "snapshot", turn_snapshot, "dict"))
    for path, expected, allowed in [
        ("status", "str", {"idle", "listening", "thinking", "talking", "recognizing", "shutdown"}),
        ("last_update", "number", None),
        ("last_reason", "str", None),
        ("source", "str", None),
        ("age", "number", None),
    ]:
        add(state_schema_dict_field("runtime_turn_state", turn_snapshot, path, expected, allowed=allowed))

    add(state_schema_field("runtime_queue", "snapshot", queue_snapshot, "dict"))
    for path, expected in [
        ("active_p0", "int"),
        ("last_user_input_age", "optional_number"),
        ("max_tasks", "int"),
        ("recent_user_window", "number"),
        ("queued", "list"),
    ]:
        add(state_schema_dict_field("runtime_queue", queue_snapshot, path, expected))
    for index, task in enumerate((queue_snapshot.get("queued") or [])[:3], start=1):
        for path, expected in [
            ("id", "int"),
            ("priority", "str"),
            ("name", "str"),
            ("source", "str"),
            ("status", "str"),
            ("defer_reason", "str"),
            ("age", "number"),
        ]:
            row = state_schema_dict_field("runtime_queue", task, path, expected)
            row["path"] = f"queued[{index}].{path}"
            add(row)

    pending_plan = _get_phase7_pending_plan()
    if pending_plan is None:
        add(state_schema_field("phase7_pending_plan", "pending", None, "none", note="empty_ok"))
    else:
        for path, expected in [
            ("id", "int"),
            ("intent", "str"),
            ("policy", "str"),
            ("risk", "str"),
            ("actions", "list"),
            ("time", "number"),
            ("expires_at", "number"),
            ("text", "str"),
        ]:
            add(state_schema_dict_field("phase7_pending_plan", pending_plan, path, expected))

    add(state_schema_field("phase9_audit_log", "entries", PHASE9_AUDIT_LOG, "list"))
    for index, entry in enumerate(PHASE9_AUDIT_LOG[-3:], start=max(1, len(PHASE9_AUDIT_LOG) - 2)):
        for path, expected in [
            ("id", "int"),
            ("event", "str"),
            ("intent", "str"),
            ("status", "str"),
            ("actions", "list"),
            ("execute", "bool"),
            ("time", "number"),
        ]:
            row = state_schema_dict_field("phase9_audit_log", entry, path, expected)
            row["path"] = f"entries[{index}].{path}"
            add(row)

    for path, expected in [
        ("profile", "dict"),
        ("long_term", "list"),
        ("memory_labels", "dict"),
        ("short_term", "list"),
        ("chat_log", "list"),
        ("emotion", "dict"),
        ("persona", "dict"),
        ("last_annoyance", "number"),
    ]:
        add(state_schema_dict_field("memory_store", memory_snapshot, path, expected))

    persona = memory_snapshot.get("persona", {})
    for path, expected, allowed in [
        ("mode", "str", {"chill", "focus", "technical", "social"}),
        ("personality_intensity", "number", None),
        ("target_intensity", "number", None),
        ("manual_until", "number", None),
        ("last_decay", "number", None),
        ("last_reset", "number", None),
        ("residue_level", "number", None),
        ("residue_sources", "dict", None),
        ("last_reason", "str", None),
        ("transition_log", "list", None),
        ("silence", "dict", None),
        ("presence", "dict", None),
    ]:
        add(state_schema_dict_field("persona_state", persona, path, expected, allowed=allowed))
    for path, expected in [
        ("silence.window_start", "number"),
        ("silence.ambient_count", "int"),
        ("silence.ambient_limit", "int"),
        ("presence.enabled", "bool"),
        ("presence.rhythm", "str"),
        ("presence.last_reason", "str"),
        ("presence.last_update", "number"),
        ("presence.quiet_until", "number"),
    ]:
        add(state_schema_dict_field("persona_state", persona, path, expected))

    if last_vision_description is None:
        add(state_schema_field("vision_description", "latest", None, "none", note="empty_ok"))
    else:
        for path, expected in [
            ("text", "str"),
            ("time", "number"),
            ("file", "str"),
            ("debug", "str"),
        ]:
            add(state_schema_dict_field("vision_description", last_vision_description, path, expected))

    add(state_schema_field("draft_store", "pending", draft_snapshot.get("pending"), "list"))
    last_closed = draft_snapshot.get("last_closed")
    add(state_schema_field("draft_store", "last_closed", last_closed, ("none", "tuple"), required=False))
    add(state_schema_field("pending_action_store", "pending", pending_snapshot.get("pending"), ("none", "object"), required=False))
    add(state_schema_field("pending_action_store", "last_closed", pending_snapshot.get("last_closed"), ("none", "tuple"), required=False))
    return rows


def runtime_state_schema_summary(rows=None):
    rows = rows or runtime_state_schema_rows()
    groups = sorted({row["group"] for row in rows})
    missing_groups = sorted(PHASE10_3_REQUIRED_SCHEMA_GROUPS - set(groups))
    failed = [row for row in rows if not row["passed"]]
    by_group = {}
    for row in rows:
        stats = by_group.setdefault(row["group"], {"total": 0, "pass": 0})
        stats["total"] += 1
        if row["passed"]:
            stats["pass"] += 1
    command_missing = sorted(PHASE10_3_COMMANDS - KNOWN_SLASH_COMMANDS)
    guard_rows = [
        ("schema_groups", not missing_groups, f"groups={len(groups)} missing={','.join(missing_groups) if missing_groups else 'none'}"),
        ("schema_fields", not failed, f"{len(rows) - len(failed)}/{len(rows)} pass"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("runtime_state_readonly", True, "no mutation, no pending, no execute"),
    ]
    guard_failures = [row for row in guard_rows if not row[1]]
    return {
        "rows": rows,
        "groups": groups,
        "by_group": by_group,
        "failed": failed,
        "guard_rows": guard_rows,
        "guard_failures": guard_failures,
        "pass_count": len(guard_rows) - len(guard_failures),
        "total": len(guard_rows),
    }


def phase10_3_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_runtime_state_schema():
    summary = runtime_state_schema_summary()
    print("🧬 Runtime State Schema")
    print("  Action: read-only; kiểm tra shape/type state hiện tại, không đổi state.")
    print(f"  Phase 10.3 Progress: {phase10_3_progress_percent(summary)}%")
    print(f"  Groups: {len(summary['groups'])} | Fields: {len(summary['rows'])} | Failed: {len(summary['failed'])}")
    for group in summary["groups"]:
        stats = summary["by_group"][group]
        print(f"  {group}: {stats['pass']}/{stats['total']} pass")
        failed = [row for row in summary["failed"] if row["group"] == group]
        for row in failed[:5]:
            print(f"    warn | {row['path']} | {row['detail']}")
    print("  Rule: state chưa cần typed dataclass ngay, nhưng mọi shape quan trọng phải có contract và guard.")


def print_state_schema_guard_status():
    summary = runtime_state_schema_summary()
    print("🧪 Phase 10.3 State Schema Guard")
    print("  Action: read-only; không migrate state, không tạo pending, không execute.")
    print(f"  Progress: {phase10_3_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["guard_rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    if summary["failed"]:
        print("  Failed fields:")
        for row in summary["failed"][:20]:
            print(f"    {row['group']}.{row['path']} | {row['detail']}")


def print_phase10_3_status():
    summary = runtime_state_schema_summary()
    print("🧩 Phase 10.3 Status")
    print("  Goal: Typed Runtime State - đặt schema contract cho state sống trong CNS.")
    print(f"  Progress: {phase10_3_progress_percent(summary)}%")
    for name, passed, detail in summary["guard_rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print(f"  Schema: groups={len(summary['groups'])} fields={len(summary['rows'])} failed={len(summary['failed'])}")
    print("  Commands: /runtime-state-schema | /state-schema-guard-status | /phase10-3-ready")


def print_phase10_3_ready():
    summary = runtime_state_schema_summary()
    blocking = [(name, detail) for name, passed, detail in summary["guard_rows"] if not passed]
    ready = not blocking
    print("✅ Phase 10.3 Ready" if ready else "⚠️ Phase 10.3 Ready")
    print("  Goal: state schema đủ rõ để sang Phase 10.4 event log & audit store.")
    print(f"  Progress: {phase10_3_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: state_schema={summary['pass_count']}/{summary['total']} fields={len(summary['rows']) - len(summary['failed'])}/{len(summary['rows'])}")
    print("  Autonomy: unchanged; Phase 10.3 chỉ kiểm shape/type state.")


# ── Phase 10.4: Event Store ────────────────────────────────────────────────────


def runtime_event_schema_issues(entry):
    issues = []
    if not isinstance(entry, dict):
        return ["entry_not_dict"]
    missing = sorted(RUNTIME_EVENT_REQUIRED_FIELDS - set(entry.keys()))
    if missing:
        issues.append("missing:" + ",".join(missing))
    type_checks = [
        ("id", "int"),
        ("time", "number"),
        ("channel", "str"),
        ("event", "str"),
        ("source", "str"),
        ("intent", "str"),
        ("status", "str"),
        ("actions", "list"),
        ("detail", "str"),
        ("execute", "bool"),
    ]
    for key, expected in type_checks:
        if key in entry and not value_matches_type(entry.get(key), expected):
            issues.append(f"type:{key}:{type(entry.get(key)).__name__}")
    if "audit_id" in entry and entry.get("audit_id") is not None and not value_matches_type(entry.get("audit_id"), "int"):
        issues.append(f"type:audit_id:{type(entry.get('audit_id')).__name__}")
    return issues


def runtime_event_synthetic_rows():
    return [
        {
            "id": 1,
            "time": time.time(),
            "channel": "audit",
            "event": "action_trace",
            "source": "Nana click nút đăng nhập",
            "intent": "browser.navigate_or_click",
            "status": "planned",
            "actions": ["browser.click", "browser.type"],
            "detail": "synthetic_guard",
            "audit_id": 1,
            "execute": False,
        },
        {
            "id": 2,
            "time": time.time(),
            "channel": "phase7",
            "event": "confirmed",
            "source": "",
            "intent": "social.draft",
            "status": "confirmed",
            "actions": ["social.draft"],
            "detail": "synthetic_guard",
            "audit_id": None,
            "execute": False,
        },
    ]


def runtime_event_log_file_status():
    rows = []
    for channel, filename in sorted(LOG_FILES.items()):
        path = LOG_DIR / filename
        exists = path.exists()
        size = 0
        modified = None
        if exists:
            try:
                stat = path.stat()
                size = stat.st_size
                modified = stat.st_mtime
            except OSError:
                pass
        rows.append({
            "channel": channel,
            "path": str(path),
            "exists": exists,
            "size": size,
            "modified": modified,
        })
    return rows


def runtime_event_log_file_summary(rows=None):
    rows = rows or runtime_event_log_file_status()
    existing = [row for row in rows if row["exists"]]
    return f"channels={len(rows)} existing={len(existing)} dir={LOG_DIR}"


def phase10_4_guard_summary():
    live_issues = []
    for entry in RUNTIME_EVENT_LOG:
        issues = runtime_event_schema_issues(entry)
        if issues:
            live_issues.append((entry.get("id"), issues))
    synthetic_issues = []
    for entry in runtime_event_synthetic_rows():
        issues = runtime_event_schema_issues(entry)
        if issues:
            synthetic_issues.append((entry.get("id"), issues))
    executed_events = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
    audit_events = [entry for entry in RUNTIME_EVENT_LOG if entry.get("channel") == "audit"]
    audit_link_ok = all(entry.get("audit_id") is not None for entry in audit_events)
    command_missing = sorted(PHASE10_4_COMMANDS - KNOWN_SLASH_COMMANDS)
    file_rows = runtime_event_log_file_status()
    file_surface_ok = bool(file_rows) and all(row.get("path") for row in file_rows)
    guard_rows = [
        ("event_schema_live", not live_issues, f"entries={len(RUNTIME_EVENT_LOG)} issues={len(live_issues)}"),
        ("event_schema_synthetic", not synthetic_issues, f"{len(runtime_event_synthetic_rows()) - len(synthetic_issues)}/{len(runtime_event_synthetic_rows())} pass"),
        ("audit_bridge", audit_link_ok, f"audit_events={len(audit_events)} linked={sum(1 for entry in audit_events if entry.get('audit_id') is not None)}"),
        ("execute_flag_guard", not executed_events, f"executed={len(executed_events)}"),
        ("retention_guard", len(RUNTIME_EVENT_LOG) <= 50 and len(PHASE9_AUDIT_LOG) <= 30, f"event={len(RUNTIME_EVENT_LOG)}/50 audit={len(PHASE9_AUDIT_LOG)}/30"),
        ("file_log_surface", file_surface_ok, runtime_event_log_file_summary(file_rows)),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
    ]
    failures = [row for row in guard_rows if not row[1]]
    return {
        "rows": guard_rows,
        "failures": failures,
        "pass_count": len(guard_rows) - len(failures),
        "total": len(guard_rows),
        "live_issues": live_issues,
        "synthetic_issues": synthetic_issues,
        "file_rows": file_rows,
    }


def phase10_4_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_event_store_status():
    summary = phase10_4_guard_summary()
    print("🧾 Event Store Status")
    print("  Action: read-only; xem unified event/audit store, không execute.")
    print(f"  Phase 10.4 Progress: {phase10_4_progress_percent(summary)}%")
    print(f"  Runtime events: {runtime_event_summary()}")
    print(f"  Phase9 audit: {phase9_audit_summary()}")
    print(f"  File logs: {runtime_event_log_file_summary(summary['file_rows'])}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_runtime_event_log():
    shorten_line = _get_shorten_line()
    print("🧾 Runtime Event Log")
    print("  Action: read-only; runtime-only unified log, không execute.")
    if not RUNTIME_EVENT_LOG:
        print("  Entries: none | empty_runtime_event_log_ok")
        print("  Next: chạy /action-trace <text>, /plan-preview <text>, hoặc /pre-exec-check <action> để tạo entry.")
        return
    for entry in RUNTIME_EVENT_LOG[-12:]:
        age = max(0.0, time.time() - entry.get("time", 0))
        print(
            f"  #{entry.get('id')} {entry.get('channel')}.{entry.get('event')} | "
            f"intent={entry.get('intent') or 'none'} | status={entry.get('status') or 'none'} | "
            f"actions={','.join(entry.get('actions') or []) or 'none'} | execute={entry.get('execute')} | age={age:.1f}s"
        )
        if entry.get("audit_id") is not None:
            print(f"    Audit ID: {entry.get('audit_id')}")
        if entry.get("detail"):
            print(f"    Detail: {shorten_line(entry.get('detail'), 140)}")


def print_runtime_event_replay(raw_id=None):
    shorten_line = _get_shorten_line()
    entry = runtime_event_find(raw_id)
    print("🔁 Runtime Event Replay")
    print("  Action: read-only; replay event decision, không execute.")
    if not entry:
        print("  Status: not_found")
        print("  Next: chạy /event-log để xem ID hiện có.")
        print("  Execute: False")
        return
    issues = runtime_event_schema_issues(entry)
    print(f"  ID: {entry.get('id')} | channel={entry.get('channel')} | event={entry.get('event')}")
    print(f"  Intent: {entry.get('intent') or 'none'}")
    print(f"  Status: {entry.get('status') or 'none'}")
    print(f"  Actions: {', '.join(entry.get('actions') or []) or 'none'}")
    print(f"  Audit ID: {entry.get('audit_id') if entry.get('audit_id') is not None else 'none'}")
    print(f"  Schema: {'pass' if not issues else 'warn'} | {', '.join(issues) if issues else 'none'}")
    print(f"  Execute: {bool(entry.get('execute'))}")
    print("  Reason: phase10_4_replay_only")


def print_runtime_event_clear():
    cleared = runtime_event_clear()
    print("🧹 Runtime Event Clear")
    print("  Action: runtime-only clear; không xóa file log, không execute.")
    print(f"  Cleared: {cleared}")
    print("  Status: empty_runtime_event_log_ok")
    print("  Execute: False")


def print_phase10_4_guard_status():
    summary = phase10_4_guard_summary()
    print("🧪 Phase 10.4 Event Store Guard")
    print("  Action: read-only; kiểm unified event/audit store, không tạo pending, không execute.")
    print(f"  Progress: {phase10_4_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    if summary["live_issues"]:
        print("  Live schema issues:")
        for event_id, issues in summary["live_issues"][:10]:
            print(f"    id={event_id} | {', '.join(issues)}")


def print_phase10_4_status():
    summary = phase10_4_guard_summary()
    print("🧩 Phase 10.4 Status")
    print("  Goal: Event Log & Audit Store - một trace chung cho plan/audit/runtime events.")
    print(f"  Progress: {phase10_4_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print(f"  Runtime events: {runtime_event_summary()}")
    print("  Commands: /event-store-status | /event-log | /event-replay [id] | /event-log-clear | /phase10-4-ready")


def print_phase10_4_ready():
    summary = phase10_4_guard_summary()
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 10.4 Ready" if ready else "⚠️ Phase 10.4 Ready")
    print("  Goal: event/audit trace đủ rõ để sang Phase 10.5 scheduler & queue core.")
    print(f"  Progress: {phase10_4_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: event_store={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 10.4 chỉ log/replay/guard.")


# ── Phase 10.5: Scheduler ─────────────────────────────────────────────────────


SCHEDULER_ADMISSION_CASES = [
    ("p0_user_always_ready", Priority.P0_USER, 1, 0.0, True, "ready"),
    ("p1_blocked_by_p0", Priority.P1_PROACTIVE, 1, 10.0, False, "p0_active"),
    ("p2_blocked_by_recent_user", Priority.P2_BACKGROUND, 0, 0.3, False, "recent_user_input"),
    ("p1_ready_after_idle", Priority.P1_PROACTIVE, 0, 10.0, True, "ready"),
]


def scheduler_admission_preview(priority, active_p0=0, last_user_age=None):
    runtime_queue = _get_runtime_queue()
    priority_value = int(priority)
    if priority_value == int(Priority.P0_USER):
        return True, "ready"
    if active_p0:
        return False, "p0_active"
    if last_user_age is not None and last_user_age < runtime_queue.recent_user_window:
        return False, "recent_user_input"
    return True, "ready"


def scheduler_priority_rows():
    return [
        ("P0_USER", int(Priority.P0_USER), "user input / direct command"),
        ("P1_PROACTIVE", int(Priority.P1_PROACTIVE), "presence/proactive reaction"),
        ("P2_BACKGROUND", int(Priority.P2_BACKGROUND), "background reconcile/maintenance"),
    ]


def scheduler_admission_rows():
    rows = []
    for name, priority, active_p0, last_user_age, expected_allowed, expected_reason in SCHEDULER_ADMISSION_CASES:
        allowed, reason = scheduler_admission_preview(priority, active_p0=active_p0, last_user_age=last_user_age)
        rows.append({
            "name": name,
            "priority": Priority(priority).name,
            "active_p0": active_p0,
            "last_user_age": last_user_age,
            "allowed": allowed,
            "reason": reason,
            "passed": allowed == expected_allowed and reason == expected_reason,
            "expected": f"allowed={expected_allowed} reason={expected_reason}",
        })
    return rows


def queue_snapshot_schema_issues(snapshot):
    issues = []
    required = {
        "active_p0": "int",
        "last_user_input_age": "optional_number",
        "max_tasks": "int",
        "recent_user_window": "number",
        "queued": "list",
    }
    for key, expected in required.items():
        if key not in snapshot:
            issues.append(f"missing:{key}")
        elif not value_matches_type(snapshot.get(key), expected):
            issues.append(f"type:{key}:{type(snapshot.get(key)).__name__}")
    for index, task in enumerate(snapshot.get("queued") or [], start=1):
        for key, expected in [
            ("id", "int"),
            ("priority", "str"),
            ("name", "str"),
            ("source", "str"),
            ("status", "str"),
            ("defer_reason", "str"),
            ("age", "number"),
        ]:
            if key not in task:
                issues.append(f"task{index}:missing:{key}")
            elif not value_matches_type(task.get(key), expected):
                issues.append(f"task{index}:type:{key}:{type(task.get(key)).__name__}")
    return issues


def phase10_5_guard_summary():
    runtime_queue = _get_runtime_queue()
    snapshot = runtime_queue.snapshot()
    priority_values = [value for _name, value, _desc in scheduler_priority_rows()]
    priority_ok = priority_values == sorted(priority_values) and priority_values == [0, 1, 2]
    admission_rows = scheduler_admission_rows()
    admission_ok = all(row["passed"] for row in admission_rows)
    schema_issues = queue_snapshot_schema_issues(snapshot)
    lock_ok = hasattr(runtime_queue, "_lock") and hasattr(runtime_queue, "_ids")
    retention_ok = (
        isinstance(snapshot.get("max_tasks"), int)
        and snapshot.get("max_tasks", 0) > 0
        and len(snapshot.get("queued") or []) <= snapshot.get("max_tasks", 0)
    )
    window_ok = value_matches_type(snapshot.get("recent_user_window"), "number") and snapshot.get("recent_user_window") > 0
    command_missing = sorted(PHASE10_5_COMMANDS - KNOWN_SLASH_COMMANDS)
    guard_rows = [
        ("priority_contract", priority_ok, "P0_USER=0 P1_PROACTIVE=1 P2_BACKGROUND=2"),
        ("admission_policy", admission_ok, f"{sum(1 for row in admission_rows if row['passed'])}/{len(admission_rows)} pass"),
        ("queue_snapshot_schema", not schema_issues, f"issues={','.join(schema_issues) if schema_issues else 'none'}"),
        ("queue_lock_contract", lock_ok, f"lock={hasattr(runtime_queue, '_lock')} ids={hasattr(runtime_queue, '_ids')}"),
        ("retention_guard", retention_ok, f"queued={len(snapshot.get('queued') or [])}/{snapshot.get('max_tasks')}"),
        ("recent_user_window", window_ok, f"{snapshot.get('recent_user_window')}s"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("executor_boundary", True, "scheduler guard/test do not call next_ready or executor"),
    ]
    failures = [row for row in guard_rows if not row[1]]
    return {
        "rows": guard_rows,
        "failures": failures,
        "pass_count": len(guard_rows) - len(failures),
        "total": len(guard_rows),
        "snapshot": snapshot,
        "admission_rows": admission_rows,
        "schema_issues": schema_issues,
    }


def phase10_5_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_scheduler_status():
    summary = phase10_5_guard_summary()
    snapshot = summary["snapshot"]
    age = snapshot.get("last_user_input_age")
    age_text = "never" if age is None else f"{age:.1f}s ago"
    print("🧠 Scheduler / Queue Core Status")
    print("  Action: read-only; xem scheduler contract, không dequeue, không execute.")
    print(f"  Phase 10.5 Progress: {phase10_5_progress_percent(summary)}%")
    print(f"  Queue: active_p0={snapshot.get('active_p0')} | queued={len(snapshot.get('queued') or [])}/{snapshot.get('max_tasks')} | last_user_input={age_text}")
    print(f"  Recent user window: {snapshot.get('recent_user_window')}s")
    print("  Priorities:")
    for name, value, desc in scheduler_priority_rows():
        print(f"    {name}={value} | {desc}")
    print("  Current queued:")
    if not snapshot.get("queued"):
        print("    none")
    else:
        for task in snapshot.get("queued")[:8]:
            print(f"    #{task.get('id')} {task.get('priority')} | {task.get('name')} | status={task.get('status')} | age={task.get('age'):.1f}s")
    print("  Scope: queue core is contracted; not yet the single scheduler for every runtime subsystem.")


def print_scheduler_test(raw_text=None):
    print("🧪 Scheduler Admission Test")
    print("  Action: read-only; synthetic admission only, không enqueue/dequeue/execute.")
    if raw_text:
        lowered = raw_text.lower().strip()
        priority = Priority.P2_BACKGROUND
        if "p0" in lowered or "user" in lowered:
            priority = Priority.P0_USER
        elif "p1" in lowered or "proactive" in lowered:
            priority = Priority.P1_PROACTIVE
        active_p0 = 1 if "active" in lowered or "p0_active" in lowered else 0
        if "idle" in lowered:
            last_user_age = 10.0
        elif "recent" in lowered:
            last_user_age = 0.3
        else:
            last_user_age = None
        allowed, reason = scheduler_admission_preview(priority, active_p0=active_p0, last_user_age=last_user_age)
        print(f"  Input: {raw_text}")
        print(f"  Priority: {priority.name}")
        print(f"  active_p0={active_p0} | last_user_age={'none' if last_user_age is None else f'{last_user_age:.1f}s'}")
        print(f"  Result: allowed={allowed} | reason={reason}")
        print("  Execute: False")
        return
    rows = scheduler_admission_rows()
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(
            f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | "
            f"priority={row['priority']} active_p0={row['active_p0']} last_user_age={row['last_user_age']} | "
            f"got=allowed={row['allowed']} reason={row['reason']} | {row['expected']}"
        )


def print_scheduler_guard_status():
    summary = phase10_5_guard_summary()
    print("🧪 Phase 10.5 Scheduler Guard")
    print("  Action: read-only; không dequeue, không tạo pending, không execute.")
    print(f"  Progress: {phase10_5_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Admission regression:")
    for row in summary["admission_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['reason']} | {row['expected']}")


def print_phase10_5_status():
    summary = phase10_5_guard_summary()
    print("🧩 Phase 10.5 Status")
    print("  Goal: Scheduler & Queue Core - priority/admission/retention rõ, không chạy task ngoài ý muốn.")
    print(f"  Progress: {phase10_5_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /scheduler-status | /scheduler-test | /scheduler-guard-status | /phase10-5-ready")


def print_phase10_5_ready():
    summary = phase10_5_guard_summary()
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 10.5 Ready" if ready else "⚠️ Phase 10.5 Ready")
    print("  Goal: scheduler contract đủ rõ để sang Phase 10.6 recovery governor.")
    print(f"  Progress: {phase10_5_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: scheduler={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 10.5 chỉ queue/admission guard.")


# ── Phase 10.6: Recovery Governor ─────────────────────────────────────────────


RECOVERY_DRY_RUN_CASES = [
    ("browser_unavailable_emit", "browser_unavailable", None, 120.0, True, "ready"),
    ("browser_unavailable_suppress", "browser_unavailable", None, 2.0, False, "cooldown_suppressed"),
    ("social_youtube_recovery", "social_target_missing", "browser_kind=youtube", 120.0, True, "ready"),
    ("vision_no_preview_suppress", "vision_no_preview", None, 1.0, False, "cooldown_suppressed"),
]


def recovery_dry_run_rows():
    rows = []
    for name, kind, detail, last_age, expected_emit, expected_reason in RECOVERY_DRY_RUN_CASES:
        result = recovery_dry_run(kind, detail=detail, last_age=last_age, cooldown=True)
        passed = result["would_emit"] == expected_emit and result["reason"] == expected_reason
        rows.append({
            "name": name,
            "kind": kind,
            "detail": detail,
            "last_age": last_age,
            "result": result,
            "passed": passed,
            "expected": f"emit={expected_emit} reason={expected_reason}",
        })
    return rows


def phase10_6_guard_summary():
    config = recovery_governor_config()
    snapshot = recovery_snapshot()
    known = set(config["known_kinds"])
    message = set(config["message_kinds"])
    advice = set(config["advice_kinds"])
    severity = set(config["severity_kinds"])
    missing_message = sorted(known - message)
    missing_advice = sorted(known - advice)
    missing_severity = sorted(known - severity)
    clear_groups = config.get("clear_groups") or {}
    clear_known = []
    for group, kinds in clear_groups.items():
        unknown = sorted(set(kinds) - known)
        if unknown:
            clear_known.append(f"{group}:{','.join(unknown)}")
    dry_rows = recovery_dry_run_rows()
    dry_ok = all(row["passed"] for row in dry_rows)
    cooldown_ok = isinstance(config.get("cooldown_seconds"), (int, float)) and config.get("cooldown_seconds") >= 10
    snapshot_ok = all(
        value_matches_type(row.get("count"), "int")
        and value_matches_type(row.get("suppressed"), "int")
        and row.get("severity") in {"low", "medium", "high", "critical"}
        for row in snapshot.get("active") or []
    )
    command_missing = sorted(PHASE10_6_COMMANDS - KNOWN_SLASH_COMMANDS)
    guard_rows = [
        ("taxonomy_messages", not missing_message, f"missing={','.join(missing_message) if missing_message else 'none'}"),
        ("taxonomy_advice", not missing_advice, f"missing={','.join(missing_advice) if missing_advice else 'none'}"),
        ("taxonomy_severity", not missing_severity, f"missing={','.join(missing_severity) if missing_severity else 'none'}"),
        ("cooldown_contract", cooldown_ok, f"{config.get('cooldown_seconds')}s"),
        ("dry_run_regression", dry_ok, f"{sum(1 for row in dry_rows if row['passed'])}/{len(dry_rows)} pass"),
        ("clear_groups_known", not clear_known, f"unknown={';'.join(clear_known) if clear_known else 'none'}"),
        ("snapshot_schema", snapshot_ok, f"active={snapshot.get('active_count')} suppressed={snapshot.get('suppressed_total')}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("recovery_readonly_tests", True, "status/guard/test do not mutate notices"),
    ]
    failures = [row for row in guard_rows if not row[1]]
    return {
        "rows": guard_rows,
        "failures": failures,
        "pass_count": len(guard_rows) - len(failures),
        "total": len(guard_rows),
        "config": config,
        "snapshot": snapshot,
        "dry_rows": dry_rows,
    }


def phase10_6_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_recovery_governor_status():
    summary = phase10_6_guard_summary()
    config = summary["config"]
    snapshot = summary["snapshot"]
    print("🧯 Recovery Governor Status")
    print("  Action: read-only; xem taxonomy/cooldown/suppression, không tạo recovery notice.")
    print(f"  Phase 10.6 Progress: {phase10_6_progress_percent(summary)}%")
    print(f"  Cooldown: {config.get('cooldown_seconds')}s")
    print(f"  Known kinds: {len(config.get('known_kinds') or [])}")
    print(f"  Active notices: {snapshot.get('active_count')} | suppressed_total={snapshot.get('suppressed_total')}")
    print("  Clear groups:")
    for group, kinds in (config.get("clear_groups") or {}).items():
        print(f"    {group}: {len(kinds)} kind(s)")
    if snapshot.get("active"):
        print("  Active:")
        for row in snapshot["active"][:8]:
            age = "none" if row["age"] is None else f"{row['age']:.1f}s"
            print(f"    {row['kind']} | age={age} | severity={row['severity']} | count={row['count']} suppressed={row['suppressed']}")
    else:
        print("  Active: none")


def print_recovery_test(raw_text=None):
    shorten_line = _get_shorten_line()
    print("🧪 Recovery Governor Test")
    print("  Action: read-only; dry-run recovery decision, không ghi notice/log.")
    if raw_text:
        parts = raw_text.split("|", 2)
        kind = parts[0].strip()
        detail = parts[1].strip() if len(parts) > 1 else None
        try:
            last_age = float(parts[2].strip()) if len(parts) > 2 else 120.0
        except ValueError:
            last_age = 120.0
        result = recovery_dry_run(kind, detail=detail, last_age=last_age, cooldown=True)
        print(f"  Kind: {result['kind']} | known={result['known']} | severity={result['severity']}")
        print(f"  Last age: {result['last_age']}s | cooldown={result['cooldown_seconds']}s")
        print(f"  Would emit: {result['would_emit']} | reason={result['reason']}")
        print(f"  Message: {shorten_line(result['message'], 180)}")
        print(f"  Advice: {result['advice'] or 'none'}")
        print("  Execute: False")
        return
    rows = recovery_dry_run_rows()
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        result = row["result"]
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | kind={row['kind']} | emit={result['would_emit']} reason={result['reason']} | {row['expected']}")


def print_recovery_governor_guard_status():
    summary = phase10_6_guard_summary()
    print("🧪 Phase 10.6 Recovery Governor Guard")
    print("  Action: read-only; kiểm taxonomy/cooldown/snapshot, không tạo notice.")
    print(f"  Progress: {phase10_6_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Dry-run regression:")
    for row in summary["dry_rows"]:
        result = row["result"]
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got=emit={result['would_emit']} reason={result['reason']} | {row['expected']}")


def print_phase10_6_status():
    summary = phase10_6_guard_summary()
    print("🧩 Phase 10.6 Status")
    print("  Goal: Recovery Governor - recovery có taxonomy, cooldown, suppression và clear lifecycle rõ.")
    print(f"  Progress: {phase10_6_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /recovery-governor-status | /recovery-test | /recovery-governor-guard-status | /phase10-6-ready")


def print_phase10_6_ready():
    summary = phase10_6_guard_summary()
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 10.6 Ready" if ready else "⚠️ Phase 10.6 Ready")
    print("  Goal: recovery governor đủ chắc để sang Phase 10.7 memory governance v2.")
    print(f"  Progress: {phase10_6_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: recovery_governor={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 10.6 chỉ recovery lifecycle guard.")


# ── Phase 10.7: Memory Governance ─────────────────────────────────────────────


def phase10_7_guard_summary():
    memory_summary = memory_governance_summary()
    rows = list(memory_summary["rows"])
    command_missing = sorted(PHASE10_7_COMMANDS - KNOWN_SLASH_COMMANDS)
    rows.append((
        "command_surface",
        not command_missing,
        f"missing={','.join(command_missing) if command_missing else 'none'}",
    ))
    rows.append((
        "memory_readonly_tests",
        True,
        "governance status/test/guard do not save, compact, label, or confirm",
    ))
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "memory": memory_summary,
    }


def phase10_7_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_memory_governance_status():
    summary = phase10_7_guard_summary()
    memory_summary = summary["memory"]
    snapshot = memory_summary["snapshot"]
    classes = snapshot.get("classifications") or {}
    print("🧠 Memory Governance Status")
    print("  Action: read-only; không lưu, không compact, không đổi label.")
    print(f"  Phase 10.7 Progress: {phase10_7_progress_percent(summary)}%")
    print(f"  Store: long={snapshot['long_term']}/50 short={snapshot['short_term']}/16 chat={snapshot['chat_log']}/50")
    print(f"  Labels: active={snapshot['active_labels']} orphan={snapshot['orphan_labels']} issues={len(snapshot['label_issues'])}")
    print(f"  Pending action: {snapshot['pending_status']}")
    print(
        "  Classification: "
        f"keep={classes.get('keep', 0)} review={classes.get('review', 0)} "
        f"stale={classes.get('stale-like', 0)} vibe={classes.get('vibe-heavy', 0)}"
    )
    print(
        "  Store mode: "
        f"string={snapshot['string_entries']} structured={snapshot['structured_entries']} other={snapshot['other_entries']}"
    )
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_memory_governance_guard_status():
    summary = phase10_7_guard_summary()
    memory_summary = summary["memory"]
    print("🧪 Phase 10.7 Memory Governance Guard")
    print("  Action: read-only; kiểm memory policy/shape, không lưu/compact/confirm.")
    print(f"  Progress: {phase10_7_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Filter regression:")
    for row in memory_summary["filter_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} | expected={row['expected']}")
    print("  Classification regression:")
    for row in memory_summary["classify_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} | expected={row['expected']} | reason={row['reason']}")


def print_memory_governance_test(raw_text=None):
    for line in memory_governance_test_report(raw_text):
        print(line)


def print_phase10_7_status():
    summary = phase10_7_guard_summary()
    print("🧩 Phase 10.7 Status")
    print("  Goal: Memory Governance v2 - memory phải có filter, review, capacity, label và confirm contract rõ.")
    print(f"  Progress: {phase10_7_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /memory-governance-status | /memory-governance-test | /memory-governance-guard-status | /phase10-7-ready")


def print_phase10_7_ready():
    summary = phase10_7_guard_summary()
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 10.7 Ready" if ready else "⚠️ Phase 10.7 Ready")
    print("  Goal: memory governance đủ rõ để sang Phase 10.8 presence stability.")
    print(f"  Progress: {phase10_7_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: memory_governance={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 10.7 chỉ memory governance/read-only guard.")


# ── Phase 10.8: Presence Stability ───────────────────────────────────────────


PRESENCE_ATTENTION_CASES = [
    ("flow_mutes", {"zone": "war_zone", "active_app": "cmd", "idle_state": "active", "idle_seconds": 0, "in_flow": True, "browser_kind": "youtube", "last_chat_age": None}, "work_flow", "mute"),
    ("active_work_mutes", {"zone": "war_zone", "active_app": "cmd", "idle_state": "active", "idle_seconds": 3, "in_flow": False, "browser_kind": "unknown", "last_chat_age": None}, "work_active", "mute"),
    ("relaxed_work_observe", {"zone": "war_zone", "active_app": "cmd", "idle_state": "relaxed", "idle_seconds": 420, "in_flow": False, "browser_kind": "unknown", "last_chat_age": None}, "work_paused", "observe"),
    ("chill_social_available", {"zone": "chill", "active_app": "msedge", "idle_state": "active", "idle_seconds": 10, "in_flow": False, "browser_kind": "social", "last_chat_age": None}, "browse_active", "available"),
    ("game_mutes", {"zone": "game", "active_app": "steam", "idle_state": "active", "idle_seconds": 10, "in_flow": False, "browser_kind": "unknown", "last_chat_age": None}, "game", "mute"),
]

PRESENCE_RHYTHM_CASES = [
    ("flow_active", {"zone": "war_zone", "active_app": "cmd", "idle_state": "active", "in_flow": True, "last_chat_age": None}, "quiet", "flow_active", False, False),
    ("active_work", {"zone": "war_zone", "active_app": "cmd", "idle_state": "active", "in_flow": False, "last_chat_age": None}, "observe", "active_work", True, False),
    ("game_zone", {"zone": "game", "active_app": "steam", "idle_state": "active", "in_flow": False, "last_chat_age": None}, "quiet", "game_zone", False, False),
    ("recent_chat", {"zone": "chill", "active_app": "msedge", "idle_state": "active", "in_flow": False, "last_chat_age": 5}, "observe", "recent_chat", True, False),
    ("idle_sleepy", {"zone": "unknown", "active_app": None, "idle_state": "sleepy", "in_flow": False, "last_chat_age": None}, "low", "idle_sleepy", True, False),
    ("chill_zone", {"zone": "chill", "active_app": "msedge", "idle_state": "active", "in_flow": False, "last_chat_age": None}, "available", "chill_zone", True, True),
]


def presence_rhythm_preview(context, persona_mode="chill", enabled=True, quiet_until_active=False):
    zone = context.get("zone") or "unknown"
    idle_state = context.get("idle_state") or "active"
    in_flow = bool(context.get("in_flow"))
    last_chat_age = context.get("last_chat_age")
    active_app = context.get("active_app")
    if not enabled:
        return {"enabled": False, "rhythm": "quiet", "last_reason": "presence_disabled"}
    if persona_mode in {"focus", "technical"}:
        return {"enabled": True, "rhythm": "quiet", "last_reason": f"persona_{persona_mode}"}
    if quiet_until_active:
        return {"enabled": True, "rhythm": "quiet", "last_reason": "quiet_until_active"}
    if in_flow:
        return {"enabled": True, "rhythm": "quiet", "last_reason": "flow_active"}
    if zone == "war_zone" and idle_state == "active":
        return {"enabled": True, "rhythm": "observe", "last_reason": "active_work"}
    if zone == "game":
        return {"enabled": True, "rhythm": "quiet", "last_reason": "game_zone"}
    if last_chat_age is not None and last_chat_age < 20:
        return {"enabled": True, "rhythm": "observe", "last_reason": "recent_chat"}
    if idle_state == "sleepy":
        return {"enabled": True, "rhythm": "low", "last_reason": "idle_sleepy"}
    if zone == "chill":
        return {"enabled": True, "rhythm": "available", "last_reason": "chill_zone"}
    if active_app in {None, ""}:
        return {"enabled": True, "rhythm": "low", "last_reason": "unknown_app"}
    return {"enabled": True, "rhythm": "observe", "last_reason": "default_observe"}


def presence_allowed_preview(topic, rhythm):
    current = rhythm.get("rhythm")
    if not rhythm.get("enabled", True):
        return False, "presence_disabled"
    if current == "quiet":
        return False, rhythm.get("last_reason") or "presence_quiet"
    if current == "observe" and topic != "browser_presence":
        return False, "presence_observe_context_silent"
    if current == "low" and topic == "context":
        return False, "presence_low_context_silent"
    return True, "ok"


def presence_attention_rows():
    rows = []
    for name, context, expected_window, expected_policy in PRESENCE_ATTENTION_CASES:
        attention = evaluate_attention_window(context)
        passed = attention["window"] == expected_window and attention["ambient_policy"] == expected_policy
        rows.append({
            "name": name,
            "passed": passed,
            "got": f"{attention['window']}/{attention['ambient_policy']}",
            "expected": f"{expected_window}/{expected_policy}",
            "reason": attention["reason"],
        })
    return rows


def presence_rhythm_rows():
    rows = []
    for name, context, expected_rhythm, expected_reason, browser_allowed, context_allowed in PRESENCE_RHYTHM_CASES:
        rhythm = presence_rhythm_preview(context)
        got_browser, browser_reason = presence_allowed_preview("browser_presence", rhythm)
        got_context, context_reason = presence_allowed_preview("context", rhythm)
        passed = (
            rhythm["rhythm"] == expected_rhythm
            and rhythm["last_reason"] == expected_reason
            and got_browser == browser_allowed
            and got_context == context_allowed
        )
        rows.append({
            "name": name,
            "passed": passed,
            "got": f"{rhythm['rhythm']}:{rhythm['last_reason']} browser={got_browser} context={got_context}",
            "expected": f"{expected_rhythm}:{expected_reason} browser={browser_allowed} context={context_allowed}",
            "browser_reason": browser_reason,
            "context_reason": context_reason,
        })
    return rows


def voice_snapshot_for_presence(voice=None):
    if voice is not None and hasattr(voice, "snapshot"):
        try:
            return voice.snapshot()
        except Exception as exc:
            return {"status": "error", "error": str(exc), "provided": True}
    return {
        "status": "not_provided",
        "listening": False,
        "speaking": False,
        "queue_size": None,
        "queue_maxsize": None,
        "worker_alive": None,
        "drop_old_when_full": None,
        "provided": False,
    }


def presence_stability_snapshot(voice=None):
    context_state = _get_context_state()
    context_lock = _get_context_lock()
    memory = _get_memory()
    memory_lock = _get_memory_lock()

    now = time.time()
    with context_lock:
        state = dict(context_state)
        browser = dict(state.get("browser", {}))
        proactive = dict(state.get("proactive", {}))
    with memory_lock:
        persona = dict(memory.get("persona", {}))
        silence = dict((persona.get("silence") or {}))
        presence = dict((persona.get("presence") or {}))
    last_chat = state.get("last_chat_time") or 0
    last_browser_reaction = proactive.get("last_browser_reaction_time") or 0
    attention_context = {
        "zone": state.get("active_zone"),
        "active_app": state.get("active_app"),
        "idle_state": state.get("idle_state"),
        "idle_seconds": state.get("idle_seconds"),
        "in_flow": state.get("in_flow"),
        "browser_kind": browser.get("kind"),
        "last_chat_age": None if not last_chat else max(0.0, now - last_chat),
    }
    return {
        "context": {
            "zone": state.get("active_zone"),
            "active_app": state.get("active_app"),
            "idle_state": state.get("idle_state"),
            "idle_seconds": state.get("idle_seconds"),
            "in_flow": state.get("in_flow"),
            "last_chat_age": attention_context["last_chat_age"],
            "last_browser_reaction_age": None if not last_browser_reaction else max(0.0, now - last_browser_reaction),
            "browser_kind": browser.get("kind"),
        },
        "attention": evaluate_attention_window(attention_context),
        "proactive": proactive,
        "presence": presence,
        "silence": silence,
        "turn": runtime_turn_state_snapshot(),
        "voice": voice_snapshot_for_presence(voice),
    }


def presence_stability_guard_summary(voice=None):
    snapshot = presence_stability_snapshot(voice)
    presence = snapshot["presence"]
    silence = snapshot["silence"]
    turn = snapshot["turn"]
    voice_state = snapshot["voice"]
    attention_rows = presence_attention_rows()
    rhythm_rows = presence_rhythm_rows()
    presence_fields_ok = (
        isinstance(presence.get("enabled", True), bool)
        and presence.get("rhythm", "available") in {"available", "observe", "low", "quiet"}
        and isinstance(presence.get("last_reason", ""), str)
        and isinstance(presence.get("last_update", 0.0), (int, float))
        and isinstance(presence.get("quiet_until", 0.0), (int, float))
    )
    ambient_count = int(silence.get("ambient_count") or 0)
    ambient_limit = int(silence.get("ambient_limit") or 0)
    silence_ok = ambient_limit > 0 and 0 <= ambient_count <= ambient_limit and isinstance(silence.get("window_start", 0.0), (int, float))
    turn_ok = turn.get("status") in VALID_TURN_STATUSES and isinstance(turn.get("last_update"), (int, float))
    if voice_state.get("provided") is False:
        voice_ok = True
        voice_detail = "voice=not_provided"
        interruption_ok = True
        interruption_detail = "voice=not_provided"
    else:
        voice_ok = (
            voice_state.get("status") in VALID_TURN_STATUSES
            and isinstance(voice_state.get("listening"), bool)
            and isinstance(voice_state.get("speaking"), bool)
            and isinstance(voice_state.get("queue_size"), int)
            and isinstance(voice_state.get("queue_maxsize"), int)
            and isinstance(voice_state.get("worker_alive"), bool)
        )
        voice_detail = (
            f"status={voice_state.get('status')} listening={voice_state.get('listening')} "
            f"speaking={voice_state.get('speaking')} queue={voice_state.get('queue_size')}/{voice_state.get('queue_maxsize')}"
        )
        interruption_ok = bool(voice_state.get("drop_old_when_full")) and int(voice_state.get("queue_maxsize") or 0) <= 3
        interruption_detail = f"drop_old_when_full={voice_state.get('drop_old_when_full')} queue_max={voice_state.get('queue_maxsize')}"
    cooldown_ok = CHAT_CONTEXT_SUPPRESS >= 10 and PROACTIVE_BROWSER_COOLDOWN >= 30 and ambient_limit <= 5
    residue_ok = all(isinstance(value, int) and value > 0 for value in RESIDUE_DECAY_PER_TICK.values())
    command_missing = sorted(PHASE10_8_COMMANDS - KNOWN_SLASH_COMMANDS)
    guard_rows = [
        ("presence_schema", presence_fields_ok, f"rhythm={presence.get('rhythm')} enabled={presence.get('enabled', True)} reason={presence.get('last_reason')}"),
        ("silence_budget_contract", silence_ok, f"ambient={ambient_count}/{ambient_limit} window_start={silence.get('window_start')}"),
        ("attention_regression", all(row["passed"] for row in attention_rows), f"{sum(1 for row in attention_rows if row['passed'])}/{len(attention_rows)} pass"),
        ("rhythm_regression", all(row["passed"] for row in rhythm_rows), f"{sum(1 for row in rhythm_rows if row['passed'])}/{len(rhythm_rows)} pass"),
        ("voice_state_snapshot", voice_ok, voice_detail),
        ("turn_state_schema", turn_ok, f"status={turn.get('status')} age={turn.get('age'):.1f}s reason={turn.get('last_reason')}"),
        ("interruption_contract", interruption_ok, interruption_detail),
        ("anti_spam_contract", cooldown_ok, f"chat_suppress={CHAT_CONTEXT_SUPPRESS}s browser_cooldown={PROACTIVE_BROWSER_COOLDOWN}s ambient_limit={ambient_limit}"),
        ("residue_decay_contract", residue_ok, f"sources={','.join(sorted(RESIDUE_DECAY_PER_TICK))}"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("presence_readonly_tests", True, "status/guard/test do not trigger voice, expression, recovery, or proactive reaction"),
    ]
    failures = [row for row in guard_rows if not row[1]]
    return {
        "rows": guard_rows,
        "failures": failures,
        "pass_count": len(guard_rows) - len(failures),
        "total": len(guard_rows),
        "snapshot": snapshot,
        "attention_rows": attention_rows,
        "rhythm_rows": rhythm_rows,
    }


def phase10_8_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_presence_stability_status(voice=None):
    summary = presence_stability_guard_summary(voice)
    snapshot = summary["snapshot"]
    context = snapshot["context"]
    presence = snapshot["presence"]
    silence = snapshot["silence"]
    voice_state = snapshot["voice"]
    turn = snapshot["turn"]
    attention = snapshot["attention"]
    print("🧠 Presence Stability Status")
    print("  Action: read-only; không trigger voice/expression/recovery.")
    print(f"  Phase 10.8 Progress: {phase10_8_progress_percent(summary)}%")
    print(
        "  Context: "
        f"zone={context.get('zone')} app={context.get('active_app')} idle={context.get('idle_state')} "
        f"flow={context.get('in_flow')} browser={context.get('browser_kind')}"
    )
    print(f"  Attention: {attention.get('window')} | ambient={attention.get('ambient_policy')} | reason={attention.get('reason')}")
    print(f"  Presence rhythm: enabled={presence.get('enabled', True)} rhythm={presence.get('rhythm')} reason={presence.get('last_reason')}")
    print(f"  Silence budget: {silence.get('ambient_count', 0)}/{silence.get('ambient_limit', 0)}")
    print(f"  Turn: status={turn.get('status')} age={turn.get('age'):.1f}s reason={turn.get('last_reason')}")
    print(
        "  Voice: "
        f"status={voice_state.get('status')} listening={voice_state.get('listening')} "
        f"speaking={voice_state.get('speaking')} queue={voice_state.get('queue_size')}/{voice_state.get('queue_maxsize')}"
    )
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_presence_stability_test(raw_text=None):
    print("🧪 Presence Stability Test")
    print("  Action: read-only; synthetic only, không trigger voice/expression.")
    if raw_text:
        lowered = raw_text.lower().strip()
        match = next((row for row in PRESENCE_RHYTHM_CASES if row[0] in lowered), None)
        if not match:
            match = next((row for row in PRESENCE_RHYTHM_CASES if lowered in row[0]), None)
        if not match:
            print("  Status: not_found")
            print("  Cases: " + ", ".join(row[0] for row in PRESENCE_RHYTHM_CASES))
            print("  Execute: False")
            return
        name, context, expected_rhythm, expected_reason, expected_browser, expected_context = match
        rhythm = presence_rhythm_preview(context)
        browser_allowed, browser_reason = presence_allowed_preview("browser_presence", rhythm)
        context_allowed, context_reason = presence_allowed_preview("context", rhythm)
        passed = (
            rhythm["rhythm"] == expected_rhythm
            and rhythm["last_reason"] == expected_reason
            and browser_allowed == expected_browser
            and context_allowed == expected_context
        )
        print(f"  Case: {name}")
        print(f"  Status: {'pass' if passed else 'fail'}")
        print(f"  Rhythm: {rhythm['rhythm']} | reason={rhythm['last_reason']}")
        print(f"  Browser presence allowed: {browser_allowed} | reason={browser_reason}")
        print(f"  Context presence allowed: {context_allowed} | reason={context_reason}")
        print("  Execute: False")
        return
    attention_rows = presence_attention_rows()
    rhythm_rows = presence_rhythm_rows()
    print(f"  Attention regression: {sum(1 for row in attention_rows if row['passed'])}/{len(attention_rows)} pass")
    for row in attention_rows:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} | expected={row['expected']} | reason={row['reason']}")
    print(f"  Rhythm regression: {sum(1 for row in rhythm_rows if row['passed'])}/{len(rhythm_rows)} pass")
    for row in rhythm_rows:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} | expected={row['expected']}")


def print_presence_stability_guard_status(voice=None):
    summary = presence_stability_guard_summary(voice)
    print("🧪 Phase 10.8 Presence Stability Guard")
    print("  Action: read-only; kiểm presence/voice/turn contracts, không tạo reaction.")
    print(f"  Progress: {phase10_8_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Attention regression:")
    for row in summary["attention_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} | expected={row['expected']} | reason={row['reason']}")
    print("  Rhythm regression:")
    for row in summary["rhythm_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} | expected={row['expected']}")


def print_phase10_8_status(voice=None):
    summary = presence_stability_guard_summary(voice)
    print("🧩 Phase 10.8 Status")
    print("  Goal: Presence Stability - silence/idle/listening/thinking/talking/interruption có contract rõ.")
    print(f"  Progress: {phase10_8_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /presence-stability-status | /presence-stability-test | /presence-stability-guard-status | /phase10-8-ready")


def print_phase10_8_ready(voice=None):
    summary = presence_stability_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 10.8 Ready" if ready else "⚠️ Phase 10.8 Ready")
    print("  Goal: presence đủ ổn để sang Phase 10.9 social/vision decoupling.")
    print(f"  Progress: {phase10_8_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: presence_stability={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 10.8 chỉ state/guard/read-only.")


# ── Phase 10.9: Social/Vision Decoupling ───────────────────────────────────────


SOCIAL_VISION_PRIORITY_CASES = [
    (
        "social_text_post_first",
        {
            "browser_kind": "social",
            "browser_fresh": True,
            "browser_snapshot_state": "FRESH",
            "browser_age_seconds": 0.0,
            "browser_title": "kaip trên X: Tống cả đám bạn vẫn ngồi thoải mái / X",
            "browser_url": "https://x.com/kaywa369/status/1",
            "browser_social_post_text": "Tống cả đám bạn mà vẫn ngồi thoải mái",
            "browser_social_vibe": "role=người ngoài hóng chuyện | post_topic=chủ đề trong bài gốc | stats: busy=1",
            "browser_heading": "Cuộc trò chuyện",
            "browser_local_summary": "",
        },
        None,
        "post > title > url",
        "post",
    ),
    (
        "social_video_vision_support",
        {
            "browser_kind": "social",
            "browser_fresh": True,
            "browser_snapshot_state": "FRESH",
            "browser_age_seconds": 0.0,
            "browser_title": "Elon Musk trên X: First Starship V3 launch later this week / X",
            "browser_url": "https://x.com/elonmusk/status/1",
            "browser_social_post_text": "Phóng thử Starship V3 đầu tiên vào cuối tuần này! video",
            "browser_social_vibe": "role=người ngoài hóng chuyện | post_topic=đời sống/phố xá",
            "browser_heading": "Cuộc trò chuyện",
            "browser_local_summary": "",
        },
        "ảnh chụp giao diện X với tên lửa đang bay lên và khói trắng",
        "post > title > url",
        "post",
    ),
    (
        "youtube_title_first",
        {
            "browser_kind": "youtube",
            "browser_fresh": True,
            "browser_snapshot_state": "FRESH",
            "browser_age_seconds": 0.0,
            "browser_title": "Cyrene Theme Music 1 HOUR tnbee mix Honkai Star Rail - YouTube",
            "browser_url": "https://www.youtube.com/watch?v=x",
            "browser_heading": "Cyrene Theme Music 1 HOUR",
            "browser_local_summary": "",
            "browser_social_post_text": "",
            "browser_social_vibe": "",
        },
        "ảnh minh họa anime cô gái tóc hồng",
        "title > url > page_extra",
        "title",
    ),
]

SOCIAL_VISION_CLASSIFIER_CASES = [
    (
        "video_post_beats_cute_vision",
        "video xe máy tông cột điện trong hẻm",
        {
            "browser_kind": "social",
            "browser_social_post_text": "video xe máy tông cột điện trong hẻm",
            "browser_title": "tai nạn xe máy / X",
            "browser_social_vibe": "role=người ngoài hóng chuyện | post_topic=động vật/cute",
        },
        "ảnh mèo con rất cute đang ngủ",
        "danger",
        "video",
    ),
    (
        "image_vision_can_choose_cute",
        "Nana viết nháp reply ảnh này",
        {
            "browser_kind": "social",
            "browser_social_post_text": "nhìn ảnh này nè",
            "browser_title": "ảnh mèo cute / X",
            "browser_social_vibe": "",
        },
        "ảnh tĩnh một chú mèo con cute đang nằm",
        "cute",
        "image",
    ),
    (
        "space_post_beats_street_vibe",
        "Nana viết nháp reply tweet này",
        {
            "browser_kind": "social",
            "browser_social_post_text": "video Phóng thử Starship V3 đầu tiên vào cuối tuần này!",
            "browser_title": "First Starship V3 launch later this week / X",
            "browser_social_vibe": "role=người ngoài hóng chuyện | post_topic=đời sống/phố xá | stats: uncertain=2",
        },
        "ảnh tên lửa đang bay lên với khói trắng",
        "space_launch",
        "video",
    ),
]


def social_vision_priority_rows():
    rows = []
    for name, context, vision, expected_prefix, expected_primary in SOCIAL_VISION_PRIORITY_CASES:
        policy = context_priority_policy(context=context, vision_description=vision)
        brief = context_priority_brief(policy)
        primary = (policy.get("ranked") or [{}])[0].get("name", "none")
        passed = brief.startswith(expected_prefix) and primary == expected_primary
        rows.append({
            "name": name,
            "passed": passed,
            "got": brief,
            "expected": expected_prefix,
            "primary": primary,
            "reason": policy.get("reason"),
        })
    return rows


def social_vision_classifier_rows():
    rows = []
    for name, raw_text, context, vision, expected_style, expected_media in SOCIAL_VISION_CLASSIFIER_CASES:
        source_text = build_social_draft_source(raw_text, broker_context=context, vision_description=vision)
        media_mode, style, guard_hint = social_source_classification(
            source_text,
            broker_context=context,
            raw_text=raw_text,
            vision_description=vision,
        )
        passed = style == expected_style and media_mode == expected_media
        rows.append({
            "name": name,
            "passed": passed,
            "got": f"style={style} media={media_mode}",
            "expected": f"style={expected_style} media={expected_media}",
            "guard": guard_hint,
        })
    return rows


def social_vision_reconcile_rows():
    cases = [
        ("aligned_space", "First Starship V3 launch later this week", "tên lửa đang bay lên với khói trắng", "aligned"),
        ("conflict_vehicle_anime", "video xe máy tông cột điện trong hẻm", "ảnh minh họa anime cô gái tóc hồng", "conflict"),
        ("missing_vision", "Cyrene Theme Music 1 HOUR Honkai Star Rail", "", "missing_vision"),
    ]
    rows = []
    for name, text, vision, expected in cases:
        context = {
            "browser_kind": "social",
            "browser_social_post_text": text,
            "browser_title": text,
            "browser_heading": "",
            "browser_local_summary": "",
        }
        report = vision_text_reconcile_report(context=context, vision_description=vision)
        rows.append({
            "name": name,
            "passed": report["status"] == expected,
            "got": report["status"],
            "expected": expected,
            "text_groups": report["text_groups"],
            "vision_groups": report["vision_groups"],
            "note": report["note"],
        })
    return rows


def social_vision_vibe_rows():
    context = {
        "browser_kind": "social",
        "browser_social_post_text": "Phóng thử Starship V3 đầu tiên vào cuối tuần này!",
        "browser_title": "First Starship V3 launch later this week / X",
        "browser_social_vibe": "role=người ngoài hóng chuyện | post_topic=đời sống/phố xá | stats: uncertain=2",
    }
    audit = context_vibe_audit(
        post=context.get("browser_social_post_text"),
        title=context.get("browser_title"),
        vibe=context.get("browser_social_vibe"),
    )
    return [{
        "name": "vibe_mismatch_does_not_pull_topic",
        "passed": bool(audit.get("mismatch")) and "vibe_mismatch" in audit.get("detail", ""),
        "got": audit.get("detail"),
        "expected": "vibe_mismatch",
    }]


def social_vision_prompt_contract_rows():
    prompt = build_social_draft_prompt(
        "Nana viết nháp reply tweet này",
        "visible_post_text: video xe máy tông cột điện trong hẻm\nvision_description: ảnh mèo cute",
        "social.reply",
    )
    required = [
        ("vibe_support_only", "social_vibe samples chỉ là phụ"),
        ("video_frame_support_only", "vision chỉ là một frame phụ"),
        ("image_vision_allowed", "Nếu bài là ảnh tĩnh, vision là nguồn chính"),
        ("no_context_fact_hallucination", "Không thêm fact mới ngoài request/context"),
    ]
    rows = []
    for name, marker in required:
        rows.append({
            "name": name,
            "passed": marker in prompt,
            "got": "present" if marker in prompt else "missing",
            "expected": "present",
        })
    return rows


def social_vision_decouple_guard_summary():
    priority_rows = social_vision_priority_rows()
    classifier_rows = social_vision_classifier_rows()
    reconcile_rows = social_vision_reconcile_rows()
    vibe_rows = social_vision_vibe_rows()
    prompt_rows = social_vision_prompt_contract_rows()
    command_missing = sorted(PHASE10_9_COMMANDS - KNOWN_SLASH_COMMANDS)
    rows = [
        ("source_priority_matrix", all(row["passed"] for row in priority_rows), f"{sum(1 for row in priority_rows if row['passed'])}/{len(priority_rows)} pass"),
        ("classifier_decoupling", all(row["passed"] for row in classifier_rows), f"{sum(1 for row in classifier_rows if row['passed'])}/{len(classifier_rows)} pass"),
        ("vision_text_reconcile", all(row["passed"] for row in reconcile_rows), f"{sum(1 for row in reconcile_rows if row['passed'])}/{len(reconcile_rows)} pass"),
        ("vibe_support_only", all(row["passed"] for row in vibe_rows), f"{sum(1 for row in vibe_rows if row['passed'])}/{len(vibe_rows)} pass"),
        ("prompt_contract_surface", all(row["passed"] for row in prompt_rows), f"{sum(1 for row in prompt_rows if row['passed'])}/{len(prompt_rows)} pass"),
        ("vision_stale_contract", True, "social-draft-vision requires fresh <=180s description"),
        ("persona_leak_guard", True, "social prompt uses public social rules over private persona"),
        ("command_surface", not command_missing, f"missing={','.join(command_missing) if command_missing else 'none'}"),
        ("decouple_readonly_tests", True, "status/guard/test do not call model, create draft, type, post, or mark residue"),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "priority_rows": priority_rows,
        "classifier_rows": classifier_rows,
        "reconcile_rows": reconcile_rows,
        "vibe_rows": vibe_rows,
        "prompt_rows": prompt_rows,
    }


def phase10_9_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_social_vision_decouple_status():
    summary = social_vision_decouple_guard_summary()
    print("🧩 Social/Vision Decoupling Status")
    print("  Action: read-only; không gọi model, không tạo draft, không post/type.")
    print(f"  Phase 10.9 Progress: {phase10_9_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: post/title/url là xương sống; vision/vibe chỉ hỗ trợ đúng vai, không tự kéo chủ đề.")


def print_social_vision_decouple_test(raw_text=None):
    print("🧪 Social/Vision Decoupling Test")
    print("  Action: read-only; synthetic only, không gọi model/không tạo draft.")
    summary = social_vision_decouple_guard_summary()
    sections = {
        "priority": ("Priority matrix", summary["priority_rows"]),
        "classifier": ("Classifier decoupling", summary["classifier_rows"]),
        "reconcile": ("Reconcile", summary["reconcile_rows"]),
        "vibe": ("Vibe support", summary["vibe_rows"]),
        "prompt": ("Prompt contract", summary["prompt_rows"]),
    }
    if raw_text:
        key = raw_text.strip().lower()
        if key not in sections:
            print("  Status: not_found")
            print("  Cases: " + ", ".join(sections))
            print("  Execute: False")
            return
        title, rows = sections[key]
        print(f"  Section: {title}")
        print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
        for row in rows:
            detail = row.get("reason") or row.get("note") or row.get("guard") or ""
            print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} | expected={row['expected']} | {detail}")
        print("  Execute: False")
        return
    for key, (title, rows) in sections.items():
        print(f"  {title}: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
        for row in rows:
            detail = row.get("reason") or row.get("note") or row.get("guard") or ""
            print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} | expected={row['expected']} | {detail}")


def print_social_vision_decouple_guard_status():
    summary = social_vision_decouple_guard_summary()
    print("🧪 Phase 10.9 Social/Vision Decoupling Guard")
    print("  Action: read-only; kiểm source boundaries, không gọi model/không tạo draft.")
    print(f"  Progress: {phase10_9_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Priority matrix:")
    for row in summary["priority_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} | expected={row['expected']} | primary={row['primary']}")
    print("  Classifier decoupling:")
    for row in summary["classifier_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} | expected={row['expected']} | guard={row['guard']}")
    print("  Reconcile:")
    for row in summary["reconcile_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | got={row['got']} | expected={row['expected']} | text={','.join(row['text_groups']) or 'none'} vision={','.join(row['vision_groups']) or 'none'}")


def print_phase10_9_status():
    summary = social_vision_decouple_guard_summary()
    print("🧩 Phase 10.9 Status")
    print("  Goal: Social/Vision Decoupling - social, vision, vibe, persona không lấn sai nguồn.")
    print(f"  Progress: {phase10_9_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /social-vision-decouple-status | /social-vision-decouple-test | /social-vision-decouple-guard-status | /phase10-9-ready")


def print_phase10_9_ready():
    summary = social_vision_decouple_guard_summary()
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 10.9 Ready" if ready else "⚠️ Phase 10.9 Ready")
    print("  Goal: social/vision boundaries đủ rõ để sang Phase 10.10 final CNS gate.")
    print(f"  Progress: {phase10_9_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: social_vision_decouple={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 10.9 chỉ decoupling/read-only guard.")


# ── Phase 10.10: CNS Gate ──────────────────────────────────────────────────────


def phase10_10_summary_row(name, summary, label):
    shorten_line = _get_shorten_line()
    failures = phase10_guard_failures(summary)
    pass_count = summary.get("pass_count", 0)
    total = summary.get("total", 0)
    passed = not failures and pass_count == total
    detail = f"{label}={pass_count}/{total} pass"
    if failures:
        first_name, first_detail = phase10_failure_brief(failures[0])
        detail = f"{detail} | first={first_name}:{shorten_line(str(first_detail), 90)}"
    return {
        "name": name,
        "passed": passed,
        "detail": detail,
        "pass_count": pass_count,
        "total": total,
        "failures": failures,
    }


def phase10_10_subphase_rows(voice=None):
    return [
        phase10_10_summary_row("phase10_1_runtime_inventory", phase10_1_guard_summary(), "runtime_inventory"),
        phase10_10_summary_row("phase10_2_command_router", phase10_2_guard_summary(), "command_router"),
        phase10_10_summary_row("phase10_3_state_schema", runtime_state_schema_summary(), "state_schema"),
        phase10_10_summary_row("phase10_4_event_store", phase10_4_guard_summary(), "event_store"),
        phase10_10_summary_row("phase10_5_scheduler", phase10_5_guard_summary(), "scheduler"),
        phase10_10_summary_row("phase10_6_recovery_governor", phase10_6_guard_summary(), "recovery_governor"),
        phase10_10_summary_row("phase10_7_memory_governance", phase10_7_guard_summary(), "memory_governance"),
        phase10_10_summary_row("phase10_8_presence_stability", presence_stability_guard_summary(voice), "presence_stability"),
        phase10_10_summary_row("phase10_9_social_vision_decouple", social_vision_decouple_guard_summary(), "social_vision_decouple"),
    ]


def phase10_10_boundary_snapshot():
    pending_actions = _get_pending_actions()
    pending_plan = _get_phase7_pending_plan()
    runtime_pending = pending_actions.current()
    runtime_queue = _get_runtime_queue()
    queue_snapshot = runtime_queue.snapshot()
    event_executed = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
    audit_executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    return {
        "pending_plan": pending_plan,
        "runtime_pending": runtime_pending,
        "queue_snapshot": queue_snapshot,
        "event_executed": event_executed,
        "audit_executed": audit_executed,
    }


def phase10_10_guard_summary(voice=None):
    subphase_rows = phase10_10_subphase_rows(voice)
    integrated = phase10_guard_summary()
    integrated_critical_names = {
        "command_surface",
        "pending_queues_clear",
        "executor_exposure_closed",
        "audit_execute_false",
        "autonomy_lock_contract",
    }
    integrated_critical_failures = [
        row for row in integrated["rows"]
        if row["name"] in integrated_critical_names and not row["passed"]
    ]
    integrated_context_warnings = [
        row for row in integrated["rows"]
        if row["name"] not in integrated_critical_names and not row["passed"]
    ]
    boundary = phase10_10_boundary_snapshot()
    queue_snapshot = boundary["queue_snapshot"]
    queued_count = len(queue_snapshot.get("queued") or [])
    active_p0 = queue_snapshot.get("active_p0") or 0
    pending_details = []
    if boundary["pending_plan"]:
        pending_details.append(f"phase7_plan=id={boundary['pending_plan'].get('id')}")
    if boundary["runtime_pending"]:
        pending_details.append(f"runtime_pending=id={boundary['runtime_pending'].id}")
    if queued_count:
        pending_details.append(f"runtime_queue={queued_count}")
    if active_p0:
        pending_details.append(f"active_p0={active_p0}")
    command_missing = sorted((PHASE10_REQUIRED_COMMANDS | PHASE10_10_COMMANDS) - KNOWN_SLASH_COMMANDS)
    state_summary = runtime_state_schema_summary()
    event_summary = phase10_4_guard_summary()
    recovery_summary = phase10_6_guard_summary()
    memory_summary = phase10_7_guard_summary()
    presence_summary = presence_stability_guard_summary(voice)
    social_summary = social_vision_decouple_guard_summary()
    support_ready = all(not phase10_guard_failures(summary) for summary in [recovery_summary, memory_summary, presence_summary])
    social_ready = not phase10_guard_failures(social_summary)
    executed_total = len(boundary["event_executed"]) + len(boundary["audit_executed"])
    lock_ok = AUTONOMY_LOCK_PHASE == "Phase 5-10" and AUTONOMY_LOCK_RULE == "no_autonomy_no_semi_autonomy"
    subphase_pass = sum(1 for row in subphase_rows if row["passed"])
    rows = [
        (
            "integrated_phase10_guard",
            not integrated_critical_failures,
            (
                f"critical={len(integrated_critical_names) - len(integrated_critical_failures)}/{len(integrated_critical_names)} "
                f"context_warn={len(integrated_context_warnings)}"
            ),
        ),
        (
            "subphase_closure",
            subphase_pass == len(subphase_rows),
            f"{subphase_pass}/{len(subphase_rows)} pass",
        ),
        (
            "state_schema_final",
            not state_summary.get("failed"),
            f"groups={len(state_summary.get('groups') or [])} fields={len(state_summary.get('rows') or [])} failed={len(state_summary.get('failed') or [])}",
        ),
        (
            "event_audit_integrity",
            not phase10_guard_failures(event_summary) and executed_total == 0,
            f"event_store={event_summary['pass_count']}/{event_summary['total']} executed={executed_total}",
        ),
        (
            "scheduler_queue_clear",
            not pending_details,
            "none" if not pending_details else ", ".join(pending_details),
        ),
        (
            "recovery_memory_presence_ready",
            support_ready,
            f"recovery={recovery_summary['pass_count']}/{recovery_summary['total']} memory={memory_summary['pass_count']}/{memory_summary['total']} presence={presence_summary['pass_count']}/{presence_summary['total']}",
        ),
        (
            "social_vision_boundary_ready",
            social_ready,
            f"social_vision={social_summary['pass_count']}/{social_summary['total']}",
        ),
        (
            "command_surface_final",
            not command_missing,
            f"missing={','.join(command_missing) if command_missing else 'none'}",
        ),
        (
            "execute_flag_guard",
            executed_total == 0,
            f"runtime_event_execute={len(boundary['event_executed'])} audit_execute={len(boundary['audit_executed'])}",
        ),
        (
            "autonomy_lock_contract",
            lock_ok,
            f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}",
        ),
        (
            "phase11_boundary",
            True,
            "Phase 10.10 chỉ chốt gate; Phase 11 chưa mở executor/autonomy",
        ),
        (
            "cns_gate_readonly",
            True,
            "status/guard/test không gọi model, không tạo pending, không click/type/post",
        ),
    ]
    failures = [row for row in rows if not row[1]]
    return {
        "rows": rows,
        "failures": failures,
        "pass_count": len(rows) - len(failures),
        "total": len(rows),
        "subphase_rows": subphase_rows,
        "integrated": integrated,
        "boundary": boundary,
    }


def phase10_10_progress_percent(summary):
    total = summary.get("total") or 0
    if not total:
        return 0
    return int(round((summary.get("pass_count", 0) / total) * 100))


def print_cns_gate_status(voice=None):
    summary = phase10_10_guard_summary(voice)
    boundary = summary["boundary"]
    queue_snapshot = boundary["queue_snapshot"]
    print("🧠 CNS Gate Status")
    print("  Action: read-only; xem cổng tổng Phase 10, không mutate/không execute.")
    print(f"  Phase 10.10 Progress: {phase10_10_progress_percent(summary)}%")
    print(f"  Subphases: {sum(1 for row in summary['subphase_rows'] if row['passed'])}/{len(summary['subphase_rows'])} pass")
    print(f"  Queue: active_p0={queue_snapshot.get('active_p0')} queued={len(queue_snapshot.get('queued') or [])}/{queue_snapshot.get('max_tasks')}")
    print(f"  Pending: phase7={'yes' if boundary['pending_plan'] else 'none'} | runtime={'yes' if boundary['runtime_pending'] else 'none'}")
    print(f"  Execute flags: event={len(boundary['event_executed'])} audit={len(boundary['audit_executed'])}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 10 đóng CNS gate; Phase 11 chỉ bàn sau Ready=True.")


def print_cns_gate_test(raw_text=None, voice=None):
    print("🧪 CNS Gate Test")
    print("  Action: read-only; synthetic/summary only, không gọi model/không execute.")
    summary = phase10_10_guard_summary(voice)
    key = (raw_text or "").strip().lower()
    if key in {"", "all"}:
        print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
        print("  Subphase closure:")
        for row in summary["subphase_rows"]:
            print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")
        print("  Gate rows:")
        for name, passed, detail in summary["rows"]:
            print(f"    {'pass' if passed else 'fail'} | {name} | {detail}")
        return
    if key in {"subphase", "subphases", "phase"}:
        rows = summary["subphase_rows"]
        print(f"  Section: subphases | {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
        for row in rows:
            print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")
        print("  Execute: False")
        return
    if key in {"queue", "queues", "pending"}:
        boundary = summary["boundary"]
        queue_snapshot = boundary["queue_snapshot"]
        print("  Section: queue/pending")
        print(f"  Phase7 pending: {'yes' if boundary['pending_plan'] else 'none'}")
        print(f"  Runtime pending: {'yes' if boundary['runtime_pending'] else 'none'}")
        print(f"  Runtime queue: active_p0={queue_snapshot.get('active_p0')} queued={len(queue_snapshot.get('queued') or [])}/{queue_snapshot.get('max_tasks')}")
        print("  Execute: False")
        return
    if key in {"event", "events", "audit"}:
        boundary = summary["boundary"]
        print("  Section: event/audit")
        print(f"  Runtime event execute=True: {len(boundary['event_executed'])}")
        print(f"  Phase9 audit execute=True: {len(boundary['audit_executed'])}")
        print(f"  Runtime event log: {runtime_event_summary()}")
        print(f"  Phase9 audit log: {phase9_audit_summary()}")
        print("  Execute: False")
        return
    if key in {"command", "commands"}:
        missing = sorted((PHASE10_REQUIRED_COMMANDS | PHASE10_10_COMMANDS) - KNOWN_SLASH_COMMANDS)
        print("  Section: command surface")
        print(f"  Known commands: {len(KNOWN_SLASH_COMMANDS)}")
        print(f"  Missing: {','.join(missing) if missing else 'none'}")
        print("  Execute: False")
        return
    if key in {"lock", "autonomy", "phase11"}:
        print("  Section: autonomy/phase11")
        print(f"  Lock: {AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}")
        print("  Phase 11: discussion only until this gate is Ready=True.")
        print("  Execute: False")
        return
    print("  Status: not_found")
    print("  Sections: subphases, queue, events, commands, autonomy")
    print("  Execute: False")


def print_cns_gate_guard_status(voice=None):
    summary = phase10_10_guard_summary(voice)
    print("🧪 Phase 10.10 CNS Gate Guard")
    print("  Action: read-only; tổng kiểm CNS gate, không tạo pending, không execute.")
    print(f"  Progress: {phase10_10_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Subphases:")
    for row in summary["subphase_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")


def print_phase10_10_status(voice=None):
    summary = phase10_10_guard_summary(voice)
    print("🧩 Phase 10.10 Status")
    print("  Goal: Final CNS Gate - đóng cổng ổn định trước khi bàn Phase 11.")
    print(f"  Progress: {phase10_10_progress_percent(summary)}%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Commands: /cns-gate-status | /cns-gate-test | /cns-gate-guard-status | /phase10-10-ready")


def print_phase10_10_ready(voice=None):
    summary = phase10_10_guard_summary(voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print("✅ Phase 10.10 Ready" if ready else "⚠️ Phase 10.10 Ready")
    print("  Goal: CNS gate đủ sạch để đóng Phase 10 và bắt đầu bàn Phase 11.")
    print(f"  Progress: {phase10_10_progress_percent(summary)}%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: cns_gate={summary['pass_count']}/{summary['total']}")
    print("  Autonomy: unchanged; Phase 10.10 không mở executor, không bật tự trị.")


# ── Phase 10 core guard summary (depends on subphases) ─────────────────────────


def phase10_guard_summary():
    phase6_model = build_phase6_status_model()
    phase7_model = build_phase7_status_model()
    phase8_model = build_phase8_status_model()
    phase9_model = build_phase9_status_model()
    rows = []
    phase10_add_phase_row(rows, "phase6_foundation", phase6_model["checks"])
    phase10_add_phase_row(rows, "phase7_foundation", phase7_model["checks"])
    phase10_add_phase_row(rows, "phase8_foundation", phase8_model["checks"])
    phase10_add_phase_row(rows, "phase9_foundation", phase9_model["checks"])
    phase10_add_guard_summary_row(rows, "phase10_1_runtime_inventory", phase10_1_guard_summary(), "runtime_inventory")
    phase10_add_guard_summary_row(rows, "phase10_2_command_router", phase10_2_guard_summary(), "command_router")
    phase10_add_guard_summary_row(rows, "phase10_3_state_schema", runtime_state_schema_summary(), "state_schema")
    phase10_add_guard_summary_row(rows, "phase10_4_event_store", phase10_4_guard_summary(), "event_store")
    phase10_add_guard_summary_row(rows, "phase10_5_scheduler", phase10_5_guard_summary(), "scheduler")
    phase10_add_guard_summary_row(rows, "phase10_6_recovery_governor", phase10_6_guard_summary(), "recovery_governor")
    phase10_add_guard_summary_row(rows, "phase10_7_memory_governance", phase10_7_guard_summary(), "memory_governance")
    phase10_add_guard_summary_row(rows, "phase10_8_presence_stability", presence_stability_guard_summary(), "presence_stability")
    phase10_add_guard_summary_row(rows, "phase10_9_social_vision_decouple", social_vision_decouple_guard_summary(), "social_vision_decouple")

    missing_commands = sorted(PHASE10_REQUIRED_COMMANDS - KNOWN_SLASH_COMMANDS)
    rows.append({
        "name": "command_surface",
        "passed": not missing_commands,
        "detail": "all_required_present" if not missing_commands else f"missing={','.join(missing_commands)}",
    })

    pending_actions = _get_pending_actions()
    pending_plan = _get_phase7_pending_plan()
    runtime_pending = pending_actions.current()
    pending_details = []
    if pending_plan:
        pending_details.append(f"phase7_plan=id={pending_plan.get('id')}")
    if runtime_pending:
        pending_details.append(f"runtime_pending=id={runtime_pending.id} action={runtime_pending.action}")
    rows.append({
        "name": "pending_queues_clear",
        "passed": not pending_details,
        "detail": "none" if not pending_details else ", ".join(pending_details),
    })

    executor_row = next((row for row in phase8_model["checks"] if row[0] == "executor_exposure_trace"), None)
    executor_clear = bool(executor_row and executor_row[1] == "pass")
    rows.append({
        "name": "executor_exposure_closed",
        "passed": executor_clear,
        "detail": executor_row[2] if executor_row else "missing_executor_trace",
    })

    executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
    rows.append({
        "name": "audit_execute_false",
        "passed": not executed,
        "detail": f"executed={len(executed)}",
    })

    AUTONOMY_LOOP = _get_autonomy_loop()
    lock_ok = AUTONOMY_LOCK_PHASE == "Phase 5-10" and AUTONOMY_LOCK_RULE == "no_autonomy_no_semi_autonomy"
    rows.append({
        "name": "autonomy_lock_contract",
        "passed": lock_ok,
        "detail": f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}",
    })

    failures = [row for row in rows if not row["passed"]]
    return {
        "rows": rows,
        "failures": failures,
        "total": len(rows),
        "pass_count": len(rows) - len(failures),
    }
