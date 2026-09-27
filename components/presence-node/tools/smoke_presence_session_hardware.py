"""Physical acceptance smoke for the Presence session and PCM downlink.

The test creates an ephemeral bearer token in memory, provisions it over the
physical USB-UART link, verifies bounded PCM playback, exercises Core restart
and board reboot reconnects, and then clears the temporary URI/token from NVS.
"""

from __future__ import annotations

import argparse
import asyncio
import math
from pathlib import Path
import secrets
import struct
import subprocess
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = PROJECT_ROOT.parent
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))


from nana.runtime.presence_session_server import (
    PresenceCaptureOutcome,
    PresenceSessionServer,
)

from provision_presence_session import build_set_command, serial_session, validate_uri


DEFAULT_ESPTOOL_PYTHON = Path(sys.executable)


async def _start_server(
    *, host: str, port: int, token: str
) -> tuple[PresenceSessionServer, asyncio.Task[None]]:
    async def reject_unsolicited_capture(_capture):
        return PresenceCaptureOutcome("skipped", "downlink_smoke_only")

    server = PresenceSessionServer(
        host=host,
        port=port,
        token=token,
        heartbeat_seconds=1.0,
        timeout_seconds=4.0,
        hello_timeout_seconds=5.0,
        audio_uplink_handler=reject_unsolicited_capture,
    )
    task = asyncio.create_task(server.run())
    await server.wait_started()
    if task.done():
        await task
    return server, task


async def _stop_server(
    server: PresenceSessionServer | None,
    task: asyncio.Task[None] | None,
) -> None:
    if server is not None:
        server.stop()
    if task is not None:
        await asyncio.wait_for(task, timeout=5.0)


async def _wait_for(
    predicate,
    *,
    timeout: float,
    description: str,
) -> dict:
    deadline = time.monotonic() + timeout
    last_snapshot: dict = {}
    while time.monotonic() < deadline:
        last_snapshot = predicate()
        if last_snapshot.get("accepted"):
            return last_snapshot
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {description}: {last_snapshot}")


def _healthy_snapshot(
    server: PresenceSessionServer,
    *,
    minimum_connections: int = 1,
    minimum_heartbeats: int = 3,
) -> dict:
    snapshot = server.snapshot()
    capabilities = snapshot.get("last_capabilities") or {}
    snapshot["accepted"] = (
        snapshot.get("active_sessions") == 1
        and int(snapshot.get("connections_total", 0)) >= minimum_connections
        and int(snapshot.get("heartbeats", 0)) >= minimum_heartbeats
        and snapshot.get("last_profile") == "presence_session"
        and capabilities.get("audio_uplink") is True
        and capabilities.get("audio_downlink") is True
        and capabilities.get("audio_downlink_stream") is True
        and capabilities.get("camera") is True
        and capabilities.get("display") is True
    )
    return snapshot


