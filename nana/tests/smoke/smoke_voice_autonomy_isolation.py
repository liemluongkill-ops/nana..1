"""Regression for autonomy callbacks cancelling private voice playback."""

from __future__ import annotations

import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nana.cli import app
from nana.voice.engine import VoiceEngine


class FakeLipsync:
    def __init__(self):
        self.stop_count = 0

    def stop(self):
        self.stop_count += 1


def _engine(*, speaking=False, listening=False, lipsync=None):
    voice = VoiceEngine.__new__(VoiceEngine)
    voice.state_lock = threading.Lock()
    voice.lipsync = lipsync or FakeLipsync()
    voice.state = {
        "speaking": speaking,
        "listening": listening,
        "idle_lipsync_stop_applied_total": 0,
        "idle_lipsync_stop_skipped_total": 0,
    }
    return voice


def test_active_private_voice_cannot_be_stopped_by_autonomy():
    voice = _engine(speaking=True)

    result = voice.stop_lipsync_if_idle()

    assert result is False
    assert voice.lipsync.stop_count == 0
    assert voice.state["idle_lipsync_stop_skipped_total"] == 1


def test_active_microphone_cannot_be_stopped_by_autonomy():
    voice = _engine(listening=True)

    result = voice.stop_lipsync_if_idle()

    assert result is False
    assert voice.lipsync.stop_count == 0


def test_idle_lipsync_can_still_be_settled():
    voice = _engine()

    result = voice.stop_lipsync_if_idle()

    assert result is True
    assert voice.lipsync.stop_count == 1
    assert voice.state["idle_lipsync_stop_applied_total"] == 1


def test_app_autonomy_backend_uses_guarded_stop():
    voice = _engine(speaking=True)

    app._real_autonomy_lipsync_stop(voice)

    assert voice.lipsync.stop_count == 0
    assert voice.state["idle_lipsync_stop_skipped_total"] == 1


def test_idle_check_and_stop_are_atomic_with_voice_start():
    stop_entered = threading.Event()
    release_stop = threading.Event()
    voice_started = threading.Event()

    class BlockingLipsync(FakeLipsync):
        def stop(self):
            stop_entered.set()
            release_stop.wait(timeout=1.0)
            super().stop()

    voice = _engine(lipsync=BlockingLipsync())
    stop_thread = threading.Thread(target=voice.stop_lipsync_if_idle)

    def start_voice():
        voice._update_state(speaking=True)
        voice_started.set()

    start_thread = threading.Thread(target=start_voice)
    stop_thread.start()
    assert stop_entered.wait(timeout=1.0)
    start_thread.start()
    assert not voice_started.wait(timeout=0.05)
    release_stop.set()
    stop_thread.join(timeout=1.0)
    start_thread.join(timeout=1.0)

    assert not stop_thread.is_alive()
    assert not start_thread.is_alive()
    assert voice.lipsync.stop_count == 1
    assert voice.state["speaking"] is True


def test_legacy_backend_fallback_skips_active_voice():
    class LegacyVoice:
        def __init__(self):
            self.lipsync = FakeLipsync()

        def snapshot(self):
            return {"speaking": True, "listening": False}

    voice = LegacyVoice()
    app._real_autonomy_lipsync_stop(voice)
    assert voice.lipsync.stop_count == 0


def main():
    tests = [
        test_active_private_voice_cannot_be_stopped_by_autonomy,
        test_active_microphone_cannot_be_stopped_by_autonomy,
        test_idle_lipsync_can_still_be_settled,
        test_app_autonomy_backend_uses_guarded_stop,
        test_idle_check_and_stop_are_atomic_with_voice_start,
        test_legacy_backend_fallback_skips_active_voice,
    ]
    failed = 0
    print("Voice Autonomy Isolation Smoke")
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL: {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"Result: {len(tests) - failed}/{len(tests)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
