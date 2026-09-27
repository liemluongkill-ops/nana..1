"""Focused provider-free smoke for Stream V1 CUM5 playback control."""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import types
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
NANA_ROOT = ROOT / "nana"


def _namespace(name: str, directory: Path) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = [str(directory)]
    return module


sys.modules["nana"] = _namespace("nana", NANA_ROOT)
sys.modules["nana.runtime"] = _namespace("nana.runtime", NANA_ROOT / "runtime")
_SIDE_EFFECT_VIOLATIONS: list[str] = []


def _audit(event, args):
    if event == "open" and args and not isinstance(args[0], int):
        path = str(args[0]).replace("\\", "/").lower()
        mode = str(args[1]) if len(args) > 1 else ""
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
        forbidden_read = "/nana/data/" in path or path.endswith("/.env")
        forbidden_write = (
            any(marker in mode for marker in ("w", "a", "x", "+"))
            or bool(flags & write_flags)
        )
        if forbidden_read or forbidden_write:
            _SIDE_EFFECT_VIOLATIONS.append(f"{event}:{path}:{mode}")
            raise AssertionError(f"forbidden smoke file access: {path}")
    if event in {"socket.connect", "subprocess.Popen", "os.system", "os.spawn"}:
        _SIDE_EFFECT_VIOLATIONS.append(event)
        raise AssertionError(f"forbidden smoke side effect: {event}")


sys.addaudithook(_audit)


