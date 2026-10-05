"""Authenticated private visual wiring with actual local Core transport."""
import asyncio
import json
import math
from pathlib import Path
import sys
import types
import unittest

# Reuse the existing no-production-data/non-loopback guard and ownership fake.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import smoke_private_web_chat_loopback as fixture
from aiohttp import ClientSession
from nana.runtime.private_voice_receipts import PrivateVoiceContext, PrivateVoiceReceiptBinding
from nana.runtime.private_turn_observer import PrivateTurnResult
from nana.runtime.private_web_chat_runtime import _build_private_web_chat_runtime
from nana.runtime.private_web_chat_server import PrivateWebChatServer, _Session

config = types.ModuleType('nana.config')
config.ELEVEN_PUBLIC_TTS_MODEL = 'eleven_v3'
sys.modules['nana.config'] = config

class Voice:
    def set_private_voice_receipt_ledger(self, ledger): self.ledger = ledger
    def set_private_avatar_signals(self, signals): self.signals = signals

async def connect(client, port, client_id=fixture.CLIENT):
    response = await client.post(f'http://127.0.0.1:{port}/v1/web-chat/bootstrap', headers={'Origin': fixture.ORIGIN})
    assert response.status == 200
    grant = await response.json()
    ws = await client.ws_connect(f'http://127.0.0.1:{port}/v1/web-chat', headers={'Origin': fixture.ORIGIN})
    base = {'protocol': 'nana.private-web-chat.v1', 'handshake_nonce': 'private-avatar-fixture-1'}
    await ws.send_json(dict(base, type='session.hello', handshake_id=grant['handshake_id'], capability=grant['capability'], client_instance_id=client_id))
    welcome = await fixture._receive_json(ws)
    await ws.send_json(dict(base, type='session.ready', server_epoch=fixture.EPOCH, provisional_session_id=welcome['provisional_session_id']))
    active = await fixture._receive_json(ws)
    await ws.send_json(dict(base, type='session.active_ack', server_epoch=fixture.EPOCH, session_id=active['session_id']))
    assert (await fixture._receive_json(ws))['type'] == 'session.confirmed'
    return ws, active['session_id']

