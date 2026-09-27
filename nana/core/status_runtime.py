"""Runtime and operator status panels for Nana."""

from __future__ import annotations

import time

from nana.autonomy.lock import AUTONOMY_LOCK_PHASE, AUTONOMY_LOCK_RULE
from nana.config import BROWSER_FRESH_SECONDS, CHAT_CONTEXT_SUPPRESS, PROACTIVE_BROWSER_COOLDOWN
from nana.core.format import shorten_line
from nana.memory import memory, memory_lock
from nana.runtime.attention import context_for_attention, format_attention_status
from nana.runtime.browser_state import browser_age_seconds, browser_snapshot_state
from nana.runtime.context import context_snapshot, current_time_context, get_confidence
from nana.runtime.metrics import (
    BROWSER_REFRESH_COOLDOWN_SECONDS,
    RUNTIME_LATENCY,
    browser_refresh_last_age_seconds,
)
from nana.runtime.persona import (
    RESIDUE_DECAY_PER_TICK,
    evaluate_presence_rhythm,
    format_persona_status,
    persona_state_snapshot,
    temperature_clamp,
)
from nana.runtime.priority_queue import runtime_queue
from nana.runtime.reconcile import print_runtime_reconcile_status
from nana.runtime.recovery import recovery_latest_summary, recovery_status_lines

try:
    from nana.runtime.memory_grounding import MemoryClaimDetector, EvidenceBuilder, ConfidenceVerifier
    _GROUNDING_STATUS_AVAILABLE = True
except Exception:
    _GROUNDING_STATUS_AVAILABLE = False

def _age_text(age: float | None, suffix: str = "s") -> str:
    if age is None:
        return "never"
    return f"{age:.1f}{suffix}"

def _ms_text(value) -> str:
    if value is None:
        return "None"
    try:
        return f"{float(value):.1f}ms"
    except (TypeError, ValueError):
        return str(value)

def _optional_ms_text(value, skipped_label: str = "skipped") -> str:
    if value is None:
        return skipped_label
    return _ms_text(value)

def _active_memory_counts() -> tuple[int, int, int, int]:
    with memory_lock:
        long_term = list(memory.get("long_term", []))
        short_term = list(memory.get("short_term", []))
        chat_log = list(memory.get("chat_log", []))
        labels = dict(memory.get("memory_labels", {}))
    return len(long_term), len(short_term), len(chat_log), len(labels)

def print_attention_state() -> None:
    for line in format_attention_status(context_for_attention(context_snapshot())):
        print(line)

def print_recovery_summary() -> None:
    summary = recovery_latest_summary()
    print("🧯 Recovery")
    if summary:
        print(f"  Latest: {summary}")
        print("  Detail: /recovery")
    else:
        print("  Latest: none")

def print_focus_state() -> None:
    browser = dict(context_snapshot().get("browser", {}))
    selected = browser.get("selected_text")
    heading = browser.get("page_heading")
    local_summary = browser.get("local_summary")
    focus_source = "none"
    if selected:
        focus_source = "selected_text"
    elif heading:
        focus_source = "page_heading"
    elif local_summary:
        focus_source = "local_summary"
    focus_text = selected or heading or local_summary

    print("🎯 Focus")
    print(f"  Source: {focus_source}")
    print(f"  Kind: {browser.get('kind')} | Title: {shorten_line(browser.get('title'), 120)}")
    print(f"  Selected length: {len(selected) if selected else 0}")
    print(f"  Focus text: {shorten_line(focus_text, 220)}")
    print(f"  Local summary: {shorten_line(local_summary, 220)}")
    print(f"  Local helper: {browser.get('local_helper_debug')}")
    print(f"  DOM debug: {browser.get('dom_debug')}")

