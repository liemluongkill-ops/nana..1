"""STAGE-8A: Stream State Core for Nana VTuber.

Nana must know what "stage" she is on before she knows what to say.

State machine:
    offline | live_idle | live_active | intermission | post_stream | error_safe

Policy object (output only, no action):
    state, reason
    can_proactive, can_auto_send, can_use_private_memory
    proactive_budget
    interaction_tone
    avatar_energy
    viewer_expectation
    error_type, can_reply, can_speak, can_avatar

Transitions:
    offline -> live_idle (manual /stream-live-on or platform signal)
    live_idle <-> live_active (chat velocity)
    live_* -> intermission (manual /stream-break)
    intermission -> live_* (manual /stream-resume)
    live_* -> post_stream (manual /stream-live-off)
    any -> error_safe (bridge/voice/VTS error)
    error_safe -> offline (manual recovery)

Signals:
    manual: /stream-live-on, /stream-live-off, /stream-break, /stream-resume, /stream-status
    platform: live_start, live_end, live_timeout
    runtime: chat_velocity, viewer_count, bridge_healthy, voice_ready, vts_connected, owner_focus

Note: live_focus maps to persona.focus (persona_spine mode), not a separate stream state.
Note: rehearsal is not a separate state — it's "offline with bridge running".
"""

from __future__ import annotations

import enum
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Optional

# ─── Valid States ────────────────────────────────────────────────────────────


class StreamState(str, enum.Enum):
    OFFLINE = "offline"
    LIVE_IDLE = "live_idle"
    LIVE_ACTIVE = "live_active"
    INTERMISSION = "intermission"
    POST_STREAM = "post_stream"
    ERROR_SAFE = "error_safe"


# ─── Error Types (only meaningful when state == error_safe) ──────────────────


class ErrorType(str, enum.Enum):
    NONE = "none"
    BRIDGE_DOWN = "bridge_down"
    VOICE_ERROR = "voice_error"
    VTS_DISCONNECT = "vts_disconnect"
    PLATFORM_SIGNAL_LOST = "platform_signal_lost"
    UNKNOWN = "unknown"


# ─── Interaction Tones ───────────────────────────────────────────────────────


class InteractionTone(str, enum.Enum):
    CASUAL = "casual"       # chat flowing, Nana can banter
    FOCUSED = "focused"     # reduced chatter, only reply when addressed
    MUTED = "muted"         # owner in flow, Nana stays quiet
    QUIET = "quiet"         # chat idle, Nana may prompt gently


# ─── Avatar Energy Levels ─────────────────────────────────────────────────────


class AvatarEnergy(str, enum.Enum):
    HIGH = "high"   # react expressively, body language active
    NORMAL = "normal"  # standard expression
    LOW = "low"     # subdued, relaxed posture
    DORMANT = "dormant"  # minimal to no expression


# ─── Viewer Expectation ───────────────────────────────────────────────────────


class ViewerExpectation(str, enum.Enum):
    NORMAL = "normal"   # standard chat rhythm
    HIGH = "high"       # many viewers, expect faster replies
    MUTED = "muted"     # few viewers, calm pace OK


# ─── Policy Dataclass ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StreamPolicy:
    """Immutable policy object emitted by get_policy()."""

    state: StreamState
    reason: str

    can_proactive: bool
    can_auto_send: bool
    can_use_private_memory: bool

    proactive_budget: int  # max proactive messages in 60s window

    interaction_tone: InteractionTone
    avatar_energy: AvatarEnergy
    viewer_expectation: ViewerExpectation

    # Error context (meaningful only when state == error_safe)
    error_type: ErrorType
    can_reply: bool
    can_speak: bool
    can_avatar: bool

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "reason": self.reason,
            "can_proactive": self.can_proactive,
            "can_auto_send": self.can_auto_send,
            "can_use_private_memory": self.can_use_private_memory,
            "proactive_budget": self.proactive_budget,
            "interaction_tone": self.interaction_tone.value,
            "avatar_energy": self.avatar_energy.value,
            "viewer_expectation": self.viewer_expectation.value,
            "error_type": self.error_type.value,
            "can_reply": self.can_reply,
            "can_speak": self.can_speak,
            "can_avatar": self.can_avatar,
        }


# ─── Runtime Signals ─────────────────────────────────────────────────────────


