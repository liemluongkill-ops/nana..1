"""Regression smoke for the bounded CLI camera-request queue."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nana.cli.presence_commands import _capture_camera_frame_when_ready


class FakeCameraError(RuntimeError):
    pass


class SequencedServer:
    def __init__(self) -> None:
        self.availability_checks = 0
        self.capture_attempts = 0

    def camera_available(self) -> bool:
        self.availability_checks += 1
        return self.availability_checks >= 3

    async def capture_camera_jpeg_async(self):
        self.capture_attempts += 1
        if self.capture_attempts == 1:
            raise FakeCameraError("camera cannot start during an audio turn")
        if self.capture_attempts == 2:
            raise FakeCameraError("camera cannot start during playback")
        return "frame"


class UnavailableServer:
    def camera_available(self) -> bool:
        return False


class BrokenServer:
    def camera_available(self) -> bool:
        return True

    async def capture_camera_jpeg_async(self):
        raise FakeCameraError("camera_start_failed_ESP_FAIL_0xffffffff")


async def _run() -> None:
    queued = SequencedServer()
    frame = await _capture_camera_frame_when_ready(
        queued,
        camera_error_type=FakeCameraError,
        wait_seconds=1.0,
        retry_seconds=0.001,
    )
    assert frame == "frame"
    assert queued.capture_attempts == 3
    print("  queued request: node wait plus busy audio/playback retries PASS")

    try:
        await _capture_camera_frame_when_ready(
            UnavailableServer(),
            camera_error_type=FakeCameraError,
            wait_seconds=0.01,
            retry_seconds=0.001,
        )
        raise AssertionError("unavailable camera request did not time out")
    except TimeoutError as exc:
        assert "camera-capable node" in str(exc)
    print("  unavailable node: bounded timeout PASS")

    try:
        await _capture_camera_frame_when_ready(
            BrokenServer(),
            camera_error_type=FakeCameraError,
            wait_seconds=1.0,
            retry_seconds=0.001,
        )
        raise AssertionError("real camera failure was incorrectly retried")
    except FakeCameraError as exc:
        assert "camera_start_failed" in str(exc)
    print("  real camera failure: fail-fast PASS")


if __name__ == "__main__":
    asyncio.run(_run())
    print("smoke_presence_camera_command_queue: PASS (3/3)")
