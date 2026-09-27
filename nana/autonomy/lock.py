"""Autonomy lock helper — checks if an action is blocked by the autonomy lock.

Extracted from main.py (Giai đoạn wire fix).

Note: AUTONOMY_LOCK_BLOCKED_ACTIONS is hardcoded here (instead of imported from
nana.phases.commons) to avoid a circular import:
    nana.__init__ -> nana.autonomy -> nana.phases -> nana.phases.phase12 ->
    nana.autonomy  (circular!)
The same set of blocked actions is mirrored in nana.phases.commons for use
within the phases subsystem.
"""

AUTONOMY_LOCK_PHASE = "Phase 5-10"
AUTONOMY_LOCK_RULE = "no_autonomy_no_semi_autonomy"

# Hardcoded to avoid circular import with nana.phases.commons
_AUTONOMY_LOCK_BLOCKED_ACTIONS = {
    "browser.click",
    "browser.type",
    "social.type_draft",
    "message.send",
    "purchase.checkout",
}

__all__ = [
    "autonomy_lock_block_reason",
    "AUTONOMY_LOCK_PHASE",
    "AUTONOMY_LOCK_RULE",
]


def autonomy_lock_block_reason(action_name: str) -> str | None:
    """Returns reason string if action is blocked by autonomy lock, else None."""
    if action_name in _AUTONOMY_LOCK_BLOCKED_ACTIONS:
        return f"{AUTONOMY_LOCK_RULE}:{action_name}"
    return None
