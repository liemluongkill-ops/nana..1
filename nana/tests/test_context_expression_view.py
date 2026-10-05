"""Task 9 canonical ExpressionView ownership and integration regressions."""

from __future__ import annotations

import asyncio
import builtins
import copy
from contextlib import nullcontext
from dataclasses import FrozenInstanceError
import importlib
import math
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from nana.runtime import context_adapters, context_runtime, mood_continuity, persona
from nana.runtime.context_compiler import registry_slot_ids
from nana.runtime.context_contracts import (
    CanonicalPublicIdentity,
    ContextContractError,
    ContextSection,
    Freshness,
    FrozenMapping,
    Lane,
    Lifetime,
    Route,
    SemanticRole,
    SourceRef,
    SourceSnapshot,
    UnresolvedContextRequest,
)
from nana.runtime.public_context_boundary import PublicEventScope


OPEN_GUIDANCE = (
    "Answer the direct task first. Keep the response warm and natural, use "
    "playfulness only when it fits, and do not invent memories or let stale "
    "context open a topic."
)
MODERATE_GUIDANCE = (
    "Prioritize the direct task. Keep warmth restrained and reduce humor, "
    "emoji, exaggeration, callbacks, and roleplay. Do not let stale context "
    "or mood open a topic."
)
STRICT_GUIDANCE = (
    "Prioritize the direct task. Be clear, concise, and actionable. Minimize "
    "humor, emoji, exaggeration, callbacks, and roleplay; do not let stale "
    "context or mood open a topic."
)


