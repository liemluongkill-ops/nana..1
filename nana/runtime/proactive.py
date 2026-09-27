"""Phase 3 & 4 helpers — GĐ 8.
Copied from main.py and runtime/context.py for modularization.
Do NOT run Nana live from here.
"""

from __future__ import annotations

import time

# ── win32 availability (mirrors main.py lines 12-20) ────────────────────────
try:
    import win32gui
    import win32process

    PHASE35_WIN32_AVAILABLE = True
except ImportError:
    win32gui = None
    win32process = None
    PHASE35_WIN32_AVAILABLE = False

# ── ImageGrab availability (mirrors main.py lines 22-28) ──────────────────
try:
    from PIL import ImageGrab

    PHASE36_IMAGEGRAB_AVAILABLE = True
except ImportError:
    ImageGrab = None
    PHASE36_IMAGEGRAB_AVAILABLE = False

# ── psutil (mirrors main.py line 10) ──────────────────────────────────────
import psutil


# ─── Phase 3 helpers (browser awareness) ───────────────────────────────────

def browser_age_seconds(browser: dict) -> float | None:
    """Return age in seconds of browser snapshot, or None if no timestamp."""
    last_update = browser.get("last_update") or 0
    if not last_update:
        return None
    return max(0.0, time.time() - last_update)


BROWSER_FRESH_SECONDS = 30.0


def is_browser_fresh(browser: dict) -> bool:
    """Return True if browser snapshot is within fresh threshold."""
    age = browser_age_seconds(browser)
    if age is None:
        return False
    return bool(browser.get("available")) and age <= BROWSER_FRESH_SECONDS


def format_browser_status(browser: dict) -> str:
    """Compact one-line browser status for Phase 3."""
    age = browser_age_seconds(browser)
    age_text = "None" if age is None else f"{age:.1f}s"
    fresh = is_browser_fresh(browser)
    cdp_state = "connected" if browser.get("available") else "unavailable"
    kind = browser.get("kind") or "unknown"
    title = browser.get("title") or "none"
    url = browser.get("url") or "none"
    return (
        f"🌐 Phase 3 | CDP={cdp_state} | Fresh={fresh} | Age={age_text} | "
        f"Kind={kind} | Title={title[:80]} | URL={url[:100]}"
    )


# ─── Phase 4 helpers (proactive / foundation) ─────────────────────────────

BROWSER_APPS = {"msedge", "edge", "chrome", "firefox", "brave"}


def get_active_window() -> tuple[str | None, str | None]:
    """Return (app_name, title) for the current foreground window."""
    if not PHASE35_WIN32_AVAILABLE:
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


def is_active_window(browser_app: str | None) -> bool:
    """Return True if current active window is a browser."""
    if not browser_app:
        return False
    return browser_app.lower() in BROWSER_APPS


def is_idle(idle_seconds: float | None, threshold: float = 60.0) -> bool:
    """Return True if user has been idle longer than threshold."""
    if idle_seconds is None:
        return False
    return idle_seconds >= threshold


def idle_duration(last_input_time: float | None) -> float:
    """Return seconds since last user input, or 0 if unknown."""
    if last_input_time is None:
        return 0.0
    return max(0.0, time.time() - last_input_time)


def classify_zone(app_name: str | None, title: str | None) -> str:
    """Classify current zone based on app name and window title."""
    if not app_name:
        return "unknown"
    combined = (app_name + " " + (title or "")).lower()

    ZONE_MAP = {
        "code": "chill",
        "cursor": "chill",
        "terminal": "chill",
        "powershell": "chill",
        "cmd": "chill",
        "explorer": "chill",
        "notion": "chill",
        "obsidian": "chill",
        "discord": "chill",
        "telegram": "chill",
        "slack": "chill",
        "messenger": "chill",
        "facebook": "chill",
        "youtube": "chill",
        "netflix": "chill",
        "spotify": "chill",
        "music": "chill",
        "stardew": "game",
        "stardewvalley": "game",
        "osu": "game",
        "game": "game",
        "idle": "idle",
    }

    for keyword, zone in ZONE_MAP.items():
        if keyword in combined:
            return zone
    return "unknown"


# ─── Phase 4 proactive state helpers ──────────────────────────────────────

def sync_proactive(
    proactive_state: dict,
    snapshot: dict,
    now: float | None = None,
) -> dict:
    """Sync proactive state from browser snapshot.

    Args:
        proactive_state: The proactive dict from context_state["proactive"].
        snapshot: Browser snapshot dict (from BrowserSnapshot or context).
        now: Optional monotonic timestamp; defaults to time.time().

    Returns:
        Updated proactive_state dict.
    """
    if now is None:
        now = time.time()

    available = snapshot.get("available", False)
    if not available:
        return proactive_state

    proactive = dict(proactive_state)
    proactive["last_browser_kind"] = snapshot.get("kind")
    proactive["last_browser_title"] = snapshot.get("title")
    proactive["last_browser_url"] = snapshot.get("url")
    proactive["last_sync_time"] = now

    last_kind = proactive_state.get("last_browser_kind")
    last_title = proactive_state.get("last_browser_title")
    last_url = proactive_state.get("last_browser_url")

    kind_changed = last_kind != proactive["last_browser_kind"]
    title_changed = last_title != proactive["last_browser_title"]
    url_changed = last_url != proactive["last_browser_url"]

    proactive["presence_debug"] = {
        "kind_changed": kind_changed,
        "title_changed": title_changed,
        "url_changed": url_changed,
        "should_react": kind_changed or title_changed or url_changed,
        "blocked_reason": None,
        "debug": None,
    }

    return proactive


