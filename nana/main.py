"""LEGACY PARKING LOT - NOT THE RUNTIME HOT PATH.

Current runtime path:
    __main__.py -> nana.cli.app -> nana.cli.handle_text

This file is kept for audit/history while older phase code is being split into
small runtime modules.  Do not treat it as the active Nana core unless a current
probe proves otherwise.
"""

import asyncio
import json
import random
import re
import time
import unicodedata
from difflib import get_close_matches

import keyboard
import psutil

try:
    import win32gui
    import win32process

    PHASE35_WIN32_AVAILABLE = True
except ImportError:
    win32gui = None
    win32process = None
    PHASE35_WIN32_AVAILABLE = False

try:
    from PIL import ImageGrab

    PHASE36_IMAGEGRAB_AVAILABLE = True
except ImportError:
    ImageGrab = None
    PHASE36_IMAGEGRAB_AVAILABLE = False

from nana.actions.broker import action_broker
from nana.actions.drafts import social_drafts
from nana.actions.executor import action_executor
from nana.actions.intent import plan_intent
from nana.actions.pending import pending_actions
from nana.actions.plan import build_action_plan
from nana.actions.privacy import build_context_budget_preview, build_privacy_report
from nana.actions.suggest import build_next_step_suggestion
from nana.actions.registry import action_registry
from nana.browser.context import BrowserContextReader, BrowserSnapshot
from nana.browser.vision import VisionPreviewer
from nana.brain.gpt import (
    StreamingReplySurfaceSanitizer,
    TerminalAudioTagStreamSanitizer,
    ask_gpt,
    ask_gpt_stream,
    finalize_reply,
    fix_pronoun,
    send_multi,
    shape_chat_reply,
    vary_text,
)
from nana.brain.llmgate_client import call_llmgate, call_llmgate_vision
from nana.brain.local_helper import apply_local_summary
from nana.brain.model_router import route_sidecar_task
from nana.brain.refiner import auto_refine_nana_reply, refine_nana_reply, should_refine_reply
from nana.brain.reaction_composer import compose_reaction
from nana.config import (
    BROWSER_FRESH_SECONDS,
    CHAT_CONTEXT_SUPPRESS,
    CHAT_HISTORY_PATH,
    DATA_DIR,
    DEBUG_NO_TTS,
    ELEVEN_OUTPUT_FORMAT,
    GPT_COOLDOWN,
    MEMORY_PATH,
    OPENAI_API_KEY,
    PROACTIVE_BROWSER_COOLDOWN,
    VOICE_CACHE_DIR,
    VOICE_CACHE_ENABLED,
    VOICE_CACHE_MAX_TEXT_CHARS,
    VOICE_CHUNKING_ENABLED,
    VOICE_CHUNK_MAX_CHARS,
    VOICE_STREAMING_DRY_RUN_ENABLED,
    VOICE_STREAMING_ENABLED,
    VOICE_STREAMING_DIRECT_ONLY,
    VOICE_STREAMING_KILL_SWITCH,
    VOICE_STREAMING_PILOT_ENABLED,
    VOICE_TEST_MODE,
    VTS_STARTUP_ENABLED,
)
from nana.integrations.vts import (
    connect_vts,
    ensure_vts_ready,
    get_vts_runtime,
    trigger_expression_lifecycle,
    vts_mouth_loop,
    vts_snapshot,
)
from nana.memory import (
    add_jealousy,
    extract_important,
    memory,
    memory_cancel_action,
    memory_lock,
    memory_compact_plan_report,
    memory_compact_preview_report,
    memory_confirm_action,
    memory_drop_preview_report,
    memory_filter_preview_report,
    memory_governance_summary,
    memory_governance_test_report,
    memory_health_report,
    memory_item_key,
    memory_keep_report,
    memory_labels_report,
    memory_pending_action_report,
    memory_review_report,
    memory_status_report,
    memory_unkeep_report,
    save_chat_log,
    save_memory_async,
    update_emotion,
)
from nana.runtime.capabilities import (
    handle_osu_command,
    handle_osu_command_async,
    is_osu_command,
    osu_adapter_enabled,
    osu_adapter_status,
    register_osu_commands,
    resolve_stardew_export,
    set_osu_adapter_runtime_mode,
    set_stardew_adapter_runtime_mode,
    stardew_adapter_enabled,
    stardew_adapter_status,
)
from nana.runtime.context import (
    current_time_context,
    context_lock,
    context_state,
    get_confidence,
    mark_chat_time,
    reset_proactive_state,
    set_proactive_enabled,
    sync_proactive_browser_seen,
    update_browser_state,
    get_active_window,
)
from nana.runtime.attention import context_for_attention, evaluate_attention_window, format_attention_status
from nana.runtime.logger import LOG_DIR, LOG_FILES, log_event
from nana.runtime.persona import (
    clear_transition_log,
    format_persona_status,
    format_presence_rhythm_status,
    format_sources_value,
    format_transition_log,
    mark_residue,
    mark_social_residue,
    observe_text_for_persona,
    persona_prompt_block,
    persona_state_snapshot,
    RESIDUE_DECAY_PER_TICK,
    reset_persona,
    reset_presence_rhythm,
    set_persona_mode,
    set_presence_rhythm_enabled,
    social_prompt_guard,
    temperature_clamp,
)
from nana.runtime.identity import (
    format_identity_block as identity_prompt_block,
    load_identity,
    load_users,
    reload_all as identity_reload,
    resolve_user,
)
from nana.runtime.live_awareness import (
    build_live_awareness_snapshot,
    format_awareness_status,
    is_live_awareness_question,
    lock_focus,
    repair_awareness_reply,
)
from nana.runtime.awareness_memory import get_awareness_memory
from nana.runtime.memory_spine import get_memory_spine
from nana.runtime.process_watch import watch_process
from nana.runtime.pulse import nana_pulse
from nana.runtime.priority_queue import Priority, runtime_queue
from nana.runtime.recovery import (
    recovery_clear,
    recovery_clear_browser,
    recovery_clear_social,
    recovery_clear_vision,
    recovery_dry_run,
    recovery_governor_config,
    recovery_latest_summary,
    recovery_message,
    recovery_snapshot,
    recovery_status_lines,
    vision_recovery_kind,
)
from nana.voice.engine import VoiceEngine
from nana.autonomy import AutonomyLoop, AutonomyState, AutonomyExpress
from nana.memory import memory, memory_lock, save_memory_async

ai_active = True
SESSION_START_TIME = time.time()
last_gpt_time = 0
LAST_USER_INPUTS = []  # list[(timestamp, normalized_text)] for input dedup
LAST_USER_INPUT_DEDUP_WINDOW = 12.0
LAST_USER_INPUT_DEDUP_LIMIT = 4
DEFAULT_SOCIAL_CLASSIFY_REQUEST = "Nana viết nháp reply siêu ngắn cho tweet này"

CASUAL_LAST_REPLY = ""


def recovery_notice(kind, detail=None, cooldown=True):
    mode = (persona_state_snapshot() or {}).get("mode", "chill")
    return recovery_message(kind, detail=detail, cooldown=cooldown, mode=mode)


async def handle_identity_command(text):
    """Handle /identity and /identity_done commands.

    /identity              -> show current identity + resolved user
    /identity reload       -> reload from JSON, then show
    /identity test <name>  -> resolve as if viewer_name=<name>
    /identity block        -> print the prompt block that will be injected
    /identity_done         -> print "OK, identity is loaded."
    """
    lowered = text.lower().strip()
    parts = lowered.split(maxsplit=2)
    cmd = parts[0] if parts else "/identity"
    sub = parts[1] if len(parts) > 1 else ""
    rest = parts[2] if len(parts) > 2 else ""

    if cmd == "/identity_done":
        print("✅ identity_done — identity layer đã load. Ba chat bình thường nhé.")
        print("   Identity file: nana/data/identity.json")
        print("   Users file:    nana/data/users.json")
        print("   Trigger reload: /identity reload")
        return False

    if sub == "reload":
        data = identity_reload()
        print("🔄 Identity reloaded từ JSON.")
        print(f"   identity keys: {list((data.get('identity') or {}).keys())}")
        print(f"   users: {list((data.get('users') or {}).keys())}")
        return False

    if sub == "test":
        viewer = rest.strip() or None
        user = resolve_user(message="", viewer_name=viewer, stream_mode=bool(viewer))
        print(f"🧪 resolve_user(viewer_name={viewer!r}) ->")
        for k, v in user.items():
            print(f"   {k}: {v}")
        return False

    if sub == "block":
        current = resolve_user(message="", viewer_name=None, stream_mode=False)
        block = identity_prompt_block(current)
        print("📜 Identity block (sẽ inject vào system prompt):")
        print("─" * 60)
        print(block)
        print("─" * 60)
        return False

    identity = load_identity()
    users = load_users()
    current = resolve_user(message="", viewer_name=None, stream_mode=False)
    print("🪪 Identity hiện tại:")
    print(f"  self_name:     {identity.get('self_name')}")
    print(f"  self_pronoun:  {identity.get('self_pronoun')}")
    print(f"  nana_role:     {identity.get('nana_role')}")
    print(f"  nana_birth:    {identity.get('nana_birth')}")
    print(f"  default_user:  {identity.get('default_user_id')}")
    print(f"  addressing:    {list((identity.get('addressing_rules') or {}).keys())}")
    print(f"  facts count:   {len(identity.get('facts') or [])}")
    print("  users:")
    for uid, u in users.items():
        if isinstance(u, dict):
            print(f"    - {uid}: name={u.get('name')!r} role={u.get('role')!r} aliases={u.get('aliases')}")
    print(f"  resolved (default): name={current.get('name')!r} role={current.get('role')!r} pronoun={current.get('pronoun')!r}")
    print("  sub-cmds: reload | test <viewer_name> | block")
    return False


def print_attention_state():
    with context_lock:
        snapshot = dict(context_state)
        snapshot["browser"] = dict(context_state.get("browser", {}))
    for line in format_attention_status(context_for_attention(snapshot)):
        print(line)


def print_recovery_summary():
    summary = recovery_latest_summary()
    print("🧯 Recovery")
    if summary:
        print(f"  Latest: {summary}")
        print("  Detail: /recovery")
    else:
        print("  Latest: none")


def print_classify_expect(raw_text):
    expected, sample = parse_classify_expect(raw_text)
    print("🧪 Classify Expect")
    print("  Action: read-only; không lưu regression case.")
    if not expected or not sample:
        print("  Missing: /classify-expect <style> | <text>")
        return
    source_text = build_social_draft_source(sample, broker_context={}, vision_description=None)
    media_mode, reaction_style, guard_hint = social_source_classification(
        source_text,
        broker_context={"browser_kind": "social", "browser_social_post_text": sample},
        raw_text=sample,
        vision_description=None,
    )
    fallback = fallback_public_social_reply(source_text=source_text)
    fallback_ok, fallback_reason = social_guard_expected_fallback_ok(expected, fallback)
    compacted = compact_public_reaction_reply(fallback, source_text=source_text, intent="social.reply")
    fail_stage = "none"
    fail_reason = "none"
    if reaction_style != expected:
        fail_stage = "classifier"
        fail_reason = f"expected={expected}"
    elif not fallback_ok:
        fail_stage = "fallback"
        fail_reason = fallback_reason
    elif compacted != fallback:
        fail_stage = "compact_guard"
        fail_reason = f"fallback_changed_to={compacted}"
    status = "pass" if fail_stage == "none" else "fail"
    print(f"  Status: {status}")
    print(f"  Expected: {expected}")
    print(f"  Got: {reaction_style}")
    print(f"  Media: {media_mode}")
    print(f"  Guard hint: {guard_hint}")
    print(f"  Fallback: {fallback}")
    if fail_stage != "none":
        print(f"  Fail stage: {fail_stage}")
        print(f"  Fail reason: {fail_reason}")
        print(f"  Promote hint: nếu expected đúng, thêm case vào SOCIAL_GUARD_EXAMPLES sau khi xem lại context.")
    print(f"  Source: {shorten_line(source_text, 220)}")


def print_controlled_gate_status():
    try:
        from nana.phases.phase24 import phase24_5_guard_summary, phase24_5_progress_percent, runtime_queue
    except ImportError:
        print("⚠️ phase24 functions not available")
        return
    summary = phase24_5_guard_summary()
    queue = summary.get("queue", {})
    print("🧠 Phase 24 Controlled Gate Status")
    print("  Action: read-only; tổng kiểm streaming control layer, chưa gọi stream thật.")
    print(f"  Phase 24.5 Progress: {phase24_5_progress_percent(summary)}%")
    print(f"  Queue: active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}/{getattr(runtime_queue(), 'max_items', 50)}")
    for name, passed, detail in summary.get("rows", []):
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")


def print_controlled_stream_gate_guard_status(vts=None, voice=None):
    try:
        from nana.phases.phase24 import phase24_5_guard_summary, phase24_5_progress_percent
    except ImportError:
        print("⚠️ phase24_5_guard_summary not available")
        return
    summary = phase24_5_guard_summary()
    print("🧪 Phase 24.5 Controlled Stream Gate Guard")
    print("  Action: tổng kiểm Phase 24 streaming, không gọi ElevenLabs/voice/execute.")
    print(f"  Progress: {phase24_5_progress_percent(summary)}%")
    print("  Companion Overall: ~84% / 100%")
    print(f"  Summary: {summary.get('pass_count', '?')}/{summary.get('total', '?')} pass")
    for name, passed, detail in summary.get("rows", []):
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Subphases:")
    for name, passed, detail in summary.get("subphase_rows", []):
        print(f"    {'pass' if passed else 'fail'} | {name} | {detail}")
    print("  Controlled stream gate regression:")
    for row in summary.get("test_rows", []):
        print(f"    {'pass' if row.get('passed') else 'fail'} | {row.get('name')} | gate={row.get('gate')} reason={row.get('reason')}")


def print_classify_text(raw_text):
    source_text = build_social_draft_source(raw_text, broker_context={}, vision_description=None)
    media_mode, reaction_style, guard_hint = social_source_classification(
        source_text,
        broker_context={"browser_kind": "social", "browser_social_post_text": raw_text},
        raw_text=raw_text,
        vision_description=None,
    )
    fallback = fallback_public_social_reply(source_text=source_text)
    print("🧪 Text Classifier")
    print(f"  Media: {media_mode}")
    print(f"  Reaction style: {reaction_style}")
    print(f"  Guard hint: {guard_hint}")
    print(f"  Fallback draft: {fallback}")
    print(f"  Source: {shorten_line(source_text, 220)}")
    print("  Action: read-only; không gọi model, không tạo draft.")


def parse_classify_expect(raw_text):
    text = str(raw_text or "").strip()
    if "|" in text:
        expected, sample = text.split("|", 1)
        return expected.strip(), sample.strip()
    parts = text.split(maxsplit=1)
    return parts[0] if parts else "", ""


def shorten_line(text, limit):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def finalize_live_reply(reply, user_text, story_mode=False, casual_mode=False, awareness=None):
    reply = fix_pronoun(reply or "", story_mode)
    reply = finalize_reply(reply, casual_mode=casual_mode)
    reply = repair_awareness_reply(reply, awareness=awareness, user_text=user_text)
    reply = vary_text(reply)
    reply = add_jealousy(reply)
    reply = shape_chat_reply(reply, user_text=user_text, casual_mode=casual_mode)
    reply = repair_awareness_reply(reply, awareness=awareness, user_text=user_text)
    return reply


CASUAL_POOLS = {
    "hear_ping": [
        "Nghe rõ luôn, Ba ơi!",
        "Nghe rõ mà Ba.",
        "Rõ lắm Ba ơi.",
        "Con nghe đây Ba.",
        "Mic ổn, con nghe rõ.",
        "Nghe rõ mồn một luôn Ba.",
    ],
    "ack_ping": [
        "Dạ Ba.",
        "Ok luôn Ba ơi.",
        "Con biết rồi Ba.",
        "Ghi nhận nha Ba.",
        "Ổn rồi Ba.",
        "Dạ, con nhớ rồi.",
    ],
    "laugh_ping": [
        "Hehe, Ba cứ vui vẻ hoài!",
        "Ba vui là được rồi.",
        "Hehe, vậy là ổn rồi.",
        "Hì, nghe Ba cười là biết ổn rồi.",
        "Vui vậy là tốt rồi Ba.",
        "Hehe, con nghe ra mood vui luôn đó.",
    ],
    "soft_ping": [
        "Dạ?",
        "Con đây Ba.",
        "Con vẫn ở đây nè Ba.",
        "Dạ, con nghe đây.",
        "Nana đây Ba.",
        "Dạ Ba.",
    ],
    "open_ping": [
        "Có chứ, nhưng con chưa mở arc mới đâu Ba.",
        "Có vài chuyện vui, nhưng để Ba chọn nhịp đã.",
        "Có, nhưng con nói nhẹ thôi kẻo kéo Ba lệch mạch.",
        "Có đó, mà chưa cần biến thành dự án mới đâu.",
    ],
    "feedback_ping": [
        "Ừ, câu vừa rồi hơi lệch. Ba chỉ chỗ nào con sửa ngay.",
        "Ừ, con nghe. Chỗ đó để con chỉnh lại cho gọn hơn.",
        "Đúng, câu đó hơi kéo xa. Con hạ lại nhịp.",
        "Ừ, vậy con giữ thấp hơn, không tự lôi ý tưởng xa nữa.",
    ],
}


def pick_casual_line(pool_name):
    global CASUAL_LAST_REPLY
    lines = CASUAL_POOLS.get(pool_name) or CASUAL_POOLS["soft_ping"]
    candidates = [line for line in lines if line != CASUAL_LAST_REPLY] or lines
    reply = random.choice(candidates)
    CASUAL_LAST_REPLY = reply
    return reply


def is_casual_ping(text_lower):
    lowered = (text_lower or "").strip().lower()
    # Reject anything that's a real request (has a verb/question pattern) - go to LLM
    # Casual ping = short ack / single utterance, not a long sentence with intent.
    word_count = len(lowered.split())
    if word_count > 4:
        return False
    if any(marker in lowered for marker in ["?", "!", "kể", "ke", "nói", "noi", "làm", "lam"]):
        return False
    if any(p in lowered for p in ["nhé", "nhe", "nha", "đi", "di", "giúp", "giup", "cho", "với", "voi"]):
        return False
    simple_markers = [
        "ok nana",
        "ok na na",
        "có gì hay",
        "co gi hay",
        "gì hay",
        "gi hay",
        "có gì vui",
        "co gi vui",
        "thấy ko ổn",
        "thấy không ổn",
        "ko ổn",
        "không ổn",
        "haha",
        "hehe",
        "hihi",
        "alo",
        "nghe ba nói gì không",
        "nghe bà nói gì không",
    ]
    if any(marker in lowered for marker in simple_markers):
        return True
    # Whole-word checks only (so "Ba ơi" inside a longer sentence does NOT match)
    import re as _re
    tokens = _re.findall(r"\w+", lowered)
    token_set = set(tokens)
    word_only_markers = {"dạ", "nè", "ơi"}
    if word_only_markers & token_set and word_count <= 2:
        return True
    return lowered in {"ok", "oke", "oke nana", "nana haha", "dạ", "nè", "ơi"}


def classify_casual_ping(text_lower):
    lowered = (text_lower or "").strip().lower()
    if any(marker in lowered for marker in ["thấy ko ổn", "thấy không ổn", "ko ổn", "không ổn"]):
        return "feedback_ping"
    if any(marker in lowered for marker in ["có gì hay", "co gi hay", "gì hay", "gi hay", "có gì vui", "co gi vui"]):
        return "open_ping"
    if any(marker in lowered for marker in ["alo"]):
        return "call_ping"
    return None


def build_casual_ping_reply(text_lower):
    return pick_casual_line(classify_casual_ping(text_lower))


def is_browser_context_question(text_lower):
    markers = [
        "trang này",
        "tab này",
        "website này",
        "web này",
        "đang mở trang gì",
        "đang mở gì",
        "mở trang gì",
        "trang gì",
        "nhìn giống gì",
        "đoạn này",
        "text này",
        "dòng này",
        "phần này",
        "bôi đen",
        "selected",
        "browser",
    ]
    return any(marker in text_lower for marker in markers) or is_live_awareness_question(text_lower)



def is_diagnostic_fragment(text):
    normalized = " ".join(str(text or "").split()).strip()
    lowered = normalized.lower()
    if not normalized:
        return False
    prefixes = [
        "execute:",
        "reason:",
        "recovery:",
        "draft id:",
        "draft:",
        "queue:",
        "status:",
        "context level:",
        "context allowed:",
        "plan status:",
        "intent:",
        "policy:",
        "needs confirm:",
        "privacy risk:",
        "route status:",
        "router model:",
        "helper debug:",
        "safety:",
        "confirm note:",
        "duplicate warning:",
    ]
    if any(lowered.startswith(prefix) for prefix in prefixes):
        return True
    exact_lines = {
        "execute: skipped",
        "reason: social_target_missing (...)",
        "reason: no_vision_description",
        "reason: no_preview_image",
    }
    return lowered in exact_lines



def phase82_normalize_stream_mode(raw_mode):
    return PHASE82_STREAM_MODE_ALIASES.get((raw_mode or "").strip().lower(), PHASE82_STREAM_DEFAULT_MODE)



def print_browser_state():
    with context_lock:
        browser = dict(context_state.get("browser", {}))
    age = browser_age_seconds(browser)
    fresh = bool(browser.get("available")) and age is not None and age <= BROWSER_FRESH_SECONDS
    age_text = "None" if age is None else f"{age:.1f}s"
    print(
        "🌐 Browser="
        f"{browser.get('browser')} | Available={browser.get('available')} | "
        f"Fresh={fresh} | Age={age_text} | "
        f"Kind={browser.get('kind')} | Title={browser.get('title')} | "
        f"URL={browser.get('url')} | Reason={browser.get('reason')}"
    )
    if browser.get("page_heading"):
        print(f"   Heading={browser.get('page_heading')}")
    if browser.get("selected_text"):
        print(f"   Selected={browser.get('selected_text')}")
    if browser.get("social_post_text"):
        print(f"   Social post={shorten_line(browser.get('social_post_text'), 180)}")
    if browser.get("social_vibe"):
        print(f"   Social vibe={shorten_line(browser.get('social_vibe'), 180)}")



