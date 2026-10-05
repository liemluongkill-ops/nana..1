"""Focused Task 14 tests for the public-only YouTube CUM2 profile."""

from __future__ import annotations

import importlib
import builtins
from dataclasses import replace
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))


NOW = 1_700_000_200.0


@pytest.fixture(scope="module")
def runtime_modules():
    before = {
        name: module
        for name, module in sys.modules.items()
        if name == "nana" or name.startswith("nana.")
    }
    for name in tuple(before):
        sys.modules.pop(name, None)
    package = types.ModuleType("nana")
    package.__path__ = [str(ROOT)]
    runtime_package = types.ModuleType("nana.runtime")
    runtime_package.__path__ = [str(ROOT / "runtime")]
    sys.modules["nana"] = package
    sys.modules["nana.runtime"] = runtime_package
    try:
        yield SimpleNamespace(
            context_runtime=importlib.import_module("nana.runtime.context_runtime"),
            contracts=importlib.import_module("nana.runtime.context_contracts"),
            boundary=importlib.import_module("nana.runtime.public_context_boundary"),
            identity=importlib.import_module("nana.runtime.public_identity"),
            social=importlib.import_module("nana.runtime.social_session"),
        )
    finally:
        for name in tuple(sys.modules):
            if name == "nana" or name.startswith("nana."):
                sys.modules.pop(name, None)
        sys.modules.update(before)


def _scope(
    event_id: str = "yt-event-current",
    *,
    display_name: str = "Minh",
    author_id: str = "yt-channel-current",
    room_id: str = "yt-live-chat-1",
    session_id: str = "yt-session-1",
):
    boundary = importlib.import_module("nana.runtime.public_context_boundary")
    identity_module = importlib.import_module("nana.runtime.public_identity")
    identity = identity_module.CanonicalPublicIdentity(
        platform="youtube",
        author_id=author_id,
        actor_key=f"youtube:{author_id}",
    )
    return boundary.PublicEventScope(
        platform="youtube",
        room_id=room_id,
        stream_session_id=session_id,
        event_id=event_id,
        display_name=display_name,
        identity=identity,
    )


def _turn(modules, text: str, *, scope=None, revision: int = 0, attempt_id: str = "ingress-1"):
    scope = scope or _scope()
    return modules.social.PublicTurn(
        timestamp=NOW - 1,
        monotonic=NOW / 2,
        viewer_name=scope.display_name,
        event_type="text",
        director_mode="chill",
        message_preview=text[:96],
        scope=scope,
        revision=revision,
        attempt_id=attempt_id,
    )


def _tracking_session(modules):
    class TrackingSession(modules.social.SocialSessionCache):
        def __init__(self):
            super().__init__(clock=lambda: NOW)
            self.capture_calls = 0
            self.record_calls = 0
            self.format_calls = 0

        def capture_continuity_source(self, request):
            self.capture_calls += 1
            return super().capture_continuity_source(request)

        def record_public_turn(self, **kwargs):
            self.record_calls += 1
            return super().record_public_turn(**kwargs)

        def format_public_room_context(self, *args, **kwargs):
            self.format_calls += 1
            return super().format_public_room_context(*args, **kwargs)

    return TrackingSession()


def _deliver(modules, session, scope, reply: str) -> None:
    delivery = importlib.import_module("nana.runtime.public_delivery_state")
    generated = delivery.PublicDeliveryRecord(
        scope.event_id,
        f"output-{scope.event_id}",
        "generated",
        f"delivery-{scope.event_id}",
        0,
        reply,
        scope,
        NOW - 10,
    )
    session.record_reply_context(
        scope=scope,
        delivery_record=generated,
        now=NOW,
        monotonic_now=NOW / 2,
    )
    for revision, state in enumerate(("published", "playback_started", "delivered"), 1):
        session.record_reply_context(
            scope=scope,
            delivery_record=replace(
                generated,
                state=state,
                revision=revision,
                updated_at=NOW - 10 + revision,
            ),
            now=NOW,
            monotonic_now=NOW / 2,
        )


class _LegacyCaller:
    def __init__(self, reply="Mình đang nghe đây."):
        self.reply = reply
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply, "ok"


