"""Voice/subtitle command router for Nana CLI."""

from __future__ import annotations

from nana.runtime.voice_delivery import voice_delivery_preview_lines, voice_delivery_status_lines
from nana.runtime.voice_reply_budget import voice_budget_preview_lines, voice_budget_status_lines
from nana.runtime.voice_span_planner import voice_span_preview_lines, voice_span_status_lines
from nana.voice.inline_audio_tags import inline_audio_tag_preview_lines, inline_audio_tag_status_lines


def handle_voice_command(voice, text: str, text_lower: str | None = None) -> bool:
    text_lower = text_lower or text.lower()

    if text_lower in {"/stage-output-status", "/output-status"}:
        from nana.core.status_voice import print_stage_output_status

        print_stage_output_status(voice)
        return True

    if text_lower in {"/voice-status", "/tts-status"}:
        from nana.core.status_voice import print_voice_status

        print_voice_status(voice)
        return True

    if text_lower in {
        "/interaction-latency-status",
        "/llm-voice-latency",
        "/latency-status",
    }:
        from nana.core.status_voice import print_interaction_latency_status

        print_interaction_latency_status(voice)
        return True

    if text_lower in {"/voice-inline-tag-status", "/voice-inline-tags-status"}:
        for line in inline_audio_tag_status_lines():
            print(line)
        return True

    if text_lower.startswith("/voice-inline-tag-preview") or text_lower.startswith("/voice-inline-tags-preview"):
        prefix = (
            "/voice-inline-tag-preview"
            if text_lower.startswith("/voice-inline-tag-preview")
            else "/voice-inline-tags-preview"
        )
        rest = text[len(prefix):].strip()
        for line in inline_audio_tag_preview_lines(rest):
            print(line)
        return True

    if text_lower in {"/voice-budget-status", "/voice-reply-budget-status", "/tts-budget-status"}:
        for line in voice_budget_status_lines():
            print(line)
        return True

    if text_lower.startswith("/voice-budget-preview") or text_lower.startswith("/voice-reply-budget-preview"):
        prefix = "/voice-budget-preview" if text_lower.startswith("/voice-budget-preview") else "/voice-reply-budget-preview"
        rest = text[len(prefix):].strip()
        for line in voice_budget_preview_lines(rest):
            print(line)
        return True

    if text_lower in {"/voice-delivery-status", "/tts-delivery-status", "/voice-packet-status"}:
        for line in voice_delivery_status_lines(voice):
            print(line)
        return True

    if (
        text_lower.startswith("/voice-delivery-preview")
        or text_lower.startswith("/tts-delivery-preview")
        or text_lower.startswith("/voice-packet-preview")
    ):
        if text_lower.startswith("/voice-delivery-preview"):
            prefix = "/voice-delivery-preview"
        elif text_lower.startswith("/tts-delivery-preview"):
            prefix = "/tts-delivery-preview"
        else:
            prefix = "/voice-packet-preview"
        rest = text[len(prefix):].strip()
        for line in voice_delivery_preview_lines(rest, voice):
            print(line)
        return True

    if text_lower in {"/voice-span-status", "/voice-spans-status"}:
        for line in voice_span_status_lines():
            print(line)
        return True

    if text_lower.startswith("/voice-span-preview") or text_lower.startswith("/voice-spans-preview"):
        prefix = "/voice-span-preview" if text_lower.startswith("/voice-span-preview") else "/voice-spans-preview"
        rest = text[len(prefix):].strip()
        for line in voice_span_preview_lines(rest):
            print(line)
        return True

    if text_lower in {"/subtitle-status", "/obs-subtitle-status"}:
        from nana.core.status_voice import print_subtitle_status

        print_subtitle_status()
        return True

    return False
