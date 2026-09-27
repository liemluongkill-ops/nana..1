"""
osu! pre-input commit barrier - Phase 38.

Builds a read-only operator review dossier from existing readiness and
executor preview layers. This module does not define an execution backend and
always forces dispatch/input flags to false.
"""

from __future__ import annotations

import copy
import os
import time
from typing import Any, Callable

from nana.game.osu.executor_dispatch_preflight import build_executor_dispatch_preflight_payload
from nana.game.osu.executor_preflight import REAL_INPUT_EXECUTOR_ENV, REAL_INPUT_EXECUTOR_TOKEN
from nana.game.osu.executor_shadow_dispatch_session import build_executor_shadow_dispatch_session_payload
from nana.game.osu.executor_sim_tick_loop import MAX_SAMPLES
from nana.game.osu.input_benchmark import build_input_benchmark_preview
from nana.game.osu.input_readiness import build_input_readiness_status
from nana.game.osu.input_safety import build_input_safety_status
from nana.runtime.stream_presence_readiness import build_stream_presence_readiness_preview


PRE_INPUT_COMMIT_SCHEMA = "nana.osu.pre_input_commit_barrier.v1"
DEFAULT_PRE_INPUT_COMMIT_SNAPSHOT_TTL_MS = 120000
MIN_PRE_INPUT_COMMIT_SNAPSHOT_TTL_MS = 15000
MAX_PRE_INPUT_COMMIT_SNAPSHOT_TTL_MS = 300000

DEFAULT_SHADOW_FLAGS = {
    "attempts": 3,
    "interval-ms": 80,
    "samples": 50,
    "early-window-ms": 800,
    "late-window-ms": 800,
    "lease-ttl-ms": 6000,
    "dispatch-early-window-ms": 800,
    "dispatch-late-window-ms": 800,
}

READINESS_FLAGS = {
    "adapter-enabled",
    "zone-enabled",
    "osu-zone-enabled",
    "stale-ms",
    "lead-ms",
    "limit",
}

BENCHMARK_FLAGS = {
    "adapter-enabled",
    "zone-enabled",
    "osu-zone-enabled",
    "stale-ms",
    "lead-ms",
    "limit",
}

HARD_FALSE_FIELDS = (
    "dispatch",
    "armed",
    "executable",
    "real_input_allowed",
    "submit_allowed",
    "real_input",
    "submit",
    "gameplay_input",
    "key_down_sent",
    "key_up_sent",
    "cursor_move",
    "click",
    "tap",
    "vts_call",
    "voice_call",
    "obs_call",
    "vision_call",
)

PayloadBuilder = Callable[..., dict[str, Any]]

_LAST_READY_SNAPSHOT: dict[str, Any] | None = None


def _base_flags() -> dict[str, bool]:
    return {field: False for field in HARD_FALSE_FIELDS}


def clear_pre_input_commit_snapshot() -> None:
    global _LAST_READY_SNAPSHOT
    _LAST_READY_SNAPSHOT = None


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _tokens(text: str) -> list[str]:
    values = str(text or "").split()
    if values and values[0].startswith("/"):
        return values[1:]
    return values


def _parse_operator_token(text: str) -> str | None:
    for token in _tokens(text):
        if token.startswith("--operator-approval-token="):
            return token.split("=", 1)[1].strip()
    return None


def _flag_names(text: str) -> set[str]:
    names: set[str] = set()
    for token in _tokens(text):
        if token.startswith("--") and "=" in token:
            names.add(token[2:].split("=", 1)[0])
    return names


def _text_with_defaults(text: str, command: str, defaults: dict[str, Any]) -> str:
    args = list(_tokens(text))
    present = _flag_names(text)
    parts = [command, *args]
    for name, value in defaults.items():
        if name not in present:
            parts.append(f"--{name}={value}")
    return " ".join(parts)