class _CompiledCaller:
    def __init__(self, reply="Mình đang nghe đây."):
        self.reply = reply
        self.calls = []

    def __call__(self, compiled, **kwargs):
        self.calls.append((compiled, kwargs))
        return self.reply, "ok", SimpleNamespace(status="complete")


class _TelemetrySink:
    def __init__(self, *, raises=False):
        self.raises = raises
        self.receipts = []

    def emit(self, receipt):
        if self.raises:
            raise RuntimeError("optional telemetry failed")
        self.receipts.append(receipt)


def _generate(modules, monkeypatch, generator, turn, source_text, *, mode=None, output_id="output-1"):
    monkeypatch.setenv("NANA_STREAM_CUM2_RESPONSE_ENABLED", "1")
    if mode is None:
        monkeypatch.delenv("NANA_CONTEXT_CUM2_MODE", raising=False)
    else:
        monkeypatch.setenv("NANA_CONTEXT_CUM2_MODE", mode)
    return generator.generate(
        turn,
        source_text=source_text,
        output_id=output_id,
        now=NOW,
    )


def test_public_cum2_builder_compiles_only_the_registered_public_profile(
    runtime_modules,
) -> None:
    """Removing the sealed CUM2 builder must break public profile compilation."""

    context_runtime = runtime_modules.context_runtime
    Lane = runtime_modules.contracts.Lane
    Route = runtime_modules.contracts.Route
    assert hasattr(context_runtime, "resolve_public_cum2_request")
    assert hasattr(context_runtime, "build_public_cum2_plan")

    scope = _scope()
    request = context_runtime.resolve_public_cum2_request(
        scope=scope,
        current_input="Xin chao tu CUM2",
        request_id="cum2-output-1",
        correlation_id="cum2-correlation-1",
        wall_clock=lambda: NOW,
        monotonic_clock=lambda: NOW / 2,
    )
    session = runtime_modules.social.SocialSessionCache(clock=lambda: NOW)
    continuity = session.capture_continuity_source(request)
    compiled = context_runtime.build_public_cum2_plan(request, continuity)

    assert compiled.lane is Lane.PUBLIC_STAGE
    assert compiled.route is Route.YOUTUBE_CUM2
    assert compiled.manifest.budget_policy_id == "budget.shadow.public_cum2.v1"
    assert (
        compiled.manifest.budget_policy_revision
        == "public-cum2.measurement.2026-10-03"
    )
    assert (
        compiled.manifest.budget_policy_status
        is runtime_modules.contracts.BudgetPolicyStatus.SHADOW_CANDIDATE
    )
    assert compiled.manifest.budget_enforced is False
    assert tuple(row.section_id for row in compiled.manifest.sections) == (
        "core.public.v1",
        "policy.public.livestream.v1",
        "contract.output.cum2.v1",
        "expression.public.v1",
        "public.request_context.v1",
    )
    assert [message.role for message in compiled.messages] == ["system", "user"]
    assert compiled.messages[-1].content == "Xin chao tu CUM2"
    assert "memory.public_grounding.v1" not in compiled.messages[0].content
    decisions = {row.source: row for row in compiled.manifest.collection}
    for denied in ("owner_identity", "private_memory", "private_session", "runtime_state"):
        assert decisions[denied].decision == "DENY_NOT_READ"
        assert decisions[denied].source_revision is None


