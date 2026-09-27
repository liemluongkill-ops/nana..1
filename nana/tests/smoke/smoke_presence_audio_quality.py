"""Deterministic acoustic-gate smoke without devices or network calls."""

from __future__ import annotations

from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from nana.runtime.presence_audio_quality import evaluate_presence_audio


SAMPLE_RATE = 16000


def _pcm(signal: np.ndarray) -> bytes:
    return np.clip(signal * 32767.0, -32768, 32767).astype("<i2").tobytes()


def test_voiced_utterance_passes() -> None:
    timeline = np.arange(SAMPLE_RATE * 2, dtype=np.float64) / SAMPLE_RATE
    envelope = 0.55 + (0.45 * np.sin(2.0 * np.pi * 3.2 * timeline))
    signal = (
        0.22 * np.sin(2.0 * np.pi * 140.0 * timeline) * envelope
        + 0.08 * np.sin(2.0 * np.pi * 280.0 * timeline)
        + 0.04 * np.sin(2.0 * np.pi * 420.0 * timeline)
    )
    signal[: SAMPLE_RATE // 4] = 0.0
    signal[-SAMPLE_RATE // 4 :] = 0.0

    result = evaluate_presence_audio(
        _pcm(signal),
        firmware_noise_dbfs=-55.0,
    )
    assert result.accepted is True
    assert result.score >= 0.6
    assert result.speech_ms >= 240.0
    assert result.snr_db >= 5.0
    print(
        f"  voiced: PASS score={result.score:.2f} "
        f"speech={result.speech_ms:.0f}ms snr={result.snr_db:.1f}dB"
    )


def test_stationary_low_frequency_noise_is_rejected() -> None:
    timeline = np.arange(SAMPLE_RATE * 2, dtype=np.float64) / SAMPLE_RATE
    hum = 0.03 * np.sin(2.0 * np.pi * 55.0 * timeline)
    result = evaluate_presence_audio(
        _pcm(hum),
        firmware_noise_dbfs=-35.0,
    )
    assert result.accepted is False
    assert result.reason in {"no_stable_speech", "non_voice_spectrum"}
    print(f"  hum: REJECT reason={result.reason}")


def test_silence_is_rejected() -> None:
    result = evaluate_presence_audio(b"\x00\x00" * SAMPLE_RATE)
    assert result.accepted is False
    assert result.reason in {"too_quiet", "no_stable_speech"}
    print(f"  silence: REJECT reason={result.reason}")


def main() -> None:
    tests = (
        test_voiced_utterance_passes,
        test_stationary_low_frequency_noise_is_rejected,
        test_silence_is_rejected,
    )
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_presence_audio_quality: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
