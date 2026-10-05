"""Smoke tests for Nana's LLMGate model routing defaults."""

from __future__ import annotations

import os
import re
import sys
import types
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _module(name, **values):
    module = types.ModuleType(name)
    module.__dict__.update(values)
    sys.modules[name] = module
    return module


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Affect:
    def as_emotion_dict(self):
        return {"affection": 0.5, "annoyance": 0.0, "playfulness": 0.5}


def _forbidden(name):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"routing smoke touched {name}")

    return fail


def _install_isolated_dependencies():
    """Install fake owners before importing real route/product modules."""

    package_root = ROOT / "nana"
    nana = _module("nana")
    nana.__path__ = [str(package_root)]
    for name, relative in (
        ("nana.actions", "actions"),
        ("nana.brain", "brain"),
        ("nana.core", "core"),
        ("nana.runtime", "runtime"),
        ("nana.voice", "voice"),
    ):
        package = _module(name)
        package.__path__ = [str(package_root / relative)]
    _module(
        "nana.config",
        LLM_CHAT_MAX_TOKENS=420,
        LLM_COMPACT_PRIVATE_PROMPT_ENABLED=True,
        LLM_FAST_PRIVATE_MAX_TOKENS=80,
        LLM_PROMPT_MEMORY_RULE_LIMIT=12,
        LLM_PROMPT_RECENT_CHAT_LINES=8,
        LLM_PROMPT_RETRIEVAL_LIMIT=3,
        LLM_PROMPT_SHORT_TERM_LINES=6,
        LLM_STORY_MAX_TOKENS=1000,
        LLMGATE_CHEAP_MODEL="gpt-5.4-mini",
        LLMGATE_FALLBACK_MODELS=["gpt-5.4", "gpt-5.5", "gpt-5.4-mini", "gemini-3.5-flash"],
        LLMGATE_MAIN_MODEL="gemini-3-flash",
        LLMGATE_MAIN_REASONING_EFFORT="none",
        LLMGATE_PUBLIC_FALLBACK_MODELS=[
            "gemini-3-flash",
            "grok-4.20-0309-non-reasoning",
            "gemini-3.1-flash-lite",
        ],
        LLMGATE_PUBLIC_MODEL="gemini-3-flash",
        LLMGATE_SETTINGS_PATH="fixture-settings-never-read.json",
        LLMGATE_TIMEOUT=30.0,
        NANA_CHAT_PROVIDER="llmgate",
        NANA_CONTEXT_AUTONOMY_MODE="legacy",
        NANA_CONTEXT_BUDGET_POLICY_REVISION="",
        NANA_CONTEXT_CUM2_MODE="legacy",
        NANA_CONTEXT_PRIVATE_MODE="legacy",
        NANA_CONTEXT_PUBLIC_GPT_MODE="legacy",
        NANA_OPENAI_FALLBACK_ENABLED=False,
        NANA_PERSONALITY="fixture-personality",
        NANA_SHARED_HISTORY="fixture-shared-history",
        OPENAI_API_KEY="fixture-key",
        OPENAI_FALLBACK_MODELS=[],
        OPENAI_MODEL="fixture-openai",
    )
    _module("openai", OpenAI=lambda **_kwargs: object())
    _module(
        "nana.memory",
        load_recent_chat=_forbidden("production history"),
        memory={"emotion": {}, "short_term": [], "long_term": []},
        memory_lock=_Lock(),
    )
    _module("nana.runtime.history_privacy", redact_history_text=lambda value: value)
    _module("nana.runtime.context", context_snapshot=_forbidden("production context"))
    _module(
        "nana.runtime.identity",
        load_identity=lambda: {},
        resolve_user=lambda **_kwargs: {},
        format_identity_block=lambda *_a, **_k: "fixture identity",
    )
    _module(
        "nana.runtime.live_awareness",
        build_live_awareness_snapshot=lambda *_a, **_k: {},
        format_live_awareness_prompt=lambda *_a, **_k: "",
    )
    _module(
        "nana.runtime.awareness_memory",
        get_awareness_memory=lambda: types.SimpleNamespace(),
        get_deterministic_browser_answer=lambda *_a, **_k: "",
        get_deterministic_temporal_answer=lambda *_a, **_k: "",
        get_ground_truth_object=lambda *_a, **_k: {},
        format_surface_phrase=lambda *_a, **_k: "",
        guard_with_fallback=lambda value, *_a, **_k: value,
        ContinuityTracker=lambda: types.SimpleNamespace(record_mention=lambda *_a: None),
        clean_browser_title=lambda value: value,
        is_bad_browser_title=lambda _value: False,
    )
    _module(
        "nana.runtime.memory_spine",
        get_memory_spine=lambda: types.SimpleNamespace(),
        format_prompt_memory_rules=lambda *_a, **_k: "",
    )
    _module("nana.runtime.logger", log_event=lambda *_a, **_k: None)
    _module(
        "nana.runtime.affect_lane",
        format_affect_prompt_block=lambda *_a, **_k: "",
        project_affect=lambda *_a, **_k: _Affect(),
    )
    _module("nana.runtime.persona", persona_prompt_block=lambda *_a, **_k: "")
    _module(
        "nana.runtime.llm_private_fast_lane",
        build_fast_private_messages=_forbidden("fast prompt"),
        classify_private_fast_lane=lambda *_a, **_k: types.SimpleNamespace(eligible=False),
        prompt_char_count=lambda *_a, **_k: 0,
        record_fast_lane_decision=lambda *_a, **_k: None,
        record_fast_lane_result=lambda *_a, **_k: None,
        validate_fast_private_reply=lambda value: value,
    )
    _module(
        "nana.voice.inline_audio_tags",
        ELEVEN_V3_INLINE_AUDIO_TAG_COMPACT_GUIDE="",
        ELEVEN_V3_INLINE_AUDIO_TAG_GUIDE="",
        KNOWN_INLINE_AUDIO_TAG_RE=re.compile(r"$^"),
        repair_malformed_inline_audio_tags=lambda value: value,
        strip_inline_audio_tags=lambda value: value,
    )
    _module("nana.runtime.mood_continuity", format_mood_prompt_block=lambda *_a: "", observe_mood_text=lambda *_a: None)
    _module("nana.runtime.persona_spine", generate_spine_block=lambda *_a: "")
    _module("nana.runtime.core_self", generate_core_self_block=lambda *_a: "", get_core_self=lambda: None)
    _module(
        "nana.runtime.memory_grounding",
        MemoryEvidence=object,
        ConfidenceInjector=object,
        ConfidenceVerifier=object,
        VerificationResult=object,
        ground_user_message=lambda *_a, **_k: None,
        verify_reply=lambda *_a, **_k: None,
        verify_public_response=lambda *_a, **_k: {"accepted": True},
    )
    _module(
        "nana.runtime.social_session",
        get_social_session=lambda: types.SimpleNamespace(
            _topic_stack=[], format_public_room_context=lambda *_a, **_k: ""
        ),
    )
    _module("nana.runtime.avatar_reply_turn", daily_reaction_prompt=lambda: "")


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

    aliases = {
        "NANA_LLM_ALIAS_MAIN": "gemini-3-flash",
        "NANA_LLM_ALIAS_CHAT": "gemini-3-flash",
        "NANA_LLM_ALIAS_PUBLIC": "gemini-3-flash",
        "NANA_LLM_ALIAS_BANTER": "gpt-5.4-mini",
        "NANA_LLM_ALIAS_REFINER": "gpt-5.5",
        "NANA_LLM_ALIAS_REASONING": "gpt-5.4",
    }
    with patch.dict(os.environ, aliases):
        assert _candidate_model_names("nana-main")[-1] == "gemini-3-flash"
        assert _candidate_model_names("nana-chat")[-1] == "gemini-3-flash"
        assert _candidate_model_names("nana-public")[-1] == "gemini-3-flash"
        assert _candidate_model_names("nana-banter")[-1] == "gpt-5.4-mini"
        assert _candidate_model_names("nana-refiner")[-1] == "gpt-5.5"
        assert _candidate_model_names("nana-reasoning")[-1] == "gpt-5.4"
    print("  PASSED")