def _thaw(value):
    if isinstance(value, FrozenMapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


class _Authority:
    def __init__(self, lane):
        self.lane = lane

    def authorize(self, _request):
        public_scope = None
        if self.lane is Lane.PUBLIC_STAGE:
            public_scope = PublicEventScope(
                platform="youtube",
                room_id="expression-room",
                stream_session_id="expression-session",
                event_id="expression-event",
                display_name="Expression Viewer",
                identity=CanonicalPublicIdentity(
                    platform="youtube",
                    author_id="expression-viewer",
                    actor_key="youtube:expression-viewer",
                ),
            )
        return context_runtime.TrustedIngress(self.lane, public_scope)


def _request(lane=Lane.PRIVATE_OWNER, *, text="Explain the expression fixture in detail"):
    public = lane is Lane.PUBLIC_STAGE
    operator = lane is Lane.OPERATOR_BACKSTAGE
    raw = UnresolvedContextRequest(
        request_id=f"expression-{lane.value}",
        correlation_id=f"expression-correlation-{lane.value}",
        route=Route.INTERACTIVE,
        model="fake-private-model",
        current_input=text,
        viewer_name="Expression Viewer" if public else None,
        stream_mode=public,
        public_platform="youtube" if public else None,
        caller_metadata=(
            {
                "author_id": "expression-viewer",
                "room_id": "expression-room",
                "stream_session_id": "expression-session",
                "event_id": "expression-event",
                "display_name": "Expression Viewer",
            }
            if public
            else {}
        ),
        bridge_system=operator,
        story_mode=False,
        casual_mode=False,
        temporal_intent=False,
        grounding_intent=False,
    )
    return context_runtime.LaneResolver(
        _Authority(lane),
        wall_clock=lambda: 2_000.0,
        monotonic_clock=lambda: 900.0,
    ).resolve(raw)


def _source(request, name, payload, *, freshness=Freshness.FRESH):
    observed_at = request.captured_wall_time if freshness is not Freshness.UNKNOWN else None
    return SourceSnapshot(
        source=name,
        revision=f"{name}-revision",
        observed_at=observed_at,
        captured_at=request.captured_wall_time,
        freshness=freshness,
        payload=payload,
    )


def _snapshot(request, expression_payload):
    runtime_source = _source(
        request,
        "runtime_context",
        {
            "active_app": "fixture",
            "active_zone": "chill",
            "idle_state": "active",
            "in_flow": False,
            "time": {},
        },
        freshness=Freshness.UNKNOWN,
    )
    browser_source = _source(
        request,
        "browser_state",
        {"available": False},
        freshness=Freshness.UNKNOWN,
    )
    awareness_source = _source(
        request,
        "live_awareness",
        {
            "browser_available": False,
            "focus_source": "none",
            "focus_text": "",
            "focus_confidence": 0.0,
        },
        freshness=Freshness.UNKNOWN,
    )
    memory_source = _source(
        request,
        "awareness_memory",
        {"enabled": True, "moments": []},
        freshness=Freshness.UNKNOWN,
    )
    checkpoint_source = _source(
        request,
        "private_checkpoint",
        {
            "available": False,
            "session_id": "",
            "summary": "",
            "anchors": [],
            "pending_turns": [],
        },
        freshness=Freshness.UNKNOWN,
    )
    private_memory_source = _source(
        request,
        "private_memory",
        {"long_term": []},
    )
    expression_source = _source(request, "expression_private", expression_payload)
    sources = (
        runtime_source,
        browser_source,
        awareness_source,
        memory_source,
        checkpoint_source,
        expression_source,
        private_memory_source,
    )
    return context_runtime.RuntimeContextSnapshot(
        request=request,
        snapshot_revision=1,
        captured_at=request.captured_wall_time,
        captured_monotonic_at=request.captured_monotonic_time,
        runtime_context=runtime_source,
        browser_state=browser_source,
        live_awareness=awareness_source,
        awareness_memory=memory_source,
        private_checkpoint=checkpoint_source,
        expression=expression_source,
        private_memory=private_memory_source,
        source_revisions=tuple(
            context_adapters.SourceRevision(
                source.source,
                source.revision,
                "content_hash",
            )
            for source in sorted(sources, key=lambda item: item.source)
        ),
    )


def _expression_payload(*, mode="technical", clamp="strict", energy=1.234):
    return {
        "mood": {
            "tone": "focused",
            "energy": energy,
            "focus": -0.25,
            "tension": 0.555,
            "warmth": 0.99,
            "anchor": "MOOD-DEBUG-SENTINEL",
        },
        "affect": {
            "warmth": 0.333,
            "playfulness": 0.666,
            "assertiveness": 0.444,
            "intimacy": 0.876,
            "energy": 0.01,
            "tone": "AFFECT-TONE-SENTINEL",
            "last_source": "AFFECT-DEBUG-SENTINEL",
        },
        "persona": {
            "mode": mode,
            "clamp": clamp,
            "tone": "PERSONA-TONE-SENTINEL",
            "guidance": "GOVERNOR-PROSE-SENTINEL " * 40,
            "core_identity": "CORE-IDENTITY-SENTINEL",
        },
    }


def test_private_projection_has_one_structured_view_with_frozen_owner_precedence():
    request = _request()
    snapshot = _snapshot(request, _expression_payload())

    section = context_adapters.build_private_expression_section(snapshot, request)
    payload = _thaw(section.payload)

    assert section.id == "expression.private.v1"
    assert section.lifetime is Lifetime.TURN
    assert section.semantic_role is SemanticRole.STATE
    assert section.source.owner == "expression"
    assert payload == {
        "assertiveness": 0.44,
        "clamp": "strict",
        "energy": 1.0,
        "focus": 0.0,
        "guidance": STRICT_GUIDANCE,
        "intimacy": 0.88,
        "lane": "private_owner",
        "mode": "technical",
        "playfulness": 0.67,
        "tension": 0.56,
        "tone": "focused",
        "warmth": 0.33,
    }
    rendered = importlib.import_module("nana.runtime.context_compiler")._render_payload(
        section.payload
    )
    assert rendered.count('"lane":"private_owner"') == 1
    assert "0.330000" in rendered
    for forbidden in (
        "MOOD-DEBUG-SENTINEL",
        "AFFECT-TONE-SENTINEL",
        "AFFECT-DEBUG-SENTINEL",
        "PERSONA-TONE-SENTINEL",
        "GOVERNOR-PROSE-SENTINEL",
        "CORE-IDENTITY-SENTINEL",
        "anchor",
        "last_source",
        "affection",
        "annoyance",
    ):
        assert forbidden not in rendered


def test_private_source_capture_is_pure_finite_and_immutable(monkeypatch):
    memory_module = importlib.import_module("nana.memory")

    lock = importlib.import_module("threading").RLock()
    test_memory = {
        "persona": {
            "mode": "focus",
            "personality_intensity": 15,
            "target_intensity": 15,
            "manual_until": 0.0,
            "last_decay": 1_000.0,
            "residue_level": 0,
            "residue_sources": {},
        },
        "emotion": {
            "affection": math.inf,
            "annoyance": -4.0,
            "playfulness": math.nan,
        },
    }
    owner = object.__new__(mood_continuity.MoodContinuity)
    owner._lock = lock
    owner._last_persist_at = 0.0
    owner._state = {
        "energy": math.inf,
        "warmth": 0.9,
        "playfulness": 0.8,
        "focus": 0.8,
        "tension": math.nan,
        "mood": "steady",
        "last_update": 1_900.0,
        "last_decay": 2_000.0,
    }

    monkeypatch.setattr(mood_continuity, "_MOOD", owner)
    monkeypatch.setattr(mood_continuity, "memory", test_memory)
    monkeypatch.setattr(mood_continuity, "memory_lock", lock)
    monkeypatch.setattr(persona, "memory", test_memory)
    monkeypatch.setattr(persona, "memory_lock", lock)
    monkeypatch.setattr(memory_module, "memory", test_memory)
    monkeypatch.setattr(memory_module, "memory_lock", lock)

    calls = {"mood_save": 0, "persona_save": 0, "social": 0}

    def counted(name):
        def call(*_args, **_kwargs):
            calls[name] += 1
            raise AssertionError(f"unexpected side effect: {name}")

        return call

    monkeypatch.setattr(mood_continuity, "save_memory_async", counted("mood_save"))
    monkeypatch.setattr(persona, "save_memory_async", counted("persona_save"))
    monkeypatch.setattr(
        mood_continuity,
        "_social_temperature_snapshot",
        counted("social"),
    )
    affect_module = importlib.import_module("nana.runtime.affect_lane")
    real_project_affect = affect_module.project_affect
    projection_calls = []

    def project_once(emotion, lane):
        projection_calls.append((dict(emotion), lane))
        return real_project_affect(emotion, lane)

    monkeypatch.setattr(affect_module, "project_affect", project_once)

    persona_before = copy.deepcopy(test_memory["persona"])
    mood_last_decay = owner._state["last_decay"]
    request = _request()
    captured = context_runtime.capture_private_expression_sources(request=request)

    assert calls == {"mood_save": 0, "persona_save": 0, "social": 0}
    assert len(projection_calls) == 1
    captured_raw, captured_lane = projection_calls[0]
    assert captured_lane == "private_owner"
    assert math.isinf(captured_raw["affection"])
    assert captured_raw["annoyance"] == -4.0
    assert math.isnan(captured_raw["playfulness"])
    assert captured.captured_at == request.captured_wall_time
    assert test_memory["persona"] == persona_before
    assert owner._state["last_decay"] == mood_last_decay
    assert math.isinf(owner._state["energy"])
    assert math.isnan(owner._state["tension"])

    payload = _thaw(captured.payload)
    numeric = [
        payload["mood"][name] for name in ("energy", "focus", "tension")
    ] + [
        payload["affect"][name]
        for name in ("warmth", "playfulness", "assertiveness", "intimacy")
    ]
    assert all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in numeric)
    assert payload["mood"]["tone"] == "focused"
    assert payload["persona"] == {"clamp": "strict", "mode": "focus"}

    owner._state = {**owner._state, "energy": 0.17}
    test_memory["persona"] = {**test_memory["persona"], "mode": "social"}
    test_memory["emotion"] = {
        "affection": 0.1,
        "annoyance": 0.9,
        "playfulness": 0.1,
    }
    assert _thaw(captured.payload) == payload
    with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
        captured.payload["mood"]["energy"] = 0.5


