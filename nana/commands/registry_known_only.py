"""Registry-only strings not classified as live, archived, or reserved."""

# Generated from registry_data.py split. Keep buckets separate so registry membership is not confused with live routing.

from __future__ import annotations

KNOWN_REGISTRY_ONLY_COMMANDS: set[str] = {
}

__all__ = ["KNOWN_REGISTRY_ONLY_COMMANDS"]
