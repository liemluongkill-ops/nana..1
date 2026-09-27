"""Authenticated control and bounded audio session for Presence nodes."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import hmac
from http import HTTPStatus
import json
import threading
import time
from typing import Any, Awaitable, Callable, Iterable, Iterator
import uuid

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from nana.runtime.presence_media_protocol import (
    MEDIA_CHUNK_BYTES,
    MEDIA_KIND_JPEG_UPLINK,
    MEDIA_KIND_PCM_DOWNLINK,
    MEDIA_KIND_PCM_UPLINK,
    PresenceMediaFrame,
    PresenceMediaProtocolError,
    decode_media_frame,
    encode_media_frame,
)


PROTOCOL_NAME = "nana.presence.session.v1"
SESSION_PATH = "/presence/v1"
MAX_TEXT_FRAME_BYTES = 4096
HELLO_TIMEOUT_SECONDS = 5.0
PLAYBACK_READY_TIMEOUT_SECONDS = 5.0
PLAYBACK_CREDIT_TIMEOUT_SECONDS = 3.0
PLAYBACK_DRAIN_GRACE_SECONDS = 8.0
DISPLAY_ACK_TIMEOUT_SECONDS = 2.0
CAMERA_SNAPSHOT_TIMEOUT_SECONDS = 20.0
CAMERA_CANCEL_TIMEOUT_SECONDS = 2.0
MAX_PLAYBACK_BYTES = 4 * 1024 * 1024
MAX_NODE_AUDIO_CREDITS = 16
MAX_CAPTURE_SECONDS = 20
MAX_CAPTURE_BYTES = 16000 * 2 * MAX_CAPTURE_SECONDS
CAPTURE_INITIAL_CREDITS = 8
MAX_CAMERA_BYTES = 192 * 1024
CAMERA_INITIAL_CREDITS = 8
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480

_PCM_STREAM_END = object()


def _next_pcm_stream_item(iterator: Iterator[bytes]) -> object:
    try:
        return next(iterator)
    except StopIteration:
        return _PCM_STREAM_END


def _close_pcm_stream_iterator(iterator: Iterator[bytes]) -> None:
    close = getattr(iterator, "close", None)
    if callable(close):
        close()

DISPLAY_EXPRESSION_TAGS = frozenset(
    {
        "neutral",
        "listening",
        "pondering",
        "happy",
        "laughing",
        "glee",
        "sleepy",
        "surprised",
        "shocked",
        "scared",
        "awe",
        "skeptical",
        "squint",
        "mic_unclear",
        "core_loading",
        "curious",
        "playful",
        "shy",
        "relieved",
        "pleading",
        "love",
        "dizzy",
        "proud",
    }
)
DISPLAY_GESTURE_TAGS = frozenset(
    {
        "wink_left",
        "wink_right",
        "double_blink",
        "glance_left",
        "glance_right",
        "glance_up",
        "glance_down",
        "look_around",
        "scan",
        "peek_left",
        "peek_right",
        "startled_blink",
        "double_take",
        "celebrate",
        "panic",
    }
)
DISPLAY_EVENT_TAGS = (DISPLAY_EXPRESSION_TAGS | DISPLAY_GESTURE_TAGS) - {
    "neutral"
}


class PresenceSessionProtocolError(ValueError):
    """Raised when a node sends a frame outside the session contract."""


class PresencePlaybackError(RuntimeError):
    """Raised when a Presence node cannot complete bounded playback."""


class PresenceDisplayError(RuntimeError):
    """Raised when a Presence node rejects or cannot apply a display command."""


class PresenceCameraError(RuntimeError):
    """Raised when a bounded Presence camera snapshot cannot complete."""


@dataclass(frozen=True)
class PresenceAudioCapture:
    device_id: str
    session_id: str
    stream_id: int
    pcm: bytes
    sample_rate: int
    sample_width: int
    wire_bytes: int
    chunks: int
    trim_samples: int
    stop_reason: str
    noise_dbfs: float | None
    start_dbfs: float | None
    elapsed_ms: float


@dataclass(frozen=True)
class PresenceCaptureOutcome:
    status: str
    reason: str


PresenceAudioCaptureHandler = Callable[
    [PresenceAudioCapture],
    Awaitable[PresenceCaptureOutcome],
]


@dataclass(frozen=True)
class PresencePlaybackResult:
    stream_id: int
    status: str
    total_bytes: int
    bytes_received: int
    bytes_played: int
    queue_high_water: int
    underruns: int
    elapsed_ms: float
    first_audio_ms: float | None = None

    @property
    def completed(self) -> bool:
        return (
            self.status == "complete"
            and self.bytes_received == self.total_bytes
            and self.bytes_played == self.total_bytes
            and self.underruns == 0
        )


@dataclass(frozen=True)
class PresenceDisplayResult:
    request_id: int
    kind: str
    tag: str
    status: str
    reason: str

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


@dataclass(frozen=True)
class PresenceCameraFrame:
    device_id: str
    session_id: str
    stream_id: int
    jpeg: bytes
    width: int
    height: int
    chunks: int
    elapsed_ms: float


@dataclass
class _PlaybackState:
    stream_id: int
    total_bytes: int
    started_at: float
    ready: asyncio.Future
    drained: asyncio.Future
    credits: asyncio.Queue[int]
    streaming: bool = False
    first_audio_at: float | None = None


@dataclass
class _CaptureState:
    stream_id: int
    max_bytes: int
    started_at: float
    noise_dbfs: float | None
    start_dbfs: float | None
    expected_sequence: int = 0
    data: bytearray = field(default_factory=bytearray)


@dataclass
class _DisplayState:
    request_id: int
    kind: str
    tag: str
    acknowledged: asyncio.Future


@dataclass
class _CameraState:
    request_id: int
    max_bytes: int
    started_at: float
    completed: asyncio.Future
    width: int = 0
    height: int = 0
    total_bytes: int = 0
    expected_sequence: int = 0
    data: bytearray = field(default_factory=bytearray)


@dataclass
class _Session:
    device_id: str
    session_id: str
    connection: ServerConnection
    connected_at: float
    last_seen_at: float
    firmware: str
    profile: str
    capabilities: dict[str, bool]
    last_sequence: int | None = None
    heartbeats: int = 0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    playback_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    display_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    playback: _PlaybackState | None = None
    display: _DisplayState | None = None
    display_sync_task: asyncio.Task | None = None
    capture: _CaptureState | None = None
    camera: _CameraState | None = None
    turn_task: asyncio.Task | None = None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _decode_json_frame(message: str | bytes) -> dict[str, Any]:
    if isinstance(message, bytes):
        raise PresenceSessionProtocolError("binary frames are not enabled")
    if len(message.encode("utf-8")) > MAX_TEXT_FRAME_BYTES:
        raise PresenceSessionProtocolError("text frame exceeds size limit")
    try:
        payload = json.loads(message)
    except json.JSONDecodeError as exc:
        raise PresenceSessionProtocolError("frame is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise PresenceSessionProtocolError("frame must be a JSON object")
    return payload


def _required_text(payload: dict[str, Any], name: str, *, limit: int) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise PresenceSessionProtocolError(f"{name} must be non-empty text")
    value = value.strip()
    if len(value) > limit:
        raise PresenceSessionProtocolError(f"{name} exceeds length limit")
    return value


def _parse_hello(message: str | bytes) -> dict[str, Any]:
    payload = _decode_json_frame(message)
    if payload.get("type") != "hello":
        raise PresenceSessionProtocolError("first frame must be hello")
    if payload.get("protocol") != PROTOCOL_NAME:
        raise PresenceSessionProtocolError("protocol mismatch")

    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict):
        raise PresenceSessionProtocolError("capabilities must be an object")
    normalized_capabilities: dict[str, bool] = {}
    for name, enabled in capabilities.items():
        if not isinstance(name, str) or not name or len(name) > 64:
            raise PresenceSessionProtocolError("invalid capability name")
        if not isinstance(enabled, bool):
            raise PresenceSessionProtocolError(
                f"capability {name} must be boolean"
            )
        normalized_capabilities[name] = enabled

    return {
        "device_id": _required_text(payload, "device_id", limit=96),
        "firmware": _required_text(payload, "firmware", limit=96),
        "profile": _required_text(payload, "profile", limit=64),
        "capabilities": normalized_capabilities,
    }


def _parse_heartbeat(message: str | bytes) -> tuple[int, int]:
    payload = _decode_json_frame(message)
    if payload.get("type") != "heartbeat":
        raise PresenceSessionProtocolError("only heartbeat is enabled")
    if payload.get("protocol") != PROTOCOL_NAME:
        raise PresenceSessionProtocolError("protocol mismatch")
    sequence = payload.get("sequence")
    uptime_ms = payload.get("uptime_ms")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise PresenceSessionProtocolError("sequence must be a non-negative integer")
    if not isinstance(uptime_ms, int) or isinstance(uptime_ms, bool) or uptime_ms < 0:
        raise PresenceSessionProtocolError("uptime_ms must be a non-negative integer")
    return sequence, uptime_ms


def _required_uint32(payload: dict[str, Any], name: str) -> int:
    value = payload.get(name)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
        or value > 0xFFFFFFFF
    ):
        raise PresenceSessionProtocolError(f"{name} must be a uint32")
    return value


def _required_bounded_int(
    payload: dict[str, Any],
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = payload.get(name)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise PresenceSessionProtocolError(
            f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _decode_control_frame(message: str | bytes) -> dict[str, Any]:
    payload = _decode_json_frame(message)
    if payload.get("protocol") != PROTOCOL_NAME:
        raise PresenceSessionProtocolError("protocol mismatch")
    frame_type = payload.get("type")
    if not isinstance(frame_type, str) or not frame_type:
        raise PresenceSessionProtocolError("control frame type is missing")
    return payload


class PresenceSessionServer:
    """Own one authenticated Presence-node WebSocket listener."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        token: str,
        heartbeat_seconds: float = 5.0,
        timeout_seconds: float = 15.0,
        hello_timeout_seconds: float = HELLO_TIMEOUT_SECONDS,
        audio_uplink_handler: PresenceAudioCaptureHandler | None = None,
        initial_display_state: str | None = None,
    ) -> None:
        if not host.strip():
            raise ValueError("session host must not be empty")
        if not 0 <= int(port) <= 65535:
            raise ValueError("session port must be between 0 and 65535")
        if not token:
            raise ValueError("session token must not be empty")
        if heartbeat_seconds <= 0.0:
            raise ValueError("heartbeat interval must be positive")
        if timeout_seconds <= heartbeat_seconds:
            raise ValueError("session timeout must exceed heartbeat interval")
        if hello_timeout_seconds <= 0.0:
            raise ValueError("hello timeout must be positive")
        if (
            initial_display_state is not None
            and initial_display_state not in DISPLAY_EXPRESSION_TAGS
        ):
            raise ValueError("initial display state is not an approved expression")

        self._host = host.strip()
        self._port = int(port)
        self._token = token
        self._heartbeat_seconds = float(heartbeat_seconds)
        self._timeout_seconds = float(timeout_seconds)
        self._hello_timeout_seconds = float(hello_timeout_seconds)
        self._audio_uplink_handler = audio_uplink_handler
        self._initial_display_state = initial_display_state
        self._stop_event = asyncio.Event()
        self._started_event = asyncio.Event()
        self._sessions: dict[str, _Session] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stream_sequence = 0
        self._display_sequence = 0
        self._status_lock = threading.Lock()
        self._status: dict[str, Any] = {
            "state": "created",
            "bound_port": None,
            "connections_total": 0,
            "auth_rejected": 0,
            "protocol_rejected": 0,
            "replacements": 0,
            "disconnects": 0,
            "heartbeat_timeouts": 0,
            "active_sessions": 0,
            "errors": 0,
            "last_error": None,
            "last_device_id": None,
            "last_session_id": None,
            "last_firmware": None,
            "last_profile": None,
            "last_capabilities": {},
            "last_connected_at": None,
            "last_seen_at": None,
            "last_sequence": None,
            "heartbeats": 0,
            "playback_started": 0,
            "playback_completed": 0,
            "playback_failed": 0,
            "playback_bytes_sent": 0,
            "last_playback_stream_id": None,
            "last_playback_status": "none",
            "last_playback_phase": "none",
            "last_playback_total_bytes": 0,
            "last_playback_bytes_sent": 0,
            "last_playback_bytes_received": 0,
            "last_playback_bytes_played": 0,
            "last_playback_queue_high_water": 0,
            "last_playback_underruns": 0,
            "last_playback_elapsed_ms": None,
            "last_playback_streaming": False,
            "last_playback_first_audio_ms": None,
            "capture_started": 0,
            "capture_completed": 0,
            "capture_rejected": 0,
            "capture_failed": 0,
            "capture_bytes_received": 0,
            "last_capture_stream_id": None,
            "last_capture_status": "none",
            "last_capture_reason": "none",
            "last_capture_wire_bytes": 0,
            "last_capture_effective_bytes": 0,
            "last_capture_chunks": 0,
            "last_capture_trim_samples": 0,
            "last_capture_elapsed_ms": None,
            "display_sent": 0,
            "display_accepted": 0,
            "display_rejected": 0,
            "display_failed": 0,
            "last_display_request_id": None,
            "last_display_kind": "none",
            "last_display_tag": "none",
            "last_display_status": "none",
            "last_display_reason": "none",
            "camera_started": 0,
            "camera_completed": 0,
            "camera_rejected": 0,
            "camera_failed": 0,
            "camera_bytes_received": 0,
            "last_camera_stream_id": None,
            "last_camera_status": "none",
            "last_camera_reason": "none",
            "last_camera_width": 0,
            "last_camera_height": 0,
            "last_camera_bytes": 0,
            "last_camera_chunks": 0,
            "last_camera_elapsed_ms": None,
        }

    @property
    def bound_port(self) -> int | None:
        with self._status_lock:
            value = self._status.get("bound_port")
        return int(value) if value is not None else None

    def _update(self, **values: Any) -> None:
        with self._status_lock:
            self._status.update(values)

    def _increment(self, name: str) -> None:
        with self._status_lock:
            self._status[name] = int(self._status.get(name, 0)) + 1

    async def wait_started(self, timeout: float = 5.0) -> None:
        await asyncio.wait_for(self._started_event.wait(), timeout=timeout)

    def set_audio_uplink_handler(
        self,
        handler: PresenceAudioCaptureHandler | None,
    ) -> None:
        if handler is not None and not callable(handler):
            raise TypeError("audio uplink handler must be callable or None")
        self._audio_uplink_handler = handler

    @staticmethod
    async def _send_json(session: _Session, payload: dict[str, Any]) -> None:
        message = json.dumps(payload, separators=(",", ":"))
        async with session.send_lock:
            await session.connection.send(message)

    @staticmethod
    async def _send_binary(session: _Session, payload: bytes) -> None:
        async with session.send_lock:
            await session.connection.send(payload)

    def audio_uplink_available(self) -> bool:
        with self._status_lock:
            active = int(self._status.get("active_sessions", 0))
            capabilities = dict(self._status.get("last_capabilities") or {})
        return (
            active > 0
            and capabilities.get("audio_uplink") is True
            and callable(self._audio_uplink_handler)
        )

    async def _process_request(self, connection, request):
        if request.path != SESSION_PATH:
            self._increment("protocol_rejected")
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found\n")
        try:
            authorization = request.headers.get("Authorization", "")
        except Exception:
            authorization = ""
        expected = f"Bearer {self._token}"
        if not hmac.compare_digest(str(authorization), expected):
            self._increment("auth_rejected")
            return connection.respond(HTTPStatus.UNAUTHORIZED, "Unauthorized\n")
        return None

    async def _reject_protocol(
        self,
        connection: ServerConnection,
        exc: PresenceSessionProtocolError,
    ) -> None:
        self._increment("protocol_rejected")
        self._update(last_error=str(exc))
        code = 1003 if "binary" in str(exc) else 1008
        await connection.close(code=code, reason=str(exc)[:120])

    def audio_downlink_available(self) -> bool:
        with self._status_lock:
            active = int(self._status.get("active_sessions", 0))
            capabilities = dict(self._status.get("last_capabilities") or {})
        return active > 0 and capabilities.get("audio_downlink") is True

    def audio_downlink_stream_available(self) -> bool:
        with self._status_lock:
            active = int(self._status.get("active_sessions", 0))
            capabilities = dict(self._status.get("last_capabilities") or {})
        return (
            active > 0
            and capabilities.get("audio_downlink") is True
            and capabilities.get("audio_downlink_stream") is True
        )

    def display_available(self) -> bool:
        with self._status_lock:
            active = int(self._status.get("active_sessions", 0))
            capabilities = dict(self._status.get("last_capabilities") or {})
        return active > 0 and capabilities.get("display") is True

    def camera_available(self) -> bool:
        with self._status_lock:
            active = int(self._status.get("active_sessions", 0))
            capabilities = dict(self._status.get("last_capabilities") or {})
        return active > 0 and capabilities.get("camera") is True

    def _next_stream_id(self) -> int:
        self._stream_sequence = (self._stream_sequence + 1) & 0xFFFFFFFF
        if self._stream_sequence == 0:
            self._stream_sequence = 1
        return self._stream_sequence

    def _next_display_request_id(self) -> int:
        self._display_sequence = (self._display_sequence + 1) & 0xFFFFFFFF
        if self._display_sequence == 0:
            self._display_sequence = 1
        return self._display_sequence

    @staticmethod
    def _fail_pending_playback(session: _Session, exc: BaseException) -> None:
        playback = session.playback
        if playback is None:
            return
        for future in (playback.ready, playback.drained):
            if not future.done():
                future.set_exception(exc)

    @staticmethod
    def _fail_pending_display(session: _Session, exc: BaseException) -> None:
        display = session.display
        if display is not None and not display.acknowledged.done():
            display.acknowledged.set_exception(exc)

    @staticmethod
    def _fail_pending_camera(session: _Session, exc: BaseException) -> None:
        camera = session.camera
        if camera is not None and not camera.completed.done():
            camera.completed.set_exception(exc)

    @staticmethod
    async def _cancel_display_sync_task(session: _Session) -> None:
        task = session.display_sync_task
        session.display_sync_task = None
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    async def _cancel_turn_task(session: _Session) -> None:
        task = session.turn_task
        session.turn_task = None
        session.capture = None
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _run_capture_handler(
        self,
        session: _Session,
        capture: PresenceAudioCapture,
    ) -> None:
        outcome = PresenceCaptureOutcome("failed", "handler_unavailable")
        try:
            handler = self._audio_uplink_handler
            if handler is None:
                outcome = PresenceCaptureOutcome("skipped", "handler_unavailable")
            else:
                outcome = await handler(capture)
                if not isinstance(outcome, PresenceCaptureOutcome):
                    raise TypeError(
                        "audio uplink handler must return PresenceCaptureOutcome"
                    )
                if outcome.status not in {"complete", "skipped", "failed"}:
                    raise ValueError("audio uplink handler returned invalid status")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            outcome = PresenceCaptureOutcome(
                "failed",
                f"{type(exc).__name__}",
            )
            self._update(last_error=f"{type(exc).__name__}: {exc}")
        finally:
            if self._sessions.get(session.device_id) is session:
                try:
                    await self._send_json(
                        session,
                        {
                            "type": "turn_complete",
                            "protocol": PROTOCOL_NAME,
                            "stream_id": capture.stream_id,
                            "status": outcome.status,
                            "reason": str(outcome.reason or "none")[:96],
                        },
                    )
                except Exception as exc:
                    self._update(last_error=f"{type(exc).__name__}: {exc}")

            if outcome.status == "complete":
                self._increment("capture_completed")
            elif outcome.status == "skipped":
                self._increment("capture_rejected")
            else:
                self._increment("capture_failed")
            self._update(
                last_capture_status=outcome.status,
                last_capture_reason=str(outcome.reason or "none")[:96],
            )
            if session.turn_task is asyncio.current_task():
                session.turn_task = None

    async def _handle_binary_uplink(
        self,
        session: _Session,
        message: bytes,
    ) -> None:
        try:
            frame = decode_media_frame(message)
        except PresenceMediaProtocolError as exc:
            raise PresenceSessionProtocolError(str(exc)) from exc

        if frame.kind == MEDIA_KIND_JPEG_UPLINK:
            camera = session.camera
            if camera is None or camera.total_bytes <= 0:
                raise PresenceSessionProtocolError(
                    "JPEG uplink arrived without active camera snapshot"
                )
            if frame.stream_id != camera.request_id:
                raise PresenceSessionProtocolError("camera stream_id mismatch")
            if frame.sequence != camera.expected_sequence:
                raise PresenceSessionProtocolError("camera sequence mismatch")
            if len(camera.data) + len(frame.payload) > camera.total_bytes:
                raise PresenceSessionProtocolError(
                    "camera JPEG exceeds declared byte limit"
                )
            camera.data.extend(frame.payload)
            camera.expected_sequence += 1
            await self._send_json(
                session,
                {
                    "type": "camera_credit",
                    "protocol": PROTOCOL_NAME,
                    "stream_id": camera.request_id,
                    "credits": 1,
                    "bytes_received": len(camera.data),
                },
            )
            return

        if frame.kind != MEDIA_KIND_PCM_UPLINK:
            raise PresenceSessionProtocolError("binary media kind is not enabled")
        capture = session.capture
        if capture is None:
            raise PresenceSessionProtocolError(
                "PCM uplink arrived without active capture"
            )
        if frame.stream_id != capture.stream_id:
            raise PresenceSessionProtocolError("capture stream_id mismatch")
        if frame.sequence != capture.expected_sequence:
            raise PresenceSessionProtocolError("capture sequence mismatch")
        if len(frame.payload) % 2:
            raise PresenceSessionProtocolError("capture PCM is not sample-aligned")
        if len(capture.data) + len(frame.payload) > capture.max_bytes:
            raise PresenceSessionProtocolError("capture exceeds declared byte limit")

        capture.data.extend(frame.payload)
        capture.expected_sequence += 1
        await self._send_json(
            session,
            {
                "type": "capture_credit",
                "protocol": PROTOCOL_NAME,
                "stream_id": capture.stream_id,
                "credits": 1,
                "bytes_received": len(capture.data),
            },
        )

    async def _begin_capture(
        self,
        session: _Session,
        payload: dict[str, Any],
    ) -> None:
        if session.capabilities.get("audio_uplink") is not True:
            raise PresenceSessionProtocolError("audio uplink capability is disabled")
        if not callable(self._audio_uplink_handler):
            raise PresenceSessionProtocolError("audio uplink handler is unavailable")
        if session.capture is not None:
            raise PresenceSessionProtocolError("capture is already active")
        if session.turn_task is not None and not session.turn_task.done():
            raise PresenceSessionProtocolError("previous capture turn is still active")
        if session.playback is not None:
            raise PresenceSessionProtocolError("capture cannot start during playback")
        if session.camera is not None and session.camera.total_bytes > 0:
            raise PresenceSessionProtocolError(
                "capture cannot start during camera transfer"
            )
        if payload.get("codec") != "pcm_s16le":
            raise PresenceSessionProtocolError("capture codec must be pcm_s16le")
        if payload.get("integrity") != "crc32_per_chunk":
            raise PresenceSessionProtocolError("capture integrity must be crc32_per_chunk")
        if _required_bounded_int(
            payload, "sample_rate", minimum=16000, maximum=16000
        ) != 16000:
            raise PresenceSessionProtocolError("capture sample rate mismatch")
        _required_bounded_int(payload, "channels", minimum=1, maximum=1)
        _required_bounded_int(payload, "sample_width", minimum=2, maximum=2)
        _required_bounded_int(
            payload,
            "chunk_bytes",
            minimum=MEDIA_CHUNK_BYTES,
            maximum=MEDIA_CHUNK_BYTES,
        )
        max_bytes = _required_bounded_int(
            payload,
            "max_bytes",
            minimum=2,
            maximum=MAX_CAPTURE_BYTES,
        )
        if max_bytes % 2:
            raise PresenceSessionProtocolError("capture max_bytes is not sample-aligned")

        stream_id = _required_uint32(payload, "stream_id")
        noise_x10 = _required_bounded_int(
            payload, "noise_dbfs_x10", minimum=-1200, maximum=0
        )
        start_x10 = _required_bounded_int(
            payload, "start_dbfs_x10", minimum=-1200, maximum=0
        )
        session.capture = _CaptureState(
            stream_id=stream_id,
            max_bytes=max_bytes,
            started_at=time.monotonic(),
            noise_dbfs=noise_x10 / 10.0,
            start_dbfs=start_x10 / 10.0,
        )
        self._increment("capture_started")
        self._update(
            last_capture_stream_id=stream_id,
            last_capture_status="receiving",
            last_capture_reason="none",
            last_capture_wire_bytes=0,
            last_capture_effective_bytes=0,
            last_capture_chunks=0,
            last_capture_trim_samples=0,
            last_capture_elapsed_ms=None,
        )
        await self._send_json(
            session,
            {
                "type": "capture_ready",
                "protocol": PROTOCOL_NAME,
                "stream_id": stream_id,
                "credits": CAPTURE_INITIAL_CREDITS,
                "queue_capacity": CAPTURE_INITIAL_CREDITS,
                "max_bytes": MAX_CAPTURE_BYTES,
            },
        )

    async def _end_capture(
        self,
        session: _Session,
        payload: dict[str, Any],
    ) -> None:
        state = session.capture
        if state is None:
            raise PresenceSessionProtocolError("capture_end arrived without capture")
        stream_id = _required_uint32(payload, "stream_id")
        if stream_id != state.stream_id:
            raise PresenceSessionProtocolError("capture stream_id mismatch")
        total_bytes = _required_uint32(payload, "total_bytes")
        chunks = _required_uint32(payload, "chunks")
        trim_samples = _required_uint32(payload, "trim_samples")
        stop_reason = payload.get("reason")
        if stop_reason not in {"silence", "max_duration", "short"}:
            raise PresenceSessionProtocolError("capture stop reason is invalid")
        if total_bytes != len(state.data):
            raise PresenceSessionProtocolError("capture total_bytes mismatch")
        if chunks != state.expected_sequence:
            raise PresenceSessionProtocolError("capture chunk count mismatch")
        if trim_samples * 2 > total_bytes:
            raise PresenceSessionProtocolError("capture trim exceeds payload")
        effective_bytes = total_bytes - (trim_samples * 2)
        if effective_bytes <= 0:
            raise PresenceSessionProtocolError("capture has no effective PCM")

        elapsed_ms = (time.monotonic() - state.started_at) * 1000.0
        capture = PresenceAudioCapture(
            device_id=session.device_id,
            session_id=session.session_id,
            stream_id=stream_id,
            pcm=bytes(state.data[:effective_bytes]),
            sample_rate=16000,
            sample_width=2,
            wire_bytes=total_bytes,
            chunks=chunks,
            trim_samples=trim_samples,
            stop_reason=str(stop_reason),
            noise_dbfs=state.noise_dbfs,
            start_dbfs=state.start_dbfs,
            elapsed_ms=elapsed_ms,
        )
        session.capture = None
        with self._status_lock:
            self._status["capture_bytes_received"] = int(
                self._status.get("capture_bytes_received", 0)
            ) + total_bytes
        self._update(
            last_capture_status="received",
            last_capture_wire_bytes=total_bytes,
            last_capture_effective_bytes=effective_bytes,
            last_capture_chunks=chunks,
            last_capture_trim_samples=trim_samples,
            last_capture_elapsed_ms=round(elapsed_ms, 1),
        )
        await self._send_json(
            session,
            {
                "type": "capture_received",
                "protocol": PROTOCOL_NAME,
                "stream_id": stream_id,
                "wire_bytes": total_bytes,
                "effective_bytes": effective_bytes,
                "chunks": chunks,
            },
        )
        session.turn_task = asyncio.create_task(
            self._run_capture_handler(session, capture),
            name=f"presence-turn-{session.device_id}-{stream_id}",
        )

    async def _begin_camera(
        self,
        session: _Session,
        payload: dict[str, Any],
    ) -> None:
        camera = session.camera
        if camera is None:
            raise PresenceSessionProtocolError(
                "camera_begin arrived without a pending snapshot"
            )
        if camera.total_bytes != 0:
            raise PresenceSessionProtocolError("camera snapshot is already active")
        if session.capabilities.get("camera") is not True:
            raise PresenceSessionProtocolError("camera capability is disabled")
        if session.capture is not None or (
            session.turn_task is not None and not session.turn_task.done()
        ):
            raise PresenceSessionProtocolError(
                "camera cannot start during an audio turn"
            )
        if payload.get("codec") != "jpeg":
            raise PresenceSessionProtocolError("camera codec must be jpeg")
        if payload.get("integrity") != "crc32_per_chunk":
            raise PresenceSessionProtocolError(
                "camera integrity must be crc32_per_chunk"
            )
        _required_bounded_int(
            payload,
            "chunk_bytes",
            minimum=MEDIA_CHUNK_BYTES,
            maximum=MEDIA_CHUNK_BYTES,
        )
        stream_id = _required_uint32(payload, "stream_id")
        if stream_id != camera.request_id:
            raise PresenceSessionProtocolError("camera request_id mismatch")
        width = _required_bounded_int(
            payload, "width", minimum=CAMERA_WIDTH, maximum=CAMERA_WIDTH
        )
        height = _required_bounded_int(
            payload, "height", minimum=CAMERA_HEIGHT, maximum=CAMERA_HEIGHT
        )
        total_bytes = _required_bounded_int(
            payload,
            "total_bytes",
            minimum=4,
            maximum=camera.max_bytes,
        )
        camera.width = width
        camera.height = height
        camera.total_bytes = total_bytes
        self._update(
            last_camera_stream_id=stream_id,
            last_camera_status="receiving",
            last_camera_reason="none",
            last_camera_width=width,
            last_camera_height=height,
            last_camera_bytes=0,
            last_camera_chunks=0,
            last_camera_elapsed_ms=None,
        )
        await self._send_json(
            session,
            {
                "type": "camera_ready",
                "protocol": PROTOCOL_NAME,
                "stream_id": stream_id,
                "credits": CAMERA_INITIAL_CREDITS,
                "queue_capacity": CAMERA_INITIAL_CREDITS,
                "max_bytes": camera.max_bytes,
            },
        )

    async def _end_camera(
        self,
        session: _Session,
        payload: dict[str, Any],
    ) -> None:
        camera = session.camera
        if camera is None or camera.total_bytes <= 0:
            raise PresenceSessionProtocolError(
                "camera_end arrived without an active snapshot"
            )
        stream_id = _required_uint32(payload, "stream_id")
        total_bytes = _required_uint32(payload, "total_bytes")
        chunks = _required_uint32(payload, "chunks")
        if stream_id != camera.request_id:
            raise PresenceSessionProtocolError("camera stream_id mismatch")
        if total_bytes != camera.total_bytes or total_bytes != len(camera.data):
            raise PresenceSessionProtocolError("camera total_bytes mismatch")
        if chunks == 0 or chunks != camera.expected_sequence:
            raise PresenceSessionProtocolError("camera chunk count mismatch")
        jpeg = bytes(camera.data)
        if not (
            len(jpeg) >= 4
            and jpeg[:2] == b"\xff\xd8"
            and jpeg[-2:] == b"\xff\xd9"
        ):
            raise PresenceSessionProtocolError("camera payload is not a complete JPEG")

        elapsed_ms = (time.monotonic() - camera.started_at) * 1000.0
        frame = PresenceCameraFrame(
            device_id=session.device_id,
            session_id=session.session_id,
            stream_id=stream_id,
            jpeg=jpeg,
            width=camera.width,
            height=camera.height,
            chunks=chunks,
            elapsed_ms=elapsed_ms,
        )
        with self._status_lock:
            self._status["camera_bytes_received"] = int(
                self._status.get("camera_bytes_received", 0)
            ) + total_bytes
        self._increment("camera_completed")
        self._update(
            last_camera_status="complete",
            last_camera_reason="none",
            last_camera_bytes=total_bytes,
            last_camera_chunks=chunks,
            last_camera_elapsed_ms=round(elapsed_ms, 1),
        )
        await self._send_json(
            session,
            {
                "type": "camera_received",
                "protocol": PROTOCOL_NAME,
                "stream_id": stream_id,
                "total_bytes": total_bytes,
                "chunks": chunks,
            },
        )
        if not camera.completed.done():
            camera.completed.set_result(frame)

    async def _handle_control_message(
        self,
        session: _Session,
        message: str | bytes,
    ) -> None:
        if isinstance(message, bytes):
            await self._handle_binary_uplink(session, message)
            return
        payload = _decode_control_frame(message)
        frame_type = payload["type"]

        if frame_type == "heartbeat":
            sequence, _uptime_ms = _parse_heartbeat(message)
            session.last_sequence = sequence
            session.heartbeats += 1
            self._increment("heartbeats")
            self._update(last_sequence=sequence)
            await self._send_json(
                session,
                {
                    "type": "heartbeat_ack",
                    "protocol": PROTOCOL_NAME,
                    "sequence": sequence,
                    "server_time_ms": _now_ms(),
                },
            )
            return

        if frame_type == "display_ack":
            display = session.display
            if display is None:
                raise PresenceSessionProtocolError(
                    "display_ack arrived without a pending command"
                )
            request_id = _required_uint32(payload, "request_id")
            kind = _required_text(payload, "kind", limit=16)
            tag = _required_text(payload, "tag", limit=32)
            status = _required_text(payload, "status", limit=16)
            reason = _required_text(payload, "reason", limit=64)
            if request_id != display.request_id:
                raise PresenceSessionProtocolError(
                    "display request_id mismatch"
                )
            if kind != display.kind or tag != display.tag:
                raise PresenceSessionProtocolError(
                    "display acknowledgement mismatch"
                )
            if status not in {"accepted", "rejected"}:
                raise PresenceSessionProtocolError(
                    "invalid display acknowledgement status"
                )
            if display.acknowledged.done():
                raise PresenceSessionProtocolError(
                    "duplicate display acknowledgement"
                )
            display.acknowledged.set_result(
                PresenceDisplayResult(
                    request_id=request_id,
                    kind=kind,
                    tag=tag,
                    status=status,
                    reason=reason,
                )
            )
            return

        if frame_type == "capture_begin":
            await self._begin_capture(session, payload)
            return

        if frame_type == "capture_end":
            await self._end_capture(session, payload)
            return

        if frame_type == "capture_abort":
            capture = session.capture
            if capture is None:
                raise PresenceSessionProtocolError(
                    "capture_abort arrived without capture"
                )
            stream_id = _required_uint32(payload, "stream_id")
            if stream_id != capture.stream_id:
                raise PresenceSessionProtocolError("capture stream_id mismatch")
            reason = _required_text(payload, "reason", limit=96)
            session.capture = None
            self._increment("capture_rejected")
            self._update(
                last_capture_status="aborted",
                last_capture_reason=reason,
                last_capture_wire_bytes=len(capture.data),
                last_capture_chunks=capture.expected_sequence,
            )
            await self._send_json(
                session,
                {
                    "type": "capture_aborted",
                    "protocol": PROTOCOL_NAME,
                    "stream_id": stream_id,
                    "reason": reason,
                },
            )
            return

        if frame_type == "camera_begin":
            await self._begin_camera(session, payload)
            return

        if frame_type == "camera_end":
            await self._end_camera(session, payload)
            return

        if frame_type == "camera_abort":
            camera = session.camera
            if camera is None:
                raise PresenceSessionProtocolError(
                    "camera_abort arrived without a pending snapshot"
                )
            stream_id = _required_uint32(payload, "stream_id")
            if stream_id != camera.request_id:
                raise PresenceSessionProtocolError("camera stream_id mismatch")
            reason = _required_text(payload, "reason", limit=96)
            self._increment("camera_rejected")
            self._update(
                last_camera_stream_id=stream_id,
                last_camera_status="aborted",
                last_camera_reason=reason,
                last_camera_bytes=len(camera.data),
                last_camera_chunks=camera.expected_sequence,
            )
            if not camera.completed.done():
                camera.completed.set_exception(
                    PresenceCameraError(f"camera snapshot aborted: {reason}")
                )
            return

        if frame_type == "camera_cancelled":
            camera = session.camera
            if camera is None:
                # A cancellation acknowledgement may race with the Core-side
                # timeout cleanup; late ACKs are harmless and idempotent.
                return
            stream_id = _required_uint32(payload, "stream_id")
            if stream_id != camera.request_id:
                raise PresenceSessionProtocolError("camera stream_id mismatch")
            reason = _required_text(payload, "reason", limit=96)
            self._update(
                last_camera_stream_id=stream_id,
                last_camera_status="cancelled",
                last_camera_reason=reason,
                last_camera_bytes=len(camera.data),
                last_camera_chunks=camera.expected_sequence,
            )
            if not camera.completed.done():
                camera.completed.set_exception(
                    PresenceCameraError(f"camera snapshot cancelled: {reason}")
                )
            return

        playback = session.playback
        if playback is None:
            raise PresenceSessionProtocolError(
                f"{frame_type} arrived without active playback"
            )
        stream_id = _required_uint32(payload, "stream_id")
        if stream_id != playback.stream_id:
            raise PresenceSessionProtocolError("playback stream_id mismatch")

        if frame_type == "playback_ready":
            if playback.ready.done():
                raise PresenceSessionProtocolError("duplicate playback_ready")
            credits = _required_bounded_int(
                payload,
                "credits",
                minimum=1,
                maximum=MAX_NODE_AUDIO_CREDITS,
            )
            capacity = _required_bounded_int(
                payload,
                "queue_capacity",
                minimum=credits,
                maximum=MAX_NODE_AUDIO_CREDITS,
            )
            playback.ready.set_result((credits, capacity))
            return

        if frame_type == "playback_credit":
            credits = _required_bounded_int(
                payload,
                "credits",
                minimum=1,
                maximum=MAX_NODE_AUDIO_CREDITS,
            )
            _required_uint32(payload, "bytes_played")
            try:
                playback.credits.put_nowait(credits)
            except asyncio.QueueFull as exc:
                raise PresenceSessionProtocolError(
                    "playback credit queue overflow"
                ) from exc
            return

        if frame_type == "playback_started":
            if playback.first_audio_at is not None:
                raise PresenceSessionProtocolError("duplicate playback_started")
            _required_bounded_int(
                payload,
                "bytes_buffered",
                minimum=0,
                maximum=MAX_PLAYBACK_BYTES,
            )
            playback.first_audio_at = time.monotonic()
            self._update(
                last_playback_phase="playing",
                last_playback_first_audio_ms=round(
                    (playback.first_audio_at - playback.started_at) * 1000.0,
                    1,
                ),
            )
            return

        if frame_type == "playback_drained":
            if playback.drained.done():
                raise PresenceSessionProtocolError("duplicate playback_drained")
            status = payload.get("status")
            if status not in {"complete", "underrun", "aborted", "error"}:
                raise PresenceSessionProtocolError("invalid playback status")
            drained = {
                "status": status,
                "bytes_received": _required_uint32(payload, "bytes_received"),
                "bytes_played": _required_uint32(payload, "bytes_played"),
                "queue_high_water": _required_bounded_int(
                    payload,
                    "queue_high_water",
                    minimum=0,
                    maximum=MAX_NODE_AUDIO_CREDITS,
                ),
                "underruns": _required_uint32(payload, "underruns"),
            }
            playback.drained.set_result(drained)
            return

        raise PresenceSessionProtocolError(
            f"control frame type is not enabled: {frame_type}"
        )

    def _select_display_session(
        self,
        device_id: str | None,
    ) -> _Session | None:
        if device_id is not None:
            candidate = self._sessions.get(str(device_id))
            if candidate is not None and candidate.capabilities.get("display") is True:
                return candidate
            return None
        for candidate in self._sessions.values():
            if candidate.capabilities.get("display") is True:
                return candidate
        return None

    async def _send_display_command_to_session(
        self,
        session: _Session,
        *,
        kind: str,
        tag: str,
    ) -> PresenceDisplayResult:
        async with session.display_lock:
            if self._sessions.get(session.device_id) is not session:
                raise PresenceDisplayError(
                    "Presence node disconnected before display command"
                )
            if session.capabilities.get("display") is not True:
                raise PresenceDisplayError(
                    "Presence node did not advertise display capability"
                )

            loop = asyncio.get_running_loop()
            request_id = self._next_display_request_id()
            pending = _DisplayState(
                request_id=request_id,
                kind=kind,
                tag=tag,
                acknowledged=loop.create_future(),
            )
            session.display = pending
            self._increment("display_sent")
            self._update(
                last_display_request_id=request_id,
                last_display_kind=kind,
                last_display_tag=tag,
                last_display_status="waiting_ack",
                last_display_reason="none",
            )
            command_rejected = False
            try:
                await self._send_json(
                    session,
                    {
                        "type": f"display_{kind}",
                        "protocol": PROTOCOL_NAME,
                        "request_id": request_id,
                        "tag": tag,
                    },
                )
                try:
                    result = await asyncio.wait_for(
                        pending.acknowledged,
                        timeout=DISPLAY_ACK_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError as exc:
                    raise PresenceDisplayError(
                        "timed out waiting for display acknowledgement"
                    ) from exc

                self._update(
                    last_display_status=result.status,
                    last_display_reason=result.reason,
                )
                if result.accepted:
                    self._increment("display_accepted")
                    return result
                self._increment("display_rejected")
                command_rejected = True
                raise PresenceDisplayError(
                    f"display command was rejected: {result.reason}"
                )
            except PresenceDisplayError:
                if not command_rejected:
                    self._increment("display_failed")
                    self._update(
                        last_display_status="failed",
                        last_display_reason="ack_timeout_or_disconnect",
                    )
                raise
            except Exception as exc:
                self._increment("display_failed")
                self._update(
                    last_display_status="failed",
                    last_display_reason=f"{type(exc).__name__}",
                )
                raise PresenceDisplayError(str(exc)) from exc
            finally:
                if session.display is pending:
                    session.display = None

    async def set_display_state_async(
        self,
        tag: str,
        *,
        device_id: str | None = None,
    ) -> PresenceDisplayResult:
        normalized = str(tag).strip()
        if normalized not in DISPLAY_EXPRESSION_TAGS:
            raise ValueError("display state is not an approved expression tag")
        self._initial_display_state = normalized
        session = self._select_display_session(device_id)
        if session is None:
            raise PresenceDisplayError(
                "no display-capable Presence node is connected"
            )
        return await self._send_display_command_to_session(
            session,
            kind="state",
            tag=normalized,
        )

    async def trigger_display_event_async(
        self,
        tag: str,
        *,
        device_id: str | None = None,
    ) -> PresenceDisplayResult:
        normalized = str(tag).strip()
        if normalized not in DISPLAY_EVENT_TAGS:
            raise ValueError("display event is not an approved event tag")
        session = self._select_display_session(device_id)
        if session is None:
            raise PresenceDisplayError(
                "no display-capable Presence node is connected"
            )
        return await self._send_display_command_to_session(
            session,
            kind="event",
            tag=normalized,
        )

    async def _sync_initial_display(self, session: _Session) -> None:
        tag = self._initial_display_state
        if tag is None or session.capabilities.get("display") is not True:
            return
        try:
            await self._send_display_command_to_session(
                session,
                kind="state",
                tag=tag,
            )
        except asyncio.CancelledError:
            raise
        except PresenceDisplayError:
            return

    def _select_camera_session(
        self,
        device_id: str | None,
    ) -> _Session | None:
        if device_id is not None:
            candidate = self._sessions.get(str(device_id))
            if candidate is not None and candidate.capabilities.get("camera") is True:
                return candidate
            return None
        for candidate in self._sessions.values():
            if candidate.capabilities.get("camera") is True:
                return candidate
        return None

    async def capture_camera_jpeg_async(
        self,
        *,
        device_id: str | None = None,
        max_bytes: int = MAX_CAMERA_BYTES,
    ) -> PresenceCameraFrame:
        if not 4096 <= int(max_bytes) <= MAX_CAMERA_BYTES:
            raise ValueError(
                f"camera max_bytes must be between 4096 and {MAX_CAMERA_BYTES}"
            )
        session = self._select_camera_session(device_id)
        if session is None:
            raise PresenceCameraError(
                "no camera-capable Presence node is connected"
            )

        # Camera and speaker share this media lease. The node separately pauses
        # the microphone and confirms that I2S has actually been released.
        async with session.playback_lock:
            if self._sessions.get(session.device_id) is not session:
                raise PresenceCameraError(
                    "Presence node disconnected before camera snapshot"
                )
            if session.playback is not None:
                raise PresenceCameraError("camera cannot start during playback")
            if session.capture is not None or (
                session.turn_task is not None and not session.turn_task.done()
            ):
                raise PresenceCameraError("camera cannot start during an audio turn")
            if session.camera is not None:
                raise PresenceCameraError("camera snapshot is already active")

            loop = asyncio.get_running_loop()
            request_id = self._next_stream_id()
            pending = _CameraState(
                request_id=request_id,
                max_bytes=int(max_bytes),
                started_at=time.monotonic(),
                completed=loop.create_future(),
            )
            session.camera = pending
            self._increment("camera_started")
            self._update(
                last_camera_stream_id=request_id,
                last_camera_status="requesting",
                last_camera_reason="none",
                last_camera_width=0,
                last_camera_height=0,
                last_camera_bytes=0,
                last_camera_chunks=0,
                last_camera_elapsed_ms=None,
            )
            try:
                await self._send_json(
                    session,
                    {
                        "type": "camera_snapshot",
                        "protocol": PROTOCOL_NAME,
                        "request_id": request_id,
                        "codec": "jpeg",
                        "max_bytes": int(max_bytes),
                        "integrity": "crc32_per_chunk",
                    },
                )
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(pending.completed),
                        timeout=CAMERA_SNAPSHOT_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError as exc:
                    try:
                        await self._send_json(
                            session,
                            {
                                "type": "camera_cancel",
                                "protocol": PROTOCOL_NAME,
                                "stream_id": request_id,
                                "reason": "core_timeout",
                            },
                        )
                        try:
                            await asyncio.wait_for(
                                asyncio.shield(pending.completed),
                                timeout=CAMERA_CANCEL_TIMEOUT_SECONDS,
                            )
                        except (asyncio.TimeoutError, PresenceCameraError):
                            pass
                    except Exception:
                        pass
                    raise PresenceCameraError(
                        "timed out waiting for camera snapshot"
                    ) from exc
                if not isinstance(result, PresenceCameraFrame):
                    raise PresenceCameraError(
                        "camera snapshot returned an invalid result"
                    )
                self._update(last_error=None)
                return result
            except PresenceCameraError:
                with self._status_lock:
                    camera_status = self._status.get("last_camera_status")
                if camera_status != "aborted":
                    self._increment("camera_failed")
                    self._update(
                        last_camera_status="failed",
                        last_camera_reason="snapshot_failed",
                    )
                raise
            except Exception as exc:
                self._increment("camera_failed")
                self._update(
                    last_camera_status="failed",
                    last_camera_reason=f"{type(exc).__name__}",
                    last_error=f"{type(exc).__name__}: {exc}",
                )
                raise PresenceCameraError(str(exc)) from exc
            finally:
                if session.camera is pending:
                    session.camera = None

    def capture_camera_jpeg(
        self,
        *,
        device_id: str | None = None,
        max_bytes: int = MAX_CAMERA_BYTES,
        timeout: float = CAMERA_SNAPSHOT_TIMEOUT_SECONDS + 2.0,
    ) -> PresenceCameraFrame:
        loop = self._loop
        if loop is None or not loop.is_running():
            raise PresenceCameraError("Presence session loop is not running")
        future = asyncio.run_coroutine_threadsafe(
            self.capture_camera_jpeg_async(
                device_id=device_id,
                max_bytes=max_bytes,
            ),
            loop,
        )
        return future.result(timeout=timeout)

    async def play_pcm16_async(
        self,
        pcm: bytes | bytearray | memoryview,
        *,
        sample_rate: int = 16000,
        device_id: str | None = None,
    ) -> PresencePlaybackResult:
        payload = bytes(pcm)
        if sample_rate != 16000:
            raise ValueError("Presence playback currently requires 16000 Hz PCM")
        if not payload or len(payload) % 2:
            raise ValueError("PCM payload must contain aligned signed 16-bit samples")
        if len(payload) > MAX_PLAYBACK_BYTES:
            raise ValueError("PCM payload exceeds the bounded playback limit")

        session = None
        if device_id is not None:
            session = self._sessions.get(str(device_id))
        else:
            for candidate in self._sessions.values():
                if candidate.capabilities.get("audio_downlink") is True:
                    session = candidate
                    break
        if session is None or session.capabilities.get("audio_downlink") is not True:
            raise PresencePlaybackError("no audio-capable Presence node is connected")

        async with session.playback_lock:
            if self._sessions.get(session.device_id) is not session:
                raise PresencePlaybackError("Presence node disconnected before playback")
            loop = asyncio.get_running_loop()
            stream_id = self._next_stream_id()
            playback = _PlaybackState(
                stream_id=stream_id,
                total_bytes=len(payload),
                started_at=time.monotonic(),
                ready=loop.create_future(),
                drained=loop.create_future(),
                credits=asyncio.Queue(maxsize=MAX_NODE_AUDIO_CREDITS * 2),
                streaming=False,
            )
            session.playback = playback
            self._increment("playback_started")
            self._update(
                last_playback_stream_id=stream_id,
                last_playback_status="starting",
                last_playback_phase="starting",
                last_playback_total_bytes=len(payload),
                last_playback_bytes_sent=0,
                last_playback_bytes_received=0,
                last_playback_bytes_played=0,
                last_playback_queue_high_water=0,
                last_playback_underruns=0,
                last_playback_elapsed_ms=None,
                last_playback_streaming=False,
                last_playback_first_audio_ms=None,
            )

            completed = False

            def drained_error(stage: str, drained: dict[str, Any]) -> PresencePlaybackError:
                return PresencePlaybackError(
                    f"node ended playback during {stage}: "
                    f"status={drained['status']} "
                    f"received={drained['bytes_received']}/{len(payload)} "
                    f"played={drained['bytes_played']}/{len(payload)} "
                    f"underruns={drained['underruns']}"
                )

            async def next_credit_or_drain(offset: int) -> int:
                credit_task = asyncio.create_task(playback.credits.get())
                try:
                    done, _ = await asyncio.wait(
                        {credit_task, playback.drained},
                        timeout=PLAYBACK_CREDIT_TIMEOUT_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if playback.drained in done:
                        raise drained_error("streaming", playback.drained.result())
                    if credit_task not in done:
                        raise PresencePlaybackError(
                            "timed out waiting for playback_credit "
                            f"after {offset}/{len(payload)} bytes"
                        )
                    return int(credit_task.result())
                finally:
                    if not credit_task.done():
                        credit_task.cancel()
                        try:
                            await credit_task
                        except asyncio.CancelledError:
                            pass

            try:
                self._update(last_playback_phase="waiting_ready")
                await self._send_json(
                    session,
                    {
                        "type": "playback_begin",
                        "protocol": PROTOCOL_NAME,
                        "stream_id": stream_id,
                        "codec": "pcm_s16le",
                        "sample_rate": sample_rate,
                        "channels": 1,
                        "sample_width": 2,
                        "total_bytes": len(payload),
                        "total_samples": len(payload) // 2,
                        "chunk_bytes": MEDIA_CHUNK_BYTES,
                        "integrity": "crc32_per_chunk",
                    },
                )
                ready_done, _ = await asyncio.wait(
                    {playback.ready, playback.drained},
                    timeout=PLAYBACK_READY_TIMEOUT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if playback.drained in ready_done:
                    raise drained_error("waiting_ready", playback.drained.result())
                if playback.ready not in ready_done:
                    raise PresencePlaybackError(
                        "timed out waiting for playback_ready"
                    )
                credits, capacity = playback.ready.result()
                self._update(
                    last_playback_status="streaming",
                    last_playback_phase="streaming",
                )

                offset = 0
                sequence = 0
                available_credits = int(credits)
                while offset < len(payload):
                    while available_credits <= 0:
                        available_credits += await next_credit_or_drain(offset)
                        if available_credits > capacity:
                            raise PresencePlaybackError(
                                "node returned more credits than queue capacity"
                            )
                    chunk = payload[offset : offset + MEDIA_CHUNK_BYTES]
                    await self._send_binary(
                        session,
                        encode_media_frame(
                            PresenceMediaFrame(
                                kind=MEDIA_KIND_PCM_DOWNLINK,
                                stream_id=stream_id,
                                sequence=sequence,
                                payload=chunk,
                            )
                        )
                    )
                    offset += len(chunk)
                    sequence += 1
                    available_credits -= 1
                    self._update(last_playback_bytes_sent=offset)

                self._update(last_playback_phase="waiting_drain")
                await self._send_json(
                    session,
                    {
                        "type": "playback_end",
                        "protocol": PROTOCOL_NAME,
                        "stream_id": stream_id,
                        "total_bytes": len(payload),
                        "chunks": sequence,
                    },
                )
                duration_seconds = len(payload) / (sample_rate * 2.0)
                try:
                    drained = await asyncio.wait_for(
                        asyncio.shield(playback.drained),
                        timeout=duration_seconds + PLAYBACK_DRAIN_GRACE_SECONDS,
                    )
                except asyncio.TimeoutError as exc:
                    raise PresencePlaybackError(
                        "timed out waiting for playback_drained "
                        f"after sending {offset}/{len(payload)} bytes"
                    ) from exc
                elapsed_ms = (time.monotonic() - playback.started_at) * 1000.0
                result = PresencePlaybackResult(
                    stream_id=stream_id,
                    status=str(drained["status"]),
                    total_bytes=len(payload),
                    bytes_received=int(drained["bytes_received"]),
                    bytes_played=int(drained["bytes_played"]),
                    queue_high_water=int(drained["queue_high_water"]),
                    underruns=int(drained["underruns"]),
                    elapsed_ms=elapsed_ms,
                    first_audio_ms=(
                        None
                        if playback.first_audio_at is None
                        else (playback.first_audio_at - playback.started_at) * 1000.0
                    ),
                )
                self._update(
                    last_playback_status=result.status,
                    last_playback_phase="complete",
                    last_playback_bytes_received=result.bytes_received,
                    last_playback_bytes_played=result.bytes_played,
                    last_playback_queue_high_water=result.queue_high_water,
                    last_playback_underruns=result.underruns,
                    last_playback_elapsed_ms=round(result.elapsed_ms, 1),
                    last_playback_first_audio_ms=(
                        None
                        if result.first_audio_ms is None
                        else round(result.first_audio_ms, 1)
                    ),
                )
                if not result.completed:
                    raise PresencePlaybackError(
                        "node playback did not drain cleanly: "
                        f"status={result.status} received={result.bytes_received} "
                        f"played={result.bytes_played} underruns={result.underruns}"
                    )
                completed = True
                self._increment("playback_completed")
                with self._status_lock:
                    self._status["playback_bytes_sent"] = int(
                        self._status.get("playback_bytes_sent", 0)
                    ) + len(payload)
                return result
            except Exception as exc:
                self._increment("playback_failed")
                self._update(
                    last_playback_status="failed",
                    last_playback_phase="failed",
                    last_error=f"{type(exc).__name__}: {exc}",
                )
                try:
                    await self._send_json(
                        session,
                        {
                            "type": "playback_abort",
                            "protocol": PROTOCOL_NAME,
                            "stream_id": stream_id,
                            "reason": type(exc).__name__,
                        },
                    )
                except Exception:
                    pass
                raise
            finally:
                if session.playback is playback:
                    session.playback = None
                if completed:
                    self._update(last_error=None)

    def play_pcm16(
        self,
        pcm: bytes | bytearray | memoryview,
        *,
        sample_rate: int = 16000,
        device_id: str | None = None,
        timeout: float | None = None,
    ) -> PresencePlaybackResult:
        loop = self._loop
        if loop is None or not loop.is_running():
            raise PresencePlaybackError("Presence session loop is not running")
        duration = len(bytes(pcm)) / (sample_rate * 2.0)
        wait_timeout = timeout or (
            PLAYBACK_READY_TIMEOUT_SECONDS
            + duration
            + PLAYBACK_DRAIN_GRACE_SECONDS
            + 2.0
        )
        future = asyncio.run_coroutine_threadsafe(
            self.play_pcm16_async(
                pcm,
                sample_rate=sample_rate,
                device_id=device_id,
            ),
            loop,
        )
        return future.result(timeout=wait_timeout)

    async def play_pcm16_stream_async(
        self,
        chunks: Iterable[bytes],
        *,
        sample_rate: int = 16000,
        device_id: str | None = None,
    ) -> PresencePlaybackResult:
        if sample_rate != 16000:
            raise ValueError("Presence playback currently requires 16000 Hz PCM")
        iterator = iter(chunks)

        session = None
        if device_id is not None:
            session = self._sessions.get(str(device_id))
        else:
            for candidate in self._sessions.values():
                if (
                    candidate.capabilities.get("audio_downlink") is True
                    and candidate.capabilities.get("audio_downlink_stream") is True
                ):
                    session = candidate
                    break
        if (
            session is None
            or session.capabilities.get("audio_downlink") is not True
            or session.capabilities.get("audio_downlink_stream") is not True
        ):
            raise PresencePlaybackError(
                "no streaming-audio-capable Presence node is connected"
            )

        async with session.playback_lock:
            if self._sessions.get(session.device_id) is not session:
                raise PresencePlaybackError("Presence node disconnected before playback")
            loop = asyncio.get_running_loop()
            stream_id = self._next_stream_id()
            playback = _PlaybackState(
                stream_id=stream_id,
                total_bytes=MAX_PLAYBACK_BYTES,
                started_at=time.monotonic(),
                ready=loop.create_future(),
                drained=loop.create_future(),
                credits=asyncio.Queue(maxsize=MAX_NODE_AUDIO_CREDITS * 2),
                streaming=True,
            )
            session.playback = playback
            self._increment("playback_started")
            self._update(
                last_playback_stream_id=stream_id,
                last_playback_status="starting",
                last_playback_phase="starting",
                last_playback_total_bytes=0,
                last_playback_bytes_sent=0,
                last_playback_bytes_received=0,
                last_playback_bytes_played=0,
                last_playback_queue_high_water=0,
                last_playback_underruns=0,
                last_playback_elapsed_ms=None,
                last_playback_streaming=True,
                last_playback_first_audio_ms=None,
            )

            completed = False
            total_bytes = 0
            sequence = 0

            def drained_error(
                stage: str,
                drained: dict[str, Any],
            ) -> PresencePlaybackError:
                return PresencePlaybackError(
                    f"node ended streaming playback during {stage}: "
                    f"status={drained['status']} "
                    f"received={drained['bytes_received']}/{total_bytes} "
                    f"played={drained['bytes_played']}/{total_bytes} "
                    f"underruns={drained['underruns']}"
                )

            async def next_credit_or_drain() -> int:
                credit_task = asyncio.create_task(playback.credits.get())
                try:
                    done, _ = await asyncio.wait(
                        {credit_task, playback.drained},
                        timeout=PLAYBACK_CREDIT_TIMEOUT_SECONDS,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if playback.drained in done:
                        raise drained_error("streaming", playback.drained.result())
                    if credit_task not in done:
                        raise PresencePlaybackError(
                            "timed out waiting for playback_credit "
                            f"after {total_bytes} streamed bytes"
                        )
                    return int(credit_task.result())
                finally:
                    if not credit_task.done():
                        credit_task.cancel()
                        try:
                            await credit_task
                        except asyncio.CancelledError:
                            pass

            try:
                self._update(last_playback_phase="waiting_ready")
                await self._send_json(
                    session,
                    {
                        "type": "playback_begin",
                        "protocol": PROTOCOL_NAME,
                        "stream_id": stream_id,
                        "codec": "pcm_s16le",
                        "sample_rate": sample_rate,
                        "channels": 1,
                        "sample_width": 2,
                        "total_bytes": 0,
                        "total_samples": 0,
                        "chunk_bytes": MEDIA_CHUNK_BYTES,
                        "integrity": "crc32_per_chunk",
                        "streaming": True,
                    },
                )
                ready_done, _ = await asyncio.wait(
                    {playback.ready, playback.drained},
                    timeout=PLAYBACK_READY_TIMEOUT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if playback.drained in ready_done:
                    raise drained_error("waiting_ready", playback.drained.result())
                if playback.ready not in ready_done:
                    raise PresencePlaybackError(
                        "timed out waiting for playback_ready"
                    )
                credits, capacity = playback.ready.result()
                self._update(
                    last_playback_status="streaming",
                    last_playback_phase="streaming",
                )

                available_credits = int(credits)
                pending = b""
                reached_eof = False
                while not reached_eof or pending:
                    if not pending and not reached_eof:
                        item = await asyncio.to_thread(
                            _next_pcm_stream_item,
                            iterator,
                        )
                        if item is _PCM_STREAM_END:
                            reached_eof = True
                            continue
                        if not isinstance(item, (bytes, bytearray, memoryview)):
                            raise TypeError("PCM stream yielded a non-bytes chunk")
                        pending = bytes(item)
                        if not pending:
                            continue
                        if len(pending) % 2:
                            raise ValueError("PCM stream yielded an unaligned chunk")
                        if total_bytes + len(pending) > MAX_PLAYBACK_BYTES:
                            raise ValueError(
                                "PCM stream exceeds the bounded playback limit"
                            )

                    while pending:
                        while available_credits <= 0:
                            available_credits += await next_credit_or_drain()
                            if available_credits > capacity:
                                raise PresencePlaybackError(
                                    "node returned more credits than queue capacity"
                                )
                        chunk = pending[:MEDIA_CHUNK_BYTES]
                        pending = pending[len(chunk) :]
                        await self._send_binary(
                            session,
                            encode_media_frame(
                                PresenceMediaFrame(
                                    kind=MEDIA_KIND_PCM_DOWNLINK,
                                    stream_id=stream_id,
                                    sequence=sequence,
                                    payload=chunk,
                                )
                            ),
                        )
                        total_bytes += len(chunk)
                        sequence += 1
                        available_credits -= 1
                        self._update(last_playback_bytes_sent=total_bytes)

                if total_bytes == 0 or total_bytes % 2:
                    raise ValueError("PCM stream completed without aligned audio")

                self._update(
                    last_playback_phase="waiting_drain",
                    last_playback_total_bytes=total_bytes,
                )
                await self._send_json(
                    session,
                    {
                        "type": "playback_end",
                        "protocol": PROTOCOL_NAME,
                        "stream_id": stream_id,
                        "total_bytes": total_bytes,
                        "chunks": sequence,
                    },
                )
                duration_seconds = total_bytes / (sample_rate * 2.0)
                try:
                    drained = await asyncio.wait_for(
                        asyncio.shield(playback.drained),
                        timeout=duration_seconds + PLAYBACK_DRAIN_GRACE_SECONDS,
                    )
                except asyncio.TimeoutError as exc:
                    raise PresencePlaybackError(
                        "timed out waiting for playback_drained "
                        f"after streaming {total_bytes} bytes"
                    ) from exc

                elapsed_ms = (time.monotonic() - playback.started_at) * 1000.0
                result = PresencePlaybackResult(
                    stream_id=stream_id,
                    status=str(drained["status"]),
                    total_bytes=total_bytes,
                    bytes_received=int(drained["bytes_received"]),
                    bytes_played=int(drained["bytes_played"]),
                    queue_high_water=int(drained["queue_high_water"]),
                    underruns=int(drained["underruns"]),
                    elapsed_ms=elapsed_ms,
                    first_audio_ms=(
                        None
                        if playback.first_audio_at is None
                        else (playback.first_audio_at - playback.started_at) * 1000.0
                    ),
                )
                self._update(
                    last_playback_status=result.status,
                    last_playback_phase="complete",
                    last_playback_bytes_received=result.bytes_received,
                    last_playback_bytes_played=result.bytes_played,
                    last_playback_queue_high_water=result.queue_high_water,
                    last_playback_underruns=result.underruns,
                    last_playback_elapsed_ms=round(result.elapsed_ms, 1),
                    last_playback_first_audio_ms=(
                        None
                        if result.first_audio_ms is None
                        else round(result.first_audio_ms, 1)
                    ),
                )
                if not result.completed:
                    raise PresencePlaybackError(
                        "node streaming playback did not drain cleanly: "
                        f"status={result.status} received={result.bytes_received} "
                        f"played={result.bytes_played} underruns={result.underruns}"
                    )
                completed = True
                self._increment("playback_completed")
                with self._status_lock:
                    self._status["playback_bytes_sent"] = int(
                        self._status.get("playback_bytes_sent", 0)
                    ) + total_bytes
                return result
            except Exception as exc:
                self._increment("playback_failed")
                self._update(
                    last_playback_status="failed",
                    last_playback_phase="failed",
                    last_playback_total_bytes=total_bytes,
                    last_error=f"{type(exc).__name__}: {exc}",
                )
                try:
                    await self._send_json(
                        session,
                        {
                            "type": "playback_abort",
                            "protocol": PROTOCOL_NAME,
                            "stream_id": stream_id,
                            "reason": type(exc).__name__,
                        },
                    )
                except Exception:
                    pass
                raise
            finally:
                try:
                    await asyncio.to_thread(_close_pcm_stream_iterator, iterator)
                except Exception:
                    pass
                if session.playback is playback:
                    session.playback = None
                if completed:
                    self._update(last_error=None)

    def play_pcm16_stream(
        self,
        chunks: Iterable[bytes],
        *,
        sample_rate: int = 16000,
        device_id: str | None = None,
        timeout: float = 180.0,
    ) -> PresencePlaybackResult:
        loop = self._loop
        if loop is None or not loop.is_running():
            raise PresencePlaybackError("Presence session loop is not running")
        future = asyncio.run_coroutine_threadsafe(
            self.play_pcm16_stream_async(
                chunks,
                sample_rate=sample_rate,
                device_id=device_id,
            ),
            loop,
        )
        return future.result(timeout=timeout)

    async def _handle_connection(self, connection: ServerConnection) -> None:
        session: _Session | None = None
        try:
            try:
                first_frame = await asyncio.wait_for(
                    connection.recv(),
                    timeout=self._hello_timeout_seconds,
                )
                hello = _parse_hello(first_frame)
            except asyncio.TimeoutError:
                self._increment("protocol_rejected")
                self._update(last_error="hello timeout")
                await connection.close(code=4000, reason="hello timeout")
                return
            except PresenceSessionProtocolError as exc:
                await self._reject_protocol(connection, exc)
                return

            now = time.monotonic()
            session = _Session(
                device_id=hello["device_id"],
                session_id=uuid.uuid4().hex,
                connection=connection,
                connected_at=now,
                last_seen_at=now,
                firmware=hello["firmware"],
                profile=hello["profile"],
                capabilities=hello["capabilities"],
            )
            previous = self._sessions.get(session.device_id)
            self._sessions[session.device_id] = session
            if previous is not None and previous.connection is not connection:
                self._increment("replacements")
                self._fail_pending_playback(
                    previous,
                    PresencePlaybackError("Presence node was replaced by reconnect"),
                )
                await self._cancel_display_sync_task(previous)
                self._fail_pending_display(
                    previous,
                    PresenceDisplayError(
                        "Presence node was replaced by reconnect"
                    ),
                )
                self._fail_pending_camera(
                    previous,
                    PresenceCameraError(
                        "Presence node was replaced during camera snapshot"
                    ),
                )
                await self._cancel_turn_task(previous)
                await previous.connection.close(
                    code=4001,
                    reason="replaced by reconnect",
                )

            self._increment("connections_total")
            self._update(
                state="connected",
                last_error=None,
                last_device_id=session.device_id,
                last_session_id=session.session_id,
                last_firmware=session.firmware,
                last_profile=session.profile,
                last_capabilities=dict(session.capabilities),
                last_connected_at=now,
                last_seen_at=now,
                last_sequence=None,
                active_sessions=len(self._sessions),
            )
            await self._send_json(
                session,
                {
                    "type": "welcome",
                    "protocol": PROTOCOL_NAME,
                    "session_id": session.session_id,
                    "heartbeat_ms": int(self._heartbeat_seconds * 1000),
                    "server_time_ms": _now_ms(),
                },
            )
            if (
                self._initial_display_state is not None
                and session.capabilities.get("display") is True
            ):
                session.display_sync_task = asyncio.create_task(
                    self._sync_initial_display(session),
                    name=f"presence-display-sync-{session.device_id}",
                )

            while True:
                try:
                    message = await asyncio.wait_for(
                        connection.recv(),
                        timeout=self._timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    self._increment("heartbeat_timeouts")
                    self._update(last_error="heartbeat timeout")
                    await connection.close(code=4000, reason="heartbeat timeout")
                    return

                now = time.monotonic()
                session.last_seen_at = now
                self._update(last_seen_at=now)
                try:
                    await self._handle_control_message(session, message)
                except PresenceSessionProtocolError as exc:
                    await self._reject_protocol(connection, exc)
                    return
        except ConnectionClosed:
            pass
        except Exception as exc:
            self._increment("errors")
            self._update(last_error=f"{type(exc).__name__}: {exc}")
        finally:
            if session is not None:
                self._fail_pending_playback(
                    session,
                    PresencePlaybackError("Presence node disconnected during playback"),
                )
                await self._cancel_display_sync_task(session)
                self._fail_pending_display(
                    session,
                    PresenceDisplayError(
                        "Presence node disconnected during display command"
                    ),
                )
                self._fail_pending_camera(
                    session,
                    PresenceCameraError(
                        "Presence node disconnected during camera snapshot"
                    ),
                )
                await self._cancel_turn_task(session)
                current = self._sessions.get(session.device_id)
                if current is session:
                    self._sessions.pop(session.device_id, None)
                    self._increment("disconnects")
            self._update(
                state="connected" if self._sessions else "listening",
                active_sessions=len(self._sessions),
            )

    async def run(self) -> None:
        self._update(state="starting", last_error=None)
        self._loop = asyncio.get_running_loop()
        try:
            async with serve(
                self._handle_connection,
                self._host,
                self._port,
                process_request=self._process_request,
                compression=None,
                ping_interval=None,
                close_timeout=1.0,
                max_size=MAX_TEXT_FRAME_BYTES,
                max_queue=4,
                server_header=None,
            ) as server:
                sockets = tuple(server.sockets or ())
                bound_port = sockets[0].getsockname()[1] if sockets else self._port
                self._update(state="listening", bound_port=int(bound_port))
                self._started_event.set()
                await self._stop_event.wait()
                self._update(state="stopping")
                sessions = tuple(self._sessions.values())
                if sessions:
                    await asyncio.gather(
                        *(
                            item.connection.close(
                                code=1001,
                                reason="Nana Core shutting down",
                            )
                            for item in sessions
                        ),
                        return_exceptions=True,
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._increment("errors")
            self._update(
                state="error",
                last_error=f"{type(exc).__name__}: {exc}",
            )
            self._started_event.set()
            raise
        finally:
            for session in tuple(self._sessions.values()):
                self._fail_pending_playback(
                    session,
                    PresencePlaybackError("Presence session server stopped"),
                )
                await self._cancel_display_sync_task(session)
                self._fail_pending_display(
                    session,
                    PresenceDisplayError("Presence session server stopped"),
                )
                self._fail_pending_camera(
                    session,
                    PresenceCameraError(
                        "Presence session server stopped during camera snapshot"
                    ),
                )
                await self._cancel_turn_task(session)
            self._sessions.clear()
            self._loop = None
            self._update(active_sessions=0)
            if self._status.get("state") != "error":
                self._update(state="stopped")

    def stop(self) -> None:
        if self._stop_event.is_set():
            return
        self._update(state="stopping")
        self._stop_event.set()

    def snapshot(self) -> dict[str, Any]:
        with self._status_lock:
            status = dict(self._status)
        now = time.monotonic()
        connected_at = status.get("last_connected_at")
        last_seen_at = status.get("last_seen_at")
        status.update(
            {
                "protocol": PROTOCOL_NAME,
                "path": SESSION_PATH,
                "host": self._host,
                "configured_port": self._port,
                "heartbeat_seconds": self._heartbeat_seconds,
                "timeout_seconds": self._timeout_seconds,
                "active_sessions": len(self._sessions),
                "connected_age_s": (
                    max(0.0, now - connected_at)
                    if connected_at is not None
                    else None
                ),
                "last_seen_age_s": (
                    max(0.0, now - last_seen_at)
                    if last_seen_at is not None
                    else None
                ),
            }
        )
        return status


_ACTIVE_LOCK = threading.Lock()
_ACTIVE_SERVER: PresenceSessionServer | None = None
_CONFIG_STATUS: dict[str, Any] = {
    "enabled": False,
    "host": "0.0.0.0",
    "port": 8765,
    "token_present": False,
    "heartbeat_seconds": 5.0,
    "timeout_seconds": 15.0,
}


def configure_presence_session_status(
    *,
    enabled: bool,
    host: str,
    port: int,
    token_present: bool,
    heartbeat_seconds: float,
    timeout_seconds: float,
) -> None:
    with _ACTIVE_LOCK:
        _CONFIG_STATUS.update(
            {
                "enabled": bool(enabled),
                "host": str(host),
                "port": int(port),
                "token_present": bool(token_present),
                "heartbeat_seconds": float(heartbeat_seconds),
                "timeout_seconds": float(timeout_seconds),
            }
        )


def set_active_presence_session_server(
    server: PresenceSessionServer | None,
) -> None:
    global _ACTIVE_SERVER
    with _ACTIVE_LOCK:
        _ACTIVE_SERVER = server


def get_active_presence_session_server() -> PresenceSessionServer | None:
    with _ACTIVE_LOCK:
        return _ACTIVE_SERVER


def presence_session_snapshot() -> dict[str, Any]:
    with _ACTIVE_LOCK:
        server = _ACTIVE_SERVER
        config = dict(_CONFIG_STATUS)
    if server is None:
        return {
            **config,
            "protocol": PROTOCOL_NAME,
            "path": SESSION_PATH,
            "configured_port": config["port"],
            "bound_port": None,
            "state": "disabled" if not config["enabled"] else "not_started",
            "active_sessions": 0,
            "connections_total": 0,
            "auth_rejected": 0,
            "protocol_rejected": 0,
            "replacements": 0,
            "disconnects": 0,
            "heartbeat_timeouts": 0,
            "heartbeats": 0,
            "errors": 0,
            "last_error": None,
            "last_device_id": None,
            "last_session_id": None,
            "last_seen_age_s": None,
        }
    snapshot = server.snapshot()
    snapshot.update(
        {
            "enabled": config["enabled"],
            "token_present": config["token_present"],
        }
    )
    return snapshot


def _diagnosis(status: dict[str, Any]) -> tuple[str, str]:
    if not status.get("enabled"):
        return "DISABLED", "Set NANA_PRESENCE_SESSION_ENABLED=1 to listen."
    if not status.get("token_present"):
        return "TOKEN_MISSING", "Set a private NANA_PRESENCE_SESSION_TOKEN."
    if status.get("state") == "error":
        return "LISTENER_ERROR", "Inspect the bind address, port, and last error."
    if status.get("active_sessions", 0) > 0:
        if status.get("last_camera_status") == "failed":
            return "CAMERA_FAILED", "Inspect the bounded snapshot counters and node log."
        if status.get("last_playback_status") == "failed":
            return "PLAYBACK_FAILED", "Inspect playback counters and the node error."
        if (status.get("last_capabilities") or {}).get("audio_downlink") is True:
            return "AUDIO_READY", "The authenticated node accepts bounded PCM downlink."
        return "CONNECTED", "The node session and heartbeat are healthy."
    if status.get("state") in {"starting", "created"}:
        return "STARTING", "Wait for the WebSocket listener to bind."
    if status.get("state") == "listening":
        return "WAITING_FOR_NODE", "Power the node and verify its LAN URI and token."
    return "STOPPED", "Start Nana Core to accept Presence-node sessions."


def print_presence_session_status() -> None:
    status = presence_session_snapshot()
    print("Presence Node Session")
    print("  Action: read-only diagnostics; no playback is started by this command.")
    print(
        "  Config: "
        f"enabled={status.get('enabled')} | "
        f"listen={status.get('host')}:{status.get('configured_port')} | "
        f"path={status.get('path')} | token_present={status.get('token_present')}"
    )
    print(
        "  Runtime: "
        f"state={status.get('state')} | bound_port={status.get('bound_port') or 'none'} | "
        f"active={status.get('active_sessions', 0)} | "
        f"connections={status.get('connections_total', 0)}"
    )
    age = status.get("last_seen_age_s")
    print(
        "  Last node: "
        f"device={status.get('last_device_id') or 'none'} | "
        f"session={status.get('last_session_id') or 'none'} | "
        f"seen_age={f'{age:.1f}s' if age is not None else 'none'} | "
        f"sequence={status.get('last_sequence')}"
    )
    print(
        "  Heartbeat: "
        f"interval={status.get('heartbeat_seconds')}s | "
        f"timeout={status.get('timeout_seconds')}s | "
        f"received={status.get('heartbeats', 0)} | "
        f"timeouts={status.get('heartbeat_timeouts', 0)}"
    )
    print(
        "  Audio downlink: "
        f"capable={(status.get('last_capabilities') or {}).get('audio_downlink', False)} | "
        f"streaming={(status.get('last_capabilities') or {}).get('audio_downlink_stream', False)} | "
        f"started={status.get('playback_started', 0)} | "
        f"completed={status.get('playback_completed', 0)} | "
        f"failed={status.get('playback_failed', 0)}"
    )
    print(
        "  Audio uplink: "
        f"capable={(status.get('last_capabilities') or {}).get('audio_uplink', False)} | "
        f"started={status.get('capture_started', 0)} | "
        f"completed={status.get('capture_completed', 0)} | "
        f"rejected={status.get('capture_rejected', 0)} | "
        f"failed={status.get('capture_failed', 0)}"
    )
    print(
        "  Display control: "
        f"capable={(status.get('last_capabilities') or {}).get('display', False)} | "
        f"sent={status.get('display_sent', 0)} | "
        f"accepted={status.get('display_accepted', 0)} | "
        f"rejected={status.get('display_rejected', 0)} | "
        f"failed={status.get('display_failed', 0)}"
    )
    print(
        "  Camera snapshot: "
        f"capable={(status.get('last_capabilities') or {}).get('camera', False)} | "
        f"started={status.get('camera_started', 0)} | "
        f"completed={status.get('camera_completed', 0)} | "
        f"rejected={status.get('camera_rejected', 0)} | "
        f"failed={status.get('camera_failed', 0)}"
    )
    print(
        "  Last display: "
        f"request={status.get('last_display_request_id') or 'none'} | "
        f"kind={status.get('last_display_kind', 'none')} | "
        f"tag={status.get('last_display_tag', 'none')} | "
        f"status={status.get('last_display_status', 'none')} | "
        f"reason={status.get('last_display_reason', 'none')}"
    )
    camera_elapsed_ms = status.get("last_camera_elapsed_ms")
    print(
        "  Last camera: "
        f"stream={status.get('last_camera_stream_id') or 'none'} | "
        f"status={status.get('last_camera_status', 'none')} | "
        f"reason={status.get('last_camera_reason', 'none')} | "
        f"frame={status.get('last_camera_width', 0)}x"
        f"{status.get('last_camera_height', 0)} | "
        f"bytes={status.get('last_camera_bytes', 0)} | "
        f"chunks={status.get('last_camera_chunks', 0)} | "
        f"elapsed={f'{float(camera_elapsed_ms):.1f}ms' if camera_elapsed_ms is not None else 'none'}"
    )
    capture_elapsed_ms = status.get("last_capture_elapsed_ms")
    print(
        "  Last capture: "
        f"stream={status.get('last_capture_stream_id') or 'none'} | "
        f"status={status.get('last_capture_status', 'none')} | "
        f"reason={status.get('last_capture_reason', 'none')} | "
        f"wire={status.get('last_capture_wire_bytes', 0)}B | "
        f"effective={status.get('last_capture_effective_bytes', 0)}B | "
        f"chunks={status.get('last_capture_chunks', 0)} | "
        f"trim={status.get('last_capture_trim_samples', 0)} samples | "
        f"elapsed={f'{float(capture_elapsed_ms):.1f}ms' if capture_elapsed_ms is not None else 'none'}"
    )
    playback_elapsed_ms = status.get("last_playback_elapsed_ms")
    playback_first_audio_ms = status.get("last_playback_first_audio_ms")
    print(
        "  Last playback: "
        f"stream={status.get('last_playback_stream_id') or 'none'} | "
        f"status={status.get('last_playback_status', 'none')} | "
        f"phase={status.get('last_playback_phase', 'none')} | "
        f"streaming={status.get('last_playback_streaming', False)} | "
        f"sent={status.get('last_playback_bytes_sent', 0)}/"
        f"{status.get('last_playback_total_bytes', 0)}B | "
        f"received={status.get('last_playback_bytes_received', 0)}B | "
        f"played={status.get('last_playback_bytes_played', 0)}B | "
        f"queue_high_water={status.get('last_playback_queue_high_water', 0)} | "
        f"underruns={status.get('last_playback_underruns', 0)} | "
        f"first_audio={f'{float(playback_first_audio_ms):.1f}ms' if playback_first_audio_ms is not None else 'none'} | "
        f"elapsed={f'{float(playback_elapsed_ms):.1f}ms' if playback_elapsed_ms is not None else 'none'}"
    )
    print(f"  Last error: {status.get('last_error') or 'none'}")


def print_presence_session_diagnostics() -> None:
    status = presence_session_snapshot()
    print_presence_session_status()
    diagnosis, action = _diagnosis(status)
    print(f"  Diagnosis: {diagnosis}")
    print(f"  Next action: {action}")
    print(
        "  Scope: authenticated control plus bounded half-duplex PCM16/16k mono; "
        "bounded display state/events and one-shot RAM-only JPEG snapshots are enabled."
    )


__all__ = [
    "PROTOCOL_NAME",
    "SESSION_PATH",
    "PresenceAudioCapture",
    "PresenceAudioCaptureHandler",
    "PresenceCaptureOutcome",
    "PresenceCameraError",
    "PresenceCameraFrame",
    "PresenceDisplayError",
    "PresenceDisplayResult",
    "PresencePlaybackError",
    "PresencePlaybackResult",
    "PresenceSessionProtocolError",
    "PresenceSessionServer",
    "configure_presence_session_status",
    "get_active_presence_session_server",
    "presence_session_snapshot",
    "print_presence_session_diagnostics",
    "print_presence_session_status",
    "set_active_presence_session_server",
]
