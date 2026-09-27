"""Explicit Presence media commands for the Nana CLI."""

from __future__ import annotations

from array import array
import asyncio
import math
import sys
import time


_CAMERA_REQUEST_WAIT_SECONDS = 30.0
_CAMERA_REQUEST_RETRY_SECONDS = 0.10
_CAMERA_BUSY_REASONS = (
    "no camera-capable Presence node is connected",
    "camera cannot start during playback",
    "camera cannot start during an audio turn",
    "camera snapshot is already active",
)
_VOICE_STRESS_MIN_CHARS = 240
_VOICE_STRESS_MAX_CHARS = 1500
_VOICE_STRESS_TIMEOUT_SECONDS = 360.0
_VOICE_STRESS_CLOSING = " Kết thúc bài kiểm tra."
_VOICE_STRESS_SENTENCES = (
    "Đêm nay đường âm thanh của Nana được kiểm tra bằng một văn bản cố định, không nhờ mô hình tự ước lượng số ký tự.",
    "Từng câu có độ dài khác nhau để thử giọng tiếng Việt, dấu câu, phụ âm cuối và nhịp nghỉ tự nhiên giữa các ý.",
    "Dữ liệu đi từ ElevenLabs về Core, được chia thành các stream nhỏ rồi chuyển qua Wi-Fi đến ESP32 theo đúng thứ tự.",
    "Mỗi stream phải được nhận đủ, phát đủ và kết thúc sạch trước khi phần tiếp theo bắt đầu đi qua bộ đệm PSRAM.",
    "Các từ ESP32, ElevenLabs, WebSocket, PSRAM và MAX98357 giúp kiểm tra cả tiếng Việt lẫn những cụm kỹ thuật quen thuộc.",
    "Nếu mạng chậm trong chốc lát, hàng đợi có giới hạn phải hấp thụ độ trễ mà không lặp âm, nuốt chữ hay phát tiếng rè.",
    "Bài kiểm tra cũng theo dõi số segment, tổng byte, underrun, trạng thái hoàn tất và phần văn bản thực sự được gửi đi.",
    "Nội dung này không gọi LLM nên số lượng ký tự luôn có thể được máy đếm lại chính xác trước khi dùng một credit TTS nào.",
    "Trong sử dụng hằng ngày Nana thường trả lời ngắn hơn nhiều, nhưng bài stress vẫn cần chạm đúng trần đã công bố.",
    "Khi toàn bộ đoạn dài chạy sạch nhiều lần, giới hạn đó trở thành bằng chứng tải thay vì một con số nhìn bằng mắt trong terminal.",
    "Cơ chế chia stream là phần bảo vệ production, còn việc cắt toàn bộ câu trả lời chỉ được bật bằng cấu hình thử nghiệm rõ ràng.",
    "Nhờ vậy Nana không âm thầm bỏ mất lời nói, đồng thời từng khối PCM vẫn nằm dưới giới hạn bộ nhớ của phần cứng.",
    "Người nghe chỉ cần xác nhận âm lượng đều, không nói lắp, không hụt đoạn cuối và không xuất hiện khoảng rè bất thường.",
    "Sau lượt cuối, Core phải báo chính xác số ký tự nguồn, số phần đã phát và mọi bộ đếm lỗi còn bằng không.",
)
_camera_snapshot_task: asyncio.Task | None = None
_stream_tone_task: asyncio.Task | None = None
_voice_stress_task: asyncio.Task | None = None


