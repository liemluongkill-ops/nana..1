"""Pure receipt controller for Stream V1 CUM5 local public playback.

The controller owns validation, idempotency, delivery-state reduction and an
injected session projection.  Provider and output work live behind an injected
port; importing this module creates no port and performs no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import math
import os
import threading
from typing import Any, Callable, Protocol, runtime_checkable

from nana.runtime.persona_boundary import validate_public_reply
from nana.runtime.public_context_boundary import PublicEventScope
from nana.runtime.public_delivery_state import PublicDeliveryRecord, transition_delivery
from nana.runtime.stream_cum0_contract import (
    CUM0_MAX_ID_CHARS,
    ReadOnlyPolicySource,
    compute_correlation_id,
)
from nana.runtime.stream_cum2_response import (
    CUM2_MAX_RESPONSE_CHARS,
    CUM2_MODEL_ROUTE,
    ResponseArtifact,
)
from nana.runtime.stream_cum3_youtube_publish import (
    CUM3_MAX_MESSAGE_CHARS,
    YouTubePublishResult,
)
from nana.runtime.stream_state import StreamPolicy, StreamState


PHASE = "STREAM-V1-CUM5-VOICE-PLAYBACK"
CUM5_MAX_LEDGER_ENTRIES = 256
CUM5_TARGET = "local_speaker"
_SEPARATOR = "\x1f"
_REQUIRED_GATES = (
    ("NANA_STREAM_CUM0_ENABLED", "cum0_disabled"),
    ("NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED", "cum1_disabled"),
    ("NANA_STREAM_CUM2_RESPONSE_ENABLED", "cum2_disabled"),
    ("NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED", "cum3_disabled"),
    ("NANA_STREAM_CUM4_HOST_ENABLED", "cum4_disabled"),
    ("NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED", "cum5_disabled"),
)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def is_cum5_voice_playback_enabled() -> bool:
    return _env_flag("NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED", False)


def _gate_reason(delivery_mode: str = "published_then_voice") -> str | None:
    for name, reason in _REQUIRED_GATES:
        if name == "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED":
            enabled = _env_flag(name, False)
            if delivery_mode == "voice_only":
                if enabled:
                    return "cum3_must_be_disabled"
            elif not enabled:
                return reason
            continue
        if not _env_flag(name, False):
            return reason
    return None


def _canonical_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if len(value) > CUM0_MAX_ID_CHARS or _SEPARATOR in value:
        return None
    return value


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) and numeric >= 0 else None


def _scope_is_canonical(scope: Any) -> bool:
    if not isinstance(scope, PublicEventScope):
        return False
    identity = scope.identity
    values = (
        scope.platform,
        scope.room_id,
        scope.stream_session_id,
        scope.event_id,
        scope.display_name,
        getattr(identity, "author_id", None),
        getattr(identity, "actor_key", None),
    )
    return bool(
        all(_canonical_id(value) is not None for value in values)
        and getattr(identity, "platform", None) == scope.platform
        and identity.actor_key == f"{scope.platform}:{identity.author_id}"
    )


@dataclass(frozen=True)
class PlaybackReadiness:
    ready: bool
    reason_code: str = "ready"


@dataclass(frozen=True)
class FirstAudioEvidence:
    playback_id: str
    content_sha256: str
    timestamp: float
    frame_bytes: int


@dataclass(frozen=True)
class AudioCompletionResult:
    state: str
    audio_completed: bool
    requested_segments: int
    fetched_segments: int
    played_segments: int
    unplayed_segments: int
    original_chars: int
    remaining_chars: int
    played_duration_ms: float
    abort_reason: str
    playback_id: str
    content_sha256: str
    completed_at: float
    stop_confirmed: bool


@dataclass(frozen=True)
class PlaybackStopResult:
    status: str
    reason_code: str

    @property
    def confirmed_stopped(self) -> bool:
        return self.status == "confirmed_stopped"


@dataclass(frozen=True)
class PublicVoicePlaybackRequest:
    artifact: ResponseArtifact
    generated_record: PublicDeliveryRecord
    published_record: PublicDeliveryRecord | None
    publish_status: str
    publish_reason: str
    provider_message_id: str = field(repr=False)
    correlation_id: str
    playback_id: str
    content_sha256: str
    target: str
    requested_at: float

    def __post_init__(self) -> None:
        if not isinstance(self.generated_record, PublicDeliveryRecord):
            raise ValueError("invalid_generated_record")
        if self.generated_record.delivery_mode == "voice_only":
            if (
                self.generated_record.state != "generated"
                or self.published_record is not None
                or self.publish_status != ""
                or self.publish_reason != ""
                or self.provider_message_id != ""
            ):
                raise ValueError("invalid_voice_publication_fields")
            return
        if (
            not isinstance(self.published_record, PublicDeliveryRecord)
            or self.published_record.delivery_mode != "published_then_voice"
            or self.published_record.state != "published"
            or self.published_record.key != self.generated_record.key
            or not isinstance(self.publish_status, str)
            or not self.publish_status
            or not isinstance(self.publish_reason, str)
            or not self.publish_reason
            or not isinstance(self.provider_message_id, str)
            or not self.provider_message_id
        ):
            raise ValueError("invalid_legacy_publication_fields")


@dataclass(frozen=True)
class CUM5PlaybackResult:
    status: str
    reason_code: str
    playback_id: str | None = None
    delivery_record: PublicDeliveryRecord | None = None
    start_evidence: FirstAudioEvidence | None = None
    terminal_evidence: AudioCompletionResult | None = None


@runtime_checkable
class PublicPlaybackPort(Protocol):
    def ready(self) -> PlaybackReadiness:
        ...

    def play(
        self,
        request: PublicVoicePlaybackRequest,
        *,
        should_continue: Callable[[], bool],
        on_first_audio: Callable[[FirstAudioEvidence], bool],
    ) -> AudioCompletionResult:
        ...

    def cancel(self, playback_id: str) -> PlaybackStopResult:
        ...


@runtime_checkable
class DeliveryRecorder(Protocol):
    def record_delivery(self, record: PublicDeliveryRecord) -> bool:
        ...


@dataclass
class _LedgerEntry:
    request: PublicVoicePlaybackRequest
    current: PublicDeliveryRecord
    result: CUM5PlaybackResult
    start_evidence: FirstAudioEvidence | None = None
    terminal_evidence: AudioCompletionResult | None = None
    terminal: bool = False
    violation: str | None = None


class PublicVoicePlaybackController:
    """Single-flight CUM5 controller with a bounded process-local ledger."""

    def __init__(
        self,
        *,
        playback_port_factory: Callable[[], PublicPlaybackPort],
        policy_source: ReadOnlyPolicySource,
        active_session_id: str | Callable[[], str],
        delivery_recorder: DeliveryRecorder,
        max_entries: int = CUM5_MAX_LEDGER_ENTRIES,
    ) -> None:
        if type(max_entries) is not int or not 1 <= max_entries <= CUM5_MAX_LEDGER_ENTRIES:
            raise ValueError("invalid_ledger_limit")
        if not callable(playback_port_factory):
            raise ValueError("invalid_port_factory")
        self._playback_port_factory = playback_port_factory
        self._policy_source = policy_source
        self._active_session_id = active_session_id
        self._delivery_recorder = delivery_recorder
        self.max_entries = max_entries
        self._entries: dict[tuple[str, ...], _LedgerEntry] = {}
        self._record_bindings: dict[
            tuple[str, ...],
            tuple[tuple[str, ...], str, str],
        ] = {}
        self._active_key: tuple[str, ...] | None = None
        self._halted = False
        self._lock = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "max_entries": self.max_entries,
                "active": self._active_key is not None,
                "halted": self._halted,
                "terminal": sum(entry.terminal for entry in self._entries.values()),
                "automatic_retry": False,
                "durable": False,
            }

    def play_published(
        self,
        artifact: ResponseArtifact,
        publish_result: YouTubePublishResult,
        *,
        now: float,
    ) -> CUM5PlaybackResult:
        gate_reason = _gate_reason("published_then_voice")
        if gate_reason:
            status = "disabled" if gate_reason == "cum5_disabled" else "rejected"
            return CUM5PlaybackResult(status, gate_reason)

        request, reason = self._build_published_request(artifact, publish_result, now)
        if request is None:
            return CUM5PlaybackResult("rejected", reason or "invalid_handoff")
        return self._execute_request(request)

    def play_generated(
        self,
        artifact: ResponseArtifact,
        *,
        attempt_id: str,
        now: float,
    ) -> CUM5PlaybackResult:
        gate_reason = _gate_reason("voice_only")
        if gate_reason:
            status = "disabled" if gate_reason == "cum5_disabled" else "rejected"
            return CUM5PlaybackResult(status, gate_reason)

        request, reason = self._build_generated_request(artifact, attempt_id, now)
        if request is None:
            return CUM5PlaybackResult("rejected", reason or "invalid_handoff")
        return self._execute_request(request)

    def _execute_request(
        self,
        request: PublicVoicePlaybackRequest,
    ) -> CUM5PlaybackResult:
        current_record = request.published_record or request.generated_record
        policy_reason = self._policy_reason(request.artifact.scope)
        if policy_reason:
            return CUM5PlaybackResult(
                "rejected",
                policy_reason,
                request.playback_id,
                current_record,
            )

        ledger_key = (
            *current_record.key,
            current_record.delivery_mode,
            request.content_sha256,
        )
        record_key = current_record.key
        with self._lock:
            if self._halted:
                return CUM5PlaybackResult(
                    "halted",
                    "controller_halted",
                    request.playback_id,
                    current_record,
                )
            bound = self._record_bindings.get(record_key)
            binding = (
                ledger_key,
                request.correlation_id,
                request.artifact.actor_key,
            )
            if bound is not None and bound != binding:
                self._halted = True
                return CUM5PlaybackResult(
                    "halted",
                    "delivery_conflict",
                    request.playback_id,
                    current_record,
                )
            existing = self._entries.get(ledger_key)
            if existing is not None:
                if existing.terminal:
                    return existing.result
                status = "playback_started" if existing.current.state == "playback_started" else "held"
                return CUM5PlaybackResult(
                    status,
                    "duplicate_in_progress",
                    request.playback_id,
                    existing.current,
                    existing.start_evidence,
                    existing.terminal_evidence,
                )
            if self._active_key is not None:
                return CUM5PlaybackResult(
                    "held",
                    "busy",
                    request.playback_id,
                    current_record,
                )
            if len(self._entries) >= self.max_entries:
                return CUM5PlaybackResult(
                    "rejected",
                    "ledger_full",
                    request.playback_id,
                    current_record,
                )
            entry = _LedgerEntry(
                request=request,
                current=current_record,
                result=CUM5PlaybackResult(
                    "held",
                    "reserved",
                    request.playback_id,
                    current_record,
                ),
            )
            self._entries[ledger_key] = entry
            self._record_bindings[record_key] = binding
            self._active_key = ledger_key

        try:
            if not self._project(request.generated_record):
                return self._halt_entry(ledger_key, entry, "session_projection_failed")
            if request.published_record is not None and not self._project(request.published_record):
                return self._halt_entry(ledger_key, entry, "session_projection_failed")

            dynamic_reason = (
                _gate_reason(request.generated_record.delivery_mode)
                or self._policy_reason(request.artifact.scope)
            )
            if dynamic_reason:
                return self._interrupt_entry(
                    ledger_key,
                    entry,
                    dynamic_reason,
                    request.requested_at,
                )
            try:
                port = self._playback_port_factory()
            except Exception:
                return self._interrupt_entry(
                    ledger_key,
                    entry,
                    "port_factory_error",
                    request.requested_at,
                )
            if not isinstance(port, PublicPlaybackPort):
                return self._interrupt_entry(
                    ledger_key,
                    entry,
                    "invalid_playback_port",
                    request.requested_at,
                )
            try:
                readiness = port.ready()
            except Exception:
                return self._interrupt_entry(
                    ledger_key,
                    entry,
                    "readiness_error",
                    request.requested_at,
                )
            if not isinstance(readiness, PlaybackReadiness):
                return self._interrupt_entry(
                    ledger_key,
                    entry,
                    "invalid_readiness",
                    request.requested_at,
                )
            if not readiness.ready:
                return self._interrupt_entry(
                    ledger_key,
                    entry,
                    readiness.reason_code or "not_ready",
                    request.requested_at,
                )

            def should_continue() -> bool:
                if _gate_reason(request.generated_record.delivery_mode) is not None:
                    return False
                if self._policy_reason(request.artifact.scope) is not None:
                    return False
                with self._lock:
                    return not self._halted and self._active_key == ledger_key and not entry.terminal

            def on_first_audio(evidence: FirstAudioEvidence) -> bool:
                if not should_continue():
                    return False
                return self._record_start(ledger_key, entry, evidence)

            if not should_continue():
                return self._interrupt_entry(
                    ledger_key,
                    entry,
                    "dispatch_cancelled",
                    request.requested_at,
                )
            try:
                completion = port.play(
                    request,
                    should_continue=should_continue,
                    on_first_audio=on_first_audio,
                )
            except Exception:
                return self._resolve_ambiguous_stop(
                    ledger_key,
                    entry,
                    port,
                    "playback_exception",
                )
            return self._resolve_completion(ledger_key, entry, port, completion)
        finally:
            with self._lock:
                if self._active_key == ledger_key:
                    self._active_key = None

    def _build_published_request(
        self,
        artifact: Any,
        publish_result: Any,
        now: Any,
    ) -> tuple[PublicVoicePlaybackRequest | None, str | None]:
        requested_at = _finite(now)
        if requested_at is None:
            return None, "invalid_request_time"
        if not isinstance(artifact, ResponseArtifact):
            return None, "invalid_response_artifact"
        if artifact.response_kind != "full_reply" or artifact.model_route != CUM2_MODEL_ROUTE:
            return None, "full_reply_required"
        if artifact.generation_state != "generated":
            return None, "invalid_generation_state"
        if not isinstance(publish_result, YouTubePublishResult):
            return None, "invalid_publish_result"
        if not publish_result.is_fresh_youtube_ack:
            return None, "publication_not_fresh"
        if artifact.scope.platform != "youtube":
            return None, "invalid_platform"
        try:
            expected_correlation = compute_correlation_id(artifact.scope)
        except ValueError:
            return None, "invalid_scope"
        if artifact.correlation_id != expected_correlation or publish_result.correlation_id != expected_correlation:
            return None, "correlation_mismatch"
        provider_message_id = _canonical_id(publish_result.provider_message_id)
        if provider_message_id is None:
            return None, "invalid_provider_message_id"
        generated = publish_result.generated_record
        published = publish_result.published_record
        if not isinstance(generated, PublicDeliveryRecord) or not isinstance(published, PublicDeliveryRecord):
            return None, "delivery_records_missing"
        if (
            generated.delivery_mode != "published_then_voice"
            or published.delivery_mode != "published_then_voice"
        ):
            return None, "delivery_mode_mismatch"
        if _canonical_id(artifact.output_id) is None:
            return None, "invalid_output_id"
        if _canonical_id(artifact.source_attempt_id) is None:
            return None, "invalid_source_attempt"
        if _canonical_id(published.attempt_id) is None:
            return None, "invalid_delivery_attempt"
        if published.attempt_id == artifact.source_attempt_id:
            return None, "delivery_attempt_reuses_ingress_attempt"
        expected_key = (
            artifact.scope.platform,
            artifact.scope.room_id,
            artifact.scope.stream_session_id,
            artifact.event_id,
            artifact.output_id,
            published.attempt_id,
        )
        if generated.key != expected_key or published.key != expected_key:
            return None, "delivery_identity_mismatch"
        if generated.scope != artifact.scope or published.scope != artifact.scope:
            return None, "scope_mismatch"
        if generated.state != "generated" or published.state != "published":
            return None, "invalid_delivery_state"
        if publish_result.delivery_record != published:
            return None, "published_record_mismatch"
        if generated.revision >= published.revision:
            return None, "delivery_revision_invalid"
        if generated.updated_at > published.updated_at or published.updated_at > requested_at:
            return None, "delivery_time_invalid"
        generated_at = _finite(artifact.generated_at)
        if generated_at is None or generated_at > generated.updated_at:
            return None, "generation_time_invalid"
        text = artifact.text
        if not isinstance(text, str) or not text or text != text.strip():
            return None, "invalid_text"
        if len(text) > CUM3_MAX_MESSAGE_CHARS:
            return None, "message_too_long"
        safe, _violations = validate_public_reply(text)
        if not safe:
            return None, "unsafe_public_text"
        preview = text[:96]
        if generated.text_preview != preview or published.text_preview != preview:
            return None, "text_preview_mismatch"
        digest = sha256(text.encode("utf-8")).hexdigest()
        playback_material = _SEPARATOR.join((*published.key, expected_correlation, digest))
        playback_id = "cum5-" + sha256(playback_material.encode("utf-8")).hexdigest()
        return PublicVoicePlaybackRequest(
            artifact=artifact,
            generated_record=generated,
            published_record=published,
            publish_status=publish_result.status,
            publish_reason=publish_result.reason_code,
            provider_message_id=provider_message_id,
            correlation_id=expected_correlation,
            playback_id=playback_id,
            content_sha256=digest,
            target=CUM5_TARGET,
            requested_at=requested_at,
        ), None

    def _build_generated_request(
        self,
        artifact: Any,
        attempt_id: Any,
        now: Any,
    ) -> tuple[PublicVoicePlaybackRequest | None, str | None]:
        requested_at = _finite(now)
        if requested_at is None:
            return None, "invalid_request_time"
        if not isinstance(artifact, ResponseArtifact):
            return None, "invalid_response_artifact"
        if artifact.response_kind != "full_reply" or artifact.model_route != CUM2_MODEL_ROUTE:
            return None, "full_reply_required"
        if artifact.generation_state != "generated":
            return None, "invalid_generation_state"
        if not _scope_is_canonical(artifact.scope):
            return None, "invalid_scope"
        if artifact.scope.platform != "youtube":
            return None, "invalid_platform"
        try:
            expected_correlation = compute_correlation_id(artifact.scope)
        except ValueError:
            return None, "invalid_scope"
        if artifact.correlation_id != expected_correlation:
            return None, "correlation_mismatch"
        if _canonical_id(artifact.output_id) is None:
            return None, "invalid_output_id"
        if _canonical_id(artifact.source_attempt_id) is None:
            return None, "invalid_source_attempt"
        delivery_attempt_id = _canonical_id(attempt_id)
        if delivery_attempt_id is None:
            return None, "invalid_delivery_attempt"
        if delivery_attempt_id == artifact.source_attempt_id:
            return None, "delivery_attempt_reuses_ingress_attempt"
        if type(artifact.source_revision) is not int or artifact.source_revision < 0:
            return None, "invalid_source_revision"
        generated_at = _finite(artifact.generated_at)
        if generated_at is None or generated_at > requested_at:
            return None, "generation_time_invalid"
        text = artifact.text
        if not isinstance(text, str) or not text or text != text.strip():
            return None, "invalid_text"
        if len(text) > CUM2_MAX_RESPONSE_CHARS:
            return None, "message_too_long"
        safe, _violations = validate_public_reply(text)
        if not safe:
            return None, "unsafe_public_text"

        generated = PublicDeliveryRecord(
            event_id=artifact.event_id,
            output_id=artifact.output_id,
            state="generated",
            attempt_id=delivery_attempt_id,
            revision=0,
            text_preview=text[:96],
            scope=artifact.scope,
            updated_at=requested_at,
            delivery_mode="voice_only",
        )
        digest = sha256(text.encode("utf-8")).hexdigest()
        playback_material = _SEPARATOR.join(
            (*generated.key, expected_correlation, digest)
        )
        playback_id = "cum5-" + sha256(playback_material.encode("utf-8")).hexdigest()
        return PublicVoicePlaybackRequest(
            artifact=artifact,
            generated_record=generated,
            published_record=None,
            publish_status="",
            publish_reason="",
            provider_message_id="",
            correlation_id=expected_correlation,
            playback_id=playback_id,
            content_sha256=digest,
            target=CUM5_TARGET,
            requested_at=requested_at,
        ), None

    def _active_session(self) -> str | None:
        try:
            value = self._active_session_id() if callable(self._active_session_id) else self._active_session_id
        except Exception:
            return None
        return _canonical_id(value)

    def _policy_reason(self, scope: Any) -> str | None:
        active_session = self._active_session()
        if active_session is None:
            return "active_session_missing"
        if active_session != scope.stream_session_id:
            return "session_mismatch"
        if not isinstance(self._policy_source, ReadOnlyPolicySource):
            return "policy_source_invalid"
        try:
            policy = self._policy_source.get_policy()
        except Exception:
            return "policy_read_error"
        if not isinstance(policy, StreamPolicy) or not isinstance(policy.state, StreamState):
            return "policy_source_invalid"
        if policy.state not in {StreamState.LIVE_IDLE, StreamState.LIVE_ACTIVE}:
            return "state_not_live"
        if policy.can_speak is not True:
            return "policy_speak_not_allowed"
        return None

    def _project(self, record: PublicDeliveryRecord) -> bool:
        if not isinstance(self._delivery_recorder, DeliveryRecorder):
            return False
        try:
            return self._delivery_recorder.record_delivery(record) is True
        except Exception:
            return False

    @staticmethod
    def _receipt(record: PublicDeliveryRecord, state: str, timestamp: float) -> dict[str, Any]:
        return {
            "event_id": record.event_id,
            "output_id": record.output_id,
            "attempt_id": record.attempt_id,
            "state": state,
            "revision": record.revision + 1,
            "platform": record.scope.platform,
            "room_id": record.scope.room_id,
            "stream_session_id": record.scope.stream_session_id,
            "timestamp": timestamp,
            "delivery_mode": record.delivery_mode,
        }

    def _record_start(
        self,
        ledger_key: tuple[str, ...],
        entry: _LedgerEntry,
        evidence: Any,
    ) -> bool:
        request = entry.request
        if not isinstance(evidence, FirstAudioEvidence):
            entry.violation = "invalid_start_evidence"
            return False
        timestamp = _finite(evidence.timestamp)
        if (
            evidence.playback_id != request.playback_id
            or evidence.content_sha256 != request.content_sha256
            or type(evidence.frame_bytes) is not int
            or evidence.frame_bytes <= 0
            or timestamp is None
        ):
            entry.violation = "start_evidence_mismatch"
            return False
        expected_state = (
            "generated"
            if request.generated_record.delivery_mode == "voice_only"
            else "published"
        )
        with self._lock:
            if entry.start_evidence is not None:
                return entry.start_evidence == evidence
            if self._active_key != ledger_key or entry.current.state != expected_state:
                entry.violation = "start_out_of_order"
                return False
            advanced = transition_delivery(
                entry.current,
                "playback_started",
                entry.current.revision + 1,
                entry.current.attempt_id,
                self._receipt(entry.current, "playback_started", timestamp),
            )
            if advanced.state != "playback_started" or advanced is entry.current:
                entry.violation = "start_transition_rejected"
                return False
            if not self._project(advanced):
                entry.violation = "session_projection_failed"
                return False
            entry.current = advanced
            entry.start_evidence = evidence
            entry.result = CUM5PlaybackResult(
                "playback_started",
                "first_audio_confirmed",
                request.playback_id,
                advanced,
                evidence,
            )
            return True

    def _resolve_completion(
        self,
        ledger_key: tuple[str, ...],
        entry: _LedgerEntry,
        port: PublicPlaybackPort,
        completion: Any,
    ) -> CUM5PlaybackResult:
        if entry.violation:
            return self._resolve_ambiguous_stop(ledger_key, entry, port, entry.violation)
        if not isinstance(completion, AudioCompletionResult):
            return self._resolve_ambiguous_stop(ledger_key, entry, port, "invalid_terminal_evidence")
        entry.terminal_evidence = completion
        request = entry.request
        completed_at = _finite(completion.completed_at)
        if (
            completion.playback_id != request.playback_id
            or completion.content_sha256 != request.content_sha256
            or completed_at is None
            or completed_at < entry.current.updated_at
        ):
            return self._resolve_ambiguous_stop(ledger_key, entry, port, "terminal_evidence_mismatch")
        counts = (
            completion.requested_segments,
            completion.fetched_segments,
            completion.played_segments,
            completion.unplayed_segments,
            completion.original_chars,
            completion.remaining_chars,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            return self._resolve_ambiguous_stop(ledger_key, entry, port, "invalid_terminal_counts")
        if _finite(completion.played_duration_ms) is None:
            return self._resolve_ambiguous_stop(ledger_key, entry, port, "invalid_terminal_duration")

        complete = (
            completion.state == "completed"
            and completion.audio_completed is True
            and completion.requested_segments == 1
            and completion.fetched_segments == 1
            and completion.played_segments == 1
            and completion.unplayed_segments == 0
            and completion.remaining_chars == 0
            and completion.original_chars == len(request.artifact.text)
            and completion.abort_reason == "none"
            and completion.stop_confirmed is True
        )
        if complete:
            if entry.start_evidence is None or entry.current.state != "playback_started":
                return self._halt_entry(
                    ledger_key,
                    entry,
                    "completion_without_start",
                    terminal_evidence=completion,
                )
            advanced = transition_delivery(
                entry.current,
                "delivered",
                entry.current.revision + 1,
                entry.current.attempt_id,
                self._receipt(entry.current, "delivered", completed_at),
            )
            if advanced.state != "delivered" or advanced is entry.current:
                return self._halt_entry(
                    ledger_key,
                    entry,
                    "delivery_transition_rejected",
                    terminal_evidence=completion,
                )
            if not self._project(advanced):
                return self._halt_entry(
                    ledger_key,
                    entry,
                    "session_projection_failed",
                    terminal_evidence=completion,
                )
            return self._finish_entry(
                ledger_key,
                entry,
                CUM5PlaybackResult(
                    "delivered",
                    "playback_completed",
                    request.playback_id,
                    advanced,
                    entry.start_evidence,
                    completion,
                ),
                advanced,
            )

        confirmed_failure = (
            completion.state in {"failed", "interrupted", "cancelled"}
            and completion.audio_completed is False
            and completion.stop_confirmed is True
            and completion.abort_reason != "none"
        )
        if confirmed_failure:
            if entry.start_evidence is None and completion.played_segments != 0:
                return self._halt_entry(
                    ledger_key,
                    entry,
                    "played_without_start_evidence",
                    terminal_evidence=completion,
                )
            return self._interrupt_entry(
                ledger_key,
                entry,
                completion.abort_reason,
                completed_at,
                terminal_evidence=completion,
            )
        return self._resolve_ambiguous_stop(
            ledger_key,
            entry,
            port,
            completion.abort_reason or "ambiguous_completion",
            terminal_evidence=completion,
        )

    def _resolve_ambiguous_stop(
        self,
        ledger_key: tuple[str, ...],
        entry: _LedgerEntry,
        port: PublicPlaybackPort,
        reason: str,
        *,
        terminal_evidence: AudioCompletionResult | None = None,
    ) -> CUM5PlaybackResult:
        try:
            stopped = port.cancel(entry.request.playback_id)
        except Exception:
            stopped = PlaybackStopResult("ambiguous", "cancel_error")
        if isinstance(stopped, PlaybackStopResult) and stopped.confirmed_stopped:
            timestamp = (
                _finite(terminal_evidence.completed_at)
                if terminal_evidence is not None
                else entry.current.updated_at
            )
            return self._interrupt_entry(
                ledger_key,
                entry,
                reason,
                timestamp if timestamp is not None else entry.current.updated_at,
                terminal_evidence=terminal_evidence,
            )
        halt_reason = stopped.reason_code if isinstance(stopped, PlaybackStopResult) else reason
        return self._halt_entry(
            ledger_key,
            entry,
            halt_reason or reason,
            terminal_evidence=terminal_evidence,
        )

    def _interrupt_entry(
        self,
        ledger_key: tuple[str, ...],
        entry: _LedgerEntry,
        reason: str,
        timestamp: float,
        *,
        terminal_evidence: AudioCompletionResult | None = None,
    ) -> CUM5PlaybackResult:
        current = entry.current
        advanced = transition_delivery(
            current,
            "interrupted",
            current.revision + 1,
            current.attempt_id,
            self._receipt(current, "interrupted", max(float(timestamp), current.updated_at)),
        )
        if advanced.state != "interrupted" or advanced is current:
            return self._halt_entry(
                ledger_key,
                entry,
                "interruption_transition_rejected",
                terminal_evidence=terminal_evidence,
            )
        if not self._project(advanced):
            return self._halt_entry(
                ledger_key,
                entry,
                "session_projection_failed",
                terminal_evidence=terminal_evidence,
            )
        return self._finish_entry(
            ledger_key,
            entry,
            CUM5PlaybackResult(
                "interrupted",
                reason,
                entry.request.playback_id,
                advanced,
                entry.start_evidence,
                terminal_evidence,
            ),
            advanced,
        )

    def _halt_entry(
        self,
        ledger_key: tuple[str, ...],
        entry: _LedgerEntry,
        reason: str,
        *,
        terminal_evidence: AudioCompletionResult | None = None,
    ) -> CUM5PlaybackResult:
        result = CUM5PlaybackResult(
            "halted",
            reason,
            entry.request.playback_id,
            entry.current,
            entry.start_evidence,
            terminal_evidence,
        )
        with self._lock:
            self._halted = True
        return self._finish_entry(ledger_key, entry, result, entry.current)

    def _finish_entry(
        self,
        ledger_key: tuple[str, ...],
        entry: _LedgerEntry,
        result: CUM5PlaybackResult,
        current: PublicDeliveryRecord,
    ) -> CUM5PlaybackResult:
        with self._lock:
            entry.current = current
            entry.result = result
            entry.terminal_evidence = result.terminal_evidence
            entry.terminal = True
            if self._active_key == ledger_key:
                self._active_key = None
        return result


__all__ = [
    "AudioCompletionResult",
    "CUM5PlaybackResult",
    "CUM5_MAX_LEDGER_ENTRIES",
    "DeliveryRecorder",
    "FirstAudioEvidence",
    "PHASE",
    "PlaybackReadiness",
    "PlaybackStopResult",
    "PublicPlaybackPort",
    "PublicVoicePlaybackController",
    "PublicVoicePlaybackRequest",
    "is_cum5_voice_playback_enabled",
]