class TransportSmoke(unittest.TestCase):
    def test_actual_nanaapp_client_consumes_core_visual_frames(self):
        async def run():
            voice = Voice()
            audio_tasks = []
            async def dispatch(_text, observer):
                from nana.runtime.avatar_reply_turn import AvatarReplyTurn
                ids = observer.identity
                context = PrivateVoiceContext(ids.server_epoch, ids.session_id, ids.turn_id, ids.correlation_id)
                receipt = voice.ledger.queued(context, engine_ticket=1)
                binding = PrivateVoiceReceiptBinding(context, receipt.receipt_id, voice.ledger, 1)
                AvatarReplyTurn().consider('[happy] Fixture visual reply')
                observer.on_text_final('Fixture visual reply')
                async def audio():
                    await asyncio.sleep(.05)
                    voice.ledger.first_audio(receipt, engine_ticket=1)
                    for _ in range(12):
                        voice.signals.publish_pcm_level(binding, .2)
                        await asyncio.sleep(.05)
                    voice.ledger.complete(receipt, engine_ticket=1, completed=True)
                audio_tasks.append(asyncio.create_task(audio()))
                return PrivateTurnResult(False, 'complete', 'Fixture visual reply', None, 1)
            runtime = _build_private_web_chat_runtime(enabled=True, voice=voice, turn_lock=asyncio.Lock(),
                dispatch_turn_unlocked=dispatch, launcher=fixture.Launcher(), ownership_verifier=fixture.Ownership(),
                server_epoch=fixture.EPOCH, port=0)
            await runtime.start()
            process = None
            try:
                process = await asyncio.create_subprocess_exec('node', str(Path(__file__).resolve().parents[3] / "components/nana-app/tests/privateAvatarLoopback.mjs"),
                    str(runtime.server.bound_port), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
                self.assertEqual(process.returncode, 0, stdout.decode() + stderr.decode())
                self.assertIn(b'PRIVATE_AVATAR_NODE_PASS', stdout)
                print(stdout.decode().strip())
            finally:
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
                await asyncio.gather(*audio_tasks, return_exceptions=True)
                await runtime.stop(.1)
        asyncio.run(run())

    def test_private_visual_mailbox_coalesces_without_filling_chat_queue(self):
        server = PrivateWebChatServer(server_epoch=fixture.EPOCH, launcher=fixture.Launcher(), ownership_verifier=fixture.Ownership(), port=0)
        session = _Session(object(), None, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', fixture.CLIENT, 'fixture', state='confirmed')
        for sequence in range(1, 501):
            self.assertTrue(server.enqueue_visual_event(session, {'type': 'avatar.signal', 'session_id': session.provisional_session_id, 'visual_sequence': sequence}))
        self.assertEqual(session.queue.qsize(), 1)
        self.assertEqual(session.visual_message.payload['visual_sequence'], 500)
        self.assertTrue(server.enqueue_event(session, {'type': 'assistant.final', 'payload': {'text': 'fixture'}}))
        self.assertEqual(session.queue.qsize(), 2)
        session.closed = True
        self.assertFalse(server.enqueue_visual_event(session, {'type': 'avatar.signal'}))

    def test_actual_session_receives_private_signals_without_changing_turn_revisions(self):
        async def run():
            voice = Voice()
            binding = None
            async def dispatch(_text, observer):
                nonlocal binding
                from nana.runtime.avatar_reply_turn import AvatarReplyTurn, daily_reaction_prompt
                self.assertIn('NATURAL AVATAR REACTION', daily_reaction_prompt())
                ids = observer.identity
                context = PrivateVoiceContext(ids.server_epoch, ids.session_id, ids.turn_id, ids.correlation_id)
                receipt = voice.ledger.queued(context, engine_ticket=1)
                binding = PrivateVoiceReceiptBinding(context, receipt.receipt_id, voice.ledger, 1)
                AvatarReplyTurn().consider('[happy] Fixture reply')
                observer.on_text_final('Fixture reply')
                voice.ledger.first_audio(receipt, engine_ticket=1)
                self.assertTrue(voice.signals.publish_pcm_level(binding, .2))
                return PrivateTurnResult(False, 'complete', 'Fixture reply', None, 1)
            runtime = _build_private_web_chat_runtime(enabled=True, voice=voice, turn_lock=asyncio.Lock(),
                dispatch_turn_unlocked=dispatch, launcher=fixture.Launcher(), ownership_verifier=fixture.Ownership(),
                server_epoch=fixture.EPOCH, port=0)
            self.assertIsNotNone(getattr(runtime, 'visual_signals', None))
            await runtime.start()
            try:
                async with ClientSession() as client:
                    ws, sid = await connect(client, runtime.server.bound_port)
                    await ws.send_json(dict(protocol='nana.private-web-chat.v1', server_epoch=fixture.EPOCH,
                        session_id=sid, turn_id=fixture.TURN, correlation_id=fixture.CORRELATION, revision=0,
                        type='chat.submit', payload={'text': 'fixture'}))
                    events = []
                    while True:
                        event = await fixture._receive_json(ws)
                        events.append(event)
                        if event['type'] == 'avatar.signal' and event['payload']['mouth']['open'] > 0:
                            visual = event
                            break
                    self.assertEqual(visual['session_id'], sid)
                    self.assertEqual(visual['payload']['expression']['action'], 'happy')
                    self.assertNotIn('revision', visual)
                    self.assertNotIn('Fixture reply', json.dumps(visual))
                    revisions = [e['revision'] for e in events if e['type'] not in ('avatar.signal', 'runtime.state')]
                    self.assertEqual(revisions, list(range(1, len(revisions) + 1)))
                    self.assertFalse(runtime.coordinator.publish_avatar_signal(
                        PrivateVoiceContext(fixture.EPOCH, sid, '33333333-3333-4333-8333-333333333333', fixture.CORRELATION), 99, visual['payload']))
                    voice.ledger.complete(binding.receipt_id, engine_ticket=1, completed=True)
                    while True:
                        event = await fixture._receive_json(ws)
                        if event['type'] == 'avatar.signal' and event['payload']['voice_state'] == 'delivered':
                            self.assertEqual(event['payload']['mouth']['open'], 0)
                            break
                    self.assertFalse(voice.signals.publish_pcm_level(binding, .8))
                    await ws.close()
            finally:
                await runtime.stop(.1)
            self.assertIsNone(voice.signals)
            self.assertIsNone(voice.ledger)
            self.assertTrue(runtime.visual_task.done())
        asyncio.run(run())

async def serve_ui():
    """Explicit local UI fixture: simulated amplitude, no speaker or provider."""
    voice = Voice()
    tasks = []
    ticket = 0
    async def dispatch(_text, observer):
        nonlocal ticket
        from nana.runtime.avatar_reply_turn import AvatarReplyTurn
        ticket += 1
        ids = observer.identity
        context = PrivateVoiceContext(ids.server_epoch, ids.session_id, ids.turn_id, ids.correlation_id)
        receipt = voice.ledger.queued(context, engine_ticket=ticket)
        binding = PrivateVoiceReceiptBinding(context, receipt.receipt_id, voice.ledger, ticket)
        observer.on_text_delta('Fixture visual ')
        observer.on_text_final('Fixture visual reply')
        AvatarReplyTurn().consider('[happy] Fixture visual reply')
        async def audio():
            await asyncio.sleep(.25)
            voice.ledger.first_audio(receipt, engine_ticket=binding.ticket)
            for frame in range(60):
                voice.signals.publish_pcm_level(binding, .1 + .08 * math.sin(frame))
                await asyncio.sleep(.05)
            voice.ledger.complete(receipt, engine_ticket=binding.ticket, completed=True)
        tasks.append(asyncio.create_task(audio()))
        return PrivateTurnResult(False, 'complete', 'Fixture visual reply', None, binding.ticket)
    runtime = _build_private_web_chat_runtime(enabled=True, voice=voice, turn_lock=asyncio.Lock(),
        dispatch_turn_unlocked=dispatch, launcher=fixture.Launcher(), ownership_verifier=fixture.Ownership(),
        server_epoch=fixture.EPOCH)
    assert await runtime.start()
    print('PRIVATE_AVATAR_UI_FIXTURE_READY 127.0.0.1:8767; fake amplitude/receipts; no audio/provider/data', flush=True)
    try:
        await asyncio.Future()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await runtime.stop(.1)

if __name__ == '__main__':
    if '--serve-ui' in sys.argv:
        try:
            asyncio.run(serve_ui())
        except KeyboardInterrupt:
            pass
    else:
        unittest.main(verbosity=2)
