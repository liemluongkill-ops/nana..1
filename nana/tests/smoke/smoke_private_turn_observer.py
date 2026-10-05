"""Provider-free smoke for the private turn observer seam."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nana.runtime.private_turn_observer import (  # noqa: E402
    PrivateTurnIdentity,
    PrivateTurnObserver,
    PrivateTurnResult,
)
from smoke_history_voice_consistency import Voice, pipeline_fixture  # noqa: E402


EPOCH = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SESSION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
CLIENT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
TURN = "11111111-1111-4111-8111-111111111111"
CORRELATION = "22222222-2222-4222-8222-222222222222"


def identity() -> PrivateTurnIdentity:
    return PrivateTurnIdentity(EPOCH, SESSION, CLIENT, TURN, CORRELATION)


def test_indexed_delta_and_final_are_sanitized_and_ordered():
    events = []
    observer = PrivateTurnObserver(identity(), events.append)
    observer.on_thinking()
    observer.on_text_delta("Xin ")
    observer.on_text_delta("chao")
    observer.on_text_final("Xin chao")
    observer.on_text_final("Xin chao")
    assert [event.message_type for event in events] == [
        "turn.state", "assistant.delta", "assistant.delta", "assistant.final", "turn.state",
    ]
    assert [event.payload["delta_index"] for event in events[1:3]] == [1, 2]
    assert events[2].payload["text"] == "chao"
    assert events[3].payload["text"] == "Xin chao"
    assert observer.final_text == "Xin chao"


def test_voice_receipts_and_terminal_result_are_correlated():
    events = []
    observer = PrivateTurnObserver(identity(), events.append)
    observer.on_voice_queued(17)
    observer.on_voice_first_audio(17)
    observer.on_voice_complete(17)
    observer.on_terminal(PrivateTurnResult(False, "complete", "Xin chao", None, 17))
    assert [event.message_type for event in events] == [
        "voice.state", "voice.state", "voice.state",
        "assistant.final", "turn.state", "turn.state",
    ]
    assert [event.payload["state"] for event in events[:3]] == ["queued", "speaking", "delivered"]
    assert events[0].payload["voice_ticket"] == 17
    assert observer.result.final_text == "Xin chao"
    assert observer.result.status == "complete"


def test_terminal_and_observer_failures_are_idempotent_and_isolated():
    delivered = []

    def sink(event):
        delivered.append(event)
        raise RuntimeError("sink must not affect the turn")

    observer = PrivateTurnObserver(identity(), sink)
    observer.on_text_final("reply")
    observer.on_terminal(PrivateTurnResult(False, "complete", "reply", None, None))
    observer.on_terminal(PrivateTurnResult(False, "failed", "other", "provider_failed", None))
    assert len(delivered) == 3
    assert observer.result.status == "complete"
    assert observer.result.final_text == "reply"
    assert observer.result.reason_code is None


def test_casual_pipeline_returns_typed_result_without_changing_history():
    with pipeline_fixture() as (pipeline, memory, _tags, _notes):
        pipeline.is_casual_ping = lambda _text: True
        pipeline.build_casual_ping_reply = lambda _text: "Nana nghe day Ba."
        observer = PrivateTurnObserver(identity(), lambda _event: None)
        voice = Voice()
        result = asyncio.run(
            pipeline.handle_chat_turn(None, voice, "hello", None, observer=observer)
        )
        assert result.status == "complete"
        assert result.final_text == "Nana nghe day Ba."
        assert memory.memory["chat_log"][-1] == f"NANA: {result.final_text}"
        assert voice.normal == [result.final_text]


def test_stream_pipeline_emits_only_sanitized_indexed_text_and_final():
    with pipeline_fixture() as (pipeline, memory, _tags, _notes):
        async def stream(*_args, **_kwargs):
            for fragment in ("Xin ", "chao ", "sk-FAKE_ONLY_1234567890"):
                yield fragment

        pipeline.ask_gpt_stream = stream
        events = []
        observer = PrivateTurnObserver(identity(), events.append)
        result = asyncio.run(
            pipeline.handle_chat_turn(
                None,
                Voice(),
                "Ke con nghe mot chuyen",
                None,
                observer=observer,
            )
        )
        assert result.status == "complete"
        deltas = [event for event in events if event.message_type == "assistant.delta"]
        assert [event.payload["delta_index"] for event in deltas] == list(
            range(1, len(deltas) + 1)
        )
        visible = "".join(event.payload["text"] for event in deltas)
        assert "sk-FAKE_ONLY_1234567890" not in visible
        finals = [event for event in events if event.message_type == "assistant.final"]
        assert len(finals) == 1
        assert finals[0].payload["text"] == result.final_text
        assert memory.memory["chat_log"][-1] == f"NANA: {result.final_text}"


def test_diagnostic_skip_returns_bounded_failed_result_without_fake_reply():
    with pipeline_fixture() as (pipeline, _memory, _tags, _notes):
        pipeline.is_diagnostic_fragment = lambda _text: True
        events = []
        observer = PrivateTurnObserver(identity(), events.append)
        result = asyncio.run(
            pipeline.handle_chat_turn(None, Voice(), "runtime diagnostic", None, observer=observer)
        )
        assert result.status == "failed"
        assert result.reason_code == "no_final_text"
        assert not [event for event in events if event.message_type == "assistant.final"]


def main() -> None:
    tests = [
        test_indexed_delta_and_final_are_sanitized_and_ordered,
        test_voice_receipts_and_terminal_result_are_correlated,
        test_terminal_and_observer_failures_are_idempotent_and_isolated,
        test_casual_pipeline_returns_typed_result_without_changing_history,
        test_stream_pipeline_emits_only_sanitized_indexed_text_and_final,
        test_diagnostic_skip_returns_bounded_failed_result_without_fake_reply,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_private_turn_observer: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
