"""Stardew Adapter V2 command surface metadata.

The live CLI keeps only the V2 observer/planner/bridge commands. This module is
a compatibility metadata surface for tests and audits; it does not dispatch the
retired movement stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from nana.game.stardew import command_catalog


@dataclass(frozen=True)
class CommandSurfaceSyncResult:
    known_commands: set[str]
    summary: dict[str, object]


def visible_command_names(*, include_main: bool = False) -> set[str]:
    return command_catalog.visible_command_names(include_main=include_main)


def hidden_or_internal_command_names(*, include_main: bool = False) -> set[str]:
    return command_catalog.hidden_or_internal_command_names(include_main=include_main)


def command_surface_summary(*, include_main: bool = False) -> dict[str, object]:
    catalog_summary = command_catalog.catalog_summary(include_main=include_main)
    return {
        "schema": "nana.stardew.command_surface.v1",
        "dispatch_behavior_changed": False,
        "unique_commands_visible": catalog_summary["unique_commands_visible"],
        "unique_commands_hidden_or_internal": catalog_summary["unique_commands_hidden_or_internal"],
        "sample_visible_count": len(visible_command_names(include_main=include_main)),
        "sample_hidden_or_internal_count": len(hidden_or_internal_command_names(include_main=include_main)),
        "source": "metadata_compat_surface",
        "include_main": bool(include_main),
    }


def _call_register_phase_commands(
    command_set: set[str],
    register_phase_commands: Callable[..., Iterable[str]] | None,
    *,
    min_phase=None,
    max_phase=None,
    ctx=None,
) -> set[str]:
    if register_phase_commands is None:
        return set()
    registered = register_phase_commands(
        command_set,
        min_phase=min_phase,
        max_phase=max_phase,
        ctx=ctx,
    )
    return set(registered or set())


def sync_stardew_known_commands(
    command_set,
    *,
    static_commands=None,
    register_phase_commands: Callable[..., Iterable[str]] | None = None,
    adapter_enabled: bool,
    is_stardew_adapter_command: Callable[[str], bool],
    ctx=None,
    min_phase=None,
    max_phase=None,
) -> CommandSurfaceSyncResult:
    base = set(command_set or set())
    static = set(static_commands or set())

    if not adapter_enabled:
        kept = {cmd for cmd in base if not is_stardew_adapter_command(cmd)}
        return CommandSurfaceSyncResult(
            known_commands=kept,
            summary={
                "enabled": False,
                "static_count": len(static),
                "registered_count": 0,
                "stardew_count": 0,
                "known_count": len(kept),
                "visible_count": command_catalog.VISIBLE_COUNT,
                "hidden_or_internal_count": command_catalog.HIDDEN_OR_INTERNAL_COUNT,
            },
        )

    known = set(base)
    known.update(static)
    known.update(visible_command_names(include_main=False))
    known.update(hidden_or_internal_command_names(include_main=False))
    registered = _call_register_phase_commands(
        known,
        register_phase_commands,
        min_phase=min_phase,
        max_phase=max_phase,
        ctx=ctx,
    )
    known.update(registered)
    stardew_count = sum(1 for cmd in known if is_stardew_adapter_command(cmd))

    return CommandSurfaceSyncResult(
        known_commands=known,
        summary={
            "enabled": True,
            "static_count": len(static),
            "registered_count": len(registered),
            "stardew_count": stardew_count,
            "known_count": len(known),
            "visible_count": command_catalog.VISIBLE_COUNT,
            "hidden_or_internal_count": command_catalog.HIDDEN_OR_INTERNAL_COUNT,
        },
    )
