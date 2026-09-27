import asyncio
import random
import time

import keyboard

from nana.autonomy import AUTONOMY_LOOP
from nana.browser.context import BrowserContextReader
from nana.brain.local_helper import apply_local_summary
from nana.brain.reaction_composer import compose_reaction
from nana.config import CHAT_CONTEXT_SUPPRESS, CONTEXT_REACT_COOLDOWN, PROACTIVE_BROWSER_COOLDOWN
from nana.integrations.vts import trigger_expression_lifecycle
from nana.runtime.capabilities import maybe_auto_enable_osu_adapter, maybe_auto_enable_stardew_adapter
from nana.runtime.context import (
    POLL_RATE_MAP,
    accumulate_confidence,
    check_flow,
    classify_zone,
    confidence_state,
    context_lock,
    context_state,
    decay_confidence,
    get_active_window,
    get_confidence,
    get_python_cpu,
    is_in_flow,
    on_keystroke,
    update_browser_state,
)
from nana.runtime.logger import log_event
from nana.runtime.persona import (
    ambient_reaction_allowed,
    presence_rhythm_allowed,
    record_ambient_reaction,
)
from nana.runtime.recovery import recovery_message


def autonomous_output_enabled():
    """Use the canonical autonomy lifecycle as the global ambient-output gate."""
    return AUTONOMY_LOOP.output_enabled


def _clear_suppressed_output_state():
    """Discard confidence and deferred speech while autonomous output is off."""
    with context_lock:
        confidence_state["score"] = 0.0
        context_state["alt_tab_count"] = 0
        context_state["flow_blocked_reaction"] = None


def _sync_browser_presence_baseline(snapshot, reason="autonomy_paused"):
    """Observe browser state silently so resume does not replay stale changes."""
    with context_lock:
        proactive = context_state["proactive"]
        proactive["last_browser_kind"] = getattr(snapshot, "kind", None)
        proactive["last_browser_title"] = getattr(snapshot, "title", None)
        proactive["last_browser_url"] = getattr(snapshot, "url", None)
        proactive["presence_debug"] = {
            "kind_changed": False,
            "title_changed": False,
            "url_changed": False,
            "should_react": False,
            "blocked_reason": reason,
            "debug": None,
        }


