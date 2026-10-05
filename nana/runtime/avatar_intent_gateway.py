"""Semantic avatar intent gateway for Nana's external character runtime.

The core emits small, typed intents such as ``nod`` or ``happy``.  It never
emits bone paths, frame data, or arbitrary animation names.  A runtime such as
Unity owns blending, masking, tracking, and the actual pose.

The gateway is deliberately disabled by default.  In its default recording
mode it is useful for offline rehearsal and protocol tests without opening a
socket or calling an avatar application.
"""

from __future__ import annotations

import copy
import hmac
import http.server
import json
import math
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from nana.runtime.avatar_reaction_policy import (
    OWNER_DISABLED_ACTIONS, REPLY_ACTIONS, REPLY_COOLDOWN_SECONDS,
    action_block_reason, select_reply_cue,
)
from nana.runtime.stream_public_visual_signals import (
    PROTOCOL_NAME as PUBLIC_VISUAL_PROTOCOL_NAME,
    STALE_AFTER_MS as PUBLIC_VISUAL_STALE_AFTER_MS,
)


PROTOCOL_NAME = "nana.avatar.v1"
PHASE = "AVATAR-GATEWAY-v1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
DEFAULT_PENDING_LIMIT = 1
MAX_DURATION_SECONDS = 60.0
MAX_METADATA_ITEMS = 12
MAX_METADATA_STRING = 160
MAX_HTTP_BODY_BYTES = 16 * 1024
MAX_COMMAND_HISTORY = 64
PUBLIC_VISUAL_HOST = "127.0.0.1"
PUBLIC_VISUAL_PORT = 8766
PUBLIC_VISUAL_PATH = "/v1/avatar/public-signals"
PUBLIC_VISUAL_ALLOWED_ORIGIN = "http://127.0.0.1:5174"
MAX_PUBLIC_VISUAL_TARGET_CHARS = 512
MAX_PUBLIC_VISUAL_RESPONSE_BYTES = 16 * 1024
LOOK_MAX_YAW_DEGREES = 30.0
LOOK_MAX_PITCH_DEGREES = 15.0
LOOK_MAX_ROLL_DEGREES = 12.0
LOOK_DEFAULT_MOVE_SECONDS = 0.8
LOOK_DEFAULT_HOLD_SECONDS = 0.7
LOOK_DEFAULT_RETURN_SECONDS = 0.8
LOOK_MAX_MOVE_SECONDS = 3.0
LOOK_MAX_HOLD_SECONDS = 5.0
LOOK_MAX_RETURN_SECONDS = 3.0
LOOK_COMMAND_MAX_AGE_SECONDS = 8.0


class AvatarIntentError(ValueError):
    """Raised when an untrusted intent payload fails validation."""


class IntentKind(str, Enum):
    AMBIENT = "ambient"
    EXPRESSION = "expression"
    GESTURE = "gesture"
    STATE = "state"
    FX = "fx"


class InterruptPolicy(str, Enum):
    QUEUE = "queue"
    REPLACE = "replace"
    DROP = "drop"