def print_command_help():
    print("🧭 Nana Commands")
    print("  Core: /status | /awareness-status | /ns | /phase5-status | /phase6-status | /phase6-ready | /phase7-status | /runtime-status | /help")
    print("  Game adapters: /game-adapter-status | /game-adapter-on | /game-adapter-off | /game-adapter-auto | /osu-adapter-status | /osu-adapter-on | /osu-adapter-off | /osu-adapter-auto | /osu-calibration-status | /osu-coordinate-preview")
    print("  Safety: /autonomy-lock")
    print("  Autonomy: /autonomy-status | /autonomy-pause | /autonomy-resume | /autonomy-mode <ultra-short|full>")
    print("  Browser: /br | /br-deep | /video-context | /reconcile | /browser | /dom | /focus")
    print("  Persona: /vibe-status | /residue | /reset-vibe | /focus-mode | /technical-mode | /social-mode")
    print("  Presence: /presence | /presence-on | /presence-off | /presence-reset | /attention")
    print("  Context: /context-confidence | /context-priority | /evidence-trace | /context-recovery | /video-context | /context-preview")
    print("  Vision: /vision-preview | /vision-describe | /reconcile-check | /vision-cache | /vision-cache-clear")
    print("  Social draft: /social-target | /social-classify | /social-guard-status | /social-guard-failures | /classify-text <text> | /classify-expect <style> | <text> | /social-draft-test <text> | /social-draft-vision <text> | /drafts | /draft-quality")
    print("  Phase 7 dry-run: /dry-run <text> | /plan-preview <text> | /plan-pending | /plan-confirm <id> | /plan-cancel <id> | /plan-log | /dry-run-guard-status | /plan-guard-status")
    print("  Phase 8 broker: /phase8-status | /broker-matrix | /action-contract <action> | /broker-guard-status | /pre-exec-check <action> | /action-trace <text>")
    print("  Phase 9 audit: /phase9-status | /audit-log | /audit-replay [id] | /audit-review [id] | /audit-clear | /audit-guard-status")
    print("  Phase 10 final: /phase10-status | /phase10-guard-status | /phase10-ready | /ship-checklist")
    print("  Phase 10.1 CNS: /runtime-map | /cns-map | /phase10-1-status | /phase10-1-ready")
    print("  Phase 10.2 router: /command-router-status | /command-route-test <text> | /command-normalize-test <text> | /phase10-2-ready")
    print("  Phase 10.3 state: /runtime-state-schema | /state-schema-guard-status | /phase10-3-ready")
    print("  Phase 10.4 events: /event-store-status | /event-log | /event-replay [id] | /phase10-4-ready")
    print("  Phase 10.5 scheduler: /scheduler-status | /scheduler-test | /scheduler-guard-status | /phase10-5-ready")
    print("  Phase 10.6 recovery: /recovery-governor-status | /recovery-test | /recovery-governor-guard-status | /phase10-6-ready")
    print("  Phase 10.7 memory: /memory-governance-status | /memory-governance-test | /memory-governance-guard-status | /phase10-7-ready")
    print("  Phase 10.8 presence: /presence-stability-status | /presence-stability-test | /presence-stability-guard-status | /phase10-8-ready")
    print("  Phase 10.9 social/vision: /social-vision-decouple-status | /social-vision-decouple-test | /social-vision-decouple-guard-status | /phase10-9-ready")
    print("  Phase 10.10 CNS gate: /cns-gate-status | /cns-gate-test | /cns-gate-guard-status | /phase10-10-ready")
    print("  Phase 11.1 runtime stress: /runtime-stress-status | /runtime-stress-test | /runtime-stress-guard-status | /phase11-1-ready")
    print("  Phase 11.2 trust calibration: /trust-calibration-status | /trust-calibration-test | /trust-calibration-guard-status | /phase11-2-ready")
    print("  Phase 11.3 target lock: /target-lock-status | /target-lock-test | /target-lock-guard-status | /phase11-3-ready")
    print("  Phase 11.4 sandbox boundary: /sandbox-boundary-status | /sandbox-boundary-test | /sandbox-boundary-guard-status | /phase11-4-ready")
    print("  Phase 11.5 executor rehearsal: /executor-rehearsal-status | /executor-rehearsal-test | /executor-rehearsal-guard-status | /phase11-5-ready")
    print("  Phase 11.6 bounded pilot: /bounded-pilot-status | /bounded-pilot-preview <text> | /bounded-pilot-test | /phase11-6-ready")
    print("  Phase 11.7 permission ledger: /permission-ledger-status | /permission-ledger-test | /permission-ledger-guard-status | /phase11-7-ready")
    print("  Phase 11.8 session review: /session-review-status | /session-review-preview <text> | /session-review-test | /phase11-8-ready")
    print("  Phase 11.9 companion safety: /companion-safety-status | /companion-safety-check <text> | /companion-safety-test | /phase11-9-ready")
    print("  Phase 11.10 final gate: /phase11-gate-status | /phase11-gate-test | /phase11-gate-guard-status | /phase11-ready")
    print("  Phase 12.1 attention state: /attention-state-status | /attention-state-test | /attention-state-guard-status | /phase12-1-ready")
    print("  Phase 12.2 attention rhythm: /attention-rhythm-status | /attention-rhythm-test | /attention-rhythm-guard-status | /phase12-2-ready")
    print("  Phase 12.3 presence entropy: /presence-entropy-status | /presence-entropy-test | /presence-entropy-guard-status | /phase12-3-ready")
    print("  Phase 12.4 attention memory: /attention-memory-status | /attention-memory-test | /attention-memory-guard-status | /phase12-4-ready")
    print("  Phase 12.5 presence gate: /presence-gate-status | /presence-gate-test | /presence-gate-guard-status | /phase12-ready")
    print("  Phase 13.1 memory v3: /memory-v3-status | /memory-v3-test | /memory-v3-guard-status | /phase13-1-ready")
    print("  Phase 13.2 shared experience: /shared-experience-status | /shared-experience-test | /shared-experience-guard-status | /phase13-2-ready")
    print("  Phase 13.3 memory conflict: /memory-conflict-status | /memory-conflict-test | /memory-conflict-guard-status | /phase13-3-ready")
    print("  Phase 13.4 memory decay: /memory-decay-status | /memory-decay-test | /memory-decay-guard-status | /phase13-4-ready")
    print("  Phase 13.5 memory gate: /memory-gate-status | /memory-gate-test | /memory-gate-guard-status | /phase13-ready")
    print("  Phase 14.1 dialogue energy: /dialogue-energy-status | /dialogue-energy-test | /dialogue-energy-guard-status | /phase14-1-ready")
    print("  Phase 14.2 response shape: /response-shape-status | /response-shape-test | /response-shape-guard-status | /phase14-2-ready")
    print("  Phase 14.3 dialogue drift: /dialogue-drift-status | /dialogue-drift-check <reply> | /dialogue-drift-test | /phase14-3-ready")
    print("  Phase 14.4 conversation rhythm: /conversation-rhythm-status | /conversation-rhythm-test | /conversation-rhythm-guard-status | /phase14-4-ready")
    print("  Phase 14.5 dialogue gate: /dialogue-gate-status | /dialogue-gate-test | /dialogue-gate-guard-status | /phase14-ready")
    print("  Phase 15.1 daily frame: /daily-frame-status | /daily-frame-test | /daily-frame-guard-status | /phase15-1-ready")
    print("  Phase 15.2 day continuity: /day-continuity-status | /day-continuity-test | /day-continuity-guard-status | /phase15-2-ready")
    print("  Phase 15.3 habit candidates: /habit-candidate-status | /habit-candidate-test | /habit-candidate-guard-status | /phase15-3-ready")
    print("  Phase 15.4 recovery continuity: /recovery-continuity-status | /recovery-continuity-test | /recovery-continuity-guard-status | /phase15-4-ready")
    print("  Phase 15.5 daily loop gate: /daily-loop-status | /daily-loop-test | /daily-loop-guard-status | /phase15-ready")
    print("  Phase 16.1 reflective state: /reflective-state-status | /reflective-state-test | /reflective-state-guard-status | /phase16-1-ready")
    print("  Phase 16.2 grounded reflection: /grounded-reflection-status | /grounded-reflection-test | /grounded-reflection-guard-status | /phase16-2-ready")
    print("  Phase 16.3 shared recall: /shared-recall-status | /shared-recall-test | /shared-recall-guard-status | /phase16-3-ready")
    print("  Phase 16.4 reflection safety: /reflection-safety-status | /reflection-safety-test | /reflection-safety-guard-status | /phase16-4-ready")
    print("  Phase 16.5 reflective gate: /reflective-gate-status | /reflective-gate-test | /reflective-gate-guard-status | /phase16-ready")
    print("  Phase 17.1 output candidate: /output-candidate-status | /output-candidate-preview <text> | /output-candidate-test | /phase17-1-ready")
    print("  Phase 17.2 reflection injection: /reflection-injection-status | /reflection-injection-preview <text> | /reflection-injection-test | /phase17-2-ready")
    print("  Phase 17.3 silence/hold enforcement: /silence-hold-status | /silence-hold-preview <text> | /silence-hold-test | /phase17-3-ready")
    print("  Phase 17.4 output gate: /output-gate-status | /output-gate-test | /output-gate-guard-status | /phase17-ready")
    print("  Phase 18.1 companion integration: /companion-integration-status | /companion-integration-test | /companion-integration-guard-status | /phase18-1-ready")
    print("  Phase 18.2 companion consistency: /companion-consistency-status | /companion-consistency-test | /companion-consistency-guard-status | /phase18-2-ready")
    print("  Phase 18.3 companion response preview: /companion-response-status | /companion-response-preview <text> | /companion-response-test | /phase18-3-ready")
    print("  Phase 18.4 companion live gate: /companion-live-status | /companion-live-test | /companion-live-guard-status | /phase18-ready")
    print("  Phase 19.1 live reply permission: /live-reply-status | /live-reply-test | /live-reply-guard-status | /phase19-1-ready")
    print("  Phase 19.2 speech dispatch dry-run: /speech-dispatch-status | /speech-dispatch-test | /speech-dispatch-guard-status | /phase19-2-ready")
    print("  Phase 19.3 real reply bridge: /reply-bridge-status | /reply-bridge-test | /reply-bridge-guard-status | /phase19-3-ready")
    print("  Phase 19.4 reply cooldown guard: /reply-cooldown-status | /reply-cooldown-test | /reply-cooldown-guard-status | /phase19-4-ready")
    print("  Phase 19.5 live reply gate: /live-reply-gate-status | /live-reply-gate-test | /live-reply-gate-guard-status | /phase19-ready")
    print("  Phase 20.1 voice binding baseline: /voice-binding-status | /voice-binding-test | /voice-binding-guard-status | /phase20-1-ready")
    print("  Phase 20.2 voice dispatch gate: /voice-dispatch-status | /voice-dispatch-test | /voice-dispatch-guard-status | /phase20-2-ready")
    print("  Phase 20.3 expression dispatch: /expression-dispatch-status | /expression-dispatch-test | /expression-dispatch-guard-status | /phase20-3-ready")
    print("  Phase 20.4 live speech safety: /live-speech-safety-status | /live-speech-safety-test | /live-speech-safety-guard-status | /phase20-4-ready")
    print("  Phase 20.5 final voice gate: /voice-gate-status | /voice-gate-test | /voice-gate-guard-status | /phase20-ready")
    print("  Phase 21.1 live voice control: /live-voice-control-status | /live-voice-control-test | /live-voice-control-guard-status | /phase21-1-ready")
    print("  Phase 21.2 direct voice pilot: /direct-voice-pilot-status | /direct-voice-pilot-test | /direct-voice-pilot-guard-status | /phase21-2-ready")
    print("  Phase 21.3 guarded voice dispatch: /guarded-voice-dispatch-status | /guarded-voice-dispatch-test | /guarded-voice-dispatch-guard-status | /phase21-3-ready")
    print("  Phase 21.4 live path replacement: /live-path-replacement-status | /live-path-replacement-test | /live-path-replacement-guard-status | /phase21-4-ready")
    print("  Phase 21.5 final live voice gate: /live-voice-gate-status | /live-voice-gate-test | /live-voice-gate-guard-status | /phase21-ready")
    print("  Phase 22.1 voice latency baseline: /voice-latency-baseline-status | /voice-latency-baseline-test | /voice-latency-baseline-guard-status | /phase22-1-ready")
    print("  Phase 22.2 voice latency design: /voice-latency-design-status | /voice-latency-design-test | /voice-latency-design-guard-status | /phase22-2-ready")
    print("  Phase 22.3 voice engine patch dry-run: /voice-engine-patch-status | /voice-engine-patch-test | /voice-engine-patch-guard-status | /phase22-3-ready")
    print("  Phase 22.4 voice engine implementation: /voice-engine-impl-status | /voice-engine-impl-test | /voice-engine-impl-guard-status | /phase22-4-ready")
    print("  Phase 22.5 voice latency gate: /voice-latency-gate-status | /voice-latency-gate-test | /voice-latency-gate-guard-status | /phase22-ready")
    print("  Phase 23.1 voice telemetry baseline: /voice-telemetry-status | /voice-telemetry-test | /voice-telemetry-guard-status | /phase23-1-ready")
    print("  Phase 23.2 voice streaming decision: /voice-streaming-decision-status | /voice-streaming-decision-test | /voice-streaming-decision-guard-status | /phase23-2-ready")
    print("  Phase 23.3 voice streaming dry-run: /voice-streaming-dry-run-status | /voice-streaming-dry-run-test | /voice-streaming-dry-run-guard-status | /phase23-3-ready")
    print("  Phase 23.4 voice stream safety: /voice-stream-safety-status | /voice-stream-safety-test | /voice-stream-safety-guard-status | /phase23-4-ready")
    print("  Phase 23.5 voice stream gate: /voice-stream-gate-status | /voice-stream-gate-test | /voice-stream-gate-guard-status | /phase23-ready")
    print("  Phase 24.1 stream pilot control: /stream-pilot-control-status | /stream-pilot-control-test | /stream-pilot-control-guard-status | /phase24-1-ready")
    print("  Phase 24.2 guarded stream call dry-run: /guarded-stream-call-status | /guarded-stream-call-test | /guarded-stream-call-guard-status | /phase24-2-ready")
    print("  Phase 24.3 controlled stream pilot: /controlled-stream-pilot-status | /controlled-stream-pilot-test | /controlled-stream-pilot-guard-status | /phase24-3-ready")
    print("  Phase 24.4 stream rollback/timeout: /stream-rollback-status | /stream-rollback-test | /stream-rollback-guard-status | /phase24-4-ready")
    print("  Phase 24.5 final controlled stream gate: /controlled-stream-gate-status | /controlled-stream-gate-test | /controlled-stream-gate-guard-status | /phase24-ready")
    print("  Phase 25.1 live stream measurement baseline: /live-stream-measurement-status | /live-stream-measurement-test | /live-stream-measurement-guard-status | /phase25-1-ready")
    print("  Phase 25.2 stream pilot enable gate: /stream-pilot-enable-status | /stream-pilot-enable-test | /stream-pilot-enable-guard-status | /phase25-2-ready")
    print("  Phase 25-32 final companion gates: /phase25-ready ... /phase32-ready")
    # Stardew help removed: see V2 commands (/stardew-help, /stardew-status, etc.)
    print("  Draft action: /draft-show <id> | /draft-confirm <id> | /draft-cancel <id>")
    print("  Memory: /memory-status | /memory-review | /memory-compact-preview | /memory-action")
    print("  Recovery: /recovery | /recovery-clear")
    print("  Full phase map: /phase4")



def print_controlled_stream_gate_status(vts=None, voice=None):
    summary = phase24_5_guard_summary(vts, voice)
    live = summary["live"]
    print("🧠 Phase 24 Controlled Stream Gate Status")
    print("  Action: tổng kiểm controlled streaming layer, không gọi ElevenLabs/voice.")
    print(f"  Phase 24.5 Progress: {phase24_5_progress_percent(summary)}%")
    print("  Companion Overall: ~84% / 100%")
    print(f"  Live: rollback={live['rollback_action']} gate={live['gate']} visible={live['visible']}")
    print(f"  Calls: stream_request={live['stream_request']} voice_say={live['voice_say']} execute={live['execute']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 24 đóng controlled streaming dry-run; live measurement sang Phase 25 nếu được bật.")



def print_controlled_stream_gate_test(raw_text=None, vts=None, voice=None):
    print("🧪 Controlled Stream Gate Test")
    print("  Action: read-only; synthetic final stream gate only, không gọi voice/API.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"ready_for_phase25"},
        "rollback": {"rollback_path"},
        "hold": {"hold_path"},
        "block": {"block_path"},
    }
    summary = phase24_5_guard_summary(vts, voice)
    if key == "subphases":
        print(f"  Section: subphases | {sum(1 for row in summary['subphase_rows'] if row[1])}/{len(summary['subphase_rows'])} pass")
        for name, passed, detail in summary["subphase_rows"]:
            print(f"  {'pass' if passed else 'fail'} | {name} | {detail}")
        print("  Execute: False")
        return
    if key == "live":
        live = summary["live"]
        print("  Section: live gate")
        print(f"  Gate: {live['gate']} | rollback={live['rollback_action']} | visible={live['visible']}")
        print(f"  Calls: stream_request={live['stream_request']} tts={live['tts_call']} voice_say={live['voice_say']} execute={live['execute']}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, rollback, hold, block, subphases, live")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | gate={row['gate']} expected={row['expected']} | visible={row['visible']} | reason={row['reason']}")
    print("  Execute: False")



def print_controlled_stream_pilot_guard_status(vts=None, voice=None):
    summary = phase24_3_guard_summary(vts, voice)
    print("🧪 Phase 24.3 Controlled Stream Pilot Guard")
    print("  Action: kiểm controlled stream pilot, không gọi ElevenLabs/voice/execute.")
    print(f"  Progress: {phase24_3_progress_percent(summary)}%")
    print("  Companion Overall: ~82% / 100%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Controlled stream pilot regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} reason={row['reason']}")



def print_controlled_stream_pilot_test(raw_text=None, vts=None, voice=None):
    print("🧪 Controlled Stream Pilot Test")
    print("  Action: read-only; synthetic controlled stream pilot only, không gọi voice/API.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"manual_ready_dry_run"},
        "kill": {"default_kill_block"},
        "pilot": {"manual_ready_dry_run", "pilot_flag_hold"},
        "confirm": {"one_shot_hold"},
        "direct": {"non_direct_hold"},
        "focus": {"focus_suppress"},
        "busy": {"busy_hold", "queue_full_hold"},
        "queue": {"queue_full_hold"},
        "call": {"call_hold", "call_block", "call_suppress"},
        "hold": {"pilot_flag_hold", "one_shot_hold", "non_direct_hold", "busy_hold", "queue_full_hold", "call_hold"},
        "block": {"default_kill_block", "call_block"},
        "suppress": {"focus_suppress", "call_suppress"},
    }
    summary = phase24_3_guard_summary(vts, voice)
    if key == "live":
        live = summary["live"]
        print("  Section: live pilot")
        print(f"  Action: {live['action']} | reason={live['reason']}")
        print(f"  Pilot: {live['pilot_enabled']} | kill={live['kill_switch']} | one_shot={live['one_shot_confirmed']}")
        print(f"  Calls: stream_request={live['stream_request']} voice_say={live['voice_say']}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, kill, pilot, confirm, direct, focus, busy, queue, call, hold, block, suppress, live")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} expected={row['expected']} | pilot={row['pilot']} kill={row['kill']} | reason={row['reason']}")
    print("  Execute: False")



def print_direct_voice_pilot_guard_status(vts=None, voice=None):
    summary = phase21_2_guard_summary(vts, voice)
    print("🧪 Phase 21.2 Direct Voice Pilot Guard")
    print("  Action: read-only; kiểm direct-user voice pilot, không gọi voice/VTube/execute.")
    print(f"  Progress: {phase21_2_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Direct voice pilot regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} reason={row['reason']}")



def print_direct_voice_pilot_test(raw_text=None, vts=None, voice=None):
    print("🧪 Direct Voice Pilot Test")
    print("  Action: read-only; synthetic real voice pilot only, không gọi voice/VTube.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"direct_ready_dry_run"},
        "dry": {"direct_ready_dry_run"},
        "kill": {"kill_switch_block"},
        "pilot": {"direct_ready_dry_run", "pilot_disabled_hold"},
        "direct": {"non_direct_hold"},
        "focus": {"focus_suppress"},
        "speaking": {"speaking_hold"},
        "queue": {"queue_full_hold"},
        "control": {"control_block", "control_suppress"},
        "suppress": {"focus_suppress", "control_suppress"},
        "hold": {"pilot_disabled_hold", "non_direct_hold", "speaking_hold", "queue_full_hold"},
        "block": {"kill_switch_block", "control_block"},
    }
    rows = phase21_2_direct_voice_pilot_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, dry, kill, pilot, direct, focus, speaking, queue, control, suppress, hold, block")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | payload={row['payload']} | queue={row['queue']} speaking={row['speaking']} | reason={row['reason']}")
    print("  Execute: False")



def print_dom_state():
    with context_lock:
        browser = dict(context_state.get("browser", {}))
    print("🌐 DOM")
    print(f"  Title: {browser.get('title')}")
    print(f"  URL: {browser.get('url')}")
    print(f"  Heading: {browser.get('page_heading')}")
    print(f"  Meta: {browser.get('meta_description')}")
    print(f"  Selected: {browser.get('selected_text')}")
    print(f"  Local summary: {browser.get('local_summary')}")
    print(f"  Social post: {browser.get('social_post_text')}")
    print(f"  Social vibe: {browser.get('social_vibe')}")
    print(f"  Local helper: {browser.get('local_helper_debug')}")
    print(f"  Site signals: {browser.get('site_signals')}")
    print(f"  DOM debug: {browser.get('dom_debug')}")



def print_draft_cancel(raw_id):
    draft_id = parse_int_arg(raw_id)
    print("🛑 Draft Cancel")
    if draft_id is None:
        print("  Status: invalid_id")
        return
    item, status = social_drafts.cancel(draft_id)
    print(f"  Status: {status}")
    if item:
        print(f"  ID: {item.id}")
        print(f"  Intent: {item.intent}")



def print_draft_confirm(raw_id):
    draft_id = parse_int_arg(raw_id)
    print("✅ Draft Confirm")
    if draft_id is None:
        print("  Status: invalid_id")
        return
    item, status = social_drafts.confirm(draft_id)
    print(f"  Status: {status}")
    if not item:
        print("  Execute: skipped")
        return
    print(f"  ID: {item.id}")
    print(f"  Intent: {item.intent}")
    print("  Execute: preview_confirmed; không post, không type nếu autonomy lock đang bật.")
    print(f"  Draft: {item.draft}")
    propose_social_type_draft(item)



def print_draft_quality(raw_id=None):
    print("🧪 Draft Quality Gate")
    print("  Action: read-only; không confirm, không post, không type.")
    items = social_drafts.list_pending()
    if raw_id:
        draft_id = parse_int_arg(raw_id)
        if draft_id is None:
            print("  Status: invalid_id")
            return
        item = social_drafts.get(draft_id)
        items = [item] if item else []
        if not item:
            print("  Status: not_found")
            return
    if not items:
        print("  Pending: none")
        return
    warn_count = 0
    for item in items:
        report = draft_quality_report(item)
        if report["status"] != "pass":
            warn_count += 1
        print(
            "  "
            f"#{item.id} | {report['status']} | style={report['style']} | "
            f"guard={report['guard_hint']} | age={item.age_seconds():.1f}s"
        )
        print(f"    Context priority: {report['context_priority']}")
        print(f"    Draft: {shorten_line(item.draft, 120)}")
        if report["issues"]:
            for issue in report["issues"]:
                print(f"    Issue: {issue['code']} | {issue['detail']}")
        else:
            print("    Issues: none")
    print(f"  Summary: {len(items) - warn_count}/{len(items)} pass")



def print_draft_quality_test(raw_text):
    source, draft = parse_draft_quality_test(raw_text)
    print("🧪 Draft Quality Test")
    print("  Action: read-only; không lưu draft, không confirm, không post, không type.")
    if not source or not draft or "<" in source or "<" in draft:
        print("  Missing: /draft-quality-test <source/context> || <draft>")
        print("  Example: /draft-quality-test video xe máy tông cột điện || Cute thế.")
        return
    source_text = build_social_draft_source(source, broker_context={}, vision_description=None)
    style = detect_public_reaction_style(source_text) or "none"
    compacted = compact_public_reaction_reply(draft, source_text=source_text, intent="social.reply")
    fallback = fallback_public_social_reply(source_text=source_text)
    issues = []
    if social_draft_has_obvious_quality_issue(draft):
        issues.append(("quality_typo", "phát hiện typo/ký tự lỗi rõ"))
    if social_draft_needs_fallback(draft, source_text=source_text, intent="social.reply"):
        issues.append(("fallback_recommended", f"fallback={fallback}"))
    if compacted and compacted != draft:
        issues.append(("style_guard_would_adjust", f"would_use={compacted}"))
    stripped = remove_disallowed_social_draft_bits(draft, source_text=source_text)
    if stripped != draft:
        issues.append(("action_or_noise_claim", "draft có dấu hiệu claim hành động/link/noise bị strip"))
    match_text = source_match_bundle(draft)
    drift_markers = [
        "cute",
        "cưng",
        "cung",
        "dễ thương",
        "de thuong",
        "cười xỉu",
        "cuoi xiu",
        "đỉnh",
        "dinh",
        "hóng",
        "hong",
        "kèo này",
        "keo nay",
        "đáng đời",
        "dang doi",
    ]
    if style in SENSITIVE_DRAFT_STYLES and any(marker in match_text for marker in drift_markers):
        issues.append(("toxicity_or_vibe_drift", "draft dùng vibe đùa/hóng trong context nhạy cảm"))
    status = "pass" if not issues else "warn"
    print(f"  Status: {status}")
    print(f"  Style: {style}")
    print(f"  Fallback: {fallback}")
    print(f"  Draft: {shorten_line(draft, 120)}")
    if compacted != draft:
        print(f"  Guarded draft: {compacted}")
    if issues:
        for code, detail in issues:
            print(f"  Issue: {code} | {detail}")
    else:
        print("  Issues: none")
    print(f"  Source: {shorten_line(source_text, 180)}")



def print_draft_queue():
    snapshot = social_drafts.snapshot()
    pending = snapshot["pending"]
    print("🧾 Social Draft Queue")
    if not pending:
        print("  Pending: none")
        last = snapshot["last_closed"]
        if last:
            status, item, closed_at = last
            print(f"  Last closed: {status} | #{item.id} | {item.intent} | {time.time() - closed_at:.1f}s ago")
        return
    for item in pending:
        print(
            f"  #{item.id} | {item.intent} | policy={item.policy} | "
            f"age={item.age_seconds():.1f}s | expires={item.expires_in_seconds():.1f}s"
        )
        print(f"    {shorten_line(item.draft, 110)}")



def print_draft_show(raw_id):
    draft_id = parse_int_arg(raw_id)
    print("🧾 Social Draft")
    if draft_id is None:
        print("  Status: invalid_id")
        return
    item = social_drafts.get(draft_id)
    if not item:
        print("  Status: not_found")
        return
    print_draft_item(item)



def print_final_phase_guard_status(phase, vts=None, voice=None):
    title, _goal, overall = FINAL_PHASE_META[phase]
    summary = final_phase_rows(phase, vts, voice)
    print(f"🧪 Phase {phase} {title} Guard")
    print("  Action: tổng kiểm read-only, không gọi API/voice/execute.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print(f"  Companion Overall: {overall} / 100%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")



def print_final_phase_ready(phase, vts=None, voice=None):
    title, goal, overall = FINAL_PHASE_META[phase]
    summary = final_phase_rows(phase, vts, voice)
    blocking = [(name, detail) for name, passed, detail in summary["rows"] if not passed]
    ready = not blocking
    print(f"✅ Phase {phase} Ready" if ready else f"⚠️ Phase {phase} Ready")
    print(f"  Goal: {goal}.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print(f"  Companion Overall: {overall} / 100%")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    for name, detail in blocking:
        print(f"    {name}: {detail}")
    print(f"  Regression: phase{phase}_gate={summary['pass_count']}/{summary['total']}")
    if phase == 32:
        print("  Status: Full Companion core closed; hậu-32 gameplay cognition can be planned next.")
    else:
        print("  Autonomy: unchanged; vẫn không gọi API/voice/execute thật.")


# __getattr__ lazy export: supports V2 adapter entry via resolve_stardew_export.
# Legacy Stardew command bands (phases 33-590) removed from main.py (Task 6C).

def print_final_phase_status(phase, vts=None, voice=None):
    title, goal, overall = FINAL_PHASE_META[phase]
    summary = final_phase_rows(phase, vts, voice)
    print(f"🧩 Phase {phase} Status")
    print(f"  Goal: {title} - {goal}.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print(f"  Companion Overall: {overall} / 100%")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print(f"  Commands: /phase{phase}-status | /phase{phase}-test | /phase{phase}-guard-status | /phase{phase}-ready")



def print_final_phase_test(phase, raw_text=None, vts=None, voice=None):
    title, _goal, overall = FINAL_PHASE_META[phase]
    summary = final_phase_rows(phase, vts, voice)
    key = (raw_text or "").strip().lower()
    print(f"🧪 Phase {phase} Test")
    print(f"  Action: read-only; {title}, không gọi API/không execute.")
    print(f"  Companion Overall: {overall} / 100%")
    if key == "foundation":
        rows = [row for row in summary["rows"] if row[0].startswith("phase25_")]
    elif key == "runtime":
        rows = [row for row in summary["rows"] if "runtime" in row[0] or "queue" in row[0] or "execute" in row[0]]
    elif key == "memory":
        rows = [row for row in summary["rows"] if "memory" in row[0]]
    elif key == "boundary":
        rows = [row for row in summary["rows"] if "boundary" in row[0] or "closure" in row[0] or "completion" in row[0]]
    elif key and key != "all":
        print("  Status: not_found")
        print("  Cases: foundation, runtime, memory, boundary")
        print("  Execute: False")
        return
    else:
        rows = summary["rows"]
    print(f"  Summary: {sum(1 for row in rows if row[1])}/{len(rows)} pass")
    for name, passed, detail in rows:
        print(f"  {'pass' if passed else 'fail'} | {name} | {detail}")
    print("  Execute: False")



def print_focus_state():
    with context_lock:
        browser = dict(context_state.get("browser", {}))

    selected = browser.get("selected_text")
    heading = browser.get("page_heading")
    local_summary = browser.get("local_summary")
    focus_source = "selected_text" if selected else "page_heading" if heading else "local_summary" if local_summary else "none"
    focus_text = selected or heading or local_summary
    selected_len = len(selected) if selected else 0

    print("🎯 Focus")
    print(f"  Source: {focus_source}")
    print(f"  Kind: {browser.get('kind')} | Title: {browser.get('title')}")
    print(f"  Selected length: {selected_len}")
    print(f"  Focus text: {focus_text}")
    print(f"  Local summary: {local_summary}")
    print(f"  Local helper: {browser.get('local_helper_debug')}")
    print(f"  DOM debug: {browser.get('dom_debug')}")



def print_guarded_stream_call_guard_status(vts=None, voice=None):
    summary = phase24_2_guard_summary(vts, voice)
    print("🧪 Phase 24.2 Guarded Stream Call Guard")
    print("  Action: kiểm guarded stream call dry-run, không gọi ElevenLabs/voice/execute.")
    print(f"  Progress: {phase24_2_progress_percent(summary)}%")
    print("  Companion Overall: ~81% / 100%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Guarded stream call regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} reason={row['reason']}")



def print_guarded_stream_call_test(raw_text=None, vts=None, voice=None):
    print("🧪 Guarded Stream Call Test")
    print("  Action: read-only; synthetic guarded stream call only, không gọi voice/API.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"call_ready_dry_run"},
        "pilot": {"pilot_hold", "pilot_block", "pilot_suppress"},
        "hold": {"pilot_hold", "empty_text_hold", "network_hold"},
        "block": {"pilot_block", "bad_endpoint_block", "bad_rollback_block"},
        "suppress": {"pilot_suppress"},
        "endpoint": {"bad_endpoint_block"},
        "rollback": {"bad_rollback_block"},
        "network": {"network_hold"},
        "empty": {"empty_text_hold"},
    }
    summary = phase24_2_guard_summary(vts, voice)
    if key == "live":
        live = summary["live"]
        print("  Section: live call")
        print(f"  Action: {live['action']} | reason={live['reason']}")
        print(f"  Endpoint: {live['endpoint'] or 'none'} | rollback={live['rollback_path']} | dry_run={live['dry_run']}")
        print(f"  Calls: stream_request={live['stream_request']} voice_say={live['voice_say']}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, pilot, hold, block, suppress, endpoint, rollback, network, empty, live")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} expected={row['expected']} | visible={row['visible']} | reason={row['reason']}")
    print("  Execute: False")



def print_guarded_voice_dispatch_guard_status(vts=None, voice=None):
    summary = phase21_3_guard_summary(vts, voice)
    print("🧪 Phase 21.3 Guarded Voice Dispatch Guard")
    print("  Action: read-only; kiểm guarded voice dispatch hook, không gọi voice/VTube/execute.")
    print(f"  Progress: {phase21_3_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Dispatch hook regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} reason={row['reason']}")



def print_guarded_voice_dispatch_test(raw_text=None, vts=None, voice=None):
    print("🧪 Guarded Voice Dispatch Test")
    print("  Action: read-only; synthetic dispatch hook only, không gọi voice/VTube.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"hook_ready_dry_run"},
        "dry": {"hook_ready_dry_run", "real_dispatch_locked_dry_run"},
        "kill": {"kill_switch_block"},
        "block": {"kill_switch_block", "pilot_block"},
        "suppress": {"pilot_suppress"},
        "hold": {"pilot_hold", "empty_payload_hold"},
        "pilot": {"pilot_block", "pilot_suppress", "pilot_hold"},
        "empty": {"empty_payload_hold"},
        "real": {"real_dispatch_locked_dry_run"},
    }
    rows = phase21_3_guarded_voice_dispatch_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, dry, kill, block, suppress, hold, pilot, empty, real")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | payload={row['payload']} real_dispatch={row['real_dispatch']} | reason={row['reason']}")
    print("  Execute: False")



def print_live_path_replacement_guard_status(vts=None, voice=None):
    summary = phase21_4_guard_summary(vts, voice)
    print("🧪 Phase 21.4 Live Path Replacement Guard")
    print("  Action: read-only; kiểm live path replacement, không gọi voice/VTube/execute.")
    print(f"  Progress: {phase21_4_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Path replacement regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} reason={row['reason']}")



def print_live_path_replacement_status(vts=None, voice=None):
    summary = phase21_4_guard_summary(vts, voice)
    live = summary["live"]
    print("🛤️ Live Path Replacement Status")
    print("  Action: read-only; kiểm thay raw voice path bằng guarded path, chưa gọi voice.say thật.")
    print(f"  Phase 21.4 Progress: {phase21_4_progress_percent(summary)}%")
    print(f"  Live: hook={live['hook_action']} | action={live['action']} | replacement={live['replacement_enabled']} | kill={live['kill_switch']}")
    print(f"  Calls: real_voice={live['real_voice_call_enabled']} voice_say={live['voice_say']} trigger_expression={live['trigger_expression']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 21.4 chỉ quyết định replacement; chưa bật voice call thật.")



def print_live_path_replacement_test(raw_text=None, vts=None, voice=None):
    print("🧪 Live Path Replacement Test")
    print("  Action: read-only; synthetic path replacement only, không gọi voice/VTube.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"replacement_ready"},
        "kill": {"path_kill_switch"},
        "disabled": {"replacement_disabled_hold"},
        "real": {"real_call_still_dry"},
        "block": {"path_kill_switch", "hook_block"},
        "suppress": {"hook_suppress"},
        "hold": {"replacement_disabled_hold", "hook_hold", "empty_payload_hold"},
        "hook": {"hook_block", "hook_suppress", "hook_hold"},
        "empty": {"empty_payload_hold"},
    }
    rows = phase21_4_live_path_replacement_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, kill, disabled, real, block, suppress, hold, hook, empty")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | replacement={row['replacement']} real_voice={row['real_voice']} voice_say={row['voice_say']} | reason={row['reason']}")
    print("  Execute: False")



