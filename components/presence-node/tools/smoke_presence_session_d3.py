"""Physical D3 failure/reconnect acceptance for Presence Session V1.

The suite keeps all media capabilities disabled and the MAX98357 hard-muted.
It uses an ephemeral bearer token, interrupts the board radio through the USB
maintenance channel, injects malformed Core data, verifies bounded reconnect
backoff, and clears the temporary session configuration before exit.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
from http import HTTPStatus
import json
from pathlib import Path
import secrets
import sys
import time
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = PROJECT_ROOT.parent
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))


from nana.runtime.presence_session_server import (
    PROTOCOL_NAME,
    SESSION_PATH,
    PresenceSessionServer,
)

from provision_presence_session import build_set_command, serial_session, validate_uri


EXPECTED_CAPABILITIES = {
    "audio_uplink": False,
    "audio_downlink": False,
    "camera": False,
    "display": False,
}


async def _start_server(
    *, host: str, port: int, token: str
) -> tuple[PresenceSessionServer, asyncio.Task[None]]:
    server = PresenceSessionServer(
        host=host,
        port=port,
        token=token,
        heartbeat_seconds=1.0,
        timeout_seconds=4.0,
        hello_timeout_seconds=5.0,
    )
    task = asyncio.create_task(server.run())
    await server.wait_started()
    if task.done():
        await task
    return server, task


async def _stop_server(
    server: PresenceSessionServer | None,
    task: asyncio.Task[None] | None,
    *,
    repeat: bool = False,
) -> None:
    if server is not None:
        server.stop()
        if repeat:
            server.stop()
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
    if server is not None and repeat:
        server.stop()


async def _wait_for(
    predicate,
    *,
    timeout: float,
    description: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_snapshot: dict[str, Any] = {}
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
    minimum_heartbeats: int = 2,
) -> dict[str, Any]:
    snapshot = server.snapshot()
    snapshot["accepted"] = (
        snapshot.get("active_sessions") == 1
        and int(snapshot.get("connections_total", 0)) >= minimum_connections
        and int(snapshot.get("heartbeats", 0)) >= minimum_heartbeats
        and snapshot.get("last_profile") == "presence_session"
        and snapshot.get("last_capabilities") == EXPECTED_CAPABILITIES
    )
    return snapshot


class _MalformedCore:
    """Authenticated test listener that deliberately violates the protocol."""

    def __init__(self, *, host: str, port: int, token: str) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.started = asyncio.Event()
        self.stop_requested = asyncio.Event()
        self.attempt_times: list[float] = []
        self.errors: list[str] = []

    async def _process_request(self, connection, request):
        if request.path != SESSION_PATH:
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
        authorization = request.headers.get("Authorization", "")
        if not hmac.compare_digest(
            str(authorization), f"Bearer {self.token}"
        ):
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        return None

    async def _handle(self, connection: ServerConnection) -> None:
        try:
            raw_hello = await asyncio.wait_for(connection.recv(), timeout=5.0)
            if not isinstance(raw_hello, str):
                raise AssertionError("board HELLO was not a text frame")
            hello = json.loads(raw_hello)
            if hello.get("type") != "hello" or hello.get("protocol") != PROTOCOL_NAME:
                raise AssertionError(f"invalid board HELLO: {hello}")
            if hello.get("profile") != "presence_session":
                raise AssertionError(f"unexpected board profile: {hello.get('profile')}")
            if hello.get("capabilities") != EXPECTED_CAPABILITIES:
                raise AssertionError(
                    f"media was not hard-disabled: {hello.get('capabilities')}"
                )

            await connection.send("not-json")
            self.attempt_times.append(time.monotonic())
            try:
                await asyncio.wait_for(connection.wait_closed(), timeout=5.0)
            except asyncio.TimeoutError:
                await connection.close(code=1008, reason="fault injection complete")
        except ConnectionClosed:
            pass
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")

    async def run(self) -> None:
        async with serve(
            self._handle,
            self.host,
            self.port,
            process_request=self._process_request,
            compression=None,
            ping_interval=None,
            close_timeout=1.0,
            max_size=4096,
            max_queue=4,
            server_header=None,
        ):
            self.started.set()
            await self.stop_requested.wait()

    def stop(self) -> None:
        self.stop_requested.set()


async def _wait_for_fault_attempts(
    fault: _MalformedCore, *, count: int, timeout: float
) -> list[float]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fault.errors:
            raise RuntimeError(f"malformed Core failed: {fault.errors[-1]}")
        if len(fault.attempt_times) >= count:
            return list(fault.attempt_times)
        await asyncio.sleep(0.1)
    raise TimeoutError(
        f"Timed out waiting for {count} malformed-frame attempts; "
        f"observed={len(fault.attempt_times)}"
    )


async def _run(args: argparse.Namespace) -> None:
    uri = validate_uri(args.uri)
    token = secrets.token_urlsafe(32)
    set_command = build_set_command(uri, token)
    clear_command = b"NANA_SESSION_CLEAR\n"
    wifi_drop_command = b"NANA_SESSION_TEST_WIFI_DROP\n"
    server: PresenceSessionServer | None = None
    server_task: asyncio.Task[None] | None = None
    fault: _MalformedCore | None = None
    fault_task: asyncio.Task[None] | None = None
    config_cleared = False

    try:
        print("[1/6] Provision an ephemeral control-only session")
        server, server_task = await _start_server(
            host=args.host, port=args.server_port, token=token
        )
        result = await asyncio.to_thread(
            serial_session,
            port=args.serial_port,
            command=set_command,
            timeout=args.serial_timeout,
            forbidden_text=token,
        )
        if result != 0:
            raise RuntimeError("board did not accept ephemeral session config")
        baseline = await _wait_for(
            lambda: _healthy_snapshot(server),
            timeout=args.connect_timeout,
            description="initial control-only session",
        )
        device_id = baseline.get("last_device_id")
        print(
            "  PASS baseline | "
            f"device={device_id} | media=false | heartbeats={baseline['heartbeats']}"
        )

        print("[2/6] Interrupt the ESP32 Wi-Fi radio for 3 seconds")
        before_connections = int(server.snapshot().get("connections_total", 0))
        before_heartbeats = int(server.snapshot().get("heartbeats", 0))
        result = await asyncio.to_thread(
            serial_session,
            port=args.serial_port,
            command=wifi_drop_command,
            timeout=args.serial_timeout,
            success_markers=("NANA_SESSION_WIFI_DROP_DONE",),
            send_immediately=True,
            forbidden_text=token,
        )
        if result != 0:
            raise RuntimeError("board did not complete maintenance Wi-Fi interruption")
        wifi_recovered = await _wait_for(
            lambda: _healthy_snapshot(
                server,
                minimum_connections=before_connections + 1,
                minimum_heartbeats=before_heartbeats + 2,
            ),
            timeout=args.connect_timeout,
            description="session recovery after physical Wi-Fi interruption",
        )
        print(
            "  PASS Wi-Fi recovery | "
            f"connections={wifi_recovered['connections_total']} | "
            f"heartbeats={wifi_recovered['heartbeats']}"
        )

        print("[3/6] Remove and restore the Core listener")
        await _stop_server(server, server_task)
        server = None
        server_task = None
        await asyncio.sleep(args.core_outage_seconds)
        server, server_task = await _start_server(
            host=args.host, port=args.server_port, token=token
        )
        core_recovered = await _wait_for(
            lambda: _healthy_snapshot(server),
            timeout=args.connect_timeout,
            description="session recovery after Core loss",
        )
        if core_recovered.get("last_device_id") != device_id:
            raise RuntimeError("device ID changed after Core loss")
        print("  PASS Core recovery | active=1 | newest-session owner retained")

        print("[4/6] Inject malformed Core frames and verify bounded backoff")
        await _stop_server(server, server_task)
        server = None
        server_task = None
        fault = _MalformedCore(
            host=args.host, port=args.server_port, token=token
        )
        fault_task = asyncio.create_task(fault.run())
        await asyncio.wait_for(fault.started.wait(), timeout=5.0)
        attempts = await _wait_for_fault_attempts(
            fault, count=3, timeout=args.connect_timeout
        )
        intervals = [
            attempts[index] - attempts[index - 1]
            for index in range(1, len(attempts))
        ]
        if not (0.75 <= intervals[0] <= 2.5):
            raise RuntimeError(f"first reconnect delay is outside bounds: {intervals}")
        if not (1.5 <= intervals[1] <= 3.75):
            raise RuntimeError(f"second reconnect delay is outside bounds: {intervals}")
        if intervals[1] <= intervals[0] + 0.25:
            raise RuntimeError(f"reconnect backoff did not increase: {intervals}")
        fault.stop()
        await asyncio.wait_for(fault_task, timeout=5.0)
        fault = None
        fault_task = None
        server, server_task = await _start_server(
            host=args.host, port=args.server_port, token=token
        )
        malformed_recovered = await _wait_for(
            lambda: _healthy_snapshot(server),
            timeout=args.connect_timeout,
            description="valid reconnect after malformed Core frames",
        )
        if malformed_recovered.get("last_device_id") != device_id:
            raise RuntimeError("device ID changed after malformed-frame recovery")
        print(
            "  PASS malformed fail-closed | "
            f"backoff={intervals[0]:.2f}s->{intervals[1]:.2f}s | recovered=1"
        )

        print("[5/6] Cancel while reconnecting and clear the ephemeral credential")
        await _stop_server(server, server_task)
        server = None
        server_task = None
        await asyncio.sleep(args.cancel_during_backoff_seconds)
        result = await asyncio.to_thread(
            serial_session,
            port=args.serial_port,
            command=clear_command,
            timeout=args.serial_timeout,
            success_markers=("NANA_SESSION_CLEARED",),
            send_immediately=True,
            forbidden_text=token,
        )
        if result != 0:
            raise RuntimeError("board did not clear the session during reconnect")
        config_cleared = True
        await asyncio.sleep(1.0)
        server, server_task = await _start_server(
            host=args.host, port=args.server_port, token=token
        )
        await asyncio.sleep(args.cancellation_observation_seconds)
        cancelled = server.snapshot()
        if cancelled.get("active_sessions") != 0 or cancelled.get("connections_total") != 0:
            raise RuntimeError(
                "board retained a stale session credential after cancellation: "
                f"{cancelled}"
            )
        print("  PASS cancellation | token=cleared | stale reconnects=0")

        print("[6/6] Repeat Core shutdown and prove listener cleanup")
        await _stop_server(server, server_task, repeat=True)
        stopped = server.snapshot()
        if stopped.get("state") != "stopped" or stopped.get("active_sessions") != 0:
            raise RuntimeError(f"first repeated shutdown leaked state: {stopped}")
        server = None
        server_task = None
        server, server_task = await _start_server(
            host=args.host, port=args.server_port, token=token
        )
        await _stop_server(server, server_task, repeat=True)
        stopped = server.snapshot()
        if stopped.get("state") != "stopped" or stopped.get("active_sessions") != 0:
            raise RuntimeError(f"second repeated shutdown leaked state: {stopped}")
        server = None
        server_task = None
        print("  PASS repeated shutdown | listener rebound | active_sessions=0")
    finally:
        if fault is not None:
            fault.stop()
        if fault_task is not None:
            try:
                await asyncio.wait_for(fault_task, timeout=5.0)
            except Exception as exc:
                print(f"Fault-listener cleanup warning: {type(exc).__name__}: {exc}")
        if not config_cleared:
            try:
                cleanup_result = await asyncio.to_thread(
                    serial_session,
                    port=args.serial_port,
                    command=clear_command,
                    timeout=10.0,
                    success_markers=("NANA_SESSION_CLEARED",),
                    send_immediately=True,
                    forbidden_text=token,
                )
                if cleanup_result == 0:
                    config_cleared = True
                    print("Cleanup after failure: token=cleared")
                else:
                    print("Cleanup warning: board did not acknowledge NVS clear")
            except Exception as exc:
                print(f"Cleanup warning: {type(exc).__name__}: {exc}")
        await _stop_server(server, server_task)

    print("smoke_presence_session_d3: PASS (6/6)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the physical Presence Session D3 Wi-Fi, Core-loss, malformed "
            "frame, cancellation, and repeated-shutdown matrix."
        )
    )
    parser.add_argument("--serial-port", default="COM14")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--server-port", type=int, default=8765)
    parser.add_argument(
        "--uri", default="ws://<LOCAL_IP>:8765/presence/v1"
    )
    parser.add_argument("--serial-timeout", type=float, default=30.0)
    parser.add_argument("--connect-timeout", type=float, default=45.0)
    parser.add_argument("--core-outage-seconds", type=float, default=3.0)
    parser.add_argument(
        "--cancel-during-backoff-seconds", type=float, default=1.2
    )
    parser.add_argument(
        "--cancellation-observation-seconds", type=float, default=5.0
    )
    return parser


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
