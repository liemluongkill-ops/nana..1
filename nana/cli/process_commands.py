"""Process/watch command routing for the CLI."""

from __future__ import annotations

import asyncio


def handle_process_command(vts, voice, text: str, text_lower: str | None = None) -> bool:
    text_lower = text_lower or text.lower()

    if text_lower == "/run":
        print("⚠️ Thiếu command sau /run. Ví dụ: /run python --version")
        return True

    if text_lower.startswith("/run "):
        command = text[5:].strip()
        if not command:
            print("⚠️ Thiếu command sau /run")
            return True
        from nana.runtime.process_watch import watch_process

        asyncio.create_task(watch_process(command, vts, voice))
        print(f"🟢 Nana bắt đầu watch terminal: {command}")
        return True

    return False


__all__ = ["handle_process_command"]
