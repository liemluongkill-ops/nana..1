"""Thin boundary for action/admin/diagnostic command routing."""

from __future__ import annotations


async def handle_action_diagnostic_command(loop, text: str, text_lower: str | None = None) -> bool:
    text_lower = text_lower or text.lower()

    from nana.cli.action_diagnostic_command_index import is_action_diagnostic_command

    if not is_action_diagnostic_command(text_lower):
        return False

    from nana.cli.action_diagnostic_commands_impl import (
        handle_action_diagnostic_command as _handle_action_diagnostic_command,
    )

    return await _handle_action_diagnostic_command(loop, text, text_lower)


__all__ = ["handle_action_diagnostic_command"]
