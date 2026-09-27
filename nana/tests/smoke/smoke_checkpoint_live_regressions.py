"""Regressions observed in the September 11 real-model checkpoint trial."""
import copy
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[3]
def guard(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes)):
        path = str(args[0]).replace('\\', '/').lower()
        if '/nana/data/' in path or path.endswith('/.env'):
            raise AssertionError('Production data is forbidden')
    if event in {'socket.connect', 'subprocess.Popen', 'os.system'}:
        raise AssertionError('External runtime is forbidden')
sys.addaudithook(guard)
for package_name, package_path in (("nana", ROOT / "nana"), ("nana.runtime", ROOT / "nana/runtime")):
    package = types.ModuleType(package_name)
    package.__path__ = [str(package_path)]
    sys.modules[package_name] = package
spec = importlib.util.spec_from_file_location('nana.runtime.checkpoint_regression_domain', ROOT / 'nana/runtime/session_checkpoint.py')
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

class LiveRegressions(unittest.TestCase):
    def record(self, store, index, text):
        return cp.record_private_turn(store, user_text=text, nana_text='Nana đã nhận câu này.',
                                      event_id=f'e{index}', now=float(index))

    def test_indirect_synthetic_password_is_not_retained(self):
        store = {}
        self.record(store, 1, 'Chi tiết an toàn: đèn giấy tên Sao Nhỏ. Mật khẩu tạm dùng để kiểm tra là violet-test-730.')
        self.assertNotIn('violet-test-730', str(store))
        self.assertNotIn('violet-test-730', cp.format_private_checkpoint_prompt(store, now=2))
        self.assertIn('Sao Nhỏ', cp.format_private_checkpoint_prompt(store, now=2))

    def test_password_punctuation_does_not_expose_suffixes(self):
        for secret in ('violet.test.730', 'violet!test730'):
            for prefix in ('password=', 'Mật khẩu tạm dùng để kiểm tra là '):
                store = {}
                self.record(store, 1, 'Chi tiết an toàn: đèn giấy tên Sao Nhỏ. ' + prefix + secret)
                self.assertNotIn('test', str(store))
                self.assertNotIn('730', str(store))

    def test_spoken_questions_without_punctuation_are_not_facts(self):
        for question in ('Con nhớ con hươu có cánh không', 'Lúc nãy Ba kể con hươu có cánh à'):
            store = {}
            self.record(store, 1, question)
            self.assertEqual(cp.private_checkpoint_evidence_candidates(store, 'Con hươu có cánh', now=2), [])

    def test_leading_question_is_not_factual_evidence(self):
        store = {}
        self.record(store, 1, 'Một chi tiết: chú thỏ Mít đội mũ cam.')
        self.record(store, 2, 'Lúc nãy Ba kể một con hươu có cánh ở hồ bạc, đúng không?')
        evidence = cp.private_checkpoint_evidence_candidates(store, 'Con nhớ con hươu Ba kể có đặc điểm gì?', now=3)
        self.assertNotIn('e2', [item['source_event_id'] for item in evidence])
        for index in range(3, 10):
            self.record(store, index, f'Ba hỏi câu phụ thứ {index}?')
        evidence = cp.private_checkpoint_evidence_candidates(store, 'Con nhớ con hươu Ba kể có đặc điểm gì?', now=10)
        self.assertNotIn('e2', [item['source_event_id'] for item in evidence])

    def test_pending_correction_remains_visible_under_prompt_pressure(self):
        store = {}
        for index in range(1, 17):
            self.record(store, index, f'Chi tiết câu chuyện {index}: ' + 'những viên đá nhiều màu nằm bên bờ sông. ' * 8)
        self.record(store, 17, 'Chốt chi tiết mới: khăn của rái cá là màu vàng, không còn tím.')
        prompt = cp.format_private_checkpoint_prompt(store, now=18)
        self.assertLessEqual(len(prompt), cp.MAX_PROMPT_CHARS)
        self.assertIn('khăn của rái cá là màu vàng', prompt)
        self.assertIn('Chi tiết câu chuyện 1:', prompt)
        for index in range(18, 25):
            self.record(store, index, f'Ba hỏi câu phụ thứ {index}?')
        prompt = cp.format_private_checkpoint_prompt(store, now=25)
        self.assertLessEqual(len(prompt), cp.MAX_PROMPT_CHARS)
        self.assertIn('khăn của rái cá là màu vàng', prompt)
        self.assertIn('Chi tiết câu chuyện 1:', prompt)

if __name__ == '__main__':
    unittest.main(verbosity=2)
