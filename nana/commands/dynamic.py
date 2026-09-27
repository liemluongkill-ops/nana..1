"""Stardew + osu dynamic command dispatchers."""
from nana.commands.registry import (
    STARDEW_STATIC_COMMANDS,
    KNOWN_SLASH_COMMANDS,
    OSU_STATIC_COMMANDS,
)
from nana.runtime.capabilities import (
    invoke_stardew_v2_command,
    osu_adapter_enabled,
    register_osu_commands,
    is_osu_command,
    stardew_adapter_enabled,
)


def is_stardew_adapter_command(text: str) -> bool:
    """Check if text is a stardew command (V2 only, legacy phases removed)."""
    cmd = str(text or "").strip().split(" ", 1)[0].lower()
    return cmd in STARDEW_STATIC_COMMANDS


def sync_stardew_command_surface() -> dict:
    """Register only the safe Stardew Adapter V2 command surface."""
    if not stardew_adapter_enabled():
        before = len(KNOWN_SLASH_COMMANDS)
        KNOWN_SLASH_COMMANDS.difference_update(STARDEW_STATIC_COMMANDS)
        return {
            "enabled": False,
            "known_commands": set(),
            "stardew_added": 0,
            "stardew_removed": before - len(KNOWN_SLASH_COMMANDS),
            "note": "adapter_off",
        }
    before = len(KNOWN_SLASH_COMMANDS)
    KNOWN_SLASH_COMMANDS.update(STARDEW_STATIC_COMMANDS)
    return {
        "enabled": True,
        "known_commands": set(STARDEW_STATIC_COMMANDS),
        "stardew_added": len(KNOWN_SLASH_COMMANDS) - before,
        "stardew_removed": 0,
        "note": "v2_only",
    }


def handle_stardew_v2_command(text: str) -> bool:
    """Dispatch safe Stardew Adapter V2 status/help commands."""
    text_stripped = str(text or "").strip()
    # Split only on first space to separate command from arguments
    cmd = text_stripped.split(" ", 1)[0].lower()
    # Extract the rest of the text as arguments (empty string if no args)
    args = text_stripped.split(" ", 1)[1] if " " in text_stripped else ""
    handler_names = {
        "/stardew-status": "stardew_status",
        "/stardew-observer-status": "stardew_observer_status",
        "/stardew-plan-status": "stardew_plan_status",
        "/stardew-goal-preview": "stardew_goal_preview",
        "/stardew-bridge-status": "stardew_bridge_status",
        "/stardew-help": "stardew_help",
    }
    handler_name = handler_names.get(cmd)
    if not handler_name:
        return False
    if not stardew_adapter_enabled():
        print("Stardew adapter inactive.")
        return True
    try:
        # goal_preview parses goal text + optional --bridge-dry-run flag
        if handler_name == "stardew_goal_preview":
            invoke_stardew_v2_command(handler_name, args)
        else:
            invoke_stardew_v2_command(handler_name)
    except Exception as exc:
        print("⚠️ Stardew Adapter V2 command failed")
        print(f"  Command: {cmd}")
        print(f"  Error: {type(exc).__name__}: {exc}")
    return True


def sync_osu_command_surface() -> dict:
    if osu_adapter_enabled():
        KNOWN_SLASH_COMMANDS.update(OSU_STATIC_COMMANDS)
        registered = register_osu_commands(KNOWN_SLASH_COMMANDS, ctx=globals())
        osu_count = sum(1 for cmd in KNOWN_SLASH_COMMANDS if is_osu_command(cmd))
        return {
            "enabled": True,
            "static_count": len(OSU_STATIC_COMMANDS),
            "registered_count": len(registered),
            "osu_count": osu_count,
            "known_count": len(KNOWN_SLASH_COMMANDS),
        }
    remove = {cmd for cmd in KNOWN_SLASH_COMMANDS if is_osu_command(cmd)}
    KNOWN_SLASH_COMMANDS.difference_update(remove)
    return {
        "enabled": False,
        "static_count": len(OSU_STATIC_COMMANDS),
        "registered_count": 0,
        "osu_count": 0,
        "known_count": len(KNOWN_SLASH_COMMANDS),
    }
