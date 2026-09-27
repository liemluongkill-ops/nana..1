"""CLI wrapper for Nana identity diagnostics."""

from __future__ import annotations

from nana.runtime.identity import (
    format_identity_block,
    load_identity,
    load_users,
    reload_all,
    resolve_user,
)


async def handle_identity_command(text: str):
    """Handle /identity and /identity_done commands without importing legacy main."""

    lowered = (text or "").lower().strip()
    parts = lowered.split(maxsplit=2)
    cmd = parts[0] if parts else "/identity"
    sub = parts[1] if len(parts) > 1 else ""
    rest = parts[2] if len(parts) > 2 else ""

    if cmd == "/identity_done":
        print("✅ identity_done — identity layer đã load. Ba chat bình thường nhé.")
        print("   Identity file: nana/data/identity.json")
        print("   Users file:    nana/data/users.json")
        print("   Trigger reload: /identity reload")
        return False

    if sub == "reload":
        data = reload_all()
        print("🔄 Identity reloaded từ JSON.")
        print(f"   identity keys: {list((data.get('identity') or {}).keys())}")
        print(f"   users: {list((data.get('users') or {}).keys())}")
        return False

    if sub == "test":
        viewer = rest.strip() or None
        user = resolve_user(message="", viewer_name=viewer, stream_mode=bool(viewer))
        print(f"🧪 resolve_user(viewer_name={viewer!r}) ->")
        for key, value in user.items():
            print(f"   {key}: {value}")
        return False

    if sub == "block":
        current = resolve_user(message="", viewer_name=None, stream_mode=False)
        block = format_identity_block(current)
        print("📜 Identity block (sẽ inject vào system prompt):")
        print("─" * 60)
        print(block)
        print("─" * 60)
        return False

    identity = load_identity()
    users = load_users()
    current = resolve_user(message="", viewer_name=None, stream_mode=False)
    print("🪪 Identity hiện tại:")
    print(f"  self_name:     {identity.get('self_name')}")
    print(f"  self_pronoun:  {identity.get('self_pronoun')}")
    print(f"  nana_role:     {identity.get('nana_role')}")
    print(f"  nana_birth:    {identity.get('nana_birth')}")
    print(f"  default_user:  {identity.get('default_user_id')}")
    print(f"  addressing:    {list((identity.get('addressing_rules') or {}).keys())}")
    print(f"  facts count:   {len(identity.get('facts') or [])}")
    print("  users:")
    for uid, user in users.items():
        if isinstance(user, dict):
            print(
                f"    - {uid}: name={user.get('name')!r} "
                f"role={user.get('role')!r} aliases={user.get('aliases')}"
            )
    print(
        "  resolved (default): "
        f"name={current.get('name')!r} "
        f"role={current.get('role')!r} "
        f"pronoun={current.get('pronoun')!r}"
    )
    print("  sub-cmds: reload | test <viewer_name> | block")
    return False
