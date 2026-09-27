"""Provider-free contract tests for STREAM V1 CUM 0.

These tests deliberately import only the bounded contract and existing value
objects. They never start Nana, a transport, a provider, a sink, or a service.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
import inspect
import os
from pathlib import Path
import re
import threading
import uuid
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from nana.runtime.public_context_boundary import PublicEventScope
from nana.runtime.public_identity import CanonicalPublicIdentity
from nana.runtime.stream_state import (
    AvatarEnergy,
    ErrorType,
    InteractionTone,
    StreamPolicy,
    StreamState,
    ViewerExpectation,
)
from nana.runtime.stream_cum0_contract import (
    CUM0_CORRELATION_PREFIX,
    CUM0_FINGERPRINT_VERSION,
    CUM0_LEDGER_TTL_SECONDS,
    CUM0_MAX_EVENT_AGE_SECONDS,
    CUM0_MAX_FUTURE_SKEW_SECONDS,
    CUM0_MAX_LEDGER_ENTRIES,
    CUM0_MAX_TEXT_PREVIEW_CHARS,
    EnvelopeBuildStatus,
    StreamContractLedger,
    StreamEnvelope,
    build_stream_envelope,
    compute_correlation_id,
    compute_content_fingerprint,
    normalize_sanitized_text,
    authorize_ingress,
)


NOW = 1_700_000_100.0
CREATED = 1_700_000_090.0


def make_scope(
    *,
    platform: str = "youtube",
    room_id: str = "live-chat-1",
    session_id: str = "session-20260914-a",
    event_id: str = "yt-event-001",
    actor_id: str = "channel-001",
    display_name: str = "Viewer",
) -> PublicEventScope:
    identity = CanonicalPublicIdentity(platform, actor_id, f"{platform}:{actor_id}")
    return PublicEventScope(platform, room_id, session_id, event_id, display_name, identity)


class FakeProvenance:
    source = "youtube_live_chat"
    adapter_id = "fake-youtube-adapter-v1"
    provider = "youtube"

    def __init__(self, *, allowed: bool = True, source: str | None = None, adapter_id: str | None = None,
                 provider: str | None = None) -> None:
        self.allowed = allowed
        if source is not None:
            self.source = source
        if adapter_id is not None:
            self.adapter_id = adapter_id
        if provider is not None:
            self.provider = provider
        self.calls: list[dict] = []

    def verify(self, *, scope, provider_event_type, provider_stream_state, offline_at) -> bool:
        self.calls.append({
            "scope": scope,
            "provider_event_type": provider_event_type,
            "provider_stream_state": provider_stream_state,
            "offline_at": offline_at,
        })
        return self.allowed


def make_policy(state: StreamState, *, can_reply: bool = True) -> StreamPolicy:
    return StreamPolicy(
        state=state,
        reason="cum0-test",
        can_proactive=False,
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=0,
        interaction_tone=InteractionTone.QUIET,
        avatar_energy=AvatarEnergy.LOW,
        viewer_expectation=ViewerExpectation.MUTED,
        error_type=ErrorType.NONE,
        can_reply=can_reply,
        can_speak=False,
        can_avatar=False,
    )


class PolicySource:
    def __init__(self, policy: StreamPolicy) -> None:
        self.policy = policy
        self.calls = 0

    def get_policy(self) -> StreamPolicy:
        self.calls += 1
        return self.policy


def build_valid(
    *,
    scope: PublicEventScope | None = None,
    text: str = "Xin chào Nana",
    provider_event_type: str = "text_message_event",
    provider_stream_state: str = "live",
    offline_at: float | None = None,
    ingress_attempt_id: str = "ingress-1",
    revision: int = 1,
    received_at: float = NOW - 1.0,
    created_at: float = CREATED,
    provenance: FakeProvenance | None = None,
):
    scope = scope or make_scope()
    provenance = provenance or FakeProvenance()
    return build_stream_envelope(
        scope,
        provenance,
        text,
        provider_event_type,
        provider_stream_state,
        offline_at,
        ingress_attempt_id,
        revision,
        received_at,
        created_at,
        NOW,
    )


def tamper_envelope(envelope, **changes):
    clone = replace(envelope)
    for private_name in ("_provenance", "_provenance_seal", "_provenance_binding"):
        if hasattr(envelope, private_name):
            object.__setattr__(clone, private_name, getattr(envelope, private_name))
    for name, value in changes.items():
        object.__setattr__(clone, name, value)
    return clone


@pytest.fixture(autouse=True)
def cum0_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NANA_STREAM_CUM0_ENABLED", "1")


def test_builder_signature_owns_fingerprint_and_has_no_public_turn() -> None:
    parameters = inspect.signature(build_stream_envelope).parameters
    assert "fingerprint" not in parameters
    result = build_valid()
    assert result.status == EnvelopeBuildStatus.VALIDATED
    assert not hasattr(result, "public_turn")
    assert result.envelope is not None
    assert re.fullmatch(r"[0-9a-f]{64}", result.envelope.fingerprint)


def test_builder_uses_scope_as_single_identity_source_and_projects_read_only_fields() -> None:
    scope = make_scope()
    provenance = FakeProvenance()
    result = build_valid(scope=scope, provenance=provenance)
    envelope = result.envelope
    assert envelope is not None
    assert envelope.scope is scope
    assert envelope.provider_event_id == scope.event_id
    assert envelope.actor_id == scope.identity.author_id
    assert envelope.actor_key == scope.identity.actor_key
    assert envelope.platform == scope.platform
    assert envelope.room_id == scope.room_id
    assert envelope.stream_session_id == scope.stream_session_id
    assert provenance.calls == [{
        "scope": scope,
        "provider_event_type": "text_message_event",
        "provider_stream_state": "live",
        "offline_at": None,
    }]


@pytest.mark.parametrize(
    "event_id",
    [
        "public", "legacy", "event", "derived-abc", "fallback-abc", "synthetic-abc",
        "generated-abc", "00000000-0000-0000-0000-000000000000", str(uuid.uuid4()),
        "{123e4567-e89b-12d3-a456-426614174000}",
        "urn:uuid:123e4567-e89b-12d3-a456-426614174000",
        "123e4567e89b12d3a456426614174000", "",
    ],
)
def test_builder_rejects_fallback_derived_uuid_or_missing_provider_event_id(event_id: str) -> None:
    result = build_valid(scope=make_scope(event_id=event_id))
    assert result.status == EnvelopeBuildStatus.REJECTED
    assert result.reason_code in {"missing_provider_event_id", "fallback_event_id"}


@pytest.mark.parametrize(
    "scope",
    [
        make_scope(room_id="public"),
        make_scope(session_id="legacy"),
        make_scope(actor_id="anonymous:yt-event-001"),
        PublicEventScope("public_chat", "room", "session", "event-1", "Viewer",
                         CanonicalPublicIdentity("public_chat", "actor", "public_chat:actor")),
    ],
)
def test_builder_rejects_compatibility_identity_fallbacks(scope: PublicEventScope) -> None:
    result = build_valid(scope=scope)
    assert result.status == EnvelopeBuildStatus.REJECTED
    assert result.reason_code in {
        "missing_room_id", "missing_session_id", "missing_actor_id", "fallback_event_id",
    }


def test_builder_rejects_mismatched_or_untrusted_provenance() -> None:
    result = build_valid(provenance=FakeProvenance(allowed=False))
    assert result.status == EnvelopeBuildStatus.REJECTED
    assert result.reason_code == "untrusted_adapter"
    mapping_like = {"source": "youtube", "adapter_id": "fake", "provider": "youtube"}
    result = build_valid(provenance=mapping_like)  # type: ignore[arg-type]
    assert result.status == EnvelopeBuildStatus.REJECTED
    assert result.reason_code == "untrusted_adapter"
    for provenance in (
        FakeProvenance(provider="discord"),
        FakeProvenance(source=" youtube_live_chat"),
        FakeProvenance(adapter_id="fake-youtube-adapter-v1 "),
    ):
        result = build_valid(provenance=provenance)
        assert result.status == EnvelopeBuildStatus.REJECTED
        assert result.reason_code == "untrusted_adapter"

    class ExactProtocolProvenance:
        source = "youtube_live_chat"
        adapter_id = "fake-youtube-adapter-v1"
        provider = "youtube"

        def verify(self, **_kwargs):
            return True

    result = build_valid(provenance=ExactProtocolProvenance())
    assert result.status == EnvelopeBuildStatus.VALIDATED
    assert result.envelope.event_id_origin == "provider"

    for invalid in (True, "youtube", object()):
        result = build_valid(provenance=invalid)
        assert result.status == EnvelopeBuildStatus.REJECTED
        assert result.reason_code == "untrusted_adapter"


def test_uuid_shape_is_allowed_outside_provider_message_event_id() -> None:
    stable_uuid = "123e4567-e89b-12d3-a456-426614174000"
    cases = (
        (make_scope(platform=stable_uuid), FakeProvenance(provider=stable_uuid)),
        (make_scope(room_id=stable_uuid), FakeProvenance()),
        (make_scope(session_id=stable_uuid), FakeProvenance()),
        (make_scope(actor_id=stable_uuid), FakeProvenance()),
    )
    for scope, provenance in cases:
        result = build_valid(scope=scope, provenance=provenance)
        assert result.status == EnvelopeBuildStatus.VALIDATED


@pytest.mark.parametrize("provider_event_type", ["TEXT_MESSAGE_EVENT", " text_message_event ", "text message event"])
def test_builder_rejects_noncanonical_provider_event_type(provider_event_type: str) -> None:
    result = build_valid(provider_event_type=provider_event_type)
    assert result.status == EnvelopeBuildStatus.REJECTED
    assert result.reason_code == "unsupported_event_type"


@pytest.mark.parametrize("provider_stream_state", ["LIVE", " live ", 1])
def test_builder_rejects_noncanonical_provider_stream_state(provider_stream_state) -> None:
    result = build_valid(provider_stream_state=provider_stream_state)
    assert result.status in {EnvelopeBuildStatus.REJECTED, EnvelopeBuildStatus.HELD}
    assert result.reason_code == "provider_offline"


def test_builder_rejects_noncanonical_ingress_attempt_id() -> None:
    result = build_valid(ingress_attempt_id=" ingress-1 ")
    assert result.status == EnvelopeBuildStatus.REJECTED
    assert result.reason_code == "invalid_attempt"


@pytest.mark.parametrize("text", ["", "   \t\n"])
def test_builder_rejects_empty_text_before_hashing(text: str) -> None:
    result = build_valid(text=text)
    assert result.status == EnvelopeBuildStatus.REJECTED
    assert result.reason_code == "invalid_text"


def test_builder_rejects_oversized_text_before_hashing() -> None:
    result = build_valid(text="x" * (CUM0_MAX_TEXT_PREVIEW_CHARS + 1))
    assert result.status == EnvelopeBuildStatus.REJECTED
    assert result.reason_code == "invalid_text"


def test_text_normalization_is_nfc_trim_and_ascii_space() -> None:
    assert normalize_sanitized_text("  Xin\tchào\n Nana  ") == "Xin chào Nana"
    assert normalize_sanitized_text("Xin chào Nana") == "Xin chào Nana"


def test_fingerprint_formula_is_exact_and_excludes_receipt_retry_transport_metadata() -> None:
    scope = make_scope()
    text = "Xin chào Nana"
    first = compute_content_fingerprint(scope, "text_message_event", text, CREATED)
    second = compute_content_fingerprint(scope, "text_message_event", "  Xin\nchào\tNana ", CREATED)
    assert first == second
    expected_material = "\x1f".join([
        CUM0_FINGERPRINT_VERSION,
        scope.platform,
        scope.room_id,
        scope.stream_session_id,
        scope.event_id,
        scope.identity.author_id,
        scope.identity.actor_key,
        "text_message_event",
        "Xin chào Nana",
        f"{CREATED:.6f}",
    ])
    assert first == hashlib.sha256(expected_material.encode("utf-8")).hexdigest()
    assert first != compute_content_fingerprint(scope, "text_message_event", "Khác", CREATED)

    base = build_valid(scope=scope).envelope
    transport_variant = build_valid(
        scope=scope,
        provenance=FakeProvenance(source="youtube_live_chat_stream", adapter_id="fake-youtube-stream-v2"),
        ingress_attempt_id="reconnect-attempt-2",
        revision=9,
        received_at=NOW,
    ).envelope
    assert transport_variant.fingerprint == base.fingerprint
    assert transport_variant.correlation_id == base.correlation_id


def test_correlation_uses_only_canonical_scope_identity() -> None:
    scope = make_scope()
    expected = CUM0_CORRELATION_PREFIX + hashlib.sha256(
        "\x1f".join((scope.platform, scope.room_id, scope.stream_session_id, scope.event_id)).encode("utf-8")
    ).hexdigest()
    assert compute_correlation_id(scope) == expected
    assert build_valid().envelope.correlation_id == expected


@pytest.mark.parametrize("provider_event_type", ["tombstone", "chat_ended_event", "user_banned_event", "invalid_type"])
def test_builder_holds_terminal_provider_events(provider_event_type: str) -> None:
    result = build_valid(provider_event_type=provider_event_type)
    assert result.status in {EnvelopeBuildStatus.HELD, EnvelopeBuildStatus.REJECTED}
    assert result.reason_code in {"terminal_provider_event", "provider_offline"}


def test_builder_holds_offline_state_or_offline_at() -> None:
    for kwargs in ({"provider_stream_state": "offline"}, {"offline_at": CREATED}):
        result = build_valid(**kwargs)
        assert result.status in {EnvelopeBuildStatus.HELD, EnvelopeBuildStatus.REJECTED}
        assert result.reason_code in {"provider_offline", "terminal_provider_event"}


def test_nonfinite_offline_at_is_rejected_before_offline_hold() -> None:
    result = build_valid(offline_at=float("nan"))
    assert result.status == EnvelopeBuildStatus.REJECTED
    assert result.reason_code == "nonfinite_timestamp"


def test_builder_rejects_noncanonical_scope_whitespace() -> None:
    result = build_valid(scope=make_scope(room_id=" room-1 "))
    assert result.status == EnvelopeBuildStatus.REJECTED
    assert result.reason_code == "event_identity_conflict"


@pytest.mark.parametrize(
    "scope",
    [
        make_scope(platform="you\x1ftube"),
        make_scope(room_id="room\x1fother"),
        make_scope(session_id="session\x1fother"),
        make_scope(event_id="event\x1fother"),
        make_scope(actor_id="actor\x1fother"),
    ],
)
def test_builder_rejects_hash_delimiter_in_canonical_identity(scope: PublicEventScope) -> None:
    result = build_valid(scope=scope)
    assert result.status == EnvelopeBuildStatus.REJECTED


def test_builder_rejects_hash_delimiter_in_actor_key() -> None:
    identity = CanonicalPublicIdentity("youtube", "actor", "youtube:actor\x1fother")
    scope = PublicEventScope("youtube", "room", "session", "event-1", "Viewer", identity)
    result = build_valid(scope=scope)
    assert result.status == EnvelopeBuildStatus.REJECTED


def test_manual_envelope_without_adapter_attestation_is_rejected() -> None:
    built = build_valid().envelope
    manual = StreamEnvelope(
        scope=built.scope,
        correlation_id=built.correlation_id,
        event_id_origin=built.event_id_origin,
        provider=built.provider,
        event_type=built.event_type,
        provider_event_type=built.provider_event_type,
        source=built.source,
        adapter_id=built.adapter_id,
        lifecycle=built.lifecycle,
        provider_stream_state=built.provider_stream_state,
        offline_at=built.offline_at,
        ingress_attempt_id=built.ingress_attempt_id,
        revision=built.revision,
        received_at=built.received_at,
        created_at=built.created_at,
        fingerprint=built.fingerprint,
    )
    decision = authorize_ingress(
        manual,
        PolicySource(make_policy(StreamState.LIVE_ACTIVE)),
        manual.stream_session_id,
        NOW,
    )
    assert decision.status == "denied"
    assert decision.reason_code == "untrusted_adapter"


def test_ledger_rejects_capacity_above_exact_bound() -> None:
    with pytest.raises(ValueError, match="invalid_ledger_limit"):
        StreamContractLedger(max_entries=CUM0_MAX_LEDGER_ENTRIES + 1)


def test_builder_returns_held_for_stale_or_future_timestamps() -> None:
    stale = build_valid(received_at=NOW - 31.0)
    future = build_valid(received_at=NOW + 6.0)
    assert stale.status == EnvelopeBuildStatus.HELD
    assert stale.reason_code == "stale_event"
    assert future.status == EnvelopeBuildStatus.HELD
    assert future.reason_code == "future_event"


def test_freshness_boundaries_are_inclusive() -> None:
    oldest = build_valid(received_at=NOW - CUM0_MAX_EVENT_AGE_SECONDS,
                         created_at=NOW - CUM0_MAX_EVENT_AGE_SECONDS)
    newest = build_valid(received_at=NOW + CUM0_MAX_FUTURE_SKEW_SECONDS,
                         created_at=NOW + CUM0_MAX_FUTURE_SKEW_SECONDS)
    assert oldest.status == EnvelopeBuildStatus.VALIDATED
    assert newest.status == EnvelopeBuildStatus.VALIDATED


def test_fingerprint_is_internal_and_raw_text_is_not_in_envelope_dict() -> None:
    result = build_valid(text="Secret-ish public text")
    envelope = result.envelope
    assert envelope is not None
    data = envelope.to_dict()
    assert "fingerprint" in data
    assert "Secret-ish public text" not in str(data)
    assert "sanitized_text" not in data
    assert getattr(envelope, "_provenance", None) is None


@pytest.mark.parametrize("state", list(StreamState))
def test_authorization_allows_only_live_states(monkeypatch: pytest.MonkeyPatch, state: StreamState) -> None:
    envelope = build_valid().envelope
    decision = authorize_ingress(envelope, PolicySource(make_policy(state)), "session-20260914-a", NOW)
    if state in {StreamState.LIVE_IDLE, StreamState.LIVE_ACTIVE}:
        assert decision.status == "allowed"
    else:
        assert decision.status in {"denied", "held"}
        assert decision.reason_code == "state_not_live"


def test_authorization_requires_matching_nonempty_active_session() -> None:
    envelope = build_valid().envelope
    source = PolicySource(make_policy(StreamState.LIVE_IDLE))
    assert authorize_ingress(envelope, source, None, NOW).reason_code == "active_session_missing"
    assert authorize_ingress(envelope, source, "other-session", NOW).reason_code == "session_mismatch"
    assert authorize_ingress(envelope, source, f" {envelope.stream_session_id} ", NOW).reason_code == "session_mismatch"


def test_authorization_rejects_forged_policy_sources_and_policy_errors() -> None:
    envelope = build_valid().envelope
    for source in ({"state": "live_active"}, object()):
        decision = authorize_ingress(envelope, source, "session-20260914-a", NOW)  # type: ignore[arg-type]
        assert decision.reason_code == "policy_source_invalid"

    class Broken:
        def get_policy(self):
            raise RuntimeError("no policy")

    assert authorize_ingress(envelope, Broken(), "session-20260914-a", NOW).reason_code == "policy_read_error"


def test_authorization_rejects_stale_future_nonfinite_and_offline_metadata() -> None:
    source = PolicySource(make_policy(StreamState.LIVE_ACTIVE))
    envelope = build_valid().envelope
    stale_tampered = tamper_envelope(envelope, received_at=NOW - 31)
    future_tampered = tamper_envelope(envelope, received_at=NOW + 6)
    assert authorize_ingress(stale_tampered, source, envelope.stream_session_id, NOW).reason_code == "stale_event"
    assert authorize_ingress(future_tampered, source, envelope.stream_session_id, NOW).reason_code == "future_event"
    tampered = tamper_envelope(envelope, received_at=float("nan"))
    assert authorize_ingress(tampered, source, envelope.stream_session_id, NOW).reason_code == "nonfinite_timestamp"
    offline_tampered = tamper_envelope(envelope, provider_stream_state="offline")
    assert authorize_ingress(offline_tampered, source, envelope.stream_session_id, NOW).reason_code == "provider_offline"
    offline_nan = tamper_envelope(envelope, offline_at=float("nan"))
    assert authorize_ingress(offline_nan, source, envelope.stream_session_id, NOW).reason_code == "nonfinite_timestamp"


def test_invalid_session_or_time_fails_before_policy_read() -> None:
    envelope = build_valid().envelope
    source = PolicySource(make_policy(StreamState.LIVE_ACTIVE))
    assert authorize_ingress(envelope, source, "wrong-session", NOW).reason_code == "session_mismatch"
    assert source.calls == 0

    stale = tamper_envelope(envelope, received_at=NOW - CUM0_MAX_EVENT_AGE_SECONDS - 0.001)
    assert authorize_ingress(stale, source, envelope.stream_session_id, NOW).reason_code == "stale_event"
    assert source.calls == 0


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("event_type", "image", "unsupported_event_type"),
        ("lifecycle", "published", "envelope_invalid"),
        ("provider", "other-provider", "untrusted_adapter"),
        ("source", "untrusted-source", "untrusted_adapter"),
        ("adapter_id", "untrusted-adapter", "untrusted_adapter"),
        ("revision", -1, "invalid_revision"),
        ("correlation_id", "sc0-forged", "invalid_correlation"),
        ("fingerprint", "0" * 64, "content_mismatch"),
    ],
)
def test_authorization_revalidates_tampered_envelope_fields(field: str, value, reason: str) -> None:
    envelope = build_valid().envelope
    tampered = tamper_envelope(envelope, **{field: value})
    decision = authorize_ingress(
        tampered,
        PolicySource(make_policy(StreamState.LIVE_ACTIVE)),
        envelope.stream_session_id,
        NOW,
    )
    assert decision.status == "denied"
    assert decision.reason_code == reason


@pytest.mark.parametrize(
    ("field", "reason"),
    [("ingress_attempt_id", "invalid_attempt"), ("revision", "invalid_revision"), ("received_at", "content_mismatch")],
)
def test_authorization_rejects_validly_shaped_but_rebound_control_metadata(field: str, reason: str) -> None:
    envelope = build_valid().envelope
    values = {
        "ingress_attempt_id": "different-attempt",
        "revision": envelope.revision + 1,
        "received_at": envelope.received_at + 1.0,
    }
    tampered = tamper_envelope(envelope, **{field: values[field]})
    decision = authorize_ingress(
        tampered,
        PolicySource(make_policy(StreamState.LIVE_ACTIVE)),
        envelope.stream_session_id,
        NOW,
    )
    assert decision.status == "denied"
    assert decision.reason_code == reason


def test_authorize_and_accept_malformed_envelope_returns_typed_rejection() -> None:
    ledger = StreamContractLedger()
    result = ledger.authorize_and_accept(
        object(),
        "text",
        PolicySource(make_policy(StreamState.LIVE_ACTIVE)),
        "session-20260914-a",
        NOW,
    )
    assert result.status == "rejected"
    assert result.reason_code == "envelope_invalid"


def test_authorize_and_accept_returns_turn_only_after_allowed_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    import nana.runtime.stream_cum0_contract as module

    ledger = StreamContractLedger()
    envelope = build_valid().envelope
    clock_calls = []

    def sentinel_clock():
        clock_calls.append(True)
        return 1234.5

    monkeypatch.setattr(module, "_MONOTONIC_CLOCK", sentinel_clock, raising=False)
    result = ledger.authorize_and_accept(
        envelope,
        "Xin chào Nana",
        PolicySource(make_policy(StreamState.LIVE_ACTIVE)),
        envelope.stream_session_id,
        NOW,
    )
    assert result.status == "accepted"
    assert result.public_turn is not None
    assert result.public_turn.scope is envelope.scope
    assert result.public_turn.timestamp == envelope.received_at
    assert result.public_turn.monotonic == 1234.5
    assert result.public_turn.delivery_state == "generated"
    assert result.public_turn.attempt_id == envelope.ingress_attempt_id
    assert clock_calls == [True]


def test_denied_admission_never_projects_public_turn() -> None:
    ledger = StreamContractLedger()
    envelope = build_valid().envelope
    result = ledger.authorize_and_accept(
        envelope,
        "Xin chào Nana",
        PolicySource(make_policy(StreamState.INTERMISSION)),
        envelope.stream_session_id,
        NOW,
    )
    assert result.status == "rejected"
    assert result.public_turn is None
    assert ledger.snapshot()["entries"] == 0


def test_monotonic_clock_runs_only_for_accepted_transactions(monkeypatch: pytest.MonkeyPatch) -> None:
    import nana.runtime.stream_cum0_contract as module

    calls = []

    def clock():
        calls.append(True)
        return 2222.0

    monkeypatch.setattr(module, "_MONOTONIC_CLOCK", clock, raising=False)
    ledger = StreamContractLedger()
    source = PolicySource(make_policy(StreamState.LIVE_ACTIVE))
    envelope = build_valid(text="same").envelope

    accepted = ledger.authorize_and_accept(
        envelope, "same", source, envelope.stream_session_id, NOW
    )
    duplicate = ledger.authorize_and_accept(
        envelope, "same", source, envelope.stream_session_id, NOW
    )
    rejected = ledger.authorize_and_accept(
        envelope, "changed", source, envelope.stream_session_id, NOW
    )
    held_envelope = build_valid(scope=make_scope(event_id="held-event")).envelope
    held = ledger.authorize_and_accept(
        held_envelope,
        "Xin chào Nana",
        source,
        held_envelope.stream_session_id,
        NOW + CUM0_MAX_EVENT_AGE_SECONDS + 0.001,
    )

    assert accepted.status == "accepted"
    assert accepted.public_turn.monotonic == 2222.0
    assert calls == [True]
    for result in (duplicate, rejected, held):
        assert result.status in {"duplicate", "rejected", "held"}
        assert result.public_turn is None


@pytest.mark.parametrize("clock_result", [float("nan"), float("inf"), -1.0, True, "1234.5"])
def test_invalid_monotonic_clock_fails_closed_without_ledger_mutation(
    monkeypatch: pytest.MonkeyPatch, clock_result
) -> None:
    import nana.runtime.stream_cum0_contract as module

    monkeypatch.setattr(module, "_MONOTONIC_CLOCK", lambda: clock_result, raising=False)
    ledger = StreamContractLedger()
    envelope = build_valid().envelope
    result = ledger.authorize_and_accept(
        envelope,
        "Xin chào Nana",
        PolicySource(make_policy(StreamState.LIVE_ACTIVE)),
        envelope.stream_session_id,
        NOW,
    )
    assert result.status == "rejected"
    assert result.reason_code == "monotonic_clock_invalid"
    assert result.public_turn is None
    assert ledger.snapshot()["entries"] == 0


def test_raising_monotonic_clock_fails_closed_without_ledger_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nana.runtime.stream_cum0_contract as module

    def broken_clock():
        raise RuntimeError("clock unavailable")

    monkeypatch.setattr(module, "_MONOTONIC_CLOCK", broken_clock, raising=False)
    ledger = StreamContractLedger()
    envelope = build_valid().envelope
    result = ledger.authorize_and_accept(
        envelope,
        "Xin chào Nana",
        PolicySource(make_policy(StreamState.LIVE_ACTIVE)),
        envelope.stream_session_id,
        NOW,
    )
    assert result.status == "rejected"
    assert result.reason_code == "monotonic_clock_invalid"
    assert result.public_turn is None
    assert ledger.snapshot()["entries"] == 0


def test_authorize_and_accept_recomputes_and_rejects_content_mismatch() -> None:
    ledger = StreamContractLedger()
    envelope = build_valid(text="original").envelope
    result = ledger.authorize_and_accept(
        envelope,
        "changed",
        PolicySource(make_policy(StreamState.LIVE_IDLE)),
        envelope.stream_session_id,
        NOW,
    )
    assert result.status == "rejected"
    assert result.reason_code == "content_mismatch"
    assert ledger.snapshot()["entries"] == 0


def test_projection_failure_rolls_back_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    import nana.runtime.stream_cum0_contract as module

    ledger = StreamContractLedger()
    envelope = build_valid().envelope

    def fail_projection(*_args, **_kwargs):
        raise RuntimeError("injected projection failure")

    monkeypatch.setattr(module, "_project_public_turn", fail_projection)
    result = ledger.authorize_and_accept(
        envelope,
        "Xin chào Nana",
        PolicySource(make_policy(StreamState.LIVE_ACTIVE)),
        envelope.stream_session_id,
        NOW,
    )
    assert result.status == "rejected"
    assert result.reason_code == "projection_failed"
    assert result.public_turn is None
    assert ledger.snapshot()["entries"] == 0


def test_projection_failure_restores_exact_previous_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    import nana.runtime.stream_cum0_contract as module

    ledger = StreamContractLedger()
    source = PolicySource(make_policy(StreamState.LIVE_ACTIVE))
    scope = make_scope()
    revision_one = build_valid(scope=scope, revision=1, ingress_attempt_id="attempt-1").envelope
    first = ledger.authorize_and_accept(
        revision_one, "Xin chào Nana", source, scope.stream_session_id, NOW
    )
    assert first.status == "accepted"
    previous_entry = ledger._entries[revision_one.key]
    original_projection = module._project_public_turn

    def fail_projection(*_args, **_kwargs):
        raise RuntimeError("injected revision-2 projection failure")

    monkeypatch.setattr(module, "_project_public_turn", fail_projection)
    revision_two = build_valid(scope=scope, revision=2, ingress_attempt_id="attempt-2").envelope
    failed = ledger.authorize_and_accept(
        revision_two, "Xin chào Nana", source, scope.stream_session_id, NOW
    )
    assert failed.status == "rejected"
    assert failed.reason_code == "projection_failed"
    assert failed.public_turn is None
    assert ledger._entries[revision_one.key] is previous_entry
    assert ledger.snapshot()["entries"] == 1

    monkeypatch.setattr(module, "_project_public_turn", original_projection)
    duplicate = ledger.authorize_and_accept(
        revision_one, "Xin chào Nana", source, scope.stream_session_id, NOW
    )
    assert duplicate.status == "duplicate"
    assert duplicate.public_turn is None
    assert ledger._entries[revision_one.key] is previous_entry


def test_projection_failure_is_atomic_against_identical_concurrent_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nana.runtime.stream_cum0_contract as module

    class TrackingLock:
        def __init__(self) -> None:
            self._inner = threading.RLock()
            self.second_attempted = threading.Event()

        def __enter__(self):
            if threading.current_thread().name == "cum0-second":
                self.second_attempted.set()
            self._inner.acquire()
            return self

        def __exit__(self, exc_type, exc, tb):
            self._inner.release()
            return False

    ledger = StreamContractLedger()
    tracking_lock = TrackingLock()
    ledger._lock = tracking_lock
    envelope = build_valid().envelope
    source = PolicySource(make_policy(StreamState.LIVE_ACTIVE))
    original_projection = module._project_public_turn
    projection_entered = threading.Event()
    release_projection = threading.Event()
    second_started = threading.Event()
    second_done = threading.Event()
    results = {}

    def controlled_projection(grant, sanitized_text, *args):
        if threading.current_thread().name == "cum0-first":
            projection_entered.set()
            if not release_projection.wait(5):
                raise RuntimeError("projection test timeout")
            raise RuntimeError("injected first projection failure")
        return original_projection(grant, sanitized_text, *args)

    monkeypatch.setattr(module, "_project_public_turn", controlled_projection)

    def submit(label: str) -> None:
        if label == "second":
            second_started.set()
        try:
            results[label] = ledger.authorize_and_accept(
                envelope,
                "Xin chào Nana",
                source,
                envelope.stream_session_id,
                NOW,
            )
        finally:
            if label == "second":
                second_done.set()

    first_thread = threading.Thread(target=submit, args=("first",), name="cum0-first", daemon=True)
    second_thread = threading.Thread(target=submit, args=("second",), name="cum0-second", daemon=True)
    first_thread.start()
    assert projection_entered.wait(5)
    second_thread.start()
    assert second_started.wait(5)
    assert tracking_lock.second_attempted.wait(5)
    finished_before_release = second_done.wait(0.2)
    release_projection.set()
    first_thread.join(5)
    second_thread.join(5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert finished_before_release is False
    assert results["first"].status == "rejected"
    assert results["first"].reason_code == "projection_failed"
    assert results["first"].public_turn is None
    assert results["second"].status == "accepted"
    assert results["second"].public_turn is not None
    assert ledger.snapshot()["entries"] == 1


def test_ledger_duplicate_is_idempotent_and_conflict_cannot_replace() -> None:
    ledger = StreamContractLedger()
    source = PolicySource(make_policy(StreamState.LIVE_IDLE))
    envelope = build_valid(text="same").envelope
    first = ledger.authorize_and_accept(envelope, "same", source, envelope.stream_session_id, NOW)
    duplicate = ledger.authorize_and_accept(envelope, "same", source, envelope.stream_session_id, NOW)
    assert first.status == "accepted"
    assert duplicate.status == "duplicate"
    assert duplicate.idempotent is True
    assert duplicate.public_turn is None
    assert ledger.snapshot()["entries"] == 1

    changed = build_valid(scope=make_scope(event_id=envelope.provider_event_id), text="different").envelope
    conflict = ledger.authorize_and_accept(changed, "different", source, changed.stream_session_id, NOW)
    assert conflict.status == "rejected"
    assert conflict.reason_code == "event_conflict"
    assert ledger.snapshot()["entries"] == 1


@pytest.mark.parametrize("change", ["actor", "type", "created"])
def test_same_provider_event_changed_fingerprint_inputs_conflict(change: str) -> None:
    ledger = StreamContractLedger()
    source = PolicySource(make_policy(StreamState.LIVE_ACTIVE))
    original = build_valid(text="same").envelope
    assert ledger.authorize_and_accept(
        original, "same", source, original.stream_session_id, NOW
    ).status == "accepted"

    kwargs = {"scope": make_scope(event_id=original.provider_event_id), "text": "same"}
    if change == "actor":
        kwargs["scope"] = make_scope(event_id=original.provider_event_id, actor_id="channel-002")
    elif change == "type":
        kwargs["provider_event_type"] = "text"
    else:
        kwargs["created_at"] = CREATED + 1.0
    changed = build_valid(**kwargs).envelope
    result = ledger.authorize_and_accept(
        changed, "same", source, changed.stream_session_id, NOW
    )
    assert result.status == "rejected"
    assert result.reason_code == "event_conflict"
    assert ledger.snapshot()["entries"] == 1


def test_ledger_rejects_stale_order_and_requires_new_attempt_for_retry() -> None:
    ledger = StreamContractLedger()
    source = PolicySource(make_policy(StreamState.LIVE_ACTIVE))
    scope = make_scope()
    first_env = build_valid(scope=scope, revision=2, ingress_attempt_id="attempt-a").envelope
    assert ledger.authorize_and_accept(first_env, "Xin chào Nana", source, scope.stream_session_id, NOW).status == "accepted"

    old_env = build_valid(scope=scope, revision=1, ingress_attempt_id="attempt-old").envelope
    stale = ledger.authorize_and_accept(old_env, "Xin chào Nana", source, scope.stream_session_id, NOW)
    assert stale.status == "rejected"
    assert stale.reason_code == "stale_revision"

    same_attempt_new_revision = build_valid(scope=scope, revision=3, ingress_attempt_id="attempt-a").envelope
    reused = ledger.authorize_and_accept(same_attempt_new_revision, "Xin chào Nana", source, scope.stream_session_id, NOW)
    assert reused.status == "rejected"
    assert reused.reason_code == "attempt_reuse"

    retry_env = build_valid(scope=scope, revision=3, ingress_attempt_id="attempt-b").envelope
    retry = ledger.authorize_and_accept(retry_env, "Xin chào Nana", source, scope.stream_session_id, NOW)
    assert retry.status == "accepted"


def test_grant_like_objects_cannot_bypass_private_acceptor() -> None:
    import copy
    import nana.runtime.stream_cum0_contract as module

    ledger = StreamContractLedger()
    envelope = build_valid().envelope
    fake = module._AuthorizedIngress(
        envelope=envelope,
        envelope_key=envelope.key,
        fingerprint=envelope.fingerprint,
        ingress_attempt_id=envelope.ingress_attempt_id,
        revision=envelope.revision,
        policy_state=StreamState.LIVE_ACTIVE,
        active_session_id=envelope.stream_session_id,
        issued_at=NOW,
        _seal=object(),
    )
    copied = copy.copy(fake)
    for candidate in (fake, copied, {"envelope": envelope}):
        result = ledger._accept_authorized(candidate, now=NOW, active_session_id=envelope.stream_session_id)
        assert result.status == "rejected"
        assert result.reason_code == "unauthorized"

    authorized = ledger.authorize_and_accept(
        envelope,
        "Xin chào Nana",
        PolicySource(make_policy(StreamState.LIVE_ACTIVE)),
        envelope.stream_session_id,
        NOW,
    )
    assert authorized.status == "accepted"
    assert authorized.public_turn is not None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("revision", 99),
        ("fingerprint", "0" * 64),
        ("envelope_key", ("forged",)),
        ("issued_at", NOW - 1.0),
        ("policy_state", StreamState.LIVE_IDLE),
    ],
)
def test_private_acceptor_rejects_mutated_authorized_grant_fields(
    monkeypatch: pytest.MonkeyPatch, field: str, value
) -> None:
    ledger = StreamContractLedger()
    envelope = build_valid().envelope
    source = PolicySource(make_policy(StreamState.LIVE_ACTIVE))
    original_accept = ledger._accept_authorized

    def mutate_then_accept(grant, *, now, active_session_id):
        object.__setattr__(grant, field, value)
        return original_accept(grant, now=now, active_session_id=active_session_id)

    monkeypatch.setattr(ledger, "_accept_authorized", mutate_then_accept)
    result = ledger.authorize_and_accept(
        envelope, "Xin chào Nana", source, envelope.stream_session_id, NOW
    )
    assert result.status == "rejected"
    assert result.reason_code == "unauthorized"
    assert result.public_turn is None
    assert ledger.snapshot()["entries"] == 0


def test_grant_registry_access_is_locked_and_cleans_up_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nana.runtime.stream_cum0_contract as module

    class TrackingRegistryLock:
        def __init__(self) -> None:
            self._inner = threading.RLock()
            self._local = threading.local()

        @property
        def owned(self) -> bool:
            return getattr(self._local, "depth", 0) > 0

        def __enter__(self):
            self._inner.acquire()
            self._local.depth = getattr(self._local, "depth", 0) + 1
            return self

        def __exit__(self, exc_type, exc, tb):
            self._local.depth -= 1
            self._inner.release()
            return False

    class GuardedRegistry(dict):
        def __init__(self, lock) -> None:
            super().__init__()
            self.lock = lock
            self.accesses = []

        def _check(self, operation: str) -> None:
            if not self.lock.owned:
                raise AssertionError(f"registry {operation} outside lock")
            self.accesses.append(operation)

        def get(self, key, default=None):
            self._check("get")
            return super().get(key, default)

        def __getitem__(self, key):
            self._check("getitem")
            return super().__getitem__(key)

        def __setitem__(self, key, value):
            self._check("setitem")
            return super().__setitem__(key, value)

        def pop(self, key, default=None):
            self._check("pop")
            return super().pop(key, default)

    tracking_lock = TrackingRegistryLock()
    registry = GuardedRegistry(tracking_lock)
    monkeypatch.setattr(module, "_GRANT_REGISTRY_LOCK", tracking_lock, raising=False)
    monkeypatch.setattr(module, "_GRANT_REGISTRY", registry)
    monkeypatch.setattr(module, "_MONOTONIC_CLOCK", lambda: 3333.0, raising=False)

    source = PolicySource(make_policy(StreamState.LIVE_ACTIVE))
    success_ledger = StreamContractLedger()
    success_envelope = build_valid(scope=make_scope(event_id="registry-success")).envelope
    success = success_ledger.authorize_and_accept(
        success_envelope,
        "Xin chào Nana",
        source,
        success_envelope.stream_session_id,
        NOW,
    )
    assert success.status == "accepted"
    assert registry == {}

    monkeypatch.setattr(
        module,
        "_project_public_turn",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("projection failure")),
    )
    failure_ledger = StreamContractLedger()
    failure_envelope = build_valid(scope=make_scope(event_id="registry-failure")).envelope
    failure = failure_ledger.authorize_and_accept(
        failure_envelope,
        "Xin chào Nana",
        source,
        failure_envelope.stream_session_id,
        NOW,
    )
    assert failure.status == "rejected"
    assert failure.reason_code == "projection_failed"
    assert registry == {}
    assert {"setitem", "get", "pop"} <= set(registry.accesses)


def test_ledger_capacity_and_ttl_are_bounded() -> None:
    assert CUM0_MAX_LEDGER_ENTRIES == 256
    assert CUM0_LEDGER_TTL_SECONDS == 900.0
    ledger = StreamContractLedger(max_entries=1, ttl_seconds=CUM0_LEDGER_TTL_SECONDS)
    source = PolicySource(make_policy(StreamState.LIVE_IDLE))
    first_scope = make_scope(event_id="one")
    first_env = build_valid(scope=first_scope).envelope
    assert ledger.authorize_and_accept(first_env, "Xin chào Nana", source, first_scope.stream_session_id, NOW).status == "accepted"
    duplicate = ledger.authorize_and_accept(first_env, "Xin chào Nana", source, first_scope.stream_session_id, NOW)
    assert duplicate.status == "duplicate"
    second_scope = make_scope(event_id="two")
    second_env = build_valid(scope=second_scope).envelope
    full = ledger.authorize_and_accept(second_env, "Xin chào Nana", source, second_scope.stream_session_id, NOW)
    assert full.reason_code == "ledger_full"
    ledger.purge(NOW + CUM0_LEDGER_TTL_SECONDS)
    assert ledger.snapshot()["entries"] == 1
    ledger.purge(NOW + CUM0_LEDGER_TTL_SECONDS + 0.001)
    assert ledger.snapshot()["entries"] == 0


def test_ledger_rejects_ttl_above_exact_bound() -> None:
    with pytest.raises(ValueError, match="invalid_ledger_ttl"):
        StreamContractLedger(ttl_seconds=CUM0_LEDGER_TTL_SECONDS + 0.001)


def test_session_text_overlap_does_not_make_actor_key_session_scoped() -> None:
    scope = make_scope(session_id="a", actor_id="abc")
    result = build_valid(scope=scope)
    assert result.status == EnvelopeBuildStatus.VALIDATED


def test_cum0_flag_is_default_off_and_disable_is_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NANA_STREAM_CUM0_ENABLED", raising=False)
    from nana import config
    import importlib
    importlib.reload(config)
    assert config.STREAM_CUM0_ENABLED is False

    from nana.runtime.stream_cum0_contract import StreamContractLedger
    envelope = build_valid().envelope
    monkeypatch.delenv("NANA_STREAM_CUM0_ENABLED", raising=False)
    result = StreamContractLedger().authorize_and_accept(
        envelope,
        "Xin chào Nana",
        PolicySource(make_policy(StreamState.LIVE_ACTIVE)),
        envelope.stream_session_id,
        NOW,
    )
    assert result.status == "rejected"
    assert result.reason_code == "cum0_disabled"


def test_response_artifact_is_deferred_from_cum0() -> None:
    import nana.runtime.stream_cum0_contract as module
    assert not hasattr(module, "ResponseArtifact")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
