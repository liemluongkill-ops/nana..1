"""
osu! runtime executor intent preview - Phase 44.

Maps the thin runtime controller preview into a concrete no-input executor
intent. State is in-memory only.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Callable

from nana.game.osu.runtime_controller_preview import build_runtime_controller_preview_payload


RUNTIME_EXECUTOR_INTENT_SCHEMA = "nana.osu.runtime_executor_intent.v1"

StateReader = Callable[[], dict[str, Any]]
ActivityReader = Callable[[dict[str, Any] | None], dict[str, Any]]

_LAST_INTENT_PAYLOAD: dict[str, Any] | None = None


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


def clear_runtime_executor_intent_state() -> None:
    global _LAST_INTENT_PAYLOAD
    _LAST_INTENT_PAYLOAD = None


def _copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(payload)
    copied.update(_base_flags())
    return copied


def _store_payload(payload: dict[str, Any]) -> dict[str, Any]:
    global _LAST_INTENT_PAYLOAD
    safe_payload = _copy_payload(payload)
    _LAST_INTENT_PAYLOAD = _copy_payload(safe_payload)
    return safe_payload


def _has_xy(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return value.get("x") is not None and value.get("y") is not None


def _target_state(controller: dict[str, Any], *, target_required: bool) -> tuple[str, list[str], list[str], bool]:
    screen = controller.get("screen")
    playfield = controller.get("playfield")
    if _has_xy(screen):
        return "screen", [], [], True
    if _has_xy(playfield):
        warnings = ["screen_target_missing"] if target_required else []
        return "playfield_only", [], warnings, True
    if target_required:
        return "none", ["target_missing"], [], False
    return "none", [], [], True


def _intent_for(controller_decision: str | None, action_type: str | None) -> tuple[str, bool, bool, list[str]]:
    blockers: list[str] = []
    if controller_decision == "hold":
        return "no_op_hold", False, False, blockers
    if controller_decision == "would_wait":
        return "no_op_wait", True, False, blockers
    if controller_decision == "would_skip_late":
        return "no_op_skip_late", False, False, blockers

    if controller_decision == "would_prepare":
        if action_type in {"tap_candidate", "slider_follow_candidate", "move_to_candidate"}:
            return "would_move_cursor_to", True, True, blockers
        if action_type == "spinner_candidate":
            return "would_prepare_spinner", True, False, blockers
        blockers.append("unknown_action_type")
        return "no_op_unknown_action", False, False, blockers

    if controller_decision == "would_fire_preview_only":
        if action_type == "tap_candidate":
            return "would_tap_at", True, True, blockers
        if action_type == "slider_follow_candidate":
            return "would_slider_follow", True, True, blockers
        if action_type == "spinner_candidate":
            return "would_spinner_hold_or_follow", True, False, blockers
        if action_type == "move_to_candidate":
            return "would_move_cursor_to", True, True, blockers
        blockers.append("unknown_action_type")
        return "no_op_unknown_action", False, False, blockers

    blockers.append("unknown_controller_decision")
    return "no_op_hold", False, False, blockers


def _payload(
    *,
    decision: str,
    reason: str,
    controller: dict[str, Any] | None = None,
    intent_ready: bool = False,
    executor_intent: str = "no_op_hold",
    target_required: bool = False,
    target_kind: str = "none",
    blockers: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    controller = controller or {}
    payload = {
        "schema": RUNTIME_EXECUTOR_INTENT_SCHEMA,
        "version": 1,
        "decision": decision,
        "reason": reason,
        "intent_ready": bool(intent_ready),
        "executor_intent": executor_intent,
        "target_required": bool(target_required),
        "target_kind": target_kind,
        "target_screen": controller.get("screen") if target_kind == "screen" else None,
        "target_playfield": controller.get("playfield") if target_kind in {"screen", "playfield_only"} else None,
        "controller_decision": controller.get("controller_decision") or "hold",
        "action_id": controller.get("action_id"),
        "action_type": controller.get("action_type"),
        "suggested_intent": controller.get("suggested_intent"),
        "action_changed": bool(controller.get("action_changed")),
        "time_to_action_ms": controller.get("time_to_action_ms"),
        "timing_state": controller.get("timing_state"),
        "cache_identity_status": controller.get("cache_identity_status"),
        "cache_identity_match": controller.get("cache_identity_match"),
        "map_changed": bool(controller.get("map_changed")),
        "lookup_cost_ms": controller.get("lookup_cost_ms"),
        "blockers": list(blockers or []),
        "warnings": list(warnings or []),
        "created_at": time.time(),
        **_base_flags(),
    }
    payload.update(_base_flags())
    return payload


def build_runtime_executor_intent_payload(
    text: str = "",
    *,
    bridge_state: dict[str, Any] | None = None,
    state_reader: StateReader | None = None,
    activity_reader: ActivityReader | None = None,
) -> dict[str, Any]:
    controller = build_runtime_controller_preview_payload(
        text,
        bridge_state=bridge_state,
        state_reader=state_reader,
        activity_reader=activity_reader,
    )
    controller_decision = str(controller.get("controller_decision") or "hold")
    controller_blockers = list(controller.get("blockers") or [])
    if controller.get("decision") == "runtime_controller_hold_no_input" or controller_decision == "hold":
        reason = str(controller.get("reason") or (controller_blockers[0] if controller_blockers else "controller_hold"))
        return _store_payload(
            _payload(
                decision="runtime_executor_intent_hold_no_input",
                reason=reason,
                controller=controller,
                intent_ready=False,
                executor_intent="no_op_hold",
                blockers=controller_blockers or [reason],
            )
        )

    executor_intent, intent_ready, target_required, intent_blockers = _intent_for(
        controller_decision,
        controller.get("action_type"),
    )
    target_kind, target_blockers, warnings, target_ok = _target_state(
        controller,
        target_required=target_required,
    )
    blockers = [*intent_blockers, *target_blockers]
    if not target_ok:
        intent_ready = False
    if controller_decision == "would_skip_late":
        intent_ready = False
    decision = (
        "runtime_executor_intent_preview_only_no_input"
        if intent_ready
        else "runtime_executor_intent_hold_no_input"
    )
    reason = "runtime_controller_intent_mapped" if intent_ready else executor_intent
    if blockers:
        reason = blockers[0]
    return _store_payload(
        _payload(
            decision=decision,
            reason=reason,
            controller=controller,
            intent_ready=intent_ready,
            executor_intent=executor_intent,
            target_required=target_required,
            target_kind=target_kind,
            blockers=blockers,
            warnings=warnings,
        )
    )


def build_runtime_executor_intent_status_payload(text: str = "") -> dict[str, Any]:
    if _LAST_INTENT_PAYLOAD is not None:
        payload = _copy_payload(_LAST_INTENT_PAYLOAD)
        payload["decision"] = "runtime_executor_intent_status_only_no_input"
        payload["reason"] = "last_runtime_executor_intent"
        payload.update(_base_flags())
        return payload
    return _payload(
        decision="runtime_executor_intent_status_only_no_input",
        reason="no_runtime_executor_intent",
        intent_ready=False,
        executor_intent="no_op_hold",
        blockers=["no_runtime_executor_intent"],
    )


def build_runtime_executor_intent_error_payload(exc: Exception) -> dict[str, Any]:
    return _payload(
        decision="runtime_executor_intent_hold_no_input",
        reason=f"runtime_executor_intent_error:{type(exc).__name__}",
        intent_ready=False,
        executor_intent="no_op_hold",
        blockers=[f"runtime_executor_intent_error:{type(exc).__name__}"],
    )


def print_runtime_executor_intent_payload(
    payload: dict[str, Any],
    *,
    title: str = "osu! Runtime Executor Intent Preview",
) -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Intent ready: {payload['intent_ready']}")
    print(f"  Executor intent: {payload['executor_intent']}")
    print(f"  Target kind: {payload['target_kind']}")
    print(f"  Controller decision: {payload['controller_decision']}")
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
