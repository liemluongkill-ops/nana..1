"""Text-only YouTube publisher for STREAM V1 CUM 3.

The module consumes a CUM2 ``ResponseArtifact`` and may advance a correlated
``PublicDeliveryRecord`` from generated to published only after a confirmed
YouTube API response containing a provider message ID. It has no model, memory,
TTS, playback, avatar, OBS, subtitle, or other output integration.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
import threading
from typing import Any, Callable, Mapping

import requests

from nana.runtime.persona_boundary import validate_public_reply
from nana.runtime.public_context_boundary import PublicEventScope
from nana.runtime.public_delivery_state import PublicDeliveryRecord, transition_delivery
from nana.runtime.stream_cum0_contract import CUM0_MAX_ID_CHARS, compute_correlation_id
from nana.runtime.stream_cum2_response import (
    CUM2_DETERMINISTIC_ROUTE,
    CUM2_MODEL_ROUTE,
    ResponseArtifact,
)
from nana.runtime.youtube_chat_transport import YOUTUBE_API_ROOT


PHASE = "STREAM-V1-CUM3-YOUTUBE-PUBLISH"
CUM3_MAX_MESSAGE_CHARS = 200
CUM3_MAX_LEDGER_ENTRIES = 256


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def is_cum3_youtube_output_enabled() -> bool:
    return _env_flag("NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED", False)


def _default_token_provider() -> str | None:
    for name in ("NANA_YOUTUBE_OAUTH_TOKEN", "YOUTUBE_OAUTH_TOKEN"):
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    try:
        from nana.runtime.youtube_oauth import get_youtube_oauth_token

        return get_youtube_oauth_token()
    except Exception:
        return None


def _canonical_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    if len(value) > CUM0_MAX_ID_CHARS or "\x1f" in value:
        return None
    return value


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
    if any(_canonical_id(value) is None for value in values):
        return False
    return (
        identity.platform == scope.platform
        and identity.actor_key == f"{scope.platform}:{identity.author_id}"
    )


def _error_reason(payload: Any, fallback: str) -> str:
    if not isinstance(payload, Mapping):
        return fallback
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return fallback
    errors = error.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], Mapping):
        reason = _canonical_id(errors[0].get("reason"))
        if reason:
            return reason
    status = _canonical_id(error.get("status"))
    return status.lower() if status else fallback


@dataclass(frozen=True)
class YouTubeSendResult:
    outcome: str
    reason_code: str
    http_status: int | None = None
    provider_message_id: str | None = None
    retryable: bool = False


class YouTubeLiveChatSender:
    """One-shot OAuth sender; token refresh remains an injected concern."""

    def __init__(
        self,
        *,
        token_provider: Callable[[], str | None] | None = None,
        session: Any | None = None,
        timeout_seconds: float = 15.0,
        api_root: str = YOUTUBE_API_ROOT,
    ) -> None:
        self.token_provider = token_provider or _default_token_provider
        self.session = session or requests.Session()
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.api_root = str(api_root).rstrip("/")

    def send_text(self, live_chat_id: str, message_text: str) -> YouTubeSendResult:
        chat_id = _canonical_id(live_chat_id)
        text = str(message_text or "").strip()
        if chat_id is None:
            return YouTubeSendResult("failed", "invalid_live_chat_id")
        if not text:
            return YouTubeSendResult("failed", "empty_message")
        if len(text) > CUM3_MAX_MESSAGE_CHARS:
            return YouTubeSendResult("failed", "message_too_long")
        try:
            token = self.token_provider()
        except Exception:
            return YouTubeSendResult("failed", "oauth_provider_error")
        if not isinstance(token, str) or not token.strip():
            return YouTubeSendResult("failed", "missing_oauth")

        try:
            response = self.session.post(
                f"{self.api_root}/liveChat/messages",
                params={"part": "snippet"},
                headers={
                    "Authorization": f"Bearer {token.strip()}",
                    "Content-Type": "application/json",
                },
                json={
                    "snippet": {
                        "liveChatId": chat_id,
                        "type": "textMessageEvent",
                        "textMessageDetails": {"messageText": text},
                    }
                },
                timeout=self.timeout_seconds,
            )
        except requests.Timeout:
            return YouTubeSendResult("unknown", "transport_timeout", retryable=False)
        except requests.RequestException:
            return YouTubeSendResult("unknown", "transport_error", retryable=False)
        except Exception:
            return YouTubeSendResult("unknown", "client_error", retryable=False)

        try:
            status = int(response.status_code)
        except (TypeError, ValueError, AttributeError):
            return YouTubeSendResult("unknown", "invalid_http_status")
        try:
            payload = response.json()
        except Exception:
            payload = None

        if 200 <= status < 300:
            provider_id = _canonical_id(payload.get("id")) if isinstance(payload, Mapping) else None
            if provider_id is None:
                return YouTubeSendResult("unknown", "missing_provider_message_id", status)
            return YouTubeSendResult("published", "youtube_acknowledged", status, provider_id)
        if status >= 500:
            return YouTubeSendResult("unknown", "youtube_server_error", status, retryable=False)
        reason = _error_reason(payload, "youtube_rejected")
        return YouTubeSendResult(
            "failed",
            reason,
            status,
            retryable=status == 429,
        )


@dataclass(frozen=True)
class YouTubePublishResult:
    status: str
    reason_code: str
    delivery_record: PublicDeliveryRecord | None = None
    provider_message_id: str | None = None
    http_status: int | None = None
    correlation_id: str | None = None
    generated_record: PublicDeliveryRecord | None = None
    published_record: PublicDeliveryRecord | None = None

    @property
    def is_fresh_youtube_ack(self) -> bool:
        """True only for the publish call that observed YouTube's ACK."""

        generated = self.generated_record
        published = self.published_record
        return bool(
            self.status == "published"
            and self.reason_code == "youtube_acknowledged"
            and isinstance(self.provider_message_id, str)
            and bool(self.provider_message_id)
            and isinstance(self.correlation_id, str)
            and bool(self.correlation_id)
            and generated is not None
            and published is not None
            and generated.state == "generated"
            and published.state == "published"
            and generated.key == published.key
            and generated.revision < published.revision
            and generated.updated_at <= published.updated_at
            and self.delivery_record == published
        )


