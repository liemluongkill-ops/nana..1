"""nana.social.quality — draft quality report, summary, print, and test."""
import time

from nana.social.classifier import (
    detect_public_reaction_style,
    build_social_draft_source,
    SENSITIVE_DRAFT_STYLES,
    source_match_bundle,
    strip_accents_for_match,
)


def draft_quality_report(item):
    from nana.core.context import broker_context_snapshot
    from nana.social.guards import (
        fallback_public_social_reply,
        compact_public_reaction_reply,
        remove_disallowed_social_draft_bits,
    )
    source_text = getattr(item, "source_text", None) or build_social_draft_source(
        item.request,
        broker_context=broker_context_snapshot(),
        vision_description=None,
    )
    style = getattr(item, "reaction_style", None) or detect_public_reaction_style(source_text) or "none"
    guard_hint = getattr(item, "guard_hint", None) or "none"
    draft = item.draft or ""
    issues = []

    def warn(code, detail):
        issues.append({"code": code, "detail": detail})

    if item.expired():
        warn("expired", "draft hết hạn")
    if is_diagnostic_fragment(draft):
        warn("diagnostic_fragment", "draft giống output runtime, không nên public")
    if social_draft_has_obvious_quality_issue(draft):
        warn("quality_typo", "phát hiện typo/ký tự lỗi rõ")
    if social_draft_needs_fallback(draft, source_text=source_text, intent=item.intent):
        warn("fallback_recommended", f"fallback={fallback_public_social_reply(source_text=source_text)}")

    compacted = compact_public_reaction_reply(draft, source_text=source_text, intent=item.intent)
    if compacted and compacted != draft:
        warn("style_guard_would_adjust", f"would_use={compacted}")

    stripped = remove_disallowed_social_draft_bits(draft, source_text=source_text)
    if stripped != draft:
        warn("action_or_noise_claim", "draft có dấu hiệu claim hành động/link/noise bị strip")

    match_text = source_match_bundle(draft)
    drift_markers = [
        "cute", "cưng", "cung", "dễ thương", "de thuong",
        "cười xỉu", "cuoi xiu", "đỉnh", "dinh",
        "hóng", "hong", "kèo này", "keo nay", "đáng đời", "dang doi",
    ]
    if style in SENSITIVE_DRAFT_STYLES and any(marker in match_text for marker in drift_markers):
        warn("toxicity_or_vibe_drift", "draft dùng vibe đùa/hóng trong context nhạy cảm")

    if item.intent in {"social.reply", "social.draft"} and len(draft.strip()) > 90:
        warn("too_long_public_reply", "reply public dài hơn budget gọn")

    status = "pass" if not issues else "warn"
    return {
        "status": status,
        "issues": issues,
        "style": style,
        "guard_hint": guard_hint,
        "source_text": source_text,
        "context_priority": getattr(item, "context_priority", None) or "unknown",
    }


def draft_quality_summary():
    from nana.actions.drafts import social_drafts
    items = social_drafts.list_pending()
    reports = [(item, draft_quality_report(item)) for item in items]
    warn_count = sum(1 for _, report in reports if report["status"] != "pass")
    return {
        "items": items,
        "reports": reports,
        "warn_count": warn_count,
        "pass_count": len(reports) - warn_count,
        "total": len(reports),
    }


def print_draft_quality(raw_id=None):
    from nana.actions.drafts import social_drafts
    from nana.core.format import shorten_line
    print("🧪 Draft Quality Gate")
    print("  Action: read-only; không confirm, không post, không type.")
    items = social_drafts.list_pending()
    if raw_id:
        draft_id = parse_int_arg(raw_id)
        if draft_id is None:
            print("  Status: invalid_id")
            return
        item = social_drafts.get(draft_id)
        items = [item] if item else []
        if not item:
            print("  Status: not_found")
            return
    if not items:
        print("  Pending: none")
        return
    warn_count = 0
    for item in items:
        report = draft_quality_report(item)
        if report["status"] != "pass":
            warn_count += 1
        print(
            "  "
            f"#{item.id} | {report['status']} | style={report['style']} | "
            f"guard={report['guard_hint']} | age={item.age_seconds():.1f}s"
        )
        print(f"    Context priority: {report['context_priority']}")
        print(f"    Draft: {shorten_line(item.draft, 120)}")
        if report["issues"]:
            for issue in report["issues"]:
                print(f"    Issue: {issue['code']} | {issue['detail']}")
        else:
            print("    Issues: none")
    print(f"  Summary: {len(items) - warn_count}/{len(items)} pass")


