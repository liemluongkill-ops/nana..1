"""Deterministic smoke tests for Nana's Eleven v3 inline audio-tag adapter.

No live Nana runtime, LLM, ElevenLabs, TTS, playback, Discord, VTS, OBS,
memory write, or game input is used.
"""

from __future__ import annotations

import io
import re
import sys
import threading
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_adapter_normalizes_and_filters() -> None:
    print("[Inline Tags] Test 1: canonical adapter normalizes aliases and filters blocked tags...")
    from nana.voice.inline_audio_tags import render_inline_audio_tags

    result = render_inline_audio_tags(
        "[thoughtfully] Con nghĩ thế này. [whispers] Nói nhỏ thôi. "
        "[laughs] Không phát tag này. [khụ] Cũng chặn. "
        "[long pause] Rồi nói tiếp. [code] giữ nguyên."
    )
    assert result.tags == ("thoughtful", "whisper", "long pause"), result
    assert result.alias_normalized_count == 2, result
    assert result.blocked_count == 2, result
    assert result.unknown_count == 1, result
    assert "[thoughtful]" in result.tts_text, result.tts_text
    assert "[whisper]" in result.tts_text, result.tts_text
    assert "[long pause]" in result.tts_text, result.tts_text
    assert "[laughs]" not in result.tts_text, result.tts_text
    assert "[khụ]" not in result.tts_text, result.tts_text
    assert "[code]" in result.tts_text, result.tts_text
    assert "[thoughtful]" not in result.display_text, result.display_text
    assert "[whisper]" not in result.display_text, result.display_text
    assert "[long pause]" not in result.display_text, result.display_text
    assert "[code]" in result.display_text, result.display_text
    print("  PASSED")


def _test_cap_rollback_and_malformed_repair() -> None:
    print("[Inline Tags] Test 2: cap, rollback, and malformed-tag repair are deterministic...")
    from nana.voice.inline_audio_tags import render_inline_audio_tags

    text = "[happy] Một. [sad] Hai. [excited] Ba."
    capped = render_inline_audio_tags(text, max_tags=2)
    assert capped.tags == ("happy", "sad"), capped
    assert capped.overflow_count == 1, capped
    assert "[excited]" not in capped.tts_text, capped.tts_text

    disabled = render_inline_audio_tags(text, enabled=False)
    assert disabled.tags == (), disabled
    assert disabled.disabled_count == 3, disabled
    assert "[" not in disabled.tts_text, disabled.tts_text

    repaired = render_inline_audio_tags("[thoughtful Ba nghe con nói nè.")
    assert repaired.tags == ("thoughtful",), repaired
    assert repaired.tts_text.startswith("[thoughtful] Ba"), repaired.tts_text
    print("  PASSED")


def _test_documented_catalog_and_effect_firewall() -> None:
    print("[Inline Tags] Test 3: documented voice directions pass while effects stay blocked...")
    from nana.voice.inline_audio_tags import (
        CANONICAL_AUDIO_TAGS,
        ELEVEN_V3_INLINE_AUDIO_TAG_GUIDE,
        render_inline_audio_tags,
    )

    directions = render_inline_audio_tags(
        "[warmly] Một. [frustrated] Hai. [reassuring] Ba. "
        "[deadpan] Bốn. [muttering] Năm."
    )
    assert directions.tags == (
        "warmly",
        "frustrated",
        "reassuring",
        "deadpan",
        "muttering",
    ), directions
    assert directions.blocked_count == 0, directions

    aliases = render_inline_audio_tags(
        "[dramatic] Một. [sheepish] Hai. [pause] Ba."
    )
    assert aliases.tags == (
        "dramatically",
        "sheepishly",
        "short pause",
    ), aliases
    assert aliases.alias_normalized_count == 3, aliases

    blocked = render_inline_audio_tags(
        "[gunshot] Một. [snorts] Hai. [strong French accent] Ba. "
        "[robotic voice] Bốn. [laughing loudly] Năm."
    )
    assert blocked.tags == (), blocked
    assert blocked.blocked_count == 5, blocked
    assert "[" not in blocked.tts_text, blocked.tts_text
    assert "[" not in blocked.display_text, blocked.display_text

    guide_tags = {
        " ".join(tag.lower().split())
        for tag in re.findall(r"\[([^\]]+)\]", ELEVEN_V3_INLINE_AUDIO_TAG_GUIDE)
    }
    assert guide_tags, ELEVEN_V3_INLINE_AUDIO_TAG_GUIDE
    assert guide_tags <= set(CANONICAL_AUDIO_TAGS), sorted(
        guide_tags - set(CANONICAL_AUDIO_TAGS)
    )
    print("  PASSED")


def _test_engine_tone_telemetry_and_cache_identity() -> None:
    print("[Inline Tags] Test 4: engine tone, telemetry, and cache identity use normalized tags...")
    from nana.voice.engine import VoiceEngine, _detect_tone_from_text, _prepare_tts_text

    assert _prepare_tts_text("[playfully] A [softly] B") == "[mischievously] A B"
    assert _detect_tone_from_text("[annoyed] Thôi nào.") == "annoyed"
    assert _detect_tone_from_text("[warmly] Con ở đây.") == "caring"
    assert _detect_tone_from_text("[delighted] Hay quá.") == "happy"
    assert _detect_tone_from_text("[panicking] Khoan đã.") == "nervous"
    assert _detect_tone_from_text("[frustrated] Lại nữa rồi.") == "annoyed"
    assert _detect_tone_from_text("[softly] Thôi nào.") == "default"

    engine = VoiceEngine.__new__(VoiceEngine)
    engine.state_lock = threading.Lock()
    engine.state = {}
    rendered = engine._prepare_tts_request_text("[thoughtfully] A [laughs] B")
    snapshot = dict(engine.state)
    assert rendered == "[thoughtful] A B", rendered
    assert snapshot["last_inline_tags"] == ("thoughtful",), snapshot
    assert snapshot["last_inline_tag_aliases"] == 1, snapshot
    assert snapshot["last_inline_tag_blocked"] == 1, snapshot

    happy_key = engine._normalize_cache_text("[happy] Cùng một câu.")
    sad_key = engine._normalize_cache_text("[sad] Cùng một câu.")
    assert happy_key != sad_key, (happy_key, sad_key)
    print("  PASSED")


