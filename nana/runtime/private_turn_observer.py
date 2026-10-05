"""Sanitized, provider-neutral observer for one private web turn.

The observer is deliberately passive: it records no prompts, memory, or
history and cannot alter the canonical pipeline when its event sink fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal
from uuid import UUID


PROTOCOL_NAME = "nana.private-web-chat.v1"
_TURN_STATES = frozenset({"accepted", "thinking", "generated", "complete", "failed", "unknown"})
_VOICE_STATES = frozenset({"idle", "queued", "speaking", "delivered", "failed", "unknown"})


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid_{field}")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"invalid_{field}") from None
    if str(parsed) != value:
        raise ValueError(f"invalid_{field}")
    return value


@dataclass(frozen=True)
class PrivateTurnIdentity:
    server_epoch: str
    session_id: str
    client_instance_id: str
    turn_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        for name in (
            "server_epoch",
            "session_id",
            "client_instance_id",
            "turn_id",
            "correlation_id",
        ):
            _uuid(getattr(self, name), name)


@dataclass(frozen=True)
class PrivateTurnResult:
    should_exit: bool
    status: Literal["complete", "failed", "unknown"]
    final_text: str
    reason_code: str | None
    voice_ticket: int | None

    def __post_init__(self) -> None:
        if self.status not in {"complete", "failed", "unknown"}:
            raise ValueError("invalid_private_turn_status")
        if not isinstance(self.final_text, str):
            raise ValueError("invalid_private_final_text")
        if self.reason_code is not None and not isinstance(self.reason_code, str):
            raise ValueError("invalid_private_reason_code")
        if self.voice_ticket is not None and (
            type(self.voice_ticket) is not int or self.voice_ticket < 1
        ):
            raise ValueError("invalid_private_voice_ticket")


@dataclass(frozen=True)
class PrivateTurnEvent:
    protocol: str
    message_type: str
    identity: PrivateTurnIdentity
    revision: int
    event_sequence: int
    payload: dict[str, Any]


class PrivateTurnObserver:
    """Emit indexed, sanitized events while preserving canonical turn results."""

    def __init__(
        self,
        identity: PrivateTurnIdentity,
        sink: Callable[[PrivateTurnEvent], object] | None = None,
    ) -> None:
        if not isinstance(identity, PrivateTurnIdentity):
            raise TypeError("identity must be PrivateTurnIdentity")
        if sink is not None and not callable(sink):
            raise TypeError("sink must be callable")
        self.identity = identity
        self._sink = sink
        self._revision = 0
        self._event_sequence = 0
        self._delta_index = 0
        self._final_emitted = False
        self._terminal = False
        self._final_text = ""
        self._voice_ticket: int | None = None
        self._voice_state = "idle"
        self._last_turn_state: str | None = None
        self._result = PrivateTurnResult(False, "unknown", "", "not_finished", None)
        self._sink_failures = 0

    @property
    def final_text(self) -> str:
        return self._final_text

    @property
    def result(self) -> PrivateTurnResult:
        return self._result

    @property
    def sink_failures(self) -> int:
        return self._sink_failures

    @property
    def voice_ticket(self) -> int | None:
        return self._voice_ticket

    @property
    def delta_index(self) -> int:
        return self._delta_index

    def _emit(
        self,
        message_type: str,
        payload: dict[str, Any],
        *,
        allow_after_terminal: bool = False,
    ) -> None:
        if self._terminal and not allow_after_terminal:
            return
        self._revision += 1
        self._event_sequence += 1
        event = PrivateTurnEvent(
            protocol=PROTOCOL_NAME,
            message_type=message_type,
            identity=self.identity,
            revision=self._revision,
            event_sequence=self._event_sequence,
            payload=dict(payload),
        )
        if self._sink is None:
            return
        try:
            self._sink(event)
        except Exception:
            self._sink_failures += 1

    def on_accepted(self, *_args: object, **_kwargs: object) -> None:
        self._turn_state("accepted")

    def on_thinking(self, *_args: object, **_kwargs: object) -> None:
        self._turn_state("thinking")

    def _turn_state(self, state: str, reason_code: str | None = None) -> None:
        if self._terminal or state not in _TURN_STATES or state == self._last_turn_state:
            return
        self._last_turn_state = state
        payload: dict[str, Any] = {"state": state}
        if reason_code:
            payload["reason_code"] = reason_code[:80]
        self._emit("turn.state", payload)

    def on_text_delta(self, text: object = "", *_args: object, **_kwargs: object) -> None:
        if self._terminal or self._final_emitted or not isinstance(text, str) or not text:
            return
        self._delta_index += 1
        self._emit(
            "assistant.delta",
            {"delta_index": self._delta_index, "text": text},
        )

    def on_text_final(self, text: object = "", *_args: object, **_kwargs: object) -> None:
        if self._terminal or self._final_emitted or not isinstance(text, str):
            return
        self._final_text = text
        self._final_emitted = True
        self._emit("assistant.final", {"text": text})
        self._turn_state("generated")

    def on_voice_queued(self, ticket: object = None, *_args: object, **_kwargs: object) -> None:
        self._set_voice(ticket, "queued")

    def on_voice_first_audio(self, ticket: object = None, *_args: object, **_kwargs: object) -> None:
        self._set_voice(ticket, "speaking")

    on_voice_first = on_voice_first_audio

    def on_voice_complete(self, ticket: object = None, *_args: object, **_kwargs: object) -> None:
        self._set_voice(ticket, "delivered")

    def on_voice_failed(self, reason_code: object = "voice_failed", *_args: object, **kwargs: object) -> None:
        self._set_voice(kwargs.get("ticket"), "failed", reason_code)

    def on_voice_unknown(self, reason_code: object = "unknown_outcome", *_args: object, **kwargs: object) -> None:
        self._set_voice(kwargs.get("ticket"), "unknown", reason_code)

    def on_voice_receipt(self, receipt: object = None, *_args: object, **_kwargs: object) -> None:
        """Consume a receipt snapshot without importing the ledger module.

        The ledger is intentionally provider/runtime neutral. Duck typing here
        keeps the observer independent while allowing late audio receipts after
        the text turn has already reached ``complete``.
        """

        state = getattr(receipt, "state", None)
        context = getattr(receipt, 'context', None)
        if any(getattr(context, name, None) != getattr(self.identity, name) for name in ('server_epoch', 'session_id', 'turn_id', 'correlation_id')):
            return
        ticket = getattr(receipt, "engine_ticket", None)
        reason = getattr(receipt, "reason_code", None)
        if state == "queued":
            self.on_voice_queued(ticket)
        elif state == "speaking":
            self.on_voice_first_audio(ticket)
        elif state == "delivered":
            self.on_voice_complete(ticket)
        elif state == "failed":
            self.on_voice_failed(reason or "voice_failed", ticket=ticket)
        elif state == "unknown":
            self.on_voice_unknown(reason or "unknown_outcome", ticket=ticket)

    def _set_voice(self, ticket: object, state: str, reason_code: object = None) -> None:
        if state not in _VOICE_STATES:
            return
        if ticket is not None:
            if type(ticket) is not int or ticket < 1:
                return
            if self._voice_ticket is None:
                self._voice_ticket = ticket
            elif self._voice_ticket != ticket:
                return
        order = {
            "idle": 0,
            "queued": 1,
            "speaking": 2,
            "delivered": 3,
            "failed": 3,
            "unknown": 3,
        }
        if order.get(state, 0) <= order.get(self._voice_state, 0):
            return
        self._voice_state = state
        payload: dict[str, Any] = {"state": state}
        if self._voice_ticket is not None:
            payload["voice_ticket"] = self._voice_ticket
        if isinstance(reason_code, str) and reason_code:
            payload["reason_code"] = reason_code[:80]
        self._emit("voice.state", payload, allow_after_terminal=True)

    def on_terminal(self, result: object = None, *_args: object, **_kwargs: object) -> None:
        if self._terminal:
            return
        if isinstance(result, PrivateTurnResult):
            candidate = result
        else:
            candidate = PrivateTurnResult(
                False,
                "complete" if self._final_text else "unknown",
                self._final_text,
                None,
                self._voice_ticket,
            )
        if candidate.final_text and not self._final_emitted:
            self.on_text_final(candidate.final_text)
        self._final_text = candidate.final_text or self._final_text
        self._result = PrivateTurnResult(
            candidate.should_exit,
            candidate.status,
            self._final_text,
            candidate.reason_code,
            candidate.voice_ticket if candidate.voice_ticket is not None else self._voice_ticket,
        )
        self._terminal = True
        payload: dict[str, Any] = {"state": self._result.status}
        if self._result.reason_code:
            payload["reason_code"] = self._result.reason_code[:80]
        self._last_turn_state = self._result.status
        self._emit_terminal("turn.state", payload)

    def _emit_terminal(self, message_type: str, payload: dict[str, Any]) -> None:
        self._revision += 1
        self._event_sequence += 1
        event = PrivateTurnEvent(
            protocol=PROTOCOL_NAME,
            message_type=message_type,
            identity=self.identity,
            revision=self._revision,
            event_sequence=self._event_sequence,
            payload=dict(payload),
        )
        if self._sink is None:
            return
        try:
            self._sink(event)
        except Exception:
            self._sink_failures += 1


__all__ = [
    "PROTOCOL_NAME",
    "PrivateTurnEvent",
    "PrivateTurnIdentity",
    "PrivateTurnObserver",
    "PrivateTurnResult",
]
