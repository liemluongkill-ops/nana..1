# Stardew Adapter V2 - Command Surface
# Minimal command handlers for V2 adapter.
# NO keyboard/mouse/pathfinding. Observer/Planner only.

from nana.game.stardew.observer import StardewObserver
from nana.game.stardew.planner import StardewPlanner
from nana.game.stardew.bridge import StardewBridge


ADAPTER_VERSION = "2.0.2"
ADAPTER_STATE = "observer_bridge_ready"


def get_adapter_info() -> dict:
    """Return basic adapter info."""
    return {
        "version": ADAPTER_VERSION,
        "state": ADAPTER_STATE,
        "python_role": "observer + planner",
        "executor": "SMAPI/C# bridge (file-based observer)",
    }


def stardew_status(print_fn=print) -> str:
    """Handle /stardew-status"""
    info = get_adapter_info()
    lines = [
        "[Stardew Adapter V2]",
        f"  Version: {info['version']}",
        f"  State: {info['state']}",
        f"  Python role: OBSERVER + PLANNER",
        f"  Executor: {info['executor']}",
    ]
    for line in lines:
        print_fn(line)
    return "\n".join(lines)


def stardew_observer_status(print_fn=print) -> str:
    """Handle /stardew-observer-status

    Reads state from nana/game/stardew/data/observer_state.json.
    Shows available fields when state exists.
    """
    observer = StardewObserver()
    state = observer.read()

    available = observer.is_available()
    zone = state.get("zone", "unknown")
    player_tile = state.get("player_tile")
    energy = state.get("player_energy", 0)
    time_of_day = state.get("time_of_day", 0)
    day = state.get("day", 0)
    season = state.get("season", "unknown")
    year = state.get("year", 0)
    ts = state.get("timestamp")
    stale = observer.is_stale()
    age = observer.state_age()

    lines = [
        "[Stardew Observer V2]",
        f"  Available: {str(available).lower()}",
        f"  Zone: {zone}",
        f"  Player tile: {player_tile}",
        f"  Energy: {energy}",
        f"  Time: {time_of_day} ({_format_time(time_of_day)})",
        f"  Date: {season} {day}, Year {year}",
        f"  Stale: {str(stale).lower()}",
        f"  Age: {age:.1f}s",
        f"  Stale after: {observer.stale_after():.0f}s",
        f"  Last read: {ts}",
    ]
    for line in lines:
        print_fn(line)
    return "\n".join(lines)


def stardew_plan_status(print_fn=print) -> str:
    """Handle /stardew-plan-status"""
    observer = StardewObserver()
    planner = StardewPlanner(observer)
    plan = planner.plan()
    observer_state = observer.read()

    lines = ["[Stardew Planner V2]"]
    lines.append(f"  Command: {plan.get('command', 'unknown')}")
    lines.append(f"  Reason: {plan.get('reason', 'unknown')}")
    lines.append(f"  Suggested next: {plan.get('suggested_next', 'n/a')}")
    lines.append(f"  Timestamp: {plan.get('timestamp')}")
    lines.append(f"  Observer available: {str(plan.get('observer_available', False)).lower()}")

    if plan.get("zone"):
        lines.append(f"  Zone: {plan.get('zone')}")
    if plan.get("player_tile"):
        lines.append(f"  Player tile: {plan.get('player_tile')}")
    if plan.get("energy") is not None:
        lines.append(f"  Energy: {plan.get('energy')}")
    if plan.get("time_of_day") is not None:
        lines.append(f"  Time: {plan.get('time_of_day')} ({_format_time(plan.get('time_of_day', 0))})")
    if plan.get("date"):
        lines.append(f"  Date: {plan.get('date')}")

    for line in lines:
        print_fn(line)
    return "\n".join(lines)