def parse_int_arg(raw_value):
    try:
        return int(str(raw_value).strip())
    except (TypeError, ValueError):
        return None


def parse_draft_quality_test(raw_text):
    text = str(raw_text or "").strip()
    if "||" in text:
        source, draft = text.split("||", 1)
        return source.strip(), draft.strip()
    return "", ""


def print_draft_quality_test(raw_text):
    from nana.core.format import shorten_line
    from nana.social.guards import (
        fallback_public_social_reply,
        compact_public_reaction_reply,
        remove_disallowed_social_draft_bits,
    )
    source, draft = parse_draft_quality_test(raw_text)
    print("🧪 Draft Quality Test")
    print("  Action: read-only; không lưu draft, không confirm, không post, không type.")
    if not source or not draft or "<" in source or "<" in draft:
        print("  Missing: /draft-quality-test <source/context> || <draft>")
        print("  Example: /draft-quality-test video xe máy tông cột điện || Cute thế.")
        return
    source_text = build_social_draft_source(source, broker_context={}, vision_description=None)
    style = detect_public_reaction_style(source_text) or "none"
    compacted = compact_public_reaction_reply(draft, source_text=source_text, intent="social.reply")
    fallback = fallback_public_social_reply(source_text=source_text)
    issues = []
    if social_draft_has_obvious_quality_issue(draft):
        issues.append(("quality_typo", "phát hiện typo/ký tự lỗi rõ"))
    if social_draft_needs_fallback(draft, source_text=source_text, intent="social.reply"):
        issues.append(("fallback_recommended", f"fallback={fallback}"))
    if compacted and compacted != draft:
        issues.append(("style_guard_would_adjust", f"would_use={compacted}"))
    stripped = remove_disallowed_social_draft_bits(draft, source_text=source_text)
    if stripped != draft:
        issues.append(("action_or_noise_claim", "draft có dấu hiệu claim hành động/link/noise bị strip"))
    match_text = source_match_bundle(draft)
    drift_markers = [
        "cute", "cưng", "cung", "dễ thương", "de thuong",
        "cười xỉu", "cuoi xiu", "đỉnh", "dinh",
        "hóng", "hong", "kèo này", "keo nay", "đáng đời", "dang doi",
    ]
    if style in SENSITIVE_DRAFT_STYLES and any(marker in match_text for marker in drift_markers):
        issues.append(("toxicity_or_vibe_drift", "draft dùng vibe đùa/hóng trong context nhạy cảm"))
    status = "pass" if not issues else "warn"
    print(f"  Status: {status}")
    print(f"  Style: {style}")
    print(f"  Fallback: {fallback}")
    print(f"  Draft: {shorten_line(draft, 120)}")
    if compacted != draft:
        print(f"  Guarded draft: {compacted}")
    if issues:
        for code, detail in issues:
            print(f"  Issue: {code} | {detail}")
    else:
        print("  Issues: none")
    print(f"  Source: {shorten_line(source_text, 180)}")


def is_diagnostic_fragment(text):
    normalized = " ".join(str(text or "").split()).strip()
    lowered = normalized.lower()
    if not normalized:
        return False
    prefixes = [
        "execute:", "reason:", "recovery:",
        "draft id:", "draft:", "queue:",
        "status:", "context level:", "context allowed:",
        "plan status:", "intent:", "policy:",
        "needs confirm:", "privacy risk:",
        "route status:", "router model:", "helper debug:",
        "safety:", "confirm note:", "duplicate warning:",
    ]
    if any(lowered.startswith(prefix) for prefix in prefixes):
        return True
    exact_lines = {
        "execute: skipped",
        "reason: social_target_missing (...)",
        "reason: no_vision_description",
        "reason: no_preview_image",
    }
    return lowered in exact_lines


