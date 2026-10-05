#!/usr/bin/env python3
"""Guarded, provider-free verification gate for Context Runtime.

Phases 2 through private-only 4, cumulative private Phases 6-7, the bounded
CUM2/autonomy adoption gates, and the complete provider-free final gate are
implemented. Every suite executes after the runner has installed fail-closed
guards for network access, arbitrary process creation, production Nana data/log
paths, and credential sources.
"""

from __future__ import annotations

import _io
import argparse
import builtins
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import dataclass
import importlib.util
import io
import hashlib
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import types
from unittest import mock


ROOT = Path(__file__).resolve().parent
PACKAGE_PARENT = ROOT.parent
SMOKE_ROOT = ROOT / "tests" / "smoke"
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))
if str(SMOKE_ROOT) not in sys.path:
    sys.path.insert(0, str(SMOKE_ROOT))

SUPPORTED_PHASE = "private-readiness"
LATER_PHASES = tuple(str(value) for value in range(5, 9))
PHASE_CHOICES = (
    '2', '3', '4', *LATER_PHASES, '8-cum2', '8-autonomy',
    'private-readiness',
)
PHASE_2_BOUNDARY = (
    "Phase 2 source complete / budget review pending / canonical unavailable"
)
_REAL_SUBPROCESS_RUN = subprocess.run
_REAL_POPEN = subprocess.Popen
_AUTHORIZED_CHILD_COMMAND: tuple[str, ...] | None = None
_HASH_SEED_SCRIPT = (
    "from nana.runtime.context_contracts import freeze_payload; "
    "print('|'.join(freeze_payload({'items': {'zeta', 'alpha', 'mu'}})['items']))"
)
_MEMORY_PREIMPORT_SCRIPT_SHA256 = (
    "2714370504C8B7D36C0E2DBEFCBBEFBEEA9BD25865962A1AB97B548038E19624"
)


class GuardViolation(AssertionError):
    """Raised when a verification suite attempts a forbidden side effect."""


@dataclass(frozen=True)
class SuiteSpec:
    label: str
    path: str
    kind: str


@dataclass(frozen=True)
class SuiteResult:
    label: str
    collected: int
    passed: int
    failed: int
    skipped: int
    exit_code: int
    detail: str = ""


