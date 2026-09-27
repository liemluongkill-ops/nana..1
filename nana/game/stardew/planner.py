# Stardew Planner - V2
# High-level decision maker. NO movement, NO pathfinding.

import re as _re_module
import time
from typing import Optional

from nana.game.stardew.observer import StardewObserver
from nana.game.stardew.bridge.schema import CommandValidator

# Module-level regex + keyword sets (compiled once at import)
_RE_GO_TO = _re_module.compile(
    r"(?:go\s+to|move\s+to|đi\s+tới|đến)\s+(\d+)\s+(\d+)"
)

_WATER_KEYWORDS = frozenset({
    "water", "tưới", "tươi", "cây", "crops", "plant",
    "watering", "garden", "vườn",
})

_STATUS_KEYWORDS = frozenset({
    "status", "trạng thái", "xem", "look", "inspect", "check",
    "where", "đâu", "ở đâu", "inventory", "kho",
})


class StardewPlanner:
    """Decides what to do based on observer state.

    Reads from StardewObserver (file-based, SMAPI, or injected for testing).
    Always returns noop. Passes read-only structured data through for context.
    """

    # Safety thresholds
    MIN_ENERGY = 10
    MAX_TIME = 2340  # 10PM - no new movement after this

    def __init__(self, observer: Optional[StardewObserver] = None):
        self.observer = observer or StardewObserver()
        self.validator = CommandValidator()
        self._test_state: Optional[dict] = None

    def plan(self) -> dict:
        """Make a decision based on current observer state.

        Returns a structured command dict. Always noop at this stage.
        Safety gates block action when state is unreliable.
        """
        state = self._effective_state()
        ts = time.time()

        if not state:
            return self._noop("state_unavailable", ts, None)

        if self.observer.is_stale():
            return self._noop("state_stale", ts, state)

        energy = state.get("player_energy", 0)
        if energy < self.MIN_ENERGY:
            return self._noop("low_energy", ts, state)

        time_of_day = state.get("time_of_day", 0)
        if time_of_day > self.MAX_TIME:
            return self._noop("late_time", ts, state)

        return self._readonly_plan(ts, state)

    def _effective_state(self) -> dict:
        """Return test state if set, otherwise read from observer."""
        if self._test_state is not None:
            return self._test_state
        return self.observer.read()

    def _noop(self, reason: str, ts: float, state: Optional[dict]) -> dict:
        """Return a noop command."""
        plan = {
            "timestamp": ts,
            "command": "noop",
            "reason": reason,
            "observer_available": self.observer.is_available(),
        }
        if state:
            plan.update({
                "zone": state.get("zone", "unknown"),
                "player_tile": state.get("player_tile"),
                "energy": state.get("player_energy", 0),
                "time_of_day": state.get("time_of_day", 0),
                "date": self._format_date(state),
            })
        plan["bridge_command_draft"] = self._make_bridge_draft(None, reason, state)
        return plan

    def _readonly_plan(self, ts: float, state: dict) -> dict:
        """Return a structured read-only plan for fresh state."""
        zone = state.get("zone", "unknown")
        tile = state.get("player_tile")
        energy = state.get("player_energy", 0)
        time_of_day = state.get("time_of_day", 0)
        date_str = self._format_date(state)
        suggested = self._derive_suggestion(zone, energy, time_of_day)

        result = {
            "timestamp": ts,
            "command": "noop",
            "reason": "observer_ready",
            "observer_available": True,
            "zone": zone,
            "player_tile": tile,
            "energy": energy,
            "time_of_day": time_of_day,
            "date": date_str,
            "suggested_next": suggested,
        }
        result["bridge_command_draft"] = self._make_bridge_draft(
            "observer_ready", "plan_observer_ready", state
        )
        return result

    def _derive_suggestion(self, zone: str, energy: int, time_of_day: int) -> str:
        """Derive a read-only suggested next action hint from state."""
        if time_of_day >= 2100:
            return "wind_down"
        if energy < 20:
            return "rest"
        if energy < 60:
            return "conserve"
        if zone in ("unknown", None, ""):
            return "await_zone"
        return "await_goal"

    def _format_date(self, state: dict) -> str:
        """Format date as 'Season Day, Year N'."""
        season = state.get("season", "unknown")
        day = state.get("day", 0)
        year = state.get("year", 0)
        return f"{season.capitalize()} {day}, Year {year}" if day else "unknown"

    def set_state(self, state: dict) -> None:
        """Set test state directly (bypasses file read)."""
        self._test_state = state

    # ---- Goal Preview ----

    def goal_preview(self, goal_text: str) -> dict:
        """Parse a goal string and return a read-only preview.

        This does NOT execute anything. It only reports what the goal
        would be if executed, along with current observer state and
        safety-gate blockers.

        Args:
            goal_text: freeform goal string (e.g. "đi tới 70 18", "water crops")

        Returns:
            A structured preview dict with bridge_command_draft.
        """
        ts = time.time()
        state = self._effective_state()
        intent, target, extra = self._parse_goal(goal_text)

        # Safety gate: observer unavailable or state missing
        if not state:
            draft = self._make_bridge_draft(intent, "state_unavailable", state, target)
            return self._preview_base(
                ts,
                goal_text,
                "state_unavailable",
                intent,
                state,
                target=tuple(target) if target else None,
                draft=draft,
                **extra,
            )

        # Safety gate: state is stale
        if self.observer.is_stale():
            draft = self._make_bridge_draft(intent, "state_stale", state, target)
            return self._preview_base(
                ts,
                goal_text,
                "state_stale",
                intent,
                state,
                target=tuple(target) if target else None,
                draft=draft,
                **extra,
            )

        # Safety gate: very low energy
        energy = state.get("player_energy", 0)
        if energy < self.MIN_ENERGY:
            draft = self._make_bridge_draft(intent, "low_energy", state, target)
            return self._preview_base(
                ts,
                goal_text,
                "low_energy",
                intent,
                state,
                target=tuple(target) if target else None,
                draft=draft,
                **extra,
            )

        # Safety gate: too late in day
        time_of_day = state.get("time_of_day", 0)
        if time_of_day > self.MAX_TIME:
            draft = self._make_bridge_draft(intent, "late_time", state, target)
            return self._preview_base(
                ts,
                goal_text,
                "late_time",
                intent,
                state,
                target=tuple(target) if target else None,
                draft=draft,
                **extra,
            )

        draft_reason = self._intents_to_draft_reason(intent, state)
        draft = self._make_bridge_draft(intent, draft_reason, state, target)

        return self._preview_base(
            ts, goal_text,
            reason="goal_preview_only",
            preview_intent=intent,
            target=tuple(target) if target else None,
            state=state,
            draft=draft,
            **extra,
        )

    def _parse_goal(self, goal_text: str) -> tuple:
        """Parse goal text into (intent, target_xy, extra_kwargs)."""
        if not goal_text or not goal_text.strip():
            return ("goal_missing", None, {})

        text = goal_text.strip().lower()

        # move_to: "đi tới X Y", "move to X Y", "go to X Y"
        move_match = _RE_GO_TO.match(text)
        if move_match:
            try:
                x = int(move_match.group(1))
                y = int(move_match.group(2))
                return ("move_to", (x, y), {})
            except ValueError:
                pass

        # water_crops
        if any(kw in text for kw in _WATER_KEYWORDS):
            return ("water_crops", None, {})

        # inspect / status
        if any(kw in text for kw in _STATUS_KEYWORDS):
            return ("inspect_state", None, {})

        return ("unknown_goal", None, {})

    def _intents_to_draft_reason(self, intent: str, state: dict) -> str:
        """Map parsed intent to bridge draft reason string."""
        if intent == "goal_missing":
            return "goal_preview_goal_missing"
        if intent == "move_to":
            return "goal_preview_move_to"
        if intent == "water_crops":
            return "goal_preview_water_crops_requires_observer_crop_tiles"
        if intent == "inspect_state":
            return "goal_preview_inspect_state_is_read_only"
        if intent == "unknown_goal":
            return "goal_preview_unknown_goal"
        return f"goal_preview_{intent}"

    def _make_bridge_draft(
        self,
        intent: Optional[str],
        reason: str,
        state: Optional[dict],
        target: Optional[tuple] = None,
    ) -> dict:
        """Build a bridge_command_draft dict.

        Shows what Python would ask the future SMAPI/C# executor to do.
        Never executes. Never writes queue files.
        """
        zone = state.get("zone", "unknown") if state else "unknown"

        # Build draft based on intent
        if intent == "move_to" and target and reason == "goal_preview_move_to":
            return {
                "schema_version": "stardew_bridge_command_v1",
                "command": "move_to",
                "map": zone,
                "target": {"x": target[0], "y": target[1]},
                "source": "goal_preview",
                "dry_run": True,
                "forwarded": False,
                "can_execute": False,
                "input_allowed": False,
                "reason": reason,
            }

        # All other cases: noop draft
        draft = {
            "schema_version": "stardew_bridge_command_v1",
            "command": "noop",
            "dry_run": True,
            "forwarded": False,
            "can_execute": False,
            "input_allowed": False,
            "reason": reason,
        }
        if intent == "water_crops":
            draft["command"] = "noop"
        elif intent == "inspect_state":
            draft["command"] = "noop"
        return draft

    def _preview_base(
        self,
        ts: float,
        goal_text: str,
        reason: str,
        preview_intent: Optional[str],
        state: Optional[dict],
        target: Optional[tuple] = None,
        draft: Optional[dict] = None,
        **extra,
    ) -> dict:
        """Build the standard preview dict."""
        zone = state.get("zone", "unknown") if state else "unknown"
        tile = state.get("player_tile") if state else None
        energy = state.get("player_energy", 0) if state else 0
        time_of_day = state.get("time_of_day", 0) if state else 0
        date_str = self._format_date(state) if state else "unknown"

        preview = {
            "timestamp": ts,
            "command": "noop",
            "reason": reason,
            "goal": goal_text,
            "preview_intent": preview_intent,
            "observer_available": self.observer.is_available(),
            "can_execute": False,
            "input_allowed": False,
            "executor_connected": False,
            "zone": zone,
            "player_tile": tile,
            "energy": energy,
            "time_of_day": time_of_day,
            "date": date_str,
            "next_step": "await_smapi_executor",
        }
        if target:
            preview["target_tile"] = {"x": target[0], "y": target[1]}
        if draft:
            preview["bridge_command_draft"] = draft
        preview.update(extra)
        return preview
