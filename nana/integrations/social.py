"""nana.integrations.social — stub module.

Provides social draft helpers used by phase7 dry-run and status checks.
"""
from __future__ import annotations


def build_social_draft_source(raw_text="", broker_context=None, vision_description=None):
    return raw_text or ""


def social_source_classification(source_text, broker_context=None, raw_text="", vision_description=None):
    return "auto", "default", "none"


def fallback_public_social_reply(source_text=""):
    return source_text or ""


def compact_public_reaction_reply(text, source_text="", intent="social.reply"):
    return text or ""


def social_target_missing_reason(raw_text, broker_context, context_preview, vision_description=None, omit_page_context=False):
    return None


def context_recovery_next_step(context=None, missing_reason=None, vision_description=None):
    return "Bổ sung context còn thiếu rồi chạy lại /dry-run."
