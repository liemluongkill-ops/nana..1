"""Small Stardew Adapter V2 help surface.

The old phase catalog help page was retired with the Python movement stack.
This helper keeps the help surface importable while showing only safe V2
commands.
"""

from __future__ import annotations

from nana.commands.registry import STARDEW_STATIC_COMMANDS


def emit_stardew_help_lines() -> tuple[str, ...]:
    lines = [
        "[Stardew Adapter V2 Help Surface]",
        "  Python role: observer + planner only",
        "  Executor: SMAPI/C# bridge",
        "  Safety: no Python keyboard, mouse, pathfinding, or movement",
        "  Current commands:",
    ]
    lines.extend(f"    {command}" for command in sorted(STARDEW_STATIC_COMMANDS))
    lines.extend(
        [
            "  Legacy Stardew phase commands are retired.",
            "  Use /stardew-goal-preview <goal> --bridge-dry-run for dry-run bridge checks.",
        ]
    )
    return tuple(lines)


def print_stardew_help_surface(print_fn=print) -> tuple[str, ...]:
    lines = emit_stardew_help_lines()
    for line in lines:
        print_fn(line)
    return lines
