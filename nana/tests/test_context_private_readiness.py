"""Offline private provider/model/capacity readiness contracts."""

from __future__ import annotations

import gc
import importlib
from pathlib import Path
import sys
import types
import weakref

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))


from nana.runtime.context_budget import ReviewedModelCapabilityV1  # noqa: E402
from nana.runtime.context_contracts import ContextContractError  # noqa: E402
from nana.runtime import context_runtime as context_runtime_module  # noqa: E402
from nana.runtime.context_runtime import ContextRuntime  # noqa: E402
from test_context_budget import (  # noqa: E402
    _base_sections,
    _fixture_policy,
    _packet,
    _request,
    _section,
)


def _readiness_module():
    try:
        return importlib.import_module("nana.runtime.context_readiness")
    except ModuleNotFoundError as exc:
        pytest.fail(f"private readiness contract unavailable: {exc}")


def _profile(
    *,
    logical_model: str = "fixture-logical-model",
    resolved_model: str = "fixture-resolved-model",
    counter=None,
    context_window_tokens: int = 20_000,
    max_completion_tokens: int = 2_000,
    route_revision: str = "fixture-route.v1",
    reasoning_effort: str = "low",
):
    readiness = _readiness_module()
    if counter is None:
        counter = lambda messages: sum(
            len(message.content.encode("utf-8")) for message in messages
        )
    capability = ReviewedModelCapabilityV1(
        provider="llmgate",
        model=resolved_model,
        revision="fixture-capability.v1",
        context_window_tokens=context_window_tokens,
        provider_input_limit_tokens=None,
        max_input_characters=100_000,
        count_input_tokens=counter,
    )
    profile = readiness.ReviewedPrivateRouteV1(
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
        logical_model=logical_model,
        transport_provider="llmgate",
        endpoint_fingerprint=readiness.endpoint_fingerprint_v1(
            "https://llmgate.invalid/v1"
        ),
        capability=capability,
        max_completion_tokens=max_completion_tokens,
        counter_revision="fixture-counter.v1",
        evidence_revision="fixture-evidence.v1",
        evidence_provenance=("synthetic:test-only",),
        route_revision=route_revision,
        reasoning_effort=reasoning_effort,
    )
    return readiness, profile


def _turn_fixture(
    *,
    story: bool = False,
    text: str = "fixture query",
    model: str = "fixture-model",
):
    contracts = importlib.import_module("nana.runtime.context_contracts")
    runtime = context_runtime_module
    if model == "fixture-model":
        request = _request(current_input=text, story_mode=story)
    else:
        unresolved = contracts.UnresolvedContextRequest(
            request_id="readiness-request",
            correlation_id="readiness-correlation",
            route=contracts.Route.INTERACTIVE,
            model=model,
            current_input=text,
            viewer_name=None,
            stream_mode=False,
            public_platform=None,
            caller_metadata={},
            bridge_system=False,
            story_mode=story,
            casual_mode=False,
            temporal_intent=False,
            grounding_intent=False,
        )

        class Authority:
            def authorize(self, _request_value):
                return runtime.TrustedIngress(contracts.Lane.PRIVATE_OWNER, None)

        request = runtime.LaneResolver(
            Authority(),
            wall_clock=lambda: 1_700_000_000.0,
            monotonic_clock=lambda: 123.0,
            compiler_config_revision="compiler-config.v1",
        ).resolve(unresolved)
    sections = _base_sections()
    if story:
        sections = sections + (
            _section(
                "mode.turn.private.v1",
                "STORY MODE",
                required=True,
                owner="private_session",
            ),
        )
    return contracts, runtime, request, sections


def _bound_plan(
    *,
    story: bool = False,
    counter=None,
    max_completion_tokens: int = 2_000,
    max_output_tokens: int = 200,
    route_revision: str = "fixture-route.v1",
    resolved_model: str = "fixture-resolved-model",
    reasoning_effort: str = "low",
    registry=None,
    binding_capacity: int = 256,
):
    logical_model = (
        "gpt-5.6-terra"
        if resolved_model == "gpt-5.6-terra"
        else "fixture-model"
    )
    contracts, runtime, request, sections = _turn_fixture(
        story=story,
        model=logical_model,
    )
    readiness, profile = _profile(
        logical_model=request.model,
        resolved_model=resolved_model,
        counter=counter,
        max_completion_tokens=max_completion_tokens,
        route_revision=route_revision,
        reasoning_effort=reasoning_effort,
    )
    if registry is None:
        registry = readiness.PrivateReadinessRegistry(
            (profile,),
            binding_capacity=binding_capacity,
        )
    grant = registry.issue(
        logical_model=request.model,
        lane=contracts.Lane.PRIVATE_OWNER,
        route=contracts.Route.INTERACTIVE,
    )
    plan = runtime.build_turn_plan(
        runtime.GptTurnInput(
            request=request,
            sections=sections,
            evidence=None,
            model=request.model,
            max_output_tokens=max_output_tokens,
            budget_revision=runtime.PRIVATE_POLICY_REVISION,
            readiness=grant,
        )
    )
    return readiness, profile, registry, grant, plan, request, runtime