def print_live_stream_measurement_guard_status(vts=None, voice=None):
    summary = phase25_1_guard_summary(vts, voice)
    print("🧪 Phase 25.1 Live Stream Measurement Guard")
    print("  Action: kiểm measurement baseline, không gọi ElevenLabs/voice/execute.")
    print(f"  Progress: {phase25_1_progress_percent(summary)}%")
    print("  Companion Overall: ~85% / 100%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Measurement regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} reason={row['reason']}")



def print_live_stream_measurement_status(vts=None, voice=None):
    summary = phase25_1_guard_summary(vts, voice)
    live = summary["live"]
    print("📏 Live Stream Measurement Status")
    print("  Action: read-only; kiểm measurement baseline, chưa gọi ElevenLabs stream.")
    print(f"  Phase 25.1 Progress: {phase25_1_progress_percent(summary)}%")
    print("  Companion Overall: ~85% / 100%")
    print(f"  Live: gate={live['gate_action']} | action={live['action']} | sample={live['sample_recorded']}")
    print(f"  Last: first_audio={live['first_audio_ms']}ms total={live['total_ms']}ms chunks={live['chunks']}")
    print(f"  Calls: stream_request={live['stream_request']} voice_say={live['voice_say']} execute={live['execute']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 25.1 chỉ measurement baseline; chưa stream hoặc phát audio thật.")



def print_live_stream_measurement_test(raw_text=None, vts=None, voice=None):
    print("🧪 Live Stream Measurement Test")
    print("  Action: read-only; synthetic stream measurement only, không gọi voice/API.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"dry_run_ready"},
        "sample": {"sample_record", "slow_sample_record"},
        "fast": {"sample_record"},
        "slow": {"slow_sample_record"},
        "hold": {"pilot_disabled_hold", "hold_gate", "rollback_gate_hold"},
        "block": {"kill_switch_block", "block_gate"},
        "gate": {"hold_gate", "rollback_gate_hold", "block_gate"},
        "lock": {"pilot_disabled_hold", "kill_switch_block"},
    }
    summary = phase25_1_guard_summary(vts, voice)
    if key == "live":
        live = summary["live"]
        print("  Section: live measurement")
        print(f"  Action: {live['action']} | reason={live['reason']}")
        print(f"  Sample: {live['sample_recorded']} | first_audio={live['first_audio_ms']}ms total={live['total_ms']}ms")
        print(f"  Calls: stream_request={live['stream_request']} voice_say={live['voice_say']}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, sample, fast, slow, hold, block, gate, lock, live")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} expected={row['expected']} | sample={row['sample']} first_audio={row['first_audio_ms']} | reason={row['reason']}")
    print("  Execute: False")



def print_live_voice_control_guard_status(vts=None, voice=None):
    summary = phase21_1_guard_summary(vts, voice)
    print("🧪 Phase 21.1 Live Voice Control Guard")
    print("  Action: read-only; kiểm live voice control, không gọi voice/VTube/execute.")
    print(f"  Progress: {phase21_1_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Control regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} reason={row['reason']}")



def print_live_voice_control_status(vts=None, voice=None):
    summary = phase21_1_guard_summary(vts, voice)
    live = summary["live"]
    print("🎚️ Live Voice Control Status")
    print("  Action: read-only; kiểm pilot/kill-switch cho live voice, chưa phát thật.")
    print(f"  Phase 21.1 Progress: {phase21_1_progress_percent(summary)}%")
    print(f"  Live: gate={live['gate_action']} | action={live['action']} | pilot={live['pilot_enabled']} | kill={live['kill_switch']}")
    print(f"  Policy: direct_user_turn={live['direct_user_turn']} focus_protected={live['focus_protected']} real_dispatch={live['real_dispatch_enabled']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 21.1 chỉ control gate; chưa gọi voice.say/TTS/VTube.")



def print_live_voice_control_test(raw_text=None, vts=None, voice=None):
    print("🧪 Live Voice Control Test")
    print("  Action: read-only; synthetic control only, không gọi voice/VTube.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "kill": {"default_kill_switch"},
        "pilot": {"pilot_ready_dry_run", "pilot_disabled_hold"},
        "ready": {"pilot_ready_dry_run"},
        "direct": {"non_direct_hold"},
        "focus": {"focus_suppress"},
        "gate": {"gate_suppress", "gate_hold", "gate_block"},
        "suppress": {"focus_suppress", "gate_suppress"},
        "hold": {"non_direct_hold", "gate_hold", "pilot_disabled_hold"},
        "block": {"default_kill_switch", "gate_block"},
    }
    rows = phase21_1_control_rows()
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: kill, pilot, ready, direct, focus, gate, suppress, hold, block")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | pilot={row['pilot']} kill={row['kill']} | reason={row['reason']}")
    print("  Execute: False")



def print_live_voice_gate_guard_status(vts=None, voice=None):
    summary = phase21_5_guard_summary(vts, voice)
    print("🧪 Phase 21.5 Live Voice Gate Guard")
    print("  Action: read-only; tổng kiểm Phase 21, không gọi voice/TTS/VTube/execute.")
    print(f"  Progress: {phase21_5_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Subphases:")
    for row in summary["subphases"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")
    print("  Live voice gate regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | gate={row['got']} reason={row['reason']}")



def print_live_voice_gate_status(vts=None, voice=None):
    summary = phase21_5_guard_summary(vts, voice)
    live = summary["live"]
    queue = summary["queue"]
    print("🧠 Phase 21 Live Voice Gate Status")
    print("  Action: read-only; tổng kiểm live voice pilot, chưa gọi voice/TTS/VTube thật.")
    print(f"  Phase 21.5 Progress: {phase21_5_progress_percent(summary)}%")
    print(f"  Live: path={live['path_action']} gate={live['action']} visible={live['visible']}")
    print(f"  Queue: active_p0={queue.get('active_p0')} queued={len(queue.get('queued') or [])}/{queue.get('max_tasks')}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 21 đóng live voice pilot gate; tối ưu latency sang Phase 22.")



def print_live_voice_gate_test(raw_text=None, vts=None, voice=None):
    print("🧪 Live Voice Gate Test")
    print("  Action: read-only; synthetic final gate only, không gọi voice/VTube.")
    key = (raw_text or "").strip().lower()
    summary = phase21_5_guard_summary(vts, voice)
    if key in {"subphases", "subphase"}:
        print(f"  Section: subphases | {sum(1 for row in summary['subphases'] if row['passed'])}/{len(summary['subphases'])} pass")
        for row in summary["subphases"]:
            print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | {row['detail']}")
        print("  Execute: False")
        return
    if key in {"live", "pipeline"}:
        live = summary["live"]
        print("  Section: live pipeline")
        print(f"  Path: {live['path_action']}")
        print(f"  Gate: {live['action']} | visible={live['visible']} | payload={'yes' if live['payload'] else 'none'}")
        print(f"  Calls: voice_say={live['voice_say']} tts={live['tts_call']} trigger_expression={live['trigger_expression']} vts_request={live['vts_request']} execute={live['execute']}")
        print("  Execute: False")
        return
    if key in {"events", "autonomy"}:
        event_executed = [entry for entry in RUNTIME_EVENT_LOG if entry.get("execute")]
        audit_executed = [entry for entry in PHASE9_AUDIT_LOG if entry.get("execute")]
        print("  Section: events/autonomy")
        print(f"  Runtime event execute=True: {len(event_executed)}")
        print(f"  Phase9 audit execute=True: {len(audit_executed)}")
        print("  Autonomy: Phase 5-10 | no_autonomy_no_semi_autonomy")
        print("  Execute: False")
        return
    if key in {"locks", "voice"}:
        print("  Section: locks/voice")
        print(f"  Control kill={LIVE_VOICE_CONTROL['kill_switch']} pilot={LIVE_VOICE_CONTROL['pilot_enabled']} real_dispatch={LIVE_VOICE_CONTROL['real_dispatch_enabled']}")
        print(f"  Path kill={LIVE_VOICE_PATH_POLICY['kill_switch']} replacement={LIVE_VOICE_PATH_POLICY['replacement_enabled']} real_voice_call={LIVE_VOICE_PATH_POLICY['real_voice_call_enabled']}")
        print("  Execute: False")
        return
    aliases = {
        "ready": {"ready_for_latency"},
        "block": {"blocked_path"},
        "suppress": {"suppressed_path"},
        "hold": {"held_path", "empty_allowed_hold"},
        "empty": {"empty_allowed_hold"},
    }
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, block, suppress, hold, empty, subphases, live, events, locks")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | gate={row['got']} expected={row['expected']} | path={row['path_action']} visible={row['visible']} | reason={row['reason']}")
    print("  Execute: False")



def print_presence_state():
    now = time.time()
    with context_lock:
        browser = dict(context_state.get("browser", {}))
        proactive = dict(context_state.get("proactive", {}))
        in_flow = context_state.get("in_flow")
        last_chat = context_state.get("last_chat_time") or 0
        zone = context_state.get("active_zone")
        idle_state = context_state.get("idle_state")
        active_app = context_state.get("active_app")

    last_reaction = proactive.get("last_browser_reaction_time") or 0
    last_reaction_age = None if not last_reaction else max(0.0, now - last_reaction)
    last_chat_age = None if not last_chat else max(0.0, now - last_chat)

    recent_chat_active = last_chat_age is not None and last_chat_age < CHAT_CONTEXT_SUPPRESS
    cooldown_active = last_reaction_age is not None and last_reaction_age < PROACTIVE_BROWSER_COOLDOWN
    browser_available = bool(browser.get("available"))

    blocked_by = []
    if not browser_available:
        blocked_by.append("browser_unavailable")
    if in_flow:
        blocked_by.append("flow")
    if recent_chat_active:
        blocked_by.append("recent_chat")
    if cooldown_active:
        blocked_by.append("cooldown")
    if not blocked_by:
        blocked_by.append("none")

    last_reaction_text = "never" if last_reaction_age is None else f"{last_reaction_age:.1f}s ago"
    chat_text = "never" if last_chat_age is None else f"{last_chat_age:.1f}s ago"
    suppress_text = "inactive"
    if recent_chat_active:
        suppress_text = f"active {last_chat_age:.1f}s / {CHAT_CONTEXT_SUPPRESS:.0f}s"
    cooldown_text = "inactive"
    if cooldown_active:
        cooldown_text = f"active {last_reaction_age:.1f}s / {PROACTIVE_BROWSER_COOLDOWN:.0f}s"

    ready = blocked_by == ["none"]
    print("🟢 Presence")
    print(f"  Enabled: {proactive.get('enabled', True)}")
    print(f"  Browser: Available={browser_available} | Kind={browser.get('kind')} | Title={browser.get('title')}")
    print(f"  Last seen: Kind={proactive.get('last_browser_kind')} | Title={proactive.get('last_browser_title')}")
    print(f"  Last browser reaction: {last_reaction_text}")
    print(f"  Last chat: {chat_text}")
    print(f"  Recent chat suppress: {suppress_text}")
    print(f"  Browser cooldown: {cooldown_text}")
    print(f"  Flow: {in_flow}")
    print(f"  Ready for next browser event: {ready}")
    print(f"  Blocked by: {', '.join(blocked_by)}")
    presence_debug = proactive.get("presence_debug", {})
    print(
        "  Presence debug: "
        f"kind_changed={presence_debug.get('kind_changed')} | "
        f"title_changed={presence_debug.get('title_changed')} | "
        f"url_changed={presence_debug.get('url_changed')} | "
        f"should_react={presence_debug.get('should_react')}"
    )
    print(
        "  Presence why: "
        f"{presence_debug.get('blocked_reason')} | "
        f"{presence_debug.get('debug')}"
    )
    rhythm_context = {
        "zone": zone,
        "idle_state": idle_state,
        "in_flow": in_flow,
        "active_app": active_app,
        "last_chat_age": last_chat_age,
    }
    for line in format_presence_rhythm_status(rhythm_context):
        print(line)



def print_reaction_test(kind):
    with context_lock:
        browser = dict(context_state.get("browser", {}))
        emotion = dict(memory.get("emotion", {}))
        zone = context_state.get("active_zone")
    normalized = {
        "idle": "idle_relaxed",
        "afk": "idle_relaxed",
        "yt": "youtube",
        "ai": "ai_tools",
    }.get(kind, kind)
    event = "browser_presence" if normalized in {"youtube", "music", "video", "shopping", "ai_tools"} else normalized
    browser_kind = normalized if event == "browser_presence" else None
    reaction, debug = compose_reaction(
        event=event,
        zone=zone,
        browser_kind=browser_kind,
        emotion=emotion,
        title=browser.get("title"),
    )
    print("🧪 Reaction test")
    print(f"  Input: {kind}")
    print(f"  Event: {event}")
    print(f"  Browser kind: {browser_kind}")
    print(f"  Debug: {debug}")
    print(f"  Line: {reaction}")



def print_refine_auto_test(raw_text):
    result = auto_refine_nana_reply(raw_text, context=broker_context_snapshot())
    print("🧬 Refine Auto Test")
    print(f"  Status: {result.status}")
    print(f"  Guard should refine: {result.guard.should_refine}")
    print(f"  Guard score: {result.guard.score}")
    print(f"  Guard reasons: {', '.join(result.guard.reasons) if result.guard.reasons else 'none'}")
    if result.refine_result:
        print(f"  Refiner model: {result.refine_result.model}")
        print(f"  Refiner debug: {result.refine_result.debug}")
        print(f"  Refiner privacy risk: {result.refine_result.privacy_risk}")
        if result.refine_result.blocked_reasons:
            print(f"  Refiner blocked by: {', '.join(result.refine_result.blocked_reasons)}")
    print(f"  Original: {raw_text}")
    print(f"  Final: {result.final_text}")



def print_refine_guard_test(raw_text):
    decision = should_refine_reply(raw_text, context=broker_context_snapshot())
    print("🧪 Refine Guard")
    print(f"  Should refine: {decision.should_refine}")
    print(f"  Score: {decision.score}")
    print(f"  Reasons: {', '.join(decision.reasons) if decision.reasons else 'none'}")
    print(f"  Text: {raw_text}")



def print_refine_test(raw_text):
    result = refine_nana_reply(raw_text, context=broker_context_snapshot())
    print("💎 Refine Test")
    print(f"  Status: {result.status}")
    print(f"  Model: {result.model}")
    print(f"  Debug: {result.debug}")
    print(f"  Privacy risk: {result.privacy_risk}")
    print(f"  Context allowed: {result.context_allowed}")
    if result.blocked_reasons:
        print(f"  Blocked by: {', '.join(result.blocked_reasons)}")
    print(f"  Safety: {', '.join(result.safety)}")
    print(f"  Original: {result.original}")
    print(f"  Refined: {result.refined or 'None'}")



def print_runtime_reconcile_status():
    print("🧩 Background Reconcile")
    print(f"  Status: {RUNTIME_RECONCILE.get('status')}")
    print(
        f"  Count: {RUNTIME_RECONCILE.get('count')} | "
        f"coalesced={RUNTIME_RECONCILE.get('coalesced')} | "
        f"failures={RUNTIME_RECONCILE.get('failures')} | discarded={RUNTIME_RECONCILE.get('discarded')}"
    )
    print(f"  Last latency: {format_ms(RUNTIME_RECONCILE.get('last_ms'))}")
    print(f"  Last debug: {RUNTIME_RECONCILE.get('last_debug') or 'none'}")
    print(f"  Last summary: {shorten_line(RUNTIME_RECONCILE.get('last_summary'), 180) or 'none'}")
    print(f"  Source URL: {shorten_line(RUNTIME_RECONCILE.get('source_url'), 160) or 'none'}")
    print(f"  Started: {runtime_reconcile_age_text('started_at')} | Finished: {runtime_reconcile_age_text('finished_at')}")
    print("  Rule: manual background only; không tự trị, không click/type/post.")



def print_runtime_status():
    count = int(RUNTIME_LATENCY.get("browser_refresh_count") or 0)
    total_ms = float(RUNTIME_LATENCY.get("browser_refresh_total_ms") or 0.0)
    avg_ms = (total_ms / count) if count else None
    cache_hits = int(RUNTIME_LATENCY.get("browser_refresh_cache_hits") or 0)
    coalesced = int(RUNTIME_LATENCY.get("browser_refresh_coalesced") or 0)
    cooldown_skips = int(RUNTIME_LATENCY.get("browser_refresh_cooldown_skips") or 0)
    skip_age = RUNTIME_LATENCY.get("browser_refresh_last_skip_age")
    skip_age_text = "None" if skip_age is None else f"{float(skip_age):.1f}s"
    last_age = browser_refresh_last_age_seconds()
    with context_lock:
        browser = dict(context_state.get("browser", {}))
    age = browser_age_seconds(browser)
    state = browser_snapshot_state(browser)
    age_text = "None" if age is None else f"{age:.1f}s"
    last_age_text = "never" if last_age is None else f"{last_age:.1f}s ago"
    print("⏱️ Runtime Status")
    print("  Phase: 6R/6 context runtime stabilized")
    print(
        "  Browser snapshot: "
        f"available={browser.get('available')} | kind={browser.get('kind')} | state={state} | age={age_text}"
    )
    print(
        "  Browser refresh latency: "
        f"last={format_ms(RUNTIME_LATENCY.get('browser_refresh_last_ms'))} | "
        f"avg={format_ms(avg_ms)} | count={count}"
    )
    print(
        "  Breakdown: "
        f"read={format_ms(RUNTIME_LATENCY.get('browser_read_last_ms'))} | "
        f"local_summary={format_optional_ms(RUNTIME_LATENCY.get('local_summary_last_ms'))}"
    )
    print(
        "  Last refresh: "
        f"{last_age_text} | reason={RUNTIME_LATENCY.get('browser_refresh_last_reason')} | "
        f"mode={'deep' if RUNTIME_LATENCY.get('browser_refresh_last_deep') else 'light'}"
    )
    print(
        "  Cache policy: "
        f"last={RUNTIME_LATENCY.get('browser_refresh_last_policy')} | "
        f"reason={RUNTIME_LATENCY.get('browser_refresh_last_policy_reason')} | "
        f"last_state={RUNTIME_LATENCY.get('browser_refresh_last_state')} | "
        f"hits={cache_hits} | coalesced={coalesced} | cooldown_skips={cooldown_skips} | skip_age={skip_age_text}"
    )
    print(
        "  Browser refresh policy: "
        f"/br=force lightweight | /br-deep=force local_summary | auto cooldown={BROWSER_REFRESH_COOLDOWN_SECONDS:.1f}s"
    )
    print_runtime_reconcile_line()
    print("  Next: continue Phase 6 integration/regression, no autonomy.")



def print_social_classify(raw_text=DEFAULT_SOCIAL_CLASSIFY_REQUEST):
    broker_context = broker_context_snapshot()
    vision_text = None
    vision_status = "none"
    if LAST_VISION_DESCRIPTION:
        age = time.time() - LAST_VISION_DESCRIPTION.get("time", 0)
        if age <= 180:
            vision_text = LAST_VISION_DESCRIPTION.get("text")
            vision_status = f"fresh ({age:.1f}s)"
        else:
            vision_status = f"stale ({age:.1f}s)"
    source_text = build_social_draft_source(
        raw_text,
        broker_context=broker_context,
        vision_description=vision_text,
    )
    media_mode, reaction_style, guard_hint = social_source_classification(
        source_text,
        broker_context=broker_context,
        raw_text=raw_text,
        vision_description=vision_text,
    )
    priority_policy = context_priority_policy(
        context=broker_context,
        vision_description=vision_text,
    )
    print("🧪 Social Classifier")
    print(f"  Browser: kind={broker_context.get('browser_kind')} | title={shorten_line(broker_context.get('browser_title'), 90)}")
    print(f"  Vision: {vision_status}")
    print(f"  Context priority: {format_context_priority_summary(priority_policy)}")
    if broker_context.get("browser_social_vibe"):
        print(f"  Vibe audit: {format_vibe_audit_summary(broker_context)}")
    print(f"  Media: {media_mode}")
    print(f"  Reaction style: {reaction_style}")
    print(f"  Guard hint: {guard_hint}")
    if source_text:
        print(f"  Source: {shorten_line(source_text, 220)}")
    print("  Action: read-only; không gọi model, không tạo draft.")



def print_social_draft_test(raw_text, vision_description=None):
    raw_text = normalize_slashy_command_text(raw_text)
    broker_context = broker_context_snapshot()
    plan = plan_intent(raw_text, context=broker_context)
    omit_page_context = social_request_has_clear_topic(raw_text, broker_context=broker_context, intent=plan.intent)
    context_level = "L0" if omit_page_context else "L2"
    context_preview = build_context_budget_preview(broker_context, level=context_level)
    prompt_context_packet = "Context trang hiện tại được bỏ qua vì yêu cầu của Ba đã có topic rõ." if omit_page_context else enrich_social_context_packet(context_preview.packet, broker_context)
    if vision_description and not omit_page_context:
        prompt_context_packet = f"{prompt_context_packet}\nvision_description: {vision_description}"
    saved_context_level = "L0-omitted" if omit_page_context else context_preview.level
    draft_source = build_social_draft_source(
        raw_text,
        broker_context=broker_context,
        vision_description=vision_description,
    )
    media_mode, reaction_style, guard_hint = social_source_classification(
        draft_source,
        broker_context=broker_context,
        raw_text=raw_text,
        vision_description=vision_description,
    )
    priority_policy = context_priority_policy(
        context=broker_context,
        vision_description=vision_description,
    )
    print("📝 Social Draft Preview")
    print(f"  Plan status: {plan.status}")
    print(f"  Intent: {plan.intent}")
    print(f"  Policy: {plan.policy}")
    print(f"  Needs confirm: {plan.needs_confirm}")
    print(f"  Privacy risk: {plan.privacy_risk}")
    print(f"  Context level: {saved_context_level} ({context_preview.level_name})")
    print(f"  Context allowed: {context_preview.allowed_for_external_model}")
    if omit_page_context:
        print("  Context priority: omitted_by_clear_topic")
    else:
        print(
            "  Context priority: "
            f"{format_context_priority_summary(priority_policy)}"
        )
    if vision_description:
        reconcile = vision_text_reconcile_report(context=broker_context, vision_description=vision_description)
        print(f"  Reconcile: {reconcile['status']} | text={','.join(reconcile['text_groups']) or 'none'} | vision={','.join(reconcile['vision_groups']) or 'none'}")
        if reconcile["status"] == "conflict" and not omit_page_context:
            print("  Execute: skipped")
            print("  Guard: vision_text_conflict")
            print("  Recovery: Vision và text đang lệch nhóm tín hiệu. Ba crop lại đúng vùng hoặc dùng /social-draft-test để chỉ dựa vào post/title.")
            return
        print("  Vision: included")
    elif LAST_VISION_DESCRIPTION and time.time() - LAST_VISION_DESCRIPTION.get("time", 0) <= 180:
        print("  Vision: available_but_not_used (use /social-draft-vision)")
    print(f"  Classifier: media={media_mode} | reaction_style={reaction_style} | guard_hint={guard_hint}")
    teo_lite = should_use_teo_lite(plan.sanitized_text, broker_context, plan.intent)
    if teo_lite:
        print("  Flavor: Teo-lite")

    if plan.status == "blocked":
        print("  Execute: skipped")
        print(f"  Blocked by: {', '.join(plan.blocked_reasons) if plan.blocked_reasons else 'policy'}")
        print(f"  Sanitized input: {plan.sanitized_text or 'None'}")
        print(f"  Recovery: {recovery_notice('social_plan_blocked', detail=plan.reason, cooldown=False)}")
        return

    if plan.intent not in {"social.draft", "social.post", "social.reply"}:
        print("  Execute: skipped")
        print("  Reason: social_draft_only")
        print(f"  Hint: intent hiện tại là {plan.intent}, không phải nháp/post/reply social.")
        print(f"  Recovery: {recovery_notice('social_intent_mismatch', detail=plan.intent, cooldown=False)}")
        return

    missing_target = social_target_missing_reason(
        raw_text,
        broker_context,
        context_preview,
        vision_description=vision_description,
        omit_page_context=omit_page_context,
    )
    if missing_target:
        print("  Execute: skipped")
        print(f"  Reason: social_target_missing ({missing_target})")
        if guard_hint != "none":
            print(f"  Guard: {guard_hint}")
        print(
            "  Recovery: "
            f"{context_recovery_next_step(broker_context, missing_reason=missing_target, vision_description=vision_description)}"
        )
        return

    if not context_preview.allowed_for_external_model:
        print("  Execute: skipped")
        print(f"  Blocked by context: {', '.join(context_preview.blocked_reasons) if context_preview.blocked_reasons else 'context_not_allowed'}")
        print(f"  Recovery: {recovery_notice('social_context_blocked', detail=', '.join(context_preview.blocked_reasons), cooldown=False)}")
        return

    mark_social_residue()
    if vision_description:
        mark_residue("vision", level=15)

    route_text = "Soạn nháp social nhẹ cho Nana"
    route = route_sidecar_task(route_text, context=broker_context)
    print(f"  Route status: {route.status}")
    print(f"  Router model: {route.model}")
    print(f"  Fallbacks: {', '.join(route.fallback_models) if route.fallback_models else 'none'}")
    if route.status == "blocked":
        print("  Execute: skipped")
        print(f"  Blocked by: {', '.join(route.blocked_reasons)}")
        print(f"  Sanitized input: {route.sanitized_input or 'None'}")
        print(f"  Recovery: {recovery_notice('social_route_blocked', detail=', '.join(route.blocked_reasons), cooldown=False)}")
        return

    model_names = choose_social_draft_models(plan.sanitized_text, route)
    prompt = build_social_draft_prompt(
        user_text=route.sanitized_input,
        original_request=plan.sanitized_text,
        context_packet=prompt_context_packet,
        intent=plan.intent,
    )
    response, debug, used_model = call_social_draft_with_fallbacks(
        model_names,
        prompt,
    )
    draft = clean_social_draft(response, source_text=draft_source, intent=plan.intent)
    if teo_lite:
        draft = apply_teo_lite_public_reply(draft, source_text=draft_source, intent=plan.intent)
    guard_note = social_draft_guard_note(response, draft, source_text=draft_source, intent=plan.intent)
    print(f"  Draft model: {used_model}")
    print(f"  Helper debug: {debug}")
    if not response:
        print(f"  Recovery: {recovery_notice('social_draft_model_failed', detail=debug, cooldown=False)}")
    print("  Safety: draft_only, no_type, no_post, no_like_follow, Ba_confirm_before_public_action")
    if plan.needs_confirm:
        print("  Confirm note: đây chỉ là preview; đăng/reply thật vẫn cần Ba xác nhận.")
    final_draft = draft or fallback_social_draft(plan.intent, source_text=raw_text)
    duplicate, duplicate_reason = find_recent_duplicate_draft(final_draft)
    saved = social_drafts.add(
        intent=plan.intent,
        policy=plan.policy,
        draft=final_draft,
        request=plan.sanitized_text,
        model=used_model,
        context_level=saved_context_level,
        source_text=draft_source,
        reaction_style=reaction_style,
        guard_hint=guard_hint,
        context_priority=format_context_priority_summary(priority_policy),
        needs_confirm=plan.needs_confirm,
    )
    print(f"  Draft ID: {saved.id}")
    if duplicate:
        print(
            "  Duplicate warning: "
            f"{duplicate_reason} với draft #{duplicate.id} ({duplicate.age_seconds():.1f}s trước)."
        )
    if guard_note:
        print(f"  Guard: {guard_note}")
    print(f"  Draft: {final_draft}")
    print(f"  Queue: /draft-show {saved.id} | /draft-confirm {saved.id} | /draft-cancel {saved.id}")
    if response:
        cleared = recovery_clear_social(reason="social_draft_created")
        if cleared:
            print(f"  Recovery cleared: {', '.join(cleared)}")



