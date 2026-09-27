import threading
import time
from pathlib import Path

from nana.config import BASE_DIR

LOG_DIR = BASE_DIR / "runtime_logs"
LOG_FILES = {
    "errors": "errors.log",
    "voice": "voice.log",
    "runtime": "runtime.log",
    "vts": "vts.log",
    "watcher": "watcher.log",
}

_log_lock = threading.Lock()


def log_event(channel, message):
    filename = LOG_FILES.get(channel, "runtime.log")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}][{channel.upper()}] {message}\n"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _log_lock:
            with (LOG_DIR / filename).open("a", encoding="utf-8") as file:
                file.write(line)
                file.flush()
    except Exception:
        pass