def _build_presence_voice_stress_text(target_chars: int = 1500) -> str:
    """Build deterministic natural speech with an exact character count."""

    target = int(target_chars)
    if not _VOICE_STRESS_MIN_CHARS <= target <= _VOICE_STRESS_MAX_CHARS:
        raise ValueError(
            "Presence voice stress length must be between "
            f"{_VOICE_STRESS_MIN_CHARS} and {_VOICE_STRESS_MAX_CHARS} characters"
        )

    source = " ".join(_VOICE_STRESS_SENTENCES)
    while len(source) < target + len(_VOICE_STRESS_CLOSING):
        source = f"{source} {source}"

    prefix_limit = target - len(_VOICE_STRESS_CLOSING)
    boundary = source.rfind(" ", 0, prefix_limit + 1)
    if boundary <= 0:
        raise RuntimeError("Presence voice stress corpus has no safe word boundary")
    prefix = source[:boundary].rstrip()
    padding = target - len(prefix) - len(_VOICE_STRESS_CLOSING)
    result = prefix + (" " * padding) + _VOICE_STRESS_CLOSING
    if len(result) != target:
        raise RuntimeError("Presence voice stress generator produced a wrong length")
    return result


async def _run_presence_voice_stress(voice, target_chars: int) -> None:
    from nana.runtime.presence_session_server import (
        get_active_presence_session_server,
    )

    server = get_active_presence_session_server()
    if server is None:
        print("Presence voice stress: Presence Session is not running.")
        return
    if not server.audio_downlink_stream_available():
        print("Presence voice stress: no streaming-audio-capable node is connected.")
        return

    stress_text = _build_presence_voice_stress_text(target_chars)
    segments = voice._presence_tts_segments(stress_text)
    print(
        "Presence voice stress starting | "
        f"chars={len(stress_text)} | segments={len(segments)} | "
        f"segment_chars={','.join(str(len(segment)) for segment in segments)} | "
        "source=deterministic | llm=unused"
    )
    ticket = voice.say(stress_text, voice_mode="full")
    completed = await asyncio.to_thread(
        voice.wait_for_voice_ticket,
        ticket,
        _VOICE_STRESS_TIMEOUT_SECONDS,
    )
    runtime = voice.snapshot()
    requested = int(runtime.get("last_audio_requested_segments", 0) or 0)
    played = int(runtime.get("last_audio_played_segments", 0) or 0)
    fetched = int(runtime.get("last_audio_fetched_segments", 0) or 0)
    input_chars = int(runtime.get("last_presence_pcm_input_chars", 0) or 0)
    source_chars = int(runtime.get("last_presence_pcm_text_chars", 0) or 0)
    underruns = runtime.get("last_presence_pcm_underruns")
    status = str(runtime.get("last_presence_pcm_status", "none"))
    passed = (
        completed
        and bool(runtime.get("last_audio_completed", False))
        and status == "complete_streaming"
        and input_chars == target_chars
        and source_chars == target_chars
        and not bool(runtime.get("last_presence_pcm_text_truncated", False))
        and requested == len(segments)
        and fetched == requested
        and played == requested
        and underruns == 0
    )
    outcome = "PASS" if passed else "FAIL"
    print(
        f"Presence voice stress: {outcome} | chars={source_chars}/{input_chars} | "
        f"segments={played}/{requested} | fetched={fetched} | "
        f"played={runtime.get('last_presence_pcm_bytes', 0)}B | "
        f"underruns={underruns} | grade="
        f"{runtime.get('last_presence_pcm_playback_grade', 'none')} | "
        f"status={status} | limit_enabled="
        f"{runtime.get('presence_voice_limit_enabled', False)}"
    )