PHASE_2_SUITES = (
    SuiteSpec("contracts", "tests/test_context_contracts.py", "pytest"),
    SuiteSpec("runtime", "tests/test_context_runtime.py", "pytest"),
    SuiteSpec("compiler", "tests/test_context_compiler.py", "pytest"),
    SuiteSpec("telemetry", "tests/test_context_telemetry.py", "pytest"),
    SuiteSpec(
        "context integration",
        "tests/smoke/smoke_context_runtime_integration.py",
        "self_running",
    ),
    SuiteSpec(
        "legacy baseline",
        "tests/smoke/smoke_context_runtime_legacy_baseline.py",
        "self_running",
    ),
    SuiteSpec(
        "public prompt privacy",
        "tests/smoke/smoke_memory_v2_public_prompt.py",
        "self_running",
    ),
    SuiteSpec(
        "history privacy",
        "tests/smoke/smoke_history_privacy.py",
        "unittest",
    ),
)
PHASE_3_SUITES = (
    *PHASE_2_SUITES[:-1],
    SuiteSpec('canonical private parity', 'tests/test_context_pipeline_parity.py', 'pytest'),
    SuiteSpec('frozen canonical boundary', 'tests/test_context_frozen_boundary.py', 'pytest'),
    SuiteSpec('private web protocol', 'tests/smoke/smoke_private_web_chat_protocol.py', 'pytest'),
    SuiteSpec('private observer', 'tests/smoke/smoke_private_turn_observer.py', 'pytest'),
    SuiteSpec('memory answer retest', 'tests/smoke/smoke_memory_answer_retest.py', 'unittest'),
    PHASE_2_SUITES[-1],
)
PHASE_4_SUITES = (
    *PHASE_3_SUITES[:-2],
    SuiteSpec('one-turn snapshot', 'tests/test_context_snapshot.py', 'pytest'),
    SuiteSpec(
        'canonical current situation',
        'tests/test_context_current_situation.py',
        'pytest',
    ),
    *PHASE_3_SUITES[-2:],
)
PHASE_6_SUITES = (
    *PHASE_4_SUITES[:4],
    SuiteSpec('Task 11 memory bundle', 'tests/test_context_memory_bundle.py', 'pytest'),
    SuiteSpec('Task 11 memory conflicts', 'tests/test_context_conflicts.py', 'pytest'),
    SuiteSpec('Task 12 private budget', 'tests/test_context_budget.py', 'pytest'),
    SuiteSpec('Task 9 expression view', 'tests/test_context_expression_view.py', 'pytest'),
    SuiteSpec('Task 10 continuity view', 'tests/test_context_continuity_view.py', 'pytest'),
    *PHASE_4_SUITES[4:],
)
PHASE_7_SUITES = (
    *PHASE_6_SUITES,
    SuiteSpec(
        'Task 13 transport receipt',
        'tests/test_context_transport_receipt.py',
        'pytest',
    ),
    SuiteSpec(
        'LLMGate routing',
        'tests/smoke/smoke_llmgate_model_routing.py',
        'self_running',
    ),
)
PHASE_8_CUM2_SUITES = (
    *PHASE_7_SUITES,
    SuiteSpec(
        'Task 14 CUM2 profile',
        'tests/test_context_cum2_profile.py',
        'pytest',
    ),
    SuiteSpec(
        'CUM2 response smoke',
        'tests/smoke/smoke_stream_cum2_response.py',
        'self_running',
    ),
)
PHASE_8_AUTONOMY_SUITES = (
    *PHASE_8_CUM2_SUITES,
    SuiteSpec(
        'Task 15 autonomy profile',
        'tests/test_context_autonomy_profile.py',
        'pytest',
    ),
    SuiteSpec(
        'autonomy output gate',
        'tests/smoke/smoke_autonomy_output_gate.py',
        'self_running',
    ),
    SuiteSpec(
        'autonomy start silent',
        'tests/smoke/smoke_autonomy_start_silent.py',
        'pytest',
    ),
    SuiteSpec(
        'voice autonomy isolation',
        'tests/smoke/smoke_voice_autonomy_isolation.py',
        'pytest',
    ),
)
PRIVATE_READINESS_SUITE = SuiteSpec(
    'private readiness',
    'tests/test_context_private_readiness.py',
    'pytest',
)
PRIVATE_READINESS_SUITES = (
    PRIVATE_READINESS_SUITE,
    SuiteSpec('private readiness budget', 'tests/test_context_budget.py', 'pytest'),
    SuiteSpec('private readiness compiler', 'tests/test_context_compiler.py', 'pytest'),
    SuiteSpec('private readiness telemetry', 'tests/test_context_telemetry.py', 'pytest'),
    SuiteSpec(
        'private readiness planner parity',
        'tests/test_context_pipeline_parity.py',
        'pytest',
    ),
    SuiteSpec(
        'private readiness transport',
        'tests/test_context_transport_receipt.py',
        'pytest',
    ),
)
ALL_SUITES = (*PHASE_8_AUTONOMY_SUITES, PRIVATE_READINESS_SUITE)


_CREDENTIAL_ENV_MARKERS = (
    "API_KEY",
    "ACCESS_TOKEN",
    "AUTH_TOKEN",
    "CLIENT_SECRET",
    "CREDENTIAL",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
)
_SAFE_ENV_NAMES = {
    "APPDATA",
    "LOCALAPPDATA",
    "COMSPEC",
    "HOMEDRIVE",
    "HOMEPATH",
    "PATH",
    "PATHEXT",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONHASHSEED",
    "PYTHONIOENCODING",
    "PYTHONPATH",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
}
_CREDENTIAL_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "service-account.json",
    "token.json",
    "token.txt",
}
_SUMMARY_PATTERNS = (
    re.compile(r"SUMMARY:\s*(?P<passed>\d+) passed,\s*(?P<failed>\d+) failed"),
    re.compile(r"Results:\s*(?P<passed>\d+) passed,\s*(?P<failed>\d+) failed"),
    re.compile(
        r"Context runtime legacy baseline:\s*"
        r"(?P<passed>\d+) passed,\s*(?P<failed>\d+) failed"
    ),
    re.compile(
        r"Memory v2 public prompt:\s*"
        r"(?P<passed>\d+) passed,\s*(?P<failed>\d+) failed"
    ),
)


def _credential_env_name(name: object) -> bool:
    folded = str(name).strip().upper()
    return any(marker in folded for marker in _CREDENTIAL_ENV_MARKERS)