class _Response:
    def __init__(self, data=None):
        self._data = data
        self.status_code = 200
        self.closed = False

    def raise_for_status(self):
        return None

    def json(self):
        return self._data

    def close(self):
        self.closed = True


def _install_sse(events):
    class FakeSSEClient:
        def __init__(self, _response):
            pass

        def events(self):
            for data in events:
                yield types.SimpleNamespace(data=data)

    sys.modules["sseclient"] = types.SimpleNamespace(SSEClient=FakeSSEClient)


def _llmgate_client(monkeypatch):
    # The cumulative gate forbids production config reads, including on a clean clone.
    config = types.ModuleType("nana.config")
    config.LLMGATE_CHEAP_MODEL = "fixture-cheap"
    config.LLMGATE_MAIN_MODEL = "fixture-main"
    config.LLMGATE_PUBLIC_MODEL = "fixture-public"
    monkeypatch.setitem(sys.modules, "nana.config", config)
    logger = types.ModuleType("nana.runtime.logger")
    logger.log_event = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "nana.runtime.logger", logger)
    monkeypatch.setattr(config, "LLMGATE_MAIN_REASONING_EFFORT", "none", raising=False)
    monkeypatch.setattr(config, "LLMGATE_SETTINGS_PATH", "unused-settings.json", raising=False)
    monkeypatch.setattr(config, "LLMGATE_TIMEOUT", 30.0, raising=False)
    sys.modules.pop("nana.brain.llmgate_client", None)
    client = importlib.import_module("nana.brain.llmgate_client")
    monkeypatch.setattr(client, "log_event", lambda *_args, **_kwargs: None)
    return client


def _strict_fixture_config(
    resolved_model="gpt-5.6-terra",
    endpoint=None,
    api_key="fixture-key",
):
    return {
        "apiKey": api_key,
        "baseUrl": endpoint or "https://llmgate.invalid/v1",
        "model": resolved_model,
    }


def _resolve_with_capacity(*, counted_tokens: int):
    sections = _base_sections()
    policy = _fixture_policy(sections, output_reserve=3, framing=2)
    observed_wire = []

    def count_exact_wire(messages):
        observed_wire.append(messages)
        return counted_tokens

    capability = ReviewedModelCapabilityV1(
        provider="llmgate",
        model="fixture-resolved-model",
        revision="fixture-capability.v1",
        context_window_tokens=20,
        provider_input_limit_tokens=None,
        max_input_characters=None,
        count_input_tokens=count_exact_wire,
    )
    resolved = ContextRuntime().resolve(
        _packet(sections),
        policy,
        enforce_budget=True,
        allow_synthetic_budget=True,
        model_capability=capability,
        resolved_model="fixture-resolved-model",
        require_model_capacity=True,
        requested_output_tokens=3,
    )
    return resolved, observed_wire


def test_resolved_model_capacity_counts_exact_wire_without_double_framing_reserve():
    resolved, observed_wire = _resolve_with_capacity(counted_tokens=17)

    assert resolved.budget_enforced is True
    assert observed_wire
    assert all(
        tuple((message.role, message.content) for message in messages)
        == tuple((message.role, message.content) for message in observed_wire[0])
        for messages in observed_wire
    )
    assert tuple(message.role for message in observed_wire[0]) == ("system", "user")


def test_resolved_model_capacity_rejects_exact_wire_over_window_minus_output():
    with pytest.raises(ContextContractError, match="context_budget_exceeded"):
        _resolve_with_capacity(counted_tokens=18)


def test_default_registry_is_closed_and_assessment_names_missing_profile():
    readiness = _readiness_module()

    assessment = readiness.DEFAULT_PRIVATE_READINESS_REGISTRY.assess(
        logical_model="gpt-5.6-terra",
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
    )

    assert readiness.DEFAULT_PRIVATE_READINESS_REGISTRY.profile_count == 0
    assert assessment.ready is False
    assert assessment.reason == "readiness_profile_missing"


def test_logical_and_resolved_model_are_separate_but_may_have_same_value():
    _readiness, profile = _profile(
        logical_model="gpt-5.6-terra",
        resolved_model="gpt-5.6-terra",
    )

    assert profile.logical_model == profile.capability.model == "gpt-5.6-terra"


