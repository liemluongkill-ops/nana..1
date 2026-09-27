"""Smoke tests for voice playback boundary smoothing.

Read-only: no live Nana runtime, ElevenLabs, playback, TTS, VTS, OBS,
Discord, memory write, or game input is used.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_edge_fade_moves_boundaries_to_zero():
    print("[Voice Boundary Smoke] Test 1: fades first/last samples toward zero...")
    from nana.voice.lipsync import _smooth_audio_edges

    data = np.ones(1000, dtype=np.float32) * 0.8
    smoothed = _smooth_audio_edges(data, samplerate=1000, fade_ms=10.0)

    assert smoothed.shape == data.shape, smoothed.shape
    assert smoothed.dtype == np.float32, smoothed.dtype
    assert float(smoothed[0]) == 0.0, smoothed[0]
    assert float(smoothed[-1]) == 0.0, smoothed[-1]
    assert 0.7 < float(smoothed[20]) <= 0.800001, smoothed[20]
    assert np.max(np.abs(smoothed)) <= np.max(np.abs(data)) + 1e-6, np.max(np.abs(smoothed))
    print("  PASSED")


def _test_edge_fade_preserves_middle_and_length_for_short_audio():
    print("[Voice Boundary Smoke] Test 2: short audio remains sane...")
    from nana.voice.lipsync import _smooth_audio_edges

    data = np.ones(12, dtype=np.float32)
    smoothed = _smooth_audio_edges(data, samplerate=1000, fade_ms=10.0)

    assert len(smoothed) == len(data), len(smoothed)
    assert float(smoothed[0]) == 0.0, smoothed
    assert float(smoothed[-1]) == 0.0, smoothed
    assert np.max(np.abs(smoothed)) <= 1.0 + 1e-6, smoothed
    assert np.any(smoothed > 0.0), smoothed
    print("  PASSED")


def _test_edge_fade_supports_multichannel_arrays():
    print("[Voice Boundary Smoke] Test 3: multichannel arrays fade on time axis...")
    from nana.voice.lipsync import _smooth_audio_edges

    data = np.ones((100, 2), dtype=np.float32) * 0.5
    smoothed = _smooth_audio_edges(data, samplerate=1000, fade_ms=10.0)

    assert smoothed.shape == data.shape, smoothed.shape
    assert np.all(smoothed[0] == 0.0), smoothed[0]
    assert np.all(smoothed[-1] == 0.0), smoothed[-1]
    assert np.all(smoothed[20] == data[20]), smoothed[20]
    print("  PASSED")


def _test_disabled_or_invalid_fade_is_passthrough():
    print("[Voice Boundary Smoke] Test 4: disabled fade is passthrough...")
    from nana.voice.lipsync import _smooth_audio_edges

    data = np.linspace(-0.5, 0.5, 32, dtype=np.float32)
    smoothed = _smooth_audio_edges(data, samplerate=1000, fade_ms=0.0)

    assert smoothed is data, "disabled fade should not allocate"
    print("  PASSED")


def main():
    print("Voice Playback Boundary Smoothing - Smoke Tests")
    _test_edge_fade_moves_boundaries_to_zero()
    _test_edge_fade_preserves_middle_and_length_for_short_audio()
    _test_edge_fade_supports_multichannel_arrays()
    _test_disabled_or_invalid_fade_is_passthrough()
    print("Voice Playback Boundary Smoothing - PASS (4/4)")


if __name__ == "__main__":
    main()