@pytest.mark.parametrize("lane", [Lane.PUBLIC_STAGE, Lane.OPERATOR_BACKSTAGE])
def test_aggregate_expression_capture_denies_nonprivate_before_private_lookup(
    monkeypatch, lane
):
    private_imports = []
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "nana.memory" or (
            name == "nana.runtime"
            and any(
                item in {"affect_lane", "mood_continuity", "persona"}
                for item in fromlist
            )
        ):
            private_imports.append((name, tuple(fromlist)))
            raise AssertionError("nonprivate capture attempted private module lookup")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    with pytest.raises(ContextContractError, match="private_source_capture_denied"):
        context_runtime.capture_private_expression_sources(request=_request(lane))

    assert private_imports == []


@pytest.mark.parametrize(
    ("mode", "clamp", "expected"),
    (
        ("chill", "open", OPEN_GUIDANCE),
        ("chill", "moderate", MODERATE_GUIDANCE),
        ("chill", "strict", STRICT_GUIDANCE),
        ("focus", "open", STRICT_GUIDANCE),
        ("technical", "moderate", STRICT_GUIDANCE),
    ),
)
def test_private_guidance_uses_complete_fixed_templates(mode, clamp, expected):
    request = _request()
    source = _expression_payload(mode=mode, clamp=clamp)
    source["persona"]["guidance"] = "UNBOUNDED-GOVERNOR-SENTINEL " * 100
    section = context_adapters.build_private_expression_section(
        _snapshot(request, source),
        request,
    )
    payload = _thaw(section.payload)

    assert payload["guidance"] == expected
    assert len(payload["guidance"]) <= 240
    assert "UNBOUNDED-GOVERNOR-SENTINEL" not in payload["guidance"]
    if mode in {"focus", "technical"} or clamp in {"moderate", "strict"}:
        lowered = payload["guidance"].lower()
        for rule in ("humor", "emoji", "exaggeration", "callbacks", "roleplay"):
            assert rule in lowered


