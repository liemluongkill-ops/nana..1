"""Loopback smoke test for the Presence session milestone-1 contract."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from nana.runtime.presence_session_server import (
    MAX_CAMERA_BYTES,
    PROTOCOL_NAME,
    SESSION_PATH,
    PresenceCameraError,
    PresenceCaptureOutcome,
    PresenceDisplayError,
    PresencePlaybackError,
    PresenceSessionServer,
)
from nana.runtime.presence_media_protocol import (
    MEDIA_CHUNK_BYTES,
    MEDIA_KIND_JPEG_UPLINK,
    MEDIA_KIND_PCM_DOWNLINK,
    MEDIA_KIND_PCM_UPLINK,
    PresenceMediaFrame,
    decode_media_frame,
    encode_media_frame,
)


TOKEN = "presence-session-smoke-secret"


def _hello(
    device_id: str = "nana-node-smoke",
    *,
    audio_downlink: bool = False,
    audio_downlink_stream: bool = False,
    audio_uplink: bool = False,
    display: bool = False,
    camera: bool = False,
) -> str:
    return json.dumps(
        {
            "type": "hello",
            "protocol": PROTOCOL_NAME,
            "device_id": device_id,
            "firmware": "smoke-1.0",
            "profile": "presence_session",
            "capabilities": {
                "audio_uplink": audio_uplink,
                "audio_downlink": audio_downlink,
                "audio_downlink_stream": audio_downlink_stream,
                "camera": camera,
                "display": display,
            },
        }
    )


def _heartbeat(sequence: int) -> str:
    return json.dumps(
        {
            "type": "heartbeat",
            "protocol": PROTOCOL_NAME,
            "sequence": sequence,
            "uptime_ms": sequence * 1000,
        }
    )


@asynccontextmanager
async def _running_server(
    *,
    heartbeat=0.1,
    timeout=0.4,
    audio_uplink_handler=None,
):
    server = PresenceSessionServer(
        host="127.0.0.1",
        port=0,
        token=TOKEN,
        heartbeat_seconds=heartbeat,
        timeout_seconds=timeout,
        hello_timeout_seconds=0.3,
        audio_uplink_handler=audio_uplink_handler,
    )
    task = asyncio.create_task(server.run())
    await server.wait_started()
    if task.done():
        await task
    try:
        yield server, f"ws://127.0.0.1:{server.bound_port}{SESSION_PATH}"
    finally:
        server.stop()
        await asyncio.wait_for(task, timeout=2.0)
        assert server.snapshot()["state"] == "stopped"


async def test_authentication_rejects_missing_and_wrong_tokens() -> None:
    async with _running_server() as (server, uri):
        for headers in (None, {"Authorization": "Bearer wrong"}):
            try:
                async with connect(
                    uri,
                    additional_headers=headers,
                    proxy=None,
                ):
                    raise AssertionError("unauthorized WebSocket was accepted")
            except InvalidStatus as exc:
                assert exc.response.status_code == 401
        assert server.snapshot()["auth_rejected"] == 2
    print("  auth: missing and wrong bearer tokens rejected")


async def test_hello_welcome_and_heartbeat() -> None:
    async with _running_server() as (server, uri):
        async with connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        ) as websocket:
            await websocket.send(_hello())
            welcome = json.loads(await websocket.recv())
            assert welcome["type"] == "welcome"
            assert welcome["protocol"] == PROTOCOL_NAME
            assert welcome["heartbeat_ms"] == 100
            assert welcome["session_id"]

            await websocket.send(_heartbeat(7))
            heartbeat_ack = json.loads(await websocket.recv())
            assert heartbeat_ack["type"] == "heartbeat_ack"
            assert heartbeat_ack["sequence"] == 7

            snapshot = server.snapshot()
            assert snapshot["active_sessions"] == 1
            assert snapshot["last_device_id"] == "nana-node-smoke"
            assert snapshot["last_sequence"] == 7
            assert snapshot["heartbeats"] == 1
    print("  control: HELLO/WELCOME and heartbeat ACK accepted")


async def test_binary_and_malformed_frames_fail_closed() -> None:
    async with _running_server() as (server, uri):
        websocket = await connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        )
        await websocket.send(b"media-is-not-enabled")
        try:
            await websocket.recv()
            raise AssertionError("binary frame did not close the session")
        except ConnectionClosed as exc:
            assert exc.code == 1003
        await websocket.close()

        websocket = await connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        )
        await websocket.send("not-json")
        try:
            await websocket.recv()
            raise AssertionError("malformed frame did not close the session")
        except ConnectionClosed as exc:
            assert exc.code == 1008
        await websocket.close()
        assert server.snapshot()["protocol_rejected"] == 2
    print("  protocol: binary and malformed frames fail closed")


async def test_duplicate_device_replaces_the_old_session() -> None:
    async with _running_server() as (server, uri):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        first = await connect(uri, additional_headers=headers, proxy=None)
        await first.send(_hello("same-device"))
        first_welcome = json.loads(await first.recv())

        second = await connect(uri, additional_headers=headers, proxy=None)
        await second.send(_hello("same-device"))
        second_welcome = json.loads(await second.recv())
        assert second_welcome["session_id"] != first_welcome["session_id"]
        try:
            await first.recv()
            raise AssertionError("old duplicate session remained open")
        except ConnectionClosed as exc:
            assert exc.code == 4001

        await second.send(_heartbeat(1))
        assert json.loads(await second.recv())["sequence"] == 1
        snapshot = server.snapshot()
        assert snapshot["active_sessions"] == 1
        assert snapshot["replacements"] == 1
        await first.close()
        await second.close()
    print("  reconnect: newest connection replaces duplicate device session")


async def test_stale_heartbeat_closes_the_session() -> None:
    async with _running_server(heartbeat=0.05, timeout=0.15) as (server, uri):
        websocket = await connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        )
        await websocket.send(_hello("stale-device"))
        assert json.loads(await websocket.recv())["type"] == "welcome"
        try:
            await asyncio.wait_for(websocket.recv(), timeout=1.0)
            raise AssertionError("stale session remained open")
        except ConnectionClosed as exc:
            assert exc.code == 4000
        await websocket.close()
        assert server.snapshot()["heartbeat_timeouts"] == 1
    print("  liveness: stale heartbeat closes the session")


async def test_task_cancellation_releases_session_and_listener() -> None:
    server = PresenceSessionServer(
        host="127.0.0.1",
        port=0,
        token=TOKEN,
        heartbeat_seconds=0.1,
        timeout_seconds=0.4,
        hello_timeout_seconds=0.3,
    )
    task = asyncio.create_task(server.run())
    await server.wait_started()
    port = server.bound_port
    assert port is not None
    websocket = await connect(
        f"ws://127.0.0.1:{port}{SESSION_PATH}",
        additional_headers={"Authorization": f"Bearer {TOKEN}"},
        proxy=None,
    )
    await websocket.send(_hello("cancel-device"))
    assert json.loads(await websocket.recv())["type"] == "welcome"
    assert server.snapshot()["active_sessions"] == 1

    task.cancel()
    try:
        await task
        raise AssertionError("cancelled server task returned normally")
    except asyncio.CancelledError:
        pass
    await websocket.wait_closed()
    assert server.snapshot()["state"] == "stopped"
    assert server.snapshot()["active_sessions"] == 0

    replacement = PresenceSessionServer(
        host="127.0.0.1",
        port=port,
        token=TOKEN,
        heartbeat_seconds=0.1,
        timeout_seconds=0.4,
        hello_timeout_seconds=0.3,
    )
    replacement_task = asyncio.create_task(replacement.run())
    await replacement.wait_started()
    assert replacement.bound_port == port
    replacement.stop()
    await asyncio.wait_for(replacement_task, timeout=2.0)
    print("  cancellation: sessions cleared and listener port released")


async def test_repeated_shutdown_is_idempotent() -> None:
    server = PresenceSessionServer(
        host="127.0.0.1",
        port=0,
        token=TOKEN,
        heartbeat_seconds=0.1,
        timeout_seconds=0.4,
        hello_timeout_seconds=0.3,
    )
    task = asyncio.create_task(server.run())
    await server.wait_started()
    server.stop()
    server.stop()
    await asyncio.wait_for(task, timeout=2.0)
    server.stop()
    snapshot = server.snapshot()
    assert snapshot["state"] == "stopped"
    assert snapshot["active_sessions"] == 0
    print("  shutdown: repeated stop leaves no active session")


async def test_partial_listener_start_failure_releases_runtime_state() -> None:
    blocker = await asyncio.start_server(
        lambda _reader, _writer: None,
        host="127.0.0.1",
        port=0,
    )
    sockets = tuple(blocker.sockets or ())
    assert sockets
    port = int(sockets[0].getsockname()[1])

    failed = PresenceSessionServer(
        host="127.0.0.1",
        port=port,
        token=TOKEN,
        heartbeat_seconds=0.1,
        timeout_seconds=0.4,
        hello_timeout_seconds=0.3,
    )
    failed_task = asyncio.create_task(failed.run())
    await failed.wait_started()
    try:
        await failed_task
        raise AssertionError("listener unexpectedly bound to an occupied port")
    except OSError:
        pass

    failed_snapshot = failed.snapshot()
    assert failed_snapshot["state"] == "error"
    assert failed_snapshot["active_sessions"] == 0
    assert failed_snapshot["errors"] == 1
    assert "OSError" in str(failed_snapshot["last_error"])

    blocker.close()
    await blocker.wait_closed()

    replacement = PresenceSessionServer(
        host="127.0.0.1",
        port=port,
        token=TOKEN,
        heartbeat_seconds=0.1,
        timeout_seconds=0.4,
        hello_timeout_seconds=0.3,
    )
    replacement_task = asyncio.create_task(replacement.run())
    await replacement.wait_started()
    assert replacement.snapshot()["state"] == "listening"
    assert replacement.bound_port == port
    replacement.stop()
    await asyncio.wait_for(replacement_task, timeout=2.0)
    assert replacement.snapshot()["state"] == "stopped"
    print("  rollback: occupied-port start failure leaves a reusable listener")


async def test_bounded_pcm_downlink_drains_without_underrun() -> None:
    async with _running_server(timeout=1.5) as (server, uri):
        async with connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        ) as websocket:
            await websocket.send(_hello(audio_downlink=True))
            assert json.loads(await websocket.recv())["type"] == "welcome"

            pcm = bytes(index % 251 for index in range(MEDIA_CHUNK_BYTES * 4 + 318))
            assert len(pcm) % 2 == 0
            playback_task = asyncio.create_task(server.play_pcm16_async(pcm))

            begin = json.loads(await websocket.recv())
            assert begin["type"] == "playback_begin"
            assert begin["codec"] == "pcm_s16le"
            assert begin["sample_rate"] == 16000
            assert begin["channels"] == 1
            assert begin["total_bytes"] == len(pcm)
            stream_id = begin["stream_id"]
            await websocket.send(
                json.dumps(
                    {
                        "type": "playback_ready",
                        "protocol": PROTOCOL_NAME,
                        "stream_id": stream_id,
                        "credits": 2,
                        "queue_capacity": 4,
                    }
                )
            )

            received = bytearray()
            expected_sequence = 0
            while True:
                message = await websocket.recv()
                if isinstance(message, bytes):
                    frame = decode_media_frame(message)
                    assert frame.kind == MEDIA_KIND_PCM_DOWNLINK
                    assert frame.stream_id == stream_id
                    assert frame.sequence == expected_sequence
                    expected_sequence += 1
                    received.extend(frame.payload)
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "playback_credit",
                                "protocol": PROTOCOL_NAME,
                                "stream_id": stream_id,
                                "credits": 1,
                                "bytes_played": len(received),
                            }
                        )
                    )
                    continue

                end = json.loads(message)
                assert end["type"] == "playback_end"
                assert end["stream_id"] == stream_id
                assert end["total_bytes"] == len(pcm)
                assert end["chunks"] == expected_sequence
                break

            assert bytes(received) == pcm
            await websocket.send(
                json.dumps(
                    {
                        "type": "playback_drained",
                        "protocol": PROTOCOL_NAME,
                        "stream_id": stream_id,
                        "status": "complete",
                        "bytes_received": len(received),
                        "bytes_played": len(received),
                        "queue_high_water": 2,
                        "underruns": 0,
                    }
                )
            )
            result = await asyncio.wait_for(playback_task, timeout=2.0)
            assert result.completed
            assert result.bytes_received == len(pcm)
            assert result.bytes_played == len(pcm)
            snapshot = server.snapshot()
            assert snapshot["playback_started"] == 1
            assert snapshot["playback_completed"] == 1
            assert snapshot["playback_failed"] == 0
            assert snapshot["playback_bytes_sent"] == len(pcm)
    print("  media: bounded PCM frames, credits, CRC, and drain accepted")


async def test_streaming_pcm_downlink_starts_before_total_is_known() -> None:
    async with _running_server(timeout=1.5) as (server, uri):
        async with connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        ) as websocket:
            await websocket.send(
                _hello(
                    audio_downlink=True,
                    audio_downlink_stream=True,
                )
            )
            assert json.loads(await websocket.recv())["type"] == "welcome"

            source = [b"\x01\x00" * 600, b"\x02\x00" * 700]
            expected = b"".join(source)
            playback_task = asyncio.create_task(
                server.play_pcm16_stream_async(iter(source))
            )

            begin = json.loads(await websocket.recv())
            assert begin["type"] == "playback_begin"
            assert begin["streaming"] is True
            assert begin["total_bytes"] == 0
            assert begin["total_samples"] == 0
            stream_id = begin["stream_id"]
            await websocket.send(
                json.dumps(
                    {
                        "type": "playback_ready",
                        "protocol": PROTOCOL_NAME,
                        "stream_id": stream_id,
                        "credits": 2,
                        "queue_capacity": 4,
                    }
                )
            )

            received = bytearray()
            expected_sequence = 0
            started_sent = False
            while True:
                message = await websocket.recv()
                if isinstance(message, bytes):
                    frame = decode_media_frame(message)
                    assert frame.kind == MEDIA_KIND_PCM_DOWNLINK
                    assert frame.stream_id == stream_id
                    assert frame.sequence == expected_sequence
                    expected_sequence += 1
                    received.extend(frame.payload)
                    if not started_sent:
                        await websocket.send(
                            json.dumps(
                                {
                                    "type": "playback_started",
                                    "protocol": PROTOCOL_NAME,
                                    "stream_id": stream_id,
                                    "bytes_buffered": len(received),
                                }
                            )
                        )
                        started_sent = True
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "playback_credit",
                                "protocol": PROTOCOL_NAME,
                                "stream_id": stream_id,
                                "credits": 1,
                                "bytes_played": len(received),
                            }
                        )
                    )
                    continue

                end = json.loads(message)
                assert end["type"] == "playback_end"
                assert end["total_bytes"] == len(expected)
                assert end["chunks"] == expected_sequence
                break

            assert bytes(received) == expected
            await websocket.send(
                json.dumps(
                    {
                        "type": "playback_drained",
                        "protocol": PROTOCOL_NAME,
                        "stream_id": stream_id,
                        "status": "complete",
                        "bytes_received": len(received),
                        "bytes_played": len(received),
                        "queue_high_water": 2,
                        "underruns": 0,
                    }
                )
            )
            result = await asyncio.wait_for(playback_task, timeout=2.0)
            assert result.completed
            assert result.total_bytes == len(expected)
            assert result.first_audio_ms is not None
            snapshot = server.snapshot()
            assert snapshot["last_playback_streaming"] is True
            assert snapshot["last_playback_first_audio_ms"] is not None
    print("  media: unknown-length PCM streams start before final byte count")


async def test_playback_disconnect_fails_promptly_and_reconnects_cleanly() -> None:
    async with _running_server(timeout=1.5) as (server, uri):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        websocket = await connect(uri, additional_headers=headers, proxy=None)
        await websocket.send(_hello("playback-drop", audio_downlink=True))
        assert json.loads(await websocket.recv())["type"] == "welcome"

        pcm = bytes(index % 251 for index in range(MEDIA_CHUNK_BYTES * 6))
        playback_task = asyncio.create_task(server.play_pcm16_async(pcm))
        begin = json.loads(await websocket.recv())
        assert begin["type"] == "playback_begin"
        await websocket.send(
            json.dumps(
                {
                    "type": "playback_ready",
                    "protocol": PROTOCOL_NAME,
                    "stream_id": begin["stream_id"],
                    "credits": 1,
                    "queue_capacity": 1,
                }
            )
        )
        first_frame = decode_media_frame(await websocket.recv())
        assert first_frame.kind == MEDIA_KIND_PCM_DOWNLINK
        assert first_frame.sequence == 0

        await websocket.close()
        try:
            await asyncio.wait_for(playback_task, timeout=1.0)
            raise AssertionError("playback survived a disconnected node")
        except PresencePlaybackError as exc:
            assert "disconnected" in str(exc)

        for _ in range(20):
            if server.snapshot()["active_sessions"] == 0:
                break
            await asyncio.sleep(0.01)
        snapshot = server.snapshot()
        assert snapshot["active_sessions"] == 0
        assert snapshot["playback_started"] == 1
        assert snapshot["playback_completed"] == 0
        assert snapshot["playback_failed"] == 1
        assert snapshot["last_playback_status"] == "failed"
        assert snapshot["last_playback_phase"] == "failed"

        async with connect(uri, additional_headers=headers, proxy=None) as replacement:
            await replacement.send(
                _hello("playback-drop", audio_downlink=True)
            )
            assert json.loads(await replacement.recv())["type"] == "welcome"
            await replacement.send(_heartbeat(91))
            heartbeat_ack = json.loads(await replacement.recv())
            assert heartbeat_ack["type"] == "heartbeat_ack"
            assert heartbeat_ack["sequence"] == 91
            assert server.snapshot()["active_sessions"] == 1
    print("  recovery: interrupted playback fails promptly and reconnects cleanly")


async def test_pcm_downlink_requires_advertised_capability() -> None:
    async with _running_server() as (server, uri):
        async with connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        ) as websocket:
            await websocket.send(_hello(audio_downlink=False))
            assert json.loads(await websocket.recv())["type"] == "welcome"
            try:
                await server.play_pcm16_async(b"\x00\x00" * 128)
                raise AssertionError("audio-disabled node accepted playback")
            except PresencePlaybackError as exc:
                assert "no audio-capable" in str(exc)
    print("  media: capability gate rejects audio-disabled nodes")


async def test_display_state_and_event_require_exact_acknowledgement() -> None:
    async with _running_server() as (server, uri):
        async with connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        ) as websocket:
            await websocket.send(_hello(display=True))
            assert json.loads(await websocket.recv())["type"] == "welcome"

            state_task = asyncio.create_task(
                server.set_display_state_async("listening")
            )
            state = json.loads(await websocket.recv())
            assert state["type"] == "display_state"
            assert state["tag"] == "listening"
            await websocket.send(
                json.dumps(
                    {
                        "type": "display_ack",
                        "protocol": PROTOCOL_NAME,
                        "request_id": state["request_id"],
                        "kind": "state",
                        "tag": "listening",
                        "status": "accepted",
                        "reason": "accepted",
                    }
                )
            )
            state_result = await asyncio.wait_for(state_task, timeout=1.0)
            assert state_result.accepted

            event_task = asyncio.create_task(
                server.trigger_display_event_async("wink_right")
            )
            event = json.loads(await websocket.recv())
            assert event["type"] == "display_event"
            assert event["tag"] == "wink_right"
            await websocket.send(
                json.dumps(
                    {
                        "type": "display_ack",
                        "protocol": PROTOCOL_NAME,
                        "request_id": event["request_id"],
                        "kind": "event",
                        "tag": "wink_right",
                        "status": "accepted",
                        "reason": "accepted",
                    }
                )
            )
            event_result = await asyncio.wait_for(event_task, timeout=1.0)
            assert event_result.accepted

            snapshot = server.snapshot()
            assert snapshot["display_sent"] == 2
            assert snapshot["display_accepted"] == 2
            assert snapshot["display_rejected"] == 0
            assert snapshot["display_failed"] == 0
            assert snapshot["last_display_tag"] == "wink_right"
    print("  display: state and event commands require exact node ACKs")


async def test_display_capability_and_tag_allowlist_fail_closed() -> None:
    async with _running_server() as (server, uri):
        async with connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        ) as websocket:
            await websocket.send(_hello(display=False))
            assert json.loads(await websocket.recv())["type"] == "welcome"
            try:
                await server.set_display_state_async("listening")
                raise AssertionError("display-disabled node accepted a command")
            except PresenceDisplayError as exc:
                assert "no display-capable" in str(exc)

            try:
                await server.set_display_state_async("not-a-nana-face")
                raise AssertionError("unknown display state passed the allowlist")
            except ValueError as exc:
                assert "approved expression" in str(exc)

            try:
                await server.trigger_display_event_async("neutral")
                raise AssertionError("neutral was accepted as an event")
            except ValueError as exc:
                assert "approved event" in str(exc)
    print("  display: capability and tag allowlist fail closed")


async def test_pending_display_command_does_not_leak_across_reconnect() -> None:
    async with _running_server(timeout=1.5) as (server, uri):
        headers = {"Authorization": f"Bearer {TOKEN}"}
        first = await connect(uri, additional_headers=headers, proxy=None)
        await first.send(_hello("display-reconnect", display=True))
        assert json.loads(await first.recv())["type"] == "welcome"

        stale_task = asyncio.create_task(
            server.set_display_state_async("core_loading")
        )
        stale_command = json.loads(await first.recv())
        assert stale_command["type"] == "display_state"

        second = await connect(uri, additional_headers=headers, proxy=None)
        await second.send(_hello("display-reconnect", display=True))
        assert json.loads(await second.recv())["type"] == "welcome"

        try:
            await asyncio.wait_for(stale_task, timeout=1.0)
            raise AssertionError("replaced session completed a stale display command")
        except PresenceDisplayError as exc:
            assert "replaced by reconnect" in str(exc)

        resync_command = json.loads(await second.recv())
        assert resync_command["type"] == "display_state"
        assert resync_command["tag"] == "core_loading"
        await second.send(
            json.dumps(
                {
                    "type": "display_ack",
                    "protocol": PROTOCOL_NAME,
                    "request_id": resync_command["request_id"],
                    "kind": "state",
                    "tag": "core_loading",
                    "status": "accepted",
                    "reason": "accepted",
                }
            )
        )

        fresh_task = asyncio.create_task(
            server.trigger_display_event_async("double_blink")
        )
        fresh_command = json.loads(await second.recv())
        assert fresh_command["type"] == "display_event"
        assert fresh_command["tag"] == "double_blink"
        await second.send(
            json.dumps(
                {
                    "type": "display_ack",
                    "protocol": PROTOCOL_NAME,
                    "request_id": fresh_command["request_id"],
                    "kind": "event",
                    "tag": "double_blink",
                    "status": "accepted",
                    "reason": "accepted",
                }
            )
        )
        assert (await asyncio.wait_for(fresh_task, timeout=1.0)).accepted

        try:
            await first.recv()
            raise AssertionError("replaced display session remained open")
        except ConnectionClosed as exc:
            assert exc.code == 4001

        snapshot = server.snapshot()
        assert snapshot["replacements"] == 1
        assert snapshot["display_sent"] == 3
        assert snapshot["display_accepted"] == 2
        assert snapshot["display_failed"] == 1
        await first.close()
        await second.close()
    print("  display: pending command is isolated from replacement session")


async def test_bounded_pcm_uplink_reaches_handler_and_completes_turn() -> None:
    captures = []

    async def handle_capture(capture):
        captures.append(capture)
        return PresenceCaptureOutcome("complete", "reply_drained")

    async with _running_server(
        timeout=1.5,
        audio_uplink_handler=handle_capture,
    ) as (server, uri):
        async with connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        ) as websocket:
            await websocket.send(_hello(audio_uplink=True))
            assert json.loads(await websocket.recv())["type"] == "welcome"

            stream_id = 73
            pcm = bytes(index % 251 for index in range(MEDIA_CHUNK_BYTES * 3 + 200))
            assert len(pcm) % 2 == 0
            await websocket.send(
                json.dumps(
                    {
                        "type": "capture_begin",
                        "protocol": PROTOCOL_NAME,
                        "stream_id": stream_id,
                        "codec": "pcm_s16le",
                        "sample_rate": 16000,
                        "channels": 1,
                        "sample_width": 2,
                        "max_bytes": len(pcm),
                        "chunk_bytes": MEDIA_CHUNK_BYTES,
                        "integrity": "crc32_per_chunk",
                        "noise_dbfs_x10": -472,
                        "start_dbfs_x10": -312,
                    }
                )
            )
            ready = json.loads(await websocket.recv())
            assert ready["type"] == "capture_ready"
            assert ready["stream_id"] == stream_id
            assert ready["credits"] == 8

            chunks = 0
            for offset in range(0, len(pcm), MEDIA_CHUNK_BYTES):
                payload = pcm[offset : offset + MEDIA_CHUNK_BYTES]
                await websocket.send(
                    encode_media_frame(
                        PresenceMediaFrame(
                            kind=MEDIA_KIND_PCM_UPLINK,
                            stream_id=stream_id,
                            sequence=chunks,
                            payload=payload,
                        )
                    )
                )
                credit = json.loads(await websocket.recv())
                assert credit["type"] == "capture_credit"
                assert credit["stream_id"] == stream_id
                assert credit["credits"] == 1
                chunks += 1

            trim_samples = 4
            await websocket.send(
                json.dumps(
                    {
                        "type": "capture_end",
                        "protocol": PROTOCOL_NAME,
                        "stream_id": stream_id,
                        "total_bytes": len(pcm),
                        "chunks": chunks,
                        "trim_samples": trim_samples,
                        "reason": "silence",
                    }
                )
            )
            received = json.loads(await websocket.recv())
            assert received["type"] == "capture_received"
            assert received["wire_bytes"] == len(pcm)
            assert received["effective_bytes"] == len(pcm) - 8

            complete = json.loads(await websocket.recv())
            assert complete["type"] == "turn_complete"
            assert complete["stream_id"] == stream_id
            assert complete["status"] == "complete"
            assert complete["reason"] == "reply_drained"

            assert len(captures) == 1
            capture = captures[0]
            assert capture.pcm == pcm[:-8]
            assert capture.wire_bytes == len(pcm)
            assert capture.trim_samples == trim_samples
            assert capture.noise_dbfs == -47.2
            assert capture.start_dbfs == -31.2
            snapshot = server.snapshot()
            assert snapshot["capture_started"] == 1
            assert snapshot["capture_completed"] == 1
            assert snapshot["capture_failed"] == 0
            assert snapshot["capture_bytes_received"] == len(pcm)
    print("  media: bounded PCM uplink, credits, trim, and turn completion accepted")


async def test_pcm_uplink_fails_closed_without_capability_or_valid_sequence() -> None:
    async def handle_capture(_capture):
        return PresenceCaptureOutcome("complete", "unused")

    async with _running_server(
        timeout=1.5,
        audio_uplink_handler=handle_capture,
    ) as (_server, uri):
        websocket = await connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        )
        await websocket.send(_hello(audio_uplink=False))
        assert json.loads(await websocket.recv())["type"] == "welcome"
        await websocket.send(
            json.dumps(
                {
                    "type": "capture_begin",
                    "protocol": PROTOCOL_NAME,
                    "stream_id": 1,
                    "codec": "pcm_s16le",
                    "sample_rate": 16000,
                    "channels": 1,
                    "sample_width": 2,
                    "max_bytes": 1024,
                    "chunk_bytes": 1024,
                    "integrity": "crc32_per_chunk",
                    "noise_dbfs_x10": -500,
                    "start_dbfs_x10": -300,
                }
            )
        )
        try:
            await websocket.recv()
            raise AssertionError("uplink-disabled node started a capture")
        except ConnectionClosed as exc:
            assert exc.code == 1008
        await websocket.close()

        websocket = await connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        )
        await websocket.send(_hello("bad-sequence", audio_uplink=True))
        assert json.loads(await websocket.recv())["type"] == "welcome"
        await websocket.send(
            json.dumps(
                {
                    "type": "capture_begin",
                    "protocol": PROTOCOL_NAME,
                    "stream_id": 2,
                    "codec": "pcm_s16le",
                    "sample_rate": 16000,
                    "channels": 1,
                    "sample_width": 2,
                    "max_bytes": 1024,
                    "chunk_bytes": 1024,
                    "integrity": "crc32_per_chunk",
                    "noise_dbfs_x10": -500,
                    "start_dbfs_x10": -300,
                }
            )
        )
        assert json.loads(await websocket.recv())["type"] == "capture_ready"
        await websocket.send(
            encode_media_frame(
                PresenceMediaFrame(
                    kind=MEDIA_KIND_PCM_UPLINK,
                    stream_id=2,
                    sequence=1,
                    payload=b"\x00\x00" * 8,
                )
            )
        )
        try:
            await websocket.recv()
            raise AssertionError("out-of-order uplink frame was accepted")
        except ConnectionClosed as exc:
            assert exc.code == 1008
        await websocket.close()
    print("  media: uplink capability and sequence violations fail closed")


async def test_bounded_jpeg_snapshot_uses_credits_and_stays_in_memory() -> None:
    async with _running_server(timeout=1.5) as (server, uri):
        async with connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        ) as websocket:
            await websocket.send(_hello(camera=True))
            assert json.loads(await websocket.recv())["type"] == "welcome"

            snapshot_task = asyncio.create_task(server.capture_camera_jpeg_async())
            request = json.loads(await websocket.recv())
            assert request["type"] == "camera_snapshot"
            assert request["codec"] == "jpeg"
            assert request["integrity"] == "crc32_per_chunk"
            assert request["max_bytes"] == MAX_CAMERA_BYTES == 192 * 1024
            stream_id = request["request_id"]

            jpeg = b"\xff\xd8" + bytes(
                index % 251 for index in range(MEDIA_CHUNK_BYTES * 2 + 317)
            ) + b"\xff\xd9"
            await websocket.send(
                json.dumps(
                    {
                        "type": "camera_begin",
                        "protocol": PROTOCOL_NAME,
                        "stream_id": stream_id,
                        "codec": "jpeg",
                        "width": 640,
                        "height": 480,
                        "total_bytes": len(jpeg),
                        "chunk_bytes": MEDIA_CHUNK_BYTES,
                        "integrity": "crc32_per_chunk",
                    }
                )
            )
            ready = json.loads(await websocket.recv())
            assert ready["type"] == "camera_ready"
            assert ready["stream_id"] == stream_id
            assert ready["credits"] == 8

            chunks = 0
            for offset in range(0, len(jpeg), MEDIA_CHUNK_BYTES):
                payload = jpeg[offset : offset + MEDIA_CHUNK_BYTES]
                await websocket.send(
                    encode_media_frame(
                        PresenceMediaFrame(
                            kind=MEDIA_KIND_JPEG_UPLINK,
                            stream_id=stream_id,
                            sequence=chunks,
                            payload=payload,
                        )
                    )
                )
                credit = json.loads(await websocket.recv())
                assert credit["type"] == "camera_credit"
                assert credit["stream_id"] == stream_id
                assert credit["credits"] == 1
                chunks += 1

            await websocket.send(
                json.dumps(
                    {
                        "type": "camera_end",
                        "protocol": PROTOCOL_NAME,
                        "stream_id": stream_id,
                        "total_bytes": len(jpeg),
                        "chunks": chunks,
                    }
                )
            )
            receipt = json.loads(await websocket.recv())
            assert receipt["type"] == "camera_received"
            assert receipt["stream_id"] == stream_id
            assert receipt["total_bytes"] == len(jpeg)

            frame = await asyncio.wait_for(snapshot_task, timeout=1.0)
            assert frame.jpeg == jpeg
            assert frame.width == 640
            assert frame.height == 480
            assert frame.chunks == chunks
            snapshot = server.snapshot()
            assert snapshot["camera_started"] == 1
            assert snapshot["camera_completed"] == 1
            assert snapshot["camera_failed"] == 0
            assert snapshot["camera_bytes_received"] == len(jpeg)
    print("  camera: bounded JPEG, credits, CRC, and RAM-only receipt accepted")


async def test_camera_capability_and_cancel_ack_fail_closed_safely() -> None:
    async with _running_server(timeout=1.5) as (server, uri):
        async with connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        ) as websocket:
            await websocket.send(_hello(camera=False))
            assert json.loads(await websocket.recv())["type"] == "welcome"
            try:
                await server.capture_camera_jpeg_async()
                raise AssertionError("camera-disabled node accepted a snapshot")
            except PresenceCameraError as exc:
                assert "no camera-capable" in str(exc)

        async with connect(
            uri,
            additional_headers={"Authorization": f"Bearer {TOKEN}"},
            proxy=None,
        ) as websocket:
            await websocket.send(_hello("camera-cancel", camera=True))
            assert json.loads(await websocket.recv())["type"] == "welcome"
            snapshot_task = asyncio.create_task(server.capture_camera_jpeg_async())
            request = json.loads(await websocket.recv())
            await websocket.send(
                json.dumps(
                    {
                        "type": "camera_cancelled",
                        "protocol": PROTOCOL_NAME,
                        "stream_id": request["request_id"],
                        "reason": "core_cancelled",
                    }
                )
            )
            try:
                await asyncio.wait_for(snapshot_task, timeout=1.0)
                raise AssertionError("cancelled camera snapshot completed")
            except PresenceCameraError as exc:
                assert "cancelled" in str(exc)
    print("  camera: capability gate and idempotent cancellation accepted")


async def _run() -> None:
    tests = (
        test_authentication_rejects_missing_and_wrong_tokens,
        test_hello_welcome_and_heartbeat,
        test_binary_and_malformed_frames_fail_closed,
        test_duplicate_device_replaces_the_old_session,
        test_stale_heartbeat_closes_the_session,
        test_task_cancellation_releases_session_and_listener,
        test_repeated_shutdown_is_idempotent,
        test_partial_listener_start_failure_releases_runtime_state,
        test_bounded_pcm_downlink_drains_without_underrun,
        test_streaming_pcm_downlink_starts_before_total_is_known,
        test_playback_disconnect_fails_promptly_and_reconnects_cleanly,
        test_pcm_downlink_requires_advertised_capability,
        test_display_state_and_event_require_exact_acknowledgement,
        test_display_capability_and_tag_allowlist_fail_closed,
        test_pending_display_command_does_not_leak_across_reconnect,
        test_bounded_pcm_uplink_reaches_handler_and_completes_turn,
        test_pcm_uplink_fails_closed_without_capability_or_valid_sequence,
        test_bounded_jpeg_snapshot_uses_credits_and_stays_in_memory,
        test_camera_capability_and_cancel_ack_fail_closed_safely,
    )
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        await test()
    print(f"smoke_presence_session_server: PASS ({len(tests)}/{len(tests)})")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
