"""Owner-invoked bounded live canary for the Stream V1 CUM4 text host."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-id", required=True)
    parser.add_argument("--marker", default="NANA-CUM4-LIVE-01")
    parser.add_argument("--seconds", type=float, default=180.0)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    root = Path(__file__).resolve().parents[1]

    def manifest():
        return {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (root / "data").glob("*")
            if path.is_file()
        }

    before = manifest()

    import requests
    import nana.config
    from nana.brain import llmgate_client
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.stream_cum0_contract import StreamContractLedger
    from nana.runtime.stream_cum2_response import PublicResponseGenerator
    from nana.runtime.stream_cum3_youtube_publish import (
        YouTubeCum3Publisher,
        YouTubeLiveChatSender,
    )
    from nana.runtime.stream_cum4_host import YouTubeTextHost
    from nana.runtime.stream_state import StreamStateCore
    from nana.runtime.youtube_chat_cum1 import YouTubeChatCum1, YouTubeLiveSession
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress
    from nana.runtime.youtube_chat_transport import YouTubeCredentials, YouTubeRestChatTransport
    from nana.runtime.youtube_oauth import get_youtube_oauth_token

    statuses = {"read": [], "input": [], "model": [], "publish": []}

    class ReadSession(requests.Session):
        def get(self, *positional, **kwargs):
            response = super().get(*positional, **kwargs)
            statuses["read"].append(response.status_code)
            return response

    class InputSession(requests.Session):
        def post(self, *positional, **kwargs):
            response = super().post(*positional, **kwargs)
            statuses["input"].append(response.status_code)
            return response

    class PublishSession(requests.Session):
        def post(self, *positional, **kwargs):
            if statuses["publish"]:
                raise RuntimeError("one_host_publish_only")
            response = super().post(*positional, **kwargs)
            statuses["publish"].append(response.status_code)
            return response

    original_model_post = llmgate_client._http_post

    def model_post(*positional, **kwargs):
        response = original_model_post(*positional, **kwargs)
        statuses["model"].append(response.status_code)
        return response

    llmgate_client._http_post = model_post
    stream_flags = (
        "NANA_STREAM_CUM0_ENABLED",
        "NANA_STREAM_CUM1_YOUTUBE_INGRESS_ENABLED",
        "NANA_STREAM_CUM2_RESPONSE_ENABLED",
        "NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED",
        "NANA_STREAM_CUM4_HOST_ENABLED",
    )
    saved_flags = {name: os.environ.get(name) for name in stream_flags}
    result_data = {"status": "timeout", "reason_code": "canary_timeout"}

    try:
        token = get_youtube_oauth_token()
        if not token:
            raise RuntimeError("oauth_unavailable")
        read_transport = YouTubeRestChatTransport(
            YouTubeCredentials(oauth_token=token),
            session=ReadSession(),
        )
        live_chat_id = read_transport.resolve_live_chat_id(args.video_id)
        policy = StreamStateCore()
        policy.go_live()
        cum1 = YouTubeChatCum1(
            session=YouTubeLiveSession(live_chat_id, "cum4-canary-" + args.video_id),
            policy_source=policy,
            ingress=YouTubeChatIngress(actionable_limit=1),
            ledger=StreamContractLedger(),
        )
        host = YouTubeTextHost(
            cum1=cum1,
            generator=PublicResponseGenerator(session_context=SocialSessionCache()),
            publisher=YouTubeCum3Publisher(
                sender=YouTubeLiveChatSender(
                    token_provider=lambda: token,
                    session=PublishSession(),
                ),
                min_send_interval_seconds=0,
            ),
            social_session=SocialSessionCache(priority_viewers=()),
            min_publish_interval_seconds=0,
        )
        for name in stream_flags:
            os.environ[name] = "1"

        page = read_transport.list_messages(live_chat_id)
        now = time.time()
        host.ingest_response(page, received_at=now, now=now, bootstrap=True)
        page_token = page.get("nextPageToken")

        injector = YouTubeLiveChatSender(
            token_provider=lambda: token,
            session=InputSession(),
        )
        skip_input = injector.send_text(live_chat_id, "🙂")
        full_input = injector.send_text(
            live_chat_id,
            args.marker + " hom nay ban thay the nao?",
        )
        if skip_input.outcome != "published" or full_input.outcome != "published":
            raise RuntimeError("canary_input_failed")
        print(json.dumps({"state": "READY", "marker": args.marker}), flush=True)

        deadline = time.monotonic() + max(10.0, float(args.seconds))
        while time.monotonic() < deadline:
            interval = max(1.0, min(10.0, float(page.get("pollingIntervalMillis") or 2000) / 1000 + 0.3))
            time.sleep(interval)
            page = read_transport.list_messages(live_chat_id, page_token=page_token)
            page_token = page.get("nextPageToken") or page_token
            now = time.time()
            host.handle_response(page, received_at=now, now=now)
            snapshot = host.snapshot()
            stats = snapshot["stats"]
            if stats["published"] > 1:
                raise RuntimeError("publish_bound_exceeded")
            if snapshot["halted"]:
                result_data = {
                    "status": "halted",
                    "reason_code": snapshot["halt_reason"],
                    "stats": stats,
                }
                break
            if stats["skipped"] >= 1 and stats["published"] == 1 and stats["self_ignored"] >= 1:
                result_data = {
                    "status": "accepted",
                    "reason_code": "cum4_live_selection_pass",
                    "stats": stats,
                    "queued": snapshot["queued"],
                }
                break
    except Exception as exc:
        result_data = {
            "status": "error",
            "reason_code": type(exc).__name__,
        }
    finally:
        llmgate_client._http_post = original_model_post
        for name, value in saved_flags.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    result_data.update(
        http_statuses=statuses,
        production_data_unchanged=before == manifest(),
        flags_restored=all(os.environ.get(name) == value for name, value in saved_flags.items()),
        max_host_publishes=1,
        tts=False,
        avatar=False,
        obs=False,
        memory_write=False,
    )
    print(json.dumps(result_data), flush=True)
    return 0 if (
        result_data["status"] == "accepted"
        and result_data["production_data_unchanged"]
        and result_data["flags_restored"]
    ) else 2


if __name__ == "__main__":
    raise SystemExit(main())
