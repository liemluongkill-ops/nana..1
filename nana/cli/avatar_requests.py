"""Explicit private-owner avatar requests. Never used for public viewer input."""
from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

from nana.runtime.avatar_reaction_policy import OWNER_DISABLED_ACTIONS, REPLY_ACTIONS


_ALIASES = {
    'wave': r'vay tay',
    'shy_smile': r'giau tay(?: cuoi)?|dua (?:hai )?tay (?:ve |ra )?(?:phia )?sau',
    'heart_happy': r'(?:vui )?mat (?:trai )?tim',
    'wink_soft_smile': r'nhay nhe(?: cuoi)?|nhay (?:mot ben mat|mot ben|mot mat)|nhay (?:mat )?(?:ben )?trai|nhay mat(?! (?:ben )?phai)',
    'playful_wink': r'nhay tinh nghich|nhay (?:mat )?(?:ben )?phai',
    'surprised_pout': r'ngac nhien biu|biu moi',
    'serious_think': r'nghiem tuc',
    'cat_teary_smile': r'(?:mat )?meo|rung rung',
    'shy_crying': r'ngai khoc|khoc',
    'curious': r'to mo|nghieng dau',
    'happy': r'cuoi nhe|cuoi',
    'nod': r'gat dau',
    'listen': r'lang nghe',
    'think': r'suy nghi',
    'surprised': r'ngac nhien',
    'shy': r'ngai ngung',
    'playful': r'tinh nghich',
    'settle': r've idle|ve binh thuong|ve trang thai binh thuong|dung dong tac',
}
_REQUEST_START = re.compile(
    r'\b(?:con|em|nana|na na|yumi|nayumi(?: liora)?)\s+(?:oi\s+)?'
    r'(?P<verb>hay|thu|cu|co the|lam on|vui long|gui|ban|phat|chay|doi|lam|the hien|gat|nhay|cuoi|nghieng|ve|dung|khong|chua|khoan|bieu cam|dong tac)\b'
)
_DIRECT_REQUEST = re.compile(
    r'^(?P<verb>hay|thu|cu|lam on|vui long|gui|ban|phat|chay|doi|gat dau|nhay mat|cuoi|nghieng dau|ve idle|ve binh thuong|dung dong tac)\b'
)
_DISCUSSION = re.compile(
    r'\b(neu|gia su|vi du|gia vo|noi rang|noi la|ke ve|giai thich|dich|co nen|co nghia|la gi(?! day)|tai sao|vi sao|toi thay|nguoi ta)\b'
)
_PURPOSE = re.compile(
    r'\b(de (?:cho )?(?:ba|toi)\b|xem (?:no|model|cai|co|ben)\b|kiem tra xem|hay la (?:con|em)\b|cho (?:ba|toi) xem)'
)
_AVATAR_TOPIC = re.compile(r'\b(event|bieu cam|dong tac|avatar|model|mo hinh 3d)\b')


@dataclass(frozen=True)
class OwnerAvatarRequest:
    action: str = ''
    reason: str = ''


def parse_owner_avatar_request(text: str) -> OwnerAvatarRequest | None:
    raw = str(text or '').strip()
    if any(c in raw for c in ('`', '"', '\u201c', '\u201d')):
        return None
    folded = ''.join(c for c in unicodedata.normalize('NFD', raw) if unicodedata.category(c) != 'Mn')
    folded = re.sub(r'\s+', ' ', folded.casefold().replace('\u0111', 'd'))
    start = _REQUEST_START.search(folded) or _DIRECT_REQUEST.search(folded)
    if start is None or _DISCUSSION.search(folded[:start.start('verb')]):
        return None
    # Recognize the command clause independently of a vocative/ASR preamble and
    # purpose questions such as "xem no co hoat dong khong".
    clause = _PURPOSE.split(folded[start.start('verb'):], maxsplit=1)[0].strip()
    if _DISCUSSION.search(clause):
        return None
    clause = re.sub(r'\s+duoc (?:khong|ko)[.!?]*$', '', clause)
    clause = re.sub(r'\b(gui|ban|phat|chay) dung\b', r'\1', clause)
    if len(raw) > 1000:
        return OwnerAvatarRequest(reason='name_one_action') if _AVATAR_TOPIC.search(clause) else None
    if re.search(r'\b(camera|goc may|goc quay|goc nhin|zoom|orbit|khung hinh|doi model)\b', clause):
        return OwnerAvatarRequest(reason='camera_is_human_owned')
    actions = {
        action for action, aliases in _ALIASES.items()
        if re.search(rf'(?<!\w)(?:{re.escape(action)}|{aliases})(?!\w)', clause)
    }
    if (actions or _AVATAR_TOPIC.search(clause)) and re.search(r'\b(khong|chua|khoan|dung(?! dong tac))\b', clause):
        return OwnerAvatarRequest(reason='negated_owner_request')
    blocked = actions & OWNER_DISABLED_ACTIONS
    if blocked:
        return OwnerAvatarRequest(sorted(blocked)[0], 'owner_disabled_action')
    # A composite label contains its simpler words; it still names one preset.
    if actions & {'wink_soft_smile', 'cat_teary_smile'}:
        actions.discard('happy')
    if 'playful_wink' in actions:
        actions.discard('playful')
    if 'surprised_pout' in actions:
        actions.discard('surprised')
    if actions & {'think', 'listen', 'happy', 'shy', 'playful', 'serious_think'}:
        explicit = _AVATAR_TOPIC.search(clause)
        named = any(re.search(rf'(?<!\w){re.escape(a)}(?!\w)', clause) for a in actions)
        if actions & {'think', 'listen'} and not explicit and not named:
            return None
        simple = any(re.search(rf'(?:{_ALIASES[a]})(?: (?:di|nha|nhe|xem|nao|mot lan))*[.!?]*$', clause) for a in actions)
        if not explicit and not named and not simple:
            return None
    if len(actions) == 1:
        return OwnerAvatarRequest(actions.pop(), 'explicit_owner_request')
    if actions or _AVATAR_TOPIC.search(clause):
        return OwnerAvatarRequest(reason='name_one_action')
    return None


