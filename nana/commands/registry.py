"""Compatibility facade for Nana's broad slash-command registry.

This module is intentionally not the live router truth.  It exposes the same
mutable registry objects used by normalization/suggestion code, while
``registry_data`` assembles layered live/archive/reserved buckets.
"""

from __future__ import annotations

from nana.commands.registry_data import (
    COMMAND_NORMALIZATION_CASES,
    COMMAND_ROUTE_CASES,
    KNOWN_SLASH_COMMANDS,
    OSU_STATIC_COMMANDS,
    STARDEW_STATIC_COMMANDS,
)

__all__ = [
    "KNOWN_SLASH_COMMANDS",
    "STARDEW_STATIC_COMMANDS",
    "OSU_STATIC_COMMANDS",
    "COMMAND_NORMALIZATION_CASES",
    "COMMAND_ROUTE_CASES",
]
