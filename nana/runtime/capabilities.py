import os
import re
from functools import lru_cache
from importlib import import_module
from typing import Any, Optional, Tuple


STARDEW_ADAPTER_ENV = "NANA_STARDEW_ADAPTER_ENABLED"
STARDEW_ADAPTER_AUTO_ZONE_ENV = "NANA_STARDEW_ADAPTER_AUTO_ZONE_ENABLED"
STARDEW_ENV_PREFIX = "NANA_STARDEW_"
STARDEW_REGISTRY_MODULE = "nana.game.stardew.registry"
STARDEW_WINDOW_KEYWORDS = ("stardew", "stardew valley", "stardewmoddingapi", "smapi")
OSU_ADAPTER_ENV = "NANA_OSU_ADAPTER_ENABLED"
OSU_ADAPTER_AUTO_ZONE_ENV = "NANA_OSU_ADAPTER_AUTO_ZONE_ENABLED"
OSU_ENV_PREFIX = "NANA_OSU_"
OSU_REGISTRY_MODULE = "nana.game.osu.registry"
OSU_WINDOW_KEYWORDS = ("osu!", "osu.exe", "osu")
_STARDEW_RUNTIME_OVERRIDE: Optional[str] = None
_STARDEW_RUNTIME_REASON: Optional[str] = None
_OSU_RUNTIME_OVERRIDE: Optional[str] = None
_OSU_RUNTIME_REASON: Optional[str] = None


def _toggle_value(raw: Any) -> Optional[str]:
    if raw is not None:
        value = str(raw).strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return "on"
        if value in {"0", "false", "no", "off"}:
            return "off"
    return None


def _truthy_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _stardew_env_autodetect_enabled() -> bool:
    for key, value in os.environ.items():
        if key == STARDEW_ADAPTER_ENV:
            continue
        if key.startswith(STARDEW_ENV_PREFIX) and str(value).strip():
            return True
    return False


def _osu_env_autodetect_enabled() -> bool:
    for key, value in os.environ.items():
        if key == OSU_ADAPTER_ENV:
            continue
        if key.startswith(OSU_ENV_PREFIX) and str(value).strip():
            return True
    return False


def _window_matches(active_app: str = "", active_title: str = "", keywords=()) -> bool:
    combined = f"{active_app or ''} {active_title or ''}".lower()
    return any(keyword in combined for keyword in keywords)


def _auto_adapter_response(status: dict[str, Any], *, changed: bool, reason: str) -> dict[str, Any]:
    response = dict(status)
    response["adapter_reason"] = response.get("reason")
    response["changed"] = changed
    response["reason"] = reason
    return response


@lru_cache(maxsize=1)
def stardew_adapter_mode() -> str:
    if _STARDEW_RUNTIME_OVERRIDE in {"on", "off"}:
        return _STARDEW_RUNTIME_OVERRIDE
    env_mode = _toggle_value(os.getenv(STARDEW_ADAPTER_ENV))
    if env_mode:
        return env_mode
    return "auto_on" if _stardew_env_autodetect_enabled() else "auto_off"


def stardew_adapter_enabled() -> bool:
    return stardew_adapter_mode() in {"on", "auto_on"}


def stardew_adapter_status() -> dict[str, Any]:
    mode = stardew_adapter_mode()
    if _STARDEW_RUNTIME_OVERRIDE in {"on", "off"}:
        reason = _STARDEW_RUNTIME_REASON or "runtime_override"
    else:
        reason = "env_toggle" if mode in {"on", "off"} else "env_autodetect"
    return {
        "mode": mode,
        "enabled": mode in {"on", "auto_on"},
        "reason": reason,
        "runtime_override": _STARDEW_RUNTIME_OVERRIDE,
        "auto_zone_enabled": stardew_adapter_auto_zone_enabled(),
    }


def stardew_adapter_auto_zone_enabled() -> bool:
    return _truthy_env(STARDEW_ADAPTER_AUTO_ZONE_ENV, default=True)


