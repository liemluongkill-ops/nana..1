"""Semantic action contract through private chat; fake provider and fake voice."""
import asyncio
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from nana.runtime.avatar_action_selector import AvatarSelection, decode_selection, select_avatar_action
from nana.runtime.avatar_intent_gateway import AvatarIntentGateway, RecordingTransport

USE_NOD = 's\u1eed d\u1ee5ng event g\u1eadt \u0111\u1ea7u xem n\u00e0o xem l\u1ea7n n\u00e0y n\u00f3 c\u00f3 g\u1eadt \u0111\u1ea7u th\u1eadt kh\u00f4ng'
INDIRECT_NOD = 'th\u1ebf N\u00f3i chung l\u00e0 con c\u00f3 mu\u1ed1n th\u1ea3 event g\u1eadt \u0111\u1ea7u kh\u00f4ng v\u00e0 con c\u00f3 th\u1ec3 \u0111\u01b0\u1ee3c kh\u00f4ng'
PERMISSION = 'kh\u00f3 nh\u1ec9 d\u1ef1ng l\u00ean r\u1ed3i sau \u0111\u1ea5y l\u00e0 ba \u0111\u00e3 \u0111\u01b0a cho con Quy\u1ec1n g\u1eedi event qua Model 3D r\u1ed3i m\u00e0 v\u1eabn kh\u00f4ng \u0111\u01b0\u1ee3c'
ARCHITECTURE_DISCUSSION = (
    'giờ họ toàn bảo  là Đây là một dự án AI có quy mô phức tạp với tư duy thiết kế phần mềm '
    '(Software Architecture) rất tốt. Việc tách bạch giữa "Tâm trí/Nhận thức" (Mind/Identity) '
    'và "Phương tiện ngoại vi" (Game adapters, 3D Avatar, Stream) giúp hệ thống dễ dàng mở rộng, '
    'bảo trì và kiểm thử mà không làm hỏng tính toàn vẹn (integrity) của AI.'
)


