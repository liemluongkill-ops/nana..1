"""Smoke tests for Nana's LLMGate model routing defaults."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_core_llmgate_models_are_upgraded():
    print("[LLMGate Routing] Test 1: core and cheap models use upgraded LLMGate IDs...")
    from nana.config import (
        LLMGATE_CHEAP_MODEL,
        LLMGATE_FALLBACK_MODELS,
        LLMGATE_MAIN_MODEL,
        LLMGATE_PUBLIC_FALLBACK_MODELS,
        LLMGATE_PUBLIC_MODEL,
    )

    assert LLMGATE_MAIN_MODEL == "gemini-3-flash"
    assert LLMGATE_CHEAP_MODEL == "gpt-5.4-mini"
    assert LLMGATE_PUBLIC_MODEL == LLMGATE_MAIN_MODEL
    assert LLMGATE_PUBLIC_FALLBACK_MODELS[:3] == [
        "gemini-3-flash",
        "grok-4.20-0309-non-reasoning",
        "gemini-3.1-flash-lite",
    ]
    assert LLMGATE_FALLBACK_MODELS[:2] == ["gpt-5.4", "gpt-5.5"]
    print("  PASSED")


def _test_llmgate_aliases_resolve_without_api_call():
    print("[LLMGate Routing] Test 2: logical aliases resolve without API calls...")
    from nana.brain.llmgate_client import _candidate_model_names

    assert _candidate_model_names("nana-main")[-1] == "gemini-3-flash"
    assert _candidate_model_names("nana-chat")[-1] == "gemini-3-flash"
    assert _candidate_model_names("nana-public")[-1] == "gpt-5.4-mini"
    assert _candidate_model_names("nana-banter")[-1] == "gpt-5.4-mini"
    assert _candidate_model_names("nana-refiner")[-1] == "gpt-5.5"
    assert _candidate_model_names("nana-reasoning")[-1] == "gpt-5.4"
    print("  PASSED")


def _test_sidecar_router_uses_upgraded_models():
    print("[LLMGate Routing] Test 3: sidecar router uses upgraded model tiers...")
    from nana.brain.model_router import CHEAP_MODEL, NUCLEAR_MODEL, REASONING_MODEL

    assert CHEAP_MODEL == "gpt-5.4-mini"
    assert REASONING_MODEL == "gpt-5.4"
    assert NUCLEAR_MODEL == "gpt-5.5"
    print("  PASSED")


def _test_public_lane_uses_chat_model_and_local_model_topic_reply():
    print("[LLMGate Routing] Test 4: public lane uses the regular chat model and local model-topic reply...")
    import nana.brain.gpt as gpt
    from nana.runtime.persona_boundary import resolve_persona_boundary

    public = resolve_persona_boundary(viewer_name="linhcute2746", stream_mode=True)
    private = resolve_persona_boundary(viewer_name=None, stream_mode=False)

    assert gpt.llmgate_model_for_boundary(public) == "gemini-3-flash"
    assert gpt.llmgate_public_model_order() == [
        "gemini-3-flash",
        "grok-4.20-0309-non-reasoning",
        "gemini-3.1-flash-lite",
        "gpt-5.4-mini",
    ]
    assert gpt.llmgate_model_for_boundary(private) == "gemini-3-flash"
    assert gpt._is_public_model_topic("5.6 mạnh không nana") is True

    reply = gpt.ask_gpt("5.6 mạnh không nana", viewer_name="linhcute2746", stream_mode=True)
    lowered = reply.lower()
    assert "nana" in lowered, reply
    assert any(marker in lowered for marker in ("model", "5.6", "mạch", "nhịp", "tự nhiên", "bảng")), reply
    assert "quầy hỗ trợ" not in lowered, reply
    print("  PASSED")


def _test_reasoning_effort_is_scoped_to_gpt56_private_main():
    print("[LLMGate Routing] Test 7: reasoning effort is scoped to GPT-5.6 private main...")
    from nana.brain.llmgate_client import (
        apply_llmgate_reasoning_effort,
        resolve_llmgate_reasoning_effort,
    )

    assert resolve_llmgate_reasoning_effort("gemini-3-flash") is None
    assert resolve_llmgate_reasoning_effort("nana-chat") is None
    assert resolve_llmgate_reasoning_effort("gpt-5.6-terra") is None
    assert resolve_llmgate_reasoning_effort("gpt-5.4") is None
    assert resolve_llmgate_reasoning_effort("gpt-5.4-mini") is None
    assert resolve_llmgate_reasoning_effort("gpt-5.6-luna") is None

    # Retain the GPT-specific effort behavior for an explicit owner rollback.
    with patch("nana.brain.llmgate_client.LLMGATE_MAIN_MODEL", "gpt-5.6-terra"):
        assert resolve_llmgate_reasoning_effort("gpt-5.6-terra") == "none"

    main_payload = {}
    legacy_payload = {}
    apply_llmgate_reasoning_effort(main_payload, "nana-chat")
    apply_llmgate_reasoning_effort(legacy_payload, "gpt-5.4-mini")
    assert main_payload == {}, main_payload
    assert legacy_payload == {}, legacy_payload
    print("  PASSED")


def _test_private_prompt_budget_defaults_are_compact():
    print("[LLMGate Routing] Test 10: private prompt budget is compact and bounded...")
    import nana.brain.gpt as gpt
    from nana.config import (
        LLM_CHAT_MAX_TOKENS,
        LLM_COMPACT_PRIVATE_PROMPT_ENABLED,
        LLM_PROMPT_MEMORY_RULE_LIMIT,
        LLM_PROMPT_RECENT_CHAT_LINES,
        LLM_PROMPT_RETRIEVAL_LIMIT,
        LLM_PROMPT_SHORT_TERM_LINES,
        LLM_STORY_MAX_TOKENS,
    )
    from nana.runtime.persona_boundary import resolve_persona_boundary

    assert LLM_COMPACT_PRIVATE_PROMPT_ENABLED is True
    assert LLM_PROMPT_SHORT_TERM_LINES == 6
    assert LLM_PROMPT_RECENT_CHAT_LINES == 8
    assert LLM_PROMPT_RETRIEVAL_LIMIT == 3
    assert LLM_PROMPT_MEMORY_RULE_LIMIT == 12
    assert LLM_CHAT_MAX_TOKENS == 420
    assert LLM_STORY_MAX_TOKENS == 1000

    private = resolve_persona_boundary()
    core, spine, public_voice = gpt._identity_prompt_blocks_for_boundary(private)
    assert "NANA PRIVATE CORE (compact)" in core
    assert spine == ""
    assert public_voice == ""
    assert gpt._reply_max_tokens(story_mode=False) == 420
    assert gpt._reply_max_tokens(story_mode=True) == 1000
    assert gpt._reply_max_tokens_for_text(
        "Nana nói một câu khoảng mười lăm từ",
        story_mode=False,
    ) == 80
    assert gpt._reply_max_tokens_for_text(
        "Nana nói khoảng một trăm từ, không quá 110 từ",
        story_mode=False,
    ) == 266
    assert gpt._reply_max_tokens_for_text(
        "Nana phân tích kỹ đoạn code này",
        story_mode=False,
    ) == 420
    assert gpt._reply_max_tokens_for_text(
        "Nana kể một câu chuyện dài",
        story_mode=True,
    ) == 1000
    print("  PASSED")


def _test_chat_payload_only_adds_effort_for_private_main():
    print("[LLMGate Routing] Test 8: chat payload only adds effort for private main...")
    import nana.brain.llmgate_client as client

    captured = []

    class FakeResponse:
        status_code = 200

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "Nana test ok."}}]}

    def fake_model(model_name):
        return {
            "apiKey": "test-key",
            "baseUrl": "https://llmgate.invalid/v1",
            "model": model_name,
        }

    def fake_post(url, *, headers, json, timeout):
        captured.append(dict(json))
        return FakeResponse()

    original_model_loader = client.load_llmgate_model
    original_post = client._http_post
    original_log_event = client.log_event
    try:
        client.load_llmgate_model = fake_model
        client._http_post = fake_post
        client.log_event = lambda *_args, **_kwargs: None
        messages = [{"role": "user", "content": "test"}]
        assert client.call_llmgate_messages("gemini-3-flash", messages)[0]
        assert client.call_llmgate_messages("gpt-5.4-mini", messages)[0]
    finally:
        client.load_llmgate_model = original_model_loader
        client._http_post = original_post
        client.log_event = original_log_event

    assert captured[0]["model"] == "gemini-3-flash", captured[0]
    assert "reasoning_effort" not in captured[0], captured[0]
    assert "reasoning_effort" not in captured[1], captured[1]
    print("  PASSED")


def _test_stream_payload_only_adds_effort_for_private_main():
    print("[LLMGate Routing] Test 9: SSE payload only adds effort for private main...")
    import nana.brain.llmgate_client as client

    captured = []

    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

    class FakeEvent:
        data = '{"choices":[{"delta":{"content":"Nana stream ok."}}]}'

    class FakeSSEClient:
        def __init__(self, _response):
            pass

        @staticmethod
        def events():
            yield FakeEvent()

    class FakeSSEModule:
        SSEClient = FakeSSEClient

    def fake_model(model_name):
        return {
            "apiKey": "test-key",
            "baseUrl": "https://llmgate.invalid/v1",
            "model": model_name,
        }

    def fake_post(url, *, headers, json, timeout, stream):
        captured.append(dict(json))
        return FakeResponse()

    original_model_loader = client.load_llmgate_model
    original_post = client._http_post
    original_log_event = client.log_event
    original_sseclient = sys.modules.get("sseclient")
    try:
        client.load_llmgate_model = fake_model
        client._http_post = fake_post
        client.log_event = lambda *_args, **_kwargs: None
        sys.modules["sseclient"] = FakeSSEModule()
        messages = [{"role": "user", "content": "test"}]
        assert list(client.stream_llmgate_messages("gemini-3-flash", messages))
        assert list(client.stream_llmgate_messages("gpt-5.4-mini", messages))
    finally:
        client.load_llmgate_model = original_model_loader
        client._http_post = original_post
        client.log_event = original_log_event
        if original_sseclient is None:
            sys.modules.pop("sseclient", None)
        else:
            sys.modules["sseclient"] = original_sseclient

    assert captured[0]["model"] == "gemini-3-flash", captured[0]
    assert "reasoning_effort" not in captured[0], captured[0]
    assert "reasoning_effort" not in captured[1], captured[1]
    print("  PASSED")


def _test_dashboard_model_can_reuse_llmgate_base_config():
    print("[LLMGate Routing] Test 6: dashboard-only public model can reuse LLMGate base config...")
    from nana.brain.llmgate_client import load_llmgate_model

    model = load_llmgate_model("gemini-3-flash")
    assert model is not None
    assert model.get("model") == "gemini-3-flash"
    assert model.get("apiKey")
    assert model.get("baseUrl")
    print("  PASSED")


def _test_public_short_quiet_room_routes_deterministically():
    print("[LLMGate Routing] Test 5A: public quiet-room routing is deterministic and local...")
    import nana.brain.gpt as gpt
    import nana.runtime.public_voice_style as public_voice_style

    prompt = "phòng nay im quá"
    fixed_time_ns = 123456789
    expected_seed = f"quiet:{prompt}:{fixed_time_ns}"
    expected = public_voice_style.choose_variant(
        public_voice_style.QUIET_ROOM_RESPONSE_VARIANTS,
        seed=expected_seed,
    )

    old_style = public_voice_style._PUBLIC_VOICE_STYLE
    old_time_ns = gpt.time.time_ns
    old_completion = gpt.create_chat_completion_with_fallback
    try:
        public_voice_style._PUBLIC_VOICE_STYLE = public_voice_style.PublicVoiceStyle()
        gpt.time.time_ns = lambda: fixed_time_ns

        def fail_if_provider_called(**_kwargs):
            raise AssertionError("quiet-room local route attempted a provider call")

        gpt.create_chat_completion_with_fallback = fail_if_provider_called
        assert gpt._is_public_short_quiet_room_prompt(prompt) is True
        reply = gpt.ask_gpt(
            prompt,
            viewer_name="linhcute2746",
            stream_mode=True,
        )
    finally:
        public_voice_style._PUBLIC_VOICE_STYLE = old_style
        gpt.time.time_ns = old_time_ns
        gpt.create_chat_completion_with_fallback = old_completion

    assert reply == expected, (reply, expected)
    print(f"  fixed_time_ns={fixed_time_ns} | provider_call=False | PASSED")


def _test_public_quiet_room_seeded_quality_contract():
    print("[LLMGate Routing] Test 5B: quiet-room variants satisfy the quality contract...")
    import nana.runtime.public_voice_style as public_voice_style

    style = public_voice_style.PublicVoiceStyle()
    variants = set(public_voice_style.QUIET_ROOM_RESPONSE_VARIANTS)
    replies = [
        style.quiet_room_reply(seed=f"quiet-quality-{index}")
        for index in range(120)
    ]
    quiet_image_markers = ("phòng", "yên", "lặng", "im", "sân khấu", "chat")
    concrete_topic_markers = (
        "hôm nay",
        "chi tiết",
        "chuyện",
        "mảnh",
        "mẩu",
        "kỳ kỳ",
        "đáng kể",
    )

    assert set(replies) == variants, (len(set(replies)), len(variants))
    for reply in replies:
        lowered = reply.lower()
        assert 80 <= len(reply) <= 140, reply
        assert "nana" in lowered, reply
        assert any(marker in lowered for marker in quiet_image_markers), reply
        assert any(marker in lowered for marker in concrete_topic_markers), reply
        assert not any(
            pattern.search(reply)
            for pattern in public_voice_style.SOFT_SERVICE_PATTERNS
        ), reply
        assert not any(
            pattern.search(reply.strip())
            for pattern in public_voice_style.TOO_POLITE_ENDINGS
        ), reply
    print(
        f"  seeded_quality={len(replies)}/{len(replies)} | "
        f"variant_coverage={len(set(replies))}/{len(variants)} | PASSED"
    )


def run_all() -> int:
    print("=" * 60)
    print("LLMGate Model Routing — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_core_llmgate_models_are_upgraded,
        _test_llmgate_aliases_resolve_without_api_call,
        _test_sidecar_router_uses_upgraded_models,
        _test_public_lane_uses_chat_model_and_local_model_topic_reply,
        _test_public_short_quiet_room_routes_deterministically,
        _test_public_quiet_room_seeded_quality_contract,
        _test_dashboard_model_can_reuse_llmgate_base_config,
        _test_reasoning_effort_is_scoped_to_gpt56_private_main,
        _test_private_prompt_budget_defaults_are_compact,
        _test_chat_payload_only_adds_effort_for_private_main,
        _test_stream_payload_only_adds_effort_for_private_main,
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