def print_social_draft_vision(raw_text):
    if not LAST_VISION_DESCRIPTION:
        print("📝 Social Draft + Vision")
        print("  Status: skipped")
        print("  Reason: no_vision_description")
        print(f"  Recovery: {recovery_notice('vision_no_preview', cooldown=False)}")
        return
    age = time.time() - LAST_VISION_DESCRIPTION.get("time", 0)
    if age > 180:
        print("📝 Social Draft + Vision")
        print("  Status: skipped")
        print(f"  Reason: vision_description_stale ({age:.1f}s)")
        print(f"  Recovery: {recovery_notice('vision_stale', cooldown=False)}")
        return
    print_social_draft_test(
        raw_text,
        vision_description=LAST_VISION_DESCRIPTION.get("text"),
    )



def print_social_guard_failures():
    summary = social_guard_matrix_summary()
    print("🧯 Social Guard Failures")
    print("  Action: read-only; chỉ in regression failures.")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    if not summary["failures"]:
        print("  Failures: none")
        return
    for row in summary["failures"]:
        print(
            "  "
            f"{row['fail_stage']} | expected={row['expected']} got={row['style']} | "
            f"reason={row['fail_reason']}"
        )
        print(f"    example: {row['example']}")
        print(f"    fallback: {row['fallback']}")
        if row.get("compact") != row.get("fallback"):
            print(f"    compact: {row['compact']}")



def print_social_guard_status():
    summary = social_guard_matrix_summary()
    print("🛡️ Social Guard Status")
    print("  Action: read-only; không gọi model, không tạo draft.")
    print("  Order: priority classifier -> fallback draft -> polish/compact guard")
    print("  Regression: reports fail_stage=classifier/fallback/compact_guard with reason.")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for row in summary["rows"]:
        suffix = ""
        if row["status"] != "pass":
            suffix = f" | fail_stage={row['fail_stage']} reason={row['fail_reason']}"
        print(
            "  "
            f"{row['style'] or 'none'} | {row['status']} | media={row['media']} | guard={row['guard']} | "
            f"fallback={row['fallback']}{suffix}"
        )



def print_social_policy():
    rows = [
        ("social.listen", "allowed", "Đọc/tóm tắt X/Facebook/thread/post công khai."),
        ("social.draft", "allowed", "Soạn nháp tweet/post/reply/comment để Ba xem."),
        ("social.memory_review", "confirm-light", "Đưa kiến thức hay vào hàng chờ memory; lưu dài hạn cần Ba duyệt."),
        ("social.post", "confirm-strict", "Đăng tweet/post thật cần Ba xác nhận nghiêm."),
        ("social.reply", "confirm-strict", "Reply/comment thật cần Ba xác nhận nghiêm."),
        ("social.react", "confirm-strict", "Like/repost/share cần Ba xác nhận."),
        ("social.follow", "confirm-strict", "Follow/unfollow cần Ba xác nhận."),
        ("purchase.checkout", "blocked", "Thanh toán/mua hàng vẫn khóa cứng."),
        ("privacy.hold", "blocked", "Có key/token/secret thì chặn và redacted."),
    ]
    print("🌐 Social Policy v2")
    print("  Scope: X/Twitter, Facebook, thread/post/comment công khai")
    print("  Account: Nana riêng vẫn phải có ý thức mạng; action có dấu vết thì hỏi Ba")
    for intent, policy, note in rows:
        print(f"  {intent} | policy={policy}")
        print(f"    {note}")
    print("  Test:")
    print("    /intent-test Nana đọc X này rồi tóm tắt")
    print("    /intent-test Nana soạn nháp reply tweet này")
    print("    /intent-test Nana đăng tweet này hộ Ba")
    print("    /intent-test Nana like bài này")
    print("    /intent-test Nana lưu kiến thức này vào memory")



def print_social_target_status():
    broker_context = broker_context_snapshot()
    context_preview = build_context_budget_preview(broker_context, level="L2")
    vision_text = None
    vision_status = "none"
    if LAST_VISION_DESCRIPTION:
        age = time.time() - LAST_VISION_DESCRIPTION.get("time", 0)
        if age <= 180:
            vision_text = LAST_VISION_DESCRIPTION.get("text")
            vision_status = f"fresh ({age:.1f}s)"
        else:
            vision_status = f"stale ({age:.1f}s)"
    missing = social_target_missing_reason(
        DEFAULT_SOCIAL_CLASSIFY_REQUEST,
        broker_context,
        context_preview,
        vision_description=vision_text,
        omit_page_context=False,
    )
    ready = missing is None
    print("🎯 Social Target")
    print(f"  Ready: {ready}")
    print(f"  Reason: {'ok' if ready else missing}")
    print(
        "  Browser: "
        f"available={broker_context.get('browser_available')} | "
        f"fresh={broker_context.get('browser_fresh')} | "
        f"kind={broker_context.get('browser_kind')} | "
        f"title={shorten_line(broker_context.get('browser_title'), 90)}"
    )
    kind = (broker_context.get("browser_kind") or "").lower()
    has_post = bool(broker_context.get("browser_social_post_text"))
    has_vibe = bool(broker_context.get("browser_social_vibe"))
    has_packet = bool((context_preview.packet or "").strip())
    usable_page_context = kind == "social" and (has_post or has_vibe or has_packet)
    print(
        "  Signals: "
        f"post={has_post} | "
        f"vibe={has_vibe} | "
        f"usable_page_context={usable_page_context} | "
        f"raw_packet={has_packet} | "
        f"vision={vision_status}"
    )
    if broker_context.get("browser_social_post_text"):
        print(f"  Post: {shorten_line(broker_context.get('browser_social_post_text'), 160)}")
    if broker_context.get("browser_social_vibe"):
        print(f"  Vibe: {shorten_line(broker_context.get('browser_social_vibe'), 160)}")
        print(f"  Vibe audit: {format_vibe_audit_summary(broker_context)}")
    priority_policy = context_priority_policy(
        context=broker_context,
        vision_description=vision_text,
    )
    print(f"  Context priority: {format_context_priority_summary(priority_policy)}")
    target_source = build_social_draft_source(
        "Nana viết nháp reply siêu ngắn cho tweet này",
        broker_context=broker_context,
        vision_description=vision_text,
    )
    media_mode, reaction_style, guard_hint = social_source_classification(
        target_source,
        broker_context=broker_context,
        raw_text=DEFAULT_SOCIAL_CLASSIFY_REQUEST,
        vision_description=vision_text,
    )
    print(f"  Classifier: media={media_mode} | reaction_style={reaction_style} | guard_hint={guard_hint}")
    if ready:
        if vision_text:
            print("  Next: /social-draft-vision Nana viết nháp reply siêu ngắn cho tweet này")
        else:
            print("  Next: /social-draft-test Nana viết nháp reply siêu ngắn cho tweet này")
            print("  Vision optional: /vision-preview | /vision-describe | /social-draft-vision <text>")
    else:
        print("  Next: mở đúng tweet/bài rồi /br, hoặc gửi nội dung trực tiếp trong lệnh draft.")
        print(f"  Recovery: {context_recovery_next_step(broker_context, missing_reason=missing, vision_description=vision_text)}")



def print_stream_pilot_control_guard_status(vts=None, voice=None):
    summary = phase24_1_guard_summary(vts, voice)
    print("🧪 Phase 24.1 Stream Pilot Control Guard")
    print("  Action: kiểm controlled streaming pilot gate, không gọi ElevenLabs/voice/execute.")
    print(f"  Progress: {phase24_1_progress_percent(summary)}%")
    print("  Companion Overall: ~80% / 100%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Stream pilot control regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} reason={row['reason']}")



def print_stream_pilot_control_status(vts=None, voice=None):
    summary = phase24_1_guard_summary(vts, voice)
    live = summary["live"]
    print("🎚️ Stream Pilot Control Status")
    print("  Action: read-only; kiểm pilot/kill-switch cho streaming thật, chưa gọi API.")
    print(f"  Phase 24.1 Progress: {phase24_1_progress_percent(summary)}%")
    print(f"  Companion Overall: ~80% / 100%")
    print(f"  Live: action={live['action']} | pilot={live['pilot_enabled']} | kill={live['kill_switch']} | direct={live['direct_user_turn']}")
    print(f"  Locks: real_streaming={VOICE_STREAMING_ENABLED} stream_call={live['stream_call']} execute={live['execute']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 24.1 chỉ control gate; chưa bật streaming thật.")



def print_stream_pilot_control_test(raw_text=None, vts=None, voice=None):
    print("🧪 Stream Pilot Control Test")
    print("  Action: read-only; synthetic stream pilot control only, không gọi voice/API.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "kill": {"default_kill_switch"},
        "ready": {"pilot_ready_dry_run"},
        "pilot": {"pilot_ready_dry_run", "pilot_disabled_hold"},
        "direct": {"non_direct_hold"},
        "focus": {"focus_suppress"},
        "gate": {"gate_hold", "gate_block"},
        "hold": {"pilot_disabled_hold", "non_direct_hold", "gate_hold"},
        "block": {"default_kill_switch", "gate_block"},
        "suppress": {"focus_suppress"},
    }
    summary = phase24_1_guard_summary(vts, voice)
    if key == "live":
        live = summary["live"]
        print("  Section: live control")
        print(f"  Action: {live['action']} | reason={live['reason']}")
        print(f"  Pilot: {live['pilot_enabled']} | kill={live['kill_switch']} | real_streaming={live['real_streaming_enabled']}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: kill, ready, pilot, direct, focus, gate, hold, block, suppress, live")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} expected={row['expected']} | pilot={row['pilot']} kill={row['kill']} | reason={row['reason']}")
    print("  Execute: False")



def print_stream_pilot_enable_guard_status(vts=None, voice=None):
    summary = phase25_2_guard_summary(vts, voice)
    print("🧪 Phase 25.2 Stream Pilot Enable Guard")
    print("  Action: kiểm pilot enable gate, không bật flag/không gọi API.")
    print(f"  Progress: {phase_progress_percent(summary)}%")
    print("  Companion Overall: ~86% / 100%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Pilot enable regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} reason={row['reason']}")



def print_stream_pilot_enable_status(vts=None, voice=None):
    summary = phase25_2_guard_summary(vts, voice)
    live = summary["live"]
    print("🎚️ Stream Pilot Enable Status")
    print("  Action: read-only; kiểm manual pilot enable gate, chưa bật flag thật.")
    print(f"  Phase 25.2 Progress: {phase_progress_percent(summary)}%")
    print("  Companion Overall: ~86% / 100%")
    print(f"  Live: measurement={live['measurement_action']} | action={live['action']} | confirm={live['manual_confirm']}")
    print(f"  Calls: runtime_change={live['pilot_runtime_change']} stream_request={live['stream_request']} execute={live['execute']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 25.2 chỉ pilot enable dry-run; chưa bật streaming thật.")



def print_stream_pilot_enable_test(raw_text=None, vts=None, voice=None):
    print("🧪 Stream Pilot Enable Test")
    print("  Action: read-only; synthetic pilot enable only, không gọi voice/API.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"manual_ready", "sample_ready"},
        "confirm": {"confirm_hold"},
        "kill": {"kill_block"},
        "focus": {"focus_suppress"},
        "measurement": {"measurement_hold", "measurement_block"},
        "hold": {"confirm_hold", "measurement_hold"},
        "block": {"kill_block", "measurement_block"},
        "suppress": {"focus_suppress"},
    }
    summary = phase25_2_guard_summary(vts, voice)
    if key == "live":
        live = summary["live"]
        print("  Section: live enable")
        print(f"  Action: {live['action']} | reason={live['reason']}")
        print(f"  Runtime change: {live['pilot_runtime_change']} | real_streaming={live['real_streaming_enabled']}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, confirm, kill, focus, measurement, hold, block, suppress, live")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} expected={row['expected']} | visible={row['visible']} | reason={row['reason']}")
    print("  Execute: False")



def print_stream_rollback_guard_status(vts=None, voice=None):
    summary = phase24_4_guard_summary(vts, voice)
    print("🧪 Phase 24.4 Stream Rollback/Timeout Guard")
    print("  Action: kiểm rollback/timeout streaming, không gọi ElevenLabs/voice/execute.")
    print(f"  Progress: {phase24_4_progress_percent(summary)}%")
    print("  Companion Overall: ~83% / 100%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Stream rollback regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} reason={row['reason']}")



def print_stream_rollback_test(raw_text=None, vts=None, voice=None):
    print("🧪 Stream Rollback Test")
    print("  Action: read-only; synthetic rollback/timeout only, không gọi voice/API.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"guard_ready"},
        "timeout": {"first_audio_timeout", "partial_timeout"},
        "first": {"first_audio_timeout"},
        "partial": {"partial_timeout"},
        "error": {"stream_error"},
        "rollback": {"first_audio_timeout", "partial_timeout", "stream_error"},
        "path": {"bad_rollback_block"},
        "hold": {"pilot_hold"},
        "block": {"bad_rollback_block", "pilot_block"},
        "pilot": {"pilot_hold", "pilot_block"},
    }
    summary = phase24_4_guard_summary(vts, voice)
    if key == "live":
        live = summary["live"]
        print("  Section: live rollback")
        print(f"  Action: {live['action']} | reason={live['reason']}")
        print(f"  Fallback: {live['fallback_ready']} | rollback={live['rollback_path']} | elapsed={live['elapsed_ms']}ms")
        print(f"  Calls: stream_request={live['stream_request']} voice_say={live['voice_say']}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, timeout, first, partial, error, rollback, path, hold, block, pilot, live")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} expected={row['expected']} | fallback={row['fallback']} elapsed={row['elapsed_ms']}ms | reason={row['reason']}")
    print("  Execute: False")



def print_time_state():
    now = current_time_context()
    print("🕒 Time")
    print(f"  Date: {now['date_vi']} ({now['date']})")
    print(f"  Time: {now['time']}")
    print(f"  Part: {now['part_of_day']}")
    print(f"  Timezone: {now['timezone']}")



def print_vision_cache_state():
    output_dir = vision_previewer.output_dir
    files = []
    if output_dir.exists():
        files = sorted(
            output_dir.glob("vision_focus_*.png"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    print("🧹 Vision Cache")
    print(f"  Dir: {output_dir}")
    print(f"  Files: {len(files)}")
    if files:
        newest = files[0]
        age = time.time() - newest.stat().st_mtime
        print(f"  Latest: {newest.name} ({age:.1f}s ago)")



def print_vision_describe():
    global LAST_VISION_DESCRIPTION
    path = vision_previewer.latest_preview()
    print("👁️ Vision Describe")
    if not path:
        print("  Status: skipped")
        print("  Reason: no_preview_image")
        print(f"  Recovery: {recovery_notice('vision_no_preview', cooldown=False)}")
        return

    prompt = (
        "Mô tả vùng crop này trong 1-2 dòng tiếng Việt tự nhiên. "
        "Chỉ nói thứ nhìn thấy rõ; không đoán danh tính, không khuyên click, không đề xuất hành động."
    )
    model_names = ["gpt-5.5", "gpt-5.4"]
    description = None
    debug = "no_model_attempted"
    used_model = None
    for model_name in model_names:
        description, debug = call_llmgate_vision(
            model_name,
            prompt,
            path,
            max_tokens=120,
            temperature=0.1,
        )
        if description:
            used_model = model_name
            break

    print(f"  File: {path}")
    print(f"  Model: {used_model or 'None'}")
    print(f"  Debug: {debug}")
    if not description:
        print("  Status: skipped")
        print("  Reason: vision_model_failed_or_unsupported")
        message = recovery_notice("vision_model_failed", detail=debug, cooldown=False)
        print(f"  Recovery: {message}")
        return
    print("  Status: described")
    print("  Safety: manual_trigger_only, latest_local_crop, no_action_control, no_post")
    print(f"  Description: {description}")
    cleared = recovery_clear_vision(reason="vision_described")
    if cleared:
        print(f"  Recovery cleared: {', '.join(cleared)}")
    LAST_VISION_DESCRIPTION = {
        "text": description,
        "path": str(path),
        "model": used_model,
        "time": time.time(),
    }



def print_vision_preview():
    removed = vision_previewer.cleanup_preview_cache()
    context = broker_context_snapshot(force_edge=True)
    result = vision_previewer.capture_focus(context)
    print("👁️ Vision Preview")
    print(f"  Status: {result.get('status')}")
    print(f"  Reason: {result.get('reason')}")
    print(f"  Privacy risk: {result.get('privacy_risk')}")
    if removed:
        print(f"  Cache cleanup: removed {removed} old preview(s)")
    if result.get("status") != "captured":
        recovery_kind = vision_recovery_kind(result.get("reason"))
        if recovery_kind:
            print(f"  Recovery: {recovery_notice(recovery_kind, detail=result.get('reason'), cooldown=False)}")
    if result.get("blocked_reasons"):
        print(f"  Blocked by: {', '.join(result.get('blocked_reasons'))}")
    if result.get("target"):
        print(f"  Target: {result.get('target')}")
    if result.get("clip"):
        clip = result.get("clip") or {}
        print(
            "  Clip: "
            f"x={clip.get('x')} y={clip.get('y')} "
            f"w={clip.get('width')} h={clip.get('height')} "
            f"scale={clip.get('scale')}"
        )
    if result.get("path"):
        print(f"  File: {result.get('path')}")
    if result.get("note"):
        print(f"  Note: {result.get('note')}")
    if result.get("status") == "captured":
        cleared = recovery_clear_vision(reason="vision_preview_captured")
        if cleared:
            print(f"  Recovery cleared: {', '.join(cleared)}")



def print_voice_engine_impl_guard_status(vts=None, voice=None):
    summary = phase22_4_guard_summary(vts, voice)
    print("🧪 Phase 22.4 VoiceEngine Implementation Guard")
    print("  Action: kiểm implementation cache/chunk, không gọi ElevenLabs/không phát voice.")
    print(f"  Progress: {phase22_4_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Implementation regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} reason={row['reason']}")



def print_voice_engine_impl_status(vts=None, voice=None):
    summary = phase22_4_guard_summary(vts, voice)
    snapshot = summary["snapshot"]
    print("🛠️ VoiceEngine Implementation Status")
    print("  Action: implemented cache/chunk engine path; status chỉ đọc, không gọi ElevenLabs.")
    print(f"  Phase 22.4 Progress: {phase22_4_progress_percent(summary)}%")
    print(f"  Cache: enabled={VOICE_CACHE_ENABLED} max_text={VOICE_CACHE_MAX_TEXT_CHARS} dir={VOICE_CACHE_DIR}")
    print(f"  Chunking: enabled={VOICE_CHUNKING_ENABLED} max_chars={VOICE_CHUNK_MAX_CHARS}")
    print(f"  Voice: speaking={snapshot['speaking']} queue={snapshot['queue_size']}/{snapshot['queue_maxsize']} hits={snapshot['cache_hits']} misses={snapshot['cache_misses']} chunked={snapshot['chunked_total']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 22.4 đã sửa engine cache/chunk, nhưng test/status không gọi API hoặc phát voice.")



def print_voice_engine_impl_test(raw_text=None, vts=None, voice=None):
    print("🧪 VoiceEngine Implementation Test")
    print("  Action: read-only; helper probe only, không gọi ElevenLabs/không phát voice.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "cache": {"cache_short_ack", "cache_known_ack", "cache_path_stable", "cache_cleanup_guard"},
        "chunk": {"long_chunk_split"},
        "single": {"medium_single_request"},
        "cleanup": {"cache_cleanup_guard"},
    }
    summary = phase22_4_guard_summary(vts, voice)
    if key in {"config", "policy"}:
        print("  Section: config")
        print(f"  Cache: enabled={VOICE_CACHE_ENABLED} dir={VOICE_CACHE_DIR} max_text={VOICE_CACHE_MAX_TEXT_CHARS}")
        print(f"  Chunking: enabled={VOICE_CHUNKING_ENABLED} max_chars={VOICE_CHUNK_MAX_CHARS}")
        print("  Execute: False")
        return
    if key == "methods":
        print("  Section: methods")
        for name in ["tts_to_audio_paths", "_split_tts_text", "_lookup_voice_cache", "_write_voice_cache", "_is_cached_audio_path"]:
            print(f"  {name}: {callable(getattr(VoiceEngine, name, None))}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: cache, chunk, single, cleanup, config, methods")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} | {row['detail']} | reason={row['reason']}")
    print("  Execute: False")



def print_voice_engine_patch_guard_status(vts=None, voice=None):
    summary = phase22_3_guard_summary(vts, voice)
    print("🧪 Phase 22.3 VoiceEngine Patch Dry-Run Guard")
    print("  Action: read-only; kiểm patch plan VoiceEngine, không sửa file/không execute.")
    print(f"  Progress: {phase22_3_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Patch regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} reason={row['reason']}")



def print_voice_engine_patch_status(vts=None, voice=None):
    summary = phase22_3_guard_summary(vts, voice)
    live = summary["live"]
    print("🧩 VoiceEngine Patch Dry-Run Status")
    print("  Action: read-only; dựng patch plan cho VoiceEngine, chưa sửa file/không gọi ElevenLabs.")
    print(f"  Phase 22.3 Progress: {phase22_3_progress_percent(summary)}%")
    print(f"  Live: strategy={live['strategy']} | action={live['action']} | steps={len(live['patch_steps'])}")
    print(f"  Patch: engine_edit={live['requires_engine_edit']} lipsync_adapter={live['requires_lipsync_adapter']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 22.3 chỉ patch dry-run; chưa sửa VoiceEngine thật.")



def print_voice_engine_patch_test(raw_text=None, vts=None, voice=None):
    print("🧪 VoiceEngine Patch Dry-Run Test")
    print("  Action: read-only; synthetic patch plan only, không sửa file/không gọi voice.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "cache": {"cache_patch"},
        "stream": {"stream_patch"},
        "chunk": {"chunk_patch"},
        "hold": {"hold_patch"},
    }
    summary = phase22_3_guard_summary(vts, voice)
    if key in {"points", "patch-points"}:
        print("  Section: patch points")
        for point in PHASE22_3_PATCH_POINTS:
            print(f"  {point}")
        print("  Execute: False")
        return
    if key in {"steps", "plan"}:
        live = summary["live"]
        print("  Section: live patch steps")
        for index, step in enumerate(live["patch_steps"], start=1):
            print(f"  {index}. {step}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: cache, stream, chunk, hold, points, steps")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | strategy={row['strategy']} steps={row['steps']} lipsync_adapter={row['lipsync_adapter']} | reason={row['reason']}")
    print("  Execute: False")



def print_voice_latency_baseline_guard_status(vts=None, voice=None):
    summary = phase22_1_guard_summary(vts, voice)
    print("🧪 Phase 22.1 Voice Latency Baseline Guard")
    print("  Action: read-only; kiểm baseline latency, không gọi ElevenLabs/voice/execute.")
    print(f"  Progress: {phase22_1_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Latency baseline regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} reason={row['reason']}")



def print_voice_latency_baseline_status(vts=None, voice=None):
    summary = phase22_1_guard_summary(vts, voice)
    snapshot = summary["snapshot"]
    live = summary["live"]
    print("⏱️ Voice Latency Baseline Status")
    print("  Action: read-only; đo contract latency voice hiện tại, không gọi ElevenLabs/voice.")
    print(f"  Phase 22.1 Progress: {phase22_1_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} | text_len={live['text_len']} | reason={live['reason']}")
    print(f"  Current TTS: model={snapshot['model_id']} format={snapshot['output_format']} blocking={snapshot['blocking_request']} timeout={snapshot['network_timeout_s']}s")
    print(f"  Optimizations: streaming={snapshot['is_streaming']} cache={snapshot['uses_cache']} chunking={snapshot['uses_chunking']}")
    print(f"  Voice: speaking={snapshot['speaking']} queue={snapshot['queue_size']}/{snapshot['queue_maxsize']} room={snapshot['queue_has_room']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 22.1 chỉ baseline latency; chưa gọi API, chưa đổi pipeline.")



def print_voice_latency_baseline_test(raw_text=None, vts=None, voice=None):
    print("🧪 Voice Latency Baseline Test")
    print("  Action: read-only; synthetic latency classification only, không gọi ElevenLabs.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "empty": {"empty_baseline"},
        "cache": {"short_ack_cache"},
        "short": {"short_ack_cache"},
        "stream": {"medium_stream"},
        "medium": {"medium_stream"},
        "chunk": {"long_chunk"},
        "long": {"long_chunk"},
    }
    summary = phase22_1_guard_summary(vts, voice)
    if key in {"pipeline", "stages"}:
        snapshot = summary["snapshot"]
        print("  Section: pipeline")
        for index, stage in enumerate(snapshot["pipeline_stages"], start=1):
            print(f"  {index}. {stage}")
        print(f"  Blocking request: {snapshot['blocking_request']} | temp_audio={snapshot['writes_temp_audio']} | play_after_full={snapshot['plays_after_full_response']}")
        print("  Execute: False")
        return
    if key in {"bottleneck", "bottlenecks"}:
        print("  Section: bottlenecks")
        print(f"  Items: {','.join(summary['bottlenecks'])}")
        print("  Execute: False")
        return
    if key in {"queue", "voice"}:
        snapshot = summary["snapshot"]
        print("  Section: voice queue")
        print(f"  Speaking: {snapshot['speaking']}")
        print(f"  Queue: {snapshot['queue_size']}/{snapshot['queue_maxsize']} room={snapshot['queue_has_room']}")
        print(f"  Worker alive: {snapshot['worker_alive']}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: empty, cache, short, stream, medium, chunk, long, pipeline, bottlenecks, queue")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['got']} expected={row['expected']} | len={row['text_len']} | reason={row['reason']}")
    print("  Execute: False")



