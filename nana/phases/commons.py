"""
Phase constants — tất cả global dict/set/list constants dùng trong phase helpers.
Tách khỏi main.py (GĐ 0.4).
"""

import time

# KNOWN_SLASH_COMMANDS — must be imported here (before phase modules)
from nana.commands.registry import KNOWN_SLASH_COMMANDS

# Runtime event log (from main.py line 504)
RUNTIME_EVENT_LOG = []

# Phase 9 audit log (from main.py line 502)
PHASE9_AUDIT_LOG = []

# Autonomy lock constants (from main.py lines 451-452)
AUTONOMY_LOCK_PHASE = "Phase 5-10"
AUTONOMY_LOCK_RULE = "no_autonomy_no_semi_autonomy"

# Phase 7 pending plan — shared placeholder (defined in phase12.py line 58)
PHASE7_PENDING_PLAN = None

# ── Autonomy lock ────────────────────────────────────────────────────────────

AUTONOMY_LOCK_BLOCKED_ACTIONS = {
    "browser.click",
    "browser.type",
    "social.type_draft",
    "message.send",
    "purchase.checkout",
}

# ── Runtime turn state ───────────────────────────────────────────────────────

RUNTIME_TURN_STATE = {
    "status": "idle",
    "last_update": time.time(),
    "last_reason": "startup",
    "source": "runtime",
}

# ── Casual pools (banter) ────────────────────────────────────────────────────

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

# ── Phase 7 ─────────────────────────────────────────────────────────────────

PHASE7_VIRTUAL_ACTIONS = {
    "social.draft": ("virtual", "draft_preview_only"),
    "message.draft": ("virtual", "message_preview_only"),
    "memory.review": ("virtual", "memory_review_preview_only"),
}

PHASE7_BASE_GUARD_CONTEXT = {
    "browser_available": True,
    "browser_fresh": True,
    "browser_snapshot_state": "FRESH",
    "active_app_is_edge": True,
    "active_window_valid": True,
}

PHASE7_SOCIAL_GUARD_CONTEXT = {
    **PHASE7_BASE_GUARD_CONTEXT,
    "browser_kind": "social",
    "browser_social_post_text": "Bài test social",
}

# ── Phase 10 ─────────────────────────────────────────────────────────────────

PHASE10_REQUIRED_COMMANDS = {
    "/phase6-status",
    "/phase6-ready",
    "/phase7-status",
    "/phase7-ready",
    "/phase8-status",
    "/phase8-ready",
    "/phase9-status",
    "/phase9-ready",
    "/phase10-status",
    "/phase10-ready",
    "/phase10-guard-status",
    "/phase10-1-status",
    "/phase10-1-ready",
    "/runtime-map",
    "/cns-map",
    "/phase10-2-status",
    "/phase10-2-ready",
    "/command-router-status",
    "/command-router-guard-status",
    "/phase10-3-status",
    "/phase10-3-ready",
    "/runtime-state-schema",
    "/state-schema-guard-status",
    "/phase10-4-status",
    "/phase10-4-ready",
    "/event-store-status",
    "/event-store-guard-status",
    "/phase10-5-status",
    "/phase10-5-ready",
    "/scheduler-status",
    "/scheduler-guard-status",
    "/phase10-6-status",
    "/phase10-6-ready",
    "/recovery-governor-status",
    "/recovery-governor-guard-status",
    "/phase10-7-status",
    "/phase10-7-ready",
    "/memory-governance-status",
    "/memory-governance-guard-status",
    "/phase10-8-status",
    "/phase10-8-ready",
    "/presence-stability-status",
    "/presence-stability-guard-status",
    "/phase10-9-status",
    "/phase10-9-ready",
    "/social-vision-decouple-status",
    "/social-vision-decouple-guard-status",
    "/phase10-10-status",
    "/phase10-10-ready",
    "/cns-gate-status",
    "/cns-gate-guard-status",
    "/context-confidence",
    "/context-recovery",
    "/video-context",
    "/dry-run-guard-status",
    "/plan-guard-status",
    "/broker-guard-status",
    "/pre-exec-guard-status",
    "/action-trace-guard-status",
    "/audit-guard-status",
    "/audit-clear",
}

# Phase 10.1

