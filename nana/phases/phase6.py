"""Phase 6 — Social Draft & Vision Status.
Stub: Phase 6 is still in main.py and not yet split out.
The import in phase7.py is wrapped in try/except, so this stub
prevents ModuleNotFoundError while preserving phase7's graceful fallback.

This is not a live command surface.  /phase6-status and /phase6-ready remain
archived compatibility shells until phase 6 is split for real.
"""

from __future__ import annotations


def build_phase6_status_model():
    """Return a minimal status model so phase7's try/except never fires."""
    return {
        "checks": [],
        "blocking": [],
        "ready": True,
    }