@dataclass(frozen=True)
class _CachedPublish:
    result: YouTubePublishResult
    outcome: str


class YouTubeCum3Publisher:
    """Bounded, process-local publisher with no automatic retry."""

    def __init__(
        self,
        *,
        sender: YouTubeLiveChatSender,
        max_entries: int = CUM3_MAX_LEDGER_ENTRIES,
        min_send_interval_seconds: float = 1.0,
    ) -> None:
        if type(max_entries) is not int or not 1 <= max_entries <= CUM3_MAX_LEDGER_ENTRIES:
            raise ValueError("invalid_ledger_limit")
        interval = float(min_send_interval_seconds)
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("invalid_send_interval")
        self.sender = sender
        self.max_entries = max_entries
        self.min_send_interval_seconds = interval
        self._entries: dict[tuple[str, ...], _CachedPublish] = {}
        self._outputs: dict[tuple[str, ...], tuple[str, ...]] = {}
        self._last_send_at: float | None = None
        self._lock = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "max_entries": self.max_entries,
                "published": sum(entry.outcome == "published" for entry in self._entries.values()),
                "unknown": sum(entry.outcome == "unknown" for entry in self._entries.values()),
                "durable": False,
                "automatic_retry": False,
            }

    def publish(
        self,
        artifact: ResponseArtifact,
        *,
        delivery_attempt_id: str,
        now: float,
    ) -> YouTubePublishResult:
        if not is_cum3_youtube_output_enabled():
            return YouTubePublishResult("disabled", "cum3_disabled")
        reason = self._validate_artifact(artifact, delivery_attempt_id, now)
        if reason:
            return YouTubePublishResult("rejected", reason)

        scope = artifact.scope
        attempt_id = delivery_attempt_id
        output_key = (
            scope.platform,
            scope.room_id,
            scope.stream_session_id,
            scope.event_id,
            artifact.output_id,
        )
        attempt_key = (*output_key, attempt_id)
        correlation_id = artifact.correlation_id

        with self._lock:
            existing_key = self._outputs.get(output_key)
            if existing_key is not None:
                cached = self._entries[existing_key]
                if cached.outcome == "published":
                    prior = cached.result
                    return YouTubePublishResult(
                        "duplicate",
                        "already_published",
                        prior.delivery_record,
                        prior.provider_message_id,
                        prior.http_status,
                        prior.correlation_id,
                        prior.generated_record,
                        prior.published_record,
                    )
                if cached.outcome == "unknown":
                    return cached.result
                return YouTubePublishResult(
                    "rejected",
                    "output_attempt_closed",
                    cached.result.delivery_record,
                    http_status=cached.result.http_status,
                    correlation_id=correlation_id,
                    generated_record=cached.result.generated_record,
                    published_record=cached.result.published_record,
                )
            if len(self._entries) >= self.max_entries:
                return YouTubePublishResult(
                    "rejected",
                    "idempotency_capacity_exhausted",
                    correlation_id=correlation_id,
                )
            if (
                self._last_send_at is not None
                and float(now) - self._last_send_at < self.min_send_interval_seconds
            ):
                return YouTubePublishResult(
                    "held",
                    "send_cooldown",
                    correlation_id=correlation_id,
                )

            generated = PublicDeliveryRecord(
                event_id=artifact.event_id,
                output_id=artifact.output_id,
                state="generated",
                attempt_id=attempt_id,
                revision=0,
                text_preview=artifact.text[:96],
                scope=scope,
                updated_at=float(now),
            )
            self._last_send_at = float(now)
            try:
                send_result = self.sender.send_text(scope.room_id, artifact.text)
            except Exception:
                send_result = YouTubeSendResult("unknown", "sender_error")

            if send_result.outcome == "published" and send_result.provider_message_id:
                published = transition_delivery(
                    generated,
                    "published",
                    1,
                    attempt_id,
                    self._receipt(generated, "published", 1, now),
                )
                result = YouTubePublishResult(
                    "published",
                    "youtube_acknowledged",
                    published,
                    send_result.provider_message_id,
                    send_result.http_status,
                    correlation_id,
                    generated,
                    published,
                )
                outcome = "published"
            elif send_result.outcome == "failed":
                interrupted = transition_delivery(
                    generated,
                    "interrupted",
                    1,
                    attempt_id,
                    self._receipt(generated, "interrupted", 1, now),
                )
                result = YouTubePublishResult(
                    "failed",
                    send_result.reason_code,
                    interrupted,
                    http_status=send_result.http_status,
                    correlation_id=correlation_id,
                    generated_record=generated,
                )
                outcome = "failed"
            else:
                result = YouTubePublishResult(
                    "held",
                    "unknown_outcome",
                    generated,
                    http_status=send_result.http_status,
                    correlation_id=correlation_id,
                    generated_record=generated,
                )
                outcome = "unknown"

            self._entries[attempt_key] = _CachedPublish(result, outcome)
            self._outputs[output_key] = attempt_key
            return result

    def reconcile_unknown(
        self,
        artifact: ResponseArtifact,
        *,
        delivery_attempt_id: str,
        now: float,
        provider_message_id: str | None = None,
        confirmed_absent: bool = False,
    ) -> YouTubePublishResult:
        """Resolve one ambiguous send from independent operator evidence."""

        reason = self._validate_artifact(artifact, delivery_attempt_id, now)
        if reason:
            return YouTubePublishResult("rejected", reason)
        output_key = (
            artifact.scope.platform,
            artifact.scope.room_id,
            artifact.scope.stream_session_id,
            artifact.scope.event_id,
            artifact.output_id,
        )
        with self._lock:
            attempt_key = self._outputs.get(output_key)
            cached = self._entries.get(attempt_key) if attempt_key else None
            if cached is None or cached.outcome != "unknown":
                return YouTubePublishResult("rejected", "unknown_attempt_not_found")
            record = cached.result.delivery_record
            if record is None:
                return YouTubePublishResult("rejected", "unknown_attempt_invalid")
            if provider_message_id is not None and confirmed_absent:
                return YouTubePublishResult("rejected", "ambiguous_reconciliation")
            if provider_message_id is not None:
                provider_id = _canonical_id(provider_message_id)
                if provider_id is None:
                    return YouTubePublishResult("rejected", "invalid_provider_message_id")
                advanced = transition_delivery(
                    record,
                    "published",
                    record.revision + 1,
                    record.attempt_id,
                    self._receipt(record, "published", record.revision + 1, now),
                )
                result = YouTubePublishResult(
                    "published",
                    "operator_confirmed_published",
                    advanced,
                    provider_id,
                    correlation_id=artifact.correlation_id,
                    generated_record=cached.result.generated_record or record,
                    published_record=advanced,
                )
                outcome = "published"
            elif confirmed_absent:
                advanced = transition_delivery(
                    record,
                    "interrupted",
                    record.revision + 1,
                    record.attempt_id,
                    self._receipt(record, "interrupted", record.revision + 1, now),
                )
                result = YouTubePublishResult(
                    "failed",
                    "operator_confirmed_absent",
                    advanced,
                    correlation_id=artifact.correlation_id,
                    generated_record=cached.result.generated_record or record,
                )
                outcome = "failed"
            else:
                return YouTubePublishResult(
                    "held",
                    "reconciliation_required",
                    record,
                    correlation_id=artifact.correlation_id,
                    generated_record=cached.result.generated_record or record,
                )
            self._entries[attempt_key] = _CachedPublish(result, outcome)
            return result

    @staticmethod
    def _receipt(
        record: PublicDeliveryRecord,
        state: str,
        revision: int,
        timestamp: float,
    ) -> dict[str, Any]:
        return {
            "event_id": record.event_id,
            "output_id": record.output_id,
            "attempt_id": record.attempt_id,
            "state": state,
            "revision": revision,
            "platform": record.scope.platform,
            "room_id": record.scope.room_id,
            "stream_session_id": record.scope.stream_session_id,
            "timestamp": float(timestamp),
        }

    @staticmethod
    def _validate_artifact(
        artifact: Any,
        delivery_attempt_id: Any,
        now: Any,
    ) -> str | None:
        if not isinstance(artifact, ResponseArtifact):
            return "invalid_response_artifact"
        route_matches_kind = (
            (artifact.response_kind == "full_reply" and artifact.model_route == CUM2_MODEL_ROUTE)
            or (
                artifact.response_kind == "ack_only"
                and artifact.model_route == CUM2_DETERMINISTIC_ROUTE
            )
        )
        if artifact.generation_state != "generated" or not route_matches_kind:
            return "invalid_response_artifact"
        if not _scope_is_canonical(artifact.scope) or artifact.scope.platform != "youtube":
            return "invalid_youtube_scope"
        if artifact.correlation_id != compute_correlation_id(artifact.scope):
            return "correlation_mismatch"
        if _canonical_id(artifact.output_id) is None:
            return "invalid_output_id"
        attempt_id = _canonical_id(delivery_attempt_id)
        if attempt_id is None:
            return "invalid_delivery_attempt"
        if artifact.source_attempt_id and attempt_id == artifact.source_attempt_id:
            return "delivery_attempt_reuses_ingress_attempt"
        if type(artifact.source_revision) is not int or artifact.source_revision < 0:
            return "invalid_source_revision"
        if (
            isinstance(artifact.generated_at, bool)
            or not isinstance(artifact.generated_at, (int, float))
            or not math.isfinite(float(artifact.generated_at))
            or isinstance(now, bool)
            or not isinstance(now, (int, float))
            or not math.isfinite(float(now))
            or float(now) < float(artifact.generated_at)
        ):
            return "invalid_publish_time"
        if not isinstance(artifact.text, str) or not artifact.text.strip():
            return "empty_message"
        if artifact.text != artifact.text.strip() or len(artifact.text) > CUM3_MAX_MESSAGE_CHARS:
            return "message_too_long" if len(artifact.text) > CUM3_MAX_MESSAGE_CHARS else "invalid_message"
        safe, _violations = validate_public_reply(artifact.text)
        if not safe:
            return "unsafe_public_text"
        return None


