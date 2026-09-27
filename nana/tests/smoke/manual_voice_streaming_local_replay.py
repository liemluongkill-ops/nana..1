"""Manual local-file replay through Nana's HTTP-stream PCM feeder.

This utility never calls ElevenLabs or any network API. It decodes an existing
MP3 with local FFmpeg, feeds the same bounded PCM queue used by the streaming
pilot, and prints output-feed telemetry after audible playback.
"""

from __future__ import annotations

import argparse
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def replay(path: Path, *, callback_output: bool = False) -> int:
    from nana.voice.lipsync import LipsyncManager

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("FFmpeg not found on PATH.")
        return 2

    pcm_queue = queue.Queue(maxsize=64)
    decoder_done = threading.Event()
    playback_done = threading.Event()
    playback_box = {}
    producer_errors = []
    manager = LipsyncManager()
    process = subprocess.Popen(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vn",
            "-acodec",
            "pcm_f32le",
            "-f",
            "f32le",
            "-ac",
            "1",
            "-ar",
            "44100",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )

    def produce_pcm():
        remainder = b""
        try:
            while not manager.stop_event.is_set():
                chunk = process.stdout.read(16384)
                if not chunk:
                    break
                payload = remainder + bytes(chunk)
                aligned = len(payload) - (len(payload) % 4)
                remainder = payload[aligned:]
                if aligned <= 0:
                    continue
                pcm = np.frombuffer(payload[:aligned], dtype="<f4").astype(np.float32, copy=True)
                while not manager.stop_event.is_set():
                    try:
                        pcm_queue.put(pcm, timeout=0.05)
                        break
                    except queue.Full:
                        continue
            return_code = process.wait(timeout=5.0)
            if remainder:
                producer_errors.append("decoder_partial_sample")
            if return_code != 0:
                producer_errors.append(f"ffmpeg_exit_{return_code}")
        except Exception as exc:
            producer_errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            decoder_done.set()

    def playback_finished(result):
        playback_box["result"] = result
        playback_done.set()

    producer = threading.Thread(target=produce_pcm, daemon=True, name="manual-local-pcm-producer")
    producer.start()
    manager.play_pcm_stream_nonblocking(
        pcm_queue,
        decoder_done,
        samplerate=44100,
        startup_buffer_ms=300,
        stall_timeout_s=45,
        on_done=playback_finished,
        callback_output=callback_output,
    )

    try:
        if not playback_done.wait(timeout=600.0):
            manager.stop()
            print("Local replay timed out.")
            return 3
    except KeyboardInterrupt:
        manager.stop()
        print("Local replay cancelled.")
        return 130
    finally:
        if process.poll() is None:
            process.terminate()
        producer.join(timeout=2.0)

    result = playback_box.get("result")
    if result is None:
        print("No playback result returned.")
        return 4

    print("Local Streaming Feeder Replay")
    print(f"  File: {path}")
    print(f"  Output mode: {'callback_ring' if callback_output else 'blocking_feeder'}")
    print(
        "  Completion: "
        f"completed={result.completed} | duration={result.played_duration_ms}ms | "
        f"abort={result.abort_reason}"
    )
    print(
        "  Output feed: "
        f"writes={result.output_write_calls} | refills={result.refill_count} | "
        f"true_rebuffers={result.rebuffer_count} | underflows={result.output_underflow_count} | "
        f"low_watermark={result.playback_buffer_low_watermark_ms}ms | "
        f"max_feed_gap={result.max_feed_gap_ms}ms"
    )
    if callback_output:
        print(
            "  Callback output: "
            f"callback_calls={result.callback_calls} | "
            f"callback_status_underflows={result.callback_status_underflows} | "
            f"ring_starvation_count={result.ring_starvation_count} | "
            f"ring_low_watermark={result.ring_low_watermark_ms}ms | "
            f"max_callback_lateness={result.max_callback_lateness_ms}ms | "
            f"feeder_refill_count={result.feeder_refill_count} | "
            f"feeder_done={result.feeder_done} | "
            f"callback_finished={result.callback_finished}"
        )
    if producer_errors:
        print(f"  Producer errors: {', '.join(producer_errors)}")
        return 5
    return 0 if result.completed else 6


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a local MP3 through Nana's streaming PCM feeder.")
    parser.add_argument("audio_file", type=Path)
    parser.add_argument(
        "--callback",
        action="store_true",
        help="Use the callback-driven SPSC PCM ring pilot.",
    )
    args = parser.parse_args()
    path = args.audio_file.expanduser().resolve()
    if not path.is_file():
        parser.error(f"audio file not found: {path}")
    return replay(path, callback_output=args.callback)


if __name__ == "__main__":
    raise SystemExit(main())
