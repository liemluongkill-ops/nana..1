# Stardew Observer - V2
# Read-only state reader from file. NO input, NO movement, NO pathfinding.

import json
import os
import time
from typing import Optional

# Allowed: standard library only. No pyautogui, keyboard, win32api.

# BANNED IMPORTS (enforced by smoke):
#   pyautogui, keyboard, win32api, win32con, mouse
#   pydirectinput, inputs


# Default path for observer state JSON.
# This is the file-based bridge input path. When SMAPI bridge is connected,
# the bridge writes this file and observer reads it.
DEFAULT_STATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "data",
    "observer_state.json"
)

# Staleness threshold in seconds.
# Default 60s for dev testing. Override via NANA_STARDEW_OBSERVER_STALE_AFTER_SEC (1-300).
DEFAULT_MAX_AGE = 60.0
_MIN_STALE = 1.0
_MAX_STALE = 300.0


def _get_stale_after() -> float:
    """Read NANA_STARDEW_OBSERVER_STALE_AFTER_SEC env var, clamped 1-300."""
    raw = os.environ.get("NANA_STARDEW_OBSERVER_STALE_AFTER_SEC", "")
    try:
        val = float(raw)
        val = max(_MIN_STALE, min(_MAX_STALE, val))
        return val
    except (ValueError, TypeError):
        return DEFAULT_MAX_AGE


class StardewObserver:
    """Read-only Stardew Valley state reader.

    Reads game state from a JSON file written by the SMAPI bridge.
    If the file is missing, invalid, or stale, observer reports unavailable.
    Staleness threshold defaults to 60s, configurable via
    NANA_STARDEW_OBSERVER_STALE_AFTER_SEC (1-300s).
    No input logic, no movement, no pathfinding.
    """

    def __init__(
        self,
        state_path: Optional[str] = None,
        max_age: Optional[float] = None,
    ):
        self._state_path = state_path or DEFAULT_STATE_PATH
        self._max_age = max_age if max_age is not None else _get_stale_after()
        self._last_read = None
        self._last_file_mtime = None
        self._cached_state: Optional[dict] = None

    def read(self) -> dict:
        """Read current game state from file.

        Returns a dict matching the ObserverStateSchema.
        Returns empty state if file is missing, invalid, or stale.
        """
        self._last_read = time.time()

        # Check if file exists
        if not os.path.isfile(self._state_path):
            self._cached_state = None
            return {}

        # Check if file has changed since last read
        try:
            file_mtime = os.path.getmtime(self._state_path)
        except OSError:
            self._cached_state = None
            return {}

        if file_mtime != self._last_file_mtime or self._cached_state is None:
            # File changed or not cached — reload
            self._last_file_mtime = file_mtime
            self._cached_state = self._read_file()

        return self._cached_state if self._cached_state is not None else {}

    def _read_file(self) -> Optional[dict]:
        """Read and parse the state JSON file. Returns None on error."""
        try:
            with open(self._state_path, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return None
            # Require timestamp to be considered valid
            if "timestamp" not in raw:
                return None
            return raw
        except (json.JSONDecodeError, OSError):
            return None

    def is_available(self) -> bool:
        """True if a valid, non-stale state file exists."""
        state = self.read()
        if not state:
            return False
        return not self.is_stale()

    def is_stale(self, max_age: Optional[float] = None) -> bool:
        """Check if state is too old to be useful.

        True when: no state, no timestamp, or age > max_age.
        """
        state = self._cached_state if self._cached_state is not None else self.read()
        if not state:
            return True

        ts = state.get("timestamp")
        if ts is None:
            return True

        age = time.time() - ts
        threshold = max_age if max_age is not None else self._max_age
        return age > threshold

    def get_player_tile(self) -> Optional[dict]:
        """Get player position as {x, y} or None."""
        state = self.read()
        return state.get("player_tile")

    def get_energy(self) -> int:
        """Get current player energy, 0 if unknown."""
        state = self.read()
        return state.get("player_energy", 0)

    def get_zone(self) -> str:
        """Get current map/zone name, 'unknown' if not available."""
        state = self.read()
        return state.get("zone", "unknown")

    def get_time_of_day(self) -> int:
        """Get time of day in Stardew format (e.g. 900 = 9am), 0 if unknown."""
        state = self.read()
        return state.get("time_of_day", 0)

    def get_day(self) -> int:
        """Get day of month, 0 if unknown."""
        state = self.read()
        return state.get("day", 0)

    def get_season(self) -> str:
        """Get season name, 'unknown' if unknown."""
        state = self.read()
        return state.get("season", "unknown")

    def get_year(self) -> int:
        """Get year, 0 if unknown."""
        state = self.read()
        return state.get("year", 0)

    def state_age(self) -> float:
        """Return age of state in seconds, or infinity if no state."""
        state = self._cached_state if self._cached_state is not None else self.read()
        ts = state.get("timestamp") if state else None
        if ts is None:
            return float("inf")
        return time.time() - ts

    def stale_after(self) -> float:
        """Return the staleness threshold in seconds."""
        return self._max_age

    @property
    def state_path(self) -> str:
        """Return observer state file path."""
        return self._state_path


# Module-level convenience function for simple usage.
def read_state(state_path: Optional[str] = None) -> dict:
    """Read observer state from file. Returns {} if unavailable."""
    observer = StardewObserver(state_path=state_path)
    return observer.read()
