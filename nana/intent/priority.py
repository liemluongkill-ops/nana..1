"""nana.intent.priority — deterministic context priority helpers.

Provides context_priority_policy and format_context_priority_summary.
"""
from __future__ import annotations


def context_priority_policy(context=None, vision_description=None):
    context = context or {}
    kind = (context.get("browser_kind") or "unknown").lower()
    state = context.get("browser_snapshot_state") or "INVALID"
    rows = _build_rows(context, vision_description=vision_description)
    order = _priority_order(kind, rows)
    ranked = []
    ignored = []
    row_map = {row["name"]: row for row in rows}
    for name in order:
        row = row_map.get(name)
        if not row:
            continue
        if row["available"]:
            ranked.append(row)
        else:
            ignored.append(row)
    overall = max([row["score"] for row in ranked], default=0.0)
    return {
        "kind": kind,
        "media_mode": _media_mode(context, vision_description),
        "reason": _priority_reason(kind),
        "fresh": bool(context.get("browser_fresh")),
        "state": state,
        "age": context.get("browser_age_seconds"),
        "overall": overall,
        "ranked": ranked,
        "ignored": ignored,
        "rows": rows,
    }


def context_priority_brief(policy, limit=3):
    ranked = (policy or {}).get("ranked") or []
    if not ranked:
        return "none"
    return " > ".join(str(row.get("name")) for row in ranked[:limit])


def format_context_priority_summary(policy):
    policy = policy or {}
    overall = float(policy.get("overall") or 0.0)
    return (
        f"{context_priority_brief(policy)} | "
        f"overall={overall:.2f} ({_confidence_label(overall)}) | "
        f"state={policy.get('state') or 'INVALID'}"
    )


def _build_rows(context, vision_description=None):
    fields = [
        ("post", context.get("browser_social_post_text"), 0.95),
        ("title", context.get("browser_title"), 0.78),
        ("url", context.get("browser_url"), 0.55),
        ("page_extra", context.get("browser_local_summary") or context.get("browser_heading") or context.get("browser_meta_description"), 0.72),
        ("vision", vision_description, 0.70),
        ("vibe", context.get("browser_social_vibe"), 0.45),
        ("comments", context.get("browser_social_comments"), 0.35),
    ]
    rows = []
    for name, value, score in fields:
        available = bool(value)
        rows.append({
            "name": name,
            "available": available,
            "score": score if available else 0.0,
        })
    return rows


def _priority_order(kind, rows):
    media = _media_mode_from_rows(rows)
    if kind == "social":
        if media == "image":
            return ["post", "vision", "title", "url", "page_extra", "vibe", "comments"]
        if media == "video":
            return ["post", "title", "url", "vision", "page_extra", "vibe", "comments"]
        return ["post", "title", "url", "page_extra", "vibe", "comments", "vision"]
    if kind in {"youtube", "video", "music"}:
        return ["title", "url", "page_extra", "vision", "post", "vibe", "comments"]
    if kind in {"github", "docs", "search", "search_home"}:
        return ["title", "page_extra", "url", "vision", "post", "vibe", "comments"]
    return ["title", "url", "page_extra", "vision", "post", "vibe", "comments"]


def _media_mode(context, vision_description):
    source = " ".join(
        str(x or "")
        for x in [
            context.get("browser_title"),
            context.get("browser_social_post_text"),
            context.get("browser_local_summary"),
            vision_description,
        ]
    ).lower()
    if any(marker in source for marker in ["video", "clip", "watch", "youtube"]):
        return "video"
    if any(marker in source for marker in ["ảnh", "anh", "image", "photo", "pic"]):
        return "image"
    return "text"


def _media_mode_from_rows(rows):
    # Rows do not preserve raw text; this is only used for broad ordering.
    return "text"


def _priority_reason(kind):
    if kind == "social":
        return "social: visible post/title trước, vision là nguồn phụ khi có"
    if kind in {"youtube", "video", "music"}:
        return f"{kind}: title/url là xương sống, local summary hỗ trợ"
    if kind in {"github", "docs"}:
        return f"{kind}: title/heading/summary quan trọng hơn vibe"
    return "default: ưu tiên nguồn có sẵn, không đoán sâu"


def _confidence_label(value):
    if value >= 0.8:
        return "high"
    if value >= 0.5:
        return "medium"
    if value > 0:
        return "low"
    return "none"