def print_presence_state() -> None:
    now = time.time()
    snapshot = context_snapshot()
    browser = dict(snapshot.get("browser", {}))
    proactive = dict(snapshot.get("proactive", {}))
    last_chat = snapshot.get("last_chat_time") or 0
    last_reaction = proactive.get("last_browser_reaction_time") or 0
    last_chat_age = None if not last_chat else max(0.0, now - last_chat)
    last_reaction_age = None if not last_reaction else max(0.0, now - last_reaction)
    recent_chat_active = last_chat_age is not None and last_chat_age < CHAT_CONTEXT_SUPPRESS
    cooldown_active = last_reaction_age is not None and last_reaction_age < PROACTIVE_BROWSER_COOLDOWN
    blocked_by = []
    if not browser.get("available"):
        blocked_by.append("browser_unavailable")
    if snapshot.get("in_flow"):
        blocked_by.append("flow")
    if recent_chat_active:
        blocked_by.append("recent_chat")
    if cooldown_active:
        blocked_by.append("cooldown")
    if not blocked_by:
        blocked_by.append("none")

    print("🌿 Presence")
    print(f"  Ready: {blocked_by == ['none']}")
    print(f"  Zone: {snapshot.get('active_zone')} | App: {snapshot.get('active_app')} | Flow: {snapshot.get('in_flow')}")
    print(f"  Browser: available={browser.get('available')} | kind={browser.get('kind')}")
    print(f"  Last chat: {_age_text(last_chat_age, 's ago')}")
    print(f"  Last browser reaction: {_age_text(last_reaction_age, 's ago')}")
    print(f"  Suppress: {'active' if recent_chat_active else 'inactive'}")
    print(f"  Cooldown: {'active' if cooldown_active else 'inactive'}")
    print(f"  Blocked by: {', '.join(blocked_by)}")
    debug = proactive.get("presence_debug") or {}
    print(f"  Debug: {debug.get('blocked_reason') or 'none'}")

def print_runtime_status() -> None:
    count = int(RUNTIME_LATENCY.get("browser_refresh_count") or 0)
    total_ms = float(RUNTIME_LATENCY.get("browser_refresh_total_ms") or 0.0)
    avg_ms = (total_ms / count) if count else None
    cache_hits = int(RUNTIME_LATENCY.get("browser_refresh_cache_hits") or 0)
    coalesced = int(RUNTIME_LATENCY.get("browser_refresh_coalesced") or 0)
    cooldown_skips = int(RUNTIME_LATENCY.get("browser_refresh_cooldown_skips") or 0)
    skip_age = RUNTIME_LATENCY.get("browser_refresh_last_skip_age")
    skip_age_text = "None" if skip_age is None else f"{float(skip_age):.1f}s"
    browser = dict(context_snapshot().get("browser", {}))
    age = browser_age_seconds(browser)
    state = browser_snapshot_state(browser)
    last_age = browser_refresh_last_age_seconds()

    print("⏱️ Runtime Status")
    print("  Architecture: new runtime core; legacy main proxy removed.")
    print(
        "  Browser snapshot: "
        f"available={browser.get('available')} | kind={browser.get('kind')} | "
        f"state={state} | age={'None' if age is None else f'{age:.1f}s'}"
    )
    print(
        "  Browser refresh latency: "
        f"last={_ms_text(RUNTIME_LATENCY.get('browser_refresh_last_ms'))} | "
        f"avg={_ms_text(avg_ms)} | count={count}"
    )
    print(
        "  Breakdown: "
        f"read={_ms_text(RUNTIME_LATENCY.get('browser_read_last_ms'))} | "
        f"local_summary={_optional_ms_text(RUNTIME_LATENCY.get('local_summary_last_ms'))}"
    )
    print(
        "  Last refresh: "
        f"{'never' if last_age is None else f'{last_age:.1f}s ago'} | "
        f"reason={RUNTIME_LATENCY.get('browser_refresh_last_reason')} | "
        f"mode={'deep' if RUNTIME_LATENCY.get('browser_refresh_last_deep') else 'light'}"
    )
    print(
        "  Cache policy: "
        f"last={RUNTIME_LATENCY.get('browser_refresh_last_policy')} | "
        f"reason={RUNTIME_LATENCY.get('browser_refresh_last_policy_reason')} | "
        f"last_state={RUNTIME_LATENCY.get('browser_refresh_last_state')} | "
        f"hits={cache_hits} | coalesced={coalesced} | "
        f"cooldown_skips={cooldown_skips} | skip_age={skip_age_text}"
    )
    print(
        "  Browser refresh policy: "
        f"/br=force lightweight | /br-deep=force local_summary | "
        f"auto cooldown={BROWSER_REFRESH_COOLDOWN_SECONDS:.1f}s"
    )

