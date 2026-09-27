"""Memory command router for Nana's CLI dispatcher."""

from __future__ import annotations

import json
import os
from pathlib import Path
from collections.abc import Iterable

from nana.memory import (
    memory_cancel_action,
    memory_compact_plan_report,
    memory_compact_preview_report,
    memory_confirm_action,
    memory_drop_preview_report,
    memory_filter_preview_report,
    memory_health_report,
    memory_keep_report,
    memory_labels_report,
    memory_pending_action_report,
    memory_review_report,
    memory_status_report,
    memory_unkeep_report,
)
from nana.runtime.memory_spine import get_memory_spine


def _print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        print(line)


def _arg_after_space(text: str, default: str = "") -> str:
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else default


def handle_memory_command(text: str, text_lower: str | None = None) -> bool:
    """Handle /memory-* command surfaces."""

    text_lower = text_lower or text.lower()

    if text_lower in {"/memory-status"}:
        spine = get_memory_spine()
        _print_lines(spine.format_status_report())
        return True

    if text_lower in {"/memory-legacy-status"}:
        _print_lines(memory_status_report())
        return True

    if text_lower in {"/memory-health"}:
        _print_lines(memory_health_report())
        return True

    if text_lower.startswith("/memory-filter-test"):
        sample = _arg_after_space(text)
        if not sample:
            print("🧠 Memory Filter Test")
            print("  Missing: /memory-filter-test <text>")
            return True
        _print_lines(memory_filter_preview_report(sample))
        return True

    if text_lower in {"/memory-review"}:
        _print_lines(memory_review_report())
        return True

    if text_lower == "/memory-export":
        spine = get_memory_spine()
        export = spine.export_memory()
        print("🧠 CORE-MEMORY-1 Export")
        print(f"  Phase: {export['phase']}")
        print(f"  Total items: {export['total_items']}")
        print(f"  Session turn: {export['turn_counter']}")
        print(f"  Session ID: {export['session_id']}")
        export_path = str(Path(__file__).resolve().parents[1] / "data" / "memory_export.json")
        try:
            os.makedirs(os.path.dirname(export_path), exist_ok=True)
            with open(export_path, "w", encoding="utf-8") as f:
                json.dump(export, f, ensure_ascii=False, indent=2)
            print(f"  Full export: {export_path}")
        except Exception as exc:
            print(f"  Export error: {exc}")
        return True

    if text_lower.startswith("/memory-search"):
        keyword = _arg_after_space(text)
        if not keyword:
            print("🧠 Memory Search")
            print("  Usage: /memory-search <keyword>")
            print("  Example: /memory-search python")
            return True
        spine = get_memory_spine()
        _print_lines(spine.format_search_report(keyword))
        return True

    if text_lower in {"/memory-core"}:
        spine = get_memory_spine()
        _print_lines(spine.format_status_report())
        return True

    if text_lower in {"/memory-labels", "/memory-label"}:
        _print_lines(memory_labels_report())
        return True

    if text_lower in {"/memory-compact-plan"}:
        _print_lines(memory_compact_plan_report())
        return True

    if text_lower in {"/memory-compact-preview", "/memory-preview"}:
        _print_lines(memory_compact_preview_report())
        return True

    if text_lower in {"/memory-action", "/memory-action-plan"}:
        _print_lines(memory_pending_action_report())
        return True

    if text_lower.startswith("/memory-keep"):
        index = _arg_after_space(text)
        if not index:
            print("🧠 Memory Keep")
            print("  Missing: /memory-keep <index>")
            return True
        _print_lines(memory_keep_report(index))
        return True

    if text_lower.startswith("/memory-unkeep"):
        index = _arg_after_space(text)
        if not index:
            print("🧠 Memory Unkeep")
            print("  Missing: /memory-unkeep <index>")
            return True
        _print_lines(memory_unkeep_report(index))
        return True

    if text_lower.startswith("/memory-drop-preview"):
        index = _arg_after_space(text)
        if not index:
            print("🧠 Memory Drop Preview")
            print("  Missing: /memory-drop-preview <index>")
            return True
        _print_lines(memory_drop_preview_report(index))
        return True

    if text_lower.startswith("/memory-confirm"):
        action_id = _arg_after_space(text)
        if not action_id:
            _print_lines(memory_pending_action_report())
            print("  Missing: /memory-confirm <id>")
            return True
        _print_lines(memory_confirm_action(action_id))
        return True

    if text_lower.startswith("/memory-cancel"):
        action_id = _arg_after_space(text) or None
        _print_lines(memory_cancel_action(action_id))
        return True

    return False
