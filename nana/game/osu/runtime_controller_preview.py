"""
osu! runtime controller preview - Phase 43.

Classifies the current cached-plan runtime packet into a no-input controller
state. State is in-memory only.
"""

from __future__ import annotations

import copy
import re
import time
from typing import Any, Callable

from nana.game.osu.runtime_packet import build_runtime_packet_payload


RUNTIME_CONTROLLER_SCHEMA = "nana.osu.runtime_controller_preview.v1"
DEFAULT_PREPARE_WINDOW_MS = 160
DEFAULT_FIRE_WINDOW_MS = 80
DEFAULT_LATE_WINDOW_MS = 60
PREPARE_WINDOW_MIN_MS = 0
PREPARE_WINDOW_MAX_MS = 1000
FIRE_WINDOW_MIN_MS = 0
FIRE_WINDOW_MAX_MS = 500
LATE_WINDOW_MIN_MS = 0
LATE_WINDOW_MAX_MS = 500

StateReader = Callable[[], dict[str, Any]]
ActivityReader = Callable[[dict[str, Any] | None], dict[str, Any]]

_LAST_CONTROLLER_PAYLOAD: dict[str, Any] | None = None
_LAST_ACTION_ID: str | None = None


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


def clear_runtime_controller_state() -> None:
    global _LAST_CONTROLLER_PAYLOAD, _LAST_ACTION_ID
    _LAST_CONTROLLER_PAYLOAD = None
    _LAST_ACTION_ID = None


def _copy_payload(payload: dict[str, Any]) -> dict[str, Any]:
    copied = copy.deepcopy(payload)
    copied.update(_base_flags())
    return copied


def _store_payload(payload: dict[str, Any]) -> dict[str, Any]:
    global _LAST_CONTROLLER_PAYLOAD
    safe_payload = _copy_payload(payload)
    _LAST_CONTROLLER_PAYLOAD = _copy_payload(safe_payload)
    return safe_payload


def _parse_int_flag(text: str, name: str, default: int) -> tuple[int, str | None]:
    match = re.search(rf"(?:^|\s){re.escape(name)}=(\S+)(?=\s|$)", text or "")
    if not match:
        return int(default), None
    raw = match.group(1)
    try:
        return int(raw), None
    except ValueError:
        return int(default), f"{name.lstrip('-').replace('-', '_')}_invalid"


def _parse_windows(text: str) -> tuple[dict[str, int], list[str]]:
    prepare_window_ms, prepare_error = _parse_int_flag(
        text, "--prepare-window-ms", DEFAULT_PREPARE_WINDOW_MS
    )
    fire_window_ms, fire_error = _parse_int_flag(text, "--fire-window-ms", DEFAULT_FIRE_WINDOW_MS)
    late_window_ms, late_error = _parse_int_flag(text, "--late-window-ms", DEFAULT_LATE_WINDOW_MS)
    blockers = [error for error in (prepare_error, fire_error, late_error) if error]
    if prepare_window_ms < PREPARE_WINDOW_MIN_MS or prepare_window_ms > PREPARE_WINDOW_MAX_MS:
        blockers.append(f"prepare_window_ms_out_of_bounds:{PREPARE_WINDOW_MIN_MS}..{PREPARE_WINDOW_MAX_MS}")
    if fire_window_ms < FIRE_WINDOW_MIN_MS or fire_window_ms > FIRE_WINDOW_MAX_MS:
        blockers.append(f"fire_window_ms_out_of_bounds:{FIRE_WINDOW_MIN_MS}..{FIRE_WINDOW_MAX_MS}")
    if late_window_ms < LATE_WINDOW_MIN_MS or late_window_ms > LATE_WINDOW_MAX_MS:
        blockers.append(f"late_window_ms_out_of_bounds:{LATE_WINDOW_MIN_MS}..{LATE_WINDOW_MAX_MS}")
    return {
        "prepare_window_ms": prepare_window_ms,
        "fire_window_ms": fire_window_ms,
        "late_window_ms": late_window_ms,
    }, blockers


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _empty_payload(
    *,
    decision: str,
    reason: str,
    controller_decision: str = "hold",
    windows: dict[str, int] | None = None,
    packet: dict[str, Any] | None = None,
    blockers: list[str] | None = None,
    action_changed: bool = False,
    previous_action_id: str | None = None,
) -> dict[str, Any]:
    windows = windows or {
        "prepare_window_ms": DEFAULT_PREPARE_WINDOW_MS,
        "fire_window_ms": DEFAULT_FIRE_WINDOW_MS,
        "late_window_ms": DEFAULT_LATE_WINDOW_MS,
    }
    packet = packet or {}
    payload = {
        "schema": RUNTIME_CONTROLLER_SCHEMA,
        "version": 1,
        "decision": decision,
        "reason": reason,
        "controller_decision": controller_decision,
        "packet_ready": bool(packet.get("packet_ready")),
        "action_changed": bool(action_changed),
        "previous_action_id": previous_action_id,
        "action_id": packet.get("action_id"),
        "action_type": packet.get("action_type"),
        "suggested_intent": packet.get("suggested_intent"),
        "scheduled_t": packet.get("scheduled_t"),
        "current_time_ms": packet.get("current_time_ms"),
        "time_to_action_ms": packet.get("time_to_action_ms"),
        "timing_state": packet.get("timing_state"),
        "prepare_window_ms": int(windows["prepare_window_ms"]),
        "fire_window_ms": int(windows["fire_window_ms"]),
        "late_window_ms": int(windows["late_window_ms"]),
        "cache_identity_status": packet.get("cache_identity_status"),
        "cache_identity_match": packet.get("cache_identity_match"),
        "map_changed": bool(packet.get("map_changed")),
        "lookup_cost_ms": packet.get("lookup_cost_ms"),
        "playfield": packet.get("playfield"),
        "screen": packet.get("screen"),
        "blockers": list(blockers or packet.get("blockers") or []),
        "created_at": time.time(),
        **_base_flags(),
    }
    payload.update(_base_flags())
    return payload