def test_default_legacy_preserves_exact_messages_and_artifact_contract(
    runtime_modules,
    monkeypatch,
) -> None:
    """Selecting the wrong default or altering legacy bytes must fail this test."""

    stream = importlib.import_module("nana.runtime.stream_cum2_response")
    text = "Nana oi, minh ke tiep nhe"
    scope = _scope()
    expected_session = runtime_modules.social.SocialSessionCache(clock=lambda: NOW)
    expected_session.record_public_turn(
        scope=scope,
        text=text,
        revision=0,
        attempt_id="ingress-1",
    )
    expected = stream._public_messages(
        scope,
        text,
        expected_session.format_public_room_context(scope=scope, limit=5),
    )

    session = _tracking_session(runtime_modules)
    legacy = _LegacyCaller("Ba ơi, con đang nghe đây.")
    compiled = _CompiledCaller()
    generator = stream.PublicResponseGenerator(
        caller=legacy,
        compiled_caller=compiled,
        session_context=session,
    )
    result = _generate(
        runtime_modules,
        monkeypatch,
        generator,
        _turn(runtime_modules, text, scope=scope),
        text,
    )

    assert result.status == "generated"
    assert result.artifact is not None
    assert result.artifact.scope is scope
    assert result.artifact.output_id == "output-1"
    assert result.artifact.source_attempt_id == "ingress-1"
    assert result.artifact.model_route == "nana-public"
    assert result.artifact.generation_state == "generated"
    assert not hasattr(result.artifact, "delivery_state")
    assert "Ba" not in result.artifact.text and "con " not in result.artifact.text.lower()
    assert len(legacy.calls) == 1 and compiled.calls == []
    assert legacy.calls[0]["messages"] == expected
    assert legacy.calls[0]["model_name"] == "nana-public"
    assert legacy.calls[0]["max_tokens"] == 180
    assert legacy.calls[0]["temperature"] == 0.75
    assert legacy.calls[0]["timeout_s"] == 30.0
    assert session.record_calls == 1
    assert session.capture_calls == 0
    assert session.format_calls == 1


def test_shadow_sends_legacy_but_emits_distinct_candidate_and_sent_hashes(
    runtime_modules,
    monkeypatch,
) -> None:
    """Replacing the shadow legacy payload or conflating its hashes must fail."""

    stream = importlib.import_module("nana.runtime.stream_cum2_response")
    session = _tracking_session(runtime_modules)
    prior_scope = _scope(
        "yt-event-prior",
        display_name="Older Label",
        author_id="yt-channel-prior",
    )
    current_text = "same visible text from a distinct event"
    session.record_public_turn(scope=prior_scope, text=current_text)
    _deliver(runtime_modules, session, prior_scope, "Older delivered Nana reply")
    baseline_record_calls = session.record_calls
    legacy = _LegacyCaller("Mình nghe rồi nha.")
    compiled = _CompiledCaller()
    sink = _TelemetrySink()
    generator = stream.PublicResponseGenerator(
        caller=legacy,
        compiled_caller=compiled,
        session_context=session,
        telemetry_sink=sink,
    )
    turn = _turn(runtime_modules, current_text)

    result = _generate(
        runtime_modules,
        monkeypatch,
        generator,
        turn,
        current_text,
        mode="shadow",
    )

    assert result.status == "generated"
    assert len(legacy.calls) == 1 and compiled.calls == []
    assert session.capture_calls == 1
    assert session.record_calls == baseline_record_calls + 1
    assert session.format_calls == 1
    assert [receipt.label for receipt in sink.receipts] == [
        "candidate_context",
        "sent_context",
    ]
    candidate, sent = sink.receipts
    assert candidate.production_bound is False
    assert sent.production_bound is True
    assert candidate.manifest.full_context_hash != sent.manifest.full_context_hash
    assert candidate.manifest.request_id == sent.manifest.request_id
    assert candidate.manifest.correlation_id == sent.manifest.correlation_id
    candidate_system = next(
        receipt for receipt in sink.receipts if receipt.label == "candidate_context"
    ).manifest
    assert candidate_system.snapshot_revision >= 0
    legacy_payload = "\n".join(
        message["content"] for message in legacy.calls[0]["messages"]
    )
    # Legacy intentionally retains both room rows plus the final user message.
    assert legacy_payload.count(current_text) == 3


def test_real_canonical_readiness_refuses_even_an_injected_compiled_caller(
    runtime_modules,
    monkeypatch,
) -> None:
    """A fake caller must never bypass the production CUM2 budget gate."""

    stream = importlib.import_module("nana.runtime.stream_cum2_response")
    compiled = _CompiledCaller()
    legacy = _LegacyCaller()
    session = _tracking_session(runtime_modules)
    generator = stream.PublicResponseGenerator(
        caller=legacy,
        compiled_caller=compiled,
        session_context=session,
    )
    text = "Canonical must stay closed"

    result = _generate(
        runtime_modules,
        monkeypatch,
        generator,
        _turn(runtime_modules, text),
        text,
        mode="canonical",
    )

    assert result.status == "rejected"
    assert result.reason_code == "budget_policy_unapproved"
    assert result.artifact is None
    assert legacy.calls == [] and compiled.calls == []


