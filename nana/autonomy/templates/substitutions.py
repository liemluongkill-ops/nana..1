"""Template substitution helpers.

A line may include simple placeholders wrapped in {curly_braces}.
v1 only supports a fixed set; unknown placeholders stay as-is so
the smoke test can flag them.

Placeholders:
    {time_of_day}     "sáng" / "trưa" / "chiều" / "tối" / "khuya"
    {mood_word}       short Vietnamese mood adjective, picked from
                      the mood vector
    {recent_moment}   short reference to a recent chat moment
                      (stubbed in A3, real in A6)

The substitution engine never blocks, never calls the LLM, and never
reaches the network.
"""

from __future__ import annotations

import time
from typing import Optional


_TIME_BUCKETS = [
    (5, 11, "sáng"),
    (11, 14, "trưa"),
    (14, 18, "chiều"),
    (18, 22, "tối"),
    (22, 24, "khuya"),
    (0, 5, "khuya"),
]


_MOOD_WORDS = {
    # Map a mood score (0..1) to a single soft Vietnamese adjective.
    # We use a 3-bucket system in v1 to avoid over-engineering.
    "low":      ["buồn buồn", "lặng lẽ", "tĩnh lặng"],
    "neutral":  ["bình thường", "êm đềm", "nhẹ nhàng"],
    "high":     ["ấm áp", "vui vẻ", "thương thương"],
}


def time_of_day(now: Optional[float] = None) -> str:
    """Return a Vietnamese time-of-day bucket."""
    if now is None:
        now = time.time()
    hour = time.localtime(now).tm_hour
    for lo, hi, label in _TIME_BUCKETS:
        if lo <= hour < hi:
            return label
    return "khuya"


def mood_word(score: float) -> str:
    """Map a mood score in [0, 1] to a single short Vietnamese word."""
    if score < 0.33:
        bucket = "low"
    elif score < 0.66:
        bucket = "neutral"
    else:
        bucket = "high"
    # Deterministic pick: index from score.
    options = _MOOD_WORDS[bucket]
    return options[int(score * 1000) % len(options)]


def recent_moment() -> str:
    """Stub for A3. Returns a fixed phrase; real impl comes in A6."""
    return "lúc nãy mình nói chuyện"


def substitute(text: str, *, mood_score: float = 0.5) -> str:
    """Apply v1 substitutions to a single line."""
    return (
        text
        .replace("{time_of_day}", time_of_day())
        .replace("{mood_word}", mood_word(mood_score))
        .replace("{recent_moment}", recent_moment())
    )


def find_unknown_placeholders(text: str) -> list:
    """Return a list of {xxx} tokens that are not in the v1 set."""
    import re
    known = {"{time_of_day}", "{mood_word}", "{recent_moment}"}
    found = re.findall(r"\{[a-z_]+\}", text)
    return [f for f in found if f not in known]
