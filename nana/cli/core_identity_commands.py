"""Core identity/self diagnostic command router.

All commands here are read-only status/preview/test surfaces.  The router does
not call models, TTS, VTS, OBS, Discord, or game input.
"""

from __future__ import annotations

from collections.abc import Iterable

from nana.runtime.core_anchor_recovery import (
    core_anchor_preview_lines,
    core_anchor_status_lines,
)
from nana.runtime.core_drift_monitor import (
    core_drift_preview_lines,
    core_drift_status_lines,
)
from nana.runtime.core_self import (
    core_self_preview_lines,
    core_self_test_lines,
)


def _print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        print(line)


def _rest(text: str, prefix: str) -> str:
    return text[len(prefix):].strip()


def _starts_with_any(text_lower: str, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        if text_lower.startswith(prefix):
            return prefix
    return ""


def handle_core_identity_command(text: str, text_lower: str | None = None) -> bool:
    """Handle core identity/self read-only command surfaces."""

    text_lower = text_lower or text.lower()

    if text_lower in {"/persona-boundary-status", "/public-persona-status"}:
        from nana.core.status_public import print_persona_boundary_status

        print_persona_boundary_status()
        return True

    if text_lower in {"/persona-spine-status", "/core-persona-status", "/spine-status"}:
        from nana.core.status_public import print_persona_spine_status

        print_persona_spine_status()
        return True

    if text_lower in {"/core-self-status", "/self-status", "/nana-self-status"}:
        from nana.core.status_public import print_core_self_status

        print_core_self_status()
        return True

    if text_lower.startswith("/core-self-preview"):
        lane = _rest(text, "/core-self-preview") or "private_owner"
        _print_lines(core_self_preview_lines(lane))
        return True

    if text_lower.startswith("/core-self-test"):
        rest = _rest(text, "/core-self-test")
        if not rest:
            print("  Usage: /core-self-test <prompt>|<reply>")
        else:
            _print_lines(core_self_test_lines(rest))
        return True

    if text_lower in {"/core-drift-status", "/self-drift-status", "/nana-drift-status"}:
        _print_lines(core_drift_status_lines())
        return True

    prefix = _starts_with_any(text_lower, ("/core-drift-preview", "/self-drift-preview"))
    if prefix:
        _print_lines(core_drift_preview_lines(_rest(text, prefix)))
        return True

    if text_lower in {"/core-anchor-status", "/self-anchor-status", "/nana-anchor-status"}:
        _print_lines(core_anchor_status_lines())
        return True

    prefix = _starts_with_any(text_lower, ("/core-anchor-preview", "/self-anchor-preview"))
    if prefix:
        _print_lines(core_anchor_preview_lines(_rest(text, prefix)))
        return True

    return False
