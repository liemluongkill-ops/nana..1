#!/usr/bin/env python3
"""Capture one isolated INMP441 bench utterance over maintenance USB-UART."""

from __future__ import annotations

import argparse
import array
import binascii
import math
import re
import sys
import time
import wave
from pathlib import Path

import serial


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "captures" / "nana_mic_vad_test.wav"
STREAM_BEGIN_PATTERN = re.compile(
    rb"^NANA_AUDIO_STREAM_BEGIN seq=(\d+) rate=(\d+) channels=(\d+) "
    rb"bits=(\d+) noise_dbfs_x10=(-?\d+) start_dbfs_x10=(-?\d+)$"
)
CHUNK_PATTERN = re.compile(
    rb"^NANA_AUDIO_CHUNK seq=(\d+) index=(\d+) bytes=(\d+)$"
)
STREAM_END_PATTERN = re.compile(
    rb"^NANA_AUDIO_STREAM_END seq=(\d+) chunks=(\d+) bytes=(\d+) "
    rb"samples=(\d+) crc32=([0-9a-fA-F]{8}) duration_ms=(\d+) "
    rb"trim_samples=(\d+) reason=([a-z_]+)$"
)
STREAM_ABORT_PATTERN = re.compile(
    rb"^NANA_AUDIO_STREAM_ABORT seq=(\d+) reason=([a-z0-9_:-]+)$"
)
MAX_CAPTURE_BYTES = 20 * 16000 * 2
MAX_CHUNK_BYTES = 16 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Save one speech-only INMP441 capture as a validated WAV file."
    )
    parser.add_argument("--port", default="COM14")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=120.0,
        help="Maximum time to wait for speech to begin.",
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not hard-reset the ESP32 after opening the serial port.",
    )
    return parser.parse_args()


def open_serial(port: str, baud: int, reset_device: bool) -> serial.Serial:
    connection = serial.Serial()
    connection.port = port
    connection.baudrate = baud
    connection.timeout = 1
    connection.write_timeout = 10
    connection.dtr = False
    connection.rts = False
    connection.open()
    connection.reset_input_buffer()
    if reset_device:
        # CH343 RTS drives ESP32-S3 EN active-low; DTR remains deasserted so
        # GPIO0 stays high and the board boots the application, not download mode.
        connection.rts = True
        time.sleep(0.1)
        connection.rts = False
        time.sleep(0.25)
    return connection


def read_exact(
    connection: serial.Serial, byte_count: int, deadline: float
) -> bytes:
    payload = bytearray()
    while len(payload) < byte_count:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"PCM transfer timed out at {len(payload)}/{byte_count} bytes"
            )
        chunk = connection.read(min(4096, byte_count - len(payload)))
        if chunk:
            payload.extend(chunk)
    return bytes(payload)


def wait_for_header(
    connection: serial.Serial, wait_seconds: float
) -> re.Match[bytes]:
    armed_reported = False
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        line = connection.readline().strip()
        if not line:
            continue

        match = STREAM_BEGIN_PATTERN.match(line)
        if match is not None:
            return match

        decoded = line.decode("utf-8", errors="replace")
        if "VAD ARMED" in decoded:
            # A discarded short trigger starts a fresh listening window on the
            # device, so the receiver must grant that window a fresh deadline.
            deadline = time.monotonic() + wait_seconds
            if armed_reported:
                print(
                    "microphone_vad: ARMED again - speak continuously for 3-5 "
                    "seconds, then stay silent",
                    flush=True,
                )
            else:
                print(
                    "microphone_vad: ARMED - speak continuously for 3-5 seconds, "
                    "then stay silent",
                    flush=True,
                )
                armed_reported = True
        elif "SPEECH START" in decoded:
            print("microphone_vad: speech detected; recording", flush=True)
        elif "SPEECH STOP" in decoded:
            print("microphone_vad: silence detected; recording stopped", flush=True)
        else:
            print(f"device: {decoded}", flush=True)

    raise TimeoutError("No audio header received before the wait deadline")


