"""Windows-protected credential storage for the Nana Presence session."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
from typing import MutableMapping


_FILE_MAGIC = b"NANA-PRESENCE-DPAPI-V1\0"
_OPTIONAL_ENTROPY = b"nana.presence.session.v1"
_MAX_TOKEN_BYTES = 128
_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}


class PresenceCredentialError(RuntimeError):
    """Raised when a protected Presence credential cannot be used."""


@dataclass(frozen=True)
class PresenceCredentialActivation:
    enabled: bool
    source: str
    error: str | None = None


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(wintypes.BYTE)),
    ]


def default_presence_credential_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home() / "AppData" / "Local"
    return base / "Nana" / "credentials" / "presence_session_token.dpapi"


def _validate_token(token: str) -> str:
    normalized = str(token or "").strip()
    encoded = normalized.encode("utf-8")
    if not encoded:
        raise ValueError("Presence session token cannot be empty")
    if len(encoded) > _MAX_TOKEN_BYTES:
        raise ValueError(
            f"Presence session token must be at most {_MAX_TOKEN_BYTES} UTF-8 bytes"
        )
    return normalized


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = (wintypes.BYTE * len(data)).from_buffer_copy(data)
    return (
        _DataBlob(
            len(data),
            ctypes.cast(buffer, ctypes.POINTER(wintypes.BYTE)),
        ),
        buffer,
    )


def _dpapi_transform(data: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise PresenceCredentialError(
            "Presence credential storage requires Windows DPAPI"
        )

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    input_blob, input_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(_OPTIONAL_ENTROPY)
    output_blob = _DataBlob()
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN

    if protect:
        function = crypt32.CryptProtectData
        function.argtypes = [
            ctypes.POINTER(_DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        arguments = (
            ctypes.byref(input_blob),
            "Nana Presence Session",
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    else:
        function = crypt32.CryptUnprotectData
        function.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(_DataBlob),
        ]
        arguments = (
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )

    function.restype = wintypes.BOOL
    if not function(*arguments):
        error_code = ctypes.get_last_error()
        raise PresenceCredentialError(
            f"Windows DPAPI failed: {ctypes.FormatError(error_code).strip()}"
        )

    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))
        # Keep ctypes-owned input buffers alive through the native call.
        _ = input_buffer, entropy_buffer


def save_presence_session_token(
    token: str,
    *,
    path: Path | None = None,
) -> Path:
    normalized = _validate_token(token)
    target = Path(path) if path is not None else default_presence_credential_path()
    protected = _dpapi_transform(normalized.encode("utf-8"), protect=True)
    payload = _FILE_MAGIC + protected
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return target


def load_presence_session_token(*, path: Path | None = None) -> str | None:
    target = Path(path) if path is not None else default_presence_credential_path()
    try:
        payload = target.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PresenceCredentialError(
            f"Cannot read the protected Presence credential: {exc}"
        ) from exc
    if not payload.startswith(_FILE_MAGIC):
        raise PresenceCredentialError("Presence credential file has an unknown format")
    protected = payload[len(_FILE_MAGIC) :]
    if not protected:
        raise PresenceCredentialError("Presence credential file is empty")
    try:
        token = _dpapi_transform(protected, protect=False).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PresenceCredentialError(
            "Protected Presence credential is not valid UTF-8"
        ) from exc
    return _validate_token(token)


def clear_presence_session_token(*, path: Path | None = None) -> bool:
    target = Path(path) if path is not None else default_presence_credential_path()
    try:
        target.unlink()
    except FileNotFoundError:
        return False
    return True


def activate_presence_session_from_store(
    environ: MutableMapping[str, str] | None = None,
    *,
    path: Path | None = None,
) -> PresenceCredentialActivation:
    environment = os.environ if environ is None else environ
    enabled_raw = environment.get("NANA_PRESENCE_SESSION_ENABLED")
    enabled_value = str(enabled_raw or "").strip().lower()
    explicitly_disabled = enabled_raw is not None and enabled_value in _FALSE_VALUES
    explicitly_enabled = enabled_raw is not None and enabled_value in _TRUE_VALUES

    if explicitly_disabled:
        return PresenceCredentialActivation(False, "explicitly_disabled")

    environment_token = str(
        environment.get("NANA_PRESENCE_SESSION_TOKEN", "") or ""
    ).strip()
    if environment_token:
        try:
            environment["NANA_PRESENCE_SESSION_TOKEN"] = _validate_token(
                environment_token
            )
        except ValueError as exc:
            return PresenceCredentialActivation(False, "environment", str(exc))
        if enabled_raw is None:
            environment["NANA_PRESENCE_SESSION_ENABLED"] = "1"
        _set_presence_defaults(environment)
        return PresenceCredentialActivation(True, "environment")

    if enabled_raw is not None and not explicitly_enabled:
        return PresenceCredentialActivation(False, "invalid_enable_value")

    try:
        stored_token = load_presence_session_token(path=path)
    except (PresenceCredentialError, ValueError) as exc:
        return PresenceCredentialActivation(False, "windows_dpapi", str(exc))
    if stored_token is None:
        return PresenceCredentialActivation(False, "missing")

    environment["NANA_PRESENCE_SESSION_ENABLED"] = "1"
    environment["NANA_PRESENCE_SESSION_TOKEN"] = stored_token
    _set_presence_defaults(environment)
    return PresenceCredentialActivation(True, "windows_dpapi")


def _set_presence_defaults(environment: MutableMapping[str, str]) -> None:
    environment.setdefault("NANA_PRESENCE_SESSION_HOST", "0.0.0.0")
    environment.setdefault("NANA_PRESENCE_SESSION_PORT", "8765")
    environment.setdefault("NANA_PRESENCE_SESSION_HEARTBEAT_SECONDS", "5")
    environment.setdefault("NANA_PRESENCE_SESSION_TIMEOUT_SECONDS", "15")


__all__ = [
    "PresenceCredentialActivation",
    "PresenceCredentialError",
    "activate_presence_session_from_store",
    "clear_presence_session_token",
    "default_presence_credential_path",
    "load_presence_session_token",
    "save_presence_session_token",
]
