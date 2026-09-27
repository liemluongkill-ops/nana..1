"""Real private pipeline + stream builders, with fake model/audio owners."""
import asyncio
from contextlib import contextmanager, redirect_stdout
import importlib
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from smoke_memory_v2_phase1 import _isolated_nana_imports, _temporary_memory_module, _module, _namespace, NANA_ROOT
from smoke_memory_v2_public_prompt import _install_isolated_dependencies

def guard(event, args):
    if event == 'socket.connect':
        frame = sys._getframe(1)
        if frame.f_code.co_name in {'socketpair', '_fallback_socketpair'} and Path(frame.f_code.co_filename).name == 'socket.py':
            return
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = str(args[0]).replace('\\', '/').lower()
        if '/nana/data/' in path or path.endswith('/.env'):
            raise AssertionError('production data forbidden')
    if event in ('socket.connect', 'subprocess.Popen', 'os.system'):
        raise AssertionError('external runtime forbidden')
sys.addaudithook(guard)

async def noop(*a, **kw):
    return None
async def no_avatar(*a, **kw):
    return False

@contextmanager
def pipeline_fixture():
    with _isolated_nana_imports(), patch.dict(sys.modules):
        directory = sys.modules['nana.config'].DATA_DIR
        _gpt, _ = _install_isolated_dependencies()
        config = sys.modules['nana.config']
        for name, value in {
            'GPT_COOLDOWN': 0, 'PRIVATE_VOICE_OVERLAP_COALESCE_MS': 1,
            'PRIVATE_VOICE_OVERLAP_ENABLED': False, 'PRIVATE_VOICE_OVERLAP_MAX_CHARS': 180,
            'PRIVATE_VOICE_OVERLAP_MIN_CHARS': 30, 'PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS': 200,
            'PRIVATE_VOICE_TTD_CHUNK_TARGET_CHARS': 100, 'PRIVATE_VOICE_TTD_ENABLED': False,
            'PRIVATE_VOICE_TTD_MIN_CHARS': 30, 'PRIVATE_VOICE_TTD_MIN_WORDS': 3,
        }.items():
            setattr(config, name, value)
        memory = _temporary_memory_module(directory)
        sys.modules['nana.memory'] = memory
        # Keep the real tag parser and real surface finalizer. External owners
        # are the only replaced layers below the pipeline under test.
        sys.modules.pop('nana.voice.inline_audio_tags', None)
        tags = importlib.import_module('nana.voice.inline_audio_tags')
        sys.modules.pop('nana.brain.gpt', None)
        gpt = importlib.import_module('nana.brain.gpt')
        sys.modules['nana.core'] = _namespace('nana.core', NANA_ROOT / 'core')
        sys.modules['nana.cli'] = _namespace('nana.cli', NANA_ROOT / 'cli')
        sys.modules['nana.phases'] = _namespace('nana.phases', NANA_ROOT / 'phases')
        sys.modules['nana.phases.commons'] = _module('nana.phases.commons', CASUAL_POOLS={'soft_ping': ['Con nghe đây Ba.']})
        sys.modules['nana.cli.globals'] = _module('nana.cli.globals', ai_active=True, last_gpt_time=0,
                                                 set_runtime_turn_state=lambda *a: None)
        sys.modules['nana.cli.avatar_requests'] = _module('nana.cli.avatar_requests', handle_owner_avatar_turn=no_avatar)
        sys.modules['nana.runtime.avatar_reply_turn'] = _module('nana.runtime.avatar_reply_turn',
            AvatarReplyTurn=lambda: types.SimpleNamespace(consider=lambda *a: None))
        sys.modules['nana.core.diagnostics'] = _module('nana.core.diagnostics', is_diagnostic_fragment=lambda t: False)
        sys.modules['nana.runtime.browser_refresh'] = _module('nana.runtime.browser_refresh',
            ensure_browser_snapshot=noop, refresh_browser_state=noop)
        sys.modules['nana.runtime.browser_state'] = _module('nana.runtime.browser_state',
            current_browser_snapshot_state=lambda: 'FRESH', is_browser_context_question=lambda t: False)
        sys.modules['nana.runtime.context'].mark_chat_time = lambda: None
        sys.modules['nana.runtime.live_awareness'].build_live_awareness_snapshot = lambda: {}
        sys.modules['nana.runtime.live_awareness'].repair_awareness_reply = lambda text, **kw: text
        sys.modules['nana.runtime.live_awareness'].lock_focus = lambda *a, **kw: None
        sys.modules['nana.runtime.persona'].observe_text_for_persona = lambda text: None
        sys.modules['nana.runtime.recovery'] = _module('nana.runtime.recovery', recovery_message=lambda *a, **kw: 'Đường truyền lỗi, Ba thử lại nhé.')
        sys.modules['nana.integrations'] = _namespace('nana.integrations', NANA_ROOT / 'integrations')
        sys.modules['nana.integrations.vts'] = _module('nana.integrations.vts', trigger_expression_lifecycle=noop)
        sys.modules.pop('nana.runtime.memory_grounding', None)
        for name in ('nana.core.chat_surface', 'nana.cli.chat_turn_pipeline'):
            sys.modules.pop(name, None)
        pipeline = importlib.import_module('nana.cli.chat_turn_pipeline')
        pipeline.extract_important = lambda *a, **kw: None
        notes = []
        pipeline.awareness_memory_note_user_chat = lambda **kw: notes.append(kw)
        yield pipeline, memory, tags, notes