def _build_stream_tone_pcm(
    *,
    duration_seconds: float = 4.0,
    sample_rate: int = 16000,
    frequency_hz: float = 440.0,
    gain: float = 0.10,
) -> bytes:
    """Build a bounded tone that exposes gaps without hard edge clicks."""

    if not 0.5 <= duration_seconds <= 10.0:
        raise ValueError("tone duration must be between 0.5 and 10 seconds")
    if sample_rate != 16000:
        raise ValueError("Presence tone requires 16000 Hz")
    if not 0.0 < gain <= 0.25:
        raise ValueError("tone gain must be between 0 and 0.25")

    sample_count = int(round(duration_seconds * sample_rate))
    fade_samples = max(1, int(round(sample_rate * 0.05)))
    amplitude = 32767.0 * gain
    samples = array("h")
    for index in range(sample_count):
        fade_in = min(1.0, index / fade_samples)
        fade_out = min(1.0, (sample_count - 1 - index) / fade_samples)
        envelope = max(0.0, min(fade_in, fade_out))
        value = amplitude * envelope * math.sin(
            2.0 * math.pi * frequency_hz * index / sample_rate
        )
        samples.append(int(round(value)))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def _iter_pcm_chunks(pcm: bytes, chunk_bytes: int = 2048):
    if chunk_bytes <= 0 or chunk_bytes % 2:
        raise ValueError("PCM chunk size must be a positive even number")
    for offset in range(0, len(pcm), chunk_bytes):
        yield pcm[offset : offset + chunk_bytes]


async def _play_presence_stream_tone(server=None) -> None:
    if server is None:
        from nana.runtime.presence_session_server import (
            get_active_presence_session_server,
        )

        server = get_active_presence_session_server()
    if server is None:
        print("Presence stream tone: Presence Session is not running.")
        return
    if not server.audio_downlink_stream_available():
        print("Presence stream tone: no streaming-audio-capable node is connected.")
        return

    pcm = _build_stream_tone_pcm()
    try:
        result = await server.play_pcm16_stream_async(
            _iter_pcm_chunks(pcm),
            sample_rate=16000,
        )
    except Exception as exc:
        print(f"Presence stream tone failed: {type(exc).__name__}: {exc}")
        return

    first_audio = (
        "none"
        if result.first_audio_ms is None
        else f"{result.first_audio_ms:.1f}ms"
    )
    print(
        "Presence stream tone: PASS | source=generated_pcm | "
        f"duration={len(pcm) / 32.0:.1f}ms | "
        f"sent={result.total_bytes}B | received={result.bytes_received}B | "
        f"played={result.bytes_played}B | "
        f"queue_high_water={result.queue_high_water} | "
        f"underruns={result.underruns} | "
        f"first_audio={first_audio} | "
        f"elapsed={result.elapsed_ms:.1f}ms"
    )


async def _capture_camera_frame_when_ready(
    server,
    *,
    camera_error_type: type[Exception],
    wait_seconds: float = _CAMERA_REQUEST_WAIT_SECONDS,
    retry_seconds: float = _CAMERA_REQUEST_RETRY_SECONDS,
):
    """Wait for a connected idle media lease, while preserving real failures."""

    if wait_seconds <= 0.0 or retry_seconds <= 0.0:
        raise ValueError("camera wait and retry intervals must be positive")
    deadline = time.monotonic() + wait_seconds
    last_reason = "camera-capable node is not connected"
    while True:
        if server.camera_available():
            try:
                return await server.capture_camera_jpeg_async()
            except camera_error_type as exc:
                last_reason = str(exc)
                if not any(reason in last_reason for reason in _CAMERA_BUSY_REASONS):
                    raise

        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            raise TimeoutError(
                "timed out waiting for a camera-capable node and idle media "
                f"lease; last={last_reason}"
            )
        await asyncio.sleep(min(retry_seconds, remaining))


async def _capture_camera_snapshot() -> None:
    from nana.runtime.presence_camera_vision import (
        PresenceCameraVisionError,
        YuNetFacePresenceDetector,
    )
    from nana.runtime.presence_session_server import (
        PresenceCameraError,
        get_active_presence_session_server,
    )

    server = get_active_presence_session_server()
    if server is None:
        print("Presence camera snapshot: Presence Session is not running.")
        return

    try:
        frame = await _capture_camera_frame_when_ready(
            server,
            camera_error_type=PresenceCameraError,
        )
        detector = YuNetFacePresenceDetector()
        result = await asyncio.to_thread(
            detector.detect_jpeg,
            frame.jpeg,
            expected_width=frame.width,
            expected_height=frame.height,
        )
    except (PresenceCameraError, PresenceCameraVisionError, TimeoutError) as exc:
        print(f"Presence camera snapshot failed: {exc}")
        return
    except Exception as exc:
        print(
            "Presence camera snapshot failed: "
            f"{type(exc).__name__}: {exc}"
        )
        return

    state = "present" if result.present else "absent"
    print(
        "Presence camera snapshot: PASS | "
        f"state={state} | faces={result.face_count} | "
        f"confidence={result.max_confidence:.3f} | "
        f"frame={result.frame_width}x{result.frame_height} | "
        f"jpeg={len(frame.jpeg)}B | transfer={frame.elapsed_ms:.1f}ms | "
        f"yunet={result.inference_ms:.1f}ms | persistence=none"
    )