def print_queue_state() -> None:
    snapshot = runtime_queue.snapshot()
    age = snapshot["last_user_input_age"]
    print("📚 Runtime Queue")
    print(f"  P0 active: {bool(snapshot['active_p0'])}")
    print(f"  Last user input: {'never' if age is None else f'{age:.1f}s ago'}")
    if not snapshot["queued"]:
        print("  Queued: none")
        return
    for task in snapshot["queued"]:
        print(
            "  Task: "
            f"{task['priority']} | {task['name']} | "
            f"source={task['source']} | status={task['status']} | "
            f"defer={task['defer_reason']} | age={task['age']:.1f}s"
        )

def print_time_state() -> None:
    now = current_time_context()
    print("🕒 Time")
    print(f"  Date: {now['date_vi']} ({now['date']})")
    print(f"  Time: {now['time']}")
    print(f"  Part: {now['part_of_day']}")
    print(f"  Timezone: {now['timezone']}")

def print_nana_status() -> None:
    now = time.time()
    snapshot = context_snapshot()
    browser = dict(snapshot.get("browser", {}))
    proactive = dict(snapshot.get("proactive", {}))
    persona = persona_state_snapshot()
    silence = persona.get("silence") or {}
    window_start = float(silence.get("window_start") or now)
    ambient_count = int(silence.get("ambient_count") or 0)
    ambient_limit = int(silence.get("ambient_limit") or 0)
    manual_until = float(persona.get("manual_until") or 0)
    manual_text = "inactive"
    if manual_until and now < manual_until:
        manual_text = f"active {manual_until - now:.0f}s"
    age = browser_age_seconds(browser)
    browser_fresh = bool(browser.get("available")) and age is not None and age <= BROWSER_FRESH_SECONDS
    attention_lines = format_attention_status(context_for_attention(snapshot))[1:4]
    last_reaction = proactive.get("last_browser_reaction_time") or 0
    reaction_text = "never" if not last_reaction else f"{max(0.0, now - last_reaction):.1f}s ago"
    long_count, short_count, chat_count, label_count = _active_memory_counts()
    recovery = recovery_latest_summary() or "none"

    print("🧩 Nana Status")
    print(
        "  Runtime: "
        f"zone={snapshot.get('active_zone')} | app={snapshot.get('active_app')} | "
        f"idle={snapshot.get('idle_state')} | flow={snapshot.get('in_flow')} | "
        f"confidence={get_confidence():.2f}"
    )
    print(
        "  Persona: "
        f"mode={persona.get('mode')} | intensity={persona.get('personality_intensity')}->"
        f"{persona.get('target_intensity')} | residue={persona.get('residue_level')} | manual={manual_text}"
    )
    print(
        "  Silence: "
        f"{ambient_count}/{ambient_limit} in {max(0.0, now - window_start):.0f}s | "
        f"last_reason={persona.get('last_reason')}"
    )
    print(
        "  Presence: "
        f"enabled={proactive.get('enabled', True)} | last_seen={proactive.get('last_browser_kind')} | "
        f"last_reaction={reaction_text}"
    )
    for line in attention_lines:
        print(f"  {line.strip()}")
    print(
        "  Browser: "
        f"available={browser.get('available')} | fresh={browser_fresh} | "
        f"age={'None' if age is None else f'{age:.1f}s'} | "
        f"kind={browser.get('kind')} | title={shorten_line(browser.get('title'), 90)}"
    )
    print(f"  Recovery: {recovery}")
    print(f"  Memory: long={long_count}/50 | short={short_count}/16 | chat={chat_count}/50 | labels={label_count}")
    print(f"  Autonomy lock: {AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}")
    print("  Detail: /vibe-status | /presence | /attention | /recovery | /memory-status | /runtime-status")

    # === MEMORY GROUNDING STATUS (CONFIDENCE-VERIFIER-1) ===
    if _GROUNDING_STATUS_AVAILABLE:
        try:
            detector = MemoryClaimDetector()
            builder = EvidenceBuilder(lane="operator_backstage")
            sample_evidence = builder.build(query="hôm qua", claim_text="hôm qua mình nói gì?")
            print(f"[MEMORY-EVIDENCE] detector_ready={True} lane=operator_backstage")
            print(f"[MEMORY-EVIDENCE] sample_status={sample_evidence.status} confidence={sample_evidence.confidence:.2f}")
        except Exception as e:
            print(f"[MEMORY-EVIDENCE] unavailable: {type(e).__name__}")
    else:
        print("[MEMORY-EVIDENCE] module_unavailable")

