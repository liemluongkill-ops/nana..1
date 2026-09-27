"""Deterministic coverage for the canonical autonomous-output gate.

The smoke never starts Nana, TTS, VTS, a browser, or an external API.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import threading


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
    from nana.autonomy import AutonomyLoop, AutonomyState

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
    from nana.runtime import pulse

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
        asyncio.run(
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
    from nana.runtime import pulse

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
        asyncio.run(pulse.maybe_context_react(None, voice))
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
    from nana.runtime import pulse

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

        asyncio.run(
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


def main() -> None:
    tests = [
        test_loop_state_is_the_output_source_of_truth,
        test_paused_browser_observes_without_output,
        test_paused_context_discards_confidence_and_deferred_speech,
        test_running_browser_path_still_emits_once,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_autonomy_output_gate: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