def reset_proactive(proactive_state: dict) -> dict:
    """Reset proactive state to defaults.

    Args:
        proactive_state: The proactive dict to reset.

    Returns:
        Reset proactive_state dict.
    """
    proactive = dict(proactive_state)
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
    return proactive


def update_proactive(
    proactive_state: dict,
    *,
    enabled: bool | None = None,
    last_reaction_time: float | None = None,
    **extra,
) -> dict:
    """Update specific fields of proactive state.

    Args:
        proactive_state: The proactive dict to update.
        enabled: Set proactive enabled flag.
        last_reaction_time: Update last browser reaction timestamp.
        **extra: Any additional key-value pairs to update.

    Returns:
        Updated proactive_state dict.
    """
    proactive = dict(proactive_state)
    if enabled is not None:
        proactive["enabled"] = bool(enabled)
    if last_reaction_time is not None:
        proactive["last_browser_reaction_time"] = last_reaction_time
    for k, v in extra.items():
        proactive[k] = v
    return proactive


# ─── Phase 3 & 4 status printers ──────────────────────────────────────────

def print_phase3_state(browser: dict | None = None) -> None:
    """Print Phase 3 browser awareness status."""
    if browser is None:
        browser = {}
    age = browser_age_seconds(browser)
    fresh = is_browser_fresh(browser)
    age_text = "None" if age is None else f"{age:.1f}s"
    cdp_state = "connected" if browser.get("available") else "unavailable"

    print("🌐 Phase 3 Browser")
    print(f"  CDP: {cdp_state} ({browser.get('reason')})")
    print(f"  Snapshot: Fresh={fresh} | Age={age_text} | MaxAge={BROWSER_FRESH_SECONDS:.0f}s")
    print(f"  Page: Kind={browser.get('kind')} | Title={browser.get('title')}")
    print(f"  URL: {browser.get('url')}")
    print(f"  Heading: {browser.get('page_heading')}")
    print(f"  Selected: {browser.get('selected_text')}")
    print(f"  DOM debug: {browser.get('dom_debug')}")
    print("  Guardrail: read-only; no click/type/navigate actions")
    print("  Commands: /browser | /br | /br-deep | /browser-refresh | /dom | /focus")


def print_phase4_state() -> None:
    """Print Phase 4 foundation / proactive status overview."""
    print("🧭 Phase 4 Foundation")
    print("  Queue: P0 user input > P1 proactive > P2 background")
    print("  Actions: registry + broker + pending confirmation")
    print("  Guardrail: brain suggests, broker validates, Ba confirms later")
    print("  Commands: /queue | /actions | /broker-test | /action-propose | /pending-action")
    print("  Plan: /action-plan | /suggest")
    print("  Pillar 0: /privacy-test | /context-preview")
    print("  Sidecar router: /route-test <text>")
    print("  LLMGate sidecar: /llmgate-test <text>")
    print("  Intent planner: /intent-test <text>")
    print("  Social policy: /social-policy")
    print("  Social draft: /social-target | /social-classify | /social-draft-test <text>")
    print("  Social draft + vision: /social-draft-vision <text>")
    print("  Vision preview: /vision-preview | /vision-describe")
    print("  Draft queue: /drafts | /draft-show <id> | /draft-confirm <id> | /draft-cancel <id>")
    print("  Persona governor: /vibe-status | /state-log | /reset-vibe | /focus-mode | /technical-mode | /social-mode | /chill-mode")
    print("  Residue: /residue-status | /residue")
    print("  Presence rhythm: /presence | /presence-on | /presence-off | /presence-reset")
    print("  Attention window: /attention")
    print("  Recovery: /recovery | /recovery-clear")
    print("  Runtime dashboard: /nana-status | /ns | /phase5-status")
    print("  Refiner sidecar: /refine-test <draft>")
    print("  Refiner guard: /refine-guard-test <draft>")
    print("  Refiner auto-test: /refine-auto-test <draft>")
    print("  Confirm: /action-confirm <id> | /action-cancel <id>")
    print("  Debug only: /broker-test-edge | /action-propose-edge")
    print("  Real executor: browser.scroll only")


__all__ = [
    "PHASE35_WIN32_AVAILABLE",
    "PHASE36_IMAGEGRAB_AVAILABLE",
    "BROWSER_APPS",
    "BROWSER_FRESH_SECONDS",
    "browser_age_seconds",
    "classify_zone",
    "get_active_window",
    "idle_duration",
    "is_active_window",
    "is_browser_fresh",
    "is_idle",
    "print_phase3_state",
    "print_phase4_state",
    "reset_proactive",
    "sync_proactive",
    "update_proactive",
]
