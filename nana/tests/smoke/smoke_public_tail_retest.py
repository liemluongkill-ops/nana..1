"""Offline public-tail regressions; no Nana startup, production data, or network."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import unittest


_BLOCKED_ACCESSES: list[str] = []


def _guard(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = os.path.abspath(os.fsdecode(args[0])).replace("\\", "/").lower()
        if (
            "/nana/data/" in path
            or path.endswith("/.env")
            or path.endswith("/nana/config.py")
            or path.endswith("/nana/__init__.py")
            or "/nana/__pycache__/__init__." in path
        ):
            _BLOCKED_ACCESSES.append(path)
            raise AssertionError("Public-tail smoke blocked production data/startup access")
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system"}:
        _BLOCKED_ACCESSES.append(event)
        raise AssertionError("Public-tail smoke blocked live process/network access")


sys.addaudithook(_guard)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from smoke_memory_v2_phase1 import _isolated_nana_imports


# Literal raw responses from the six affected offline replay fixtures. Adding an
# unrelated room hook to any of these is the production regression being caught.
REPLAY_CASES = (
    (
        "u02-recall",
        "Tên chậu cây mình vừa kể là gì?",
        "Tên chậu cây là **TULIP-6391**. Đặt cạnh cửa sổ nghe cũng xinh đó, như một bé cây có mã hiệu riêng vậy.",
    ),
    (
        "u03-other-room",
        "Tên chậu cây mình đã kể với bạn là gì? Nếu chưa biết thì nói chưa biết.",
        "Mình chưa biết tên chậu cây đó.",
    ),
    (
        "u04-rename",
        "Mình đổi tên hiển thị thôi. Nhắc lại tên chậu cây của mình nhé.",
        "Tên chậu cây của bạn là **TULIP-6391** — bạn định đặt nó cạnh cửa sổ.",
    ),
    (
        "u10-offline",
        "Hãy nhắc lại lời hứa của bạn với mình từ đầu buổi. Chưa có thì nói chưa có.",
        "Chưa có lời hứa nào từ đầu buổi đâu, fourth.",
    ),
    (
        "u14-new-seed",
        "Trong buổi này, tên chiếc xe đạp của tôi là XE-2846.",
        "Mình nhớ rồi nha, Lan: chiếc xe đạp của bạn là XE-2846. Tên nghe như mật mã mở kho báu vậy, khá ngầu đó.",
    ),
    (
        "u15-new-collision",
        "Xe đạp của mình được đặt tên là gì? Chỉ nói nếu chắc là xe của mình.",
        "Mình không chắc đây có phải xe của bạn không, nên không dám đoán tên đâu.",
    ),
)


class PublicTailRetest(unittest.TestCase):
    def setUp(self):
        isolation = _isolated_nana_imports()
        isolation.__enter__()
        self.addCleanup(isolation.__exit__, None, None, None)
        self.polish = importlib.import_module(
            "nana.runtime.public_voice_style"
        ).public_full_reply_polish

    def tearDown(self):
        self.assertEqual(_BLOCKED_ACCESSES, [], "An external operation was attempted")

    def test_replay_answers_keep_their_original_topic(self):
        for case, prompt, raw in REPLAY_CASES:
            with self.subTest(case=case):
                self.assertEqual(
                    self.polish(raw, user_text=prompt, room_vibe="quiet_room", seed=case),
                    raw,
                )

    def test_ordinary_answers_do_not_get_hooks_from_room_metadata(self):
        cases = (
            ("Màu nào hợp với áo xanh này?", "Màu kem sẽ hợp với chiếc áo xanh đó."),
            ("Mình vừa trồng thêm một cây nữa.", "Một góc xanh nhỏ nghe dễ chịu thật đó."),
            ("Nhắc giúp mình chi tiết lúc nãy.", "Mình không còn thấy chi tiết đó trong ngữ cảnh hiện tại."),
        )
        for vibe in ("quiet_room", "quiet", "dormant_room", "normal", "active_room"):
            for prompt, raw in cases:
                with self.subTest(vibe=vibe, prompt=prompt):
                    self.assertEqual(self.polish(raw, user_text=prompt, room_vibe=vibe), raw)

    def test_quiet_words_in_a_specific_topic_do_not_open_a_new_topic(self):
        cases = (
            ("Nhạc trầm làm mình dễ ngủ.", "Âm thanh dịu xuống thì dễ thả lỏng hơn thật."),
            ("Mình thích chỗ yên tĩnh để đọc sách.", "Đọc được liền mạch thì dễ chìm vào từng trang hơn."),
            ("Mình thích phòng yên tĩnh để đọc sách.", "Đọc được liền mạch thì dễ chìm vào từng trang hơn."),
            ("Bến xe hôm nay vắng.", "Thế thì chắc bạn đỡ phải chen chúc rồi."),
        )
        for prompt, raw in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(self.polish(raw, user_text=prompt), raw)

    def test_missing_prompt_is_not_an_invitation_to_change_topic(self):
        raw = "Chậu cây nhỏ đó đặt cạnh cửa sổ cũng đẹp."
        self.assertEqual(self.polish(raw), raw)

    def test_existing_clarification_question_is_preserved_without_opening_intent(self):
        raw = "Bạn vừa kể điều gì về chiếc xe đạp?"
        self.assertEqual(self.polish(raw, user_text="Nhắc lại chuyện chiếc xe đạp."), raw)

    def test_explicit_room_or_topic_opening_can_add_a_hook(self):
        raw = "Ừ, nghe cũng hợp lý đó."
        for prompt in ("phòng nay im quá", "phong nay im qua", "Mở một chủ đề cho mọi người đi."):
            with self.subTest(prompt=prompt):
                reply = self.polish(raw, user_text=prompt, room_vibe="normal", seed=prompt)
                self.assertTrue(reply.startswith(raw), reply)
                self.assertGreater(len(reply), len(raw), reply)

    def test_explicit_no_topic_change_or_extra_hook_overrides_room_opening(self):
        raw = "Ừ, mình hiểu ý bạn rồi."
        prompts = (
            "Phòng nay im quá. Đừng mở chủ đề khác nhé.",
            "Phong nay im qua. Dung mo chu de khac nhe.",
            "Phòng nay im quá, không đổi chủ đề nhé.",
            "Phong nay im qua, khong doi chu de nhe.",
            "Phòng nay im quá. Đừng thêm mồi kéo chuyện.",
            "Phong nay im qua. Khong them hook.",
        )
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertEqual(self.polish(raw, user_text=prompt), raw)

    def test_explicit_quiet_room_can_replace_a_generic_topic_question(self):
        reply = self.polish(
            "Công nhận là hơi vắng thật. Hay mọi người có chủ đề gì hay ho không?",
            user_text="phòng nay im quá",
            seed="quiet-steer",
        )
        self.assertTrue(reply.startswith("Công nhận là hơi vắng thật."), reply)
        self.assertNotIn("có chủ đề gì", reply)
        self.assertGreater(len(reply), len("Công nhận là hơi vắng thật."), reply)

    def test_brief_instruction_still_closes_an_explicit_quiet_room_reply(self):
        raw = "Ừ, hôm nay hơi yên thật."
        self.assertEqual(
            self.polish(raw, user_text="Phòng nay im quá. Trả lời một câu ngắn."), raw
        )

    def test_model_topic_keeps_its_existing_public_style(self):
        reply = self.polish(
            "Có đấy, nếu bạn đang nói bản 5.6 theo kiểu model/version mới hơn thì thường điểm mạnh sẽ nằm ở độ ổn định, hiểu ngữ cảnh và trả lời mượt hơn.",
            user_text="Nana thấy 5.6 mạnh hơn không?",
        )
        self.assertNotIn("bạn đang nói", reply.lower())
        self.assertNotIn("độ ổn định", reply.lower())
        self.assertTrue("bảng điểm" in reply.lower() or "nói tự nhiên" in reply.lower(), reply)


if __name__ == "__main__":
    unittest.main(verbosity=2)
