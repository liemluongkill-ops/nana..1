"""Nana autonomy module: continuous low-key behavior between user commands.

Public surface (re-exports for ergonomics):

    AutonomyLoop         the background thread
    CadenceScheduler     timing, jitter, burst control, backoff
    ExpressionGate       hybrid gate (hard + intensity + mix)
    InnerThought         template line picker
    AutonomyExpress      VTS / TTS / subtitle / lipsync wiring
    RealObserver         reads real signals from runtime state (Phase A5)
    Thought              dataclass for a picked line
    ExpressTrace         dataclass for an emit() trace
    AUTONOMY_LOOP        module-level AutonomyLoop singleton
    AUTONOMY_EXPRESS     module-level AUTONOMY_LOOP.express shorthand
"""

from nana.autonomy.cadence import CadenceScheduler
from nana.autonomy.expression_gate import ExpressionGate
from nana.autonomy.inner_thought import InnerThought, Thought
from nana.autonomy.express import AutonomyExpress, ExpressTrace
from nana.autonomy.loop import AutonomyLoop, AutonomyState, AUTONOMY_LOOP, AUTONOMY_EXPRESS
from nana.autonomy.observer import RealObserver
from nana.autonomy.wiring import wire_autonomy_backends

# Autonomy lock helpers (extracted from main.py)
# Constants defined here directly to avoid circular import with nana.phases
# (phase12 imports from nana.autonomy during phase/__init__.py load)
AUTONOMY_LOCK_PHASE = "Phase 5-10"
AUTONOMY_LOCK_RULE = "no_autonomy_no_semi_autonomy"

# Function from lock.py (lock.py itself is safe — AUTONOMY_LOCK_BLOCKED_ACTIONS
# from phases.commons is only needed inside the function, not at import time)
from nana.autonomy.lock import autonomy_lock_block_reason

__all__ = [
    "autonomy_lock_block_reason",
    "AutonomyLoop",
    "AutonomyState",
    "AUTONOMY_LOOP",
    "AUTONOMY_EXPRESS",
    "AUTONOMY_LOCK_PHASE",
    "AUTONOMY_LOCK_RULE",
    "AutonomyExpress",
    "CadenceScheduler",
    "ExpressionGate",
    "ExpressTrace",
    "InnerThought",
    "RealObserver",
    "Thought",
    "wire_autonomy_backends",
]
