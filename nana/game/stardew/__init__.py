"""Stardew Adapter V2 package exports.

Imports are lazy so metadata-only probes can inspect command catalogs without
loading observer, planner, bridge, or command handlers.
"""

from __future__ import annotations

from importlib import import_module


ADAPTER_VERSION = "2.0.0"
ADAPTER_STATE = "stub"

__all__ = [
    "StardewObserver",
    "StardewPlanner",
    "StardewBridge",
    "stardew_commands",
]


def __getattr__(name: str):
    if name == "StardewObserver":
        return import_module("nana.game.stardew.observer").StardewObserver
    if name == "StardewPlanner":
        return import_module("nana.game.stardew.planner").StardewPlanner
    if name == "StardewBridge":
        return import_module("nana.game.stardew.bridge").StardewBridge
    if name == "stardew_commands":
        return import_module("nana.game.stardew.commands")
    raise AttributeError(name)