def test_fake_readiness_uses_compiled_transport_without_mutable_messages(
    runtime_modules,
    monkeypatch,
) -> None:
    """Passing a message list instead of CompiledContext must fail this test."""

    stream = importlib.import_module("nana.runtime.stream_cum2_response")
    compiled = _CompiledCaller("Mình đang nghe, kể tiếp đi.")
    readiness_calls = []
    session = _tracking_session(runtime_modules)
    generator = stream.PublicResponseGenerator(
        caller=_LegacyCaller(),
        compiled_caller=compiled,
        session_context=session,
    )
    monkeypatch.setattr(
        runtime_modules.context_runtime,
        "require_cum2_canonical_dispatch_ready",
        lambda value: readiness_calls.append(value),
    )
    text = "Compile this exact public turn"

    result = _generate(
        runtime_modules,
        monkeypatch,
        generator,
        _turn(runtime_modules, text, revision=3, attempt_id="ingress-attempt-3"),
        text,
        mode="canonical",
        output_id="output-canonical-3",
    )

    assert result.status == "generated"
    assert len(readiness_calls) == 1
    assert len(compiled.calls) == 1
    sent, options = compiled.calls[0]
    assert sent is readiness_calls[0]
    assert options == {"max_tokens": 180, "temperature": 0.75, "timeout_s": 30.0}
    assert tuple(message.role for message in sent.messages) == ("system", "user")
    assert sent.messages[-1].content == text
    assert sent.messages[0].content.count(text) == 0
    assert sent.manifest.request_id.startswith("cum2-generation:")
    assert result.artifact.source_revision == 3
    assert result.artifact.source_attempt_id == "ingress-attempt-3"
    assert session.capture_calls == 1
    assert session.format_calls == 0


@pytest.mark.parametrize(
    ("mode", "source_text", "reason"),
    [
        ("not-a-mode", "accepted source", "invalid_context_mode"),
        ("shadow", "changed source", "source_text_mismatch"),
    ],
)
def test_rejected_mode_or_input_reads_no_context_and_calls_no_provider(
    runtime_modules,
    monkeypatch,
    mode,
    source_text,
    reason,
) -> None:
    """Moving validation after capture/provider must fail this test."""

    stream = importlib.import_module("nana.runtime.stream_cum2_response")
    session = _tracking_session(runtime_modules)
    legacy = _LegacyCaller()
    compiled = _CompiledCaller()
    generator = stream.PublicResponseGenerator(
        caller=legacy,
        compiled_caller=compiled,
        session_context=session,
    )
    turn = _turn(runtime_modules, "accepted source")
    result = _generate(
        runtime_modules,
        monkeypatch,
        generator,
        turn,
        source_text,
        mode=mode,
    )

    assert result.status == "rejected"
    assert result.reason_code == reason
    assert session.capture_calls == 0
    assert session.record_calls == 0
    assert session.format_calls == 0
    assert legacy.calls == [] and compiled.calls == []


