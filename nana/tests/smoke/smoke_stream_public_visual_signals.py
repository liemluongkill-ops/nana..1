"""Offline smoke for Stream V1 public playback visual signals."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import threading
import types
from types import SimpleNamespace

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
NANA_ROOT = ROOT / "nana"


def _namespace(name: str, directory: Path) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = [str(directory)]
    return module


def _module(name: str, **values) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__.update(values)
    return module


class _FakeCallbackAbort(Exception):
    pass


class _FakeCallbackStop(Exception):
    pass


def _forbid_real_audio(*_args, **_kwargs):
    raise AssertionError("public visual smoke attempted real audio output")


sys.modules["nana"] = _namespace("nana", NANA_ROOT)
sys.modules["nana.runtime"] = _namespace("nana.runtime", NANA_ROOT / "runtime")
sys.modules["nana.voice"] = _namespace("nana.voice", NANA_ROOT / "voice")
sys.modules["sounddevice"] = _module(
    "sounddevice",
    CallbackAbort=_FakeCallbackAbort,
    CallbackStop=_FakeCallbackStop,
    OutputStream=_forbid_real_audio,
    stop=lambda: None,
)
sys.modules["soundfile"] = _module(
    "soundfile",
    read=lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("public visual smoke attempted audio decoding")
    ),
)
sys.modules["keyboard"] = _module("keyboard", is_pressed=lambda _key: False)
sys.modules["nana.runtime.logger"] = _module(
    "nana.runtime.logger",
    log_event=lambda *_args, **_kwargs: None,
)
sys.modules["nana.config"] = _module(
    "nana.config",
    ELEVEN_PUBLIC_TTS_MODEL="eleven_v4",
)
sys.modules["requests"] = _module(
    "requests",
    Session=lambda: (_ for _ in ()).throw(
        AssertionError("public visual smoke constructed an HTTP session")
    ),
    Timeout=type("Timeout", (Exception,), {}),
    RequestException=type("RequestException", (Exception,), {}),
)


_SIDE_EFFECT_VIOLATIONS: list[str] = []


def _audit(event, args):
    if event == "open" and args and not isinstance(args[0], int):
        path = str(args[0]).replace("\\", "/").lower()
        mode = str(args[1]) if len(args) > 1 else ""
        flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
        forbidden_read = "/nana/data/" in path or path.endswith("/.env")
        forbidden_write = (
            any(marker in mode for marker in ("w", "a", "x", "+"))
            or bool(flags & write_flags)
        )
        if forbidden_read or forbidden_write:
            _SIDE_EFFECT_VIOLATIONS.append(f"{event}:{path}:{mode}")
            raise AssertionError(f"forbidden public visual smoke file access: {path}")
    if event in {"socket.connect", "socket.bind", "subprocess.Popen", "os.system", "os.spawn"}:
        _SIDE_EFFECT_VIOLATIONS.append(event)
        raise AssertionError(f"forbidden public visual smoke side effect: {event}")


sys.addaudithook(_audit)


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance_ms(self, milliseconds: int) -> None:
        self.value += milliseconds / 1000.0


def _identity(
    *,
    session: str = "stream-session-1",
    playback: str = "playback-1",
    attempt: str = "attempt-1",
    event: str = "event-1",
):
    from nana.runtime.stream_public_visual_signals import PublicVisualPlaybackIdentity

    return PublicVisualPlaybackIdentity(session, playback, attempt, event)


def test_store_emits_only_current_correlated_state_and_server_computes_age():
    from nana.runtime.stream_public_visual_signals import (
        PROTOCOL_NAME,
        STALE_AFTER_MS,
        PublicVisualSignalStore,
    )

    clock = _Clock()
    store = PublicVisualSignalStore("stream-session-1", clock=clock)
    identity = _identity()
    assert store.begin_playback(identity, expression_action="happy") is True
    assert store.snapshot(after=0) == {
        "ok": True,
        "protocol": PROTOCOL_NAME,
        "cursor": 1,
        "stale_after_ms": STALE_AFTER_MS,
        "current": None,
    }

    assert store.publish_frame(
        identity,
        sequence=1,
        open_value=0.75,
        energy=0.25,
        viseme="aa",
        speaking=True,
    ) is True
    first = store.snapshot(after=0)
    assert set(first) == {"ok", "protocol", "cursor", "stale_after_ms", "current"}
    assert first["cursor"] == 2
    assert first["current"] == {
        "stream_session_id": "stream-session-1",
        "playback_id": "playback-1",
        "attempt_id": "attempt-1",
        "event_id": "event-1",
        "sequence": 1,
        "open": 0.75,
        "energy": 0.25,
        "viseme": "aa",
        "speaking": True,
        "age_ms": 0,
        "expires_in_ms": 180,
        "expression": {
            "event_id": "event-1",
            "action": "happy",
            "expires_in_ms": 5000,
        },
        "terminal_reason": None,
    }
    assert store.snapshot(after=first["cursor"])["current"] is None
    restarted_consumer = store.snapshot(after=999)
    assert restarted_consumer["current"]["playback_id"] == "playback-1"

    clock.advance_ms(40)
    aged = store.snapshot(after=0)["current"]
    assert aged["age_ms"] == 40
    assert aged["expires_in_ms"] == 140
    assert aged["expression"]["expires_in_ms"] == 4960

    clock.advance_ms(140)
    assert store.snapshot(after=0)["current"] is None


def test_store_rejects_identity_crossover_sequences_and_unsafe_values():
    from nana.runtime.stream_public_visual_signals import PublicVisualSignalStore

    store = PublicVisualSignalStore("stream-session-1", clock=_Clock())
    identity = _identity()
    wrong_session = replace(identity, stream_session_id="stream-session-2")
    wrong_playback = replace(identity, playback_id="playback-2")
    assert store.begin_playback(wrong_session) is False
    assert store.snapshot(after=0)["current"] is None
    assert store.begin_playback(identity, expression_action="wave") is False
    assert store.begin_playback(identity, expression_action="happy") is True

    assert store.publish_frame(
        wrong_playback, sequence=1, open_value=0.5, energy=0.5, viseme="aa", speaking=True
    ) is False
    assert store.publish_frame(
        identity, sequence=1, open_value=2.0, energy=0.5, viseme="aa", speaking=True
    ) is False
    assert store.publish_frame(
        identity, sequence=1, open_value=0.5, energy=0.5, viseme="unsafe", speaking=True
    ) is False
    assert store.publish_frame(
        identity, sequence=1, open_value=0.5, energy=0.25, viseme="ih", speaking=True
    ) is True
    accepted = store.snapshot(after=0)
    cursor = accepted["cursor"]
    assert store.publish_frame(
        identity, sequence=1, open_value=0.9, energy=0.9, viseme="aa", speaking=True
    ) is False
    assert store.publish_frame(
        identity, sequence=0, open_value=0.9, energy=0.9, viseme="aa", speaking=True
    ) is False
    assert store.publish_frame(
        identity, sequence=3, open_value=0.9, energy=0.9, viseme="aa", speaking=True
    ) is False
    assert store.snapshot(after=0)["cursor"] == cursor
    assert store.snapshot(after=0)["current"]["sequence"] == 1


def test_new_playback_and_session_change_clear_old_current_without_backlog():
    from nana.runtime.stream_public_visual_signals import PublicVisualSignalStore

    store = PublicVisualSignalStore("stream-session-1", clock=_Clock())
    first = _identity(playback="playback-1", event="event-1")
    second = _identity(playback="playback-2", attempt="attempt-2", event="event-2")
    assert store.begin_playback(first) is True
    assert store.publish_frame(
        first, sequence=1, open_value=0.4, energy=0.2, viseme="aa", speaking=True
    ) is True
    old_cursor = store.snapshot(after=0)["cursor"]
    assert store.begin_playback(second) is True
    cleared = store.snapshot(after=old_cursor)
    assert cleared["cursor"] > old_cursor and cleared["current"] is None
    assert store.publish_frame(
        first, sequence=2, open_value=0.8, energy=0.8, viseme="aa", speaking=True
    ) is False
    assert store.publish_frame(
        second, sequence=1, open_value=0.3, energy=0.1, viseme="oh", speaking=True
    ) is True
    assert store.snapshot(after=0)["current"]["playback_id"] == "playback-2"

    before_change = store.snapshot(after=0)["cursor"]
    assert store.change_session("stream-session-2") is True
    changed = store.snapshot(after=before_change)
    assert changed["cursor"] > before_change and changed["current"] is None
    assert store.publish_frame(
        second, sequence=2, open_value=0.3, energy=0.1, viseme="oh", speaking=True
    ) is False


def test_terminal_and_shutdown_are_bounded_silence_states():
    from nana.runtime.stream_public_visual_signals import PublicVisualSignalStore

    store = PublicVisualSignalStore("stream-session-1", clock=_Clock())
    identity = _identity()
    assert store.begin_playback(identity, expression_action="blink") is True
    assert store.publish_frame(
        identity, sequence=1, open_value=0.6, energy=0.2, viseme="aa", speaking=True
    ) is True
    assert store.terminal(identity, "completed") is True
    terminal = store.snapshot(after=0)["current"]
    assert terminal["sequence"] == 2
    assert terminal["open"] == terminal["energy"] == 0.0
    assert terminal["viseme"] == "sil" and terminal["speaking"] is False
    assert terminal["expression"] is None
    assert terminal["terminal_reason"] == "completed"
    assert store.publish_frame(
        identity, sequence=3, open_value=0.5, energy=0.5, viseme="aa", speaking=True
    ) is False
    assert store.terminal(identity, "not-allowed") is False
    completed_cursor = store.snapshot(after=0)["cursor"]
    assert store.shutdown() is True
    shutdown = store.snapshot(after=completed_cursor)["current"]
    assert shutdown["sequence"] == 3
    assert shutdown["terminal_reason"] == "shutdown"
    assert shutdown["open"] == 0.0 and shutdown["speaking"] is False

    store = PublicVisualSignalStore("stream-session-1", clock=_Clock())
    next_identity = _identity(playback="playback-2", attempt="attempt-2")
    assert store.begin_playback(next_identity) is True
    assert store.publish_frame(
        next_identity, sequence=1, open_value=0.5, energy=0.5, viseme="aa", speaking=True
    ) is True
    assert store.shutdown() is True
    stopped = store.snapshot(after=0)["current"]
    assert stopped["terminal_reason"] == "shutdown"
    assert stopped["open"] == 0.0 and stopped["speaking"] is False


class _Stream:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def write(self, frame):
        if self.fail:
            raise RuntimeError("fake sink write failure")
        self.writes.append(np.asarray(frame).copy())
        return False


def test_lipsync_visual_callback_runs_only_after_successful_sink_write():
    from nana.voice.lipsync import LipsyncManager, PreparedAudio

    manager = LipsyncManager()
    stream = _Stream()
    seen = []

    def visual(**payload):
        seen.append((len(stream.writes), payload))

    result = manager.play_prepared_audio_receipted(
        PreparedAudio(np.ones(45, dtype=np.float32), 1000, 0.045),
        before_first_audio=lambda: True,
        on_first_audio=lambda _size: True,
        emit_mouth=False,
        on_sink_frame=visual,
        output_stream_factory=lambda **_kwargs: stream,
    )
    assert result.completed is True
    assert len(seen) == len(stream.writes) == 3
    assert seen[0][0] == 1
    assert set(seen[0][1]) == {"open_value", "energy", "viseme", "speaking"}
    assert 0.0 <= seen[0][1]["open_value"] <= 1.0
    assert 0.0 <= seen[0][1]["energy"] <= 1.0

    failed_seen = []
    failed = manager.play_prepared_audio_receipted(
        PreparedAudio(np.ones(20, dtype=np.float32), 1000, 0.02),
        before_first_audio=lambda: True,
        on_first_audio=lambda _size: True,
        emit_mouth=False,
        on_sink_frame=lambda **payload: failed_seen.append(payload),
        output_stream_factory=lambda **_kwargs: _Stream(fail=True),
    )
    assert failed.completed is False and failed.abort_reason == "playback_error"
    assert failed_seen == []


def test_lipsync_visual_callback_error_cannot_change_audio_receipt():
    from nana.voice.lipsync import LipsyncManager, PreparedAudio

    manager = LipsyncManager()
    stream = _Stream()
    calls = []

    def broken(**_payload):
        calls.append(len(stream.writes))
        raise RuntimeError("visual observer failure")

    result = manager.play_prepared_audio_receipted(
        PreparedAudio(np.ones(40, dtype=np.float32), 1000, 0.04),
        before_first_audio=lambda: True,
        on_first_audio=lambda _size: True,
        emit_mouth=False,
        on_sink_frame=broken,
        output_stream_factory=lambda **_kwargs: stream,
    )
    assert result.completed is True and result.first_audio_started is True
    assert result.written_frames == len(stream.writes) == len(calls) == 2


def _request():
    from nana.runtime.public_context_boundary import PublicEventScope
    from nana.runtime.public_delivery_state import PublicDeliveryRecord
    from nana.runtime.public_identity import CanonicalPublicIdentity
    from nana.runtime.stream_cum0_contract import compute_correlation_id
    from nana.runtime.stream_cum2_response import ResponseArtifact
    from nana.runtime.stream_cum5_voice_playback import PublicVoicePlaybackRequest

    identity = CanonicalPublicIdentity("youtube", "viewer", "youtube:viewer")
    scope = PublicEventScope(
        "youtube", "room", "stream-session-1", "event-1", "Viewer", identity
    )
    text = "[happy] Nana public visual smoke."
    artifact = ResponseArtifact(
        scope,
        compute_correlation_id(scope),
        0,
        "ingress-attempt",
        "output-1",
        text,
        "nana-public",
        10.0,
    )
    generated = PublicDeliveryRecord(
        scope.event_id,
        artifact.output_id,
        "generated",
        "attempt-1",
        0,
        text[:96],
        scope,
        11.0,
        delivery_mode="voice_only",
    )
    import hashlib

    return PublicVoicePlaybackRequest(
        artifact,
        generated,
        None,
        "",
        "",
        "",
        artifact.correlation_id,
        "playback-1",
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "local_speaker",
        12.0,
    )


class _VisualEngine:
    def __init__(self, *, state: str = "completed") -> None:
        self.state = state
        self.cancelled = 0
        self.shutdowns = 0

    def play_public_once(self, text, **kwargs):
        assert kwargs["before_provider"]() is True
        assert kwargs["before_first_audio"]() is True
        assert kwargs["on_first_audio"](320) is True
        kwargs["on_sink_frame"](
            open_value=0.7,
            energy=0.2,
            viseme="aa",
            speaking=True,
        )
        if self.state == "completed":
            return SimpleNamespace(
                state="completed",
                audio_completed=True,
                requested_segments=1,
                fetched_segments=1,
                played_segments=1,
                unplayed_segments=0,
                original_chars=len(text),
                remaining_chars=0,
                played_duration_ms=20.0,
                abort_reason="none",
                stop_confirmed=True,
            )
        return SimpleNamespace(
            state="aborted",
            audio_completed=False,
            requested_segments=1,
            fetched_segments=1,
            played_segments=0,
            unplayed_segments=1,
            original_chars=len(text),
            remaining_chars=len(text),
            played_duration_ms=20.0,
            abort_reason="playback_error",
            stop_confirmed=True,
        )

    def cancel_public_playback(self, timeout=2.0):
        self.cancelled += 1
        return True

    def shutdown(self):
        self.shutdowns += 1


def test_adapter_binds_request_identity_and_emits_completed_or_failed_silence():
    from nana.runtime.stream_cum5_voice_engine_adapter import VoiceEnginePublicPlaybackPort
    from nana.runtime.stream_public_visual_signals import PublicVisualSignalStore

    request = _request()
    for engine_state, expected_state, terminal_reason in (
        ("completed", "completed", "completed"),
        ("failed", "failed", "failed"),
    ):
        store = PublicVisualSignalStore("stream-session-1", clock=_Clock())
        engine = _VisualEngine(state=engine_state)
        port = VoiceEnginePublicPlaybackPort(
            engine_factory=lambda engine=engine: engine,
            visual_signal_store=store,
            clock=lambda: 14.0,
        )
        result = port.play(
            request,
            should_continue=lambda: True,
            on_first_audio=lambda _evidence: True,
        )
        assert result.state == expected_state
        current = store.snapshot(after=0)["current"]
        assert current["stream_session_id"] == request.artifact.scope.stream_session_id
        assert current["playback_id"] == request.playback_id
        assert current["attempt_id"] == request.generated_record.attempt_id
        assert current["event_id"] == request.artifact.scope.event_id
        assert current["sequence"] == 2
        assert current["terminal_reason"] == terminal_reason
        assert current["open"] == 0.0 and current["speaking"] is False
        assert "text" not in json.dumps(store.snapshot(after=0)).lower()


def test_visual_store_failures_never_change_audio_completion():
    from nana.runtime.stream_cum5_voice_engine_adapter import VoiceEnginePublicPlaybackPort

    class BrokenStore:
        def begin_playback(self, *_args, **_kwargs):
            raise RuntimeError("begin failed")

        def publish_frame(self, *_args, **_kwargs):
            raise RuntimeError("publish failed")

        def terminal(self, *_args, **_kwargs):
            raise RuntimeError("terminal failed")

    request = _request()
    port = VoiceEnginePublicPlaybackPort(
        engine_factory=_VisualEngine,
        visual_signal_store=BrokenStore(),
        clock=lambda: 14.0,
    )
    result = port.play(
        request,
        should_continue=lambda: True,
        on_first_audio=lambda _evidence: True,
    )
    assert result.state == "completed" and result.audio_completed is True


def test_adapter_cancel_is_correlated_terminal_silence():
    from nana.runtime.stream_cum5_voice_engine_adapter import VoiceEnginePublicPlaybackPort
    from nana.runtime.stream_public_visual_signals import PublicVisualSignalStore

    entered = threading.Event()
    release = threading.Event()

    class BlockingEngine:
        def play_public_once(self, text, **kwargs):
            assert kwargs["before_provider"]() is True
            assert kwargs["before_first_audio"]() is True
            assert kwargs["on_first_audio"](320) is True
            kwargs["on_sink_frame"](
                open_value=0.6,
                energy=0.2,
                viseme="aa",
                speaking=True,
            )
            entered.set()
            assert release.wait(timeout=2.0)
            return SimpleNamespace(
                state="cancelled",
                audio_completed=False,
                requested_segments=1,
                fetched_segments=1,
                played_segments=0,
                unplayed_segments=1,
                original_chars=len(text),
                remaining_chars=len(text),
                played_duration_ms=20.0,
                abort_reason="shutdown_cancelled",
                stop_confirmed=True,
            )

        def cancel_public_playback(self, timeout=2.0):
            release.set()
            return True

        def shutdown(self):
            return None

    request = _request()
    store = PublicVisualSignalStore("stream-session-1", clock=_Clock())
    port = VoiceEnginePublicPlaybackPort(
        engine_factory=BlockingEngine,
        visual_signal_store=store,
        clock=lambda: 14.0,
    )
    result_box = {}
    thread = threading.Thread(
        target=lambda: result_box.setdefault(
            "result",
            port.play(
                request,
                should_continue=lambda: True,
                on_first_audio=lambda _evidence: True,
            ),
        )
    )
    thread.start()
    assert entered.wait(timeout=1.0)
    speaking = store.snapshot(after=0)
    speaking_cursor = speaking["cursor"]
    assert speaking["current"]["speaking"] is True
    assert port.cancel("wrong-playback").confirmed_stopped is False
    assert store.snapshot(after=0)["cursor"] == speaking_cursor
    assert port.cancel(request.playback_id).confirmed_stopped is True
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert result_box["result"].state == "cancelled"
    terminal = store.snapshot(after=0)["current"]
    assert terminal["terminal_reason"] == "cancelled"
    assert terminal["open"] == 0.0 and terminal["speaking"] is False


def test_public_http_application_is_get_only_exact_cors_and_private_route_closed():
    assert "nana.runtime.avatar_mouth_stream" not in sys.modules
    from nana.runtime.avatar_intent_gateway import PublicVisualSignalHttpApplication
    from nana.runtime.stream_public_visual_signals import PublicVisualSignalStore
    assert "nana.runtime.avatar_mouth_stream" not in sys.modules

    store = PublicVisualSignalStore("stream-session-1", clock=_Clock())
    identity = _identity()
    assert store.begin_playback(identity) is True
    assert store.publish_frame(
        identity, sequence=1, open_value=0.5, energy=0.2, viseme="aa", speaking=True
    ) is True
    app = PublicVisualSignalHttpApplication(store)
    allowed_origin = "http://127.0.0.1:5174"

    ok = app.handle(
        "GET", "/v1/avatar/public-signals?after=0", origin=allowed_origin
    )
    assert ok.status_code == 200
    assert ok.headers == {
        "Access-Control-Allow-Origin": allowed_origin,
        "Cache-Control": "no-store",
        "Content-Type": "application/json; charset=utf-8",
        "Vary": "Origin",
    }
    assert ok.payload["ok"] is True and ok.payload["current"]["sequence"] == 1
    assert "Access-Control-Allow-Credentials" not in ok.headers

    unchanged = app.handle(
        "GET",
        f"/v1/avatar/public-signals?after={ok.payload['cursor']}",
        origin=allowed_origin,
    )
    assert unchanged.status_code == 200 and unchanged.payload["current"] is None
    assert app.handle("GET", "/v1/avatar/mouth", origin=allowed_origin).status_code == 404
    assert app.handle("GET", "/v1/avatar/status", origin=allowed_origin).status_code == 404
    assert app.handle(
        "GET", "/v1/avatar/public-signals?after=0&limit=1", origin=allowed_origin
    ).status_code == 400
    assert app.handle(
        "GET", "/v1/avatar/public-signals?after=0&after=1", origin=allowed_origin
    ).status_code == 400
    assert app.handle(
        "GET", "/v1/avatar/public-signals?after=-1", origin=allowed_origin
    ).status_code == 400
    assert app.handle(
        "GET",
        "http://example.invalid/v1/avatar/public-signals?after=0",
        origin=allowed_origin,
    ).status_code == 400
    assert app.handle(
        "GET", "/v1/avatar/public-signals?after=0", origin="http://localhost:5174"
    ).status_code == 403
    assert app.handle(
        "POST", "/v1/avatar/public-signals?after=0", origin=allowed_origin
    ).status_code == 405
    assert app.handle(
        "OPTIONS", "/v1/avatar/public-signals?after=0", origin=allowed_origin
    ).status_code == 405
    for response in (
        unchanged,
        app.handle("GET", "/v1/avatar/mouth", origin=allowed_origin),
        app.handle("POST", "/v1/avatar/public-signals", origin=allowed_origin),
    ):
        assert set(response.payload) == {
            "ok", "protocol", "cursor", "stale_after_ms", "current"
        }
        assert response.payload["current"] is None
        assert len(json.dumps(response.payload)) < 16 * 1024


def main() -> None:
    tests = (
        test_store_emits_only_current_correlated_state_and_server_computes_age,
        test_store_rejects_identity_crossover_sequences_and_unsafe_values,
        test_new_playback_and_session_change_clear_old_current_without_backlog,
        test_terminal_and_shutdown_are_bounded_silence_states,
        test_lipsync_visual_callback_runs_only_after_successful_sink_write,
        test_lipsync_visual_callback_error_cannot_change_audio_receipt,
        test_adapter_binds_request_identity_and_emits_completed_or_failed_silence,
        test_visual_store_failures_never_change_audio_completion,
        test_adapter_cancel_is_correlated_terminal_silence,
        test_public_http_application_is_get_only_exact_cors_and_private_route_closed,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    assert _SIDE_EFFECT_VIOLATIONS == [], _SIDE_EFFECT_VIOLATIONS
    print(f"smoke_stream_public_visual_signals: {len(tests)}/{len(tests)} passed")


if __name__ == "__main__":
    main()
