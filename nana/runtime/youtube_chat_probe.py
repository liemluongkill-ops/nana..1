"""Command-line receive-only probe for YouTube Live Chat.

This is an explicit diagnostic tool, not a Nana startup worker.  It resolves a
live chat, feeds responses into :mod:`youtube_chat_ingress`, and prints bounded
JSON lines.  It never calls an LLM, TTS, playback, OBS, VTS, Discord, or the
Nana application runtime.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import statistics
import time
from typing import Any, Callable, Iterable

from nana.runtime.youtube_chat_ingress import IngestResult, YouTubeChatIngress
from nana.runtime.youtube_chat_transport import (
    LiveChatProbeConfig,
    YouTubeChatTransportError,
    YouTubeCredentials,
    YouTubeRestChatTransport,
    YouTubeStreamListTransport,
    ingest_response_into_probe,
)


PHASE = "STREAM-YT-PROBE-1"
DEFAULT_REST_INTERVAL_SECONDS = 1.0


@dataclass
class ProbeStats:
    responses: int = 0
    history_events: int = 0
    new_events: int = 0
    duplicate_events: int = 0
    invalid_events: int = 0
    queued_actionable: int = 0
    dropped_actionable: int = 0
    reconnects: int = 0
    errors: int = 0
    delivery_ms: list[float] | None = None

    def __post_init__(self) -> None:
        if self.delivery_ms is None:
            self.delivery_ms = []

    def record(self, result: IngestResult) -> None:
        self.responses += 1
        self.history_events += result.history_count
        self.new_events += result.new_count
        self.duplicate_events += result.duplicate_count
        self.invalid_events += result.invalid_count
        self.queued_actionable += result.queued_actionable
        self.dropped_actionable += result.dropped_actionable
        self.delivery_ms.extend(
            event.delivery_ms
            for event in result.new_events
            if event.delivery_ms is not None
        )

    def summary(self, ingress: YouTubeChatIngress) -> dict[str, Any]:
        values = sorted(self.delivery_ms or [])
        distribution: dict[str, Any] = {"count": len(values)}
        if values:
            distribution.update(
                {
                    "min_ms": round(values[0], 3),
                    "p50_ms": round(statistics.median(values), 3),
                    "p95_ms": round(_percentile(values, 0.95), 3),
                    "max_ms": round(values[-1], 3),
                }
            )
        context = ingress.build_context()
        return {
            "phase": PHASE,
            "kind": "summary",
            "responses": self.responses,
            "history_events": self.history_events,
            "new_events": self.new_events,
            "duplicate_events": self.duplicate_events,
            "invalid_events": self.invalid_events,
            "queued_actionable": self.queued_actionable,
            "dropped_actionable": self.dropped_actionable,
            "reconnects": self.reconnects,
            "errors": self.errors,
            "delivery_ms": distribution,
            "context": {
                "recent_count": context["recent_count"],
                "realtime_count": context["realtime_count"],
                "pending_count": context["pending_count"],
            },
            "cursor": {
                "next_page_token_present": context["next_page_token_present"],
                "polling_interval_ms": context["polling_interval_ms"],
            },
            "safety": {
                "network_call": True,
                "llm_call": False,
                "tts_call": False,
                "obs_call": False,
                "vts_call": False,
                "discord_call": False,
                "nana_runtime_call": False,
            },
        }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _emit(payload: dict[str, Any], output: Callable[[str], None]) -> None:
    output(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


# Keep output shaping separate from the public result object while avoiding a
# large raw event dump in the response line.
def _result_metadata(result: IngestResult) -> dict[str, Any]:
    return {
        "history_count": result.history_count,
        "new_count": result.new_count,
        "duplicate_count": result.duplicate_count,
        "invalid_count": result.invalid_count,
        "queued_actionable": result.queued_actionable,
        "dropped_actionable": result.dropped_actionable,
        "next_page_token_present": result.next_page_token_present,
        "polling_interval_ms": result.polling_interval_ms,
    }


def _ingest_one(
    ingress: YouTubeChatIngress,
    response: Any,
    *,
    bootstrap: bool,
    live_chat_id: str,
    stats: ProbeStats,
    output: Callable[[str], None],
) -> None:
    result = ingest_response_into_probe(
        ingress,
        response,
        received_at=time.time(),
        bootstrap=bootstrap,
        live_chat_id=live_chat_id,
    )
    stats.record(result)
    _emit(
        {
            "phase": PHASE,
            "kind": "response",
            "bootstrap": bootstrap,
            **_result_metadata(result),
        },
        output,
    )
    for event in result.new_events:
        _emit({"phase": PHASE, "kind": "viewer_event", "event": event.to_dict()}, output)


def run_probe(
    config: LiveChatProbeConfig,
    credentials: YouTubeCredentials,
    *,
    ingress: YouTubeChatIngress | None = None,
    rest_transport: Any | None = None,
    stream_transport: Any | None = None,
    output: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run an explicitly requested probe and return its sanitized summary.

    ``rest_transport`` and ``stream_transport`` are injectable for offline
    tests.  Production callers leave them unset.
    """

    config.validate()
    credentials.validate()
    active_ingress = ingress or YouTubeChatIngress()
    rest = rest_transport or YouTubeRestChatTransport(
        credentials,
        timeout_seconds=config.request_timeout_seconds,
    )
    live_chat_id = rest.resolve_live_chat_id(config.video_id)
    stats = ProbeStats()
    _emit(
        {
            "phase": PHASE,
            "kind": "connected",
            "transport": config.transport,
            "video_id_present": bool(config.video_id.strip()),
            "live_chat_id_present": bool(str(live_chat_id).strip()),
            "bootstrap_history_is_context_only": True,
            "safety": {
                "llm_call": False,
                "tts_call": False,
                "obs_call": False,
                "nana_runtime_call": False,
            },
        },
        output,
    )

    started = clock()
    deadline = started + config.duration_seconds
    bootstrap = True
    page_token: str | None = None

    if config.transport == "rest":
        while clock() < deadline and (
            config.max_responses <= 0 or stats.responses < config.max_responses
        ):
            response = rest.list_messages(live_chat_id, page_token=page_token)
            _ingest_one(
                active_ingress,
                response,
                bootstrap=bootstrap,
                live_chat_id=live_chat_id,
                stats=stats,
                output=output,
            )
            bootstrap = False
            page_token = active_ingress.next_page_token
            if (
                config.max_responses > 0
                and stats.responses >= config.max_responses
            ):
                break
            if active_ingress.build_context().get("offline_at_present"):
                break
            interval = active_ingress.build_context().get("polling_interval_ms")
            wait_seconds = (
                max(0.1, float(interval) / 1000.0)
                if interval is not None
                else DEFAULT_REST_INTERVAL_SECONDS
            )
            remaining = max(0.0, deadline - clock())
            if remaining <= 0:
                break
            sleep(min(wait_seconds, remaining))
    else:
        if stream_transport is None:
            stream_transport = YouTubeStreamListTransport(credentials)
        reconnects = 0
        while clock() < deadline and (
            config.max_responses <= 0 or stats.responses < config.max_responses
        ):
            try:
                responses: Iterable[Any] = stream_transport.iter_responses(
                    live_chat_id,
                    page_token=page_token,
                )
                for response in responses:
                    if clock() >= deadline or (
                        config.max_responses > 0 and stats.responses >= config.max_responses
                    ):
                        break
                    _ingest_one(
                        active_ingress,
                        response,
                        bootstrap=bootstrap,
                        live_chat_id=live_chat_id,
                        stats=stats,
                        output=output,
                    )
                    bootstrap = False
                    page_token = active_ingress.next_page_token
                    if active_ingress.build_context().get("offline_at_present"):
                        break
                if active_ingress.build_context().get("offline_at_present"):
                    break
                if clock() >= deadline or (
                    config.max_responses > 0 and stats.responses >= config.max_responses
                ):
                    break
            except YouTubeChatTransportError as exc:
                stats.errors += 1
                _emit(
                    {
                        "phase": PHASE,
                        "kind": "transport_error",
                        "error": str(exc),
                        "cursor_present": bool(page_token),
                    },
                    output,
                )
            reconnects += 1
            stats.reconnects = reconnects
            if reconnects > config.reconnect_limit:
                break
            remaining = max(0.0, deadline - clock())
            if remaining <= 0:
                break
            sleep(min(max(0.0, config.reconnect_backoff_seconds), remaining))

    summary = stats.summary(active_ingress)
    _emit(summary, output)
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Receive-only YouTube Live Chat probe")
    parser.add_argument("--video-id", default=os.environ.get("NANA_YOUTUBE_VIDEO_ID", ""))
    parser.add_argument("--transport", choices=("stream", "rest"), default="stream")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--max-responses", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--reconnect-limit", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        config = LiveChatProbeConfig(
            video_id=args.video_id,
            transport=args.transport,
            duration_seconds=args.seconds,
            max_responses=args.max_responses,
            request_timeout_seconds=args.timeout,
            reconnect_limit=args.reconnect_limit,
        )
        credentials = YouTubeCredentials.from_environment()
        run_probe(config, credentials)
    except YouTubeChatTransportError as exc:
        print(json.dumps({"phase": PHASE, "kind": "blocked", "error": str(exc)}))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"phase": PHASE, "kind": "stopped", "reason": "keyboard_interrupt"}))
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
