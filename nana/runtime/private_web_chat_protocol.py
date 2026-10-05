"""Pure contracts for the loopback-only Nana Private Web Chat v1 protocol."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
import json
import re
from uuid import UUID


PROTOCOL_NAME = "nana.private-web-chat.v1"
MAX_WS_TEXT_BYTES = 64 * 1024
MAX_USER_TEXT_CHARS = 4000
HEARTBEAT_INTERVAL_SECONDS = 5.0
HEARTBEAT_PONG_TIMEOUT_SECONDS = 10.0
SESSION_IDLE_TIMEOUT_SECONDS = 20.0


class PrivateWebCloseCode(IntEnum):
    """Fixed WebSocket close mapping for the private bridge."""

    NORMAL = 1000
    CORE_SHUTDOWN = 1001
    INTERNAL_ERROR = 1011
    HANDSHAKE_REQUIRED = 4001
    CAPABILITY_REJECTED = 4002
    ORIGIN_REJECTED = 4003
    PROTOCOL_ERROR = 4004
    SESSION_CAPACITY = 4005
    HEARTBEAT_TIMEOUT = 4006
    SLOW_CONSUMER = 4007
    BRIDGE_DISABLED = 4008
    EPOCH_CHANGED = 4009
    WEB_TURN_STUCK = 4010


_SAFE_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class PrivateWebProtocolError(ValueError):
    """A bounded protocol failure that is safe to expose by reason code."""

    def __init__(
        self,
        reason_code: str,
        close_code: PrivateWebCloseCode = PrivateWebCloseCode.PROTOCOL_ERROR,
    ) -> None:
        if not isinstance(reason_code, str) or not _SAFE_CODE_PATTERN.fullmatch(reason_code):
            reason_code = "protocol_error"
        self.reason_code = reason_code
        self.close_code = close_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SessionContext:
    server_epoch: str
    session_id: str
    client_instance_id: str


@dataclass(frozen=True)
class ClientFrame:
    message_type: str
    server_epoch: str
    session_id: str
    turn_id: str
    correlation_id: str
    revision: int
    payload: Mapping[str, object]


@dataclass(frozen=True)
class ServerEvent:
    message_type: str
    server_epoch: str
    session_id: str
    turn_id: str
    correlation_id: str
    revision: int
    event_sequence: int
    payload: Mapping[str, object]


_CLIENT_ROOT_KEYS = frozenset(
    {
        "protocol",
        "server_epoch",
        "session_id",
        "turn_id",
        "correlation_id",
        "revision",
        "type",
        "payload",
    }
)
_CLIENT_MESSAGE_TYPES = frozenset(
    {
        "chat.submit",
        "chat.reconcile",
        "runtime.state.get",
        "session.pong",
        "settings.get",
        "settings.set",
        "model.list",
        "model.select",
    }
)
_EMPTY_PAYLOAD_TYPES = frozenset(
    {
        "runtime.state.get",
        "session.pong",
        "settings.get",
        "settings.set",
        "model.list",
        "model.select",
    }
)
_SERVER_EVENT_TYPES = frozenset(
    {
        "turn.state",
        "assistant.delta",
        "assistant.final",
        "voice.state",
        "error",
        "turn.snapshot",
    }
)
_TURN_STATES = frozenset(
    {"accepted", "thinking", "generated", "complete", "failed", "unknown"}
)
_VOICE_STATES = frozenset(
    {"idle", "queued", "speaking", "delivered", "failed", "unknown"}
)
_CORE_STATUSES = frozenset({"ready", "busy", "shutting_down", "degraded"})
_RUNTIME_REASON_CODES = frozenset(
    {
        "bridge_disabled",
        "untrusted_preview_owner",
        "port_conflict",
        "core_busy",
        "core_sleeping",
        "provider_failed",
        "voice_failed",
        "epoch_changed",
        "unknown_outcome",
        "session_capacity",
        "handshake_capacity",
        "protocol_error",
        "session_queue_full",
        "ledger_full",
        "web_turn_stuck",
        "shutting_down",
        "degraded",
    }
)
_ADMIN_INPUTS = frozenset(
    {"exit", "thoat", "thoát", "dừng lại", "mở ai", "tắt ai"}
)
_RUNTIME_CAPABILITIES: Mapping[str, bool] = {
    "chat_submit": True,
    "reconcile": True,
    "runtime_state": True,
    "microphone": False,
    "settings": False,
    "model_select": False,
    "cancel": False,
}


def _fail(reason_code: str) -> None:
    raise PrivateWebProtocolError(reason_code)


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        _fail("invalid_uuid")
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError):
        _fail("invalid_uuid")
    if str(parsed) != value:
        _fail("invalid_uuid")
    return value


def _integer_at_least(value: object, minimum: int, reason_code: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        _fail(reason_code)
    return value


def _safe_code(value: object, reason_code: str) -> str:
    if not isinstance(value, str) or not _SAFE_CODE_PATTERN.fullmatch(value):
        _fail(reason_code)
    return value


def _safe_code_or_none(value: object, reason_code: str) -> str | None:
    if value is None:
        return None
    return _safe_code(value, reason_code)


def _runtime_reason_code_or_none(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _RUNTIME_REASON_CODES:
        _fail("invalid_reason_code")
    return value


def _exact_keys(value: Mapping[str, object], keys: set[str] | frozenset[str]) -> bool:
    return set(value) == set(keys)


def _validate_context(context: SessionContext) -> None:
    if not isinstance(context, SessionContext):
        _fail("invalid_session_context")
    _canonical_uuid(context.server_epoch)
    _canonical_uuid(context.session_id)
    _canonical_uuid(context.client_instance_id)


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> None:
    _fail("invalid_json")


def _parse_json_object(raw: str) -> Mapping[str, object]:
    if not isinstance(raw, str):
        _fail("invalid_frame_type")
    try:
        raw_size = len(raw.encode("utf-8"))
    except UnicodeEncodeError:
        _fail("invalid_utf8")
    if raw_size > MAX_WS_TEXT_BYTES:
        _fail("frame_too_large")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except PrivateWebProtocolError:
        raise
    except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
        _fail("invalid_json")
    if not isinstance(value, Mapping):
        _fail("invalid_frame_schema")
    return value


def _validate_submit_payload(payload: Mapping[str, object]) -> dict[str, object]:
    if not _exact_keys(payload, {"text"}):
        _fail("invalid_submit_payload")
    text = payload["text"]
    if not isinstance(text, str) or not text.strip():
        _fail("invalid_submit_payload")
    if len(text) > MAX_USER_TEXT_CHARS:
        _fail("text_too_long")
    normalized = text.strip().casefold()
    if normalized.startswith("/") or normalized in _ADMIN_INPUTS:
        _fail("admin_input_not_allowed")
    return {"text": text}


def _validate_reconcile_payload(
    payload: Mapping[str, object],
    *,
    context: SessionContext,
    turn_id: str,
    correlation_id: str,
    revision: int,
) -> dict[str, object]:
    expected = {
        "turn_server_epoch",
        "client_instance_id",
        "turn_id",
        "correlation_id",
        "revision",
    }
    if not _exact_keys(payload, expected):
        _fail("invalid_reconcile_payload")
    turn_server_epoch = _canonical_uuid(payload["turn_server_epoch"])
    client_instance_id = _canonical_uuid(payload["client_instance_id"])
    payload_turn_id = _canonical_uuid(payload["turn_id"])
    payload_correlation_id = _canonical_uuid(payload["correlation_id"])
    payload_revision = _integer_at_least(
        payload["revision"], 0, "invalid_reconcile_payload"
    )
    if client_instance_id != context.client_instance_id:
        _fail("client_instance_mismatch")
    if payload_turn_id != turn_id or payload_correlation_id != correlation_id:
        _fail("turn_identity_mismatch")
    if payload_revision != revision:
        _fail("revision_mismatch")
    return {
        "turn_server_epoch": turn_server_epoch,
        "client_instance_id": client_instance_id,
        "turn_id": payload_turn_id,
        "correlation_id": payload_correlation_id,
        "revision": payload_revision,
    }


def parse_client_frame(raw: str, session_context: SessionContext) -> ClientFrame:
    """Parse one post-confirmation client frame using fail-closed schemas."""

    _validate_context(session_context)
    frame = _parse_json_object(raw)
    if not _exact_keys(frame, _CLIENT_ROOT_KEYS):
        _fail("invalid_frame_schema")
    if frame["protocol"] != PROTOCOL_NAME:
        _fail("protocol_mismatch")

    server_epoch = _canonical_uuid(frame["server_epoch"])
    session_id = _canonical_uuid(frame["session_id"])
    turn_id = _canonical_uuid(frame["turn_id"])
    correlation_id = _canonical_uuid(frame["correlation_id"])
    if server_epoch != session_context.server_epoch:
        _fail("server_epoch_mismatch")
    if session_id != session_context.session_id:
        _fail("session_mismatch")

    revision = _integer_at_least(frame["revision"], 0, "invalid_revision")
    message_type = frame["type"]
    if not isinstance(message_type, str) or message_type not in _CLIENT_MESSAGE_TYPES:
        _fail("unsupported_message_type")
    payload_value = frame["payload"]
    if not isinstance(payload_value, Mapping):
        _fail("invalid_payload")

    if message_type == "chat.submit":
        if revision != 0:
            _fail("invalid_revision")
        payload = _validate_submit_payload(payload_value)
    elif message_type == "chat.reconcile":
        payload = _validate_reconcile_payload(
            payload_value,
            context=session_context,
            turn_id=turn_id,
            correlation_id=correlation_id,
            revision=revision,
        )
    elif message_type in _EMPTY_PAYLOAD_TYPES:
        if revision != 0:
            _fail("invalid_revision")
        if payload_value:
            _fail("invalid_payload")
        payload = {}
    else:  # The allowlist above makes this unreachable.
        _fail("unsupported_message_type")

    return ClientFrame(
        message_type=message_type,
        server_epoch=server_epoch,
        session_id=session_id,
        turn_id=turn_id,
        correlation_id=correlation_id,
        revision=revision,
        payload=payload,
    )


def _validate_state_payload(
    payload: Mapping[str, object],
    states: frozenset[str],
    reason_code: str,
) -> dict[str, object]:
    keys = set(payload)
    if keys not in ({"state"}, {"state", "reason_code"}):
        _fail(reason_code)
    state = payload["state"]
    if not isinstance(state, str) or state not in states:
        _fail(reason_code)
    result: dict[str, object] = {"state": state}
    if "reason_code" in payload:
        result["reason_code"] = _safe_code_or_none(payload["reason_code"], reason_code)
    return result


def _validate_server_payload(
    message_type: str, payload: Mapping[str, object]
) -> dict[str, object]:
    if message_type == 'turn.snapshot':
        if not _exact_keys(payload, {'turn_server_epoch', 'turn_state', 'voice_state', 'text', 'delta_index', 'reason_code'}):
            _fail('invalid_snapshot_payload')
        _canonical_uuid(payload['turn_server_epoch'])
        if not isinstance(payload['turn_state'], str) or payload['turn_state'] not in _TURN_STATES:
            _fail('invalid_snapshot_payload')
        if not isinstance(payload['voice_state'], str) or payload['voice_state'] not in _VOICE_STATES or not isinstance(payload['text'], str):
            _fail('invalid_snapshot_payload')
        _integer_at_least(payload['delta_index'], 0, 'invalid_snapshot_payload')
        _safe_code_or_none(payload['reason_code'], 'invalid_snapshot_payload')
        return dict(payload)
    if message_type == "assistant.delta":
        if not _exact_keys(payload, {"delta_index", "text"}):
            _fail("invalid_delta_payload")
        delta_index = _integer_at_least(
            payload["delta_index"], 1, "invalid_delta_payload"
        )
        text = payload["text"]
        if not isinstance(text, str) or not text:
            _fail("invalid_delta_payload")
        return {"delta_index": delta_index, "text": text}
    if message_type == "assistant.final":
        if not _exact_keys(payload, {"text"}) or not isinstance(payload["text"], str):
            _fail("invalid_final_payload")
        return {"text": payload["text"]}
    if message_type == "turn.state":
        return _validate_state_payload(payload, _TURN_STATES, "invalid_turn_state_payload")
    if message_type == "voice.state":
        return _validate_state_payload(
            payload, _VOICE_STATES, "invalid_voice_state_payload"
        )
    if message_type == "error":
        if not _exact_keys(payload, {"code", "retryable"}):
            _fail("invalid_error_payload")
        code = _safe_code(payload["code"], "invalid_error_payload")
        retryable = payload["retryable"]
        if not isinstance(retryable, bool):
            _fail("invalid_error_payload")
        return {"code": code, "retryable": retryable}
    _fail("unsupported_server_event_type")


def build_server_event(
    *,
    context: SessionContext,
    turn_id: str,
    correlation_id: str,
    revision: int,
    event_sequence: int,
    message_type: str,
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Build one exact turn-scoped event from coordinator-owned counters."""

    _validate_context(context)
    canonical_turn_id = _canonical_uuid(turn_id)
    canonical_correlation_id = _canonical_uuid(correlation_id)
    checked_revision = _integer_at_least(revision, 1, "invalid_revision")
    checked_sequence = _integer_at_least(
        event_sequence, 1, "invalid_event_sequence"
    )
    if not isinstance(message_type, str) or message_type not in _SERVER_EVENT_TYPES:
        _fail("unsupported_server_event_type")
    if not isinstance(payload, Mapping):
        _fail("invalid_payload")
    checked_payload = _validate_server_payload(message_type, payload)
    event = ServerEvent(
        message_type=message_type,
        server_epoch=context.server_epoch,
        session_id=context.session_id,
        turn_id=canonical_turn_id,
        correlation_id=canonical_correlation_id,
        revision=checked_revision,
        event_sequence=checked_sequence,
        payload=checked_payload,
    )
    return {
        "protocol": PROTOCOL_NAME,
        "server_epoch": event.server_epoch,
        "session_id": event.session_id,
        "turn_id": event.turn_id,
        "correlation_id": event.correlation_id,
        "revision": event.revision,
        "event_sequence": event.event_sequence,
        "type": event.message_type,
        "payload": dict(event.payload),
    }