def test_compiled_profile_preserves_exact_public_association_and_static_prefix(
    runtime_modules,
) -> None:
    """Embedding request labels or IDs in STATIC must fail this test."""

    runtime = runtime_modules.context_runtime
    first_scope = _scope(display_name="Display Alpha")
    second_scope = _scope(
        "yt-event-other",
        display_name="Display Beta",
        author_id="yt-channel-other",
        room_id="yt-live-chat-other",
        session_id="yt-session-other",
    )

    def compile_for(scope, captured_at):
        request = runtime.resolve_public_cum2_request(
            scope=scope,
            current_input="stable input",
            request_id=f"request-{scope.event_id}",
            correlation_id=f"correlation-{scope.event_id}",
            wall_clock=lambda: captured_at,
            monotonic_clock=lambda: captured_at / 2,
        )
        session = runtime_modules.social.SocialSessionCache(clock=lambda: captured_at)
        source = session.capture_continuity_source(request)
        return runtime.build_public_cum2_plan(request, source)

    first = compile_for(first_scope, NOW)
    second = compile_for(second_scope, NOW + 500)
    assert first.static_prefix_hash == second.static_prefix_hash
    assert first.manifest.static_prefix_chars == second.manifest.static_prefix_chars
    assert "Display Alpha" in first.messages[0].content
    assert "Display Beta" in second.messages[0].content
    for compiled, scope in ((first, first_scope), (second, second_scope)):
        system = compiled.messages[0].content
        assert scope.identity.actor_key not in system
        assert scope.identity.author_id not in system
        assert scope.event_id not in system
        assert scope.room_id not in system
        assert scope.stream_session_id not in system
        assert "memory.public_grounding.v1" not in system


def test_continuity_snapshot_excludes_current_before_bound_and_is_read_once(
    runtime_modules,
    monkeypatch,
) -> None:
    """Including current event or a non-delivered Nana reply must fail."""

    stream = importlib.import_module("nana.runtime.stream_cum2_response")
    session = _tracking_session(runtime_modules)
    current_text = "identical public text"
    for index in range(6):
        prior_scope = _scope(
            f"prior-{index}",
            display_name=f"Viewer {index}",
            author_id=f"author-{index}",
        )
        text = current_text if index == 5 else f"prior text {index}"
        session.record_public_turn(scope=prior_scope, text=text)
        if index % 2 == 0:
            _deliver(runtime_modules, session, prior_scope, f"delivered reply {index}")
    baseline_record_calls = session.record_calls
    compiled = _CompiledCaller()
    generator = stream.PublicResponseGenerator(
        caller=_LegacyCaller(),
        compiled_caller=compiled,
        session_context=session,
    )
    monkeypatch.setattr(
        runtime_modules.context_runtime,
        "require_cum2_canonical_dispatch_ready",
        lambda _compiled: None,
    )

    result = _generate(
        runtime_modules,
        monkeypatch,
        generator,
        _turn(runtime_modules, current_text),
        current_text,
        mode="canonical",
    )

    assert result.status == "generated"
    sent = compiled.calls[0][0]
    system = sent.messages[0].content
    assert session.capture_calls == 1
    assert session.record_calls == baseline_record_calls + 1
    assert system.count(current_text) == 1
    assert "prior text 0" not in system
    assert "prior text 1" in system
    assert "delivered reply 2" in system
    assert "delivered reply 4" in system
    assert "delivered reply 1" not in system
    assert "delivered reply 3" not in system


def test_shadow_telemetry_failure_cannot_change_legacy_result(
    runtime_modules,
    monkeypatch,
) -> None:
    """Letting an optional sink failure abort generation must fail this test."""

    stream = importlib.import_module("nana.runtime.stream_cum2_response")
    caller = _LegacyCaller("Mình vẫn trả lời bình thường.")
    generator = stream.PublicResponseGenerator(
        caller=caller,
        compiled_caller=_CompiledCaller(),
        session_context=_tracking_session(runtime_modules),
        telemetry_sink=_TelemetrySink(raises=True),
    )
    text = "Telemetry is optional"
    result = _generate(
        runtime_modules,
        monkeypatch,
        generator,
        _turn(runtime_modules, text),
        text,
        mode="shadow",
    )

    assert result.status == "generated"
    assert result.artifact is not None
    assert result.artifact.text == "Mình vẫn trả lời bình thường."
    assert len(caller.calls) == 1