def test_reasoning_is_profile_specific_and_exact_terra_requires_low():
    _readiness, non_terra = _profile(reasoning_effort="medium")
    assert non_terra.reasoning_effort == "medium"

    with pytest.raises(ContextContractError, match="invalid_readiness_reasoning"):
        _profile(
            logical_model="gpt-5.6-terra",
            resolved_model="gpt-5.6-terra",
            reasoning_effort="high",
        )

    with pytest.raises(ContextContractError, match="invalid_readiness_model"):
        _profile(
            logical_model="nana-main",
            resolved_model="gpt-5.6-terra",
        )

    with pytest.raises(ContextContractError, match="invalid_readiness_model"):
        _profile(
            logical_model="gpt-5.6-terra",
            resolved_model="fixture-resolved-model",
        )


def test_registry_issues_only_exact_live_attested_grants():
    readiness, profile = _profile()
    registry = readiness.PrivateReadinessRegistry((profile,), grant_capacity=2)
    grant = registry.issue(
        logical_model=profile.logical_model,
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
    )

    assessment = registry.assess(
        logical_model=profile.logical_model,
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
    )
    assert assessment.ready is False
    assert assessment.reason == "readiness_profile_reviewed_adoption_required"

    assert readiness.require_private_readiness_grant(
        grant,
        logical_model=profile.logical_model,
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
    ) is profile
    for logical_model, lane, route, reason in (
        ("other-model", readiness.Lane.PRIVATE_OWNER, readiness.Route.INTERACTIVE,
         "readiness_model_mismatch"),
        (profile.logical_model, readiness.Lane.PUBLIC_STAGE, readiness.Route.INTERACTIVE,
         "readiness_lane_mismatch"),
        (profile.logical_model, readiness.Lane.PRIVATE_OWNER, readiness.Route.AUTONOMY,
         "readiness_route_mismatch"),
    ):
        with pytest.raises(ContextContractError, match=reason):
            readiness.require_private_readiness_grant(
                grant,
                logical_model=logical_model,
                lane=lane,
                route=route,
            )


def test_fabricated_revoked_stale_and_changed_counter_grants_fail_closed():
    readiness, profile = _profile()
    registry = readiness.PrivateReadinessRegistry((profile,), grant_capacity=1)
    grant = registry.issue(
        logical_model=profile.logical_model,
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
    )
    fabricated = object.__new__(type(grant))
    for name in ("_registry", "_profile", "_serial"):
        object.__setattr__(fabricated, name, getattr(grant, name))
    with pytest.raises(ContextContractError, match="readiness_grant_unavailable"):
        readiness.require_private_readiness_grant(
            fabricated,
            logical_model=profile.logical_model,
            lane=readiness.Lane.PRIVATE_OWNER,
            route=readiness.Route.INTERACTIVE,
        )

    registry.revoke(grant)
    with pytest.raises(ContextContractError, match="readiness_grant_revoked"):
        readiness.require_private_readiness_grant(
            grant,
            logical_model=profile.logical_model,
            lane=readiness.Lane.PRIVATE_OWNER,
            route=readiness.Route.INTERACTIVE,
        )

    stale = registry.issue(
        logical_model=profile.logical_model,
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
    )
    registry.issue(
        logical_model=profile.logical_model,
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
    )
    with pytest.raises(ContextContractError, match="readiness_grant_unavailable"):
        readiness.require_private_readiness_grant(
            stale,
            logical_model=profile.logical_model,
            lane=readiness.Lane.PRIVATE_OWNER,
            route=readiness.Route.INTERACTIVE,
        )

    live = registry.issue(
        logical_model=profile.logical_model,
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
    )
    object.__setattr__(profile.capability, "count_input_tokens", lambda _messages: 1)
    with pytest.raises(ContextContractError, match="readiness_profile_changed"):
        readiness.require_private_readiness_grant(
            live,
            logical_model=profile.logical_model,
            lane=readiness.Lane.PRIVATE_OWNER,
            route=readiness.Route.INTERACTIVE,
        )


def test_assessment_distinguishes_missing_counter_capacity_and_route():
    readiness, counter_profile = _profile()
    counter_registry = readiness.PrivateReadinessRegistry((counter_profile,))
    object.__setattr__(counter_profile.capability, "count_input_tokens", None)
    assert counter_registry.assess(
        logical_model=counter_profile.logical_model,
        lane=counter_profile.lane,
        route=counter_profile.route,
    ).reason == "readiness_counter_unavailable"

    readiness, capacity_profile = _profile()
    capacity_registry = readiness.PrivateReadinessRegistry((capacity_profile,))
    object.__setattr__(capacity_profile.capability, "context_window_tokens", 0)
    assert capacity_registry.assess(
        logical_model=capacity_profile.logical_model,
        lane=capacity_profile.lane,
        route=capacity_profile.route,
    ).reason == "readiness_capacity_unavailable"

    readiness, route_profile = _profile()
    route_registry = readiness.PrivateReadinessRegistry((route_profile,))
    object.__setattr__(route_profile, "endpoint_fingerprint", "0" * 63)
    assert route_registry.assess(
        logical_model=route_profile.logical_model,
        lane=route_profile.lane,
        route=route_profile.route,
    ).reason == "readiness_route_unavailable"


