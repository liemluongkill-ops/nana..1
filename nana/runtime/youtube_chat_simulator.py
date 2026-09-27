"""Local REST-shaped fixture transport for the Stream V1 receive path.

The simulator replaces only the external YouTube network. It feeds one
provider-shaped text event at a time through the real ingress, CUM0 and CUM1,
and may optionally continue through CUM2 or the CUM4 host with a fake sender.
Results are always labelled local simulation and are never live evidence.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
import types
from typing import Any, Callable, Iterator, Sequence


def _install_direct_run_namespace() -> None:
    """Bypass Nana's eager root facade for the provider-free direct CLI."""

    if __name__ != "__main__" or __package__:
        return
    nana_root = Path(__file__).resolve().parents[1]
    package_parent = nana_root.parent
    if str(package_parent) not in sys.path:
        sys.path.insert(0, str(package_parent))
    if "nana" not in sys.modules:
        package = types.ModuleType("nana")
        package.__path__ = [str(nana_root)]
        sys.modules["nana"] = package
    if "nana.runtime" not in sys.modules:
        runtime = types.ModuleType("nana.runtime")
        runtime.__path__ = [str(nana_root / "runtime")]
        sys.modules["nana.runtime"] = runtime


_install_direct_run_namespace()

from nana.runtime.stream_cum0_contract import StreamContractLedger
from nana.runtime.stream_cum2_response import PublicResponseGenerator, YouTubeCum2Pipeline
from nana.runtime.stream_state import (
    AvatarEnergy,
    ErrorType,
    InteractionTone,
    StreamPolicy,
    StreamState,
    ViewerExpectation,
)
from nana.runtime.youtube_chat_cum1 import YouTubeChatCum1, YouTubeLiveSession
from nana.runtime.youtube_chat_ingress import YouTubeChatIngress


PHASE = "STREAM-V1-LOCAL-SIMULATOR"
EVIDENCE_LEVEL = "local_simulation"
INPUT_SOURCE = "local_fixture"
DEFAULT_FAKE_REPLY = "Mình đã nhận được tin nhắn mô phỏng."
_PIPELINE_FLAGS = (
    "NANA_STREAM_CUM0_ENABLED",
    "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED",
    "NANA_STREAM_CUM2_RESPONSE_ENABLED",
    "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED",
    "NANA_STREAM_CUM4_HOST_ENABLED",
    "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED",
)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def is_local_simulator_enabled() -> bool:
    return _env_flag("NANA_STREAM_LOCAL_SIMULATOR_ENABLED", False)


