from __future__ import annotations

import atexit
import csv
import importlib.util
import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from nana.game.osu.bridge import OSU_STATE_PATH_DEFAULT, OSU_STATE_PATH_ENV, read_tosu_state, write_state
from nana.game.osu.input_safety import run_emergency_release

VENDOR_ROOT_ENV = "NANA_OSU_CURSOR_DANCE_ROOT"
KILL_HOTKEY_ENV = "OSU_CURSOR_DANCE_KILL_HOTKEY"
AUTO_ARM_ENV = "OSU_CURSOR_DANCE_AUTO_ARM_ON_START"
AUTO_START_MAX_LATE_ENV = "OSU_CURSOR_DANCE_AUTO_START_MAX_LATE_MS"
SYNC_OFFSET_ENV = "OSU_CURSOR_DANCE_SYNC_OFFSET_MS"
NANA_SYNC_OFFSET_ENV = "NANA_OSU_CURSOR_DANCE_SYNC_OFFSET_MS"
MOVEMENT_PROFILE_ENV = "OSU_CURSOR_DANCE_MOVEMENT_PROFILE"
NANA_MOVEMENT_PROFILE_ENV = "NANA_OSU_CURSOR_DANCE_MOVEMENT_PROFILE"
DEFAULT_KILL_HOTKEY = "delete"
DEFAULT_AUTO_START_MAX_LATE_MS = "3000"
DEFAULT_SYNC_OFFSET_MS = "0"
DEFAULT_MOVEMENT_PROFILE = "survival"
VENDOR_DIR_NAME = "osu_lazer_cursor_dance_v1"
SCHEMA = "nana.osu.cursor_dance_runtime.v1"
STATE_SCHEMA = "nana.osu.cursor_dance_state.v1"
REQUIRED_MODULES = ("websocket", "keyboard")

_BASE_DIR = Path(__file__).resolve().parent
_PROCESS: subprocess.Popen | None = None
_STARTED_AT: float | None = None
_SCRIPT_PATH: Path | None = None
_LAST_EXIT_CODE: int | None = None
_LAST_EXIT_AT: float | None = None
_LAST_RUNTIME_MS: int | None = None
_LAST_STOP_REASON: str | None = None
_LAST_KILL_CALLED_AT: float | None = None
_LAST_KILLED_PIDS: list[int] = []
_LAST_KILL_ERROR: str | None = None
_LOCK = threading.RLock()


def _runtime_state_path() -> Path:
    return Path(os.getenv(OSU_STATE_PATH_ENV) or OSU_STATE_PATH_DEFAULT)


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.getenv(VENDOR_ROOT_ENV)
    if env_root:
        roots.append(Path(env_root))
    roots.extend(
        [
            _BASE_DIR / "vendor" / VENDOR_DIR_NAME,
            _BASE_DIR / "vendor" / "OsuLazerCursorDanceV1-main",
            _BASE_DIR / "vendor" / "OsuLazerCursorDanceV1-main" / "OsuLazerCursorDanceV1-main",
            _BASE_DIR / "vendor" / VENDOR_DIR_NAME / "OsuLazerCursorDanceV1-main",
        ]
    )
    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(root)
    return deduped


def _resolve_vendor() -> tuple[Path, Path | None]:
    for root in _candidate_roots():
        direct = root / "main.py"
        if direct.exists():
            return root, direct
        if root.exists():
            for child in root.iterdir():
                candidate = child / "main.py"
                if child.is_dir() and candidate.exists():
                    return child, candidate
    root = _candidate_roots()[0]
    return root, None


def _dependency_status() -> dict[str, bool]:
    return {name: importlib.util.find_spec(name) is not None for name in REQUIRED_MODULES}


def _supervise_process() -> int | None:
    global _PROCESS, _STARTED_AT, _SCRIPT_PATH
    global _LAST_EXIT_CODE, _LAST_EXIT_AT, _LAST_RUNTIME_MS, _LAST_STOP_REASON

    if _PROCESS is None:
        return None
    exit_code = _PROCESS.poll()
    if exit_code is None:
        return None
    now = time.time()
    _LAST_EXIT_CODE = int(exit_code)
    _LAST_EXIT_AT = now
    _LAST_RUNTIME_MS = int((now - _STARTED_AT) * 1000) if _STARTED_AT else None
    _LAST_STOP_REASON = "process_exited" if exit_code == 0 else f"process_exit_code:{exit_code}"
    _PROCESS = None
    _STARTED_AT = None
    _SCRIPT_PATH = None
    return exit_code