def _filtered_text(text: str, command: str, allowed_flags: set[str]) -> str:
    parts = [command]
    for token in _tokens(text):
        if not token.startswith("--") or "=" not in token:
            continue
        name = token[2:].split("=", 1)[0]
        if name in allowed_flags:
            parts.append(token)
    return " ".join(parts)


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if value))


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_snapshot_ttl_ms(text: str) -> int:
    ttl_ms = DEFAULT_PRE_INPUT_COMMIT_SNAPSHOT_TTL_MS
    for token in _tokens(text):
        if token.startswith("--snapshot-ttl-ms="):
            ttl_ms = _to_int(token.split("=", 1)[1], DEFAULT_PRE_INPUT_COMMIT_SNAPSHOT_TTL_MS)
            break
    return max(
        MIN_PRE_INPUT_COMMIT_SNAPSHOT_TTL_MS,
        min(MAX_PRE_INPUT_COMMIT_SNAPSHOT_TTL_MS, ttl_ms),
    )


def _snapshot_fields(
    *,
    source: str,
    created_at: float | None = None,
    expires_at: float | None = None,
    now: float | None = None,
    from_ready_review: bool = False,
    ttl_ms: int = DEFAULT_PRE_INPUT_COMMIT_SNAPSHOT_TTL_MS,
) -> dict[str, Any]:
    current = time.time() if now is None else float(now)
    remaining_ms = 0
    bounded_ttl_ms = max(
        MIN_PRE_INPUT_COMMIT_SNAPSHOT_TTL_MS,
        min(MAX_PRE_INPUT_COMMIT_SNAPSHOT_TTL_MS, int(ttl_ms)),
    )
    if expires_at is not None:
        remaining_ms = max(0, int(round((float(expires_at) - current) * 1000)))
    return {
        "snapshot_source": source,
        "snapshot_created_at": created_at,
        "snapshot_expires_at": expires_at,
        "snapshot_remaining_ms": remaining_ms,
        "snapshot_from_ready_review": bool(from_ready_review),
        "snapshot_ttl_ms": bounded_ttl_ms,
    }


def _scrub_snapshot_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace(REAL_INPUT_EXECUTOR_TOKEN, "[redacted]")
    if isinstance(value, dict):
        scrubbed: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in {"operator_approval_token", "raw_operator_token", "token", "executor_text"}:
                scrubbed[key] = "[redacted]"
            else:
                scrubbed[key] = _scrub_snapshot_value(item)
        return scrubbed
    if isinstance(value, list):
        return [_scrub_snapshot_value(item) for item in value]
    return value


def _remember_ready_snapshot(payload: dict[str, Any], *, now: float | None = None, ttl_ms: int | None = None) -> dict[str, Any]:
    global _LAST_READY_SNAPSHOT
    current = time.time() if now is None else float(now)
    ttl = ttl_ms if ttl_ms is not None else DEFAULT_PRE_INPUT_COMMIT_SNAPSHOT_TTL_MS
    expires_at = current + (int(ttl) / 1000.0)
    payload.update(
        _snapshot_fields(
            source="live_ready_review",
            created_at=current,
            expires_at=expires_at,
            now=current,
            from_ready_review=True,
            ttl_ms=int(ttl),
        )
    )
    snapshot = copy.deepcopy(_scrub_snapshot_value(payload))
    snapshot.update(_base_flags())
    _LAST_READY_SNAPSHOT = snapshot
    return payload


def _last_snapshot_state(*, now: float | None = None) -> tuple[dict[str, Any] | None, bool, dict[str, Any]]:
    current = time.time() if now is None else float(now)
    if _LAST_READY_SNAPSHOT is None:
        return None, False, _snapshot_fields(source="no_snapshot", now=current)
    snapshot = copy.deepcopy(_LAST_READY_SNAPSHOT)
    expires_at = snapshot.get("snapshot_expires_at")
    try:
        expired = expires_at is None or current > float(expires_at)
    except (TypeError, ValueError):
        expired = True
    metadata = _snapshot_fields(
        source="ready_review_snapshot_expired" if expired else "ready_review_snapshot",
        created_at=snapshot.get("snapshot_created_at"),
        expires_at=snapshot.get("snapshot_expires_at"),
        now=current,
        from_ready_review=bool(snapshot.get("snapshot_from_ready_review")),
        ttl_ms=_to_int(snapshot.get("snapshot_ttl_ms"), DEFAULT_PRE_INPUT_COMMIT_SNAPSHOT_TTL_MS),
    )
    if expired:
        return None, True, metadata
    snapshot.update(metadata)
    snapshot.update(_base_flags())
    return snapshot, False, metadata


