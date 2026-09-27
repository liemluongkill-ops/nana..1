"""nana.phases - phase helpers subsystem.

This package keeps the old import surface stable while avoiding eager imports
of every extracted phase module.  Phase functions are resolved lazily on first
call through ``live_surface``.
"""
from __future__ import annotations

from nana.phases.commons import (
    AUTONOMY_LOCK_BLOCKED_ACTIONS,
    CASUAL_POOLS,
    PHASE7_VIRTUAL_ACTIONS,
    PHASE7_BASE_GUARD_CONTEXT,
    PHASE7_SOCIAL_GUARD_CONTEXT,
    PHASE10_REQUIRED_COMMANDS,
    PHASE10_1_REQUIRED_GROUPS,
    PHASE10_1_COMMANDS,
    PHASE10_2_COMMANDS,
    PHASE10_2_EXPECTED_ALIASES,
    PHASE10_3_COMMANDS,
    PHASE10_3_REQUIRED_SCHEMA_GROUPS,
    PHASE10_4_COMMANDS,
    RUNTIME_EVENT_REQUIRED_FIELDS,
    PHASE10_5_COMMANDS,
    PHASE10_6_COMMANDS,
    PHASE10_7_COMMANDS,
    PHASE10_8_COMMANDS,
    PHASE10_9_COMMANDS,
    PHASE10_10_COMMANDS,
    PHASE11_1_COMMANDS,
    PHASE11_2_COMMANDS,
    TRUST_LEVELS,
    RUNTIME_TURN_STATE,
    TYPE_ALIASES,
    VALID_TURN_STATUSES,
)

_COMMON_EXPORTS = {
    "AUTONOMY_LOCK_BLOCKED_ACTIONS",
    "CASUAL_POOLS",
    "PHASE7_VIRTUAL_ACTIONS",
    "PHASE7_BASE_GUARD_CONTEXT",
    "PHASE7_SOCIAL_GUARD_CONTEXT",
    "PHASE10_REQUIRED_COMMANDS",
    "PHASE10_1_REQUIRED_GROUPS",
    "PHASE10_1_COMMANDS",
    "PHASE10_2_COMMANDS",
    "PHASE10_2_EXPECTED_ALIASES",
    "PHASE10_3_COMMANDS",
    "PHASE10_3_REQUIRED_SCHEMA_GROUPS",
    "PHASE10_4_COMMANDS",
    "RUNTIME_EVENT_REQUIRED_FIELDS",
    "PHASE10_5_COMMANDS",
    "PHASE10_6_COMMANDS",
    "PHASE10_7_COMMANDS",
    "PHASE10_8_COMMANDS",
    "PHASE10_9_COMMANDS",
    "PHASE10_10_COMMANDS",
    "PHASE11_1_COMMANDS",
    "PHASE11_2_COMMANDS",
    "TRUST_LEVELS",
    "RUNTIME_TURN_STATE",
    "TYPE_ALIASES",
    "VALID_TURN_STATUSES",
}


def __getattr__(name: str):
    from nana.phases.live_surface import resolve_phase_symbol

    return resolve_phase_symbol(name)


def __dir__():
    from nana.phases.live_surface import lazy_phase_names

    return sorted(set(globals()) | _COMMON_EXPORTS | set(lazy_phase_names()))


def _build_all():
    from nana.phases.live_surface import lazy_phase_names

    return tuple(sorted(_COMMON_EXPORTS | set(lazy_phase_names())))


__all__ = _build_all()
