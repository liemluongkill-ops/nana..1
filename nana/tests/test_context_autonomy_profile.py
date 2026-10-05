"""Focused Task 15 tests for audience-resolved autonomy context."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
import sys
import types
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load_exact_smoke_consumers(path, *, functions=(), class_methods=None):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in functions
    ]
    if class_methods is not None:
        class_name, method_names = class_methods
        original = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == class_name
        )
        selected.append(
            ast.ClassDef(
                name=original.name,
                bases=original.bases,
                keywords=original.keywords,
                body=[
                    node
                    for node in original.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name in method_names
                ],
                decorator_list=original.decorator_list,
            )
        )
    module_ast = ast.Module(body=selected, type_ignores=[])
    ast.fix_missing_locations(module_ast)
    namespace = {
        "__file__": str(path),
        "SimpleNamespace": SimpleNamespace,
        "STAGE_NAME": "Nayumi Liora",
        "patch": patch,
        "unittest": unittest,
    }
    exec(compile(module_ast, str(path), "exec"), namespace)
    return namespace


@pytest.fixture
def isolated_observer():
    """Import the real observer behind inert, synthetic Nana packages."""

    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "nana" or name.startswith("nana.")
    }
    for name in tuple(saved):
        sys.modules.pop(name, None)

    nana = types.ModuleType("nana")
    nana.__path__ = [str(ROOT)]
    runtime = types.ModuleType("nana.runtime")
    runtime.__path__ = [str(ROOT / "runtime")]
    autonomy = types.ModuleType("nana.autonomy")
    autonomy.__path__ = [str(ROOT / "autonomy")]
    brain = types.ModuleType("nana.brain")
    brain.__path__ = [str(ROOT / "brain")]
    sys.modules.update(
        {
            "nana": nana,
            "nana.runtime": runtime,
            "nana.autonomy": autonomy,
            "nana.brain": brain,
        }
    )

    reads: list[str] = []
    state = {
        "runtime": {},
        "web": {"effective": False},
    }
    runtime_context = types.ModuleType("nana.runtime.context")

    def context_snapshot():
        reads.append("runtime_context")
        return dict(state["runtime"])

    runtime_context.context_snapshot = context_snapshot
    attention = types.ModuleType("nana.runtime.attention")
    attention.evaluate_attention_window = lambda _value: {"window": "unknown"}
    class TrackingMemory(dict):
        def get(self, key, default=None):
            reads.append("emotion_memory")
            return super().get(key, default)

    memory = types.ModuleType("nana.memory")
    memory.memory = TrackingMemory({"emotion": {"affection": 0.9}})
    memory.memory_lock = __import__("threading").RLock()
    web = types.ModuleType("nana.autonomy.web_context")

    def get_web_context(**_kwargs):
        reads.append("web_context")
        return dict(state["web"])

    web.get_web_context = get_web_context
    sys.modules.update(
        {
            "nana.runtime.context": runtime_context,
            "nana.runtime.attention": attention,
            "nana.memory": memory,
            "nana.autonomy.web_context": web,
        }
    )

    try:
        yield SimpleNamespace(
            module=importlib.import_module("nana.autonomy.observer"),
            reads=reads,
            state=state,
        )
    finally:
        for name in tuple(sys.modules):
            if name == "nana" or name.startswith("nana."):
                sys.modules.pop(name, None)
        sys.modules.update(saved)


def test_raw_observer_is_unresolved_and_reads_no_owner_sources(isolated_observer):
    """Adding an ambient private fallback would make this privacy regression fail."""

    observer_module = isolated_observer.module

    context = observer_module.RealObserver(clock=lambda: 41.0).get_context()

    assert context["audience_resolved"] is False
    assert isolated_observer.reads == []


def test_private_authority_captures_normal_fields_in_one_frozen_snapshot(
    isolated_observer,
):
    """Dropping zone/app/time/audience or retaining mutable source state must fail."""

    profile = importlib.import_module("nana.runtime.context_autonomy")
    isolated_observer.state["runtime"] = {
        "active_zone": "work",
        "active_app": "Code.exe",
        "time": {"part_of_day": "tối"},
        "idle_state": "active",
        "idle_seconds": 3.0,
        "in_flow": True,
        "last_chat_time": 90.0,
        "browser": {"kind": "docs"},
    }
    isolated_observer.state["web"] = {
        "effective": True,
        "available": True,
        "kind": "docs",
        "title": "Frozen source title",
    }
    authority = profile.PrivateOwnerAutonomyAuthority()
    observer = isolated_observer.module.RealObserver(
        clock=lambda: 100.0,
        audience_authority=authority,
    )

    context = observer.get_context()

    assert context["active_zone"] == "work"
    assert context["active_app"] == "Code.exe"
    assert context["time"]["part_of_day"] == "tối"
    assert context["audience"] == {
        "lane": "private_owner",
        "interaction_scope": "private_owner",
        "public_scope": None,
    }
    assert context["audience_resolved"] is True
    assert context["stream_stage_policy_gate"] is False
    snapshot = context["_autonomy_snapshot"]
    assert snapshot.capture_id
    assert snapshot.payload["active_zone"] == "work"
    with pytest.raises(TypeError):
        snapshot.payload["active_zone"] = "mutated"
    isolated_observer.state["runtime"]["active_zone"] = "game"
    assert snapshot.payload["active_zone"] == "work"
    assert isolated_observer.reads == [
        "runtime_context",
        "emotion_memory",
        "web_context",
    ]


def _public_scope(*, platform="youtube", event_id="event-1"):
    identity_module = importlib.import_module("nana.runtime.public_identity")
    boundary_module = importlib.import_module("nana.runtime.public_context_boundary")
    identity = identity_module.CanonicalPublicIdentity(
        platform=platform,
        author_id="viewer-1",
        actor_key=f"{platform}:viewer-1",
    )
    return boundary_module.PublicEventScope(
        platform=platform,
        room_id="room-1",
        stream_session_id="session-1",
        event_id=event_id,
        display_name="Minh",
        identity=identity,
    )


def test_public_snapshot_uses_only_caller_state_and_reads_zero_owner_sources(
    isolated_observer,
):
    """Any public fallback to runtime, browser, memory, or mood must fail."""

    profile = importlib.import_module("nana.runtime.context_autonomy")
    caller_state = {
        "platform": "youtube",
        "room_id": "room-1",
        "stream_session_id": "session-1",
        "event_id": "event-1",
        "active_zone": "public_stage",
        "active_app": "public_chat",
        "time": {"part_of_day": "tối"},
        "user_is_typing": False,
        "audio_busy": False,
        "scene_relevance": 0.8,
        "silence_duration_s": 12.0,
        "attention_window": "recent_chat",
        "web_context": {"effective": False},
    }
    authority = profile.PublicAutonomyAuthority(
        scope=_public_scope(),
        caller_state=caller_state,
    )
    observer = isolated_observer.module.RealObserver(
        clock=lambda: 200.0,
        wall_clock=lambda: 1_700_000_200.0,
        audience_authority=authority,
    )

    context = observer.get_context()

    assert context["audience_resolved"] is True
    assert context["audience"]["lane"] == "public_stage"
    assert context["audience"]["public_scope"]["event_id"] == "event-1"
    assert context["active_zone"] == "public_stage"
    assert context["active_app"] == "public_chat"
    assert context["time"]["part_of_day"] == "tối"
    assert context["mood_affection"] == 0.5
    assert context["stream_stage_policy_gate"] is True
    assert isolated_observer.reads == []
    caller_state["active_app"] = "mutated-after-authority"
    assert context["_autonomy_snapshot"].payload["active_app"] == "public_chat"


def test_raw_public_labels_and_stage_flags_cannot_issue_authority(isolated_observer):
    """Trusting a string or stage boolean would re-open private/public confusion."""

    raw = isolated_observer.module.RealObserver(
        clock=lambda: 300.0,
        audience_authority="public_stage",
    ).get_context()
    assert raw["audience_resolved"] is False
    assert isolated_observer.reads == []

    profile = importlib.import_module("nana.runtime.context_autonomy")
    authority = profile.PublicAutonomyAuthority(
        scope=_public_scope(platform="discord", event_id="discord-event"),
        caller_state={
            "platform": "discord",
            "room_id": "room-1",
            "stream_session_id": "session-1",
            "event_id": "discord-event",
            "stream_stage_policy_gate": True,
        },
    )
    public = isolated_observer.module.RealObserver(
        clock=lambda: 301.0,
        audience_authority=authority,
    ).get_context()
    assert public["audience_resolved"] is True
    assert public["stream_stage_policy_gate"] is False
    assert isolated_observer.reads == []


def test_lane_specific_autonomy_profiles_compile_only_declared_sections(
    isolated_observer,
):
    """Adding chat history/memory or omitting the typed final trigger must fail."""

    profile = importlib.import_module("nana.runtime.context_autonomy")
    contracts = importlib.import_module("nana.runtime.context_contracts")
    isolated_observer.state["runtime"] = {
        "active_zone": "work",
        "active_app": "Editor.exe",
        "time": {"part_of_day": "chiều"},
        "last_chat_time": 0.0,
        "browser": {},
    }
    private_context = isolated_observer.module.RealObserver(
        clock=lambda: 400.0,
        wall_clock=lambda: 1_700_000_400.0,
        audience_authority=profile.PrivateOwnerAutonomyAuthority(),
    ).get_context()
    public_context = isolated_observer.module.RealObserver(
        clock=lambda: 401.0,
        wall_clock=lambda: 1_700_000_401.0,
        audience_authority=profile.PublicAutonomyAuthority(
            scope=_public_scope(event_id="event-compile"),
            caller_state={
                "platform": "youtube",
                "room_id": "room-1",
                "stream_session_id": "session-1",
                "event_id": "event-compile",
                "active_zone": "public_stage",
                "active_app": "public_chat",
                "time": {"part_of_day": "chiều"},
            },
        ),
    ).get_context()

    cases = (
        (
            private_context,
            contracts.Lane.PRIVATE_OWNER,
            {
                "core.private.v1",
                "policy.private.v1",
                "policy.autonomy.v1",
                "contract.output.autonomy.v1",
                "expression.private.v1",
                "situation.current.v1",
            },
        ),
        (
            public_context,
            contracts.Lane.PUBLIC_STAGE,
            {
                "core.public.v1",
                "policy.public.v1",
                "policy.autonomy.v1",
                "contract.output.autonomy.v1",
                "expression.public.v1",
                "situation.current.v1",
            },
        ),
    )
    for context, lane, expected_sections in cases:
        snapshot = context["_autonomy_snapshot"]
        compiled = profile.compile_autonomy_context(
            snapshot,
            mode="observer_aware",
            model="nana-banter",
        )
        assert compiled.manifest.lane is lane
        assert compiled.manifest.route is contracts.Route.AUTONOMY
        assert compiled.manifest.capture_id == snapshot.capture_id
        assert compiled.manifest.snapshot_revision == snapshot.snapshot_revision
        assert compiled.manifest.budget_policy_status is contracts.BudgetPolicyStatus.SHADOW_CANDIDATE
        assert compiled.manifest.budget_enforced is False
        assert {row.section_id for row in compiled.manifest.sections} == expected_sections
        assert [message.role for message in compiled.messages] == ["system", "user"]
        assert compiled.messages[-1].content.startswith("autonomy.trigger.v1\n")
        assert "mode=observer_aware" in compiled.messages[-1].content
        system = compiled.messages[0].content
        assert "autonomy.trigger.v1" not in system
        assert "chat history" not in system.casefold()
        assert "durable memory" not in system.casefold()
        assert "NANA_PERSONALITY" not in system

        budget = profile.provisional_autonomy_budget(lane)
        assert budget.status is contracts.BudgetPolicyStatus.SHADOW_CANDIDATE
        assert budget.max_estimated_tokens == 3_000
        assert budget.max_characters == 12_000
        assert profile.autonomy_slot_limits(lane)["autonomy.trigger.v1"] == (
            256,
            768,
        )


def test_snapshot_rejects_audience_transplant_even_with_copied_seal(
    isolated_observer,
):
    """Seal copying plus an audience swap must never cross the lane boundary."""

    profile = importlib.import_module("nana.runtime.context_autonomy")
    isolated_observer.state["runtime"] = {
        "active_zone": "private_work",
        "active_app": "PrivateEditor.exe",
        "time": {"part_of_day": "khuya"},
        "browser": {},
    }
    private = isolated_observer.module.RealObserver(
        clock=lambda: 500.0,
        wall_clock=lambda: 1_700_000_500.0,
        audience_authority=profile.PrivateOwnerAutonomyAuthority(),
    ).get_context()["_autonomy_snapshot"]
    public = isolated_observer.module.RealObserver(
        clock=lambda: 501.0,
        wall_clock=lambda: 1_700_000_501.0,
        audience_authority=profile.PublicAutonomyAuthority(
            scope=_public_scope(event_id="event-transplant"),
            caller_state={
                "platform": "youtube",
                "room_id": "room-1",
                "stream_session_id": "session-1",
                "event_id": "event-transplant",
                "active_zone": "public_stage",
            },
        ),
    ).get_context()["_autonomy_snapshot"]

    transplanted = object.__new__(type(private))
    for name in (
        "capture_id",
        "snapshot_revision",
        "captured_wall_time",
        "captured_monotonic_time",
        "payload",
        "_seal",
    ):
        object.__setattr__(transplanted, name, getattr(private, name))
    object.__setattr__(transplanted, "audience", public.audience)

    with pytest.raises(
        importlib.import_module("nana.runtime.context_contracts").ContextContractError,
        match="invalid_autonomy_snapshot",
    ):
        profile.compile_autonomy_context(
            transplanted,
            mode="idle_banter",
            model="nana-banter",
        )


class _StepClock:
    def __init__(self, value=1_000.0):
        self.value = value

    def __call__(self):
        self.value += 0.01
        return self.value


class _ManualClock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


def test_legacy_shadow_and_canonical_transport_modes_preserve_boundaries(
    isolated_observer,
    monkeypatch,
):
    """Changing the sent payload or bypassing canonical readiness must fail."""

    profile = importlib.import_module("nana.runtime.context_autonomy")
    isolated_observer.state["runtime"] = {
        "active_zone": "work",
        "active_app": "Editor.exe",
        "time": {"part_of_day": "tối"},
        "last_chat_time": 900.0,
        "browser": {},
    }
    context = isolated_observer.module.RealObserver(
        clock=lambda: 1_000.0,
        wall_clock=lambda: 1_700_001_000.0,
        audience_authority=profile.PrivateOwnerAutonomyAuthority(),
    ).get_context()
    legacy_calls = []
    compiled_calls = []
    client = types.ModuleType("nana.brain.llmgate_client")

    def call_messages(model, messages, **kwargs):
        legacy_calls.append((model, messages, kwargs))
        return "Một câu thử nghiệm.", "ok"

    def call_compiled(compiled, **kwargs):
        compiled_calls.append((compiled, kwargs))
        return "Một câu canonical.", "ok", SimpleNamespace(status="complete")

    client.call_llmgate_messages = call_messages
    client.call_llmgate_compiled = call_compiled
    sys.modules["nana.brain.llmgate_client"] = client
    banter_module = importlib.import_module("nana.autonomy.llm_banter")
    monkeypatch.setenv("NANA_AUTONOMY_LLM_MODEL", "nana-banter")
    monkeypatch.delenv("NANA_AUTONOMY_LLM_DISABLED", raising=False)

    shared = banter_module.LLMBanter(clock=_StepClock())
    monkeypatch.setenv("NANA_CONTEXT_AUTONOMY_MODE", "legacy")
    legacy_result = shared.generate(
        "idle_banter",
        context,
        context["web_context"],
        min_age_s=60.0,
    )
    assert legacy_result is not None
    legacy_messages = legacy_calls[-1][1]

    monkeypatch.setenv("NANA_CONTEXT_AUTONOMY_MODE", "shadow")
    monkeypatch.setattr(
        banter_module,
        "_emit_shadow_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("sink failed")),
        raising=False,
    )
    shadow_result = banter_module.LLMBanter(clock=_StepClock()).generate(
        "idle_banter",
        context,
        context["web_context"],
        min_age_s=0.0,
    )
    assert shadow_result is not None
    assert legacy_calls[-1][1] == legacy_messages
    assert compiled_calls == []

    monkeypatch.setenv("NANA_CONTEXT_AUTONOMY_MODE", "canonical")
    blocked = shared.generate(
        "idle_banter",
        context,
        context["web_context"],
        min_age_s=60.0,
    )
    assert blocked is None
    assert len(legacy_calls) == 2
    assert compiled_calls == []

    runtime = importlib.import_module("nana.runtime.context_runtime")
    monkeypatch.setattr(
        runtime,
        "require_autonomy_canonical_dispatch_ready",
        lambda _compiled: None,
    )
    canonical = banter_module.LLMBanter(clock=_StepClock()).generate(
        "idle_banter",
        context,
        context["web_context"],
        min_age_s=0.0,
    )
    assert canonical is not None
    assert canonical.text == "Một câu canonical."
    assert len(legacy_calls) == 2
    assert len(compiled_calls) == 1
    compiled, kwargs = compiled_calls[0]
    assert compiled.messages[-1].content.startswith("autonomy.trigger.v1\n")
    assert kwargs["max_tokens"] == 120
    assert kwargs["temperature"] == 0.8


def test_raw_stage_flag_does_not_select_livestream_identity(isolated_observer):
    """A raw flag or stream_host style must never activate the stage identity."""

    profile = importlib.import_module("nana.runtime.context_autonomy")
    banter = importlib.import_module("nana.autonomy.llm_banter")
    raw_prompt, _ = banter._build_prompt(
        "stream_host",
        {"stream_stage_policy_gate": True},
        {},
    )
    assert "Nayumi Liora" not in raw_prompt

    trusted = isolated_observer.module.RealObserver(
        clock=lambda: 1_100.0,
        wall_clock=lambda: 1_700_001_100.0,
        audience_authority=profile.PublicAutonomyAuthority(
            scope=_public_scope(event_id="event-live-prompt"),
            caller_state={
                "platform": "youtube",
                "room_id": "room-1",
                "stream_session_id": "session-1",
                "event_id": "event-live-prompt",
            },
        ),
    ).get_context()
    live_prompt, _ = banter._build_prompt(
        "stream_host",
        trusted,
        trusted["web_context"],
    )
    assert "Nayumi Liora" in live_prompt

    public_non_live = isolated_observer.module.RealObserver(
        clock=lambda: 1_101.0,
        wall_clock=lambda: 1_700_001_101.0,
        audience_authority=profile.PublicAutonomyAuthority(
            scope=_public_scope(platform="discord", event_id="event-social-prompt"),
            caller_state={
                "platform": "discord",
                "room_id": "room-1",
                "stream_session_id": "session-1",
                "event_id": "event-social-prompt",
            },
        ),
    ).get_context()
    public_prompt, _ = banter._build_prompt(
        "stream_host",
        public_non_live,
        public_non_live["web_context"],
    )
    assert "Ba (user)" not in public_prompt
    assert "Nayumi Liora" not in public_prompt
    assert "kênh công khai" in public_prompt


def test_loop_supplies_private_authority_and_rejects_unresolved_after_cadence(
    isolated_observer,
):
    """Removing composition authority or moving rejection before pause must fail."""

    loop_module = importlib.import_module("nana.autonomy.loop")
    loop = loop_module.AutonomyLoop(clock=lambda: 1_200.0)
    resolved = loop.observer.get_context()
    assert resolved["audience_resolved"] is True
    assert resolved["audience"]["lane"] == "private_owner"

    isolated_observer.reads.clear()
    loop.pause()
    paused = loop.tick()
    assert paused["reason"] == "paused"
    assert isolated_observer.reads == []

    loop._cadence.resume()
    loop._observer = isolated_observer.module.RealObserver(clock=lambda: 1_201.0)
    cadence_calls = []
    gate_calls = []
    loop._cadence.can_express = lambda mode: (
        cadence_calls.append(mode) or True,
        "ok",
    )
    loop._cadence.record_expression = lambda *_args, **_kwargs: None
    loop._gate.evaluate_debug = lambda _ctx: gate_calls.append("debug")
    loop._gate.evaluate = lambda _ctx: gate_calls.append("gate")
    unresolved = loop.tick()
    assert unresolved["reason"] == "audience_unresolved"
    assert cadence_calls
    assert gate_calls == []
    assert loop.express.last_trace is None


def test_cache_reuses_rendered_content_but_never_crosses_audience(
    isolated_observer,
    monkeypatch,
):
    """Jitter/time telemetry must not bust cache; lane/scope changes always must."""

    profile = importlib.import_module("nana.runtime.context_autonomy")
    isolated_observer.state["runtime"] = {
        "active_zone": "work",
        "active_app": "Editor.exe",
        "time": {"part_of_day": "tối", "time": "20:00:00"},
        "browser": {},
    }
    first_context = isolated_observer.module.RealObserver(
        clock=lambda: 1_300.0,
        wall_clock=lambda: 1_700_001_300.0,
        rng=SimpleNamespace(random=lambda: 0.1),
        audience_authority=profile.PrivateOwnerAutonomyAuthority(),
    ).get_context()
    first = first_context["_autonomy_snapshot"]
    second_payload = dict(first.payload)
    second_payload["jitter_value"] = 0.9
    second_payload["time"] = {"part_of_day": "tối", "time": "20:00:01"}
    second = profile.freeze_autonomy_snapshot(
        audience=first.audience,
        payload=second_payload,
        captured_wall_time=1_700_001_301.0,
        captured_monotonic_time=1_301.0,
    )
    second_context = dict(second.payload)
    second_context["_autonomy_snapshot"] = second

    calls = []
    client = types.ModuleType("nana.brain.llmgate_client")
    client.call_llmgate_messages = lambda model, messages, **kwargs: (
        calls.append((model, messages, kwargs)) or "Cache line",
        "ok",
    )
    sys.modules["nana.brain.llmgate_client"] = client
    banter = importlib.import_module("nana.autonomy.llm_banter")
    monkeypatch.setenv("NANA_CONTEXT_AUTONOMY_MODE", "legacy")
    instance = banter.LLMBanter(clock=_StepClock())

    assert instance.generate("idle_banter", first_context, {}, min_age_s=60.0)
    assert instance.generate("idle_banter", second_context, {}, min_age_s=60.0)
    assert len(calls) == 1
    assert instance.stats["cached"] == 1

    public_context = isolated_observer.module.RealObserver(
        clock=lambda: 1_302.0,
        wall_clock=lambda: 1_700_001_302.0,
        audience_authority=profile.PublicAutonomyAuthority(
            scope=_public_scope(platform="discord", event_id="event-cache"),
            caller_state={
                "platform": "discord",
                "room_id": "room-1",
                "stream_session_id": "session-1",
                "event_id": "event-cache",
            },
        ),
    ).get_context()
    monkeypatch.setattr(banter, "_build_prompt", lambda *_args: ("same", False))
    isolated = banter.LLMBanter(clock=_StepClock())
    assert isolated.generate("idle_banter", first_context, {}, min_age_s=60.0)
    assert isolated.generate("idle_banter", public_context, {}, min_age_s=60.0)
    assert len(calls) == 3


def test_loop_gate_uses_frozen_snapshot_not_mutable_overlay(isolated_observer):
    """Clearing audio/attention fields after capture must not bypass the gate."""

    profile = importlib.import_module("nana.runtime.context_autonomy")
    loop_module = importlib.import_module("nana.autonomy.loop")
    isolated_observer.state["runtime"] = {
        "active_zone": "work",
        "active_app": "Editor.exe",
        "time": {"part_of_day": "tối"},
        "browser": {},
    }
    observer = isolated_observer.module.RealObserver(
        clock=lambda: 1_400.0,
        wall_clock=lambda: 1_700_001_400.0,
        audience_authority=profile.PrivateOwnerAutonomyAuthority(),
    )
    observer.set_voice_snapshot_fn(lambda: {"speaking": True})
    captured = observer.get_context()
    initial_snapshot = captured["_autonomy_snapshot"]
    payload = dict(initial_snapshot.payload)
    payload["forced_mode"] = "observer_aware"
    authoritative = profile.freeze_autonomy_snapshot(
        audience=initial_snapshot.audience,
        payload=payload,
        captured_wall_time=initial_snapshot.captured_wall_time,
        captured_monotonic_time=initial_snapshot.captured_monotonic_time,
    )
    captured = dict(authoritative.payload)
    captured["_autonomy_snapshot"] = authoritative
    assert authoritative.payload["audio_busy"] is True
    captured["audio_busy"] = False
    captured["attention_window"] = "recent_chat"
    captured["forced_mode"] = "stream_host"

    loop = loop_module.AutonomyLoop(clock=lambda: 1_400.0)
    loop._observer = SimpleNamespace(get_context=lambda: captured)
    loop._cadence.can_express = lambda _mode: (True, "ok")
    loop._cadence.record_expression = lambda *_args, **_kwargs: None
    thought_calls = []
    loop._thought.pick = lambda *_args, **_kwargs: thought_calls.append("pick")
    loop._thought.pick_prefer_ultra_short = loop._thought.pick

    decision = loop.tick()

    assert decision["reason"] == "audio_busy"
    assert decision["mode"] == "observer_aware"
    assert thought_calls == []
    assert loop.express.last_trace is None


def test_frozen_mode_gets_the_single_real_cadence_admission(isolated_observer):
    """A cooling mutable mode must not reject an eligible frozen mode."""

    profile = importlib.import_module("nana.runtime.context_autonomy")
    loop_module = importlib.import_module("nana.autonomy.loop")
    thought_module = importlib.import_module("nana.autonomy.inner_thought")
    isolated_observer.state["runtime"] = {
        "active_zone": "work",
        "active_app": "Editor.exe",
        "time": {"part_of_day": "tối"},
        "browser": {},
    }
    observer = isolated_observer.module.RealObserver(
        clock=lambda: 1_450.0,
        wall_clock=lambda: 1_700_001_450.0,
        audience_authority=profile.PrivateOwnerAutonomyAuthority(),
    )
    initial = observer.get_context()["_autonomy_snapshot"]
    payload = dict(initial.payload)
    payload["forced_mode"] = "observer_aware"
    snapshot = profile.freeze_autonomy_snapshot(
        audience=initial.audience,
        payload=payload,
        captured_wall_time=initial.captured_wall_time,
        captured_monotonic_time=initial.captured_monotonic_time,
    )
    mutable_overlay = dict(snapshot.payload)
    mutable_overlay["forced_mode"] = "stream_host"
    mutable_overlay["_autonomy_snapshot"] = snapshot

    clock = _ManualClock()
    loop = loop_module.AutonomyLoop(clock=clock)
    loop.cadence.record_expression("stream_host", accepted=True, reason="ok")
    clock.value = 10.0
    loop._observer = SimpleNamespace(get_context=lambda: mutable_overlay)
    loop._gate.evaluate_debug = lambda _ctx: {}
    loop._gate.evaluate = lambda _ctx: SimpleNamespace(
        allowed=True,
        reason="ok",
        level="full",
        intensity=0.8,
        payload={"tts": False, "vts": False, "subtitle": False},
    )
    thought = thought_module.Thought(
        mode="observer_aware",
        text="Frozen mode accepted.",
        cooldown_s=0,
        min_silence_s=0,
        tags=[],
        raw="Frozen mode accepted.",
        line_index=0,
        source="test",
    )
    loop._thought.pick = lambda *_args, **_kwargs: thought
    loop._thought.pick_prefer_ultra_short = loop._thought.pick
    loop._express.emit = lambda *_args, **_kwargs: {"mock": True}

    decision = loop.tick()

    assert decision["accepted"] is True
    assert decision["mode"] == "observer_aware"
    cadence_events = loop.cadence.events
    assert [event.mode for event in cadence_events] == [
        "stream_host",
        "observer_aware",
    ]


def test_paused_tick_consumes_one_shot_override_without_observing(isolated_observer):
    """A paused override must not survive and unexpectedly fire after resume."""

    loop_module = importlib.import_module("nana.autonomy.loop")
    loop = loop_module.AutonomyLoop(clock=lambda: 1_500.0)
    loop.request_user_override()
    loop.pause()
    isolated_observer.reads.clear()

    decision = loop.tick()

    assert decision["reason"] == "paused"
    assert loop._user_override is False
    assert isolated_observer.reads == []


def test_public_zero_signals_and_stale_web_are_preserved_safely(isolated_observer):
    """Truthiness defaults or stale-title promotion must fail this regression."""

    profile = importlib.import_module("nana.runtime.context_autonomy")
    context = isolated_observer.module.RealObserver(
        clock=lambda: 1_600.0,
        wall_clock=lambda: 1_700_001_600.0,
        audience_authority=profile.PublicAutonomyAuthority(
            scope=_public_scope(event_id="event-zero"),
            caller_state={
                "platform": "youtube",
                "room_id": "room-1",
                "stream_session_id": "session-1",
                "event_id": "event-zero",
                "scene_relevance": 0.0,
                "jitter_value": 0.0,
                "silence_duration_s": 0.0,
                "web_context": {
                    "available": True,
                    "fresh": False,
                    "effective": False,
                    "kind": "private-stale-kind",
                    "title": "PRIVATE_STALE_SENTINEL",
                },
            },
        ),
    ).get_context()

    assert context["scene_relevance"] == 0.0
    assert context["jitter_value"] == 0.0
    compiled = profile.compile_autonomy_context(
        context["_autonomy_snapshot"],
        mode="observer_aware",
        model="nana-banter",
    )
    assert "PRIVATE_STALE_SENTINEL" not in compiled.messages[0].content
    assert "private-stale-kind" not in compiled.messages[0].content


def test_exact_livestream_identity_autonomy_consumers_are_guarded(
    isolated_observer,
):
    """The exact amended identity consumers must accept public loop context."""

    namespace = _load_exact_smoke_consumers(
        ROOT / "tests" / "smoke" / "smoke_livestream_identity.py",
        functions=("_autonomy_context",),
        class_methods=(
            "LivestreamIdentityTests",
            ("test_banter_prompt_explicit_gate", "test_autonomy_output_scope"),
        ),
    )
    public_context = namespace["_autonomy_context"](
        {"mood_affection": 0.9, "scene_relevance": 0.8},
        public=True,
        event_id="focused-public-helper",
    )
    assert public_context["mood_affection"] == 0.5
    assert isolated_observer.reads == []

    case = namespace["LivestreamIdentityTests"]()
    case.test_banter_prompt_explicit_gate()
    case.test_autonomy_output_scope()


def test_exact_stage8d_autonomy_stream_policy_scope_is_guarded(isolated_observer):
    """The exact Stage8D scope consumer must preserve private/live assertions."""

    namespace = _load_exact_smoke_consumers(
        ROOT / "tests" / "smoke" / "smoke_stage8d_stream_policy_wiring.py",
        functions=("_autonomy_context", "_test_autonomy_stream_policy_scope"),
    )
    namespace["_test_autonomy_stream_policy_scope"]()
    assert isolated_observer.reads == []
