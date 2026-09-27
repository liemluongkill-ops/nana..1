"""Small offline smoke for YouTube chat -> CUM0 -> PublicTurn wiring.

The smoke uses the real normalization, CUM0 ledger, and public-turn projection.
It never opens a network connection or calls Nana, a model, memory, voice, or
an output sink.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


NOW = 1_700_000_100.0
CREATED = NOW - 1.0


def _message(
    *,
    message_id: str | None = "yt-message-1",
    author_channel_id: str | None = "yt-channel-viewer-1",
    published_at: float | None = CREATED,
    text: str = "Nana oi, hom nay khoe khong?",
) -> dict:
    snippet = {
        "type": "textMessageEvent",
        "liveChatId": "yt-live-chat-1",
        "displayMessage": text,
    }
    if author_channel_id is not None:
        snippet["authorChannelId"] = author_channel_id
    if published_at is not None:
        snippet["publishedAt"] = (
            datetime.fromtimestamp(published_at, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    result = {
        "snippet": snippet,
        "authorDetails": {"displayName": "Minh"},
    }
    if message_id is not None:
        result["id"] = message_id
    return result


def _response(message: dict) -> dict:
    return {
        "items": [message],
        "nextPageToken": "cursor-next",
        "pollingIntervalMillis": 1000,
    }


def _policy(state, *, can_reply: bool = True):
    from nana.runtime.stream_state import (
        AvatarEnergy,
        ErrorType,
        InteractionTone,
        StreamPolicy,
        ViewerExpectation,
    )

    return StreamPolicy(
        state=state,
        reason="cum1-smoke",
        can_proactive=False,
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=0,
        interaction_tone=InteractionTone.QUIET,
        avatar_energy=AvatarEnergy.LOW,
        viewer_expectation=ViewerExpectation.MUTED,
        error_type=ErrorType.NONE,
        can_reply=can_reply,
        can_speak=False,
        can_avatar=False,
    )


class _PolicySource:
    def __init__(self, policy) -> None:
        self.policy = policy
        self.calls = 0

    def get_policy(self):
        self.calls += 1
        return self.policy


def _bridge(policy_source, *, ingress=None, ledger=None):
    from nana.runtime.youtube_chat_cum1 import YouTubeChatCum1, YouTubeLiveSession

    return YouTubeChatCum1(
        session=YouTubeLiveSession(
            live_chat_id="yt-live-chat-1",
            stream_session_id="yt-video-session-1",
        ),
        policy_source=policy_source,
        ingress=ingress,
        ledger=ledger,
    )


def test_cum1_off_does_not_ingest_or_read_policy() -> None:
    from nana.runtime.stream_state import StreamState
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress

    ingress = YouTubeChatIngress()
    policy_source = _PolicySource(_policy(StreamState.LIVE_ACTIVE))
    bridge = _bridge(policy_source, ingress=ingress)

    with patch.dict(
        os.environ,
        {
            "NANA_STREAM_CUM0_ENABLED": "1",
            "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "0",
        },
        clear=False,
    ):
        result = bridge.ingest_response(_response(_message()), received_at=NOW, now=NOW)

    assert result.status == "disabled", result
    assert result.reason_code == "cum1_disabled", result
    assert result.ingest_result is None
    assert result.admission is None
    assert ingress.snapshot()["recent_count"] == 0
    assert policy_source.calls == 0


def test_real_normalized_youtube_message_reaches_public_turn() -> None:
    from nana.runtime.stream_state import StreamState

    policy_source = _PolicySource(_policy(StreamState.LIVE_ACTIVE))
    bridge = _bridge(policy_source)

    with patch.dict(
        os.environ,
        {
            "NANA_STREAM_CUM0_ENABLED": "1",
            "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "1",
        },
        clear=False,
    ):
        result = bridge.ingest_response(_response(_message()), received_at=NOW, now=NOW)

    assert result.status == "accepted", result
    assert result.reason_code == "admitted", result
    assert result.ingest_result is not None and result.ingest_result.new_count == 1
    assert result.public_turn is not None
    assert result.public_turn.scope.platform == "youtube"
    assert result.public_turn.scope.room_id == "yt-live-chat-1"
    assert result.public_turn.scope.stream_session_id == "yt-video-session-1"
    assert result.public_turn.scope.event_id == "yt-message-1"
    assert result.public_turn.scope.identity.author_id == "yt-channel-viewer-1"
    assert result.public_turn.scope.identity.actor_key == "youtube:yt-channel-viewer-1"
    assert result.public_turn.message_preview == "Nana oi, hom nay khoe khong?"
    assert policy_source.calls == 1


def test_missing_provider_fields_are_rejected_without_fallback_identity() -> None:
    from nana.runtime.stream_state import StreamState

    cases = (
        (_message(message_id=None), "provider_event_id_unverified"),
        (_message(author_channel_id=None), "missing_actor_id"),
        (_message(published_at=None), "missing_published_at"),
    )
    for message, expected_reason in cases:
        policy_source = _PolicySource(_policy(StreamState.LIVE_ACTIVE))
        bridge = _bridge(policy_source)
        with patch.dict(
            os.environ,
            {
                "NANA_STREAM_CUM0_ENABLED": "1",
                "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "1",
            },
            clear=False,
        ):
            result = bridge.ingest_response(_response(message), received_at=NOW, now=NOW)
        assert result.status == "rejected", (expected_reason, result)
        assert result.reason_code == expected_reason, result
        assert result.public_turn is None
        assert policy_source.calls == 0


def test_non_live_policy_cannot_create_public_turn() -> None:
    from nana.runtime.stream_state import StreamState

    policy_source = _PolicySource(_policy(StreamState.OFFLINE))
    bridge = _bridge(policy_source)
    with patch.dict(
        os.environ,
        {
            "NANA_STREAM_CUM0_ENABLED": "1",
            "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "1",
        },
        clear=False,
    ):
        result = bridge.ingest_response(_response(_message()), received_at=NOW, now=NOW)

    assert result.status == "rejected", result
    assert result.reason_code == "state_not_live", result
    assert result.public_turn is None


def test_same_provider_event_is_idempotent_across_receiver_reconnect() -> None:
    from nana.runtime.stream_cum0_contract import StreamContractLedger
    from nana.runtime.stream_state import StreamState

    ledger = StreamContractLedger()
    policy_source = _PolicySource(_policy(StreamState.LIVE_ACTIVE))
    first_receiver = _bridge(policy_source, ledger=ledger)
    reconnected_receiver = _bridge(policy_source, ledger=ledger)

    with patch.dict(
        os.environ,
        {
            "NANA_STREAM_CUM0_ENABLED": "1",
            "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "1",
        },
        clear=False,
    ):
        first = first_receiver.ingest_response(_response(_message()), received_at=NOW, now=NOW)
        duplicate = reconnected_receiver.ingest_response(
            _response(_message()),
            received_at=NOW,
            now=NOW,
        )

    assert first.status == "accepted", first
    assert first.public_turn is not None
    assert duplicate.status == "duplicate", duplicate
    assert duplicate.reason_code == "idempotent_duplicate", duplicate
    assert duplicate.public_turn is None
    assert ledger.snapshot()["entries"] == 1


def main() -> None:
    tests = (
        test_cum1_off_does_not_ingest_or_read_policy,
        test_real_normalized_youtube_message_reaches_public_turn,
        test_missing_provider_fields_are_rejected_without_fallback_identity,
        test_non_live_policy_cannot_create_public_turn,
        test_same_provider_event_is_idempotent_across_receiver_reconnect,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"smoke_youtube_chat_cum1: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
