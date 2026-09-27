"""Shared mutable state and canonical autonomy aliases for the CLI layer."""
import time

from nana.autonomy import AUTONOMY_EXPRESS, AUTONOMY_LOOP

ai_active = True
last_gpt_time = 0
LAST_USER_INPUTS = []
LAST_USER_INPUT_DEDUP_WINDOW = 12.0
LAST_USER_INPUT_DEDUP_LIMIT = 4

PHASE82_RUNTIME_STREAM_MODE = "off"


def autonomy_note_user_command():
    try:
        AUTONOMY_LOOP.note_user_command()
    except Exception as exc:
        print(f"  [autonomy] note_user_command failed: {exc}")


# Canonical CLI runtime turn state.
RUNTIME_TURN_STATE = {
    "status": "idle",
    "last_update": 0.0,
    "last_reason": "",
    "last_source": "",
}


def set_runtime_turn_state(status, reason="manual", source="runtime"):
    RUNTIME_TURN_STATE.update({
        "status": status,
        "last_update": time.time(),
        "last_reason": reason,
        "last_source": source,
    })
