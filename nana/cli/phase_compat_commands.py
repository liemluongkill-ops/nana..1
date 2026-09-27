"""Thin boundary for legacy phase/voice/stream compatibility routing."""

from __future__ import annotations


async def handle_phase_compat_command(loop, vts, voice, text: str, text_lower: str | None = None) -> bool:
    text_lower = text_lower or text.lower()
    if not text_lower.startswith("/"):
        return False

    from nana.cli.phase_compat_command_index import is_phase_compat_command

    if not is_phase_compat_command(text_lower):
        return False

    from nana.cli.phase_compat_commands_impl import handle_phase_compat_command as _handle_phase_compat_command

    return await _handle_phase_compat_command(loop, vts, voice, text, text_lower)


__all__ = ["handle_phase_compat_command"]
