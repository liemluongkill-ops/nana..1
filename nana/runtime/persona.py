import time

from nana.memory import memory, memory_lock, save_memory_async


VALID_PERSONA_MODES = {"chill", "focus", "technical", "social"}

MODE_DEFAULT_INTENSITY = {
    "chill": 35,
    "focus": 15,
    "technical": 10,
    "social": 55,
}

SILENCE_WINDOW_SECONDS = 600
DEFAULT_AMBIENT_LIMIT = 3
MAX_TRANSITION_LOG = 40
PRESENCE_RECOVERY_SECONDS = 180

RESIDUE_DECAY_PER_TICK = {
    "social": 8,
    "vision": 10,
    "voice": 6,
    "focus": 3,
    "ambient": 5,
    "recovery": 4,
    "legacy": 5,
    "unknown": 5,
}

MODE_LABELS = {
    "chill": "chill",
    "focus": "focus",
    "technical": "technical",
    "social": "social",
}

MODE_RULES = {
    "chill": "ấm, tự nhiên, vừa đủ thân; không kéo dài roleplay.",
    "focus": "gọn, ít đùa, không chen personality vào flow làm việc.",
    "technical": "factual, concise, ưu tiên phân tích và bước làm rõ ràng.",
    "social": "bắt vibe social khi được hỏi, nhưng không để social tone tràn sang chat nhà.",
}

TECHNICAL_MARKERS = [
    "traceback",
    "exception",
    "error",
    "bug",
    "log",
    "runtime",
    "compile",
    "test",
    "pytest",
    "python",
    "code",
    "debug",
    "stack",
    "api",
    "websocket",
    "config",
]

FOCUS_MARKERS = [
    "tập trung",
    "tap trung",
    "focus",
    "làm việc",
    "lam viec",
    "nghiêm túc",
    "nghiem tuc",
]


def default_persona_state():
    now = time.time()
    return {
        "mode": "chill",
        "personality_intensity": MODE_DEFAULT_INTENSITY["chill"],
        "target_intensity": MODE_DEFAULT_INTENSITY["chill"],
        "manual_until": 0.0,
        "last_decay": now,
        "last_reset": now,
        "residue_level": 0,
        "residue_sources": {},
        "last_reason": "default",
        "transition_log": [],
        "silence": {
            "window_start": now,
            "ambient_count": 0,
            "ambient_limit": DEFAULT_AMBIENT_LIMIT,
        },
        "presence": {
            "enabled": True,
            "rhythm": "available",
            "last_reason": "default",
            "last_update": now,
            "quiet_until": 0.0,
        },
    }


def ensure_persona_state_locked():
    state = memory.setdefault("persona", {})
    defaults = default_persona_state()
    for key, value in defaults.items():
        state.setdefault(key, value)
    if state.get("mode") not in VALID_PERSONA_MODES:
        state["mode"] = "chill"
    state["personality_intensity"] = clamp_intensity(state.get("personality_intensity"))
    state["target_intensity"] = clamp_intensity(state.get("target_intensity"))
    normalize_residue_sources_locked(state)
    state.setdefault("transition_log", [])
    state.setdefault("silence", default_persona_state()["silence"])
    state.setdefault("presence", default_persona_state()["presence"])
    for key, value in default_persona_state()["presence"].items():
        state["presence"].setdefault(key, value)
    return state


