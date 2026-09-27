"""Chat surface helpers for the new Nana runtime."""
from __future__ import annotations

import random
import re

from nana.brain.gpt import finalize_reply, fix_pronoun, shape_chat_reply, vary_text
from nana.memory import add_jealousy
from nana.phases.commons import CASUAL_POOLS
from nana.runtime.awareness_memory import get_awareness_memory
from nana.runtime.live_awareness import repair_awareness_reply, lock_focus
from nana.runtime.recovery import recovery_message


_casual_last_reply = ""


def finalize_live_reply(reply, user_text, story_mode=False, casual_mode=False, awareness=None):
    reply = fix_pronoun(reply or "", story_mode)
    reply = finalize_reply(reply, casual_mode=casual_mode)
    reply = repair_awareness_reply(reply, awareness=awareness, user_text=user_text)
    reply = vary_text(reply)
    reply = add_jealousy(reply)
    reply = shape_chat_reply(reply, user_text=user_text, casual_mode=casual_mode)
    reply = repair_awareness_reply(reply, awareness=awareness, user_text=user_text)
    return reply


def is_casual_ping(text_lower):
    lowered = (text_lower or "").strip().lower()
    word_count = len(lowered.split())
    if word_count > 4:
        return False
    if any(marker in lowered for marker in ["?", "!", "kể", "ke", "nói", "noi", "làm", "lam"]):
        return False
    if any(marker in lowered for marker in ["nhé", "nhe", "nha", "đi", "di", "giúp", "giup", "cho", "với", "voi"]):
        return False
    simple_markers = [
        "ok nana",
        "ok na na",
        "có gì hay",
        "co gi hay",
        "gì hay",
        "gi hay",
        "có gì vui",
        "co gi vui",
        "thấy ko ổn",
        "thấy không ổn",
        "ko ổn",
        "không ổn",
        "haha",
        "hehe",
        "hihi",
        "alo",
        "nghe ba nói gì không",
        "nghe bà nói gì không",
    ]
    if any(marker in lowered for marker in simple_markers):
        return True
    tokens = re.findall(r"\w+", lowered)
    if {"dạ", "nè", "ơi"} & set(tokens) and word_count <= 2:
        return True
    return lowered in {"ok", "oke", "oke nana", "nana haha", "dạ", "nè", "ơi"}


def is_story_request(text_lower):
    lowered = (text_lower or "").strip().lower()
    if not lowered:
        return False
    direct_markers = [
        "fortuna",
        "story",
        "kể chuyện",
        "ke chuyen",
        "câu chuyện",
        "cau chuyen",
        "chuyện ngáo",
        "chuyen ngao",
        "chuyện thật",
        "chuyen that",
    ]
    if any(marker in lowered for marker in direct_markers):
        return True
    for tell in ("kể", "ke"):
        tell_index = lowered.find(tell)
        if tell_index < 0:
            continue
        for story in ("chuyện", "chuyen"):
            story_index = lowered.find(story, tell_index)
            if 0 <= story_index - tell_index <= 64:
                return True
    return False


def classify_casual_ping(text_lower):
    lowered = (text_lower or "").strip().lower()
    if any(marker in lowered for marker in ["thấy ko ổn", "thấy không ổn", "ko ổn", "không ổn"]):
        return "feedback_ping"
    if any(marker in lowered for marker in ["có gì hay", "co gi hay", "gì hay", "gi hay", "có gì vui", "co gi vui"]):
        return "open_ping"
    if "alo" in lowered:
        return "call_ping"
    return None


def build_casual_ping_reply(text_lower):
    return _pick_casual_line(classify_casual_ping(text_lower))


def awareness_memory_note_user_chat(user_text: str, nana_text: str, awareness: dict | None = None):
    try:
        mem = get_awareness_memory()
        moment = mem.record_chat(
            user_text=user_text,
            nana_text=nana_text,
            awareness=awareness,
            importance="medium",
        )
        if moment is not None and awareness is not None:
            try:
                lock_focus(awareness, reason="awareness_record")
            except Exception:
                pass
    except Exception:
        pass


def recovery_notice(key, detail=None, cooldown=True):
    return recovery_message(key, detail=detail, cooldown=cooldown)


def _pick_casual_line(pool_name):
    global _casual_last_reply
    lines = CASUAL_POOLS.get(pool_name) or CASUAL_POOLS["soft_ping"]
    candidates = [line for line in lines if line != _casual_last_reply] or lines
    reply = random.choice(candidates)
    _casual_last_reply = reply
    return reply
