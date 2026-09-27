"""Owner-invoked fixed-text local audio canary for Stream V1 CUM5."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True

CANARY_TEXT = (
    "Nana đang kiểm tra âm thanh cục bộ. "
    "Nếu nghe trọn câu này, lượt phát đã hoàn tất."
)


def _manifest(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    before_data = _manifest(root / "data")

    from nana.runtime.avatar_mouth_stream import get_avatar_mouth_stream
    from nana.runtime.public_context_boundary import PublicEventScope
    from nana.runtime.public_delivery_state import PublicDeliveryRecord
    from nana.runtime.public_identity import CanonicalPublicIdentity
    from nana.runtime.stream_cum0_contract import compute_correlation_id
    from nana.runtime.stream_cum2_response import ResponseArtifact
    from nana.runtime.stream_cum3_youtube_publish import YouTubePublishResult
    from nana.runtime.stream_cum5_voice_engine_adapter import (
        VoiceEnginePublicPlaybackPort,
    )
    from nana.runtime.stream_cum5_voice_playback import (
        PublicVoicePlaybackController,
    )
    from nana.runtime.stream_state import (
        AvatarEnergy,
        ErrorType,
        InteractionTone,
        StreamPolicy,
        StreamState,
        ViewerExpectation,
    )
    from nana.voice.engine import VoiceEngine
    from nana.voice import lipsync as lipsync_module

    flags = (
        "NANA_STREAM_CUM0_ENABLED",
        "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED",
        "NANA_STREAM_CUM2_RESPONSE_ENABLED",
        "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED",
        "NANA_STREAM_CUM4_HOST_ENABLED",
        "NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED",
    )
    saved_flags = {name: os.environ.get(name) for name in flags}
    mouth = get_avatar_mouth_stream()
    mouth_before = int(mouth.snapshot().get("cursor") or 0)
    provider_requests = 0
    sink_writes = 0
    records: list[PublicDeliveryRecord] = []
    engine = None
    result_data: dict[str, object] = {
        "status": "error",
        "reason_code": "canary_not_run",
    }

    class PolicySource:
        def get_policy(self):
            return StreamPolicy(
                state=StreamState.LIVE_ACTIVE,
                reason="cum5-local-canary",
                can_proactive=False,
                can_auto_send=False,
                can_use_private_memory=False,
                proactive_budget=0,
                interaction_tone=InteractionTone.QUIET,
                avatar_energy=AvatarEnergy.LOW,
                viewer_expectation=ViewerExpectation.MUTED,
                error_type=ErrorType.NONE,
                can_reply=True,
                can_speak=True,
                can_avatar=False,
            )

    class Recorder:
        def record_delivery(self, record):
            records.append(record)
            return True

    class CountingOutputStream:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def __enter__(self):
            self.wrapped.__enter__()
            return self

        def __exit__(self, *args):
            return self.wrapped.__exit__(*args)

        def write(self, frame):
            nonlocal sink_writes
            result = self.wrapped.write(frame)
            sink_writes += 1
            return result

    def output_stream_factory(**kwargs):
        return CountingOutputStream(lipsync_module.sd.OutputStream(**kwargs))

    try:
        for name in flags:
            os.environ[name] = "1"

        now = time.time()
        identity = CanonicalPublicIdentity(
            platform="youtube",
            author_id="cum5-local-owner",
            actor_key="youtube:cum5-local-owner",
        )
        scope = PublicEventScope(
            platform="youtube",
            room_id="yt-cum5-local-room",
            stream_session_id="yt-cum5-local-session",
            event_id="LCC-cum5-local-event-20260918",
            display_name="Local Operator",
            identity=identity,
        )
        correlation_id = compute_correlation_id(scope)
        artifact = ResponseArtifact(
            scope=scope,
            correlation_id=correlation_id,
            source_revision=0,
            source_attempt_id="cum5-local-ingress-attempt",
            output_id="cum5-local-output",
            text=CANARY_TEXT,
            model_route="nana-public",
            generated_at=now - 2.0,
        )
        generated = PublicDeliveryRecord(
            event_id=scope.event_id,
            output_id=artifact.output_id,
            state="generated",
            attempt_id="cum5-local-delivery-attempt",
            revision=0,
            text_preview=CANARY_TEXT[:96],
            scope=scope,
            updated_at=now - 1.0,
        )
        published = PublicDeliveryRecord(
            event_id=scope.event_id,
            output_id=artifact.output_id,
            state="published",
            attempt_id=generated.attempt_id,
            revision=1,
            text_preview=CANARY_TEXT[:96],
            scope=scope,
            updated_at=now,
        )
        publish_result = YouTubePublishResult(
            status="published",
            reason_code="youtube_acknowledged",
            delivery_record=published,
            provider_message_id="LCC-cum5-local-provider-ack",
            http_status=200,
            correlation_id=correlation_id,
            generated_record=generated,
            published_record=published,
        )

        engine = VoiceEngine(avatar_mouth_enabled=False, start_worker=False)
        original_post = engine._voice_http_post

        def counted_post(*args, **kwargs):
            nonlocal provider_requests
            provider_requests += 1
            if provider_requests > 1:
                raise RuntimeError("cum5_provider_request_bound_exceeded")
            return original_post(*args, **kwargs)

        engine._voice_http_post = counted_post
        port = VoiceEnginePublicPlaybackPort(
            engine_factory=lambda: engine,
            output_stream_factory=output_stream_factory,
        )
        controller = PublicVoicePlaybackController(
            playback_port_factory=lambda: port,
            policy_source=PolicySource(),
            active_session_id=scope.stream_session_id,
            delivery_recorder=Recorder(),
        )
        result = controller.play_published(
            artifact,
            publish_result,
            now=now + 0.01,
        )
        result_data = {
            "status": result.status,
            "reason_code": result.reason_code,
            "delivery_states": [record.state for record in records],
            "delivery_revisions": [record.revision for record in records],
            "provider_requests": provider_requests,
            "sink_write_calls": sink_writes,
            "first_audio_receipt": result.start_evidence is not None,
            "terminal_audio_completed": bool(
                result.terminal_evidence
                and result.terminal_evidence.audio_completed
            ),
        }
    except Exception as exc:
        result_data = {
            "status": "error",
            "reason_code": type(exc).__name__,
            "provider_requests": provider_requests,
            "sink_write_calls": sink_writes,
            "delivery_states": [record.state for record in records],
        }
    finally:
        if engine is not None:
            try:
                engine.shutdown()
            except Exception:
                pass
        for name, value in saved_flags.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    mouth_after = int(mouth.snapshot().get("cursor") or 0)
    result_data.update(
        production_data_unchanged=before_data == _manifest(root / "data"),
        avatar_mouth_unchanged=mouth_before == mouth_after,
        flags_restored=all(
            os.environ.get(name) == value for name, value in saved_flags.items()
        ),
        youtube_calls=0,
        model_calls=0,
        avatar_calls=0,
        obs_calls=0,
        memory_writes=0,
    )
    print(json.dumps(result_data, ensure_ascii=False), flush=True)
    accepted = bool(
        result_data.get("status") == "delivered"
        and result_data.get("delivery_states")
        == ["generated", "published", "playback_started", "delivered"]
        and result_data.get("provider_requests") == 1
        and int(result_data.get("sink_write_calls") or 0) > 0
        and result_data.get("first_audio_receipt") is True
        and result_data.get("terminal_audio_completed") is True
        and result_data.get("production_data_unchanged") is True
        and result_data.get("avatar_mouth_unchanged") is True
        and result_data.get("flags_restored") is True
    )
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
