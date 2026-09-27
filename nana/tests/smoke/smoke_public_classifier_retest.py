"""Offline regressions for public Vietnamese noun classifiers.

Only namespace imports and temporary bridge directories are used. Production
sanitizing/finalizing code stays real; providers, playback and private data are
guarded by the shared Memory v2 isolation fixture.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from smoke_memory_v2_phase1 import _isolated_nana_imports


class PublicClassifierTests(unittest.TestCase):
    def setUp(self):
        # Windows creates a local socket pair for the event loop. Construct it
        # before installing the network tripwire; all test code runs after it.
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)
        self.enterContext(patch.object(socket.socket, "connect", side_effect=AssertionError("network disabled")))
        self.enterContext(_isolated_nana_imports())
        sys.modules.pop("nana.runtime.livestream_identity", None)
        self.live = importlib.import_module("nana.runtime.livestream_identity")
        self.persona = importlib.import_module("nana.runtime.persona_boundary")

    def test_noun_classifiers_survive_public_and_livestream_guards(self):
        # A broad pronoun substitution corrupts the subject/object of a story.
        samples = (
            "Thế là một con mèo tưởng mình đang livestream ASMR.",
            "Một con thỏ đứng cạnh hai con chó.",
            "Con mèo ngủ rồi, con thỏ vẫn thức.",
            "Con đường chạy dọc con sông, qua một con suối.",
            "Một con thuyền nằm cạnh con tàu.",
            "Con số này có nghĩa với con người và con vật trong truyện.",
            "Mỗi con chữ đều có thể kể chuyện.",
            "Một CON MÈO, một con thỏ, một con chó!",
        )
        for raw in samples:
            with self.subTest(raw=raw):
                self.assertTrue(self.persona.validate_public_reply(raw)[0], raw)
                sanitized = self.persona.sanitize_public_reply(raw)
                self.assertEqual(sanitized, raw)
                final = self.live.finalize_livestream_identity(sanitized, source="youtube")
                self.assertEqual(final, raw)
                self.assertEqual(self.persona.sanitize_public_reply(final), raw)

    def test_mixed_private_pronoun_and_classifier_rewrites_only_pronoun(self):
        # Allowing all "con", or shielding a whole classifier sentence, leaks
        # private address terms in exactly the sentence that needs preserving.
        samples = (
            ("Con thấy một con mèo đang ngủ, Ba ơi.",
             "Nana thấy một con mèo đang ngủ, bạn ơi.",
             "Mình thấy một con mèo đang ngủ, bạn ơi."),
            ("Ba, con thích con thỏ và con chó này.",
             "Bạn, Nana thích con thỏ và con chó này.",
             "Bạn, mình thích con thỏ và con chó này."),
            ("Con chào Ba nha.", "Nana chào bạn nha.", "Mình chào bạn nha."),
            ("Con chạy trên con đường này.",
             "Nana chạy trên con đường này.", "Mình chạy trên con đường này."),
        )
        for raw, public, live in samples:
            with self.subTest(raw=raw):
                self.assertFalse(self.persona.validate_public_reply(raw)[0], raw)
                sanitized = self.persona.sanitize_public_reply(raw, user_text="Kể tiếp đi.", viewer_name="Alex")
                self.assertEqual(sanitized, public)
                self.assertTrue(self.persona.validate_public_reply(sanitized)[0], sanitized)
                self.assertEqual(self.live.finalize_livestream_identity(sanitized, source="youtube"), live)

    def test_classifier_exceptions_do_not_bypass_other_privacy_filters(self):
        fallback = "Mình chỉ nói chuyện công khai."
        for raw in (
            "Con thấy một con mèo trong runtime của Ba.",
            "Con thỏ xuất hiện trên máy của Ba.",
            "Một con chó nằm cạnh codebase của Ba.",
        ):
            with self.subTest(raw=raw):
                self.assertFalse(self.persona.validate_public_reply(raw)[0], raw)
                self.assertEqual(self.persona.sanitize_public_reply(raw, fallback=fallback), fallback)

    def test_real_bridge_preserves_animals_and_sanitizes_private_pronouns(self):
        from smoke_memory_v2_bridge import _setup

        with tempfile.TemporaryDirectory(prefix="nana-classifier-bridge-") as directory:
            bridge, runtime, _cache = _setup(Path(directory))
            request = bridge.ExternalBridgeRequest.from_payload({
                "request_id": "classifier-bridge", "source": "youtube",
                "text": "Mô tả cảnh trước mắt nhé.", "author_id": "viewer-fixture",
                "author_name": "Alex", "channel_id": "fixture-room",
                "local_playback": False,
                "metadata": {"event_id": "classifier-bridge", "stream_session_id": "fixture-session"},
            })
            response = runtime.process_request(
                request, responder=lambda _request: "Con thấy một con mèo, con thỏ và con chó, Ba ơi.",
            )
            self.assertTrue(response["ok"], response)
            self.assertEqual(response["reply_text"], "Mình thấy một con mèo, con thỏ và con chó, bạn ơi.")
            self.assertFalse(response["speak"], response)
            self.assertFalse(response["local_playback"], response)

    def test_actual_gpt_sync_and_stream_preserve_classifiers(self):
        from smoke_memory_v2_public_prompt import _install_isolated_dependencies, _Response

        # This existing fake-provider fixture installs namespace-only packages.
        # Retain the outer root namespace so its startup tripwire remains valid.
        isolated_root = sys.modules["nana"]
        try:
            gpt, _captured = _install_isolated_dependencies()
        finally:
            sys.modules["nana"] = isolated_root
        client = importlib.import_module("nana.brain.llmgate_client")
        raw = "Nana thấy một con mèo ngủ cạnh con thỏ và con chó."
        expected = "Mình thấy một con mèo ngủ cạnh con thỏ và con chó."
        gpt.create_chat_completion_with_fallback = lambda **_kwargs: _Response(raw)

        def stream_reply(**_kwargs):
            yield "Nana thấy một con "
            yield "mèo ngủ cạnh con thỏ và con chó."

        client.stream_llmgate_messages = stream_reply
        common = {"viewer_name": "Alex", "stream_mode": True, "public_platform": "youtube",
                  "metadata": {"author_id": "viewer-fixture", "room_id": "fixture-room",
                               "stream_session_id": "fixture-session", "event_id": "classifier-gpt"}}
        with self.subTest(path="sync"):
            self.assertEqual(gpt.ask_gpt("Mô tả cảnh trước mắt nhé.", **common), expected)

        async def consume():
            return "".join([part async for part in gpt.ask_gpt_stream("Mô tả cảnh trước mắt nhé.", **common)])

        with self.subTest(path="stream"):
            self.assertEqual(self.loop.run_until_complete(consume()), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
