"""Offline smoke for the Nana Private Web Chat v1 protocol contract.

The smoke is pure: it opens no socket and touches no model, memory, voice,
browser, OBS, credential, or persistent-data boundary.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from nana.runtime.private_web_chat_protocol import (  # noqa: E402
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


SERVER_EPOCH = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SESSION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
CLIENT_INSTANCE_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
TURN_ID = "11111111-1111-4111-8111-111111111111"
CORRELATION_ID = "22222222-2222-4222-8222-222222222222"

CLIENT_ROOT_KEYS = {
    "protocol",
    "server_epoch",
    "session_id",
    "turn_id",
    "correlation_id",
    "revision",
    "type",
    "payload",
}
SERVER_EVENT_KEYS = CLIENT_ROOT_KEYS | {"event_sequence"}
RUNTIME_STATE_KEYS = {
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
RUNTIME_CAPABILITIES = {
    "chat_submit": True,
    "reconcile": True,
    "runtime_state": True,
    "microphone": False,
    "settings": False,
    "model_select": False,
    "cancel": False,
}


def make_context() -> SessionContext:
    return SessionContext(SERVER_EPOCH, SESSION_ID, CLIENT_INSTANCE_ID)


def make_frame(
    message_type: str,
    *,
    revision: int = 0,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "protocol": PROTOCOL_NAME,
        "server_epoch": SERVER_EPOCH,
        "session_id": SESSION_ID,
        "turn_id": TURN_ID,
        "correlation_id": CORRELATION_ID,
        "revision": revision,
        "type": message_type,
        "payload": {} if payload is None else payload,
    }


def expect_protocol_error(code: str, callback) -> PrivateWebProtocolError:
    try:
        callback()
    except PrivateWebProtocolError as exc:
        assert exc.reason_code == code, exc
        assert str(exc) == code, exc
        assert re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(exc)), exc
        return exc
    raise AssertionError(f"expected PrivateWebProtocolError({code!r})")


def test_constants_close_codes_and_heartbeat_contract_are_exact() -> None:
    assert PROTOCOL_NAME == "nana.private-web-chat.v1"
    assert MAX_WS_TEXT_BYTES == 64 * 1024
    assert MAX_USER_TEXT_CHARS == 4000
    assert HEARTBEAT_INTERVAL_SECONDS == 5.0
    assert HEARTBEAT_PONG_TIMEOUT_SECONDS == 10.0
    assert SESSION_IDLE_TIMEOUT_SECONDS == 20.0
    assert {
        "NORMAL": PrivateWebCloseCode.NORMAL.value,
        "CORE_SHUTDOWN": PrivateWebCloseCode.CORE_SHUTDOWN.value,
        "INTERNAL_ERROR": PrivateWebCloseCode.INTERNAL_ERROR.value,
        "HANDSHAKE_REQUIRED": PrivateWebCloseCode.HANDSHAKE_REQUIRED.value,
        "CAPABILITY_REJECTED": PrivateWebCloseCode.CAPABILITY_REJECTED.value,
        "ORIGIN_REJECTED": PrivateWebCloseCode.ORIGIN_REJECTED.value,
        "PROTOCOL_ERROR": PrivateWebCloseCode.PROTOCOL_ERROR.value,
        "SESSION_CAPACITY": PrivateWebCloseCode.SESSION_CAPACITY.value,
        "HEARTBEAT_TIMEOUT": PrivateWebCloseCode.HEARTBEAT_TIMEOUT.value,
        "SLOW_CONSUMER": PrivateWebCloseCode.SLOW_CONSUMER.value,
        "BRIDGE_DISABLED": PrivateWebCloseCode.BRIDGE_DISABLED.value,
        "EPOCH_CHANGED": PrivateWebCloseCode.EPOCH_CHANGED.value,
        "WEB_TURN_STUCK": PrivateWebCloseCode.WEB_TURN_STUCK.value,
    } == {
        "NORMAL": 1000,
        "CORE_SHUTDOWN": 1001,
        "INTERNAL_ERROR": 1011,
        "HANDSHAKE_REQUIRED": 4001,
        "CAPABILITY_REJECTED": 4002,
        "ORIGIN_REJECTED": 4003,
        "PROTOCOL_ERROR": 4004,
        "SESSION_CAPACITY": 4005,
        "HEARTBEAT_TIMEOUT": 4006,
        "SLOW_CONSUMER": 4007,
        "BRIDGE_DISABLED": 4008,
        "EPOCH_CHANGED": 4009,
        "WEB_TURN_STUCK": 4010,
    }


def test_submit_schema_uuid_and_revision_zero_are_exact() -> None:
    raw_frame = make_frame("chat.submit", payload={"text": "Chao Nana"})
    assert set(raw_frame) == CLIENT_ROOT_KEYS

    parsed = parse_client_frame(json.dumps(raw_frame), make_context())

    assert parsed.message_type == "chat.submit"
    assert parsed.server_epoch == SERVER_EPOCH
    assert parsed.session_id == SESSION_ID
    assert parsed.turn_id == TURN_ID
    assert parsed.correlation_id == CORRELATION_ID
    assert parsed.revision == 0
    assert parsed.payload == {"text": "Chao Nana"}

    nonzero = make_frame("chat.submit", revision=1, payload={"text": "No"})
    expect_protocol_error(
        "invalid_revision",
        lambda: parse_client_frame(json.dumps(nonzero), make_context()),
    )


def test_client_frames_fail_closed_on_unknown_fields_and_invalid_ids() -> None:
    extra = make_frame("chat.submit", payload={"text": "hello"})
    extra["unexpected"] = True
    expect_protocol_error(
        "invalid_frame_schema",
        lambda: parse_client_frame(json.dumps(extra), make_context()),
    )

    invalid_turn = make_frame("chat.submit", payload={"text": "hello"})
    invalid_turn["turn_id"] = "not-a-uuid"
    expect_protocol_error(
        "invalid_uuid",
        lambda: parse_client_frame(json.dumps(invalid_turn), make_context()),
    )

    noncanonical = make_frame("chat.submit", payload={"text": "hello"})
    noncanonical["correlation_id"] = "ABCDEFAB-CDEF-4ABC-8ABC-ABCDEFABCDEF"
    expect_protocol_error(
        "invalid_uuid",
        lambda: parse_client_frame(json.dumps(noncanonical), make_context()),
    )

    wrong_session = make_frame("chat.submit", payload={"text": "hello"})
    wrong_session["session_id"] = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    expect_protocol_error(
        "session_mismatch",
        lambda: parse_client_frame(json.dumps(wrong_session), make_context()),
    )


def test_duplicate_json_keys_are_rejected() -> None:
    raw = (
        '{"protocol":"nana.private-web-chat.v1",'
        '"server_epoch":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",'
        '"session_id":"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",'
        '"turn_id":"11111111-1111-4111-8111-111111111111",'
        '"correlation_id":"22222222-2222-4222-8222-222222222222",'
        '"revision":0,"revision":0,"type":"chat.submit",'
        '"payload":{"text":"hello"}}'
    )
    expect_protocol_error(
        "duplicate_json_key",
        lambda: parse_client_frame(raw, make_context()),
    )


def test_control_heartbeat_and_reserved_types_are_recognized() -> None:
    context = make_context()
    reconcile_payload = {
        "turn_server_epoch": SERVER_EPOCH,
        "client_instance_id": CLIENT_INSTANCE_ID,
        "turn_id": TURN_ID,
        "correlation_id": CORRELATION_ID,
        "revision": 2,
    }
    reconcile = parse_client_frame(
        json.dumps(make_frame("chat.reconcile", revision=2, payload=reconcile_payload)),
        context,
    )
    assert reconcile.message_type == "chat.reconcile"
    assert reconcile.payload == reconcile_payload

    for message_type in (
        "runtime.state.get",
        "session.pong",
        "settings.get",
        "settings.set",
        "model.list",
        "model.select",
    ):
        parsed = parse_client_frame(json.dumps(make_frame(message_type)), context)
        assert parsed.message_type == message_type
        assert parsed.payload == {}

    expect_protocol_error(
        "unsupported_message_type",
        lambda: parse_client_frame(json.dumps(make_frame("admin.shutdown")), context),
    )


def test_indexed_delta_ordering_and_server_event_roots_are_exact() -> None:
    context = make_context()
    first = build_server_event(
        context=context,
        turn_id=TURN_ID,
        correlation_id=CORRELATION_ID,
        revision=3,
        event_sequence=7,
        message_type="assistant.delta",
        payload={"delta_index": 1, "text": "Chao"},
    )
    second = build_server_event(
        context=context,
        turn_id=TURN_ID,
        correlation_id=CORRELATION_ID,
        revision=4,
        event_sequence=8,
        message_type="assistant.delta",
        payload={"delta_index": 2, "text": " Nana"},
    )

    assert set(first) == SERVER_EVENT_KEYS
    assert first["payload"] == {"delta_index": 1, "text": "Chao"}
    assert second["payload"] == {"delta_index": 2, "text": " Nana"}
    assert [first["revision"], second["revision"]] == [3, 4]
    assert [first["event_sequence"], second["event_sequence"]] == [7, 8]


def test_server_events_require_positive_ordering_and_safe_errors() -> None:
    context = make_context()
    common = {
        "context": context,
        "turn_id": TURN_ID,
        "correlation_id": CORRELATION_ID,
        "message_type": "assistant.delta",
        "payload": {"delta_index": 1, "text": "ok"},
    }
    expect_protocol_error(
        "invalid_revision",
        lambda: build_server_event(revision=0, event_sequence=1, **common),
    )
    expect_protocol_error(
        "invalid_event_sequence",
        lambda: build_server_event(revision=1, event_sequence=0, **common),
    )
    bad_index = dict(common)
    bad_index["payload"] = {"delta_index": 0, "text": "out of order"}
    expect_protocol_error(
        "invalid_delta_payload",
        lambda: build_server_event(revision=1, event_sequence=1, **bad_index),
    )

    safe_error = build_server_event(
        context=context,
        turn_id=TURN_ID,
        correlation_id=CORRELATION_ID,
        revision=5,
        event_sequence=9,
        message_type="error",
        payload={"code": "provider_failed", "retryable": True},
    )
    assert safe_error["payload"] == {"code": "provider_failed", "retryable": True}
    expect_protocol_error(
        "invalid_error_payload",
        lambda: build_server_event(
            context=context,
            turn_id=TURN_ID,
            correlation_id=CORRELATION_ID,
            revision=5,
            event_sequence=9,
            message_type="error",
            payload={
                "code": "provider_failed",
                "retryable": True,
                "detail": "RuntimeError: secret provider response",
            },
        ),
    )


def test_runtime_state_is_exact_redacted_allowlist() -> None:
    state = build_runtime_state(
        context=make_context(),
        revision=4,
        core_status="ready",
        chat_available=True,
        reason_code=None,
        active_turn_state=None,
        voice_state="idle",
        queue_depth=0,
    )

    assert set(state) == RUNTIME_STATE_KEYS
    assert state["capabilities"] == RUNTIME_CAPABILITIES
    assert state == {
        "protocol": PROTOCOL_NAME,
        "server_epoch": SERVER_EPOCH,
        "session_id": SESSION_ID,
        "revision": 4,
        "core_status": "ready",
        "chat_available": True,
        "reason_code": None,
        "active_turn_state": None,
        "voice_state": "idle",
        "queue_depth": 0,
        "capabilities": RUNTIME_CAPABILITIES,
    }
    serialized = json.dumps(state).lower()
    for forbidden in (
        "provider",
        "model_name",
        "model_config",
        "prompt",
        "memory",
        "history",
        "credential",
        "api_key",
        "filesystem",
        "exception",
        "transport",
    ):
        assert forbidden not in serialized

    expect_protocol_error(
        "invalid_queue_depth",
        lambda: build_runtime_state(
            context=make_context(),
            revision=4,
            core_status="ready",
            chat_available=True,
            reason_code=None,
            active_turn_state=None,
            voice_state="idle",
            queue_depth=2,
        ),
    )


def test_runtime_state_reason_code_uses_fixed_allowlist() -> None:
    allowed = (
        None,
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
    )
    for reason_code in allowed:
        state = build_runtime_state(
            context=make_context(),
            revision=1,
            core_status="ready",
            chat_available=True,
            reason_code=reason_code,
            active_turn_state=None,
            voice_state="idle",
            queue_depth=0,
        )
        assert state["reason_code"] == reason_code

    for reason_code in ("api_key_secret123", "provider_openai", "model_gpt5"):
        expect_protocol_error(
            "invalid_reason_code",
            lambda reason_code=reason_code: build_runtime_state(
                context=make_context(),
                revision=1,
                core_status="ready",
                chat_available=False,
                reason_code=reason_code,
                active_turn_state=None,
                voice_state="idle",
                queue_depth=0,
            ),
        )


def test_runtime_state_enum_fields_reject_non_strings_safely() -> None:
    cases = (
        ("core_status", [], "invalid_core_status"),
        ("core_status", {}, "invalid_core_status"),
        ("core_status", True, "invalid_core_status"),
        ("active_turn_state", [], "invalid_turn_state"),
        ("active_turn_state", {}, "invalid_turn_state"),
        ("active_turn_state", True, "invalid_turn_state"),
        ("voice_state", [], "invalid_voice_state"),
        ("voice_state", {}, "invalid_voice_state"),
        ("voice_state", True, "invalid_voice_state"),
        ("reason_code", [], "invalid_reason_code"),
        ("reason_code", {}, "invalid_reason_code"),
        ("reason_code", True, "invalid_reason_code"),
    )
    base: dict[str, object] = {
        "context": make_context(),
        "revision": 1,
        "core_status": "ready",
        "chat_available": True,
        "reason_code": None,
        "active_turn_state": None,
        "voice_state": "idle",
        "queue_depth": 0,
    }
    for field, value, expected_code in cases:
        arguments = dict(base)
        arguments[field] = value
        expect_protocol_error(
            expected_code,
            lambda arguments=arguments: build_runtime_state(**arguments),
        )


def test_malformed_oversized_and_private_inputs_return_safe_errors() -> None:
    secret = "sk-private-should-never-escape"
    malformed = '{"payload":"' + secret
    error = expect_protocol_error(
        "invalid_json",
        lambda: parse_client_frame(malformed, make_context()),
    )
    assert secret not in str(error)

    oversized = "x" * (MAX_WS_TEXT_BYTES + 1)
    expect_protocol_error(
        "frame_too_large",
        lambda: parse_client_frame(oversized, make_context()),
    )

    too_long = make_frame(
        "chat.submit",
        payload={"text": "x" * (MAX_USER_TEXT_CHARS + 1)},
    )
    expect_protocol_error(
        "text_too_long",
        lambda: parse_client_frame(json.dumps(too_long), make_context()),
    )

    for blocked in ("/status", "exit", "thoat", "thoát", "dừng lại", "mở ai", "tắt ai"):
        frame = make_frame("chat.submit", payload={"text": blocked})
        expect_protocol_error(
            "admin_input_not_allowed",
            lambda frame=frame: parse_client_frame(json.dumps(frame), make_context()),
        )


def main() -> None:
    tests = [
        test_constants_close_codes_and_heartbeat_contract_are_exact,
        test_submit_schema_uuid_and_revision_zero_are_exact,
        test_client_frames_fail_closed_on_unknown_fields_and_invalid_ids,
        test_duplicate_json_keys_are_rejected,
        test_control_heartbeat_and_reserved_types_are_recognized,
        test_indexed_delta_ordering_and_server_event_roots_are_exact,
        test_server_events_require_positive_ordering_and_safe_errors,
        test_runtime_state_is_exact_redacted_allowlist,
        test_runtime_state_reason_code_uses_fixed_allowlist,
        test_runtime_state_enum_fields_reject_non_strings_safely,
        test_malformed_oversized_and_private_inputs_return_safe_errors,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_private_web_chat_protocol: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
