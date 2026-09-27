"""Deterministic smoke for the camera-agnostic Presence human gate."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from nana.runtime.presence_human_gate import PresenceHumanGate


def test_disabled_gate_is_backward_compatible() -> None:
    gate = PresenceHumanGate(enabled=False)
    snapshot = gate.snapshot(at=0.0)
    assert snapshot["state"] == "disabled"
    assert snapshot["audio_allowed"] is True
    print("  disabled: audio remains backward-compatible")


def test_two_detections_confirm_presence() -> None:
    gate = PresenceHumanGate(enabled=True, confirmations_required=2)
    assert gate.snapshot(at=0.0)["interaction_mode"] == "display_only"
    first = gate.observe(True, confidence=0.9, at=1.0)
    assert first["state"] == "candidate"
    assert first["audio_allowed"] is False
    second = gate.observe(True, confidence=0.9, at=1.2)
    assert second["state"] == "present"
    assert second["audio_allowed"] is True
    print("  debounce: two detections open interactive mode")


def test_low_confidence_does_not_open_gate() -> None:
    gate = PresenceHumanGate(enabled=True, minimum_confidence=0.55)
    gate.observe(True, confidence=0.2, at=1.0)
    snapshot = gate.snapshot(at=1.0)
    assert snapshot["state"] == "absent"
    assert snapshot["audio_allowed"] is False
    print("  confidence: weak camera evidence stays display-only")


def test_absence_grace_prevents_flapping() -> None:
    gate = PresenceHumanGate(
        enabled=True,
        confirmations_required=1,
        absence_grace_seconds=10.0,
    )
    gate.observe(True, at=1.0)
    grace = gate.observe(False, at=2.0)
    assert grace["state"] == "absence_grace"
    assert gate.allows_audio(at=10.9) is True
    assert gate.allows_audio(at=11.1) is False
    print("  grace: one missed frame cannot abruptly mute a person")


def test_conversation_latch_keeps_active_turn_open() -> None:
    gate = PresenceHumanGate(
        enabled=True,
        confirmations_required=1,
        absence_grace_seconds=2.0,
        conversation_latch_seconds=30.0,
    )
    gate.observe(True, at=1.0)
    gate.note_interaction(at=1.5)
    assert gate.snapshot(at=20.0)["state"] == "conversation_latch"
    assert gate.allows_audio(at=31.4) is True
    assert gate.allows_audio(at=31.6) is False
    print("  latch: an active conversation survives brief camera loss")


def test_reset_clears_stale_presence_fail_closed() -> None:
    gate = PresenceHumanGate(enabled=True, confirmations_required=1)
    gate.observe(True, at=1.0)
    assert gate.allows_audio(at=1.0) is True
    gate.reset(enabled=True)
    snapshot = gate.snapshot(at=2.0)
    assert snapshot["state"] == "absent"
    assert snapshot["audio_allowed"] is False
    print("  reset: stale camera evidence is cleared fail-closed")


def main() -> None:
    tests = (
        test_disabled_gate_is_backward_compatible,
        test_two_detections_confirm_presence,
        test_low_confidence_does_not_open_gate,
        test_absence_grace_prevents_flapping,
        test_conversation_latch_keeps_active_turn_open,
        test_reset_clears_stale_presence_fail_closed,
    )
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_presence_human_gate: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
