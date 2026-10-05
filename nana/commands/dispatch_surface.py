"""Static read-only view of command literals handled by CLI dispatch modules.

This is a diagnostic helper, not a dispatcher.  It reads the source AST and
extracts slash command literals from dispatch modules so status commands can
tell the difference between "listed in the registry" and "visible in the
dispatcher".
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


_CLI_DIR = Path(__file__).resolve().parents[1] / "cli"
_DISPATCH_SOURCE_PATHS = (
    _CLI_DIR / "handle_text.py",
    _CLI_DIR / "action_diagnostic_commands.py",
    _CLI_DIR / "action_diagnostic_commands_impl.py",
    _CLI_DIR / "adapter_commands.py",
    _CLI_DIR / "autonomy_runtime_commands.py",
    _CLI_DIR / "awareness_commands.py",
    _CLI_DIR / "core_identity_commands.py",
    _CLI_DIR / "core_phase_commands.py",
    _CLI_DIR / "core_phase_commands_impl.py",
    _CLI_DIR / "memory_commands.py",
    _CLI_DIR / "phase_compat_commands.py",
    _CLI_DIR / "phase_compat_commands_impl.py",
    _CLI_DIR / "persona_commands.py",
    _CLI_DIR / "presence_commands.py",
    _CLI_DIR / "process_commands.py",
    _CLI_DIR / "public_stage_commands.py",
    _CLI_DIR / "review_audit_commands.py",
    _CLI_DIR / "stage_runtime_commands.py",
    _CLI_DIR / "starter_commands.py",
    _CLI_DIR / "vts_commands.py",
    _CLI_DIR / "voice_commands.py",
    _CLI_DIR / "web_commands.py",
)


@dataclass(frozen=True)
class DispatchSurface:
    exact: frozenset[str]
    prefixes: frozenset[str]
    sources: tuple[str, ...]


def normalize_dispatch_literal(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text.startswith("/"):
        return ""
    if text.startswith("//"):
        text = "/" + text.lstrip("/")
    if text.startswith("/ "):
        text = "/" + text[2:].lstrip()
    if " " in text:
        text = text.split(" ", 1)[0]
    return text


@lru_cache(maxsize=1)
def static_dispatch_surface() -> DispatchSurface:
    exact: set[str] = set()
    prefixes: set[str] = set()
    sources: list[str] = []

    for source_path in _DISPATCH_SOURCE_PATHS:
        if not source_path.exists():
            continue
        sources.append(str(source_path))
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            literal = normalize_dispatch_literal(node.value)
            if not literal:
                continue
            if literal.endswith("-") or literal.endswith("/"):
                prefixes.add(literal)
            else:
                exact.add(literal)

    return DispatchSurface(
        exact=frozenset(exact),
        prefixes=frozenset(prefixes),
        sources=tuple(sources),
    )


def is_static_dispatch_command(command: str) -> bool:
    cmd = normalize_dispatch_literal(command)
    if not cmd:
        return False
    surface = static_dispatch_surface()
    return cmd in surface.exact or any(cmd.startswith(prefix) for prefix in surface.prefixes)


def dispatch_surface_status_lines():
    surface = static_dispatch_surface()
    yield "Static Dispatch Surface"
    yield f"  Sources: {len(surface.sources)}"
    for source in surface.sources:
        yield f"    - {source}"
    yield f"  Exact slash literals in dispatch sources: {len(surface.exact)}"
    yield f"  Slash prefixes in dispatch sources: {len(surface.prefixes)}"
    yield "  Note: static AST evidence improves truth labels, but runtime gates still decide whether actions can run."
