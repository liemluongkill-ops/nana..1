"""VTS command routing for the CLI."""

from __future__ import annotations


async def handle_vts_command(text_lower: str) -> bool:
    if text_lower in {"/vts-status"}:
        from nana.integrations.vts import get_vts_runtime, vts_snapshot
        from nana.runtime.expression_router import get_expression_router

        snapshot = vts_snapshot(get_vts_runtime())
        router = get_expression_router()
        router_status = router.get_status()

        print("🎭 VTS Status")
        print(f"  Ready: {snapshot['ready']}")
        print(f"  Connected: {snapshot['connected']}")
        print(f"  Last error: {snapshot['last_error']}")
        print(f"  Token/auth status: {snapshot['auth_status_label']} ({snapshot['auth_status']})")
        print()
        print("  Expression Policy:")
        print(f"    Enabled: {router_status['enabled']}")
        print(f"    VTS available: {router_status['vts_available']}")
        print(f"    VTS connected: {router_status['vts_connected']}")
        print(f"    Cooldown: {router_status['cooldown_remaining_seconds']:.1f}s remaining")
        print(f"    Missing expression count: {router_status['missing_count_total']}")
        if router_status["last_missing_expression"]:
            print(f"    Last missing: {router_status['last_missing_expression']}")
        print(f"    Catalog size: {router_status['catalog_size']} expressions")
        print("  Real input: False")
        return True

    if text_lower in {"/vts-connect"}:
        from nana.integrations.vts import ensure_vts_ready

        status = await ensure_vts_ready(force=True, announce=True)
        snapshot = status["snapshot"]
        print("🎭 VTS Connect")
        print(f"  Ready: {snapshot['ready']}")
        print(f"  Connected: {snapshot['connected']}")
        print(f"  Last error: {snapshot['last_error']}")
        print(f"  Token/auth status: {snapshot['auth_status_label']} ({snapshot['auth_status']})")
        print("  Real input: False")
        return True

    return False


__all__ = ["handle_vts_command"]