@pytest.mark.parametrize("label", ["tone", "mode", "clamp"])
@pytest.mark.parametrize("bad_value", ["not-a-valid-label", "", ("wrong-type",)])
def test_private_expression_enum_labels_fall_back_safely(label, bad_value):
    request = _request()
    source = _expression_payload(mode="social", clamp="open")
    if label == "tone":
        source["mood"]["tone"] = bad_value
    else:
        source["persona"][label] = bad_value

    payload = _thaw(
        context_adapters.build_private_expression_section(
            _snapshot(request, source),
            request,
        ).payload
    )

    assert payload[label] == {
        "tone": "steady",
        "mode": "chill",
        "clamp": "moderate",
    }[label]


@pytest.mark.parametrize(
    ("state", "expected_mode", "expected_clamp"),
    (
        (
            {"mode": "chill", "personality_intensity": 35, "target_intensity": 35, "residue_level": 0},
            "chill",
            "open",
        ),
        (
            {"mode": "social", "personality_intensity": 20, "target_intensity": 55, "residue_level": 0},
            "social",
            "moderate",
        ),
        (
            {"mode": "chill", "personality_intensity": 35, "target_intensity": 35, "residue_level": 60},
            "chill",
            "strict",
        ),
        (
            {"mode": "technical", "personality_intensity": 10, "target_intensity": 10, "residue_level": 0},
            "technical",
            "strict",
        ),
    ),
)
def test_pure_persona_capture_preserves_all_clamp_modes(
    monkeypatch, state, expected_mode, expected_clamp
):
    lock = importlib.import_module("threading").RLock()
    raw = {
        **state,
        "manual_until": 0.0,
        "last_decay": 2_000.0,
        "residue_sources": (
            {"legacy": state["residue_level"]} if state["residue_level"] else {}
        ),
    }
    test_memory = {"persona": copy.deepcopy(raw)}
    monkeypatch.setattr(persona, "memory", test_memory)
    monkeypatch.setattr(persona, "memory_lock", lock)
    before = copy.deepcopy(test_memory)

    result = persona.capture_persona_expression_snapshot(captured_at=2_000.0)

    assert result.mode == expected_mode
    assert result.clamp == expected_clamp
    assert test_memory == before


def test_pure_persona_capture_recomputes_stale_residue_from_owned_sources(monkeypatch):
    lock = importlib.import_module("threading").RLock()
    test_memory = {
        "persona": {
            "mode": "chill",
            "personality_intensity": 35,
            "target_intensity": 35,
            "manual_until": 0.0,
            "last_decay": 2_000.0,
            "residue_level": 90,
            "residue_sources": {"focus": 10},
        }
    }
    monkeypatch.setattr(persona, "memory", test_memory)
    monkeypatch.setattr(persona, "memory_lock", lock)
    before = copy.deepcopy(test_memory)

    result = persona.capture_persona_expression_snapshot(captured_at=2_000.0)

    assert result == persona.PersonaExpressionSnapshot(mode="chill", clamp="open")
    assert test_memory == before