def _running() -> bool:
    _supervise_process()
    return _PROCESS is not None and _PROCESS.poll() is None


def _kill_hotkey() -> str:
    return os.getenv(KILL_HOTKEY_ENV, DEFAULT_KILL_HOTKEY).strip().lower() or DEFAULT_KILL_HOTKEY


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


def _auto_arm_on_start() -> bool:
    return _env_bool(AUTO_ARM_ENV, True)


def _auto_start_max_late_ms() -> int:
    try:
        value = int(float(os.getenv(AUTO_START_MAX_LATE_ENV, DEFAULT_AUTO_START_MAX_LATE_MS)))
    except (TypeError, ValueError):
        value = int(DEFAULT_AUTO_START_MAX_LATE_MS)
    return max(0, min(30000, value))


def _sync_offset_ms() -> int:
    raw = os.getenv(NANA_SYNC_OFFSET_ENV, os.getenv(SYNC_OFFSET_ENV, DEFAULT_SYNC_OFFSET_MS))
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        value = int(DEFAULT_SYNC_OFFSET_MS)
    return max(-5000, min(5000, value))


def _movement_profile() -> str:
    raw = os.getenv(NANA_MOVEMENT_PROFILE_ENV, os.getenv(MOVEMENT_PROFILE_ENV, DEFAULT_MOVEMENT_PROFILE))
    profile = raw.strip().lower() if isinstance(raw, str) else DEFAULT_MOVEMENT_PROFILE
    return profile or DEFAULT_MOVEMENT_PROFILE


def _dig(data: Any, *paths: str) -> Any:
    for path in paths:
        cur = data
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return None


def _infer_activity_state(state: dict[str, Any], raw: dict[str, Any] | None, raw_menu_state: Any) -> tuple[str, str]:
    if str(raw_menu_state) == "2":
        return "gameplay", "menu_state_2"
    if raw_menu_state is not None:
        return "non_gameplay", "menu_state_present"
    if not isinstance(raw, dict):
        return "unknown", "no_raw_tosu_state"

    state_name = _dig(raw, "state.name", "state")
    if isinstance(state_name, str) and state_name.strip().lower() in {"play", "gameplay", "playing"}:
        return "gameplay", "state_name_play"

    current_time = state.get("current_time_ms")
    beatmap = state.get("beatmap") or {}
    has_beatmap = any(beatmap.get(key) for key in ("title", "path", "file", "id"))
    if current_time is not None and has_beatmap:
        return "gameplay", "explicit_gameplay_time"
    if has_beatmap:
        return "menu", "beatmap_loaded_no_time"
    return "unknown", "no_activity_signal"


def _activity_from_tosu() -> dict[str, Any]:
    try:
        state = read_tosu_state()
    except Exception as exc:
        return {
            "tosu_ok": False,
            "activity_state": "unknown",
            "current_time_ms": None,
            "beatmap": {},
            "tosu_error": f"{type(exc).__name__}:{exc}",
        }
    raw = state.get("raw") if isinstance(state, dict) else None
    menu = raw.get("menu") if isinstance(raw, dict) else None
    raw_menu_state = menu.get("state") if isinstance(menu, dict) else None
    activity_state, activity_source = _infer_activity_state(state, raw, raw_menu_state)
    return {
        "tosu_ok": bool(state.get("ok")),
        "activity_state": activity_state,
        "activity_source": activity_source,
        "current_time_ms": state.get("current_time_ms"),
        "raw_menu_state": raw_menu_state,
        "beatmap": state.get("beatmap") or {},
        "tosu_error": None,
    }


