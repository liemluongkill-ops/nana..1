"""Deterministic smoke for CORE-CAPABILITY-BOUNDARY-1.

No live Nana runtime, adapter, external backend, or game input is used.
"""

from __future__ import annotations

import ast
from contextlib import contextmanager, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = ROOT / "nana"
ACTIVE_CORE_DIRS = ("core", "runtime", "cli", "commands", "autonomy", "brain")
CAPABILITIES_PATH = "runtime/capabilities.py"
GAME_MODULE_PREFIX = "nana.game"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _active_core_python_files():
    for directory in ACTIVE_CORE_DIRS:
        yield from sorted((PACKAGE_ROOT / directory).rglob("*.py"))


def _is_game_module(name: str) -> bool:
    return name == GAME_MODULE_PREFIX or name.startswith(f"{GAME_MODULE_PREFIX}.")


def _package_name(path: Path) -> str:
    parts = path.relative_to(ROOT).with_suffix("").parts
    if path.name == "__init__.py":
        return ".".join(parts[:-1])
    return ".".join(parts[:-1])


def _import_targets(node: ast.AST, package: str):
    if isinstance(node, ast.Import):
        yield from (alias.name for alias in node.names)
        return
    if not isinstance(node, ast.ImportFrom):
        return

    if node.level:
        relative_name = "." * node.level + (node.module or "")
        base = importlib.util.resolve_name(relative_name, package)
    else:
        base = node.module or ""

    if base:
        yield base
    for alias in node.names:
        if alias.name != "*":
            yield f"{base}.{alias.name}" if base else alias.name


def _string_constants(tree: ast.AST) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            constants[node.target.id] = node.value.value
    return constants


def _dynamic_import_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    functions = {"__import__"}
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    functions.add(alias.asname or alias.name)
    return functions, modules


def _dynamic_import_target(node: ast.Call, constants: dict[str, str], functions: set[str], modules: set[str]):
    function = node.func
    is_loader = isinstance(function, ast.Name) and function.id in functions
    if isinstance(function, ast.Attribute) and function.attr == "import_module":
        is_loader = isinstance(function.value, ast.Name) and function.value.id in modules
    if not is_loader or not node.args:
        return None

    argument = node.args[0]
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    if isinstance(argument, ast.Name):
        return constants.get(argument.id)
    return None


def test_active_core_has_no_direct_game_imports() -> None:
    violations: list[tuple[str, int, str]] = []
    scanned = 0
    for path in _active_core_python_files():
        scanned += 1
        tree = _parse(path)
        package = _package_name(path)
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for target in _import_targets(node, package):
                    if _is_game_module(target):
                        violations.append((relative, node.lineno, target))

    assert violations == [], violations
    print(f"  direct nana.game imports: 0 across {scanned} active-core files")


def test_dynamic_game_loading_is_capabilities_owned() -> None:
    loads: list[tuple[str, int, str]] = []
    game_literals: list[tuple[str, int, str]] = []
    for path in _active_core_python_files():
        tree = _parse(path)
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        constants = _string_constants(tree)
        functions, modules = _dynamic_import_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and _is_game_module(node.value):
                game_literals.append((relative, node.lineno, node.value))
            if isinstance(node, ast.Call):
                target = _dynamic_import_target(node, constants, functions, modules)
                if target and _is_game_module(target):
                    loads.append((relative, node.lineno, target))

    assert loads, "no dynamic nana.game loading found"
    assert {path for path, _, _ in loads} == {CAPABILITIES_PATH}, loads
    assert {path for path, _, _ in game_literals} == {CAPABILITIES_PATH}, game_literals
    expected_targets = {
        "nana.game.stardew.registry",
        "nana.game.osu.registry",
        "nana.game.stardew.commands",
        "nana.game.osu.executor_preflight",
    }
    assert expected_targets <= {target for _, _, target in loads}, loads
    print(f"  dynamic nana.game loads: {len(loads)}, owner: {CAPABILITIES_PATH}")


