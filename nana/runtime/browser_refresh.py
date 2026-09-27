"""nana.runtime.browser_refresh — browser snapshot refresh logic."""

import asyncio
import time

from nana.browser.context import BrowserContextReader, BrowserSnapshot
from nana.browser.vision import VisionPreviewer
from nana.brain.local_helper import apply_local_summary
from nana.runtime.context import sync_proactive_browser_seen, update_browser_state
from nana.runtime.metrics import (
    RUNTIME_LATENCY,
    BROWSER_REFRESH_COOLDOWN_SECONDS,
    BROWSER_REFRESH_INFLIGHT,
    record_browser_refresh_latency,
    record_browser_cache_hit,
    record_browser_coalesced,
    record_browser_cooldown_skip,
    browser_refresh_last_age_seconds,
)
from nana.runtime.recovery import recovery_clear_browser
from nana.runtime.browser_state import (
    browser_age_seconds,
    browser_snapshot_state,
    current_browser_snapshot_state,
    snapshot_state_usable_for_context,
)


browser_reader = BrowserContextReader()
vision_previewer = VisionPreviewer()


def browser_snapshot_from_context(browser):
    browser = browser or {}
    return BrowserSnapshot(
        available=bool(browser.get("available")),
        browser=browser.get("browser"),
        url=browser.get("url"),
        title=browser.get("title"),
        kind=browser.get("kind") or "unknown",
        reason=browser.get("reason") or "not_checked",
        page_heading=browser.get("page_heading"),
        meta_description=browser.get("meta_description"),
        selected_text=browser.get("selected_text"),
        dom_debug=browser.get("dom_debug"),
        site_signals=browser.get("site_signals") or {},
        social_post_text=browser.get("social_post_text"),
        social_comments=browser.get("social_comments") or [],
        social_vibe=browser.get("social_vibe"),
        local_summary=browser.get("local_summary"),
        local_helper_debug=browser.get("local_helper_debug"),
    )


async def _refresh_browser_state_impl(loop, show=True, reason="manual", deep=False):
    total_start = time.perf_counter()
    read_start = time.perf_counter()
    snapshot = await loop.run_in_executor(None, browser_reader.read)
    read_ms = (time.perf_counter() - read_start) * 1000.0
    summary_ms = None
    if deep:
        summary_start = time.perf_counter()
        snapshot = await loop.run_in_executor(None, apply_local_summary, snapshot)
        summary_ms = (time.perf_counter() - summary_start) * 1000.0
    update_browser_state(snapshot)
    sync_proactive_browser_seen(snapshot)
    if snapshot.available:
        recovery_clear_browser(reason="browser_context_refreshed")
    total_ms = (time.perf_counter() - total_start) * 1000.0
    record_browser_refresh_latency(read_ms, summary_ms, total_ms, reason=reason, deep=deep)
    if show:
        print_browser_state()
    return snapshot


async def refresh_browser_state(loop, show=True, reason="manual", deep=False, force=False):
    global BROWSER_REFRESH_INFLIGHT
    if not deep and BROWSER_REFRESH_INFLIGHT and not BROWSER_REFRESH_INFLIGHT.done():
        record_browser_coalesced(reason)
        snapshot = await BROWSER_REFRESH_INFLIGHT
        if show:
            print_browser_state()
        return snapshot
    if not deep and not force:
        last_age = browser_refresh_last_age_seconds()
        if last_age is not None and last_age < BROWSER_REFRESH_COOLDOWN_SECONDS:
            state = current_browser_snapshot_state()
            record_browser_cooldown_skip(reason, state, last_age)
            if show:
                print_browser_state()
            return None

    task = asyncio.create_task(_refresh_browser_state_impl(loop, show=show, reason=reason, deep=deep))
    if not deep:
        BROWSER_REFRESH_INFLIGHT = task
    try:
        return await task
    finally:
        if not deep and BROWSER_REFRESH_INFLIGHT is task:
            BROWSER_REFRESH_INFLIGHT = None


async def ensure_browser_snapshot(loop, min_state="context", reason="auto_context", show=False):
    from nana.runtime.browser_state import snapshot_state_usable_for_social_draft

    state = current_browser_snapshot_state()
    if min_state == "context" and snapshot_state_usable_for_context(state):
        record_browser_cache_hit(reason, state)
        return None
    if min_state == "social_draft" and snapshot_state_usable_for_social_draft(state):
        record_browser_cache_hit(reason, state)
        return None
    return await refresh_browser_state(loop, show=show, reason=reason, deep=False, force=False)


def print_browser_state():
    from nana.config import BROWSER_FRESH_SECONDS
    from nana.runtime.context import context_lock, context_state

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