@dataclass(frozen=True)
class YouTubeCum3PipelineResult:
    status: str
    reason_code: str
    upstream: Any | None = None
    publish: YouTubePublishResult | None = None


class YouTubeCum3Pipeline:
    """Compose CUM2 generation with CUM3 text publishing only."""

    def __init__(self, *, response_pipeline: Any, publisher: YouTubeCum3Publisher) -> None:
        self.response_pipeline = response_pipeline
        self.publisher = publisher

    def ingest_generate_publish(
        self,
        response: Any,
        *,
        output_id: str,
        delivery_attempt_id: str,
        received_at: float | None = None,
        bootstrap: bool = False,
        now: float,
    ) -> YouTubeCum3PipelineResult:
        if not is_cum3_youtube_output_enabled():
            return YouTubeCum3PipelineResult("disabled", "cum3_disabled")
        upstream = self.response_pipeline.ingest_and_generate(
            response,
            output_id=output_id,
            received_at=received_at,
            bootstrap=bootstrap,
            now=now,
        )
        generation = getattr(upstream, "generation", None)
        artifact = getattr(generation, "artifact", None)
        if artifact is None:
            return YouTubeCum3PipelineResult(
                str(getattr(upstream, "status", "rejected")),
                str(getattr(upstream, "reason_code", "response_artifact_missing")),
                upstream,
                None,
            )
        published = self.publisher.publish(
            artifact,
            delivery_attempt_id=delivery_attempt_id,
            now=now,
        )
        return YouTubeCum3PipelineResult(
            published.status,
            published.reason_code,
            upstream,
            published,
        )


__all__ = [
    "CUM3_MAX_LEDGER_ENTRIES",
    "CUM3_MAX_MESSAGE_CHARS",
    "PHASE",
    "YouTubeCum3Publisher",
    "YouTubeCum3Pipeline",
    "YouTubeCum3PipelineResult",
    "YouTubeLiveChatSender",
    "YouTubePublishResult",
    "YouTubeSendResult",
    "is_cum3_youtube_output_enabled",
]
