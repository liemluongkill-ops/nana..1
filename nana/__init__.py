"""Nana AI companion runtime.

Public root-level names — re-exported from submodules for ergonomic access
(e.g. `from nana import context_state` instead of `from nana.runtime.context import context_state`).
"""

# ── Runtime state ──────────────────────────────────────────────────────────────
from nana.runtime.context import context_state, context_lock

# ── Memory ────────────────────────────────────────────────────────────────────
from nana.memory import memory, memory_lock

# ── Priority queue ────────────────────────────────────────────────────────────
from nana.runtime.priority_queue import runtime_queue

# ── Autonomy singleton ────────────────────────────────────────────────────────
from nana.autonomy import AUTONOMY_LOOP

# ── Pending actions ──────────────────────────────────────────────────────────
from nana.actions.pending import pending_actions

# ── Vision state ─────────────────────────────────────────────────────────────
from nana.brain.vision import LAST_VISION_DESCRIPTION

__all__ = [
    # Runtime context
    "context_state",
    "context_lock",
    # Memory
    "memory",
    "memory_lock",
    # Priority queue
    "runtime_queue",
    # Autonomy
    "AUTONOMY_LOOP",
    # Pending actions
    "pending_actions",
    # Vision
    "LAST_VISION_DESCRIPTION",
]
