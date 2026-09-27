"""Live VoiceEngine -> Presence session -> ESP32 speaker acceptance smoke."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import secrets
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = PROJECT_ROOT.parent
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))


from nana.runtime.presence_session_server import (
    PresenceCaptureOutcome,
    PresenceSessionServer,
)
from nana.voice.engine import VoiceEngine

from provision_presence_session import build_set_command, serial_session, validate_uri


async def _wait_for(predicate, *, timeout: float, description: str):
    deadline = time.monotonic() + timeout
    last_value = None
    while time.monotonic() < deadline:
        last_value = predicate()
        if last_value:
            return last_value
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {description}: {last_value}")


async def _run(args: argparse.Namespace) -> None:
    uri = validate_uri(args.uri)
    token = secrets.token_urlsafe(32)

    async def reject_unsolicited_capture(_capture):
        return PresenceCaptureOutcome("skipped", "downlink_smoke_only")

    server = PresenceSessionServer(
        host=args.host,
        port=args.server_port,
        token=token,
        heartbeat_seconds=1.0,
        timeout_seconds=4.0,
        hello_timeout_seconds=5.0,
        audio_uplink_handler=reject_unsolicited_capture,
    )
    server_task = asyncio.create_task(server.run())
    voice: VoiceEngine | None = None
    credentials_cleared = False

    try:
        print("[1/4] Start authenticated Presence listener")
        await server.wait_started()
        if server_task.done():
            await server_task
        print(f"  PASS listener | port={server.bound_port}")

        print("[2/4] Provision an ephemeral node session")
        result = await asyncio.to_thread(
            serial_session,
            port=args.serial_port,
            command=build_set_command(uri, token),
            timeout=args.serial_timeout,
        )
        if result != 0:
            raise RuntimeError("board did not accept ephemeral session config")
        connected = await _wait_for(
            lambda: server.snapshot()
            if server.audio_downlink_available()
            else None,
            timeout=args.connect_timeout,
            description="an audio-capable Presence node",
        )
        print(
            "  PASS node | "
            f"device={connected.get('last_device_id')} | "
            "audio_downlink=True"
        )

        print("[3/4] Route one real VoiceEngine utterance to the node")
        voice = VoiceEngine()
        voice.set_presence_pcm_output(
            available_fn=server.audio_downlink_available,
            playback_fn=server.play_pcm16,
            stream_available_fn=server.audio_downlink_stream_available,
            stream_playback_fn=server.play_pcm16_stream,
        )
        voice.say(args.text, voice_mode="chat")

        terminal = await _wait_for(
            lambda: (
                voice.snapshot()
                if voice.snapshot().get("last_presence_pcm_status")
                in {"complete", "complete_streaming", "failed", "failed_streaming"}
                else None
            ),
            timeout=args.voice_timeout,
            description="VoiceEngine Presence PCM completion",
        )
        if terminal.get("last_presence_pcm_status") not in {
            "complete",
            "complete_streaming",
        }:
            raise RuntimeError(
                "VoiceEngine Presence output failed: "
                f"{terminal.get('last_presence_pcm_error')}"
            )
        session = server.snapshot()
        if (
            int(session.get("playback_completed", 0)) != 1
            or int(session.get("playback_failed", 0)) != 0
            or int(session.get("last_playback_underruns", -1)) != 0
            or int(session.get("last_playback_bytes_played", 0)) <= 0
        ):
            raise RuntimeError(f"session playback counters are not clean: {session}")
        print(
            "  PASS voice | "
            f"stream={terminal.get('last_presence_pcm_stream_id')} | "
            f"bytes={terminal.get('last_presence_pcm_bytes')} | "
            f"mode={terminal.get('last_presence_pcm_streaming')} | "
            f"first_audio={terminal.get('last_presence_pcm_first_audio_ms')}ms | "
            f"underruns={terminal.get('last_presence_pcm_underruns')} | "
            f"elapsed={terminal.get('last_presence_pcm_elapsed_ms')}ms"
        )

        print("[4/4] Clear ephemeral credentials from NVS")
        voice.set_presence_pcm_output()
        result = await asyncio.to_thread(
            serial_session,
            port=args.serial_port,
            command=b"NANA_SESSION_CLEAR\n",
            timeout=args.serial_timeout,
        )
        if result != 0:
            raise RuntimeError("board did not clear ephemeral session config")
        credentials_cleared = True
        print("  PASS cleanup | token=cleared")
    finally:
        if voice is not None:
            try:
                voice.set_presence_pcm_output()
            except Exception:
                pass
            voice.shutdown()
            voice.worker.join(timeout=5.0)
        if not credentials_cleared:
            try:
                cleanup = await asyncio.to_thread(
                    serial_session,
                    port=args.serial_port,
                    command=b"NANA_SESSION_CLEAR\n",
                    timeout=10.0,
                )
                if cleanup == 0:
                    print("Cleanup after failure: token=cleared")
            except Exception as exc:
                print(f"Cleanup warning: {type(exc).__name__}: {exc}")
        server.stop()
        await asyncio.wait_for(server_task, timeout=5.0)

    print("smoke_presence_voice_engine_hardware: PASS (4/4)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Physically accept one real VoiceEngine TTS utterance over the "
            "authenticated Presence PCM downlink."
        )
    )
    parser.add_argument("--serial-port", default="COM14")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--server-port", type=int, default=8765)
    parser.add_argument(
        "--uri",
        default="ws://<LOCAL_IP>:8765/presence/v1",
    )
    parser.add_argument("--serial-timeout", type=float, default=45.0)
    parser.add_argument("--connect-timeout", type=float, default=45.0)
    parser.add_argument("--voice-timeout", type=float, default=90.0)
    parser.add_argument(
        "--text",
        default=(
            "[calm] Ba oi, day la bai kiem tra giong Nana qua ket noi Wi-Fi."
        ),
    )
    return parser


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
