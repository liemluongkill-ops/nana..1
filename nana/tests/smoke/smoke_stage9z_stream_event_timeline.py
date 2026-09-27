"""Smoke tests for STAGE-9Z stream event timeline.

Read-only/live-safe: no live Nana runtime, Discord bot, TTS, VTS, OBS, API,
memory write, or game input is used. The smoke writes only temp diagnostic
JSONL/request/reply files under a temporary directory.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _with_temp_timeline():
    temp = tempfile.TemporaryDirectory()
    old_path = os.environ.get("NANA_STREAM_EVENT_TIMELINE_PATH")
    timeline_path = Path(temp.name) / "stream_event_timeline.jsonl"
    os.environ["NANA_STREAM_EVENT_TIMELINE_PATH"] = str(timeline_path)
    return temp, old_path, timeline_path


def _restore_timeline_env(old_path: str | None) -> None:
    if old_path is None:
        os.environ.pop("NANA_STREAM_EVENT_TIMELINE_PATH", None)
    else:
        os.environ["NANA_STREAM_EVENT_TIMELINE_PATH"] = old_path


def _test_record_snapshot_and_status_lines():
    print("[9Z Smoke] Test 1: record/snapshot/status lines...")
    temp, old_path, timeline_path = _with_temp_timeline()
    try:
        from nana.runtime.stream_event_timeline import (
            clear_stream_event_timeline,
            record_stream_event,
            stream_event_timeline_snapshot,
            stream_event_timeline_status_lines,
        )

        clear_stream_event_timeline(remove_file=True)
        record_stream_event("first_event", request_id="r1", status="start")
        record_stream_event("second_event", request_id="r2", status="done")
        snap = stream_event_timeline_snapshot(limit=10)
        events = list(snap.get("events") or [])
        kinds = [event.get("kind") for event in events[-2:]]
        assert kinds == ["first_event", "second_event"], kinds
        assert snap["read_only"] is True, snap
        assert snap["can_act"] is False, snap
        assert snap["memory_write"] is False, snap
        assert snap["tts_call"] is False, snap
        assert snap["vts_call"] is False, snap
        assert snap["obs_call"] is False, snap
        assert snap["discord_call"] is False, snap
        assert snap["game_input"] is False, snap
        assert timeline_path.exists(), timeline_path

        text = "\n".join(stream_event_timeline_status_lines())
        assert "Stream Event Timeline (STAGE-9Z)" in text, text
        assert "second_event" in text, text
        assert "no LLM/TTS/VTS/OBS/Discord/API/game input" in text, text
        print("  PASSED")
    finally:
        _restore_timeline_env(old_path)
        temp.cleanup()


def _test_status_router_and_registry():
    print("[9Z Smoke] Test 2: status router and registry expose command...")
    temp, old_path, _timeline_path = _with_temp_timeline()
    try:
        from nana.cli.status_commands import handle_status_command
        from nana.commands.registry import KNOWN_SLASH_COMMANDS
        from nana.commands.router_manifest import classify_command_truth
        from nana.runtime.stream_event_timeline import clear_stream_event_timeline, record_stream_event

        clear_stream_event_timeline(remove_file=True)
        record_stream_event("router_probe", request_id="router")
        buf = io.StringIO()
        with redirect_stdout(buf):
            handled = handle_status_command(None, None, "/stream-event-log")
        out = buf.getvalue()
        assert handled is True, out
        assert "Stream Event Timeline" in out, out
        assert "Safety:" in out, out
        assert "/stream-event-log" in KNOWN_SLASH_COMMANDS
        assert classify_command_truth("/stream-event-log", known=True).status == "live"
        print("  PASSED")
    finally:
        _restore_timeline_env(old_path)
        temp.cleanup()


def _test_external_bridge_instrumentation_preserves_reply_behavior():
    print("[9Z Smoke] Test 3: external bridge instrumentation preserves reply behavior...")
    temp, old_path, _timeline_path = _with_temp_timeline()
    try:
        from nana.runtime.external_bridge import ExternalBridgeRuntime
        from nana.runtime.social_session import SocialSessionCache
        from nana.runtime.stream_event_timeline import clear_stream_event_timeline, stream_event_timeline_snapshot
        from nana.runtime.viewer_chat import ViewerChatQueue

        clear_stream_event_timeline(remove_file=True)
        base = Path(temp.name)
        request_dir = base / "requests"
        reply_dir = base / "replies"
        request_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "request_id": "timeline-bridge-1",
            "source": "discord",
            "event_type": "message",
            "text": "Nana timeline smoke",
            "guild_id": 1,
            "channel_id": 2,
            "author_id": 3,
            "author_name": "timeline_viewer",
            "audio_target": "discord_voice",
            "local_playback": False,
            "metadata": {"route": {"chat_channel_name": "smoke", "input_surface": "discord_text"}},
        }
        (request_dir / "timeline-bridge-1.json").write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        runtime = ExternalBridgeRuntime(
            request_dir=request_dir,
            reply_dir=reply_dir,
            queue=ViewerChatQueue(),
            social_session=SocialSessionCache(),
            enabled=True,
        )
        result = runtime.poll_once(responder=lambda _request: "Nana timeline ok.")
        assert result["processed"] == 1, result
        assert result["failed"] == 0, result
        reply_path = reply_dir / "timeline-bridge-1.json"
        assert reply_path.exists(), reply_path
        reply = json.loads(reply_path.read_text(encoding="utf-8"))
        assert reply["request_id"] == "timeline-bridge-1", reply
        assert reply["ok"] is True, reply
        assert reply["speak"] is False, reply
        assert reply["local_playback"] is False, reply

        events = list(stream_event_timeline_snapshot(limit=20).get("events") or [])
        kinds = {event.get("kind") for event in events}
        expected = {
            "bridge_request_start",
            "viewer_queue",
            "social_decision",
            "reply_text_ready",
            "reply_json_written",
        }
        assert expected <= kinds, kinds
        event_blob = json.dumps(events, ensure_ascii=False)
        assert "Nana timeline smoke" not in event_blob, event_blob
        bridge_start = next(event for event in events if event.get("kind") == "bridge_request_start")
        assert "text_preview" not in bridge_start, bridge_start
        assert bridge_start.get("has_text") is True, bridge_start
        assert bridge_start.get("text_chars") == len("Nana timeline smoke"), bridge_start
        assert bridge_start.get("text_hash"), bridge_start
        reply_written = next(event for event in events if event.get("kind") == "reply_json_written")
        assert "path" not in reply_written, reply_written
        assert reply_written.get("reply_file") == "timeline-bridge-1.json", reply_written
        print("  PASSED")
    finally:
        _restore_timeline_env(old_path)
        temp.cleanup()


def main():
    print("STAGE-9Z Stream Event Timeline - Smoke Tests")
    _test_record_snapshot_and_status_lines()
    _test_status_router_and_registry()
    _test_external_bridge_instrumentation_preserves_reply_behavior()
    print("[9Z Smoke] All tests passed.")


if __name__ == "__main__":
    main()
