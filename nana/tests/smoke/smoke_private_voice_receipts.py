"""Provider-free smoke for correlated private voice receipts.

The suite exercises the receipt ledger and the standard VoiceEngine queue
boundary without a provider, audio device, Presence node, or Core runtime.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import queue
import sys
import threading
from concurrent.futures import Future

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nana.runtime.private_voice_receipts import (  # noqa: E402
    PrivateVoiceContext,
    PrivateVoiceReceiptLedger,
    ReceiptLedgerFull,
)
from nana.runtime.private_turn_observer import (  # noqa: E402
    PrivateTurnIdentity,
    PrivateTurnObserver,
    PrivateTurnResult,
)
from nana.runtime.private_voice_overlap import OverlapCommitRequest, OverlapQueueItem  # noqa: E402
from nana.runtime.private_voice_ttd import TtdCommitRequest, TtdQueueItem  # noqa: E402


class SmokeSkip(RuntimeError):
    pass


def _require_voice_engine():
    try:
        from nana.voice.engine import VoiceEngine
    except ModuleNotFoundError as exc:
        if exc.name in {"keyboard", "speech_recognition", "sounddevice"}:
            raise SmokeSkip(f"voice engine dependency unavailable: {exc.name}") from exc
        raise
    return VoiceEngine


EPOCH = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
SESSION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
TURN = "11111111-1111-4111-8111-111111111111"
CORRELATION = "22222222-2222-4222-8222-222222222222"


class FakeClock:
    def __init__(self, value: float = 100.0):
        self.value = float(value)

    def __call__(self) -> float:
        return self.value


def context(*, turn_id: str = TURN) -> PrivateVoiceContext:
    return PrivateVoiceContext(EPOCH, SESSION, turn_id, CORRELATION)


def test_first_audio_and_completion_require_same_ticket_and_context():
    events = []
    ledger = PrivateVoiceReceiptLedger(clock=FakeClock(), event_sink=events.append)
    receipt = ledger.queued(context(), engine_ticket=7)

    assert ledger.first_audio(receipt, engine_ticket=8) is False
    assert ledger.first_audio(receipt, engine_ticket=7, context=context(turn_id=TURN)) is True
    assert ledger.complete(receipt, engine_ticket=7, completed=True) is True
    assert [item.state for item in ledger.events(receipt)] == [
        "queued",
        "speaking",
        "delivered",
    ]
    assert [item.engine_ticket for item in events] == [7, 7, 7]


def test_duplicate_queue_and_terminal_callbacks_are_idempotent():
    ledger = PrivateVoiceReceiptLedger(clock=FakeClock())
    first = ledger.queued(context(), engine_ticket=4)
    duplicate = ledger.queued(context(), engine_ticket=4)
    assert duplicate.receipt_id == first.receipt_id
    assert len(ledger.events(first)) == 1
    assert ledger.first_audio(first, engine_ticket=4) is True
    assert ledger.first_audio(first, engine_ticket=4) is False
    assert ledger.complete(first, engine_ticket=4, completed=True) is True
    assert ledger.complete(first, engine_ticket=4, completed=True) is False
    assert len(ledger.events(first)) == 3


def test_confirmed_failure_and_ambiguous_stop_never_become_delivered():
    ledger = PrivateVoiceReceiptLedger(clock=FakeClock())
    before_audio = ledger.queued(context(turn_id="33333333-3333-4333-8333-333333333333"), engine_ticket=1)
    assert ledger.complete(before_audio, engine_ticket=1, completed=False, reason_code="provider_failed") is True
    assert ledger.current(before_audio).state == "failed"

    ambiguous = ledger.queued(context(turn_id="44444444-4444-4444-8444-444444444444"), engine_ticket=2)
    assert ledger.first_audio(ambiguous, engine_ticket=2) is True
    assert ledger.complete(ambiguous, engine_ticket=2, completed=None, reason_code="stop_unconfirmed") is True
    assert ledger.current(ambiguous).state == "unknown"


def test_context_mismatch_cannot_advance_or_complete_ticket():
    ledger = PrivateVoiceReceiptLedger(clock=FakeClock())
    receipt = ledger.queued(context(), engine_ticket=9)
    wrong = context(turn_id="55555555-5555-4555-8555-555555555555")
    assert ledger.first_audio(receipt, engine_ticket=9, context=wrong) is False
    assert ledger.complete(receipt, engine_ticket=9, context=wrong, completed=True) is False
    assert ledger.current(receipt).state == "queued"


def test_terminal_only_eviction_and_ttl_pruning():
    clock = FakeClock()
    ledger = PrivateVoiceReceiptLedger(clock=clock, max_tickets=2, ttl_seconds=30)
    first = ledger.queued(context(), engine_ticket=1)
    assert ledger.complete(first, engine_ticket=1, completed=False, reason_code="failed") is True
    second = ledger.queued(context(turn_id="66666666-6666-4666-8666-666666666666"), engine_ticket=2)
    third = ledger.queued(context(turn_id="77777777-7777-4777-8777-777777777777"), engine_ticket=3)
    assert ledger.current(first) is None
    assert ledger.current(second).state == "queued"
    assert ledger.current(third).state == "queued"
    try:
        ledger.queued(context(turn_id="88888888-8888-4888-8888-888888888888"), engine_ticket=4)
    except ReceiptLedgerFull:
        pass
    else:
        raise AssertionError("non-terminal receipts must not be evicted")
    clock.value += 31
    ledger.prune()
    assert ledger.current(second) is not None
    assert ledger.current(third) is not None


def test_sink_failure_does_not_change_receipt_state_or_raise():
    def broken(_receipt):
        raise RuntimeError("observer failure")

    ledger = PrivateVoiceReceiptLedger(clock=FakeClock(), event_sink=broken)
    receipt = ledger.queued(context(), engine_ticket=5)
    assert ledger.first_audio(receipt, engine_ticket=5) is True
    assert ledger.complete(receipt, engine_ticket=5, completed=True) is True
    assert ledger.current(receipt).state == "delivered"
    assert ledger.sink_failures == 3


def test_receipt_snapshots_are_immutable_and_redact_no_payload_text():
    ledger = PrivateVoiceReceiptLedger(clock=FakeClock())
    receipt = ledger.queued(context(), engine_ticket=6)
    assert not hasattr(receipt, "text")
    try:
        receipt.state = "delivered"
    except Exception:
        pass
    else:
        raise AssertionError("receipt snapshot must be immutable")


def test_observer_accepts_voice_receipts_after_text_terminal():
    events = []
    identity = PrivateTurnIdentity(EPOCH, SESSION, "cccccccc-cccc-4ccc-8ccc-cccccccccccc", TURN, CORRELATION)
    observer = PrivateTurnObserver(identity, events.append)
    ledger = PrivateVoiceReceiptLedger(event_sink=observer.on_voice_receipt)
    receipt = ledger.queued(context(), engine_ticket=12)
    observer.on_terminal(PrivateTurnResult(False, "complete", "final text", None, 12))
    assert ledger.first_audio(receipt, engine_ticket=12) is True
    assert ledger.complete(receipt, engine_ticket=12, completed=True) is True
    voice_states = [
        event.payload["state"]
        for event in events
        if event.message_type == "voice.state"
    ]
    assert voice_states == ["queued", "speaking", "delivered"]


def test_voice_engine_say_binds_context_to_one_queued_ticket():
    VoiceEngine = _require_voice_engine()

    clock = FakeClock()
    ledger = PrivateVoiceReceiptLedger(clock=clock)
    engine = VoiceEngine.__new__(VoiceEngine)
    engine.voice_queue = queue.Queue(maxsize=2)
    engine._voice_completion = threading.Condition()
    engine._voice_enqueued_ticket = 0
    engine._voice_completed_ticket = 0
    engine._private_voice_receipt_ledger = ledger
    engine._shutdown_event = threading.Event()
    engine.state_lock = threading.RLock()
    engine.state = {"queued_total": 0, "dropped_total": 0}
    engine._shutdown_requested = lambda: False
    engine._presence_pcm_outputs_if_available = lambda: None
    engine.private_overlap_readiness = lambda: (True, "ready")
    engine.private_ttd_readiness = lambda: (True, "ready")
    engine.record_private_overlap_bypass = lambda _reason: None
    engine.record_private_ttd_bypass = lambda _reason: None
    engine._update_state = lambda **values: engine.state.update(values)
    engine._increment_state = lambda name, amount=1: engine.state.__setitem__(
        name, int(engine.state.get(name, 0)) + int(amount)
    )

    ticket = engine.say("hello", receipt_context=context())
    item = engine.voice_queue.get_nowait()
    assert ticket == 1
    assert item.receipt_context == context()
    assert item.receipt_id
    receipt = ledger.current(item.receipt_id)
    assert receipt is not None and receipt.state == "queued"


def test_voice_engine_receipt_helpers_require_actual_first_audio_before_delivery():
    from types import SimpleNamespace
    VoiceEngine = _require_voice_engine()

    ledger = PrivateVoiceReceiptLedger(clock=FakeClock())
    engine = VoiceEngine.__new__(VoiceEngine)
    engine._private_voice_receipt_ledger = ledger
    engine._receipt_first_audio = VoiceEngine._receipt_first_audio.__get__(engine)
    engine._receipt_completion = VoiceEngine._receipt_completion.__get__(engine)
    identity = context()
    queued = ledger.queued(identity, engine_ticket=19)
    binding = SimpleNamespace(
        receipt_context=identity,
        receipt_id=queued.receipt_id,
        receipt_ledger=ledger,
        ticket=19,
    )
    assert engine._receipt_completion(binding, SimpleNamespace(audio_completed=True, stop_confirmed=True, abort_reason="none")) is False
    assert ledger.current(queued).state == "unknown"
    successful = ledger.queued(
        context(turn_id="99999999-9999-4999-8999-999999999999"),
        engine_ticket=20,
    )
    binding = SimpleNamespace(
        receipt_context=successful.context,
        receipt_id=successful.receipt_id,
        receipt_ledger=ledger,
        ticket=20,
    )
    assert engine._receipt_first_audio(binding) is True
    assert engine._receipt_completion(binding, SimpleNamespace(audio_completed=True, stop_confirmed=True, abort_reason="none")) is True
    assert ledger.current(successful).state == "delivered"


def test_private_pipeline_forwards_receipt_context_without_changing_terminal_callers():
    try:
        from nana.cli import chat_turn_pipeline as pipeline
    except ModuleNotFoundError as exc:
        if exc.name in {"pyvts", "keyboard", "speech_recognition", "sounddevice"}:
            raise SmokeSkip(f"pipeline dependency unavailable: {exc.name}") from exc
        raise

    class Voice:
        def __init__(self):
            self.calls = []

        def say(self, text, **kwargs):
            self.calls.append((text, kwargs))
            return 1

    voice = Voice()
    pipeline.say_with_voice_budget(voice, "hello", receipt_context=context())
    assert voice.calls[0][1]["receipt_context"] == context()


def _run_private_pipeline_admission(*, ticket, latest_ticket, streamed):
    from smoke_history_voice_consistency import Voice, pipeline_fixture

    class AdmissionVoice(Voice):
        def say(self, text, **kwargs):
            self.normal.append(text)
            self.say_kwargs = dict(kwargs)
            return ticket

        def latest_voice_ticket(self):
            return latest_ticket

    events = []
    observer = PrivateTurnObserver(
        PrivateTurnIdentity(
            EPOCH,
            SESSION,
            "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
            TURN,
            CORRELATION,
        ),
        events.append,
    )
    voice = AdmissionVoice()
    with pipeline_fixture() as (pipeline, _memory, _tags, _notes):
        if streamed:
            async def reply_stream(*_args, **_kwargs):
                yield "Nana trả lời đủ một câu để kiểm tra đường voice stream cuối."

            pipeline.is_casual_ping = lambda _text: False
            pipeline.ask_gpt_stream = reply_stream
            user_text = "Nana kiểm tra đường stream cuối cho Ba."
        else:
            pipeline.is_casual_ping = lambda _text: True
            pipeline.build_casual_ping_reply = lambda _text: "Nana nghe đây Ba."
            user_text = "Nana ơi"
        result = asyncio.run(
            pipeline.handle_chat_turn(
                None,
                voice,
                user_text,
                None,
                observer=observer,
            )
        )
    assert result.status == "complete"
    assert result.final_text == voice.normal[0]
    voice_events = [event for event in events if event.message_type == "voice.state"]
    return result, voice, voice_events, events


def test_private_budget_pipeline_reports_rejected_voice_admission_as_failed():
    result, voice, voice_events, events = _run_private_pipeline_admission(
        ticket=0,
        latest_ticket=41,
        streamed=False,
    )

    assert voice.normal == ["Nana nghe đây Ba."]
    assert len(voice_events) == 1
    assert voice_events[0].payload == {
        "state": "failed",
        "reason_code": "voice_admission_rejected",
    }
    final_event = next(
        event for event in events if event.message_type == "assistant.final"
    )
    assert events.index(final_event) < events.index(voice_events[0])
    assert result.voice_ticket is None


def test_private_budget_pipeline_reports_the_accepted_say_ticket():
    result, _voice, voice_events, events = _run_private_pipeline_admission(
        ticket=17,
        latest_ticket=41,
        streamed=False,
    )

    assert len(voice_events) == 1
    assert voice_events[0].payload == {"state": "queued", "voice_ticket": 17}
    final_event = next(
        event for event in events if event.message_type == "assistant.final"
    )
    assert events.index(final_event) < events.index(voice_events[0])
    assert result.voice_ticket == 17


def test_private_stream_pipeline_reports_rejected_voice_admission_as_failed():
    result, voice, voice_events, _events = _run_private_pipeline_admission(
        ticket=0,
        latest_ticket=43,
        streamed=True,
    )

    assert len(voice.normal) == 1
    assert len(voice_events) == 1
    assert voice_events[0].payload == {
        "state": "failed",
        "reason_code": "voice_admission_rejected",
    }
    assert result.voice_ticket is None


def test_private_stream_pipeline_reports_the_accepted_say_ticket():
    result, voice, voice_events, _events = _run_private_pipeline_admission(
        ticket=19,
        latest_ticket=43,
        streamed=True,
    )

    assert len(voice.normal) == 1
    assert len(voice_events) == 1
    assert voice_events[0].payload == {"state": "queued", "voice_ticket": 19}
    assert result.voice_ticket == 19


def test_standard_route_uses_first_sink_frame_and_completion_receipts():
    from types import MethodType, SimpleNamespace
    VoiceEngine = _require_voice_engine()

    events = []
    ledger = PrivateVoiceReceiptLedger(clock=FakeClock(), event_sink=events.append)
    receipt = ledger.queued(context(), engine_ticket=31)
    binding = SimpleNamespace(
        receipt_context=receipt.context,
        receipt_id=receipt.receipt_id,
        receipt_ledger=ledger,
        ticket=31,
        stop_confirmed=True,
    )

    class FakeLipsync:
        def prepare_audio_bytes(self, audio):
            return SimpleNamespace(data=audio, samplerate=1, duration_seconds=0.01)

        def play_prepared_audio_receipted(self, prepared, *, on_first_audio):
            assert prepared.data == b"audio"
            assert on_first_audio(4) is True
            return SimpleNamespace(completed=True, stop_confirmed=True)

    engine = VoiceEngine.__new__(VoiceEngine)
    engine.lipsync = FakeLipsync()
    engine.state_lock = threading.RLock()
    engine.state = {}
    engine._shutdown_event = threading.Event()
    engine._shutdown_lock = threading.Lock()
    engine._tts_fetch_audio = lambda _text, _profile: b"audio"
    engine._split_tts_text = lambda _text, **_kwargs: ["hello"]
    engine._shutdown_requested = lambda: False
    engine._update_state = lambda **values: engine.state.update(values)
    engine._seam_grade = lambda _value: "good"
    engine._receipt_first_audio = MethodType(VoiceEngine._receipt_first_audio, engine)
    engine._receipt_completion = MethodType(VoiceEngine._receipt_completion, engine)

    result = engine._tts_and_lipsync("hello", voice_mode="chat", receipt_binding=binding)
    assert result.audio_completed is True
    assert [event.state for event in events] == ["queued", "speaking"]
    assert engine._receipt_completion(binding, result) is True
    assert [event.state for event in ledger.events(receipt)] == [
        "queued", "speaking", "delivered"
    ]


def _engine_for_receipt_queue(ledger):
    VoiceEngine = _require_voice_engine()
    engine = VoiceEngine.__new__(VoiceEngine)
    engine.voice_queue = queue.Queue(maxsize=4)
    engine._voice_completion = threading.Condition()
    engine._voice_enqueued_ticket = 0
    engine._voice_completed_ticket = 0
    engine._private_voice_receipt_ledger = ledger
    engine._shutdown_event = threading.Event()
    engine.state_lock = threading.RLock()
    engine.state = {"queued_total": 0, "dropped_total": 0}
    engine._shutdown_requested = lambda: False
    engine._presence_pcm_outputs_if_available = lambda: None
    engine.private_overlap_readiness = lambda: (True, "ready")
    engine.private_ttd_readiness = lambda: (True, "ready")
    engine.record_private_overlap_bypass = lambda _reason: None
    engine.record_private_ttd_bypass = lambda _reason: None
    engine._update_state = lambda **values: engine.state.update(values)
    engine._increment_state = lambda name, amount=1: engine.state.__setitem__(
        name, int(engine.state.get(name, 0)) + int(amount)
    )
    return engine


def test_voice_engine_rejects_say_when_private_receipt_ledger_is_full():
    from unittest.mock import patch
    import nana.voice.engine as engine_module

    ledger = PrivateVoiceReceiptLedger(clock=FakeClock(), max_tickets=1)
    ledger.queued(context(), engine_ticket=90)
    engine = _engine_for_receipt_queue(ledger)
    rejected_context = context(turn_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    with patch.object(engine_module, "log_event", lambda *_args, **_kwargs: None):
        ticket = engine.say("must not enter the queue", receipt_context=rejected_context)

    assert ticket == 0
    assert engine.voice_queue.empty()
    assert engine.latest_voice_ticket() == 1
    assert engine.completed_voice_ticket() == 1
    assert engine.state["dropped_total"] == 1
    assert ledger.current(rejected_context) is None


def test_overlap_and_ttd_jobs_carry_one_shared_receipt_context():
    ledger = PrivateVoiceReceiptLedger(clock=FakeClock())
    engine = _engine_for_receipt_queue(ledger)
    overlap_request = OverlapCommitRequest(
        turn_id=TURN,
        lead_text="lead.",
        split_offset=5,
        tail_future=Future(),
        cancel_event=threading.Event(),
        turn_started_at=1.0,
        lead_committed_at=2.0,
        receipt_context=context(),
    )
    overlap_ticket = engine.say_overlap(overlap_request)
    overlap_item = engine.voice_queue.get_nowait()
    assert overlap_ticket == 1
    assert overlap_item.receipt_context == context()
    assert overlap_item.receipt_id == ledger.current(overlap_item.receipt_id).receipt_id

    ttd_request = TtdCommitRequest(
        turn_id=TURN,
        text_queue=queue.SimpleQueue(),
        final_future=Future(),
        cancel_event=threading.Event(),
        turn_started_at=1.0,
        committed_at=2.0,
        receipt_context=context(turn_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    )
    ttd_ticket = engine.say_ttd(ttd_request)
    ttd_item = engine.voice_queue.get_nowait()
    assert ttd_ticket == 2
    assert ttd_item.receipt_context == ttd_request.receipt_context
    assert ledger.current(ttd_item.receipt_id).state == "queued"


def test_sink_entry_is_not_first_audio_and_failed_write_never_speaks():
    import numpy as np
    from nana.voice.lipsync import LipsyncManager
    for fail in (False, True):
        events = []
        class Sink:
            def __init__(self, **_kw): pass
            def __enter__(self):
                events.append('opened')
                assert 'speaking' not in events
                return self
            def __exit__(self, *_args): return False
            def write(self, frame):
                if fail: raise OSError('fake sink refusal')
                events.append('write')
                return False
        lips = LipsyncManager.__new__(LipsyncManager)
        lips.stop_event = threading.Event()
        lips.audio_lock = threading.Lock()
        lips.mouth = 0.0
        lips._set_mouth = lambda *a, **kw: None
        pcm = queue.Queue()
        pcm.put(np.ones(480, dtype=np.float32) * .1)
        eof = threading.Event(); eof.set()
        result = lips._play_pcm_queue(pcm, eof, samplerate=24000,
            startup_buffer_ms=0, stall_timeout_s=1,
            on_first_audio=lambda: events.append('speaking'), output_stream_factory=Sink)
        assert events == (['opened'] if fail else ['opened', 'write', 'speaking']), events
        assert result.completed is (not fail)


def test_all_queue_types_resolve_receipt_binding():
    from dataclasses import replace
    ledger = PrivateVoiceReceiptLedger()
    engine = _engine_for_receipt_queue(ledger)
    engine.say_overlap(OverlapCommitRequest(TURN, 'lead', 4, Future(), threading.Event(), 0., 0., context()))
    item = engine.voice_queue.get_nowait()
    binding = engine._receipt_binding_for_item(item)
    assert binding is not None and binding.ticket == item.ticket


def test_worker_receipts_cover_overlap_pcm_and_ttd():
    from dataclasses import replace
    from unittest.mock import patch
    import nana.voice.engine as mod
    import smoke_private_voice_overlap_pcm as pcm
    import smoke_private_voice_ttd as ttd
    for route in ('pcm', 'ttd'):
        events = []
        ledger = PrivateVoiceReceiptLedger(event_sink=events.append)
        if route == 'pcm':
            engine = pcm._make_engine()
            item = pcm._item()
            pcm._bind_open(engine, lambda _text: pcm._FakeResponse([pcm._pcm(1000)]))
        else:
            engine = ttd._make_engine()
            first, rest = ttd._initial_text(), ttd._next_text()
            item = ttd._item(first, rest, first + rest)
            socket = ttd._FakeTtdSocket([pcm._pcm(1000)])
            engine._ttd_websocket_connect = lambda _uri: ttd._FakeConnect(socket)
        engine._presence_pcm_outputs_if_available = lambda: None
        engine.set_private_voice_receipt_ledger(ledger)
        receipt = ledger.queued(context(), engine_ticket=item.ticket)
        item = replace(item, receipt_context=context(), receipt_id=receipt.receipt_id)
        with pcm._pcm_route_enabled(), patch.object(mod, 'log_event', lambda *args: None), patch.object(mod, 'PRIVATE_VOICE_TTD_INPUT_MODE', 'incremental'):
            if route == 'pcm': engine._process_overlap_queue_item(item)
            else: engine._process_ttd_queue_item(item)
        assert [e.state for e in events] == ['queued', 'speaking', 'delivered'], (route, [e.state for e in events])


def test_multisegment_worker_does_not_abort_tail_or_merge_with_legacy_item():
    from types import SimpleNamespace
    from unittest.mock import patch
    import nana.voice.engine as mod
    events, played = [], []
    ledger = PrivateVoiceReceiptLedger(event_sink=events.append)
    engine = _engine_for_receipt_queue(ledger)
    engine._private_overlap_pcm_route_available = lambda: False
    engine._http_streaming_pilot_selected = lambda *args: False
    engine._provider_segments_for_full_voice = lambda text: [text] if text == 'legacy' else ['lead', 'tail']
    engine._tts_fetch_audio = lambda text, profile: text.encode()
    class Lips:
        stop_event = threading.Event()
        def prepare_audio_bytes(self, audio): return SimpleNamespace(data=audio, duration_seconds=.01)
        def play_prepared_audio_nonblocking(self, prepared, on_done):
            played.append(prepared.data); on_done(True)
        def play_prepared_audio_receipted(self, prepared, on_first_audio):
            played.append(prepared.data)
            assert on_first_audio(4) is True
            return SimpleNamespace(completed=True, stop_confirmed=True)
    engine.lipsync = Lips()
    engine.say('legacy', voice_mode='full')
    engine.say('private', voice_mode='full', receipt_context=context())
    engine.voice_queue.put(None)
    with patch.object(mod, 'log_event', lambda *args: None):
        engine._voice_worker()
    assert played == [b'legacy', b'lead', b'tail'], played
    assert [e.state for e in events] == ['queued', 'speaking', 'delivered']


def main() -> None:
    tests = [
        test_multisegment_worker_does_not_abort_tail_or_merge_with_legacy_item,
        test_worker_receipts_cover_overlap_pcm_and_ttd,
        test_sink_entry_is_not_first_audio_and_failed_write_never_speaks,
        test_all_queue_types_resolve_receipt_binding,
        test_first_audio_and_completion_require_same_ticket_and_context,
        test_duplicate_queue_and_terminal_callbacks_are_idempotent,
        test_confirmed_failure_and_ambiguous_stop_never_become_delivered,
        test_context_mismatch_cannot_advance_or_complete_ticket,
        test_terminal_only_eviction_and_ttl_pruning,
        test_sink_failure_does_not_change_receipt_state_or_raise,
        test_receipt_snapshots_are_immutable_and_redact_no_payload_text,
        test_observer_accepts_voice_receipts_after_text_terminal,
        test_voice_engine_say_binds_context_to_one_queued_ticket,
        test_voice_engine_receipt_helpers_require_actual_first_audio_before_delivery,
        test_private_pipeline_forwards_receipt_context_without_changing_terminal_callers,
        test_private_budget_pipeline_reports_rejected_voice_admission_as_failed,
        test_private_budget_pipeline_reports_the_accepted_say_ticket,
        test_private_stream_pipeline_reports_rejected_voice_admission_as_failed,
        test_private_stream_pipeline_reports_the_accepted_say_ticket,
        test_standard_route_uses_first_sink_frame_and_completion_receipts,
        test_voice_engine_rejects_say_when_private_receipt_ledger_is_full,
        test_overlap_and_ttd_jobs_carry_one_shared_receipt_context,
    ]
    skipped = []
    passed = 0
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        try:
            test()
        except SmokeSkip as exc:
            skipped.append((test.__name__, str(exc)))
            print(f"  SKIP: {exc}")
        else:
            passed += 1
    if skipped:
        print(
            "smoke_private_voice_receipts: PASS "
            f"({passed}/{len(tests)}; skipped={len(skipped)})"
        )
    else:
        print(f"smoke_private_voice_receipts: PASS ({passed}/{len(tests)})")


if __name__ == "__main__":
    main()
