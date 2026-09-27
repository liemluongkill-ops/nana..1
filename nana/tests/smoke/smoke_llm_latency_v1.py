"""Offline smoke coverage for Nana's low-latency private LLM path."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_private_prompt_is_compact_without_api_call():
    print("[LLM Latency V1] Test 1: private prompt is compact and uses the short budget...")
    import nana.brain.gpt as gpt
    import nana.brain.llmgate_client as client

    captured = {}

    def fake_stream(model_name, messages, **kwargs):
        captured["model"] = model_name
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        yield "Con nghe rõ"
        yield " nè Ba."

    old_stream = client.stream_llmgate_messages
    old_observe = gpt._observe_mood_for_turn
    old_compact = gpt.LLM_COMPACT_PRIVATE_PROMPT_ENABLED
    old_short = gpt.LLM_PROMPT_SHORT_TERM_LINES
    old_recent = gpt.LLM_PROMPT_RECENT_CHAT_LINES
    old_retrieval = gpt.LLM_PROMPT_RETRIEVAL_LIMIT
    old_rules = gpt.LLM_PROMPT_MEMORY_RULE_LIMIT
    old_audio_prompt = gpt._audio_tag_prompt_for_boundary
    try:
        client.stream_llmgate_messages = fake_stream
        gpt._observe_mood_for_turn = lambda *_args, **_kwargs: None

        async def collect():
            return "".join(
                [
                    chunk
                    async for chunk in gpt.ask_gpt_stream(
                        "Nana ơi, trả lời Ba một câu ngắn"
                    )
                ]
            )

        reply = asyncio.run(collect())
        compact_system = captured["messages"][0]["content"]

        gpt.LLM_COMPACT_PRIVATE_PROMPT_ENABLED = False
        asyncio.run(collect())
        full_identity_system = captured["messages"][0]["content"]

        gpt.LLM_PROMPT_SHORT_TERM_LINES = 8
        gpt.LLM_PROMPT_RECENT_CHAT_LINES = 12
        gpt.LLM_PROMPT_RETRIEVAL_LIMIT = 5
        gpt.LLM_PROMPT_MEMORY_RULE_LIMIT = 50
        gpt._audio_tag_prompt_for_boundary = (
            lambda boundary, *, story_mode: (
                gpt.AUDIO_TAG_GUIDE
                if not getattr(boundary, "public", False)
                else ""
            )
        )
        asyncio.run(collect())
        legacy_system = captured["messages"][0]["content"]
    finally:
        client.stream_llmgate_messages = old_stream
        gpt._observe_mood_for_turn = old_observe
        gpt.LLM_COMPACT_PRIVATE_PROMPT_ENABLED = old_compact
        gpt.LLM_PROMPT_SHORT_TERM_LINES = old_short
        gpt.LLM_PROMPT_RECENT_CHAT_LINES = old_recent
        gpt.LLM_PROMPT_RETRIEVAL_LIMIT = old_retrieval
        gpt.LLM_PROMPT_MEMORY_RULE_LIMIT = old_rules
        gpt._audio_tag_prompt_for_boundary = old_audio_prompt

    system = compact_system
    assert reply == "Con nghe rõ nè Ba.", reply
    assert captured["model"] == "gpt-5.6-terra", captured
    assert captured["kwargs"]["max_tokens"] == 120, captured
    assert "NANA PRIVATE CORE (compact)" in system
    assert "NANA CORE SELF (STAGE-9G)" not in system
    assert "NANA CORE SPINE (stable)" not in system
    assert len(system) < 18000, len(system)
    assert len(full_identity_system) > len(system) + 2000
    assert len(legacy_system) > len(system) + 5000
    print(
        f"  prompt_chars={len(system)} | full_identity={len(full_identity_system)} | "
        f"legacy={len(legacy_system)} | saved={len(legacy_system) - len(system)}ch | "
        "max_tokens=120 | PASSED"
    )


def _test_medium_and_long_reply_budgets_match_runtime_contract():
    print("[LLM Latency V1] Test 2: medium and long output budgets match runtime...")
    import nana.brain.gpt as gpt

    assert gpt._reply_max_tokens_for_text(
        "Nana nói khoảng một trăm từ, không quá 110 từ",
        story_mode=False,
    ) == 266
    assert gpt._reply_max_tokens_for_text(
        "Nana phân tích kỹ nguyên nhân độ trễ này",
        story_mode=False,
    ) == 420
    assert gpt._reply_max_tokens_for_text(
        "Nana kể một câu chuyện dài",
        story_mode=True,
    ) == 1000
    assert gpt._reply_max_tokens_for_text(
        "Nana trả lời khoảng mười lăm từ",
        story_mode=False,
    ) == 80
    print("  explicit_15=80 | medium_100_to_110=266 | chat=420 | story=1000 | PASSED")


def _test_stream_transport_records_first_text_without_network():
    print("[LLM Latency V1] Test 3: transport records TTFT and uses pooling...")
    import nana.brain.llmgate_client as client

    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

    class FakeEvent:
        def __init__(self, data):
            self.data = data

    class FakeSSEClient:
        def __init__(self, _response):
            pass

        @staticmethod
        def events():
            yield FakeEvent('{"choices":[{"delta":{"content":"Nana "}}]}')
            yield FakeEvent('{"choices":[{"delta":{"content":"test."}}]}')
            yield FakeEvent("[DONE]")

    class FakeSSEModule:
        SSEClient = FakeSSEClient

    def fake_model(model_name):
        return {
            "apiKey": "test-key",
            "baseUrl": "https://llmgate.invalid/v1",
            "model": model_name,
        }

    def fake_post(_url, **_kwargs):
        return FakeResponse()

    old_model = client.load_llmgate_model
    old_post = client._http_post
    old_log = client.log_event
    old_sse = sys.modules.get("sseclient")
    try:
        client.load_llmgate_model = fake_model
        client._http_post = fake_post
        client.log_event = lambda *_args, **_kwargs: None
        sys.modules["sseclient"] = FakeSSEModule()
        messages = [
            {"role": "system", "content": "Nana test system."},
            {"role": "user", "content": "Alo"},
        ]
        reply = "".join(
            client.stream_llmgate_messages(
                "gpt-5.6-terra",
                messages,
                max_tokens=420,
            )
        )
        snap = client.llmgate_transport_snapshot()
    finally:
        client.load_llmgate_model = old_model
        client._http_post = old_post
        client.log_event = old_log
        if old_sse is None:
            sys.modules.pop("sseclient", None)
        else:
            sys.modules["sseclient"] = old_sse

    assert reply == "Nana test.", reply
    assert snap["status"] == "complete", snap
    assert snap["stream"] is True, snap
    assert snap["pooled"] is True, snap
    assert snap["first_text_ms"] is not None, snap
    assert snap["tail_after_first_ms"] is not None, snap
    assert snap["chunks"] == 2, snap
    assert snap["output_chars"] == len(reply), snap
    assert snap["max_tokens"] == 420, snap
    assert snap["prompt_chars"] == len("Nana test system.") + len("Alo"), snap
    assert client._http_session() is client._http_session()
    stats = client.llmgate_transport_stats(
        model_name="gpt-5.6-terra",
        stream=True,
        limit=20,
    )
    assert stats["window"] >= 1, stats
    assert stats["first_text"]["p50_ms"] is not None, stats
    assert stats["tail_after_first"]["p50_ms"] is not None, stats
    assert stats["total"]["p95_ms"] is not None, stats
    print("  pooled=True | first_text=recorded | PASSED")


def run_all() -> int:
    tests = [
        _test_private_prompt_is_compact_without_api_call,
        _test_medium_and_long_reply_budgets_match_runtime_contract,
        _test_stream_transport_records_first_text_without_network,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"  FAILED: {type(exc).__name__}: {exc}")
    passed = len(tests) - failed
    print(f"smoke_llm_latency_v1: {passed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_all())
