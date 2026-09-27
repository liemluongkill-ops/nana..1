"""Deterministic smoke for CORE-RUNTIME-OWNERSHIP-1A.

This smoke never starts Nana or calls external backends. Thread checks patch
AutonomyLoop._run with a local Event wait so no autonomy tick can execute.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = ROOT / "nana"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


EXCLUDED_RUNTIME_PARTS = {
    "archive",
    "archives",
    "backup",
    "backups",
    "game",
    "games",
    "legacy",
    "maintenance",
    "phases",
    "scripts",
    "tests",
    "tools",
}
EXCLUDED_RUNTIME_FILES = {"main.py", "main_cut.py"}


def _active_runtime_python_files():
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative = path.relative_to(PACKAGE_ROOT)
        lowered_parts = {part.lower() for part in relative.parts[:-1]}
        if lowered_parts & EXCLUDED_RUNTIME_PARTS:
            continue
        if path.name.lower() in EXCLUDED_RUNTIME_FILES:
            continue
        if path.name.lower().startswith("smoke_"):
            continue
        yield path


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def _is_autonomy_loop_constructor(node: ast.Call) -> bool:
    function = node.func
    if isinstance(function, ast.Name):
        return function.id == "AutonomyLoop"
    return isinstance(function, ast.Attribute) and function.attr == "AutonomyLoop"


def test_active_runtime_sources() -> None:
    constructors = []
    setters = []
    state_sources = []

    for path in _active_runtime_python_files():
        tree = _parse(path)
        relative = path.relative_to(PACKAGE_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_autonomy_loop_constructor(node):
                constructors.append((relative, node.lineno))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "set_runtime_turn_state":
                    setters.append((relative, node.lineno))
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id == "RUNTIME_TURN_STATE" for target in targets):
                    state_sources.append((relative, node.lineno))

    assert len(constructors) == 1, constructors
    assert constructors[0][0] == "autonomy/loop.py", constructors
    assert len(setters) == 1, setters
    assert setters[0][0] == "cli/globals.py", setters
    assert len(state_sources) == 1, state_sources
    assert state_sources[0][0] == "cli/globals.py", state_sources
    print(f"  construction sites: {constructors}")
    print(f"  turn-state setter: {setters}")
    print(f"  turn-state source: {state_sources}")


def test_direct_identity() -> None:
    app = importlib.import_module("nana.cli.app")
    cli_globals = importlib.import_module("nana.cli.globals")
    package = importlib.import_module("nana.autonomy")
    module = importlib.import_module("nana.autonomy.loop")

    assert app.AUTONOMY_LOOP is cli_globals.AUTONOMY_LOOP is package.AUTONOMY_LOOP is module.AUTONOMY_LOOP
    assert app.AUTONOMY_EXPRESS is cli_globals.AUTONOMY_EXPRESS is module.AUTONOMY_EXPRESS
    assert module.AUTONOMY_EXPRESS is module.AUTONOMY_LOOP.express
    print("  direct identity: app == cli.globals == autonomy package == loop module")


def test_import_order_subprocesses() -> None:
    orders = [
        ("nana.autonomy.loop", "nana.cli.globals", "nana.cli.app"),
        ("nana.cli.app", "nana.autonomy.loop", "nana.cli.globals"),
        ("nana.cli.globals", "nana.cli.app", "nana.autonomy.loop"),
    ]
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    for order in orders:
        code = f"""
import importlib
for name in {order!r}:
    importlib.import_module(name)
app = importlib.import_module('nana.cli.app')
cli_globals = importlib.import_module('nana.cli.globals')
package = importlib.import_module('nana.autonomy')
module = importlib.import_module('nana.autonomy.loop')
assert app.AUTONOMY_LOOP is cli_globals.AUTONOMY_LOOP is package.AUTONOMY_LOOP is module.AUTONOMY_LOOP
assert app.AUTONOMY_EXPRESS is cli_globals.AUTONOMY_EXPRESS is module.AUTONOMY_EXPRESS
print('same-instance')
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
        assert result.returncode == 0, (
            order,
            result.stdout,
            result.stderr,
        )
        assert "same-instance" in result.stdout, (order, result.stdout)
        print(f"  import order: {' -> '.join(order)}")


