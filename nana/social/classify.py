"""Social classification command helpers for the new Nana runtime."""
from __future__ import annotations

import time

from nana.core.context import broker_context_snapshot
from nana.core.format import shorten_line
from nana.core.vision import LAST_VISION_DESCRIPTION
from nana.intent.priority import context_priority_policy, format_context_priority_summary
from nana.social import (
    build_social_draft_source,
    compact_public_reaction_reply,
    fallback_public_social_reply,
)
from nana.social.classifier import (
    DEFAULT_SOCIAL_CLASSIFY_REQUEST,
    social_guard_expected_fallback_ok,
    social_source_classification,
)


def print_classify_text(raw_text):
    """Print classification results for raw text only."""
    source_text = build_social_draft_source(raw_text, broker_context={}, vision_description=None)
    media_mode, reaction_style, guard_hint = social_source_classification(
        source_text,
        broker_context={"browser_kind": "social", "browser_social_post_text": raw_text},
        raw_text=raw_text,
        vision_description=None,
    )
    fallback = fallback_public_social_reply(source_text=source_text)
    print("🧪 Text Classifier")
    print(f"  Media: {media_mode}")
    print(f"  Reaction style: {reaction_style}")
    print(f"  Guard hint: {guard_hint}")
    print(f"  Fallback draft: {fallback}")
    print(f"  Source: {shorten_line(source_text, 220)}")
    print("  Action: read-only; không gọi model, không tạo draft.")


def print_classify_expect(raw_text):
    parts = str(raw_text or "").split(" ", 1)
    if len(parts) < 2:
        print("🧪 Classify Expect")
        print("  Usage: /classify-expect <expected_style> | <text>")
        return
    expected, sample = _split_expected(parts[1])
    if not expected or not sample:
        print("🧪 Classify Expect")
        print("  Usage: /classify-expect <expected_style> | <text>")
        return
    source_text = build_social_draft_source(sample, broker_context={}, vision_description=None)
    media_mode, reaction_style, guard_hint = social_source_classification(
        source_text,
        broker_context={"browser_kind": "social", "browser_social_post_text": sample},
        raw_text=sample,
        vision_description=None,
    )
    fallback = fallback_public_social_reply(source_text=source_text)
    compact = compact_public_reaction_reply(fallback)
    fallback_ok, fallback_reason = social_guard_expected_fallback_ok(expected, fallback)
    status = "pass" if reaction_style == expected and fallback_ok else "warn"
    print("🧪 Classify Expect")
    print(f"  Expected: {expected}")
    print(f"  Actual: {reaction_style}")
    print(f"  Status: {status}")
    print(f"  Media: {media_mode}")
    print(f"  Guard hint: {guard_hint}")
    print(f"  Fallback ok: {fallback_ok} ({fallback_reason})")
    print(f"  Fallback: {fallback}")
    if compact != fallback:
        print(f"  Compact: {compact}")
    print(f"  Source: {shorten_line(source_text, 220)}")
    print("  Action: read-only; không gọi model, không tạo draft.")


def print_social_classify(raw_text=DEFAULT_SOCIAL_CLASSIFY_REQUEST):
    """Print social classification using the real browser/vision context."""
    broker_context = broker_context_snapshot()
    vision_text, vision_status = _fresh_vision()
    source_text = build_social_draft_source(
        raw_text,
        broker_context=broker_context,
        vision_description=vision_text,
    )
    media_mode, reaction_style, guard_hint = social_source_classification(
        source_text,
        broker_context=broker_context,
        raw_text=raw_text,
        vision_description=vision_text,
    )
    priority_policy = context_priority_policy(
        context=broker_context,
        vision_description=vision_text,
    )
    print("🧪 Social Classifier")
    print(
        "  Browser: "
        f"kind={broker_context.get('browser_kind')} | "
        f"title={shorten_line(broker_context.get('browser_title'), 90)}"
    )
    print(f"  Vision: {vision_status}")
    print(f"  Context priority: {format_context_priority_summary(priority_policy)}")
    print(f"  Media: {media_mode}")
    print(f"  Reaction style: {reaction_style}")
    print(f"  Guard hint: {guard_hint}")
    if source_text:
        print(f"  Source: {shorten_line(source_text, 220)}")
    print("  Action: read-only; không gọi model, không tạo draft.")


def _fresh_vision(max_age_seconds=180):
    if not LAST_VISION_DESCRIPTION:
        return None, "none"
    try:
        age = max(0.0, time.time() - float(LAST_VISION_DESCRIPTION.get("time") or 0.0))
    except (TypeError, ValueError):
        age = None
    if age is None:
        return LAST_VISION_DESCRIPTION.get("text"), "unknown_age"
    if age <= max_age_seconds:
        return LAST_VISION_DESCRIPTION.get("text"), f"fresh ({age:.1f}s)"
    return None, f"stale ({age:.1f}s)"


def _split_expected(value):
    text = str(value or "").strip()
    if "|" in text:
        expected, sample = text.split("|", 1)
        return expected.strip(), sample.strip()
    parts = text.split(" ", 1)
    if len(parts) < 2:
        return "", ""
    return parts[0].strip(), parts[1].strip()
