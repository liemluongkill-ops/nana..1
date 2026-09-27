"""Background browser local-summary reconcile for the new runtime."""
from __future__ import annotations

import asyncio
import time

from nana.brain.local_helper import apply_local_summary
from nana.runtime.browser_refresh import browser_snapshot_from_context
from nana.runtime.context import context_lock, context_state
from nana.runtime.metrics import RUNTIME_LATENCY, RUNTIME_RECONCILE


RUNTIME_RECONCILE_TASK = None


def start_runtime_reconcile(loop):
    global RUNTIME_RECONCILE_TASK
    if RUNTIME_RECONCILE_TASK and not RUNTIME_RECONCILE_TASK.done():
        RUNTIME_RECONCILE["status"] = "running"
        RUNTIME_RECONCILE["coalesced"] = int(RUNTIME_RECONCILE.get("coalesced") or 0) + 1
        RUNTIME_RECONCILE["last_debug"] = "coalesced_running_task"
        return "already_running"

    with context_lock:
        browser = dict(context_state.get("browser", {}))
    if not browser.get("available"):
        RUNTIME_RECONCILE["status"] = "skipped"
        RUNTIME_RECONCILE["last_debug"] = "browser_unavailable"
        RUNTIME_RECONCILE["last_summary"] = None
        RUNTIME_RECONCILE["finished_at"] = time.time()
        return "browser_unavailable"

    snapshot = browser_snapshot_from_context(browser)
    RUNTIME_RECONCILE["status"] = "queued"
    RUNTIME_RECONCILE["last_debug"] = "queued"
    RUNTIME_RECONCILE["last_summary"] = None
    RUNTIME_RECONCILE["source_url"] = snapshot.url
    RUNTIME_RECONCILE["source_title"] = snapshot.title
    RUNTIME_RECONCILE_TASK = asyncio.create_task(_runtime_reconcile_worker(loop, snapshot))
    return "started"


async def _runtime_reconcile_worker(loop, snapshot):
    start = time.perf_counter()
    RUNTIME_RECONCILE["status"] = "running"
    RUNTIME_RECONCILE["started_at"] = time.time()
    RUNTIME_RECONCILE["finished_at"] = 0.0
    RUNTIME_RECONCILE["source_url"] = snapshot.url
    RUNTIME_RECONCILE["source_title"] = snapshot.title
    try:
        updated = await loop.run_in_executor(None, apply_local_summary, snapshot)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        with context_lock:
            current_url = (context_state.get("browser") or {}).get("url")
            if current_url != snapshot.url:
                RUNTIME_RECONCILE["status"] = "discarded"
                RUNTIME_RECONCILE["discarded"] = int(RUNTIME_RECONCILE.get("discarded") or 0) + 1
                RUNTIME_RECONCILE["last_debug"] = "snapshot_changed"
                RUNTIME_RECONCILE["last_summary"] = None
                return
            context_state["browser"]["local_summary"] = updated.local_summary
            context_state["browser"]["local_helper_debug"] = updated.local_helper_debug
        RUNTIME_LATENCY["local_summary_last_ms"] = float(elapsed_ms)
        RUNTIME_RECONCILE["status"] = "done"
        RUNTIME_RECONCILE["count"] = int(RUNTIME_RECONCILE.get("count") or 0) + 1
        RUNTIME_RECONCILE["last_ms"] = float(elapsed_ms)
        RUNTIME_RECONCILE["last_debug"] = updated.local_helper_debug
        RUNTIME_RECONCILE["last_summary"] = updated.local_summary
    except Exception as exc:
        RUNTIME_RECONCILE["status"] = "failed"
        RUNTIME_RECONCILE["failures"] = int(RUNTIME_RECONCILE.get("failures") or 0) + 1
        RUNTIME_RECONCILE["last_debug"] = f"{type(exc).__name__}"
        RUNTIME_RECONCILE["last_summary"] = None
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if RUNTIME_RECONCILE.get("last_ms") is None or RUNTIME_RECONCILE.get("status") in {"discarded", "failed"}:
            RUNTIME_RECONCILE["last_ms"] = float(elapsed_ms)
        RUNTIME_RECONCILE["finished_at"] = time.time()


def print_runtime_reconcile_status():
    print("🧩 Runtime Reconcile")
    print(f"  Status: {RUNTIME_RECONCILE.get('status')}")
    print(
        f"  Count: {RUNTIME_RECONCILE.get('count')} | "
        f"coalesced={RUNTIME_RECONCILE.get('coalesced')} | "
        f"failures={RUNTIME_RECONCILE.get('failures')} | discarded={RUNTIME_RECONCILE.get('discarded')}"
    )
    print(f"  Last latency: {_format_ms(RUNTIME_RECONCILE.get('last_ms'))}")
    print(f"  Last debug: {RUNTIME_RECONCILE.get('last_debug') or 'none'}")
    print(f"  Last summary: {_shorten_line(RUNTIME_RECONCILE.get('last_summary'), 180) or 'none'}")
    print(f"  Source URL: {_shorten_line(RUNTIME_RECONCILE.get('source_url'), 160) or 'none'}")


def _format_ms(value):
    if value is None:
        return "None"
    try:
        return f"{float(value):.1f}ms"
    except (TypeError, ValueError):
        return str(value)


def _shorten_line(text, limit):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