def social_draft_needs_fallback(text, source_text="", intent="social.draft"):
    if not text:
        return True
    lowered = text.lower().strip()
    if intent in {"social.reply", "social.draft"}:
        reaction_style = detect_public_reaction_style(source_text)
        short_ok = {
            "cute thế.", "cute the.",
            "cưng thế.", "cung the.",
            "dễ thương thế.", "de thuong the.",
            "phản ứng nhanh thật.", "phan ung nhanh that.",
            "né kịp quá.", "ne kip qua.",
            "nhìn chill thật.", "nhin chill that.",
            "cười xỉu.", "cuoi xiu.",
            "năng lượng ghê.", "nang luong ghe.",
            "đỉnh thật.", "dinh that.",
            "ủa phe nào thắng vậy?", "ua phe nao thang vay?",
            "quạ vào can à?", "qua vao can a?",
            "cần thưởng gấp rồi.", "can thuong gap roi.",
            "đi làm tới mức này luôn à?", "di lam toi muc nay luon a?",
        }
        if reaction_style and lowered in short_ok:
            return False
        if reaction_style and 4 <= len(lowered) <= 24:
            return False
    if len(lowered) < 16:
        return True
    dangling_endings = ["làm", "vì", "nên", "mà", "rồi", "với", "cho"]
    if any(lowered.endswith(ending) for ending in dangling_endings):
        return True
    return social_draft_has_obvious_quality_issue(text)


def social_draft_has_obvious_quality_issue(text):
    import re
    lowered = str(text or "").lower()
    if "�" in lowered:
        return True
    if re.search(r"(.)\1{5,}", lowered, flags=re.IGNORECASE):
        return True
    if re.search(r"\b(lquen|lạquen)\b", lowered, flags=re.IGNORECASE):
        return True
    if re.search(r"\bl(vậy|vay)\b", lowered, flags=re.IGNORECASE):
        return True
    if re.search(r"\bxnhanh\b", lowered, flags=re.IGNORECASE):
        return True
    return False


def polish_social_draft_quality(text, source_text="", intent="social.draft"):
    import re
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return cleaned
    replacements = {
        r"\bphản\s+xnhanh\b": "Phản ứng nhanh",
        r"\bphan\s+xnhanh\b": "Phản ứng nhanh",
        r"\bxnhanh\b": "nhanh",
        r"\blquen\b": "quen",
        r"\blạquen\b": "lạ quen",
        r"\blq\b": "liên quan",
    }
    for pattern, replacement in replacements.items():
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
    cleaned = re.sub(r"([,.!?]){3,}", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    if intent in {"social.reply", "social.draft"} and not cleaned.endswith((".", "!", "?")):
        cleaned += "."
    return cleaned


def sanitize_teo_lite_reply(text, source_text=""):
    cleaned = " ".join(str(text or "").split()).strip()
    if not cleaned:
        return fallback_public_social_reply(source_text=source_text)
    lowered = cleaned.lower()
    normalized = strip_accents_for_match(lowered)
    match_text = f"{lowered} {normalized}"
    banned = [
        "ngu", "óc", "oc ", "đần", "dan",
        "điên", "dien", "mày", "may ",
        "thằng", "thang", "con này", "con nay",
        "bọn", "bon ", "lũ", "lu ",
    ]
    if any(marker in match_text for marker in banned):
        return fallback_public_social_reply(source_text=source_text)
    if len(cleaned) > 42:
        return fallback_public_social_reply(source_text=source_text)
    return cleaned


def remove_disallowed_social_draft_bits(text, source_text=""):
    import re
    cleaned = re.sub(
        r"^(con|nana)\s+(sẽ\s+)?(đăng|dang|gửi|gui|post|tweet)\s+[^:：]{0,40}[:：]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(
        r"^(con|nana)\s+(sẽ\s+)?(đăng|dang|gửi|gui|post|tweet)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = cleaned.strip('"').strip("'").strip(""" """).strip("''").strip()
    if "http" not in (source_text or "").lower():
        cleaned = re.sub(r"https?://\S+", "", cleaned).strip()
    weak_patterns = [
        r",?\s*ba xem giúp con với\.?",
        r"\s*ba xem giúp con với\.?",
        r"con đau đầu quá,?\s*",
        r"con cần ba cứu,?\s*",
        r"cứu con với,?\s*",
    ]
    for pattern in weak_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"[\U00010000-\U0010ffff]", "", cleaned)
    cleaned = re.sub(r"[\u2600-\u27BF]", "", cleaned)
    cleaned = cleaned.replace(" ạ", "").replace("ạ ", "").strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" -\"'")
