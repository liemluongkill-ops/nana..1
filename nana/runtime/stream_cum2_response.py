"""Public-only response generation boundary for STREAM V1 CUM 2.

The boundary consumes an admitted public turn plus its full sanitized source
text, calls only an injected/public model route, and stops at an immutable
generated artifact. It does not create delivery records or call memory, TTS,
playback, avatar, OBS, YouTube output, or any other sink.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import os
from typing import Any, Callable

from nana.runtime.livestream_identity import finalize_livestream_identity
from nana.runtime.persona_boundary import (
    public_safe_fallback_for_message,
    resolve_persona_boundary,
    sanitize_public_reply,
)
from nana.runtime.public_context_boundary import PublicEventScope
from nana.runtime.stream_cum0_contract import (
    CUM0_MAX_ID_CHARS,
    compute_correlation_id,
    normalize_sanitized_text,
)


PHASE = "STREAM-V1-CUM2-RESPONSE"
CUM2_MODEL_ROUTE = "nana-public"
CUM2_DETERMINISTIC_ROUTE = "nana-public-deterministic"
CUM2_MAX_RESPONSE_CHARS = 1200
PublicModelCaller = Callable[..., tuple[str | None, str]]


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def is_cum2_response_enabled() -> bool:
    return _env_flag("NANA_STREAM_CUM2_RESPONSE_ENABLED", False)


def _default_public_model_caller(**kwargs) -> tuple[str | None, str]:
    from nana.brain.llmgate_client import call_llmgate_messages

    return call_llmgate_messages(**kwargs)


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


@dataclass(frozen=True)
class ResponseArtifact:
    """Generated text only; later delivery code owns attempts and receipts."""

    scope: PublicEventScope
    correlation_id: str
    source_revision: int
    source_attempt_id: str
    output_id: str
    text: str
    model_route: str
    generated_at: float
    generation_state: str = "generated"
    response_kind: str = "full_reply"

    @property
    def event_id(self) -> str:
        return self.scope.event_id

    @property
    def actor_key(self) -> str:
        return self.scope.identity.actor_key

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["event_id"] = self.event_id
        data["actor_key"] = self.actor_key
        return data


@dataclass(frozen=True)
class GenerationResult:
    status: str
    reason_code: str
    artifact: ResponseArtifact | None = None


@dataclass(frozen=True)
class YouTubePipelineResult:
    status: str
    reason_code: str
    ingress: Any | None = None
    generation: GenerationResult | None = None


class SessionDeliveryRecorder:
    """Project immutable delivery records into one injected session context."""

    def __init__(self, session_context: Any) -> None:
        self._session_context = session_context

    def record_delivery(self, record: Any) -> bool:
        from nana.runtime.public_delivery_state import PublicDeliveryRecord

        if not isinstance(record, PublicDeliveryRecord):
            return False
        self._session_context.record_reply_context(
            scope=record.scope,
            delivery_record=record,
        )
        return True


def _public_messages(
    scope: PublicEventScope,
    source_text: str,
    public_room_context: str,
) -> list[dict[str, str]]:
    boundary = resolve_persona_boundary(
        viewer_name=scope.display_name,
        stream_mode=True,
        platform=scope.platform,
    )
    if not boundary.public or not boundary.livestream:
        raise ValueError("non_livestream_scope")
    blocks = [
            "You are Nana's public livestream voice. Reply naturally in Vietnamese, usually in one to three concise sentences.",
            boundary.prompt_block,
            (
                "PUBLIC INPUT ONLY:\n"
                f"- platform={scope.platform}\n"
                f"- viewer display label={scope.display_name}\n"
                "- stable account IDs, event IDs, backend state, and non-public context are unavailable\n"
                "- answer only the viewer message below; do not claim an action was delivered"
            ),
    ]
    if public_room_context:
        blocks.append(public_room_context)
    system = "\n\n".join(blocks)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": source_text},
    ]


class PublicResponseGenerator:
    """Generate one public artifact without crossing into output ownership."""

    def __init__(
        self,
        *,
        caller: PublicModelCaller | None = None,
        session_context: Any | None = None,
    ) -> None:
        self._caller = caller or _default_public_model_caller
        self.model_route = CUM2_MODEL_ROUTE
        if session_context is None:
            from nana.runtime.social_session import SocialSessionCache

            session_context = SocialSessionCache()
        self._session_context = session_context
        self.delivery_recorder = SessionDeliveryRecorder(session_context)

    def generate(
        self,
        turn: Any,
        *,
        source_text: str,
        output_id: str,
        now: float,
    ) -> GenerationResult:
        if not is_cum2_response_enabled():
            return GenerationResult("disabled", "cum2_disabled")

        scope = getattr(turn, "scope", None)
        if not _scope_is_canonical(scope):
            return GenerationResult("rejected", "invalid_public_turn")
        if getattr(turn, "event_type", None) != "text":
            return GenerationResult("rejected", "unsupported_event_type")
        revision = getattr(turn, "revision", None)
        if type(revision) is not int or revision < 0:
            return GenerationResult("rejected", "invalid_source_revision")
        source_attempt_id = getattr(turn, "attempt_id", "")
        if not isinstance(source_attempt_id, str):
            return GenerationResult("rejected", "invalid_source_attempt")
        canonical_output_id = _canonical_id(output_id)
        if canonical_output_id is None:
            return GenerationResult("rejected", "invalid_output_id")
        if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(float(now)):
            return GenerationResult("rejected", "invalid_generation_time")
        try:
            normalized_source = normalize_sanitized_text(source_text)
        except ValueError as exc:
            return GenerationResult("rejected", str(exc))
        if getattr(turn, "message_preview", None) != normalized_source[:96]:
            return GenerationResult("rejected", "source_text_mismatch")

        try:
            self._session_context.record_public_turn(
                scope=scope,
                text=normalized_source,
                revision=revision,
                attempt_id=source_attempt_id,
            )
            public_room_context = self._session_context.format_public_room_context(
                scope=scope,
                limit=5,
            )
        except Exception:
            public_room_context = ""
        try:
            messages = _public_messages(scope, normalized_source, public_room_context)
        except ValueError as exc:
            return GenerationResult("rejected", str(exc))
        try:
            raw_reply, _debug = self._caller(
                model_name=self.model_route,
                messages=messages,
                max_tokens=180,
                temperature=0.75,
                timeout_s=30.0,
            )
        except Exception:
            return GenerationResult("failed", "provider_error")
        if raw_reply is None:
            return GenerationResult("failed", "provider_failed")
        raw_text = str(raw_reply).strip()
        if not raw_text:
            return GenerationResult("failed", "empty_response")
        if len(raw_text) > CUM2_MAX_RESPONSE_CHARS:
            return GenerationResult("failed", "response_too_long")

        fallback = public_safe_fallback_for_message(
            normalized_source,
            viewer_name=scope.display_name,
        )
        cleaned = sanitize_public_reply(
            raw_text,
            fallback=fallback,
            user_text=normalized_source,
            viewer_name=scope.display_name,
        )
        cleaned = finalize_livestream_identity(
            cleaned,
            source=scope.platform,
            viewer_name=scope.display_name,
        ).strip()
        if not cleaned:
            return GenerationResult("failed", "empty_response")

        artifact = ResponseArtifact(
            scope=scope,
            correlation_id=compute_correlation_id(scope),
            source_revision=revision,
            source_attempt_id=source_attempt_id,
            output_id=canonical_output_id,
            text=cleaned,
            model_route=self.model_route,
            generated_at=float(now),
        )
        return GenerationResult("generated", "generated", artifact)


def build_ack_response(
    turn: Any,
    *,
    text: str,
    output_id: str,
    now: float,
) -> GenerationResult:
    """Build a public-safe deterministic acknowledgement without a model call."""

    if not is_cum2_response_enabled():
        return GenerationResult("disabled", "cum2_disabled")
    scope = getattr(turn, "scope", None)
    if not _scope_is_canonical(scope) or getattr(turn, "event_type", None) != "text":
        return GenerationResult("rejected", "invalid_public_turn")
    revision = getattr(turn, "revision", None)
    if type(revision) is not int or revision < 0:
        return GenerationResult("rejected", "invalid_source_revision")
    canonical_output_id = _canonical_id(output_id)
    if canonical_output_id is None:
        return GenerationResult("rejected", "invalid_output_id")
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(float(now)):
        return GenerationResult("rejected", "invalid_generation_time")
    raw = str(text or "").strip()
    if not raw or len(raw) > CUM2_MAX_RESPONSE_CHARS:
        return GenerationResult("rejected", "invalid_ack_text")
    cleaned = sanitize_public_reply(
        raw,
        fallback="Mình thấy rồi nha.",
        viewer_name=scope.display_name,
    )
    cleaned = finalize_livestream_identity(
        cleaned,
        source=scope.platform,
        viewer_name=scope.display_name,
    ).strip()
    artifact = ResponseArtifact(
        scope=scope,
        correlation_id=compute_correlation_id(scope),
        source_revision=revision,
        source_attempt_id=str(getattr(turn, "attempt_id", "") or ""),
        output_id=canonical_output_id,
        text=cleaned,
        model_route=CUM2_DETERMINISTIC_ROUTE,
        generated_at=float(now),
        response_kind="ack_only",
    )
    return GenerationResult("generated", "generated_ack", artifact)


class YouTubeCum2Pipeline:
    """Compose CUM1 admission with CUM2 generation, stopping before output."""

    def __init__(self, *, ingress: Any, generator: PublicResponseGenerator) -> None:
        self.ingress = ingress
        self.generator = generator

    def ingest_and_generate(
        self,
        response: Any,
        *,
        output_id: str,
        received_at: float | None = None,
        bootstrap: bool = False,
        now: float,
    ) -> YouTubePipelineResult:
        if not is_cum2_response_enabled():
            return YouTubePipelineResult("disabled", "cum2_disabled")
        current = now
        ingress_result = self.ingress.ingest_response(
            response,
            received_at=received_at,
            bootstrap=bootstrap,
            now=current,
        )
        turn = ingress_result.public_turn
        if turn is None:
            return YouTubePipelineResult(
                ingress_result.status,
                ingress_result.reason_code,
                ingress_result,
                None,
            )
        ingest_result = ingress_result.ingest_result
        events = getattr(ingest_result, "new_events", ()) if ingest_result is not None else ()
        source_event = next(
            (event for event in events if event.message_id == turn.scope.event_id),
            None,
        )
        if source_event is None:
            return YouTubePipelineResult(
                "rejected",
                "source_event_missing",
                ingress_result,
                None,
            )
        generation = self.generator.generate(
            turn,
            source_text=source_event.text,
            output_id=output_id,
            now=current,
        )
        return YouTubePipelineResult(
            generation.status,
            generation.reason_code,
            ingress_result,
            generation,
        )


__all__ = [
    "CUM2_MAX_RESPONSE_CHARS",
    "CUM2_MODEL_ROUTE",
    "CUM2_DETERMINISTIC_ROUTE",
    "GenerationResult",
    "PHASE",
    "PublicResponseGenerator",
    "ResponseArtifact",
    "SessionDeliveryRecorder",
    "YouTubeCum2Pipeline",
    "YouTubePipelineResult",
    "build_ack_response",
    "is_cum2_response_enabled",
]
