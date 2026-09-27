"""One optional avatar reaction for a committed conversational voice turn."""
from __future__ import annotations

import threading
import uuid


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


def daily_reaction_prompt() -> str:
    from nana.runtime.avatar_intent_gateway import get_avatar_intent_gateway
    gateway = get_avatar_intent_gateway()
    return DAILY_REACTION_GUIDE if gateway.enabled and gateway.running and gateway.reply_reactions_enabled else ''


class AvatarReplyTurn:
    def __init__(self, source: str = 'chat_reply'):
        self.source = source
        self.correlation_id = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._considered = False

    def consider(self, reply: str):
        # Voice-overlap commit may run on its timer thread while the final reply
        # callback is on the event loop. The turn must still publish at most once.
        with self._lock:
            if self._considered:
                return None
            self._considered = True
        from nana.runtime.avatar_intent_gateway import publish_reply_avatar
        result = publish_reply_avatar(reply, source=self.source, correlation_id=self.correlation_id)
        if result.ok and result.intent is not None:
            print(f'[Avatar] auto={result.intent.action} status={result.receipt.status} id={result.intent.intent_id}', flush=True)
        return result