def build_runtime_state(
    *,
    context: SessionContext,
    revision: int,
    core_status: str,
    chat_available: bool,
    reason_code: str | None,
    active_turn_state: str | None,
    voice_state: str,
    queue_depth: int,
) -> dict[str, object]:
    """Build the exact redacted runtime-state allowlist."""

    _validate_context(context)
    checked_revision = _integer_at_least(revision, 1, "invalid_revision")
    if not isinstance(core_status, str) or core_status not in _CORE_STATUSES:
        _fail("invalid_core_status")
    if not isinstance(chat_available, bool):
        _fail("invalid_chat_available")
    checked_reason = _runtime_reason_code_or_none(reason_code)
    if active_turn_state is not None:
        if not isinstance(active_turn_state, str) or active_turn_state not in _TURN_STATES:
            _fail("invalid_turn_state")
    if not isinstance(voice_state, str) or voice_state not in _VOICE_STATES:
        _fail("invalid_voice_state")
    if not isinstance(queue_depth, int) or isinstance(queue_depth, bool) or queue_depth not in {0, 1}:
        _fail("invalid_queue_depth")
    return {
        "protocol": PROTOCOL_NAME,
        "server_epoch": context.server_epoch,
        "session_id": context.session_id,
        "revision": checked_revision,
        "core_status": core_status,
        "chat_available": chat_available,
        "reason_code": checked_reason,
        "active_turn_state": active_turn_state,
        "voice_state": voice_state,
        "queue_depth": queue_depth,
        "capabilities": dict(_RUNTIME_CAPABILITIES),
    }


__all__ = [
    "HEARTBEAT_INTERVAL_SECONDS",
    "HEARTBEAT_PONG_TIMEOUT_SECONDS",
    "MAX_USER_TEXT_CHARS",
    "MAX_WS_TEXT_BYTES",
    "PROTOCOL_NAME",
    "SESSION_IDLE_TIMEOUT_SECONDS",
    "ClientFrame",
    "PrivateWebCloseCode",
    "PrivateWebProtocolError",
    "ServerEvent",
    "SessionContext",
    "build_runtime_state",
    "build_server_event",
    "parse_client_frame",
]