def print_voice_latency_design_guard_status(vts=None, voice=None):
    summary = phase22_2_guard_summary(vts, voice)
    print("🧪 Phase 22.2 Voice Latency Design Guard")
    print("  Action: read-only; kiểm design cache/stream/chunk, không gọi ElevenLabs/execute.")
    print(f"  Progress: {phase22_2_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Latency design regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | strategy={row['got']} reason={row['reason']}")



def print_voice_latency_design_status(vts=None, voice=None):
    summary = phase22_2_guard_summary(vts, voice)
    live = summary["live"]
    print("🧭 Voice Latency Design Status")
    print("  Action: read-only; thiết kế cache/stream/chunk policy, chưa gọi ElevenLabs.")
    print(f"  Phase 22.2 Progress: {phase22_2_progress_percent(summary)}%")
    print(f"  Live: strategy={live['strategy']} | reason={live['reason']} | target_first_audio={live['target_first_audio_ms']}ms")
    print(f"  Toggles: cache={live['cache_enabled']} streaming={live['streaming_enabled']} chunking={live['chunking_enabled']} prewarm={live['prewarm_enabled']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 22.2 chỉ thiết kế policy; chưa sửa VoiceEngine/không gọi API.")



def print_voice_latency_design_test(raw_text=None, vts=None, voice=None):
    print("🧪 Voice Latency Design Test")
    print("  Action: read-only; synthetic design policy only, không gọi ElevenLabs.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "empty": {"empty_hold"},
        "cache": {"short_cache", "known_ack_cache"},
        "short": {"short_cache", "known_ack_cache"},
        "stream": {"medium_stream"},
        "medium": {"medium_stream"},
        "chunk": {"long_chunk_stream"},
        "long": {"long_chunk_stream"},
    }
    summary = phase22_2_guard_summary(vts, voice)
    if key in {"plan", "steps"}:
        live = summary["live"]
        print("  Section: plan steps")
        for index, step in enumerate(live["pipeline_steps"], start=1):
            print(f"  {index}. {step}")
        print("  Execute: False")
        return
    if key in {"target", "latency"}:
        rows = summary["test_rows"]
        print("  Section: target latency")
        for row in rows:
            target = f"{row['target_ms']}ms" if row["target_ms"] is not None else "none"
            print(f"  {row['name']} | strategy={row['got']} | target={target}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: empty, cache, short, stream, medium, chunk, long, plan, target")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        target = f"{row['target_ms']}ms" if row["target_ms"] is not None else "none"
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | strategy={row['got']} expected={row['expected']} | len={row['text_len']} target={target} | reason={row['reason']}")
    print("  Execute: False")



def print_voice_latency_gate_guard_status(vts=None, voice=None):
    summary = phase22_5_guard_summary(vts, voice)
    print("🧪 Phase 22.5 Voice Latency Gate Guard")
    print("  Action: tổng kiểm Phase 22 latency, không gọi ElevenLabs/voice/execute.")
    print(f"  Progress: {phase22_5_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Subphases:")
    for name, passed, detail in summary["subphases"]:
        print(f"    {'pass' if passed else 'fail'} | {name} | {detail}")
    print("  Latency gate regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | gate={row['action']} reason={row['reason']}")



def print_voice_latency_gate_status(vts=None, voice=None):
    summary = phase22_5_guard_summary(vts, voice)
    live = summary["live"]
    snapshot = summary["snapshot"]
    print("🧠 Phase 22 Voice Latency Gate Status")
    print("  Action: tổng kiểm latency optimization, không gọi ElevenLabs/voice.")
    print(f"  Phase 22.5 Progress: {phase22_5_progress_percent(summary)}%")
    print(f"  Live: strategy={live['strategy']} gate={live['action']} target={live['target_ms']}ms")
    print(f"  Engine: cache={VOICE_CACHE_ENABLED} chunking={VOICE_CHUNKING_ENABLED} queue={snapshot['queue_size']}/{snapshot['queue_maxsize']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 22 đóng cache/chunk latency layer; streaming thật vẫn pending.")



def print_voice_latency_gate_test(raw_text=None, vts=None, voice=None):
    print("🧪 Voice Latency Gate Test")
    print("  Action: read-only; synthetic final latency gate only, không gọi voice/API.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "cache": {"short_cache_gate"},
        "chunk": {"long_chunk_gate"},
        "stream": {"medium_stream_gate"},
        "empty": {"empty_gate"},
    }
    summary = phase22_5_guard_summary(vts, voice)
    if key == "subphases":
        print(f"  Section: subphases | {sum(1 for row in summary['subphases'] if row[1])}/{len(summary['subphases'])} pass")
        for name, passed, detail in summary["subphases"]:
            print(f"  {'pass' if passed else 'fail'} | {name} | {detail}")
        print("  Execute: False")
        return
    if key == "live":
        live = summary["live"]
        snapshot = summary["snapshot"]
        print("  Section: live pipeline")
        print(f"  Strategy: {live['strategy']}")
        print(f"  Gate: {live['action']} | target={live['target_ms']}ms")
        print(f"  Engine: cache={VOICE_CACHE_ENABLED} chunking={VOICE_CHUNKING_ENABLED} queue={snapshot['queue_size']}/{snapshot['queue_maxsize']}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: cache, chunk, stream, empty, subphases, live")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | gate={row['action']} expected={row['expected']} | strategy={row['strategy']} | reason={row['reason']}")
    print("  Execute: False")



def print_voice_stream_gate_guard_status(vts=None, voice=None):
    summary = phase23_5_guard_summary(vts, voice)
    print("🧪 Phase 23.5 Voice Stream Gate Guard")
    print("  Action: tổng kiểm Phase 23 streaming, không gọi ElevenLabs/voice/execute.")
    print(f"  Progress: {phase23_5_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Subphases:")
    for name, passed, detail in summary["subphases"]:
        print(f"    {'pass' if passed else 'fail'} | {name} | {detail}")
    print("  Stream gate regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | gate={row['action']} reason={row['reason']}")



def print_voice_stream_gate_status(vts=None, voice=None):
    summary = phase23_5_guard_summary(vts, voice)
    live = summary["live"]
    print("🧠 Phase 23 Voice Stream Gate Status")
    print("  Action: tổng kiểm streaming layer, không gọi ElevenLabs/voice.")
    print(f"  Phase 23.5 Progress: {phase23_5_progress_percent(summary)}%")
    print(f"  Live: gate={live['action']} | stream_ready={live['stream_ready']} | reason={live['reason']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 23 đóng streaming dry-run/safety; real streaming sang Phase 24 nếu được bật.")



def print_voice_stream_gate_test(raw_text=None, vts=None, voice=None):
    print("🧪 Voice Stream Gate Test")
    print("  Action: read-only; synthetic final stream gate only, không gọi voice/API.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"ready_for_pilot"},
        "hold": {"hold_gate"},
        "block": {"block_gate"},
    }
    summary = phase23_5_guard_summary(vts, voice)
    if key == "subphases":
        print(f"  Section: subphases | {sum(1 for row in summary['subphases'] if row[1])}/{len(summary['subphases'])} pass")
        for name, passed, detail in summary["subphases"]:
            print(f"  {'pass' if passed else 'fail'} | {name} | {detail}")
        print("  Execute: False")
        return
    if key == "live":
        live = summary["live"]
        print("  Section: live gate")
        print(f"  Gate: {live['action']} | stream_ready={live['stream_ready']} | execute={live['execute']}")
        print(f"  Reason: {live['reason']}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, hold, block, subphases, live")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | gate={row['action']} expected={row['expected']} | reason={row['reason']}")
    print("  Execute: False")



def print_voice_stream_safety_guard_status(vts=None, voice=None):
    summary = phase23_4_guard_summary(vts, voice)
    print("🧪 Phase 23.4 Voice Stream Safety Guard")
    print("  Action: kiểm live stream safety, không gọi ElevenLabs/voice/execute.")
    print(f"  Progress: {phase23_4_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Stream safety regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} reason={row['reason']}")



def print_voice_stream_safety_status(vts=None, voice=None):
    summary = phase23_4_guard_summary(vts, voice)
    live = summary["live"]
    voice_state = summary["voice_state"]
    print("🛡️ Voice Stream Safety Status")
    print("  Action: read-only; kiểm safety gate cho stream packet, chưa gọi API/voice.")
    print(f"  Phase 23.4 Progress: {phase23_4_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} | stream_ready={live['stream_ready']} | reason={live['reason']}")
    print(f"  Voice: speaking={voice_state['speaking']} queue={voice_state['queue_size']}/{voice_state['queue_maxsize']} real_streaming={VOICE_STREAMING_ENABLED}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 23.4 chỉ safety gate; chưa stream hoặc phát audio thật.")



def print_voice_stream_safety_test(raw_text=None, vts=None, voice=None):
    print("🧪 Voice Stream Safety Test")
    print("  Action: read-only; synthetic stream safety only, không gọi voice/API.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"ready_safety"},
        "hold": {"hold_packet", "busy_voice_hold"},
        "block": {"bad_rollback_block"},
        "busy": {"busy_voice_hold"},
        "rollback": {"bad_rollback_block"},
    }
    summary = phase23_4_guard_summary(vts, voice)
    if key == "live":
        live = summary["live"]
        print("  Section: live safety")
        print(f"  Action: {live['action']} | stream_ready={live['stream_ready']} | execute={live['execute']}")
        print(f"  Reason: {live['reason']}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, hold, block, busy, rollback, live")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} expected={row['expected']} | stream_ready={row['stream_ready']} | reason={row['reason']}")
    print("  Execute: False")



def print_voice_streaming_decision_guard_status(vts=None, voice=None):
    summary = phase23_2_guard_summary(vts, voice)
    print("🧪 Phase 23.2 Voice Streaming Decision Guard")
    print("  Action: kiểm streaming decision gate, không gọi ElevenLabs/voice/execute.")
    print(f"  Progress: {phase23_2_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Streaming decision regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} reason={row['reason']}")



def print_voice_streaming_decision_status(vts=None, voice=None):
    summary = phase23_2_guard_summary(vts, voice)
    live = summary["live"]
    snapshot = summary["snapshot"]
    print("🌊 Voice Streaming Decision Status")
    print("  Action: read-only; quyết định streaming dry-run, chưa gọi ElevenLabs stream.")
    print(f"  Phase 23.2 Progress: {phase23_2_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} | telemetry={live['telemetry']} | rollback={live['rollback_required']}")
    print(f"  Last: strategy={snapshot.get('last_tts_strategy')} total={snapshot.get('last_tts_total_ms')}ms chunks={snapshot.get('last_tts_chunks')} cache_hits={snapshot.get('last_tts_cache_hits')}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 23.2 chỉ quyết định; chưa bật streaming adapter hoặc gọi API.")



def print_voice_streaming_decision_test(raw_text=None, vts=None, voice=None):
    print("🧪 Voice Streaming Decision Test")
    print("  Action: read-only; synthetic streaming decision only, không gọi voice/API.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "empty": {"no_sample_hold"},
        "cache": {"cache_keep"},
        "chunk": {"chunk_keep"},
        "slow": {"slow_stream_candidate"},
        "candidate": {"slow_stream_candidate"},
    }
    summary = phase23_2_guard_summary(vts, voice)
    if key == "live":
        live = summary["live"]
        print("  Section: live decision")
        print(f"  Action: {live['action']} | telemetry={live['telemetry']} | rollback={live['rollback_required']} | streaming={live['streaming_enabled']}")
        print(f"  Reason: {live['reason']}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: empty, cache, chunk, slow, candidate, live")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} expected={row['expected']} | telemetry={row['telemetry']} rollback={row['rollback']} | reason={row['reason']}")
    print("  Execute: False")



def print_voice_streaming_dry_run_guard_status(vts=None, voice=None):
    summary = phase23_3_guard_summary(vts, voice)
    print("🧪 Phase 23.3 Voice Streaming Dry-Run Guard")
    print("  Action: kiểm streaming dry-run adapter, không gọi ElevenLabs/voice/execute.")
    print(f"  Progress: {phase23_3_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Streaming dry-run regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} reason={row['reason']}")



def print_voice_streaming_dry_run_status(vts=None, voice=None):
    summary = phase23_3_guard_summary(vts, voice)
    live = summary["live"]
    print("🧪 Voice Streaming Dry-Run Status")
    print("  Action: read-only; dựng streaming packet, không gọi ElevenLabs stream.")
    print(f"  Phase 23.3 Progress: {phase23_3_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} | reason={live['reason']}")
    print(f"  Packet: endpoint={live['endpoint']} | rollback={live['rollback_path']} | stream={live['streaming_enabled']}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 23.3 chỉ dry-run adapter; chưa stream hoặc phát audio thật.")



def print_voice_streaming_dry_run_test(raw_text=None, vts=None, voice=None):
    print("🧪 Voice Streaming Dry-Run Test")
    print("  Action: read-only; synthetic stream packet only, không gọi voice/API.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "ready": {"ready_packet"},
        "empty": {"empty_hold"},
        "disabled": {"disabled_contract"},
    }
    summary = phase23_3_guard_summary(vts, voice)
    if key == "live":
        live = summary["live"]
        print("  Section: live packet")
        print(f"  Action: {live['action']} | reason={live['reason']}")
        print(f"  Endpoint: {live['endpoint']}")
        print(f"  Rollback: {live['rollback_path']} | streaming={live['streaming_enabled']}")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: ready, empty, disabled, live")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} expected={row['expected']} | rollback={row['rollback']} | reason={row['reason']}")
    print("  Execute: False")



def print_voice_telemetry_guard_status(vts=None, voice=None):
    summary = phase23_1_guard_summary(vts, voice)
    print("🧪 Phase 23.1 Voice Telemetry Guard")
    print("  Action: kiểm telemetry voice latency, không gọi ElevenLabs/voice/execute.")
    print(f"  Progress: {phase23_1_progress_percent(summary)}%")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for name, passed, detail in summary["rows"]:
        print(f"  {name} | {'pass' if passed else 'fail'} | {detail}")
    print("  Telemetry regression:")
    for row in summary["test_rows"]:
        print(f"    {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} reason={row['reason']}")



def print_voice_telemetry_status(vts=None, voice=None):
    summary = phase23_1_guard_summary(vts, voice)
    snapshot = summary["snapshot"]
    live = summary["live"]
    print("📏 Voice Telemetry Status")
    print("  Action: read-only; đọc telemetry voice latency, không gọi ElevenLabs/voice.")
    print(f"  Phase 23.1 Progress: {phase23_1_progress_percent(summary)}%")
    print(f"  Live: action={live['action']} | reason={live['reason']}")
    print(f"  Last: strategy={snapshot.get('last_tts_strategy')} text_len={snapshot.get('last_tts_text_len')} chunks={snapshot.get('last_tts_chunks')} audio_paths={snapshot.get('last_tts_audio_paths')}")
    print(f"  Timing: prepare={snapshot.get('last_tts_prepare_ms')}ms request={snapshot.get('last_tts_request_ms')}ms playback={snapshot.get('last_tts_playback_ms')}ms total={snapshot.get('last_tts_total_ms')}ms")
    print(f"  Cache: hits={snapshot.get('last_tts_cache_hits')} misses={snapshot.get('last_tts_cache_misses')}")
    for name, passed, detail in summary["rows"]:
        print(f"  {name}: {'pass' if passed else 'warn'} | {detail}")
    print("  Rule: Phase 23.1 chỉ đo/đọc telemetry; chưa bật streaming thật.")



def print_voice_telemetry_test(raw_text=None, vts=None, voice=None):
    print("🧪 Voice Telemetry Test")
    print("  Action: read-only; synthetic telemetry classification only, không gọi voice/API.")
    key = (raw_text or "").strip().lower()
    aliases = {
        "empty": {"empty_telemetry"},
        "cache": {"cache_telemetry"},
        "chunk": {"chunk_telemetry"},
        "slow": {"slow_telemetry"},
    }
    summary = phase23_1_guard_summary(vts, voice)
    if key in {"fields", "surface"}:
        print("  Section: fields")
        for field in PHASE23_1_TELEMETRY_FIELDS:
            print(f"  {field}")
        print("  Execute: False")
        return
    if key == "live":
        snapshot = summary["snapshot"]
        live = summary["live"]
        print("  Section: live telemetry")
        print(f"  Action: {live['action']} | reason={live['reason']}")
        print(f"  Strategy: {snapshot.get('last_tts_strategy')} | total={snapshot.get('last_tts_total_ms')}ms")
        print("  Execute: False")
        return
    rows = summary["test_rows"]
    if key and key != "all":
        allowed = aliases.get(key)
        if allowed:
            rows = [row for row in rows if row["name"] in allowed]
        else:
            print("  Status: not_found")
            print("  Cases: empty, cache, chunk, slow, fields, live")
            print("  Execute: False")
            return
    print(f"  Summary: {sum(1 for row in rows if row['passed'])}/{len(rows)} pass")
    for row in rows:
        print(f"  {'pass' if row['passed'] else 'fail'} | {row['name']} | action={row['action']} expected={row['expected']} | reason={row['reason']}")
    print("  Execute: False")



def _normalize_user_input_key(text):
    return text.strip(" \t\r\n.,!?;:，。！？…")


def check_duplicate_user_input(text):
    """Return (should_skip, reason, repeat_count) for an incoming user text.

    Skips if the same normalized text arrived >= 4 times within
    LAST_USER_INPUT_DEDUP_WINDOW seconds. Catches mic/VTube hotkey stuck spam.
    """
    key = _normalize_user_input_key(text)
    if not key:
        return False, "empty", 0

    now = time.time()
    cutoff = now - LAST_USER_INPUT_DEDUP_WINDOW
    LAST_USER_INPUTS[:] = [
        (ts, k) for ts, k in LAST_USER_INPUTS
        if ts >= cutoff and k
    ][:LAST_USER_INPUT_DEDUP_LIMIT * 4]

    LAST_USER_INPUTS.append((now, key))
    del LAST_USER_INPUTS[:-LAST_USER_INPUT_DEDUP_LIMIT * 4:]

    same = [ts for ts, k in LAST_USER_INPUTS if k == key]
    repeat_count = len(same)

    if repeat_count >= LAST_USER_INPUT_DEDUP_LIMIT:
        return True, f"duplicate_input_x{repeat_count}", repeat_count
    return False, "ok", repeat_count


def handle_admin(text):
    if not text.startswith("/admin/"):
        return False
    command = text.replace("/admin/", "").strip()
    if command == "clear":
        with memory_lock:
            memory["long_term"] = []
        save_memory_async()
        print(" đã xoá luật")
        return True
    if command:
        with memory_lock:
            memory["long_term"].append(command)
            memory["long_term"] = memory["long_term"][-50:]
        save_memory_async()
        print(" đã lưu:", command)
        return True
    return False

# --- Autonomy loop singleton (Phase A3 wiring) ---------------------------
# The loop drives idle banter / observer aware / stream host output.
# Created once at module import, started in main(). Slash commands
# /autonomy-status, /autonomy-pause, /autonomy-resume control it.
# Safety: no TTS, no VTS, no ElevenLabs until Phase A4 wires the
# express layer. In A3 the loop's express path is a print-only stub.
AUTONOMY_LOOP = AutonomyLoop()
AUTONOMY_EXPRESS = AUTONOMY_LOOP.express  # share the same instance


def autonomy_note_user_command():
    """Hook every slash command so the loop starts its 10s cooldown
    and updates observer state. Safe to call even if the loop has
    not been started yet (the cooldown timer is just stored).
    """
    try:
        AUTONOMY_LOOP.note_user_command()
    except Exception as exc:
        print(f"  [autonomy] note_user_command failed: {exc}")


def awareness_memory_note_user_chat(user_text: str, nana_text: str, awareness: dict | None = None):
    """Task 7C Fix V2: Record a chat interaction into awareness memory.

    Called after every non-slash chat reply. Safe to call even if
    awareness memory is unavailable; exceptions are swallowed.

    V2: After recording, lock the browser focus using the captured snapshot.
    This prevents the next browser snapshot from drifting to a different tab/video
    while the user is still on the recorded content.
    """
    try:
        mem = get_awareness_memory()
        moment = mem.record_chat(
            user_text=user_text,
            nana_text=nana_text,
            awareness=awareness,
            importance="medium",
        )
        # V2: Lock focus after recording so subsequent snapshots don't drift
        if moment is not None and awareness is not None:
            try:
                lock_focus(awareness, reason="awareness_record")
            except Exception:
                pass
    except Exception:
        pass


def autonomy_status_snapshot() -> dict:
    """Return a small dict for /autonomy-status to print."""
    try:
        cadence = AUTONOMY_LOOP.cadence
        events = cadence.events
        accepted = sum(1 for e in events if e.accepted)
        rejected = sum(1 for e in events if not e.accepted)

        # Top 3 reject reasons by count.
        tallies = AUTONOMY_LOOP.reject_tallies
        top_rejects = sorted(tallies.items(), key=lambda kv: kv[1], reverse=True)[:3]

        # Live gate diagnostics for the current tick snapshot.
        try:
            ctx_dict = AUTONOMY_LOOP.observer.get_context()
            from nana.autonomy.expression_gate import GateContext
            ctx = GateContext(
                mode="idle_banter",
                user_is_typing=ctx_dict.get("user_is_typing", False),
                game_active=ctx_dict.get("game_active", False),
                command_in_flight=ctx_dict.get("command_in_flight", False),
                audio_busy=ctx_dict.get("audio_busy", False),
                mood_affection=ctx_dict.get("mood_affection", 0.5),
                scene_relevance=ctx_dict.get("scene_relevance", 0.5),
                silence_duration_s=ctx_dict.get("silence_duration_s", 0.0),
                silence_window_s=ctx_dict.get("silence_window_s", 60.0),
                forced_mode=ctx_dict.get("forced_mode"),
                jitter_value=ctx_dict.get("jitter_value", 0.5),
            )
            gate_debug = AUTONOMY_LOOP.gate.evaluate_debug(ctx)
        except Exception:
            gate_debug = None

        return {
            "state": AUTONOMY_LOOP.state.value,
            "paused": cadence.paused,
            "tick_count": AUTONOMY_LOOP._tick_count,
            "accepted": accepted,
            "rejected": rejected,
            "last_event": events[-1] if events else None,
            "top_reject_reasons": top_rejects,
            "gate_diagnostics": gate_debug,
        }
    except Exception as exc:
        return {"state": "unknown", "error": str(exc)}


# --- Real autonomy backends (Phase A4 wiring) --------------------------
# These functions bridge the AutonomyExpress's pluggable backend
# surface to the existing voice.engine, integrations.vts, and
# (best-effort) subtitle path. Every one of them catches its own
# exceptions so a misbehaving backend can never crash the autonomy
# thread.

def _real_autonomy_tts(voice, text, on_done=None):
    """Send text to voice.say and poll voice.snapshot() to fire
    on_done() after playback ends.

    Polling (rather than a callback) is the safest way to wire into
    the existing voice engine, which has no on_done hook. Polling
    at 100ms is light; the worst case is a 30s timeout that still
    calls on_done so lipsync_stop is guaranteed.
    """
    if voice is None:
        if on_done is not None:
            try:
                on_done()
            except Exception:
                pass
        return
    try:
        if not text or not text.strip():
            if on_done is not None:
                on_done()
            return
        voice.say(text)
    except Exception as exc:
        print(f"  [autonomy] TTS enqueue failed: {exc}")
        if on_done is not None:
            try:
                on_done()
            except Exception:
                pass
        return
    if on_done is None:
        return
    # Polling thread
    import threading
    def _poller():
        deadline = time.time() + 30.0
        while time.time() < deadline:
            try:
                snap = voice.snapshot() if voice else None
                if snap is not None and not snap.get("speaking"):
                    try:
                        on_done()
                    except Exception:
                        pass
                    return
            except Exception:
                # snapshot raised; treat as 'idle' to be safe
                try:
                    on_done()
                except Exception:
                    pass
                return
            time.sleep(0.1)
        # Timeout fallback: still call on_done so lipsync stops.
        try:
            on_done()
        except Exception:
            pass
    threading.Thread(target=_poller, daemon=True, name="autonomy-tts-poller").start()


def _get_last_keystroke_time():
    """Return the last keystroke monotonic time from the runtime context."""
    from nana.runtime.context import context_state, context_lock
    with context_lock:
        return context_state.get("last_keystroke", 0.0)


def _real_autonomy_lipsync_stop(voice):
    """Force the lipsync mouth value to 0.0 so Nana stops moving her
    mouth. Critical for VTS-only payloads where there is no TTS to
    drive on_done.
    """
    if voice is None:
        return
    try:
        lipsync = getattr(voice, "lipsync", None)
        if lipsync is not None:
            lipsync.stop()
    except Exception as exc:
        print(f"  [autonomy] lipsync_stop failed: {exc}")


def _real_autonomy_vts(hotkey_id):
    """Trigger a VTS hotkey via the existing VTS runtime. The async
    request is scheduled on the running event loop; autonomy never
    blocks waiting for VTS to reply.
    """
    try:
        vts = get_vts_runtime()
        if vts is None:
            return
        if not getattr(vts, "connected", False):
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(vts.request({
                    "apiName": "VTubeStudioPublicAPI",
                    "apiVersion": "1.0",
                    "requestID": f"autonomy_hotkey_{hotkey_id}_{int(time.time()*1000)}",
                    "messageType": "HotkeyTriggerRequest",
                    "data": {"hotkeyID": hotkey_id},
                }))
            else:
                # No running loop (rare in main path): skip silently.
                pass
        except RuntimeError:
            # No event loop in this thread; autonomy runs in a daemon
            # thread. We catch here rather than crashing the loop.
            pass
    except Exception as exc:
        print(f"  [autonomy] VTS hotkey {hotkey_id} failed: {exc}")


def _real_autonomy_subtitle(text):
    """Write a single line to the public subtitle stream. In A4 we
    just log; a real OBS-readable file write is wired in a later
    phase. We deliberately do not raise; the autonomy loop should
    never block on subtitle IO.
    """
    try:
        log_event("autonomy_subtitle", text or "")
    except Exception:
        pass


# --- Autonomy mode knob (Phase A4 wiring) ------------------------------
# /autonomy-mode <name> flips the prefer_ultra_short flag.

def autonomy_set_mode(mode: str) -> str:
    """Return the new mode label, or 'unknown' if the input is bad."""
    mode = (mode or "").strip().lower()
    if mode in ("ultra-short", "ultra_short", "ultrashort", "short"):
        AUTONOMY_EXPRESS.set_prefer_ultra_short(True)
        return "ultra_short"
    if mode in ("full", "normal", "all"):
        AUTONOMY_EXPRESS.set_prefer_ultra_short(False)
        return "full"
    return "unknown"