NOW = 1_700_001_000.0
FLAGS = {
    "NANA_STREAM_CUM0_ENABLED": "1",
    "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "1",
    "NANA_STREAM_CUM2_RESPONSE_ENABLED": "1",
    "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "1",
    "NANA_STREAM_CUM4_HOST_ENABLED": "1",
    "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED": "1",
}
VOICE_FLAGS = {**FLAGS, "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "0"}


def _scope(*, event_id: str = "yt-cum5-event-1", session_id: str = "yt-cum5-session"):
    from nana.runtime.public_context_boundary import PublicEventScope
    from nana.runtime.public_identity import CanonicalPublicIdentity

    identity = CanonicalPublicIdentity(
        platform="youtube",
        author_id="yt-cum5-viewer",
        actor_key="youtube:yt-cum5-viewer",
    )
    return PublicEventScope(
        platform="youtube",
        room_id="yt-cum5-room",
        stream_session_id=session_id,
        event_id=event_id,
        display_name="Minh",
        identity=identity,
    )


def _handoff(
    *,
    text: str = "Mình trả lời câu này nha.",
    response_kind: str = "full_reply",
    event_id: str = "yt-cum5-event-1",
    output_id: str = "yt-cum5-output-1",
    attempt_id: str = "yt-cum5-delivery-1",
    correlation_id: str | None = None,
    status: str = "published",
    reason_code: str = "youtube_acknowledged",
):
    from nana.runtime.public_delivery_state import PublicDeliveryRecord
    from nana.runtime.stream_cum0_contract import compute_correlation_id
    from nana.runtime.stream_cum2_response import (
        CUM2_DETERMINISTIC_ROUTE,
        CUM2_MODEL_ROUTE,
        ResponseArtifact,
    )
    from nana.runtime.stream_cum3_youtube_publish import YouTubePublishResult

    scope = _scope(event_id=event_id)
    correlation = correlation_id or compute_correlation_id(scope)
    artifact = ResponseArtifact(
        scope=scope,
        correlation_id=correlation,
        source_revision=0,
        source_attempt_id="yt-cum5-ingress-1",
        output_id=output_id,
        text=text,
        model_route=CUM2_MODEL_ROUTE if response_kind == "full_reply" else CUM2_DETERMINISTIC_ROUTE,
        generated_at=NOW - 2,
        response_kind=response_kind,
    )
    generated = PublicDeliveryRecord(
        event_id,
        output_id,
        "generated",
        attempt_id,
        0,
        text[:96],
        scope,
        NOW - 1,
    )
    published = PublicDeliveryRecord(
        event_id,
        output_id,
        "published",
        attempt_id,
        1,
        text[:96],
        scope,
        NOW,
    )
    publish = YouTubePublishResult(
        status=status,
        reason_code=reason_code,
        delivery_record=published,
        provider_message_id="yt-provider-message-1",
        http_status=200,
        correlation_id=correlation,
        generated_record=generated,
        published_record=published,
    )
    return artifact, publish


def _policy(*, can_speak: bool = True, state=None):
    from nana.runtime.stream_state import (
        AvatarEnergy,
        ErrorType,
        InteractionTone,
        StreamPolicy,
        StreamState,
        ViewerExpectation,
    )

    return StreamPolicy(
        state=state or StreamState.LIVE_ACTIVE,
        reason="cum5-smoke",
        can_proactive=False,
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=0,
        interaction_tone=InteractionTone.QUIET,
        avatar_energy=AvatarEnergy.LOW,
        viewer_expectation=ViewerExpectation.MUTED,
        error_type=ErrorType.NONE,
        can_reply=True,
        can_speak=can_speak,
        can_avatar=False,
    )


class _PolicySource:
    def __init__(self, policy=None):
        self.policy = policy or _policy()
        self.calls = 0

    def get_policy(self):
        self.calls += 1
        return self.policy


class _Recorder:
    def __init__(self):
        self.records = []

    def record_delivery(self, record):
        self.records.append(record)
        return True


class _Port:
    def __init__(self, mode: str = "completed"):
        self.mode = mode
        self.calls = []
        self.cancel_calls = []
        self.continuation_checks = []

    def ready(self):
        from nana.runtime.stream_cum5_voice_playback import PlaybackReadiness

        return PlaybackReadiness(True, "ready")

    def play(self, request, *, should_continue, on_first_audio):
        from nana.runtime.stream_cum5_voice_playback import (
            AudioCompletionResult,
            FirstAudioEvidence,
        )

        self.calls.append(request)
        self.continuation_checks.append(should_continue())
        if self.mode in {"dynamic_off", "dynamic_cum3_on"}:
            if self.mode == "dynamic_off":
                os.environ["NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED"] = "0"
            else:
                os.environ["NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED"] = "1"
            self.continuation_checks.append(should_continue())
            return AudioCompletionResult(
                state="ambiguous",
                audio_completed=False,
                requested_segments=1,
                fetched_segments=0,
                played_segments=0,
                unplayed_segments=1,
                original_chars=len(request.artifact.text),
                remaining_chars=len(request.artifact.text),
                played_duration_ms=0.0,
                abort_reason="gate_disabled",
                playback_id=request.playback_id,
                content_sha256=request.content_sha256,
                completed_at=request.requested_at + 0.1,
                stop_confirmed=False,
            )
        if self.mode in {"mismatched_start", "mismatched_digest"}:
            on_first_audio(
                FirstAudioEvidence(
                    (
                        "wrong-playback-id"
                        if self.mode == "mismatched_start"
                        else request.playback_id
                    ),
                    (
                        "0" * 64
                        if self.mode == "mismatched_digest"
                        else request.content_sha256
                    ),
                    request.requested_at + 0.1,
                    320,
                )
            )
            return AudioCompletionResult(
                "completed", True, 1, 1, 1, 0,
                len(request.artifact.text), 0, 400.0, "none",
                request.playback_id, request.content_sha256,
                request.requested_at + 0.2, True,
            )
        if self.mode not in {"pre_audio_failure", "completed_without_start"}:
            on_first_audio(
                FirstAudioEvidence(
                    request.playback_id,
                    request.content_sha256,
                    request.requested_at + 0.1,
                    320,
                )
            )
        if self.mode == "completed":
            return AudioCompletionResult(
                "completed", True, 1, 1, 1, 0,
                len(request.artifact.text), 0, 400.0, "none",
                request.playback_id, request.content_sha256,
                request.requested_at + 0.2, True,
            )
        if self.mode == "completed_without_start":
            return AudioCompletionResult(
                "completed", True, 1, 1, 1, 0,
                len(request.artifact.text), 0, 400.0, "none",
                request.playback_id, request.content_sha256,
                request.requested_at + 0.2, True,
            )
        if self.mode == "pre_audio_failure":
            return AudioCompletionResult(
                "failed", False, 1, 0, 0, 1,
                len(request.artifact.text), len(request.artifact.text), 0.0,
                "provider_error", request.playback_id, request.content_sha256,
                request.requested_at + 0.2, True,
            )
        if self.mode == "partial":
            return AudioCompletionResult(
                "interrupted", False, 1, 1, 0, 1,
                len(request.artifact.text), 4, 120.0, "playback_error",
                request.playback_id, request.content_sha256,
                request.requested_at + 0.2, True,
            )
        raise AssertionError(f"unknown mode: {self.mode}")

    def cancel(self, playback_id):
        from nana.runtime.stream_cum5_voice_playback import PlaybackStopResult

        self.cancel_calls.append(playback_id)
        return PlaybackStopResult("ambiguous", "stop_unconfirmed")


class _PortFactory:
    def __init__(self, port):
        self.port = port
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.port


def _controller(port=None, *, policy_source=None, recorder=None, active_session_id="yt-cum5-session", max_entries=256):
    from nana.runtime.stream_cum5_voice_playback import PublicVoicePlaybackController

    port = port or _Port()
    factory = _PortFactory(port)
    recorder = recorder or _Recorder()
    controller = PublicVoicePlaybackController(
        playback_port_factory=factory,
        policy_source=policy_source or _PolicySource(),
        active_session_id=active_session_id,
        delivery_recorder=recorder,
        max_entries=max_entries,
    )
    return controller, port, factory, recorder


def test_gate_off_is_side_effect_free() -> None:
    artifact, publish = _handoff()
    controller, port, factory, recorder = _controller()
    with patch.dict(os.environ, {**FLAGS, "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED": "0"}, clear=False):
        result = controller.play_published(artifact, publish, now=NOW + 1)
    assert result.status == "disabled" and result.reason_code == "cum5_disabled"
    assert factory.calls == 0 and port.calls == [] and recorder.records == []


def test_nonfresh_ack_only_and_identity_errors_never_touch_port() -> None:
    from nana.runtime.stream_cum0_contract import compute_correlation_id

    controller, port, factory, recorder = _controller()
    with patch.dict(os.environ, FLAGS, clear=False):
        artifact, publish = _handoff(status="duplicate", reason_code="already_published")
        duplicate = controller.play_published(artifact, publish, now=NOW + 1)
        ack, ack_publish = _handoff(response_kind="ack_only", output_id="yt-ack-output")
        ack_result = controller.play_published(ack, ack_publish, now=NOW + 1)
        bad, bad_publish = _handoff(output_id="yt-bad-output")
        bad_publish = replace(bad_publish, correlation_id=compute_correlation_id(_scope(event_id="other-event")))
        mismatch = controller.play_published(bad, bad_publish, now=NOW + 1)
        invalid_scope_artifact, invalid_scope_publish = _handoff(output_id="yt-invalid-scope-output")
        invalid_scope_artifact = replace(
            invalid_scope_artifact,
            scope=replace(invalid_scope_artifact.scope, room_id=""),
        )
        invalid_scope = controller.play_published(
            invalid_scope_artifact,
            invalid_scope_publish,
            now=NOW + 1,
        )
        reused, reused_publish = _handoff(
            output_id="yt-reused-attempt-output",
            attempt_id="yt-cum5-ingress-1",
        )
        reused_attempt = controller.play_published(reused, reused_publish, now=NOW + 1)
    assert duplicate.reason_code == "publication_not_fresh"
    assert ack_result.reason_code == "full_reply_required"
    assert mismatch.reason_code == "correlation_mismatch"
    assert invalid_scope.reason_code == "invalid_scope"
    assert reused_attempt.reason_code == "delivery_attempt_reuses_ingress_attempt"
    assert factory.calls == 0 and port.calls == [] and recorder.records == []


def test_policy_and_active_session_fail_closed_before_projection() -> None:
    from nana.runtime.stream_state import StreamState

    artifact, publish = _handoff()
    muted, port, factory, recorder = _controller(policy_source=_PolicySource(_policy(can_speak=False)))
    offline, _, offline_factory, offline_recorder = _controller(
        policy_source=_PolicySource(_policy(state=StreamState.OFFLINE))
    )
    wrong_session, _, session_factory, session_recorder = _controller(active_session_id="other-session")
    with patch.dict(os.environ, FLAGS, clear=False):
        muted_result = muted.play_published(artifact, publish, now=NOW + 1)
        offline_result = offline.play_published(artifact, publish, now=NOW + 1)
        session_result = wrong_session.play_published(artifact, publish, now=NOW + 1)
    assert muted_result.reason_code == "policy_speak_not_allowed"
    assert offline_result.reason_code == "state_not_live"
    assert session_result.reason_code == "session_mismatch"
    assert factory.calls == offline_factory.calls == session_factory.calls == 0
    assert recorder.records == offline_recorder.records == session_recorder.records == []


def test_fresh_full_reply_projects_exact_order_and_delivers_once() -> None:
    artifact, publish = _handoff()
    controller, port, factory, recorder = _controller()
    with patch.dict(os.environ, FLAGS, clear=False):
        result = controller.play_published(artifact, publish, now=NOW + 1)
        duplicate = controller.play_published(artifact, publish, now=NOW + 2)
    assert result.status == "delivered" and result.reason_code == "playback_completed"
    assert [record.state for record in recorder.records] == [
        "generated", "published", "playback_started", "delivered",
    ]
    assert [record.revision for record in recorder.records] == [0, 1, 2, 3]
    assert len(port.calls) == 1 and factory.calls == 1
    assert port.calls[0].artifact.text == artifact.text
    assert port.calls[0].content_sha256 == result.terminal_evidence.content_sha256
    assert "yt-provider-message-1" not in repr(port.calls[0])
    assert duplicate == result


def test_generated_voice_projects_exact_order_without_publication() -> None:
    artifact, _publish = _handoff()
    controller, port, factory, recorder = _controller()
    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = controller.play_generated(
            artifact,
            attempt_id="voice-attempt-1",
            now=NOW + 1,
        )
        duplicate = controller.play_generated(
            artifact,
            attempt_id="voice-attempt-1",
            now=NOW + 2,
        )
    assert result.status == "delivered" and result.reason_code == "playback_completed"
    assert [record.state for record in recorder.records] == [
        "generated", "playback_started", "delivered",
    ]
    assert [record.revision for record in recorder.records] == [0, 1, 2]
    assert all(record.delivery_mode == "voice_only" for record in recorder.records)
    assert len(port.calls) == 1 and factory.calls == 1
    request = port.calls[0]
    assert request.published_record is None
    assert request.publish_status == request.publish_reason == request.provider_message_id == ""
    assert request.artifact.text == artifact.text
    assert request.content_sha256 == result.terminal_evidence.content_sha256
    assert duplicate == result


def test_generated_voice_gate_contract_is_side_effect_free() -> None:
    artifact, _publish = _handoff()
    cases = (
        (
            {**VOICE_FLAGS, "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED": "0"},
            "disabled",
            "cum5_disabled",
        ),
        (FLAGS, "rejected", "cum3_must_be_disabled"),
        (
            {**VOICE_FLAGS, "NANA_STREAM_CUM2_RESPONSE_ENABLED": "0"},
            "rejected",
            "cum2_disabled",
        ),
    )
    for flags, expected_status, expected_reason in cases:
        controller, port, factory, recorder = _controller()
        with patch.dict(os.environ, flags, clear=False):
            result = controller.play_generated(
                artifact,
                attempt_id="voice-attempt-1",
                now=NOW + 1,
            )
        assert (result.status, result.reason_code) == (
            expected_status,
            expected_reason,
        )
        assert factory.calls == 0 and port.calls == [] and recorder.records == []


def test_generated_voice_rejects_invalid_scope_session_and_content() -> None:
    artifact, _publish = _handoff()
    controller, port, factory, recorder = _controller()
    ack_only, _ack_publish = _handoff(
        response_kind="ack_only",
        output_id="voice-ack-output",
    )
    checks = (
        (replace(artifact, scope=replace(artifact.scope, room_id="")), "voice-attempt-1", NOW + 1, "invalid_scope"),
        (replace(artifact, correlation_id="cum0:not-canonical"), "voice-attempt-1", NOW + 1, "correlation_mismatch"),
        (replace(artifact, text="Ba ơi, đây là nội dung riêng."), "voice-attempt-1", NOW + 1, "unsafe_public_text"),
        (ack_only, "voice-ack-attempt", NOW + 1, "full_reply_required"),
        (replace(artifact, generated_at=NOW + 2), "voice-attempt-1", NOW + 1, "generation_time_invalid"),
        (replace(artifact, source_revision=-1), "voice-attempt-1", NOW + 1, "invalid_source_revision"),
        (artifact, artifact.source_attempt_id, NOW + 1, "delivery_attempt_reuses_ingress_attempt"),
    )
    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        for candidate, attempt_id, requested_at, expected_reason in checks:
            result = controller.play_generated(
                candidate,
                attempt_id=attempt_id,
                now=requested_at,
            )
            assert result.status == "rejected" and result.reason_code == expected_reason

        wrong_session, wrong_port, wrong_factory, wrong_recorder = _controller(
            active_session_id="other-session"
        )
        session_result = wrong_session.play_generated(
            artifact,
            attempt_id="voice-attempt-1",
            now=NOW + 1,
        )
    assert session_result.status == "rejected" and session_result.reason_code == "session_mismatch"
    assert factory.calls == wrong_factory.calls == 0
    assert port.calls == wrong_port.calls == []
    assert recorder.records == wrong_recorder.records == []


def test_generated_voice_uses_cum2_length_cap_and_exact_text() -> None:
    artifact_201, _ = _handoff(
        text="a" * 201,
        event_id="voice-length-event-201",
        output_id="voice-length-output-201",
    )
    artifact_1200, _ = _handoff(
        text="b" * 1200,
        event_id="voice-length-event-1200",
        output_id="voice-length-output-1200",
    )
    artifact_1201, _ = _handoff(
        text="c" * 1201,
        event_id="voice-length-event-1201",
        output_id="voice-length-output-1201",
    )
    controller, port, factory, recorder = _controller()
    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        first = controller.play_generated(
            artifact_201,
            attempt_id="voice-length-attempt-201",
            now=NOW + 1,
        )
        second = controller.play_generated(
            artifact_1200,
            attempt_id="voice-length-attempt-1200",
            now=NOW + 2,
        )
        too_long = controller.play_generated(
            artifact_1201,
            attempt_id="voice-length-attempt-1201",
            now=NOW + 3,
        )
    assert first.status == second.status == "delivered"
    assert too_long.status == "rejected" and too_long.reason_code == "message_too_long"
    assert [request.artifact.text for request in port.calls] == ["a" * 201, "b" * 1200]
    assert factory.calls == 2
    assert [record.state for record in recorder.records] == [
        "generated", "playback_started", "delivered",
        "generated", "playback_started", "delivered",
    ]


def test_generated_voice_digest_conflict_halts_without_replay() -> None:
    artifact, _publish = _handoff()
    changed = replace(artifact, text="Mình trả lời một nội dung khác nha.")
    controller, port, _factory, _recorder = _controller()
    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        first = controller.play_generated(
            artifact,
            attempt_id="voice-attempt-1",
            now=NOW + 1,
        )
        conflict = controller.play_generated(
            changed,
            attempt_id="voice-attempt-1",
            now=NOW + 2,
        )
    assert first.status == "delivered"
    assert conflict.status == "halted" and conflict.reason_code == "delivery_conflict"
    assert len(port.calls) == 1


def test_delivery_key_cannot_switch_between_legacy_and_voice_modes() -> None:
    artifact, publish = _handoff(attempt_id="shared-delivery-attempt")
    controller, port, _factory, recorder = _controller()
    with patch.dict(os.environ, FLAGS, clear=False):
        legacy = controller.play_published(artifact, publish, now=NOW + 1)
    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        conflict = controller.play_generated(
            artifact,
            attempt_id="shared-delivery-attempt",
            now=NOW + 2,
        )
    assert legacy.status == "delivered"
    assert conflict.status == "halted" and conflict.reason_code == "delivery_conflict"
    assert len(port.calls) == 1
    assert [record.delivery_mode for record in recorder.records] == [
        "published_then_voice",
        "published_then_voice",
        "published_then_voice",
        "published_then_voice",
    ]


def test_delivery_key_cannot_switch_canonical_actor() -> None:
    from nana.runtime.stream_cum0_contract import compute_correlation_id

    artifact, _publish = _handoff()
    other_identity = replace(
        artifact.scope.identity,
        author_id="yt-other-viewer",
        actor_key="youtube:yt-other-viewer",
    )
    other_scope = replace(artifact.scope, identity=other_identity, display_name="Lan")
    other_artifact = replace(
        artifact,
        scope=other_scope,
        correlation_id=compute_correlation_id(other_scope),
    )
    controller, port, _factory, _recorder = _controller()
    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        first = controller.play_generated(
            artifact,
            attempt_id="voice-attempt-1",
            now=NOW + 1,
        )
        conflict = controller.play_generated(
            other_artifact,
            attempt_id="voice-attempt-1",
            now=NOW + 2,
        )
    assert first.status == "delivered"
    assert conflict.status == "halted" and conflict.reason_code == "delivery_conflict"
    assert len(port.calls) == 1


def test_confirmed_failures_interrupt_without_false_delivery() -> None:
    artifact, publish = _handoff()
    before, _, _, before_recorder = _controller(_Port("pre_audio_failure"))
    after, _, _, after_recorder = _controller(_Port("partial"))
    with patch.dict(os.environ, FLAGS, clear=False):
        before_result = before.play_published(artifact, publish, now=NOW + 1)
        after_result = after.play_published(artifact, publish, now=NOW + 1)
    assert before_result.status == "interrupted"
    assert [record.state for record in before_recorder.records] == ["generated", "published", "interrupted"]
    assert after_result.status == "interrupted"
    assert [record.state for record in after_recorder.records] == [
        "generated", "published", "playback_started", "interrupted",
    ]
    assert all(record.state != "delivered" for record in before_recorder.records + after_recorder.records)


def test_generated_voice_confirmed_failures_interrupt_without_false_delivery() -> None:
    artifact, _publish = _handoff()
    before, _, _, before_recorder = _controller(_Port("pre_audio_failure"))
    after, _, _, after_recorder = _controller(_Port("partial"))
    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        before_result = before.play_generated(
            artifact,
            attempt_id="voice-before-audio",
            now=NOW + 1,
        )
        after_result = after.play_generated(
            artifact,
            attempt_id="voice-after-audio",
            now=NOW + 1,
        )
    assert before_result.status == "interrupted"
    assert [record.state for record in before_recorder.records] == [
        "generated", "interrupted",
    ]
    assert after_result.status == "interrupted"
    assert [record.state for record in after_recorder.records] == [
        "generated", "playback_started", "interrupted",
    ]
    records = before_recorder.records + after_recorder.records
    assert all(record.delivery_mode == "voice_only" for record in records)
    assert all(record.state != "delivered" for record in records)


def test_generated_voice_uncertain_stop_and_bad_digest_halt_nonterminal() -> None:
    artifact, _publish = _handoff()
    bad_digest, digest_port, _, digest_recorder = _controller(
        _Port("mismatched_digest")
    )
    dynamic, dynamic_port, _, dynamic_recorder = _controller(
        _Port("dynamic_cum3_on")
    )
    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        digest_result = bad_digest.play_generated(
            artifact,
            attempt_id="voice-bad-digest",
            now=NOW + 1,
        )
    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        dynamic_result = dynamic.play_generated(
            artifact,
            attempt_id="voice-dynamic-gate",
            now=NOW + 1,
        )
    assert digest_result.status == "halted" and digest_result.reason_code == "stop_unconfirmed"
    assert [record.state for record in digest_recorder.records] == ["generated"]
    assert len(digest_port.calls) == len(digest_port.cancel_calls) == 1
    assert dynamic_result.status == "halted" and dynamic_result.reason_code == "stop_unconfirmed"
    assert dynamic_port.continuation_checks == [True, False]
    assert len(dynamic_port.calls) == len(dynamic_port.cancel_calls) == 1
    assert [record.state for record in dynamic_recorder.records] == ["generated"]


def test_completion_without_start_and_dynamic_off_halt_ambiguously() -> None:
    artifact, publish = _handoff()
    missing_start, _, _, missing_recorder = _controller(_Port("completed_without_start"))
    dynamic, dynamic_port, _, dynamic_recorder = _controller(_Port("dynamic_off"))
    with patch.dict(os.environ, FLAGS, clear=False):
        missing = missing_start.play_published(artifact, publish, now=NOW + 1)
        dynamic_result = dynamic.play_published(artifact, publish, now=NOW + 1)
    assert missing.status == "halted" and missing.reason_code == "completion_without_start"
    assert [record.state for record in missing_recorder.records] == ["generated", "published"]
    assert dynamic_result.status == "halted" and dynamic_result.reason_code == "stop_unconfirmed"
    assert dynamic_port.continuation_checks == [True, False]
    assert len(dynamic_port.cancel_calls) == 1
    assert [record.state for record in dynamic_recorder.records] == ["generated", "published"]


def test_mismatched_start_receipt_cannot_advance() -> None:
    artifact, publish = _handoff()
    controller, port, _, recorder = _controller(_Port("mismatched_start"))
    with patch.dict(os.environ, FLAGS, clear=False):
        result = controller.play_published(artifact, publish, now=NOW + 1)
    assert result.status == "halted" and result.reason_code == "stop_unconfirmed"
    assert [record.state for record in recorder.records] == ["generated", "published"]
    assert len(port.calls) == 1 and len(port.cancel_calls) == 1


class _BlockingPort(_Port):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def play(self, request, *, should_continue, on_first_audio):
        from nana.runtime.stream_cum5_voice_playback import AudioCompletionResult, FirstAudioEvidence

        self.calls.append(request)
        assert should_continue()
        assert on_first_audio(FirstAudioEvidence(
            request.playback_id, request.content_sha256, request.requested_at + 0.1, 320,
        ))
        self.started.set()
        assert self.release.wait(timeout=2.0)
        return AudioCompletionResult(
            "completed", True, 1, 1, 1, 0,
            len(request.artifact.text), 0, 400.0, "none",
            request.playback_id, request.content_sha256,
            request.requested_at + 0.2, True,
        )


def test_single_flight_holds_busy_and_duplicate_without_replay() -> None:
    first_artifact, first_publish = _handoff()
    second_artifact, second_publish = _handoff(
        event_id="yt-cum5-event-2",
        output_id="yt-cum5-output-2",
        attempt_id="yt-cum5-delivery-2",
    )
    port = _BlockingPort()
    controller, _, _, recorder = _controller(port)
    holder = {}

    def run_first():
        with patch.dict(os.environ, FLAGS, clear=False):
            holder["result"] = controller.play_published(first_artifact, first_publish, now=NOW + 1)

    with patch.dict(os.environ, FLAGS, clear=False):
        thread = threading.Thread(target=run_first)
        thread.start()
        assert port.started.wait(timeout=1.0)
        duplicate = controller.play_published(first_artifact, first_publish, now=NOW + 1.1)
        busy = controller.play_published(second_artifact, second_publish, now=NOW + 1.1)
        port.release.set()
        thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert duplicate.status == "playback_started" and duplicate.reason_code == "duplicate_in_progress"
    assert busy.status == "held" and busy.reason_code == "busy"
    assert len(port.calls) == 1
    assert holder["result"].status == "delivered"
    assert [record.state for record in recorder.records] == [
        "generated", "published", "playback_started", "delivered",
    ]


def test_digest_conflict_halts_lane_and_ledger_is_bounded() -> None:
    artifact, publish = _handoff()
    controller, port, _, _ = _controller(max_entries=1)
    with patch.dict(os.environ, FLAGS, clear=False):
        first = controller.play_published(artifact, publish, now=NOW + 1)
        conflict_artifact, conflict_publish = _handoff(text="Nội dung đã đổi nhưng dùng lại cùng attempt.")
        conflict = controller.play_published(conflict_artifact, conflict_publish, now=NOW + 2)
    assert first.status == "delivered"
    assert conflict.status == "halted" and conflict.reason_code == "delivery_conflict"
    assert len(port.calls) == 1

    bounded, bounded_port, _, _ = _controller(max_entries=1)
    other_artifact, other_publish = _handoff(
        event_id="yt-cum5-event-2",
        output_id="yt-cum5-output-2",
        attempt_id="yt-cum5-delivery-2",
    )
    with patch.dict(os.environ, FLAGS, clear=False):
        assert bounded.play_published(artifact, publish, now=NOW + 1).status == "delivered"
        full = bounded.play_published(other_artifact, other_publish, now=NOW + 2)
    assert full.status == "rejected" and full.reason_code == "ledger_full"
    assert len(bounded_port.calls) == 1


def test_session_owned_recorder_exposes_reply_only_after_delivered() -> None:
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.stream_cum2_response import PublicResponseGenerator

    artifact, publish = _handoff()
    cache = SocialSessionCache(clock=lambda: NOW + 1)
    cache.record_public_turn(
        scope=artifact.scope,
        text="Nana ơi, nghe thấy không?",
        revision=0,
        attempt_id=artifact.source_attempt_id,
    )

    class InspectingPort(_Port):
        def play(self, request, *, should_continue, on_first_audio):
            assert "Nana replied:" not in cache.format_public_room_context(artifact.scope)
            result = super().play(
                request,
                should_continue=should_continue,
                on_first_audio=on_first_audio,
            )
            assert "Nana replied:" not in cache.format_public_room_context(artifact.scope)
            return result

    generator = PublicResponseGenerator(caller=lambda **_: ("unused", "unused"), session_context=cache)
    controller, _, _, _ = _controller(
        InspectingPort(),
        recorder=generator.delivery_recorder,
    )
    with patch.dict(os.environ, FLAGS, clear=False):
        result = controller.play_published(artifact, publish, now=NOW + 1)
    context = cache.format_public_room_context(artifact.scope)
    assert result.status == "delivered"
    assert "Nana replied:" in context and artifact.text[:72] in context


def test_generated_voice_enters_session_context_only_after_delivered() -> None:
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.stream_cum2_response import PublicResponseGenerator

    artifact, _publish = _handoff()
    cache = SocialSessionCache(clock=lambda: NOW + 1)
    cache.record_public_turn(
        scope=artifact.scope,
        text="Nana ơi, nói thử câu này nhé?",
        revision=0,
        attempt_id=artifact.source_attempt_id,
    )

    class InspectingVoicePort(_Port):
        def play(self, request, *, should_continue, on_first_audio):
            assert "Nana replied:" not in cache.format_public_room_context(artifact.scope)
            result = super().play(
                request,
                should_continue=should_continue,
                on_first_audio=on_first_audio,
            )
            assert "Nana replied:" not in cache.format_public_room_context(artifact.scope)
            return result

    generator = PublicResponseGenerator(
        caller=lambda **_: ("unused", "unused"),
        session_context=cache,
    )
    controller, port, _, _ = _controller(
        InspectingVoicePort(),
        recorder=generator.delivery_recorder,
    )
    with patch.dict(os.environ, VOICE_FLAGS, clear=False):
        result = controller.play_generated(
            artifact,
            attempt_id="voice-session-attempt",
            now=NOW + 1,
        )
    context = cache.format_public_room_context(artifact.scope)
    assert result.status == "delivered"
    assert len(port.calls) == 1 and port.calls[0].published_record is None
    assert "Nana replied:" in context and artifact.text[:72] in context


def test_controller_imports_no_runtime_sinks() -> None:
    source = (ROOT / "nana" / "runtime" / "stream_cum5_voice_playback.py").read_text(encoding="utf-8")
    forbidden = (
        "nana.voice", "voice.engine", "voice.lipsync", "external_bridge",
        "avatar", "vts", "obs", "requests", "subprocess",
    )
    assert all(token not in source.lower() for token in forbidden)


def main() -> None:
    tests = (
        test_gate_off_is_side_effect_free,
        test_nonfresh_ack_only_and_identity_errors_never_touch_port,
        test_policy_and_active_session_fail_closed_before_projection,
        test_fresh_full_reply_projects_exact_order_and_delivers_once,
        test_generated_voice_projects_exact_order_without_publication,
        test_generated_voice_gate_contract_is_side_effect_free,
        test_generated_voice_rejects_invalid_scope_session_and_content,
        test_generated_voice_uses_cum2_length_cap_and_exact_text,
        test_generated_voice_digest_conflict_halts_without_replay,
        test_delivery_key_cannot_switch_between_legacy_and_voice_modes,
        test_delivery_key_cannot_switch_canonical_actor,
        test_confirmed_failures_interrupt_without_false_delivery,
        test_generated_voice_confirmed_failures_interrupt_without_false_delivery,
        test_generated_voice_uncertain_stop_and_bad_digest_halt_nonterminal,
        test_completion_without_start_and_dynamic_off_halt_ambiguously,
        test_mismatched_start_receipt_cannot_advance,
        test_single_flight_holds_busy_and_duplicate_without_replay,
        test_digest_conflict_halts_lane_and_ledger_is_bounded,
        test_session_owned_recorder_exposes_reply_only_after_delivered,
        test_generated_voice_enters_session_context_only_after_delivered,
        test_controller_imports_no_runtime_sinks,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    assert _SIDE_EFFECT_VIOLATIONS == [], _SIDE_EFFECT_VIOLATIONS
    print(f"smoke_stream_cum5_voice_playback: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
