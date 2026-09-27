"""Offline regression coverage for the private PC voice overlap pilot.

No LLM, ElevenLabs, playback device, Presence node, VTS, OBS, Discord, or game
input is used. Provider and lipsync boundaries are deterministic fakes.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from contextlib import redirect_stdout
import io
import queue
import threading
import time
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _lead() -> str:
    return "Đây là câu đầu tiên đủ dài để Nana bắt đầu nói sớm cho Ba nghe. "


def _tail() -> str:
    return "Phần còn lại vẫn được giữ nguyên, tải song song và phát đúng một lần."


def _test_builder_commits_once_and_preserves_exact_text() -> None:
    from nana.runtime.private_voice_overlap import PrivateVoiceOverlapTurn

    committed = []
    committed_event = threading.Event()

    def commit(request):
        committed.append(request)
        committed_event.set()
        return 17

    turn = PrivateVoiceOverlapTurn(
        commit,
        minimum_chars=45,
        maximum_chars=140,
        coalesce_ms=15,
        turn_id="builder-exact",
    )
    turn.feed("[warmly] " + _lead())
    assert committed_event.wait(1.0), "lead timer did not commit"
    turn.feed(_tail())
    full = "[warmly] " + _lead() + _tail()
    result = turn.finish(full)
    request = committed[0]
    payload = request.tail_future.result(timeout=1.0)

    assert result.committed is True and result.ticket == 17, result
    assert len(committed) == 1, committed
    assert request.lead_text + payload.tail_text == full
    assert payload.full_text == full
    assert payload.split_offset == request.split_offset
    assert result.full_chars == result.lead_chars + result.tail_chars


def _test_builder_eof_before_timer_and_open_tag_fall_back() -> None:
    from nana.runtime.private_voice_overlap import PrivateVoiceOverlapTurn

    calls = []
    turn = PrivateVoiceOverlapTurn(
        lambda request: calls.append(request) or 1,
        minimum_chars=45,
        maximum_chars=140,
        coalesce_ms=200,
    )
    turn.feed(_lead())
    result = turn.finish(_lead())
    time.sleep(0.25)
    assert result.committed is False, result
    assert calls == [], calls

    committed = threading.Event()
    japanese_text = "[warmly] 今日はゆっくり話すから、最後まで安心して聞いてね。 "
    japanese_tail = "続きも一度だけ、同じ順番で届けるよ。"
    japanese = PrivateVoiceOverlapTurn(
        lambda request: calls.append(request) or committed.set() or 4,
        minimum_chars=20,
        maximum_chars=100,
        coalesce_ms=10,
    )
    japanese.feed(japanese_text)
    assert committed.wait(1.0)
    japanese.feed(japanese_tail)
    japanese_full = japanese_text + japanese_tail
    japanese_result = japanese.finish(japanese_full)
    japanese_request = calls[-1]
    japanese_payload = japanese_request.tail_future.result(timeout=1.0)
    assert japanese_result.committed is True
    assert japanese_request.lead_text + japanese_payload.tail_text == japanese_full

    calls.clear()
    open_tag = PrivateVoiceOverlapTurn(
        lambda request: calls.append(request) or 2,
        minimum_chars=45,
        maximum_chars=140,
        coalesce_ms=10,
    )
    text = "[warmly câu này cố ý không đóng tag và đủ dài để kiểm tra an toàn."
    open_tag.feed(text)
    open_result = open_tag.finish(text)
    time.sleep(0.05)
    assert open_result.committed is False, open_result
    assert calls == [], calls


def _test_builder_abort_cancels_committed_future() -> None:
    from nana.runtime.private_voice_overlap import PrivateVoiceOverlapTurn

    requests = []
    committed = threading.Event()

    def commit(request):
        requests.append(request)
        committed.set()
        return 5

    turn = PrivateVoiceOverlapTurn(
        commit,
        minimum_chars=45,
        maximum_chars=140,
        coalesce_ms=10,
    )
    turn.feed(_lead())
    assert committed.wait(1.0)
    assert turn.abort("test_interrupt") is True
    request = requests[0]
    assert request.cancel_event.is_set()
    try:
        request.tail_future.result(timeout=1.0)
    except RuntimeError as exc:
        assert "test_interrupt" in str(exc)
    else:
        raise AssertionError("aborted overlap future unexpectedly resolved")


def _test_tail_payload_rechecks_cancel_after_wait() -> None:
    def fetch(_self, _text, _profile):
        raise AssertionError("cancelled tail must not fetch")

    engine = _make_engine(fetch)
    future = Future()
    cancel = threading.Event()
    item = _item(
        future=future,
        cancel_event=cancel,
        turn_id="cancel-after-wait",
    )
    outcome = []

    def wait_payload():
        try:
            engine._wait_overlap_tail_payload(item)
        except Exception as exc:
            outcome.append(f"{type(exc).__name__}:{exc}")

    thread = threading.Thread(target=wait_payload, daemon=True)
    thread.start()
    time.sleep(0.02)
    cancel.set()
    from nana.runtime.private_voice_overlap import OverlapTailPayload

    future.set_result(
        OverlapTailPayload(
            full_text=item.lead_text + _tail(),
            tail_text=_tail(),
            split_offset=item.split_offset,
            llm_completed_at=time.perf_counter(),
        )
    )
    thread.join(timeout=1.0)
    assert not thread.is_alive()
    assert outcome == ["RuntimeError:overlap_cancelled"], outcome

class _FakeLipsync:
    def __init__(self, *, lead_seconds=0.12, tail_seconds=0.01):
        self.lead_seconds = lead_seconds
        self.tail_seconds = tail_seconds
        self.stop_event = threading.Event()
        self.played = []

    def prepare_audio_bytes(self, audio):
        duration = self.lead_seconds if audio == b"lead-audio" else self.tail_seconds
        return SimpleNamespace(data=audio, samplerate=1, duration_seconds=duration)

    def play_prepared_audio_nonblocking(self, prepared, on_done=None):
        def run():
            self.played.append(prepared.data)
            time.sleep(float(prepared.duration_seconds))
            if on_done is not None:
                on_done(True)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def stop(self):
        self.stop_event.set()


def _make_engine(fetch_behavior):
    from nana.voice.engine import VoiceEngine

    engine = VoiceEngine.__new__(VoiceEngine)
    engine.state_lock = threading.RLock()
    engine.state = {}
    engine._shutdown_event = threading.Event()
    engine._shutdown_lock = threading.Lock()
    engine._tts_semaphore = threading.BoundedSemaphore(3)
    engine.lipsync = _FakeLipsync()
    engine._tts_fetch_audio = MethodType(fetch_behavior, engine)
    engine._resolve_stream_ffmpeg_path = lambda: None
    engine._private_overlap_pcm_route_available = lambda: False
    return engine


def _item(*, future, cancel_event=None, turn_id="engine-overlap"):
    from nana.runtime.private_voice_overlap import OverlapQueueItem

    started = time.perf_counter()
    return OverlapQueueItem(
        turn_id=turn_id,
        lead_text=_lead(),
        split_offset=len(_lead()),
        tail_future=future,
        cancel_event=cancel_event or threading.Event(),
        turn_started_at=started,
        lead_committed_at=started,
        voice_mode="full",
        ticket=3,
    )


def _resolve_tail(future, item, *, delay=0.03):
    from nana.runtime.private_voice_overlap import OverlapTailPayload

    def resolve():
        time.sleep(delay)
        full = item.lead_text + _tail()
        future.set_result(
            OverlapTailPayload(
                full_text=full,
                tail_text=_tail(),
                split_offset=item.split_offset,
                llm_completed_at=time.perf_counter(),
            )
        )

    threading.Thread(target=resolve, daemon=True).start()


def _test_engine_fetches_tail_during_lead_playback() -> None:
    fetch_calls = []

    def fetch(_self, text, _profile):
        fetch_calls.append((text, time.perf_counter()))
        return b"lead-audio" if text == _lead() else b"tail-audio"

    engine = _make_engine(fetch)
    future = Future()
    item = _item(future=future)
    _resolve_tail(future, item, delay=0.03)
    result = engine._tts_overlap_completion(item)
    snap = dict(engine.state)

    assert result.audio_completed is True, result
    assert fetch_calls.count((_lead(), fetch_calls[0][1])) == 1
    assert [call[0] for call in fetch_calls].count(_lead()) == 1
    assert [call[0] for call in fetch_calls].count(_tail()) == 1
    assert engine.lipsync.played == [b"lead-audio", b"tail-audio"]
    assert snap["last_private_voice_overlap_true_overlap"] is True, snap
    assert (
        snap["last_private_voice_overlap_tail_fetch_started_ms"]
        < snap["last_private_voice_overlap_lead_playback_ended_ms"]
    ), snap
    assert snap["last_private_voice_overlap_missing_chars"] == 0
    assert snap["last_private_voice_overlap_duplicate_chars"] == 0
    assert snap["last_private_voice_overlap_lead_play_count"] == 1
    assert snap["last_private_voice_overlap_tail_play_count"] == 1


def _test_streaming_lead_uses_turn_first_audio_and_prefetches_tail() -> None:
    from nana.voice.engine import AudioCompletionResult

    fetch_times = []

    def fetch(_self, text, _profile):
        fetch_times.append((text, time.perf_counter()))
        time.sleep(0.02)
        return b"tail-audio"

    engine = _make_engine(fetch)
    engine._resolve_stream_ffmpeg_path = lambda: "fake-ffmpeg"
    engine._spawn_streaming_decoder = lambda _path: object()

    def stream_lead(_self, text, _profile, _mode, _process, *, original_chars=None):
        _self._update_state(
            last_stream_time_to_first_byte_ms=8.0,
            last_stream_time_to_first_audio_ms=18.0,
        )
        time.sleep(0.12)
        return AudioCompletionResult(
            state="completed",
            audio_completed=True,
            requested_segments=1,
            fetched_segments=1,
            played_segments=1,
            unplayed_segments=0,
            original_chars=int(original_chars or len(text)),
            remaining_chars=0,
            played_duration_ms=120.0,
            abort_reason="none",
        )

    engine._tts_http_streaming_completion = MethodType(stream_lead, engine)
    future = Future()
    item = _item(future=future, turn_id="streaming-lead")
    _resolve_tail(future, item, delay=0.02)
    result = engine._tts_overlap_completion(item)
    snap = dict(engine.state)

    assert result.audio_completed is True, result
    assert snap["last_tts_strategy"] == "private_voice_overlap_streaming_lead"
    assert snap["last_private_voice_overlap_first_audio_ms"] is not None
    assert snap["last_private_voice_overlap_first_audio_ms"] < 80.0, snap
    assert snap["last_private_voice_overlap_true_overlap"] is True, snap
    assert fetch_times and fetch_times[0][1] < (
        item.turn_started_at
        + snap["last_private_voice_overlap_lead_playback_ended_ms"] / 1000.0
    )
    assert engine.lipsync.played == [b"tail-audio"]


def _test_tail_retry_never_replays_lead() -> None:
    counts = {"lead": 0, "tail": 0}

    def fetch(_self, text, _profile):
        if text == _lead():
            counts["lead"] += 1
            return b"lead-audio"
        counts["tail"] += 1
        return None if counts["tail"] == 1 else b"tail-audio"

    engine = _make_engine(fetch)
    future = Future()
    item = _item(future=future, turn_id="tail-retry")
    _resolve_tail(future, item, delay=0.0)
    result = engine._tts_overlap_completion(item)

    assert result.audio_completed is True, result
    assert counts == {"lead": 1, "tail": 2}, counts
    assert engine.lipsync.played == [b"lead-audio", b"tail-audio"]
    assert engine.state["last_private_voice_overlap_tail_retry_count"] == 1


def _test_tail_failure_does_not_replay_lead() -> None:
    counts = {"lead": 0, "tail": 0}

    def fetch(_self, text, _profile):
        if text == _lead():
            counts["lead"] += 1
            return b"lead-audio"
        counts["tail"] += 1
        return None

    engine = _make_engine(fetch)
    future = Future()
    item = _item(future=future, turn_id="tail-failure")
    _resolve_tail(future, item, delay=0.0)
    result = engine._tts_overlap_completion(item)

    assert result.audio_completed is False, result
    assert result.played_segments == 1, result
    assert counts == {"lead": 1, "tail": 2}, counts
    assert engine.lipsync.played == [b"lead-audio"]
    assert engine.state["last_private_voice_overlap_status"] == "failed_after_lead"


def _test_lead_fetch_failure_falls_back_to_full_before_audio() -> None:
    full_calls = []

    def fetch(_self, _text, _profile):
        return None

    engine = _make_engine(fetch)
    future = Future()
    item = _item(future=future, turn_id="lead-fallback")
    _resolve_tail(future, item, delay=0.0)

    def fallback(_self, text, voice_mode="chat"):
        full_calls.append((text, voice_mode))
        segments = [text]
        return _self._record_audio_completion(
            _self._audio_completion_result(
                state="completed",
                segments=segments,
                fetched_segments=1,
                played_segments=1,
                original_chars=len(text),
                played_duration_ms=10.0,
            )
        )

    engine._tts_and_lipsync = MethodType(fallback, engine)
    result = engine._tts_overlap_completion(item)
    assert result.audio_completed is True
    assert full_calls == [(_lead() + _tail(), "full")]
    assert engine.lipsync.played == []
    assert engine.state["last_private_voice_overlap_status"] == "fallback_full_before_audio"


def _test_readiness_fails_closed_for_queue_and_presence() -> None:
    import nana.voice.engine as engine_module
    from nana.voice.engine import VoiceEngine

    engine = VoiceEngine.__new__(VoiceEngine)
    engine.voice_queue = queue.Queue(maxsize=5)
    engine.state_lock = threading.RLock()
    engine.state = {"speaking": False, "listening": False}
    engine._shutdown_event = threading.Event()
    engine._voice_completion = threading.Condition()
    engine._voice_enqueued_ticket = 0
    engine._voice_completed_ticket = 0
    engine._presence_pcm_outputs_if_available = lambda: None
    old_enabled = engine_module.PRIVATE_VOICE_OVERLAP_ENABLED
    try:
        engine_module.PRIVATE_VOICE_OVERLAP_ENABLED = True
        assert engine.private_overlap_readiness() == (True, "ready")
        engine.voice_queue.put_nowait("busy")
        assert engine.private_overlap_readiness() == (False, "queue_not_empty")
        engine.voice_queue.get_nowait()
        engine._voice_enqueued_ticket = 1
        assert engine.private_overlap_readiness() == (False, "voice_ticket_in_flight")
        engine._voice_completed_ticket = 1
        engine._presence_pcm_outputs_if_available = lambda: (
            lambda *_args, **_kwargs: None,
            None,
        )
        assert engine.private_overlap_readiness() == (False, "presence_available")
    finally:
        engine_module.PRIVATE_VOICE_OVERLAP_ENABLED = old_enabled


def _test_worker_uses_one_item_and_one_completion_ticket() -> None:
    def fetch(_self, text, _profile):
        return b"lead-audio" if text == _lead() else b"tail-audio"

    engine = _make_engine(fetch)
    engine.voice_queue = queue.Queue(maxsize=5)
    engine._voice_completion = threading.Condition()
    engine._voice_enqueued_ticket = 1
    engine._voice_completed_ticket = 0
    engine._presence_pcm_outputs_if_available = lambda: None
    engine.worker = threading.Thread(target=engine._voice_worker, daemon=True)
    future = Future()
    item = _item(future=future, turn_id="worker-ticket")
    item = item.__class__(
        turn_id=item.turn_id,
        lead_text=item.lead_text,
        split_offset=item.split_offset,
        tail_future=item.tail_future,
        cancel_event=item.cancel_event,
        turn_started_at=item.turn_started_at,
        lead_committed_at=item.lead_committed_at,
        voice_mode=item.voice_mode,
        ticket=1,
    )
    _resolve_tail(future, item, delay=0.02)
    engine.worker.start()
    engine.voice_queue.put_nowait(item)
    assert engine.wait_for_voice_ticket(1, timeout=3.0) is True
    assert engine.completed_voice_ticket() == 1
    assert engine.lipsync.played == [b"lead-audio", b"tail-audio"]
    engine.voice_queue.put_nowait(None)
    engine.worker.join(timeout=1.0)
    assert not engine.worker.is_alive()


def _test_status_exposes_actual_turn_overlap_without_calls() -> None:
    from nana.core.status_voice import (
        interaction_latency_snapshot,
        print_interaction_latency_status,
    )

    class StatusVoice:
        def snapshot(self):
            return {
                "last_stream_playback_state": "done",
                "last_stream_time_to_first_byte_ms": 900.0,
                "last_stream_time_to_first_audio_ms": 1400.0,
                "last_tts_text_len": 120,
                "last_audio_played_duration_ms": 6000.0,
                "private_voice_overlap_enabled": True,
                "last_private_voice_overlap_status": "completed",
                "last_private_voice_overlap_first_audio_ms": 2850.0,
                "last_private_voice_overlap_seam_wait_ms": 25.0,
                "last_private_voice_overlap_true_overlap": True,
                "last_private_voice_overlap_missing_chars": 0,
                "last_private_voice_overlap_duplicate_chars": 0,
            }

    voice = StatusVoice()
    snap = interaction_latency_snapshot(voice)
    assert snap["overlap"]["turn_first_audio_ms"] == 2850.0
    assert snap["overlap"]["true_overlap"] is True
    output = io.StringIO()
    with redirect_stdout(output):
        print_interaction_latency_status(voice)
    rendered = output.getvalue()
    assert "Actual overlap turn:" in rendered
    assert "first_audio=2850ms" in rendered
    assert "true_overlap=True" in rendered


def _test_pipeline_enqueues_one_overlap_item_without_normal_say() -> None:
    import nana.cli.chat_turn_pipeline as pipeline

    class FakeVoice:
        def __init__(self):
            self.requests = []
            self.normal_say = []
            self.committed = threading.Event()

        def private_overlap_readiness(self):
            return True, "ready"

        def record_private_overlap_bypass(self, _reason):
            raise AssertionError("pilot unexpectedly bypassed")

        def say_overlap(self, request, *, voice_mode="full"):
            self.requests.append((request, voice_mode))
            self.committed.set()
            return 9

        def say(self, text, *, voice_mode=None):
            self.normal_say.append((text, voice_mode))
            return 10

        def snapshot(self):
            return {
                "queue_size": 0,
                "queue_maxsize": 5,
                "dropped_total": 0,
                "last_error": None,
                "worker_alive": True,
            }

    async def fake_stream(*_args, **_kwargs):
        yield _lead()
        await asyncio.sleep(0.04)
        yield _tail()

    async def fake_expression(*_args, **_kwargs):
        return None

    class FakeSpine:
        def increment_turn(self):
            return 1

    class FakeLock:
        def __enter__(self):
            return None

        def __exit__(self, *_args):
            return False

    old = {
        "enabled": pipeline.PRIVATE_VOICE_OVERLAP_ENABLED,
        "coalesce": pipeline.PRIVATE_VOICE_OVERLAP_COALESCE_MS,
        "ask": pipeline.ask_gpt_stream,
        "expression": pipeline.trigger_expression_lifecycle,
        "spine": pipeline.get_memory_spine,
        "extract": pipeline.extract_important,
        "emotion": pipeline.update_emotion,
        "chat_log": pipeline.save_chat_log,
        "memory_save": pipeline.save_memory_async,
        "awareness_note": pipeline.awareness_memory_note_user_chat,
        "persona": pipeline.observe_text_for_persona,
        "awareness": pipeline.build_live_awareness_snapshot,
        "mark_chat": pipeline.mark_chat_time,
        "memory_lock": pipeline.memory_lock,
        "memory": pipeline.memory,
        "last_gpt_time": pipeline.cli_globals.last_gpt_time,
    }
    try:
        pipeline.PRIVATE_VOICE_OVERLAP_ENABLED = True
        pipeline.PRIVATE_VOICE_OVERLAP_COALESCE_MS = 10
        pipeline.ask_gpt_stream = fake_stream
        pipeline.trigger_expression_lifecycle = fake_expression
        pipeline.get_memory_spine = lambda: FakeSpine()
        pipeline.extract_important = lambda *_args, **_kwargs: None
        pipeline.update_emotion = lambda *_args, **_kwargs: None
        pipeline.save_chat_log = lambda *_args, **_kwargs: None
        pipeline.save_memory_async = lambda *_args, **_kwargs: None
        pipeline.awareness_memory_note_user_chat = lambda *_args, **_kwargs: None
        pipeline.observe_text_for_persona = lambda *_args, **_kwargs: None
        pipeline.build_live_awareness_snapshot = lambda *_args, **_kwargs: {"ok": True}
        pipeline.mark_chat_time = lambda: None
        pipeline.memory_lock = FakeLock()
        pipeline.memory = {
            "chat_log": [],
            "short_term": [],
            "emotion": {"annoyance": 0.0, "playfulness": 0.0},
        }
        pipeline.cli_globals.last_gpt_time = 0
        voice = FakeVoice()
        result = asyncio.run(
            pipeline.handle_chat_turn(
                None,
                voice,
                "Nana phân tích một đoạn vừa đủ để kiểm tra overlap.",
                None,
            )
        )
        assert result is False
        assert voice.committed.wait(1.0)
        assert len(voice.requests) == 1, voice.requests
        assert voice.normal_say == [], voice.normal_say
        request, mode = voice.requests[0]
        payload = request.tail_future.result(timeout=1.0)
        assert mode == "full"
        assert request.lead_text + payload.tail_text == _lead() + _tail()
    finally:
        pipeline.PRIVATE_VOICE_OVERLAP_ENABLED = old["enabled"]
        pipeline.PRIVATE_VOICE_OVERLAP_COALESCE_MS = old["coalesce"]
        pipeline.ask_gpt_stream = old["ask"]
        pipeline.trigger_expression_lifecycle = old["expression"]
        pipeline.get_memory_spine = old["spine"]
        pipeline.extract_important = old["extract"]
        pipeline.update_emotion = old["emotion"]
        pipeline.save_chat_log = old["chat_log"]
        pipeline.save_memory_async = old["memory_save"]
        pipeline.awareness_memory_note_user_chat = old["awareness_note"]
        pipeline.observe_text_for_persona = old["persona"]
        pipeline.build_live_awareness_snapshot = old["awareness"]
        pipeline.mark_chat_time = old["mark_chat"]
        pipeline.memory_lock = old["memory_lock"]
        pipeline.memory = old["memory"]
        pipeline.cli_globals.last_gpt_time = old["last_gpt_time"]


def _test_pipeline_eof_before_commit_records_fresh_bypass() -> None:
    import nana.cli.chat_turn_pipeline as pipeline

    class FakeVoice:
        def __init__(self):
            self.normal_say = []
            self.bypasses = []

        def private_overlap_readiness(self):
            return True, "ready"

        def record_private_overlap_bypass(self, reason):
            self.bypasses.append(reason)

        def say_overlap(self, _request, *, voice_mode="full"):
            raise AssertionError("short EOF must not commit overlap")

        def say(self, text, *, voice_mode=None):
            self.normal_say.append((text, voice_mode))
            return 11

        def snapshot(self):
            return {
                "queue_size": 0,
                "queue_maxsize": 5,
                "dropped_total": 0,
                "last_error": None,
                "worker_alive": True,
            }

    short_reply = "Ba nghỉ mắt một chút nha, đừng để bug chưa mệt mà Ba đã mệt trước."

    async def fake_stream(*_args, **_kwargs):
        yield short_reply

    async def fake_expression(*_args, **_kwargs):
        return None

    class FakeSpine:
        def increment_turn(self):
            return 1

    class FakeLock:
        def __enter__(self):
            return None

        def __exit__(self, *_args):
            return False

    old = {
        "enabled": pipeline.PRIVATE_VOICE_OVERLAP_ENABLED,
        "coalesce": pipeline.PRIVATE_VOICE_OVERLAP_COALESCE_MS,
        "ask": pipeline.ask_gpt_stream,
        "expression": pipeline.trigger_expression_lifecycle,
        "spine": pipeline.get_memory_spine,
        "extract": pipeline.extract_important,
        "emotion": pipeline.update_emotion,
        "chat_log": pipeline.save_chat_log,
        "memory_save": pipeline.save_memory_async,
        "awareness_note": pipeline.awareness_memory_note_user_chat,
        "persona": pipeline.observe_text_for_persona,
        "awareness": pipeline.build_live_awareness_snapshot,
        "mark_chat": pipeline.mark_chat_time,
        "memory_lock": pipeline.memory_lock,
        "memory": pipeline.memory,
        "last_gpt_time": pipeline.cli_globals.last_gpt_time,
    }
    try:
        pipeline.PRIVATE_VOICE_OVERLAP_ENABLED = True
        pipeline.PRIVATE_VOICE_OVERLAP_COALESCE_MS = 150
        pipeline.ask_gpt_stream = fake_stream
        pipeline.trigger_expression_lifecycle = fake_expression
        pipeline.get_memory_spine = lambda: FakeSpine()
        pipeline.extract_important = lambda *_args, **_kwargs: None
        pipeline.update_emotion = lambda *_args, **_kwargs: None
        pipeline.save_chat_log = lambda *_args, **_kwargs: None
        pipeline.save_memory_async = lambda *_args, **_kwargs: None
        pipeline.awareness_memory_note_user_chat = lambda *_args, **_kwargs: None
        pipeline.observe_text_for_persona = lambda *_args, **_kwargs: None
        pipeline.build_live_awareness_snapshot = lambda *_args, **_kwargs: {"ok": True}
        pipeline.mark_chat_time = lambda: None
        pipeline.memory_lock = FakeLock()
        pipeline.memory = {
            "chat_log": [],
            "short_term": [],
            "emotion": {"annoyance": 0.0, "playfulness": 0.0},
        }
        pipeline.cli_globals.last_gpt_time = 0
        voice = FakeVoice()
        result = asyncio.run(
            pipeline.handle_chat_turn(
                None,
                voice,
                "Nana nhắc Ba nghỉ mắt bằng một câu ngắn.",
                None,
            )
        )
        assert result is False
        assert voice.normal_say == [(short_reply, "full")]
        assert voice.bypasses == ["llm_completed_before_commit"]
    finally:
        pipeline.PRIVATE_VOICE_OVERLAP_ENABLED = old["enabled"]
        pipeline.PRIVATE_VOICE_OVERLAP_COALESCE_MS = old["coalesce"]
        pipeline.ask_gpt_stream = old["ask"]
        pipeline.trigger_expression_lifecycle = old["expression"]
        pipeline.get_memory_spine = old["spine"]
        pipeline.extract_important = old["extract"]
        pipeline.update_emotion = old["emotion"]
        pipeline.save_chat_log = old["chat_log"]
        pipeline.save_memory_async = old["memory_save"]
        pipeline.awareness_memory_note_user_chat = old["awareness_note"]
        pipeline.observe_text_for_persona = old["persona"]
        pipeline.build_live_awareness_snapshot = old["awareness"]
        pipeline.mark_chat_time = old["mark_chat"]
        pipeline.memory_lock = old["memory_lock"]
        pipeline.memory = old["memory"]
        pipeline.cli_globals.last_gpt_time = old["last_gpt_time"]


def main() -> None:
    tests = [
        _test_builder_commits_once_and_preserves_exact_text,
        _test_builder_eof_before_timer_and_open_tag_fall_back,
        _test_builder_abort_cancels_committed_future,
        _test_tail_payload_rechecks_cancel_after_wait,
        _test_engine_fetches_tail_during_lead_playback,
        _test_streaming_lead_uses_turn_first_audio_and_prefetches_tail,
        _test_tail_retry_never_replays_lead,
        _test_tail_failure_does_not_replay_lead,
        _test_lead_fetch_failure_falls_back_to_full_before_audio,
        _test_readiness_fails_closed_for_queue_and_presence,
        _test_worker_uses_one_item_and_one_completion_ticket,
        _test_status_exposes_actual_turn_overlap_without_calls,
        _test_pipeline_enqueues_one_overlap_item_without_normal_say,
        _test_pipeline_eof_before_commit_records_fresh_bypass,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
        print("  PASS")
    print(f"smoke_private_voice_overlap: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
