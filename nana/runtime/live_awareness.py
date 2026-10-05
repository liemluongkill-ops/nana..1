"""Read-only live awareness snapshot for Nana (Task 7C Fix V2).

This module does not open pages, click, type, OCR, or run game input. It only
normalizes the passive runtime/browser state that already exists so Nana core
and autonomy can consume one stable "where is Ba / what is Ba looking at"
packet.

V2 fixes:
- Sticky focus: only update if the change is significant (URL, title, or
  content changed beyond minor variations like player state).
- Prioritize active/focused tab content over background tabs.
- Add metadata for drift tracking (is_confirmed_active, source_tab_id).
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import unicodedata
from typing import Any

from nana.runtime.context import context_snapshot
from nana.runtime.context_contracts import SourceSnapshot


# ─── Browser apps ────────────────────────────────────────────────────────────────

BROWSER_APPS = {"msedge", "edge", "chrome", "firefox", "brave"}

# ─── Sticky focus state (thread-safe, per-process) ─────────────────────────────

_sticky_lock = threading.Lock()
_sticky_state: dict[str, Any] = {
    "locked": False,       # True = focus is locked
    "locked_at": 0.0,     # monotonic timestamp when locked
    "locked_title": "",     # title that was locked
    "locked_url": "",      # URL that was locked
    "locked_focus_text": "",  # focus_text that was locked
    "locked_focus_source": "",  # source-owned precedence class at lock time
    "lock_reason": "",     # why it was locked ("awareness_record" / "user_question")
    "last_snap": {},       # last raw browser snapshot for comparison
    "unlock_at": 0.0,     # monotonic timestamp when auto-unlock happens
    "auto_unlock_seconds": 30.0,  # auto-unlock after 30s of no user activity
}


def _is_significant_change(new_snap: dict[str, Any]) -> bool:
    """Return True if the new snapshot differs SIGNIFICANTLY from the locked state.

    Minor variations (same video, player state change, seek) are NOT significant.
    Tab switches, URL changes, title changes ARE significant.
    """
    with _sticky_lock:
        locked = _sticky_state.get("locked", False)
        locked_title = _sticky_state.get("locked_title", "")
        locked_url = _sticky_state.get("locked_url", "")
        locked_focus = _sticky_state.get("locked_focus_text", "")
        locked_reason = _sticky_state.get("lock_reason", "")

    # FIX V3: awareness snapshots use browser_title/browser_url, not title/url
    new_title = str(new_snap.get("browser_title") or new_snap.get("title") or "")
    new_url = str(new_snap.get("browser_url") or new_snap.get("url") or "")
    new_focus = str(new_snap.get("focus_text") or "")

    # If not locked, anything is a significant change
    if not locked:
        return True

    # If the URL changed entirely (different page), it's significant
    # Handle YouTube /video/ vs /watch/ same-video variations
    if new_url and locked_url:
        # Normalize URLs for comparison
        norm_new = _normalize_url(new_url)
        norm_locked = _normalize_url(locked_url)
        if norm_new != norm_locked:
            return True

    # If title changed entirely (not just player state), it's significant
    if new_title and locked_title:
        norm_new_title = _normalize_title(new_title)
        norm_locked_title = _normalize_title(locked_title)
        if norm_new_title != norm_locked_title:
            return True

    # If focus content changed significantly (different text), it's significant
    if new_focus and locked_focus:
        # Only consider it significant if the new focus is substantively different
        if not _is_minor_variation(locked_focus, new_focus):
            return True

    # If locked_url was set but new_url is empty (tab closed?), it's significant
    if locked_url and not new_url:
        return True

    return False


def _normalize_url(url: str) -> str:
    """Normalize URL for comparison, stripping player state noise."""
    if not url:
        return ""
    url = str(url).strip()
    # Remove YouTube player state parameters
    url = re.sub(r"[?&]t=\d+", "", url)  # time seek
    url = re.sub(r"[?&]index=\d+", "", url)  # playlist index
    url = re.sub(r"[?&]list=[^&]+", "", url)  # playlist ID
    url = re.sub(r"#\d+", "", url)  # timestamps
    return url


def _normalize_title(title: str) -> str:
    """Normalize title for comparison, stripping player state noise."""
    if not title:
        return ""
    title = str(title).strip()
    # Remove YouTube player suffixes that change on every seek/pause
    title = re.sub(r"\s*[-|]\s*(?:YouTube|YouTube Music).*$", "", title, flags=re.IGNORECASE)
    # Remove time indicators
    title = re.sub(r"\s*\(\d+:\d+\).*$", "", title)
    # Remove stream/pause/play indicators
    title = re.sub(r"\s*[\[\(]?(?:▶|⏸|⏯|LIVE|Stream)[\]\)]?\s*", "", title)
    # Normalize whitespace
    title = " ".join(title.split())
    return title


def _is_minor_variation(old: str, new: str) -> bool:
    """Return True if new is a minor variation of old (same content, minor edits)."""
    if not old or not new:
        return False
    old = str(old).strip()
    new = str(new).strip()
    if old == new:
        return True
    # If one is a prefix/suffix of the other with >80% overlap, consider minor
    shorter = old if len(old) <= len(new) else new
    longer = new if len(old) <= len(new) else old
    if len(shorter) > 10 and longer.startswith(shorter):
        return True
    # Levenshtein-like: if they're very similar, treat as minor
    # Simple check: if the shorter string is contained in the longer
    # and the difference is < 20% of the longer length
    if len(shorter) > 20 and shorter in longer and (len(longer) - len(shorter)) / len(longer) < 0.2:
        return True
    return False


def lock_focus(snap: dict[str, Any], reason: str = "user_question") -> None:
    """Lock the current browser focus so minor variations don't cause drift.

    Args:
        snap: Awareness snapshot dict from build_live_awareness_snapshot().
              Must contain browser_title and browser_url (NOT title/url).
        reason: Why the lock was set.
    """
    with _sticky_lock:
        _sticky_state["locked"] = True
        _sticky_state["locked_at"] = time.monotonic()
        # FIX V3: awareness snapshots use browser_title/browser_url, not title/url
        _sticky_state["locked_title"] = str(snap.get("browser_title") or snap.get("title") or "")
        _sticky_state["locked_url"] = str(snap.get("browser_url") or snap.get("url") or "")
        _sticky_state["locked_focus_text"] = str(snap.get("focus_text") or "")
        _sticky_state["locked_focus_source"] = str(snap.get("focus_source") or "")
        _sticky_state["lock_reason"] = reason
        # Auto-unlock after 30s of no user activity
        _sticky_state["unlock_at"] = time.monotonic() + _sticky_state.get("auto_unlock_seconds", 30.0)


def unlock_focus_if_stale() -> bool:
    """Auto-unlock if the lock has expired (no activity). Returns True if unlocked."""
    with _sticky_lock:
        unlock_at = _sticky_state.get("unlock_at", 0.0)
        if unlock_at > 0 and time.monotonic() >= unlock_at:
            _sticky_state["locked"] = False
            _sticky_state["locked_title"] = ""
            _sticky_state["locked_url"] = ""
            _sticky_state["locked_focus_text"] = ""
            _sticky_state["locked_focus_source"] = ""
            _sticky_state["lock_reason"] = ""
            _sticky_state["unlock_at"] = 0.0
            return True
    return False


def reset_sticky_focus() -> None:
    """Reset all sticky focus state (call on session start)."""
    with _sticky_lock:
        _sticky_state["locked"] = False
        _sticky_state["locked_at"] = 0.0
        _sticky_state["locked_title"] = ""
        _sticky_state["locked_url"] = ""
        _sticky_state["locked_focus_text"] = ""
        _sticky_state["locked_focus_source"] = ""
        _sticky_state["lock_reason"] = ""
        _sticky_state["unlock_at"] = 0.0
        _sticky_state["last_snap"] = {}


def get_sticky_focus_state() -> dict[str, Any]:
    """Return current sticky focus state for debugging/status."""
    with _sticky_lock:
        return dict(_sticky_state)


# ─── FOCUS fields (priority order) ────────────────────────────────────────────

FOCUS_FIELDS = (
    ("selected_text", "selected_text"),
    ("social_post_text", "social_post"),
    ("local_summary", "local_summary"),
    ("page_heading", "page_heading"),
    ("title", "title"),
)

SOURCE_LABELS = {
    "selected_text": "selected_text",
    "social_post": "social_post",
    "local_summary": "local_summary",
    "page_heading": "docs_or_page_heading",
    "title": "page_title",
    "none": "none",
}

LIVE_AWARENESS_MARKERS = (
    "trang này",
    "tab này",
    "website này",
    "web này",
    "link này",
    "url này",
    "đang mở trang gì",
    "đang mở gì",
    "mở trang gì",
    "trang gì",
    "web gì",
    "tab gì",
    "link gì",
    "đường link nào",
    "đang xem",
    "xem cái gì",
    "xem gì",
    "đang coi",
    "coi gì",
    "video gì",
    "clip gì",
    "youtube gì",
    "đang đọc",
    "đọc gì",
    "nhìn giống gì",
    "đang ở đâu",
    "ở trang nào",
    "đoạn này",
    "text này",
    "dòng này",
    "phần này",
    "chữ này",
    "bôi đen",
    "selected",
    "browser",
    "trình duyệt",
)

AWARENESS_DENIAL_MARKERS = (
    "không có quyền xem",
    "khong co quyen xem",
    "không được cấp quyền",
    "khong duoc cap quyen",
    "đâu có được cấp quyền",
    "dau co duoc cap quyen",
    "không thể xem",
    "khong the xem",
    "không thấy trình duyệt",
    "khong thay trinh duyet",
    "không thấy browser",
    "khong thay browser",
    "không thấy trang",
    "khong thay trang",
    "không ngó",
    "khong ngo",
    "đâu có được ngó",
    "dau co duoc ngo",
    "không truy cập được",
    "khong truy cap duoc",
    "chưa được ngó",
    "chua duoc ngo",
    "chỉ sống trong code",
    "chi song trong code",
)


def build_live_awareness_snapshot(source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a stable read-only awareness packet.

    The packet favors what Ba is actively pointing at:
    selected text > social post > local summary > heading > title.

    V2 Sticky Focus:
    - If focus is currently locked (after a question or awareness record),
      return the LOCKED focus data, not fresh data from a different tab/video.
    - This prevents browser "eye drift" where the snapshot wanders to a
      background tab while the user is still on the locked content.
    - Lock auto-expires after 30s of no user activity.
    """

    snap = dict(source or context_snapshot())
    browser = dict(snap.get("browser") or {})
    active_app = (snap.get("active_app") or "").lower()
    active_zone = snap.get("active_zone") or "unknown"

    browser_available = bool(browser.get("available"))
    browser_fresh = bool(browser.get("fresh"))
    browser_age = browser.get("age_seconds")
    in_browser_app = active_app in BROWSER_APPS
    in_browser_zone = active_zone == "chill" and in_browser_app
    browser_effective = browser_available and browser_fresh and in_browser_app

    # V2: Check if sticky focus is active and should override fresh data
    is_confirmed_active = True
    drift_warning = ""
    with _sticky_lock:
        is_locked = _sticky_state.get("locked", False)
        lock_reason = _sticky_state.get("lock_reason", "")
        locked_url = _sticky_state.get("locked_url", "")
        locked_title = _sticky_state.get("locked_title", "")
        locked_focus_text = _sticky_state.get("locked_focus_text", "")

    # Auto-unlock stale locks
    unlock_fresh = unlock_focus_if_stale()
    if unlock_fresh:
        is_locked = False

    # V2: If locked, prefer the locked data over potentially drifting fresh data
    if is_locked and (locked_url or locked_title or locked_focus_text):
        # Use locked URL if fresh URL is different and it's a significant change
        fresh_url = str(browser.get("url") or "")
        fresh_title = str(browser.get("title") or "")

        if fresh_url and locked_url:
            norm_fresh = _normalize_url(fresh_url)
            norm_locked = _normalize_url(locked_url)
            if norm_fresh != norm_locked:
                # Fresh data is from a different page — use locked data
                is_confirmed_active = False
                drift_warning = f"focus_locked ({lock_reason}) — tab drifted from '{locked_title}'"
            else:
                # Same URL, same content — use fresh data (for player state updates)
                is_confirmed_active = True
                drift_warning = ""
        elif fresh_title and locked_title:
            norm_fresh = _normalize_title(fresh_title)
            norm_locked = _normalize_title(locked_title)
            if norm_fresh != norm_locked:
                is_confirmed_active = False
                drift_warning = f"focus_locked ({lock_reason}) — title drifted"
            else:
                is_confirmed_active = True
                drift_warning = ""
        else:
            # No fresh URL/title but we have locked data — use locked
            is_confirmed_active = True
            drift_warning = f"focus_locked ({lock_reason})"

        if not is_confirmed_active:
            # Override browser data with locked data
            browser = dict(browser)
            if locked_url:
                browser["url"] = locked_url
            if locked_title:
                browser["title"] = locked_title
            # Keep fresh focus_text if it's meaningfully different from locked
            fresh_focus = str(browser.get("focus_text") or "")
            if fresh_focus and not _is_minor_variation(locked_focus_text, fresh_focus):
                # Don't override focus_text — it's likely a selection
                pass
            elif locked_focus_text:
                browser["focus_text"] = locked_focus_text

    focus_source = "none"
    focus_text = ""
    for field, label in FOCUS_FIELDS:
        value = _clean(browser.get(field))
        if value:
            focus_source = label
            focus_text = value
            break

    confidence = _focus_confidence(
        focus_source=focus_source,
        browser_available=browser_available,
        browser_fresh=browser_fresh,
        in_browser_app=in_browser_app,
    )

    stale_reason = "ok"
    if not browser_available:
        stale_reason = browser.get("reason") or "browser_unavailable"
    elif not browser_fresh:
        stale_reason = "browser_snapshot_stale"
    elif not in_browser_app:
        stale_reason = "active_window_not_browser"

    is_confirmed_active = bool(is_confirmed_active and browser_effective)
    return {
        "active_app": snap.get("active_app"),
        "active_title": snap.get("active_title"),
        "active_zone": active_zone,
        "idle_state": snap.get("idle_state"),
        "idle_seconds": snap.get("idle_seconds"),
        "in_flow": bool(snap.get("in_flow")),
        "time": snap.get("time") or {},
        "browser_available": browser_available,
        "browser_fresh": browser_fresh,
        "browser_effective": browser_effective,
        "browser_age_seconds": browser_age,
        "browser_stale_reason": stale_reason,
        "browser": browser.get("browser"),
        "browser_kind": browser.get("kind") or "unknown",
        "browser_title": _clean(browser.get("title")),
        "browser_url": _short(browser.get("url"), 220),
        "browser_heading": _clean(browser.get("page_heading")),
        "browser_meta_description": _clean(browser.get("meta_description")),
        "browser_selected_text": _clean(browser.get("selected_text")),
        "browser_social_post_text": _clean(browser.get("social_post_text")),
        "browser_social_vibe": _clean(browser.get("social_vibe")),
        "browser_local_summary": _clean(browser.get("local_summary")),
        "browser_site_signals": browser.get("site_signals") or {},
        "focus_source": focus_source,
        "focus_text": _short(focus_text, 360),
        "focus_confidence": confidence,
        # V2 drift-tracking metadata
        "is_confirmed_active": is_confirmed_active,
        "drift_warning": drift_warning,
        "is_sticky_locked": bool(is_locked),
        "lock_reason": lock_reason if is_locked else "",
        "router": build_awareness_router_packet(
            focus_source=focus_source,
            focus_text=focus_text,
            confidence=confidence,
            browser_available=browser_available,
            browser_fresh=browser_fresh,
            browser_effective=browser_effective,
            stale_reason=stale_reason,
            browser_kind=browser.get("kind") or "unknown",
        ),
        "awareness_mode": "read_only",
        "can_act": False,
    }


