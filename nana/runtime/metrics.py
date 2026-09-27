"""
Runtime metrics — browser refresh latency, coalescing, reconcile state.
Tách khỏi main.py (GĐ 0.2).
"""

import time


BROWSER_REFRESH_COOLDOWN_SECONDS = 2.0

RUNTIME_LATENCY: dict = {
    "browser_refresh_count": 0,
    "browser_refresh_total_ms": 0.0,
    "browser_refresh_last_ms": None,
    "browser_read_last_ms": None,
    "local_summary_last_ms": None,
    "browser_refresh_last_deep": False,
    "browser_refresh_last_reason": "none",
    "browser_refresh_last_time": 0.0,
    "browser_refresh_cache_hits": 0,
    "browser_refresh_coalesced": 0,
    "browser_refresh_cooldown_skips": 0,
    "browser_refresh_last_policy": "none",
    "browser_refresh_last_policy_reason": "none",
    "browser_refresh_last_state": "INVALID",
    "browser_refresh_last_skip_age": None,
}

BROWSER_REFRESH_INFLIGHT = None

RUNTIME_RECONCILE: dict = {
    "status": "idle",
    "count": 0,
    "coalesced": 0,
    "failures": 0,
    "discarded": 0,
    "last_ms": None,
    "last_debug": None,
    "last_summary": None,
    "started_at": 0.0,
    "finished_at": 0.0,
    "source_url": None,
    "source_title": None,
}


def record_browser_refresh_latency(read_ms, summary_ms, total_ms, reason="manual", deep=False):
    RUNTIME_LATENCY["browser_refresh_count"] = int(RUNTIME_LATENCY.get("browser_refresh_count") or 0) + 1
    RUNTIME_LATENCY["browser_refresh_total_ms"] = float(RUNTIME_LATENCY.get("browser_refresh_total_ms") or 0.0) + float(total_ms)
    RUNTIME_LATENCY["browser_refresh_last_ms"] = float(total_ms)
    RUNTIME_LATENCY["browser_read_last_ms"] = float(read_ms)
    RUNTIME_LATENCY["local_summary_last_ms"] = None if summary_ms is None else float(summary_ms)
    RUNTIME_LATENCY["browser_refresh_last_deep"] = bool(deep)
    RUNTIME_LATENCY["browser_refresh_last_reason"] = reason
    RUNTIME_LATENCY["browser_refresh_last_time"] = time.time()
    RUNTIME_LATENCY["browser_refresh_last_policy"] = "refresh"
    RUNTIME_LATENCY["browser_refresh_last_policy_reason"] = reason
    RUNTIME_LATENCY["browser_refresh_last_state"] = current_browser_snapshot_state()


def record_browser_cache_hit(reason, state):
    RUNTIME_LATENCY["browser_refresh_cache_hits"] = int(RUNTIME_LATENCY.get("browser_refresh_cache_hits") or 0) + 1
    RUNTIME_LATENCY["browser_refresh_last_policy"] = "cache"
    RUNTIME_LATENCY["browser_refresh_last_policy_reason"] = reason
    RUNTIME_LATENCY["browser_refresh_last_state"] = state


def record_browser_coalesced(reason):
    RUNTIME_LATENCY["browser_refresh_coalesced"] = int(RUNTIME_LATENCY.get("browser_refresh_coalesced") or 0) + 1
    RUNTIME_LATENCY["browser_refresh_last_policy"] = "coalesced"
    RUNTIME_LATENCY["browser_refresh_last_policy_reason"] = reason


def record_browser_cooldown_skip(reason, state, age):
    RUNTIME_LATENCY["browser_refresh_cooldown_skips"] = int(RUNTIME_LATENCY.get("browser_refresh_cooldown_skips") or 0) + 1
    RUNTIME_LATENCY["browser_refresh_last_policy"] = "cooldown"
    RUNTIME_LATENCY["browser_refresh_last_policy_reason"] = reason
    RUNTIME_LATENCY["browser_refresh_last_state"] = state
    RUNTIME_LATENCY["browser_refresh_last_skip_age"] = None if age is None else float(age)


def browser_refresh_last_age_seconds():
    last_time = float(RUNTIME_LATENCY.get("browser_refresh_last_time") or 0.0)
    if not last_time:
        return None
    return max(0.0, time.time() - last_time)


def current_browser_snapshot_state():
    """Return current browser snapshot state without importing legacy main."""
    from nana.runtime.browser_state import current_browser_snapshot_state as _fn

    return _fn()
