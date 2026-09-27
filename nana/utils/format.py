"""nana.utils.format — stub module."""
from __future__ import annotations


def shorten_line(text, limit):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