def stardew_goal_preview(raw_input: str = "", print_fn=print) -> str:
    """Handle /stardew-goal-preview [goal text] [--bridge-dry-run]

    Parses a freeform goal and returns a read-only preview.
    Does NOT execute anything unless --bridge-dry-run is explicitly given.

    Flags:
        --bridge-dry-run  Write dry-run inbox to command_inbox.json.
                           SMAPI reads and acks it; no game execution occurs.
    """
    import shlex

    # Parse flags from raw input
    parts = raw_input.strip().split(" --")
    goal_text = parts[0].strip()
    flags = [f"--{p.strip()}" for p in parts[1:]]
    bridge_dry_run = "--bridge-dry-run" in flags

    # Parse --executor-approval-token=TOKEN (8H)
    executor_approval_token = None
    for flag in flags:
        if flag.startswith("--executor-approval-token="):
            executor_approval_token = flag.split("=", 1)[1].strip().upper()

    observer = StardewObserver()
    planner = StardewPlanner(observer)
    preview = planner.goal_preview(goal_text)

    lines = ["[Stardew Goal Preview V2]"]
    lines.append(f"  Command: {preview.get('command', 'unknown')}")
    lines.append(f"  Reason: {preview.get('reason', 'unknown')}")
    if goal_text:
        lines.append(f"  Goal: {goal_text}")
    lines.append(f"  Preview intent: {preview.get('preview_intent', 'n/a')}")
    if preview.get("target_tile"):
        lines.append(f"  Target tile: {preview['target_tile']}")
    if preview.get("zone"):
        lines.append(f"  Zone: {preview['zone']}")
    if preview.get("player_tile"):
        lines.append(f"  Current tile: {preview['player_tile']}")
    if preview.get("energy") is not None:
        lines.append(f"  Energy: {preview['energy']}")
    if preview.get("time_of_day") is not None:
        lines.append(f"  Time: {preview['time_of_day']} ({_format_time(preview['time_of_day'])})")
    if preview.get("date"):
        lines.append(f"  Date: {preview['date']}")
    lines.append(f"  Can execute: {str(preview.get('can_execute', False)).lower()}")
    lines.append(f"  Input allowed: {str(preview.get('input_allowed', False)).lower()}")
    lines.append(f"  Executor connected: {str(preview.get('executor_connected', False)).lower()}")
    lines.append(f"  Next step: {preview.get('next_step', 'await_smapi_executor')}")

    # Bridge draft block
    draft = preview.get("bridge_command_draft", {})
    if draft:
        lines.append("")
        lines.append("  [Bridge draft]")
        lines.append(f"    Draft command: {draft.get('command', 'unknown')}")
        if draft.get("map"):
            lines.append(f"    Map: {draft.get('map')}")
        if draft.get("target"):
            lines.append(f"    Target: {draft.get('target')}")
        if draft.get("schema_version"):
            lines.append(f"    Schema version: {draft.get('schema_version')}")
        lines.append(f"    Dry run: {str(draft.get('dry_run', True)).lower()}")
        lines.append(f"    Forwarded: {str(draft.get('forwarded', False)).lower()}")
        lines.append(f"    Can execute: {str(draft.get('can_execute', False)).lower()}")
        lines.append(f"    Reason: {draft.get('reason', 'unknown')}")

    # Dry-run inbox write (only when explicitly requested)
    if bridge_dry_run:
        from nana.game.stardew.bridge import write_inbox, read_ack, inbox_path, ack_path

        draft_reason = draft.get("reason", "goal_preview_unknown") if draft else "unknown"
        inbox_msg = {
            "schema_version": "stardew_bridge_command_v1",
            "protocol": "dry_run_ack_v1",
            "command_id": str(preview.get("timestamp", "")) + "_dryrun",
            "command": draft.get("command", "noop") if draft else "noop",
            "source": "goal_preview",
            "dry_run": True,
            "ack_only": True,
            "forwarded": True,
            "can_execute": False,
            "input_allowed": False,
            "timestamp": preview.get("timestamp", 0),
        }
        if draft and draft.get("map"):
            inbox_msg["map"] = draft["map"]
        if draft and draft.get("target"):
            inbox_msg["target"] = draft["target"]
        if goal_text:
            inbox_msg["goal_text"] = goal_text
        if preview.get("preview_intent"):
            inbox_msg["preview_intent"] = preview["preview_intent"]
        inbox_msg["reason"] = draft_reason

        # STARDEW-V2-8H: executor approval token (from --executor-approval-token flag)
        if executor_approval_token:
            inbox_msg["executor_approval_token"] = executor_approval_token

        try:
            ipath = write_inbox(inbox_msg)
            lines.append("")
            lines.append(f"  [Dry-run inbox written]")
            lines.append(f"    Inbox: {ipath}")
            lines.append(f"    Ack expected at: {ack_path()}")
            lines.append(f"    SMAPI will write ack; no game execution will occur.")

            # Read ack for diagnostics (8H: required_token, approval_status)
            ack = read_ack()
            expected_command_id = inbox_msg["command_id"]
            if ack and ack.get("command_id") == expected_command_id:
                lines.append("")
                lines.append("  [Ack received]")
                lines.append(f"    Command ID: {ack.get('command_id', 'n/a')}")
                lines.append(f"    Status: {ack.get('status', 'n/a')}")
                lines.append(f"    Executed: {str(ack.get('executed', False)).lower()}")
                lines.append(f"    Input allowed: {str(ack.get('input_allowed', False)).lower()}")
                lines.append(f"    Reason: {ack.get('reason', 'n/a')}")
                # 8H: executor approval gate diagnostics
                if ack.get('required_token'):
                    lines.append(f"    Required token: {ack['required_token']}")
                if ack.get('approval_status'):
                    lines.append(f"    Approval status: {ack['approval_status']}")
                if ack.get('executor_armed') is not None:
                    lines.append(f"    Executor armed: {str(ack.get('executor_armed', False)).lower()}")
                validation = ack.get('validation', {})
                if validation:
                    lines.append(f"    Validation: {validation}")
                plan = ack.get('planner_kind') or ack.get('planned_steps')
                if plan:
                    lines.append(f"    Plan: {plan}")
                executor_intent = ack.get('executor_intent', {})
                if executor_intent:
                    lines.append(f"    Executor intent: {executor_intent.get('kind', 'n/a')} ({executor_intent.get('safety_mode', 'n/a')})")
                action_plan = ack.get('executor_action_plan', {})
                if action_plan:
                    action_steps = action_plan.get('steps') or []
                    lines.append(
                        f"    Executor action plan: {action_plan.get('kind', 'n/a')} "
                        f"steps={len(action_steps)} ({action_plan.get('safety_mode', 'n/a')})"
                    )
                elif ack.get('executor_action_plan_ready') is not None:
                    lines.append(f"    Executor action plan ready: {str(ack.get('executor_action_plan_ready', False)).lower()}")
            elif ack:
                lines.append("")
                lines.append("  [Waiting for matching ack]")
                lines.append(f"    Expected command ID: {expected_command_id}")
                lines.append(f"    Latest ack command ID: {ack.get('command_id', 'n/a')}")
                lines.append(f"    Ack path: {ack_path()}")
            else:
                lines.append("")
                lines.append("  [No ack yet]")
                lines.append(f"    Ack path: {ack_path()}")
                lines.append(f"    Run /stardew-bridge-status after SMAPI processes the inbox.")
        except OSError as e:
            lines.append("")
            lines.append(f"  [Dry-run inbox FAILED]")
            lines.append(f"    Error: {e}")

    for line in lines:
        print_fn(line)
    return "\n".join(lines)