def _base_payload(decision: str, reason: str) -> dict[str, Any]:
    _supervise_process()
    vendor_root, script = _resolve_vendor()
    dependency_status = _dependency_status()
    running = _PROCESS is not None and _PROCESS.poll() is None
    payload = {
        "schema": SCHEMA,
        "version": 1,
        "decision": decision,
        "reason": reason,
        "vendor_root": str(vendor_root),
        "vendor_present": vendor_root.exists(),
        "script_path": str(script) if script else None,
        "script_present": bool(script and script.exists()),
        "running": running,
        "pid": _PROCESS.pid if running and _PROCESS is not None else None,
        "started_at": _STARTED_AT,
        "uptime_ms": int((time.time() - _STARTED_AT) * 1000) if running and _STARTED_AT else 0,
        "dependency_status": dependency_status,
        "dependencies_ready": all(dependency_status.values()),
        "kill_hotkey": _kill_hotkey(),
        "auto_arm_on_start": _auto_arm_on_start(),
        "auto_start_max_late_ms": _auto_start_max_late_ms(),
        "sync_offset_ms": _sync_offset_ms(),
        "movement_profile": _movement_profile(),
        "last_exit_code": _LAST_EXIT_CODE,
        "last_exit_at": _LAST_EXIT_AT,
        "last_runtime_ms": _LAST_RUNTIME_MS,
        "last_stop_reason": _LAST_STOP_REASON,
        "last_kill_called_at": _LAST_KILL_CALLED_AT,
        "last_killed_pids": list(_LAST_KILLED_PIDS),
        "last_kill_error": _LAST_KILL_ERROR,
        "start_command_hint": "/osu-cursor-dance-start",
        "stop_command_hint": "/osu-cursor-dance-stop",
        "kill_command_hint": "/osu-cursor-dance-kill",
        "real_input": running,
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
    }
    payload.update(_activity_from_tosu())
    return payload


def _blockers() -> list[str]:
    vendor_root, script = _resolve_vendor()
    blockers: list[str] = []
    if not vendor_root.exists():
        blockers.append("vendor_root_missing")
    if not script or not script.exists():
        blockers.append("main_py_missing")
    for name, ready in _dependency_status().items():
        if not ready:
            blockers.append(f"dependency_missing:{name}")
    return blockers


def _write_core_state(event: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    activity = _activity_from_tosu()
    running = _PROCESS is not None and _PROCESS.poll() is None
    vendor_root, script = _resolve_vendor()
    payload = {
        "schema": STATE_SCHEMA,
        "adapter": "osu",
        "source": "nana_cursor_dance_runtime",
        "created_at": time.time(),
        "current_time_ms": activity.get("current_time_ms"),
        "activity_state": activity.get("activity_state"),
        "activity_source": activity.get("activity_source"),
        "raw_menu_state": activity.get("raw_menu_state"),
        "beatmap": activity.get("beatmap") or {},
        "skill": {
            "name": "osu_cursor_dance_boss",
            "mode": "cursor_dance_v1",
            "active": running,
        },
        "runtime": {
            "event": event,
            "runner": "cursor_dance_v1",
            "running": running,
            "pid": _PROCESS.pid if running and _PROCESS is not None else None,
            "script": str(script) if script else None,
            "uptime_ms": int((time.time() - _STARTED_AT) * 1000) if running and _STARTED_AT else 0,
            "vendor_root": str(vendor_root),
            "kill_hotkey": _kill_hotkey(),
            "auto_arm_on_start": _auto_arm_on_start(),
            "auto_start_max_late_ms": _auto_start_max_late_ms(),
            "sync_offset_ms": _sync_offset_ms(),
            "real_input": running,
            "submit": False,
            "last_exit_code": _LAST_EXIT_CODE,
            "last_stop_reason": _LAST_STOP_REASON,
        },
        "safety": {
            "real_input": running,
            "submit": False,
            "vts_call": False,
            "voice_call": False,
            "obs_call": False,
            "vision_call": False,
        },
    }
    if extra:
        payload["runtime"].update(extra)
    try:
        path = write_state(payload)
        return {"state_write": True, "state_path": str(path), "state_error": None}
    except Exception as exc:
        return {"state_write": False, "state_path": str(_runtime_state_path()), "state_error": f"{type(exc).__name__}:{exc}"}


def _call_emergency_release() -> dict[str, Any]:
    try:
        return run_emergency_release(
            "/osu-emergency-release --operator-approval-token=I_APPROVE_OSU_EMERGENCY_RELEASE"
        )
    except Exception as exc:
        return {"decision": "release_error", "error": f"{type(exc).__name__}:{exc}"}


def build_cursor_dance_status_payload(text: str = "") -> dict[str, Any]:
    with _LOCK:
        payload = _base_payload("cursor_dance_runtime_status", "status_only")
        payload["blockers"] = _blockers()
        payload.update(_write_core_state("status"))
        return payload


def build_cursor_dance_start_payload(text: str = "") -> dict[str, Any]:
    global _PROCESS, _STARTED_AT, _SCRIPT_PATH

    with _LOCK:
        if _running():
            payload = _base_payload("cursor_dance_runtime_already_running", "already_running")
            payload["blockers"] = []
            payload.update(_write_core_state("already_running"))
            return payload

        blockers = _blockers()
        if blockers:
            payload = _base_payload("cursor_dance_runtime_hold", "runtime_config_not_ready")
            payload["blockers"] = blockers
            payload.update(_write_core_state("start_blocked", extra={"blockers": blockers}))
            return payload

        vendor_root, script = _resolve_vendor()
        env = os.environ.copy()
        env.setdefault(KILL_HOTKEY_ENV, DEFAULT_KILL_HOTKEY)
        env.setdefault(AUTO_ARM_ENV, "1")
        env.setdefault(AUTO_START_MAX_LATE_ENV, DEFAULT_AUTO_START_MAX_LATE_MS)
        env[SYNC_OFFSET_ENV] = str(_sync_offset_ms())
        env[MOVEMENT_PROFILE_ENV] = _movement_profile()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        _PROCESS = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(vendor_root),
            env=env,
            creationflags=creationflags,
        )
        _STARTED_AT = time.time()
        _SCRIPT_PATH = script
        payload = _base_payload("cursor_dance_runtime_started", "process_started")
        payload["blockers"] = []
        payload.update(_write_core_state("started"))
        return payload


