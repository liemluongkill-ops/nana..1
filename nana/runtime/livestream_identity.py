"""Stage naming for explicitly identified livestreams, never the shared public lane."""

from __future__ import annotations

import re


STAGE_NAME = "Nayumi Liora"
SHORT_NAME = "Yumi"
CHANNEL_NAME = "Nayumi Liora | Yumi\u2019s Little World"
SELF_PRONOUN = "m\u00ecnh"
ALIASES = (STAGE_NAME, "Nayumi", "Liora", SHORT_NAME, "NayumiLiora")
_LIVE_SOURCES = frozenset({"youtube", "youtube_live_chat", "twitch", "twitch_live_chat", "livestream"})
_ALIAS = "|".join(re.escape(name).replace(r"\ ", r"\s+") for name in ALIASES)
_CALL = re.compile(rf"(?<!\w)(?:{_ALIAS})(?!\w)", re.IGNORECASE)
_BARE_CALL = re.compile(rf"^\s*@?(?:{_ALIAS})(?:\s+\u01a1i)?[\s!?.~,]*$", re.IGNORECASE)
_PROTECTED = re.compile(r'```[\s\S]*?```|`[^`]*`|https?://\S+|@[\w.]+|"[^"\n]*"|\u201c[^\u201d\n]*\u201d|\u2018[^\u2019\n]*\u2019')


def is_livestream_source(source: str | None) -> bool:
    # The existing stream_mode flag and autonomy's stream_host label also mean
    # private/Discord work. Neither is proof that the output is on a livestream.
    return str(source or "").strip().casefold() in _LIVE_SOURCES


def mentions_stage_name(text: str | None) -> bool:
    return bool(_CALL.search(str(text or "")))


def is_stage_call(text: str | None) -> bool:
    return bool(_BARE_CALL.fullmatch(str(text or "")))


def stage_prompt_block() -> str:
    return (
        "LIVESTREAM NAME (this output destination only):\n"
        f"- Your on-air name is {STAGE_NAME}; nickname {SHORT_NAME}.\n"
        f"- Channel: {CHANNEL_NAME}.\n"
        f"- Viewer aliases that address you: {', '.join(ALIASES)} (case-insensitive).\n"
        "- Use Vietnamese first-person 'm\u00ecnh' in normal speech, not your name, 'em', or 'con'.\n"
        f"- When asked your name, say 'M\u00ecnh l\u00e0 {STAGE_NAME}, c\u1ee9 g\u1ecdi m\u00ecnh l\u00e0 {SHORT_NAME} nha.'\n"
        "- Nana in other system context is an internal identity label, NOT your on-air name.\n"
        "- Keep the same personality and public privacy rules; do not disclose private identity/history.\n"
        "- Do not rename viewers, quoted names, works, account handles or URLs.\n"
        "- This rule does not change private-owner, Discord, or non-livestream social identity."
    )


def stage_identity_answer(text: str | None) -> str | None:
    raw = str(text or "").strip()
    if is_stage_call(raw):
        return "M\u00ecnh \u0111\u00e2y, m\u00ecnh \u0111ang nghe n\u00e8."
    if re.fullmatch(rf"(?:(?:b\u1ea1n|em|c\u1eadu|{_ALIAS})\s+)?(?:t\u00ean\s+(?:l\u00e0\s+)?g\u00ec|l\u00e0\s+ai)[\s?!.,]*", raw, re.IGNORECASE):
        return f"M\u00ecnh l\u00e0 {STAGE_NAME}, c\u1ee9 g\u1ecdi m\u00ecnh l\u00e0 {SHORT_NAME} nha."
    return None


