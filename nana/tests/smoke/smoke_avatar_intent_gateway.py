"""Offline smoke tests for Nana's semantic avatar intent gateway.

No Warudo, VTS, OBS, LLM, TTS, or external network calls are used here.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _intent(action: str, **fields):
    from nana.runtime.avatar_intent_gateway import AvatarIntent

    return AvatarIntent.from_payload({"action": action, **fields})


def _test_contract_rejects_untrusted_shape():
    print("[Avatar Gateway Smoke] Test 1: semantic contract rejects raw controls...")
    from nana.runtime.avatar_intent_gateway import AvatarIntentError

    for payload in (
        {"action": "rotate_head", "bone_path": "Head"},
        {"action": "wave", "bone_path": "Head"},
        {"action": "wave", "mask": "Head"},
        {"action": "wave", "duration_s": 999},
        {"action": "wave", "priority": 101},
        {"action": "wave", "interrupt_policy": "teleport"},
    ):
        try:
            _intent(payload.pop("action"), **payload)
        except AvatarIntentError:
            continue
        raise AssertionError(f"untrusted payload accepted: {payload}")
    print("  PASSED")


def _test_action_defaults_and_wire_shape():
    print("[Avatar Gateway Smoke] Test 2: action defaults produce typed wire envelope...")
    from nana.runtime.avatar_intent_gateway import PROTOCOL_NAME

    intent = _intent("wave", source="nana", channel="private")
    assert intent.kind == "gesture", intent
    assert intent.mask == "upper_body", intent
    assert intent.priority == 50, intent
    assert intent.duration_s == 2.8, intent
    wire = intent.to_wire()
    assert wire["protocol"] == PROTOCOL_NAME, wire
    assert wire["type"] == "nana_avatar_intent", wire
    assert wire["intent"]["action"] == "wave", wire
    assert "bone_path" not in json.dumps(wire), wire
    print("  PASSED")


def _test_mask_arbiter_queue_and_promotion():
    print("[Avatar Gateway Smoke] Test 3: mask conflict queues one item and promotes it...")
    from nana.runtime.avatar_intent_gateway import AvatarIntentArbiter

    arbiter = AvatarIntentArbiter(pending_limit=1)
    wave = _intent("wave", intent_id="wave-1")
    nod = _intent("nod", intent_id="nod-1")
    first = arbiter.submit(wave)
    second = arbiter.submit(nod)
    assert first.status == "accepted", first
    assert second.status == "queued", second
    claimed = arbiter.claim_dispatchable()
    assert [item.intent_id for item in claimed] == ["wave-1"], claimed
    sent = arbiter.mark_sent("wave-1", ok=True)
    assert sent.status == "sent", sent
    finished = arbiter.acknowledge("wave-1", "finished", reason="smoke_done")
    assert finished.status == "finished", finished
    snapshot = arbiter.snapshot()
    assert snapshot["active"][0]["intent"]["intent_id"] == "nod-1", snapshot
    assert snapshot["active"][0]["receipt"]["status"] == "accepted", snapshot
    print("  PASSED")


def _test_priority_replace_and_nonconflicting_face():
    print("[Avatar Gateway Smoke] Test 4: priority replacement and face/body coexistence...")
    from nana.runtime.avatar_intent_gateway import AvatarIntentArbiter

    arbiter = AvatarIntentArbiter(pending_limit=1)
    wave = _intent("wave", intent_id="wave-2")
    think = _intent("think", intent_id="think-2")
    happy = _intent("happy", intent_id="happy-2")
    assert arbiter.submit(wave).status == "accepted"
    replaced = arbiter.submit(think)
    assert replaced.status == "accepted", replaced
    assert arbiter.receipt("wave-2").status == "cancelled"
    assert arbiter.submit(happy).status == "accepted"
    active = arbiter.snapshot()["active"]
    assert {item["intent"]["intent_id"] for item in active} == {"think-2", "happy-2"}, active
    print("  PASSED")


def _test_gateway_recording_transport_and_receipt():
    print("[Avatar Gateway Smoke] Test 5: enabled gateway records without external call...")
    from nana.runtime.avatar_intent_gateway import AvatarIntentGateway, RecordingTransport

    transport = RecordingTransport()
    gateway = AvatarIntentGateway(enabled=True, host="127.0.0.1", port=0, transport=transport)
    assert gateway.start(), gateway.snapshot()
    try:
        result = gateway.submit_action("nod", intent_id="gateway-wave")
        assert result.ok and result.status_code == 202, result
        deadline = time.time() + 2.0
        while time.time() < deadline and not transport.payloads():
            time.sleep(0.02)
        payloads = transport.payloads()
        assert len(payloads) == 1, payloads
        assert payloads[0]["intent"]["action"] == "nod", payloads
        receipt = gateway.acknowledge_payload(
            {"intent_id": "gateway-wave", "status": "started"}
        )
        assert receipt.ok and receipt.receipt.status == "started", receipt
        receipt = gateway.acknowledge_payload(
            {"intent_id": "gateway-wave", "status": "finished"}
        )
        assert receipt.ok and receipt.receipt.status == "finished", receipt
    finally:
        gateway.stop()
    print("  PASSED")


def _http_json(method: str, url: str, payload=None, token: str = ""):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=2.0) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _test_http_loopback_auth_and_status():
    print("[Avatar Gateway Smoke] Test 6: HTTP loopback/auth/status contract...")
    from nana.runtime.avatar_intent_gateway import AvatarIntentGateway, RecordingTransport

    gateway = AvatarIntentGateway(
        enabled=True,
        host="127.0.0.1",
        port=0,
        token="smoke-token",
        transport=RecordingTransport(),
    )
    assert gateway.start(), gateway.snapshot()
    base = f"http://127.0.0.1:{gateway.bound_port}"
    try:
        status, body = _http_json("GET", f"{base}/v1/avatar/status")
        assert status == 401 and body["reason"] == "unauthorized", body
        status, body = _http_json("GET", f"{base}/healthz", token="smoke-token")
        assert status == 200 and body["protocol"] == "nana.avatar.v1", body
        status, body = _http_json(
            "POST",
            f"{base}/v1/avatar/preview",
            {"action": "nod"},
            token="smoke-token",
        )
        assert status == 200 and body["receipt"]["status"] == "preview", body
        status, body = _http_json(
            "POST",
            f"{base}/v1/avatar/intents",
            {"action": "nod", "intent_id": "http-wave"},
            token="smoke-token",
        )
        assert status == 202 and body["receipt"]["status"] == "accepted", body
        status, body = _http_json(
            "GET",
            f"{base}/v1/avatar/receipts/http-wave",
            token="smoke-token",
        )
        assert status == 200 and body["receipt"]["intent_id"] == "http-wave", body
    finally:
        gateway.stop()
    print("  PASSED")


def _test_private_commands_and_public_firewall():
    print("[Avatar Gateway Smoke] Test 7: private commands exist and public firewall blocks them...")
    import asyncio
    import io
    from contextlib import redirect_stdout

    from nana.cli.stage_runtime_commands import handle_stage_runtime_command
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    output = io.StringIO()
    with redirect_stdout(output):
        handled = asyncio.run(handle_stage_runtime_command(None, "/avatar-runtime-preview nod"))
    assert handled, output.getvalue()
    assert "action=nod" in output.getvalue(), output.getvalue()

    guard = get_public_stage_identity_guard()
    for command in (
        "/avatar-runtime-status",
        "/avatar-runtime-preview wave",
        "/avatar-runtime-start",
        "/avatar-runtime-stop",
        "/avatar-runtime-submit wave",
    ):
        assert guard.classify_public_input(command) == "backstage_command", command
    print("  PASSED")


def _test_sparse_event_mapping_is_opt_in():
    print("[Avatar Gateway Smoke] Test 8: sparse event mapping is opt-in...")
    from nana.runtime.avatar_intent_gateway import AvatarIntentGateway, RecordingTransport

    transport = RecordingTransport()
    gateway = AvatarIntentGateway(enabled=True, port=0, transport=transport)
    disabled = gateway.submit_event("highlight")
    assert not disabled.ok and disabled.reason == "auto_events_disabled", disabled
    gateway.auto_events_enabled = True
    assert gateway.start(), gateway.snapshot()
    try:
        ignored = gateway.submit_event("message")
        assert ignored.ok and ignored.status_code == 204, ignored
        accepted = gateway.submit_event("highlight", correlation_id="event-1")
        assert accepted.ok and accepted.intent.action == "wink_soft_smile", accepted
        deadline = time.time() + 2.0
        while time.time() < deadline and not transport.payloads():
            time.sleep(0.02)
        assert transport.payloads()[0]["intent"]["action"] == "wink_soft_smile", transport.payloads()
    finally:
        gateway.stop()
    print("  PASSED")


def _test_live_hook_emits_semantic_event_when_opted_in():
    print("[Avatar Gateway Smoke] Test 9: live hook emits semantic event when opted in...")
    from nana.runtime.avatar_event_bridge import AvatarEventBridge, get_avatar_event_bridge
    from nana.runtime.avatar_intent_gateway import AvatarIntentGateway, RecordingTransport
    from nana.runtime.avatar_live_hook import AvatarLiveHook, get_avatar_live_hook
    from nana.runtime.avatar_reactor import AvatarReactionController
    from nana.runtime.stream_state import get_stream_state

    AvatarEventBridge.reset_for_test()
    AvatarLiveHook.reset_for_test()
    AvatarReactionController.reset_for_test()
    stream = get_stream_state()
    stream.force_offline()
    stream.go_live()

    transport = RecordingTransport()
    gateway = AvatarIntentGateway(enabled=True, port=0, transport=transport)
    gateway.auto_events_enabled = True
    AvatarIntentGateway._instance = gateway
    assert gateway.start(), gateway.snapshot()
    try:
        get_avatar_event_bridge().observe_public_event(
            text="new support",
            event_type="highlight",
            source="youtube",
            viewer_name="viewer-1",
            request_id="highlight-1",
        )
        result = get_avatar_live_hook().evaluate_latest(dry_run=False)
        assert result.ok, result
        assert result.runtime_action == "wink_soft_smile", result
        assert result.runtime_status in {"accepted", "sent"}, result
        deadline = time.time() + 2.0
        while time.time() < deadline and not transport.payloads():
            time.sleep(0.02)
        assert transport.payloads()[0]["intent"]["action"] == "wink_soft_smile", transport.payloads()
    finally:
        gateway.stop()
        AvatarIntentGateway.reset_for_test()
        AvatarEventBridge.reset_for_test()
        AvatarLiveHook.reset_for_test()
        AvatarReactionController.reset_for_test()
    print("  PASSED")


def _test_warudo_transport_serializes_without_network():
    print("[Avatar Gateway Smoke] Test 10: Warudo transport serializes through injected socket...")
    import sys
    from types import SimpleNamespace

    from nana.runtime.avatar_intent_gateway import AvatarIntent, WarudoWebSocketTransport

    class FakeSocket:
        def __init__(self):
            self.sent = []
            self.closed = False

        def send(self, value):
            self.sent.append(value)

        def close(self):
            self.closed = True

    socket = FakeSocket()
    fake_websocket = SimpleNamespace(
        create_connection=lambda *args, **kwargs: socket,
    )
    previous = sys.modules.get("websocket")
    sys.modules["websocket"] = fake_websocket
    try:
        transport = WarudoWebSocketTransport("ws://127.0.0.1:9999")
        result = transport.send(AvatarIntent.from_payload({"action": "wave"}).to_wire())
        assert result.ok, result
        assert len(socket.sent) == 1, socket.sent
        payload = json.loads(socket.sent[0])
        assert payload["protocol"] == "nana.avatar.v1", payload
        assert payload["intent"]["action"] == "wave", payload
        transport.close()
        assert socket.closed, socket.closed
    finally:
        if previous is None:
            sys.modules.pop("websocket", None)
        else:
            sys.modules["websocket"] = previous
    print("  PASSED")


def _test_core_facade_keeps_transport_out_of_callers():
    print("[Avatar Gateway Smoke] Test 11: core facade exposes only semantic action...")
    from nana.runtime.avatar_intent_gateway import (
        AvatarIntentGateway,
        RecordingTransport,
        issue_avatar_action,
    )

    transport = RecordingTransport()
    gateway = AvatarIntentGateway(enabled=True, port=0, transport=transport)
    AvatarIntentGateway._instance = gateway
    assert gateway.start(), gateway.snapshot()
    try:
        result = issue_avatar_action("nod", source="nana_core", channel="private")
        assert result.ok and result.intent.action == "nod", result
        assert result.intent.kind == "gesture" and result.intent.mask == "head", result
    finally:
        gateway.stop()
        AvatarIntentGateway.reset_for_test()
    print("  PASSED")


def _test_duration_fallback_releases_without_pose_claim():
    print("[Avatar Gateway Smoke] Test 12: duration fallback releases without claiming pose...")
    from nana.runtime.avatar_intent_gateway import AvatarIntentGateway, RecordingTransport

    gateway = AvatarIntentGateway(enabled=True, port=0, transport=RecordingTransport())
    assert gateway.start(), gateway.snapshot()
    try:
        result = gateway.submit_action("blink", intent_id="blink-duration", duration_s=0.05)
        assert result.ok, result
        deadline = time.time() + 2.0
        while time.time() < deadline:
            receipt = gateway.arbiter.receipt("blink-duration")
            if receipt is not None and receipt.status == "finished":
                break
            time.sleep(0.02)
        receipt = gateway.arbiter.receipt("blink-duration")
        assert receipt is not None and receipt.status == "finished", receipt
        assert receipt.reason == "duration_elapsed_without_runtime_ack", receipt
    finally:
        gateway.stop()
    print("  PASSED")


def _test_loopback_websocket_transport():
    print("[Avatar Gateway Smoke] Test 13: loopback WebSocket transport delivers one envelope...")
    import asyncio
    import threading

    from websockets.asyncio.server import serve

    from nana.runtime.avatar_intent_gateway import AvatarIntent, WarudoWebSocketTransport

    received: list[str] = []
    ready = threading.Event()
    stop = threading.Event()
    port_holder: list[int] = []

    async def handler(connection):
        received.append(await connection.recv())

    async def server_main():
        async with serve(handler, "127.0.0.1", 0) as server:
            port_holder.append(server.sockets[0].getsockname()[1])
            ready.set()
            await asyncio.to_thread(stop.wait)

    thread = threading.Thread(target=lambda: asyncio.run(server_main()), daemon=True)
    thread.start()
    assert ready.wait(2.0), "loopback receiver did not start"
    transport = WarudoWebSocketTransport(f"ws://127.0.0.1:{port_holder[0]}")
    try:
        result = transport.send(AvatarIntent.from_payload({"action": "wave"}).to_wire())
        assert result.ok, result
        deadline = time.time() + 2.0
        while time.time() < deadline and not received:
            time.sleep(0.02)
        assert len(received) == 1, received
        payload = json.loads(received[0])
        assert payload["protocol"] == "nana.avatar.v1", payload
        assert payload["intent"]["action"] == "wave", payload
    finally:
        transport.close()
        stop.set()
        thread.join(timeout=2.0)
    assert not thread.is_alive(), "loopback receiver did not stop"
    print("  PASSED")


def run_smoke_tests() -> bool:
    print("=" * 64)
    print("Avatar Intent Gateway — Offline Smoke Tests")
    print("=" * 64)
    tests = [
        _test_contract_rejects_untrusted_shape,
        _test_action_defaults_and_wire_shape,
        _test_mask_arbiter_queue_and_promotion,
        _test_priority_replace_and_nonconflicting_face,
        _test_gateway_recording_transport_and_receipt,
        _test_http_loopback_auth_and_status,
        _test_private_commands_and_public_firewall,
        _test_sparse_event_mapping_is_opt_in,
        _test_live_hook_emits_semantic_event_when_opted_in,
        _test_warudo_transport_serializes_without_network,
        _test_core_facade_keeps_transport_out_of_callers,
        _test_duration_fallback_releases_without_pose_claim,
        _test_loopback_websocket_transport,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as exc:
            print(f"  FAILED: {exc}")
            failed += 1
        except Exception as exc:
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            failed += 1
    print("=" * 64)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 64)
    return failed == 0


if __name__ == "__main__":
    raise SystemExit(0 if run_smoke_tests() else 1)