def receive_capture(
    connection: serial.Serial, wait_seconds: float
) -> tuple[bytes, int, int, int, float, float, str]:
    header = wait_for_header(connection, wait_seconds)
    sequence = int(header.group(1))
    sample_rate = int(header.group(2))
    channels = int(header.group(3))
    bits = int(header.group(4))
    noise_dbfs = int(header.group(5)) / 10.0
    start_dbfs = int(header.group(6)) / 10.0

    if channels != 1 or bits != 16:
        raise ValueError(f"unsupported PCM format: channels={channels} bits={bits}")
    if sample_rate != 16000:
        raise ValueError(f"unsupported sample rate: {sample_rate}")

    payload = bytearray()
    expected_chunk_index = 0
    idle_deadline = time.monotonic() + 5.0
    while True:
        if time.monotonic() >= idle_deadline:
            raise TimeoutError(f"capture {sequence}: stream stalled")
        line = connection.readline().strip()
        if not line:
            continue

        chunk_match = CHUNK_PATTERN.match(line)
        if chunk_match is not None:
            chunk_sequence = int(chunk_match.group(1))
            chunk_index = int(chunk_match.group(2))
            chunk_bytes = int(chunk_match.group(3))
            if (
                chunk_sequence != sequence
                or chunk_index != expected_chunk_index
                or chunk_bytes <= 0
                or chunk_bytes > MAX_CHUNK_BYTES
                or chunk_bytes % 2
                or len(payload) + chunk_bytes > MAX_CAPTURE_BYTES
            ):
                raise ValueError(f"capture {sequence}: invalid stream chunk")
            transfer_seconds = max(
                2.0,
                chunk_bytes * 10.0 / connection.baudrate + 1.0,
            )
            payload.extend(
                read_exact(
                    connection,
                    chunk_bytes,
                    time.monotonic() + transfer_seconds,
                )
            )
            expected_chunk_index += 1
            idle_deadline = time.monotonic() + 5.0
            continue

        abort_match = STREAM_ABORT_PATTERN.match(line)
        if abort_match is not None and int(abort_match.group(1)) == sequence:
            reason = abort_match.group(2).decode("ascii", errors="replace")
            raise ValueError(f"capture {sequence}: stream aborted ({reason})")

        trailer_match = STREAM_END_PATTERN.match(line)
        if trailer_match is None:
            decoded = line.decode("utf-8", errors="replace")
            raise ValueError(
                f"capture {sequence}: unexpected stream frame ({decoded})"
            )
        break

    trailer_sequence = int(trailer_match.group(1))
    chunk_count = int(trailer_match.group(2))
    byte_count = int(trailer_match.group(3))
    sample_count = int(trailer_match.group(4))
    expected_crc = int(trailer_match.group(5), 16)
    duration_ms = int(trailer_match.group(6))
    trim_samples = int(trailer_match.group(7))
    stop_reason = trailer_match.group(8).decode("ascii")
    if (
        trailer_sequence != sequence
        or chunk_count != expected_chunk_index
        or byte_count != len(payload)
        or sample_count * channels * (bits // 8) != byte_count
        or trim_samples > sample_count
        or abs(duration_ms - sample_count * 1000 // sample_rate) > 2
    ):
        raise ValueError(f"capture {sequence}: inconsistent stream trailer")

    actual_crc = binascii.crc32(payload) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise ValueError(
            f"capture {sequence}: CRC32 {actual_crc:08x} != {expected_crc:08x}"
        )
    if trim_samples:
        del payload[-trim_samples * channels * (bits // 8) :]
    return (
        bytes(payload),
        sample_rate,
        channels,
        bits,
        noise_dbfs,
        start_dbfs,
        stop_reason,
    )


def pcm_metrics(payload: bytes) -> tuple[float, float, float, int, int]:
    samples = array.array("h")
    samples.frombytes(payload)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return -120.0, -120.0, 0.0, 0, 0

    count = len(samples)
    sum_samples = sum(samples)
    sum_squares = sum(sample * sample for sample in samples)
    peak = max(abs(sample) for sample in samples)
    clipped = sum(1 for sample in samples if abs(sample) >= 32760)
    rms = math.sqrt(sum_squares / count)
    rms_dbfs = -120.0 if rms == 0 else 20.0 * math.log10(rms / 32767.0)
    peak_dbfs = -120.0 if peak == 0 else 20.0 * math.log10(peak / 32767.0)
    dc = sum_samples / count
    return rms_dbfs, peak_dbfs, dc, clipped, count


def write_wav(
    output: Path,
    payload: bytes,
    sample_rate: int,
    channels: int,
    bits: int,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(bits // 8)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(payload)


def main() -> int:
    args = parse_args()
    if args.wait_seconds <= 0:
        raise SystemExit("--wait-seconds must be greater than zero")

    print(
        f"microphone_vad: waiting on {args.port} at {args.baud} baud",
        flush=True,
    )
    with open_serial(args.port, args.baud, not args.no_reset) as connection:
        (
            payload,
            sample_rate,
            channels,
            bits,
            noise_dbfs,
            start_dbfs,
            stop_reason,
        ) = receive_capture(connection, args.wait_seconds)

    write_wav(args.output, payload, sample_rate, channels, bits)
    rms_dbfs, peak_dbfs, dc, clipped, sample_count = pcm_metrics(payload)
    duration_seconds = sample_count / sample_rate
    print(
        "microphone_vad: saved "
        f"duration={duration_seconds:.3f}s bytes={len(payload)} "
        f"stop={stop_reason} "
        f"noise={noise_dbfs:.1f}dBFS gate={start_dbfs:.1f}dBFS "
        f"rms={rms_dbfs:.1f}dBFS peak={peak_dbfs:.1f}dBFS "
        f"dc={dc:.1f} clipped={clipped} path={args.output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (TimeoutError, ValueError, serial.SerialException) as error:
        print(f"microphone_vad: ERROR: {error}", file=sys.stderr, flush=True)
        raise SystemExit(1) from error
    except KeyboardInterrupt:
        print("\nmicrophone_vad: stopped", flush=True)
