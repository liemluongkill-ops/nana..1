"""Adapter command surfaces kept separate from the broad suggestion registry."""

from __future__ import annotations

STARDEW_STATIC_COMMANDS: set[str] = {
    '/stardew-bridge-status',
    '/stardew-goal-preview',
    '/stardew-help',
    '/stardew-observer-status',
    '/stardew-plan-status',
    '/stardew-status',
}

OSU_STATIC_COMMANDS: set[str] = {
    '/osu-adapter-auto',
    '/osu-adapter-off',
    '/osu-adapter-on',
    '/osu-game-status',
}

__all__ = [
    "STARDEW_STATIC_COMMANDS",
    "OSU_STATIC_COMMANDS",
]
