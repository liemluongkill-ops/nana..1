"""Pure smoke for private-web bootstrap capabilities and handshake retries.

The smoke injects clock and randomness. It opens no socket and touches no
process, model, memory, voice, browser, OBS, credential, or persistent-data
boundary.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from enum import IntEnum
import hashlib
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import nana.runtime.private_web_chat_auth as auth  # noqa: E402
from nana.runtime.nana_web_ownership import NanaWebOwnershipLease  # noqa: E402
from nana.runtime.private_web_chat_auth import (  # noqa: E402
    BOOTSTRAP_TTL_SECONDS,
    HANDSHAKE_RETRY_SECONDS,
    PROTOCOL_NAME,
    BootstrapGrant,
    HandshakeLedger,
    HandshakeRejected,
)


SERVER_EPOCH = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_EPOCH = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
CLIENT_INSTANCE_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


class PendingLimitEnum(IntEnum):
    ONE = 1


class PendingLimitIntSubclass(int):
    pass


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class DeterministicRandom:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, size: int) -> bytes:
        offset = self.calls
        self.calls += 1
        return bytes((offset + index) % 256 for index in range(size))


def make_lease(*, core_boot_id: str = SERVER_EPOCH) -> NanaWebOwnershipLease:
    return NanaWebOwnershipLease(
        core_boot_id=core_boot_id,
        launch_nonce="f" * 64,
        node_pid=1234,
        node_process_creation_time=100.0,
        app_root=(ROOT / "components" / "nana-app").as_posix(),
        host="127.0.0.1",
        port=5174,
        command_fingerprint="vite-preview-v1",
    )


def make_ledger(
    *,
    clock: FakeClock | None = None,
    random_bytes: DeterministicRandom | None = None,
    max_pending: int = 2,
) -> tuple[HandshakeLedger, FakeClock, DeterministicRandom]:
    owned_clock = clock or FakeClock()
    owned_random = random_bytes or DeterministicRandom()
    return (
        HandshakeLedger(
            server_epoch=SERVER_EPOCH,
            clock=owned_clock,
            random_bytes=owned_random,
            max_pending=max_pending,
        ),
        owned_clock,
        owned_random,
    )


def expect_rejected(code: str, callback) -> HandshakeRejected:
    try:
        callback()
    except HandshakeRejected as exc:
        assert exc.reason_code == code, exc
        assert str(exc) == code, exc
        assert re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(exc)), exc
        return exc
    raise AssertionError(f"expected HandshakeRejected({code!r})")


def validate_active_ack(
    ledger: HandshakeLedger, grant: BootstrapGrant, reservation
):
    return ledger.validate_active_ack(
        handshake_id=grant.handshake_id,
        server_epoch=SERVER_EPOCH,
        provisional_session_id=reservation.provisional_session_id,
        handshake_nonce=reservation.handshake_nonce,
        client_instance_id=reservation.client_instance_id,
    )


def commit_confirmed(
    ledger: HandshakeLedger, grant: BootstrapGrant, reservation
):
    return ledger.commit_confirmed(
        handshake_id=grant.handshake_id,
        server_epoch=SERVER_EPOCH,
        provisional_session_id=reservation.provisional_session_id,
        handshake_nonce=reservation.handshake_nonce,
        client_instance_id=reservation.client_instance_id,
    )


def test_issue_returns_immutable_redacted_digest_only_grant() -> None:
    ledger, _, _ = make_ledger()
    grant = ledger.issue(make_lease())

    assert grant.protocol == PROTOCOL_NAME == "nana.private-web-chat.v1"
    assert grant.server_epoch == SERVER_EPOCH
    assert grant.expires_in_ms == 10_000
    assert len(grant.handshake_id) == 64
    assert len(grant.capability) == 64
    assert bytes.fromhex(grant.handshake_id)
    assert bytes.fromhex(grant.capability)

    try:
        grant.capability = "changed"
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("BootstrapGrant must be immutable")

    record = ledger._records[grant.handshake_id]
    expected_digest = hashlib.sha256(grant.capability.encode("ascii")).digest()
    assert record.capability_digest == expected_digest
    assert "capability" not in vars(record)
    rendered = "\n".join((repr(grant), repr(record), repr(ledger), repr(vars(ledger))))
    assert grant.capability not in rendered
    assert expected_digest.hex() not in rendered
    assert make_lease().launch_nonce not in rendered


def test_valid_and_invalid_capabilities_cross_digest_comparison_boundary() -> None:
    ledger, _, _ = make_ledger()
    grant = ledger.issue(make_lease())
    calls: list[tuple[bytes, bytes]] = []
    real_compare_digest = auth.hmac.compare_digest

    def tracked_compare_digest(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return real_compare_digest(left, right)

    auth.hmac.compare_digest = tracked_compare_digest
    try:
        expect_rejected(
            "capability_rejected",
            lambda: ledger.reserve_hello(
                grant.handshake_id,
                "0" * 64,
                "nonce-1",
                CLIENT_INSTANCE_ID,
            ),
        )
        reservation = ledger.reserve_hello(
            grant.handshake_id,
            grant.capability,
            "nonce-1",
            CLIENT_INSTANCE_ID,
        )
    finally:
        auth.hmac.compare_digest = real_compare_digest

    assert reservation.handshake_id == grant.handshake_id
    assert len(calls) == 2
    assert calls[0][0] == hashlib.sha256(grant.capability.encode("ascii")).digest()
    assert calls[0][1] == hashlib.sha256(("0" * 64).encode("ascii")).digest()
    assert calls[1][0] == hashlib.sha256(grant.capability.encode("ascii")).digest()
    assert calls[1][1] == hashlib.sha256(grant.capability.encode("ascii")).digest()
    assert all(len(value) == 32 for call in calls for value in call)


def test_capability_expires_at_ten_seconds() -> None:
    assert BOOTSTRAP_TTL_SECONDS == 10.0
    ledger, clock, _ = make_ledger()
    still_valid = ledger.issue(make_lease())
    clock.advance(BOOTSTRAP_TTL_SECONDS - 0.001)
    reservation = ledger.reserve_hello(
        still_valid.handshake_id,
        still_valid.capability,
        "nonce-before-expiry",
        CLIENT_INSTANCE_ID,
    )
    assert reservation.handshake_id == still_valid.handshake_id

    ledger.clear()
    expires = ledger.issue(make_lease())
    clock.advance(BOOTSTRAP_TTL_SECONDS)
    expect_rejected(
        "capability_expired",
        lambda: ledger.reserve_hello(
            expires.handshake_id,
            expires.capability,
            "nonce-at-expiry",
            CLIENT_INSTANCE_ID,
        ),
    )


def test_active_ack_is_retained_until_confirmed_commit() -> None:
    ledger, _, _ = make_ledger()
    grant = ledger.issue(make_lease())
    expect_rejected(
        "handshake_not_reserved",
        lambda: ledger.validate_active_ack(
            grant.handshake_id,
            SERVER_EPOCH,
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "nonce-1",
            CLIENT_INSTANCE_ID,
        ),
    )

    reservation = ledger.reserve_hello(
        grant.handshake_id,
        grant.capability,
        "nonce-1",
        CLIENT_INSTANCE_ID,
    )
    validated = validate_active_ack(ledger, grant, reservation)

    assert validated == reservation
    assert ledger._records[grant.handshake_id].state == "active_ack_validated"
    assert ledger._records[grant.handshake_id].capability_digest
    committed = commit_confirmed(ledger, grant, reservation)
    assert committed == reservation
    expect_rejected(
        "handshake_not_found",
        lambda: ledger.retry_reservation(
            grant.handshake_id,
            grant.capability,
            "nonce-1",
            CLIENT_INSTANCE_ID,
        ),
    )


def test_invalid_active_ack_does_not_consume_reservation() -> None:
    ledger, _, _ = make_ledger()
    grant = ledger.issue(make_lease())
    reservation = ledger.reserve_hello(
        grant.handshake_id,
        grant.capability,
        "nonce-1",
        CLIENT_INSTANCE_ID,
    )

    expect_rejected(
        "epoch_mismatch",
        lambda: ledger.validate_active_ack(
            grant.handshake_id,
            OTHER_EPOCH,
            reservation.provisional_session_id,
            "nonce-1",
            CLIENT_INSTANCE_ID,
        ),
    )
    expect_rejected(
        "active_ack_mismatch",
        lambda: ledger.validate_active_ack(
            grant.handshake_id,
            SERVER_EPOCH,
            reservation.provisional_session_id,
            "changed-nonce",
            CLIENT_INSTANCE_ID,
        ),
    )

    assert validate_active_ack(ledger, grant, reservation) == reservation
    assert grant.handshake_id in ledger._records
    assert commit_confirmed(ledger, grant, reservation) == reservation


def test_disconnect_after_active_ack_retries_same_session_before_commit() -> None:
    ledger, _, random_bytes = make_ledger()
    grant = ledger.issue(make_lease())
    first = ledger.reserve_hello(
        grant.handshake_id,
        grant.capability,
        "nonce-1",
        CLIENT_INSTANCE_ID,
    )
    validate_active_ack(ledger, grant, first)
    assert ledger._records[grant.handshake_id].state == "active_ack_validated"

    retry = ledger.retry_reservation(
        grant.handshake_id,
        grant.capability,
        "nonce-1",
        CLIENT_INSTANCE_ID,
    )
    assert retry == first
    assert retry.provisional_session_id == first.provisional_session_id
    assert ledger._records[grant.handshake_id].state == "reserved"
    assert random_bytes.calls == 2

    validate_active_ack(ledger, grant, retry)
    expect_rejected(
        "handshake_retry_exhausted",
        lambda: ledger.retry_reservation(
            grant.handshake_id,
            grant.capability,
            "nonce-1",
            CLIENT_INSTANCE_ID,
        ),
    )
    assert commit_confirmed(ledger, grant, retry) == first


def test_disconnect_before_confirm_gets_one_same_session_retry() -> None:
    clock = FakeClock()
    epoch = SERVER_EPOCH
    client_id = CLIENT_INSTANCE_ID
    trusted_lease = make_lease()
    ledger = HandshakeLedger(
        server_epoch=epoch,
        clock=clock,
        random_bytes=lambda size: bytes(range(size)),
        max_pending=2,
    )
    grant = ledger.issue(trusted_lease)
    first = ledger.reserve_hello(
        grant.handshake_id, grant.capability, "nonce-1", client_id
    )
    assert first.provisional_session_id
    retry = ledger.retry_reservation(
        grant.handshake_id, grant.capability, "nonce-1", client_id
    )
    assert retry.provisional_session_id == first.provisional_session_id
    assert retry == first
    expect_rejected(
        "handshake_retry_exhausted",
        lambda: ledger.retry_reservation(
            grant.handshake_id, grant.capability, "nonce-1", client_id
        ),
    )


def test_changed_retry_nonce_fails_without_spending_retry() -> None:
    ledger, _, _ = make_ledger()
    grant = ledger.issue(make_lease())
    first = ledger.reserve_hello(
        grant.handshake_id,
        grant.capability,
        "nonce-1",
        CLIENT_INSTANCE_ID,
    )

    expect_rejected(
        "handshake_retry_mismatch",
        lambda: ledger.retry_reservation(
            grant.handshake_id,
            grant.capability,
            "nonce-2",
            CLIENT_INSTANCE_ID,
        ),
    )
    retry = ledger.retry_reservation(
        grant.handshake_id,
        grant.capability,
        "nonce-1",
        CLIENT_INSTANCE_ID,
    )
    assert retry == first


def test_retry_must_start_within_five_seconds() -> None:
    assert HANDSHAKE_RETRY_SECONDS == 5.0
    ledger, clock, _ = make_ledger()
    grant = ledger.issue(make_lease())
    ledger.reserve_hello(
        grant.handshake_id,
        grant.capability,
        "nonce-1",
        CLIENT_INSTANCE_ID,
    )
    clock.advance(HANDSHAKE_RETRY_SECONDS)

    expect_rejected(
        "handshake_retry_expired",
        lambda: ledger.retry_reservation(
            grant.handshake_id,
            grant.capability,
            "nonce-1",
            CLIENT_INSTANCE_ID,
        ),
    )


def test_pending_handshake_capacity_is_two_and_confirm_releases_it() -> None:
    ledger, _, _ = make_ledger()
    first = ledger.issue(make_lease())
    second = ledger.issue(make_lease())
    assert first.handshake_id != second.handshake_id

    expect_rejected("handshake_capacity", lambda: ledger.issue(make_lease()))

    reservation = ledger.reserve_hello(
        first.handshake_id,
        first.capability,
        "nonce-1",
        CLIENT_INSTANCE_ID,
    )
    validate_active_ack(ledger, first, reservation)
    commit_confirmed(ledger, first, reservation)
    replacement = ledger.issue(make_lease())
    assert replacement.handshake_id not in {
        first.handshake_id,
        second.handshake_id,
    }


def test_max_pending_accepts_only_integer_one_or_two() -> None:
    for valid in (1, 2):
        ledger, _, _ = make_ledger(max_pending=valid)
        assert ledger.issue(make_lease()).handshake_id

    for invalid in (
        0,
        3,
        -1,
        True,
        False,
        1.0,
        "2",
        None,
        PendingLimitEnum.ONE,
        PendingLimitIntSubclass(1),
        PendingLimitIntSubclass(2),
    ):
        try:
            HandshakeLedger(
                server_epoch=SERVER_EPOCH,
                clock=FakeClock(),
                random_bytes=DeterministicRandom(),
                max_pending=invalid,
            )
        except ValueError as exc:
            assert str(exc) == "max_pending must be 1 or 2"
        else:
            raise AssertionError(f"max_pending={invalid!r} must be rejected")


def test_issue_binds_lease_epoch_and_clear_revokes_every_grant() -> None:
    ledger, _, _ = make_ledger()
    expect_rejected(
        "epoch_mismatch",
        lambda: ledger.issue(make_lease(core_boot_id=OTHER_EPOCH)),
    )

    first = ledger.issue(make_lease())
    second = ledger.issue(make_lease())
    ledger.clear()
    for grant in (first, second):
        expect_rejected(
            "handshake_not_found",
            lambda grant=grant: ledger.reserve_hello(
                grant.handshake_id,
                grant.capability,
                "nonce-cleared",
                CLIENT_INSTANCE_ID,
            ),
        )


def main() -> None:
    tests = [
        test_issue_returns_immutable_redacted_digest_only_grant,
        test_valid_and_invalid_capabilities_cross_digest_comparison_boundary,
        test_capability_expires_at_ten_seconds,
        test_active_ack_is_retained_until_confirmed_commit,
        test_invalid_active_ack_does_not_consume_reservation,
        test_disconnect_after_active_ack_retries_same_session_before_commit,
        test_disconnect_before_confirm_gets_one_same_session_retry,
        test_changed_retry_nonce_fails_without_spending_retry,
        test_retry_must_start_within_five_seconds,
        test_pending_handshake_capacity_is_two_and_confirm_releases_it,
        test_max_pending_accepts_only_integer_one_or_two,
        test_issue_binds_lease_epoch_and_clear_revokes_every_grant,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_private_web_chat_auth: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