@dataclass
class StreamSignals:
    """Input signals for state machine transitions."""

    # Platform
    platform_live: bool = False
    platform_live_timeout: bool = False

    # Runtime health
    bridge_healthy: bool = True
    bridge_enabled: bool = False
    bridge_running: bool = False
    voice_ready: bool = True
    vts_connected: bool = True

    # Chat
    chat_velocity: float = 0.0  # messages per minute, 0 = silent
    viewer_count: int = 0

    # Owner context (maps to persona.focus via persona module)
    owner_in_flow: bool = False

    # Last event age (for timeout detection)
    last_event_age_s: float = 0.0

    # Manual override flags
    manual_command: Optional[str] = None  # e.g. "live-on", "break", "resume", "off"

    # Diagnostics for read-only signal refresh paths.
    signal_source: str = ""
    signal_errors: tuple[str, ...] = ()


# ─── State Machine ────────────────────────────────────────────────────────────


# Chat velocity thresholds (messages per minute)
_CHAT_VELOCITY_ACTIVE_THRESHOLD = 3.0   # above this -> live_active
_CHAT_VELOCITY_IDLE_THRESHOLD = 0.5    # below this -> live_idle

# Timeout thresholds (seconds)
_LIVE_IDLE_TIMEOUT_S = 300.0  # 5 min no events -> warn, don't auto-offline
_POST_STREAM_TIMEOUT_S = 600.0  # 10 min -> suggest going fully offline


def _coerce_stream_state(state: StreamState | str) -> StreamState | None:
    if isinstance(state, StreamState):
        return state
    try:
        return StreamState(str(state))
    except Exception:
        return None


def project_stream_lifecycle(state: StreamState | str, signals: StreamSignals | None = None) -> str:
    """Project raw STAGE-8A state into lifecycle metadata for status surfaces."""

    raw = _coerce_stream_state(state)
    if raw is None:
        return "unknown"
    if raw == StreamState.OFFLINE:
        if signals is not None and bool(getattr(signals, "bridge_running", False)):
            return "rehearsal_candidate"
        return "offline"
    if raw in {StreamState.LIVE_IDLE, StreamState.LIVE_ACTIVE}:
        return "live"
    if raw == StreamState.INTERMISSION:
        return "live_intermission"
    if raw == StreamState.POST_STREAM:
        return "ending"
    if raw == StreamState.ERROR_SAFE:
        return "error_safe"
    return "unknown"