def handle_owner_avatar_request(text: str, say_reply: Callable[[str], object]) -> bool:
    request = parse_owner_avatar_request(text)
    if request is None:
        return False
    return _respond_to_request(request, say_reply)


def _requests_unavailable_avatar(text: str) -> bool:
    """Recognize clear requests without a model when the gateway is stopped.

    A topic match alone says nothing about intent. Uncertain/quoted discussion
    belongs to normal chat; the running gateway keeps its semantic selector.
    This predicate only permits an availability reply, never a dispatch.
    """
    raw = str(text or '').strip()
    if any(c in raw for c in ('`', '"', '\u201c', '\u201d')):
        return False
    folded = ''.join(c for c in unicodedata.normalize('NFD', raw) if unicodedata.category(c) != 'Mn')
    folded = re.sub(r'\s+', ' ', folded.casefold().replace('\u0111', 'd'))
    if re.search(
        r'\b(neu|gia su|vi du|gia vo|noi rang|noi la|ke ve|giai thich|dich|'
        r'co nen|co nghia|la gi|toi thay|nguoi ta|ho bao|ban ve|thao luan|mo ta)\b',
        folded,
    ):
        return False
    return bool(re.search(
        r'^(?:su dung|tha|kich hoat|thuc hien|dieu khien)\b|'
        r'\b(?:con|em|nana|na na|yumi|nayumi(?: liora)?)\s+(?:oi\s+)?'
        r'(?:co (?:muon|the|quyen)|duoc phep|biet cach)\b|'
        r'\b(?:quyen|cho phep|kha nang)\s+(?:gui|su dung|dieu khien|tha|phat)\b',
        folded,
    ))


async def handle_owner_avatar_turn(text: str, say_reply: Callable[[str], object]) -> bool:
    request = parse_owner_avatar_request(text)
    if request is not None:
        return _respond_to_request(request, say_reply)
    from nana.runtime.avatar_action_selector import AvatarSelection, is_avatar_topic, select_avatar_action
    if not is_avatar_topic(text):
        return False
    from nana.runtime.avatar_intent_gateway import get_avatar_intent_gateway
    gateway = get_avatar_intent_gateway()
    if not gateway.enabled or not gateway.running:
        if not _requests_unavailable_avatar(text):
            return False
        return _respond_to_request(OwnerAvatarRequest(reason='capability'), say_reply)
    try:
        selection = await asyncio.wait_for(asyncio.to_thread(select_avatar_action, text), timeout=10)
    except asyncio.TimeoutError:
        selection = AvatarSelection('clarify', reason='semantic_timeout')
    except Exception:
        selection = AvatarSelection('clarify', reason='semantic_unavailable')
    print(f'[Avatar] route=semantic intent={selection.intent} action={selection.action or "none"} reason={selection.reason}', flush=True)
    if selection.intent == 'discuss':
        return False
    if selection.intent == 'perform':
        request = OwnerAvatarRequest(selection.action, 'semantic_owner_request')
    elif selection.intent == 'capability':
        request = OwnerAvatarRequest(reason='capability')
    elif selection.intent == 'no_action':
        request = OwnerAvatarRequest(reason='semantic_no_action')
    elif selection.reason != 'semantic_selected':
        request = OwnerAvatarRequest(reason='selection_unavailable')
    else:
        request = OwnerAvatarRequest(reason='name_one_action')
    return _respond_to_request(request, say_reply)


