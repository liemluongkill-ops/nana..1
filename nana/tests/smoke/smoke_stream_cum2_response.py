"""Small provider-free smoke for STREAM V1 CUM 2 response generation."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


NOW = 1_700_000_200.0


def _turn(text: str):
    from nana.runtime.public_context_boundary import PublicEventScope
    from nana.runtime.public_identity import CanonicalPublicIdentity
    from nana.runtime.social_session import PublicTurn

    identity = CanonicalPublicIdentity(
        platform="youtube",
        author_id="yt-channel-1",
        actor_key="youtube:yt-channel-1",
    )
    scope = PublicEventScope(
        platform="youtube",
        room_id="yt-live-chat-1",
        stream_session_id="yt-session-1",
        event_id="yt-event-1",
        display_name="Minh",
        identity=identity,
    )
    return PublicTurn(
        timestamp=NOW - 1,
        monotonic=123.0,
        viewer_name="Minh",
        event_type="text",
        director_mode="chill",
        message_preview=text[:96],
        scope=scope,
        revision=0,
        attempt_id="yt-ingress-attempt-1",
    )


class _Caller:
    def __init__(self, reply: str | None = "Mình đang nghe đây, bạn kể tiếp đi.", *, raises: bool = False):
        self.reply = reply
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises:
            raise RuntimeError("provider unavailable")
        return self.reply, "ok" if self.reply else "empty"


def _policy(state):
    from nana.runtime.stream_state import (
        AvatarEnergy,
        ErrorType,
        InteractionTone,
        StreamPolicy,
        ViewerExpectation,
    )

    return StreamPolicy(
        state=state,
        reason="cum2-smoke",
        can_proactive=False,
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=0,
        interaction_tone=InteractionTone.QUIET,
        avatar_energy=AvatarEnergy.LOW,
        viewer_expectation=ViewerExpectation.MUTED,
        error_type=ErrorType.NONE,
        can_reply=True,
        can_speak=False,
        can_avatar=False,
    )


class _PolicySource:
    def __init__(self, policy) -> None:
        self.policy = policy

    def get_policy(self):
        return self.policy


def test_flag_off_never_calls_provider() -> None:
    from nana.runtime.stream_cum2_response import PublicResponseGenerator

    caller = _Caller(raises=True)
    generator = PublicResponseGenerator(caller=caller)
    with patch.dict(os.environ, {"NANA_STREAM_CUM2_RESPONSE_ENABLED": "0"}, clear=False):
        result = generator.generate(
            _turn("Xin chao"),
            source_text="Xin chao",
            output_id="yt-output-1",
            now=NOW,
        )

    assert result.status == "disabled", result
    assert result.reason_code == "cum2_disabled", result
    assert result.artifact is None
    assert caller.calls == []


def test_public_turn_generates_correlated_public_artifact() -> None:
    from nana.runtime.stream_cum0_contract import compute_correlation_id
    from nana.runtime.stream_cum2_response import PublicResponseGenerator

    source = (
        "Nana oi, hom nay minh muon ke mot cau chuyen dai hon binh thuong de "
        "chung minh prompt van nhan du full text sau message preview."
    )
    caller = _Caller("Ba ơi, con đang nghe đây.")
    turn = _turn(source)
    generator = PublicResponseGenerator(caller=caller)
    with patch.dict(os.environ, {"NANA_STREAM_CUM2_RESPONSE_ENABLED": "1"}, clear=False):
        result = generator.generate(
            turn,
            source_text=source,
            output_id="yt-output-1",
            now=NOW,
        )

    assert result.status == "generated", result
    assert result.reason_code == "generated", result
    artifact = result.artifact
    assert artifact is not None
    assert artifact.scope is turn.scope
    assert artifact.event_id == "yt-event-1"
    assert artifact.correlation_id == compute_correlation_id(turn.scope)
    assert artifact.output_id == "yt-output-1"
    assert artifact.source_revision == 0
    assert artifact.source_attempt_id == "yt-ingress-attempt-1"
    assert artifact.generation_state == "generated"
    assert artifact.model_route == "nana-public"
    assert "Ba" not in artifact.text and "con " not in artifact.text.lower()
    assert not hasattr(artifact, "delivery_state")
    assert not hasattr(artifact, "delivery_attempt_id")

    assert len(caller.calls) == 1
    call = caller.calls[0]
    assert call["model_name"] == "nana-public"
    assert call["messages"][-1] == {"role": "user", "content": source}
    prompt = str(call["messages"])
    assert "Minh" in prompt
    assert "youtube:yt-channel-1" not in prompt
    assert "yt-event-1" not in prompt
    assert "private owner" not in prompt.lower()


def test_pipeline_connects_youtube_cum1_to_generated_artifact() -> None:
    from datetime import datetime, timezone

    from nana.runtime.stream_cum0_contract import StreamContractLedger
    from nana.runtime.stream_cum2_response import PublicResponseGenerator, YouTubeCum2Pipeline
    from nana.runtime.stream_state import StreamState
    from nana.runtime.youtube_chat_cum1 import YouTubeChatCum1, YouTubeLiveSession
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress

    published = (
        datetime.fromtimestamp(NOW - 1, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    response = {
        "items": [{
            "id": "yt-event-pipeline-1",
            "snippet": {
                "type": "textMessageEvent",
                "liveChatId": "yt-live-chat-1",
                "authorChannelId": "yt-channel-1",
                "publishedAt": published,
                "displayMessage": "Yumi oi, ke chuyen gi vui di",
            },
            "authorDetails": {"displayName": "Minh"},
        }],
        "nextPageToken": "cursor-2",
        "pollingIntervalMillis": 1000,
    }
    policy_source = _PolicySource(_policy(StreamState.LIVE_ACTIVE))
    cum1 = YouTubeChatCum1(
        session=YouTubeLiveSession("yt-live-chat-1", "yt-session-1"),
        policy_source=policy_source,
        ingress=YouTubeChatIngress(actionable_limit=1),
        ledger=StreamContractLedger(),
    )
    caller = _Caller("Mình đang nghe đây, để mình kể một chuyện vui nha.")
    pipeline = YouTubeCum2Pipeline(
        ingress=cum1,
        generator=PublicResponseGenerator(caller=caller),
    )

    with patch.dict(
        os.environ,
        {
            "NANA_STREAM_CUM0_ENABLED": "1",
            "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "1",
            "NANA_STREAM_CUM2_RESPONSE_ENABLED": "1",
        },
        clear=False,
    ):
        result = pipeline.ingest_and_generate(
            response,
            output_id="yt-output-pipeline-1",
            received_at=NOW,
            now=NOW,
        )

    assert result.status == "generated", result
    assert result.ingress.status == "accepted"
    assert result.generation is not None
    assert result.generation.artifact is not None
    assert result.generation.artifact.event_id == "yt-event-pipeline-1"
    assert result.generation.artifact.text == "Mình đang nghe đây, để mình kể một chuyện vui nha."
    assert caller.calls[0]["messages"][-1]["content"] == "Yumi oi, ke chuyen gi vui di"
    assert "PUBLIC ROOM CONTEXT:" in caller.calls[0]["messages"][0]["content"]


def test_source_text_mismatch_is_rejected_before_provider() -> None:
    from nana.runtime.stream_cum2_response import PublicResponseGenerator

    caller = _Caller()
    generator = PublicResponseGenerator(caller=caller)
    with patch.dict(os.environ, {"NANA_STREAM_CUM2_RESPONSE_ENABLED": "1"}, clear=False):
        result = generator.generate(
            _turn("Tin nhan goc"),
            source_text="Tin nhan da bi thay",
            output_id="yt-output-2",
            now=NOW,
        )

    assert result.status == "rejected", result
    assert result.reason_code == "source_text_mismatch", result
    assert result.artifact is None
    assert caller.calls == []


def test_provider_error_returns_typed_failure_without_artifact() -> None:
    from nana.runtime.stream_cum2_response import PublicResponseGenerator

    caller = _Caller(raises=True)
    generator = PublicResponseGenerator(caller=caller)
    with patch.dict(os.environ, {"NANA_STREAM_CUM2_RESPONSE_ENABLED": "1"}, clear=False):
        result = generator.generate(
            _turn("Nana oi"),
            source_text="Nana oi",
            output_id="yt-output-3",
            now=NOW,
        )

    assert result.status == "failed", result
    assert result.reason_code == "provider_error", result
    assert result.artifact is None
    assert len(caller.calls) == 1


def main() -> None:
    tests = (
        test_flag_off_never_calls_provider,
        test_public_turn_generates_correlated_public_artifact,
        test_pipeline_connects_youtube_cum1_to_generated_artifact,
        test_source_text_mismatch_is_rejected_before_provider,
        test_provider_error_returns_typed_failure_without_artifact,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"smoke_stream_cum2_response: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