def build_cursor_dance_stop_payload(text: str = "") -> dict[str, Any]:
    global _PROCESS, _STARTED_AT, _SCRIPT_PATH, _LAST_STOP_REASON

    with _LOCK:
        if not _running():
            payload = _base_payload("cursor_dance_runtime_stopped", "not_running")
            payload["blockers"] = []
            payload.update(_write_core_state("stop_not_running"))
            return payload

        assert _PROCESS is not None
        pid = int(_PROCESS.pid)
        release_result = _call_emergency_release()
        reason = "terminated"
        _PROCESS.terminate()
        try:
            _PROCESS.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            _PROCESS.kill()
            try:
                _PROCESS.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
            reason = "killed_after_timeout"
        _LAST_STOP_REASON = reason
        _PROCESS = None
        _STARTED_AT = None
        _SCRIPT_PATH = None
        payload = _base_payload("cursor_dance_runtime_stopped", reason)
        payload.update(
            {
                "stopped_pid": pid,
                "release_called": True,
                "release_result": release_result,
                "blockers": [],
                "real_input": False,
            }
        )
        payload.update(_write_core_state("stopped", extra={"stopped_pid": pid, "release_called": True}))
        return payload


def _terminate_process_tree(pid: int) -> tuple[bool, str | None]:
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if completed.returncode == 0:
                return True, None
            message = (completed.stderr or completed.stdout or "").strip()
            return False, message or f"taskkill_returncode:{completed.returncode}"
        except Exception as exc:
            return False, f"{type(exc).__name__}:{exc}"
    try:
        os.kill(int(pid), 9)
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}:{exc}"


def _scan_process_command_lines() -> list[dict[str, Any]]:
    if os.name != "nt":
        return []
    try:
        completed = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                "name='python.exe' or name='pythonw.exe'",
                "get",
                "ProcessId,CommandLine",
                "/format:csv",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    rows: list[dict[str, Any]] = []
    try:
        reader = csv.DictReader(io.StringIO(completed.stdout))
        for row in reader:
            try:
                pid = int(str(row.get("ProcessId")).strip())
            except Exception:
                continue
            rows.append({"pid": pid, "command_line": row.get("CommandLine")})
    except Exception:
        return []
    return rows


def _is_cursor_dance_command_line(command_line: Any) -> bool:
    normalized = str(command_line or "").replace("/", "\\").lower()
    vendor_root, script = _resolve_vendor()
    script_path = str(script or (vendor_root / "main.py")).replace("/", "\\").lower()
    return script_path in normalized or "osu_lazer_cursor_dance_v1\\main.py" in normalized or "osulazercursordancev1-main\\main.py" in normalized


