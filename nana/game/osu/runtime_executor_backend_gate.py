"""
osu! runtime executor backend gate - Phase 46.

Separately gated boundary over the dry backend operation. This module reports
what would be eligible at the boundary, but never enables execution.
"""

from __future__ import annotations

import copy
import os
import re
import time
from typing import Any, Callable

from nana.game.osu.runtime_executor_backend_dry import build_runtime_executor_backend_dry_payload


RUNTIME_EXECUTOR_BACKEND_GATE_SCHEMA = "nana.osu.runtime_executor_backend_gate.v1"
BACKEND_GATE_ENV = "NANA_OSU_RUNTIME_BACKEND_GATE_ENABLED"
BACKEND_GATE_TOKEN = "I_APPROVE_OSU_RUNTIME_BACKEND_GATE"
DEFAULT_BACKEND_MODE = "preview_only"
ARMED_BACKEND_MODE = "armed_preview_only"
VALID_BACKEND_MODES = {DEFAULT_BACKEND_MODE, ARMED_BACKEND_MODE}

StateReader = Callable[[], dict[str, Any]]
ActivityReader = Callable[[dict[str, Any] | None], dict[str, Any]]

_LAST_BACKEND_GATE_PAYLOAD: dict[str, Any] | None = None

BACKEND_OP_TO_GATE_DECISION = {
    "no_op_wait": "no_op_wait",
    "no_op_hold": "no_op_hold",
    "no_op_skip_late": "no_op_skip_late",
    "no_op_unknown_action": "no_op_unknown_action",
    "move_cursor_to": "would_execute_move_cursor_to",
    "tap_at": "would_execute_tap_at",
    "slider_follow": "would_execute_slider_follow",
    "spinner_hold_or_follow": "would_execute_spinner_hold_or_follow",
}