PHASE10_1_REQUIRED_GROUPS = {
    "context_state",
    "browser_snapshot",
    "runtime_refresh_cache",
    "runtime_reconcile",
    "runtime_queue",
    "phase7_dry_run_history",
    "phase7_pending_plan",
    "phase7_action_log",
    "phase9_audit_log",
    "runtime_event_log",
    "runtime_turn_state",
    "memory_store",
    "persona_presence",
    "vision_preview_cache",
    "social_drafts",
    "pending_actions",
    "voice_queue",
    "vts_session",
}

PHASE10_1_COMMANDS = {
    "/runtime-map",
    "/cns-map",
    "/phase10-1-status",
    "/phase10-1-ready",
}

# Phase 10.2

PHASE10_2_COMMANDS = {
    "/command-router-status",
    "/command-router-guard-status",
    "/command-normalize-test",
    "/command-route-test",
    "/phase10-2-status",
    "/phase10-2-ready",
    "/phase10-2-guard-status",
    "/p10-2",
    "/p10-2-ready",
}

PHASE10_2_EXPECTED_ALIASES = {
    "/action-propose-edge",
    "/attention-status",
    "/broker-test",
    "/broker-test-edge",
    "/cancel",
    "/chillmode",
    "/confirm",
    "/clear-recovery",
    "/clear-vibe-log",
    "/context-budget",
    "/debug-mode",
    "/focusmode",
    "/fx",
    "/memory-action-plan",
    "/memory-label",
    "/memory-preview",
    "/next-step",
    "/p5",
    "/pa",
    "/persona-log",
    "/persona-status",
    "/phase8-action-trace-guard-status",
    "/reconcile-guards",
    "/recovery-status",
    "/social-guard-fails",
    "/socialmode",
    "/state-log",
    "/tech-mode",
    "/vc",
    "/vibe-log",
    "/vibe-reset",
}

# Phase 10.3

PHASE10_3_COMMANDS = {
    "/runtime-state-schema",
    "/state-schema",
    "/state-schema-guard-status",
    "/phase10-3-status",
    "/phase10-3-ready",
    "/phase10-3-guard-status",
    "/p10-3",
    "/p10-3-ready",
}

PHASE10_3_REQUIRED_SCHEMA_GROUPS = {
    "context_state",
    "browser_snapshot",
    "proactive_state",
    "runtime_latency",
    "runtime_reconcile",
    "runtime_event_log",
    "runtime_turn_state",
    "runtime_queue",
    "phase7_pending_plan",
    "phase9_audit_log",
    "memory_store",
    "persona_state",
    "vision_description",
    "draft_store",
    "pending_action_store",
}

# ── Type aliases (for state schema) ────────────────────────────────────────

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

# Phase 10.4

PHASE10_4_COMMANDS = {
    "/event-log",
    "/event-log-clear",
    "/event-replay",
    "/event-store-status",
    "/event-store-guard-status",
    "/phase10-4-status",
    "/phase10-4-ready",
    "/phase10-4-guard-status",
    "/p10-4",
    "/p10-4-ready",
}

RUNTIME_EVENT_REQUIRED_FIELDS = {
    "id",
    "time",
    "channel",
    "event",
    "source",
    "intent",
    "status",
    "actions",
    "detail",
    "audit_id",
    "execute",
}

# Phase 10.5

PHASE10_5_COMMANDS = {
    "/scheduler-status",
    "/scheduler-guard-status",
    "/scheduler-test",
    "/queue-core-status",
    "/queue-core-guard-status",
    "/phase10-5-status",
    "/phase10-5-ready",
    "/phase10-5-guard-status",
    "/p10-5",
    "/p10-5-ready",
}

# Phase 10.6

PHASE10_6_COMMANDS = {
    "/recovery-governor-status",
    "/recovery-governor-guard-status",
    "/recovery-test",
    "/phase10-6-status",
    "/phase10-6-ready",
    "/phase10-6-guard-status",
    "/p10-6",
    "/p10-6-ready",
}

# Phase 10.7

PHASE10_7_COMMANDS = {
    "/memory-governance-status",
    "/memory-governance-guard-status",
    "/memory-governance-test",
    "/phase10-7-status",
    "/phase10-7-ready",
    "/phase10-7-guard-status",
    "/p10-7",
    "/p10-7-ready",
}

