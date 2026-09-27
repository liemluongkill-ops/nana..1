"""
osu! executor armed lease preview - Phase 31.

Creates a short-lived preview lease for one selected dry-run action. This module
does not execute input, does not call SendInput, does not invoke emergency
release, and never marks a lease as armed or executable.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from nana.game.osu.executor_arm_preview import build_executor_arm_preview_payload
from nana.game.osu.executor_preflight import REAL_INPUT_EXECUTOR_TOKEN, build_executor_preflight_payload
from nana.game.osu.input_safety import DEFAULT_OPERATOR_ABORT_PATH, OPERATOR_ABORT_PATH_ENV


ARMED_LEASE_SCHEMA = "nana.osu.executor_armed_lease.v1"
DEFAULT_LEASE_TTL_MS = 500
DEFAULT_ARMED_LEASE_PREVIEW_PATH = Path(__file__).resolve().parent / "data" / "executor_armed_lease_preview.json"

ARMED_LEASE_PREVIEW_WRITE_ENV = "NANA_OSU_ARMED_LEASE_PREVIEW_WRITE_ENABLED"
ARMED_LEASE_PREVIEW_WRITE_TOKEN = "I_APPROVE_OSU_ARMED_LEASE_PREVIEW_WRITE"
ARMED_LEASE_CLEAR_ENV = "NANA_OSU_ARMED_LEASE_CLEAR_ENABLED"
ARMED_LEASE_CLEAR_TOKEN = "I_APPROVE_OSU_ARMED_LEASE_CLEAR"
TOKEN_GATE_BLOCKERS = {"missing_or_invalid_operator_token", "missing_or_disabled_env_gate"}

PayloadBuilder = Callable[..., dict[str, Any]]
Clock = Callable[[], float]

_CURRENT_LEASE: dict[str, Any] | None = None
_CLEARED_MARKER: dict[str, Any] | None = None


def _base_flags() -> dict[str, bool]:
    return {
        "armed": False,
        "executable": False,
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "gameplay_input": False,
        "key_down_sent": False,
        "key_up_sent": False,
        "cursor_move": False,
        "click": False,
        "tap": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
    }


def _empty_selection() -> dict[str, Any]:
    return {
        "selected_action_id": None,
        "selected_action_type": None,
        "selected_source_object_index": None,
        "selected_scheduled_t": None,
        "selected_relative_ms": None,
        "selected_timing_state": None,
        "selected_playfield": None,
        "selected_screen": None,
    }


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_flags(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {
        "lease_ttl_ms": DEFAULT_LEASE_TTL_MS,
        "path": None,
        "operator_approval_token": None,
    }
    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        if name in {"lease-ttl-ms", "ttl-ms"}:
            values["lease_ttl_ms"] = int(raw_value)
        elif name == "path":
            values["path"] = raw_value
        elif name == "operator-approval-token":
            values["operator_approval_token"] = raw_value.strip()
    if int(values["lease_ttl_ms"]) <= 0:
        raise ValueError("lease_ttl_ms_must_be_positive")
    return values


def _resolve_state_path(raw_path: str | os.PathLike[str] | None = None) -> Path:
    if raw_path:
        return Path(raw_path)
    return DEFAULT_ARMED_LEASE_PREVIEW_PATH


def _gate(env_name: str, token: str, supplied_token: Any) -> dict[str, Any]:
    raw_env = os.getenv(env_name)
    env_present = raw_env is not None and str(raw_env).strip() != ""
    env_enabled = _is_truthy(raw_env)
    token_present = bool(supplied_token)
    token_valid = supplied_token == token
    blockers: list[str] = []
    if not env_enabled:
        blockers.append("missing_or_disabled_env_gate")
    if not token_valid:
        blockers.append("missing_or_invalid_operator_token")
    return {
        "env_gate_name": env_name,
        "env_gate_present": env_present,
        "env_gate_enabled": env_enabled,
        "token_present": token_present,
        "token_valid": token_valid,
        "blockers": blockers,
    }


def _safe_call_builder(builder: PayloadBuilder, text: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        return builder(text, **kwargs)
    except TypeError:
        compact_kwargs = {key: value for key, value in kwargs.items() if value is not None}
        try:
            return builder(text, **compact_kwargs)
        except TypeError:
            return builder(text)


def _safe_arm_preview(
    text: str,
    *,
    arm_preview_builder: PayloadBuilder | None,
    preflight_builder: PayloadBuilder | None,
    schedule_builder: PayloadBuilder | None,
    schedule_kwargs: dict[str, Any],
) -> dict[str, Any]:
    builder = arm_preview_builder or build_executor_arm_preview_payload
    call_kwargs = dict(schedule_kwargs)
    if preflight_builder is not None:
        call_kwargs["preflight_builder"] = preflight_builder
    if schedule_builder is not None:
        call_kwargs["schedule_builder"] = schedule_builder
    try:
        return _safe_call_builder(builder, text, call_kwargs)
    except Exception as exc:
        return {
            "schema": "nana.osu.executor_arm_preview.v1",
            "version": 1,
            "decision": "executor_arm_preview_hold_no_input",
            "reason": f"executor_arm_preview_error:{type(exc).__name__}",
            "armable": False,
            **_base_flags(),
            **_empty_selection(),
            "preflight_decision": "executor_preflight_error_no_input",
            "ready_for_manual_review": False,
            "focus_readiness": "unavailable_no_helper",
            "emergency_abort_available": "unavailable_no_helper",
            "emergency_release_available": "unavailable_no_helper",
            "blockers": [f"executor_arm_preview_error:{type(exc).__name__}"],
        }


def _safe_preflight(
    text: str,
    *,
    preflight_builder: PayloadBuilder | None,
    schedule_builder: PayloadBuilder | None,
    schedule_kwargs: dict[str, Any],
) -> dict[str, Any]:
    builder = preflight_builder or build_executor_preflight_payload
    call_kwargs = dict(schedule_kwargs)
    if schedule_builder is not None:
        call_kwargs["schedule_builder"] = schedule_builder
    try:
        return _safe_call_builder(builder, text, call_kwargs)
    except Exception as exc:
        return {
            "schema": "nana.osu.executor_preflight.v1",
            "version": 1,
            "decision": "executor_preflight_error_no_input",
            "ready_for_manual_review": False,
            "focus_readiness": "unavailable_no_helper",
            "emergency_abort_available": "unavailable_no_helper",
            "emergency_release_available": "unavailable_no_helper",
            "blockers": [f"executor_preflight_error:{type(exc).__name__}"],
            "executable": False,
            "real_input": False,
            "submit": False,
        }


def _selection_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "selected_action_id": payload.get("selected_action_id"),
        "selected_action_type": payload.get("selected_action_type"),
        "selected_source_object_index": payload.get("selected_source_object_index"),
        "selected_scheduled_t": payload.get("selected_scheduled_t"),
        "selected_relative_ms": payload.get("selected_relative_ms"),
        "selected_timing_state": payload.get("selected_timing_state"),
        "selected_playfield": payload.get("selected_playfield"),
        "selected_screen": payload.get("selected_screen"),
    }


def _lease_remaining_ms(payload: dict[str, Any], now: float) -> int:
    expires_at = payload.get("lease_expires_at")
    if expires_at is None:
        return 0
    try:
        return max(0, int(round((float(expires_at) - now) * 1000)))
    except (TypeError, ValueError):
        return 0


def _required_payload(
    *,
    decision: str,
    lease_state: str,
    lease_id: str | None = None,
    lease_ttl_ms: int | None = None,
    lease_created_at: float | None = None,
    lease_expires_at: float | None = None,
    lease_remaining_ms: int = 0,
    armed_preview: bool = False,
    armable: bool = False,
    selected: dict[str, Any] | None = None,
    focus_readiness: Any = "unavailable_no_lease",
    emergency_abort_available: Any = "unavailable_no_lease",
    emergency_release_available: Any = "unavailable_no_lease",
    preflight_decision: Any = "not_checked_no_lease",
    ready_for_manual_review: bool = False,
    blockers: list[str] | None = None,
    file_write: bool = False,
    cleared: bool = False,
    reason: str | None = None,
    lease_created_with_token_present: bool | None = None,
    lease_created_with_token_valid: bool | None = None,
    lease_created_preflight_decision: Any = None,
    lease_created_ready_for_manual_review: bool | None = None,
    status_recheck_token_present: bool = False,
    status_recheck_token_valid: bool = False,
    status_recheck_mode: str = "snapshot",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": ARMED_LEASE_SCHEMA,
        "version": 1,
        "decision": decision,
        "lease_state": lease_state,
        "lease_id": lease_id,
        "lease_ttl_ms": lease_ttl_ms,
        "lease_created_at": lease_created_at,
        "lease_expires_at": lease_expires_at,
        "lease_remaining_ms": lease_remaining_ms,
        "armed_preview": armed_preview,
        "armable": armable,
        **_base_flags(),
        **(selected or _empty_selection()),
        "focus_readiness": focus_readiness,
        "emergency_abort_available": emergency_abort_available,
        "emergency_release_available": emergency_release_available,
        "preflight_decision": preflight_decision,
        "ready_for_manual_review": ready_for_manual_review,
        "blockers": blockers or [],
        "file_write": file_write,
        "cleared": cleared,
        "lease_created_with_token_present": lease_created_with_token_present,
        "lease_created_with_token_valid": lease_created_with_token_valid,
        "lease_created_preflight_decision": lease_created_preflight_decision,
        "lease_created_ready_for_manual_review": lease_created_ready_for_manual_review,
        "status_recheck_token_present": status_recheck_token_present,
        "status_recheck_token_valid": status_recheck_token_valid,
        "status_recheck_mode": status_recheck_mode,
    }
    if reason is not None:
        payload["reason"] = reason
    if extra:
        payload.update(extra)
    payload.update(_base_flags())
    return payload


def _build_lease_from_arm_preview(
    arm_preview: dict[str, Any],
    *,
    ttl_ms: int,
    now: float,
    operator_token: str | None,
) -> dict[str, Any]:
    lease_id = f"lease-{int(now * 1000)}-{uuid.uuid4().hex[:8]}"
    expires_at = now + (ttl_ms / 1000.0)
    token_present = bool(operator_token)
    token_valid = operator_token == REAL_INPUT_EXECUTOR_TOKEN
    preflight_decision = arm_preview.get("preflight_decision")
    ready_for_manual_review = bool(arm_preview.get("ready_for_manual_review"))
    return _required_payload(
        decision="executor_armed_lease_preview_created_no_input",
        lease_state="preview_active",
        lease_id=lease_id,
        lease_ttl_ms=int(ttl_ms),
        lease_created_at=now,
        lease_expires_at=expires_at,
        lease_remaining_ms=int(ttl_ms),
        armed_preview=True,
        armable=True,
        selected=_selection_from_payload(arm_preview),
        focus_readiness=arm_preview.get("focus_readiness") or "unavailable_no_helper",
        emergency_abort_available=arm_preview.get("emergency_abort_available") or "unavailable_no_helper",
        emergency_release_available=arm_preview.get("emergency_release_available") or "unavailable_no_helper",
        preflight_decision=preflight_decision,
        ready_for_manual_review=ready_for_manual_review,
        blockers=[],
        reason="lease_preview_created_no_input",
        lease_created_with_token_present=token_present,
        lease_created_with_token_valid=token_valid,
        lease_created_preflight_decision=preflight_decision,
        lease_created_ready_for_manual_review=ready_for_manual_review,
        extra={
            "source_arm_preview_decision": arm_preview.get("decision"),
            "source_arm_preview_reason": arm_preview.get("reason"),
        },
    )


def _write_state_file(payload: dict[str, Any], path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    output = dict(payload)
    output["written_at"] = time.time()
    output["output_path"] = str(path)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
    return {
        "file_write": True,
        "output_path": str(path),
        "bytes_written": path.stat().st_size,
    }


def _try_optional_write(payload: dict[str, Any], *, text: str, path: Path) -> dict[str, Any]:
    flags = _parse_flags(text)
    gate = _gate(
        ARMED_LEASE_PREVIEW_WRITE_ENV,
        ARMED_LEASE_PREVIEW_WRITE_TOKEN,
        flags.get("operator_approval_token"),
    )
    result = {
        "file_write": False,
        "output_path": str(path),
        "file_write_env_gate_name": ARMED_LEASE_PREVIEW_WRITE_ENV,
        "file_write_env_gate_present": gate["env_gate_present"],
        "file_write_env_gate_enabled": gate["env_gate_enabled"],
        "file_write_token_present": gate["token_present"],
        "file_write_token_valid": gate["token_valid"],
        "file_write_blockers": list(gate["blockers"]),
    }
    if gate["blockers"]:
        return result
    try:
        written = _write_state_file(payload, path)
    except Exception as exc:
        result["file_write_error"] = f"{type(exc).__name__}:{exc}"
        return result
    result.update(written)
    result["file_write_blockers"] = []
    return result


def _read_state_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != ARMED_LEASE_SCHEMA:
        return None
    return payload


def _abort_flag_path(preflight: dict[str, Any]) -> Path:
    nested_abort = (preflight.get("input_safety") or {}).get("abort") or {}
    raw_path = (
        preflight.get("abort_flag_path")
        or nested_abort.get("abort_flag_path")
        or os.getenv(OPERATOR_ABORT_PATH_ENV)
        or DEFAULT_OPERATOR_ABORT_PATH
    )
    return Path(raw_path)


def _truthy_payload_key(payload: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(bool(payload.get(key)) for key in keys)


def _abort_blockers(preflight: dict[str, Any]) -> list[str]:
    nested_abort = (preflight.get("input_safety") or {}).get("abort") or {}
    path = _abort_flag_path(preflight)
    blockers: list[str] = []
    if path.exists():
        blockers.append(f"operator_abort_flag_present:{path}")
    if _truthy_payload_key(preflight, ("abort_blocked", "abort_requested", "operator_abort_active")):
        blockers.append("operator_abort_status_blocked")
    if _truthy_payload_key(nested_abort, ("abort_blocked", "abort_requested", "operator_abort_active")):
        blockers.append("operator_abort_status_blocked")
    for value in (
        preflight.get("abort_status"),
        preflight.get("operator_abort_status"),
        nested_abort.get("abort_status"),
        nested_abort.get("operator_abort_status"),
        preflight.get("emergency_abort_available"),
        nested_abort.get("emergency_abort_available"),
    ):
        if str(value or "").strip().lower() in {"blocked", "abort_requested", "active", "triggered"}:
            blockers.append(f"operator_abort_status:{value}")
    return blockers


def _status_token_flags(text: str) -> dict[str, bool]:
    flags = _parse_flags(text)
    token = flags.get("operator_approval_token")
    return {
        "status_recheck_token_present": bool(token),
        "status_recheck_token_valid": token == REAL_INPUT_EXECUTOR_TOKEN,
    }


def _lease_approval_snapshot_valid(lease: dict[str, Any]) -> bool:
    if lease.get("lease_created_with_token_valid") is True and lease.get("lease_created_ready_for_manual_review") is True:
        return True
    return (
        lease.get("lease_created_with_token_valid") is None
        and lease.get("ready_for_manual_review") is True
        and lease.get("preflight_decision") == "executor_preflight_ready_for_manual_review_no_input"
    )


def _filtered_non_token_blockers(preflight: dict[str, Any]) -> list[str]:
    return [
        str(blocker)
        for blocker in (preflight.get("blockers") or [])
        if str(blocker) not in TOKEN_GATE_BLOCKERS
    ]


def _preflight_chain_ready_without_token_gate(preflight: dict[str, Any], *, fallback_ready: bool) -> bool:
    non_token_blockers = _filtered_non_token_blockers(preflight)
    if non_token_blockers:
        return False
    if bool(preflight.get("ready_for_manual_review")):
        return True

    chain_keys = (
        "readiness_ready",
        "benchmark_ready",
        "trajectory_ready",
        "schedule_ready",
        "all_actions_non_executable",
    )
    if all(key in preflight for key in chain_keys):
        try:
            schedule_count = int(preflight.get("schedule_count") or 0)
        except (TypeError, ValueError):
            schedule_count = 0
        return all(bool(preflight.get(key)) for key in chain_keys) and schedule_count > 0

    return fallback_ready


def _lease_from_memory_or_file(path: Path) -> dict[str, Any] | None:
    if _CURRENT_LEASE is not None:
        return dict(_CURRENT_LEASE)
    file_lease = _read_state_file(path)
    if file_lease is not None:
        return file_lease
    if _CLEARED_MARKER is not None:
        return dict(_CLEARED_MARKER)
    return None


def _status_from_lease(
    lease: dict[str, Any] | None,
    *,
    text: str,
    path: Path,
    now: float,
    preflight_builder: PayloadBuilder | None,
    schedule_builder: PayloadBuilder | None,
    schedule_kwargs: dict[str, Any],
) -> dict[str, Any]:
    token_flags = _status_token_flags(text)
    if lease is None:
        return _required_payload(
            decision="executor_armed_lease_status_only_no_input",
            lease_state="no_lease",
            lease_ttl_ms=DEFAULT_LEASE_TTL_MS,
            blockers=["no_preview_lease"],
            status_recheck_token_present=token_flags["status_recheck_token_present"],
            status_recheck_token_valid=token_flags["status_recheck_token_valid"],
            status_recheck_mode="snapshot",
            extra={"output_path": str(path)},
        )

    if lease.get("lease_state") == "preview_cleared":
        return _required_payload(
            decision="executor_armed_lease_status_only_no_input",
            lease_state="preview_cleared",
            lease_id=lease.get("lease_id"),
            lease_ttl_ms=lease.get("lease_ttl_ms"),
            lease_created_at=lease.get("lease_created_at"),
            lease_expires_at=lease.get("lease_expires_at"),
            lease_remaining_ms=0,
            selected=_selection_from_payload(lease),
            blockers=["preview_lease_cleared"],
            cleared=True,
            lease_created_with_token_present=lease.get("lease_created_with_token_present"),
            lease_created_with_token_valid=lease.get("lease_created_with_token_valid"),
            lease_created_preflight_decision=lease.get("lease_created_preflight_decision") or lease.get("preflight_decision"),
            lease_created_ready_for_manual_review=lease.get("lease_created_ready_for_manual_review"),
            status_recheck_token_present=token_flags["status_recheck_token_present"],
            status_recheck_token_valid=token_flags["status_recheck_token_valid"],
            status_recheck_mode="snapshot",
            extra={"output_path": str(path)},
        )

    lease_snapshot_valid = _lease_approval_snapshot_valid(lease)
    status_recheck_mode = "full_token_recheck" if token_flags["status_recheck_token_present"] else "safety_only_recheck"
    preflight = _safe_preflight(
        text,
        preflight_builder=preflight_builder,
        schedule_builder=schedule_builder,
        schedule_kwargs=schedule_kwargs,
    )
    focus_readiness = preflight.get("focus_readiness") or "unavailable_no_helper"
    abort_available = preflight.get("emergency_abort_available") or "unavailable_no_helper"
    release_available = preflight.get("emergency_release_available") or "unavailable_no_helper"
    preflight_decision = preflight.get("decision")
    if status_recheck_mode == "safety_only_recheck" and lease_snapshot_valid:
        ready_for_manual_review = _preflight_chain_ready_without_token_gate(preflight, fallback_ready=True)
    else:
        ready_for_manual_review = bool(preflight.get("ready_for_manual_review"))
    blockers: list[str] = []

    abort_blockers = _abort_blockers(preflight)
    if abort_blockers:
        lease_state = "preview_blocked_by_abort"
        blockers.extend(abort_blockers)
    elif focus_readiness != "ready":
        lease_state = "preview_blocked_by_focus"
        blockers.append(f"focus_not_ready:{focus_readiness}")
    elif not ready_for_manual_review:
        lease_state = "preview_blocked_by_preflight"
        if status_recheck_mode == "safety_only_recheck" and lease_snapshot_valid:
            blockers.extend(_filtered_non_token_blockers(preflight))
        else:
            blockers.extend(str(blocker) for blocker in (preflight.get("blockers") or []))
        if not blockers:
            blockers.append("preflight_not_ready")
    else:
        try:
            expires_at = float(lease.get("lease_expires_at"))
        except (TypeError, ValueError):
            expires_at = now
        if now > expires_at:
            lease_state = "preview_expired"
            blockers.append("lease_expired")
        else:
            lease_state = "preview_active"

    active = lease_state == "preview_active"
    return _required_payload(
        decision="executor_armed_lease_status_only_no_input",
        lease_state=lease_state,
        lease_id=lease.get("lease_id"),
        lease_ttl_ms=lease.get("lease_ttl_ms"),
        lease_created_at=lease.get("lease_created_at"),
        lease_expires_at=lease.get("lease_expires_at"),
        lease_remaining_ms=_lease_remaining_ms(lease, now),
        armed_preview=lease_state != "preview_cleared",
        armable=active,
        selected=_selection_from_payload(lease),
        focus_readiness=focus_readiness,
        emergency_abort_available=abort_available,
        emergency_release_available=release_available,
        preflight_decision=preflight_decision,
        ready_for_manual_review=ready_for_manual_review,
        blockers=blockers,
        lease_created_with_token_present=lease.get("lease_created_with_token_present"),
        lease_created_with_token_valid=lease.get("lease_created_with_token_valid"),
        lease_created_preflight_decision=lease.get("lease_created_preflight_decision") or lease.get("preflight_decision"),
        lease_created_ready_for_manual_review=lease.get("lease_created_ready_for_manual_review"),
        status_recheck_token_present=token_flags["status_recheck_token_present"],
        status_recheck_token_valid=token_flags["status_recheck_token_valid"],
        status_recheck_mode=status_recheck_mode,
        extra={"output_path": str(path)},
    )


def build_executor_armed_lease_preview_payload(
    text: str = "",
    *,
    arm_preview_builder: PayloadBuilder | None = None,
    preflight_builder: PayloadBuilder | None = None,
    schedule_builder: PayloadBuilder | None = None,
    clock: Clock | None = None,
    **schedule_kwargs: Any,
) -> dict[str, Any]:
    flags = _parse_flags(text)
    now = (clock or time.time)()
    path = _resolve_state_path(flags.get("path"))
    arm_preview = _safe_arm_preview(
        text,
        arm_preview_builder=arm_preview_builder,
        preflight_builder=preflight_builder,
        schedule_builder=schedule_builder,
        schedule_kwargs=schedule_kwargs,
    )

    if not bool(arm_preview.get("armable")):
        selected = _selection_from_payload(arm_preview)
        return _required_payload(
            decision="executor_armed_lease_preview_hold_no_input",
            lease_state="no_lease",
            lease_ttl_ms=int(flags["lease_ttl_ms"]),
            selected=selected,
            focus_readiness=arm_preview.get("focus_readiness") or "unavailable_no_helper",
            emergency_abort_available=arm_preview.get("emergency_abort_available") or "unavailable_no_helper",
            emergency_release_available=arm_preview.get("emergency_release_available") or "unavailable_no_helper",
            preflight_decision=arm_preview.get("preflight_decision"),
            ready_for_manual_review=bool(arm_preview.get("ready_for_manual_review")),
            blockers=[str(blocker) for blocker in (arm_preview.get("blockers") or ["arm_preview_not_armable"])],
            reason=arm_preview.get("reason") or "arm_preview_not_armable",
            extra={
                "output_path": str(path),
                "source_arm_preview_decision": arm_preview.get("decision"),
            },
        )

    lease = _build_lease_from_arm_preview(
        arm_preview,
        ttl_ms=int(flags["lease_ttl_ms"]),
        now=now,
        operator_token=flags.get("operator_approval_token"),
    )
    global _CURRENT_LEASE, _CLEARED_MARKER
    _CURRENT_LEASE = dict(lease)
    _CLEARED_MARKER = None
    write_result = _try_optional_write(lease, text=text, path=path)
    lease.update(write_result)
    return lease


def build_executor_armed_lease_status_payload(
    text: str = "",
    *,
    preflight_builder: PayloadBuilder | None = None,
    schedule_builder: PayloadBuilder | None = None,
    clock: Clock | None = None,
    **schedule_kwargs: Any,
) -> dict[str, Any]:
    flags = _parse_flags(text)
    now = (clock or time.time)()
    path = _resolve_state_path(flags.get("path"))
    lease = _lease_from_memory_or_file(path)
    return _status_from_lease(
        lease,
        text=text,
        path=path,
        now=now,
        preflight_builder=preflight_builder,
        schedule_builder=schedule_builder,
        schedule_kwargs=schedule_kwargs,
    )


def build_executor_armed_lease_clear_payload(text: str = "", *, clock: Clock | None = None) -> dict[str, Any]:
    flags = _parse_flags(text)
    path = _resolve_state_path(flags.get("path"))
    gate = _gate(ARMED_LEASE_CLEAR_ENV, ARMED_LEASE_CLEAR_TOKEN, flags.get("operator_approval_token"))
    now = (clock or time.time)()
    previous = _lease_from_memory_or_file(path)
    previous_id = previous.get("lease_id") if previous else None

    if gate["blockers"]:
        return _required_payload(
            decision="executor_armed_lease_clear_hold_no_input",
            lease_state=(previous or {}).get("lease_state") or "no_lease",
            lease_id=previous_id,
            lease_ttl_ms=(previous or {}).get("lease_ttl_ms") or int(flags["lease_ttl_ms"]),
            lease_created_at=(previous or {}).get("lease_created_at"),
            lease_expires_at=(previous or {}).get("lease_expires_at"),
            lease_remaining_ms=_lease_remaining_ms(previous or {}, now),
            armed_preview=bool(previous and previous.get("lease_state") != "preview_cleared"),
            armable=False,
            selected=_selection_from_payload(previous or {}),
            blockers=list(gate["blockers"]),
            reason="clear_gate_hold_no_input",
            lease_created_with_token_present=(previous or {}).get("lease_created_with_token_present"),
            lease_created_with_token_valid=(previous or {}).get("lease_created_with_token_valid"),
            lease_created_preflight_decision=(previous or {}).get("lease_created_preflight_decision")
            or (previous or {}).get("preflight_decision"),
            lease_created_ready_for_manual_review=(previous or {}).get("lease_created_ready_for_manual_review"),
            extra={
                "output_path": str(path),
                "clear_env_gate_name": ARMED_LEASE_CLEAR_ENV,
                "clear_env_gate_present": gate["env_gate_present"],
                "clear_env_gate_enabled": gate["env_gate_enabled"],
                "clear_token_present": gate["token_present"],
                "clear_token_valid": gate["token_valid"],
            },
        )

    file_write = False
    file_clear_error = None
    if path.exists():
        try:
            path.unlink()
            file_write = True
        except Exception as exc:
            file_clear_error = f"{type(exc).__name__}:{exc}"

    marker = _required_payload(
        decision="executor_armed_lease_cleared_no_input",
        lease_state="preview_cleared",
        lease_id=previous_id,
        lease_ttl_ms=(previous or {}).get("lease_ttl_ms") or int(flags["lease_ttl_ms"]),
        lease_created_at=(previous or {}).get("lease_created_at"),
        lease_expires_at=(previous or {}).get("lease_expires_at"),
        lease_remaining_ms=0,
        selected=_selection_from_payload(previous or {}),
        blockers=[] if file_clear_error is None else [f"file_clear_error:{file_clear_error}"],
        file_write=file_write,
        cleared=True,
        reason="lease_preview_cleared_no_input",
        lease_created_with_token_present=(previous or {}).get("lease_created_with_token_present"),
        lease_created_with_token_valid=(previous or {}).get("lease_created_with_token_valid"),
        lease_created_preflight_decision=(previous or {}).get("lease_created_preflight_decision")
        or (previous or {}).get("preflight_decision"),
        lease_created_ready_for_manual_review=(previous or {}).get("lease_created_ready_for_manual_review"),
        extra={
            "output_path": str(path),
            "clear_env_gate_name": ARMED_LEASE_CLEAR_ENV,
            "clear_env_gate_present": gate["env_gate_present"],
            "clear_env_gate_enabled": gate["env_gate_enabled"],
            "clear_token_present": gate["token_present"],
            "clear_token_valid": gate["token_valid"],
        },
    )

    global _CURRENT_LEASE, _CLEARED_MARKER
    _CURRENT_LEASE = None
    _CLEARED_MARKER = dict(marker)
    return marker


def print_executor_armed_lease(payload: dict[str, Any], *, title: str = "osu! Executor Armed Lease") -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    if payload.get("reason"):
        print(f"  Reason: {payload['reason']}")
    print(f"  Lease state: {payload['lease_state']}")
    print(f"  Lease id: {payload['lease_id']}")
    print(f"  Lease ttl ms: {payload['lease_ttl_ms']}")
    print(f"  Lease created at: {payload['lease_created_at']}")
    print(f"  Lease expires at: {payload['lease_expires_at']}")
    print(f"  Lease remaining ms: {payload['lease_remaining_ms']}")
    print(f"  Armed preview: {payload['armed_preview']}")
    print(f"  Armed: {payload['armed']}")
    print(f"  Armable: {payload['armable']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Selected action id: {payload['selected_action_id']}")
    print(f"  Selected action type: {payload['selected_action_type']}")
    print(f"  Selected source object index: {payload.get('selected_source_object_index')}")
    print(f"  Selected scheduled t: {payload['selected_scheduled_t']}")
    print(f"  Selected relative ms: {payload['selected_relative_ms']}")
    print(f"  Selected timing state: {payload['selected_timing_state']}")
    print(f"  Selected playfield: {payload.get('selected_playfield')}")
    print(f"  Selected screen: {payload.get('selected_screen')}")
    print(f"  Focus readiness: {payload['focus_readiness']}")
    print(f"  Emergency abort available: {payload['emergency_abort_available']}")
    print(f"  Emergency release available: {payload['emergency_release_available']}")
    print(f"  Preflight decision: {payload['preflight_decision']}")
    print(f"  Ready for manual review: {payload['ready_for_manual_review']}")
    print(f"  Lease created token present: {payload.get('lease_created_with_token_present')}")
    print(f"  Lease created token valid: {payload.get('lease_created_with_token_valid')}")
    print(f"  Lease created preflight decision: {payload.get('lease_created_preflight_decision')}")
    print(f"  Lease created ready for manual review: {payload.get('lease_created_ready_for_manual_review')}")
    print(f"  Status recheck token present: {payload.get('status_recheck_token_present')}")
    print(f"  Status recheck token valid: {payload.get('status_recheck_token_valid')}")
    print(f"  Status recheck mode: {payload.get('status_recheck_mode')}")
    print(f"  Blockers: {payload['blockers']}")
    print(f"  File write: {payload['file_write']}")
    print(f"  Cleared: {payload['cleared']}")
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
