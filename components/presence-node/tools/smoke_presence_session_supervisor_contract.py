"""Static regression checks for the Presence session supervisor contract."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main"


def _read(name: str) -> str:
    return (MAIN / name).read_text(encoding="utf-8")


def _require(source: str, *needles: str) -> None:
    for needle in needles:
        assert needle in source, f"missing supervisor contract: {needle}"


def _require_order(source: str, *needles: str) -> None:
    positions = [source.index(needle) for needle in needles]
    assert positions == sorted(positions), (
        "supervisor cleanup order changed: " + " -> ".join(needles)
    )


def main() -> None:
    audio_header = _read("nana_session_audio.h")
    uplink_header = _read("nana_session_uplink.h")
    camera_header = _read("nana_session_camera.h")
    audio = _read("nana_session_audio.c")
    uplink = _read("nana_session_uplink.c")
    camera = _read("nana_session_camera.c")
    session = _read("nana_presence_session.c")

    _require(audio_header, "nana_session_audio_deinit(uint32_t timeout_ms)")
    _require(uplink_header, "nana_session_uplink_deinit(uint32_t timeout_ms)")
    _require(camera_header, "nana_session_camera_deinit(uint32_t timeout_ms)")

    _require(
        audio,
        "NANA_AUDIO_MESSAGE_SHUTDOWN",
        "static TaskHandle_t audio_task",
        "static SemaphoreHandle_t audio_stopped",
        "xSemaphoreGive(audio_stopped)",
        "nana_session_audio_deinit",
        "NANA_AUDIO_ADVERTISED_CREDITS + 2U",
    )
    _require(
        uplink,
        "UPLINK_SHUTDOWN_BIT",
        "static TaskHandle_t uplink_task",
        "static SemaphoreHandle_t uplink_stopped",
        "xSemaphoreGive(uplink_stopped)",
        "nana_session_uplink_deinit",
    )
    _require(
        camera,
        "CAMERA_SHUTDOWN_BIT",
        "static TaskHandle_t camera_task",
        "static SemaphoreHandle_t camera_stopped",
        "xSemaphoreGive(camera_stopped)",
        "nana_session_camera_deinit",
    )

    cleanup_start = session.index("static esp_err_t cleanup_session_supervisor")
    cleanup_end = session.index("static esp_err_t fail_session_start")
    cleanup = session[cleanup_start:cleanup_end]
    _require_order(
        cleanup,
        "nana_speaker_mute()",
        "nana_session_camera_deinit",
        "nana_session_uplink_deinit",
        "nana_session_audio_deinit",
        "vQueueDelete(context->tx_queue)",
        "vEventGroupDelete(context->events)",
    )
    assert cleanup.count("nana_speaker_mute()") >= 2

    _require(
        session,
        "NANA_SESSION_TX_QUEUE_DEPTH 24U",
        "NANA_SESSION_WORKER_STOP_TIMEOUT_MS 5000U",
        "NANA_SESSION_ACK_TIMEOUT_MS 15000U",
        "NANA_SESSION_CONNECT_TIMEOUT_MS 5000U",
        "NANA_SESSION_BACKOFF_MAX_MS 5000U",
        "NANA_SESSION_BACKOFF_JITTER_MS 250U",
        "session TX queue must hold one complete bounded media frame",
        "NANA_SESSION_SUPERVISOR state=rollback",
        "NANA_SESSION_SUPERVISOR state=ready",
    )
    assert session.count("return fail_session_start(") == 4

    print("smoke_presence_session_supervisor_contract: PASS")
    print("  workers: bounded queues plus acknowledged shutdown")
    print("  rollback: camera -> uplink -> audio -> session resources")
    print("  safety: speaker hard-muted before and after cleanup")


if __name__ == "__main__":
    main()