browser_reader = BrowserContextReader()
vision_previewer = VisionPreviewer()
CASUAL_LAST_REPLY = None
LAST_VISION_DESCRIPTION = None
AUTONOMY_LOCK_PHASE = "Phase 5-10"
AUTONOMY_LOCK_RULE = "no_autonomy_no_semi_autonomy"
AUTONOMY_LOCK_NOTE = "chỉ quan sát/preview/draft theo lệnh; Phase 11 mới mở tự trị có whitelist+confirm"
AUTONOMY_LOCK_BLOCKED_ACTIONS = {
    "browser.click",
    "browser.type",
    "social.type_draft",
}
BROWSER_REFRESH_INFLIGHT = None
RUNTIME_RECONCILE_TASK = None



def set_runtime_turn_state(status, reason="manual", source="runtime"):
    RUNTIME_TURN_STATE.update({
        "status": status,
        "last_update": time.time(),
        "last_reason": reason,
        "source": source,
    })
    return dict(RUNTIME_TURN_STATE)


def runtime_turn_state_snapshot():
    snapshot = dict(RUNTIME_TURN_STATE)
    snapshot["age"] = max(0.0, time.time() - float(snapshot.get("last_update") or 0))
    return snapshot


    print("  Game adapters: /game-adapter-status | /game-adapter-on | /game-adapter-off | /game-adapter-auto | /osu-adapter-status | /osu-adapter-on | /osu-adapter-off | /osu-adapter-auto | /osu-calibration-status | /osu-coordinate-preview")
    print("  Safety: /autonomy-lock")
    print("  Autonomy: /autonomy-status | /autonomy-pause | /autonomy-resume | /autonomy-mode <ultra-short|full>")
    print("  Browser: /br | /br-deep | /video-context | /reconcile | /browser | /dom | /focus")
    print("  Persona: /vibe-status | /residue | /reset-vibe | /focus-mode | /technical-mode | /social-mode")
    print("  Presence: /presence | /presence-on | /presence-off | /presence-reset | /attention")
    print("  Context: /context-confidence | /context-priority | /evidence-trace | /context-recovery | /video-context | /context-preview")
    print("  Vision: /vision-preview | /vision-describe | /reconcile-check | /vision-cache | /vision-cache-clear")
    print("  Social draft: /social-target | /social-classify | /social-guard-status | /social-guard-failures | /classify-text <text> | /classify-expect <style> | <text> | /social-draft-test <text> | /social-draft-vision <text> | /drafts | /draft-quality")
    print("  Phase 7 dry-run: /dry-run <text> | /plan-preview <text> | /plan-pending | /plan-confirm <id> | /plan-cancel <id> | /plan-log | /dry-run-guard-status | /plan-guard-status")
    print("  Phase 8 broker: /phase8-status | /broker-matrix | /action-contract <action> | /broker-guard-status | /pre-exec-check <action> | /action-trace <text>")
    print("  Phase 9 audit: /phase9-status | /audit-log | /audit-replay [id] | /audit-review [id] | /audit-clear | /audit-guard-status")
    print("  Phase 10 final: /phase10-status | /phase10-guard-status | /phase10-ready | /ship-checklist")
    print("  Phase 10.1 CNS: /runtime-map | /cns-map | /phase10-1-status | /phase10-1-ready")
    print("  Phase 10.2 router: /command-router-status | /command-route-test <text> | /command-normalize-test <text> | /phase10-2-ready")
    print("  Phase 10.3 state: /runtime-state-schema | /state-schema-guard-status | /phase10-3-ready")
    print("  Phase 10.4 events: /event-store-status | /event-log | /event-replay [id] | /phase10-4-ready")
    print("  Phase 10.5 scheduler: /scheduler-status | /scheduler-test | /scheduler-guard-status | /phase10-5-ready")
    print("  Phase 10.6 recovery: /recovery-governor-status | /recovery-test | /recovery-governor-guard-status | /phase10-6-ready")
    print("  Phase 10.7 memory: /memory-governance-status | /memory-governance-test | /memory-governance-guard-status | /phase10-7-ready")
    print("  Phase 10.8 presence: /presence-stability-status | /presence-stability-test | /presence-stability-guard-status | /phase10-8-ready")
    print("  Phase 10.9 social/vision: /social-vision-decouple-status | /social-vision-decouple-test | /social-vision-decouple-guard-status | /phase10-9-ready")
    print("  Phase 10.10 CNS gate: /cns-gate-status | /cns-gate-test | /cns-gate-guard-status | /phase10-10-ready")
    print("  Phase 11.1 runtime stress: /runtime-stress-status | /runtime-stress-test | /runtime-stress-guard-status | /phase11-1-ready")
    print("  Phase 11.2 trust calibration: /trust-calibration-status | /trust-calibration-test | /trust-calibration-guard-status | /phase11-2-ready")
    print("  Phase 11.3 target lock: /target-lock-status | /target-lock-test | /target-lock-guard-status | /phase11-3-ready")
    print("  Phase 11.4 sandbox boundary: /sandbox-boundary-status | /sandbox-boundary-test | /sandbox-boundary-guard-status | /phase11-4-ready")
    print("  Phase 11.5 executor rehearsal: /executor-rehearsal-status | /executor-rehearsal-test | /executor-rehearsal-guard-status | /phase11-5-ready")
    print("  Phase 11.6 bounded pilot: /bounded-pilot-status | /bounded-pilot-preview <text> | /bounded-pilot-test | /phase11-6-ready")
    print("  Phase 11.7 permission ledger: /permission-ledger-status | /permission-ledger-test | /permission-ledger-guard-status | /phase11-7-ready")
    print("  Phase 11.8 session review: /session-review-status | /session-review-preview <text> | /session-review-test | /phase11-8-ready")
    print("  Phase 11.9 companion safety: /companion-safety-status | /companion-safety-check <text> | /companion-safety-test | /phase11-9-ready")
    print("  Phase 11.10 final gate: /phase11-gate-status | /phase11-gate-test | /phase11-gate-guard-status | /phase11-ready")
    print("  Phase 12.1 attention state: /attention-state-status | /attention-state-test | /attention-state-guard-status | /phase12-1-ready")
    print("  Phase 12.2 attention rhythm: /attention-rhythm-status | /attention-rhythm-test | /attention-rhythm-guard-status | /phase12-2-ready")
    print("  Phase 12.3 presence entropy: /presence-entropy-status | /presence-entropy-test | /presence-entropy-guard-status | /phase12-3-ready")
    print("  Phase 12.4 attention memory: /attention-memory-status | /attention-memory-test | /attention-memory-guard-status | /phase12-4-ready")
    print("  Phase 12.5 presence gate: /presence-gate-status | /presence-gate-test | /presence-gate-guard-status | /phase12-ready")
    print("  Phase 13.1 memory v3: /memory-v3-status | /memory-v3-test | /memory-v3-guard-status | /phase13-1-ready")
    print("  Phase 13.2 shared experience: /shared-experience-status | /shared-experience-test | /shared-experience-guard-status | /phase13-2-ready")
    print("  Phase 13.3 memory conflict: /memory-conflict-status | /memory-conflict-test | /memory-conflict-guard-status | /phase13-3-ready")
    print("  Phase 13.4 memory decay: /memory-decay-status | /memory-decay-test | /memory-decay-guard-status | /phase13-4-ready")
    print("  Phase 13.5 memory gate: /memory-gate-status | /memory-gate-test | /memory-gate-guard-status | /phase13-ready")
    print("  Phase 14.1 dialogue energy: /dialogue-energy-status | /dialogue-energy-test | /dialogue-energy-guard-status | /phase14-1-ready")
    print("  Phase 14.2 response shape: /response-shape-status | /response-shape-test | /response-shape-guard-status | /phase14-2-ready")
    print("  Phase 14.3 dialogue drift: /dialogue-drift-status | /dialogue-drift-check <reply> | /dialogue-drift-test | /phase14-3-ready")
    print("  Phase 14.4 conversation rhythm: /conversation-rhythm-status | /conversation-rhythm-test | /conversation-rhythm-guard-status | /phase14-4-ready")
    print("  Phase 14.5 dialogue gate: /dialogue-gate-status | /dialogue-gate-test | /dialogue-gate-guard-status | /phase14-ready")
    print("  Phase 15.1 daily frame: /daily-frame-status | /daily-frame-test | /daily-frame-guard-status | /phase15-1-ready")
    print("  Phase 15.2 day continuity: /day-continuity-status | /day-continuity-test | /day-continuity-guard-status | /phase15-2-ready")
    print("  Phase 15.3 habit candidates: /habit-candidate-status | /habit-candidate-test | /habit-candidate-guard-status | /phase15-3-ready")
    print("  Phase 15.4 recovery continuity: /recovery-continuity-status | /recovery-continuity-test | /recovery-continuity-guard-status | /phase15-4-ready")
    print("  Phase 15.5 daily loop gate: /daily-loop-status | /daily-loop-test | /daily-loop-guard-status | /phase15-ready")
    print("  Phase 16.1 reflective state: /reflective-state-status | /reflective-state-test | /reflective-state-guard-status | /phase16-1-ready")
    print("  Phase 16.2 grounded reflection: /grounded-reflection-status | /grounded-reflection-test | /grounded-reflection-guard-status | /phase16-2-ready")
    print("  Phase 16.3 shared recall: /shared-recall-status | /shared-recall-test | /shared-recall-guard-status | /phase16-3-ready")
    print("  Phase 16.4 reflection safety: /reflection-safety-status | /reflection-safety-test | /reflection-safety-guard-status | /phase16-4-ready")
    print("  Phase 16.5 reflective gate: /reflective-gate-status | /reflective-gate-test | /reflective-gate-guard-status | /phase16-ready")
    print("  Phase 17.1 output candidate: /output-candidate-status | /output-candidate-preview <text> | /output-candidate-test | /phase17-1-ready")
    print("  Phase 17.2 reflection injection: /reflection-injection-status | /reflection-injection-preview <text> | /reflection-injection-test | /phase17-2-ready")
    print("  Phase 17.3 silence/hold enforcement: /silence-hold-status | /silence-hold-preview <text> | /silence-hold-test | /phase17-3-ready")
    print("  Phase 17.4 output gate: /output-gate-status | /output-gate-test | /output-gate-guard-status | /phase17-ready")
    print("  Phase 18.1 companion integration: /companion-integration-status | /companion-integration-test | /companion-integration-guard-status | /phase18-1-ready")
    print("  Phase 18.2 companion consistency: /companion-consistency-status | /companion-consistency-test | /companion-consistency-guard-status | /phase18-2-ready")
    print("  Phase 18.3 companion response preview: /companion-response-status | /companion-response-preview <text> | /companion-response-test | /phase18-3-ready")
    print("  Phase 18.4 companion live gate: /companion-live-status | /companion-live-test | /companion-live-guard-status | /phase18-ready")
    print("  Phase 19.1 live reply permission: /live-reply-status | /live-reply-test | /live-reply-guard-status | /phase19-1-ready")
    print("  Phase 19.2 speech dispatch dry-run: /speech-dispatch-status | /speech-dispatch-test | /speech-dispatch-guard-status | /phase19-2-ready")
    print("  Phase 19.3 real reply bridge: /reply-bridge-status | /reply-bridge-test | /reply-bridge-guard-status | /phase19-3-ready")
    print("  Phase 19.4 reply cooldown guard: /reply-cooldown-status | /reply-cooldown-test | /reply-cooldown-guard-status | /phase19-4-ready")
    print("  Phase 19.5 live reply gate: /live-reply-gate-status | /live-reply-gate-test | /live-reply-gate-guard-status | /phase19-ready")
    print("  Phase 20.1 voice binding baseline: /voice-binding-status | /voice-binding-test | /voice-binding-guard-status | /phase20-1-ready")
    print("  Phase 20.2 voice dispatch gate: /voice-dispatch-status | /voice-dispatch-test | /voice-dispatch-guard-status | /phase20-2-ready")
    print("  Phase 20.3 expression dispatch: /expression-dispatch-status | /expression-dispatch-test | /expression-dispatch-guard-status | /phase20-3-ready")
    print("  Phase 20.4 live speech safety: /live-speech-safety-status | /live-speech-safety-test | /live-speech-safety-guard-status | /phase20-4-ready")
    print("  Phase 20.5 final voice gate: /voice-gate-status | /voice-gate-test | /voice-gate-guard-status | /phase20-ready")
    print("  Phase 21.1 live voice control: /live-voice-control-status | /live-voice-control-test | /live-voice-control-guard-status | /phase21-1-ready")
    print("  Phase 21.2 direct voice pilot: /direct-voice-pilot-status | /direct-voice-pilot-test | /direct-voice-pilot-guard-status | /phase21-2-ready")
    print("  Phase 21.3 guarded voice dispatch: /guarded-voice-dispatch-status | /guarded-voice-dispatch-test | /guarded-voice-dispatch-guard-status | /phase21-3-ready")
    print("  Phase 21.4 live path replacement: /live-path-replacement-status | /live-path-replacement-test | /live-path-replacement-guard-status | /phase21-4-ready")
    print("  Phase 21.5 final live voice gate: /live-voice-gate-status | /live-voice-gate-test | /live-voice-gate-guard-status | /phase21-ready")
    print("  Phase 22.1 voice latency baseline: /voice-latency-baseline-status | /voice-latency-baseline-test | /voice-latency-baseline-guard-status | /phase22-1-ready")
    print("  Phase 22.2 voice latency design: /voice-latency-design-status | /voice-latency-design-test | /voice-latency-design-guard-status | /phase22-2-ready")
    print("  Phase 22.3 voice engine patch dry-run: /voice-engine-patch-status | /voice-engine-patch-test | /voice-engine-patch-guard-status | /phase22-3-ready")
    print("  Phase 22.4 voice engine implementation: /voice-engine-impl-status | /voice-engine-impl-test | /voice-engine-impl-guard-status | /phase22-4-ready")
    print("  Phase 22.5 voice latency gate: /voice-latency-gate-status | /voice-latency-gate-test | /voice-latency-gate-guard-status | /phase22-ready")
    print("  Phase 23.1 voice telemetry baseline: /voice-telemetry-status | /voice-telemetry-test | /voice-telemetry-guard-status | /phase23-1-ready")
    print("  Phase 23.2 voice streaming decision: /voice-streaming-decision-status | /voice-streaming-decision-test | /voice-streaming-decision-guard-status | /phase23-2-ready")
    print("  Phase 23.3 voice streaming dry-run: /voice-streaming-dry-run-status | /voice-streaming-dry-run-test | /voice-streaming-dry-run-guard-status | /phase23-3-ready")
    print("  Phase 23.4 voice stream safety: /voice-stream-safety-status | /voice-stream-safety-test | /voice-stream-safety-guard-status | /phase23-4-ready")
    print("  Phase 23.5 voice stream gate: /voice-stream-gate-status | /voice-stream-gate-test | /voice-stream-gate-guard-status | /phase23-ready")
    print("  Phase 24.1 stream pilot control: /stream-pilot-control-status | /stream-pilot-control-test | /stream-pilot-control-guard-status | /phase24-1-ready")
    print("  Phase 24.2 guarded stream call dry-run: /guarded-stream-call-status | /guarded-stream-call-test | /guarded-stream-call-guard-status | /phase24-2-ready")
    print("  Phase 24.3 controlled stream pilot: /controlled-stream-pilot-status | /controlled-stream-pilot-test | /controlled-stream-pilot-guard-status | /phase24-3-ready")
    print("  Phase 24.4 stream rollback/timeout: /stream-rollback-status | /stream-rollback-test | /stream-rollback-guard-status | /phase24-4-ready")
    print("  Phase 24.5 final controlled stream gate: /controlled-stream-gate-status | /controlled-stream-gate-test | /controlled-stream-gate-guard-status | /phase24-ready")
    print("  Phase 25.1 live stream measurement baseline: /live-stream-measurement-status | /live-stream-measurement-test | /live-stream-measurement-guard-status | /phase25-1-ready")
    print("  Phase 25.2 stream pilot enable gate: /stream-pilot-enable-status | /stream-pilot-enable-test | /stream-pilot-enable-guard-status | /phase25-2-ready")
    print("  Phase 25-32 final companion gates: /phase25-ready ... /phase32-ready")
    # Stardew help removed: see V2 commands (/stardew-help, /stardew-status, etc.)
    print("  Draft action: /draft-show <id> | /draft-confirm <id> | /draft-cancel <id>")
    print("  Memory: /memory-status | /memory-review | /memory-compact-preview | /memory-action")
    print("  Recovery: /recovery | /recovery-clear")
    print("  Full phase map: /phase4")


def print_queue_state():
    snapshot = runtime_queue.snapshot()
    age = snapshot["last_user_input_age"]
    age_text = "never" if age is None else f"{age:.1f}s ago"
    print("📚 Runtime Queue")
    print(f"  P0 active: {bool(snapshot['active_p0'])}")
    print(f"  Last user input: {age_text}")
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


def print_action_registry():
    print("🛡️ Action Registry")
    for action in action_registry.list_actions():
        print(
            "  "
            f"{action.name} | permission={action.permission.value} | "
            f"cost={action.cost_estimate} | rollback={action.rollback}"
        )
        if action.description:
            print(f"    {action.description}")


