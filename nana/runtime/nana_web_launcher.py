"""Owned NanaApp preview lifecycle and Edge launch boundary.

The launcher is intentionally local-only. It verifies the NanaApp document
before opening a browser, owns only preview processes it starts itself, and
never reads browser credentials or profile data.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time
from typing import Callable, Protocol
import urllib.error
import urllib.parse
import urllib.request
import uuid

from nana.runtime.nana_web_ownership import (
    NANA_WEB_APP_ROOT,
    NanaWebOwnershipLease,
    ProcessSnapshot,
    create_ownership_lease,
    process_snapshot,
)


NANA_WEB_URL = "http://127.0.0.1:5174/"
NANA_WEB_MARKERS = ("<title>Nana</title>", 'id="chat-composer"')


class ProcessHandle(Protocol):
    pid: int

    def poll(self): ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout=None): ...


@dataclass(frozen=True)
class NanaWebConfig:
    auto_open_enabled: bool = True
    app_root: Path = Path(NANA_WEB_APP_ROOT)
    url: str = NANA_WEB_URL
    ready_timeout_s: float = 10.0
    poll_interval_s: float = 0.1

    @classmethod
    def from_environment(cls) -> "NanaWebConfig":
        raw_enabled = os.getenv("NANA_WEB_AUTO_OPEN_ENABLED", "1")
        enabled = str(raw_enabled).strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
            "disabled",
        }
        app_root = Path(os.getenv("NANA_WEB_APP_ROOT", NANA_WEB_APP_ROOT))
        return cls(auto_open_enabled=enabled, app_root=app_root)


@dataclass(frozen=True)
class NanaWebProbe:
    state: str
    detail: str = ""

    def __post_init__(self) -> None:
        if self.state not in {"ready", "unreachable", "unexpected"}:
            raise ValueError(f"unsupported Nana web probe state: {self.state}")


@dataclass(frozen=True)
class NanaWebLaunchResult:
    status: str
    web_ready: bool
    server_started: bool
    browser_opened: bool
    reason: str = ""


def probe_nana_web(url: str) -> NanaWebProbe:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "NanaCore-WebLauncher/1"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=0.6) as response:
            status = int(response.getcode() or 0)
            body = response.read(131072).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return NanaWebProbe("unexpected", f"http_{exc.code}")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
        return NanaWebProbe("unreachable", type(exc).__name__)

    if status != 200:
        return NanaWebProbe("unexpected", f"http_{status}")
    if not all(marker in body for marker in NANA_WEB_MARKERS):
        return NanaWebProbe("unexpected", "nana_document_marker_missing")
    return NanaWebProbe("ready", "nana_document_verified")


def _spawn_vite_preview(config: NanaWebConfig) -> ProcessHandle:
    root = config.app_root.resolve()
    dist_index = root / "dist" / "index.html"
    vite_entry = root / "node_modules" / "vite" / "bin" / "vite.js"
    node = shutil.which("node")
    if not root.is_dir():
        raise FileNotFoundError(f"NanaApp root missing: {root}")
    if not dist_index.is_file():
        raise FileNotFoundError(f"NanaApp build missing: {dist_index}")
    if not vite_entry.is_file():
        raise FileNotFoundError(f"Vite entry missing: {vite_entry}")
    if not node:
        raise FileNotFoundError("node executable not found")

    command = [
        node,
        str(vite_entry),
        "preview",
        "--host",
        "127.0.0.1",
        "--port",
        "5174",
        "--strictPort",
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        command,
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )


def _find_edge() -> Path:
    candidates = []
    for env_name in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
        base = os.getenv(env_name)
        if base:
            candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    resolved = shutil.which("msedge.exe") or shutil.which("msedge")
    if resolved:
        candidates.append(Path(resolved))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Microsoft Edge executable not found")


def _open_edge_app(url: str) -> None:
    edge = _find_edge()
    subprocess.Popen(
        [str(edge), f"--app={url}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


class NanaWebLauncher:
    def __init__(
        self,
        *,
        config: NanaWebConfig | None = None,
        probe: Callable[[str], NanaWebProbe] = probe_nana_web,
        spawn_preview: Callable[[NanaWebConfig], ProcessHandle] = _spawn_vite_preview,
        open_browser: Callable[[str], None] = _open_edge_app,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        core_boot_id: str | None = None,
        process_snapshot: Callable[[int], ProcessSnapshot | None] = process_snapshot,
        token_hex: Callable[[int], str] = secrets.token_hex,
    ) -> None:
        self.config = config or NanaWebConfig.from_environment()
        self._probe = probe
        self._spawn_preview = spawn_preview
        self._open_browser = open_browser
        self._monotonic = monotonic
        self._sleep = sleep
        self._core_boot_id = core_boot_id or str(uuid.uuid4())
        self._process_snapshot = process_snapshot
        self._token_hex = token_hex
        self._lock = threading.RLock()
        self._owned_process: ProcessHandle | None = None
        self._ownership_lease: NanaWebOwnershipLease | None = None
        self._browser_open_requested = False

    def ensure_open(
        self,
        *,
        manual: bool = False,
        reopen: bool = False,
    ) -> NanaWebLaunchResult:
        with self._lock:
            if not manual and not self.config.auto_open_enabled:
                return NanaWebLaunchResult("disabled", False, False, False)

            first_probe = self._probe(self.config.url)
            if first_probe.state == "unexpected":
                return NanaWebLaunchResult(
                    "port_conflict",
                    False,
                    False,
                    False,
                    first_probe.detail,
                )

            server_started = False
            if first_probe.state != "ready":
                process = self._owned_process
                if process is None or process.poll() is not None:
                    try:
                        process = self._spawn_preview(self.config)
                    except Exception as exc:
                        return NanaWebLaunchResult(
                            "server_start_failed",
                            False,
                            False,
                            False,
                            f"{type(exc).__name__}: {exc}",
                        )
                    self._owned_process = process
                    self._browser_open_requested = False
                    server_started = True
                    try:
                        self._ownership_lease = self._record_ownership_lease(process)
                    except Exception as exc:
                        self._stop_owned_process_locked()
                        return NanaWebLaunchResult(
                            "server_start_failed",
                            False,
                            True,
                            False,
                            f"{type(exc).__name__}: {exc}",
                        )

                ready_result = self._wait_until_ready(process)
                if ready_result is not None:
                    if server_started:
                        self._stop_owned_process_locked()
                    return NanaWebLaunchResult(
                        ready_result.status,
                        False,
                        server_started,
                        False,
                        ready_result.reason,
                    )

            if self._browser_open_requested and not reopen:
                return NanaWebLaunchResult(
                    "ready_already_open",
                    True,
                    server_started,
                    False,
                )

            try:
                self._open_browser(self.config.url)
            except Exception as exc:
                return NanaWebLaunchResult(
                    "browser_failed",
                    True,
                    server_started,
                    False,
                    f"{type(exc).__name__}: {exc}",
                )
            self._browser_open_requested = True
            return NanaWebLaunchResult(
                "ready_started" if server_started else "ready_existing",
                True,
                server_started,
                True,
            )

    @property
    def core_boot_id(self):
        return self._core_boot_id

    def ownership_lease(self) -> NanaWebOwnershipLease | None:
        with self._lock:
            process = self._owned_process
            if process is None or process.poll() is not None:
                self._ownership_lease = None
                return None
            return self._ownership_lease

    def _record_ownership_lease(
        self,
        process: ProcessHandle,
    ) -> NanaWebOwnershipLease:
        parsed_url = urllib.parse.urlsplit(self.config.url)
        host = parsed_url.hostname or ""
        port = parsed_url.port
        if host != "127.0.0.1" or port != 5174:
            raise ValueError("NanaApp preview endpoint must be 127.0.0.1:5174")
        return create_ownership_lease(
            core_boot_id=self._core_boot_id,
            process=process,
            app_root=self.config.app_root,
            host=host,
            port=port,
            inspect_process=self._process_snapshot,
            token_hex=self._token_hex,
        )

    def _wait_until_ready(
        self,
        process: ProcessHandle,
    ) -> NanaWebLaunchResult | None:
        deadline = self._monotonic() + max(0.0, self.config.ready_timeout_s)
        while True:
            probe = self._probe(self.config.url)
            if probe.state == "ready":
                return None
            if probe.state == "unexpected":
                return NanaWebLaunchResult(
                    "port_conflict", False, False, False, probe.detail
                )
            returncode = process.poll()
            if returncode is not None:
                return NanaWebLaunchResult(
                    "server_exited",
                    False,
                    False,
                    False,
                    f"exit_code={returncode}",
                )
            if self._monotonic() >= deadline:
                return NanaWebLaunchResult(
                    "server_timeout",
                    False,
                    False,
                    False,
                    "nana_document_not_ready",
                )
            self._sleep(max(0.0, self.config.poll_interval_s))

    def close(self) -> None:
        with self._lock:
            self._stop_owned_process_locked()
            self._browser_open_requested = False

    def _stop_owned_process_locked(self) -> None:
        process = self._owned_process
        self._owned_process = None
        self._ownership_lease = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)


def format_nana_web_result(result: NanaWebLaunchResult, *, url: str = NANA_WEB_URL) -> str:
    if result.status == "disabled":
        return "Nana Web auto-open: disabled by NANA_WEB_AUTO_OPEN_ENABLED=0"
    if result.web_ready:
        server = "started" if result.server_started else "existing"
        browser = "opened" if result.browser_opened else "already requested"
        if result.status == "browser_failed":
            browser = f"soft-fail ({result.reason})"
        return f"Nana Web: ready | server={server} | Edge={browser} | url={url}"
    return f"Nana Web soft-fail: status={result.status} | reason={result.reason or 'unknown'}"


_singleton_lock = threading.Lock()
_singleton: NanaWebLauncher | None = None
_singleton_core_boot_id: str | None = None


def get_nana_web_launcher() -> NanaWebLauncher:
    global _singleton, _singleton_core_boot_id
    with _singleton_lock:
        if _singleton is None:
            _singleton_core_boot_id = str(uuid.uuid4())
            _singleton = NanaWebLauncher(core_boot_id=_singleton_core_boot_id)
        return _singleton


def ensure_nana_web_open(
    *,
    manual: bool = False,
    reopen: bool = False,
) -> NanaWebLaunchResult:
    return get_nana_web_launcher().ensure_open(manual=manual, reopen=reopen)


def shutdown_nana_web() -> None:
    get_nana_web_launcher().close()
