"""nana.runtime.browser_state — browser state query helpers."""

import time

from nana.config import BROWSER_FRESH_SECONDS


def browser_age_seconds(browser):
    last_update = browser.get("last_update") or 0
    if not last_update:
        return None
    return max(0.0, time.time() - last_update)


def browser_snapshot_state(browser):
    if not browser or not browser.get("available"):
        return "INVALID"
    age = browser_age_seconds(browser)
    if age is None:
        return "INVALID"
    if age <= BROWSER_FRESH_SECONDS:
        return "FRESH"
    if age <= max(BROWSER_FRESH_SECONDS * 4, 30):
        return "WARM"
    if age <= max(BROWSER_FRESH_SECONDS * 12, 120):
        return "STALE"
    return "EXPIRED"


def snapshot_state_usable_for_context(state):
    return state in {"FRESH", "WARM", "STALE"}


def snapshot_state_usable_for_social_draft(state):
    return state in {"FRESH", "WARM", "STALE"}


def current_browser_snapshot_state():
    from nana.runtime.context import context_lock, context_state

    with context_lock:
        browser = dict(context_state.get("browser", {}))
    return browser_snapshot_state(browser)


def is_browser_context_question(text_lower):
    from nana.runtime.live_awareness import is_live_awareness_question

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
