"""Replay memory answer defects with real grounding/GPT code and fake I/O."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

from smoke_memory_v2_phase1 import _isolated_nana_imports
from smoke_memory_v2_public_prompt import _install_isolated_dependencies, _Response


def _audit(event, args):
    if event == 'socket.connect':
        caller = sys._getframe(1)
        if (caller.f_code.co_name in {'socketpair', '_fallback_socketpair'}
                and Path(caller.f_code.co_filename).name == 'socket.py'
                and args[1][0] in {'127.0.0.1', '::1'}):
            return  # Windows asyncio's own wakeup pipe, not a runtime endpoint.
    if event == 'open' and isinstance(args[0], (str, bytes)):
        name = str(args[0]).replace('\\', '/').lower()
        if '/nana/data/' in name or name.endswith('/.env') or name.endswith('/nana/config.py'):
            raise AssertionError('Memory answer smoke attempted production data/config access')
    if event in {'socket.connect', 'subprocess.Popen', 'os.system'}:
        raise AssertionError('Memory answer smoke attempted live network/process access')


sys.addaudithook(_audit)

RECALL_PROMPT = 'Con có nhớ mã vé tàu của Ba không? Nếu chưa từng được cung cấp thì nói chưa biết nhé.'
DENIAL = '[thoughtful] Con chưa biết nha Ba, Ba chưa từng kể mã vé tàu nào cho con nghe hết đó.'
SAVE_PROMPT = 'Có phải con đã lưu mã hộp thư phụ vào trí nhớ lâu dài không? Đừng nhận đã lưu nếu lần đó báo lỗi.'
SAVE_EXPLANATION = '[thoughtful] Con vừa kiểm tra lại bộ nhớ lâu dài thì không có bản lưu nào về mã hộp thư phụ hết nha Ba.'
SAVE_SPECULATION = 'Chắc lần đó bị lỗi thật nên chưa có ghi vào bộ nhớ được rồi.'


@contextmanager
def private_gpt(raw_reply):
    # Preserve the outer namespace identity; only external owners are stubbed.
    with _isolated_nana_imports(), patch.dict(sys.modules):
        gpt, captured = _install_isolated_dependencies()
        sys.modules.pop('nana.runtime.memory_grounding', None)
        grounding = importlib.import_module('nana.runtime.memory_grounding')
        gpt.MemoryEvidence = grounding.MemoryEvidence
        gpt.ConfidenceInjector = grounding.ConfidenceInjector
        gpt.ground_user_message = grounding.ground_user_message
        gpt.grounding_verify_reply = grounding.verify_reply
        gpt._GROUNDING_AVAILABLE = True
        boundary = types.SimpleNamespace(public=False, livestream=False,
                                         interaction_scope='private_owner', prompt_block='PRIVATE BOUNDARY')
        context = {'active_zone': 'unknown', 'active_app': None, 'idle_state': 'active',
                   'time': {}, 'browser': {}}
        gpt._lane_first_inputs = lambda **kw: (
            boundary, context, {}, {'affection': .5, 'annoyance': 0., 'playfulness': .5})
        gpt.resolve_user = lambda **kw: {'name': 'Ba'}
        gpt.format_identity_block = lambda *a, **kw: 'PRIVATE IDENTITY'
        gpt.format_live_awareness_prompt = lambda *a: ''
        gpt._identity_prompt_blocks_for_boundary = lambda *a: ('PRIVATE CORE', '', '')
        gpt._observe_mood_for_turn = lambda *a, **kw: None
        gpt._mood_prompt_for_boundary = lambda *a: ''
        gpt._get_natural_style_hint = lambda: ''
        gpt.persona_prompt_block = lambda **kw: ''
        gpt.load_recent_chat = lambda *a, **kw: ''
        gpt.format_prompt_memory_rules = lambda *a, **kw: ''
        gpt.NANA_SHARED_HISTORY = ''
        gpt.memory['short_term'] = []
        gpt.memory['long_term'] = []
        gpt.get_awareness_memory = lambda: types.SimpleNamespace(
            format_recent_moments_block=lambda **kw: '', get_recent=lambda **kw: [],
            format_timeline_summary=lambda **kw: '')
        spine = types.SimpleNamespace(retrieve=lambda *a, **kw: [],
                                      build_retrieval_block=lambda *a, **kw: '')
        gpt._get_spine = lambda: spine
        sys.modules['nana.runtime.memory_spine'].get_memory_spine = lambda: spine
        def sync(**kw):
            captured['sync'].append(kw['messages'])
            return _Response(raw_reply)
        def stream(**kw):
            captured['stream'].append(kw['messages'])
            midpoint = len(raw_reply) // 2
            yield raw_reply[:midpoint]
            yield raw_reply[midpoint:]
        gpt.create_chat_completion_with_fallback = sync
        sys.modules['nana.brain.llmgate_client'].stream_llmgate_messages = stream
        yield gpt, grounding, captured


def collect(gpt, prompt):
    async def consume():
        return [part async for part in gpt.ask_gpt_stream(prompt)]
    return asyncio.run(consume())


class MemoryAnswerRetest(unittest.TestCase):
    def test_recall_questions_without_time_markers_are_grounded(self):
        with _isolated_nana_imports():
            detector = importlib.import_module('nana.runtime.memory_grounding').MemoryClaimDetector()
            for prompt in (
                RECALL_PROMPT,
                'Con có nhớ trong câu chuyện của Ba, con vật gì đội mũ màu gì không?',
                'Nana còn nhớ tên chiếc hộp của Ba không',
                'Con co nho ma ve tau cua Ba khong?',
                'Nhắc lại mã nhãn chiếc hộp của Ba nhé.',
            ):
                with self.subTest(prompt=prompt):
                    self.assertTrue(detector.is_memory_claim(prompt))
            for prompt in ('Ba nhớ ăn cơm nhé.', 'Nana ơi nói chuyện thử xem',
                           'Giải thích bộ nhớ máy tính là gì?', 'Nhắc lại công thức diện tích hình tròn.',
                           'Con có nhớ công thức tính diện tích hình tròn không?',
                           'Con nhớ Ba lắm.', 'Ba đã nói giúp con việc này rồi.'):
                with self.subTest(ordinary=prompt):
                    self.assertFalse(detector.is_memory_claim(prompt))

    def test_general_knowledge_recall_keeps_the_factual_answer(self):
        raw = 'Con nhớ: S = pi nhân bán kính bình phương.'
        with private_gpt(raw) as (gpt, _grounding, _captured):
            prompt = 'Con có nhớ công thức tính diện tích hình tròn không?'
            self.assertEqual(gpt.ask_gpt(prompt), raw)
            self.assertEqual(''.join(collect(gpt, prompt)), raw)

    def test_found_failure_record_cannot_confirm_write_success(self):
        with _isolated_nana_imports():
            module = importlib.import_module('nana.runtime.memory_grounding')
            evidence = module.MemoryEvidence('found', .95, 'private_only', 'strong', [
                'Ba hỏi lưu mã hộp thư vào bộ nhớ, nhưng lần lưu báo lỗi và không thành công.'
            ])
            result = module.verify_reply(evidence, 'Con đã lưu mã vào bộ nhớ thành công rồi Ba.',
                                         user_text=SAVE_PROMPT)
            self.assertFalse(result.passed)
            self.assertIn('chưa', result.suggested_fallback.lower())

    def test_categorical_denial_is_filtered_before_sync_and_stream_output(self):
        with private_gpt(DENIAL) as (gpt, _grounding, captured):
            sync = gpt.ask_gpt(RECALL_PROMPT)
            chunks = collect(gpt, RECALL_PROMPT)
            for reply in (sync, ''.join(chunks)):
                self.assertNotIn('chưa từng kể', reply.lower())
                self.assertIn('không nhớ rõ', reply.lower())
            self.assertEqual(len(chunks), 1, 'Ungrounded chunks reached the output')
            for messages in captured['sync'] + captured['stream']:
                self.assertIn('MEMORY EVIDENCE:', messages[0]['content'])

    def test_failed_save_explanation_survives_without_speculated_cause(self):
        raw = SAVE_EXPLANATION + ' ' + SAVE_SPECULATION
        with private_gpt(raw) as (gpt, _grounding, _captured):
            for reply in (gpt.ask_gpt(SAVE_PROMPT), ''.join(collect(gpt, SAVE_PROMPT))):
                self.assertIn('không có bản lưu', reply.lower())
                self.assertNotIn('không nhớ rõ', reply.lower())
                self.assertNotIn('bị lỗi thật', reply.lower(), 'Do not invent a verified failure cause')

    def test_status_question_does_not_allow_unverified_save_success(self):
        raw = 'Con đã lưu mã vào trí nhớ lâu dài thành công rồi Ba.'
        with private_gpt(raw) as (gpt, _grounding, _captured):
            reply = ''.join(collect(gpt, SAVE_PROMPT))
            self.assertNotEqual(reply, raw)
            self.assertIn('chưa', reply.lower())
            self.assertIn('xác nhận', reply.lower())

    def test_careful_qualification_is_not_an_absolute_denial(self):
        reply = 'Con không thể kết luận Ba chưa từng kể chỉ vì không thấy trong ngữ cảnh hiện tại.'
        with _isolated_nana_imports():
            module = importlib.import_module('nana.runtime.memory_grounding')
            ev = module.MemoryEvidence('user_claim_only', .25, 'private_only')
            self.assertTrue(module.verify_reply(ev, reply).passed)

    def test_no_record_paraphrase_survives_without_invented_error(self):
        raw = ('Con kiểm tra lại thì không thấy thông tin mã hộp thư phụ nào trong bộ nhớ hết Ba ơi. '
               'Chắc lần đó bị lỗi nên con chưa lưu lại được rồi.')
        with private_gpt(raw) as (gpt, _grounding, _captured):
            reply = ''.join(collect(gpt, SAVE_PROMPT))
            self.assertIn('không thấy thông tin mã hộp thư phụ', reply)
            self.assertNotIn('bị lỗi', reply)

    def test_absence_statement_can_put_storage_before_negation(self):
        raw = ('Trong trí nhớ lâu dài hiện tại của con không có dữ liệu nào về mã hộp thư phụ hết Ba ơi. '
               'Lần đó bị báo lỗi nên chắc chắn là nó chưa được ghi lại vào bộ nhớ của con rồi.')
        with private_gpt(raw) as (gpt, _grounding, _captured):
            reply = ''.join(collect(gpt, SAVE_PROMPT))
            self.assertIn('không có dữ liệu nào', reply)
            self.assertNotIn('báo lỗi', reply)

    def test_one_sentence_request_removes_the_second_sentence(self):
        with private_gpt('') as (gpt, _grounding, _captured):
            self.assertEqual(gpt.shape_chat_reply('Một con mèo đang ngủ. Một con thỏ đang thức.',
                user_text='Kể một câu về con mèo.'), 'Một con mèo đang ngủ.')

    def test_not_provided_claim_is_not_evidence_of_missing_history(self):
        with private_gpt('Con chưa biết nha Ba. Ba chưa cho con thông tin đó mà.') as (gpt, _grounding, _captured):
            reply = ''.join(collect(gpt, RECALL_PROMPT))
            self.assertNotIn('Ba chưa cho', reply)
            self.assertIn('không nhớ rõ', reply)

    def test_removed_opening_does_not_leave_a_punctuation_sentence(self):
        with private_gpt('') as (gpt, _grounding, _captured):
            cleaner = gpt.StreamingReplySurfaceSanitizer()
            parts = [cleaner.feed(part) for part in ('Con nhớ rồi Ba', '.', ' Từ MANTRA-8642 là ghi chú riêng.')]
            reply = ''.join(parts) + cleaner.flush()
            self.assertFalse(reply.lstrip().startswith('.'), reply)
            self.assertIn('MANTRA-8642', reply)

    def test_removed_opening_with_real_audio_tag_has_no_orphan_dot(self):
        with private_gpt('') as (gpt, _grounding, _captured):
            sys.modules.pop('nana.voice.inline_audio_tags', None)
            tags = importlib.import_module('nana.voice.inline_audio_tags')
            gpt.strip_inline_audio_tags = tags.strip_inline_audio_tags
            gpt.repair_malformed_inline_audio_tags = tags.repair_malformed_inline_audio_tags
            cleaner = gpt.StreamingReplySurfaceSanitizer()
            raw = '[warmly] Con nhớ rồi Ba. Từ MANTRA-8642 được giữ riêng.'
            reply = cleaner.feed(raw) + cleaner.flush()
            spoken = gpt.strip_terminal_audio_tags(reply).strip()
            self.assertFalse(spoken.startswith('.'), spoken)
            self.assertIn('MANTRA-8642', spoken)
            final = gpt.finalize_reply(raw)
            self.assertFalse(final.lstrip().startswith('.'), final)

    def test_elliptical_absence_of_saved_data_is_kept(self):
        raw = ('Trong bộ nhớ lâu dài hiện tại của con không có lưu mã hộp thư phụ nào hết Ba ơi. '
               'Chắc lần đó bị lỗi nên dữ liệu chưa ghi lại được rồi.')
        with private_gpt(raw) as (gpt, _grounding, _captured):
            reply = ''.join(collect(gpt, SAVE_PROMPT))
            self.assertIn('không có lưu mã hộp thư phụ', reply)
            self.assertNotIn('bị lỗi', reply)

    def test_denials_and_unsupported_details_still_fail(self):
        with _isolated_nana_imports():
            module = importlib.import_module('nana.runtime.memory_grounding')
            for status in ('not_found', 'partial', 'user_claim_only'):
                ev = module.MemoryEvidence(status, .25, 'private_only')
                for reply in ('Ba chưa từng kể chuyện đó. Con không chắc thời gian thôi.',
                              'Đúng rồi Ba, token expire lúc 4 giờ sáng.',
                              'Ba chưa bao giờ cung cấp mã đó.',
                              'Con chưa có xác nhận lưu. Con chắc chắn đã lưu lúc 4 giờ sáng.'):
                    with self.subTest(status=status, reply=reply):
                        self.assertFalse(module.verify_reply(ev, reply).passed)


if __name__ == '__main__':
    unittest.main(verbosity=2)