def stardew_bridge_status(print_fn=print) -> str:
    """Handle /stardew-bridge-status"""
    bridge = StardewBridge()
    health = bridge.health()
    lines = [
        "[Stardew Bridge V2]",
        f"  Connected: {str(health['connected']).lower()}",
        f"  Source: {health['source']}",
        "  Executor: read_only_observer",
        f"  Bridge executor: {health['executor']}",
        f"  Executor connected: {str(health['executor_connected']).lower()}",
        f"  Input allowed: {str(health['input_allowed']).lower()}",
        f"  Can execute commands: {str(health['can_execute']).lower()}",
        f"  Observer state file: {health['state_path']}",
        f"  Observer available: {str(health['observer_available']).lower()}",
        f"  State stale: {str(health['state_stale']).lower()}",
        f"  Last write age: {_format_age(health['last_write_age_sec'])}",
        f"  Stale after: {health['stale_after_sec']:.0f}s",
        f"  Last read: {health['last_read']}",
        f"  Zone: {health['zone']}",
        # Inbox / ack fields (STARDEW-V2-8D)
        f"  Inbox path: {health.get('inbox_path', 'n/a')}",
        f"  Ack path: {health.get('ack_path', 'n/a')}",
        f"  Last ack command ID: {health.get('last_ack_command_id') or 'none'}",
        f"  Last ack status: {health.get('last_ack_status') or 'none'}",
        f"  Last ack reason: {health.get('last_ack_reason') or 'none'}",
        f"  Last ack planned steps: {health.get('last_ack_planned_steps') if health.get('last_ack_planned_steps') is not None else 'none'}",
        f"  Last ack executor armed: {str(health.get('last_ack_executor_armed') or False).lower()}",
        f"  Last ack approval status: {health.get('last_ack_approval_status') or 'none'}",
        f"  Last ack required token: {health.get('last_ack_required_token') or 'none'}",
        f"  Last ack action plan ready: {str(health.get('last_ack_action_plan_ready') or False).lower()}",
        f"  Last ack action plan steps: {health.get('last_ack_action_plan_steps') if health.get('last_ack_action_plan_steps') is not None else 'none'}",
        f"  Last ack action plan safety: {health.get('last_ack_action_plan_safety') or 'none'}",
        f"  Last ack action plan executed steps: {health.get('last_ack_action_plan_executed_steps') if health.get('last_ack_action_plan_executed_steps') is not None else 'none'}",
        f"  Last ack action plan remaining steps: {health.get('last_ack_action_plan_remaining_steps') if health.get('last_ack_action_plan_remaining_steps') is not None else 'none'}",
        # 8J: live execution fields
        f"  Last ack executed: {str(health.get('last_ack_executed') or False).lower()}",
        f"  Last ack input allowed: {str(health.get('last_ack_input_allowed') or False).lower()}",
        f"  Last ack live approval: {health.get('last_ack_live_approval') or 'none'}",
        f"  Last ack post map: {health.get('last_ack_post_map') or 'none'}",
        f"  Last ack post tile: {health.get('last_ack_post_tile') or 'none'}",
        f"  Last ack verified: {str(health.get('last_ack_verified') or False).lower()}",
        f"  Last ack age: {_format_age(health.get('last_ack_age_sec'))}",
    ]

    route_contract = health.get('last_ack_route_contract')
    if route_contract:
        lines.append("")
        lines.append("  [Route]")
        lines.append(
            f"    Destination: {route_contract.get('destination_tile') or 'none'} | "
            f"interaction target: {route_contract.get('interaction_target_tile') or 'none'}"
        )
        lines.append(
            f"    Found: {str(route_contract.get('route_found') or False).lower()} | "
            f"target passable: {str(route_contract.get('target_passable') or False).lower()} | "
            f"stand-near: {str(route_contract.get('uses_adjacent_stand_tile') or False).lower()} | "
            f"steps={route_contract.get('planned_steps') if route_contract.get('planned_steps') is not None else 'none'} | "
            f"chunks={route_contract.get('planned_chunks') if route_contract.get('planned_chunks') is not None else 'none'}"
        )
        lines.append(
            f"    Safety: {route_contract.get('safety_mode') or 'unknown'} | "
            f"mode={route_contract.get('movement_mode') or 'unknown'} | "
            f"cap={route_contract.get('executor_cap_steps') if route_contract.get('executor_cap_steps') is not None else 'none'}"
        )
        if route_contract.get('recommended_stand_tile') or route_contract.get('adjacent_stand_tile_count'):
            lines.append(
                f"    Recommended stand tile: {route_contract.get('recommended_stand_tile') or 'none'} | "
                f"adjacent options={route_contract.get('adjacent_stand_tile_count') or 0}"
            )
        transition_contract = route_contract.get('transition_contract')
        if transition_contract:
            source_map = transition_contract.get('source_map') or 'unknown'
            target_map = transition_contract.get('expected_target_map') or 'unknown'
            lines.append("")
            lines.append("  [Transition preview]")
            lines.append(
                f"    {source_map} approach {transition_contract.get('approach_tile') or 'none'} "
                f"-> transition {transition_contract.get('transition_tile') or 'none'} "
                f"-> {target_map} {transition_contract.get('expected_target_tile') or 'none'}"
            )
            lines.append(
                f"    Stage: {transition_contract.get('execution_stage') or 'unknown'} | "
                f"allowed: {str(transition_contract.get('live_transition_execution_allowed') or False).lower()} | "
                f"reobserve: {str(transition_contract.get('requires_reobserve_after_transition') or False).lower()}"
            )

    # 8J: Show events if present
    events = health.get('last_ack_events')
    if events:
        lines.append("")
        lines.append("  [Events]")
        for event in events:
            line = (
                f"    - {event.get('event', 'unknown')}: "
                f"pre={event.get('pre_tile')} target={event.get('target_tile')} "
                f"step_target={event.get('step_target_tile')} "
                f"post={event.get('post_tile')} verified={event.get('verified')}"
            )
            if event.get("post_pixel") is not None or event.get("target_pixel") is not None:
                line += (
                    f" pixels pre={event.get('pre_pixel')} "
                    f"target={event.get('target_pixel')} post={event.get('post_pixel')} "
                    f"delta={event.get('pixel_delta')}"
                )
            lines.append(line)

    passability = health.get('last_ack_passability_diagnostic')
    if passability:
        blocked_tile = passability.get("blocked_tile")
        lines.append("")
        lines.append("  [Passability]")
        lines.append(
            f"    Blocked tile: {blocked_tile or 'unknown'} | "
            f"kind={passability.get('blocked_kind') or 'unknown'} | "
            f"reason={passability.get('reason') or 'unknown'}"
        )
        lines.append(
            f"    Action: {passability.get('recommended_action') or 'none'} | "
            f"tool={passability.get('recommended_tool') or 'none'} | "
            f"source={passability.get('source_type') or 'unknown'}"
        )

    for line in lines:
        print_fn(line)
    return "\n".join(lines)