class SemanticSelection(unittest.TestCase):
    def setUp(self):
        self.gateway=AvatarIntentGateway(enabled=True,port=0,transport=RecordingTransport())
        self.saved=AvatarIntentGateway._instance
        AvatarIntentGateway._instance=self.gateway

    def tearDown(self):
        self.gateway.stop()
        AvatarIntentGateway._instance=self.saved

    def run_turn(self,text,provider_content):
        from nana.cli import chat_turn_pipeline as pipeline
        spoken=[]
        class FakeVoice:
            def say(self,text,**kwargs):spoken.append(text)
        with patch.object(pipeline.cli_globals,'ai_active',True), \
             patch.object(pipeline,'ask_gpt',side_effect=AssertionError('No free-text reply for action')), \
             patch.object(pipeline,'ask_gpt_stream',side_effect=AssertionError('No free-text reply for action')), \
             patch.object(pipeline,'observe_text_for_persona',side_effect=AssertionError('Action escaped routing')), \
             patch('nana.brain.llmgate_client.call_llmgate_messages',return_value=(provider_content,'ok')) as provider, \
             patch.object(pipeline,'trigger_expression_lifecycle') as extra_reaction, redirect_stdout(io.StringIO()) as output:
            asyncio.run(pipeline.handle_chat_turn(None,FakeVoice(),text,None))
            extra_reaction.assert_not_called()
        return spoken,output.getvalue(),provider

    def test_exact_user_transcripts_get_one_validated_action(self):
        self.assertTrue(self.gateway.start())
        for text in (USE_NOD,INDIRECT_NOD):
            before=self.gateway.snapshot()['stats']['submitted']
            spoken,output,provider=self.run_turn(text,'{"intent":"perform","action":"nod"}')
            provider.assert_called_once()
            self.assertEqual(provider.call_args.args[1][-1]['content'],text)
            self.assertEqual(provider.call_args.kwargs['max_tokens'],80)
            self.assertEqual(provider.call_args.kwargs['timeout_s'],8)
            self.assertIn('source=owner_request_semantic','\n'.join(self.gateway.status_lines()))
            self.assertEqual(self.gateway.snapshot()['stats']['submitted'],before+1)
            self.assertIn('action=nod status=accepted',output)
            self.assertIn('\u0111ang ch\u1edd',spoken[0])

    def test_permission_answer_uses_real_availability(self):
        self.assertTrue(self.gateway.start())
        spoken,output,provider=self.run_turn(PERMISSION,'{"intent":"capability","action":""}')
        provider.assert_called_once()
        self.assertIn('C\u00f3 Ba, con c\u00f3',spoken[0])
        self.assertIn('ready=True',output)
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'],0)

    def test_disabled_gateway_needs_no_provider(self):
        spoken,output,provider=self.run_turn(USE_NOD,'{"intent":"perform","action":"nod"}')
        provider.assert_not_called()
        self.assertIn('ready=False',output)
        self.assertIn('\u0111ang t\u1eaft',spoken[0])
        self.assertFalse(self.gateway.running)

    def test_unavailable_gateway_does_not_consume_avatar_discussion(self):
        from nana.cli.avatar_requests import handle_owner_avatar_turn
        discussions = (
            ARCHITECTURE_DISCUSSION,
            'Avatar 3D đẹp quá, nhánh này phát triển tốt rồi.',
            'Event Javascript là gì?',
            'Ví dụ "sử dụng event gật đầu" là một câu yêu cầu.',
            'Giải thích quyền gửi event qua model 3D là gì.',
            'Hệ thống sử dụng event để tách module avatar khỏi bộ nhớ.',
            'Tôi thấy người ta hỏi con có muốn thả event gật đầu không.',
        )
        for enabled in (False, True):
            self.gateway.enabled = enabled
            for text in discussions:
                with self.subTest(enabled=enabled, text=text):
                    spoken = []
                    with patch('nana.runtime.avatar_action_selector.select_avatar_action') as selector, \
                         redirect_stdout(io.StringIO()) as output:
                        handled = asyncio.run(handle_owner_avatar_turn(text, spoken.append))
                    self.assertFalse(handled)
                    self.assertEqual(spoken, [])
                    self.assertNotIn('[Avatar]', output.getvalue())
                    selector.assert_not_called()
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'], 0)

    def test_unavailable_gateway_still_answers_actual_indirect_requests(self):
        for text in (USE_NOD, INDIRECT_NOD, PERMISSION,
                     'Nana có quyền gửi event qua avatar được không?',
                     'Con có quyền điều khiển model 3D chưa?'):
            with self.subTest(text=text):
                spoken, output, provider = self.run_turn(text, None)
                provider.assert_not_called()
                self.assertIn('ready=False', output)
                self.assertIn('đang tắt', spoken[0])
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'], 0)

    def test_architecture_discussion_reaches_normal_chat_pipeline(self):
        from nana.cli import chat_turn_pipeline as pipeline
        class ReachedConversation(Exception):
            pass
        spoken = []
        class FakeVoice:
            def say(self, text, **kwargs):
                spoken.append(text)
        with patch.object(pipeline.cli_globals, 'ai_active', True), \
             patch.object(pipeline, 'observe_text_for_persona', side_effect=ReachedConversation) as conversation, \
             patch('nana.runtime.avatar_action_selector.select_avatar_action') as selector, \
             redirect_stdout(io.StringIO()):
            with self.assertRaises(ReachedConversation):
                asyncio.run(pipeline.handle_chat_turn(None, FakeVoice(), ARCHITECTURE_DISCUSSION, None))
        conversation.assert_called_once_with(ARCHITECTURE_DISCUSSION)
        selector.assert_not_called()
        self.assertEqual(spoken, [])

    def test_invalid_output_is_never_executed(self):
        self.assertTrue(self.gateway.start())
        bad_values=(
            'I nodded already', 'Here:\n```json\n{"intent":"perform","action":"nod"}\n```',
            '{"intent":"perform","action":"wave"}', '{"intent":"perform","action":"shy_smile"}',
            '{"intent":"perform","action":"heart_happy"}', '{"intent":"perform","action":"camera"}',
            '{"intent":"perform","action":"nod","bones":{}}',
            '{"intent":"perform","action":"nod","action":"wave"}',
            '{"intent":"capability","action":"nod"}', '{"intent":"perform","action":["nod"]}',
        )
        for content in bad_values:
            spoken,output,_=self.run_turn(USE_NOD,content)
            self.assertIn('not_sent reason=selection_unavailable',output)
            self.assertIn('ch\u01b0a g\u1eedi',spoken[0])
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'],0)

    def test_provider_failure_is_visible_and_not_faked(self):
        self.assertTrue(self.gateway.start())
        spoken,output,_=self.run_turn(USE_NOD,None)
        self.assertIn('semantic_unavailable',output)
        self.assertIn('ch\u01b0a g\u1eedi',spoken[0])
        self.assertEqual(self.gateway.snapshot()['stats']['sent'],0)

    def test_no_action_and_clarify_do_not_submit(self):
        self.assertTrue(self.gateway.start())
        for intent in ('no_action','clarify'):
            self.run_turn(INDIRECT_NOD,json.dumps({'intent':intent,'action':''}))
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'],0)

    def test_regular_voice_request_does_not_call_selector(self):
        from nana.cli.avatar_requests import handle_owner_avatar_turn
        with patch('nana.runtime.avatar_action_selector.select_avatar_action') as selector:
            self.assertFalse(asyncio.run(handle_owner_avatar_turn('Con thu noi mot doan dai xem nao',lambda _:None)))
        selector.assert_not_called()

    def test_selection_exception_does_not_break_chat_or_send(self):
        from nana.cli.avatar_requests import handle_owner_avatar_turn
        self.assertTrue(self.gateway.start())
        spoken=[]
        with patch('nana.runtime.avatar_action_selector.select_avatar_action',side_effect=RuntimeError('test')), redirect_stdout(io.StringIO()) as output:
            self.assertTrue(asyncio.run(handle_owner_avatar_turn(USE_NOD,spoken.append)))
        self.assertIn('semantic_unavailable',output.getvalue())
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'],0)

    def test_discussion_continues_without_dispatch(self):
        from nana.cli.avatar_requests import handle_owner_avatar_turn
        self.assertTrue(self.gateway.start())
        with patch('nana.brain.llmgate_client.call_llmgate_messages',return_value=('{"intent":"discuss","action":""}','ok')), redirect_stdout(io.StringIO()):
            self.assertFalse(asyncio.run(handle_owner_avatar_turn('Event Javascript la gi?',lambda _:None)))
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'],0)

    def test_cancelled_selection_cannot_dispatch_late(self):
        from nana.cli.avatar_requests import handle_owner_avatar_turn
        self.assertTrue(self.gateway.start())
        started,release=threading.Event(),threading.Event()
        spoken=[]
        def delayed(_):
            started.set();release.wait(2)
            return AvatarSelection('perform','nod')
        async def exercise():
            with patch('nana.runtime.avatar_action_selector.select_avatar_action',side_effect=delayed):
                task=asyncio.create_task(handle_owner_avatar_turn(USE_NOD,spoken.append))
                await asyncio.to_thread(started.wait,1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
                release.set()
        asyncio.run(exercise())
        self.assertEqual(spoken,[])
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'],0)

    def test_decoding_alone_has_no_side_effects(self):
        self.assertEqual(decode_selection('{"intent":"perform","action":"nod"}').action,'nod')
        self.assertEqual(decode_selection('```json\n{\n  "intent": "perform",\n  "action": "nod"\n}\n```').action,'nod')
        self.assertEqual(decode_selection('```json\n{"intent":"perform","action":"wave"}\n```').reason,'semantic_invalid')
        self.assertFalse(self.gateway.running)
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'],0)


if __name__=='__main__':unittest.main(verbosity=2)