def _sanitized_environment() -> dict[str, str]:
    """Return a minimal environment without copying host credentials."""

    clean = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in _SAFE_ENV_NAMES and not _credential_env_name(name)
    }
    clean.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "NANA_CONTEXT_PRIVATE_MODE": "legacy",
            "NANA_CONTEXT_PUBLIC_GPT_MODE": "legacy",
            "NANA_CONTEXT_CUM2_MODE": "legacy",
            "NANA_CONTEXT_AUTONOMY_MODE": "legacy",
            "NANA_CONTEXT_BUDGET_POLICY_REVISION": "",
        }
    )
    return clean


def _normalized_path(value: object) -> Path | None:
    if isinstance(value, int):
        return None
    try:
        return Path(os.fsdecode(os.fspath(value))).resolve(strict=False)
    except (OSError, TypeError, ValueError):
        return None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _protected_path_kind(value: object) -> str | None:
    path = _normalized_path(value)
    if path is None:
        return None
    if _is_within(path, (ROOT / "data").resolve()):
        return "production_data"
    if _is_within(path, (ROOT / "runtime_logs").resolve()):
        return "runtime_logs"
    if path.name.casefold() in _CREDENTIAL_FILE_NAMES:
        return "credential_file"
    folded = str(path).replace("/", "\\").casefold()
    if "\\.factory\\settings.json" in folded:
        return "credential_file"
    return None


def _install_startup_audit_guard() -> None:
    """Install before any Nana/runtime/test import and keep it for process life."""

    def audit(event: str, args: tuple[object, ...]) -> None:
        if event == "open" and args:
            kind = _protected_path_kind(args[0])
            if kind:
                raise GuardViolation(f"blocked {kind}")
        if event == "socket.connect":
            caller = sys._getframe(1)
            address = args[1] if len(args) > 1 else None
            if (
                caller.f_code.co_name in {"socketpair", "_fallback_socketpair"}
                and Path(caller.f_code.co_filename).name == "socket.py"
                and isinstance(address, tuple)
                and address
                and address[0] in {"127.0.0.1", "::1"}
            ):
                return
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo"}:
            raise GuardViolation(f"blocked {event}")
        if event in {
            "subprocess.Popen",
            "os.system",
            "os.posix_spawn",
            "os.posix_spawnp",
            "os.spawn",
        }:
            if event == "subprocess.Popen" and _AUTHORIZED_CHILD_COMMAND:
                command = args[1] if len(args) > 1 else None
                expected = _AUTHORIZED_CHILD_COMMAND
                if command == list(expected) or command == tuple(expected) or (
                    isinstance(command, str)
                    and command == subprocess.list2cmdline(expected)
                ):
                    return
            raise GuardViolation(f"blocked {event}")

    sys.addaudithook(audit)


_install_startup_audit_guard()


def _deny(kind: str):
    def denied(*_args: object, **_kwargs: object) -> object:
        raise GuardViolation(f"blocked {kind}")

    return denied


def _guarded_open(real_open):
    def guarded(file: object, *args: object, **kwargs: object) -> object:
        kind = _protected_path_kind(file)
        if kind:
            raise GuardViolation(f"blocked {kind}")
        return real_open(file, *args, **kwargs)

    return guarded


def _guarded_exists(real_exists):
    def guarded(path: Path) -> bool:
        if _protected_path_kind(path) == "credential_file":
            return False
        return real_exists(path)

    return guarded


