"""nana.core.context — stable context facade for the new runtime.

This keeps new modules off the old monolithic ``main.py`` while still
exposing the broker context shape used by social/action flows.
"""
from __future__ import annotations


class _RuntimeQueueFacade:
    def push(self, item):
        from nana.runtime.priority_queue import Priority, runtime_queue as _queue

        return _queue.enqueue(
            Priority.P2_BACKGROUND,
            name=str((item or {}).get("type") or "runtime.event"),
            source="nana.core.context",
            payload=dict(item or {}),
        )

    def snapshot(self):
        from nana.runtime.priority_queue import runtime_queue as _queue

        return _queue.snapshot()


def broker_context_snapshot(force_edge=False):
    from nana.config import BROWSER_FRESH_SECONDS
    from nana.runtime.browser_state import browser_age_seconds, browser_snapshot_state
    from nana.runtime.context import context_lock, context_state

    with context_lock:
        browser = dict(context_state.get("browser", {}))
        active_app = context_state.get("active_app")
        active_title = context_state.get("active_title")

    if force_edge:
        active_app = "msedge"
        active_title = browser.get("title") or active_title

    age = browser_age_seconds(browser)
    browser_available = bool(browser.get("available"))
    snapshot_state = browser_snapshot_state(browser)
    browser_fresh = browser_available and age is not None and age <= BROWSER_FRESH_SECONDS
    active_app_lower = (active_app or "").lower()
    active_app_is_edge = active_app_lower in {"edge", "msedge"} or "msedge" in active_app_lower

    return {
        "active_app": active_app,
        "active_title": active_title,
        "active_app_is_edge": active_app_is_edge,
        "browser_available": browser_available,
        "browser_fresh": browser_fresh,
        "browser_snapshot_state": snapshot_state,
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
        "active_window_valid": browser_available and snapshot_state in {"FRESH", "WARM"} and active_app_is_edge,
        "debug_force_edge": bool(force_edge),
    }


def mark_residue(tag, level=5):
    from nana.runtime.persona import mark_residue as _mark_residue

    return _mark_residue(tag, level=level)


runtime_queue = _RuntimeQueueFacade()