def test_public_and_operator_projections_have_zero_private_reader_calls(monkeypatch):
    calls = {"mood": 0, "persona": 0, "emotion": 0}

    def tripwire(name):
        def call(*_args, **_kwargs):
            calls[name] += 1
            raise AssertionError(f"private reader called: {name}")

        return call

    monkeypatch.setattr(
        mood_continuity,
        "capture_mood_expression_snapshot",
        tripwire("mood"),
        raising=False,
    )
    monkeypatch.setattr(
        persona,
        "capture_persona_expression_snapshot",
        tripwire("persona"),
        raising=False,
    )
    monkeypatch.setattr(
        importlib.import_module("nana.runtime.affect_lane"),
        "project_affect",
        tripwire("emotion"),
    )

    public = context_adapters.build_public_expression_section(
        _request(Lane.PUBLIC_STAGE)
    )
    operator = context_adapters.build_operator_expression_section(
        _request(Lane.OPERATOR_BACKSTAGE)
    )

    assert calls == {"mood": 0, "persona": 0, "emotion": 0}
    assert public.id == "expression.public.v1"
    assert operator.id == "expression.operator.v1"
    assert _thaw(public.payload)["lane"] == "public_stage"
    assert _thaw(operator.payload)["lane"] == "operator_backstage"
    assert _thaw(public.payload)["intimacy"] == 0.22
    assert _thaw(operator.payload)["clamp"] == "strict"
    assert "expression.operator.v1" in registry_slot_ids(
        Lane.OPERATOR_BACKSTAGE,
        Route.INTERACTIVE,
    )


def _required_section(request, section_id, lifetime, role, owner, payload):
    return ContextSection(
        id=section_id,
        lifetime=lifetime,
        semantic_role=role,
        freshness=Freshness.FRESH,
        visibility=frozenset({Lane.PRIVATE_OWNER}),
        source=SourceRef(owner, "expression-test.adapter.v1", section_id),
        revision="expression-test-r1",
        authority=None,
        observed_at=request.captured_wall_time,
        expires_at=None,
        conflict_key=None,
        dedupe_key=section_id,
        max_tokens=2_500,
        payload=payload,
        formatter_version="expression-test.v1",
        required=True,
        budget_class="mandatory",
        semantic_status="active",
        provenance=(),
        relevance=1.0,
    )


def _plan(request, expression):
    sections = (
        _required_section(
            request,
            "core.private.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            "core",
            "STATIC CORE",
        ),
        _required_section(
            request,
            "policy.private.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            "core",
            "STATIC PRIORITY AND SAFETY",
        ),
        _required_section(
            request,
            "contract.output.private.v1",
            Lifetime.STATIC,
            SemanticRole.INSTRUCTION,
            "core",
            "STATIC OUTPUT",
        ),
        _required_section(
            request,
            "identity.owner.v1",
            Lifetime.DURABLE,
            SemanticRole.IDENTITY,
            "owner_identity",
            "OWNER",
        ),
        expression,
    )
    return context_runtime.build_turn_plan(
        context_runtime.GptTurnInput(
            request=request,
            sections=sections,
            evidence=None,
            model=request.model,
            max_output_tokens=200,
            budget_revision=context_runtime.PRIVATE_POLICY_REVISION,
        )
    )


def test_expression_only_change_preserves_all_static_session_prefix_hashes():
    request = _request()
    first = context_adapters.build_private_expression_section(
        _snapshot(request, _expression_payload(energy=0.21)),
        request,
    )
    second = context_adapters.build_private_expression_section(
        _snapshot(request, _expression_payload(energy=0.89)),
        request,
    )

    first_plan = _plan(request, first)
    second_plan = _plan(request, second)

    assert first_plan.compiled.static_prefix_hash == second_plan.compiled.static_prefix_hash
    assert first_plan.compiled.durable_prefix_hash == second_plan.compiled.durable_prefix_hash
    assert first_plan.compiled.session_prefix_hash == second_plan.compiled.session_prefix_hash
    assert first_plan.compiled.full_context_hash != second_plan.compiled.full_context_hash


