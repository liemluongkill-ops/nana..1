"""Read-only views over the Stardew Adapter V2 command metadata."""

from __future__ import annotations

from nana.game.stardew.command_catalog import (
    best_command_entry,
    catalog_summary,
    hidden_or_internal_command_names,
    visible_command_names,
)


def command_visibility(command: str) -> str:
    entry = best_command_entry(command)
    if entry is None:
        return "unknown"
    return entry.visibility


def registry_view_summary() -> dict[str, object]:
    summary = catalog_summary(include_main=False)
    return {
        "schema": "nana.stardew.registry_views.v1",
        "dispatch_behavior_changed": False,
        "visible_sample_count": len(visible_command_names(include_main=False)),
        "hidden_sample_count": len(hidden_or_internal_command_names(include_main=False)),
        "catalog": summary,
    }
