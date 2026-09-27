"""Nana CLI entry point — chạy asyncio.run(main())."""
import asyncio
import sys
from pathlib import Path

# Ensure nana package is on path
_nana_root = Path(__file__).resolve().parent.parent
if str(_nana_root) not in sys.path:
    sys.path.insert(0, str(_nana_root))

from nana.cli.app import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Nana da dung bang Ctrl+C")
