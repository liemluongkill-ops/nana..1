"""Isolated actual-call smoke for the Memory v2 public GPT boundary."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRIVATE = "PRIVATE_SENTINEL_NEVER_PUBLIC"


def _module(name: str, **values):
    module = types.ModuleType(name)
    module.__dict__.update(values)
    sys.modules[name] = module
    return module


def _forbidden(name):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"public path read private {name}: {PRIVATE}")
    return fail


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Choice:
    def __init__(self, text):
        self.message = types.SimpleNamespace(content=text)


class _Response:
    def __init__(self, text):
        self.choices = [_Choice(text)]


def _install_isolated_dependencies():
    nana_package = _module("nana")
    nana_package.__path__ = [str(ROOT / "nana")]
    for package_name, relative in (("nana.brain", "brain"), ("nana.runtime", "runtime"), ("nana.voice", "voice")):
        package = _module(package_name)
        package.__path__ = [str(ROOT / "nana" / relative)]
    config_values = {
        "LLM_CHAT_MAX_TOKENS": 200, "LLM_COMPACT_PRIVATE_PROMPT_ENABLED": False,
        "LLM_FAST_PRIVATE_MAX_TOKENS": 80, "LLM_PROMPT_MEMORY_RULE_LIMIT": 5,
        "LLM_PROMPT_RECENT_CHAT_LINES": 5, "LLM_PROMPT_RETRIEVAL_LIMIT": 5,
        "LLM_PROMPT_SHORT_TERM_LINES": 5, "LLM_STORY_MAX_TOKENS": 300,
        "LLMGATE_CHEAP_MODEL": "fake", "LLMGATE_FALLBACK_MODELS": [],
        "LLMGATE_MAIN_MODEL": "fake", "LLMGATE_PUBLIC_FALLBACK_MODELS": [],
        "LLMGATE_PUBLIC_MODEL": "fake-public", "NANA_CHAT_PROVIDER": "llmgate",
        "NANA_OPENAI_FALLBACK_ENABLED": False, "NANA_PERSONALITY": "PUBLIC CORE",
        "NANA_SHARED_HISTORY": PRIVATE, "OPENAI_API_KEY": "fake",
        "OPENAI_FALLBACK_MODELS": [], "OPENAI_MODEL": "fake",
    }
    _module("nana.config", **config_values)
    _module("openai", OpenAI=lambda **_kwargs: object())
    _module("nana.memory", load_recent_chat=_forbidden("recent chat"),
            memory={"emotion": {"affection": 1}, "short_term": [PRIVATE], "long_term": [PRIVATE]},
            memory_lock=_Lock())
    _module("nana.runtime.context", context_snapshot=_forbidden("context"))
    _module("nana.runtime.identity", load_identity=_forbidden("identity"),
            resolve_user=_forbidden("users"), format_identity_block=_forbidden("identity block"))
    _module("nana.runtime.live_awareness", build_live_awareness_snapshot=_forbidden("awareness"),
            format_live_awareness_prompt=_forbidden("awareness prompt"))
    _module("nana.runtime.awareness_memory",
            get_awareness_memory=_forbidden("awareness memory"),
            get_deterministic_browser_answer=lambda _aw: "",
            get_deterministic_temporal_answer=lambda *_a, **_k: "",
            get_ground_truth_object=lambda *_a, **_k: {},
            format_surface_phrase=lambda *_a, **_k: "", guard_with_fallback=lambda *_a, **_k: "",
            ContinuityTracker=lambda: types.SimpleNamespace(record_mention=lambda *_a: None),
            clean_browser_title=lambda value: value, is_bad_browser_title=lambda _value: False)
    _module("nana.runtime.memory_spine", get_memory_spine=_forbidden("memory spine"),
            format_prompt_memory_rules=_forbidden("memory rules"))
    _module("nana.runtime.logger", log_event=lambda *_a, **_k: None)

    class Affect:
        affection = .4; annoyance = 0.; playfulness = .6
        def as_emotion_dict(self):
            return {"affection": self.affection, "annoyance": self.annoyance, "playfulness": self.playfulness}
    _module("nana.runtime.affect_lane", project_affect=lambda *_a: Affect(),
            format_affect_prompt_block=lambda _a: "PUBLIC AFFECT")
    _module("nana.runtime.persona", persona_prompt_block=_forbidden("private persona governor"))
    _module("nana.runtime.llm_private_fast_lane",
            build_fast_private_messages=_forbidden("private fast messages"),
            classify_private_fast_lane=lambda *_a, **_k: types.SimpleNamespace(eligible=False),
            prompt_char_count=lambda *_a: 0, record_fast_lane_decision=lambda *_a: None,
            record_fast_lane_result=lambda *_a, **_k: None,
            validate_fast_private_reply=lambda value: value)
    _module("nana.voice.inline_audio_tags", ELEVEN_V3_INLINE_AUDIO_TAG_COMPACT_GUIDE="",
            ELEVEN_V3_INLINE_AUDIO_TAG_GUIDE="", KNOWN_INLINE_AUDIO_TAG_RE=__import__("re").compile(r"$^"),
            repair_malformed_inline_audio_tags=lambda value: value,
            strip_inline_audio_tags=lambda value: value)
    _module("nana.runtime.mood_continuity", format_mood_prompt_block=_forbidden("private mood"),
            observe_mood_text=_forbidden("private mood write"))
    _module("nana.runtime.persona_spine", generate_spine_block=_forbidden("persona spine"))
    _module("nana.runtime.core_self", generate_core_self_block=_forbidden("core self"),
            get_core_self=_forbidden("core self singleton"))
    _module("nana.runtime.public_voice_style", generate_public_voice_block=lambda *_a: "PUBLIC VOICE",
            public_identity_boundary_reply=lambda **_k: "Nana public identity",
            public_full_reply_polish=lambda text, **_k: text,
            public_model_topic_reply=lambda **_k: "Nana public model",
            public_quiet_room_reply=lambda **_k: "Nana public quiet",
            public_service_boundary_reply=lambda **_k: "Nana public boundary")
    _module("nana.runtime.memory_grounding", MemoryEvidence=object, ConfidenceInjector=object,
            ConfidenceVerifier=object, VerificationResult=object,
            ground_user_message=_forbidden("private grounding"), verify_reply=lambda *_a: None)

    captured = {"sync": [], "stream": []}
    llmgate = _module("nana.brain.llmgate_client")
    llmgate.call_llmgate_messages = lambda *_a, **_k: ("normal public response", {})
    def stream_llmgate_messages(**kwargs):
        captured["stream"].append(kwargs["messages"])
        yield "stream public response"
    llmgate.stream_llmgate_messages = stream_llmgate_messages
    _module("nana.runtime.social_session",
            get_social_session=lambda: types.SimpleNamespace(
                _topic_stack=[], format_public_room_context=lambda *a, **k: ""))
    _module("nana.runtime.public_stage_identity",
            build_public_stage_prompt_block=lambda **_k: "PUBLIC STAGE IDENTITY")

    for name in ("nana.runtime.persona_boundary", "nana.brain.gpt"):
        sys.modules.pop(name, None)
    gpt = importlib.import_module("nana.brain.gpt")
    def sync_provider(**kwargs):
        captured["sync"].append(kwargs["messages"])
        return _Response("normal public response")
    gpt.create_chat_completion_with_fallback = sync_provider
    return gpt, captured


def test_actual_sync_and_stream_prompts_are_public_only():
    gpt, captured = _install_isolated_dependencies()
    metadata = {"author_id": "viewer-7", "room_id": "room-a", "stream_session_id": "stream-1",
                "event_id": "evt-1", "public_metadata": {"topic": "public-topic"}}
    sync_reply = gpt.ask_gpt("tell me about public-topic", viewer_name="Alex", stream_mode=True,
                             public_platform="youtube", metadata=metadata)
    async def consume():
        return "".join([part async for part in gpt.ask_gpt_stream(
            "continue public-topic", viewer_name="Alex", stream_mode=True,
            public_platform="youtube", metadata=metadata)])
    stream_reply = asyncio.run(consume())
    assert sync_reply and stream_reply
    prompts = str(captured)
    assert "public-topic" in prompts
    assert PRIVATE not in prompts


def test_deterministic_and_empty_paths_are_safe():
    gpt, captured = _install_isolated_dependencies()
    common = {"viewer_name": "Alex", "stream_mode": True, "public_platform": "youtube"}
    for text in ("", "browser status?", "what did we just discuss?", "play music", "Ba đang mở trang web nào vậy?"):
        reply = gpt.ask_gpt(text, **common)
        assert reply and PRIVATE not in reply
    assert not captured["sync"], "bounded public early paths must not call the responder"


def test_public_temporal_recall_uses_available_public_room_context():
    gpt, captured = _install_isolated_dependencies()
    sys.modules['nana.runtime.social_session'].get_social_session = lambda: types.SimpleNamespace(
        format_public_room_context=lambda *a, **kw: 'PUBLIC ROOM: current_viewer cup COC-1928')
    common = dict(viewer_name='An mới', stream_mode=True, public_platform='youtube',
                  metadata={'author_id': 'a', 'room_id': 'r', 'stream_session_id': 's', 'event_id': 'e'})
    prompt = 'Tên chiếc cốc mình vừa nói là gì?'
    sync = gpt.ask_gpt(prompt, **common)
    async def consume():
        return ''.join([part async for part in gpt.ask_gpt_stream(prompt, **common)])
    stream = asyncio.run(consume())
    assert len(captured['sync']) == len(captured['stream']) == 1, (sync, stream)
    for messages in captured['sync'] + captured['stream']:
        assert 'COC-1928' in messages[0]['content']
        assert PRIVATE not in str(messages)


def test_metadata_less_scope_is_request_local_and_coherent():
    gpt, _captured = _install_isolated_dependencies()
    first = gpt._lane_first_inputs(text="one", viewer_name="Alex", stream_mode=True,
                                   public_platform="youtube", metadata=None)
    second = gpt._lane_first_inputs(text="two", viewer_name="Alex", stream_mode=True,
                                    public_platform="youtube", metadata=None)
    first_boundary, first_context, *_ = first
    second_boundary, second_context, *_ = second
    assert first_boundary.scope.event_id == first_context["event_id"]
    assert first_boundary.scope.identity.actor_key == first_context["actor_key"]
    assert first_boundary.scope.event_id != second_boundary.scope.event_id
    assert first_boundary.scope.identity.actor_key != second_boundary.scope.identity.actor_key
    explicit, explicit_context, *_ = gpt._lane_first_inputs(
        text="explicit", viewer_name="Alex", stream_mode=True, public_platform="YouTube",
        metadata={"author_id": 42, "room_id": "room-a", "stream_session_id": "stream-9", "event_id": "evt-9"},
    )
    assert explicit.scope.platform == "youtube"
    assert explicit.scope.event_id == explicit_context["event_id"] == "evt-9"
    assert explicit.scope.stream_session_id == explicit_context["stream_session_id"] == "stream-9"
    assert explicit.scope.identity.actor_key == explicit_context["actor_key"] == "youtube:42"
    assert "stream-9" not in explicit.scope.identity.actor_key


def test_final_sanitize_runs_after_livestream_finalizer():
    gpt, _captured = _install_isolated_dependencies()
    import nana.runtime.livestream_identity as live
    original = live.finalize_livestream_identity
    live.finalize_livestream_identity = lambda *_a, **_k: "Con chào Ba " + PRIVATE
    try:
        boundary, *_ = gpt._lane_first_inputs(text="hello", viewer_name="Alex", stream_mode=True,
                                              public_platform="youtube", metadata=None)
        reply = gpt._maybe_public_reply("Nana hello", user_text="hello", viewer_name="Alex", boundary=boundary)
    finally:
        live.finalize_livestream_identity = original
    assert "Ba" not in reply and PRIVATE in reply


def test_private_memory_claim_is_verified_before_stream_yield():
    gpt, _captured = _install_isolated_dependencies()
    import nana.brain.llmgate_client as client

    boundary = types.SimpleNamespace(
        public=False,
        livestream=False,
        interaction_scope="private_owner",
        prompt_block="PRIVATE BOUNDARY",
    )
    context = {
        "active_zone": "unknown",
        "active_app": None,
        "idle_state": "active",
        "time": {},
        "browser": {},
    }
    evidence = types.SimpleNamespace(
        status="user_claim_only",
        confidence=0.25,
        lane_visible_to="private_only",
        evidence_strength="weak",
        snippets=[],
    )
    fast_calls = []
    temporal_calls = []

    gpt._lane_first_inputs = lambda **_kwargs: (
        boundary,
        context,
        {},
        {"affection": 0.5, "annoyance": 0.0, "playfulness": 0.5},
    )
    gpt.ground_user_message = lambda *_args, **_kwargs: (True, evidence)
    gpt.classify_private_fast_lane = lambda *_args, **_kwargs: types.SimpleNamespace(
        eligible=True, model="must-not-run"
    )
    gpt.record_fast_lane_decision = lambda *_args, **_kwargs: None
    gpt.resolve_user = lambda **_kwargs: {"name": "Ba"}
    gpt.format_identity_block = lambda *_args, **_kwargs: "PRIVATE IDENTITY"
    gpt.format_live_awareness_prompt = lambda _awareness: ""
    gpt._identity_prompt_blocks_for_boundary = lambda _boundary: ("PRIVATE CORE", "", "")
    gpt._observe_mood_for_turn = lambda *_args, **_kwargs: None
    gpt._mood_prompt_for_boundary = lambda _boundary: ""
    gpt._get_spine = lambda: types.SimpleNamespace(build_retrieval_block=lambda *_args, **_kwargs: "")
    gpt._get_natural_style_hint = lambda: ""
    gpt.get_awareness_memory = lambda: types.SimpleNamespace(
        format_recent_moments_block=lambda **_kwargs: "",
        get_recent=lambda **_kwargs: [],
        format_timeline_summary=lambda **_kwargs: "",
    )
    gpt.persona_prompt_block = lambda **_kwargs: "PRIVATE GOVERNOR"
    gpt.load_recent_chat = lambda *_args, **_kwargs: ""
    gpt.format_prompt_memory_rules = lambda *_args, **_kwargs: ""
    gpt.is_temporal_question = lambda _text: True
    def forbidden_temporal(*_args, **_kwargs):
        temporal_calls.append(True)
        return "unsafe temporal shortcut"
    gpt.get_deterministic_temporal_answer = forbidden_temporal
    gpt.NANA_SHARED_HISTORY = ""
    gpt.ConfidenceInjector = types.SimpleNamespace(
        inject_into_messages=lambda messages, _evidence: messages
    )
    gpt.grounding_verify_reply = lambda _evidence, _reply, **_kwargs: types.SimpleNamespace(
        passed=False,
        suggested_fallback=(
            "Con không còn thấy đủ chi tiết đó trong ngữ cảnh hiện tại, "
            "nên con không nhớ rõ đâu Ba."
        ),
    )

    def forbidden_fast(*_args, **_kwargs):
        fast_calls.append(True)
        raise AssertionError("memory claim entered fast lane")

    def false_denial_stream(**_kwargs):
        yield "Ba chưa từng kể cho con nghe "
        yield "câu chuyện đó đâu nha."

    client.call_llmgate_messages = forbidden_fast
    client.stream_llmgate_messages = false_denial_stream

    async def consume():
        return [chunk async for chunk in gpt.ask_gpt_stream(
            "Lúc nãy Ba đã kể một chi tiết; con vật gì vậy?"
        )]

    chunks = asyncio.run(consume())
    reply = "".join(chunks).lower()
    assert fast_calls == [], fast_calls
    assert temporal_calls == [], temporal_calls
    assert len(chunks) == 1, chunks
    assert "chưa từng kể" not in reply, reply
    assert "ngữ cảnh hiện tại" in reply and "không nhớ rõ" in reply, reply

    gpt.create_chat_completion_with_fallback = lambda **_kwargs: _Response(
        "Ba chưa từng kể cho con nghe câu chuyện đó đâu nha."
    )
    sync_reply = gpt.ask_gpt("Lúc nãy Ba đã kể một chi tiết; con vật gì vậy?").lower()
    assert temporal_calls == [], temporal_calls
    assert "chưa từng kể" not in sync_reply, sync_reply
    assert "ngữ cảnh hiện tại" in sync_reply and "không nhớ rõ" in sync_reply, sync_reply


def test_public_model_fact_bypasses_opinion_fast_path():
    gpt, captured = _install_isolated_dependencies()
    common = {
        "viewer_name": "Alex",
        "stream_mode": True,
        "public_platform": "youtube",
    }
    prompt = "GPT là viết tắt của gì? Trả lời một câu ngắn."
    sync_reply = gpt.ask_gpt(prompt, **common)

    async def consume():
        return "".join([part async for part in gpt.ask_gpt_stream(prompt, **common)])

    stream_reply = asyncio.run(consume())
    assert sync_reply.lower() == "normal public response", sync_reply
    assert stream_reply.lower() == "stream public response", stream_reply
    assert len(captured["sync"]) == 1 and len(captured["stream"]) == 1, captured

    captured["sync"].clear()
    captured["stream"].clear()
    yes_no_prompt = "GPT có phải là AI không?"
    yes_no_sync = gpt.ask_gpt(yes_no_prompt, **common)
    async def consume_yes_no():
        return "".join([part async for part in gpt.ask_gpt_stream(yes_no_prompt, **common)])
    yes_no_stream = asyncio.run(consume_yes_no())
    assert yes_no_sync.lower() == "normal public response", yes_no_sync
    assert yes_no_stream.lower() == "stream public response", yes_no_stream
    assert len(captured["sync"]) == 1 and len(captured["stream"]) == 1, captured

    captured["sync"].clear()
    captured["stream"].clear()
    opinion = gpt.ask_gpt("5.6 mạnh hơn nhiều không Nana?", **common)
    assert "public model" in opinion.lower(), opinion
    assert captured["sync"] == [], captured


def run_all():
    tests = [test_actual_sync_and_stream_prompts_are_public_only,
             test_deterministic_and_empty_paths_are_safe,
             test_public_temporal_recall_uses_available_public_room_context,
             test_metadata_less_scope_is_request_local_and_coherent,
             test_final_sanitize_runs_after_livestream_finalizer,
             test_private_memory_claim_is_verified_before_stream_yield,
             test_public_model_fact_bypasses_opinion_fast_path]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    print(f"Memory v2 public prompt: {len(tests)-failures} passed, {failures} failed")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(run_all())