def _guarded_subprocess_run(
    args: object,
    *positional: object,
    **kwargs: object,
) -> subprocess.CompletedProcess[str]:
    """Execute only the two exact reviewed child regressions under guards."""
    command = list(args) if isinstance(args, (list, tuple)) else []
    environment = kwargs.get("env", {})
    seed = environment.get("PYTHONHASHSEED") if isinstance(environment, dict) else None
    hash_seed_child = (
        command == [sys.executable, "-B", "-c", _HASH_SEED_SCRIPT]
        and seed in {"1", "987654"}
    )
    memory_preimport_child = (
        len(command) == 4
        and command[:3] == [sys.executable, "-B", "-c"]
        and isinstance(command[3], str)
        and hashlib.sha256(command[3].encode("utf-8")).hexdigest().upper()
        == _MEMORY_PREIMPORT_SCRIPT_SHA256
        and kwargs.get("check", False) is False
    )
    if (
        positional
        or not (hash_seed_child or memory_preimport_child)
        or _normalized_path(kwargs.get("cwd")) != PACKAGE_PARENT
        or kwargs.get("shell", False)
        or set(kwargs) - {"cwd", "env", "check", "capture_output", "text"}
    ):
        raise GuardViolation("blocked arbitrary process creation")
    child_script = _HASH_SEED_SCRIPT if hash_seed_child else command[3]
    bootstrap = (
        "import runpy\n"
        f"gate = runpy.run_path({str(Path(__file__).resolve())!r}, run_name='_hash_seed_guard')\n"
        "gate['_install_lightweight_nana_packages']()\n"
        "with gate['_runtime_guards']():\n"
        "    gate['_verify_guards']()\n"
        f"    exec({child_script!r})\n"
    )
    child_command = (sys.executable, "-B", "-c", bootstrap)
    clean = _sanitized_environment()
    if hash_seed_child:
        clean["PYTHONHASHSEED"] = seed
    global _AUTHORIZED_CHILD_COMMAND
    _AUTHORIZED_CHILD_COMMAND = child_command
    try:
        with mock.patch.object(subprocess, "Popen", _REAL_POPEN):
            return _REAL_SUBPROCESS_RUN(
                list(child_command), cwd=PACKAGE_PARENT, env=clean,
                check=kwargs.get("check", False), capture_output=True, text=True,
                timeout=30,
            )
    finally:
        _AUTHORIZED_CHILD_COMMAND = None


def _runtime_guards() -> ExitStack:
    stack = ExitStack()
    stack.enter_context(mock.patch.dict(os.environ, _sanitized_environment(), clear=True))
    stack.enter_context(mock.patch("builtins.open", _guarded_open(builtins.open)))
    stack.enter_context(mock.patch("io.open", _guarded_open(io.open)))
    stack.enter_context(mock.patch("io.open_code", _guarded_open(io.open_code)))
    stack.enter_context(mock.patch("_io.open_code", _guarded_open(_io.open_code)))
    stack.enter_context(mock.patch("os.open", _guarded_open(os.open)))
    stack.enter_context(mock.patch.object(Path, "exists", _guarded_exists(Path.exists)))
    stack.enter_context(mock.patch("socket.create_connection", _deny("network")))
    stack.enter_context(mock.patch("socket.getaddrinfo", _deny("network")))
    stack.enter_context(mock.patch.object(subprocess, "run", _guarded_subprocess_run))
    for name in ("Popen", "call", "check_call", "check_output"):
        stack.enter_context(mock.patch.object(subprocess, name, _deny("process")))
    stack.enter_context(mock.patch("os.system", _deny("process")))
    stack.enter_context(mock.patch("os.popen", _deny("process")))
    return stack


def _verify_guards() -> None:
    probes = (
        lambda: builtins.open(ROOT / "data" / "memory.json", "rb"),
        lambda: builtins.open(ROOT / ".env", "rb"),
        lambda: socket.create_connection(("127.0.0.1", 9)),
        lambda: subprocess.Popen(["context-runtime-forbidden"]),
    )
    for probe in probes:
        try:
            probe()
        except GuardViolation:
            continue
        raise AssertionError("side-effect guard did not fail closed")
    if any(_credential_env_name(name) for name in os.environ):
        raise AssertionError("credential-bearing environment reached suites")


class _PytestCounter:
    def __init__(self) -> None:
        self.collected = 0
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def pytest_collection_finish(self, session: object) -> None:
        self.collected = len(getattr(session, "items", ()))

    def pytest_runtest_logreport(self, report: object) -> None:
        outcome = str(getattr(report, "outcome", ""))
        when = str(getattr(report, "when", ""))
        if outcome == "skipped":
            if when == "setup" or when == "call":
                self.skipped += 1
            return
        if outcome == "failed":
            self.failed += 1
            return
        if outcome == "passed" and when == "call":
            self.passed += 1


def _run_pytest(spec: SuiteSpec) -> SuiteResult:
    import pytest

    counter = _PytestCounter()
    output = io.StringIO()
    with redirect_stdout(output), redirect_stderr(output):
        exit_code = int(
            pytest.main(
                [str(ROOT / spec.path), "-q", "--tb=short", "-p", "no:cacheprovider"],
                plugins=[counter],
            )
        )
    detail = "" if exit_code == 0 else _output_tail(output.getvalue())
    return SuiteResult(
        spec.label,
        counter.collected,
        counter.passed,
        counter.failed,
        counter.skipped,
        exit_code,
        detail,
    )


