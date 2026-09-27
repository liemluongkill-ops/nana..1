"""Awareness command routing for the CLI."""

from __future__ import annotations


def handle_awareness_command(text_lower: str) -> bool:
    if text_lower in {"/awareness-status", "/awareness"}:
        from nana.runtime.awareness_memory import get_awareness_memory
        from nana.runtime.live_awareness import (
            build_live_awareness_snapshot,
            format_awareness_status,
            lock_focus,
        )

        aw_snap = None
        try:
            aw_snap = build_live_awareness_snapshot()
            try:
                get_awareness_memory().record_live_awareness(awareness=aw_snap)
                lock_focus(aw_snap, reason="awareness_check")
            except Exception:
                pass
        except Exception:
            pass
        print(format_awareness_status())
        return True

    if text_lower in {"/awareness-memory-status", "/awareness-memory", "/aw-memory"}:
        from nana.runtime.awareness_memory import get_awareness_memory

        print(get_awareness_memory().format_status())
        return True

    return False


__all__ = ["handle_awareness_command"]