async def nana_pulse(vts, loop, voice):
    keyboard.on_press(on_keystroke)
    browser_reader = BrowserContextReader()

    while True:
        try:
            with context_lock:
                poll_rate = context_state["poll_rate"]

            await asyncio.sleep(poll_rate)
            if autonomous_output_enabled():
                decay_confidence()
            else:
                _clear_suppressed_output_state()

            app_name, title = await loop.run_in_executor(None, get_active_window)
            with context_lock:
                old_zone = context_state["active_zone"]
                new_zone = classify_zone(app_name, title)
                context_state["active_app"] = app_name
                context_state["active_title"] = title

                if new_zone != old_zone:
                    context_state["active_zone"] = new_zone
                    context_state["zone_since"] = time.time()
                    log_event("runtime", f"Zone changed: {old_zone} -> {new_zone} ({app_name})")
                    print(f"🪟 Zone: {old_zone} -> {new_zone} ({app_name})")

                context_state["poll_rate"] = POLL_RATE_MAP.get(new_zone, 5.0)

                idle_seconds = time.time() - context_state["last_input_time"]
                context_state["idle_seconds"] = idle_seconds
                if idle_seconds < 300:
                    context_state["idle_state"] = "active"
                elif idle_seconds < 900:
                    context_state["idle_state"] = "relaxed"
                else:
                    context_state["idle_state"] = "sleepy"

                idle_state = context_state["idle_state"]
                zone = context_state["active_zone"]

            auto_adapter = maybe_auto_enable_stardew_adapter(zone, app_name, title)
            if auto_adapter.get("changed"):
                log_event("runtime", f"Stardew adapter auto-enabled ({app_name})")
                print("🎮 Stardew adapter auto-on (zone=game).")
            auto_osu = maybe_auto_enable_osu_adapter(zone, app_name, title)
            if auto_osu.get("changed"):
                log_event("runtime", f"osu! adapter auto-enabled ({app_name})")
                print("osu! adapter auto-on (zone=game).")

            python_cpu = await loop.run_in_executor(None, get_python_cpu)
            with context_lock:
                context_state["python_cpu"] = python_cpu
                if python_cpu > 80:
                    if not context_state["cpu_spike_since"]:
                        context_state["cpu_spike_since"] = time.time()
                else:
                    context_state["cpu_spike_since"] = None

            if autonomous_output_enabled():
                if old_zone == "war_zone" and new_zone == "chill":
                    with context_lock:
                        context_state["alt_tab_count"] += 1
                    accumulate_confidence("alt_tab_to_chill", 0.2)

                if idle_state == "relaxed":
                    accumulate_confidence("idle_relaxed", 0.1)
                elif idle_state == "sleepy":
                    accumulate_confidence("idle_sleepy", 0.3)

                with context_lock:
                    cpu_spike_since = context_state["cpu_spike_since"]
                if cpu_spike_since and time.time() - cpu_spike_since > 300:
                    accumulate_confidence("cpu_spike_long", 0.3)

            if app_name == "msedge":
                browser_snapshot = await loop.run_in_executor(None, browser_reader.read)
                if not browser_snapshot.available:
                    browser_snapshot = browser_reader.from_active_window(app_name, title, browser_snapshot.reason)
                else:
                    browser_snapshot = await loop.run_in_executor(None, apply_local_summary, browser_snapshot)
                update_browser_state(browser_snapshot)
                if browser_snapshot.available:
                    log_event("runtime", f"Browser: {browser_snapshot.browser} | {browser_snapshot.title} | {browser_snapshot.url}")
                    await maybe_browser_presence_react(vts, voice, browser_snapshot, old_zone, new_zone)

            blocked_reaction = check_flow()
            if blocked_reaction:
                if not autonomous_output_enabled():
                    _clear_suppressed_output_state()
                    continue
                print(f"🤖 Nana (delayed): {blocked_reaction}")
                asyncio.create_task(trigger_expression_lifecycle(vts, blocked_reaction, voice=voice, reason="blocked_browser_reaction"))
                voice.say(blocked_reaction)
                continue

            await maybe_context_react(vts, voice)
        except Exception as exc:
            log_event("runtime", f"NanaPulse error: {exc}")
            message = recovery_message("pulse_error", detail=exc)
            if message:
                print(f"🧯 {message}")
            await asyncio.sleep(5)


