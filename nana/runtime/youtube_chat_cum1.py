"""STREAM V1 CUM 1 wiring from YouTube responses into CUM0 admission.

This module composes the existing transport-neutral YouTube normalizer with
the CUM0 contract. It stops at an admitted ``PublicTurn`` and owns no model,
memory, scheduler, delivery, voice, avatar, OBS, or public-output behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import math
import os
import time
from typing import Any, Mapping

from nana.runtime.public_context_boundary import PublicEventScope
from nana.runtime.public_identity import CanonicalPublicIdentity
from nana.runtime.stream_cum0_contract import (
    AdmissionResult,
    EnvelopeBuildStatus,
    ReadOnlyPolicySource,
    StreamContractLedger,
    build_stream_envelope,
    is_cum0_enabled,
)
from nana.runtime.youtube_chat_ingress import IngestResult, ViewerEvent, YouTubeChatIngress


PHASE = "STREAM-V1-CUM1-YOUTUBE-INGRESS"
YOUTUBE_ADAPTER_ID = "youtube-chat-cum1-v1"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def is_cum1_youtube_ingress_enabled() -> bool:
    return _env_flag("NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED", False)


@dataclass(frozen=True)
class YouTubeLiveSession:
    """Immutable binding created for one resolved YouTube live-chat session."""

    live_chat_id: str
    stream_session_id: str


@dataclass(frozen=True)
class YouTubeCum1Result:
    status: str
    reason_code: str
    ingest_result: IngestResult | None = None
    admission: AdmissionResult | None = None

    @property
    def public_turn(self):
        return self.admission.public_turn if self.admission is not None else None


@dataclass(frozen=True)
class _YouTubeEventProvenance:
    scope: PublicEventScope
    provider_event_type: str
    source: str = "youtube_live_chat"
    adapter_id: str = YOUTUBE_ADAPTER_ID
    provider: str = "youtube"

    def verify(
        self,
        *,
        scope: PublicEventScope,
        provider_event_type: str,
        provider_stream_state: str,
        offline_at: float | None,
    ) -> bool:
        return (
            scope == self.scope
            and provider_event_type == self.provider_event_type
            and provider_stream_state == "live"
            and offline_at is None
        )


def _field(value: Any, *names: str) -> Any:
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return None


def _exact_nonempty(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return value


def _local_admission(status: str, reason_code: str) -> AdmissionResult:
    return AdmissionResult(status, reason_code, None, None, False, None)


def _result_from_admission(
    admission: AdmissionResult,
    ingest_result: IngestResult,
) -> YouTubeCum1Result:
    return YouTubeCum1Result(
        status=admission.status,
        reason_code=admission.reason_code,
        ingest_result=ingest_result,
        admission=admission,
    )


class YouTubeChatCum1:
    """Receive one normalized response and admit at most one public turn."""

    def __init__(
        self,
        *,
        session: YouTubeLiveSession,
        policy_source: ReadOnlyPolicySource,
        ingress: YouTubeChatIngress | None = None,
        ledger: StreamContractLedger | None = None,
    ) -> None:
        self.session = session
        self.policy_source = policy_source
        self.ingress = ingress if ingress is not None else YouTubeChatIngress(actionable_limit=1)
        self.ledger = ledger if ledger is not None else StreamContractLedger()

    def ingest_response(
        self,
        response: Any,
        *,
        received_at: float | None = None,
        bootstrap: bool = False,
        now: float | None = None,
    ) -> YouTubeCum1Result:
        """Feed a REST/streamList response through normalization and CUM0."""

        if not is_cum1_youtube_ingress_enabled():
            return YouTubeCum1Result("disabled", "cum1_disabled")
        if not is_cum0_enabled():
            return YouTubeCum1Result("disabled", "cum0_disabled")
        if _field(response, "offlineAt", "offline_at"):
            return YouTubeCum1Result("held", "provider_offline")

        current = time.time() if now is None else float(now)
        ingested = self.ingress.ingest_response(
            response,
            received_at=received_at,
            live_chat_id=self.session.live_chat_id,
            bootstrap=bootstrap,
        )
        if bootstrap:
            return YouTubeCum1Result("idle", "bootstrap_context_only", ingested)

        event = self.ingress.pop_actionable(now=current)
        if event is None:
            return YouTubeCum1Result("idle", "no_actionable_event", ingested)
        return _result_from_admission(self._admit_event(event, now=current), ingested)

    def _admit_event(self, event: ViewerEvent, *, now: float) -> AdmissionResult:
        if not isinstance(event, ViewerEvent) or event.source != "youtube_live_chat":
            return _local_admission("rejected", "untrusted_adapter")
        if event.message_id_origin != "provider":
            return _local_admission("rejected", "provider_event_id_unverified")
        if event.event_type != "text_message_event" or event.actionable_candidate is not True:
            return _local_admission("rejected", "unsupported_event_type")

        event_id = _exact_nonempty(event.message_id)
        room_id = _exact_nonempty(event.live_chat_id)
        session_id = _exact_nonempty(self.session.stream_session_id)
        author_id = _exact_nonempty(event.author_channel_id)
        if event_id is None:
            return _local_admission("rejected", "missing_provider_event_id")
        if room_id is None:
            return _local_admission("rejected", "missing_room_id")
        if room_id != self.session.live_chat_id:
            return _local_admission("rejected", "session_mismatch")
        if session_id is None:
            return _local_admission("rejected", "active_session_missing")
        if author_id is None:
            return _local_admission("rejected", "missing_actor_id")
        if (
            event.published_at is None
            or isinstance(event.published_at, bool)
            or not isinstance(event.published_at, (int, float))
            or not math.isfinite(float(event.published_at))
        ):
            return _local_admission("rejected", "missing_published_at")

        identity = CanonicalPublicIdentity(
            platform="youtube",
            author_id=author_id,
            actor_key=f"youtube:{author_id}",
        )
        scope = PublicEventScope(
            platform="youtube",
            room_id=room_id,
            stream_session_id=session_id,
            event_id=event_id,
            display_name=event.viewer_name,
            identity=identity,
        )
        provenance = _YouTubeEventProvenance(scope, event.event_type)
        attempt_id = "yt-cum1-" + sha256(
            f"{room_id}\x1f{session_id}\x1f{event_id}".encode("utf-8")
        ).hexdigest()
        built = build_stream_envelope(
            scope,
            provenance,
            event.text,
            event.event_type,
            "live",
            None,
            attempt_id,
            0,
            event.received_at,
            float(event.published_at),
            now,
        )
        if built.status is not EnvelopeBuildStatus.VALIDATED or built.envelope is None:
            status = "held" if built.status is EnvelopeBuildStatus.HELD else "rejected"
            return _local_admission(status, built.reason_code)
        return self.ledger.authorize_and_accept(
            built.envelope,
            event.text,
            self.policy_source,
            session_id,
            now,
        )


__all__ = [
    "PHASE",
    "YOUTUBE_ADAPTER_ID",
    "YouTubeChatCum1",
    "YouTubeCum1Result",
    "YouTubeLiveSession",
    "is_cum1_youtube_ingress_enabled",
]