def test_bound_plan_uses_exact_wire_counter_and_stable_route_metadata():
    observed_wire = []

    def count_wire(messages):
        observed_wire.append(messages)
        return sum(len(message.content.encode("utf-8")) for message in messages)

    readiness, profile, registry, _grant, first, request, runtime = _bound_plan(
        counter=count_wire
    )
    second_grant = registry.issue(
        logical_model=request.model,
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
    )
    _, _, _, sections = _turn_fixture()
    second = runtime.build_turn_plan(
        runtime.GptTurnInput(
            request=request,
            sections=sections,
            evidence=None,
            model=request.model,
            max_output_tokens=200,
            budget_revision=runtime.PRIVATE_POLICY_REVISION,
            readiness=second_grant,
        )
    )

    manifest = first.compiled.manifest
    assert manifest.resolved_provider == "llmgate"
    assert manifest.resolved_model == profile.capability.model
    assert manifest.compiler_config_revision == second.compiled.manifest.compiler_config_revision
    assert manifest.compiler_config_revision != request.compiler_config_revision
    assert first.compiled.messages == second.compiled.messages
    assert first.compiled.full_context_hash == second.compiled.full_context_hash
    assert any(messages == first.compiled.messages for messages in observed_wire)
    bound = readiness.require_compiled_private_readiness(
        first.compiled,
        requested_output_tokens=200,
    )
    assert (bound.logical_model, bound.resolved_model, bound.reasoning_effort) == (
        request.model,
        profile.capability.model,
        "low",
    )


def test_bound_normal_and_story_plans_both_enforce_reviewed_capacity():
    for story, expected_reserve in ((False, 1_000), (True, 1_600)):
        readiness, profile, _registry, _grant, plan, _request_value, _runtime = (
            _bound_plan(story=story, max_completion_tokens=expected_reserve)
        )
        bound = readiness.require_compiled_private_readiness(
            plan.compiled,
            requested_output_tokens=200,
        )
        assert bound.max_completion_tokens == expected_reserve
        assert plan.compiled.manifest.resolved_model == profile.capability.model


def test_stable_route_is_prefix_comparable_and_changed_route_is_incompatible():
    readiness, _profile_one, registry, _grant, first, request, _runtime = _bound_plan()
    second_grant = registry.issue(
        logical_model=request.model,
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
    )
    contracts, runtime, same_request, sections = _turn_fixture()
    second = runtime.build_turn_plan(
        runtime.GptTurnInput(
            same_request,
            sections,
            None,
            same_request.model,
            200,
            runtime.PRIVATE_POLICY_REVISION,
            None,
            second_grant,
        )
    )
    _readiness, profile_changed = _profile(
        logical_model=request.model,
        route_revision="fixture-route.v2",
    )
    changed_registry = readiness.PrivateReadinessRegistry((profile_changed,))
    changed_grant = changed_registry.issue(
        logical_model=request.model,
        lane=contracts.Lane.PRIVATE_OWNER,
        route=contracts.Route.INTERACTIVE,
    )
    changed = runtime.build_turn_plan(
        runtime.GptTurnInput(
            same_request,
            sections,
            None,
            same_request.model,
            200,
            runtime.PRIVATE_POLICY_REVISION,
            None,
            changed_grant,
        )
    )
    telemetry = importlib.import_module("nana.runtime.context_telemetry")
    scope = "0" * 64
    first_fingerprint = telemetry.PrefixFingerprint.from_compiled(
        first.compiled,
        comparison_scope_digest=scope,
    )
    second_fingerprint = telemetry.PrefixFingerprint.from_compiled(
        second.compiled,
        comparison_scope_digest=scope,
    )
    changed_fingerprint = telemetry.PrefixFingerprint.from_compiled(
        changed.compiled,
        comparison_scope_digest=scope,
    )

    assert telemetry.compare_prefixes(first_fingerprint, second_fingerprint).status == (
        "compatible_equal"
    )
    assert telemetry.compare_prefixes(first_fingerprint, changed_fingerprint).status == (
        "incompatible"
    )


@pytest.mark.parametrize(
    "counter",
    [
        lambda _messages: False,
        lambda _messages: 0,
        lambda _messages: -1,
        lambda _messages: (_ for _ in ()).throw(RuntimeError("counter failed")),
    ],
)
def test_bound_plan_rejects_invalid_exact_counter(counter):
    with pytest.raises(ContextContractError, match="model_context_capacity_unavailable"):
        _bound_plan(counter=counter)


def test_bound_plan_checks_requested_and_policy_output_capacity():
    with pytest.raises(ContextContractError, match="readiness_output_limit_exceeded"):
        _bound_plan(max_completion_tokens=199, max_output_tokens=200)
    with pytest.raises(
        ContextContractError,
        match="readiness_output_capacity_unavailable",
    ):
        _bound_plan(max_completion_tokens=999, max_output_tokens=200)
    with pytest.raises(
        ContextContractError,
        match="readiness_output_capacity_unavailable",
    ):
        _bound_plan(story=True, max_completion_tokens=1_599, max_output_tokens=200)


