"""Offline operator-entry smoke; never starts a provider or OBS stream."""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "tools" / "start_stream_voice_session.py"


def load_entry():
    if not ENTRY.is_file():
        raise AssertionError("Step 7 operator entry is missing")
    for name in ("nana", "nana.runtime", "nana.tools"):
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = [str(ROOT.joinpath(*name.split('.')[1:]))]
            sys.modules[name] = module
    spec = importlib.util.spec_from_file_location("stream_session_cli_under_test", ENTRY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SessionCliTests(unittest.TestCase):
    def setUp(self):
        self.cli = load_entry()

    def test_supported_urls_produce_one_canonical_video_id(self):
        for value in ("AbCdef_12-3", "https://youtube.com/live/AbCdef_12-3?feature=share",
                      "https://www.youtube.com/watch?v=AbCdef_12-3&t=5",
                      "https://youtu.be/AbCdef_12-3", "https://m.youtube.com/watch?v=AbCdef_12-3"):
            with self.subTest(value=value):
                self.assertEqual(self.cli.parse_video_id(value), "AbCdef_12-3")
        for value in ("", "bad-id", "https://youtube.com.evil.test/live/AbCdef_12-3",
                      "https://user@youtube.com/live/AbCdef_12-3", "file:///secret",
                      "https://youtube.com/watch?v=AbCdef_12-3&v=other-value",
                      "https://youtube.com/live/AbCdef_12-3/extra"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.cli.parse_video_id(value)

    def test_live_flags_are_scoped_and_restored_on_failure(self):
        original = dict(os.environ)
        with self.assertRaisesRegex(RuntimeError, "fixture"), self.cli.session_flags():
            self.assertEqual(os.environ["NANA_STREAM_CUM3_YOUTUBE_OUTPUT_ENABLED"], "0")
            self.assertEqual(os.environ["NANA_STREAM_CUM5_VOICE_PLAYBACK_ENABLED"], "1")
            self.assertEqual(os.environ["NANA_MEMORY_PROMOTION_ENABLED"], "0")
            raise RuntimeError("fixture")
        self.assertEqual(dict(os.environ), original)

    def test_exclusive_lock_rejects_second_owner_and_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.lock"
            with self.cli.SessionLock(path):
                with self.assertRaises(self.cli.SessionAlreadyRunning):
                    with self.cli.SessionLock(path):
                        self.fail("duplicate acquired")
            with self.cli.SessionLock(path):
                pass

    def test_ctrl_c_requests_graceful_stop_and_restores_signal_handler(self):
        stops = []
        control = types.SimpleNamespace(request_stop=lambda reason: stops.append(reason))
        previous = signal.getsignal(signal.SIGINT)
        with self.cli.graceful_signals(control):
            signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
            self.assertEqual(stops, ["operator_stop"])
        self.assertIs(signal.getsignal(signal.SIGINT), previous)

    def test_process_exit_releases_lock_without_deleting_another_owners_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.lock"
            code = ("import importlib.util,time,pathlib; "
                    "s=importlib.util.spec_from_file_location('entry'," + repr(str(ENTRY)) + "); "
                    "m=importlib.util.module_from_spec(s);s.loader.exec_module(m); "
                    "lock=m.SessionLock(pathlib.Path(" + repr(str(path)) + "));lock.__enter__(); "
                    "print('locked',flush=True);time.sleep(2)")
            child = subprocess.Popen([sys.executable, "-u", "-B", "-c", code],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "locked")
                with self.assertRaises(self.cli.SessionAlreadyRunning):
                    with self.cli.SessionLock(path):
                        self.fail("second process acquired")
                child.communicate(timeout=6)
                self.assertEqual(child.returncode, 0)
                with self.cli.SessionLock(path):
                    self.assertTrue(path.exists())
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.communicate(timeout=5)

    def test_bad_input_fails_before_session_runtime_is_loaded(self):
        with patch.object(self.cli, "execute_session", side_effect=AssertionError("provider entered")), redirect_stdout(io.StringIO()) as output:
            result = self.cli.main(["https://invalid.example/live/AbCdef_12-3"])
        self.assertEqual(result, 2)
        self.assertIn("invalid_video_url", output.getvalue())

    @unittest.skipUnless(os.name == "nt", "Windows launcher")
    def test_powershell_entry_runs_fake_session_and_restores_title(self):
        script = ROOT / "tools" / "start_stream_voice_host.ps1"
        command = ("$before=$Host.UI.RawUI.WindowTitle; "
                   "& '" + str(script).replace("'", "''") + "' -Demo -PublicVisualSignals -InCurrentWindow -NoPause -PythonPath '"
                   + sys.executable.replace("'", "''") + "'; "
                   "$runExit=$LASTEXITCODE; "
                   "if($Host.UI.RawUI.WindowTitle -ne $before){throw 'title_not_restored'}; exit $runExit")
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run([str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=35)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        self.assertEqual(records[-1]["turns_delivered"], 3)
        self.assertEqual(records[-1]["fake_playback_calls"], 3)
        self.assertTrue(records[-1]["flags_restored"])
        self.assertIn("Public visual signals: enabled for this session.", result.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows console signals")
    def test_windows_console_ctrl_c_reaches_python_and_releases_session(self):
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            # Slow only the fake demo's short wait, leaving time for a real OS
            # signal. A set Event still wakes immediately; no provider is used.
            (fixture / "sitecustomize.py").write_text(
                "import threading\noriginal_wait=threading.Event.wait\n"
                "def longer_wait(self, timeout=None):\n"
                "    return original_wait(self, 5 if timeout is not None and 0 < timeout < 1 else timeout)\n"
                "threading.Event.wait=longer_wait\n", encoding="utf-8")
            output = fixture / "console.log"
            env = {**os.environ, "PYTHONPATH": directory, "PYTHONUTF8": "1", "TEMP": directory, "TMP": directory}
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            command = [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                       str(ROOT / "tools/start_stream_voice_host.ps1"), "-Demo", "-InCurrentWindow",
                       "-NoPause", "-PythonPath", sys.executable]
            with output.open("wb") as sink:
                child = subprocess.Popen(command, stdout=sink, stderr=sink, env=env,
                                         creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
                try:
                    until = time.monotonic() + 12
                    while time.monotonic() < until:
                        content = output.read_text(encoding="utf-8", errors="replace")
                        if "Dang cho chat" in content:
                            break
                        if child.poll() is not None:
                            self.fail("demo exited before signal: " + content)
                        time.sleep(0.02)
                    else:
                        self.fail("no waiting marker: " + content)
                    sender = (
                        "import ctypes,sys,time; k=ctypes.WinDLL('kernel32',use_last_error=True); "
                        "k.FreeConsole(); "
                        "assert k.AttachConsole(int(sys.argv[1])),ctypes.get_last_error(); "
                        "assert k.SetConsoleCtrlHandler(None,True); "
                        "assert k.GenerateConsoleCtrlEvent(0,0),ctypes.get_last_error(); "
                        "time.sleep(0.25); k.FreeConsole()")
                    sent = subprocess.run([sys.executable, "-B", "-c", sender, str(child.pid)],
                                          capture_output=True, text=True, timeout=5)
                    self.assertEqual(sent.returncode, 0, sent.stdout + sent.stderr)
                    child.wait(timeout=12)
                    content = output.read_text(encoding="utf-8", errors="replace")
                    records = [json.loads(line) for line in content.splitlines() if line.startswith('{')]
                    self.assertTrue(records, content)
                    self.assertEqual(records[-1]["reason_code"], "operator_stop", content)
                    self.assertEqual(records[-1]["turns_delivered"], 0, content)
                    self.assertTrue(records[-1]["flags_restored"], content)
                    with self.cli.SessionLock(fixture / "nana-voice-host-session.lock"):
                        pass
                finally:
                    if child.poll() is None:
                        child.terminate()
                        child.wait(timeout=5)

    def test_demo_cli_drives_multiple_real_turns_without_external_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "TEMP": directory, "TMP": directory, "PYTHONUTF8": "1"}
            result = subprocess.run([sys.executable, "-B", str(ENTRY), "--demo", "--json-status"],
                                    capture_output=True, text=True, encoding="utf-8",
                                    env=env, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
        summary = records[-1]
        self.assertEqual(summary["evidence_level"], "local_simulation")
        self.assertEqual(summary["turns_delivered"], 3)
        self.assertEqual(summary["fake_playback_calls"], 3)
        self.assertEqual(summary["tts_provider_requests"], 0)
        self.assertEqual(summary["local_audio_sink_writes"], 0)
        self.assertEqual(summary["reason_code"], "demo_complete")
        self.assertEqual(summary["queued"], 0)
        self.assertTrue(summary["flags_restored"])
        states = [r.get("state") for r in records if r.get("type") == "session_status"]
        for state in ("connecting", "waiting", "thinking", "preparing_audio", "speaking", "stopped"):
            self.assertIn(state, states)
        self.assertNotIn("nana.config", summary["loaded_private_modules"])
        self.assertEqual(summary["loaded_private_modules"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