def print_autonomy_lock_status() -> None:
    print("🔒 Autonomy Lock")
    print(f"  Scope: {AUTONOMY_LOCK_PHASE}")
    print(f"  Rule: {AUTONOMY_LOCK_RULE}")
    print("  Blocks: browser.click, browser.type, social.type_draft, message.send, purchase.checkout")
    print("  Allowed: observe/read/status/draft preview only")

def print_residue_status() -> None:
    state = persona_state_snapshot()
    sources = dict(state.get("residue_sources") or {})
    total = int(state.get("residue_level") or 0)
    print("🧼 Residue Status")
    print(f"  Total: {total}/100")
    print(f"  Clamp: {temperature_clamp(state)}")
    print(f"  Last reason: {state.get('last_reason')}")
    if not sources:
        print("  Sources: none")
        print("  State: sạch; stale context không có lực kéo hiện tại.")
        return
    print("  Sources:")
    for source, value in sorted(sources.items()):
        value = max(0, int(value or 0))
        per_tick = RESIDUE_DECAY_PER_TICK.get(source, RESIDUE_DECAY_PER_TICK.get("unknown", 5))
        ticks = 0 if per_tick <= 0 else (value + per_tick - 1) // per_tick
        eta = ticks * 30
        print(f"    {source}: {value}/100 | decay={per_tick}/30s | clears_in~{eta}s")
    print("  Rule: nguồn social/vision/voice chỉ ảnh hưởng ngắn hạn; task hiện tại luôn ưu tiên cao hơn.")

def print_vts_expression_policy_status() -> None:
    from nana.config import (
        VTS_EXPRESSION_COOLDOWN_SECONDS,
        VTS_EXPRESSION_DEFAULT_CHANCE,
        VTS_EXPRESSION_RESET_DELAY_SECONDS,
        VTS_EXPRESSION_RESET_FALLBACK_HOTKEY,
        VTS_EXPRESSION_RESET_TIMEOUT_SECONDS,
    )
    from nana.runtime.expression_router import get_expression_router

    router_status = get_expression_router().get_status()

    print("🎭 VTS Expression Policy")
    print("  Action: read-only; không gọi VTube Studio.")
    print(f"  Router enabled: {router_status['enabled']}")
    print(f"  VTS available: {router_status['vts_available']}")
    print(f"  VTS connected: {router_status['vts_connected']}")
    print(f"  Cooldown: {VTS_EXPRESSION_COOLDOWN_SECONDS:.1f}s")
    print(f"  Router cooldown remaining: {router_status['cooldown_remaining_seconds']:.1f}s")
    print(f"  Default chance: {VTS_EXPRESSION_DEFAULT_CHANCE:.2f}")
    print(f"  Reset after voice idle: {VTS_EXPRESSION_RESET_DELAY_SECONDS:.1f}s")
    print(f"  Reset timeout: {VTS_EXPRESSION_RESET_TIMEOUT_SECONDS:.1f}s")
    print(f"  Fallback hotkey toggle: {VTS_EXPRESSION_RESET_FALLBACK_HOTKEY}")
    print(f"  Catalog size: {router_status['catalog_size']} expressions")
    print(f"  Missing expression count: {router_status['missing_count_total']}")
    print(f"  Last missing expression: {router_status['last_missing_expression'] or 'none'}")
    print("  Rule: chat/ambient/runtime expression sẽ tự reset sau khi voice queue idle.")

def print_recovery_status() -> None:
    for line in recovery_status_lines():
        print(line)

def print_persona_status() -> None:
    for line in format_persona_status():
        print(line)

def print_runtime_reconcile_line() -> None:
    print_runtime_reconcile_status()
