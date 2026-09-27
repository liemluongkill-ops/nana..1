"""Stardew Adapter V2 registry shim.

This module keeps the optional game-adapter boundary importable without
re-opening the retired Python movement stack.  It only exposes the V2
observer/planner/bridge command surface.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from nana.commands.registry import STARDEW_STATIC_COMMANDS
from nana.game.stardew import command_catalog


_HANDLER_NAMES: dict[str, str] = {
    "/stardew-status": "stardew_status",
    "/stardew-observer-status": "stardew_observer_status",
    "/stardew-plan-status": "stardew_plan_status",
    "/stardew-goal-preview": "stardew_goal_preview",
    "/stardew-bridge-status": "stardew_bridge_status",
    "/stardew-help": "stardew_help",
}


def _resolve_handler(name: str) -> Callable[..., str] | None:
    if name not in set(_HANDLER_NAMES.values()):
        return None
    from nana.game.stardew import commands

    return getattr(commands, name, None)


def phase_command_set(min_phase=None, max_phase=None, ctx=None) -> set[str]:
    """Return the active V2 command set.

    Phase ranges belong to the retired legacy surface.  Keeping this function
    importable prevents old callers from crashing, but it does not revive the
    legacy command catalog.
    """
    if min_phase is not None or max_phase is not None:
        return {"/stardew-runtime-validation-test"}
    return set(STARDEW_STATIC_COMMANDS) | command_catalog.visible_command_names(include_main=False)


def register_phase_commands(command_set, *, min_phase=None, max_phase=None, ctx=None) -> set[str]:
    registered = phase_command_set(min_phase=min_phase, max_phase=max_phase, ctx=ctx)
    if command_set is not None:
        command_set.update(registered)
    return registered


def resolve_export(name: str, ctx=None) -> Tuple[bool, Optional[Any]]:
    value = _resolve_handler(str(name or ""))
    if value is None:
        return False, None
    return True, value


def module_name_for_export(name: str) -> Optional[str]:
    found, _value = resolve_export(name)
    if not found:
        return None
    return "nana.game.stardew.commands"


def handle_phase_command(ctx, text, vts=None, voice=None, *, min_phase=None, max_phase=None):
    if min_phase is not None or max_phase is not None:
        return None

    text_stripped = str(text or "").strip()
    if not text_stripped:
        return None
    command, _, args = text_stripped.partition(" ")
    handler_name = _HANDLER_NAMES.get(command.lower())
    handler = _resolve_handler(handler_name or "")
    if handler is None:
        entry = command_catalog.best_command_entry(command.lower())
        if entry is None:
            return None
        if entry.visibility != command_catalog.VISIBILITY_VISIBLE:
            print("archived_stardew_command_disabled")
            print(f"  Command: {command.lower()}")
            print(f"  Reason: {entry.reason or 'command_moved_to_archive_registry'}")
            return False
        print("Stardew command metadata")
        print(f"  Command: {entry.command}")
        print(f"  Visibility: {entry.visibility}")
        print(f"  Category: {entry.category}")
        return False
    if command.lower() == "/stardew-goal-preview":
        handler(args)
    else:
        handler()
    return False


def archived_command_metadata(command: str) -> dict[str, Any]:
    """Describe retired commands without dispatching them."""
    metadata = command_catalog.archived_metadata(command)
    if metadata is not None:
        return dict(metadata)
    return {
        "command": str(command or "").strip(),
        "active": False,
        "reason": "command_moved_to_archive_registry",
        "replacement": "stardew_adapter_v2_observer_planner_bridge",
    }