def _build_test_tone(
    *,
    sample_rate: int,
    frequency_hz: float,
    duration_seconds: float,
    gain: float,
) -> bytes:
    if sample_rate != 16000:
        raise ValueError("hardware smoke requires 16000 Hz PCM")
    if not 100.0 <= frequency_hz <= 2000.0:
        raise ValueError("test tone frequency must be between 100 and 2000 Hz")
    if not 0.2 <= duration_seconds <= 3.0:
        raise ValueError("test tone duration must be between 0.2 and 3 seconds")
    if not 0.01 <= gain <= 0.25:
        raise ValueError("test tone gain must be between 0.01 and 0.25")

    sample_count = round(sample_rate * duration_seconds)
    fade_samples = min(round(sample_rate * 0.02), sample_count // 2)
    pcm = bytearray(sample_count * 2)
    for index in range(sample_count):
        envelope = 1.0
        if index < fade_samples:
            envelope = index / max(1, fade_samples)
        elif index >= sample_count - fade_samples:
            envelope = (sample_count - 1 - index) / max(1, fade_samples)
        sample = round(
            32767.0
            * gain
            * max(0.0, envelope)
            * math.sin(2.0 * math.pi * frequency_hz * index / sample_rate)
        )
        struct.pack_into("<h", pcm, index * 2, sample)
    return bytes(pcm)


def _hard_reset_board(*, python: Path, port: str) -> None:
    completed = subprocess.run(
        [
            str(python),
            "-m",
            "esptool",
            "--chip",
            "esp32s3",
            "--port",
            port,
            "--after",
            "hard_reset",
            "chip_id",
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        check=False,
        timeout=30.0,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"esptool board reset failed: {detail}")


async def _run(args: argparse.Namespace) -> None:
    uri = validate_uri(args.uri)
    token = secrets.token_urlsafe(32)
    set_command = build_set_command(uri, token)
    clear_command = b"NANA_SESSION_CLEAR\n"
    server: PresenceSessionServer | None = None
    task: asyncio.Task[None] | None = None
    config_cleared = False

    try:
        print("[1/5] Provision ephemeral session credentials and connect")
        server, task = await _start_server(
            host=args.host,
            port=args.server_port,
            token=token,
        )
        result = await asyncio.to_thread(
            serial_session,
            port=args.serial_port,
            command=set_command,
            timeout=args.serial_timeout,
        )
        if result != 0:
            raise RuntimeError("board did not accept ephemeral session config")
        first = await _wait_for(
            lambda: _healthy_snapshot(server),
            timeout=args.connect_timeout,
            description="initial HELLO/WELCOME and heartbeat",
        )
        device_id = first.get("last_device_id")
        print(
            "  PASS initial | "
            f"device={device_id} | heartbeats={first['heartbeats']}"
        )

        print("[2/5] Play bounded PCM and require an exact clean drain")
        pcm = _build_test_tone(
            sample_rate=16000,
            frequency_hz=args.audio_hz,
            duration_seconds=args.audio_seconds,
            gain=args.audio_gain,
        )
        playback = await server.play_pcm16_async(pcm, sample_rate=16000)
        if not playback.completed:
            raise RuntimeError(f"PCM playback did not complete: {playback}")
        print(
            "  PASS audio | "
            f"stream={playback.stream_id} | bytes={playback.bytes_played} | "
            f"queue_high_water={playback.queue_high_water} | "
            f"underruns={playback.underruns} | elapsed={playback.elapsed_ms:.1f}ms"
        )

        print("[3/5] Restart the Core listener and require reconnect")
        await _stop_server(server, task)
        server = None
        task = None
        await asyncio.sleep(args.core_restart_delay)
        server, task = await _start_server(
            host=args.host,
            port=args.server_port,
            token=token,
        )
        restarted = await _wait_for(
            lambda: _healthy_snapshot(server),
            timeout=args.connect_timeout,
            description="reconnect after Core restart",
        )
        if restarted.get("last_device_id") != device_id:
            raise RuntimeError("device ID changed after Core restart")
        print(
            "  PASS Core restart | "
            f"heartbeats={restarted['heartbeats']}"
        )

        print("[4/5] Hard-reset the ESP32 and require reconnect")
        before_connections = int(server.snapshot().get("connections_total", 0))
        before_heartbeats = int(server.snapshot().get("heartbeats", 0))
        await asyncio.to_thread(
            _hard_reset_board,
            python=args.esptool_python,
            port=args.serial_port,
        )
        rebooted = await _wait_for(
            lambda: _healthy_snapshot(
                server,
                minimum_connections=before_connections + 1,
                minimum_heartbeats=before_heartbeats + 3,
            ),
            timeout=args.connect_timeout,
            description="reconnect after ESP32 reboot",
        )
        if rebooted.get("last_device_id") != device_id:
            raise RuntimeError("device ID changed after board reboot")
        print(
            "  PASS board reboot | "
            f"connections={rebooted['connections_total']} | "
            f"heartbeats={rebooted['heartbeats']}"
        )

        print("[5/5] Clear ephemeral credentials from NVS")
        result = await asyncio.to_thread(
            serial_session,
            port=args.serial_port,
            command=clear_command,
            timeout=args.serial_timeout,
        )
        if result != 0:
            raise RuntimeError("board did not clear ephemeral session config")
        config_cleared = True
        print("  PASS cleanup | token=cleared")
    finally:
        if not config_cleared:
            try:
                cleanup_result = await asyncio.to_thread(
                    serial_session,
                    port=args.serial_port,
                    command=clear_command,
                    timeout=10.0,
                )
                if cleanup_result == 0:
                    config_cleared = True
                    print("Cleanup after failure: token=cleared")
                else:
                    print("Cleanup warning: board did not acknowledge NVS clear")
            except Exception as exc:
                print(f"Cleanup warning: {type(exc).__name__}: {exc}")
        await _stop_server(server, task)

    print("smoke_presence_session_hardware: PASS (5/5)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Physically accept Presence session hello, heartbeat, bounded PCM, "
            "Core restart, board reboot, and NVS cleanup with an ephemeral token."
        )
    )
    parser.add_argument("--serial-port", default="COM14")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--server-port", type=int, default=8765)
    parser.add_argument(
        "--uri",
        default="ws://<LOCAL_IP>:8765/presence/v1",
    )
    parser.add_argument("--serial-timeout", type=float, default=45.0)
    parser.add_argument("--connect-timeout", type=float, default=45.0)
    parser.add_argument("--core-restart-delay", type=float, default=3.0)
    parser.add_argument("--audio-hz", type=float, default=660.0)
    parser.add_argument("--audio-seconds", type=float, default=1.0)
    parser.add_argument("--audio-gain", type=float, default=0.08)
    parser.add_argument(
        "--esptool-python",
        type=Path,
        default=DEFAULT_ESPTOOL_PYTHON,
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not args.esptool_python.is_file():
        raise SystemExit(f"ESP-IDF Python not found: {args.esptool_python}")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
