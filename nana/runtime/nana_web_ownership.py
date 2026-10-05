"""In-memory ownership proof for the Core-started NanaApp preview."""

from __future__ import annotations

from dataclasses import dataclass, field
import hmac
import math
import os
from pathlib import Path
import re
from typing import Callable, Iterable, Sequence
import uuid

import psutil


NANA_WEB_HOST = "127.0.0.1"
NANA_WEB_PORT = 5174
NANA_WEB_APP_ROOT = (Path(__file__).resolve().parents[2] / "components" / "nana-app").as_posix()
EXPECTED_COMMAND_FINGERPRINT = "vite-preview-v1"
EXPECTED_COMMAND = (
    "node",
    f"{NANA_WEB_APP_ROOT}/node_modules/vite/bin/vite.js",
    "preview",
    "--host",
    NANA_WEB_HOST,
    "--port",
    str(NANA_WEB_PORT),
    "--strictPort",
)

_HEX_256 = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    creation_time: float
    command: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", tuple(self.command))


@dataclass(frozen=True)
class NanaWebOwnershipLease:
    core_boot_id: str
    launch_nonce: str = field(repr=False)
    node_pid: int
    node_process_creation_time: float
    app_root: str
    host: str
    port: int
    command_fingerprint: str


@dataclass(frozen=True)
class OwnershipResult:
    trusted: bool
    reason: str


def process_snapshot(pid: int) -> ProcessSnapshot | None:
    """Read the process identity needed for ownership verification."""
    try:
        process = psutil.Process(pid)
        creation_time = float(process.create_time())
        command = tuple(process.cmdline())
    except (psutil.Error, OSError, ValueError, TypeError):
        return None
    return ProcessSnapshot(
        pid=int(process.pid),
        creation_time=creation_time,
        command=command,
    )


def tcp_listener_pid(
    host: str,
    port: int,
    *,
    net_connections: Callable[..., Iterable[object]] | None = None,
) -> int | None:
    """Return the unique PID listening on an exact TCP host and port."""
    inspect_connections = net_connections or psutil.net_connections
    try:
        connections = inspect_connections(kind="tcp")
    except (psutil.Error, OSError, ValueError, TypeError):
        return None

    matches: set[int] = set()
    for connection in connections:
        if str(getattr(connection, "status", "")).upper() != "LISTEN":
            continue
        address = getattr(connection, "laddr", None)
        if isinstance(address, tuple) and len(address) >= 2:
            address_host, address_port = address[0], address[1]
        else:
            address_host = getattr(address, "ip", None)
            address_port = getattr(address, "port", None)
        if address_host != host or address_port != port:
            continue
        pid = getattr(connection, "pid", None)
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            return None
        matches.add(pid)
    if len(matches) != 1:
        return None
    return next(iter(matches))


def create_ownership_lease(
    *,
    core_boot_id: str,
    process: object,
    app_root: Path,
    host: str,
    port: int,
    inspect_process: Callable[[int], ProcessSnapshot | None] = process_snapshot,
    token_hex: Callable[[int], str],
) -> NanaWebOwnershipLease:
    """Record immutable identity for the exact preview handle Core spawned."""
    pid = getattr(process, "pid", None)
    poll = getattr(process, "poll", None)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError("spawned preview has no valid pid")
    if not callable(poll) or poll() is not None:
        raise RuntimeError("spawned preview is not running")

    snapshot = inspect_process(pid)
    if snapshot is None or snapshot.pid != pid:
        raise RuntimeError("spawned preview identity unavailable")
    nonce = token_hex(32)
    if not isinstance(nonce, str) or _HEX_256.fullmatch(nonce) is None:
        raise ValueError("launch nonce must contain 256 random bits")

    return NanaWebOwnershipLease(
        core_boot_id=core_boot_id,
        launch_nonce=nonce,
        node_pid=pid,
        node_process_creation_time=snapshot.creation_time,
        app_root=_portable_path(app_root),
        host=host,
        port=port,
        command_fingerprint=EXPECTED_COMMAND_FINGERPRINT,
    )


