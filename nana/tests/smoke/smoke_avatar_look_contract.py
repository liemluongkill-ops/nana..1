"""Offline checks for Nana's target-driven head look contract."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _request(method: str, url: str, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request, timeout=2.0) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def test_contract_and_absolute_target():
    from nana.runtime.avatar_intent_gateway import AvatarIntent

    intent = AvatarIntent.from_payload(
        {
            "action": "look",
            "target": {"yaw": -24, "pitch": -3, "roll": 2},
            "move_s": 0.8,
            "hold_s": 0.7,
            "return_s": 0.8,
            "return_to": "neutral",
        }
    )
    assert intent.target is not None
    assert intent.target.yaw == -24.0
    assert intent.duration_s == 2.3
    custom = AvatarIntent.from_payload(
        {
            "action": "look",
            "target": {"yaw": 5, "pitch": 0, "roll": 0},
            "move_s": 0.3,
            "hold_s": 0.2,
            "return_s": 0.4,
        }
    )
    assert custom.duration_s == 0.9, custom
    wire = intent.to_wire()
    assert wire["intent"]["target"] == {"yaw": -24.0, "pitch": -3.0, "roll": 2.0}
    assert "bone_path" not in json.dumps(wire)


def test_contract_rejects_unsafe_or_ambiguous_targets():
    from nana.runtime.avatar_intent_gateway import AvatarIntent, AvatarIntentError

    invalid = (
        {"action": "look"},
        {"action": "look", "target": {"yaw": 31, "pitch": 0, "roll": 0}},
        {"action": "look", "target": {"yaw": 0, "pitch": 16, "roll": 0}},
        {"action": "look", "target": {"yaw": 0, "pitch": 0, "roll": 13}},
        {"action": "look", "target": {"yaw": 0, "pitch": 0, "roll": 0}, "return_to": "delta"},
        {"action": "wave", "target": {"yaw": 0, "pitch": 0, "roll": 0}},
    )
    for payload in invalid:
        try:
            AvatarIntent.from_payload(payload)
        except AvatarIntentError:
            continue
        raise AssertionError(f"unsafe look payload accepted: {payload}")


def test_preset_is_absolute_and_cursor_is_monotonic():
    from nana.runtime.avatar_intent_gateway import (
        AvatarIntentGateway,
        RecordingTransport,
        look_payload_for_preset,
    )

    left = look_payload_for_preset("left")
    right = look_payload_for_preset("right")
    assert left["target"]["yaw"] < 0
    assert right["target"]["yaw"] > 0

    gateway = AvatarIntentGateway(
        enabled=True,
        host="127.0.0.1",
        port=0,
        transport=RecordingTransport(),
    )
    assert gateway.start(), gateway.snapshot()
    try:
        first = gateway.submit_payload(left)
        assert first.ok, first
        deadline = time.time() + 2.0
        while time.time() < deadline and gateway.commands_since(0)["cursor"] < 1:
            time.sleep(0.02)
        batch = gateway.commands_since(0)
        assert batch["cursor"] == 1, batch
        assert batch["commands"][0]["intent"]["target"]["yaw"] < 0, batch

        consumed = gateway.commands_since(batch["cursor"])
        assert consumed["commands"] == [], consumed

        second = gateway.submit_payload(right)
        assert second.ok, second
        deadline = time.time() + 2.0
        while time.time() < deadline and gateway.commands_since(batch["cursor"])["cursor"] < 2:
            time.sleep(0.02)
        next_batch = gateway.commands_since(batch["cursor"])
        assert len(next_batch["commands"]) == 1, next_batch
        assert next_batch["commands"][0]["intent"]["target"]["yaw"] > 0, next_batch
    finally:
        gateway.stop()


def test_http_commands_endpoint():
    from nana.runtime.avatar_intent_gateway import AvatarIntentGateway, RecordingTransport

    gateway = AvatarIntentGateway(
        enabled=True,
        host="127.0.0.1",
        port=0,
        transport=RecordingTransport(),
    )
    assert gateway.start(), gateway.snapshot()
    try:
        result = gateway.submit_payload(
            {
                "action": "look",
                "target": {"yaw": 0, "pitch": 0, "roll": 0},
                "return_to": "hold",
            }
        )
        assert result.ok, result
        deadline = time.time() + 2.0
        while time.time() < deadline and gateway.commands_since(0)["cursor"] < 1:
            time.sleep(0.02)
        status, body = _request(
            "GET",
            f"http://127.0.0.1:{gateway.bound_port}/v1/avatar/commands?after=0",
        )
        assert status == 200, body
        assert body["protocol"] == "nana.avatar.v1", body
        assert body["commands"][0]["intent"]["action"] == "look", body
    finally:
        gateway.stop()


def test_operator_look_command_is_private_and_semantic():
    import asyncio
    import io
    from contextlib import redirect_stdout

    from nana.cli.stage_runtime_commands import handle_stage_runtime_command
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    output = io.StringIO()
    with redirect_stdout(output):
        handled = asyncio.run(
            handle_stage_runtime_command(None, "/avatar-runtime-look-preview right")
        )
    assert handled, output.getvalue()
    assert "target=yaw:24.0" in output.getvalue(), output.getvalue()
    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/avatar-runtime-look left") == "backstage_command"


def test_operator_look_submit_reaches_recording_transport():
    import asyncio
    import io
    from contextlib import redirect_stdout

    from nana.cli.stage_runtime_commands import handle_stage_runtime_command
    from nana.runtime.avatar_intent_gateway import (
        AvatarIntentGateway,
        RecordingTransport,
    )

    transport = RecordingTransport()
    gateway = AvatarIntentGateway(
        enabled=True,
        host="127.0.0.1",
        port=0,
        transport=transport,
    )
    AvatarIntentGateway._instance = gateway
    assert gateway.start(), gateway.snapshot()
    try:
        output = io.StringIO()
        with redirect_stdout(output):
            handled = asyncio.run(
                handle_stage_runtime_command(None, "/avatar-runtime-look right")
            )
        assert handled, output.getvalue()
        deadline = time.time() + 2.0
        while time.time() < deadline and not transport.payloads():
            time.sleep(0.02)
        payloads = transport.payloads()
        assert payloads, output.getvalue()
        assert payloads[0]["intent"]["action"] == "look", payloads
        assert payloads[0]["intent"]["target"]["yaw"] > 0, payloads
    finally:
        gateway.stop()
        AvatarIntentGateway.reset_for_test()


def run() -> bool:
    tests = [
        test_contract_and_absolute_target,
        test_contract_rejects_unsafe_or_ambiguous_targets,
        test_preset_is_absolute_and_cursor_is_monotonic,
        test_http_commands_endpoint,
        test_operator_look_command_is_private_and_semantic,
        test_operator_look_submit_reaches_recording_transport,
    ]
    passed = 0
    for test in tests:
        test()
        passed += 1
        print(f"PASS {test.__name__}")
    print(f"smoke_avatar_look_contract: {passed}/{len(tests)} passed")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
