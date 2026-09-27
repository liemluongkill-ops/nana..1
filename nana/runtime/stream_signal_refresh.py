"""Safe local signal refresh for STAGE-8A stream status surfaces.

This module only reads already-available runtime snapshots. It does not start
Nana, call TTS/VTS/OBS/Discord, send messages, or touch game input.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from nana.runtime.stream_state import (
    StreamSignals,
    get_stream_state,
    project_stream_lifecycle,
)


@dataclass(frozen=True)
class StreamSignalRefresh:
    """Result object for read-only stream signal refreshes."""

    signals: StreamSignals
    raw_state: str
    lifecycle: str
    errors: tuple[str, ...]
    applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["signals"] = asdict(self.signals)
        return data


def _bool(value: Any) -> bool:
    return bool(value)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _external_bridge_snapshot(errors: list[str]) -> dict[str, Any]:
    try:
        from nana.runtime.external_bridge import get_external_bridge_runtime

        return dict(get_external_bridge_runtime().snapshot())
    except Exception as exc:
        errors.append(f"external_bridge:{type(exc).__name__}")
        return {}


def _voice_snapshot(voice: Any, errors: list[str]) -> dict[str, Any]:
    try:
        from nana.core.status_voice import _voice_status_snapshot

        return dict(_voice_status_snapshot(voice))
    except Exception as exc:
        errors.append(f"voice:{type(exc).__name__}")
        return {}


def _vts_snapshot(errors: list[str]) -> dict[str, Any]:
    try:
        from nana.integrations.vts import get_vts_runtime, vts_snapshot

        return dict(vts_snapshot(get_vts_runtime()))
    except Exception as exc:
        errors.append(f"vts:{type(exc).__name__}")
        return {}


def _social_snapshot(errors: list[str]) -> dict[str, Any]:
    try:
        from nana.runtime.social_session import get_social_session

        return dict(get_social_session().snapshot())
    except Exception as exc:
        errors.append(f"social_session:{type(exc).__name__}")
        return {}


def _owner_flow(errors: list[str]) -> bool:
    try:
        from nana.runtime.context import context_snapshot

        return _bool(dict(context_snapshot()).get("in_flow"))
    except Exception as exc:
        errors.append(f"context:{type(exc).__name__}")
        return False


def build_stream_signals(*, voice: Any = None, reason: str = "status") -> StreamSignalRefresh:
    """Build StreamSignals from local runtime snapshots without applying them."""

    errors: list[str] = []
    bridge = _external_bridge_snapshot(errors)
    voice_snap = _voice_snapshot(voice, errors)
    vts = _vts_snapshot(errors)
    social = _social_snapshot(errors)

    bridge_enabled = _bool(bridge.get("enabled"))
    bridge_running = _bool(bridge.get("running"))
    bridge_last_error = str(bridge.get("last_error") or bridge.get("error") or "none")
    bridge_healthy = bridge_enabled and bridge_running and bridge_last_error == "none"

    velocity_per_minute = _safe_float(social.get("chat_velocity_per_second")) * 60.0
    viewer_count = _safe_int(social.get("viewer_count"))
    last_viewer = dict(social.get("last_viewer") or {})
    last_event_age = _safe_float(last_viewer.get("age_seconds"), 0.0)

    core = get_stream_state()
    raw_state = core.get_state().value
    signals = StreamSignals(
        platform_live=raw_state in {"live_idle", "live_active", "intermission"},
        platform_live_timeout=False,
        bridge_healthy=bridge_healthy,
        bridge_enabled=bridge_enabled,
        bridge_running=bridge_running,
        voice_ready=_bool(voice_snap.get("ready")),
        vts_connected=_bool(vts.get("ready")),
        chat_velocity=round(velocity_per_minute, 2),
        viewer_count=viewer_count,
        owner_in_flow=_owner_flow(errors),
        last_event_age_s=last_event_age,
        signal_source=reason,
        signal_errors=tuple(errors),
    )
    return StreamSignalRefresh(
        signals=signals,
        raw_state=raw_state,
        lifecycle=project_stream_lifecycle(raw_state, signals),
        errors=tuple(errors),
        applied=False,
    )


def refresh_stream_signals(*, voice: Any = None, reason: str = "status") -> StreamSignalRefresh:
    """Refresh get_stream_state() signal snapshot without automatic transitions."""

    built = build_stream_signals(voice=voice, reason=reason)
    get_stream_state().refresh_signals(built.signals)
    raw_state = get_stream_state().get_state().value
    return StreamSignalRefresh(
        signals=built.signals,
        raw_state=raw_state,
        lifecycle=project_stream_lifecycle(raw_state, built.signals),
        errors=built.errors,
        applied=True,
    )
