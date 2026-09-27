"""Focused offline smoke for the Stream V1 CUM4 text host."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NOW = 1_700_000_500.0
FLAGS = {
    "NANA_STREAM_CUM0_ENABLED": "1",
    "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED": "1",
    "NANA_STREAM_CUM2_RESPONSE_ENABLED": "1",
    "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "1",
    "NANA_STREAM_CUM4_HOST_ENABLED": "1",
}


def _message(index: int, text: str) -> dict:
    published = datetime.fromtimestamp(NOW + index, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "id": f"yt-cum4-event-{index}",
        "snippet": {
            "type": "textMessageEvent",
            "liveChatId": "yt-cum4-room",
            "authorChannelId": f"yt-viewer-{index}",
            "publishedAt": published,
            "displayMessage": text,
        },
        "authorDetails": {"displayName": f"Viewer {index}"},
    }


def _policy():
    from nana.runtime.stream_state import (
        AvatarEnergy, ErrorType, InteractionTone, StreamPolicy, StreamState, ViewerExpectation,
    )
    return StreamPolicy(
        state=StreamState.LIVE_ACTIVE, reason="cum4-smoke", can_proactive=False,
        can_auto_send=False, can_use_private_memory=False, proactive_budget=0,
        interaction_tone=InteractionTone.QUIET, avatar_energy=AvatarEnergy.LOW,
        viewer_expectation=ViewerExpectation.MUTED, error_type=ErrorType.NONE,
        can_reply=True, can_speak=False, can_avatar=False,
    )


class _PolicySource:
    def get_policy(self):
        return _policy()


class _Social:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = []

    def observe(self, **kwargs):
        from nana.runtime.social_session import SocialDecision
        self.calls.append(kwargs)
        action = self.actions.pop(0)
        return SocialDecision(
            action=action,
            reason="smoke_" + action,
            event_type="text",
            chat_velocity=0.0,
            priority_score=2,
            should_call_llm=action == "full_reply",
            reply_text="Mình thấy rồi nha." if action == "ack_only" else "",
        )


class _Model:
    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return "Mình trả lời câu này nha.", "ok"


class _Sender:
    def __init__(self, outcome="published"):
        self.outcome = outcome
        self.calls = []

    def send_text(self, live_chat_id, text):
        from nana.runtime.stream_cum3_youtube_publish import YouTubeSendResult
        self.calls.append((live_chat_id, text))
        if self.outcome == "unknown":
            return YouTubeSendResult("unknown", "transport_timeout")
        return YouTubeSendResult("published", "youtube_acknowledged", 200, f"yt-output-{len(self.calls)}")


class _RaisingPublisher:
    def __init__(self):
        self.calls = []

    def publish(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("generation_must_not_publish")


_UNSET = object()


def _host(actions, *, sender=None, publisher=_UNSET, model=None, max_queue=16, sleep=None):
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.stream_cum0_contract import StreamContractLedger
    from nana.runtime.stream_cum2_response import PublicResponseGenerator
    from nana.runtime.stream_cum3_youtube_publish import YouTubeCum3Publisher
    from nana.runtime.stream_cum4_host import YouTubeTextHost
    from nana.runtime.youtube_chat_cum1 import YouTubeChatCum1, YouTubeLiveSession
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress

    model = model or _Model()
    sender = sender or _Sender()
    if publisher is _UNSET:
        publisher = YouTubeCum3Publisher(sender=sender, min_send_interval_seconds=0)
    cum1 = YouTubeChatCum1(
        session=YouTubeLiveSession("yt-cum4-room", "yt-cum4-session"),
        policy_source=_PolicySource(),
        ingress=YouTubeChatIngress(actionable_limit=1),
        ledger=StreamContractLedger(),
    )
    host = YouTubeTextHost(
        cum1=cum1,
        generator=PublicResponseGenerator(caller=model, session_context=SocialSessionCache()),
        publisher=publisher,
        social_session=_Social(actions),
        max_queue=max_queue,
        min_publish_interval_seconds=0,
    )
    return host, model, sender


def test_host_off_does_not_admit_or_generate() -> None:
    from nana.runtime.stream_cum4_host import YouTubeTextHost
    host, model, sender = _host(["full_reply"])
    with patch.dict(os.environ, {**FLAGS, "NANA_STREAM_CUM4_HOST_ENABLED": "0"}, clear=False):
        result = host.handle_response({"items": [_message(0, "Yumi oi")]}, received_at=NOW, now=NOW)
    assert result.status == "disabled"
    assert host.snapshot()["queued"] == 0
    assert model.calls == [] and sender.calls == []


def test_social_skip_ack_and_full_reply_route_correctly() -> None:
    host, model, sender = _host(["skip", "ack_only", "full_reply"])
    with patch.dict(os.environ, FLAGS, clear=False):
        admitted = host.ingest_response(
            {"items": [_message(0, "noise"), _message(1, "hmm"), _message(2, "Yumi oi?")]},
            received_at=NOW + 2,
            now=NOW + 2,
        )
        ack = host.process_next(now=NOW + 2)
        full = host.process_next(now=NOW + 3)
    assert admitted.skipped == 1 and admitted.queued == 2
    assert ack.status == "published" and ack.action == "ack_only"
    assert full.status == "published" and full.action == "full_reply"
    assert full.publish_result is not None
    assert full.publish_result.is_fresh_youtube_ack is True
    assert full.publish_result.generated_record is not None
    assert full.publish_result.generated_record.state == "generated"
    assert full.publish_result.published_record is full.publish_result.delivery_record
    assert full.publish_result.published_record is not None
    assert full.publish_result.published_record.state == "published"
    assert full.publish_result.generated_record.key == full.publish_result.published_record.key
    assert len(model.calls) == 1
    assert [text for _, text in sender.calls] == ["Mình thấy rồi nha.", "Mình trả lời câu này nha."]


def test_generate_next_returns_artifact_without_publishing() -> None:
    publisher = _RaisingPublisher()
    host, model, sender = _host(["full_reply"], publisher=publisher)
    flags = {**FLAGS, "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "0"}

    with patch.dict(os.environ, flags, clear=False):
        host.ingest_response(
            {"items": [_message(0, "Yumi oi?")]},
            received_at=NOW,
            now=NOW,
        )
        result = host.generate_next(now=NOW)

    assert result.status == "generated"
    assert result.reason_code == "generated"
    assert result.response_artifact is not None
    assert result.response_artifact.output_id.startswith("cum4-output-")
    assert result.published == 0 and result.publish_result is None
    assert len(model.calls) == 1
    assert publisher.calls == [] and sender.calls == []


def test_generate_next_discards_stale_queued_event_before_model() -> None:
    publisher = _RaisingPublisher()
    host, model, sender = _host(["full_reply"], publisher=publisher)
    flags = {**FLAGS, "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED": "0"}

    with patch.dict(os.environ, flags, clear=False):
        host.ingest_response(
            {"items": [_message(0, "old question?")]},
            received_at=NOW,
            now=NOW,
        )
        result = host.generate_next(now=NOW + 30.001, max_job_age_seconds=30.0)

    assert result.status == "skipped"
    assert result.reason_code == "stale_queued_event"
    assert result.skipped == 1 and result.queued == 0
    assert host.snapshot()["queued"] == 0
    assert model.calls == []
    assert publisher.calls == [] and sender.calls == []


def test_missing_publisher_rejects_without_consuming_text_job() -> None:
    host, model, sender = _host(["full_reply"], publisher=None)
    with patch.dict(os.environ, FLAGS, clear=False):
        host.ingest_response(
            {"items": [_message(0, "Yumi oi?")]},
            received_at=NOW,
            now=NOW,
        )
        before = host.snapshot()["queued"]
        result = host.process_next(now=NOW)

    assert result.status == "rejected"
    assert result.reason_code == "publisher_unavailable"
    assert before == 1 and host.snapshot()["queued"] == 1
    assert model.calls == [] and sender.calls == []


def test_queue_bound_drops_extra_without_model_call() -> None:
    host, model, sender = _host(["full_reply", "full_reply"], max_queue=1)
    with patch.dict(os.environ, FLAGS, clear=False):
        result = host.ingest_response(
            {"items": [_message(0, "one?"), _message(1, "two?")]},
            received_at=NOW + 1,
            now=NOW + 1,
        )
    assert result.queued == 1 and result.dropped == 1
    assert model.calls == [] and sender.calls == []


def test_unknown_publish_halts_and_never_blind_retries() -> None:
    sender = _Sender("unknown")
    host, model, _ = _host(["full_reply", "full_reply"], sender=sender)
    with patch.dict(os.environ, FLAGS, clear=False):
        host.ingest_response({"items": [_message(0, "first?")]}, received_at=NOW, now=NOW)
        first = host.process_next(now=NOW)
        second = host.process_next(now=NOW + 1)
        reconciled = host.reconcile_unknown(confirmed_absent=True, now=NOW + 2)
        sender.outcome = "published"
        host.ingest_response({"items": [_message(1, "second?")]}, received_at=NOW + 3, now=NOW + 3)
        resumed = host.process_next(now=NOW + 3)
    assert first.status == "halted" and first.reason_code == "unknown_outcome"
    assert first.publish_result is not None
    assert first.publish_result.is_fresh_youtube_ack is False
    assert first.publish_result.generated_record is not None
    assert first.publish_result.published_record is None
    assert second.status == "halted"
    assert reconciled.status == "resumed" and reconciled.reason_code == "operator_confirmed_absent"
    assert resumed.status == "published"
    assert len(sender.calls) == 2 and len(model.calls) == 2
    assert host.snapshot()["halted"] is False


def test_published_provider_message_is_ignored_when_it_returns_from_chat() -> None:
    host, model, sender = _host(["full_reply"])
    with patch.dict(os.environ, FLAGS, clear=False):
        host.ingest_response({"items": [_message(0, "first?")]}, received_at=NOW, now=NOW)
        published = host.process_next(now=NOW)
        own_message = _message(1, "Mình trả lời câu này nha.")
        own_message["id"] = "yt-output-1"
        reflected = host.ingest_response(
            {"items": [own_message]},
            received_at=NOW + 1,
            now=NOW + 1,
        )

    assert published.status == "published"
    assert reflected.ignored_self == 1
    assert reflected.queued == 0
    assert len(model.calls) == 1
    assert len(sender.calls) == 1
    assert host.snapshot()["stats"]["self_ignored"] == 1


def test_run_loop_reconnects_uses_cursor_and_obeys_kill_switch() -> None:
    from nana.runtime.stream_cum4_host import run_text_host
    from nana.runtime.youtube_chat_transport import YouTubeChatTransportError
    host, model, sender = _host(["full_reply"])

    class Transport:
        def __init__(self):
            self.calls = []
        def list_messages(self, live_chat_id, *, page_token=None):
            self.calls.append(page_token)
            if len(self.calls) == 1:
                raise YouTubeChatTransportError("temporary")
            if len(self.calls) == 2:
                return {"items": [], "nextPageToken": "cursor-1", "pollingIntervalMillis": 1200}
            return {"items": [_message(0, "reply?")], "nextPageToken": "cursor-2", "pollingIntervalMillis": 1200}

    waits = []
    transport = Transport()
    ticks = iter([NOW, NOW + 1, NOW + 2, NOW + 3])
    with patch.dict(os.environ, FLAGS, clear=False):
        result = run_text_host(
            transport=transport,
            live_chat_id="yt-cum4-room",
            host=host,
            max_cycles=2,
            reconnect_limit=2,
            sleep=waits.append,
            clock=lambda: next(ticks, NOW + 3),
        )
    assert result.status == "completed"
    assert transport.calls == [None, None, "cursor-1"]
    assert waits[0] == 1.0 and waits[1] >= 1.2
    assert len(model.calls) == 1 and len(sender.calls) == 1


def main() -> None:
    tests = (
        test_host_off_does_not_admit_or_generate,
        test_social_skip_ack_and_full_reply_route_correctly,
        test_generate_next_returns_artifact_without_publishing,
        test_generate_next_discards_stale_queued_event_before_model,
        test_missing_publisher_rejects_without_consuming_text_job,
        test_queue_bound_drops_extra_without_model_call,
        test_unknown_publish_halts_and_never_blind_retries,
        test_published_provider_message_is_ignored_when_it_returns_from_chat,
        test_run_loop_reconnects_uses_cursor_and_obeys_kill_switch,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"smoke_stream_cum4_host: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
