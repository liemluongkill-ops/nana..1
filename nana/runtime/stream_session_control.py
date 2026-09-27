"""Thread-safe lifecycle control for a foreground Stream V1 voice session."""
from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable


SESSION_STATES = frozenset({
    "connecting",
    "waiting",
    "thinking",
    "preparing_audio",
    "speaking",
    "stopping",
    "stopped",
    "error",
})
_ACTIVE_STATES = frozenset({
    "connecting",
    "waiting",
    "thinking",
    "preparing_audio",
    "speaking",
})
_TERMINAL_STATES = frozenset({"stopped", "error"})
_COUNTER_KEYS = frozenset({
    "poll_requests",
    "turns_delivered",
    "queued",
    "skipped",
    "dropped",
    "model_requests",
    "playback_dispatches",
    "tts_provider_requests",
    "local_audio_sink_writes",
})


def _safe_reason_code(value: Any, fallback: str | None = None) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 96 or not value.isascii():
        return fallback
    if not all(character.isalnum() or character in "_.-" for character in value):
        return fallback
    return value


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


class StreamSessionControl:
    """Serialize stop requests and emit a sanitized operator status stream."""

    def __init__(
        self,
        status_sink: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if status_sink is not None and not callable(status_sink):
            raise TypeError("status_sink must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._status_sink = status_sink
        self._clock = clock
        self._deadline: float | None = None
        self._deadline_clock = clock
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._state = "connecting"
        self._reason_code = ""
        self._stop_reason = ""
        self._metrics: dict[str, int] = {}
        self._updated_at = self._read_clock(clock, 0.0)

    @staticmethod
    def _read_clock(clock: Callable[[], float], fallback: float) -> float:
        try:
            current = _finite(clock())
        except Exception:
            current = None
        return fallback if current is None else current

    def _snapshot_locked(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "state": self._state,
            "reason_code": self._reason_code,
            "stop_requested": self._stop_event.is_set(),
            "stop_reason": self._stop_reason,
            "updated_at": self._updated_at,
        }
        result.update(self._metrics)
        return result

    def _emit_locked(self) -> None:
        if self._status_sink is None:
            return
        try:
            self._status_sink(self._snapshot_locked())
        except Exception:
            pass

    def _touch_locked(self) -> None:
        self._updated_at = self._read_clock(self._clock, self._updated_at)

    def _request_stop_locked(self, reason: str) -> None:
        if self._state in _TERMINAL_STATES or self._stop_event.is_set():
            return
        self._stop_reason = reason
        self._reason_code = reason
        self._state = "stopping"
        self._stop_event.set()
        self._touch_locked()
        self._emit_locked()

    def _check_deadline_locked(self) -> None:
        if (
            self._deadline is None
            or self._stop_event.is_set()
            or self._state in _TERMINAL_STATES
        ):
            return
        current = self._read_clock(self._deadline_clock, float("-inf"))
        if current >= self._deadline:
            self._request_stop_locked("session_timeout")

    def request_stop(self, reason: str = "operator_stop") -> None:
        safe_reason = _safe_reason_code(reason, "operator_stop")
        with self._lock:
            self._check_deadline_locked()
            self._request_stop_locked(safe_reason or "operator_stop")

    @property
    def stop_requested(self) -> bool:
        with self._lock:
            self._check_deadline_locked()
            return self._stop_event.is_set()

    @property
    def stop_reason(self) -> str:
        with self._lock:
            self._check_deadline_locked()
            return self._stop_reason

    def stage(self, state: str, **metrics: Any) -> None:
        if state not in SESSION_STATES:
            raise ValueError("invalid_session_state")
        sanitized_metrics = {
            key: value
            for key, value in metrics.items()
            if key in _COUNTER_KEYS and type(value) is int and value >= 0
        }
        reason = _safe_reason_code(metrics.get("reason_code"))

        with self._lock:
            self._check_deadline_locked()
            if self._state in _TERMINAL_STATES:
                return

            self._metrics.update(sanitized_metrics)
            if reason is not None:
                self._reason_code = reason

            if state == "stopping":
                stop_reason = self._stop_reason or reason or "operator_stop"
                if not self._stop_event.is_set():
                    self._stop_reason = stop_reason
                    self._stop_event.set()
                self._state = "stopping"
            elif state in _TERMINAL_STATES:
                if not self._stop_event.is_set():
                    self._stop_reason = reason or (
                        "session_error" if state == "error" else "session_stopped"
                    )
                    self._stop_event.set()
                self._state = state
            elif self._state != "stopping":
                self._state = state

            self._touch_locked()
            self._emit_locked()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._check_deadline_locked()
            return self._snapshot_locked()

    def wait(self, timeout_seconds: float) -> bool:
        timeout = _finite(timeout_seconds)
        if timeout is None or timeout < 0:
            raise ValueError("invalid_wait_timeout")
        with self._lock:
            self._check_deadline_locked()
            if self._stop_event.is_set():
                return True
            wait_for = timeout
            if self._deadline is not None:
                current = self._read_clock(self._deadline_clock, float("-inf"))
                if math.isfinite(current):
                    wait_for = min(wait_for, max(0.0, self._deadline - current))
        self._stop_event.wait(wait_for)
        with self._lock:
            self._check_deadline_locked()
            return self._stop_event.is_set()

    def set_deadline(
        self,
        deadline: float,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        value = _finite(deadline)
        if value is None:
            raise ValueError("invalid_session_deadline")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        with self._lock:
            self._deadline = value
            self._deadline_clock = self._clock if clock is None else clock
            self._check_deadline_locked()


class SessionPlaybackPort:
    """Add graceful session-stop behavior to a public playback port."""

    def __init__(self, port: Any, control: StreamSessionControl) -> None:
        if not isinstance(control, StreamSessionControl):
            raise TypeError("control must be StreamSessionControl")
        self._port = port
        self._control = control

    def ready(self):
        return self._port.ready()

    def play(self, request, *, should_continue, on_first_audio):
        started = False
        started_lock = threading.Lock()
        self._control.stage("preparing_audio")

        def session_should_continue() -> bool:
            original_allowed = should_continue() is True
            stop_requested = self._control.stop_requested
            with started_lock:
                audio_started = started
            return original_allowed and (audio_started or not stop_requested)

        def session_first_audio(evidence) -> bool:
            nonlocal started
            if not session_should_continue():
                return False
            if on_first_audio(evidence) is not True:
                return False
            with started_lock:
                started = True
            self._control.stage("speaking")
            return True

        return self._port.play(
            request,
            should_continue=session_should_continue,
            on_first_audio=session_first_audio,
        )

    def cancel(self, playback_id: str):
        return self._port.cancel(playback_id)


__all__ = [
    "SESSION_STATES",
    "SessionPlaybackPort",
    "StreamSessionControl",
]