def finalize_livestream_identity(text: str | None, *, source: str | None, viewer_name: str | None = None) -> str:
    """Normalize assistant self-reference after all shared public rewrites.

    Protect quoted/external names and an addressed viewer with the same name.
    This is not a universal Vietnamese coreference solver; prompts remain the
    primary control for pronouns. Quoted names and common third-party name
    phrases are preserved; arbitrary unquoted coreference still needs review.
    """
    raw = str(text or "")
    if not is_livestream_source(source) or not raw:
        return raw
    protected = {}

    def shield(match):
        token = f"\x00{len(protected)}\x00"
        protected[token] = match.group(0)
        return token

    body = _PROTECTED.sub(shield, raw)
    body = re.sub(r"(?:b\u00e0i\s+h\u00e1t|b\u1ed9\s+phim|truy\u1ec7n|nh\u00e2n\s+v\u1eadt|ca\s+s\u0129|b\u1ea1n)\s+Nana\b", shield, body, flags=re.IGNORECASE)
    viewer = str(viewer_name or "").strip()
    if viewer:
        # Vocative/reference to an identically named viewer must not become "minh".
        body = re.sub(rf"(?<!\w)(?:ch\u00e0o\s+|b\u1ea1n\s+|viewer\s+|@){re.escape(viewer)}(?!\w)|(?<!\w){re.escape(viewer)}\s+\u01a1i\b", shield, body, flags=re.IGNORECASE)
    body = re.sub(r"(^|[.!?]\s+)(?:em|t\u00f4i)\s+(?=(?:l\u00e0|t\u00ean)\s+)", lambda m: m[1] + "M\u00ecnh ", body, flags=re.IGNORECASE)
    body = re.sub(r"(?<!\w)Nana\s+l\u00e0\s+Nana(?!\w)", f"M\u00ecnh l\u00e0 {STAGE_NAME}", body, flags=re.IGNORECASE)
    body = re.sub(r"((?:t\u00ean\s+(?:c\u1ee7a\s+)?m\u00ecnh|m\u00ecnh)\s+(?:l\u00e0|t\u00ean(?:\s+l\u00e0)?)\s+)Nana(?!\w)", lambda m: m[1] + STAGE_NAME, body, flags=re.IGNORECASE)
    # Keep declared names in introductions; replace only name-as-pronoun uses.
    body = re.sub(rf"(?<!\w)(?:g\u1ecdi\s+m\u00ecnh\s+l\u00e0|m\u00ecnh\s+l\u00e0|m\u00ecnh\s+t\u00ean(?:\s+l\u00e0)?)\s+(?:{_ALIAS})(?!\w)", shield, body, flags=re.IGNORECASE)
    verbs = r"(?:l\u00e0|th\u1ea5y|nghe|\u0111\u00e2y|ch\u00e0o|\u0111ang|s\u1ebd|v\u1eeba|v\u1eabn|\u0111\u00e3|c\u0169ng|kh\u00f4ng|ch\u01b0a|c\u00f3|mu\u1ed1n|th\u00edch|bi\u1ebft|ngh\u0129|k\u1ec3|\u0111\u1ecdc|hi\u1ec3u|xin|c\u1ea3m|t\u00f2|nh\u1eadn|b\u1eaft|nh\u1edb|h\u1ecfi|ch\u1ecdi|n\u00f3i|\u0111\u1ee9ng|g\u00f5|\u0111\u1ec3|gi\u1eef|ch\u1ea5m|b\u1edbt|theo)\b"
    body = re.sub(rf"(?<![\w@])(?:Nana|{_ALIAS})(?!\w)(?=\s+{verbs})", SELF_PRONOUN, body, flags=re.IGNORECASE)
    body = re.sub(r"\b(?:ph\u00f2ng|th\u1ebf gi\u1edbi|s\u00e2n kh\u1ea5u)(?:\s+c\u1ee7a)?\s+Nana\b", lambda m: re.sub(r"Nana$", SELF_PRONOUN, m[0], flags=re.IGNORECASE), body, flags=re.IGNORECASE)
    body = re.sub(r"(?<![\w@])Nana(?!\w)", SELF_PRONOUN, body, flags=re.IGNORECASE)
    body = re.sub(rf"(^|[.!?]\s+)(?:em|t\u00f4i)\s+(?={verbs})", lambda m: m[1] + "M\u00ecnh ", body, flags=re.IGNORECASE)
    body = re.sub(r"([.!?]\s+)m\u00ecnh\b", lambda m: m[1] + "M\u00ecnh", body)
    for token, value in reversed(list(protected.items())):
        body = body.replace(token, value)
    return body[:1].upper() + body[1:]
