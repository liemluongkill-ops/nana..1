"""Run a bounded YouTube-read / Nana-voice foreground host.

This runner never inserts a chat message and never constructs a YouTube text
publisher. It consumes only messages observed after the bootstrap page. Its
timeout is a soft polling/admission deadline; it does not cancel a model or
audio call already in flight.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
import types
from typing import TYPE_CHECKING, Any, Callable, Mapping


if TYPE_CHECKING:
    from nana.runtime.stream_session_control import StreamSessionControl


def _install_direct_run_namespace() -> None:
    """Bypass Nana's eager root facade when this file is run directly."""

    if __name__ != "__main__" or __package__:
        return
    nana_root = Path(__file__).resolve().parents[1]
    package_parent = nana_root.parent
    if str(package_parent) not in sys.path:
        sys.path.insert(0, str(package_parent))
    if "nana" not in sys.modules:
        package = types.ModuleType("nana")
        package.__path__ = [str(nana_root)]
        sys.modules["nana"] = package
    if "nana.runtime" not in sys.modules:
        runtime = types.ModuleType("nana.runtime")
        runtime.__path__ = [str(nana_root / "runtime")]
        sys.modules["nana.runtime"] = runtime


_install_direct_run_namespace()
sys.dont_write_bytecode = True


PHASE = "STREAM-V1-VOICE-HOST-RUNNER"
DEFAULT_MAX_TURNS = 1
DEFAULT_MAX_POLLS = 40
DEFAULT_TIMEOUT_SECONDS = 120.0
PUBLIC_VISUAL_SIGNALS_ENV = "NANA_STREAM_PUBLIC_VISUAL_SIGNALS_ENABLED"
_MAX_CONSECUTIVE_TRANSIENT_READ_RETRIES = 2
_RETRYABLE_READ_REASONS = frozenset({
    "youtube_transport_error",
    "youtube_service_unavailable",
})

_VOICE_GATE_EXPECTATIONS = (
    ("NANA_STREAM_CUM0_ENABLED", True),
    ("NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED", True),
    ("NANA_STREAM_CUM2_RESPONSE_ENABLED", True),
    ("NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED", False),
    ("NANA_STREAM_CUM4_HOST_ENABLED", True),
    ("NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED", True),
)
_BENIGN_STOP_REASONS = {
    "poll_limit",
    "timeout",
    "timeout_before_next_poll",
    "turn_limit",
}
_GRACEFUL_STOP_REASONS = _BENIGN_STOP_REASONS | {
    "keyboard_interrupt",
    "live_chat_ended",
    "live_chat_unavailable",
    "operator_stop",
    "provider_offline",
    "session_timeout",
}


def _enabled(name: str) -> bool:
    return str(os.getenv(name, "0")).strip().lower() in {"1", "true", "yes", "on"}


def _public_visual_signals_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(PUBLIC_VISUAL_SIGNALS_ENV, "0")).strip().lower() in {
        "1", "true", "yes", "on"
    }


def _gate_snapshot() -> dict[str, bool]:
    return {name: _enabled(name) for name, _expected in _VOICE_GATE_EXPECTATIONS}


def _gate_reason() -> str | None:
    snapshot = _gate_snapshot()
    if snapshot["NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED"]:
        return "cum3_must_be_disabled"
    if any(snapshot[name] is not expected for name, expected in _VOICE_GATE_EXPECTATIONS):
        return "stream_gate_disabled"
    return None


