"""Real public sanitizers/quality/stage/bridge; only provider/hardware are fake."""
import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest

from smoke_memory_v2_phase1 import _isolated_nana_imports, _module, _scope


def _audit(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = str(args[0]).replace('\\', '/').lower()
        if '/nana/data/' in path or path.endswith('/.env'):
            raise AssertionError('Production data forbidden in full-chain smoke')
    if event in {'socket.connect', 'subprocess.Popen', 'os.system'}:
        raise AssertionError('Live network/process forbidden in full-chain smoke')


sys.addaudithook(_audit)


class FullPublicChain(unittest.TestCase):
    def setUp(self):
        self.enterContext(_isolated_nana_imports())
        for name in ('viewer_chat', 'livestream_identity'):
            sys.modules.pop('nana.runtime.' + name, None)
        empty = types.SimpleNamespace(to_dict=lambda: {})
        for name, attrs in {
            'avatar_event_bridge': {'get_avatar_event_bridge': lambda: types.SimpleNamespace(observe_public_event=lambda **kw: empty)},
            'avatar_live_hook': {'get_avatar_live_hook': lambda: types.SimpleNamespace(evaluate_latest=lambda **kw: None)},
            'stream_event_timeline': {'record_stream_event': lambda *a, **kw: None},
        }.items():
            sys.modules['nana.runtime.' + name] = _module('nana.runtime.' + name, **attrs)
        self.bridge = importlib.import_module('nana.runtime.external_bridge')
        self.social = importlib.import_module('nana.runtime.social_session')
        self.viewer = importlib.import_module('nana.runtime.viewer_chat')
        self.temp = self.enterContext(tempfile.TemporaryDirectory(prefix='nana-full-public-'))

    def reply(self, raw, text='Mô tả cảnh này bằng một câu.', display='Alex'):
        directory = Path(self.temp)
        runtime = self.bridge.ExternalBridgeRuntime(
            request_dir=directory/'inbox', reply_dir=directory/'outbox', enabled=True,
            queue=self.viewer.ViewerChatQueue(rate_limit_max=100),
            social_session=self.social.SocialSessionCache())
        request = self.bridge.ExternalBridgeRequest.from_payload({
            'request_id': 'full-chain', 'source': 'youtube', 'text': text,
            'author_id': 'actor-1', 'author_name': display, 'channel_id': 'room-1',
            'metadata': {'event_id': 'full-chain', 'stream_session_id': 'session-1'}})
        result = runtime.process_request(request, responder=lambda _: raw)
        runtime._write_reply(request.request_id, result)
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['speak'])
        self.assertEqual(runtime._output_records[request.request_id].state, 'published')
        return result

    def test_classifiers_and_numerals_survive_all_real_guards(self):
        for raw in (
            'Một con mèo cuộn tròn ngủ say trên chiếc ghế êm.',
            'Một con pixel đi dép lê qua ba cánh cửa.',
            'Hai con robot chia bánh thành ba phần bằng nhau.',
            'Ba con thỏ ngồi trên con thuyền vào thứ ba.',
            'Con mèo và con chó ở cạnh con sông.',
            'Cả ba ngủ quên cạnh một con pixel.',
            'Ba con robot chia bánh công bằng, không con nào giận dỗi.',
        ):
            with self.subTest(raw=raw):
                result = self.reply(raw)
                self.assertEqual(result['reply_text'], raw)
                self.assertNotIn('fallback', result['metadata']['public_quality']['actions'])

    def test_private_address_still_rewritten_without_shielding_whole_sentence(self):
        reply = self.reply('Con thấy một con pixel chia bánh thành ba phần, Ba ơi.')
        self.assertEqual(reply['reply_text'], 'Mình thấy một con pixel chia bánh thành ba phần, bạn ơi.')
        blocked = self.reply('Con thấy một con mèo trong codebase riêng của Ba.')
        self.assertNotIn('codebase', blocked['reply_text'].lower())

    def test_robot_story_is_not_an_identity_challenge(self):
        raw = 'Hai con robot chia chiếc bánh thành ba phần đều nhau.'
        reply = self.reply(raw, text='Viết một câu về hai con robot chia bánh thành ba phần.')
        self.assertEqual(reply['reply_text'], raw)

    def test_factual_names_are_not_throttled(self):
        stage = importlib.import_module('nana.runtime.public_stage_identity').PublicStageIdentityGuard()
        stage._record_name_used(True)
        stage._record_name_used(True)
        raw = 'Chiếc xe tên XE-2846 thuộc về Lan mới.'
        rewritten = stage.rewrite_public_stage_reply(raw, viewer_name='Lan mới')
        self.assertEqual(rewritten.text, raw)
        self.assertNotIn('name_throttle', rewritten.actions)

    def test_actor_history_uses_current_display_label(self):
        cache = self.social.SocialSessionCache(clock=lambda: 1000.)
        common = dict(platform='youtube', room_id='a', stream_session_id='s')
        first = _scope(dict(common, author_id='1', event_id='e1', display_name='Lan'))
        other = _scope(dict(common, author_id='2', event_id='e2', display_name='Lan'))
        renamed = _scope(dict(common, author_id='1', event_id='e3', display_name='Lan mới'))
        old_turn = cache.record_public_turn(scope=first, text='My bike is XE-2846')
        cache.record_public_turn(scope=other, text='What is my bike called?')
        cache.record_public_turn(scope=renamed, text='Only my display label changed')
        context = cache.format_public_room_context(renamed)
        owned = next(line for line in context.splitlines() if 'XE-2846' in line)
        self.assertIn('speaker_ref=current_viewer', owned)
        self.assertIn('display_name="Lan mới"', owned)
        self.assertEqual(old_turn.viewer_name, 'Lan')


if __name__ == '__main__':
    unittest.main(verbosity=2)
