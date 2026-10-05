"""Focused loopback smoke for the private Web Chat aiohttp server.

The smoke uses only fake ownership/process state and a loopback client.  It
never invokes the model, memory, voice, browser, OBS or any user service.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
from typing import Any
from uuid import UUID, uuid4

from aiohttp import ClientSession, WSMsgType
from aiohttp.client_exceptions import WSServerHandshakeError


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from nana.runtime.nana_web_ownership import (  # noqa: E402
    EXPECTED_COMMAND,
    NanaWebOwnershipLease,
    NanaWebOwnershipVerifier,
    ProcessSnapshot,
)
from nana.runtime.private_web_chat_auth import HandshakeLedger  # noqa: E402
from nana.runtime.private_web_chat_protocol import (  # noqa: E402
    PROTOCOL_NAME,
    PrivateWebCloseCode,
)
from nana.runtime.private_web_chat_server import (  # noqa: E402
    MAX_WS_PARSER_BYTES,
    PrivateWebChatServer,
)


EPOCH = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CLIENT_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
ORIGIN = "http://127.0.0.1:5174"


class FakeClock:
    def __init__(self, value: float = 0.0) -> None:
        self.value = float(value)

    def __call__(self) -> float:
        return self.value

    def advance(self, amount: float) -> None:
        self.value += float(amount)


class DeterministicRandom:
    def __init__(self) -> None:
        self.counter = 0

    def __call__(self, size: int) -> bytes:
        start = self.counter
        self.counter += size
        return bytes((start + index) % 256 for index in range(size))


class FakeLauncher:
    def __init__(self, lease: NanaWebOwnershipLease | None) -> None:
        self.lease = lease
        self.calls = 0

    def ownership_lease(self) -> NanaWebOwnershipLease | None:
        self.calls += 1
        return self.lease


def make_lease() -> NanaWebOwnershipLease:
    return NanaWebOwnershipLease(
        core_boot_id=EPOCH,
        launch_nonce="a" * 64,
        node_pid=1234,
        node_process_creation_time=100.0,
        app_root=(ROOT / "components" / "nana-app").as_posix(),
        host="127.0.0.1",
        port=5174,
        command_fingerprint="vite-preview-v1",
    )


def make_verifier(launcher: FakeLauncher) -> NanaWebOwnershipVerifier:
    return NanaWebOwnershipVerifier(
        process_snapshot=lambda pid: ProcessSnapshot(pid, 100.0, EXPECTED_COMMAND),
        listener_pid=lambda host, port: 1234,
        current_core_boot_id=lambda: EPOCH,
        active_lease_provider=launcher.ownership_lease,
    )


def build_server(
    *,
    clock: FakeClock | None = None,
    lease: NanaWebOwnershipLease | None = None,
    port: int = 8767,
    mark_unknown=None,
    runtime_state_provider=None,
) -> tuple[PrivateWebChatServer, FakeLauncher, FakeClock]:
    fake_clock = clock or FakeClock(time.monotonic())
    launcher = FakeLauncher(lease if lease is not None else make_lease())
    ledger = HandshakeLedger(
        server_epoch=EPOCH,
        clock=fake_clock,
        random_bytes=DeterministicRandom(),
        max_pending=2,
    )
    server = PrivateWebChatServer(
        server_epoch=EPOCH,
        ledger=ledger,
        launcher=launcher,
        ownership_verifier=make_verifier(launcher),
        clock=fake_clock,
        host="127.0.0.1",
        port=port,
        mark_turn_unknown=mark_unknown,
        runtime_state_provider=runtime_state_provider,
    )
    return server, launcher, fake_clock


async def bootstrap(server: PrivateWebChatServer, *, origin: str = ORIGIN):
    async with ClientSession() as session:
        response = await session.post(
            f"http://127.0.0.1:{server.bound_port}/v1/web-chat/bootstrap",
            headers={"Origin": origin},
        )
        body = await response.json()
        return response.status, body


async def receive_json(ws):
    message = await ws.receive(timeout=2)
    assert message.type == WSMsgType.TEXT, message
    return json.loads(message.data)


async def connect_confirmed(server: PrivateWebChatServer, *, autoclose: bool = True):
    session = ClientSession()
    status, grant = await _bootstrap_with_session(session, server)
    assert status == 200, grant
    ws = await session.ws_connect(
        f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
        headers={"Origin": ORIGIN},
        autoclose=autoclose,
    )
    nonce = "handshake-nonce"
    await ws.send_json(
        {
            "type": "session.hello",
            "protocol": PROTOCOL_NAME,
            "handshake_id": grant["handshake_id"],
            "capability": grant["capability"],
            "handshake_nonce": nonce,
            "client_instance_id": CLIENT_ID,
        }
    )
    welcome = await receive_json(ws)
    assert welcome["type"] == "session.welcome"
    await ws.send_json(
        {
            "type": "session.ready",
            "protocol": PROTOCOL_NAME,
            "server_epoch": EPOCH,
            "provisional_session_id": welcome["provisional_session_id"],
            "handshake_nonce": nonce,
        }
    )
    active = await receive_json(ws)
    assert active["type"] == "session.active"
    await ws.send_json(
        {
            "type": "session.active_ack",
            "protocol": PROTOCOL_NAME,
            "server_epoch": EPOCH,
            "session_id": active["session_id"],
            "handshake_nonce": nonce,
        }
    )
    confirmed = await receive_json(ws)
    assert confirmed["type"] == "session.confirmed"
    return session, ws, grant, welcome, active, confirmed


async def _bootstrap_with_session(session: ClientSession, server: PrivateWebChatServer):
    response = await session.post(
        f"http://127.0.0.1:{server.bound_port}/v1/web-chat/bootstrap",
        headers={"Origin": ORIGIN},
    )
    return response.status, await response.json()


async def test_start_binds_loopback_and_exact_routes() -> None:
    server, _launcher, _clock = build_server()
    assert await server.start() is True
    try:
        assert server.bound_port == 8767
        status, body = await bootstrap(server)
        assert status == 200
        assert body["protocol"] == PROTOCOL_NAME
        assert body["expires_in_ms"] == 10000
    finally:
        await server.stop(0)
    assert server.bound_port == 0


async def test_bootstrap_requires_exact_origin_host_path_and_post() -> None:
    server, _launcher, _clock = build_server()
    assert await server.start() is True
    try:
        async with ClientSession() as session:
            for method, path, headers in (
                ("get", "/v1/web-chat/bootstrap", {"Origin": ORIGIN}),
                ("post", "/v1/web-chat/bootstrap", {"Origin": "http://localhost:5174"}),
                ("post", "/v1/web-chat/bootstrap", {"Origin": ORIGIN, "Host": "localhost:8767"}),
                ("post", "/v1/web-chat/bootstrap/", {"Origin": ORIGIN}),
                ("post", "/v1/web-chat/bootstrap?private=in_url", {"Origin": ORIGIN}),
            ):
                response = await getattr(session, method)(
                    f"http://127.0.0.1:{server.bound_port}{path}", headers=headers
                )
                assert response.status in {400, 403, 404, 405}
        assert server.snapshot()["bootstrap_rejected"] >= 2
    finally:
        await server.stop(0)


async def test_untrusted_preview_returns_503_without_issuing_capability() -> None:
    server, launcher, _clock = build_server(lease=None)
    launcher.lease = None
    assert await server.start() is True
    try:
        status, body = await bootstrap(server)
        assert status == 503
        assert body == {"error": "untrusted_preview_owner"}
        assert server.snapshot()["grants_issued"] == 0
    finally:
        await server.stop(0)


async def test_bootstrap_exposes_only_exact_origin_to_browser() -> None:
    server, _launcher, _clock = build_server()
    assert await server.start() is True
    try:
        async with ClientSession() as session:
            response = await session.post(
                f"http://127.0.0.1:{server.bound_port}/v1/web-chat/bootstrap",
                headers={"Origin": ORIGIN},
            )
            assert response.status == 200
            assert response.headers["Access-Control-Allow-Origin"] == ORIGIN
            assert response.headers["Vary"] == "Origin"
            assert response.headers["Cache-Control"] == "no-store"
            assert "Access-Control-Allow-Credentials" not in response.headers
            await response.read()
    finally:
        await server.stop(0)


async def test_handshake_four_frames_and_application_gate() -> None:
    server, _launcher, _clock = build_server()
    assert await server.start() is True
    try:
        session = ClientSession()
        status, grant = await _bootstrap_with_session(session, server)
        assert status == 200
        ws = await session.ws_connect(
            f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
            headers={"Origin": ORIGIN},
        )
        await ws.send_json(
            {
                "type": "chat.submit",
                "protocol": PROTOCOL_NAME,
                "server_epoch": EPOCH,
            }
        )
        message = await ws.receive(timeout=2)
        assert message.type == WSMsgType.CLOSE
        assert ws.close_code == PrivateWebCloseCode.HANDSHAKE_REQUIRED
        await ws.close()
        await session.close()

        session, ws, grant, welcome, active, confirmed = await connect_confirmed(server)
        assert welcome["limits"]["outbound_queue"] == 128
        assert active["event_sequence"] == 1
        assert confirmed["session_id"] == active["session_id"]
        await ws.close()
        await session.close()
    finally:
        await server.stop(0)


async def test_application_frame_during_handshake_is_rejected_with_4001() -> None:
    server, _launcher, _clock = build_server()
    assert await server.start() is True
    session = ClientSession()
    try:
        status, grant = await _bootstrap_with_session(session, server)
        assert status == 200
        ws = await session.ws_connect(
            f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
            headers={"Origin": ORIGIN},
        )
        await ws.send_json(
            {
                "type": "session.hello",
                "protocol": PROTOCOL_NAME,
                "handshake_id": grant["handshake_id"],
                "capability": grant["capability"],
                "handshake_nonce": "stage-nonce",
                "client_instance_id": CLIENT_ID,
            }
        )
        await receive_json(ws)
        await ws.send_json({"type": "chat.submit"})
        await ws.receive(timeout=2)
        assert ws.close_code == PrivateWebCloseCode.HANDSHAKE_REQUIRED
        await ws.close()
    finally:
        await session.close()
        await server.stop(0)


async def test_websocket_route_rejects_query_strings_before_upgrade() -> None:
    server, _launcher, _clock = build_server()
    assert await server.start() is True
    try:
        async with ClientSession() as session:
            try:
                await session.ws_connect(
                    f"http://127.0.0.1:{server.bound_port}/v1/web-chat?token=forbidden",
                    headers={"Origin": ORIGIN},
                )
            except WSServerHandshakeError as exc:
                assert exc.status == 403
            else:
                raise AssertionError("query-string websocket route must reject upgrade")
    finally:
        await server.stop(0)


async def test_malformed_and_oversized_frames_use_protocol_close_code() -> None:
    async def binary_hello() -> None:
        server, _launcher, _clock = build_server()
        assert await server.start() is True
        session = ClientSession()
        try:
            ws = await session.ws_connect(
                f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
                headers={"Origin": ORIGIN},
            )
            await ws.send_bytes(b"not-json")
            close = await ws.receive(timeout=2)
            assert close.type == WSMsgType.CLOSE
            assert close.data == PrivateWebCloseCode.PROTOCOL_ERROR
        finally:
            await session.close()
            await server.stop(0)

    async def malformed_ready() -> None:
        server, _launcher, _clock = build_server()
        assert await server.start() is True
        session = ClientSession()
        try:
            status, grant = await _bootstrap_with_session(session, server)
            assert status == 200
            ws = await session.ws_connect(
                f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
                headers={"Origin": ORIGIN},
            )
            await ws.send_json(
                {
                    "type": "session.hello",
                    "protocol": PROTOCOL_NAME,
                    "handshake_id": grant["handshake_id"],
                    "capability": grant["capability"],
                    "handshake_nonce": "malformed-nonce",
                    "client_instance_id": CLIENT_ID,
                }
            )
            await receive_json(ws)
            await ws.send_str("{not-json")
            close = await ws.receive(timeout=2)
            assert close.type == WSMsgType.CLOSE
            assert close.data == PrivateWebCloseCode.PROTOCOL_ERROR
        finally:
            await session.close()
            await server.stop(0)

    async def oversized_hello() -> None:
        server, _launcher, _clock = build_server()
        assert await server.start() is True
        session = ClientSession()
        try:
            ws = await session.ws_connect(
                f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
                headers={"Origin": ORIGIN},
            )
            # Substantially beyond the private 64 KiB cap but below the
            # bounded aiohttp parser ceiling: must remain a private 4004, not
            # aiohttp's native 1009.
            await ws.send_str("x" * (80 * 1024))
            close = await ws.receive(timeout=2)
            assert close.type == WSMsgType.CLOSE
            assert close.data == PrivateWebCloseCode.PROTOCOL_ERROR
        finally:
            await session.close()
            await server.stop(0)

    async def parser_ceiling_oversized_hello() -> None:
        server, _launcher, _clock = build_server()
        assert await server.start() is True
        session = ClientSession()
        try:
            ws = await session.ws_connect(
                f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
                headers={"Origin": ORIGIN},
            )
            # This crosses aiohttp's finite parser ceiling on the real wire.
            # The server contract still requires private 4004, never native
            # MESSAGE_TOO_BIG (1009).
            await ws.send_str("x" * (MAX_WS_PARSER_BYTES + 1))
            close = await ws.receive(timeout=2)
            assert close.type == WSMsgType.CLOSE
            assert close.data == PrivateWebCloseCode.PROTOCOL_ERROR, (
                close,
                ws.close_code,
            )
            assert close.extra == "protocol_error"
        finally:
            await session.close()
            await server.stop(0)

    async def oversized_confirmed() -> None:
        server, _launcher, _clock = build_server()
        assert await server.start() is True
        session = None
        try:
            session, ws, *_ = await connect_confirmed(server)
            # Multibyte payload: character count is below 64 KiB while UTF-8
            # wire size is 80 KiB, so byte-based validation is required.
            await ws.send_str("é" * (40 * 1024))
            close = await ws.receive(timeout=2)
            assert close.type == WSMsgType.CLOSE
            assert close.data == PrivateWebCloseCode.PROTOCOL_ERROR
        finally:
            if session is not None:
                await session.close()
            await server.stop(0)

    async def duplicate_pong_confirmed() -> None:
        clock = FakeClock(0)
        server, _launcher, _clock = build_server(clock=clock)
        assert await server.start() is True
        session = None
        try:
            session, ws, *_ = await connect_confirmed(server)
            record = next(iter(server._confirmed.values()))
            await server.heartbeat_tick(now=5.0)
            assert (await receive_json(ws))["type"] == "session.ping"
            assert record.pong_deadline == 15.0
            # The final duplicate key would be interpreted as session.pong by
            # a plain json.loads call.  Duplicate-key rejection must happen
            # before the pong deadline can be cleared.
            await ws.send_str(
                '{"type":"chat.submit","type":"session.pong",'
                f'"protocol":"{PROTOCOL_NAME}"}}'
            )
            close = await ws.receive(timeout=2)
            assert close.type == WSMsgType.CLOSE
            assert close.data == PrivateWebCloseCode.PROTOCOL_ERROR
            assert record.pong_deadline == 15.0
        finally:
            if session is not None:
                await session.close()
            await server.stop(0)

    async def nested_malformed_confirmed() -> None:
        server, _launcher, _clock = build_server()
        assert await server.start() is True
        session = None
        try:
            session, ws, *_ = await connect_confirmed(server)
            # Exceed the stdlib JSON decoder's recursion depth while staying
            # below the bounded websocket parser ceiling.
            nested = "[" * 2000 + "0" + "]" * 2000
            await ws.send_str(nested)
            close = await ws.receive(timeout=2)
            assert close.type == WSMsgType.CLOSE
            assert close.data == PrivateWebCloseCode.PROTOCOL_ERROR, (close, ws.close_code)
        finally:
            if session is not None:
                await session.close()
            await server.stop(0)

    async def binary_ready() -> None:
        server, _launcher, _clock = build_server()
        assert await server.start() is True
        session = ClientSession()
        try:
            status, grant = await _bootstrap_with_session(session, server)
            assert status == 200
            ws = await session.ws_connect(
                f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
                headers={"Origin": ORIGIN},
            )
            await ws.send_json(
                {
                    "type": "session.hello",
                    "protocol": PROTOCOL_NAME,
                    "handshake_id": grant["handshake_id"],
                    "capability": grant["capability"],
                    "handshake_nonce": "binary-ready-nonce",
                    "client_instance_id": CLIENT_ID,
                }
            )
            await receive_json(ws)
            await ws.send_bytes(b"not-ready")
            close = await ws.receive(timeout=2)
            assert close.type == WSMsgType.CLOSE
            assert close.data == PrivateWebCloseCode.PROTOCOL_ERROR
        finally:
            await session.close()
            await server.stop(0)

    async def binary_confirmed() -> None:
        server, _launcher, _clock = build_server()
        assert await server.start() is True
        session = None
        try:
            session, ws, *_ = await connect_confirmed(server)
            await ws.send_bytes(b"not-application-json")
            close = await ws.receive(timeout=2)
            assert close.type == WSMsgType.CLOSE
            assert close.data == PrivateWebCloseCode.PROTOCOL_ERROR
        finally:
            if session is not None:
                await session.close()
            await server.stop(0)

    await binary_hello()
    await malformed_ready()
    await oversized_hello()
    await parser_ceiling_oversized_hello()
    await oversized_confirmed()
    await nested_malformed_confirmed()
    await duplicate_pong_confirmed()
    await binary_ready()
    await binary_confirmed()


async def test_preconfirmation_disconnect_gets_one_same_session_retry() -> None:
    server, _launcher, _clock = build_server()
    assert await server.start() is True
    try:
        session = ClientSession()
        status, grant = await _bootstrap_with_session(session, server)
        assert status == 200
        ws = await session.ws_connect(
            f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
            headers={"Origin": ORIGIN},
        )
        nonce = "retry-nonce"
        await ws.send_json(
            {
                "type": "session.hello",
                "protocol": PROTOCOL_NAME,
                "handshake_id": grant["handshake_id"],
                "capability": grant["capability"],
                "handshake_nonce": nonce,
                "client_instance_id": CLIENT_ID,
            }
        )
        first_welcome = await receive_json(ws)
        first_id = first_welcome["provisional_session_id"]
        await ws.close()
        await session.close()
        await ws.receive(timeout=2)

        retry_session = ClientSession()
        retry_ws = await retry_session.ws_connect(
            f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
            headers={"Origin": ORIGIN},
        )
        await retry_ws.send_json(
            {
                "type": "session.hello",
                "protocol": PROTOCOL_NAME,
                "handshake_id": grant["handshake_id"],
                "capability": grant["capability"],
                "handshake_nonce": nonce,
                "client_instance_id": CLIENT_ID,
            }
        )
        retry_welcome = await receive_json(retry_ws)
        assert retry_welcome["provisional_session_id"] == first_id
        await retry_ws.send_json(
            {
                "type": "session.ready",
                "protocol": PROTOCOL_NAME,
                "server_epoch": EPOCH,
                "provisional_session_id": first_id,
                "handshake_nonce": nonce,
            }
        )
        active = await receive_json(retry_ws)
        await retry_ws.send_json(
            {
                "type": "session.active_ack",
                "protocol": PROTOCOL_NAME,
                "server_epoch": EPOCH,
                "session_id": active["session_id"],
                "handshake_nonce": nonce,
            }
        )
        assert (await receive_json(retry_ws))["type"] == "session.confirmed"
        await retry_ws.close()
        await retry_session.close()
    finally:
        await server.stop(0)


async def test_prehello_socket_is_closed_at_idle_deadline() -> None:
    clock = FakeClock(0)
    server, _launcher, _clock = build_server(clock=clock)
    assert await server.start() is True
    session = ClientSession()
    try:
        ws = await session.ws_connect(
            f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
            headers={"Origin": ORIGIN},
        )
        await server.heartbeat_tick(now=19.9)
        assert not ws.closed
        assert server.snapshot()["prehello_sessions"] == 1
        await server.heartbeat_tick(now=20.0)
        close = await ws.receive(timeout=2)
        assert close.type in {WSMsgType.CLOSE, WSMsgType.CLOSED}, close
        observed_code = close.data if close.type == WSMsgType.CLOSE else ws.close_code
        assert observed_code == PrivateWebCloseCode.HEARTBEAT_TIMEOUT
        assert server.snapshot()["prehello_sessions"] == 0
        await ws.close()
    finally:
        await session.close()
        await server.stop(0)


async def test_confirmed_session_detaches_from_prehello_registry() -> None:
    server, _launcher, _clock = build_server()
    assert await server.start() is True
    session = None
    ws = None
    try:
        session, ws, *_ = await connect_confirmed(server)
        assert not ws.closed
        assert server.snapshot()["confirmed_sessions"] == 1
        assert server.snapshot()["prehello_sessions"] == 0
    finally:
        if ws is not None:
            await ws.close()
        if session is not None:
            await session.close()
        await server.stop(0)


async def test_second_authenticated_session_uses_capacity_close_code() -> None:
    server, _launcher, _clock = build_server()
    assert await server.start() is True
    first_session = None
    second_session = None
    try:
        first_session, first_ws, *_ = await connect_confirmed(server)
        second_session = ClientSession()
        status, grant = await _bootstrap_with_session(second_session, server)
        assert status == 200
        second_ws = await second_session.ws_connect(
            f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
            headers={"Origin": ORIGIN},
        )
        await second_ws.send_json(
            {
                "type": "session.hello",
                "protocol": PROTOCOL_NAME,
                "handshake_id": grant["handshake_id"],
                "capability": grant["capability"],
                "handshake_nonce": "capacity-nonce",
                "client_instance_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
            }
        )
        await second_ws.receive(timeout=2)
        assert second_ws.close_code == PrivateWebCloseCode.SESSION_CAPACITY
        await second_session.close()
        await first_ws.close()
        await first_session.close()
    finally:
        if second_session is not None:
            await second_session.close()
        if first_session is not None:
            await first_session.close()
        await server.stop(0)


async def test_confirmed_commit_waits_for_successful_send() -> None:
    server, _launcher, _clock = build_server()
    assert await server.start() is True
    session = ClientSession()
    try:
        status, grant = await _bootstrap_with_session(session, server)
        assert status == 200
        original_send = server._send_and_wait

        async def fail_confirm(record, payload):
            if payload.get("type") == "session.confirmed":
                return False
            return await original_send(record, payload)

        server._send_and_wait = fail_confirm
        ws = await session.ws_connect(
            f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
            headers={"Origin": ORIGIN},
        )
        nonce = "send-order-nonce"
        await ws.send_json(
            {
                "type": "session.hello",
                "protocol": PROTOCOL_NAME,
                "handshake_id": grant["handshake_id"],
                "capability": grant["capability"],
                "handshake_nonce": nonce,
                "client_instance_id": CLIENT_ID,
            }
        )
        welcome = await receive_json(ws)
        await ws.send_json(
            {
                "type": "session.ready",
                "protocol": PROTOCOL_NAME,
                "server_epoch": EPOCH,
                "provisional_session_id": welcome["provisional_session_id"],
                "handshake_nonce": nonce,
            }
        )
        active = await receive_json(ws)
        await ws.send_json(
            {
                "type": "session.active_ack",
                "protocol": PROTOCOL_NAME,
                "server_epoch": EPOCH,
                "session_id": active["session_id"],
                "handshake_nonce": nonce,
            }
        )
        await ws.receive(timeout=2)
        assert ws.close_code in {
            PrivateWebCloseCode.INTERNAL_ERROR,
            PrivateWebCloseCode.CAPABILITY_REJECTED,
        }
        await ws.close()
        server._send_and_wait = original_send
        await asyncio.sleep(0)
        retry_session = ClientSession()
        retry_ws = await retry_session.ws_connect(
            f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
            headers={"Origin": ORIGIN},
        )
        await retry_ws.send_json(
            {
                "type": "session.hello",
                "protocol": PROTOCOL_NAME,
                "handshake_id": grant["handshake_id"],
                "capability": grant["capability"],
                "handshake_nonce": nonce,
                "client_instance_id": CLIENT_ID,
            }
        )
        retry_welcome = await receive_json(retry_ws)
        await retry_ws.send_json(
            {
                "type": "session.ready",
                "protocol": PROTOCOL_NAME,
                "server_epoch": EPOCH,
                "provisional_session_id": retry_welcome["provisional_session_id"],
                "handshake_nonce": nonce,
            }
        )
        retry_active = await receive_json(retry_ws)
        await retry_ws.send_json(
            {
                "type": "session.active_ack",
                "protocol": PROTOCOL_NAME,
                "server_epoch": EPOCH,
                "session_id": retry_active["session_id"],
                "handshake_nonce": nonce,
            }
        )
        assert (await receive_json(retry_ws))["type"] == "session.confirmed"
        await retry_ws.close()
        await retry_session.close()
    finally:
        await session.close()
        await server.stop(0)


async def test_runtime_state_provider_is_redacted_by_protocol_allowlist() -> None:
    server, _launcher, _clock = build_server(
        runtime_state_provider=lambda _session: {
            "protocol": PROTOCOL_NAME,
            "server_epoch": EPOCH,
            "session_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "revision": 1,
            "core_status": "ready",
            "chat_available": True,
            "reason_code": None,
            "active_turn_state": None,
            "voice_state": "idle",
            "queue_depth": 0,
            "capabilities": {
                "chat_submit": True,
                "reconcile": True,
                "runtime_state": True,
                "microphone": False,
                "settings": False,
                "model_select": False,
                "cancel": False,
            },
            "private_secret": "must-not-escape",
        }
    )
    assert await server.start() is True
    session = None
    ws = None
    try:
        session, ws, *_ = await connect_confirmed(server)
        await ws.send_json(
            {
                "protocol": PROTOCOL_NAME,
                "server_epoch": EPOCH,
                "session_id": next(iter(server._confirmed)),
                "turn_id": "11111111-1111-4111-8111-111111111111",
                "correlation_id": "22222222-2222-4222-8222-222222222222",
                "revision": 0,
                "type": "runtime.state.get",
                "payload": {},
            }
        )
        event = await receive_json(ws)
        assert event["type"] == "error"
        assert event["payload"]["code"] == "internal_error"
        assert "private_secret" not in json.dumps(event)
    finally:
        if ws is not None:
            await ws.close()
        if session is not None:
            await session.close()
        await server.stop(0)


async def test_reserved_settings_and_model_frames_are_unsupported_without_mutation() -> None:
    mutations = []

    async def unexpected_handler(*args, **kwargs):
        mutations.append((args, kwargs))

    server, _launcher, _clock = build_server()
    server._application_handler = unexpected_handler
    assert await server.start() is True
    session = None
    ws = None
    try:
        session, ws, *_ = await connect_confirmed(server)
        session_id = next(iter(server._confirmed))
        record = next(iter(server._confirmed.values()))
        for index, message_type in enumerate(
            ("settings.get", "settings.set", "model.list", "model.select"), 1
        ):
            turn_id = f"00000000-0000-4000-8000-{index:012d}"
            correlation_id = f"10000000-0000-4000-8000-{index:012d}"
            before_turns = frozenset(record.nonterminal_turns)
            await ws.send_json(
                {
                    "protocol": PROTOCOL_NAME,
                    "server_epoch": EPOCH,
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "correlation_id": correlation_id,
                    "revision": 0,
                    "type": message_type,
                    "payload": {},
                }
            )
            event = await receive_json(ws)
            assert event["type"] == "error"
            assert event["turn_id"] == turn_id
            assert event["correlation_id"] == correlation_id
            assert event["payload"] == {
                "code": "unsupported_in_v1",
                "retryable": False,
            }
            # Only the protocol event counters advance; none of the injected
            # settings/model mutation seams or turn state are touched.
            assert record.nonterminal_turns == set(before_turns)
        assert mutations == []
    finally:
        if ws is not None:
            await ws.close()
        if session is not None:
            await session.close()
        await server.stop(0)


async def test_stop_is_bounded_with_live_confirmed_client() -> None:
    server, _launcher, _clock = build_server()
    assert await server.start() is True
    session = None
    try:
        session, ws, *_ = await connect_confirmed(server, autoclose=False)
        await asyncio.wait_for(server.stop(0.05), timeout=0.5)
        close = await ws.receive(timeout=2)
        assert close.type in {WSMsgType.CLOSE, WSMsgType.CLOSED}, close
        observed_code = close.data if close.type == WSMsgType.CLOSE else ws.close_code
        assert observed_code == PrivateWebCloseCode.CORE_SHUTDOWN
        assert server.bound_port == 0
        assert server.snapshot()["confirmed_sessions"] == 0
        await ws.close()
    finally:
        if session is not None:
            await session.close()
        await server.stop(0)


async def test_stop_deadline_cancels_stalled_public_close() -> None:
    server, _launcher, _clock = build_server()
    assert await server.start() is True
    session = None
    try:
        session, ws, *_ = await connect_confirmed(server)
        record = next(iter(server._confirmed.values()))
        entered = asyncio.Event()

        async def never_closes(*, code=1000, message=b"", drain=True):
            del code, message, drain
            entered.set()
            await asyncio.Future()

        record.websocket.close = never_closes
        await asyncio.wait_for(server.stop(0.02), timeout=0.5)
        assert entered.is_set()
        assert server.bound_port == 0
        assert server.snapshot()["confirmed_sessions"] == 0
        assert record.closed is True
        assert record.writer_task is None or record.writer_task.cancelled() or record.writer_task.done()
        assert record.handler_task is None or record.handler_task.cancelled() or record.handler_task.done()
        await ws.close()
    finally:
        if session is not None:
            await session.close()
        await server.stop(0)


async def test_close_reason_matrix_is_ascii_and_bounded() -> None:
    from nana.runtime.private_web_chat_server import _close_reason

    for code in PrivateWebCloseCode:
        secret = "provider-secret-token\n" + "x" * 500
        reason = _close_reason(code, secret)
        assert reason.isascii()
        assert 0 < len(reason.encode("ascii")) <= 120
        assert "provider-secret-token" not in reason
        assert reason in {
            "normal",
            "core_shutdown",
            "internal_error",
            "handshake_required",
            "capability_rejected",
            "origin_rejected",
            "protocol_error",
            "session_capacity",
            "heartbeat_timeout",
            "slow_consumer",
            "bridge_disabled",
            "epoch_changed",
            "web_turn_stuck",
        }
    assert PrivateWebChatServer.close_code_for("origin_rejected") == PrivateWebCloseCode.ORIGIN_REJECTED
    assert PrivateWebChatServer.close_code_for("protocol_error") == PrivateWebCloseCode.PROTOCOL_ERROR
    assert PrivateWebChatServer.close_code_for("bridge_disabled") == PrivateWebCloseCode.BRIDGE_DISABLED
    assert PrivateWebChatServer.close_code_for("epoch_changed") == PrivateWebCloseCode.EPOCH_CHANGED
    assert PrivateWebChatServer.close_code_for("web_turn_stuck") == PrivateWebCloseCode.WEB_TURN_STUCK


async def test_full_close_matrix_is_observed_over_live_websocket() -> None:
    # Normal/core-shutdown lifecycle codes have dedicated tests above.  Here
    # exercise every error close code over a live socket without conflating a
    # server-owned shutdown with protocol failure calibration.
    for expected in PrivateWebCloseCode:
        if expected in {PrivateWebCloseCode.NORMAL, PrivateWebCloseCode.CORE_SHUTDOWN}:
            continue
        server, _launcher, _clock = build_server()
        assert await server.start() is True
        session = None
        try:
            session, ws, *_ = await connect_confirmed(server)
            record = next(iter(server._confirmed.values()))
            await server.close_session(record, expected)
            close = await ws.receive(timeout=2)
            assert close.type in {WSMsgType.CLOSE, WSMsgType.CLOSED}, (expected, close)
            observed_code = close.data if close.type == WSMsgType.CLOSE else ws.close_code
            assert observed_code == expected, (expected, close, ws.close_code)
            if close.type == WSMsgType.CLOSE:
                assert close.extra.isascii()
                assert len(close.extra.encode("ascii")) <= 120
            await ws.close()
        finally:
            if session is not None:
                await session.close()
            await server.stop(0)


async def test_close_completes_when_client_does_not_ack() -> None:
    server, _launcher, _clock = build_server()
    assert await server.start() is True
    session = ClientSession()
    try:
        status, grant = await _bootstrap_with_session(session, server)
        assert status == 200
        ws = await session.ws_connect(
            f"http://127.0.0.1:{server.bound_port}/v1/web-chat",
            headers={"Origin": ORIGIN},
            autoclose=False,
        )
        nonce = "non-acking-nonce"
        await ws.send_json(
            {
                "type": "session.hello",
                "protocol": PROTOCOL_NAME,
                "handshake_id": grant["handshake_id"],
                "capability": grant["capability"],
                "handshake_nonce": nonce,
                "client_instance_id": CLIENT_ID,
            }
        )
        welcome = await receive_json(ws)
        await ws.send_json(
            {
                "type": "session.ready",
                "protocol": PROTOCOL_NAME,
                "server_epoch": EPOCH,
                "provisional_session_id": welcome["provisional_session_id"],
                "handshake_nonce": nonce,
            }
        )
        active = await receive_json(ws)
        await ws.send_json(
            {
                "type": "session.active_ack",
                "protocol": PROTOCOL_NAME,
                "server_epoch": EPOCH,
                "session_id": active["session_id"],
                "handshake_nonce": nonce,
            }
        )
        await receive_json(ws)
        record = next(iter(server._confirmed.values()))
        await server.close_session(record, PrivateWebCloseCode.PROTOCOL_ERROR)
        close = await ws.receive(timeout=2)
        while close.type == WSMsgType.TEXT:
            close = await ws.receive(timeout=2)
        assert close.data == PrivateWebCloseCode.PROTOCOL_ERROR
        await asyncio.sleep(0.05)
        assert record.websocket.closed is True
        assert server.snapshot()["confirmed_sessions"] == 0
        await ws.close()
    finally:
        await session.close()
        await server.stop(0)


async def test_heartbeat_5_10_20_and_fixed_timeout_close() -> None:
    clock = FakeClock(0)
    server, _launcher, _clock = build_server(clock=clock)
    assert await server.start() is True
    try:
        session, ws, *_ = await connect_confirmed(server)
        await server.heartbeat_tick(now=4.9)
        try:
            await ws.receive(timeout=0.05)
        except asyncio.TimeoutError:
            pass
        else:
            raise AssertionError("heartbeat must not fire before five seconds")
        await server.heartbeat_tick(now=5.0)
        ping = await receive_json(ws)
        assert ping["type"] == "session.ping"
        await ws.send_json({"type": "session.pong", "protocol": PROTOCOL_NAME})
        await server.heartbeat_tick(now=10.0)
        assert not ws.closed
        await server.heartbeat_tick(now=20.0)
        assert not ws.closed
        await server.heartbeat_tick(now=21.0)
        await ws.receive(timeout=2)
        assert ws.close_code == PrivateWebCloseCode.HEARTBEAT_TIMEOUT
        await session.close()
    finally:
        await server.stop(0)


async def test_pong_deadline_does_not_slide_on_unrelated_activity() -> None:
    clock = FakeClock(0)
    server, _launcher, _clock = build_server(clock=clock)
    assert await server.start() is True
    session = None
    try:
        session, ws, *_ = await connect_confirmed(server)
        record = next(iter(server._confirmed.values()))
        await server.heartbeat_tick(now=5.0)
        assert (await receive_json(ws))["type"] == "session.ping"
        assert record.pong_deadline == 15.0
        for now in (9.0, 10.0, 14.9):
            record.last_activity = now
            await server.heartbeat_tick(now=now)
            assert record.pong_deadline == 15.0
            assert record.closed is False
        record.last_activity = 15.0
        await server.heartbeat_tick(now=15.0)
        close = await ws.receive(timeout=2)
        assert close.data == PrivateWebCloseCode.HEARTBEAT_TIMEOUT
    finally:
        if session is not None:
            await session.close()
        await server.stop(0)


async def test_outbound_queue_bound_and_close_code_matrix() -> None:
    unknown = []
    server, _launcher, _clock = build_server(mark_unknown=lambda *args: unknown.append(args))
    assert await server.start() is True
    session = None
    try:
        session, ws, *_ = await connect_confirmed(server)
        record = next(iter(server._confirmed.values()))
        record.writer_paused = True
        record.nonterminal_turns.add("11111111-1111-4111-8111-111111111111")
        for index in range(128):
            assert server.enqueue_event(record, {"type": "session.ping", "n": index})
        assert not server.enqueue_event(record, {"type": "session.ping", "n": 129})
        await asyncio.sleep(0.05)
        close = await ws.receive(timeout=2)
        while close.type == WSMsgType.TEXT:
            close = await ws.receive(timeout=2)
        assert close.type in {WSMsgType.CLOSE, WSMsgType.CLOSED}, close
        observed_code = close.data if close.type == WSMsgType.CLOSE else ws.close_code
        assert observed_code == PrivateWebCloseCode.SLOW_CONSUMER, (close, ws.close_code)
        assert unknown
    finally:
        if session is not None:
            await session.close()
        await server.stop(0)


async def main() -> None:
    tests = [
        test_start_binds_loopback_and_exact_routes,
        test_bootstrap_requires_exact_origin_host_path_and_post,
        test_untrusted_preview_returns_503_without_issuing_capability,
        test_bootstrap_exposes_only_exact_origin_to_browser,
        test_handshake_four_frames_and_application_gate,
        test_application_frame_during_handshake_is_rejected_with_4001,
        test_websocket_route_rejects_query_strings_before_upgrade,
        test_malformed_and_oversized_frames_use_protocol_close_code,
        test_preconfirmation_disconnect_gets_one_same_session_retry,
        test_prehello_socket_is_closed_at_idle_deadline,
        test_confirmed_session_detaches_from_prehello_registry,
        test_second_authenticated_session_uses_capacity_close_code,
        test_confirmed_commit_waits_for_successful_send,
        test_runtime_state_provider_is_redacted_by_protocol_allowlist,
        test_reserved_settings_and_model_frames_are_unsupported_without_mutation,
        test_stop_is_bounded_with_live_confirmed_client,
        test_stop_deadline_cancels_stalled_public_close,
        test_close_reason_matrix_is_ascii_and_bounded,
        test_full_close_matrix_is_observed_over_live_websocket,
        test_close_completes_when_client_does_not_ack,
        test_heartbeat_5_10_20_and_fixed_timeout_close,
        test_pong_deadline_does_not_slide_on_unrelated_activity,
        test_outbound_queue_bound_and_close_code_matrix,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        await test()
    print(f"smoke_private_web_chat_server: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    asyncio.run(main())
