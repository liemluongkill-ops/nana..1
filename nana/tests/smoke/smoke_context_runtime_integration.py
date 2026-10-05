"""Independent fake-only regressions for Context Runtime Phase 1/2 wiring.

The smoke imports the real legacy prompt builders but replaces every transport
and lane dependency with in-memory fakes.  Captured prompts never leave RAM.
"""

from __future__ import annotations

import importlib
import hashlib
import json
from pathlib import Path
import re
import socket
import sys
import types
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRIVATE_SENTINEL = "PRIVATE-SMOKE-SENTINEL"
REAL_PROVIDER_CALLS = 0


def _module(name: str, **values):
    module = types.ModuleType(name)
    module.__dict__.update(values)
    sys.modules[name] = module
    return module


def _forbidden(name):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"public smoke touched private dependency: {name}")

    return fail


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _Response:
    def __init__(self, text):
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content=text))]


def _install_fake_dependencies(
    *,
    private_mode="legacy",
    public_mode="legacy",
    budget_revision="",
):
    """Install only in-memory modules needed by the public legacy builder."""

    nana_package = _module("nana")
    nana_package.__path__ = [str(ROOT / "nana")]
    for package_name, relative in (
        ("nana.brain", "brain"),
        ("nana.runtime", "runtime"),
        ("nana.voice", "voice"),
    ):
        package = _module(package_name)
        package.__path__ = [str(ROOT / "nana" / relative)]

    _module(
        "nana.config",
        LLM_CHAT_MAX_TOKENS=200,
        LLM_COMPACT_PRIVATE_PROMPT_ENABLED=False,
        LLM_FAST_PRIVATE_MAX_TOKENS=80,
        LLM_PROMPT_MEMORY_RULE_LIMIT=5,
        LLM_PROMPT_RECENT_CHAT_LINES=5,
        LLM_PROMPT_RETRIEVAL_LIMIT=5,
        LLM_PROMPT_SHORT_TERM_LINES=5,
        LLM_STORY_MAX_TOKENS=300,
        LLMGATE_CHEAP_MODEL="fake",
        LLMGATE_FALLBACK_MODELS=[],
        LLMGATE_MAIN_MODEL="fake",
        LLMGATE_PUBLIC_FALLBACK_MODELS=[],
        LLMGATE_PUBLIC_MODEL="fake-public",
        NANA_CHAT_PROVIDER="llmgate",
        NANA_CONTEXT_PRIVATE_MODE=private_mode,
        NANA_CONTEXT_PUBLIC_GPT_MODE=public_mode,
        NANA_CONTEXT_CUM2_MODE="legacy",
        NANA_CONTEXT_AUTONOMY_MODE="legacy",
        NANA_CONTEXT_BUDGET_POLICY_REVISION=budget_revision,
        NANA_OPENAI_FALLBACK_ENABLED=False,
        NANA_PERSONALITY="PUBLIC CORE",
        NANA_SHARED_HISTORY=PRIVATE_SENTINEL,
        OPENAI_API_KEY="fake-key",
        OPENAI_FALLBACK_MODELS=[],
        OPENAI_MODEL="fake",
    )
    _module("openai", OpenAI=lambda **_kwargs: object())
    _module(
        "nana.memory",
        load_recent_chat=_forbidden("recent chat"),
        memory={"emotion": {"affection": 1}, "short_term": [], "long_term": []},
        memory_lock=_Lock(),
    )
    _module("nana.runtime.history_privacy", redact_history_text=lambda value: value)
    _module("nana.runtime.context", context_snapshot=_forbidden("context"))
    _module(
        "nana.runtime.identity",
        load_identity=_forbidden("identity"),
        resolve_user=_forbidden("users"),
        format_identity_block=_forbidden("identity block"),
    )
    _module(
        "nana.runtime.live_awareness",
        build_live_awareness_snapshot=_forbidden("awareness"),
        format_live_awareness_prompt=_forbidden("awareness prompt"),
    )
    _module(
        "nana.runtime.awareness_memory",
        get_awareness_memory=_forbidden("awareness memory"),
        get_deterministic_browser_answer=lambda _aw: "",
        get_deterministic_temporal_answer=lambda *_a, **_k: "",
        get_ground_truth_object=lambda *_a, **_k: {},
        format_surface_phrase=lambda *_a, **_k: "",
        guard_with_fallback=lambda *_a, **_k: "",
        ContinuityTracker=lambda: types.SimpleNamespace(record_mention=lambda *_a: None),
        clean_browser_title=lambda value: value,
        is_bad_browser_title=lambda _value: False,
    )
    _module(
        "nana.runtime.memory_spine",
        get_memory_spine=_forbidden("memory spine"),
        format_prompt_memory_rules=_forbidden("memory rules"),
    )
    _module("nana.runtime.logger", log_event=lambda *_a, **_k: None)

    class _Affect:
        def as_emotion_dict(self):
            return {"affection": 0.4, "annoyance": 0.0, "playfulness": 0.6}

    _module(
        "nana.runtime.affect_lane",
        project_affect=lambda *_a: _Affect(),
        format_affect_prompt_block=lambda _a: "PUBLIC AFFECT",
    )
    _module("nana.runtime.persona", persona_prompt_block=_forbidden("private persona governor"))
    _module(
        "nana.runtime.llm_private_fast_lane",
        build_fast_private_messages=_forbidden("private fast messages"),
        classify_private_fast_lane=lambda *_a, **_k: types.SimpleNamespace(eligible=False),
        prompt_char_count=lambda *_a: 0,
        record_fast_lane_decision=lambda *_a: None,
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
    _module(
        "nana.runtime.mood_continuity",
        format_mood_prompt_block=_forbidden("private mood"),
        observe_mood_text=_forbidden("private mood write"),
    )
    _module(
        "nana.runtime.persona_spine",
        generate_spine_block=_forbidden("persona spine"),
    )
    _module(
        "nana.runtime.core_self",
        generate_core_self_block=_forbidden("core self"),
        get_core_self=_forbidden("core self singleton"),
    )
    _module(
        "nana.runtime.public_voice_style",
        generate_public_voice_block=lambda *_a: "PUBLIC VOICE",
        public_identity_boundary_reply=lambda **_k: "Nana public identity",
        public_full_reply_polish=lambda text, **_k: text,
        public_model_topic_reply=lambda **_k: "Nana public model",
        public_quiet_room_reply=lambda **_k: "Nana public quiet",
        public_service_boundary_reply=lambda **_k: "Nana public boundary",
    )
    _module(
        "nana.runtime.memory_grounding",
        MemoryEvidence=object,
        ConfidenceInjector=object,
        ConfidenceVerifier=object,
        VerificationResult=object,
        ground_user_message=_forbidden("private grounding"),
        verify_reply=lambda *_a: None,
    )

    _module(
        "nana.runtime.social_session",
        get_social_session=lambda: types.SimpleNamespace(
            _topic_stack=[],
            format_public_room_context=lambda *_a, **_k: "",
        ),
    )
    _module("nana.runtime.public_stage_identity")
    captures = {"sync": [], "stream": []}
    llmgate = _module("nana.brain.llmgate_client")
    llmgate.call_llmgate_messages = lambda *_a, **_k: ("fake", {})

    def compiled_sync(compiled, **_kwargs):
        captures["sync"].append(
            [
                {"role": message.role, "content": message.content}
                for message in compiled.messages
            ]
        )
        return "fake sync reply", "ok", types.SimpleNamespace(status="complete")

    def compiled_stream(compiled, **_kwargs):
        captures["stream"].append(
            [
                {"role": message.role, "content": message.content}
                for message in compiled.messages
            ]
        )
        yield "fake stream reply"

    llmgate.call_llmgate_compiled = compiled_sync
    llmgate.stream_llmgate_compiled = compiled_stream
    _module(
        "nana.runtime.async_stream_bridge",
        iterate_blocking=lambda source: source,
    )

    for name in (
        "nana.runtime.context_shadow",
        "nana.runtime.context_telemetry",
        "nana.runtime.context_runtime",
        "nana.runtime.context_compiler",
        "nana.runtime.context_budget",
        "nana.runtime.context_contracts",
        "nana.runtime.context_adapters",
        "nana.runtime.persona_boundary",
        "nana.brain.gpt",
    ):
        sys.modules.pop(name, None)
    gpt = importlib.import_module("nana.brain.gpt")

    def sync_provider(**kwargs):
        captures["sync"].append(kwargs["messages"])
        return _Response("fake sync reply")

    def stream_provider(**kwargs):
        captures["stream"].append(kwargs["messages"])
        yield "fake stream reply"

    gpt.create_chat_completion_with_fallback = sync_provider
    llmgate.stream_llmgate_messages = stream_provider
    return gpt, captures


def _public_kwargs():
    return {
        "viewer_name": "legacy-viewer",
        "stream_mode": True,
        "public_platform": "youtube",
        "metadata": {
            "author_id": "fixture-author",
            "room_id": "fixture-room",
            "stream_session_id": "fixture-session",
            "event_id": "fixture-event",
            "display_name": "ViewerLabel",
            "public_metadata": {"topic": "fixture-topic"},
        },
    }


def _strict_public_kwargs():
    kwargs = _public_kwargs()
    kwargs["viewer_name"] = "ViewerLabel"
    return kwargs


def _install_private_dependencies(*, private_mode):
    gpt, captures = _install_fake_dependencies(private_mode=private_mode)
    reads = {
        "context": 0,
        "awareness": 0,
        "retrieval": 0,
        "checkpoint": 0,
        "history": 0,
        "identity": 0,
    }

    def context_snapshot():
        reads["context"] += 1
        return {
            "active_zone": "fixture-zone",
            "active_app": "fixture-app",
            "idle_state": "active",
            "time": {},
            "browser": {},
        }

    def awareness_snapshot(_context):
        reads["awareness"] += 1
        return {"fixture": "awareness"}

    def retrieval(_text):
        reads["retrieval"] += 1
        return "PRIVATE RETRIEVAL"

    def checkpoint(_boundary):
        reads["checkpoint"] += 1
        return "PRIVATE CHECKPOINT"

    def recent_chat(_limit):
        reads["history"] += 1
        return "PRIVATE HISTORY"

    def resolve_user(**_kwargs):
        reads["identity"] += 1
        return {"name": "Ba"}

    gpt.context_snapshot = context_snapshot
    gpt.build_live_awareness_snapshot = awareness_snapshot
    gpt.format_live_awareness_prompt = lambda _awareness: "PRIVATE AWARENESS"
    gpt._private_memory_retrieval_block = retrieval
    gpt._private_session_checkpoint_block = checkpoint
    gpt.load_recent_chat = recent_chat
    gpt.resolve_user = resolve_user
    gpt.format_identity_block = lambda *_a, **_k: "PRIVATE IDENTITY"
    gpt.format_prompt_memory_rules = lambda *_a, **_k: "PRIVATE RULES"
    gpt.persona_prompt_block = lambda **_kwargs: "PRIVATE PERSONA GOVERNOR"
    gpt._identity_prompt_blocks_for_boundary = (
        lambda _boundary: ("PRIVATE CORE SELF", "PRIVATE PERSONA", "")
    )
    gpt._mood_prompt_for_boundary = lambda _boundary: "PRIVATE MOOD"
    gpt._observe_mood_for_turn = lambda *_a, **_k: None
    gpt.memory = {
        "emotion": {"affection": 0.5, "annoyance": 0.0, "playfulness": 0.5},
        "short_term": ["PRIVATE SHORT"],
        "long_term": [],
    }
    sys.modules["nana.memory"].memory = gpt.memory
    gpt.redact_history_text = lambda value: str(value).replace(
        "PRIVATE-RAW-SECRET", "[redacted]"
    )
    evidence = types.SimpleNamespace(marker="fixture-evidence")
    gpt._private_memory_evidence_for_turn = lambda *_a, **_k: evidence

    class _Injector:
        @staticmethod
        def inject_into_messages(messages, _evidence):
            return [
                {
                    **message,
                    "content": (
                        message["content"] + "\nPRIVATE-EVIDENCE-PRIVATE-RAW-SECRET"
                        if index == 0
                        else message["content"]
                    ),
                }
                for index, message in enumerate(messages)
            ]

    gpt._GROUNDING_AVAILABLE = True
    gpt.ConfidenceInjector = _Injector
    gpt.grounding_verify_reply = lambda *_a, **_k: types.SimpleNamespace(
        passed=True,
        suggested_fallback="",
    )

    async def iterate_blocking(source):
        for item in source:
            yield item

    sys.modules["nana.runtime.async_stream_bridge"].iterate_blocking = iterate_blocking
    return gpt, captures, reads


def _exact_message_hash(messages):
    projection = json.dumps(
        [[message["role"], message["content"]] for message in messages],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(projection).hexdigest()


def _run_immediate(coroutine):
    """Drive fake-only async work that must never suspend externally."""
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    coroutine.close()
    raise AssertionError("offline stream capture attempted external async work")


def _run_stream(gpt, text, **kwargs):
    async def consume():
        return "".join([part async for part in gpt.ask_gpt_stream(text, **kwargs)])

    return _run_immediate(consume())


def test_public_stage_identity_uses_scope_display_name_not_user_text():
    gpt, captures = _install_fake_dependencies()
    text = "MESSAGE-MUST-NOT-BECOME-VIEWER-NAME"
    builder = sys.modules["nana.runtime.public_stage_identity"]
    builder.build_public_stage_prompt_block = lambda **kwargs: (
        f"Viewer just said hi: {kwargs['viewer_name']}"
    )

    kwargs = _public_kwargs()
    assert gpt.ask_gpt(text, **kwargs)
    assert _run_stream(gpt, text, **kwargs)
    systems = [messages[0]["content"] for messages in captures["sync"] + captures["stream"]]
    assert systems, "fake transport did not capture sync/stream prompts"
    for system in systems:
        assert "Viewer just said hi: ViewerLabel" in system
        assert "Viewer just said hi: MESSAGE-MUST-NOT-BECOME-VIEWER-NAME" not in system

    captures["sync"].clear()
    captures["stream"].clear()
    boundary = types.SimpleNamespace(
        public=True,
        livestream=True,
        interaction_scope="public_viewer",
        prompt_block="PUBLIC-BOUNDARY",
        scope=None,
    )
    gpt._lane_first_inputs = lambda **_kwargs: (
        boundary,
        {"public_metadata": {}},
        {},
        {"affection": 0.5, "annoyance": 0.0, "playfulness": 0.55},
    )
    gpt._public_grounding_decision_for_turn = lambda *_args, **_kwargs: None
    legacy_kwargs = {**kwargs, "viewer_name": "LegacyResolvedLabel"}
    assert gpt.ask_gpt(text, **legacy_kwargs)
    assert _run_stream(gpt, text, **legacy_kwargs)
    fallback_systems = [
        messages[0]["content"]
        for messages in captures["sync"] + captures["stream"]
    ]
    assert len(fallback_systems) == 2, fallback_systems
    for system in fallback_systems:
        assert "Viewer just said hi: LegacyResolvedLabel" in system
        assert "Viewer just said hi: MESSAGE-MUST-NOT-BECOME-VIEWER-NAME" not in system


def test_livestream_boundary_section_compiled_exactly_once_sync_and_stream():
    gpt, captures = _install_fake_dependencies()
    marker = "BOUNDARY-SENTINEL-5412"
    boundary = types.SimpleNamespace(
        public=True,
        livestream=True,
        interaction_scope="public_viewer",
        prompt_block=marker,
        scope=types.SimpleNamespace(display_name="ViewerLabel"),
    )
    gpt._lane_first_inputs = lambda **_kwargs: (
        boundary,
        {"public_metadata": {}},
        {},
        {"affection": 0.5, "annoyance": 0.0, "playfulness": 0.55},
    )
    gpt._public_grounding_decision_for_turn = lambda *_args, **_kwargs: None
    sys.modules["nana.runtime.public_stage_identity"].build_public_stage_prompt_block = (
        lambda **_kwargs: ""
    )

    text = "CONTEXT-RUNTIME-BLOCKER-B-SYNTHETIC"
    kwargs = _public_kwargs()
    assert gpt.ask_gpt(text, **kwargs)
    assert _run_stream(gpt, text, **kwargs)
    systems = [messages[0]["content"] for messages in captures["sync"] + captures["stream"]]
    assert len(systems) == 2, systems
    for system in systems:
        assert system.count(marker) == 1, system.count(marker)


def test_lane_resolution_precedes_private_reads_and_keeps_tuple4_contract():
    gpt, _captures = _install_fake_dependencies(private_mode="legacy")
    shadow = importlib.import_module("nana.runtime.context_shadow")
    order = []
    real_resolve = shadow.resolve_context_turn

    def resolve_first(**kwargs):
        order.append("resolve")
        return real_resolve(**kwargs)

    shadow.resolve_context_turn = resolve_first
    gpt.context_snapshot = lambda: order.append("context") or {
        "active_zone": "fixture-zone",
        "active_app": "fixture-app",
        "idle_state": "active",
        "time": {},
        "browser": {},
    }
    gpt.build_live_awareness_snapshot = (
        lambda _context: order.append("awareness") or {"fixture": "awareness"}
    )
    gpt.memory = {
        "emotion": {"affection": 0.5, "annoyance": 0.0, "playfulness": 0.5},
        "short_term": [],
        "long_term": [],
    }
    sys.modules["nana.memory"].memory = gpt.memory

    current_input = "PRIVATE-TURN-REQUEST-MUST-NOT-ENTER-CONTEXT-REPR"
    result = gpt._lane_first_inputs(
        text=current_input,
        viewer_name=None,
        stream_mode=False,
        public_platform=None,
        metadata=None,
        story_mode=True,
        casual_mode=False,
        temporal_intent=True,
        grounding_intent=True,
    )

    assert type(result) is tuple and len(result) == 4
    boundary, context, awareness, emotion = result
    assert order[:3] == ["resolve", "context", "awareness"], order
    assert boundary.public is False
    assert context["active_zone"] == "fixture-zone"
    assert awareness == {"fixture": "awareness"}
    assert emotion["affection"] == 0.5
    assert current_input not in repr(context)
    assert all(not str(key).startswith("_context_runtime") for key in context)

    turn = shadow.context_turn_from_mapping(context)
    request = turn.resolved_request
    assert request.scope.lane.value == "private_owner"
    assert request.story_mode is True
    assert request.casual_mode is False
    assert request.temporal_intent is True
    assert request.grounding_intent is True


def test_legacy_mode_never_collects_or_compiles():
    gpt, captures = _install_fake_dependencies(public_mode="legacy")
    runtime = importlib.import_module("nana.runtime.context_runtime")
    compiler = importlib.import_module("nana.runtime.context_compiler")
    runtime.LaneAwareContextCollector.collect = _forbidden("legacy collection")
    compiler.ContextCompiler.compile = _forbidden("legacy compilation")

    text = "CONTEXT-RUNTIME-LEGACY-NO-COMPILE"
    kwargs = _strict_public_kwargs()
    assert gpt.ask_gpt(text, **kwargs)
    assert _run_stream(gpt, text, **kwargs)
    assert len(captures["sync"]) == 1
    assert len(captures["stream"]) == 1


def test_shadow_preserves_exact_payload_and_emits_truthful_views():
    text = "CONTEXT-RUNTIME-SHADOW-PARITY"
    kwargs = _strict_public_kwargs()

    legacy_gpt, legacy_captures = _install_fake_dependencies(public_mode="legacy")
    assert legacy_gpt.ask_gpt(text, **kwargs)
    assert _run_stream(legacy_gpt, text, **kwargs)
    legacy_payloads = (
        legacy_captures["sync"][0],
        legacy_captures["stream"][0],
    )

    shadow_gpt, shadow_captures = _install_fake_dependencies(public_mode="shadow")
    shadow = importlib.import_module("nana.runtime.context_shadow")
    observed_objects = []
    real_observe = shadow.observe_provider_bound_context

    def observe_identity(**call):
        observed_objects.append(call["messages"])
        return real_observe(**call)

    shadow.observe_provider_bound_context = observe_identity
    assert shadow_gpt.ask_gpt(text, **kwargs)
    assert _run_stream(shadow_gpt, text, **kwargs)
    shadow_payloads = (
        shadow_captures["sync"][0],
        shadow_captures["stream"][0],
    )

    assert shadow_payloads == legacy_payloads
    assert observed_objects == list(shadow_payloads)
    assert shadow_payloads[0] is observed_objects[0]
    assert shadow_payloads[1] is observed_objects[1]

    observations = shadow.shadow_telemetry_snapshot()
    assert len(observations) == 2, observations
    for observation, payload in zip(observations, shadow_payloads):
        assert observation.candidate_status == "available"
        assert observation.sent_context.label == "sent_context"
        assert observation.sent_context.production_bound is True
        assert observation.candidate_context.label == "candidate_context"
        assert observation.candidate_context.production_bound is False
        assert (
            observation.sent_context.manifest.full_context_hash
            == _exact_message_hash(payload)
        )
        assert observation.payload_unchanged is True
    rendered = repr(observations)
    assert text not in rendered
    assert PRIVATE_SENTINEL not in rendered


def test_shadow_keeps_legacy_public_scope_but_marks_candidate_unavailable():
    gpt, captures = _install_fake_dependencies(public_mode="shadow")
    shadow = importlib.import_module("nana.runtime.context_shadow")
    text = "CONTEXT-RUNTIME-LEGACY-PUBLIC-SCOPE"
    kwargs = _public_kwargs()

    assert gpt.ask_gpt(text, **kwargs)
    assert _run_stream(gpt, text, **kwargs)
    assert len(captures["sync"]) == 1 and len(captures["stream"]) == 1
    observations = shadow.shadow_telemetry_snapshot()
    assert len(observations) == 2
    for observation, payload in zip(
        observations,
        (captures["sync"][0], captures["stream"][0]),
    ):
        assert observation.candidate_status == "candidate_unavailable_scope"
        assert observation.candidate_context is None
        assert (
            observation.sent_context.manifest.full_context_hash
            == _exact_message_hash(payload)
        )


def test_canonical_mode_is_unavailable_before_provider_even_with_revision():
    gpt, captures = _install_fake_dependencies(
        public_mode="canonical",
        budget_revision="fabricated-approved-looking-revision",
    )
    text = "CONTEXT-RUNTIME-CANONICAL-MUST-STOP"
    kwargs = _strict_public_kwargs()

    for call in (
        lambda: gpt.ask_gpt(text, **kwargs),
        lambda: _run_stream(gpt, text, **kwargs),
    ):
        try:
            call()
        except Exception as exc:
            assert str(exc) == "budget_policy_unapproved", repr(exc)
        else:
            raise AssertionError("canonical mode reached the provider path")
    assert captures == {"sync": [], "stream": []}


def test_invalid_context_mode_is_rejected_before_provider():
    gpt, captures = _install_fake_dependencies(public_mode="invalid-mode")
    try:
        gpt.ask_gpt("CONTEXT-RUNTIME-INVALID-MODE", **_strict_public_kwargs())
    except Exception as exc:
        assert str(exc) == "invalid_context_mode", repr(exc)
    else:
        raise AssertionError("invalid context mode reached provider")
    assert captures == {"sync": [], "stream": []}


def test_private_shadow_matches_final_injected_and_redacted_legacy_payload():
    text = "PRIVATE-SHADOW-PARITY-PRIVATE-RAW-SECRET"
    legacy_gpt, legacy_captures, legacy_reads = _install_private_dependencies(
        private_mode="legacy"
    )
    assert legacy_gpt.ask_gpt(text)
    assert _run_stream(legacy_gpt, text)
    legacy_payloads = (
        legacy_captures["sync"][0],
        legacy_captures["stream"][0],
    )

    shadow_gpt, shadow_captures, shadow_reads = _install_private_dependencies(
        private_mode="shadow"
    )
    shadow = importlib.import_module("nana.runtime.context_shadow")
    assert shadow_gpt.ask_gpt(text)
    assert _run_stream(shadow_gpt, text)
    shadow_payloads = (
        shadow_captures["sync"][0],
        shadow_captures["stream"][0],
    )

    assert shadow_payloads == legacy_payloads
    assert shadow_reads == legacy_reads
    assert all(value == 2 for value in shadow_reads.values()), shadow_reads
    for payload in shadow_payloads:
        rendered = repr(payload)
        assert "PRIVATE-EVIDENCE-[redacted]" in rendered
        assert "PRIVATE-RAW-SECRET" not in rendered

    observations = shadow.shadow_telemetry_snapshot()
    assert len(observations) == 2
    for observation, payload in zip(observations, shadow_payloads):
        assert observation.lane.value == "private_owner"
        assert observation.candidate_status == "available"
        assert observation.candidate_context is not None
        assert (
            observation.sent_context.manifest.full_context_hash
            == _exact_message_hash(payload)
        )


def test_shadow_failures_preserve_exact_payload_and_provider_count():
    text = "CONTEXT-RUNTIME-SHADOW-FAILURE-ISOLATION"
    kwargs = _strict_public_kwargs()
    legacy_gpt, legacy_captures = _install_fake_dependencies(public_mode="legacy")
    assert legacy_gpt.ask_gpt(text, **kwargs)
    expected = legacy_captures["sync"][0]

    class _FailingSink:
        def emit(self, _receipt):
            raise RuntimeError("sink unavailable")

    cases = (
        ("candidate", "_compile_candidate"),
        ("sent", "emit_sent_context_telemetry"),
        ("record", "_record_observation"),
    )
    for label, attribute in cases:
        gpt, captures = _install_fake_dependencies(public_mode="shadow")
        shadow = importlib.import_module("nana.runtime.context_shadow")

        def fail(*_args, **_kwargs):
            raise RuntimeError(f"{label} unavailable")

        setattr(shadow, attribute, fail)
        assert gpt.ask_gpt(text, **kwargs)
        assert captures["sync"] == [expected]

    gpt, captures = _install_fake_dependencies(public_mode="shadow")
    shadow = importlib.import_module("nana.runtime.context_shadow")
    shadow.set_context_shadow_sink(_FailingSink())
    assert gpt.ask_gpt(text, **kwargs)
    assert captures["sync"] == [expected]


def test_reviewed_private_story_limits_measure_without_changing_messages():
    _install_fake_dependencies(private_mode="shadow", public_mode="shadow")
    shadow = importlib.import_module("nana.runtime.context_shadow")
    expected_revision = "private-budget-review.2026-10-01.r1"

    def observe(output, *, story, public=False):
        boundary, turn = shadow.resolve_context_turn(
            text="fixture current input", viewer_name="ViewerLabel" if public else None,
            stream_mode=public, public_platform="youtube" if public else None,
            metadata=_strict_public_kwargs()["metadata"] if public else None,
            private_model="fake", public_model="fake-public", story_mode=story,
            casual_mode=False, temporal_intent=False, grounding_intent=False,
        )
        factory = shadow.public_candidate_inputs if public else shadow.private_candidate_inputs
        inputs = factory(core="core", policy="policy", output_contract=output)
        compiled = shadow._compile_candidate(turn.resolved_request, inputs)
        messages = [{"role": row.role, "content": row.content} for row in compiled.messages]
        before = _exact_message_hash(messages)
        observation = shadow.observe_provider_bound_context(
            context=shadow.context_mapping_with_turn({}, turn), boundary=boundary,
            messages=messages, model="fake-public" if public else "fake",
            candidate_factory=lambda: inputs,
        )
        assert _exact_message_hash(messages) == before == observation.sent_context.manifest.full_context_hash
        assert observation.candidate_context.manifest.full_context_hash == compiled.full_context_hash
        assert observation.candidate_context.manifest.budget_enforced is False
        assert output in compiled.messages[0].content
        return turn, inputs, compiled, observation

    # 3,628 ASCII chars independently estimate to 1,210: the observed overflow
    # must clear for story only. The same payload must still overflow normal/public.
    _, _, story_compiled, story_observation = observe("a" * 3628, story=True)
    assert story_compiled.manifest.candidate_overflow is False
    assert story_compiled.manifest.budget_policy_revision == expected_revision
    assert story_compiled.manifest.budget_policy_status.value == "shadow_candidate"
    _, _, normal, normal_observation = observe("a" * 3628, story=False)
    _, _, public, public_observation = observe("a" * 3628, story=True, public=True)
    assert normal.manifest.candidate_overflow is True
    assert public.manifest.candidate_overflow is True
    assert public.manifest.budget_policy_revision == "provisional-shadow.2026-09-30"
    assert normal_observation.reviewed_section_budgets == ()
    assert public_observation.reviewed_section_budgets == ()
    measurement = story_observation.reviewed_section_budgets[0]
    assert (measurement.revision, measurement.section_id) == (expected_revision, "contract.output.private.v1")
    assert (measurement.chars, measurement.token_estimate) == (3628, 1210)
    assert (measurement.max_characters, measurement.max_estimated_tokens) == (6400, 1600)
    assert not measurement.character_overflow and not measurement.token_overflow

    # Exact boundary and +1 cases use the real compiler's rendered row sizes;
    # cap changes do not truncate or drop the required output instructions.
    for length, token_overflow, character_overflow in (
        (4800, False, False), (4801, True, False),
        (6400, True, False), (6401, True, True),
    ):
        _, _, compiled, observation = observe("a" * length, story=True)
        row = observation.reviewed_section_budgets[0]
        assert row.chars == length and row.token_estimate == (length + 2) // 3
        assert row.token_overflow is token_overflow
        assert row.character_overflow is character_overflow
        assert compiled.manifest.candidate_overflow is token_overflow

    for story, expected in ((False, (12000, 48000)), (True, (16000, 64000))):
        turn, _, _, _ = observe("output", story=story)
        budget = shadow._provisional_budget(turn.resolved_request)
        assert (budget.max_estimated_tokens, budget.max_characters) == expected


def _run_tests():
    tests = (
        test_public_stage_identity_uses_scope_display_name_not_user_text,
        test_livestream_boundary_section_compiled_exactly_once_sync_and_stream,
        test_lane_resolution_precedes_private_reads_and_keeps_tuple4_contract,
        test_legacy_mode_never_collects_or_compiles,
        test_shadow_preserves_exact_payload_and_emits_truthful_views,
        test_shadow_keeps_legacy_public_scope_but_marks_candidate_unavailable,
        test_canonical_mode_is_unavailable_before_provider_even_with_revision,
        test_invalid_context_mode_is_rejected_before_provider,
        test_private_shadow_matches_final_injected_and_redacted_legacy_payload,
        test_shadow_failures_preserve_exact_payload_and_provider_count,
        test_reviewed_private_story_limits_measure_without_changing_messages,
    )
    passed = failed = 0
    failures = []
    for test in tests:
        try:
            with mock.patch.object(
                socket.socket,
                "connect",
                side_effect=AssertionError("network disabled in smoke"),
            ), mock.patch.object(
                socket.socket,
                "connect_ex",
                side_effect=AssertionError("network disabled in smoke"),
            ), mock.patch(
                "socket.create_connection",
                side_effect=AssertionError("network disabled in smoke"),
            ):
                test()
        except Exception as exc:
            failed += 1
            failures.append((test.__name__, exc))
        else:
            passed += 1
            print(f"PASS {test.__name__}")
    for name, exc in failures:
        print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(
        f"SUMMARY: {passed} passed, {failed} failed, "
        f"real_provider_calls={REAL_PROVIDER_CALLS}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_tests())
