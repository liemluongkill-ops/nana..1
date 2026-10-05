"""Private visual ownership, stale PCM and reaction smoke; no providers/data."""
import dataclasses
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
package = types.ModuleType('nana')
package.__path__ = [str(ROOT / 'nana')]
sys.modules['nana'] = package
config = types.ModuleType('nana.config')
config.ELEVEN_PUBLIC_TTS_MODEL = 'eleven_v3'
sys.modules['nana.config'] = config

def guard(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = str(args[0]).replace('\\', '/').lower()
        if '/nana/data/' in path or path.endswith('/.env') or '/runtime_logs/' in path:
            raise AssertionError('production data access forbidden')
    if event == 'socket.connect':
        raise AssertionError('network forbidden')
sys.addaudithook(guard)

from nana.runtime.private_voice_receipts import PrivateVoiceContext, PrivateVoiceReceiptBinding, PrivateVoiceReceiptLedger

EPOCH = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
SID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
TURN = '11111111-1111-4111-8111-111111111111'
CORR = '22222222-2222-4222-8222-222222222222'

class SignalsSmoke(unittest.TestCase):
    def setUp(self):
        from nana.runtime.private_avatar_signals import PrivateAvatarSignals
        self.now = 10.0
        self.ledger = PrivateVoiceReceiptLedger(clock=lambda: self.now, event_sink=lambda receipt: self.signals.on_receipt(receipt))
        self.signals = PrivateAvatarSignals(self.ledger, clock=lambda: self.now)
        self.context = PrivateVoiceContext(EPOCH, SID, TURN, CORR)
        self.assertTrue(self.signals.begin_turn(self.context))
        self.receipt = self.ledger.queued(self.context, engine_ticket=1)
        self.binding = PrivateVoiceReceiptBinding(self.context, self.receipt.receipt_id, self.ledger, 1)

    def payload(self):
        frames = self.signals.collect()
        self.assertEqual(len(frames), 1)
        return frames[0][2]

    def speaking(self):
        self.ledger.first_audio(self.receipt, engine_ticket=1)

    def test_pcm_requires_actual_first_audio_and_expires_without_refresh(self):
        self.assertFalse(self.signals.publish_pcm_level(self.binding, .2))
        self.assertEqual(self.payload()['mouth']['open'], 0)
        self.speaking()
        self.assertTrue(self.signals.publish_pcm_level(self.binding, .2))
        payload = self.payload()
        self.assertGreater(payload['mouth']['open'], .7)
        self.assertLess(payload['mouth']['open'], .9)
        self.assertEqual(payload['mouth']['energy'], 0)
        self.now += .181
        self.assertEqual(self.payload()['mouth'], dict(open=0., energy=0., viseme='sil', speaking=False))

    def test_foreign_binding_nan_and_terminal_callbacks_cannot_animate(self):
        self.speaking()
        foreign = dataclasses.replace(self.binding, ticket=2)
        self.assertFalse(self.signals.publish_pcm_level(foreign, .8))
        self.assertFalse(self.signals.publish_pcm_level(dataclasses.replace(self.binding, receipt_ledger=PrivateVoiceReceiptLedger()), .8))
        self.signals.publish_pcm_level(self.binding, float('nan'))
        self.assertEqual(self.payload()['mouth']['open'], 0)
        self.ledger.complete(self.receipt, engine_ticket=1, completed=True)
        self.assertFalse(self.signals.publish_pcm_level(self.binding, .8))
        terminal = self.payload()
        self.assertEqual(terminal['voice_state'], 'delivered')
        self.assertIsNone(terminal['expression'])
        self.assertEqual(terminal['expires_in_ms'], 0)
        self.assertEqual(self.signals.collect(), [])

    def test_reaction_waits_for_voice_and_is_once_per_turn_without_text(self):
        self.assertTrue(self.signals.publish_reply(self.context, '[happy] private sentinel reply'))
        self.assertIsNone(self.payload()['expression'])
        self.speaking()
        first = self.payload()['expression']
        self.assertEqual(first['action'], 'happy')
        self.assertFalse(self.signals.publish_reply(self.context, '[curious] changed reply'))
        self.assertEqual(self.payload()['expression']['event_id'], first['event_id'])
        self.assertNotIn('sentinel', repr(self.signals.collect()))
        self.now += 2.1
        self.assertIsNone(self.payload()['expression'])

    def test_forged_receipt_cannot_enable_playback_or_rollback_terminal(self):
        fake = dataclasses.replace(self.receipt, state='speaking', revision=2)
        self.signals.on_receipt(fake)
        self.assertEqual(self.payload()['voice_state'], 'queued')
        self.ledger.unknown(self.receipt, engine_ticket=1)
        self.signals.on_receipt(self.receipt)
        self.assertEqual(self.payload()['voice_state'], 'unknown')

    def test_unregistered_context_and_closed_publisher_are_inert(self):
        other = dataclasses.replace(self.context, turn_id='33333333-3333-4333-8333-333333333333')
        self.assertFalse(self.signals.publish_reply(other, '[happy] reply'))
        self.signals.close()
        self.assertFalse(self.signals.begin_turn(other))
        self.assertFalse(self.signals.publish_pcm_level(self.binding, .2))
        self.assertEqual(self.signals.collect(), [])

    def test_delivery_queue_age_cannot_revive_stale_mouth_or_expression(self):
        from nana.runtime.private_avatar_signals import age_visual_event
        self.signals.publish_reply(self.context, '[happy] reply')
        self.speaking()
        self.signals.publish_pcm_level(self.binding, .2)
        original = {'type': 'avatar.signal', 'payload': self.payload()}
        aged = age_visual_event(original, 2100)
        self.assertEqual(aged['payload']['mouth']['open'], 0)
        self.assertIsNone(aged['payload']['expression'])
        self.assertGreater(original['payload']['mouth']['open'], 0)

if __name__ == '__main__':
    unittest.main(verbosity=2)