def _is_hard_hold(packet: dict[str, Any]) -> bool:
    reason = str(packet.get("reason") or "")
    blockers = [str(blocker) for blocker in packet.get("blockers") or []]
    if reason in {"no_cached_play_plan", "cached_play_plan_map_mismatch", "current_time_missing"}:
        return True
    if reason.startswith("activity_not_gameplay"):
        return True
    return any(
        blocker in {"no_cached_play_plan", "cached_play_plan_map_mismatch", "current_time_missing"}
        or blocker.startswith("activity_not_gameplay")
        for blocker in blockers
    )


def _classify(time_to_action_ms: int, windows: dict[str, int]) -> str:
    if time_to_action_ms > windows["prepare_window_ms"]:
        return "would_wait"
    if windows["fire_window_ms"] < time_to_action_ms <= windows["prepare_window_ms"]:
        return "would_prepare"
    if -windows["late_window_ms"] <= time_to_action_ms <= windows["fire_window_ms"]:
        return "would_fire_preview_only"
    return "would_skip_late"


def build_runtime_controller_preview_payload(
    text: str = "",
    *,
    bridge_state: dict[str, Any] | None = None,
    state_reader: StateReader | None = None,
    activity_reader: ActivityReader | None = None,
) -> dict[str, Any]:
    global _LAST_ACTION_ID
    windows, param_blockers = _parse_windows(text)
    if param_blockers:
        return _store_payload(
            _empty_payload(
                decision="runtime_controller_hold_no_input",
                reason="invalid_controller_params",
                windows=windows,
                blockers=param_blockers,
            )
        )

    packet = build_runtime_packet_payload(
        text,
        bridge_state=bridge_state,
        state_reader=state_reader,
        activity_reader=activity_reader,
    )
    if _is_hard_hold(packet):
        blockers = list(packet.get("blockers") or [])
        reason = str(packet.get("reason") or (blockers[0] if blockers else "packet_hold"))
        return _store_payload(
            _empty_payload(
                decision="runtime_controller_hold_no_input",
                reason=reason,
                windows=windows,
                packet=packet,
                blockers=blockers or [reason],
            )
        )
    if packet.get("packet_ready") is not True:
        blockers = list(packet.get("blockers") or [])
        return _store_payload(
            _empty_payload(
                decision="runtime_controller_hold_no_input",
                reason=str(packet.get("reason") or "packet_not_ready"),
                windows=windows,
                packet=packet,
                blockers=blockers or ["packet_not_ready"],
            )
        )

    time_to_action_ms = _as_int(packet.get("time_to_action_ms"))
    if time_to_action_ms is None:
        return _store_payload(
            _empty_payload(
                decision="runtime_controller_hold_no_input",
                reason="time_to_action_missing",
                windows=windows,
                packet=packet,
                blockers=["time_to_action_missing"],
            )
        )

    current_action_id = str(packet.get("action_id")) if packet.get("action_id") is not None else None
    previous_action_id = _LAST_ACTION_ID
    action_changed = previous_action_id is not None and current_action_id != previous_action_id
    _LAST_ACTION_ID = current_action_id
    controller_decision = _classify(time_to_action_ms, windows)
    return _store_payload(
        _empty_payload(
            decision="runtime_controller_preview_only_no_input",
            reason="runtime_packet_classified",
            controller_decision=controller_decision,
            windows=windows,
            packet=packet,
            blockers=[],
            action_changed=action_changed,
            previous_action_id=previous_action_id,
        )
    )


def build_runtime_controller_status_payload(text: str = "") -> dict[str, Any]:
    if _LAST_CONTROLLER_PAYLOAD is not None:
        payload = _copy_payload(_LAST_CONTROLLER_PAYLOAD)
        payload["decision"] = "runtime_controller_status_only_no_input"
        payload["reason"] = "last_runtime_controller_preview"
        payload.update(_base_flags())
        return payload
    return _empty_payload(
        decision="runtime_controller_status_only_no_input",
        reason="no_runtime_controller_preview",
        blockers=["no_runtime_controller_preview"],
    )


def build_runtime_controller_error_payload(exc: Exception) -> dict[str, Any]:
    return _empty_payload(
        decision="runtime_controller_hold_no_input",
        reason=f"runtime_controller_error:{type(exc).__name__}",
        blockers=[f"runtime_controller_error:{type(exc).__name__}"],
    )


def print_runtime_controller_payload(
    payload: dict[str, Any],
    *,
    title: str = "osu! Runtime Controller Preview",
) -> None:
    print(title)
    print(f"  Decision: {payload['decision']}")
    print(f"  Controller decision: {payload['controller_decision']}")
    print(f"  Packet ready: {payload['packet_ready']}")
    print(f"  Action changed: {payload['action_changed']}")
    print(f"  Action id: {payload.get('action_id')}")
    print(f"  Action type: {payload.get('action_type')}")
    print(f"  Time to action ms: {payload.get('time_to_action_ms')}")
    print(f"  Timing state: {payload.get('timing_state')}")
    print(f"  Identity status: {payload.get('cache_identity_status')}")
    print(f"  Lookup cost ms: {payload.get('lookup_cost_ms')}")
    print(f"  Blockers: {payload['blockers']}")
    print(f"  Dispatch: {payload['dispatch']}")
    print(f"  Armed: {payload['armed']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
