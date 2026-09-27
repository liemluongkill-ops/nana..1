"""Smoke tests for STAGE-9T voice reply budget.

No LLM, no ElevenLabs, no playback. This only verifies deterministic text
shaping before local/private TTS.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _long_story() -> str:
    return (
        "Có một đêm rất muộn, Ba ngồi trước CMD và căn phòng yên tới mức nghe rõ tiếng quạt máy. "
        "Bug không nổ tung, không đỏ rực, chỉ lì lợm nằm ở một assumption nhỏ mà ai cũng dễ bỏ qua. "
        "Nana đứng cạnh màn hình, nhìn Ba đọc lại từng input, từng dòng log, từng nhịp rất chậm. "
        "Không có nhạc nền hào hùng, chỉ có một người mệt nhưng vẫn chịu khó thành thật với vấn đề. "
        "Cuối cùng lỗi nằm ở đúng chỗ chủ quan nhất, nhỏ tới mức phát hiện ra rồi chỉ muốn thở dài. "
        "Câu chuyện không kịch tính, nhưng nó thật theo kiểu rất người, rất khuya, và rất Nana nhớ. "
    ) * 4


def _test_short_text_passes_through():
    print("[9T Smoke] Test 1: short voice text passes through...")
    from nana.runtime.voice_reply_budget import shape_voice_reply

    text = "Nana kể ngắn thôi nha, không nhồi voice đâu."
    result = shape_voice_reply(text, mode="chat")
    assert result.phase == "STAGE-9T", result
    assert result.read_only is True and result.can_act is False, result
    assert result.memory_write is False and result.api_call is False and result.tts_call is False, result
    assert result.changed is False, result
    assert result.voice_text == text, result
    print("  PASSED")


def _test_long_story_gets_voice_cap():
    print("[9T Smoke] Test 2: long story is capped for voice...")
    from nana.runtime.voice_reply_budget import STORY_MAX_CHARS, VOICE_CLOSING, shape_voice_reply

    result = shape_voice_reply(_long_story(), mode="story")
    assert result.changed is True, result
    assert result.reason == "long_reply_truncated_for_voice", result
    assert result.voice_chars <= STORY_MAX_CHARS, result
    assert VOICE_CLOSING.strip() in result.voice_text, result.voice_text
    assert result.omitted_chars > 0, result
    print("  PASSED")


def _test_chat_mode_still_caps_long_text():
    print("[9T Smoke] Test 3: chat mode still caps long text...")
    from nana.runtime.voice_reply_budget import CHAT_MAX_CHARS, VOICE_CLOSING, shape_voice_reply

    result = shape_voice_reply(_long_story(), mode="chat")
    assert result.changed is True, result
    assert result.mode == "chat", result
    assert result.reason == "long_reply_truncated_for_voice", result
    assert result.voice_chars <= CHAT_MAX_CHARS, result
    assert VOICE_CLOSING.strip() in result.voice_text, result.voice_text
    print("  PASSED")


def _test_full_policy_never_caps_private_text():
    print("[9T Smoke] Test 4: full policy keeps all private voice text...")
    from nana.runtime.voice_reply_budget import shape_voice_reply

    text = _long_story()
    result = shape_voice_reply(text, mode="full")
    assert result.mode == "full", result
    assert result.changed is False, result
    assert result.reason == "full_voice_policy", result
    assert result.voice_text == result.original_text, result
    assert result.voice_chars == result.original_chars == len(result.original_text), result
    assert result.omitted_chars == 0, result
    assert result.max_chars == 0, result
    print("  PASSED")


def _test_full_policy_preserves_text_beyond_clean_limit():
    print("[9T Smoke] Test 5: full policy preserves exact text beyond 5000 chars...")
    from nana.runtime.voice_reply_budget import shape_voice_reply, voice_budget_preview_lines

    text = ("0123456789  \n" * 400) + ("abcdefghij" * 20) + "END"
    assert len(text) > 5001, len(text)
    result = shape_voice_reply(text, mode="full")
    assert result.voice_text == text, result
    assert result.original_text == text, result
    assert result.original_chars == result.voice_chars == len(text), result
    assert result.changed is False, result
    assert result.omitted_chars == 0, result
    assert result.reason == "full_voice_policy", result

    preview = "\n".join(voice_budget_preview_lines(text))
    assert "Policy: full | source=private_default" in preview, preview[:300]
    assert f"Input chars: {len(text)}" in preview, preview[:300]
    assert f"Voice chars: {len(text)} | max=unlimited | changed=False" in preview, preview[:300]
    print("  PASSED")


def _test_stream_budget_caps_across_chunks():
    print("[9T Smoke] Test 6: stream budget caps across many chunks...")
    from nana.runtime.voice_reply_budget import (
        STORY_MAX_CHARS,
        get_voice_reply_budget,
        should_defer_stream_voice,
        story_stream_dispatch_config,
        voice_stream_dispatch_config,
    )

    session = get_voice_reply_budget().begin_turn(mode="story")
    spoken_parts = []
    for chunk in _long_story().split(" "):
        spoken = session.feed(chunk + " ")
        if spoken:
            spoken_parts.append(spoken)
    result = session.finalize()
    spoken_text = "".join(spoken_parts)
    assert result.changed is True, result
    assert result.reason == "stream_truncated_for_voice", result
    assert len(spoken_text) <= STORY_MAX_CHARS, len(spoken_text)
    assert "không nhồi voice nữa" in spoken_text, spoken_text
    assert session.feed("đoạn sau không được nói") == ""
    assert should_defer_stream_voice("story") is True
    assert should_defer_stream_voice("full") is True
    assert should_defer_stream_voice("chat") is False
    config = story_stream_dispatch_config("story")
    assert config["mode"] == "lead_then_tail", config
    assert config["lead_segments"] == 3, config
    assert config["tail_packet"] is True, config
    assert config["tail_full"] is True, config

    full_config = voice_stream_dispatch_config("full")
    assert full_config["mode"] == "lead_then_tail", full_config
    assert full_config["tail_full"] is True, full_config
    full_session = get_voice_reply_budget().begin_turn(mode="full")
    full_text = _long_story()
    assert full_session.feed(full_text) == full_text
    full_result = full_session.finalize()
    assert full_result.changed is False, full_result
    assert full_result.reason == "stream_full_voice", full_result
    assert full_result.voice_chars == full_result.original_chars == len(full_text), full_result

    hybrid = get_voice_reply_budget().begin_turn(mode="story", truncate=not config["tail_full"])
    tail = _long_story()
    packets = [
        hybrid.feed("Có một đêm rất muộn, Ba ngồi trước CMD và căn phòng yên tới mức nghe rõ tiếng quạt máy. "),
        hybrid.feed("Bug không nổ tung, không đỏ rực, chỉ lì lợm nằm ở một assumption nhỏ mà ai cũng dễ bỏ qua. "),
        hybrid.feed("Nana đứng cạnh màn hình, nhìn Ba đọc lại từng input, từng dòng log, từng nhịp rất chậm. "),
        hybrid.feed(tail),
    ]
    hybrid_result = hybrid.finalize()
    spoken_packets = [packet for packet in packets if packet]
    assert len(spoken_packets) == 4, spoken_packets
    assert hybrid_result.changed is False, hybrid_result
    assert hybrid_result.reason == "stream_full_tail_voice", hybrid_result
    assert spoken_packets[-1] == tail, spoken_packets[-1]
    assert "không nhồi voice nữa" not in spoken_packets[-1], spoken_packets[-1]
    print("  PASSED")


def _test_status_preview_help_firewall_stage_surface():
    print("[9T Smoke] Test 7: status/preview/help/firewall/stage surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.chat_surface import is_story_request
    from nana.core.status import print_stage_status, print_voice_status
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard
    from nana.runtime.voice_reply_budget import (
        voice_budget_log_enabled,
        voice_budget_preview_lines,
        voice_budget_status_lines,
    )

    status = "\n".join(voice_budget_status_lines())
    preview = "\n".join(voice_budget_preview_lines(_long_story()))
    assert "STAGE-9T" in status, status
    assert "tts_call=False" in status, status
    assert "log=off" in status, status
    assert "private_default=full" in status, status
    assert "reply_mode_independent=True" in status, status
    assert "full_mode=A_single_utterance" in status, status
    assert "full_stream=lead_then_tail" in status, status
    assert "single_utterance=on" in status, status
    assert "lead=3" in status, status
    assert "tail=full" in status, status
    assert voice_budget_log_enabled() is False
    assert is_story_request("oh nana kể một chuyện nào đấy thật hơn đi haha") is True
    assert is_story_request("oh nana ke mot chuyen nao day that hon di haha") is True
    assert is_story_request("ba có chuyện gì không") is False
    assert "Voice Budget Preview" in preview, preview
    assert "Policy: full | source=private_default" in preview, preview
    assert "max=unlimited" in preview, preview
    assert "no TTS call" in preview, preview

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_voice_status()
    voice_status = buf.getvalue()
    assert "Voice budget:" in voice_status, voice_status

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Voice budget:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/voice-budget-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/voice-budget-status") == "backstage_command"
    assert guard.classify_public_input("/voice-budget-preview x") == "backstage_command"
    print("  PASSED")


def _test_mode_a_default_ignores_legacy_disable_env():
    print("[9T Smoke] Test 8: Mode A default ignores legacy disable env...")
    legacy_env = dict(os.environ)
    legacy_env["NANA_VOICE_STORY_SINGLE_UTTERANCE_ENABLED"] = "0"
    legacy_env.pop("NANA_VOICE_STORY_LEAD_PACKETS_ENABLED", None)
    legacy = subprocess.run(
        [
            sys.executable,
            "-c",
            "from nana.runtime.voice_reply_budget import voice_budget_status_lines; print('\\n'.join(voice_budget_status_lines()))",
        ],
        cwd=str(ROOT),
        env=legacy_env,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert "full_mode=A_single_utterance" in legacy, legacy
    assert "single_utterance=on" in legacy, legacy

    rollback_env = dict(os.environ)
    rollback_env["NANA_VOICE_STORY_LEAD_PACKETS_ENABLED"] = "1"
    rollback = subprocess.run(
        [
            sys.executable,
            "-c",
            "from nana.runtime.voice_reply_budget import voice_budget_status_lines; print('\\n'.join(voice_budget_status_lines()))",
        ],
        cwd=str(ROOT),
        env=rollback_env,
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert "full_mode=B_lead_packets" in rollback, rollback
    assert "single_utterance=off" in rollback, rollback
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9T Voice Reply Budget — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_short_text_passes_through,
        _test_long_story_gets_voice_cap,
        _test_chat_mode_still_caps_long_text,
        _test_full_policy_never_caps_private_text,
        _test_full_policy_preserves_text_beyond_clean_limit,
        _test_stream_budget_caps_across_chunks,
        _test_status_preview_help_firewall_stage_surface,
        _test_mode_a_default_ignores_legacy_disable_env,
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
