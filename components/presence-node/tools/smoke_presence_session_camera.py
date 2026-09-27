"""Physical RAM-only camera smoke for the durable Presence session."""

from __future__ import annotations

import argparse
import asyncio
from getpass import getpass
from pathlib import Path
import sys
import time


NODE_ROOT = Path(__file__).resolve().parents[1]
PY_ROOT = NODE_ROOT.parent
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))


from nana.runtime.presence_camera_vision import YuNetFacePresenceDetector
from nana.runtime.presence_session_server import (
    MAX_CAMERA_BYTES,
    PresenceCameraError,
    PresenceCaptureOutcome,
    PresenceSessionServer,
)


async def _reject_audio_capture(_capture) -> PresenceCaptureOutcome:
    return PresenceCaptureOutcome("skipped", "camera_smoke_only")


async def _wait_for_camera_node(
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
            and capabilities.get("camera") is True
        ):
            return last_snapshot
        await asyncio.sleep(0.1)
    raise TimeoutError(
        "timed out waiting for a camera-capable Presence node: "
        f"state={last_snapshot.get('state')} "
        f"active={last_snapshot.get('active_sessions', 0)} "
        f"profile={last_snapshot.get('last_profile')} "
        f"capabilities={last_snapshot.get('last_capabilities')} "
        f"last_error={last_snapshot.get('last_error')}"
    )


async def _run(args: argparse.Namespace) -> None:
    token = getpass("Presence bearer token: ").strip()
    if not token:
        raise ValueError("Presence bearer token must not be empty")

    detector = YuNetFacePresenceDetector(score_threshold=args.score_threshold)
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
            f"Camera smoke listener ready on {args.host}:{server.bound_port}; "
            "waiting for the board..."
        )
        connected = await _wait_for_camera_node(
            server,
            timeout=args.connect_timeout,
        )
        print(
            "Connected | "
            f"device={connected.get('last_device_id')} | "
            f"firmware={connected.get('last_firmware')} | "
            f"camera={connected.get('last_capabilities', {}).get('camera')}"
        )
        print(
            "Keep still and quiet. Frames stay in RAM; microphone processing, "
            "TTS, speaker playback, and file persistence are not used."
        )

        present_frames = 0
        for index in range(1, args.frames + 1):
            frame = await server.capture_camera_jpeg_async()
            result = await asyncio.to_thread(
                detector.detect_jpeg,
                frame.jpeg,
                expected_width=frame.width,
                expected_height=frame.height,
            )
            if result.present:
                present_frames += 1
            state = "present" if result.present else "absent"
            print(
                f"[{index}/{args.frames}] PASS | state={state} | "
                f"faces={result.face_count} | "
                f"confidence={result.max_confidence:.3f} | "
                f"frame={result.frame_width}x{result.frame_height} | "
                f"jpeg={len(frame.jpeg)}B | transfer={frame.elapsed_ms:.1f}ms | "
                f"yunet={result.inference_ms:.1f}ms | persistence=none"
            )
            if index < args.frames:
                await asyncio.sleep(args.interval_seconds)

        snapshot = server.snapshot()
        completed = int(snapshot.get("camera_completed", 0))
        rejected = int(snapshot.get("camera_rejected", 0))
        failed = int(snapshot.get("camera_failed", 0))
        if completed != args.frames or rejected or failed:
            raise RuntimeError(
                "camera counters did not close cleanly: "
                f"expected={args.frames} completed={completed} "
                f"rejected={rejected} failed={failed}"
            )
        required_present = (
            args.min_present_frames
            if args.min_present_frames is not None
            else max(1, (args.frames + 1) // 2)
        )
        if present_frames < required_present:
            raise RuntimeError(
                "YuNet did not confirm enough face-present frames: "
                f"required={required_present} detected={present_frames} "
                f"captured={args.frames}"
            )
        print(
            "Summary | "
            f"snapshots={completed}/{args.frames} | "
            f"face_present={present_frames}/{args.frames} | "
            f"bytes={snapshot.get('camera_bytes_received', 0)} | "
            f"active={snapshot.get('active_sessions', 0)} | "
            f"heartbeats={snapshot.get('heartbeats', 0)} | "
            f"errors={snapshot.get('errors', 0)}"
        )
        print("smoke_presence_session_camera: PASS")
    finally:
        server.stop()
        await asyncio.wait_for(server_task, timeout=5.0)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Capture bounded VGA JPEG snapshots over Presence Session and run "
            "RAM-only YuNet face-presence detection."
        )
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--connect-timeout", type=float, default=45.0)
    parser.add_argument("--frames", type=int, default=3)
    parser.add_argument(
        "--min-present-frames",
        type=int,
        default=None,
        help="minimum YuNet face-present frames; default is a strict majority",
    )
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--score-threshold", type=float, default=0.80)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if MAX_CAMERA_BYTES != 192 * 1024:
        raise SystemExit(
            "Core camera bound drifted from the firmware contract: "
            f"expected={192 * 1024} actual={MAX_CAMERA_BYTES}"
        )
    if args.connect_timeout <= 0.0:
        raise SystemExit("--connect-timeout must be positive")
    if not 1 <= args.frames <= 10:
        raise SystemExit("--frames must be between 1 and 10")
    if args.min_present_frames is not None and not (
        0 <= args.min_present_frames <= args.frames
    ):
        raise SystemExit("--min-present-frames must be between 0 and --frames")
    if not 1.0 <= args.interval_seconds <= 30.0:
        raise SystemExit("--interval-seconds must be between 1 and 30 seconds")
    if not 0.5 <= args.score_threshold <= 0.99:
        raise SystemExit("--score-threshold must be between 0.5 and 0.99")
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("Camera smoke stopped by operator.")
        raise SystemExit(130) from None
    except (PresenceCameraError, TimeoutError, RuntimeError, ValueError) as exc:
        print(
            "smoke_presence_session_camera: FAIL | "
            f"{type(exc).__name__}: {exc}"
        )
        print(
            "Next action: keep the lens lit, verify the Session token and node "
            "camera capability, then inspect the first firmware camera error."
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