def _awareness_content_revision(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256-" + hashlib.sha256(encoded).hexdigest()


def capture_live_awareness_source(
    source: dict[str, Any],
    *,
    browser_snapshot: SourceSnapshot,
    captured_at: float,
    captured_monotonic_at: float,
) -> SourceSnapshot:
    """Freeze a side-effect-free awareness view under the sticky-focus lock."""

    snap = dict(source)
    browser = dict(snap.get("browser") or {})
    active_app = str(snap.get("active_app") or "").lower()
    active_zone = snap.get("active_zone") or "unknown"
    browser_available = bool(browser.get("available"))
    browser_fresh = bool(browser.get("fresh"))
    in_browser_app = active_app in BROWSER_APPS
    browser_effective = browser_available and browser_fresh and in_browser_app

    with _sticky_lock:
        sticky = dict(_sticky_state)
        unlock_at = sticky.get("unlock_at", 0.0)
        is_locked = bool(sticky.get("locked")) and not (
            type(unlock_at) in (int, float)
            and unlock_at > 0.0
            and captured_monotonic_at >= float(unlock_at)
        )
        lock_reason = str(sticky.get("lock_reason") or "") if is_locked else ""
        locked_url = str(sticky.get("locked_url") or "")
        locked_title = str(sticky.get("locked_title") or "")
        locked_focus_text = str(sticky.get("locked_focus_text") or "")
        locked_focus_source = str(sticky.get("locked_focus_source") or "")
        is_confirmed_active = True
        drift_warning = ""
        prefer_locked_focus = False

        if is_locked and (locked_url or locked_title or locked_focus_text):
            fresh_url = str(browser.get("url") or "")
            fresh_title = str(browser.get("title") or "")
            if fresh_url and locked_url:
                is_confirmed_active = _normalize_url(fresh_url) == _normalize_url(locked_url)
                if not is_confirmed_active:
                    drift_warning = f"focus_locked ({lock_reason})"
            elif fresh_title and locked_title:
                is_confirmed_active = _normalize_title(fresh_title) == _normalize_title(locked_title)
                if not is_confirmed_active:
                    drift_warning = f"focus_locked ({lock_reason})"
            else:
                drift_warning = f"focus_locked ({lock_reason})"
                prefer_locked_focus = True

            if not is_confirmed_active:
                prefer_locked_focus = True
                browser = dict(browser)
                if locked_url:
                    browser["url"] = locked_url
                if locked_title:
                    browser["title"] = locked_title

        focus_source = "none"
        focus_text = ""
        for field, label in FOCUS_FIELDS:
            value = _clean(browser.get(field))
            if value:
                focus_source = label
                focus_text = value
                break
        if prefer_locked_focus and locked_focus_text:
            focus_source = (
                locked_focus_source
                if locked_focus_source in SOURCE_LABELS
                else focus_source
            )
            focus_text = locked_focus_text

        confidence = _focus_confidence(
            focus_source=focus_source,
            browser_available=browser_available,
            browser_fresh=browser_fresh,
            in_browser_app=in_browser_app,
        )
        stale_reason = "ok"
        if not browser_available:
            stale_reason = browser.get("reason") or "browser_unavailable"
        elif not browser_fresh:
            stale_reason = "browser_snapshot_stale"
        elif not in_browser_app:
            stale_reason = "active_window_not_browser"

        is_confirmed_active = bool(is_confirmed_active and browser_effective)
        payload = {
            "active_app": snap.get("active_app"),
            "active_title": snap.get("active_title"),
            "active_zone": active_zone,
            "idle_state": snap.get("idle_state"),
            "idle_seconds": snap.get("idle_seconds"),
            "in_flow": bool(snap.get("in_flow")),
            "time": snap.get("time") or {},
            "browser_available": browser_available,
            "browser_fresh": browser_fresh,
            "browser_effective": browser_effective,
            "browser_age_seconds": browser.get("age_seconds"),
            "browser_stale_reason": stale_reason,
            "browser": browser.get("browser"),
            "browser_kind": browser.get("kind") or "unknown",
            "browser_title": _clean(browser.get("title")),
            "browser_url": _short(browser.get("url"), 220),
            "browser_heading": _clean(browser.get("page_heading")),
            "browser_meta_description": _clean(browser.get("meta_description")),
            "browser_selected_text": _clean(browser.get("selected_text")),
            "browser_social_post_text": _clean(browser.get("social_post_text")),
            "browser_social_vibe": _clean(browser.get("social_vibe")),
            "browser_local_summary": _clean(browser.get("local_summary")),
            "browser_site_signals": browser.get("site_signals") or {},
            "focus_source": focus_source,
            "focus_text": _short(focus_text, 360),
            "focus_confidence": confidence,
            "is_confirmed_active": is_confirmed_active,
            "drift_warning": drift_warning,
            "is_sticky_locked": is_locked,
            "lock_reason": lock_reason,
            "router": build_awareness_router_packet(
                focus_source=focus_source,
                focus_text=focus_text,
                confidence=confidence,
                browser_available=browser_available,
                browser_fresh=browser_fresh,
                browser_effective=browser_effective,
                stale_reason=stale_reason,
                browser_kind=browser.get("kind") or "unknown",
            ),
            "awareness_mode": "read_only",
            "can_act": False,
        }
        semantic = {
            key: payload.get(key)
            for key in (
                "active_app",
                "active_title",
                "active_zone",
                "idle_state",
                "in_flow",
                "browser_available",
                "browser",
                "browser_kind",
                "browser_title",
                "browser_url",
                "browser_heading",
                "browser_meta_description",
                "browser_selected_text",
                "browser_social_post_text",
                "browser_social_vibe",
                "browser_local_summary",
                "focus_source",
                "focus_text",
                "is_sticky_locked",
            )
        }
        result = SourceSnapshot(
            source="live_awareness",
            revision=_awareness_content_revision(semantic),
            observed_at=browser_snapshot.observed_at,
            captured_at=captured_at,
            freshness=browser_snapshot.freshness,
            payload=payload,
        )
    return result


def build_awareness_router_packet(
    *,
    focus_source: str,
    focus_text: str,
    confidence: float,
    browser_available: bool,
    browser_fresh: bool,
    browser_effective: bool,
    stale_reason: str,
    browser_kind: str,
) -> dict[str, Any]:
    """Return the routing decision for current read-only awareness.

    This is not an action router. It only tells Nana which passive context
    source should answer the user's current-context question.
    """

    can_answer = bool(browser_available and focus_source != "none" and confidence >= 0.45)
    if not browser_available:
        reason = "browser_unavailable"
    elif focus_source == "none":
        reason = "no_focus_text"
    elif not browser_fresh:
        reason = "stale_context_answer_as_snapshot"
    elif not browser_effective:
        reason = stale_reason or "browser_not_active_window"
    else:
        reason = "ok"

    return {
        "source": SOURCE_LABELS.get(focus_source, focus_source or "none"),
        "focus_source": focus_source or "none",
        "confidence": confidence,
        "stale_reason": stale_reason,
        "can_answer": can_answer,
        "can_act": False,
        "browser_kind": browser_kind or "unknown",
        "reason": reason,
        "preview": _short(focus_text, 160),
    }


def format_live_awareness_prompt(awareness: dict[str, Any] | None = None) -> str:
    """Format the awareness packet as a compact, high-priority prompt block."""

    data = awareness or build_live_awareness_snapshot()
    age = data.get("browser_age_seconds")
    age_text = "unknown" if age is None else f"{float(age):.1f}s"
    focus_text = data.get("focus_text") or "none"
    title = data.get("browser_title") or "none"
    heading = data.get("browser_heading") or "none"
    selected = data.get("browser_selected_text") or "none"
    local_summary = data.get("browser_local_summary") or "none"
    social_post = data.get("browser_social_post_text") or "none"
    vibe = data.get("browser_social_vibe") or "none"
    url = data.get("browser_url") or "none"
    # V2 drift metadata
    is_confirmed = data.get("is_confirmed_active", True)
    drift_warn = data.get("drift_warning") or ""
    is_locked = data.get("is_sticky_locked", False)

    # Add drift warning if present
    drift_note = ""
    if not is_confirmed and drift_warn:
        drift_note = f"\n  ⚠️  DRIFT WARNING: {drift_warn} — using last confirmed focus."
    elif is_locked:
        drift_note = f"\n  🔒 Focus is sticky-locked (reason={data.get('lock_reason', 'unknown')})"

    return (
        "LIVE AWARENESS SNAPSHOT - READ ONLY, OVERRIDES OLD CHAT MEMORY FOR CURRENT-CONTEXT QUESTIONS:\n"
        f"- Active: zone={data.get('active_zone')} | app={data.get('active_app')} | title={_short(data.get('active_title'), 140) or 'none'} | flow={data.get('in_flow')}{drift_note}\n"
        f"- Browser: available={data.get('browser_available')} | fresh={data.get('browser_fresh')} | effective={data.get('browser_effective')} | age={age_text} | kind={data.get('browser_kind')} | reason={data.get('browser_stale_reason')}\n"
        f"- Page title: {title}\n"
        f"- URL: {url}\n"
        f"- Heading: {heading}\n"
        f"- Selected text: {selected}\n"
        f"- Social post: {social_post}\n"
        f"- Social vibe: {vibe}\n"
        f"- Local summary: {local_summary}\n"
        f"- Current focus: source={data.get('focus_source')} | confidence={data.get('focus_confidence')} | text={focus_text}\n"
        "Awareness rules:\n"
        "- This is passive awareness only. Nana must not claim she clicked, typed, submitted, opened, or controlled anything.\n"
        "- If browser_available=True and the user asks what Ba is viewing/reading/selecting, answer from this snapshot.\n"
        "- If browser_fresh=True, do not say Nana cannot see the browser; say what the snapshot shows.\n"
        "- If Selected text is not none, treat it as the strongest focus and explain it before page title/URL.\n"
        "- If browser_fresh=False, phrase it as the latest browser snapshot, not guaranteed live.\n"
        "- If a drift_warning is present, use the LAST CONFIRMED focus, not the fresh data.\n"
        "- V2 sticky focus: once a focus is confirmed, it stays locked until user switches tabs/URL.\n"
    )


def format_awareness_status(awareness: dict[str, Any] | None = None) -> str:
    """Human-readable status for /awareness-status."""

    data = awareness or build_live_awareness_snapshot()
    router = data.get("router") or {}
    age = data.get("browser_age_seconds")
    age_text = "unknown" if age is None else f"{float(age):.1f}s"
    focus_preview = _short(data.get("focus_text"), 180) or "none"
    # V2 drift info
    is_locked = data.get("is_sticky_locked", False)
    lock_reason = data.get("lock_reason", "")
    is_confirmed = data.get("is_confirmed_active", True)
    drift_warn = data.get("drift_warning", "")

    lines = [
        "🧠 Live Awareness Status",
        "  Mode: read_only | can_act=False",
        (
            "  Active: "
            f"zone={data.get('active_zone')} | app={data.get('active_app')} | "
            f"flow={data.get('in_flow')} | idle={data.get('idle_state')}"
        ),
        (
            "  Browser: "
            f"available={data.get('browser_available')} | fresh={data.get('browser_fresh')} | "
            f"effective={data.get('browser_effective')} | age={age_text} | "
            f"kind={data.get('browser_kind')} | stale_reason={data.get('browser_stale_reason')}"
        ),
        (
            "  Focus: "
            f"source={data.get('focus_source')} | confidence={data.get('focus_confidence')} | "
            f"text={focus_preview}"
        ),
        (
            "  V2 Drift Tracking: "
            f"sticky_locked={is_locked} | lock_reason={lock_reason} | "
            f"confirmed_active={is_confirmed} | warning={drift_warn or '(none)'}"
        ),
        (
            "  Router: "
            f"source={router.get('source')} | can_answer={router.get('can_answer')} | "
            f"can_act={router.get('can_act')} | reason={router.get('reason')}"
        ),
        (
            "  Page: "
            f"title={_short(data.get('browser_title'), 120) or 'none'} | "
            f"url={_short(data.get('browser_url'), 120) or 'none'}"
        ),
        "  Rule: awareness reads context only; click/type/send/game input stay outside this layer.",
    ]
    return "\n".join(lines)


def is_live_awareness_question(text: str | None) -> bool:
    lowered = _normalize_vi(text)
    return any(_normalize_vi(marker) in lowered for marker in LIVE_AWARENESS_MARKERS)


def reply_denies_awareness(text: str | None) -> bool:
    lowered = _normalize_vi(text)
    return any(_normalize_vi(marker) in lowered for marker in AWARENESS_DENIAL_MARKERS)


def repair_awareness_reply(
    reply: str | None,
    awareness: dict[str, Any] | None = None,
    user_text: str | None = None,
) -> str:
    """Deterministically repair a browser-denial reply when context exists."""

    reply = (reply or "").strip()
    data = awareness or build_live_awareness_snapshot()
    if not reply_denies_awareness(reply):
        return reply
    if not data.get("browser_available"):
        return reply

    focus = data.get("focus_text") or ""
    title = data.get("browser_title") or data.get("browser_heading") or ""
    kind = (data.get("browser_kind") or "").lower()
    fresh = bool(data.get("browser_fresh"))
    prefix = "" if fresh else "Theo snapshot trình duyệt mới nhất thì "

    if data.get("focus_source") == "selected_text" and focus:
        return f"{prefix}Ba đang bôi đen đoạn này: {focus}"
    if focus and data.get("focus_source") in {"social_post", "local_summary"}:
        return f"{prefix}Ba đang nhìn phần này: {focus}"
    if title:
        if kind == "youtube":
            return f"{prefix}Ba đang xem YouTube: {title}"
        if kind in {"video", "music"}:
            return f"{prefix}Ba đang xem: {title}"
        return f"{prefix}Ba đang ở trang: {title}"
    return reply


def _focus_confidence(
    *,
    focus_source: str,
    browser_available: bool,
    browser_fresh: bool,
    in_browser_app: bool,
) -> float:
    if not browser_available or focus_source == "none":
        return 0.0
    base = {
        "selected_text": 0.95,
        "social_post": 0.9,
        "local_summary": 0.85,
        "page_heading": 0.8,
        "title": 0.72,
    }.get(focus_source, 0.5)
    if not browser_fresh:
        base -= 0.25
    if not in_browser_app:
        base -= 0.15
    return round(max(0.0, min(1.0, base)), 2)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _short(value: Any, limit: int = 220) -> str:
    text = _clean(value)
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "..."


def _normalize_vi(text: str | None) -> str:
    raw = str(text or "").lower()
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = raw.replace("đ", "d")
    raw = re.sub(r"\s+", " ", raw)
    return raw.strip()


__all__ = [
    "build_awareness_router_packet",
    "build_live_awareness_snapshot",
    "capture_live_awareness_source",
    "format_awareness_status",
    "format_live_awareness_prompt",
    "get_sticky_focus_state",
    "is_live_awareness_question",
    "lock_focus",
    "repair_awareness_reply",
    "reply_denies_awareness",
    "reset_sticky_focus",
    "unlock_focus_if_stale",
]