def _safe_reason_code(value: Any, fallback: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 96 or not value.isascii():
        return fallback
    if not all(character.isalnum() or character in "_.-" for character in value):
        return fallback
    return value


@contextmanager
def _scoped_pipeline_flags(stage: str) -> Iterator[None]:
    previous = {name: (name in os.environ, os.environ.get(name)) for name in _PIPELINE_FLAGS}
    os.environ["NANA_STREAM_CUM0_ENABLED"] = "1"
    os.environ["NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED"] = "1"
    os.environ["NANA_STREAM_CUM2_RESPONSE_ENABLED"] = "1" if stage in {"generate", "host", "voice"} else "0"
    os.environ["NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED"] = "1" if stage == "host" else "0"
    os.environ["NANA_STREAM_CUM4_HOST_ENABLED"] = "1" if stage in {"host", "voice"} else "0"
    os.environ["NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED"] = "1" if stage == "voice" else "0"
    try:
        yield
    finally:
        for name, (was_present, value) in previous.items():
            if was_present:
                os.environ[name] = "" if value is None else value
            else:
                os.environ.pop(name, None)


class _LocalLivePolicySource:
    def __init__(self, *, can_speak: bool = False) -> None:
        self.can_speak = bool(can_speak)

    def get_policy(self) -> StreamPolicy:
        return StreamPolicy(
            state=StreamState.LIVE_ACTIVE,
            reason="local_stream_simulation",
            can_proactive=False,
            can_auto_send=False,
            can_use_private_memory=False,
            proactive_budget=0,
            interaction_tone=InteractionTone.QUIET,
            avatar_energy=AvatarEnergy.LOW,
            viewer_expectation=ViewerExpectation.MUTED,
            error_type=ErrorType.NONE,
            can_reply=True,
            can_speak=self.can_speak,
            can_avatar=False,
        )


class _RecordingDeliveryRecorder:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.records: list[Any] = []

    def record_delivery(self, record: Any) -> bool:
        accepted = self.delegate.record_delivery(record) is True
        if accepted:
            self.records.append(record)
        return accepted


class _LocalFakePlaybackPort:
    """Production-shaped receipts without provider or local audio I/O."""

    def __init__(self) -> None:
        self.play_calls = 0
        self.first_audio_receipts = 0
        self.completion_receipts = 0

    def ready(self):
        from nana.runtime.stream_cum5_voice_playback import PlaybackReadiness

        return PlaybackReadiness(True, "local_fake_ready")

    def play(self, request, *, should_continue, on_first_audio):
        from nana.runtime.stream_cum5_voice_playback import (
            AudioCompletionResult,
            FirstAudioEvidence,
        )

        self.play_calls += 1
        if should_continue() is not True:
            return AudioCompletionResult(
                "cancelled", False, 1, 0, 0, 1,
                len(request.artifact.text), len(request.artifact.text), 0.0,
                "fixture_gate_closed", request.playback_id,
                request.content_sha256, request.requested_at, True,
            )
        started_at = request.requested_at + 0.001
        evidence = FirstAudioEvidence(
            request.playback_id,
            request.content_sha256,
            started_at,
            320,
        )
        if on_first_audio(evidence) is not True:
            return AudioCompletionResult(
                "cancelled", False, 1, 1, 0, 1,
                len(request.artifact.text), len(request.artifact.text), 0.0,
                "fixture_start_rejected", request.playback_id,
                request.content_sha256, started_at, True,
            )
        self.first_audio_receipts += 1
        self.completion_receipts += 1
        return AudioCompletionResult(
            "completed", True, 1, 1, 1, 0,
            len(request.artifact.text), 0, 1.0,
            "none", request.playback_id, request.content_sha256,
            started_at + 0.001, True,
        )

    def cancel(self, _playback_id):
        from nana.runtime.stream_cum5_voice_playback import PlaybackStopResult

        return PlaybackStopResult("confirmed_stopped", "local_fake_stopped")


class _CountingOutputStream:
    def __init__(self, wrapped: Any, counters: dict[str, int]) -> None:
        self.wrapped = wrapped
        self.counters = counters

    def __enter__(self):
        self.wrapped.__enter__()
        return self

    def __exit__(self, *exc):
        return self.wrapped.__exit__(*exc)

    def write(self, frames: Any):
        self.counters["sink"] += 1
        return self.wrapped.write(frames)


@dataclass(frozen=True)
class LocalSimulationResult:
    status: str
    reason_code: str
    stage: str
    event_id: str | None = None
    public_turn: Any | None = None
    response_artifact: Any | None = None
    model_mode: str = "none"
    reply_text: str | None = None
    host_action: str = ""
    local_publish_simulated: bool = False
    evidence_level: str = EVIDENCE_LEVEL
    input_source: str = INPUT_SOURCE
    playback_result: Any | None = None
    voice_mode: str = "none"
    voice_receipt_source: str = "none"
    model_requests: int = 0
    tts_provider_requests: int = 0
    local_audio_sink_writes: int = 0
    fake_playback_calls: int = 0
    fake_first_audio_receipts: int = 0
    fake_completion_receipts: int = 0

    def to_dict(self) -> dict[str, Any]:
        artifact = self.response_artifact
        safe_status = _safe_reason_code(self.status, "failed")
        safe_reason = _safe_reason_code(self.reason_code, "simulation_failed")
        raw_playback_status = getattr(self.playback_result, "status", None)
        playback_status = (
            _safe_reason_code(raw_playback_status, "unknown")
            if raw_playback_status is not None
            else None
        )
        raw_playback_reason = getattr(self.playback_result, "reason_code", None)
        playback_reason = (
            safe_reason
            if playback_status in {"interrupted", "halted"}
            else _safe_reason_code(raw_playback_reason, "voice_not_delivered")
            if raw_playback_reason is not None
            else None
        )
        provider_calls_attempted = bool(
            (self.model_mode == "real_llmgate" and self.model_requests > 0)
            or self.tts_provider_requests > 0
        )
        return {
            "phase": PHASE,
            "evidence_level": self.evidence_level,
            "source_claim": "synthetic_fixture",
            "transport": "rest_shaped_fixture",
            "simulated_platform": "youtube",
            "stage": self.stage,
            "status": safe_status,
            "reason_code": safe_reason,
            "event_id": self.event_id,
            "public_turn_created": self.public_turn is not None,
            "response_generated": artifact is not None,
            "reply": getattr(artifact, "text", None) or self.reply_text,
            "correlation_id": getattr(artifact, "correlation_id", None),
            "output_id": getattr(artifact, "output_id", None),
            "model_mode": self.model_mode,
            "model_route": getattr(artifact, "model_route", None),
            "source_attempt_id_present": bool(getattr(artifact, "source_attempt_id", "")),
            "provider_calls_attempted": provider_calls_attempted,
            "provider_network_observed": None if provider_calls_attempted else False,
            "model_requests": self.model_requests,
            "model_provider_called": (
                self.model_mode == "real_llmgate" and self.model_requests > 0
            ),
            "youtube_network_called": False,
            "youtube_output_called": False,
            "cum3_imported": "nana.runtime.stream_cum3_youtube_publish" in sys.modules,
            "local_publisher_constructed": self.stage == "host",
            "local_publication_calls": int(self.local_publish_simulated),
            "host_action": self.host_action,
            "local_publish_simulated": self.local_publish_simulated,
            "playback_status": playback_status,
            "playback_reason_code": playback_reason,
            "voice_mode": self.voice_mode,
            "voice_receipt_source": self.voice_receipt_source,
            "fake_playback_calls": self.fake_playback_calls,
            "fake_first_audio_receipts": self.fake_first_audio_receipts,
            "fake_completion_receipts": self.fake_completion_receipts,
            "tts_provider_requests": self.tts_provider_requests,
            "local_audio_sink_writes": self.local_audio_sink_writes,
            "tts_called": self.tts_provider_requests > 0,
            "effects_measurement": {
                "model": "gateway_client_invocations",
                "tts_provider": "client_invocations",
                "local_audio_sink": "write_calls",
                "provider_network": (
                    "unmeasured" if provider_calls_attempted else "not_attempted"
                ),
            },
            "avatar_called": False,
            "obs_called": False,
            "memory_write_called": False,
        }


class _LocalFakeYouTubeSender:
    """Capture CUM3 output locally without constructing an HTTP session."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def send_text(self, live_chat_id: str, text: str):
        from nana.runtime.stream_cum3_youtube_publish import YouTubeSendResult

        self.calls.append((live_chat_id, text))
        return YouTubeSendResult(
            "published",
            "local_simulated_ack",
            200,
            f"local-sim-published-{len(self.calls):06d}",
        )


class LocalYouTubeChatSimulator:
    """Keep a local simulated room/session alive across multiple submissions."""

    def __init__(
        self,
        *,
        stage: str = "ingress",
        viewer_name: str = "Local Viewer",
        author_id: str = "local-sim-viewer",
        room_id: str = "local-sim-room",
        session_id: str = "local-sim-session",
        model_caller: Callable[..., tuple[str | None, str]] | None = None,
        fake_reply: str = DEFAULT_FAKE_REPLY,
        real_model: bool = False,
        real_voice: bool = False,
        playback_port_factory: Callable[[], Any] | None = None,
    ) -> None:
        if stage not in {"ingress", "generate", "host", "voice"}:
            raise ValueError("stage must be ingress, generate, host, or voice")
        if real_model and stage not in {"generate", "host", "voice"}:
            raise ValueError("real model requires generate, host, or voice stage")
        if real_model and model_caller is not None:
            raise ValueError("real model cannot use an injected caller")
        if real_voice and stage != "voice":
            raise ValueError("real voice requires voice stage")
        if playback_port_factory is not None and stage != "voice":
            raise ValueError("playback injection requires voice stage")
        if real_voice and playback_port_factory is not None:
            raise ValueError("real voice cannot use an injected playback port")
        for value, name in (
            (viewer_name, "viewer_name"),
            (author_id, "author_id"),
            (room_id, "room_id"),
            (session_id, "session_id"),
        ):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise ValueError(f"invalid {name}")

        self.stage = stage
        self.viewer_name = viewer_name
        self.author_id = author_id
        self.room_id = room_id
        self.session_id = session_id
        self.model_mode = "real_llmgate" if real_model else ("fake" if stage in {"generate", "host", "voice"} else "none")
        self.voice_mode = "real_voice" if real_voice else ("fake" if stage == "voice" else "none")
        self._sequence = 0
        self._lock = threading.RLock()
        self._model_requests = 0
        self._voice_counts = {"tts": 0, "sink": 0}
        self._ingress = YouTubeChatIngress(actionable_limit=1)
        self._policy_source = _LocalLivePolicySource(can_speak=stage == "voice")
        self._cum1 = YouTubeChatCum1(
            session=YouTubeLiveSession(room_id, session_id),
            policy_source=self._policy_source,
            ingress=self._ingress,
            ledger=StreamContractLedger(),
        )
        self._cum2 = None
        self._host = None
        self._voice_host = None
        self._delivery_recorder = None
        self._fake_playback = None
        self._voice_receipt_source = "none"
        self._fake_sender = None
        if stage in {"generate", "host", "voice"}:
            caller = (
                self._real_caller
                if real_model
                else (model_caller or self._fake_caller(fake_reply))
            )

            def counted_caller(**kwargs):
                self._model_requests += 1
                return caller(**kwargs)

            if stage == "voice":
                from nana.runtime.social_session import SocialSessionCache

                session_context = SocialSessionCache()
                generator = PublicResponseGenerator(
                    caller=counted_caller,
                    session_context=session_context,
                )
            else:
                session_context = None
                generator = PublicResponseGenerator(caller=counted_caller)
            if stage == "generate":
                self._cum2 = YouTubeCum2Pipeline(
                    ingress=self._cum1,
                    generator=generator,
                )
            else:
                from nana.runtime.social_session import SocialSessionCache
                from nana.runtime.stream_cum4_host import YouTubeTextHost

                social_session = session_context or SocialSessionCache()
                publisher = None
                if stage == "host":
                    from nana.runtime.stream_cum3_youtube_publish import YouTubeCum3Publisher

                    self._fake_sender = _LocalFakeYouTubeSender()
                    publisher = YouTubeCum3Publisher(
                        sender=self._fake_sender,
                        min_send_interval_seconds=0,
                    )
                self._host = YouTubeTextHost(
                    cum1=self._cum1,
                    generator=generator,
                    publisher=publisher,
                    social_session=social_session,
                    min_publish_interval_seconds=0,
                )
                if stage == "voice":
                    from nana.runtime.stream_cum5_voice_playback import PublicVoicePlaybackController
                    from nana.runtime.stream_voice_host import PublicVoiceHost

                    self._delivery_recorder = _RecordingDeliveryRecorder(
                        generator.delivery_recorder
                    )
                    if real_voice:
                        active_playback_factory = self._real_playback_port_factory
                        self._voice_receipt_source = "real_voice_adapter"
                    elif playback_port_factory is not None:
                        active_playback_factory = playback_port_factory
                        self._voice_receipt_source = "injected_playback"
                    else:
                        self._fake_playback = _LocalFakePlaybackPort()
                        active_playback_factory = lambda: self._fake_playback
                        self._voice_receipt_source = "fake_playback_fixture"
                    controller = PublicVoicePlaybackController(
                        playback_port_factory=active_playback_factory,
                        policy_source=self._policy_source,
                        active_session_id=self.session_id,
                        delivery_recorder=self._delivery_recorder,
                    )
                    self._voice_host = PublicVoiceHost(
                        host=self._host,
                        playback=controller,
                    )

    @staticmethod
    def _fake_caller(reply: str) -> Callable[..., tuple[str | None, str]]:
        def call(**_kwargs) -> tuple[str | None, str]:
            return reply, "local_fake"

        return call

    @staticmethod
    def _real_caller(**kwargs) -> tuple[str | None, str]:
        from nana.brain.llmgate_client import call_llmgate_messages

        return call_llmgate_messages(**kwargs)

    def _real_playback_port_factory(self):
        from nana.runtime.stream_cum5_voice_engine_adapter import VoiceEnginePublicPlaybackPort

        def engine_factory():
            from nana.voice.engine import VoiceEngine

            engine = VoiceEngine(avatar_mouth_enabled=False, start_worker=False)
            original_post = engine._voice_http_post

            def counted_post(*args, **kwargs):
                self._voice_counts["tts"] += 1
                return original_post(*args, **kwargs)

            engine._voice_http_post = counted_post
            return engine

        def output_stream_factory(**kwargs):
            from nana.voice import lipsync as lipsync_module

            return _CountingOutputStream(
                lipsync_module.sd.OutputStream(**kwargs),
                self._voice_counts,
            )

        return VoiceEnginePublicPlaybackPort(
            engine_factory=engine_factory,
            output_stream_factory=output_stream_factory,
        )

    @property
    def voice_delivery_records(self) -> tuple[Any, ...]:
        records = getattr(self._delivery_recorder, "records", ())
        return tuple(records)

    def submit(self, text: str, *, now: float | None = None) -> LocalSimulationResult:
        if not is_local_simulator_enabled():
            return LocalSimulationResult("disabled", "local_simulator_disabled", self.stage)
        current = time.time() if now is None else now
        if (
            isinstance(current, bool)
            or not isinstance(current, (int, float))
            or not math.isfinite(float(current))
        ):
            return LocalSimulationResult("rejected", "invalid_simulation_time", self.stage)

        with self._lock:
            model_requests_before = self._model_requests
            tts_requests_before = self._voice_counts["tts"]
            sink_writes_before = self._voice_counts["sink"]
            self._sequence += 1
            sequence = self._sequence
            event_id = f"local-sim-event-{sequence:06d}"
            output_id = f"local-sim-output-{sequence:06d}"
            response = self._fixture_response(text, event_id, float(current))
            with _scoped_pipeline_flags(self.stage):
                if self.stage == "ingress":
                    ingress_result = self._cum1.ingest_response(
                        response,
                        received_at=float(current),
                        now=float(current),
                    )
                    return LocalSimulationResult(
                        ingress_result.status,
                        ingress_result.reason_code,
                        self.stage,
                        event_id,
                        ingress_result.public_turn,
                        None,
                        self.model_mode,
                    )

                if self.stage == "host":
                    hosted = self._host.handle_response(
                        response,
                        received_at=float(current),
                        now=float(current),
                    )
                    reply_text = (
                        self._fake_sender.calls[-1][1]
                        if hosted.status == "published" and self._fake_sender.calls
                        else None
                    )
                    return LocalSimulationResult(
                        hosted.status,
                        _safe_reason_code(hosted.reason_code, "host_failed"),
                        self.stage,
                        event_id,
                        hosted.public_turn,
                        hosted.response_artifact,
                        self.model_mode,
                        reply_text,
                        hosted.action,
                        hosted.status == "published",
                        model_requests=self._model_requests - model_requests_before,
                    )

                if self.stage == "voice":
                    ingested = self._host.ingest_response(
                        response,
                        received_at=float(current),
                        now=float(current),
                    )
                    if ingested.status in {"disabled", "halted", "rejected", "failed"}:
                        return LocalSimulationResult(
                            status=ingested.status,
                            reason_code=_safe_reason_code(
                                ingested.reason_code,
                                "host_ingest_failed",
                            ),
                            stage=self.stage,
                            event_id=event_id,
                            model_mode=self.model_mode,
                            voice_mode=self.voice_mode,
                            voice_receipt_source=self._voice_receipt_source,
                            model_requests=self._model_requests - model_requests_before,
                        )
                    voiced = self._voice_host.process_next(now=float(current))
                    hosted = voiced.host_result
                    playback = voiced.playback_result
                    raw_status = playback.status if playback is not None else hosted.status
                    status = _safe_reason_code(raw_status, "failed")
                    if playback is not None and status in {"interrupted", "halted"}:
                        reason_value = hosted.reason_code
                        reason_fallback = (
                            "voice_playback_interrupted"
                            if status == "interrupted"
                            else "voice_playback_halted"
                        )
                    else:
                        reason_value = (
                            playback.reason_code if playback is not None else hosted.reason_code
                        )
                        reason_fallback = "voice_not_delivered"
                    reason_code = _safe_reason_code(
                        reason_value,
                        reason_fallback,
                    )
                    fake = self._fake_playback
                    return LocalSimulationResult(
                        status=status,
                        reason_code=reason_code,
                        stage=self.stage,
                        event_id=event_id,
                        public_turn=hosted.public_turn,
                        response_artifact=hosted.response_artifact,
                        model_mode=self.model_mode,
                        reply_text=getattr(hosted.response_artifact, "text", None),
                        host_action=hosted.action,
                        local_publish_simulated=False,
                        playback_result=playback,
                        voice_mode=self.voice_mode,
                        voice_receipt_source=self._voice_receipt_source,
                        model_requests=self._model_requests - model_requests_before,
                        tts_provider_requests=self._voice_counts["tts"] - tts_requests_before,
                        local_audio_sink_writes=self._voice_counts["sink"] - sink_writes_before,
                        fake_playback_calls=getattr(fake, "play_calls", 0),
                        fake_first_audio_receipts=getattr(fake, "first_audio_receipts", 0),
                        fake_completion_receipts=getattr(fake, "completion_receipts", 0),
                    )

                generated = self._cum2.ingest_and_generate(
                    response,
                    output_id=output_id,
                    received_at=float(current),
                    now=float(current),
                )
                public_turn = generated.ingress.public_turn if generated.ingress is not None else None
                artifact = generated.generation.artifact if generated.generation is not None else None
                return LocalSimulationResult(
                    generated.status,
                    generated.reason_code,
                    self.stage,
                    event_id,
                    public_turn,
                    artifact,
                    self.model_mode,
                    model_requests=self._model_requests - model_requests_before,
                )

    def _fixture_response(self, text: str, event_id: str, now: float) -> dict[str, Any]:
        published = datetime.fromtimestamp(now, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "items": [{
                "id": event_id,
                "snippet": {
                    "type": "textMessageEvent",
                    "liveChatId": self.room_id,
                    "authorChannelId": self.author_id,
                    "publishedAt": published,
                    "displayMessage": text,
                },
                "authorDetails": {
                    "displayName": self.viewer_name,
                    "channelId": self.author_id,
                },
            }],
            "nextPageToken": f"local-sim-cursor-{self._sequence:06d}",
            "pollingIntervalMillis": 1000,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Feed local YouTube-shaped chat fixtures through Stream V1.",
    )
    parser.add_argument("--stage", choices=("ingress", "generate", "host", "voice"), default="ingress")
    parser.add_argument("--text", action="append", required=True)
    parser.add_argument("--viewer-name", default="Local Viewer")
    parser.add_argument("--author-id", default="local-sim-viewer")
    parser.add_argument("--room-id", default="local-sim-room")
    parser.add_argument("--session-id", default="local-sim-session")
    parser.add_argument("--fake-reply", default=DEFAULT_FAKE_REPLY)
    parser.add_argument(
        "--real-model",
        action="store_true",
        help="Explicitly call the configured nana-public LLMGate route; never calls YouTube.",
    )
    parser.add_argument(
        "--real-voice",
        action="store_true",
        help="Explicitly call the configured exact TTS/local sink; voice stage only.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        simulator = LocalYouTubeChatSimulator(
            stage=args.stage,
            viewer_name=args.viewer_name,
            author_id=args.author_id,
            room_id=args.room_id,
            session_id=args.session_id,
            fake_reply=args.fake_reply,
            real_model=bool(args.real_model),
            real_voice=bool(args.real_voice),
        )
    except ValueError as exc:
        print(json.dumps({
            "phase": PHASE,
            "evidence_level": EVIDENCE_LEVEL,
            "status": "rejected",
            "reason_code": str(exc),
        }))
        return 2

    exit_code = 0
    for text in args.text:
        result = simulator.submit(text)
        print(json.dumps(result.to_dict(), ensure_ascii=False))
        if result.status not in {"accepted", "generated", "published", "delivered", "idle"}:
            exit_code = 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_FAKE_REPLY",
    "EVIDENCE_LEVEL",
    "INPUT_SOURCE",
    "LocalSimulationResult",
    "LocalYouTubeChatSimulator",
    "PHASE",
    "is_local_simulator_enabled",
    "main",
]
