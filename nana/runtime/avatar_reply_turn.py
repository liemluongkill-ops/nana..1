"""One optional avatar reaction for a committed conversational voice turn."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import threading
from typing import Iterator, Protocol
import uuid

from nana.runtime.private_voice_receipts import PrivateVoiceContext


DAILY_REACTION_GUIDE = """
NANA'S NATURAL AVATAR REACTION
- You may express yourself through the existing 3D body during ordinary conversation;
  Ba does not need to ask for an event. Choose your own delivery, not a keyword from his message.
- For a warm, playful, curious, happy or reflective reply, place one matching existing
  silent voice direction at the beginning: [warmly], [mischievously], [curious], [happy],
  [thoughtful], [relaxed] or [nervous]. It also cues one short body/face reaction.
- A neutral factual answer can stay untagged. Do not make every sentence emotional.
- Use sad/crying directions only when your own delivery really warrants that intensity.
- Keep all current voice-tag restrictions. Do not invent motion tags or action JSON.
- Do not describe the gesture, mention events, or change the substance of the reply
  just to animate. The body returns automatically; the mouth follows actual voice separately.
""".strip()


class _PrivateAvatarPublisher(Protocol):
    def publish_reply(self, context: PrivateVoiceContext, reply: str) -> bool: ...


_PRIVATE_AVATAR_SCOPE: ContextVar[
    tuple[_PrivateAvatarPublisher, PrivateVoiceContext] | None
] = ContextVar("private_avatar_scope", default=None)


@contextmanager
def private_avatar_scope(
    publisher: _PrivateAvatarPublisher,
    context: PrivateVoiceContext,
) -> Iterator[None]:
    token = _PRIVATE_AVATAR_SCOPE.set((publisher, context))
    try:
        yield
    finally:
        _PRIVATE_AVATAR_SCOPE.reset(token)


def daily_reaction_prompt() -> str:
    if _PRIVATE_AVATAR_SCOPE.get() is not None:
        return DAILY_REACTION_GUIDE
    from nana.runtime.avatar_intent_gateway import get_avatar_intent_gateway
    gateway = get_avatar_intent_gateway()
    return DAILY_REACTION_GUIDE if gateway.enabled and gateway.running and gateway.reply_reactions_enabled else ''


class AvatarReplyTurn:
    def __init__(self, source: str = 'chat_reply'):
        self.source = source
        self._private_binding = _PRIVATE_AVATAR_SCOPE.get()
        self.correlation_id = (
            self._private_binding[1].correlation_id
            if self._private_binding is not None
            else uuid.uuid4().hex
        )
        self._lock = threading.Lock()
        self._considered = False

    def consider(self, reply: str):
        # Voice-overlap commit may run on its timer thread while the final reply
        # callback is on the event loop. The turn must still publish at most once.
        with self._lock:
            if self._considered:
                return None
            self._considered = True
        if self._private_binding is not None:
            publisher, context = self._private_binding
            try:
                return bool(publisher.publish_reply(context, reply))
            except Exception:
                return False
        from nana.runtime.avatar_intent_gateway import publish_reply_avatar
        result = publish_reply_avatar(reply, source=self.source, correlation_id=self.correlation_id)
        if result.ok and result.intent is not None:
            print(f'[Avatar] auto={result.intent.action} status={result.receipt.status} id={result.intent.intent_id}', flush=True)
        return result
