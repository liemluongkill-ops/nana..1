"""Run one focused Stream smoke with private data and external I/O blocked.

Use the desktop Python 3.11 environment for --suite adapter. Other suites also
work in the Python 3.12 pilot environment. This never starts Nana's facade.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import runpy
import subprocess
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    "delivery": "smoke_memory_v2_delivery.py",
    "controller": "smoke_stream_cum5_voice_playback.py",
    "adapter": "smoke_stream_cum5_voice_engine_adapter.py",
    "text-host": "smoke_stream_cum4_host.py",
    "publisher": "smoke_stream_cum3_youtube_publish.py",
    "voice-host": "smoke_stream_voice_host.py",
    "simulator": "smoke_youtube_chat_simulator.py",
    "runner": "smoke_stream_voice_runner.py",
    "session-control": "smoke_stream_session_control.py",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=tuple(SUITES), required=True)
    args = parser.parse_args(argv)
    protected = os.path.normcase(str(ROOT / "data")) + os.sep

    def guard(event, values):
        if event == "open" and isinstance(values[0], (str, bytes, os.PathLike)):
            path = os.path.normcase(os.path.abspath(os.fsdecode(values[0])))
            if path.startswith(protected):
                raise AssertionError("Stream smoke attempted production data access")
            if os.path.basename(path) == ".env":
                # The optional config loader already treats an unreadable env
                # file as absent. Defaults remain real; credentials stay unread.
                raise PermissionError("Stream smoke does not load .env")
        if event in {"socket.connect", "socket.bind"}:
            frame = sys._getframe(1)
            if (frame.f_code.co_name in {"socketpair", "_fallback_socketpair"}
                    and Path(frame.f_code.co_filename).name == "socket.py"):
                return
            raise AssertionError("Stream smoke attempted network access")
        if event in {"socket.sendto", "os.system"}:
            raise AssertionError("Stream smoke attempted external I/O")
        if event == "subprocess.Popen":
            # These two suites have guarded fresh-process CLI probes. Permit
            # only their Python/file pair, never another app or a shell.
            command = values[1]
            child_files = {
                "runner": ROOT / "tools" / "run_stream_voice_host.py",
                "simulator": ROOT / "runtime" / "youtube_chat_simulator.py",
            }
            if args.suite not in child_files:
                raise AssertionError("Stream smoke attempted an external process")
            expected_runner = str(child_files[args.suite])
            expected = [sys.executable, "-B", expected_runner]
            matches = (isinstance(command, (list, tuple)) and list(command[:3]) == expected)
            if isinstance(command, str):
                matches = command.startswith(subprocess.list2cmdline(expected) + " ")
            if matches:
                return
            raise AssertionError("Stream smoke attempted an external process")

    sys.addaudithook(guard)
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(ROOT.parent), str(ROOT / "tests" / "smoke")]
    names = ["nana", "nana.runtime", "nana.tests", "nana.tests.smoke"]
    if args.suite == "adapter":
        names.extend(("nana.brain", "nana.voice"))
    for name in names:
        package = types.ModuleType(name)
        package.__path__ = [str(ROOT.joinpath(*name.split(".")[1:]))]
        sys.modules[name] = package
        parent, _, child = name.rpartition(".")
        if parent:
            setattr(sys.modules[parent], child, package)
    filename = ROOT / "tests" / "smoke" / SUITES[args.suite]
    sys.argv = [str(filename)]
    try:
        runpy.run_path(str(filename), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
