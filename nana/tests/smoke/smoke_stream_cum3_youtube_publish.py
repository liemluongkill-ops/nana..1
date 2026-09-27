"""Small offline smoke for STREAM V1 CUM 3 YouTube text publishing."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import requests


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


NOW = 1_700_000_300.0


def _artifact(*, text: str = "Mình đang nghe đây, bạn kể tiếp đi.", output_id: str = "yt-output-1"):
    from nana.runtime.public_context_boundary import PublicEventScope
    from nana.runtime.public_identity import CanonicalPublicIdentity
    from nana.runtime.stream_cum0_contract import compute_correlation_id
    from nana.runtime.stream_cum2_response import ResponseArtifact

    identity = CanonicalPublicIdentity("youtube", "yt-channel-1", "youtube:yt-channel-1")
    scope = PublicEventScope(
        "youtube",
        "yt-live-chat-1",
        "yt-session-1",
        "yt-event-1",
        "Minh",
        identity,
    )
    return ResponseArtifact(
        scope=scope,
        correlation_id=compute_correlation_id(scope),
        source_revision=0,
        source_attempt_id="yt-ingress-1",
        output_id=output_id,
        text=text,
        model_route="nana-public",
        generated_at=NOW - 1,
    )


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[dict] = []

    def post(self, url, *, params, headers, json, timeout):
        self.calls.append({
            "url": url,
            "params": params,
            "headers": headers,
            "json": json,
            "timeout": timeout,
        })
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class _TokenProvider:
    def __init__(self, token: str | None = "oauth-token-for-smoke"):
        self.token = token
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.token


def _publisher(session, token_provider=None):
    from nana.runtime.stream_cum3_youtube_publish import (
        YouTubeCum3Publisher,
        YouTubeLiveChatSender,
    )

    token_provider = token_provider or _TokenProvider()
    sender = YouTubeLiveChatSender(
        token_provider=token_provider,
        session=session,
        timeout_seconds=5,
    )
    return YouTubeCum3Publisher(sender=sender, min_send_interval_seconds=0), token_provider


def test_flag_off_does_not_fetch_oauth_or_send() -> None:
    session = _Session(_Response(200, {"id": "yt-message-1"}))
    token_provider = _TokenProvider()
    publisher, _ = _publisher(session, token_provider)

    with patch.dict(os.environ, {"NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "0"}, clear=False):
        result = publisher.publish(
            _artifact(),
            delivery_attempt_id="yt-delivery-1",
            now=NOW,
        )

    assert result.status == "disabled", result
    assert result.reason_code == "cum3_disabled", result
    assert result.delivery_record is None
    assert token_provider.calls == 0
    assert session.calls == []


def test_default_sender_uses_cached_oauth_provider() -> None:
    from nana.runtime.stream_cum3_youtube_publish import YouTubeLiveChatSender

    session = _Session(_Response(200, {"id": "yt-message-from-cache"}))
    with patch.dict(
        os.environ,
        {"NANA_YOUTUBE_OAUTH_TOKEN": "", "YOUTUBE_OAUTH_TOKEN": ""},
        clear=False,
    ), patch(
        "nana.runtime.youtube_oauth.get_youtube_oauth_token",
        return_value="cached-access-token",
    ):
        result = YouTubeLiveChatSender(session=session).send_text(
            "yt-live-chat-1",
            "Mình đang nghe đây.",
        )

    assert result.outcome == "published", result
    assert result.provider_message_id == "yt-message-from-cache"
    assert len(session.calls) == 1
    assert session.calls[0]["headers"]["Authorization"] == "Bearer cached-access-token"


def test_confirmed_youtube_ack_publishes_exactly_once() -> None:
    session = _Session(_Response(200, {"id": "youtube-live-message-99"}))
    publisher, token_provider = _publisher(session)
    artifact = _artifact()

    with patch.dict(os.environ, {"NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "1"}, clear=False):
        first = publisher.publish(
            artifact,
            delivery_attempt_id="yt-delivery-1",
            now=NOW,
        )
        duplicate = publisher.publish(
            artifact,
            delivery_attempt_id="yt-delivery-1",
            now=NOW + 1,
        )

    assert first.status == "published", first
    assert first.reason_code == "youtube_acknowledged", first
    assert first.provider_message_id == "youtube-live-message-99"
    assert first.is_fresh_youtube_ack is True
    assert first.generated_record is not None
    assert first.generated_record.state == "generated"
    assert first.generated_record.revision == 0
    assert first.published_record is first.delivery_record
    assert first.delivery_record is not None
    assert first.delivery_record.state == "published"
    assert first.delivery_record.revision == 1
    assert first.delivery_record.event_id == artifact.event_id
    assert first.delivery_record.output_id == artifact.output_id
    assert first.delivery_record.attempt_id == "yt-delivery-1"
    assert first.delivery_record.state != "delivered"

    assert duplicate.status == "duplicate", duplicate
    assert duplicate.reason_code == "already_published", duplicate
    assert duplicate.is_fresh_youtube_ack is False
    assert duplicate.generated_record == first.generated_record
    assert duplicate.published_record == first.published_record
    assert duplicate.delivery_record == first.delivery_record
    assert len(session.calls) == 1
    assert token_provider.calls == 1
    call = session.calls[0]
    assert call["url"].endswith("/youtube/v3/liveChat/messages")
    assert call["params"] == {"part": "snippet"}
    assert call["headers"] == {
        "Authorization": "Bearer oauth-token-for-smoke",
        "Content-Type": "application/json",
    }
    assert call["json"] == {
        "snippet": {
            "liveChatId": "yt-live-chat-1",
            "type": "textMessageEvent",
            "textMessageDetails": {"messageText": artifact.text},
        }
    }


def test_timeout_is_unknown_and_same_attempt_is_not_retried() -> None:
    session = _Session(requests.Timeout("response lost"))
    publisher, token_provider = _publisher(session)
    artifact = _artifact(output_id="yt-output-timeout")

    with patch.dict(os.environ, {"NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "1"}, clear=False):
        first = publisher.publish(
            artifact,
            delivery_attempt_id="yt-delivery-timeout",
            now=NOW,
        )
        second = publisher.publish(
            artifact,
            delivery_attempt_id="yt-delivery-timeout",
            now=NOW + 10,
        )

    assert first.status == "held", first
    assert first.reason_code == "unknown_outcome", first
    assert first.is_fresh_youtube_ack is False
    assert first.generated_record is not None
    assert first.generated_record.state == "generated"
    assert first.published_record is None
    assert first.delivery_record is not None
    assert first.delivery_record.state == "generated"
    assert second.status == "held", second
    assert second.reason_code == "unknown_outcome", second
    assert len(session.calls) == 1
    assert token_provider.calls == 1


def test_pipeline_connects_cum2_result_to_youtube_publisher() -> None:
    from nana.runtime.stream_cum3_youtube_publish import YouTubeCum3Pipeline

    artifact = _artifact(output_id="yt-output-pipeline")

    class _Cum2Pipeline:
        def __init__(self):
            self.calls = []

        def ingest_and_generate(self, response, **kwargs):
            self.calls.append({"response": response, **kwargs})
            return SimpleNamespace(
                status="generated",
                reason_code="generated",
                generation=SimpleNamespace(artifact=artifact),
            )

    cum2 = _Cum2Pipeline()
    session = _Session(_Response(200, {"id": "youtube-live-message-pipeline"}))
    publisher, _ = _publisher(session)
    pipeline = YouTubeCum3Pipeline(response_pipeline=cum2, publisher=publisher)

    with patch.dict(os.environ, {"NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "1"}, clear=False):
        result = pipeline.ingest_generate_publish(
            {"items": []},
            output_id="yt-output-pipeline",
            delivery_attempt_id="yt-delivery-pipeline",
            received_at=NOW,
            now=NOW,
        )

    assert result.status == "published", result
    assert result.upstream.status == "generated"
    assert result.publish is not None
    assert result.publish.delivery_record is not None
    assert result.publish.delivery_record.state == "published"
    assert len(cum2.calls) == 1
    assert len(session.calls) == 1


def test_unsafe_or_definitely_rejected_text_never_becomes_published() -> None:
    unsafe_session = _Session(_Response(200, {"id": "must-not-send"}))
    unsafe_publisher, _ = _publisher(unsafe_session)
    rejected_session = _Session(
        _Response(403, {"error": {"errors": [{"reason": "forbidden"}]}})
    )
    rejected_publisher, _ = _publisher(rejected_session)

    with patch.dict(os.environ, {"NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "1"}, clear=False):
        unsafe = unsafe_publisher.publish(
            _artifact(text="Ba ơi, con đang nghe đây.", output_id="yt-output-unsafe"),
            delivery_attempt_id="yt-delivery-unsafe",
            now=NOW,
        )
        rejected = rejected_publisher.publish(
            _artifact(output_id="yt-output-forbidden"),
            delivery_attempt_id="yt-delivery-forbidden",
            now=NOW,
        )

    assert unsafe.status == "rejected", unsafe
    assert unsafe.reason_code == "unsafe_public_text", unsafe
    assert unsafe.delivery_record is None
    assert unsafe_session.calls == []

    assert rejected.status == "failed", rejected
    assert rejected.reason_code == "forbidden", rejected
    assert rejected.is_fresh_youtube_ack is False
    assert rejected.generated_record is not None
    assert rejected.generated_record.state == "generated"
    assert rejected.published_record is None
    assert rejected.delivery_record is not None
    assert rejected.delivery_record.state == "interrupted"
    assert rejected.delivery_record.state != "published"
    assert len(rejected_session.calls) == 1


def main() -> None:
    tests = (
        test_flag_off_does_not_fetch_oauth_or_send,
        test_default_sender_uses_cached_oauth_provider,
        test_confirmed_youtube_ack_publishes_exactly_once,
        test_timeout_is_unknown_and_same_attempt_is_not_retried,
        test_pipeline_connects_cum2_result_to_youtube_publisher,
        test_unsafe_or_definitely_rejected_text_never_becomes_published,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"smoke_stream_cum3_youtube_publish: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
