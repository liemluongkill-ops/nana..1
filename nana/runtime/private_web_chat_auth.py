"""Pure bootstrap-capability and handshake-retry ledger for private web chat."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import math
import re
from typing import Callable
from uuid import UUID

from .nana_web_ownership import NanaWebOwnershipLease
from .private_web_chat_protocol import PROTOCOL_NAME


BOOTSTRAP_TTL_SECONDS = 10.0
HANDSHAKE_RETRY_SECONDS = 5.0
_TOKEN_BYTES = 32
_ISSUE_MATERIAL_BYTES = _TOKEN_BYTES * 2
_MAX_RANDOM_ATTEMPTS = 8
_HEX_TOKEN = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class HandshakeRejected(ValueError):
    """A bounded handshake failure safe to map to a protocol response."""

    def __init__(self, reason_code: str) -> None:
        if not isinstance(reason_code, str) or _SAFE_CODE.fullmatch(reason_code) is None:
            reason_code = "handshake_rejected"
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class BootstrapGrant:
    protocol: str
    server_epoch: str
    handshake_id: str
    capability: str = field(repr=False)
    expires_in_ms: int


@dataclass(frozen=True)
class HandshakeReservation:
    server_epoch: str
    handshake_id: str
    provisional_session_id: str
    handshake_nonce: str = field(repr=False)
    client_instance_id: str


@dataclass
class _HandshakeRecord:
    capability_digest: bytes = field(repr=False)
    issued_at: float
    expires_at: float
    state: str = "issued"
    handshake_nonce: str | None = field(default=None, repr=False)
    client_instance_id: str | None = None
    provisional_session_id: str | None = None
    reserved_at: float | None = None
    retry_used: bool = False


class HandshakeLedger:
    """Bounded in-memory state for bootstrap and pre-session authentication."""

    def __init__(
        self,
        *,
        server_epoch: str,
        clock: Callable[[], float],
        random_bytes: Callable[[int], bytes],
        max_pending: int = 2,
    ) -> None:
        self._server_epoch = _canonical_uuid(server_epoch, "invalid_server_epoch")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if not callable(random_bytes):
            raise TypeError("random_bytes must be callable")
        if (
            type(max_pending) is not int
            or max_pending not in {1, 2}
        ):
            raise ValueError("max_pending must be 1 or 2")
        self._clock = clock
        self._random_bytes = random_bytes
        self._max_pending = max_pending
        self._records: dict[str, _HandshakeRecord] = {}

    def __repr__(self) -> str:
        return (
            f"HandshakeLedger(server_epoch={self._server_epoch!r}, "
            f"pending={len(self._records)}, max_pending={self._max_pending})"
        )

    def issue(self, trusted_lease: NanaWebOwnershipLease) -> BootstrapGrant:
        """Issue one grant after an external ownership verifier trusted the lease."""

        if not isinstance(trusted_lease, NanaWebOwnershipLease):
            raise HandshakeRejected("untrusted_preview_owner")
        if trusted_lease.core_boot_id != self._server_epoch:
            raise HandshakeRejected("epoch_mismatch")

        now = self._now()
        self._discard_expired(now)
        if len(self._records) >= self._max_pending:
            raise HandshakeRejected("handshake_capacity")

        handshake_id, capability = self._new_grant_material()
        self._records[handshake_id] = _HandshakeRecord(
            capability_digest=_capability_digest(capability),
            issued_at=now,
            expires_at=now + BOOTSTRAP_TTL_SECONDS,
        )
        return BootstrapGrant(
            protocol=PROTOCOL_NAME,
            server_epoch=self._server_epoch,
            handshake_id=handshake_id,
            capability=capability,
            expires_in_ms=int(BOOTSTRAP_TTL_SECONDS * 1000),
        )

    def reserve_hello(
        self,
        handshake_id: str,
        capability: str,
        handshake_nonce: str,
        client_instance_id: str,
    ) -> HandshakeReservation:
        """Reserve a valid one-time grant for the first session.hello."""

        checked_nonce = _handshake_nonce(handshake_nonce)
        checked_client = _canonical_uuid(client_instance_id, "invalid_client_instance")
        now = self._now()
        record = self._record(handshake_id, now)
        self._require_capability(record, capability)
        if record.state != "issued":
            raise HandshakeRejected("handshake_already_reserved")

        provisional_session_id = self._new_session_id()
        record.state = "reserved"
        record.handshake_nonce = checked_nonce
        record.client_instance_id = checked_client
        record.provisional_session_id = provisional_session_id
        record.reserved_at = now
        return self._reservation(handshake_id, record)

    def retry_reservation(
        self,
        handshake_id: str,
        capability: str,
        handshake_nonce: str,
        client_instance_id: str,
    ) -> HandshakeReservation:
        """Allow the identical pre-confirmation handshake to reconnect once."""

        checked_nonce = _handshake_nonce(handshake_nonce)
        checked_client = _canonical_uuid(client_instance_id, "invalid_client_instance")
        now = self._now()
        record = self._record(handshake_id, now)
        self._require_capability(record, capability)
        if record.state not in {"reserved", "active_ack_validated"}:
            raise HandshakeRejected("handshake_not_reserved")
        if (
            record.handshake_nonce != checked_nonce
            or record.client_instance_id != checked_client
        ):
            raise HandshakeRejected("handshake_retry_mismatch")
        if record.retry_used:
            raise HandshakeRejected("handshake_retry_exhausted")
        if (
            record.reserved_at is None
            or now - record.reserved_at >= HANDSHAKE_RETRY_SECONDS
        ):
            raise HandshakeRejected("handshake_retry_expired")

        record.retry_used = True
        record.state = "reserved"
        return self._reservation(handshake_id, record)

    def validate_active_ack(
        self,
        handshake_id: str,
        server_epoch: str,
        provisional_session_id: str,
        handshake_nonce: str,
        client_instance_id: str,
    ) -> HandshakeReservation:
        """Validate session.active_ack without consuming the capability."""

        checked_epoch = _canonical_uuid(server_epoch, "epoch_mismatch")
        if checked_epoch != self._server_epoch:
            raise HandshakeRejected("epoch_mismatch")
        checked_session = _canonical_uuid(
            provisional_session_id, "active_ack_mismatch"
        )
        checked_nonce = _handshake_nonce(handshake_nonce)
        checked_client = _canonical_uuid(client_instance_id, "active_ack_mismatch")
        now = self._now()
        record = self._record(handshake_id, now)
        if record.state != "reserved":
            raise HandshakeRejected("handshake_not_reserved")
        if (
            record.provisional_session_id != checked_session
            or record.handshake_nonce != checked_nonce
            or record.client_instance_id != checked_client
        ):
            raise HandshakeRejected("active_ack_mismatch")

        reservation = self._reservation(handshake_id, record)
        record.state = "active_ack_validated"
        return reservation

    def commit_confirmed(
        self,
        handshake_id: str,
        server_epoch: str,
        provisional_session_id: str,
        handshake_nonce: str,
        client_instance_id: str,
    ) -> HandshakeReservation:
        """Consume a validated capability after session.confirmed was emitted."""

        checked_epoch = _canonical_uuid(server_epoch, "epoch_mismatch")
        if checked_epoch != self._server_epoch:
            raise HandshakeRejected("epoch_mismatch")
        checked_session = _canonical_uuid(
            provisional_session_id, "confirmed_commit_mismatch"
        )
        checked_nonce = _handshake_nonce(handshake_nonce)
        checked_client = _canonical_uuid(
            client_instance_id, "confirmed_commit_mismatch"
        )
        now = self._now()
        record = self._record(handshake_id, now)
        if record.state != "active_ack_validated":
            raise HandshakeRejected("active_ack_not_validated")
        if (
            record.provisional_session_id != checked_session
            or record.handshake_nonce != checked_nonce
            or record.client_instance_id != checked_client
        ):
            raise HandshakeRejected("confirmed_commit_mismatch")

        reservation = self._reservation(handshake_id, record)
        record.state = "confirmed"
        record.capability_digest = b""
        record.state = "consumed"
        del self._records[handshake_id]
        return reservation

    def clear(self) -> None:
        """Revoke all outstanding bootstrap grants."""

        for record in self._records.values():
            record.capability_digest = b""
            record.state = "consumed"
        self._records.clear()

    def _new_grant_material(self) -> tuple[str, str]:
        for _ in range(_MAX_RANDOM_ATTEMPTS):
            material = self._random_material(_ISSUE_MATERIAL_BYTES)
            handshake_id = material[:_TOKEN_BYTES].hex()
            if handshake_id in self._records:
                continue
            capability = material[_TOKEN_BYTES:].hex()
            return handshake_id, capability
        raise HandshakeRejected("random_source_failure")

    def _new_session_id(self) -> str:
        existing = {
            record.provisional_session_id
            for record in self._records.values()
            if record.provisional_session_id is not None
        }
        for _ in range(_MAX_RANDOM_ATTEMPTS):
            session_id = str(UUID(bytes=self._random_material(16), version=4))
            if session_id not in existing:
                return session_id
        raise HandshakeRejected("random_source_failure")

    def _random_material(self, size: int) -> bytes:
        try:
            material = self._random_bytes(size)
        except Exception:
            raise HandshakeRejected("random_source_failure") from None
        if not isinstance(material, bytes) or len(material) != size:
            raise HandshakeRejected("random_source_failure")
        return material

    def _now(self) -> float:
        try:
            now = self._clock()
        except Exception:
            raise HandshakeRejected("clock_unavailable") from None
        if (
            not isinstance(now, (int, float))
            or isinstance(now, bool)
            or not math.isfinite(float(now))
        ):
            raise HandshakeRejected("clock_unavailable")
        return float(now)

    def _discard_expired(self, now: float) -> None:
        expired = [
            handshake_id
            for handshake_id, record in self._records.items()
            if now >= record.expires_at
        ]
        for handshake_id in expired:
            record = self._records.pop(handshake_id)
            record.capability_digest = b""
            record.state = "consumed"

    def _record(self, handshake_id: str, now: float) -> _HandshakeRecord:
        if not isinstance(handshake_id, str) or _HEX_TOKEN.fullmatch(handshake_id) is None:
            raise HandshakeRejected("handshake_not_found")
        record = self._records.get(handshake_id)
        if record is None:
            raise HandshakeRejected("handshake_not_found")
        if now >= record.expires_at:
            record.capability_digest = b""
            record.state = "consumed"
            del self._records[handshake_id]
            raise HandshakeRejected("capability_expired")
        return record

    @staticmethod
    def _require_capability(record: _HandshakeRecord, capability: object) -> None:
        valid_shape = (
            isinstance(capability, str)
            and _HEX_TOKEN.fullmatch(capability) is not None
        )
        candidate = capability if valid_shape else ""
        candidate_digest = _capability_digest(candidate)
        matches = hmac.compare_digest(record.capability_digest, candidate_digest)
        if not valid_shape or not matches:
            raise HandshakeRejected("capability_rejected")

    def _reservation(
        self, handshake_id: str, record: _HandshakeRecord
    ) -> HandshakeReservation:
        if (
            record.provisional_session_id is None
            or record.handshake_nonce is None
            or record.client_instance_id is None
        ):
            raise HandshakeRejected("handshake_not_reserved")
        return HandshakeReservation(
            server_epoch=self._server_epoch,
            handshake_id=handshake_id,
            provisional_session_id=record.provisional_session_id,
            handshake_nonce=record.handshake_nonce,
            client_instance_id=record.client_instance_id,
        )


def _capability_digest(capability: str) -> bytes:
    return hashlib.sha256(capability.encode("ascii")).digest()


def _canonical_uuid(value: object, reason_code: str) -> str:
    if not isinstance(value, str):
        raise HandshakeRejected(reason_code)
    try:
        parsed = UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise HandshakeRejected(reason_code) from None
    if str(parsed) != value:
        raise HandshakeRejected(reason_code)
    return value


def _handshake_nonce(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise HandshakeRejected("invalid_handshake_nonce")
    return value


__all__ = [
    "BOOTSTRAP_TTL_SECONDS",
    "HANDSHAKE_RETRY_SECONDS",
    "PROTOCOL_NAME",
    "BootstrapGrant",
    "HandshakeLedger",
    "HandshakeRejected",
    "HandshakeReservation",
]