def _valid_bounds(max_turns: Any, max_polls: Any, timeout_seconds: Any) -> bool:
    if type(max_turns) is not int or max_turns < 1:
        return False
    if type(max_polls) is not int or max_polls < 1:
        return False
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        return False
    return math.isfinite(float(timeout_seconds)) and float(timeout_seconds) > 0


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _safe_reason_code(value: Any, fallback: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 96 or not value.isascii():
        return fallback
    if not all(character.isalnum() or character in "_.-" for character in value):
        return fallback
    return value


def _read_failure_reason(exc: BaseException, fallback: str) -> str:
    """Map known read failures without returning provider payload text."""

    chain: list[BaseException] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        cause = current.__cause__ or current.__context__
        current = cause if isinstance(cause, BaseException) else None

    markers: set[str] = set()
    status_code: int | None = None
    is_youtube_transport = False
    for error in chain:
        if type(error).__name__ == "YouTubeChatTransportError":
            is_youtube_transport = True
        for argument in getattr(error, "args", ()):
            if isinstance(argument, str) and argument.isascii():
                markers.add(argument.lower().replace("_", "").replace("-", ""))
        response = getattr(error, "response", None)
        raw_status = getattr(response, "status_code", None)
        if type(raw_status) is int:
            status_code = raw_status
        if response is None or not callable(getattr(response, "json", None)):
            continue
        try:
            payload = response.json()
        except Exception:
            continue
        error_payload = payload.get("error") if isinstance(payload, dict) else None
        details = error_payload.get("errors") if isinstance(error_payload, dict) else None
        if isinstance(details, list):
            for detail in details:
                reason = detail.get("reason") if isinstance(detail, dict) else None
                if isinstance(reason, str) and reason.isascii():
                    markers.add(reason.lower().replace("_", "").replace("-", ""))

    combined = " ".join(markers)
    if "livechatended" in combined or "live chat ended" in combined or "no longer live" in combined:
        return "live_chat_ended"
    if "quota" in combined or "dailylimit" in combined:
        return "youtube_quota_exceeded"
    if "ratelimit" in combined or "rate limit" in combined or status_code == 429:
        return "youtube_rate_limited"
    if "invalid json" in combined or "nonobject" in combined:
        return "youtube_invalid_response"
    if "no broadcast" in combined or "no active live chat" in combined:
        return "live_chat_unavailable"
    if status_code == 410:
        return "live_chat_ended"
    if status_code == 401:
        return "youtube_unauthorized"
    if status_code == 403:
        return "youtube_forbidden"
    if status_code == 404:
        return "live_chat_unavailable" if fallback == "live_chat_resolution_failed" else "live_chat_ended"
    if status_code is not None and 500 <= status_code <= 599:
        return "youtube_service_unavailable"
    if is_youtube_transport:
        return "youtube_transport_error"
    return fallback


def _provider_interval_seconds(page: Any) -> float:
    raw = _field(page, "pollingIntervalMillis", None)
    if raw is None:
        raw = _field(page, "polling_interval_millis", 1000)
    try:
        seconds = float(raw) / 1000.0
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(seconds) or seconds < 0:
        return 1.0
    return seconds


def _increment(counts: dict[str, int], key: str) -> None:
    counts[key] = int(counts.get(key, 0)) + 1


def _add_count(counts: dict[str, int], key: str, value: Any) -> None:
    if type(value) is int and value > 0:
        counts[key] = int(counts.get(key, 0)) + value


@dataclass(frozen=True)
class VoiceRunnerResult:
    status: str
    reason_code: str
    turns_delivered: int = 0
    poll_requests: int = 0
    youtube_resolve_requests: int = 0
    youtube_read_requests: int = 0
    host_ingest_calls: int = 0
    host_process_calls: int = 0
    queued: int = 0
    skipped: int = 0
    dropped: int = 0
    model_requests: int | None = None
    playback_dispatches: int | None = None
    tts_provider_requests: int | None = None
    local_audio_sink_writes: int | None = None
    playback_statuses: tuple[str, ...] = ()
    last_host_status: str | None = None
    last_host_reason: str | None = None
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.update({
            "phase": PHASE,
            "youtube_text_output": False,
            "bootstrap_context_only": True,
            "effects_measurement": {
                "youtube_read": "instrumented",
                "model": "instrumented" if self.model_requests is not None else "unmeasured",
                "playback": (
                    "instrumented" if self.playback_dispatches is not None else "unmeasured"
                ),
                "tts_provider": (
                    "instrumented" if self.tts_provider_requests is not None else "unmeasured"
                ),
                "local_audio_sink": (
                    "instrumented" if self.local_audio_sink_writes is not None else "unmeasured"
                ),
                "avatar": "not_integrated",
                "obs": "not_integrated",
                "memory_write": "not_integrated",
            },
        })
        return result


def _observed_value(observed: Mapping[str, int] | None, name: str) -> int | None:
    if observed is None or name not in observed:
        return None
    return int(observed[name])


def _result(
    status: str,
    reason_code: str,
    *,
    started_at: float,
    monotonic_clock: Callable[[], float],
    counts: Mapping[str, int],
    observed_counts: Mapping[str, int] | None,
    playback_statuses: list[str],
    last_host_status: str | None = None,
    last_host_reason: str | None = None,
) -> VoiceRunnerResult:
    try:
        elapsed = max(0.0, float(monotonic_clock()) - float(started_at))
    except Exception:
        elapsed = 0.0
    return VoiceRunnerResult(
        status=status,
        reason_code=reason_code,
        turns_delivered=int(counts.get("turns_delivered", 0)),
        poll_requests=int(counts.get("poll_requests", 0)),
        youtube_resolve_requests=int(counts.get("youtube_resolve_requests", 0)),
        youtube_read_requests=int(counts.get("youtube_read_requests", 0)),
        host_ingest_calls=int(counts.get("host_ingest_calls", 0)),
        host_process_calls=int(counts.get("host_process_calls", 0)),
        queued=int(counts.get("queued", 0)),
        skipped=int(counts.get("skipped", 0)),
        dropped=int(counts.get("dropped", 0)),
        model_requests=_observed_value(observed_counts, "model_requests"),
        playback_dispatches=_observed_value(observed_counts, "playback_dispatches"),
        tts_provider_requests=_observed_value(observed_counts, "tts_provider_requests"),
        local_audio_sink_writes=_observed_value(observed_counts, "local_audio_sink_writes"),
        playback_statuses=tuple(playback_statuses),
        last_host_status=last_host_status,
        last_host_reason=last_host_reason,
        elapsed_seconds=elapsed,
    )


def _preflight_result(status: str, reason_code: str) -> VoiceRunnerResult:
    return VoiceRunnerResult(
        status=status,
        reason_code=reason_code,
        model_requests=0,
        playback_dispatches=0,
        tts_provider_requests=0,
        local_audio_sink_writes=0,
    )


def _progress_metrics(
    counts: Mapping[str, int],
    observed_counts: Mapping[str, int] | None,
) -> dict[str, int]:
    metrics = {
        name: int(counts.get(name, 0))
        for name in ("poll_requests", "turns_delivered", "queued", "skipped", "dropped")
    }
    if observed_counts is not None:
        for name in (
            "model_requests",
            "playback_dispatches",
            "tts_provider_requests",
            "local_audio_sink_writes",
        ):
            if name in observed_counts:
                metrics[name] = int(observed_counts[name])
    return metrics


def _stage(
    control: StreamSessionControl | None,
    state: str,
    *,
    counts: Mapping[str, int],
    observed_counts: Mapping[str, int] | None,
    reason_code: str | None = None,
) -> None:
    if control is None:
        return
    metrics: dict[str, Any] = _progress_metrics(counts, observed_counts)
    if reason_code:
        metrics["reason_code"] = _safe_reason_code(reason_code, "session_stopped")
    control.stage(state, **metrics)


def _queue_depth(host: Any) -> int | None:
    try:
        value = host.snapshot().get("queued")
    except Exception:
        return None
    return value if type(value) is int and value >= 0 else None


def _requested_stop_reason(control: StreamSessionControl | None) -> str | None:
    if control is None or not control.stop_requested:
        return None
    return _safe_reason_code(control.stop_reason, "operator_stop")


def run_bounded_voice_host(
    *,
    video_id: str,
    transport: Any,
    voice_host_factory: Callable[[str], Any],
    max_turns: int = DEFAULT_MAX_TURNS,
    max_polls: int = DEFAULT_MAX_POLLS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    wall_clock: Callable[[], float] = time.time,
    monotonic_clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    observed_counts: dict[str, int] | None = None,
    control: StreamSessionControl | None = None,
) -> VoiceRunnerResult:
    """Poll fresh chat and drain selected voice jobs within the session bounds."""

    if not _valid_bounds(max_turns, max_polls, timeout_seconds):
        return _preflight_result("rejected", "invalid_bounds")
    canonical_video_id = str(video_id or "").strip()
    if not canonical_video_id:
        return _preflight_result("rejected", "video_id_required")
    gate_reason = _gate_reason()
    if gate_reason:
        return _preflight_result("blocked", gate_reason)
    if transport is None or not callable(voice_host_factory):
        return _preflight_result("rejected", "runtime_dependency_missing")

    try:
        started_at = float(monotonic_clock())
    except Exception:
        return _preflight_result("rejected", "invalid_monotonic_clock")
    if not math.isfinite(started_at):
        return _preflight_result("rejected", "invalid_monotonic_clock")
    deadline = started_at + float(timeout_seconds)
    counts: dict[str, int] = {
        "turns_delivered": 0,
        "poll_requests": 0,
        "youtube_resolve_requests": 0,
        "youtube_read_requests": 0,
        "host_ingest_calls": 0,
        "host_process_calls": 0,
        "queued": 0,
        "skipped": 0,
        "dropped": 0,
    }
    statuses: list[str] = []
    voice_host = None

    if control is not None:
        control.set_deadline(deadline, clock=monotonic_clock)
    _stage(
        control,
        "connecting",
        counts=counts,
        observed_counts=observed_counts,
    )

    def finish(
        status: str,
        reason: str,
        *,
        host_status: str | None = None,
        host_reason: str | None = None,
    ) -> VoiceRunnerResult:
        result = _result(
            status,
            reason,
            started_at=started_at,
            monotonic_clock=monotonic_clock,
            counts=counts,
            observed_counts=observed_counts,
            playback_statuses=statuses,
            last_host_status=host_status,
            last_host_reason=host_reason,
        )
        try:
            terminal_state = "stopped" if (
                status == "completed" or reason in _GRACEFUL_STOP_REASONS
            ) else "error"
            if terminal_state == "stopped":
                _stage(
                    control,
                    "stopping",
                    counts=counts,
                    observed_counts=observed_counts,
                    reason_code=reason,
                )
            _stage(
                control,
                terminal_state,
                counts=counts,
                observed_counts=observed_counts,
                reason_code=reason,
            )
        finally:
            _shutdown_voice_host(voice_host)
        return result

    try:
        stop_reason = _requested_stop_reason(control)
        if stop_reason:
            return finish("stopped", stop_reason)
        _increment(counts, "youtube_resolve_requests")
        _increment(counts, "youtube_read_requests")
        live_chat_id = str(transport.resolve_live_chat_id(canonical_video_id) or "").strip()
    except KeyboardInterrupt:
        return finish("stopped", "keyboard_interrupt")
    except Exception as exc:
        return finish("stopped", _read_failure_reason(exc, "live_chat_resolution_failed"))
    stop_reason = _requested_stop_reason(control)
    if stop_reason:
        return finish("stopped", stop_reason)
    if not live_chat_id:
        return finish("stopped", "live_chat_unavailable")
    if float(monotonic_clock()) >= deadline:
        return finish("stopped", _requested_stop_reason(control) or "timeout")

    try:
        stop_reason = _requested_stop_reason(control)
        if stop_reason:
            return finish("stopped", stop_reason)
        voice_host = voice_host_factory(live_chat_id)
        inner_host = voice_host.host
    except KeyboardInterrupt:
        return finish("stopped", "keyboard_interrupt")
    except Exception:
        return finish("stopped", "voice_host_setup_failed")
    stop_reason = _requested_stop_reason(control)
    if stop_reason:
        return finish("stopped", stop_reason)
    if float(monotonic_clock()) >= deadline:
        return finish("stopped", _requested_stop_reason(control) or "timeout")

    if _gate_reason() is not None:
        return finish("stopped", "stream_gate_changed")
    try:
        stop_reason = _requested_stop_reason(control)
        if stop_reason:
            return finish("stopped", stop_reason)
        _increment(counts, "poll_requests")
        _increment(counts, "youtube_read_requests")
        bootstrap_page = transport.list_messages(live_chat_id, page_token=None)
    except KeyboardInterrupt:
        return finish("stopped", "keyboard_interrupt")
    except Exception as exc:
        return finish("stopped", _read_failure_reason(exc, "bootstrap_poll_failed"))
    stop_reason = _requested_stop_reason(control)
    if stop_reason:
        return finish("stopped", stop_reason)
    try:
        now = float(wall_clock())
        _increment(counts, "host_ingest_calls")
        bootstrap_result = inner_host.ingest_response(
            bootstrap_page,
            received_at=now,
            now=now,
            bootstrap=True,
        )
    except KeyboardInterrupt:
        return finish("stopped", "keyboard_interrupt")
    except Exception:
        return finish("stopped", "bootstrap_ingest_failed")
    bootstrap_status = str(_field(bootstrap_result, "status", "") or "")
    if bootstrap_status in {"disabled", "halted", "rejected", "failed"}:
        bootstrap_reason = _safe_reason_code(
            _field(bootstrap_result, "reason_code"),
            "bootstrap_rejected",
        )
        return finish(
            "stopped",
            bootstrap_reason,
            host_status=bootstrap_status,
            host_reason=bootstrap_reason,
        )

    page = bootstrap_page
    page_token = _field(bootstrap_page, "nextPageToken", None)
    if page_token is None:
        page_token = _field(bootstrap_page, "next_page_token", None)
    next_poll_at = float(monotonic_clock()) + _provider_interval_seconds(page)
    transient_read_failures = 0
    _stage(
        control,
        "waiting",
        counts=counts,
        observed_counts=observed_counts,
    )

    while counts["poll_requests"] < max_polls:
        if counts["turns_delivered"] >= max_turns:
            return finish("completed", "turn_limit")
        stop_reason = _requested_stop_reason(control)
        if stop_reason:
            return finish("stopped", stop_reason)
        current_mono = float(monotonic_clock())
        if current_mono >= deadline:
            return finish("stopped", _requested_stop_reason(control) or "timeout")
        if next_poll_at > deadline:
            return finish("stopped", "timeout_before_next_poll")
        remaining = next_poll_at - current_mono
        if remaining > 0:
            try:
                if control is not None and sleep is time.sleep:
                    control.wait(remaining)
                else:
                    sleep(remaining)
            except KeyboardInterrupt:
                return finish("stopped", "keyboard_interrupt")
            except Exception:
                return finish("stopped", "poll_wait_failed")
        stop_reason = _requested_stop_reason(control)
        if stop_reason:
            return finish("stopped", stop_reason)
        if float(monotonic_clock()) > deadline:
            return finish("stopped", _requested_stop_reason(control) or "timeout")
        if _gate_reason() is not None:
            return finish("stopped", "stream_gate_changed")

        try:
            stop_reason = _requested_stop_reason(control)
            if stop_reason:
                return finish("stopped", stop_reason)
            _increment(counts, "poll_requests")
            _increment(counts, "youtube_read_requests")
            page = transport.list_messages(live_chat_id, page_token=page_token)
        except KeyboardInterrupt:
            return finish("stopped", "keyboard_interrupt")
        except Exception as exc:
            read_reason = _read_failure_reason(exc, "chat_poll_failed")
            can_retry = (
                read_reason in _RETRYABLE_READ_REASONS
                and transient_read_failures < _MAX_CONSECUTIVE_TRANSIENT_READ_RETRIES
                and counts["poll_requests"] < max_polls
            )
            if not can_retry:
                return finish("stopped", read_reason)
            transient_read_failures += 1
            next_poll_at = (
                float(monotonic_clock()) + _provider_interval_seconds(page)
            )
            _stage(
                control,
                "waiting",
                counts=counts,
                observed_counts=observed_counts,
                reason_code="youtube_transport_retry",
            )
            continue
        transient_read_failures = 0
        stop_reason = _requested_stop_reason(control)
        if stop_reason:
            return finish("stopped", stop_reason)
        if float(monotonic_clock()) >= deadline:
            return finish("stopped", _requested_stop_reason(control) or "timeout")
        next_token = _field(page, "nextPageToken", page_token)
        page_token = _field(page, "next_page_token", next_token)
        next_poll_at = float(monotonic_clock()) + _provider_interval_seconds(page)

        try:
            now = float(wall_clock())
            _increment(counts, "host_ingest_calls")
            ingested = inner_host.ingest_response(
                page,
                received_at=now,
                now=now,
                bootstrap=False,
            )
        except KeyboardInterrupt:
            return finish("stopped", "keyboard_interrupt")
        except Exception:
            return finish("stopped", "chat_ingest_failed")
        ingest_status = str(_field(ingested, "status", "") or "")
        ingest_reason = _safe_reason_code(
            _field(ingested, "reason_code"),
            "chat_ingest_rejected",
        )
        _add_count(counts, "skipped", _field(ingested, "skipped", 0))
        _add_count(counts, "dropped", _field(ingested, "dropped", 0))
        if ingest_status in {"disabled", "halted", "rejected", "failed"}:
            return finish(
                "stopped",
                ingest_reason or "chat_ingest_rejected",
                host_status=ingest_status,
                host_reason=ingest_reason,
            )

        queue_depth = _queue_depth(inner_host)
        if queue_depth is not None:
            counts["queued"] = queue_depth
        _stage(
            control,
            "waiting",
            counts=counts,
            observed_counts=observed_counts,
        )
        process_once_for_legacy_host = queue_depth is None
        while process_once_for_legacy_host or (queue_depth is not None and queue_depth > 0):
            process_once_for_legacy_host = False
            stop_reason = _requested_stop_reason(control)
            if stop_reason:
                return finish("stopped", stop_reason)
            if float(monotonic_clock()) >= deadline:
                return finish("stopped", _requested_stop_reason(control) or "timeout")
            if _gate_reason() is not None:
                return finish("stopped", "stream_gate_changed")
            try:
                process_now = float(wall_clock())
                _increment(counts, "host_process_calls")
                voice_result = voice_host.process_next(now=process_now)
            except KeyboardInterrupt:
                return finish("stopped", "keyboard_interrupt")
            except Exception:
                return finish("stopped", "voice_host_error")

            host_result = _field(voice_result, "host_result")
            playback_result = _field(voice_result, "playback_result")
            host_status = _safe_reason_code(_field(host_result, "status"), "unknown")
            host_reason = _safe_reason_code(
                _field(host_result, "reason_code"),
                "voice_host_stopped",
            )
            _add_count(counts, "skipped", _field(host_result, "skipped", 0))
            _add_count(counts, "dropped", _field(host_result, "dropped", 0))
            remaining = _field(host_result, "queued", None)
            queue_depth = (
                remaining
                if type(remaining) is int and remaining >= 0
                else _queue_depth(inner_host)
            )
            if queue_depth is not None:
                counts["queued"] = queue_depth
            if playback_result is not None:
                playback_status = _safe_reason_code(
                    _field(playback_result, "status"),
                    "unknown",
                )
                statuses.append(playback_status)
                if playback_status == "delivered":
                    _increment(counts, "turns_delivered")
                    if counts["turns_delivered"] >= max_turns:
                        return finish(
                            "completed",
                            "turn_limit",
                            host_status=host_status,
                            host_reason=host_reason,
                        )
                else:
                    stop_reason = _requested_stop_reason(control)
                    playback_reason = (
                        stop_reason
                        if playback_status == "interrupted" and stop_reason
                        else host_reason
                        if playback_status in {"interrupted", "halted"}
                        else _safe_reason_code(
                            _field(playback_result, "reason_code"),
                            "voice_not_delivered",
                        )
                    )
                    return finish(
                        "stopped",
                        playback_reason,
                        host_status=host_status,
                        host_reason=host_reason,
                    )

            stop_reason = _requested_stop_reason(control)
            if stop_reason:
                return finish(
                    "stopped",
                    stop_reason,
                    host_status=host_status,
                    host_reason=host_reason,
                )

            _stage(
                control,
                "waiting",
                counts=counts,
                observed_counts=observed_counts,
            )

            if playback_result is None:
                if host_status == "idle":
                    break
                if host_status == "generated":
                    return finish(
                        "stopped",
                        "playback_result_missing",
                        host_status=host_status,
                        host_reason=host_reason,
                    )
                if host_status == "stopped":
                    return finish(
                        "stopped",
                        host_reason or _requested_stop_reason(control) or "operator_stop",
                        host_status=host_status,
                        host_reason=host_reason,
                    )
                if host_status in {"held", "failed", "halted", "disabled", "rejected"}:
                    return finish(
                        "stopped",
                        host_reason or "voice_host_stopped",
                        host_status=host_status,
                        host_reason=host_reason,
                    )
                if host_status not in {"skipped"}:
                    return finish(
                        "stopped",
                        "unexpected_host_status",
                        host_status=host_status,
                        host_reason=host_reason,
                    )

            if queue_depth is None or queue_depth == 0:
                break

        _stage(
            control,
            "waiting",
            counts=counts,
            observed_counts=observed_counts,
        )

    return finish("stopped", "poll_limit")


class _CountingPlaybackController:
    def __init__(self, controller: Any, observed_counts: dict[str, int]) -> None:
        self._controller = controller
        self._observed_counts = observed_counts

    def snapshot(self) -> dict[str, Any]:
        return self._controller.snapshot()

    def play_generated(self, artifact: Any, *, attempt_id: str, now: float):
        _increment(self._observed_counts, "playback_dispatches")
        return self._controller.play_generated(artifact, attempt_id=attempt_id, now=now)


class _CountingOutputStream:
    def __init__(self, wrapped: Any, observed_counts: dict[str, int]) -> None:
        self._wrapped = wrapped
        self._observed_counts = observed_counts

    def __enter__(self):
        self._wrapped.__enter__()
        return self

    def __exit__(self, *exc):
        return self._wrapped.__exit__(*exc)

    def write(self, frames: Any):
        _increment(self._observed_counts, "local_audio_sink_writes")
        return self._wrapped.write(frames)


def _shutdown_voice_host(voice_host: Any) -> None:
    shutdown = getattr(voice_host, "shutdown", None)
    if callable(shutdown):
        try:
            shutdown()
        except Exception:
            pass


class _PublicVisualRuntime:
    def __init__(self, stream_session_id: str) -> None:
        from nana.runtime.avatar_intent_gateway import PublicVisualSignalServer
        from nana.runtime.stream_public_visual_signals import PublicVisualSignalStore

        self.store = PublicVisualSignalStore(stream_session_id)
        self._server = PublicVisualSignalServer(self.store)
        self._lock = threading.RLock()
        self._closed = False

    def start(self) -> bool:
        with self._lock:
            if self._closed:
                return False
        return self._server.start() is True

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self.store.shutdown()
        finally:
            self._server.stop()


class _ManagedVoiceHost:
    def __init__(self, voice_host: Any, public_visual_runtime: Any) -> None:
        self._voice_host = voice_host
        self._public_visual_runtime = public_visual_runtime
        self.host = voice_host.host
        self.playback = voice_host.playback
        self.control = voice_host.control
        self._closed = False

    def process_next(self, *, now: float):
        return self._voice_host.process_next(now=now)

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._public_visual_runtime.shutdown()


class _LiveRuntimeSetupError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _load_runtime_config() -> None:
    """Load normal provider configuration only after explicit CLI gates pass."""

    import importlib

    importlib.import_module("nana.config")


def _select_read_credentials(
    read_auth: str,
    *,
    environ: Mapping[str, str] | None = None,
    oauth_token_loader: Callable[[], str | None] | None = None,
):
    """Select exactly the requested existing read credential source."""

    from nana.runtime.youtube_chat_transport import YouTubeCredentials

    env = os.environ if environ is None else environ

    def first(*names: str) -> str | None:
        for name in names:
            value = str(env.get(name) or "").strip()
            if value:
                return value
        return None

    if read_auth == "api-key":
        credentials = YouTubeCredentials(
            api_key=first("NANA_YOUTUBE_API_KEY", "YOUTUBE_API_KEY")
        )
    elif read_auth == "oauth":
        token = first("NANA_YOUTUBE_OAUTH_TOKEN", "YOUTUBE_OAUTH_TOKEN")
        credentials = YouTubeCredentials(oauth_token=token)
    elif read_auth == "oauth-cache":
        if oauth_token_loader is None:
            from nana.runtime.youtube_oauth import get_youtube_oauth_token

            oauth_token_loader = get_youtube_oauth_token
        credentials = YouTubeCredentials(oauth_token=oauth_token_loader())
    else:
        raise ValueError("invalid_read_auth")
    credentials.validate()
    return credentials


def _build_live_runtime(
    *,
    video_id: str,
    read_auth: str,
    observed_counts: dict[str, int],
    control: StreamSessionControl | None = None,
    public_visual_runtime_factory: Callable[[str], Any] | None = None,
) -> tuple[Any, Callable[[str], Any]]:
    """Construct provider/audio owners only after CLI validation and gates."""

    _load_runtime_config()
    changed_gate = _gate_reason()
    if changed_gate:
        raise _LiveRuntimeSetupError(changed_gate)

    from nana.runtime.youtube_chat_transport import (
        YouTubeChatTransportError,
        YouTubeRestChatTransport,
    )

    try:
        credentials = _select_read_credentials(read_auth)
    except YouTubeChatTransportError as exc:
        raise _LiveRuntimeSetupError("youtube_credentials_unavailable") from exc
    transport = YouTubeRestChatTransport(credentials)
    session_digest = sha256(video_id.encode("utf-8")).hexdigest()[:24]
    stream_session_id = f"youtube-voice-{session_digest}"
    visual_runtime = None
    if _public_visual_signals_enabled():
        runtime_factory = public_visual_runtime_factory or _PublicVisualRuntime
        visual_runtime = runtime_factory(stream_session_id)
        if visual_runtime is None or getattr(visual_runtime, "store", None) is None:
            raise _LiveRuntimeSetupError("public_visual_runtime_unavailable")

    def host_factory(live_chat_id: str):
        from nana.brain.llmgate_client import call_llmgate_messages
        from nana.runtime.social_session import SocialSessionCache
        from nana.runtime.stream_cum0_contract import StreamContractLedger
        from nana.runtime.stream_cum2_response import PublicResponseGenerator
        from nana.runtime.stream_cum4_host import YouTubeTextHost
        from nana.runtime.stream_cum5_voice_engine_adapter import VoiceEnginePublicPlaybackPort
        from nana.runtime.stream_cum5_voice_playback import PublicVoicePlaybackController
        from nana.runtime.stream_session_control import SessionPlaybackPort
        from nana.runtime.stream_state import StreamStateCore
        from nana.runtime.stream_voice_host import PublicVoiceHost
        from nana.runtime.youtube_chat_cum1 import YouTubeChatCum1, YouTubeLiveSession
        from nana.runtime.youtube_chat_ingress import YouTubeChatIngress

        policy = StreamStateCore()
        policy.go_live()
        social_session = SocialSessionCache(priority_viewers=())

        def counted_model_call(**kwargs):
            _increment(observed_counts, "model_requests")
            return call_llmgate_messages(**kwargs)

        generator = PublicResponseGenerator(
            caller=counted_model_call,
            session_context=social_session,
        )
        host = YouTubeTextHost(
            cum1=YouTubeChatCum1(
                session=YouTubeLiveSession(live_chat_id, stream_session_id),
                policy_source=policy,
                ingress=YouTubeChatIngress(actionable_limit=1),
                ledger=StreamContractLedger(),
            ),
            generator=generator,
            publisher=None,
            social_session=social_session,
            min_publish_interval_seconds=0,
        )

        def engine_factory():
            from nana.voice.engine import VoiceEngine

            engine = VoiceEngine(avatar_mouth_enabled=False, start_worker=False)
            original_post = engine._voice_http_post

            def counted_tts_post(*args, **kwargs):
                _increment(observed_counts, "tts_provider_requests")
                return original_post(*args, **kwargs)

            engine._voice_http_post = counted_tts_post
            return engine

        def output_stream_factory(**kwargs):
            from nana.voice import lipsync as lipsync_module

            return _CountingOutputStream(
                lipsync_module.sd.OutputStream(**kwargs),
                observed_counts,
            )

        def playback_port_factory():
            port = VoiceEnginePublicPlaybackPort(
                engine_factory=engine_factory,
                output_stream_factory=output_stream_factory,
                visual_signal_store=(
                    None if visual_runtime is None else visual_runtime.store
                ),
            )
            return SessionPlaybackPort(port, control) if control is not None else port

        controller = PublicVoicePlaybackController(
            playback_port_factory=playback_port_factory,
            policy_source=policy,
            active_session_id=stream_session_id,
            delivery_recorder=generator.delivery_recorder,
        )
        voice_host = PublicVoiceHost(
            host=host,
            playback=_CountingPlaybackController(controller, observed_counts),
            control=control,
        )
        if visual_runtime is None:
            return voice_host
        try:
            started = visual_runtime.start() is True
        except Exception:
            started = False
        if not started:
            try:
                visual_runtime.shutdown()
            except Exception:
                pass
            raise _LiveRuntimeSetupError("public_visual_endpoint_unavailable")
        return _ManagedVoiceHost(voice_host, visual_runtime)

    return transport, host_factory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--max-polls", type=int, default=DEFAULT_MAX_POLLS)
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            "Soft polling/admission deadline; does not cancel an in-flight model or audio call."
        ),
    )
    parser.add_argument(
        "--read-auth",
        choices=("api-key", "oauth", "oauth-cache"),
        default="api-key",
        help=(
            "Select API key, bearer OAuth environment, or explicit existing OAuth cache "
            "(which may refresh); never authorizes or publishes."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not _valid_bounds(args.max_turns, args.max_polls, args.timeout_seconds):
        print(json.dumps(_preflight_result("rejected", "invalid_bounds").to_dict()))
        return 2
    canonical_video_id = str(args.video_id or "").strip()
    if not canonical_video_id:
        print(json.dumps(_preflight_result("rejected", "video_id_required").to_dict()))
        return 2
    gate_reason = _gate_reason()
    if gate_reason:
        payload = _preflight_result("blocked", gate_reason).to_dict()
        payload["gates"] = _gate_snapshot()
        print(json.dumps(payload))
        return 2

    observed = {
        "model_requests": 0,
        "playback_dispatches": 0,
        "tts_provider_requests": 0,
        "local_audio_sink_writes": 0,
    }
    try:
        transport, host_factory = _build_live_runtime(
            video_id=canonical_video_id,
            read_auth=args.read_auth,
            observed_counts=observed,
        )
        result = run_bounded_voice_host(
            video_id=canonical_video_id,
            transport=transport,
            voice_host_factory=host_factory,
            max_turns=args.max_turns,
            max_polls=args.max_polls,
            timeout_seconds=args.timeout_seconds,
            observed_counts=observed,
        )
    except _LiveRuntimeSetupError as exc:
        result = VoiceRunnerResult(
            "blocked",
            exc.reason_code,
            model_requests=observed["model_requests"],
            playback_dispatches=observed["playback_dispatches"],
            tts_provider_requests=observed["tts_provider_requests"],
            local_audio_sink_writes=observed["local_audio_sink_writes"],
        )
    except KeyboardInterrupt:
        result = VoiceRunnerResult(
            "stopped",
            "keyboard_interrupt",
            model_requests=observed["model_requests"],
            playback_dispatches=observed["playback_dispatches"],
            tts_provider_requests=observed["tts_provider_requests"],
            local_audio_sink_writes=observed["local_audio_sink_writes"],
        )
    except Exception:
        result = VoiceRunnerResult(
            "stopped",
            "runtime_setup_failed",
            model_requests=observed["model_requests"],
            playback_dispatches=observed["playback_dispatches"],
            tts_provider_requests=observed["tts_provider_requests"],
            local_audio_sink_writes=observed["local_audio_sink_writes"],
        )
    print(json.dumps(result.to_dict(), ensure_ascii=True))
    if result.status == "completed" or result.reason_code in _BENIGN_STOP_REASONS:
        return 0
    return 130 if result.reason_code == "keyboard_interrupt" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_MAX_POLLS",
    "DEFAULT_MAX_TURNS",
    "DEFAULT_TIMEOUT_SECONDS",
    "PHASE",
    "VoiceRunnerResult",
    "main",
    "run_bounded_voice_host",
]
