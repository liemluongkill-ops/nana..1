"""
osu! runtime executor backend dry adapter - Phase 45.

Converts the runtime executor intent preview into one dry backend operation.
State is in-memory only.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Callable

from nana.game.osu.runtime_executor_intent import build_runtime_executor_intent_payload


RUNTIME_EXECUTOR_BACKEND_DRY_SCHEMA = "nana.osu.runtime_executor_backend_dry.v1"

StateReader = Callable[[], dict[str, Any]]
ActivityReader = Callable[[dict[str, Any] | None], dict[str, Any]]

_LAST_BACKEND_DRY_PAYLOAD: dict[str, Any] | None = None

INTENT_TO_BACKEND_OP = {
    "no_op_wait": "no_op_wait",
    "no_op_hold": "no_op_hold",
    "no_op_skip_late": "no_op_skip_late",
    "would_move_cursor_to": "move_cursor_to",
    "would_tap_at": "tap_at",
    "would_slider_follow": "slider_follow",
    "would_spinner_hold_or_follow": "spinner_hold_or_follow",
    "no_op_unknown_action": "no_op_unknown_action",
}

TARGET_BACKEND_OPS = {"move_cursor_to", "tap_at", "slider_follow"}


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


def clear_runtime_executor_backend_dry_state() -> None:
    global _LAST_BACKEND_DRY_PAYLOAD
    _LAST_BACKEND_DRY_PAYLOAD = None


def _copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(payload)
    copied.update(_base_flags())
    return copied


def _store_payload(payload: dict[str, Any]) -> dict[str, Any]:
    global _LAST_BACKEND_DRY_PAYLOAD
    safe_payload = _copy_payload(payload)
    _LAST_BACKEND_DRY_PAYLOAD = _copy_payload(safe_payload)
    return safe_payload


def _has_xy(value: Any) -> bool:
    return isinstance(value, dict) and value.get("x") is not None and value.get("y") is not None


def _target_ok(intent: dict[str, Any], backend_op: str) -> bool:
    if backend_op not in TARGET_BACKEND_OPS and intent.get("target_required") is not True:
        return True
    return _has_xy(intent.get("target_screen")) or _has_xy(intent.get("target_playfield"))


def _payload(
    *,
    decision: str,
    reason: str,
    intent: dict[str, Any] | None = None,
    backend_ready: bool = False,
    backend_op: str = "no_op_hold",
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    intent = intent or {}
    payload = {
        "schema": RUNTIME_EXECUTOR_BACKEND_DRY_SCHEMA,
        "version": 1,
        "decision": decision,
        "reason": reason,
        "backend_ready": bool(backend_ready),
        "backend_op": backend_op,
        "backend_mode": "dry_preview",
        "intent_ready": bool(intent.get("intent_ready")),
        "executor_intent": intent.get("executor_intent") or "no_op_hold",
        "target_required": bool(intent.get("target_required")),
        "target_kind": intent.get("target_kind") or "none",
        "target_screen": intent.get("target_screen"),
        "target_playfield": intent.get("target_playfield"),
        "controller_decision": intent.get("controller_decision") or "hold",
        "action_id": intent.get("action_id"),
        "action_type": intent.get("action_type"),
        "suggested_intent": intent.get("suggested_intent"),
        "action_changed": bool(intent.get("action_changed")),
        "time_to_action_ms": intent.get("time_to_action_ms"),
        "timing_state": intent.get("timing_state"),
        "cache_identity_status": intent.get("cache_identity_status"),
        "cache_identity_match": intent.get("cache_identity_match"),
        "map_changed": bool(intent.get("map_changed")),
        "lookup_cost_ms": intent.get("lookup_cost_ms"),
        "blockers": list(blockers or []),
        "warnings": list(warnings or intent.get("warnings") or []),
        "created_at": time.time(),
        **_base_flags(),
    }
    payload.update(_base_flags())
    return payload


def build_runtime_executor_backend_dry_payload(
    text: str = "",
    *,
    bridge_state: dict[str, Any] | None = None,
    state_reader: StateReader | None = None,
    activity_reader: ActivityReader | None = None,
) -> dict[str, Any]:
    intent = build_runtime_executor_intent_payload(
        text,
        bridge_state=bridge_state,
        state_reader=state_reader,
        activity_reader=activity_reader,
    )
    executor_intent = str(intent.get("executor_intent") or "no_op_hold")
    backend_op = INTENT_TO_BACKEND_OP.get(executor_intent, "no_op_unknown_action")
    blockers = list(intent.get("blockers") or [])
    warnings = list(intent.get("warnings") or [])

    if executor_intent not in INTENT_TO_BACKEND_OP:
        blockers.append("unknown_executor_intent")
    if intent.get("intent_ready") is not True:
        reason = str(intent.get("reason") or (blockers[0] if blockers else executor_intent))
        return _store_payload(
            _payload(
                decision="runtime_executor_backend_dry_hold_no_input",
                reason=reason,
                intent=intent,
                backend_ready=False,
                backend_op=backend_op,
                blockers=blockers or [reason],
                warnings=warnings,
            )
        )
    if not _target_ok(intent, backend_op):
        blockers.append("target_missing")
        return _store_payload(
            _payload(
                decision="runtime_executor_backend_dry_hold_no_input",
                reason="target_missing",
                intent=intent,
                backend_ready=False,
                backend_op=backend_op,
                blockers=blockers,
                warnings=warnings,
            )
        )

    return _store_payload(
        _payload(
            decision="runtime_executor_backend_dry_preview_only_no_input",
            reason="runtime_executor_intent_mapped_to_backend_op",
            intent=intent,
            backend_ready=True,
            backend_op=backend_op,
            blockers=blockers,
            warnings=warnings,
        )
    )


def build_runtime_executor_backend_dry_status_payload(text: str = "") -> dict[str, Any]:
    if _LAST_BACKEND_DRY_PAYLOAD is not None:
        payload = _copy_payload(_LAST_BACKEND_DRY_PAYLOAD)
        payload["decision"] = "runtime_executor_backend_dry_status_only_no_input"
        payload["reason"] = "last_runtime_executor_backend_dry"
        payload.update(_base_flags())
        return payload
    return _payload(
        decision="runtime_executor_backend_dry_status_only_no_input",
        reason="no_runtime_executor_backend_dry",
        backend_ready=False,
        backend_op="no_op_hold",
        blockers=["no_runtime_executor_backend_dry"],
    )


def build_runtime_executor_backend_dry_error_payload(exc: Exception) -> dict[str, Any]:
    return _payload(
        decision="runtime_executor_backend_dry_hold_no_input",
        reason=f"runtime_executor_backend_dry_error:{type(exc).__name__}",
        backend_ready=False,
        backend_op="no_op_hold",
        blockers=[f"runtime_executor_backend_dry_error:{type(exc).__name__}"],
    )


def print_runtime_executor_backend_dry_payload(
    payload: dict[str, Any],
    *,
    title: str = "osu! Runtime Executor Backend Dry Preview",
) -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Backend ready: {payload['backend_ready']}")
    print(f"  Backend op: {payload['backend_op']}")
    print(f"  Executor intent: {payload['executor_intent']}")
    print(f"  Target kind: {payload['target_kind']}")
    print(f"  Action id: {payload.get('action_id')}")
    print(f"  Action type: {payload.get('action_type')}")
    print(f"  Time to action ms: {payload.get('time_to_action_ms')}")
    print(f"  Target screen: {payload.get('target_screen')}")
    print(f"  Target playfield: {payload.get('target_playfield')}")
    print(f"  Blockers: {payload['blockers']}")
    print(f"  Warnings: {payload['warnings']}")
    print(f"  Dispatch: {payload['dispatch']}")
    print(f"  Armed: {payload['armed']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