def test_turn_input_revalidates_raw_fabricated_and_revoked_readiness():
    contracts, runtime, request, sections = _turn_fixture()
    with pytest.raises(ContextContractError, match="readiness_grant_unavailable"):
        runtime.GptTurnInput(
            request,
            sections,
            None,
            request.model,
            200,
            runtime.PRIVATE_POLICY_REVISION,
            None,
            {"ready": True},
        )

    readiness, profile = _profile(logical_model=request.model)
    registry = readiness.PrivateReadinessRegistry((profile,))
    grant = registry.issue(
        logical_model=request.model,
        lane=contracts.Lane.PRIVATE_OWNER,
        route=contracts.Route.INTERACTIVE,
    )
    inputs = runtime.GptTurnInput(
        request,
        sections,
        None,
        request.model,
        200,
        runtime.PRIVATE_POLICY_REVISION,
        None,
        grant,
    )
    registry.revoke(grant)
    with pytest.raises(ContextContractError, match="readiness_grant_revoked"):
        runtime.build_turn_plan(inputs)


def test_bound_compiled_ledger_does_not_retain_prompt_and_eviction_denies():
    readiness, _profile_one, registry, _grant, first, request, runtime = _bound_plan(
        binding_capacity=1
    )
    first_reference = weakref.ref(first.compiled)
    second_grant = registry.issue(
        logical_model=request.model,
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
    )
    _contracts, _runtime, same_request, sections = _turn_fixture(text="second query")
    second = runtime.build_turn_plan(
        runtime.GptTurnInput(
            same_request,
            sections,
            None,
            same_request.model,
            200,
            runtime.PRIVATE_POLICY_REVISION,
            None,
            second_grant,
        )
    )
    with pytest.raises(ContextContractError, match="readiness_binding_unavailable"):
        readiness.require_compiled_private_readiness(
            first.compiled,
            requested_output_tokens=200,
        )
    replacement_grant = registry.issue(
        logical_model=request.model,
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
    )
    with pytest.raises(ContextContractError, match="readiness_binding_already_issued"):
        readiness.bind_compiled_private_readiness(
            first.compiled,
            replacement_grant,
            requested_output_tokens=200,
            policy_output_reserve=1_000,
        )

    second_reference = weakref.ref(second.compiled)
    del first
    del second
    gc.collect()
    assert first_reference() is None
    assert second_reference() is None


def test_bound_compiled_context_cannot_be_rebound_to_replace_revoked_grant():
    readiness, _profile_value, registry, first_grant, plan, request, _runtime = (
        _bound_plan()
    )
    replacement_grant = registry.issue(
        logical_model=request.model,
        lane=readiness.Lane.PRIVATE_OWNER,
        route=readiness.Route.INTERACTIVE,
    )

    with pytest.raises(ContextContractError, match="readiness_binding_already_issued"):
        readiness.bind_compiled_private_readiness(
            plan.compiled,
            replacement_grant,
            requested_output_tokens=200,
            policy_output_reserve=1_000,
        )
    registry.revoke(first_grant)
    with pytest.raises(ContextContractError, match="readiness_binding_already_issued"):
        readiness.bind_compiled_private_readiness(
            plan.compiled,
            replacement_grant,
            requested_output_tokens=200,
            policy_output_reserve=1_000,
        )
    with pytest.raises(ContextContractError, match="readiness_grant_revoked"):
        readiness.require_compiled_private_readiness(
            plan.compiled,
            requested_output_tokens=200,
        )


