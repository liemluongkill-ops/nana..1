"""Start Nana Core with the Presence session server enabled.

The bearer token is loaded from the current Windows user's DPAPI-protected
credential. The first manual prompt stores it securely unless explicitly
disabled. The selected runtime owns bounded PCM16/16 kHz mono uplink and
downlink over the authenticated Wi-Fi session; USB-UART remains maintenance-only.
"""

from __future__ import annotations

import argparse
from getpass import getpass
import os
from pathlib import Path
import subprocess
import sys


DEFAULT_MAIN = Path(__file__).resolve().parents[1] / "__main__.py"
PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from nana.runtime.presence_credentials import (  # noqa: E402
    clear_presence_session_token,
    load_presence_session_token,
    save_presence_session_token,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Nana Core with authenticated Presence control and bounded "
            "half-duplex PCM16 audio enabled."
        )
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--heartbeat-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    parser.add_argument("--main", type=Path, default=DEFAULT_MAIN)
    parser.add_argument(
        "--prompt-token",
        action="store_true",
        help="Prompt for and replace the Windows-protected token",
    )
    parser.add_argument(
        "--no-store-token",
        action="store_true",
        help="Do not persist a token entered at the hidden prompt",
    )
    parser.add_argument(
        "--forget-token",
        action="store_true",
        help="Delete the Windows-protected token and exit",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.forget_token:
        removed = clear_presence_session_token()
        print(
            "Presence protected credential removed."
            if removed
            else "No Presence protected credential was stored."
        )
        return 0
    main_path = args.main.expanduser().resolve()
    if not main_path.is_file():
        raise SystemExit(f"Nana entrypoint does not exist: {main_path}")
    if not 1 <= args.port <= 65535:
        raise SystemExit("--port must be between 1 and 65535")
    if args.heartbeat_seconds <= 0:
        raise SystemExit("--heartbeat-seconds must be positive")
    if args.timeout_seconds <= args.heartbeat_seconds:
        raise SystemExit("--timeout-seconds must exceed --heartbeat-seconds")

    token = os.environ.get("NANA_PRESENCE_SESSION_TOKEN", "").strip()
    token_source = "environment"
    if not token and not args.prompt_token:
        token = load_presence_session_token() or ""
        token_source = "windows_dpapi"
    if not token or args.prompt_token:
        token = getpass("Presence bearer token: ")
        token_source = "prompt"
    if not token.strip():
        raise SystemExit("Presence bearer token cannot be empty")
    token = token.strip()
    if token_source == "prompt" and not args.no_store_token:
        try:
            save_presence_session_token(token)
        except (OSError, RuntimeError, ValueError) as exc:
            raise SystemExit(f"Could not protect the Presence token: {exc}") from exc
        print("Presence bearer token saved with Windows DPAPI.")
    elif token_source == "windows_dpapi":
        print("Presence bearer token loaded from Windows DPAPI.")

    environment = os.environ.copy()
    environment.update(
        {
            "NANA_PRESENCE_SESSION_ENABLED": "1",
            "NANA_PRESENCE_SESSION_HOST": args.host,
            "NANA_PRESENCE_SESSION_PORT": str(args.port),
            "NANA_PRESENCE_SESSION_TOKEN": token,
            "NANA_PRESENCE_SESSION_HEARTBEAT_SECONDS": str(
                args.heartbeat_seconds
            ),
            "NANA_PRESENCE_SESSION_TIMEOUT_SECONDS": str(args.timeout_seconds),
        }
    )

    try:
        completed = subprocess.run(
            [sys.executable, "-u", str(main_path)],
            cwd=str(main_path.parent.parent),
            env=environment,
            check=False,
        )
    except KeyboardInterrupt:
        return 130
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
