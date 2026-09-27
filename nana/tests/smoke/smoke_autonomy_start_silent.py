"""Deterministic smoke for silent-by-default autonomy startup."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from nana.autonomy.loop import AutonomyLoop, AutonomyState


def test_paused_start_rejects_before_thought_or_output() -> None:
    loop = AutonomyLoop()
    try:
        loop.start(paused=True)
        assert loop.state == AutonomyState.PAUSED
        assert loop.cadence.paused is True

        decision = loop.tick()
        assert decision["accepted"] is False
        assert decision["reason"] == "paused"
        assert loop.express.last_trace is None
    finally:
        assert loop.stop(join_timeout_s=2.0) is True
    print("  paused start: no thought or autonomous output")


def test_explicit_resume_restores_runtime_policy() -> None:
    loop = AutonomyLoop()
    try:
        loop.start(paused=True)
        loop.resume()
        assert loop.state == AutonomyState.RUNNING
        assert loop.cadence.paused is False
        loop.pause()
        assert loop.state == AutonomyState.PAUSED
    finally:
        assert loop.stop(join_timeout_s=2.0) is True
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
