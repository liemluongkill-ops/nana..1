"""Physical display-only smoke for the durable Presence session.

The tool starts a temporary Core listener with the bearer token already
provisioned on the board, sends only bounded display commands, and requires an
exact acknowledgement for every state/event. It never provisions NVS, captures
speech for processing, or starts audio playback.
"""

from __future__ import annotations

import argparse
import asyncio
from getpass import getpass
from pathlib import Path
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = PROJECT_ROOT.parent
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))


from nana.runtime.presence_session_server import (
    PresenceCaptureOutcome,
    PresenceDisplayResult,
    PresenceSessionServer,
)


DISPLAY_SEQUENCE = (
    ("state", "neutral", "neutral eyes"),
    ("state", "listening", "listening eyes"),
    ("event", "double_blink", "double blink"),
    ("state", "happy", "happy eyes"),
    ("event", "wink_right", "right-eye wink"),
    ("state", "core_loading", "Core loading indicator"),
    ("state", "mic_unclear", "microphone unclear indicator"),
    ("state", "neutral", "return to neutral"),
)


async def _reject_audio_capture(_capture) -> PresenceCaptureOutcome:
    return PresenceCaptureOutcome("skipped", "display_smoke_only")


async def _wait_for_display_node(
    server: PresenceSessionServer,
    *,
    timeout: float,
) -> dict:
    deadline = time.monotonic() + timeout
    last_snapshot: dict = {}
    while time.monotonic() < deadline:
        last_snapshot = server.snapshot()
        capabilities = dict(last_snapshot.get("last_capabilities") or {})
        if (
            last_snapshot.get("active_sessions") == 1
            and last_snapshot.get("last_profile") == "presence_session"
            and capabilities.get("display") is True
        ):
            return last_snapshot
        await asyncio.sleep(0.1)
    raise TimeoutError(
        "timed out waiting for a display-capable Presence node: "
        f"state={last_snapshot.get('state')} "
        f"active={last_snapshot.get('active_sessions', 0)} "
        f"profile={last_snapshot.get('last_profile')} "
        f"capabilities={last_snapshot.get('last_capabilities')} "
        f"last_error={last_snapshot.get('last_error')}"
    )


async def _send_display_step(
    server: PresenceSessionServer,
    *,
    kind: str,
    tag: str,
) -> PresenceDisplayResult:
    if kind == "state":
        return await server.set_display_state_async(tag)
    if kind == "event":
        return await server.trigger_display_event_async(tag)
    raise ValueError(f"unsupported display command kind: {kind}")


async def _run(args: argparse.Namespace) -> None:
    token = getpass("Presence bearer token: ").strip()
    if not token:
        raise ValueError("Presence bearer token must not be empty")

    server = PresenceSessionServer(
        host=args.host,
        port=args.port,
        token=token,
        heartbeat_seconds=5.0,
        timeout_seconds=15.0,
        hello_timeout_seconds=5.0,
        audio_uplink_handler=_reject_audio_capture,
    )
    server_task = asyncio.create_task(server.run())

    try:
        await server.wait_started()
        if server_task.done():
            await server_task
        print(
            f"Display smoke listener ready on {args.host}:{server.bound_port}; "
            "waiting for the board..."
        )
        connected = await _wait_for_display_node(
            server,
            timeout=args.connect_timeout,
        )
        print(
            "Connected | "
            f"device={connected.get('last_device_id')} | "
            f"firmware={connected.get('last_firmware')} | "
            f"display={connected.get('last_capabilities', {}).get('display')}"
        )
        print("Watch the TFT. No microphone, TTS, or speaker playback is used.")

        for index, (kind, tag, label) in enumerate(DISPLAY_SEQUENCE, start=1):
            result = await _send_display_step(
                server,
                kind=kind,
                tag=tag,
            )
            print(
                f"[{index}/{len(DISPLAY_SEQUENCE)}] ACK | "
                f"request={result.request_id} | kind={result.kind} | "
                f"tag={result.tag} | status={result.status} | "
                f"reason={result.reason} | view={label}"
            )
            await asyncio.sleep(args.step_seconds)

        snapshot = server.snapshot()
        expected = len(DISPLAY_SEQUENCE)
        accepted = int(snapshot.get("display_accepted", 0))
        rejected = int(snapshot.get("display_rejected", 0))
        failed = int(snapshot.get("display_failed", 0))
        if accepted != expected or rejected or failed:
            raise RuntimeError(
                "display counters did not close cleanly: "
                f"expected={expected} accepted={accepted} "
                f"rejected={rejected} failed={failed}"
            )

        print(
            "Summary | "
            f"sent={snapshot.get('display_sent', 0)} | accepted={accepted} | "
            f"rejected={rejected} | failed={failed} | "
            f"last={snapshot.get('last_display_kind')}:"
            f"{snapshot.get('last_display_tag')} | "
            f"active={snapshot.get('active_sessions', 0)} | "
            f"heartbeats={snapshot.get('heartbeats', 0)} | "
            f"errors={snapshot.get('errors', 0)}"
        )
        print("smoke_presence_session_display: PASS (8/8 protocol ACKs)")
    finally:
        server.stop()
        await asyncio.wait_for(server_task, timeout=5.0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a display-only physical smoke against the durable Presence "
            "session without provisioning, microphone processing, or playback."
        )
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--connect-timeout", type=float, default=45.0)
    parser.add_argument("--step-seconds", type=float, default=1.5)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.connect_timeout <= 0.0:
        raise SystemExit("--connect-timeout must be positive")
    if not 0.25 <= args.step_seconds <= 10.0:
        raise SystemExit("--step-seconds must be between 0.25 and 10 seconds")
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("Display smoke stopped by operator.")
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
