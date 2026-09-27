"""Run the default-off Stream V1 CUM4 text host."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.dont_write_bytecode = True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video-id", default=os.getenv("NANA_YOUTUBE_VIDEO_ID", ""))
    parser.add_argument("--max-cycles", type=int, default=0)
    parser.add_argument("--reconnect-limit", type=int, default=3)
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    if not str(args.video_id).strip():
        print(json.dumps({"status": "blocked", "reason_code": "video_id_required"}))
        return 2

    from nana.runtime.stream_cum0_contract import is_cum0_enabled
    from nana.runtime.stream_cum2_response import PublicResponseGenerator, is_cum2_response_enabled
    from nana.runtime.stream_cum3_youtube_publish import (
        YouTubeCum3Publisher,
        YouTubeLiveChatSender,
        is_cum3_youtube_output_enabled,
    )
    from nana.runtime.stream_cum4_host import YouTubeTextHost, is_cum4_host_enabled, run_text_host
    from nana.runtime.stream_state import StreamStateCore
    from nana.runtime.social_session import SocialSessionCache
    from nana.runtime.youtube_chat_cum1 import (
        YouTubeChatCum1,
        YouTubeLiveSession,
        is_cum1_youtube_ingress_enabled,
    )
    from nana.runtime.youtube_chat_ingress import YouTubeChatIngress
    from nana.runtime.youtube_chat_transport import YouTubeCredentials, YouTubeRestChatTransport
    from nana.runtime.youtube_oauth import get_youtube_oauth_token

    gates = {
        "cum0": is_cum0_enabled(),
        "cum1": is_cum1_youtube_ingress_enabled(),
        "cum2": is_cum2_response_enabled(),
        "cum3": is_cum3_youtube_output_enabled(),
        "cum4": is_cum4_host_enabled(),
    }
    if not all(gates.values()):
        print(json.dumps({"status": "blocked", "reason_code": "stream_gate_disabled", "gates": gates}))
        return 2
    oauth = get_youtube_oauth_token()
    if not oauth:
        print(json.dumps({"status": "blocked", "reason_code": "youtube_oauth_unavailable"}))
        return 2

    transport = YouTubeRestChatTransport(YouTubeCredentials(oauth_token=oauth))
    try:
        live_chat_id = transport.resolve_live_chat_id(str(args.video_id).strip())
    except Exception as exc:
        print(json.dumps({"status": "blocked", "reason_code": type(exc).__name__}))
        return 2

    policy = StreamStateCore()
    policy.go_live()
    session_id = "youtube-host-" + str(args.video_id).strip()
    cum1 = YouTubeChatCum1(
        session=YouTubeLiveSession(live_chat_id, session_id),
        policy_source=policy,
        ingress=YouTubeChatIngress(actionable_limit=1),
    )
    social = SocialSessionCache()
    host = YouTubeTextHost(
        cum1=cum1,
        generator=PublicResponseGenerator(session_context=SocialSessionCache()),
        publisher=YouTubeCum3Publisher(sender=YouTubeLiveChatSender()),
        social_session=social,
    )
    result = run_text_host(
        transport=transport,
        live_chat_id=live_chat_id,
        host=host,
        max_cycles=max(0, int(args.max_cycles)),
        reconnect_limit=max(0, int(args.reconnect_limit)),
    )
    snapshot = host.snapshot()
    print(json.dumps({
        "status": result.status,
        "reason_code": result.reason_code,
        "cycles": result.cycles,
        "reconnects": result.reconnects,
        "queued": snapshot["queued"],
        "halted": snapshot["halted"],
        "stats": snapshot["stats"],
        "tts": False,
        "avatar": False,
        "obs": False,
        "memory_write": False,
    }))
    return 0 if result.status in {"completed", "stopped"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