class ReceiptStatus(str, Enum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    SENT = "sent"
    STARTED = "started"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass(frozen=True)
class AvatarLookTarget:
    """Absolute, bounded head target in degrees relative to neutral."""

    yaw: float
    pitch: float
    roll: float

    def to_dict(self) -> dict[str, float]:
        return {
            "yaw": self.yaw,
            "pitch": self.pitch,
            "roll": self.roll,
        }


@dataclass(frozen=True)
class AvatarActionSpec:
    kind: IntentKind
    mask: str
    default_duration_s: float
    default_priority: int
    default_interrupt_policy: InterruptPolicy


# This is the public semantic vocabulary.  Adding an action is an explicit
# contract change: the Warudo blueprint must know how to map it.
ACTION_SPECS: dict[str, AvatarActionSpec] = {
    "idle": AvatarActionSpec(IntentKind.AMBIENT, "ambient", 0.0, 0, InterruptPolicy.REPLACE),
    "wave": AvatarActionSpec(IntentKind.GESTURE, "upper_body", 2.8, 50, InterruptPolicy.QUEUE),
    "think": AvatarActionSpec(IntentKind.STATE, "upper_body", 4.0, 60, InterruptPolicy.REPLACE),
    "listen": AvatarActionSpec(IntentKind.STATE, "head", 4.0, 40, InterruptPolicy.REPLACE),
    "nod": AvatarActionSpec(IntentKind.GESTURE, "head", 1.2, 35, InterruptPolicy.QUEUE),
    "look": AvatarActionSpec(IntentKind.STATE, "head", 2.3, 45, InterruptPolicy.REPLACE),
    "happy": AvatarActionSpec(IntentKind.EXPRESSION, "face", 2.0, 30, InterruptPolicy.REPLACE),
    "surprised": AvatarActionSpec(IntentKind.EXPRESSION, "face", 1.6, 55, InterruptPolicy.REPLACE),
    "blink": AvatarActionSpec(IntentKind.EXPRESSION, "face", 0.35, 10, InterruptPolicy.DROP),
    "cheek_fx": AvatarActionSpec(IntentKind.FX, "fx", 2.0, 20, InterruptPolicy.REPLACE),
    "dance": AvatarActionSpec(IntentKind.GESTURE, "full_body", 5.0, 80, InterruptPolicy.REPLACE),
    "curious": AvatarActionSpec(IntentKind.STATE, "head", 3.2, 45, InterruptPolicy.REPLACE),
    "shy": AvatarActionSpec(IntentKind.STATE, "head", 3.5, 45, InterruptPolicy.REPLACE),
    "playful": AvatarActionSpec(IntentKind.STATE, "head", 3.0, 45, InterruptPolicy.REPLACE),
    "shy_smile": AvatarActionSpec(IntentKind.STATE, "upper_body", 5.5, 65, InterruptPolicy.REPLACE),
    "settle": AvatarActionSpec(IntentKind.STATE, "full_body", 1.2, 100, InterruptPolicy.REPLACE),
    # Face is required; optional head accents are borrowed only while the
    # runtime's head owner is free, and a look/body intent can take them away.
    "wink_soft_smile": AvatarActionSpec(IntentKind.EXPRESSION, "face", 2.20, 30, InterruptPolicy.REPLACE),
    "heart_happy": AvatarActionSpec(IntentKind.EXPRESSION, "face", 2.52, 30, InterruptPolicy.REPLACE),
    "surprised_pout": AvatarActionSpec(IntentKind.EXPRESSION, "face", 1.92, 30, InterruptPolicy.REPLACE),
    "serious_think": AvatarActionSpec(IntentKind.EXPRESSION, "face", 2.55, 30, InterruptPolicy.REPLACE),
    "cat_teary_smile": AvatarActionSpec(IntentKind.EXPRESSION, "face", 2.75, 30, InterruptPolicy.REPLACE),
    "shy_crying": AvatarActionSpec(IntentKind.EXPRESSION, "face", 2.80, 30, InterruptPolicy.REPLACE),
    "playful_wink": AvatarActionSpec(IntentKind.EXPRESSION, "face", 2.26, 30, InterruptPolicy.REPLACE),
}

FACE_PRESET_ACTIONS = frozenset({
    "wink_soft_smile", "heart_happy", "surprised_pout", "serious_think",
    "cat_teary_smile", "shy_crying", "playful_wink",
})

# Public event ingress is intentionally sparse.  A normal viewer message does
# not animate the character; only events with a clear stage cue are mapped.
EVENT_ACTIONS: dict[str, str] = {
    "emoji_only": "happy",
    "emoji": "happy",
    "highlight": "wink_soft_smile",
    "subscription": "wink_soft_smile",
    "auto_starter": "listen",
    "starter": "listen",
    "mention": "listen",
}

_ALLOWED_CHANNELS = {"private", "public", "owner", "system"}
_ALLOWED_INTENT_FIELDS = {
    "protocol",
    "type",
    "intent_id",
    "id",
    "action",
    "kind",
    "mask",
    "priority",
    "duration_s",
    "interrupt_policy",
    "source",
    "channel",
    "correlation_id",
    "metadata",
    "target",
    "move_s",
    "hold_s",
    "return_s",
    "return_to",
}
_ALLOWED_RUNTIME_ACKS = {
    ReceiptStatus.STARTED.value,
    ReceiptStatus.FINISHED.value,
    ReceiptStatus.CANCELLED.value,
    ReceiptStatus.FAILED.value,
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return value if math.isfinite(value) else default


def _bounded_text(value: Any, *, field_name: str, maximum: int) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise AvatarIntentError(f"{field_name}_too_long")
    return text


def _safe_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise AvatarIntentError("metadata_must_be_object")
    if len(value) > MAX_METADATA_ITEMS:
        raise AvatarIntentError("metadata_too_many_items")

    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = _bounded_text(raw_key, field_name="metadata_key", maximum=48)
        if not key or not all(ch.isalnum() or ch in "_-" for ch in key):
            raise AvatarIntentError("metadata_key_invalid")
        if raw_value is None or isinstance(raw_value, bool):
            result[key] = raw_value
        elif isinstance(raw_value, (int, float)):
            if isinstance(raw_value, float) and not math.isfinite(raw_value):
                raise AvatarIntentError("metadata_number_invalid")
            result[key] = raw_value
        elif isinstance(raw_value, str):
            result[key] = raw_value[:MAX_METADATA_STRING]
        else:
            raise AvatarIntentError("metadata_value_unsupported")
    return result


def _coerce_int(value: Any, *, field_name: str, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise AvatarIntentError(f"{field_name}_must_be_integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AvatarIntentError(f"{field_name}_must_be_integer") from exc
    if parsed < low or parsed > high:
        raise AvatarIntentError(f"{field_name}_out_of_range")
    return parsed


def _coerce_float(value: Any, *, field_name: str, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise AvatarIntentError(f"{field_name}_must_be_number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AvatarIntentError(f"{field_name}_must_be_number") from exc
    if not math.isfinite(parsed) or parsed < low or parsed > high:
        raise AvatarIntentError(f"{field_name}_out_of_range")
    return parsed


def _parse_look_target(value: Any) -> AvatarLookTarget:
    if not isinstance(value, dict):
        raise AvatarIntentError("look_target_must_be_object")
    unknown_fields = set(value).difference({"yaw", "pitch", "roll"})
    if unknown_fields:
        raise AvatarIntentError("look_target_fields_not_allowed")
    return AvatarLookTarget(
        yaw=_coerce_float(
            value.get("yaw", 0.0),
            field_name="target_yaw",
            low=-LOOK_MAX_YAW_DEGREES,
            high=LOOK_MAX_YAW_DEGREES,
        ),
        pitch=_coerce_float(
            value.get("pitch", 0.0),
            field_name="target_pitch",
            low=-LOOK_MAX_PITCH_DEGREES,
            high=LOOK_MAX_PITCH_DEGREES,
        ),
        roll=_coerce_float(
            value.get("roll", 0.0),
            field_name="target_roll",
            low=-LOOK_MAX_ROLL_DEGREES,
            high=LOOK_MAX_ROLL_DEGREES,
        ),
    )


@dataclass(frozen=True)
class AvatarIntent:
    intent_id: str
    action: str
    kind: str
    mask: str
    priority: int
    duration_s: float
    interrupt_policy: str
    source: str
    channel: str
    correlation_id: str
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)
    target: AvatarLookTarget | None = None
    move_s: float = 0.0
    hold_s: float = 0.0
    return_s: float = 0.0
    return_to: str = "none"

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AvatarIntent":
        if not isinstance(payload, dict):
            raise AvatarIntentError("intent_must_be_object")

        unknown_fields = set(payload).difference(_ALLOWED_INTENT_FIELDS)
        if unknown_fields:
            raise AvatarIntentError("intent_fields_not_allowed")

        protocol = payload.get("protocol")
        if protocol not in (None, "", PROTOCOL_NAME):
            raise AvatarIntentError("protocol_mismatch")
        message_type = payload.get("type")
        if message_type not in (None, "", "intent", "nana_avatar_intent"):
            raise AvatarIntentError("message_type_invalid")

        action = str(payload.get("action") or "").strip().lower()
        spec = ACTION_SPECS.get(action)
        if spec is None:
            raise AvatarIntentError("action_not_allowed")

        kind = str(payload.get("kind") or spec.kind.value).strip().lower()
        mask = str(payload.get("mask") or spec.mask).strip().lower()
        if kind != spec.kind.value or mask != spec.mask:
            raise AvatarIntentError("action_contract_mismatch")

        raw_id = payload.get("intent_id", payload.get("id"))
        intent_id = _bounded_text(raw_id, field_name="intent_id", maximum=96) if raw_id else uuid.uuid4().hex
        if not intent_id:
            raise AvatarIntentError("intent_id_empty")

        priority = (
            spec.default_priority
            if payload.get("priority") is None
            else _coerce_int(payload.get("priority"), field_name="priority", low=0, high=100)
        )
        duration_s = (
            spec.default_duration_s
            if payload.get("duration_s") is None
            else _coerce_float(
                payload.get("duration_s"),
                field_name="duration_s",
                low=0.0,
                high=MAX_DURATION_SECONDS,
            )
        )
        if action in FACE_PRESET_ACTIONS:
            # These presets auto-return. Zero is not an indefinite hold, and a
            # longer request must not leave an arbiter slot alive after playback.
            duration_s = min(duration_s, spec.default_duration_s) if duration_s > 0 else spec.default_duration_s
        interrupt_policy = str(
            payload.get("interrupt_policy") or spec.default_interrupt_policy.value
        ).strip().lower()
        if interrupt_policy not in {item.value for item in InterruptPolicy}:
            raise AvatarIntentError("interrupt_policy_invalid")

        source = _bounded_text(payload.get("source") or "nana", field_name="source", maximum=64)
        channel = str(payload.get("channel") or "private").strip().lower()
        if channel not in _ALLOWED_CHANNELS:
            raise AvatarIntentError("channel_invalid")
        correlation_id = _bounded_text(
            payload.get("correlation_id") or "",
            field_name="correlation_id",
            maximum=96,
        )

        look_fields_present = any(
            field_name in payload
            for field_name in ("target", "move_s", "hold_s", "return_s", "return_to")
        )
        target: AvatarLookTarget | None = None
        move_s = 0.0
        hold_s = 0.0
        return_s = 0.0
        return_to = "none"
        if action == "look":
            target = _parse_look_target(payload.get("target"))
            move_s = (
                LOOK_DEFAULT_MOVE_SECONDS
                if payload.get("move_s") is None
                else _coerce_float(
                    payload.get("move_s"),
                    field_name="move_s",
                    low=0.1,
                    high=LOOK_MAX_MOVE_SECONDS,
                )
            )
            hold_s = (
                LOOK_DEFAULT_HOLD_SECONDS
                if payload.get("hold_s") is None
                else _coerce_float(
                    payload.get("hold_s"),
                    field_name="hold_s",
                    low=0.0,
                    high=LOOK_MAX_HOLD_SECONDS,
                )
            )
            return_to = str(payload.get("return_to") or "neutral").strip().lower()
            if return_to not in {"neutral", "hold"}:
                raise AvatarIntentError("return_to_invalid")
            return_s = (
                0.0
                if return_to == "hold"
                else (
                    LOOK_DEFAULT_RETURN_SECONDS
                    if payload.get("return_s") is None
                    else _coerce_float(
                        payload.get("return_s"),
                        field_name="return_s",
                        low=0.1,
                        high=LOOK_MAX_RETURN_SECONDS,
                    )
                )
            )
            phase_duration = move_s + hold_s + return_s
            if payload.get("duration_s") is None:
                duration_s = phase_duration
            else:
                if duration_s + 1e-6 < phase_duration:
                    raise AvatarIntentError("look_duration_shorter_than_phases")
        elif look_fields_present:
            raise AvatarIntentError("look_fields_only_valid_for_look")

        return cls(
            intent_id=intent_id,
            action=action,
            kind=kind,
            mask=mask,
            priority=priority,
            duration_s=duration_s,
            interrupt_policy=interrupt_policy,
            source=source or "nana",
            channel=channel,
            correlation_id=correlation_id,
            created_at=time.time(),
            metadata=_safe_metadata(payload.get("metadata")),
            target=target,
            move_s=move_s,
            hold_s=hold_s,
            return_s=return_s,
            return_to=return_to,
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        if self.target is None:
            for key in ("target", "move_s", "hold_s", "return_s", "return_to"):
                result.pop(key, None)
        return result

    def to_wire(self) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_NAME,
            "type": "nana_avatar_intent",
            "intent": self.to_dict(),
        }


@dataclass(frozen=True)
class AvatarReceipt:
    intent_id: str
    status: str
    reason: str
    action: str
    kind: str
    mask: str
    priority: int
    source: str
    channel: str
    correlation_id: str
    created_at: float
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def masks_conflict(left: str, right: str) -> bool:
    """Return whether two semantic masks may fight over the same runtime pose."""
    a = str(left or "").strip().lower()
    b = str(right or "").strip().lower()
    if not a or not b:
        return True
    if a == "ambient" or b == "ambient":
        return False
    if "full_body" in {a, b}:
        return True
    if a == b:
        return True
    # A conservative rule: upper-body events include the head unless a runtime
    # adapter explicitly narrows the mask in its own blueprint.
    if {a, b} == {"upper_body", "head"}:
        return True
    return False


def action_for_event(event: str) -> str | None:
    """Map a normalized external event to an allowlisted semantic action."""
    return EVENT_ACTIONS.get(str(event or "").strip().lower())


LOOK_PRESETS: dict[str, dict[str, float]] = {
    "left": {"yaw": -24.0, "pitch": -3.0, "roll": 2.0},
    "right": {"yaw": 24.0, "pitch": -2.0, "roll": -2.0},
    "center": {"yaw": 0.0, "pitch": 0.0, "roll": 0.0},
    "up": {"yaw": 0.0, "pitch": -8.0, "roll": 0.0},
    "down": {"yaw": 0.0, "pitch": 7.0, "roll": 0.0},
}


def look_payload_for_preset(
    preset: str,
    *,
    move_s: float = LOOK_DEFAULT_MOVE_SECONDS,
    hold_s: float = LOOK_DEFAULT_HOLD_SECONDS,
    return_s: float = LOOK_DEFAULT_RETURN_SECONDS,
    return_to: str = "neutral",
) -> dict[str, Any]:
    """Build a bounded target command for an operator-friendly preset."""
    key = str(preset or "").strip().lower().replace("-", "_")
    aliases = {"centre": "center", "neutral": "center"}
    key = aliases.get(key, key)
    target = LOOK_PRESETS.get(key)
    if target is None:
        raise AvatarIntentError("look_preset_invalid")
    return {
        "action": "look",
        "target": dict(target),
        "move_s": move_s,
        "hold_s": hold_s,
        "return_s": return_s,
        "return_to": return_to,
    }


@dataclass
class _ActiveIntent:
    intent: AvatarIntent
    dispatched: bool = False
    sent_at: float = 0.0
    started_at: float = 0.0


class AvatarIntentArbiter:
    """Thread-safe priority/mask arbiter with one bounded pending item."""

    def __init__(self, *, pending_limit: int = DEFAULT_PENDING_LIMIT) -> None:
        self.pending_limit = max(0, int(pending_limit))
        self._lock = threading.RLock()
        self._active: dict[str, _ActiveIntent] = {}
        self._pending: list[AvatarIntent] = []
        self._receipts: dict[str, AvatarReceipt] = {}
        self._history: list[AvatarReceipt] = []

    def _make_receipt(
        self,
        intent: AvatarIntent,
        status: ReceiptStatus | str,
        reason: str,
        *,
        now: float | None = None,
    ) -> AvatarReceipt:
        stamp = time.time() if now is None else float(now)
        previous = self._receipts.get(intent.intent_id)
        return AvatarReceipt(
            intent_id=intent.intent_id,
            status=status.value if isinstance(status, ReceiptStatus) else str(status),
            reason=str(reason),
            action=intent.action,
            kind=intent.kind,
            mask=intent.mask,
            priority=intent.priority,
            source=intent.source,
            channel=intent.channel,
            correlation_id=intent.correlation_id,
            created_at=previous.created_at if previous is not None else stamp,
            updated_at=stamp,
        )

    def _record_locked(self, receipt: AvatarReceipt) -> AvatarReceipt:
        self._receipts[receipt.intent_id] = receipt
        self._history.append(receipt)
        if len(self._history) > 100:
            del self._history[:-100]
        return receipt

    def _set_status_locked(
        self,
        intent: AvatarIntent,
        status: ReceiptStatus | str,
        reason: str,
    ) -> AvatarReceipt:
        return self._record_locked(self._make_receipt(intent, status, reason))

    def _conflicts_locked(self, intent: AvatarIntent) -> list[_ActiveIntent]:
        return [
            active
            for active in self._active.values()
            if masks_conflict(active.intent.mask, intent.mask)
        ]

    def submit(self, intent: AvatarIntent) -> AvatarReceipt:
        with self._lock:
            self.tick()
            if intent.intent_id in self._receipts:
                return self._set_status_locked(intent, ReceiptStatus.REJECTED, "duplicate_intent_id")

            conflicts = self._conflicts_locked(intent)
            can_replace = (
                intent.interrupt_policy == InterruptPolicy.REPLACE.value
                and (not conflicts or intent.priority >= max(item.intent.priority for item in conflicts))
            )
            if intent.action == "settle" and (not conflicts or can_replace):
                for pending in self._pending:
                    self._set_status_locked(pending, ReceiptStatus.CANCELLED, "settled_by_owner")
                self._pending.clear()
            if not conflicts:
                self._active[intent.intent_id] = _ActiveIntent(intent)
                return self._set_status_locked(intent, ReceiptStatus.ACCEPTED, "active_slot_available")

            if (
                intent.interrupt_policy == InterruptPolicy.REPLACE.value
                and intent.priority >= max(item.intent.priority for item in conflicts)
            ):
                for active in conflicts:
                    self._active.pop(active.intent.intent_id, None)
                    self._set_status_locked(
                        active.intent,
                        ReceiptStatus.CANCELLED,
                        f"replaced_by:{intent.intent_id}",
                    )
                self._active[intent.intent_id] = _ActiveIntent(intent)
                return self._set_status_locked(intent, ReceiptStatus.ACCEPTED, "replaced_lower_priority_active")

            if intent.interrupt_policy == InterruptPolicy.DROP.value:
                return self._set_status_locked(intent, ReceiptStatus.REJECTED, "conflicting_active_drop_policy")

            if len(self._pending) < self.pending_limit:
                self._pending.append(intent)
                self._pending.sort(key=lambda item: (item.priority, item.created_at), reverse=True)
                return self._set_status_locked(intent, ReceiptStatus.QUEUED, "conflicting_active_queued")

            if self._pending and intent.priority > self._pending[-1].priority:
                displaced = self._pending.pop()
                self._set_status_locked(displaced, ReceiptStatus.CANCELLED, f"preempted_by:{intent.intent_id}")
                self._pending.append(intent)
                self._pending.sort(key=lambda item: (item.priority, item.created_at), reverse=True)
                return self._set_status_locked(intent, ReceiptStatus.QUEUED, "higher_priority_pending_replacement")

            return self._set_status_locked(intent, ReceiptStatus.REJECTED, "pending_queue_full")

    def claim_dispatchable(self) -> list[AvatarIntent]:
        with self._lock:
            self.tick()
            claimed: list[AvatarIntent] = []
            for active in self._active.values():
                if active.dispatched:
                    continue
                active.dispatched = True
                claimed.append(active.intent)
            return sorted(claimed, key=lambda item: item.priority, reverse=True)

    def mark_sent(self, intent_id: str, *, ok: bool, reason: str = "transport_sent") -> AvatarReceipt | None:
        with self._lock:
            active = self._active.get(intent_id)
            if active is None:
                return self._receipts.get(intent_id)
            if not ok:
                self._active.pop(intent_id, None)
                receipt = self._set_status_locked(active.intent, ReceiptStatus.FAILED, reason)
                self._promote_pending_locked()
                return receipt
            active.sent_at = time.monotonic()
            current = self._receipts.get(intent_id)
            if current is not None and current.status in {
                ReceiptStatus.STARTED.value,
                ReceiptStatus.FINISHED.value,
            }:
                return current
            return self._set_status_locked(active.intent, ReceiptStatus.SENT, reason)

    def acknowledge(self, intent_id: str, status: str, *, reason: str = "runtime_ack") -> AvatarReceipt | None:
        normalized = str(status or "").strip().lower()
        if normalized not in _ALLOWED_RUNTIME_ACKS:
            raise AvatarIntentError("runtime_ack_status_invalid")
        with self._lock:
            active = self._active.get(intent_id)
            if active is None:
                return None
            if normalized == ReceiptStatus.STARTED.value:
                if active.sent_at <= 0.0:
                    active.sent_at = time.monotonic()
                active.started_at = time.monotonic()
                return self._set_status_locked(active.intent, ReceiptStatus.STARTED, reason)
            if normalized == ReceiptStatus.FINISHED.value:
                self._active.pop(intent_id, None)
                receipt = self._set_status_locked(active.intent, ReceiptStatus.FINISHED, reason)
                self._promote_pending_locked()
                return receipt
            if normalized == ReceiptStatus.CANCELLED.value:
                self._active.pop(intent_id, None)
                receipt = self._set_status_locked(active.intent, ReceiptStatus.CANCELLED, reason)
                self._promote_pending_locked()
                return receipt
            self._active.pop(intent_id, None)
            receipt = self._set_status_locked(active.intent, ReceiptStatus.FAILED, reason)
            self._promote_pending_locked()
            return receipt

    def cancel(self, intent_id: str, *, reason: str = "cancelled_by_owner") -> AvatarReceipt | None:
        with self._lock:
            active = self._active.pop(intent_id, None)
            if active is not None:
                receipt = self._set_status_locked(active.intent, ReceiptStatus.CANCELLED, reason)
                self._promote_pending_locked()
                return receipt
            for index, pending in enumerate(self._pending):
                if pending.intent_id == intent_id:
                    self._pending.pop(index)
                    return self._set_status_locked(pending, ReceiptStatus.CANCELLED, reason)
            return self._receipts.get(intent_id)

    def tick(self, *, now: float | None = None) -> None:
        monotonic_now = time.monotonic() if now is None else float(now)
        with self._lock:
            expired: list[_ActiveIntent] = []
            for active in self._active.values():
                if active.intent.duration_s <= 0.0 or active.sent_at <= 0.0:
                    continue
                base = active.started_at or active.sent_at
                # Let a renderer's final frame and HTTP receipt arrive before the watchdog.
                grace = 0.75 if active.started_at else 0.0
                if monotonic_now - base >= active.intent.duration_s + grace:
                    expired.append(active)
            for active in expired:
                self._active.pop(active.intent.intent_id, None)
                self._set_status_locked(
                    active.intent,
                    ReceiptStatus.FINISHED,
                    "duration_elapsed_without_runtime_ack",
                )
            if expired:
                self._promote_pending_locked()

    def _promote_pending_locked(self) -> None:
        while self._pending:
            candidate_index = next(
                (
                    index
                    for index, candidate in enumerate(self._pending)
                    if not self._conflicts_locked(candidate)
                ),
                None,
            )
            if candidate_index is None:
                return
            candidate = self._pending.pop(candidate_index)
            self._active[candidate.intent_id] = _ActiveIntent(candidate)
            self._set_status_locked(candidate, ReceiptStatus.ACCEPTED, "promoted_from_pending")

    def receipt(self, intent_id: str) -> AvatarReceipt | None:
        with self._lock:
            return self._receipts.get(str(intent_id))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self.tick()
            active = []
            for item in self._active.values():
                receipt = self._receipts.get(item.intent.intent_id)
                active.append({
                    "intent": item.intent.to_dict(),
                    "dispatched": item.dispatched,
                    "receipt": receipt.to_dict() if receipt else None,
                })
            pending = []
            for item in self._pending:
                receipt = self._receipts.get(item.intent_id)
                pending.append({"intent": item.to_dict(), "receipt": receipt.to_dict() if receipt else None})
            last = self._history[-1].to_dict() if self._history else None
            recent, seen = [], set()
            for receipt in reversed(self._history):
                if receipt.intent_id not in seen:
                    recent.append(receipt.to_dict())
                    seen.add(receipt.intent_id)
                    if len(recent) == 5:
                        break
            return {
                "active": active,
                "pending": pending,
                "pending_limit": self.pending_limit,
                "last_receipt": last,
                "recent_receipts": recent,
                "history_count": len(self._history),
            }


@dataclass(frozen=True)
class TransportResult:
    ok: bool
    reason: str


class AvatarTransport(Protocol):
    name: str

    @property
    def connected(self) -> bool: ...

    def send(self, payload: dict[str, Any]) -> TransportResult: ...

    def close(self) -> None: ...


class RecordingTransport:
    """Offline transport used by default and by smoke tests."""

    name = "recording"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._payloads: list[dict[str, Any]] = []

    @property
    def connected(self) -> bool:
        return True

    def send(self, payload: dict[str, Any]) -> TransportResult:
        with self._lock:
            self._payloads.append(copy.deepcopy(payload))
        return TransportResult(True, "recorded")

    def close(self) -> None:
        return None

    def payloads(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._payloads)


class UnavailableTransport:
    name = "unavailable"

    @property
    def connected(self) -> bool:
        return False

    def send(self, payload: dict[str, Any]) -> TransportResult:
        return TransportResult(False, "transport_unavailable")

    def close(self) -> None:
        return None


class WarudoWebSocketTransport:
    """Small persistent WebSocket client for a Warudo message receiver.

    Warudo's blueprint remains responsible for parsing the semantic envelope;
    this class only delivers JSON text.  It is never constructed unless the
    transport mode and endpoint are explicitly configured.
    """

    name = "warudo_websocket"

    def __init__(self, url: str, *, timeout_s: float = 2.0) -> None:
        self.url = str(url).strip()
        self.timeout_s = max(0.2, float(timeout_s))
        self._lock = threading.RLock()
        self._socket: Any = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._socket is not None

    def _connect_locked(self) -> Any:
        if self._socket is not None:
            return self._socket
        if not self.url:
            raise RuntimeError("warudo_websocket_url_missing")
        try:
            import websocket
        except ImportError as exc:
            raise RuntimeError("websocket_client_not_installed") from exc
        self._socket = websocket.create_connection(
            self.url,
            timeout=self.timeout_s,
            enable_multithread=True,
            http_no_proxy=["127.0.0.1", "localhost"],
        )
        return self._socket

    def send(self, payload: dict[str, Any]) -> TransportResult:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            try:
                socket = self._connect_locked()
                socket.send(encoded)
                return TransportResult(True, "sent")
            except Exception as exc:
                self.close()
                return TransportResult(False, f"websocket_send_failed:{type(exc).__name__}")

    def close(self) -> None:
        with self._lock:
            socket = self._socket
            self._socket = None
            if socket is not None:
                try:
                    socket.close()
                except Exception:
                    pass


def _build_transport() -> AvatarTransport:
    mode = str(os.getenv("NANA_AVATAR_GATEWAY_TRANSPORT", "recording") or "recording").strip().lower()
    if mode in {"warudo", "warudo_ws", "websocket"}:
        url = str(os.getenv("NANA_WARUDO_WS_URL", "") or "").strip()
        if not url:
            return UnavailableTransport()
        return WarudoWebSocketTransport(
            url,
            timeout_s=_env_float("NANA_AVATAR_GATEWAY_TIMEOUT_S", 2.0),
        )
    return RecordingTransport()


@dataclass(frozen=True)
class GatewayResult:
    ok: bool
    status_code: int
    reason: str
    intent: AvatarIntent | None = None
    receipt: AvatarReceipt | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status_code": self.status_code,
            "reason": self.reason,
            "intent": self.intent.to_dict() if self.intent else None,
            "receipt": self.receipt.to_dict() if self.receipt else None,
        }


class AvatarIntentGateway:
    """Local gateway that arbitrates semantic intents and delivers them."""

    _instance: "AvatarIntentGateway | None" = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
        transport: AvatarTransport | None = None,
        pending_limit: int | None = None,
    ) -> None:
        self.enabled = (
            _env_bool("NANA_AVATAR_GATEWAY_ENABLED", False)
            if enabled is None
            else bool(enabled)
        )
        self.auto_events_enabled = _env_bool(
            "NANA_AVATAR_GATEWAY_AUTO_EVENTS_ENABLED",
            False,
        )
        self.reply_reactions_enabled = _env_bool("NANA_AVATAR_REPLY_EVENTS_ENABLED", True)
        self._reply_last_at = None
        self._reply_last_key = ""
        self._reply_last = {"action": "", "reason": "not_requested"}
        self._reply_last_sent = None
        self.host = str(host or os.getenv("NANA_AVATAR_GATEWAY_HOST", DEFAULT_HOST)).strip() or DEFAULT_HOST
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("avatar_gateway_host_must_be_loopback")
        self.port = (
            _env_int("NANA_AVATAR_GATEWAY_PORT", DEFAULT_PORT, 0, 65535)
            if port is None
            else int(port)
        )
        if self.port < 0 or self.port > 65535:
            raise ValueError("avatar_gateway_port_out_of_range")
        self.token = str(token if token is not None else os.getenv("NANA_AVATAR_GATEWAY_TOKEN", ""))
        self.transport = transport or _build_transport()
        self.arbiter = AvatarIntentArbiter(
            pending_limit=(
                _env_int("NANA_AVATAR_GATEWAY_PENDING_LIMIT", DEFAULT_PENDING_LIMIT, 0, 8)
                if pending_limit is None
                else int(pending_limit)
            )
        )
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._server: http.server.ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._stats = {
            "submitted": 0,
            "accepted": 0,
            "queued": 0,
            "rejected": 0,
            "sent": 0,
            "transport_failed": 0,
            "runtime_acks": 0,
        }
        self._command_history: list[tuple[int, float, AvatarIntent]] = []
        self._command_session = uuid.uuid4().hex
        from nana.runtime.avatar_mouth_stream import get_avatar_mouth_stream

        self.mouth_stream = get_avatar_mouth_stream()
        self._command_cursor = 0
        self._last_error = ""

    @classmethod
    def get_instance(cls) -> "AvatarIntentGateway":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_test(cls) -> None:
        with cls._instance_lock:
            instance = cls._instance
            cls._instance = None
        if instance is not None:
            instance.stop()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._server is not None or (
                self._worker_thread is not None and self._worker_thread.is_alive()
            )

    @property
    def bound_port(self) -> int:
        with self._lock:
            if self._server is None:
                return self.port
            return int(self._server.server_address[1])

    def start(self) -> bool:
        with self._lock:
            if not self.enabled:
                return False
            if self.transport.name == "unavailable":
                self._last_error = "transport_unavailable"
                return False
            if self.running:
                return True
            self._stop.clear()
            try:
                handler = _make_http_handler(self)
                self._server = http.server.ThreadingHTTPServer((self.host, self.port), handler)
                self._server.daemon_threads = True
                self._server_thread = threading.Thread(
                    target=self._server.serve_forever,
                    name="nana-avatar-http",
                    daemon=True,
                )
                self._worker_thread = threading.Thread(
                    target=self._worker_loop,
                    name="nana-avatar-dispatch",
                    daemon=True,
                )
                self._server_thread.start()
                self._worker_thread.start()
                return True
            except Exception as exc:
                self._last_error = f"start_failed:{type(exc).__name__}"
                server = self._server
                self._server = None
                if server is not None:
                    try:
                        server.server_close()
                    except Exception:
                        pass
                return False

    def enable_and_start(self) -> bool:
        with self._lock:
            self.enabled = True
        return self.start()

    def disable(self) -> None:
        with self._lock:
            self.enabled = False
        self.stop()

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            self._wake.set()
            server = self._server
            self._server = None
            server_thread = self._server_thread
            worker_thread = self._worker_thread
            self._server_thread = None
            self._worker_thread = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        current = threading.current_thread()
        for thread in (server_thread, worker_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=2.0)
        try:
            self.transport.close()
        except Exception:
            pass

    def preview_payload(self, payload: dict[str, Any]) -> GatewayResult:
        try:
            intent = AvatarIntent.from_payload(payload)
        except AvatarIntentError as exc:
            return GatewayResult(False, 400, str(exc))
        blocked = action_block_reason(intent.action)
        if blocked:
            return GatewayResult(False, 403, blocked, intent)
        receipt = AvatarReceipt(
            intent_id=intent.intent_id,
            status="preview",
            reason="preview_only",
            action=intent.action,
            kind=intent.kind,
            mask=intent.mask,
            priority=intent.priority,
            source=intent.source,
            channel=intent.channel,
            correlation_id=intent.correlation_id,
            created_at=intent.created_at,
            updated_at=time.time(),
        )
        return GatewayResult(True, 200, "preview_only", intent, receipt)

    def submit_payload(self, payload: dict[str, Any]) -> GatewayResult:
        try:
            intent = AvatarIntent.from_payload(payload)
        except AvatarIntentError as exc:
            with self._lock:
                self._stats["rejected"] += 1
                self._last_error = str(exc)
            return GatewayResult(False, 400, str(exc))

        with self._lock:
            self._stats["submitted"] += 1
            blocked = action_block_reason(intent.action)
            if blocked:
                self._stats["rejected"] += 1
                self._last_error = blocked
                return GatewayResult(False, 403, blocked, intent)
            if not self.enabled:
                self._stats["rejected"] += 1
                receipt = AvatarReceipt(
                    intent_id=intent.intent_id,
                    status=ReceiptStatus.REJECTED.value,
                    reason="gateway_disabled",
                    action=intent.action,
                    kind=intent.kind,
                    mask=intent.mask,
                    priority=intent.priority,
                    source=intent.source,
                    channel=intent.channel,
                    correlation_id=intent.correlation_id,
                    created_at=intent.created_at,
                    updated_at=time.time(),
                )
                return GatewayResult(False, 503, "gateway_disabled", intent, receipt)
            if not self.running:
                self._stats["rejected"] += 1
                receipt = AvatarReceipt(
                    intent_id=intent.intent_id,
                    status=ReceiptStatus.REJECTED.value,
                    reason="gateway_not_running",
                    action=intent.action,
                    kind=intent.kind,
                    mask=intent.mask,
                    priority=intent.priority,
                    source=intent.source,
                    channel=intent.channel,
                    correlation_id=intent.correlation_id,
                    created_at=intent.created_at,
                    updated_at=time.time(),
                )
                return GatewayResult(False, 503, "gateway_not_running", intent, receipt)

        receipt = self.arbiter.submit(intent)
        with self._lock:
            if receipt.status == ReceiptStatus.ACCEPTED.value:
                self._stats["accepted"] += 1
            elif receipt.status == ReceiptStatus.QUEUED.value:
                self._stats["queued"] += 1
            else:
                self._stats["rejected"] += 1
        if receipt.status in {ReceiptStatus.ACCEPTED.value, ReceiptStatus.QUEUED.value}:
            self._wake.set()
            return GatewayResult(True, 202, receipt.reason, intent, receipt)
        return GatewayResult(False, 409, receipt.reason, intent, receipt)

    def submit_action(self, action: str, **fields: Any) -> GatewayResult:
        payload = {"action": action, **fields}
        return self.submit_payload(payload)

    def submit_reply(self, reply: str, *, source: str = "nana_reply", correlation_id: str = "") -> GatewayResult:
        """Bounded presentation cue after Nana commits a reply. No transport I/O here."""
        with self._lock:
            if not self.reply_reactions_enabled:
                return self._record_reply_result(GatewayResult(False, 409, "reply_events_disabled"), source)
            if not self.enabled or not self.running:
                return self._record_reply_result(GatewayResult(False, 503, "gateway_unavailable"), source)
            cue = select_reply_cue(reply)
            if not cue.action:
                return self._record_reply_result(GatewayResult(True, 204, cue.reason), source)
            now = time.monotonic()
            key = f"{source}:{correlation_id}" if correlation_id else ""
            if key and key == self._reply_last_key:
                return self._record_reply_result(GatewayResult(True, 204, "duplicate_reply"), source, cue.action)
            if self._reply_last_at is not None and now - self._reply_last_at < REPLY_COOLDOWN_SECONDS:
                return self._record_reply_result(GatewayResult(True, 204, "reply_cooldown"), source, cue.action)
            result = self.submit_action(
                cue.action, source=source, channel="system", correlation_id=correlation_id,
                priority=20, interrupt_policy="drop", metadata={"cue": cue.reason},
            )
            if result.ok:
                self._reply_last_at = now
                self._reply_last_key = key
            return self._record_reply_result(result, source, cue.action)

    def _record_reply_result(self, result: GatewayResult, source: str, action: str = "") -> GatewayResult:
        self._reply_last = {
            "action": action, "reason": result.reason, "source": source,
            "intent_id": result.intent.intent_id if result.intent else "",
            "status": result.receipt.status if result.receipt else "skipped",
        }
        if result.ok and result.intent is not None:
            self._reply_last_sent = dict(self._reply_last)
        return result

    def submit_event(
        self,
        event: str,
        *,
        source: str = "event",
        channel: str = "public",
        correlation_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> GatewayResult:
        if not self.auto_events_enabled:
            return GatewayResult(False, 409, "auto_events_disabled")
        action = action_for_event(event)
        if action is None:
            return GatewayResult(True, 204, "event_has_no_avatar_action")
        return self.submit_action(
            action,
            source=source,
            channel=channel,
            correlation_id=correlation_id,
            metadata={"event": str(event or ""), **(metadata or {})},
        )

    def acknowledge_payload(self, payload: dict[str, Any]) -> GatewayResult:
        if not isinstance(payload, dict):
            return GatewayResult(False, 400, "receipt_must_be_object")
        intent_id = str(payload.get("intent_id", payload.get("id", "")) or "").strip()
        if not intent_id:
            return GatewayResult(False, 400, "intent_id_required")
        try:
            receipt = self.arbiter.acknowledge(
                intent_id,
                str(payload.get("status") or ""),
                reason=str(payload.get("reason") or "runtime_ack"),
            )
        except AvatarIntentError as exc:
            return GatewayResult(False, 400, str(exc))
        if receipt is None:
            previous = self.arbiter.receipt(intent_id)
            if previous is not None and previous.status in {"finished", "cancelled", "failed"}:
                return GatewayResult(True, 200, "already_terminal", receipt=previous)
            return GatewayResult(False, 404, "intent_not_active")
        with self._lock:
            self._stats["runtime_acks"] += 1
        self._wake.set()
        return GatewayResult(True, 200, receipt.reason, receipt=receipt)

    def commands_since(self, after: int = 0, *, limit: int = 16) -> dict[str, Any]:
        """Return dispatched commands, never pending ones, without skipping a page."""
        try:
            cursor = max(0, int(after))
        except (TypeError, ValueError):
            cursor = 0
        try:
            bounded_limit = max(1, min(32, int(limit)))
        except (TypeError, ValueError):
            bounded_limit = 16
        now = time.time()
        with self._lock:
            current_cursor = self._command_cursor
            history = list(self._command_history)

        commands: list[dict[str, Any]] = []
        visible_statuses = {
            ReceiptStatus.SENT.value,
            ReceiptStatus.STARTED.value,
        }
        for sequence, dispatched_at, intent in history:
            if sequence <= cursor or now - dispatched_at > LOOK_COMMAND_MAX_AGE_SECONDS:
                continue
            receipt = self.arbiter.receipt(intent.intent_id)
            if receipt is not None and receipt.status not in visible_statuses:
                continue
            commands.append({"cursor": sequence, **intent.to_wire()})
            if len(commands) >= bounded_limit:
                break
        return {
            "ok": True,
            "protocol": PROTOCOL_NAME,
            "cursor": commands[-1]["cursor"] if len(commands) >= bounded_limit else current_cursor,
            "session_id": self._command_session,
            "commands": commands,
        }

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.arbiter.tick()
                for intent in self.arbiter.claim_dispatchable():
                    result = self.transport.send(intent.to_wire())
                    self.arbiter.mark_sent(intent.intent_id, ok=result.ok, reason=result.reason)
                    with self._lock:
                        if result.ok:
                            self._stats["sent"] += 1
                            self._command_cursor += 1
                            self._command_history.append((self._command_cursor, time.time(), intent))
                            if len(self._command_history) > MAX_COMMAND_HISTORY:
                                del self._command_history[:-MAX_COMMAND_HISTORY]
                        else:
                            self._stats["transport_failed"] += 1
                            self._last_error = result.reason
            except Exception as exc:
                with self._lock:
                    self._last_error = f"dispatch_loop:{type(exc).__name__}"
            self._wake.wait(0.05)
            self._wake.clear()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            arbiter = self.arbiter.snapshot()
            return {
                "phase": PHASE,
                "protocol": PROTOCOL_NAME,
                "enabled": self.enabled,
                "running": self.running,
                "host": self.host,
                "port": self.bound_port,
                "transport": self.transport.name,
                "transport_connected": bool(self.running and self.transport.connected),
                "auto_events_enabled": self.auto_events_enabled,
                "reply_reactions_enabled": self.reply_reactions_enabled,
                "reply_reaction_cooldown_s": REPLY_COOLDOWN_SECONDS,
                "last_reply_reaction": dict(self._reply_last),
                "last_submitted_reply_reaction": dict(self._reply_last_sent) if self._reply_last_sent else None,
                "reply_actions": sorted(REPLY_ACTIONS),
                "owner_disabled_actions": sorted(OWNER_DISABLED_ACTIONS),
                "camera_owner": "human",
                "auth": "bearer" if self.token else "loopback_only",
                "stats": dict(self._stats),
                "last_error": self._last_error,
                "arbiter": arbiter,
                "safety": {
                    "loopback_only": True,
                    "semantic_actions_only": True,
                    "raw_bone_commands": False,
                    "frame_commands": False,
                    "vts_call": False,
                    "obs_call": False,
                    "game_input": False,
                },
            }

    def status_lines(self) -> list[str]:
        snap = self.snapshot()
        stats = snap["stats"]
        arbiter = snap["arbiter"]
        return [
            f"Avatar Intent Gateway ({PHASE})",
            (
                f"  Enabled: {snap['enabled']} | running={snap['running']} | "
                f"listen={snap['host']}:{snap['port']} | auth={snap['auth']}"
            ),
            (
                f"  Transport: {snap['transport']} | connected={snap['transport_connected']} | "
                f"active={len(arbiter['active'])} | pending={len(arbiter['pending'])}/{arbiter['pending_limit']}"
            ),
            f"  Auto event mapping: {snap['auto_events_enabled']} | normal viewer messages do not animate",
            f"  Reply events: {snap['reply_reactions_enabled']} | cooldown={REPLY_COOLDOWN_SECONDS}s | last_attempt={snap['last_reply_reaction']}",
            f"  Last submitted reply event: {snap['last_submitted_reply_reaction'] or 'none'}",
            f"  Owner disabled: {', '.join(snap['owner_disabled_actions'])} | camera=human only",
            (
                "  Stats: "
                f"submitted={stats['submitted']} | accepted={stats['accepted']} | queued={stats['queued']} | "
                f"rejected={stats['rejected']} | sent={stats['sent']} | failed={stats['transport_failed']} | "
                f"acks={stats['runtime_acks']}"
            ),
            f"  Last error: {snap['last_error'] or 'none'}",
            "  Recent events (latest receipt per intent):",
            *[
                f"    {r['action']} | source={r['source']} | {r['status']} | {r['reason']} | id={r['intent_id']}"
                for r in arbiter['recent_receipts']
            ],
            "  Proof: accepted/queued=gateway only; sent=transport; runtime_finished=Unity completion ACK; duration_elapsed_without_runtime_ack is not completion proof.",
            "  Protocol: Nana emits semantic action/target; runtime owns blend and pose.",
            "  Endpoints: GET /healthz | GET /v1/avatar/status | GET /v1/avatar/commands | GET /v1/avatar/receipts/<id> | POST /v1/avatar/intents | POST /v1/avatar/receipts",
            "  Safety: loopback-only | no raw bones/frames | no VTS/OBS/game call from gateway.",
        ]


@dataclass(frozen=True)
class PublicVisualHttpResponse:
    status_code: int
    headers: dict[str, str]
    payload: dict[str, Any]


class PublicVisualSignalHttpApplication:
    """Pure, testable HTTP policy for the public-only visual route."""

    def __init__(self, store: Any) -> None:
        if store is None or not callable(getattr(store, "snapshot", None)):
            raise TypeError("public_visual_store_required")
        self._store = store

    def _empty_payload(self, *, ok: bool) -> dict[str, Any]:
        try:
            payload = self._store.snapshot(after=(1 << 63) - 1)
            cursor = payload.get("cursor", 0)
            if type(cursor) is not int or cursor < 0:
                cursor = 0
        except Exception:
            cursor = 0
        return {
            "ok": bool(ok),
            "protocol": PUBLIC_VISUAL_PROTOCOL_NAME,
            "cursor": cursor,
            "stale_after_ms": PUBLIC_VISUAL_STALE_AFTER_MS,
            "current": None,
        }

    def _response(
        self,
        status_code: int,
        payload: dict[str, Any],
        *,
        origin: str | None,
    ) -> PublicVisualHttpResponse:
        headers = {
            "Cache-Control": "no-store",
            "Content-Type": "application/json; charset=utf-8",
        }
        if origin == PUBLIC_VISUAL_ALLOWED_ORIGIN:
            headers["Access-Control-Allow-Origin"] = PUBLIC_VISUAL_ALLOWED_ORIGIN
            headers["Vary"] = "Origin"
        return PublicVisualHttpResponse(status_code, headers, payload)

    def handle(
        self,
        method: str,
        target: str,
        *,
        origin: str | None = None,
    ) -> PublicVisualHttpResponse:
        normalized_origin = str(origin or "")
        if normalized_origin and normalized_origin != PUBLIC_VISUAL_ALLOWED_ORIGIN:
            return self._response(
                403,
                self._empty_payload(ok=False),
                origin=None,
            )
        if str(method or "").upper() != "GET":
            return self._response(
                405,
                self._empty_payload(ok=False),
                origin=normalized_origin,
            )
        raw_target = str(target or "")
        if not raw_target or len(raw_target) > MAX_PUBLIC_VISUAL_TARGET_CHARS:
            return self._response(
                400,
                self._empty_payload(ok=False),
                origin=normalized_origin,
            )
        split = urlsplit(raw_target)
        if split.scheme or split.netloc or split.fragment:
            return self._response(
                400,
                self._empty_payload(ok=False),
                origin=normalized_origin,
            )
        if split.path != PUBLIC_VISUAL_PATH:
            return self._response(
                404,
                self._empty_payload(ok=False),
                origin=normalized_origin,
            )
        query = parse_qs(split.query, keep_blank_values=True)
        if any(key != "after" for key in query) or len(query.get("after", [])) > 1:
            return self._response(
                400,
                self._empty_payload(ok=False),
                origin=normalized_origin,
            )
        raw_after = query.get("after", ["0"])[0]
        if not raw_after.isascii() or not raw_after.isdigit():
            return self._response(
                400,
                self._empty_payload(ok=False),
                origin=normalized_origin,
            )
        after = int(raw_after)
        if after > (1 << 63) - 1:
            return self._response(
                400,
                self._empty_payload(ok=False),
                origin=normalized_origin,
            )
        try:
            payload = self._store.snapshot(after=after)
        except Exception:
            return self._response(
                503,
                self._empty_payload(ok=False),
                origin=normalized_origin,
            )
        return self._response(200, payload, origin=normalized_origin)


def _make_public_visual_http_handler(application: PublicVisualSignalHttpApplication):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "NanaPublicVisual/1"

        def log_message(self, format: str, *args: Any) -> None:
            return None

        def _dispatch(self, method: str) -> None:
            response = application.handle(
                method,
                self.path,
                origin=self.headers.get("Origin"),
            )
            encoded = json.dumps(
                response.payload,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > MAX_PUBLIC_VISUAL_RESPONSE_BYTES:
                fallback = application._empty_payload(ok=False)
                encoded = json.dumps(
                    fallback,
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                response = PublicVisualHttpResponse(500, response.headers, fallback)
            self.send_response(response.status_code)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            self._dispatch("GET")

        def do_HEAD(self) -> None:  # noqa: N802 - stdlib handler API
            self._dispatch("HEAD")

        def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
            self._dispatch("OPTIONS")

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802 - stdlib handler API
            self._dispatch("PUT")

        def do_PATCH(self) -> None:  # noqa: N802 - stdlib handler API
            self._dispatch("PATCH")

        def do_DELETE(self) -> None:  # noqa: N802 - stdlib handler API
            self._dispatch("DELETE")

    return Handler


class PublicVisualSignalServer:
    """Dedicated loopback server exposing only the public visual GET route."""

    def __init__(self, store: Any, *, server_factory=None) -> None:
        self.application = PublicVisualSignalHttpApplication(store)
        self._server_factory = server_factory or http.server.ThreadingHTTPServer
        self._lock = threading.RLock()
        self._server = None
        self._thread: threading.Thread | None = None
        self._last_error = ""

    @property
    def running(self) -> bool:
        with self._lock:
            return self._server is not None

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def start(self) -> bool:
        with self._lock:
            if self._server is not None:
                return True
            server = None
            try:
                handler = _make_public_visual_http_handler(self.application)
                server = self._server_factory(
                    (PUBLIC_VISUAL_HOST, PUBLIC_VISUAL_PORT),
                    handler,
                )
                server.daemon_threads = True
                thread = threading.Thread(
                    target=server.serve_forever,
                    name="nana-public-visual-http",
                    daemon=True,
                )
                self._server = server
                self._thread = thread
                self._last_error = ""
                thread.start()
                return True
            except Exception as exc:
                self._server = None
                self._thread = None
                self._last_error = f"start_failed:{type(exc).__name__}"
                if server is not None:
                    try:
                        server.server_close()
                    except Exception:
                        pass
                return False

    def stop(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)


def _make_http_handler(gateway: AvatarIntentGateway):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "NanaAvatarGateway/1"

        def log_message(self, format: str, *args: Any) -> None:
            return None

        def _authorized(self) -> bool:
            if not gateway.token:
                return True
            header = self.headers.get("Authorization", "")
            expected = f"Bearer {gateway.token}"
            return hmac.compare_digest(header, expected)

        def _write_json(self, status_code: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _read_json(self) -> dict[str, Any] | None:
            raw_length = self.headers.get("Content-Length", "")
            try:
                length = int(raw_length)
            except (TypeError, ValueError):
                self._write_json(400, {"ok": False, "reason": "content_length_required"})
                return None
            if length < 0 or length > MAX_HTTP_BODY_BYTES:
                self._write_json(413, {"ok": False, "reason": "body_too_large"})
                return None
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._write_json(400, {"ok": False, "reason": "invalid_json"})
                return None
            if not isinstance(payload, dict):
                self._write_json(400, {"ok": False, "reason": "json_object_required"})
                return None
            return payload

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if not self._authorized():
                self._write_json(401, {"ok": False, "reason": "unauthorized"})
                return
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._write_json(200, {"ok": True, "protocol": PROTOCOL_NAME, "phase": PHASE})
                return
            if path == "/v1/avatar/status":
                self._write_json(200, gateway.snapshot())
                return
            if path == "/v1/avatar/commands":
                query = parse_qs(urlsplit(self.path).query)
                raw_after = query.get("after", ["0"])[0]
                raw_limit = query.get("limit", ["16"])[0]
                try:
                    after = int(raw_after)
                except (TypeError, ValueError):
                    self._write_json(400, {"ok": False, "reason": "after_must_be_integer"})
                    return
                try:
                    limit = int(raw_limit)
                except (TypeError, ValueError):
                    self._write_json(400, {"ok": False, "reason": "limit_must_be_integer"})
                    return
                self._write_json(200, gateway.commands_since(after, limit=limit))
                return
            if path == "/v1/avatar/mouth":
                query = parse_qs(urlsplit(self.path).query)
                self._write_json(200, gateway.mouth_stream.snapshot(query.get("after", ["0"])[0]))
                return
            receipt_prefix = "/v1/avatar/receipts/"
            if path.startswith(receipt_prefix):
                intent_id = path[len(receipt_prefix):].strip()
                receipt = gateway.arbiter.receipt(intent_id) if intent_id else None
                if receipt is None:
                    self._write_json(404, {"ok": False, "reason": "receipt_not_found"})
                else:
                    self._write_json(200, {"ok": True, "receipt": receipt.to_dict()})
                return
            self._write_json(404, {"ok": False, "reason": "not_found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            if not self._authorized():
                self._write_json(401, {"ok": False, "reason": "unauthorized"})
                return
            payload = self._read_json()
            if payload is None:
                return
            path = urlsplit(self.path).path
            if path in {"/v1/avatar/intents", "/v1/avatar/intent"}:
                result = gateway.submit_payload(payload)
                self._write_json(result.status_code, result.to_dict())
                return
            if path == "/v1/avatar/preview":
                result = gateway.preview_payload(payload)
                self._write_json(result.status_code, result.to_dict())
                return
            if path in {"/v1/avatar/receipts", "/v1/avatar/receipt"}:
                result = gateway.acknowledge_payload(payload)
                self._write_json(result.status_code, result.to_dict())
                return
            self._write_json(404, {"ok": False, "reason": "not_found"})

    return Handler


def get_avatar_intent_gateway() -> AvatarIntentGateway:
    return AvatarIntentGateway.get_instance()


def publish_reply_avatar(reply: str, *, source: str = "nana_reply", correlation_id: str = "") -> GatewayResult:
    """Best-effort output hook; unavailable avatar must never interrupt a reply."""
    try:
        return get_avatar_intent_gateway().submit_reply(reply, source=source, correlation_id=correlation_id)
    except Exception as exc:
        return GatewayResult(False, 503, f"reply_avatar_unavailable:{type(exc).__name__}")


def avatar_runtime_status_lines() -> list[str]:
    return get_avatar_intent_gateway().status_lines()


def issue_avatar_action(
    action: str,
    *,
    source: str = "nana_core",
    channel: str = "private",
    correlation_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> GatewayResult:
    """Issue one semantic action without exposing transport details to core."""
    return get_avatar_intent_gateway().submit_action(
        action,
        source=source,
        channel=channel,
        correlation_id=correlation_id,
        metadata=metadata,
    )


def issue_avatar_look(
    *,
    yaw: float,
    pitch: float = 0.0,
    roll: float = 0.0,
    move_s: float = LOOK_DEFAULT_MOVE_SECONDS,
    hold_s: float = LOOK_DEFAULT_HOLD_SECONDS,
    return_s: float = LOOK_DEFAULT_RETURN_SECONDS,
    return_to: str = "neutral",
    source: str = "nana_core",
    channel: str = "private",
    correlation_id: str = "",
) -> GatewayResult:
    """Issue one bounded absolute look target without exposing bone controls."""
    return get_avatar_intent_gateway().submit_action(
        "look",
        target={"yaw": yaw, "pitch": pitch, "roll": roll},
        move_s=move_s,
        hold_s=hold_s,
        return_s=return_s,
        return_to=return_to,
        source=source,
        channel=channel,
        correlation_id=correlation_id,
    )


def avatar_runtime_preview_lines(action: str = "wave") -> list[str]:
    result = get_avatar_intent_gateway().preview_payload({"action": action})
    if not result.ok:
        return [
            f"Avatar Runtime Preview ({PHASE})",
            f"  OK: False | reason={result.reason}",
            "  Safety: no transport call.",
        ]
    intent = result.intent
    return [
        f"Avatar Runtime Preview ({PHASE})",
        f"  OK: True | action={intent.action} | kind={intent.kind} | mask={intent.mask}",
        f"  Priority: {intent.priority} | duration={intent.duration_s:.2f}s | interrupt={intent.interrupt_policy}",
        f"  Protocol: {PROTOCOL_NAME} | intent_id={intent.intent_id}",
        "  Safety: preview only; no gateway/Warudo/VTS/OBS call.",
    ]


def avatar_runtime_look_preview_lines(preset: str = "left") -> list[str]:
    try:
        payload = look_payload_for_preset(preset)
    except AvatarIntentError as exc:
        return [
            f"Avatar Look Preview ({PHASE})",
            f"  OK: False | reason={exc}",
            "  Presets: left | right | center | up | down",
        ]
    result = get_avatar_intent_gateway().preview_payload(payload)
    if not result.ok or result.intent is None or result.intent.target is None:
        return [
            f"Avatar Look Preview ({PHASE})",
            f"  OK: False | reason={result.reason}",
            "  Safety: no transport call.",
        ]
    intent = result.intent
    target = intent.target
    return [
        f"Avatar Look Preview ({PHASE})",
        f"  OK: True | preset={preset} | target=yaw:{target.yaw:.1f},pitch:{target.pitch:.1f},roll:{target.roll:.1f}",
        f"  Move: {intent.move_s:.2f}s | hold={intent.hold_s:.2f}s | return={intent.return_to}:{intent.return_s:.2f}s",
        "  Runtime rule: retarget from current actual pose; never reset or accumulate command angles.",
        "  Safety: preview only; no gateway/runtime call.",
    ]


def avatar_runtime_submit_lines(action: str = "wave") -> list[str]:
    result = get_avatar_intent_gateway().submit_action(action)
    receipt = result.receipt.to_dict() if result.receipt else {}
    return [
        f"Avatar Runtime Submit ({PHASE})",
        f"  OK: {result.ok} | status_code={result.status_code} | reason={result.reason}",
        f"  Action: {action} | receipt={receipt.get('status', 'none')} | intent_id={receipt.get('intent_id', 'none')}",
        "  Safety: transport only when gateway is explicitly enabled; no VTS/OBS/game call.",
    ]


def avatar_runtime_look_submit_lines(preset: str = "left") -> list[str]:
    try:
        payload = look_payload_for_preset(preset)
    except AvatarIntentError as exc:
        return [
            f"Avatar Look Submit ({PHASE})",
            f"  OK: False | reason={exc}",
            "  Presets: left | right | center | up | down",
        ]
    result = get_avatar_intent_gateway().submit_payload(payload)
    receipt = result.receipt.to_dict() if result.receipt else {}
    target = result.intent.target if result.intent else None
    target_text = (
        f"yaw:{target.yaw:.1f},pitch:{target.pitch:.1f},roll:{target.roll:.1f}"
        if target is not None
        else "none"
    )
    return [
        f"Avatar Look Submit ({PHASE})",
        f"  OK: {result.ok} | status_code={result.status_code} | reason={result.reason}",
        f"  Preset: {preset} | target={target_text} | receipt={receipt.get('status', 'none')}",
        f"  Intent: {receipt.get('intent_id', 'none')} | runtime owns easing, hold, return, and interruption.",
    ]


def avatar_runtime_start_lines() -> list[str]:
    gateway = get_avatar_intent_gateway()
    started = gateway.enable_and_start()
    return [
        f"Avatar Runtime Start ({PHASE})",
        f"  Started: {started} | listen={gateway.host}:{gateway.bound_port}",
        "  Transport remains recording unless NANA_AVATAR_GATEWAY_TRANSPORT=warudo_ws is configured.",
    ]


def avatar_runtime_stop_lines() -> list[str]:
    gateway = get_avatar_intent_gateway()
    gateway.disable()
    return [
        f"Avatar Runtime Stop ({PHASE})",
        "  Enabled: False | running=False",
        "  Safety: no transport calls after stop.",
    ]


__all__ = [
    "ACTION_SPECS",
    "EVENT_ACTIONS",
    "LOOK_PRESETS",
    "AvatarActionSpec",
    "AvatarIntent",
    "AvatarIntentArbiter",
    "AvatarIntentError",
    "AvatarIntentGateway",
    "AvatarLookTarget",
    "AvatarReceipt",
    "GatewayResult",
    "IntentKind",
    "InterruptPolicy",
    "PHASE",
    "PROTOCOL_NAME",
    "PUBLIC_VISUAL_ALLOWED_ORIGIN",
    "PUBLIC_VISUAL_HOST",
    "PUBLIC_VISUAL_PATH",
    "PUBLIC_VISUAL_PORT",
    "PublicVisualHttpResponse",
    "PublicVisualSignalHttpApplication",
    "PublicVisualSignalServer",
    "ReceiptStatus",
    "RecordingTransport",
    "TransportResult",
    "avatar_runtime_preview_lines",
    "avatar_runtime_look_preview_lines",
    "avatar_runtime_look_submit_lines",
    "avatar_runtime_start_lines",
    "avatar_runtime_status_lines",
    "avatar_runtime_stop_lines",
    "avatar_runtime_submit_lines",
    "action_for_event",
    "get_avatar_intent_gateway",
    "publish_reply_avatar",
    "issue_avatar_action",
    "issue_avatar_look",
    "look_payload_for_preset",
    "masks_conflict",
]
