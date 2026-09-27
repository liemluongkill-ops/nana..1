"""Offline smoke tests for the explicit YouTube chat transport/probe.

No network connection is made.  All transports and responses are fakes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _message(message_id: str, name: str, text: str, second: int = 0) -> dict:
    published = datetime.fromtimestamp(1_700_000_000 + second, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "id": message_id,
        "snippet": {
            "type": "textMessageEvent",
            "liveChatId": "chat-1",
            "authorChannelId": f"channel-{name}",
            "publishedAt": published,
            "displayMessage": text,
        },
        "authorDetails": {"displayName": name},
    }


class _Response:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Session:
    def __init__(self):
        self.calls = []
        self.responses = [
            _Response({"items": [{"liveStreamingDetails": {"activeLiveChatId": "chat-1"}}]}),
            _Response({"items": [_message("m1", "a", "hello")], "nextPageToken": "c1", "pollingIntervalMillis": 500}),
        ]

    def get(self, url, *, params, headers, timeout):
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return self.responses.pop(0)


def test_rest_transport_builds_safe_requests() -> None:
    from nana.runtime.youtube_chat_transport import YouTubeCredentials, YouTubeRestChatTransport

    session = _Session()
    transport = YouTubeRestChatTransport(
        YouTubeCredentials(api_key="secret-api-key"),
        session=session,
    )
    assert transport.resolve_live_chat_id("video-1") == "chat-1"
    response = transport.list_messages("chat-1", page_token="cursor-1")
    assert response["nextPageToken"] == "c1"
    assert session.calls[0]["params"]["id"] == "video-1"
    assert session.calls[1]["params"]["pageToken"] == "cursor-1"
    assert session.calls[0]["headers"] == {"X-Goog-Api-Key": "secret-api-key"}


def test_environment_credentials_skip_blank_alias() -> None:
    from nana.runtime.youtube_chat_transport import YouTubeCredentials

    credentials = YouTubeCredentials.from_environment(
        {
            "NANA_YOUTUBE_API_KEY": "  ",
            "YOUTUBE_API_KEY": "fallback-key",
        }
    )
    assert credentials.api_key == "fallback-key", credentials


def test_grpc_shape_normalizes_enum_and_nested_text() -> None:
    from nana.runtime.youtube_chat_ingress import normalize_youtube_chat_message
    from nana.runtime.youtube_stream_list_proto import LiveChatMessage

    raw = LiveChatMessage(
        id="grpc-1",
        snippet={
            "type": 1,
            "live_chat_id": "chat-1",
            "author_channel_id": "channel-a",
            "published_at": "2023-11-14T22:13:20Z",
            "text_message_details": {"message_text": "from grpc"},
        },
        author_details={"display_name": "alice"},
    )
    event = normalize_youtube_chat_message(raw, received_at=1_700_000_001)
    assert event.event_type == "text_message_event", event
    assert event.viewer_name == "alice", event
    assert event.text == "from grpc", event


def test_grpc_response_round_trip_keeps_items_and_cursor() -> None:
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress
    from nana.runtime.youtube_stream_list_proto import LiveChatMessageListResponse

    response = LiveChatMessageListResponse(
        next_page_token="cursor-2",
        items=[
            {
                "id": "grpc-2",
                "snippet": {
                    "type": 1,
                    "live_chat_id": "chat-1",
                    "published_at": "2023-11-14T22:13:20Z",
                    "display_message": "round trip",
                },
                "author_details": {"display_name": "bob"},
            }
        ],
    )
    decoded = LiveChatMessageListResponse.FromString(response.SerializeToString())
    ingress = YouTubeChatIngress()
    result = ingress.ingest_response(decoded, received_at=1_700_000_001)
    assert result.new_count == 1, result
    assert result.new_events[0].text == "round trip", result
    assert ingress.next_page_token == "cursor-2"


def test_grpc_transport_builds_stream_request_without_socket() -> None:
    from nana.runtime.youtube_chat_transport import YouTubeCredentials, YouTubeStreamListTransport
    from nana.runtime.youtube_stream_list_proto import LiveChatMessageListResponse

    seen = {}
    response = LiveChatMessageListResponse(next_page_token="next")

    class FakeChannel:
        def unary_stream(self, path, *, request_serializer, response_deserializer):
            seen["path"] = path

            def invoke(request, metadata):
                seen["request"] = request
                seen["metadata"] = metadata
                return iter([response])

            return invoke

        def close(self):
            seen["closed"] = True

    transport = YouTubeStreamListTransport(
        YouTubeCredentials(api_key="key"),
        channel_factory=lambda *_args: FakeChannel(),
    )
    responses = list(transport.iter_responses("chat-1", page_token="cursor-1"))
    assert responses == [response]
    assert seen["path"].endswith("/StreamList"), seen
    assert seen["request"].live_chat_id == "chat-1", seen
    assert list(seen["request"].part) == ["snippet", "authorDetails"], seen
    assert seen["request"].page_token == "cursor-1", seen
    assert seen["metadata"] == (("x-goog-api-key", "key"),), seen
    assert seen["closed"] is True, seen


def test_receive_only_probe_keeps_history_separate() -> None:
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress
    from nana.runtime.youtube_chat_probe import run_probe
    from nana.runtime.youtube_chat_transport import LiveChatProbeConfig, YouTubeCredentials

    class FakeRest:
        def resolve_live_chat_id(self, video_id):
            assert video_id == "video-1"
            return "chat-1"

        def list_messages(self, live_chat_id, *, page_token=None):
            if page_token is None:
                return {"items": [_message("m1", "a", "history")], "nextPageToken": "c1", "pollingIntervalMillis": 1}
            return {"items": [_message("m1", "a", "history"), _message("m2", "b", "new", 1)], "nextPageToken": "c2", "pollingIntervalMillis": 1}

    output = []
    ticks = iter([0.0, 0.0, 1.0, 1.0, 2.0])
    summary = run_probe(
        LiveChatProbeConfig(video_id="video-1", transport="rest", duration_seconds=60, max_responses=2),
        YouTubeCredentials(api_key="key"),
        ingress=YouTubeChatIngress(),
        rest_transport=FakeRest(),
        output=output.append,
        sleep=lambda _seconds: None,
        clock=lambda: next(ticks, 2.0),
    )
    records = [json.loads(line) for line in output]
    events = [record for record in records if record.get("kind") == "viewer_event"]
    assert summary["history_events"] == 1, summary
    assert summary["new_events"] == 1, summary
    assert summary["duplicate_events"] == 1, summary
    assert len(events) == 1 and events[0]["event"]["message_id"] == "m2", records
    assert summary["context"]["recent_count"] == 2, summary


def test_stream_probe_uses_cursor_on_reconnect() -> None:
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress
    from nana.runtime.youtube_chat_probe import run_probe
    from nana.runtime.youtube_chat_transport import LiveChatProbeConfig, YouTubeCredentials

    class FakeRest:
        def resolve_live_chat_id(self, video_id):
            return "chat-1"

    class FakeStream:
        def __init__(self):
            self.tokens = []

        def iter_responses(self, live_chat_id, *, page_token=None):
            self.tokens.append(page_token)
            if page_token is None:
                yield {"items": [_message("m1", "a", "history")], "nextPageToken": "c1"}
            else:
                yield {"items": [_message("m2", "b", "new", 1)], "nextPageToken": "c2"}

    stream = FakeStream()
    ticks = iter([0.0, 0.0, 1.0, 1.0, 2.0, 2.0])
    summary = run_probe(
        LiveChatProbeConfig(video_id="video-1", transport="stream", duration_seconds=60, max_responses=2),
        YouTubeCredentials(api_key="key"),
        ingress=YouTubeChatIngress(),
        rest_transport=FakeRest(),
        stream_transport=stream,
        output=lambda _line: None,
        sleep=lambda _seconds: None,
        clock=lambda: next(ticks, 2.0),
    )
    assert stream.tokens[:2] == [None, "c1"], stream.tokens
    assert summary["history_events"] == 1, summary
    assert summary["new_events"] == 1, summary


def main() -> None:
    tests = [
        test_rest_transport_builds_safe_requests,
        test_grpc_shape_normalizes_enum_and_nested_text,
        test_grpc_response_round_trip_keeps_items_and_cursor,
        test_grpc_transport_builds_stream_request_without_socket,
        test_receive_only_probe_keeps_history_separate,
        test_stream_probe_uses_cursor_on_reconnect,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"smoke_youtube_chat_transport: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
