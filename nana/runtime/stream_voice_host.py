"""Bounded composition of CUM4 generation and CUM5 voice-only playback."""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import threading
from typing import Any

from nana.runtime.stream_cum4_host import HostResult, YouTubeTextHost
from nana.runtime.stream_cum5_voice_playback import (
    CUM5PlaybackResult,
    PublicVoicePlaybackController,
)
from nana.runtime.stream_session_control import StreamSessionControl


PHASE = "STREAM-V1-VOICE-HOST"
_SEPARATOR = "\x1f"


def _voice_attempt_id(artifact: Any) -> str:
    scope = artifact.scope
    parts = (
        scope.platform,
        scope.room_id,
        scope.stream_session_id,
        scope.event_id,
        artifact.output_id,
    )
    if not all(isinstance(part, str) and part for part in parts):
        raise ValueError("invalid_response_artifact")
    digest = sha256(_SEPARATOR.join(parts).encode("utf-8")).hexdigest()[:24]
    attempt_id = f"voice-{digest}"
    if attempt_id == getattr(artifact, "source_attempt_id", None):
        attempt_id = f"voice-1-{digest}"
    return attempt_id


def _safe_reason_code(value: Any, fallback: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 96 or not value.isascii():
        return fallback
    if not all(character.isalnum() or character in "_.-" for character in value):
        return fallback
    return value


@dataclass(frozen=True)
class VoiceHostResult:
    host_result: HostResult
    playback_result: CUM5PlaybackResult | None = None


class PublicVoiceHost:
    """Generate and play at most one full public reply per serialized step."""

    def __init__(
        self,
        *,
        host: YouTubeTextHost,
        playback: PublicVoicePlaybackController,
        control: StreamSessionControl | None = None,
    ) -> None:
        self.host = host
        self.playback = playback
        self.control = control
        self._step_lock = threading.Lock()
        self._halted = False
        self._halt_reason = ""

    def _queued(self) -> int:
        try:
            value = self.host.snapshot().get("queued", 0)
        except Exception:
            return 0
        return value if type(value) is int and value >= 0 else 0

    def _halted_result(self, reason_code: str) -> HostResult:
        return HostResult("halted", reason_code, queued=self._queued())

    def _stopped_result(self) -> HostResult:
        reason = "operator_stop"
        if self.control is not None:
            reason = _safe_reason_code(self.control.stop_reason, reason)
        return HostResult("stopped", reason, queued=self._queued())

    def process_next(self, *, now: float) -> VoiceHostResult:
        if not self._step_lock.acquire(blocking=False):
            return VoiceHostResult(
                HostResult("held", "voice_host_busy", queued=self._queued())
            )
        try:
            if self._halted:
                return VoiceHostResult(self._halted_result(self._halt_reason))
            if self.control is not None and self.control.stop_requested:
                return VoiceHostResult(self._stopped_result())

            try:
                playback_state = self.playback.snapshot()
                playback_halted = bool(playback_state.get("halted", False))
                playback_active = bool(playback_state.get("active", False))
            except Exception:
                self._halted = True
                self._halt_reason = "playback_state_error"
                return VoiceHostResult(self._halted_result(self._halt_reason))

            if playback_halted:
                self._halted = True
                self._halt_reason = "playback_halted"
                return VoiceHostResult(self._halted_result(self._halt_reason))
            if playback_active:
                return VoiceHostResult(
                    HostResult("held", "playback_busy", queued=self._queued())
                )

            if self.control is None:
                host_result = self.host.generate_next(now=now, full_replies_only=True)
            else:
                queued = self._queued()
                if queued > 0:
                    self.control.stage("thinking", queued=queued)
                if self.control.stop_requested:
                    return VoiceHostResult(self._stopped_result())
                host_result = self.host.generate_next(
                    now=now,
                    full_replies_only=True,
                    max_job_age_seconds=30.0,
                )
                if self.control.stop_requested:
                    return VoiceHostResult(self._stopped_result())
            artifact = host_result.response_artifact
            if host_result.status != "generated" or artifact is None:
                return VoiceHostResult(host_result)

            try:
                attempt_id = _voice_attempt_id(artifact)
            except (AttributeError, TypeError, ValueError):
                return VoiceHostResult(
                    host_result,
                    CUM5PlaybackResult("rejected", "invalid_response_artifact"),
                )

            if self.control is not None:
                self.control.stage("preparing_audio", queued=self._queued())
                if self.control.stop_requested:
                    return VoiceHostResult(self._stopped_result())
            try:
                playback_result = self.playback.play_generated(
                    artifact,
                    attempt_id=attempt_id,
                    now=now,
                )
            except Exception:
                playback_result = CUM5PlaybackResult("halted", "voice_playback_error")

            if (
                self.control is not None
                and self.control.stop_requested
                and playback_result.status != "halted"
            ):
                return VoiceHostResult(
                    replace(
                        host_result,
                        status="stopped",
                        reason_code=self._stopped_result().reason_code,
                    ),
                    playback_result,
                )

            if playback_result.status in {"interrupted", "halted"}:
                fallback = (
                    "voice_playback_interrupted"
                    if playback_result.status == "interrupted"
                    else "voice_playback_halted"
                )
                reason = _safe_reason_code(playback_result.reason_code, fallback)
                self._halted = True
                self._halt_reason = reason
                return VoiceHostResult(
                    replace(host_result, status="halted", reason_code=reason),
                    playback_result,
                )
            return VoiceHostResult(host_result, playback_result)
        finally:
            self._step_lock.release()


__all__ = [
    "PHASE",
    "PublicVoiceHost",
    "VoiceHostResult",
]
