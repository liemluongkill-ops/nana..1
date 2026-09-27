"""Web context scraper for the Autonomy Loop (Phase A7+).

Pulls live web/browser context from `nana.runtime.context.context_snapshot`
and turns it into a compact dict the LLM banter generator can consume.

We do NOT do OCR and we do NOT need a browser extension. The browser
block in the runtime snapshot is already populated by the existing
browser bridge (see `nana.runtime.context.update_browser_state`):

    context_state["browser"] = {
        "available": bool,
        "browser": str | None,           # e.g. "msedge"
        "url": str | None,               # https://...
        "title": str | None,             # page title
        "kind": str,                     # youtube|social|video|music|...
        "page_heading": str | None,      # main H1/H2
        "meta_description": str | None,  # <meta name=description>
        "selected_text": str | None,
        "social_post_text": str | None,
        "social_vibe": str | None,
        "site_signals": dict,
        "age_seconds": float | None,
        "fresh": bool,                   # True if age <= BROWSER_FRESH_SECONDS
    }

The scraper is intentionally read-only. It never touches the DOM, never
runs JS, never opens a new tab. It just normalizes the snapshot into a
prompt-friendly dict.

Locked design rules:

- Phase A7 (Web Awareness) is opt-in: gate is
  NANA_AUTONOMY_WEB_CONTEXT_ENABLED (default ON).
- Web context is only "fresh" if the runtime says so (fresh=True).
  Stale snapshots are still reported but flagged stale=True so the LLM
  prompt can say "do not assume this is still on screen".
- Privacy: the URL is included only if it is not flagged private by
  the runtime (the runtime already sanitizes social/private URLs).
- Cost: scraping is in-memory only. We never fetch the URL ourselves.
"""

from __future__ import annotations

import os
import threading
from typing import Optional

from nana.runtime.context import context_snapshot, BROWSER_FRESH_SECONDS
from nana.config import BROWSER_FRESH_SECONDS  # noqa: F401  (re-export)


# Phase A7: env gate (default ON, opt-out by setting 0).
_DEFAULT_ENABLED = True


def _env_enabled() -> bool:
    raw = os.environ.get("NANA_AUTONOMY_WEB_CONTEXT_ENABLED")
    if raw is None:
        return _DEFAULT_ENABLED
    return raw.strip().lower() not in ("0", "false", "no", "off")


# Module-level cache. We do not want the autonomy loop to re-read the
# full context snapshot every tick (the loop is on a 5-15s cadence and
# browser is on its own poll rate). We cache the last result and only
# re-read if `min_age_s` has elapsed.
class WebContextScraper:
    """Caches the most recent browser snapshot for the autonomy loop.

    Thread-safe: read/write under a single lock. The autonomy loop calls
    `get(min_age_s=...)` once per tick. The runtime updates the snapshot
    in the background, so this scraper just re-packages what is there.
    """

    def __init__(self, clock=None):
        import time as _time
        self._lock = threading.Lock()
        self._last_t: Optional[float] = None
        self._last_payload: dict = {}
        self._clock = clock or _time.monotonic

    def get(self, min_age_s: float = 0.0) -> dict:
        """Return a compact web-context dict.

        Returns a stable shape (always has the same keys) so callers
        never have to guard against missing keys.

        Empty / no-browser payload still has all keys set to None or
        sensible defaults.
        """
        with self._lock:
            now = self._clock()
            if self._last_t is not None and (now - self._last_t) < min_age_s:
                return dict(self._last_payload)

        snap = context_snapshot()
        browser = snap.get("browser") or {}
        zone = snap.get("active_zone") or "unknown"
        app = snap.get("active_app") or ""

        available = bool(browser.get("available"))
        fresh = bool(browser.get("fresh"))
        age = browser.get("age_seconds")
        in_browser_zone = zone == "chill" and app.lower() in (
            "msedge", "edge", "chrome", "firefox", "brave",
        )

        # Effective: available AND in a recognized browser AND fresh.
        effective = available and in_browser_zone and fresh

        # Build a prompt-friendly summary. We do NOT pass raw URL
        # strings longer than 200 chars to keep prompt cost down.
        url = browser.get("url")
        url_short = (url[:200] + "...") if url and len(url) > 200 else url

        title = browser.get("title")
        page_heading = browser.get("page_heading")
        meta_desc = browser.get("meta_description")
        selected = browser.get("selected_text")
        social_post = browser.get("social_post_text")
        social_vibe = browser.get("social_vibe")
        kind = browser.get("kind") or "unknown"
        site_signals = browser.get("site_signals") or {}

        payload = {
            "available": available,
            "in_browser_zone": in_browser_zone,
            "fresh": fresh,
            "stale": available and not fresh,
            "effective": effective,
            "age_seconds": age,
            "browser": browser.get("browser"),
            "url": url_short,
            "title": title,
            "page_heading": page_heading,
            "meta_description": meta_desc,
            "selected_text": (selected[:300] + "...") if selected and len(selected) > 300 else selected,
            "kind": kind,
            "social_post_text": (social_post[:300] + "...") if social_post and len(social_post) > 300 else social_post,
            "social_vibe": social_vibe,
            "site_signals": site_signals,
            # Hint for the LLM prompt.
            "summary_hint": _summary_hint(
                effective=effective,
                in_browser_zone=in_browser_zone,
                available=available,
                fresh=fresh,
                kind=kind,
                title=title,
                url=url_short,
                age=age,
            ),
        }

        with self._lock:
            self._last_t = now
            self._last_payload = payload
        return dict(payload)

    def is_enabled(self) -> bool:
        return _env_enabled()

    def invalidate(self) -> None:
        with self._lock:
            self._last_t = None
            self._last_payload = {}


def _summary_hint(effective: bool, in_browser_zone: bool, available: bool,
                  fresh: bool, kind: str, title, url, age) -> str:
    if not available:
        return "No browser content available right now."
    if not in_browser_zone:
        return "Browser content exists but Ba is not in the browser window."
    if available and in_browser_zone and not fresh:
        age_s = f"{age:.0f}s" if isinstance(age, (int, float)) else "?"
        return f"Browser content is stale ({age_s} old). Do not assume it is still on screen."
    if effective:
        bits = []
        if kind and kind != "unknown":
            bits.append(f"kind={kind}")
        if title:
            bits.append(f"title='{title}'")
        if url:
            bits.append(f"url={url}")
        return "Active web context: " + " | ".join(bits) if bits else "Active web context."
    return "Browser present but no usable content."


# Module-level singleton (mirrors the observer pattern).
_scraper: Optional[WebContextScraper] = None


def init_scraper(clock=None) -> WebContextScraper:
    global _scraper
    _scraper = WebContextScraper(clock=clock)
    return _scraper


def get_web_context(min_age_s: float = 0.0) -> dict:
    """Public API. Returns a stable dict shape, never raises."""
    global _scraper
    if _scraper is None:
        _scraper = WebContextScraper()
    try:
        return _scraper.get(min_age_s=min_age_s)
    except Exception:
        # Never let a web-context bug kill the autonomy loop.
        return {
            "available": False,
            "in_browser_zone": False,
            "fresh": False,
            "stale": False,
            "effective": False,
            "age_seconds": None,
            "browser": None,
            "url": None,
            "title": None,
            "page_heading": None,
            "meta_description": None,
            "selected_text": None,
            "kind": "unknown",
            "social_post_text": None,
            "social_vibe": None,
            "site_signals": {},
            "summary_hint": "Web context unavailable (scraper error).",
        }


__all__ = ["WebContextScraper", "init_scraper", "get_web_context"]
