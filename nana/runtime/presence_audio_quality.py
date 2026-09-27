"""Acoustic quality gate for audio captured by a Nana Presence Node."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np


@dataclass(frozen=True)
class PresenceAudioQuality:
    accepted: bool
    score: float
    reason: str
    backend: str
    duration_ms: float
    speech_ms: float
    speech_ratio: float
    snr_db: float
    rms_dbfs: float
    peak_dbfs: float
    clipping_ratio: float
    dc_offset: float
    voice_band_ratio: float


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _dbfs(rms: float) -> float:
    if rms <= 1e-12:
        return -120.0
    return 20.0 * math.log10(rms)


def _smooth_speech_flags(flags: np.ndarray) -> np.ndarray:
    """Remove isolated hits and fill a single-frame gap between speech frames."""

    if flags.size < 3:
        return flags.copy()
    smoothed = flags.copy()
    for index in range(1, flags.size - 1):
        if not flags[index] and flags[index - 1] and flags[index + 1]:
            smoothed[index] = True
    original = smoothed.copy()
    for index in range(smoothed.size):
        left = index > 0 and original[index - 1]
        right = index + 1 < original.size and original[index + 1]
        if original[index] and not left and not right:
            smoothed[index] = False
    return smoothed


def _webrtc_flags(frames: np.ndarray, frame_bytes: list[bytes], sample_rate: int) -> tuple[np.ndarray, str]:
    try:
        import webrtcvad
    except ImportError:
        return np.ones(frames.shape[0], dtype=bool), "energy_spectral"

    vad = webrtcvad.Vad(3)
    flags = np.asarray(
        [vad.is_speech(payload, sample_rate) for payload in frame_bytes],
        dtype=bool,
    )
    return _smooth_speech_flags(flags), "webrtcvad3+energy_spectral"


def evaluate_presence_audio(
    pcm: bytes,
    *,
    sample_rate: int = 16000,
    sample_width: int = 2,
    firmware_noise_dbfs: float | None = None,
) -> PresenceAudioQuality:
    """Return a deterministic audio-quality decision without calling STT."""

    if sample_width != 2 or sample_rate not in (8000, 16000, 32000, 48000):
        raise ValueError("quality gate requires WebRTC-compatible s16le mono PCM")
    if not pcm or len(pcm) % sample_width:
        return PresenceAudioQuality(
            False, 0.0, "invalid_pcm", "none", 0.0, 0.0, 0.0,
            -120.0, -120.0, -120.0, 0.0, 0.0, 0.0,
        )

    samples_i16 = np.frombuffer(pcm, dtype="<i2")
    samples = samples_i16.astype(np.float32) / 32768.0
    duration_ms = (samples.size * 1000.0) / sample_rate
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    rms_dbfs = _dbfs(rms)
    peak = float(np.max(np.abs(samples)))
    peak_dbfs = _dbfs(peak)
    clipping_ratio = float(np.mean(np.abs(samples_i16.astype(np.int32)) >= 32700))
    dc_offset = float(abs(np.mean(samples, dtype=np.float64)))

    frame_ms = 30
    frame_samples = (sample_rate * frame_ms) // 1000
    frame_count = samples_i16.size // frame_samples
    if frame_count == 0:
        return PresenceAudioQuality(
            False, 0.0, "too_short", "none", duration_ms, 0.0, 0.0,
            -120.0, rms_dbfs, peak_dbfs, clipping_ratio, dc_offset, 0.0,
        )

    trimmed_i16 = samples_i16[: frame_count * frame_samples]
    trimmed = samples[: frame_count * frame_samples]
    frames_i16 = trimmed_i16.reshape(frame_count, frame_samples)
    frames = trimmed.reshape(frame_count, frame_samples)
    frame_rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
    frame_dbfs = np.asarray([_dbfs(float(value)) for value in frame_rms])

    measured_noise_dbfs = float(np.percentile(frame_dbfs, 20.0))
    if firmware_noise_dbfs is None or not math.isfinite(firmware_noise_dbfs):
        noise_dbfs = measured_noise_dbfs
    else:
        # The firmware calibration is useful, but a stale value must not make
        # a newly noisy room look like high-SNR speech.
        noise_dbfs = max(float(firmware_noise_dbfs), measured_noise_dbfs - 3.0)
    energy_threshold_dbfs = max(noise_dbfs + 6.0, -45.0)
    energy_flags = frame_dbfs >= energy_threshold_dbfs

    frame_bytes = [frame.tobytes() for frame in frames_i16]
    vad_flags, backend = _webrtc_flags(frames, frame_bytes, sample_rate)

    window = np.hanning(frame_samples).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(frames * window, axis=1)) ** 2
    frequencies = np.fft.rfftfreq(frame_samples, d=1.0 / sample_rate)
    total_energy = np.sum(spectrum[:, frequencies >= 40.0], axis=1) + 1e-12
    voice_energy = np.sum(
        spectrum[:, (frequencies >= 100.0) & (frequencies <= 3800.0)],
        axis=1,
    )
    frame_voice_ratio = voice_energy / total_energy
    spectral_flags = frame_voice_ratio >= 0.35

    speech_flags = _smooth_speech_flags(vad_flags & energy_flags & spectral_flags)
    speech_frames = int(np.count_nonzero(speech_flags))
    speech_ms = speech_frames * float(frame_ms)
    speech_ratio = speech_frames / float(frame_count)
    if speech_frames:
        speech_dbfs = float(np.median(frame_dbfs[speech_flags]))
        voice_band_ratio = float(np.mean(frame_voice_ratio[speech_flags]))
    else:
        speech_dbfs = -120.0
        voice_band_ratio = float(np.mean(frame_voice_ratio[energy_flags])) if np.any(energy_flags) else 0.0
    snr_db = speech_dbfs - noise_dbfs if speech_frames else -120.0

    score = (
        0.34 * _clamp((speech_ms - 180.0) / 720.0)
        + 0.18 * _clamp((speech_ratio - 0.06) / 0.34)
        + 0.28 * _clamp((snr_db - 3.0) / 15.0)
        + 0.15 * _clamp((voice_band_ratio - 0.30) / 0.45)
        + 0.05 * _clamp(1.0 - (clipping_ratio / 0.03))
    )

    reason = "accepted"
    if duration_ms < 330.0:
        reason = "too_short"
    elif peak_dbfs < -48.0:
        reason = "too_quiet"
    elif clipping_ratio > 0.03:
        reason = "clipping"
    elif dc_offset > 0.12:
        reason = "dc_offset"
    elif speech_ms < 240.0:
        reason = "no_stable_speech"
    elif speech_ratio < 0.08:
        reason = "low_speech_ratio"
    elif snr_db < 5.0:
        reason = "low_snr"
    elif voice_band_ratio < 0.35:
        reason = "non_voice_spectrum"
    elif score < 0.45:
        reason = "low_audio_confidence"

    return PresenceAudioQuality(
        accepted=reason == "accepted",
        score=round(score, 4),
        reason=reason,
        backend=backend,
        duration_ms=round(duration_ms, 1),
        speech_ms=round(speech_ms, 1),
        speech_ratio=round(speech_ratio, 4),
        snr_db=round(snr_db, 2),
        rms_dbfs=round(rms_dbfs, 2),
        peak_dbfs=round(peak_dbfs, 2),
        clipping_ratio=round(clipping_ratio, 6),
        dc_offset=round(dc_offset, 6),
        voice_band_ratio=round(voice_band_ratio, 4),
    )


def quality_as_dict(result: PresenceAudioQuality) -> dict[str, Any]:
    return dict(result.__dict__)


__all__ = [
    "PresenceAudioQuality",
    "evaluate_presence_audio",
    "quality_as_dict",
]
