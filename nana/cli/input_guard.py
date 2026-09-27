"""Input guard helpers for Nana CLI runtime."""
from __future__ import annotations

import time

from nana.cli.globals import (
    LAST_USER_INPUTS,
    LAST_USER_INPUT_DEDUP_LIMIT,
    LAST_USER_INPUT_DEDUP_WINDOW,
)


def normalize_user_input_key(text):
    return str(text or "").strip(" \t\r\n.,!?;:，。！？…")


def check_duplicate_user_input(text):
    """Return (should_skip, reason, repeat_count) for incoming user text."""
    key = normalize_user_input_key(text)
    if not key:
        return False, "empty", 0

    now = time.time()
    cutoff = now - LAST_USER_INPUT_DEDUP_WINDOW
    LAST_USER_INPUTS[:] = [
        (ts, k) for ts, k in LAST_USER_INPUTS
        if ts >= cutoff and k
    ][:LAST_USER_INPUT_DEDUP_LIMIT * 4]

    LAST_USER_INPUTS.append((now, key))
    del LAST_USER_INPUTS[:-LAST_USER_INPUT_DEDUP_LIMIT * 4:]

    same = [ts for ts, k in LAST_USER_INPUTS if k == key]
    repeat_count = len(same)
    if repeat_count >= LAST_USER_INPUT_DEDUP_LIMIT:
        return True, f"duplicate_input_x{repeat_count}", repeat_count
    return False, "ok", repeat_count