def _test_prompt_and_terminal_boundaries() -> None:
    print("[Inline Tags] Test 5: private guide is canonical and terminal hides streamed tags...")
    from nana.brain import gpt
    from nana.config import NANA_PERSONALITY
    from nana.runtime.persona_boundary import resolve_persona_boundary

    guide = gpt.AUDIO_TAG_GUIDE.lower()
    assert "[thoughtful]" in guide, guide
    assert "[long pause]" in guide, guide
    assert "[laughs]" not in guide, guide
    assert "[chuckles]" not in guide, guide
    assert "[softly]" not in guide, guide
    assert "emotional audio tags" not in NANA_PERSONALITY.lower(), NANA_PERSONALITY

    assert gpt.strip_terminal_audio_tags("[thoughtfully] Chào [happy] Ba") == "Chào Ba"
    sanitizer = gpt.TerminalAudioTagStreamSanitizer()
    visible = "".join(
        sanitizer.feed(part)
        for part in ("[thou", "ghtful] Chào ", "[hap", "py] Ba")
    ) + sanitizer.flush()
    assert "[" not in visible and "]" not in visible, visible
    assert "Chào" in visible and "Ba" in visible, visible

    private = resolve_persona_boundary()
    public = resolve_persona_boundary(viewer_name="viewer", stream_mode=True)
    compact = gpt._audio_tag_prompt_for_boundary(private, story_mode=False)
    story = gpt._audio_tag_prompt_for_boundary(private, story_mode=True)
    assert "[thoughtful]" in compact
    assert "[long pause]" in compact
    assert len(compact) < len(story)
    assert gpt._audio_tag_prompt_for_boundary(public, story_mode=False) == ""
    assert gpt._audio_tag_prompt_for_boundary(public, story_mode=True) == ""
    print("  PASSED")


def _test_commands_registry_firewall_and_status() -> None:
    print("[Inline Tags] Test 6: commands, registry, public firewall, and telemetry are wired...")
    from nana.cli.voice_commands import handle_voice_command
    from nana.commands.help import print_command_help
    from nana.commands.registry import KNOWN_SLASH_COMMANDS
    from nana.commands.router_manifest import classify_command_truth
    from nana.core.status_voice import print_voice_status
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_voice_command(
            None,
            "/voice-inline-tag-preview [thoughtfully] Chào [laughs] Ba",
        )
    preview = buf.getvalue()
    assert handled is True, preview
    assert "Inline Audio Tag Preview" in preview, preview
    assert "[thoughtful]" in preview, preview
    assert "blocked=1" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        handled = handle_voice_command(None, "/voice-inline-tag-status")
    status = buf.getvalue()
    assert handled is True, status
    assert "no LLM/TTS/VoiceEngine/API" in status, status

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_text = buf.getvalue()
    assert "/voice-inline-tag-status" in help_text, help_text
    assert "/voice-inline-tag-preview" in help_text, help_text
    assert "/voice-inline-tag-status" in KNOWN_SLASH_COMMANDS
    assert "/voice-inline-tag-preview" in KNOWN_SLASH_COMMANDS
    assert classify_command_truth("/voice-inline-tag-status", known=True).status == "live"

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/voice-inline-tag-status") == "backstage_command"
    assert guard.classify_public_input("/voice-inline-tag-preview [happy] x") == "backstage_command"

    class FakeVoice:
        def snapshot(self):
            return {
                "last_inline_tags": ("thoughtful", "happy"),
                "last_inline_tag_count": 2,
                "last_inline_tag_aliases": 1,
                "last_inline_tag_blocked": 1,
                "last_inline_tag_overflow": 0,
                "last_inline_tag_unknown": 0,
                "last_inline_tag_input_chars": 44,
                "last_inline_tag_tts_chars": 39,
                "voice_inline_audio_tags_enabled": True,
                "voice_inline_audio_tag_max": 5,
            }

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_voice_status(FakeVoice())
    voice_status = buf.getvalue()
    assert "Inline tags: enabled=True" in voice_status, voice_status
    assert "last=2/5" in voice_status, voice_status
    assert "tags=thoughtful,happy" in voice_status, voice_status
    print("  PASSED")


def main() -> int:
    print("CORE-VOICE-INLINE-AUDIO-TAGS-1 - Smoke Tests")
    tests = [
        _test_adapter_normalizes_and_filters,
        _test_cap_rollback_and_malformed_repair,
        _test_documented_catalog_and_effect_firewall,
        _test_engine_tone_telemetry_and_cache_identity,
        _test_prompt_and_terminal_boundaries,
        _test_commands_registry_firewall_and_status,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"  FAILED: {type(exc).__name__}: {exc}")
    passed = len(tests) - failed
    print(f"[Inline Tags] Results: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