def stardew_help(print_fn=print) -> str:
    """Handle /stardew-help"""
    lines = [
        "[Stardew Adapter V2 Help]",
        "",
        "Stardew Adapter V2 is active.",
        "",
        "Python role: OBSERVER + PLANNER only.",
        "  - Reads game state from file (read-only).",
        "  - Decides next action (returns noop by default).",
        "  - Does NOT send keyboard/mouse input.",
        "  - Does NOT do pathfinding.",
        "  - Does NOT move the character.",
        "",
        "Observer reads state from:",
        "  nana/game/stardew/data/observer_state.json",
        "",
        "Available V2 commands:",
        "  /stardew-status              - Adapter info",
        "  /stardew-observer-status    - Read current game state",
        "  /stardew-plan-status        - Current planner decision",
        "  /stardew-goal-preview       - Preview a goal (read-only)",
        "  /stardew-bridge-status      - Bridge/executor state",
        "  /stardew-help               - This help",
        "",
        "Goal preview examples:",
        "  /stardew-goal-preview đi tới 70 18",
        "  /stardew-goal-preview water crops",
        "  /stardew-goal-preview inspect_state",
        "",
        "Legacy commands (/stardew-*, /pN) are retired.",
        "  See NANA_STARDEW_ADAPTER_V2_SPEC.md",
    ]
    for line in lines:
        print_fn(line)
    return "\n".join(lines)


def _format_time(time_of_day: int) -> str:
    """Convert Stardew time integer to HH:MM string."""
    if not isinstance(time_of_day, int) or time_of_day < 0:
        return "??:??"
    hours = time_of_day // 100
    minutes = time_of_day % 100
    if hours > 23:
        hours = 23
    if minutes > 59:
        minutes = 59
    return f"{hours:02d}:{minutes:02d}"


def _format_age(age_seconds) -> str:
    """Format optional age seconds for command output."""
    if age_seconds is None:
        return "unknown"
    return f"{age_seconds:.1f}s"
