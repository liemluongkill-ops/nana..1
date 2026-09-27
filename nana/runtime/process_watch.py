import asyncio
import threading
import time

from nana.config import RUNTIME_REACTION_COOLDOWN
from nana.integrations.vts import trigger_expression_lifecycle
from nana.runtime.context import accumulate_confidence
from nana.runtime.logger import log_event

runtime_lock = threading.Lock()

runtime_state = {
    "current_app": None,
    "running_command": None,
    "error_count": 0,
    "success_streak": 0,
    "last_error": "",
    "last_success": "",
    "last_reaction_time": 0,
    "last_log_time": 0,
    "process_running": False,
}

ERROR_PATTERNS = [
    "traceback (most recent",
    "error:",
    "exception:",
    "failed:",
    "modulenotfounderror",
    "typeerror:",
    "valueerror:",
    "connectionrefusederror",
    "winerror",
]

SUCCESS_PATTERNS = [
    "successfully",
    "thành công",
    "passed",
    " connected",
    "finished ok",
    "process complete",
]


def parse_log_line(line):
    lowered = line.lower()
    if any(pattern in lowered for pattern in ERROR_PATTERNS):
        return "error"
    if any(pattern in lowered for pattern in SUCCESS_PATTERNS):
        return "success"
    return None


def update_runtime_state(line):
    event = parse_log_line(line)
    if not event:
        return None
    with runtime_lock:
        runtime_state["last_log_time"] = time.time()
        if event == "error":
            runtime_state["error_count"] += 1
            runtime_state["success_streak"] = 0
            runtime_state["last_error"] = line.strip()
        elif event == "success":
            runtime_state["success_streak"] += 1
            runtime_state["error_count"] = max(0, runtime_state["error_count"] - 1)
            runtime_state["last_success"] = line.strip()
        return {
            "event": event,
            "error_count": runtime_state["error_count"],
            "success_streak": runtime_state["success_streak"],
            "line": line.strip(),
        }


def build_runtime_reaction(event_data):
    event = event_data["event"]
    error_count = event_data["error_count"]
    success_streak = event_data["success_streak"]
    if event == "error":
        if error_count == 1:
            return "Đợi tí... log này có lỗi thật rồi. Nana nhìn thấy dấu hiệu lỗi trong terminal."
        if error_count >= 5:
            return "Khônggg, con bug này stubborn thật đấy 🤣 gửi Nana đoạn traceback chính đi."
        if error_count >= 3:
            return "Lại tới bug arc nữa rồi đó... nhìn giống lỗi runtime hơn là lỗi nhỏ."
    if event == "success" and success_streak >= 2:
        return "Ổn rồi đó 😏 terminal có tín hiệu qua được đoạn này."
    return None


async def maybe_runtime_react(vts, voice, event_data):
    reaction = build_runtime_reaction(event_data)
    if not reaction:
        return
    now = time.time()
    with runtime_lock:
        if now - runtime_state["last_reaction_time"] < RUNTIME_REACTION_COOLDOWN:
            return
        runtime_state["last_reaction_time"] = now
    if event_data["event"] == "error":
        accumulate_confidence("terminal_error", 0.4)
    print("🤖 Nana:", reaction)
    log_event("watcher", f"Runtime reaction ({event_data['event']}): {reaction}")
    voice.say(reaction)
    asyncio.create_task(trigger_expression_lifecycle(vts, reaction, voice=voice, reason="runtime_reaction"))


async def watch_process(command, vts, voice):
    print(f"🖥 Nana đang chạy: {command}")
    log_event("watcher", f"Process started: {command}")
    with runtime_lock:
        runtime_state["running_command"] = command
        runtime_state["current_app"] = command.split()[0] if command.split() else "unknown"
        runtime_state["process_running"] = True
        runtime_state["error_count"] = 0
        runtime_state["success_streak"] = 0
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        while True:
            raw = await process.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                print(f"📟 {line}")
            event_data = update_runtime_state(line)
            if event_data:
                log_event("watcher", f"Detected {event_data['event']}: {event_data['line']}")
                await maybe_runtime_react(vts, voice, event_data)
        code = await process.wait()
        with runtime_lock:
            runtime_state["process_running"] = False
        if code == 0:
            event_data = update_runtime_state("success: process complete")
            if event_data:
                await maybe_runtime_react(vts, voice, event_data)
            print("✅ Process finished OK")
            log_event("watcher", f"Process finished OK: {command}")
        else:
            event_data = update_runtime_state(f"error: process exited with code {code}")
            if event_data:
                await maybe_runtime_react(vts, voice, event_data)
            print(f"❌ Process exited with code {code}")
            log_event("watcher", f"Process exited code={code}: {command}")
    except Exception as exc:
        with runtime_lock:
            runtime_state["process_running"] = False
        print("❌ Watch process lỗi:", exc)
        log_event("watcher", f"Watch process error: {exc}")
        event_data = update_runtime_state(f"error: {exc}")
        if event_data:
            await maybe_runtime_react(vts, voice, event_data)
