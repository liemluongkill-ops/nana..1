"""Reply-to-avatar checks with fake voice and isolated recording gateways only."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from nana.runtime.avatar_intent_gateway import AvatarIntentGateway, RecordingTransport, publish_reply_avatar
from nana.runtime.avatar_reaction_policy import OWNER_DISABLED_ACTIONS, REPLY_ACTIONS, TAG_ACTIONS, select_reply_cue


class ReplyEvents(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'NANA_AVATAR_REPLY_EVENTS_ENABLED':'1'})
        self.env.start()
        self.gateway = AvatarIntentGateway(enabled=True, port=0, transport=RecordingTransport())
        self.saved = AvatarIntentGateway._instance
        AvatarIntentGateway._instance = self.gateway

    def tearDown(self):
        self.gateway.stop()
        AvatarIntentGateway._instance = self.saved
        self.env.stop()

    def start(self):
        self.assertTrue(self.gateway.start())

    def test_all_approved_actions_have_cues(self):
        self.assertEqual(len(REPLY_ACTIONS), 14)
        self.assertEqual(set(TAG_ACTIONS.values()), REPLY_ACTIONS)
        self.assertFalse(REPLY_ACTIONS & OWNER_DISABLED_ACTIONS)
        for tag, action in TAG_ACTIONS.items():
            self.assertEqual(select_reply_cue(f'[{tag}] A reply.').action, action)
        self.assertEqual(select_reply_cue('[curiously] A reply.').action, 'curious')

    def test_no_viewer_camera_or_quoted_instruction_dispatch(self):
        for text in ('', '[curious]', '[wave] hello', '[camera] zoom', '"[happy]" means happy',
                     '`[surprised]`', '> [crying] quoted', 'Please use wave', 'A normal answer.'):
            self.assertFalse(select_reply_cue(text).action, text)
        self.assertEqual(select_reply_cue('Nana day Ba.').action, 'listen')
        self.assertEqual(select_reply_cue('Uk dung roi =))').action, 'nod')

    def test_unavailable_does_not_start_or_queue(self):
        self.assertFalse(publish_reply_avatar('[curious] Hello').ok)
        self.assertFalse(self.gateway.running)
        self.start()
        self.assertEqual(self.gateway.commands_since()['commands'], [])
        self.gateway.reply_reactions_enabled = False
        self.assertEqual(publish_reply_avatar('[curious] Hello').reason, 'reply_events_disabled')

    def test_reply_semantic_payload_cooldown_and_dedupe(self):
        self.start()
        result = publish_reply_avatar('[curious] A private sentence.', correlation_id='turn-1')
        self.assertTrue(result.ok)
        self.assertEqual(result.intent.action, 'curious')
        wire = result.intent.to_wire()['intent']
        self.assertEqual(wire['priority'], 20)
        self.assertEqual(wire['interrupt_policy'], 'drop')
        self.assertNotIn('private sentence', json.dumps(wire))
        self.assertEqual(publish_reply_avatar('[curious] Again.', correlation_id='turn-1').reason, 'duplicate_reply')
        self.assertEqual(publish_reply_avatar('[happy] Next.').reason, 'reply_cooldown')

    def test_auto_reply_cannot_replace_owner_pose(self):
        self.start()
        owner = self.gateway.submit_action('serious_think')
        result = publish_reply_avatar('[cute] A reply.')
        self.assertFalse(result.ok)
        self.assertIn(self.gateway.arbiter.receipt(owner.intent.intent_id).status, ('accepted','sent'))

    def test_three_banned_actions_rejected_at_preview_and_http(self):
        self.start()
        for action in OWNER_DISABLED_ACTIONS:
            preview = self.gateway.preview_payload({'action':action})
            self.assertEqual(preview.reason, 'owner_disabled_action')
            request = Request(f'http://127.0.0.1:{self.gateway.bound_port}/v1/avatar/intents',
                              data=json.dumps({'action':action,'source':'owner','channel':'owner'}).encode(),
                              headers={'Content-Type':'application/json'}, method='POST')
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=2)
            self.assertEqual(caught.exception.code, 403)
            self.assertEqual(json.loads(caught.exception.read())['reason'], 'owner_disabled_action')
        self.assertEqual(self.gateway.commands_since()['commands'], [])

    def test_unknown_runtime_and_camera_are_not_dispatched(self):
        self.start()
        for action in ('dance','cheek_fx','camera','zoom','orbit'):
            self.assertFalse(self.gateway.submit_action(action).ok)
        self.assertEqual(self.gateway.commands_since()['commands'], [])

    def test_disabled_avatar_never_breaks_reply_hook(self):
        with patch.object(self.gateway, 'submit_reply', side_effect=RuntimeError('test')):
            self.assertFalse(publish_reply_avatar('[happy] Hello').ok)

    def test_current_chat_expression_hook_works_with_vts_off(self):
        from nana.integrations import vts
        self.start()
        with patch.object(vts,'trigger_expression',new_callable=AsyncMock) as legacy, \
             patch.object(vts,'reset_expression_after_voice',new_callable=AsyncMock):
            asyncio.run(vts.trigger_expression_lifecycle(None,'[thoughtful] A reply.',reason='chat_reply'))
            legacy.assert_awaited_once()
        snap=self.gateway.snapshot()
        self.assertEqual(snap['last_reply_reaction']['action'],'think')
        self.assertEqual(snap['stats']['accepted'],1)

    def test_display_cleanup_does_not_erase_avatar_direction(self):
        from nana.integrations import vts
        from nana.brain.gpt import strip_terminal_audio_tags
        raw='[curious] A reply.'
        display=strip_terminal_audio_tags(raw)
        self.assertNotIn('[curious]', display)
        self.start()
        with patch.object(vts,'trigger_expression',new_callable=AsyncMock) as legacy, \
             patch.object(vts,'reset_expression_after_voice',new_callable=AsyncMock):
            asyncio.run(vts.trigger_expression_lifecycle(None,display,avatar_reply=raw))
            legacy.assert_awaited_once_with(None,display)
        self.assertEqual(self.gateway.snapshot()['last_reply_reaction']['action'],'curious')

    def test_autonomy_hook_only_after_voice_enqueue(self):
        from nana.cli.app import _real_autonomy_tts
        self.start()
        class FakeVoice:
            def say(self,text):
                self.text=text
        voice=FakeVoice()
        _real_autonomy_tts(voice,'[warmly] A reply.')
        self.assertEqual(voice.text,'[warmly] A reply.')
        self.assertEqual(self.gateway.snapshot()['last_reply_reaction']['action'],'wink_soft_smile')


if __name__ == '__main__':
    unittest.main(verbosity=2)