async def maybe_browser_presence_react(vts, voice, snapshot, old_zone, new_zone):
    if not autonomous_output_enabled():
        _sync_browser_presence_baseline(snapshot)
        return

    if not snapshot.available:
        with context_lock:
            context_state["proactive"]["presence_debug"] = {
                "kind_changed": False,
                "title_changed": False,
                "url_changed": False,
                "should_react": False,
                "blocked_reason": "browser_unavailable",
                "debug": None,
            }
        return

    rhythm_context = build_presence_context()
    rhythm_allowed, rhythm_reason, rhythm = presence_rhythm_allowed("browser_presence", rhythm_context)
    if not rhythm_allowed:
        with context_lock:
            context_state["proactive"]["presence_debug"] = {
                "kind_changed": False,
                "title_changed": False,
                "url_changed": False,
                "should_react": False,
                "blocked_reason": rhythm_reason,
                "debug": rhythm,
            }
        return

    allowed, persona_reason = ambient_reaction_allowed(topic=snapshot.kind)
    if not allowed:
        with context_lock:
            context_state["proactive"]["presence_debug"] = {
                "kind_changed": False,
                "title_changed": False,
                "url_changed": False,
                "should_react": False,
                "blocked_reason": persona_reason,
                "debug": None,
            }
        return

    now = time.time()
    with context_lock:
        proactive = context_state["proactive"]
        enabled = proactive.get("enabled", True)
        last_kind = proactive.get("last_browser_kind")
        last_title = proactive.get("last_browser_title")
        last_url = proactive.get("last_browser_url")
        last_reaction = proactive.get("last_browser_reaction_time") or 0
        in_flow = context_state["in_flow"]
        last_chat = context_state["last_chat_time"]

        kind_changed = snapshot.kind != last_kind
        title_changed = snapshot.title and snapshot.title != last_title
        url_changed = snapshot.url and snapshot.url != last_url

        proactive["last_browser_kind"] = snapshot.kind
        proactive["last_browser_title"] = snapshot.title
        proactive["last_browser_url"] = snapshot.url

    if not enabled:
        with context_lock:
            context_state["proactive"]["presence_debug"] = {
                "kind_changed": kind_changed,
                "title_changed": title_changed,
                "url_changed": url_changed,
                "should_react": False,
                "blocked_reason": "disabled",
                "debug": None,
            }
        return
    if not kind_changed and not title_changed and not url_changed:
        with context_lock:
            context_state["proactive"]["presence_debug"] = {
                "kind_changed": kind_changed,
                "title_changed": title_changed,
                "url_changed": url_changed,
                "should_react": False,
                "blocked_reason": "no_change",
                "debug": None,
            }
        return
    if in_flow:
        with context_lock:
            context_state["proactive"]["presence_debug"] = {
                "kind_changed": kind_changed,
                "title_changed": title_changed,
                "url_changed": url_changed,
                "should_react": False,
                "blocked_reason": "flow",
                "debug": None,
            }
        return
    if now - last_chat < CHAT_CONTEXT_SUPPRESS:
        with context_lock:
            context_state["proactive"]["presence_debug"] = {
                "kind_changed": kind_changed,
                "title_changed": title_changed,
                "url_changed": url_changed,
                "should_react": False,
                "blocked_reason": "recent_chat",
                "debug": None,
            }
        return
    if now - last_reaction < PROACTIVE_BROWSER_COOLDOWN:
        with context_lock:
            context_state["proactive"]["presence_debug"] = {
                "kind_changed": kind_changed,
                "title_changed": title_changed,
                "url_changed": url_changed,
                "should_react": False,
                "blocked_reason": "cooldown",
                "debug": None,
            }
        return

    should_react = False
    if old_zone == "war_zone" and new_zone == "chill" and snapshot.kind in {"youtube", "music", "video", "shopping", "ai_tools"}:
        should_react = True
    elif last_kind and last_kind != snapshot.kind and snapshot.kind in {"youtube", "music", "video", "shopping", "ai_tools"}:
        should_react = True

    if not should_react:
        with context_lock:
            context_state["proactive"]["presence_debug"] = {
                "kind_changed": kind_changed,
                "title_changed": title_changed,
                "url_changed": url_changed,
                "should_react": False,
                "blocked_reason": "guard_no_react",
                "debug": None,
            }
        return

    reaction, debug = compose_reaction(
        event="browser_presence",
        zone=new_zone,
        browser_kind=snapshot.kind,
        title=snapshot.title,
    )
    if not reaction:
        with context_lock:
            context_state["proactive"]["presence_debug"] = {
                "kind_changed": kind_changed,
                "title_changed": title_changed,
                "url_changed": url_changed,
                "should_react": True,
                "blocked_reason": "empty_reaction",
                "debug": debug,
            }
        return

    if not autonomous_output_enabled():
        _sync_browser_presence_baseline(snapshot)
        return

    with context_lock:
        context_state["proactive"]["last_browser_reaction_time"] = now
        context_state["proactive"]["presence_debug"] = {
            "kind_changed": kind_changed,
            "title_changed": title_changed,
            "url_changed": url_changed,
            "should_react": True,
            "blocked_reason": "reacted",
            "debug": debug,
        }

    print(f"🤖 Nana (browser): {reaction}")
    record_ambient_reaction("browser")
    log_event("runtime", f"Browser presence reaction: {debug} | {reaction}")
    voice.say(reaction)
    asyncio.create_task(trigger_expression_lifecycle(vts, reaction, voice=voice, reason="browser_reaction"))


