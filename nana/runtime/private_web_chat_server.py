"""Loopback-only aiohttp shell for Nana Private Web Chat v1.

Task 4 owns transport admission and the authenticated session lifecycle. Turn
dispatch is intentionally an injected seam; the coordinator is added later.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
import inspect
import json
import secrets
import time
from typing import Any
from uuid import UUID, uuid4

from aiohttp import WSCloseCode, WSMsgType, web

from .nana_web_ownership import NanaWebOwnershipVerifier
from .private_web_chat_auth import BootstrapGrant, HandshakeLedger, HandshakeRejected
from .private_web_chat_protocol import (
    HEARTBEAT_INTERVAL_SECONDS,
    HEARTBEAT_PONG_TIMEOUT_SECONDS,
    MAX_USER_TEXT_CHARS,
    MAX_WS_TEXT_BYTES,
    PROTOCOL_NAME,
    SESSION_IDLE_TIMEOUT_SECONDS,
    PrivateWebCloseCode,
    PrivateWebProtocolError,
    SessionContext,
    build_runtime_state,
    build_server_event,
    parse_client_frame,
)


PRIVATE_WEB_HOST = "127.0.0.1"
PRIVATE_WEB_PORT = 8767
PRIVATE_WEB_ORIGIN = "http://127.0.0.1:5174"
OUTBOUND_QUEUE_LIMIT = 128
MAX_SAFE_REASON_BYTES = 120
# Bounded parser headroom lets the application translate substantially
# oversized client frames to the private 4004 code before aiohttp emits 1009.
# The authoritative accepted payload cap remains MAX_WS_TEXT_BYTES below.
MAX_WS_PARSER_BYTES = MAX_WS_TEXT_BYTES * 4

_SAFE_REASON_BY_CLOSE = {
    PrivateWebCloseCode.NORMAL: "normal",
    PrivateWebCloseCode.CORE_SHUTDOWN: "core_shutdown",
    PrivateWebCloseCode.INTERNAL_ERROR: "internal_error",
    PrivateWebCloseCode.HANDSHAKE_REQUIRED: "handshake_required",
    PrivateWebCloseCode.CAPABILITY_REJECTED: "capability_rejected",
    PrivateWebCloseCode.ORIGIN_REJECTED: "origin_rejected",
    PrivateWebCloseCode.PROTOCOL_ERROR: "protocol_error",
    PrivateWebCloseCode.SESSION_CAPACITY: "session_capacity",
    PrivateWebCloseCode.HEARTBEAT_TIMEOUT: "heartbeat_timeout",
    PrivateWebCloseCode.SLOW_CONSUMER: "slow_consumer",
    PrivateWebCloseCode.BRIDGE_DISABLED: "bridge_disabled",
    PrivateWebCloseCode.EPOCH_CHANGED: "epoch_changed",
    PrivateWebCloseCode.WEB_TURN_STUCK: "web_turn_stuck",
}


def _canonical_uuid(value: object, reason: str) -> str:
    if not isinstance(value, str):
        raise ValueError(reason)
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise ValueError(reason) from None
    if str(parsed) != value:
        raise ValueError(reason)
    return value


def _safe_reason(value: object, fallback: str) -> str:
    candidate = value if isinstance(value, str) else fallback
    candidate = candidate if candidate.isascii() else fallback
    candidate = candidate.replace("\r", " ").replace("\n", " ").strip()
    if not candidate:
        candidate = fallback
    return candidate.encode("ascii", errors="ignore")[:MAX_SAFE_REASON_BYTES].decode(
        "ascii", errors="ignore"
    ) or fallback


def _close_reason(code: PrivateWebCloseCode, reason: str | None = None) -> str:
    # Close reasons are a fixed wire allowlist.  Caller-provided exception,
    # provider, request or secret text must never reach the browser.
    del reason
    return _SAFE_REASON_BY_CLOSE.get(code, "internal_error")


def _is_awaitable(value: object) -> bool:
    return inspect.isawaitable(value)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    raise ValueError("invalid_json")


class _PrivateWebSocketResponse(web.WebSocketResponse):
    """Keep aiohttp's finite parser limit on the private close-code contract."""

    async def close(
        self,
        *,
        code: int = WSCloseCode.OK,
        message: bytes = b"",
        drain: bool = True,
    ) -> bool:
        if int(code) == int(WSCloseCode.MESSAGE_TOO_BIG):
            code = int(PrivateWebCloseCode.PROTOCOL_ERROR)
            message = _close_reason(PrivateWebCloseCode.PROTOCOL_ERROR).encode(
                "ascii"
            )
        return await super().close(code=code, message=message, drain=drain)


@dataclass
class _QueuedMessage:
    payload: Mapping[str, object]
    sent: asyncio.Future[None] | None = None
    visual: bool = False
    queued_at: float = 0.


@dataclass(eq=False)
class _Session:
    websocket: web.WebSocketResponse
    handshake_id: str | None
    provisional_session_id: str
    client_instance_id: str
    handshake_nonce: str
    state: str = "provisional"
    confirmed_session_id: str | None = None
    queue: asyncio.Queue[_QueuedMessage] = field(
        default_factory=lambda: asyncio.Queue(maxsize=OUTBOUND_QUEUE_LIMIT)
    )
    writer_task: asyncio.Task[None] | None = None
    close_task: asyncio.Task[None] | None = None
    handler_task: asyncio.Task[object] | None = None
    closed: bool = False
    close_code: int | None = None
    close_reason: str | None = None
    writer_paused: bool = False
    last_activity: float = 0.0
    last_ping: float | None = None
    pong_deadline: float | None = None
    event_sequence: int = 1
    revision: int = 0
    nonterminal_turns: set[str] = field(default_factory=set)
    visual_message: _QueuedMessage | None = None
    visual_wake_pending: bool = False


