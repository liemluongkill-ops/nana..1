"""Offline smoke for the Core-owned NanaApp preview identity proof.

Every process and listener inspection boundary is injected. This smoke must
never inspect or terminate a process running on the developer machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from nana.runtime.nana_web_ownership import (  # noqa: E402
    EXPECTED_COMMAND,
    NanaWebOwnershipLease,
    NanaWebOwnershipVerifier,
    ProcessSnapshot,
    tcp_listener_pid,
)


CORE_BOOT_ID = "33333333-3333-4333-8333-333333333333"
_UNSET = object()


def make_lease(**overrides) -> NanaWebOwnershipLease:
    fields = {
        "core_boot_id": CORE_BOOT_ID,
        "launch_nonce": "a" * 64,
        "node_pid": 1234,
        "node_process_creation_time": 100.0,
        "app_root": (ROOT / "components" / "nana-app").as_posix(),
        "host": "127.0.0.1",
        "port": 5174,
        "command_fingerprint": "vite-preview-v1",
    }
    fields.update(overrides)
    return NanaWebOwnershipLease(**fields)


def make_verifier(
    *,
    snapshot,
    listener=1234,
    boot_id=CORE_BOOT_ID,
    active_lease=_UNSET,
    active_lease_provider=None,
):
    if active_lease_provider is None:
        if active_lease is _UNSET:
            active_lease = make_lease()
        active_lease_provider = lambda: active_lease
    return NanaWebOwnershipVerifier(
        process_snapshot=lambda pid: snapshot(pid),
        listener_pid=lambda host, port: listener,
        current_core_boot_id=lambda: boot_id,
        active_lease_provider=active_lease_provider,
    )


def test_owned_preview_requires_process_and_listener_identity() -> None:
    lease = make_lease()
    matching = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(pid, 100.0, EXPECTED_COMMAND),
    )

    result = matching.verify(lease)

    assert result.trusted is True
    assert result.reason == "owned_preview_verified"

    reused_pid = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(pid, 101.0, EXPECTED_COMMAND),
    )
    mismatch = reused_pid.verify(lease)
    assert mismatch.trusted is False
    assert mismatch.reason == "process_identity_mismatch"


def test_candidate_without_active_launcher_lease_is_not_trusted() -> None:
    result = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(pid, 100.0, EXPECTED_COMMAND),
        active_lease=None,
    ).verify(make_lease())

    assert result.trusted is False
    assert result.reason == "active_lease_unavailable"


def test_fabricated_candidate_cannot_replace_launcher_authority() -> None:
    active = make_lease()
    fabricated = make_lease(launch_nonce="b" * 64)
    result = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(pid, 100.0, EXPECTED_COMMAND),
        active_lease=active,
    ).verify(fabricated)

    assert result.trusted is False
    assert result.reason == "active_lease_mismatch"


def test_active_lease_comparison_includes_nonsecret_identity_fields() -> None:
    active = make_lease()
    fabricated = make_lease(node_pid=5678)
    result = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(pid, 100.0, EXPECTED_COMMAND),
        listener=5678,
        active_lease=active,
    ).verify(fabricated)

    assert result.trusted is False
    assert result.reason == "active_lease_mismatch"


def test_replaced_active_lease_revokes_previously_valid_candidate() -> None:
    original = make_lease()
    active = [original]
    verifier = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(pid, 100.0, EXPECTED_COMMAND),
        active_lease_provider=lambda: active[0],
    )
    assert verifier.verify(original).trusted is True

    active[0] = make_lease(
        launch_nonce="b" * 64,
        node_pid=5678,
        node_process_creation_time=200.0,
    )
    result = verifier.verify(original)

    assert result.trusted is False
    assert result.reason == "active_lease_mismatch"


def test_dead_process_is_not_trusted() -> None:
    result = make_verifier(snapshot=lambda _pid: None).verify(make_lease())

    assert result.trusted is False
    assert result.reason == "process_not_running"


def test_command_mismatch_is_not_trusted() -> None:
    altered_command = list(EXPECTED_COMMAND)
    altered_command[2] = "serve"
    result = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(pid, 100.0, tuple(altered_command)),
    ).verify(make_lease())

    assert result.trusted is False
    assert result.reason == "command_mismatch"


def test_windows_command_path_variants_are_normalized() -> None:
    windows_command = (
        "C:\\Program Files\\nodejs\\NODE.EXE",
        EXPECTED_COMMAND[1].replace("/", chr(92)).upper(),
        "preview",
        "--host",
        "127.0.0.1",
        "--port",
        "5174",
        "--strictPort",
    )
    result = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(pid, 100.0, windows_command),
    ).verify(make_lease())

    assert result.trusted is True
    assert result.reason == "owned_preview_verified"


def test_declared_command_fingerprint_must_match_verifier_contract() -> None:
    lease = make_lease(command_fingerprint="other-preview-v1")
    result = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(pid, 100.0, EXPECTED_COMMAND),
        active_lease=lease,
    ).verify(lease)

    assert result.trusted is False
    assert result.reason == "command_mismatch"


def test_active_lease_for_noncanonical_app_root_is_not_trusted() -> None:
    lease = make_lease(app_root="C:/OtherApp")
    other_app_command = (
        "node",
        "C:/OtherApp/node_modules/vite/bin/vite.js",
        "preview",
        "--host",
        "127.0.0.1",
        "--port",
        "5174",
        "--strictPort",
    )
    result = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(pid, 100.0, other_app_command),
        active_lease=lease,
    ).verify(lease)

    assert result.trusted is False
    assert result.reason == "app_root_mismatch"


def test_lease_must_target_the_fixed_loopback_preview_endpoint() -> None:
    lease = make_lease(host="0.0.0.0")
    result = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(
            pid,
            100.0,
            (
                "node",
                (ROOT / "components/nana-app/node_modules/vite/bin/vite.js").as_posix(),
                "preview",
                "--host",
                "0.0.0.0",
                "--port",
                "5174",
                "--strictPort",
            ),
        ),
        active_lease=lease,
    ).verify(lease)

    assert result.trusted is False
    assert result.reason == "endpoint_mismatch"


def test_wrong_listener_pid_is_not_trusted() -> None:
    result = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(pid, 100.0, EXPECTED_COMMAND),
        listener=9999,
    ).verify(make_lease())

    assert result.trusted is False
    assert result.reason == "listener_identity_mismatch"


def test_lease_from_another_core_boot_is_not_trusted() -> None:
    result = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(pid, 100.0, EXPECTED_COMMAND),
        boot_id="44444444-4444-4444-8444-444444444444",
    ).verify(make_lease())

    assert result.trusted is False
    assert result.reason == "core_boot_mismatch"


def test_malformed_nonce_fails_closed_without_raw_type_error() -> None:
    result = make_verifier(
        snapshot=lambda pid: ProcessSnapshot(pid, 100.0, EXPECTED_COMMAND),
    ).verify(make_lease(launch_nonce=None))

    assert result.trusted is False
    assert result.reason == "invalid_lease"


def test_lease_repr_never_contains_launch_nonce() -> None:
    nonce = "f" * 64
    rendered = repr(make_lease(launch_nonce=nonce))

    assert nonce not in rendered
    assert "launch_nonce" not in rendered


@dataclass(frozen=True)
class FakeConnection:
    laddr: tuple[str, int]
    status: str
    pid: int | None


def test_tcp_listener_lookup_uses_only_injected_connection_snapshot() -> None:
    calls = []

    def net_connections(*, kind):
        calls.append(kind)
        return (
            FakeConnection(("127.0.0.1", 5174), "ESTABLISHED", 7000),
            FakeConnection(("0.0.0.0", 5174), "LISTEN", 7001),
            FakeConnection(("127.0.0.1", 5174), "LISTEN", 1234),
        )

    assert tcp_listener_pid(
        "127.0.0.1",
        5174,
        net_connections=net_connections,
    ) == 1234
    assert calls == ["tcp"]


def test_exact_listener_with_pidless_row_fails_closed() -> None:
    def net_connections(*, kind):
        assert kind == "tcp"
        return (
            FakeConnection(("127.0.0.1", 5174), "LISTEN", 1234),
            FakeConnection(("127.0.0.1", 5174), "LISTEN", None),
        )

    assert tcp_listener_pid(
        "127.0.0.1",
        5174,
        net_connections=net_connections,
    ) is None


def main() -> None:
    tests = [
        test_owned_preview_requires_process_and_listener_identity,
        test_candidate_without_active_launcher_lease_is_not_trusted,
        test_fabricated_candidate_cannot_replace_launcher_authority,
        test_active_lease_comparison_includes_nonsecret_identity_fields,
        test_replaced_active_lease_revokes_previously_valid_candidate,
        test_dead_process_is_not_trusted,
        test_command_mismatch_is_not_trusted,
        test_windows_command_path_variants_are_normalized,
        test_declared_command_fingerprint_must_match_verifier_contract,
        test_active_lease_for_noncanonical_app_root_is_not_trusted,
        test_lease_must_target_the_fixed_loopback_preview_endpoint,
        test_wrong_listener_pid_is_not_trusted,
        test_lease_from_another_core_boot_is_not_trusted,
        test_malformed_nonce_fails_closed_without_raw_type_error,
        test_lease_repr_never_contains_launch_nonce,
        test_tcp_listener_lookup_uses_only_injected_connection_snapshot,
        test_exact_listener_with_pidless_row_fails_closed,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_nana_web_ownership: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
