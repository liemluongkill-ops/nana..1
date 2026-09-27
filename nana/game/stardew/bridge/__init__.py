# Stardew Bridge - V2
# Interface between Python Planner and SMAPI/C# Executor.
# Health is backed by the SMAPI observer state and command ack files.
# Dry-run inbox/ack contract added in STARDEW-V2-8D.

import json
import math
import os
import time

from nana.game.stardew.observer import StardewObserver
from nana.game.stardew.bridge.schema import (
    CommandValidator,
    is_valid_command,
    build_inbox_message,
    build_ack_message,
    INBOX_FILENAME,
    ACK_FILENAME,
)

__all__ = [
    "StardewBridge",
    "CommandValidator",
    "is_valid_command",
    "INBOX_FILENAME",
    "ACK_FILENAME",
    "build_inbox_message",
    "build_ack_message",
]


def _data_dir() -> str:
    """Return the stardew data directory path.

    Points to the package-local nana/game/stardew/data/ directory.
    Same directory where observer_state.json lives.
    Must match the path used by NanaBridge C# for inbox/ack.
    """
    # bridge/__init__.py -> stardew/ -> nana/game/
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def inbox_path() -> str:
    """Return the path to the command inbox file."""
    return os.path.join(_data_dir(), INBOX_FILENAME)


def ack_path() -> str:
    """Return the path to the command ack file."""
    return os.path.join(_data_dir(), ACK_FILENAME)


def write_inbox(message: dict) -> str:
    """Write a dry-run inbox message to the command inbox file.

    This is only called when the user explicitly passes --bridge-dry-run.
    SMAPI reads this file and writes an ack. No execution occurs.

    Args:
        message: The inbox message dict.

    Returns:
        The inbox file path.

    Raises:
        OSError: If the file cannot be written.
    """
    path = inbox_path()
    dir_ = os.path.dirname(path)
    if dir_:
        os.makedirs(dir_, exist_ok=True)

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(message, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def read_ack() -> dict | None:
    """Read the command ack file if it exists and is valid.

    Returns:
        The ack dict if found and valid, otherwise None.
    """
    path = ack_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        if "command_id" not in data:
            return None
        return data
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def ack_age() -> float | None:
    """Return the age of the last ack file in seconds, or None if missing/stale."""
    path = ack_path()
    if not os.path.exists(path):
        return None
    try:
        mtime = os.path.getmtime(path)
        return time.time() - mtime
    except OSError:
        return None


class StardewBridge:
    """Reports SMAPI/C# bridge health and validates command JSON.

    Python remains observer/planner/inbox writer only. Game-side execution,
    when explicitly token-gated, belongs to the SMAPI/C# NanaBridge.
    """

    def __init__(self):
        self.validator = CommandValidator()
        self._connected = False

    def connect(self) -> bool:
        """Connect to SMAPI process.

        STUB: always returns False until real connection exists.
        """
        return False

    def send(self, command: dict) -> dict:
        """Send a command JSON to executor.

        Validates before sending. Returns result from executor.
        """
        if not self.validator.validate(command):
            return {
                "ok": False,
                "error": "schema_validation_failed",
                "command": command,
            }

        if not is_valid_command(command.get("command", "")):
            return {
                "ok": False,
                "error": "forbidden_command",
                "command": command,
            }

        # STUB: no real send yet
        return {
            "ok": False,
            "error": "bridge_not_connected",
            "command": command,
        }

    def health(self, observer: StardewObserver | None = None) -> dict:
        """Return read-only bridge health without executing any command."""
        obs = observer or StardewObserver()
        state = obs.read()
        available = obs.is_available()
        stale = obs.is_stale()
        age = obs.state_age()

        ack = read_ack()
        ack_age_sec = ack_age()
        action_plan = ack.get("executor_action_plan") if isinstance(ack, dict) else None
        if not isinstance(action_plan, dict):
            action_plan = None
        action_plan_steps = action_plan.get("steps") if action_plan else None
        if not isinstance(action_plan_steps, list):
            action_plan_steps = None
        executed = ack.get("executed", False) if ack else False
        input_allowed = ack.get("input_allowed", False) if ack else False
        executor_label = "smapi_csharp_executor" if executed or input_allowed else "smapi_csharp_bridge"
        action_plan_steps_count = None
        if action_plan_steps is not None:
            action_plan_steps_count = len(action_plan_steps)
        elif action_plan and action_plan.get("planned_steps") is not None:
            action_plan_steps_count = action_plan.get("planned_steps")

        return {
            "connected": bool(available),
            "source": "SMAPI observer_state.json",
            "state_path": obs.state_path,
            "observer_available": bool(available),
            "state_stale": bool(stale),
            "last_write_age_sec": None if math.isinf(age) else age,
            "stale_after_sec": obs.stale_after(),
            "last_read": state.get("timestamp") if state else None,
            "zone": state.get("zone", "unknown") if state else "unknown",
            "executor": executor_label,
            "executor_connected": bool(available and not stale),
            # Python never has standing permission to send input. A tokened
            # SMAPI execution reports its own per-command permission in ack.
            "input_allowed": False,
            "can_execute": False,
            # Inbox / ack paths (STARDEW-V2-8D)
            "inbox_path": inbox_path(),
            "ack_path": ack_path(),
            "last_ack_command_id": ack.get("command_id") if ack else None,
            "last_ack_status": ack.get("status") if ack else None,
            "last_ack_executed": ack.get("executed", False) if ack else None,
            "last_ack_input_allowed": ack.get("input_allowed", False) if ack else None,
            "last_ack_reason": ack.get("reason") if ack else None,
            "last_ack_planned_steps": ack.get("planned_steps") if ack else None,
            "last_ack_required_token": ack.get("required_token") if ack else None,
            "last_ack_token_provided": ack.get("token_provided") if ack else None,
            "last_ack_token_matched": ack.get("token_matched") if ack else None,
            "last_ack_executor_armed": ack.get("executor_armed") if ack else None,
            "last_ack_approval_status": ack.get("approval_status") if ack else None,
            "last_ack_action_plan_ready": ack.get("executor_action_plan_ready") if ack else None,
            "last_ack_action_plan_steps": action_plan_steps_count,
            "last_ack_action_plan_safety": action_plan.get("safety_mode") if action_plan else None,
            "last_ack_action_plan_executed_steps": action_plan.get("executed_steps") if action_plan else None,
            "last_ack_action_plan_remaining_steps": action_plan.get("remaining_steps") if action_plan else None,
            "last_ack_live_approval": ack.get("live_approval") if ack else None,
            "last_ack_events": ack.get("events") if ack else None,
            "last_ack_route_contract": ack.get("route_contract") if ack else None,
            "last_ack_passability_diagnostic": ack.get("passability_diagnostic") if ack else None,
            "last_ack_post_tile": ack.get("post_tile") if ack else None,
            "last_ack_post_map": ack.get("post_map") if ack else None,
            "last_ack_verified": ack.get("verified") if ack else None,
            "last_ack_executed": ack.get("executed", False) if ack else None,
            "last_ack_input_allowed": ack.get("input_allowed", False) if ack else None,
            "last_ack_age_sec": None if ack_age_sec is None else ack_age_sec,
        }

    @property
    def connected(self) -> bool:
        return self._connected
