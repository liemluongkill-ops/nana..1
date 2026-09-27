"""Small /admin command facade for the new Nana runtime."""
from __future__ import annotations

from nana.memory import memory, memory_lock, save_memory_async


def handle_admin(text):
    """Handle legacy /admin/* memory commands without importing nana.main."""
    if not str(text or "").startswith("/admin/"):
        return False

    command = str(text or "").replace("/admin/", "", 1).strip()
    if command == "clear":
        with memory_lock:
            memory["long_term"] = []
        save_memory_async()
        print("đã xoá luật")
        return True

    if command:
        with memory_lock:
            memory.setdefault("long_term", []).append(command)
            memory["long_term"] = memory["long_term"][-50:]
        save_memory_async()
        print("đã lưu:", command)
        return True

    return False