def _load_source_module(spec: SuiteSpec) -> types.ModuleType:
    module_name = "_context_gate_" + re.sub(r"\W+", "_", spec.label)
    module_spec = importlib.util.spec_from_file_location(module_name, ROOT / spec.path)
    if module_spec is None or module_spec.loader is None:
        raise RuntimeError(f"cannot load {spec.path}")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_name] = module
    module_spec.loader.exec_module(module)
    return module


def _summary_counts(output: str) -> tuple[int, int]:
    for pattern in _SUMMARY_PATTERNS:
        matched = pattern.search(output)
        if matched:
            return int(matched.group("passed")), int(matched.group("failed"))
    raise RuntimeError("suite did not emit a recognized pass/fail summary")


def _run_self_running(spec: SuiteSpec) -> SuiteResult:
    output = io.StringIO()
    exit_code = 1
    try:
        with redirect_stdout(output), redirect_stderr(output):
            module = _load_source_module(spec)
            if hasattr(module, "_run_tests"):
                exit_code = int(module._run_tests())
            elif hasattr(module, "run_all"):
                exit_code = int(module.run_all())
            else:
                raise RuntimeError("self-running suite has no runner")
        passed, failed = _summary_counts(output.getvalue())
    except Exception as exc:
        passed, failed, exit_code = 0, 1, 1
        output.write(f"\n{type(exc).__name__}: {exc}\n")
    return SuiteResult(
        spec.label,
        passed + failed,
        passed,
        failed,
        0,
        exit_code,
        "" if exit_code == 0 else _output_tail(output.getvalue()),
    )


def _run_unittest(spec: SuiteSpec) -> SuiteResult:
    import unittest

    output = io.StringIO()
    try:
        module = _load_source_module(spec)
        if spec.label == 'memory answer retest':
            # Existing isolated fixture predates inline_audio_tags' config field.
            # Supply synthetic v3 configuration at the fixture boundary only.
            install = module._install_isolated_dependencies
            def install_with_audio_config():
                result = install()
                sys.modules['nana.config'].ELEVEN_PUBLIC_TTS_MODEL = 'eleven_v3'
                return result
            module._install_isolated_dependencies = install_with_audio_config
        suite = unittest.defaultTestLoader.loadTestsFromModule(module)
        collected = suite.countTestCases()
        with redirect_stdout(output), redirect_stderr(output):
            result = unittest.TextTestRunner(stream=output, verbosity=1).run(suite)
        failed = len(result.failures) + len(result.errors) + len(result.unexpectedSuccesses)
        skipped = len(result.skipped)
        passed = collected - failed - skipped
        exit_code = 0 if result.wasSuccessful() else 1
    except Exception as exc:
        collected, passed, failed, skipped, exit_code = 1, 0, 1, 0, 1
        output.write(f"\n{type(exc).__name__}: {exc}\n")
    return SuiteResult(
        spec.label,
        collected,
        passed,
        failed,
        skipped,
        exit_code,
        "" if exit_code == 0 else _output_tail(output.getvalue()),
    )