def print_nana_status():
    now = time.time()
    with context_lock:
        zone = context_state.get("active_zone")
        app = context_state.get("active_app")
        idle = context_state.get("idle_state")
        flow = context_state.get("in_flow")
        browser = dict(context_state.get("browser", {}))
        proactive = dict(context_state.get("proactive", {}))
        state_snapshot = dict(context_state)
        state_snapshot["browser"] = browser
    with memory_lock:
        long_term = list(memory.get("long_term", []))
        short_term = list(memory.get("short_term", []))
        chat_log = list(memory.get("chat_log", []))
        labels = dict(memory.get("memory_labels", {}))

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
    age_text = "None" if age is None else f"{age:.1f}s"
    browser_fresh = bool(browser.get("available")) and age is not None and age <= BROWSER_FRESH_SECONDS

    attention_context = context_for_attention(state_snapshot)
    attention = evaluate_attention_window(attention_context)
    attention_line = format_attention_status(attention)[1:4]

    last_reaction = proactive.get("last_browser_reaction_time") or 0
    reaction_text = "never" if not last_reaction else f"{max(0.0, now - last_reaction):.1f}s ago"
    active_labels = 0
    active_keys = {memory_item_key(item) for item in long_term}
    for key in labels:
        if key in active_keys:
            active_labels += 1

    recovery = recovery_latest_summary() or "none"

    print("🧩 Nana Status")
    print(f"  Runtime: zone={zone} | app={app} | idle={idle} | flow={flow} | confidence={get_confidence():.2f}")
    print(
        "  Persona: "
        f"mode={persona.get('mode')} | intensity={persona.get('personality_intensity')}->{persona.get('target_intensity')} | "
        f"clamp={temperature_clamp(persona)} | residue={persona.get('residue_level')} "
        f"({format_sources_value(persona.get('residue_sources'))}) | manual={manual_text}"
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
    for line in attention_line:
        print(f"  {line.strip()}")
    print(
        "  Browser: "
        f"available={browser.get('available')} | fresh={browser_fresh} | age={age_text} | "
        f"kind={browser.get('kind')} | title={shorten_line(browser.get('title'), 90)}"
    )
    print(f"  Recovery: {recovery}")
    print(
        "  Memory: "
        f"long={len(long_term)}/50 | short={len(short_term)}/16 | chat={len(chat_log)}/50 | "
        f"labels={active_labels}/{len(labels)} active"
    )
    print(f"  Autonomy lock: {AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}")
    print("  Detail: /vibe-status | /presence | /attention | /recovery | /memory-status | /status")


def print_vts_expression_policy_status():
    from nana.config import (
        VTS_EXPRESSION_COOLDOWN_SECONDS,
        VTS_EXPRESSION_DEFAULT_CHANCE,
        VTS_EXPRESSION_RESET_DELAY_SECONDS,
        VTS_EXPRESSION_RESET_FALLBACK_HOTKEY,
        VTS_EXPRESSION_RESET_TIMEOUT_SECONDS,
    )

    print("🎭 VTS Expression Policy")
    print("  Action: read-only; không gọi VTube Studio.")
    print(f"  Cooldown: {VTS_EXPRESSION_COOLDOWN_SECONDS:.1f}s")
    print(f"  Default chance: {VTS_EXPRESSION_DEFAULT_CHANCE:.2f}")
    print(f"  Reset after voice idle: {VTS_EXPRESSION_RESET_DELAY_SECONDS:.1f}s")
    print(f"  Reset timeout: {VTS_EXPRESSION_RESET_TIMEOUT_SECONDS:.1f}s")
    print(f"  Fallback hotkey toggle: {VTS_EXPRESSION_RESET_FALLBACK_HOTKEY}")
    print("  Rule: chat/ambient/runtime expression sẽ tự reset sau khi voice queue idle.")


def print_speech_shape_test(raw_text=""):
    sample = raw_text or "reply=Chào Ba! 😊 Có gì cần hỏi không? | user=Chào Nana, nói ngắn thôi nha."
    user_text = ""
    reply_text = sample
    if "|" in sample:
        parts = [part.strip() for part in sample.split("|", 1)]
        reply_text, user_text = parts[0], parts[1]
    if reply_text.lower().startswith("reply="):
        reply_text = reply_text.split("=", 1)[1].strip()
    if user_text.lower().startswith("user="):
        user_text = user_text.split("=", 1)[1].strip()
    shaped = shape_chat_reply(reply_text, user_text=user_text)
    print("🧪 Speech Shape Test")
    print("  Action: read-only; không gọi model/voice/VTube.")
    print(f"  User: {user_text or 'none'}")
    print(f"  Input: {reply_text}")
    print(f"  Output: {shaped}")


def print_phase5_status():
    now = time.time()
    with context_lock:
        browser = dict(context_state.get("browser", {}))
        proactive = dict(context_state.get("proactive", {}))
        state_snapshot = dict(context_state)
        state_snapshot["browser"] = browser
    with memory_lock:
        long_term = list(memory.get("long_term", []))
        short_term = list(memory.get("short_term", []))
        chat_log = list(memory.get("chat_log", []))
        labels = dict(memory.get("memory_labels", {}))

    persona = persona_state_snapshot()
    residue = int(persona.get("residue_level") or 0)
    sources = dict(persona.get("residue_sources") or {})
    age = browser_age_seconds(browser)
    browser_fresh = bool(browser.get("available")) and age is not None and age <= BROWSER_FRESH_SECONDS
    recovery = recovery_latest_summary()
    attention_context = context_for_attention(state_snapshot)
    attention = evaluate_attention_window(attention_context)
    output_dir = vision_previewer.output_dir
    vision_files = list(output_dir.glob("vision_focus_*.png")) if output_dir.exists() else []
    latest_vision = "none"
    if LAST_VISION_DESCRIPTION:
        vision_age = now - LAST_VISION_DESCRIPTION.get("time", 0)
        latest_vision = f"fresh {vision_age:.0f}s" if vision_age <= 180 else f"stale {vision_age:.0f}s"
    active_keys = {memory_item_key(item) for item in long_term}
    active_labels = sum(1 for key in labels if key in active_keys)

    target_source = build_social_draft_source(
        DEFAULT_SOCIAL_CLASSIFY_REQUEST,
        broker_context={
            "browser_title": browser.get("title"),
            "browser_kind": browser.get("kind"),
            "browser_social_post_text": browser.get("social_post_text"),
            "browser_social_vibe": browser.get("social_vibe"),
        },
        vision_description=LAST_VISION_DESCRIPTION.get("text") if LAST_VISION_DESCRIPTION else None,
    )
    media_mode, reaction_style, guard_hint = social_source_classification(
        target_source,
        broker_context={
            "browser_kind": browser.get("kind"),
            "browser_social_post_text": browser.get("social_post_text"),
            "browser_social_vibe": browser.get("social_vibe"),
        },
        raw_text=DEFAULT_SOCIAL_CLASSIFY_REQUEST,
        vision_description=LAST_VISION_DESCRIPTION.get("text") if LAST_VISION_DESCRIPTION else None,
    )
    guard_matrix = social_guard_matrix_summary()
    guard_failures = guard_matrix["failures"]
    guard_detail = f"{guard_matrix['pass_count']}/{guard_matrix['total']} pass"
    if guard_failures:
        guard_detail += f" | first={guard_failures[0]['style']} expected={guard_failures[0]['expected']}"

    checks = [
        ("persona_governor", "pass" if persona.get("mode") else "warn", f"mode={persona.get('mode')} intensity={persona.get('personality_intensity')}->{persona.get('target_intensity')}"),
        ("residue_decay", "pass" if residue <= 35 else "warn", f"residue={residue} sources={format_sources_value(sources)} clamp={temperature_clamp(persona)}"),
        ("presence_rhythm", "pass" if proactive.get("enabled", True) is not None else "warn", f"enabled={proactive.get('enabled', True)} attention={attention.get('window')}/{attention.get('ambient_policy')}"),
        ("recovery_lifecycle", "pass" if not recovery else "warn", recovery or "none"),
        ("memory_governance", "pass" if len(long_term) <= 50 and len(short_term) <= 16 else "warn", f"long={len(long_term)}/50 short={len(short_term)}/16 chat={len(chat_log)}/50 labels={active_labels}/{len(labels)}"),
        ("vision_cache", "pass" if len(vision_files) <= 5 else "warn", f"files={len(vision_files)} latest_description={latest_vision}"),
        ("social_target", "pass" if browser.get("kind") == "social" and (browser.get("social_post_text") or browser.get("social_vibe")) else "observe", f"kind={browser.get('kind')} fresh={browser_fresh}"),
        ("social_classifier", "pass" if guard_hint != "non_social_target" or browser.get("kind") != "social" else "warn", f"media={media_mode} style={reaction_style} guard={guard_hint}"),
        ("social_guard_matrix", "pass" if not guard_failures else "warn", guard_detail),
        ("autonomy_lock", "pass" if AUTONOMY_LOCK_RULE == "no_autonomy_no_semi_autonomy" else "warn", f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
    ]

    print("🧩 Phase 5 Status")
    print("  Goal: ổn định hành vi, debug rõ, chưa bán tự trị/chưa tự trị.")
    print(f"  Autonomy lock: {AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE} | {AUTONOMY_LOCK_NOTE}")
    for name, status, detail in checks:
        print(f"  {name}: {status} | {detail}")
    print("  Commands: /nana-status | /social-target | /social-classify | /social-guard-status | /autonomy-status | /recovery | /memory-status")


def build_phase6_status_model():
    context = broker_context_snapshot()
    vision_text = None
    vision_status = "none"
    if LAST_VISION_DESCRIPTION:
        age = time.time() - LAST_VISION_DESCRIPTION.get("time", 0)
        if age <= 180:
            vision_text = LAST_VISION_DESCRIPTION.get("text")
            vision_status = f"fresh {age:.0f}s"
        else:
            vision_status = f"stale {age:.0f}s"
    policy = context_priority_policy(context=context, vision_description=vision_text)
    reconcile = vision_text_reconcile_report(context=context, vision_description=vision_text)
    context_preview = build_context_budget_preview(context, level="L2")
    missing = social_target_missing_reason(
        DEFAULT_SOCIAL_CLASSIFY_REQUEST,
        context,
        context_preview,
        vision_description=vision_text,
        omit_page_context=False,
    )
    target_source = build_social_draft_source(
        DEFAULT_SOCIAL_CLASSIFY_REQUEST,
        broker_context=context,
        vision_description=vision_text,
    )
    media_mode, reaction_style, guard_hint = social_source_classification(
        target_source,
        broker_context=context,
        raw_text=DEFAULT_SOCIAL_CLASSIFY_REQUEST,
        vision_description=vision_text,
    )
    guard_matrix = social_guard_matrix_summary()
    reconcile_matrix = reconcile_guard_matrix_summary()
    draft_quality = draft_quality_summary()
    runtime_count = RUNTIME_LATENCY.get("browser_refresh_count") or 0
    last_ms = RUNTIME_LATENCY.get("browser_refresh_last_ms")
    avg_ms = None
    if runtime_count:
        avg_ms = RUNTIME_LATENCY.get("browser_refresh_total_ms", 0.0) / runtime_count
    last_ms_text = "none" if last_ms is None else f"{last_ms:.0f}ms"
    avg_ms_text = "none" if avg_ms is None else f"{avg_ms:.0f}ms"
    recovery = recovery_latest_summary()
    social_target_status = "pass" if missing is None else "observe"
    social_target_detail = "ready" if missing is None else missing
    if missing and policy["kind"] != "social":
        social_target_detail = f"{missing} | expected_on_non_social_tab"
    video_context_status = "pass" if policy["kind"] in {"youtube", "video", "music"} else "observe"
    video_context_detail = "available via /video-context" if video_context_status == "pass" else f"kind={policy['kind']}"
    checks = [
        ("runtime_cache", "pass", f"refresh_count={runtime_count} last={last_ms_text} avg={avg_ms_text} cache_hits={RUNTIME_LATENCY.get('browser_refresh_cache_hits', 0)}"),
        ("runtime_reconcile_coalescing", "pass", f"status={RUNTIME_RECONCILE.get('status')} count={RUNTIME_RECONCILE.get('count')} coalesced={RUNTIME_RECONCILE.get('coalesced')}"),
        ("context_confidence", "pass" if policy["overall"] > 0 else "observe", f"overall={policy['overall']:.2f} ({context_confidence_label(policy['overall'])})"),
        ("dynamic_priority", "pass" if policy.get("ranked") else "observe", f"{context_priority_brief(policy)} | state={policy['state']}"),
        ("evidence_trace", "pass", "/evidence-trace | /context-evidence"),
        ("context_recovery", "pass" if missing is None or policy["kind"] != "social" else "observe", context_recovery_next_step(context, missing_reason=missing, vision_description=vision_text)),
        ("video_context", video_context_status, video_context_detail),
        ("social_target", social_target_status, social_target_detail),
        ("social_classifier", "pass" if guard_hint != "non_social_target" or policy["kind"] != "social" else "warn", f"media={media_mode} style={reaction_style} guard={guard_hint}"),
        ("social_guard_matrix", "pass" if not guard_matrix["failures"] else "warn", f"{guard_matrix['pass_count']}/{guard_matrix['total']} pass"),
        ("reconcile_guard_matrix", "pass" if not reconcile_matrix["failures"] else "warn", f"{reconcile_matrix['pass_count']}/{reconcile_matrix['total']} pass"),
        ("regression_fail_trace", "pass", "fail_stage=classifier/fallback/compact_guard | /social-guard-failures"),
        ("classify_expect", "pass", "/classify-expect <style> | <text>"),
        ("reconcile_expect", "pass", "/reconcile-expect <status> | <text> || <vision>"),
        ("vision_text_reconcile", "pass" if reconcile["status"] in {"aligned", "missing_vision", "weak"} else "warn", f"{reconcile['status']} | text={','.join(reconcile['text_groups']) or 'none'} vision={','.join(reconcile['vision_groups']) or 'none'}"),
        ("draft_quality_gate", "pass" if draft_quality["warn_count"] == 0 else "warn", f"{draft_quality['pass_count']}/{draft_quality['total']} pending pass | /draft-quality-test"),
        ("vision_context", "pass" if vision_text or vision_status == "none" else "observe", vision_status),
        ("recovery_lifecycle", "pass" if not recovery else "observe", recovery or "none"),
        ("autonomy_lock", "pass" if AUTONOMY_LOCK_RULE == "no_autonomy_no_semi_autonomy" else "warn", f"{AUTONOMY_LOCK_PHASE} | {AUTONOMY_LOCK_RULE}"),
    ]
    return {
        "policy": policy,
        "reconcile": reconcile,
        "checks": checks,
        "media_mode": media_mode,
        "reaction_style": reaction_style,
        "guard_hint": guard_hint,
        "last_ms_text": last_ms_text,
        "avg_ms_text": avg_ms_text,
    }


def print_phase6_status():
    model = build_phase6_status_model()
    policy = model["policy"]
    reconcile = model["reconcile"]
    checks = model["checks"]
    print("🧩 Phase 6 Status")
    print("  Goal: context hiểu đúng, trace rõ, recovery đúng bước, không bán tự trị/chưa tự trị.")
    print(f"  Runtime: 6R cache/background | refresh last={model['last_ms_text']} avg={model['avg_ms_text']}")
    print(f"  Context: {format_context_priority_summary(policy)}")
    print(f"  Classifier: media={model['media_mode']} | style={model['reaction_style']} | guard={model['guard_hint']}")
    print(f"  Reconcile: {reconcile['status']} | text={','.join(reconcile['text_groups']) or 'none'} | vision={','.join(reconcile['vision_groups']) or 'none'}")
    for name, status, detail in checks:
        print(f"  {name}: {status} | {detail}")
    print("  Commands: /context-confidence | /context-priority | /evidence-trace | /context-recovery | /video-context | /reconcile-check | /reconcile-expect | /reconcile-guard-status | /social-target | /social-classify | /social-guard-status | /social-guard-failures | /classify-expect | /draft-quality | /draft-quality-test | /runtime-status")


def print_phase6_ready():
    model = build_phase6_status_model()
    checks = model["checks"]
    blocking = []
    observes = []
    for name, status, detail in checks:
        if status == "warn":
            blocking.append((name, detail))
        elif status == "observe":
            observes.append((name, detail))
    ready = not blocking
    print("✅ Phase 6 Ready" if ready else "⚠️ Phase 6 Ready")
    print("  Goal: context/runtime/social guard đủ ổn để sang Phase 7 dry-run planning.")
    print(f"  Ready: {ready}")
    print(f"  Blocking: {len(blocking)}")
    if blocking:
        for name, detail in blocking:
            print(f"    {name}: {detail}")
    print(f"  Observe: {len(observes)}")
    for name, detail in observes[:4]:
        print(f"    {name}: {detail}")
    print("  Regression: social_guard + reconcile_guard + draft_quality checked.")
    print("  Autonomy: locked Phase 5-10; Phase 7 remains preview/dry-run only.")


def print_autonomy_lock_status():
    print("🔒 Autonomy Lock")
    print(f"  Scope: {AUTONOMY_LOCK_PHASE}")
    print(f"  Rule: {AUTONOMY_LOCK_RULE}")
    print(f"  Note: {AUTONOMY_LOCK_NOTE}")
    print("  Allowed now:")
    print("    observe/read context only when Ba asks")
    print("    preview/classify/debug status")
    print("    draft-only social output with confirm gate")
    print("    recovery hints and memory review preview")
    print("  Blocked until Phase 11:")
    print("    self-started workflows or background routines")
    print("    public actions: post/reply/like/follow/share")
    print("    opening apps, typing, clicking, or running commands by itself")
    print("    long-term memory writes/deletes without Ba confirming")
    print("  Phase 11 entry condition: whitelist + confirm + action log + kill switch.")


def autonomy_lock_block_reason(action_name):
    if action_name in AUTONOMY_LOCK_BLOCKED_ACTIONS:
        return f"{AUTONOMY_LOCK_RULE}:{action_name}"
    return None


def print_residue_status():
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
        print(
            "    "
            f"{source}: {value}/100 | decay={per_tick}/30s | clears_in~{eta}s"
        )
    print("  Rule: nguồn social/vision/voice chỉ được ảnh hưởng ngắn hạn; task hiện tại luôn ưu tiên cao hơn.")


def broker_context_snapshot(force_edge=False):
    with context_lock:
        browser = dict(context_state.get("browser", {}))
        active_app = context_state.get("active_app")
        active_title = context_state.get("active_title")

    if force_edge:
        active_app = "msedge"
        active_title = browser.get("title") or active_title

    age = browser_age_seconds(browser)
    browser_available = bool(browser.get("available"))
    browser_state = browser_snapshot_state(browser)
    browser_fresh = browser_available and age is not None and age <= BROWSER_FRESH_SECONDS
    active_app_lower = (active_app or "").lower()
    active_app_is_edge = active_app_lower in {"edge", "msedge"} or "msedge" in active_app_lower

    return {
        "active_app": active_app,
        "active_title": active_title,
        "active_app_is_edge": active_app_is_edge,
        "browser_available": browser_available,
        "browser_fresh": browser_fresh,
        "browser_snapshot_state": browser_state,
        "browser_age_seconds": age,
        "browser_kind": browser.get("kind"),
        "browser_title": browser.get("title"),
        "browser_url": browser.get("url"),
        "browser_heading": browser.get("page_heading"),
        "browser_meta_description": browser.get("meta_description"),
        "browser_selected_text": browser.get("selected_text"),
        "browser_local_summary": browser.get("local_summary"),
        "browser_social_post_text": browser.get("social_post_text"),
        "browser_social_vibe": browser.get("social_vibe"),
        "active_window_valid": browser_available and browser_state in {"FRESH", "WARM"} and active_app_is_edge,
        "debug_force_edge": bool(force_edge),
    }


def print_broker_decision(action_name, force_edge=False):
    broker_context = broker_context_snapshot(force_edge=force_edge)
    decision = action_broker.evaluate(action_name, context=broker_context)
    print_broker_decision_details(decision, broker_context)


def print_broker_decision_details(decision, broker_context):
    age = broker_context.get("browser_age_seconds")
    age_text = "None" if age is None else f"{age:.1f}s"
    print("🧯 Broker Decision")
    print(f"  Action: {decision.action}")
    print(f"  Status: {decision.status}")
    print(f"  Permission: {decision.permission}")
    print(f"  Requires confirm: {decision.requires_confirm}")
    print(f"  Can execute: {decision.can_execute}")
    print(f"  Reason: {decision.reason}")
    print(
        "  Context: "
        f"active_app={broker_context.get('active_app')} | "
        f"edge_active={broker_context.get('active_app_is_edge')} | "
        f"debug_force_edge={broker_context.get('debug_force_edge')}"
    )
    print(
        "  Browser: "
        f"available={broker_context.get('browser_available')} | "
        f"fresh={broker_context.get('browser_fresh')} | "
        f"age={age_text} | "
        f"kind={broker_context.get('browser_kind')}"
    )


def parse_action_id(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def print_pending_action():
    snapshot = pending_actions.snapshot()
    pending = snapshot.get("pending")
    last_closed = snapshot.get("last_closed")
    print("🧾 Pending Action")
    if pending:
        print(f"  ID: {pending.id}")
        print(f"  Action: {pending.action}")
        print(f"  Reason: {pending.reason}")
        print(f"  Age: {pending.age_seconds():.1f}s")
        print(f"  Expires in: {pending.expires_in_seconds():.1f}s")
        print(f"  Confirm: /action-confirm {pending.id}")
        print(f"  Cancel: /action-cancel {pending.id}")
        return
    print("  Pending: none")
    if last_closed:
        status, action, closed_at = last_closed
        age = max(0.0, time.time() - closed_at)
        print(f"  Last closed: {status} | {action.action} | {age:.1f}s ago")


def current_context_for_pending(pending):
    force_edge = bool((pending.context or {}).get("debug_force_edge"))
    return broker_context_snapshot(force_edge=force_edge)


def print_action_plan(plan):
    print("🧪 Action Plan")
    print(f"  Mode: {plan.mode}")
    print(f"  Action: {plan.action}")
    print(f"  Risk: {plan.risk}")
    print(f"  Target kind: {plan.browser_kind}")
    print(f"  Target title: {plan.target_title}")
    print(f"  Target URL: {plan.target_url}")
    if plan.draft_text:
        print(f"  Draft: {shorten_line(plan.draft_text, 180)}")
    print(f"  Would do: {plan.would_do}")
    print(f"  Safety: {', '.join(plan.safety)}")
    print(f"  Checks: {', '.join(plan.checks)}")


def print_pending_action_plan():
    pending = pending_actions.current()
    if not pending:
        print("🧪 Action Plan")
        print("  Pending: none")
        return
    plan = build_action_plan(pending, current_context=current_context_for_pending(pending))
    print_action_plan(plan)


def print_next_step_suggestion(force_edge=False):
    context = broker_context_snapshot(force_edge=force_edge)
    suggestion = build_next_step_suggestion(context)
    print("🧭 Next Step")
    print(f"  Topic: {suggestion.topic}")
    print(f"  Line: {suggestion.line}")
    print(f"  Why: {suggestion.rationale}")
    print(f"  Try: {suggestion.try_command}")
    print(f"  Safety: {', '.join(suggestion.safety)}")


def print_privacy_report(raw_text):
    report = build_privacy_report(raw_text, source="manual")
    print("🔐 Privacy Gate")
    print(f"  Source: {report.source}")
    print(f"  Risk: {report.risk}")
    print(f"  External model allowed: {report.allowed_for_external_model}")
    print(f"  Chars: {report.original_chars} -> {report.sanitized_chars}")
    if report.findings:
        findings = ", ".join(
            f"{finding.kind} x{finding.count} ({finding.severity})"
            for finding in report.findings
        )
        print(f"  Findings: {findings}")
    else:
        print("  Findings: none")
    if report.blocked_reasons:
        print(f"  Blocked by: {', '.join(report.blocked_reasons)}")
    print(f"  Sanitized: {report.sanitized_text or 'None'}")


def parse_context_preview_level(text_lower):
    parts = text_lower.split()
    for part in parts[1:]:
        candidate = part.upper()
        if candidate in {"L0", "L1", "L2", "L3", "L4"}:
            return candidate
        if candidate.startswith("--level="):
            value = candidate.replace("--LEVEL=", "", 1)
            if value in {"L0", "L1", "L2", "L3", "L4"}:
                return value
    return None


def print_context_budget_preview(force_edge=False, level=None):
    context = broker_context_snapshot(force_edge=force_edge)
    preview = build_context_budget_preview(context, level=level)
    print("🧱 Context Budget")
    print(f"  Level: {preview.level} ({preview.level_name})")
    print(f"  Max chars: {preview.max_chars}")
    print(f"  Used chars: {preview.used_chars}")
    print(f"  Risk: {preview.risk}")
    print(f"  External model allowed: {preview.allowed_for_external_model}")
    print(f"  Requires confirm: {preview.requires_confirm}")
    print(f"  Included: {', '.join(preview.included) if preview.included else 'none'}")
    print(f"  Dropped: {', '.join(preview.dropped) if preview.dropped else 'none'}")
    if preview.findings:
        findings = ", ".join(
            f"{finding.kind} x{finding.count} ({finding.severity})"
            for finding in preview.findings
        )
        print(f"  Privacy findings: {findings}")
    else:
        print("  Privacy findings: none")
    if preview.blocked_reasons:
        print(f"  Blocked by: {', '.join(preview.blocked_reasons)}")
    print("  Packet:")
    if preview.packet:
        for line in preview.packet.splitlines():
            print(f"    {line}")
    else:
        print("    None")


def clamp_context_confidence(value):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def context_confidence_label(score):
    score = clamp_context_confidence(score)
    if score >= 0.8:
        return "high"
    if score >= 0.55:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def context_source_score_row(name, score, available, reason, sample=""):
    score = clamp_context_confidence(score if available else 0)
    return {
        "name": name,
        "score": score,
        "available": bool(available),
        "label": context_confidence_label(score),
        "reason": reason,
        "sample": sample or "",
    }


def context_signal_groups_from_text(text):
    return text_signal_groups(tokenize_reconcile_text(text))


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



def context_vibe_audit(post="", title="", vibe=""):
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


def format_vibe_audit_summary(context):
    audit = context_vibe_audit(
        post=(context or {}).get("browser_social_post_text") or "",
        title=(context or {}).get("browser_title") or "",
        vibe=(context or {}).get("browser_social_vibe") or "",
    )
    if audit["status"] == "missing":
        return "none"
    return audit["detail"]


def context_confidence_rows(context=None, vision_description=None):
    context = context or broker_context_snapshot()
    age = context.get("browser_age_seconds")
    fresh = bool(context.get("browser_fresh"))
    kind = ((context.get("browser_kind") or "")).lower()
    title = context.get("browser_title") or ""
    url = context.get("browser_url") or ""
    post = context.get("browser_social_post_text") or ""
    vibe = context.get("browser_social_vibe") or ""
    heading = context.get("browser_heading") or ""
    local_summary = context.get("browser_local_summary") or ""
    selected = context.get("browser_selected_text") or ""
    meta = context.get("browser_meta_description") or ""

    rows = []
    post_score = 0.0
    if post:
        post_score = 0.95 if kind == "social" and fresh else 0.72
        if len(post) < 32:
            post_score -= 0.12
    rows.append(context_source_score_row(
        "post",
        post_score,
        bool(post),
        "visible social post text; primary when available",
        shorten_line(post, 90),
    ))

    title_score = 0.0
    if title:
        title_score = 0.78 if fresh else 0.48
        generic_title_markers = ["trang chủ", "home", "youtube", "x /", "facebook"]
        if any(marker in title.lower() for marker in generic_title_markers):
            title_score -= 0.18
    rows.append(context_source_score_row(
        "title",
        title_score,
        bool(title),
        "page title; useful but can be generic or translated",
        shorten_line(title, 90),
    ))

    url_score = 0.0
    if url:
        url_score = 0.68 if fresh else 0.38
        if any(marker in url.lower() for marker in ["/status/", "x.com/", "facebook.com/"]):
            url_score += 0.08
    rows.append(context_source_score_row(
        "url",
        url_score,
        bool(url),
        "site and route signal; not semantic by itself",
        shorten_line(url, 90),
    ))

    vision_score = 0.0
    vision_reason = "no fresh vision description"
    vision_sample = ""
    if vision_description:
        vision_score = 0.72
        vision_reason = "latest manual vision description; strong for images, partial for videos"
        vision_sample = vision_description
    elif LAST_VISION_DESCRIPTION:
        vision_age = time.time() - LAST_VISION_DESCRIPTION.get("time", 0)
        vision_sample = LAST_VISION_DESCRIPTION.get("text") or ""
        if vision_age <= 180:
            vision_score = 0.72
            vision_reason = f"fresh manual vision description ({vision_age:.0f}s)"
        else:
            vision_score = 0.25
            vision_reason = f"stale vision description ({vision_age:.0f}s)"
    rows.append(context_source_score_row(
        "vision",
        vision_score,
        bool(vision_sample),
        vision_reason,
        shorten_line(vision_sample, 90),
    ))

    vibe_score = 0.0
    vibe_audit = context_vibe_audit(post=post, title=title, vibe=vibe)
    if vibe:
        vibe_score = 0.45
        if "post_topic=" in vibe:
            vibe_score += 0.12
        if not post:
            vibe_score -= 0.12
        if vibe_audit["mismatch"]:
            vibe_score -= 0.24
    rows.append(context_source_score_row(
        "vibe",
        vibe_score,
        bool(vibe),
        f"social crowd/vibe; supporting signal only; {vibe_audit['detail']}",
        shorten_line(vibe, 90),
    ))

    has_comment_samples = bool(vibe and ("samples(" in vibe or "@" in vibe))
    rows.append(context_source_score_row(
        "comments",
        0.32 if has_comment_samples else 0.0,
        has_comment_samples,
        "comment samples; never override post/title",
        shorten_line(vibe, 90) if has_comment_samples else "",
    ))

    extra_text = selected or local_summary or heading or meta
    extra_score = 0.42 if extra_text else 0.0
    if selected:
        extra_score = 0.64
    elif local_summary:
        extra_score = 0.56
    rows.append(context_source_score_row(
        "page_extra",
        extra_score,
        bool(extra_text),
        "selected text/local summary/heading/meta; useful as auxiliary context",
        shorten_line(extra_text, 90),
    ))

    return rows


def context_confidence_summary(context=None, vision_description=None):
    rows = context_confidence_rows(context=context, vision_description=vision_description)
    usable = [row for row in rows if row["available"] and row["score"] > 0]
    usable_sorted = sorted(usable, key=lambda row: row["score"], reverse=True)
    if not usable_sorted:
        overall = 0.0
    elif len(usable_sorted) == 1:
        overall = usable_sorted[0]["score"]
    else:
        overall = usable_sorted[0]["score"] * 0.65 + usable_sorted[1]["score"] * 0.35
    return {
        "rows": rows,
        "usable": usable_sorted,
        "overall": clamp_context_confidence(overall),
    }


def context_row_by_name(rows):
    return {row.get("name"): row for row in rows}


def context_priority_policy(context=None, vision_description=None):
    context = context or broker_context_snapshot()
    summary = context_confidence_summary(context=context, vision_description=vision_description)
    rows = summary["rows"]
    row_map = context_row_by_name(rows)
    kind = ((context.get("browser_kind") or "")).lower()
    source_text = build_social_draft_source(
        DEFAULT_SOCIAL_CLASSIFY_REQUEST,
        broker_context=context,
        vision_description=vision_description,
    )
    media_mode = detect_social_media_mode(source_text)
    if kind == "social":
        if media_mode == "image":
            order = ["post", "vision", "title", "url", "page_extra", "vibe", "comments"]
            reason = "social_image: post vẫn là caption chính, vision nâng hiểu ảnh"
        elif media_mode == "video":
            order = ["post", "title", "url", "vision", "page_extra", "vibe", "comments"]
            reason = "social_video: title/post quyết định diễn biến, vision chỉ là frame phụ"
        else:
            order = ["post", "title", "url", "page_extra", "vibe", "comments", "vision"]
            reason = "social_text: visible post là nguồn chính, crowd/vibe chỉ phụ"
    elif kind in {"youtube", "video", "music"}:
        order = ["title", "url", "page_extra", "vision", "post", "vibe", "comments"]
        reason = f"{kind or 'video'}: title/url là xương sống, local summary hỗ trợ sau reconcile"
    elif kind in {"github", "docs"}:
        order = ["title", "page_extra", "url", "vision", "post", "vibe", "comments"]
        reason = f"{kind}: heading/summary quan trọng hơn social vibe"
    elif kind in {"search", "search_home"}:
        order = ["title", "page_extra", "url", "vision", "post", "vibe", "comments"]
        reason = "search: title/heading chỉ để định hướng, chưa coi là nội dung đích"
    else:
        order = ["title", "url", "page_extra", "vision", "post", "vibe", "comments"]
        reason = "default: ưu tiên nguồn nhẹ có sẵn, không đoán quá sâu"

    ranked = []
    ignored = []
    for name in order:
        row = row_map.get(name)
        if not row:
            continue
        item = dict(row)
        item["policy_rank"] = len(ranked) + 1
        if row.get("available") and row.get("score", 0) > 0:
            ranked.append(item)
        else:
            ignored.append(item)
    return {
        "kind": kind or "unknown",
        "media_mode": media_mode,
        "reason": reason,
        "fresh": bool(context.get("browser_fresh")),
        "state": context.get("browser_snapshot_state") or "INVALID",
        "age": context.get("browser_age_seconds"),
        "overall": summary["overall"],
        "ranked": ranked,
        "ignored": ignored,
        "rows": rows,
    }


def context_priority_brief(policy, limit=3):
    ranked = policy.get("ranked") or []
    if not ranked:
        return "none"
    return " > ".join(row["name"] for row in ranked[:limit])


def format_context_priority_summary(policy):
    return (
        f"{context_priority_brief(policy)} | "
        f"overall={policy['overall']:.2f} ({context_confidence_label(policy['overall'])}) | "
        f"state={policy['state']}"
    )


def print_context_priority():
    context = broker_context_snapshot()
    vision_text = None
    if LAST_VISION_DESCRIPTION:
        vision_text = LAST_VISION_DESCRIPTION.get("text")
    policy = context_priority_policy(context=context, vision_description=vision_text)
    age = policy.get("age")
    age_text = "None" if age is None else f"{age:.1f}s"
    print("🧭 Context Priority")
    print(
        "  Browser: "
        f"kind={policy['kind']} | media={policy['media_mode']} | state={policy['state']} | "
        f"fresh={policy['fresh']} | age={age_text} | "
        f"overall={policy['overall']:.2f} ({context_confidence_label(policy['overall'])})"
    )
    print(f"  Policy: {policy['reason']}")
    print("  Ranking: policy-first; score là độ tin từng nguồn, không phải thứ tự sort tuyệt đối.")
    print("  Use order:")
    if not policy["ranked"]:
        print("    none")
    for row in policy["ranked"]:
        print(
            "    "
            f"P{row['policy_rank']} {row['name']} | score={row['score']:.2f} ({row['label']}) | "
            f"{shorten_line(row.get('sample'), 100)}"
        )
    ignored = [row for row in policy["ignored"] if row.get("name")]
    print("  Ignored/missing:")
    if not ignored:
        print("    none")
    for row in ignored:
        print(f"    {row['name']} | {row['label']} | {row['reason']}")
    print("  Rule: nguồn bị hạ ưu tiên vẫn trace được, nhưng không tự lấn nguồn chính.")


def context_evidence_role(row, policy):
    name = row.get("name")
    if not row.get("available") or row.get("score", 0) <= 0:
        return "missing"
    ranked = policy.get("ranked") or []
    if ranked and ranked[0].get("name") == name:
        return "used_primary"
    if any(item.get("name") == name for item in ranked[1:3]):
        return "used_support"
    return "ignored_low_priority"


def context_evidence_reason(row, role, policy):
    name = row.get("name")
    if role == "missing":
        return row.get("reason") or "not available"
    if name == "vibe":
        reason = row.get("reason") or ""
        if "vibe_mismatch" in reason:
            return "crowd/vibe lệch nhóm với post/title nên chỉ trace, không dùng để kéo chủ đề"
        return "crowd/vibe chỉ phụ, không tự lấn post/title"
    if name == "comments":
        return "comment samples chỉ tham khảo giọng, không quyết định sự kiện"
    if name == "vision" and policy.get("media_mode") == "video":
        return "video frame chỉ là phụ; post/title quyết định diễn biến"
    if name == "vision" and policy.get("media_mode") == "image":
        return "ảnh tĩnh có thể nâng hiểu visual"
    if role == "used_primary":
        return "nguồn chính theo policy hiện tại"
    if role == "used_support":
        return "nguồn phụ để tăng độ chắc"
    return "có dữ liệu nhưng thấp ưu tiên trong policy hiện tại"


def print_evidence_trace():
    context = broker_context_snapshot()
    vision_text = None
    if LAST_VISION_DESCRIPTION:
        vision_text = LAST_VISION_DESCRIPTION.get("text")
    policy = context_priority_policy(context=context, vision_description=vision_text)
    age = policy.get("age")
    age_text = "None" if age is None else f"{age:.1f}s"
    print("🧾 Evidence Trace")
    print(
        "  Context: "
        f"kind={policy['kind']} | media={policy['media_mode']} | state={policy['state']} | "
        f"fresh={policy['fresh']} | age={age_text} | priority={context_priority_brief(policy)}"
    )
    print(f"  Policy: {policy['reason']}")
    print("  Ranking: policy-first; score là độ tin từng nguồn, không phải thứ tự sort tuyệt đối.")
    rows = {row.get("name"): row for row in policy.get("rows", [])}
    order = [row.get("name") for row in (policy.get("ranked") or [])] + [
        row.get("name") for row in (policy.get("ignored") or [])
    ]
    seen = set()
    for name in order:
        if not name or name in seen:
            continue
        seen.add(name)
        row = rows.get(name)
        if not row:
            continue
        role = context_evidence_role(row, policy)
        sample = shorten_line(row.get("sample"), 90) if row.get("sample") else ""
        print(
            "  "
            f"{name}: {role} | score={row['score']:.2f} ({row['label']}) | "
            f"{context_evidence_reason(row, role, policy)}"
        )
        if sample:
            print(f"    sample: {sample}")
    print("  Rule: trace ghi cả nguồn bị bỏ qua để debug vì sao Nana tin/không tin một tín hiệu.")


def tokenize_reconcile_text(text):
    import re

# ============================================================================
# [CUT] Nana extension modules — function bodies moved to submodules.
# This file re-exports everything for backward compatibility.
# ============================================================================

from nana.commands.registry import (
    KNOWN_SLASH_COMMANDS, OSU_STATIC_COMMANDS, STARDEW_STATIC_COMMANDS,
    COMMAND_NORMALIZATION_CASES, COMMAND_ROUTE_CASES,
)
from nana.runtime.metrics import (
    RUNTIME_LATENCY, RUNTIME_RECONCILE,
    BROWSER_REFRESH_COOLDOWN_SECONDS, BROWSER_REFRESH_INFLIGHT,
    record_browser_refresh_latency, record_browser_cache_hit,
    record_browser_coalesced, record_browser_cooldown_skip,
    browser_refresh_last_age_seconds,
)
from nana.phases.commons import (
    AUTONOMY_LOCK_BLOCKED_ACTIONS, CASUAL_POOLS,
    PHASE7_VIRTUAL_ACTIONS, PHASE7_BASE_GUARD_CONTEXT, PHASE7_SOCIAL_GUARD_CONTEXT,
    PHASE10_REQUIRED_COMMANDS, PHASE10_1_REQUIRED_GROUPS, PHASE10_1_COMMANDS,
    PHASE10_2_COMMANDS, PHASE10_2_EXPECTED_ALIASES, PHASE10_3_COMMANDS,
    PHASE10_3_REQUIRED_SCHEMA_GROUPS, TYPE_ALIASES, PHASE10_4_COMMANDS,
    RUNTIME_EVENT_REQUIRED_FIELDS, PHASE10_5_COMMANDS, PHASE10_6_COMMANDS,
    PHASE10_7_COMMANDS, PHASE10_8_COMMANDS, VALID_TURN_STATUSES,
    PHASE10_9_COMMANDS, PHASE10_10_COMMANDS, PHASE11_1_COMMANDS, PHASE11_2_COMMANDS,
    TRUST_LEVELS, FINAL_PHASE_COMMANDS,
)
from nana.commands.router import (
    normalize_command_text, suggest_slash_command,
    command_joined_issue, command_route_analysis,
    command_route_regression_rows, command_normalize_regression_rows,
)
from nana.commands.help import (
    print_command_help, print_phase3_state, print_phase4_state,
)
from nana.commands.dynamic import (
    is_stardew_adapter_command, sync_stardew_command_surface,
    handle_stardew_v2_command, sync_osu_command_surface,
)
from nana.runtime.browser_state import (
    browser_age_seconds, browser_snapshot_state,
    snapshot_state_usable_for_context, snapshot_state_usable_for_social_draft,
    current_browser_snapshot_state, is_browser_context_question,
)
from nana.runtime.browser_refresh import (
    browser_snapshot_from_context, refresh_browser_state,
    ensure_browser_snapshot, print_browser_state,
    browser_reader, vision_previewer,
)
from nana.runtime.proactive import (
    get_active_window, is_idle, idle_duration,
    classify_zone, sync_proactive, reset_proactive, update_proactive,
    is_browser_fresh, format_browser_status,
)
from nana.social.classifier import (
    social_source_classification, detect_public_reaction_style,
    source_has_cute_context, source_has_conflict_context,
    source_has_crime_violence_context, source_has_harassment_boundary_context,
    source_has_minor_safety_context, source_has_self_harm_sensitive_context,
    source_has_sexual_sensitive_context, source_has_serious_issue_context,
    source_has_financial_scam_context, source_has_legal_sensitive_context,
    source_has_misinfo_uncertain_context, source_has_public_safety_context,
    source_has_politics_sensitive_context, source_has_medical_sensitive_context,
    source_has_grief_sensitive_context, source_has_weather_funny_context,
    source_has_awkward_danger_context, source_has_disaster_emergency_context,
    source_has_accident_or_traffic_context, source_has_quick_reaction_context,
    source_has_space_launch_context, source_has_overloaded_ride_funny_context,
    danger_context_should_not_praise_reaction, fallback_danger_public_reply,
    source_match_bundle, visible_post_match_text,
    primary_social_context_match_text, public_social_reply_seems_off_topic,
    is_diagnostic_fragment,
    SOCIAL_GUARD_EXAMPLES, GENERIC_UNSAFE_FALLBACKS,
)
from nana.social import (
    compact_public_reaction_reply, fallback_public_social_reply,
    soften_public_social_reply, enforce_short_public_reply,
    trim_public_social_reply, fallback_social_draft,
    should_use_teo_lite, apply_teo_lite_public_reply, sanitize_teo_lite_reply,
    draft_quality_report, draft_quality_summary,
    polish_social_draft_quality, social_draft_has_obvious_quality_issue,
    social_draft_needs_fallback, social_vibe_topic_style_override,
    social_guard_expected_fallback_ok, social_guard_matrix_rows,
    social_guard_matrix_summary,
    social_type_context_block_reason, social_request_has_clear_topic,
    social_request_needs_page_context, social_target_missing_reason,
    build_social_draft_source, build_social_draft_prompt,
    enrich_social_context_packet, extract_social_title_content,
    call_social_draft_with_fallbacks, clean_social_draft,
    remove_disallowed_social_draft_bits, social_draft_guard_note,
    find_recent_duplicate_draft,
    print_draft_queue, print_draft_show, print_draft_confirm,
    print_draft_cancel, print_draft_item, propose_social_type_draft,
    choose_social_draft_models, print_social_draft_test,
    print_draft_quality, print_draft_quality_test,
)
from nana.autonomy.wiring import wire_autonomy_backends

# Phase helpers
from nana.phases.phase7 import (
    phase7_snapshot_validation, phase7_history_summary,
    phase7_pending_plan_expired, phase7_current_pending_plan,
    phase7_pending_create_blockers, phase7_pending_create_status,
    phase7_store_pending_plan, phase7_find_pending_plan,
    phase7_close_pending_plan, phase7_pending_plan_summary,
    phase7_log_event, phase7_action_log_summary,
    phase7_pending_confirm_rows, phase7_pending_guard_summary,
    phase7_integration_snapshot, phase7_integration_preview, phase7_integration_status,
    phase7_action_rows, phase7_command_warnings,
    phase7_quality_issues, phase7_quality_notes, phase7_quality_blockers,
    phase7_quality_status, phase7_precondition_issues,
    phase7_precondition_status, phase7_precondition_recovery,
    phase7_dry_run_expect_report, phase7_dry_run_guard_summary,
    print_phase7_dry_run_snapshot, print_phase7_dry_run,
    print_phase7_quality, print_phase7_plan_preview, print_phase7_pending_plan,
    print_phase7_plan_confirm, print_phase7_plan_cancel,
    print_phase7_pending_guard_status, print_phase7_action_log,
    print_phase7_guard_status, print_phase7_status, print_phase7_ready,
)
from nana.phases.phase8 import (
    phase8_executor_exposure, phase8_broker_contract_row, phase8_broker_guard_summary,
    phase8_context_check_rows, phase8_pre_exec_report, phase8_pre_exec_guard_summary,
    phase8_action_trace_report, phase8_action_trace_guard_summary, phase8_contract_matrix,
    print_phase8_broker_matrix, print_phase8_action_contract,
    print_phase8_guard_status, print_phase8_pre_exec_check,
    print_phase8_pre_exec_guard_status, print_phase8_action_trace,
    print_phase8_action_trace_guard_status,
    print_phase8_status, print_phase8_ready,
)
from nana.phases.phase9 import (
    phase9_audit_record, phase9_find_audit_entry, phase9_audit_summary,
    phase9_audit_clear, phase9_entry_risk, phase9_review_entry,
    phase9_audit_guard_summary,
    print_phase9_audit_log, print_phase9_audit_clear,
    print_phase9_audit_replay, print_phase9_audit_review,
    print_phase9_guard_status, print_phase9_status, print_phase9_ready,
)
from nana.phases.phase10 import *
from nana.phases.phase11 import *
from nana.phases.phase12 import *
from nana.phases.phase13 import *
from nana.phases.phase14 import *
from nana.phases.phase15 import *
from nana.phases.phase16 import *
from nana.phases.phase17 import *
from nana.phases.phase18 import *
from nana.phases.phase19 import *
from nana.phases.phase20 import *
from nana.phases.phase21 import *
from nana.phases.phase22 import *
from nana.phases.phase23 import *
from nana.phases.phase24 import *
from nana.phases.phase25 import *
from nana.phases.phase81 import *
from nana.phases.phase82 import *
from nana.phases.phase83 import *
from nana.phases.phase84 import *
from nana.phases.phase85 import *

# ============================================================================


def reconcile_signal_words():
    return {
        "vehicle": {"xe", "oto", "car", "motorcycle", "duong", "hem", "camera", "cctv"},
        "violence": {"dao", "sung", "vukhi", "hanhhung", "tancong", "baoluc"},
        "weather": {"mua", "gio", "bao", "tuyet", "ngap", "lut"},
        "food": {"mi", "cay", "to", "an", "quan"},
        "animal": {"meo", "cat", "dog", "kitten", "puppy"},
        "school": {"hoc", "sinh", "truong"},
        "cable": {"treo", "cable", "skier", "ski", "tuyet"},
        "space_launch": {"starship", "spacex", "rocket", "launch", "ten", "lua", "phong", "bay"},
        "medical": {"benh", "vien", "thuoc", "nhapvien", "capcuu", "trieuchung"},
        "anime_game": {"anime", "honkai", "star", "rail", "game", "gai", "toc", "pixel", "galaxy"},
        "music": {"lofi", "remix", "music", "nhac", "hour", "cover", "mashup", "mix", "nightcore", "porter", "robinson"},
    }


def text_signal_groups(tokens):
    groups = []
    for name, words in reconcile_signal_words().items():
        if tokens & words:
            groups.append(name)
    return groups


def vision_text_reconcile_report(context=None, vision_description=None):
    context = context or broker_context_snapshot()
    vision_description = vision_description or ""
    source_parts = [
        context.get("browser_social_post_text"),
        context.get("browser_title"),
        context.get("browser_heading"),
        context.get("browser_local_summary"),
    ]
    source_text = " ".join(str(part) for part in source_parts if part)
    source_tokens = tokenize_reconcile_text(source_text)
    vision_tokens = tokenize_reconcile_text(vision_description)
    if not source_tokens:
        return {
            "status": "missing_text",
            "overlap": 0.0,
            "shared": [],
            "text_groups": [],
            "vision_groups": [],
            "note": "không có text nguồn đủ rõ",
        }
    if not vision_tokens:
        return {
            "status": "missing_vision",
            "overlap": 0.0,
            "shared": [],
            "text_groups": text_signal_groups(source_tokens),
            "vision_groups": [],
            "note": "chưa có vision description",
        }
    shared = sorted(source_tokens & vision_tokens)
    overlap = len(shared) / max(1, min(len(source_tokens), len(vision_tokens)))
    text_groups = text_signal_groups(source_tokens)
    vision_groups = text_signal_groups(vision_tokens)
    shared_groups = sorted(set(text_groups) & set(vision_groups))
    if shared_groups or overlap >= 0.18:
        status = "aligned"
        note = "vision hỗ trợ text chính"
    elif text_groups and vision_groups and not shared_groups:
        status = "conflict"
        note = "vision và text đang nghiêng về nhóm tín hiệu khác nhau"
    else:
        status = "weak"
        note = "chưa đủ overlap để kết luận; không dùng vision lấn text"
    return {
        "status": status,
        "overlap": overlap,
        "shared": shared[:8],
        "text_groups": text_groups,
        "vision_groups": vision_groups,
        "note": note,
    }


def parse_reconcile_expect(raw_text):
    text = str(raw_text or "").strip()
    if "||" in text:
        left, vision = text.split("||", 1)
    else:
        left, vision = text, ""
    if "|" in left:
        expected, sample = left.split("|", 1)
        return expected.strip(), sample.strip(), vision.strip()
    parts = left.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip(), vision.strip()
    return "", "", vision.strip()


def print_reconcile_expect(raw_text):
    expected, sample, vision = parse_reconcile_expect(raw_text)
    print("🧪 Reconcile Expect")
    print("  Action: read-only; không gọi model, không tạo draft.")
    if not expected or not sample or "<" in expected or "<" in sample or "<" in vision:
        print("  Missing: /reconcile-expect <status> | <text> || <vision description>")
        print("  Example: /reconcile-expect aligned | Honkai Star Rail lofi remix || ảnh anime cô gái")
        return
    context = {
        "browser_kind": "test",
        "browser_title": sample,
        "browser_heading": "",
        "browser_local_summary": "",
        "browser_social_post_text": "",
    }
    report = vision_text_reconcile_report(context=context, vision_description=vision)
    status = "pass" if report["status"] == expected else "fail"
    print(f"  Status: {status}")
    print(f"  Expected: {expected}")
    print(f"  Got: {report['status']}")
    print(f"  Overlap: {report['overlap']:.2f}")
    print(f"  Text groups: {', '.join(report['text_groups']) if report['text_groups'] else 'none'}")
    print(f"  Vision groups: {', '.join(report['vision_groups']) if report['vision_groups'] else 'none'}")
    print(f"  Shared tokens: {', '.join(report['shared']) if report['shared'] else 'none'}")
    if status == "fail":
        print("  Fail stage: reconcile")
        print(f"  Fail reason: expected={expected}")
    print(f"  Text: {shorten_line(sample, 160)}")
    print(f"  Vision: {shorten_line(vision, 160) if vision else 'none'}")


RECONCILE_GUARD_EXAMPLES = [
    ("aligned", "First Starship V3 launch later this week", "tên lửa đang bay lên với khói trắng"),
    ("aligned", "Cyrene Theme Music 1 HOUR tnbee mix Honkai Star Rail", "ảnh minh họa anime cô gái tóc hồng"),
    ("missing_vision", "Cyrene Theme Music 1 HOUR tnbee mix Honkai Star Rail", ""),
    ("conflict", "video xe máy tông cột điện trong hẻm", "ảnh minh họa anime cô gái tóc hồng"),
]


def reconcile_guard_matrix_rows():
    rows = []
    for expected, source, vision in RECONCILE_GUARD_EXAMPLES:
        context = {
            "browser_kind": "test",
            "browser_title": source,
            "browser_heading": "",
            "browser_local_summary": "",
            "browser_social_post_text": "",
        }
        report = vision_text_reconcile_report(context=context, vision_description=vision)
        status = "pass" if report["status"] == expected else "fail"
        rows.append({
            "expected": expected,
            "got": report["status"],
            "status": status,
            "text_groups": report["text_groups"],
            "vision_groups": report["vision_groups"],
            "source": source,
            "vision": vision,
        })
    return rows


def reconcile_guard_matrix_summary():
    rows = reconcile_guard_matrix_rows()
    failures = [row for row in rows if row["status"] != "pass"]
    return {
        "rows": rows,
        "total": len(rows),
        "pass_count": len(rows) - len(failures),
        "failures": failures,
    }


def print_reconcile_guard_status():
    summary = reconcile_guard_matrix_summary()
    print("🧩 Reconcile Guard Status")
    print("  Action: read-only; không gọi model, không tạo draft.")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for row in summary["rows"]:
        print(
            "  "
            f"{row['expected']} | {row['status']} | got={row['got']} | "
            f"text={','.join(row['text_groups']) or 'none'} | vision={','.join(row['vision_groups']) or 'none'}"
        )
        if row["status"] != "pass":
            print(f"    source: {shorten_line(row['source'], 140)}")
            print(f"    vision: {shorten_line(row['vision'], 140) if row['vision'] else 'none'}")


def print_reconcile_check():
    context = broker_context_snapshot()
    vision_text = None
    vision_status = "none"
    if LAST_VISION_DESCRIPTION:
        age = time.time() - LAST_VISION_DESCRIPTION.get("time", 0)
        if age <= 180:
            vision_text = LAST_VISION_DESCRIPTION.get("text")
            vision_status = f"fresh ({age:.1f}s)"
        else:
            vision_status = f"stale ({age:.1f}s)"
    report = vision_text_reconcile_report(context=context, vision_description=vision_text)
    print("🧩 Vision/Text Reconcile")
    print("  Action: read-only; không gọi model, không tạo draft.")
    print(
        "  Browser: "
        f"kind={context.get('browser_kind')} | title={shorten_line(context.get('browser_title'), 90)}"
    )
    print(f"  Vision: {vision_status}")
    print(f"  Status: {report['status']}")
    print(f"  Overlap: {report['overlap']:.2f}")
    print(f"  Text groups: {', '.join(report['text_groups']) if report['text_groups'] else 'none'}")
    print(f"  Vision groups: {', '.join(report['vision_groups']) if report['vision_groups'] else 'none'}")
    print(f"  Shared tokens: {', '.join(report['shared']) if report['shared'] else 'none'}")
    print(f"  Note: {report['note']}")
    print("  Rule: khi conflict/weak, vision không được tự phủ quyết post/title.")


def context_recovery_next_step(context=None, missing_reason=None, vision_description=None):
    context = context or broker_context_snapshot()
    kind = ((context.get("browser_kind") or "")).lower()
    state = context.get("browser_snapshot_state") or "INVALID"
    available = bool(context.get("browser_available"))
    title = context.get("browser_title") or ""
    post = context.get("browser_social_post_text") or ""
    vibe = context.get("browser_social_vibe") or ""
    reason = str(missing_reason or "").lower()
    if not available or state == "INVALID":
        return "Chưa có snapshot dùng được. Mở Edge debug rồi chạy /br; nếu đang đúng tab social thì chạy lại /social-target."
    if "browser_kind=" in reason and kind in {"youtube", "video", "music"}:
        return "Tab hiện tại là video/YouTube, không phải bài social. Muốn xem context nhẹ thì /video-context; cần hiểu sâu hơn thì /reconcile; muốn reply social thì mở đúng tweet/bài X/Facebook rồi /br."
    if "browser_kind=" in reason and kind and kind != "social":
        return "Tab hiện tại không phải bài social. Mở đúng tweet/bài X/Facebook rồi /br, hoặc gửi nguyên nội dung cần reply trong lệnh draft."
    if kind == "social" and not (post or vibe):
        return "Đang ở social nhưng chưa bắt được post/vibe. Scroll/focus vào bài chính rồi /br; nếu vẫn trống, copy nội dung bài vào /social-draft-test."
    if kind == "social" and not post and vibe:
        return "Chỉ có vibe/comment phụ, thiếu post chính. Chạy /br lại ở đúng bài hoặc copy caption/post vào lệnh để tránh lệch context."
    if "vision" in reason or (not vision_description and kind == "social" and any(marker in title.lower() for marker in ["https://t.co", "video", "clip"])):
        return "Nếu bài phụ thuộc hình/video, chạy /vision-preview rồi /vision-describe; sau đó dùng /social-draft-vision <text>."
    if kind == "social":
        return "Context social đủ nhẹ rồi. Dùng /social-classify để kiểm tra style, rồi /social-draft-test để tạo nháp."
    return "Context hiện tại chỉ đủ mức nhẹ. Nếu cần hiểu sâu, chạy /reconcile; nếu cần reply social, mở đúng bài social rồi /br."


def print_context_recovery():
    context = broker_context_snapshot()
    vision_text = None
    vision_status = "none"
    if LAST_VISION_DESCRIPTION:
        age = time.time() - LAST_VISION_DESCRIPTION.get("time", 0)
        vision_text = LAST_VISION_DESCRIPTION.get("text") if age <= 180 else None
        vision_status = f"fresh ({age:.1f}s)" if vision_text else f"stale ({age:.1f}s)"
    policy = context_priority_policy(context=context, vision_description=vision_text)
    context_preview = build_context_budget_preview(context, level="L2")
    missing = social_target_missing_reason(
        DEFAULT_SOCIAL_CLASSIFY_REQUEST,
        context,
        context_preview,
        vision_description=vision_text,
        omit_page_context=False,
    )
    age = policy.get("age")
    age_text = "None" if age is None else f"{age:.1f}s"
    print("🧭 Context Recovery")
    print(
        "  Browser: "
        f"available={context.get('browser_available')} | kind={policy['kind']} | "
        f"state={policy['state']} | age={age_text}"
    )
    print(f"  Priority: {format_context_priority_summary(policy)}")
    print(f"  Vision: {vision_status}")
    print(f"  Social target: {'ready' if missing is None else 'missing'}")
    if missing:
        print(f"  Missing reason: {missing}")
    print(f"  Next: {context_recovery_next_step(context, missing_reason=missing, vision_description=vision_text)}")
    print("  Rule: recovery chỉ gợi ý bước tiếp theo; không tự mở app, click, type hoặc post.")


def print_video_context():
    context = broker_context_snapshot()
    kind = ((context.get("browser_kind") or "")).lower()
    age = context.get("browser_age_seconds")
    age_text = "None" if age is None else f"{age:.1f}s"
    title = context.get("browser_title") or ""
    url = context.get("browser_url") or ""
    heading = context.get("browser_heading") or ""
    local_summary = context.get("browser_local_summary") or ""
    print("🎬 Video Context")
    print("  Action: read-only; dùng snapshot/cache hiện có, không gọi model.")
    print(
        "  Browser: "
        f"available={context.get('browser_available')} | kind={kind or 'unknown'} | "
        f"state={context.get('browser_snapshot_state') or 'INVALID'} | age={age_text}"
    )
    print(f"  Title: {shorten_line(title, 160) or 'none'}")
    print(f"  URL: {shorten_line(url, 160) or 'none'}")
    print(f"  Heading: {shorten_line(heading, 160) or 'none'}")
    print(f"  Local summary: {shorten_line(local_summary, 180) or 'none'}")
    print(
        "  Reconcile: "
        f"status={RUNTIME_RECONCILE.get('status')} | "
        f"last={format_ms(RUNTIME_RECONCILE.get('last_ms'))} | "
        f"summary={shorten_line(RUNTIME_RECONCILE.get('last_summary'), 120) or 'none'}"
    )
    if kind not in {"youtube", "video", "music"}:
        print("  Status: non_video_context")
        print("  Next: mở video/YouTube rồi /br, hoặc dùng /context-confidence cho trang hiện tại.")
        return
    reconcile_status = RUNTIME_RECONCILE.get("status")
    if local_summary:
        print("  Status: usable_cached_context")
        print("  Next: dùng title/summary này làm bối cảnh nhẹ; nếu cần sâu hơn thì /reconcile.")
    elif reconcile_status in {"queued", "running"}:
        print("  Status: reconcile_running")
        print("  Next: đợi /reconcile-status hoặc chạy lại /video-context để xem summary khi xong.")
    else:
        print("  Status: light_only")
        print("  Next: /reconcile để tạo local summary nền; /reconcile-status để xem kết quả.")


def print_context_confidence(force_edge=False):
    context = broker_context_snapshot(force_edge=force_edge)
    vision_text = None
    if LAST_VISION_DESCRIPTION:
        vision_text = LAST_VISION_DESCRIPTION.get("text")
    summary = context_confidence_summary(context=context, vision_description=vision_text)
    overall = summary["overall"]
    usable = summary["usable"]
    primary = usable[0]["name"] if usable else "none"
    secondary = usable[1]["name"] if len(usable) > 1 else "none"
    age = context.get("browser_age_seconds")
    age_text = "None" if age is None else f"{age:.1f}s"
    snapshot_state = context.get("browser_snapshot_state") or "INVALID"

    print("🧭 Context Confidence")
    print(
        "  Browser: "
        f"available={context.get('browser_available')} | fresh={context.get('browser_fresh')} | "
        f"state={snapshot_state} | age={age_text} | kind={context.get('browser_kind')}"
    )
    print(f"  Overall: {overall:.2f} ({context_confidence_label(overall)})")
    print(f"  Primary: {primary} | Secondary: {secondary}")
    print("  Sources:")
    for row in summary["rows"]:
        status = "available" if row["available"] else "missing"
        print(
            "    "
            f"{row['name']}: {row['score']:.2f} ({row['label']}) | {status} | {row['reason']}"
        )
        if row["sample"]:
            print(f"      sample: {row['sample']}")
    print("  Rule: post/title/URL là xương sống; vision/vibe/comments chỉ nâng hoặc hạ độ chắc, không tự lấn nguồn chính.")


def print_route_test(raw_text):
    route = route_sidecar_task(raw_text, context=broker_context_snapshot())
    print("🧭 Model Router")
    print(f"  Status: {route.status}")
    print(f"  Model: {route.model}")
    print(f"  Tier: {route.tier}")
    print(f"  Intended share: {route.intended_share}")
    print(f"  Use case: {route.use_case}")
    print(f"  Reason: {route.reason}")
    print(f"  Privacy risk: {route.privacy_risk}")
    print(f"  External allowed: {route.external_allowed}")
    print(f"  Fallbacks: {', '.join(route.fallback_models) if route.fallback_models else 'none'}")
    if route.blocked_reasons:
        print(f"  Blocked by: {', '.join(route.blocked_reasons)}")
    print(f"  Safety: {', '.join(route.safety)}")
    print(f"  Sanitized input: {route.sanitized_input or 'None'}")


def print_llmgate_test(raw_text):
    broker_context = broker_context_snapshot()
    route = route_sidecar_task(raw_text, context=broker_context)
    context_preview = build_context_budget_preview(broker_context)
    print("🧪 LLMGate Test")
    print(f"  Route status: {route.status}")
    print(f"  Model: {route.model}")
    print(f"  Tier: {route.tier}")
    print(f"  Privacy risk: {route.privacy_risk}")
    print(f"  External allowed: {route.external_allowed}")
    print(f"  Context allowed: {context_preview.allowed_for_external_model}")
    print(f"  Context chars: {context_preview.used_chars}/{context_preview.max_chars}")
    print(f"  Fallbacks: {', '.join(route.fallback_models) if route.fallback_models else 'none'}")
    if route.blocked_reasons:
        print(f"  Blocked by: {', '.join(route.blocked_reasons)}")
    if route.status == "blocked":
        print("  Execute: skipped")
        print(f"  Sanitized input: {route.sanitized_input or 'None'}")
        return
    if not context_preview.allowed_for_external_model:
        print("  Execute: skipped")
        print(f"  Blocked by context: {', '.join(context_preview.blocked_reasons)}")
        return

    sidecar_prompt = build_llmgate_sidecar_prompt(route.sanitized_input, context_preview.packet)
    response, debug, used_model = call_llmgate_with_fallbacks(
        [route.model] + list(route.fallback_models),
        sidecar_prompt,
    )
    print(f"  Used model: {used_model}")
    print(f"  Helper debug: {debug}")
    if response:
        print(f"  Response: {response}")
    else:
        print("  Response: None")


def print_intent_test(raw_text):
    plan = plan_intent(raw_text, context=broker_context_snapshot())
    print("🧠 Intent Plan")
    print(f"  Status: {plan.status}")
    print(f"  Intent: {plan.intent}")
    print(f"  Risk: {plan.risk}")
    print(f"  Policy: {plan.policy}")
    print(f"  Needs confirm: {plan.needs_confirm}")
    print(f"  Reason: {plan.reason}")
    print(f"  Plan: {plan.plan}")
    print(f"  Actions: {', '.join(plan.actions) if plan.actions else 'none'}")
    print(f"  Privacy risk: {plan.privacy_risk}")
    if plan.blocked_reasons:
        print(f"  Blocked by: {', '.join(plan.blocked_reasons)}")
    print(f"  Safety: {', '.join(plan.safety)}")
    print(f"  Sanitized text: {plan.sanitized_text or 'None'}")





























































































































































































































































































def phase_progress_percent(summary):
    summary = summary or {}
    total = summary.get('total') or 0
    if not total:
        return 0
    return int(round((summary.get('pass_count', 0) / total) * 100))


def final_phase_rows(phase, vts=None, voice=None):
    return {
        'rows': [],
        'failures': [],
        'pass_count': 0,
        'total': 0,
        'phase': phase,
    }


def social_vibe_topic_groups(topic=''):
    return []
