"""Owner-approved semantic reactions. No model, transport, camera, or pose calls."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from nana.voice.inline_audio_tags import render_inline_audio_tags


OWNER_DISABLED_ACTIONS = frozenset({'wave', 'shy_smile', 'heart_happy'})
REPLY_ACTIONS = frozenset({
    'wink_soft_smile', 'surprised_pout', 'serious_think', 'cat_teary_smile',
    'shy_crying', 'playful_wink', 'curious', 'happy', 'nod', 'listen',
    'think', 'surprised', 'shy', 'playful',
})
SUPPORTED_ACTIONS = REPLY_ACTIONS | {'look', 'blink', 'idle', 'settle'}
REPLY_COOLDOWN_SECONDS = 3.5

# Use the direction Nana already chose for its reply, without another LLM call.
TAG_ACTIONS = {
    'happy': 'happy', 'excited': 'happy', 'delighted': 'happy',
    'warmly': 'wink_soft_smile', 'reassuring': 'wink_soft_smile',
    'surprised': 'surprised', 'alarmed': 'surprised',
    'amazed': 'surprised_pout', 'appalled': 'surprised_pout',
    'professional': 'serious_think', 'angry': 'serious_think',
    'annoyed': 'serious_think', 'frustrated': 'serious_think',
    'sad': 'cat_teary_smile', 'sympathetic': 'cat_teary_smile', 'cute': 'cat_teary_smile',
    'crying': 'shy_crying', 'desperately': 'shy_crying',
    'mischievously': 'playful_wink', 'sarcastic': 'playful',
    'curious': 'curious', 'questioning': 'curious', 'confused': 'curious',
    'thoughtful': 'think', 'confidently': 'nod', 'impressed': 'nod',
    'relaxed': 'listen', 'casual': 'listen', 'whisper': 'listen',
    'nervous': 'shy', 'sheepishly': 'shy',
}


@dataclass(frozen=True)
class ReplyAvatarCue:
    action: str = ''
    reason: str = 'no_reply_cue'


def action_block_reason(action: str) -> str:
    if action in OWNER_DISABLED_ACTIONS:
        return 'owner_disabled_action'
    return '' if action in SUPPORTED_ACTIONS else 'unsupported_runtime_action'


def select_reply_cue(reply: str) -> ReplyAvatarCue:
    """Inspect only Nana's finalized reply, never viewer text or camera controls."""
    text = str(reply or '').strip()[:2000]
    if not text or text.startswith(('"', "'", '`', '>', 'http://', 'https://', '\u201c')):
        return ReplyAvatarCue()
    prefix = re.match(r'(?:\[[^\]\r\n]{1,40}\]\s*){1,5}', text)
    if prefix:
        if not text[prefix.end():].strip():
            return ReplyAvatarCue()
        direction = render_inline_audio_tags(prefix.group(), enabled=True)
        for tag in direction.tags:
            if tag in TAG_ACTIONS:
                return ReplyAvatarCue(TAG_ACTIONS[tag], f'reply_tag:{tag}')
        return ReplyAvatarCue()
    folded = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    folded = folded.casefold().replace('\u0111', 'd')
    rules = (
        (r'^(?:ha(?:ha)+|hi(?:hi)+|he(?:he)+|=\)+|:\)+)', 'happy'),
        (r'^(?:u|uk|uh|dung|chuan|duoc|ok|okay|vang|dong y)\b', 'nod'),
        (r'^(?:minh|con|nana|yumi) (?:day|dang nghe|nghe)\b', 'listen'),
        (r'^(?:(?:minh|con) (?:nghi|dang nghi)|de (?:minh|con) nghi)\b', 'think'),
        (r'^(?:ua|ui|troi oi|that a)\b', 'surprised'),
    )
    for pattern, action in rules:
        if re.search(pattern, folded):
            return ReplyAvatarCue(action, 'reply_opening')
    return ReplyAvatarCue()