def _base_flags() -> dict[str, bool]:
    return {
        "dispatch": False,
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


def clear_runtime_executor_backend_gate_state() -> None:
    global _LAST_BACKEND_GATE_PAYLOAD
    _LAST_BACKEND_GATE_PAYLOAD = None


def _copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(payload)
    copied.update(_base_flags())
    return copied


def _store_payload(payload: dict[str, Any]) -> dict[str, Any]:
    global _LAST_BACKEND_GATE_PAYLOAD
    safe_payload = _copy_payload(payload)
    _LAST_BACKEND_GATE_PAYLOAD = _copy_payload(safe_payload)
    return safe_payload


def _flag_value(text: str, name: str) -> str | None:
    match = re.search(rf"(?:^|\s){re.escape(name)}=(\S+)(?=\s|$)", text or "")
    if not match:
        return None
    return match.group(1).strip()


def _gate_diagnostics(text: str) -> dict[str, Any]:
    env_value = os.getenv(BACKEND_GATE_ENV)
    operator_token = _flag_value(text, "--operator-approval-token")
    requested_mode = _flag_value(text, "--backend-mode") or DEFAULT_BACKEND_MODE
    return {
        "env_gate_name": BACKEND_GATE_ENV,
        "env_gate_present": env_value is not None,
        "env_gate_enabled": env_value == "1",
        "operator_token_present": operator_token is not None,
        "operator_token_valid": operator_token == BACKEND_GATE_TOKEN,
        "backend_mode": requested_mode if requested_mode in VALID_BACKEND_MODES else requested_mode,
        "backend_mode_valid": requested_mode in VALID_BACKEND_MODES,
    }


def _gate_blockers(gate: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if gate.get("env_gate_enabled") is not True:
        blockers.append("missing_env_gate")
    if gate.get("operator_token_valid") is not True:
        blockers.append("missing_or_invalid_operator_token")
    if gate.get("backend_mode") != ARMED_BACKEND_MODE:
        blockers.append("backend_mode_not_armed_preview_only")
    if gate.get("backend_mode_valid") is not True:
        blockers.append("invalid_backend_mode")
    return blockers


def _payload(
    *,
    decision: str,
    reason: str,
    dry: dict[str, Any] | None = None,
    gate: dict[str, Any] | None = None,
    backend_gate_decision: str = "no_op_hold",
    backend_armed: bool = False,
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    dry = dry or {}
    gate = gate or {
        "env_gate_name": BACKEND_GATE_ENV,
        "env_gate_present": False,
        "env_gate_enabled": False,
        "operator_token_present": False,
        "operator_token_valid": False,
        "backend_mode": DEFAULT_BACKEND_MODE,
        "backend_mode_valid": True,
    }
    payload = {
        "schema": RUNTIME_EXECUTOR_BACKEND_GATE_SCHEMA,
        "version": 1,
        "decision": decision,
        "reason": reason,
        "env_gate_name": gate.get("env_gate_name"),
        "env_gate_present": bool(gate.get("env_gate_present")),
        "env_gate_enabled": bool(gate.get("env_gate_enabled")),
        "operator_token_present": bool(gate.get("operator_token_present")),
        "operator_token_valid": bool(gate.get("operator_token_valid")),
        "backend_mode": gate.get("backend_mode") or DEFAULT_BACKEND_MODE,
        "backend_armed": bool(backend_armed),
        "backend_execute_allowed": False,
        "backend_gate_decision": backend_gate_decision,
        "backend_ready": bool(dry.get("backend_ready")),
        "backend_op": dry.get("backend_op") or "no_op_hold",
        "dry_backend_mode": dry.get("backend_mode") or "dry_preview",
        "executor_intent": dry.get("executor_intent") or "no_op_hold",
        "target_required": bool(dry.get("target_required")),
        "target_kind": dry.get("target_kind") or "none",
        "target_screen": dry.get("target_screen"),
        "target_playfield": dry.get("target_playfield"),
        "controller_decision": dry.get("controller_decision") or "hold",
        "action_id": dry.get("action_id"),
        "action_type": dry.get("action_type"),
        "time_to_action_ms": dry.get("time_to_action_ms"),
        "timing_state": dry.get("timing_state"),
        "cache_identity_status": dry.get("cache_identity_status"),
        "cache_identity_match": dry.get("cache_identity_match"),
        "map_changed": bool(dry.get("map_changed")),
        "lookup_cost_ms": dry.get("lookup_cost_ms"),
        "blockers": list(blockers or []),
        "warnings": list(warnings or dry.get("warnings") or []),
        "created_at": time.time(),
        **_base_flags(),
    }
    payload.update(_base_flags())
    return payload


def build_runtime_executor_backend_gate_payload(
    text: str = "",
    *,
    bridge_state: dict[str, Any] | None = None,
    state_reader: StateReader | None = None,
    activity_reader: ActivityReader | None = None,
) -> dict[str, Any]:
    dry = build_runtime_executor_backend_dry_payload(
        text,
        bridge_state=bridge_state,
        state_reader=state_reader,
        activity_reader=activity_reader,
    )
    gate = _gate_diagnostics(text)
    gate_blockers = _gate_blockers(gate)
    dry_blockers = list(dry.get("blockers") or [])
    backend_op = str(dry.get("backend_op") or "no_op_hold")
    backend_gate_decision = BACKEND_OP_TO_GATE_DECISION.get(backend_op, "no_op_unknown_action")
    warnings = list(dry.get("warnings") or [])
    if backend_op not in BACKEND_OP_TO_GATE_DECISION:
        dry_blockers.append("unknown_backend_op")

    backend_armed = (
        not gate_blockers
        and gate.get("env_gate_enabled") is True
        and gate.get("operator_token_valid") is True
        and gate.get("backend_mode") == ARMED_BACKEND_MODE
    )
    blockers = [*dry_blockers, *gate_blockers]
    if backend_armed:
        return _store_payload(
            _payload(
                decision="runtime_executor_backend_gate_preview_only_no_input",
                reason="backend_gate_armed_preview_only",
                dry=dry,
                gate=gate,
                backend_gate_decision=backend_gate_decision,
                backend_armed=True,
                blockers=dry_blockers,
                warnings=warnings,
            )
        )

    reason = str(dry.get("reason") or (blockers[0] if blockers else "backend_gate_preview_only"))
    if gate_blockers:
        reason = gate_blockers[0]
    return _store_payload(
        _payload(
            decision="runtime_executor_backend_gate_preview_only_no_input",
            reason=reason,
            dry=dry,
            gate=gate,
            backend_gate_decision=backend_gate_decision,
            backend_armed=False,
            blockers=blockers,
            warnings=warnings,
        )
    )


def build_runtime_executor_backend_gate_status_payload(text: str = "") -> dict[str, Any]:
    if _LAST_BACKEND_GATE_PAYLOAD is not None:
        payload = _copy_payload(_LAST_BACKEND_GATE_PAYLOAD)
        payload["decision"] = "runtime_executor_backend_gate_status_only_no_input"
        payload["reason"] = "last_runtime_executor_backend_gate"
        payload.update(_base_flags())
        return payload
    return _payload(
        decision="runtime_executor_backend_gate_status_only_no_input",
        reason="no_runtime_executor_backend_gate",
        backend_gate_decision="no_op_hold",
        blockers=["no_runtime_executor_backend_gate"],
    )


def build_runtime_executor_backend_gate_error_payload(exc: Exception) -> dict[str, Any]:
    return _payload(
        decision="runtime_executor_backend_gate_preview_only_no_input",
        reason=f"runtime_executor_backend_gate_error:{type(exc).__name__}",
        backend_gate_decision="no_op_hold",
        blockers=[f"runtime_executor_backend_gate_error:{type(exc).__name__}"],
    )


def print_runtime_executor_backend_gate_payload(
    payload: dict[str, Any],
    *,
    title: str = "osu! Runtime Executor Backend Gate Preview",
) -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Backend gate decision: {payload['backend_gate_decision']}")
    print(f"  Backend op: {payload['backend_op']}")
    print(f"  Backend ready: {payload['backend_ready']}")
    print(f"  Backend mode: {payload['backend_mode']}")
    print(f"  Backend armed: {payload['backend_armed']}")
    print(f"  Backend execute allowed: {payload['backend_execute_allowed']}")
    print(f"  Target screen: {payload.get('target_screen')}")
    print(f"  Target playfield: {payload.get('target_playfield')}")
    print(f"  Blockers: {payload['blockers']}")
    print(f"  Warnings: {payload['warnings']}")
    print(f"  Dispatch: {payload['dispatch']}")
    print(f"  Armed: {payload['armed']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