def _merge_snapshot_expired(payload: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    blockers = _dedupe([*list(payload.get("blockers") or []), "snapshot_expired"])
    payload.update(metadata)
    payload.update(
        {
            "snapshot_source": "ready_review_snapshot_expired",
            "blockers": blockers,
        }
    )
    if payload.get("ready_for_operator_review") is not True:
        payload["reason"] = "snapshot_expired" if payload.get("reason") == "operator_gate_not_ready" else payload.get("reason")
    payload.update(_base_flags())
    return payload


def _empty_dispatch_payload(reason: str) -> dict[str, Any]:
    return {
        "schema": "nana.osu.executor_dispatch_preflight.v1",
        "version": 1,
        "decision": "executor_dispatch_preflight_hold_no_input",
        "dispatch_ready": False,
        "packet_valid": False,
        "packet_ready": False,
        "packet_id": None,
        "packet_action": None,
        "packet_action_type": None,
        "packet_source": "unavailable",
        "samples_max": MAX_SAMPLES,
        "time_to_action_ms": None,
        "timing_consistent": False,
        "timing_consistency_error_ms": None,
        "selected_metadata_source": None,
        "selected_metadata_complete": False,
        "blockers": [reason],
        "reason": reason,
        **_base_flags(),
    }


def _empty_shadow_payload(reason: str) -> dict[str, Any]:
    return {
        "schema": "nana.osu.executor_shadow_dispatch_session.v1",
        "version": 1,
        "decision": "executor_shadow_dispatch_session_hold_no_input",
        "session_result": "session_not_ready",
        "reason": reason,
        "attempts_requested": 0,
        "attempts_attempted": 0,
        "attempts_ok": 0,
        "attempts_max": 10,
        "interval_ms": 0,
        "interval_max_ms": 500,
        "samples_max": MAX_SAMPLES,
        "dispatch_ready_count": 0,
        "packet_valid_count": 0,
        "packet_ready_count": 0,
        "timing_consistent_count": 0,
        "metadata_complete_count": 0,
        "stale_lease_ignored_count": 0,
        "unique_action_count": 0,
        "first_dispatch_ready_attempt_index": None,
        "first_packet_id": None,
        "first_packet_action": None,
        "first_time_to_action_ms": None,
        "min_time_to_action_ms": None,
        "max_time_to_action_ms": None,
        "closest_time_to_action_ms": None,
        "attempts": [],
        "blockers": [reason],
        **_base_flags(),
    }


def _component_error(name: str, exc: Exception) -> dict[str, Any]:
    reason = f"{name}_error:{type(exc).__name__}"
    return {
        "decision": f"{name}_error_no_input",
        "readiness": "error",
        "overall_ready": False,
        "blockers": [reason],
        "reason": reason,
        **_base_flags(),
    }


def _degrade_reasons(stream_payload: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if stream_payload.get("overall_ready") is not True:
        reasons.append("stream_presence_not_overall_ready")
    for field in (
        "vts_degrade_mode",
        "voice_degrade_mode",
        "dialogue_degrade_mode",
        "subtitle_degrade_mode",
    ):
        value = stream_payload.get(field)
        if value and value != "normal":
            reasons.append(f"{field}:{value}")
    return _dedupe(reasons)


def _safe_input_readiness(text: str, builder: PayloadBuilder | None) -> dict[str, Any]:
    try:
        return (builder or build_input_readiness_status)(_filtered_text(text, "/osu-input-readiness-status", READINESS_FLAGS))
    except Exception as exc:
        return _component_error("input_readiness", exc)


def _safe_input_benchmark(text: str, builder: PayloadBuilder | None) -> dict[str, Any]:
    try:
        return (builder or build_input_benchmark_preview)(_filtered_text(text, "/osu-input-benchmark-preview", BENCHMARK_FLAGS))
    except Exception as exc:
        return _component_error("input_benchmark", exc)


def _safe_input_safety(builder: PayloadBuilder | None) -> dict[str, Any]:
    try:
        return (builder or build_input_safety_status)()
    except Exception as exc:
        payload = _component_error("input_safety", exc)
        payload.update(
            {
                "focus_readiness": "error",
                "emergency_abort_available": "error",
                "emergency_release_available": "error",
            }
        )
        return payload


def _safe_stream_presence(builder: PayloadBuilder | None) -> tuple[dict[str, Any], bool]:
    try:
        payload = (builder or build_stream_presence_readiness_preview)(None, include_executor=False)
        return payload, True
    except Exception as exc:
        payload = _component_error("stream_presence", exc)
        payload.update({"overall_ready": False, "stream_safe_mode": "degraded"})
        return payload, False


def _safe_dispatch_preflight(text: str, builder: PayloadBuilder | None) -> dict[str, Any]:
    try:
        return (builder or build_executor_dispatch_preflight_payload)(_text_with_defaults(
            text,
            "/osu-executor-dispatch-preflight-preview",
            DEFAULT_SHADOW_FLAGS,
        ))
    except Exception as exc:
        return _empty_dispatch_payload(f"dispatch_preflight_error:{type(exc).__name__}")


def _safe_shadow_session(text: str, builder: PayloadBuilder | None) -> dict[str, Any]:
    try:
        return (builder or build_executor_shadow_dispatch_session_payload)(_text_with_defaults(
            text,
            "/osu-executor-shadow-dispatch-session-preview",
            DEFAULT_SHADOW_FLAGS,
        ))
    except Exception as exc:
        return _empty_shadow_payload(f"shadow_session_error:{type(exc).__name__}")


def _gate_status(text: str) -> dict[str, Any]:
    raw_env = os.environ.get(REAL_INPUT_EXECUTOR_ENV)
    token = _parse_operator_token(text)
    return {
        "operator_token_present": bool(token),
        "operator_token_valid": token == REAL_INPUT_EXECUTOR_TOKEN,
        "env_gate_name": REAL_INPUT_EXECUTOR_ENV,
        "env_gate_present": raw_env is not None and str(raw_env).strip() != "",
        "env_gate_enabled": _is_truthy(raw_env),
    }


def _first_value(primary: Any, secondary: Any) -> Any:
    return primary if primary is not None else secondary


def build_pre_input_commit_barrier_payload(
    text: str = "",
    *,
    input_readiness_builder: PayloadBuilder | None = None,
    input_benchmark_builder: PayloadBuilder | None = None,
    input_safety_builder: PayloadBuilder | None = None,
    stream_presence_builder: PayloadBuilder | None = None,
    dispatch_preflight_builder: PayloadBuilder | None = None,
    shadow_session_builder: PayloadBuilder | None = None,
) -> dict[str, Any]:
    now = time.time()
    snapshot_ttl_ms = _parse_snapshot_ttl_ms(text)
    gate = _gate_status(text)
    input_readiness = _safe_input_readiness(text, input_readiness_builder)
    benchmark = _safe_input_benchmark(text, input_benchmark_builder)
    safety = _safe_input_safety(input_safety_builder)
    stream_presence, stream_checked = _safe_stream_presence(stream_presence_builder)

    gate_ready = bool(gate["env_gate_enabled"] and gate["operator_token_valid"])
    if gate_ready:
        dispatch_preflight = _safe_dispatch_preflight(text, dispatch_preflight_builder)
        shadow_session = _safe_shadow_session(text, shadow_session_builder)
    else:
        dispatch_preflight = _empty_dispatch_payload("operator_gate_not_ready")
        shadow_session = _empty_shadow_payload("operator_gate_not_ready")

    input_readiness_ready = input_readiness.get("readiness") == "ready"
    benchmark_ready = (
        benchmark.get("readiness") == "ready"
        and benchmark.get("recommended_benchmark") == "ready_for_future_input_benchmark"
    )
    focus_readiness = safety.get("focus_readiness") or input_readiness.get("focus_readiness") or "unavailable_no_helper"
    emergency_abort_available = (
        safety.get("emergency_abort_available")
        or input_readiness.get("emergency_abort_available")
        or "unavailable_no_helper"
    )
    emergency_release_available = (
        safety.get("emergency_release_available")
        or input_readiness.get("emergency_release_available")
        or "unavailable_no_helper"
    )
    stream_overall_ready = bool(stream_presence.get("overall_ready"))
    stream_safe_mode = stream_presence.get("stream_safe_mode") or "degraded"
    stream_degrade_reasons = _degrade_reasons(stream_presence)
    dispatch_preflight_ready = bool(dispatch_preflight.get("dispatch_ready"))
    shadow_dispatch_ready_count = _to_int(shadow_session.get("dispatch_ready_count"))
    shadow_packet_ready_count = _to_int(shadow_session.get("packet_ready_count"))
    shadow_metadata_complete_count = _to_int(shadow_session.get("metadata_complete_count"))
    shadow_timing_consistent_count = _to_int(shadow_session.get("timing_consistent_count"))

    blockers: list[str] = []
    if not gate["env_gate_enabled"]:
        blockers.append("missing_or_disabled_env_gate")
    if not gate["operator_token_valid"]:
        blockers.append("missing_or_invalid_operator_token")
    if not input_readiness_ready:
        blockers.append("input_readiness_not_ready")
    if not benchmark_ready:
        blockers.append("benchmark_not_ready")
    if focus_readiness != "ready":
        blockers.append(f"focus_not_ready:{focus_readiness}")
    if emergency_abort_available != "ready":
        blockers.append(f"emergency_abort_not_ready:{emergency_abort_available}")
    if emergency_release_available != "ready":
        blockers.append(f"emergency_release_not_ready:{emergency_release_available}")
    if not stream_checked:
        blockers.append("stream_presence_check_error")
    if not stream_overall_ready:
        blockers.append("stream_presence_not_ready")
    if not (dispatch_preflight_ready or shadow_dispatch_ready_count >= 1):
        blockers.append("dispatch_or_shadow_not_ready")
    if shadow_dispatch_ready_count < 1:
        blockers.append("shadow_dispatch_ready_count_zero")
    if shadow_packet_ready_count < 1:
        blockers.append("shadow_packet_ready_count_zero")
    if shadow_metadata_complete_count < 1:
        blockers.append("shadow_metadata_complete_count_zero")
    if shadow_timing_consistent_count < 1:
        blockers.append("shadow_timing_consistent_count_zero")

    for blocker in input_readiness.get("blockers") or []:
        blockers.append(f"input_readiness:{blocker}")
    for blocker in benchmark.get("blockers") or []:
        blockers.append(f"benchmark:{blocker}")
    if not dispatch_preflight_ready and shadow_dispatch_ready_count < 1:
        for blocker in dispatch_preflight.get("blockers") or []:
            if blocker != "operator_gate_not_ready":
                blockers.append(f"dispatch_preflight:{blocker}")
    if (
        shadow_dispatch_ready_count < 1
        or shadow_packet_ready_count < 1
        or shadow_metadata_complete_count < 1
        or shadow_timing_consistent_count < 1
    ):
        for blocker in shadow_session.get("blockers") or []:
            if blocker != "operator_gate_not_ready":
                blockers.append(f"shadow_session:{blocker}")

    blockers = _dedupe(blockers)
    ready_for_operator_review = not blockers
    if ready_for_operator_review:
        decision = "pre_input_commit_ready_for_operator_review_no_input"
        reason = "ready_for_operator_review"
    elif not gate_ready:
        decision = "pre_input_commit_hold_no_input"
        reason = "operator_gate_not_ready"
    else:
        decision = "pre_input_commit_blocked_no_input"
        reason = blockers[0] if blockers else "blocked"

    payload = {
        "schema": PRE_INPUT_COMMIT_SCHEMA,
        "version": 1,
        "decision": decision,
        "reason": reason,
        **gate,
        "input_readiness_ready": bool(input_readiness_ready),
        "input_readiness_decision": input_readiness.get("decision"),
        "input_readiness_blockers": list(input_readiness.get("blockers") or []),
        "benchmark_ready": bool(benchmark_ready),
        "benchmark_decision": benchmark.get("decision"),
        "benchmark_recommended": benchmark.get("recommended_benchmark"),
        "benchmark_blockers": list(benchmark.get("blockers") or []),
        "focus_readiness": focus_readiness,
        "window_title": safety.get("window_title") or input_readiness.get("window_title"),
        "window_class": safety.get("window_class") or input_readiness.get("window_class"),
        "process_name": safety.get("process_name") or input_readiness.get("process_name"),
        "emergency_abort_available": emergency_abort_available,
        "emergency_abort_strategy": safety.get("abort_strategy") or input_readiness.get("abort_strategy") or "preview_only",
        "emergency_release_available": emergency_release_available,
        "emergency_release_strategy": (
            safety.get("release_strategy") or input_readiness.get("release_strategy") or "preview_only"
        ),
        "stream_presence_checked": bool(stream_checked),
        "stream_presence_decision": stream_presence.get("decision"),
        "stream_presence_overall_ready": stream_overall_ready,
        "stream_safe_mode": stream_safe_mode,
        "stream_presence_degrade_reasons": stream_degrade_reasons,
        "dispatch_preflight_ready": dispatch_preflight_ready,
        "dispatch_preflight_decision": dispatch_preflight.get("decision"),
        "dispatch_packet_valid": bool(dispatch_preflight.get("packet_valid")),
        "dispatch_packet_ready": bool(dispatch_preflight.get("packet_ready")),
        "dispatch_packet_source": dispatch_preflight.get("packet_source"),
        "dispatch_selected_metadata_source": dispatch_preflight.get("selected_metadata_source"),
        "dispatch_selected_metadata_complete": bool(dispatch_preflight.get("selected_metadata_complete")),
        "dispatch_timing_consistent": dispatch_preflight.get("timing_consistent") is True,
        "dispatch_timing_consistency_error_ms": dispatch_preflight.get("timing_consistency_error_ms"),
        "shadow_session_result": shadow_session.get("session_result"),
        "shadow_session_decision": shadow_session.get("decision"),
        "shadow_dispatch_ready_count": shadow_dispatch_ready_count,
        "shadow_packet_valid_count": _to_int(shadow_session.get("packet_valid_count")),
        "shadow_packet_ready_count": shadow_packet_ready_count,
        "shadow_metadata_complete_count": shadow_metadata_complete_count,
        "shadow_timing_consistent_count": shadow_timing_consistent_count,
        "shadow_stale_lease_ignored_count": _to_int(shadow_session.get("stale_lease_ignored_count")),
        "shadow_unique_action_count": _to_int(shadow_session.get("unique_action_count")),
        "shadow_first_dispatch_ready_attempt_index": shadow_session.get("first_dispatch_ready_attempt_index"),
        "first_packet_id": _first_value(shadow_session.get("first_packet_id"), dispatch_preflight.get("packet_id")),
        "first_packet_action": _first_value(shadow_session.get("first_packet_action"), dispatch_preflight.get("packet_action")),
        "first_time_to_action_ms": _first_value(
            shadow_session.get("first_time_to_action_ms"),
            dispatch_preflight.get("time_to_action_ms"),
        ),
        "samples_max": _to_int(shadow_session.get("samples_max"), MAX_SAMPLES),
        "attempts_max": _to_int(shadow_session.get("attempts_max"), 10),
        "interval_max_ms": _to_int(shadow_session.get("interval_max_ms"), 500),
        "blockers": blockers,
        "warnings": stream_degrade_reasons if stream_overall_ready else [],
        "ready_for_operator_review": ready_for_operator_review,
        "created_at": now,
        **_snapshot_fields(source="live_review", now=now, ttl_ms=snapshot_ttl_ms),
        **_base_flags(),
    }
    payload.update(_base_flags())
    if ready_for_operator_review:
        _remember_ready_snapshot(payload, now=now, ttl_ms=snapshot_ttl_ms)
    return payload


def build_pre_input_commit_json_payload(text: str = "", **kwargs: Any) -> dict[str, Any]:
    snapshot, expired, metadata = _last_snapshot_state()
    if snapshot is not None:
        return snapshot
    if expired:
        payload = build_pre_input_commit_status_payload(text)
        payload["decision"] = "pre_input_commit_hold_no_input"
        payload["reason"] = "snapshot_expired"
        payload["blockers"] = _dedupe([*list(payload.get("blockers") or []), "snapshot_expired"])
        payload.update(metadata)
        payload["snapshot_source"] = "ready_review_snapshot_expired"
        payload.update(_base_flags())
        return payload
    return build_pre_input_commit_barrier_payload(text, **kwargs)


def build_pre_input_commit_status_payload(text: str = "") -> dict[str, Any]:
    snapshot, expired, metadata = _last_snapshot_state()
    if snapshot is not None:
        snapshot["decision"] = "pre_input_commit_status_only_no_input"
        snapshot["reason"] = "ready_review_snapshot_fresh"
        snapshot.update(_base_flags())
        return snapshot
    gate = _gate_status(text)
    payload = {
        "schema": PRE_INPUT_COMMIT_SCHEMA,
        "version": 1,
        "decision": "pre_input_commit_status_only_no_input",
        "reason": "no_persistent_pre_input_commit_state",
        **gate,
        "input_readiness_ready": False,
        "input_readiness_decision": None,
        "input_readiness_blockers": [],
        "benchmark_ready": False,
        "benchmark_decision": None,
        "benchmark_recommended": None,
        "benchmark_blockers": [],
        "focus_readiness": "not_checked",
        "window_title": None,
        "window_class": None,
        "process_name": None,
        "emergency_abort_available": "not_checked",
        "emergency_abort_strategy": "preview_only",
        "emergency_release_available": "not_checked",
        "emergency_release_strategy": "preview_only",
        "stream_presence_checked": False,
        "stream_presence_decision": None,
        "stream_presence_overall_ready": False,
        "stream_safe_mode": "not_checked",
        "stream_presence_degrade_reasons": [],
        "dispatch_preflight_ready": False,
        "dispatch_preflight_decision": None,
        "dispatch_packet_valid": False,
        "dispatch_packet_ready": False,
        "dispatch_packet_source": "unavailable",
        "dispatch_selected_metadata_source": None,
        "dispatch_selected_metadata_complete": False,
        "dispatch_timing_consistent": False,
        "dispatch_timing_consistency_error_ms": None,
        "shadow_session_result": "session_not_ready",
        "shadow_session_decision": None,
        "shadow_dispatch_ready_count": 0,
        "shadow_packet_valid_count": 0,
        "shadow_packet_ready_count": 0,
        "shadow_metadata_complete_count": 0,
        "shadow_timing_consistent_count": 0,
        "shadow_stale_lease_ignored_count": 0,
        "shadow_unique_action_count": 0,
        "shadow_first_dispatch_ready_attempt_index": None,
        "first_packet_id": None,
        "first_packet_action": None,
        "first_time_to_action_ms": None,
        "samples_max": MAX_SAMPLES,
        "attempts_max": 10,
        "interval_max_ms": 500,
        "blockers": ["no_persistent_pre_input_commit_state"],
        "warnings": [],
        "ready_for_operator_review": False,
        "created_at": time.time(),
        **(
            {
                **metadata,
                "snapshot_source": "ready_review_snapshot_expired",
                "blockers": ["no_persistent_pre_input_commit_state", "snapshot_expired"],
                "reason": "snapshot_expired",
            }
            if expired
            else metadata
        ),
        **_base_flags(),
    }
    payload.update(_base_flags())
    return payload


def build_pre_input_commit_error_payload(exc: Exception) -> dict[str, Any]:
    payload = build_pre_input_commit_status_payload()
    payload.update(
        {
            "decision": "pre_input_commit_hold_no_input",
            "reason": f"pre_input_commit_error:{type(exc).__name__}",
            "blockers": [f"pre_input_commit_error:{type(exc).__name__}"],
        }
    )
    payload.update(_base_flags())
    return payload


def print_pre_input_commit_barrier(
    payload: dict[str, Any],
    *,
    title: str = "osu! Pre-Input Commit Barrier",
) -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Ready for operator review: {payload['ready_for_operator_review']}")
    print(f"  Reason: {payload['reason']}")
    print(f"  Env gate present: {payload['env_gate_present']}")
    print(f"  Env gate enabled: {payload['env_gate_enabled']}")
    print(f"  Operator token present: {payload['operator_token_present']}")
    print(f"  Operator token valid: {payload['operator_token_valid']}")
    print(f"  Input readiness ready: {payload['input_readiness_ready']}")
    print(f"  Benchmark ready: {payload['benchmark_ready']}")
    print(f"  Focus readiness: {payload['focus_readiness']}")
    print(f"  Window title: {payload.get('window_title')}")
    print(f"  Window class: {payload.get('window_class')}")
    print(f"  Process name: {payload.get('process_name')}")
    print(f"  Emergency abort available: {payload['emergency_abort_available']}")
    print(f"  Emergency abort strategy: {payload['emergency_abort_strategy']}")
    print(f"  Emergency release available: {payload['emergency_release_available']}")
    print(f"  Emergency release strategy: {payload['emergency_release_strategy']}")
    print(f"  Stream presence checked: {payload['stream_presence_checked']}")
    print(f"  Stream presence overall ready: {payload['stream_presence_overall_ready']}")
    print(f"  Stream safe mode: {payload['stream_safe_mode']}")
    print(f"  Stream presence degrade reasons: {payload['stream_presence_degrade_reasons']}")
    print(f"  Dispatch preflight ready: {payload['dispatch_preflight_ready']}")
    print(f"  Dispatch packet valid: {payload['dispatch_packet_valid']}")
    print(f"  Dispatch packet ready: {payload['dispatch_packet_ready']}")
    print(f"  Dispatch packet source: {payload['dispatch_packet_source']}")
    print(f"  Dispatch metadata source: {payload['dispatch_selected_metadata_source']}")
    print(f"  Dispatch metadata complete: {payload['dispatch_selected_metadata_complete']}")
    print(f"  Dispatch timing consistent: {payload['dispatch_timing_consistent']}")
    print(f"  Dispatch timing consistency error ms: {payload['dispatch_timing_consistency_error_ms']}")
    print(f"  Shadow session result: {payload['shadow_session_result']}")
    print(f"  Shadow dispatch ready count: {payload['shadow_dispatch_ready_count']}")
    print(f"  Shadow packet ready count: {payload['shadow_packet_ready_count']}")
    print(f"  Shadow metadata complete count: {payload['shadow_metadata_complete_count']}")
    print(f"  Shadow timing consistent count: {payload['shadow_timing_consistent_count']}")
    print(f"  First packet id: {payload['first_packet_id']}")
    print(f"  First packet action: {payload['first_packet_action']}")
    print(f"  First time to action ms: {payload['first_time_to_action_ms']}")
    print(f"  Samples max: {payload['samples_max']}")
    print(f"  Snapshot source: {payload.get('snapshot_source')}")
    print(f"  Snapshot created at: {payload.get('snapshot_created_at')}")
    print(f"  Snapshot expires at: {payload.get('snapshot_expires_at')}")
    print(f"  Snapshot ttl ms: {payload.get('snapshot_ttl_ms')}")
    print(f"  Snapshot remaining ms: {payload.get('snapshot_remaining_ms')}")
    print(f"  Snapshot from ready review: {payload.get('snapshot_from_ready_review')}")
    print(f"  Blockers: {payload['blockers']}")
    print(f"  Warnings: {payload['warnings']}")
    print(f"  Dispatch: {payload['dispatch']}")
    print(f"  Armed: {payload['armed']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input allowed: {payload['real_input_allowed']}")
    print(f"  Submit allowed: {payload['submit_allowed']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
    print(f"  Gameplay input: {payload['gameplay_input']}")
    print(f"  Key down sent: {payload['key_down_sent']}")
    print(f"  Key up sent: {payload['key_up_sent']}")
    print(f"  Cursor move: {payload['cursor_move']}")
    print(f"  Click: {payload['click']}")
    print(f"  Tap: {payload['tap']}")
    print(f"  VTS call: {payload['vts_call']}")
    print(f"  Voice call: {payload['voice_call']}")
    print(f"  OBS call: {payload['obs_call']}")
    print(f"  Vision call: {payload['vision_call']}")
