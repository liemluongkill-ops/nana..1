"""nana.core.format — stub module.

Provides formatting helpers used by social quality checks.
"""
from __future__ import annotations


def shorten_line(text, limit):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
