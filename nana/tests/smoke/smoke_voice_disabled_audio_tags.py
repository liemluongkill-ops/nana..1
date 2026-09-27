"""Smoke tests for disabled voice audio tags.

Only [laughs], [chuckles], and [softly] are disabled. Other voice tags still
pass through for ElevenLabs tone control.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_disabled_tags_are_removed_before_tts() -> None:
    print("[Voice Tags] Test 1: disabled tags are removed before TTS...")
    from nana.voice.engine import _prepare_tts_text

    text = "[laughs] Nana chào Ba [softly] nè [chuckles]"
    prepared = _prepare_tts_text(text)
    assert "[laughs]" not in prepared.lower(), prepared
    assert "[chuckles]" not in prepared.lower(), prepared
    assert "[softly]" not in prepared.lower(), prepared
    assert prepared == "Nana chào Ba nè", prepared
    print("  PASSED")


def _test_disabled_tags_do_not_drive_voice_tone() -> None:
    print("[Voice Tags] Test 2: disabled tags do not drive voice tone...")
    from nana.voice.engine import _detect_tone_from_text

    assert _detect_tone_from_text("[laughs] Nana chào Ba") == "default"
    assert _detect_tone_from_text("[chuckles] Nana chào Ba") == "default"
    assert _detect_tone_from_text("[softly] Nana chào Ba") == "default"
    print("  PASSED")


def _test_other_tags_still_work() -> None:
    print("[Voice Tags] Test 3: other audio tags still work...")
    from nana.voice.engine import _detect_tone_from_text, _prepare_tts_text

    text = "Nana [excited] vui quá [sighs] thôi chịu."
    prepared = _prepare_tts_text(text)
    assert "[excited]" in prepared, prepared
    assert "[sighs]" in prepared, prepared
    assert _detect_tone_from_text(text) == "excited"
    print("  PASSED")


def _test_prompt_no_longer_suggests_disabled_tags() -> None:
    print("[Voice Tags] Test 4: prompt guide no longer suggests disabled tags...")
    from nana.brain.gpt import AUDIO_TAG_GUIDE

    guide = AUDIO_TAG_GUIDE.lower()
    assert "[laughs]" not in guide, AUDIO_TAG_GUIDE
    assert "[chuckles]" not in guide, AUDIO_TAG_GUIDE
    assert "[softly]" not in guide, AUDIO_TAG_GUIDE
    assert "[excited]" in guide, AUDIO_TAG_GUIDE
    assert "[sighs]" in guide, AUDIO_TAG_GUIDE
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("Voice Disabled Audio Tags — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_disabled_tags_are_removed_before_tts,
        _test_disabled_tags_do_not_drive_voice_tone,
        _test_other_tags_still_work,
        _test_prompt_no_longer_suggests_disabled_tags,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"  FAILED: {type(exc).__name__}: {exc}")
    passed = len(tests) - failed
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_all())
