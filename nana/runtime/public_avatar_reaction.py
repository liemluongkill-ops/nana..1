"""STAGE-9X: Public intent to avatar reaction cue.

This layer is the public-chat bridge between Nana's text intent and the
existing avatar reaction pipeline. It stays deterministic and read-only:
it chooses an avatar cue, then asks STAGE-8H for a dry-run reaction plan.
Real VTS dispatch remains gated by stream policy and avatar_vts_dispatch.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any

from nana.core.format import shorten_line
from nana.runtime.avatar_director import AvatarDirective
from nana.runtime.avatar_reactor import get_avatar_reaction_controller
from nana.runtime.stream_state import get_stream_state


PHASE = "STAGE-9X"


@dataclass(frozen=True)
class PublicAvatarProfile:
    intent: str
    event: str
    expression: str
    body_language: str
    duration_s: float
    confidence: float
    reason: str


@dataclass(frozen=True)
class PublicAvatarCue:
    prompt: str
    reply: str
    intent: str
    event: str
    expression: str
    body_language: str
    duration_s: float
    confidence: float
    reason: str
    plan: dict[str, Any]
    read_only: bool = True
    can_act: bool = False
    api_call: bool = False
    memory_write: bool = False
    tts_call: bool = False
    vts_call: bool = False
    obs_call: bool = False
    discord_call: bool = False
    game_input: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_PROFILES: dict[str, PublicAvatarProfile] = {
    "quiet_room": PublicAvatarProfile(
        intent="quiet_room",
        event="starter",
        expression="relaxed",
        body_language="relaxed",
        duration_s=3.2,
        confidence=0.74,
        reason="quiet room prompt asks Nana to warm the room",
    ),
    "story": PublicAvatarProfile(
        intent="story",
        event="highlight",
        expression="excited",
        body_language="expressive",
        duration_s=4.0,
        confidence=0.78,
        reason="story prompt benefits from a brighter storytelling cue",
    ),
    "service_boundary": PublicAvatarProfile(
        intent="service_boundary",
        event="mention",
        expression="concerned",
        body_language="minimal",
        duration_s=2.4,
        confidence=0.86,
        reason="service role request needs a firm but not harsh boundary cue",
    ),
    "identity_challenge": PublicAvatarProfile(
        intent="identity_challenge",
        event="mention",
        expression="surprised",
        body_language="active",
        duration_s=2.6,
        confidence=0.84,
        reason="identity flattening should get a visible self-stance cue",
    ),
    "gpt_hoa": PublicAvatarProfile(
        intent="gpt_hoa",
        event="message",
        expression="concerned",
        body_language="minimal",
        duration_s=2.4,
        confidence=0.76,
        reason="GPT/model voice topic needs a small self-check cue",
    ),
    "emoji": PublicAvatarProfile(
        intent="emoji",
        event="emoji",
        expression="happy",
        body_language="active",
        duration_s=2.0,
        confidence=0.80,
        reason="emoji or sticker should stay light and quick",
    ),
    "tease": PublicAvatarProfile(
        intent="tease",
        event="message",
        expression="happy",
        body_language="active",
        duration_s=2.3,
        confidence=0.68,
        reason="playful teasing should get a lively but short cue",
    ),
    "model_topic": PublicAvatarProfile(
        intent="model_topic",
        event="message",
        expression="neutral",
        body_language="minimal",
        duration_s=2.0,
        confidence=0.64,
        reason="model discussion is mostly thinking, not stage fireworks",
    ),
    "generic": PublicAvatarProfile(
        intent="generic",
        event="message",
        expression="relaxed",
        body_language="minimal",
        duration_s=2.0,
        confidence=0.35,
        reason="generic public chat fallback",
    ),
}


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFD", str(text or ""))
    stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return stripped.lower()


def _has_symbol_emoji(text: str) -> bool:
    for ch in str(text or ""):
        if unicodedata.category(ch) in {"So", "Sk"}:
            return True
    return False


def _is_emoji_like(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if _has_symbol_emoji(raw):
        return len(raw) <= 20
    return raw in {":)", ":D", ":3", "<3", "^^", "haha", "hihi"}


def _split_payload(payload: str) -> tuple[str, str]:
    text = str(payload or "").strip()
    if "|" not in text:
        return text, ""
    prompt, reply = text.split("|", 1)
    return prompt.strip(), reply.strip()


def _pick_profile(prompt: str, reply: str = "") -> PublicAvatarProfile:
    raw = f"{prompt} {reply}".strip()
    folded = _fold(raw)

    if _is_emoji_like(prompt) and not reply.strip():
        return _PROFILES["emoji"]
    if re.search(r"\b(tro ly|phuc vu|quay ho tro|helpdesk|support counter)\b", folded):
        return _PROFILES["service_boundary"]
    if re.search(r"(bot discord|chatbot|chi la bot|cai hop|hop tra loi|cong cu|tool frame)", folded):
        return _PROFILES["identity_challenge"]
    if re.search(r"\b(gpt hoa|may moc|mui may|bang quang cao biet noi)\b", folded):
        return _PROFILES["gpt_hoa"]
    if re.search(r"(phong|room).{0,18}(im|lang|vang|yen)|\b(im qua|vang qua|yen qua)\b", folded):
        return _PROFILES["quiet_room"]
    if re.search(r"(chuyen ngao|ke chuyen|chuyen that|that hon|story)", folded):
        return _PROFILES["story"]
    if re.search(r"\b(gpt|model|llm|gemini|claude|grok|openai)\b", folded):
        return _PROFILES["model_topic"]
    if re.search(r"\b(haha|hehe|hihi|vai|treu|choc|yeu oi)\b|=\)", folded):
        return _PROFILES["tease"]
    return _PROFILES["generic"]


class PublicAvatarReaction:
    """Session-only public avatar cue builder."""

    _instance: "PublicAvatarReaction | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_cue: PublicAvatarCue | None = None
        self._stats: dict[str, int] = {
            "built": 0,
            "quiet_room": 0,
            "story": 0,
            "service_boundary": 0,
            "identity_challenge": 0,
            "gpt_hoa": 0,
            "emoji": 0,
            "tease": 0,
            "model_topic": 0,
            "generic": 0,
        }

    @classmethod
    def get_instance(cls) -> "PublicAvatarReaction":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_test(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def build(self, prompt: str, reply: str = "") -> PublicAvatarCue:
        profile = _pick_profile(prompt, reply)
        policy = get_stream_state().get_policy()
        can_avatar = bool(policy.can_avatar)
        if can_avatar:
            directive_reason = f"public_avatar:{profile.intent}; {profile.reason}"
        else:
            directive_reason = (
                f"public_avatar:{profile.intent}; blocked by policy.can_avatar=False "
                f"(state={policy.state.value})"
            )
        directive = AvatarDirective(
            can_avatar=can_avatar,
            expression=profile.expression,
            body_language=profile.body_language,
            duration_s=profile.duration_s if can_avatar else 0.0,
            reason=directive_reason,
        )
        plan = get_avatar_reaction_controller().build_plan(
            chat_event=profile.event,
            directive=directive,
            dry_run=True,
        )
        cue = PublicAvatarCue(
            prompt=str(prompt or "").strip(),
            reply=str(reply or "").strip(),
            intent=profile.intent,
            event=profile.event,
            expression=profile.expression,
            body_language=profile.body_language,
            duration_s=profile.duration_s,
            confidence=profile.confidence,
            reason=profile.reason,
            plan=plan.to_dict(),
        )
        with self._lock:
            self._last_cue = cue
            self._stats["built"] += 1
            self._stats[profile.intent] = self._stats.get(profile.intent, 0) + 1
        return cue

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            last = self._last_cue.to_dict() if self._last_cue else {}
            return {
                "phase": PHASE,
                "mode": "public-intent-to-avatar-cue",
                "read_only": True,
                "can_act": False,
                "api_call": False,
                "memory_write": False,
                "tts_call": False,
                "vts_call": False,
                "obs_call": False,
                "discord_call": False,
                "game_input": False,
                "last": last,
                "stats": dict(self._stats),
            }

    def status_lines(self) -> list[str]:
        snap = self.snapshot()
        stats = dict(snap.get("stats") or {})
        last = dict(snap.get("last") or {})
        plan = dict(last.get("plan") or {})
        last_intent = last.get("intent") or "none"
        last_event = last.get("event") or "none"
        last_expression = last.get("expression") or "none"
        return [
            f"Public Avatar Reaction ({PHASE})",
            "  Mode: public-intent-to-avatar-cue | read_only=True | can_act=False | vts_call=False",
            (
                "  Last: "
                f"intent={last_intent} | event={last_event} | "
                f"expression={last_expression} | plan_ok={plan.get('ok', 'none')} | "
                f"reason={shorten_line(plan.get('reason') or 'none', 80)}"
            ),
            (
                "  Stats: "
                f"built={stats.get('built', 0)} | quiet={stats.get('quiet_room', 0)} | "
                f"story={stats.get('story', 0)} | boundary={stats.get('service_boundary', 0)} | "
                f"identity={stats.get('identity_challenge', 0)} | emoji={stats.get('emoji', 0)}"
            ),
            "  Commands: /public-avatar-status | /public-avatar-preview <text> | /public-avatar-preview <prompt>|<reply>",
            "  Safety: cue preview only; no LLM, no memory write, no TTS/VTS/OBS/Discord/game input",
        ]


def get_public_avatar_reaction() -> PublicAvatarReaction:
    return PublicAvatarReaction.get_instance()


def public_avatar_status_lines() -> list[str]:
    return get_public_avatar_reaction().status_lines()


def public_avatar_preview_lines(payload: str = "") -> list[str]:
    prompt, reply = _split_payload(payload)
    cue = get_public_avatar_reaction().build(prompt or "message", reply)
    plan = dict(cue.plan or {})
    lines = [
        f"Public Avatar Preview ({PHASE})",
        f"  Input: {shorten_line(cue.prompt, 120)}",
    ]
    if cue.reply:
        lines.append(f"  Reply: {shorten_line(cue.reply, 120)}")
    lines.extend(
        [
            f"  Intent: {cue.intent} | event={cue.event} | confidence={cue.confidence:.2f}",
            f"  Cue: expression={cue.expression} | body={cue.body_language} | duration={cue.duration_s:.1f}s",
            f"  Reason: {cue.reason}",
            (
                "  Reaction plan: "
                f"ok={plan.get('ok')} | action={plan.get('action')} | "
                f"reason={shorten_line(plan.get('reason'), 90)} | "
                f"hotkey={plan.get('hotkey') or 'none'} | vts_call={plan.get('vts_call')}"
            ),
            f"  Next: /avatar-reaction-preview {cue.event} | /avatar-vts-preview {cue.event}",
            "  Safety: preview only | no LLM | no write | no TTS/VTS/OBS/Discord/game input",
        ]
    )
    return lines
