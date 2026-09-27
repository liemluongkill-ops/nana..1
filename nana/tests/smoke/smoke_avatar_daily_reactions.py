"""Ordinary conversation chooses one reaction at voice commit; no live providers."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stdout
import io
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from nana.runtime.avatar_intent_gateway import AvatarIntentGateway, RecordingTransport
from nana.runtime.avatar_reply_turn import AvatarReplyTurn, daily_reaction_prompt


class DailyReactions(unittest.TestCase):
    def setUp(self):
        self.gateway=AvatarIntentGateway(enabled=True,port=0,transport=RecordingTransport())
        self.gateway.reply_reactions_enabled=True
        self.saved=AvatarIntentGateway._instance
        AvatarIntentGateway._instance=self.gateway

    def tearDown(self):
        self.gateway.stop()
        AvatarIntentGateway._instance=self.saved

    def test_prompt_only_for_enabled_private_reactions(self):
        from nana.brain import gpt
        from nana.runtime.persona_boundary import resolve_persona_boundary
        self.assertEqual(daily_reaction_prompt(),'')
        self.assertTrue(self.gateway.start())
        private=resolve_persona_boundary()
        self.assertIn("NANA'S NATURAL AVATAR REACTION",gpt._identity_prompt_blocks_for_boundary(private)[0])
        public=resolve_persona_boundary(viewer_name='viewer',stream_mode=True)
        self.assertNotIn("NANA'S NATURAL AVATAR REACTION",' '.join(gpt._identity_prompt_blocks_for_boundary(public)))
        self.gateway.reply_reactions_enabled=False
        self.assertEqual(daily_reaction_prompt(),'')

    def test_concurrent_callbacks_only_consider_once(self):
        from nana.runtime import avatar_intent_gateway as module
        self.assertTrue(self.gateway.start())
        turn=AvatarReplyTurn()
        with patch.object(module,'publish_reply_avatar',wraps=module.publish_reply_avatar) as publish, redirect_stdout(io.StringIO()):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(turn.consider,['[happy] Con vui qua.']*12))
        self.assertEqual(publish.call_count,1)
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'],1)

    def test_neutral_offline_and_disabled_do_not_emit(self):
        with redirect_stdout(io.StringIO()):
            AvatarReplyTurn().consider('[happy] A reply.')
        self.assertTrue(self.gateway.start())
        AvatarReplyTurn().consider('120 phut.')
        self.gateway.reply_reactions_enabled=False
        AvatarReplyTurn().consider('[happy] A reply.')
        self.assertEqual(self.gateway.snapshot()['stats']['submitted'],0)

    def test_cooldown_does_not_queue_for_later(self):
        self.assertTrue(self.gateway.start())
        with redirect_stdout(io.StringIO()):
            first=AvatarReplyTurn().consider('[happy] A reply.')
            second=AvatarReplyTurn().consider('[curious] Another reply.')
        self.assertTrue(first.ok)
        self.assertEqual(second.reason,'reply_cooldown')
        self.assertEqual(self.gateway.snapshot()['arbiter']['pending'],[])

    def test_owner_gesture_is_not_overridden(self):
        self.assertTrue(self.gateway.start())
        owner=self.gateway.submit_action('serious_think',priority=80,source='owner_request')
        result=AvatarReplyTurn().consider('[happy] A reply.')
        self.assertFalse(result.ok)
        self.assertIn(self.gateway.arbiter.receipt(owner.intent.intent_id).status,('accepted','sent'))

    def _pipeline(self,mode):
        from nana.cli import chat_turn_pipeline as pipeline
        from nana.integrations import vts
        from nana.runtime import avatar_intent_gateway as gateway_module
        self.assertTrue(self.gateway.start())
        lead='[warmly] Ba da lam rat tot roi, hom nay cu nghi ngoi mot chut nha. '
        tail='[mischievously] Ngay mai minh lai tiep tuc, khong can voi vang dau.'
        queued=[]
        timing={}
        class FakeVoice:
            def say(self,text,**kwargs):queued.append(('normal',text));return 1
            def say_overlap(self,request,**kwargs):queued.append(('overlap',request));return 2
            def say_ttd(self,request,**kwargs):queued.append(('ttd',request));return 3
            def private_overlap_readiness(self):return True,'ready'
            def private_ttd_readiness(self):return True,'ready'
            def record_private_overlap_bypass(self,*args):pass
            def record_private_ttd_bypass(self,*args):pass
            def snapshot(self):return {'queue_size':0,'queue_maxsize':5,'worker_alive':True,'last_error':None}
        async def stream(*args,**kwargs):
            yield lead
            await asyncio.sleep(.08)
            timing['before_eof']=self.gateway.snapshot()['stats']['submitted']
            yield tail
        async def expression(*args,**kwargs):
            with patch.object(vts,'trigger_expression',return_value=None), patch.object(vts,'reset_expression_after_voice',return_value=None):
                await vts.trigger_expression_lifecycle(*args,**kwargs)
        with ExitStack() as stack,redirect_stdout(io.StringIO()) as output:
            for name in ('extract_important','update_emotion','save_chat_log','save_memory_async','awareness_memory_note_user_chat','observe_text_for_persona','mark_chat_time'):
                stack.enter_context(patch.object(pipeline,name,return_value=None))
            stack.enter_context(patch.object(pipeline,'get_memory_spine',return_value=SimpleNamespace(increment_turn=lambda:1)))
            stack.enter_context(patch.object(pipeline,'build_live_awareness_snapshot',return_value={}))
            stack.enter_context(patch.object(pipeline,'memory_lock',threading.RLock()))
            stack.enter_context(patch.object(pipeline,'memory',{'chat_log':[],'short_term':[],'emotion':{'annoyance':0,'playfulness':0}}))
            stack.enter_context(patch.object(pipeline,'is_casual_ping',return_value=False))
            stack.enter_context(patch.object(pipeline.cli_globals,'last_gpt_time',0))
            stack.enter_context(patch.object(pipeline.cli_globals,'ai_active',True))
            stack.enter_context(patch.object(pipeline,'PRIVATE_VOICE_OVERLAP_ENABLED',mode=='overlap'))
            stack.enter_context(patch.object(pipeline,'PRIVATE_VOICE_OVERLAP_COALESCE_MS',1))
            stack.enter_context(patch.object(pipeline,'PRIVATE_VOICE_TTD_ENABLED',mode=='ttd'))
            stack.enter_context(patch.object(pipeline,'ask_gpt_stream',side_effect=stream))
            stack.enter_context(patch.object(pipeline,'trigger_expression_lifecycle',side_effect=expression))
            stack.enter_context(patch('nana.runtime.avatar_action_selector.select_avatar_action',side_effect=AssertionError('No extra model to select daily reactions')))
            publish=stack.enter_context(patch.object(gateway_module,'publish_reply_avatar',wraps=gateway_module.publish_reply_avatar))
            asyncio.run(pipeline.handle_chat_turn(None,FakeVoice(),'Hom nay Ba vua xong viec roi.',None))
        self.assertEqual(publish.call_count,1)
        last=self.gateway.snapshot()['last_submitted_reply_reaction']
        self.assertIsNotNone(last,(output.getvalue(),publish.call_args,self.gateway.snapshot()['last_reply_reaction']))
        self.assertEqual((last['action'],last['source']),('wink_soft_smile','chat_reply'))
        self.assertIn('[Avatar] auto=wink_soft_smile',output.getvalue())
        self.assertTrue(queued)
        if mode in ('overlap','ttd'):
            self.assertEqual(timing['before_eof'],1)
            self.assertEqual(queued[0][0],mode)
        else:
            self.assertEqual(queued,[('normal',lead+tail)])

    def test_full_voice_commit(self):self._pipeline('normal')
    def test_overlap_reacts_before_llm_eof(self):self._pipeline('overlap')
    def test_ttd_reacts_before_llm_eof(self):self._pipeline('ttd')


if __name__=='__main__':unittest.main(verbosity=2)
