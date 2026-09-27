"""Layered compatibility data for Nana's broad command registry.

This module preserves the old public API while keeping command categories
separate.  ``KNOWN_SLASH_COMMANDS`` remains a mutable suggestion/normalization
surface; it is not the live router truth.  Live truth is still audited through
``dispatch_surface`` and ``registry_truth``.
"""

from __future__ import annotations

from nana.commands.registry_adapters import OSU_STATIC_COMMANDS, STARDEW_STATIC_COMMANDS
from nana.commands.registry_archived import ARCHIVED_REGISTRY_COMMANDS
from nana.commands.registry_known_only import KNOWN_REGISTRY_ONLY_COMMANDS
from nana.commands.registry_live import LIVE_SLASH_COMMANDS
from nana.commands.registry_regression_cases import COMMAND_NORMALIZATION_CASES, COMMAND_ROUTE_CASES
from nana.commands.registry_reserved import RESERVED_REGISTRY_COMMANDS

KNOWN_SLASH_COMMANDS: set[str] = set().union(
    LIVE_SLASH_COMMANDS,
    ARCHIVED_REGISTRY_COMMANDS,
    RESERVED_REGISTRY_COMMANDS,
    KNOWN_REGISTRY_ONLY_COMMANDS,
)

__all__ = [
    "KNOWN_SLASH_COMMANDS",
    "STARDEW_STATIC_COMMANDS",
    "OSU_STATIC_COMMANDS",
    "COMMAND_NORMALIZATION_CASES",
    "COMMAND_ROUTE_CASES",
]
