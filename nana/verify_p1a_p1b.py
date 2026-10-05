#!/usr/bin/env python3
"""Executable acceptance gate for Memory Phase 2 blockers P1-A and P1-B.

The gate is provider-free: semantic calls use fakes and every production flag
must remain disabled. Persistence/privacy smokes run in separate subprocesses
because their audit hooks intentionally forbid loading Nana's production
configuration and cannot be safely combined into one pytest process.
"""

from __future__ import annotations

import asyncio
import inspect
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent
PACKAGE_PARENT = ROOT.parent
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

PYTEST_SUITES = (
    (
        "P1-A unit + private hot-path",
        "tests/test_semantic_adapter_p1a.py",
        "tests/test_semantic_adapter_p1a_hotpath.py",
    ),
    (
        "P1-B unit + compatibility + sync/stream hot-path",
        "tests/test_public_grounding_p1b.py",
        "tests/test_p1b_minimal.py",
        "tests/test_public_grounding_p1b_hotpath.py",
    ),
    (
        "Phase 2 canary regression",
        "tests/smoke/smoke_phase2_semantic_retrieval.py",
        "tests/smoke/smoke_phase2_consolidation_preview.py",
        "tests/smoke/smoke_phase2_public_cross_session.py",
        "tests/smoke/smoke_phase2_grounding_integration.py",
        "tests/smoke/smoke_phase2_status.py",
    ),
    (
        "Memory v2 public/private prompt regression",
        "tests/smoke/smoke_memory_v2_public_prompt.py",
    ),
)

ISOLATED_SCRIPTS = (
    ("Memory v2 Phase 1", "tests/smoke/smoke_memory_v2_phase1.py"),
    ("Atomic persistence/recovery", "tests/smoke/smoke_memory_v2_persistence.py"),
    ("History privacy", "tests/smoke/smoke_history_privacy.py"),
    ("Legacy public memory", "tests/smoke/smoke_memory_v2_legacy_public.py"),
    ("Delivery state machine", "tests/smoke/smoke_memory_v2_delivery.py"),
    ("Runtime/outbox bridge", "tests/smoke/smoke_memory_v2_bridge.py"),
)

SOURCE_FILES = (
    "runtime/semantic_adapters/__init__.py",
    "runtime/controlled_memory_retrieval.py",
    "runtime/public_grounding_helpers.py",
    "runtime/memory_grounding.py",
    "brain/gpt.py",
    "tests/test_semantic_adapter_p1a.py",
    "tests/test_semantic_adapter_p1a_hotpath.py",
    "tests/test_public_grounding_p1b.py",
    "tests/test_public_grounding_p1b_hotpath.py",
)


def pytest_configure(config: pytest.Config) -> None:
    if not config.pluginmanager.hasplugin("asyncio"):
        config.addinivalue_line(
            "markers",
            "asyncio: run a marked coroutine with the verifier-local fallback",
        )


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    if pyfuncitem.config.pluginmanager.hasplugin("asyncio"):
        return None
    if pyfuncitem.get_closest_marker("asyncio") is None:
        return None
    testfunction = pyfuncitem.obj
    if not inspect.iscoroutinefunction(testfunction):
        return None
    testargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
    }
    asyncio.run(testfunction(**testargs))
    return True


def _tail(value: str, limit: int = 16) -> str:
    lines = [line for line in str(value or "").splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


def _run(label: str, args: list[str], *, cwd: Path = ROOT) -> bool:
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        print(f"FAIL {label} (exit={result.returncode})")
        print(_tail(combined))
        return False
    summary = _tail(combined, limit=5)
    print(f"PASS {label}")
    if summary:
        for line in summary.splitlines():
            print(f"  {line}")
    return True


def _check_syntax() -> bool:
    ok = True
    for relative in SOURCE_FILES:
        path = ROOT / relative
        if not path.exists():
            print(f"FAIL syntax: missing {relative}")
            ok = False
            continue
        try:
            py_compile.compile(str(path), doraise=True)
            print(f"PASS syntax {relative}")
        except py_compile.PyCompileError as exc:
            print(f"FAIL syntax {relative}: {exc.msg}")
            ok = False
    return ok


def _check_defaults() -> bool:
    try:
        from nana import config as nana_config
        from nana.runtime.semantic_adapters import (
            build_semantic_adapter,
            resolve_semantic_config,
        )

        semantic = resolve_semantic_config()
        assert semantic.provider == "none"
        assert semantic.endpoint == ""
        assert semantic.model == ""
        assert semantic.api_key_env == ""
        assert build_semantic_adapter() is None
        for name in (
            "MEMORY_SEMANTIC_RETRIEVAL_ENABLED",
            "MEMORY_CONSOLIDATION_PREVIEW_ENABLED",
            "MEMORY_PUBLIC_CROSS_SESSION_RECALL_ENABLED",
            "MEMORY_PROMOTION_ENABLED",
        ):
            assert getattr(nana_config, name, False) is False, f"{name} must remain OFF"
    except Exception as exc:
        print(f"FAIL provider/flag defaults: {type(exc).__name__}: {exc}")
        return False
    print("PASS provider=none, no embedding credential configured, all Phase 2/3 production flags OFF")
    return True


def main() -> int:
    print("=" * 72)
    print("Memory Phase 2 P1-A/P1-B executable acceptance gate")
    print("=" * 72)
    checks = [_check_syntax(), _check_defaults()]

    for label, *files in PYTEST_SUITES:
        checks.append(
            _run(
                label,
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    *files,
                    "-q",
                    "--tb=short",
                    "-p",
                    "verify_p1a_p1b",
                ],
            )
        )

    for label, relative in ISOLATED_SCRIPTS:
        checks.append(
            _run(label, [sys.executable, str(ROOT / relative)], cwd=(ROOT / relative).parent)
        )

    print("=" * 72)
    if all(checks):
        print("PASS: P1-A/P1-B implementation and scoped regressions are green")
        print("No real embedding provider or production Nana runtime was called")
        return 0
    print("FAIL: acceptance gate is not green; do not promote to Wiki")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