def _policy_for_state(
    state: StreamState,
    signals: StreamSignals,
    reason: str,
    error_type: ErrorType,
) -> StreamPolicy:
    """Derive policy from state and signals."""

    # ── Default policy per state ──────────────────────────────────────────

    if state == StreamState.OFFLINE:
        return StreamPolicy(
            state=state,
            reason=reason,
            can_proactive=False,
            can_auto_send=False,
            can_use_private_memory=False,
            proactive_budget=0,
            interaction_tone=InteractionTone.MUTED,
            avatar_energy=AvatarEnergy.DORMANT,
            viewer_expectation=ViewerExpectation.MUTED,
            error_type=error_type,
            can_reply=False,
            can_speak=False,
            can_avatar=False,
        )

    if state == StreamState.LIVE_IDLE:
        # Chat quiet — Nana may prompt gently
        tone = InteractionTone.QUIET
        energy = AvatarEnergy.LOW
        expectation = ViewerExpectation.MUTED
        proactive = True
        budget = 1  # one gentle prompt per 60s max
        can_reply = True
        can_speak = signals.voice_ready
        can_avatar = signals.vts_connected
        return StreamPolicy(
            state=state,
            reason=reason,
            can_proactive=proactive,
            can_auto_send=False,  # always preview before send
            can_use_private_memory=False,  # keep private memory for owner
            proactive_budget=budget,
            interaction_tone=tone,
            avatar_energy=energy,
            viewer_expectation=expectation,
            error_type=error_type,
            can_reply=can_reply,
            can_speak=can_speak,
            can_avatar=can_avatar,
        )

    if state == StreamState.LIVE_ACTIVE:
        # Chat flowing — Nana replies, keeps pace
        tone = InteractionTone.CASUAL
        energy = AvatarEnergy.NORMAL
        expectation = ViewerExpectation.NORMAL if signals.viewer_count < 50 else ViewerExpectation.HIGH
        proactive = True
        budget = 3  # up to 3 proactive lines per 60s when chat is active
        can_reply = True
        can_speak = signals.voice_ready
        can_avatar = signals.vts_connected
        return StreamPolicy(
            state=state,
            reason=reason,
            can_proactive=proactive,
            can_auto_send=False,
            can_use_private_memory=False,
            proactive_budget=budget,
            interaction_tone=tone,
            avatar_energy=energy,
            viewer_expectation=expectation,
            error_type=error_type,
            can_reply=can_reply,
            can_speak=can_speak,
            can_avatar=can_avatar,
        )

    if state == StreamState.INTERMISSION:
        # Between sets — Nana keeps atmosphere light
        tone = InteractionTone.QUIET
        energy = AvatarEnergy.LOW
        expectation = ViewerExpectation.MUTED
        proactive = True
        budget = 1
        can_reply = True
        can_speak = signals.voice_ready
        can_avatar = signals.vts_connected
        return StreamPolicy(
            state=state,
            reason=reason,
            can_proactive=proactive,
            can_auto_send=False,
            can_use_private_memory=False,
            proactive_budget=budget,
            interaction_tone=tone,
            avatar_energy=energy,
            viewer_expectation=expectation,
            error_type=error_type,
            can_reply=can_reply,
            can_speak=can_speak,
            can_avatar=can_avatar,
        )

    if state == StreamState.POST_STREAM:
        # Stream just ended — wrap up, thank viewers
        tone = InteractionTone.FOCUSED
        energy = AvatarEnergy.LOW
        expectation = ViewerExpectation.MUTED
        proactive = False
        budget = 0
        can_reply = True
        can_speak = signals.voice_ready
        can_avatar = signals.vts_connected
        return StreamPolicy(
            state=state,
            reason=reason,
            can_proactive=proactive,
            can_auto_send=False,
            can_use_private_memory=False,
            proactive_budget=budget,
            interaction_tone=tone,
            avatar_energy=energy,
            viewer_expectation=expectation,
            error_type=error_type,
            can_reply=can_reply,
            can_speak=can_speak,
            can_avatar=can_avatar,
        )

    # error_safe: degraded mode — fail closed
    return _policy_for_error(error_type, signals, reason)


def _policy_for_error(
    error_type: ErrorType,
    signals: StreamSignals,
    reason: str,
) -> StreamPolicy:
    """Derive policy for error_safe state based on error type."""

    if error_type == ErrorType.BRIDGE_DOWN:
        # Cannot receive/send messages at all
        return StreamPolicy(
            state=StreamState.ERROR_SAFE,
            reason=reason,
            can_proactive=False,
            can_auto_send=False,
            can_use_private_memory=False,
            proactive_budget=0,
            interaction_tone=InteractionTone.MUTED,
            avatar_energy=AvatarEnergy.DORMANT,
            viewer_expectation=ViewerExpectation.MUTED,
            error_type=error_type,
            can_reply=False,  # no bridge = no public reply
            can_speak=False,
            can_avatar=False,
        )

    if error_type == ErrorType.VOICE_ERROR:
        # Text reply OK, voice disabled
        return StreamPolicy(
            state=StreamState.ERROR_SAFE,
            reason=reason,
            can_proactive=False,
            can_auto_send=False,
            can_use_private_memory=False,
            proactive_budget=0,
            interaction_tone=InteractionTone.QUIET,
            avatar_energy=AvatarEnergy.LOW,
            viewer_expectation=ViewerExpectation.MUTED,
            error_type=error_type,
            can_reply=True,
            can_speak=False,  # voice broken
            can_avatar=signals.vts_connected,  # avatar still OK if VTS works
        )

    if error_type == ErrorType.VTS_DISCONNECT:
        # Can still talk, avatar frozen
        return StreamPolicy(
            state=StreamState.ERROR_SAFE,
            reason=reason,
            can_proactive=False,
            can_auto_send=False,
            can_use_private_memory=False,
            proactive_budget=0,
            interaction_tone=InteractionTone.QUIET,
            avatar_energy=AvatarEnergy.DORMANT,  # no animation
            viewer_expectation=ViewerExpectation.MUTED,
            error_type=error_type,
            can_reply=True,
            can_speak=signals.voice_ready,
            can_avatar=False,  # VTS down
        )

    # Generic unknown error — fail safest
    return StreamPolicy(
        state=StreamState.ERROR_SAFE,
        reason=reason,
        can_proactive=False,
        can_auto_send=False,
        can_use_private_memory=False,
        proactive_budget=0,
        interaction_tone=InteractionTone.MUTED,
        avatar_energy=AvatarEnergy.DORMANT,
        viewer_expectation=ViewerExpectation.MUTED,
        error_type=error_type,
        can_reply=False,
        can_speak=False,
        can_avatar=False,
    )