class NanaWebOwnershipVerifier:
    def __init__(
        self,
        *,
        process_snapshot: Callable[[int], ProcessSnapshot | None] = process_snapshot,
        listener_pid: Callable[[str, int], int | None] = tcp_listener_pid,
        current_core_boot_id: Callable[[], str],
        active_lease_provider: Callable[[], NanaWebOwnershipLease | None],
    ) -> None:
        self._process_snapshot = process_snapshot
        self._listener_pid = listener_pid
        self._current_core_boot_id = current_core_boot_id
        self._active_lease_provider = active_lease_provider

    def verify(self, lease: NanaWebOwnershipLease) -> OwnershipResult:
        if not _valid_lease_shape(lease):
            return OwnershipResult(False, "invalid_lease")
        try:
            active_lease = self._active_lease_provider()
        except Exception:
            active_lease = None
        if not _valid_lease_shape(active_lease):
            return OwnershipResult(False, "active_lease_unavailable")
        if not _leases_match(lease, active_lease):
            return OwnershipResult(False, "active_lease_mismatch")
        try:
            current_boot_id = self._current_core_boot_id()
        except Exception:
            return OwnershipResult(False, "core_boot_mismatch")
        if lease.core_boot_id != current_boot_id:
            return OwnershipResult(False, "core_boot_mismatch")
        if _normalized_path(lease.app_root) != _normalized_path(NANA_WEB_APP_ROOT):
            return OwnershipResult(False, "app_root_mismatch")
        if lease.host != NANA_WEB_HOST or lease.port != NANA_WEB_PORT:
            return OwnershipResult(False, "endpoint_mismatch")
        if lease.command_fingerprint != EXPECTED_COMMAND_FINGERPRINT:
            return OwnershipResult(False, "command_mismatch")
        try:
            snapshot = self._process_snapshot(lease.node_pid)
        except Exception:
            snapshot = None
        if snapshot is None:
            return OwnershipResult(False, "process_not_running")
        if (
            snapshot.pid != lease.node_pid
            or snapshot.creation_time != lease.node_process_creation_time
        ):
            return OwnershipResult(False, "process_identity_mismatch")
        if not _matches_vite_preview_command(snapshot.command, lease):
            return OwnershipResult(False, "command_mismatch")

        try:
            listener = self._listener_pid(lease.host, lease.port)
        except Exception:
            listener = None
        if listener != lease.node_pid:
            return OwnershipResult(False, "listener_identity_mismatch")
        return OwnershipResult(True, "owned_preview_verified")


def _leases_match(
    candidate: NanaWebOwnershipLease,
    active: NanaWebOwnershipLease,
) -> bool:
    nonce_matches = hmac.compare_digest(candidate.launch_nonce, active.launch_nonce)
    other_fields_match = (
        candidate.core_boot_id,
        candidate.node_pid,
        candidate.node_process_creation_time,
        candidate.app_root,
        candidate.host,
        candidate.port,
        candidate.command_fingerprint,
    ) == (
        active.core_boot_id,
        active.node_pid,
        active.node_process_creation_time,
        active.app_root,
        active.host,
        active.port,
        active.command_fingerprint,
    )
    return nonce_matches and other_fields_match


def _valid_lease_shape(lease: object) -> bool:
    if not isinstance(lease, NanaWebOwnershipLease):
        return False
    try:
        canonical_boot_id = str(uuid.UUID(lease.core_boot_id))
    except (AttributeError, ValueError, TypeError):
        return False
    return (
        canonical_boot_id == lease.core_boot_id
        and isinstance(lease.launch_nonce, str)
        and _HEX_256.fullmatch(lease.launch_nonce) is not None
        and isinstance(lease.node_pid, int)
        and not isinstance(lease.node_pid, bool)
        and lease.node_pid > 0
        and isinstance(lease.node_process_creation_time, (int, float))
        and not isinstance(lease.node_process_creation_time, bool)
        and math.isfinite(float(lease.node_process_creation_time))
        and lease.node_process_creation_time > 0
        and isinstance(lease.app_root, str)
        and bool(lease.app_root)
        and isinstance(lease.host, str)
        and isinstance(lease.port, int)
        and not isinstance(lease.port, bool)
        and isinstance(lease.command_fingerprint, str)
    )


def _matches_vite_preview_command(
    command: Sequence[str],
    lease: NanaWebOwnershipLease,
) -> bool:
    if isinstance(command, (str, bytes)):
        return False
    try:
        arguments = tuple(str(part).strip('"') for part in command)
    except TypeError:
        return False
    if len(arguments) != len(EXPECTED_COMMAND):
        return False
    executable = Path(arguments[0].replace("\\", "/")).name.casefold()
    if executable not in {"node", "node.exe"}:
        return False
    app_root = lease.app_root.rstrip("/\\")
    expected_vite = _normalized_path(
        f"{app_root}/node_modules/vite/bin/vite.js"
    )
    if _normalized_path(arguments[1]) != expected_vite:
        return False
    expected_tail = (
        "preview",
        "--host",
        lease.host,
        "--port",
        str(lease.port),
        "--strictPort",
    )
    return arguments[2:] == expected_tail


def _portable_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").rstrip("/")


def _normalized_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value)).replace("\\", "/")
