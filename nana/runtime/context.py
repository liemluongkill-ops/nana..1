import threading
import time

import psutil

from nana.config import BROWSER_FRESH_SECONDS

try:
    import win32gui
    import win32process

    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False
    print("⚠️ pywin32 chưa cài - Active Window sẽ bị skip")

context_lock = threading.Lock()

context_state = {
    "active_app": None,
    "active_title": None,
    "active_zone": "unknown",
    "zone_since": time.time(),
    "last_input_time": time.time(),
    "idle_seconds": 0,
    "idle_state": "active",
    "python_cpu": 0.0,
    "cpu_spike_since": None,
    "alt_tab_count": 0,
    "in_flow": False,
    "flow_since": None,
    "last_keystroke": time.time(),
    "flow_blocked_reaction": None,
    "poll_rate": 5.0,
    "last_chat_time": 0,
    "browser": {
        "available": False,
        "browser": None,
        "url": None,
        "title": None,
        "kind": "unknown",
        "reason": "not_checked",
        "page_heading": None,
        "meta_description": None,
        "selected_text": None,
        "dom_debug": None,
        "site_signals": {},
        "social_post_text": None,
        "social_comments": [],
        "social_vibe": None,
        "local_summary": None,
        "local_helper_debug": None,
        "last_update": 0,
        "age_seconds": None,
        "fresh": False,
    },
    "proactive": {
        "enabled": True,
        "last_browser_kind": None,
        "last_browser_title": None,
        "last_browser_url": None,
        "last_browser_reaction_time": 0,
        "last_idle_reaction": None,
        "presence_debug": {
            "kind_changed": False,
            "title_changed": False,
            "url_changed": False,
            "should_react": False,
            "blocked_reason": "not_checked",
            "debug": None,
        },
    },
}

confidence_state = {
    "score": 0.0,
    "last_decay": time.time(),
    "last_context_react": 0,
}

ZONE_MAP = {
    "code": "war_zone",
    "vscode": "war_zone",
    "pycharm": "war_zone",
    "cmd": "war_zone",
    "powershell": "war_zone",
    "terminal": "war_zone",
    "python": "war_zone",
    "youtube": "chill",
    "edge": "chill",
    "msedge": "chill",
    "spotify": "chill",
    "netflix": "chill",
    "vlc": "chill",
    "chrome": "chill",
    "firefox": "chill",
    "discord": "chill",
    "stardew": "game",
    "stardew valley": "game",
    "stardewvalley": "game",
    "osu": "game",
    "osu!": "game",
    "steam": "game",
    "epicgames": "game",
}

POLL_RATE_MAP = {
    "war_zone": 2.0,
    "chill": 7.0,
    "game": 8.0,
    "idle": 10.0,
    "unknown": 5.0,
}


def get_active_window():
    if not WIN32_AVAILABLE:
        return None, None
    try:
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process = psutil.Process(pid)
        app_name = process.name().lower().replace(".exe", "")
        return app_name, title
    except Exception:
        return None, None


def classify_zone(app_name, title):
    if not app_name:
        return "unknown"
    combined = (app_name + " " + (title or "")).lower()
    for keyword, zone in ZONE_MAP.items():
        if keyword in combined:
            return zone
    return "unknown"


def get_python_cpu():
    try:
        total = 0.0
        for process in psutil.process_iter(["name", "cpu_percent"]):
            name = process.info.get("name") or ""
            if "python" in name.lower():
                total += process.info["cpu_percent"] or 0.0
        return total
    except Exception:
        return 0.0


def accumulate_confidence(signal, score):
    with context_lock:
        confidence_state["score"] = min(1.0, confidence_state["score"] + score)
        current = confidence_state["score"]
    print(f"📊 Confidence: {current:.2f} (+{score} from {signal})")


def decay_confidence():
    with context_lock:
        now = time.time()
        elapsed = now - confidence_state["last_decay"]
        ticks = elapsed / 5.0
        confidence_state["score"] *= 0.92 ** ticks
        confidence_state["score"] = max(0.0, confidence_state["score"])
        confidence_state["last_decay"] = now


def get_confidence():
    with context_lock:
        return confidence_state["score"]


