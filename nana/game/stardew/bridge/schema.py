# Stardew Bridge Schema - V2
# JSON schemas for Observer state and Planner commands.
# NO socket, NO SMAPI, NO input validation against live state.

from typing import Any, Set


# Allowed command types
ALLOWED_COMMANDS: Set[str] = {
    "noop",
    "move_to",
    "interact",
    "use_tool",
    "water",
    "plant",
    "harvest",
    "check_time",
    "check_inventory",
}

# Forbidden command types (safety net)
FORBIDDEN_COMMANDS: Set[str] = {
    "send_key",
    "press_key",
    "hold_key",
    "release_key",
    "mouse_move",
    "mouse_click",
    "mouse_down",
    "mouse_up",
    "left_click",
    "right_click",
    "click",
    "move_and_click",
}


class CommandValidator:
    """Validates command JSON shape.

    Only checks structure. Does NOT connect to SMAPI or game.
    """

    def validate(self, command: dict) -> bool:
        """Check if command has required top-level fields."""
        if not isinstance(command, dict):
            return False
        if "command" not in command:
            return False
        if "timestamp" not in command:
            return False
        return True

    def validate_reason(self, reason: str) -> bool:
        """Check if reason field is a non-empty string."""
        return isinstance(reason, str) and len(reason) > 0


class StateValidator:
    """Validates observer state JSON shape."""

    def validate(self, state: dict) -> bool:
        """Check if state has expected structure."""
        if not isinstance(state, dict):
            return False
        # Basic check: has timestamp
        if "timestamp" not in state:
            return False
        return True


def is_valid_command(command: str) -> bool:
    """Check if command type is allowed."""
    return command in ALLOWED_COMMANDS


def is_forbidden(command: str) -> bool:
    """Check if command type is explicitly forbidden."""
    return command in FORBIDDEN_COMMANDS


# ---- Inbox / Ack Protocol Schemas (STARDEW-V2-8D) ----

# File paths relative to data directory
INBOX_FILENAME = "command_inbox.json"
ACK_FILENAME = "command_ack.json"


def build_inbox_message(
    command: str,
    goal_text: str,
    preview_intent: str,
    dry_run: bool = True,
    target: dict | None = None,
    zone: str = "unknown",
    command_id: str | None = None,
    reason: str | None = None,
) -> dict:
    """Build a dry-run inbox message dict.

    Shows what Python sends to SMAPI for acknowledgment.
    SMAPI must NOT execute this; it only acknowledges receipt.
    """
    import time
    import uuid

    msg = {
        "schema_version": "stardew_bridge_command_v1",
        "protocol": "dry_run_ack_v1",
        "command_id": command_id or str(uuid.uuid4()),
        "command": command,
        "source": "goal_preview",
        "dry_run": dry_run,
        "ack_only": True,
        "forwarded": True,
        "can_execute": False,
        "input_allowed": False,
        "timestamp": time.time(),
    }
    if zone:
        msg["map"] = zone
    if target:
        msg["target"] = target
    if goal_text:
        msg["goal_text"] = goal_text
    if preview_intent:
        msg["preview_intent"] = preview_intent
    if reason:
        msg["reason"] = reason
    return msg


def build_ack_message(command_id: str, status: str, reason: str) -> dict:
    """Build a dry-run ack message dict (Python-side stub).

    Real ack comes from SMAPI. This is used by smoke tests and as fallback.
    """
    import time

    return {
        "schema_version": "stardew_bridge_ack_v1",
        "protocol": "dry_run_ack_v1",
        "command_id": command_id,
        "status": status,
        "executed": False,
        "input_allowed": False,
        "reason": reason,
        "timestamp": time.time(),
    }
