"""Lazy export surface for ``nana.phases``.

This module indexes phase files from source so importing ``nana.phases`` does
not eagerly import every extracted phase module.  The real phase module is only
loaded when a command calls one of the exported functions.
"""
from __future__ import annotations

import ast
import importlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


PACKAGE = "nana.phases"
PHASE_DIR = Path(__file__).resolve().parent

PHASE_MODULE_ORDER = (
    "phase81",
    "phase82",
    "phase83",
    "phase84",
    "phase85",
    "phase10",
    "phase11",
    "phase12",
    "phase13",
    "phase14",
    "phase15",
    "phase16",
    "phase17",
    "phase18",
    "phase19",
    "phase20",
    "phase21",
    "phase22",
    "phase23",
    "phase24",
    "phase25",
    "phase7",
    "phase8",
    "phase9",
    "phase_stubs",
)


@dataclass(frozen=True)
class PhaseExport:
    name: str
    module: str
    kind: str


class LazyPhaseCallable:
    """Callable proxy that imports the owning phase module on first use."""

    def __init__(self, export: PhaseExport):
        self._export = export
        self.__name__ = export.name
        self.__qualname__ = export.name
        self.__module__ = PACKAGE
        self.__doc__ = f"Lazy phase export for {export.module}.{export.name}."

    def _target(self) -> Any:
        module = importlib.import_module(f"{PACKAGE}.{self._export.module}")
        return getattr(module, self._export.name)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self._target()(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<lazy phase export {self._export.module}.{self._export.name}>"


def _assignment_target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for item in target.elts:
            names.update(_assignment_target_names(item))
        return names
    return set()


def _module_exports(module_name: str) -> dict[str, PhaseExport]:
    path = PHASE_DIR / f"{module_name}.py"
    if not path.exists():
        return {}
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    exports: dict[str, PhaseExport] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                kind = "callable" if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "value"
                exports[node.name] = PhaseExport(node.name, module_name, kind)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for name in _assignment_target_names(target):
                    if not name.startswith("_"):
                        exports[name] = PhaseExport(name, module_name, "value")
        elif isinstance(node, ast.AnnAssign):
            for name in _assignment_target_names(node.target):
                if not name.startswith("_"):
                    exports[name] = PhaseExport(name, module_name, "value")
    return exports


@lru_cache(maxsize=1)
def phase_export_index() -> dict[str, PhaseExport]:
    exports: dict[str, PhaseExport] = {}
    for module_name in PHASE_MODULE_ORDER:
        exports.update(_module_exports(module_name))
    return exports


@lru_cache(maxsize=1)
def phase_submodule_names() -> set[str]:
    names = set(PHASE_MODULE_ORDER)
    for path in PHASE_DIR.glob("phase*.py"):
        names.add(path.stem)
    return names


def resolve_phase_symbol(name: str) -> Any:
    if name in phase_submodule_names():
        return importlib.import_module(f"{PACKAGE}.{name}")

    export = phase_export_index().get(name)
    if export is None:
        raise AttributeError(f"module {PACKAGE!r} has no attribute {name!r}")

    if export.kind == "callable":
        return LazyPhaseCallable(export)

    module = importlib.import_module(f"{PACKAGE}.{export.module}")
    return getattr(module, name)


def lazy_phase_names() -> tuple[str, ...]:
    names = set(phase_export_index())
    names.update(phase_submodule_names())
    return tuple(sorted(names))


def phase_surface_status_lines() -> list[str]:
    exports = phase_export_index()
    modules = sorted({export.module for export in exports.values()})
    return [
        "Phase Lazy Surface",
        f"  Exported names: {len(exports)}",
        f"  Source modules: {len(modules)}",
        "  Mode: source-indexed lazy import",
        "  Meaning: phase modules load when their exported function is called.",
    ]