# ─── Stream State Core ───────────────────────────────────────────────────────


class StreamStateCore:
    """Singleton stream state machine.

    Only emits policy — does NOT trigger actions.
    State transitions happen on signals and manual commands.
    """

    # Class-level singleton
    _instance: Optional["StreamStateCore"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = StreamState.OFFLINE
        self._error_type = ErrorType.NONE
        self._reason = "init"
        self._initialized_at = time.time()
        self._last_transition_at = time.time()

        # Session tracking
        self._session_start: Optional[float] = None
        self._stream_start: Optional[float] = None

        # Velocity smoothing (simple EMA)
        self._velocity_ema: float = 0.0
        self._last_velocity_update: float = 0.0

        # Post-stream tracking
        self._post_stream_at: Optional[float] = None

        # Error recovery history
        self._error_history: list[dict] = []

        # Transition log (for diagnostics)
        self._transition_log: list[dict] = []

        # Latest runtime health/chat signals. get_policy() reads this snapshot
        # so 8B/8C do not accidentally reason from default healthy signals.
        self._last_signals = StreamSignals()

    @classmethod
    def get_instance(cls) -> "StreamStateCore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── Public API ────────────────────────────────────────────────────────

    def get_policy(self) -> StreamPolicy:
        """Return current policy. Thread-safe. No side effects."""
        with self._lock:
            return self._current_policy_locked()

    def get_policy_with_signals(self, signals: StreamSignals) -> StreamPolicy:
        """Return current policy with provided signals. Thread-safe."""
        with self._lock:
            return _policy_for_state(self._state, signals, self._reason, self._error_type)

    def get_state(self) -> StreamState:
        """Return current state."""
        with self._lock:
            return self._state

    def get_error_type(self) -> ErrorType:
        """Return current error type."""
        with self._lock:
            return self._error_type

    def get_reason(self) -> str:
        """Return current reason string."""
        with self._lock:
            return self._reason

    # ── Signal Updates ───────────────────────────────────────────────────

    def update_signals(self, signals: StreamSignals) -> None:
        """Update runtime signals and evaluate automatic transitions.

        Called by the main loop periodically. Does not change state
        unless automatic transition conditions are met.
        """
        with self._lock:
            self._last_signals = replace(signals)
            self._update_velocity(signals.chat_velocity)
            self._evaluate_auto_transitions(signals)

    def refresh_signals(self, signals: StreamSignals) -> None:
        """Refresh the policy signal snapshot without state transitions.

        Status/readiness commands use this path to avoid reasoning from stale
        default signals while preserving STAGE-8A's transition behavior.
        """

        with self._lock:
            self._last_signals = replace(signals)
            self._update_velocity(signals.chat_velocity)

    def _update_velocity(self, raw_velocity: float) -> None:
        """Update EMA-smoothed chat velocity."""
        now = time.time()
        alpha = 0.3  # smoothing factor
        if self._last_velocity_update == 0:
            self._velocity_ema = raw_velocity
        else:
            self._velocity_ema = alpha * raw_velocity + (1 - alpha) * self._velocity_ema
        self._last_velocity_update = now

    def _evaluate_auto_transitions(self, signals: StreamSignals) -> None:
        """Evaluate automatic (non-manual) transitions."""
        current = self._state

        # ── Platform timeout ──────────────────────────────────────────────
        if signals.platform_live_timeout:
            self._transition_to(
                StreamState.OFFLINE,
                reason="platform_timeout",
                error_type=ErrorType.NONE,
            )
            return

        # ── Health failures ──────────────────────────────────────────────
        if current not in {StreamState.ERROR_SAFE, StreamState.OFFLINE}:
            error_type = self._detect_health_error(signals)
            if error_type != ErrorType.NONE:
                self._transition_to(
                    StreamState.ERROR_SAFE,
                    reason=f"health_check:{error_type.value}",
                    error_type=error_type,
                )
                return
            # No error — fall through to velocity/timeout checks

        # ── Chat velocity: live_idle <-> live_active ───────────────────
        if current in {StreamState.LIVE_IDLE, StreamState.LIVE_ACTIVE}:
            if self._velocity_ema >= _CHAT_VELOCITY_ACTIVE_THRESHOLD:
                self._transition_to(
                    StreamState.LIVE_ACTIVE,
                    reason="chat_velocity_rising",
                    error_type=ErrorType.NONE,
                )
            elif self._velocity_ema < _CHAT_VELOCITY_IDLE_THRESHOLD:
                self._transition_to(
                    StreamState.LIVE_IDLE,
                    reason="chat_velocity_dropping",
                    error_type=ErrorType.NONE,
                )

        # ── Post-stream timeout ─────────────────────────────────────────
        # Only check if state hasn't changed (use `current`, not self._state
        # to avoid re-checking after a transition in this same call)
        if current == StreamState.POST_STREAM:
            if self._post_stream_at and \
               time.time() - self._post_stream_at >= _POST_STREAM_TIMEOUT_S:
                self._transition_to(
                    StreamState.OFFLINE,
                    reason="post_stream_timeout",
                    error_type=ErrorType.NONE,
                )

    def _detect_health_error(self, signals: StreamSignals) -> ErrorType:
        """Detect which health subsystem failed."""
        if not signals.bridge_healthy:
            return ErrorType.BRIDGE_DOWN
        if not signals.voice_ready:
            return ErrorType.VOICE_ERROR
        if not signals.vts_connected:
            return ErrorType.VTS_DISCONNECT
        return ErrorType.NONE

    # ── Manual Commands ─────────────────────────────────────────────────

    def go_live(self) -> StreamPolicy:
        """Handle /stream-live-on. Transitions from offline to live_idle."""
        with self._lock:
            if self._state in {StreamState.OFFLINE, StreamState.POST_STREAM, StreamState.ERROR_SAFE}:
                self._session_start = self._session_start or time.time()
                self._stream_start = time.time()
                self._post_stream_at = None
                self._transition_to(
                    StreamState.LIVE_IDLE,
                    reason="manual_go_live",
                    error_type=ErrorType.NONE,
                )
            # Return current policy regardless
            return self._current_policy_locked()

    def end_stream(self) -> StreamPolicy:
        """Handle /stream-live-off. Transitions to post_stream then offline."""
        with self._lock:
            if self._state not in {StreamState.OFFLINE, StreamState.POST_STREAM}:
                self._transition_to(
                    StreamState.POST_STREAM,
                    reason="manual_end_stream",
                    error_type=ErrorType.NONE,
                )
                self._post_stream_at = time.time()
                self._stream_start = None
            return self._current_policy_locked()

    def go_intermission(self) -> StreamPolicy:
        """Handle /stream-break. Transitions live_* to intermission."""
        with self._lock:
            if self._state in {StreamState.LIVE_IDLE, StreamState.LIVE_ACTIVE}:
                self._transition_to(
                    StreamState.INTERMISSION,
                    reason="manual_intermission",
                    error_type=ErrorType.NONE,
                )
            return self._current_policy_locked()

    def resume_from_intermission(self) -> StreamPolicy:
        """Handle /stream-resume. Transitions intermission to live_idle."""
        with self._lock:
            if self._state == StreamState.INTERMISSION:
                self._transition_to(
                    StreamState.LIVE_IDLE,
                    reason="manual_resume",
                    error_type=ErrorType.NONE,
                )
            return self._current_policy_locked()

    def recover_from_error(self, target: StreamState = StreamState.OFFLINE) -> StreamPolicy:
        """Handle recovery from error_safe. Target defaults to offline."""
        with self._lock:
            if self._state == StreamState.ERROR_SAFE:
                self._transition_to(
                    target,
                    reason="manual_recovery",
                    error_type=ErrorType.NONE,
                )
            return self._current_policy_locked()

    def force_offline(self) -> StreamPolicy:
        """Force to offline regardless of current state."""
        with self._lock:
            self._transition_to(
                StreamState.OFFLINE,
                reason="manual_force_offline",
                error_type=ErrorType.NONE,
            )
            self._stream_start = None
            self._post_stream_at = None
            return self._current_policy_locked()

    # ── Internal ────────────────────────────────────────────────────────

    def _current_policy_locked(self) -> StreamPolicy:
        return _policy_for_state(
            self._state,
            self._last_signals,
            self._reason,
            self._error_type,
        )

    def _transition_to(
        self,
        new_state: StreamState,
        reason: str,
        error_type: ErrorType,
    ) -> None:
        """Record a state transition."""
        now = time.time()
        old_state = self._state
        if old_state == new_state and error_type == self._error_type:
            return  # No-op

        self._state = new_state
        self._reason = reason
        self._error_type = error_type
        self._last_transition_at = now

        # Log transition
        self._transition_log.append({
            "time": now,
            "from": old_state.value,
            "to": new_state.value,
            "reason": reason,
            "error_type": error_type.value,
        })
        # Keep last 100 transitions
        if len(self._transition_log) > 100:
            self._transition_log = self._transition_log[-100:]

        # Record error in history
        if new_state == StreamState.ERROR_SAFE:
            self._error_history.append({
                "time": now,
                "error_type": error_type.value,
                "reason": reason,
            })
            if len(self._error_history) > 50:
                self._error_history = self._error_history[-50:]

    # ── Status / Diagnostics ─────────────────────────────────────────────

    def status(self) -> dict:
        """Return full status snapshot."""
        with self._lock:
            uptime = time.time() - self._initialized_at
            stream_duration = time.time() - self._stream_start if self._stream_start else None
            policy = self._current_policy_locked()
            lifecycle = project_stream_lifecycle(self._state, self._last_signals)
            return {
                "state": self._state.value,
                "raw_state": self._state.value,
                "lifecycle": lifecycle,
                "error_type": self._error_type.value,
                "reason": self._reason,
                "uptime_s": uptime,
                "stream_start": self._stream_start,
                "stream_duration_s": stream_duration,
                "session_start": self._session_start,
                "last_transition_at": self._last_transition_at,
                "velocity_ema": round(self._velocity_ema, 2),
                "last_signals": {
                    "platform_live": self._last_signals.platform_live,
                    "platform_live_timeout": self._last_signals.platform_live_timeout,
                    "bridge_healthy": self._last_signals.bridge_healthy,
                    "bridge_enabled": self._last_signals.bridge_enabled,
                    "bridge_running": self._last_signals.bridge_running,
                    "voice_ready": self._last_signals.voice_ready,
                    "vts_connected": self._last_signals.vts_connected,
                    "chat_velocity": round(self._last_signals.chat_velocity, 2),
                    "viewer_count": self._last_signals.viewer_count,
                    "owner_in_flow": self._last_signals.owner_in_flow,
                    "last_event_age_s": self._last_signals.last_event_age_s,
                    "signal_source": self._last_signals.signal_source,
                    "signal_errors": list(self._last_signals.signal_errors),
                },
                "policy": policy.to_dict(),
                "post_stream_at": self._post_stream_at,
                "transition_log": list(self._transition_log[-10:]),
                "error_history": list(self._error_history[-5:]),
            }

    def status_lines(self) -> list[str]:
        """Return formatted status lines for CLI."""
        s = self.status()
        policy = dict(s.get("policy") or {})
        last_signals = dict(s.get("last_signals") or {})
        stream_dur = s["stream_duration_s"]
        stream_dur_text = f"{stream_dur:.0f}s" if stream_dur else "none"
        lines = [
            "🎬 Stream State Core (STAGE-8A)",
            f"  State: {s['state']} | lifecycle={s.get('lifecycle', 'unknown')}",
            f"  Reason: {s['reason']}",
            f"  Error type: {s['error_type']}",
            f"  Uptime: {s['uptime_s']:.0f}s",
            f"  Stream duration: {stream_dur_text}",
            f"  Velocity EMA: {s['velocity_ema']:.1f} msg/min",
            (
                "  Runtime signals: "
                f"bridge={last_signals.get('bridge_healthy')} | "
                f"bridge_running={last_signals.get('bridge_running')} | "
                f"voice={last_signals.get('voice_ready')} | "
                f"vts={last_signals.get('vts_connected')} | "
                f"viewers={last_signals.get('viewer_count')} | "
                f"velocity={last_signals.get('chat_velocity')}"
            ),
            (
                "  Policy: "
                f"proactive={policy.get('can_proactive')} | "
                f"auto_send={policy.get('can_auto_send')} | "
                f"private_memory={policy.get('can_use_private_memory')} | "
                f"budget={policy.get('proactive_budget')} | "
                f"tone={policy.get('interaction_tone')} | avatar={policy.get('avatar_energy')}"
            ),
            (
                "  Output gates: "
                f"reply={policy.get('can_reply')} | "
                f"speak={policy.get('can_speak')} | "
                f"avatar={policy.get('can_avatar')}"
            ),
            (
                f"  Thresholds: active>={_CHAT_VELOCITY_ACTIVE_THRESHOLD} "
                f"| idle<{_CHAT_VELOCITY_IDLE_THRESHOLD}"
            ),
        ]
        if s["transition_log"]:
            last = s["transition_log"][-1]
            lines.append(
                f"  Last transition: {last['from']} -> {last['to']} "
                f"| reason={last['reason']}"
            )
        if s["error_history"]:
            lines.append(f"  Recent errors: {len(s['error_history'])} in session")
        lines.append("  Commands: /stream-live-on | /stream-live-off | /stream-break | /stream-resume | /stream-status")
        lines.append("  Rule: stream policy is public-stage only; private owner memory stays outside this layer.")
        return lines


# ─── Module-level Singleton ─────────────────────────────────────────────────


_INSTANCE: Optional[StreamStateCore] = None
_INSTANCE_LOCK = threading.Lock()


def get_stream_state() -> StreamStateCore:
    """Return the singleton StreamStateCore instance."""
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = StreamStateCore()
    return _INSTANCE


def get_policy() -> StreamPolicy:
    """Convenience: get current policy."""
    return get_stream_state().get_policy()


def get_policy_with_signals(signals: StreamSignals) -> StreamPolicy:
    """Convenience: get current policy with signals."""
    return get_stream_state().get_policy_with_signals(signals)


def stream_state_status_lines() -> list[str]:
    """Convenience: get status lines."""
    return get_stream_state().status_lines()


# ─── Smoke Tests ─────────────────────────────────────────────────────────────


def _smoke_test_state_policies() -> bool:
    """Verify all states produce valid policies."""
    print("[8A Smoke] Testing state policies...")
    core = get_stream_state()

    # Save current state
    current = core.get_state()

    for state in StreamState:
        # Force to each state for testing
        with core._lock:
            core._state = state
            core._reason = f"smoke_{state.value}"
            core._error_type = ErrorType.NONE

        policy = core.get_policy()

        # Verify basic invariants
        assert policy.state == state, f"policy.state mismatch: {policy.state} != {state}"
        assert policy.can_proactive in {True, False}
        assert policy.can_auto_send in {True, False}
        assert isinstance(policy.proactive_budget, int)
        assert policy.proactive_budget >= 0

        # Offline and error_safe: can_proactive must be False
        if state in {StreamState.OFFLINE, StreamState.ERROR_SAFE}:
            assert not policy.can_proactive, f"{state.value} must have can_proactive=False"

        # Live states: can_reply must be True (unless error)
        if state in {StreamState.LIVE_IDLE, StreamState.LIVE_ACTIVE, StreamState.INTERMISSION}:
            assert policy.can_reply, f"{state.value} must have can_reply=True"

        print(f"  {state.value}: OK (proactive={policy.can_proactive}, budget={policy.proactive_budget})")

    # Restore
    with core._lock:
        core._state = current

    print("[8A Smoke] State policies: PASSED")
    return True


def _smoke_test_error_types() -> bool:
    """Verify error_safe with all error types."""
    print("[8A Smoke] Testing error types...")
    core = get_stream_state()

    current = core.get_state()

    with core._lock:
        core._state = StreamState.ERROR_SAFE
        core._reason = "smoke_error"

    for err in ErrorType:
        with core._lock:
            core._error_type = err

        policy = core.get_policy()
        assert policy.state == StreamState.ERROR_SAFE
        assert policy.error_type == err

        # Verify error-specific can_* flags
        if err == ErrorType.BRIDGE_DOWN:
            assert not policy.can_reply
            assert not policy.can_speak
            assert not policy.can_avatar
        elif err == ErrorType.VOICE_ERROR:
            assert policy.can_reply
            assert not policy.can_speak
        elif err == ErrorType.VTS_DISCONNECT:
            assert policy.can_reply
            assert not policy.can_avatar
        elif err == ErrorType.NONE:
            assert not policy.can_reply
            assert not policy.can_speak
            assert not policy.can_avatar

        print(f"  {err.value}: OK (reply={policy.can_reply}, speak={policy.can_speak}, avatar={policy.can_avatar})")

    # Restore
    with core._lock:
        core._state = current
        core._error_type = ErrorType.NONE

    print("[8A Smoke] Error types: PASSED")
    return True


def _smoke_test_manual_transitions() -> bool:
    """Verify manual command transitions."""
    print("[8A Smoke] Testing manual transitions...")
    core = get_stream_state()

    # Start from offline
    core.force_offline()
    assert core.get_state() == StreamState.OFFLINE

    # offline -> live
    core.go_live()
    assert core.get_state() == StreamState.LIVE_IDLE

    # live_idle -> live_active (via velocity)
    with core._lock:
        core._velocity_ema = 5.0
    signals = StreamSignals(chat_velocity=5.0)
    core.update_signals(signals)
    assert core.get_state() == StreamState.LIVE_ACTIVE

    # live_active -> intermission
    core.go_intermission()
    assert core.get_state() == StreamState.INTERMISSION

    # intermission -> live_idle
    core.resume_from_intermission()
    assert core.get_state() == StreamState.LIVE_IDLE

    # live_idle -> post_stream -> offline
    core.end_stream()
    assert core.get_state() == StreamState.POST_STREAM

    core.force_offline()
    assert core.get_state() == StreamState.OFFLINE

    print("[8A Smoke] Manual transitions: PASSED")
    return True


def _smoke_test_timeout() -> bool:
    """Verify timeout triggers post_stream -> offline."""
    print("[8A Smoke] Testing timeout...")
    # Use a FRESH instance to avoid singleton state leaking
    core = StreamStateCore()

    # Start from offline, go live first
    core.go_live()
    assert core.get_state() == StreamState.LIVE_IDLE, f"Expected LIVE_IDLE, got {core.get_state()}"

    # Then end stream -> post_stream
    core.end_stream()
    assert core.get_state() == StreamState.POST_STREAM, f"Expected POST_STREAM, got {core.get_state()}"

    # Manually set post_stream_at to the past
    with core._lock:
        core._post_stream_at = time.time() - _POST_STREAM_TIMEOUT_S - 1

    # update_signals should auto-transition to offline
    signals = StreamSignals()
    core.update_signals(signals)
    assert core.get_state() == StreamState.OFFLINE, f"Expected OFFLINE, got {core.get_state()}"

    print("[8A Smoke] Timeout: PASSED")
    return True


def _smoke_test_signal_persistence() -> bool:
    """Verify get_policy() uses the latest update_signals snapshot."""
    print("[8A Smoke] Testing signal persistence...")
    core = StreamStateCore()
    core.go_live()

    core.update_signals(StreamSignals(chat_velocity=0.0, voice_ready=False, vts_connected=True))
    policy = core.get_policy()

    assert policy.state == StreamState.ERROR_SAFE
    assert policy.error_type == ErrorType.VOICE_ERROR
    assert policy.can_reply
    assert not policy.can_speak
    assert policy.can_avatar
    assert not policy.can_use_private_memory

    print("[8A Smoke] Signal persistence: PASSED")
    return True


def _smoke_test_stream_policy_privacy() -> bool:
    """Verify stream/public policy never grants private owner memory."""
    print("[8A Smoke] Testing public memory boundary...")
    core = StreamStateCore()
    for state in StreamState:
        with core._lock:
            core._state = state
            core._reason = f"privacy_{state.value}"
            core._error_type = ErrorType.VOICE_ERROR if state == StreamState.ERROR_SAFE else ErrorType.NONE
        policy = core.get_policy()
        assert not policy.can_use_private_memory, f"{state.value} must not expose private memory"

    print("[8A Smoke] Public memory boundary: PASSED")
    return True


def run_smoke_tests() -> bool:
    """Run all smoke tests. Returns True if all pass."""
    print("=" * 60)
    print("STAGE-8A Stream State Core — Smoke Tests")
    print("=" * 60)

    # Use fresh instances for each test to avoid singleton pollution
    tests = [
        _smoke_test_state_policies,
        _smoke_test_error_types,
        _smoke_test_manual_transitions,
        _smoke_test_timeout,
        _smoke_test_signal_persistence,
        _smoke_test_stream_policy_privacy,
    ]

    passed = 0
    failed = 0

    for test in tests:
        # Clear singleton before each test (both module-level and class-level)
        global _INSTANCE
        _INSTANCE = None
        StreamStateCore._instance = None
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except AssertionError as e:
            print(f"  FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {e}")
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    import sys
    success = run_smoke_tests()
    sys.exit(0 if success else 1)