def _output_tail(value: str, limit: int = 20) -> str:
    lines = [line for line in value.splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


def _purge_suite_modules() -> None:
    for name in tuple(sys.modules):
        if (
            name.startswith("_context_gate_")
            or name.startswith("nana")
            or name == "test_context_budget"
        ):
            sys.modules.pop(name, None)


def _install_lightweight_nana_packages() -> None:
    nana = types.ModuleType("nana")
    nana.__path__ = [str(ROOT)]
    runtime = types.ModuleType("nana.runtime")
    runtime.__path__ = [str(ROOT / "runtime")]
    sys.modules["nana"] = nana
    sys.modules["nana.runtime"] = runtime


def _install_suite_prerequisites(spec: SuiteSpec) -> None:
    if spec.label not in {
        'Task 9 expression view',
        'Task 10 continuity view',
        'Task 11 memory bundle',
    }:
        return
    memory_module = types.ModuleType('nana.memory')
    memory_module.memory = {
        'chat_log': [],
        'emotion': {},
        'long_term': [],
        'mood_continuity': {},
        'persona': {},
        'short_term': [],
    }
    memory_module.memory_lock = threading.RLock()
    memory_module.load_recent_chat = lambda *_args, **_kwargs: ''
    memory_module.save_memory_async = lambda *_args, **_kwargs: None
    sys.modules['nana.memory'] = memory_module


def _run_suite(spec: SuiteSpec) -> SuiteResult:
    if spec.kind == "pytest":
        return _run_pytest(spec)
    if spec.kind == "self_running":
        return _run_self_running(spec)
    if spec.kind == "unittest":
        return _run_unittest(spec)
    raise RuntimeError(f"unknown suite kind: {spec.kind}")


def _print_result(result: SuiteResult) -> None:
    print(
        f"SUITE {result.label}: collected={result.collected} "
        f"pass={result.passed} fail={result.failed} skip={result.skipped} "
        f"exit={result.exit_code}"
    )
    if result.detail:
        for line in result.detail.splitlines():
            print(f"  {line}")


def _run_phase_2(phase='2') -> int:
    print(f"Context Runtime guarded provider-free gate: phase={phase}")
    results: list[SuiteResult] = []
    with _runtime_guards():
        _verify_guards()
        suites = {
            '2': PHASE_2_SUITES,
            '3': PHASE_3_SUITES,
            '4': PHASE_4_SUITES,
            '6': PHASE_6_SUITES,
            '7': PHASE_7_SUITES,
            '8-cum2': PHASE_8_CUM2_SUITES,
            '8-autonomy': PHASE_8_AUTONOMY_SUITES,
            'private-readiness': PRIVATE_READINESS_SUITES,
            'all': ALL_SUITES,
        }[phase]
        for spec in suites:
            _purge_suite_modules()
            _install_lightweight_nana_packages()
            _install_suite_prerequisites(spec)
            result = _run_suite(spec)
            results.append(result)
            _print_result(result)
        _verify_guards()

    mandatory_coverage_failed = phase == 'all' and (
        not results
        or any(
            result.collected <= 0
            or result.failed != 0
            or result.skipped != 0
            or result.passed != result.collected
            for result in results
        )
    )
    totals = SuiteResult(
        "TOTAL",
        sum(result.collected for result in results),
        sum(result.passed for result in results),
        sum(result.failed for result in results),
        sum(result.skipped for result in results),
        int(
            any(result.exit_code != 0 for result in results)
            or mandatory_coverage_failed
        ),
    )
    _print_result(totals)
    boundaries = {
        '2': PHASE_2_BOUNDARY,
        '3': 'Phase 3 private fake/offline / default legacy / live canonical unavailable',
        '4': 'Phase 4 private snapshot/current-situation fake/offline / default legacy / live canonical unavailable',
        '6': 'Phase 6 private budget fake/offline / default legacy / live canonical unavailable',
        '7': 'Phase 7 frozen transport receipt fake/offline / default legacy / live canonical unavailable',
        '8-cum2': (
            'Phase 8 CUM2 profile fake/offline / default legacy / '
            'real canonical budget gate closed / Task 15 not executed'
        ),
        '8-autonomy': (
            'Phase 8 autonomy profile fake/offline / default legacy / '
            'real canonical autonomy budget gate closed / Task 16 not executed'
        ),
        'private-readiness': (
            'Private readiness fake/offline / injected synthetic profiles only / '
            'production registry empty / canonical dispatch gate closed'
        ),
        'all': (
            'Task 16 Context gate PASS / provider-free / default legacy / '
            'separate closeout regressions and audit pending'
        ),
    }
    boundary = boundaries[phase]
    print(boundary if totals.exit_code == 0 else f"phase_{phase}_verification_failed")
    return totals.exit_code


def _unavailable(requested: str) -> int:
    print(f"UNAVAILABLE: Context Runtime phase {requested} is not implemented")
    print("phase_4_not_executed / canonical unavailable")
    print("No later phase was executed or certified")
    return 2


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--phase", choices=PHASE_CHOICES)
    selection.add_argument("--all", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.all:
        return _run_phase_2('all')
    if args.phase not in {
        '2', '3', '4', '6', '7', '8-cum2', '8-autonomy',
        'private-readiness',
    }:
        return _unavailable(str(args.phase))
    return _run_phase_2(args.phase)


if __name__ == "__main__":
    raise SystemExit(main())