def set_stardew_adapter_runtime_mode(mode: str, *, reason: str = "manual") -> dict[str, Any]:
    global _STARDEW_RUNTIME_OVERRIDE, _STARDEW_RUNTIME_REASON
    normalized = str(mode or "").strip().lower()
    if normalized in {"auto", "clear", "reset"}:
        _STARDEW_RUNTIME_OVERRIDE = None
        _STARDEW_RUNTIME_REASON = reason or "manual_auto"
    else:
        toggle = _toggle_value(normalized)
        if toggle not in {"on", "off"}:
            raise ValueError(f"unsupported_stardew_adapter_mode:{mode}")
        _STARDEW_RUNTIME_OVERRIDE = toggle
        _STARDEW_RUNTIME_REASON = reason or "manual"
    stardew_adapter_mode.cache_clear()
    return stardew_adapter_status()


def maybe_auto_enable_stardew_adapter(active_zone: str, active_app: str = "", active_title: str = "") -> dict[str, Any]:
    if not stardew_adapter_auto_zone_enabled():
        return _auto_adapter_response(stardew_adapter_status(), changed=False, reason="auto_zone_disabled")
    if _STARDEW_RUNTIME_OVERRIDE == "off":
        return _auto_adapter_response(stardew_adapter_status(), changed=False, reason="runtime_off")
    env_mode = _toggle_value(os.getenv(STARDEW_ADAPTER_ENV))
    if env_mode == "off" and _STARDEW_RUNTIME_OVERRIDE is None:
        return _auto_adapter_response(stardew_adapter_status(), changed=False, reason="env_off")
    if stardew_adapter_enabled():
        return _auto_adapter_response(stardew_adapter_status(), changed=False, reason="already_enabled")
    if str(active_zone or "").lower() != "game":
        return _auto_adapter_response(stardew_adapter_status(), changed=False, reason="zone_not_game")
    if not _window_matches(active_app, active_title, STARDEW_WINDOW_KEYWORDS):
        return _auto_adapter_response(stardew_adapter_status(), changed=False, reason="window_not_stardew")
    set_stardew_adapter_runtime_mode(
        "on",
        reason=f"auto_zone:{active_app or active_title or 'game'}",
    )
    return _auto_adapter_response(stardew_adapter_status(), changed=True, reason="auto_zone_game")


@lru_cache(maxsize=1)
def osu_adapter_mode() -> str:
    if _OSU_RUNTIME_OVERRIDE in {"on", "off"}:
        return _OSU_RUNTIME_OVERRIDE
    env_mode = _toggle_value(os.getenv(OSU_ADAPTER_ENV))
    if env_mode:
        return env_mode
    return "auto_on" if _osu_env_autodetect_enabled() else "auto_off"


def osu_adapter_enabled() -> bool:
    return osu_adapter_mode() in {"on", "auto_on"}


def osu_adapter_auto_zone_enabled() -> bool:
    return _truthy_env(OSU_ADAPTER_AUTO_ZONE_ENV, default=True)


def osu_adapter_status() -> dict[str, Any]:
    mode = osu_adapter_mode()
    if _OSU_RUNTIME_OVERRIDE in {"on", "off"}:
        reason = _OSU_RUNTIME_REASON or "runtime_override"
    else:
        reason = "env_toggle" if mode in {"on", "off"} else "env_autodetect"
    return {
        "mode": mode,
        "enabled": mode in {"on", "auto_on"},
        "reason": reason,
        "runtime_override": _OSU_RUNTIME_OVERRIDE,
        "auto_zone_enabled": osu_adapter_auto_zone_enabled(),
    }


def set_osu_adapter_runtime_mode(mode: str, *, reason: str = "manual") -> dict[str, Any]:
    global _OSU_RUNTIME_OVERRIDE, _OSU_RUNTIME_REASON
    normalized = str(mode or "").strip().lower()
    if normalized in {"auto", "clear", "reset"}:
        _OSU_RUNTIME_OVERRIDE = None
        _OSU_RUNTIME_REASON = reason or "manual_auto"
    else:
        toggle = _toggle_value(normalized)
        if toggle not in {"on", "off"}:
            raise ValueError(f"unsupported_osu_adapter_mode:{mode}")
        _OSU_RUNTIME_OVERRIDE = toggle
        _OSU_RUNTIME_REASON = reason or "manual"
    osu_adapter_mode.cache_clear()
    return osu_adapter_status()


