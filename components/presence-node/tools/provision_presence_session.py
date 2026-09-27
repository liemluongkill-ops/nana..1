from __future__ import annotations

import argparse
import base64
import getpass
from pathlib import Path
import secrets
import sys
import time
import urllib.parse

import serial
from serial.tools import list_ports


CH343_VID = 0x1A86
CH343_PID = 0x55D3
SESSION_PATH = "/presence/v1"
PACKAGE_PARENT = Path(__file__).resolve().parents[2]
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from nana.runtime.presence_credentials import (  # noqa: E402
    clear_presence_session_token,
    load_presence_session_token,
    save_presence_session_token,
)


def find_ch343_port() -> str:
    matches = [
        port.device
        for port in list_ports.comports()
        if port.vid == CH343_VID and port.pid == CH343_PID
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError("CH343 USB-UART port not found")
    raise RuntimeError(f"Multiple CH343 ports found: {', '.join(matches)}")


def validate_uri(value: str) -> str:
    uri = value.strip()
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "ws":
        raise ValueError("Milestone 1 requires a LAN ws:// URI")
    if not parsed.hostname or not parsed.port:
        raise ValueError("URI must include the Nana Core LAN host and port")
    if parsed.path != SESSION_PATH or parsed.params or parsed.query or parsed.fragment:
        raise ValueError(f"URI path must be exactly {SESSION_PATH}")
    return uri


def encode_field(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def build_set_command(uri: str, token: str) -> bytes:
    return (
        f"NANA_SESSION_SET {encode_field(uri)} {encode_field(token)}\n"
    ).encode("ascii")


def serial_session(
    *,
    port: str,
    command: bytes,
    timeout: float,
    success_markers: tuple[str, ...] = (
        "NANA_SESSION_SAVED",
        "NANA_SESSION_UNCHANGED",
        "NANA_SESSION_CLEARED",
    ),
    send_immediately: bool = False,
    forbidden_text: str | None = None,
) -> int:
    deadline = time.monotonic() + timeout
    startup_fallback = time.monotonic() + min(8.0, max(2.0, timeout / 2.0))
    next_send = 0.0
    command_ready = send_immediately
    ready_markers = (
        "NANA_SESSION_PROVISION_READY",
        "Presence session milestone 1",
        "NANA_SESSION_CONNECTED",
        "NANA_SESSION_RECONNECT",
    )

    with serial.Serial(port, 115200, timeout=0.25) as connection:
        connection.dtr = False
        connection.rts = False
        time.sleep(0.4)
        connection.reset_input_buffer()

        while time.monotonic() < deadline:
            now = time.monotonic()
            if not command_ready and now >= startup_fallback:
                command_ready = True
            if command_ready and now >= next_send:
                connection.write(command)
                connection.flush()
                next_send = now + 2.0

            raw_line = connection.readline()
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if forbidden_text and forbidden_text in line:
                raise RuntimeError("forbidden secret appeared in board log output")
            if any(marker in line for marker in ready_markers):
                command_ready = True
                next_send = 0.0
            if any(marker in line for marker in success_markers):
                print(line)
                return 0
            if "nana_session" in line or "NANA_SESSION" in line:
                print(line)

    print(
        "The board did not acknowledge session provisioning. Flash the "
        "Presence session firmware and verify the USB-UART port.",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Provision the Nana Presence WebSocket URI and bearer token over "
            "USB-UART, or run a bounded maintenance radio interruption. "
            "The token is never printed; by default it is also stored for "
            "Nana Core with Windows DPAPI."
        )
    )
    parser.add_argument("--port", help="CH343 serial port; auto-detected by default")
    parser.add_argument("--uri", help="Example: ws://<LOCAL_IP>:8765/presence/v1")
    token_source = parser.add_mutually_exclusive_group()
    token_source.add_argument(
        "--token-stdin",
        action="store_true",
        help="Read one token line from stdin instead of prompting",
    )
    token_source.add_argument(
        "--generate-token",
        action="store_true",
        help="Generate a strong token and pair both the board and Nana Core",
    )
    token_source.add_argument(
        "--use-core-token",
        action="store_true",
        help=(
            "Pair the board with the existing Windows-protected Nana Core "
            "token without printing or prompting for it"
        ),
    )
    parser.add_argument(
        "--no-store-core-token",
        action="store_true",
        help="Do not save the token for Nana Core with Windows DPAPI",
    )
    parser.add_argument(
        "--timeout", type=float, default=45.0, help="Overall wait in seconds"
    )
    maintenance = parser.add_mutually_exclusive_group()
    maintenance.add_argument(
        "--clear", action="store_true", help="Erase the saved session URI/token"
    )
    maintenance.add_argument(
        "--test-wifi-drop",
        action="store_true",
        help="Interrupt the ESP32 Wi-Fi radio for three seconds without changing NVS",
    )
    args = parser.parse_args()

    try:
        port = args.port or find_ch343_port()
    except RuntimeError as error:
        parser.error(str(error))

    if args.clear:
        print(f"Clearing Presence session configuration on {port}.")
        result = serial_session(
            port=port,
            command=b"NANA_SESSION_CLEAR\n",
            timeout=args.timeout,
        )
        if result == 0 and clear_presence_session_token():
            print("Cleared the Windows-protected Nana Core token.")
        return result
    if args.test_wifi_drop:
        print(
            f"Requesting the bounded three-second Presence Wi-Fi interruption on {port}."
        )
        return serial_session(
            port=port,
            command=b"NANA_SESSION_TEST_WIFI_DROP\n",
            timeout=args.timeout,
            success_markers=("NANA_SESSION_WIFI_DROP_DONE",),
            send_immediately=True,
        )

    try:
        uri = validate_uri(
            args.uri or input("Nana Core WebSocket URI: ")
        )
    except ValueError as error:
        parser.error(str(error))
    if len(uri.encode("utf-8")) > 255:
        parser.error("URI is too long")

    if args.generate_token:
        token = secrets.token_urlsafe(32)
    elif args.use_core_token:
        try:
            token = load_presence_session_token() or ""
        except (OSError, RuntimeError, ValueError) as exc:
            parser.error(f"Cannot load the Windows-protected Core token: {exc}")
        if not token:
            parser.error(
                "No Windows-protected Core token exists; provision once with "
                "--generate-token or enter a token interactively"
            )
    elif args.token_stdin:
        token = sys.stdin.readline().rstrip("\r\n")
    else:
        token = getpass.getpass("Presence session token (hidden): ")
    if not token:
        parser.error("Token cannot be empty")
    if len(token.encode("utf-8")) > 128:
        parser.error("Token must be at most 128 UTF-8 bytes")

    print(f"Provisioning {port}; the token is never printed.")
    result = serial_session(
        port=port,
        command=build_set_command(uri, token),
        timeout=args.timeout,
    )
    if result != 0 or args.no_store_core_token:
        return result
    try:
        save_presence_session_token(token)
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            "The board was paired, but Nana Core could not store the protected "
            f"token: {exc}",
            file=sys.stderr,
        )
        return 1
    print("Nana Core token saved with Windows DPAPI; daily prompts are disabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
