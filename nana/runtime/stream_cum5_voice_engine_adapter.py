"""Dedicated local-speaker adapter for Stream V1 CUM5.

The adapter is lazy and single-flight. It does not use the private voice queue,
Presence output, audio cache, fallback speech, avatar mouth output, or OBS.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from nana.runtime.stream_cum2_response import CUM2_MAX_RESPONSE_CHARS
from nana.runtime.stream_cum3_youtube_publish import CUM3_MAX_MESSAGE_CHARS
from nana.runtime.stream_cum5_voice_playback import (
    AudioCompletionResult,
    FirstAudioEvidence,
    PlaybackReadiness,
    PlaybackStopResult,
    PublicVoicePlaybackRequest,
)


class VoiceEnginePublicPlaybackPort:
    """Map one CUM5 request to one exact VoiceEngine playback call."""

    def __init__(
        self,
        *,
        engine_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.time,
        output_stream_factory: Callable[..., Any] | None = None,
        visual_signal_store: Any | None = None,
        assume_ready: bool = False,
    ) -> None:
        self._engine_factory = engine_factory
        self._clock = clock
        self._output_stream_factory = output_stream_factory
        self._visual_signal_store = visual_signal_store
        self._assume_ready = bool(assume_ready)
        self._engine = None
        self._active_playback_id: str | None = None
        self._last_playback_id: str | None = None
        self._cancel_requested = False
        self._last_stop_confirmed = True
        self._active_visual_identity = None
        self._visual_sequence = 0
        self._lock = threading.RLock()

    def ready(self) -> PlaybackReadiness:
        if self._assume_ready or self._engine_factory is not None:
            return PlaybackReadiness(True, "ready")
        try:
            from nana.config import (
                DEBUG_NO_TTS,
                ELEVEN_API_KEY,
                VOICE_ID,
                VOICE_TEST_MODE,
            )
        except Exception:
            return PlaybackReadiness(False, "voice_config_unavailable")
        if DEBUG_NO_TTS or VOICE_TEST_MODE:
            return PlaybackReadiness(False, "voice_disabled")
        if not str(ELEVEN_API_KEY or "").strip() or ELEVEN_API_KEY == "ELEVENLABS_KEY_CUA_BAN":
            return PlaybackReadiness(False, "eleven_credentials_missing")
        if not str(VOICE_ID or "").strip():
            return PlaybackReadiness(False, "voice_id_missing")
        return PlaybackReadiness(True, "ready")

    def _get_engine(self):
        with self._lock:
            if self._engine is None:
                if self._engine_factory is None:
                    from nana.voice.engine import VoiceEngine

                    self._engine = VoiceEngine(
                        avatar_mouth_enabled=False,
                        start_worker=False,
                    )
                else:
                    self._engine = self._engine_factory()
            return self._engine

    def play(
        self,
        request: PublicVoicePlaybackRequest,
        *,
        should_continue,
        on_first_audio,
    ) -> AudioCompletionResult:
        if not isinstance(request, PublicVoicePlaybackRequest):
            raise TypeError("request must be PublicVoicePlaybackRequest")
        if not callable(should_continue) or not callable(on_first_audio):
            raise TypeError("playback callbacks must be callable")
        with self._lock:
            if self._active_playback_id is not None:
                raise RuntimeError("public_playback_busy")
            self._active_playback_id = request.playback_id
            self._last_playback_id = request.playback_id
            self._cancel_requested = False
            self._last_stop_confirmed = False

        visual_identity = self._begin_visual_playback(request)

        engine = None

        def continue_allowed() -> bool:
            with self._lock:
                if (
                    self._cancel_requested
                    or self._active_playback_id != request.playback_id
                ):
                    return False
            try:
                allowed = should_continue() is True
            except Exception:
                return False
            if not allowed:
                return False
            with self._lock:
                return bool(
                    not self._cancel_requested
                    and self._active_playback_id == request.playback_id
                )

        def first_audio(frame_bytes: int) -> bool:
            if not continue_allowed():
                return False
            evidence = FirstAudioEvidence(
                playback_id=request.playback_id,
                content_sha256=request.content_sha256,
                timestamp=max(float(self._clock()), request.requested_at),
                frame_bytes=int(frame_bytes),
            )
            return on_first_audio(evidence) is True

        def sink_frame(**payload: Any) -> None:
            self._publish_visual_frame(request.playback_id, visual_identity, payload)

        try:
            if not continue_allowed():
                with self._lock:
                    self._last_stop_confirmed = True
                return self._completion(
                    request,
                    state="cancelled",
                    audio_completed=False,
                    fetched=0,
                    played=0,
                    remaining=len(request.artifact.text),
                    duration_ms=0.0,
                    reason="dispatch_cancelled",
                    stop_confirmed=True,
                )
            engine = self._get_engine()
            if not continue_allowed():
                confirmed = self._cancel_engine(engine)
                with self._lock:
                    self._last_stop_confirmed = confirmed
                return self._completion(
                    request,
                    state="cancelled" if confirmed else "ambiguous",
                    audio_completed=False,
                    fetched=0,
                    played=0,
                    remaining=len(request.artifact.text),
                    duration_ms=0.0,
                    reason="dispatch_cancelled",
                    stop_confirmed=confirmed,
                )
            result = engine.play_public_once(
                request.artifact.text,
                max_text_chars=(
                    CUM2_MAX_RESPONSE_CHARS
                    if request.generated_record.delivery_mode == "voice_only"
                    else CUM3_MAX_MESSAGE_CHARS
                ),
                before_provider=continue_allowed,
                before_first_audio=continue_allowed,
                on_first_audio=first_audio,
                on_sink_frame=sink_frame,
                output_stream_factory=self._output_stream_factory,
            )
            engine_state = str(getattr(result, "state", "ambiguous"))
            audio_completed = bool(getattr(result, "audio_completed", False))
            terminal_state = (
                "completed"
                if engine_state == "completed" and audio_completed
                else "cancelled"
                if engine_state == "cancelled"
                else "failed"
            )
            stop_confirmed = bool(getattr(result, "stop_confirmed", False))
            with self._lock:
                self._last_stop_confirmed = stop_confirmed
            return self._completion(
                request,
                state=terminal_state,
                audio_completed=audio_completed,
                requested=int(getattr(result, "requested_segments", 1) or 0),
                fetched=int(getattr(result, "fetched_segments", 0) or 0),
                played=int(getattr(result, "played_segments", 0) or 0),
                unplayed=int(getattr(result, "unplayed_segments", 1) or 0),
                original_chars=int(
                    getattr(result, "original_chars", len(request.artifact.text))
                    or 0
                ),
                remaining=int(
                    getattr(result, "remaining_chars", len(request.artifact.text))
                    or 0
                ),
                duration_ms=float(getattr(result, "played_duration_ms", 0.0) or 0.0),
                reason=str(getattr(result, "abort_reason", "ambiguous_completion") or "ambiguous_completion"),
                stop_confirmed=stop_confirmed,
            )
        except Exception:
            confirmed = self._cancel_engine(engine) if engine is not None else False
            with self._lock:
                self._last_stop_confirmed = confirmed
            return self._completion(
                request,
                state="failed" if confirmed else "ambiguous",
                audio_completed=False,
                fetched=0,
                played=0,
                remaining=len(request.artifact.text),
                duration_ms=0.0,
                reason="playback_exception",
                stop_confirmed=confirmed,
            )
        finally:
            with self._lock:
                release_engine = bool(self._last_stop_confirmed)
                self._active_playback_id = None
                self._cancel_requested = False
                if self._active_visual_identity == visual_identity:
                    self._active_visual_identity = None
                    self._visual_sequence = 0
                if release_engine and self._engine is engine:
                    self._engine = None
            if release_engine and engine is not None:
                self._shutdown_engine(engine)

    def cancel(self, playback_id: str) -> PlaybackStopResult:
        with self._lock:
            if playback_id not in {self._active_playback_id, self._last_playback_id}:
                return PlaybackStopResult("ambiguous", "playback_id_mismatch")
            active = self._active_playback_id == playback_id
            if active:
                self._cancel_requested = True
            visual_identity = self._active_visual_identity if active else None
            engine = self._engine
        self._emit_visual_terminal(visual_identity, "cancelled")
        if engine is None:
            if active:
                with self._lock:
                    self._last_stop_confirmed = True
                return PlaybackStopResult(
                    "confirmed_stopped",
                    "cancel_latched_before_start",
                )
            with self._lock:
                confirmed = self._last_stop_confirmed
            return PlaybackStopResult(
                "confirmed_stopped" if confirmed else "ambiguous",
                "already_stopped" if confirmed else "stop_unconfirmed",
            )
        if self._cancel_engine(engine):
            with self._lock:
                self._last_stop_confirmed = True
                if self._engine is engine:
                    self._engine = None
            self._shutdown_engine(engine)
            return PlaybackStopResult("confirmed_stopped", "local_sink_stopped")
        return PlaybackStopResult("ambiguous", "stop_unconfirmed")

    @staticmethod
    def _cancel_engine(engine: Any) -> bool:
        cancel = getattr(engine, "cancel_public_playback", None)
        if not callable(cancel):
            return False
        try:
            return cancel() is True
        except Exception:
            return False

    @staticmethod
    def _shutdown_engine(engine: Any) -> None:
        shutdown = getattr(engine, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass

    def _begin_visual_playback(self, request: PublicVoicePlaybackRequest):
        store = self._visual_signal_store
        if store is None:
            return None
        try:
            from nana.runtime.stream_public_visual_signals import (
                PUBLIC_EXPRESSION_ACTIONS,
                identity_from_playback_request,
            )

            identity = identity_from_playback_request(request)
            action = None
            try:
                from nana.runtime.avatar_reaction_policy import (
                    action_block_reason,
                    select_reply_cue,
                )

                cue = select_reply_cue(request.artifact.text)
                candidate = str(getattr(cue, "action", "") or "")
                if (
                    candidate in PUBLIC_EXPRESSION_ACTIONS
                    and not action_block_reason(candidate)
                ):
                    action = candidate
            except Exception:
                action = None
            if store.begin_playback(identity, expression_action=action) is not True:
                return None
            with self._lock:
                self._active_visual_identity = identity
                self._visual_sequence = 0
            return identity
        except Exception:
            return None

    def _publish_visual_frame(
        self,
        playback_id: str,
        identity: Any,
        payload: dict[str, Any],
    ) -> None:
        store = self._visual_signal_store
        if store is None or identity is None:
            return
        with self._lock:
            if (
                self._active_playback_id != playback_id
                or self._active_visual_identity != identity
                or self._cancel_requested
            ):
                return
            sequence = self._visual_sequence + 1
        try:
            accepted = store.publish_frame(
                identity,
                sequence=sequence,
                open_value=payload.get("open_value"),
                energy=payload.get("energy"),
                viseme=payload.get("viseme"),
                speaking=payload.get("speaking"),
            ) is True
        except Exception:
            accepted = False
        if accepted:
            with self._lock:
                if self._active_visual_identity == identity:
                    self._visual_sequence = sequence

    def _emit_visual_terminal(self, identity: Any, reason: str) -> None:
        store = self._visual_signal_store
        if store is None or identity is None:
            return
        try:
            store.terminal(identity, reason)
        except Exception:
            pass

    def _completion(
        self,
        request: PublicVoicePlaybackRequest,
        *,
        state: str,
        audio_completed: bool,
        fetched: int,
        played: int,
        remaining: int,
        duration_ms: float,
        reason: str,
        stop_confirmed: bool,
        requested: int = 1,
        unplayed: int | None = None,
        original_chars: int | None = None,
    ) -> AudioCompletionResult:
        requested_i = max(0, int(requested))
        played_i = max(0, int(played))
        result = AudioCompletionResult(
            state=state,
            audio_completed=bool(audio_completed),
            requested_segments=requested_i,
            fetched_segments=max(0, int(fetched)),
            played_segments=played_i,
            unplayed_segments=(
                max(0, requested_i - played_i)
                if unplayed is None
                else max(0, int(unplayed))
            ),
            original_chars=(
                len(request.artifact.text)
                if original_chars is None
                else max(0, int(original_chars))
            ),
            remaining_chars=max(0, int(remaining)),
            played_duration_ms=max(0.0, float(duration_ms)),
            abort_reason=reason,
            playback_id=request.playback_id,
            content_sha256=request.content_sha256,
            completed_at=max(float(self._clock()), request.requested_at),
            stop_confirmed=bool(stop_confirmed),
        )
        visual_reason = (
            "completed"
            if state == "completed" and audio_completed
            else "cancelled"
            if state == "cancelled"
            else "failed"
        )
        with self._lock:
            visual_identity = (
                self._active_visual_identity
                if self._active_playback_id == request.playback_id
                else None
            )
        self._emit_visual_terminal(visual_identity, visual_reason)
        return result


__all__ = ["VoiceEnginePublicPlaybackPort"]
