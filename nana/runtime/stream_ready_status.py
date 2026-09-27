"""STAGE-9W: read-only stream readiness rollup.

This module is a preflight board for stream mode.  It aggregates already
existing read-only snapshots and never starts live systems, writes files, calls
TTS/VTS/OBS/Discord, or touches game input.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from nana.core.format import shorten_line


PHASE = "STAGE-9W"


@dataclass(frozen=True)
class StreamReadyCheck:
    key: str
    label: str
    state: str
    scope: str
    detail: str
    action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _check_error(key: str, label: str, scope: str, exc: Exception, action: str) -> StreamReadyCheck:
    return StreamReadyCheck(
        key=key,
        label=label,
        state="error",
        scope=scope,
        detail=f"{type(exc).__name__}: {exc}",
        action=action,
    )


def _state_bad(state: str) -> bool:
    return state in {"blocked", "error"}


def _state_warn(state: str) -> bool:
    return state == "warn"


def _core_identity_check() -> StreamReadyCheck:
    try:
        from nana.runtime.core_self import get_core_self

        snap = get_core_self().snapshot()
        blocks = dict(snap.get("blocks") or {})
        stats = dict(snap.get("stats") or {})
        ready = bool(blocks) and all(bool(value) for value in blocks.values())
        failed = int(stats.get("failed", 0) or 0)
        state = "ready" if ready and failed == 0 else ("warn" if ready else "blocked")
        detail = (
            f"blocks={','.join(name for name, value in blocks.items() if value) or 'none'} | "
            f"failed_checks={failed} | root=companion-first"
        )
        return StreamReadyCheck(
            key="core_identity",
            label="Core identity",
            state=state,
            scope="core",
            detail=detail,
            action="/core-self-status",
        )
    except Exception as exc:
        return _check_error("core_identity", "Core identity", "core", exc, "/core-self-status")


def _public_voice_check() -> StreamReadyCheck:
    try:
        from nana.runtime.public_voice_style import get_public_voice_style

        snap = get_public_voice_style().snapshot()
        stats = dict(snap.get("stats") or {})
        failed = int(stats.get("failed", 0) or 0)
        soft = int(stats.get("service_soft_hits", 0) or 0)
        state = "ready" if failed == 0 else "warn"
        detail = (
            f"phase={snap.get('phase')} | checks={stats.get('checks', 0)} | "
            f"failed={failed} | soft_service={soft}"
        )
        return StreamReadyCheck(
            key="public_voice",
            label="Public voice",
            state=state,
            scope="core",
            detail=detail,
            action="/public-voice-status",
        )
    except Exception as exc:
        return _check_error("public_voice", "Public voice", "core", exc, "/public-voice-status")


def _public_memory_filter_check() -> StreamReadyCheck:
    try:
        from nana.runtime.public_memory_filter import get_public_memory_filter

        snap = get_public_memory_filter().snapshot()
        stats = dict(snap.get("stats") or {})
        state = "ready" if snap.get("read_only") and not snap.get("memory_write") else "blocked"
        detail = (
            f"mode={snap.get('mode')} | built={stats.get('built', 0)} | "
            f"excluded={stats.get('excluded', 0)} | memory_write={snap.get('memory_write')}"
        )
        return StreamReadyCheck(
            key="public_memory_filter",
            label="Public memory filter",
            state=state,
            scope="core",
            detail=detail,
            action="/public-memory-filter-status",
        )
    except Exception as exc:
        return _check_error(
            "public_memory_filter",
            "Public memory filter",
            "core",
            exc,
            "/public-memory-filter-status",
        )


def _public_avatar_reaction_check() -> StreamReadyCheck:
    try:
        from nana.runtime.public_avatar_reaction import get_public_avatar_reaction

        snap = get_public_avatar_reaction().snapshot()
        stats = dict(snap.get("stats") or {})
        last = dict(snap.get("last") or {})
        state = "ready" if snap.get("read_only") and not snap.get("can_act") and not snap.get("vts_call") else "blocked"
        detail = (
            f"mode={snap.get('mode')} | built={stats.get('built', 0)} | "
            f"last_intent={last.get('intent') or 'none'} | vts_call={snap.get('vts_call')}"
        )
        return StreamReadyCheck(
            key="public_avatar_reaction",
            label="Public avatar cue",
            state=state,
            scope="core",
            detail=detail,
            action="/public-avatar-status",
        )
    except Exception as exc:
        return _check_error(
            "public_avatar_reaction",
            "Public avatar cue",
            "core",
            exc,
            "/public-avatar-status",
        )


def _discord_bridge_check() -> StreamReadyCheck:
    try:
        from nana.runtime.external_bridge import get_external_bridge_runtime

        snap = get_external_bridge_runtime().snapshot()
        stats = dict(snap.get("stats") or {})
        enabled = bool(snap.get("enabled"))
        running = bool(snap.get("running"))
        last_error = str(snap.get("last_error") or "none")
        if enabled and running and last_error == "none":
            state = "ready"
        elif enabled and running:
            state = "warn"
        else:
            state = "blocked"
        detail = (
            f"enabled={enabled} | running={running} | pending={snap.get('pending')} | "
            f"processed={stats.get('processed', 0)} | reply_written={stats.get('reply_written', 0)} | "
            f"last_error={shorten_line(last_error, 70)}"
        )
        return StreamReadyCheck(
            key="discord_bridge",
            label="Discord bridge",
            state=state,
            scope="live",
            detail=detail,
            action="/discord-core-status",
        )
    except Exception as exc:
        return _check_error("discord_bridge", "Discord bridge", "live", exc, "/discord-core-status")


def _voice_check(voice: Any = None) -> StreamReadyCheck:
    try:
        from nana.core.status_voice import _voice_status_snapshot

        snap = _voice_status_snapshot(voice)
        runtime = dict(snap.get("runtime") or {})
        queue_size = runtime.get("queue_size")
        queue_max = runtime.get("queue_maxsize")
        queue_text = "n/a" if queue_size is None or queue_max is None else f"{queue_size}/{queue_max}"
        ready = bool(snap.get("ready"))
        state = "ready" if ready else ("warn" if snap.get("configured") else "blocked")
        if ready and queue_max and queue_size is not None and queue_size >= queue_max:
            state = "warn"
        detail = (
            f"provider={snap.get('provider')} | configured={snap.get('configured')} | "
            f"runtime={snap.get('runtime_available')} | queue={queue_text} | "
            f"chunking={snap.get('chunking_enabled')} | streaming={snap.get('streaming_enabled')}"
        )
        return StreamReadyCheck(
            key="voice",
            label="Voice",
            state=state,
            scope="live",
            detail=detail,
            action="/voice-status",
        )
    except Exception as exc:
        return _check_error("voice", "Voice", "live", exc, "/voice-status")


def _voice_delivery_check(voice: Any = None) -> StreamReadyCheck:
    try:
        from nana.runtime.voice_delivery import get_voice_delivery

        snap = get_voice_delivery().snapshot()
        stats = dict(snap.get("stats") or {})
        last = dict(snap.get("last_plan") or {})
        warnings = list(last.get("warnings") or [])
        state = "ready" if snap.get("read_only") and not snap.get("tts_call") else "blocked"
        if warnings:
            state = "warn"
        detail = (
            f"mode={snap.get('mode')} | built={stats.get('built', 0)} | "
            f"last_chunks={len(last.get('chunks') or [])} | "
            f"tail={last.get('tail_count', 0)} | warnings={','.join(warnings) or 'none'}"
        )
        return StreamReadyCheck(
            key="voice_delivery",
            label="Voice delivery",
            state=state,
            scope="live",
            detail=detail,
            action="/voice-delivery-status",
        )
    except Exception as exc:
        return _check_error("voice_delivery", "Voice delivery", "live", exc, "/voice-delivery-status")


def _subtitle_check() -> StreamReadyCheck:
    try:
        from nana.core.status_voice import _subtitle_status_snapshot

        snap = _subtitle_status_snapshot()
        ready = bool(snap.get("ready"))
        state = "ready" if ready else "blocked"
        age = snap.get("age_ms")
        age_text = "none" if age is None else f"{int(age)}ms"
        detail = (
            f"ready={ready} | exists={snap.get('exists')} | fresh={snap.get('fresh')} | "
            f"age={age_text} | path={shorten_line(snap.get('path'), 90)}"
        )
        return StreamReadyCheck(
            key="subtitle",
            label="Subtitle",
            state=state,
            scope="live",
            detail=detail,
            action="/subtitle-status",
        )
    except Exception as exc:
        return _check_error("subtitle", "Subtitle", "live", exc, "/subtitle-status")


def _vts_check() -> StreamReadyCheck:
    try:
        from nana.integrations.vts import get_vts_runtime, vts_snapshot
        from nana.runtime.avatar_vts_dispatch import get_avatar_vts_dispatch_gate

        vts = vts_snapshot(get_vts_runtime())
        gate = get_avatar_vts_dispatch_gate().snapshot()
        enabled = bool(gate.get("enabled"))
        if vts.get("ready") and enabled:
            state = "ready"
        elif vts.get("ready"):
            state = "warn"
        else:
            state = "warn"
        detail = (
            f"ready={vts.get('ready')} | connected={vts.get('connected')} | "
            f"auth={vts.get('auth_status_label')} | dispatch_enabled={enabled}"
        )
        return StreamReadyCheck(
            key="vts",
            label="VTS/avatar",
            state=state,
            scope="optional",
            detail=detail,
            action="/vts-status | /avatar-vts-status",
        )
    except Exception as exc:
        return _check_error("vts", "VTS/avatar", "optional", exc, "/vts-status")


def _stream_state_check() -> StreamReadyCheck:
    try:
        from nana.runtime.stream_state import get_stream_state

        snap = get_stream_state().status()
        policy = dict(snap.get("policy") or {})
        state_name = str(snap.get("state") or "unknown")
        lifecycle = str(snap.get("lifecycle") or "unknown")
        can_reply = bool(policy.get("can_reply"))
        can_speak = bool(policy.get("can_speak"))
        can_avatar = bool(policy.get("can_avatar"))
        if state_name == "offline":
            state = "blocked"
        elif can_reply or can_speak or can_avatar:
            state = "ready"
        else:
            state = "warn"
        detail = (
            f"state={state_name} | lifecycle={lifecycle} | reply={can_reply} | speak={can_speak} | "
            f"avatar={can_avatar} | proactive={policy.get('can_proactive')} | "
            f"tone={policy.get('interaction_tone')}"
        )
        return StreamReadyCheck(
            key="stream_state",
            label="Stream state",
            state=state,
            scope="live",
            detail=detail,
            action="/stream-status | /stream-live-on",
        )
    except Exception as exc:
        return _check_error("stream_state", "Stream state", "live", exc, "/stream-status")


def stream_ready_snapshot(voice: Any = None) -> dict[str, Any]:
    try:
        from nana.runtime.stream_signal_refresh import refresh_stream_signals

        signal_refresh = refresh_stream_signals(voice=voice, reason="stream_ready_status")
    except Exception as exc:
        signal_refresh = {
            "applied": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    try:
        from nana.runtime.stream_event_timeline import stream_event_timeline_snapshot

        timeline = stream_event_timeline_snapshot(limit=5)
    except Exception as exc:
        timeline = {
            "count": 0,
            "last_event": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    checks = [
        _core_identity_check(),
        _public_voice_check(),
        _public_memory_filter_check(),
        _public_avatar_reaction_check(),
        _discord_bridge_check(),
        _voice_check(voice),
        _voice_delivery_check(voice),
        _subtitle_check(),
        _vts_check(),
        _stream_state_check(),
    ]
    core_blockers = [
        check for check in checks
        if check.scope == "core" and _state_bad(check.state)
    ]
    live_blockers = [
        check for check in checks
        if check.scope in {"core", "live"} and _state_bad(check.state)
    ]
    warnings = [check for check in checks if _state_warn(check.state)]
    return {
        "phase": PHASE,
        "mode": "read_only_stream_preflight",
        "read_only": True,
        "can_act": False,
        "api_call": False,
        "memory_write": False,
        "tts_call": False,
        "vts_call": False,
        "obs_call": False,
        "discord_call": False,
        "game_input": False,
        "rehearsal_ready": not core_blockers,
        "live_ready": not live_blockers,
        "core_blockers": [check.key for check in core_blockers],
        "live_blockers": [check.key for check in live_blockers],
        "warnings": [check.key for check in warnings],
        "checks": [check.to_dict() for check in checks],
        "signal_refresh": (
            signal_refresh.to_dict()
            if hasattr(signal_refresh, "to_dict")
            else dict(signal_refresh)
        ),
        "timeline": dict(timeline),
    }


def _format_check(check: dict[str, Any]) -> str:
    state = check.get("state") or "unknown"
    return (
        f"  - {check.get('label')}: {state} | scope={check.get('scope')} | "
        f"{check.get('detail')} | next={check.get('action')}"
    )


def stream_ready_status_lines(voice: Any = None) -> list[str]:
    snap = stream_ready_snapshot(voice)
    checks = list(snap.get("checks") or [])
    warnings = ", ".join(snap.get("warnings") or []) or "none"
    core_blockers = ", ".join(snap.get("core_blockers") or []) or "none"
    live_blockers = ", ".join(snap.get("live_blockers") or []) or "none"
    timeline = dict(snap.get("timeline") or {})
    last_timeline = dict(timeline.get("last_event") or {})
    last_timeline_text = (
        f"{last_timeline.get('kind')}:{last_timeline.get('age_seconds', 0.0):.1f}s"
        if last_timeline
        else "none"
    )
    lines = [
        f"Stream Ready Status ({PHASE})",
        "  Mode: read_only=True | can_act=False | api_call=False | memory_write=False",
        (
            "  Overall: "
            f"rehearsal_ready={snap.get('rehearsal_ready')} | "
            f"live_ready={snap.get('live_ready')} | warnings={warnings}"
        ),
        f"  Blockers: core={core_blockers} | live={live_blockers}",
        (
            "  Stream timeline: "
            f"events={timeline.get('count', 0)} | last={last_timeline_text} | "
            "command=/stream-event-log"
        ),
        "  Checks:",
    ]
    lines.extend(_format_check(check) for check in checks)
    lines.extend(
        [
            "  Commands: /stream-ready-status | /stage-status | /stream-status | /discord-core-status | /voice-status | /voice-delivery-status | /subtitle-status | /vts-status",
            "  Safety: status only; no TTS/VTS/OBS/Discord/API/game input and no memory write.",
        ]
    )
    return lines