def build_cursor_dance_kill_payload(text: str = "", *, trigger: str = "command") -> dict[str, Any]:
    global _PROCESS, _STARTED_AT, _SCRIPT_PATH
    global _LAST_KILL_CALLED_AT, _LAST_KILLED_PIDS, _LAST_KILL_ERROR, _LAST_STOP_REASON

    with _LOCK:
        _LAST_KILL_CALLED_AT = time.time()
        release_before = _call_emergency_release()
        killed_pids: list[int] = []
        errors: list[str] = []

        if _PROCESS is not None and _PROCESS.poll() is None:
            pid = int(_PROCESS.pid)
            ok, error = _terminate_process_tree(pid)
            if ok:
                killed_pids.append(pid)
            elif error:
                errors.append(f"tracked_process:{pid}:{error}")
        current_pid = os.getpid()
        for proc in _scan_process_command_lines():
            pid = proc.get("pid")
            if not isinstance(pid, int) or pid == current_pid or pid in killed_pids:
                continue
            if not _is_cursor_dance_command_line(proc.get("command_line")):
                continue
            ok, error = _terminate_process_tree(pid)
            if ok:
                killed_pids.append(pid)
            elif error:
                errors.append(f"stale_process:{pid}:{error}")

        _PROCESS = None
        _STARTED_AT = None
        _SCRIPT_PATH = None
        _LAST_STOP_REASON = "killed"
        release_after = _call_emergency_release()
        _LAST_KILLED_PIDS = sorted(set(int(pid) for pid in killed_pids))
        _LAST_KILL_ERROR = "; ".join(errors) if errors else None

        payload = _base_payload("cursor_dance_runtime_killed", "killed")
        payload.update(
            {
                "kill_trigger": trigger,
                "killed": True,
                "killed_pids": list(_LAST_KILLED_PIDS),
                "release_called": True,
                "release_before": release_before,
                "release_after": release_after,
                "blockers": [],
                "running": False,
                "pid": None,
                "uptime_ms": 0,
                "real_input": False,
            }
        )
        payload.update(_write_core_state("killed", extra={"killed_pids": list(_LAST_KILLED_PIDS)}))
        return payload


def _atexit_cleanup() -> None:
    try:
        if _PROCESS is not None and _PROCESS.poll() is None:
            build_cursor_dance_kill_payload("/osu-cursor-dance-kill", trigger="atexit")
    except Exception:
        pass


def print_cursor_dance_payload(payload: dict[str, Any], *, title: str) -> None:
    print(title)
    print(f"  Decision: {payload.get('decision')}")
    print(f"  Reason: {payload.get('reason')}")
    print(f"  Vendor root: {payload.get('vendor_root')}")
    print(f"  Vendor present: {payload.get('vendor_present')}")
    print(f"  Script path: {payload.get('script_path')}")
    print(f"  Script present: {payload.get('script_present')}")
    print(f"  Running: {payload.get('running')}")
    print(f"  PID: {payload.get('pid')}")
    print(f"  Uptime ms: {payload.get('uptime_ms')}")
    print(f"  Dependencies ready: {payload.get('dependencies_ready')}")
    print(f"  Dependency status: {payload.get('dependency_status')}")
    print(f"  Activity state: {payload.get('activity_state')}")
    print(f"  Activity source: {payload.get('activity_source')}")
    print(f"  Current time ms: {payload.get('current_time_ms')}")
    print(f"  Raw menu state: {payload.get('raw_menu_state')}")
    print(f"  Kill hotkey: {payload.get('kill_hotkey')}")
    print(f"  Auto arm on start: {payload.get('auto_arm_on_start')}")
    print(f"  Auto start max late ms: {payload.get('auto_start_max_late_ms')}")
    print(f"  Sync offset ms: {payload.get('sync_offset_ms')}")
    print(f"  Movement profile: {payload.get('movement_profile')}")
    print(f"  Last exit code: {payload.get('last_exit_code')}")
    print(f"  Last stop reason: {payload.get('last_stop_reason')}")
    print(f"  Last killed pids: {payload.get('last_killed_pids')}")
    print(f"  Last kill error: {payload.get('last_kill_error')}")
    print(f"  State write: {payload.get('state_write')}")
    print(f"  State path: {payload.get('state_path')}")
    print(f"  State error: {payload.get('state_error')}")
    print(f"  Blockers: {payload.get('blockers')}")
    print(f"  Start command hint: {payload.get('start_command_hint')}")
    print("  Emergency kill: press DELETE inside the Boss process, or run /osu-cursor-dance-kill")
    print(f"  Real input: {payload.get('real_input')}")
    print(f"  Submit: {payload.get('submit')}")


atexit.register(_atexit_cleanup)