class PrivateWebChatServer:
    """Own the private loopback HTTP/WebSocket admission boundary."""

    def __init__(
        self,
        *,
        server_epoch: str | None = None,
        ledger: HandshakeLedger | None = None,
        auth: HandshakeLedger | None = None,
        launcher: object | None = None,
        ownership_verifier: NanaWebOwnershipVerifier | object | None = None,
        ownership: object | None = None,
        runtime_state_provider: Callable[..., Mapping[str, object] | Awaitable[Mapping[str, object]]] | None = None,
        application_handler: Callable[..., object] | None = None,
        on_frame: Callable[..., object] | None = None,
        mark_turn_unknown: Callable[..., object] | None = None,
        clock: Callable[[], float] | None = None,
        random_bytes: Callable[[int], bytes] | None = None,
        host: str = PRIVATE_WEB_HOST,
        port: int = PRIVATE_WEB_PORT,
        bridge_enabled: bool = True,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
        pong_timeout: float = HEARTBEAT_PONG_TIMEOUT_SECONDS,
        idle_timeout: float = SESSION_IDLE_TIMEOUT_SECONDS,
    ) -> None:
        self._server_epoch = _canonical_uuid(
            server_epoch or str(uuid4()), "invalid_server_epoch"
        )
        if host != PRIVATE_WEB_HOST:
            raise ValueError("private web chat must bind to 127.0.0.1")
        if type(port) is not int or port < 0 or port > 65535:
            raise ValueError("invalid private web chat port")
        if heartbeat_interval <= 0 or pong_timeout <= 0 or idle_timeout <= 0:
            raise ValueError("heartbeat intervals must be positive")
        if pong_timeout < heartbeat_interval:
            raise ValueError("pong timeout must not precede heartbeat interval")

        self._host = host
        self._requested_port = port
        self._bound_port = 0
        self._clock = clock or time.monotonic
        self._random_bytes = random_bytes or secrets.token_bytes
        self._heartbeat_interval = float(heartbeat_interval)
        self._pong_timeout = float(pong_timeout)
        self._idle_timeout = float(idle_timeout)
        self._bridge_enabled = bool(bridge_enabled)
        self._runtime_state_provider = runtime_state_provider
        self._application_handler = application_handler or on_frame
        self._mark_turn_unknown = mark_turn_unknown

        if ledger is not None and auth is not None and ledger is not auth:
            raise ValueError("ledger and auth aliases disagree")
        self._ledger = ledger or auth
        if self._ledger is None:
            self._ledger = HandshakeLedger(
                server_epoch=self._server_epoch,
                clock=self._clock,
                random_bytes=self._random_bytes,
                max_pending=2,
            )
        self._launcher = launcher
        self._ownership_verifier = ownership_verifier or ownership
        if self._ownership_verifier is None:
            raise ValueError("ownership_verifier is required")
        if launcher is None or not callable(getattr(launcher, "ownership_lease", None)):
            raise ValueError("launcher with ownership_lease() is required")

        self._app = web.Application()
        self._app.router.add_post("/v1/web-chat/bootstrap", self._bootstrap)
        self._app.router.add_get("/v1/web-chat", self._websocket, allow_head=False)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._started = False
        self._admission_open = False
        self._stopping = False
        self._auth_lock = asyncio.Lock()
        self._provisional: dict[str, _Session] = {}
        self._confirmed: dict[str, _Session] = {}
        self._by_handshake: dict[str, _Session] = {}
        self._all_sessions: set[_Session] = set()
        self._prehello: dict[web.WebSocketResponse, float] = {}
        self._close_counts: Counter[str] = Counter()
        self._grants_issued = 0
        self._bootstrap_rejected = 0
        self._bootstrap_requests = 0
        self._frames_received = 0
        self._queue_overflows = 0
        self._last_error: str | None = None
        self._shutdown_deadline: float | None = None

    @property
    def app(self) -> web.Application:
        return self._app

    @property
    def server_epoch(self) -> str:
        return self._server_epoch

    @property
    def bound_port(self) -> int:
        return self._bound_port

    @property
    def port(self) -> int:
        return self._bound_port

    @property
    def bound_host(self) -> str:
        return self._host

    async def start(self) -> bool:
        if self._started:
            return True
        if not self._bridge_enabled:
            self._last_error = "bridge_disabled"
            return False
        try:
            runner = web.AppRunner(self._app, access_log=None)
            await runner.setup()
            site = web.TCPSite(
                runner,
                self._host,
                self._requested_port,
                reuse_address=False,
                reuse_port=False,
            )
            await site.start()
        except (OSError, RuntimeError, ValueError):
            self._last_error = "port_conflict"
            try:
                await runner.cleanup()  # type: ignore[has-type]
            except Exception:
                pass
            return False
        self._runner = runner
        self._site = site
        sockets = getattr(getattr(site, "_server", None), "sockets", None) or ()
        if not sockets:
            await runner.cleanup()
            self._runner = None
            self._site = None
            self._last_error = "bind_failed"
            return False
        try:
            self._bound_port = int(sockets[0].getsockname()[1])
        except (IndexError, TypeError, ValueError, OSError):
            await runner.cleanup()
            self._runner = None
            self._site = None
            self._last_error = "bind_failed"
            return False
        self._started = True
        self._admission_open = True
        self._stopping = False
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="nana-private-web-heartbeat"
        )
        return True

    def begin_shutdown(self):
        self._admission_open = False

    async def stop(self, drain_seconds: float = 15.0) -> None:
        if not self._started and self._runner is None:
            self._admission_open = False
            try:
                self._ledger.clear()
            except Exception:
                pass
            return
        timeout = max(0.0, float(drain_seconds))
        self._shutdown_deadline = asyncio.get_running_loop().time() + timeout
        self._admission_open = False
        self._stopping = True
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        sessions = list(self._all_sessions)
        prehello = list(self._prehello)
        for ws in prehello:
            remaining = max(
                0.0,
                self._shutdown_deadline - asyncio.get_running_loop().time(),
            )
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(
                    self._close_ws(ws, PrivateWebCloseCode.CORE_SHUTDOWN),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                break
        if sessions:
            close_tasks = [
                asyncio.create_task(
                    self._close_session(item, PrivateWebCloseCode.CORE_SHUTDOWN)
                )
                for item in sessions
            ]
            remaining = max(
                0.0,
                self._shutdown_deadline - asyncio.get_running_loop().time(),
            )
            done, pending = await asyncio.wait(close_tasks, timeout=remaining)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if done:
                await asyncio.gather(*done, return_exceptions=True)
            writers = [item.writer_task for item in sessions if item.writer_task]
            remaining = max(
                0.0,
                (self._shutdown_deadline or asyncio.get_running_loop().time())
                - asyncio.get_running_loop().time(),
            )
            if writers and remaining > 0:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*writers, return_exceptions=True), timeout=remaining
                    )
                except asyncio.TimeoutError:
                    for task in writers:
                        if not task.done():
                            task.cancel()
            elif writers:
                for task in writers:
                    if not task.done():
                        task.cancel()
        if self._runner is not None:
            cleanup_task = asyncio.create_task(self._runner.cleanup())
            remaining = max(
                0.0,
                (self._shutdown_deadline or asyncio.get_running_loop().time())
                - asyncio.get_running_loop().time(),
            )
            cleanup_timeout = remaining if remaining > 0 else 0.05
            try:
                await asyncio.wait_for(cleanup_task, timeout=cleanup_timeout)
            except asyncio.TimeoutError:
                cleanup_task.cancel()
                await asyncio.gather(cleanup_task, return_exceptions=True)
        self._runner = None
        self._site = None
        self._started = False
        self._bound_port = 0
        for session in sessions:
            session.closed = True
            if session.close_code is None:
                session.close_code = int(PrivateWebCloseCode.CORE_SHUTDOWN)
                session.close_reason = _close_reason(
                    PrivateWebCloseCode.CORE_SHUTDOWN
                )
            if session.writer_task is not None and not session.writer_task.done():
                session.writer_task.cancel()
            handler = session.handler_task
            if handler is not None and handler is not asyncio.current_task() and not handler.done():
                handler.cancel()
        self._provisional.clear()
        self._confirmed.clear()
        self._by_handshake.clear()
        self._all_sessions.clear()
        self._prehello.clear()
        self._shutdown_deadline = None
        try:
            self._ledger.clear()
        except Exception:
            pass

    def snapshot(self) -> Mapping[str, object]:
        return {
            "server_epoch": self._server_epoch,
            "host": self._host,
            "bound_host": self._host,
            "bound_port": self._bound_port,
            "started": self._started,
            "admission_open": self._admission_open,
            "provisional": len(self._provisional),
            "confirmed": len(self._confirmed),
            "provisional_sessions": len(self._provisional),
            "confirmed_sessions": len(self._confirmed),
            "prehello_sessions": len(self._prehello),
            "active_sessions": len(self._confirmed),
            "pending_handshakes": len(self._by_handshake),
            "grants_issued": self._grants_issued,
            "bootstrap_requests": self._bootstrap_requests,
            "bootstrap_rejected": self._bootstrap_rejected,
            "frames_received": self._frames_received,
            "queue_overflows": self._queue_overflows,
            "close_counts": dict(self._close_counts),
            "last_error": self._last_error,
        }

    async def heartbeat_tick(self, now: float | None = None) -> None:
        if not self._started:
            return
        current = float(self._clock() if now is None else now)
        for session in list(self._all_sessions):
            if session.closed:
                continue
            if session.pong_deadline is not None and current >= session.pong_deadline:
                await self._close_session(session, PrivateWebCloseCode.HEARTBEAT_TIMEOUT)
                continue
            if current - session.last_activity >= self._idle_timeout:
                await self._close_session(session, PrivateWebCloseCode.HEARTBEAT_TIMEOUT)
                continue
            if (
                session.pong_deadline is None
                and (session.last_ping is None or current - session.last_ping >= self._heartbeat_interval)
            ):
                session.last_ping = current
                session.pong_deadline = current + self._pong_timeout
                self.enqueue_event(session, self._control_frame(session, "session.ping"))
        for ws, opened_at in list(self._prehello.items()):
            if ws not in self._prehello:
                continue
            if ws.closed:
                self._prehello.pop(ws, None)
                continue
            if current - opened_at >= self._idle_timeout:
                await self._close_ws(ws, PrivateWebCloseCode.HEARTBEAT_TIMEOUT)
                self._prehello.pop(ws, None)

    async def close_session(
        self,
        session: object,
        code: PrivateWebCloseCode,
        reason: str | None = None,
    ) -> None:
        """Close an injected session with the fixed private close mapping."""

        if not isinstance(session, _Session):
            raise TypeError("session must be a server session")
        if not isinstance(code, PrivateWebCloseCode):
            code = PrivateWebCloseCode(int(code))
        await self._close_session(session, code, reason)

    def enqueue_event(self, session: _Session, payload: Mapping[str, object]) -> bool:
        """Assign wire order at the one FIFO shared by turn and error events.

        Producers supply turn-local revisions and provisional sequence values.
        Only this transport owns session event_sequence; control frames and
        optional avatar signals retain their separate schemas/counters.
        """
        if session.closed or not isinstance(payload, Mapping):
            return False
        message = dict(payload)
        if "event_sequence" in message:
            message["event_sequence"] = session.event_sequence + 1
        try:
            if payload.get('type') == 'turn.state' and payload.get('payload', {}).get('state') in {'complete', 'failed', 'unknown'}:
                session.nonterminal_turns.discard(payload.get('turn_id'))
            session.queue.put_nowait(_QueuedMessage(message))
        except asyncio.QueueFull:
            self._queue_overflows += 1
            self._schedule_close(session, PrivateWebCloseCode.SLOW_CONSUMER)
            for turn_id in tuple(session.nonterminal_turns):
                self._mark_unknown(turn_id, "session_queue_full", session)
            return False
        if "event_sequence" in message:
            session.event_sequence = message["event_sequence"]
        return True

    def enqueue_visual_event(self, session: _Session, payload: Mapping[str, object]) -> bool:
        """Coalesce optional visual frames so slow rendering cannot queue PCM."""
        if (session.closed or session.state != 'confirmed'
                or payload.get('type') != 'avatar.signal'
                or payload.get('session_id') != session.provisional_session_id):
            return False
        if not session.visual_wake_pending:
            # Leave room for authoritative text/receipt traffic. Dropped visuals
            # expire in the consumer and never close or block the chat session.
            if session.queue.qsize() >= OUTBOUND_QUEUE_LIMIT // 2:
                return False
            session.queue.put_nowait(_QueuedMessage({}, visual=True))
            session.visual_wake_pending = True
        session.visual_message = _QueuedMessage(dict(payload), queued_at=self._now())
        return True

    async def _send_and_wait(self, session: _Session, payload: Mapping[str, object]) -> bool:
        if session.closed:
            return False
        loop = asyncio.get_running_loop()
        sent = loop.create_future()
        try:
            session.queue.put_nowait(_QueuedMessage(dict(payload), sent))
        except asyncio.QueueFull:
            self._queue_overflows += 1
            self._schedule_close(session, PrivateWebCloseCode.SLOW_CONSUMER)
            return False
        try:
            await sent
            return True
        except (asyncio.CancelledError, ConnectionError, RuntimeError):
            return False

    async def _heartbeat_loop(self) -> None:
        try:
            while self._started:
                await asyncio.sleep(min(self._heartbeat_interval, 1.0))
                await self.heartbeat_tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._last_error = "heartbeat_failed"

    async def _bootstrap(self, request: web.Request) -> web.StreamResponse:
        self._bootstrap_requests += 1
        if not self._admission_open:
            return self._json_error("shutting_down", status=503)
        if not self._request_is_exact(request, "/v1/web-chat/bootstrap"):
            self._bootstrap_rejected += 1
            return self._json_error("origin_rejected", status=403)
        try:
            candidate = self._launcher.ownership_lease()  # type: ignore[union-attr]
            verifier = self._ownership_verifier
            verify = getattr(verifier, "verify", None)
            result = (
                verify(candidate)
                if callable(verify)
                else verifier(candidate)
                if callable(verifier)
                else None
            )
        except Exception:
            result = None
        trusted = result is True or getattr(result, "trusted", False) is True
        if result is None or not trusted:
            self._bootstrap_rejected += 1
            return self._json_error("untrusted_preview_owner", status=503)
        try:
            grant: BootstrapGrant = self._ledger.issue(candidate)
        except HandshakeRejected as exc:
            self._bootstrap_rejected += 1
            if exc.reason_code == "handshake_capacity":
                return self._json_error("handshake_capacity", status=503)
            return self._json_error("untrusted_preview_owner", status=503)
        except Exception:
            self._bootstrap_rejected += 1
            return self._json_error("untrusted_preview_owner", status=503)
        self._grants_issued += 1
        return self._bootstrap_response(
            {
                "protocol": grant.protocol,
                "server_epoch": grant.server_epoch,
                "handshake_id": grant.handshake_id,
                "capability": grant.capability,
                "expires_in_ms": grant.expires_in_ms,
            }
        )

    async def _websocket(self, request: web.Request) -> web.StreamResponse:
        if not self._admission_open:
            return self._json_error("shutting_down", status=503)
        if not self._request_is_exact(request, "/v1/web-chat"):
            return self._json_error("origin_rejected", status=403)
        # Keep aiohttp's parser limit above the private protocol limit.  That
        # lets application code map oversized *text* frames to 4004 through
        # the supported public close API instead of aiohttp pre-closing them
        # with its parser-native 1009 before the protocol sees the frame.
        ws = _PrivateWebSocketResponse(
            # Receive bounded parser headroom beyond the protocol cap so the
            # application can emit its fixed 4004 mapping.
            max_msg_size=MAX_WS_PARSER_BYTES,
            autoping=True,
            heartbeat=None,
        )
        session_task = None
        try:
            await ws.prepare(request)
        except (ConnectionError, RuntimeError, web.HTTPException):
            return ws
        session: _Session | None = None
        self._prehello[ws] = self._now()
        try:
            current = asyncio.current_task()
            if current is not None:
                session_task = current
            session = await self._handshake(ws)
            if session is not None:
                session.handler_task = session_task
                await self._session_loop(session)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._last_error = "internal_error"
            if session is not None:
                await self._close_session(session, PrivateWebCloseCode.INTERNAL_ERROR)
            elif not ws.closed:
                await self._close_ws(ws, PrivateWebCloseCode.INTERNAL_ERROR)
        finally:
            self._prehello.pop(ws, None)
            if session is not None:
                await self._detach_session(session)
        return ws

    async def _handshake(self, ws: web.WebSocketResponse) -> _Session | None:
        first = await self._receive_json(ws)
        if first is None:
            return None
        if first.get("type") != "session.hello":
            await self._close_ws(ws, PrivateWebCloseCode.HANDSHAKE_REQUIRED)
            return None
        try:
            hello = self._parse_hello(first)
        except ValueError:
            await self._close_ws(ws, PrivateWebCloseCode.PROTOCOL_ERROR)
            return None
        try:
            async with self._auth_lock:
                for active in tuple(self._confirmed.values()):
                    if active.closed or active.websocket.closed:
                        self._discard_closed_session(active)
                if self._confirmed:
                    raise HandshakeRejected("session_capacity")
                existing = self._by_handshake.get(hello["handshake_id"])
                if existing is not None:
                    if existing.closed or existing.websocket.closed:
                        self._discard_closed_session(existing)
                    else:
                        raise HandshakeRejected("handshake_already_reserved")
                try:
                    reservation = self._ledger.reserve_hello(
                        hello["handshake_id"],
                        hello["capability"],
                        hello["handshake_nonce"],
                        hello["client_instance_id"],
                    )
                except HandshakeRejected as exc:
                    if exc.reason_code != "handshake_already_reserved":
                        raise
                    reservation = self._ledger.retry_reservation(
                        hello["handshake_id"],
                        hello["capability"],
                        hello["handshake_nonce"],
                        hello["client_instance_id"],
                    )
                session = _Session(
                    websocket=ws,
                    handshake_id=reservation.handshake_id,
                    provisional_session_id=reservation.provisional_session_id,
                    client_instance_id=reservation.client_instance_id,
                    handshake_nonce=reservation.handshake_nonce,
                    last_activity=self._now(),
                )
                session.last_ping = session.last_activity
                session.writer_task = asyncio.create_task(
                    self._writer(session), name="nana-private-web-writer"
                )
                self._provisional[session.provisional_session_id] = session
                self._by_handshake[reservation.handshake_id] = session
                self._all_sessions.add(session)
        except HandshakeRejected as exc:
            await self._close_ws(ws, self._close_for_handshake_reason(exc.reason_code))
            return None
        except Exception:
            await self._close_ws(ws, PrivateWebCloseCode.INTERNAL_ERROR)
            return None

        welcome = {
            "protocol": PROTOCOL_NAME,
            "type": "session.welcome",
            "server_epoch": self._server_epoch,
            "provisional_session_id": session.provisional_session_id,
            "handshake_nonce": session.handshake_nonce,
            "limits": {
                "heartbeat_ms": int(self._heartbeat_interval * 1000),
                "pong_timeout_ms": int(self._pong_timeout * 1000),
                "idle_timeout_ms": int(self._idle_timeout * 1000),
                "max_frame_bytes": MAX_WS_TEXT_BYTES,
                "max_user_text_chars": MAX_USER_TEXT_CHARS,
                "outbound_queue": OUTBOUND_QUEUE_LIMIT,
            },
            "capabilities": {
                "chat_submit": True,
                "reconcile": True,
                "runtime_state": True,
                "microphone": False,
                "settings": False,
                "model_select": False,
                "cancel": False,
            },
        }
        if not await self._send_and_wait(session, welcome):
            await self._close_session(session, PrivateWebCloseCode.INTERNAL_ERROR)
            return None
        ready = await self._receive_json(ws)
        if ready is None:
            return None
        if ready.get("type") != "session.ready":
            await self._close_session(session, PrivateWebCloseCode.HANDSHAKE_REQUIRED)
            return None
        try:
            self._validate_ready(ready, session, self._server_epoch)
        except ValueError as exc:
            code = (
                PrivateWebCloseCode.EPOCH_CHANGED
                if str(exc) == "epoch_mismatch"
                else PrivateWebCloseCode.PROTOCOL_ERROR
            )
            await self._close_session(session, code)
            return None
        active = {
            "protocol": PROTOCOL_NAME,
            "type": "session.active",
            "server_epoch": self._server_epoch,
            "session_id": session.provisional_session_id,
            "event_sequence": 1,
        }
        if not await self._send_and_wait(session, active):
            await self._close_session(session, PrivateWebCloseCode.INTERNAL_ERROR)
            return None
        ack = await self._receive_json(ws)
        if ack is None:
            return None
        if ack.get("type") != "session.active_ack":
            await self._close_session(session, PrivateWebCloseCode.HANDSHAKE_REQUIRED)
            return None
        try:
            self._validate_active_ack(ack, session, self._server_epoch)
        except ValueError as exc:
            code = (
                PrivateWebCloseCode.EPOCH_CHANGED
                if str(exc) == "epoch_mismatch"
                else PrivateWebCloseCode.CAPABILITY_REJECTED
            )
            await self._close_session(session, code)
            return None
        try:
            async with self._auth_lock:
                self._ledger.validate_active_ack(
                    session.handshake_id or "",
                    self._server_epoch,
                    session.provisional_session_id,
                    session.handshake_nonce,
                    session.client_instance_id,
                )
                if self._confirmed:
                    await self._close_session(session, PrivateWebCloseCode.SESSION_CAPACITY)
                    return None
                confirmed = {
                    "protocol": PROTOCOL_NAME,
                    "type": "session.confirmed",
                    "server_epoch": self._server_epoch,
                    "session_id": session.provisional_session_id,
                }
                if not await self._send_and_wait(session, confirmed):
                    await self._close_session(session, PrivateWebCloseCode.INTERNAL_ERROR)
                    return None
                self._ledger.commit_confirmed(
                    session.handshake_id or "",
                    self._server_epoch,
                    session.provisional_session_id,
                    session.handshake_nonce,
                    session.client_instance_id,
                )
                session.state = "confirmed"
                session.confirmed_session_id = session.provisional_session_id
                self._provisional.pop(session.provisional_session_id, None)
                self._confirmed[session.provisional_session_id] = session
                # A confirmed transport is no longer awaiting its first hello;
                # remove it before the confirmed session loop begins so the
                # pre-hello idle registry only tracks provisional sockets.
                self._prehello.pop(ws, None)
        except HandshakeRejected:
            await self._close_session(session, PrivateWebCloseCode.CAPABILITY_REJECTED)
            return None
        except Exception:
            await self._close_session(session, PrivateWebCloseCode.INTERNAL_ERROR)
            return None
        return session

    def _discard_closed_session(self, session: _Session) -> None:
        """Remove stale transport state without changing the retry ledger."""

        self._provisional.pop(session.provisional_session_id, None)
        self._confirmed.pop(session.provisional_session_id, None)
        if session.handshake_id is not None:
            self._by_handshake.pop(session.handshake_id, None)
        self._all_sessions.discard(session)
        session.closed = True
        if session.writer_task is not None and not session.writer_task.done():
            session.writer_task.cancel()

    async def _session_loop(self, session: _Session) -> None:
        ws = session.websocket
        async for message in ws:
            session.last_activity = self._now()
            self._frames_received += 1
            if message.type == WSMsgType.TEXT:
                try:
                    if len(message.data.encode("utf-8")) > MAX_WS_TEXT_BYTES:
                        raise ValueError
                    frame = json.loads(
                        message.data,
                        object_pairs_hook=_reject_duplicate_keys,
                        parse_constant=_reject_json_constant,
                    )
                except (
                    RecursionError,
                    UnicodeError,
                    TypeError,
                    ValueError,
                    json.JSONDecodeError,
                ):
                    await self._close_session(session, PrivateWebCloseCode.PROTOCOL_ERROR)
                    return
                if not isinstance(frame, Mapping):
                    await self._close_session(session, PrivateWebCloseCode.PROTOCOL_ERROR)
                    return
                if frame.get("type") == "session.pong":
                    if self._parse_pong(frame, session, self._server_epoch):
                        session.pong_deadline = None
                        continue
                    await self._close_session(session, PrivateWebCloseCode.PROTOCOL_ERROR)
                    return
                try:
                    parsed = parse_client_frame(
                        message.data,
                        SessionContext(
                            self._server_epoch,
                            session.provisional_session_id,
                            session.client_instance_id,
                        ),
                    )
                except PrivateWebProtocolError:
                    await self._close_session(session, PrivateWebCloseCode.PROTOCOL_ERROR)
                    return
                await self._handle_application_frame(session, parsed)
            elif message.type == WSMsgType.ERROR:
                await self._close_session(session, PrivateWebCloseCode.PROTOCOL_ERROR)
                return
            elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING}:
                return
            elif message.type == WSMsgType.BINARY:
                await self._close_session(session, PrivateWebCloseCode.PROTOCOL_ERROR)
                return

    async def _handle_application_frame(self, session: _Session, frame: Any) -> None:
        if frame.message_type == "session.pong":
            session.pong_deadline = None
            return
        if frame.message_type in {"settings.get", "settings.set", "model.list", "model.select"}:
            await self._send_error_event(
                session,
                frame.turn_id,
                frame.correlation_id,
                "unsupported_in_v1",
                retryable=False,
            )
            return
        if frame.message_type == "runtime.state.get":
            await self._send_runtime_state(session)
            return
        if frame.message_type in {"chat.submit", "chat.reconcile"}:
            if frame.message_type == 'chat.submit':
                session.nonterminal_turns.add(frame.turn_id)
            if self._application_handler is None:
                await self._send_error_event(
                    session,
                    frame.turn_id,
                    frame.correlation_id,
                    "unsupported_in_v1",
                    retryable=False,
                )
                session.nonterminal_turns.discard(frame.turn_id)
                return
            try:
                handler = self._application_handler
                try:
                    result = handler(session, frame)
                except TypeError:
                    result = handler(frame)
                if _is_awaitable(result):
                    result = await result
                if isinstance(result, Mapping):
                    self.enqueue_event(session, result)
            except Exception:
                await self._send_error_event(
                    session,
                    frame.turn_id,
                    frame.correlation_id,
                    "internal_error",
                    retryable=True,
                )
            return
        await self._close_session(session, PrivateWebCloseCode.PROTOCOL_ERROR)

    async def _send_runtime_state(self, session: _Session) -> None:
        context = SessionContext(
            self._server_epoch,
            session.provisional_session_id,
            session.client_instance_id,
        )
        try:
            if self._runtime_state_provider is None:
                state = build_runtime_state(
                    context=context,
                    revision=max(1, session.revision),
                    core_status="ready",
                    chat_available=self._application_handler is not None,
                    reason_code=None,
                    active_turn_state=None,
                    voice_state="idle",
                    queue_depth=0,
                )
            else:
                provider = self._runtime_state_provider
                try:
                    value = provider(session)
                except TypeError:
                    value = provider()
                if _is_awaitable(value):
                    value = await value
                if not isinstance(value, Mapping):
                    raise ValueError("invalid runtime state")
                state = self._sanitize_runtime_state(value, context)
        except Exception:
            await self._send_error_event(
                session, str(uuid4()), str(uuid4()), "internal_error", retryable=True
            )
            return
        self.enqueue_event(session, {**dict(state), "type": "runtime.state"})

    @staticmethod
    def _sanitize_runtime_state(
        value: Mapping[str, object], context: SessionContext
    ) -> Mapping[str, object]:
        """Rebuild provider output through the protocol's exact allowlist."""

        required = {
            "protocol",
            "server_epoch",
            "session_id",
            "revision",
            "core_status",
            "chat_available",
            "reason_code",
            "active_turn_state",
            "voice_state",
            "queue_depth",
            "capabilities",
        }
        if set(value) != required:
            raise ValueError("invalid runtime state")
        if value["protocol"] != PROTOCOL_NAME:
            raise ValueError("invalid runtime state")
        if value["server_epoch"] != context.server_epoch:
            raise ValueError("invalid runtime state")
        if value["session_id"] != context.session_id:
            raise ValueError("invalid runtime state")
        state = build_runtime_state(
            context=context,
            revision=value["revision"],  # type: ignore[arg-type]
            core_status=value["core_status"],  # type: ignore[arg-type]
            chat_available=value["chat_available"],  # type: ignore[arg-type]
            reason_code=value["reason_code"],  # type: ignore[arg-type]
            active_turn_state=value["active_turn_state"],  # type: ignore[arg-type]
            voice_state=value["voice_state"],  # type: ignore[arg-type]
            queue_depth=value["queue_depth"],  # type: ignore[arg-type]
        )
        if value["capabilities"] != state["capabilities"]:
            raise ValueError("invalid runtime state")
        return state

    async def _send_error_event(
        self,
        session: _Session,
        turn_id: str,
        correlation_id: str,
        code: str,
        *,
        retryable: bool,
    ) -> None:
        try:
            turn_id = _canonical_uuid(turn_id, "invalid_uuid")
            correlation_id = _canonical_uuid(correlation_id, "invalid_uuid")
        except ValueError:
            turn_id = str(uuid4())
            correlation_id = str(uuid4())
        session.revision += 1
        try:
            event = build_server_event(
                context=SessionContext(
                    self._server_epoch,
                    session.provisional_session_id,
                    session.client_instance_id,
                ),
                turn_id=turn_id,
                correlation_id=correlation_id,
                revision=session.revision,
                event_sequence=1,  # Stamped with session order by enqueue_event.
                message_type="error",
                payload={
                    "code": _safe_reason(code, "internal_error"),
                    "retryable": bool(retryable),
                },
            )
        except Exception:
            return
        self.enqueue_event(session, event)

    async def _writer(self, session: _Session) -> None:
        ws = session.websocket
        try:
            while not session.closed:
                while session.writer_paused and not session.closed:
                    await asyncio.sleep(0.001)
                if session.closed:
                    break
                item = await session.queue.get()
                if session.closed:
                    if item.sent is not None and not item.sent.done():
                        item.sent.set_exception(ConnectionError("closed"))
                    continue
                try:
                    payload = dict(item.payload)
                    if item.visual:
                        from .private_avatar_signals import age_visual_event
                        latest = session.visual_message
                        session.visual_message = None
                        session.visual_wake_pending = False
                        if latest is None:
                            continue
                        payload = age_visual_event(latest.payload, (self._now() - latest.queued_at) * 1000)
                    await ws.send_json(payload)
                except (ConnectionError, RuntimeError, asyncio.CancelledError) as exc:
                    if item.sent is not None and not item.sent.done():
                        item.sent.set_exception(exc)
                    if not session.closed and not isinstance(exc, asyncio.CancelledError):
                        session.closed = True
                    break
                else:
                    if item.sent is not None and not item.sent.done():
                        item.sent.set_result(None)
        except asyncio.CancelledError:
            raise
        finally:
            while not session.queue.empty():
                try:
                    item = session.queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item.sent is not None and not item.sent.done():
                    item.sent.set_exception(ConnectionError("writer_stopped"))

    async def _receive_json(self, ws: web.WebSocketResponse) -> Mapping[str, object] | None:
        try:
            message = await ws.receive()
        except (ConnectionError, RuntimeError, asyncio.CancelledError):
            return None
        if message.type != WSMsgType.TEXT:
            if message.type in {WSMsgType.BINARY, WSMsgType.ERROR}:
                await self._close_ws(ws, PrivateWebCloseCode.PROTOCOL_ERROR)
            return None
        try:
            if len(message.data.encode("utf-8")) > MAX_WS_TEXT_BYTES:
                raise ValueError("frame_too_large")
            value = json.loads(
                message.data,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_json_constant,
            )
        except (
            RecursionError,
            UnicodeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            await self._close_ws(ws, PrivateWebCloseCode.PROTOCOL_ERROR)
            return None
        if not isinstance(value, Mapping):
            await self._close_ws(ws, PrivateWebCloseCode.PROTOCOL_ERROR)
            return None
        return value

    @staticmethod
    def _parse_hello(frame: Mapping[str, object]) -> dict[str, str]:
        expected = {
            "type",
            "protocol",
            "handshake_id",
            "capability",
            "handshake_nonce",
            "client_instance_id",
        }
        if set(frame) != expected or frame.get("type") != "session.hello":
            raise ValueError("invalid_hello")
        if frame.get("protocol") != PROTOCOL_NAME:
            raise ValueError("protocol_mismatch")
        values: dict[str, str] = {}
        for key in ("handshake_id", "capability", "handshake_nonce", "client_instance_id"):
            value = frame.get(key)
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError("invalid_hello")
            values[key] = value
        _canonical_uuid(values["client_instance_id"], "invalid_client_instance")
        return values

    @staticmethod
    def _validate_ready(
        frame: Mapping[str, object], session: _Session, server_epoch: str
    ) -> None:
        expected = {
            "type",
            "protocol",
            "server_epoch",
            "provisional_session_id",
            "handshake_nonce",
        }
        if set(frame) != expected or frame.get("type") != "session.ready":
            raise ValueError("invalid_ready")
        if frame.get("protocol") != PROTOCOL_NAME:
            raise ValueError("protocol_mismatch")
        if frame.get("server_epoch") != server_epoch:
            raise ValueError("epoch_mismatch")
        if frame.get("provisional_session_id") != session.provisional_session_id:
            raise ValueError("session_mismatch")
        if frame.get("handshake_nonce") != session.handshake_nonce:
            raise ValueError("handshake_nonce_mismatch")

    @staticmethod
    def _validate_active_ack(
        frame: Mapping[str, object], session: _Session, server_epoch: str
    ) -> None:
        expected = {"type", "protocol", "server_epoch", "session_id", "handshake_nonce"}
        if set(frame) != expected or frame.get("type") != "session.active_ack":
            raise ValueError("invalid_active_ack")
        if frame.get("protocol") != PROTOCOL_NAME:
            raise ValueError("protocol_mismatch")
        if frame.get("server_epoch") != server_epoch:
            raise ValueError("epoch_mismatch")
        if frame.get("session_id") != session.provisional_session_id:
            raise ValueError("active_ack_mismatch")
        if frame.get("handshake_nonce") != session.handshake_nonce:
            raise ValueError("active_ack_mismatch")

    @staticmethod
    def _parse_pong(
        frame: Mapping[str, object], session: _Session, server_epoch: str
    ) -> bool:
        if frame.get("type") != "session.pong" or frame.get("protocol") != PROTOCOL_NAME:
            return False
        if set(frame) == {"type", "protocol"}:
            return True
        expected = {"type", "protocol", "server_epoch", "session_id"}
        if set(frame) == expected:
            return (
                frame.get("server_epoch") == server_epoch
                and frame.get("session_id") == session.provisional_session_id
            )
        # A full protocol envelope is also a valid session.pong.  Validate it
        # through the shared parser so no alternate identity rules emerge.
        if set(frame) == {
            "protocol",
            "server_epoch",
            "session_id",
            "turn_id",
            "correlation_id",
            "revision",
            "type",
            "payload",
        }:
            try:
                parsed = parse_client_frame(
                    json.dumps(frame),
                    SessionContext(
                        server_epoch,
                        session.provisional_session_id,
                        session.client_instance_id,
                    ),
                )
            except (PrivateWebProtocolError, TypeError, ValueError):
                return False
            return parsed.message_type == "session.pong"
        return False

    def _request_is_exact(self, request: web.Request, expected_path: str) -> bool:
        if request.path != expected_path:
            return False
        if request.rel_url.raw_query_string:
            return False
        if request.headers.get("Origin") != PRIVATE_WEB_ORIGIN:
            return False
        expected_host = f"{PRIVATE_WEB_HOST}:{self._bound_port or self._requested_port}"
        return request.headers.get("Host") == expected_host

    def _control_frame(self, session: _Session, message_type: str) -> dict[str, object]:
        return {
            "protocol": PROTOCOL_NAME,
            "type": message_type,
            "server_epoch": self._server_epoch,
            "session_id": session.provisional_session_id,
        }

    async def _detach_session(self, session: _Session) -> None:
        if session.state == "confirmed":
            self._confirmed.pop(session.provisional_session_id, None)
        else:
            self._provisional.pop(session.provisional_session_id, None)
        if session.handshake_id is not None:
            self._by_handshake.pop(session.handshake_id, None)
        self._all_sessions.discard(session)
        session.closed = True
        if session.writer_task is not None and not session.writer_task.done():
            session.writer_task.cancel()
            try:
                await session.writer_task
            except asyncio.CancelledError:
                pass

    async def _close_session(
        self,
        session: _Session,
        code: PrivateWebCloseCode,
        reason: str | None = None,
    ) -> None:
        if session.closed:
            return
        session.closed = True
        session.close_code = int(code)
        session.close_reason = _close_reason(code, reason)
        self._close_counts[str(int(code))] += 1
        await self._close_ws(session.websocket, code, session.close_reason)
        current = asyncio.current_task()
        handler = session.handler_task
        if handler is not None and handler is not current and not handler.done():
            handler.cancel()
            try:
                await handler
            except asyncio.CancelledError:
                pass
        if session.writer_task is not None and not session.writer_task.done():
            session.writer_task.cancel()

    async def _close_ws(
        self,
        ws: web.WebSocketResponse,
        code: PrivateWebCloseCode,
        reason: str | None = None,
    ) -> None:
        if ws.closed:
            return
        try:
            close_call = ws.close(
                code=int(code),
                message=_close_reason(code, reason).encode("ascii"),
                drain=False,
            )
            if self._stopping and self._shutdown_deadline is not None:
                remaining = max(
                    0.0,
                    self._shutdown_deadline - asyncio.get_running_loop().time(),
                )
                await asyncio.wait_for(close_call, timeout=remaining)
            else:
                await close_call
        except (ConnectionError, RuntimeError, asyncio.CancelledError, asyncio.TimeoutError):
            return

    def _schedule_close(self, session: _Session, code: PrivateWebCloseCode) -> None:
        if session.close_task is None or session.close_task.done():
            session.close_task = asyncio.create_task(self._close_session(session, code))

    @staticmethod
    def _close_for_handshake_reason(reason: str) -> PrivateWebCloseCode:
        if reason in {
            "handshake_capacity",
            "session_capacity",
            "handshake_already_reserved",
        }:
            return PrivateWebCloseCode.SESSION_CAPACITY
        if reason in {
            "capability_rejected",
            "capability_expired",
            "handshake_retry_exhausted",
            "handshake_not_found",
            "handshake_retry_mismatch",
            "handshake_retry_expired",
            "epoch_mismatch",
            "active_ack_mismatch",
            "confirmed_commit_mismatch",
            "active_ack_not_validated",
        }:
            return PrivateWebCloseCode.CAPABILITY_REJECTED
        return PrivateWebCloseCode.PROTOCOL_ERROR

    @staticmethod
    def close_code_for(reason_code: str) -> PrivateWebCloseCode:
        """Map a bounded protocol reason to the fixed WebSocket close code."""

        if reason_code in {"normal", "client_teardown"}:
            return PrivateWebCloseCode.NORMAL
        if reason_code in {"core_shutdown", "shutting_down"}:
            return PrivateWebCloseCode.CORE_SHUTDOWN
        if reason_code in {"internal_error", "server_failure"}:
            return PrivateWebCloseCode.INTERNAL_ERROR
        if reason_code in {"handshake_required"}:
            return PrivateWebCloseCode.HANDSHAKE_REQUIRED
        if reason_code in {
            "capability_rejected",
            "capability_expired",
            "handshake_retry_exhausted",
            "handshake_retry_mismatch",
        }:
            return PrivateWebCloseCode.CAPABILITY_REJECTED
        if reason_code in {"origin_rejected", "host_rejected", "path_rejected"}:
            return PrivateWebCloseCode.ORIGIN_REJECTED
        if reason_code in {"protocol_error", "invalid_frame", "schema_error"}:
            return PrivateWebCloseCode.PROTOCOL_ERROR
        if reason_code in {"session_capacity", "handshake_capacity"}:
            return PrivateWebCloseCode.SESSION_CAPACITY
        if reason_code in {"heartbeat_timeout", "idle_timeout"}:
            return PrivateWebCloseCode.HEARTBEAT_TIMEOUT
        if reason_code in {"slow_consumer", "session_queue_full"}:
            return PrivateWebCloseCode.SLOW_CONSUMER
        if reason_code in {"bridge_disabled"}:
            return PrivateWebCloseCode.BRIDGE_DISABLED
        if reason_code in {"epoch_changed", "epoch_mismatch"}:
            return PrivateWebCloseCode.EPOCH_CHANGED
        if reason_code in {"web_turn_stuck", "web_turn_timeout"}:
            return PrivateWebCloseCode.WEB_TURN_STUCK
        return PrivateWebCloseCode.INTERNAL_ERROR

    @staticmethod
    def _json_error(code: str, *, status: int) -> web.Response:
        return web.json_response({"error": _safe_reason(code, "internal_error")}, status=status)

    @staticmethod
    def _bootstrap_response(payload: Mapping[str, object]) -> web.Response:
        return web.json_response(
            dict(payload),
            headers={
                "Access-Control-Allow-Origin": PRIVATE_WEB_ORIGIN,
                "Vary": "Origin",
                "Cache-Control": "no-store",
            },
        )

    def _now(self) -> float:
        try:
            value = float(self._clock())
            if value != value or value in {float("inf"), float("-inf")}:
                raise ValueError
            return value
        except Exception:
            return time.monotonic()

    def _mark_unknown(self, turn_id: str, reason: str, session: _Session) -> None:
        callback = self._mark_turn_unknown
        if callback is None:
            return
        try:
            try:
                result = callback(session, turn_id, reason)
            except TypeError:
                try:
                    result = callback(turn_id, reason)
                except TypeError:
                    result = callback(turn_id=turn_id, reason_code=reason)
            if _is_awaitable(result):
                asyncio.create_task(result)
        except Exception:
            return


__all__ = [
    "OUTBOUND_QUEUE_LIMIT",
    "PRIVATE_WEB_HOST",
    "PRIVATE_WEB_ORIGIN",
    "PRIVATE_WEB_PORT",
    "PrivateWebChatServer",
]
