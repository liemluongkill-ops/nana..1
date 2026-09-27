"""Bounded text-only host loop for Stream V1 CUM4."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from hashlib import sha256
import math
import os
import time
from typing import Any, Callable

from nana.runtime.social_session import (
    ACTION_ACK_ONLY,
    ACTION_FULL_REPLY,
    ACTION_SKIP,
    SocialSessionCache,
)
from nana.runtime.stream_cum2_response import PublicResponseGenerator, build_ack_response
from nana.runtime.stream_cum3_youtube_publish import (
    YouTubeCum3Publisher,
    YouTubePublishResult,
)
from nana.runtime.youtube_chat_cum1 import YouTubeChatCum1
from nana.runtime.youtube_chat_transport import YouTubeChatTransportError


PHASE = "STREAM-V1-CUM4-TEXT-HOST"
CUM4_MAX_QUEUE = 16
CUM4_MAX_OWN_MESSAGE_IDS = 256


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def is_cum4_host_enabled() -> bool:
    return _env_flag("NANA_STREAM_CUM4_HOST_ENABLED", False)


def _field(value: Any, *names: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        for name in names:
            if name in value:
                return value[name]
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


@dataclass(frozen=True)
class HostJob:
    turn: Any
    text: str
    action: str
    ack_text: str
    priority_score: int


@dataclass(frozen=True)
class HostResult:
    status: str
    reason_code: str
    queued: int = 0
    dropped: int = 0
    skipped: int = 0
    ignored_self: int = 0
    action: str = ""
    published: int = 0
    public_turn: Any | None = None
    response_artifact: Any | None = None
    publish_result: YouTubePublishResult | None = None


@dataclass(frozen=True)
class HostRunResult:
    status: str
    reason_code: str
    cycles: int
    reconnects: int


class YouTubeTextHost:
    """Select and queue public responses, with explicit generation/text steps."""

    def __init__(
        self,
        *,
        cum1: YouTubeChatCum1,
        generator: PublicResponseGenerator,
        publisher: YouTubeCum3Publisher | None = None,
        social_session: SocialSessionCache,
        max_queue: int = CUM4_MAX_QUEUE,
        min_publish_interval_seconds: float = 2.0,
    ) -> None:
        if type(max_queue) is not int or not 1 <= max_queue <= CUM4_MAX_QUEUE:
            raise ValueError("invalid_queue_limit")
        interval = float(min_publish_interval_seconds)
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("invalid_publish_interval")
        self.cum1 = cum1
        self.generator = generator
        self.publisher = publisher
        self.social_session = social_session
        self.max_queue = max_queue
        self.min_publish_interval_seconds = interval
        self._queue: deque[HostJob] = deque()
        self._last_publish_at: float | None = None
        self._halted = False
        self._halt_reason = ""
        self._unknown_artifact = None
        self._unknown_attempt_id = ""
        self._own_message_order: deque[str] = deque()
        self._own_message_ids: set[str] = set()
        self._stats = {"admitted": 0, "queued": 0, "dropped": 0, "skipped": 0,
                       "self_ignored": 0, "published": 0, "failed": 0, "unknown": 0}

    def snapshot(self) -> dict[str, Any]:
        return {
            "phase": PHASE,
            "enabled": is_cum4_host_enabled(),
            "queued": len(self._queue),
            "max_queue": self.max_queue,
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "stats": dict(self._stats),
            "tts": False,
            "avatar": False,
            "obs": False,
            "memory_write": False,
        }

    def ingest_response(
        self,
        response: Any,
        *,
        received_at: float,
        now: float,
        bootstrap: bool = False,
    ) -> HostResult:
        if not is_cum4_host_enabled():
            return HostResult("disabled", "cum4_disabled")
        if self._halted:
            return HostResult("halted", self._halt_reason, queued=len(self._queue))
        if _field(response, "offlineAt", "offline_at"):
            self._halted, self._halt_reason = True, "provider_offline"
            return HostResult("halted", self._halt_reason, queued=len(self._queue))
        items = _field(response, "items", default=()) or ()
        queued = dropped = skipped = ignored_self = 0
        for raw_item in items:
            raw_event_id = str(_field(raw_item, "id", "message_id", default="") or "").strip()
            if raw_event_id and raw_event_id in self._own_message_ids:
                ignored_self += 1
                self._stats["self_ignored"] += 1
                continue
            single = {
                "items": [raw_item],
                "nextPageToken": _field(response, "nextPageToken", "next_page_token"),
                "pollingIntervalMillis": _field(
                    response, "pollingIntervalMillis", "polling_interval_millis"
                ),
            }
            admitted = self.cum1.ingest_response(
                single,
                received_at=received_at,
                bootstrap=bootstrap,
                now=now,
            )
            turn = admitted.public_turn
            if turn is None:
                continue
            source_event = next(
                (event for event in admitted.ingest_result.new_events
                 if event.message_id == turn.scope.event_id),
                None,
            )
            if source_event is None:
                continue
            self._stats["admitted"] += 1
            decision = self.social_session.observe(
                scope=turn.scope,
                platform=turn.scope.platform,
                channel=turn.scope.room_id,
                viewer_name=turn.scope.display_name,
                text=source_event.text,
                event_type=source_event.event_type,
                now=now,
                monotonic_now=now,
            )
            if decision.action == ACTION_SKIP:
                skipped += 1
                self._stats["skipped"] += 1
                continue
            if decision.action not in {ACTION_ACK_ONLY, ACTION_FULL_REPLY}:
                skipped += 1
                self._stats["skipped"] += 1
                continue
            if len(self._queue) >= self.max_queue:
                dropped += 1
                self._stats["dropped"] += 1
                continue
            self._queue.append(HostJob(
                turn=turn,
                text=source_event.text,
                action=decision.action,
                ack_text=decision.reply_text,
                priority_score=decision.priority_score,
            ))
            queued += 1
            self._stats["queued"] += 1
        return HostResult(
            "queued", "response_ingested", queued, dropped, skipped, ignored_self
        )

    def generate_next(
        self,
        *,
        now: float,
        full_replies_only: bool = False,
        max_job_age_seconds: float | None = None,
    ) -> HostResult:
        job_age_limit = None
        if max_job_age_seconds is not None:
            if isinstance(max_job_age_seconds, bool):
                raise ValueError("invalid_job_age_limit")
            job_age_limit = float(max_job_age_seconds)
            if not math.isfinite(job_age_limit) or job_age_limit < 0:
                raise ValueError("invalid_job_age_limit")
        if not is_cum4_host_enabled():
            return HostResult("disabled", "cum4_disabled", queued=len(self._queue))
        if self._halted:
            return HostResult("halted", self._halt_reason, queued=len(self._queue))
        if not self._queue:
            return HostResult("idle", "queue_empty")
        job = self._queue.popleft()
        if job_age_limit is not None:
            source_timestamp = _field(job.turn, "timestamp")
            timestamp_valid = (
                not isinstance(source_timestamp, bool)
                and isinstance(source_timestamp, (int, float))
                and math.isfinite(float(source_timestamp))
            )
            current = float(now)
            if (
                not math.isfinite(current)
                or not timestamp_valid
                or current - float(source_timestamp) > job_age_limit
            ):
                self._stats["skipped"] += 1
                return HostResult(
                    "skipped",
                    "stale_queued_event",
                    queued=len(self._queue),
                    skipped=1,
                    action=job.action,
                    public_turn=job.turn,
                )
        digest = sha256(
            f"{job.turn.scope.session_key}\x1f{job.turn.scope.event_id}".encode("utf-8")
        ).hexdigest()[:24]
        output_id = f"cum4-output-{digest}"
        if full_replies_only and job.action == ACTION_ACK_ONLY:
            self._stats["skipped"] += 1
            return HostResult(
                "skipped",
                "ack_only_silent",
                queued=len(self._queue),
                skipped=1,
                action=job.action,
                public_turn=job.turn,
            )
        if job.action == ACTION_ACK_ONLY:
            generated = build_ack_response(
                job.turn,
                text=job.ack_text or "Mình thấy rồi nha.",
                output_id=output_id,
                now=now,
            )
        else:
            generated = self.generator.generate(
                job.turn,
                source_text=job.text,
                output_id=output_id,
                now=now,
            )
        if generated.artifact is None:
            self._stats["failed"] += 1
            return HostResult(
                "failed",
                generated.reason_code,
                queued=len(self._queue),
                action=job.action,
                public_turn=job.turn,
            )
        return HostResult(
            "generated",
            generated.reason_code,
            queued=len(self._queue),
            action=job.action,
            public_turn=job.turn,
            response_artifact=generated.artifact,
        )

    def process_next(self, *, now: float) -> HostResult:
        if not is_cum4_host_enabled():
            return HostResult("disabled", "cum4_disabled", queued=len(self._queue))
        if self._halted:
            return HostResult("halted", self._halt_reason, queued=len(self._queue))
        if not self._queue:
            return HostResult("idle", "queue_empty")
        publisher = self.publisher
        if publisher is None:
            return HostResult("rejected", "publisher_unavailable", queued=len(self._queue))
        if (
            self._last_publish_at is not None
            and now - self._last_publish_at < self.min_publish_interval_seconds
        ):
            return HostResult("held", "publish_cooldown", queued=len(self._queue))

        generated = self.generate_next(now=now)
        artifact = generated.response_artifact
        if generated.status != "generated" or artifact is None:
            return generated
        turn = generated.public_turn
        digest = sha256(
            f"{turn.scope.session_key}\x1f{turn.scope.event_id}".encode("utf-8")
        ).hexdigest()[:24]
        published = publisher.publish(
            artifact,
            delivery_attempt_id=f"cum4-send-{digest}",
            now=now,
        )
        if published.reason_code == "unknown_outcome":
            self._halted, self._halt_reason = True, "unknown_outcome"
            self._unknown_artifact = artifact
            self._unknown_attempt_id = f"cum4-send-{digest}"
            self._stats["unknown"] += 1
            return HostResult(
                "halted", "unknown_outcome", len(self._queue), action=generated.action,
                public_turn=turn, response_artifact=artifact,
                publish_result=published,
            )
        if published.status in {"published", "duplicate"}:
            if published.provider_message_id:
                self._remember_own_message_id(published.provider_message_id)
            self._last_publish_at = now
            self._stats["published"] += 1
            return HostResult(
                "published", published.reason_code, len(self._queue),
                action=generated.action, published=1, public_turn=turn,
                response_artifact=artifact,
                publish_result=published,
            )
        self._stats["failed"] += 1
        return HostResult(
            "failed",
            published.reason_code,
            len(self._queue),
            action=generated.action,
            public_turn=turn,
            response_artifact=artifact,
            publish_result=published,
        )

    def _remember_own_message_id(self, provider_message_id: str) -> None:
        if provider_message_id in self._own_message_ids:
            return
        if len(self._own_message_order) >= CUM4_MAX_OWN_MESSAGE_IDS:
            expired = self._own_message_order.popleft()
            self._own_message_ids.discard(expired)
        self._own_message_order.append(provider_message_id)
        self._own_message_ids.add(provider_message_id)

    def reconcile_unknown(
        self,
        *,
        now: float,
        provider_message_id: str | None = None,
        confirmed_absent: bool = False,
    ) -> HostResult:
        """Resume only after explicit evidence resolves the ambiguous send."""

        if not self._halted or self._halt_reason != "unknown_outcome":
            return HostResult("rejected", "unknown_outcome_not_pending", len(self._queue))
        publisher = self.publisher
        if publisher is None:
            return HostResult("rejected", "publisher_unavailable", len(self._queue))
        result = publisher.reconcile_unknown(
            self._unknown_artifact,
            delivery_attempt_id=self._unknown_attempt_id,
            now=now,
            provider_message_id=provider_message_id,
            confirmed_absent=confirmed_absent,
        )
        if result.reason_code not in {
            "operator_confirmed_published",
            "operator_confirmed_absent",
        }:
            return HostResult(
                "halted",
                result.reason_code,
                len(self._queue),
                publish_result=result,
            )
        self._halted = False
        self._halt_reason = ""
        self._unknown_artifact = None
        self._unknown_attempt_id = ""
        return HostResult(
            "resumed",
            result.reason_code,
            len(self._queue),
            publish_result=result,
        )

    def handle_response(self, response: Any, *, received_at: float, now: float) -> HostResult:
        ingested = self.ingest_response(response, received_at=received_at, now=now)
        if ingested.status in {"disabled", "halted"}:
            return ingested
        return self.process_next(now=now)


def run_text_host(
    *,
    transport: Any,
    live_chat_id: str,
    host: YouTubeTextHost,
    max_cycles: int = 0,
    reconnect_limit: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> HostRunResult:
    """Poll a receive transport until killed, halted, offline, or bounded completion."""

    page_token = None
    bootstrap = True
    cycles = reconnects = consecutive_errors = 0
    while is_cum4_host_enabled() and not host.snapshot()["halted"]:
        try:
            response = transport.list_messages(live_chat_id, page_token=page_token)
        except YouTubeChatTransportError:
            consecutive_errors += 1
            reconnects += 1
            if consecutive_errors > reconnect_limit:
                return HostRunResult("halted", "transport_reconnect_exhausted", cycles, reconnects)
            sleep(min(8.0, float(2 ** (consecutive_errors - 1))))
            continue
        consecutive_errors = 0
        current = float(clock())
        page_token = _field(response, "nextPageToken", "next_page_token", default=page_token)
        if bootstrap:
            host.ingest_response(
                response, received_at=current, now=current, bootstrap=True
            )
            bootstrap = False
        else:
            result = host.handle_response(response, received_at=current, now=current)
            if result.status == "halted":
                return HostRunResult("halted", result.reason_code, cycles, reconnects)
        cycles += 1
        if max_cycles > 0 and cycles >= max_cycles:
            return HostRunResult("completed", "cycle_limit", cycles, reconnects)
        interval_ms = _field(
            response, "pollingIntervalMillis", "polling_interval_millis", default=1000
        )
        try:
            wait_seconds = max(1.0, min(10.0, float(interval_ms) / 1000.0 + 0.3))
        except (TypeError, ValueError):
            wait_seconds = 1.3
        sleep(wait_seconds)
    reason = host.snapshot()["halt_reason"] or "cum4_disabled"
    return HostRunResult("stopped", reason, cycles, reconnects)


__all__ = [
    "CUM4_MAX_QUEUE",
    "CUM4_MAX_OWN_MESSAGE_IDS",
    "HostJob",
    "HostResult",
    "HostRunResult",
    "PHASE",
    "YouTubeTextHost",
    "is_cum4_host_enabled",
    "run_text_host",
]