def maybe_auto_enable_osu_adapter(active_zone: str, active_app: str = "", active_title: str = "") -> dict[str, Any]:
    if not osu_adapter_auto_zone_enabled():
        return _auto_adapter_response(osu_adapter_status(), changed=False, reason="auto_zone_disabled")
    if _OSU_RUNTIME_OVERRIDE == "off":
        return _auto_adapter_response(osu_adapter_status(), changed=False, reason="runtime_off")
    env_mode = _toggle_value(os.getenv(OSU_ADAPTER_ENV))
    if env_mode == "off" and _OSU_RUNTIME_OVERRIDE is None:
        return _auto_adapter_response(osu_adapter_status(), changed=False, reason="env_off")
    if osu_adapter_enabled():
        return _auto_adapter_response(osu_adapter_status(), changed=False, reason="already_enabled")
    if str(active_zone or "").lower() != "game":
        return _auto_adapter_response(osu_adapter_status(), changed=False, reason="zone_not_game")
    if not _window_matches(active_app, active_title, OSU_WINDOW_KEYWORDS):
        return _auto_adapter_response(osu_adapter_status(), changed=False, reason="window_not_osu")
    set_osu_adapter_runtime_mode(
        "on",
        reason=f"auto_zone:{active_app or active_title or 'game'}",
    )
    return _auto_adapter_response(osu_adapter_status(), changed=True, reason="auto_zone_game")


def invoke_stardew_v2_command(handler_name, *args, **kwargs):
    commands = import_module("nana.game.stardew.commands")
    return getattr(commands, handler_name)(*args, **kwargs)


def build_osu_executor_preflight_payload(text="", **kwargs):
    executor_preflight = import_module("nana.game.osu.executor_preflight")
    return executor_preflight.build_executor_preflight_payload(text, **kwargs)


def _load_stardew_registry() -> Any:
    if not stardew_adapter_enabled():
        return None
    return import_module(STARDEW_REGISTRY_MODULE)


def _load_osu_registry() -> Any:
    if not osu_adapter_enabled():
        return None
    return import_module(OSU_REGISTRY_MODULE)


def register_stardew_phase_commands(command_set, *, min_phase=None, max_phase=None, ctx=None) -> set[str]:
    registry = _load_stardew_registry()
    if registry is None:
        return set()
    return registry.register_phase_commands(command_set, min_phase=min_phase, max_phase=max_phase, ctx=ctx)


def register_osu_commands(command_set, *, ctx=None) -> set[str]:
    registry = _load_osu_registry()
    if registry is None:
        return set()
    return registry.register_commands(command_set, ctx=ctx)


def resolve_stardew_export(name: str, ctx=None) -> Tuple[bool, Optional[Any]]:
    registry = _load_stardew_registry()
    if registry is None:
        return False, None
    return registry.resolve_export(name, ctx)


def handle_stardew_phase_command(ctx, text, vts=None, voice=None, *, min_phase=None, max_phase=None):
    registry = _load_stardew_registry()
    if registry is None:
        return None
    return registry.handle_phase_command(ctx, text, vts, voice, min_phase=min_phase, max_phase=max_phase)


def handle_osu_command(ctx, text, vts=None, voice=None):
    registry = _load_osu_registry()
    if registry is None:
        return None
    return registry.handle_command(ctx, text, vts, voice)


async def handle_osu_command_async(ctx, text, vts=None, voice=None):
    registry = _load_osu_registry()
    if registry is None:
        return None
    command = str(text or "").strip().split(" ", 1)[0].lower()
    if command == "/osu-vts-reaction-send":
        await registry.run_osu_vts_reaction_send(text)
        return False
    return registry.handle_command(ctx, text, vts, voice)


def is_stardew_command(text: str) -> bool:
    command = str(text or "").strip().split(" ", 1)[0].lower()
    return command.startswith("/stardew")


def is_osu_command(text: str) -> bool:
    command = str(text or "").strip().split(" ", 1)[0].lower()
    return command.startswith("/osu")


def is_stardew_adapter_command(text: str) -> bool:
    command = str(text or "").strip().split(" ", 1)[0].lower()
    if command.startswith("/stardew"):
        return True
    match = re.match(r"^/(?:phase|p)(\d{1,4})(?:$|[-_])", command)
    if not match:
        return False
    try:
        phase = int(match.group(1))
    except ValueError:
        return False
    return 33 <= phase <= 80 or phase >= 86
