import time


def evaluate_attention_window(context):
    zone = context.get("zone") or "unknown"
    app = (context.get("active_app") or "").lower()
    browser_kind = context.get("browser_kind") or "unknown"
    idle_state = context.get("idle_state") or "active"
    idle_seconds = float(context.get("idle_seconds") or 0)
    in_flow = bool(context.get("in_flow"))
    last_chat_age = context.get("last_chat_age")

    if in_flow:
        return _window("work_flow", "mute", "flow_active")
    if zone == "war_zone":
        if idle_state == "active":
            return _window("work_active", "mute", "active_work_zone")
        return _window("work_paused", "observe", "war_zone_not_active")
    if idle_state == "sleepy":
        return _window("away", "low", "idle_sleepy")
    if idle_state == "relaxed" and idle_seconds >= 300:
        return _window("idle", "low", "idle_relaxed")
    if zone == "game":
        return _window("game", "mute", "game_zone")
    if zone == "chill" and app in {"msedge", "edge", "chrome", "firefox"}:
        if browser_kind in {"social", "youtube", "music", "video", "shopping", "ai_tools"}:
            return _window("browse_active", "available", f"browser_{browser_kind}")
        return _window("browse_light", "observe", "browser_unknown_or_plain")
    if zone == "chill":
        return _window("chill", "available", "chill_zone")
    if last_chat_age is not None and last_chat_age < 30:
        return _window("recent_chat", "observe", "recent_chat")
    return _window("unknown", "observe", "default_unknown")


def format_attention_status(context):
    attention = evaluate_attention_window(context)
    last_chat_age = context.get("last_chat_age")
    chat_text = "never" if last_chat_age is None else f"{last_chat_age:.1f}s ago"
    return [
        "👁️ Attention Window",
        f"  Window: {attention['window']}",
        f"  Ambient policy: {attention['ambient_policy']}",
        f"  Reason: {attention['reason']}",
        f"  Zone: {context.get('zone')} | App: {context.get('active_app')} | Flow: {context.get('in_flow')}",
        f"  Idle: {context.get('idle_state')} ({float(context.get('idle_seconds') or 0):.1f}s)",
        f"  Browser kind: {context.get('browser_kind')}",
        f"  Last chat: {chat_text}",
    ]


def context_for_attention(context_state):
    now = time.time()
    browser = context_state.get("browser") or {}
    last_chat = context_state.get("last_chat_time") or 0
    return {
        "zone": context_state.get("active_zone"),
        "active_app": context_state.get("active_app"),
        "idle_state": context_state.get("idle_state"),
        "idle_seconds": context_state.get("idle_seconds"),
        "in_flow": context_state.get("in_flow"),
        "browser_kind": browser.get("kind"),
        "last_chat_age": None if not last_chat else max(0.0, now - last_chat),
    }


def _window(window, ambient_policy, reason):
    return {
        "window": window,
        "ambient_policy": ambient_policy,
        "reason": reason,
    }