def test_bound_sync_and_stream_use_one_strict_snapshot_and_exact_low(monkeypatch):
    readiness, profile, _registry, _grant, plan, _request_value, _runtime = _bound_plan(
        resolved_model="gpt-5.6-terra",
    )
    client = _llmgate_client(monkeypatch)
    strict_calls = []
    payloads = []

    def strict(logical_model, resolved_model):
        strict_calls.append((logical_model, resolved_model))
        return _strict_fixture_config(endpoint=" https://llmgate.invalid/v1/ ")

    urls = []

    def post(url, **kwargs):
        urls.append(url)
        payloads.append(kwargs["json"].copy())
        if kwargs.get("stream"):
            return _Response()
        return _Response(
            {
                "choices": [{"message": {"content": "bound sync"}}],
                "provider": "fixture-provider",
                "model": "gpt-5.6-terra",
            }
        )

    monkeypatch.setattr(client, "load_llmgate_model_strict", strict)
    monkeypatch.setattr(
        client,
        "load_llmgate_model",
        lambda *_args: (_ for _ in ()).throw(AssertionError("legacy resolver reached")),
    )
    monkeypatch.setattr(client, "_http_post", post)
    _install_sse(
        [
            '{"model":"gpt-5.6-terra","choices":[{"delta":{"content":"bound "}}]}',
            '{"choices":[{"delta":{"content":"stream"}}]}',
            '{"choices":[],"usage":{"input_tokens":12,"output_tokens":2,"total_tokens":14}}',
            "[DONE]",
        ]
    )

    content, debug, receipt = client.call_llmgate_compiled(
        plan.compiled,
        max_tokens=200,
        temperature=0.75,
    )
    chunks = list(
        client.stream_llmgate_compiled(
            plan.compiled,
            max_tokens=200,
            temperature=0.75,
        )
    )

    assert (content, debug, chunks) == ("bound sync", "ok", ["bound ", "stream"])
    assert strict_calls == [
        (plan.model, profile.capability.model),
        (plan.model, profile.capability.model),
    ]
    assert [payload["model"] for payload in payloads] == ["gpt-5.6-terra"] * 2
    assert urls == ["https://llmgate.invalid/v1/chat/completions"] * 2
    assert [payload["reasoning_effort"] for payload in payloads] == ["low"] * 2
    assert [payload["messages"] for payload in payloads] == [
        plan.transport_messages(),
        plan.transport_messages(),
    ]
    assert receipt.resolved_model == "gpt-5.6-terra"
    assert readiness.require_compiled_private_readiness(
        plan.compiled,
        requested_output_tokens=200,
    ).endpoint_fingerprint == profile.endpoint_fingerprint


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("config", "error"),
    [
        (_strict_fixture_config(resolved_model="wrong-model"), "readiness_model_drift"),
        (
            _strict_fixture_config(endpoint="https://different.invalid/v1"),
            "readiness_endpoint_drift",
        ),
    ],
)
def test_bound_transport_rejects_model_and_endpoint_drift_before_http(
    monkeypatch,
    stream,
    config,
    error,
):
    _readiness, _profile_value, _registry, _grant, plan, _request_value, _runtime = (
        _bound_plan(resolved_model="gpt-5.6-terra")
    )
    client = _llmgate_client(monkeypatch)
    calls = {"strict": 0, "http": 0}

    def strict(*_args):
        calls["strict"] += 1
        return dict(config)

    monkeypatch.setattr(client, "load_llmgate_model_strict", strict)
    monkeypatch.setattr(
        client,
        "_http_post",
        lambda *_args, **_kwargs: calls.__setitem__("http", calls["http"] + 1),
    )
    with pytest.raises(ContextContractError, match=error):
        if stream:
            list(
                client.stream_llmgate_compiled(
                    plan.compiled,
                    max_tokens=200,
                    temperature=0.75,
                )
            )
        else:
            client.call_llmgate_compiled(
                plan.compiled,
                max_tokens=200,
                temperature=0.75,
            )
    assert calls == {"strict": 1, "http": 0}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("api_key", ["   ", "${MISSING_LLMGATE_KEY}"])
def test_bound_transport_rejects_unresolved_or_blank_credentials_before_http(
    monkeypatch,
    stream,
    api_key,
):
    _readiness, _profile_value, _registry, _grant, plan, _request_value, _runtime = (
        _bound_plan(resolved_model="gpt-5.6-terra")
    )
    client = _llmgate_client(monkeypatch)
    calls = {"strict": 0, "http": 0}

    def strict(*_args):
        calls["strict"] += 1
        return _strict_fixture_config(api_key=api_key)

    monkeypatch.setattr(client, "load_llmgate_model_strict", strict)
    monkeypatch.setattr(
        client,
        "_http_post",
        lambda *_args, **_kwargs: calls.__setitem__("http", calls["http"] + 1),
    )
    with pytest.raises(ContextContractError, match="readiness_credentials_unavailable") as caught:
        if stream:
            list(
                client.stream_llmgate_compiled(
                    plan.compiled,
                    max_tokens=200,
                    temperature=0.75,
                )
            )
        else:
            client.call_llmgate_compiled(
                plan.compiled,
                max_tokens=200,
                temperature=0.75,
            )
    assert api_key not in str(caught.value)
    assert calls == {"strict": 1, "http": 0}


@pytest.mark.parametrize("response_model", [None, "wrong-model"])
def test_bound_sync_requires_exact_response_model(monkeypatch, response_model):
    _readiness, _profile_value, _registry, _grant, plan, _request_value, _runtime = (
        _bound_plan(resolved_model="gpt-5.6-terra")
    )
    client = _llmgate_client(monkeypatch)
    data = {"choices": [{"message": {"content": "must not escape"}}]}
    if response_model is not None:
        data["model"] = response_model
    monkeypatch.setattr(
        client,
        "load_llmgate_model_strict",
        lambda *_args: _strict_fixture_config(),
    )
    monkeypatch.setattr(client, "_http_post", lambda *_args, **_kwargs: _Response(data))

    with pytest.raises(
        ContextContractError,
        match="readiness_response_model_unverified|readiness_response_model_mismatch",
    ):
        client.call_llmgate_compiled(
            plan.compiled,
            max_tokens=200,
            temperature=0.75,
        )


