"""Owner-invoked, one-turn CUM0-CUM5 foreground canary.

This runner is deliberately not a host supervisor. It injects one marked
question, admits only that returned provider message, processes it once,
plays only a fresh full YouTube acknowledgement once, and ingests the
reflected Nana message without another host step.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True


_FLAGS = (
    ("NANA_STREAM_CUM0_ENABLED", "cum0_disabled"),
    ("NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED", "cum1_disabled"),
    ("NANA_STREAM_CUM2_RESPONSE_ENABLED", "cum2_disabled"),
    ("NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED", "cum3_disabled"),
    ("NANA_STREAM_CUM4_HOST_ENABLED", "cum4_disabled"),
    ("NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED", "cum5_disabled"),
)


def _enabled(name: str) -> bool:
    return str(os.getenv(name, "0")).strip().lower() in {"1", "true", "yes", "on"}


def _gate_reason() -> str | None:
    for name, reason in _FLAGS:
        if not _enabled(name):
            return reason
    return None


@contextmanager
def _scoped_cum_flags():
    """Enable all CUM gates only for this foreground owner canary."""

    saved = {name: os.environ.get(name) for name, _reason in _FLAGS}
    if any(_enabled(name) for name, _reason in _FLAGS):
        raise RuntimeError("flags_must_start_off")
    try:
        for name, _reason in _FLAGS:
            os.environ[name] = "1"
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _manifest(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {
        str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


@dataclass(frozen=True)
class IntegrityBaseline:
    data_manifest: dict[str, str]
    avatar_mouth_cursor: int


def _mouth_cursor(mouth: Any) -> int:
    return int(mouth.snapshot().get("cursor") or 0)


def capture_integrity(root: Path, mouth: Any) -> IntegrityBaseline:
    return IntegrityBaseline(_manifest(root / "data"), _mouth_cursor(mouth))


def integrity_unchanged(root: Path, mouth: Any, baseline: IntegrityBaseline) -> dict[str, bool]:
    return {
        "production_data_unchanged": baseline.data_manifest == _manifest(root / "data"),
        "avatar_mouth_unchanged": baseline.avatar_mouth_cursor == _mouth_cursor(mouth),
    }


class RecordingDeliveryRecorder:
    """Capture the production delivery projection while preserving its delegate."""

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.records: list[Any] = []

    def record_delivery(self, record: Any) -> bool:
        accepted = bool(self._delegate.record_delivery(record))
        if accepted:
            self.records.append(record)
        return accepted


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _only_message(page: Any, message_id: str) -> dict[str, Any] | None:
    if not isinstance(page, dict):
        return None
    for item in page.get("items") or ():
        if isinstance(item, dict) and str(item.get("id") or "") == message_id:
            return {
                "items": [item],
                "nextPageToken": page.get("nextPageToken"),
                "pollingIntervalMillis": page.get("pollingIntervalMillis"),
            }
    return None


def _next_read_deadline(page: Any, monotonic_clock: Callable[[], float]) -> float:
    raw_interval = _field(page, "pollingIntervalMillis", 1000)
    try:
        interval_seconds = float(raw_interval) / 1000.0
    except (TypeError, ValueError):
        interval_seconds = 1.0
    if interval_seconds < 0 or interval_seconds != interval_seconds:
        interval_seconds = 1.0
    return float(monotonic_clock()) + interval_seconds


def _wait_for_read_deadline(
    deadline: float,
    *,
    monotonic_clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    remaining = deadline - float(monotonic_clock())
    if remaining > 0:
        sleep(remaining)


def _observed_fields(
    observed_counts: dict[str, int] | None,
    recorder: Any,
    *,
    input_count: int = 0,
    model_count: int = 0,
    publish_count: int = 0,
    tts_count: int = 0,
    sink_write_calls: int = 0,
    playback_status: str | None = None,
) -> dict[str, Any]:
    counts = observed_counts or {}
    records = getattr(recorder, "records", ())
    return {
        "input_count": int(counts.get("input", input_count)),
        "model_count": int(counts.get("model", model_count)),
        "publish_count": int(counts.get("publish", publish_count)),
        "tts_count": int(counts.get("tts", tts_count)),
        "sink_write_calls": int(counts.get("sink", sink_write_calls)),
        "playback_status": playback_status,
        "delivery_revisions": [int(_field(record, "revision")) for record in records],
    }


def _result(status: str, reason_code: str, **values: Any) -> dict[str, Any]:
    result = {
        "status": status,
        "reason_code": reason_code,
        "input_count": 0,
        "model_count": 0,
        "publish_count": 0,
        "tts_count": 0,
        "sink_write_calls": 0,
        "playback_status": None,
        "delivery_revisions": [],
        "reflected_self_ignored": 0,
        "avatar_calls": 0,
        "obs_calls": 0,
        "subtitle_calls": 0,
        "memory_writes": 0,
        "production_data_unchanged": False,
        "avatar_mouth_unchanged": False,
    }
    result.update(values)
    return result


def run_owner_live_turn(
    *,
    video_id: str,
    marker: str,
    transport: Any | None,
    input_sender: Any,
    host_factory: Callable[[str], Any],
    playback_factory: Callable[[Any], Any],
    clock: Callable[[], float] = time.time,
    monotonic_clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    max_polls: int = 30,
    live_chat_id: str | None = None,
    observed_counts: dict[str, int] | None = None,
    delivery_recorder: RecordingDeliveryRecorder | None = None,
) -> dict[str, Any]:
    """Run exactly one eligible owner-live turn through accepted CUM4/CUM5 APIs."""

    reason = _gate_reason()
    recorder = delivery_recorder
    playback_status = None
    if reason:
        return _result("disabled" if reason == "cum5_disabled" else "rejected", reason)
    if transport is None:
        return _result("rejected", "transport_missing")
    if type(max_polls) is not int or max_polls < 1:
        return _result("rejected", "invalid_poll_limit")
    chat_id = str(live_chat_id or "").strip()
    if not chat_id:
        try:
            chat_id = str(transport.resolve_live_chat_id(video_id) or "").strip()
        except Exception:
            return _result("rejected", "live_chat_unavailable")
    if not chat_id:
        return _result("rejected", "live_chat_unavailable")
    try:
        host = host_factory(chat_id)
    except Exception:
        return _result("rejected", "host_setup_failed")

    try:
        bootstrap_page = transport.list_messages(chat_id)
        now = float(clock())
        host.ingest_response(bootstrap_page, received_at=now, now=now, bootstrap=True)
    except Exception:
        return _result("rejected", "bootstrap_failed")
    page_token = _field(bootstrap_page, "nextPageToken")
    next_read_deadline = _next_read_deadline(bootstrap_page, monotonic_clock)

    question = f"{marker} how are you today?"
    try:
        injected = input_sender.send_text(chat_id, question)
    except Exception:
        return _result(
            "rejected", "input_send_failed",
            **_observed_fields(observed_counts, recorder),
        )
    injected_id = str(_field(injected, "provider_message_id") or "").strip()
    if _field(injected, "outcome") != "published" or not injected_id:
        return _result(
            "rejected", "input_not_acknowledged",
            **_observed_fields(observed_counts, recorder, input_count=1),
        )

    selected_page = None
    for poll in range(max_polls):
        try:
            _wait_for_read_deadline(
                next_read_deadline, monotonic_clock=monotonic_clock, sleep=sleep,
            )
            page = transport.list_messages(chat_id, page_token=page_token)
        except Exception:
            return _result(
                "rejected", "question_poll_failed",
                **_observed_fields(observed_counts, recorder, input_count=1),
            )
        page_token = _field(page, "nextPageToken", page_token)
        next_read_deadline = _next_read_deadline(page, monotonic_clock)
        selected_page = _only_message(page, injected_id)
        if selected_page is not None:
            break
        if poll + 1 < max_polls:
            interval_ms = _field(page, "pollingIntervalMillis", 1000)
            sleep(max(1.0, min(10.0, float(interval_ms or 1000) / 1000.0)))
    if selected_page is None:
        return _result(
            "rejected", "injected_question_not_observed",
            **_observed_fields(observed_counts, recorder, input_count=1),
        )

    try:
        now = float(clock())
        host.ingest_response(selected_page, received_at=now, now=now)
        published = host.process_next(now=now)
    except Exception:
        return _result(
            "rejected", "host_process_failed",
            **_observed_fields(observed_counts, recorder, input_count=1),
        )
    publish_result = _field(published, "publish_result")
    if (
        _field(published, "status") != "published"
        or _field(published, "action") != "full_reply"
        or _field(publish_result, "is_fresh_youtube_ack") is not True
        or _field(published, "response_artifact") is None
    ):
        return _result(
            "rejected", "publish_not_fresh",
            **_observed_fields(observed_counts, recorder, input_count=1),
        )

    try:
        controller = playback_factory(host)
        playback = controller.play_published(
            published.response_artifact,
            publish_result,
            now=float(clock()),
        )
    except Exception:
        return _result(
            "rejected", "voice_dispatch_failed",
            **_observed_fields(
                observed_counts, recorder, input_count=1, model_count=1, publish_count=1,
            ),
        )
    recorder = delivery_recorder or getattr(controller, "_delivery_recorder", None)
    playback_status = _field(playback, "status")
    if _field(playback, "status") != "delivered":
        return _result(
            "rejected", "voice_not_delivered_" + str(_field(playback, "reason_code") or "unknown"),
            **_observed_fields(
                observed_counts, recorder, input_count=1, model_count=1, publish_count=1,
                playback_status=playback_status,
            ),
        )

    provider_message_id = str(_field(publish_result, "provider_message_id") or "").strip()
    reflected = None
    for poll in range(max_polls):
        try:
            _wait_for_read_deadline(
                next_read_deadline, monotonic_clock=monotonic_clock, sleep=sleep,
            )
            page = transport.list_messages(chat_id, page_token=page_token)
        except Exception:
            return _result(
                "rejected", "reflection_poll_failed",
                **_observed_fields(
                    observed_counts, recorder, input_count=1, model_count=1, publish_count=1,
                    tts_count=1, playback_status=playback_status,
                ),
            )
        page_token = _field(page, "nextPageToken", page_token)
        next_read_deadline = _next_read_deadline(page, monotonic_clock)
        reflected = _only_message(page, provider_message_id)
        if reflected is not None:
            break
        if poll + 1 < max_polls:
            interval_ms = _field(page, "pollingIntervalMillis", 1000)
            sleep(max(1.0, min(10.0, float(interval_ms or 1000) / 1000.0)))
    if reflected is None:
        return _result(
            "rejected", "reflection_not_observed",
            **_observed_fields(
                observed_counts, recorder, input_count=1, model_count=1, publish_count=1,
                tts_count=1, playback_status=playback_status,
            ),
        )
    try:
        now = float(clock())
        reflection = host.ingest_response(reflected, received_at=now, now=now)
    except Exception:
        return _result(
            "rejected", "reflection_ingest_failed",
            **_observed_fields(
                observed_counts, recorder, input_count=1, model_count=1, publish_count=1,
                tts_count=1, playback_status=playback_status,
            ),
        )
    ignored = int(_field(reflection, "ignored_self", 0) or 0)
    if ignored != 1:
        return _result(
            "rejected", "reflection_not_ignored",
            **_observed_fields(
                observed_counts, recorder, input_count=1, model_count=1, publish_count=1,
                tts_count=1, playback_status=playback_status,
            ),
        )

    return _result(
        "accepted", "one_turn_delivered",
        **_observed_fields(
            observed_counts, recorder, input_count=1, model_count=1, publish_count=1,
            tts_count=1, playback_status=playback_status,
        ),
        reflected_self_ignored=ignored,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--marker", default="NANA-CUM5-LIVE-01")
    parser.add_argument("--max-polls", type=int, default=30)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    saved_flags = {name: os.environ.get(name) for name, _reason in _FLAGS}
    result = _result("rejected", "canary_not_run")
    engine = None
    original_model_post = None
    mouth = None
    integrity_baseline = None
    try:
        with _scoped_cum_flags():
            import requests
            from nana.brain import llmgate_client
            from nana.runtime.avatar_mouth_stream import get_avatar_mouth_stream
            from nana.runtime.social_session import SocialSessionCache
            from nana.runtime.stream_cum0_contract import StreamContractLedger
            from nana.runtime.stream_cum2_response import PublicResponseGenerator
            from nana.runtime.stream_cum3_youtube_publish import YouTubeCum3Publisher, YouTubeLiveChatSender
            from nana.runtime.stream_cum4_host import YouTubeTextHost
            from nana.runtime.stream_cum5_voice_engine_adapter import VoiceEnginePublicPlaybackPort
            from nana.runtime.stream_cum5_voice_playback import PublicVoicePlaybackController
            from nana.runtime.stream_state import StreamStateCore
            from nana.runtime.youtube_chat_cum1 import YouTubeChatCum1, YouTubeLiveSession
            from nana.runtime.youtube_chat_ingress import YouTubeChatIngress
            from nana.runtime.youtube_chat_transport import YouTubeCredentials, YouTubeRestChatTransport
            from nana.runtime.youtube_oauth import get_youtube_oauth_token
            from nana.voice import lipsync as lipsync_module
            from nana.voice.engine import VoiceEngine

            mouth = get_avatar_mouth_stream()
            integrity_baseline = capture_integrity(root, mouth)

            counts = {"input": 0, "model": 0, "publish": 0, "tts": 0, "sink": 0}

            class CountingSession(requests.Session):
                def __init__(self, count_key: str):
                    super().__init__()
                    self.count_key = count_key

                def post(self, *positional, **kwargs):
                    counts[self.count_key] += 1
                    if counts[self.count_key] > 1:
                        raise RuntimeError("one_request_bound_exceeded")
                    return super().post(*positional, **kwargs)

            class CountingOutputStream:
                def __init__(self, wrapped):
                    self.wrapped = wrapped

                def __enter__(self):
                    self.wrapped.__enter__()
                    return self

                def __exit__(self, *exc):
                    return self.wrapped.__exit__(*exc)

                def write(self, frame):
                    counts["sink"] += 1
                    return self.wrapped.write(frame)

            token = get_youtube_oauth_token()
            if not token:
                raise RuntimeError("oauth_unavailable")
            read_transport = YouTubeRestChatTransport(YouTubeCredentials(oauth_token=token))
            live_chat_id = read_transport.resolve_live_chat_id(args.video_id)
            policy = StreamStateCore()
            policy.go_live()
            social_session = SocialSessionCache(priority_viewers=())
            generator = PublicResponseGenerator(session_context=social_session)
            host = YouTubeTextHost(
                cum1=YouTubeChatCum1(
                    session=YouTubeLiveSession(live_chat_id, "cum5-live-" + args.video_id),
                    policy_source=policy, ingress=YouTubeChatIngress(actionable_limit=1), ledger=StreamContractLedger(),
                ),
                generator=generator,
                publisher=YouTubeCum3Publisher(
                    sender=YouTubeLiveChatSender(token_provider=lambda: token, session=CountingSession("publish")),
                    min_send_interval_seconds=0,
                ),
                social_session=social_session, min_publish_interval_seconds=0,
            )
            original_model_post = llmgate_client._http_post

            def counted_model_post(*positional, **kwargs):
                counts["model"] += 1
                if counts["model"] > 1:
                    raise RuntimeError("one_model_bound_exceeded")
                return original_model_post(*positional, **kwargs)

            llmgate_client._http_post = counted_model_post
            engine = VoiceEngine(avatar_mouth_enabled=False, start_worker=False)
            original_tts_post = engine._voice_http_post

            def counted_tts_post(*positional, **kwargs):
                counts["tts"] += 1
                if counts["tts"] > 1:
                    raise RuntimeError("one_tts_bound_exceeded")
                return original_tts_post(*positional, **kwargs)

            engine._voice_http_post = counted_tts_post
            recording_recorder = RecordingDeliveryRecorder(generator.delivery_recorder)
            controller = PublicVoicePlaybackController(
                playback_port_factory=lambda: VoiceEnginePublicPlaybackPort(
                    engine_factory=lambda: engine,
                    output_stream_factory=lambda **kwargs: CountingOutputStream(lipsync_module.sd.OutputStream(**kwargs)),
                ),
                policy_source=policy,
                active_session_id="cum5-live-" + args.video_id,
                delivery_recorder=recording_recorder,
            )
            result = run_owner_live_turn(
                video_id=args.video_id, marker=args.marker, transport=read_transport,
                input_sender=YouTubeLiveChatSender(token_provider=lambda: token, session=CountingSession("input")),
                host_factory=lambda _chat: host, playback_factory=lambda _host: controller,
                max_polls=args.max_polls, live_chat_id=live_chat_id, observed_counts=counts,
                delivery_recorder=recording_recorder,
            )
            if result["status"] == "accepted" and not (
                counts["input"] == counts["model"] == counts["publish"] == counts["tts"] == 1
                and counts["sink"] > 0
                and result["delivery_revisions"] == [0, 1, 2, 3]
            ):
                result["status"] = "rejected"
                result["reason_code"] = "hard_bound_verification_failed"
    except Exception as exc:
        result = _result("rejected", type(exc).__name__)
    finally:
        if original_model_post is not None:
            try:
                from nana.brain import llmgate_client
                llmgate_client._http_post = original_model_post
            except Exception:
                pass
        if engine is not None:
            try:
                engine.shutdown()
            except Exception:
                pass
        integrity = (
            integrity_unchanged(root, mouth, integrity_baseline)
            if mouth is not None and integrity_baseline is not None
            else {"production_data_unchanged": False, "avatar_mouth_unchanged": False}
        )
        result.update(
            flags_restored=all(os.environ.get(name) == value for name, value in saved_flags.items()),
            avatar_calls=0,
            obs_calls=0,
            subtitle_calls=0,
            memory_writes=0,
            **integrity,
        )
        print(json.dumps(result, ensure_ascii=True), flush=True)
    return 0 if (
        result.get("status") == "accepted"
        and result.get("flags_restored")
        and result.get("production_data_unchanged")
        and result.get("avatar_mouth_unchanged")
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