def is_in_flow():
    with context_lock:
        return context_state["in_flow"]


def check_flow():
    with context_lock:
        now = time.time()
        idle_since_key = now - context_state["last_keystroke"]
        zone = context_state["active_zone"]
        was_in_flow = context_state["in_flow"]

        if zone == "war_zone" and idle_since_key < 30:
            context_state["in_flow"] = True
            if not was_in_flow:
                context_state["flow_since"] = now
                print("🌊 Flow mode ON")
        else:
            if was_in_flow:
                context_state["in_flow"] = False
                blocked = context_state["flow_blocked_reaction"]
                context_state["flow_blocked_reaction"] = None
                print("🌊 Flow mode OFF")
                return blocked
    return None


def on_keystroke(_):
    with context_lock:
        context_state["last_keystroke"] = time.time()
        context_state["last_input_time"] = time.time()


def mark_chat_time():
    with context_lock:
        context_state["last_chat_time"] = time.time()


def update_browser_state(snapshot):
    now = time.time()
    with context_lock:
        context_state["browser"] = {
            "available": snapshot.available,
            "browser": snapshot.browser,
            "url": snapshot.url,
            "title": snapshot.title,
            "kind": snapshot.kind,
            "reason": snapshot.reason,
            "page_heading": snapshot.page_heading,
            "meta_description": snapshot.meta_description,
            "selected_text": snapshot.selected_text,
            "dom_debug": snapshot.dom_debug,
            "site_signals": snapshot.site_signals or {},
            "social_post_text": snapshot.social_post_text,
            "social_comments": snapshot.social_comments or [],
            "social_vibe": snapshot.social_vibe,
            "local_summary": snapshot.local_summary,
            "local_helper_debug": snapshot.local_helper_debug,
            "last_update": now,
            "age_seconds": 0.0,
            "fresh": snapshot.available,
        }


def sync_proactive_browser_seen(snapshot):
    if not snapshot.available:
        return
    with context_lock:
        proactive = context_state["proactive"]
        proactive["last_browser_kind"] = snapshot.kind
        proactive["last_browser_title"] = snapshot.title
        proactive["last_browser_url"] = snapshot.url


def set_proactive_enabled(enabled):
    with context_lock:
        context_state["proactive"]["enabled"] = bool(enabled)
        context_state["proactive"]["presence_debug"] = {
            "kind_changed": False,
            "title_changed": False,
            "url_changed": False,
            "should_react": False,
            "blocked_reason": "manual_presence_on" if enabled else "manual_presence_off",
            "debug": None,
        }


def reset_proactive_state():
    with context_lock:
        proactive = context_state["proactive"]
        proactive["last_browser_kind"] = None
        proactive["last_browser_title"] = None
        proactive["last_browser_url"] = None
        proactive["last_browser_reaction_time"] = 0
        proactive["presence_debug"] = {
            "kind_changed": False,
            "title_changed": False,
            "url_changed": False,
            "should_react": False,
            "blocked_reason": "reset",
            "debug": None,
        }


def current_time_context():
    now = time.localtime()
    hour = now.tm_hour
    if 5 <= hour < 11:
        part = "sáng"
    elif 11 <= hour < 14:
        part = "trưa"
    elif 14 <= hour < 18:
        part = "chiều"
    elif 18 <= hour < 23:
        part = "tối"
    else:
        part = "khuya"
    return {
        "date": time.strftime("%Y-%m-%d", now),
        "date_vi": time.strftime("%d/%m/%Y", now),
        "time": time.strftime("%H:%M:%S", now),
        "weekday": time.strftime("%A", now),
        "part_of_day": part,
        "timezone": "local",
    }


def context_snapshot():
    with context_lock:
        snapshot = dict(context_state)
        browser = dict(snapshot.get("browser", {}))
        last_update = browser.get("last_update") or 0
        if last_update:
            age = max(0.0, time.time() - last_update)
            browser["age_seconds"] = age
            browser["fresh"] = bool(browser.get("available")) and age <= BROWSER_FRESH_SECONDS
        else:
            browser["age_seconds"] = None
            browser["fresh"] = False
        snapshot["browser"] = browser
        snapshot["time"] = current_time_context()
        return snapshot