def handle_presence_command(loop, text_lower: str, *, voice=None) -> bool:
    """Schedule explicit Presence media actions without blocking the CLI."""

    global _camera_snapshot_task, _stream_tone_task, _voice_stress_task

    camera_command = text_lower in {
        "/presence-camera-snapshot",
        "/presence-camera-test",
    }
    tone_command = text_lower == "/presence-stream-tone"
    stress_command = (
        text_lower == "/presence-voice-stress"
        or text_lower.startswith("/presence-voice-stress ")
    )
    if not camera_command and not tone_command and not stress_command:
        return False
    if loop is None or not loop.is_running():
        action = (
            "voice stress"
            if stress_command
            else "stream tone"
            if tone_command
            else "camera snapshot"
        )
        print(f"Presence {action}: Nana event loop is not running.")
        return True

    if stress_command:
        if voice is None:
            print("Presence voice stress: VoiceEngine is not available.")
            return True
        raw_target = text_lower[len("/presence-voice-stress") :].strip()
        try:
            target_chars = int(raw_target) if raw_target else 1500
            _build_presence_voice_stress_text(target_chars)
        except (TypeError, ValueError) as exc:
            print(f"Presence voice stress: {exc}")
            return True
        if _voice_stress_task is not None and not _voice_stress_task.done():
            print("Presence voice stress: one test is already running.")
            return True
        task = loop.create_task(
            _run_presence_voice_stress(voice, target_chars),
            name="nana-presence-voice-stress",
        )
        _voice_stress_task = task

        def clear_voice_stress(completed: asyncio.Task) -> None:
            global _voice_stress_task
            if _voice_stress_task is completed:
                _voice_stress_task = None

        task.add_done_callback(clear_voice_stress)
        print(
            "Presence voice stress queued: "
            f"exactly {target_chars} deterministic characters; "
            "LLM and microphone are not used."
        )
        return True

    if tone_command:
        if _stream_tone_task is not None and not _stream_tone_task.done():
            print("Presence stream tone: one test is already running.")
            return True
        task = loop.create_task(
            _play_presence_stream_tone(),
            name="nana-presence-stream-tone",
        )
        _stream_tone_task = task

        def clear_tone(completed: asyncio.Task) -> None:
            global _stream_tone_task
            if _stream_tone_task is completed:
                _stream_tone_task = None

        task.add_done_callback(clear_tone)
        print(
            "Presence stream tone queued: 440 Hz for 4 seconds; "
            "microphone and ElevenLabs are not used."
        )
        return True

    if _camera_snapshot_task is not None and not _camera_snapshot_task.done():
        print("Presence camera snapshot: one request is already queued.")
        return True
    task = loop.create_task(
        _capture_camera_snapshot(),
        name="nana-presence-camera-snapshot",
    )
    _camera_snapshot_task = task

    def clear_completed(completed: asyncio.Task) -> None:
        global _camera_snapshot_task
        if _camera_snapshot_task is completed:
            _camera_snapshot_task = None

    task.add_done_callback(clear_completed)
    print(
        "Presence camera snapshot queued; waiting up to 30s for the node and "
        "current audio turn to become idle."
    )
    return True


__all__ = ["handle_presence_command"]