def test_canonical_output_cap_and_generated_only_ownership_remain_unchanged(
    runtime_modules,
    monkeypatch,
) -> None:
    """Accepting raw replies above 1200 chars or making a delivery receipt must fail."""

    stream = importlib.import_module("nana.runtime.stream_cum2_response")
    compiled = _CompiledCaller("x" * 1201)
    generator = stream.PublicResponseGenerator(
        caller=_LegacyCaller(),
        compiled_caller=compiled,
        session_context=_tracking_session(runtime_modules),
    )
    monkeypatch.setattr(
        runtime_modules.context_runtime,
        "require_cum2_canonical_dispatch_ready",
        lambda _compiled: None,
    )
    text = "Keep the output cap"
    result = _generate(
        runtime_modules,
        monkeypatch,
        generator,
        _turn(runtime_modules, text),
        text,
        mode="canonical",
    )

    assert result.status == "failed"
    assert result.reason_code == "response_too_long"
    assert result.artifact is None
    assert len(compiled.calls) == 1


def test_public_collection_imports_no_private_source_owner(
    runtime_modules,
    monkeypatch,
) -> None:
    """Adding a private source import to CUM2 collection must fail this test."""

    runtime = runtime_modules.context_runtime
    scope = _scope()
    request = runtime.resolve_public_cum2_request(
        scope=scope,
        current_input="public isolation",
        request_id="public-isolation-request",
        correlation_id="public-isolation-correlation",
        wall_clock=lambda: NOW,
        monotonic_clock=lambda: NOW / 2,
    )
    session = runtime_modules.social.SocialSessionCache(clock=lambda: NOW)
    source = session.capture_continuity_source(request)
    forbidden = (
        "nana.memory",
        "nana.runtime.session_checkpoint",
        "nana.runtime.awareness_memory",
        "nana.runtime.live_awareness",
        "nana.runtime.mood_continuity",
        "nana.runtime.affect_lane",
        "nana.runtime.persona",
    )
    for name in forbidden:
        sys.modules.pop(name, None)
    imported = []
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name in forbidden:
            imported.append(name)
            raise AssertionError(f"private source import: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    compiled = runtime.build_public_cum2_plan(request, source)
    assert imported == []
    assert "NANA_PERSONALITY" not in compiled.messages[0].content
    assert "memory.public_grounding.v1" not in compiled.messages[0].content


def test_continuity_keeps_exact_scope_actor_association_without_mutation(
    runtime_modules,
) -> None:
    """Cross-room capture or display-label actor collapse must fail this test."""

    runtime = runtime_modules.context_runtime
    session = runtime_modules.social.SocialSessionCache(clock=lambda: NOW)
    repeated = "same label and same text"
    for event_id, author_id in (("prior-a", "actor-a"), ("prior-b", "actor-b")):
        scope = _scope(
            event_id,
            display_name="Shared Label",
            author_id=author_id,
        )
        session.record_public_turn(scope=scope, text=repeated)
    current_scope = _scope()
    request = runtime.resolve_public_cum2_request(
        scope=current_scope,
        current_input="current",
        request_id="association-request",
        correlation_id="association-correlation",
        wall_clock=lambda: NOW,
        monotonic_clock=lambda: NOW / 2,
    )
    partition = session._sessions[("youtube", "yt-live-chat-1", "yt-session-1")]
    before = tuple(turn.to_dict() for turn in partition._recent_turns)
    source = session.capture_continuity_source(request)
    compiled = runtime.build_public_cum2_plan(request, source)

    assert tuple(turn.to_dict() for turn in partition._recent_turns) == before
    assert compiled.messages[0].content.count(repeated) == 2
    assert "youtube:actor-a" not in compiled.messages[0].content
    assert "youtube:actor-b" not in compiled.messages[0].content
    assert dict(compiled.manifest.source_revisions)["public_session"] == source.revision

    other_scope = _scope(
        "other-event",
        room_id="other-room",
        session_id="other-session",
    )
    other_request = runtime.resolve_public_cum2_request(
        scope=other_scope,
        current_input="other",
        request_id="other-request",
        correlation_id="other-correlation",
        wall_clock=lambda: NOW,
        monotonic_clock=lambda: NOW / 2,
    )
    wrong_source = session.capture_continuity_source(other_request)
    with pytest.raises(runtime_modules.contracts.ContextContractError, match="invalid_source_snapshot"):
        runtime.build_public_cum2_plan(request, wrong_source)


def test_cum2_rejects_same_partition_snapshot_for_a_different_current_event(
    runtime_modules,
) -> None:
    """Removing exact current-event binding must accept A's snapshot for B."""

    runtime = runtime_modules.context_runtime
    session = runtime_modules.social.SocialSessionCache(clock=lambda: NOW)
    equal_text = "equal text from distinct events"
    scope_a = _scope("same-partition-event-a")
    scope_b = _scope("same-partition-event-b")

    def request_for(scope, suffix):
        return runtime.resolve_public_cum2_request(
            scope=scope,
            current_input=equal_text,
            request_id=f"same-partition-request-{suffix}",
            correlation_id=f"same-partition-correlation-{suffix}",
            wall_clock=lambda: NOW,
            monotonic_clock=lambda: NOW / 2,
        )

    request_a = request_for(scope_a, "a")
    request_b = request_for(scope_b, "b")
    session.record_public_turn(scope=scope_a, text=equal_text)
    snapshot_a = session.capture_continuity_source(request_a)
    frozen_snapshot_a = snapshot_a
    session.record_public_turn(scope=scope_b, text=equal_text)

    with pytest.raises(
        runtime_modules.contracts.ContextContractError,
        match="invalid_source_snapshot",
    ):
        runtime.build_public_cum2_plan(request_b, snapshot_a)
    assert snapshot_a == frozen_snapshot_a

    snapshot_b = session.capture_continuity_source(request_b)
    event_ids = [row["event_id"] for row in snapshot_b.payload["turns"]]
    assert event_ids == [scope_a.event_id]
    compiled_b = runtime.build_public_cum2_plan(request_b, snapshot_b)
    assert compiled_b.messages[0].content.count(equal_text) == 1
    assert compiled_b.messages[1].content == equal_text
    assert snapshot_b.payload["current_event_id"] == scope_b.event_id


def test_shadow_capture_failure_still_sends_unchanged_legacy_payload(
    runtime_modules,
    monkeypatch,
) -> None:
    """Making candidate capture mandatory for shadow must fail this test."""

    stream = importlib.import_module("nana.runtime.stream_cum2_response")
    session = _tracking_session(runtime_modules)

    def fail_capture(_request):
        session.capture_calls += 1
        raise RuntimeError("candidate unavailable")

    monkeypatch.setattr(session, "capture_continuity_source", fail_capture)
    caller = _LegacyCaller("Mình vẫn trả lời từ legacy.")
    generator = stream.PublicResponseGenerator(
        caller=caller,
        compiled_caller=_CompiledCaller(),
        session_context=session,
    )
    text = "Shadow capture may fail"
    result = _generate(
        runtime_modules,
        monkeypatch,
        generator,
        _turn(runtime_modules, text),
        text,
        mode="shadow",
    )

    assert result.status == "generated"
    assert result.artifact is not None
    assert result.artifact.text == "Mình vẫn trả lời từ legacy."
    assert len(caller.calls) == 1
    assert session.capture_calls == 1
    assert session.record_calls == 1
    assert session.format_calls == 1


def test_generation_request_identity_distinguishes_canonical_events(
    runtime_modules,
    monkeypatch,
) -> None:
    """Reusing output metadata across distinct events must not reuse request ID."""

    stream = importlib.import_module("nana.runtime.stream_cum2_response")
    compiled = _CompiledCaller()
    generator = stream.PublicResponseGenerator(
        caller=_LegacyCaller(),
        compiled_caller=compiled,
        session_context=_tracking_session(runtime_modules),
    )
    monkeypatch.setattr(
        runtime_modules.context_runtime,
        "require_cum2_canonical_dispatch_ready",
        lambda _compiled: None,
    )
    text = "same source text"
    for event_id in ("canonical-event-a", "canonical-event-b"):
        result = _generate(
            runtime_modules,
            monkeypatch,
            generator,
            _turn(runtime_modules, text, scope=_scope(event_id)),
            text,
            mode="canonical",
            output_id="reused-output-metadata",
        )
        assert result.status == "generated"

    request_ids = [call[0].manifest.request_id for call in compiled.calls]
    correlations = [call[0].manifest.correlation_id for call in compiled.calls]
    assert request_ids[0] != request_ids[1]
    assert correlations[0] != correlations[1]
