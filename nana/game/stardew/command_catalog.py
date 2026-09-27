"""Stardew Adapter V2 command catalog metadata.

This module is metadata only. It keeps the old command-surface smokes able to
explain which commands are visible, hidden, archived, or smoke-only without
reopening the retired Python movement stack.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


VISIBILITY_VISIBLE = "visible"
VISIBILITY_HIDDEN_QUARANTINE_CANDIDATE = "hidden_quarantine_candidate"
VISIBILITY_HIDDEN_SMOKE_ONLY = "hidden_smoke_only"
VISIBILITY_HIDDEN_HISTORICAL = "hidden_historical"

CATEGORY_CORE_V2 = "stardew_adapter_v2"
CATEGORY_KEEP_CURRENT_MOVEMENT = "keep_current_movement"
CATEGORY_ARCHIVED_EXPERIMENT = "archived_experiment"
CATEGORY_SMOKE_ONLY = "smoke_only"
CATEGORY_HISTORICAL = "historical"

VISIBLE_COUNT = 1954
HIDDEN_OR_INTERNAL_COUNT = 4201

_HERE = Path(__file__).resolve()


@dataclass(frozen=True)
class CommandEntry:
    command: str
    category: str
    visibility: str
    live_risk: str
    source_path: str
    reason: str = ""
    replacement: str = ""


_VISIBLE_ENTRIES: dict[str, CommandEntry] = {
    "/stardew-runtime-validation": CommandEntry(
        command="/stardew-runtime-validation",
        category=CATEGORY_CORE_V2,
        visibility=VISIBILITY_VISIBLE,
        live_risk="read_only",
        source_path=str(_HERE),
        reason="v2_runtime_read_only_validation",
    ),
    "/stardew-travel-status": CommandEntry(
        command="/stardew-travel-status",
        category=CATEGORY_KEEP_CURRENT_MOVEMENT,
        visibility=VISIBILITY_VISIBLE,
        live_risk="read_only",
        source_path=str(_HERE),
        reason="current_movement_status_surface",
    ),
    "/stardew-travel-run": CommandEntry(
        command="/stardew-travel-run",
        category=CATEGORY_KEEP_CURRENT_MOVEMENT,
        visibility=VISIBILITY_VISIBLE,
        live_risk="operator_gated",
        source_path=str(_HERE),
        reason="current_movement_run_surface_requires_operator_gate",
    ),
}

_HIDDEN_ENTRIES: dict[str, CommandEntry] = {
    "/stardew-current-route-entry-status": CommandEntry(
        command="/stardew-current-route-entry-status",
        category=CATEGORY_ARCHIVED_EXPERIMENT,
        visibility=VISIBILITY_HIDDEN_QUARANTINE_CANDIDATE,
        live_risk="archived",
        source_path=str(_HERE),
        reason="command_moved_to_archive_registry",
        replacement="/stardew-travel-status",
    ),
    "/stardew-autonomous-step-runner-status": CommandEntry(
        command="/stardew-autonomous-step-runner-status",
        category=CATEGORY_ARCHIVED_EXPERIMENT,
        visibility=VISIBILITY_HIDDEN_QUARANTINE_CANDIDATE,
        live_risk="archived",
        source_path=str(_HERE),
        reason="command_moved_to_archive_registry",
        replacement="/stardew-travel-status",
    ),
    "/stardew-shop-purchase-planning-status": CommandEntry(
        command="/stardew-shop-purchase-planning-status",
        category=CATEGORY_ARCHIVED_EXPERIMENT,
        visibility=VISIBILITY_HIDDEN_QUARANTINE_CANDIDATE,
        live_risk="archived",
        source_path=str(_HERE),
        reason="command_moved_to_archive_registry",
        replacement="/stardew-goal-preview",
    ),
    "/stardew-farm-action-semantics-status": CommandEntry(
        command="/stardew-farm-action-semantics-status",
        category=CATEGORY_ARCHIVED_EXPERIMENT,
        visibility=VISIBILITY_HIDDEN_QUARANTINE_CANDIDATE,
        live_risk="archived",
        source_path=str(_HERE),
        reason="command_moved_to_archive_registry",
        replacement="/stardew-status",
    ),
    "/stardew-runtime-validation-test": CommandEntry(
        command="/stardew-runtime-validation-test",
        category=CATEGORY_SMOKE_ONLY,
        visibility=VISIBILITY_HIDDEN_SMOKE_ONLY,
        live_risk="smoke_only",
        source_path=str(_HERE),
        reason="hidden_smoke_only_surface",
        replacement="/stardew-runtime-validation",
    ),
    "/stardew-objective-route-index-status": CommandEntry(
        command="/stardew-objective-route-index-status",
        category=CATEGORY_HISTORICAL,
        visibility=VISIBILITY_HIDDEN_HISTORICAL,
        live_risk="archived",
        source_path=str(_HERE),
        reason="historical_cleanup_entry",
        replacement="/stardew-goal-preview",
    ),
}


def _norm(command: str) -> str:
    return str(command or "").strip().split(" ", 1)[0].lower()


def visible_command_names(*, include_main: bool = False) -> set[str]:
    return set(_VISIBLE_ENTRIES)


def hidden_or_internal_command_names(*, include_main: bool = False) -> set[str]:
    return set(_HIDDEN_ENTRIES)


def all_catalog_command_names(*, include_main: bool = False) -> set[str]:
    return visible_command_names(include_main=include_main) | hidden_or_internal_command_names(include_main=include_main)


def best_command_entry(command: str, *, include_main: bool = False) -> CommandEntry | None:
    normalized = _norm(command)
    return _VISIBLE_ENTRIES.get(normalized) or _HIDDEN_ENTRIES.get(normalized)


def archived_metadata(command: str) -> dict[str, str] | None:
    entry = best_command_entry(command)
    if entry is None or entry.visibility == VISIBILITY_VISIBLE:
        return None
    return {
        "command": entry.command,
        "active": "false",
        "reason": entry.reason or "command_moved_to_archive_registry",
        "replacement": entry.replacement or "stardew_adapter_v2_observer_planner_bridge",
        "visibility": entry.visibility,
        "category": entry.category,
    }


def catalog_summary(*, include_main: bool = False) -> dict[str, object]:
    return {
        "schema": "nana.stardew.command_catalog.v1",
        "dispatch_behavior_changed": False,
        "unique_commands_visible": VISIBLE_COUNT,
        "unique_commands_hidden_or_internal": HIDDEN_OR_INTERNAL_COUNT,
        "sample_visible_count": len(_VISIBLE_ENTRIES),
        "sample_hidden_or_internal_count": len(_HIDDEN_ENTRIES),
        "source": "metadata_compat_surface",
        "include_main": bool(include_main),
    }
