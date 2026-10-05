"""Deterministic smoke for silent-by-default autonomy startup."""

from __future__ import annotations

from pathlib import Path
import sys
import threading
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


_install_synthetic_packages()


from nana.autonomy.loop import AutonomyLoop, AutonomyState


def _install_collaborator_spies(loop):
    import nana.autonomy.llm_banter as llm_banter

    calls = {"observer": 0, "thought": 0, "model": 0, "output": 0}
    unexpected = threading.Event()

    def hit(name, result=None):
        calls[name] += 1
        unexpected.set()
        return result

    loop._observer.get_context = lambda: hit("observer", {})
    loop._thought.pick = lambda *_args, **_kwargs: hit("thought")
    loop._thought.pick_prefer_ultra_short = loop._thought.pick
    loop._express.emit = lambda *_args, **_kwargs: hit("output")
    original_call = llm_banter._call_llm
    llm_banter._call_llm = lambda *_args, **_kwargs: hit(
        "model", (None, "blocked")
    )
    return calls, unexpected, lambda: setattr(llm_banter, "_call_llm", original_call)


def _install_inert_worker(loop):
    entered = threading.Event()

    def inert_driver():
        entered.set()
        loop._stop_event.wait()

    loop._run = inert_driver
    return entered


def test_paused_start_rejects_before_thought_or_output() -> None:
    loop = AutonomyLoop()
    calls, _unexpected, restore = _install_collaborator_spies(loop)
    entered = _install_inert_worker(loop)
    try:
        loop.start(paused=True)
        assert entered.wait(timeout=1.0)
        assert loop.state == AutonomyState.PAUSED
        assert loop.cadence.paused is True

        decision = loop.tick()
        assert decision["accepted"] is False
        assert decision["reason"] == "paused"
        assert loop.express.last_trace is None
    finally:
        assert loop.stop(join_timeout_s=2.0) is True
        restore()
    assert calls == {"observer": 0, "thought": 0, "model": 0, "output": 0}
    print("  paused start: no thought or autonomous output")


def test_explicit_resume_restores_runtime_policy() -> None:
    loop = AutonomyLoop()
    calls, unexpected, restore = _install_collaborator_spies(loop)
    entered = _install_inert_worker(loop)
    try:
        loop.start(paused=True)
        assert entered.wait(timeout=1.0)
        loop.resume()
        assert loop.state == AutonomyState.RUNNING
        assert loop.cadence.paused is False
        assert not unexpected.wait(timeout=0.05)
        loop.pause()
        assert loop.state == AutonomyState.PAUSED
    finally:
        assert loop.stop(join_timeout_s=2.0) is True
        restore()
    assert calls == {"observer": 0, "thought": 0, "model": 0, "output": 0}
    print("  resume: explicit session opt-in remains available")


def main() -> None:
    tests = (
        test_paused_start_rejects_before_thought_or_output,
        test_explicit_resume_restores_runtime_policy,
    )
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_autonomy_start_silent: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