def clamp_intensity(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = MODE_DEFAULT_INTENSITY["chill"]
    return max(0, min(100, value))


def clamp_residue(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 0
    return max(0, min(100, value))


def decay_persona_state(now=None):
    now = now or time.time()
    changed = False
    with memory_lock:
        state = ensure_persona_state_locked()
        last_decay = float(state.get("last_decay") or now)
        elapsed = max(0.0, now - last_decay)
        if elapsed < 30:
            return dict(state)

        mode = state.get("mode") or "chill"
        manual_until = float(state.get("manual_until") or 0)
        if manual_until and now > manual_until:
            previous_mode = mode
            state["mode"] = "chill"
            state["manual_until"] = 0.0
            state["last_reason"] = "manual_expired"
            state["target_intensity"] = MODE_DEFAULT_INTENSITY["chill"]
            mode = "chill"
            record_transition_locked(
                state,
                from_mode=previous_mode,
                to_mode=mode,
                reason="manual_expired",
                now=now,
            )
            changed = True

        target = clamp_intensity(state.get("target_intensity", MODE_DEFAULT_INTENSITY.get(mode, 35)))
        current = clamp_intensity(state.get("personality_intensity"))
        step = max(1, int(elapsed // 30))
        if current > target:
            current = max(target, current - step * 3)
            changed = True
        elif current < target:
            current = min(target, current + step * 2)
            changed = True

        previous_residue = clamp_residue(state.get("residue_level"))
        residue = decay_residue_sources_locked(state, step)
        if residue != previous_residue:
            changed = True
            if previous_residue > 0 and residue == 0:
                state["last_reason"] = "residue_cleared"
                record_transition_locked(
                    state,
                    from_mode=state.get("mode", "chill"),
                    to_mode=state.get("mode", "chill"),
                    reason="residue_cleared",
                    now=now,
                    extra={"residue": 0, "sources": {}},
                )

        state["personality_intensity"] = current
        state["target_intensity"] = target
        state["residue_level"] = residue
        state["last_decay"] = now
        refresh_silence_window_locked(state, now)
        snapshot = dict(state)

    if changed:
        save_memory_async()
    return snapshot


def set_persona_mode(mode, reason="manual", minutes=0, immediate=True):
    mode = (mode or "").strip().lower()
    if mode not in VALID_PERSONA_MODES:
        return None
    now = time.time()
    with memory_lock:
        state = ensure_persona_state_locked()
        previous_mode = state.get("mode", "chill")
        previous_intensity = clamp_intensity(state.get("personality_intensity"))
        state["mode"] = mode
        state["target_intensity"] = MODE_DEFAULT_INTENSITY[mode]
        if immediate:
            state["personality_intensity"] = MODE_DEFAULT_INTENSITY[mode]
        else:
            state["personality_intensity"] = ease_intensity_toward(
                previous_intensity,
                MODE_DEFAULT_INTENSITY[mode],
                step=8,
            )
        state["manual_until"] = now + minutes * 60 if minutes else 0.0
        state["last_decay"] = now
        state["last_reason"] = reason
        record_transition_locked(
            state,
            from_mode=previous_mode,
            to_mode=mode,
            reason=reason,
            now=now,
        )
        snapshot = dict(state)
    save_memory_async()
    return snapshot


def reset_persona(reason="manual_reset"):
    now = time.time()
    with memory_lock:
        state = ensure_persona_state_locked()
        previous_mode = state.get("mode", "chill")
        state.update(
            {
                "mode": "chill",
                "personality_intensity": MODE_DEFAULT_INTENSITY["chill"],
                "target_intensity": MODE_DEFAULT_INTENSITY["chill"],
                "manual_until": 0.0,
                "last_decay": now,
                "last_reset": now,
                "residue_level": 0,
                "residue_sources": {},
                "last_reason": reason,
            }
        )
        reset_silence_budget_locked(state, now)
        state["presence"] = {
            "enabled": True,
            "rhythm": "available",
            "last_reason": reason,
            "last_update": now,
            "quiet_until": 0.0,
        }
        record_transition_locked(
            state,
            from_mode=previous_mode,
            to_mode="chill",
            reason=reason,
            now=now,
        )
        snapshot = dict(state)
    save_memory_async()
    return snapshot


def persona_state_snapshot():
    return decay_persona_state()


def observe_text_for_persona(text):
    lowered = (text or "").lower()
    with memory_lock:
        state = ensure_persona_state_locked()
        manual_until = float(state.get("manual_until") or 0)
    if manual_until and time.time() < manual_until:
        return persona_state_snapshot()

    if any(marker in lowered for marker in TECHNICAL_MARKERS):
        return set_persona_mode("technical", reason="auto_technical", minutes=8, immediate=False)
    if any(marker in lowered for marker in FOCUS_MARKERS):
        return set_persona_mode("focus", reason="auto_focus", minutes=8, immediate=False)
    return persona_state_snapshot()


def mark_residue(source, level=20, reason=None):
    source = sanitize_residue_source(source)
    reason = reason or f"{source}_residue_marked"
    now = time.time()
    with memory_lock:
        state = ensure_persona_state_locked()
        old_residue = clamp_residue(state.get("residue_level"))
        sources = normalize_residue_sources_locked(state)
        sources[source] = max(clamp_residue(sources.get(source)), clamp_residue(level))
        state["residue_level"] = residue_total(sources)
        state["last_reason"] = f"{source}_context_seen"
        if state["residue_level"] != old_residue:
            record_transition_locked(
                state,
                from_mode=state.get("mode", "chill"),
                to_mode=state.get("mode", "chill"),
                reason=reason,
                now=now,
                extra={"residue": state["residue_level"], "sources": dict(sources)},
            )
        snapshot = dict(state)
    save_memory_async()
    return snapshot


def mark_social_residue(level=20):
    return mark_residue("social", level=level, reason="social_residue_marked")


def persona_prompt_block(casual_mode=False):
    state = persona_state_snapshot()
    mode = state.get("mode", "chill")
    intensity = effective_prompt_intensity(state)
    residue = clamp_residue(state.get("residue_level"))
    clamp = temperature_clamp(state)
    casual_line = "- Casual ping: trả lời 1 câu ngắn rồi dừng." if casual_mode else ""
    return f"""
Persona Governor:
- Current mode: {mode}
- Personality intensity: {intensity}/100
- Temperature clamp: {clamp}
- Mode rule: {MODE_RULES.get(mode, MODE_RULES['chill'])}
- Priority: safety/privacy > direct task > current chat/voice > focus/debug > fresh context > personality > stale context.
- Personality là ambient layer, không phải core logic.
- Stale context hoặc social residue không được tự mở topic mới.
- Nếu mode là technical/focus: trả lời rõ, ngắn, actionable; giảm đùa và không roleplay.
- Nếu mode là social: chỉ dùng social vibe khi đang xử lý social task; không để tone social tràn sang chat riêng với Ba.
- Có phản ứng thì phải có kết thúc: không kéo mood cũ nếu câu hiện tại không cần.
- Residue level: {residue}/100; nếu cao, càng phải bám task hiện tại và tránh callback/lore cũ.
- Nếu clamp là strict/moderate: giảm humor, emoji, phóng đại và callback cũ; trả lời theo task trước.
{casual_line}
""".strip()


def social_prompt_guard():
    state = persona_state_snapshot()
    return (
        "Persona Governor for social draft:\n"
        f"- Current private mode: {state.get('mode')} | intensity={effective_prompt_intensity(state)}/100 | clamp={temperature_clamp(state)}.\n"
        "- Public social draft rules override private persona.\n"
        "- Không để lore/chat nhà với Ba tràn vào reply public.\n"
        "- Nếu request không hỏi Teo-lite/cà khịa, giữ reply tự nhiên, ngắn, sạch.\n"
    )


def ambient_reaction_allowed(topic=None):
    state = persona_state_snapshot()
    mode = state.get("mode", "chill")
    if mode in {"focus", "technical"}:
        return False, f"persona_{mode}"
    if clamp_residue(state.get("residue_level")) >= 70:
        return False, "persona_residue_high"
    if not silence_budget_available(state):
        return False, "silence_budget"
    return True, "ok"


def evaluate_presence_rhythm(context, commit=True):
    now = time.time()
    zone = context.get("zone") or "unknown"
    idle_state = context.get("idle_state") or "active"
    in_flow = bool(context.get("in_flow"))
    last_chat_age = context.get("last_chat_age")
    active_app = context.get("active_app")

    state = persona_state_snapshot()
    presence = dict(state.get("presence") or {})
    if not presence.get("enabled", True):
        return presence

    mode = state.get("mode", "chill")
    if mode in {"focus", "technical"}:
        return update_presence_rhythm("quiet", f"persona_{mode}", quiet_seconds=PRESENCE_RECOVERY_SECONDS, commit=commit)
    quiet_until = float(presence.get("quiet_until") or 0)
    if quiet_until and now < quiet_until:
        if presence.get("rhythm") != "quiet":
            return update_presence_rhythm(
                "quiet",
                presence.get("last_reason") or "quiet_until_active",
                quiet_until=quiet_until,
                commit=commit,
            )
        return presence
    if in_flow:
        return update_presence_rhythm("quiet", "flow_active", commit=commit)
    if zone == "war_zone" and idle_state == "active":
        return update_presence_rhythm("observe", "active_work", commit=commit)
    if zone == "game":
        return update_presence_rhythm("quiet", "game_zone", quiet_seconds=PRESENCE_RECOVERY_SECONDS, commit=commit)
    if last_chat_age is not None and last_chat_age < 20:
        return update_presence_rhythm("observe", "recent_chat", commit=commit)
    if idle_state == "sleepy":
        return update_presence_rhythm("low", "idle_sleepy", commit=commit)
    if zone == "chill":
        return update_presence_rhythm("available", "chill_zone", commit=commit)
    if active_app in {None, ""}:
        return update_presence_rhythm("low", "unknown_app", commit=commit)
    return update_presence_rhythm("observe", "default_observe", commit=commit)


def presence_rhythm_allowed(topic, context):
    rhythm = evaluate_presence_rhythm(context, commit=True)
    current = rhythm.get("rhythm")
    if not rhythm.get("enabled", True):
        return False, "presence_disabled", rhythm
    if current == "quiet":
        return False, rhythm.get("last_reason", "presence_quiet"), rhythm
    if current == "observe" and topic != "browser_presence":
        return False, "presence_observe_context_silent", rhythm
    if current == "low" and topic == "context":
        return False, "presence_low_context_silent", rhythm
    return True, "ok", rhythm


def update_presence_rhythm(rhythm, reason, quiet_seconds=0, quiet_until=None, commit=True):
    now = time.time()
    if not commit:
        quiet_target = 0.0
        if quiet_until is not None:
            quiet_target = quiet_until
        elif quiet_seconds:
            quiet_target = now + quiet_seconds
        return {
            "enabled": True,
            "rhythm": rhythm,
            "last_reason": reason,
            "last_update": now,
            "quiet_until": quiet_target,
        }
    changed = False
    with memory_lock:
        state = ensure_persona_state_locked()
        presence = state.setdefault("presence", default_persona_state()["presence"])
        if presence.get("rhythm") != rhythm or presence.get("last_reason") != reason:
            changed = True
        presence["enabled"] = bool(presence.get("enabled", True))
        presence["rhythm"] = rhythm
        presence["last_reason"] = reason
        presence["last_update"] = now
        if quiet_until is not None:
            presence["quiet_until"] = quiet_until
        elif quiet_seconds:
            presence["quiet_until"] = now + quiet_seconds
        else:
            presence["quiet_until"] = 0.0
        snapshot = dict(presence)
    if changed:
        save_memory_async()
    return snapshot


def set_presence_rhythm_enabled(enabled):
    now = time.time()
    with memory_lock:
        state = ensure_persona_state_locked()
        presence = state.setdefault("presence", default_persona_state()["presence"])
        presence["enabled"] = bool(enabled)
        presence["last_reason"] = "manual_presence_on" if enabled else "manual_presence_off"
        presence["last_update"] = now
        presence["rhythm"] = "available" if enabled else "quiet"
        presence["quiet_until"] = 0.0
        snapshot = dict(presence)
    save_memory_async()
    return snapshot


def reset_presence_rhythm():
    now = time.time()
    with memory_lock:
        state = ensure_persona_state_locked()
        state["presence"] = {
            "enabled": True,
            "rhythm": "available",
            "last_reason": "manual_presence_reset",
            "last_update": now,
            "quiet_until": 0.0,
        }
        snapshot = dict(state["presence"])
    save_memory_async()
    return snapshot


def record_ambient_reaction(kind="ambient"):
    now = time.time()
    with memory_lock:
        state = ensure_persona_state_locked()
        silence = refresh_silence_window_locked(state, now)
        silence["ambient_count"] = int(silence.get("ambient_count") or 0) + 1
        state["last_reason"] = f"ambient_reaction:{kind}"
        snapshot = dict(state)
    save_memory_async()
    return snapshot


def format_persona_status():
    state = persona_state_snapshot()
    manual_until = float(state.get("manual_until") or 0)
    manual_text = "inactive"
    if manual_until and time.time() < manual_until:
        manual_text = f"active {manual_until - time.time():.0f}s"
    silence = state.get("silence") or {}
    sources = format_residue_sources(state)
    window_start = float(silence.get("window_start") or time.time())
    window_age = max(0.0, time.time() - window_start)
    ambient_count = int(silence.get("ambient_count") or 0)
    ambient_limit = int(silence.get("ambient_limit") or DEFAULT_AMBIENT_LIMIT)
    return [
        "🧭 Persona Governor",
        f"  Mode: {state.get('mode')}",
        f"  Intensity: {state.get('personality_intensity')}/100",
        f"  Target intensity: {state.get('target_intensity')}/100",
        f"  Temperature clamp: {temperature_clamp(state)}",
        f"  Residue: {state.get('residue_level')}/100",
        f"  Residue sources: {sources}",
        f"  Silence budget: {ambient_count}/{ambient_limit} in {window_age:.0f}s/{SILENCE_WINDOW_SECONDS}s",
        f"  Manual override: {manual_text}",
        f"  Last reason: {state.get('last_reason')}",
        f"  Rule: {MODE_RULES.get(state.get('mode'), MODE_RULES['chill'])}",
        "  Priority: safety > task > current chat > focus/debug > fresh context > personality > stale context",
    ]


def format_presence_rhythm_status(context=None):
    rhythm = evaluate_presence_rhythm(context or {}, commit=False)
    quiet_until = float(rhythm.get("quiet_until") or 0)
    quiet_text = "inactive"
    if quiet_until and time.time() < quiet_until:
        quiet_text = f"active {quiet_until - time.time():.0f}s"
    return [
        "🌗 Presence Rhythm",
        f"  Enabled: {rhythm.get('enabled', True)}",
        f"  Rhythm: {rhythm.get('rhythm')}",
        f"  Last reason: {rhythm.get('last_reason')}",
        f"  Quiet: {quiet_text}",
        "  Rule: ưu tiên im khi focus/technical/flow; chỉ hiện diện nhẹ khi có ngữ cảnh mới đủ đáng.",
    ]


def format_transition_log(limit=10):
    persona_state_snapshot()
    with memory_lock:
        state = ensure_persona_state_locked()
        entries = list(state.get("transition_log", []))[-limit:]
    lines = ["🧾 State Transition Log"]
    if not entries:
        lines.append("  Empty")
        return lines
    for entry in reversed(entries):
        ts = time.strftime("%H:%M:%S", time.localtime(entry.get("time", time.time())))
        lines.append(
            "  "
            f"{ts} | {entry.get('from')} -> {entry.get('to')} | "
            f"reason={entry.get('reason')} | "
            f"intensity={entry.get('intensity')} target={entry.get('target')} "
            f"residue={entry.get('residue')} sources={format_sources_value(entry.get('sources'))}"
        )
    return lines


def clear_transition_log():
    with memory_lock:
        state = ensure_persona_state_locked()
        state["transition_log"] = []
        state["last_reason"] = "transition_log_cleared"
        snapshot = dict(state)
    save_memory_async()
    return snapshot


def ease_intensity_toward(current, target, step=8):
    current = clamp_intensity(current)
    target = clamp_intensity(target)
    if current < target:
        return min(target, current + step)
    if current > target:
        return max(target, current - step)
    return current


def effective_prompt_intensity(state):
    current = clamp_intensity(state.get("personality_intensity"))
    target = clamp_intensity(state.get("target_intensity", current))
    mode = state.get("mode", "chill")
    if mode in {"focus", "technical"}:
        return min(current, target)
    residue = clamp_residue(state.get("residue_level"))
    if residue >= 60:
        return min(current, 25)
    return current


def temperature_clamp(state=None):
    state = state or persona_state_snapshot()
    mode = state.get("mode", "chill")
    intensity = effective_prompt_intensity(state)
    residue = clamp_residue(state.get("residue_level"))
    if mode in {"technical", "focus"} or residue >= 60:
        return "strict"
    if residue >= 30 or (mode != "chill" and intensity <= 30):
        return "moderate"
    return "open"


def silence_budget_available(state=None):
    now = time.time()
    with memory_lock:
        if state is None:
            state = ensure_persona_state_locked()
        silence = refresh_silence_window_locked(state, now)
        ambient_count = int(silence.get("ambient_count") or 0)
        ambient_limit = int(silence.get("ambient_limit") or DEFAULT_AMBIENT_LIMIT)
        return ambient_count < ambient_limit


def refresh_silence_window_locked(state, now):
    silence = state.setdefault("silence", {})
    window_start = float(silence.get("window_start") or now)
    if now - window_start >= SILENCE_WINDOW_SECONDS:
        silence["window_start"] = now
        silence["ambient_count"] = 0
    silence.setdefault("ambient_limit", DEFAULT_AMBIENT_LIMIT)
    silence.setdefault("ambient_count", 0)
    return silence


def reset_silence_budget_locked(state, now):
    state["silence"] = {
        "window_start": now,
        "ambient_count": 0,
        "ambient_limit": DEFAULT_AMBIENT_LIMIT,
    }


def record_transition_locked(state, from_mode, to_mode, reason, now=None, extra=None):
    now = now or time.time()
    log = state.setdefault("transition_log", [])
    entry = {
        "time": now,
        "from": from_mode,
        "to": to_mode,
        "reason": reason,
        "intensity": clamp_intensity(state.get("personality_intensity")),
        "target": clamp_intensity(state.get("target_intensity")),
        "residue": clamp_residue(state.get("residue_level")),
        "sources": dict(state.get("residue_sources") or {}),
    }
    if extra:
        entry.update(extra)
    if log and log[-1].get("from") == entry["from"] and log[-1].get("to") == entry["to"] and log[-1].get("reason") == entry["reason"]:
        log[-1] = entry
    else:
        log.append(entry)
    del log[:-MAX_TRANSITION_LOG]


def sanitize_residue_source(source):
    source = str(source or "unknown").strip().lower().replace(" ", "_")
    allowed = {"social", "vision", "voice", "focus", "ambient", "recovery", "unknown", "legacy"}
    return source if source in allowed else "unknown"


def normalize_residue_sources_locked(state):
    raw_sources = state.get("residue_sources")
    sources = {}
    if isinstance(raw_sources, dict):
        for key, value in raw_sources.items():
            clean_key = sanitize_residue_source(key)
            clean_value = clamp_residue(value)
            if clean_value:
                sources[clean_key] = max(sources.get(clean_key, 0), clean_value)
    legacy_residue = clamp_residue(state.get("residue_level"))
    if legacy_residue and not sources:
        sources["legacy"] = legacy_residue
    state["residue_sources"] = sources
    state["residue_level"] = residue_total(sources)
    return sources


def residue_total(sources):
    if not isinstance(sources, dict):
        return 0
    return min(100, sum(clamp_residue(value) for value in sources.values()))


def decay_residue_sources_locked(state, ticks):
    sources = normalize_residue_sources_locked(state)
    ticks = max(0, int(ticks or 0))
    if not ticks:
        return residue_total(sources)
    decayed = {}
    for key, value in sources.items():
        next_value = max(0, clamp_residue(value) - residue_decay_amount(key, ticks))
        if next_value:
            decayed[key] = next_value
    state["residue_sources"] = decayed
    state["residue_level"] = residue_total(decayed)
    return state["residue_level"]


def residue_decay_amount(source, ticks):
    source = sanitize_residue_source(source)
    per_tick = RESIDUE_DECAY_PER_TICK.get(source, RESIDUE_DECAY_PER_TICK["unknown"])
    return max(0, int(ticks or 0)) * per_tick


def format_residue_sources(state):
    return format_sources_value((state or {}).get("residue_sources"))


def format_sources_value(sources):
    if not isinstance(sources, dict) or not sources:
        return "none"
    parts = [
        f"{key}={clamp_residue(value)}"
        for key, value in sorted(sources.items())
        if clamp_residue(value)
    ]
    return ", ".join(parts) if parts else "none"