@pytest.mark.parametrize(
    ("events", "error"),
    [
        (
            ['{"choices":[{"delta":{"content":"must not escape"}}]}'],
            "readiness_response_model_unverified",
        ),
        (
            ['{"model":"wrong-model","choices":[{"delta":{"content":"must not escape"}}]}'],
            "readiness_response_model_mismatch",
        ),
        (
            [
                '{"model":"gpt-5.6-terra","choices":[{"delta":{"content":"first"}}]}',
                '{"model":"wrong-model","choices":[{"delta":{"content":"second"}}]}',
            ],
            "readiness_response_model_mismatch",
        ),
    ],
)
def test_bound_stream_requires_and_preserves_exact_response_model(
    monkeypatch,
    events,
    error,
):
    _readiness, _profile_value, _registry, _grant, plan, _request_value, _runtime = (
        _bound_plan(resolved_model="gpt-5.6-terra")
    )
    client = _llmgate_client(monkeypatch)
    monkeypatch.setattr(
        client,
        "load_llmgate_model_strict",
        lambda *_args: _strict_fixture_config(),
    )
    monkeypatch.setattr(client, "_http_post", lambda *_args, **_kwargs: _Response())
    _install_sse([*events, "[DONE]"])

    with pytest.raises(ContextContractError, match=error):
        list(
            client.stream_llmgate_compiled(
                plan.compiled,
                max_tokens=200,
                temperature=0.75,
            )
        )


@pytest.mark.parametrize(
    "events",
    [
        ['{"choices":[],"usage":{"input_tokens":12,"output_tokens":0,"total_tokens":12}}'],
        [],
    ],
)
def test_bound_stream_requires_terminal_model_proof_without_text(monkeypatch, events):
    _readiness, _profile_value, _registry, _grant, plan, _request_value, _runtime = (
        _bound_plan(resolved_model="gpt-5.6-terra")
    )
    client = _llmgate_client(monkeypatch)
    monkeypatch.setattr(
        client,
        "load_llmgate_model_strict",
        lambda *_args: _strict_fixture_config(),
    )
    monkeypatch.setattr(client, "_http_post", lambda *_args, **_kwargs: _Response())
    _install_sse([*events, "[DONE]"])

    with pytest.raises(ContextContractError, match="readiness_response_model_unverified"):
        list(
            client.stream_llmgate_compiled(
                plan.compiled,
                max_tokens=200,
                temperature=0.75,
            )
        )


def test_bound_stream_allows_exact_model_usage_only_terminal_event_without_text(
    monkeypatch,
):
    _readiness, _profile_value, _registry, _grant, plan, _request_value, _runtime = (
        _bound_plan(resolved_model="gpt-5.6-terra")
    )
    client = _llmgate_client(monkeypatch)
    monkeypatch.setattr(
        client,
        "load_llmgate_model_strict",
        lambda *_args: _strict_fixture_config(),
    )
    monkeypatch.setattr(client, "_http_post", lambda *_args, **_kwargs: _Response())
    _install_sse(
        [
            '{"model":"gpt-5.6-terra","choices":[],"usage":{"input_tokens":12,"output_tokens":0,"total_tokens":12}}',
            "[DONE]",
        ]
    )

    assert list(
        client.stream_llmgate_compiled(
            plan.compiled,
            max_tokens=200,
            temperature=0.75,
        )
    ) == []


@pytest.mark.parametrize("stream", [False, True])
def test_revoked_bound_transport_fails_before_credentials_or_http(monkeypatch, stream):
    _readiness, _profile_value, registry, grant, plan, _request_value, _runtime = (
        _bound_plan(resolved_model="gpt-5.6-terra")
    )
    registry.revoke(grant)
    client = _llmgate_client(monkeypatch)
    calls = {"strict": 0, "http": 0}
    monkeypatch.setattr(
        client,
        "load_llmgate_model_strict",
        lambda *_args: calls.__setitem__("strict", calls["strict"] + 1),
        raising=False,
    )
    monkeypatch.setattr(
        client,
        "_http_post",
        lambda *_args, **_kwargs: calls.__setitem__("http", calls["http"] + 1),
    )

    with pytest.raises(ContextContractError, match="readiness_grant_revoked"):
        if stream:
            list(
                client.stream_llmgate_compiled(
                    plan.compiled,
                    max_tokens=200,
                    temperature=0.75,
                )
            )
        else:
            client.call_llmgate_compiled(
                plan.compiled,
                max_tokens=200,
                temperature=0.75,
            )
    assert calls == {"strict": 0, "http": 0}


