"""Private request routing and observable receipts; fake voice, no LLM/provider."""
import asyncio
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from nana.cli.avatar_requests import parse_owner_avatar_request, handle_owner_avatar_request
from nana.runtime.avatar_intent_gateway import AvatarIntentGateway, RecordingTransport, publish_reply_avatar

ASR_GENERIC_REQUEST = 'M\u1eb9 \u01a1i Con th\u1eed g\u1eedi event l\u1ea1i xem \u0111\u1ec3 ba Xem xem n\u00f3 c\u00f3 ho\u1ea1t \u0111\u1ed9ng th\u1eadt kh\u00f4ng n\u00e0o'
ASR_NOD_REQUEST = 'Th\u1ebf th\u00ec con h\u00e3y g\u1eedi m\u1ed9t event c\u1ee5 th\u1ec3 \u0111i \u0111\u00f3 ch\u00ednh l\u00e0 event g\u1eadt \u0111\u1ea7u xem n\u00f3 c\u00f3 ho\u1ea1t \u0111\u1ed9ng kh\u00f4ng hay l\u00e0 con kh\u00f4ng \u0111\u1ecbnh g\u1eedi'


class OwnerRequests(unittest.TestCase):
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

    def test_owner_transcripts_and_named_actions(self):
        cases = {
            'Con thu Doi sang cai bieu cam nhay mot ben mat xem nao':'wink_soft_smile',
            'Con thu bieu cam gat dau xem cai day la cai de nhat de ta co the nhin thay ay':'nod',
            'Nana oi con hay nhay mat trai di':'wink_soft_smile',
            'Nana oi con hay nhay mat phai di':'playful_wink',
            'Gat dau di':'nod',
            'Con thu gat dau duoc khong?':'nod',
            'Con thu bieu cam nghiem tuc':'serious_think',
            'Con thu bieu cam ngac nhien biu':'surprised_pout',
            'Con thu bieu cam ngai khoc':'shy_crying',
            'Con ve trang thai binh thuong':'settle',
        }
        for text, action in cases.items():
            self.assertEqual(parse_owner_avatar_request(text).action, action, text)

    def test_unicode_transcript(self):
        self.assertEqual(parse_owner_avatar_request('Con th\u1eed bi\u1ec3u c\u1ea3m g\u1eadt \u0111\u1ea7u xem n\u00e0o').action,'nod')

    def test_normal_quoted_negative_hypothetical_text_is_not_command(self):
        for text in ('Neu con thu gat dau thi sao',
                     'Con thu gia vo noi la da nhay mat', 'Con thu giai thich gat dau co nghia gi',
                     'Toi thay nguoi ta gat dau', 'Con thu dich "gat dau"',
                     'Con thu suy nghi xem cach sua loi', 'Con thu suy nghi xem co cach sua khong', 'Con hay lang nghe cau chuyen nay',
                     'Con thu cuoi vi du la gi', 'Nana oi con co nen nhay mat khong?'):
            self.assertIsNone(parse_owner_avatar_request(text), text)

    def test_transcribed_filler_and_question_tail(self):
        self.assertEqual(parse_owner_avatar_request(ASR_GENERIC_REQUEST).reason, 'name_one_action')
        self.assertEqual(parse_owner_avatar_request(ASR_NOD_REQUEST).action, 'nod')
        for lead in ('The thi ', 'Me oi ', 'Nao bay gio ', 'U dung roi, ', ''):
            for tail in (' xem no co hoat dong khong', ' de ba xem no co chay khong', ' xem cai nay co dung khong', ''):
                self.assertEqual(parse_owner_avatar_request(lead+'con hay gui event gat dau'+tail).action, 'nod')
        self.assertEqual(parse_owner_avatar_request('Me oi con gui dung event gat dau').action, 'nod')

    def test_negation_belongs_to_command_clause(self):
        for text in ('Con dung nhay mat', 'Con khong gat dau', 'Me oi con dung gui event gat dau de ba xem',
                     'The thi con chua gui event gat dau', 'Con hay khoan gui event nhay mat'):
            self.assertEqual(parse_owner_avatar_request(text).reason,'negated_owner_request')
            with redirect_stdout(io.StringIO()):
                self.assertTrue(handle_owner_avatar_request(text,lambda _:None))
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'],0)

    def test_exact_owner_logs_cannot_fall_through_to_llm(self):
        from nana.cli import chat_turn_pipeline as pipeline
        self.assertTrue(self.gateway.start())
        spoken=[]
        class FakeVoice:
            def say(self,text,**kwargs):
                spoken.append(text)
        with patch.object(pipeline.cli_globals,'ai_active',True), \
             patch.object(pipeline,'ask_gpt',side_effect=AssertionError('No LLM for avatar request')), \
             patch.object(pipeline,'ask_gpt_stream',side_effect=AssertionError('No LLM for avatar request')), \
             patch.object(pipeline,'observe_text_for_persona',side_effect=AssertionError('Request escaped routing')), \
             patch.object(pipeline,'trigger_expression_lifecycle') as reaction, redirect_stdout(io.StringIO()) as output:
            asyncio.run(pipeline.handle_chat_turn(None,FakeVoice(),ASR_GENERIC_REQUEST,None))
            self.assertEqual(self.gateway.snapshot()['stats']['submitted'],0)
            self.assertIn('not_sent reason=name_one_action',output.getvalue())
            asyncio.run(pipeline.handle_chat_turn(None,FakeVoice(),ASR_NOD_REQUEST,None))
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'],1)
        self.assertEqual(self.gateway.snapshot()['arbiter']['recent_receipts'][0]['action'],'nod')
        reaction.assert_not_called()
        self.assertIn('ch\u01b0a g\u1eedi',spoken[0])
        self.assertIn('\u0111ang ch\u1edd',spoken[1])
        self.assertIn('action=nod status=accepted',output.getvalue())

    def test_ambiguous_does_not_pick_a_random_action(self):
        for text in ('Ok roi day Na Na oi Con thu gui event xem nao hay la gi day xem',
                     'Con thu gat dau roi nhay mat', 'Con thu bieu cam abc'):
            self.assertEqual(parse_owner_avatar_request(text).reason,'name_one_action')
            spoken=[]
            with redirect_stdout(io.StringIO()):
                self.assertTrue(handle_owner_avatar_request(text,spoken.append))
            self.assertEqual(self.gateway.snapshot()['stats']['submitted'],0)

    def test_owner_blocks_and_camera_do_not_dispatch(self):
        for text in ('Con thu vay tay', 'Con thu giau tay cuoi', 'Con thu vui mat tim', 'Con thu doi goc camera'):
            with redirect_stdout(io.StringIO()):
                self.assertTrue(handle_owner_avatar_request(text,lambda _:None))
            self.assertEqual(self.gateway.snapshot()['stats']['submitted'],0)

    def test_private_pipeline_routes_before_llm_and_no_self_reaction(self):
        from nana.cli import chat_turn_pipeline as pipeline
        self.assertTrue(self.gateway.start())
        spoken=[]
        class FakeVoice:
            def say(self,text,**kwargs):
                spoken.append(text)
        with patch.object(pipeline.cli_globals,'ai_active',True), \
             patch.object(pipeline,'ask_gpt',side_effect=AssertionError('LLM must not run')), \
             patch.object(pipeline,'ask_gpt_stream',side_effect=AssertionError('LLM must not run')), \
             patch.object(pipeline,'observe_text_for_persona',side_effect=AssertionError('not a chat turn')), \
             patch.object(pipeline,'trigger_expression_lifecycle') as reaction, redirect_stdout(io.StringIO()) as output:
            asyncio.run(pipeline.handle_chat_turn(None,FakeVoice(),'Con thu bieu cam gat dau xem nao',None))
        self.assertIn('action=nod',output.getvalue())
        self.assertIn('id=',output.getvalue())
        self.assertIn('status=accepted',output.getvalue())
        reaction.assert_not_called()
        self.assertEqual(len(spoken),1)
        self.assertIn('\u0111ang ch\u1edd',spoken[0])
        self.assertNotIn('\u0111\u00e3 g\u1eadt',spoken[0])
        active=self.gateway.arbiter.snapshot()['active'][0]['intent']
        self.assertEqual((active['action'],active['source'],active['channel']),('nod','owner_request','owner'))
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'],1)

    def test_unavailable_never_claims_success(self):
        spoken=[]
        with redirect_stdout(io.StringIO()) as output:
            self.assertTrue(handle_owner_avatar_request('Con thu gat dau',spoken.append))
        self.assertFalse(self.gateway.running)
        self.assertIn('gateway_not_running',output.getvalue())
        self.assertNotIn('Con \u0111\u00e3 g\u1eedi',spoken[0])

    def test_reply_attempt_does_not_erase_last_submitted_event(self):
        self.assertTrue(self.gateway.start())
        sent=publish_reply_avatar('[curious] Hello.',correlation_id='r1')
        publish_reply_avatar('Day nha Ba.')
        snap=self.gateway.snapshot()
        self.assertEqual(snap['last_reply_reaction']['reason'],'no_reply_cue')
        self.assertEqual(snap['last_submitted_reply_reaction']['intent_id'],sent.intent.intent_id)
        publish_reply_avatar('[happy] Hello.')
        self.assertEqual(self.gateway.snapshot()['last_reply_reaction']['reason'],'reply_cooldown')
        self.gateway.reply_reactions_enabled=False
        publish_reply_avatar('[happy] Hello.')
        self.assertEqual(self.gateway.snapshot()['last_reply_reaction']['reason'],'reply_events_disabled')

    def test_status_shows_actual_receipts_not_only_counters(self):
        self.assertTrue(self.gateway.start())
        intent=self.gateway.submit_action('nod',source='owner_request').intent
        self.gateway.acknowledge_payload({'intent_id':intent.intent_id,'status':'started','reason':'runtime_started'})
        self.gateway.acknowledge_payload({'intent_id':intent.intent_id,'status':'finished','reason':'runtime_finished'})
        receipts=self.gateway.snapshot()['arbiter']['recent_receipts']
        self.assertEqual(len(receipts),1)
        self.assertEqual(receipts[0]['status'],'finished')
        status='\n'.join(self.gateway.status_lines())
        self.assertIn('nod | source=owner_request | finished | runtime_finished',status)
        self.assertIn(intent.intent_id,status)

    def test_status_distinguishes_expiry_from_completion(self):
        self.assertTrue(self.gateway.start())
        intent=self.gateway.submit_action('nod',intent_id='no-renderer').intent
        self.gateway.arbiter.mark_sent(intent.intent_id,ok=True,reason='recording')
        self.gateway.arbiter.tick(now=10**20)
        status='\n'.join(self.gateway.status_lines())
        self.assertIn('finished | duration_elapsed_without_runtime_ack',status)
        self.assertIn('not completion proof',status)


if __name__ == '__main__':
    unittest.main(verbosity=2)
