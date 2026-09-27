"""Deterministic guard for Nana's active runtime architecture boundary.

This smoke only imports modules in fresh subprocesses. It never starts Nana or
calls LLM, voice, VTS, Discord, OBS, or game backends.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = ROOT / "nana"
ACTIVE_DIRECTORIES = ("autonomy", "brain", "cli", "commands", "core", "runtime")
LEGACY_MODULES = {"nana.main", "nana.main_cut"}


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _active_python_files():
    yield PACKAGE_ROOT / "__main__.py"
    for directory in ACTIVE_DIRECTORIES:
        yield from (PACKAGE_ROOT / directory).rglob("*.py")


def _imported_modules(path: Path, tree: ast.AST):
    package_parts = path.relative_to(ROOT).with_suffix("").parts[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                keep = max(0, len(package_parts) - node.level + 1)
                prefix = package_parts[:keep]
                suffix = tuple(part for part in (node.module or "").split(".") if part)
                module = ".".join(prefix + suffix)
            else:
                module = node.module or ""
            if module:
                yield module, node.lineno


def test_entrypoint_contract() -> None:
    path = PACKAGE_ROOT / "__main__.py"
    tree = _parse(path)
    imports_cli_main = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level == 0 and node.module == "nana.cli.app":
            imports_cli_main = any(alias.name == "main" for alias in node.names)
    assert imports_cli_main, "nana.__main__ must import main from nana.cli.app"
    print("  entrypoint: nana.__main__ -> nana.cli.app.main")


def test_active_sources_do_not_import_legacy_runtime() -> None:
    violations = []
    for path in _active_python_files():
        tree = _parse(path)
        for module, lineno in _imported_modules(path, tree):
            if module in LEGACY_MODULES or any(
                module.startswith(f"{legacy}.") for legacy in LEGACY_MODULES
            ):
                violations.append(
                    (path.relative_to(PACKAGE_ROOT).as_posix(), lineno, module)
                )
    assert violations == [], violations
    print("  active sources: no nana.main or nana.main_cut imports")


def test_cold_import_has_no_legacy_side_effects() -> None:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(ROOT) + os.pathsep + env.get("PYTHONPATH", ""),
            "NANA_AUTONOMY_LLM_DISABLED": "1",
            "NANA_STARDEW_V2_ADAPTER_ENABLED": "0",
            "NANA_OSU_ADAPTER_ENABLED": "0",
        }
    )
    code = r"""
import importlib
import sys
import threading

before = {thread.ident for thread in threading.enumerate()}
entry = importlib.import_module("nana.__main__")
app = importlib.import_module("nana.cli.app")
handle_text = importlib.import_module("nana.cli.handle_text")
new_threads = [
    thread.name for thread in threading.enumerate() if thread.ident not in before
]
legacy = sorted(name for name in sys.modules if name in {"nana.main", "nana.main_cut"})
phases = sorted(name for name in sys.modules if name.startswith("nana.phases.phase"))
assert entry.main is app.main
assert handle_text.__file__.replace("\\", "/").endswith("nana/cli/handle_text.py")
assert legacy == [], legacy
assert phases == [], phases
assert new_threads == [], new_threads
print("cold-import-clean")
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "cold-import-clean" in result.stdout, result.stdout
    print("  cold import: no legacy modules, phase modules, or new threads")


def main() -> None:
    tests = [
        test_entrypoint_contract,
        test_active_sources_do_not_import_legacy_runtime,
        test_cold_import_has_no_legacy_side_effects,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_core_architecture_guard: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
