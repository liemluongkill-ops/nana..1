"""Task 13 Core loopback contract with fake dispatcher and voice receipts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiohttp import ClientSession, WSMsgType  # noqa: E402

# Bypass Nana's eager root facade. The composition module has no model/data
# imports; fail if that boundary ever changes.
package = types.ModuleType('nana')
package.__path__ = [str(ROOT / 'nana')]
sys.modules['nana'] = package
def guard(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes)):
        name = str(args[0]).replace('\\', '/').lower()
        if '/nana/data/' in name or name.endswith('/.env') or '/runtime_logs/' in name:
            raise AssertionError('production data access forbidden')
    if event == 'socket.connect' and isinstance(args[1], tuple):
        if args[1][0] not in ('127.0.0.1', '::1'):
            raise AssertionError('non-loopback connection forbidden')
sys.addaudithook(guard)
from nana.runtime.private_web_chat_runtime import _build_private_web_chat_runtime  # noqa: E402
from nana.runtime.nana_web_ownership import OwnershipResult  # noqa: E402
from nana.runtime.nana_web_ownership import NanaWebOwnershipLease  # noqa: E402
from nana.runtime.private_turn_observer import PrivateTurnResult  # noqa: E402


EPOCH = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
CLIENT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
TURN = "11111111-1111-4111-8111-111111111111"
CORRELATION = "22222222-2222-4222-8222-222222222222"
ORIGIN = "http://127.0.0.1:5174"


class Launcher:
    def __init__(self):
        self.lease = NanaWebOwnershipLease(
            core_boot_id=EPOCH,
            launch_nonce="a" * 64,
            node_pid=1234,
            node_process_creation_time=1.0,
            app_root=(ROOT / "components" / "nana-app").as_posix(),
            host="127.0.0.1",
            port=5174,
            command_fingerprint="vite-preview-v1",
        )

    def ownership_lease(self):
        return self.lease


class Ownership:
    def verify(self, _lease):
        return OwnershipResult(True, "trusted")


async def _receive_json(ws):
    while True:
        message = await ws.receive(timeout=2)
        if message.type == WSMsgType.TEXT:
            return json.loads(message.data)
        if message.type in {WSMsgType.CLOSED, WSMsgType.CLOSE, WSMsgType.ERROR}:
            raise AssertionError(f"socket closed during smoke: {message}")


async def run_smoke():
    calls = []

    async def dispatch(text, observer):
        calls.append(text)
        observer.on_thinking()
        observer.on_text_delta("Xin ")
        observer.on_text_delta("chao Nana")
        observer.on_text_final("Xin chao Nana")
        observer.on_voice_queued(7)
        observer.on_voice_first_audio(7)
        observer.on_voice_complete(7)
        return PrivateTurnResult(False, "complete", "Xin chao Nana", None, 7)

    runtime = _build_private_web_chat_runtime(
        enabled=True,
        voice=object(),
        turn_lock=asyncio.Lock(),
        dispatch_turn_unlocked=dispatch,
        launcher=Launcher(),
        ownership_verifier=Ownership(),
        server_epoch=EPOCH,
        port=0,
    )
    assert runtime is not None
    assert await runtime.start() is True
    port = runtime.server.bound_port
    session = ClientSession()
    try:
        response = await session.post(
            f"http://127.0.0.1:{port}/v1/web-chat/bootstrap",
            headers={"Origin": ORIGIN},
        )
        if response.status != 200:
            raise AssertionError(f"bootstrap failed: {response.status} {await response.text()}")
        grant = await response.json()
        ws = await session.ws_connect(
            f"http://127.0.0.1:{port}/v1/web-chat",
            headers={"Origin": ORIGIN},
        )
        nonce = "nonce-loopback-1"
        await ws.send_json({
            "type": "session.hello",
            "protocol": "nana.private-web-chat.v1",
            "handshake_id": grant["handshake_id"],
            "capability": grant["capability"],
            "handshake_nonce": nonce,
            "client_instance_id": CLIENT,
        })
        welcome = await _receive_json(ws)
        await ws.send_json({
            "type": "session.ready",
            "protocol": "nana.private-web-chat.v1",
            "server_epoch": EPOCH,
            "provisional_session_id": welcome["provisional_session_id"],
            "handshake_nonce": nonce,
        })
        active = await _receive_json(ws)
        await ws.send_json({
            "type": "session.active_ack",
            "protocol": "nana.private-web-chat.v1",
            "server_epoch": EPOCH,
            "session_id": active["session_id"],
            "handshake_nonce": nonce,
        })
        confirmed = await _receive_json(ws)
        assert confirmed["type"] == "session.confirmed"
        await ws.send_json({
            "protocol": "nana.private-web-chat.v1",
            "server_epoch": EPOCH,
            "session_id": active["session_id"],
            "turn_id": TURN,
            "correlation_id": CORRELATION,
            "revision": 0,
            "type": "chat.submit",
            "payload": {"text": "hello"},
        })
        events = []
        for _ in range(10):
            event = await _receive_json(ws)
            events.append(event)
            if event.get("type") == "turn.state" and event.get("payload", {}).get("state") == "complete":
                break
        types = [event["type"] for event in events]
        assert types.count("assistant.delta") == 2, events
        assert "assistant.final" in types, events
        assert any(event.get("payload", {}).get("state") == "delivered" for event in events), events
        assert calls == ["hello"]
        await ws.close()
    finally:
        await session.close()
        await runtime.stop(0.1)
    assert runtime.server.bound_port == 0


def main():
    asyncio.run(run_node_smoke())
    asyncio.run(run_smoke())
    asyncio.run(run_session_sequence_smoke())
    print("smoke_private_web_chat_loopback: PASS (3/3)")


async def run_session_sequence_smoke():
    """Wire ordering survives multiple turns, errors and snapshot replay."""
    from smoke_private_web_chat_server import connect_confirmed

    calls = []

    async def dispatch(text, observer):
        calls.append(text)
        observer.on_text_delta("fixture")
        observer.on_text_final("fixture")
        return PrivateTurnResult(False, "complete", "fixture", None, None)

    runtime = _build_private_web_chat_runtime(
        enabled=True, voice=object(), turn_lock=asyncio.Lock(),
        dispatch_turn_unlocked=dispatch, launcher=Launcher(),
        ownership_verifier=Ownership(), server_epoch=EPOCH, port=0,
    )
    assert await runtime.start()
    client = None
    try:
        client, ws, _, _, active, _ = await connect_confirmed(runtime.server)
        observed = [active]
        completed = {}

        async def request(kind, turn_id, payload, terminal_type):
            await ws.send_json({
                "protocol": "nana.private-web-chat.v1", "server_epoch": EPOCH,
                "session_id": active["session_id"], "turn_id": turn_id,
                "correlation_id": CORRELATION, "revision": 0,
                "type": kind, "payload": payload,
            })
            while True:
                event = await _receive_json(ws)
                if "event_sequence" in event:
                    observed.append(event)
                if event["type"] == terminal_type:
                    if terminal_type != "turn.state" or event["payload"]["state"] == "complete":
                        return event

        second_turn = "33333333-3333-4333-8333-333333333333"
        for turn_id in (TURN, second_turn):
            completed[turn_id] = await request("chat.submit", turn_id, {"text": "fixture"}, "turn.state")
            error = await request("settings.get", "44444444-4444-4444-8444-444444444444", {}, "error")
            assert error["payload"]["code"] == "unsupported_in_v1"

        duplicate = await request("chat.submit", second_turn, {"text": "fixture"}, "turn.snapshot")
        assert duplicate["revision"] == completed[second_turn]["revision"] + 1
        missing = "55555555-5555-4555-8555-555555555555"
        snapshot = await request("chat.reconcile", missing, {
            "turn_server_epoch": EPOCH, "client_instance_id": CLIENT,
            "turn_id": missing, "correlation_id": CORRELATION, "revision": 0,
        }, "turn.snapshot")
        assert snapshot["payload"]["reason_code"] == "turn_not_found"
        sequences = [event["event_sequence"] for event in observed]
        assert all(a < b for a, b in zip(sequences, sequences[1:])), sequences
        assert calls == ["fixture", "fixture"], calls
        for turn_id in (TURN, second_turn):
            revisions = [event["revision"] for event in observed
                         if event.get("turn_id") == turn_id]
            assert revisions == list(range(1, len(revisions) + 1)), revisions

        # A replacement session owns a fresh wire counter; a retained turn
        # keeps its revision and must never be dispatched again.
        await ws.close()
        await client.close()
        async with asyncio.timeout(2):
            while runtime.server.snapshot()["active_sessions"]:
                await asyncio.sleep(0)
        client, ws, _, _, active, _ = await connect_confirmed(runtime.server)
        observed = [active]
        replay = await request("chat.reconcile", second_turn, {
            "turn_server_epoch": EPOCH, "client_instance_id": CLIENT,
            "turn_id": second_turn, "correlation_id": CORRELATION, "revision": 0,
        }, "turn.snapshot")
        assert replay["event_sequence"] == 2, replay
        assert replay["session_id"] == active["session_id"]
        assert replay["revision"] == duplicate["revision"] + 1
        assert calls == ["fixture", "fixture"], calls
    finally:
        if client is not None:
            await client.close()
        await runtime.stop(.2)
    assert runtime.server.bound_port == 0


async def run_node_smoke():
    from nana.runtime.private_voice_receipts import PrivateVoiceContext
    calls = []
    pending = []
    async def dispatch(text, observer):
        calls.append(text)
        observer.on_text_delta('fixture ')
        observer.on_text_delta('final')
        observer.on_text_final('fixture final')
        identity = observer.identity
        async def audio():
            await asyncio.sleep(.02)
            receipt = runtime.receipt_ledger.queued(PrivateVoiceContext(identity.server_epoch,
                identity.session_id, identity.turn_id, identity.correlation_id), engine_ticket=len(calls))
            runtime.receipt_ledger.first_audio(receipt, engine_ticket=receipt.engine_ticket)
            runtime.receipt_ledger.complete(receipt, engine_ticket=receipt.engine_ticket, completed=True)
        pending.append(asyncio.create_task(audio()))
        return PrivateTurnResult(False, 'complete', 'fixture final', None, None)
    runtime = _build_private_web_chat_runtime(enabled=True, voice=object(), turn_lock=asyncio.Lock(),
        dispatch_turn_unlocked=dispatch, launcher=Launcher(), ownership_verifier=Ownership(), server_epoch=EPOCH, port=0)
    assert await runtime.start()
    process = None
    try:
        process = await asyncio.create_subprocess_exec('node', str(Path(__file__).resolve().parents[3] / "components/nana-app/tests/coreChatLoopback.mjs"),
            str(runtime.server.bound_port), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
        assert process.returncode == 0, stdout.decode() + stderr.decode() + str(runtime.server.snapshot())
        assert b'NODE_LOOPBACK_PASS' in stdout
        assert len(calls) == 2, len(calls)
        print(stdout.decode().strip())
    finally:
        if process is not None and process.returncode is None:
            process.kill(); await process.wait()
        await asyncio.gather(*pending, return_exceptions=True)
        await runtime.stop(.2)
    assert runtime.server.bound_port == 0


async def serve_ui_fixture():
    from nana.runtime.private_voice_receipts import PrivateVoiceContext
    async def dispatch(_text, observer):
        observer.on_text_delta('Fixture ')
        await asyncio.sleep(.1)
        observer.on_text_delta('reply from Core')
        identity = observer.identity
        receipt = runtime.receipt_ledger.queued(PrivateVoiceContext(identity.server_epoch,
            identity.session_id, identity.turn_id, identity.correlation_id), engine_ticket=len(runtime.receipt_ledger) + 1)
        observer.on_text_final('Fixture reply from Core')
        async def audio():
            await asyncio.sleep(.1)
            runtime.receipt_ledger.first_audio(receipt, engine_ticket=receipt.engine_ticket)
            runtime.receipt_ledger.complete(receipt, engine_ticket=receipt.engine_ticket, completed=True)
        asyncio.create_task(audio())
        return PrivateTurnResult(False, 'complete', 'Fixture reply from Core', None, receipt.engine_ticket)
    runtime = _build_private_web_chat_runtime(enabled=True, voice=object(), turn_lock=asyncio.Lock(),
        dispatch_turn_unlocked=dispatch, launcher=Launcher(), ownership_verifier=Ownership(), server_epoch=EPOCH)
    assert await runtime.start()
    print('UI_FIXTURE_READY 127.0.0.1:8767; fake dispatch/audio; no production data')
    try:
        await asyncio.Future()
    finally:
        await runtime.stop(.2)


if __name__ == "__main__":
    if '--serve-ui' in sys.argv:
        try:
            asyncio.run(serve_ui_fixture())
        except KeyboardInterrupt:
            print('UI_FIXTURE_STOPPED')
    else:
        main()
