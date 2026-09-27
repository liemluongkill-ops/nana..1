"""Offline stage-name/pronoun checks. No providers, playback or real dispatch."""
from __future__ import annotations

import asyncio
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from nana.runtime.livestream_identity import (
    ALIASES, CHANNEL_NAME, STAGE_NAME, finalize_livestream_identity,
    is_livestream_source, is_stage_call, mentions_stage_name, stage_identity_answer,
)
from nana.runtime.persona_boundary import resolve_persona_boundary, sanitize_public_reply
from nana.runtime.identity import format_identity_block, load_identity, resolve_user


class LivestreamIdentityTests(unittest.TestCase):
    def test_destination_gate(self):
        for source in ('youtube', 'youtube_live_chat', 'twitch', 'livestream', ' YouTube '):
            self.assertTrue(is_livestream_source(source))
        for source in (None, '', 'discord', 'social', 'private_owner', 'stream_host', 'youtube.com', 'public'):
            self.assertFalse(is_livestream_source(source))
            self.assertEqual(finalize_livestream_identity('Nana nghe rồi.', source=source), 'Nana nghe rồi.')

    def test_aliases(self):
        for name in ALIASES:
            for text in (name, f'{name.upper()} ơi!', f'@{name} ơi'):
                self.assertTrue(is_stage_call(text), text)
                self.assertIn('Mình', stage_identity_answer(text))
            self.assertTrue(mentions_stage_name(f'{name} thấy sao?'))
        self.assertTrue(is_stage_call('Nayumi   Liora ơi'))
        for text in ('banana', 'Liorax', 'Yumiko', 'Nayumiko', 'yumi_tool', 'liorafile'):
            self.assertFalse(mentions_stage_name(text), text)
        self.assertFalse(is_stage_call('Tôi đang nói về Yumi trong phim'))

    def test_identity_and_pronoun(self):
        for query in ('bạn tên gì?', 'Nayumi là ai?', 'Liora tên gì?'):
            self.assertIn(STAGE_NAME, stage_identity_answer(query))
        samples = {
            'Nana nghe rồi, Nana sẽ kể tiếp.': 'Mình nghe rồi, mình sẽ kể tiếp.',
            'Mình là Nana.': 'Mình là Nayumi Liora.',
            'Mình tên Nana.': 'Mình tên Nayumi Liora.',
            'Tôi là Nana.': 'Mình là Nayumi Liora.',
            'Em tên là Nana.': 'Mình tên là Nayumi Liora.',
            'Nayumi thích bài này.': 'Mình thích bài này.',
            'Yumi đang nghe nè.': 'Mình đang nghe nè.',
            'Mình là Nayumi Liora, cứ gọi mình là Yumi nha.': 'Mình là Nayumi Liora, cứ gọi mình là Yumi nha.',
        }
        for raw, expected in samples.items():
            result = finalize_livestream_identity(raw, source='youtube')
            self.assertEqual(result, expected)
            self.assertEqual(finalize_livestream_identity(result, source='youtube'), result)

    def test_external_names_preserved(self):
        for text in ('Bài hát Nana hay đó.', 'Bạn Nana vừa chào.', 'Tên phim là "Nana".',
                     'Xem https://example.com/Nana và @Nana nhé.', 'Mã `Nana` không đổi.', 'Banana là chuối.'):
            self.assertEqual(finalize_livestream_identity(text, source='youtube'), text)
        self.assertEqual(finalize_livestream_identity('Chào Nana nha. Nana nghe rồi.', source='youtube', viewer_name='Nana'),
                         'Chào Nana nha. Mình nghe rồi.')

    def test_private_and_discord_unchanged(self):
        self.assertEqual(load_identity()['self_name'], 'Nana')
        self.assertEqual(load_identity()['self_pronoun'], 'con')
        private = resolve_persona_boundary()
        discord = resolve_persona_boundary(viewer_name='linh', stream_mode=True, platform='discord')
        unknown_public = resolve_persona_boundary(viewer_name='linh', stream_mode=True)
        for boundary in (private, discord, unknown_public):
            self.assertFalse(boundary.livestream)
            self.assertNotIn(STAGE_NAME, boundary.prompt_block)
        self.assertEqual(sanitize_public_reply('Con chào Ba nha.'), 'Nana chào bạn nha.')
        live = resolve_persona_boundary(viewer_name='linh', stream_mode=True, platform='youtube')
        self.assertTrue(live.livestream)
        block = format_identity_block(resolve_user(viewer_name='linh'), persona_boundary=live)
        self.assertIn(CHANNEL_NAME, block)
        self.assertNotIn('con gái AI của Ba', block)

    def test_social_alias_scope(self):
        from nana.runtime.social_session import SocialSessionCache
        for source in ('youtube', 'discord'):
            decision = SocialSessionCache().observe(platform=source, viewer_name='linh', text='Liora ơi')
            if source == 'youtube':
                self.assertEqual(decision.event_type, 'greeting')
                self.assertIn(STAGE_NAME, decision.style_hint)
                self.assertIn('addressing you', decision.style_hint)
            else:
                self.assertNotIn(STAGE_NAME, decision.style_hint)
                self.assertNotEqual(decision.event_type, 'greeting')

    def test_gpt_local_and_provider_routes(self):
        import nana.brain.gpt as gpt
        from nana.brain import llmgate_client
        captured = []
        def fake_completion(**kwargs):
            captured.append(kwargs['messages'])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='Nana thích màu xanh.'))])
        def fake_stream(**kwargs):
            captured.append(kwargs['messages'])
            yield 'Nana thích '
            yield 'màu xanh.'
        with patch.object(gpt, 'create_chat_completion_with_fallback', fake_completion), \
             patch.object(llmgate_client, 'stream_llmgate_messages', fake_stream), \
             patch.object(gpt, 'context_snapshot', return_value={'active_zone':'unknown','active_app':'','idle_state':'active','zone_history':[], 'last_window_title':''}), \
             patch.object(gpt, 'build_live_awareness_snapshot', return_value={}), \
             patch.object(gpt, '_GROUNDING_AVAILABLE', False), \
             patch.object(gpt, '_core_self_repair_reply', side_effect=lambda reply, **kw: reply):
            for alias in ALIASES:
                self.assertEqual(gpt.ask_gpt(alias+' ơi', viewer_name='linh', stream_mode=True, public_platform='youtube'),
                                 'Mình đây, mình đang nghe nè.')
            self.assertEqual(len(captured), 0)
            reply = gpt.ask_gpt('Hôm nay chọn màu nào?', viewer_name='linh', stream_mode=True, public_platform='youtube')
            self.assertNotIn('Nana', reply)
            self.assertIn('Mình', reply)
            self.assertIn(CHANNEL_NAME, captured[-1][0]['content'])
            async def collect(source):
                return ''.join([part async for part in gpt.ask_gpt_stream('Hôm nay chọn màu nào?', viewer_name='linh', stream_mode=True, public_platform=source)])
            self.assertEqual(self.loop.run_until_complete(collect('youtube')), 'Mình thích màu xanh.')
            self.assertEqual(self.loop.run_until_complete(collect('discord')), 'Nana thích màu xanh.')
            self.assertNotIn(CHANNEL_NAME, captured[-1][0]['content'])

    def test_bridge_final_guard_and_no_dispatch(self):
        import nana.runtime.external_bridge as bridge
        from nana.runtime.viewer_chat import ViewerChatQueue
        from nana.runtime.social_session import SocialSessionCache
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(bridge, 'record_stream_event'), \
             patch.object(bridge, 'get_avatar_live_hook') as hook, \
             patch.object(bridge, '_safe_public_full_reply_polish', side_effect=lambda text, **kw: text):
            for source in ('youtube', 'discord'):
                runtime = bridge.ExternalBridgeRuntime(request_dir=Path(directory)/'requests', reply_dir=Path(directory)/'replies',
                    queue=ViewerChatQueue(), social_session=SocialSessionCache(), enabled=True)
                request = bridge.ExternalBridgeRequest.from_payload({'request_id':source,'source':source,'text':'Chào bạn',
                    'author_name':'linh','channel_id':'room','local_playback':False})
                response = runtime.process_request(request, responder=lambda req:'Nana thích bài nhạc này, nghe vui ghê.')
                line = response['reply_text']
                self.assertFalse(response['speak'])
                self.assertFalse(response['local_playback'])
                if source=='youtube':
                    self.assertNotIn('Nana',line)
                    self.assertIn('mình',line.lower())
                else:
                    self.assertIn('Nana',line)

    def test_banter_prompt_explicit_gate(self):
        from nana.autonomy.llm_banter import _build_prompt
        normal,_ = _build_prompt('stream_host', {}, {})
        live,_ = _build_prompt('stream_host', {'stream_stage_policy_gate':True}, {})
        self.assertNotIn(STAGE_NAME, normal)
        self.assertTrue(live.startswith('LIVESTREAM NAME'))
        self.assertIn(STAGE_NAME,live)

    def test_autonomy_output_scope(self):
        from nana.autonomy.loop import AutonomyLoop
        from nana.autonomy.inner_thought import Thought
        from nana.runtime.stream_state import StreamStateCore
        import nana.runtime.stream_state as stream_state
        state = StreamStateCore()
        state.go_live()
        loop = AutonomyLoop(clock=lambda:1000.)
        loop._cadence.can_express = lambda mode:(True,'ok')
        loop._cadence.record_expression = lambda *args, **kw:None
        loop._gate.evaluate_debug = lambda ctx:{}
        loop._gate.evaluate = lambda ctx:SimpleNamespace(allowed=True,reason='ok',level='full',intensity=.8,payload={'tts':False,'vts':False,'subtitle':False})
        thought = Thought(mode='stream_host',text='Nana đang nghe.',cooldown_s=0,min_silence_s=0,tags=[],raw='Nana đang nghe.',line_index=0,source='smoke')
        loop._thought.pick = lambda *a,**kw:thought
        loop._thought.pick_prefer_ultra_short = loop._thought.pick
        loop._express.emit = lambda mode,level,payload,selected:{'text':selected.text,'mock':True}
        context={'forced_mode':'stream_host','user_is_typing':False,'game_active':False,'command_in_flight':False,
                 'audio_busy':False,'mood_affection':.7,'scene_relevance':.9,'silence_duration_s':999.,
                 'silence_window_s':60.,'jitter_value':.9,'web_context':{},'attention_window':'browse_active'}
        with patch.object(stream_state,'get_stream_state',return_value=state):
            loop._observer.get_context=lambda:dict(context)
            self.assertEqual(loop.tick()['thought'].text,'Nana đang nghe.')
            loop._observer.get_context=lambda:{**context,'stream_stage_policy_gate':True}
            decision=loop.tick()
            self.assertTrue(decision['accepted'],decision)
            self.assertEqual(decision['thought'].text,'Mình đang nghe.')
            self.assertEqual(thought.text,'Nana đang nghe.')


if __name__ == '__main__':
    # Fail if an unexpected provider/transport tries to connect during smokes.
    # Windows asyncio creates a private socketpair; initialize it before blocking network.
    LivestreamIdentityTests.loop = asyncio.new_event_loop()
    try:
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('Network disabled in smoke')):
            unittest.main(verbosity=2)
    finally:
        LivestreamIdentityTests.loop.close()
