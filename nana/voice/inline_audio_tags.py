"""Curated public ElevenLabs inline audio-tag adapter for Nana voice."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Any

from nana.config import ELEVEN_PUBLIC_TTS_MODEL


PHASE = "CORE-VOICE-INLINE-AUDIO-TAGS-1"


def _env_enabled(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "off", "no"}


def _bounded_env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = int(default)
    return max(low, min(high, value))


INLINE_AUDIO_TAGS_ENABLED = _env_enabled("NANA_VOICE_INLINE_AUDIO_TAGS_ENABLED", "1")
INLINE_AUDIO_TAG_MAX = _bounded_env_int("NANA_VOICE_INLINE_AUDIO_TAG_MAX", 5, 1, 12)

VOICE_DIRECTION_AUDIO_TAGS = (
    "happy",
    "sad",
    "excited",
    "angry",
    "annoyed",
    "appalled",
    "thoughtful",
    "surprised",
    "curious",
    "sarcastic",
    "whisper",
    "confused",
    "nervous",
    "confidently",
    "crying",
    "mischievously",
    "warmly",
    "frustrated",
    "dismissive",
    "cute",
    "professional",
    "sympathetic",
    "questioning",
    "reassuring",
    "impressed",
    "delighted",
    "amazed",
    "alarmed",
    "sheepishly",
    "desperately",
    "deadpan",
    "cautiously",
    "dramatically",
    "panicking",
    "muttering",
    "energetic",
    "relaxed",
    "casual",
)

PACING_AUDIO_TAGS = (
    "sighs",
    "exhales",
    "short pause",
    "long pause",
    "inhales deeply",
    "exhales sharply",
)

CANONICAL_AUDIO_TAGS = VOICE_DIRECTION_AUDIO_TAGS + PACING_AUDIO_TAGS

AUDIO_TAG_ALIASES = {
    "thoughtfully": "thoughtful",
    "curiously": "curious",
    "whispers": "whisper",
    "whispering": "whisper",
    "nervously": "nervous",
    "sigh": "sighs",
    "sighing": "sighs",
    "frustrated sigh": "sighs",
    "excitedly": "excited",
    "disappointed": "sad",
    "playfully": "mischievously",
    "warm": "warmly",
    "dramatic": "dramatically",
    "cautious": "cautiously",
    "sheepish": "sheepishly",
    "desperate": "desperately",
    "panicked": "panicking",
    "mutter": "muttering",
    "mutters": "muttering",
    "professionally": "professional",
    "sympathetically": "sympathetic",
    "reassuringly": "reassuring",
    "questioningly": "questioning",
    "dismissively": "dismissive",
    "energetically": "energetic",
    "casually": "casual",
    "pause": "short pause",
    "pauses": "short pause",
    "inhaling deeply": "inhales deeply",
    "exhaling sharply": "exhales sharply",
}

BLOCKED_AUDIO_TAGS = frozenset(
    {
        "laugh",
        "laughs",
        "laughing",
        "chuckle",
        "chuckles",
        "softly",
        "clear throat",
        "clears throat",
        "throat",
        "cough",
        "coughs",
        "ahem",
        "ehem",
        "khụ",
        "khạc",
        "khặc",
        "hắng",
        "gunshot",
        "applause",
        "clapping",
        "explosion",
        "swallows",
        "gulps",
        "music",
        "standing",
        "grinning",
        "pacing",
        "movement",
        "visual action",
        "sings",
        "singing",
        "singing quickly",
        "woo",
        "fart",
        "robotic voice",
        "binary beeping",
        "starting to speak",
        "jumping in",
        "overlapping",
        "interrupting",
        "interrupting then stopping abruptly",
        "pause then normally",
    }
)

BLOCKED_AUDIO_TAG_PREFIXES = (
    "laugh",
    "chuckle",
    "giggl",
    "wheez",
    "snort",
    "cough",
    "clear throat",
    "clears throat",
    "shout",
    "sing",
    "strong ",
)

_CANONICAL_SET = frozenset(CANONICAL_AUDIO_TAGS)
INLINE_AUDIO_TAG_RE = re.compile(r"\[([^\W\d_][^\[\]\r\n]{0,39})\]", re.IGNORECASE)
_KNOWN_TAG_NAMES = tuple(
    sorted(
        _CANONICAL_SET | frozenset(AUDIO_TAG_ALIASES) | BLOCKED_AUDIO_TAGS,
        key=lambda value: (-len(value), value),
    )
)
_KNOWN_TAG_ALTERNATION = "|".join(re.escape(value) for value in _KNOWN_TAG_NAMES)
KNOWN_INLINE_AUDIO_TAG_RE = re.compile(
    rf"\[({_KNOWN_TAG_ALTERNATION})\]",
    re.IGNORECASE,
)
_MALFORMED_INLINE_AUDIO_TAG_RE = re.compile(
    rf"\[({_KNOWN_TAG_ALTERNATION})(?!\])(?=\s|(?-i:[A-ZÀ-Ỹ]))",
    re.IGNORECASE,
)


ELEVEN_V3_INLINE_AUDIO_TAG_GUIDE = """
Private voice delivery uses Eleven v3 inline audio tags.
- Tags are silent delivery directions; never explain or read them as content.
- Use only these curated voice directions when they genuinely improve a span:
  [thoughtful], [curious], [warmly], [sympathetic], [reassuring],
  [questioning], [professional], [confidently], [casual], [relaxed],
  [happy], [excited], [impressed], [delighted], [amazed], [surprised],
  [cute], [energetic], [mischievously], [sarcastic], [deadpan],
  [annoyed], [frustrated], [dismissive], [angry], [appalled],
  [sad], [crying], [nervous], [alarmed], [sheepishly], [desperately],
  [panicking], [cautiously], [dramatically], [muttering], [whisper].