def choose_idle_reaction():
    reaction, _ = compose_reaction(event="idle_relaxed", zone="idle")
    with context_lock:
        context_state["proactive"]["last_idle_reaction"] = reaction
    return reaction


async def maybe_context_react(vts, voice):
    if not autonomous_output_enabled():
        _clear_suppressed_output_state()
        return

    score = get_confidence()

    rhythm_context = build_presence_context()
    rhythm_allowed, rhythm_reason, rhythm = presence_rhythm_allowed("context", rhythm_context)
    if not rhythm_allowed:
        with context_lock:
            context_state["proactive"]["presence_debug"] = {
                "kind_changed": False,
                "title_changed": False,
                "url_changed": False,
                "should_react": False,
                "blocked_reason": rhythm_reason,
                "debug": rhythm,
            }
        return

    allowed, persona_reason = ambient_reaction_allowed(topic="context")
    if not allowed:
        with context_lock:
            context_state["proactive"]["presence_debug"] = {
                "kind_changed": False,
                "title_changed": False,
                "url_changed": False,
                "should_react": False,
                "blocked_reason": persona_reason,
                "debug": None,
            }
        return

    with context_lock:
        last_react = confidence_state["last_context_react"]
        zone = context_state["active_zone"]
        idle_state = context_state["idle_state"]
        alt_tab_count = context_state["alt_tab_count"]
        cpu_spike_since = context_state["cpu_spike_since"]
        in_flow = context_state["in_flow"]
        last_chat = context_state["last_chat_time"]

    if in_flow:
        return
    if time.time() - last_react < CONTEXT_REACT_COOLDOWN:
        return
    if time.time() - last_chat < CHAT_CONTEXT_SUPPRESS:
        return

    reaction = None
    if score >= 0.8:
        if idle_state == "sleepy":
            reaction = random.choice([
                "Ba rời máy lâu thật rồi. Nana cũng buồn ngủ lây.",
                "15 phút không thấy gõ gì. Ba ổn không?",
            ])
        elif alt_tab_count >= 5 and zone == "chill":
            reaction = random.choice([
                "Alt-tab qua YouTube rồi à... bug arc tạm hoãn à Ba?",
                "Ba đang procrastinate hay đang research? Nana không phán xét đâu 😏",
            ])
        elif cpu_spike_since and time.time() - cpu_spike_since > 600:
            reaction = "Python đang nướng CPU 10 phút rồi đó Ba. Script kia ổn không?"
    elif score >= 0.6:
        if zone == "chill" and alt_tab_count >= 3:
            reaction = "Hơi nhiều lần alt-tab rồi đấy Ba..."
        elif idle_state == "relaxed":
            reaction = choose_idle_reaction()
    elif score >= 0.3:
        if zone == "chill" and alt_tab_count >= 2:
            if not autonomous_output_enabled():
                _clear_suppressed_output_state()
                return
            asyncio.create_task(trigger_expression_lifecycle(vts, "星星眼", voice=voice, reason="context_expression_only"))
            return

    if not reaction:
        return

    if not autonomous_output_enabled():
        _clear_suppressed_output_state()
        return

    with context_lock:
        confidence_state["last_context_react"] = time.time()
        confidence_state["score"] = 0.0
        context_state["alt_tab_count"] = 0

    if is_in_flow():
        with context_lock:
            context_state["flow_blocked_reaction"] = reaction
        print(f"🌊 Holding reaction (flow): {reaction}")
        return

    print(f"🤖 Nana (context): {reaction}")
    record_ambient_reaction("context")
    log_event("runtime", f"Context reaction: {reaction}")
    voice.say(reaction)
    asyncio.create_task(trigger_expression_lifecycle(vts, reaction, voice=voice, reason="context_reaction"))


def build_presence_context():
    now = time.time()
    with context_lock:
        last_chat = context_state.get("last_chat_time") or 0
        return {
            "zone": context_state.get("active_zone"),
            "idle_state": context_state.get("idle_state"),
            "in_flow": context_state.get("in_flow"),
            "active_app": context_state.get("active_app"),
            "last_chat_age": None if not last_chat else max(0.0, now - last_chat),
        }