@pytest.mark.parametrize("stream", [False, True])
def test_bound_transport_rechecks_grant_after_route_load(monkeypatch, stream):
    _readiness, _profile_value, registry, grant, plan, _request_value, _runtime = (
        _bound_plan(resolved_model="gpt-5.6-terra")
    )
    client = _llmgate_client(monkeypatch)
    calls = {"strict": 0, "http": 0}

    def strict(*_args):
        calls["strict"] += 1
        registry.revoke(grant)
        return _strict_fixture_config()

    monkeypatch.setattr(client, "load_llmgate_model_strict", strict)
    monkeypatch.setattr(
        client,
        "_http_post",
        lambda *_args, **_kwargs: calls.__setitem__("http", calls["http"] + 1),
    )
    with pytest.raises(ContextContractError, match="readiness_grant_revoked"):
        if stream:
            list(
                client.stream_llmgate_compiled(
                    plan.compiled,
                    max_tokens=200,
                    temperature=0.75,
                )
            )
        else:
            client.call_llmgate_compiled(
                plan.compiled,
                max_tokens=200,
                temperature=0.75,
            )
    assert calls == {"strict": 1, "http": 0}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    ("max_tokens", "reasoning_effort", "error"),
    [
        (201, None, "readiness_output_binding_mismatch"),
        (200, "high", "readiness_reasoning_override_unsupported"),
    ],
)
def test_bound_transport_rejects_output_and_reasoning_expansion_before_credentials(
    monkeypatch,
    stream,
    max_tokens,
    reasoning_effort,
    error,
):
    _readiness, _profile_value, _registry, _grant, plan, _request_value, _runtime = (
        _bound_plan(resolved_model="gpt-5.6-terra")
    )
    client = _llmgate_client(monkeypatch)
    calls = {"strict": 0, "http": 0}
    monkeypatch.setattr(
        client,
        "load_llmgate_model_strict",
        lambda *_args: calls.__setitem__("strict", calls["strict"] + 1),
        raising=False,
    )
    monkeypatch.setattr(
        client,
        "_http_post",
        lambda *_args, **_kwargs: calls.__setitem__("http", calls["http"] + 1),
    )
    with pytest.raises(ContextContractError, match=error):
        if stream:
            list(
                client.stream_llmgate_compiled(
                    plan.compiled,
                    max_tokens=max_tokens,
                    temperature=0.75,
                    reasoning_effort=reasoning_effort,
                )
            )
        else:
            client.call_llmgate_compiled(
                plan.compiled,
                max_tokens=max_tokens,
                temperature=0.75,
                reasoning_effort=reasoning_effort,
            )
    assert calls == {"strict": 0, "http": 0}


def test_strict_resolver_never_uses_clone_first_endpoint_fallback(monkeypatch):
    client = _llmgate_client(monkeypatch)
    monkeypatch.setattr(
        client,
        "load_llmgate_settings",
        lambda: {
            "customModels": [
                {
                    "displayName": "configured-model",
                    "model": "configured-model",
                    "apiKey": "fixture-key",
                    "baseUrl": "https://llmgate.invalid/v1",
                }
            ]
        },
    )
    monkeypatch.setattr(
        client,
        "clone_llmgate_base_model",
        lambda *_args: (_ for _ in ()).throw(AssertionError("clone fallback reached")),
    )

    assert client.load_llmgate_model_strict("arbitrary-model", "arbitrary-model") is None
    exact = client.load_llmgate_model_strict("configured-model", "configured-model")
    assert exact["model"] == "configured-model"


def test_unbound_compiled_transport_keeps_legacy_resolver_compatibility(monkeypatch):
    _contracts, runtime, request, sections = _turn_fixture()
    plan = runtime.build_turn_plan(
        runtime.GptTurnInput(
            request,
            sections,
            None,
            request.model,
            200,
            runtime.PRIVATE_POLICY_REVISION,
        )
    )
    client = _llmgate_client(monkeypatch)
    calls = {"legacy": 0, "strict": 0}

    def legacy(_model):
        calls["legacy"] += 1
        return _strict_fixture_config(resolved_model="legacy-resolved")

    monkeypatch.setattr(client, "load_llmgate_model", legacy)
    monkeypatch.setattr(
        client,
        "load_llmgate_model_strict",
        lambda *_args: calls.__setitem__("strict", calls["strict"] + 1),
        raising=False,
    )
    monkeypatch.setattr(
        client,
        "_http_post",
        lambda *_args, **_kwargs: _Response(
            {"choices": [{"message": {"content": "legacy compatible"}}]}
        ),
    )

    content, debug, _receipt = client.call_llmgate_compiled(
        plan.compiled,
        max_tokens=200,
        temperature=0.75,
    )

    assert (content, debug) == ("legacy compatible", "ok")
    assert calls == {"legacy": 1, "strict": 0}
