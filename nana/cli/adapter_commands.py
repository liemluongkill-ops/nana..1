"""External adapter command routing for the CLI."""

from __future__ import annotations


async def handle_adapter_command(globals_dict, vts, voice, text: str, text_lower: str | None = None):
    text_lower = text_lower or text.lower()

    if text_lower in {"/osu-game-status", "/osu-adapter-status"}:
        from nana.commands.dynamic import sync_osu_command_surface
        from nana.runtime.capabilities import osu_adapter_status

        sync = sync_osu_command_surface()
        status = osu_adapter_status()
        print("osu! adapter status")
        print(f"  Enabled: {status['enabled']} mode={status['mode']} reason={status['reason']}")
        print(f"  Runtime override: {status['runtime_override']}")
        print(f"  Auto zone enabled: {status['auto_zone_enabled']}")
        print(f"  osu commands visible: {sync['osu_count']}")
        return False

    if text_lower == "/osu-adapter-on":
        from nana.commands.dynamic import sync_osu_command_surface
        from nana.runtime.capabilities import set_osu_adapter_runtime_mode

        status = set_osu_adapter_runtime_mode("on", reason="manual_command")
        sync = sync_osu_command_surface()
        print("osu! adapter on")
        print(f"  mode={status['mode']} enabled={status['enabled']} reason={status['reason']}")
        print(f"  osu commands visible: {sync['osu_count']}")
        return False

    if text_lower == "/osu-adapter-off":
        from nana.commands.dynamic import sync_osu_command_surface
        from nana.runtime.capabilities import set_osu_adapter_runtime_mode

        status = set_osu_adapter_runtime_mode("off", reason="manual_command")
        sync = sync_osu_command_surface()
        print("osu! adapter off")
        print(f"  mode={status['mode']} enabled={status['enabled']} reason={status['reason']}")
        print(f"  osu commands visible: {sync['osu_count']}")
        return False

    if text_lower == "/osu-adapter-auto":
        from nana.commands.dynamic import sync_osu_command_surface
        from nana.runtime.capabilities import set_osu_adapter_runtime_mode

        status = set_osu_adapter_runtime_mode("auto", reason="manual_command")
        sync = sync_osu_command_surface()
        print("osu! adapter auto")
        print(f"  mode={status['mode']} enabled={status['enabled']} reason={status['reason']}")
        print(f"  osu commands visible: {sync['osu_count']}")
        return False

    from nana.runtime.capabilities import handle_osu_command_async, is_osu_command, osu_adapter_enabled

    if is_osu_command(text):
        if not osu_adapter_enabled():
            print("osu! adapter inactive.")
            return False
        from nana.commands.dynamic import sync_osu_command_surface

        sync_osu_command_surface()
        return False

    handled = await handle_osu_command_async(globals_dict, text, vts, voice)
    if handled is not None:
        return handled

    return None


def handle_stardew_adapter_command(text_lower: str) -> bool:
    from nana.commands.dynamic import handle_stardew_v2_command, is_stardew_adapter_command

    if is_stardew_adapter_command(text_lower):
        handle_stardew_v2_command(text_lower)
        return True

    return False


__all__ = ["handle_adapter_command", "handle_stardew_adapter_command"]
