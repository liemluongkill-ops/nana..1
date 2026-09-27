"""Nana CLI entry point — chạy: python -m nana"""
import asyncio
import sys
from pathlib import Path

_nana_root = Path(__file__).resolve().parent.parent
if str(_nana_root) not in sys.path:
    sys.path.insert(0, str(_nana_root))

from nana.runtime.presence_credentials import activate_presence_session_from_store


def _activate_saved_presence_session() -> None:
    activation = activate_presence_session_from_store()
    if activation.enabled and activation.source == "windows_dpapi":
        print(
            "Presence session auto-enable: Windows protected credential | "
            "listen=0.0.0.0:8765"
        )
    elif activation.error:
        print(f"Presence session credential warning: {activation.error}")


from nana.cli.app import main

if __name__ == "__main__":
    try:
        _activate_saved_presence_session()
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Nana da dung bang Ctrl+C")