# Phase 10.8

PHASE10_8_COMMANDS = {
    "/presence-stability-status",
    "/presence-stability-guard-status",
    "/presence-stability-test",
    "/phase10-8-status",
    "/phase10-8-ready",
    "/phase10-8-guard-status",
    "/p10-8",
    "/p10-8-ready",
}

# ── Valid turn statuses ──────────────────────────────────────────────────────

VALID_TURN_STATUSES = {"idle", "listening", "thinking", "talking", "recognizing", "shutdown"}

# Phase 10.9

PHASE10_9_COMMANDS = {
    "/social-vision-decouple-status",
    "/social-vision-decouple-guard-status",
    "/social-vision-decouple-test",
    "/phase10-9-status",
    "/phase10-9-ready",
    "/phase10-9-guard-status",
    "/p10-9",
    "/p10-9-ready",
}

# Phase 10.10

PHASE10_10_COMMANDS = {
    "/cns-gate-status",
    "/cns-gate-guard-status",
    "/cns-gate-test",
    "/phase10-10-status",
    "/phase10-10-ready",
    "/phase10-10-guard-status",
    "/phase10-10-test",
    "/p10-10",
    "/p10-10-ready",
}

# Phase 11.1

PHASE11_1_COMMANDS = {
    "/runtime-stress-status",
    "/runtime-stress-guard-status",
    "/runtime-stress-test",
    "/long-session-status",
    "/phase11-1-status",
    "/phase11-1-ready",
    "/phase11-1-guard-status",
    "/phase11-1-test",
    "/p11-1",
    "/p11-1-ready",
}

# Phase 11.2

PHASE11_2_COMMANDS = {
    "/trust-calibration-status",
    "/trust-calibration-guard-status",
    "/trust-calibration-test",
    "/phase11-2-status",
    "/phase11-2-ready",
    "/phase11-2-guard-status",
    "/phase11-2-test",
    "/p11-2",
    "/p11-2-ready",
}

# ── Trust levels ─────────────────────────────────────────────────────────────

TRUST_LEVELS = {"known", "inferred", "uncertain", "missing_context", "stale_context", "unsafe_to_assume"}

# ── Final Phase commands ────────────────────────────────────────────────────────

FINAL_PHASE_COMMANDS = {
    25: {"/phase25-status", "/phase25-ready", "/phase25-guard-status", "/phase25-test", "/p25", "/p25-ready"},
    26: {"/phase26-status", "/phase26-ready", "/phase26-guard-status", "/phase26-test", "/p26", "/p26-ready"},
    27: {"/phase27-status", "/phase27-ready", "/phase27-guard-status", "/phase27-test", "/p27", "/p27-ready"},
    28: {"/phase28-status", "/phase28-ready", "/phase28-guard-status", "/phase28-test", "/p28", "/p28-ready"},
    29: {"/phase29-status", "/phase29-ready", "/phase29-guard-status", "/phase29-test", "/p29", "/p29-ready"},
    30: {"/phase30-status", "/phase30-ready", "/phase30-guard-status", "/phase30-test", "/p30", "/p30-ready"},
    31: {"/phase31-status", "/phase31-ready", "/phase31-guard-status", "/phase31-test", "/p31", "/p31-ready"},
    32: {"/phase32-status", "/phase32-ready", "/phase32-guard-status", "/phase32-test", "/p32", "/p32-ready"},
}

FINAL_PHASE_META = {
    25: ("Final Stream Measurement Gate", "đóng live stream measurement/pilot enable layer", "~86%"),
    26: ("Runtime Stability Gate", "đóng ổn định runtime/queue/event sau streaming gates", "~88%"),
    27: ("Speech Behavior Gate", "đóng speech cadence/silence/grounding", "~91%"),
    28: ("Memory Continuity Gate", "đóng memory continuity/readiness không ghi bừa", "~94%"),
    29: ("Presence Continuity Gate", "đóng presence rhythm/identity anchor baseline", "~96%"),
    30: ("Safe Action Recovery Gate", "đóng recovery/action safety tổng", "~98%"),
    31: ("Long Session Soak Gate", "đóng soak metrics/drift watch baseline", "~99%"),
    32: ("Full Companion Core Gate", "đóng Companion core trước hậu-32 gameplay cognition", "100%"),
}
