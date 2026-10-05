"""Private avatar reply routing stays correlated and isolated; no live services."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import threading
import types
from types import SimpleNamespace
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# Keep the eager Nana root facade, production memory and .env out of this smoke.
package = types.ModuleType('nana')
package.__path__ = [str(Path(__file__).resolve().parents[2])]
sys.modules['nana'] = package
config = types.ModuleType('nana.config')
config.ELEVEN_PUBLIC_TTS_MODEL = 'eleven_v3'
sys.modules['nana.config'] = config

def guard(event, args):
    if event == 'open' and isinstance(args[0], (str, bytes)):
        name = str(args[0]).replace('\\', '/').lower()
        if '/nana/data/' in name or name.endswith('/.env') or '/runtime_logs/' in name:
            raise AssertionError('production data access forbidden')
    if event == 'socket.connect':
        raise AssertionError('network forbidden')
sys.addaudithook(guard)

from nana.runtime.avatar_reply_turn import (
    DAILY_REACTION_GUIDE,
    AvatarReplyTurn,
    daily_reaction_prompt,
    private_avatar_scope,
)
from nana.runtime.private_voice_receipts import PrivateVoiceContext


EPOCH = "11111111-1111-4111-8111-111111111111"
SESSION = "22222222-2222-4222-8222-222222222222"
TURN = "33333333-3333-4333-8333-333333333333"
CORRELATION = "44444444-4444-4444-8444-444444444444"


def private_context() -> PrivateVoiceContext:
    return PrivateVoiceContext(EPOCH, SESSION, TURN, CORRELATION)


class RecordingPublisher:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[PrivateVoiceContext, str]] = []
        self._lock = threading.Lock()

    def publish_reply(self, context: PrivateVoiceContext, reply: str) -> bool:
        with self._lock:
            self.calls.append((context, reply))
        if self.error is not None:
            raise self.error
        return True


class PrivateAvatarReactions(unittest.TestCase):
    def test_private_prompt_does_not_get_generic_gateway(self):
        publisher = RecordingPublisher()
        with patch(
            "nana.runtime.avatar_intent_gateway.get_avatar_intent_gateway",
            side_effect=AssertionError("private prompt touched generic gateway"),
        ):
            with private_avatar_scope(publisher, private_context()):
                self.assertEqual(daily_reaction_prompt(), DAILY_REACTION_GUIDE)

    def test_private_turn_is_captured_and_published_once_across_threads(self):
        publisher = RecordingPublisher()
        context = private_context()
        reply = "[warmly] Con o day voi Ba."
        with private_avatar_scope(publisher, context):
            turn = AvatarReplyTurn()

        with patch(
            "nana.runtime.avatar_intent_gateway.publish_reply_avatar",
            side_effect=AssertionError("private turn fell back to generic gateway"),
        ):
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(turn.consider, [reply] * 24))

        self.assertEqual(turn.correlation_id, CORRELATION)
        self.assertEqual(publisher.calls, [(context, reply)])
        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(None), 23)

    def test_scope_resets_after_dispatch_failure(self):
        publisher = RecordingPublisher()
        context = private_context()
        with self.assertRaisesRegex(RuntimeError, "dispatcher failed"):
            with private_avatar_scope(publisher, context):
                raise RuntimeError("dispatcher failed")

        generic_result = SimpleNamespace(ok=False, intent=None)
        with patch(
            "nana.runtime.avatar_intent_gateway.publish_reply_avatar",
            return_value=generic_result,
        ) as generic_publish:
            outside_turn = AvatarReplyTurn()
            self.assertIs(outside_turn.consider("A neutral reply."), generic_result)

        self.assertNotEqual(outside_turn.correlation_id, CORRELATION)
        self.assertEqual(len(outside_turn.correlation_id), 32)
        generic_publish.assert_called_once_with(
            "A neutral reply.",
            source="chat_reply",
            correlation_id=outside_turn.correlation_id,
        )
        self.assertEqual(publisher.calls, [])

    def test_private_publisher_failure_is_harmless_and_has_no_fallback(self):
        publisher = RecordingPublisher(error=RuntimeError("sink unavailable"))
        context = private_context()
        with private_avatar_scope(publisher, context):
            turn = AvatarReplyTurn()

        with patch(
            "nana.runtime.avatar_intent_gateway.publish_reply_avatar",
            side_effect=AssertionError("private failure fell back to generic gateway"),
        ):
            self.assertFalse(turn.consider("[happy] Van noi tiep."))
            self.assertIsNone(turn.consider("[happy] Khong gui lai."))

        self.assertEqual(publisher.calls, [(context, "[happy] Van noi tiep.")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
