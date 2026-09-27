"""nana.social.teo_lite — Teo-lite mode for social drafts."""
from nana.social.classifier import detect_public_reaction_style, strip_accents_for_match, source_match_bundle


def should_use_teo_lite(raw_text, broker_context=None, intent="social.draft"):
    if intent not in {"social.reply", "social.post", "social.draft"}:
        return False
    if ((broker_context or {}).get("browser_kind") or "").lower() != "social":
        return False
    lowered = (raw_text or "").lower()
    normalized = strip_accents_for_match(lowered)
    request_match = f"{lowered} {normalized}"
    explicit = any(marker in request_match for marker in [
        "teo-lite", "teo lite", "kiểu tèo", "kieu teo",
        "cà khịa", "ca khia", "mỉa nhẹ", "mia nhe",
        "combat nhẹ", "combat nhe",
    ])
    if explicit:
        return True
    source = " | ".join(
        str(part)
        for part in [
            (broker_context or {}).get("browser_title"),
            (broker_context or {}).get("browser_social_post_text"),
            (broker_context or {}).get("browser_social_vibe"),
        ]
        if part
    )
    return detect_public_reaction_style(source) in {"absurd_work"}


def apply_teo_lite_public_reply(text, source_text="", intent="social.draft"):
    if intent not in {"social.reply", "social.post", "social.draft"}:
        return text
    from nana.social.quality import sanitize_teo_lite_reply, social_draft_needs_fallback
    from nana.social.guards import fallback_public_social_reply
    style = detect_public_reaction_style(source_text)
    candidate = sanitize_teo_lite_reply(text, source_text=source_text)
    lowered = candidate.lower()
    normalized = strip_accents_for_match(lowered)
    match_text = f"{lowered} {normalized}"
    if style == "absurd_work":
        if any(marker in match_text for marker in ["cần thưởng", "can thuong", "tinh thần", "tinh than"]):
            return "Lương chắc cao lắm."
        return candidate
    if style == "animal_standoff":
        if "thắng" in match_text or "thang" in match_text:
            return candidate
        return "Ủa phe nào thắng vậy?"
    if style == "danger":
        if any(marker in match_text for marker in ["nhanh", "né", "ne"]):
            return candidate
        return "May mà né kịp."
    return sanitize_teo_lite_reply(candidate, source_text=source_text)
