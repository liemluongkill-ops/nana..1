"""Regression cases for command normalization and routing."""

from __future__ import annotations

COMMAND_NORMALIZATION_CASES: list[tuple[str, str]] = [
    ('//br', '/br'),
    ('/ br', '/br'),
    ('//browser', '/browser'),
    ('/ help', '/help'),
    ('//social-draft-test Nana viết nháp', '/social-draft-test Nana viết nháp'),
    ('/ run python --version', '/run python --version'),
    ('hello /br', 'hello /br'),
]

COMMAND_ROUTE_CASES: list[tuple[str, str, str | None, str | None]] = [
    ('/br', 'known', '/br', None),
    ('//br', 'known', '/br', None),
    ('/dry-run Nana viết nháp reply tweet này', 'known', '/dry-run', None),
    ('/phase7-status/br', 'joined_command', '/phase7-status/br', 'joined_command:/phase7-status+/br'),
    ('/not-a-command', 'unknown', '/not-a-command', None),
    ('50 x 10 bằng bao nhiêu', 'chat', None, None),
]

__all__ = [
    "COMMAND_NORMALIZATION_CASES",
    "COMMAND_ROUTE_CASES",
]
