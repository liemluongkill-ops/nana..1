"""Offline smoke for the NanaApp startup/Edge launcher.

All process, HTTP, clock, and browser boundaries are local fakes. The smoke
must never start NanaApp, Edge, a provider, audio, OBS, or network I/O.
"""

from __future__ import annotations

import asyncio
import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += float(seconds)


class FakeProcess:
    def __init__(self, *, pid=1234, returncode=None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.terminate_count = 0
        self.kill_count = 0
        self.wait_count = 0

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminate_count += 1
        self.returncode = 0

    def kill(self) -> None:
        self.kill_count += 1
        self.returncode = -9

    def wait(self, timeout=None):
        self.wait_count += 1
        return self.returncode


class ProbeScript:
    def __init__(self, *states: str) -> None:
        self.states = list(states)
        self.calls = 0

    def __call__(self, _url: str):
        from nana.runtime.nana_web_launcher import NanaWebProbe

        self.calls += 1
        index = min(self.calls - 1, len(self.states) - 1)
        state = self.states[index]
        return NanaWebProbe(state=state, detail=state)


def make_launcher(
    *,
    probe_states,
    enabled=True,
    browser_error=None,
    launch_nonce="a" * 64,
):
    from nana.runtime.nana_web_launcher import NanaWebConfig, NanaWebLauncher
    from nana.runtime.nana_web_ownership import EXPECTED_COMMAND, ProcessSnapshot

    clock = FakeClock()
    process = FakeProcess()
    spawn_calls = []
    browser_calls = []

    def spawn(config):
        spawn_calls.append(config)
        return process

    def open_browser(url):
        browser_calls.append(url)
        if browser_error is not None:
            raise browser_error

    def token_hex(size):
        assert size == 32
        return launch_nonce

    config = NanaWebConfig(
        auto_open_enabled=enabled,
        app_root=(ROOT / "components" / "nana-app"),
        url="http://127.0.0.1:5174/",
        ready_timeout_s=1.0,
        poll_interval_s=0.1,
    )
    launcher = NanaWebLauncher(
        config=config,
        probe=ProbeScript(*probe_states),
        spawn_preview=spawn,
        open_browser=open_browser,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        core_boot_id="33333333-3333-4333-8333-333333333333",
        process_snapshot=lambda pid: ProcessSnapshot(pid, 100.0, EXPECTED_COMMAND),
        token_hex=token_hex,
    )
    return launcher, process, spawn_calls, browser_calls


def test_ready_server_opens_edge_without_spawning() -> None:
    launcher, process, spawn_calls, browser_calls = make_launcher(
        probe_states=("ready",),
    )

    result = launcher.ensure_open()

    assert result.status == "ready_existing", result
    assert result.web_ready is True
    assert result.server_started is False
    assert result.browser_opened is True
    assert spawn_calls == []
    assert browser_calls == ["http://127.0.0.1:5174/"]
    launcher.close()
    assert process.terminate_count == 0


def test_refused_connection_starts_preview_before_opening_edge() -> None:
    launcher, process, spawn_calls, browser_calls = make_launcher(
        probe_states=("unreachable", "unreachable", "ready"),
    )

    result = launcher.ensure_open()

    assert result.status == "ready_started", result
    assert result.web_ready is True
    assert result.server_started is True
    assert result.browser_opened is True
    assert len(spawn_calls) == 1
    assert browser_calls == ["http://127.0.0.1:5174/"]
    launcher.close()
    assert process.terminate_count == 1
    assert process.wait_count == 1


def test_unexpected_service_on_port_fails_closed() -> None:
    launcher, process, spawn_calls, browser_calls = make_launcher(
        probe_states=("unexpected",),
    )

    result = launcher.ensure_open()

    assert result.status == "port_conflict", result
    assert result.web_ready is False
    assert spawn_calls == []
    assert browser_calls == []
    launcher.close()
    assert process.terminate_count == 0


def test_repeated_auto_open_is_idempotent() -> None:
    launcher, _process, spawn_calls, browser_calls = make_launcher(
        probe_states=("ready", "ready"),
    )

    first = launcher.ensure_open()
    second = launcher.ensure_open()

    assert first.browser_opened is True
    assert second.status == "ready_already_open"
    assert second.browser_opened is False
    assert spawn_calls == []
    assert browser_calls == ["http://127.0.0.1:5174/"]


def test_auto_open_flag_does_not_block_explicit_web_command() -> None:
    launcher, _process, spawn_calls, browser_calls = make_launcher(
        probe_states=("ready",),
        enabled=False,
    )

    automatic = launcher.ensure_open()
    manual = launcher.ensure_open(manual=True, reopen=True)

    assert automatic.status == "disabled"
    assert manual.status == "ready_existing"
    assert spawn_calls == []
    assert browser_calls == ["http://127.0.0.1:5174/"]


def test_browser_failure_is_soft_after_web_is_ready() -> None:
    launcher, process, spawn_calls, browser_calls = make_launcher(
        probe_states=("unreachable", "ready"),
        browser_error=OSError("edge unavailable"),
    )

    result = launcher.ensure_open()

    assert result.status == "browser_failed", result
    assert result.web_ready is True
    assert result.server_started is True
    assert result.browser_opened is False
    assert result.reason == "OSError: edge unavailable"
    assert len(spawn_calls) == 1
    assert len(browser_calls) == 1
    launcher.close()
    assert process.terminate_count == 1


def test_started_preview_exposes_lease_only_while_exact_handle_is_alive() -> None:
    launcher, process, _spawn_calls, _browser_calls = make_launcher(
        probe_states=("unreachable", "ready"),
    )

    result = launcher.ensure_open()
    lease = launcher.ownership_lease()

    assert result.status == "ready_started"
    assert lease is not None
    assert lease.core_boot_id == "33333333-3333-4333-8333-333333333333"
    assert lease.launch_nonce == "a" * 64
    assert lease.node_pid == process.pid
    assert lease.node_process_creation_time == 100.0
    assert lease.app_root == (ROOT / "components" / "nana-app").as_posix()
    assert lease.host == "127.0.0.1"
    assert lease.port == 5174
    assert lease.command_fingerprint == "vite-preview-v1"

    process.returncode = 1
    assert launcher.ownership_lease() is None


def test_reused_external_server_never_receives_an_ownership_lease() -> None:
    launcher, process, spawn_calls, _browser_calls = make_launcher(
        probe_states=("ready",),
    )

    result = launcher.ensure_open()

    assert result.status == "ready_existing"
    assert spawn_calls == []
    assert launcher.ownership_lease() is None
    launcher.close()
    assert process.terminate_count == 0


def test_close_revokes_owned_preview_lease() -> None:
    launcher, process, _spawn_calls, _browser_calls = make_launcher(
        probe_states=("unreachable", "ready"),
    )
    assert launcher.ensure_open().status == "ready_started"
    assert launcher.ownership_lease() is not None

    launcher.close()

    assert launcher.ownership_lease() is None
    assert process.terminate_count == 1


def test_vite_and_edge_commands_never_contain_launch_nonce() -> None:
    from nana.runtime import nana_web_launcher as launcher_module
    from nana.runtime.nana_web_launcher import NanaWebConfig

    nonce = "f" * 64
    popen_calls = []

    def fake_popen(command, **kwargs):
        popen_calls.append((list(command), kwargs))
        return FakeProcess(pid=4321)

    original_which = launcher_module.shutil.which
    original_popen = launcher_module.subprocess.Popen
    original_find_edge = launcher_module._find_edge
    try:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            dist_index = root / "dist" / "index.html"
            vite_entry = root / "node_modules" / "vite" / "bin" / "vite.js"
            dist_index.parent.mkdir(parents=True)
            vite_entry.parent.mkdir(parents=True)
            dist_index.write_text("Nana", encoding="utf-8")
            vite_entry.write_text("// vite fixture", encoding="utf-8")

            node = "C:/Tools/node.exe"
            edge = Path("C:/Tools/msedge.exe")
            config = NanaWebConfig(app_root=root)
            launcher_module.shutil.which = lambda executable: node
            launcher_module.subprocess.Popen = fake_popen
            launcher_module._find_edge = lambda: edge

            launcher_module._spawn_vite_preview(config)
            launcher_module._open_edge_app(config.url)

            expected_vite_command = [
                node,
                str(vite_entry.resolve()),
                "preview",
                "--host",
                "127.0.0.1",
                "--port",
                "5174",
                "--strictPort",
            ]
            assert popen_calls[0][0] == expected_vite_command
            assert popen_calls[1][0] == [str(edge), f"--app={config.url}"]
            assert all(nonce not in " ".join(call[0]) for call in popen_calls)
    finally:
        launcher_module.shutil.which = original_which
        launcher_module.subprocess.Popen = original_popen
        launcher_module._find_edge = original_find_edge


def test_launcher_browser_url_and_result_formatting_never_expose_nonce() -> None:
    from nana.runtime.nana_web_launcher import format_nana_web_result

    nonce = "f" * 64
    launcher, _process, _spawn_calls, browser_calls = make_launcher(
        probe_states=("unreachable", "ready"),
        launch_nonce=nonce,
    )

    result = launcher.ensure_open()
    lease = launcher.ownership_lease()
    rendered = format_nana_web_result(result, url=launcher.config.url)

    assert lease is not None
    assert lease.launch_nonce == nonce
    assert browser_calls == ["http://127.0.0.1:5174/"]
    assert nonce not in launcher.config.url
    assert nonce not in repr(launcher)
    assert nonce not in repr(result)
    assert nonce not in rendered
    launcher.close()


def test_web_command_routes_manual_reopen_and_unknown_input_falls_through() -> None:
    from nana.cli import web_commands
    from nana.runtime.nana_web_launcher import NanaWebLaunchResult

    calls = []

    def ensure_web(*, manual=False, reopen=False):
        calls.append((manual, reopen))
        return NanaWebLaunchResult(
            status="ready_existing",
            web_ready=True,
            server_started=False,
            browser_opened=True,
            reason="",
        )

    original = web_commands.ensure_nana_web_open
    try:
        web_commands.ensure_nana_web_open = ensure_web
        output = io.StringIO()
        with redirect_stdout(output):
            assert web_commands.handle_web_command("/web") is True
            assert web_commands.handle_web_command("/not-web") is False
    finally:
        web_commands.ensure_nana_web_open = original

    assert calls == [(True, True)]
    assert "Nana Web" in output.getvalue()
    assert "127.0.0.1:5174" in output.getvalue()


def test_mo_ai_ensures_web_without_duplicate_reopen_when_already_awake() -> None:
    from nana.cli import globals as cli_globals
    from nana.cli import chat_turn_pipeline
    from nana.runtime.nana_web_launcher import NanaWebLaunchResult

    calls = []

    def open_web(*, manual=False, reopen=False):
        calls.append((manual, reopen))
        return NanaWebLaunchResult(
            status="ready_already_open",
            web_ready=True,
            server_started=False,
            browser_opened=False,
            reason="",
        )

    original = chat_turn_pipeline.open_nana_web
    previous_active = cli_globals.ai_active
    try:
        chat_turn_pipeline.open_nana_web = open_web
        cli_globals.ai_active = True
        result = asyncio.run(
            chat_turn_pipeline.handle_chat_turn(None, None, "mở ai", None, "mở ai")
        )
    finally:
        chat_turn_pipeline.open_nana_web = original
        cli_globals.ai_active = previous_active

    assert result is False
    assert calls == [(True, False)]


def test_mo_ai_reopens_edge_when_waking_from_sleep() -> None:
    from nana.cli import globals as cli_globals
    from nana.cli import chat_turn_pipeline
    from nana.runtime.nana_web_launcher import NanaWebLaunchResult

    calls = []

    def open_web(*, manual=False, reopen=False):
        calls.append((manual, reopen))
        return NanaWebLaunchResult(
            status="ready_existing",
            web_ready=True,
            server_started=False,
            browser_opened=True,
            reason="",
        )

    original = chat_turn_pipeline.open_nana_web
    previous_active = cli_globals.ai_active
    try:
        chat_turn_pipeline.open_nana_web = open_web
        cli_globals.ai_active = False
        result = asyncio.run(
            chat_turn_pipeline.handle_chat_turn(None, None, "mở ai", None, "mở ai")
        )
    finally:
        chat_turn_pipeline.open_nana_web = original
        cli_globals.ai_active = previous_active

    assert result is False
    assert calls == [(True, True)]


def test_core_startup_wrapper_uses_the_same_launcher() -> None:
    from nana.cli import app
    from nana.runtime.nana_web_launcher import NanaWebLaunchResult

    class FakeLauncher:
        def __init__(self):
            self.ensure_count = 0

        def ensure_open(self):
            self.ensure_count += 1
            return NanaWebLaunchResult(
                status="ready_started",
                web_ready=True,
                server_started=True,
                browser_opened=True,
                reason="",
            )

    launcher = FakeLauncher()
    output = io.StringIO()
    with redirect_stdout(output):
        returned = app._start_nana_web_launcher(launcher)

    assert returned is launcher
    assert launcher.ensure_count == 1
    assert "server=started" in output.getvalue()
    assert "Edge=opened" in output.getvalue()


def main() -> None:
    tests = [
        test_ready_server_opens_edge_without_spawning,
        test_refused_connection_starts_preview_before_opening_edge,
        test_unexpected_service_on_port_fails_closed,
        test_repeated_auto_open_is_idempotent,
        test_auto_open_flag_does_not_block_explicit_web_command,
        test_browser_failure_is_soft_after_web_is_ready,
        test_started_preview_exposes_lease_only_while_exact_handle_is_alive,
        test_reused_external_server_never_receives_an_ownership_lease,
        test_close_revokes_owned_preview_lease,
        test_vite_and_edge_commands_never_contain_launch_nonce,
        test_launcher_browser_url_and_result_formatting_never_expose_nonce,
        test_web_command_routes_manual_reopen_and_unknown_input_falls_through,
        test_mo_ai_ensures_web_without_duplicate_reopen_when_already_awake,
        test_mo_ai_reopens_edge_when_waking_from_sleep,
        test_core_startup_wrapper_uses_the_same_launcher,
    ]
    for index, test in enumerate(tests, 1):
        print(f"[{index}/{len(tests)}] {test.__name__}")
        test()
    print(f"smoke_nana_web_launcher: PASS ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
