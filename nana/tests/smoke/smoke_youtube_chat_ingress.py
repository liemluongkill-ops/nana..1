"""Offline smoke tests for the YouTube chat ingress/context contract.

No network, YouTube, LLM, TTS, OBS, VTS, or live runtime calls are allowed.
"""

from __future__ import annotations

from datetime import datetime, timezone
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _message(message_id: str, name: str, text: str, second: int) -> dict:
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
        "private_key": "must-not-be-retained",
    }


def test_bootstrap_is_context_only() -> None:
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress

    ingress = YouTubeChatIngress()
    result = ingress.ingest_response(
        {
            "items": [
                _message("m1", "a", "Xin chao Nana", 0),
                _message("m2", "b", "Nana oi", 1),
            ],
            "nextPageToken": "cursor-1",
            "pollingIntervalMillis": 1200,
        },
        received_at=1_700_000_002,
        bootstrap=True,
    )
    assert result.history_count == 2, result
    assert result.new_count == 0, result
    assert ingress.peek_actionable(now=1_700_000_002) is None
    assert ingress.next_page_token == "cursor-1"
    assert ingress.snapshot()["recent_count"] == 2


def test_new_events_dedupe_and_single_candidate() -> None:
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress

    ingress = YouTubeChatIngress(actionable_limit=1)
    ingress.ingest_response(
        {"items": [_message("m1", "a", "old", 0)]},
        received_at=1_700_000_001,
        bootstrap=True,
    )
    result = ingress.ingest_response(
        {
            "items": [
                _message("m1", "a", "old", 0),
                _message("m2", "b", "first", 2),
                _message("m3", "c", "second", 3),
            ]
        },
        received_at=1_700_000_004,
    )
    assert result.duplicate_count == 1, result
    assert result.new_count == 2, result
    assert [event.message_id for event in result.new_events] == ["m2", "m3"], result
    assert result.queued_actionable == 1, result
    assert result.dropped_actionable == 1, result
    assert ingress.peek_actionable(now=1_700_000_004).text == "first"


def test_context_is_bounded_and_timestamped() -> None:
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress

    ingress = YouTubeChatIngress(recent_limit=30, realtime_limit=10)
    history = [_message(f"h{i}", "viewer", f"history {i}", i) for i in range(35)]
    ingress.ingest_response({"items": history}, received_at=1_700_000_040, bootstrap=True)
    live = _message("live-1", "viewer", "new question", 39)
    result = ingress.ingest_response({"items": [live]}, received_at=1_700_000_040)
    context = ingress.build_context(recent_limit=30, realtime_limit=5)
    assert result.new_count == 1, result
    assert context["recent_count"] == 30, context
    assert context["realtime_count"] == 1, context
    assert context["realtime_messages"][0]["message_id"] == "live-1"
    assert 900.0 <= context["realtime_messages"][0]["delivery_ms"] <= 1100.0
    assert "private_key" not in context["realtime_messages"][0]


def test_cursor_reconnect_and_ttl() -> None:
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress

    ingress = YouTubeChatIngress(actionable_ttl_seconds=5)
    ingress.ingest_response(
        {"items": [_message("m1", "a", "hello", 0)], "nextPageToken": "c1"},
        received_at=1_700_000_001,
        bootstrap=True,
    )
    result = ingress.ingest_response(
        {"items": [_message("m1", "a", "hello", 0)], "nextPageToken": "c2"},
        received_at=1_700_000_002,
    )
    assert result.duplicate_count == 1, result
    assert ingress.next_page_token == "c2"
    assert ingress.pop_actionable(now=1_700_000_002) is None

    ingress.ingest_response(
        {"items": [_message("m3", "b", "fresh", 3)]},
        received_at=1_700_000_003,
    )
    assert ingress.peek_actionable(now=1_700_000_007) is not None
    assert ingress.peek_actionable(now=1_700_000_009) is None
    assert ingress.snapshot()["stats"]["actionable_expired"] == 1


def main() -> None:
    tests = [
        test_bootstrap_is_context_only,
        test_new_events_dedupe_and_single_candidate,
        test_context_is_bounded_and_timestamped,
        test_cursor_reconnect_and_ttl,
    ]
    passed = 0
    for test in tests:
        test()
        passed += 1
        print(f"PASS {test.__name__}")
    print(f"smoke_youtube_chat_ingress: {passed}/{len(tests)} passed")


if __name__ == "__main__":
    main()
