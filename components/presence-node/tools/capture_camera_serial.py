#!/usr/bin/env python3
"""Receive validated JPEG frames from Nana's ESP32-S3 USB-UART stream."""

from __future__ import annotations

import argparse
import base64
import binascii
import re
import time
from pathlib import Path

import serial


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "captures" / "nana_camera_latest.jpg"
BEGIN_PATTERN = re.compile(
    rb"^NANA_FRAME_BEGIN seq=(\d+) bytes=(\d+) base64=(\d+) "
    rb"width=(\d+) height=(\d+) crc32=([0-9a-fA-F]{8})$"
)
END_PATTERN = re.compile(rb"^NANA_FRAME_END seq=(\d+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture JPEG frames from Nana Presence Node over USB-UART."
    )
    parser.add_argument("--port", default="COM14")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument(
        "--frames",
        type=int,
        default=1,
        help="Number of valid frames to save; 0 runs until Ctrl+C.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--keep-all",
        action="store_true",
        help="Also retain each frame beside the latest image.",
    )
    return parser.parse_args()


def open_serial(port: str, baud: int) -> serial.Serial:
    connection = serial.Serial()
    connection.port = port
    connection.baudrate = baud
    connection.timeout = 10
    connection.write_timeout = 10
    connection.dtr = False
    connection.rts = False
    connection.open()
    connection.reset_input_buffer()
    return connection


def receive_frame(connection: serial.Serial) -> tuple[int, int, int, bytes]:
    while True:
        line = connection.readline().strip()
        match = BEGIN_PATTERN.match(line)
        if match is not None:
            break
        if line:
            if len(line) > 240:
                print(
                    "device: skipped partial serial payload "
                    f"bytes={len(line)}"
                )
            else:
                print(f"device: {line.decode('utf-8', errors='replace')}")

    sequence, byte_count, encoded_count, width, height = (
        int(value) for value in match.groups()[:5]
    )
    expected_crc = int(match.group(6), 16)
    encoded = connection.readline().strip()
    end_line = connection.readline().strip()
    end_match = END_PATTERN.match(end_line)
    if end_match is None or int(end_match.group(1)) != sequence:
        raise ValueError(f"frame {sequence}: missing matching end marker")
    if len(encoded) != encoded_count:
        raise ValueError(
            f"frame {sequence}: base64 length {len(encoded)} != {encoded_count}"
        )

    try:
        jpeg = base64.b64decode(encoded, validate=True)
    except binascii.Error as error:
        raise ValueError(f"frame {sequence}: invalid base64: {error}") from error
    if len(jpeg) != byte_count:
        raise ValueError(
            f"frame {sequence}: JPEG length {len(jpeg)} != {byte_count}"
        )
    if not (jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")):
        raise ValueError(f"frame {sequence}: incomplete JPEG markers")
    actual_crc = binascii.crc32(jpeg) & 0xFFFFFFFF
    if actual_crc != expected_crc:
        raise ValueError(
            f"frame {sequence}: CRC32 {actual_crc:08x} != {expected_crc:08x}"
        )
    return sequence, width, height, jpeg


def main() -> int:
    args = parse_args()
    if args.frames < 0:
        raise SystemExit("--frames must be zero or greater")
    args.output.parent.mkdir(parents=True, exist_ok=True)

    saved = 0
    print(f"camera_serial: waiting on {args.port} at {args.baud} baud")
    with open_serial(args.port, args.baud) as connection:
        while args.frames == 0 or saved < args.frames:
            try:
                sequence, width, height, jpeg = receive_frame(connection)
            except ValueError as error:
                print(f"camera_serial: rejected: {error}")
                continue

            args.output.write_bytes(jpeg)
            if args.keep_all:
                archive = args.output.with_name(
                    f"{args.output.stem}_{sequence:06d}{args.output.suffix}"
                )
                archive.write_bytes(jpeg)
            saved += 1
            print(
                "camera_serial: saved "
                f"seq={sequence} image={width}x{height} bytes={len(jpeg)} "
                f"path={args.output}"
            )

    print(f"camera_serial: complete frames={saved}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\ncamera_serial: stopped")
