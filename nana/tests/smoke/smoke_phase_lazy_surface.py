"""Smoke test for the lazy ``nana.phases`` export surface."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _loaded_phase_modules() -> list[str]:
    return sorted(
        name
        for name in sys.modules
        if name.startswith("nana.phases.phase")
    )


def test_handle_text_import_does_not_load_phase_modules() -> None:
    importlib.import_module("nana.cli.handle_text")
    loaded = _loaded_phase_modules()
    assert loaded == [], f"handle_text imported phase modules eagerly: {loaded}"


def test_phase_function_exports_are_lazy() -> None:
    phases = importlib.import_module("nana.phases")
    status_fn = getattr(phases, "print_phase7_status")
    assert type(status_fn).__name__ == "LazyPhaseCallable"
    assert getattr(status_fn, "__name__", "") == "print_phase7_status"
    assert _loaded_phase_modules() == []


def test_phase_submodule_export_still_works() -> None:
    phases = importlib.import_module("nana.phases")
    phase10 = getattr(phases, "phase10")
    assert phase10.__name__ == "nana.phases.phase10"
    assert "nana.phases.phase10" in sys.modules


def main() -> int:
    tests = [
        test_handle_text_import_does_not_load_phase_modules,
        test_phase_function_exports_are_lazy,
        test_phase_submodule_export_still_works,
    ]
    passed = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            print(f"FAIL {test.__name__}: {exc}")
            return 1
        print(f"PASS {test.__name__}")
        passed += 1
    print(f"Result: {passed}/{len(tests)} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