- For real breath or pacing changes, use only [sighs], [exhales],
  [inhales deeply], [exhales sharply], [short pause], or [long pause].
- Put a tag immediately before the words or span it modifies.
- Short/simple replies usually need no tag. Normal replies use at most 1-2.
  Long replies may use 3-5, only at real emotional or pacing transitions.
- Keep one continuous reply. Tags change delivery; they do not split audio.
- Do not alter facts or add dialogue just to justify a tag.
- Do not use laughter, chuckle, generic-soft-delivery, cough, throat-clear,
  shouting, singing, accents, music, movement, visual-action, environmental
  sound effects, or multi-speaker timing tags.
""".strip()


ELEVEN_V3_INLINE_AUDIO_TAG_COMPACT_GUIDE = """
Private voice tags are silent delivery directions; never explain or read them.
- Short replies usually need no tag. Normal replies use at most 1-2.
- Use only when useful: [warmly], [thoughtful], [curious], [happy], [excited],
  [mischievously], [sarcastic], [deadpan], [sad], [nervous], [whisper],
  [sighs], [exhales], [short pause], [long pause].
- Put a tag immediately before its spoken span. Keep one continuous reply.
- Never add facts, dialogue, laughter, coughs, singing, music, or sound effects
  just to justify a tag.
""".strip()


def _tag_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _clean_spacing(text: str) -> str:
    cleaned = re.sub(r"[ \t]{2,}", " ", str(text or ""))
    cleaned = re.sub(r"[ \t]+([,.!?;:])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]*\n[ \t]*", "\n", cleaned)
    return cleaned.strip()


def _known_tag(name: str) -> str | None:
    canonical = AUDIO_TAG_ALIASES.get(name, name)
    return canonical if canonical in _CANONICAL_SET else None


def _blocked_tag(name: str) -> bool:
    if name in BLOCKED_AUDIO_TAGS:
        return True
    if " accent" in name:
        return True
    return any(name.startswith(prefix) for prefix in BLOCKED_AUDIO_TAG_PREFIXES)


def repair_malformed_inline_audio_tags(text: Any) -> str:
    """Close a known tag when a streamed model omitted only the final bracket."""

    raw = str(text or "")
    if not raw:
        return raw
    return _MALFORMED_INLINE_AUDIO_TAG_RE.sub(
        lambda match: f"[{_tag_name(match.group(1))}] ",
        raw,
    )


@dataclass(frozen=True)
class InlineAudioTagRender:
    original_text: str
    tts_text: str
    display_text: str
    tags: tuple[str, ...]
    enabled: bool
    max_tags: int
    alias_normalized_count: int
    blocked_count: int
    overflow_count: int
    disabled_count: int
    unknown_count: int

    @property
    def tag_count(self) -> int:
        return len(self.tags)

    @property
    def changed(self) -> bool:
        return self.tts_text != self.original_text


def render_inline_audio_tags(
    text: Any,
    *,
    enabled: bool | None = None,
    max_tags: int | None = None,
) -> InlineAudioTagRender:
    original = str(text or "")
    repaired = repair_malformed_inline_audio_tags(original)
    active = INLINE_AUDIO_TAGS_ENABLED if enabled is None else bool(enabled)
    limit = INLINE_AUDIO_TAG_MAX if max_tags is None else max(0, int(max_tags))
    tags: list[str] = []
    alias_normalized = 0
    blocked = 0
    overflow = 0
    disabled = 0
    unknown = 0

    def _render_tag(match: re.Match[str]) -> str:
        nonlocal alias_normalized, blocked, overflow, disabled, unknown
        raw = _tag_name(match.group(1))
        if _blocked_tag(raw):
            blocked += 1
            return " "
        canonical = _known_tag(raw)
        if canonical is None:
            unknown += 1
            return match.group(0)
        if not active:
            disabled += 1
            return " "
        if len(tags) >= limit:
            overflow += 1
            return " "
        if canonical != raw:
            alias_normalized += 1
        tags.append(canonical)
        return f"[{canonical}]"

    def _hide_tag(match: re.Match[str]) -> str:
        raw = _tag_name(match.group(1))
        if _blocked_tag(raw) or _known_tag(raw) is not None:
            return " "
        return match.group(0)

    tts_text = _clean_spacing(INLINE_AUDIO_TAG_RE.sub(_render_tag, repaired))
    display_text = _clean_spacing(INLINE_AUDIO_TAG_RE.sub(_hide_tag, repaired))
    return InlineAudioTagRender(
        original_text=original,
        tts_text=tts_text,
        display_text=display_text,
        tags=tuple(tags),
        enabled=active,
        max_tags=limit,
        alias_normalized_count=alias_normalized,
        blocked_count=blocked,
        overflow_count=overflow,
        disabled_count=disabled,
        unknown_count=unknown,
    )


def strip_inline_audio_tags(text: Any) -> str:
    return render_inline_audio_tags(text).display_text


def inline_audio_tag_status_lines() -> list[str]:
    return [
        f"Inline Audio Tags ({PHASE})",
        (
            "  Mode: provider_adapter | "
            f"enabled={INLINE_AUDIO_TAGS_ENABLED} | max_tags={INLINE_AUDIO_TAG_MAX} | "
            f"model={ELEVEN_PUBLIC_TTS_MODEL}"
        ),
        "  Flow: tagged_reply -> normalize/filter -> one utterance -> ElevenLabs",
        "  Display: terminal/chat hides recognized tags; spoken text keeps canonical tags",
        (
            f"  Catalog: voice={len(VOICE_DIRECTION_AUDIO_TAGS)} | "
            f"pacing={len(PACING_AUDIO_TAGS)} | max_per_reply={INLINE_AUDIO_TAG_MAX}"
        ),
        "  Blocked: laughter | chuckle | softly | cough/throat | shouting/singing | accents/effects",
        "  Commands: /voice-inline-tag-status | /voice-inline-tag-preview <text>",
        "  Safety: preview/status only | no LLM/TTS/VoiceEngine/API/output action",
    ]


def inline_audio_tag_preview_lines(text: Any) -> list[str]:
    result = render_inline_audio_tags(text)
    tag_text = ",".join(result.tags) if result.tags else "none"
    return [
        f"Inline Audio Tag Preview ({PHASE})",
        (
            f"  Result: enabled={result.enabled} | tags={result.tag_count}/{result.max_tags} "
            f"| aliases={result.alias_normalized_count} | blocked={result.blocked_count} "
            f"| overflow={result.overflow_count} | unknown={result.unknown_count}"
        ),
        f"  Tags: {tag_text}",
        f"  TTS text: {result.tts_text[:1600] or 'none'}",
        f"  Display text: {result.display_text[:1600] or 'none'}",
        "  Safety: preview only | no LLM | no TTS call | no VoiceEngine call | no output action",
    ]