def test_start_is_idempotent() -> None:
    from nana.autonomy import AutonomyLoop, AutonomyState

    loop = AutonomyLoop()
    entered = threading.Event()
    run_count = 0

    def harmless_run() -> None:
        nonlocal run_count
        run_count += 1
        entered.set()
        loop._stop_event.wait(timeout=2.0)

    loop._run = harmless_run
    loop.start()
    assert entered.wait(timeout=1.0)
    first_thread = loop._thread
    loop.start()

    assert run_count == 1
    assert loop._thread is first_thread
    assert first_thread is not None and first_thread.is_alive()

    loop.stop()
    assert loop.state == AutonomyState.STOPPED
    assert not first_thread.is_alive()
    print("  start twice: one thread; stop joined it")


def test_canonical_command_paths() -> None:
    from nana.autonomy import AUTONOMY_LOOP, AutonomyState
    from nana.cli import autonomy_command
    from nana.cli.autonomy_runtime_commands import handle_autonomy_runtime_command

    cli_globals = importlib.import_module("nana.cli.globals")
    handle_text_module = importlib.import_module("nana.cli.handle_text")
    assert autonomy_command.AUTONOMY_LOOP is AUTONOMY_LOOP
    assert handle_text_module.AUTONOMY_LOOP is AUTONOMY_LOOP

    entered = threading.Event()
    original_run = AUTONOMY_LOOP._run
    original_handle_admin = handle_text_module.handle_admin

    def harmless_run() -> None:
        entered.set()
        AUTONOMY_LOOP._stop_event.wait(timeout=2.0)

    try:
        AUTONOMY_LOOP._run = harmless_run
        AUTONOMY_LOOP._user_override = False
        AUTONOMY_LOOP.start()
        assert entered.wait(timeout=1.0)

        assert handle_autonomy_runtime_command("/autonomy-pause") is True
        paused = autonomy_command.autonomy_status_snapshot()
        assert AUTONOMY_LOOP.state == AutonomyState.PAUSED
        assert paused["state"] == "paused" and paused["paused"] is True
        assert paused["output_enabled"] is False

        assert handle_autonomy_runtime_command("/autonomy-resume") is True
        resumed = autonomy_command.autonomy_status_snapshot()
        assert AUTONOMY_LOOP.state == AutonomyState.RUNNING
        assert resumed["state"] == "running" and resumed["paused"] is False
        assert resumed["output_enabled"] is True

        handle_text_module.handle_admin = lambda _text: True
        result = asyncio.run(
            handle_text_module.handle_text(None, None, "ownership smoke", None)
        )
        assert result is False
        assert AUTONOMY_LOOP._user_override is True
        assert cli_globals.AUTONOMY_LOOP is AUTONOMY_LOOP
    finally:
        AUTONOMY_LOOP.stop()
        AUTONOMY_LOOP._run = original_run
        handle_text_module.handle_admin = original_handle_admin

    assert AUTONOMY_LOOP.state == AutonomyState.STOPPED
    print("  pause/resume/status/override paths use the canonical running loop")


def test_runtime_turn_state() -> None:
    app = importlib.import_module("nana.cli.app")
    cli_globals = importlib.import_module("nana.cli.globals")
    pipeline = importlib.import_module("nana.cli.chat_turn_pipeline")

    assert app.set_runtime_turn_state is cli_globals.set_runtime_turn_state
    assert pipeline.set_runtime_turn_state is cli_globals.set_runtime_turn_state

    started_at = time.time()
    cli_globals.set_runtime_turn_state("thinking", "ownership_smoke", "smoke")
    snapshot = dict(cli_globals.RUNTIME_TURN_STATE)
    assert snapshot["status"] == "thinking"
    assert snapshot["last_reason"] == "ownership_smoke"
    assert snapshot["last_source"] == "smoke"
    assert snapshot["last_update"] >= started_at
    assert snapshot["last_update"] > 0
    print(f"  shared turn state timestamp: {snapshot['last_update']:.6f}")


def main() -> None:
    tests = [
        test_active_runtime_sources,
        test_direct_identity,
        test_import_order_subprocesses,
        test_start_is_idempotent,
        test_canonical_command_paths,
        test_runtime_turn_state,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_core_runtime_ownership: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
