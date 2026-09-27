"""Thin boundary for core/live phase command routing.

The heavy phase router lives in ``core_phase_commands_impl`` and is imported
only for slash commands.  Normal chat should not parse the large phase command
surface while entering the hot path.
"""

from __future__ import annotations


async def handle_core_phase_command(loop, vts, voice, text: str, text_lower: str | None = None) -> bool:
    text_lower = text_lower or text.lower()
    if not text_lower.startswith("/"):
        return False

    from nana.cli.core_phase_command_index import is_core_phase_command

    if not is_core_phase_command(text_lower):
        return False

    from nana.cli.core_phase_commands_impl import handle_core_phase_command as _handle_core_phase_command

    return await _handle_core_phase_command(loop, vts, voice, text, text_lower)


__all__ = ["handle_core_phase_command"]