def _respond_to_request(request: OwnerAvatarRequest, say_reply: Callable[[str], object]) -> bool:
    if request.reason == 'capability':
        from nana.runtime.avatar_intent_gateway import get_avatar_intent_gateway
        gateway = get_avatar_intent_gateway()
        ready = gateway.enabled and gateway.running
        reply = ('C\u00f3 Ba, con c\u00f3 \u0111\u01b0\u1eddng g\u1eedi bi\u1ec3u c\u1ea3m v\u00e0 \u0111\u1ed9ng t\u00e1c. Ba ch\u1ecdn m\u1ed9t c\u00e1i, con s\u1ebd g\u1eedi v\u00e0 ki\u1ec3m tra k\u1ebft qu\u1ea3 nha.'
                 if ready else 'Con c\u00f3 ch\u1ee9c n\u0103ng g\u1eedi \u0111\u1ed9ng t\u00e1c, nh\u01b0ng \u0111\u01b0\u1eddng k\u1ebft n\u1ed1i \u0111ang t\u1eaft. Con ch\u01b0a g\u1eedi l\u1ec7nh n\u00e0o nha Ba.')
        detail = f'not_sent reason=capability ready={ready}'
    elif request.reason == 'selection_unavailable':
        reply = 'Con ch\u01b0a ch\u1ecdn \u0111\u01b0\u1ee3c l\u1ec7nh cho c\u00e2u n\u00e0y, n\u00ean ch\u01b0a g\u1eedi g\u00ec. Ba th\u1eed n\u00f3i t\u00ean \u0111\u1ed9ng t\u00e1c nha.'
        detail = 'not_sent reason=selection_unavailable'
    elif request.reason == 'semantic_no_action':
        reply = 'Con kh\u00f4ng g\u1eedi \u0111\u1ed9ng t\u00e1c cho y\u00eau c\u1ea7u n\u00e0y nha Ba. Con v\u1eabn gi\u1eef c\u00e1c gi\u1edbi h\u1ea1n Ba \u0111\u00e3 ch\u1ecdn.'
        detail = 'not_sent reason=semantic_no_action'
    elif request.reason == 'negated_owner_request':
        reply = 'Con kh\u00f4ng g\u1eedi th\u00eam \u0111\u1ed9ng t\u00e1c n\u00e0y nha Ba.'
        detail = 'not_sent reason=negated_owner_request'
    elif request.reason == 'owner_disabled_action':
        reply = 'Ba \u0111\u00e3 kh\u00f3a \u0111\u1ed9ng t\u00e1c n\u00e0y r\u1ed3i, con kh\u00f4ng g\u1eedi nha.'
        detail = f'action={request.action} not_sent reason=owner_disabled_action'
    elif request.reason == 'camera_is_human_owned':
        reply = 'G\u00f3c camera v\u1eabn do Ba ch\u1ec9nh nha, con kh\u00f4ng g\u1eedi l\u1ec7nh camera.'
        detail = 'not_sent reason=camera_is_human_owned'
    elif request.reason == 'name_one_action':
        reply = 'Ba ch\u1ecdn m\u1ed9t c\u00e1i nha: g\u1eadt \u0111\u1ea7u, nh\u00e1y m\u1eaft hay nghi\u00eang t\u00f2 m\u00f2? Con ch\u01b0a g\u1eedi l\u1ec7nh n\u00e0o.'
        detail = 'not_sent reason=name_one_action'
    else:
        from nana.runtime.avatar_intent_gateway import get_avatar_intent_gateway

        assert request.action in REPLY_ACTIONS | {'settle'}
        try:
            result = get_avatar_intent_gateway().submit_action(
                request.action, source='owner_request_semantic' if request.reason == 'semantic_owner_request' else 'owner_request', channel='owner',
                priority=80, interrupt_policy='replace',
            )
            intent_id = result.intent.intent_id if result.intent else 'none'
            status = result.receipt.status if result.receipt else 'not_sent'
            detail = f'action={request.action} status={status} reason={result.reason} id={intent_id}'
            if result.ok:
                reply = 'Con \u0111\u00e3 g\u1eedi l\u1ec7nh m\u1ed9t l\u1ea7n, \u0111ang ch\u1edd model th\u1ef1c hi\u1ec7n nha Ba.'
            else:
                reply = 'L\u1ec7nh ch\u01b0a \u0111\u01b0\u1ee3c nh\u1eadn, n\u00ean con ch\u01b0a x\u00e1c nh\u1eadn model l\u00e0m \u0111\u01b0\u1ee3c nha Ba.'
        except Exception as exc:
            detail = f'action={request.action} not_sent reason=unavailable:{type(exc).__name__}'
            reply = 'Con ch\u01b0a k\u1ebft n\u1ed1i \u0111\u01b0\u1ee3c v\u1edbi model, l\u1ec7nh ch\u01b0a g\u1eedi nha Ba.'
    print(f'[Avatar] {detail}', flush=True)
    print(f'Nana: {reply}', flush=True)
    # No reply-expression hook: an acknowledgement must not replace its own action.
    say_reply(reply)
    return True
