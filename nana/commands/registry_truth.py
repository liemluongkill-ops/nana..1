"""Read-only truth views for the slash command registry.

The registry is intentionally broad: it feeds suggestions, normalization, and
compatibility aliases.  A registered string is not the same thing as a live
handler.  This module keeps that distinction explicit for status commands and
smoke tests without changing dispatch behavior.
"""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from nana.commands.router_manifest import CommandTruth, classify_command_truth


TRUTH_STATUS_ORDER = (
    "live",
    "archived",
    "reserved",
    "known_registry",
    "unknown",
    "chat",
)


def classify_registry_commands(commands: Iterable[str] | None = None) -> dict[str, CommandTruth]:
    """Classify registered commands by current router-truth policy."""

    if commands is None:
        from nana.commands.registry import KNOWN_SLASH_COMMANDS

        commands = KNOWN_SLASH_COMMANDS
    return {
        str(command): classify_command_truth(str(command), known=True)
        for command in sorted(set(commands))
    }


def registry_truth_summary(commands: Iterable[str] | None = None) -> dict:
    rows = classify_registry_commands(commands)
    counts = Counter(truth.status for truth in rows.values())
    examples: dict[str, list[str]] = {status: [] for status in TRUTH_STATUS_ORDER}
    for command, truth in rows.items():
        bucket = examples.setdefault(truth.status, [])
        if len(bucket) < 5:
            bucket.append(command)

    not_current_live = sum(
        counts[status]
        for status in ("archived", "reserved", "known_registry", "unknown")
    )
    return {
        "total": len(rows),
        "counts": {status: counts.get(status, 0) for status in TRUTH_STATUS_ORDER},
        "examples": examples,
        "not_current_live": not_current_live,
        "live": counts.get("live", 0),
    }


def registry_truth_status_lines(commands: Iterable[str] | None = None, *, max_examples: int = 3):
    summary = registry_truth_summary(commands)
    counts = summary["counts"]
    yield "Command Registry Truth Surface"
    yield f"  Total registry strings: {summary['total']}"
    yield f"  Live-hinted: {counts['live']}"
    yield f"  Archived compatibility shells: {counts['archived']}"
    yield f"  Reserved/future aliases: {counts['reserved']}"
    yield f"  Registry-only unclassified strings: {counts['known_registry']}"
    yield f"  Not-current-live strings: {summary['not_current_live']}"
    yield "  Meaning: KNOWN_SLASH_COMMANDS is broad by design; live means static dispatcher evidence, not permission to act."
    for status in ("archived", "reserved", "known_registry"):
        sample = summary["examples"].get(status, [])[:max_examples]
        if sample:
            yield f"  Example {status}: {', '.join(sample)}"