class Voice:
    def __init__(self):
        self.normal = []
        self.overlap = []
        self.ttd = []
    def say(self, text, **kw):
        self.normal.append(text)
        return 1
    def private_overlap_readiness(self):
        return True, 'ready'
    def private_ttd_readiness(self):
        return True, 'ready'
    def record_private_overlap_bypass(self, *a):
        pass
    def record_private_ttd_bypass(self, *a):
        pass
    def say_overlap(self, request, **kw):
        self.overlap.append(request)
        return 1
    def say_ttd(self, request, **kw):
        self.ttd.append(request)
        return 1
    def snapshot(self):
        return {'queue_size': 0, 'queue_maxsize': 5, 'worker_alive': True, 'last_error': None}

LEAD = '[thoughtful] Anna đặt chiếc hộp của bạn nhỏ lên bàn cạnh cửa sổ. '
TAIL = ('Mình cùng đọc từng trang tài liệu để hiểu rõ các chi tiết còn thiếu. ' * 12
        + 'Câu cuối vẫn còn nguyên ở đây END-VOICE-9147.')

class ConsistencyTests(unittest.TestCase):
    def test_fragmented_model_secret_is_masked_before_voice_and_history(self):
        with pipeline_fixture() as (pipeline, memory, tags, notes):
            secret = 'sk-FAKE_ONLY_GENERATED_8147'
            async def stream(*a, **kw):
                for fragment in ('The to', 'ken is s', 'k-FAKE_', 'ONLY_GENERATED_8147'):
                    yield fragment
            pipeline.ask_gpt_stream = stream
            voice = Voice()
            output = io.StringIO()
            with redirect_stdout(output):
                asyncio.run(pipeline.handle_chat_turn(None, voice, 'Nana thử một câu.', None))
            self.assertNotIn(secret, str(voice.normal))
            self.assertNotIn(secret, output.getvalue())
            stored = memory.memory['chat_log'][-1].removeprefix('NANA: ')
            self.assertEqual(stored, ' '.join(tags.strip_inline_audio_tags(''.join(voice.normal)).split()))
            self.assertIn('[redacted secret]', stored)

    def test_buffered_awareness_and_empty_stream_use_same_final_text(self):
        for mode in ('awareness', 'empty'):
            with self.subTest(mode=mode), pipeline_fixture() as (pipeline, memory, tags, notes):
                pipeline.is_browser_context_question = lambda text: mode == 'awareness'
                pipeline.ask_gpt = lambda *a, **kw: LEAD + TAIL
                async def empty(*a, **kw):
                    if False:
                        yield ''
                pipeline.ask_gpt_stream = empty
                voice = Voice()
                with redirect_stdout(io.StringIO()):
                    asyncio.run(pipeline.handle_chat_turn(None, voice, 'Nana giải thích giúp Ba.', None))
                self.assertEqual(' '.join(memory.memory['chat_log'][-1].removeprefix('NANA: ').split()),
                                 ' '.join(tags.strip_inline_audio_tags(''.join(voice.normal)).split()))
                self.assertEqual(len(notes), 1)

    def test_stream_failure_after_commit_records_interruption_not_unspoken_recovery(self):
        with pipeline_fixture() as (pipeline, memory, tags, notes):
            voice = Voice()
            pipeline.PRIVATE_VOICE_OVERLAP_ENABLED = True
            async def broken(*a, **kw):
                yield LEAD
                await asyncio.sleep(.04)
                self.assertTrue(voice.overlap)
                raise RuntimeError('fake stream loss')
            pipeline.ask_gpt_stream = broken
            with redirect_stdout(io.StringIO()):
                asyncio.run(pipeline.handle_chat_turn(None, voice, 'Nana giải thích giúp Ba.', None))
            self.assertEqual(voice.normal, [])
            self.assertTrue(voice.overlap[0].cancel_event.is_set())
            stored = memory.memory['chat_log'][-1]
            self.assertIn('[interrupted]', stored)
            self.assertIn('Anna', stored)
            self.assertNotIn('Đường truyền lỗi', stored)
            self.assertFalse(memory.memory['session_checkpoint'])

    def test_stream_privacy_holds_split_multiword_labels_and_pem(self):
        with pipeline_fixture():
            from nana.runtime.reply_stream_privacy import ReplyStreamPrivacy
            for raw, forbidden in (
                ('A safe start. private key=FAKE_BOUNDARY_9147', 'FAKE_BOUNDARY_9147'),
                ('A safe start. access token=FAKE_BOUNDARY_9147', 'FAKE_BOUNDARY_9147'),
                ('A safe start. -----BEGIN PRIVATE KEY-----\nFAKE_BODY_9147\n-----END PRIVATE KEY-----', 'FAKE_BODY_9147'),
                ('A safe start. Mật khẩu của Ba là FAKE_BOUNDARY_9147.', 'FAKE_BOUNDARY_9147'),
            ):
                stream = ReplyStreamPrivacy()
                emitted = ''.join(stream.feed(char) for char in raw)
                self.assertTrue(emitted.startswith('A safe start.'))
                self.assertNotIn(forbidden, emitted)
                emitted += stream.flush()
                self.assertNotIn(forbidden, emitted)
                self.assertIn('[redacted', emitted)

    def test_stream_privacy_keeps_definition_and_owner_context(self):
        with pipeline_fixture():
            from nana.runtime.reply_stream_privacy import ReplyStreamPrivacy
            for raw, expected in (
                ('My password is a unit of text.', 'My [redacted secret]'),
                ('Ngân sách token là 1200 cho hội thoại.', 'Ngân sách token là 1200 cho hội thoại.'),
            ):
                stream = ReplyStreamPrivacy()
                actual = ''.join(stream.feed(char) for char in raw) + stream.flush()
                self.assertEqual(actual, expected)

    def test_explicit_secret_save_still_fails_without_model_call(self):
        with pipeline_fixture() as (pipeline, memory, tags, notes):
            secret = 'FAKE_SECRET_SAVE_8147'
            pipeline.extract_important = memory.extract_important
            async def forbidden(*a, **kw):
                self.fail('secret save called model')
                yield ''
            pipeline.ask_gpt_stream = forbidden
            with redirect_stdout(io.StringIO()):
                asyncio.run(pipeline.handle_chat_turn(None, Voice(), 'Nhớ kỹ: mật khẩu đăng nhập là ' + secret, None))
            self.assertIn('chưa lưu được', memory.memory['chat_log'][-1])
            self.assertEqual(memory.memory['long_term'], [])
            self.assertNotIn(secret, str(memory.memory))

    def test_direct_gpt_scrubs_current_and_legacy_history_in_both_prompts(self):
        from smoke_memory_answer_retest import private_gpt, collect
        secret = 'FAKE_ONLY_HISTORY_8147'
        with private_gpt('Con đã nhận yêu cầu.') as (gpt, _grounding, captured):
            gpt.memory['short_term'] = ['ba: password=' + secret]
            gpt.load_recent_chat = lambda *a: 'USER: Mật khẩu đăng nhập của tôi là ' + secret
            gpt._get_spine = lambda: types.SimpleNamespace(build_retrieval_block=lambda *a, **kw: 'token=' + secret)
            query = 'Ba muốn hỏi về chuỗi password=' + secret
            gpt.ask_gpt(query)
            collect(gpt, query)
            self.assertEqual(len(captured['sync']), 1)
            self.assertEqual(len(captured['stream']), 1)
            self.assertNotIn(secret, str(captured))

    def test_pipeline_does_not_retain_secret_in_history_or_persona(self):
        with pipeline_fixture() as (pipeline, memory, tags, notes):
            secret = 'FAKE_ONLY_PIPELINE_8147'
            seen = []
            pipeline.observe_text_for_persona = lambda text: seen.append(text)
            async def stream(text, **kw):
                seen.append(text)
                yield 'Con đã nhận yêu cầu.'
            pipeline.ask_gpt_stream = stream
            with redirect_stdout(io.StringIO()):
                asyncio.run(pipeline.handle_chat_turn(None, Voice(), 'Mật khẩu đăng nhập của Ba là ' + secret, None))
            self.assertNotIn(secret, str(seen))
            self.assertNotIn(secret, str(memory.memory))
            self.assertNotIn(secret, memory.load_recent_chat())

    def run_mode(self, mode):
        with pipeline_fixture() as (pipeline, memory, tags, notes):
            voice = Voice()
            pipeline.PRIVATE_VOICE_OVERLAP_ENABLED = mode == 'overlap'
            pipeline.PRIVATE_VOICE_TTD_ENABLED = mode == 'ttd'
            async def stream(*a, **kw):
                yield LEAD
                if mode != 'full':
                    await asyncio.sleep(.04)
                    self.assertTrue(voice.overlap or voice.ttd, 'audio must commit before model EOF')
                yield TAIL
            pipeline.ask_gpt_stream = stream
            with redirect_stdout(io.StringIO()):
                asyncio.run(pipeline.handle_chat_turn(None, voice, 'Nana nói về nhân vật Anna.', None))
            if mode == 'overlap':
                self.assertEqual(len(voice.overlap), 1)
                self.assertEqual(voice.normal, [])
                payload = voice.overlap[0].tail_future.result(timeout=1)
                spoken = voice.overlap[0].lead_text + payload.tail_text
            elif mode == 'ttd':
                self.assertEqual(len(voice.ttd), 1)
                self.assertEqual(voice.normal, [])
                payload = voice.ttd[0].final_future.result(timeout=1)
                spoken = payload.sent_text
            else:
                spoken = ''.join(voice.normal)
            expected = ' '.join(tags.strip_inline_audio_tags(spoken).split())
            stored = memory.memory['chat_log'][-1].removeprefix('NANA: ')
            self.assertIn('END-VOICE-9147', stored)
            self.assertIn('Anna', stored)
            self.assertIn('bạn nhỏ', stored)
            self.assertEqual(' '.join(stored.split()), expected)
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0]['nana_text'], stored)
            self.assertIn(stored, memory.load_recent_chat())
    def test_full_stream_preserves_sent_tail_and_names(self):
        self.run_mode('full')
    def test_overlap_keeps_exact_history_and_early_commit(self):
        self.run_mode('overlap')
    def test_ttd_keeps_exact_history_and_early_commit(self):
        self.run_mode('ttd')

if __name__ == '__main__':
    unittest.main(verbosity=2)