def _ask_public_without_owner_reads(gpt, boundary, text):
    with (
        patch.object(
            gpt,
            "_lane_first_inputs",
            lambda **_kwargs: (
                boundary,
                {},
                {},
                {"affection": 0.5, "annoyance": 0.0, "playfulness": 0.5},
            ),
        ),
        patch.object(gpt, "_is_canonical_private_context", lambda *_args: False),
        patch.object(
            gpt,
            "_public_grounding_decision_for_turn",
            lambda *_args, **_kwargs: None,
        ),
        patch.object(
            gpt,
            "_public_cross_session_query_eligible",
            lambda *_args, **_kwargs: False,
        ),
    ):
        return gpt.ask_gpt(text, viewer_name="linhcute2746", stream_mode=True)


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

    reply = _ask_public_without_owner_reads(gpt, public, "5.6 mạnh không nana")
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
    from nana.runtime.persona_boundary import resolve_persona_boundary

    prompt = "phòng nay im quá"
    public = resolve_persona_boundary(viewer_name="linhcute2746", stream_mode=True)
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
        reply = _ask_public_without_owner_reads(gpt, public, prompt)
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
    missing = object()
    openai_before = sys.modules.get("openai", missing)
    _install_isolated_dependencies()
    import nana.brain.llmgate_client as client

    synthetic_settings = {
        "customModels": [
            {
                "model": "fixture-base-model",
                "displayName": "fixture-base-model",
                "apiKey": "fixture-key",
                "baseUrl": "https://llmgate.invalid/v1",
            }
        ]
    }
    original_settings_loader = client.load_llmgate_settings
    original_post = client._http_post
    client.load_llmgate_settings = lambda: synthetic_settings
    client._http_post = _forbidden("network")
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
    try:
        for test in tests:
            try:
                test()
            except Exception as exc:
                failed += 1
                print(f"  FAILED: {type(exc).__name__}: {exc}")
    finally:
        client.load_llmgate_settings = original_settings_loader
        client._http_post = original_post
        if openai_before is missing:
            sys.modules.pop("openai", None)
        else:
            sys.modules["openai"] = openai_before
    passed = len(tests) - failed
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_all())
