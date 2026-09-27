"""
osu! input safety readiness helpers - Phase 29.

Foreground window readiness plus emergency abort/release readiness previews.
The gated emergency release command is release-only: neutral key-up events for
known osu! input keys. No key-down, cursor move, click/tap, score submit, VTS,
voice/TTS, OBS, or gameplay executor path is implemented here.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Callable


INPUT_SAFETY_SCHEMA = "nana.osu.input_safety.v1"
DEFAULT_OPERATOR_ABORT_PATH = Path(__file__).resolve().parent / "data" / "operator_abort.flag"
OPERATOR_ABORT_PATH_ENV = "NANA_OSU_OPERATOR_ABORT_PATH"
EMERGENCY_RELEASE_ENV = "NANA_OSU_EMERGENCY_RELEASE_ENABLED"
EMERGENCY_RELEASE_EXTRA_KEYS_ENV = "NANA_OSU_EMERGENCY_RELEASE_EXTRA_KEYS"
EMERGENCY_RELEASE_TOKEN = "I_APPROVE_OSU_EMERGENCY_RELEASE"
DEFAULT_EMERGENCY_RELEASE_KEYS = ("W", "A", "S", "D", "Z", "X")

WindowReader = Callable[[], dict[str, Any]]
ReadinessReader = Callable[[], dict[str, Any]]
ReleaseBackend = Callable[[tuple[str, ...]], dict[str, Any]]

_LETTER_VK = {chr(code): code for code in range(ord("A"), ord("Z") + 1)}
_DIGIT_VK = {str(num): ord(str(num)) for num in range(10)}
_NAMED_VK = {
    "SPACE": 0x20,
    "SHIFT": 0x10,
    "CTRL": 0x11,
    "CONTROL": 0x11,
    "ALT": 0x12,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
}
_KEY_TO_VK = {**_LETTER_VK, **_DIGIT_VK, **_NAMED_VK}


def _safe_bool_probe(value: bool | Callable[[], bool] | None) -> bool | None:
    if value is None:
        return None
    if callable(value):
        return bool(value())
    return bool(value)


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_operator_token(text: str) -> str | None:
    for token in str(text or "").split()[1:]:
        if token.startswith("--operator-approval-token="):
            return token.split("=", 1)[1].strip()
    return None


def _base_flags() -> dict[str, bool]:
    return {
        "executable": False,
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
    }


def _release_only_flags() -> dict[str, bool]:
    return {
        "safety_release_only": True,
        "gameplay_input": False,
        "key_down_sent": False,
        "key_up_sent": False,
        "cursor_move": False,
        "click": False,
        "tap": False,
        "submit": False,
        "real_input": False,
    }


def _normalize_key_name(raw_key: Any) -> str | None:
    key = str(raw_key or "").strip().upper()
    if not key:
        return None
    aliases = {
        " ": "SPACE",
        "SPACEBAR": "SPACE",
        "CONTROL": "CTRL",
        "RETURN": "ENTER",
    }
    key = aliases.get(key, key)
    if key not in _KEY_TO_VK:
        return None
    return key


def _split_extra_keys(raw_keys: Any) -> list[str]:
    text = str(raw_keys or "")
    if not text.strip():
        return []
    for separator in (";", "|", "\n", "\t"):
        text = text.replace(separator, ",")
    pieces: list[str] = []
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        pieces.extend(part for part in chunk.split(" ") if part.strip())
    return pieces


def read_emergency_release_keys(*, extra_keys: Any | None = None) -> dict[str, Any]:
    raw_extra = os.getenv(EMERGENCY_RELEASE_EXTRA_KEYS_ENV) if extra_keys is None else extra_keys
    keys: list[str] = []
    filtered: list[str] = []

    for raw_key in [*DEFAULT_EMERGENCY_RELEASE_KEYS, *_split_extra_keys(raw_extra)]:
        key = _normalize_key_name(raw_key)
        if key is None:
            if str(raw_key or "").strip():
                filtered.append(str(raw_key).strip())
            continue
        if key not in keys:
            keys.append(key)

    return {
        "release_keys": tuple(keys),
        "default_release_keys": DEFAULT_EMERGENCY_RELEASE_KEYS,
        "extra_keys_env": EMERGENCY_RELEASE_EXTRA_KEYS_ENV,
        "extra_keys_raw": raw_extra or "",
        "filtered_extra_keys": tuple(filtered),
    }


def _release_backend_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        ctypes.WinDLL("user32", use_last_error=True)
        return True
    except Exception:
        return False


def _format_win32_error(error_code: int, formatter: Callable[[int], str] | None = None) -> str:
    if not error_code:
        return ""
    if formatter is not None:
        try:
            return str(formatter(int(error_code)))
        except Exception as exc:
            return f"win32_error_format_failed:{type(exc).__name__}"
    try:
        import ctypes

        return str(ctypes.FormatError(int(error_code))).strip()
    except Exception as exc:
        return f"win32_error_format_unavailable:{type(exc).__name__}"


def _keyup_backend_payload(keys: tuple[str, ...], event_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "backend": "win32_sendinput_keyup",
        "attempted": False,
        "reason": None,
        "release_keys": tuple(keys),
        "requested_count": len(event_keys),
        "sent_count": 0,
        "sendinput_return": 0,
        "win32_last_error": 0,
        "win32_error_message": "",
        "backend_error": None,
        "failed_keys": tuple(event_keys),
        "input_size": None,
        "pointer_size": None,
        "key_event_flags": ("KEYEVENTF_KEYUP",),
        "key_event_flags_value": 0x0002,
        **_release_only_flags(),
        **_base_flags(),
    }


def _send_keyup_release_all_win32(
    keys: tuple[str, ...],
    *,
    platform_name: str | None = None,
    user32_factory: Callable[[], Any] | None = None,
    last_error_reader: Callable[[], int] | None = None,
    error_message_formatter: Callable[[int], str] | None = None,
) -> dict[str, Any]:
    """
    Send neutral KEYUP events only. This function never sends key-down events.
    """
    event_keys = tuple(key for key in keys if _KEY_TO_VK.get(key) is not None)
    base = _keyup_backend_payload(tuple(keys), event_keys)
    if (platform_name or sys.platform) != "win32":
        return {
            **base,
            "reason": "windows_api_unavailable",
            "backend_error": "windows_api_unavailable",
        }

    try:
        import ctypes
        from ctypes import wintypes

        ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
        INPUT_KEYBOARD = 1
        KEYEVENTF_KEYUP = 0x0002

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR),
            ]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR),
            ]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            ]

        class _INPUT_UNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("union", _INPUT_UNION)]

        events = []
        built_event_keys: list[str] = []
        for key in keys:
            vk = _KEY_TO_VK.get(key)
            if vk is None:
                continue
            event = INPUT()
            event.type = INPUT_KEYBOARD
            event.union.ki = KEYBDINPUT(
                wVk=wintypes.WORD(vk),
                wScan=wintypes.WORD(0),
                dwFlags=wintypes.DWORD(KEYEVENTF_KEYUP),
                time=wintypes.DWORD(0),
                dwExtraInfo=ULONG_PTR(0),
            )
            events.append(event)
            built_event_keys.append(key)

        event_keys = tuple(built_event_keys)
        input_size = ctypes.sizeof(INPUT)
        pointer_size = ctypes.sizeof(ctypes.c_void_p)
        if not events:
            return {
                **_keyup_backend_payload(tuple(keys), event_keys),
                "attempted": False,
                "reason": "no_valid_release_keys",
                "input_size": input_size,
                "pointer_size": pointer_size,
                "failed_keys": (),
            }

        user32 = user32_factory() if user32_factory is not None else ctypes.WinDLL("user32", use_last_error=True)
        try:
            user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
            user32.SendInput.restype = wintypes.UINT
        except AttributeError:
            pass
        array_type = INPUT * len(events)
        event_array = array_type(*events)
        try:
            ctypes.set_last_error(0)
        except Exception:
            pass
        sent = int(user32.SendInput(len(events), event_array, input_size))
        if sent == len(events):
            last_error = 0
            backend_error = None
            reason = "keyup_release_all_sent"
            failed_keys: tuple[str, ...] = ()
        else:
            if last_error_reader is not None:
                last_error = int(last_error_reader())
            else:
                try:
                    last_error = int(ctypes.get_last_error())
                except Exception:
                    last_error = 0
            backend_error = "sendinput_return_zero" if sent == 0 else "sendinput_partial"
            reason = "keyup_release_all_failed" if sent == 0 else "keyup_release_all_partial"
            failed_keys = event_keys[sent:] if sent > 0 else event_keys
        return {
            **_keyup_backend_payload(tuple(keys), event_keys),
            "attempted": True,
            "reason": reason,
            "sent_count": sent,
            "sendinput_return": sent,
            "requested_count": len(events),
            "win32_last_error": last_error,
            "win32_error_message": _format_win32_error(last_error, error_message_formatter),
            "backend_error": backend_error,
            "failed_keys": failed_keys,
            "input_size": input_size,
            "pointer_size": pointer_size,
            "key_up_sent": sent == len(events),
            "key_down_sent": False,
            "cursor_move": False,
            "click": False,
            "tap": False,
        }
    except Exception as exc:
        last_error = 0
        if last_error_reader is not None:
            try:
                last_error = int(last_error_reader())
            except Exception:
                last_error = 0
        return {
            **base,
            "attempted": True,
            "reason": f"keyup_release_all_error:{type(exc).__name__}",
            "sent_count": 0,
            "sendinput_return": 0,
            "win32_last_error": last_error,
            "win32_error_message": _format_win32_error(last_error, error_message_formatter) or str(exc),
            "backend_error": f"{type(exc).__name__}:{exc}",
            "failed_keys": event_keys,
            "key_up_sent": False,
            "key_down_sent": False,
            "cursor_move": False,
            "click": False,
            "tap": False,
        }


def _release_gate(text: str = "") -> dict[str, Any]:
    raw_env = os.getenv(EMERGENCY_RELEASE_ENV)
    env_present = raw_env is not None and str(raw_env).strip() != ""
    env_enabled = _is_truthy(raw_env)
    operator_token = _parse_operator_token(text)
    token_present = bool(operator_token)
    token_valid = operator_token == EMERGENCY_RELEASE_TOKEN
    return {
        "env_gate_name": EMERGENCY_RELEASE_ENV,
        "env_gate_present": env_present,
        "env_gate_enabled": env_enabled,
        "token_present": token_present,
        "token_valid": token_valid,
    }


def build_emergency_release_status(text: str = "", *, extra_keys: Any | None = None) -> dict[str, Any]:
    keys = read_emergency_release_keys(extra_keys=extra_keys)
    gate = _release_gate(text)
    helper_available = _release_backend_available()
    return {
        "decision": "emergency_release_status_only_no_input",
        "emergency_release_available": "ready" if helper_available else "unavailable_no_helper",
        "release_strategy": "keyup_release_all_available" if helper_available else "preview_only",
        "release_command_available": bool(helper_available),
        "release_helper_available": bool(helper_available),
        "reason": "keyup_release_all_helper_available" if helper_available else "keyup_release_all_helper_unavailable",
        **gate,
        **keys,
        **_release_only_flags(),
        **_base_flags(),
    }


def build_emergency_release_dry_run(text: str = "", *, extra_keys: Any | None = None) -> dict[str, Any]:
    status = build_emergency_release_status(text, extra_keys=extra_keys)
    blockers: list[str] = []
    if not status["env_gate_enabled"]:
        blockers.append("missing_or_disabled_env_gate")
    if not status["token_valid"]:
        blockers.append("missing_or_invalid_operator_token")
    if not status["release_helper_available"]:
        blockers.append("release_helper_unavailable")
    return {
        **status,
        "decision": "emergency_release_dry_run_no_input",
        "blockers": blockers,
        "would_release_keys": status["release_keys"],
        "key_up_sent": False,
        "key_down_sent": False,
        "real_input": False,
        "submit": False,
    }


def run_emergency_release(
    text: str = "",
    *,
    release_backend: ReleaseBackend | None = None,
    extra_keys: Any | None = None,
) -> dict[str, Any]:
    dry_run = build_emergency_release_dry_run(text, extra_keys=extra_keys)
    blockers = list(dry_run.get("blockers") or [])
    if blockers:
        return {
            **dry_run,
            "decision": "emergency_release_hold_no_input",
            "hold_reason": ",".join(blockers),
            "backend": None,
            "sent_count": 0,
            "key_up_sent": False,
            "key_down_sent": False,
            "real_input": False,
            "submit": False,
        }

    backend = release_backend or _send_keyup_release_all_win32
    try:
        send_result = backend(tuple(dry_run["release_keys"]))
    except Exception as exc:
        send_result = {
            "backend": "mock_or_backend",
            "attempted": True,
            "reason": f"keyup_release_all_error:{type(exc).__name__}",
            "release_keys": tuple(dry_run["release_keys"]),
            "requested_count": len(tuple(dry_run["release_keys"])),
            "sent_count": 0,
            "sendinput_return": 0,
            "win32_last_error": 0,
            "win32_error_message": str(exc),
            "backend_error": f"{type(exc).__name__}:{exc}",
            "failed_keys": tuple(dry_run["release_keys"]),
            "key_up_sent": False,
            "key_down_sent": False,
            "cursor_move": False,
            "click": False,
            "tap": False,
        }

    key_up_sent = bool(send_result.get("key_up_sent"))
    return {
        **dry_run,
        **send_result,
        "decision": "emergency_release_keyup_sent" if key_up_sent else "emergency_release_keyup_failed",
        "hold_reason": None,
        "safety_release_only": True,
        "gameplay_input": False,
        "key_down_sent": False,
        "key_up_sent": key_up_sent,
        "cursor_move": False,
        "click": False,
        "tap": False,
        "executable": False,
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
    }


def _read_foreground_window_win32() -> dict[str, Any]:
    if sys.platform != "win32":
        return {
            "available": False,
            "window_title": None,
            "window_class": None,
            "process_name": None,
            "reason": "windows_api_unavailable",
        }

    try:
        import ctypes
        from ctypes import wintypes
    except Exception as exc:
        return {
            "available": False,
            "window_title": None,
            "window_class": None,
            "process_name": None,
            "reason": f"ctypes_unavailable:{type(exc).__name__}",
        }

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {
                "available": False,
                "window_title": None,
                "window_class": None,
                "process_name": None,
                "reason": "foreground_window_unavailable",
            }

        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        title_len = max(0, int(user32.GetWindowTextLengthW(hwnd)))
        title_buf = ctypes.create_unicode_buffer(title_len + 1)
        user32.GetWindowTextW(hwnd, title_buf, title_len + 1)

        class_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buf, 256)

        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = None
        if int(pid.value) > 0:
            try:
                import psutil

                process_name = psutil.Process(int(pid.value)).name()
            except Exception:
                process_name = None

        return {
            "available": True,
            "window_title": title_buf.value or "",
            "window_class": class_buf.value or "",
            "process_name": process_name,
            "reason": "foreground_window_read",
        }
    except Exception as exc:
        return {
            "available": False,
            "window_title": None,
            "window_class": None,
            "process_name": None,
            "reason": f"foreground_window_error:{type(exc).__name__}",
        }


def _looks_like_osu_window(*, title: Any, window_class: Any, process_name: Any) -> bool:
    title_l = str(title or "").strip().lower()
    class_l = str(window_class or "").strip().lower()
    process_l = str(process_name or "").strip().lower()

    if process_l in {"osu!.exe", "osu.exe", "osu!", "osu"}:
        return True
    if title_l == "osu!" or title_l.startswith("osu!"):
        return True
    if class_l in {"osu!", "osu"} or class_l.startswith("osu!"):
        return True
    return False


def read_osu_focus_readiness(*, window_reader: WindowReader | None = None) -> dict[str, Any]:
    """
    Read the current foreground window without changing focus or sending input.
    """
    try:
        window = window_reader() if window_reader is not None else _read_foreground_window_win32()
        if not bool(window.get("available")):
            return {
                "focus_readiness": "unavailable_no_helper",
                "window_title": window.get("window_title"),
                "window_class": window.get("window_class"),
                "process_name": window.get("process_name"),
                "is_osu_window": False,
                "reason": window.get("reason") or "foreground_window_helper_unavailable",
                **_base_flags(),
            }

        is_osu = _looks_like_osu_window(
            title=window.get("window_title"),
            window_class=window.get("window_class"),
            process_name=window.get("process_name"),
        )
        return {
            "focus_readiness": "ready" if is_osu else "not_osu",
            "window_title": window.get("window_title"),
            "window_class": window.get("window_class"),
            "process_name": window.get("process_name"),
            "is_osu_window": bool(is_osu),
            "reason": "foreground_window_looks_like_osu" if is_osu else "foreground_window_not_osu",
            **_base_flags(),
        }
    except Exception as exc:
        return {
            "focus_readiness": "error",
            "window_title": None,
            "window_class": None,
            "process_name": None,
            "is_osu_window": False,
            "reason": f"focus_readiness_error:{type(exc).__name__}",
            **_base_flags(),
        }


def read_emergency_release_readiness(
    *,
    release_helper_available: bool | Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """
    Report whether an osu!-specific neutral key-up release-all helper exists.
    """
    try:
        probed = _safe_bool_probe(release_helper_available)
        helper_available = _release_backend_available() if probed is None else probed
        keys = read_emergency_release_keys()
        if helper_available is True:
            return {
                "emergency_release_available": "ready",
                "release_strategy": "keyup_release_all_available",
                "release_command_available": True,
                "release_helper_available": True,
                "release_keys": keys["release_keys"],
                "filtered_extra_keys": keys["filtered_extra_keys"],
                "reason": "keyup_release_all_helper_available",
                "safety_release_only": True,
                "gameplay_input": False,
                "key_down_sent": False,
                "key_up_sent": False,
                "cursor_move": False,
                "click": False,
                **_base_flags(),
            }
        return {
            "emergency_release_available": "unavailable_no_helper",
            "release_strategy": "preview_only",
            "release_command_available": False,
            "release_helper_available": False,
            "release_keys": keys["release_keys"],
            "filtered_extra_keys": keys["filtered_extra_keys"],
            "reason": "no_osu_keyup_release_all_helper",
            "safety_release_only": True,
            "gameplay_input": False,
            "key_down_sent": False,
            "key_up_sent": False,
            "cursor_move": False,
            "click": False,
            **_base_flags(),
        }
    except Exception as exc:
        return {
            "emergency_release_available": "error",
            "release_strategy": "preview_only",
            "release_command_available": False,
            "release_helper_available": False,
            "release_keys": DEFAULT_EMERGENCY_RELEASE_KEYS,
            "filtered_extra_keys": (),
            "reason": f"emergency_release_readiness_error:{type(exc).__name__}",
            "safety_release_only": True,
            "gameplay_input": False,
            "key_down_sent": False,
            "key_up_sent": False,
            "cursor_move": False,
            "click": False,
            **_base_flags(),
        }


def _resolve_abort_path(raw_path: str | os.PathLike[str] | None = None) -> Path:
    if raw_path:
        return Path(raw_path)
    return Path(os.getenv(OPERATOR_ABORT_PATH_ENV) or DEFAULT_OPERATOR_ABORT_PATH)


def read_emergency_abort_readiness(
    *,
    abort_command_available: bool | Callable[[], bool] | None = None,
    abort_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """
    Report whether an operator-visible abort command/flag path is ready.

    This is a readiness preview only. It does not kill processes or send input.
    """
    try:
        path = _resolve_abort_path(abort_path)
        parent_exists = path.parent.exists()
        command_available_probe = _safe_bool_probe(abort_command_available)
        command_available = parent_exists if command_available_probe is None else command_available_probe
        ready = bool(command_available)
        return {
            "emergency_abort_available": "ready" if ready else "unavailable_no_helper",
            "abort_strategy": "operator_abort_command_available" if ready else "preview_only",
            "abort_command_available": ready,
            "abort_flag_path": str(path),
            "abort_flag_parent_exists": bool(parent_exists),
            "reason": "operator_abort_command_available" if ready else "operator_abort_command_unavailable",
            **_base_flags(),
        }
    except Exception as exc:
        return {
            "emergency_abort_available": "error",
            "abort_strategy": "preview_only",
            "abort_command_available": False,
            "abort_flag_path": None,
            "abort_flag_parent_exists": False,
            "reason": f"emergency_abort_readiness_error:{type(exc).__name__}",
            **_base_flags(),
        }


def _safe_read(reader: ReadinessReader, fallback: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = reader()
        return payload if isinstance(payload, dict) else dict(fallback)
    except Exception as exc:
        result = dict(fallback)
        result["reason"] = f"readiness_reader_error:{type(exc).__name__}"
        return result


def build_input_safety_status(
    *,
    focus_reader: ReadinessReader | None = None,
    release_reader: ReadinessReader | None = None,
    abort_reader: ReadinessReader | None = None,
) -> dict[str, Any]:
    focus = _safe_read(
        focus_reader or read_osu_focus_readiness,
        {
            "focus_readiness": "error",
            "window_title": None,
            "window_class": None,
            "process_name": None,
            "is_osu_window": False,
            "reason": "focus_readiness_error",
            **_base_flags(),
        },
    )
    release = _safe_read(
        release_reader or read_emergency_release_readiness,
        {
            "emergency_release_available": "error",
            "release_strategy": "preview_only",
            "release_command_available": False,
            "reason": "release_readiness_error",
            "safety_release_only": False,
            **_base_flags(),
        },
    )
    abort = _safe_read(
        abort_reader or read_emergency_abort_readiness,
        {
            "emergency_abort_available": "error",
            "abort_strategy": "preview_only",
            "abort_command_available": False,
            "abort_flag_path": None,
            "abort_flag_parent_exists": False,
            "reason": "abort_readiness_error",
            **_base_flags(),
        },
    )

    return {
        "schema": INPUT_SAFETY_SCHEMA,
        "version": 1,
        "decision": "input_safety_status_only_no_input",
        "focus": focus,
        "release": release,
        "abort": abort,
        "focus_readiness": focus.get("focus_readiness") or "unavailable_no_helper",
        "window_focus_readiness": focus.get("focus_readiness") or "unavailable_no_helper",
        "window_title": focus.get("window_title"),
        "window_class": focus.get("window_class"),
        "process_name": focus.get("process_name"),
        "is_osu_window": bool(focus.get("is_osu_window")),
        "focus_reason": focus.get("reason"),
        "emergency_release_available": release.get("emergency_release_available") or "unavailable_no_helper",
        "release_strategy": release.get("release_strategy") or "preview_only",
        "release_command_available": bool(release.get("release_command_available")),
        "release_reason": release.get("reason"),
        "emergency_abort_available": abort.get("emergency_abort_available") or "unavailable_no_helper",
        "abort_strategy": abort.get("abort_strategy") or "preview_only",
        "abort_command_available": bool(abort.get("abort_command_available")),
        "abort_flag_path": abort.get("abort_flag_path"),
        "abort_flag_parent_exists": bool(abort.get("abort_flag_parent_exists")),
        "abort_reason": abort.get("reason"),
        "created_at": time.time(),
        **_base_flags(),
    }


def print_input_safety_status(payload: dict[str, Any]) -> None:
    print("osu! Input Safety Status")
    print(f"  Decision: {payload['decision']}")
    print(f"  Focus readiness: {payload['focus_readiness']}")
    print(f"  Window title: {payload['window_title']}")
    print(f"  Window class: {payload['window_class']}")
    print(f"  Process name: {payload['process_name']}")
    print(f"  Is osu window: {payload['is_osu_window']}")
    print(f"  Focus reason: {payload['focus_reason']}")
    print(f"  Emergency release available: {payload['emergency_release_available']}")
    print(f"  Release strategy: {payload['release_strategy']}")
    print(f"  Release command available: {payload['release_command_available']}")
    print(f"  Release reason: {payload['release_reason']}")
    print(f"  Emergency abort available: {payload['emergency_abort_available']}")
    print(f"  Abort strategy: {payload['abort_strategy']}")
    print(f"  Abort command available: {payload['abort_command_available']}")
    print(f"  Abort flag path: {payload['abort_flag_path']}")
    print(f"  Abort flag parent exists: {payload['abort_flag_parent_exists']}")
    print(f"  Abort reason: {payload['abort_reason']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input allowed: {payload['real_input_allowed']}")
    print(f"  Submit allowed: {payload['submit_allowed']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
    print(f"  VTS call: {payload['vts_call']}")
    print(f"  Voice call: {payload['voice_call']}")
    print(f"  OBS call: {payload['obs_call']}")
    print(f"  Vision call: {payload['vision_call']}")


def print_focus_status(payload: dict[str, Any]) -> None:
    print("osu! Input Focus Status")
    print(f"  Focus readiness: {payload['focus_readiness']}")
    print(f"  Window title: {payload['window_title']}")
    print(f"  Window class: {payload['window_class']}")
    print(f"  Process name: {payload['process_name']}")
    print(f"  Is osu window: {payload['is_osu_window']}")
    print(f"  Reason: {payload['reason']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")


def print_emergency_release_preview(payload: dict[str, Any]) -> None:
    print("osu! Emergency Release Preview")
    print(f"  Emergency release available: {payload['emergency_release_available']}")
    print(f"  Release strategy: {payload['release_strategy']}")
    print(f"  Release command available: {payload['release_command_available']}")
    print(f"  Safety release only: {payload['safety_release_only']}")
    print(f"  Reason: {payload['reason']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")


def _format_keys(keys: Any) -> str:
    return ",".join(str(key) for key in (keys or ()))


def print_emergency_release_status(payload: dict[str, Any]) -> None:
    print("osu! Emergency Release Status")
    print(f"  Decision: {payload['decision']}")
    print(f"  Env gate: {payload['env_gate_name']}")
    print(f"  Env gate present: {payload['env_gate_present']}")
    print(f"  Env gate enabled: {payload['env_gate_enabled']}")
    print(f"  Emergency release available: {payload['emergency_release_available']}")
    print(f"  Release strategy: {payload['release_strategy']}")
    print(f"  Release command available: {payload['release_command_available']}")
    print(f"  Release helper available: {payload['release_helper_available']}")
    print(f"  Release keys: {_format_keys(payload['release_keys'])}")
    print(f"  Filtered extra keys: {_format_keys(payload['filtered_extra_keys'])}")
    print(f"  Safety release only: {payload['safety_release_only']}")
    print(f"  Gameplay input: {payload['gameplay_input']}")
    print(f"  Key down sent: {payload['key_down_sent']}")
    print(f"  Key up sent: {payload['key_up_sent']}")
    print(f"  Cursor move: {payload['cursor_move']}")
    print(f"  Click: {payload['click']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
    print(f"  VTS call: {payload['vts_call']}")
    print(f"  Voice call: {payload['voice_call']}")
    print(f"  OBS call: {payload['obs_call']}")


def print_emergency_release_dry_run(payload: dict[str, Any]) -> None:
    print("osu! Emergency Release Dry Run")
    print(f"  Decision: {payload['decision']}")
    print(f"  Env gate present: {payload['env_gate_present']}")
    print(f"  Env gate enabled: {payload['env_gate_enabled']}")
    print(f"  Token present: {payload['token_present']}")
    print(f"  Token valid: {payload['token_valid']}")
    print(f"  Would release keys: {_format_keys(payload['would_release_keys'])}")
    print(f"  Blockers: {payload['blockers']}")
    print(f"  Safety release only: {payload['safety_release_only']}")
    print(f"  Gameplay input: {payload['gameplay_input']}")
    print(f"  Key down sent: {payload['key_down_sent']}")
    print(f"  Key up sent: {payload['key_up_sent']}")
    print(f"  Cursor move: {payload['cursor_move']}")
    print(f"  Click: {payload['click']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
    print(f"  VTS call: {payload['vts_call']}")
    print(f"  Voice call: {payload['voice_call']}")
    print(f"  OBS call: {payload['obs_call']}")


def print_emergency_release_result(payload: dict[str, Any]) -> None:
    print("osu! Emergency Release")
    print(f"  Decision: {payload['decision']}")
    if payload.get("hold_reason"):
        print(f"  Hold reason: {payload['hold_reason']}")
    print(f"  Env gate enabled: {payload['env_gate_enabled']}")
    print(f"  Token valid: {payload['token_valid']}")
    print(f"  Release keys: {_format_keys(payload['release_keys'])}")
    print(f"  Backend: {payload.get('backend')}")
    print(f"  Requested count: {payload.get('requested_count')}")
    print(f"  SendInput return: {payload.get('sendinput_return')}")
    print(f"  Sent count: {payload.get('sent_count')}")
    print(f"  Win32 last error: {payload.get('win32_last_error')}")
    print(f"  Win32 error message: {payload.get('win32_error_message')}")
    print(f"  Backend error: {payload.get('backend_error')}")
    print(f"  Failed keys: {_format_keys(payload.get('failed_keys'))}")
    print(f"  Input size: {payload.get('input_size')}")
    print(f"  Pointer size: {payload.get('pointer_size')}")
    print(f"  Safety release only: {payload['safety_release_only']}")
    print(f"  Gameplay input: {payload['gameplay_input']}")
    print(f"  Key down sent: {payload['key_down_sent']}")
    print(f"  Key up sent: {payload['key_up_sent']}")
    print(f"  Cursor move: {payload['cursor_move']}")
    print(f"  Click: {payload['click']}")
    print(f"  Executable: {payload['executable']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
    print(f"  VTS call: {payload['vts_call']}")
    print(f"  Voice call: {payload['voice_call']}")
    print(f"  OBS call: {payload['obs_call']}")


def print_emergency_abort_preview(payload: dict[str, Any]) -> None:
    print("osu! Emergency Abort Preview")
    print(f"  Emergency abort available: {payload['emergency_abort_available']}")
    print(f"  Abort strategy: {payload['abort_strategy']}")
    print(f"  Abort command available: {payload['abort_command_available']}")
    print(f"  Abort flag path: {payload['abort_flag_path']}")
    print(f"  Abort flag parent exists: {payload['abort_flag_parent_exists']}")
    print(f"  Reason: {payload['reason']}")
    print(f"  Real input: {payload['real_input']}")
    print(f"  Submit: {payload['submit']}")
