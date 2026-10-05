"""nana.runtime.browser_state — browser state query helpers."""

import math
import time

from nana.config import BROWSER_FRESH_SECONDS
from nana.runtime.context_contracts import Freshness


_FRESH_SECONDS = 45.0
_WARM_SECONDS = 180.0
_STALE_SECONDS = 540.0
_FUTURE_SKEW_SECONDS = 2.0


def normalized_browser_observed_at(observed_at, captured_at):
    """Return an honest finite observation clock, tolerating tiny clock skew."""

    if type(observed_at) not in (int, float) or type(captured_at) not in (int, float):
        return None
    observed = float(observed_at)
    captured = float(captured_at)
    if (
        not math.isfinite(observed)
        or not math.isfinite(captured)
        or observed <= 0.0
        or captured < 0.0
        or observed > captured + _FUTURE_SKEW_SECONDS
    ):
        return None
    return min(observed, captured)


def classify_browser_freshness(observed_at, captured_at):
    """Classify browser age once at the turn capture clock."""

    observed = normalized_browser_observed_at(observed_at, captured_at)
    if observed is None:
        return Freshness.UNKNOWN
    age = max(0.0, float(captured_at) - observed)
    if age <= _FRESH_SECONDS:
        return Freshness.FRESH
    if age <= _WARM_SECONDS:
        return Freshness.WARM
    if age <= _STALE_SECONDS:
        return Freshness.STALE
    return Freshness.EXPIRED


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
