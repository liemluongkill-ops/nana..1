"""Deterministic coverage for the canonical autonomous-output gate.

The smoke never starts Nana, TTS, VTS, a browser, or an external API.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import threading
import time
import types


NANA_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PARENT = NANA_ROOT.parent
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))


def _install_synthetic_packages() -> None:
    packages = {
        "nana": NANA_ROOT,
        "nana.autonomy": NANA_ROOT / "autonomy",
        "nana.runtime": NANA_ROOT / "runtime",
        "nana.brain": NANA_ROOT / "brain",
    }
    for name, path in packages.items():
        if name in sys.modules:
            continue
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module


def _load_pulse_surface():
    path = NANA_ROOT / "runtime" / "pulse.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    wanted = {
        "autonomous_output_enabled",
        "_clear_suppressed_output_state",
        "_sync_browser_presence_baseline",
        "maybe_browser_presence_react",
        "maybe_context_react",
        "build_presence_context",
    }
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in wanted
    ]
    module_ast = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module_ast)
    context_lock = threading.RLock()
    context_state = {
        "proactive": {
            "enabled": True,
            "last_browser_kind": None,
            "last_browser_title": None,
            "last_browser_url": None,
            "last_browser_reaction_time": 0,
            "presence_debug": {},
        },
        "active_app": "",
        "active_zone": "chill",
        "idle_state": "active",
        "alt_tab_count": 0,
        "cpu_spike_since": None,
        "in_flow": False,
        "last_chat_time": 0,
        "flow_blocked_reaction": None,
    }
    confidence_state = {"score": 0.0, "last_context_react": 0.0}

    async def no_lifecycle(*_args, **_kwargs):
        return None

    def close_task(coroutine):
        coroutine.close()
        return SimpleNamespace(cancel=lambda: None)

    namespace = {
        "asyncio": SimpleNamespace(create_task=close_task),
        "random": __import__("random"),
        "time": time,
        "AUTONOMY_LOOP": SimpleNamespace(output_enabled=False),
        "context_lock": context_lock,
        "context_state": context_state,
        "confidence_state": confidence_state,
        "CHAT_CONTEXT_SUPPRESS": 120.0,
        "CONTEXT_REACT_COOLDOWN": 120.0,
        "PROACTIVE_BROWSER_COOLDOWN": 120.0,
        "presence_rhythm_allowed": lambda *_args, **_kwargs: (True, "ok", {}),
        "ambient_reaction_allowed": lambda *_args, **_kwargs: (True, "ok"),
        "compose_reaction": lambda **_kwargs: (None, {}),
        "record_ambient_reaction": lambda *_args, **_kwargs: None,
        "log_event": lambda *_args, **_kwargs: None,
        "trigger_expression_lifecycle": no_lifecycle,
        "get_confidence": lambda: confidence_state["score"],
        "is_in_flow": lambda: bool(context_state["in_flow"]),
    }
    surface = types.ModuleType("_isolated_autonomy_pulse")
    surface.__dict__.update(namespace)
    exec(compile(module_ast, str(path), "exec"), surface.__dict__)
    return surface


_install_synthetic_packages()
pulse = _load_pulse_surface()


def _run_coroutine(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as stopped:
        return stopped.value
    finally:
        coroutine.close()
    raise AssertionError("isolated pulse coroutine unexpectedly suspended")


class _VoiceSpy:
    def __init__(self) -> None:
        self.calls = []

    def say(self, text) -> None:
        self.calls.append(text)


def _browser_snapshot():
    return SimpleNamespace(
        available=True,
        kind="music",
        title="Output gate smoke",
        url="https://example.invalid/music",
        browser="edge",
    )


def test_loop_state_is_the_output_source_of_truth() -> None:
    from nana.autonomy.loop import AutonomyLoop, AutonomyState

    loop = AutonomyLoop()
    entered = threading.Event()

    def harmless_run() -> None:
        entered.set()
        loop._stop_event.wait(timeout=2.0)

    loop._run = harmless_run
    assert loop.output_enabled is False

    loop.start(paused=True)
    assert entered.wait(timeout=1.0)
    assert loop.state == AutonomyState.PAUSED
    assert loop.output_enabled is False

    loop.resume()
    assert loop.output_enabled is True
    loop.pause()
    assert loop.output_enabled is False

    assert loop.stop() is True
    assert loop.output_enabled is False
    print("  stopped/paused=False; running=True")


def test_paused_browser_observes_without_output() -> None:
    original_gate = pulse.autonomous_output_enabled
    with pulse.context_lock:
        saved_proactive = deepcopy(pulse.context_state["proactive"])
        pulse.context_state["proactive"]["last_browser_kind"] = "shopping"
        pulse.context_state["proactive"]["last_browser_title"] = "old"
        pulse.context_state["proactive"]["last_browser_url"] = "old"

    voice = _VoiceSpy()
    snapshot = _browser_snapshot()
    try:
        pulse.autonomous_output_enabled = lambda: False
        _run_coroutine(
            pulse.maybe_browser_presence_react(
                None,
                voice,
                snapshot,
                "war_zone",
                "chill",
            )
        )
        with pulse.context_lock:
            proactive = pulse.context_state["proactive"]
            assert proactive["last_browser_kind"] == snapshot.kind
            assert proactive["last_browser_title"] == snapshot.title
            assert proactive["last_browser_url"] == snapshot.url
            assert proactive["presence_debug"]["blocked_reason"] == "autonomy_paused"
        assert voice.calls == []
    finally:
        pulse.autonomous_output_enabled = original_gate
        with pulse.context_lock:
            pulse.context_state["proactive"] = saved_proactive

    print("  paused browser snapshot synced; voice calls=0")


def test_paused_context_discards_confidence_and_deferred_speech() -> None:
    original_gate = pulse.autonomous_output_enabled
    voice = _VoiceSpy()
    with pulse.context_lock:
        saved_confidence = deepcopy(pulse.confidence_state)
        saved_alt_tabs = pulse.context_state["alt_tab_count"]
        saved_blocked = pulse.context_state["flow_blocked_reaction"]
        pulse.confidence_state["score"] = 1.0
        pulse.context_state["alt_tab_count"] = 9
        pulse.context_state["flow_blocked_reaction"] = "stale reaction"

    try:
        pulse.autonomous_output_enabled = lambda: False
        _run_coroutine(pulse.maybe_context_react(None, voice))
        with pulse.context_lock:
            assert pulse.confidence_state["score"] == 0.0
            assert pulse.context_state["alt_tab_count"] == 0
            assert pulse.context_state["flow_blocked_reaction"] is None
        assert voice.calls == []
    finally:
        pulse.autonomous_output_enabled = original_gate
        with pulse.context_lock:
            pulse.confidence_state.clear()
            pulse.confidence_state.update(saved_confidence)
            pulse.context_state["alt_tab_count"] = saved_alt_tabs
            pulse.context_state["flow_blocked_reaction"] = saved_blocked

    print("  paused context reset score/deferred reaction; voice calls=0")


def test_running_browser_path_still_emits_once() -> None:
    patched = {
        "autonomous_output_enabled": pulse.autonomous_output_enabled,
        "presence_rhythm_allowed": pulse.presence_rhythm_allowed,
        "ambient_reaction_allowed": pulse.ambient_reaction_allowed,
        "compose_reaction": pulse.compose_reaction,
        "record_ambient_reaction": pulse.record_ambient_reaction,
        "log_event": pulse.log_event,
        "trigger_expression_lifecycle": pulse.trigger_expression_lifecycle,
    }
    with pulse.context_lock:
        saved_proactive = deepcopy(pulse.context_state["proactive"])
        saved_flow = pulse.context_state["in_flow"]
        saved_chat = pulse.context_state["last_chat_time"]
        proactive = pulse.context_state["proactive"]
        proactive["enabled"] = True
        proactive["last_browser_kind"] = "shopping"
        proactive["last_browser_title"] = "old"
        proactive["last_browser_url"] = "old"
        proactive["last_browser_reaction_time"] = 0
        pulse.context_state["in_flow"] = False
        pulse.context_state["last_chat_time"] = 0

    async def fake_lifecycle(*_args, **_kwargs):
        return None

    voice = _VoiceSpy()
    try:
        pulse.autonomous_output_enabled = lambda: True
        pulse.presence_rhythm_allowed = lambda *_args, **_kwargs: (True, "ok", {})
        pulse.ambient_reaction_allowed = lambda *_args, **_kwargs: (True, "ok")
        pulse.compose_reaction = lambda **_kwargs: ("running reaction", {"source": "smoke"})
        pulse.record_ambient_reaction = lambda *_args, **_kwargs: None
        pulse.log_event = lambda *_args, **_kwargs: None
        pulse.trigger_expression_lifecycle = fake_lifecycle

        _run_coroutine(
            pulse.maybe_browser_presence_react(
                None,
                voice,
                _browser_snapshot(),
                "chill",
                "chill",
            )
        )
        assert voice.calls == ["running reaction"]
    finally:
        for name, value in patched.items():
            setattr(pulse, name, value)
        with pulse.context_lock:
            pulse.context_state["proactive"] = saved_proactive
            pulse.context_state["in_flow"] = saved_flow
            pulse.context_state["last_chat_time"] = saved_chat

    print("  resumed/running browser output emits exactly once")


def _run_tests() -> int:
    tests = [
        test_loop_state_is_the_output_source_of_truth,
        test_paused_browser_observes_without_output,
        test_paused_context_discards_confidence_and_deferred_speech,
        test_running_browser_path_still_emits_once,
    ]
    failed = 0
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"  FAIL: {type(exc).__name__}: {exc}")
    passed = len(tests) - failed
    print(f"SUMMARY: {passed} passed, {failed} failed")
    return int(failed != 0)


def main() -> None:
    raise SystemExit(_run_tests())


if __name__ == "__main__":
    main()