def test_canonical_sync_and_stream_reuse_one_structured_expression(monkeypatch):
    from nana import config
    from nana.brain import gpt
    from nana.runtime import context_shadow

    monkeypatch.setattr(config, "NANA_CONTEXT_PRIVATE_MODE", "canonical")
    monkeypatch.setattr(
        config,
        "NANA_CONTEXT_BUDGET_POLICY_REVISION",
        context_runtime.PRIVATE_POLICY_REVISION,
    )
    monkeypatch.setattr(context_runtime, "require_canonical_dispatch_ready", lambda: None)

    text = "Explain the complete synthetic expression fixture with concrete steps"
    boundary, turn = context_shadow.resolve_context_turn(
        text=text,
        viewer_name=None,
        stream_mode=False,
        public_platform=None,
        metadata=None,
        private_model=gpt.LLMGATE_MAIN_MODEL,
        public_model=gpt.llmgate_public_chat_model(),
        story_mode=False,
        casual_mode=False,
        temporal_intent=False,
        grounding_intent=False,
    )
    snapshot = _snapshot(turn.resolved_request, _expression_payload())

    monkeypatch.setattr(gpt, "memory", {"short_term": [], "long_term": [], "emotion": {}})
    monkeypatch.setattr(gpt, "memory_lock", nullcontext())
    monkeypatch.setattr(gpt, "load_recent_chat", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(gpt, "resolve_user", lambda **_kwargs: {"name": "Ba"})
    monkeypatch.setattr(gpt, "format_identity_block", lambda *_args, **_kwargs: "OWNER ID")
    monkeypatch.setattr(
        gpt,
        "_identity_prompt_blocks_for_boundary",
        lambda _boundary: ("CORE ID", "SPINE ID", ""),
    )
    monkeypatch.setattr(gpt, "_private_session_checkpoint_block", lambda _boundary: "")
    monkeypatch.setattr(gpt, "_private_memory_retrieval_block", lambda _text: "")
    monkeypatch.setattr(gpt, "_private_memory_evidence_for_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(gpt, "format_prompt_memory_rules", lambda *_a, **_k: "RULES")
    monkeypatch.setattr(gpt, "_get_natural_style_hint", lambda: "")

    def forbidden(name):
        def fail(*_args, **_kwargs):
            raise AssertionError(f"canonical path called legacy expression source: {name}")

        return fail

    monkeypatch.setattr(gpt, "_mood_prompt_for_boundary", forbidden("mood formatter"))
    monkeypatch.setattr(gpt, "_observe_mood_for_turn", forbidden("mood observation"))
    monkeypatch.setattr(gpt, "persona_prompt_block", forbidden("persona governor"))
    monkeypatch.setattr(gpt, "format_affect_prompt_block", forbidden("affect formatter"))

    plans = []
    real_build = context_runtime.build_turn_plan

    def record_plan(inputs, **kwargs):
        plan = real_build(inputs, **kwargs)
        plans.append((inputs, plan))
        return plan

    monkeypatch.setattr(context_runtime, "build_turn_plan", record_plan)
    monkeypatch.setattr(
        gpt,
        "call_llmgate_compiled",
        lambda _compiled, **_kwargs: (
            "sync reply",
            "ok",
            SimpleNamespace(status="complete"),
        ),
    )
    llmgate = importlib.import_module("nana.brain.llmgate_client")
    monkeypatch.setattr(
        llmgate,
        "stream_llmgate_compiled",
        lambda _compiled, **_kwargs: iter(("stream reply",)),
    )
    bridge = importlib.import_module("nana.runtime.async_stream_bridge")

    async def iterate(source):
        for item in source:
            yield item

    monkeypatch.setattr(bridge, "iterate_blocking", iterate)

    assert gpt.ask_gpt(
        text,
        _prepared_private_turn=(boundary, turn),
        _turn_snapshot=snapshot,
    ) == "sync reply"
    streamed = asyncio.run(
        _collect_stream(
            gpt.ask_gpt_stream(
                text,
                _prepared_private_turn=(boundary, turn),
                _turn_snapshot=snapshot,
            )
        )
    )
    assert streamed == "stream reply"
    assert len(plans) == 2
    for inputs, plan in plans:
        expression_sections = [
            section for section in inputs.sections if section.id.startswith("expression.")
        ]
        assert len(expression_sections) == 1
        assert expression_sections[0].payload == plans[0][0].sections[
            [section.id for section in plans[0][0].sections].index("expression.private.v1")
        ].payload
        system = plan.compiled.messages[0].content
        assert system.count('"lane":"private_owner"') == 1
        assert "Emotion state:" not in system
        assert "Lane affect projection:" not in system
        assert "Persona Governor:" not in system
        assert "Priority: safety/privacy" in system
    assert plans[0][1].compiled.messages == plans[1][1].compiled.messages


def _install_hard_grounded_expression_fixture(
    monkeypatch,
    *,
    mode,
    captured_affect,
    raw_emotion,
):
    from nana import config
    from nana.brain import gpt
    from nana.runtime import context_shadow

    monkeypatch.setattr(config, "NANA_CONTEXT_PRIVATE_MODE", mode)
    if mode == "canonical":
        monkeypatch.setattr(
            config,
            "NANA_CONTEXT_BUDGET_POLICY_REVISION",
            context_runtime.PRIVATE_POLICY_REVISION,
        )
        monkeypatch.setattr(
            context_runtime,
            "require_canonical_dispatch_ready",
            lambda: None,
        )
    text = "What is on the current fixture page right now?"
    boundary, turn = context_shadow.resolve_context_turn(
        text=text,
        viewer_name=None,
        stream_mode=False,
        public_platform=None,
        metadata=None,
        private_model=gpt.LLMGATE_MAIN_MODEL,
        public_model=gpt.llmgate_public_chat_model(),
        story_mode=False,
        casual_mode=False,
        temporal_intent=False,
        grounding_intent=gpt._public_cross_session_query_eligible(text),
    )
    expression = _expression_payload(mode="chill", clamp="open")
    expression["affect"].update(captured_affect)
    snapshot = _snapshot(turn.resolved_request, expression)

    monkeypatch.setattr(
        gpt,
        "memory",
        {"short_term": [], "long_term": [], "emotion": dict(raw_emotion)},
    )
    monkeypatch.setattr(gpt, "memory_lock", nullcontext())
    monkeypatch.setattr(gpt, "_private_memory_evidence_for_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(gpt, "is_music_reference_question", lambda _text: False)
    monkeypatch.setattr(gpt, "is_browser_question", lambda _text: True)
    monkeypatch.setattr(gpt, "get_deterministic_browser_answer", lambda _aw: "Fixture Page")
    monkeypatch.setattr(
        gpt,
        "get_ground_truth_object",
        lambda *_a, **_k: {
            "title": "Fixture Page",
            "platform": "browser",
            "kind": "docs",
            "media_type": "page",
            "activity": "read",
        },
    )

    def surface(_truth, _mode, **kwargs):
        emotion = kwargs["emotion"]
        return (
            f"warmth={emotion['warmth']:.2f};"
            f"playfulness={emotion['playfulness']:.2f};"
            f"assertiveness={emotion['assertiveness']:.2f};"
            f"intimacy={emotion['intimacy']:.2f};"
            f"affection={emotion['affection']:.2f}"
        )

    monkeypatch.setattr(gpt, "format_surface_phrase", surface)
    monkeypatch.setattr(gpt, "guard_with_fallback", lambda value, *_a, **_k: value)
    monkeypatch.setattr(gpt, "add_casual_tone", lambda value: value)
    monkeypatch.setattr(
        gpt,
        "_surface_continuity",
        SimpleNamespace(record_mention=lambda *_a, **_k: None),
    )
    return gpt, text, boundary, turn, snapshot


def test_canonical_hard_grounded_sync_stream_use_frozen_affect_without_reprojection(
    monkeypatch,
):
    gpt, text, boundary, turn, snapshot = _install_hard_grounded_expression_fixture(
        monkeypatch,
        mode="canonical",
        captured_affect={
            "warmth": 0.91,
            "playfulness": 0.82,
            "assertiveness": 0.47,
            "intimacy": 0.79,
        },
        raw_emotion={"affection": 0.2, "annoyance": 0.9, "playfulness": 0.1},
    )
    projection_calls = []

    def reject_projection(*args, **kwargs):
        projection_calls.append((args, kwargs))
        raise AssertionError("canonical entrypoint re-projected frozen lane affect")

    monkeypatch.setattr(gpt, "project_affect", reject_projection)
    expected = (
        "warmth=0.91;playfulness=0.82;assertiveness=0.47;"
        "intimacy=0.79;affection=0.91"
    )

    sync = gpt.ask_gpt(
        text,
        _prepared_private_turn=(boundary, turn),
        _turn_snapshot=snapshot,
    )
    streamed = asyncio.run(
        _collect_stream(
            gpt.ask_gpt_stream(
                text,
                _prepared_private_turn=(boundary, turn),
                _turn_snapshot=snapshot,
            )
        )
    )

    assert sync == expected
    assert streamed == expected
    assert projection_calls == []


@pytest.mark.parametrize("mode", ["legacy", "shadow"])
def test_legacy_and_shadow_hard_grounded_paths_still_project_raw_emotion(
    monkeypatch, mode
):
    raw = {"affection": 0.91, "annoyance": 0.4, "playfulness": 0.77}
    gpt, text, boundary, turn, snapshot = _install_hard_grounded_expression_fixture(
        monkeypatch,
        mode=mode,
        captured_affect={
            "warmth": 0.2,
            "playfulness": 0.2,
            "assertiveness": 0.2,
            "intimacy": 0.2,
        },
        raw_emotion=raw,
    )
    affect_module = importlib.import_module("nana.runtime.affect_lane")
    real_project = affect_module.project_affect
    projection_calls = []

    def project(emotion, lane):
        projection_calls.append((dict(emotion), lane))
        return real_project(emotion, lane)

    monkeypatch.setattr(gpt, "project_affect", project)

    reply = gpt.ask_gpt(
        text,
        _prepared_private_turn=(boundary, turn),
        _turn_snapshot=snapshot,
    )

    assert reply == (
        "warmth=0.91;playfulness=0.77;assertiveness=0.38;"
        "intimacy=0.86;affection=0.91"
    )
    assert projection_calls == [(raw, "private_owner")]


@pytest.mark.parametrize("empty_stream", [False, True])
def test_pipeline_observes_mood_once_after_capture_and_reuses_fallback(monkeypatch, empty_stream):
    smoke_root = ROOT / "tests" / "smoke"
    if str(smoke_root) not in sys.path:
        sys.path.insert(0, str(smoke_root))
    from smoke_history_voice_consistency import Voice, pipeline_fixture

    with pipeline_fixture() as (pipeline, _memory, _tags, _notes):
        events = []
        boundary = SimpleNamespace(public=False)
        turn = SimpleNamespace(mode="canonical")
        prepared = (boundary, turn)
        awareness = {"focus_text": "frozen expression turn"}
        snapshot = SimpleNamespace(awareness_mapping=lambda: awareness)

        def capture_once(**_kwargs):
            events.append(("capture", None))
            return prepared, snapshot

        def observe(text, **kwargs):
            events.append(("mood", (text, kwargs)))

        async def stream(*_args, **kwargs):
            events.append(
                (
                    "stream",
                    (kwargs.get("_prepared_private_turn"), kwargs.get("_turn_snapshot")),
                )
            )
            if not empty_stream:
                yield "one reply"

        def sync(*_args, **kwargs):
            events.append(
                (
                    "sync",
                    (kwargs.get("_prepared_private_turn"), kwargs.get("_turn_snapshot")),
                )
            )
            return "fallback reply"

        monkeypatch.setattr(pipeline, "_capture_private_model_snapshot", capture_once)
        monkeypatch.setattr(pipeline, "observe_mood_text", observe, raising=False)
        monkeypatch.setattr(pipeline, "ask_gpt_stream", stream)
        monkeypatch.setattr(pipeline, "ask_gpt", sync)

        asyncio.run(
            pipeline.handle_chat_turn(None, Voice(), "ordinary canonical turn", None)
        )

        names = [name for name, _value in events]
        assert names.count("capture") == 1
        assert names.count("mood") == 1
        assert names.index("capture") < names.index("mood") < names.index("stream")
        assert names.count("sync") == (1 if empty_stream else 0)
        expected = (prepared, snapshot)
        assert next(value for name, value in events if name == "stream") == expected
        if empty_stream:
            assert next(value for name, value in events if name == "sync") == expected
        mood_text, mood_kwargs = next(value for name, value in events if name == "mood")
        assert mood_text == "ordinary canonical turn"
        assert mood_kwargs == {
            "lane": "private_owner",
            "source": "private_chat_pipeline",
            "event_type": "message",
        }


def test_post_capture_mood_observation_failure_does_not_drop_frozen_turn(monkeypatch):
    smoke_root = ROOT / "tests" / "smoke"
    if str(smoke_root) not in sys.path:
        sys.path.insert(0, str(smoke_root))
    from smoke_history_voice_consistency import pipeline_fixture

    with pipeline_fixture() as (pipeline, _memory, _tags, _notes):
        calls = []

        def fail(*_args, **_kwargs):
            calls.append("mood")
            raise RuntimeError("offline mood persistence failure")

        monkeypatch.setattr(pipeline, "observe_mood_text", fail, raising=False)
        pipeline._observe_canonical_mood_after_capture(
            (SimpleNamespace(public=False), SimpleNamespace(mode="canonical")),
            object(),
            "frozen turn",
        )

        assert calls == ["mood"]


def test_canonical_readiness_and_defaults_remain_fail_closed():
    from nana import config
    from nana.runtime.context_contracts import ContextContractError

    assert config.NANA_CONTEXT_PRIVATE_MODE == "legacy"
    assert config.NANA_CONTEXT_PUBLIC_GPT_MODE == "legacy"
    assert config.NANA_CONTEXT_CUM2_MODE == "legacy"
    assert config.NANA_CONTEXT_AUTONOMY_MODE == "legacy"
    with pytest.raises(ContextContractError, match="canonical_runtime_not_ready"):
        context_runtime.require_canonical_dispatch_ready()


async def _collect_stream(source):
    return "".join([chunk async for chunk in source])
