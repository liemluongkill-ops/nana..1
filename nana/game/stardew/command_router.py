"""Stardew Adapter V2 compatibility router.

The live CLI dispatches the compact V2 observer/planner/bridge surface. This
module keeps older router probes meaningful by exposing metadata, visible
read-only commands, and archived-command messages without reviving retired
movement phases.
"""

from __future__ import annotations

from nana.game.stardew import command_catalog, command_surface


def _norm(text: str) -> str:
    return str(text or "").strip().split(" ", 1)[0].lower()


def _is_stardew_adapter_command(command: str) -> bool:
    cmd = _norm(command)
    return cmd.startswith(("/stardew", "/phase", "/p"))


def sync_command_surface(
    command_set,
    *,
    static_commands=None,
    register_phase_commands=None,
    adapter_enabled: bool,
    is_stardew_adapter_command=None,
    ctx=None,
    min_phase=None,
    max_phase=None,
):
    checker = is_stardew_adapter_command or _is_stardew_adapter_command
    return command_surface.sync_stardew_known_commands(
        command_set,
        static_commands=static_commands,
        register_phase_commands=register_phase_commands,
        adapter_enabled=adapter_enabled,
        is_stardew_adapter_command=checker,
        ctx=ctx,
        min_phase=min_phase,
        max_phase=max_phase,
    )


def handle_adapter_command(
    text: str,
    *,
    status_provider=None,
    set_runtime_mode=None,
    sync_surface=None,
):
    cmd = _norm(text)
    if cmd not in {
        "/adapter-status",
        "/game-adapter-status",
        "/adapter-on",
        "/game-adapter-on",
        "/adapter-off",
        "/game-adapter-off",
        "/adapter-auto",
        "/game-adapter-auto",
    }:
        return None

    if status_provider is None or set_runtime_mode is None or sync_surface is None:
        return None

    if cmd.endswith("-status"):
        status = status_provider()
        sync = sync_surface()
        print("Game adapter status")
        print(f"  Enabled: {str(status.get('enabled', False)).lower()}")
        print(f"  Mode: {status.get('mode') or 'unknown'}")
        print(f"  Reason: {status.get('reason') or 'none'}")
        print(f"  Runtime override: {status.get('runtime_override') or 'none'}")
        print(f"  Auto zone enabled: {str(status.get('auto_zone_enabled', False)).lower()}")
        print(f"  Stardew commands visible: {sync.get('stardew_count', 0)}")
        return False

    mode = "auto"
    if cmd.endswith("-on"):
        mode = "on"
    elif cmd.endswith("-off"):
        mode = "off"

    status = set_runtime_mode(mode, reason="command_router")
    sync = sync_surface()
    print(f"Game adapter {mode}")
    print(f"  Enabled: {str(status.get('enabled', False)).lower()}")
    print(f"  Mode: {status.get('mode') or mode}")
    print(f"  Stardew commands visible: {sync.get('stardew_count', 0)}")
    return False


def guard_adapter_command(text: str, *, adapter_enabled: bool, sync_surface):
    if not _is_stardew_adapter_command(text):
        return None
    if not adapter_enabled:
        print("Stardew adapter inactive.")
        return False
    sync_surface()
    return None


def _print_runtime_validation() -> None:
    print("Runtime Read-Only Validation")
    print("  Decision: observer/planner only")
    print("  Python game input: disabled")
    print("  Executor: read_only_observer")


def _print_travel_status() -> None:
    print("Stardew travel status")
    print("  Surface: current movement metadata")
    print("  Mode: read_only_observer")


def _print_archived(command: str) -> None:
    metadata = command_catalog.archived_metadata(command) or {}
    print("archived_stardew_command_disabled")
    print(f"  Command: {command}")
    print(f"  Reason: {metadata.get('reason') or 'command_moved_to_archive_registry'}")
    print(f"  Replacement: {metadata.get('replacement') or 'stardew_adapter_v2_observer_planner_bridge'}")


def handle_phase_command(ctx=None, text=None, vts=None, voice=None, *, adapter_enabled=True, min_phase=None, max_phase=None):
    if not adapter_enabled:
        return None

    if text is None and isinstance(ctx, str):
        text = ctx
        ctx = None

    cmd = _norm(text)
    if not cmd:
        return None

    entry = command_catalog.best_command_entry(cmd)
    if entry is None:
        return None

    if entry.visibility != command_catalog.VISIBILITY_VISIBLE:
        if entry.visibility == command_catalog.VISIBILITY_HIDDEN_SMOKE_ONLY:
            _print_runtime_validation()
        else:
            _print_archived(cmd)
        return False

    if cmd == "/stardew-runtime-validation":
        _print_runtime_validation()
        return False
    if cmd == "/stardew-travel-status":
        _print_travel_status()
        return False
    if cmd == "/stardew-travel-run":
        print("Stardew travel run")
        print("  Decision: operator-gated run surface only")
        print("  Python game input: disabled unless external approval gates are armed")
        return False
    return None


def handle_legacy_phase_491_590_command(*_args, **_kwargs):
    return None


def handle_live_supervised_control_command(ctx, text, vts=None, voice=None, *, adapter_enabled=True):
    cmd = _norm(text)
    if not cmd.startswith("/stardew-live"):
        return None
    print("archived_stardew_command_disabled")
    print("  Reason: live_supervised_control_requires_explicit_executor_gate")
    return False


def __getattr__(name: str):
    if name.startswith("handle_legacy_phase_") and name.endswith("_command"):
        return handle_phase_command
    raise AttributeError(name)