def test_disabled_cold_core_imports_load_no_game_modules() -> None:
    env = os.environ.copy()
    env.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "NANA_STARDEW_ADAPTER_ENABLED": "0",
            "NANA_STARDEW_ADAPTER_AUTO_ZONE_ENABLED": "0",
            "NANA_OSU_ADAPTER_ENABLED": "0",
            "NANA_OSU_ADAPTER_AUTO_ZONE_ENABLED": "0",
        }
    )
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    code = """
import importlib
import sys

for module_name in (
    "nana",
    "nana.cli",
    "nana.commands.dynamic",
    "nana.runtime.capabilities",
    "nana.runtime.stream_presence_readiness",
):
    importlib.import_module(module_name)

loaded = sorted(
    name for name in sys.modules
    if name == "nana.game" or name.startswith("nana.game.")
)
assert loaded == [], loaded
print("loaded_nana_game_modules=0")
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
    assert "loaded_nana_game_modules=0" in result.stdout, result.stdout
    print("  disabled cold imports: loaded nana.game modules = 0")


@contextmanager
def _disabled_adapters():
    from nana.runtime import capabilities

    disabled = {
        capabilities.STARDEW_ADAPTER_ENV: "0",
        capabilities.STARDEW_ADAPTER_AUTO_ZONE_ENV: "0",
        capabilities.OSU_ADAPTER_ENV: "0",
        capabilities.OSU_ADAPTER_AUTO_ZONE_ENV: "0",
    }
    with (
        patch.dict(os.environ, disabled, clear=False),
        patch.object(capabilities, "_STARDEW_RUNTIME_OVERRIDE", None),
        patch.object(capabilities, "_OSU_RUNTIME_OVERRIDE", None),
    ):
        capabilities.stardew_adapter_mode.cache_clear()
        capabilities.osu_adapter_mode.cache_clear()
        try:
            assert capabilities.stardew_adapter_enabled() is False
            assert capabilities.stardew_adapter_auto_zone_enabled() is False
            assert capabilities.osu_adapter_enabled() is False
            assert capabilities.osu_adapter_auto_zone_enabled() is False
            yield capabilities
        finally:
            capabilities.stardew_adapter_mode.cache_clear()
            capabilities.osu_adapter_mode.cache_clear()


def test_wrappers_forward_while_adapters_are_disabled() -> None:
    stardew_module = ModuleType("nana.game.stardew.commands")
    osu_module = ModuleType("nana.game.osu.executor_preflight")
    stardew_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    osu_calls: list[tuple[str, dict[str, object]]] = []
    stardew_value = object()
    osu_value = object()

    def fake_stardew_handler(*args, **kwargs):
        stardew_calls.append((args, kwargs))
        return stardew_value

    def fake_osu_builder(text="", **kwargs):
        osu_calls.append((text, kwargs))
        return osu_value

    stardew_module.fake_handler = fake_stardew_handler
    osu_module.build_executor_preflight_payload = fake_osu_builder

    with _disabled_adapters() as capabilities:
        with patch.dict(
            sys.modules,
            {
                stardew_module.__name__: stardew_module,
                osu_module.__name__: osu_module,
            },
        ):
            stardew_result = capabilities.invoke_stardew_v2_command(
                "fake_handler", "goal text", 7, dry_run=True
            )
            osu_result = capabilities.build_osu_executor_preflight_payload(
                "executor text", token="review-only", attempts=2
            )

    assert stardew_result is stardew_value
    assert stardew_calls == [(('goal text', 7), {"dry_run": True})]
    assert osu_result is osu_value
    assert osu_calls == [("executor text", {"token": "review-only", "attempts": 2})]
    print("  disabled wrappers: args/kwargs and return values forwarded exactly")


def test_disabled_stardew_command_does_not_invoke_wrapper() -> None:
    from nana.commands import dynamic

    with _disabled_adapters():
        output = io.StringIO()
        with patch.object(
            dynamic,
            "invoke_stardew_v2_command",
            side_effect=AssertionError("disabled path invoked wrapper"),
        ) as invoke:
            with redirect_stdout(output):
                handled = dynamic.handle_stardew_v2_command("/stardew-goal-preview water crops")

    assert handled is True
    assert invoke.call_count == 0
    assert output.getvalue() == "Stardew adapter inactive.\n"
    print("  disabled Stardew command: outer gate handled without wrapper invocation")


def main() -> None:
    tests = [
        test_active_core_has_no_direct_game_imports,
        test_dynamic_game_loading_is_capabilities_owned,
        test_disabled_cold_core_imports_load_no_game_modules,
        test_wrappers_forward_while_adapters_are_disabled,
        test_disabled_stardew_command_does_not_invoke_wrapper,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_core_capability_boundary: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
