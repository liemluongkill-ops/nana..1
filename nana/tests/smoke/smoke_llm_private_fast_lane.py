"""Offline smokes for Nana's bounded private Gemini fast-lane pilot."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# This smoke asserts LLMGate transport behavior with fake callers; pin the
# private route so a <repository>/.env openai_direct default never reaches the network.
os.environ["NANA_PRIVATE_LLM_PROVIDER"] = "llmgate"

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _with_enabled_lane(callback):
    import nana.runtime.llm_private_fast_lane as lane

    old_enabled = lane.LLM_FAST_PRIVATE_ENABLED
    old_model = lane.LLM_FAST_PRIVATE_MODEL
    try:
        lane.LLM_FAST_PRIVATE_ENABLED = True
        lane.LLM_FAST_PRIVATE_MODEL = "gemini-3-flash"
        return callback(lane)
    finally:
        lane.LLM_FAST_PRIVATE_ENABLED = old_enabled
        lane.LLM_FAST_PRIVATE_MODEL = old_model


def _test_classifier_is_narrow_and_fail_closed():
    print("[Private Fast Lane] Test 1: classifier admits only bounded voice prompts...")

    def run(lane):
        allowed = lane.classify_private_fast_lane(
            "Nana ơi, trả lời Ba một câu ngắn khoảng mười lăm từ.",
            public=False,
            story_mode=False,
            casual_mode=False,
        )
        negated_explanation = lane.classify_private_fast_lane(
            "Nana ơi, nói một câu tiếng Việt đúng mười lăm từ, có Ba và con, không giải thích.",
            public=False,
            story_mode=False,
            casual_mode=False,
        )
        positive_explanation = lane.classify_private_fast_lane(
            "Nana ơi, nói một câu rồi giải thích tại sao code này lỗi.",
            public=False,
            story_mode=False,
            casual_mode=False,
        )
        technical = lane.classify_private_fast_lane(
            "Nana phân tích log lỗi ESP32 này giúp Ba",
            public=False,
            story_mode=False,
            casual_mode=False,
        )
        temporal = lane.classify_private_fast_lane(
            "Nana nhớ lúc nãy mình nói gì không?",
            public=False,
            story_mode=False,
            casual_mode=False,
        )
        context_dependent = lane.classify_private_fast_lane(
            "Nana làm tiếp phần này đi",
            public=False,
            story_mode=False,
            casual_mode=False,
        )
        checkpoint_followup = lane.classify_private_fast_lane(
            "Nana ơi, nói một câu tiếp nối chuyện chú thỏ đội mũ cam.",
            public=False,
            story_mode=False,
            casual_mode=False,
        )
        public = lane.classify_private_fast_lane(
            "Nana nói một câu ngắn",
            public=True,
            story_mode=False,
            casual_mode=False,
        )
        assert allowed.eligible and allowed.model == "gemini-3-flash", allowed
        assert negated_explanation.eligible, negated_explanation
        assert positive_explanation.reason == "requires_main_context", positive_explanation
        assert technical.reason == "requires_main_context", technical
        assert temporal.reason == "requires_main_context", temporal
        assert context_dependent.reason == "requires_main_context", context_dependent
        assert checkpoint_followup.reason == "requires_main_context", checkpoint_followup
        assert public.reason == "public_lane", public

    _with_enabled_lane(run)
    print("  PASSED")


def _test_prompt_is_small_and_reply_gate_blocks_assistant_drift():
    print("[Private Fast Lane] Test 2: compact prompt and identity gate are strict...")
    import nana.runtime.llm_private_fast_lane as lane

    messages = lane.build_fast_private_messages(
        "Nana ơi, trả lời Ba một câu ngắn khoảng mười lăm từ.",
        affection=0.9,
        annoyance=0.0,
        playfulness=0.5,
        recent_lines=["ba: test một câu", "nana: Con nghe đây Ba."],
    )
    prompt_chars = lane.prompt_char_count(messages)
    assert prompt_chars < 2200, prompt_chars
    assert lane.validate_fast_private_reply(
        "Con nghe đây Ba, tối nay mình làm gọn rồi nghỉ sớm nha."
    ).accepted
    assert lane.validate_fast_private_reply(
        "Dạ, Ba cần gì con cũng sẵn sàng giúp Ba ạ."
    ).reason in {"service_tone", "formal_da", "formal_a"}
    assert lane.validate_fast_private_reply(
        "I'm Kiro, an AI development environment."
    ).reason == "foreign_identity"
    assert lane.validate_fast_private_reply(
        "Tối nay mình làm gọn rồi nghỉ sớm nha."
    ).reason == "private_identity_missing"
    print(f"  prompt_chars={prompt_chars} | PASSED")


def _test_gpt_stream_uses_fast_model_and_short_prompt():
    print("[Private Fast Lane] Test 3: live stream surface selects Gemini pilot...")
    import nana.brain.gpt as gpt
    import nana.brain.llmgate_client as client
    import nana.runtime.llm_private_fast_lane as lane

    captured = []

    def fake_call(model_name, messages, **kwargs):
        captured.append((model_name, messages, kwargs))
        return "Con nghe đây Ba, hôm nay mình làm gọn rồi nghỉ sớm nha.", "ok"

    old_call = client.call_llmgate_messages
    old_observe = gpt._observe_mood_for_turn

    def run(_lane):
        try:
            client.call_llmgate_messages = fake_call
            gpt._observe_mood_for_turn = lambda *_args, **_kwargs: None

            async def collect():
                return "".join(
                    [
                        chunk
                        async for chunk in gpt.ask_gpt_stream(
                            "Nana ơi, trả lời Ba một câu ngắn khoảng mười lăm từ."
                        )
                    ]
                )

            reply = asyncio.run(collect())
        finally:
            client.call_llmgate_messages = old_call
            gpt._observe_mood_for_turn = old_observe

        assert reply.startswith("Con nghe đây Ba"), reply
        assert len(captured) == 1, captured
        model, messages, kwargs = captured[0]
        assert model == "gemini-3-flash", captured
        assert kwargs["max_tokens"] == 96, kwargs
        assert lane.prompt_char_count(messages) < 2200
        snap = lane.fast_lane_snapshot()
        assert snap["last_status"] == "accepted", snap
        assert snap["last_validation"] == "accepted", snap

    _with_enabled_lane(run)
    print("  PASSED")


def _test_rejected_fast_reply_falls_back_to_terra():
    print("[Private Fast Lane] Test 4: invalid Gemini reply falls back to main...")
    import nana.brain.gpt as gpt
    import nana.brain.llmgate_client as client
    import nana.runtime.llm_private_fast_lane as lane

    fast_calls = []
    main_calls = []

    def fake_call(model_name, messages, **kwargs):
        fast_calls.append((model_name, messages, kwargs))
        return "Dạ, Ba cần gì con cũng sẵn sàng giúp Ba ạ.", "ok"

    def fake_stream(model_name, messages, **kwargs):
        main_calls.append((model_name, messages, kwargs))
        yield "Con nghe đây Ba, mình làm gọn rồi nghỉ sớm nha."

    old_call = client.call_llmgate_messages
    old_stream = client.stream_llmgate_messages
    old_observe = gpt._observe_mood_for_turn

    def run(_lane):
        try:
            client.call_llmgate_messages = fake_call
            client.stream_llmgate_messages = fake_stream
            gpt._observe_mood_for_turn = lambda *_args, **_kwargs: None

            async def collect():
                return "".join(
                    [
                        chunk
                        async for chunk in gpt.ask_gpt_stream(
                            "Nana ơi, trả lời Ba một câu ngắn khoảng mười lăm từ."
                        )
                    ]
                )

            reply = asyncio.run(collect())
        finally:
            client.call_llmgate_messages = old_call
            client.stream_llmgate_messages = old_stream
            gpt._observe_mood_for_turn = old_observe

        assert reply.startswith("Con nghe đây Ba"), reply
        assert [call[0] for call in fast_calls] == ["gemini-3-flash"], fast_calls
        assert [call[0] for call in main_calls] == ["gpt-5.6-terra"], main_calls
        snap = lane.fast_lane_snapshot()
        assert snap["last_status"] == "fallback", snap
        assert snap["fallbacks"] >= 1, snap

    _with_enabled_lane(run)
    print("  PASSED")


def run_all() -> int:
    tests = [
        _test_classifier_is_narrow_and_fail_closed,
        _test_prompt_is_small_and_reply_gate_blocks_assistant_drift,
        _test_gpt_stream_uses_fast_model_and_short_prompt,
        _test_rejected_fast_reply_falls_back_to_terra,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"  FAILED: {type(exc).__name__}: {exc}")
    passed = len(tests) - failed
    print(f"smoke_llm_private_fast_lane: {passed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_all())
