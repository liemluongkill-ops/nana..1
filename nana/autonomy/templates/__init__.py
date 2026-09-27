"""Template line pool (Phase A3)."""

from nana.autonomy.templates import idle_lines, observer_lines, host_lines
from nana.autonomy.templates.substitutions import (
    substitute,
    find_unknown_placeholders,
    time_of_day,
    mood_word,
)


__all__ = [
    "idle_lines",
    "observer_lines",
    "host_lines",
    "substitute",
    "find_unknown_placeholders",
    "time_of_day",
    "mood_word",
]
