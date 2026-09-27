"""Smoke the Windows-protected Presence credential lifecycle."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nana.runtime.presence_credentials import (
    activate_presence_session_from_store,
    clear_presence_session_token,
    load_presence_session_token,
    save_presence_session_token,
)


def main() -> None:
    if os.name != "nt":
        print("smoke_presence_credentials: SKIP (Windows DPAPI required)")
        return

    token = "presence-smoke-token-not-plaintext"
    with tempfile.TemporaryDirectory(prefix="nana-presence-credential-") as root:
        path = Path(root) / "token.dpapi"
        save_presence_session_token(token, path=path)
        assert path.is_file()
        assert token.encode("utf-8") not in path.read_bytes()
        assert load_presence_session_token(path=path) == token

        environment: dict[str, str] = {}
        activation = activate_presence_session_from_store(environment, path=path)
        assert activation.enabled
        assert activation.source == "windows_dpapi"
        assert environment["NANA_PRESENCE_SESSION_ENABLED"] == "1"
        assert environment["NANA_PRESENCE_SESSION_TOKEN"] == token
        assert environment["NANA_PRESENCE_SESSION_PORT"] == "8765"

        disabled_environment = {"NANA_PRESENCE_SESSION_ENABLED": "0"}
        disabled = activate_presence_session_from_store(
            disabled_environment,
            path=path,
        )
        assert not disabled.enabled
        assert "NANA_PRESENCE_SESSION_TOKEN" not in disabled_environment

        assert clear_presence_session_token(path=path)
        assert load_presence_session_token(path=path) is None
        assert not clear_presence_session_token(path=path)

    print("smoke_presence_credentials: PASS")
    print("  storage: Windows DPAPI, current-user scope")
    print("  startup: auto-enable with explicit opt-out")
    print("  secret: plaintext absent from credential file")


if __name__ == "__main__":
    main()
